"""Canonical warp-grid fingerprints for ALS production contracts."""

from __future__ import annotations

import hashlib
import html
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class WarpGridSummary:
    marker_count: int
    grid_sha256: str
    source_grid_bpm: float


def _marker_pairs(clip: ET.Element) -> tuple[tuple[float, float], ...]:
    pairs = tuple(
        (float(marker.get("SecTime")), float(marker.get("BeatTime")))
        for marker in clip.iter("WarpMarker")
    )
    if len(pairs) < 2:
        raise ValueError("Warp grid has fewer than two markers")
    if any(not all(math.isfinite(value) for value in pair) for pair in pairs):
        raise ValueError("Warp grid contains a non-finite marker")
    return pairs


def summarize_warp_grid(clip: ET.Element) -> WarpGridSummary:
    pairs = _marker_pairs(clip)
    sec_delta = pairs[-1][0] - pairs[0][0]
    beat_delta = pairs[-1][1] - pairs[0][1]
    if sec_delta <= 0 or beat_delta <= 0:
        raise ValueError("Warp grid has non-positive duration")
    canonical = json.dumps(
        pairs, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
    return WarpGridSummary(
        marker_count=len(pairs),
        grid_sha256=hashlib.sha256(canonical).hexdigest(),
        source_grid_bpm=60.0 * beat_delta / sec_delta,
    )


def summarize_track_warp_grids(track: ET.Element) -> WarpGridSummary:
    summaries = [summarize_warp_grid(clip) for clip in track.iter("AudioClip")]
    if not summaries:
        raise ValueError("Track has no AudioClips")
    if any(summary != summaries[0] for summary in summaries[1:]):
        raise ValueError("AudioClips on the track do not share one warp grid")
    return summaries[0]


def _track_name(track: ET.Element) -> str:
    effective = next(track.iter("EffectiveName"), None)
    return html.unescape(effective.get("Value", "")) if effective is not None else ""


def extract_warp_grid_summaries(
    root: ET.Element, expected_names: Iterable[str]
) -> dict[str, WarpGridSummary]:
    tracks = {_track_name(track): track for track in root.iter("AudioTrack")}
    summaries: dict[str, WarpGridSummary] = {}
    for expected_name in expected_names:
        clean_name = html.unescape(expected_name)
        track = tracks.get(clean_name)
        if track is None:
            raise ValueError(f"Warp-contract track '{expected_name}' was not found")
        summaries[expected_name] = summarize_track_warp_grids(track)
    return summaries
