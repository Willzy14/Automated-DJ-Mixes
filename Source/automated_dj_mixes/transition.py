"""Unified transition planning — one technique, vary timing.

Every transition uses the same automation shape: volume fade + EQ bass swap.
The bass swap point IS the chop point — one cue, music-aligned. No arbitrary
phrase snapping; Rekordbox phrase boundaries are already on musical phrases.

The chop happens at the outgoing's `last_kick` (end of outro percussion).
The incoming is positioned so its first chorus drop lands on that exact beat.
The loop region is the 2 bars right before the chop — outro percussion,
"lowest energy that still has percussion" (Sam).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from automated_dj_mixes.analysis import TrackAnalysis
from automated_dj_mixes.automation import AutomationPoint
from automated_dj_mixes.cue_candidates import CueCandidate, first_credible

LOOP_BEATS = 8           # 2 bars — subtle continuity, hats/perc
MAX_OVERLAP_BEATS = 48 * 4
MIN_OVERLAP_BEATS = 4 * 4
MIN_CANDIDATE_CONFIDENCE = 0.5


@dataclass
class LoopSpec:
    """Chop-and-duplicate spec.

    The original clip is chopped at `chop_at_beats` (source beats) — this also
    skips any post-percussion tail. Then `num_extra_copies` duplicate clips
    are placed on the timeline, each playing the 2 bars right before the chop
    (the outro percussion region). NOT Ableton's clip loop feature.
    """
    chop_at_beats: float        # source beat where the original is chopped
    loop_source_start: float    # source beat where the loop section begins
    loop_source_end: float      # source beat where the loop section ends (= chop)
    num_extra_copies: int       # number of duplicate clips after the chop


@dataclass
class TransitionSpec:
    """Complete transition between two tracks — positions, automation, loops."""
    incoming_arrangement_start: float
    transition_start: float
    bass_swap: float
    transition_end: float

    outgoing_volume: list[AutomationPoint]
    incoming_volume: list[AutomationPoint]
    outgoing_eq_bass: list[AutomationPoint]
    incoming_eq_bass: list[AutomationPoint]

    outgoing_loop: LoopSpec | None = None
    incoming_loop: LoopSpec | None = None

    decision_log: list[str] = field(default_factory=list)


def _find_outgoing_bass_end(outgoing_rb, outgoing_total_beats):
    """End of last 'chorus' (drop) — where bass naturally cuts (source beats)."""
    if outgoing_rb and outgoing_rb.phrases:
        for i in range(len(outgoing_rb.phrases) - 1, -1, -1):
            if outgoing_rb.phrases[i].label == "chorus":
                return float(outgoing_rb.phrase_end_beat(i) - 1), "rb_last_drop_end"
    # No Rekordbox phrases — last 32 bars is a rough fallback
    return max(0.0, outgoing_total_beats - 128), "fallback_32bars_before_end"


def _find_outgoing_chop_point(outgoing_rb, outgoing_total_beats, outgoing_bass_end):
    """Where to chop the outgoing — end of outro percussion, before any decay.

    Uses Rekordbox 'outro' phrase end if present (rounded down to a bar).
    Falls back to bass_end if no outro phrase exists.
    """
    if outgoing_rb and outgoing_rb.phrases:
        for i in range(len(outgoing_rb.phrases) - 1, -1, -1):
            if outgoing_rb.phrases[i].label == "outro":
                outro_end = float(outgoing_rb.phrase_end_beat(i) - 1)
                # Round down to bar
                chop = math.floor(outro_end / 4) * 4
                chop = min(chop, outgoing_total_beats)
                if chop > outgoing_bass_end:
                    return chop, "rb_outro_end"
                break
    # Fall back to bass_end (= end of last drop). Bar-align it.
    chop = math.ceil(outgoing_bass_end / 4) * 4
    chop = min(float(chop), outgoing_total_beats)
    return chop, "bass_end"


def _find_incoming_bass_start(incoming_rb, incoming_total_beats):
    """First 'chorus' (drop) — where bass naturally enters (source beats)."""
    if incoming_rb and incoming_rb.phrases:
        for p in incoming_rb.phrases:
            if p.label == "chorus":
                return float(p.start_beat - 1), "rb_first_drop"
    return min(128.0, incoming_total_beats / 2), "fallback_32bars"


def _find_incoming_first_break(incoming_rb, incoming_bass_start, incoming_total_beats):
    """First 'down' (break) after the first drop — transition end (source beats)."""
    if incoming_rb and incoming_rb.phrases:
        past_first_chorus = False
        for p in incoming_rb.phrases:
            if p.label == "chorus":
                past_first_chorus = True
            elif past_first_chorus and p.label == "down":
                return float(p.start_beat - 1), "rb_first_break_after_drop"
    return incoming_bass_start + 128, "fallback_32bars_after_drop"


def plan_transition(
    outgoing: TrackAnalysis,
    incoming: TrackAnalysis,
    outgoing_arrangement_start: float,
    outgoing_total_beats: float,
    incoming_total_beats: float,
    project_bpm: float,
    outgoing_rb=None,
    incoming_rb=None,
    outgoing_candidates: list[CueCandidate] | None = None,
    incoming_candidates: list[CueCandidate] | None = None,
) -> TransitionSpec:
    log: list[str] = []

    # Prefer ranked cue candidates (Step 7). Fall back to RB phrase logic
    # when candidates are missing or below the confidence floor.
    chop_cand = first_credible(outgoing_candidates or [], "chop_point", MIN_CANDIDATE_CONFIDENCE) \
        or first_credible(outgoing_candidates or [], "outro_start", MIN_CANDIDATE_CONFIDENCE)
    bass_entry_cand = first_credible(incoming_candidates or [], "bass_entry", MIN_CANDIDATE_CONFIDENCE)
    incoming_break_cand = first_credible(incoming_candidates or [], "break_start", MIN_CANDIDATE_CONFIDENCE)

    # --- Chop point (where outgoing audio is cut) ---
    if chop_cand:
        chop_at = float(chop_cand.beat)
        chop_src = f"candidate:{chop_cand.cue_type}(conf={chop_cand.confidence:.2f})"
        log.append(f"chop sources: {', '.join(chop_cand.sources)}")
    else:
        outgoing_bass_end, _ = _find_outgoing_bass_end(outgoing_rb, outgoing_total_beats)
        chop_at, chop_src = _find_outgoing_chop_point(
            outgoing_rb, outgoing_total_beats, outgoing_bass_end,
        )
        chop_src = f"rb_fallback:{chop_src}"

    # --- Incoming bass entry (where the drop drops) ---
    if bass_entry_cand:
        incoming_bass_start = float(bass_entry_cand.beat)
        in_bs_src = f"candidate:bass_entry(conf={bass_entry_cand.confidence:.2f})"
        log.append(f"bass_entry sources: {', '.join(bass_entry_cand.sources)}")
    else:
        incoming_bass_start, in_bs_src = _find_incoming_bass_start(
            incoming_rb, incoming_total_beats,
        )
        in_bs_src = f"rb_fallback:{in_bs_src}"

    # --- Incoming first break (transition end target) ---
    if incoming_break_cand:
        incoming_first_break = float(incoming_break_cand.beat)
        in_fb_src = f"candidate:break_start(conf={incoming_break_cand.confidence:.2f})"
    else:
        incoming_first_break, in_fb_src = _find_incoming_first_break(
            incoming_rb, incoming_bass_start, incoming_total_beats,
        )
        in_fb_src = f"rb_fallback:{in_fb_src}"

    # Position incoming so its first chorus drop coincides with the chop point.
    # This makes bass_swap = chop_arrangement = a single, natural musical cue.
    chop_arrangement = outgoing_arrangement_start + chop_at
    incoming_arrangement_start = chop_arrangement - incoming_bass_start
    bass_swap = chop_arrangement

    # Bound overlap — if the natural alignment requires too much overlap, we
    # clamp and let bass_swap follow incoming's bass entry (chop sits earlier;
    # the gap is filled by the looping duplicates of outro percussion).
    outgoing_clip_end = outgoing_arrangement_start + outgoing_total_beats
    earliest_start = outgoing_clip_end - MAX_OVERLAP_BEATS
    latest_start = outgoing_clip_end - MIN_OVERLAP_BEATS

    if incoming_arrangement_start < earliest_start:
        incoming_arrangement_start = earliest_start
        bass_swap = incoming_arrangement_start + incoming_bass_start
    elif incoming_arrangement_start > latest_start:
        incoming_arrangement_start = latest_start
        bass_swap = incoming_arrangement_start + incoming_bass_start

    transition_start = float(incoming_arrangement_start)
    transition_end = incoming_arrangement_start + incoming_first_break

    log.append(
        f"chop@{chop_arrangement:.0f}({chop_src}={chop_at:.0f}) "
        f"swap@{bass_swap:.0f}({in_bs_src}={incoming_bass_start:.0f}) "
        f"start@{transition_start:.0f} end@{transition_end:.0f}({in_fb_src})"
    )

    # Looping: 2 bars from before the chop (outro percussion content)
    loop_source_start = max(0.0, chop_at - LOOP_BEATS)
    loop_source_end = chop_at

    if chop_arrangement < transition_end:
        shortfall = transition_end - chop_arrangement
        num_extra = max(1, int(math.ceil(shortfall / LOOP_BEATS)))
    else:
        num_extra = 0

    outgoing_loop = LoopSpec(
        chop_at_beats=chop_at,
        loop_source_start=loop_source_start,
        loop_source_end=loop_source_end,
        num_extra_copies=num_extra,
    )
    log.append(
        f"loop: 2bars src[{loop_source_start:.0f}-{loop_source_end:.0f}] x{num_extra}"
    )

    # AUTOMATION
    # Outgoing volume: hold full until bass_swap, THEN fade to 0 by transition_end.
    # (Pre-swap, both tracks play normally with incoming bass-cut — no need to
    # fade the outgoing yet.)
    outgoing_volume = [
        AutomationPoint(time_beats=transition_start, value=1.0),
        AutomationPoint(time_beats=bass_swap, value=1.0),
        AutomationPoint(time_beats=transition_end, value=0.0),
    ]
    # Incoming volume: smooth fade-in across whole transition
    incoming_volume = [
        AutomationPoint(time_beats=transition_start, value=0.2),
        AutomationPoint(time_beats=transition_end, value=1.0),
    ]
    # EQ bass: hard swap at bass_swap (1.0=unity, 0.18≈-15dB)
    outgoing_eq_bass = [
        AutomationPoint(time_beats=transition_start, value=1.0),
        AutomationPoint(time_beats=bass_swap, value=1.0),
        AutomationPoint(time_beats=bass_swap + 0.01, value=0.18),
        AutomationPoint(time_beats=transition_end, value=0.18),
    ]
    incoming_eq_bass = [
        AutomationPoint(time_beats=transition_start, value=0.18),
        AutomationPoint(time_beats=bass_swap, value=0.18),
        AutomationPoint(time_beats=bass_swap + 0.01, value=1.0),
        AutomationPoint(time_beats=transition_end, value=1.0),
    ]

    return TransitionSpec(
        incoming_arrangement_start=incoming_arrangement_start,
        transition_start=transition_start,
        bass_swap=bass_swap,
        transition_end=transition_end,
        outgoing_volume=outgoing_volume,
        incoming_volume=incoming_volume,
        outgoing_eq_bass=outgoing_eq_bass,
        incoming_eq_bass=incoming_eq_bass,
        outgoing_loop=outgoing_loop,
        incoming_loop=None,
        decision_log=log,
    )
