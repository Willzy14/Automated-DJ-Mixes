"""Grid phase fix: synthetic kicks with a known delay, a tiny pretty-printed ALS, the library and the CLI run for real."""
import gzip
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import quoteattr

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Source"))
import grid_phase_fix as gpf  # noqa: E402
import kick_alignment_check as kac  # noqa: E402
import validate_als  # noqa: E402

CLI = ROOT / "Tools" / "fix_grid_phase.py"
SR = 44100
BEAT_S = 0.5                                   # 120 BPM
WAV_BEATS = 100                                # 50 s
CLIPS = {"A": (0, 60), "B": (40, 100), "C": (80, 140), "D": (120, 180)}   # arrangement start and end; source beat = arrangement - start
NAMES = {"A": "Out - Alpha SW V1", "B": "Mid - Bravo SW V1", "C": "In - Charlie SW V1", "D": "Fin - Delta SW V1"}
CRLF = "\r\n"
LEFT = 2.0                                     # EPS_MS the solve leaves, plus what a re-measure can differ by


# ---------------------------------------------------------------- fixtures: kicks, wavs, a pretty-printed ALS

def _click(t):
    return np.sin(2 * np.pi * 4000 * t) * np.exp(-t / 0.0015) * (t < 0.008)


def _body(t):
    return np.sin(2 * np.pi * 60 * t) * (1 - np.exp(-t / 0.0015)) * np.exp(-t / 0.12)


def write_wav(path, delay_ms=0.0, kick_beats=range(WAV_BEATS), beats=WAV_BEATS, claps=(), clap_delay_ms=6.0):
    """A sharp kick on each of kick_beats, delay_ms late (one figure, or a function of the source beat). claps: beats
    that carry a clap instead: the same sharp click but only 0.2 of the low body, clap_delay_ms late."""
    rng = np.random.default_rng(7)
    y = 0.001 * rng.standard_normal(beats * int(BEAT_S * SR))
    t = np.arange(int(0.4 * SR)) / SR
    k = 0.8 * _click(t) + _body(t)
    c = 0.8 * _click(t) + 0.2 * _body(t)
    for hits, shape, late in ((kick_beats, k, delay_ms), (claps, c, clap_delay_ms)):
        for b in hits:
            d = late(b) if callable(late) else late
            i = int(round((b * BEAT_S + d / 1000.0) * SR))
            if i < 0:                       # an early kick on the very first beat would start before the file
                continue
            y[i:i + len(shape)] += shape[:len(y) - i]
    sf.write(str(path), y.astype("float32"), SR, subtype="FLOAT")


