"""Diff two Sections .als files (Claude proposal vs Sam correction) and extract
learned automation patterns into the Mix Patterns Library.

Reads both ALS files, extracts automation envelopes per track, compares them,
classifies corrections, and appends entries to pair_history.jsonl.

Usage:
    python Source/learn_from_correction.py <claude.als> <sam.als> <sections.json> [options]

Options:
    --project NAME          Project name (default: derived from path)
    --library PATH          Mix Patterns Library dir (default: Documentation/Mix Patterns Library/)
    --dry-run               Print report only, don't write to pair_history.jsonl

Example:
    python Source/learn_from_correction.py ^
        "Test Project/.../Sections V21.als" ^
        "Test Project/.../Sections V22.als" ^
        "Test Project/.../Sections_V20.json" ^
        --project "Black Book x Defected V2"
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


# ── Reused from apply_automation.py ──────────────────────────────────────────

def decompress_als(als_path: Path) -> list[str]:
    with gzip.open(als_path, "rb") as f:
        content = f.read().decode("utf-8")
    return content.splitlines(keepends=True)


def find_track_line_ranges(lines: list[str]) -> list[tuple[int, int, str]]:
    """Return [(start_line, end_line, effective_name), ...] for AudioTracks."""
    tracks: list[tuple[int, int, str]] = []
    track_start: int | None = None
    depth = 0
    track_name = ""
    for i, line in enumerate(lines):
        if "<AudioTrack " in line:
            if track_start is None:
                track_start = i
                depth = 1
                track_name = ""
            else:
                depth += 1
        elif track_start is not None:
            if "<EffectiveName" in line and not track_name:
                m = re.search(r'Value="([^"]*)"', line)
                if m:
                    track_name = m.group(1)
            if "</AudioTrack>" in line:
                depth -= 1
                if depth == 0:
                    tracks.append((track_start, i, track_name))
                    track_start = None
    return tracks


def _find_target(lines: list[str], start: int, end: int,
                 device_tag: str, param_tag: str) -> str | None:
    """Walk a track's lines to find an AutomationTarget Id for a device+param."""
    in_device = False
    in_param = False
    for i in range(start, end + 1):
        line = lines[i]
        if f"<{device_tag} " in line or f"<{device_tag}>" in line:
            in_device = True
        if in_device and f"</{device_tag}>" in line:
            in_device = False
            in_param = False
        if in_device and f"<{param_tag}>" in line:
            in_param = True
        if in_param and "AutomationTarget" in line and 'Id="' in line:
            m = re.search(r'Id="(\d+)"', line)
            if m:
                return m.group(1)
            in_param = False
    return None


# ── Envelope extraction ──────────────────────────────────────────────────────

SENTINEL_THRESHOLD = -1_000_000  # skip Time < this (sentinel events)


@dataclass
class TrackAutomation:
    name: str
    volume_target_id: str | None = None
    bass_target_id: str | None = None
    volume_points: list[tuple[float, float]] = field(default_factory=list)
    bass_points: list[tuple[float, float]] = field(default_factory=list)


def extract_track_automation(lines: list[str],
                             als_tracks: list[tuple[int, int, str]]
                             ) -> dict[str, TrackAutomation]:
    """Extract automation envelopes for all tracks in an ALS."""
    result: dict[str, TrackAutomation] = {}

    for start, end, name in als_tracks:
        # skip placeholder tracks
        if not name or "Audio" in name and "-" not in name:
            continue

        ta = TrackAutomation(name=name)

        # discover target IDs via device chain walk
        ta.volume_target_id = _find_target(lines, start, end,
                                           "StereoGain", "Gain")
        ta.bass_target_id = _find_target(lines, start, end,
                                         "ChannelEq", "LowShelfGain")

        # build reverse map: target_id -> param_name
        id_to_param: dict[str, str] = {}
        if ta.volume_target_id:
            id_to_param[ta.volume_target_id] = "volume"
        if ta.bass_target_id:
            id_to_param[ta.bass_target_id] = "bass"

        # scan envelopes for this track
        in_envelope = False
        pointee_id: str | None = None
        events: list[tuple[float, float]] = []

        for i in range(start, end + 1):
            stripped = lines[i].strip()

            if "<AutomationEnvelope " in stripped:
                in_envelope = True
                pointee_id = None
                events = []

            if in_envelope and "<PointeeId Value=" in stripped:
                m = re.search(r'Value="(\d+)"', stripped)
                if m:
                    pointee_id = m.group(1)

            if in_envelope and "<FloatEvent " in stripped:
                tm = re.search(r'Time="([^"]+)"', stripped)
                vm = re.search(r'Value="([^"]+)"', stripped)
                if tm and vm:
                    t = float(tm.group(1))
                    v = float(vm.group(1))
                    if t > SENTINEL_THRESHOLD:
                        events.append((t, round(v, 6)))

            if in_envelope and "</AutomationEnvelope>" in stripped:
                in_envelope = False
                if pointee_id and events:
                    param = id_to_param.get(pointee_id)
                    if param == "volume":
                        ta.volume_points = events
                    elif param == "bass":
                        ta.bass_points = events

        result[name] = ta

    return result


