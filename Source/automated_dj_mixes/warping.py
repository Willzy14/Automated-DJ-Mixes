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


def calculate_warp_markers_from_beat_grid(
    beat_times_ms: list[int],
    bpm: float,
    duration_sec: float,
    first_downbeat_offset: int = 0,
) -> list[WarpMarker]:
    """Build PER-BEAT warp markers from Rekordbox's beat grid.

    One marker per BEAT (not per downbeat). The previous per-downbeat
    setup left 4 beats between markers, and Ableton's linear interpolation
    over that span slid inner beats off the grid on tracks with any
    micro-tempo drift. Per-beat markers eliminate the drift entirely.

    Pre-downbeat audio (PQTZ entries before the first beat_of_bar=1) is
    emitted as warp markers with NEGATIVE beat_time, so the very first
    audio beat is preserved and Ableton doesn't extrapolate backwards.

    first_downbeat_offset is the index of the first beat_of_bar=1 entry.
    Rekordbox grids can start on beat 2, 3, or 4 — warp beat 0 is the
    first true downbeat, earlier entries map to beats -1, -2, -3.

    Falls back to 2-marker calculation if the beat grid is too short.
    """
    if len(beat_times_ms) < 8:
        first_downbeat = beat_times_ms[0] / 1000.0 if beat_times_ms else 0.0
        return calculate_warp_markers(bpm, first_downbeat, duration_sec)

    markers: list[WarpMarker] = []
    for i in range(len(beat_times_ms)):
        beat_time = float(i - first_downbeat_offset)
        sample_time = beat_times_ms[i] / 1000.0
        markers.append(WarpMarker(beat_time=beat_time, sample_time=sample_time))

    return markers
