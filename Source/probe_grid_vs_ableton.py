"""Diagnostic #5: grade shipped .als beat grids against ABLETON's transient
ticks (from .asd) — the sample-accurate ruler Sam's eye uses in Live.

For the three phase-overridden tracks, also grades the original RB grid
(shipped minus shift) so we can see which one Ableton agrees with.

Usage:
    python Source/probe_grid_vs_ableton.py "<als>" "<audio dir>"
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

import numpy as np

from asd_onsets import ableton_onsets_sec, grid_offset_vs_ticks
from probe_als_warp import beats_from_markers, clip_markers_by_source, load_als

SHIFTS = {
    "Hold Me": 85.9,
    "Blackout": 97.4,
    "Bullerengue": -83.1,
}


def main() -> int:
    als, audio_dir = Path(sys.argv[1]), Path(sys.argv[2])
    root = load_als(als)
    wavs = {p.name: p for p in audio_dir.glob("*.wav")}

    print(f"Grid vs ABLETON TICKS — {als.name}")
    print(f"{'track':<50} {'offset':>8} {'beats-hit':>9}  quartiles(ms)")
    print("-" * 100)
    for name, secs in sorted(clip_markers_by_source(root).items()):
        wav = wavs.get(name) or wavs.get(html.unescape(name))
        if wav is None or len(secs) < 64:
            continue
        ticks = ableton_onsets_sec(wav)
        if ticks is None:
            print(f"{name[:50]:<50}  no .asd onsets")
            continue
        beats = beats_from_markers(list(secs))
        shift = next((v for k, v in SHIFTS.items() if k.lower() in name.lower()), None)
        variants = [("shipped", beats)]
        if shift is not None:
            variants.append((f"ORIG RB (-{shift}ms)", beats - shift / 1000.0))
        for label, b in variants:
            off, n, quarts = grid_offset_vs_ticks(b, ticks)
            q = " ".join(f"{x:+.0f}" for x in quarts)
            print(f"{(name[:36] + ' [' + label + ']'):<50} {off:>+7.1f}ms {n:>9}  [{q}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
