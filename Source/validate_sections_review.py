"""Validate BLIND_VALIDATION_V<N>.md against Sections_V<N>.json.

This is the **hard gate** between Phase 1d and Phase 2. It exists because
the previous skill version trusted Claude to "read every PNG and fill in
the table" — and Claude could (and did) bluff or skip. This script makes
that bluff impossible to hide: it parses the markdown table and checks
that every chop has a row with real numbers and a verdict.

Exit codes:
  0 — gate passes, pipeline can continue
  1 — usage error or files missing
  2 — gate fails (counts mismatch, missing rows, missing stats)
  3 — escalation (same-error 2+ attempts; see validation_state.json)

CLI:
  python validate_sections_review.py <project_dir> [--version N]

Outputs:
  - prints failures to stderr, summary to stdout
  - updates <project>/Sections Review/validation_state.json (Fix 5)
  - on escalation, writes <project>/Sections Review/ESCALATE.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _find_latest_sections_json(sections_dir: Path) -> tuple[int, Path]:
    candidates = []
    for p in sections_dir.glob("Sections_V*.json"):
        rest = p.stem[len("Sections_V"):]
        n = ""
        for c in rest:
            if c.isdigit():
                n += c
            else:
                break
        if n:
            candidates.append((int(n), p))
    if not candidates:
        raise FileNotFoundError(f"No Sections_V<N>.json in {sections_dir}")
    candidates.sort(key=lambda t: t[0])
    return candidates[-1]


def _count_chops(sections_data: dict) -> dict[str, int]:
    """Per-track count of internal chop BOUNDARIES (= sections - 1).
    A 6-section track has 5 boundaries: intro→build, build→drop_1, etc."""
    out = {}
    for name, secs in sections_data.items():
        if not name or "Audio" in name or not secs:
            continue
        out[name] = max(0, len(secs) - 1)
    return out


# Parser for the per-track table rows in BLIND_VALIDATION_V<N>.md.
# Expected row format (pipe-delimited, with header row + separator):
#   | Section | label | amp | L | M | H | Verdict | Why |
ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
                    r"\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
                    r"\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
                    r"\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*$")


def _is_numeric(s: str) -> bool:
    s = s.strip()
    if not s or s in ("-", "—", "_"):
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _is_separator_row(s: str) -> bool:
    stripped = s.strip()
    return bool(re.match(r"^\|[\s\-:|]+\|\s*$", stripped))


def parse_validation(md_path: Path) -> dict[str, list[dict]]:
    """Parse BLIND_VALIDATION_V<N>.md → {track_name: [row_dict, ...]}.

    Track headings are H2 (`## <name>`). Rows are matched by ROW_RE and
    skipped if they're the header row or the separator row."""
    if not md_path.exists():
        return {}
    txt = md_path.read_text(encoding="utf-8")
    by_track: dict[str, list[dict]] = {}
    current_track = None
    in_table = False
    for line in txt.splitlines():
        h2 = re.match(r"^##\s+(.+?)\s*$", line)
        if h2:
            current_track = h2.group(1).strip()
            by_track.setdefault(current_track, [])
            in_table = False
            continue
        if current_track is None:
            continue
        # Detect header row containing 'Section' and 'Verdict' so we don't
        # treat it as data.
        if re.search(r"\|\s*Section\s*\|", line, re.IGNORECASE) and \
           re.search(r"\|\s*Verdict\s*\|", line, re.IGNORECASE):
            in_table = True
            continue
        if in_table and _is_separator_row(line):
            continue
        m = ROW_RE.match(line) if in_table else None
        if m:
            row = {
                "section": m.group(1).strip(),
                "label":   m.group(2).strip(),
                "amp":     m.group(3).strip(),
                "L":       m.group(4).strip(),
                "M":       m.group(5).strip(),
                "H":       m.group(6).strip(),
                "verdict": m.group(7).strip(),
                "why":     m.group(8).strip(),
            }
            by_track[current_track].append(row)
        elif in_table and not line.strip():
            in_table = False  # blank line ends the table
    return by_track


