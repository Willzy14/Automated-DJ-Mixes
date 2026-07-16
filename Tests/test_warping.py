"""Tests for warp marker calculation."""

import pytest

from automated_dj_mixes.warping import calculate_warp_markers, WarpMarker


def test_basic_markers():
    markers = calculate_warp_markers(bpm=128.0, first_downbeat_sec=0.5, duration_sec=300.0)
    assert len(markers) == 2
    assert markers[0].beat_time == 0.0
    assert markers[0].sample_time == 0.5


def test_first_marker_at_downbeat():
    markers = calculate_warp_markers(bpm=126.0, first_downbeat_sec=1.2, duration_sec=200.0)
    assert markers[0].sample_time == pytest.approx(1.2)
    assert markers[0].beat_time == 0.0


def test_second_marker_at_end():
    markers = calculate_warp_markers(bpm=128.0, first_downbeat_sec=0.0, duration_sec=240.0)
    assert markers[1].sample_time == 240.0
    secs_per_beat = 60.0 / 128.0
    expected_beats = 240.0 / secs_per_beat
    assert markers[1].beat_time == pytest.approx(expected_beats)


def test_beat_count_accounts_for_downbeat_offset():
    markers = calculate_warp_markers(bpm=120.0, first_downbeat_sec=2.0, duration_sec=302.0)
    secs_per_beat = 60.0 / 120.0
    expected_beats = (302.0 - 2.0) / secs_per_beat
    assert markers[1].beat_time == pytest.approx(expected_beats)


def test_different_bpms_produce_different_beat_counts():
    m1 = calculate_warp_markers(bpm=120.0, first_downbeat_sec=0.0, duration_sec=300.0)
    m2 = calculate_warp_markers(bpm=140.0, first_downbeat_sec=0.0, duration_sec=300.0)
    assert m1[1].beat_time != m2[1].beat_time
    assert m2[1].beat_time > m1[1].beat_time  # faster BPM = more beats


def test_choose_warp_mode_never_repitches_audible_shifts():
    # 127 vs 126 = ~14 cents if repitched — must time-stretch instead
    # (2026-06-12: grid-true BPMs hit the old <=1.0 boundary exactly)
    from automated_dj_mixes.warping import (
        WARP_MODE_COMPLEX_PRO, WARP_MODE_REPITCH, choose_warp_mode,
    )
    assert choose_warp_mode(127.0, 126.0) == WARP_MODE_COMPLEX_PRO
    assert choose_warp_mode(126.0033, 126.0) == WARP_MODE_REPITCH
    assert choose_warp_mode(126.0, 126.0) == WARP_MODE_REPITCH


def test_dj_mix_policy_keeps_nominal_one_bpm_moves_in_repitch():
    from automated_dj_mixes.warping import (
        WARP_MODE_COMPLEX_PRO,
        WARP_MODE_REPITCH,
        choose_dj_mix_warp_mode,
    )

    assert choose_dj_mix_warp_mode(119.999506, 121.0) == WARP_MODE_REPITCH
    assert choose_dj_mix_warp_mode(122.004529, 121.0) == WARP_MODE_REPITCH
    assert choose_dj_mix_warp_mode(122.06, 121.0) == WARP_MODE_COMPLEX_PRO
