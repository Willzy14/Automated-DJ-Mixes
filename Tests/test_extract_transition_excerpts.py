"""Tests for Source/extract_transition_excerpts.py: burn list A4's second
half - per-transition excerpt extraction feeding seal_listening_test.py.

Fixtures are built in code: a minimal gzipped ALS (Tempo/Manual, optional
MainTrack tempo automation envelope) matching what render_check.TempoMap
expects, a synthetic ARRANGEMENT_REPORT dict, and short synthetic WAVs
(distinguishable tones per side, never accidentally silent).
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import extract_transition_excerpts as ete  # noqa: E402
from render_check import TempoAutomationUnsupported  # noqa: E402


# --------------------------------------------------------------------------- #
# fixtures                                                                    #
# --------------------------------------------------------------------------- #

def _als_bytes(bpm: float = 130.0, tempo_envelope=None) -> bytes:
    """Minimal gzipped ALS carrying only what render_check.TempoMap reads:
    LiveSet/Tempo/Manual, and optionally a MainTrack tempo automation
    envelope (Tempo/AutomationTarget Id="8" + AutomationEnvelope whose
    EnvelopeTarget/PointeeId is "8"), mirroring als_generator's own shape."""
    root = Element("Ableton")
    live = SubElement(root, "LiveSet")
    tempo = SubElement(live, "Tempo")
    manual = SubElement(tempo, "Manual")
    manual.set("Value", str(bpm))

    if tempo_envelope is not None:
        mt = SubElement(live, "MainTrack")
        mt_tempo = SubElement(mt, "Tempo")
        at = SubElement(mt_tempo, "AutomationTarget")
        at.set("Id", "8")
        env = SubElement(mt, "AutomationEnvelope")
        target = SubElement(env, "EnvelopeTarget")
        pid = SubElement(target, "PointeeId")
        pid.set("Value", "8")
        auto = SubElement(env, "Automation")
        events = SubElement(auto, "Events")
        for event_time, value in tempo_envelope:
            fe = SubElement(events, "FloatEvent")
            fe.set("Time", str(event_time))
            fe.set("Value", str(value))
    return gzip.compress(tostring(root, encoding="utf-8"))


def _write_als(path: Path, bpm: float = 130.0, tempo_envelope=None) -> None:
    path.write_bytes(_als_bytes(bpm, tempo_envelope))


def _track(name, arr_start, arr_end):
    return {"name": name, "arr_start": arr_start, "arr_end": arr_end}


def _report(tracks, transitions):
    return {"tracks": tracks, "transitions": transitions}


def _transition(pair_index, out_name, in_name):
    return {"pair_index": pair_index, "out_track": out_name, "in_track": in_name}


def _write_wav(path: Path, seconds: float, sr: int = 44100, freq: float = 440.0,
              amplitude: float = 0.5) -> None:
    n = int(seconds * sr)
    t = np.arange(n) / sr
    y = (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float64)
    sf.write(str(path), np.stack([y, y], axis=1), sr, subtype="PCM_16")


# --------------------------------------------------------------------------- #
# _select_transitions                                                        #
# --------------------------------------------------------------------------- #

def test_select_transitions_pairs_tracks_by_position():
    tracks = [_track("A", 0.0, 400.0), _track("B", 300.0, 700.0),
             _track("C", 600.0, 1000.0)]
    transitions = [_transition(1, "A", "B"), _transition(2, "B", "C")]
    report = _report(tracks, transitions)
    result = ete._select_transitions(report)
    assert result[1]["out_track"] == tracks[0]
    assert result[1]["in_track"] == tracks[1]
    assert result[2]["out_track"] == tracks[1]
    assert result[2]["in_track"] == tracks[2]


def test_select_transitions_refuses_when_names_dont_match_tracks_order():
    # Simulates a broken invariant: transitions[] names a track that isn't
    # actually at the position pair_index implies.
    tracks = [_track("A", 0.0, 400.0), _track("B", 300.0, 700.0),
             _track("C", 600.0, 1000.0)]
    transitions = [_transition(1, "A", "WRONG NAME")]
    report = _report(tracks, transitions)
    with pytest.raises(ValueError, match="does not match"):
        ete._select_transitions(report)


# --------------------------------------------------------------------------- #
# _transition_window_beats                                                   #
# --------------------------------------------------------------------------- #

