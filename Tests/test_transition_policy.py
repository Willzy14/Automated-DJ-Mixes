"""Transition policy is the single source of truth for overlap/loop geometry.

The caps used to be declared three times (align_engine in bars,
propose_arrangement in beats, mix_plan in beats) with the loop budget derived
by subtraction. Raising one copy produced a fail-closed rejection in a
different module with a misleading message. These tests pin the values and,
more importantly, assert the modules still agree with the policy - so a future
re-declaration fails here instead of at ALS-write time.
"""

import pytest

from automated_dj_mixes.transition_policy import (
    INTERIM_V1,
    SAM_V1,
    bars_to_beats,
    beats_to_bars,
    get_policy,
)


def test_interim_values_match_historical_constants():
    """These exact numbers shipped every accepted mix - do not drift."""
    assert INTERIM_V1.min_overlap_beats == 64.0            # 16 bars
    assert INTERIM_V1.max_overlap_beats == 192.0           # 48 bars
    assert INTERIM_V1.max_landmark_overlap_beats == 256.0  # 64 bars
    assert INTERIM_V1.max_loop_extension_beats == 128.0    # 32 bars
    assert INTERIM_V1.max_loop_repeats == 8


def test_bar_beat_conversion_round_trips():
    for bars in (16, 32, 48, 64, 80):
        assert beats_to_bars(bars_to_beats(bars)) == bars


def test_interim_refuses_the_extended_lane():
    """A policy without the extended lane must refuse it, not silently allow."""
    assert INTERIM_V1.max_extended_overlap_beats is None
    assert "evidence_extended_80" not in INTERIM_V1.allowed_overlap_policies
    with pytest.raises(ValueError, match="does not permit the extended lane"):
        INTERIM_V1.cap_for("evidence_extended_80")


def test_sam_v1_extended_cap_covers_the_measured_correction():
    """T3 of the Sam-Tweaks correction is 295.29 beats - the reason the lane
    exists at all. It must fit, and 80 bars must remain the ceiling."""
    assert SAM_V1.cap_for("evidence_extended_80") == 320.0
    assert 295.29 <= SAM_V1.cap_for("evidence_extended_80")
    assert "evidence_extended_80" in SAM_V1.allowed_overlap_policies


def test_sam_v1_does_not_relax_the_standard_lanes():
    """Only the extended lane is new; standard/landmark caps are untouched."""
    assert SAM_V1.cap_for("standard_48") == INTERIM_V1.cap_for("standard_48")
    assert (SAM_V1.cap_for("named_landmark_64")
            == INTERIM_V1.cap_for("named_landmark_64"))
    assert SAM_V1.min_overlap_beats == INTERIM_V1.min_overlap_beats


def test_unknown_policy_names_are_rejected():
    with pytest.raises(ValueError, match="unknown overlap policy"):
        INTERIM_V1.cap_for("anything_goes")
    with pytest.raises(ValueError, match="unknown transition policy"):
        get_policy("no_such_policy")


def test_policies_are_frozen():
    """Policy must not be mutable - the A/B depends on one run being unable to
    alter the other's geometry."""
    with pytest.raises(Exception):
        INTERIM_V1.max_overlap_beats = 999.0


def test_modules_have_not_redeclared_the_caps():
    """Anti-drift: every consumer must still agree with the policy.

    This is the test that would have caught the original triplication.
    """
    import align_engine
    import apply_loops
    import propose_arrangement
    from automated_dj_mixes import mix_plan

    assert align_engine.MAX_OVERLAP_BARS == beats_to_bars(
        INTERIM_V1.max_overlap_beats)
    assert align_engine.MAX_LANDMARK_OVERLAP_BARS == beats_to_bars(
        INTERIM_V1.max_landmark_overlap_beats)
    assert align_engine.MAX_LOOP_EXTENSION_BARS == beats_to_bars(
        INTERIM_V1.max_loop_extension_beats)
    assert align_engine.MAX_LOOP_REPEATS == INTERIM_V1.max_loop_repeats

    for module in (propose_arrangement, mix_plan):
        assert module.MIN_OVERLAP_BEATS == INTERIM_V1.min_overlap_beats
        assert module.MAX_OVERLAP_BEATS == INTERIM_V1.max_overlap_beats
        assert (module.MAX_LANDMARK_OVERLAP_BEATS
                == INTERIM_V1.max_landmark_overlap_beats)

    assert apply_loops.MAX_LOOP_REPEATS == INTERIM_V1.max_loop_repeats
    assert (apply_loops.MAX_LOOP_EXTENSION_BEATS
            == INTERIM_V1.max_loop_extension_beats)


def test_loop_budget_is_independent_of_the_overlap_cap():
    """It used to be MAX_OVERLAP_BARS - PHRASE_GRID, so raising the overlap cap
    silently raised the loop budget. sam_v1 raises the ceiling via the extended
    lane and must NOT drag the loop budget up with it."""
    assert (SAM_V1.max_loop_extension_beats
            == INTERIM_V1.max_loop_extension_beats)
