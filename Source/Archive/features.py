"""Per-beat feature extraction with disk caching.

Combines librosa (overall RMS + 40-180Hz bass band) with Rekordbox waveform
data (height/r/g/b per beat) into a single BeatFeatures stream. Per-track
caching avoids re-running librosa on every viz iteration.

ANALYSIS_MODEL_VERSION = "cue-candidates-v1"

Cache key includes: audio path, mtime, size, ANALYSIS_MODEL_VERSION,
WAVEFORM_PARSER_VERSION. Bump versions to invalidate.
"""

from __future__ import annotations

import hashlib
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import librosa
import numpy as np

from automated_dj_mixes.rekordbox_waveform import (
    ANALYSIS_MODEL_VERSION,
    WAVEFORM_PARSER_VERSION,
    parse_waveform,
    waveform_per_beat,
)

DEFAULT_CACHE_DIR = Path(
    "F:/Wired Masters Dropbox/Sam Wills/0.1---GIT HUB---/"
    "Automated DJ Mixes/Test Project/May 2026 Mix/Analysis Cache"
)


@dataclass
class BeatFeatures:
    """One beat's worth of analysis signals."""
    beat_index: int               # 0-based, counted from first downbeat
    sec: float                    # source-seconds where this beat starts
    rms: float                    # librosa overall RMS (normalized 0-1 to track max)
    bass: float                   # librosa 40-180 Hz band RMS (normalized 0-1)
    wf_height: float | None       # PWV5 height (normalized 0-1) or None
    wf_r: float | None            # PWV5 red channel (normalized 0-1) or None
    wf_g: float | None
    wf_b: float | None


@dataclass
class FeatureStats:
    """Per-track percentile distribution for a single feature stream."""
    p30: float
    p50: float
    p70: float


@dataclass
class TrackFeatures:
    """All per-beat features for one track plus per-track stats."""
    audio_path: str
    bpm: float
    beat_times_ms: list[int]
    first_downbeat_offset: int
    beats: list[BeatFeatures]
    stats: dict[str, FeatureStats]   # keys: rms, bass, wf_height
    waveform_source: str             # "PWV5" | "PWV4" | "none"
    analysis_model_version: str = ANALYSIS_MODEL_VERSION
    waveform_parser_version: str = WAVEFORM_PARSER_VERSION


def _cache_key(audio_path: Path, ext_path: Path | None) -> str:
    """Hash that invalidates when audio/EXT change or analysis version bumps."""
    parts = [
        str(audio_path.resolve()),
        str(audio_path.stat().st_mtime),
        str(audio_path.stat().st_size),
        ANALYSIS_MODEL_VERSION,
        WAVEFORM_PARSER_VERSION,
    ]
    if ext_path and ext_path.exists():
        parts.append(str(ext_path.stat().st_mtime))
    h = hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]
    return h


def _cache_path(audio_path: Path, ext_path: Path | None, cache_dir: Path) -> Path:
    key = _cache_key(audio_path, ext_path)
    stem = audio_path.stem[:40].replace("/", "_").replace("\\", "_")
    return cache_dir / f"{stem}_{key}.pkl"


def _normalize(values: list[float]) -> list[float]:
    """Divide by max so values sit in 0-1. Empty/zero-only lists return unchanged."""
    if not values:
        return values
    m = max(values)
    if m <= 0:
        return values
    return [v / m for v in values]


def _percentiles(values: list[float]) -> FeatureStats:
    if not values:
        return FeatureStats(p30=0.0, p50=0.0, p70=0.0)
    arr = np.asarray(values)
    return FeatureStats(
        p30=float(np.percentile(arr, 30)),
        p50=float(np.percentile(arr, 50)),
        p70=float(np.percentile(arr, 70)),
    )


