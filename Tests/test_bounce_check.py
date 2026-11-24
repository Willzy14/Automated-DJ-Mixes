"""Bounce check: every measurement is run on a synthetic bounce whose answer is known, then the CLI for real."""
import gzip
import json
import math
import subprocess
import sys
import tracemalloc
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np
import pytest
import pyloudnorm as pyln
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Source"))
import bounce_check as bc  # noqa: E402
from render_check import TempoMap  # noqa: E402

SR = 44100
CLI = ROOT / "Tools" / "check_bounce.py"
TEMPO = TempoMap.flat(120.0)          # a beat is 0.5 s, a bar 2 s
S, E, DUR = 50.0, 80.0, 110.0         # overlap seconds in the three-part bounces
NOISE_DB = -50.0


# ---------------------------------------------------------------- synthetic bounces

def sine(freq, dur, db, phase=0.0):
    t = np.arange(int(round(dur * SR))) / SR
    return 10 ** (db / 20.0) * np.sqrt(2.0) * np.sin(2 * np.pi * freq * t + phase)


def white(dur, db, seed):
    return 10 ** (db / 20.0) * np.random.default_rng(seed).standard_normal(int(round(dur * SR)))


def write(path, left, right=None):
    right = left if right is None else right
    sf.write(str(path), np.stack([left, right], axis=1).astype("float32"), SR, subtype="FLOAT")
    return path


def join(start=S, end=E):
    return bc.Join(1, "Out - Song A SW V2", "In - Song B SW V3", start * 2, end * 2, start, end)


def hops_of(path):
    return bc.analyse_span(path, 0.0, DUR)


def measure(path, j=None):
    return bc.measure_join(hops_of(path), j or join(), TEMPO, DUR)


def build(path, before, during, after, noise_db=None):
    """Lead-in 10 s of silence, then before 40 s (so it ends at S), during 30 s, after 30 s: total 110 s."""
    def st(seg):
        return (seg[0], seg[0] if seg[1] is None else seg[1])
    segs = [st(x) for x in (before, during, after)]
    left = np.concatenate([np.zeros(int(10 * SR))] + [p[0] for p in segs])
    right = np.concatenate([np.zeros(int(10 * SR))] + [p[1] for p in segs])
    if noise_db is not None:
        left = left + white(len(left) / SR, noise_db, 1)
        right = right + white(len(right) / SR, noise_db, 2)
    return write(path, left, right)


@pytest.fixture(scope="module")
def tmp(tmp_path_factory):
    return tmp_path_factory.mktemp("bounce_check")


# ---------------------------------------------------------------- loudness

def test_k_weighted_level_matches_pyloudnorm(tmp):
    rng = np.random.default_rng(3)
    x = np.stack([10 ** (-23 / 20) * rng.standard_normal(int(DUR * SR)), 10 ** (-26 / 20) * rng.standard_normal(int(DUR * SR))], axis=1)
    path = tmp / "noise.wav"
    sf.write(str(path), x.astype("float32"), SR, subtype="FLOAT")
    h = bc.analyse_span(path, 0.0, DUR)
    mine = bc._lufs(float(np.mean(h.k_l + h.k_r)))
    ref = pyln.Meter(SR).integrated_loudness(x.astype("float32").astype("float64"))
    assert abs(mine - ref) < 0.05, (mine, ref)


def test_loudness_step_and_pileup_read_back_in_lu(tmp):
    # sides at -20 and -14 dBFS (a +6.02 LU step), the blend at -11 (3 LU above the louder side)
    path = build(tmp / "loud.wav", (sine(1000, 40, -20), None), (sine(1000, 30, -11), None), (sine(1000, 30, -14), None))
    loud = measure(path)["loudness"]
    assert abs(loud["step_lu"] - 6.02) < 0.1, loud
    assert abs(loud["peak_vs_sides_lu"] - 3.0) < 0.15, loud
    assert abs(loud["mean_vs_sides_lu"] - 3.0) < 0.15, loud
    assert abs((loud["level_lufs"]["after"] - loud["level_lufs"]["before"]) - loud["step_lu"]) < 1e-9


def test_a_quiet_blend_reads_negative_against_the_sides(tmp):
    path = build(tmp / "dip.wav", (sine(1000, 40, -14), None), (sine(1000, 30, -20), None), (sine(1000, 30, -14), None))
    loud = measure(path)["loudness"]
    assert abs(loud["step_lu"]) < 0.1
    assert abs(loud["peak_vs_sides_lu"] + 6.02) < 0.15, loud


# ---------------------------------------------------------------- tonal

def test_one_band_step_is_found_and_named(tmp):
    # 1 kHz +9 dB, a constant 100 Hz tone and a noise bed so only the mid band really changes
    before = (sine(100, 40, -30) + sine(1000, 40, -30), None)
    during = (sine(100, 30, -30) + sine(1000, 30, -25), None)
    after = (sine(100, 30, -30) + sine(1000, 30, -21), None)
    tonal = measure(build(tmp / "tonal.wav", before, during, after, noise_db=NOISE_DB))["tonal"]
    assert tonal["worst_band"] == "mid", tonal["step_db"]
    assert abs(tonal["worst_step_db"] - 9.0) < 0.4, tonal["step_db"]
    assert abs(tonal["step_db"]["low"]) < 0.4, tonal["step_db"]
    assert abs(tonal["step_db"]["high"]) < 0.4, tonal["step_db"]
    # the middle window carries the +5 dB in between
    assert 4.0 < tonal["levels_db"]["mid"]["during"] - tonal["levels_db"]["mid"]["before"] < 6.0


def test_a_negative_step_keeps_its_sign(tmp):
    before = (sine(100, 40, -30) + sine(1000, 40, -24), None)
    during = (sine(100, 30, -30) + sine(1000, 30, -27), None)
    after = (sine(100, 30, -30) + sine(1000, 30, -30), None)
    tonal = measure(build(tmp / "tonal_neg.wav", before, during, after, noise_db=NOISE_DB))["tonal"]
    assert tonal["worst_band"] == "mid", tonal["step_db"]
    assert -6.4 < tonal["worst_step_db"] < -5.6, tonal["step_db"]
    assert abs(tonal["step_db"]["low"]) < 0.4, tonal["step_db"]
    assert max(abs(v) for b, v in tonal["step_db"].items() if b != "mid") < 4.0, tonal["step_db"]


def test_each_band_level_matches_the_filter_the_edges_say(tmp):
    """White noise has a known power in any band: the mean squared magnitude of that band's own filter. The edges here are
    written out, not read from the module, so a band that moved shows as a level that is wrong by dB."""
    edges = {"sub": (20, 60), "low": (60, 150), "low_mid": (150, 500), "mid": (500, 2000), "high_mid": (2000, 6000), "high": (6000, None)}
    noise_db = -20.0
    x = white(40.0, noise_db, 21)
    path = write(tmp / "bands.wav", x, white(40.0, noise_db, 22))
    levels = bc.analyse_span(path, 0.0, 40.0)
    from scipy import signal as sg
    for i, (name, (lo, hi)) in enumerate(edges.items()):
        sos = sg.butter(4, lo, btype="highpass", fs=SR, output="sos") if hi is None else sg.butter(4, [lo, hi], btype="bandpass", fs=SR, output="sos")
        w, h = sg.sosfreqz(sos, worN=200000, fs=SR)
        expect = noise_db + 10 * math.log10(float(np.mean(np.abs(h) ** 2)))
        got = 10 * math.log10(float(levels.band[:, i].mean()))
        assert abs(got - expect) < 0.2, (name, got, expect)


# ---------------------------------------------------------------- low-end summing

def _bass(dur, db, phase=0.0):
    return sine(50, dur, db, phase)


def test_bass_phase_hole_shows_as_the_level_dropping(tmp):
    # during the overlap a second 50 Hz bass at half the amplitude, 180 degrees out: the sum is 6.02 dB down
    outgoing = _bass(30, -20) + _bass(30, -26.02, math.pi)
    path = build(tmp / "bass_hole.wav", (_bass(40, -20), None), (outgoing, None), (_bass(30, -20), None))
    low = measure(path)["low_end"]
    assert abs(low["level_vs_sides_db"] + 6.02) < 0.3, low
    assert low["corr"]["during"] > 0.99 and low["corr_drop"] > -0.01


def test_worst_bar_is_the_bar_with_the_hole_and_its_time_is_right(tmp):
    n = int(30 * SR)
    during = _bass(30, -20)
    bar = 7                                              # bar 8 of the overlap: seconds 14 to 16, 64.0 s to 66.0 s in the bounce
    a, b = int(14 * SR), int(16 * SR)
    during[a:b] += sine(50, 2.0, -20, math.pi)           # a second, equal bass, 180 degrees out: that bar cancels
    path = build(tmp / "bass_bar.wav", (_bass(40, -20), None), (during, None), (_bass(30, -20), None))
    low = measure(path)["low_end"]
    worst = low["worst_bar"]
    assert worst["bar"] == bar + 1, worst
    assert abs(worst["start_s"] - (S + 14.0)) < 0.01, worst
    assert worst["level_vs_sides_db"] < -20.0, worst
    assert n == len(during)


def test_stereo_bass_out_of_phase_shows_as_the_correlation_dropping(tmp):
    # sides: the same bass in both channels (corr 1). overlap: right channel 90 degrees away (corr 0)
    left = _bass(30, -20)
    right = _bass(30, -20, math.pi / 2)
    path = build(tmp / "bass_corr.wav", (_bass(40, -20), None), (left, right), (_bass(30, -20), None))
    low = measure(path)["low_end"]
    assert abs(low["corr"]["during"]) < 0.05, low["corr"]
    assert low["corr"]["before"] > 0.99 and low["corr"]["after"] > 0.99
    assert abs(low["corr_drop"] + 1.0) < 0.06, low
    assert abs(low["level_vs_sides_db"]) < 0.2


