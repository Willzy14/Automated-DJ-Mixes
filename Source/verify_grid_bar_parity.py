"""Verify (and fix) bar parity of tick-fitted grids against a previous .als.

Every clip start in the previous mix is a bar-snapped section boundary, so
each clip's first warp-marker SecTime (minus any reverted phase shift) must
land on a BAR line of the new grid: beat index ≡ first_downbeat_offset
(mod 4). Computes the consensus offset per track from all clips and corrects
the override when it disagrees.

CAVEAT (2026-06-12, La Trumpter): a clip's first warp marker can be a
PRE-ROLL beat before the musical clip start, which shifts the measured
consensus by a beat. The stronger parity oracle is a HINT DIFF: re-derive
track_hints.json on the new grid and compare to the previous hints — any
track whose hints all move by ~one beat (60/bpm seconds) has broken parity;
ms-scale moves are just the phase correction. Trust the hint diff over this
checker when they disagree.

Usage:
    python Source/verify_grid_bar_parity.py "<project dir>" "<prev.als>" [--fix]
"""
from __future__ import annotations

import gzip
import html
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

REVERTED_SHIFTS = {
    "Hold Me": 85.9,
    "Blackout": 97.4,
    "Bullerengue": -83.1,
}


def clip_start_secs(als: Path) -> dict[str, list[float]]:
    """First warp-marker SecTime of every clip, grouped by source file."""
    with gzip.open(als, "rb") as f:
        root = ET.fromstring(f.read())
    out: dict[str, list[float]] = defaultdict(list)
    for clip in root.iter("AudioClip"):
        name = None
        for fr in clip.iter("FileRef"):
            p = fr.find("Path")
            if p is not None and p.get("Value"):
                name = html.unescape(Path(p.get("Value")).name)
                break
        if not name:
            continue
        firsts = [float(wm.get("SecTime")) for wm in clip.iter("WarpMarker")
                  if wm.get("SecTime") is not None]
        if firsts:
            out[name].append(min(firsts))
    return out


def main() -> int:
    project = Path(sys.argv[1])
    prev_als = Path(sys.argv[2])
    fix = "--fix" in sys.argv

    op = project / "Hints" / "grid_overrides.json"
    overrides = json.loads(op.read_text(encoding="utf-8"))
    starts = clip_start_secs(prev_als)

    changed = 0
    for name, ov in overrides.items():
        rep = ov.get("replace_grid")
        if not rep:
            continue
        period = 60.0 / float(rep["bpm"])
        first = float(rep["first_ms"]) / 1000.0
        shift = next((v for k, v in REVERTED_SHIFTS.items()
                      if k.lower() in name.lower()), 0.0)
        votes: Counter[int] = Counter()
        worst = 0.0
        for t in starts.get(name, []):
            k = (t - shift / 1000.0 - first) / period
            err = abs(k - round(k)) * period * 1000.0
            worst = max(worst, err)
            votes[int(round(k)) % 4] += 1
        if not votes:
            print(f"[????] {name[:52]:<54} no clips found in previous als")
            continue
        consensus, n = votes.most_common(1)[0]
        written = int(rep.get("first_downbeat_offset", 0))
        status = "OK  " if consensus == written else "FIX "
        print(f"[{status}] {name[:52]:<54} consensus dboff={consensus} "
              f"({n}/{sum(votes.values())} clips, worst beat-err {worst:.1f}ms) "
              f"written={written}")
        if consensus != written and fix:
            rep["first_downbeat_offset"] = consensus
            changed += 1

    if fix and changed:
        op.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
        print(f"\nfixed {changed} downbeat offset(s) -> {op}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
