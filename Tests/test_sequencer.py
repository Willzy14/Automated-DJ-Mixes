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