def extract_track_features(
    audio_path: Path,
    bpm: float,
    beat_times_ms: list[int],
    first_downbeat_offset: int,
    ext_path: Path | None = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> TrackFeatures:
    """Extract per-beat features for one track, with disk cache.

    Args:
      audio_path: WAV/MP3/FLAC file.
      bpm: project BPM (used as fallback when beat_times_ms is short).
      beat_times_ms: per-beat ms timestamps from Rekordbox PQTZ.
      first_downbeat_offset: index of first beat_of_bar=1 in beat_times_ms.
      ext_path: matching Rekordbox .EXT file (for PWV5 waveform). Optional.
      cache_dir: where to store/look up cached feature pickles.

    Returns: TrackFeatures with per-beat data + per-feature percentiles.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(audio_path, ext_path, cache_dir)
    if cache_file.exists():
        try:
            with cache_file.open("rb") as f:
                cached = pickle.load(f)
            if cached.analysis_model_version == ANALYSIS_MODEL_VERSION:
                return cached
        except Exception:
            pass  # corrupt cache, regenerate

    # --- Librosa: per-beat RMS + bass band ---
    y, sr = librosa.load(audio_path, sr=None, mono=True)
    hop = 512
    rms_frames = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    bass_mel = librosa.feature.melspectrogram(
        y=y, sr=sr, hop_length=hop, n_mels=8, fmin=40, fmax=180,
    )
    bass_frames = np.mean(bass_mel, axis=0)

    # --- Rekordbox waveform (optional) ---
    wf_per_beat = None
    waveform_source = "none"
    if ext_path and ext_path.exists():
        entries, src = parse_waveform(ext_path)
        if entries:
            waveform_source = src
            duration_sec = len(y) / sr
            wf_per_beat = waveform_per_beat(entries, beat_times_ms, duration_sec)

    # --- Per-beat aggregation ---
    rms_raw: list[float] = []
    bass_raw: list[float] = []
    for i in range(len(beat_times_ms)):
        start_sec = beat_times_ms[i] / 1000.0
        end_sec = (
            beat_times_ms[i + 1] / 1000.0
            if i + 1 < len(beat_times_ms)
            else start_sec + (60.0 / bpm)
        )
        f0 = max(0, int(start_sec * sr / hop))
        f1 = min(len(rms_frames), int(end_sec * sr / hop))
        if f1 <= f0:
            rms_raw.append(0.0)
            bass_raw.append(0.0)
            continue
        rms_raw.append(float(np.mean(rms_frames[f0:f1])))
        bass_raw.append(float(np.mean(bass_frames[f0:f1])))

    rms_norm = _normalize(rms_raw)
    bass_norm = _normalize(bass_raw)

    # --- Assemble per-beat records ---
    beats: list[BeatFeatures] = []
    for i, beat_ms in enumerate(beat_times_ms):
        wf_h = wf_per_beat["height"][i] if wf_per_beat and i < len(wf_per_beat["height"]) else None
        wf_r = wf_per_beat["r"][i] if wf_per_beat and i < len(wf_per_beat["r"]) else None
        wf_g = wf_per_beat["g"][i] if wf_per_beat and i < len(wf_per_beat["g"]) else None
        wf_b = wf_per_beat["b"][i] if wf_per_beat and i < len(wf_per_beat["b"]) else None
        # beat_index counts from the first downbeat (negative for pre-downbeat audio)
        beats.append(BeatFeatures(
            beat_index=i - first_downbeat_offset,
            sec=beat_ms / 1000.0,
            rms=rms_norm[i] if i < len(rms_norm) else 0.0,
            bass=bass_norm[i] if i < len(bass_norm) else 0.0,
            wf_height=wf_h,
            wf_r=wf_r,
            wf_g=wf_g,
            wf_b=wf_b,
        ))

    stats = {
        "rms": _percentiles(rms_norm),
        "bass": _percentiles(bass_norm),
        "wf_height": _percentiles([b.wf_height for b in beats if b.wf_height is not None]),
    }

    result = TrackFeatures(
        audio_path=str(audio_path),
        bpm=bpm,
        beat_times_ms=beat_times_ms,
        first_downbeat_offset=first_downbeat_offset,
        beats=beats,
        stats=stats,
        waveform_source=waveform_source,
    )

    # Write cache
    try:
        with cache_file.open("wb") as f:
            pickle.dump(result, f)
    except Exception as e:
        print(f"  WARNING: could not write feature cache for {audio_path.name}: {e}")

    return result


def smooth_window(values: list[float], window_beats: int) -> list[float]:
    """Rolling-mean smoothing over `window_beats` beats. Returns same-length list."""
    if not values or window_beats <= 1:
        return list(values)
    arr = np.asarray(values, dtype=float)
    kernel = np.ones(window_beats) / window_beats
    smoothed = np.convolve(arr, kernel, mode="same")
    return smoothed.tolist()