def test_a_heavier_low_end_in_the_blend_reads_positive(tmp):
    path = build(tmp / "bass_heavy.wav", (_bass(40, -26), None), (_bass(30, -20), None), (_bass(30, -23), None))
    low = measure(path)["low_end"]
    assert abs(low["level_vs_sides_db"] - 3.0) < 0.3, low


# ---------------------------------------------------------------- width

def _wide(dur, side_ratio):
    m = sine(1000, dur, -25)
    return m * (1 + side_ratio), m * (1 - side_ratio)


def test_width_step_reads_back_in_db(tmp):
    # side over mid is 0.1 before (-20 dB) and 0.3 after (-10.46 dB): a +9.54 dB step
    path = build(tmp / "width.wav", _wide(40, 0.1), _wide(30, 0.2), _wide(30, 0.3))
    width = measure(path)["width"]
    assert abs(width["side_over_mid_db"]["before"] + 20.0) < 0.2, width
    assert abs(width["side_over_mid_db"]["after"] + 10.46) < 0.2, width
    assert abs(width["step_db"] - 9.54) < 0.2, width
    assert abs(width["side_over_mid_db"]["during"] + 13.98) < 0.2, width           # 0.2 in between


def test_width_ignores_a_wide_bass_below_150_hz(tmp):
    # a mono 1 kHz tone with a 40 Hz tone out of polarity between the channels: wide below 150 Hz, mono above it
    m = sine(1000, 30, -25)
    b = sine(40, 30, -10)
    path = build(tmp / "wide_bass.wav", (m, None), (m, None), (m + b, m - b))
    width = measure(path)["width"]
    assert width["side_over_mid_db"]["after"] < -25.0, width            # the 40 Hz side is some 46 dB down in the >150 Hz band
    assert width["mono"]["before"] is True and width["mono"]["after"] is False, width           # a mono signal has no width to step
    assert "step_db" not in width, width


# ---------------------------------------------------------------- silence, mono, channels, gating

def test_a_silent_window_is_not_measured_and_makes_no_step(tmp):
    path = build(tmp / "silent_after.wav", (sine(1000, 40, -14) + sine(50, 40, -20), None), (sine(1000, 30, -14) + sine(50, 30, -20), None),
                 (np.zeros(int(30 * SR)), None))
    res = measure(path)
    assert res["silent_windows"] == ["after"], res["notes"]
    assert any("after window silent: not measured" in n for n in res["notes"]), res["notes"]
    assert "step_lu" not in res["loudness"] and "step_db" not in res["tonal"] and "step_db" not in res["width"], res
    assert "level_vs_sides_db" not in res["low_end"]
    assert bc.scalars(res) == {}, "a join with a silent window feeds no range"


def test_dither_level_noise_counts_as_silent(tmp):
    path = build(tmp / "dither_after.wav", (sine(1000, 40, -14), None), (sine(1000, 30, -14), None), (white(30, -90, 7), None))
    res = measure(path)
    assert res["silent_windows"] == ["after"] and "step_lu" not in res["loudness"], res


def test_a_silent_gap_does_not_pull_the_window_level_down(tmp):
    whole = measure(build(tmp / "no_gap.wav", (sine(1000, 40, -20), None), (sine(1000, 30, -20), None), (sine(1000, 30, -20), None)))
    # 4 s of digital silence inside the 20 s before window (36 s to 40 s): gated out, as BS.1770 gates it
    gap = np.concatenate([sine(1000, 26, -20), np.zeros(int(4 * SR)), sine(1000, 10, -20)])
    gapped = measure(build(tmp / "gap.wav", (gap, None), (sine(1000, 30, -20), None), (sine(1000, 30, -20), None)))
    assert not gapped["silent_windows"], gapped["notes"]
    # the two hops that straddle the gap edges are half full and stay in (as BS.1770 blocks would): about 0.07 LU; ungated it is -0.97 LU
    assert abs(gapped["loudness"]["level_lufs"]["before"] - whole["loudness"]["level_lufs"]["before"]) < 0.15, gapped["loudness"]
    # more than half silent: the window is silent
    mostly = np.concatenate([sine(1000, 28, -20), np.zeros(int(12 * SR))])
    res = measure(build(tmp / "mostly_gap.wav", (mostly, None), (sine(1000, 30, -20), None), (sine(1000, 30, -20), None)))
    assert res["silent_windows"] == ["before"], res["notes"]


def test_a_mono_file_reads_the_level_pyloudnorm_reads(tmp):
    x = white(60, -23, 31)
    path = tmp / "mono.wav"
    sf.write(str(path), x.astype("float32"), SR, subtype="FLOAT")
    h = bc.analyse_span(path, 0.0, 60.0)
    mine = bc._lufs(float(np.mean(h.k_l + h.k_r)))
    ref = pyln.Meter(SR).integrated_loudness(x.astype("float32").astype("float64"))
    assert abs(mine - ref) < 0.05, (mine, ref)


def test_more_than_two_channels_are_refused_with_a_clear_message(tmp):
    path = tmp / "six.wav"
    sf.write(str(path), np.zeros((SR * 2, 3), dtype="float32"), SR, subtype="FLOAT")
    with pytest.raises(ValueError, match="3 channels: only mono and stereo"):
        bc.analyse_span(path, 0.0, 2.0)
    with pytest.raises(ValueError, match="3 channels: only mono and stereo"):
        bc.check_bounce(path, TEMPO, [join(0.2, 1.0)], 10.0, flam=False)


def test_a_mono_level_step_is_not_reported_as_a_width_step(tmp):
    path = build(tmp / "mono_step.wav", (sine(1000, 40, -20), None), (sine(1000, 30, -20), None), (sine(1000, 30, -14), None))
    res = measure(path)
    assert abs(res["loudness"]["step_lu"] - 6.02) < 0.1
    assert res["width"]["mono"] == {"before": True, "during": True, "after": True}, res["width"]
    assert "step_db" not in res["width"] and "width_step_db" not in bc.scalars(res), res["width"]
    assert any("width step not measured" in n and "mono" in n for n in res["notes"]), res["notes"]


def test_the_lowest_correlation_bar_ignores_bars_with_no_low_end(tmp):
    n, fade = int(2 * SR), int(0.4 * SR)
    a = int(14 * SR)                                   # bar 8 of the overlap: no bass at all (faded out and in), uncorrelated hiss
    env = np.ones(int(30 * SR))
    env[a - fade:a] = np.linspace(1.0, 0.0, fade)
    env[a:a + n] = 0.0
    env[a + n:a + n + fade] = np.linspace(0.0, 1.0, fade)
    left, right = _bass(30, -20) * env, _bass(30, -20) * env
    left[a:a + n] = white(2.0, -70, 41)
    right[a:a + n] = white(2.0, -70, 42)
    low = measure(build(tmp / "bar_hiss.wav", (_bass(40, -20), None), (left, right), (_bass(30, -20), None)))["low_end"]
    assert low["worst_bar"]["bar"] == 8, low["worst_bar"]               # the dip is still the dip
    assert low["lowest_corr_bar"]["bar"] != 8, low["lowest_corr_bar"]   # but a bar with no low end has no correlation to speak of


def test_a_low_band_with_no_energy_in_one_channel_prints_na_and_does_not_crash(tmp):
    path = build(tmp / "left_only_bass.wav", (_bass(40, -20), np.zeros(int(40 * SR))), (_bass(30, -20), np.zeros(int(30 * SR))),
                 (_bass(30, -20), np.zeros(int(30 * SR))))
    res = measure(path)
    assert res["low_end"]["corr"]["during"] is None and "level_vs_sides_db" in res["low_end"], res["low_end"]
    res["join"] = {"index": 1, "out": "A - x", "in": "B - y", "start_s": S, "end_s": E}
    res["flam"] = {"state": "not_run"}
    line = bc.summary_line(res)
    assert "corr n/a" in line, line
    assert "low_corr_overlap" not in bc.scalars(res)


def test_hops_of_a_window_lie_wholly_inside_it_off_the_hop_grid(tmp):
    # the overlap starts at 50.1 s, between hops; the before window must hold none of the louder overlap, the during window none of the before
    quiet, loud = sine(1000, 40.1, -30), sine(1000, 30, -10)
    path = build(tmp / "off_grid.wav", (quiet[:int(round(40.1 * SR))], None), (loud, None), (sine(1000, 29.9, -30), None))
    j = join(50.1, 80.1)
    h = bc.analyse_span(path, 29.9, 100.0)
    res = bc.measure_join(h, j, TEMPO, 110.0)
    lv = res["loudness"]["level_lufs"]
    assert abs((lv["during"] - lv["before"]) - 20.0) < 0.05, lv       # 0.25 s of the louder part in the before window would add 3 dB
    assert abs((lv["after"] - lv["before"])) < 0.05, lv
    for name in ("before", "during", "after"):
        w = res["windows"][name]
        i0, i1 = h.span(w["start_s"], w["end_s"])
        assert h.t0 + i0 * h.hop_s >= w["start_s"] - 1e-9 and h.t0 + i1 * h.hop_s <= w["end_s"] + 1e-9, (name, w)


def test_a_short_overlap_off_the_hop_grid_still_gets_its_blend_peak_or_a_note(tmp):
    for off in (0.0, 0.05, 0.1, 0.15, 0.2, 0.24):
        j = join(50.0 + off, 53.4 + off)
        path = build(tmp / f"short_ov_{int(off * 100)}.wav", (sine(1000, 40 + off, -20), None), (sine(1000, 3.4, -11), None),
                     (sine(1000, 56.6 - off, -14), None))
        res = measure(path, j)
        assert "peak_vs_sides_lu" in res["loudness"] or any("during window" in n for n in res["notes"]), (off, res["loudness"], res["notes"])


