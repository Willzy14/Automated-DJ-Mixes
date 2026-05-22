"""Apply automation (volume crossfades + EQ bass kills) to a Sections .als.

Reads an arranged Sections .als (with loops, NO automation) and the matching
sections JSON.  Identifies overlap zones between consecutive tracks, determines
bass-swap points from section structure, and patches in:

  - Utility Gain automation   (volume crossfade)
  - ChannelEQ LowShelfGain    (bass kill/restore)

Usage:
    python Source/apply_automation.py <sections.als> <sections.json> <output.als>

Example:
    python Source/apply_automation.py ^
        "Test Project/Black Book x Defected V2/Output/Sections V20 Project/Sections V20.als" ^
        "Test Project/Black Book x Defected V2/Sections Review/Sections_V20.json" ^
        "Test Project/Black Book x Defected V2/Output/Sections V21 Project/Sections V21.als"
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class TransitionStyle(Enum):
    STANDARD = "standard"
    LONG_BLEND = "long_blend"
    QUICK_SWAP = "quick_swap"


# ── Constants ─────────────────────────────────────────────────────────────────

EQ_BASS_KILL = 0.18       # ChannelEQ LowShelfGain ~ -15 dB
EQ_BASS_PARTIAL = 0.52    # two-stage partial cut ~ -6 dB  [Rule 2: V21→V22 T5]
EQ_BASS_UNITY = 1.0       # ChannelEQ LowShelfGain 0 dB
VOL_UNITY = 1.0           # full volume on Utility Gain
VOL_SNEAK = 0.2           # incoming sneaks in at 20 %
VOL_SNEAK_LOW = 0.1       # lower sneak for percussive intros  [Rule 4: V21→V22 T8]
VOL_PARTIAL_DROP = 0.56   # two-stage vol instant drop ~ -5 dB [Rule 3: V21→V22 T9]
VOL_ZERO = 0.0            # silent
SENTINEL_TIME = -63072000  # Ableton "before-all-time" default event

# Boundary avoidance: minimum gap between swap and overlap end (beats).
# [Rule 1: V21→V22 T3+T4 — boundary swaps ALWAYS corrected by Sam]
BOUNDARY_MARGIN = 64  # 16 bars — must have this much room after swap


# ── ID allocation ─────────────────────────────────────────────────────────────

_NEXT_ID = 50000


def _alloc_id() -> int:
    global _NEXT_ID
    _NEXT_ID += 1
    return _NEXT_ID


# ── ALS read / write ─────────────────────────────────────────────────────────

def decompress_als(als_path: Path) -> list[str]:
    with gzip.open(als_path, "rb") as f:
        content = f.read().decode("utf-8")
    return content.splitlines(keepends=True)


def compress_als(lines: list[str], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(lines)
    with gzip.open(output_path, "wb") as f:
        f.write(content.encode("utf-8"))
    return output_path


# ── Track range finding ──────────────────────────────────────────────────────

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


# ── Automation-target discovery ───────────────────────────────────────────────

def _find_target(lines: list[str], start: int, end: int,
                 device_tag: str, param_tag: str) -> str | None:
    """Walk a track's lines to find an AutomationTarget Id for a given
    device + parameter combination."""
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


def find_utility_gain_target(lines: list[str], s: int, e: int) -> str | None:
    return _find_target(lines, s, e, "StereoGain", "Gain")


def find_eq_bass_target(lines: list[str], s: int, e: int) -> str | None:
    return _find_target(lines, s, e, "ChannelEq", "LowShelfGain")


# ── Envelope XML building ────────────────────────────────────────────────────

def build_envelope_xml(target_id: str,
                       points: list[tuple[float, float]],
                       base_indent: str) -> list[str]:
    """Return lines for one <AutomationEnvelope> block.

    *points* is [(time_beats, value), ...] — must already be sorted.
    *base_indent* is the tab string for the <AutomationEnvelope> tag itself.
    """
    t = base_indent
    out: list[str] = []
    out.append(f'{t}<AutomationEnvelope Id="{_alloc_id()}">\r\n')
    out.append(f'{t}\t<EnvelopeTarget>\r\n')
    out.append(f'{t}\t\t<PointeeId Value="{target_id}" />\r\n')
    out.append(f'{t}\t</EnvelopeTarget>\r\n')
    out.append(f'{t}\t<Automation>\r\n')
    out.append(f'{t}\t\t<Events>\r\n')

    # sentinel default event
    default_val = points[0][1] if points else 1.0
    out.append(f'{t}\t\t\t<FloatEvent Id="{_alloc_id()}" '
               f'Time="{SENTINEL_TIME}" Value="{default_val}" />\r\n')

    for beat, val in points:
        out.append(f'{t}\t\t\t<FloatEvent Id="{_alloc_id()}" '
                   f'Time="{beat}" Value="{val}" />\r\n')

    out.append(f'{t}\t\t</Events>\r\n')
    out.append(f'{t}\t\t<AutomationTransformViewState>\r\n')
    out.append(f'{t}\t\t\t<IsTransformPending Value="false" />\r\n')
    out.append(f'{t}\t\t\t<TimeAndValueTransforms />\r\n')
    out.append(f'{t}\t\t</AutomationTransformViewState>\r\n')
    out.append(f'{t}\t</Automation>\r\n')
    out.append(f'{t}</AutomationEnvelope>\r\n')
    return out


# ── Envelope insertion ────────────────────────────────────────────────────────

def _find_envelopes_tag(lines: list[str], start: int, end: int
                        ) -> tuple[int, bool, str]:
    """Locate the track-level <Envelopes /> or <Envelopes> inside
    <AutomationEnvelopes>.  Returns (line_index, is_self_closing, indent)."""
    in_auto = False
    for i in range(start, end + 1):
        line = lines[i]
        if "<AutomationEnvelopes>" in line and "<AutomationEnvelopesListWrapper" not in line:
            in_auto = True
        if in_auto:
            stripped = line.lstrip()
            if stripped.startswith("<Envelopes"):
                indent = line[: len(line) - len(line.lstrip())]
                if "<Envelopes />" in line:
                    return i, True, indent
                if "<Envelopes>" in line:
                    # find the matching close
                    for j in range(i + 1, end + 1):
                        if "</Envelopes>" in lines[j]:
                            return j, False, indent
                    return i, False, indent
            if "</AutomationEnvelopes>" in line:
                in_auto = False
    return -1, False, ""


def insert_envelopes(lines: list[str], start: int, end: int,
                     env_blocks: list[list[str]]) -> int:
    """Splice envelope XML into a track. Returns the line-count delta."""
    idx, self_closing, indent = _find_envelopes_tag(lines, start, end)
    if idx < 0:
        return 0

    all_lines: list[str] = []
    for block in env_blocks:
        all_lines.extend(block)

    if self_closing:
        replacement = [f"{indent}<Envelopes>\r\n"] + all_lines + [f"{indent}</Envelopes>\r\n"]
        lines[idx: idx + 1] = replacement
        return len(replacement) - 1
    else:
        # idx points at </Envelopes> — insert just before it
        lines[idx:idx] = all_lines
        return len(all_lines)


# ── Automation lane visibility ───────────────────────────────────────────────
#
# Ableton shows automation lanes below each track when they're defined in the
# <AutomationLanes> block inside <DeviceChain>.  Each lane points to a device
# (SelectedDevice = chain_index + 2, offset accounts for Mixer/routing) and
# a parameter within that device (SelectedEnvelope).
#
# For our standard template chain [StereoGain, ChannelEq]:
#   ChannelEq  LowShelfGain → SelectedDevice=3, SelectedEnvelope=2
#   StereoGain Gain          → SelectedDevice=2, SelectedEnvelope=9
#

# Lane indices (template-dependent: [StereoGain @ chain 0, ChannelEq @ chain 1])
_LANE_EQ_BASS = {"device": 3, "envelope": 2}     # ChannelEq LowShelfGain
_LANE_VOL     = {"device": 2, "envelope": 9}      # StereoGain Gain


def set_automation_lanes(lines: list[str], start: int, end: int) -> int:
    """Replace the AutomationLanes block with two visible lanes
    (Channel EQ + Utility Volume).  Returns line-count delta."""
    # Find the outer <AutomationLanes> inside <DeviceChain>
    # Note: "<AutomationLane" is a substring of "<AutomationLanes" so we
    # must match the exact tag, not use substring exclusion.
    for i in range(start, end + 1):
        stripped = lines[i].lstrip()
        if stripped.startswith("<AutomationLanes>") and \
                "<AutomationLane " not in stripped and \
                "<AutomationLane>" not in stripped:
            indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            # Find matching closing tag
            depth = 0
            close_idx = -1
            for j in range(i, end + 1):
                if "<AutomationLanes>" in lines[j] and \
                        "<AutomationLane " not in lines[j] and \
                        "<AutomationLane>" not in lines[j]:
                    depth += 1
                if "</AutomationLanes>" in lines[j] and \
                        "</AutomationLane>" not in lines[j]:
                    depth -= 1
                    if depth == 0:
                        close_idx = j
                        break
            if close_idx < 0:
                return 0

            t = indent
            replacement = [
                f'{t}<AutomationLanes>\r\n',
                f'{t}\t<AutomationLanes>\r\n',
                f'{t}\t\t<AutomationLane Id="0">\r\n',
                f'{t}\t\t\t<SelectedDevice Value="{_LANE_EQ_BASS["device"]}" />\r\n',
                f'{t}\t\t\t<SelectedEnvelope Value="{_LANE_EQ_BASS["envelope"]}" />\r\n',
                f'{t}\t\t\t<IsContentSelectedInDocument Value="false" />\r\n',
                f'{t}\t\t\t<LaneHeight Value="68" />\r\n',
                f'{t}\t\t</AutomationLane>\r\n',
                f'{t}\t\t<AutomationLane Id="1">\r\n',
                f'{t}\t\t\t<SelectedDevice Value="{_LANE_VOL["device"]}" />\r\n',
                f'{t}\t\t\t<SelectedEnvelope Value="{_LANE_VOL["envelope"]}" />\r\n',
                f'{t}\t\t\t<IsContentSelectedInDocument Value="false" />\r\n',
                f'{t}\t\t\t<LaneHeight Value="68" />\r\n',
                f'{t}\t\t</AutomationLane>\r\n',
                f'{t}\t</AutomationLanes>\r\n',
                f'{t}\t<AreAdditionalAutomationLanesFolded Value="false" />\r\n',
                f'{t}</AutomationLanes>\r\n',
            ]

            old_len = close_idx - i + 1
            lines[i: close_idx + 1] = replacement
            return len(replacement) - old_len

    return 0


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class TrackInfo:
    name: str
    sections: list[dict]
    arr_start: float
    arr_end: float
    als_index: int = -1
    als_start: int = -1
    als_end: int = -1


@dataclass
class TransitionPlan:
    outgoing: TrackInfo
    incoming: TrackInfo
    overlap_start: float
    overlap_end: float
    bass_swap: float
    reason: str = ""
    style: TransitionStyle = TransitionStyle.STANDARD
    # Learned modifiers (populated by plan_transitions)
    two_stage_bass: bool = False       # Rule 2: partial cut before full kill
    two_stage_bass_beat: float = 0.0   # beat where partial cut starts
    two_stage_kill_beat: float = 0.0   # beat where full kill happens
    two_stage_volume: bool = False     # Rule 3: instant partial vol drop at swap
    low_sneak: bool = False            # Rule 4: use VOL_SNEAK_LOW for this incoming


# ── Section helpers ───────────────────────────────────────────────────────────

def ordered_tracks(sections: dict) -> list[TrackInfo]:
    """Build a list of TrackInfo sorted by arrangement start."""
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


def _label(sec: dict) -> str:
    return sec.get("label", "").lower()


# ── Bass-swap detection ──────────────────────────────────────────────────────
#
# Learned rules baked in from V21→V22 diff (Mix Patterns Library):
#
#   Rule 1 (HIGH CONFIDENCE, 2/2):
#     NEVER pick a swap point within BOUNDARY_MARGIN beats of the overlap end.
#     Sam corrected 100% of boundary swaps — they create hard cuts with no
#     fade room.  Fall through to the next priority level instead.
#
#   Rule 5 (HIGH CONFIDENCE, 2/2):
#     Volume fade timing MUST follow the bass swap position.  When swap moves,
#     incoming ramp shortens and outgoing fade extends.  (Enforced in
#     build_track_automation, not here.)
#

def _inside_overlap(t: float, ov_start: float, ov_end: float) -> bool:
    """Is beat *t* inside the overlap zone with boundary margin?"""
    return ov_start <= t <= ov_end - BOUNDARY_MARGIN


def find_bass_swap(out: TrackInfo, inc: TrackInfo,
                   ov_start: float, ov_end: float) -> tuple[float, str]:
    """Pick the bass-swap beat — the dual-cut point.

    Priority:
      1. Outgoing's outro start (the outro IS the fade zone, no margin needed).
      2. Incoming's first build or drop after intro (the energy rise point).
      3. Outgoing's last fill / break / drop boundary (with margin).
      4. Midpoint (snapped to 16 beats).
    """
    # 1 — outgoing outro start. No boundary margin: the outro IS the post-cut
    #     fade zone, by design. Bass kills at outro start, volume fades through.
    for s in out.sections:
        t = s["arr_time"]
        if ov_start <= t <= ov_end and _label(s) == "outro":
            return t, f"outgoing {s['name']} start"

    # 2 — incoming's first build or drop after intro (energy rise point)
    for s in inc.sections:
        t = s["arr_time"]
        if _inside_overlap(t, ov_start, ov_end) and _label(s) in ("build", "drop"):
            return t, f"incoming {s['name']} start"

    # 3 — outgoing's last fill / break / drop boundary in overlap
    best: tuple[float, str] | None = None
    for s in out.sections:
        t = s["arr_time"]
        if _inside_overlap(t, ov_start, ov_end) and \
                _label(s) in ("fill", "break", "drop"):
            best = (t, f"outgoing {s['name']} start")
    if best:
        return best

    # 4 — midpoint
    mid = round((ov_start + ov_end) / 2 / 16) * 16
    return mid, "midpoint (16-beat snap)"


# ── Transition planning ──────────────────────────────────────────────────────
#
# Learned rules applied here:
#
#   Rule 2 (EMERGING, 1/1 — T5 Revoloution→Route 94):
#     Two-stage bass: when outgoing has a long outro AND incoming has a
#     build section (not an immediate drop), use partial cut at outro start
#     then full kill at the incoming's build/drop boundary.
#
#   Rule 3 (EMERGING, 1/1 — T9 Kids→Sapian):
#     Two-stage volume: instant partial drop at the bass swap point, then
#     gradual fade. Applied when the outgoing track has high section count
#     (complex structure = likely high energy at the transition).
#
#   Rule 4 (EMERGING, 1/1 — T8 Professor X→Kids):
#     Lower sneak volume (0.1 vs 0.2) when the incoming track's intro
#     is short and percussive (many clips = looped percussion intro).
#

def _has_build_section(track: TrackInfo, ov_start: float, ov_end: float) -> bool:
    """Does the track have a build/break section in or near the overlap?"""
    for s in track.sections:
        t = s["arr_time"]
        if ov_start <= t <= ov_end and _label(s) in ("build", "break"):
            return True
    return False


def _outro_length(track: TrackInfo) -> float:
    """Length of the outgoing's outro in beats (0 if no outro)."""
    for s in reversed(track.sections):
        if _label(s) == "outro":
            return s["arr_end"] - s["arr_time"]
    return 0


