"""Read key/BPM from file tags, detect transients/downbeats, measure LUFS."""

from pathlib import Path
from dataclasses import dataclass


@dataclass
class TrackAnalysis:
    path: Path
    key: str | None = None
    camelot: str | None = None
    bpm: float | None = None
    lufs: float | None = None
    first_downbeat_sec: float | None = None


def analyse_track(track_path: Path) -> TrackAnalysis:
    """Analyse a single track: read tags, detect transients, measure loudness."""
    raise NotImplementedError


def analyse_folder(folder: Path) -> list[TrackAnalysis]:
    """Analyse all audio files in a folder."""
    raise NotImplementedError
