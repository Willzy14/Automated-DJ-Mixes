"""Derive visual Ableton sections from coarse structure and raw landmarks."""

from __future__ import annotations

import copy
import math

from automated_dj_mixes.phrase_viz import LABEL_TO_COLOR


MAX_INTRO_BOUNDARY_SHIFT_BEATS = 16
MAX_DISPLAY_DROPOUT_BEATS = 16


def _merged_kick_gaps(landmarks: list[dict]) -> list[tuple[int, int]]:
    gaps = sorted(
        (int(item["start_beat"]), int(item["end_beat"]))
        for item in landmarks
        if item.get("type") in {"kick_dropout", "pre_drop_kick_gap"}
    )
    merged: list[tuple[int, int]] = []
    for start, end in gaps:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def refine_intro_drop_boundary(
    sections: list[dict],
    landmarks: list[dict],
    *,
    bpm: float,
    downbeat: float,
) -> tuple[list[dict], dict | None]:
    """Move an early intro/drop boundary to the stable kick return."""
    refined = copy.deepcopy(sections)
    if len(refined) < 2:
        return refined, None
    intro, drop = refined[0], refined[1]
    if intro.get("label") != "intro" or drop.get("label") != "drop":
        return refined, None
    if not math.isfinite(bpm) or bpm <= 0:
        raise ValueError(f"Invalid BPM for intro refinement: {bpm!r}")

    boundary = int(round(float(intro["end_bar"]) * 4.0))
    candidate = next(
        (
            (start, end) for start, end in _merged_kick_gaps(landmarks)
            if start < boundary < end
            and 0 < end - boundary <= MAX_INTRO_BOUNDARY_SHIFT_BEATS
        ),
        None,
    )
    if candidate is None:
        return refined, None

    _start, stable_return = candidate
    new_bar = stable_return / 4.0
    if not new_bar.is_integer() or new_bar >= float(drop["end_bar"]):
        return refined, None
    beat_sec = 60.0 / bpm
    boundary_sec = round(downbeat + stable_return * beat_sec, 2)
    old_bar = float(intro["end_bar"])
    intro["end_bar"] = int(new_bar)
    intro["end_sec"] = boundary_sec
    drop["start_bar"] = int(new_bar)
    drop["start_sec"] = boundary_sec
    return refined, {
        "type": "intro_drop_to_stable_kick_return",
        "old_boundary_bar": old_bar,
        "new_boundary_bar": new_bar,
        "new_boundary_beat": stable_return,
        "evidence_gap_start_beat": candidate[0],
        "evidence_gap_end_beat": candidate[1],
    }


def derive_display_sections(
    coarse_sections: list[dict],
    landmarks: list[dict],
    *,
    source_end_beat: float,
) -> list[dict]:
    """Split coarse sections around every short Kick V3 dropout."""
    if not math.isfinite(source_end_beat) or source_end_beat <= 0:
        raise ValueError(f"Invalid source end beat: {source_end_beat!r}")
    detail_gaps = sorted(
        (float(item["start_beat"]), float(item["end_beat"]), item)
        for item in landmarks
        if item.get("type") in {"kick_dropout", "pre_drop_kick_gap"}
        and 0 < float(item["duration_beats"]) <= MAX_DISPLAY_DROPOUT_BEATS
    )

    raw: list[dict] = []
    for section in coarse_sections:
        start = max(0.0, float(section["start_bar"]) * 4.0)
        end = min(source_end_beat, float(section["end_bar"]) * 4.0)
        if end <= start:
            continue
        cursor = start
        for gap_start, gap_end, landmark in detail_gaps:
            clipped_start = max(start, gap_start)
            clipped_end = min(end, gap_end)
            if clipped_end <= clipped_start:
                continue
            if clipped_start > cursor:
                raw.append({
                    "label": section["label"],
                    "start_beat": cursor,
                    "end_beat": clipped_start,
                    "parent_section": section.get("name"),
                })
            raw.append({
                "label": "beat_dropout",
                "start_beat": clipped_start,
                "end_beat": clipped_end,
                "parent_section": section.get("name"),
                "landmark_id": landmark["landmark_id"],
                "landmark_type": landmark["type"],
            })
            cursor = max(cursor, clipped_end)
        if cursor < end:
            raw.append({
                "label": section["label"],
                "start_beat": cursor,
                "end_beat": end,
                "parent_section": section.get("name"),
            })

    if not raw or raw[0]["start_beat"] != 0.0:
        raise ValueError("Display sections do not start at source beat zero")
    for previous, current in zip(raw, raw[1:]):
        if not math.isclose(previous["end_beat"], current["start_beat"], abs_tol=1e-6):
            raise ValueError("Display sections contain a gap or overlap")
    if not math.isclose(raw[-1]["end_beat"], source_end_beat, abs_tol=1e-6):
        raise ValueError("Display sections do not cover the certified source range")

    counts: dict[str, int] = {}
    for section in raw:
        label = section["label"]
        counts[label] = counts.get(label, 0) + 1
        section["name"] = f"{label}_{counts[label]}"
        section["color"] = LABEL_TO_COLOR.get(label, LABEL_TO_COLOR["unknown"])
    return raw
