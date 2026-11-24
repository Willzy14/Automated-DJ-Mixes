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
    assert abs(width["side_over_mid_db"]["before"] - (bc.LEVEL_FLOOR_DB + 25.0)) < 0.1, width   # a mono signal has no side: the floor over the mid


# ---------------------------------------------------------------- windows and notes

def test_a_side_window_stops_at_the_neighbouring_join(tmp):
    path = build(tmp / "neigh.wav", (sine(1000, 40, -20), None), (sine(1000, 30, -20), None), (sine(1000, 30, -20), None))
    j = join()
    j.prev_end_s, j.next_start_s = S - 8.0, E + 4.0
    res = bc.measure_join(hops_of(path), j, TEMPO, DUR)
    assert abs(res["windows"]["before"]["start_s"] - (S - 8.0)) < 1e-9
    assert abs(res["windows"]["after"]["end_s"] - (E + 4.0)) < 1e-9
    assert res["windows"]["before"]["measured_s"] <= 8.0 + 1e-9
    assert not res["notes"]


def test_a_side_window_under_three_seconds_is_not_measured_and_says_so(tmp):
    path = build(tmp / "short.wav", (sine(1000, 40, -20), None), (sine(1000, 30, -20), None), (sine(1000, 30, -20), None))
    j = join()
    j.next_start_s = E + 2.0
    res = bc.measure_join(hops_of(path), j, TEMPO, DUR)
    assert any("after window" in n for n in res["notes"]), res["notes"]
    assert "step_lu" not in res["loudness"] and "step_db" not in res["width"]
    assert "worst_band" not in res["tonal"]


def test_memory_stays_far_below_the_file_size(tmp):
    n = 300 * SR
    x = np.stack([white(300, -30, 5), white(300, -30, 6)], axis=1).astype("float32")
    path = tmp / "long.wav"
    sf.write(str(path), x, SR, subtype="FLOAT")
    whole_file_mb = n * 2 * 8 / 2 ** 20
    tracemalloc.start()
    h = bc.analyse_span(path, 0.0, 300.0)
    peak = tracemalloc.get_traced_memory()[1] / 2 ** 20
    tracemalloc.stop()
    assert h.n == 1200
    assert peak < whole_file_mb / 2, (peak, whole_file_mb)


# ---------------------------------------------------------------- kick flam on the bounce

BEAT = 0.5


def _kick(kind):
    """Two different kicks: a noise click plus a carrier (3 kHz or 7 kHz) over a sine body (55 or 70 Hz)."""
    t = np.arange(int(0.35 * SR)) / SR
    click = np.random.default_rng(11 if kind == "out" else 12).standard_normal(len(t)) * np.exp(-t / 0.002) * (t < 0.012)
    if kind == "out":
        return 0.9 * click + 0.7 * np.sin(2 * np.pi * 3000 * t) * np.exp(-t / 0.003) + np.sin(2 * np.pi * 55 * t) * np.exp(-t / 0.12)
    return 0.8 * click + 0.9 * np.sin(2 * np.pi * 7000 * t + 1.0) * np.exp(-t / 0.0012) + np.sin(2 * np.pi * 70 * t) * np.exp(-t / 0.1)


def kick_bounce(path, in_offset_ms=8.0, out_beats=range(0, 220), in_beats=range(140, 400), seed=9):
    n = 400 * int(BEAT * SR)
    y = 0.002 * np.random.default_rng(seed).standard_normal(n)
    for kind, beats, off in (("out", out_beats, 0.0), ("in", in_beats, in_offset_ms)):
        k = _kick(kind)
        for b in beats:
            i = int(round((b * BEAT + off / 1000.0) * SR))
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
    assert ranges["loud_step_lu"] == {"n": 4, "min": -1.0, "median": 1.25, "max": 3.0}


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
    ranges = {"flam_fraction_of_kick_beats": {"n": 5, "min": 0.01, "median": 0.03, "max": 0.35},
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
            r["flam"] = {"state": "no_flam_seen", "fraction_of_kick_beats": flam}
        return r

    inside = bc.judge(res(flam=0.0, worst=-0.5, corr=1.0, drop=0.2), ranges)
    assert {k: v["verdict"] for k, v in inside.items()} == {n: "inside" for n in ranges}, inside
    out = bc.judge(res(flam=0.4, worst=20.0, corr=0.9, drop=-0.1), ranges)
    assert {k: v["verdict"] for k, v in out.items()} == {n: "outside" for n in ranges}, out
    assert abs(out["flam_fraction_of_kick_beats"]["by"] - 0.05) < 1e-12 and abs(out["tonal_worst_abs_db"]["by"] - 4.0) < 1e-12
    assert abs(out["low_corr_overlap"]["by"] - 0.06) < 1e-12 and abs(out["low_corr_drop"]["by"] - 0.09) < 1e-12


def test_flam_enters_the_ranges_only_when_it_could_be_judged():
    seen = {"loudness": {}, "tonal": {}, "low_end": {}, "width": {}, "flam": {"state": "flam_seen", "fraction_of_kick_beats": 0.09}}
    unknown = {"loudness": {}, "tonal": {}, "low_end": {}, "width": {}, "flam": {"state": "not_measurable", "fraction_of_kick_beats": 0.0}}
    assert bc.scalars(seen) == {"flam_fraction_of_kick_beats": 0.09}
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
