"""Filter automation envelopes, crossfade curves, and gain offsets."""

from dataclasses import dataclass


@dataclass
class AutomationPoint:
    time_beats: float
    value: float


@dataclass
class TransitionAutomation:
    outgoing_filter: list[AutomationPoint]
    incoming_filter: list[AutomationPoint]
    crossfade: list[AutomationPoint]


def generate_transition(transition_bars: int, filter_depth_db: float) -> TransitionAutomation:
    """Generate filter + crossfade automation for a transition between two tracks."""
    raise NotImplementedError


def calculate_gain_offsets(lufs_values: list[float]) -> list[float]:
    """Find quietest track and calculate negative gain offsets for all others."""
    raise NotImplementedError
