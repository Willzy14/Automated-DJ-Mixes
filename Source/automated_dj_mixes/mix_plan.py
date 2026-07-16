"""Immutable, hash-backed production intent for the DJ mix pipeline."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Mapping

from apply_loops import MAX_LOOP_EXTENSION_BEATS, MAX_LOOP_REPEATS
from automated_dj_mixes.warp_contract import WarpGridSummary


SCHEMA_VERSION = "1.3"
PRODUCTION_SCOPE = "multi_transition_arrangement_v1"
MIN_OVERLAP_BEATS = 64.0
MAX_OVERLAP_BEATS = 192.0
MAX_LANDMARK_OVERLAP_BEATS = 256.0
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
    warp_marker_count: int
    warp_grid_sha256: str
    source_grid_bpm: float
    warp_mode: str
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
    overlap_policy: str


@dataclass(frozen=True)
class MixPlan:
    schema_version: str
    production_scope: str
    plan_version: int
    parent_plan_hash: str | None
    project_bpm: float | None
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


def build_mix_plan(
    arrangement_plan,
    *,
    source_hashes: Mapping[str, str],
    section_map_hashes: Mapping[str, str],
    warp_grid_contracts: Mapping[str, WarpGridSummary],
    project_bpm: float | None = None,
    warp_modes: Mapping[str, str] | None = None,
    input_hashes: Mapping[str, str] | None = None,
    policy_versions: Mapping[str, str] | None = None,
    tool_versions: Mapping[str, str] | None = None,
    human_overrides: Mapping[str, str] | None = None,
    plan_version: int = 1,
    parent_plan_hash: str | None = None,
) -> MixPlan:
    """Convert a validated N-track ArrangementPlan into canonical intent."""
    from propose_arrangement import validate_arrangement_plan

    validate_arrangement_plan(arrangement_plan)
    if len(arrangement_plan.tracks) < 2:
        raise ValueError("MixPlan requires at least two tracks")
    if plan_version < 1:
        raise ValueError("plan_version must be at least 1")
    if plan_version > 1 and not parent_plan_hash:
        raise ValueError("A revised MixPlan requires parent_plan_hash")
    if parent_plan_hash is not None:
        _normalise_digest(parent_plan_hash, "parent_plan_hash")

    if project_bpm is not None and (
        not math.isfinite(project_bpm) or not 60.0 <= project_bpm <= 200.0
    ):
        raise ValueError(f"Invalid project BPM: {project_bpm!r}")
    warp_modes = dict(warp_modes or {})

    source_contracts: list[SourceContract] = []
    track_contracts: list[TrackInstanceContract] = []
    extra_hashes = dict(input_hashes or {})

    for index, track in enumerate(arrangement_plan.tracks):
        if track.name not in source_hashes:
            raise ValueError(f"Missing certified source hash for '{track.name}'")
        if track.name not in section_map_hashes:
            raise ValueError(f"Missing section map hash for '{track.name}'")
        if track.name not in warp_grid_contracts:
            raise ValueError(f"Missing warp-grid contract for '{track.name}'")

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
        warp_grid = warp_grid_contracts[track.name]
        warp_mode = warp_modes.get(track.name, "inherited")
        if warp_mode not in ("inherited", "repitch", "complex_pro"):
            raise ValueError(f"Unsupported warp mode for '{track.name}': {warp_mode!r}")
        if project_bpm is None and warp_mode != "inherited":
            raise ValueError("Explicit track warp modes require project_bpm")
        if project_bpm is not None and warp_mode == "inherited":
            raise ValueError(f"Missing explicit warp mode for '{track.name}'")

        if not any(source.source_id == source_id for source in source_contracts):
            source_contracts.append(SourceContract(source_id, track.name, source_hash))
        track_contracts.append(TrackInstanceContract(
            track_instance_id=track_instance_id,
            source_id=source_id,
            section_map_id=section_map_id,
            sequence_index=index,
            display_name=track.name,
            warp_marker_count=int(warp_grid.marker_count),
            warp_grid_sha256=_normalise_digest(
                warp_grid.grid_sha256, f"warp grid for '{track.name}'"
            ),
            source_grid_bpm=float(warp_grid.source_grid_bpm),
            warp_mode=warp_mode,
            arrangement_start_beat=float(track.arr_start),
            arrangement_end_beat=float(track.arr_end),
        ))
        extra_hashes[f"source:{source_id}"] = source_hash
        extra_hashes[f"section_map:{track_instance_id}"] = section_hash

    track_id_by_name = {
        track.display_name: track.track_instance_id for track in track_contracts
    }

    loop_contracts: list[LoopContract] = []
    transition_contracts: list[TransitionContract] = []
    seen_loop_objects: set[int] = set()
    for index, overlap in enumerate(arrangement_plan.overlaps):
        out_track = track_contracts[index]
        in_track = track_contracts[index + 1]
        transition_id = _semantic_id("trn", {
            "out_track_instance_id": out_track.track_instance_id,
            "in_track_instance_id": in_track.track_instance_id,
        })
        transition_loops: list[LoopContract] = []
        for spec in (overlap.out_tail_loop, overlap.in_intro_loop):
            if spec is None:
                continue
            seen_loop_objects.add(id(spec))
            track_instance_id = track_id_by_name.get(spec.track_name)
            if track_instance_id is None:
                raise ValueError(f"Loop target '{spec.track_name}' is unknown")
            loop_value = {
                "transition_id": transition_id,
                "track_instance_id": track_instance_id,
                "source_beat_start": float(spec.source_beat_start),
                "source_beat_end": float(spec.source_beat_end),
                "repeat_count": spec.count,
                "partial_beats": float(spec.tail_partial_beats),
                "insert_at_beat": float(spec.insert_at_beat),
            }
            transition_loops.append(LoopContract(
                loop_id=_semantic_id("lop", loop_value),
                **loop_value,
            ))
        loop_contracts.extend(transition_loops)
        transition_contracts.append(TransitionContract(
            transition_id=transition_id,
            transition_index=index,
            out_track_instance_id=out_track.track_instance_id,
            in_track_instance_id=in_track.track_instance_id,
            overlap_start_beat=float(overlap.overlap_start),
            overlap_end_beat=float(overlap.overlap_end),
            overlap_beats=float(overlap.overlap_beats),
            loop_ids=tuple(loop.loop_id for loop in transition_loops),
            overlap_policy=getattr(overlap, "overlap_policy", "standard_48"),
        ))
    if seen_loop_objects != {id(spec) for spec in arrangement_plan.loops}:
        raise ValueError("Arrangement contains a loop not owned by a transition")

    policies = {"loop_safety": "safety_v1", "overlap_safety": "safety_v1"}
    policies.update(policy_versions or {})
    tools = {"mix_plan": SCHEMA_VERSION}
    tools.update(tool_versions or {})

    provisional = MixPlan(
        schema_version=SCHEMA_VERSION,
        production_scope=PRODUCTION_SCOPE,
        plan_version=plan_version,
        parent_plan_hash=parent_plan_hash,
        project_bpm=float(project_bpm) if project_bpm is not None else None,
        main_track_sequence=tuple(
            track.track_instance_id for track in track_contracts
        ),
        sources=tuple(source_contracts),
        tracks=tuple(track_contracts),
        loops=tuple(loop_contracts),
        transitions=tuple(transition_contracts),
        input_hashes=_artifact_hashes(extra_hashes),
        policy_versions=_metadata_entries(policies),
        tool_versions=_metadata_entries(tools),
        human_overrides=_metadata_entries(human_overrides or {}),
        plan_hash="",
    )
    plan = replace(provisional, plan_hash=_compute_plan_hash(provisional))
    validate_mix_plan(plan)
    return plan


def build_one_transition_mix_plan(arrangement_plan, **kwargs) -> MixPlan:
    """Backward-compatible entry point for the original two-track scope."""
    if len(arrangement_plan.tracks) != 2 or len(arrangement_plan.overlaps) != 1:
        raise ValueError("One-transition MixPlan requires exactly two tracks")
    return build_mix_plan(arrangement_plan, **kwargs)


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

    if len(plan.tracks) < 2 or len(plan.transitions) != len(plan.tracks) - 1:
        raise ValueError("MixPlan requires one transition per adjacent track pair")
    if plan.project_bpm is not None and (
        not math.isfinite(plan.project_bpm) or not 60.0 <= plan.project_bpm <= 200.0
    ):
        raise ValueError(f"Invalid project BPM: {plan.project_bpm!r}")
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

    for expected_index, track in enumerate(plan.tracks):
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
        if track.sequence_index != expected_index:
            raise ValueError(f"Track {track.track_instance_id} has stale sequence_index")
        section_hash = hashes.get(f"section_map:{track.track_instance_id}")
        if section_hash is None:
            raise ValueError(f"Track {track.track_instance_id} has no section map hash")
        if track.section_map_id != _semantic_id("sec", {"sha256": section_hash}):
            raise ValueError(f"Section map ID {track.section_map_id} is stale")
        if track.warp_marker_count < 2:
            raise ValueError(f"Track {track.track_instance_id} has too few warp markers")
        _normalise_digest(
            track.warp_grid_sha256, f"warp grid for '{track.display_name}'"
        )
        if (not math.isfinite(track.source_grid_bpm)
                or not 40.0 <= track.source_grid_bpm <= 300.0):
            raise ValueError(f"Track {track.track_instance_id} has invalid source-grid BPM")
        if track.warp_mode not in ("inherited", "repitch", "complex_pro"):
            raise ValueError(f"Track {track.track_instance_id} has invalid warp mode")
        if plan.project_bpm is None and track.warp_mode != "inherited":
            raise ValueError("Explicit track warp modes require project_bpm")
        if plan.project_bpm is not None and track.warp_mode == "inherited":
            raise ValueError(f"Track {track.track_instance_id} has no explicit warp mode")
        if not all(math.isfinite(value) for value in (
                track.arrangement_start_beat, track.arrangement_end_beat)):
            raise ValueError(f"Track {track.track_instance_id} has non-finite geometry")
        if (track.arrangement_start_beat < 0
                or track.arrangement_end_beat <= track.arrangement_start_beat):
            raise ValueError(f"Track {track.track_instance_id} has invalid geometry")

    loops_by_id = {loop.loop_id: loop for loop in plan.loops}
    if len(loops_by_id) != len(plan.loops):
        raise ValueError("MixPlan contains duplicate loop IDs")
    claimed_loop_ids: list[str] = []
    transition_by_id: dict[str, TransitionContract] = {}
    for index, transition in enumerate(plan.transitions):
        out_track = plan.tracks[index]
        in_track = plan.tracks[index + 1]
        expected_transition_id = _semantic_id("trn", {
            "out_track_instance_id": out_track.track_instance_id,
            "in_track_instance_id": in_track.track_instance_id,
        })
        if transition.transition_id != expected_transition_id:
            raise ValueError("Transition ID does not match adjacent track instances")
        if (transition.transition_index != index
                or transition.out_track_instance_id != out_track.track_instance_id
                or transition.in_track_instance_id != in_track.track_instance_id):
            raise ValueError("Transition does not match main_track_sequence adjacency")
        overlap_beats = out_track.arrangement_end_beat - in_track.arrangement_start_beat
        max_overlap = (MAX_LANDMARK_OVERLAP_BEATS
                       if transition.overlap_policy == "named_landmark_64"
                       else MAX_OVERLAP_BEATS)
        if transition.overlap_policy not in ("standard_48", "named_landmark_64"):
            raise ValueError("Transition has an unknown overlap policy")
        if not MIN_OVERLAP_BEATS <= overlap_beats <= max_overlap:
            raise ValueError(
                f"Transition overlap is outside its 16-{max_overlap / 4:g} bar safety window"
            )
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
        if len(set(transition.loop_ids)) != len(transition.loop_ids):
            raise ValueError("Transition contains duplicate loop IDs")
        claimed_loop_ids.extend(transition.loop_ids)
        transition_by_id[transition.transition_id] = transition

    if set(claimed_loop_ids) != set(loops_by_id) or len(claimed_loop_ids) != len(loops_by_id):
        raise ValueError("Transition loop IDs do not match unique loop contracts")
    for loop in plan.loops:
        transition = transition_by_id.get(loop.transition_id)
        if transition is None or loop.loop_id not in transition.loop_ids:
            raise ValueError(f"Loop {loop.loop_id} belongs to another transition")
        participant_ids = {
            transition.out_track_instance_id,
            transition.in_track_instance_id,
        }
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