def test_the_corr_drop_names_the_concerning_end_both_ways(tmp):
    ranges = {"low_corr_drop": {"n": 8, "min": -0.01, "median": 0.0, "max": 0.07, "thin": False}}
    # concerning: the sides are mono bass (corr 1) and the overlap has the bass opposite in the two channels (corr -1)
    left = _bass(30, -20)
    path = build(tmp / "opp.wav", (_bass(40, -20), None), (left, -left), (_bass(30, -20), None))
    low = measure(path)["low_end"]
    assert abs(low["corr_drop"] + 2.0) < 0.05, low
    res_opp = {"loudness": {}, "tonal": {}, "low_end": low, "width": {}}
    assert bc.judge(res_opp, ranges)["low_corr_drop"]["verdict"] == "outside"
    # not a fault: the sides are a wide bass (corr 0.5) and the overlap is the same bass in both channels (corr 1)
    wide_l, wide_r = _bass(40, -20), _bass(40, -20, math.pi / 3)
    path = build(tmp / "same.wav", (wide_l, wide_r), (_bass(30, -20), None), (_bass(30, -20), _bass(30, -20, math.pi / 3)))
    low = measure(path)["low_end"]
    assert abs(low["corr_drop"] - 0.5) < 0.05, low
    res_same = {"loudness": {}, "tonal": {}, "low_end": low, "width": {}}
    assert bc.judge(res_same, ranges)["low_corr_drop"]["verdict"] == "inside"


# ---------------------------------------------------------------- the start of the bounce

def _shifted_kick_bounce(tmp, name, shift_s):
    src = kick_bounce(tmp / (name + "_src.wav"), 0.0)
    x, sr = sf.read(str(src), dtype="float32")
    path = tmp / (name + ".wav")
    sf.write(str(path), np.concatenate([np.zeros(int(round(shift_s * sr)), dtype="float32"), x]), sr, subtype="FLOAT")
    return path


def test_a_bounce_with_leading_silence_warns_that_it_may_not_start_at_beat_0(tmp):
    j = [kick_join()]
    ok = bc.check_bounce(_shifted_kick_bounce(tmp, "start_ok", 0.0), TEMPO, j, 400.0, flam=False)
    assert ok["mapping"]["start"]["warnings"] == [] and ok["mapping"]["ok"], ok["mapping"]
    late = bc.check_bounce(_shifted_kick_bounce(tmp, "start_4s", 4.0), TEMPO, j, 400.0, flam=False)
    start = late["mapping"]["start"]
    assert abs(start["first_sound_s"] - 4.0) < 0.5 and start["warnings"], start
    assert any("silence" in w for w in start["warnings"]), start


def test_a_bounce_started_a_fraction_of_a_beat_late_warns_from_the_kick_grid(tmp):
    j = [kick_join()]
    off = bc.check_bounce(_shifted_kick_bounce(tmp, "start_130ms", 0.13), TEMPO, j, 400.0, flam=False)
    start = off["mapping"]["start"]
    assert start["grid_phase_ms"] is not None and abs(start["grid_phase_ms"]) > bc.GRID_PHASE_WARN_MS, start
    assert any("kick grid" in w for w in start["warnings"]), start
    good = bc.check_bounce(_shifted_kick_bounce(tmp, "start_good", 0.0), TEMPO, j, 400.0, flam=False)
    assert good["mapping"]["start"]["grid_phase_ms"] is not None and abs(good["mapping"]["start"]["grid_phase_ms"]) < bc.GRID_PHASE_WARN_MS


def test_the_cli_prints_the_start_warning_loudly(tmp):
    d = tmp / "cli_start"
    d.mkdir()
    sh = _shifted_kick_bounce(tmp, "cli_start_src", 4.0)
    (d / "Mix.wav").write_bytes(sh.read_bytes())
    plan = _plan([[0.0, 120.0], [400.0, 120.0]], [(140.0, 220.0)], [("Out - A", 220.0), ("In - B", 400.0)])
    report = _report([(140.0, 220.0)], [(0.0, 220.0), (140.0, 400.0)], ["Out - A", "In - B"])
    (d / "MIX_PLAN.json").write_text(json.dumps(plan))
    (d / "ARRANGEMENT_REPORT.json").write_text(json.dumps(report))
    proc = _cli(d / "Mix.wav", "--no-flam", "--out", d / "out", "--ranges", d / "none.json")
    assert "START WARNING" in proc.stdout, proc.stdout
    data = json.loads((d / "out" / "bounce_check.json").read_text())
    assert data["mapping"]["start"]["warnings"], data["mapping"]


def _bar8_case(tmp, name, bass_db, hiss_db):
    n, fade = int(2 * SR), int(0.4 * SR)
    a = int(14 * SR)
    env = np.ones(int(30 * SR))
    env[a - fade:a] = np.linspace(1.0, 0.0, fade)
    env[a:a + n] = 0.0
    env[a + n:a + n + fade] = np.linspace(0.0, 1.0, fade)
    left, right = _bass(30, bass_db) * env, _bass(30, bass_db) * env
    left[a:a + n] = white(2.0, hiss_db, 41)
    right[a:a + n] = white(2.0, hiss_db, 42)
    return measure(build(tmp / name, (_bass(40, bass_db), None), (left, right), (_bass(30, bass_db), None)))["low_end"]


def test_the_lowest_correlation_gate_works_on_each_of_its_two_rules(tmp):
    # a bar 40 dB under the loudest but well above silence: only the 30 dB rule can skip it
    low = _bar8_case(tmp, "gate_30.wav", -6, -22)
    assert low["worst_bar"]["bar"] == 8 and low["lowest_corr_bar"]["bar"] != 8, (low["worst_bar"], low["lowest_corr_bar"])
    # a silent bar (at the floor) only 25 dB under a quiet loudest bar: only the silence rule can skip it
    low = _bar8_case(tmp, "gate_silent.wav", -32, -110)
    assert low["worst_bar"]["bar"] == 8 and low["worst_bar"]["level_db"] <= bc.LEVEL_FLOOR_DB + 1e-9, low["worst_bar"]
    assert low["lowest_corr_bar"]["bar"] != 8, low["lowest_corr_bar"]


# ---------------------------------------------------------------- windows and notes

def test_a_side_window_stops_at_the_neighbouring_join(tmp):
    path = build(tmp / "neigh.wav", (sine(1000, 40, -20), None), (sine(1000, 30, -20), None), (sine(1000, 30, -20), None))
    j = join()
    j.prev_end_s, j.next_start_s = S - 8.0, E + 4.0
    res = bc.measure_join(hops_of(path), j, TEMPO, DUR)
    assert abs(res["windows"]["before"]["start_s"] - (S - 8.0)) < 1e-9
    assert abs(res["windows"]["after"]["end_s"] - (E + 4.0)) < 1e-9
    assert res["windows"]["before"]["measured_s"] <= 8.0 + 1e-9
    assert not [n for n in res["notes"] if "width" not in n], res["notes"]       # (a mono sine has no width to step: that note is right)


def test_a_side_window_under_three_seconds_is_not_measured_and_says_so(tmp):
    path = build(tmp / "short.wav", (sine(1000, 40, -20), None), (sine(1000, 30, -20), None), (sine(1000, 30, -20), None))
    j = join()
    j.next_start_s = E + 2.0
    res = bc.measure_join(hops_of(path), j, TEMPO, DUR)
    assert any("after window" in n for n in res["notes"]), res["notes"]
    assert "step_lu" not in res["loudness"] and "step_db" not in res["width"]
    assert "worst_band" not in res["tonal"]


def test_memory_stays_far_below_the_file_size(tmp):
    secs = 600
    x = np.stack([white(secs, -30, 5), white(secs, -30, 6)], axis=1)
    path = tmp / "long.wav"
    sf.write(str(path), x, SR, subtype="PCM_16")
    del x
    whole_file_mb = secs * SR * 2 * 8 / 2 ** 20            # one float64 stereo copy of the file: about 400 MB
    tracemalloc.start()
    h = bc.analyse_span(path, 0.0, float(secs))
    peak = tracemalloc.get_traced_memory()[1] / 2 ** 20
    tracemalloc.stop()
    assert h.n == secs * 4
    assert peak < whole_file_mb / 5, (peak, whole_file_mb)   # a whole-file read (and every filter copy of it) would break this


# ---------------------------------------------------------------- kick flam on the bounce

BEAT = 0.5


def _kick(kind):
    """Two different kicks: a noise click plus a carrier (3 kHz or 7 kHz) over a sine body (55 or 70 Hz)."""
    t = np.arange(int(0.35 * SR)) / SR
    click = np.random.default_rng(11 if kind == "out" else 12).standard_normal(len(t)) * np.exp(-t / 0.002) * (t < 0.012)
    if kind == "out":
        return 0.9 * click + 0.7 * np.sin(2 * np.pi * 3000 * t) * np.exp(-t / 0.003) + np.sin(2 * np.pi * 55 * t) * np.exp(-t / 0.12)
    return 0.8 * click + 0.9 * np.sin(2 * np.pi * 7000 * t + 1.0) * np.exp(-t / 0.0012) + np.sin(2 * np.pi * 70 * t) * np.exp(-t / 0.1)