# ── Track matching helpers ───────────────────────────────────────────────────

def _normalise(s: str) -> str:
    return s.lower().replace("–", "-").replace("—", "-").strip()


def _match_name(a: str, b: str) -> bool:
    na, nb = _normalise(a), _normalise(b)
    return na == nb or na in nb or nb in na or na[:20] in nb or nb[:20] in na


def _short(name: str) -> str:
    parts = name.split(" - ")
    if len(parts) >= 2:
        return parts[1].split(" (")[0].split(" SW")[0].split(" 24")[0][:20]
    return name[:25]


# ── Sections parsing ─────────────────────────────────────────────────────────

@dataclass
class TrackInfo:
    name: str
    sections: list[dict]
    arr_start: float
    arr_end: float


def ordered_tracks_from_json(sections: dict) -> list[TrackInfo]:
    tracks: list[TrackInfo] = []
    for name, secs in sections.items():
        if not secs:
            continue
        tracks.append(TrackInfo(
            name=name,
            sections=secs,
            arr_start=secs[0]["arr_time"],
            arr_end=secs[-1]["arr_end"],
        ))
    tracks.sort(key=lambda t: t.arr_start)
    return tracks


# ── Diff analysis ────────────────────────────────────────────────────────────

@dataclass
class ParamDiff:
    param: str  # "volume" or "bass"
    claude_points: list[tuple[float, float]]
    sam_points: list[tuple[float, float]]
    changed: bool = False


@dataclass
class TransitionDiff:
    pair_index: int
    out_name: str
    in_name: str
    overlap_start: float
    overlap_end: float
    overlap_bars: float
    # per-param diffs for both sides of the transition
    out_volume: ParamDiff | None = None
    out_bass: ParamDiff | None = None
    in_volume: ParamDiff | None = None
    in_bass: ParamDiff | None = None
    # derived
    bass_swap_claude: float | None = None
    bass_swap_sam: float | None = None
    arrangement_changed: bool = False
    corrections: list[str] = field(default_factory=list)
    verdict: str = "correct"
    classified_style: str = "standard"
    notes: str = ""


def _classify_style(td_out_vol: ParamDiff | None,
                    td_out_bass: ParamDiff | None,
                    td_in_vol: ParamDiff | None,
                    overlap_start: float,
                    overlap_end: float) -> str:
    """Classify which TransitionStyle Sam's corrections most closely match.

    Returns "standard", "long_blend", or "quick_swap".
    """
    sam_in_vol = td_in_vol.sam_points if td_in_vol else []
    sam_out_bass = td_out_bass.sam_points if td_out_bass else []

    sneak = _find_sneak_level(sam_in_vol, overlap_start)
    has_sneak = sneak is not None and sneak > 0.01

    bass_kill_val = None
    for t, v in sam_out_bass:
        if overlap_start <= t <= overlap_end and v < 0.9:
            bass_kill_val = v
            break

    partial_bass = bass_kill_val is not None and bass_kill_val > 0.25

    vol_points_in_zone = [(t, v) for t, v in sam_in_vol
                          if overlap_start - 4 <= t <= overlap_end + 4]
    instant_swap = False
    if len(vol_points_in_zone) >= 2:
        for i in range(len(vol_points_in_zone) - 1):
            t1, v1 = vol_points_in_zone[i]
            t2, v2 = vol_points_in_zone[i + 1]
            if v1 < 0.3 and v2 > 0.8 and abs(t2 - t1) < 1:
                instant_swap = True
                break

    if instant_swap and not has_sneak:
        return "quick_swap"
    if partial_bass and has_sneak and sneak < 0.18:
        return "long_blend"
    return "standard"


