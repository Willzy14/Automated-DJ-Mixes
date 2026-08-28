"""Tests for Source/mix_predict.py: predicting a bounce without rendering it.

Fixtures are built in code - a minimal gzipped ALS the loader accepts plus
synthetic source audio - so the suite runs without the audio corpus. The
corpus-backed accuracy pin skips cleanly when the real render is absent.
"""
from __future__ import annotations

import gzip
import math
import sys
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import mix_predict as MP  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #

def _als(tmp_path, *, bpm=120.0, master=1.0, trim=1.0, gain_pts=None,
         shelf_pts=None, filters=(("0", 19999.99), ("1", 20.0)),
         master_devices=(), resonance="0", automate_filter=False,
         src_name="src.wav", seconds=40.0):
    root = Element("Ableton")
    live = SubElement(root, "LiveSet")
    SubElement(SubElement(live, "Tempo"), "Manual").set("Value", str(bpm))

    track = SubElement(live, "AudioTrack")
    SubElement(track, "EffectiveName").set("Value", "T1")
    mixer = SubElement(track, "Mixer")
    SubElement(SubElement(mixer, "Volume"), "Manual").set("Value", str(trim))

    gain_dev = SubElement(track, "StereoGain")
    g = SubElement(gain_dev, "Gain")
    SubElement(g, "AutomationTarget").set("Id", "100")
    eq = SubElement(track, "ChannelEq")
    lsg = SubElement(eq, "LowShelfGain")
    SubElement(lsg, "AutomationTarget").set("Id", "200")

    for i, (ftype, fhz) in enumerate(filters):
        af = SubElement(track, "AutoFilter2")
        SubElement(SubElement(af, "On"), "Manual").set("Value", "true")
        SubElement(SubElement(af, "Filter_Frequency"), "Manual").set("Value", str(fhz))
        SubElement(SubElement(af, "Filter_Type"), "Manual").set("Value", ftype)
        SubElement(SubElement(af, "Filter_Resonance"), "Manual").set("Value", resonance)
        SubElement(af, "AutomationTarget").set("Id", f"90{i}")

    clip = SubElement(track, "AudioClip")
    SubElement(clip, "CurrentStart").set("Value", "0")
    SubElement(clip, "CurrentEnd").set("Value", str(seconds * bpm / 60.0))
    loop = SubElement(clip, "Loop")
    SubElement(loop, "LoopStart").set("Value", "0")
    SubElement(loop, "LoopEnd").set("Value", str(seconds * bpm / 60.0))
    SubElement(loop, "StartRelative").set("Value", "0")
    SubElement(loop, "LoopOn").set("Value", "false")
    SubElement(clip, "RelativePath").set("Value", src_name)
    wm = SubElement(clip, "WarpMarkers")
    for beat in range(0, int(seconds * bpm / 60.0) + 1):
        w = SubElement(wm, "WarpMarker")
        w.set("SecTime", str(beat * 60.0 / bpm))
        w.set("BeatTime", str(float(beat)))

    main = SubElement(live, "MainTrack")
    mmix = SubElement(main, "Mixer")
    SubElement(SubElement(mmix, "Volume"), "Manual").set("Value", str(master))
    mchain = SubElement(main, "DeviceChain")
    mdevs = SubElement(mchain, "Devices")
    for d in master_devices:
        SubElement(mdevs, d)

    def env(pointee, pts):
        e = SubElement(live, "AutomationEnvelope")
        SubElement(e, "PointeeId").set("Value", pointee)
        ev = SubElement(SubElement(e, "Automation"), "Events")
        for t, v in pts:
            fe = SubElement(ev, "FloatEvent")
            fe.set("Time", str(t))
            fe.set("Value", str(v))

    if gain_pts:
        env("100", gain_pts)
    if shelf_pts:
        env("200", shelf_pts)
    if automate_filter:
        env("900", [(0.0, 19999.99), (16.0, 400.0)])

    p = tmp_path / "m.als"
    p.write_bytes(gzip.compress(tostring(root, encoding="utf-8")))
    return p


