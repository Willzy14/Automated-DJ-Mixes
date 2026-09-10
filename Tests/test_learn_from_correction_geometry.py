import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

from learn_from_correction import (
    TrackAutomation,
    TrackInfo,
    VacuousLearningError,
    _source_beat,
    analyse_transitions,
    guard_against_vacuous_result,
    ordered_tracks_from_clip_records,
)


def _clip(name, start, end, source_start=None):
    clip = {
        "name": name,
        "label": name.rsplit("_", 1)[0],
        "label_n": 1,
        "arr_time": start,
        "arr_end": end,
    }
    if source_start is not None:
        clip["source_start_beats"] = source_start
        clip["source_end_beats"] = source_start + (end - start)
    return clip


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
    # arrangement_changed is now length-only (2026-09-07 follow-up, see
    # test_arrangement_changed_ignores_outgoing_start_drift_from_upstream_
    # edit below) - this scenario's overlap length is UNCHANGED (100 both
    # sides: 400-300 baseline, 200-100 corrected), so it correctly reads
    # False here. The scenario's real substance (a huge 200-beat window
    # shift with no source_start_beats data to normalize through) is still
    # caught, via out_bass.changed instead - verdict stays "corrected".
    assert diffs[0].arrangement_changed is False
    assert diffs[0].out_bass.changed is True
    assert diffs[0].verdict == "corrected"


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


def test_source_anchor_ignores_outgoing_clip_resize_at_unchanged_tail():
    """A tail handoff stays unchanged when an earlier clip trim changes length."""
    baseline = ordered_tracks_from_clip_records({
        "Out": [_clip("outro_1", 1000.0, 1400.0, source_start=0.0)],
        "In": [_clip("intro_1", 1300.0, 1700.0, source_start=0.0)],
    })
    # The outgoing clip's start moved by -100 beats and end by -132 beats.
    # It now begins 32 source beats later, but the automation at its tail is
    # still at precisely the same source-audio positions (380 and 399).
    corrected = ordered_tracks_from_clip_records({
        "Out": [_clip("outro_1", 900.0, 1268.0, source_start=32.0)],
        "In": [_clip("intro_1", 1168.0, 1568.0, source_start=0.0)],
    })
    claude_auto = {
        "Out": TrackAutomation(
            "Out", volume_points=[(1380.0, 1.0), (1399.0, 0.18)],
            bass_points=[(1380.0, 1.0), (1399.0, 0.18)]),
        "In": TrackAutomation("In"),
    }
    sam_auto = {
        "Out": TrackAutomation(
            "Out", volume_points=[(1248.0, 1.0), (1267.0, 0.18)],
            bass_points=[(1248.0, 1.0), (1267.0, 0.18)]),
        "In": TrackAutomation("In"),
    }

    [diff] = analyse_transitions(claude_auto, sam_auto, baseline, corrected)

    assert diff.out_volume.claude_points == diff.out_volume.sam_points == [
        (380.0, 1.0), (399.0, 0.18)]
    assert diff.out_bass.claude_points == diff.out_bass.sam_points == [
        (380.0, 1.0), (399.0, 0.18)]
    assert diff.out_volume.changed is False
    assert diff.out_bass.changed is False


def test_arrangement_changed_ignores_outgoing_start_drift_from_upstream_edit():
    """2026-09-07 follow-up: arrangement_changed must not fire purely
    because the OUTGOING track's own arr_start moved - that reflects
    whatever UPSTREAM transition precedes this one, not this transition's
    own geometry. Real corpus evidence (02.09.26 House 10, T6): How Good's
    start moved -132 beats and its end moved -164 beats (an upstream
    edit's cascade, not a T6 edit), while T6's own overlap length and
    swap position were provably untouched (source-anchored automation
    matches exactly - see test_source_anchor_ignores_outgoing_clip_resize_
    at_unchanged_tail above). arrangement_changed wrongly read True from
    the (in.arr_start - out.arr_start) delta alone; it must now key on
    overlap LENGTH only, which these two transitions keep identical."""
    baseline = ordered_tracks_from_clip_records({
        "Out": [_clip("outro_1", 1000.0, 1400.0, source_start=0.0)],
        "In": [_clip("intro_1", 1300.0, 1700.0, source_start=0.0)],
    })
    # Same resize as the sibling test above: Out's start moves -100, end
    # moves -132 (a genuine 32-beat asymmetric resize from an UPSTREAM
    # edit). In moves by the SAME -132 as Out's end, so THIS transition's
    # own overlap length (Out.arr_end - In.arr_start = 100.0 both sides:
    # 1400-1300 baseline, 1268-1168 corrected) and swap position are
    # unchanged.
    corrected = ordered_tracks_from_clip_records({
        "Out": [_clip("outro_1", 900.0, 1268.0, source_start=32.0)],
        "In": [_clip("intro_1", 1168.0, 1568.0, source_start=0.0)],
    })
    claude_auto = {
        "Out": TrackAutomation(
            "Out", volume_points=[(1380.0, 1.0), (1399.0, 0.18)],
            bass_points=[(1380.0, 1.0), (1399.0, 0.18)]),
        "In": TrackAutomation("In"),
    }
    sam_auto = {
        "Out": TrackAutomation(
            "Out", volume_points=[(1248.0, 1.0), (1267.0, 0.18)],
            bass_points=[(1248.0, 1.0), (1267.0, 0.18)]),
        "In": TrackAutomation("In"),
    }

    [diff] = analyse_transitions(claude_auto, sam_auto, baseline, corrected)

    assert diff.arrangement_changed is False
    assert diff.verdict == "correct"