def _intro_clip_count(track: TrackInfo, ov_start: float, ov_end: float) -> int:
    """Count sections in the incoming track's intro region within overlap."""
    count = 0
    for s in track.sections:
        t = s["arr_time"]
        if t > ov_end:
            break
        if ov_start <= t <= ov_end:
            count += 1
    return count


def _find_incoming_build_drop(track: TrackInfo,
                              ov_start: float, ov_end: float) -> float | None:
    """Find the first build→drop or break→drop boundary in the incoming within
    overlap.  Returns the drop start beat, or None."""
    prev_label = ""
    for s in track.sections:
        t = s["arr_time"]
        if t > ov_end + 16:
            break
        if ov_start <= t and _label(s) == "drop" and prev_label in ("build", "break"):
            return t
        prev_label = _label(s)
    return None


def plan_transitions(tracks: list[TrackInfo]) -> list[TransitionPlan]:
    plans: list[TransitionPlan] = []
    for i in range(len(tracks) - 1):
        out_t, in_t = tracks[i], tracks[i + 1]
        ov_start = in_t.arr_start
        ov_end = out_t.arr_end
        if ov_start >= ov_end:
            print(f"  WARNING  no overlap: {_short(out_t.name)} -> {_short(in_t.name)}")
            continue

        swap, reason = find_bass_swap(out_t, in_t, ov_start, ov_end)
        plan = TransitionPlan(out_t, in_t, ov_start, ov_end, swap, reason)

        # ── Rule 2: two-stage bass ───────────────────────────────────
        # Conditions: outgoing outro >= 32 beats AND incoming has a build
        # section.  The partial cut goes at the swap point, full kill at
        # the incoming build→drop boundary (or swap + 48 beats).
        # [V24 fix: full kill beat must ALSO be inside boundary margin,
        #  otherwise two-stage pushes the kill to the overlap edge.
        #  T4 had partial@2368 + full kill@2400 (boundary) — Sam rejected.]
        out_outro_len = _outro_length(out_t)
        if out_outro_len >= 32 and _has_build_section(in_t, ov_start, ov_end):
            build_drop = _find_incoming_build_drop(in_t, ov_start, ov_end)
            kill_beat = build_drop if build_drop else swap + 48
            # Only enable two-stage if the full kill is safely inside the
            # boundary margin — otherwise we'd violate Rule 1.
            if _inside_overlap(kill_beat, ov_start, ov_end):
                plan.two_stage_bass = True
                plan.two_stage_bass_beat = swap
                plan.two_stage_kill_beat = kill_beat
                reason += " + two-stage bass [Rule 2]"
                plan.reason = reason

        # ── Rule 3: two-stage volume ─────────────────────────────────
        # DISABLED — only 1 observation (T9, Kids 18 sections) and
        # threshold 14 caused false positives on Adam Ten (29, T1 correct)
        # and Revoloution (16, T5 not vol-corrected).
        # Re-enable when we have ≥3 observations to calibrate threshold.
        # Original: if len(out_t.sections) >= 14: plan.two_stage_volume = True

        # ── Rule 4: lower sneak for short overlaps ──────────────────
        overlap_len = ov_end - ov_start
        if overlap_len <= 80:
            plan.low_sneak = True

        # ── Style selection ──────────────────────────────────────────
        overlap_bars = overlap_len / 4
        if overlap_bars < 24:
            plan.style = TransitionStyle.QUICK_SWAP
        elif overlap_bars > 36:
            plan.style = TransitionStyle.LONG_BLEND
        else:
            plan.style = TransitionStyle.STANDARD

        plans.append(plan)

    return plans