def _tone(path, seconds=40.0, sr=44100, hzs=(40.0, 250.0, 1000.0, 4000.0),
          amp=0.15):
    t = np.arange(int(seconds * sr)) / sr
    y = sum(np.sin(2 * math.pi * hz * t) for hz in hzs) * amp
    sf.write(str(path), np.stack([y, y], axis=1), sr, subtype="PCM_24")


# --------------------------------------------------------------------------- #
# Guards                                                                      #
# --------------------------------------------------------------------------- #

def test_refuses_a_non_empty_master_chain(tmp_path):
    """A bus processor makes the sum non-linear, and the model has no term for
    it. Refusing beats returning a number that looks fine."""
    _tone(tmp_path / "src.wav")
    als = _als(tmp_path, master_devices=("Compressor2",))
    with pytest.raises(MP.ModelRefused, match="master chain"):
        MP.load_model(als, tmp_path)


def test_refuses_an_automated_filter(tmp_path):
    """A swept filter is a time-varying term the model does not carry. It must
    fail loudly - the pipeline CAN automate these (lp_filter/hp_filter param
    keys exist), so this is a live risk, not a hypothetical one."""
    _tone(tmp_path / "src.wav")
    als = _als(tmp_path, automate_filter=True)
    with pytest.raises(MP.ModelRefused, match="automated"):
        MP.load_model(als, tmp_path)


def test_refuses_filter_resonance(tmp_path):
    _tone(tmp_path / "src.wav")
    als = _als(tmp_path, resonance="0.7")
    with pytest.raises(MP.ModelRefused, match="resonance"):
        MP.load_model(als, tmp_path)


def test_accepts_the_ordinary_transparent_path(tmp_path):
    _tone(tmp_path / "src.wav")
    m = MP.load_model(_als(tmp_path), tmp_path)
    assert len(m.tracks) == 1
    assert m.tracks[0].filters == [("low", 19999.99), ("high", 20.0)]


# --------------------------------------------------------------------------- #
# Physics                                                                     #
# --------------------------------------------------------------------------- #

def test_gain_moves_every_band_by_the_same_amount(tmp_path):
    """A broadband gain is broadband. If halving the gain does not move all
    five bands by 6 dB, the gain staging is wrong."""
    _tone(tmp_path / "src.wav")
    full = MP.predict_bands(MP.load_model(_als(tmp_path), tmp_path), 10.0)
    half = MP.predict_bands(
        MP.load_model(_als(tmp_path, trim=0.5), tmp_path), 10.0)
    for band in full["band_db"]:
        d = full["band_db"][band] - half["band_db"][band]
        assert d == pytest.approx(6.02, abs=0.15), (band, d)


def test_low_shelf_cuts_the_bottom_and_leaves_the_top(tmp_path):
    """The shelf is a filter, not a broadband trim. Treating it as a scalar
    would move 'high' as much as 'sub', which is the error this pins."""
    _tone(tmp_path / "src.wav")
    flat = MP.predict_bands(MP.load_model(_als(tmp_path), tmp_path), 10.0)
    cut = MP.predict_bands(
        MP.load_model(_als(tmp_path, shelf_pts=[(0.0, 0.18)]), tmp_path), 10.0)
    sub_drop = flat["band_db"]["sub"] - cut["band_db"]["sub"]
    high_drop = flat["band_db"]["high"] - cut["band_db"]["high"]
    assert sub_drop > 8.0, sub_drop          # the shelf really cuts the bottom
    assert abs(high_drop) < 1.0, high_drop   # and leaves the top alone
    assert sub_drop > high_drop + 8.0


def test_a_muted_track_contributes_nothing(tmp_path):
    _tone(tmp_path / "src.wav")
    m = MP.load_model(_als(tmp_path, gain_pts=[(0.0, 0.0)]), tmp_path)
    out = MP.predict_bands(m, 10.0)
    assert out["shares"] == {}
    assert all(v < -100 for v in out["band_db"].values()), out["band_db"]


# --------------------------------------------------------------------------- #
# Knowing what it is worth                                                    #
# --------------------------------------------------------------------------- #