def kick_bounce(path, in_offset_ms=8.0, out_beats=range(0, 220), in_beats=range(140, 400), seed=9, tempo=TEMPO):
    """in_offset_ms: one figure, or a function of the beat. tempo: the map the kicks are laid on (a ramp moves them off b * 0.5 s)."""
    n = int(round(float(tempo.beat_to_sec(400.0)) * SR))
    y = 0.002 * np.random.default_rng(seed).standard_normal(n)
    for kind, beats in (("out", out_beats), ("in", in_beats)):
        k = _kick(kind)
        for b in beats:
            off = (in_offset_ms(b) if callable(in_offset_ms) else in_offset_ms) if kind == "in" else 0.0
            i = int(round((float(tempo.beat_to_sec(float(b))) + off / 1000.0) * SR))
            y[i:i + len(k)] += k[:n - i]
    sf.write(str(path), y.astype("float32"), SR, subtype="FLOAT")
    return path


def kick_join():
    return bc.Join(1, "A", "B", 140.0, 220.0, 70.0, 110.0)


@pytest.fixture(scope="module")
def flam_runs(tmp):
    return {
        "late8": bc.measure_flam(kick_bounce(tmp / "late8.wav", 8.0), TEMPO, kick_join()),
        "early8": bc.measure_flam(kick_bounce(tmp / "early8.wav", -8.0), TEMPO, kick_join()),
        "aligned": bc.measure_flam(kick_bounce(tmp / "aligned.wav", 0.0), TEMPO, kick_join()),
        "late20": bc.measure_flam(kick_bounce(tmp / "late20.wav", 20.0), TEMPO, kick_join()),
        "late2": bc.measure_flam(kick_bounce(tmp / "late2.wav", 2.0), TEMPO, kick_join()),
        "no_incoming": bc.measure_flam(kick_bounce(tmp / "noin.wav", 8.0, in_beats=range(140, 220)), TEMPO, kick_join()),
        "silent_in": bc.measure_flam(kick_bounce(tmp / "silentin.wav", 8.0, in_beats=range(220, 400)), TEMPO, kick_join()),
        "few_in": bc.measure_flam(kick_bounce(tmp / "fewin.wav", 8.0, in_beats=range(140, 232)), TEMPO, kick_join()),
        "early_in": bc.measure_flam(kick_bounce(tmp / "earlyin.wav", 8.0, in_beats=range(60, 400)), TEMPO, kick_join()),
    }


def test_an_injected_8_ms_double_is_seen_with_its_spacing_and_sign(flam_runs):
    r = flam_runs["late8"]
    assert r["state"] == "flam_seen", r
    assert r["n_flam"] >= 0.8 * r["n_kick_beats"], r
    assert abs(r["median_spacing_ms"] - 8.0) < 1.5, r
    assert abs(r["median_lag_ms"] - 8.0) < 1.5 and r["incoming_later_fraction"] == 1.0, r
    assert r["control"]["n_flam"] == 0 and r["control"]["n_beats"] == 2 * bc.N_CONTROL


def test_an_incoming_kick_that_is_early_reads_negative(flam_runs):
    r = flam_runs["early8"]
    assert r["state"] == "flam_seen", r
    assert abs(r["median_lag_ms"] + 8.0) < 1.5 and r["incoming_later_fraction"] == 0.0, r


def test_a_20_ms_double_is_inside_the_window_and_measured(flam_runs):
    r = flam_runs["late20"]
    assert r["state"] == "flam_seen" and abs(r["median_spacing_ms"] - 20.0) < 2.0, r


def test_kicks_on_the_same_beat_are_not_a_flam(flam_runs):
    r = flam_runs["aligned"]
    assert r["state"] == "no_flam_seen", r
    assert r["n_flam"] == 0 and r["n_fit_both"] >= bc.MIN_FIT_BEATS, r


def test_a_double_under_the_window_is_not_called_a_flam_and_says_what_it_cannot_see(flam_runs):
    r = flam_runs["late2"]
    assert r["state"] == "no_flam_seen" and r["n_flam"] == 0, r
    assert any("under 3 ms" in n for n in r["notes"]), r["notes"]


def test_without_clean_incoming_kicks_outside_the_blend_it_says_not_measurable(flam_runs):
    r = flam_runs["no_incoming"]
    assert r["state"] == "not_measurable", r
    assert any("cannot be judged" in n for n in r["notes"]), r["notes"]


def test_an_incoming_kick_that_does_not_play_in_the_blend_is_not_measurable_never_clean(flam_runs):
    r = flam_runs["silent_in"]               # templates exist (before and after the blend) but the blend has no incoming kicks
    assert r["bank_out"] and r["bank_in"], r
    assert r["state"] == "not_measurable" and r["n_fit_both"] < bc.MIN_FIT_BEATS, r
    assert any("only" in n and "both kicks found" in n for n in r["notes"]), r["notes"]


def test_templates_learnt_from_the_control_beats_are_not_judged(flam_runs):
    r = flam_runs["few_in"]                  # only 12 incoming kicks after the blend: none can be held back as a control
    assert r["state"] == "not_measurable", r
    assert any("same beats as the control" in n for n in r["notes"]) and any("not judged" in n for n in r["notes"]), r["notes"]
    assert r["n_flam"] >= bc.MIN_FLAM_BEATS, r          # the fit still ran and says what it found; it just is not trusted


def test_a_few_doubles_in_a_short_overlap_are_too_few_beats_never_none_seen(tmp):
    doubles = {145, 149, 153}                # 3 of the 16 kick beats of a 16-beat overlap carry an 8 ms double
    path = kick_bounce(tmp / "few16.wav", lambda b: 8.0 if b in doubles else 0.0, out_beats=range(0, 156), in_beats=range(140, 400))
    r = bc.measure_flam(path, TEMPO, bc.Join(1, "A", "B", 140.0, 156.0, 70.0, 78.0))
    assert r["state"] == "too_few_beats", r
    assert r["n_flam"] == 3 and r["n_kick_beats"] == 16 and abs(r["fraction_of_fit_beats"] - 3 / r["n_fit_both"]) < 1e-9, r
    assert any("not counted as none seen" in n for n in r["notes"]), r["notes"]
    assert bc.scalars({"flam": r}) == {}, "the observed fraction must not feed the calibration ranges"
    line = bc.summary_line({"join": {"index": 1, "out": "A - x", "in": "B - y", "start_s": 70.0, "end_s": 78.0}, "loudness": {}, "tonal": {},
                            "low_end": {}, "width": {}, "flam": r})
    assert "too few beats to call: 3 of 16 fitted beats" in line and "none seen" not in line, line


def test_flam_is_found_on_a_bounce_made_with_a_tempo_ramp(tmp):
    ramp = TempoMap(np.array([0.0, 100.0, 300.0, 400.0]), np.array([120.0, 120.0, 130.0, 130.0]), 120.0)
    path = kick_bounce(tmp / "ramp.wav", 8.0, tempo=ramp)
    j = bc.Join(1, "A", "B", 140.0, 220.0, float(ramp.beat_to_sec(140.0)), float(ramp.beat_to_sec(220.0)))
    r = bc.measure_flam(path, ramp, j)
    assert r["state"] == "flam_seen" and abs(r["median_spacing_ms"] - 8.0) < 1.5, r
    flat = bc.measure_flam(path, TEMPO, bc.Join(1, "A", "B", 140.0, 220.0, 70.0, 110.0))
    assert flat["state"] != "flam_seen", flat   # read on the wrong (flat) map the kicks are not where the beats say


def test_a_fit_that_fires_on_the_control_beats_is_not_trusted_even_when_it_finds_nothing_in_the_blend(tmp):
    # the incoming kicks flam against the outgoing ones in the stretch used as the control, and sit on the beat in the blend itself
    path = kick_bounce(tmp / "ctrl_only.wav", lambda b: 8.0 if b < 140 else 0.0, in_beats=range(60, 400))
    r = bc.measure_flam(path, TEMPO, kick_join())
    assert r["control"]["n_flam"] >= 10 and r["n_flam"] == 0, r
    assert r["state"] == "not_measurable", r
    assert any("control beats" in n for n in r["notes"]), r["notes"]
    assert bc.scalars({"flam": r}) == {}, "0.0 from a fit that cannot tell a blend from a single track must not enter the ranges"


def test_the_fraction_is_over_the_beats_the_fit_placed_not_all_the_kick_beats(tmp):
    # the incoming kick is missing on every other beat of the blend: half the kick beats cannot be fitted, the other half all flam
    in_beats = [b for b in range(140, 400) if not (140 <= b < 220 and b % 2)]
    r = bc.measure_flam(kick_bounce(tmp / "half_fit.wav", 8.0, in_beats=in_beats), TEMPO, kick_join())
    assert r["state"] == "flam_seen", r
    assert r["n_fit_both"] <= 0.6 * r["n_kick_beats"], r
    assert r["fraction_of_fit_beats"] > 0.9 and bc.scalars({"flam": r})["flam_fraction_of_fitted_beats"] > 0.9, r


def test_control_beats_that_flam_too_stop_the_blend_being_called_a_flam(flam_runs):
    r = flam_runs["early_in"]            # the incoming kicks already play in the stretch used as the single-track control
    assert r["state"] == "not_measurable", r
    assert r["n_flam"] >= bc.MIN_FLAM_BEATS and r["control"]["n_flam"] >= 1, r
    assert any("control beats" in n for n in r["notes"]), r["notes"]


def test_literal_second_attack_test_tells_the_blend_from_the_control_on_clean_kicks(flam_runs):
    t = flam_runs["late8"]["onset_test"]
    assert t["overlap_fraction"] > 0.8 and t["control_fraction"] < 0.2 and t["discriminates"], t
    a = flam_runs["aligned"]["onset_test"]
    assert a["overlap_fraction"] < 0.2 and not a["discriminates"], a


