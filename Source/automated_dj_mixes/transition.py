"""Unified transition planning — one technique, vary timing.

Every transition uses the same automation shape: two-phase volume fade +
hard EQ bass swap.

Phase 1 (transition_start → bass_swap):
  Outgoing holds at unity. Incoming volume ramps from 0.2 → 1.0 with its
  bass killed by EQ. Listener still hears the outgoing as the main track.

Phase 2 (bass_swap → transition_end):
  Hard EQ swap at bass_swap: outgoing bass off, incoming bass on. Outgoing
  is chopped at this point and a stripped-percussion loop fills the gap
  while its volume slowly fades to 0. The fade lands on the incoming's
  first break for a natural breath.

Inputs come from cue_candidates.py — both the RB-derived path (energy +
phrase) and the MIK-only synthesis path (energy-validated MIK auto-cues).
Both feed the same candidate pipeline, so plan_transition doesn't care
where the candidates came from. RB-phrase fallbacks remain only for the
edge case where NEITHER signal is available.

Sam's hard rules (2026-05):
  - All breakpoints snap to whole Ableton beats (see snap()).
  - Loops come from intro or outro only — never the middle.
  - Bass swap lands on the incoming's energy change (the drop).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from automated_dj_mixes.analysis import TrackAnalysis
from automated_dj_mixes.automation import AutomationPoint
from automated_dj_mixes.cue_candidates import CueCandidate, first_credible, first_drop_candidate
from automated_dj_mixes.phrase_viz import Interval

LOOP_BEATS = 8           # 2 bars — subtle continuity, hats/perc
MAX_OVERLAP_BEATS = 48 * 4
MIN_OVERLAP_BEATS = 16 * 4   # aligned with validator (Codex review, 2026-05)
MIN_CANDIDATE_CONFIDENCE = 0.5

# Outro reserve: the chop must leave at least this many beats of audio
# AFTER it for the loop to source clean outro percussion. Tracks where
# the chop would land within OUTRO_RESERVE_BEATS of the track end get
# their chop pulled back so the loop has somewhere to live.
OUTRO_RESERVE_BEATS = 4 * 4   # 4 bars of outro audio reserved past chop


def snap(beat: float) -> float:
    """Snap a beat position to the nearest whole Ableton beat.

    Sam's hard rule (2026-05): Ableton's grid is the overriding timing
    source. Any cue suggested by MIK or Rekordbox MUST be pulled to the
    nearest whole beat before being written into automation.
    """
    return float(round(beat))


@dataclass
class PhraseGrid:
    """Snap arrangement beats to dance-music phrase boundaries.

    Sam's rule (2026-05): "automation should obviously be hitting on bars,
    but then it needs to be within the musical theory element of it all".
    Dance music phrases are 16 beats (4 bars). Sections are 32 or 64 beats.
    Automation that lands mid-phrase sounds wrong even when on a beat.

    Tiered snap behaviour (Sam's chosen mode):
      1. Try the preferred grid (default 16-beat / 4-bar phrase).
         Accept if natural drift ≤ max_shift_prefer (default 4 beats).
      2. Fall back to first secondary grid (default 8-beat / 2-bar).
         Accept if drift ≤ max_shift_secondary (default 4 beats).
      3. Fall back to next secondary (default 4-beat / bar) — HARD FLOOR.

    The bar boundary (4-beat) is the absolute floor — anything off-bar is
    musically broken and the validator hard-fails it.
    """
    origin: float = 0.0
    prefer: int = 16
    fallbacks: tuple = (8, 4)
    max_shift_prefer: int = 4
    max_shift_secondary: int = 4

    def snap(self, beat: float, log: list[str] | None = None) -> float:
        """Snap to the largest acceptable phrase boundary."""
        for grid in (self.prefer, *self.fallbacks):
            limit = self.max_shift_prefer if grid == self.prefer else self.max_shift_secondary
            snapped = self.origin + round((beat - self.origin) / grid) * grid
            if abs(snapped - beat) <= limit:
                if log is not None and grid != self.prefer:
                    log.append(
                        f"[phrase-snap fallback] beat {beat:.1f} → {snapped:.0f} "
                        f"(grid={grid}, would shift {snapped - beat:+.1f})"
                    )
                return float(snapped)
        # Last resort: bar-align (4-beat) even if it shifts more than the limit.
        snapped = self.origin + round((beat - self.origin) / 4) * 4
        if log is not None:
            log.append(
                f"[phrase-snap FLOOR] beat {beat:.1f} → {snapped:.0f} "
                f"(grid=4, forced; natural drift exceeded all phrase tolerances)"
            )
        return float(snapped)


@dataclass
class LoopSpec:
    """Chop-and-duplicate spec.

    The original clip is chopped at `chop_at_beats` (source beats) — this also
    skips any post-percussion tail. Then `num_extra_copies` duplicate clips
    are placed on the timeline, each playing the `[loop_source_start,
    loop_source_end)` region of the SAME source audio. NOT Ableton's clip
    loop feature.

    `loop_source_*` is chosen INDEPENDENTLY of `chop_at_beats` — we want the
    stripped-down percussion region (Sam's brief: drums and maybe bass, no
    melody). It is usually somewhere in the outro but may sit several bars
    earlier than the chop point.
    """
    chop_at_beats: float        # source beat where the original is chopped
    loop_source_start: float    # source beat where the loop section begins
    loop_source_end: float      # source beat where the loop section ends
    num_extra_copies: int       # number of duplicate clips after the chop


def _score_loop_interval(iv: Interval) -> float:
    """Lower = better loop candidate (more stripped, more percussion-focused).

    A good loop is drums (+ maybe bass) with minimal melodic content.
    Indicators:
      - low waveform_height (Rekordbox PWV5 — quieter, simpler content)
      - low bass_librosa (drums-only sections often have transient bass only)
      - rb_label is outro or down (stripped phrases), not chorus
    """
    height = iv.energy.waveform_height if iv.energy.waveform_height is not None else 0.5
    bass = iv.energy.bass_librosa
    score = height * 0.55 + bass * 0.45
    label_bonus = {
        "outro": -0.15,   # stripped by definition
        "down": -0.10,    # breaks are also typically stripped
        "intro": -0.05,
        "up": 0.05,
        "chorus": 0.30,   # full energy — bad loop content
    }
    score += label_bonus.get(iv.rb_label or "", 0.0)
    return score


def find_loop_region(
    chop_at_beats: float,
    total_beats: float,
    loop_beats: int = LOOP_BEATS,
    intro_end_beat: float | None = None,
    intervals: list[Interval] | None = None,
    mik_energy_segments: list | None = None,
    bpm: float | None = None,
    first_downbeat_sec: float = 0.0,
    role: str = "outgoing",
    audio_path=None,
) -> tuple[float, float, str]:
    """Find a stripped-percussion loop region from the front (intro) or
    back (outro) of the track — never from the middle.

    Sam's rules (2026-05):
      - "Loops from the front or back of the track which have drums
        definitely, maybe bass, but are simple parts."
      - "If you are looping an INCOMING track use the intro; if you are
        looping an OUTGOING track use the outro. Where possible — if
        there are only clean beats at one end, use those."
      - The OUTRO starts at `chop_at_beats` (the chop position) — that's
        where the bass switch happens and where the music transitions
        into stripped percussion. The loop sources from the 8 beats AT
        or just after the chop, NOT from the "post-break body" before it.

    `role` selects the preference order:
      - "outgoing" (default): outro first, intro fallback
      - "incoming": intro first, outro fallback

    Within each end, signal sources are tried in this order:
      1. Rekordbox interval scored by stripped-ness (phrase + height + bass)
      2. MIK lowest-energy segment in that region
      3. Anchor-only (first/last 8 beats of the region)

    All beat coordinates are in source-beat space (beats since
    first_downbeat_sec). Returns (loop_start, loop_end, source_label).
    """

    def clamp(start: float) -> tuple[float, float]:
        end = start + loop_beats
        if end > total_beats:
            end = total_beats
            start = max(0.0, end - loop_beats)
        return max(0.0, start), end

    def refine_for_clean_audio(start: float, end: float, label: str,
                                search_back_beats: int = 16) -> tuple[float, float, str]:
        """If we have the audio path, scan a wider window for dead-air-free
        content. Sam's rule (2026-05): "from the finish backwards, take 16
        beats, chop 4 from that point" — find a clean groove window inside
        the broader 16-beat region near `end`.
        """
        if audio_path is None or bpm is None or bpm <= 0:
            return start, end, label
        try:
            from automated_dj_mixes.amplitude_analysis import find_clean_loop_window
            sec_per_beat = 60.0 / bpm
            search_lo_beat = max(0.0, end - search_back_beats)
            search_lo_sec = first_downbeat_sec + search_lo_beat * sec_per_beat
            search_hi_sec = first_downbeat_sec + end * sec_per_beat
            clean = find_clean_loop_window(
                audio_path, search_lo_sec, search_hi_sec, loop_beats, bpm,
            )
            if clean is not None:
                clean_start_sec, clean_end_sec = clean
                refined_start = max(0.0, (clean_start_sec - first_downbeat_sec) / sec_per_beat)
                refined_end = refined_start + loop_beats
                if refined_end > total_beats:
                    return start, end, label
                # If the refinement moved meaningfully, note it in the label
                if abs(refined_start - start) > 0.5:
                    return refined_start, refined_end, f"{label}|clean_shift={refined_start - start:+.0f}b"
                return refined_start, refined_end, label
        except Exception:
            pass
        return start, end, label

    def try_outro() -> tuple[float, float, str] | None:
        # OUTRO = at/after chop_at_beats (Sam's terminology, 2026-05).
        # Path A: RB outro intervals — RB phrase data marks the outro region
        # directly. These can sit on either side of the chop; we prefer ones
        # that overlap with or sit past the chop position.
        if intervals:
            outro_ivs = [
                iv for iv in intervals
                if iv.rb_label == "outro"
                and (iv.source_end_beats - iv.source_start_beats) >= loop_beats
            ]
            # Prefer outro intervals at or past the chop (the real outro region).
            # Tolerance widened from 4 to 16 beats because phrase-snapping the
            # chop can move it up to 8 beats from natural; 16 covers that drift
            # plus a phrase of slack.
            past_chop = [iv for iv in outro_ivs if iv.source_start_beats >= chop_at_beats - 16]
            pool = past_chop or outro_ivs
            if pool:
                best = min(pool, key=_score_loop_interval)
                start, end = clamp(float(best.source_start_beats))
                score = _score_loop_interval(best)
                return start, end, f"rb_outro_interval_idx={best.index}_score={score:.2f}"

        # Path B: MIK lowest-energy segment AT or after the chop position.
        # This is the stripped percussion region — past where we'd normally
        # let the original play.
        if mik_energy_segments and bpm and bpm > 0 and total_beats:
            sec_per_beat = 60.0 / bpm
            chop_at_sec = first_downbeat_sec + chop_at_beats * sec_per_beat
            track_end_sec = first_downbeat_sec + total_beats * sec_per_beat
            post_chop_segs = [
                s for s in mik_energy_segments
                if s.start_sec >= chop_at_sec - 1.0 and s.start_sec < track_end_sec
            ]
            if post_chop_segs:
                best = min(post_chop_segs, key=lambda s: s.energy)
                start_beat = (best.start_sec - first_downbeat_sec) / sec_per_beat
                start, end = clamp(max(start_beat, chop_at_beats))
                return start, end, f"mik_outro_E{best.energy}_at_{best.start_sec:.0f}s"

        # Path C: 8 beats AT chop_at (anchor only).
        if total_beats - chop_at_beats >= loop_beats:
            start, end = clamp(chop_at_beats)
            return start, end, f"outro@chop_{chop_at_beats:.0f}_first_{loop_beats}beats"

        return None

    def try_intro() -> tuple[float, float, str] | None:
        # Path A: RB intro intervals
        if intervals:
            intro_ivs = [
                iv for iv in intervals
                if iv.rb_label == "intro"
                and (iv.source_end_beats - iv.source_start_beats) >= loop_beats
            ]
            if intro_ivs:
                best = min(intro_ivs, key=_score_loop_interval)
                start, end = clamp(float(best.source_start_beats))
                score = _score_loop_interval(best)
                return start, end, f"rb_intro_interval_idx={best.index}_score={score:.2f}"

        # Path B: MIK lowest-energy segment within the intro region
        if (mik_energy_segments and bpm and bpm > 0
                and intro_end_beat is not None):
            sec_per_beat = 60.0 / bpm
            intro_end_sec = first_downbeat_sec + intro_end_beat * sec_per_beat
            intro_segs = [
                s for s in mik_energy_segments
                if s.start_sec >= first_downbeat_sec and s.end_sec <= intro_end_sec + 1.0
            ]
            if intro_segs:
                best = min(intro_segs, key=lambda s: s.energy)
                start_beat = (best.start_sec - first_downbeat_sec) / sec_per_beat
                start, end = clamp(max(0.0, start_beat))
                return start, end, f"mik_intro_E{best.energy}_at_{best.start_sec:.0f}s"

        # Path C: pre-drop intro anchor (last 8 beats of the intro)
        if intro_end_beat is not None and intro_end_beat >= loop_beats * 2:
            start, end = clamp(intro_end_beat - loop_beats)
            return start, end, f"intro_pre_drop@{intro_end_beat:.0f}_last_{loop_beats}beats"

        return None

    if role == "incoming":
        result = try_intro() or try_outro()
    else:
        result = try_outro() or try_intro()

    if result is not None:
        # Dead-air refinement: shift the chosen region to a clean groove
        # window if one exists in the surrounding 16 beats.
        start, end, label = result
        return refine_for_clean_audio(start, end, label)

    # Final fallback: 8 beats before the chop (last-resort, may include
    # melodic content). Logged so we can spot tracks with no clean ends.
    fallback_start = max(0.0, chop_at_beats - loop_beats)
    fallback_end = chop_at_beats
    return refine_for_clean_audio(fallback_start, fallback_end, "fallback_8beats_before_chop")


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
    outgoing_intervals: list[Interval] | None = None,
    outgoing_mik_energy_segments: list | None = None,
) -> TransitionSpec:
    log: list[str] = []

    # Hard invariant: outgoing must already sit on a bar boundary. If not,
    # phrase-snapping the arrangement-side bass_swap would silently pull
    # chop_at off the source's musical grid. Catch this loudly here.
    if outgoing_arrangement_start % 4 != 0:
        raise ValueError(
            f"outgoing_arrangement_start={outgoing_arrangement_start} is not on a "
            f"bar boundary (mod 4 = {outgoing_arrangement_start % 4}). "
            "This will misalign chop_at on the source. Previous transition's "
            "incoming_arrangement_start should snap to ≥ bar boundary."
        )

    # Per-track phrase grids (Sam's rule, 2026-05). Each track's phrase
    # grid starts at THAT track's beat 1, not at arrangement beat 0. The
    # outgoing grid is used to snap where the incoming track LANDS — so
    # the new track's beat 1 is in phrase alignment with the old track's
    # phrase grid. The incoming grid (computed later) is used to snap
    # bass_swap — so the swap event lands on the incoming track's own
    # phrase boundary, which is what the listener perceives.
    outgoing_grid = PhraseGrid(origin=outgoing_arrangement_start)

    # Prefer ranked cue candidates (Step 7). Fall back to RB phrase logic
    # when candidates are missing or below the confidence floor.
    chop_cand = first_credible(outgoing_candidates or [], "chop_point", MIN_CANDIDATE_CONFIDENCE) \
        or first_credible(outgoing_candidates or [], "outro_start", MIN_CANDIDATE_CONFIDENCE)
    # bass_entry must be the FIRST drop, not the loudest/biggest.
    # See first_drop_candidate docstring for the dance-music rationale.
    bass_entry_cand = first_drop_candidate(incoming_candidates or [], MIN_CANDIDATE_CONFIDENCE)
    incoming_break_cand = first_credible(incoming_candidates or [], "break_start", MIN_CANDIDATE_CONFIDENCE)

    # For loop-region anchoring: the OUTGOING track's "intro end" (= where
    # the first drop happens). The outro anchor is `chop_at` itself —
    # Sam's terminology rule: outro starts at the chop, not at the
    # post-break-body marker.
    outgoing_intro_end_cand = first_drop_candidate(outgoing_candidates or [], MIN_CANDIDATE_CONFIDENCE)

    # --- Chop point (where outgoing audio is cut) ---
    # All beat coordinates are snapped to whole Ableton beats per Sam's
    # hard rule (2026-05). MIK/RB suggestions are pulled to the nearest beat.
    if chop_cand:
        chop_at = snap(chop_cand.beat)
        chop_src = f"candidate:{chop_cand.cue_type}(conf={chop_cand.confidence:.2f})"
        log.append(f"chop sources: {', '.join(chop_cand.sources)}")
    else:
        outgoing_bass_end, _ = _find_outgoing_bass_end(outgoing_rb, outgoing_total_beats)
        raw_chop, chop_src = _find_outgoing_chop_point(
            outgoing_rb, outgoing_total_beats, outgoing_bass_end,
        )
        chop_at = snap(raw_chop)
        chop_src = f"rb_fallback:{chop_src}"

    # Reserve outro audio for the loop. If chop_at sits within
    # OUTRO_RESERVE_BEATS of the track end, the outro loop region has
    # nowhere to live — we'd fall back to intro and the transition feels
    # wrong. Pull chop_at back by enough to leave loop room.
    max_chop_at = snap(outgoing_total_beats - (LOOP_BEATS + OUTRO_RESERVE_BEATS))
    if chop_at > max_chop_at:
        log.append(
            f"[chop reserved] chop_at {chop_at:.0f} → {max_chop_at:.0f} "
            f"(too close to track end; needs {LOOP_BEATS + OUTRO_RESERVE_BEATS}b for outro loop)"
        )
        chop_at = max(0.0, max_chop_at)

    # --- Incoming bass entry (where the drop drops) ---
    if bass_entry_cand:
        incoming_bass_start = snap(bass_entry_cand.beat)
        in_bs_src = f"candidate:bass_entry(conf={bass_entry_cand.confidence:.2f})"
        log.append(f"bass_entry sources: {', '.join(bass_entry_cand.sources)}")
    else:
        raw_bs, in_bs_src = _find_incoming_bass_start(
            incoming_rb, incoming_total_beats,
        )
        incoming_bass_start = snap(raw_bs)
        in_bs_src = f"rb_fallback:{in_bs_src}"

    # --- Incoming first break (transition end target) ---
    if incoming_break_cand:
        incoming_first_break = snap(incoming_break_cand.beat)
        in_fb_src = f"candidate:break_start(conf={incoming_break_cand.confidence:.2f})"
    else:
        raw_fb, in_fb_src = _find_incoming_first_break(
            incoming_rb, incoming_bass_start, incoming_total_beats,
        )
        incoming_first_break = snap(raw_fb)
        in_fb_src = f"rb_fallback:{in_fb_src}"

    # Position incoming so its first chorus drop coincides with the chop point.
    # This makes bass_swap = chop_arrangement = a single, natural musical cue.
    #
    # `incoming_bass_start` is measured in beats since first_downbeat. The
    # audio CLIP starts at source-time 0, but its first downbeat plays
    # `first_downbeat_offset_beats` later. So to put the drop at
    # `chop_arrangement`, the clip must start that many beats EARLIER than
    # naively computing arrangement_start = chop_arrangement - bass_start.
    # Without this correction the incoming's kick lands ~1 beat after the
    # bass_swap automation point — the "off-by-one" Sam saw on multiple
    # transitions (2026-05).
    incoming_first_downbeat_sec = incoming.first_downbeat_sec or 0.0
    incoming_downbeat_offset_beats = incoming_first_downbeat_sec * project_bpm / 60.0

    # Two-grid snap (per-track, Sam's rule 2026-05):
    # 1. Position incoming so its beat 1 lands on the OUTGOING's phrase grid.
    #    This makes the two tracks' phrase grids align with each other.
    # 2. Snap bass_swap to the INCOMING's phrase grid (which, because of
    #    step 1, also lands on outgoing's grid at the chosen tier).
    natural_swap = outgoing_arrangement_start + chop_at
    natural_incoming_start = (
        natural_swap - incoming_bass_start - incoming_downbeat_offset_beats
    )
    incoming_arrangement_start = outgoing_grid.snap(natural_incoming_start, log)

    incoming_grid = PhraseGrid(origin=incoming_arrangement_start)
    natural_swap_v2 = (
        incoming_arrangement_start + incoming_bass_start + incoming_downbeat_offset_beats
    )
    bass_swap = incoming_grid.snap(natural_swap_v2, log)
    chop_arrangement = bass_swap

    # Bound overlap — if the natural alignment requires too much overlap, we
    # clamp incoming's start. When this happens we ALSO move chop_arrangement
    # to follow bass_swap so the outgoing loop start and the incoming bass
    # entry land on the SAME beat (Sam's rule: "you've identified two spots
    # correctly, but then not locked them in"). chop_at on the outgoing is
    # also updated, capped at the track's natural end.
    outgoing_clip_end = outgoing_arrangement_start + outgoing_total_beats
    # Earliest/latest start clamps snap to OUTGOING's grid (so incoming
    # always lands on outgoing's phrase grid after clamping).
    earliest_start = outgoing_grid.snap(outgoing_clip_end - MAX_OVERLAP_BEATS, log)
    latest_start = outgoing_grid.snap(outgoing_clip_end - MIN_OVERLAP_BEATS, log)

    natural_start = incoming_arrangement_start
    pre_clamp_swap = bass_swap
    natural_chop_at = chop_at
    if incoming_arrangement_start < earliest_start:
        incoming_arrangement_start = earliest_start
        # Recreate incoming_grid for the new incoming_start and re-snap.
        incoming_grid = PhraseGrid(origin=incoming_arrangement_start)
        bass_swap = incoming_grid.snap(
            incoming_arrangement_start + incoming_bass_start + incoming_downbeat_offset_beats, log,
        )
        chop_arrangement = bass_swap
        new_chop_at = chop_arrangement - outgoing_arrangement_start
        chop_at = max(natural_chop_at, min(new_chop_at, snap(outgoing_total_beats)))
        log.append(
            f"[WARN clamp:MAX] natural_start@{natural_start:.0f} → {incoming_arrangement_start:.0f}, "
            f"swap moved {bass_swap - pre_clamp_swap:+.0f} beats (overlap exceeded {MAX_OVERLAP_BEATS // 4} bars); "
            f"chop_at synced {natural_chop_at:.0f} → {chop_at:.0f}"
        )
    elif incoming_arrangement_start > latest_start:
        incoming_arrangement_start = latest_start
        incoming_grid = PhraseGrid(origin=incoming_arrangement_start)
        bass_swap = incoming_grid.snap(
            incoming_arrangement_start + incoming_bass_start + incoming_downbeat_offset_beats, log,
        )
        chop_arrangement = bass_swap
        new_chop_at = chop_arrangement - outgoing_arrangement_start
        chop_at = max(natural_chop_at, min(new_chop_at, snap(outgoing_total_beats)))
        log.append(
            f"[WARN clamp:MIN] natural_start@{natural_start:.0f} → {incoming_arrangement_start:.0f}, "
            f"swap moved {bass_swap - pre_clamp_swap:+.0f} beats (overlap below {MIN_OVERLAP_BEATS // 4} bars); "
            f"chop_at synced {natural_chop_at:.0f} → {chop_at:.0f}"
        )

    # transition_start = incoming's beat 1 (already on outgoing's phrase grid).
    # transition_end snaps to incoming's grid — the fade-out lands on a
    # phrase boundary of the new track (= the first break).
    transition_start = incoming_arrangement_start
    transition_end = incoming_grid.snap(
        incoming_arrangement_start + incoming_first_break, log,
    )

    log.append(
        f"chop@{chop_arrangement:.0f}({chop_src}={chop_at:.0f}) "
        f"swap@{bass_swap:.0f}({in_bs_src}={incoming_bass_start:.0f}) "
        f"start@{transition_start:.0f} end@{transition_end:.0f}({in_fb_src})"
    )

    # Loop region: stripped percussion from INTRO or OUTRO of the source —
    # never the middle. Sam's brief (2026-05): "loops from the front or back
    # of the track which have drums definitely, maybe bass, but are simple
    # parts." OUTRO is at/past chop_at; INTRO is the 8 beats before the
    # first drop.
    intro_end_beat = snap(outgoing_intro_end_cand.beat) if outgoing_intro_end_cand else None
    raw_loop_start, raw_loop_end, loop_src_label = find_loop_region(
        chop_at_beats=chop_at,
        total_beats=outgoing_total_beats,
        loop_beats=LOOP_BEATS,
        intro_end_beat=intro_end_beat,
        intervals=outgoing_intervals,
        mik_energy_segments=outgoing_mik_energy_segments,
        bpm=outgoing.bpm,
        first_downbeat_sec=outgoing.first_downbeat_sec or 0.0,
        audio_path=outgoing.path,
    )
    loop_source_start = snap(raw_loop_start)
    loop_source_end = snap(raw_loop_end)

    if chop_arrangement < transition_end:
        shortfall = transition_end - chop_arrangement
        loop_len = max(1.0, loop_source_end - loop_source_start)
        num_extra = max(1, int(math.ceil(shortfall / loop_len)))
    else:
        num_extra = 0

    outgoing_loop = LoopSpec(
        chop_at_beats=chop_at,
        loop_source_start=loop_source_start,
        loop_source_end=loop_source_end,
        num_extra_copies=num_extra,
    )
    log.append(
        f"loop: src[{loop_source_start:.0f}-{loop_source_end:.0f}] x{num_extra} ({loop_src_label})"
    )

    # AUTOMATION — two-phase model (Sam's correction, 2026-05).
    # Phase 1 (transition_start → bass_swap): bring incoming UP to unity,
    #   hold outgoing at unity. Incoming has its bass killed by EQ.
    # Phase 2 (bass_swap → transition_end): hard EQ swap on bass at the
    #   swap point, then slowly fade outgoing OUT across the rest of the
    #   transition. The slow fade works because the outgoing has been
    #   chopped to a stripped percussion loop — pure groove that blends
    #   into the incoming. The fade lands on incoming's first break, where
    #   the natural "breath" hides the final drop-out.
    # All breakpoints are on whole Ableton beats per Sam's hard rule.
    outgoing_volume = [
        AutomationPoint(time_beats=transition_start, value=1.0),
        AutomationPoint(time_beats=bass_swap, value=1.0),
        AutomationPoint(time_beats=transition_end, value=0.0),
    ]
    incoming_volume = [
        AutomationPoint(time_beats=transition_start, value=0.2),
        AutomationPoint(time_beats=bass_swap, value=1.0),
        AutomationPoint(time_beats=transition_end, value=1.0),
    ]
    # EQ bass swap: 1-beat ramp ending AT bass_swap (essentially instant
    # but on-grid). 1.0 = unity, 0.18 ≈ -15dB.
    eq_pre_swap = snap(bass_swap - 1)
    outgoing_eq_bass = [
        AutomationPoint(time_beats=transition_start, value=1.0),
        AutomationPoint(time_beats=eq_pre_swap, value=1.0),
        AutomationPoint(time_beats=bass_swap, value=0.18),
        AutomationPoint(time_beats=transition_end, value=0.18),
    ]
    incoming_eq_bass = [
        AutomationPoint(time_beats=transition_start, value=0.18),
        AutomationPoint(time_beats=eq_pre_swap, value=0.18),
        AutomationPoint(time_beats=bass_swap, value=1.0),
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
