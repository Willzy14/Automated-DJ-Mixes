"""Materialize coarse sections and short kick dropouts as colored ALS clips."""

from __future__ import annotations

import argparse
import copy
import gzip
import html
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import apply_loops
from apply_loops import (
    LoopSpec,
    _match_track,
    _normalise,
    clone_clip,
    compress_als,
    decompress_als,
    extract_first_clip_lines,
    find_clip_events,
    find_max_id,
    find_track_line_ranges,
)
from automated_dj_mixes.display_sections import (
    derive_display_sections,
    refine_intro_drop_boundary,
)
from automated_dj_mixes.warp_contract import extract_warp_grid_summaries
from extract_sections_als import parse_sections_als


def _source_range(lines: list[str], events_start: int, events_end: int) -> tuple[float, float]:
    starts: list[float] = []
    ends: list[float] = []
    for line in lines[events_start:events_end + 1]:
        start = re.search(r'<LoopStart Value="([^"]+)"', line)
        end = re.search(r'<LoopEnd Value="([^"]+)"', line)
        if start:
            starts.append(float(start.group(1)))
        if end:
            ends.append(float(end.group(1)))
    if not starts or not ends:
        raise ValueError("Track clips have no source range")
    return min(starts), max(ends)


def _clip_origin(template: list[str]) -> float:
    opening = next(line for line in template if "<AudioClip " in line)
    loop_start_line = next(line for line in template if "<LoopStart " in line)
    arr_time = float(re.search(r'Time="([^"]+)"', opening).group(1))
    source_start = float(re.search(r'Value="([^"]+)"', loop_start_line).group(1))
    return arr_time - source_start


def _set_clip_color(lines: list[str], color: int) -> None:
    for index, line in enumerate(lines):
        if "<Color Value=" in line:
            lines[index] = re.sub(
                r'Value="[^"]+"', f'Value="{int(color)}"', line, count=1
            )
            return
    raise ValueError("AudioClip template has no Color field")


def materialize_track(
    lines: list[str],
    track_name: str,
    stem_result: dict,
) -> tuple[list[dict], list[dict], dict | None]:
    matched = _match_track(track_name, find_track_line_ranges(lines))
    if matched is None:
        raise ValueError(f"Detailed-section track '{track_name}' was not found")
    track_start, track_end, _matched_name = matched
    events_start, events_end = find_clip_events(lines, track_start, track_end)
    first_span = extract_first_clip_lines(lines, events_start, events_end)
    if first_span is None:
        raise ValueError(f"Track '{track_name}' has no template AudioClip")
    source_start, source_end = _source_range(lines, events_start, events_end)
    if source_start != 0.0:
        raise ValueError(
            f"Track '{track_name}' starts at source beat {source_start}, not zero"
        )
    template = lines[first_span[0]:first_span[1] + 1]
    origin = _clip_origin(template)
    landmarks = stem_result.get("signals", {}).get("musical_landmarks", [])
    coarse, refinement = refine_intro_drop_boundary(
        stem_result["sections"],
        landmarks,
        bpm=float(stem_result["bpm"]),
        downbeat=float(stem_result["sections"][0]["start_sec"]),
    )
    display = derive_display_sections(
        coarse,
        landmarks,
        source_end_beat=source_end,
    )

    clips: list[str] = []
    for section in display:
        spec = LoopSpec(
            track_name=track_name,
            source_beat_start=section["start_beat"],
            source_beat_end=section["end_beat"],
            count=0,
            insert_at_beat=origin + section["start_beat"],
            clip_name=section["name"],
        )
        cloned = clone_clip(template, spec, spec.insert_at_beat)
        _set_clip_color(cloned, section["color"])
        clips.extend(cloned)
    lines[events_start + 1:events_end] = clips
    return coarse, display, refinement


