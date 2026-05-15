"""Camelot wheel logic and harmonic path optimisation."""

from __future__ import annotations

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

CAMELOT_ALIASES = {
    "Gbm": "F# minor", "Abm": "G# minor", "Dbm": "C# minor",
    "Bbm": "Bb minor", "Ebm": "Eb minor",
    "Gb": "F# major", "Db": "Db major", "Ab": "Ab major",
    "Eb": "Eb major", "Bb": "Bb major",
    "Am": "A minor", "Bm": "B minor", "Cm": "C minor",
    "Dm": "D minor", "Em": "E minor", "Fm": "F minor",
    "Gm": "G minor", "F#m": "F# minor", "G#m": "G# minor",
    "C#m": "C# minor",
    "C": "C major", "D": "D major", "E": "E major",
    "F": "F major", "G": "G major", "A": "A major",
    "B": "B major", "F#": "F# major",
}


def _parse_camelot(code: str) -> tuple[int, str]:
    """Parse '8A' into (8, 'A')."""
    letter = code[-1]
    number = int(code[:-1])
    return number, letter


def _camelot_distance(num_a: int, num_b: int) -> int:
    """Shortest distance around the 12-position wheel."""
    diff = abs(num_a - num_b)
    return min(diff, 12 - diff)


def _is_camelot_code(key: str) -> bool:
    """Check if a string is already a valid Camelot code (e.g. '8A', '12B')."""
    if len(key) < 2 or key[-1] not in ("A", "B"):
        return False
    try:
        n = int(key[:-1])
        return 1 <= n <= 12
    except ValueError:
        return False


def key_to_camelot(key: str) -> str | None:
    """Convert a musical key string to its Camelot code. Handles common aliases and raw Camelot codes."""
    if _is_camelot_code(key):
        return key
    result = CAMELOT_WHEEL.get(key)
    if result:
        return result
    canonical = CAMELOT_ALIASES.get(key)
    if canonical:
        return CAMELOT_WHEEL.get(canonical)
    return None


def compatibility_score(camelot_a: str, camelot_b: str) -> tuple[int, str]:
    """Score how well two Camelot codes mix. Returns (score, transition_type).

    Scores:
        4 = identical key
        3 = ±1 number same letter (smooth) or same number A↔B (relative key)
        2 = ±2 number same letter (power mix)
        1 = ±1 number different letter (diagonal)
        0 = clash
    """
    num_a, let_a = _parse_camelot(camelot_a)
    num_b, let_b = _parse_camelot(camelot_b)
    dist = _camelot_distance(num_a, num_b)
    same_letter = let_a == let_b

    if dist == 0 and same_letter:
        return 4, "identical"
    if dist == 1 and same_letter:
        return 3, "smooth"
    if dist == 0 and not same_letter:
        return 3, "relative_key"
    if dist == 2 and same_letter:
        return 2, "power_mix"
    if dist == 1 and not same_letter:
        return 1, "diagonal"
    return 0, "clash"


def is_compatible(camelot_a: str, camelot_b: str) -> tuple[bool, str]:
    """Check if two Camelot codes are compatible for mixing."""
    score, transition_type = compatibility_score(camelot_a, camelot_b)
    return score >= 1, transition_type


def build_harmonic_path(tracks: list[dict]) -> list[dict]:
    """Build an optimal harmonic path through all tracks using Camelot rules.

    Each track dict must have a 'camelot' key. Greedy nearest-neighbour:
    start with the first track, always pick the highest-scoring unused
    neighbour. Ties broken by order in original list.

    Returns the tracks in optimised order.
    """
    if len(tracks) <= 1:
        return list(tracks)

    remaining = list(range(len(tracks)))
    current = remaining.pop(0)
    path = [current]

    while remaining:
        best_idx = None
        best_score = -1
        for idx in remaining:
            score, _ = compatibility_score(
                tracks[current]["camelot"], tracks[idx]["camelot"]
            )
            if score > best_score:
                best_score = score
                best_idx = idx
        remaining.remove(best_idx)
        path.append(best_idx)
        current = best_idx

    return [tracks[i] for i in path]
