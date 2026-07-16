"""Isolate selected Sections-layout tracks without rebuilding their ALS XML."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from apply_loops import (
    _normalise,
    compress_als,
    decompress_als,
    find_clip_events,
    find_track_line_ranges,
)


def _block_hash(lines: list[str], start: int, end: int) -> str:
    return hashlib.sha256("".join(lines[start:end + 1]).encode("utf-8")).hexdigest()


def isolate_track_events(
    lines: list[str], target_names: list[str]
) -> tuple[list[str], dict[str, str]]:
    """Empty non-target arrangement Events while preserving targets byte-for-byte."""
    output = list(lines)
    targets = {_normalise(name): name for name in target_names}
    source_tracks = find_track_line_ranges(lines)
    matched = {
        _normalise(name): _block_hash(lines, start, end)
        for start, end, name in source_tracks
        if _normalise(name) in targets
    }
    missing = [targets[key] for key in targets if key not in matched]
    if missing:
        raise ValueError(f"Target track(s) not found: {', '.join(missing)}")

    for start, end, name in reversed(source_tracks):
        if _normalise(name) in targets:
            continue
        events_start, events_end = find_clip_events(output, start, end)
        if events_start < 0 or events_start == events_end:
            continue
        del output[events_start + 1:events_end]

    output_tracks = find_track_line_ranges(output)
    result: dict[str, str] = {}
    for start, end, name in output_tracks:
        key = _normalise(name)
        if key not in matched:
            continue
        digest = _block_hash(output, start, end)
        if digest != matched[key]:
            raise ValueError(f"Retained track '{name}' changed during isolation")
        result[targets[key]] = digest
    return output, result


def select_sections(
    sections: dict[str, list[dict]], target_names: list[str]
) -> dict[str, list[dict]]:
    """Return requested tracks under their human-readable names."""
    available = {_normalise(name): value for name, value in sections.items()}
    selected = {
        name: available[_normalise(name)]
        for name in target_names
        if _normalise(name) in available
    }
    missing = [name for name in target_names if name not in selected]
    if missing:
        raise ValueError(
            f"Isolated section map is missing: {', '.join(missing)}"
        )
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_als", type=Path)
    parser.add_argument("output_als", type=Path)
    parser.add_argument("track_names", nargs="+")
    parser.add_argument("--sections-json", type=Path)
    args = parser.parse_args()

    lines = decompress_als(args.input_als)
    isolated, hashes = isolate_track_events(lines, args.track_names)
    compress_als(isolated, args.output_als)
    if args.sections_json is not None:
        from extract_sections_als import parse_sections_als

        sections = parse_sections_als(args.output_als)
        selected = select_sections(sections, args.track_names)
        args.sections_json.parent.mkdir(parents=True, exist_ok=True)
        args.sections_json.write_text(
            json.dumps(selected, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    print(f"PASS: isolated {len(hashes)} tracks with byte-identical XML")
    for name, digest in hashes.items():
        print(f"  {name}: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
