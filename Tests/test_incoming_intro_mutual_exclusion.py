"""Mutual exclusion between the two incoming-intro mechanisms in
plan_fill_or_cut: last-drop `incoming_intro_loop` (CUE_CONFIG) and
`extend_incoming_entry` (TransitionPolicy). Both produce a
kind="incoming_intro" FillCutSpec; propose_arrangement stores only ONE such
spec per transition (analysis.in_intro_loop), and until this fix both blocks
could fire on the same transition - the second silently overwriting the
first's analysis bookkeeping while BOTH sets of loop clips still got
physically prepended (found in Codex's review of the SAM_V2 candidate plan,
2026-09-10, before any of this shipped).

Order: last-drop loop is tried first (stronger evidence - 5/6 of Fresh Mix
V2's reworked transitions land there, independently corroborated by House
10's T9); entry-extension is the fallback, only when the first did not fire.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

from automated_dj_mixes.transition_policy import SAM_V1  # noqa: E402


def _quality_context():
    import numpy as np
    from align_engine import LoopQualityContext

    clean = np.full(5000, 0.1, dtype=float)
    return LoopQualityContext(
        Path("synthetic__stemenv.npz"), 126.0, 0.0, 0.1,
        {name: clean.copy() for name in ("drums", "bass", "other", "vocals", "mix")},
    )


def _track(name, *, n_bars=128, sections, loop_windows=None, quality_context=True):
    from align_engine import Track

    return Track(
        name=name, bpm=126.0, spb=4 * 60.0 / 126.0, downbeat=0.0,
        n_bars=n_bars, sections=sections, bass_in_bar=0.0, bass_out_bar=120.0,
        last_min_bars=64, loop_windows=loop_windows or [],
        loop_quality_context=_quality_context() if quality_context else None,
    )


def _outgoing(**kw):
    # drop_1[0,64) break_1[64,96) drop_2[96,112) outro_1[112,128). Last drop
    # start = 96. A cue also lands at 96 (section:drop:start), so both
    # mechanisms target the SAME outgoing point when arr=112 (gap=16, clean
    # multiple of both SNAP_BARS and the 8/4-bar entry phrases).
    return _track("out", sections=[
        {"name": "drop_1", "label": "drop", "start_bar": 0.0, "end_bar": 64.0},
        {"name": "break_1", "label": "break", "start_bar": 64.0, "end_bar": 96.0},
        {"name": "drop_2", "label": "drop", "start_bar": 96.0, "end_bar": 112.0},
        {"name": "outro_1", "label": "outro", "start_bar": 112.0, "end_bar": 128.0},
    ], **kw)


def _incoming(**kw):
    return _track("in", sections=[
        {"name": "intro_1", "label": "intro", "start_bar": 0.0, "end_bar": 32.0},
        {"name": "drop_1", "label": "drop", "start_bar": 32.0, "end_bar": 128.0},
    ], loop_windows=[(0.0, 32.0)], **kw)


def _alignment(arr=112.0):
    from align_engine import Alignment
    return Alignment(
        "out", "in", arr - 3.0, "drop->drop", 0.0,
        arr, 128.0 - arr + 19.0, 3, alignment_policy="paired_landmarks_v2",
    )


def test_both_mechanisms_eligible_only_last_drop_loop_fires(monkeypatch):
    import align_engine as AE

    monkeypatch.setattr(AE, "CUE_CONFIG", AE.CueConfig(incoming_intro_loop=True))
    outgoing, incoming, alignment = _outgoing(), _incoming(), _alignment()

    specs = AE.plan_fill_or_cut(outgoing, incoming, alignment, SAM_V1)
    entries = [s for s in specs if s.kind == "incoming_intro"]

    assert len(entries) == 1, (
        f"expected exactly one incoming_intro spec (mutual exclusion), got "
        f"{len(entries)}: {[e.note for e in entries]}")
    assert "outgoing last drop" in entries[0].note, (
        "last-drop loop should win when both mechanisms are eligible on the "
        f"same cue - got: {entries[0].note!r}")
    assert any("suppressed" in n and "entry-extension" in n
               for n in alignment.notes), (
        "entry-extension's suppression must be visible in the report, not "
        f"silent - alignment.notes was: {alignment.notes}")


def test_entry_extension_fires_as_fallback_when_last_drop_loop_disabled(monkeypatch):
    import align_engine as AE

    # incoming_intro_loop left at its default (False) - block (1) cannot fire
    # in landmark mode, so entry-extension is the only eligible mechanism.
    outgoing, incoming, alignment = _outgoing(), _incoming(), _alignment()

    specs = AE.plan_fill_or_cut(outgoing, incoming, alignment, SAM_V1)
    entries = [s for s in specs if s.kind == "incoming_intro"]

    assert len(entries) == 1
    assert "entry extension" in entries[0].note
    # Shadow density instrumentation: attached, not gating (flat synthetic
    # envelope -> window RMS == track RMS -> delta 0.0 for every stem).
    assert entries[0].density_status == "measured"
    assert entries[0].density_score == pytest.approx(0.0, abs=1e-9)
    assert not any("suppressed" in n for n in alignment.notes)


def test_density_is_unmeasured_not_a_guess_when_no_cache():
    import align_engine as AE

    outgoing = _outgoing(quality_context=False)
    incoming, alignment = _incoming(), _alignment()

    specs = AE.plan_fill_or_cut(outgoing, incoming, alignment, SAM_V1)
    entries = [s for s in specs if s.kind == "incoming_intro"]

    assert len(entries) == 1, "absence of a density cache must not block selection"
    assert entries[0].density_score is None
    assert entries[0].density_status.startswith("unmeasured")


def test_measure_outgoing_density_normalises_against_the_tracks_own_level():
    """A window that is LOUDER than the track's own median reads positive;
    quieter reads negative. Not a fixed dB scale - relative to the SAME
    track, per Codex's review finding (raw RMS is not comparable across
    masters)."""
    import numpy as np
    import align_engine as AE

    hop = 0.1
    n = 2000
    # drums: loud in [0,50) frames, quiet after; a flat "mix"/"bass"/"vocals"/
    # "other" so only the drums delta is exercised deliberately.
    drums = np.full(n, 0.02, dtype=float)
    drums[:500] = 0.5   # ~50 bars at 126 bpm-ish granularity is plenty
    envelopes = {
        "drums": drums,
        "bass": np.full(n, 0.1, dtype=float),
        "other": np.full(n, 0.1, dtype=float),
        "vocals": np.full(n, 0.1, dtype=float),
        "mix": np.full(n, 0.1, dtype=float),
    }
    context = AE.LoopQualityContext(
        Path("synthetic__stemenv.npz"), 126.0, 0.0, hop, envelopes)
    track = _track("probe", sections=[
        {"name": "drop_1", "label": "drop", "start_bar": 0.0, "end_bar": 200.0},
    ], n_bars=200, quality_context=False)
    track.loop_quality_context = context

    loud_score, loud_status = AE._measure_outgoing_density(track, 0.0, 8.0)
    quiet_score, quiet_status = AE._measure_outgoing_density(track, 100.0, 108.0)

    assert loud_status == "measured" and quiet_status == "measured"
    assert loud_score > 0.0, "a window louder than the track's own average must read positive"
    assert quiet_score < loud_score


def test_density_baseline_is_the_median_not_whole_track_rms():
    """Regression pin for a real bug caught in round-2 code review (Codex,
    2026-09-10, executed and constructed this exact counterexample): the
    docstring always said "median", but the first implementation computed
    _rms_db(whole_envelope) - a plain RMS, not a median. RMS is dragged up
    by a single loud outlier, so a window sitting at the track's actual
    TYPICAL level scored as if it were ~20 dB quieter than "baseline",
    because one transient elsewhere inflated the RMS baseline. A 100-frame
    envelope, 99 frames at a constant -40 dB and one frame at 0 dB: a window
    measured well away from the transient, AT the constant -40 dB level,
    must read close to 0.0 (it IS the track's typical level) - not the
    ~-20 dB the RMS-baseline bug produced.
    """
    import numpy as np
    import align_engine as AE

    n = 100
    quiet = 10 ** (-40.0 / 20.0)
    env = np.full(n, quiet, dtype=float)
    env[0] = 1.0  # one 0 dB transient - must not dominate the baseline
    envelopes = {name: env.copy() for name in ("drums", "bass", "other", "vocals", "mix")}
    context = AE.LoopQualityContext(
        Path("synthetic__stemenv.npz"), 126.0, 0.0, 0.1, envelopes)
    # hop=0.1s, n=100 frames -> 10s total -> ~5.25 bars at 126 bpm. Measure a
    # window inside that span, away from the transient at frame 0.
    track = _track("probe", sections=[
        {"name": "drop_1", "label": "drop", "start_bar": 0.0, "end_bar": 5.0},
    ], n_bars=5, quality_context=False)
    track.loop_quality_context = context

    score, status = AE._measure_outgoing_density(track, 2.0, 3.0)

    assert status == "measured"
    assert score == pytest.approx(0.0, abs=0.5), (
        f"a window at the track's own typical level should score ~0.0, got "
        f"{score:.2f} dB - median baseline check regressed to an outlier-"
        f"sensitive one")


def _propose_track_info(name, arr_start, arr_end, sections, source_end=200.0):
    from propose_arrangement import TrackInfo
    return TrackInfo(name, sections, arr_start, arr_end, source_end=source_end)


def test_suppression_note_reaches_the_machine_readable_report(monkeypatch):
    """The report, not just the console/PNG title, must say WHY entry-
    extension didn't fire when last-drop loop already claimed the
    transition - Codex's review asked for this explicitly ("record...
    suppression reason... in the arrangement report")."""
    import align_engine as AE
    from propose_arrangement import OverlapAnalysis, _plan_marker_loops

    monkeypatch.setattr(AE, "CUE_CONFIG", AE.CueConfig(incoming_intro_loop=True))
    outgoing, incoming, alignment = _outgoing(), _incoming(), _alignment()

    # plan_fill_or_cut returns the specs list; it does not itself mutate
    # al.fills_cuts (propose_arrangement's own align call does that). Mirror
    # that wiring directly here, matching the sibling fractional-remainder
    # test's pattern.
    alignment.fills_cuts = AE.plan_fill_or_cut(outgoing, incoming, alignment, SAM_V1)

    out_info = _propose_track_info("out", 0.0, 448.0, [
        {"name": "drop_2", "label": "drop", "start_bar": 96.0, "end_bar": 112.0,
         "source_start_beats": 384.0, "source_end_beats": 448.0},
    ])
    in_info = _propose_track_info("in", 448.0, 960.0, [
        {"name": "intro_1", "label": "intro", "arr_time": 448.0, "arr_end": 576.0,
         "source_start_beats": 0.0, "source_end_beats": 128.0},
    ])
    analysis = OverlapAnalysis("out", "in", 1, 448.0, 512.0, 64.0, 16.0, "ok")

    _plan_marker_loops(out_info, in_info, alignment, analysis)

    assert "suppressed" in analysis.notes and "entry-extension" in analysis.notes, (
        f"analysis.notes did not carry the suppression reason: {analysis.notes!r}")


def test_density_reaches_the_machine_readable_report_when_entry_extension_fires():
    """Same report requirement, for the density value on the mechanism that
    actually USES it (entry-extension; the last-drop branch never sets
    density_score/status, matching _measure_outgoing_density's docstring)."""
    import align_engine as AE
    from propose_arrangement import OverlapAnalysis, _plan_marker_loops

    # incoming_intro_loop stays at its default False - only entry-extension
    # is eligible.
    outgoing, incoming, alignment = _outgoing(), _incoming(), _alignment()
    alignment.fills_cuts = AE.plan_fill_or_cut(outgoing, incoming, alignment, SAM_V1)

    out_info = _propose_track_info("out", 0.0, 448.0, [
        {"name": "drop_2", "label": "drop", "start_bar": 96.0, "end_bar": 112.0,
         "source_start_beats": 384.0, "source_end_beats": 448.0},
    ])
    in_info = _propose_track_info("in", 448.0, 960.0, [
        {"name": "intro_1", "label": "intro", "arr_time": 448.0, "arr_end": 576.0,
         "source_start_beats": 0.0, "source_end_beats": 128.0},
    ])
    analysis = OverlapAnalysis("out", "in", 1, 448.0, 512.0, 64.0, 16.0, "ok")

    _plan_marker_loops(out_info, in_info, alignment, analysis)

    assert "density" in analysis.notes, (
        f"analysis.notes did not carry the density value: {analysis.notes!r}")
