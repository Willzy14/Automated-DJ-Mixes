"""Tests for automation: gain offsets and transition generation."""

import pytest

from automated_dj_mixes.automation import (
    calculate_gain_offsets,
    generate_transition,
    AutomationPoint,
    TransitionAutomation,
)


def test_gain_offsets_empty():
    assert calculate_gain_offsets([]) == []


def test_gain_offsets_single():
    assert calculate_gain_offsets([-8.0]) == [0.0]


def test_gain_offsets_all_same():
    offsets = calculate_gain_offsets([-8.0, -8.0, -8.0])
    assert offsets == [0.0, 0.0, 0.0]


def test_gain_offsets_quietest_gets_zero():
    offsets = calculate_gain_offsets([-6.0, -10.0, -8.0])
    assert offsets[1] == 0.0


def test_gain_offsets_louder_tracks_reduced():
    offsets = calculate_gain_offsets([-6.0, -10.0, -8.0])
    assert offsets[0] == pytest.approx(-4.0)
    assert offsets[2] == pytest.approx(-2.0)


def test_gain_offsets_capped():
    offsets = calculate_gain_offsets([-2.0, -20.0], max_reduction_db=12.0)
    assert offsets[0] == -12.0
    assert offsets[1] == 0.0


def test_transition_returns_all_fields():
    t = generate_transition(100.0, 32)
    assert isinstance(t, TransitionAutomation)
    assert len(t.outgoing_lp_filter) == 2
    assert len(t.outgoing_hp_filter) == 2
    assert len(t.incoming_hp_filter) == 4
    assert len(t.incoming_lp_filter) == 2
    assert len(t.outgoing_volume) == 2
    assert len(t.incoming_volume) == 2


def test_transition_outgoing_lp_sweeps_down():
    t = generate_transition(0.0, 32)
    assert t.outgoing_lp_filter[0].value == 20000.0
    assert t.outgoing_lp_filter[-1].value == 200.0


def test_transition_incoming_hp_opens_at_mid():
    t = generate_transition(0.0, 32)
    assert t.incoming_hp_filter[0].value == 500.0
    assert t.incoming_hp_filter[-1].value == 20.0


def test_transition_volume_crossfade():
    t = generate_transition(0.0, 16)
    assert t.outgoing_volume[0].value == 1.0
    assert t.outgoing_volume[-1].value == 0.0
    assert t.incoming_volume[0].value == 0.0
    assert t.incoming_volume[-1].value == 1.0


def test_transition_timing():
    t = generate_transition(100.0, 32)
    total = 32 * 4
    assert t.outgoing_lp_filter[0].time_beats == 100.0
    assert t.outgoing_lp_filter[-1].time_beats == 100.0 + total
