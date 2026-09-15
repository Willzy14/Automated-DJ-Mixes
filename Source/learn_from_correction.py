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
import html
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from extract_sections_als import parse_sections_als


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
    # ALS attributes contain XML entities (for example ``&amp;``), while
    # JSON hints and user-facing names generally do not. Normalise that
    # boundary before every exact/substring comparison.
    return html.unescape(s).lower().replace("–", "-").replace("—", "-").strip()


def _match_name(a: str, b: str) -> bool:
    na, nb = _normalise(a), _normalise(b)
    # Exact/substring only — no 20-char prefix match (it collided
    # "Your Love" with "Your Love (Instrumental Mix)").
    return na == nb or na in nb or nb in na


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
    clips: list[dict] = field(default_factory=list)


def _section_semantics(section: dict) -> dict:
    """Keep label semantics from JSON without carrying its stale geometry."""
    result = {
        key: html.unescape(value) if isinstance(value, str) else value
        for key, value in section.items()
        if key in {"name", "label", "label_n"}
    }
    if "name" not in result:
        result["name"] = result.get("label", "(unnamed)")
    return result


def _semantics_for_track(sections: dict | None,
                         track_name: str) -> list[dict] | None:
    if not sections:
        return None
    for candidate, records in sections.items():
        if _match_name(candidate, track_name):
            return [_section_semantics(record) for record in records]
    return None


def ordered_tracks_from_clip_records(
        clip_records: dict[str, list[dict]],
        section_semantics: dict | None = None,
        ) -> list[TrackInfo]:
    """Build ordered track spans from parsed ALS clip records.

    ``clip_records`` has the output shape of :func:`parse_sections_als`, so
    arranged clip behaviour is testable without constructing gzip fixtures.
    Phase-1 JSON may supply section names for history records, but its
    arrangement fields are never read here.
    """
    tracks: list[TrackInfo] = []
    for xml_name, clips in clip_records.items():
        name = html.unescape(xml_name)
        if not name or ("Audio" in name and "-" not in name):
            continue
        positioned = [clip for clip in clips
                      if clip.get("arr_time") is not None
                      and clip.get("arr_end") is not None]
        if not positioned:
            continue

        semantics = _semantics_for_track(section_semantics, name)
        if semantics is None:
            semantics = [_section_semantics(clip) for clip in positioned]

        tracks.append(TrackInfo(
            name=name,
            sections=semantics,
            arr_start=min(float(clip["arr_time"]) for clip in positioned),
            arr_end=max(float(clip["arr_end"]) for clip in positioned),
            clips=positioned,
        ))
    tracks.sort(key=lambda t: t.arr_start)
    return tracks


def _find_track(tracks: list[TrackInfo], name: str) -> TrackInfo | None:
    return next((track for track in tracks if _match_name(track.name, name)), None)


def _has_overlapping_pair(tracks: list[TrackInfo]) -> bool:
    for index, outgoing in enumerate(tracks[:-1]):
        incoming = tracks[index + 1]
        if max(outgoing.arr_start, incoming.arr_start) < min(
                outgoing.arr_end, incoming.arr_end):
            return True
    return False


class VacuousLearningError(RuntimeError):
    """Raised when real ALS overlaps somehow yield an empty comparison."""


def guard_against_vacuous_result(
        claude_tracks: list[TrackInfo],
        sam_tracks: list[TrackInfo],
        diffs: list[TransitionDiff],
        ) -> None:
    """Refuse the silent-success shape that lost the 2026-09-02 correction."""
    if (not diffs
            and len(claude_tracks) >= 2
            and len(sam_tracks) >= 2
            and _has_overlapping_pair(claude_tracks)
            and _has_overlapping_pair(sam_tracks)):
        raise VacuousLearningError(
            "both ALS files contain overlapping audio-track spans, but the "
            "comparison produced 0 transitions; refusing to append an empty "
            "learning result")


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
    bass_swap_delta: float | None = None
    out_overlap_end_claude: float | None = None
    out_overlap_end_sam: float | None = None
    sam_overlap_start: float | None = None
    sam_overlap_end: float | None = None
    sam_overlap_bars: float | None = None
    arrangement_changed: bool = False
    corrections: list[str] = field(default_factory=list)
    verdict: str = "correct"
    classified_style: str = "standard"
    notes: str = ""
    geometry: dict | None = None
    # False when the swap sits on a loop clip on either side: the source-anchored
    # bass_swap_delta above is then not comparable (burn list D12).
    bass_swap_reliable: bool = True


def _classify_style(td_out_vol: ParamDiff | None,
                    td_out_bass: ParamDiff | None,
                    td_in_vol: ParamDiff | None,
                    out_overlap_start: float,
                    out_overlap_end: float,
                    in_overlap_start: float,
                    in_overlap_end: float) -> str:
    """Classify which TransitionStyle Sam's corrections most closely match.

    Returns "standard", "long_blend", or "quick_swap".
    """
    sam_in_vol = td_in_vol.sam_points if td_in_vol else []
    sam_out_bass = td_out_bass.sam_points if td_out_bass else []

    sneak = _find_sneak_level(sam_in_vol, in_overlap_start)
    has_sneak = sneak is not None and sneak > 0.01

    bass_kill_val = None
    for t, v in sam_out_bass:
        if out_overlap_start <= t <= out_overlap_end and v < 0.9:
            bass_kill_val = v
            break

    partial_bass = bass_kill_val is not None and bass_kill_val > 0.25

    vol_points_in_zone = [(t, v) for t, v in sam_in_vol
                          if in_overlap_start - 4 <= t <= in_overlap_end + 4]
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


