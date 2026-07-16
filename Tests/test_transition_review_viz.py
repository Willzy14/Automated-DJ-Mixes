import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))


def test_arrangement_mapping_replays_loop_source_range():
    from transition_review_viz import _source_beats_for_arrangement

    clips = [
        {
            "arr_time": 0.0,
            "arr_end": 4.0,
            "source_start_beats": 0.0,
            "source_end_beats": 4.0,
        },
        {
            "arr_time": 4.0,
            "arr_end": 8.0,
            "source_start_beats": 0.0,
            "source_end_beats": 4.0,
        },
        {
            "arr_time": 8.0,
            "arr_end": 12.0,
            "source_start_beats": 4.0,
            "source_end_beats": 8.0,
        },
    ]

    mapped = _source_beats_for_arrangement(clips, np.array([1.0, 5.0, 9.0]))

    assert mapped.tolist() == [1.0, 1.0, 5.0]
