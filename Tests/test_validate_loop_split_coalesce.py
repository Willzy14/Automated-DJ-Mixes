"""D16: apply_automation splits a loop-repeat clip at the bass swap; the
validator must accept that bookkeeping split but still fail real defects."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

from validate_mix_plan_als import _coalesce_automation_splits as coalesce

WINDOWS = [(2756.0, 2788.0)]          # 2 repeats of 16 beats
EXPECTED = [2756.0, 2772.0]


def test_mid_repeat_contiguous_split_is_absorbed():
    clips = [(2756.0, 2772.0), (2772.0, 2776.0), (2776.0, 2788.0)]
    assert coalesce(clips, EXPECTED, WINDOWS) == [2756.0, 2772.0]


def test_planned_boundaries_untouched():
    clips = [(2756.0, 2772.0), (2772.0, 2788.0)]
    assert coalesce(clips, EXPECTED, WINDOWS) == [2756.0, 2772.0]


def test_gap_before_extra_clip_still_fails():
    clips = [(2756.0, 2772.0), (2772.0, 2774.0), (2776.0, 2788.0)]
    assert coalesce(clips, EXPECTED, WINDOWS) == [2756.0, 2772.0, 2776.0]


def test_extra_clip_outside_loop_window_still_fails():
    clips = [(2756.0, 2772.0), (2772.0, 2788.0), (2788.0, 2796.0)]
    assert coalesce(clips, EXPECTED, WINDOWS) == [2756.0, 2772.0, 2788.0]


def test_first_clip_is_never_dropped():
    assert coalesce([(2760.0, 2772.0)], EXPECTED, WINDOWS) == [2760.0]
