"""Tests for generate_report's selected_style field (burn list C9).

Before this fix, ARRANGEMENT_REPORT.json's selected_style was computed from
overlap length alone - a second, unpatched copy of the exact rule burn list
C2 fixed in apply_automation.py's real style selection. Report-only (grepped:
no reader anywhere in Source/ or Tests/), but silently disagreed with what
apply_automation.py actually built for any transition C2's fix changes.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Source"))

from propose_arrangement import (  # noqa: E402
    ArrangementPlan,
    OverlapAnalysis,
    generate_report,
)
from align_engine import Alignment  # noqa: E402


def _overlap(overlap_bars: float) -> OverlapAnalysis:
    return OverlapAnalysis(
        out_track="Out", in_track="In", pair_index=1,
        overlap_start=0.0, overlap_end=overlap_bars * 4,
        overlap_beats=overlap_bars * 4, overlap_bars=overlap_bars,
        status="ok",
    )


def _alignment(alignment_policy: str, outgoing_has_post_swap_content: bool) -> Alignment:
    return Alignment(
        out_name="Out", in_name="In", handoff_bar_out=0.0,
        handoff_kind="bass_out", anchor_bar_in=0.0, arr_offset_bars=0.0,
        overlap_bars=17.0, score=0,
        alignment_policy=alignment_policy,
        outgoing_has_post_swap_content=outgoing_has_post_swap_content,
    )


def _style(ov: OverlapAnalysis, al: Alignment | None, tmp_path: Path) -> str:
    plan = ArrangementPlan(
        tracks=[], overlaps=[ov], shifts=[], loops=[],
        alignments=[al] if al is not None else [],
    )
    out = generate_report(plan, tmp_path / "Arranged.als")
    data = json.loads(out.read_text(encoding="utf-8"))
    return data["transitions"][0]["selected_style"]


def test_landmark_policy_short_overlap_with_content_is_now_standard(tmp_path):
    """The exact case C2 fixed in apply_automation.py: a short overlap whose
    outgoing genuinely still has content left to fade must no longer read
    quick_swap in the report either."""
    ov = _overlap(17.0)
    al = _alignment("paired_landmarks_v2", outgoing_has_post_swap_content=True)
    assert _style(ov, al, tmp_path) == "standard"


def test_landmark_policy_short_overlap_no_content_stays_quick_swap(tmp_path):
    """A genuinely cold-ending outgoing keeps quick_swap - same as before,
    same as apply_automation.py's own rule."""
    ov = _overlap(17.0)
    al = _alignment("paired_landmarks_v2", outgoing_has_post_swap_content=False)
    assert _style(ov, al, tmp_path) == "quick_swap"


def test_legacy_policy_short_overlap_ignores_content_entirely(tmp_path):
    """Legacy/non-landmark alignments keep the exact original overlap-
    length-only rule, byte-identical to before - even with content=True,
    a short overlap on the legacy path is still quick_swap."""
    ov = _overlap(17.0)
    al = _alignment("legacy_v1", outgoing_has_post_swap_content=True)
    assert _style(ov, al, tmp_path) == "quick_swap"


def test_long_overlap_still_long_blend_regardless_of_content(tmp_path):
    ov = _overlap(40.0)
    al = _alignment("paired_landmarks_v2", outgoing_has_post_swap_content=True)
    assert _style(ov, al, tmp_path) == "long_blend"

    al_cold = _alignment("paired_landmarks_v2", outgoing_has_post_swap_content=False)
    assert _style(ov, al_cold, tmp_path) == "long_blend"


def test_no_matching_alignment_falls_back_to_overlap_length_only(tmp_path):
    """pair_index with no matching entry in plan.alignments (al is None) -
    the report must not crash, and falls back to the original rule."""
    ov = _overlap(17.0)
    assert _style(ov, None, tmp_path) == "quick_swap"

    ov_long = _overlap(40.0)
    assert _style(ov_long, None, tmp_path) == "long_blend"


def test_medium_overlap_is_standard_in_every_case(tmp_path):
    """24-36 bars: STANDARD regardless of policy or content - the middle
    band was never gated by content awareness in apply_automation.py
    either, only the two boundary rules (short/long) are content-aware or
    fixed."""
    ov = _overlap(30.0)
    for policy in ("paired_landmarks_v2", "legacy_v1"):
        for content in (True, False):
            al = _alignment(policy, content)
            assert _style(ov, al, tmp_path) == "standard"