def als_text(tracks, saved_marker=False):
    """tracks: list of (track name, wav name, start, end[, options]). One element per line, tabs, CRLF, as Live writes it.
    options: loop_start, loop_on, beats (length of the wav in beats), markers (the beats that carry a warp marker),
    or empty=True for an audio track with no clips."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<Ableton>", "\t<LiveSet>", "\t\t<Tracks>"]
    for n, entry in enumerate(tracks):
        name, wav, start, end = entry[:4]
        opt = entry[4] if len(entry) > 4 else {}
        loop_start, beats = opt.get("loop_start", 0), opt.get("beats", WAV_BEATS)
        lines += [f'\t\t\t<AudioTrack Id="{n}">', "\t\t\t\t<Name>", f"\t\t\t\t\t<EffectiveName Value={quoteattr(name)} />",
                  "\t\t\t\t</Name>", "\t\t\t\t<DeviceChain>", "\t\t\t\t\t<MainSequencer>", "\t\t\t\t\t\t<Sample>",
                  "\t\t\t\t\t\t\t<ArrangerAutomation>", "\t\t\t\t\t\t\t\t<Events>"]
        if opt.get("empty"):
            lines += ["\t\t\t\t\t\t\t\t</Events>", "\t\t\t\t\t\t\t</ArrangerAutomation>", "\t\t\t\t\t\t</Sample>",
                      "\t\t\t\t\t</MainSequencer>", "\t\t\t\t</DeviceChain>", "\t\t\t</AudioTrack>"]
            continue
        lines += [f'\t\t\t\t\t\t\t\t\t<AudioClip Id="{n}" Time="{start}">',
                  f'\t\t\t\t\t\t\t\t\t\t<CurrentStart Value="{start}" />', f'\t\t\t\t\t\t\t\t\t\t<CurrentEnd Value="{end}" />',
                  '\t\t\t\t\t\t\t\t\t\t<IsWarped Value="true" />', "\t\t\t\t\t\t\t\t\t\t<Loop>",
                  f'\t\t\t\t\t\t\t\t\t\t\t<LoopStart Value="{loop_start}" />',
                  f'\t\t\t\t\t\t\t\t\t\t\t<LoopEnd Value="{loop_start + end - start}" />',
                  '\t\t\t\t\t\t\t\t\t\t\t<StartRelative Value="0" />',
                  f'\t\t\t\t\t\t\t\t\t\t\t<LoopOn Value="{str(opt.get("loop_on", False)).lower()}" />',
                  "\t\t\t\t\t\t\t\t\t\t</Loop>", "\t\t\t\t\t\t\t\t\t\t<SampleRef>", "\t\t\t\t\t\t\t\t\t\t\t<FileRef>",
                  '\t\t\t\t\t\t\t\t\t\t\t\t<RelativePath Value="" />',
                  f'\t\t\t\t\t\t\t\t\t\t\t\t<Path Value={quoteattr("C:/Not Here/" + wav)} />',
                  "\t\t\t\t\t\t\t\t\t\t\t</FileRef>", "\t\t\t\t\t\t\t\t\t\t</SampleRef>", "\t\t\t\t\t\t\t\t\t\t<WarpMarkers>"]
        lines += [f'\t\t\t\t\t\t\t\t\t\t\t<WarpMarker Id="{1000 * n + i}" SecTime="{i * BEAT_S!r}" BeatTime="{i}" />'
                  for i in opt.get("markers", range(beats + 1))]
        lines.append("\t\t\t\t\t\t\t\t\t\t</WarpMarkers>")
        if saved_marker:
            lines += ["\t\t\t\t\t\t\t\t\t\t<SavedWarpMarkersForStretched>",
                      f'\t\t\t\t\t\t\t\t\t\t\t<WarpMarker Id="{900 + n}" SecTime="1.5" BeatTime="3" />',
                      "\t\t\t\t\t\t\t\t\t\t</SavedWarpMarkersForStretched>"]
        lines += ["\t\t\t\t\t\t\t\t\t</AudioClip>", "\t\t\t\t\t\t\t\t</Events>", "\t\t\t\t\t\t\t</ArrangerAutomation>",
                  "\t\t\t\t\t\t</Sample>", "\t\t\t\t\t</MainSequencer>", "\t\t\t\t</DeviceChain>", "\t\t\t</AudioTrack>"]
    lines += ["\t\t</Tracks>", "\t</LiveSet>", "</Ableton>"]
    return CRLF.join(lines) + CRLF


def make_mix(root, name, delays, kick_beats=None, doc_order="ABC", saved_marker=False, markers=None, empties=()):
    """delays: track letter -> delay in ms (figure or function of the source beat); only those letters exist.
    markers: letter -> the beats that carry a warp marker. empties: positions (in the file) of clipless audio tracks."""
    folder = Path(root) / name
    audio = folder / "Audio"
    audio.mkdir(parents=True)
    kick_beats = kick_beats or {}
    for letter, delay in delays.items():
        write_wav(audio / (NAMES[letter] + ".wav"), delay, kick_beats.get(letter, range(WAV_BEATS)))
    order = [c for c in doc_order if c in delays]
    entries = [(NAMES[c], NAMES[c] + ".wav", *CLIPS[c]) + (({"markers": markers[c]},) if markers and c in markers else ())
               for c in order]
    for position in sorted(empties):
        entries.insert(position, ("Empty track %d" % position, None, 0, 0, {"empty": True}))
    als = folder / "Set.als"
    with gzip.open(als, "wb") as fh:
        fh.write(als_text(entries, saved_marker).encode("utf-8"))
    return als, audio


def text_of(path):
    return gzip.open(path, "rb").read().decode("utf-8")


def click_lags(als, audio):
    return [r.click["median"] for r in kac.check_als(als, audio)]


@pytest.fixture(scope="module")
def sets(tmp_path_factory):
    root = tmp_path_factory.mktemp("grid_phase")
    return {
        # B and C 8 ms late: B is fixed by its own drift fit, C then needs one slide
        "late": make_mix(root, "late", {"A": 0.0, "B": 8.0, "C": 8.0}, doc_order="BCA"),
        # B has too few kicks to be fitted, so only a slide can fix its +8 ms
        "slide": make_mix(root, "slide", {"A": 0.0, "B": 8.0}, kick_beats={"B": range(0, 30)}),
        # B drifts from 0 to 10.6 ms over its source beats: no constant slide can fix both of its blends
        "drift": make_mix(root, "drift", {"A": 0.0, "B": lambda b: 0.18 * b, "C": 0.0}, doc_order="CAB"),
        "good": make_mix(root, "good", {"A": 0.0, "B": 2.0, "C": 0.0}),
        "plain": make_mix(root, "plain", {"A": 0.0, "B": 8.0}, saved_marker=True),
        # a drifting track in the MIDDLE: its fix alone pushes the earlier, good blend out (the reviewer's probe)
        "mid": make_mix(root, "mid", {"A": 3.3, "B": lambda b: 2.5 + 5.0 * b / 60.0, "C": 0.5}),
        "five": make_mix(root, "five", {"A": 0.0, "B": 5.0}),                 # a lag between WARN_MS and a looser gate
        # B also sits in a blend with C that the check cannot judge (C has 5 kicks), and kicks 8 ms early
        "frozen": make_mix(root, "frozen", {"A": 0.0, "B": -8.0, "C": 0.0}, kick_beats={"C": range(0, 5)}),
        # B has two warp markers (a hand re-warp) and is 8 ms late
        "sparse": make_mix(root, "sparse", {"A": 0.0, "B": 8.0}, markers={"B": [0, 100]}),
        # the first ten beats of the blend are 12 ms late and the rest 4 ms early; B has 30 kicks, so no drift fit
        "halves": make_mix(root, "halves", {"A": 0.0, "B": lambda b: 12.0 if b < 10 else -3.0}, kick_beats={"B": range(0, 30)}),
        "four": make_mix(root, "four", {"A": 0.0, "B": 8.0, "C": 8.0, "D": 8.0}, doc_order="ABCD"),
        # empty audio tracks (no clips) at file positions 0 and 2: doc_index is not the position in the arrangement
        "empties": make_mix(root, "empties", {"A": 0.0, "B": 8.0, "C": 8.0}, doc_order="BCA", empties=(0, 2)),
    }


@pytest.fixture(scope="module")
def outcomes(sets, tmp_path_factory):
    root = tmp_path_factory.mktemp("grid_phase_out")
    keys = ("late", "slide", "drift", "good", "mid", "five", "frozen", "sparse", "halves", "empties")
    done = {"bytes": {k: sets[k][0].read_bytes() for k in keys}}
    for key in keys:
        als, audio = sets[key]
        with pytest.MonkeyPatch.context() as patch:
            if key in ("drift", "mid"):      # the synthetic track is 60 beats, the real window is 97: shrink both to fit it
                patch.setattr(gpf, "MEDIAN_HALF_WINDOW", 6.0)
                patch.setattr(gpf, "SMOOTH_BEATS", 5)
            done[key] = gpf.run(als, audio, root / (key + " Phased.als"), root / (key + " report"))
    return done


# ---------------------------------------------------------------- the drift fit

def test_drift_fit_recovers_a_linear_drift_within_half_a_ms():
    rng = np.random.default_rng(3)
    offsets = [(b, -2.0 - 0.01 * b + float(rng.normal(0, 0.2))) for b in range(0, 700)]
    fit = gpf.fit_drift(offsets)
    assert fit.status == gpf.FIT
    for beat in (0, 300, 600, 699):
        assert abs(fit.at(beat) - (-2.0 - 0.01 * beat)) <= 0.5, (beat, fit.at(beat))


def test_drift_fit_recovers_a_constant_offset_and_is_flat_beyond_the_measured_beats():
    fit = gpf.fit_drift([(b, 5.0) for b in range(10, 90)])
    assert fit.status == gpf.FIT
    assert all(abs(fit.at(b) - 5.0) <= 0.5 for b in (10, 50, 89))
    assert abs(fit.at(-500) - 5.0) <= 0.5 and abs(fit.at(5000) - 5.0) <= 0.5


def test_drift_fit_correction_has_the_sign_of_the_offset():
    assert gpf.fit_drift([(b, 6.0) for b in range(60)]).at(30) > 5.0           # kicks late: a positive correction
    assert gpf.fit_drift([(b, -6.0) for b in range(60)]).at(30) < -5.0


@pytest.mark.parametrize("count, status", [(39, gpf.CANNOT_FIT), (40, gpf.FIT)])
def test_a_track_needs_forty_usable_kick_beats(count, status):
    assert gpf.fit_drift([(b, 6.0) for b in range(count)]).status == status


def test_outliers_over_15_ms_are_dropped_before_counting_and_fitting():
    wild = [(b, 25.0 if b % 2 else -25.0) for b in range(100, 160)]
    fit = gpf.fit_drift([(b, 6.0) for b in range(60)] + wild)
    assert fit.status == gpf.FIT and fit.kicks == 60 and all(abs(fit.at(b) - 6.0) < 0.1 for b in (0, 30, 59))
    assert gpf.fit_drift([(b, 6.0) for b in range(30)] + wild).status == gpf.CANNOT_FIT


def test_a_correction_over_max_drift_is_refused_and_reported():
    assert gpf.MAX_DRIFT_MS < gpf.OUTLIER_MS                  # separate cuts: the refusal is reachable
    fit = gpf.fit_drift([(b, 13.0) for b in range(60)])      # not an outlier (under 15), but further out than 12
    assert fit.status == gpf.REFUSED and "more than 12 ms" in fit.note
    assert fit.at(10) == 0.0                                  # a refused fit corrects nothing
    assert gpf.fit_drift([(b, 11.0) for b in range(60)]).status == gpf.FIT


def test_a_wobble_inside_the_noise_is_not_drift(tmp_path):
    # Taste on the real mix: a correction that never passed 1.9 ms. Applying it only added noise, so it is left alone.
    wobble = [(b, 1.8 * math.sin(b / 40.0)) for b in range(0, 400)]
    fit = gpf.fit_drift(wobble)
    assert fit.status == gpf.NO_DRIFT and "noise, not drift" in fit.note
    assert gpf.DRIFT_MIN_MS == gpf.WARN_MS


def test_a_real_drift_like_get_rights_is_fitted():
    # Get Right on the real mix: the kicks run from -2.0 ms early at the start to -6.1 ms at the end.
    fit = gpf.fit_drift([(b, -2.0 - 4.1 * b / 700.0) for b in range(0, 701)])
    assert fit.status == gpf.FIT and fit.low < -5.5 and fit.high > -2.5


def test_a_kick_free_track_is_cannot_fit(tmp_path):
    noise = tmp_path / "noise.wav"
    sf.write(str(noise), (0.001 * np.random.default_rng(1).standard_normal(WAV_BEATS * int(BEAT_S * SR))).astype("float32"), SR)
    als = tmp_path / "Set.als"
    with gzip.open(als, "wb") as fh:
        fh.write(als_text([("Out - Alpha SW V1", "noise.wav", 0, 60)]).encode("utf-8"))
    track = kac.read_als(als)[0]
    track.clips[0].audio = noise
    reader = kac.WavReader(noise)
    try:
        offsets = gpf.measure_offsets(track, {str(noise): reader})
    finally:
        reader.close()
    assert gpf.fit_drift(offsets).status == gpf.CANNOT_FIT


def test_measured_offsets_follow_a_known_linear_delay(tmp_path):
    wav = tmp_path / "Out - Alpha SW V1.wav"
    write_wav(wav, lambda b: 1.0 + 0.04 * b)
    als = tmp_path / "Set.als"
    with gzip.open(als, "wb") as fh:
        fh.write(als_text([("Out - Alpha SW V1", wav.name, 0, 60)]).encode("utf-8"))
    track = kac.read_als(als)[0]
    track.clips[0].audio = wav
    reader = kac.WavReader(wav)
    try:
        offsets = gpf.measure_offsets(track, {str(wav): reader})
    finally:
        reader.close()
    assert len(offsets) >= 55
    # the leading edge sits a constant fraction of a ms before the nominal kick time, so compare along the track
    first = offsets[0][1]
    assert max(abs((o - first) - 0.04 * b) for b, o in offsets) <= 0.25


# ---------------------------------------------------------------- the slide solve

def _old_single_stage(lags, pairs, n, movable=None):
    """The objective of 5979206: the worst lag and 0.02 per ms of slide in ONE linear program."""
    from scipy import optimize
    can = {i for p in pairs for i in p} if movable is None else set(movable)
    c = np.concatenate([np.zeros(n), np.full(n, 0.02), [1.0]])
    rows, rhs = [], []
    for lag, (a, b) in zip(lags, pairs):
        for sign in (1.0, -1.0):
            r = np.zeros(2 * n + 1)
            r[a], r[b], r[-1] = sign, -sign, -1.0
            rows.append(r)
            rhs.append(-sign * lag)
    for i in range(n):
        for sign in (1.0, -1.0):
            r = np.zeros(2 * n + 1)
            r[i], r[n + i] = sign, -1.0
            rows.append(r)
            rhs.append(0.0)
    bounds = [(-gpf.MAX_SLIDE_MS, gpf.MAX_SLIDE_MS) if i in can else (0.0, 0.0) for i in range(n)] + [(0.0, None)] * (n + 1)
    return optimize.linprog(c, A_ub=np.array(rows), b_ub=np.array(rhs), bounds=bounds, method="highs").x[:n]


def _sam_case_is_met(s):
    return 4.2 - 0.01 <= s[1] <= 5.6 and s[0] == pytest.approx(0.0, abs=0.01) and s[2] == pytest.approx(0.0, abs=0.01)


def test_sams_case_slides_only_the_middle_track_and_by_just_enough():
    s, left = gpf.solve_slides(np.array([4.1, -5.7]), [(0, 1), (1, 2)], 3)
    assert _sam_case_is_met(s), s
    assert left <= gpf.EPS_MS + 1e-6
    assert abs(4.1 + s[0] - s[1]) <= gpf.EPS_MS + 1e-6 and abs(-5.7 + s[1] - s[2]) <= gpf.EPS_MS + 1e-6


def test_the_old_single_stage_objective_fails_sams_case():
    # the test above is not vacuous: 0.02 per ms of slide against the worst lag shaves the last ms with a slide on the neighbour
    assert not _sam_case_is_met(_old_single_stage(np.array([4.1, -5.7]), [(0, 1), (1, 2)], 3))


def test_the_slides_stop_where_the_lags_are_inside_the_measurement_resolution():
    s, left = gpf.solve_slides(np.array([6.0, -6.0]), [(0, 1), (1, 2)], 3)
    assert s == pytest.approx([0.0, 6.0 - gpf.EPS_MS, 0.0], abs=0.01)      # not 6.0: the last 1.5 ms is not chased
    assert left == pytest.approx(gpf.EPS_MS, abs=1e-6)


def test_a_blend_that_was_fine_is_not_left_worse_than_it_was():
    # blend 1 has a lag of 8 to fix; blend 2 sits at 1.5 after the drift fit but was 0.3 in the input
    lags, pairs = np.array([8.0, 1.5]), [(0, 1), (1, 2)]
    loose, _ = gpf.solve_slides(lags, pairs, 3)
    held, _ = gpf.solve_slides(lags, pairs, 3, was=[8.0, 0.3])
    assert abs(lags[1] + loose[1] - loose[2]) == pytest.approx(1.5, abs=0.01)    # inside EPS_MS, so left alone
    assert abs(lags[1] + held[1] - held[2]) <= 0.3 + gpf.REFUSE_TOL_MS + 1e-6    # held to what it was, within the tolerance


def test_a_worst_lag_the_slides_cannot_reduce_below_eps_sets_the_limit():
    # lag 40 with 15 ms each way: t* = 10, so the blend is brought to 10 and no further
    s, left = gpf.solve_slides(np.array([40.0]), [(0, 1)], 2)
    assert np.abs(s).max() <= gpf.MAX_SLIDE_MS + 1e-9
    assert left == pytest.approx(40.0 - 2 * gpf.MAX_SLIDE_MS, abs=0.01)


def test_the_slide_has_the_sign_that_cancels_the_lag():
    for lag in (10.0, -10.0):
        s, left = gpf.solve_slides(np.array([lag]), [(0, 1)], 2)
        assert abs(lag + s[0] - s[1]) <= gpf.EPS_MS + 1e-6 and left <= gpf.EPS_MS + 1e-6
        assert (s[0] - s[1]) * lag < 0                             # incoming later (lag > 0): outgoing slides -, or incoming +


def test_a_tie_between_equal_total_slides_goes_to_one_track_the_earlier_one():
    s, _ = gpf.solve_slides(np.array([10.0]), [(0, 1)], 2)        # -8.5 on track 0, or +8.5 on track 1, or any split
    assert s == pytest.approx([-8.5, 0.0], abs=0.01)


@pytest.mark.parametrize("lags", [[3.9, -3.9], [2.0, -1.5, 0.3], [0.0]])
def test_a_worst_lag_under_warn_changes_nothing(lags):
    pairs = [(i, i + 1) for i in range(len(lags))]
    s, left = gpf.solve_slides(np.array(lags), pairs, len(lags) + 1)
    assert not s.any() and left == pytest.approx(max(abs(v) for v in lags))


def test_a_worst_lag_of_exactly_warn_is_fixed():
    s, left = gpf.solve_slides(np.array([gpf.WARN_MS]), [(0, 1)], 2)
    assert s.any() and abs(gpf.WARN_MS + s[0] - s[1]) <= gpf.EPS_MS + 1e-6


def test_only_movable_tracks_slide():
    s, _ = gpf.solve_slides(np.array([6.0, -6.0]), [(0, 1), (1, 2)], 3, movable={0, 1})
    assert s[2] == 0.0 and abs(s[0]) + abs(s[1]) > 1.0
    s, left = gpf.solve_slides(np.array([6.0, -6.0]), [(0, 1), (1, 2)], 3, movable={1})
    assert s[0] == 0.0 and s[2] == 0.0 and s[1] == pytest.approx(4.5, abs=0.01)


def test_a_track_in_no_blend_never_slides():
    s, _ = gpf.solve_slides(np.array([9.0]), [(1, 2)], 4)
    assert s[0] == 0.0 and s[3] == 0.0


def test_the_solve_is_deterministic():
    args = (np.array([7.3, -2.2, 5.1]), [(0, 1), (1, 2), (2, 3)], 4)
    first = gpf.solve_slides(*args)
    assert all(np.array_equal(first[0], gpf.solve_slides(*args)[0]) for _ in range(3))


def _fake_result(verdict, flags=(), median=20.0, n=30, shared=30):
    return SimpleNamespace(verdict=verdict, flags=list(flags), click=dict(median=median, n=n), shared_kicks=shared)


@pytest.mark.parametrize("fake, expect", [
    (_fake_result("FAIL"), True), (_fake_result("WARN"), True),
    (_fake_result("CANNOT_JUDGE"), False),                       # gives no constraint
    (_fake_result("FAIL", ["OFF_GRID"]), False),                 # kicks on different beats: no lag to speak of
    (_fake_result("FAIL", median=None), False),
    (_fake_result("FAIL", shared=kac.MIN_SHARED_KICKS - 1), False),
])
def test_only_judged_blends_with_a_click_lag_give_a_constraint(fake, expect):
    assert gpf.usable(fake) is expect


def test_blends_the_kick_check_judged_are_the_ones_that_give_constraints(outcomes):
    judged = outcomes["late"].before + outcomes["good"].before
    assert judged and all(gpf.usable(res) == (res.verdict != "CANNOT_JUDGE") for res in judged)


# ---------------------------------------------------------------- the ALS text patch

def _changed(before_text, after_text):
    a, b = gpf._lines(before_text), gpf._lines(after_text)
    assert len(a) == len(b)
    return [i for i in range(len(a)) if a[i] != b[i]], a, b


def test_patch_changes_only_the_warp_marker_sec_times_of_the_chosen_track(tmp_path, sets):
    als, _ = make_mix(tmp_path, "scrambled", {"A": 0.0, "B": 0.0, "C": 0.0}, doc_order="CAB", saved_marker=True)
    tracks = {t.name: t for t in kac.read_als(als)}
    out = tmp_path / "patched.als"
    before_bytes = als.read_bytes()
    changed = gpf.patch_als(als, out, {tracks[NAMES["B"]].doc_index: lambda beat: 5.0})
    assert als.read_bytes() == before_bytes                        # the input is untouched
    assert changed == {tracks[NAMES["B"]].doc_index: WAV_BEATS + 1}
    idx, a, b = _changed(text_of(als), text_of(out))
    assert len(idx) == WAV_BEATS + 1
    block = [i for i, line in enumerate(a) if f'<EffectiveName Value="{NAMES["B"]}"' in line][0]
    start = max(i for i, line in enumerate(a) if line.lstrip().startswith("<AudioTrack ") and i < block)
    end = min(i for i, line in enumerate(a) if line.lstrip().startswith("</AudioTrack>") and i > block)
    assert all(start < i < end for i in idx)                       # every change is inside track B's own block
    sec = re.compile(r'SecTime="[^"]+"')
    for i in idx:
        assert "<WarpMarker " in a[i]
        assert sec.sub("", a[i]) == sec.sub("", b[i])              # nothing but the SecTime value moved
        new, old = float(sec.search(b[i]).group(0)[9:-1]), float(sec.search(a[i]).group(0)[9:-1])
        assert new - old == pytest.approx(0.005, abs=1e-9)         # +5 ms is added to the SecTime
    assert not any("SavedWarpMarkers" in a[i] or 'Id="9' in a[i] for i in idx)


def test_patch_keeps_crlf_and_every_other_byte(tmp_path):
    als, _ = make_mix(tmp_path, "crlf", {"A": 0.0, "B": 0.0})
    out = tmp_path / "patched.als"
    gpf.patch_als(als, out, {0: lambda beat: 2.5, 1: lambda beat: 1.0})
    before, after = text_of(als), text_of(out)
    assert after.count("\r\n") == before.count("\r\n") == after.count("\n") and "\r\n" in after
    assert re.sub(r'SecTime="[^"]+"', "", before) == re.sub(r'SecTime="[^"]+"', "", after)


def test_a_patch_with_no_shift_for_a_marker_leaves_its_text_alone(tmp_path):
    als, _ = make_mix(tmp_path, "zero", {"A": 0.0, "B": 0.0})
    out = tmp_path / "patched.als"
    patched = gpf.patch_als(als, out, {0: lambda beat: 0.0})
    assert patched == {} and text_of(als) == text_of(out)


def test_patch_floats_keep_full_precision(tmp_path):
    als, _ = make_mix(tmp_path, "prec", {"A": 0.0, "B": 0.0})
    out = tmp_path / "patched.als"
    gpf.patch_als(als, out, {0: lambda beat: 1.23456789})
    marker = [l for l in text_of(out).splitlines() if 'BeatTime="7"' in l][0]
    assert abs(float(re.search(r'SecTime="([^"]+)"', marker).group(1)) - (7 * BEAT_S + 0.00123456789)) < 1e-12


def test_a_patched_file_passes_the_validator_and_reads_back(tmp_path):
    als, _ = make_mix(tmp_path, "valid", {"A": 0.0, "B": 0.0, "C": 0.0})
    out = tmp_path / "patched.als"
    gpf.patch_als(als, out, {1: lambda beat: 3.0})
    assert validate_als.validate_als(out) == []
    assert [t.name for t in kac.read_als(out)] == [NAMES[c] for c in "ABC"]


def test_the_patch_is_deterministic(tmp_path):
    als, _ = make_mix(tmp_path, "det", {"A": 0.0, "B": 0.0})
    one, two = tmp_path / "one.als", tmp_path / "two.als"
    gpf.patch_als(als, one, {1: lambda beat: 4.0})
    gpf.patch_als(als, two, {1: lambda beat: 4.0})
    assert one.read_bytes() == two.read_bytes()


def test_a_slide_of_plus_five_plays_the_kicks_five_ms_earlier(sets, tmp_path):
    als, audio = sets["plain"]                                     # B is 8 ms late: lag +8
    before = click_lags(als, audio)[0]
    out = tmp_path / "slid.als"
    gpf.patch_als(als, out, {1: lambda beat: 5.0})                 # B (the incoming track) +5 ms
    after = click_lags(out, audio)[0]
    assert before == pytest.approx(8.0, abs=1.0)
    assert after == pytest.approx(before - 5.0, abs=1.0), (before, after)
    out2 = tmp_path / "slid_out.als"
    gpf.patch_als(als, out2, {0: lambda beat: 5.0})                # A (the outgoing track) +5 ms: its kicks earlier, lag up
    assert click_lags(out2, audio)[0] == pytest.approx(before + 5.0, abs=1.0)


# ---------------------------------------------------------------- the whole run on synthetic mixes

def _marker_secs(path, track_name):
    t = {tr.name: tr for tr in kac.read_als(path)}[track_name]
    return t.clips[0].markers


def test_a_late_track_is_pulled_onto_the_grid_by_its_own_drift_fit(outcomes, sets):
    out = outcomes["late"]
    assert out.status == gpf.PHASED, out.reason
    first = kac.check_als(out.out_path, sets["late"][1])
    assert all(r.verdict in ("PASS", "WARN") for r in first)
    assert abs(first[0].click["median"]) <= LEFT and abs(first[1].click["median"]) <= LEFT
    moved = _marker_secs(out.out_path, NAMES["B"])[:, 1] - _marker_secs(sets["late"][0], NAMES["B"])[:, 1]
    assert np.median(moved) * 1000.0 == pytest.approx(8.0, abs=2.0)  # B's kicks were 8 ms late: its markers move about +8 ms
    by_name = {r.name: r for r in out.tracks}
    assert by_name[NAMES["B"]].applied and by_name[NAMES["B"]].fit.status == gpf.FIT
    assert not by_name[NAMES["C"]].applied                         # C sits in no blend that needed fixing


def test_the_late_track_that_the_corrected_one_left_behind_is_brought_in_with_the_least_slide(outcomes, sets):
    # B is pulled onto A by its drift fit; C, 8 ms late like B was, then differs from B by 8 ms. The slides add up to
    # that 8 ms less EPS_MS (spread over B and C by the tie-break), and no more.
    out = outcomes["late"]
    total = sum(abs(r.slide) for r in out.tracks)
    assert 8.0 - gpf.EPS_MS - 0.7 <= total <= 8.0
    assert abs(kac.check_als(out.out_path, sets["late"][1])[1].click["median"]) <= LEFT


def test_a_track_that_cannot_be_fitted_is_fixed_by_a_slide(outcomes, sets):
    out = outcomes["slide"]
    assert out.status == gpf.PHASED, out.reason
    by_name = {r.name: r for r in out.tracks}
    assert by_name[NAMES["B"]].fit.status == gpf.CANNOT_FIT and not by_name[NAMES["B"]].applied
    after = kac.check_als(out.out_path, sets["slide"][1])[0]
    assert abs(after.click["median"]) <= LEFT, after.click
    assert sum(abs(r.slide) for r in out.tracks) == pytest.approx(8.0 - gpf.EPS_MS, abs=1.0)
    # the lag fell: the slide moved the kicks the right way, whichever of the two tracks took it
    assert (by_name[NAMES["A"]].slide - by_name[NAMES["B"]].slide) == pytest.approx(-(8.0 - gpf.EPS_MS), abs=1.0)


def test_a_drifting_track_is_corrected_along_its_length_not_by_one_slide(outcomes, sets):
    out = outcomes["drift"]
    assert out.status == gpf.PHASED, out.reason
    after = kac.check_als(out.out_path, sets["drift"][1])
    assert all(abs(r.click["median"]) <= LEFT for r in after), [r.click for r in after]
    old = _marker_secs(sets["drift"][0], NAMES["B"])
    new = _marker_secs(out.out_path, NAMES["B"])
    moved = (new[:, 1] - old[:, 1]) * 1000.0
    assert moved[5] == pytest.approx(0.18 * 5, abs=1.5) and moved[50] == pytest.approx(0.18 * 50, abs=2.0)
    assert moved[50] - moved[5] > 5.0                               # it really is a drift, not a constant


def test_a_good_mix_is_untouched_and_the_copy_is_byte_identical(outcomes, sets):
    out = outcomes["good"]
    assert out.status == gpf.NOTHING and "already under" in out.reason
    assert Path(out.out_path).read_bytes() == sets["good"][0].read_bytes()
    assert not out.after and not any(r.applied or r.slide for r in out.tracks)


def test_the_input_is_never_changed(outcomes, sets):
    for key in outcomes["bytes"]:
        assert sets[key][0].read_bytes() == outcomes["bytes"][key]
        assert not list(sets[key][0].parent.glob("*.partial"))


def test_running_on_its_own_output_changes_nothing(outcomes, sets, tmp_path):
    out = outcomes["late"]
    again = gpf.run(out.out_path, sets["late"][1], tmp_path / "again.als", tmp_path / "report")
    assert again.status == gpf.NOTHING
    old = _marker_secs(out.out_path, NAMES["B"])[:, 1]
    new = _marker_secs(tmp_path / "again.als", NAMES["B"])[:, 1]
    assert np.abs(new - old).max() * 1000.0 < 0.5


def test_the_output_must_be_a_new_file(sets, tmp_path):
    with pytest.raises(ValueError):
        gpf.run(sets["late"][0], sets["late"][1], sets["late"][0], tmp_path)


def test_a_result_that_makes_a_blend_worse_is_refused_and_not_kept(sets, tmp_path, monkeypatch):
    monkeypatch.setattr(gpf, "solve_slides", lambda *a, **k: (np.array([8.0, 0.0]), 0.0))      # the wrong way: lag 8 to 16
    als, audio = sets["slide"]
    out_path = tmp_path / "Phased.als"
    out_path.write_bytes(b"an older result")                   # must not survive a refusal
    out = gpf.run(als, audio, out_path, tmp_path / "report")
    assert out.status == gpf.REFUSED_RESULT and out.refusals
    assert not out_path.exists() and Path(out.refused_path).exists() and out.refused_path.endswith(" REFUSED.als")
    assert not list(tmp_path.glob("*.partial"))
    assert "worst click lag" in out.reason


@pytest.mark.parametrize("before, after, expect", [
    (("WARN", 3.0), ("WARN", 3.0 + gpf.REFUSE_TOL_MS + 0.1), True),     # got worse by more than the tolerance
    (("WARN", 3.0), ("WARN", 3.0 + gpf.REFUSE_TOL_MS - 0.1), False),    # by less: the re-measure differs by that much
    (("WARN", 3.0), ("WARN", 3.0), False),
    (("FAIL", 9.0), ("WARN", 2.0), False),
    (("WARN", 3.0), ("FAIL", 2.0), True),                 # became FAIL
    (("FAIL", 9.0), ("FAIL", 8.5), False),                # was FAIL already
])
def test_the_refusal_rule(before, after, expect):
    def blend(verdict, lag):
        return SimpleNamespace(index=1, verdict=verdict, flags=[], label="T01 A to B", shared_kicks=30,
                               click=dict(median=lag, n=30, first_half=lag, second_half=lag, n_first_half=15, n_second_half=15))
    assert bool(gpf.refusals([blend(*before)], [blend(*after)])) is expect


def test_a_blend_that_can_no_longer_be_judged_is_refused():
    good = SimpleNamespace(index=1, verdict="WARN", flags=[], label="T01 A to B", shared_kicks=30,
                           click=dict(median=3.0, n=30, first_half=3.0, second_half=3.0, n_first_half=15, n_second_half=15))
    lost = SimpleNamespace(index=1, verdict="CANNOT_JUDGE", flags=[], label="T01 A to B", shared_kicks=3, click=dict(median=None, n=0))
    assert gpf.refusals([good], [lost])


def test_the_report_is_plain_ascii_without_em_dashes(outcomes, sets):
    for key in ("late", "good"):
        text = gpf.report_text(outcomes[key], sets[key][0])
        assert text.isascii() and "\u2014" not in text and "->" in text
        data = json.dumps(gpf.report_json(outcomes[key]))
        assert data.isascii()


def test_the_report_says_the_phased_file_is_a_suggestion(outcomes, sets):
    note = ("grid phase: phased file written, open it instead of Set.als if the report shows it is tighter; "
            "the MixPlan reconciliation covers the unphased original (the phased markers differ from the original by "
            "the corrections shown in the report: drift up to 12 ms, slide up to 15 ms)")
    assert note in gpf.report_text(outcomes["late"], sets["late"][0])
    assert "under 15 ms" not in gpf.report_text(outcomes["late"], sets["late"][0])
    assert "phased file written" not in gpf.report_text(outcomes["good"], sets["good"][0])


def test_only_short_windows_are_read_and_pyplot_stays_out(sets, tmp_path, monkeypatch):
    als, audio = sets["slide"]
    limit = 3 * SR
    frames = []
    real = sf.SoundFile.read

    def guarded(self, frames_asked=-1, *args, **kwargs):
        assert 0 < frames_asked <= limit, f"asked for {frames_asked} frames"
        frames.append(frames_asked)
        return real(self, frames_asked, *args, **kwargs)

    monkeypatch.setattr(sf.SoundFile, "read", guarded)
    monkeypatch.setattr(sf, "read", lambda *a, **k: (_ for _ in ()).throw(AssertionError("whole file read")))
    before = set(sys.modules)
    gpf.run(als, audio, tmp_path / "Phased.als", tmp_path / "report")
    assert frames and max(frames) <= limit < WAV_BEATS * int(BEAT_S * SR)
    assert "matplotlib.pyplot" not in set(sys.modules) - before


# ---------------------------------------------------------------- the CLI, executed

def run_cli(*args):
    return subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, timeout=600)


def test_cli_writes_the_new_file_the_report_and_the_pictures_and_exits_zero(sets, tmp_path):
    als, audio = sets["slide"]
    before = als.read_bytes()
    out, report = tmp_path / "Set Phased.als", tmp_path / "Grid Phase"
    result = run_cli(als, "--audio", audio, "--out", out, "--report", report)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT PHASED" in result.stdout and "REPORT:" in result.stdout and "FAIL -> " in result.stdout
    assert out.is_file() and als.read_bytes() == before
    assert (report / "grid_phase_report.txt").is_file() and (report / "grid_phase.json").is_file()
    assert list(report.glob("T01 *.png"))
    data = json.loads((report / "grid_phase.json").read_text())
    assert data["status"] == gpf.PHASED and data["tracks"] and data["before"] and data["after"]


def test_cli_default_names_are_beside_the_als(sets, tmp_path):
    als, audio = make_mix(tmp_path, "defaults", {"A": 0.0, "B": 8.0}, kick_beats={"B": range(0, 30)})
    result = run_cli(als, "--audio", audio)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (als.parent / "Set Phased.als").is_file() and (als.parent / "Grid Phase" / "grid_phase_report.txt").is_file()


def test_cli_a_good_mix_says_nothing_to_do_and_exits_zero(sets, tmp_path):
    als, audio = sets["good"]
    out = tmp_path / "Phased.als"
    result = run_cli(als, "--audio", audio, "--out", out, "--report", tmp_path / "r")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT NOTHING TO DO" in result.stdout and out.read_bytes() == als.read_bytes()
    assert "LOOK AT THESE PICTURES" not in result.stdout


def test_cli_stale_pictures_of_an_earlier_run_are_cleared(sets, tmp_path):
    report = tmp_path / "r"
    report.mkdir()
    (report / "T09 Old to Older.png").write_bytes(b"x")
    als, audio = sets["good"]
    run_cli(als, "--audio", audio, "--out", tmp_path / "Phased.als", "--report", report)
    assert not (report / "T09 Old to Older.png").exists()


def test_cli_unreadable_als_exits_two(tmp_path):
    bad = tmp_path / "bad.als"
    bad.write_bytes(b"not gzip")
    result = run_cli(bad, "--out", tmp_path / "o.als", "--report", tmp_path / "r")
    assert result.returncode == 2 and "could not run" in result.stderr


def test_cli_an_output_equal_to_the_input_exits_two(sets, tmp_path):
    als, audio = sets["good"]
    result = run_cli(als, "--audio", audio, "--out", als, "--report", tmp_path / "r")
    assert result.returncode == 2 and "ValueError" in result.stderr


def test_cli_a_refused_result_exits_four(sets, tmp_path, monkeypatch):
    monkeypatch.setattr(gpf, "solve_slides", lambda *a, **k: (np.array([8.0, 0.0]), 0.0))
    sys.path.insert(0, str(ROOT / "Tools"))
    try:
        import fix_grid_phase
    finally:
        sys.path.pop(0)
    als, audio = sets["slide"]
    out = tmp_path / "Phased.als"
    code = fix_grid_phase.main([str(als), "--audio", str(audio), "--out", str(out), "--report", str(tmp_path / "r")])
    assert code == 4 and not out.exists() and (tmp_path / "Phased REFUSED.als").exists()


def test_cli_never_crashes_on_a_console_that_cannot_show_a_character(capsys):
    sys.path.insert(0, str(ROOT / "Tools"))
    try:
        import fix_grid_phase
    finally:
        sys.path.pop(0)
    fix_grid_phase.say("caf\u00e9 \u2014 done")
    assert "done" in capsys.readouterr().out


def test_no_em_dash_anywhere_in_the_new_files():
    for path in (ROOT / "Source" / "grid_phase_fix.py", CLI, Path(__file__)):
        assert "\u2014" not in path.read_text(encoding="utf-8"), path


# ================================================================ fix round 2: what the reviewers found

def _track_with_audio(tmp_path, wav_name="Out - Alpha SW V1.wav", delay=0.0, kick_beats=range(WAV_BEATS), entry_options=None,
                      start=0, end=60, beats=WAV_BEATS, claps=()):
    wav = tmp_path / wav_name
    write_wav(wav, delay, kick_beats, beats=beats, claps=claps)
    als = tmp_path / "Set.als"
    with gzip.open(als, "wb") as fh:
        fh.write(als_text([("Out - Alpha SW V1", wav.name, start, end, entry_options or {"beats": beats})]).encode("utf-8"))
    track = kac.read_als(als)[0]
    for clip in track.clips:
        clip.audio = wav
    return track, wav


def _measure(track, wav):
    reader = kac.WavReader(wav)
    try:
        return gpf.measure_offsets(track, {str(wav): reader})
    finally:
        reader.close()


# ---- the solve: a blend a drift correction pushed out is repaired; good blends are held; headroom; noise

def test_a_good_blend_a_drift_correction_pushed_out_is_repaired_by_a_slide():
    # blend 1 was 10 and the drift fit brought it to 0.5; blend 2 was 0.5 and the fit pushed it to 3.0 (under WARN_MS)
    s, left = gpf.solve_slides(np.array([0.5, 3.0]), [(0, 1), (1, 2)], 3, None, [10.0, 0.5])
    assert s.any()
    assert abs(3.0 + s[1] - s[2]) <= 0.5 + gpf.REFUSE_TOL_MS + 1e-6          # back to what it was, within the tolerance
    assert abs(0.5 + s[0] - s[1]) <= gpf.EPS_MS + 1e-6


def test_good_blends_are_held_at_no_worse_than_before_when_the_outer_tracks_cannot_move():
    s, _ = gpf.solve_slides(np.array([0.5, 10.0, 0.5]), [(0, 1), (1, 2), (2, 3)], 4, {1, 2}, [0.5, 10.0, 0.5])
    assert s[0] == 0.0 and s[3] == 0.0
    assert abs(0.5 + s[0] - s[1]) <= 0.5 + gpf.REFUSE_TOL_MS + 1e-6
    assert abs(0.5 + s[2] - s[3]) <= 0.5 + gpf.REFUSE_TOL_MS + 1e-6
    assert abs(10.0 + s[1] - s[2]) < 10.0                                      # and it still helped a little


def test_a_fix_that_would_need_two_tracks_to_slide_by_8_5_is_not_made_that_way():
    s, left = gpf.solve_slides(np.array([0.2, 10.0, 0.2]), [(0, 1), (1, 2), (2, 3)], 4, None, [0.2, 10.0, 0.2])
    assert not (abs(s[0] + 8.5) < 0.05 and abs(s[1] + 8.5) < 0.05)
    assert np.abs(s).sum() < 17.0 - 0.5 and left <= gpf.EPS_MS + 1e-6


def test_a_track_cannot_slide_earlier_than_its_first_marker_allows():
    s, _ = gpf.solve_slides(np.array([10.0]), [(0, 1)], 2, headroom=[0.0, math.inf])
    assert s[0] == 0.0 and s[1] == pytest.approx(8.5, abs=0.01)
    s, _ = gpf.solve_slides(np.array([10.0]), [(0, 1)], 2, headroom=[3.0, math.inf])
    assert s[0] >= -3.0 - 1e-9 and abs(10.0 + s[0] - s[1]) <= gpf.EPS_MS + 1e-6


def test_a_slide_under_the_noise_floor_is_not_a_slide():
    # no `was`: both blends must be fixed to EPS_MS; the second sits 0.02 ms over it and the solver would slide 0.02 ms
    s, _ = gpf.solve_slides(np.array([5.52, gpf.EPS_MS + 0.02]), [(0, 1), (2, 3)], 4)
    assert s[2] == 0.0 and s[3] == 0.0 and s[0] != 0.0


# ---- the gate, at run level

def test_a_lag_of_5_ms_is_fixed_not_left_because_the_gate_is_4(outcomes):
    out = outcomes["five"]
    assert out.status == gpf.PHASED, out.reason
    assert abs(out.after[0].click["median"]) <= LEFT


def test_the_solve_is_given_the_neighbours_of_the_blends_that_need_fixing_and_no_other_track(sets, tmp_path, monkeypatch):
    seen = {}
    real = gpf.solve_slides

    def spy(lags, pairs, n, movable=None, *args, **kwargs):
        seen.update(pairs=list(pairs), movable=set(movable), n=n)
        return real(lags, pairs, n, movable, *args, **kwargs)

    monkeypatch.setattr(gpf, "solve_slides", spy)
    als, audio = sets["four"]
    out = gpf.run(als, audio, tmp_path / "Phased.als", tmp_path / "report")
    # the blend A-B needs fixing; B-C touches B; C-D touches C, which may slide; D is two blends away and stays put
    assert seen["movable"] == {0, 1, 2} and seen["pairs"] == [(0, 1), (1, 2), (2, 3)], seen
    assert out.tracks[3].slide == 0.0 and not out.tracks[3].applied


# ---- tracks the check could not judge, sparse markers

def test_a_track_in_a_blend_the_check_could_not_judge_is_not_moved(outcomes, sets):
    out = outcomes["frozen"]
    assert out.before[1].verdict == "CANNOT_JUDGE"
    by = {r.name: r for r in out.tracks}
    assert out.status == gpf.PHASED, out.reason
    b = by[NAMES["B"]]
    assert b.slide == 0.0 and not b.applied and "not moved: sits in a blend the check could not judge" in b.note
    assert by[NAMES["C"]].note == b.note and by[NAMES["C"]].slide == 0.0
    assert set(out.changed) == {by[NAMES["A"]].doc_index}                    # only A's markers were touched
    assert by[NAMES["A"]].slide == pytest.approx(8.0 - gpf.EPS_MS, abs=1.0)
    assert "not moved: sits in a blend the check could not judge" in gpf.report_text(out, sets["frozen"][0])
    assert gpf.report_json(out)["tracks"][1]["note"] == b.note


def test_a_track_with_two_warp_markers_is_slid_but_never_drift_corrected(outcomes):
    out = outcomes["sparse"]
    assert out.status == gpf.PHASED, out.reason
    b = {r.name: r for r in out.tracks}[NAMES["B"]]
    assert b.fit.status == gpf.SPARSE_MARKERS and not b.applied
    assert b.slide == pytest.approx(8.0 - gpf.EPS_MS, abs=1.0)
    assert abs(out.after[0].click["median"]) <= LEFT


def test_sparse_markers_means_fewer_than_one_marker_per_four_source_beats(tmp_path):
    for markers, sparse in (([0, 100], True), (list(range(0, 101, 4)), False), (list(range(0, 101, 5)), True)):
        als = tmp_path / "Set.als"
        with gzip.open(als, "wb") as fh:
            fh.write(als_text([("Out - Alpha SW V1", "a.wav", 0, 60, {"markers": markers})]).encode("utf-8"))
        assert gpf.sparse_markers(kac.read_als(als)[0]) is sparse, markers


# ---- the halves

def test_a_blend_whose_half_the_fix_cannot_reach_is_listed_and_the_run_is_not_called_fixed(outcomes, sets):
    out = outcomes["halves"]
    assert out.still_flamming and out.still_flamming[0]["index"] == 1
    assert {r.name: r for r in out.tracks}[NAMES["B"]].fit.status == gpf.CANNOT_FIT
    assert out.reason.startswith("NOT FIXED") and "still flamming" in out.reason
    text = gpf.report_text(out, sets["halves"][0])
    assert "STILL FLAMMING (half): the fix does not reach it" in text
    assert "STILL FLAMMING (half): the fix does not reach it" in gpf.report_json(out)["still_flamming"][0]["note"]


def test_a_fixed_run_does_not_claim_a_flam_it_does_not_have(outcomes, sets):
    for key in ("late", "five"):
        assert not outcomes[key].still_flamming and "STILL FLAMMING" not in gpf.report_text(outcomes[key], sets[key][0])


# ---- a drifting track in the middle

def test_a_drifting_track_in_the_middle_is_repaired_not_refused(outcomes):
    out = outcomes["mid"]
    assert out.status == gpf.PHASED, out.reason
    assert not out.refusals and all(abs(r.click["median"]) <= LEFT for r in out.after if gpf.usable(r))


# ---- the patch: layout, empty tracks, negative times, exponent notation, nothing changed, wrong prediction

def test_the_patch_hits_the_right_tracks_when_empty_audio_tracks_sit_in_the_file(outcomes, sets):
    out = outcomes["empties"]
    assert out.status == gpf.PHASED, out.reason
    old = {t.name: t for t in kac.read_als(sets["empties"][0])}
    new = {t.name: t for t in kac.read_als(out.out_path)}
    assert {n: t.doc_index for n, t in old.items()} == {NAMES["B"]: 1, NAMES["C"]: 3, NAMES["A"]: 4}
    moved = {name: float(np.max(np.abs(new[name].clips[0].markers[:, 1] - old[name].clips[0].markers[:, 1]))) * 1000.0
             for name in old}
    assert moved[NAMES["B"]] > 4.0                              # B's own markers moved ...
    assert set(out.changed) == {old[n].doc_index for n in old if moved[n] > 0.0}    # ... and only the tracks that were meant to
    assert abs(kac.check_als(out.out_path, sets["empties"][1])[0].click["median"]) <= LEFT


def test_the_patch_refuses_a_file_that_is_not_one_element_per_line(sets, tmp_path):
    als, audio = sets["slide"]
    flat = tmp_path / "flat.als"
    with gzip.open(flat, "wb") as fh:
        fh.write(text_of(als).replace("\r\n", "").encode("utf-8"))
    with pytest.raises(gpf.AlsLayoutError):
        gpf.patch_als(flat, tmp_path / "out.als", {1: lambda beat: 5.0})
    out = gpf.run(flat, audio, tmp_path / "Phased.als", tmp_path / "report")
    assert out.status == gpf.REFUSED_RESULT and "AudioTrack" in out.reason and not (tmp_path / "Phased.als").exists()


def test_the_patch_counts_the_same_tracks_the_reader_does(tmp_path):
    als, _ = make_mix(tmp_path, "empties", {"A": 0.0, "B": 0.0}, empties=(0, 2))
    tracks = {t.name: t for t in kac.read_als(als)}
    assert [tracks[NAMES["A"]].doc_index, tracks[NAMES["B"]].doc_index] == [1, 3]
    out = tmp_path / "patched.als"
    assert gpf.patch_als(als, out, {tracks[NAMES["B"]].doc_index: lambda beat: 3.0}) == {3: WAV_BEATS + 1}
    assert text_of(out) != text_of(als)


def test_a_patch_that_would_write_a_negative_sec_time_is_refused_with_the_track_and_marker(tmp_path):
    als, audio = make_mix(tmp_path, "early", {"A": 0.0, "B": -8.0})
    with pytest.raises(gpf.NegativeSecTime) as info:
        gpf.patch_als(als, tmp_path / "neg.als", {0: lambda beat: -5.0})
    assert info.value.doc_index == 0 and info.value.beat == 0.0 and info.value.value < 0
    out = gpf.run(als, audio, tmp_path / "Phased.als", tmp_path / "report")        # B kicks 8 ms early: a drift fit of -8 on a marker at 0
    assert out.status == gpf.REFUSED_RESULT and "negative SecTime" in out.reason and "Bravo" in out.reason
    assert not (tmp_path / "Phased.als").exists()


def test_a_sec_time_is_written_without_an_exponent_and_round_trips(tmp_path):
    als, _ = make_mix(tmp_path, "tiny", {"A": 0.0, "B": 0.0})
    out = tmp_path / "patched.als"
    gpf.patch_als(als, out, {0: lambda beat: 1e-4})                  # +0.0001 ms: the marker at SecTime 0 becomes 1e-7 s
    line = [l for l in text_of(out).splitlines() if 'BeatTime="0"' in l][0]
    value = re.search(r'SecTime="([^"]+)"', line).group(1)
    assert "e" not in value.lower() and float(value) == 1e-4 / 1000.0
    assert all("e" not in re.search(r'SecTime="([^"]+)"', l).group(1).lower() for l in text_of(out).splitlines() if "<WarpMarker " in l)


def test_a_result_that_differs_from_the_prediction_is_refused(sets, tmp_path, monkeypatch):
    real = gpf.patch_als
    monkeypatch.setattr(gpf, "patch_als", lambda src, dst, shifts: real(src, dst, {k: (lambda beat, f=f: f(beat) * 0.3)
                                                                                  for k, f in shifts.items()}))
    als, audio = sets["slide"]
    out = gpf.run(als, audio, tmp_path / "Phased.als", tmp_path / "report")
    assert out.status == gpf.REFUSED_RESULT and "predicted click lag" in out.reason and "measured" in out.reason
    assert not (tmp_path / "Phased.als").exists() and Path(out.refused_path).exists()


def test_a_patch_that_changes_no_marker_is_refused(sets, tmp_path, monkeypatch):
    real = gpf.patch_als
    monkeypatch.setattr(gpf, "patch_als", lambda src, dst, shifts: (real(src, dst, {}), {})[1])
    als, audio = sets["slide"]
    out = gpf.run(als, audio, tmp_path / "Phased.als", tmp_path / "report")
    assert out.status == gpf.REFUSED_RESULT and "changed no warp marker line" in out.reason
    assert not (tmp_path / "Phased.als").exists()


# ---- the drift fit

def test_the_swing_not_only_the_size_decides_whether_a_fit_is_drift():
    swinging = gpf.fit_drift([(b, -3.9 if b < 350 else 2.6) for b in range(700)])
    assert swinging.status == gpf.FIT                                   # neither end reaches 4 ms, the swing is 6.5
    taste = gpf.fit_drift([(b, -1.9 if b < 350 else 1.8) for b in range(700)])
    assert taste.status == gpf.NO_DRIFT                                 # largest 1.9, swing 3.7
    killa = gpf.fit_drift([(b, -9.2 if b < 350 else 3.0) for b in range(700)])
    assert killa.status == gpf.FIT                                      # a swing of 12.2 passes
    excursion = gpf.fit_drift([(b, -8.0 if b < 350 else 8.0) for b in range(700)])
    assert excursion.status == gpf.REFUSED and "swings" in excursion.note and gpf.MAX_SWING_MS < 16.0


def test_a_correction_exactly_at_max_drift_is_not_refused(monkeypatch):
    fit = gpf.fit_drift([(b, 5.0) for b in range(100)])
    largest = max(abs(fit.low), abs(fit.high))
    monkeypatch.setattr(gpf, "MAX_DRIFT_MS", largest)
    assert gpf.fit_drift([(b, 5.0) for b in range(100)]).status == gpf.FIT


def test_the_running_median_rejects_a_few_outliers_a_mean_would_follow():
    offsets = [(b, 14.0 if 50 <= b < 58 else 5.0) for b in range(120)]
    fit = gpf.fit_drift(offsets)
    assert fit.status == gpf.FIT and abs(fit.at(54) - 5.0) < 0.3


def test_a_window_with_too_few_kicks_is_widened_until_it_holds_enough():
    offsets = [(b, 2.0) for b in range(40)] + [(b, 8.0) for b in range(300, 305)]
    fit = gpf.fit_drift(offsets)
    assert fit.status in (gpf.FIT, gpf.NO_DRIFT)
    assert abs(float(np.interp(302, fit.first_beat + np.arange(len(fit.corr)), fit.corr)) - 2.0) < 1.0


def test_a_hat_or_clap_is_not_a_kick(tmp_path):
    track, wav = _track_with_audio(tmp_path, delay=0.0, kick_beats=range(0, WAV_BEATS, 2), claps=range(1, WAV_BEATS, 2))
    beats = [b for b, _ in _measure(track, wav)]
    assert len(beats) >= 25 and all(int(b) % 2 == 0 for b in beats)


def test_a_source_beat_read_twice_is_counted_once(tmp_path):
    import copy
    track, wav = _track_with_audio(tmp_path)
    once = _measure(track, wav)
    second = copy.copy(track.clips[0])
    second.start, second.end = 100.0, 160.0                              # the same source beats again, later in the arrangement
    track.clips.append(second)
    twice = _measure(track, wav)
    assert len(twice) == len(once) and len({b for b, _ in twice}) == len(twice)


def test_the_beat_label_is_the_clips_own_source_beat_when_the_loop_starts_late(tmp_path):
    options = {"loop_start": 128, "beats": 200}
    track, wav = _track_with_audio(tmp_path, delay=lambda b: 1.0 + 0.04 * (b - 128), start=64, end=124, beats=200,
                                   entry_options=options, kick_beats=range(110, 200))
    offsets = _measure(track, wav)
    labels = [b for b, _ in offsets]
    assert min(labels) == 128 and max(labels) >= 186
    first = offsets[0][1]
    assert max(abs((o - first) - 0.04 * (b - 128)) for b, o in offsets) <= 0.25
    fit = gpf.fit_drift(offsets)
    assert fit.first_beat == 128 and fit.status in (gpf.FIT, gpf.NO_DRIFT)


def test_a_clip_with_loop_on_is_not_read(tmp_path):
    track, wav = _track_with_audio(tmp_path, entry_options={"loop_on": True})
    assert _measure(track, wav) == []


def test_the_edge_only_analysis_gives_the_kick_test_and_the_edge_the_full_one_gives(sets):
    als, audio = sets["good"]
    reader = kac.WavReader(audio / (NAMES["B"] + ".wav"))
    try:
        for beat in (5, 20, 33):
            x, t0, _ = reader.window(beat * BEAT_S)
            full, light = kac.analyse_window(x, reader.sr, t0), kac.analyse_window(x, reader.sr, t0, edge_only=True)
            assert (full.candidate, full.body, full.edge_ms) == (light.candidate, light.body, light.edge_ms)
            assert light.grid is None and math.isnan(light.main_ms) and full.candidate
    finally:
        reader.close()


# ---- the refusal rule and the gate, unit level

def _blend(index, median, half=None, verdict="WARN"):
    half = median if half is None else half
    return SimpleNamespace(index=index, verdict=verdict, flags=[], label=f"T{index:02d} A to B", shared_kicks=30,
                           click=dict(median=median, n=30, first_half=half, second_half=median, n_first_half=15, n_second_half=15))


def test_a_half_that_got_worse_is_refused_even_when_the_median_did_not():
    assert gpf.refusals([_blend(1, 2.0, 2.0)], [_blend(1, 2.0, 6.0)])
    assert not gpf.refusals([_blend(1, 2.0, 2.0)], [_blend(1, 2.0, 2.0)])


def test_a_different_number_of_transitions_is_refused():
    assert gpf.refusals([_blend(1, 2.0), _blend(2, 2.0)], [_blend(1, 2.0)])
    assert gpf.refusals([_blend(1, 2.0)], [_blend(2, 2.0)])                 # same count, not the same transition


def test_a_blend_is_flagged_for_a_drift_flam_by_a_half_alone():
    assert gpf.needs_fixing(dict(median=1.0, first_half=9.0, second_half=-9.0))
    assert gpf.needs_fixing(dict(median=1.0, first_half=1.0, second_half=-8.0))
    assert not gpf.needs_fixing(dict(median=3.9, first_half=7.9, second_half=-7.9))
    assert gpf.needs_fixing(dict(median=4.0, first_half=None, second_half=None))


def test_the_edge_or_main_hit_getting_worse_is_reported_never_refused(outcomes, sets):
    def blend(index, edge, main):
        b = _blend(index, 1.0)
        b.edge, b.main = dict(median=edge), dict(median=main)
        return b

    found = gpf.edge_main_worse([blend(1, 0.5, 1.0), blend(2, 0.5, 1.0)], [blend(1, 4.9, 1.5), blend(2, 4.4, 5.1)])
    assert [(f["label"], f["estimator"]) for f in found] == [("T01 A to B", "edge"), ("T02 A to B", "main")]
    assert found[0]["before"] == 0.5 and found[0]["after"] == 4.9
    import copy
    out = copy.copy(outcomes["late"])
    out.edge_main_worse = found
    text = gpf.report_text(out, sets["late"][0])
    assert "T01 A to B: edge +0.5 -> +4.9 ms: aligned by click, as chosen by ear for this mix: check the picture" in text
    assert gpf.report_json(out)["edge_main_worse"] == found
    assert not gpf.refusals([blend(1, 0.5, 1.0)], [blend(1, 4.9, 9.0)])


def test_the_report_and_the_json_agree_about_every_track(outcomes, sets):
    for key in ("late", "frozen", "sparse", "halves"):
        out = outcomes[key]
        table = gpf.report_text(out, sets[key][0]).split("Tracks\n", 1)[1].split("\n\n")[0].splitlines()[1:]
        data = gpf.report_json(out)["tracks"]
        assert len(table) == len(data)
        for line, track in zip(table, data):
            applied = re.search(r"\s(yes|no)\s+[+-]\d", line).group(1) == "yes"
            assert track["fit"] in line and applied == track["applied"] and f"{track['slide']:+.2f}" in line, (line, track)


def test_outcome_changed_is_keyed_by_the_position_in_the_file_not_by_name(outcomes):
    out = outcomes["late"]
    assert out.changed and all(isinstance(k, int) for k in out.changed)
    assert set(out.changed) <= {r.doc_index for r in out.tracks}


def test_cli_removes_an_older_output_before_it_can_fail(tmp_path):
    bad = tmp_path / "bad.als"
    bad.write_bytes(b"not gzip")
    out = tmp_path / "Phased.als"
    out.write_bytes(b"an older result")
    refused = tmp_path / "Phased REFUSED.als"
    refused.write_bytes(b"an older refusal")
    result = run_cli(bad, "--out", out, "--report", tmp_path / "r")
    assert result.returncode == 2 and not out.exists() and not refused.exists()


def test_cli_never_removes_the_input_when_it_is_also_the_output(sets, tmp_path):
    als = tmp_path / "Set.als"
    als.write_bytes(sets["good"][0].read_bytes())
    result = run_cli(als, "--audio", sets["good"][1], "--out", als, "--report", tmp_path / "r")
    assert result.returncode == 2 and als.read_bytes() == sets["good"][0].read_bytes()


def test_a_transition_that_appears_only_in_the_result_is_refused():
    assert gpf.refusals([_blend(1, 2.0)], [_blend(1, 2.0), _blend(2, 2.0)])


def test_the_edge_and_main_hit_report_reaches_the_outcome_of_a_run(sets, tmp_path, monkeypatch):
    marker = [dict(label="T01 Alpha to Bravo", estimator="main", before=0.0, after=9.0)]
    monkeypatch.setattr(gpf, "edge_main_worse", lambda before, after: marker)
    als, audio = sets["slide"]
    out = gpf.run(als, audio, tmp_path / "Phased.als", tmp_path / "report")
    assert out.status == gpf.PHASED and out.edge_main_worse == marker
    assert "main +0.0 -> +9.0 ms: aligned by click, as chosen by ear for this mix: check the picture" in gpf.report_text(out, als)
