"""Tests for burn list C5 (2026-09-14, item (c) landing order): the overlap/
loop-extension/loop-repeat caps used to gate the swap-point search and the
loop planner were MODULE-LEVEL constants frozen from INTERIM_V1 at import
time, regardless of which `policy` object was actually threaded through the
call chain - a policy with genuinely different caps would have these checks
silently ignore it. `Tests/test_alignment_baseline.py`'s 380-pair sweep
proves this is numerically neutral for every policy that exists today
(INTERIM_V1 and SAM_V1 currently share these exact fields); these tests
prove the live-policy read actually works by giving it a policy that
GENUINELY differs, which the old frozen-constant code could never respond
to no matter what was passed.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import align_engine as AE  # noqa: E402
from automated_dj_mixes.transition_policy import INTERIM_V1  # noqa: E402


def _quality_context():
    import numpy as np
    return AE.LoopQualityContext(
        Path("synthetic__stemenv.npz"), 126.0, 0.0, 0.1,
        {name: np.full(5000, 0.1, dtype=float)
         for name in ("drums", "bass", "other", "vocals", "mix")},
    )


def _track(name, *, n_bars=128, sections=None, bass_in=0.0, bass_out=120.0,
          loop_windows=None):
    sections = sections or [
        {"name": "drop_1", "label": "drop", "start_bar": 0.0, "end_bar": 96.0},
        {"name": "outro_1", "label": "outro", "start_bar": 96.0, "end_bar": float(n_bars)},
    ]
    return AE.Track(
        name=name, bpm=126.0, spb=4 * 60.0 / 126.0, downbeat=0.0,
        n_bars=n_bars, sections=sections, bass_in_bar=bass_in,
        bass_out_bar=bass_out, last_min_bars=64,
        loop_windows=loop_windows or [], loop_quality_context=_quality_context(),
    )


# --------------------------------------------------------------------------- #
# Overlap admissibility (_align_pair_landmark_aware / _search_anchors, via   #
# the public align_pair entry point) actually reads the passed-in policy    #
# --------------------------------------------------------------------------- #

def _landmark_pair():
    """Same shape as test_arrangement_safety.py's landmark alignment fixture
    - a real landmark-path pairing that resolves to a 27-bar overlap under
    the default policy (194 - 167 = 27), comfortably inside INTERIM_V1's
    48-bar cap."""
    outgoing = _track(
        "out", n_bars=194,
        sections=[
            {"name": "drop_1", "label": "drop", "start_bar": 0.0, "end_bar": 175.0},
            {"name": "outro_1", "label": "outro", "start_bar": 175.0, "end_bar": 194.0},
        ],
    )
    incoming = _track(
        "in", n_bars=186,
        sections=[
            {"name": "intro_1", "label": "intro", "start_bar": 0.0, "end_bar": 8.0},
            {"name": "drop_1", "label": "drop", "start_bar": 8.0, "end_bar": 16.0},
            {"name": "build_1", "label": "build", "start_bar": 16.0, "end_bar": 20.0},
            {"name": "drop_2", "label": "drop", "start_bar": 20.0, "end_bar": 160.0},
            {"name": "outro_1", "label": "outro", "start_bar": 160.0, "end_bar": 186.0},
        ],
    )
    outgoing.musical_landmarks = [{
        "landmark_id": "kick_gap_190_194", "type": "kick_dropout",
        "start_bar": 190.0, "end_bar": 194.0,
    }]
    return outgoing, incoming


def test_default_policy_finds_the_27_bar_overlap():
    outgoing, incoming = _landmark_pair()
    alignment = AE.align_pair(outgoing, incoming)  # policy=None -> INTERIM_V1
    assert alignment.overlap_bars == 27.0


def test_a_tighter_policy_genuinely_rejects_the_same_overlap():
    """The actual fix: a policy object with a max_overlap_beats BELOW 27 bars
    must now cause this exact same pairing to be refused - impossible before
    this fix, since the admissibility check used to ignore whatever policy
    was passed and always gate on INTERIM_V1's frozen 48-bar module
    constant. This fixture has no other viable drop-anchor pairing, so the
    search finds nothing and raises, rather than silently substituting a
    different candidate."""
    outgoing, incoming = _landmark_pair()
    tight_policy = replace(INTERIM_V1, name="tight_test_policy",
                           max_overlap_beats=20.0 * 4.0)  # 20 bars < the 27-bar overlap
    with pytest.raises(ValueError, match="No paired section/dropout alignment"):
        AE.align_pair(outgoing, incoming, tight_policy)


def test_a_looser_custom_name_at_the_same_caps_changes_nothing():
    """Sanity check on the sanity check: a policy identical to INTERIM_V1 in
    every geometry field (only the name differs) must reproduce the exact
    same 27-bar result - proves the difference above comes from the changed
    cap, not from merely passing a non-None policy object."""
    outgoing, incoming = _landmark_pair()
    same_caps_policy = replace(INTERIM_V1, name="same_caps_test_policy")
    alignment = AE.align_pair(outgoing, incoming, same_caps_policy)
    assert alignment.overlap_bars == 27.0


# --------------------------------------------------------------------------- #
# pick_cue_bounded_drum_loop actually reads a passed-in policy.max_loop_repeats #
# --------------------------------------------------------------------------- #

def test_pick_cue_bounded_drum_loop_default_policy_allows_the_historical_repeat_count():
    track = _track("t", n_bars=64, loop_windows=[(48.0, 64.0)])
    # gap=16, length=8 -> 2 repeats, well within INTERIM_V1's real cap.
    chunk = AE.pick_cue_bounded_drum_loop(track, gap_bars=16)
    assert chunk is not None


def test_pick_cue_bounded_drum_loop_a_stricter_policy_genuinely_lowers_the_cap():
    """The actual fix: a policy with max_loop_repeats=1 must now reject a
    candidate that needs 2 repeats, where the default policy accepts it -
    impossible before this fix (the repeat cap always came from the frozen
    module constant, ignoring any policy argument, because there was no
    policy argument at all)."""
    track_default = _track("t", n_bars=64, loop_windows=[(48.0, 64.0)])
    track_strict = _track("t", n_bars=64, loop_windows=[(48.0, 64.0)])
    strict_policy = replace(INTERIM_V1, name="strict_repeats_test_policy",
                            max_loop_repeats=1)

    default_chunk = AE.pick_cue_bounded_drum_loop(track_default, gap_bars=16)
    strict_chunk = AE.pick_cue_bounded_drum_loop(
        track_strict, gap_bars=16, policy=strict_policy)

    assert default_chunk is not None  # 2 repeats of an 8-bar chunk: allowed
    assert strict_chunk is None       # same gap, but only 1 repeat allowed now


def test_pick_cue_bounded_drum_loop_omitted_policy_matches_explicit_default():
    """A caller that never passes `policy` at all (this function's own
    pre-existing direct callers/tests) must get the exact same result as one
    that explicitly passes INTERIM_V1 - the None-default must resolve to the
    identical policy, not merely `something`."""
    track_a = _track("t", n_bars=64, loop_windows=[(48.0, 64.0)])
    track_b = _track("t", n_bars=64, loop_windows=[(48.0, 64.0)])
    assert (AE.pick_cue_bounded_drum_loop(track_a, gap_bars=16)
           == AE.pick_cue_bounded_drum_loop(track_b, gap_bars=16, policy=INTERIM_V1))
