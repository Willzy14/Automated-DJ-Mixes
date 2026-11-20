"""Estimate a musical key from harmonic CQT chroma above the bass octaves.

Confidence is a step-function calibration fitted on a development set (see
_CAL_EDGES), not a theoretical probability.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from os import PathLike

import librosa
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .sequencer import CAMELOT_WHEEL

DEFAULT_PROFILE = "kk"
PROFILE_NAMES = ("kk", "edm_approx")

# Krumhansl-Kessler, tonic first, in chromatic semitone order.
_KK = (
    (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88),
    (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17),
)

# Hand-selected EDM heuristic. Published Faraldo values were unavailable.
# These weights favor tonic, fifth and modal third, with support for minor bVII.
_EDM_APPROX = (
    (6.0, 0.5, 2.5, 0.75, 4.25, 2.75, 0.5, 4.5, 0.75, 2.5, 1.0, 2.25),
    (6.0, 0.75, 2.5, 4.25, 0.5, 2.75, 0.75, 4.5, 2.75, 1.0, 2.75, 1.5),
)

_C1 = 32.703195662574829  # Hz
_C3 = 130.8127826502993  # Hz; the chroma uses only pitches from here up
_BINS_PER_OCTAVE = 36
_BLOCK_SECONDS = 30
_MIN_SECONDS = 8
_SILENCE = 1e-9

# Calibration of confidence, fitted on the 200-track development half of the
# 400-track evaluation set (random split, seed 7, 2026-11-20; scoring with bass_weight=0). The feature is
# the winning score minus the best score among keys that are NOT Camelot-
# compatible with the winner (same key, +-1 fifth, relative). Each value is the
# observed share of exact matches with the database key in that bin on the
# development tracks, made non-decreasing by pooling adjacent violators.
_CAL_EDGES = (0.05, 0.113, 0.19, 0.283)
_CAL_VALUES = (0.35, 0.38, 0.62, 0.70, 0.70)
# Pitched-ness gate: chroma contrast (std of the mean chroma over its mean).
# The lowest of 200 development tracks measured 0.163; white noise measures 0.07.
_GATE_ZERO = 0.07
_GATE_FULL = 0.15


@dataclass(frozen=True)
class KeyEstimate:
    key: str | None
    camelot: str | None
    confidence: float
    second_best: str | None
    margin: float
    method: str
    tuning_cents: float


def _pitch_class(note: str) -> int:
    natural = (9, 11, 0, 2, 4, 5, 7)[ord(note[0]) - ord("A")]
    return (natural + note.count("#") - note.count("b")) % 12


def to_camelot(key_name: str | None) -> str | None:
    """Accept spelled-out modes or compact names such as Gbm and Db."""
    if not isinstance(key_name, str):
        return None
    match = re.fullmatch(
        r"([A-Ga-g])([#bB]?)[ \t]*(?:(?i:(major|minor))|(m))?",
        key_name.strip(),
        flags=re.ASCII,
    )
    if match is None:
        return None
    letter, accidental, word, suffix = match.groups()
    mode = "minor" if suffix or (word and word.lower() == "minor") else "major"
    pitch = _pitch_class(letter.upper() + accidental.lower())
    for name, code in CAMELOT_WHEEL.items():
        tonic, canonical_mode = name.split()
        if canonical_mode == mode and _pitch_class(tonic) == pitch:
            return code
    return None


def from_camelot(code: str | None) -> str | None:
    """Return the sequencer's canonical key name for a Camelot code."""
    if not isinstance(code, str):
        return None
    code = code.strip().upper()
    return next((name for name, value in CAMELOT_WHEEL.items() if value == code), None)