def _source_beat(beat: float, clips: list[dict], fallback_origin: float) -> float:
    """Map an arrangement beat to its clip's source-audio beat.

    ``parse_sections_als`` records Ableton's ``LoopStart`` as
    ``source_start_beats``.  That source coordinate survives an independent
    trim or resize of the arranged clip, unlike its arrangement-local offset.
    Synthetic callers without clip records retain the previous local-origin
    behaviour.

    Boundary tie-break (2026-09-10, MiniMax review of 53d1b7d, Finding 1.1):
    a beat sitting exactly on the splice between two adjacent clips used to
    pick whichever clip was ARRANGED earliest, on the assumption "their
    source positions are normally identical there" - true for adjacent
    SECTION clips (a drop flowing into its own outro), but FALSE for an
    INSERTED loop clip: apply_loops.clone_clip sets an inserted loop's
    LoopStart to wherever the loop was deliberately sourced from
    (propose_arrangement's tail/intro loop specs), which can differ from
    the adjacent section's own source position by the loop's own length.
    Real automation points DO land exactly on such splices (the swap point
    is frequently the outgoing clip's own end). Fixed by preferring STRICT
    half-open containment [arr_time, arr_end) - the ordinary playback
    sense in which exactly one clip owns any given beat, no tie exists -
    and falling back to the fuzzy 0.01-tolerance match (preferring the
    LATER-arranged clip, matching Ableton's own end-exclusive/
    start-inclusive convention) only for a genuine floating-point-exact
    boundary beat, where strict containment matches none or both."""
    strict = [
        clip for clip in clips
        if (clip.get("arr_time") is not None
            and clip.get("arr_end") is not None
            and clip.get("source_start_beats") is not None
            and float(clip["arr_time"]) <= beat < float(clip["arr_end"]))
    ]
    if len(strict) == 1:
        clip = strict[0]
        return float(clip["source_start_beats"]) + (beat - float(clip["arr_time"]))

    fuzzy = [
        clip for clip in clips
        if (clip.get("arr_time") is not None
            and clip.get("arr_end") is not None
            and clip.get("source_start_beats") is not None
            and float(clip["arr_time"]) - 0.01 <= beat
            <= float(clip["arr_end"]) + 0.01)
    ]
    if not fuzzy:
        return beat - fallback_origin

    # Genuine boundary (strict containment found none or, for overlapping/
    # malformed clip data, more than one) - prefer the LATER-arranged clip.
    clip = max(fuzzy, key=lambda item: float(item["arr_time"]))
    return float(clip["source_start_beats"]) + (beat - float(clip["arr_time"]))


def _scope_points(points: list[tuple[float, float]],
                  zone_start: float, zone_end: float,
                  clips: list[dict], fallback_origin: float,
                  margin: float = 40.0) -> list[tuple[float, float]]:
    """Return transition points in their source-audio coordinate frame.

    Points are selected in the ALS's absolute arrangement coordinate system,
    then translated through their containing clip's ``LoopStart``.  A whole
    downstream arrangement shift or an independent clip trim therefore
    cannot masquerade as an automation edit.
    """
    return [(_source_beat(t, clips, fallback_origin), v) for t, v in points
            if zone_start - margin <= t <= zone_end + margin]


# ── Geometry corrections ─────────────────────────────────────────────────────
#
# The automation diff below compares each track's envelopes in its own
# SOURCE-audio frame. That is the right frame for a resized section clip and
# the wrong one for a LOOP clip: a tail loop cut from a track's intro maps the
# swap to source beat 0, outside the overlap's source window, and
# _find_bass_swap_beat then lands on whatever point comes next (15.09.26
# August Releases Mix T6 read "-64 beats" for a swap that had not moved).
# Sam's hand edits on that mix were mostly geometry the automation diff has
# no name for: where the incoming enters, how much of its intro plays, whether
# the outgoing loops its last bars or runs out, where the swap sits on the
# INCOMING. This layer reads the clip geometry of both ALS files directly and
# labels those. It never changes the automation diff's own fields; it only
# gates the bass_swap_moved label when the swap sits on a loop clip, where
# that label cannot be trusted. Burn list D12.

GEOMETRY_MIN_MOVE_BARS = 1.0
GEOMETRY_MIN_OVERLAP_DELTA_BARS = 4.0
GEOMETRY_MIN_CUT_BARS = 2.0
GEOMETRY_SWAP_MARGIN_BEATS = 40.0   # same reach as _scope_points


@dataclass
class RepeatGroup:
    """Consecutive clips that replay material the track has already played."""
    arr_start: float
    arr_end: float
    source_start: float
    source_end: float
    reps: int
    partial_beats: float = 0.0

    @property
    def chunk_beats(self) -> float:
        return self.source_end - self.source_start

    def label(self) -> str:
        # apply_loops / ARRANGEMENT_REPORT notation: 8bx7+0b
        return (f"{_bars(self.chunk_beats)}bx{self.reps}"
                f"+{_bars(self.partial_beats)}b")