def _find_bass_swap_beat(points: list[tuple[float, float]],
                         overlap_start: float,
                         overlap_end: float,
                         role: str) -> float | None:
    """Find the bass swap beat from automation points.

    For outgoing: the beat where value first drops significantly from unity.
        Threshold 0.8 catches both hard kills (0.18) and partial cuts (0.52).
    For incoming: the beat where value rises from kill to unity (0.18 -> 1.0).
    """
    for i, (t, v) in enumerate(points):
        if t < overlap_start - 10 or t > overlap_end + 10:
            continue
        if role == "outgoing" and v < 0.8:
            return t
        if role == "incoming" and v >= 0.9 and i > 0 and points[i - 1][1] < 0.5:
            return t
    return None


def _find_sneak_level(points: list[tuple[float, float]],
                      overlap_start: float) -> float | None:
    """Find the incoming track's sneak-in volume level.

    Looks for the first volume point near or just before the overlap start.
    Falls back to the minimum value in the first half of points (the
    sneak-in value before it ramps to unity).
    """
    # try exact match first (within 2 beats)
    for t, v in points:
        if abs(t - overlap_start) < 2:
            return round(v, 4)
    # fall back: first point that's clearly below unity
    for t, v in points:
        if v < 0.5:
            return round(v, 4)
    return None


def _detect_two_stage(points: list[tuple[float, float]],
                      overlap_start: float,
                      overlap_end: float,
                      param: str) -> dict | None:
    """Detect if the automation uses a two-stage pattern (partial cut then full)."""
    # For bass: look for an intermediate value between unity and kill
    # For volume: look for an instant partial drop
    relevant = [(t, v) for t, v in points
                if overlap_start - 2 <= t <= overlap_end + 2]

    if param == "bass":
        # Two-stage bass: values go 1.0 -> ~0.5 -> 0.18
        stages = []
        prev_v = None
        for t, v in relevant:
            if prev_v is not None:
                if prev_v >= 0.9 and 0.2 < v < 0.9:
                    stages.append({"beat": t, "value": v, "stage": "partial_cut"})
                elif 0.2 < prev_v < 0.9 and v <= 0.2:
                    stages.append({"beat": t, "value": v, "stage": "full_kill"})
            prev_v = v
        if len(stages) == 2:
            return {"type": "two_stage_bass", "stages": stages}

    elif param == "volume":
        # Two-stage volume: instant drop (same beat, two values) then gradual fade
        for i in range(len(relevant) - 1):
            t1, v1 = relevant[i]
            t2, v2 = relevant[i + 1]
            if abs(t1 - t2) < 0.01 and v1 >= 0.9 and 0.2 < v2 < 0.9:
                return {
                    "type": "two_stage_volume",
                    "instant_drop_beat": t1,
                    "instant_drop_value": v2,
                }

    return None


def _points_equal(a: list[tuple[float, float]],
                  b: list[tuple[float, float]],
                  tolerance: float = 0.01) -> bool:
    """Compare two point lists with tolerance."""
    if len(a) != len(b):
        return False
    for (t1, v1), (t2, v2) in zip(a, b):
        if abs(t1 - t2) > tolerance or abs(v1 - v2) > tolerance:
            return False
    return True


def _split_points_at(points: list[tuple[float, float]],
                     split_beat: float) -> tuple[list, list]:
    """Split points into before and after a beat."""
    before = [(t, v) for t, v in points if t < split_beat]
    after = [(t, v) for t, v in points if t >= split_beat]
    return before, after


def _scope_points(points: list[tuple[float, float]],
                  zone_start: float, zone_end: float,
                  margin: float = 40.0) -> list[tuple[float, float]]:
    """Return only automation points within a transition zone (with margin).

    Margin is generous (40 beats = 10 bars) to catch arrangement-shifted
    points that moved slightly outside the expected zone.
    """
    return [(t, v) for t, v in points
            if zone_start - margin <= t <= zone_end + margin]


