"""Warp marker calculation from BPM + detected downbeat. Assumes constant BPM, 4/4 time."""

from dataclasses import dataclass


@dataclass
class WarpMarker:
    beat_time: float
    sample_time: float


def calculate_warp_markers(bpm: float, first_downbeat_sec: float, duration_sec: float) -> list[WarpMarker]:
    """Calculate warp markers for a track given its BPM and first downbeat position."""
    raise NotImplementedError
