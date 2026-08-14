"""Sam's tempo model, written down as tests.

His spec (2026-08-13), verbatim where it matters:

  - "I'll usually do them across a transition of two tracks to kind of hide the
     fact that it's happening"
  - "the tempo changes need to be over a good amount of time... you don't wanna
     hear the speed up or the slowdown"
  - "if you've got five tracks that are 125, and in the middle of them is a 130,
     you'd maybe travel up to 126, 127 whilst that track was playing, so you
     wouldn't reach 130, but you'd go as high as possible"

The last one is the load-bearing case: the arc does NOT chase an outlier. The
odd track absorbs the stretch so the rest of the mix stays comfortable. A naive
follow-the-track design would drag the whole mix toward the 130 and fail this.
"""

import pytest

from automated_dj_mixes.tempo_curve import (
    DEFAULT_MAX_STEP_BPM,
    build_tempo_points,
    max_stretch_percent,
    slowest_ramp_bpm_per_bar,
    solve_track_tempos,
)

SAM_OUTLIER = [125.0, 125.0, 130.0, 125.0, 125.0]
REAL_MIX = [118.0, 118.0, 120.0, 121.0, 122.0, 123.2, 124.0, 125.0]


def test_outlier_is_absorbed_not_chased():
    """Sam's worked example, and the whole point of the design."""
    tempos = solve_track_tempos(SAM_OUTLIER)
    peak = max(tempos)
    assert 126.0 <= peak <= 127.5, f"expected 126-127, got {peak:.2f}"
    assert peak < 130.0, "must never reach the outlier's tempo"


def test_the_outlier_takes_the_stretch():
    """The odd track is the one that suffers, by design - so the other four
    stay near native rather than all being dragged sharp."""
    stretch = max_stretch_percent(SAM_OUTLIER, solve_track_tempos(SAM_OUTLIER))
    outlier = abs(stretch[2])
    others = [abs(s) for i, s in enumerate(stretch) if i != 2]
    assert outlier > max(others) * 2, "the outlier should absorb the compromise"
    assert max(others) < 1.0, "the rest of the mix must stay comfortable"


def test_uniform_tempo_mix_does_not_move():
    """No spurious drift when every track already agrees."""
    tempos = solve_track_tempos([124.0] * 6)
    assert max(tempos) - min(tempos) < 1e-6
    assert all(abs(t - 124.0) < 1e-6 for t in tempos)


def test_real_mix_is_far_better_than_one_frozen_tempo():
    """12.08.26: the mode rule froze 118 - the SLOWEST track - stretching
    everything else by up to -5.6%, which Sam heard in the car.

    Judged on FULL-SPAN stretch, not held tempo. Codex caught that the held
    number understates the truth: a track keeps playing through the ramps at
    either end, so it also experiences its neighbours' tempos. Held reads
    0.94% here; the honest figure is 1.73%."""
    from automated_dj_mixes.tempo_curve import span_stretch_percent
    tempos = solve_track_tempos(REAL_MIX)
    worst = max(abs(s) for s in span_stretch_percent(REAL_MIX, tempos))
    assert worst < 2.0, f"full-span worst {worst:.2f}% should beat the 5.6% baseline"
    frozen_worst = max(abs((118.0 / n - 1) * 100) for n in REAL_MIX)
    assert worst < frozen_worst / 3


def test_held_stretch_understates_the_truth():
    """Pins the trap itself, so nobody reports the flattering number again."""
    from automated_dj_mixes.tempo_curve import span_stretch_percent
    tempos = solve_track_tempos(REAL_MIX)
    held = max(abs(s) for s in max_stretch_percent(REAL_MIX, tempos))
    span = max(abs(s) for s in span_stretch_percent(REAL_MIX, tempos))
    assert span > held, "full-span must be >= held, and here it is strictly worse"
    assert span > 1.5 > held


def test_arc_follows_the_general_direction():
    """An ascending mix should produce an ascending arc - it is an arc through
    the BPMs, not a flat average."""
    tempos = solve_track_tempos(REAL_MIX)
    assert tempos[-1] > tempos[0]
    assert all(b >= a - 1e-9 for a, b in zip(tempos, tempos[1:])), "monotonic"


def test_step_between_tracks_is_capped():
    """A hard ceiling on top of the smoothing, so no single move lurches."""
    wild = [120.0, 138.0, 119.0, 140.0, 118.0]
    tempos = solve_track_tempos(wild)
    for a, b in zip(tempos, tempos[1:]):
        assert abs(b - a) <= DEFAULT_MAX_STEP_BPM + 1e-6


