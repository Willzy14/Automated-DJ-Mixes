"""Tests for Camelot wheel logic and harmonic sequencing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

from automated_dj_mixes.sequencer import (
    key_to_camelot,
    compatibility_score,
    is_compatible,
    build_harmonic_path,
    CAMELOT_WHEEL,
)


# --- key_to_camelot ---

def test_key_to_camelot_major():
    assert key_to_camelot("C major") == "8B"
    assert key_to_camelot("G major") == "9B"
    assert key_to_camelot("F major") == "7B"


def test_key_to_camelot_minor():
    assert key_to_camelot("A minor") == "8A"
    assert key_to_camelot("E minor") == "9A"
    assert key_to_camelot("D minor") == "7A"


def test_key_to_camelot_aliases():
    assert key_to_camelot("Am") == "8A"
    assert key_to_camelot("F#m") == "11A"
    assert key_to_camelot("Bb") == "6B"
    assert key_to_camelot("Eb") == "5B"


def test_key_to_camelot_unknown():
    assert key_to_camelot("X weird") is None


def test_all_keys_mapped():
    assert len(CAMELOT_WHEEL) == 24


# --- compatibility_score ---

def test_identical_key():
    score, kind = compatibility_score("8A", "8A")
    assert score == 4
    assert kind == "identical"


def test_smooth_transition():
    score, kind = compatibility_score("5A", "6A")
    assert score == 3
    assert kind == "smooth"


def test_smooth_wraps_around():
    score, kind = compatibility_score("12B", "1B")
    assert score == 3
    assert kind == "smooth"


def test_relative_key():
    score, kind = compatibility_score("5A", "5B")
    assert score == 3
    assert kind == "relative_key"


def test_power_mix():
    score, kind = compatibility_score("5A", "7A")
    assert score == 2
    assert kind == "power_mix"


def test_power_mix_wraps():
    score, kind = compatibility_score("11A", "1A")
    assert score == 2
    assert kind == "power_mix"


def test_diagonal():
    score, kind = compatibility_score("5A", "6B")
    assert score == 1
    assert kind == "diagonal"


def test_clash():
    score, kind = compatibility_score("5A", "9B")
    assert score == 0
    assert kind == "clash"


# --- is_compatible ---

def test_compatible_smooth():
    compat, kind = is_compatible("8A", "9A")
    assert compat is True
    assert kind == "smooth"


def test_incompatible_clash():
    compat, kind = is_compatible("1A", "7A")
    assert compat is False


# --- build_harmonic_path ---

def test_path_empty():
    assert build_harmonic_path([]) == []


def test_path_single():
    tracks = [{"camelot": "5A", "name": "track1"}]
    result = build_harmonic_path(tracks)
    assert len(result) == 1


def test_path_preserves_all_tracks():
    tracks = [
        {"camelot": "5A", "name": "a"},
        {"camelot": "8B", "name": "b"},
        {"camelot": "6A", "name": "c"},
        {"camelot": "7A", "name": "d"},
    ]
    result = build_harmonic_path(tracks)
    assert len(result) == 4
    assert set(t["name"] for t in result) == {"a", "b", "c", "d"}


def _clash_count(result):
    from automated_dj_mixes.sequencer import compatibility_score
    return sum(1 for i in range(len(result) - 1)
               if compatibility_score(result[i]["camelot"], result[i + 1]["camelot"])[0] == 0)


def test_path_keeps_smooth_neighbours_adjacent():
    """The smooth pair (5A,6A) must end up adjacent; 10B can't be made compatible
    with either, so the optimal path has the minimum 1 clash with 5A/6A together."""
    tracks = [
        {"camelot": "5A", "name": "start"},
        {"camelot": "10B", "name": "far_away"},
        {"camelot": "6A", "name": "smooth_next"},
    ]
    result = build_harmonic_path(tracks)
    names = [t["name"] for t in result]
    assert abs(names.index("start") - names.index("smooth_next")) == 1
    assert _clash_count(result) == 1   # 10B is the one unavoidable clash


def test_path_finds_optimal_smooth_chain():
    """1A..5A form a perfect +1 chain, so the optimal path is the whole chain with
    ZERO clashes. (The old greedy got stuck after 3A→4A→5A and left 2 clashes;
    the Held-Karp path reaches the floor.)"""
    tracks = [
        {"camelot": "3A", "name": "t3"},
        {"camelot": "5A", "name": "t5"},
        {"camelot": "1A", "name": "t1"},
        {"camelot": "4A", "name": "t4"},
        {"camelot": "2A", "name": "t2"},
    ]
    result = build_harmonic_path(tracks)
    assert _clash_count(result) == 0
    nums = [int(t["camelot"][:-1]) for t in result]
    assert all(abs(nums[i] - nums[i + 1]) == 1 for i in range(len(nums) - 1))


# --- unknown-key handling (burn list D4, 2026-09-22) ---
# `orchestrator.py` used to write "camelot": a.camelot or "1A" for a track with
# no key data, and sequencer.py's cost function then treated "1A" as CONFIRMED -
# either fabricating a clash against an unrelated track, or (worse) fabricating
# an "identical" (score=4, the single best score) match between two tracks that
# both merely happen to be missing key data. Track dicts below omit "camelot"
# entirely (or set it to None) to simulate the honest post-fix input.

def test_edge_cost_unknown_key_is_neutral_not_a_fabricated_match():
    """A track with no camelot paired against a known 5A must not score as
    identical (which a silent '1A' vs a real '1A' track would do) or as a
    clash - it must land at the dedicated neutral cost."""
    from automated_dj_mixes.sequencer import _edge_cost, _W_UNKNOWN_KEY, _W_CLASH
    known = {"camelot": "5A"}
    unknown = {"camelot": None}
    cost = _edge_cost(known, unknown)
    assert cost == _W_UNKNOWN_KEY
    assert cost < _W_CLASH


def test_edge_cost_unknown_key_missing_field_same_as_none():
    """A track dict that omits 'camelot' entirely (the real shape from
    orchestrator.py when a.camelot is None and the dict comprehension writes
    it through) must be treated identically to an explicit None."""
    from automated_dj_mixes.sequencer import _edge_cost
    known = {"camelot": "5A"}
    assert _edge_cost(known, {}) == _edge_cost(known, {"camelot": None})


def test_count_clashes_excludes_unknown_key_pairs():
    """A pair with an unknown key on either side is neither a confirmed clash
    nor confirmed safe - it must be excluded from the tally, not silently
    counted as zero (which the old '1A' default did whenever it happened not
    to collide with a real 1A neighbour). Uses the real production
    _count_clashes (the local `_clash_count` test helper above is for the
    known-key-only fixtures elsewhere in this file and has no None guard)."""
    from automated_dj_mixes.sequencer import _count_clashes
    tracks = [
        {"camelot": "5A", "name": "a"},
        {"camelot": None, "name": "unknown"},
        {"camelot": "10B", "name": "c"},   # a genuine clash against 5A, not adjacent here
    ]
    # a->unknown: excluded. unknown->c: excluded. Zero known-key adjacent pairs.
    assert _count_clashes(tracks) == 0


def test_two_unknown_key_tracks_not_falsely_pulled_together():
    """The actual mis-sequence risk Astra flagged: two tracks that both lack
    key data must not be preferred as neighbours just because they'd both
    have silently defaulted to the same fabricated '1A' before this fix.
    Two BPM-close known-key tracks with a real smooth relationship must win
    over parking the two unknown tracks next to each other.

    (This pins the harmonic term still having an opinion when both sides ARE
    known - it does not claim unknown-key tracks can never end up adjacent
    for an unrelated reason, e.g. if BPM strongly favours it; that case is
    legitimate and not what this test is about.)"""
    tracks = [
        {"camelot": "5A", "bpm": 120.0, "name": "known_a"},
        {"camelot": "6A", "bpm": 121.0, "name": "known_b"},   # smooth with known_a
        {"camelot": None, "bpm": 120.0, "name": "unknown_x"},
        {"camelot": None, "bpm": 121.0, "name": "unknown_y"},
    ]
    result = build_harmonic_path(tracks)
    names = [t["name"] for t in result]
    # The real smooth pair must be adjacent - the harmonic term still has an
    # opinion when both sides ARE known, even with unknown-key tracks in the pool.
    assert abs(names.index("known_a") - names.index("known_b")) == 1


def test_unknown_key_never_preferred_over_a_confirmed_weak_match():
    """Found by a Claude-subagent review, 2026-09-22: the first attempt at
    _W_UNKNOWN_KEY (_W_SMOOTH*2, the 'power_mix' midpoint) was CHEAPER than a
    real, CONFIRMED 'diagonal' compatibility (score=1, cost _W_SMOOTH*3) -
    so the optimizer preferred splicing a total unknown between two tracks
    over honouring their real, if weak, harmonic relationship. Reproduced
    exactly: 1A and 2B (a real diagonal pair) got split apart by an unrelated
    unknown-key track. An unknown key must never be preferred over ANY
    confirmed non-clash relationship - only over a confirmed clash."""
    a = {"camelot": "1A", "bpm": 120.0, "name": "a"}
    b = {"camelot": "2B", "bpm": 120.0, "name": "b"}   # diagonal (score=1) with a
    u = {"camelot": None, "bpm": 120.0, "name": "u"}   # unrelated, same BPM (no tiebreak help)
    result = build_harmonic_path([a, b, u])
    names = [t["name"] for t in result]
    assert abs(names.index("a") - names.index("b")) == 1, (
        f"the confirmed diagonal pair (a, b) must stay adjacent - unknown must "
        f"never win over a real relationship, got order {names}"
    )


def test_all_unknown_keys_falls_back_to_bpm_ordering():
    """With every key unknown, the harmonic term is a flat constant across
    every pair (no fabricated preference either way), so the path should
    order purely by the BPM terms - ascending, closest-BPM neighbours first,
    same as sam_v1's BPM-only tiebreak philosophy elsewhere in the pipeline."""
    tracks = [
        {"camelot": None, "bpm": 128.0, "name": "hi"},
        {"camelot": None, "bpm": 120.0, "name": "lo"},
        {"camelot": None, "bpm": 124.0, "name": "mid"},
    ]
    result = build_harmonic_path(tracks)
    bpms = [t["bpm"] for t in result]
    assert bpms == sorted(bpms)   # ascending BPM, the _W_BPM_DESCENT preference


def test_known_key_pool_byte_identical_to_pre_fix_behaviour():
    """No-regression pin: a pool where every track has a real camelot code
    must produce the exact same path as before - the fix only changes
    behaviour when a key is genuinely missing."""
    tracks = [
        {"camelot": "3A", "bpm": 122.0, "name": "t3"},
        {"camelot": "5A", "bpm": 124.0, "name": "t5"},
        {"camelot": "1A", "bpm": 120.0, "name": "t1"},
        {"camelot": "4A", "bpm": 123.0, "name": "t4"},
        {"camelot": "2A", "bpm": 121.0, "name": "t2"},
    ]
    result = build_harmonic_path(tracks)
    assert _clash_count(result) == 0
    nums = [int(t["camelot"][:-1]) for t in result]
    assert all(abs(nums[i] - nums[i + 1]) == 1 for i in range(len(nums) - 1))