def test_attack_pair_rules():
    def att(*pairs):
        return [{"t_ms": t, "rise": r} for t, r in pairs]
    assert bc.attack_pair(att((0.0, 1.0), (8.0, 0.9))) is not None
    assert bc.attack_pair(att((0.0, 1.0), (2.9, 1.0))) is None            # too close
    assert bc.attack_pair(att((0.0, 1.0), (25.1, 1.0))) is None           # too far
    assert bc.attack_pair(att((0.0, 1.0), (8.0, 0.2))) is None            # second one too weak
    assert bc.attack_pair(att((0.0, 1.0))) is None


# ---------------------------------------------------------------- joins, mapping, ranges

def _plan(points, transitions, tracks):
    return {"project_bpm": points[0][1], "tempo": {"points": points},
            "tracks": [{"track_instance_id": f"t{i}", "display_name": n, "arrangement_end_beat": end}
                       for i, (n, end) in enumerate(tracks)],
            "transitions": [{"out_track_instance_id": f"t{i}", "in_track_instance_id": f"t{i + 1}",
                             "overlap_start_beat": s, "overlap_end_beat": e} for i, (s, e) in enumerate(transitions)]}


def _report(transitions, tracks, names):
    return {"tracks": [{"name": n, "arr_start": a, "arr_end": b} for n, (a, b) in zip(names, tracks)],
            "transitions": [{"overlap_beats": e - s, "out_track": names[i], "in_track": names[i + 1]}
                            for i, (s, e) in enumerate(transitions)]}


NAMES = ["Alpha - One 24 Bit MASTER", "Beta &amp; Co - Two SW V1", "Gamma - Three SW V2"]
TRANS = [(200.0, 264.0), (500.0, 560.0)]
SPANS = [(0.0, 264.0), (200.0, 560.0), (500.0, 800.0)]


def test_joins_from_plan_flat_tempo_maps_beats_to_seconds():
    plan = _plan([[0.0, 120.0], [800.0, 120.0]], TRANS, [(n, b) for n, (a, b) in zip(NAMES, SPANS)])
    tempo, joins, last = bc.joins_from_plan(plan, _report(TRANS, SPANS, NAMES))
    assert last == 800.0 and len(joins) == 2
    assert (joins[0].start_s, joins[0].end_s) == (100.0, 132.0)
    assert (joins[1].start_s, joins[1].end_s) == (250.0, 280.0)
    assert joins[0].out_name == NAMES[0] and joins[1].in_name == "Gamma - Three SW V2"
    assert (joins[1].prev_end_beat, joins[1].prev_end_s) == (264.0, 132.0)
    assert (joins[0].next_start_beat, joins[0].next_start_s) == (500.0, 250.0) and joins[1].next_start_s is None
    assert joins[0].prev_end_s is None


def test_joins_from_plan_unescapes_the_names_the_report_html_escapes():
    plan = _plan([[0.0, 120.0], [800.0, 120.0]], TRANS, [(n, b) for n, (a, b) in zip(NAMES, SPANS)])
    plan["tracks"][1]["display_name"] = "Beta &amp; Co - Two SW V1"
    _t, joins, _l = bc.joins_from_plan(plan, _report(TRANS, SPANS, NAMES))
    assert joins[0].in_name == "Beta & Co - Two SW V1"


def test_joins_from_plan_tempo_ramp_integrates_per_beat():
    plan = _plan([[0.0, 120.0], [400.0, 120.0], [600.0, 140.0], [800.0, 140.0]], TRANS, [(n, b) for n, (a, b) in zip(NAMES, SPANS)])
    _tempo, joins, _l = bc.joins_from_plan(plan, _report(TRANS, SPANS, NAMES))
    assert joins[0].end_s == 132.0                                       # beat 264 is before the ramp
    # beat 500 is 100 beats into the ramp (slope 0.1 BPM per beat), so 130 BPM there: seconds = 60 / slope * ln(130 / 120)
    assert abs(joins[1].start_s - (200.0 + 600.0 * math.log(130.0 / 120.0))) < 1e-9
    assert abs(joins[1].end_s - (200.0 + 600.0 * math.log(136.0 / 120.0))) < 1e-9


def test_a_report_from_another_run_is_refused():
    plan = _plan([[0.0, 120.0], [800.0, 120.0]], TRANS, [(n, b) for n, (a, b) in zip(NAMES, SPANS)])
    other = _report([(200.0, 268.0), (500.0, 560.0)], [(0.0, 268.0), (200.0, 560.0), (500.0, 800.0)], NAMES)
    with pytest.raises(ValueError, match="not the same run"):
        bc.joins_from_plan(plan, other)
    moved = _report(TRANS, [(0.0, 264.0), (208.0, 560.0), (500.0, 800.0)], NAMES)
    with pytest.raises(ValueError, match="not the same run"):
        bc.joins_from_plan(plan, moved)
    short = _report(TRANS[:1], SPANS[:2], NAMES[:2])
    with pytest.raises(ValueError, match="not the same run"):
        bc.joins_from_plan(plan, short)


def _write_als(path, tracks, bpm=120):
    body = ""
    for name, (start, end) in tracks:
        body += (f'<AudioTrack><Name><EffectiveName Value={quoteattr(name)} /></Name><DeviceChain><MainSequencer><Sample>'
                 f'<ArrangerAutomation><Events><AudioClip><CurrentStart Value="{start}" /><CurrentEnd Value="{end}" />'
                 f'<Loop><LoopStart Value="0" /><LoopEnd Value="{end - start}" /><StartRelative Value="0" />'
                 f'<LoopOn Value="false" /></Loop><SampleRef><FileRef><Path Value="C:/x/{name}.wav" />'
                 f'<RelativePath Value="" /></FileRef></SampleRef><WarpMarkers>'
                 f'<WarpMarker SecTime="0" BeatTime="0" /><WarpMarker SecTime="1" BeatTime="2" /></WarpMarkers>'
                 f'</AudioClip></Events></ArrangerAutomation></Sample></MainSequencer></DeviceChain></AudioTrack>')
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><Ableton><LiveSet><MainTrack><DeviceChain><Mixer><Tempo>'
           f'<Manual Value="{bpm}" /></Tempo></Mixer></DeviceChain></MainTrack><Tracks>{body}</Tracks></LiveSet></Ableton>')
    with gzip.open(path, "wb") as fh:
        fh.write(xml.encode("utf-8"))


def test_joins_from_als_finds_each_overlap_and_the_seconds(tmp):
    als = tmp / "Set.als"
    _write_als(als, [("Alpha", SPANS[0]), ("Beta", SPANS[1]), ("Gamma", SPANS[2])])
    tempo, joins, last = bc.joins_from_als(als)
    assert last == 800.0 and len(joins) == 2
    assert (joins[0].start_beat, joins[0].end_beat, joins[0].start_s, joins[0].end_s) == (200.0, 264.0, 100.0, 132.0)
    assert (joins[1].out_name, joins[1].in_name) == ("Beta", "Gamma")
    assert joins[1].prev_end_s == 132.0 and joins[0].next_start_s == 250.0


def test_the_map_must_fit_the_bounce_length():
    j = [bc.Join(1, "a", "b", 100.0, 200.0, 50.0, 100.0)]
    ok = bc.check_mapping(TEMPO, 400.0, 215.0, j)                          # arrangement ends at 200 s, 15 s tail
    assert ok["ok"] and abs(ok["tail_s"] - 15.0) < 1e-9 and ok["map_end_s"] == 200.0
    short = bc.check_mapping(TEMPO, 400.0, 150.0, j)
    assert not short["ok"] and "SHORTER" in short["problems"][0]
    long = bc.check_mapping(TEMPO, 400.0, 260.0, j)
    assert not long["ok"] and "beat 0" in long["problems"][0]


def _res(**values):
    r = {"loudness": {"step_lu": values.get("step", 0.0)}, "tonal": {}, "low_end": {}, "width": {}}
    return r


def test_ranges_are_the_min_median_max_and_the_count():
    results = [_res(step=s) for s in (-1.0, 0.5, 2.0, 3.0)]
    ranges = bc.build_ranges(results)
    assert ranges["loud_step_lu"] == {"n": 4, "min": -1.0, "median": 1.25, "max": 3.0, "thin": True}


def test_judge_says_inside_or_outside_by_how_much():
    ranges = {"loud_step_lu": {"n": 4, "min": -1.0, "median": 1.25, "max": 3.0}}
    assert bc.judge(_res(step=0.0), ranges)["loud_step_lu"]["verdict"] == "inside"
    assert bc.judge(_res(step=-1.0), ranges)["loud_step_lu"]["verdict"] == "inside"
    above = bc.judge(_res(step=4.5), ranges)["loud_step_lu"]
    assert above["verdict"] == "outside" and abs(above["by"] - 1.5) < 1e-12 and not above["below"]
    below = bc.judge(_res(step=-2.5), ranges)["loud_step_lu"]
    assert below["verdict"] == "outside" and abs(below["by"] - 1.5) < 1e-12 and below["below"]
    text = bc.verdict_text(bc.judge(_res(step=4.5), ranges), ranges)
    assert text.startswith("outside: loud_step_lu above by 1.50") and "n down to 4" in text
    assert bc.verdict_text(bc.judge(_res(step=0.0), ranges), ranges).startswith("inside the observed range")
    assert bc.judge(_res(step=0.0), {}) == {}


