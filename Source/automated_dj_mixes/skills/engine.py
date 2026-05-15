"""Skills engine — picks the right skill per transition based on context."""

from __future__ import annotations

from automated_dj_mixes.skills.base import (
    TransitionContext,
    TransitionPlan,
    TransitionSkill,
)


class SkillsEngine:
    """Picks the highest-scoring skill for each transition opportunity."""

    def __init__(self, skills: list[TransitionSkill]):
        if not skills:
            raise ValueError("SkillsEngine requires at least one skill")
        self.skills = skills

    def pick_skill(self, ctx: TransitionContext) -> TransitionSkill:
        """Return the skill with the highest score for this context."""
        scored = [(s.score(ctx), s) for s in self.skills]
        scored.sort(key=lambda x: -x[0])
        return scored[0][1]

    def plan_transition(self, ctx: TransitionContext) -> TransitionPlan:
        """Pick a skill and generate the transition plan."""
        skill = self.pick_skill(ctx)
        return skill.generate(ctx)
