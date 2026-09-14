"""Tests for _outgoing_has_post_swap_content and the margin rule it feeds.

Sam (2026-09-12), correcting apply_automation's fixed overlap-length-only
margin rule: "sometimes the outgoing track can run all the way up to the
drop of the next track... so it could plausibly happen within [a few bars]
of the end of the track" - a swap sitting close to the overlap's end isn't
automatically wrong, it depends on whether the outgoing has anything left
to give. This is landing-order item (a) of the swap-first redesign
(MiniMax review, 2026-09-11).

Two rounds of review changed the shape of this fix from the first cut:
  - Codex (2026-09-13) found the detector's bass_out_is_end signal
    misclassified a real corpus case (Christoph - The Rise has 3 real bars
    of drums after its bass-out point) - dropped; the detector now checks
    distance to the track's own end only.
  - Codex also found, with an executed counterexample, that the new
    margin rule's default wiring reaches the LEGACY/non-landmark align_pair
    fallback too, which the 380-pair verification corpus does not cover
    and which interim_v1 can still hit - a real, previously-unverified
    swap-position change. Scoped the new rule to landmark-policy
    transitions only; legacy/non-landmark policies keep the exact old
    overlap-length-only margin.

The defensible claim, after both fixes: no regression observed against
the 380-pair landmark corpus (Tests/test_alignment_baseline.py's frozen
baseline is unaffected - see baseline_alignments.json's PINNED fields,
none of which include outgoing_has_post_swap_content) or the real held-out
Tech House Heldout B/C rebuild - NOT a proof that interim_v1 output can
never change, which the legacy-path counterexample disproves for the
unscoped version of this fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import align_engine as AE  # noqa: E402
from apply_automation import (  # noqa: E402
    TrackInfo,
    TransitionStyle,
    _normalise,
    plan_transitions,
)


def _track(name="Out", n_bars=200, bass_out_bar=None, bass_out_is_end=False,
           sections=None):
    return AE.Track(
        name=name, bpm=128.0, spb=60.0 / 128.0 * 4, downbeat=0.0,
        n_bars=n_bars, sections=sections or [],
        bass_in_bar=None, bass_out_bar=bass_out_bar,
        bass_out_is_end=bass_out_is_end,
    )


# --------------------------------------------------------------------------- #
# _outgoing_has_post_swap_content
# --------------------------------------------------------------------------- #

def test_bass_gone_but_real_content_remains_returns_true():
    """Real corpus regression (Codex review, 2026-09-13): Christoph - The
    Rise, n_bars=256, bass-out~253, but its outro (253-256) is a genuine
    3-bar drums-only tail. bass_out_is_end alone used to override this to
    False - bass ownership is not sufficient evidence the track is done;
    only real distance to the track's own end decides it now."""
    o = _track(n_bars=256, bass_out_bar=253.0, bass_out_is_end=True)
    assert AE._outgoing_has_post_swap_content(o, swap_bar=253.0) is True


def test_bass_gone_and_genuinely_at_the_end_returns_false():
    """Same bass-gone shape, but the swap really is within the hard floor
    of the track's own end - still correctly cold-ending, just decided by
    distance, not by the bass signal."""
    o = _track(n_bars=200, bass_out_bar=190.0, bass_out_is_end=True)
    assert AE._outgoing_has_post_swap_content(o, swap_bar=199.5) is False


def test_close_to_track_end_returns_false_even_without_bass_signal():
    """No bass_out data at all, but the swap is within the hard floor of the
    track's own real end - no time left regardless of what's playing."""
    o = _track(n_bars=200, bass_out_bar=None, bass_out_is_end=False)
    assert AE._outgoing_has_post_swap_content(o, swap_bar=199.5) is False


def test_real_content_past_swap_returns_true():
    o = _track(n_bars=200, bass_out_bar=None, bass_out_is_end=False)
    assert AE._outgoing_has_post_swap_content(o, swap_bar=150.0) is True


def test_at_exactly_the_floor_is_cold_ending():
    """Exactly MIN_REMAINING_CONTENT_BARS of the track left counts as no
    real content - the floor must be cleared, not just reached, matching
    the function's own strict '>' comparison."""
    o = _track(n_bars=200, bass_out_bar=None, bass_out_is_end=False)
    swap = 200.0 - AE.MIN_REMAINING_CONTENT_BARS
    assert AE._outgoing_has_post_swap_content(o, swap_bar=swap) is False


