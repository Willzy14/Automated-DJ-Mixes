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


def test_path_picks_smooth_neighbours():
    tracks = [
        {"camelot": "5A", "name": "start"},
        {"camelot": "10B", "name": "far_away"},
        {"camelot": "6A", "name": "smooth_next"},
    ]
    result = build_harmonic_path(tracks)
    assert result[0]["name"] == "start"
    assert result[1]["name"] == "smooth_next"


def test_path_favours_smooth_chain():
    """Greedy picks smooth (+1) transitions first. After 3A→4A→5A, both
    remaining (1A, 2A) are clashes from 5A, so tie-breaks by list order."""
    tracks = [
        {"camelot": "3A", "name": "t3"},
        {"camelot": "5A", "name": "t5"},
        {"camelot": "1A", "name": "t1"},
        {"camelot": "4A", "name": "t4"},
        {"camelot": "2A", "name": "t2"},
    ]
    result = build_harmonic_path(tracks)
    names = [t["name"] for t in result]
    assert names[:3] == ["t3", "t4", "t5"]
    assert set(names[3:]) == {"t1", "t2"}
