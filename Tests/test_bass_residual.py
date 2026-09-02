"""Tests for Sam's option-2 bass residual, and for the shelf defect it exposed.

Fixtures are synthetic - short WAVs with a known low-frequency content and
hand-built Clip/Track objects - so nothing here needs the audio corpus.

The first two tests are the important ones. They pin the reason the residual is
sizeable at all: `mix_predict` was applying the ChannelEq low shelf through
`sosfiltfilt`, which filters forward AND backward and so DOUBLES the shelf's
effect in dB. That was invisible while the pipeline only ever wrote a full kill
or unity, and it goes live the moment an intermediate shelf value is written -
which is exactly what this feature does.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import apply_automation as AA  # noqa: E402
import bass_residual as BR  # noqa: E402
import mix_predict as MP  # noqa: E402

SR = 44100


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

def _write_tone(path: Path, low_amp: float, bass_amp: float | None = None,
                seconds: float = 12.0) -> Path:
    """40 Hz (sub band) + 100 Hz (bass band) + a fixed 1 kHz reference.

    sub and bass are independently settable so a fixture can make the two bands
    DISAGREE, which is the case the overshoot guard exists for and which a
    single-tone fixture can never produce.
    """
    t = np.arange(int(seconds * SR)) / SR
    bass_amp = low_amp if bass_amp is None else bass_amp
    y = (low_amp * np.sin(2 * np.pi * 40.0 * t)
         + bass_amp * np.sin(2 * np.pi * 100.0 * t)
         + 0.1 * np.sin(2 * np.pi * 1000.0 * t))
    sf.write(str(path), np.stack([y, y], axis=1), SR)
    return path


def _track(name: str, source: Path, beats: float = 400.0):
    clip = MP.Clip(arr_start=0.0, arr_end=beats, loop_start=0.0,
                   start_relative=0.0,
                   warp_beats=np.array([0.0, beats]),
                   warp_secs=np.array([0.0, 10.0]),
                   source=source)
    return MP.Track(name=name, mixer_trim=1.0, clips=[clip])


class _Model:
    master = 1.0

    def __init__(self, tracks):
        self.tracks = tracks


# --------------------------------------------------------------------------- #
# the shelf defect
# --------------------------------------------------------------------------- #

def test_shelf_is_applied_once_not_twice(tmp_path):
    """A 0.52 shelf must attenuate sub by roughly its designed amount.

    Applied twice it lands near double, which is what shipped. The assertion is
    deliberately against the DESIGNED response rather than a recorded constant,
    so it fails if the filter is ever double-applied again for any reason.
    """
    src = _write_tone(tmp_path / "t.wav", 0.5)
    clip = _track("t", src).clips[0]
    cache: dict = {}
    unity = MP._source_band_power(clip, 5.0, 3.0, 1.0, cache)
    shelved = MP._source_band_power(clip, 5.0, 3.0, 0.52, cache)
    applied = 10 * math.log10(shelved["sub"] / unity["sub"])

    sos = MP._low_shelf_sos(0.52, float(SR))
    from scipy.signal import sosfreqz
    _, h = sosfreqz(sos, worN=[40.0], fs=SR)
    designed = 20 * math.log10(abs(h[0]))

    assert applied == pytest.approx(designed, abs=0.6), (
        f"shelf applied {applied:.2f} dB, designed {designed:.2f} dB - a factor "
        f"of ~2 here means it is being filtered forward and backward again")


def test_unity_shelf_prediction_is_bit_identical(tmp_path):
    """With the shelf at unity nothing is filtered, so the measured window is
    exactly the window that was requested.

    This is the property that keeps mix_predict's published band calibration
    valid across the one-pass shelf fix: every calibration probe was taken with
    the shelf at unity or with the track 20+ dB down, so none of them can move.
    """
    src = _write_tone(tmp_path / "t.wav", 0.5)
    clip = _track("t", src).clips[0]
    with_lead = MP._source_band_power(clip, 5.0, 3.0, 1.0, {})
    # Same window read directly, no lead-in, no filtering.
    a = int((5.0 - 1.5) * SR)
    b = int((5.0 + 1.5) * SR)
    with sf.SoundFile(str(src)) as fh:
        fh.seek(a)
        y = fh.read(b - a, dtype="float64", always_2d=True)
    from scipy.signal import butter, sosfiltfilt
    bs = butter(4, [20.0, 60.0], btype="band", fs=SR, output="sos")
    direct = sum(float(np.mean(sosfiltfilt(bs, y[:, c]) ** 2))
                 for c in range(y.shape[1])) / y.shape[1]
    assert with_lead["sub"] == pytest.approx(direct, rel=1e-12)


# --------------------------------------------------------------------------- #
# sizing
# --------------------------------------------------------------------------- #

def test_matched_tracks_get_no_residual(tmp_path):
    """Two records with the same low end have no hole to fill."""
    a = _track("out", _write_tone(tmp_path / "a.wav", 0.5))
    b = _track("in", _write_tone(tmp_path / "b.wav", 0.5))
    d = BR.size_residual(_Model([a, b]), a, b, 200.0, 320.0)
    assert d.fired is False
    assert "no hole worth sizing" in d.reason


def test_weak_incoming_fires_and_says_why(tmp_path):
    """A much quieter incoming low end is the case this exists for."""
    a = _track("out", _write_tone(tmp_path / "a.wav", 0.5))
    b = _track("in", _write_tone(tmp_path / "b.wav", 0.10))
    d = BR.size_residual(_Model([a, b]), a, b, 200.0, 320.0)
    assert d.fired is True
    assert BR.RESIDUAL_FLOOR < d.gain <= BR.RESIDUAL_CEILING
    assert d.band == "sub"
    assert d.recovered_db > 0
    assert "recover" in d.reason   # "recovers N dB" or "recovering N dB of it"


def test_every_decision_carries_a_reason(tmp_path):
    """Refusals must be legible in a build log, not silent."""
    a = _track("out", _write_tone(tmp_path / "a.wav", 0.5))
    b = _track("in", _write_tone(tmp_path / "b.wav", 0.5))
    for swap in (200.0, 1e9):          # in range, and past the end of the clip
        d = BR.size_residual(_Model([a, b]), a, b, swap, swap + 120.0)
        assert d.reason, "a decision with no reason is not reviewable"


def test_residual_never_exceeds_the_heard_ceiling(tmp_path):
    """Even a huge hole may not push past the ear-set cap: above it the outgoing's
    bass line reads as a note against the incoming's, and two audible basslines
    is the mud this avoids."""
    a = _track("out", _write_tone(tmp_path / "a.wav", 0.9))
    b = _track("in", _write_tone(tmp_path / "b.wav", 0.02))
    d = BR.size_residual(_Model([a, b]), a, b, 200.0, 320.0)
    if d.fired:
        assert d.gain <= BR.RESIDUAL_CEILING


def test_cap_pinned_firing_survives_a_small_predicted_recovery(tmp_path):
    """The live regression Sam's -9.5 dB cap exposed, pinned.

    A huge, real hole whose recovery AT THE CAP is small must still fire: the
    cap-pinned write is justified by Sam's listening verdict (the Apetite ->
    DHB proof, where predicted recovery was 2.3 dB and his ears ruled the
    result right), not by the recovery arithmetic. Requiring the recovery to
    beat the model's error bound here would refuse every cap-pinned firing
    forever, because a -9.5 dB cap bounds the measurable recovery near 2-3 dB
    by construction.
    """
    a = _track("out", _write_tone(tmp_path / "a.wav", 0.9))
    b = _track("in", _write_tone(tmp_path / "b.wav", 0.02))
    d = BR.size_residual(_Model([a, b]), a, b, 200.0, 320.0)
    assert d.fired is True
    assert d.gain == pytest.approx(BR.RESIDUAL_CEILING)
    assert d.shortfall_db > 8.0, "fixture must present a big, real hole"
    assert "ear-set cap" in d.reason
    # And the honest part: the reported recovery may sit under the sizing
    # bound - the point is that it is REPORTED, not that it is large.
    assert d.recovered_db > 0


def test_the_ceiling_is_sams_ear_ruling():
    """The cap is a LISTENING result, not a tunable: Sam heard -5.7 dB on the
    Apetite -> DHB proof, called it "too much bass in, feels clashy", hand-set
    -9.5 dB and ruled that right ("the weight, but not the sound",
    2026-09-01). Changing this constant means a NEW listening verdict exists -
    update the ruling in the module docstring in the same commit, or revert.
    """
    assert BR.RESIDUAL_CEILING == pytest.approx(0.335, abs=1e-3)
    assert 20 * math.log10(BR.RESIDUAL_CEILING) == pytest.approx(-9.5, abs=0.1)


def test_levelling_trims_change_the_sizing(tmp_path):
    """The offsets the mix ships with are an input, not a detail. Dropping the
    outgoing by 6 dB must not leave the residual unchanged (Codex FATAL 1)."""
    a = _track("out", _write_tone(tmp_path / "a.wav", 0.5))
    b = _track("in", _write_tone(tmp_path / "b.wav", 0.10))
    flat = BR.size_residual(_Model([a, b]), a, b, 200.0, 320.0)
    # -12 dB, not -6: the cap-pinned branch means a merely-shrunk hole still
    # fires at the cap, so the trim must be deep enough to take the hole under
    # the shortfall gate and flip the DECISION, which is the property Codex's
    # finding was about. Measured on this fixture: -9 leaves a 4.5 dB hole
    # (still fires); -12 turns the shortfall negative (refuses).
    trimmed = BR.size_residual(_Model([a, b]), a, b, 200.0, 320.0,
                               out_trim_db=-12.0)
    assert flat.fired is True
    assert trimmed.fired is False, trimmed.reason


def test_a_sub_hole_is_refused_when_bass_is_already_hot(tmp_path):
    """The case one 100 Hz shelf cannot serve.

    The incoming is weak in sub but STRONGER in bass, so the swap already
    over-delivers bass. Lifting the outgoing's shelf to fill the sub hole must
    also lift bass, and the guard has to refuse rather than trade one defect
    for another. This is the pair-13 shape from the V16 mix.
    """
    a = _track("out", _write_tone(tmp_path / "a.wav", low_amp=0.6, bass_amp=0.05))
    b = _track("in", _write_tone(tmp_path / "b.wav", low_amp=0.05, bass_amp=0.6))
    d = BR.size_residual(_Model([a, b]), a, b, 200.0, 320.0)
    assert d.fired is False
    assert "above its pre-swap level" in d.reason, d.reason


def test_worth_sizing_is_stricter_than_the_summed_gate():
    """The share-difference gate must be WIDER than mix_predict's own, because
    the p95 it borrows was measured on summed predictions."""
    band = "sub"
    edge = MP.SIZING_MARGIN * MP.BAND_P95_DB[band] * 1.01
    assert MP.can_size_correction(band, edge) is True
    assert BR._worth_sizing(band, edge) is False


# --------------------------------------------------------------------------- #
# how it reaches the ALS
# --------------------------------------------------------------------------- #

def _plan(style=AA.TransitionStyle.STANDARD, residual=None):
    out = AA.TrackInfo(name="out", sections=[], arr_start=0.0, arr_end=800.0)
    inc = AA.TrackInfo(name="in", sections=[], arr_start=400.0, arr_end=1200.0)
    return AA.TransitionPlan(outgoing=out, incoming=inc, overlap_start=400.0,
                             overlap_end=560.0, bass_swap=480.0, style=style,
                             bass_residual=residual), [out, inc]


def test_flag_is_off_by_default():
    assert AA.BASS_RESIDUAL_ENABLED is False


def test_no_residual_writes_todays_full_kill():
    plan, tracks = _plan(residual=None)
    pts = dict(AA.build_track_automation([plan], tracks)["out"]["eq_bass"])
    assert pts[480.0] == AA.EQ_BASS_KILL
    assert pts[560.0] == AA.EQ_BASS_KILL


def test_residual_is_held_at_the_swap_then_tapers_to_the_kill():
    plan, tracks = _plan(residual=0.4)
    pts = dict(AA.build_track_automation([plan], tracks)["out"]["eq_bass"])
    assert pts[480.0] == 0.4, "the swap must hold the residual, not the kill"
    assert pts[560.0] == AA.EQ_BASS_KILL, "must still reach a full kill by ov_e"


def test_quick_swap_refuses_a_residual():
    """Quick swap zeroes the outgoing's Utility Gain at the swap, so a residual
    would be written into silence and heard by nobody (Codex MAJOR 2)."""
    plan, tracks = _plan(style=AA.TransitionStyle.QUICK_SWAP, residual=0.4)
    auto = AA.build_track_automation([plan], tracks)
    pts = dict(auto["out"]["eq_bass"])
    assert pts[480.0] == AA.EQ_BASS_KILL
    assert dict(auto["out"]["volume"])[480.0] == AA.VOL_ZERO