def _bars(beats: float) -> str:
    # + 0.0 turns a clip's "-0" LoopStart into 0, not "-0"
    return f"{round(beats / 4.0, 1) + 0.0:g}"


def _src(clip: dict) -> tuple[float, float]:
    return float(clip["source_start_beats"]), float(clip["source_end_beats"])


def _positioned(clips: list[dict]) -> list[dict]:
    """Clips with real geometry, in arrangement order. Zero-length editing
    leftovers (Ableton keeps them in the XML) are dropped."""
    out = [c for c in clips
           if c.get("arr_time") is not None and c.get("arr_end") is not None
           and c.get("source_start_beats") is not None
           and c.get("source_end_beats") is not None
           and float(c["arr_end"]) - float(c["arr_time"]) > 0.01]
    return sorted(out, key=lambda c: (float(c["arr_time"]), _src(c)[0]))


def _repeat_groups(clips: list[dict]) -> list[RepeatGroup]:
    """Find loops, whether apply_loops built them or Sam made them by hand.

    A group starts at a clip that is arrangement-contiguous with the clip
    before it, goes BACKWARDS in source (its source start is earlier than the
    previous clip's source end), is no longer than that clip, and replays
    material the track has already played. Identical clips that follow are
    further repeats; a shorter clip starting on the chunk's own start is a
    trailing partial. A preceding identical clip that played the chunk in its
    natural place (apply_loops puts the first copy where the source was
    anyway) is counted into the group, so the notation matches
    ARRANGEMENT_REPORT's "8bx7+0b". The real section after the copies (an
    outro after tail loops, an intro after intro loops) is longer than the
    chunk or starts outside it, and is left alone.
    """
    clips = _positioned(clips)
    groups: list[RepeatGroup] = []
    i = 1
    while i < len(clips):
        prev, cur = clips[i - 1], clips[i]
        p0, p1 = _src(prev)
        c0, c1 = _src(cur)
        played_lo = min(_src(c)[0] for c in clips[:i])
        played_hi = max(_src(c)[1] for c in clips[:i])
        contiguous = abs(float(cur["arr_time"]) - float(prev["arr_end"])) < 0.01
        backwards = c0 < p1 - 0.01
        no_longer = (c1 - c0) <= (p1 - p0) + 0.01
        replays = c0 >= played_lo - 0.01 and c1 <= played_hi + 0.01
        if not (contiguous and backwards and no_longer and replays):
            i += 1
            continue
        start = i - 1 if abs(p0 - c0) < 0.01 and abs(p1 - c1) < 0.01 else i
        reps = i - start + 1
        partial = 0.0
        j = i + 1
        while j < len(clips):
            nxt = clips[j]
            n0, n1 = _src(nxt)
            if abs(float(nxt["arr_time"]) - float(clips[j - 1]["arr_end"])) >= 0.01:
                break
            if abs(n0 - c0) < 0.01 and abs(n1 - c1) < 0.01:
                reps += 1
                j += 1
                continue
            if abs(n0 - c0) < 0.01 and n1 < c1 - 0.01:
                partial = n1 - n0
                j += 1
            break
        groups.append(RepeatGroup(
            arr_start=float(clips[start]["arr_time"]),
            arr_end=float(clips[j - 1]["arr_end"]),
            source_start=c0, source_end=c1, reps=reps, partial_beats=partial))
        i = j
    return groups


def _in_group(clip: dict, groups: list[RepeatGroup]) -> bool:
    t0, t1 = float(clip["arr_time"]), float(clip["arr_end"])
    return any(g.arr_start - 0.01 <= t0 and t1 <= g.arr_end + 0.01 for g in groups)


def _containing_clip(clips: list[dict], beat: float) -> dict | None:
    strict = [c for c in clips
              if float(c["arr_time"]) <= beat < float(c["arr_end"])]
    if len(strict) == 1:
        return strict[0]
    fuzzy = [c for c in clips
             if float(c["arr_time"]) - 0.01 <= beat <= float(c["arr_end"]) + 0.01]
    if not fuzzy:
        return None
    return max(fuzzy, key=lambda c: float(c["arr_time"]))


def _source_bar_at(clips: list[dict], groups: list[RepeatGroup],
                   beat: float | None) -> tuple[float | None, bool]:
    """(source bar at an arrangement beat, whether that beat sits on a loop).

    On a loop clip the source position is not comparable across files, so the
    bar is None and the flag says why."""
    if beat is None:
        return None, False
    clip = _containing_clip(clips, beat)
    if clip is None:
        return None, False
    if _in_group(clip, groups):
        return None, True
    return round((_src(clip)[0] + beat - float(clip["arr_time"])) / 4.0, 1) + 0.0, False


