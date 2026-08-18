"""Test the mix-energy cue function in Source/stem_detector.py.

The _energy_cues function is the boundary-cutting signal that catches sustained
low-mix-energy runs the kick/bass paths miss (e.g. a sub-EQ'd kick whose click
survives but whose body is gone -- the 31-47 dip on Sam Leagas - Double Dutch).
It must:
  - cut a real, sustained dip that spans >= MIN_ENERGY_RUN_BARS consecutive bars
  - NOT cut a brief blip below threshold that is too short to be a real break
  - NOT cut a dip whose minimum is above the threshold
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
