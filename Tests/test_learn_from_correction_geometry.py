import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

from learn_from_correction import (
    TrackAutomation,
    TrackInfo,
    VacuousLearningError,
    analyse_transitions,
    guard_against_vacuous_result,
    ordered_tracks_from_clip_records,
)


def _clip(name, start, end):
    return {
        "name": name,
        "label": name.rsplit("_", 1)[0],
        "label_n": 1,
        "arr_time": start,
        "arr_end": end,
    }


def test_geometry_and_scoping_come_from_each_als_clip_set():
    phase_one_json = {
        "Artist & Co - Out": [
            {"name": "drop_1", "label": "drop", "label_n": 1,
             "arr_time": 9000.0, "arr_end": 9001.0},
        ],
        "Guest - In": [
            {"name": "intro_1", "label": "intro", "label_n": 1,
             "arr_time": 9001.0, "arr_end": 9002.0},
        ],
    }
    baseline_records = {
        "Artist &amp; Co - Out": [
            _clip("drop_1", 0.0, 400.0),
            _clip("loop_1", 20.0, 350.0),
        ],
        "Guest - In": [_clip("intro_1", 300.0, 700.0)],
    }
    corrected_records = {
        "Artist &amp; Co - Out": [_clip("drop_1", 0.0, 200.0)],
        "Guest - In": [_clip("intro_1", 100.0, 500.0)],
    }

    baseline = ordered_tracks_from_clip_records(
        baseline_records, phase_one_json)
    corrected = ordered_tracks_from_clip_records(
        corrected_records, phase_one_json)

    assert [(track.name, track.arr_start, track.arr_end) for track in baseline] == [
        ("Artist & Co - Out", 0.0, 400.0),
        ("Guest - In", 300.0, 700.0),
    ]
    assert "arr_time" not in baseline[0].sections[0]

    claude_auto = {
        "Artist &amp; Co - Out": TrackAutomation(
            "Artist &amp; Co - Out", bass_points=[(350.0, 1.0), (351.0, 0.18)]),
        "Guest - In": TrackAutomation("Guest - In"),
    }
    sam_auto = {
        "Artist &amp; Co - Out": TrackAutomation(
            "Artist &amp; Co - Out", bass_points=[(150.0, 1.0), (151.0, 0.18)]),
        "Guest - In": TrackAutomation("Guest - In"),
    }

    diffs = analyse_transitions(
        claude_auto, sam_auto, baseline, corrected)

    assert len(diffs) == 1
    assert diffs[0].out_bass.claude_points == [(350.0, 1.0), (351.0, 0.18)]
    assert diffs[0].out_bass.sam_points == [(150.0, 1.0), (151.0, 0.18)]
    assert diffs[0].bass_swap_claude == 351.0
    assert diffs[0].bass_swap_sam == 151.0
    assert diffs[0].arrangement_changed is True


def test_constant_global_shift_preserves_relative_transition_geometry():
    baseline = [
        TrackInfo("Out", [], 1000.0, 1400.0),
        TrackInfo("In", [], 1300.0, 1700.0),
    ]
    # Sam's whole downstream arrangement moved left by 164 beats, but neither
    # track's internal automation nor their relative transition changed.
    corrected = [
        TrackInfo("Out", [], 836.0, 1236.0),
        TrackInfo("In", [], 1136.0, 1536.0),
    ]
    claude_auto = {
        "Out": TrackAutomation(
            "Out", volume_points=[(1300.0, 1.0), (1400.0, 0.18)],
            bass_points=[(1300.0, 1.0), (1320.0, 0.18)]),
        "In": TrackAutomation(
            "In", volume_points=[(1300.0, 0.18), (1320.0, 1.0)],
            bass_points=[(1300.0, 0.18), (1320.0, 1.0)]),
    }
    sam_auto = {
        "Out": TrackAutomation(
            "Out", volume_points=[(1136.0, 1.0), (1236.0, 0.18)],
            bass_points=[(1136.0, 1.0), (1156.0, 0.18)]),
        "In": TrackAutomation(
            "In", volume_points=[(1136.0, 0.18), (1156.0, 1.0)],
            bass_points=[(1136.0, 0.18), (1156.0, 1.0)]),
    }

    [diff] = analyse_transitions(claude_auto, sam_auto, baseline, corrected)

    assert diff.verdict == "correct"
    assert diff.arrangement_changed is False
    assert diff.bass_swap_claude == diff.bass_swap_sam == 320.0
    assert diff.bass_swap_delta == 0.0
    assert diff.out_bass.changed is False
    assert diff.in_volume.changed is False


def test_missing_sam_track_is_recorded_as_unanalysed():
    baseline = [
        TrackInfo("Out", [], 0.0, 400.0),
        TrackInfo("In", [], 300.0, 700.0),
    ]
    corrected = [TrackInfo("Out", [], 0.0, 400.0)]

    [diff] = analyse_transitions({}, {}, baseline, corrected)

    assert diff.verdict == "unanalysed"
    assert diff.notes == "Unanalysed: no matching incoming track in Sam ALS"


def test_overlapping_als_geometries_cannot_vacuously_pass():
    baseline = [
        TrackInfo("out", [], 0.0, 400.0),
        TrackInfo("in", [], 300.0, 700.0),
    ]
    corrected = [
        TrackInfo("out", [], 0.0, 360.0),
        TrackInfo("in", [], 280.0, 640.0),
    ]

    with pytest.raises(VacuousLearningError, match="produced 0 transitions"):
        guard_against_vacuous_result(baseline, corrected, [])
