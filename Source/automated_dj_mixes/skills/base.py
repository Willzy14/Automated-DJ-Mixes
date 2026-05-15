"""Base classes and data structures for DJ mixing skills."""

from __future__ import annotations

from dataclasses import dataclass, field

from automated_dj_mixes.analysis import TrackAnalysis
from automated_dj_mixes.automation import AutomationPoint


@dataclass
class TransitionContext:
    """Everything a skill needs to decide if it applies and how to plan a transition."""

    outgoing: TrackAnalysis
    incoming: TrackAnalysis
    outgoing_arrangement_start_beats: float
    outgoing_arrangement_end_beats: float
    incoming_arrangement_start_beats: float
    available_overlap_beats: float
    project_bpm: float
    # Phase 5: where the bass swap should happen (outgoing's bass ends and
    # incoming's bass enters in arrangement time). None if bass detection
    # missing — skill falls back to midpoint timing.
    bass_swap_beat: float | None = None


@dataclass
class TransitionPlan:
    """Complete transition specification produced by a skill.

    All automation point times are in absolute arrangement beats.
    Filter frequencies in Hz (20-20000). Volume and EQ bass in normalized 0-1
    (where 1.0 = unity for EQ, full level for volume).
    """

    skill_name: str
    transition_start_beats: float
    transition_length_beats: float

    outgoing_lp: list[AutomationPoint] = field(default_factory=list)
    outgoing_hp: list[AutomationPoint] = field(default_factory=list)
    outgoing_volume: list[AutomationPoint] = field(default_factory=list)
    outgoing_eq_bass: list[AutomationPoint] = field(default_factory=list)

    incoming_lp: list[AutomationPoint] = field(default_factory=list)
    incoming_hp: list[AutomationPoint] = field(default_factory=list)
    incoming_volume: list[AutomationPoint] = field(default_factory=list)
    incoming_eq_bass: list[AutomationPoint] = field(default_factory=list)


class TransitionSkill:
    """Base class for transition skills. Subclasses implement score() and generate()."""

    name: str = "base"

    def score(self, ctx: TransitionContext) -> float:
        """Return a confidence 0.0-1.0 that this skill should handle the transition.

        Higher scores win. Return 0.0 if this skill cannot/should not apply.
        """
        raise NotImplementedError

    def generate(self, ctx: TransitionContext) -> TransitionPlan:
        """Generate the transition plan."""
        raise NotImplementedError
