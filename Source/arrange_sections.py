"""Reposition tracks in a Sections V<N>.als using natural-fill alignment.

Given a Sections .als with corrected chops (Sam-edited or algorithm output),
compute each track's ideal arrangement start so that:
  incoming.first_drop  aligns with  outgoing.last_natural_swap

last_natural_swap = source start of the LAST fill/break before outro
(walks backward from outro through the segment list).

For each track whose computed arr_start differs from current, shifts ALL its
AudioClips by the delta. Source positions (LoopStart/LoopEnd) are UNCHANGED —
we're only moving the track on the arrangement timeline.

Usage:
  python arrange_sections.py <in.als> <out.als>
"""

from __future__ import annotations

import gzip
import re
import sys
from pathlib import Path


def parse_als_tracks(content: str):
    """Return list of (track_substr, [(clip_name, source_start_beats, arr_time), ...])
    in arrangement order. Reuses the parser from extract_sections_als.py."""
    track_blocks = re.split(r"<AudioTrack ", content)
    tracks = []
    for block in track_blocks[1:]:
        end_idx = block.find("</AudioTrack>")
        if end_idx < 0:
            continue
        track_body = block[:end_idx]
        name_match = re.search(r'<EffectiveName Value="([^"]*)"', track_body)
        track_name = name_match.group(1) if name_match else "(unnamed)"
        if not track_name:
            continue

        clips = []
        clip_blocks = re.split(r'<AudioClip Id="\d+" Time="', track_body)
        for cb in clip_blocks[1:]:
            time_match = re.match(r'(-?[\d.]+)"', cb)
            if not time_match:
                continue
            arr_time = float(time_match.group(1))
            loop_start_match = re.search(r'<LoopStart Value="(-?[\d.]+)"', cb)
            src_start = float(loop_start_match.group(1)) if loop_start_match else 0.0
            name_match_clip = re.search(r'<Name Value="([^"]*)"', cb)
            clip_name = name_match_clip.group(1) if name_match_clip else "(unnamed)"
            clips.append((clip_name, src_start, arr_time))

        if clips:
            clips.sort(key=lambda c: c[2])
            tracks.append((track_name, clips))
    # Sort tracks by first clip's arr_time
    tracks.sort(key=lambda t: t[1][0][2])
    return tracks


def first_drop_src(clips) -> float:
    for name, src_start, _ in clips:
        n = name.lower()
        if n.startswith("drop"):
            return src_start
    return 0.0


def last_natural_swap_src(clips) -> float:
    """Walk backward from outro to find the LAST fill/break/build segment.
    Returns its source_start_beats."""
    # Find outro
    outro_idx = next((i for i, (n, _, _) in enumerate(clips) if "outro" in n.lower()),
                     len(clips))
    for name, src_start, _ in reversed(clips[:outro_idx]):
        n = name.lower()
        if n.startswith(("fill", "break", "braak", "build")):
            return src_start
    if outro_idx < len(clips):
        # Fallback: outro start itself
        return clips[outro_idx][1]
    return clips[-1][1]


def compute_target_arr_starts(tracks):
    """Compute the IDEAL arr_start for each track using natural-fill alignment.
    Returns list of (track_name, current_arr_start, new_arr_start, delta_beats).
    """
    result = []
    new_arr = 0.0
    for i, (track_name, clips) in enumerate(tracks):
        current_arr = clips[0][2]
        if i == 0:
            new_arr = current_arr  # Keep first track where it is (usually arr-beat 0)
        else:
            prev_name, prev_clips = tracks[i - 1]
            prev_new_arr = result[-1][2]  # The NEW arr_start of previous track
            prev_swap = last_natural_swap_src(prev_clips)
            curr_drop = first_drop_src(clips)
            new_arr = prev_new_arr + prev_swap - curr_drop
            # Don't allow tracks to overlap negatively
            new_arr = max(new_arr, prev_new_arr)
        delta = new_arr - current_arr
        result.append((track_name, current_arr, new_arr, delta))
    return result


