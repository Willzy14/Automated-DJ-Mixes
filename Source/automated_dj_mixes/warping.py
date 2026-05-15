"""Warp marker calculation from BPM + detected downbeat. Assumes constant BPM, 4/4 time."""

from __future__ import annotations

from dataclasses import dataclass

WARP_MODE_REPITCH = 6
WARP_MODE_COMPLEX_PRO = 4


@dataclass
class WarpMarker:
    """A warp marker aligning a beat position to a sample position.

    In Ableton's ALS XML:
      <WarpMarker SecTime="[sample_time]" BeatTime="[beat_time]" />
    """
    beat_time: float
    sample_time: float


def choose_warp_mode(track_bpm: float, project_bpm: float) -> int:
    """Choose warp mode based on BPM difference.

    Repitch sounds more natural but shifts pitch with tempo.
    Beyond ~1 BPM difference the pitch change becomes audible,
    so switch to Complex Pro (time-stretch without pitch change).
    """
    if abs(track_bpm - project_bpm) <= 1.0:
        return WARP_MODE_REPITCH
    return WARP_MODE_COMPLEX_PRO


def calculate_warp_markers(
    bpm: float,
    first_downbeat_sec: float,
    duration_sec: float,
    project_bpm: float = 128.0,
) -> list[WarpMarker]:
    """Calculate warp markers to align a track to the project grid.

    Places two markers: one at the first downbeat (anchoring beat 0)
    and one near the end. Ableton interpolates linearly between them,
    which works for constant-BPM tracks.
    """
    seconds_per_beat = 60.0 / bpm

    # First marker: align the first downbeat to beat 0
    markers = [WarpMarker(beat_time=0.0, sample_time=first_downbeat_sec)]

    # Second marker: place at the end to define the tempo relationship
    beats_in_track = (duration_sec - first_downbeat_sec) / seconds_per_beat
    markers.append(WarpMarker(beat_time=beats_in_track, sample_time=duration_sec))

    return markers
