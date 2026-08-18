"""Regression: a sustained >=8-bar kick-OUT run in the cached drums envelope must
produce at least one boundary cue.

Generalises the "signal exists, detector emits nothing" class of bug (the Double
Dutch case). For every track in the 14.08.26 corpus where the cached Demucs
drums envelope sits below `_solid_kick_level * KICK_ON_FRAC` for a contiguous run
of 8+ bars, the cached `signals.kick_cues` MUST be non-empty -- because
`_kick_cues` emits a dropout/return pair for any such run of >= MIN_KICK_OUT_BEATS,
and an 8-bar (32-beat) run easily clears that floor.

Reuses test_alignment_baseline.py's ROOT resolution and corpus-loading shape.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

STEM_DIR = ROOT / "Test Project" / "14.08.26" / "_Stem Analysis"

pytestmark = pytest.mark.skipif(
    not (STEM_DIR.is_dir()
         and list(STEM_DIR.glob("SECTIONS_STEM_*.json"))
         and list(STEM_DIR.glob("*__stemenv.npz"))),
    reason="14.08.26 stem JSONs or cached stem-envelope npz files are unavailable",
)

# Longest below-threshold bar run required before the property fires.
MIN_OFF_RUN_BARS = 8


def _track_ids():
    return sorted(
        p.name[len("SECTIONS_STEM_"):-len(".json")]
        for p in STEM_DIR.glob("SECTIONS_STEM_*.json")
    )


def _longest_off_run_bars(drums, hop_t, downbeat, bpm, n_bars):
    """Longest contiguous run of bars whose drums envelope sits below the
    production threshold. Mirrors stem_detector._kick_on_per_beat's per-beat
    boolean mask (no median-smoothing — we hunt for >= 32-beat runs, well
    beyond the 3-beat smoothing window), then aggregates per-beat to per-bar
    the same way production builds `kick_on_bar` at stem_detector.py:472:
    a bar counts as "on" iff >= 2 of its 4 beats cleared KICK_ON_FRAC."""
    from stem_detector import _solid_kick_level, KICK_ON_FRAC
    beat_sec = 60.0 / bpm
    # Per-beat peaks to feed _solid_kick_level (the dynamic, per-track threshold).
    n_beats = n_bars * 4
    peaks = np.zeros(n_beats)
    for k in range(n_beats):
        i0 = int((downbeat + k * beat_sec) / hop_t)
        i1 = min(int((downbeat + (k + 1) * beat_sec) / hop_t), len(drums))
        peaks[k] = float(drums[i0:i1].max()) if i1 > i0 else 0.0
    threshold = _solid_kick_level(peaks) * KICK_ON_FRAC
    # Per-beat boolean — production rule (without the median smoother: it can't
    # change whether an 8-bar/32-beat sustained run exists).
    beat_on = peaks > threshold
    # Per-bar aggregation identical to production's `kick_on_bar` rule.
    bar_on = beat_on.reshape(n_bars, 4).mean(axis=1) >= 0.5
    below = ~bar_on
    longest = run = 0
    for v in below:
        run = run + 1 if v else 0
        longest = max(longest, run)
    return longest


@pytest.mark.parametrize("track_id", _track_ids())
def test_long_off_run_produces_kick_cue(track_id):
    sec_path = STEM_DIR / f"SECTIONS_STEM_{track_id}.json"
    env_path = STEM_DIR / f"{track_id}__stemenv.npz"
    assert env_path.exists(), f"missing cached envelope {env_path.name}"

    sec = json.loads(sec_path.read_text(encoding="utf-8"))
    env = np.load(env_path)
    bpm = float(sec["bpm"])
    n_bars = int(sec["n_bars"])
    downbeat = float(sec.get("downbeat") or 0.0)

    longest = _longest_off_run_bars(env["drums"], float(env["hop_t"]),
                                   downbeat, bpm, n_bars)
    n_cues = len(sec["signals"]["kick_cues"])

    if longest < MIN_OFF_RUN_BARS:
        pytest.skip(f"precondition not met: longest off-run is {longest} bars "
                    f"(need >= {MIN_OFF_RUN_BARS}) -- nothing to assert")

    assert n_cues > 0, (
        f"track has a {longest}-bar below-threshold kick-OUT run in the cached "
        f"drums envelope but its cached kick_cues is empty -- this is the "
        f"Double-Dutch-shaped bug: signal exists, boundary-cutting emits nothing"
    )
