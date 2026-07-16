import dataclasses
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))


def _arrangement(overlap_beats=128.0):
    from apply_loops import LoopSpec
    from propose_arrangement import ArrangementPlan, OverlapAnalysis, TrackInfo

    incoming_start = 400.0 - overlap_beats
    tracks = [
        TrackInfo("out", [], 0.0, 400.0),
        TrackInfo("in", [], incoming_start, incoming_start + 400.0),
    ]
    loop = LoopSpec("out", 368.0, 384.0, 2, 368.0)
    overlap = OverlapAnalysis(
        out_track="out",
        in_track="in",
        pair_index=1,
        overlap_start=incoming_start,
        overlap_end=400.0,
        overlap_beats=overlap_beats,
        overlap_bars=overlap_beats / 4.0,
        status="ok",
        out_tail_loop=loop,
    )
    return ArrangementPlan(tracks, [overlap], [], [loop])


def _hash(char):
    return char * 64


def _three_track_arrangement():
    from apply_loops import LoopSpec
    from propose_arrangement import ArrangementPlan, OverlapAnalysis, TrackInfo

    tracks = [
        TrackInfo("one", [], 0.0, 400.0),
        TrackInfo("two", [], 272.0, 672.0),
        TrackInfo("three", [], 576.0, 976.0),
    ]
    loops = [
        LoopSpec("one", 368.0, 384.0, 1, 368.0),
        LoopSpec("two", 640.0, 656.0, 1, 640.0),
    ]
    overlaps = [
        OverlapAnalysis("one", "two", 1, 272.0, 400.0, 128.0, 32.0,
                        "ok", out_tail_loop=loops[0]),
        OverlapAnalysis("two", "three", 2, 576.0, 672.0, 96.0, 24.0,
                        "ok", out_tail_loop=loops[1]),
    ]
    return ArrangementPlan(tracks, overlaps, [], loops)


def _build(arrangement=None):
    from automated_dj_mixes.mix_plan import build_one_transition_mix_plan
    from automated_dj_mixes.warp_contract import WarpGridSummary

    return build_one_transition_mix_plan(
        arrangement or _arrangement(),
        source_hashes={"out": _hash("a"), "in": _hash("b")},
        section_map_hashes={"out": _hash("c"), "in": _hash("d")},
        warp_grid_contracts={
            "out": WarpGridSummary(401, _hash("1"), 120.0),
            "in": WarpGridSummary(405, _hash("2"), 121.0),
        },
        input_hashes={"sections_json": _hash("e"), "input_als": _hash("f")},
        policy_versions={"overlap": "safety_v1"},
        tool_versions={"planner": "mix_plan_v1"},
    )


def test_one_transition_mix_plan_is_stable_immutable_and_serializable(tmp_path):
    from automated_dj_mixes.mix_plan import validate_mix_plan, write_mix_plan

    first = _build()
    second = _build()

    assert first == second
    assert first.plan_hash == second.plan_hash
    assert len(first.main_track_sequence) == 2
    assert len(first.transitions) == 1
    assert first.transitions[0].out_track_instance_id == first.main_track_sequence[0]
    assert first.transitions[0].in_track_instance_id == first.main_track_sequence[1]
    assert first.transitions[0].loop_ids == (first.loops[0].loop_id,)
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.plan_version = 2

    output = write_mix_plan(first, tmp_path / "MIX_PLAN.json")
    assert output.read_text(encoding="utf-8").endswith("\n")
    validate_mix_plan(first)


def test_mix_plan_hash_changes_when_geometry_changes_but_semantic_pair_id_does_not():
    first = _build(_arrangement(128.0))
    changed = _build(_arrangement(96.0))

    assert first.plan_hash != changed.plan_hash
    assert first.transitions[0].transition_id == changed.transitions[0].transition_id


def test_mix_plan_freezes_named_landmark_overlap_policy():
    arrangement = _arrangement(224.0)
    arrangement.overlaps[0].overlap_policy = "named_landmark_64"
    arrangement.overlaps[0].loop_target_marker = "landmark:kick_gap:end"

    plan = _build(arrangement)

    assert plan.schema_version == "1.3"
    assert plan.transitions[0].overlap_beats == 224.0
    assert plan.transitions[0].overlap_policy == "named_landmark_64"


def test_mix_plan_rejects_missing_certified_source_hash():
    from automated_dj_mixes.mix_plan import build_one_transition_mix_plan
    from automated_dj_mixes.warp_contract import WarpGridSummary

    with pytest.raises(ValueError, match="source hash"):
        build_one_transition_mix_plan(
            _arrangement(),
            source_hashes={"out": _hash("a")},
            section_map_hashes={"out": _hash("c"), "in": _hash("d")},
            warp_grid_contracts={
                "out": WarpGridSummary(401, _hash("1"), 120.0),
                "in": WarpGridSummary(405, _hash("2"), 121.0),
            },
        )


def test_mix_plan_rejects_stale_plan_hash():
    from automated_dj_mixes.mix_plan import validate_mix_plan

    plan = _build()
    tampered = dataclasses.replace(plan, plan_hash=_hash("0"))
    with pytest.raises(ValueError, match="plan_hash"):
        validate_mix_plan(tampered)


def test_multi_transition_mix_plan_freezes_per_track_playback_policy():
    from automated_dj_mixes.mix_plan import build_mix_plan, validate_mix_plan
    from automated_dj_mixes.warp_contract import WarpGridSummary

    plan = build_mix_plan(
        _three_track_arrangement(),
        source_hashes={"one": _hash("a"), "two": _hash("b"), "three": _hash("c")},
        section_map_hashes={"one": _hash("d"), "two": _hash("e"), "three": _hash("f")},
        warp_grid_contracts={
            "one": WarpGridSummary(401, _hash("1"), 120.0),
            "two": WarpGridSummary(405, _hash("2"), 121.0),
            "three": WarpGridSummary(409, _hash("3"), 123.0),
        },
        project_bpm=121.0,
        warp_modes={"one": "repitch", "two": "repitch", "three": "complex_pro"},
    )

    assert plan.project_bpm == 121.0
    assert [track.warp_mode for track in plan.tracks] == [
        "repitch", "repitch", "complex_pro"
    ]
    assert len(plan.transitions) == 2
    assert [len(transition.loop_ids) for transition in plan.transitions] == [1, 1]
    validate_mix_plan(plan)


def test_paired_swap_boundary_matches_clip_start_or_end():
    import xml.etree.ElementTree as ET

    from validate_mix_plan_als import _matches_clip_boundary

    clips = [
        ET.fromstring(
            '<AudioClip Time="100"><CurrentEnd Value="132" /></AudioClip>'
        ),
        ET.fromstring(
            '<AudioClip Time="132"><CurrentEnd Value="148" /></AudioClip>'
        ),
    ]

    assert _matches_clip_boundary(clips, 132.0)
    assert not _matches_clip_boundary(clips, 124.0)
