"""Apply manual chop corrections to a Sections V<N>.als file.

The algorithm gets us close (V16). Where the blind validation flagged ⚠ off N
errors, we patch the .als directly to move those chops to the right bar.

Usage:
  python apply_section_corrections.py <in.als> <out.als>

Corrections are hard-coded for the current iteration (V16 → V17). Future
iterations can load a CORRECTIONS_V<N>.json file instead.

Each correction moves a SHARED BOUNDARY between two adjacent clips:
- from_clip's right edge (CurrentEnd / LoopEnd / OutMarker / HiddenLoopEnd)
- to_clip's left edge (Time / CurrentStart / LoopStart / HiddenLoopStart)
"""

from __future__ import annotations

import gzip
import re
import sys
from pathlib import Path


# (track_name_substring, from_clip_name, to_clip_name, old_bar, new_bar, arr_offset_beats)
# arr_offset_beats = track's base arrangement start in beats. Adam Ten = 0,
# Ease My Mind = 2152. Source bars are absolute within the audio file; arrangement
# beats = arr_offset + source_beats.
CORRECTIONS = [
    # Adam Ten arr_start = 0
    ("Adam Ten", "drop_3", "break_1", 72, 74, 0),
    ("Adam Ten", "break_1", "drop_4", 112, 108, 0),
    # Ease My Mind arr_start = 2152 (from orchestrator output)
    ("Ease My Mind", "drop_4", "outro_1", 240, 236, 2152),
]


def find_track_block(content: str, track_substr: str) -> tuple[int, int]:
    """Return (start, end) byte offsets of the AudioTrack block whose
    EffectiveName contains track_substr."""
    name_match = re.search(
        rf'<EffectiveName Value="[^"]*{re.escape(track_substr)}[^"]*"', content
    )
    if not name_match:
        raise ValueError(f"Track containing '{track_substr}' not found")

    # Track block: walk back from EffectiveName to the nearest <AudioTrack
    track_start = content.rfind("<AudioTrack ", 0, name_match.start())
    track_end = content.find("</AudioTrack>", name_match.start())
    if track_start < 0 or track_end < 0:
        raise ValueError(f"Could not bracket AudioTrack for '{track_substr}'")
    return track_start, track_end + len("</AudioTrack>")


def find_clip_block(content: str, track_start: int, track_end: int, clip_name: str) -> tuple[int, int]:
    """Return (start, end) offsets of the AudioClip block within the given
    track that has Name=<clip_name>."""
    track_body = content[track_start:track_end]
    # Find <Name Value="clip_name" /> within this track body
    name_match = re.search(rf'<Name Value="{re.escape(clip_name)}"', track_body)
    if not name_match:
        raise ValueError(f"Clip '{clip_name}' not found in track block")
    name_pos = track_start + name_match.start()

    # Walk back to find the enclosing <AudioClip ...>
    clip_start = content.rfind("<AudioClip ", 0, name_pos)
    clip_end = content.find("</AudioClip>", name_pos)
    if clip_start < 0 or clip_end < 0:
        raise ValueError(f"Could not bracket AudioClip for '{clip_name}'")
    return clip_start, clip_end + len("</AudioClip>")


def patch_clip_attr(clip_xml: str, attr: str, old_val: float, new_val: float) -> tuple[str, bool]:
    """Replace the Value="<old>" attribute of a specific element (<{attr} Value="X" />)
    within the clip XML. Returns (new_xml, changed)."""
    pattern = rf'(<{attr} Value=")({old_val}|{old_val:.1f})("\s*/>)'
    new_xml, count = re.subn(pattern, rf'\g<1>{new_val}\g<3>', clip_xml)
    return new_xml, count > 0


def patch_clip_time_attr(clip_xml: str, old_val: float, new_val: float) -> tuple[str, bool]:
    """Replace the Time attribute on the <AudioClip> opening tag itself."""
    pattern = rf'(<AudioClip Id="\d+" Time=")({old_val}|{old_val:.1f})(")'
    new_xml, count = re.subn(pattern, rf'\g<1>{new_val}\g<3>', clip_xml)
    return new_xml, count > 0


