"""Reconcile an N-track MixPlan against the final post-mutation ALS."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import math
import sys

from automated_dj_mixes.warping import (
    WARP_MODE_COMPLEX_PRO, WARP_MODE_REPITCH)
import xml.etree.ElementTree as ET
from pathlib import Path

from automated_dj_mixes.warp_contract import (
    summarize_marker_pairs, summarize_track_warp_grids, track_marker_pairs)

# The aligner's landmark-mode policy names. Imported from align_engine (the
# single source of truth) so a future policy cannot silently bypass the
# clip-boundary proof below the way tail_anchor_rescue_v1 did when this file
# compared against the "paired_landmarks_v2" literal (Codex round-2 BLOCKER 3).
from align_engine import LANDMARK_POLICIES


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


def _coalesce_automation_splits(
    loop_clips: list[tuple[float, float | None]],
    expected_times: list[float],
    windows: list[tuple[float, float]],
) -> list[float]:
    """Loop-clip start times, minus bookkeeping splits made after the freeze.

    apply_automation splits a loop-repeat clip in two when the bass-swap point
    lands mid-repeat. The two halves play back to back, so the audio equals one
    clip, but the frozen MixPlan (written earlier) cannot list the extra start.
    A start is such a split only if it is NOT a planned boundary, sits strictly
    inside a planned loop window, and touches the previous loop clip's end -
    and at most ONE such split is absorbed between two planned starts (the
    swap is a single point, so it splits a repeat once). Anything else (a gap,
    a stray clip, a start outside the loop, a chain of extra starts) stays in
    the list and fails the comparison exactly as before.
    """
    ordered = sorted(loop_clips, key=lambda clip: clip[0])
    kept: list[float] = []
    prev_end: float | None = None
    absorbed_since_planned = False
    for start, end in ordered:
        planned = any(math.isclose(start, t, abs_tol=1e-6) for t in expected_times)
        inside = any(lo + 1e-6 < start < hi - 1e-6 for lo, hi in windows)
        contiguous = prev_end is not None and math.isclose(start, prev_end, abs_tol=1e-6)
        if planned:
            absorbed_since_planned = False
        if (not planned and inside and contiguous
                and not absorbed_since_planned):
            absorbed_since_planned = True
            prev_end = end
            continue
        kept.append(start)
        prev_end = end
    return kept


def _main_tempo_state(root: ET.Element) -> tuple[float | None, list[tuple[float, float]]]:
    """(static tempo, [(beat, bpm), ...]) for the MainTrack.

    The times used to be discarded, so a caller could only ask "does a tempo
    envelope exist" - never "is it the envelope we planned". Verifying a tempo
    arc needs the positions: the right BPMs in the wrong places is exactly the
    failure worth catching.
    """
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
            for event in envelope.iter("FloatEvent"):
                value = event.get("Value")
                if value is None:
                    continue
                # Ableton writes a large negative sentinel time for the
                # "before all time" anchor point; keep it as-is so a caller
                # comparing against planned points sees exactly what Live has.
                events.append((float(event.get("Time", 0.0)), float(value)))
            events.sort()
    return manual, events


GRID_PHASE_SCHEMA = "grid-phase/1"
# The most a recorded grid phase may move any warp marker. A hard cap of this
# validator, never read from the record: the tool's own limits (12 ms drift plus
# 15 ms slide) sit inside it, and a forged record cannot widen it.
MAX_PHASE_SHIFT_MS = 30.0
_SHA256_HEX = frozenset("0123456789abcdef")


def _reject_constant(name: str):
    raise ValueError(f"{name} is not a number")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_finite(value) -> bool:
    try:
        return _is_number(value) and math.isfinite(value)
    except OverflowError:
        return False


def _is_sha256(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and set(value) <= _SHA256_HEX)


def _load_grid_phase(path: Path) -> tuple[dict | None, list[str]]:
    """The grid phase record, checked for shape only. A record that is missing,
    unreadable or malformed comes back as errors, never as an exception."""
    try:
        record = json.loads(
            path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, ValueError, RecursionError) as exc:
        return None, [f"grid phase record {path.name} cannot be read: {exc}"]
    if not isinstance(record, dict):
        return None, ["grid phase record is not a JSON object"]
    problems: list[str] = []
    if record.get("schema") != GRID_PHASE_SCHEMA:
        problems.append(
            f"grid phase record schema {record.get('schema')!r} is not "
            f"{GRID_PHASE_SCHEMA!r}")
    for key in ("original_als_sha256", "phased_als_sha256"):
        if not _is_sha256(record.get(key)):
            problems.append(f"grid phase record {key} is missing or not a sha256")
    entries = record.get("tracks")
    if not isinstance(entries, list) or not entries:
        problems.append("grid phase record lists no changed track")
        entries = []
    for number, entry in enumerate(entries):
        where = f"grid phase record track {number}"
        if not isinstance(entry, dict):
            problems.append(f"{where} is not an object")
            continue
        if not isinstance(entry.get("name"), str) or not entry["name"]:
            problems.append(f"{where} has no name")
        index = entry.get("doc_index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            problems.append(f"{where} has no doc_index")
        shift = entry.get("max_abs_shift_ms")
        if not _is_finite(shift) or shift < 0:
            problems.append(f"{where} has no max_abs_shift_ms")
        markers = entry.get("original_markers")
        if (not isinstance(markers, list)
                or not all(isinstance(pair, list) and len(pair) == 2
                           and all(_is_number(value) for value in pair)
                           for pair in markers)):
            problems.append(f"{where} original_markers is not a list of [SecTime, BeatTime]")
    return (None, problems) if problems else (record, [])


def _check_phased_track(
    name: str, contract: dict, entry: dict, track: ET.Element,
) -> tuple[list[str], float]:
    """Verify one track the grid phase tool changed: (errors, largest shift in ms).

    The recorded ORIGINAL markers must hash to the plan's pinned grid (the pin is
    still honoured, against the original); the ALS markers must then be the
    recorded ones with the SecTimes moved by at most MAX_PHASE_SHIFT_MS.
    """
    try:
        original = tuple((float(sec), float(beat))
                         for sec, beat in entry["original_markers"])
        recorded = summarize_marker_pairs(original)
    except (ValueError, OverflowError) as exc:
        return [f"{name}: grid phase record markers are not a warp grid: {exc}"], 0.0
    expected = (
        int(contract["warp_marker_count"]),
        contract["warp_grid_sha256"],
        float(contract["source_grid_bpm"]),
    )
    got = (recorded.marker_count, recorded.grid_sha256, recorded.source_grid_bpm)
    if got[:2] != expected[:2] or not math.isclose(got[2], expected[2], abs_tol=1e-9):
        return [f"{name}: grid phase record does not hold the pinned original "
                f"warp grid {got} != {expected}"], 0.0
    try:
        actual = track_marker_pairs(track)
    except ValueError as exc:
        return [f"{name}: {exc}"], 0.0
    if len(actual) != len(original):
        return [f"{name}: phased grid has {len(actual)} markers, the record has "
                f"{len(original)}"], 0.0
    errors: list[str] = []
    beat_moved = [i for i, (a, o) in enumerate(zip(actual, original)) if a[1] != o[1]]
    if beat_moved:
        errors.append(f"{name}: BeatTime differs from the record at markers {beat_moved[:5]}")
    for axis, label in ((0, "SecTime"), (1, "BeatTime")):
        if any(b[axis] <= a[axis] for a, b in zip(actual, actual[1:])):
            errors.append(f"{name}: phased {label} is not strictly increasing")
    shifts = [abs(a[0] - o[0]) * 1000.0 for a, o in zip(actual, original)]
    worst = max(shifts)
    over = [i for i, shift in enumerate(shifts) if shift > MAX_PHASE_SHIFT_MS]
    if over:
        errors.append(
            f"{name}: phased SecTime moved {worst:.3f} ms at marker {shifts.index(worst)}, "
            f"more than {MAX_PHASE_SHIFT_MS:g} ms")
    if not math.isclose(worst, float(entry["max_abs_shift_ms"]), abs_tol=1e-6):
        errors.append(
            f"{name}: the record says the largest shift is "
            f"{float(entry['max_abs_shift_ms']):.6f} ms, the ALS shows {worst:.6f} ms")
    return errors, worst


def reconcile(
    plan_path: Path, report_path: Path, als_path: Path,
    grid_phase_path: Path | None = None,
) -> dict:
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

    phase_entries: dict[str, dict] = {}
    phase_shifts: dict[str, float] = {}
    if grid_phase_path is not None:
        record, problems = _load_grid_phase(grid_phase_path)
        errors.extend(problems)
        if record is not None:
            if record["phased_als_sha256"] != _sha256(als_path):
                errors.append(
                    "grid phase record belongs to another ALS: it describes "
                    f"{record['phased_als_sha256']}, this file is {_sha256(als_path)}")
            document_index = {
                id(track): index
                for index, track in enumerate(root.iter("AudioTrack"))
            }
            actual_by_name = {name: track for name, track, _clips in active_tracks}
            for entry in record["tracks"]:
                name = entry["name"]
                if name in phase_entries:
                    errors.append(f"grid phase record lists {name} twice")
                elif name not in expected_names:
                    errors.append(f"grid phase record lists {name}, which is not in the MixPlan")
                elif name not in actual_by_name:
                    errors.append(f"grid phase record lists {name}, which is not in the ALS")
                elif document_index[id(actual_by_name[name])] != entry["doc_index"]:
                    errors.append(
                        f"grid phase record puts {name} at AudioTrack {entry['doc_index']}, "
                        f"the ALS has it at {document_index[id(actual_by_name[name])]}")
                else:
                    phase_entries[name] = entry

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
    elif tempo_strategy == "tempo_arc_v1":
        # The frozen curve is the authority. Compare the ALS against it point by
        # point; never re-derive a curve here, or the contract stops constraining
        # anything. A missing envelope is a FAILURE, not a default - the first
        # arc build silently lost its envelope and still validated, because
        # nothing was checking.
        tempo = plan.get("tempo") or {}
        planned = [(float(b), float(v)) for b, v in tempo.get("points", [])]
        # Ableton anchors every envelope with a "before all time" event at
        # Time=-63072000 carrying the default value. It is the writer's, not
        # part of our curve, so it must not be compared against the plan.
        tempo_events = [(t, v) for t, v in tempo_events if t > -1e6]
        if not planned:
            errors.append("tempo_arc_v1 plan carries no tempo points")
        elif not tempo_events:
            errors.append(
                "tempo_arc_v1 plan but the ALS has NO MainTrack tempo envelope")
        elif len(tempo_events) != len(planned):
            errors.append(
                f"Tempo envelope has {len(tempo_events)} points, "
                f"plan froze {len(planned)}")
        else:
            bad = [
                (i, got, want)
                for i, (got, want) in enumerate(zip(tempo_events, planned))
                if not (math.isclose(got[0], want[0], abs_tol=1e-3)
                        and math.isclose(got[1], want[1], abs_tol=1e-3))
            ]
            if bad:
                errors.append(f"Tempo envelope differs from the plan at {bad[:3]}")
            elif not math.isclose(manual_tempo, planned[0][1], abs_tol=1e-3):
                errors.append(
                    f"Static tempo {manual_tempo} does not match the arc's "
                    f"opening value {planned[0][1]}")
            else:
                checks.append(f"tempo_arc:{len(planned)}pts")
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
        expected_warp = {"repitch": WARP_MODE_REPITCH,
                         "complex_pro": WARP_MODE_COMPLEX_PRO}.get(contract.get("warp_mode"))
        if expected_warp is None or modes != {float(expected_warp)}:
            errors.append(f"{name}: WarpMode mismatch {modes} != {expected_warp}")
        else:
            checks.append(f"warp:{contract['track_instance_id']}")
        if name in phase_entries:
            phase_errors, phase_shifts[name] = _check_phased_track(
                name, contract, phase_entries[name], _track)
            errors.extend(phase_errors)
            if not phase_errors:
                checks.append(f"warp_grid_phased:{contract['track_instance_id']}")
            continue
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
    loop_windows_by_track: dict[str, list[tuple[float, float]]] = {}
    for loop in plan["loops"]:
        loop_len = loop["source_beat_end"] - loop["source_beat_start"]
        loop_windows_by_track.setdefault(loop["track_instance_id"], []).append((
            loop["insert_at_beat"],
            loop["insert_at_beat"] + loop["repeat_count"] * loop_len
            + loop["partial_beats"],
        ))
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
        expected_times = sorted(expected_times)
        actual_times = _coalesce_automation_splits(
            [
                (float(clip.get("Time")), _float(clip.find("CurrentEnd")))
                for clip in clips
                if _clip_name(clip).endswith("_tail_loop")
                or _clip_name(clip).endswith("_intro_loop")
            ],
            expected_times,
            loop_windows_by_track[track_id],
        )
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
        matches_loop_boundary = any(
            math.isclose(swap, boundary, abs_tol=1e-6)
            for boundary in loop_boundaries
        )
        swap_inside_loop = any(
            loop["insert_at_beat"] - 1e-6 <= swap <= (
                loop["insert_at_beat"]
                + loop["repeat_count"] * (
                    loop["source_beat_end"] - loop["source_beat_start"]
                )
                + loop["partial_beats"]
                + 1e-6
            )
            for loop in outgoing_loops
        )
        if outgoing_loops and not matches_loop_boundary and swap_inside_loop:
            errors.append(
                f"Transition {transition['transition_index'] + 1} swap does not match "
                "the frozen outgoing loop boundary"
            )
        else:
            checks.append(f"bass_swap:{transition['transition_id']}")
            if outgoing_loops and not matches_loop_boundary:
                checks.append(
                    f"loop_after_swap:{transition['transition_id']}:"
                    "tail_plays_under_incoming"
                )
        # Clip-boundary proof for landmark-mode transitions (the check that
        # exposed V9). The final ALS writer splits both tracks at every
        # landmark-policy swap, including a rescue's grid-derived outgoing
        # anchor, so BOTH roles carry the same real boundary requirement.
        # Membership comes from align_engine.LANDMARK_POLICIES: a new
        # landmark policy cannot silently bypass this assertion.
        policy = report_transition.get("alignment_policy")
        if policy in LANDMARK_POLICIES:
            roles = (("outgoing", "out_track"), ("incoming", "in_track"))
            prefix = (
                "rescue_boundary"
                if policy == "tail_anchor_rescue_v1"
                else "paired_boundary"
            )
            for role, key in roles:
                name = html.unescape(report_transition[key])
                clips = track_by_name.get(name, (None, []))[1]
                if not _matches_clip_boundary(clips, swap):
                    errors.append(
                        f"Transition {transition['transition_index'] + 1} {role} "
                        f"track has no clip boundary at frozen swap {swap:g} "
                        f"(policy {policy})"
                    )
                else:
                    checks.append(
                        f"{prefix}:{transition['transition_id']}:{role}"
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
    if grid_phase_path is not None and not errors:
        result["grid_phase"] = {
            "record_sha256": _sha256(grid_phase_path),
            "original_als_sha256": record["original_als_sha256"],
            "phased_als_sha256": record["phased_als_sha256"],
            "track_count": len(phase_shifts),
            "max_shift_ms": max(phase_shifts.values()),
            "max_allowed_shift_ms": MAX_PHASE_SHIFT_MS,
            "tracks": [
                {"name": name, "doc_index": phase_entries[name]["doc_index"],
                 "max_shift_ms": shift}
                for name, shift in phase_shifts.items()
            ],
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
    parser.add_argument(
        "--grid-phase", type=Path,
        help="grid_phase.json written with a phased ALS: the tracks it lists are "
             "checked against their recorded original grid instead of the pinned one")
    args = parser.parse_args()
    if args.grid_phase is None:
        result = reconcile(args.mix_plan, args.arrangement_report, args.als)
    else:
        try:
            result = reconcile(
                args.mix_plan, args.arrangement_report, args.als, args.grid_phase)
        except ValueError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    line = f"PASS: {len(result['checks'])} MixPlan-to-ALS checks"
    if "grid_phase" in result:
        phase = result["grid_phase"]
        line += (f", grid_phase: {phase['track_count']} tracks, "
                 f"max shift {phase['max_shift_ms']:.3f} ms")
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