def _find_swap_arr(out_bass: list[tuple[float, float]],
                   in_bass: list[tuple[float, float]],
                   ov_start: float, ov_end: float) -> float | None:
    """The bass-swap ARRANGEMENT beat: the outgoing's first kill in the
    overlap, else the incoming's first rise. Arrangement beats, unlike source
    beats, stay meaningful on a loop clip."""
    lo = ov_start - GEOMETRY_SWAP_MARGIN_BEATS
    hi = ov_end + GEOMETRY_SWAP_MARGIN_BEATS
    for t, v in out_bass:
        if lo <= t <= hi and v < 0.8:
            return t
    prev = None
    for t, v in in_bass:
        if lo <= t <= hi and prev is not None and prev < 0.5 and v >= 0.9:
            return t
        prev = v
    return None


def _natural_intervals(clips: list[dict], groups: list[RepeatGroup],
                       floor: float) -> list[tuple[float, float]]:
    """Source intervals played by non-loop clips, cut at `floor`, merged."""
    ivs = []
    for c in clips:
        if _in_group(c, groups):
            continue
        s0, s1 = _src(c)
        s0 = max(s0, floor)
        if s1 > s0:
            ivs.append((s0, s1))
    ivs.sort()
    merged: list[tuple[float, float]] = []
    for s0, s1 in ivs:
        if merged and s0 <= merged[-1][1] + 0.01:
            merged[-1] = (merged[-1][0], max(merged[-1][1], s1))
        else:
            merged.append((s0, s1))
    return merged


def _beats_missing(a: list[tuple[float, float]],
                   b: list[tuple[float, float]]) -> float:
    """Total length of `a` not covered by `b` (both merged interval lists)."""
    missing = 0.0
    for a0, a1 in a:
        cursor = a0
        for b0, b1 in b:
            if b1 <= cursor or b0 >= a1:
                continue
            if b0 > cursor:
                missing += b0 - cursor
            cursor = max(cursor, b1)
            if cursor >= a1:
                break
        if cursor < a1:
            missing += a1 - cursor
    return missing


def _geometry_side(out_t: "TrackInfo", in_t: "TrackInfo",
                   out_bass: list[tuple[float, float]],
                   in_bass: list[tuple[float, float]],
                   ov_start: float, ov_end: float) -> dict:
    out_clips = _positioned(out_t.clips)
    in_clips = _positioned(in_t.clips)
    out_groups = _repeat_groups(out_clips)
    in_groups = _repeat_groups(in_clips)
    entry_arr = (min(float(c["arr_time"]) for c in in_clips)
                 if in_clips else in_t.arr_start)
    entry_out_bar, entry_on_loop = _source_bar_at(out_clips, out_groups, entry_arr)
    first_natural_in = next((c for c in in_clips if not _in_group(c, in_groups)), None)
    intro_trim = (round(_src(first_natural_in)[0] / 4.0, 1) + 0.0
                  if first_natural_in is not None else None)
    swap_arr = _find_swap_arr(out_bass, in_bass, ov_start, ov_end)
    swap_in_bar, _ = _source_bar_at(in_clips, in_groups, swap_arr)
    swap_out_bar, swap_out_on_loop = _source_bar_at(out_clips, out_groups, swap_arr)
    return {
        "out_clips": out_clips, "out_groups": out_groups,
        "entry_arr": entry_arr, "entry_out_bar": entry_out_bar,
        "entry_on_loop": entry_on_loop, "intro_trim": intro_trim,
        "swap_arr": swap_arr, "swap_in_bar": swap_in_bar,
        "swap_out_bar": swap_out_bar, "swap_out_on_loop": swap_out_on_loop,
        "tail_after_swap": (round((out_t.arr_end - swap_arr) / 4.0, 1)
                            if swap_arr is not None else None),
        "tail_loops": [g.label() for g in out_groups
                       if g.arr_end > entry_arr - 0.01],
        "intro_loops": [g.label() for g in in_groups
                        if swap_arr is None or g.arr_start < swap_arr - 0.01],
    }


def _fmt_move(delta: float) -> str:
    return f"{round(delta, 1):+g}"


