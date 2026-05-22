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

import argparse
import gzip
import json
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


def _entity_variants(s: str) -> list[str]:
    """Return possible entity-encoded variants of a string for XML search."""
    variants = [s]
    enc = (s.replace("&", "&amp;").replace("'", "&apos;")
             .replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;"))
    if enc != s:
        variants.append(enc)
    # Also try just apostrophe encoded (most common case where & is rare)
    apos_only = s.replace("'", "&apos;")
    if apos_only != s and apos_only not in variants:
        variants.append(apos_only)
    amp_only = s.replace("&", "&amp;")
    if amp_only != s and amp_only not in variants:
        variants.append(amp_only)
    return variants


def find_track_block(content: str, track_substr: str) -> tuple[int, int]:
    """Return (start, end) byte offsets of the AudioTrack block whose
    EffectiveName contains track_substr. Tolerant of XML entity encoding
    (the .als stores names with &apos; / &amp; etc., but corrections may
    be authored with literal apostrophes/ampersands)."""
    name_match = None
    last_variant = track_substr
    for variant in _entity_variants(track_substr):
        last_variant = variant
        name_match = re.search(
            rf'<EffectiveName Value="[^"]*{re.escape(variant)}[^"]*"', content
        )
        if name_match:
            break
    if not name_match:
        raise ValueError(f"Track containing '{track_substr}' not found "
                         f"(also tried entity-encoded variants)")

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


def read_clip_attr_value(clip_xml: str, attr: str) -> float | None:
    """Read the numeric Value of <{attr} Value="X" /> in the given clip XML."""
    m = re.search(rf'<{attr} Value="([\d.\-]+)"', clip_xml)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def delete_clip(content: str, track_substr: str, from_clip: str,
                to_clip: str, arr_offset_beats: int) -> str:
    """Merge `to_clip` into `from_clip` and remove the to_clip block.

    Used when the auto-flagger proposes deleting a boundary (e.g. a break
    that isn't a real break, or two near-identical adjacent sections).
    """
    track_start, track_end = find_track_block(content, track_substr)
    to_start, to_end = find_clip_block(content, track_start, track_end, to_clip)
    to_xml = content[to_start:to_end]

    # Extract to_clip's right edges so we can extend from_clip to absorb them.
    new_arr_end = read_clip_attr_value(to_xml, "CurrentEnd")
    new_src_end = read_clip_attr_value(to_xml, "LoopEnd")
    if new_arr_end is None or new_src_end is None:
        raise ValueError(f"Could not read CurrentEnd/LoopEnd from {to_clip}")

    # Read from_clip's CURRENT right edge (so we know what to replace).
    track_start, track_end = find_track_block(content, track_substr)
    fr_start, fr_end = find_clip_block(content, track_start, track_end, from_clip)
    fr_xml = content[fr_start:fr_end]
    old_arr_end = read_clip_attr_value(fr_xml, "CurrentEnd")
    old_src_end = read_clip_attr_value(fr_xml, "LoopEnd")
    if old_arr_end is None or old_src_end is None:
        raise ValueError(f"Could not read CurrentEnd/LoopEnd from {from_clip}")

    print(f"  DELETE: extend {from_clip} CurrentEnd {old_arr_end}→{new_arr_end}, "
          f"LoopEnd {old_src_end}→{new_src_end}, then remove {to_clip}")

    # Extend from_clip's right edges.
    changes = []
    for attr, old, new in [
        ("CurrentEnd", old_arr_end, new_arr_end),
        ("LoopEnd", old_src_end, new_src_end),
        ("OutMarker", old_src_end, new_src_end),
        ("HiddenLoopEnd", old_src_end, new_src_end),
    ]:
        fr_xml, changed = patch_clip_attr(fr_xml, attr, old, new)
        changes.append((attr, old, new, changed))
    print("    " + ", ".join(f"{a}={o}→{n}{'✓' if c else '✗'}"
                              for a, o, n, c in changes))
    content = content[:fr_start] + fr_xml + content[fr_end:]

    # Now remove the to_clip block. Re-find positions (content length shifted).
    track_start, track_end = find_track_block(content, track_substr)
    to_start, to_end = find_clip_block(content, track_start, track_end, to_clip)
    # Also swallow any trailing whitespace/newlines after the closing tag so
    # the XML stays tidy.
    while to_end < len(content) and content[to_end] in "\r\n\t ":
        to_end += 1
    content = content[:to_start] + content[to_end:]
    return content


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
    parser = argparse.ArgumentParser(
        description="Apply chop corrections to a Sections .als. Corrections are "
                    "either the hardcoded CORRECTIONS list at the top of this "
                    "file OR loaded from a JSON via --corrections-json. JSON "
                    "format: a list of [track_substr, from_clip, to_clip, "
                    "old_bar, new_bar_or_\"DELETE\", arr_offset_beats].")
    parser.add_argument("in_als", type=Path, help="Input Sections .als")
    parser.add_argument("out_als", type=Path, help="Output Sections .als")
    parser.add_argument("--corrections-json", type=Path, default=None,
                        help="Load corrections from JSON instead of using "
                             "the hardcoded CORRECTIONS list. Use the file "
                             "auto-generated by sections_blind_viz.py "
                             "(PROPOSED_CORRECTIONS_V<N>.json).")
    args = parser.parse_args()

    if args.corrections_json:
        if not args.corrections_json.exists():
            print(f"ERROR: {args.corrections_json} does not exist", file=sys.stderr)
            return 1
        corrections = json.loads(args.corrections_json.read_text(encoding="utf-8"))
        print(f"Loaded {len(corrections)} corrections from {args.corrections_json.name}")
    else:
        corrections = CORRECTIONS
        print(f"Using hardcoded CORRECTIONS list ({len(corrections)} entries)")

    print(f"Reading {args.in_als}")
    with gzip.open(args.in_als, "rb") as f:
        content = f.read().decode("utf-8")
    print(f"  Loaded {len(content)} chars")

    # Track which clips have been removed by DELETE operations so cascading
    # deletes can redirect their from_clip to whatever absorbed it.
    # Keyed by (track_substr, clip_name).
    absorbed_by: dict[tuple[str, str], str] = {}

    for correction in corrections:
        track_substr, from_clip, to_clip, old_bar, new_bar, arr_offset = correction
        # Redirect: if from_clip has been removed by a previous DELETE, walk
        # forward to whatever absorbed it.
        original_from = from_clip
        seen = set()
        while (track_substr, from_clip) in absorbed_by and from_clip not in seen:
            seen.add(from_clip)
            from_clip = absorbed_by[(track_substr, from_clip)]
        if from_clip != original_from:
            print(f"  (cascade: '{original_from}' was absorbed → using '{from_clip}')")

        if isinstance(new_bar, str) and new_bar.upper() == "DELETE":
            print(f"\nDelete boundary: {track_substr} {from_clip} ⇒ {to_clip}")
            content = delete_clip(content, track_substr, from_clip, to_clip, arr_offset)
            absorbed_by[(track_substr, to_clip)] = from_clip
        else:
            print(f"\nCorrection: {track_substr} bar {old_bar} → bar {new_bar} "
                  f"({from_clip}/{to_clip})")
            content = apply_correction(content, track_substr, from_clip, to_clip,
                                        int(old_bar), int(new_bar), int(arr_offset))

    print(f"\nWriting {args.out_als}")
    args.out_als.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.out_als, "wb") as f:
        f.write(content.encode("utf-8"))
    print(f"  Done — {args.out_als.stat().st_size} bytes")


if __name__ == "__main__":
    sys.exit(main() or 0)
