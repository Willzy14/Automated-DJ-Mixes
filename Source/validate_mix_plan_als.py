"""Reconcile an N-track MixPlan against the final post-mutation ALS."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

from automated_dj_mixes.warp_contract import summarize_track_warp_grids


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(plan: dict) -> str:
    payload = dict(plan)
    payload.pop("plan_hash", None)
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _track_name(track: ET.Element) -> str:
    effective = next(track.iter("EffectiveName"), None)
    return html.unescape(effective.get("Value", "")) if effective is not None else ""


def _clip_name(clip: ET.Element) -> str:
    direct = clip.find("Name")
    return direct.get("Value", "") if direct is not None else ""


def _float(child: ET.Element | None, attribute: str = "Value") -> float | None:
    if child is None or child.get(attribute) is None:
        return None
    return float(child.get(attribute))


def _matches_clip_boundary(clips: list[ET.Element], beat: float) -> bool:
    boundaries = [
        value
        for clip in clips
        for value in (
            float(clip.get("Time")),
            _float(clip.find("CurrentEnd")),
        )
        if value is not None
    ]
    return any(math.isclose(beat, boundary, abs_tol=1e-6) for boundary in boundaries)


def _main_tempo_state(root: ET.Element) -> tuple[float | None, list[float]]:
    main_track = next(root.iter("MainTrack"), None)
    if main_track is None:
        return None, []
    tempo = next(main_track.iter("Tempo"), None)
    if tempo is None:
        return None, []
    manual = _float(tempo.find("Manual"))
    target = tempo.find("AutomationTarget")
    target_id = target.get("Id") if target is not None else None
    events: list[float] = []
    if target_id is not None:
        for envelope in main_track.iter("AutomationEnvelope"):
            pointee = envelope.find(".//PointeeId")
            if pointee is None or pointee.get("Value") != target_id:
                continue
            events.extend(
                float(event.get("Value"))
                for event in envelope.iter("FloatEvent")
                if event.get("Value") is not None
            )
    return manual, events


def reconcile(plan_path: Path, report_path: Path, als_path: Path) -> dict:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    checks: list[str] = []

    expected_hash = _canonical_hash(plan)
    if plan.get("plan_hash") != expected_hash:
        errors.append("MixPlan plan_hash is stale")
    else:
        checks.append("plan_hash")

    with gzip.open(als_path, "rb") as handle:
        root = ET.fromstring(handle.read())
    active_tracks = []
    for track in root.iter("AudioTrack"):
        clips = list(track.iter("AudioClip"))
        if clips:
            active_tracks.append((_track_name(track), track, clips))

    expected_tracks = plan["tracks"]
    expected_names = [html.unescape(track["display_name"]) for track in expected_tracks]
    actual_names = [name for name, _track, _clips in active_tracks]
    if actual_names != expected_names:
        errors.append(f"Active track sequence mismatch: {actual_names} != {expected_names}")
    else:
        checks.append("main_track_sequence")

    overrides = {item["name"]: item["value"] for item in plan.get("human_overrides", [])}
    expected_bpm = float(plan.get("project_bpm") or overrides.get("project_bpm", "nan"))
    tempo_strategy = {
        item["name"]: item["value"] for item in plan.get("policy_versions", [])
    }.get("tempo_strategy")
    manual_tempo, tempo_events = _main_tempo_state(root)
    if (not math.isfinite(expected_bpm) or manual_tempo is None
            or not math.isclose(manual_tempo, expected_bpm, abs_tol=1e-6)):
        errors.append(f"Project tempo does not match MixPlan {expected_bpm}")
    elif tempo_strategy == "fixed_center_v1" and tempo_events:
        errors.append(
            "Fixed project tempo is overridden by a MainTrack tempo envelope: "
            f"{tempo_events}"
        )
    else:
        checks.append("project_tempo")

    track_by_name = {name: (track, clips) for name, track, clips in active_tracks}
    for contract in expected_tracks:
        name = html.unescape(contract["display_name"])
        found = track_by_name.get(name)
        if found is None:
            continue
        _track, clips = found
        starts = [float(clip.get("Time")) for clip in clips]
        ends = [_float(clip.find("CurrentEnd")) for clip in clips]
        if not math.isclose(min(starts), contract["arrangement_start_beat"], abs_tol=1e-6):
            errors.append(f"{name}: arrangement start mismatch")
        if not math.isclose(max(ends), contract["arrangement_end_beat"], abs_tol=1e-6):
            errors.append(f"{name}: arrangement end mismatch")
        modes = {_float(clip.find("WarpMode")) for clip in clips}
        expected_warp = {"repitch": 6, "complex_pro": 4}.get(contract.get("warp_mode"))
        if expected_warp is None or modes != {float(expected_warp)}:
            errors.append(f"{name}: WarpMode mismatch {modes} != {expected_warp}")
        else:
            checks.append(f"warp:{contract['track_instance_id']}")
        try:
            warp_grid = summarize_track_warp_grids(_track)
        except ValueError as exc:
            errors.append(f"{name}: {exc}")
        else:
            expected_grid = (
                int(contract["warp_marker_count"]),
                contract["warp_grid_sha256"],
                float(contract["source_grid_bpm"]),
            )
            actual_grid = (
                warp_grid.marker_count,
                warp_grid.grid_sha256,
                warp_grid.source_grid_bpm,
            )
            if (actual_grid[:2] != expected_grid[:2]
                    or not math.isclose(actual_grid[2], expected_grid[2], abs_tol=1e-9)):
                errors.append(
                    f"{name}: warp grid mismatch "
                    f"{actual_grid} != {expected_grid}"
                )
            else:
                checks.append(f"warp_grid:{contract['track_instance_id']}")

    expected_loop_times: dict[str, list[float]] = {}
    loop_ids_by_track: dict[str, list[str]] = {}
    for loop in plan["loops"]:
        loop_len = loop["source_beat_end"] - loop["source_beat_start"]
        expected_loop_times.setdefault(loop["track_instance_id"], []).extend(
            loop["insert_at_beat"] + index * loop_len
            for index in range(loop["repeat_count"])
        )
        if loop["partial_beats"] > 0:
            expected_loop_times[loop["track_instance_id"]].append(
                loop["insert_at_beat"] + loop["repeat_count"] * loop_len
            )
        loop_ids_by_track.setdefault(loop["track_instance_id"], []).append(loop["loop_id"])
    for track_id, expected_times in expected_loop_times.items():
        track_contract = next(
            track for track in expected_tracks if track["track_instance_id"] == track_id
        )
        name = html.unescape(track_contract["display_name"])
        clips = track_by_name.get(name, (None, []))[1]
        actual_times = sorted(
            float(clip.get("Time")) for clip in clips
            if _clip_name(clip).endswith("_tail_loop")
            or _clip_name(clip).endswith("_intro_loop")
        )
        expected_times = sorted(expected_times)
        if actual_times != expected_times:
            errors.append(f"{name}: loop times mismatch: {actual_times} != {expected_times}")
        else:
            checks.extend(f"loop:{loop_id}" for loop_id in loop_ids_by_track[track_id])

    report_transitions = report["transitions"]
    if len(report_transitions) != len(plan["transitions"]):
        errors.append("Arrangement report transition count does not match MixPlan")
    swaps_by_track: dict[str, list[float]] = {}
    for transition, report_transition in zip(plan["transitions"], report_transitions):
        swap = float(report_transition["swap_beats"])
        outgoing_loops = [
            loop for loop in plan["loops"]
            if loop["transition_id"] == transition["transition_id"]
            and loop["track_instance_id"] == transition["out_track_instance_id"]
        ]
        loop_boundaries = [
            loop["insert_at_beat"] + repeat * (
                loop["source_beat_end"] - loop["source_beat_start"]
            )
            for loop in outgoing_loops
            for repeat in range(loop["repeat_count"] + 1)
        ]
        if outgoing_loops and not any(
                math.isclose(swap, boundary, abs_tol=1e-6)
                for boundary in loop_boundaries):
            errors.append(
                f"Transition {transition['transition_index'] + 1} swap does not match "
                "the frozen outgoing loop boundary"
            )
        else:
            checks.append(f"bass_swap:{transition['transition_id']}")
        if report_transition.get("alignment_policy") == "paired_landmarks_v2":
            for role, key in (("outgoing", "out_track"), ("incoming", "in_track")):
                name = html.unescape(report_transition[key])
                clips = track_by_name.get(name, (None, []))[1]
                if not _matches_clip_boundary(clips, swap):
                    errors.append(
                        f"Transition {transition['transition_index'] + 1} {role} "
                        f"track has no clip boundary at frozen swap {swap:g}"
                    )
                else:
                    checks.append(
                        f"paired_boundary:{transition['transition_id']}:{role}"
                    )
        swaps_by_track.setdefault(transition["out_track_instance_id"], []).append(swap)
        swaps_by_track.setdefault(transition["in_track_instance_id"], []).append(swap)

    track_id_by_name = {
        html.unescape(contract["display_name"]): contract["track_instance_id"]
        for contract in expected_tracks
    }
    for name, track, _clips in active_tracks:
        envelopes = {}
        for envelope in track.iter("AutomationEnvelope"):
            pointee = envelope.find(".//PointeeId")
            if pointee is None:
                continue
            events = [
                (float(event.get("Time")), float(event.get("Value")))
                for event in envelope.iter("FloatEvent")
                if event.get("Time") is not None and float(event.get("Time")) > -1e6
            ]
            if events:
                envelopes[pointee.get("Value")] = events
        event_sets = list(envelopes.values())
        required_swaps = swaps_by_track.get(track_id_by_name.get(name, ""), [])
        if len(event_sets) < 2:
            errors.append(f"{name}: missing transition automation envelopes")
            continue
        for swap in required_swaps:
            if not sum(
                any(math.isclose(time, swap, abs_tol=1e-6) for time, _ in events)
                for events in event_sets
            ) >= 2:
                errors.append(f"{name}: automation does not implement swap beat {swap}")
            else:
                checks.append(f"automation:{name}:{swap:g}")

    result = {
        "status": "PASS" if not errors else "FAIL",
        "plan_hash": plan.get("plan_hash"),
        "als_sha256": _sha256(als_path),
        "arrangement_report_sha256": _sha256(report_path),
        "checks": checks,
        "errors": errors,
    }
    if errors:
        raise ValueError("; ".join(errors))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mix_plan", type=Path)
    parser.add_argument("arrangement_report", type=Path)
    parser.add_argument("als", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = reconcile(args.mix_plan, args.arrangement_report, args.als)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"PASS: {len(result['checks'])} MixPlan-to-ALS checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
