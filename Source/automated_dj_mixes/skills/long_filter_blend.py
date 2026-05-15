"""Long filter blend — 24-32 bar transition with LP filter sweep and gradual volume crossfade.

The default transition for most blends. The outgoing track's high frequencies
fade out via low-pass filter while the incoming track creeps in subtly, then opens up.
Bass swap happens at midpoint via EQ low cut.
"""

from __future__ import annotations

from automated_dj_mixes.automation import AutomationPoint
from automated_dj_mixes.skills.base import (
    TransitionContext,
    TransitionPlan,
    TransitionSkill,
)


class LongFilterBlend(TransitionSkill):
    name = "long_filter_blend"

    MIN_BARS = 16
    PREFERRED_BARS = 32
    MAX_BARS = 48

    def score(self, ctx: TransitionContext) -> float:
        """Mid-range fallback when no specialist skill claims the transition."""
        bars = ctx.available_overlap_beats / 4
        if bars < self.MIN_BARS:
            return 0.0
        if bars >= self.PREFERRED_BARS:
            return 0.5
        return 0.3 + 0.2 * (bars - self.MIN_BARS) / (self.PREFERRED_BARS - self.MIN_BARS)

    def generate(self, ctx: TransitionContext) -> TransitionPlan:
        bars = min(self.MAX_BARS, max(self.MIN_BARS, int(ctx.available_overlap_beats / 4)))
        length = bars * 4.0
        start = ctx.incoming_arrangement_start_beats
        end = start + length
        mid = start + length / 2
        quarter = start + length / 4
        three_q = start + 3 * length / 4
        # Bass swap point: use base-to-base alignment if available, else midpoint
        swap = ctx.bass_swap_beat if ctx.bass_swap_beat is not None else mid
        # Clamp the swap to the transition window
        swap = max(start + 1, min(swap, end - 1))

        # Outgoing: LP sweeps from open (20kHz) down to bass-only (200Hz) over the transition
        outgoing_lp = [
            AutomationPoint(start, 20000.0),
            AutomationPoint(mid, 4000.0),
            AutomationPoint(end, 200.0),
        ]
        # HP stays open on outgoing
        outgoing_hp = [
            AutomationPoint(start, 20.0),
            AutomationPoint(end, 20.0),
        ]
        # Outgoing volume: stays present until ~3/4 in, then fades
        outgoing_volume = [
            AutomationPoint(start, 1.0),
            AutomationPoint(mid, 0.95),
            AutomationPoint(three_q, 0.6),
            AutomationPoint(end, 0.0),
        ]
        # Outgoing bass: unity until the swap point, then hard cut to 0.18 (-15dB)
        outgoing_eq_bass = [
            AutomationPoint(start, 1.0),
            AutomationPoint(swap - 0.01, 1.0),
            AutomationPoint(swap, 0.18),
            AutomationPoint(end, 0.18),
        ]

        # Incoming HP: held high to keep lows out, opens at the bass swap point
        incoming_hp = [
            AutomationPoint(start, 300.0),
            AutomationPoint(swap - 0.01, 300.0),
            AutomationPoint(swap, 20.0),
            AutomationPoint(end, 20.0),
        ]
        # LP stays open on incoming
        incoming_lp = [
            AutomationPoint(start, 20000.0),
            AutomationPoint(end, 20000.0),
        ]
        # Incoming volume: starts very low (barely audible), creeps in
        incoming_volume = [
            AutomationPoint(start, 0.15),
            AutomationPoint(quarter, 0.3),
            AutomationPoint(mid, 0.7),
            AutomationPoint(end, 1.0),
        ]
        # Incoming bass: cut (0.18) until the swap point, then unity (1.0)
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
