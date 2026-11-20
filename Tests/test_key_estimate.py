"""Synthetic key-estimation and Camelot tests."""

import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "Source")]

from automated_dj_mixes.key_estimate import (
    PROFILE_NAMES,
    estimate_key,
    from_camelot,
    to_camelot,
)
from automated_dj_mixes.sequencer import CAMELOT_WHEEL
from Tools.eval_key_estimate import classify_relation

SR = 22050
LOW_CONFIDENCE = 0.20


def _triad(
    tonic: int,
    minor: bool,
    *,
    seconds: float = 3.0,
    cents: float = 0.0,
    bass: bool = True,
) -> np.ndarray:
    time = np.arange(round(SR * seconds)) / SR
    notes = [(tonic, 0.18), (tonic + (3 if minor else 4), 0.18), (tonic + 7, 0.18)]
    if bass:
        notes.append((tonic - 24, 0.24))
    signal = np.zeros_like(time)
    for midi, gain in notes:
        hz = 440 * 2 ** ((midi - 69 + cents / 100) / 12)
        signal += gain * np.sin(2 * np.pi * hz * time)
    fade = np.minimum(1.0, np.minimum(time, time[-1] - time) / 0.04)
    return signal * fade


def _wav(tmp_path: Path, samples: np.ndarray) -> Path:
    path = tmp_path / "test.wav"
    sf.write(path, samples, SR, subtype="FLOAT")
    return path


@pytest.mark.parametrize("profile", PROFILE_NAMES)
@pytest.mark.parametrize(
    ("tonic", "minor", "expected"),
    [
        (60, False, "C major"),
        (63, False, "Eb major"),
        (66, False, "F# major"),
        (62, True, "D minor"),
        (68, True, "G# minor"),
        (70, True, "Bb minor"),
    ],
)
def test_sustained_triads(tmp_path, profile, tonic, minor, expected):
    result = estimate_key(_wav(tmp_path, _triad(tonic, minor)), profile=profile)
    assert result.key == expected
    assert result.camelot == to_camelot(expected)
    assert result.second_best is not None
    assert result.second_best != result.key
    assert result.margin > 0
    assert LOW_CONFIDENCE < result.confidence <= 1


@pytest.mark.parametrize("profile", PROFILE_NAMES)
def test_equal_length_i_iv_v_progression(tmp_path, profile):
    progression = np.concatenate(
        [_triad(root, False, seconds=1.5, bass=False) for root in (60, 65, 67)]
    )
    path = _wav(tmp_path, np.tile(progression, 2))
    # Include whole cycles so cropping does not give one chord more time.
    result = estimate_key(path, start_frac=0, end_frac=1, profile=profile, bass_weight=0)
    assert result.key == "C major"