def _blocks(
    wav_path: str | PathLike[str], start_frac: float, end_frac: float, sr: int
) -> Iterator[np.ndarray]:
    """Yield consecutive mono blocks of the chosen span, resampled to sr."""
    with sf.SoundFile(wav_path) as source:
        first = int(source.frames * start_frac)
        span = math.ceil(source.frames * end_frac) - first
        if span <= 0:
            return
        native_sr = source.samplerate
        count = max(1, -(-span // (_BLOCK_SECONDS * native_sr)))
        length = -(-span // count)
        divisor = math.gcd(sr, native_sr)
        source.seek(first)
        remaining = span
        while remaining > 0:
            pcm = source.read(min(length, remaining), dtype="float32", always_2d=True)
            if not len(pcm):
                break
            remaining -= len(pcm)
            if not np.isfinite(pcm).all():
                raise ValueError("Audio contains non-finite samples")
            mono = pcm.mean(axis=1)
            del pcm
            if native_sr != sr:
                mono = resample_poly(mono, sr // divisor, native_sr // divisor)
            yield mono.astype(np.float32, copy=False)


def _unit_interval(value: float) -> float:
    return min(1.0, max(0.0, value))


def _harmonic(blocks: Iterator[np.ndarray]) -> np.ndarray:
    parts = []
    for block in blocks:
        if len(block) < 4096:
            parts.append(np.zeros_like(block))
        else:
            parts.append(librosa.effects.hpss(block)[0])
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def _compatible(code: str) -> set[str]:
    number, letter = int(code[:-1]), code[-1]
    other = "A" if letter == "B" else "B"
    return {
        code,
        f"{number % 12 + 1}{letter}",
        f"{(number - 2) % 12 + 1}{letter}",
        f"{number}{other}",
    }


def _fold(rows: np.ndarray) -> np.ndarray:
    """Sum octaves, then fold 36 bins to 12 pitch classes (1/2, 1, 1/2 taps)."""
    octave = rows.reshape(-1, _BINS_PER_OCTAVE, rows.shape[1]).sum(axis=0)
    out = np.zeros((12, rows.shape[1]))
    for pitch in range(12):
        for offset, tap in ((-1, 0.5), (0, 1.0), (1, 0.5)):
            out[pitch] += tap * octave[(3 * pitch + offset) % _BINS_PER_OCTAVE]
    return out


def _analyse(
    wav_path: str | PathLike[str], start_frac: float, end_frac: float, sr: int, bass: bool
) -> tuple[np.ndarray, np.ndarray, float] | None:
    """Return (chroma above C3, bass chroma below C3, tuning in semitones)."""
    harmonic = _harmonic(_blocks(wav_path, start_frac, end_frac, sr))
    if not len(harmonic) or not np.abs(harmonic).max() > 0:
        return None
    minimum = _MIN_SECONDS * sr
    if len(harmonic) < minimum:
        harmonic = np.pad(harmonic, (0, minimum - len(harmonic)))

    # Tuning is estimated in semitone fractions (range -0.5..0.5) from the
    # harmonic signal, then converted to bins of the 36-per-octave CQT.
    tuning = float(librosa.estimate_tuning(y=harmonic, sr=sr, bins_per_octave=12, n_fft=4096))
    mid_octaves = min(5, int(math.floor(math.log2(0.45 * sr / _C3))))
    low_octaves = 2 if bass else 0
    magnitude = np.abs(
        librosa.cqt(
            harmonic,
            sr=sr,
            hop_length=64 * max(1, round(sr / 22050 * 32)),
            fmin=_C1 if bass else _C3,
            n_bins=_BINS_PER_OCTAVE * (low_octaves + mid_octaves),
            bins_per_octave=_BINS_PER_OCTAVE,
            tuning=tuning * _BINS_PER_OCTAVE / 12,
        )
    ).astype(np.float32)
    del harmonic

    mid = _fold(magnitude[_BINS_PER_OCTAVE * low_octaves :])
    total = mid.sum(axis=0)
    valid = total > _SILENCE
    if not valid.any():
        return None
    chroma = (mid[:, valid] / total[valid]).mean(axis=1)
    low = np.zeros(12)
    if bass:
        rows = _fold(magnitude[: _BINS_PER_OCTAVE * low_octaves])[:, valid]
        low_total = rows.sum(axis=0)
        present = low_total > _SILENCE
        if present.any():
            low = (rows[:, present] / low_total[present]).mean(axis=1)
            low /= low.sum()
    return chroma, low, tuning


def _calibrated(feature: float) -> float:
    return _CAL_VALUES[bisect_right(_CAL_EDGES, feature)]


def _rank(
    chroma: np.ndarray, low: np.ndarray, profile: str, bass_weight: float
) -> list[tuple[float, str, str]] | None:
    centered = chroma - chroma.mean()
    norm = float(np.linalg.norm(centered))
    if norm < 1e-12:
        return None
    centered /= norm
    profiles = np.array(_KK if profile == "kk" else _EDM_APPROX)
    profiles -= profiles.mean(axis=1, keepdims=True)
    profiles /= np.linalg.norm(profiles, axis=1, keepdims=True)
    scored = []
    for key, code in CAMELOT_WHEEL.items():
        note, mode = key.split()
        tonic = _pitch_class(note)
        score = float(np.dot(centered, np.roll(profiles[int(mode == "minor")], tonic)))
        score += bass_weight * float(low[tonic])
        scored.append((score, key, code))
    scored.sort(key=lambda row: row[0], reverse=True)
    return scored


def _confidence_feature(scored: list[tuple[float, str, str]]) -> float:
    neighbours = _compatible(scored[0][2])
    return scored[0][0] - max(score for score, _, code in scored if code not in neighbours)


def estimate_key(
    wav_path: str | PathLike[str],
    *,
    start_frac: float = 0.15,
    end_frac: float = 0.85,
    sr: int = 22050,
    profile: str = DEFAULT_PROFILE,
    bass_weight: float = 0.0,
) -> KeyEstimate:
    """Rank all 24 keys by Pearson correlation of harmonic chroma with a profile.

    The chroma is a constant-Q chroma of the harmonic part of the audio,
    folded only from C3 (131 Hz) upward: bass notes and kick sub-bass smeared
    the full-range chroma and cost about 11 points of exact accuracy on the
    development set. An optional bass-register tonic bonus (bass_weight times
    the share of C1-C3 chroma on the tonic) is off by default: on the development
    set it moved exact accuracy by less than one point (+2 and -1 tracks at 0.1)
    and cost an extra two octaves of CQT.

    Confidence is the development-set exact-match rate for the bin of
    (winning score minus the best score of any key not Camelot-compatible
    with the winner), times a pitched-ness gate that is 1 for music and 0 for
    flat noise. It is a calibrated ranking signal on that data, not a
    guarantee for other material.
    """
    if profile not in PROFILE_NAMES:
        raise ValueError("profile must be kk or edm_approx")
    if not 0 <= start_frac < end_frac <= 1:
        raise ValueError("Require 0 <= start_frac < end_frac <= 1")
    if not isinstance(sr, int) or sr < 8000:
        raise ValueError("sr must be an integer of at least 8000 Hz")
    if not 0 <= bass_weight <= 1:
        raise ValueError("bass_weight must be between 0 and 1")

    method = f"{profile}+harmonic_cqt_chroma_c3up"
    if bass_weight:
        method += f"+bass{bass_weight:g}"
    analysis = _analyse(wav_path, start_frac, end_frac, sr, bool(bass_weight))
    if analysis is None:
        return KeyEstimate(None, None, 0.0, None, 0.0, method, 0.0)
    chroma, low, tuning = analysis
    scored = _rank(chroma, low, profile, bass_weight)
    if scored is None:
        return KeyEstimate(None, None, 0.0, None, 0.0, method, tuning * 100)
    contrast = float(chroma.std() / chroma.mean())
    gate = _unit_interval((contrast - _GATE_ZERO) / (_GATE_FULL - _GATE_ZERO))
    confidence = _calibrated(_confidence_feature(scored)) * gate
    return KeyEstimate(
        scored[0][1], scored[0][2], confidence, scored[1][1],
        scored[0][0] - scored[1][0], method, tuning * 100,
    )
