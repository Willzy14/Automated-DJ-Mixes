"""Energetic punch swap — punchy 16-24 bar transition with hard EQ swap and quick filter dip.

For when BPMs differ significantly (4+ BPM) and you want a deliberate, energetic
swap rather than a smooth blend. The outgoing track's bass kills out at the
midpoint while the incoming track's bass kicks in — creating a clear "drop"
moment that the dancefloor can feel.
"""

from __future__ import annotations

from automated_dj_mixes.automation import AutomationPoint
from automated_dj_mixes.skills.base import (
    TransitionContext,
    TransitionPlan,
    TransitionSkill,
)


class EnergeticPunchSwap(TransitionSkill):
    name = "energetic_punch_swap"

    MIN_BARS = 12
    PREFERRED_BARS = 16
    MAX_BARS = 24

    def score(self, ctx: TransitionContext) -> float:
        """High score when BPMs differ significantly — a clean swap beats a long stretchy blend."""
        bars = ctx.available_overlap_beats / 4
        if bars < self.MIN_BARS:
            return 0.0
        bpm_diff = abs(ctx.outgoing.bpm - ctx.incoming.bpm)
        if bpm_diff < 3.0:
            return 0.0
        # Strong preference for BPM-far tracks
        bpm_score = min(1.0, (bpm_diff - 3.0) / 4.0 + 0.4)
        return 0.7 * bpm_score

    def generate(self, ctx: TransitionContext) -> TransitionPlan:
        bars = min(self.MAX_BARS, max(self.MIN_BARS, int(ctx.available_overlap_beats / 4)))
        length = bars * 4.0
        start = ctx.incoming_arrangement_start_beats
        end = start + length
        swap = ctx.bass_swap_beat if ctx.bass_swap_beat is not None else (start + length / 2)
        swap = max(start + 1, min(swap, end - 1))
        three_q = start + 3 * length / 4

        # Outgoing: quick LP dip just before the swap — creates anticipation
        outgoing_lp = [
            AutomationPoint(start, 20000.0),
            AutomationPoint(swap - 1, 8000.0),
            AutomationPoint(swap, 400.0),
            AutomationPoint(end, 200.0),
        ]
        outgoing_hp = [
            AutomationPoint(start, 20.0),
            AutomationPoint(end, 20.0),
        ]
        # Outgoing volume: holds full until 3/4, then quick fade
        outgoing_volume = [
            AutomationPoint(start, 1.0),
            AutomationPoint(three_q, 0.9),
            AutomationPoint(end, 0.0),
        ]
        # Outgoing bass: hard kill exactly at swap
        outgoing_eq_bass = [
            AutomationPoint(start, 1.0),
            AutomationPoint(swap - 0.01, 1.0),
            AutomationPoint(swap, 0.18),
            AutomationPoint(end, 0.18),
        ]

        # Incoming: HP opens hard at the swap
        incoming_hp = [
            AutomationPoint(start, 400.0),
            AutomationPoint(swap - 0.01, 400.0),
            AutomationPoint(swap, 20.0),
            AutomationPoint(end, 20.0),
        ]
        incoming_lp = [
            AutomationPoint(start, 20000.0),
            AutomationPoint(end, 20000.0),
        ]
        # Incoming volume: barely audible until swap, then loud
        incoming_volume = [
            AutomationPoint(start, 0.15),
            AutomationPoint(swap - 0.01, 0.4),
            AutomationPoint(swap, 0.85),
            AutomationPoint(end, 1.0),
        ]
        # Incoming bass: hard ON at swap
        incoming_eq_bass = [
            AutomationPoint(start, 0.18),
            AutomationPoint(swap - 0.01, 0.18),
            AutomationPoint(swap, 1.0),
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