def analyse_transitions(claude_auto: dict[str, TrackAutomation],
                        sam_auto: dict[str, TrackAutomation],
                        tracks: list[TrackInfo],
                        ) -> list[TransitionDiff]:
    """Compare automation between Claude and Sam for each transition.

    Only points within each transition's overlap zone (+ small margin) are
    compared, so changes in an adjacent transition don't bleed through.
    """
    diffs: list[TransitionDiff] = []

    for i in range(len(tracks) - 1):
        out_t, in_t = tracks[i], tracks[i + 1]
        ov_start = in_t.arr_start
        ov_end = out_t.arr_end
        if ov_start >= ov_end:
            continue

        td = TransitionDiff(
            pair_index=i + 1,
            out_name=out_t.name,
            in_name=in_t.name,
            overlap_start=ov_start,
            overlap_end=ov_end,
            overlap_bars=(ov_end - ov_start) / 4,
        )

        # match track names to automation data
        c_out = _find_auto(claude_auto, out_t.name)
        c_in = _find_auto(claude_auto, in_t.name)
        s_out = _find_auto(sam_auto, out_t.name)
        s_in = _find_auto(sam_auto, in_t.name)

        if not c_out or not s_out or not c_in or not s_in:
            td.notes = "Could not match all tracks to automation data"
            diffs.append(td)
            continue

        # scope to overlap zone before comparing — prevents bleed from
        # adjacent transitions that share a track
        td.out_volume = _make_diff(
            "volume",
            _scope_points(c_out.volume_points, ov_start, ov_end),
            _scope_points(s_out.volume_points, ov_start, ov_end))
        td.out_bass = _make_diff(
            "bass",
            _scope_points(c_out.bass_points, ov_start, ov_end),
            _scope_points(s_out.bass_points, ov_start, ov_end))
        td.in_volume = _make_diff(
            "volume",
            _scope_points(c_in.volume_points, ov_start, ov_end),
            _scope_points(s_in.volume_points, ov_start, ov_end))
        td.in_bass = _make_diff(
            "bass",
            _scope_points(c_in.bass_points, ov_start, ov_end),
            _scope_points(s_in.bass_points, ov_start, ov_end))

        # find bass swap beats
        td.bass_swap_claude = _find_bass_swap_beat(
            c_out.bass_points, ov_start, ov_end, "outgoing")
        td.bass_swap_sam = _find_bass_swap_beat(
            s_out.bass_points, ov_start, ov_end, "outgoing")

        # classify corrections
        any_change = False

        if td.out_bass and td.out_bass.changed:
            any_change = True
            if td.bass_swap_claude and td.bass_swap_sam:
                delta = td.bass_swap_sam - td.bass_swap_claude
                if abs(delta) > 2:
                    td.corrections.append(
                        f"bass_swap_moved:{delta:+.0f}beats ({delta/4:+.0f}bars)")

            # check for two-stage bass
            two_stage = _detect_two_stage(
                s_out.bass_points, ov_start, ov_end, "bass")
            if two_stage:
                td.corrections.append("two_stage_bass")

        if td.in_bass and td.in_bass.changed:
            any_change = True

        if td.out_volume and td.out_volume.changed:
            any_change = True
            two_stage = _detect_two_stage(
                s_out.volume_points, ov_start, ov_end, "volume")
            if two_stage:
                td.corrections.append("two_stage_volume")

        if td.in_volume and td.in_volume.changed:
            any_change = True
            # check for sneak level change
            c_sneak = _find_sneak_level(c_in.volume_points, ov_start)
            s_sneak = _find_sneak_level(s_in.volume_points, ov_start)
            if c_sneak and s_sneak and abs(c_sneak - s_sneak) > 0.01:
                td.corrections.append(
                    f"sneak_changed:{c_sneak}->{s_sneak}")

        if any_change:
            td.verdict = "corrected"
        else:
            td.verdict = "correct"

        td.classified_style = _classify_style(
            td.out_volume, td.out_bass, td.in_volume,
            td.overlap_start, td.overlap_end)

        diffs.append(td)

    return diffs


def _find_auto(autos: dict[str, TrackAutomation],
               track_name: str) -> TrackAutomation | None:
    """Find a track's automation by fuzzy name match."""
    # exact
    if track_name in autos:
        return autos[track_name]
    # normalised
    for k, v in autos.items():
        if _match_name(k, track_name):
            return v
    return None


