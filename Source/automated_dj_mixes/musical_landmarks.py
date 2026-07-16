"""Contextual musical landmarks that sit inside coarse track sections."""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np


MIN_KICK_GAP_BEATS = 2
PRE_DROP_WINDOW_BEATS = 4
MAX_PRE_DROP_GAP_BEATS = 16


def _section_at_beat(sections: Iterable[dict], beat: int) -> dict | None:
    bar = beat / 4.0
    return next(
        (section for section in sections
         if float(section["start_bar"]) <= bar < float(section["end_bar"])),
        None,
    )


def _off_runs(presence: np.ndarray) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    values = np.asarray(presence, dtype=bool)
    index = 1
    while index < len(values):
        if not values[index] and values[index - 1]:
            end = index
            while end < len(values) and not values[end]:
                end += 1
            runs.append((index, end))
            index = end
        else:
            index += 1
    return runs


def extract_kick_dropout_landmarks(
    raw_presence,
    section_presence,
    sections: list[dict],
    *,
    bpm: float,
    downbeat: float,
    kick_peaks=None,
    kick_reference: float | None = None,
    source: str = "kick-presence",
    min_gap_beats: int = MIN_KICK_GAP_BEATS,
) -> list[dict]:
    """Preserve short kick-off runs without splitting the section map."""
    raw = np.asarray(raw_presence, dtype=bool)
    section_on = np.asarray(section_presence, dtype=bool)
    if len(raw) != len(section_on):
        raise ValueError("Raw and section kick-presence arrays must have equal length")
    if not math.isfinite(bpm) or bpm <= 0:
        raise ValueError(f"Invalid BPM for musical landmarks: {bpm!r}")

    peaks = np.asarray(kick_peaks, dtype=float) if kick_peaks is not None else None
    drop_starts = sorted(
        int(round(float(section["start_bar"]) * 4.0))
        for section in sections
        if section.get("label") == "drop"
    )
    beat_sec = 60.0 / bpm
    landmarks: list[dict] = []

    for start, end in _off_runs(raw):
        duration = end - start
        if duration < min_gap_beats:
            continue
        next_drop = next((beat for beat in drop_starts if beat >= end), None)
        beats_to_drop = next_drop - end if next_drop is not None else None
        pre_drop = (
            end < len(raw)
            and duration <= MAX_PRE_DROP_GAP_BEATS
            and beats_to_drop is not None
            and 0 <= beats_to_drop <= PRE_DROP_WINDOW_BEATS
        )
        section = _section_at_beat(sections, start)
        bridged = bool(np.all(section_on[start:end]))

        energy_off_fraction = None
        if (peaks is not None and kick_reference is not None
                and kick_reference > 0 and len(peaks) >= end):
            energy_off_fraction = float(
                np.mean(peaks[start:end] < 0.55 * kick_reference)
            )

        if duration >= 4 and (energy_off_fraction is None or energy_off_fraction >= 0.5):
            confidence = "high"
        elif duration >= 2:
            confidence = "medium"
        else:
            confidence = "low"

        roles = ["transition_boundary", "automation_pivot"]
        if pre_drop:
            roles.extend(["transition_end", "incoming_ownership"])
        if duration >= 4:
            roles.append("bass_swap_candidate")

        landmarks.append({
            "landmark_id": f"kick_gap_{start}_{end}",
            "type": "pre_drop_kick_gap" if pre_drop else "kick_dropout",
            "source": source,
            "start_beat": start,
            "end_beat": end,
            "duration_beats": duration,
            "start_bar": round(start / 4.0, 2),
            "end_bar": round(end / 4.0, 2),
            "start_sec": round(downbeat + start * beat_sec, 2),
            "end_sec": round(downbeat + end * beat_sec, 2),
            "section_name": section.get("name") if section else None,
            "section_label": section.get("label") if section else None,
            "beats_to_next_drop": beats_to_drop,
            "returns_before_drop": pre_drop,
            "section_signal_bridged": bridged,
            "energy_off_fraction": (
                round(energy_off_fraction, 3)
                if energy_off_fraction is not None else None
            ),
            "confidence": confidence,
            "candidate_roles": roles,
        })
    return landmarks
