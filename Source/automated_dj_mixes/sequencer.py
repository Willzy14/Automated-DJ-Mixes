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


def apply_energy_arc(tracks: list[dict]) -> list[dict]:
    """Reorder tracks within build/peak/cooldown thirds for an energy arc.

    Each track dict should have an 'energy' key (MIK OverallEnergy, 0-10).
    Tracks without energy data keep their position.

    Skips if fewer than 4 tracks or >50% missing energy data. Within each
    third, only swaps tracks if the swap doesn't create a 15+ BPM gap.
    """
    if len(tracks) < 4:
        return tracks

    energies = [t.get("energy") for t in tracks]
    known = [e for e in energies if e is not None]
    if len(known) < len(tracks) * 0.5:
        return tracks

    n = len(tracks)
    third = n // 3
    groups = [
        list(range(0, third)),
        list(range(third, 2 * third)),
        list(range(2 * third, n)),
    ]

    def _energy_key(idx: int) -> float:
        e = tracks[idx].get("energy")
        return e if e is not None else 5.0

    def _bpm_safe(ordering: list[int]) -> bool:
        for i in range(len(ordering) - 1):
            a = tracks[ordering[i]].get("bpm")
            b = tracks[ordering[i + 1]].get("bpm")
            if a is not None and b is not None and abs(a - b) >= 15:
                return False
        return True

    build = sorted(groups[0], key=_energy_key)
    peak = sorted(groups[1], key=_energy_key, reverse=True)
    cooldown = sorted(groups[2], key=_energy_key, reverse=True)

    proposed = build + peak + cooldown

    if not _bpm_safe(proposed):
        return tracks

    return [tracks[i] for i in proposed]


def _bpm_proximity(bpm_a: float | None, bpm_b: float | None) -> float:
    """Score BPM proximity on a 0-1 scale. 0 BPM diff = 1.0, 15+ = 0.0.
    Unknown BPM returns 0.5 (neutral)."""
    if bpm_a is None or bpm_b is None:
        return 0.5
    return max(0.0, 1.0 - abs(bpm_a - bpm_b) / 15.0)


def build_harmonic_path(tracks: list[dict]) -> list[dict]:
    """Build an optimal path through all tracks using Camelot + BPM proximity.

    Each track dict must have a 'camelot' key. Optional 'bpm' key enables
    BPM proximity scoring.

    Composite score = (camelot_norm * 0.6) + (bpm_norm * 0.4)
    where camelot_norm is the raw 0-4 score divided by 4 (→ 0-1 scale).

    Starts from the slowest-BPM track and uses greedy nearest-neighbour,
    biasing toward ascending BPM (mixes naturally get faster).
    """
    if len(tracks) <= 1:
        return list(tracks)

    # Start from the slowest track so the greedy walk naturally ascends
    start = min(
        range(len(tracks)),
        key=lambda i: tracks[i].get("bpm") or 999,
    )
    remaining = [i for i in range(len(tracks)) if i != start]
    current = start
    path = [current]

    while remaining:
        cur_bpm = tracks[current].get("bpm")
        best_idx = None
        best_composite = -1.0
        for idx in remaining:
            cam_score, _ = compatibility_score(
                tracks[current]["camelot"], tracks[idx]["camelot"]
            )
            cam_norm = cam_score / 4.0
            bpm_norm = _bpm_proximity(
                cur_bpm, tracks[idx].get("bpm")
            )
            # Small ascending BPM bonus: prefer candidates at same or higher BPM
            asc_bonus = 0.0
            cand_bpm = tracks[idx].get("bpm")
            if cur_bpm and cand_bpm and cand_bpm >= cur_bpm:
                asc_bonus = 0.05
            composite = cam_norm * 0.6 + bpm_norm * 0.4 + asc_bonus
            if composite > best_composite:
                best_composite = composite
                best_idx = idx
        remaining.remove(best_idx)
        path.append(best_idx)
        current = best_idx

    return [tracks[i] for i in path]