def test_measures_with_one_concerning_end_are_judged_on_that_end_only():
    ranges = {"flam_fraction_of_fitted_beats": {"n": 5, "min": 0.01, "median": 0.03, "max": 0.35},
              "tonal_worst_abs_db": {"n": 5, "min": 1.0, "median": 5.0, "max": 16.0},
              "low_corr_overlap": {"n": 5, "min": 0.96, "median": 1.0, "max": 1.0},
              "low_corr_drop": {"n": 5, "min": -0.01, "median": 0.0, "max": 0.07}}

    def res(flam=None, worst=None, corr=None, drop=None):
        r = {"loudness": {}, "tonal": {} if worst is None else {"worst_step_db": worst}, "low_end": {}, "width": {}}
        if corr is not None:
            r["low_end"]["corr"] = {"during": corr}
        if drop is not None:
            r["low_end"]["corr_drop"] = drop
        if flam is not None:
            r["flam"] = {"state": "no_flam_seen", "fraction_of_fit_beats": flam}
        return r

    inside = bc.judge(res(flam=0.0, worst=-0.5, corr=1.0, drop=0.2), ranges)
    assert {k: v["verdict"] for k, v in inside.items()} == {n: "inside" for n in ranges}, inside
    out = bc.judge(res(flam=0.4, worst=20.0, corr=0.9, drop=-0.1), ranges)
    assert {k: v["verdict"] for k, v in out.items()} == {n: "outside" for n in ranges}, out
    assert abs(out["flam_fraction_of_fitted_beats"]["by"] - 0.05) < 1e-12 and abs(out["tonal_worst_abs_db"]["by"] - 4.0) < 1e-12
    assert abs(out["low_corr_overlap"]["by"] - 0.06) < 1e-12 and abs(out["low_corr_drop"]["by"] - 0.09) < 1e-12


def test_the_flam_fraction_is_over_the_fitted_beats_and_carries_the_count():
    r = {"loudness": {}, "tonal": {}, "low_end": {}, "width": {},
         "flam": {"state": "flam_seen", "n_kick_beats": 40, "n_fit_both": 12, "n_flam": 4, "fraction_of_fit_beats": 4 / 12}}
    assert bc.scalars(r) == {"flam_fraction_of_fitted_beats": 4 / 12}                 # not 4 / 40


def test_a_real_flam_result_carries_the_fitted_count(flam_runs):
    r = flam_runs["late8"]
    assert r["n_fit_both"] >= bc.MIN_FIT_BEATS and abs(r["fraction_of_fit_beats"] - r["n_flam"] / r["n_fit_both"]) < 1e-12
    assert "fraction_of_kick_beats" not in r
    line = bc.summary_line({"join": {"index": 1, "out": "A - x", "in": "B - y", "start_s": 70.0, "end_s": 110.0}, "loudness": {}, "tonal": {},
                            "low_end": {}, "width": {}, "flam": r})
    assert f"fitted beats ({r['n_kick_beats']} kick beats)" in line, line


def test_judged_output_shows_the_median_and_the_range():
    ranges = {"loud_step_lu": {"n": 8, "min": -1.0, "median": 0.25, "max": 3.0, "thin": False}}
    j = bc.judge(_res(step=4.5), ranges)["loud_step_lu"]
    assert (j["min"], j["median"], j["max"]) == (-1.0, 0.25, 3.0)
    text = bc.verdict_text({"loud_step_lu": j}, ranges)
    assert "median 0.25" in text and "min -1.00" in text and "max 3.00" in text, text


def test_the_n_in_the_summary_uses_only_the_measurements_judged():
    ranges = {"loud_step_lu": {"n": 50, "min": -1.0, "median": 0.0, "max": 1.0, "thin": False},
              "flam_fraction_of_fitted_beats": {"n": 2, "min": 0.0, "median": 0.0, "max": 0.0, "thin": True}}
    text = bc.verdict_text({"loud_step_lu": {"value": 0.0, "verdict": "inside", "by": 0.0}}, ranges)
    assert "n down to 50" in text and "THIN" not in text, text


def test_a_range_from_under_five_joins_says_it_is_thin():
    r3 = bc.build_ranges([_res(step=s) for s in (0.0, 1.0, 2.0)])
    r5 = bc.build_ranges([_res(step=s) for s in range(5)])
    assert r3["loud_step_lu"]["thin"] is True and r5["loud_step_lu"]["thin"] is False
    text = bc.verdict_text(bc.judge(_res(step=1.0), r3), r3)
    assert "THIN" in text and "loud_step_lu 3 joins" in text, text


def test_flam_enters_the_ranges_only_when_it_could_be_judged():
    seen = {"loudness": {}, "tonal": {}, "low_end": {}, "width": {}, "flam": {"state": "flam_seen", "fraction_of_fit_beats": 0.09}}
    unknown = {"loudness": {}, "tonal": {}, "low_end": {}, "width": {}, "flam": {"state": "not_measurable", "fraction_of_fit_beats": 0.0}}
    assert bc.scalars(seen) == {"flam_fraction_of_fitted_beats": 0.09}
    assert bc.scalars(unknown) == {}


# ---------------------------------------------------------------- the CLI for real

def _cli(*args):
    return subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, timeout=600)


def test_cli_one_line_per_join_a_json_and_a_picture_each(tmp):
    d = tmp / "cli"
    d.mkdir()
    plan = _plan([[0.0, 120.0], [200.0, 120.0]], [(100.0, 160.0)], [("Alpha - One", 160.0), ("Beta - Two", 220.0)])
    report = _report([(100.0, 160.0)], [(0.0, 160.0), (100.0, 220.0)], ["Alpha - One", "Beta - Two"])
    (d / "MIX_PLAN.json").write_text(json.dumps(plan))
    (d / "ARRANGEMENT_REPORT.json").write_text(json.dumps(report))
    build(d / "Mix.wav", (sine(1000, 40, -20), None), (sine(1000, 30, -17), None), (sine(1000, 30, -20), None))
    proc = _cli(d / "Mix.wav", "--no-flam", "--out", d / "out", "--ranges", d / "none.json")
    assert proc.returncode == 0, proc.stderr
    lines = [l for l in proc.stdout.splitlines() if l.startswith("J01")]
    assert len(lines) == 1 and "loud step" in lines[0] and "flam not run" in lines[0], proc.stdout
    data = json.loads((d / "out" / "bounce_check.json").read_text())
    assert len(data["joins"]) == 1 and data["mapping"]["ok"] and data["peak_memory_mb"]
    assert (d / "out" / "join_01.png").stat().st_size > 5000
    assert "\u2014" not in proc.stdout


def test_cli_says_when_the_map_does_not_fit_the_bounce(tmp):
    d = tmp / "cli_bad"
    d.mkdir()
    plan = _plan([[0.0, 120.0], [400.0, 120.0]], [(100.0, 160.0)], [("Alpha - One", 160.0), ("Beta - Two", 400.0)])
    report = _report([(100.0, 160.0)], [(0.0, 160.0), (100.0, 400.0)], ["Alpha - One", "Beta - Two"])
    (d / "MIX_PLAN.json").write_text(json.dumps(plan))
    (d / "ARRANGEMENT_REPORT.json").write_text(json.dumps(report))
    build(d / "Mix.wav", (sine(1000, 40, -20), None), (sine(1000, 30, -20), None), (sine(1000, 30, -20), None))
    proc = _cli(d / "Mix.wav", "--no-flam", "--out", d / "out", "--ranges", d / "none.json")
    assert proc.returncode == 3, (proc.returncode, proc.stdout, proc.stderr)
    assert "MAP PROBLEM" in proc.stdout and "SHORTER" in proc.stdout


def test_cli_reports_a_missing_plan_on_stderr_and_exits_2(tmp):
    d = tmp / "cli_none"
    d.mkdir()
    build(d / "Mix.wav", (sine(1000, 40, -20), None), (sine(1000, 30, -20), None), (sine(1000, 30, -20), None))
    proc = _cli(d / "Mix.wav", "--no-flam")
    assert proc.returncode == 2 and "could not run" in proc.stderr and "--als" in proc.stderr, (proc.stdout, proc.stderr)


def test_cli_with_flam_on_a_known_double(tmp):
    d = tmp / "cli_flam"
    d.mkdir()
    kick_bounce(d / "Mix.wav", 8.0)
    plan = _plan([[0.0, 120.0], [400.0, 120.0]], [(140.0, 220.0)], [("Out - A", 220.0), ("In - B", 400.0)])
    report = _report([(140.0, 220.0)], [(0.0, 220.0), (140.0, 400.0)], ["Out - A", "In - B"])
    (d / "MIX_PLAN.json").write_text(json.dumps(plan))
    (d / "ARRANGEMENT_REPORT.json").write_text(json.dumps(report))
    proc = _cli(d / "Mix.wav", "--out", d / "out", "--ranges", d / "none.json")
    assert proc.returncode == 0, proc.stderr
    line = next(l for l in proc.stdout.splitlines() if l.startswith("J01"))
    assert "flam SEEN" in line and "ms" in line, line


def test_the_calibrate_mode_writes_the_ranges_from_the_accepted_group(tmp):
    d = tmp / "cal"
    d.mkdir()
    for name, db in (("A", -20), ("B", -17)):
        plan = _plan([[0.0, 120.0], [200.0, 120.0]], [(100.0, 160.0)], [("Alpha - One", 160.0), ("Beta - Two", 220.0)])
        report = _report([(100.0, 160.0)], [(0.0, 160.0), (100.0, 220.0)], ["Alpha - One", "Beta - Two"])
        (d / f"plan_{name}.json").write_text(json.dumps(plan))
        (d / f"report_{name}.json").write_text(json.dumps(report))
        build(d / f"{name}.wav", (sine(1000, 40, -20), None), (sine(1000, 30, db), None), (sine(1000, 30, -20), None))
    manifest = {"basis": "synthetic", "entries": [
        {"label": "A", "bounce": "A.wav", "plan": "plan_A.json", "report": "report_A.json", "group": "accepted"},
        {"label": "B", "bounce": "B.wav", "plan": "plan_B.json", "report": "report_B.json", "group": "other"}]}
    (d / "manifest.json").write_text(json.dumps(manifest))
    proc = _cli("--calibrate", d / "manifest.json", "--corpus-root", d, "--out", d / "out", "--no-flam")
    assert proc.returncode == 0, proc.stderr
    ranges = json.loads((d / "out" / "ranges.json").read_text())
    assert ranges["groups"]["accepted"]["n_joins"] == 1 and ranges["groups"]["all"]["n_joins"] == 2
    acc = ranges["groups"]["accepted"]["measurements"]["loud_peak_vs_sides_lu"]
    assert abs(acc["min"]) < 0.2 and acc["n"] == 1
    allm = ranges["groups"]["all"]["measurements"]["loud_peak_vs_sides_lu"]
    assert abs(allm["max"] - 3.0) < 0.2 and allm["n"] == 2
    again = _cli("--calibrate", d / "manifest.json", "--corpus-root", d, "--out", d / "out", "--no-flam")
    assert "already measured" in again.stdout


