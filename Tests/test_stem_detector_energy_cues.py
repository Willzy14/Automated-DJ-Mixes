"""Test the mix-energy cue function in Source/stem_detector.py.

The _energy_cues function is the boundary-cutting signal that catches sustained
low-mix-energy runs the kick/bass paths miss (e.g. a sub-EQ'd kick whose click
survives but whose body is gone -- the 31-47 dip on Sam Leagas - Double Dutch).
It must:
  - cut a real, sustained dip that spans >= MIN_ENERGY_RUN_BARS consecutive bars
  - NOT cut a brief blip below threshold that is too short to be a real break
  - NOT cut a dip whose minimum is above the threshold
  - NOT split a sustained dip because one borderline bar (0.40-0.45) pokes above the
    entry threshold (hysteresis, Codex case (a))
  - trim trailing borderline bars so the return cue lands on the real energy return
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

import stem_detector


def _run_cues(mix_norm, downbeat=0.0, sec_per_bar=1.85):
    return stem_detector._energy_cues(mix_norm, downbeat, sec_per_bar)


def test_sustained_dip_is_cut():
    """A clear 14-bar low-energy run well below MIX_ENERGY_BREAK_FRAC fires one drop + one return."""
    mix_norm = np.ones(40)
    mix_norm[6:20] = 0.20      # 14 bars of low energy, well below 0.40 and >= MIN_ENERGY_RUN_BARS
    cues = _run_cues(mix_norm)
    drops = [c for c in cues if c["type"] == "energy_drop"]
    returns = [c for c in cues if c["type"] == "energy_return"]
    assert len(drops) == 1 and drops[0]["bar"] == 6.0
    assert len(returns) == 1 and returns[0]["bar"] == 20.0
    # start_sec must reflect the downbeat + bar offset
    assert drops[0]["start_sec"] == round(6 * 1.85, 2)
    assert returns[0]["start_sec"] == round(20 * 1.85, 2)


def test_short_blip_is_not_cut():
    """An 8-bar dip below threshold (shorter than MIN_ENERGY_RUN_BARS=12) fires no cues."""
    mix_norm = np.ones(40)
    mix_norm[8:16] = 0.20      # 8 bars, too short
    cues = _run_cues(mix_norm)
    assert cues == []


def test_blip_above_threshold_is_not_cut():
    """A dip that never crosses the threshold is invisible to the function."""
    mix_norm = np.ones(40)
    mix_norm[6:20] = 0.60      # 14 bars, but always above 0.40
    cues = _run_cues(mix_norm)
    assert cues == []


def test_two_separate_runs_emit_two_pairs():
    """Two disjoint sustained low-energy runs each emit their own drop/return pair."""
    mix_norm = np.ones(60)
    mix_norm[5:18] = 0.20      # 13 bars
    mix_norm[30:45] = 0.15     # 15 bars
    cues = _run_cues(mix_norm)
    drops = sorted(c["bar"] for c in cues if c["type"] == "energy_drop")
    returns = sorted(c["bar"] for c in cues if c["type"] == "energy_return")
    assert drops == [5.0, 30.0]
    assert returns == [18.0, 45.0]


def test_run_at_track_end_emits_no_return_when_closes_outside():
    """A run that runs off the end of the array emits a drop at the start and no return."""
    mix_norm = np.ones(40)
    mix_norm[28:40] = 0.20     # 12 bars, last bar is the array end -> no return inside
    cues = _run_cues(mix_norm)
    drops = [c for c in cues if c["type"] == "energy_drop"]
    returns = [c for c in cues if c["type"] == "energy_return"]
    assert len(drops) == 1 and drops[0]["bar"] == 28.0
    assert returns == []  # nothing inside the array to return into


def test_codex_case_a_single_borderline_bar_does_not_split():
    """Codex review case (a): a genuine 16-bar break with ONE bar at 0.41 (just above
    the 0.40 entry threshold) must yield ONE cue pair spanning the whole dip -- under
    the pre-hysteresis rule it split into 8+7 bar halves, both under the 12-bar floor,
    and the break vanished."""
    mix_norm = np.ones(40)
    mix_norm[6:22] = 0.20      # 16-bar break...
    mix_norm[14] = 0.41        # ...with one knife-edge bar poking above 0.40
    cues = _run_cues(mix_norm)
    drops = [c for c in cues if c["type"] == "energy_drop"]
    returns = [c for c in cues if c["type"] == "energy_return"]
    assert len(drops) == 1 and drops[0]["bar"] == 6.0
    assert len(returns) == 1 and returns[0]["bar"] == 22.0


def test_codex_case_b_marginal_lull_fires_by_design():
    """Codex review case (b): a 12-bar lull at 0.39 emits a cue pair BY DESIGN, with or
    without kick+bass present. The proposed kick+bass-present guard was REJECTED on the
    2026-08-20 111-track replay: every detected run corpus-wide has kick fraction 0.0
    (the guard would be dead code), and under the V3 kick-model path the model reads
    the flagship Double Dutch dip as kick-ON with bass presence 0.765 -- the guard
    would have suppressed the exact break the mechanism was built to catch. A cue is
    only a candidate boundary; _snap_merge/_assign_labels still own the final call."""
    mix_norm = np.ones(40)
    mix_norm[10:22] = 0.39     # 12 bars, exactly at the run-length floor
    cues = _run_cues(mix_norm)
    drops = [c for c in cues if c["type"] == "energy_drop"]
    returns = [c for c in cues if c["type"] == "energy_return"]
    assert len(drops) == 1 and drops[0]["bar"] == 10.0
    assert len(returns) == 1 and returns[0]["bar"] == 22.0


def test_trailing_borderline_bars_are_trimmed():
    """Bars in the hysteresis band (0.40-0.45) BRIDGE a run but never extend its tail:
    the run ends after its last genuinely-low bar, so the return cue stays anchored to
    the real energy return, not the borderline build-up after it."""
    mix_norm = np.ones(40)
    mix_norm[6:19] = 0.20      # 13 genuinely low bars
    mix_norm[19:23] = 0.43     # borderline tail -- inside the band, must be trimmed
    cues = _run_cues(mix_norm)
    drops = [c for c in cues if c["type"] == "energy_drop"]
    returns = [c for c in cues if c["type"] == "energy_return"]
    assert len(drops) == 1 and drops[0]["bar"] == 6.0
    assert len(returns) == 1 and returns[0]["bar"] == 19.0


def test_double_dutch_bars_31_48_still_detected():
    """Regression pin for the original acceptance case: the real per-bar mix_norm from
    the 2026-08-18 Mix-Envelope investigation (Sam Leagas - Double Dutch, bars 16-60,
    Documentation/Reviews table) must still yield exactly one drop at bar 31 and one
    return at bar 48. The single-bar fills at bars 23 and 55 must stay silent."""
    doc = [0.8964, 0.8948, 0.9000, 0.8823, 0.8945, 0.8936, 0.8944, 0.3827,
           0.9020, 0.9061, 0.8861, 0.9021, 0.8950, 0.9071, 0.8826,          # 16-30
           0.3871, 0.3848, 0.3221, 0.2946, 0.3030, 0.3372, 0.2845, 0.2610,
           0.2764, 0.3127, 0.3229, 0.3257, 0.3331, 0.3455, 0.3557, 0.3602,
           0.3454,                                                          # 31-47 dip
           0.9192, 0.9715, 0.9584, 0.9606, 0.9478, 0.9703, 0.9534, 0.3965,
           0.9281, 0.9826, 0.9651, 0.9660, 0.9652]                          # 48-60
    mix_norm = np.full(152, 0.90)
    mix_norm[16:61] = doc
    cues = _run_cues(mix_norm)
    drops = [c for c in cues if c["type"] == "energy_drop"]
    returns = [c for c in cues if c["type"] == "energy_return"]
    assert len(drops) == 1 and drops[0]["bar"] == 31.0
    assert len(returns) == 1 and returns[0]["bar"] == 48.0
