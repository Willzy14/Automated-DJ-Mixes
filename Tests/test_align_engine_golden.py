"""Golden-mix regression test — locks align_engine's arrangement to the validated
08.06.26 mix.

In-Key Mix V16 matched Sam's hand-edited ALS BYTE-FOR-BYTE (2026-06-09): bass-to-bass
positions, the locked swap points, the bidirectional intro/outro loops (whole-phrase
outros), and the break-skip + downstream contraction. These assertions freeze that
result, so any future change to align_engine that shifts a position, a swap, a loop,
or the break-skip fails loudly here instead of silently breaking a mix.

Skips if the 08.06.26 stem JSONs aren't present (the golden fixture). Run with
`PYTHONPATH=Source python -m pytest Tests/test_align_engine_golden.py`.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))
PROJ = ROOT / "Test Project" / "08.06.26 Mix"
STEM_DIR = PROJ / "_Stem Analysis"

pytestmark = pytest.mark.skipif(
    not list(STEM_DIR.glob("SECTIONS_STEM_*.json")),
    reason="08.06.26 stem JSONs not present (golden fixture)",
)

# Validated 2026-06-09: matches In-Key Mix V16, which matched Sam's hand-edited
# break-to-break ALS byte-for-byte (10/10 tracks).
GOLDEN_SWAPS = [528, 1136, 1776, 2416, 3024, 3552, 3952, 4400, 4880]
GOLDEN_BREAK_SKIP_PAIRS = [2]   # T2 Call Me (short no-kick break); Sinners' 32-bar break kept
GOLDEN_INTRO_LOOPS = 6
GOLDEN_OUTRO_LOOPS = 9          # one per transition


def _arrange():
    from align_engine import load_track, compute_aligned_positions, _mix_order
    stems = {load_track(p).name: load_track(p)
             for p in STEM_DIR.glob("SECTIONS_STEM_*.json")}
    order = _mix_order(PROJ, stems)
    tracks = [SimpleNamespace(name=n, arr_start=0.0, arr_end=0.0) for n in order]
    return compute_aligned_positions(tracks, STEM_DIR, order=order)


def test_golden_swap_beats():
    """The 9 locked mix points — encodes the bass-to-bass positions AND the break-skip
    downstream contraction. The single strongest regression signal."""
    _, alignments = _arrange()
    swaps = [round(a.swap_beats) for a in alignments]
    assert swaps == GOLDEN_SWAPS, f"swap_beats drifted from the validated mix: {swaps}"


def test_golden_break_skip_only_at_t2():
    """The short, no-kick break-on-break is skipped only at T2 (Call Me, 8-bar break);
    Sinners' 32-bar breakdown (T4) is kept by the length cap."""
    _, alignments = _arrange()
    fired = [i + 1 for i, a in enumerate(alignments)
             if any(f.kind == "break_skip" for f in a.fills_cuts)]
    assert fired == GOLDEN_BREAK_SKIP_PAIRS, f"break-skip fired at {fired}, expected {GOLDEN_BREAK_SKIP_PAIRS}"


def test_golden_loop_layer():
    """Bidirectional loops: an outro loop on every transition, intro loops on the six
    that need one. Catches the loop layer silently switching off."""
    _, alignments = _arrange()
    intro = sum(any(f.kind == "incoming_intro" for f in a.fills_cuts) for a in alignments)
    outro = sum(any(f.kind == "outgoing_tail" for f in a.fills_cuts) for a in alignments)
    assert outro == GOLDEN_OUTRO_LOOPS, f"expected {GOLDEN_OUTRO_LOOPS} outro loops, got {outro}"
    assert intro == GOLDEN_INTRO_LOOPS, f"expected {GOLDEN_INTRO_LOOPS} intro loops, got {intro}"


def test_align_pair_pure_invariants():
    """align_pair is a pure function — the first adjacent pair returns a sane
    bass-to-bass alignment (incoming starts after the outgoing's origin; handoff set)."""
    from align_engine import load_track, align_pair, _mix_order
    stems = {load_track(p).name: load_track(p)
             for p in STEM_DIR.glob("SECTIONS_STEM_*.json")}
    order = _mix_order(PROJ, stems)
    al = align_pair(stems[order[0]], stems[order[1]])
    assert al.arr_offset_bars > 0, "incoming should enter after the outgoing's start"
    assert al.handoff_bar_out >= 0
