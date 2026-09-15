"""Tests for vocal-clash detection (burn list D6).

Two-step pattern (mirrors landmark_candidates, per MiniMax plan review
2026-09-15 - the original plan's Alignment-field precedent, C2's
outgoing_has_post_swap_content, was wrong; a scalar bool is a different
shape from a list-of-ranges, and OverlapAnalysis/TrackInfo has no
vocal_regions at all, only align_engine's Track does):

1. align_engine.report_vocal_regions - RAW evidence (both tracks' vocal_
   regions converted to arrangement-beats), computed once in
   compute_aligned_positions where Track objects are still in scope.
2. propose_arrangement._finalize_vocal_clash - intersects that raw
   evidence against the FINAL, post-loop overlap window once
   OverlapAnalysis knows it, writing OverlapAnalysis.vocal_clash_ranges.

Report-only throughout - never gates or rejects anything.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Source"))

from align_engine import Track, report_vocal_regions, Alignment  # noqa: E402
from propose_arrangement import (  # noqa: E402
    OverlapAnalysis,
    _finalize_vocal_clash,
)


def _track(name: str, vocal_regions: list[tuple[float, float]]) -> Track:
    return Track(
        name=name, bpm=128.0, spb=60.0 / 128.0, downbeat=0.0, n_bars=64,
        sections=[], bass_in_bar=None, bass_out_bar=None,
        vocal_regions=vocal_regions,
    )


# ── report_vocal_regions (align_engine.py) ──────────────────────────────

def test_native_bars_convert_to_arrangement_beats_via_track_start_offset():
    outgoing = _track("Out", vocal_regions=[(10.0, 14.0)])
    incoming = _track("In", vocal_regions=[(2.0, 6.0)])

    evidence = report_vocal_regions(
        outgoing, incoming, outgoing_start_beat=0.0, incoming_start_beat=100.0)

    # outgoing starts at arrangement beat 0: bar 10 -> beat 40, bar 14 -> beat 56
    assert evidence["outgoing"] == [(40.0, 56.0)]
    # incoming starts at arrangement beat 100: bar 2 -> beat 108, bar 6 -> beat 124
    assert evidence["incoming"] == [(108.0, 124.0)]


def test_no_vocal_regions_on_either_track_returns_empty_lists():
    outgoing = _track("Out", vocal_regions=[])
    incoming = _track("In", vocal_regions=[])

    evidence = report_vocal_regions(outgoing, incoming, 0.0, 0.0)

    assert evidence == {"outgoing": [], "incoming": []}


def test_multiple_vocal_regions_on_one_track_all_convert():
    outgoing = _track("Out", vocal_regions=[(0.0, 4.0), (20.0, 24.0)])
    incoming = _track("In", vocal_regions=[])

    evidence = report_vocal_regions(outgoing, incoming, 0.0, 0.0)

    assert evidence["outgoing"] == [(0.0, 16.0), (80.0, 96.0)]


# ── _finalize_vocal_clash (propose_arrangement.py) ──────────────────────

def _alignment_with_evidence(outgoing_ranges, incoming_ranges) -> Alignment:
    al = Alignment(
        out_name="Out", in_name="In", handoff_bar_out=0.0,
        handoff_kind="bass_out", anchor_bar_in=0.0, arr_offset_bars=0.0,
        overlap_bars=17.0, score=0,
    )
    al.vocal_regions_arrangement = {
        "outgoing": outgoing_ranges, "incoming": incoming_ranges,
    }
    return al


def _analysis(overlap_start: float, overlap_end: float) -> OverlapAnalysis:
    return OverlapAnalysis(
        out_track="Out", in_track="In", pair_index=1,
        overlap_start=overlap_start, overlap_end=overlap_end,
        overlap_beats=overlap_end - overlap_start,
        overlap_bars=(overlap_end - overlap_start) / 4.0, status="ok",
    )


def test_genuinely_overlapping_vocals_inside_the_window_are_flagged():
    al = _alignment_with_evidence(
        outgoing_ranges=[(100.0, 120.0)], incoming_ranges=[(110.0, 130.0)])
    analysis = _analysis(overlap_start=90.0, overlap_end=140.0)

    _finalize_vocal_clash(al, analysis)

    assert len(analysis.vocal_clash_ranges) == 1
    clash = analysis.vocal_clash_ranges[0]
    assert clash["clash_range"] == (110.0, 120.0)
    assert clash["outgoing_range"] == (100.0, 120.0)
    assert clash["incoming_range"] == (110.0, 130.0)


def test_vocals_present_on_both_but_not_overlapping_in_time_is_no_clash():
    """A vocal tail on the outgoing ending well before the incoming's
    vocal entry begins is NOT a clash - both present, never colliding."""
    al = _alignment_with_evidence(
        outgoing_ranges=[(100.0, 105.0)], incoming_ranges=[(110.0, 120.0)])
    analysis = _analysis(overlap_start=90.0, overlap_end=140.0)

    _finalize_vocal_clash(al, analysis)

    assert analysis.vocal_clash_ranges == []


def test_vocals_overlap_each_other_but_outside_the_transition_window():
    """Genuinely overlapping vocals that both sit outside this
    transition's own overlap window must not be flagged here - they
    belong to a different transition (or no transition at all)."""
    al = _alignment_with_evidence(
        outgoing_ranges=[(10.0, 20.0)], incoming_ranges=[(15.0, 25.0)])
    analysis = _analysis(overlap_start=100.0, overlap_end=140.0)

    _finalize_vocal_clash(al, analysis)

    assert analysis.vocal_clash_ranges == []


def test_one_track_has_no_vocal_regions_no_crash_empty_result():
    al = _alignment_with_evidence(outgoing_ranges=[], incoming_ranges=[(110.0, 130.0)])
    analysis = _analysis(overlap_start=90.0, overlap_end=140.0)

    _finalize_vocal_clash(al, analysis)

    assert analysis.vocal_clash_ranges == []


def test_straddling_vocal_reports_its_full_extent_not_the_clamped_sliver():
    """A vocal starting well before the transition window must still show
    its REAL full extent in outgoing_range/incoming_range - the clamp only
    decides WHETHER a clash is in play, not what gets reported (MiniMax
    code review, 2026-09-15: the first draft reported the clamped sliver,
    losing "this vocal actually started 40 beats earlier")."""
    al = _alignment_with_evidence(
        outgoing_ranges=[(50.0, 110.0)], incoming_ranges=[(95.0, 105.0)])
    analysis = _analysis(overlap_start=90.0, overlap_end=140.0)

    _finalize_vocal_clash(al, analysis)

    assert len(analysis.vocal_clash_ranges) == 1
    clash = analysis.vocal_clash_ranges[0]
    # Full, unclamped outgoing extent (started at 50, well before the
    # window) - not (90.0, 110.0), the clamped sliver.
    assert clash["outgoing_range"] == (50.0, 110.0)
    assert clash["incoming_range"] == (95.0, 105.0)
    # The clash itself is still correctly bounded to what's actually
    # audible during the transition.
    assert clash["clash_range"] == (95.0, 105.0)


def test_multiple_disjoint_clashes_in_one_transition_all_returned():
    al = _alignment_with_evidence(
        outgoing_ranges=[(100.0, 110.0), (130.0, 140.0)],
        incoming_ranges=[(105.0, 115.0), (135.0, 145.0)],
    )
    analysis = _analysis(overlap_start=90.0, overlap_end=150.0)

    _finalize_vocal_clash(al, analysis)

    assert len(analysis.vocal_clash_ranges) == 2
    ranges = {c["clash_range"] for c in analysis.vocal_clash_ranges}
    assert ranges == {(105.0, 110.0), (135.0, 140.0)}


def test_al_is_none_leaves_vocal_clash_ranges_empty_no_crash():
    """analyse_overlap only calls _finalize_vocal_clash when al is not
    None, but the function itself must degrade gracefully (not crash) if
    ever called directly with al=None - getattr's default handles it."""
    analysis = _analysis(overlap_start=90.0, overlap_end=140.0)

    _finalize_vocal_clash(None, analysis)

    assert analysis.vocal_clash_ranges == []


def test_cross_layer_consistency_same_vocal_lands_at_same_beat_as_landmark_would():
    """Same conversion math as report_landmark_candidates uses
    (track_start + native_position*4.0 for bars) - a vocal at native bar
    10 on a track starting at arrangement beat 200 must land at beat
    200 + 40 = 240, matching how any OTHER beat-space consumer in this
    codebase would place the same native-bar position."""
    outgoing = _track("Out", vocal_regions=[(10.0, 14.0)])
    incoming = _track("In", vocal_regions=[])

    evidence = report_vocal_regions(outgoing, incoming, 200.0, 0.0)

    assert evidence["outgoing"][0] == (240.0, 256.0)