def validate(project_dir: Path, version: int | None = None) -> tuple[int, list[str], dict]:
    """Run the gate. Returns (exit_code, errors, summary)."""
    sections_dir = project_dir / "Sections Review"
    if version is None:
        version, sections_json_path = _find_latest_sections_json(sections_dir)
    else:
        sections_json_path = sections_dir / f"Sections_V{version}.json"
    md_path = sections_dir / f"BLIND_VALIDATION_V{version}.md"
    proposed_path = sections_dir / f"PROPOSED_CORRECTIONS_V{version}.json"

    errors: list[str] = []

    if not sections_json_path.exists():
        return 1, [f"Sections JSON not found: {sections_json_path}"], {}
    if not md_path.exists():
        return 2, [
            f"BLIND_VALIDATION_V{version}.md does not exist at {md_path}. "
            "Phase 1d was skipped — write the validation table before Phase 2."
        ], {}

    sections_data = json.loads(sections_json_path.read_text(encoding="utf-8"))
    expected_per_track = _count_chops(sections_data)
    # Sections counts (rows = sections, not boundaries) — we accept either
    # convention but we require a row per SECTION (each chop is described as
    # the entry to a section, so N sections → N rows).
    expected_rows_per_track = {name: max(1, n + 1) for name, n in expected_per_track.items()}

    actual = parse_validation(md_path)

    # Normalise key matching: XML entities like &apos;, &amp; in sections JSON
    # keys must match either entity-encoded or decoded headings in the MD.
    def _normalise(name: str) -> str:
        return (name.replace("&apos;", "'")
                    .replace("&amp;", "&")
                    .replace("&quot;", '"')
                    .replace("&lt;", "<")
                    .replace("&gt;", ">"))

    # Build a normalised lookup so MD headings can be either form
    actual_norm: dict[str, list[dict]] = {}
    for k, v in actual.items():
        actual_norm[_normalise(k)] = v
    # Also map JSON keys through normalise so we lookup with the decoded form
    expected_rows_per_track_norm = {_normalise(k): v for k, v in expected_rows_per_track.items()}
    # Replace `actual` and `expected_rows_per_track` with normalised versions
    actual = actual_norm
    expected_rows_per_track = expected_rows_per_track_norm

    # Hard check 1: every track in JSON has a heading in MD
    for name in expected_rows_per_track:
        if name not in actual:
            errors.append(f"Missing track heading in MD: '## {name}'")

    # Hard check 2: row count matches sections per track
    for name, n_expected in expected_rows_per_track.items():
        rows = actual.get(name, [])
        if len(rows) < n_expected:
            errors.append(
                f"{name}: expected ≥{n_expected} validation rows, found {len(rows)}. "
                "Every section needs its own row.")

    # Hard check 3: every row has numeric amp/L/M/H + a verdict symbol
    wrong_rows = []  # rows marked '✗ wrong' — need proposed corrections
    for name, rows in actual.items():
        for r in rows:
            for stat_key in ("amp", "L", "M", "H"):
                if not _is_numeric(r[stat_key]):
                    errors.append(
                        f"{name} / {r['section']}: column `{stat_key}` is "
                        f"'{r[stat_key]}' (must be numeric, e.g. 0.62).")
            v = r["verdict"]
            if not v:
                errors.append(f"{name} / {r['section']}: empty Verdict.")
            elif not (v.startswith("✓") or "borderline" in v.lower()
                      or v.startswith("✗") or "wrong" in v.lower()):
                errors.append(
                    f"{name} / {r['section']}: unrecognised verdict "
                    f"'{v}' — must start with ✓, ⚠ borderline, or ✗ wrong.")
            if v.startswith("✗") or "wrong" in v.lower():
                wrong_rows.append((name, r["section"]))

    # Hard check 4: every ✗ wrong row has a proposed correction
    if wrong_rows:
        proposed = []
        if proposed_path.exists():
            try:
                proposed = json.loads(proposed_path.read_text(encoding="utf-8"))
            except Exception as e:
                errors.append(f"PROPOSED_CORRECTIONS_V{version}.json is malformed: {e}")
        # Each proposed correction: [track_substr, from_clip, to_clip, old_bar, new_bar_or_DELETE, arr_offset]
        # The 'to_clip' name is the section whose START was wrong.
        proposed_keys = set()
        for entry in proposed:
            try:
                track_sub = entry[0]
                to_clip = entry[2]
                proposed_keys.add((track_sub, to_clip))
            except (IndexError, TypeError):
                pass
        for track_name, section_name in wrong_rows:
            # match if the track substring is contained in the full name AND
            # the section name matches `to_clip`
            matched = any(
                ts.lower() in track_name.lower() and tc == section_name
                for ts, tc in proposed_keys)
            if not matched:
                errors.append(
                    f"{track_name} / {section_name}: marked ✗ wrong but no "
                    f"entry in PROPOSED_CORRECTIONS_V{version}.json. "
                    "Every wrong chop needs a proposed correction (or `\"DELETE\"`).")

    summary = {
        "version": version,
        "tracks": len(actual),
        "wrong_rows": [f"{t}:{s}" for t, s in wrong_rows],
        "errors": errors,
    }

    if errors:
        return 2, errors, summary
    return 0, [], summary


