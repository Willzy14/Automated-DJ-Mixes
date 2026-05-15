"""Breakdown blend — extended 48-96 bar transition for when there's lots of overlap.

Used when the available overlap is very long (48+ bars), suggesting a generous
crossover zone. Creates a "breakdown" feel: outgoing track's filter slowly cuts
highs over many bars, bass drops out, the incoming track's bass swells in,
and full energy returns by the end.
"""

from __future__ import annotations

from automated_dj_mixes.automation import AutomationPoint
from automated_dj_mixes.skills.base import (
    TransitionContext,
    TransitionPlan,
    TransitionSkill,
)


class BreakdownBlend(TransitionSkill):
    name = "breakdown_blend"

    MIN_BARS = 48
    PREFERRED_BARS = 64
    MAX_BARS = 96

    def score(self, ctx: TransitionContext) -> float:
        """High score for very long overlap windows (48+ bars)."""
        bars = ctx.available_overlap_beats / 4
        if bars < self.MIN_BARS:
            return 0.0
        if bars >= self.PREFERRED_BARS:
            return 0.9
        return 0.6 + 0.3 * (bars - self.MIN_BARS) / (self.PREFERRED_BARS - self.MIN_BARS)

    def generate(self, ctx: TransitionContext) -> TransitionPlan:
        bars = min(self.MAX_BARS, max(self.MIN_BARS, int(ctx.available_overlap_beats / 4)))
        length = bars * 4.0
        start = ctx.incoming_arrangement_start_beats
        end = start + length
        mid = start + length / 2
        quarter = start + length / 4
        three_q = start + 3 * length / 4
        # Breakdown point at 1/3 — outgoing track goes "ambient"
        breakdown = start + length / 3
        # Bass swap point: base-to-base alignment if available, else 1/3 (breakdown moment)
        swap = ctx.bass_swap_beat if ctx.bass_swap_beat is not None else breakdown
        swap = max(start + 1, min(swap, end - 1))

        # Outgoing: long slow LP sweep that nosedives into the breakdown
        outgoing_lp = [
            AutomationPoint(start, 20000.0),
            AutomationPoint(quarter, 12000.0),
            AutomationPoint(breakdown, 2000.0),
            AutomationPoint(mid, 600.0),
            AutomationPoint(end, 150.0),
        ]
        outgoing_hp = [
            AutomationPoint(start, 20.0),
            AutomationPoint(end, 20.0),
        ]
        # Outgoing volume: holds, then drops at midpoint, recovers slightly, then full fade
        outgoing_volume = [
            AutomationPoint(start, 1.0),
            AutomationPoint(breakdown, 0.95),
            AutomationPoint(mid, 0.75),
            AutomationPoint(three_q, 0.55),
            AutomationPoint(end, 0.0),
        ]
        # Outgoing bass: cuts at the swap point (base-to-base alignment)
        outgoing_eq_bass = [
            AutomationPoint(start, 1.0),
            AutomationPoint(swap - 0.01, 1.0),
            AutomationPoint(swap, 0.18),
            AutomationPoint(end, 0.18),
        ]

        # Incoming: HP filter held mid-cut, opens gradually at midpoint
        incoming_hp = [
            AutomationPoint(start, 250.0),
            AutomationPoint(quarter, 250.0),
            AutomationPoint(mid, 80.0),
            AutomationPoint(three_q, 20.0),
            AutomationPoint(end, 20.0),
        ]
        incoming_lp = [
            AutomationPoint(start, 20000.0),
            AutomationPoint(end, 20000.0),
        ]
        # Incoming volume: very subtle build — 5 stages of growth
        incoming_volume = [
            AutomationPoint(start, 0.1),
            AutomationPoint(quarter, 0.25),
            AutomationPoint(breakdown, 0.45),
            AutomationPoint(mid, 0.7),
            AutomationPoint(three_q, 0.9),
            AutomationPoint(end, 1.0),
        ]
        # Incoming bass: brought in at the swap point, matching outgoing's cut
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
