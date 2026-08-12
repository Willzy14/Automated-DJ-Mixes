"""Which track pairs can the arranger actually align, and what running order works?

Some pairs are geometrically unmixable: a short outgoing track against an
incoming whose first drop is late forces an overlap above the ceiling, so every
candidate is rejected and the whole mix fails to build. Discovering that one
pair at a time - by running the arranger, reading the exception, swapping a
track and re-running - is slow and tells you nothing about the alternatives.

This builds the full pairwise feasibility matrix from the section maps, then
finds the longest BPM-ascending chain whose every adjacent pair is alignable,
so a workable running order can be chosen before any mix is built.

Usage:
    python Source/alignment_feasibility.py "<project path>" [--policy interim_v1]
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

from align_engine import _align_pair_landmark_aware, load_track
from automated_dj_mixes.transition_policy import get_policy


def _bpm(path: Path) -> float:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0.0
    for key in ("bpm", "grid_bpm", "tempo", "source_grid_bpm"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return 0.0


def load_tracks(project: Path):
    paths = sorted(
        Path(p) for p in glob.glob(str(project / "_Stem Analysis" / "SECTIONS_STEM_*.json"))
    )
    if not paths:
        raise SystemExit(f"No section maps under {project / '_Stem Analysis'}")
    tracks = []
    for path in paths:
        track = load_track(path)
        tracks.append((track, _bpm(path)))
    return tracks


def feasible(outgoing, incoming, policy) -> bool:
    try:
        _align_pair_landmark_aware(outgoing, incoming, policy)
        return True
    except ValueError:
        return False
    except Exception:
        return False


def longest_ascending_chain(order, ok) -> list[int]:
    """Longest chain over BPM-sorted indices where each adjacent pair aligns."""
    n = len(order)
    best = [1] * n
    prev = [-1] * n
    for b in range(n):
        for a in range(b):
            if ok[a][b] and best[a] + 1 > best[b]:
                best[b] = best[a] + 1
                prev[b] = a
    end = max(range(n), key=lambda idx: best[idx])
    chain = []
    while end != -1:
        chain.append(end)
        end = prev[end]
    return list(reversed(chain))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--policy", default="interim_v1")
    args = parser.parse_args()

    policy = get_policy(args.policy)
    tracks = load_tracks(args.project)
    tracks.sort(key=lambda item: item[1])          # BPM ascending (Sam's rule)
    names = [t.name for t, _ in tracks]
    n = len(tracks)

    print(f"{n} tracks, policy={args.policy}\n")
    ok = [[False] * n for _ in range(n)]
    for a in range(n):
        for b in range(n):
            if a != b:
                ok[a][b] = feasible(tracks[a][0], tracks[b][0], policy)

    print("Feasible as OUTGOING -> INCOMING (BPM ascending, . = no):")
    print("     " + "".join(f"{i:>3}" for i in range(n)))
    for a in range(n):
        row = "".join("  Y" if ok[a][b] else ("  -" if a == b else "  .")
                      for b in range(n))
        print(f"{a:>3}  {row}   {names[a][:40]}  {tracks[a][1]:.1f}")

    dead_in = [i for i in range(n) if not any(ok[a][i] for a in range(n))]
    dead_out = [i for i in range(n) if not any(ok[i][b] for b in range(n))]
    if dead_in:
        print("\nNever usable as INCOMING: "
              + ", ".join(f"{i} {names[i][:34]}" for i in dead_in))
    if dead_out:
        print("Never usable as OUTGOING: "
              + ", ".join(f"{i} {names[i][:34]}" for i in dead_out))

    chain = longest_ascending_chain(list(range(n)), ok)
    print(f"\nLongest BPM-ascending alignable chain: {len(chain)} tracks")
    for position, idx in enumerate(chain, 1):
        print(f"  {position:>2}. [{tracks[idx][1]:6.2f}] {names[idx][:58]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
