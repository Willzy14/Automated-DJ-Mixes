"""Burn list D15 (2026-09-22): the outgoing-outro-loop targeting fix in
`align_engine.plan_fill_or_cut`.

Sam, by eye in Ableton before bouncing a real mix, found the outro-loop
extension inconsistent across transitions: correctly reaching the incoming
track's next break in one case, undershooting it in another (a raw
Kick-Detector-V3 landmark beat a reachable, cleaner section boundary purely
by being numerically smaller - the section was never even attempted), and
not firing at all in a third case where a target WAS correctly identified
but every candidate's audio failed the loop-quality gate, silently
abandoning the attempt with zero trace.

Full root-cause + fix design: Documentation/Plans/d15-outro-loop-targeting-plan.md
Full corpus validation (0 regressions, 82 rescued, 11 legitimate target
changes, 122 cosmetic renames across the 380-pair corpus): this session's
transcript + the refreshed Tests/test_alignment_baseline.py frozen baseline.

These tests isolate each fix with monkeypatched quality-gate functions,
rather than depending on real audio math, for fast and deterministic
coverage of the control-flow itself.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))


def _track(name, *, n_bars, sections, musical_landmarks=None, loop_windows=None,
           bass_in=0.0, bass_out=None):
    from align_engine import Track

    return Track(
        name=name,
        bpm=128.0,
        spb=4 * 60.0 / 128.0,
        downbeat=0.0,
        n_bars=n_bars,
        sections=sections,
        bass_in_bar=bass_in,
        bass_out_bar=bass_out if bass_out is not None else float(n_bars) - 8,
        last_min_bars=64,
        loop_windows=loop_windows or [],
        musical_landmarks=musical_landmarks or [],
    )


def _alignment(*, handoff_bar_out, arr_offset_bars, overlap_bars=40.0):
    from align_engine import Alignment

    return Alignment(
        "out", "in", handoff_bar_out, "drop->outro", 0.0, arr_offset_bars,
        overlap_bars, 2, swap_beats=handoff_bar_out * 4.0,
        alignment_policy="paired_landmarks_v2",
    )


def _outgoing(outro_start=100.0, n_bars=110.0):
    return _track(
        "out", n_bars=n_bars,
        sections=[
            {"name": "drop_1", "label": "drop", "start_bar": 0.0, "end_bar": outro_start},
            {"name": "outro_1", "label": "outro", "start_bar": outro_start, "end_bar": n_bars},
        ],
    )


def test_section_candidate_preferred_over_a_nearer_landmark(monkeypatch):
    """D1: a `section:*` boundary must be tried BEFORE any `landmark:*`
    candidate, even when a landmark sits at a numerically smaller bar -
    T1's real bug (a kick-gap landmark 4 bars before Jones's real
    `break_1` won purely by being smaller; `section:break_1` was never
    even attempted)."""
    import align_engine as ae

    # current_incoming_bar = o.n_bars(110) - arr_offset_bars(90) = 20; every
    # candidate below needs to be >= 22 to be in range at all.
    outgoing = _outgoing()
    incoming = _track(
        "in", n_bars=60.0,
        sections=[{"name": "break_1", "label": "break", "start_bar": 28.0,
                    "end_bar": 32.0}],
        # Landmark ends at bar 24 (< the section's bar 28) - numerically
        # first in any bar-ascending merge, but must lose to the section.
        musical_landmarks=[{
            "landmark_id": "kick_gap_92_96", "type": "kick_dropout",
            "start_beat": 92.0, "end_beat": 96.0, "start_bar": 23.0, "end_bar": 24.0,
            "duration_beats": 4.0, "candidate_roles": [],
        }],
    )
    al = _alignment(handoff_bar_out=100.0, arr_offset_bars=90.0)

    # Any candidate's clean-drum search succeeds - the SELECTION order is
    # what's under test, not the audio quality gate itself.
    monkeypatch.setattr(ae, "pick_cue_bounded_drum_loop",
                         lambda *a, **k: (0.0, 2.0))

    specs = ae.plan_fill_or_cut(outgoing, incoming, al)
    tail = next(s for s in specs if s.kind == "outgoing_tail")
    assert tail.target_marker_name == "section:break_1"
    assert al.outgoing_loop_abandoned is None


def test_landmark_still_used_when_no_section_is_reachable(monkeypatch):
    """D1's fix is a PREFERENCE, not a removal - when no section candidate
    is even in range, the landmark tier still works exactly as before."""
    import align_engine as ae

    outgoing = _outgoing()
    incoming = _track(
        "in", n_bars=60.0,
        # Section is far beyond any reasonable loop_budget.
        sections=[{"name": "break_1", "label": "break", "start_bar": 500.0,
                    "end_bar": 504.0}],
        musical_landmarks=[{
            "landmark_id": "kick_gap_92_96", "type": "kick_dropout",
            "start_beat": 92.0, "end_beat": 96.0, "start_bar": 23.0, "end_bar": 24.0,
            "duration_beats": 4.0, "candidate_roles": [],
        }],
    )
    al = _alignment(handoff_bar_out=100.0, arr_offset_bars=90.0)
    monkeypatch.setattr(ae, "pick_cue_bounded_drum_loop",
                         lambda *a, **k: (0.0, 2.0))

    specs = ae.plan_fill_or_cut(outgoing, incoming, al)
    tail = next(s for s in specs if s.kind == "outgoing_tail")
    assert tail.target_marker_name == "landmark:kick_gap_92_96:end"


def test_fallback_fires_when_every_named_candidate_fails_quality(monkeypatch):
    """D2: a target IS correctly identified (within budget, reaches the
    locked swap) but `pick_cue_bounded_drum_loop` fails for every
    candidate - T3's real bug. Before this fix, `nxt`/`chunk` were only
    ever set together, so the existing "loop the outro section itself"
    last-resort (gated on `nxt is not None`) was structurally unreachable
    in landmark mode. Post-fix, that fallback must actually fire."""
    import align_engine as ae

    outgoing = _outgoing()
    incoming = _track(
        "in", n_bars=60.0,
        sections=[{"name": "break_1", "label": "break", "start_bar": 28.0,
                    "end_bar": 32.0}],
    )
    al = _alignment(handoff_bar_out=100.0, arr_offset_bars=90.0)

    # Primary search: nothing ever passes.
    monkeypatch.setattr(ae, "pick_cue_bounded_drum_loop", lambda *a, **k: None)
    # Middle fallback tier (latest o.loop_windows): also nothing.
    monkeypatch.setattr(ae, "pick_clean_drum_loop", lambda *a, **k: None)
    # Absolute last resort (loop the outro section itself): PASSES.
    monkeypatch.setattr(
        ae, "_assess_loop_candidate",
        lambda track, start, end, insert_bar: ae.LoopQualityResult(
            period_beats=end - start, silence_fraction=0.0,
            worst_beat_dip_db=0.0, insert_level_drop_db=0.0,
            self_similarity=1.0, failed_checks=(),
        ),
    )

    specs = ae.plan_fill_or_cut(outgoing, incoming, al)
    tail = next((s for s in specs if s.kind == "outgoing_tail"), None)
    assert tail is not None, "the last-resort fallback should have rescued this"
    assert tail.target_marker_name == "section:break_1"
    assert al.outgoing_loop_abandoned is None


def test_no_loop_when_the_fallback_also_fails_and_the_reason_is_recorded(monkeypatch):
    """D2b: when a target was identified but genuinely NOTHING in the
    outgoing's outro passes any quality gate (primary search, latest-
    window fallback, AND the absolute last resort all fail), the result
    must still be `loop_source: none` - the fix does not force a bad loop
    into existence - but it must be DISTINGUISHABLE from "no loop was
    needed" via `al.outgoing_loop_abandoned`, unlike before this fix."""
    import align_engine as ae

    outgoing = _outgoing()
    incoming = _track(
        "in", n_bars=60.0,
        sections=[{"name": "break_1", "label": "break", "start_bar": 28.0,
                    "end_bar": 32.0}],
    )
    al = _alignment(handoff_bar_out=100.0, arr_offset_bars=90.0)

    monkeypatch.setattr(ae, "pick_cue_bounded_drum_loop", lambda *a, **k: None)
    monkeypatch.setattr(ae, "pick_clean_drum_loop", lambda *a, **k: None)
    monkeypatch.setattr(
        ae, "_assess_loop_candidate",
        lambda track, start, end, insert_bar: ae.LoopQualityResult(
            period_beats=end - start, silence_fraction=None,
            worst_beat_dip_db=None, insert_level_drop_db=None,
            self_similarity=None, failed_checks=("insert_level_match",),
        ),
    )

    specs = ae.plan_fill_or_cut(outgoing, incoming, al)
    assert not any(s.kind == "outgoing_tail" for s in specs)
    assert al.outgoing_loop_abandoned is not None
    assert al.outgoing_loop_abandoned["target"] == "section:break_1"