def test_calibrate_without_an_accepted_group_is_a_clear_error(tmp):
    d = tmp / "cal_none"
    d.mkdir()
    plan = _plan([[0.0, 120.0], [200.0, 120.0]], [(100.0, 160.0)], [("Alpha - One", 160.0), ("Beta - Two", 220.0)])
    report = _report([(100.0, 160.0)], [(0.0, 160.0), (100.0, 220.0)], ["Alpha - One", "Beta - Two"])
    (d / "plan.json").write_text(json.dumps(plan))
    (d / "report.json").write_text(json.dumps(report))
    build(d / "A.wav", (sine(1000, 40, -20), None), (sine(1000, 30, -20), None), (sine(1000, 30, -20), None))
    manifest = {"basis": "synthetic", "entries": [{"label": "A", "bounce": "A.wav", "plan": "plan.json", "report": "report.json", "group": "other"}]}
    (d / "manifest.json").write_text(json.dumps(manifest))
    proc = _cli("--calibrate", d / "manifest.json", "--corpus-root", d, "--out", d / "out", "--no-flam")
    assert proc.returncode == 2 and "accepted" in proc.stderr and "KeyError" not in proc.stderr, (proc.returncode, proc.stderr)
    assert not (d / "out" / "ranges.json").exists()


def test_a_ranges_file_without_the_asked_group_is_a_clear_error(tmp):
    d = tmp / "cli_group"
    d.mkdir()
    (d / "ranges.json").write_text(json.dumps({"groups": {"other": {"measurements": {}}}}))
    build(d / "Mix.wav", (sine(1000, 40, -20), None), (sine(1000, 30, -20), None), (sine(1000, 30, -20), None))
    plan = _plan([[0.0, 120.0], [200.0, 120.0]], [(100.0, 160.0)], [("Alpha - One", 160.0), ("Beta - Two", 220.0)])
    report = _report([(100.0, 160.0)], [(0.0, 160.0), (100.0, 220.0)], ["Alpha - One", "Beta - Two"])
    (d / "MIX_PLAN.json").write_text(json.dumps(plan))
    (d / "ARRANGEMENT_REPORT.json").write_text(json.dumps(report))
    proc = _cli(d / "Mix.wav", "--no-flam", "--out", d / "out", "--ranges", d / "ranges.json")
    assert proc.returncode == 2 and "accepted" in proc.stderr and "KeyError" not in proc.stderr, (proc.returncode, proc.stderr)


# ---------------------------------------------------------------- the attention rule and the attention file

def _rng(lo, hi):
    return {"n": 10, "min": lo, "median": (lo + hi) / 2, "max": hi, "thin": False}


# every measure the rule reads, plus the ones that must never flag alone; accepted range -1 to +1 (the corr drop 0 to 0.1)
ATT_RANGES = {**{n: _rng(-1.0, 1.0) for n in ("loud_step_lu", "loud_peak_vs_sides_lu", "low_level_vs_sides_db", "width_step_db",
                                              "tonal_worst_abs_db", "tonal_step_sub_db", "tonal_step_low_db", "tonal_step_mid_db",
                                              "low_worst_bar_level_vs_sides_db", "loud_mean_vs_sides_lu")},
              "low_corr_drop": _rng(0.0, 0.1), "low_corr_overlap": _rng(0.9, 1.0), "flam_fraction_of_fitted_beats": _rng(0.0, 0.1)}


def att_res(index=1, step=0.0, peak=0.0, low=0.0, drop=0.05, width=0.0, tonal=0.0, bar=0.0, flam=None, picture="join_01.png"):
    flam = {"state": "no_flam_seen", "fraction_of_fit_beats": 0.0, "n_kick_beats": 20, "n_fit_both": 18} if flam is None else flam
    return {"join": {"index": index, "out": "Out - Song A SW V2", "in": "In - Song B SW V3", "start_s": 1527.3, "end_s": 1585.8},
            "windows": {n: {"measured_s": 20.0} for n in ("before", "during", "after")}, "silent_windows": [], "notes": [],
            "loudness": {"step_lu": step, "peak_vs_sides_lu": peak},
            "tonal": {"step_db": {"sub": tonal, "low": 0.0, "mid": 0.0}, "worst_band": "sub", "worst_step_db": tonal},
            "low_end": {"level_vs_sides_db": low, "corr": {"during": 1.0}, "corr_drop": drop, "worst_bar": {"level_vs_sides_db": bar}},
            "width": {"step_db": width}, "flam": flam, "picture": picture}


def att_report(*joins, problems=(), warnings=()):
    return {"joins": list(joins), "mapping": {"ok": not problems, "problems": list(problems), "start": {"warnings": list(warnings)}}}


def flagged_of(res):
    return [f["join"] for f in bc.attention(att_report(res), ATT_RANGES)["flagged"]]


@pytest.mark.parametrize("measure, edge, under", [("step", 1.0, 0.99), ("peak", 1.0, 0.99), ("low", 3.0, 2.9), ("width", 3.0, 2.9)])
@pytest.mark.parametrize("sign", [1, -1])
def test_each_attention_measure_flags_at_its_margin_and_not_under_it(measure, edge, under, sign):
    # the accepted range is -1 to +1, so a value of sign * (1 + margin) is outside by exactly the margin
    assert flagged_of(att_res(**{measure: sign * (1.0 + edge)})) == [1]
    assert flagged_of(att_res(**{measure: sign * (1.0 + under)})) == []
    assert flagged_of(att_res(**{measure: sign * 1.0})) == []


def test_the_low_end_correlation_fall_flags_at_0_05_below_the_range_and_a_rise_never_does():
    assert flagged_of(att_res(drop=-0.05)) == [1]              # range min 0.0: outside by 0.05
    assert flagged_of(att_res(drop=-0.049)) == []
    assert flagged_of(att_res(drop=0.9)) == []                 # a rise is not the concerning end


def test_a_join_outside_the_range_by_less_than_the_margin_in_every_measure_does_not_flag():
    assert flagged_of(att_res(step=1.9, peak=-1.9, low=3.9, width=3.9, drop=-0.04)) == []


def test_tonal_steps_and_worst_bar_never_flag_alone():
    res = att_res(tonal=40.0, bar=-40.0)
    judged = bc.judge(res, ATT_RANGES)
    assert judged["tonal_worst_abs_db"]["verdict"] == "outside" and judged["low_worst_bar_level_vs_sides_db"]["verdict"] == "outside"
    assert flagged_of(res) == []                               # outside the range by far, and still not an attention measure
    res["tonal"]["step_db"].update({"low": -30.0, "mid": 30.0})
    assert flagged_of(res) == []


@pytest.mark.parametrize("state", ["too_few_beats", "not_measurable", "no_flam_seen", "not_run"])
def test_a_flam_that_was_not_seen_does_not_flag(state):
    flam = {"state": state, "fraction_of_fit_beats": 0.5, "n_kick_beats": 20, "n_fit_both": 18, "n_flam": 9}
    assert flagged_of(att_res(flam=flam)) == []


def test_a_flam_seen_flags_with_or_without_ranges():
    flam = {"state": "flam_seen", "fraction_of_fit_beats": 0.5, "n_kick_beats": 20, "n_fit_both": 18, "n_flam": 9, "median_spacing_ms": 7.2}
    assert flagged_of(att_res(flam=flam)) == [1]
    att = bc.attention(att_report(att_res(flam=flam)), None)
    assert [f["join"] for f in att["flagged"]] == [1] and "kick flam SEEN on 9/18 fitted beats, median 7.2 ms" in att["text"]


def test_the_attention_line_names_the_join_the_measure_the_range_and_the_picture():
    res = att_res(index=6, step=5.4)
    res["join"].update({"out": "POP - Pop 24 Bit MASTER", "in": "Lift Off 16 Bit MASTER"})
    ranges = {**ATT_RANGES, "loud_step_lu": _rng(-1.0, 3.4)}
    att = bc.attention(att_report(res), ranges, "pics")
    assert att["text"].splitlines()[-1] == ("NEEDS A LOOK: join 6 Pop > Lift Off (25:27.3-26:25.8): loud step +5.4 LU, outside Sam's accepted "
                                            f"range (max +3.4) by 2.0; picture: {Path('pics') / 'join_01.png'}"), att["text"]
    below = bc.attention(att_report(att_res(step=-5.0)), ranges, None)
    assert "outside Sam's accepted range (min -1.0) by 4.0" in below["text"] and "picture" not in below["text"]


def test_one_block_per_flagged_join_and_the_clean_ones_are_not_named():
    att = bc.attention(att_report(att_res(index=1, step=5.0), att_res(index=2), att_res(index=3, width=-9.0)), ATT_RANGES)
    assert [l.split(":")[0] for l in att["text"].splitlines() if l.startswith("NEEDS A LOOK")] == ["NEEDS A LOOK"] * 2
    assert [f["join"] for f in att["flagged"]] == [1, 3] and "join 2" not in att["text"]
    assert bc.attention_summary(att, "ATTENTION.md") == "BOUNCE CHECK: 2 of 3 joins need a look (J01, J03): see ATTENTION.md"


