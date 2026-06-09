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


def _count_clashes(track_dicts: list[dict]) -> int:
    """Number of adjacent Camelot clashes (compatibility_score == 0) in an order."""
    c = 0
    for i in range(len(track_dicts) - 1):
        score, _ = compatibility_score(
            track_dicts[i].get("camelot", "1A"), track_dicts[i + 1].get("camelot", "1A"))
        if score == 0:
            c += 1
    return c


def apply_energy_arc(tracks: list[dict]) -> list[dict]:
    """Reorder within build/peak/cooldown thirds for an energy arc — but ONLY if
    it does NOT break harmony. Energy is a tiebreak WITHIN harmonic constraints
    (Sam, 2026-06-09), never an override: if the energy reorder would add a
    Camelot clash, the harmonic order is kept. Also keeps the 15-BPM safety.

    Each track dict should have an 'energy' key (MIK OverallEnergy, 0-10).
    Skips if fewer than 4 tracks or >50% missing energy data.
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

    # Single-humped arc: ramp up through build, crest at the 2/3 mark, fall
    # to a quiet finish. Peak sorts ASCENDING so the loudest track lands at
    # the crest; cooldown sorts descending so energy falls to the close.
    # (Peak was descending, which made cooldown re-spike to its loudest
    # track right after the peak wound down — a sawtooth, not an arc.)
    build = sorted(groups[0], key=_energy_key)
    peak = sorted(groups[1], key=_energy_key)
    cooldown = sorted(groups[2], key=_energy_key, reverse=True)

    proposed = build + peak + cooldown
    proposed_dicts = [tracks[i] for i in proposed]

    # Harmony-preserving: reject the energy reorder if it ADDS a Camelot clash.
    if _count_clashes(proposed_dicts) > _count_clashes(tracks):
        return tracks
    if not _bpm_safe(proposed):
        return tracks

    return proposed_dicts


def _bpm_proximity(bpm_a: float | None, bpm_b: float | None) -> float:
    """Score BPM proximity on a 0-1 scale. 0 BPM diff = 1.0, 15+ = 0.0.
    Unknown BPM returns 0.5 (neutral)."""
    if bpm_a is None or bpm_b is None:
        return 0.5
    return max(0.0, 1.0 - abs(bpm_a - bpm_b) / 15.0)


# Cost-weight hierarchy (strict): avoid clashes >> smoother transitions >>
# ascending BPM >> close BPM. The big separations guarantee the priority order
# so harmony can never be traded away for tempo.
_W_CLASH = 1_000_000.0
_W_SMOOTH = 1_000.0
_W_BPM_DESCENT = 1.0
_W_BPM_DIST = 0.01


def _edge_cost(a: dict, b: dict) -> float:
    """Transition cost a->b. Lower is better. Clash dominates; then transition
    smoothness (identical < smooth < power_mix); then BPM descent (mixes should
    get faster); then raw BPM distance."""
    score, _ = compatibility_score(a.get("camelot", "1A"), b.get("camelot", "1A"))
    cost = (_W_CLASH if score == 0 else 0.0) + _W_SMOOTH * (4 - score)
    bpm_a, bpm_b = a.get("bpm"), b.get("bpm")
    if bpm_a and bpm_b:
        if bpm_b < bpm_a:
            cost += _W_BPM_DESCENT * (bpm_a - bpm_b)   # penalise dropping tempo
        cost += _W_BPM_DIST * abs(bpm_a - bpm_b)
    return cost


def _held_karp_path(tracks: list[dict]) -> list[int]:
    """Exact min-cost Hamiltonian path (free start/end) over _edge_cost via
    Held-Karp DP. O(2^n * n^2) — fine for realistic mix sizes (<=15)."""
    import math
    n = len(tracks)
    cost = [[_edge_cost(tracks[i], tracks[j]) if i != j else 0.0
             for j in range(n)] for i in range(n)]
    size = 1 << n
    dp = [[math.inf] * n for _ in range(size)]
    par = [[-1] * n for _ in range(size)]
    for j in range(n):
        dp[1 << j][j] = 0.0
    for mask in range(size):
        for j in range(n):
            base = dp[mask][j]
            if base == math.inf or not (mask & (1 << j)):
                continue
            for k in range(n):
                if mask & (1 << k):
                    continue
                nm = mask | (1 << k)
                c = base + cost[j][k]
                if c < dp[nm][k]:
                    dp[nm][k] = c
                    par[nm][k] = j
    full = size - 1
    best_j = min(range(n), key=lambda j: dp[full][j])
    path, mask, j = [], full, best_j
    while j != -1:
        path.append(j)
        nj = par[mask][j]
        mask ^= (1 << j)
        j = nj
    return path[::-1]


def _greedy_path(tracks: list[dict]) -> list[int]:
    """Greedy nearest-neighbour fallback for large track counts (>15). Starts
    slowest so the walk ascends; picks the lowest-cost next track each step."""
    start = min(range(len(tracks)), key=lambda i: tracks[i].get("bpm") or 999)
    remaining = [i for i in range(len(tracks)) if i != start]
    path = [start]
    while remaining:
        cur = path[-1]
        nxt = min(remaining, key=lambda idx: _edge_cost(tracks[cur], tracks[idx]))
        remaining.remove(nxt)
        path.append(nxt)
    return path


def build_harmonic_path(tracks: list[dict]) -> list[dict]:
    """Order tracks HARMONY-FIRST: minimise Camelot clashes, then prefer smoother
    transitions, then ascending BPM, then close BPM (see _edge_cost). Uses an
    EXACT optimal Held-Karp path for realistic mix sizes (<=15 tracks); falls back
    to greedy nearest-neighbour above that. Replaces the old greedy walk that got
    stuck in local optima and left avoidable clashes.

    Each track dict must have a 'camelot' key; optional 'bpm' enables tempo bias.
    """
    if len(tracks) <= 1:
        return list(tracks)
    path = _held_karp_path(tracks) if len(tracks) <= 15 else _greedy_path(tracks)
    return [tracks[i] for i in path]
