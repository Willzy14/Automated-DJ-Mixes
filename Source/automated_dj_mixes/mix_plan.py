"""Immutable, hash-backed production intent for the DJ mix pipeline.

V1 intentionally covers the first vertical proof: two certified sources, their
arrangement geometry, loop intent, and one main handover contract. Later slices
extend this schema with tempo, warp, automation, render, and approval contracts.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Mapping

from apply_loops import MAX_LOOP_EXTENSION_BEATS, MAX_LOOP_REPEATS


SCHEMA_VERSION = "1.0"
PRODUCTION_SCOPE = "one_transition_arrangement_v1"
MIN_OVERLAP_BEATS = 64.0
MAX_OVERLAP_BEATS = 192.0
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ArtifactHash:
    name: str
    sha256: str


@dataclass(frozen=True)
class MetadataEntry:
    name: str
    value: str


@dataclass(frozen=True)
class SourceContract:
    source_id: str
    display_name: str
    sha256: str


@dataclass(frozen=True)
class TrackInstanceContract:
    track_instance_id: str
    source_id: str
    section_map_id: str
    sequence_index: int
    display_name: str
    arrangement_start_beat: float
    arrangement_end_beat: float


@dataclass(frozen=True)
class LoopContract:
    loop_id: str
    transition_id: str
    track_instance_id: str
    source_beat_start: float
    source_beat_end: float
    repeat_count: int
    partial_beats: float
    insert_at_beat: float


@dataclass(frozen=True)
class TransitionContract:
    transition_id: str
    transition_index: int
    out_track_instance_id: str
    in_track_instance_id: str
    overlap_start_beat: float
    overlap_end_beat: float
    overlap_beats: float
    loop_ids: tuple[str, ...]


@dataclass(frozen=True)
class MixPlan:
    schema_version: str
    production_scope: str
    plan_version: int
    parent_plan_hash: str | None
    main_track_sequence: tuple[str, ...]
    sources: tuple[SourceContract, ...]
    tracks: tuple[TrackInstanceContract, ...]
    loops: tuple[LoopContract, ...]
    transitions: tuple[TransitionContract, ...]
    input_hashes: tuple[ArtifactHash, ...]
    policy_versions: tuple[MetadataEntry, ...]
    tool_versions: tuple[MetadataEntry, ...]
    human_overrides: tuple[MetadataEntry, ...]
    plan_hash: str


def _canonical_json(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _semantic_id(prefix: str, value) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:20]}"


def _normalise_digest(digest: str, label: str) -> str:
    value = str(digest).lower()
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be a 64-character SHA-256 digest")
    return value


def _metadata_entries(values: Mapping[str, str]) -> tuple[MetadataEntry, ...]:
    return tuple(
        MetadataEntry(str(name), str(value))
        for name, value in sorted(values.items())
    )


def _artifact_hashes(values: Mapping[str, str]) -> tuple[ArtifactHash, ...]:
    return tuple(
        ArtifactHash(str(name), _normalise_digest(digest, f"input hash '{name}'"))
        for name, digest in sorted(values.items())
    )


def mix_plan_payload(plan: MixPlan) -> dict:
    payload = asdict(plan)
    payload.pop("plan_hash")
    return payload


def mix_plan_dict(plan: MixPlan) -> dict:
    payload = mix_plan_payload(plan)
    payload["plan_hash"] = plan.plan_hash
    return payload


def _compute_plan_hash(plan: MixPlan) -> str:
    return hashlib.sha256(
        _canonical_json(mix_plan_payload(plan)).encode("utf-8")
    ).hexdigest()


def build_one_transition_mix_plan(
    arrangement_plan,
    *,
    source_hashes: Mapping[str, str],
    section_map_hashes: Mapping[str, str],
    input_hashes: Mapping[str, str] | None = None,
    policy_versions: Mapping[str, str] | None = None,
    tool_versions: Mapping[str, str] | None = None,
    human_overrides: Mapping[str, str] | None = None,
    plan_version: int = 1,
    parent_plan_hash: str | None = None,
) -> MixPlan:
    """Convert a validated two-track ArrangementPlan into canonical V1 intent."""
    from propose_arrangement import validate_arrangement_plan

    validate_arrangement_plan(arrangement_plan)
    if len(arrangement_plan.tracks) != 2 or len(arrangement_plan.overlaps) != 1:
        raise ValueError("MixPlan V1 requires exactly two tracks and one transition")
    if plan_version < 1:
        raise ValueError("plan_version must be at least 1")
    if plan_version > 1 and not parent_plan_hash:
        raise ValueError("A revised MixPlan requires parent_plan_hash")
    if parent_plan_hash is not None:
        _normalise_digest(parent_plan_hash, "parent_plan_hash")

    source_contracts: list[SourceContract] = []
    track_contracts: list[TrackInstanceContract] = []
    extra_hashes = dict(input_hashes or {})

    for index, track in enumerate(arrangement_plan.tracks):
        if track.name not in source_hashes:
            raise ValueError(f"Missing certified source hash for '{track.name}'")
        if track.name not in section_map_hashes:
            raise ValueError(f"Missing section map hash for '{track.name}'")

        source_hash = _normalise_digest(
            source_hashes[track.name], f"source hash for '{track.name}'"
        )
        section_hash = _normalise_digest(
            section_map_hashes[track.name], f"section map hash for '{track.name}'"
        )
        source_id = _semantic_id("src", {"sha256": source_hash})
        track_instance_id = _semantic_id(
            "trk", {"source_id": source_id, "sequence_index": index}
        )
        section_map_id = _semantic_id("sec", {"sha256": section_hash})

        if not any(source.source_id == source_id for source in source_contracts):
            source_contracts.append(SourceContract(source_id, track.name, source_hash))
        track_contracts.append(TrackInstanceContract(
            track_instance_id=track_instance_id,
            source_id=source_id,
            section_map_id=section_map_id,
            sequence_index=index,
            display_name=track.name,
            arrangement_start_beat=float(track.arr_start),
            arrangement_end_beat=float(track.arr_end),
        ))
        extra_hashes[f"source:{source_id}"] = source_hash
        extra_hashes[f"section_map:{track_instance_id}"] = section_hash

    out_track, in_track = track_contracts
    transition_id = _semantic_id("trn", {
        "out_track_instance_id": out_track.track_instance_id,
        "in_track_instance_id": in_track.track_instance_id,
    })
    track_id_by_name = {
        track.display_name: track.track_instance_id for track in track_contracts
    }

    loop_contracts: list[LoopContract] = []
    for spec in arrangement_plan.loops:
        track_instance_id = track_id_by_name.get(spec.track_name)
        if track_instance_id is None:
            raise ValueError(
                f"Loop target '{spec.track_name}' is not a transition participant"
            )
        loop_value = {
            "transition_id": transition_id,
            "track_instance_id": track_instance_id,
            "source_beat_start": float(spec.source_beat_start),
            "source_beat_end": float(spec.source_beat_end),
            "repeat_count": spec.count,
            "partial_beats": float(spec.tail_partial_beats),
            "insert_at_beat": float(spec.insert_at_beat),
        }
        loop_contracts.append(LoopContract(
            loop_id=_semantic_id("lop", loop_value),
            **loop_value,
        ))

    overlap = arrangement_plan.overlaps[0]
    transition = TransitionContract(
        transition_id=transition_id,
        transition_index=0,
        out_track_instance_id=out_track.track_instance_id,
        in_track_instance_id=in_track.track_instance_id,
        overlap_start_beat=float(overlap.overlap_start),
        overlap_end_beat=float(overlap.overlap_end),
        overlap_beats=float(overlap.overlap_beats),
        loop_ids=tuple(loop.loop_id for loop in loop_contracts),
    )

    policies = {"loop_safety": "safety_v1", "overlap_safety": "safety_v1"}
    policies.update(policy_versions or {})
    tools = {"mix_plan": SCHEMA_VERSION}
    tools.update(tool_versions or {})

    provisional = MixPlan(
        schema_version=SCHEMA_VERSION,
        production_scope=PRODUCTION_SCOPE,
        plan_version=plan_version,
        parent_plan_hash=parent_plan_hash,
        main_track_sequence=tuple(
            track.track_instance_id for track in track_contracts
        ),
        sources=tuple(source_contracts),
        tracks=tuple(track_contracts),
        loops=tuple(loop_contracts),
        transitions=(transition,),
        input_hashes=_artifact_hashes(extra_hashes),
        policy_versions=_metadata_entries(policies),
        tool_versions=_metadata_entries(tools),
        human_overrides=_metadata_entries(human_overrides or {}),
        plan_hash="",
    )
    plan = replace(provisional, plan_hash=_compute_plan_hash(provisional))
    validate_mix_plan(plan)
    return plan


def validate_mix_plan(plan: MixPlan) -> None:
    if plan.schema_version != SCHEMA_VERSION:
        raise ValueError(f"Unsupported MixPlan schema {plan.schema_version!r}")
    if plan.production_scope != PRODUCTION_SCOPE:
        raise ValueError(f"Unsupported MixPlan scope {plan.production_scope!r}")
    if plan.plan_version < 1:
        raise ValueError("plan_version must be at least 1")
    if plan.plan_version > 1 and not plan.parent_plan_hash:
        raise ValueError("A revised MixPlan requires parent_plan_hash")
    if plan.parent_plan_hash is not None:
        _normalise_digest(plan.parent_plan_hash, "parent_plan_hash")

    if len(plan.tracks) != 2 or len(plan.transitions) != 1:
        raise ValueError("MixPlan V1 requires exactly two tracks and one transition")
    if len(plan.main_track_sequence) != len(plan.tracks):
        raise ValueError("main_track_sequence must contain every main track exactly once")

    track_ids = tuple(track.track_instance_id for track in plan.tracks)
    if plan.main_track_sequence != track_ids or len(set(track_ids)) != len(track_ids):
        raise ValueError("main_track_sequence does not match unique track instances")
    source_by_id = {source.source_id: source for source in plan.sources}
    if len(source_by_id) != len(plan.sources):
        raise ValueError("MixPlan contains duplicate source IDs")

    hashes = {item.name: item.sha256 for item in plan.input_hashes}
    if len(hashes) != len(plan.input_hashes):
        raise ValueError("MixPlan contains duplicate input hash names")
    for item in plan.input_hashes:
        _normalise_digest(item.sha256, f"input hash '{item.name}'")

    for track in plan.tracks:
        source = source_by_id.get(track.source_id)
        if source is None:
            raise ValueError(f"Track {track.track_instance_id} has no source contract")
        _normalise_digest(source.sha256, f"source hash for '{source.display_name}'")
        if source.source_id != _semantic_id("src", {"sha256": source.sha256}):
            raise ValueError(f"Source ID {source.source_id} is not hash-backed")
        if hashes.get(f"source:{source.source_id}") != source.sha256:
            raise ValueError(f"Source {source.source_id} has no matching input hash")
        expected_track_id = _semantic_id(
            "trk", {"source_id": track.source_id, "sequence_index": track.sequence_index}
        )
        if track.track_instance_id != expected_track_id:
            raise ValueError(f"Track instance ID {track.track_instance_id} is stale")
        section_hash = hashes.get(f"section_map:{track.track_instance_id}")
        if section_hash is None:
            raise ValueError(f"Track {track.track_instance_id} has no section map hash")
        if track.section_map_id != _semantic_id("sec", {"sha256": section_hash}):
            raise ValueError(f"Section map ID {track.section_map_id} is stale")
        if not all(math.isfinite(value) for value in (
                track.arrangement_start_beat, track.arrangement_end_beat)):
            raise ValueError(f"Track {track.track_instance_id} has non-finite geometry")
        if (track.arrangement_start_beat < 0
                or track.arrangement_end_beat <= track.arrangement_start_beat):
            raise ValueError(f"Track {track.track_instance_id} has invalid geometry")

    transition = plan.transitions[0]
    out_track, in_track = plan.tracks
    expected_transition_id = _semantic_id("trn", {
        "out_track_instance_id": out_track.track_instance_id,
        "in_track_instance_id": in_track.track_instance_id,
    })
    if transition.transition_id != expected_transition_id:
        raise ValueError("Transition ID does not match the adjacent track instances")
    if (transition.transition_index != 0
            or transition.out_track_instance_id != out_track.track_instance_id
            or transition.in_track_instance_id != in_track.track_instance_id):
        raise ValueError("Transition does not match main_track_sequence adjacency")
    overlap_beats = out_track.arrangement_end_beat - in_track.arrangement_start_beat
    if not MIN_OVERLAP_BEATS <= overlap_beats <= MAX_OVERLAP_BEATS:
        raise ValueError("Transition overlap is outside the 16-48 bar safety window")
    expected_geometry = (
        in_track.arrangement_start_beat,
        out_track.arrangement_end_beat,
        overlap_beats,
    )
    actual_geometry = (
        transition.overlap_start_beat,
        transition.overlap_end_beat,
        transition.overlap_beats,
    )
    if any(not math.isclose(actual, expected, abs_tol=1e-6)
           for actual, expected in zip(actual_geometry, expected_geometry)):
        raise ValueError("Transition carries stale overlap geometry")

    loop_ids = tuple(loop.loop_id for loop in plan.loops)
    if transition.loop_ids != loop_ids or len(set(loop_ids)) != len(loop_ids):
        raise ValueError("Transition loop IDs do not match unique loop contracts")
    participant_ids = {out_track.track_instance_id, in_track.track_instance_id}
    for loop in plan.loops:
        if loop.transition_id != transition.transition_id:
            raise ValueError(f"Loop {loop.loop_id} belongs to another transition")
        if loop.track_instance_id not in participant_ids:
            raise ValueError(f"Loop {loop.loop_id} targets a non-participant track")
        if not all(math.isfinite(value) for value in (
                loop.source_beat_start, loop.source_beat_end,
                loop.partial_beats, loop.insert_at_beat)):
            raise ValueError(f"Loop {loop.loop_id} has non-finite geometry")
        if (not isinstance(loop.repeat_count, int)
                or isinstance(loop.repeat_count, bool)):
            raise ValueError(f"Loop {loop.loop_id} has an invalid repeat count")
        loop_len = loop.source_beat_end - loop.source_beat_start
        extension = loop.repeat_count * loop_len + loop.partial_beats
        if loop_len <= 0 or loop.repeat_count < 0 or loop.repeat_count > MAX_LOOP_REPEATS:
            raise ValueError(f"Loop {loop.loop_id} violates repeat/length safety")
        if (loop.partial_beats < 0 or loop.partial_beats > loop_len
                or extension > MAX_LOOP_EXTENSION_BEATS or loop.insert_at_beat < 0):
            raise ValueError(f"Loop {loop.loop_id} violates extension/position safety")
        expected_loop_id = _semantic_id("lop", {
            "transition_id": loop.transition_id,
            "track_instance_id": loop.track_instance_id,
            "source_beat_start": loop.source_beat_start,
            "source_beat_end": loop.source_beat_end,
            "repeat_count": loop.repeat_count,
            "partial_beats": loop.partial_beats,
            "insert_at_beat": loop.insert_at_beat,
        })
        if loop.loop_id != expected_loop_id:
            raise ValueError(f"Loop ID {loop.loop_id} is stale")

    expected_hash = _compute_plan_hash(plan)
    if plan.plan_hash != expected_hash:
        raise ValueError(f"MixPlan plan_hash is stale: {plan.plan_hash} != {expected_hash}")


def write_mix_plan(plan: MixPlan, output_path: Path) -> Path:
    validate_mix_plan(plan)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(mix_plan_dict(plan), indent=2, ensure_ascii=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    return output_path