def test_nothing_flagged_is_one_line_with_the_join_count():
    att = bc.attention(att_report(att_res(index=1), att_res(index=2)), ATT_RANGES)
    assert att["text"] == "no join needs a look (2 joins checked)\n" and att["flagged"] == [] and att["caveats"] == []
    assert bc.attention_summary(att, "x/ATTENTION.md") == "BOUNCE CHECK: no join needs a look"


def test_a_join_that_was_not_measured_is_written_and_cannot_pass_as_clean():
    silent = att_res(index=2)
    silent["silent_windows"], silent["windows"]["before"]["measured_s"] = ["before"], 0.0
    silent["notes"] = ["before window silent: not measured"]
    none = {"join": {"index": 3, "out": "A - x", "in": "B - y", "start_s": 10.0, "end_s": 70.0}, "notes": ["no audio for this join in the bounce"]}
    att = bc.attention(att_report(att_res(index=1), silent, none), ATT_RANGES)
    assert att["flagged"] == [] and [c["kind"] for c in att["caveats"]] == ["NOT MEASURED", "NOT MEASURED"]
    assert "NOT MEASURED: join 2 Song A > Song B" in att["text"] and "before window silent: not measured" in att["text"]
    assert "NOT MEASURED: join 3 x > y" in att["text"] and "no audio for this join" in att["text"]
    assert att["text"].splitlines()[-1].startswith("no join needs a look (3 joins checked), but see the warnings above")
    assert bc.attention_summary(att, "p").endswith("; WARNINGS (NOT MEASURED): see p")


def test_a_start_warning_and_a_map_that_does_not_fit_are_written():
    att = bc.attention(att_report(att_res(), problems=["bounce is 40.0 s SHORTER than the arrangement"],
                                  warnings=["the bounce starts with 4.0 s of silence"]), ATT_RANGES)
    assert "bounce does not fit the arrangement: bounce is 40.0 s SHORTER than the arrangement" in att["text"]
    assert "START WARNING: the bounce starts with 4.0 s of silence" in att["text"]
    assert bc.attention_summary(att, "p") == "BOUNCE CHECK: no join needs a look; WARNINGS (MAP, START WARNING): see p"


def test_no_ranges_and_no_flam_run_are_written_as_not_judged_and_not_run():
    att = bc.attention(att_report(att_res(flam={"state": "not_run", "notes": []})), None)
    assert [c["kind"] for c in att["caveats"]] == ["NOT JUDGED", "NOT RUN"]
    assert att["flagged"] == [] and att["text"].startswith("NOT JUDGED: no accepted ranges were loaded")


def test_the_attention_file_is_ascii_even_for_a_track_name_with_a_dash_or_an_accent(tmp):
    res = att_res(step=9.0)
    res["join"]["out"] = "Beyonc\u00e9 \u2014 Break My Soul SW V1"
    path = tmp / "ascii_probe.md"
    bc.write_attention(bc.attention(att_report(res), ATT_RANGES), path)
    data = path.read_bytes()
    assert data.isascii() and b"Beyonce - Break My Soul" in data


# ---------------------------------------------------------------- the CLI writes the attention file and ends on one summary line

def _cli_case(tmp, name, mid_db, ranges=True, plan_end=220.0):
    d = tmp / name
    d.mkdir()
    plan = _plan([[0.0, 120.0], [200.0, 120.0]], [(100.0, 160.0)], [("Alpha - One", 160.0), ("Beta - Two", plan_end)])
    report = _report([(100.0, 160.0)], [(0.0, 160.0), (100.0, plan_end)], ["Alpha - One", "Beta - Two"])
    (d / "MIX_PLAN.json").write_text(json.dumps(plan))
    (d / "ARRANGEMENT_REPORT.json").write_text(json.dumps(report))
    build(d / "Mix.wav", (sine(1000, 40, -20), None), (sine(1000, 30, mid_db), None), (sine(1000, 30, -20), None))
    if ranges:
        (d / "ranges.json").write_text(json.dumps({"groups": {"accepted": {"measurements": {
            n: _rng(-1.0, 1.0) for n in ("loud_step_lu", "loud_peak_vs_sides_lu", "low_level_vs_sides_db", "width_step_db")}}}}))
    return d


def test_cli_exits_0_on_a_flagged_join_and_writes_the_attention_file_and_the_last_line(tmp):
    d = _cli_case(tmp, "cli_att_flag", -10.0)          # blend 10 dB over both sides: the blend peak is outside by far
    proc = _cli(d / "Mix.wav", "--no-flam", "--out", d / "out", "--ranges", d / "ranges.json")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    text = (d / "out" / "ATTENTION.md").read_text()
    assert "NEEDS A LOOK: join 1 One > Two (" in text and "blend peak vs sides +10.0 LU" in text and "picture: " in text, text
    assert (d / "out" / "join_01.png").name in text
    last = proc.stdout.strip().splitlines()[-1]
    assert last.startswith("BOUNCE CHECK: 1 of 1 joins need a look (J01)") and last.endswith(str(d / "out" / "ATTENTION.md")), last
    data = json.loads((d / "out" / "bounce_check.json").read_text())
    assert [f["join"] for f in data["attention"]["flagged"]] == [1] and data["attention"]["text"] == text
    assert "\u2014" not in proc.stdout and "\u2014" not in text


def test_cli_writes_the_attention_file_when_nothing_flags_too(tmp):
    d = _cli_case(tmp, "cli_att_clean", -20.0)
    proc = _cli(d / "Mix.wav", "--out", d / "out", "--ranges", d / "ranges.json", "--no-flam")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    text = (d / "out" / "ATTENTION.md").read_text()
    assert text.splitlines()[-1].startswith("no join needs a look (1 joins checked), but see the warnings above")
    assert "NOT RUN: kick flam was skipped" in text
    last = proc.stdout.strip().splitlines()[-1]
    assert last.startswith("BOUNCE CHECK: no join needs a look; WARNINGS (") and "NOT RUN" in last and last.endswith("ATTENTION.md"), last


def test_cli_with_no_warnings_writes_the_single_clean_line_and_ends_on_it(tmp):
    d = tmp / "cli_att_one_line"
    d.mkdir()
    kick_bounce(d / "Mix.wav", 0.0)
    plan = _plan([[0.0, 120.0], [400.0, 120.0]], [(140.0, 220.0)], [("Out - A", 220.0), ("In - B", 400.0)])
    report = _report([(140.0, 220.0)], [(0.0, 220.0), (140.0, 400.0)], ["Out - A", "In - B"])
    (d / "MIX_PLAN.json").write_text(json.dumps(plan))
    (d / "ARRANGEMENT_REPORT.json").write_text(json.dumps(report))
    (d / "ranges.json").write_text(json.dumps({"groups": {"accepted": {"measurements": {"loud_step_lu": _rng(-30.0, 30.0)}}}}))
    proc = _cli(d / "Mix.wav", "--out", d / "out", "--ranges", d / "ranges.json")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert (d / "out" / "ATTENTION.md").read_text() == "no join needs a look (1 joins checked)\n", (d / "out" / "ATTENTION.md").read_text()
    assert proc.stdout.strip().splitlines()[-1] == "BOUNCE CHECK: no join needs a look"


def test_cli_writes_the_attention_file_for_a_start_warning(tmp):
    d = tmp / "cli_att_start"
    d.mkdir()
    (d / "Mix.wav").write_bytes(_shifted_kick_bounce(tmp, "cli_att_start_src", 4.0).read_bytes())
    plan = _plan([[0.0, 120.0], [400.0, 120.0]], [(140.0, 220.0)], [("Out - A", 220.0), ("In - B", 400.0)])
    report = _report([(140.0, 220.0)], [(0.0, 220.0), (140.0, 400.0)], ["Out - A", "In - B"])
    (d / "MIX_PLAN.json").write_text(json.dumps(plan))
    (d / "ARRANGEMENT_REPORT.json").write_text(json.dumps(report))
    proc = _cli(d / "Mix.wav", "--no-flam", "--out", d / "out", "--ranges", d / "none.json")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "START WARNING: the bounce starts with" in (d / "out" / "ATTENTION.md").read_text()
    assert "WARNINGS (" in proc.stdout.strip().splitlines()[-1] and "START WARNING" in proc.stdout.strip().splitlines()[-1]


def test_cli_writes_the_attention_file_when_the_map_does_not_fit_and_still_exits_3(tmp):
    d = _cli_case(tmp, "cli_att_badmap", -20.0, plan_end=400.0)
    proc = _cli(d / "Mix.wav", "--no-flam", "--out", d / "out", "--ranges", d / "ranges.json")
    assert proc.returncode == 3, (proc.stdout, proc.stderr)
    assert "bounce does not fit the arrangement" in (d / "out" / "ATTENTION.md").read_text()
    assert proc.stdout.strip().splitlines()[-1].startswith("BOUNCE CHECK: ") and "MAP" in proc.stdout.strip().splitlines()[-1]


def test_cli_removes_an_old_attention_file_so_a_check_that_could_not_run_leaves_none(tmp):
    d = tmp / "cli_att_stale"
    d.mkdir()
    (d / "out").mkdir()
    (d / "out" / "ATTENTION.md").write_text("no join needs a look (9 joins checked)\n")
    build(d / "Mix.wav", (sine(1000, 40, -20), None), (sine(1000, 30, -20), None), (sine(1000, 30, -20), None))
    proc = _cli(d / "Mix.wav", "--no-flam", "--out", d / "out")           # no plan beside the bounce: could not run
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert not (d / "out" / "ATTENTION.md").exists()
