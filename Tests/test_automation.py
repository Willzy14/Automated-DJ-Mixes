"""Tests for automation gain-offset calculation.

(The old filter-sweep transition tests were removed: generate_transition and
TransitionAutomation were superseded by the skills + transition.py / apply_
automation model and no longer exist in automation.py.)
"""

import pytest

from automated_dj_mixes.automation import calculate_gain_offsets


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