@pytest.mark.parametrize("kind", ["noise", "clicks", "silence"])
def test_unpitched_audio_has_low_confidence(tmp_path, kind):
    samples = np.zeros(SR * 4)
    if kind == "noise":
        samples = np.random.default_rng(19).normal(0.0, 0.10, len(samples))
    elif kind == "clicks":
        click = np.array([0.9, -0.7, 0.4, -0.2, 0.1])
        for start in range(0, len(samples) - len(click), SR // 4):
            samples[start : start + len(click)] = click
    result = estimate_key(_wav(tmp_path, samples))
    assert 0 <= result.confidence < LOW_CONFIDENCE


@pytest.mark.parametrize("profile", PROFILE_NAMES)
def test_detuned_triad(tmp_path, profile):
    result = estimate_key(_wav(tmp_path, _triad(66, True, cents=30)), profile=profile)
    assert result.key == "F# minor"
    assert result.tuning_cents == pytest.approx(30.0, abs=5.0)


@pytest.mark.parametrize(("key", "code"), tuple(CAMELOT_WHEEL.items()))
def test_camelot_round_trips(key, code):
    tonic, mode = key.split()
    compact = tonic + ("m" if mode == "minor" else "")
    assert from_camelot(code) == key
    assert to_camelot(key) == code
    assert to_camelot(f"{tonic} {mode.title()}") == code
    assert to_camelot(compact) == code


@pytest.mark.parametrize(
    ("spelling", "code"),
    [("G Major", "9B"), ("Bb Minor", "3A"), ("A# minor", "3A"),
     ("F#m", "11A"), ("Gbm", "11A"), ("Db", "3B")],
)
def test_camelot_aliases(spelling, code):
    assert to_camelot(spelling) == code


@pytest.mark.parametrize("junk", ["", "G nonsense", "H minor", "C##", "A#maj7", None])
def test_rejects_invalid_keys(junk):
    assert to_camelot(junk) is None


@pytest.mark.parametrize("junk", ["", "0A", "13B", "08A", "C major", None])
def test_rejects_invalid_camelot_codes(junk):
    assert from_camelot(junk) is None


@pytest.mark.parametrize(
    ("reference", "estimate", "relation"),
    [
        ("C major", "G major", "fifth"),
        ("G major", "C major", "fifth"),
        ("C major", "A minor", "relative"),
        ("C major", "C minor", "parallel"),
        ("A flat", "G# minor", "other"),
        ("Ab Major", "G#m", "parallel"),
        ("C major", "F# major", "other"),
        ("Bb Minor", "A#m", "exact"),
        ("C major", None, "other"),
        ("B major", "E major", "fifth"),
    ],
)
def test_key_relations(reference, estimate, relation):
    assert classify_relation(reference, estimate) == relation


def test_kk_profiles_are_the_published_values():
    from automated_dj_mixes.key_estimate import _KK

    assert _KK[0] == (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
    assert _KK[1] == (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


def test_confidence_calibration_constants_are_valid():
    from automated_dj_mixes import key_estimate as ke

    assert list(ke._CAL_EDGES) == sorted(ke._CAL_EDGES)
    assert len(ke._CAL_VALUES) == len(ke._CAL_EDGES) + 1
    assert all(0 <= v <= 1 for v in ke._CAL_VALUES)
    assert list(ke._CAL_VALUES) == sorted(ke._CAL_VALUES)
    probes = [-1.0, 0.0, *ke._CAL_EDGES, 0.4, 1.0]
    values = [ke._calibrated(p) for p in probes]
    assert values == sorted(values)
    assert ke._calibrated(-1.0) == ke._CAL_VALUES[0]
    assert ke._calibrated(1.0) == ke._CAL_VALUES[-1]
    assert 0 < ke._GATE_ZERO < ke._GATE_FULL


@pytest.mark.parametrize(
    ("code", "expected"),
    [("8A", {"8A", "9A", "7A", "8B"}), ("12B", {"12B", "1B", "11B", "12A"}),
     ("1A", {"1A", "2A", "12A", "1B"})],
)
def test_compatible_set(code, expected):
    from automated_dj_mixes.key_estimate import _compatible

    assert _compatible(code) == expected


def test_loud_bass_pedal_below_c3_does_not_decide_the_key(tmp_path):
    time = np.arange(SR * 6) / SR
    triad = _triad(60, False, seconds=6.0, bass=False)  # C major, all above C3
    pedal = 0.9 * np.sin(2 * np.pi * 440 * 2 ** ((30 - 69) / 12) * time)  # F#1
    result = estimate_key(_wav(tmp_path, triad + pedal))
    assert result.key == "C major"


def test_bass_bonus_is_optional_and_off_by_default(tmp_path):
    path = _wav(tmp_path, _triad(62, True))
    off = estimate_key(path)
    on = estimate_key(path, bass_weight=0.1)
    assert off.method != on.method and "bass" in on.method and "bass" not in off.method
    assert on.key == off.key == "D minor"


def test_native_44100_and_multiple_blocks(tmp_path):
    seconds = 75  # more than two 30 s blocks after the default 15/85 crop
    samples = _triad(69, True, seconds=seconds)
    path = tmp_path / "long.wav"
    sf.write(path, np.interp(np.arange(0, len(samples) - 1, 0.5), np.arange(len(samples)), samples), 44100,
             subtype="FLOAT")
    result = estimate_key(path)
    assert result.key == "A minor"
    assert estimate_key(path) == result  # deterministic


def test_rejects_non_finite_audio(tmp_path):
    samples = _triad(60, False)
    samples[len(samples) // 2] = np.nan
    with pytest.raises(ValueError):
        estimate_key(_wav(tmp_path, samples))
