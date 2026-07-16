"""Warp marker calculation from BPM + detected downbeat. Assumes constant BPM, 4/4 time."""

from __future__ import annotations

import bisect
from dataclasses import dataclass

WARP_MODE_REPITCH = 6
WARP_MODE_COMPLEX_PRO = 4
DJ_MIX_REPITCH_LIMIT_BPM = 1.0
GRID_BPM_TOLERANCE = 0.05


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

    Repitch sounds more natural but shifts pitch with tempo — at 1 BPM on a
    126 track that's ~14 cents, which detunes an in-key mix (caught on the
    2026-06-12 rebuild: grid-true 127.0000 vs project 126 selected Repitch
    where MIK's 127.00002 had missed the old <=1.0 boundary by 2e-5). Only
    repitch when the shift is inaudible (<0.05 BPM ~ 0.7 cents); otherwise
    Complex Pro time-stretches without touching pitch.
    """
    if abs(track_bpm - project_bpm) <= 0.05:
        return WARP_MODE_REPITCH
    return WARP_MODE_COMPLEX_PRO


def choose_dj_mix_warp_mode(track_bpm: float, project_bpm: float) -> int:
    """Apply Sam's DJ-mix Re-Pitch rule with beat-grid drift tolerance.

    A nominal one-BPM move remains Re-Pitch. The extra 0.05 BPM prevents
    whole-track estimates such as 122.0045 from crossing the creative
    boundary because of tiny grid drift.
    """
    delta = abs(float(track_bpm) - float(project_bpm))
    if delta <= DJ_MIX_REPITCH_LIMIT_BPM + GRID_BPM_TOLERANCE:
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


# ── One-clock helpers (2026-06-11 warp/cut regression fix) ───────────────────
#
# The 09.06.26 mix surfaced a two-clock bug: section CUTS were computed on a
# constant librosa BPM (quantized to a ~2.5% lattice — it literally cannot
# output 128.00) while the AUDIO warps to the per-beat Rekordbox grid. Two
# clocks ~1% apart drift beats apart over a track, so every cut landed off
# the warped audio. These helpers make the grid the single clock: callers
# derive the detector's constant parameters from the grid itself, and map
# section times onto the clip's warp-beat coordinate exactly.


def grid_bpm_and_downbeat(
    beat_times_ms: list[int],
    first_downbeat_offset: int = 0,
    db_bpm: float | None = None,
) -> tuple[float | None, float | None]:
    """Effective constant BPM + true-downbeat anchor (sec) for a beat grid.

    Rekordbox's stored BPM (db_bpm) wins when it agrees with the grid span —
    it matches constant grids without the integer-ms quantization error of
    per-interval maths. The downbeat anchor is the first beat_of_bar=1 entry
    (first_downbeat_offset), NOT grid entry 0: many tracks start on beat
    2/3/4 of a bar, and warp beat 0 = the first TRUE downbeat.
    """
    if not beat_times_ms or len(beat_times_ms) < 2:
        return None, None
    n = len(beat_times_ms)
    span_ms = beat_times_ms[-1] - beat_times_ms[0]
    if span_ms <= 0:
        return None, None
    span_bpm = 60000.0 * (n - 1) / span_ms
    bpm = span_bpm
    if db_bpm and db_bpm > 40.0 and abs(db_bpm - span_bpm) / span_bpm < 0.05:
        bpm = float(db_bpm)
    off = min(max(first_downbeat_offset, 0), n - 1)
    return bpm, beat_times_ms[off] / 1000.0


def sec_to_clip_beats(
    sec: float,
    beat_times_ms: list[int],
    first_downbeat_offset: int = 0,
) -> float:
    """Map an audio time (seconds) to the clip's warp-beat coordinate.

    Matches calculate_warp_markers_from_beat_grid exactly: grid entry i sits
    at clip beat (i - first_downbeat_offset). Linear interpolation between
    grid entries; linear extrapolation past either end at the edge interval.
    This is THE conversion for placing section cuts on warped audio — a time
    mapped through here lands on the same musical moment the warp puts there,
    regardless of what any constant-BPM estimate says.
    """
    times = beat_times_ms
    n = len(times)
    if n == 0:
        return 0.0
    if n == 1:
        return float(-first_downbeat_offset)
    ms = sec * 1000.0
    if ms <= times[0]:
        iv = times[1] - times[0]
        idx = (ms - times[0]) / iv if iv > 0 else 0.0
    elif ms >= times[-1]:
        iv = times[-1] - times[-2]
        idx = (n - 1) + ((ms - times[-1]) / iv if iv > 0 else 0.0)
    else:
        j = bisect.bisect_right(times, ms) - 1
        iv = times[j + 1] - times[j]
        idx = j + ((ms - times[j]) / iv if iv > 0 else 0.0)
    return idx - first_downbeat_offset