def materialize(
    input_als: Path,
    stem_dir: Path,
    output_als: Path,
    track_names: list[str],
    *,
    sections_json: Path | None = None,
    display_json: Path | None = None,
    refined_stem_dir: Path | None = None,
) -> dict:
    lines = decompress_als(input_als)
    apply_loops._NEXT_ID = find_max_id(lines) + 100

    stems: dict[str, dict] = {}
    for name in track_names:
        clean_name = html.unescape(name)
        path = stem_dir / f"SECTIONS_STEM_{clean_name}.json"
        if not path.is_file():
            raise FileNotFoundError(f"Stem section analysis not found: {path}")
        stems[name] = json.loads(path.read_text(encoding="utf-8"))

    plans: dict[str, dict] = {}
    ordered = []
    track_ranges = find_track_line_ranges(lines)
    for name in track_names:
        matched = _match_track(name, track_ranges)
        if matched is None:
            raise ValueError(f"Track '{name}' was not found")
        ordered.append((matched[0], name))
    for _start, name in sorted(ordered, reverse=True):
        coarse, display, refinement = materialize_track(lines, name, stems[name])
        plans[name] = {
            "source_coarse_sections": stems[name]["sections"],
            "coarse_sections": coarse,
            "display_sections": display,
            "section_refinement": refinement,
        }

    compress_als(lines, output_als)
    with gzip.open(input_als, "rb") as handle:
        input_root = ET.fromstring(handle.read())
    with gzip.open(output_als, "rb") as handle:
        output_root = ET.fromstring(handle.read())
    before = extract_warp_grid_summaries(input_root, track_names)
    after = extract_warp_grid_summaries(output_root, track_names)
    if before != after:
        raise ValueError("Detailed section materialization changed a source warp grid")

    parsed = parse_sections_als(output_als)
    parsed_by_name = {_normalise(name): clips for name, clips in parsed.items()}
    for name in track_names:
        clips = parsed_by_name.get(_normalise(name), [])
        if len(clips) != len(plans[name]["display_sections"]):
            raise ValueError(f"Track '{name}' did not persist every display section")
    if sections_json is not None:
        sections_json.parent.mkdir(parents=True, exist_ok=True)
        sections_json.write_text(
            json.dumps(
                {
                    name: parsed_by_name[_normalise(name)]
                    for name in track_names
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    if display_json is not None:
        display_json.parent.mkdir(parents=True, exist_ok=True)
        display_json.write_text(
            json.dumps(plans, indent=2) + "\n", encoding="utf-8"
        )
    if refined_stem_dir is not None:
        refined_stem_dir.mkdir(parents=True, exist_ok=True)
        for name in track_names:
            result = copy.deepcopy(stems[name])
            result["sections"] = plans[name]["coarse_sections"]
            refinement = plans[name]["section_refinement"]
            if refinement is not None:
                result.setdefault("signals", {})["section_refinements"] = [refinement]
            clean_name = html.unescape(name)
            (refined_stem_dir / f"SECTIONS_STEM_{clean_name}.json").write_text(
                json.dumps(result, indent=1) + "\n", encoding="utf-8"
            )
    return plans


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_als", type=Path)
    parser.add_argument("stem_dir", type=Path)
    parser.add_argument("output_als", type=Path)
    parser.add_argument("--track", action="append", required=True)
    parser.add_argument("--sections-json", type=Path)
    parser.add_argument("--display-json", type=Path)
    parser.add_argument("--refined-stem-dir", type=Path)
    args = parser.parse_args()
    plans = materialize(
        args.input_als,
        args.stem_dir,
        args.output_als,
        args.track,
        sections_json=args.sections_json,
        display_json=args.display_json,
        refined_stem_dir=args.refined_stem_dir,
    )
    print(f"PASS: materialized detailed sections for {len(plans)} tracks")
    for name, plan in plans.items():
        refinement = plan["section_refinement"]
        note = (
            f"; intro/drop moved to beat {refinement['new_boundary_beat']}"
            if refinement else ""
        )
        print(f"  {name}: {len(plan['display_sections'])} clips{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
