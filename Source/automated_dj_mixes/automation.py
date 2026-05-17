"""Automation primitives and gain offset calculation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AutomationPoint:
    time_beats: float
    value: float


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
