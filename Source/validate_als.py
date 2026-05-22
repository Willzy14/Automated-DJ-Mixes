"""Validate that a Sections .als file is parseable by Ableton.

Catches the class of bug that produced corrupt V4 in 22.05.26 Mix —
where `apply_loops.py` overwrote integer fields with clip-name strings,
making Ableton refuse to load the file with:

    "Unexpected value for int node: drop_4_tail_loop"

The gate has two layers:
  1. STRUCTURAL — gzip-decompress and parse the XML. Catches any malformed
     element, unclosed tag, etc.
  2. TYPE — walk known elements that Ableton expects to hold integers
     and confirm their Value attribute parses as int. Add more known-int
     element paths to KNOWN_INT_PATHS as new corruption modes surface.

Exit codes:
  0 — clean, Ableton will load it
  1 — usage error / file missing
  2 — structural or type error (with specific element pointed out)

CLI:
  python validate_als.py <path.als>
"""

from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path
import xml.etree.ElementTree as ET


# Element paths (parent/child under <AudioClip>) whose Value attribute
# must parse as int. Each entry: (parent_tag, child_tag).
# Add more here as new corruption patterns are found.
KNOWN_INT_CHILDREN = [
    ("ScaleInformation", "Root"),   # 0-11, scale root
    ("ScaleInformation", "Name"),   # 0-11, scale name index — V4 22.05.26 bug
    ("ScaleInformation", "Cipher"),
]

# Top-level int-valued elements that appear inside an AudioClip (not nested).
KNOWN_INT_TOPLEVEL = [
    "Type",
    "Disabled",          # actually bool but Ableton stores 'true'/'false'
]


def _is_int(s: str) -> bool:
    try:
        int(s)
        return True
    except (ValueError, TypeError):
        return False


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def validate_als(path: Path) -> list[str]:
    """Return a list of error messages. Empty list = file is clean."""
    errors: list[str] = []

    # --- Layer 1: gzip + XML structural parse ---
    try:
        with gzip.open(path, "rb") as g:
            content_bytes = g.read()
    except Exception as e:
        return [f"Cannot gzip-decompress {path.name}: {e}"]

    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        return [f"UTF-8 decode failed in {path.name}: {e}"]

    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        # Try to surface the line/column from the error
        return [f"XML parse error in {path.name}: {e}"]

    # --- Layer 2: type checks on known-int fields ---
    for clip in root.iter("AudioClip"):
        clip_name = None
        # Find this clip's own <Name> for context in error messages.
        # The AudioClip-level Name is a direct child, not nested.
        for child in clip:
            if child.tag == "Name":
                clip_name = child.get("Value", "?")
                break

        for parent_tag, child_tag in KNOWN_INT_CHILDREN:
            for parent in clip.iter(parent_tag):
                child = parent.find(child_tag)
                if child is None:
                    continue
                val = child.get("Value")
                if val is None:
                    errors.append(
                        f"Clip '{clip_name}': <{parent_tag}><{child_tag}/> "
                        f"has no Value attribute"
                    )
                elif not _is_int(val):
                    errors.append(
                        f"Clip '{clip_name}': <{parent_tag}><{child_tag} "
                        f"Value=\"{val}\"/> is not an integer "
                        f"(Ableton rejects this with 'Unexpected value for "
                        f"int node: {val}')"
                    )

    # --- Layer 3: clip sanity — no zero/negative-length clips ---
    # Catches the failure mode where apply_section_corrections pushes a
    # boundary past the to_clip's end, leaving the clip with start > end.
    # Ableton may "load" this but the clip is invisible / unplayable.
    for clip in root.iter("AudioClip"):
        clip_name = "?"
        for child in clip:
            if child.tag == "Name":
                clip_name = child.get("Value", "?")
                break

        def _f(elem_tag):
            el = clip.find(elem_tag)
            if el is None:
                return None
            v = el.get("Value")
            try:
                return float(v)
            except (ValueError, TypeError):
                return None

        cs = _f("CurrentStart")
        ce = _f("CurrentEnd")
        ls = _f("LoopStart")
        le = _f("LoopEnd")

        if cs is not None and ce is not None and ce <= cs:
            errors.append(
                f"Clip '{clip_name}': CurrentEnd ({ce}) <= CurrentStart ({cs}) "
                f"— zero/negative arrangement length. Clip will be invisible "
                f"in Ableton or behave strangely."
            )
        if ls is not None and le is not None and le <= ls:
            errors.append(
                f"Clip '{clip_name}': LoopEnd ({le}) <= LoopStart ({ls}) "
                f"— zero/negative source length. The audio region "
                f"references nothing playable."
            )

    # --- Layer 4: track ordering — AudioTrack order in the .als file
    # MUST match the time order of the tracks' first clips. Catches the
    # symptom of misapplied shifts, regardless of root cause. Bug we
    # actually shipped: Mike Richters - Your Love and Your Love
    # (Instrumental Mix) collided on _match_track's loose-prefix path,
    # and both shifts hit the Instrumental track. Result: track #20
    # played at the start of the mix and track #19 played at the end.
    #
    # The orchestrator's --sections-layout places AudioTracks in
    # Camelot+BPM-sequenced order. So .als-file order = expected play
    # order. If after shifts a later-in-file track ends up earlier in
    # time than an earlier-in-file track, something is wrong.
    audio_tracks_in_file_order = []
    for at in root.iter("AudioTrack"):
        # Pull the EffectiveName for the error message
        name = "?"
        for ename in at.iter("EffectiveName"):
            name = ename.get("Value", "?")
            break
        # Pull the earliest clip's Time
        times = []
        for clip in at.iter("AudioClip"):
            t = clip.get("Time")
            try:
                times.append(float(t))
            except (ValueError, TypeError):
                pass
        if not times:
            continue  # ignore tracks with no clips (Return tracks, Session Time, etc.)
        audio_tracks_in_file_order.append((min(times), name))

    # Filter to AudioTracks that actually carry audio — the Ableton template
    # has 35 audio slots but only ~20 are used per project; empty slots get
    # filtered out above (no clips → no entry).
    if len(audio_tracks_in_file_order) >= 2:
        prev_t, prev_name = audio_tracks_in_file_order[0]
        for t, name in audio_tracks_in_file_order[1:]:
            if t < prev_t:
                errors.append(
                    f"Track ordering: '{name}' plays at arr_time {t} but the "
                    f"previous track in file order ('{prev_name}') plays at "
                    f"{prev_t} (later). Tracks are out of sequence in the "
                    f"arrangement — likely a shift was applied to the wrong "
                    f"AudioTrack (see _match_track collision class, 22.05.26)."
                )
            prev_t, prev_name = t, name

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("als_path", type=Path)
    args = parser.parse_args()

    if not args.als_path.exists():
        print(f"ERROR: {args.als_path} does not exist", file=sys.stderr)
        return 1

    errs = validate_als(args.als_path)
    if errs:
        print(f"FAIL  {args.als_path.name}: {len(errs)} issue(s)",
              file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        print(f"\nAbleton would reject this file. Fix the upstream script "
              f"that wrote it (apply_loops.py, apply_automation.py, etc.) "
              f"before proceeding.", file=sys.stderr)
        return 2

    print(f"PASS  {args.als_path.name}: structurally valid + known int "
          f"fields type-correct. Ableton can load this.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