def shift_track_clips(content: str, track_name: str, delta_beats: float) -> str:
    """Add delta_beats to Time / CurrentStart / CurrentEnd of every AudioClip
    in the named track. LoopStart/LoopEnd unchanged."""
    if abs(delta_beats) < 0.001:
        return content  # No change

    # Find track block
    name_pattern = re.escape(track_name)
    name_match = re.search(rf'<EffectiveName Value="{name_pattern}"', content)
    if not name_match:
        # Try with HTML-escaped apostrophe
        name_match = re.search(
            rf'<EffectiveName Value="{re.escape(track_name.replace("&apos;", chr(39)))}"',
            content,
        )
    if not name_match:
        print(f"  WARNING: track '{track_name}' not found")
        return content

    track_start = content.rfind("<AudioTrack ", 0, name_match.start())
    track_end = content.find("</AudioTrack>", name_match.start()) + len("</AudioTrack>")
    track_body = content[track_start:track_end]

    # Find every AudioClip in this track and shift Time/CurrentStart/CurrentEnd
    def shift_clip(match: re.Match) -> str:
        clip_xml = match.group(0)
        # Time attribute on opening tag
        clip_xml = re.sub(
            r'(<AudioClip Id="\d+" Time=")([-\d.]+)(")',
            lambda m: f'{m.group(1)}{float(m.group(2)) + delta_beats}{m.group(3)}',
            clip_xml,
        )
        # CurrentStart
        clip_xml = re.sub(
            r'(<CurrentStart Value=")([-\d.]+)(")',
            lambda m: f'{m.group(1)}{float(m.group(2)) + delta_beats}{m.group(3)}',
            clip_xml,
        )
        # CurrentEnd
        clip_xml = re.sub(
            r'(<CurrentEnd Value=")([-\d.]+)(")',
            lambda m: f'{m.group(1)}{float(m.group(2)) + delta_beats}{m.group(3)}',
            clip_xml,
        )
        return clip_xml

    new_track_body = re.sub(r'<AudioClip Id="\d+".*?</AudioClip>',
                             shift_clip, track_body, flags=re.DOTALL)

    return content[:track_start] + new_track_body + content[track_end:]


def main():
    if len(sys.argv) >= 3:
        in_path = Path(sys.argv[1])
        out_path = Path(sys.argv[2])
    else:
        # Default: V18 → V19
        in_path = Path("Test Project/Black Book x Defected V2/Output/Sections V18 Project/Sections V18.als")
        out_path = Path("Test Project/Black Book x Defected V2/Output/Sections V19.als")

    print(f"Reading {in_path}")
    with gzip.open(in_path, "rb") as f:
        content = f.read().decode("utf-8")
    print(f"  Loaded {len(content)} chars")

    tracks = parse_als_tracks(content)
    print(f"\n{len(tracks)} tracks parsed:")
    targets = compute_target_arr_starts(tracks)
    print(f"\n{'#':>2} {'Track':<45} {'current':>10} {'new':>10} {'delta':>8}")
    for i, (name, cur, new, delta) in enumerate(targets):
        marker = " ** SHIFT **" if abs(delta) > 0.5 else ""
        print(f"{i+1:>2} {name[:45]:<45} {cur:>10.0f} {new:>10.0f} {delta:>+8.0f}{marker}")

    print()
    for name, _, _, delta in targets:
        if abs(delta) > 0.5:
            print(f"Shifting '{name}' by {delta:+.0f} beats")
            content = shift_track_clips(content, name, delta)

    print(f"\nWriting {out_path}")
    with gzip.open(out_path, "wb") as f:
        f.write(content.encode("utf-8"))
    print(f"  Done — {out_path.stat().st_size} bytes")


if __name__ == "__main__":
    sys.exit(main() or 0)
