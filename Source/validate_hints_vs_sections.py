"""Check track_hints.json against Sections_V<N>.json.

Hints are authored from PNGs (seconds). Section boundaries come from the
algorithm + corrections (source beats). If these disagree, the pipeline
silently uses one for arrangement and the other for hint-driven things
(loop source, intro skip, etc.). This script catches the disagreement.

Tolerance (in bars at the track's BPM):
  diff ≤ 4 bars → ✓
  4 < diff ≤ 8 → ⚠ warning
  diff > 8     → ✗ error (exit non-zero)

CLI:
  python validate_hints_vs_sections.py <project_dir> [--version N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _label(sec: dict) -> str:
    return sec.get("label", "").lower()


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


def _bpm_lookup(project_dir: Path) -> dict[str, float]:
    """Prefer the most-recent ARRANGEMENT_REPORT*.json for BPMs (MIK-enriched).

    Fallback (2026-06-11): the per-track stem JSONs carry the grid-true BPM
    the sections were detected on. Without this, a FIRST pipeline run (no
    arrangement report yet — that's a Phase-2 artifact) found no BPMs,
    skipped every track, and the gate PASSed having checked NOTHING.
    """
    out: dict[str, float] = {}
    reports = sorted((project_dir / "Output").glob("ARRANGEMENT_REPORT*.json"),
                     key=lambda p: p.stat().st_mtime)
    if reports:
        try:
            rep = json.loads(reports[-1].read_text(encoding="utf-8"))
            for t in rep.get("tracks", []):
                out[t["name"]] = t["bpm"]
        except Exception:
            pass
    for j in (project_dir / "_Stem Analysis").glob("SECTIONS_STEM_*.json"):
        try:
            d = json.loads(j.read_text(encoding="utf-8"))
            stem = j.stem.replace("SECTIONS_STEM_", "")
            if d.get("bpm"):
                out.setdefault(stem, float(d["bpm"]))
                out.setdefault(stem + ".wav", float(d["bpm"]))
        except Exception:
            continue
    return out


def _first_section_of(secs: list[dict], label: str) -> dict | None:
    for s in secs:
        if _label(s) == label:
            return s
    return None


def _expected_for_hint(hint_key: str, secs: list[dict]) -> dict | None:
    """Return the section dict whose source_start_beats should match the hint.

    Semantics match the hint derivation (stem_detector.hints_from_stem_result):
    first_break = the first break AFTER the first drop — tracks often carry a
    pre-drop intro break (long kick-out before the drop) which is NOT the
    energy-drop the hint describes (2026-06-11, Hyzteria).
    """
    if hint_key == "first_drop_sec":
        return _first_section_of(secs, "drop")
    if hint_key == "first_break_sec":
        first_drop = _first_section_of(secs, "drop")
        if first_drop is not None:
            after = [s for s in secs if _label(s) == "break"
                     and s["source_start_beats"] > first_drop["source_start_beats"]]
            if after:
                return after[0]
        return _first_section_of(secs, "break")
    if hint_key == "outro_start_sec":
        return _first_section_of(secs, "outro")
    return None


def _verdict(diff_bars: float) -> str:
    d = abs(diff_bars)
    if d <= 4:
        return "✓"
    if d <= 8:
        return "⚠ warn"
    return "✗ error"


def validate(project_dir: Path, version: int | None = None) -> tuple[int, list[str], str]:
    sections_dir = project_dir / "Sections Review"
    hints_path = project_dir / "Hints" / "track_hints.json"
    if not hints_path.exists():
        return 0, [], "(no track_hints.json — nothing to validate)"

    if version is None:
        version, sections_json_path = _find_latest_sections_json(sections_dir)
    else:
        sections_json_path = sections_dir / f"Sections_V{version}.json"
    if not sections_json_path.exists():
        return 1, [f"Sections JSON not found: {sections_json_path}"], ""

    hints_data = json.loads(hints_path.read_text(encoding="utf-8"))
    sections_data = json.loads(sections_json_path.read_text(encoding="utf-8"))
    bpms = _bpm_lookup(project_dir)

    lines = [f"# Hints vs sections — V{version}", ""]
    lines.append("| Track | Hint | Hint value (s) | Hint bar | Section bar | Δ bars | Verdict |")
    lines.append("|-------|------|----------------|----------|-------------|--------|---------|")

    has_error = False
    has_warn = False
    rows = 0
    for track_name, secs in sections_data.items():
        if not track_name or "Audio" in track_name or not secs:
            continue
        # Resolve hint entry: filenames in track_hints.json typically end in
        # .wav; sections JSON keys are usually the stem.
        hint_entry = hints_data.get(track_name) \
            or hints_data.get(track_name + ".wav") \
            or hints_data.get(track_name.replace(".wav", ""))
        if not hint_entry:
            continue
        bpm = bpms.get(track_name)
        if bpm is None:
            # Try the .wav variant
            bpm = bpms.get(track_name + ".wav")
        if bpm is None or bpm <= 0:
            continue
        sec_per_bar = 4 * 60.0 / bpm

        for hint_key in ("first_drop_sec", "first_break_sec", "outro_start_sec"):
            hint_val = hint_entry.get(hint_key)
            if hint_val is None:
                continue
            expected = _expected_for_hint(hint_key, secs)
            if expected is None:
                continue
            hint_bar = float(hint_val) / sec_per_bar
            sec_bar = expected["source_start_beats"] / 4
            diff = hint_bar - sec_bar
            v = _verdict(diff)
            if v.startswith("✗"):
                has_error = True
            elif v.startswith("⚠"):
                has_warn = True
            lines.append(
                f"| {track_name[:40]} | {hint_key} | {float(hint_val):.1f} | "
                f"{hint_bar:.1f} | {sec_bar:.1f} | {diff:+.1f} | {v} |")
            rows += 1

        # last_bass_drop_sec — Sam's model: the natural bass-out BEFORE the
        # final kicks, i.e. within the 32-bar swap window leading INTO the
        # outro (or inside it). Matches hints_from_stem_result's window.
        last_bass = hint_entry.get("last_bass_drop_sec")
        outro = _first_section_of(secs, "outro")
        if last_bass is not None and outro is not None:
            last_bar = float(last_bass) / sec_per_bar
            outro_start = outro["source_start_beats"] / 4
            outro_end = outro["source_end_beats"] / 4
            window_lo = outro_start - 32
            if window_lo - 4 <= last_bar <= outro_end + 4:
                v = "✓"
            elif window_lo - 8 <= last_bar <= outro_end + 8:
                v = "⚠ warn"; has_warn = True
            else:
                v = "✗ error (outside the pre-outro swap window)"; has_error = True
            lines.append(
                f"| {track_name[:40]} | last_bass_drop_sec | {float(last_bass):.1f} | "
                f"{last_bar:.1f} | window {window_lo:.1f}-{outro_end:.1f} | "
                f"{'-' if window_lo <= last_bar <= outro_end else 'OUT'} | {v} |")
            rows += 1

    summary = (
        f"\n**Total checks:** {rows} | "
        f"errors: {'yes' if has_error else 'none'} | "
        f"warnings: {'yes' if has_warn else 'none'}"
    )
    lines.append(summary)

    out_path = sections_dir / f"HINTS_VS_SECTIONS_V{version}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")

    if rows == 0:
        # A gate that compared nothing must not pass (2026-06-11: first runs
        # had no ARRANGEMENT_REPORT for BPMs, every track was skipped, and
        # "PASS" went out over zero checks).
        return 2, ["0 checks performed — the gate compared NOTHING (hint/section "
                   f"key pairing or BPM lookup failed). See {out_path}"], "\n".join(lines)
    if has_error:
        return 2, [f"Hints disagree with sections by >8 bars in at least one row. "
                   f"See {out_path}"], "\n".join(lines)
    return 0, [], "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--version", type=int, default=None)
    args = parser.parse_args()

    code, errors, report = validate(args.project_dir, args.version)
    if code == 1:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(report)
    if code != 0:
        for e in errors:
            print(f"\nFAIL: {e}", file=sys.stderr)
        return 2
    print("\nPASS  hints and sections agree within tolerance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
