"""Read key/BPM from file tags, detect transients/downbeats, measure LUFS."""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field

import librosa
import numpy as np
import pyloudnorm as pyln
import mutagen
from mutagen.id3 import ID3
from mutagen.oggvorbis import OggVorbis
from mutagen.flac import FLAC

from automated_dj_mixes.sequencer import key_to_camelot

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".aiff", ".aif", ".ogg"}


@dataclass
class TrackAnalysis:
    path: Path
    key: str | None = None
    camelot: str | None = None
    bpm: float | None = None
    lufs: float | None = None
    first_downbeat_sec: float | None = None
    duration_sec: float | None = None
    sample_rate: int | None = None
    warnings: list[str] = field(default_factory=list)


def _read_tags(track_path: Path) -> dict[str, str | None]:
    """Read key and BPM from file tags. Handles ID3, Vorbis, FLAC."""
    key = None
    bpm = None

    try:
        audio = mutagen.File(track_path, easy=True)
        if audio is None:
            return {"key": None, "bpm": None}

        # BPM: common tag names
        for tag in ("bpm", "TBPM", "TEMPO"):
            val = audio.get(tag)
            if val:
                bpm = str(val[0]) if isinstance(val, list) else str(val)
                break

        # Key: Mixed In Key writes to "initialkey" or "TKEY"
        for tag in ("initialkey", "TKEY", "key"):
            val = audio.get(tag)
            if val:
                key = str(val[0]) if isinstance(val, list) else str(val)
                break
    except Exception:
        pass

    # Fallback: try raw ID3 tags for MP3
    if (key is None or bpm is None) and track_path.suffix.lower() == ".mp3":
        try:
            tags = ID3(track_path)
            if bpm is None and "TBPM" in tags:
                bpm = str(tags["TBPM"].text[0])
            if key is None and "TKEY" in tags:
                key = str(tags["TKEY"].text[0])
        except Exception:
            pass

    return {"key": key, "bpm": bpm}


def _detect_downbeat(y: np.ndarray, sr: int) -> float:
    """Detect the first strong downbeat (kick) position in seconds."""
    _, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    if len(beat_frames) == 0:
        return 0.0
    return float(librosa.frames_to_time(beat_frames[0], sr=sr))


def _measure_lufs(y: np.ndarray, sr: int) -> float:
    """Measure integrated LUFS of the audio signal."""
    meter = pyln.Meter(sr)
    if y.ndim == 1:
        y_stereo = np.stack([y, y])
    else:
        y_stereo = y
    loudness = meter.integrated_loudness(y_stereo.T)
    return float(loudness)


def analyse_track(track_path: Path) -> TrackAnalysis:
    """Analyse a single track: read tags, detect downbeat, measure loudness."""
    result = TrackAnalysis(path=track_path)

    tags = _read_tags(track_path)
    result.key = tags["key"]
    if result.key:
        result.camelot = key_to_camelot(result.key)
        if result.camelot is None:
            result.warnings.append(f"Key '{result.key}' could not be mapped to Camelot")

    if tags["bpm"]:
        try:
            result.bpm = float(tags["bpm"])
        except ValueError:
            result.warnings.append(f"Invalid BPM tag: {tags['bpm']}")

    y, sr = librosa.load(track_path, sr=None, mono=True)
    result.sample_rate = sr
    result.duration_sec = float(librosa.get_duration(y=y, sr=sr))

    if result.bpm is None:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        result.bpm = float(np.squeeze(tempo))
        result.warnings.append(f"BPM detected by librosa: {result.bpm:.1f}")

    result.first_downbeat_sec = _detect_downbeat(y, sr)

    y_full, sr_full = librosa.load(track_path, sr=None, mono=False)
    result.lufs = _measure_lufs(y_full, sr_full)

    return result


def analyse_folder(folder: Path) -> list[TrackAnalysis]:
    """Analyse all audio files in a folder."""
    tracks = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() in AUDIO_EXTENSIONS and not f.name.startswith("."):
            tracks.append(analyse_track(f))
    return tracks
