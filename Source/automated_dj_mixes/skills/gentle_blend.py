"""Gentle blend — long, smooth transition between BPM-close tracks.

For when two tracks are within 2 BPM of each other: no EQ swap (the kicks
already match), just a smooth filter sweep and gradual volume creep-in.
This is the kind of seamless mix where you can't quite tell when the new
track came in.
"""

from __future__ import annotations

from automated_dj_mixes.automation import AutomationPoint
from automated_dj_mixes.skills.base import (
    TransitionContext,
    TransitionPlan,
    TransitionSkill,
)


class GentleBlend(TransitionSkill):
    name = "gentle_blend"

    MIN_BARS = 24
    PREFERRED_BARS = 32
    MAX_BARS = 64

    def score(self, ctx: TransitionContext) -> float:
        """High score when BPMs are very close (within 2 BPM) and there's enough overlap."""
        bars = ctx.available_overlap_beats / 4
        if bars < self.MIN_BARS:
            return 0.0
        bpm_diff = abs(ctx.outgoing.bpm - ctx.incoming.bpm)
        if bpm_diff > 2.0:
            return 0.0
        # Stronger preference for very-close BPMs
        bpm_score = 1.0 - (bpm_diff / 2.0) * 0.3
        bar_score = min(1.0, bars / self.PREFERRED_BARS)
        return 0.85 * bpm_score * bar_score

    def generate(self, ctx: TransitionContext) -> TransitionPlan:
        bars = min(self.MAX_BARS, max(self.MIN_BARS, int(ctx.available_overlap_beats / 4)))
        length = bars * 4.0
        start = ctx.incoming_arrangement_start_beats
        end = start + length
        mid = start + length / 2
        quarter = start + length / 4
        three_q = start + 3 * length / 4
        # Bass swap point: base-to-base alignment if available, else 3/4 point
        swap = ctx.bass_swap_beat if ctx.bass_swap_beat is not None else three_q
        swap = max(start + 1, min(swap, end - 1))

        # Outgoing: very gentle LP sweep — only cuts highs in the last third
        outgoing_lp = [
            AutomationPoint(start, 20000.0),
            AutomationPoint(three_q, 20000.0),
            AutomationPoint(end, 1500.0),
        ]
        outgoing_hp = [
            AutomationPoint(start, 20.0),
            AutomationPoint(end, 20.0),
        ]
        # Outgoing volume: holds full for the first half, then long fade
        outgoing_volume = [
            AutomationPoint(start, 1.0),
            AutomationPoint(mid, 1.0),
            AutomationPoint(three_q, 0.6),
            AutomationPoint(end, 0.0),
        ]
        # Outgoing bass: stays unity until just before the swap, then fades
        outgoing_eq_bass = [
            AutomationPoint(start, 1.0),
            AutomationPoint(swap - 0.01, 1.0),
            AutomationPoint(end, 0.18),
        ]

        # Incoming: no HP cut needed (BPMs match, kicks line up)
        incoming_hp = [
            AutomationPoint(start, 20.0),
            AutomationPoint(end, 20.0),
        ]
        incoming_lp = [
            AutomationPoint(start, 20000.0),
            AutomationPoint(end, 20000.0),
        ]
        # Incoming volume: very subtle creep, only reaches full near the end
        incoming_volume = [
            AutomationPoint(start, 0.1),
            AutomationPoint(quarter, 0.2),
            AutomationPoint(mid, 0.5),
            AutomationPoint(three_q, 0.85),
            AutomationPoint(end, 1.0),
        ]
        # Incoming bass: held back until the swap point so kicks layer cleanly
        incoming_eq_bass = [
            AutomationPoint(start, 0.18),
            AutomationPoint(swap - 0.01, 0.18),
            AutomationPoint(end, 1.0),
        ]

        return TransitionPlan(
            skill_name=self.name,
            transition_start_beats=start,
            transition_length_beats=length,
            outgoing_lp=outgoing_lp,
            outgoing_hp=outgoing_hp,
            outgoing_volume=outgoing_volume,
            outgoing_eq_bass=outgoing_eq_bass,
            incoming_lp=incoming_lp,
            incoming_hp=incoming_hp,
            incoming_volume=incoming_volume,
            incoming_eq_bass=incoming_eq_bass,
        )