# ── Per-track automation points ───────────────────────────────────────────────
#
# Automation generation with all learned rules:
#
#   Rule 1: Boundary avoidance  → enforced in find_bass_swap (above)
#   Rule 2: Two-stage bass      → plan.two_stage_bass flag
#   Rule 3: Two-stage volume    → plan.two_stage_volume flag
#   Rule 4: Lower sneak         → plan.low_sneak flag
#   Rule 5: Volume follows swap → structural (fade_start = swap position)
#

def build_track_automation(plans: list[TransitionPlan],
                           tracks: list[TrackInfo],
                           ) -> dict[str, dict[str, list[tuple[float, float]]]]:
    """Return {track_name: {"volume": [...], "eq_bass": [...]}}."""
    auto: dict[str, dict[str, list[tuple[float, float]]]] = {
        t.name: {"volume": [], "eq_bass": []} for t in tracks
    }

    for plan in plans:
        ov_s = plan.overlap_start
        ov_e = plan.overlap_end
        swap = plan.bass_swap
        pre  = swap - 1  # 1-beat ramp for EQ

        # Volume fade starts AT the bass cut (swap), fades through to ov_e.
        # The outro between swap and ov_e is the fade zone.
        fade_start = swap
        if fade_start < ov_s:
            fade_start = ov_s

        if plan.style == TransitionStyle.QUICK_SWAP:
            # Sharp cut — no sneak, instant swap at bass_swap
            auto[plan.outgoing.name]["volume"].extend([
                (ov_s - 1, VOL_UNITY),
                (swap - 1, VOL_UNITY),
                (swap,     VOL_ZERO),
                (ov_e,     VOL_ZERO),
            ])
            auto[plan.outgoing.name]["eq_bass"].extend([
                (ov_s - 1, EQ_BASS_UNITY),
                (pre,      EQ_BASS_UNITY),
                (swap,     EQ_BASS_KILL),
                (ov_e,     EQ_BASS_KILL),
            ])
            auto[plan.incoming.name]["volume"].extend([
                (ov_s,     VOL_ZERO),
                (swap - 1, VOL_ZERO),
                (swap,     VOL_UNITY),
                (ov_e,     VOL_UNITY),
                (ov_e + 1, VOL_UNITY),
            ])
            auto[plan.incoming.name]["eq_bass"].extend([
                (ov_s,     EQ_BASS_KILL),
                (pre,      EQ_BASS_KILL),
                (swap,     EQ_BASS_UNITY),
                (ov_e,     EQ_BASS_UNITY),
                (ov_e + 1, EQ_BASS_UNITY),
            ])

        elif plan.style == TransitionStyle.LONG_BLEND:
            # Extended crossfade — full bass kill at swap (outro start),
            # volume holds until bass cut then fades through the outro.
            sneak = 0.15
            # Outgoing: hold at full until bass cut, then fade to zero
            auto[plan.outgoing.name]["volume"].extend([
                (ov_s - 1, VOL_UNITY),
                (ov_s,     VOL_UNITY),
                (swap,     VOL_UNITY),     # hold at full until bass cut
                (ov_e,     VOL_ZERO),      # fade to zero through outro
            ])
            auto[plan.outgoing.name]["eq_bass"].extend([
                (ov_s - 1, EQ_BASS_UNITY),
                (ov_s,     EQ_BASS_UNITY),
                (pre,      EQ_BASS_UNITY),
                (swap,     EQ_BASS_KILL),  # full bass kill at swap
                (ov_e,     EQ_BASS_KILL),
            ])
            # Incoming: ramp from sneak at ov_s up to full at the bass switch
            auto[plan.incoming.name]["volume"].extend([
                (ov_s,     sneak),
                (swap,     VOL_UNITY),     # full at bass switch
                (ov_e,     VOL_UNITY),
                (ov_e + 1, VOL_UNITY),
            ])
            auto[plan.incoming.name]["eq_bass"].extend([
                (ov_s,     EQ_BASS_KILL),
                (pre,      EQ_BASS_KILL),
                (swap,     EQ_BASS_UNITY), # bass back at swap
                (ov_e,     EQ_BASS_UNITY),
                (ov_e + 1, EQ_BASS_UNITY),
            ])

        else:
            # STANDARD — existing two-phase model with learned rules

            # ── OUTGOING volume ──────────────────────────────────────
            if plan.two_stage_volume:
                auto[plan.outgoing.name]["volume"].extend([
                    (ov_s - 1, VOL_UNITY),
                    (ov_s,     VOL_UNITY),
                    (fade_start, VOL_UNITY),
                    (fade_start, VOL_PARTIAL_DROP),
                    (ov_e,     VOL_ZERO),
                ])
            else:
                auto[plan.outgoing.name]["volume"].extend([
                    (ov_s - 1, VOL_UNITY),
                    (ov_s,     VOL_UNITY),
                    (fade_start, VOL_UNITY),
                    (ov_e,     VOL_ZERO),
                ])

            # ── OUTGOING bass ────────────────────────────────────────
            if plan.two_stage_bass:
                partial_beat = plan.two_stage_bass_beat
                kill_beat = plan.two_stage_kill_beat
                auto[plan.outgoing.name]["eq_bass"].extend([
                    (ov_s - 1, EQ_BASS_UNITY),
                    (ov_s,     EQ_BASS_UNITY),
                    (partial_beat - 1, EQ_BASS_UNITY),
                    (partial_beat,     EQ_BASS_PARTIAL),
                    (kill_beat - 1,    EQ_BASS_PARTIAL),
                    (kill_beat,        EQ_BASS_KILL),
                    (ov_e,             EQ_BASS_KILL),
                ])
            else:
                auto[plan.outgoing.name]["eq_bass"].extend([
                    (ov_s - 1, EQ_BASS_UNITY),
                    (ov_s,     EQ_BASS_UNITY),
                    (pre,      EQ_BASS_UNITY),
                    (swap,     EQ_BASS_KILL),
                    (ov_e,     EQ_BASS_KILL),
                ])

            # ── INCOMING volume ──────────────────────────────────────
            sneak = VOL_SNEAK_LOW if plan.low_sneak else VOL_SNEAK
            auto[plan.incoming.name]["volume"].extend([
                (ov_s,     sneak),
                (swap,     VOL_UNITY),
                (ov_e,     VOL_UNITY),
                (ov_e + 1, VOL_UNITY),
            ])

            # ── INCOMING bass ────────────────────────────────────────
            auto[plan.incoming.name]["eq_bass"].extend([
                (ov_s,     EQ_BASS_KILL),
                (pre,      EQ_BASS_KILL),
                (swap,     EQ_BASS_UNITY),
                (ov_e,     EQ_BASS_UNITY),
                (ov_e + 1, EQ_BASS_UNITY),
            ])

    # sort + dedupe per track
    for name in auto:
        for param in ("volume", "eq_bass"):
            pts = sorted(auto[name][param], key=lambda p: p[0])
            deduped: list[tuple[float, float]] = []
            for p in pts:
                if deduped and abs(deduped[-1][0] - p[0]) < 0.01:
                    deduped[-1] = p
                else:
                    deduped.append(p)
            auto[name][param] = deduped

    return auto


