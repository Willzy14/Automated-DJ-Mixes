"""Compare a generated DJ-mix ALS with Sam's manually corrected ALS.

This is a read-only extractor. It reconstructs arrangement and source clocks,
checks warp-grid preservation, remaps corrected clips to the baseline section
map, and compares transition geometry plus Utility/bass automation.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

from learn_from_correction import (
    decompress_als,
    extract_track_automation,
    find_track_line_ranges,
)


@dataclass(frozen=True)
class ClipSnapshot:
    name: str
    arrangement_start: float
    arrangement_end: float
    source_start: float
    source_end: float
    color: int | None


@dataclass
class TrackSnapshot:
    name: str
    clips: list[ClipSnapshot]
    volume_points: list[tuple[float, float]] = field(default_factory=list)
    bass_points: list[tuple[float, float]] = field(default_factory=list)
    warp_mode: int | None = None
    warp_marker_count: int = 0
    warp_grid_sha256: str = ""

    @property
    def arrangement_start(self) -> float:
        return min(clip.arrangement_start for clip in self.clips)

    @property
    def arrangement_end(self) -> float:
        return max(clip.arrangement_end for clip in self.clips)


def _value(parent: ET.Element | None, tag: str, default: str = "") -> str:
    if parent is None:
        return default
    node = parent.find(tag)
    return node.get("Value", default) if node is not None else default


def _float_value(parent: ET.Element | None, tag: str, default: float = 0.0) -> float:
    value = _value(parent, tag, str(default))
    return float(value)


def _normalise_name(value: str) -> str:
    return html.unescape(value).strip().lower().replace("&apos;", "'")


def _direct_clip_name(clip: ET.Element) -> str:
    node = clip.find("Name")
    return html.unescape(node.get("Value", "")) if node is not None else ""


def _warp_signature(clip: ET.Element) -> tuple[int, str]:
    pairs = [
        (float(marker.get("SecTime", "0")), float(marker.get("BeatTime", "0")))
        for marker in clip.iter("WarpMarker")
    ]
    payload = json.dumps(pairs, separators=(",", ":")).encode("utf-8")
    return len(pairs), hashlib.sha256(payload).hexdigest()


def load_snapshot(path: Path) -> list[TrackSnapshot]:
    root = ET.fromstring(gzip.open(path, "rb").read())
    lines = decompress_als(path)
    automation = extract_track_automation(lines, find_track_line_ranges(lines))
    automation_by_name = {
        _normalise_name(name): values for name, values in automation.items()
    }

    tracks: list[TrackSnapshot] = []
    for track in root.iter("AudioTrack"):
        effective_name = track.find(".//Name/EffectiveName")
        name = html.unescape(
            effective_name.get("Value", "") if effective_name is not None else ""
        )
        clips: list[ClipSnapshot] = []
        xml_clips = list(track.iter("AudioClip"))
        for clip in xml_clips:
            loop = clip.find("Loop")
            clips.append(
                ClipSnapshot(
                    name=_direct_clip_name(clip),
                    arrangement_start=float(clip.get("Time", "0")),
                    arrangement_end=_float_value(
                        clip, "CurrentEnd", float(clip.get("Time", "0"))
                    ),
                    source_start=_float_value(loop, "LoopStart"),
                    source_end=_float_value(loop, "LoopEnd"),
                    color=(int(_value(clip, "Color")) if _value(clip, "Color") else None),
                )
            )
        if not clips:
            continue

        clips.sort(key=lambda item: (item.arrangement_start, item.source_start))
        marker_count, marker_hash = _warp_signature(xml_clips[0])
        warp_mode = _value(xml_clips[0], "WarpMode")
        auto = automation_by_name.get(_normalise_name(name))
        tracks.append(
            TrackSnapshot(
                name=name,
                clips=clips,
                volume_points=list(auto.volume_points) if auto else [],
                bass_points=list(auto.bass_points) if auto else [],
                warp_mode=int(warp_mode) if warp_mode else None,
                warp_marker_count=marker_count,
                warp_grid_sha256=marker_hash,
            )
        )
    tracks.sort(key=lambda item: item.arrangement_start)
    return tracks


def _match_track(tracks: list[TrackSnapshot], name: str) -> TrackSnapshot:
    wanted = _normalise_name(name)
    exact = [track for track in tracks if _normalise_name(track.name) == wanted]
    if len(exact) == 1:
        return exact[0]
    raise ValueError(f"Could not uniquely match track: {name}")


def _clip_at(track: TrackSnapshot, beat: float) -> ClipSnapshot | None:
    for clip in track.clips:
        if clip.arrangement_start - 1e-6 <= beat < clip.arrangement_end - 1e-6:
            return clip
    return None


def _source_at(track: TrackSnapshot, beat: float) -> float | None:
    clip = _clip_at(track, beat)
    if clip is None:
        return None
    return clip.source_start + beat - clip.arrangement_start


def _baseline_label(track: TrackSnapshot, source_beat: float | None) -> str | None:
    if source_beat is None:
        return None
    candidates = [
        clip
        for clip in track.clips
        if clip.source_start - 1e-6 <= source_beat < clip.source_end - 1e-6
    ]
    natural = [clip for clip in candidates if "tail_loop" not in clip.name]
    if natural:
        candidates = natural
    if not candidates:
        return None
    candidates.sort(key=lambda clip: (clip.source_end - clip.source_start, clip.name))
    return candidates[0].name


def _event_at(points: list[tuple[float, float]], beat: float) -> float | None:
    if not points:
        return None
    ordered = sorted(enumerate(points), key=lambda item: (item[1][0], item[0]))
    collapsed: list[tuple[float, float]] = []
    for _, (time, value) in ordered:
        if collapsed and math.isclose(collapsed[-1][0], time, abs_tol=1e-9):
            collapsed[-1] = (time, value)
        else:
            collapsed.append((time, value))
    if beat <= collapsed[0][0]:
        return collapsed[0][1]
    for (t1, v1), (t2, v2) in zip(collapsed, collapsed[1:]):
        if t1 <= beat <= t2:
            if math.isclose(t1, t2):
                return v2
            ratio = (beat - t1) / (t2 - t1)
            return v1 + ratio * (v2 - v1)
    return collapsed[-1][1]


def _transition_swap(
    outgoing: TrackSnapshot, incoming: TrackSnapshot, start: float, end: float
) -> dict:
    incoming_rises: list[float] = []
    previous = None
    for time, value in incoming.bass_points:
        if start - 1 <= time <= end + 1:
            if previous is not None and previous < 0.5 and value >= 0.9:
                incoming_rises.append(time)
            previous = value
        elif time < start:
            previous = value

    outgoing_kills: list[float] = []
    previous = None
    for time, value in outgoing.bass_points:
        if start - 1 <= time <= end + 1:
            if previous is not None and previous >= 0.8 and value <= 0.25:
                outgoing_kills.append(time)
            previous = value
        elif time < start:
            previous = value

    swap = incoming_rises[0] if incoming_rises else (
        outgoing_kills[0] if outgoing_kills else None
    )
    return {
        "beat": swap,
        "incoming_rise_beat": incoming_rises[0] if incoming_rises else None,
        "outgoing_kill_beat": outgoing_kills[0] if outgoing_kills else None,
    }


def _repeat_groups(track: TrackSnapshot) -> list[dict]:
    groups: list[dict] = []
    index = 0
    while index < len(track.clips):
        clip = track.clips[index]
        end = index + 1
        while end < len(track.clips):
            other = track.clips[end]
            if not (
                math.isclose(other.source_start, clip.source_start, abs_tol=1e-6)
                and math.isclose(other.source_end, clip.source_end, abs_tol=1e-6)
                and math.isclose(
                    other.arrangement_start,
                    track.clips[end - 1].arrangement_end,
                    abs_tol=1e-6,
                )
            ):
                break
            end += 1
        if end - index >= 2:
            groups.append(
                {
                    "arrangement_start": clip.arrangement_start,
                    "arrangement_end": track.clips[end - 1].arrangement_end,
                    "source_start": clip.source_start,
                    "source_end": clip.source_end,
                    "phrase_beats": clip.source_end - clip.source_start,
                    "repeat_count": end - index,
                }
            )
        index = end
    return groups


def _label_repeat_groups(groups: list[dict], baseline: TrackSnapshot) -> list[dict]:
    return [
        {
            **group,
            "baseline_section": _baseline_label(
                baseline, (group["source_start"] + group["source_end"]) / 2
            ),
        }
        for group in groups
    ]


def _zero_gates(track: TrackSnapshot, start: float, end: float) -> list[dict]:
    points = [(t, v) for t, v in track.volume_points if start <= t <= end]
    gates: list[dict] = []
    for (t1, v1), (t2, v2) in zip(points, points[1:]):
        if v1 <= 0.001 and v2 <= 0.001 and t2 > t1:
            gates.append({"start": t1, "end": t2, "duration_beats": t2 - t1})
    return gates


def _cue(track: TrackSnapshot, baseline: TrackSnapshot, beat: float | None) -> dict:
    if beat is None:
        return {"arrangement_beat": None, "source_beat": None, "section": None}
    source = _source_at(track, beat)
    return {
        "arrangement_beat": beat,
        "source_beat": source,
        "section": _baseline_label(baseline, source),
    }


def _transition_snapshot(
    index: int,
    outgoing: TrackSnapshot,
    incoming: TrackSnapshot,
    baseline_outgoing: TrackSnapshot,
    baseline_incoming: TrackSnapshot,
) -> dict:
    start = incoming.arrangement_start
    end = outgoing.arrangement_end
    swap = _transition_swap(outgoing, incoming, start, end)
    return {
        "index": index,
        "outgoing": outgoing.name,
        "incoming": incoming.name,
        "overlap_start": start,
        "overlap_end": end,
        "overlap_beats": end - start,
        "overlap_bars": (end - start) / 4,
        "incoming_entry_level": _event_at(incoming.volume_points, start),
        "outgoing_exit_level": _event_at(outgoing.volume_points, end - 1e-6),
        "swap": swap,
        "incoming_entry_cue": _cue(incoming, baseline_incoming, start),
        "outgoing_exit_cue": _cue(outgoing, baseline_outgoing, end - 1e-6),
        "incoming_swap_cue": _cue(incoming, baseline_incoming, swap["beat"]),
        "outgoing_swap_cue": _cue(outgoing, baseline_outgoing, swap["beat"]),
        "incoming_zero_gates": _zero_gates(incoming, start, end),
    }


def analyse(baseline_path: Path, corrected_path: Path) -> dict:
    baseline = load_snapshot(baseline_path)
    corrected = load_snapshot(corrected_path)
    if len(baseline) != len(corrected):
        raise ValueError(
            f"Track count changed: {len(baseline)} baseline vs {len(corrected)} corrected"
        )

    track_rows: list[dict] = []
    baseline_transitions: list[dict] = []
    corrected_transitions: list[dict] = []
    ordered_corrected: list[TrackSnapshot] = []
    for base_track in baseline:
        edit_track = _match_track(corrected, base_track.name)
        ordered_corrected.append(edit_track)
        track_rows.append(
            {
                "name": base_track.name,
                "start_delta_beats": edit_track.arrangement_start
                - base_track.arrangement_start,
                "end_delta_beats": edit_track.arrangement_end - base_track.arrangement_end,
                "baseline_clip_count": len(base_track.clips),
                "corrected_clip_count": len(edit_track.clips),
                "warp_mode_preserved": base_track.warp_mode == edit_track.warp_mode,
                "warp_marker_count_preserved": base_track.warp_marker_count
                == edit_track.warp_marker_count,
                "warp_grid_preserved": base_track.warp_grid_sha256
                == edit_track.warp_grid_sha256,
                "baseline_repeat_groups": _label_repeat_groups(
                    _repeat_groups(base_track), base_track
                ),
                "corrected_repeat_groups": _label_repeat_groups(
                    _repeat_groups(edit_track), base_track
                ),
            }
        )

    for index in range(len(baseline) - 1):
        base_out = baseline[index]
        base_in = baseline[index + 1]
        edit_out = ordered_corrected[index]
        edit_in = ordered_corrected[index + 1]
        baseline_transitions.append(
            _transition_snapshot(index + 1, base_out, base_in, base_out, base_in)
        )
        corrected_transitions.append(
            _transition_snapshot(index + 1, edit_out, edit_in, base_out, base_in)
        )

    transition_rows: list[dict] = []
    for base, edit in zip(baseline_transitions, corrected_transitions):
        transition_rows.append(
            {
                "index": base["index"],
                "outgoing": base["outgoing"],
                "incoming": base["incoming"],
                "overlap_delta_beats": edit["overlap_beats"] - base["overlap_beats"],
                "swap_delta_beats": (
                    edit["swap"]["beat"] - base["swap"]["beat"]
                    if edit["swap"]["beat"] is not None
                    and base["swap"]["beat"] is not None
                    else None
                ),
                "baseline": base,
                "corrected": edit,
            }
        )

    return {
        "schema_version": "correction_diff_v1",
        "baseline_als": str(baseline_path),
        "corrected_als": str(corrected_path),
        "track_count": len(track_rows),
        "transition_count": len(transition_rows),
        "all_warp_grids_preserved": all(
            row["warp_grid_preserved"]
            and row["warp_marker_count_preserved"]
            and row["warp_mode_preserved"]
            for row in track_rows
        ),
        "tracks": track_rows,
        "transitions": transition_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_als", type=Path)
    parser.add_argument("corrected_als", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = analyse(args.baseline_als, args.corrected_als)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"Saved: {args.output}")
    else:
        print(text)
    print(
        f"Compared {result['track_count']} tracks / "
        f"{result['transition_count']} transitions; "
        f"warp grids preserved={result['all_warp_grids_preserved']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