def _geometry_diff(c_out_t: "TrackInfo", c_in_t: "TrackInfo",
                   s_out_t: "TrackInfo", s_in_t: "TrackInfo",
                   c_out_bass, c_in_bass, s_out_bass, s_in_bass,
                   c_ov: tuple[float, float], s_ov: tuple[float, float],
                   overlap_bars: float, sam_overlap_bars: float | None,
                   ) -> tuple[dict, list[str]]:
    """Compare transition geometry between the two ALS files.

    Returns the pair_history `geometry` record and the correction labels."""
    c = _geometry_side(c_out_t, c_in_t, c_out_bass, c_in_bass, *c_ov)
    s = _geometry_side(s_out_t, s_in_t, s_out_bass, s_in_bass, *s_ov)
    labels: list[str] = []

    if (c["entry_out_bar"] is not None and s["entry_out_bar"] is not None
            and abs(s["entry_out_bar"] - c["entry_out_bar"]) >= GEOMETRY_MIN_MOVE_BARS):
        labels.append(
            f"entry_moved_out:{_fmt_move(s['entry_out_bar'] - c['entry_out_bar'])}bars")

    if (c["intro_trim"] is not None and s["intro_trim"] is not None
            and abs(s["intro_trim"] - c["intro_trim"]) >= GEOMETRY_MIN_MOVE_BARS):
        labels.append(f"intro_trim:{c['intro_trim']:g}->{s['intro_trim']:g}bars")

    if c["swap_arr"] is not None and s["swap_arr"] is None:
        labels.append("swap_removed")
    elif c["swap_arr"] is None and s["swap_arr"] is not None:
        labels.append("swap_added")
    else:
        if (c["swap_in_bar"] is not None and s["swap_in_bar"] is not None
                and abs(s["swap_in_bar"] - c["swap_in_bar"]) >= GEOMETRY_MIN_MOVE_BARS):
            labels.append(
                f"swap_moved_in:{_fmt_move(s['swap_in_bar'] - c['swap_in_bar'])}bars")
        if (c["swap_out_bar"] is not None and s["swap_out_bar"] is not None
                and abs(s["swap_out_bar"] - c["swap_out_bar"]) >= GEOMETRY_MIN_MOVE_BARS):
            labels.append(
                f"swap_moved_out:{_fmt_move(s['swap_out_bar'] - c['swap_out_bar'])}bars")

    for kind in ("tail_loops", "intro_loops"):
        c_l, s_l = ",".join(c[kind]), ",".join(s[kind])
        tag = kind[:-1]  # tail_loop / intro_loop
        if c_l and not s_l:
            labels.append(f"{tag}_removed:{c_l}")
        elif s_l and not c_l:
            labels.append(f"{tag}_added:{s_l}")
        elif c_l != s_l:
            labels.append(f"{tag}_changed:{c_l}->{s_l}")

    # Outgoing material the pipeline played after the (later of the two)
    # entry points that Sam's version skips or trims away.
    cut_bars = None
    if c["entry_out_bar"] is not None and s["entry_out_bar"] is not None:
        floor = max(c["entry_out_bar"], s["entry_out_bar"]) * 4.0
        cut_bars = round(_beats_missing(
            _natural_intervals(c["out_clips"], c["out_groups"], floor),
            _natural_intervals(s["out_clips"], s["out_groups"], floor)) / 4.0, 1)
        if cut_bars >= GEOMETRY_MIN_CUT_BARS:
            labels.append(f"outro_cut:{cut_bars:g}bars")

    if (sam_overlap_bars is not None
            and abs(sam_overlap_bars - overlap_bars) >= GEOMETRY_MIN_OVERLAP_DELTA_BARS):
        labels.append(
            f"overlap_changed:{round(overlap_bars, 1):g}->{round(sam_overlap_bars, 1):g}bars")

    record = {
        "entry_out_bar": [c["entry_out_bar"], s["entry_out_bar"]],
        "entry_on_loop": [c["entry_on_loop"], s["entry_on_loop"]],
        "intro_trim_bars": [c["intro_trim"], s["intro_trim"]],
        "swap_arr_beat": [c["swap_arr"], s["swap_arr"]],
        "swap_in_bar": [c["swap_in_bar"], s["swap_in_bar"]],
        "swap_out_bar": [c["swap_out_bar"], s["swap_out_bar"]],
        "swap_out_on_loop": [c["swap_out_on_loop"], s["swap_out_on_loop"]],
        "tail_after_swap_bars": [c["tail_after_swap"], s["tail_after_swap"]],
        "out_tail_loops": [c["tail_loops"], s["tail_loops"]],
        "in_intro_loops": [c["intro_loops"], s["intro_loops"]],
        "outro_cut_bars": cut_bars,
        "overlap_bars": [round(overlap_bars, 1),
                         None if sam_overlap_bars is None else round(sam_overlap_bars, 1)],
    }
    return record, labels