def _make_diff(param: str,
               claude: list[tuple[float, float]],
               sam: list[tuple[float, float]]) -> ParamDiff:
    return ParamDiff(
        param=param,
        claude_points=claude,
        sam_points=sam,
        changed=not _points_equal(claude, sam),
    )


# ── pair_history.jsonl writing ───────────────────────────────────────────────

def _section_names(track: TrackInfo) -> list[str]:
    return [s["name"] for s in track.sections]


def diff_to_jsonl_entry(td: TransitionDiff,
                        tracks: list[TrackInfo],
                        project: str,
                        bpm: float) -> dict:
    """Convert a TransitionDiff to a pair_history.jsonl entry."""
    out_t = next((t for t in tracks if _match_name(t.name, td.out_name)), None)
    in_t = next((t for t in tracks if _match_name(t.name, td.in_name)), None)

    entry = {
        "pair_index": td.pair_index,
        "project": project,
        "out_track": td.out_name,
        "in_track": td.in_name,
        "bpm_out": bpm,
        "bpm_in": bpm,
        "overlap_bars": td.overlap_bars,
        "out_structure": _section_names(out_t) if out_t else [],
        "in_structure": _section_names(in_t) if in_t else [],
        "claude_bass_swap_beat": td.bass_swap_claude,
        "sam_bass_swap_beat": td.bass_swap_sam,
        "swap_at_boundary": False,
        "bass_changed": (td.out_bass.changed if td.out_bass else False)
                        or (td.in_bass.changed if td.in_bass else False),
        "volume_changed": (td.out_volume.changed if td.out_volume else False)
                          or (td.in_volume.changed if td.in_volume else False),
        "corrections": td.corrections,
        "classified_style": td.classified_style,
        "source": "auto_diff",
        "timestamp": str(date.today()),
        "verdict": td.verdict,
    }

    # detect boundary swap
    if td.bass_swap_claude is not None:
        if abs(td.bass_swap_claude - td.overlap_end) < 2:
            entry["swap_at_boundary"] = True

    # add sneak info if changed
    if td.in_volume and td.in_volume.changed:
        c_sneak = _find_sneak_level(td.in_volume.claude_points, td.overlap_start)
        s_sneak = _find_sneak_level(td.in_volume.sam_points, td.overlap_start)
        if c_sneak is not None:
            entry["sneak_claude"] = c_sneak
        if s_sneak is not None:
            entry["sneak_sam"] = s_sneak

    # add notes
    if td.notes:
        entry["notes"] = td.notes
    elif td.corrections:
        entry["notes"] = "; ".join(td.corrections)

    return entry


# ── Report printing ──────────────────────────────────────────────────────────