def test_source_beat_boundary_prefers_the_later_clip_on_a_repeated_tail_loop():
    """2026-09-10, second-lens review of 53d1b7d Finding 1: a beat sitting
    exactly on the splice between the LAST of several identical tail-loop
    copies and the real outro clip after them must resolve to the outro's
    OWN source position, not silently inherit the loop's - they are
    provably different (real corpus: Apt 3b's tail loop LoopStart=640,
    its own outro_1 LoopStart=608 - a 32-beat gap the old
    earliest-arranged tie-break papered over). Mirrors the real shape:
    apply_loops.clone_clip repeats N copies of a chosen loop window
    (identical source_start_beats each), then the track's real content
    resumes at its OWN, independently-set source position."""
    tail_loop_copies = [
        _clip(f"outro_1_tail_loop_{i}", 5536.0 + i * 32, 5568.0 + i * 32,
             source_start=640.0)
        for i in range(4)
    ]
    outro = _clip("outro_1", 5664.0, 5728.0, source_start=608.0)
    clips = tail_loop_copies + [outro]

    # The exact splice beat: end of the last tail-loop copy == start of
    # outro_1. This is where the tie actually occurs.
    boundary_beat = 5664.0
    assert _source_beat(boundary_beat, clips, fallback_origin=0.0) == 608.0

    # Sanity: a beat safely INSIDE a tail-loop copy (not a boundary) must
    # still resolve to that copy's own source position, unaffected.
    interior_beat = 5550.0
    assert _source_beat(interior_beat, clips, fallback_origin=0.0) == \
        640.0 + (interior_beat - 5536.0)


def test_bass_swap_delta_is_the_raw_source_difference_not_overlap_relative():
    """2026-09-10, second-lens review of 53d1b7d Finding 2 - the exact
    real bug (verified independently against 02.09.26 House 10's T9,
    Once Again -> A Deep-Felt Love: true swap source 640 -> 576, a real
    -64 move; the buggy formula reported +64, sign-flipped). Mirrors
    that transition's real shape: the outgoing's baseline side ends in a
    repeated tail loop + a differently-sourced outro (swap sits at the
    loop's own source, 200); the corrected side has the whole tail
    deleted, ending mid-drop with the swap now inside that drop's OWN,
    much-earlier source content (150) - AND the overlap window itself
    also shifts and shrinks between the two files (Sam's genuine
    overlap-length correction, unrelated to the swap fix), which is
    exactly what made the old out_ov_start-relative formula sign-flip:
    each side's independently-computed overlap-start distance differs by
    more than the true swap move itself."""
    baseline_out = ordered_tracks_from_clip_records({
        "Out": [
            _clip("drop_1", 1000.0, 1100.0, source_start=0.0),
            *[_clip(f"tail_{i}", 1100.0 + i * 10, 1110.0 + i * 10,
                    source_start=200.0) for i in range(4)],
            _clip("outro_1", 1140.0, 1200.0, source_start=180.0),
        ],
        "In": [_clip("intro_1", 1140.0, 1300.0, source_start=0.0)],
    })
    corrected_out = ordered_tracks_from_clip_records({
        "Out": [_clip("drop_1", 800.0, 1000.0, source_start=50.0)],
        "In": [_clip("intro_1", 950.0, 1110.0, source_start=0.0)],
    })
    # Swap automation - points chosen to survive _scope_points' arrangement-
    # time margin (overlap +/-40 beats) AND _find_bass_swap_beat's
    # source-time search window (source-anchored overlap +/-10 beats,
    # i.e. baseline [170,250], corrected [190,260] - computed from each
    # side's own out_ov_start/end, not hand-derivable without running the
    # code, which is the point of pinning it): baseline kills bass at arr
    # 1105 (inside the first tail-loop copy -> source
    # 200+(1105-1100)=205); corrected kills it at arr 950 (inside the
    # truncated drop_1 -> source 50+(950-800)=200). True
    # source-relative move: 200 - 205 = -5.
    claude_auto = {
        "Out": TrackAutomation("Out", bass_points=[(1105.0, 0.18)]),
        "In": TrackAutomation("In"),
    }
    sam_auto = {
        "Out": TrackAutomation("Out", bass_points=[(950.0, 0.18)]),
        "In": TrackAutomation("In"),
    }

    [diff] = analyse_transitions(
        claude_auto, sam_auto, baseline_out, corrected_out)

    assert diff.bass_swap_claude == pytest.approx(205.0)
    assert diff.bass_swap_sam == pytest.approx(200.0)
    assert diff.bass_swap_delta == pytest.approx(-5.0), (
        "expected the raw source-anchored difference (200-205=-5); "
        "the old out_ov_start-relative formula would report -25 here "
        "((200-200)-(205-180)=-25) - source-space overlap starts differ "
        "by 20 beats between files, corrupting the delta by exactly that "
        "amount. This is the same class of sign/magnitude corruption "
        "Finding 2 found live on the real corpus (T9: true -64, old "
        "formula reported +64 - a much larger corruption there because "
        "that transition's real overlap-start drift is much larger)")


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