def test_transition_window_matches_overlap_zone_plus_context_convention():
    # Same convention as transition_review_viz.render_transition:
    # ov_start = in_track.arr_start, ov_end = out_track.arr_end,
    # padded by context_bars * 4 beats each side.
    out_track = _track("out", 0.0, 708.0)
    in_track = _track("in", 548.0, 1208.0)
    start, end = ete._transition_window_beats(out_track, in_track, context_bars=8.0)
    assert start == pytest.approx(548.0 - 32.0)
    assert end == pytest.approx(708.0 + 32.0)


def test_transition_window_clamps_start_at_zero():
    out_track = _track("out", -100.0, 50.0)
    in_track = _track("in", 10.0, 200.0)
    start, end = ete._transition_window_beats(out_track, in_track, context_bars=8.0)
    assert start == 0.0
    assert end == pytest.approx(50.0 + 32.0)


def test_transition_window_rejects_inverted_overlap():
    out_track = _track("out", 0.0, 100.0)
    in_track = _track("in", 500.0, 900.0)  # in_track starts AFTER out_track ends
    with pytest.raises(ValueError, match="empty or inverted"):
        ete._transition_window_beats(out_track, in_track, context_bars=8.0)


# --------------------------------------------------------------------------- #
# resolve_tempo_map                                                          #
# --------------------------------------------------------------------------- #

def test_resolve_tempo_map_reads_flat_manual_tempo(tmp_path):
    als = tmp_path / "Mix A.als"
    _write_als(als, bpm=130.0)
    tempo_map = ete.resolve_tempo_map(als)
    assert tempo_map.is_flat
    # 130 beats at 130 bpm = 60 seconds
    assert tempo_map.beat_to_sec(130.0) == pytest.approx(60.0)


def test_resolve_tempo_map_handles_a_genuine_tempo_ramp(tmp_path):
    # render_check.TempoMap is a real piecewise beat<->time map, not a
    # flat-tempo-only reader - a clean, unambiguous ramp converts correctly
    # rather than being rejected.
    als = tmp_path / "Mix A.als"
    _write_als(als, bpm=128.0, tempo_envelope=[(0.0, 128.0), (256.0, 132.0)])
    tempo_map = ete.resolve_tempo_map(als)
    assert not tempo_map.is_flat
    assert tempo_map.bpm_at(0.0) == pytest.approx(128.0)
    assert tempo_map.bpm_at(256.0) == pytest.approx(132.0)


def test_resolve_tempo_map_aborts_on_an_ambiguous_tempo_envelope(tmp_path):
    # Two AutomationEnvelopes both targeting id "8" (the tempo target) is
    # exactly the case render_check.TempoMap refuses to guess about - a
    # malformed/hand-edited ALS, not something our own writer produces.
    als = tmp_path / "Mix A.als"
    root = Element("Ableton")
    live = SubElement(root, "LiveSet")
    tempo = SubElement(live, "Tempo")
    manual = SubElement(tempo, "Manual")
    manual.set("Value", "128.0")
    mt = SubElement(live, "MainTrack")
    mt_tempo = SubElement(mt, "Tempo")
    at = SubElement(mt_tempo, "AutomationTarget")
    at.set("Id", "8")
    for _ in range(2):
        env = SubElement(mt, "AutomationEnvelope")
        target = SubElement(env, "EnvelopeTarget")
        pid = SubElement(target, "PointeeId")
        pid.set("Value", "8")
        auto = SubElement(env, "Automation")
        events = SubElement(auto, "Events")
        fe = SubElement(events, "FloatEvent")
        fe.set("Time", "0.0")
        fe.set("Value", "128.0")
    als.write_bytes(gzip.compress(tostring(root, encoding="utf-8")))
    with pytest.raises(TempoAutomationUnsupported):
        ete.resolve_tempo_map(als)


# --------------------------------------------------------------------------- #
# extract_excerpt                                                            #
# --------------------------------------------------------------------------- #

def test_extract_excerpt_slices_the_requested_window(tmp_path):
    wav = tmp_path / "Mix A.wav"
    _write_wav(wav, seconds=10.0)

    out = tmp_path / "excerpt.wav"
    ete.extract_excerpt(wav, start_sec=2.0, end_sec=4.0, out_path=out)
    info = sf.info(str(out))
    assert info.frames / info.samplerate == pytest.approx(2.0, abs=0.01)
    assert info.samplerate == 44100