def analyse_transitions(claude_auto: dict[str, TrackAutomation],
                        sam_auto: dict[str, TrackAutomation],
                        claude_tracks: list[TrackInfo],
                        sam_tracks: list[TrackInfo],
                        ) -> list[TransitionDiff]:
    """Compare automation between Claude and Sam for each transition.

    Each automation stream is scoped with the overlap geometry from its own
    ALS. Sam's edit can move clips substantially; those different positions
    are correction data, not parse noise.
    """
    diffs: list[TransitionDiff] = []

    for i in range(len(claude_tracks) - 1):
        c_out_t, c_in_t = claude_tracks[i], claude_tracks[i + 1]
        s_out_t = _find_track(sam_tracks, c_out_t.name)
        s_in_t = _find_track(sam_tracks, c_in_t.name)
        if s_out_t is None or s_in_t is None:
            missing = []
            if s_out_t is None:
                missing.append("outgoing")
            if s_in_t is None:
                missing.append("incoming")
            c_ov_start, c_ov_end = c_in_t.arr_start, c_out_t.arr_end
            diffs.append(TransitionDiff(
                pair_index=i + 1,
                out_name=c_out_t.name,
                in_name=c_in_t.name,
                overlap_start=c_ov_start,
                overlap_end=c_ov_end,
                overlap_bars=max(0.0, (c_ov_end - c_ov_start) / 4),
                verdict="unanalysed",
                notes=("Unanalysed: no matching "
                       f"{' and '.join(missing)} track in Sam ALS"),
            ))
            continue

        c_ov_start = c_in_t.arr_start
        c_ov_end = c_out_t.arr_end
        s_ov_start = s_in_t.arr_start
        s_ov_end = s_out_t.arr_end
        c_overlaps = c_ov_start < c_ov_end
        s_overlaps = s_ov_start < s_ov_end
        if not c_overlaps and not s_overlaps:
            continue

        # Pair-history overlap fields retain the proposal-side meaning. If
        # Sam created an overlap where the proposal had none, use Sam's real
        # window so that newly created transition is still learnable.
        ov_start = c_ov_start if c_overlaps else s_ov_start
        ov_end = c_ov_end if c_overlaps else s_ov_end

        td = TransitionDiff(
            pair_index=i + 1,
            out_name=c_out_t.name,
            in_name=c_in_t.name,
            overlap_start=ov_start,
            overlap_end=ov_end,
            overlap_bars=(ov_end - ov_start) / 4,
            sam_overlap_start=s_ov_start,
            sam_overlap_end=s_ov_end,
            sam_overlap_bars=(s_ov_end - s_ov_start) / 4,
            # Overlap LENGTH only (2026-09-07, follow-up to the source-beat
            # anchoring fix above). This used to ALSO compare
            # (in.arr_start - out.arr_start) on each side - an "internal
            # offset" meant to catch the whole overlap window shifting
            # position even when its length didn't change. But out.arr_start
            # is set by the UPSTREAM transition (whatever precedes this
            # track), not by THIS transition - an untouched T6 downstream of
            # a genuinely-edited T5 has its own out.arr_start move for a
            # reason that has nothing to do with T6, reproducing exactly the
            # resize-blindness the source-beat fix above just closed for
            # automation points (verified on the real corpus: How Good's
            # start/end moved by different amounts from T5's edit alone,
            # T6 itself untouched - this condition read arrangement_changed
            # =True regardless). The signal it was reaching for - "did the
            # swap POINT itself move" - is what bass_swap_delta (now
            # source-anchored, correctly precise) and the automation-point
            # comparison below already catch: a real handoff shift moves the
            # bass-kill/volume points too, which still flips any_change via
            # out_bass.changed/out_volume.changed. Overlap length alone,
            # which IS set by this transition's own two boundaries
            # (out.arr_end and in.arr_start), is the geometry-only signal
            # this condition can measure without borrowing an upstream-
            # contaminated reference.
            arrangement_changed=(
                abs((c_ov_end - c_ov_start) - (s_ov_end - s_ov_start)) > 0.01
            ),
        )

        # match track names to automation data
        c_out = _find_auto(claude_auto, c_out_t.name)
        c_in = _find_auto(claude_auto, c_in_t.name)
        s_out = _find_auto(sam_auto, s_out_t.name)
        s_in = _find_auto(sam_auto, s_in_t.name)

        td.geometry, geometry_labels = _geometry_diff(
            c_out_t, c_in_t, s_out_t, s_in_t,
            getattr(c_out, "bass_points", []), getattr(c_in, "bass_points", []),
            getattr(s_out, "bass_points", []), getattr(s_in, "bass_points", []),
            (c_ov_start, c_ov_end), (s_ov_start, s_ov_end),
            td.overlap_bars, td.sam_overlap_bars)
        td.bass_swap_reliable = td.geometry["swap_out_on_loop"] == [False, False]

        if not c_out or not s_out or not c_in or not s_in:
            td.notes = "Could not match all tracks to automation data"
            td.corrections.extend(geometry_labels)
            if geometry_labels:
                td.verdict = "corrected"
            diffs.append(td)
            continue

        # Scope each ALS with its own overlap, then map every automation
        # stream to source-audio beats.  The resulting points share an
        # invariant frame before _make_diff compares them.
        td.out_volume = _make_diff(
            "volume",
            _scope_points(c_out.volume_points, c_ov_start, c_ov_end,
                          c_out_t.clips, c_out_t.arr_start),
            _scope_points(s_out.volume_points, s_ov_start, s_ov_end,
                          s_out_t.clips, s_out_t.arr_start))
        td.out_bass = _make_diff(
            "bass",
            _scope_points(c_out.bass_points, c_ov_start, c_ov_end,
                          c_out_t.clips, c_out_t.arr_start),
            _scope_points(s_out.bass_points, s_ov_start, s_ov_end,
                          s_out_t.clips, s_out_t.arr_start))
        td.in_volume = _make_diff(
            "volume",
            _scope_points(c_in.volume_points, c_ov_start, c_ov_end,
                          c_in_t.clips, c_in_t.arr_start),
            _scope_points(s_in.volume_points, s_ov_start, s_ov_end,
                          s_in_t.clips, s_in_t.arr_start))
        td.in_bass = _make_diff(
            "bass",
            _scope_points(c_in.bass_points, c_ov_start, c_ov_end,
                          c_in_t.clips, c_in_t.arr_start),
            _scope_points(s_in.bass_points, s_ov_start, s_ov_end,
                          s_in_t.clips, s_in_t.arr_start))

        # Find swaps in the same source-audio frame as the bass diffs.
        # Absolute arrangement positions cannot be compared across ALS files.
        c_out_ov_start = _source_beat(c_ov_start, c_out_t.clips, c_out_t.arr_start)
        c_out_ov_end = _source_beat(c_ov_end, c_out_t.clips, c_out_t.arr_start)
        s_out_ov_start = _source_beat(s_ov_start, s_out_t.clips, s_out_t.arr_start)
        s_out_ov_end = _source_beat(s_ov_end, s_out_t.clips, s_out_t.arr_start)
        c_in_ov_start = _source_beat(c_ov_start, c_in_t.clips, c_in_t.arr_start)
        c_in_ov_end = _source_beat(c_ov_end, c_in_t.clips, c_in_t.arr_start)
        s_in_ov_start = _source_beat(s_ov_start, s_in_t.clips, s_in_t.arr_start)
        s_in_ov_end = _source_beat(s_ov_end, s_in_t.clips, s_in_t.arr_start)
        td.out_overlap_end_claude = c_out_ov_end
        td.out_overlap_end_sam = s_out_ov_end
        td.bass_swap_claude = _find_bass_swap_beat(
            td.out_bass.claude_points, c_out_ov_start, c_out_ov_end, "outgoing")
        td.bass_swap_sam = _find_bass_swap_beat(
            td.out_bass.sam_points, s_out_ov_start, s_out_ov_end, "outgoing")
        if td.bass_swap_claude is not None and td.bass_swap_sam is not None:
            # Both swaps are already SOURCE-anchored (found on
            # td.out_bass.claude_points/sam_points, which _scope_points
            # mapped through _source_beat) - immune to arrangement shift
            # AND clip resize by construction, on their own. The delta is
            # simply the difference between them.
            #
            # 2026-09-10 (second-lens review of 53d1b7d, Finding 2):
            # subtracting each side's own out_ov_start ("local swap minus
            # local incoming entry", the previous rationale) does NOT
            # remove noise here - it ADDS it, because it assumes the
            # overlap's OWN geometry is stable across files, which it is
            # not for 8 of the real corpus's 9 transitions (Sam routinely
            # changes overlap length/position - that IS the correction).
            # Verified against real ground truth (T9, Once Again -> A
            # Deep-Felt Love): the true swap source position moves
            # 640 -> 576 (-64, confirmed independently from each file's
            # raw clip geometry - Claude's swap sits cleanly at the start
            # of a tail-loop clip, Sam's sits cleanly inside drop_3, no
            # boundary ambiguity on either side). The old formula reported
            # +64 - sign-flipped - because s_out_ov_start and
            # c_out_ov_start (each side's overlap start, independently
            # source-mapped) differ by a large, transition-specific
            # amount that has nothing to do with where the swap itself
            # is. This is exactly the failure class this whole fix exists
            # to close: an append-only pair_history.jsonl entry with a
            # confidently wrong number.
            td.bass_swap_delta = td.bass_swap_sam - td.bass_swap_claude

        # classify corrections
        any_change = td.arrangement_changed or bool(geometry_labels)

        if td.out_bass and td.out_bass.changed:
            any_change = True
            # Only when the swap sits on real section clips on both sides;
            # on a loop clip the source-anchored delta is not comparable and
            # the geometry labels below carry the swap moves instead.
            if td.bass_swap_delta is not None and td.bass_swap_reliable:
                delta = td.bass_swap_delta
                if abs(delta) > 2:
                    td.corrections.append(
                        f"bass_swap_moved:{delta:+.0f}beats ({delta/4:+.0f}bars)")

            # check for two-stage bass
            two_stage = _detect_two_stage(
                td.out_bass.sam_points, s_out_ov_start, s_out_ov_end, "bass")
            if two_stage:
                td.corrections.append("two_stage_bass")

        if td.in_bass and td.in_bass.changed:
            any_change = True

        if td.out_volume and td.out_volume.changed:
            any_change = True
            two_stage = _detect_two_stage(
                td.out_volume.sam_points, s_out_ov_start, s_out_ov_end, "volume")
            if two_stage:
                td.corrections.append("two_stage_volume")

        if td.in_volume and td.in_volume.changed:
            any_change = True
            # check for sneak level change
            c_sneak = _find_sneak_level(td.in_volume.claude_points, c_in_ov_start)
            s_sneak = _find_sneak_level(td.in_volume.sam_points, s_in_ov_start)
            if c_sneak and s_sneak and abs(c_sneak - s_sneak) > 0.01:
                td.corrections.append(
                    f"sneak_changed:{c_sneak}->{s_sneak}")

        td.corrections.extend(geometry_labels)

        if any_change:
            td.verdict = "corrected"
        else:
            td.verdict = "correct"

        td.classified_style = _classify_style(
            td.out_volume, td.out_bass, td.in_volume,
            s_out_ov_start, s_out_ov_end, s_in_ov_start, s_in_ov_end)

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
        "sam_overlap_bars": td.sam_overlap_bars,
        "arrangement_changed": td.arrangement_changed,
        "out_structure": _section_names(out_t) if out_t else [],
        "in_structure": _section_names(in_t) if in_t else [],
        "claude_bass_swap_beat": td.bass_swap_claude,
        "sam_bass_swap_beat": td.bass_swap_sam,
        "bass_swap_delta_beats": td.bass_swap_delta,
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
    if td.geometry is not None:
        entry["geometry"] = td.geometry
        entry["bass_swap_reliable"] = td.bass_swap_reliable

    # detect boundary swap
    if (td.bass_swap_claude is not None
            and td.out_overlap_end_claude is not None):
        if abs(td.bass_swap_claude - td.out_overlap_end_claude) < 2:
            entry["swap_at_boundary"] = True

    # add sneak info if changed
    if td.in_volume and td.in_volume.changed:
        # Incoming points are stored relative to the incoming track start.
        c_sneak = _find_sneak_level(td.in_volume.claude_points, 0.0)
        s_sneak = _find_sneak_level(td.in_volume.sam_points, 0.0)
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
    unanalysed = sum(1 for d in diffs if d.verdict == "unanalysed")

    print(f"\n{'='*70}")
    print(f"  LEARNING REPORT — {len(diffs)} transitions")
    print(f"  Correct: {correct}/{len(diffs)}  |  Corrected: {corrected}/{len(diffs)}"
          f"  |  Unanalysed: {unanalysed}/{len(diffs)}")
    print(f"{'='*70}")

    for td in diffs:
        mark = {"correct": "OK", "corrected": "FIX"}.get(td.verdict, "SKIP")
        swap_info = ""
        if td.bass_swap_delta is not None and not td.bass_swap_reliable:
            swap_info = "  swap sits on a loop clip - source delta not comparable, see geometry"
        elif td.bass_swap_delta is not None:
            if abs(td.bass_swap_delta) > 2:
                delta = td.bass_swap_delta
                swap_info = f"  swap moved {delta:+.0f} beats ({delta/4:+.0f} bars)"
            else:
                swap_info = "  swap aligned within transition"

        overlap_info = f"{td.overlap_bars:.0f} bars overlap"
        if td.sam_overlap_bars is not None:
            # ASCII only - the u2192 arrow crashed this line's own print()
            # on Sam's default Windows cp1252 console before the report
            # even started (2026-09-10, second-lens review of 53d1b7d,
            # Finding 4). Matches the project-wide encoding rule.
            overlap_info = (f"{td.overlap_bars:.0f}->{td.sam_overlap_bars:.0f} "
                            "bars overlap")

        print(f"\n  T{td.pair_index} [{mark}]  "
              f"{_short(td.out_name)} -> {_short(td.in_name)}"
              f"  ({overlap_info})"
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

        if td.geometry:
            g = td.geometry

            def pair(key: str) -> str:
                a, b = g[key]
                fa = "-" if a is None else f"{a:g}"
                fb = "-" if b is None else f"{b:g}"
                return f"{fa}->{fb}"

            loops = "/".join(",".join(x) or "none" for x in g["out_tail_loops"])
            print(f"          geometry: entry out-bar {pair('entry_out_bar')} | "
                  f"intro trim {pair('intro_trim_bars')} | "
                  f"swap in-bar {pair('swap_in_bar')} out-bar {pair('swap_out_bar')} | "
                  f"tail after swap {pair('tail_after_swap_bars')} | "
                  f"tail loops {loops}"
                  + (f" | cut {g['outro_cut_bars']:g}b" if g.get("outro_cut_bars") else ""))

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

def _print_geometry(label: str, tracks: list[TrackInfo]) -> None:
    print(f"\n{label} geometry — {len(tracks)} tracks in order:")
    for track in tracks:
        print(f"  {_short(track.name):20s}  "
              f"arr {track.arr_start:6.0f}-{track.arr_end:6.0f}")


def main() -> int:
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

    # Geometry is parsed from EACH ALS independently. The sections JSON was
    # emitted from the sequential Phase-1 layout and is used only for stable
    # section-label semantics in pair-history records.
    claude_tracks = ordered_tracks_from_clip_records(
        parse_sections_als(args.claude_als), sections_data)
    sam_tracks = ordered_tracks_from_clip_records(
        parse_sections_als(args.sam_als), sections_data)
    _print_geometry("Baseline ALS", claude_tracks)
    _print_geometry("Corrected ALS", sam_tracks)

    # ── analyse transitions ──────────────────────────────────────────
    diffs = analyse_transitions(
        claude_auto, sam_auto, claude_tracks, sam_tracks)
    try:
        guard_against_vacuous_result(claude_tracks, sam_tracks, diffs)
    except VacuousLearningError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # ── print report ─────────────────────────────────────────────────
    print_report(diffs)

    # ── write to pair_history.jsonl ──────────────────────────────────
    if not args.dry_run:
        library_path = args.library / "pair_history.jsonl"
        if not library_path.parent.exists():
            library_path.parent.mkdir(parents=True)
            print(f"Created {library_path.parent}")

        entries = [diff_to_jsonl_entry(
            td, claude_tracks, args.project, args.bpm)
                   for td in diffs]

        with open(library_path, "a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        print(f"Appended {len(entries)} entries to {library_path}")
    else:
        print("[DRY RUN] Would append entries to pair_history.jsonl")

        # print what would be written
        entries = [diff_to_jsonl_entry(
            td, claude_tracks, args.project, args.bpm)
                   for td in diffs]
        print("\nEntries that would be written:")
        for e in entries:
            print(f"  T{e['pair_index']} [{e['verdict']}] "
                  f"{_short(e['out_track'])} -> {_short(e['in_track'])}"
                  f"  corrections={e.get('corrections', [])}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
