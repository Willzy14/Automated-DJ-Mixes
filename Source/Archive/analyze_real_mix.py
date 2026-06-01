"""Open one of Sam's real DJ mix .als files and extract its arrangement
structure — clip positions, automation envelopes — so we can see HOW he
actually does transitions vs. how the pipeline currently does them.

Goal: render each transition as a timeline image with two stacked tracks,
their volume curves overlaid, the clip start/end markers visible. Look at
the result by eye and write up the pattern.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path
from dataclasses import dataclass

import sys
sys.path.insert(0, str(Path(__file__).parent))

from automated_dj_mixes.als_generator import decompress_als


@dataclass
class AudioClip:
    track_index: int
    track_name: str
    time: float           # arrangement beat where clip starts
    current_start: float
    current_end: float
    loop_start: float     # source beat
    loop_end: float       # source beat
    name: str
    file_ref: str


@dataclass
class VolumeEnvelope:
    track_index: int
    track_name: str
    target_id: str
    points: list[tuple[float, float]]  # (time, value)


def extract_arrangement(als_path: Path):
    """Walk the ALS XML and pull out (clips, envelopes) per track."""
    lines = decompress_als(als_path)

    # Find track ranges (AudioTrack only)
    tracks: list[tuple[int, int, str]] = []  # (start, end, name)
    track_start = None
    depth = 0
    track_name = ""
    for i, line in enumerate(lines):
        if "<AudioTrack " in line:
            track_start = i
            depth = 1
            track_name = ""
        elif track_start is not None:
            if "<EffectiveName" in line and not track_name:
                m = re.search(r'Value="([^"]*)"', line)
                if m:
                    track_name = m.group(1)
            if "<AudioTrack " in line:
                depth += 1
            if "</AudioTrack>" in line:
                depth -= 1
                if depth == 0:
                    tracks.append((track_start, i, track_name))
                    track_start = None

    clips: list[AudioClip] = []
    for ti, (s, e, tname) in enumerate(tracks):
        # Walk lines inside this track, find AudioClip blocks
        in_clip = False
        clip_buf = []
        clip_start_line = None
        for i in range(s, e + 1):
            line = lines[i]
            if "<AudioClip " in line and "Time=" in line:
                in_clip = True
                clip_buf = [line]
                clip_start_line = i
            elif in_clip:
                clip_buf.append(line)
                if "</AudioClip>" in line:
                    in_clip = False
                    # Parse the clip
                    blob = "".join(clip_buf)
                    time = float(re.search(r'<AudioClip [^>]*Time="([^"]+)"', blob).group(1))
                    cs = float(re.search(r'<CurrentStart Value="([^"]+)"', blob).group(1))
                    ce = float(re.search(r'<CurrentEnd Value="([^"]+)"', blob).group(1))
                    ls_m = re.search(r'<LoopStart Value="([^"]+)"', blob)
                    le_m = re.search(r'<LoopEnd Value="([^"]+)"', blob)
                    ls = float(ls_m.group(1)) if ls_m else 0.0
                    le = float(le_m.group(1)) if le_m else 0.0
                    name_m = re.search(r'<Name Value="([^"]*)"', blob)
                    name = name_m.group(1) if name_m else ""
                    path_m = re.search(r'<Path Value="([^"]*)"', blob)
                    file_ref = path_m.group(1) if path_m else ""
                    clips.append(AudioClip(
                        track_index=ti, track_name=tname,
                        time=time, current_start=cs, current_end=ce,
                        loop_start=ls, loop_end=le,
                        name=name, file_ref=file_ref,
                    ))

    return tracks, clips


def summarise(als_path: Path, label: str):
    if not als_path.exists():
        print(f"NOT FOUND: {als_path}")
        return
    print(f"\n========== {label} ==========")
    print(f"Path: {als_path}")
    tracks, clips = extract_arrangement(als_path)
    print(f"Tracks: {len(tracks)}")
    # Group clips by file_ref to see unique source tracks
    by_source = {}
    for c in clips:
        key = Path(c.file_ref).name if c.file_ref else c.name
        by_source.setdefault(key, []).append(c)
    print(f"Unique source files: {len(by_source)}")
    for src, src_clips in list(by_source.items())[:15]:
        arr_min = min(c.time for c in src_clips)
        arr_max = max(c.current_end for c in src_clips)
        print(f"  '{src[:60]}' — {len(src_clips)} clip(s) — arr[{arr_min:.0f}..{arr_max:.0f}]")
    if len(by_source) > 15:
        print(f"  ... and {len(by_source) - 15} more source files")


def main():
    candidates = [
        ("Defected Ibiza Side 1", Path("G:/Mix CD' Projects/2015 -/Defected In The House Ibiza 2015/Ibiza side 1 FINAL/Ibiza side 1 version 3 (SB1) AD1 SW V1.als")),
        ("Bargrooves Summer 2015 Mix 1 SW V1", Path("G:/Mix CD' Projects/2015 -/Bargrooves Summer Sessions 2015 Mixes Project/Bargrooves Summer Sessions 2015 Mix 1  SW V1.als")),
        ("ANTS 2015", Path("G:/Mix CD' Projects/2015 -/ANTS 2015 Project/ANTS 2015.als")),
    ]
    for label, als in candidates:
        try:
            summarise(als, label)
        except Exception as e:
            print(f"FAILED {label}: {e}")


if __name__ == "__main__":
    main()
