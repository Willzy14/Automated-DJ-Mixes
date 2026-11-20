"""Tests for the one-minute mix-point markers in Tools/section_key_picture.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Tools"))

import section_key_picture as pic  # noqa: E402


def test_marker_is_the_point_nearest_a_minute_with_the_real_time_printed():
    spb = 240.0 / 124.0                          # Reachin: 1.935 s per bar, ends 387.8 s
    pts = [24, 32, 39, 60, 65, 98, 101, 136, 152, 176]
    out = pic.mix_points(pts, 0.0, spb, 387.8)
    assert out[0][0] == 32 and out[0][1].endswith("1:01 IN")
    assert out[1][0] == 169 or out[1][0] == 176 or out[1][0] == 152
    left = 387.8 - out[1][0] * spb
    assert abs(left - 60) <= 15 and out[1][1] == f"MIX POINT, {int(round(left))} s TO END"


def test_no_marker_when_nothing_lies_near_the_minute():
    spb = 240.0 / 124.0
    out = pic.mix_points([10, 186], 0.0, spb, 387.8)   # 19 s in, 28 s to end
    assert out == []


def test_old_bug_29_seconds_to_end_is_not_drawn():
    spb = 240.0 / 124.0
    out = pic.mix_points([186], 0.0, spb, 387.8)
    assert all("TO END" not in label for _, label in out)
