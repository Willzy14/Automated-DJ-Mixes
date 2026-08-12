"""Single source of truth for transition geometry policy.

Before this module the overlap caps were declared three separate times -
`align_engine` in bars, `propose_arrangement` in beats and `mix_plan` in beats -
and the loop-extension budget was *derived* as `MAX_OVERLAP_BARS - PHRASE_GRID`.
Raising a cap therefore silently raised the loop budget too, and raising one copy
without the others produced a fail-closed rejection in a different module with a
misleading message.

Policy also travels as an explicit frozen object rather than as mutated module
constants. That matters for the A/B experiment: two policies must be runnable in
one Python process without the first leaking into the second, which module-level
rebinding cannot guarantee.

The numbers below are exactly the values the three modules held before the
consolidation (48 bars = 192 beats, 64 bars = 256 beats, 32 bars = 128 beats), so
adopting this module is behaviour-neutral.
"""

from __future__ import annotations

from dataclasses import dataclass

BEATS_PER_BAR = 4.0


def bars_to_beats(bars: float) -> float:
    return bars * BEATS_PER_BAR


def beats_to_bars(beats: float) -> float:
    return beats / BEATS_PER_BAR


@dataclass(frozen=True)
class TransitionPolicy:
    """Frozen geometry contract for one arrangement run.

    Pass this explicitly through alignment, loop planning, arrangement
    validation, report generation, automation and reconciliation. Never read a
    cap from a module global once a policy is in scope.
    """

    name: str
    min_overlap_beats: float
    max_overlap_beats: float
    max_landmark_overlap_beats: float
    max_loop_extension_beats: float
    max_loop_repeats: int

    # Extended lane. None disables it outright; a value is only an upper bound -
    # authorisation still requires the evidence contract to be satisfied and
    # independently revalidated. A policy string alone must never unlock it.
    max_extended_overlap_beats: float | None = None

    def cap_for(self, overlap_policy: str) -> float:
        """Beat ceiling for one transition's declared overlap policy."""
        if overlap_policy == "evidence_extended_80":
            if self.max_extended_overlap_beats is None:
                raise ValueError(
                    f"policy '{self.name}' does not permit the extended lane; "
                    f"transition declared '{overlap_policy}'"
                )
            return self.max_extended_overlap_beats
        if overlap_policy == "named_landmark_64":
            return self.max_landmark_overlap_beats
        if overlap_policy == "standard_48":
            return self.max_overlap_beats
        raise ValueError(f"unknown overlap policy: {overlap_policy!r}")

    @property
    def allowed_overlap_policies(self) -> tuple[str, ...]:
        base = ("standard_48", "named_landmark_64")
        if self.max_extended_overlap_beats is None:
            return base
        return base + ("evidence_extended_80",)


#: Production default. Identical to the pre-consolidation constants.
INTERIM_V1 = TransitionPolicy(
    name="interim_v1",
    min_overlap_beats=64.0,          # 16 bars - minimum for a usable transition
    max_overlap_beats=192.0,         # 48 bars
    max_landmark_overlap_beats=256.0,  # 64 bars, only with a named-cue extension
    max_loop_extension_beats=128.0,  # 32 bars
    max_loop_repeats=8,
    max_extended_overlap_beats=None,  # extended lane unavailable
)

#: Experimental lane under test against the Sam-Tweaks corrections. Not a
#: production default, and commissioned delivery stays on INTERIM_V1 until this
#: has passed a held-out blind listen. The 320-beat ceiling exists because
#: exactly one measured correction (T3, 295.29 beats) exceeded the 256-beat
#: landmark cap.
SAM_V1 = TransitionPolicy(
    name="sam_v1",
    min_overlap_beats=64.0,
    max_overlap_beats=192.0,
    max_landmark_overlap_beats=256.0,
    max_loop_extension_beats=128.0,
    max_loop_repeats=8,
    max_extended_overlap_beats=320.0,  # 80 bars
)

POLICIES: dict[str, TransitionPolicy] = {
    INTERIM_V1.name: INTERIM_V1,
    SAM_V1.name: SAM_V1,
}


def get_policy(name: str) -> TransitionPolicy:
    try:
        return POLICIES[name]
    except KeyError:
        known = ", ".join(sorted(POLICIES))
        raise ValueError(
            f"unknown transition policy {name!r} (known: {known})"
        ) from None
