"""Objective mix validation — pass/fail checks before Sam listens.

Validates from the internal mix plan (the TransitionSpec list + patches), NOT
by reparsing the generated ALS XML. ALS parsing is fragile and the plan is the
source of truth for what we INTENDED to write.

ANALYSIS_MODEL_VERSION = "cue-candidates-v1"

Bar-aligned: 8-bar / 16-bar boundary checks assume the first downbeat is at
arrangement beat 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from automated_dj_mixes.features import ANALYSIS_MODEL_VERSION
from automated_dj_mixes.transition import TransitionSpec


@dataclass
class ValidationCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ValidationReport:
    mix_version: int
    checks: list[ValidationCheck] = field(default_factory=list)
    analysis_model_version: str = ANALYSIS_MODEL_VERSION

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def summary(self) -> str:
        lines = [f"Mix V{self.mix_version} validation ({self.analysis_model_version}):"]
        for c in self.checks:
            mark = "PASS" if c.passed else "FAIL"
            lines.append(f"  [{mark}] {c.name}: {c.detail}")
        lines.append(f"Result: {'ALL PASS' if self.all_passed else 'FAIL'}")
        return "\n".join(lines)


def _on_boundary(beat: float, boundary_beats: int) -> bool:
    """Is `beat` on an N-bar boundary (multiple of boundary_beats)?"""
    return abs(beat - round(beat / boundary_beats) * boundary_beats) < 0.5


def validate_mix(
    mix_version: int,
    transition_specs: list[TransitionSpec],
    track_total_beats: list[float],
    arrangement_positions: list[int],
) -> ValidationReport:
    """Run pass/fail checks on the planned mix.

    Args:
      mix_version: integer version (for the report title).
      transition_specs: one TransitionSpec per pair (len = tracks - 1).
      track_total_beats: total beats per track (warp-marker derived).
      arrangement_positions: arrangement start beat per track.
    """
    checks: list[ValidationCheck] = []

    # 1. All overlaps in 16-80 bars range (Sam's actual mixes 28-56 bars per
    #    Bargrooves analysis 2026-05-19; previous 48 cap was too tight).
    #    1.5-bar tolerance for phrase-snap drift.
    #    (Comment originally said "16-48 bars" — updated 2026-05-19)
    # rounding; the snap can shift incoming_arrangement_start by up to 8
    # beats = 2 bars, so the actual overlap can drift accordingly).
    overlap_ok = True
    overlap_details = []
    for i, spec in enumerate(transition_specs):
        outgoing_end = arrangement_positions[i] + track_total_beats[i]
        overlap_beats = outgoing_end - spec.transition_start
        overlap_bars = overlap_beats / 4
        ok = 14.5 <= overlap_bars <= 80.5
        if not ok:
            overlap_ok = False
            overlap_details.append(f"transition {i + 1}: {overlap_bars:.1f} bars")
    checks.append(ValidationCheck(
        name="overlap in 16-80 bars",
        passed=overlap_ok,
        detail="all transitions in range" if overlap_ok else f"out of range: {'; '.join(overlap_details)}",
    ))

    # 2. Per-track phrase-grid alignment (Sam's rule, 2026-05):
    #    "It should be per track starting with the first beat of each track."
    # Each track has its own phrase grid, with origin = that track's
    # arrangement_start. bass_swap must be on the INCOMING's phrase grid
    # (= multiples of 16 from incoming_arrangement_start), because the
    # listener perceives the new track's phrase structure forward from the
    # swap point. Same logic for transition_end which is also relative to
    # the incoming.
    def _offset_on_grid(beat: float, origin: float, grid: int) -> bool:
        offset = beat - origin
        return abs(offset - round(offset / grid) * grid) < 0.5

    bar_ok = True
    bar_details = []
    phrase_details = []
    phrase_ok = True
    for i, spec in enumerate(transition_specs):
        # Incoming arrangement start is the origin for incoming's phrase grid.
        incoming_origin = spec.transition_start
        # Outgoing arrangement start is the origin for outgoing's grid.
        outgoing_origin = arrangement_positions[i]

        # bass_swap and transition_end: relative to incoming track.
        for label, beat in (
            ("bass_swap", spec.bass_swap),
            ("transition_end", spec.transition_end),
        ):
            if not _offset_on_grid(beat, incoming_origin, 4):
                bar_ok = False
                bar_details.append(
                    f"T{i + 1} {label}={beat:.1f} (incoming start={incoming_origin})"
                )
            elif not _offset_on_grid(beat, incoming_origin, 16):
                phrase_ok = False
                phrase_details.append(f"T{i + 1} {label}={beat:.0f}")

        # transition_start (= incoming_arrangement_start) must align to
        # outgoing's grid so the two tracks' grids match.
        if not _offset_on_grid(spec.transition_start, outgoing_origin, 4):
            bar_ok = False
            bar_details.append(
                f"T{i + 1} transition_start={spec.transition_start:.1f} (outgoing start={outgoing_origin})"
            )
        elif not _offset_on_grid(spec.transition_start, outgoing_origin, 16):
            phrase_ok = False
            phrase_details.append(f"T{i + 1} transition_start={spec.transition_start:.0f}")

    checks.append(ValidationCheck(
        name="all transition breakpoints on bar boundary (HARD, per-track)",
        passed=bar_ok,
        detail="all on per-track bar grid" if bar_ok else f"OFF-BAR: {'; '.join(bar_details)}",
    ))
    checks.append(ValidationCheck(
        name="all transition breakpoints on 16-beat phrase boundary (HARD, per-track)",
        passed=phrase_ok,
        detail="all on per-track phrase grid" if phrase_ok else f"OFF-PHRASE: {'; '.join(phrase_details)}",
    ))

    # 3. Outgoing fully gone by transition_end (volume ends at 0)
    fade_ok = True
    fade_details = []
    for i, spec in enumerate(transition_specs):
        last_volume = spec.outgoing_volume[-1] if spec.outgoing_volume else None
        if not last_volume or last_volume.value > 0.05:
            fade_ok = False
            fade_details.append(f"transition {i + 1}: ends at {last_volume.value if last_volume else 'no points'}")
    checks.append(ValidationCheck(
        name="outgoing fully gone by transition_end",
        passed=fade_ok,
        detail="all faded to 0" if fade_ok else f"residual: {'; '.join(fade_details)}",
    ))

    # 4. No dead air — transition_start <= outgoing_end (outgoing must still be playing)
    dead_air_ok = True
    dead_details = []
    for i, spec in enumerate(transition_specs):
        outgoing_end = arrangement_positions[i] + track_total_beats[i]
        if spec.transition_start > outgoing_end:
            dead_air_ok = False
            dead_details.append(
                f"transition {i + 1}: gap of {spec.transition_start - outgoing_end:.0f} beats"
            )
    checks.append(ValidationCheck(
        name="no dead air before incoming",
        passed=dead_air_ok,
        detail="continuous" if dead_air_ok else f"gaps: {'; '.join(dead_details)}",
    ))

    # 5. Pre-swap bass overlap controlled (incoming bass enters after, not before, swap)
    bass_overlap_ok = True
    bass_overlap_details = []
    for i, spec in enumerate(transition_specs):
        # Incoming bass entry == bass_swap by design. If they drift apart, flag it.
        # The pre-swap region should have outgoing bass full + incoming bass cut.
        # This is enforced by automation; we just check the values exist.
        if not spec.outgoing_eq_bass or not spec.incoming_eq_bass:
            bass_overlap_ok = False
            bass_overlap_details.append(f"transition {i + 1}: missing EQ automation")
    checks.append(ValidationCheck(
        name="bass swap automation present",
        passed=bass_overlap_ok,
        detail="all transitions have outgoing+incoming EQ envelopes" if bass_overlap_ok else "; ".join(bass_overlap_details),
    ))

    return ValidationReport(mix_version=mix_version, checks=checks)
