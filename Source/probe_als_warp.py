"""Diagnostic: grade the warp markers INSIDE a shipped .als against each
source track's kick onsets — the ground truth of what Ableton actually plays.

Catches anything between the beatgrid gate and the file: override
propagation bugs, sign errors, double-applies, replaced-grid drift.

Usage:
    python Source/probe_als_warp.py "<path to .als>" "<audio folder>"
"""
from __future__ import annotations

import gzip
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import numpy as np

from validate_beatgrid import _grade, _kick_onsets  # noqa: E402


def load_als(path: Path) -> ET.Element:
    with gzip.open(path, "rb") as f:
        return ET.fromstring(f.read())


def clip_markers_by_source(root: ET.Element) -> dict[str, set[float]]:
    """Union of WarpMarker SecTimes per source file across all clips."""
    by_src: dict[str, set[float]] = defaultdict(set)
    for clip in root.iter("AudioClip"):
        # source file name
        name = None
        for fr in clip.iter("FileRef"):
            p = fr.find("Path")
            if p is not None and p.get("Value"):
                name = Path(p.get("Value")).name
                break
            rp = fr.find("RelativePath")
            if rp is not None and rp.get("Value"):
                name = Path(rp.get("Value")).name
                break
        if not name:
            continue
        for wm in clip.iter("WarpMarker"):
            sec = wm.get("SecTime")
            if sec is not None:
                by_src[name].add(float(sec))
    return {k: v for k, v in by_src.items()}


def beats_from_markers(sec_times: list[float]) -> np.ndarray:
    """Markers are per-beat; interpolate only across gaps where beats are
    missing (chop boundaries), matching Ableton's linear warp between markers."""
    s = np.asarray(sorted(sec_times), dtype=float)
    if len(s) < 3:
        return s
    gaps = np.diff(s)
    beat = float(np.median(gaps))
    beats: list[float] = []
    for a, b in zip(s[:-1], s[1:]):
        n = max(1, int(round((b - a) / beat)))
        beats.extend(np.linspace(a, b, n, endpoint=False))
    beats.append(float(s[-1]))
    return np.asarray(beats)


def tempo_events(root: ET.Element) -> list[tuple[float, float]]:
    """Master tempo automation FloatEvents (beat_time, bpm)."""
    out: list[tuple[float, float]] = []
    for tempo in root.iter("Tempo"):
        for ev in tempo.iter("FloatEvent"):
            t, v = ev.get("Time"), ev.get("Value")
            if t is not None and v is not None:
                out.append((float(t), float(v)))
    return sorted(set(out))


def main() -> int:
    als = Path(sys.argv[1])
    audio_dir = Path(sys.argv[2])
    root = load_als(als)

    wavs = {p.name: p for p in audio_dir.glob("*.wav")}
    by_src = clip_markers_by_source(root)

    print(f"ALS: {als.name} — {len(by_src)} source files with warp markers\n")
    hdr = (f"{'track':<44} {'mks':>4} {'BPM':>7} {'R_half':>6} "
           f"{'phase(b)':>8} {'phase(ms)':>9} {'ctrl':>5}  seg_R")
    print(hdr)
    print("-" * len(hdr))

    for name, secs in sorted(by_src.items()):
        beats = beats_from_markers(list(secs))
        if len(beats) < 32:
            print(f"{name[:44]:<44} {len(secs):>4}  too few markers")
            continue
        period = float(np.median(np.diff(beats)))
        bpm = 60.0 / period
        wav = wavs.get(name)
        if wav is None:
            # tolerate html-escaped vs raw names
            import html
            wav = wavs.get(html.unescape(name))
        if wav is None:
            print(f"{name[:44]:<44} {len(secs):>4} {bpm:>7.2f}  WAV NOT FOUND")
            continue
        res = _kick_onsets(wav)
        if res is None:
            print(f"{name[:44]:<44} {len(secs):>4} {bpm:>7.2f}  audio load failed")
            continue
        onsets, _ = res
        onsets = onsets[(onsets >= beats[0] - period) & (onsets <= beats[-1] + period)]
        if len(onsets) < 40:
            print(f"{name[:44]:<44} {len(secs):>4} {bpm:>7.2f}  only {len(onsets)} onsets")
            continue
        r_half, phase, seg_r = _grade(onsets, beats, period)
        detuned = beats[0] + (beats - beats[0]) * 1.01
        r_det, _, _ = _grade(onsets, detuned, period * 1.01)
        print(f"{name[:44]:<44} {len(secs):>4} {bpm:>7.2f} {r_half:>6.2f} "
              f"{phase:>+8.3f} {phase * period * 1000:>+9.1f} {r_det:>5.2f}  {seg_r}")

    print("\nMaster tempo automation (beat_time -> BPM):")
    evs = tempo_events(root)
    if not evs:
        print("  NONE — no FloatEvents under <Tempo>")
    for t, v in evs:
        print(f"  {t:>10.2f} -> {v:.4f}")
    for tempo in root.iter("Tempo"):
        man = tempo.find("Manual")
        if man is not None:
            print(f"  Manual (static) tempo: {man.get('Value')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