def test_empty_incoming_sections_falls_through_to_landmarks_unchanged(monkeypatch):
    """A track with no sections at all (rare, but real data shape) must
    behave exactly like before this fix: the section tier is simply empty
    and the search falls straight through to the landmark tier - no
    crash, no different outcome for the landmark-only case."""
    import align_engine as ae

    outgoing = _outgoing()
    incoming = _track(
        "in", n_bars=60.0,
        sections=[],
        musical_landmarks=[{
            "landmark_id": "kick_gap_92_96", "type": "kick_dropout",
            "start_beat": 92.0, "end_beat": 96.0, "start_bar": 23.0, "end_bar": 24.0,
            "duration_beats": 4.0, "candidate_roles": [],
        }],
    )
    al = _alignment(handoff_bar_out=100.0, arr_offset_bars=90.0)
    monkeypatch.setattr(ae, "pick_cue_bounded_drum_loop",
                         lambda *a, **k: (0.0, 2.0))

    specs = ae.plan_fill_or_cut(outgoing, incoming, al)
    tail = next(s for s in specs if s.kind == "outgoing_tail")
    assert tail.target_marker_name == "landmark:kick_gap_92_96:end"


def test_report_landmark_candidates_includes_named_sections():
    """D3: the report-only candidate field must include the incoming's
    named section boundaries, not just raw musical landmarks - before
    this fix, a human debugging a case like T1 would never see the
    `section:*` candidate that should have won at all."""
    import align_engine as ae

    incoming = _track(
        "in", n_bars=60.0,
        sections=[{"name": "break_1", "label": "break", "start_bar": 20.0,
                    "end_bar": 24.0}],
    )
    outgoing = _outgoing()
    al = _alignment(handoff_bar_out=100.0, arr_offset_bars=10.0)

    candidates = ae.report_landmark_candidates(outgoing, incoming, al, 0.0, 40.0)
    sections = [c for c in candidates if c["type"] == "section"]
    assert len(sections) == 1
    assert sections[0]["landmark_id"] == "section:break_1"
    assert sections[0]["arrangement_start_beat"] == 40.0 + 20.0 * 4
    assert sections[0]["selected"] is False
