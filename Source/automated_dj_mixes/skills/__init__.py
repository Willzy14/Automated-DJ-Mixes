"""Modular DJ mixing skills — each technique knows when and how to apply itself.

The SkillsEngine picks the best skill per transition based on track analysis,
available overlap, and musical context. Each skill produces a TransitionPlan
with automation envelopes and clip modifications.
"""

from automated_dj_mixes.skills.base import (
    TransitionContext,
    TransitionPlan,
    TransitionSkill,
)
from automated_dj_mixes.skills.engine import SkillsEngine
from automated_dj_mixes.skills.long_filter_blend import LongFilterBlend
from automated_dj_mixes.skills.quick_eq_swap import QuickEqSwap
from automated_dj_mixes.skills.gentle_blend import GentleBlend
from automated_dj_mixes.skills.energetic_punch_swap import EnergeticPunchSwap
from automated_dj_mixes.skills.breakdown_blend import BreakdownBlend

DEFAULT_SKILLS = [
    QuickEqSwap(),
    EnergeticPunchSwap(),
    GentleBlend(),
    BreakdownBlend(),
    LongFilterBlend(),
]

__all__ = [
    "TransitionContext",
    "TransitionPlan",
    "TransitionSkill",
    "SkillsEngine",
    "LongFilterBlend",
    "QuickEqSwap",
    "GentleBlend",
    "EnergeticPunchSwap",
    "BreakdownBlend",
    "DEFAULT_SKILLS",
]
