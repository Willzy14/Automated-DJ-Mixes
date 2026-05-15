"""Quick EQ bass swap — 4-8 bar transition with hard EQ low cut swap and fast volume.

Used when there's limited overlap or high-energy back-to-back mixing.
The bass swap is sharper — outgoing bass cut hard, incoming bass on hard at the swap point.
No filter sweeps — just EQ and volume.
"""

from __future__ import annotations

from automated_dj_mixes.automation import AutomationPoint
from automated_dj_mixes.skills.base import (
    TransitionContext,
    TransitionPlan,
    TransitionSkill,
)


class QuickEqSwap(TransitionSkill):
    name = "quick_eq_swap"

    MIN_BARS = 4
    PREFERRED_BARS = 8
    MAX_BARS = 16

    def score(self, ctx: TransitionContext) -> float:
        """High score for short overlap windows where a gradual blend isn't possible."""
        bars = ctx.available_overlap_beats / 4
        if bars < self.MIN_BARS:
            return 0.0
        if bars <= self.PREFERRED_BARS:
            return 0.9
        if bars <= self.MAX_BARS:
            return 0.6 - 0.3 * (bars - self.PREFERRED_BARS) / (self.MAX_BARS - self.PREFERRED_BARS)
        return 0.2

    def generate(self, ctx: TransitionContext) -> TransitionPlan:
        bars = min(self.MAX_BARS, max(self.MIN_BARS, int(ctx.available_overlap_beats / 4)))
        length = bars * 4.0
        start = ctx.incoming_arrangement_start_beats
        end = start + length
        # Bass swap: use base-to-base alignment if available, else midpoint
        swap = ctx.bass_swap_beat if ctx.bass_swap_beat is not None else (start + length / 2)
        swap = max(start + 1, min(swap, end - 1))

        # No filter sweeps — both LP and HP stay neutral
        outgoing_lp = [
            AutomationPoint(start, 20000.0),
            AutomationPoint(end, 20000.0),
        ]
        outgoing_hp = [
            AutomationPoint(start, 20.0),
            AutomationPoint(end, 20.0),
        ]
        incoming_lp = [
            AutomationPoint(start, 20000.0),
            AutomationPoint(end, 20000.0),
        ]
        incoming_hp = [
            AutomationPoint(start, 20.0),
            AutomationPoint(end, 20.0),
        ]

        # Outgoing volume: full until swap point, then quick fade
        outgoing_volume = [
            AutomationPoint(start, 1.0),
            AutomationPoint(swap, 1.0),
            AutomationPoint(end, 0.0),
        ]
        # Incoming volume: barely audible at first, then jumps in at swap point
        incoming_volume = [
            AutomationPoint(start, 0.2),
            AutomationPoint(swap - 0.01, 0.4),
            AutomationPoint(swap, 1.0),
            AutomationPoint(end, 1.0),
        ]

        # Hard bass kill swap at midpoint (Ableton LowShelfGain: 0.18=-15dB, 1.0=unity)
        outgoing_eq_bass = [
            AutomationPoint(start, 1.0),
            AutomationPoint(swap - 0.01, 1.0),
            AutomationPoint(swap, 0.18),
            AutomationPoint(end, 0.18),
        ]
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
