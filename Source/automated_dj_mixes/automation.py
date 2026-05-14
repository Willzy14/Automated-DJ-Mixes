"""Filter automation envelopes, crossfade curves, and gain offsets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AutomationPoint:
    time_beats: float
    value: float


@dataclass
class TransitionAutomation:
    outgoing_lp_filter: list[AutomationPoint]
    outgoing_hp_filter: list[AutomationPoint]
    incoming_lp_filter: list[AutomationPoint]
    incoming_hp_filter: list[AutomationPoint]
    outgoing_volume: list[AutomationPoint]
    incoming_volume: list[AutomationPoint]


def generate_transition(
    transition_start_beats: float,
    transition_bars: int,
    filter_depth_hz: float = 200.0,
    beats_per_bar: int = 4,
) -> TransitionAutomation:
    """Generate filter + volume automation for a transition between two tracks.

    The outgoing track's LP filter sweeps down (cutting highs).
    The incoming track's HP filter starts high then opens (bass comes in at energy change).
    Volume crossfade across the full transition.
    """
    total_beats = transition_bars * beats_per_bar
    mid_beats = transition_start_beats + total_beats / 2
    end_beats = transition_start_beats + total_beats

    # Outgoing track: LP filter sweeps from 20kHz down to filter_depth_hz
    outgoing_lp = [
        AutomationPoint(transition_start_beats, 20000.0),
        AutomationPoint(end_beats, filter_depth_hz),
    ]

    # Outgoing track: HP filter stays open (20Hz)
    outgoing_hp = [
        AutomationPoint(transition_start_beats, 20.0),
        AutomationPoint(end_beats, 20.0),
    ]

    # Incoming track: HP filter starts at ~500Hz (bass cut), opens at midpoint
    incoming_hp = [
        AutomationPoint(transition_start_beats, 500.0),
        AutomationPoint(mid_beats, 500.0),
        AutomationPoint(mid_beats + 0.01, 20.0),
        AutomationPoint(end_beats, 20.0),
    ]

    # Incoming track: LP filter stays open (20kHz)
    incoming_lp = [
        AutomationPoint(transition_start_beats, 20000.0),
        AutomationPoint(end_beats, 20000.0),
    ]

    # Volume crossfade: outgoing fades out, incoming fades in
    outgoing_vol = [
        AutomationPoint(transition_start_beats, 1.0),
        AutomationPoint(end_beats, 0.0),
    ]
    incoming_vol = [
        AutomationPoint(transition_start_beats, 0.0),
        AutomationPoint(end_beats, 1.0),
    ]

    return TransitionAutomation(
        outgoing_lp_filter=outgoing_lp,
        outgoing_hp_filter=outgoing_hp,
        incoming_lp_filter=incoming_lp,
        incoming_hp_filter=incoming_hp,
        outgoing_volume=outgoing_vol,
        incoming_volume=incoming_vol,
    )


def calculate_gain_offsets(lufs_values: list[float], max_reduction_db: float = 12.0) -> list[float]:
    """Find quietest track and calculate negative gain offsets for all others.

    Returns a list of dB offsets (all <= 0). The quietest track gets 0.0.
    """
    if not lufs_values:
        return []
    quietest = min(lufs_values)
    offsets = []
    for lufs in lufs_values:
        reduction = quietest - lufs
        offsets.append(max(reduction, -max_reduction_db))
    return offsets