def update_state_and_escalate(project_dir: Path, version: int,
                              wrong_rows: list[str], outcome: str) -> bool:
    """Append to validation_state.json. Return True if an escalation was
    written (caller should exit with code 3)."""
    state_path = project_dir / "Sections Review" / "validation_state.json"
    state = {"attempts": []}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {"attempts": []}
    state["current_version"] = version
    state["attempts"].append({
        "version": version,
        "outcome": outcome,
        "wrong_chops": wrong_rows,
    })

    # Escalation: same chop appearing in ≥2 attempts across attempts list
    counts: dict[str, int] = {}
    for a in state["attempts"]:
        for c in a.get("wrong_chops", []):
            counts[c] = counts.get(c, 0) + 1
    persistent = [c for c, n in counts.items() if n >= 2]

    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    if outcome == "fail" and persistent:
        esc_path = project_dir / "Sections Review" / "ESCALATE.md"
        esc_path.write_text(
            "# Escalation — chops won't validate clean\n\n"
            f"After {len(state['attempts'])} attempt(s) the following chop(s) "
            "are still failing validation:\n\n"
            + "\n".join(f"- `{c}`" for c in persistent)
            + "\n\nStop the pipeline. These need Sam's eyes — either the "
              "section detection is wrong in a way the auto-flagger isn't "
              "catching, or the proposed corrections are themselves wrong. "
              "Don't keep looping.\n",
            encoding="utf-8")
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--version", type=int, default=None,
                        help="Sections version to validate (default: latest)")
    args = parser.parse_args()

    code, errors, summary = validate(args.project_dir, args.version)
    if code == 1:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if code != 0:
        print(f"FAIL  Sections V{summary.get('version')}: "
              f"{len(errors)} validation error(s).\n", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        escalated = update_state_and_escalate(
            args.project_dir, summary["version"], summary["wrong_rows"], "fail")
        if escalated:
            print("\nESCALATION: same chop has failed validation twice. "
                  "See ESCALATE.md. Pipeline must stop.", file=sys.stderr)
            return 3
        return 2

    print(f"PASS  Sections V{summary['version']}: "
          f"{summary['tracks']} tracks, all rows numeric, all verdicts present"
          + (f", {len(summary['wrong_rows'])} ✗ rows with proposed corrections"
             if summary["wrong_rows"] else "."))
    update_state_and_escalate(
        args.project_dir, summary["version"], summary["wrong_rows"], "pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