def test_extract_excerpt_clamps_to_file_duration(tmp_path):
    wav = tmp_path / "Mix A.wav"
    _write_wav(wav, seconds=3.0)  # short file

    out = tmp_path / "excerpt.wav"
    ete.extract_excerpt(wav, start_sec=0.0, end_sec=20.0, out_path=out)  # way past 3s
    info = sf.info(str(out))
    assert info.frames / info.samplerate == pytest.approx(3.0, abs=0.01)


def test_extract_excerpt_raises_on_empty_window_past_file_end(tmp_path):
    wav = tmp_path / "Mix A.wav"
    _write_wav(wav, seconds=2.0)

    out = tmp_path / "excerpt.wav"
    with pytest.raises(ValueError, match="empty"):
        ete.extract_excerpt(wav, start_sec=100.0, end_sec=200.0, out_path=out)


# --------------------------------------------------------------------------- #
# _equalize_windows_sec  (Codex FATAL-1, 2026-09-14)                         #
# --------------------------------------------------------------------------- #

def test_equalize_windows_extends_the_shorter_side_symmetrically():
    windows = {"A": (100.0, 150.0), "B": (100.0, 200.0)}  # A=50s, B=100s
    bounds = {"A": (0.0, 1000.0), "B": (0.0, 1000.0)}
    result = ete._equalize_windows_sec(windows, bounds)
    assert result["B"] == (100.0, 200.0)  # longest side untouched
    a_start, a_end = result["A"]
    assert a_end - a_start == pytest.approx(100.0)
    # extended symmetrically: 25s taken from each side of the original window
    assert a_start == pytest.approx(75.0)
    assert a_end == pytest.approx(175.0)


def test_equalize_windows_leaves_equal_durations_untouched():
    windows = {"A": (0.0, 50.0), "B": (10.0, 60.0)}
    bounds = {"A": (0.0, 1000.0), "B": (0.0, 1000.0)}
    result = ete._equalize_windows_sec(windows, bounds)
    assert result == windows


def test_equalize_windows_clamps_to_file_bounds_and_pushes_overflow_to_the_other_side():
    # B needs 40s more but only has 10s of room before its start (bound at 0);
    # the remaining 30s deficit must come from extending the end instead.
    windows = {"A": (0.0, 100.0), "B": (10.0, 70.0)}  # A=100s, B=60s, deficit=40s
    bounds = {"A": (0.0, 1000.0), "B": (0.0, 1000.0)}
    result = ete._equalize_windows_sec(windows, bounds)
    b_start, b_end = result["B"]
    assert b_end - b_start == pytest.approx(100.0)
    assert b_start == pytest.approx(0.0)  # clamped at its lower bound


def test_equalize_windows_raises_when_a_side_cannot_reach_the_target():
    # B's own file is only 65s long - can never be extended to match A's 100s.
    windows = {"A": (0.0, 100.0), "B": (10.0, 70.0)}
    bounds = {"A": (0.0, 1000.0), "B": (0.0, 65.0)}
    with pytest.raises(ValueError, match="cannot be extended"):
        ete._equalize_windows_sec(windows, bounds)


def test_equalize_windows_never_shortens_the_overlap_content():
    # The longest side's window must come back byte-identical - the content
    # being judged is never cropped to match a shorter side.
    windows = {"A": (500.0, 700.0), "B": (520.0, 640.0)}
    bounds = {"A": (0.0, 10000.0), "B": (0.0, 10000.0)}
    result = ete._equalize_windows_sec(windows, bounds)
    assert result["A"] == (500.0, 700.0)


# --------------------------------------------------------------------------- #
# _transition_window_beats: context_bars validation (Codex MINOR-5)          #
# --------------------------------------------------------------------------- #

def test_transition_window_rejects_negative_context_bars():
    out_track = _track("out", 0.0, 100.0)
    in_track = _track("in", 50.0, 200.0)
    with pytest.raises(ValueError, match="finite and >= 0"):
        ete._transition_window_beats(out_track, in_track, context_bars=-1.0)


def test_transition_window_rejects_non_finite_context_bars():
    out_track = _track("out", 0.0, 100.0)
    in_track = _track("in", 50.0, 200.0)
    with pytest.raises(ValueError, match="finite and >= 0"):
        ete._transition_window_beats(out_track, in_track, context_bars=float("nan"))