def test_just_past_the_floor_is_content():
    o = _track(n_bars=200, bass_out_bar=None, bass_out_is_end=False)
    swap = 200.0 - AE.MIN_REMAINING_CONTENT_BARS - 0.1
    assert AE._outgoing_has_post_swap_content(o, swap_bar=swap) is True


def test_effective_end_overrides_native_n_bars_for_a_tail_loop():
    """Codex review, 2026-09-13: real corpus case (Christoph -> A Studio)
    where the native end alone reads cold-ending (False), but a planned
    outgoing-tail loop extends real playback 8 bars past the swap - the
    caller (compute_aligned_positions, AFTER plan_fill_or_cut) must pass
    that extended end so the flag reflects what will actually play."""
    o = _track(n_bars=253)
    swap = 253.0
    assert AE._outgoing_has_post_swap_content(o, swap) is False  # native: cold
    assert AE._outgoing_has_post_swap_content(
        o, swap, effective_end=261.0
    ) is True  # loop-extended: real content remains


# --------------------------------------------------------------------------- #
# plan_transitions margin rule, via the report field
# --------------------------------------------------------------------------- #

def _sec(name, label, start, end):
    return {"name": name, "label": label, "arr_time": start, "arr_end": end}


def _long_overlap_pair():
    """Overlap 400..605 (51.25 bars -> well clear of the 24-bar quick-swap
    line), swap at 599 -> 6 beats (1.5 bars) from ov_end. Old rule (margin
    always 8 beats for overlap>=24 bars) would clamp/error this; the new
    rule only does that when the outgoing genuinely has content left."""
    out_t = TrackInfo("OutT", [_sec("drop_2", "drop", 300.0, 400.0),
                               _sec("outro_1", "outro", 400.0, 605.0)],
                      0.0, 605.0)
    in_t = TrackInfo("InT", [], 400.0, 1200.0)
    return out_t, in_t


def _swaps_for(out_t, in_t, beat, content):
    return {(_normalise(out_t.name), _normalise(in_t.name)): {
        "swap_beats": beat,
        "handoff_kind": "paired/landmark:kick_dropout:start->drop",
        "alignment_policy": "paired_landmarks_v2",
        "outgoing_has_post_swap_content": content,
    }}


def test_cold_ending_long_overlap_gets_the_lenient_margin():
    """The actual Freejak-shaped case: a long overlap whose outgoing is
    cold-ending should NOT need the full 8-beat fade margin."""
    out_t, in_t = _long_overlap_pair()
    swaps = _swaps_for(out_t, in_t, 599.0, content=False)
    plans = plan_transitions([out_t, in_t], swaps)
    assert plans[0].bass_swap == 599.0
    assert plans[0].swap_transforms == []


def test_legacy_policy_ignores_the_content_flag_entirely():
    """Codex review, 2026-09-13, executed counterexample: compute_aligned_
    positions populates outgoing_has_post_swap_content unconditionally,
    including for the legacy/non-landmark align_pair fallback - which
    interim_v1 can still hit and which the 380-pair verification corpus
    does NOT cover. A legacy-policy report claiming no content must still
    get the exact old overlap-length-only margin (8 beats here), not the
    new lenient one - only landmark policies get the new rule."""
    out_t, in_t = _long_overlap_pair()
    swaps = {(_normalise(out_t.name), _normalise(in_t.name)): {
        "swap_beats": 599.0,
        "handoff_kind": "drop->outro",
        "alignment_policy": "legacy_v1",
        "outgoing_has_post_swap_content": False,  # must be ignored
    }}
    plans = plan_transitions([out_t, in_t], swaps)
    # legacy policy clamps (never hard-errors) - the old 8-beat margin
    # still fires here even though content=False claims otherwise.
    assert plans[0].bass_swap == 597.0
    assert "clamped" in plans[0].swap_transforms


def test_non_cold_ending_long_overlap_still_needs_the_strict_margin():
    """Same geometry, but the outgoing genuinely has content left past the
    swap - the old, stricter 8-beat margin must still apply."""
    out_t, in_t = _long_overlap_pair()
    swaps = _swaps_for(out_t, in_t, 599.0, content=True)
    with pytest.raises(ValueError, match="outside the safe overlap"):
        plan_transitions([out_t, in_t], swaps)


