"""A break-skip shortens the OUTGOING too, and the plan must say so.

When the incoming enters on a short pre-drop break stacked on the outgoing's
outro, the pipeline drops that break and pulls the drop onto the swap. Two
things then shrink: the incoming (its break is gone) and the outgoing (its outro
is trimmed to the pulled marker by `split_clip_skip_before_end`).

Only the incoming's `arr_end` was being updated. The outgoing kept an `arr_end`
describing a track longer than the one actually written to the ALS, so:

  - the MixPlan froze an arrangement_end_beat that the file contradicts;
  - reconciliation compares the contract against real clip ends and fails;
  - anything reading arr_end - downstream overlap geometry, and the tempo arc's
    ramp windows - is computed from a track that does not exist.

`validate_arrangement_plan` cannot catch it: it checks the overlap against that
same `arr_end`, so the error is self-consistent and invisible.

Found by MiniMax reviewing the tempo-arc wiring, 2026-08-13.
"""

from types import SimpleNamespace

import pytest

from align_engine import FillCutSpec
from propose_arrangement import OverlapAnalysis, TrackInfo, _plan_marker_loops

SKIP_BARS = 4.0
SKIP_BEATS = SKIP_BARS * 4.0


def _section(name, label, arr_time, arr_end, src=0.0):
    return {"name": name, "label": label, "arr_time": arr_time, "arr_end": arr_end,
            "source_start_beats": src, "source_end_beats": src + (arr_end - arr_time)}


def _scenario(with_outro: bool):
    out_sections = [_section("drop_1", "drop", 0.0, 400.0)]
    if with_outro:
        out_sections.append(_section("outro_1", "outro", 400.0, 500.0, src=400.0))
    outgoing = TrackInfo(name="OUT", sections=out_sections,
                         arr_start=0.0, arr_end=500.0, bpm=124.0)
    incoming = TrackInfo(
        name="IN",
        sections=[_section("intro_1", "intro", 400.0, 432.0),
                  _section("break_1", "break", 432.0, 432.0 + SKIP_BEATS, src=32.0),
                  _section("drop_1", "drop", 432.0 + SKIP_BEATS, 900.0, src=48.0)],
        arr_start=400.0, arr_end=900.0, bpm=125.0)
    alignment = SimpleNamespace(fills_cuts=[
        FillCutSpec(kind="break_skip", skip_bars=SKIP_BARS, clip_name="break_1")])
    analysis = OverlapAnalysis(out_track="OUT", in_track="IN", pair_index=1,
                               overlap_start=400.0, overlap_end=500.0,
                               overlap_beats=100.0, overlap_bars=25.0, status="ok")
    return outgoing, incoming, alignment, analysis


def test_outgoing_end_shrinks_with_the_trimmed_outro():
    """The ALS trim is real, so the recorded geometry must follow it."""
    outgoing, incoming, alignment, analysis = _scenario(with_outro=True)
    before = outgoing.arr_end
    _plan_marker_loops(outgoing, incoming, alignment, analysis)
    assert analysis.outro_split is not None, "the outro should be trimmed"
    assert outgoing.arr_end == pytest.approx(before - SKIP_BEATS), (
        "outgoing arr_end must drop by the trimmed amount, or the plan describes "
        "a longer track than the ALS contains")


def test_incoming_end_still_shrinks():
    """The half that already worked must keep working."""
    outgoing, incoming, alignment, analysis = _scenario(with_outro=True)
    before = incoming.arr_end
    _plan_marker_loops(outgoing, incoming, alignment, analysis)
    assert incoming.arr_end == pytest.approx(before - SKIP_BEATS)


def test_no_outro_means_no_trim_and_no_adjustment():
    """No outro section, no ALS trim - so the outgoing must NOT be shortened.
    Decrementing unconditionally would invent a shortening that never happens."""
    outgoing, incoming, alignment, analysis = _scenario(with_outro=False)
    before = outgoing.arr_end
    _plan_marker_loops(outgoing, incoming, alignment, analysis)
    assert analysis.outro_split is None
    assert outgoing.arr_end == pytest.approx(before)