# --------------------------------------------------------------------------- #
# _select_transitions: duplicate pair_index (Codex MINOR-6)                  #
# --------------------------------------------------------------------------- #

def test_select_transitions_rejects_duplicate_pair_index():
    tracks = [_track("A", 0.0, 400.0), _track("B", 300.0, 700.0),
             _track("C", 600.0, 1000.0)]
    transitions = [_transition(1, "A", "B"), _transition(1, "A", "B")]
    report = _report(tracks, transitions)
    with pytest.raises(ValueError, match="duplicate pair_index"):
        ete._select_transitions(report)


# --------------------------------------------------------------------------- #
# _verify_cross_side_transition_identity (Codex MAJOR-2)                     #
# --------------------------------------------------------------------------- #

def test_verify_cross_side_transition_identity_passes_when_sides_agree():
    side_data = {
        "A": {"transitions": {1: {"out_track": {"name": "One"}, "in_track": {"name": "Two"}}}},
        "B": {"transitions": {1: {"out_track": {"name": "One"}, "in_track": {"name": "Two"}}}},
    }
    ete._verify_cross_side_transition_identity(side_data, ["A", "B"], {1})  # must not raise


def test_verify_cross_side_transition_identity_rejects_a_mismatched_pair():
    side_data = {
        "A": {"transitions": {1: {"out_track": {"name": "One"}, "in_track": {"name": "Two"}}}},
        "B": {"transitions": {1: {"out_track": {"name": "One"}, "in_track": {"name": "THREE"}}}},
    }
    with pytest.raises(ValueError, match="different tracks across sides"):
        ete._verify_cross_side_transition_identity(side_data, ["A", "B"], {1})


# --------------------------------------------------------------------------- #
# _sanity_check_wav_duration (Codex MAJOR-3 mitigation)                      #
# --------------------------------------------------------------------------- #

def test_sanity_check_wav_duration_passes_within_tolerance(tmp_path):
    als = tmp_path / "Mix A.als"
    _write_als(als, bpm=120.0)  # 1 beat = 0.5s
    tempo_map = ete.resolve_tempo_map(als)
    side_data = {
        "A": {"report": {"tracks": [{"arr_end": 200.0}]},  # predicts 100s
             "tempo_map": tempo_map, "wav_duration_sec": 102.0},  # 2% off
    }
    ete._sanity_check_wav_duration(side_data, ["A"], tolerance=0.10)  # must not raise


def test_sanity_check_wav_duration_refuses_a_wildly_mismatched_render(tmp_path):
    als = tmp_path / "Mix A.als"
    _write_als(als, bpm=120.0)
    tempo_map = ete.resolve_tempo_map(als)
    side_data = {
        "A": {"report": {"tracks": [{"arr_end": 200.0}]},  # predicts 100s
             "tempo_map": tempo_map, "wav_duration_sec": 300.0},  # 3x too long
    }
    with pytest.raises(ValueError, match="possible wrong/swapped"):
        ete._sanity_check_wav_duration(side_data, ["A"], tolerance=0.10)


# --------------------------------------------------------------------------- #
# _verify_bounce_manifest / _verify_side_binding                             #
# (Codex MAJOR-1, 2026-09-14: the duration check alone doesn't catch a       #
# realistic same-length A/B swap - the strong manifest binding does)         #
# --------------------------------------------------------------------------- #

def _make_manifested_side(ab_root: Path, label: str, bpm: float = 120.0,
                          freq: float = 440.0):
    """A side with a real ALS/report AND a real, matching bounce manifest -
    the fully-bound case. `freq` differentiates each side's WAV CONTENT
    (not just length) so a same-length swap between two sides actually
    changes the hash."""
    import record_bounce_manifest as rbm

    side_dir = ab_root / label
    side_dir.mkdir(parents=True, exist_ok=True)
    als_path = side_dir / f"Mix {label}.als"
    _write_als(als_path, bpm=bpm)
    report_path = side_dir / f"Arranged {label}_ARRANGEMENT_REPORT.json"
    report_path.write_text(json.dumps({"tracks": [{"arr_end": 200.0}]}), encoding="utf-8")
    wav_path = side_dir.parent / f"render_{label}.wav"
    _write_wav(wav_path, seconds=1.0, freq=freq)
    rbm.record(ab_root, label, wav_path)
    return side_dir, als_path, report_path, wav_path