def test_missing_report_field_defaults_to_the_old_strict_behaviour():
    """A report written before this field existed (or a hand-built one that
    never sets it) must not silently loosen a margin it never claimed -
    default True, same as test_aligner_swap_in_end_margin_is_hard_error's
    fixture never setting the key at all."""
    out_t, in_t = _long_overlap_pair()
    swaps = {(_normalise(out_t.name), _normalise(in_t.name)): {
        "swap_beats": 599.0,
        "handoff_kind": "paired/landmark:kick_dropout:start->drop",
        "alignment_policy": "paired_landmarks_v2",
        # outgoing_has_post_swap_content deliberately omitted
    }}
    with pytest.raises(ValueError, match="outside the safe overlap"):
        plan_transitions([out_t, in_t], swaps)


def test_error_message_names_more_than_one_possible_cause():
    """MiniMax review, 2026-09-11: the old comment above this raise asserted
    a clamp trigger 'can only mean the report and the arranged ALS
    disagree', which is false - a correctly-built transition can
    legitimately land here (this exact bug proved it). That phrase was
    only ever in the CODE COMMENT, never the actual raised message, so a
    test that just checks the phrase's absence from the message proves
    nothing (Codex review, 2026-09-13: it would have passed against the
    unmodified old code too). Check something the message actually
    changed instead: it must name more than one possible cause, and must
    carry the new diagnostic fields (margin, outgoing_has_post_swap_content)
    the old message never had."""
    out_t, in_t = _long_overlap_pair()
    swaps = _swaps_for(out_t, in_t, 599.0, content=True)
    with pytest.raises(ValueError) as exc_info:
        plan_transitions([out_t, in_t], swaps)
    msg = str(exc_info.value)
    assert "stale report" in msg and "align_engine picking a pair" in msg, (
        "message should name more than one possible cause, not assert a "
        "single one"
    )
    assert "margin 8 beats" in msg
    assert "outgoing_has_post_swap_content=True" in msg


# --------------------------------------------------------------------------- #
# two-stage-bass ordering guard (Codex review, 2026-09-13)
# --------------------------------------------------------------------------- #
#
# A pre-existing bug, not introduced by this change but made more reachable
# by it: the two-stage-bass rule only checked the full kill sat inside the
# overlap's boundary margin, never that it came AFTER the partial cut
# (swap). The new content-aware margin lets a swap sit closer to the
# overlap's end than before, which is exactly the shape that exposes it -
# an incoming build->drop near the overlap's START can now land before a
# swap the margin now permits near the overlap's END.

def test_two_stage_bass_refuses_a_kill_before_the_partial_cut():
    """Codex's exact executed counterexample: overlap 672-800 (32 bars),
    swap=796 (only reachable because outgoing_has_post_swap_content=False
    grants the lenient 4-beat margin here), incoming build->drop at 720 -
    before swap, not after. Without the ordering guard this would enable
    two-stage bass with kill(720) before partial(796): sorted automation
    would cut the bass, restore it, then partially cut it again."""
    out_t = TrackInfo("OutT", [_sec("outro_1", "outro", 700.0, 800.0)],
                      0.0, 800.0)
    in_t = TrackInfo("InT", [_sec("build_1", "build", 672.0, 720.0),
                             _sec("drop_1", "drop", 720.0, 900.0)],
                     672.0, 900.0)
    swaps = {(_normalise(out_t.name), _normalise(in_t.name)): {
        "swap_beats": 796.0,
        "handoff_kind": "paired/landmark:kick_dropout:start->drop",
        "alignment_policy": "paired_landmarks_v2",
        "outgoing_has_post_swap_content": False,
    }}
    plans = plan_transitions([out_t, in_t], swaps)
    assert plans[0].bass_swap == 796.0  # clears the margin cleanly first
    assert plans[0].two_stage_bass is False, (
        "two-stage bass must not activate when the full kill would land "
        "before the partial cut - it would sort the automation out of "
        "chronological order"
    )


# --------------------------------------------------------------------------- #
# Style selection (burn list C2, 2026-09-14): style used to come from        #
# overlap length ALONE - now consults the same outgoing_has_post_swap_content #
# signal the margin rule above already uses, with the same landmark-policy-  #
# only scoping (legacy path stays byte-identical).                          #
# --------------------------------------------------------------------------- #

def _short_overlap_pair(overlap_bars=17.0):
    """Overlap under the 24-bar quick-swap line - the actual Freejak->HARTY
    shape (17 bars) that exposed C2: the outgoing has real content left to
    fade across, but overlap length alone used to force QUICK_SWAP."""
    overlap_beats = overlap_bars * 4
    out_t = TrackInfo("OutT", [_sec("drop_2", "drop", 300.0, 400.0),
                               _sec("outro_1", "outro", 400.0, 400.0 + overlap_beats)],
                      0.0, 400.0 + overlap_beats)
    in_t = TrackInfo("InT", [], 400.0, 1200.0)
    return out_t, in_t


