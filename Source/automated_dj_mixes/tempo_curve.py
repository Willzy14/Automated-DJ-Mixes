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
    points = [TempoPoint(0.0, track_tempos[0])]
    for i, (start, end) in enumerate(transitions):
        if i + 1 >= len(track_tempos):
            break
        points.append(TempoPoint(float(start), track_tempos[i]))
        points.append(TempoPoint(float(end), track_tempos[i + 1]))
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