def test_verify_bounce_manifest_passes_when_everything_matches(tmp_path):
    ab_root = tmp_path / "AB"
    side_dir, als_path, report_path, wav_path = _make_manifested_side(ab_root, "A")
    manifest_path = side_dir / "Mix A.bounce_manifest.json"
    ete._verify_bounce_manifest(manifest_path, als_path, report_path, wav_path)  # must not raise


def test_verify_bounce_manifest_refuses_when_the_wav_has_changed_since(tmp_path):
    ab_root = tmp_path / "AB"
    side_dir, als_path, report_path, wav_path = _make_manifested_side(ab_root, "A")
    manifest_path = side_dir / "Mix A.bounce_manifest.json"
    _write_wav(wav_path, seconds=1.0, freq=999.0)  # re-bounced/replaced after recording
    with pytest.raises(ValueError, match="currently hashes to"):
        ete._verify_bounce_manifest(manifest_path, als_path, report_path, wav_path)


def test_verify_bounce_manifest_refuses_a_swapped_wav_of_similar_length(tmp_path):
    # The exact realistic case the duration-only check misses: two renders
    # of the SAME length, genuinely from different sides.
    ab_root = tmp_path / "AB"
    side_dir_a, als_a, report_a, wav_a = _make_manifested_side(ab_root, "A", freq=440.0)
    _side_dir_b, _als_b, _report_b, wav_b = _make_manifested_side(ab_root, "B", freq=550.0)
    manifest_a = side_dir_a / "Mix A.bounce_manifest.json"
    # Side A's manifest, but handed side B's (same-length) WAV by mistake.
    with pytest.raises(ValueError, match="currently hashes to"):
        ete._verify_bounce_manifest(manifest_a, als_a, report_a, wav_b)


def test_verify_side_binding_uses_the_manifest_when_present(tmp_path, capsys):
    ab_root = tmp_path / "AB"
    side_dir, als_path, report_path, wav_path = _make_manifested_side(ab_root, "A")
    tempo_map = ete.resolve_tempo_map(als_path)
    side_data = {
        "A": {"report": json.loads(report_path.read_text(encoding="utf-8")),
             "tempo_map": tempo_map, "wav_duration_sec": 999999.0,  # would FAIL the
             "wav": wav_path},                                      # weak check if used
    }
    ete._verify_side_binding(side_data, ["A"], ab_root, tolerance=0.10)  # must not raise
    assert "WARNING" not in capsys.readouterr().out


def test_verify_side_binding_falls_back_and_warns_when_no_manifest_exists(tmp_path, capsys):
    ab_root = tmp_path / "AB"
    als_path = ab_root / "A" / "Mix A.als"
    (ab_root / "A").mkdir(parents=True)
    _write_als(als_path, bpm=120.0)
    tempo_map = ete.resolve_tempo_map(als_path)
    wav_path = tmp_path / "render_A.wav"
    _write_wav(wav_path, seconds=100.0)
    side_data = {
        "A": {"report": {"tracks": [{"arr_end": 200.0}]},  # predicts 100s - matches
             "tempo_map": tempo_map, "wav_duration_sec": 100.0, "wav": wav_path},
    }
    binding = ete._verify_side_binding(side_data, ["A"], ab_root, tolerance=0.10)
    assert "WARNING" in capsys.readouterr().out
    assert binding == {"A": "weak"}


def test_verify_side_binding_reports_strong_for_a_manifested_side(tmp_path, capsys):
    ab_root = tmp_path / "AB"
    side_dir, als_path, report_path, wav_path = _make_manifested_side(ab_root, "A")
    tempo_map = ete.resolve_tempo_map(als_path)
    side_data = {
        "A": {"report": json.loads(report_path.read_text(encoding="utf-8")),
             "tempo_map": tempo_map, "wav_duration_sec": 999999.0, "wav": wav_path},
    }
    binding = ete._verify_side_binding(side_data, ["A"], ab_root, tolerance=0.10)
    assert binding == {"A": "strong"}
    assert side_data["A"]["bound_wav_identity"] == ete._wav_identity(wav_path)


