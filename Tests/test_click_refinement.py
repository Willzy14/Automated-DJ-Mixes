"""The click search has a bounded reach, and that bound used to fail silently.

`refine_to_click` snaps a sub-detected kick onset back to the sharp beater
transient. The search reach is limited so it cannot wander onto a neighbouring
hit. When a kick's sub body blooms slowly the true click sits further back than
the reach, and the backtrack then stops on the window wall instead of on the
strike - every kick short by the same amount, so the grid keeps a correct tempo
and takes a constant phase error.

Measured on Really Nice (2026-08-13): 95% of kicks pinned at exactly the 35 ms
cap, the true click 68 ms back, and the shipped grid 34 ms late end to end
against Ableton's own transient analysis. Healthy tracks sat at 1-4%.

The old grid_vs_kick check could never see this: it scores the grid against the
onsets that built it, so a shared error cancels. These tests pin the wall-hit
rate instead, which is independent of the grid.
"""

import numpy as np
import pytest
from scipy.signal import butter, sosfiltfilt

from audio_analysis.stem_grid import (
    CLICK_FLOOR,
    CLICK_HP,
    CLICK_SATURATION_RETRY,
    CLICK_WIN_SEC,
    refine_to_click,
)

SR = 44100
PERIOD = 0.5            # 120 BPM
N_BEATS = 16


def _drums_with_clicks(click_times, sr=SR, dur=None):
    """Sharp broadband ticks at the given times - the beater transients."""
    dur = dur or (max(click_times) + 1.0)
    y = np.zeros(int(dur * sr), dtype=np.float32)
    rng = np.random.default_rng(0)
    burst = int(0.003 * sr)
    for t in click_times:
        i = int(t * sr)
        y[i:i + burst] += rng.normal(0, 1, burst).astype(np.float32)
    return y


def _reference_original(drums, sr, kicks, win_sec=0.035):
    """The algorithm exactly as it was before the adaptive retry."""
    hp = sosfiltfilt(butter(4, CLICK_HP, btype="high", fs=sr, output="sos"), drums)
    env = np.abs(hp)
    w = max(1, int(sr * 0.0006))
    env = np.convolve(env, np.ones(w) / w, mode="same")
    win = int(win_sec * sr)
    out = []
    for k in kicks:
        i = int(k * sr)
        lo, hi = max(0, i - win), min(len(env), i + win)
        if hi - lo < 4:
            out.append(k)
            continue
        peak = lo + int(np.argmax(env[lo:hi]))
        floor = CLICK_FLOOR * env[peak]
        j = peak
        while j > lo and env[j] > floor:
            j -= 1
        out.append(j / sr)
    return np.asarray(sorted(out))


@pytest.fixture(scope="module")
def clicks():
    return np.array([1.0 + i * PERIOD for i in range(N_BEATS)])


def test_default_path_is_bit_identical_to_the_original(clicks):
    """The retry is opt-in. Without max_win_sec the function must behave exactly
    as before - this code is shared with Ableton Project Setup, so an unasked-for
    behaviour change would land in a second project silently."""
    drums = _drums_with_clicks(clicks)
    late = clicks + 0.020                     # sub detected 20 ms after the strike
    np.testing.assert_array_equal(
        refine_to_click(drums, SR, late),
        _reference_original(drums, SR, late),
    )


def test_healthy_kick_is_pulled_back_onto_the_click(clicks):
    drums = _drums_with_clicks(clicks)
    late = clicks + 0.020
    out = refine_to_click(drums, SR, late)
    assert np.median(np.abs(out - clicks)) * 1000 < 5.0


def test_slow_sub_saturates_the_search(clicks):
    """A click 68 ms back cannot be reached by a 35 ms window - and the failure
    is silent, so it has to be reported rather than inferred from the result."""
    drums = _drums_with_clicks(clicks)
    late = clicks + 0.068
    out, stats = refine_to_click(drums, SR, late, return_stats=True)
    assert stats["rate"] > 0.9, "wall-hits should be near-universal here"
    residual = np.median(out - clicks) * 1000
    assert residual > 20.0, "correction should be truncated, leaving a phase error"


def test_healthy_material_does_not_saturate(clicks):
    drums = _drums_with_clicks(clicks)
    _out, stats = refine_to_click(drums, SR, clicks + 0.020, return_stats=True)
    assert stats["rate"] < 0.1


def test_retry_recovers_the_truncated_correction(clicks):
    drums = _drums_with_clicks(clicks)
    late = clicks + 0.068
    out, stats = refine_to_click(drums, SR, late,
                                 max_win_sec=PERIOD / 6.0, return_stats=True)
    assert np.median(np.abs(out - clicks)) * 1000 < 5.0
    assert stats["rate"] < 0.1


def test_retry_leaves_unsaturated_kicks_untouched(clicks):
    """Only kicks that hit the wall retry, so offering a wider reach must not
    move material that was already correct."""
    drums = _drums_with_clicks(clicks)
    late = clicks + 0.020
    np.testing.assert_array_equal(
        refine_to_click(drums, SR, late),
        refine_to_click(drums, SR, late, max_win_sec=PERIOD / 6.0),
    )


def test_retry_reach_stays_inside_a_sixteenth():
    """period/6 must stay strictly under a 16th note (period/4), or a widened
    search could snap onto the neighbouring hit - a worse, and equally silent,
    error than the one being fixed."""
    assert PERIOD / 6.0 < PERIOD / 4.0
    assert PERIOD / 6.0 > 0.068, "must still reach the measured 68 ms case"


def test_saturation_threshold_separates_measured_populations():
    """Healthy 1-4%, borderline 28%, broken 95%. The threshold sits above the
    borderline case on purpose: a correct grid is never disturbed."""
    assert 0.28 < CLICK_SATURATION_RETRY < 0.95
    assert CLICK_WIN_SEC == 0.035
