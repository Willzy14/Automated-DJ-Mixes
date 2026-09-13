"""Tests for _outgoing_has_post_swap_content and the margin rule it feeds.

Sam (2026-09-12), correcting apply_automation's fixed overlap-length-only
margin rule: "sometimes the outgoing track can run all the way up to the
drop of the next track... so it could plausibly happen within [a few bars]
of the end of the track" - a swap sitting close to the overlap's end isn't
automatically wrong, it depends on whether the outgoing has anything left
to give. This is landing-order item (a) of the swap-first redesign
(MiniMax review, 2026-09-11): the detector plus the unified margin rule,
gated on sam_v1-style fixtures only - the interim_v1 frozen baseline
(Tests/test_alignment_baseline.py) is unaffected because this only ADDS a
field with a safe default (see baseline_alignments.json's PINNED fields,
none of which include outgoing_has_post_swap_content), and a corpus sweep
of all 380 real historical pairs found zero pairs whose actual clamp/error
outcome changes (Documentation/Plans/arranger-signal-rewiring/ - see the
2026-09-13 build session).
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

def test_cold_ending_bass_gone_returns_false():
    """Bass is already gone for good at/before the swap -> nothing left."""
    o = _track(n_bars=200, bass_out_bar=190.0, bass_out_is_end=True)
    assert AE._outgoing_has_post_swap_content(o, swap_bar=192.0) is False


def test_bass_gone_but_after_swap_is_not_cold_ending_by_that_signal():
    """bass_out_is_end is True, but the bass hasn't actually left YET at the
    swap point - the bass-gone signal doesn't apply until it fires."""
    o = _track(n_bars=200, bass_out_bar=195.0, bass_out_is_end=True)
    # swap is before the bass actually goes, and far from the track end
    assert AE._outgoing_has_post_swap_content(o, swap_bar=150.0) is True


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


def test_error_message_no_longer_claims_stale_report_as_the_only_cause():
    """MiniMax review, 2026-09-11: the old message asserted a clamp trigger
    'can only mean the report and the arranged ALS disagree', which is
    false - a correctly-built transition can legitimately land here. The
    new message must not make that specific false claim."""
    out_t, in_t = _long_overlap_pair()
    swaps = _swaps_for(out_t, in_t, 599.0, content=True)
    with pytest.raises(ValueError) as exc_info:
        plan_transitions([out_t, in_t], swaps)
    assert "can only mean" not in str(exc_info.value)
