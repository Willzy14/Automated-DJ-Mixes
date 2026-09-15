"""Extract AudioClip section boundaries from a Sections-layout `.als` file.

Used to build the diff workflow between Claude's section analysis (Sections V1)
and Sam's manual edits (Sections V2 etc.). Each AudioClip is named by the
phrase_segments name (e.g. intro_1, drop_1, break_1, fill_1, outro_1), so we
parse them out and dump JSON keyed by track.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path


def parse_sections_als(als_path: Path) -> dict:
    """Return {track_name: [{label, label_n, time, end, source_start, source_end}, ...]}"""
    with gzip.open(als_path, "rb") as f:
        content = f.read().decode("utf-8")

    # Split by AudioTrack — each AudioTrack contains one or more AudioClips
    result: dict[str, list] = {}

    # Find each AudioTrack block
    track_blocks = re.split(r"<AudioTrack ", content)
    for block in track_blocks[1:]:
        # Each block now starts after "<AudioTrack ". Find its end (matching </AudioTrack>).
        end_idx = block.find("</AudioTrack>")
        if end_idx < 0:
            continue
        track_body = block[:end_idx]

        # Extract EffectiveName
        name_match = re.search(r'<EffectiveName Value="([^"]*)"', track_body)
        track_name = name_match.group(1) if name_match else "(unnamed)"
        if not track_name:
            continue

        # Find every AudioClip in this track. Split on the tag name only
        # ("<AudioClip ") - NOT on a fixed `Id="..." Time="..."` attribute
        # order (found in review: a real, silent-failure bug - valid XML
        # that reorders AudioClip's own attributes, or that Ableton itself
        # could plausibly write differently across versions, made every
        # clip in the track vanish from the parsed result with zero error,
        # falling `apply_automation` back to whatever sections JSON already
        # existed on disk - potentially stale - rather than the .als just
        # re-extracted). `Time` is now read the same attribute-order-
        # independent way every OTHER field on this clip already is
        # (CurrentEnd/LoopStart/LoopEnd/Name/Color below), searched only
        # within the clip's own OPENING TAG (up to its first unescaped `>`)
        # so it can never accidentally match an unrelated `Time=` deeper in
        # the clip body.
        clips = []
        clip_blocks = re.split(r"<AudioClip ", track_body)
        for cb in clip_blocks[1:]:
            tag_end = cb.find(">")
            open_tag = cb[:tag_end] if tag_end >= 0 else cb
            # Substring match on the attribute NAME, not anchored - would also match a
            # hypothetical StartTime=/EndTime="..." if one ever appeared on AudioClip's
            # opening tag. None do today (MiniMax review, 2026-09-15) - flagged, not fixed.
            time_match = re.search(r'Time="(-?[\d.]+)"', open_tag)
            if not time_match:
                continue
            arr_time = float(time_match.group(1))

            current_end_match = re.search(r'<CurrentEnd Value="(-?[\d.]+)"', cb)
            arr_end = float(current_end_match.group(1)) if current_end_match else None

            loop_start_match = re.search(r'<LoopStart Value="(-?[\d.]+)"', cb)
            loop_end_match = re.search(r'<LoopEnd Value="(-?[\d.]+)"', cb)
            src_start = float(loop_start_match.group(1)) if loop_start_match else None
            src_end = float(loop_end_match.group(1)) if loop_end_match else None

            name_match_clip = re.search(r'<Name Value="([^"]*)"', cb)
            clip_name = name_match_clip.group(1) if name_match_clip else "(unnamed)"

            color_match = re.search(r'<Color Value="(\d+)"', cb)
            color = int(color_match.group(1)) if color_match else None

            # Split name into label and index (intro_1 → intro, 1)
            label = clip_name
            label_n = None
            if "_" in clip_name:
                bits = clip_name.rsplit("_", 1)
                if bits[1].isdigit():
                    label = bits[0]
                    label_n = int(bits[1])

            clips.append({
                "name": clip_name,
                "label": label,
                "label_n": label_n,
                "arr_time": arr_time,
                "arr_end": arr_end,
                "arr_bars": arr_time / 4 if arr_time is not None else None,
                "arr_end_bars": arr_end / 4 if arr_end is not None else None,
                "source_start_beats": src_start,
                "source_end_beats": src_end,
                "color": color,
            })

        if clips:
            result[track_name] = sorted(clips, key=lambda c: c["arr_time"])

    return result


def main():
    if len(sys.argv) < 2:
        als = Path("Test Project/Black Book x Defected V2/Output/Sections V1.als")
    else:
        als = Path(sys.argv[1])

    if not als.exists():
        print(f"ERROR: {als} not found")
        return 1

    data = parse_sections_als(als)

    out_dir = als.parent.parent / "Sections Review"
    out_dir.mkdir(parents=True, exist_ok=True)
    # "Sections V1" writes BOTH V1_baseline.json AND Sections_V1.json with the
    # same content (2026-09-02: extract_sections_als wrote only V1_baseline.json
    # but validate_hints_vs_sections' hard gate looks for Sections_V<N>.json,
    # so every fresh project needed a manual copy step). The "Sections V1"
    # exact-stem check avoids matching V10/V11/V12 — those write only the
    # canonical Sections_V<N>.json name.
    if als.stem == "Sections V1":
        payloads = [("V1_baseline.json", "Sections_V1.json")]
    else:
        label = als.stem.replace(" ", "_")
        payloads = [(f"{label}.json",)]
    for names in payloads:
        out_json = out_dir / names[0]
        out_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"Saved: {out_json}")
        # Dual-write: V1 also lands as Sections_V1.json for the pipeline gate.
        if len(names) > 1:
            twin = out_dir / names[1]
            twin.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print(f"Saved: {twin}")

    # Pretty print summary
    print()
    for track_name, clips in data.items():
        if not track_name or "Audio" in track_name:
            continue  # skip empty template tracks
        print(f"\n{track_name}:")
        for c in clips:
            src = f"src[{c['source_start_beats']:.0f}..{c['source_end_beats']:.0f}]" if c["source_start_beats"] is not None else ""
            print(f"  bar {c['arr_bars']:>6.1f}..{c['arr_end_bars']:>6.1f}  {c['name']:<12}  color={c['color']:<3}  {src}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