def test_sizing_asks_whether_the_move_beats_the_error_bar():
    """The useful question is not "is this band accurate" but "is this
    correction bigger than the uncertainty behind it".

    The raw model over-predicts the low end by a fixed amount whose mechanism
    is unknown; calibrating it out takes every band under 0.5 dB mean error.
    What is left is a tail, and the tail is what produces a wrong-sized fix -
    so sizing is judged against the 95th percentile, not the mean.
    """
    C = MP.can_size_correction
    # A big move in a band good to about 1 dB: worth making.
    assert C("sub", -6.0) is True
    assert C("bass", -5.0) is True
    # A move inside the noise: not worth making, in ANY band.
    assert C("sub", -1.0) is False
    assert C("mid", -1.0) is False
    # Sign must not matter - a boost and a cut carry the same uncertainty.
    assert C("bass", 5.0) == C("bass", -5.0)
    # An unknown band can never size anything.
    assert C("nonsense", -20.0) is False
    # The margin is real: just under it fails, just over it passes.
    p95 = MP.BAND_P95_DB["mid"]
    assert C("mid", -(MP.SIZING_MARGIN * p95 * 0.99)) is False
    assert C("mid", -(MP.SIZING_MARGIN * p95 * 1.01)) is True


def test_calibration_is_applied_and_the_raw_value_is_kept(tmp_path):
    """The offset is empirical, so the uncalibrated number stays visible - a
    correction derived from a fudge nobody can inspect is not auditable."""
    _tone(tmp_path / "src.wav")
    out = MP.predict_bands(MP.load_model(_als(tmp_path), tmp_path), 10.0)
    for band, off in MP.BAND_CALIBRATION_DB.items():
        raw = out["band_db_uncalibrated"][band]
        assert out["band_db"][band] == pytest.approx(raw - off, abs=1e-9), band
    # The low end is where the offset lives; the top is untouched.
    assert MP.BAND_CALIBRATION_DB["sub"] > 2.0
    assert abs(MP.BAND_CALIBRATION_DB["mid"]) < 0.1


def test_every_prediction_carries_its_uncertainty(tmp_path):
    """A number without its error bar is how a 3 dB bias becomes a 3 dB fix."""
    _tone(tmp_path / "src.wav")
    out = MP.predict_bands(MP.load_model(_als(tmp_path), tmp_path), 10.0)
    assert set(out["uncertainty_db"]) == set(out["band_db"])
    assert out["uncertainty_db"]["sub"] == MP.BAND_P95_DB["sub"]
    assert MP.band_uncertainty_db("mid") < MP.band_uncertainty_db("sub")


@pytest.mark.skipif(
    not (ROOT / "Test Project/14.08.26/Output/14.08.26 Mix V16.wav").exists(),
    reason="audio corpus absent")
def test_accuracy_against_the_real_v16_bounce():
    """Corpus pin. The model is developed against this render, so this is a
    regression guard rather than evidence - but a regression here means the
    published per-band figures have become a lie."""
    import render_check as RC
    out = ROOT / "Test Project/14.08.26/Output"
    model = MP.load_model(out / "14.08.26 Mix V16.als",
                          ROOT / "Test Project/14.08.26/Audio")
    cache = {}
    errs = {n: [] for n, _, _ in RC.DIP_BANDS}
    with sf.SoundFile(str(out / "14.08.26 Mix V16.wav")) as fh:
        sr = float(fh.samplerate)
        for t in (1500.0, 2400.0, 3600.0, 4200.0):
            a, b = int((t - 1.5) * sr), int((t + 1.5) * sr)
            fh.seek(a)
            y = fh.read(b - a, dtype="float64", always_2d=True)
            p = MP.predict_bands(model, t, cache=cache)["band_db"]
            for n, lo, hi in RC.DIP_BANDS:
                errs[n].append(p[n] - RC._band_rms_db(y, sr, lo, hi))
    # The bands the model is allowed to size from must stay accurate.
    for n in ("lowmid", "mid", "high"):
        mae = float(np.mean(np.abs(errs[n])))
        assert mae < 1.0, (n, mae, errs[n])
