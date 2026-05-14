"""Camelot wheel logic and harmonic path optimisation."""

from dataclasses import dataclass

CAMELOT_WHEEL = {
    "C major": "8B", "A minor": "8A",
    "G major": "9B", "E minor": "9A",
    "D major": "10B", "B minor": "10A",
    "A major": "11B", "F# minor": "11A",
    "E major": "12B", "C# minor": "12A",
    "B major": "1B", "G# minor": "1A",
    "F# major": "2B", "Eb minor": "2A",
    "Db major": "3B", "Bb minor": "3A",
    "Ab major": "4B", "F minor": "4A",
    "Eb major": "5B", "C minor": "5A",
    "Bb major": "6B", "G minor": "6A",
    "F major": "7B", "D minor": "7A",
}


def key_to_camelot(key: str) -> str | None:
    """Convert a musical key string to its Camelot code."""
    return CAMELOT_WHEEL.get(key)


def is_compatible(camelot_a: str, camelot_b: str) -> tuple[bool, str]:
    """Check if two Camelot codes are compatible for mixing. Returns (compatible, type)."""
    raise NotImplementedError


def build_harmonic_path(tracks: list) -> list:
    """Build an optimal harmonic path through all tracks using Camelot rules."""
    raise NotImplementedError