def print_report(diffs: list[TransitionDiff]) -> None:
    correct = sum(1 for d in diffs if d.verdict == "correct")
    corrected = sum(1 for d in diffs if d.verdict == "corrected")

    print(f"\n{'='*70}")
    print(f"  LEARNING REPORT — {len(diffs)} transitions")
    print(f"  Correct: {correct}/{len(diffs)}  |  Corrected: {corrected}/{len(diffs)}")
    print(f"{'='*70}")

    for td in diffs:
        mark = "OK" if td.verdict == "correct" else "FIX"
        swap_info = ""
        if td.bass_swap_claude and td.bass_swap_sam:
            if abs(td.bass_swap_claude - td.bass_swap_sam) > 2:
                delta = td.bass_swap_sam - td.bass_swap_claude
                swap_info = f"  swap moved {delta:+.0f} beats ({delta/4:+.0f} bars)"
            else:
                swap_info = f"  swap@{td.bass_swap_claude:.0f}"

        print(f"\n  T{td.pair_index} [{mark}]  "
              f"{_short(td.out_name)} -> {_short(td.in_name)}"
              f"  ({td.overlap_bars:.0f} bars overlap)"
              f"  style={td.classified_style}")

        if swap_info:
            print(f"          {swap_info}")

        # detail each changed param
        for label, diff in [("out_vol", td.out_volume), ("out_bass", td.out_bass),
                            ("in_vol", td.in_volume), ("in_bass", td.in_bass)]:
            if diff and diff.changed:
                print(f"          {label}: {len(diff.claude_points)} -> "
                      f"{len(diff.sam_points)} pts")

        if td.corrections:
            for c in td.corrections:
                print(f"          >> {c}")

    print(f"\n{'='*70}")

    # summary of correction types
    all_corrections: list[str] = []
    for td in diffs:
        all_corrections.extend(td.corrections)
    if all_corrections:
        print(f"\n  Correction types found:")
        seen: set[str] = set()
        for c in all_corrections:
            tag = c.split(":")[0]
            if tag not in seen:
                count = sum(1 for x in all_corrections if x.startswith(tag))
                print(f"    - {tag}: {count}x")
                seen.add(tag)
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Learn from Sam's automation corrections")
    parser.add_argument("claude_als", type=Path,
                        help="Claude's proposed ALS file")
    parser.add_argument("sam_als", type=Path,
                        help="Sam's corrected ALS file")
    parser.add_argument("sections_json", type=Path,
                        help="Sections JSON (for structure data)")
    parser.add_argument("--project", default="",
                        help="Project name (default: derived from path)")
    parser.add_argument("--bpm", type=float, default=129.2,
                        help="Project BPM (default: 129.2)")
    parser.add_argument("--library", type=Path,
                        default=Path("Documentation/Mix Patterns Library"),
                        help="Mix Patterns Library directory")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print report only, don't write to pair_history")
    args = parser.parse_args()

    if not args.project:
        # derive from path
        parts = args.claude_als.parts
        for p in parts:
            if "Project" in p or "V2" in p or "V1" in p:
                args.project = p
                break
        if not args.project:
            args.project = "unknown"

    # ── read inputs ──────────────────────────────────────────────────
    print(f"Claude ALS: {args.claude_als.name}")
    claude_lines = decompress_als(args.claude_als)
    print(f"  {len(claude_lines)} lines")

    print(f"Sam ALS:    {args.sam_als.name}")
    sam_lines = decompress_als(args.sam_als)
    print(f"  {len(sam_lines)} lines")

    print(f"Sections:   {args.sections_json.name}")
    with open(args.sections_json, encoding="utf-8") as f:
        sections_data = json.load(f)
    print(f"  {len(sections_data)} tracks")

    # ── extract automation ───────────────────────────────────────────
    print("\nExtracting Claude automation...")
    claude_als_tracks = find_track_line_ranges(claude_lines)
    claude_auto = extract_track_automation(claude_lines, claude_als_tracks)
    c_count = sum(1 for ta in claude_auto.values()
                  if ta.volume_points or ta.bass_points)
    print(f"  {c_count} tracks with automation")

    print("Extracting Sam automation...")
    sam_als_tracks = find_track_line_ranges(sam_lines)
    sam_auto = extract_track_automation(sam_lines, sam_als_tracks)
    s_count = sum(1 for ta in sam_auto.values()
                  if ta.volume_points or ta.bass_points)
    print(f"  {s_count} tracks with automation")

    # ── build track list from sections ───────────────────────────────
    tracks = ordered_tracks_from_json(sections_data)
    print(f"\n{len(tracks)} tracks in order:")
    for t in tracks:
        print(f"  {_short(t.name):20s}  "
              f"arr {t.arr_start:6.0f}-{t.arr_end:6.0f}")

    # ── analyse transitions ──────────────────────────────────────────
    diffs = analyse_transitions(claude_auto, sam_auto, tracks)

    # ── print report ─────────────────────────────────────────────────
    print_report(diffs)

    # ── write to pair_history.jsonl ──────────────────────────────────
    if not args.dry_run:
        library_path = args.library / "pair_history.jsonl"
        if not library_path.parent.exists():
            library_path.parent.mkdir(parents=True)
            print(f"Created {library_path.parent}")

        entries = [diff_to_jsonl_entry(td, tracks, args.project, args.bpm)
                   for td in diffs]

        with open(library_path, "a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        print(f"Appended {len(entries)} entries to {library_path}")
    else:
        print("[DRY RUN] Would append entries to pair_history.jsonl")

        # print what would be written
        entries = [diff_to_jsonl_entry(td, tracks, args.project, args.bpm)
                   for td in diffs]
        print("\nEntries that would be written:")
        for e in entries:
            print(f"  T{e['pair_index']} [{e['verdict']}] "
                  f"{_short(e['out_track'])} -> {_short(e['in_track'])}"
                  f"  corrections={e.get('corrections', [])}")


if __name__ == "__main__":
    main()
