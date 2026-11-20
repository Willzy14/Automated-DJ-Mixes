"""Score stem-detector section outputs against the owner's section answer key.

Usage:
    python Tools/score_sections.py --results "<dir with SECTIONS_STEM_*.json>" \
        [--key "Documentation/Golden Sections/Answer Key 2026-11-20 DFTD.json"] \
        [--out score.json]

What is scored, per key track (and summed):
  lines   the key's section starts after bar 0, against the detector's section
          starts after its first section. One-to-one nearest matching.
          found1 = within 1 bar, found0 = within one beat (0.25 bar),
          missed = key lines with no detector line within 1 bar,
          extra  = detector lines with no key line within 1 bar.
  fills   a key fill is FOUND if any detector fill or any detector kick gap
          (kick_dropout -> kick_return) overlaps it in time.
          extra = detector fills that overlap no key fill (kick gaps are not
          counted as extra: a long gap is a break, not a fill claim).
  bar1    bar-one offset in bars: the detector's first downbeat minus the
          key's first beat. 0.0 means the grids start on the same beat.

Everything is compared in SECONDS, converted to the key's bars with the key's
bpm and lead-in (the key counts bars from the track's first beat), so a grid
that starts on the wrong beat shows up as an offset on every line instead of
being hidden by bar arithmetic.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_KEY = (Path(__file__).resolve().parent.parent / "Documentation" / "Golden Sections"
               / "Answer Key 2026-11-20 DFTD.json")
LINE_TOL_BARS = 1.0       # the owner's tolerance: a line within one bar is the same line
EXACT_TOL_BARS = 0.25     # "on the bar" = within one beat
_EPS = 1e-6


def match_lines(key_bars, det_bars, tol):
    """One-to-one matching, closest pairs first. Returns (pairs, missed, extra)."""
    cands = sorted(
        (abs(k - d), i, j)
        for i, k in enumerate(key_bars)
        for j, d in enumerate(det_bars)
        if abs(k - d) <= tol + _EPS
    )
    used_k, used_d, pairs = set(), set(), []
    for _, i, j in cands:
        if i in used_k or j in used_d:
            continue
        used_k.add(i)
        used_d.add(j)
        pairs.append((key_bars[i], det_bars[j]))
    missed = [k for i, k in enumerate(key_bars) if i not in used_k]
    extra = [d for j, d in enumerate(det_bars) if j not in used_d]
    return sorted(pairs), missed, extra


def _overlaps(a, b):
    return min(a[1], b[1]) - max(a[0], b[0]) > _EPS


def _kick_gaps(cues):
    gaps, open_start = [], None
    for c in sorted(cues, key=lambda c: c.get("start_sec", 0.0)):
        if c.get("type") == "kick_dropout":
            open_start = c["start_sec"]
        elif c.get("type") == "kick_return" and open_start is not None:
            gaps.append((open_start, c["start_sec"]))
            open_start = None
    return gaps


def detector_downbeat_sec(res) -> float:
    sig = res.get("signals") or {}
    if isinstance(sig.get("downbeat_sec"), (int, float)):
        return float(sig["downbeat_sec"])
    secs = res.get("sections") or []
    return float(secs[0]["start_sec"]) if secs else 0.0


def detector_fills_sec(res):
    """Fills in seconds. Prefers the first-class `fill_sections` list when present."""
    sig = res.get("signals") or {}
    if isinstance(sig.get("fill_sections"), list):
        return [(float(f["start_sec"]), float(f["end_sec"])) for f in sig["fill_sections"]]
    return [(float(a), float(b)) for a, b in sig.get("fills", [])]


def score_track(key_track: dict, res: dict) -> dict:
    spb = 240.0 / float(key_track["bpm"])
    lead = float(key_track.get("lead_in_bars", 0.0)) * spb
    to_bar = lambda sec: (float(sec) - lead) / spb

    key_lines = [float(b) for b, _ in key_track["sections"] if float(b) > 0]
    det_lines = [round(to_bar(s["start_sec"]), 3) for s in res["sections"][1:]]
    pairs1, missed, extra = match_lines(key_lines, det_lines, LINE_TOL_BARS)
    pairs0, _, _ = match_lines(key_lines, det_lines, EXACT_TOL_BARS)

    det_fills = [(round(to_bar(a), 3), round(to_bar(b), 3)) for a, b in detector_fills_sec(res)]
    gaps = [(round(to_bar(a), 3), round(to_bar(b), 3))
            for a, b in _kick_gaps((res.get("signals") or {}).get("kick_cues", []))]
    key_fills = [(float(a), float(b)) for a, b in key_track.get("fills", [])]
    fills_found = [f for f in key_fills if any(_overlaps(f, d) for d in det_fills + gaps)]
    fills_missed = [f for f in key_fills if f not in fills_found]
    fills_extra = [d for d in det_fills if not any(_overlaps(d, f) for f in key_fills)]

    return {
        "lines_key": len(key_lines),
        "lines_det": len(det_lines),
        "found1": len(pairs1),
        "found0": len(pairs0),
        "missed": len(missed),
        "extra": len(extra),
        "fills_key": len(key_fills),
        "fills_found": len(fills_found),
        "fills_missed": len(fills_missed),
        "fills_extra": len(fills_extra),
        "bar1_offset": round(to_bar(detector_downbeat_sec(res)), 3),
        "detail": {
            "key_lines": key_lines,
            "det_lines": det_lines,
            "matched_within_1": [list(p) for p in pairs1],
            "missed_lines": missed,
            "extra_lines": extra,
            "missed_fills": [list(f) for f in fills_missed],
            "extra_fills": [list(f) for f in fills_extra],
            "det_labels": [[round(to_bar(s["start_sec"]), 2), s.get("label")] for s in res["sections"]],
        },
    }


def find_result(results_dir: Path, track_name: str):
    hits = sorted(p for p in results_dir.glob("SECTIONS_STEM_*.json")
                  if p.name[len("SECTIONS_STEM_"):].startswith(track_name))
    return hits[0] if hits else None


def score_all(key: dict, results_dir: Path) -> dict:
    tracks, missing = {}, []
    for name, kt in key["tracks"].items():
        path = find_result(results_dir, name)
        if path is None:
            missing.append(name)
            continue
        tracks[name] = score_track(kt, json.loads(path.read_text(encoding="utf-8")))
    sum_keys = ("lines_key", "lines_det", "found1", "found0", "missed", "extra",
                "fills_key", "fills_found", "fills_missed", "fills_extra")
    total = {k: sum(t[k] for t in tracks.values()) for k in sum_keys}
    total["tracks"] = len(tracks)
    return {"tracks": tracks, "total": total, "missing": missing}


def format_table(scored: dict) -> str:
    head = (f"{'track':34} {'lines':>5} {'f<=1':>5} {'f=0':>4} {'miss':>5} {'xtra':>5} | "
            f"{'fills':>5} {'found':>5} {'miss':>5} {'xtra':>5} | {'bar1':>6}")
    rows = [head, "-" * len(head)]
    for name, t in scored["tracks"].items():
        rows.append(f"{name[:34]:34} {t['lines_key']:5d} {t['found1']:5d} {t['found0']:4d} "
                    f"{t['missed']:5d} {t['extra']:5d} | {t['fills_key']:5d} {t['fills_found']:5d} "
                    f"{t['fills_missed']:5d} {t['fills_extra']:5d} | {t['bar1_offset']:6.2f}")
    t = scored["total"]
    rows.append("-" * len(head))
    rows.append(f"{'TOTAL (' + str(t['tracks']) + ' tracks)':34} {t['lines_key']:5d} {t['found1']:5d} "
                f"{t['found0']:4d} {t['missed']:5d} {t['extra']:5d} | {t['fills_key']:5d} "
                f"{t['fills_found']:5d} {t['fills_missed']:5d} {t['fills_extra']:5d} |")
    for m in scored["missing"]:
        rows.append(f"[missing result] {m}")
    return "\n".join(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--key", type=Path, default=DEFAULT_KEY)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--detail", action="store_true", help="print missed/extra per track")
    args = ap.parse_args(argv)
    key = json.loads(args.key.read_text(encoding="utf-8"))
    scored = score_all(key, args.results)
    print(format_table(scored))
    if args.detail:
        for name, t in scored["tracks"].items():
            d = t["detail"]
            print(f"\n{name}\n  missed lines {d['missed_lines']}  extra lines {d['extra_lines']}"
                  f"\n  missed fills {d['missed_fills']}  extra fills {len(d['extra_fills'])}"
                  f"\n  detector {d['det_labels']}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(scored, indent=1), encoding="utf-8")
    return 2 if scored["missing"] and not scored["tracks"] else 0


if __name__ == "__main__":
    sys.exit(main())