# --------------------------------------------------------------------------- #
# _wav_identity / _check_wav_identity_unchanged                              #
# (Codex round 4, 2026-09-14: narrows the TOCTOU window between verifying a  #
# strong binding and later slicing that WAV, at negligible cost)            #
# --------------------------------------------------------------------------- #

def test_check_wav_identity_unchanged_passes_when_nothing_changed(tmp_path):
    wav = tmp_path / "a.wav"
    _write_wav(wav, seconds=1.0)
    d = {"wav": wav, "bound_wav_identity": ete._wav_identity(wav)}
    ete._check_wav_identity_unchanged(d, "A")  # must not raise


def test_check_wav_identity_unchanged_is_a_noop_when_never_bound():
    d = {"wav": Path("does/not/exist.wav")}  # no bound_wav_identity key at all
    ete._check_wav_identity_unchanged(d, "A")  # must not raise - nothing to compare


def test_check_wav_identity_unchanged_refuses_a_replaced_file(tmp_path):
    wav = tmp_path / "a.wav"
    _write_wav(wav, seconds=1.0)
    d = {"wav": wav, "bound_wav_identity": ete._wav_identity(wav)}
    _write_wav(wav, seconds=5.0)  # replaced with a different-length render
    with pytest.raises(ValueError, match="changed since it was verified"):
        ete._check_wav_identity_unchanged(d, "A")


def test_verify_side_binding_require_manifests_refuses_an_unmanifested_side(tmp_path):
    ab_root = tmp_path / "AB"
    als_path = ab_root / "A" / "Mix A.als"
    (ab_root / "A").mkdir(parents=True)
    _write_als(als_path, bpm=120.0)
    tempo_map = ete.resolve_tempo_map(als_path)
    side_data = {
        "A": {"report": {"tracks": [{"arr_end": 200.0}]},
             "tempo_map": tempo_map, "wav_duration_sec": 100.0,
             "wav": tmp_path / "render_A.wav"},
    }
    with pytest.raises(SystemExit, match="--require-bounce-manifests"):
        ete._verify_side_binding(side_data, ["A"], ab_root, tolerance=0.10,
                                 require_manifests=True)


# --------------------------------------------------------------------------- #
# _check_build_results                                                       #
# --------------------------------------------------------------------------- #

def test_check_build_results_refuses_a_side_that_failed_its_own_gates(tmp_path):
    results_path = tmp_path / "build_results.json"
    results_path.write_text(json.dumps({
        "A": {"stage": "complete", "ok": True},
        "B": {"stage": "reconcile", "ok": False},
    }), encoding="utf-8")
    with pytest.raises(SystemExit, match="did not pass"):
        ete._check_build_results(results_path, ["A", "B"])


def test_check_build_results_passes_when_all_sides_complete(tmp_path):
    results_path = tmp_path / "build_results.json"
    results_path.write_text(json.dumps({
        "A": {"stage": "complete", "ok": True},
        "B": {"stage": "complete", "ok": True},
    }), encoding="utf-8")
    ete._check_build_results(results_path, ["A", "B"])  # must not raise


# --------------------------------------------------------------------------- #
# NO automatic cross-run temp cleanup (Codex round 3, 2026-09-14): round 1   #
# added an unconditional sweep of every matching temp dir at startup, which  #
# round 2 found could delete a CONCURRENT invocation's live clips; round 1's #
# own age-threshold fix (only remove dirs > 1hr old) was then found by round #
# 3 to still not prove inactivity - a genuinely slow run past that threshold #
# could still lose its own directory to another invocation's cleanup.       #
# Codex's own "safest" fix was adopted: drop automatic cross-run cleanup     #
# entirely. Opaque per-clip filenames (tested via the end-to-end tests       #
# below) are what actually keep a leaked file from disclosing a side; a      #
# leftover directory is left for the OS's own temp housekeeping.            #
# --------------------------------------------------------------------------- #

def test_no_automatic_cross_run_temp_cleanup_function_exists():
    """Regression guard: if a `_cleanup_stale_temp_roots`-shaped function
    reappears, it should be a deliberate, reviewed re-decision - not a
    silent reintroduction of a mechanism two straight Codex rounds found
    genuine concurrency problems with."""
    assert not hasattr(ete, "_cleanup_stale_temp_roots")


# --------------------------------------------------------------------------- #
# end-to-end: main()                                                         #
# --------------------------------------------------------------------------- #

