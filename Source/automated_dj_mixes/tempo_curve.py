"""A smoothed tempo arc through a mix, rather than one frozen project BPM.

Sam's model (2026-08-13), in his words: tempo changes happen ACROSS a transition
so the move is hidden under two tracks playing together; they must be gradual
enough that you never hear the speed change; and the curve is an ARC through the
mix rather than a chase of every track.

Crucially it does NOT follow each track's native tempo. Outliers are absorbed,
not chased:

    "if you've got five tracks that are 125, and in the middle of them is a 130,
     you'd maybe travel up to 126, 127 whilst that track was playing, so you
     wouldn't reach 130, but you'd go as high as possible whilst keeping it
     within reason for the rest of the mix"

So the outlier takes the stretch and the rest of the mix stays comfortable. That
is the opposite of a naive follow, which would drag the whole mix toward the odd
track.

Why this exists at all: the previous rule froze ONE tempo, the mode of the
rounded BPMs. On the 12.08.26 mix (118, 118, 120, 121, 122, 123.2, 124, 125)
that chose 118 - the SLOWEST track - so everything else was stretched down by up
to -5.6%, which Sam heard.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Pull toward a smooth arc versus each track's native tempo. Calibrated on
#: Sam's worked example: 125,125,130,125,125 must peak at 126-127, never 130.
DEFAULT_SMOOTHING = 2.0

#: Never move more than this between adjacent tracks. A hard ceiling on top of
#: the smoothing, so one huge outlier cannot produce an audible lurch.
DEFAULT_MAX_STEP_BPM = 1.5


@dataclass(frozen=True)
class TempoPoint:
    beat: float
    bpm: float


def solve_track_tempos(native_bpms, smoothing: float = DEFAULT_SMOOTHING,
                       max_step_bpm: float = DEFAULT_MAX_STEP_BPM) -> list[float]:
    """Per-track held tempos: a compromise between native tempo and a smooth arc.

    Minimises  sum (T_i - native_i)^2  +  smoothing * sum (T_i - T_(i-1))^2,
    which has a closed-form tridiagonal solution, then clamps any remaining step
    to `max_step_bpm`. Raising `smoothing` flattens the arc and pushes more of
    the compromise onto outliers, which is exactly Sam's preference.
    """
    n = np.asarray(native_bpms, dtype=float)
    if len(n) == 0:
        return []
    if len(n) == 1:
        return [float(n[0])]

    size = len(n)
    a = np.eye(size)
    for i in range(size):                       # second-difference smoothing
        if i > 0:
            a[i, i] += smoothing
            a[i, i - 1] -= smoothing
        if i < size - 1:
            a[i, i] += smoothing
            a[i, i + 1] -= smoothing
    solved = np.linalg.solve(a, n)

    # Clamp residual steps, sweeping both ways so the limit holds everywhere.
    for _ in range(size):
        changed = False
        for i in range(1, size):
            step = solved[i] - solved[i - 1]
            if abs(step) > max_step_bpm:
                mid = (solved[i] + solved[i - 1]) / 2.0
                half = max_step_bpm / 2.0
                solved[i - 1] = mid - half * np.sign(step)
                solved[i] = mid + half * np.sign(step)
                changed = True
        if not changed:
            break
    return [float(x) for x in solved]


#: How much of a ramp may play with only ONE track audible before it is worth
#: reporting. Sam's ruling (2026-08-13): "if there's a small section where the
#: tempo is still moving but the tail end of a mix has faded out, it's not the
#: end of the world" - so this warns rather than refuses, unlike the reviewer's
#: proposal to reject any ramp without continuous two-track coverage.
DEFAULT_EXPOSURE_TOLERANCE = 0.25


def _covered_fraction(start: float, end: float, intervals) -> float:
    """Fraction of [start, end) covered by `intervals` (list of (a, b))."""
    span = end - start
    if span <= 0:
        return 1.0
    covered = 0.0
    for a, b in sorted(intervals):
        lo, hi = max(a, start), min(b, end)
        if hi > lo:
            covered += hi - lo
    return min(1.0, covered / span)


def ramp_exposure(ramp_start: float, ramp_end: float,
                  out_intervals, in_intervals) -> float:
    """Fraction of a ramp where the two tracks are NOT both playing.

    Overlap bounds alone do not prove continuous two-track audio: a clip gap or
    a dropout inside the window leaves tempo moving with one track exposed,
    which is the thing that makes a tempo change audible. Pass each track's
    actual clip intervals to measure it honestly.
    """
    both = min(_covered_fraction(ramp_start, ramp_end, out_intervals),
               _covered_fraction(ramp_start, ramp_end, in_intervals))
    return max(0.0, 1.0 - both)


def build_tempo_points(track_tempos, transitions) -> list[TempoPoint]:
    """Hold each track's tempo, ramp across each transition.

    `transitions` is [(overlap_start_beat, overlap_end_beat), ...], one per
    adjacent pair. The ramp spans the whole overlap because that is the window
    where two tracks mask the move - Sam's "do them across a transition of two
    tracks to hide the fact that it's happening" - and because a long overlap is
    what makes the change inaudible.
    """
    if not track_tempos:
        return []

    # Fail loudly rather than truncate. The previous version silently dropped
    # mismatched transitions and could emit non-monotonic points when adjacent
    # overlaps intersected - a malformed curve that would still have been
    # written to the ALS and frozen into the contract.
    transitions = [(float(a), float(b)) for a, b in transitions]
    expected = len(track_tempos) - 1
    if len(transitions) != expected:
        raise ValueError(
            f"{len(track_tempos)} tracks need exactly {expected} transitions, "
            f"got {len(transitions)}")
    for bpm in track_tempos:
        if not (isinstance(bpm, (int, float)) and 20.0 < float(bpm) < 300.0):
            raise ValueError(f"implausible held tempo: {bpm!r}")
    previous_end = None
    for index, (start, end) in enumerate(transitions):
        if not (start < end):
            raise ValueError(
                f"transition {index}: start {start} must precede end {end}")
        if previous_end is not None and start < previous_end:
            raise ValueError(
                f"transition {index} starts at {start} before the previous ramp "
                f"ends at {previous_end}; overlapping ramps (triple overlap) are "
                "not supported")
        previous_end = end

    points = [TempoPoint(0.0, track_tempos[0])]
    for i, (start, end) in enumerate(transitions):
        points.append(TempoPoint(start, track_tempos[i]))
        points.append(TempoPoint(end, track_tempos[i + 1]))

    times = [p.beat for p in points]
    if any(b < a for a, b in zip(times, times[1:])):
        raise ValueError(f"tempo points are not monotonic in time: {times}")
    return points


def max_stretch_percent(native_bpms, track_tempos) -> list[float]:
    """Signed stretch each track suffers at its held tempo. Negative = slowed."""
    return [(t / n - 1.0) * 100.0 for n, t in zip(native_bpms, track_tempos)]


def slowest_ramp_bpm_per_bar(points) -> float:
    """Steepest tempo slope in the curve, BPM per bar - the audibility proxy."""
    worst = 0.0
    for a, b in zip(points, points[1:]):
        bars = (b.beat - a.beat) / 4.0
        if bars > 0:
            worst = max(worst, abs(b.bpm - a.bpm) / bars)
    return worst


#: A track stretched beyond this is worth trying to reposition before accepting
#: the compromise. Sam tolerates a small pitch glide through a transition but
#: not a track carrying a heavy stretch all the way through.
DEFAULT_STRETCH_TOLERANCE_PCT = 2.0


def tempo_cost(native_bpms, **kw) -> float:
    """Worst-case stretch for one candidate running order. Lower is better."""
    if len(native_bpms) < 2:
        return 0.0
    tempos = solve_track_tempos(native_bpms, **kw)
    return max(abs(s) for s in max_stretch_percent(native_bpms, tempos))


def outliers(native_bpms, tolerance_pct: float = DEFAULT_STRETCH_TOLERANCE_PCT,
             **kw) -> list[int]:
    """Positions whose track is taking more stretch than we would like."""
    if len(native_bpms) < 2:
        return []
    tempos = solve_track_tempos(native_bpms, **kw)
    stretch = max_stretch_percent(native_bpms, tempos)
    return [i for i, s in enumerate(stretch) if abs(s) > tolerance_pct]


def best_position_for(native_bpms, index: int, **kw) -> tuple[int, float]:
    """Where this track would suffer least, and the resulting worst-case cost.

    Sam's point: a track is only an outlier RELATIVE TO WHERE IT SITS. A 130 in
    the middle of a 125 mix is an outlier, but if the mix climbs to 128 by the
    end then the same track is comfortable at the end. Repositioning and the
    smoothed arc compose - do both, rather than choosing.
    """
    rest = [b for i, b in enumerate(native_bpms) if i != index]
    track = native_bpms[index]
    best, best_cost = index, float("inf")
    for pos in range(len(rest) + 1):
        cost = tempo_cost(rest[:pos] + [track] + rest[pos:], **kw)
        if cost < best_cost - 1e-9:
            best, best_cost = pos, cost
    return best, best_cost


def suggest_resequence(native_bpms,
                       tolerance_pct: float = DEFAULT_STRETCH_TOLERANCE_PCT,
                       **kw) -> list[int] | None:
    """A running order that eases the outliers, as index positions, or None.

    Returns ONLY a suggestion. Sequencing also has to satisfy harmonic and
    energy constraints that this module knows nothing about, so the caller
    decides whether the tempo gain is worth any harmonic cost.
    """
    problem = outliers(native_bpms, tolerance_pct, **kw)
    if not problem:
        return None
    order = list(range(len(native_bpms)))
    current = tempo_cost(native_bpms, **kw)
    for index in sorted(problem, key=lambda i: -abs(native_bpms[i])):
        pos_in_order = order.index(index)
        rest = [i for i in order if i != index]
        best, best_cost = pos_in_order, current
        for pos in range(len(rest) + 1):
            candidate = rest[:pos] + [index] + rest[pos:]
            cost = tempo_cost([native_bpms[i] for i in candidate], **kw)
            if cost < best_cost - 1e-9:
                best, best_cost = pos, cost
        if best_cost < current - 1e-9:
            order = rest[:best] + [index] + rest[best:]
            current = best_cost
    return order if order != list(range(len(native_bpms))) else None


def span_stretch_percent(native_bpms, track_tempos) -> list[float]:
    """Worst stretch each track sees across its whole AUDIBLE span, signed.

    `max_stretch_percent` reports only the held tempo, which understates the
    truth: a track is still playing during the ramp into it and the ramp out of
    it, so it also experiences its neighbours' held tempos. Codex caught this -
    the 12.08.26 arc reads 0.94% held but 1.73% across full spans.

    This is the number any stretch budget must be judged against.
    """
    worst = []
    for i, native in enumerate(native_bpms):
        seen = [track_tempos[i]]
        if i > 0:
            seen.append(track_tempos[i - 1])
        if i + 1 < len(track_tempos):
            seen.append(track_tempos[i + 1])
        worst.append(max(((t / native - 1.0) * 100.0 for t in seen),
                         key=abs))
    return worst


def span_tempo_cost(native_bpms, **kw) -> float:
    """Worst full-span stretch for a running order. The honest ranking metric."""
    if len(native_bpms) < 2:
        return 0.0
    tempos = solve_track_tempos(native_bpms, **kw)
    return max(abs(s) for s in span_stretch_percent(native_bpms, tempos))
