"""A partial intro trim must advance the incoming's arr_start.

`intro_cut` records a front-trim of the incoming's intro clip (`cut_to_bar`
bars off the front, keeping the rest) and the ALS writer applies it later via
`apply_loops.trim_named_clip_front`, which physically moves the clip forward
in the arrangement (its `Time` and `LoopStart` both shift by `trim_beats`).
After the trim, the track's real arrangement start is `old_start + trim_beats`.

The in-memory `in_track.arr_start` was being left at the pre-trim value, so:
  - `validate_arrangement_plan` checked the overlap against a stale value;
  - the tempo arc's `ramp_exposure()` windows were computed from a track that
    does not exist;
  - the frozen MixPlan contract described a track starting `trim_beats` too
    early and reconciliation against the written ALS failed.

The fix mirrors the sibling `incoming_intro` branch's own position update:
advance `in_track.arr_start` by exactly `trim_beats` (`== cut_to_bar * 4.0`).

Found by MiniMax reviewing the tempo-arc wiring, 2026-08-14.
"""

from types import SimpleNamespace

import pytest

from align_engine import FillCutSpec
from propose_arrangement import OverlapAnalysis, TrackInfo, _plan_marker_loops

CUT_TO_BAR = 8.0   # arbitrary, > 0; any value would do for the invariant
TRIM_BEATS = CUT_TO_BAR * 4.0


def _section(name, label, arr_time, arr_end, src=0.0):
    return {"name": name, "label": label, "arr_time": arr_time, "arr_end": arr_end,
            "source_start_beats": src, "source_end_beats": src + (arr_end - arr_time)}


def _scenario():
    """In track whose intro is longer than the cut, so the cut lands INSIDE
    the intro (the partial-trim guard's pre-condition)."""
    intro_start = 400.0
    outgoing = TrackInfo(
        name="OUT",
        sections=[_section("drop_1", "drop", 0.0, 400.0)],
        arr_start=0.0, arr_end=400.0, bpm=124.0,
    )
    incoming = TrackInfo(
        name="IN",
        sections=[_section("intro_1", "intro", intro_start, intro_start + TRIM_BEATS * 2),
                  _section("drop_1", "drop", intro_start + TRIM_BEATS * 2, 900.0)],
        arr_start=intro_start, arr_end=900.0, bpm=125.0,
    )
    alignment = SimpleNamespace(fills_cuts=[
        FillCutSpec(kind="intro_cut", cut_to_bar=CUT_TO_BAR)])
    analysis = OverlapAnalysis(out_track="OUT", in_track="IN", pair_index=1,
                               overlap_start=400.0, overlap_end=400.0,
                               overlap_beats=0.0, overlap_bars=0.0, status="ok")
    return outgoing, incoming, alignment, analysis


def test_incoming_arr_start_advances_by_trim_beats():
    """The physical trim moves the clip forward by trim_beats, so the
    in-memory arr_start must follow. Without the fix this stays at the
    pre-trim value and the frozen contract disagrees with the written ALS."""
    outgoing, incoming, alignment, analysis = _scenario()
    before = incoming.arr_start
    _plan_marker_loops(outgoing, incoming, alignment, analysis)
    assert analysis.intro_trim == ("IN", "intro_1", TRIM_BEATS), (
        "the intro_trim spec must still be recorded (the only previously-"
        "working half of the branch)"
    )
    assert incoming.arr_start == pytest.approx(before + TRIM_BEATS), (
        "incoming arr_start must advance by exactly trim_beats (= cut_to_bar*4); "
        "before the fix it stayed at the pre-trim value"
    )


def test_intro_cut_with_no_sections_records_nothing_and_does_not_move_start():
    """Defensive: with no intro section the branch is a no-op, so arr_start
    must not be touched. Guards against an unconditional += blowing up on
    empty sections."""
    outgoing = TrackInfo(name="OUT", sections=[], arr_start=0.0, arr_end=0.0)
    incoming = TrackInfo(name="IN", sections=[], arr_start=100.0, arr_end=100.0)
    alignment = SimpleNamespace(fills_cuts=[
        FillCutSpec(kind="intro_cut", cut_to_bar=CUT_TO_BAR)])
    analysis = OverlapAnalysis("OUT", "IN", 1, 0.0, 0.0, 0.0, 0.0, "ok")

    _plan_marker_loops(outgoing, incoming, alignment, analysis)

    assert analysis.intro_trim is None
    assert incoming.arr_start == pytest.approx(100.0)