def _build_two_track_side(side_dir: Path, wav_dir: Path, label: str,
                          bpm: float, freq: float) -> Path:
    """One synthetic side: a 2-track, 1-transition ARRANGEMENT_REPORT, a
    matching Mix {label}.als, and a rendered whole-mix WAV long enough to
    cover the transition window with room either side."""
    side_dir.mkdir(parents=True, exist_ok=True)
    tracks = [_track("Track One", 0.0, 32.0), _track("Track Two", 16.0, 64.0)]
    transitions = [_transition(1, "Track One", "Track Two")]
    report = _report(tracks, transitions)
    (side_dir / f"Arranged {label}_ARRANGEMENT_REPORT.json").write_text(
        json.dumps(report), encoding="utf-8")
    _write_als(side_dir / f"Mix {label}.als", bpm=bpm)

    # 64 beats at bpm -> total seconds; a small tail beyond that, same order
    # of magnitude as a real bounce's reverb/fade tail (must stay within
    # _sanity_check_wav_duration's tolerance - see that test's own coverage
    # for the case where this legitimately should be refused).
    total_sec = 64.0 * 60.0 / bpm + 1.0
    wav_path = wav_dir / f"Mix {label}.wav"
    _write_wav(wav_path, seconds=total_sec, freq=freq)
    return wav_path


def test_main_end_to_end_seals_one_transition_without_leaking_side_labels(tmp_path):
    ab_root = tmp_path / "AB"
    wav_a = _build_two_track_side(ab_root / "A", tmp_path, "A", bpm=120.0, freq=440.0)
    wav_b = _build_two_track_side(ab_root / "B", tmp_path, "B", bpm=120.0, freq=550.0)

    out_dir = tmp_path / "Listening"
    argv = [
        "extract_transition_excerpts.py",
        "--ab-root", str(ab_root),
        "--side", f"A={wav_a}",
        "--side", f"B={wav_b}",
        "--twin-of", "A",
        "--seed", "1000",
        "--out-dir", str(out_dir),
    ]
    old_argv = sys.argv
    sys.argv = argv
    try:
        rc = ete.main()
    finally:
        sys.argv = old_argv
    assert rc == 0

    t01 = out_dir / "T01"
    listen_dir = t01 / "Listen"
    clips = sorted(listen_dir.glob("Clip *.wav"))
    # 2 sides + 1 twin = 3 clips
    assert len(clips) == 3
    mapping = json.loads((t01 / "_sealed" / "MAPPING.json").read_text(encoding="utf-8"))
    sides_present = {entry["side"] for entry in mapping["mapping"]}
    assert sides_present == {"A", "B", "A_twin"}

    # Blind means blind: no filename anywhere under out_dir discloses a side
    # label the way the raw (temp-dir-only) extraction names did.
    all_names = [p.name for p in out_dir.rglob("*") if p.is_file()]
    for name in all_names:
        assert "T01_A" not in name and "T01_B" not in name

    results = json.loads((out_dir / "_audit" / "extract_results.json").read_text(encoding="utf-8"))
    assert results["transitions"]["T01"]["ok"] is True
    # Neither side has a bounce manifest in this fixture - the run's own
    # output must say so, not just print a warning during the run (Codex
    # round 3, 2026-09-14).
    assert results["binding"] == {"A": "weak", "B": "weak"}