# ── Track name matching ───────────────────────────────────────────────────────

def _normalise(s: str) -> str:
    return s.lower().replace("–", "-").replace("—", "-").strip()


def match_tracks_to_als(tracks: list[TrackInfo],
                        als_tracks: list[tuple[int, int, str]]) -> None:
    """Populate each TrackInfo's als_* fields by matching names."""
    for track in tracks:
        tn = _normalise(track.name)
        for idx, (s, e, als_name) in enumerate(als_tracks):
            an = _normalise(als_name)
            if tn == an or tn in an or an in tn:
                track.als_index = idx
                track.als_start = s
                track.als_end = e
                break
        if track.als_index < 0:
            # fuzzy: compare first 20 chars of title portion
            for idx, (s, e, als_name) in enumerate(als_tracks):
                an = _normalise(als_name)
                if len(tn) > 5 and len(an) > 5:
                    if tn[:20] in an or an[:20] in tn:
                        track.als_index = idx
                        track.als_start = s
                        track.als_end = e
                        break


# ── Display helpers ───────────────────────────────────────────────────────────

def _short(name: str) -> str:
    parts = name.split(" - ")
    if len(parts) >= 2:
        return parts[1].split(" (")[0].split(" SW")[0].split(" 24")[0][:20]
    return name[:25]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 4:
        print("Usage: python apply_automation.py <sections.als> <sections.json> <output.als>")
        sys.exit(1)

    als_path = Path(sys.argv[1])
    json_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])

    # ── read inputs ───────────────────────────────────────────────────
    print(f"Reading {als_path.name} ...")
    lines = decompress_als(als_path)
    print(f"  {len(lines)} lines")

    print(f"Reading {json_path.name} ...")
    with open(json_path, encoding="utf-8") as f:
        sections_data: dict = json.load(f)
    print(f"  {len(sections_data)} tracks in JSON")

    # ── build track list ──────────────────────────────────────────────
    tracks = ordered_tracks(sections_data)
    print(f"\nTrack order:")
    for i, t in enumerate(tracks):
        bars = (t.arr_end - t.arr_start) / 4
        print(f"  {i + 1:2}. {_short(t.name):20s}  "
              f"arr {t.arr_start:6.0f} -> {t.arr_end:6.0f}  ({bars:.0f} bars)")

    # ── find ALS tracks ───────────────────────────────────────────────
    als_tracks = find_track_line_ranges(lines)
    print(f"\nALS tracks: {len(als_tracks)}")
    for s, e, name in als_tracks:
        print(f"  {name[:50]:50s}  lines {s}-{e}")

    match_tracks_to_als(tracks, als_tracks)
    unmatched = [t for t in tracks if t.als_index < 0]
    if unmatched:
        for t in unmatched:
            print(f"  !! UNMATCHED: {t.name}")
        print("Cannot continue without matching all tracks.")
        sys.exit(1)

    # ── plan transitions ──────────────────────────────────────────────
    plans = plan_transitions(tracks)
    print(f"\n{len(plans)} transitions planned:")
    for i, p in enumerate(plans):
        bars = (p.overlap_end - p.overlap_start) / 4
        rules: list[str] = []
        if p.two_stage_bass:
            rules.append("R2:two-stage-bass")
        if p.two_stage_volume:
            rules.append("R3:two-stage-vol")
        if p.low_sneak:
            rules.append("R4:low-sneak")
        rules_str = f"  rules=[{', '.join(rules)}]" if rules else ""
        print(f"  T{i + 1}: {_short(p.outgoing.name):18s} -> {_short(p.incoming.name):18s}"
              f"  overlap {p.overlap_start:.0f}-{p.overlap_end:.0f} ({bars:.0f} bars)"
              f"  swap@{p.bass_swap:.0f} ({p.reason}){rules_str}")

    # ── generate automation ───────────────────────────────────────────
    track_auto = build_track_automation(plans, tracks)
    print(f"\nAutomation points per track:")
    for t in tracks:
        v = len(track_auto[t.name]["volume"])
        b = len(track_auto[t.name]["eq_bass"])
        if v or b:
            print(f"  {_short(t.name):20s}  vol={v}  bass={b}")

    # ── insert envelopes into ALS ─────────────────────────────────────
    print(f"\nInserting envelopes ...")
    offset = 0
    for track in tracks:
        vol_pts = track_auto[track.name]["volume"]
        bass_pts = track_auto[track.name]["eq_bass"]
        if not vol_pts and not bass_pts:
            continue

        s = track.als_start + offset
        e = track.als_end + offset

        vol_id = find_utility_gain_target(lines, s, e)
        bass_id = find_eq_bass_target(lines, s, e)

        # detect indent from the <Envelopes> tag
        _, _, indent = _find_envelopes_tag(lines, s, e)
        env_indent = indent + "\t"  # one level deeper than <Envelopes>

        envelopes: list[list[str]] = []

        if vol_id and vol_pts:
            envelopes.append(build_envelope_xml(vol_id, vol_pts, env_indent))
            print(f"  {_short(track.name):20s}  volume  target={vol_id}  "
                  f"pts={len(vol_pts)}")
        elif vol_pts:
            print(f"  !! {_short(track.name)}  volume points but NO target found")

        if bass_id and bass_pts:
            envelopes.append(build_envelope_xml(bass_id, bass_pts, env_indent))
            print(f"  {_short(track.name):20s}  eq_bass target={bass_id}  "
                  f"pts={len(bass_pts)}")
        elif bass_pts:
            print(f"  !! {_short(track.name)}  bass points but NO target found")

        if envelopes:
            delta = insert_envelopes(lines, s, e, envelopes)
            offset += delta
            # update end after insertion so lane search finds the right block
            e += delta

        # set both automation lanes visible (EQ bass + volume)
        delta = set_automation_lanes(lines, s, e)
        offset += delta

    # ── write output ──────────────────────────────────────────────────
    print(f"\nWriting {output_path.name} ...")
    compress_als(lines, output_path)
    print(f"Done -> {output_path}")


if __name__ == "__main__":
    main()
