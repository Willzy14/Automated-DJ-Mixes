"""Per-track beat-grid carrier - the ONE grid object every consumer reads.

Historically this dataclass was RekordboxAnalysis in the archived RB reader
module (now in Source/Archive/). Rekordbox was retired 2026-08-18
(Sam: "done away with"); the owned stem-grid detector and .asd tick fits
populate the same carrier. Field names are kept identical so every grid
consumer (warp markers, one-clock section cuts, the beatgrid gate, grid
overrides) is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TrackGrid:
    """Beat grid + metadata for a single track (one-clock source of truth)."""
    file_path: str
    title: str
    bpm: float
    key_name: str | None
    mood: int
    end_beat: int                       # total beats in track
    phrases: list                       # always [] - phrase analysis retired
    beat_times_ms: list[int]            # millisecond timestamp per beat
    first_downbeat_offset: int = 0      # index of first beat_of_bar=1 entry
    ext_path: str = ""                  # legacy field, unused
