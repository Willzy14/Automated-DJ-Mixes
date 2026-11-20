"""Estimate a musical key and report an uncalibrated confidence score."""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from os import PathLike

import numpy as np
import soundfile as sf
from scipy.fft import next_fast_len, rfft
from scipy.ndimage import median_filter
from scipy.signal import resample_poly
from scipy.signal.windows import hann

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


def _excerpts(
    wav_path: str | PathLike[str], start_frac: float, end_frac: float, sr: int
) -> Iterator[np.ndarray]:
    with sf.SoundFile(wav_path) as source:
        first = int(source.frames * start_frac)
        span = math.ceil(source.frames * end_frac) - first
        if span <= 0:
            return
        native_sr = source.samplerate
        max_frames = 12 * native_sr
        count = min(7, (span + max_frames - 1) // max_frames)
        length = min(max_frames, span // count)
        divisor = math.gcd(sr, native_sr)

        for index in range(count):
            offset = index * (span - length) // max(1, count - 1)
            source.seek(first + offset)
            pcm = source.read(length, dtype="float32", always_2d=True)
            if not np.isfinite(pcm).all():
                raise ValueError("Audio contains non-finite samples")
            mono = pcm.mean(axis=1)
            del pcm
            if native_sr != sr:
                mono = resample_poly(mono, sr // divisor, native_sr // divisor)
            yield mono


def _unit_interval(value: float) -> float:
    return min(1.0, max(0.0, value))


def _observations(
    samples: np.ndarray, sr: int
) -> Iterator[tuple[np.ndarray, np.ndarray, float] | None]:
    """Yield floating MIDI pitches, normalized amplitudes and frame quality."""
    width = round(sr * 8192 / 22050)
    hop = width // 2
    fft_size = next_fast_len(2 * width, real=True)
    if len(samples) < width:
        missing = width - len(samples)
        samples = np.pad(samples, (missing // 2, missing - missing // 2))
    frames = np.lib.stride_tricks.sliding_window_view(samples, width)[::hop]
    window = hann(width, sym=False).astype(np.float32)
    lower = math.ceil(36 * fft_size / sr)
    upper = math.floor(3500 * fft_size / sr) + 1
    spectrum = rfft(frames * window, n=fft_size, axis=-1)
    power = np.abs(spectrum[:, : upper + 6]) ** 2
    del spectrum
    power = median_filter(power, size=(3, 1), mode="nearest")

    for row in power:
        total = float(row[lower:upper].sum(dtype=np.float64))
        if total <= 1e-12 * width * fft_size:
            yield None
            continue
        peaks = lower + np.flatnonzero(
            (row[lower:upper] > row[lower - 1 : upper - 1])
            & (row[lower:upper] >= row[lower + 1 : upper + 1])
        )
        flanks = (row[peaks - 6] + row[peaks + 6]) * 0.5
        peaks = peaks[(row[peaks] >= 0.003 * total) & (row[peaks] >= 4 * flanks)]
        if not peaks.size:
            yield None
            continue

        quality = _unit_interval((float(row[peaks].max()) / total - 0.015) / 0.08)
        if quality == 0:
            yield None
            continue

        # Refine peaks between FFT bins by fitting a parabola to log power.
        neighbors = row[peaks[:, None] + (-1, 0, 1)].astype(np.float64)
        left, center, right = np.log(np.maximum(neighbors, 1e-30)).T
        curvature = left - 2 * center + right
        offset = np.divide(
            0.5 * (left - right),
            curvature,
            out=np.zeros_like(center),
            where=curvature < 0,
        )
        hz = (peaks + offset) * sr / fft_size
        midi = 69 + 12 * np.log2(hz / 440)
        weights = np.sqrt(neighbors[:, 1])
        yield midi, weights / weights.sum(), quality


def estimate_key(
    wav_path: str | PathLike[str],
    *,
    start_frac: float = 0.15,
    end_frac: float = 0.85,
    sr: int = 22050,
    profile: str = DEFAULT_PROFILE,
    bass_weight: float = 0.30,
) -> KeyEstimate:
    """Rank all 24 keys using Pearson profile correlation plus bass support.

    Set bass_weight=0 to disable the separate bass tonic bonus.

    Confidence is the product of four factors in [0, 1]. They are the
    clipped margin / 0.20, clipped (1 - chroma entropy / log(12)) / 0.28,
    mean spectral frame quality, and clipped (winning third's chroma
    mass minus competing third's mass) / 0.08. The third factor includes
    rejected frames as zero. Confidence is a ranking signal, not a probability.
    """
    if profile not in PROFILE_NAMES:
        raise ValueError("profile must be kk or edm_approx")
    if not 0 <= start_frac < end_frac <= 1:
        raise ValueError("Require 0 <= start_frac < end_frac <= 1")
    if not isinstance(sr, int) or sr < 8000:
        raise ValueError("sr must be an integer of at least 8000 Hz")

    if not 0 <= bass_weight <= 1:
        raise ValueError("bass_weight must be between 0 and 1")

    method = f"{profile}+median_fft_chroma"
    if bass_weight:
        method += f"+bass{bass_weight:g}"
    observations = []
    frame_count = 0
    quality_sum = 0.0
    phasor = 0j
    for excerpt in _excerpts(wav_path, start_frac, end_frac, sr):
        for observation in _observations(excerpt, sr):
            frame_count += 1
            if observation is None:
                continue
            midi, weights, quality = observation
            observations.append(observation)
            quality_sum += quality
            phasor += quality * np.dot(weights, np.exp(2j * np.pi * midi))
    if not observations:
        return KeyEstimate(None, None, 0.0, None, 0.0, method, 0.0)

    # The circular mean estimates a common tuning shift in [-0.5, 0.5]
    # semitones. Ignore it if the accepted peaks do not agree sufficiently.
    tuning = 0.0
    if abs(phasor) >= 0.4 * quality_sum:
        tuning = float(np.angle(phasor) / (2 * np.pi))
    chroma = np.zeros(12)
    bass = np.zeros(12)
    for midi, weights, quality in observations:
        pitches = np.rint(midi - tuning).astype(int) % 12
        chroma += quality * np.bincount(pitches, weights=weights, minlength=12)
        if bass_weight:
            low = midi < 54  # About 185 Hz.
            low_weights = weights[low]
            mass = float(low_weights.sum())
            if mass:
                distribution = np.bincount(pitches[low], weights=low_weights, minlength=12)
                bass += quality * min(1.0, 3 * mass) * distribution / mass
    chroma /= quality_sum
    bass /= quality_sum

    centered = chroma - chroma.mean()
    norm = np.linalg.norm(centered)
    if norm < 1e-12:
        return KeyEstimate(None, None, 0.0, None, 0.0, method, tuning * 100)
    centered /= norm
    profiles = np.array(_KK if profile == "kk" else _EDM_APPROX)
    profiles -= profiles.mean(axis=1, keepdims=True)
    profiles /= np.linalg.norm(profiles, axis=1, keepdims=True)

    scores = []
    for key, code in CAMELOT_WHEEL.items():
        note, mode = key.split()
        tonic = _pitch_class(note)
        minor = mode == "minor"
        score = np.dot(centered, np.roll(profiles[int(minor)], tonic))
        score += bass_weight * bass[tonic]
        scores.append((float(score), key, code, tonic, 3 if minor else 4))
    scores.sort(key=lambda row: row[0], reverse=True)

    best_score, key, code, tonic, third = scores[0]
    second_score, second_best = scores[1][:2]
    margin = best_score - second_score
    positive = chroma[chroma > 0]
    entropy = -float(np.dot(positive, np.log(positive)))
    other_third = 4 if third == 3 else 3
    third_advantage = float(chroma[(tonic + third) % 12] - chroma[(tonic + other_third) % 12])
    confidence = (
        _unit_interval(margin / 0.20)
        * _unit_interval((1 - entropy / math.log(12)) / 0.28)
        * (quality_sum / frame_count)
        * _unit_interval(third_advantage / 0.08)
    )
    return KeyEstimate(key, code, confidence, second_best, margin, method, tuning * 100)