def test_short_overlap_with_real_content_now_gets_a_standard_fade():
    """The actual fix: a short overlap with real content left to fade
    across no longer gets crushed to an instant cut."""
    out_t, in_t = _short_overlap_pair(17.0)
    swaps = _swaps_for(out_t, in_t, 450.0, content=True)
    plans = plan_transitions([out_t, in_t], swaps)
    assert plans[0].style == TransitionStyle.STANDARD


def test_short_overlap_with_no_content_still_gets_quick_swap():
    """No real content left past the swap - an instant cut is still the
    correct choice, not a compromise."""
    out_t, in_t = _short_overlap_pair(17.0)
    swaps = _swaps_for(out_t, in_t, 450.0, content=False)
    plans = plan_transitions([out_t, in_t], swaps)
    assert plans[0].style == TransitionStyle.QUICK_SWAP


def test_short_overlap_legacy_policy_ignores_content_entirely():
    """Same scoping discipline as the margin rule: the legacy/non-landmark
    path has no outgoing_has_post_swap_content signal to consult and keeps
    the exact original overlap-length-only rule, unaffected by any content
    claim in the report."""
    out_t, in_t = _short_overlap_pair(17.0)
    swaps = {(_normalise(out_t.name), _normalise(in_t.name)): {
        "swap_beats": 450.0,
        "handoff_kind": "drop->outro",
        "alignment_policy": "legacy_v1",
        "outgoing_has_post_swap_content": True,  # must be ignored
    }}
    plans = plan_transitions([out_t, in_t], swaps)
    assert plans[0].style == TransitionStyle.QUICK_SWAP


def test_short_overlap_missing_content_field_defaults_to_standard():
    """A report written before this field existed defaults content to True
    (the same default the margin rule already uses for the identical
    missing-field case) - so a short-overlap landmark-policy transition
    with no field at all gets the gentler STANDARD fade, not QUICK_SWAP."""
    out_t, in_t = _short_overlap_pair(17.0)
    swaps = {(_normalise(out_t.name), _normalise(in_t.name)): {
        "swap_beats": 450.0,
        "handoff_kind": "paired/landmark:kick_dropout:start->drop",
        "alignment_policy": "paired_landmarks_v2",
        # outgoing_has_post_swap_content deliberately omitted
    }}
    plans = plan_transitions([out_t, in_t], swaps)
    assert plans[0].style == TransitionStyle.STANDARD


def test_long_overlap_still_gets_long_blend_regardless_of_content():
    """Sanity check on the reordered if/elif: overlap > 36 bars must still
    always win LONG_BLEND, whatever the content signal says. Swap held well
    clear of the overlap's own end margin (unlike the 599.0 fixture used
    elsewhere in this file) so this isolates style selection only, without
    also exercising the margin-violation error path."""
    out_t, in_t = _long_overlap_pair()
    for content in (True, False):
        swaps = _swaps_for(out_t, in_t, 550.0, content=content)
        plans = plan_transitions([out_t, in_t], swaps)
        assert plans[0].style == TransitionStyle.LONG_BLEND


def test_two_stage_bass_still_activates_when_kill_is_after_swap():
    """Same shape, but the build->drop genuinely sits after the swap -
    two-stage bass should still fire exactly as before."""
    out_t = TrackInfo("OutT", [_sec("outro_1", "outro", 700.0, 800.0)],
                      0.0, 800.0)
    in_t = TrackInfo("InT", [_sec("build_1", "build", 672.0, 710.0),
                             _sec("drop_1", "drop", 710.0, 900.0)],
                     672.0, 900.0)
    swaps = {(_normalise(out_t.name), _normalise(in_t.name)): {
        "swap_beats": 700.0,
        "handoff_kind": "paired/section:drop:end->drop",
        "alignment_policy": "paired_landmarks_v2",
        "outgoing_has_post_swap_content": True,
    }}
    plans = plan_transitions([out_t, in_t], swaps)
    assert plans[0].two_stage_bass is True
    assert plans[0].two_stage_kill_beat == 710.0
    assert plans[0].two_stage_kill_beat > plans[0].two_stage_bass_beat