def test_main_end_to_end_equalizes_durations_when_sides_have_different_overlap_geometry(tmp_path):
    # The real regression this guards (Codex FATAL-1, 2026-09-14): side C's
    # introloop policy produced a genuinely longer overlap zone for the same
    # transition than A/B on the real Tech House Heldout project (147.69s vs
    # 103.38s) - an audible/visible duration tell despite randomised
    # filenames and stripped metadata. Reproduced synthetically here: side B
    # has a much wider overlap zone (out_track ends later) than side A for
    # the "same" transition, and every sealed clip must still come out the
    # same length.
    ab_root = tmp_path / "AB"

    # Both sides carry a third "Padding" track far in the future - purely to
    # give each side's render plenty of real length to extend into, mirroring
    # a real multi-track mix where the transition under test is only a small
    # slice of a much longer whole-mix WAV (a bare 2-track WAV sized tightly
    # to the transition, like _build_two_track_side's fixture, has no such
    # room and would legitimately hit its own file bounds - see
    # test_equalize_windows_raises_when_a_side_cannot_reach_the_target for
    # that failure mode covered in isolation).
    (ab_root / "A").mkdir(parents=True, exist_ok=True)
    tracks_a = [_track("Track One", 0.0, 32.0), _track("Track Two", 16.0, 64.0),
               _track("Padding", 600.0, 700.0)]
    report_a = _report(tracks_a, [_transition(1, "Track One", "Track Two")])
    (ab_root / "A" / "Arranged A_ARRANGEMENT_REPORT.json").write_text(
        json.dumps(report_a), encoding="utf-8")
    _write_als(ab_root / "A" / "Mix A.als", bpm=120.0)
    wav_a = tmp_path / "Mix A.wav"
    _write_wav(wav_a, seconds=700.0 * 60.0 / 120.0 + 1.0, freq=440.0)

    # Side B: "Track One" runs much longer, so this transition's natural
    # overlap zone is wider than A's for the "same" pair_index.
    (ab_root / "B").mkdir(parents=True, exist_ok=True)
    tracks_b = [_track("Track One", 0.0, 96.0), _track("Track Two", 16.0, 128.0),
               _track("Padding", 600.0, 700.0)]
    report_b = _report(tracks_b, [_transition(1, "Track One", "Track Two")])
    (ab_root / "B" / "Arranged B_ARRANGEMENT_REPORT.json").write_text(
        json.dumps(report_b), encoding="utf-8")
    _write_als(ab_root / "B" / "Mix B.als", bpm=120.0)
    wav_b = tmp_path / "Mix B.wav"
    _write_wav(wav_b, seconds=700.0 * 60.0 / 120.0 + 1.0, freq=550.0)

    out_dir = tmp_path / "Listening"
    argv = [
        "extract_transition_excerpts.py",
        "--ab-root", str(ab_root),
        "--side", f"A={wav_a}",
        "--side", f"B={wav_b}",
        "--twin-of", "A",
        "--seed", "2000",
        "--out-dir", str(out_dir),
    ]
    old_argv = sys.argv
    sys.argv = argv
    try:
        rc = ete.main()
    finally:
        sys.argv = old_argv
    assert rc == 0

    durations = [sf.info(str(p)).frames / sf.info(str(p)).samplerate
                for p in sorted((out_dir / "T01" / "Listen").glob("Clip *.wav"))]
    assert len(durations) == 3  # A, B, A_twin
    assert max(durations) - min(durations) < 0.01  # same duration to sample-rounding


def test_main_aborts_when_sides_have_different_transition_counts(tmp_path):
    ab_root = tmp_path / "AB"
    (ab_root / "A").mkdir(parents=True)
    (ab_root / "B").mkdir(parents=True)

    tracks_a = [_track("One", 0.0, 32.0), _track("Two", 16.0, 64.0),
               _track("Three", 48.0, 96.0)]
    report_a = _report(tracks_a, [_transition(1, "One", "Two"), _transition(2, "Two", "Three")])
    (ab_root / "A" / "Arranged A_ARRANGEMENT_REPORT.json").write_text(
        json.dumps(report_a), encoding="utf-8")
    _write_als(ab_root / "A" / "Mix A.als", bpm=120.0)

    tracks_b = [_track("One", 0.0, 32.0), _track("Two", 16.0, 64.0)]
    report_b = _report(tracks_b, [_transition(1, "One", "Two")])
    (ab_root / "B" / "Arranged B_ARRANGEMENT_REPORT.json").write_text(
        json.dumps(report_b), encoding="utf-8")
    _write_als(ab_root / "B" / "Mix B.als", bpm=120.0)

    wav_a = tmp_path / "Mix A.wav"
    _write_wav(wav_a, seconds=60.0)
    wav_b = tmp_path / "Mix B.wav"
    _write_wav(wav_b, seconds=60.0)

    argv = [
        "extract_transition_excerpts.py",
        "--ab-root", str(ab_root),
        "--side", f"A={wav_a}",
        "--side", f"B={wav_b}",
        "--twin-of", "A",
        "--seed", "1",
        "--out-dir", str(tmp_path / "out"),
    ]
    old_argv = sys.argv
    sys.argv = argv
    try:
        with pytest.raises(SystemExit, match="do not share the same transitions"):
            ete.main()
    finally:
        sys.argv = old_argv