def test_ramps_sit_inside_the_transitions():
    """Tempo moves only where two tracks are playing - that is what hides it."""
    tempos = solve_track_tempos([120.0, 124.0])
    points = build_tempo_points(tempos, [(400.0, 560.0)])
    assert points[0].beat == 0.0
    holds = [p for p in points if abs(p.bpm - tempos[0]) < 1e-9]
    assert any(p.beat == 400.0 for p in holds), "held until the overlap begins"
    assert any(p.beat == 560.0 and abs(p.bpm - tempos[1]) < 1e-9 for p in points)


def test_ramp_is_gradual_enough_to_be_inaudible():
    """Across a 40-bar overlap a 1.5 BPM move is under 0.04 BPM per bar."""
    tempos = solve_track_tempos(REAL_MIX)
    transitions = [(i * 640.0 + 480.0, i * 640.0 + 640.0) for i in range(len(REAL_MIX) - 1)]
    slope = slowest_ramp_bpm_per_bar(build_tempo_points(tempos, transitions))
    assert slope < 0.1, f"{slope:.3f} BPM/bar is too abrupt to hide"


@pytest.mark.parametrize("bpms", [[], [123.0]])
def test_degenerate_inputs(bpms):
    assert solve_track_tempos(bpms) == [float(b) for b in bpms]


# --- resequencing: an outlier is only an outlier where it sits ---------------
#
# Sam, 2026-08-13: "let's say dissection of the mix tracks one to five, it would
# be an outlier if you put it there, but maybe at the end of the mix we're
# getting up to 128 BPM, so if you put it after the 128 track it's fine. So both
# things work in this scenario."
#
# Resequencing and the smoothed arc COMPOSE. Codex proposed refusing or
# resequencing a transition above ~3 BPM; Sam's answer is to do both - move the
# track somewhere it fits, and still soften what remains.

CLIMBING_WITH_EARLY_OUTLIER = [130.0, 120.0, 121.0, 122.0, 123.0,
                               124.0, 125.0, 126.0, 127.0, 128.0]


def test_outlier_early_in_a_climbing_mix_is_flagged():
    from automated_dj_mixes.tempo_curve import outliers, tempo_cost
    assert 0 in outliers(CLIMBING_WITH_EARLY_OUTLIER)
    assert tempo_cost(CLIMBING_WITH_EARLY_OUTLIER) > 3.0


def test_moving_the_outlier_to_the_end_fixes_it():
    """The 130 belongs after the 128, where the arc has already climbed."""
    from automated_dj_mixes.tempo_curve import suggest_resequence, tempo_cost
    order = suggest_resequence(CLIMBING_WITH_EARLY_OUTLIER)
    assert order is not None
    assert order[-1] == 0, "the 130 should end up last, after the 128"
    reordered = [CLIMBING_WITH_EARLY_OUTLIER[i] for i in order]
    assert tempo_cost(reordered) < tempo_cost(CLIMBING_WITH_EARLY_OUTLIER) / 2


def test_resequence_returns_a_valid_permutation():
    from automated_dj_mixes.tempo_curve import suggest_resequence
    order = suggest_resequence(CLIMBING_WITH_EARLY_OUTLIER)
    assert sorted(order) == list(range(len(CLIMBING_WITH_EARLY_OUTLIER)))


def test_no_suggestion_when_nothing_is_suffering():
    """Must not churn a running order that is already fine - sequencing also
    answers to harmony and energy, which this module knows nothing about."""
    from automated_dj_mixes.tempo_curve import suggest_resequence
    assert suggest_resequence(REAL_MIX) is None
    assert suggest_resequence([124.0] * 5) is None


def test_best_position_for_reports_where_it_belongs():
    from automated_dj_mixes.tempo_curve import best_position_for
    pos, cost = best_position_for(CLIMBING_WITH_EARLY_OUTLIER, 0)
    assert pos == len(CLIMBING_WITH_EARLY_OUTLIER) - 1
    assert cost < 2.0


def test_both_mechanisms_beat_either_alone():
    """Sam's point that they compose. Against a single frozen tempo, the arc
    alone helps, and the arc plus repositioning helps considerably more."""
    from automated_dj_mixes.tempo_curve import suggest_resequence, tempo_cost
    bpms = CLIMBING_WITH_EARLY_OUTLIER
    frozen = max(abs((124.0 / n - 1) * 100) for n in bpms)   # one fixed tempo
    arc_only = tempo_cost(bpms)
    order = suggest_resequence(bpms)
    both = tempo_cost([bpms[i] for i in order])
    assert arc_only < frozen
    assert both < arc_only
