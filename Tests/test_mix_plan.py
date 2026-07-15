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


def _build(arrangement=None):
    from automated_dj_mixes.mix_plan import build_one_transition_mix_plan

    return build_one_transition_mix_plan(
        arrangement or _arrangement(),
        source_hashes={"out": _hash("a"), "in": _hash("b")},
        section_map_hashes={"out": _hash("c"), "in": _hash("d")},
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


def test_mix_plan_rejects_missing_certified_source_hash():
    from automated_dj_mixes.mix_plan import build_one_transition_mix_plan

    with pytest.raises(ValueError, match="source hash"):
        build_one_transition_mix_plan(
            _arrangement(),
            source_hashes={"out": _hash("a")},
            section_map_hashes={"out": _hash("c"), "in": _hash("d")},
        )


def test_mix_plan_rejects_stale_plan_hash():
    from automated_dj_mixes.mix_plan import validate_mix_plan

    plan = _build()
    tampered = dataclasses.replace(plan, plan_hash=_hash("0"))
    with pytest.raises(ValueError, match="plan_hash"):
        validate_mix_plan(tampered)