def apply_correction(content: str, track_substr: str, from_clip: str, to_clip: str,
                     old_bar: int, new_bar: int, arr_offset_beats: int) -> str:
    """Apply a single chop-move correction.

    old_bar/new_bar are source bars. Convert to source beats (×4). Arrangement
    beats = arr_offset_beats + source_beats.
    """
    old_src_beats = float(old_bar * 4)
    new_src_beats = float(new_bar * 4)
    old_arr_beats = float(arr_offset_beats + old_bar * 4)
    new_arr_beats = float(arr_offset_beats + new_bar * 4)

    track_start, track_end = find_track_block(content, track_substr)

    # --- FROM clip: update right edge ---
    fr_start, fr_end = find_clip_block(content, track_start, track_end, from_clip)
    fr_xml = content[fr_start:fr_end]
    changes = []
    for attr, old, new in [
        ("CurrentEnd", old_arr_beats, new_arr_beats),
        ("LoopEnd", old_src_beats, new_src_beats),
        ("OutMarker", old_src_beats, new_src_beats),
        ("HiddenLoopEnd", old_src_beats, new_src_beats),
    ]:
        fr_xml, changed = patch_clip_attr(fr_xml, attr, old, new)
        changes.append((attr, old, new, changed))
    print(f"  {track_substr} {from_clip}: " + ", ".join(
        f"{a}={o}→{n}{'✓' if c else '✗'}" for a, o, n, c in changes
    ))
    content = content[:fr_start] + fr_xml + content[fr_end:]

    # Re-find track block because content length may have changed (it shouldn't
    # for numeric value-only replacements, but be safe).
    track_start, track_end = find_track_block(content, track_substr)

    # --- TO clip: update left edge ---
    to_start, to_end = find_clip_block(content, track_start, track_end, to_clip)
    to_xml = content[to_start:to_end]
    changes = []
    # AudioClip Time attribute (on the opening tag)
    to_xml, changed = patch_clip_time_attr(to_xml, old_arr_beats, new_arr_beats)
    changes.append(("Time(attr)", old_arr_beats, new_arr_beats, changed))
    for attr, old, new in [
        ("CurrentStart", old_arr_beats, new_arr_beats),
        ("LoopStart", old_src_beats, new_src_beats),
        ("HiddenLoopStart", old_src_beats, new_src_beats),
    ]:
        to_xml, changed = patch_clip_attr(to_xml, attr, old, new)
        changes.append((attr, old, new, changed))
    print(f"  {track_substr} {to_clip}: " + ", ".join(
        f"{a}={o}→{n}{'✓' if c else '✗'}" for a, o, n, c in changes
    ))
    content = content[:to_start] + to_xml + content[to_end:]

    return content


def main():
    if len(sys.argv) < 3:
        in_path = Path("Test Project/Black Book x Defected V2/Output/Sections V16.als")
        out_path = Path("Test Project/Black Book x Defected V2/Output/Sections V17.als")
    else:
        in_path = Path(sys.argv[1])
        out_path = Path(sys.argv[2])

    print(f"Reading {in_path}")
    with gzip.open(in_path, "rb") as f:
        content = f.read().decode("utf-8")
    print(f"  Loaded {len(content)} chars")

    for correction in CORRECTIONS:
        track_substr, from_clip, to_clip, old_bar, new_bar, arr_offset = correction
        print(f"\nCorrection: {track_substr} bar {old_bar} → bar {new_bar} ({from_clip}/{to_clip})")
        content = apply_correction(content, track_substr, from_clip, to_clip,
                                    old_bar, new_bar, arr_offset)

    print(f"\nWriting {out_path}")
    with gzip.open(out_path, "wb") as f:
        f.write(content.encode("utf-8"))
    print(f"  Done — {out_path.stat().st_size} bytes")


if __name__ == "__main__":
    sys.exit(main() or 0)
