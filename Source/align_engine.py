"""Bass-to-bass alignment engine + per-transition visualiser (TESTING tool).

Sam's arrangement model (2026-06-08):
  * The biggest natural mix point is BASS-TO-BASS — align the incoming track's
    bass-IN to the outgoing track's bass-OUT. That's where the bass swaps (a
    clean swap, not a crossfade) and, to the listener, where the tracks change.
  * The swap must sit in the outgoing's LAST minute AND the incoming's FIRST
    minute (you don't start a mix halfway through either track).
  * If the outgoing's bass runs to the very end (no bass-out), fall back to a
    natural breakpoint — end of the last fill / break, or the outro start.
  * Coinciding section boundaries after the swap are a bonus that picks between
    otherwise-equal staggers; everything snaps to the 16-bar phrase grid.

This module READS the stem detector's SECTIONS_STEM_*.json (sections + bass-in/
out signals), computes the alignment per adjacent pair, and renders one PNG per
transition so the alignment can be tuned by eye — exactly like the detector was.
The visualiser is a TESTING aid only; production arrangement stays autonomous.

Usage:
    python Source/align_engine.py "<project path>"      # all transitions in mix order
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from stem_section_probe import _seccol  # shared section colour map

PHRASE_GRID = 16          # snap staggers to 16-bar phrases
HANDOFF_WINDOW_BARS = 8   # allow a hand-off marker this far before the last-minute line
COINCIDE_TOL_BARS = 2     # two section boundaries "line up" if within this many bars
LIKE_ENERGY = {"drop": "high", "build": "high", "break": "low", "fill": "low",
               "intro": "low", "outro": "low"}


@dataclass
class Track:
    name: str
    bpm: float
    spb: float                 # seconds per bar
    downbeat: float
    n_bars: int
    sections: list             # [{label, start_bar, end_bar, ...}]
    bass_in_bar: float | None
    bass_out_bar: float | None
    last_min_bars: int = 0
    bass_out_is_end: bool = False

    def boundaries(self) -> list[tuple[float, str]]:
        """(bar, label) for every section start — the markers."""
        return [(s["start_bar"], s["label"]) for s in self.sections]


def _sec_to_bar(sec: float | None, downbeat: float, spb: float) -> float | None:
    if sec is None:
        return None
    return (sec - downbeat) / spb


def load_track(stem_json: Path) -> Track:
    d = json.loads(stem_json.read_text(encoding="utf-8"))
    bpm = d["bpm"]
    spb = 4 * 60.0 / bpm
    secs = d["sections"]
    # downbeat = where bar 0 sits in absolute track seconds
    downbeat = secs[0]["start_sec"] - secs[0]["start_bar"] * spb if secs else 0.0
    sig = d.get("signals", {})
    bass_in = _sec_to_bar(sig.get("bass_in"), downbeat, spb)
    bass_out = _sec_to_bar(sig.get("bass_out"), downbeat, spb)
    n_bars = d["n_bars"]
    last_min = max(8, round(60.0 / spb))
    return Track(
        name=d["track"], bpm=bpm, spb=spb, downbeat=downbeat, n_bars=n_bars,
        sections=secs, bass_in_bar=bass_in, bass_out_bar=bass_out,
        last_min_bars=last_min,
        bass_out_is_end=(bass_out is not None and (n_bars - bass_out) <= 4),
    )


@dataclass
class Alignment:
    out_name: str
    in_name: str
    handoff_bar_out: float       # mix point in the OUTGOING's bars
    handoff_kind: str            # bass_out | fill_end | break_end | outro_start | ...
    anchor_bar_in: float         # the INCOMING's bass-in (its mix anchor)
    arr_offset_bars: float       # where the incoming starts, relative to outgoing bar 0
    overlap_bars: float
    score: int                   # coinciding like-energy boundaries (the bonus)
    notes: list = field(default_factory=list)


def _handoff_candidates(o: Track) -> list[tuple[float, str]]:
    """Outgoing hand-off markers within the last minute (+ a little slack)."""
    window_start = o.n_bars - o.last_min_bars - HANDOFF_WINDOW_BARS
    cands: list[tuple[float, str]] = []
    # Primary: bass-out, but only if it's a real exit (not the track end).
    if o.bass_out_bar is not None and not o.bass_out_is_end and o.bass_out_bar >= window_start:
        cands.append((o.bass_out_bar, "bass_out"))
    # Fallbacks: natural breakpoints near the end.
    for s in o.sections:
        if s["label"] in ("fill", "break") and s["end_bar"] >= window_start:
            cands.append((float(s["end_bar"]), f"{s['label']}_end"))
        if s["label"] == "outro" and s["start_bar"] >= window_start:
            cands.append((float(s["start_bar"]), "outro_start"))
    if not cands:
        # last resort: outro start, else bass-out even if near the end
        outro = next((s["start_bar"] for s in o.sections if s["label"] == "outro"), None)
        cands.append((float(outro), "outro_start") if outro is not None
                     else (o.bass_out_bar or o.n_bars - o.last_min_bars, "fallback"))
    return cands


def _score_lineup(o: Track, i: Track, arr_offset: float) -> int:
    """Count like-energy section boundaries that coincide once the incoming is
    shifted by arr_offset. Drop↔drop / break↔break etc. score; mismatches don't."""
    score = 0
    o_b = o.boundaries()
    i_b = [(b + arr_offset, lbl) for b, lbl in i.boundaries()]
    for ob, ol in o_b:
        for ib, il in i_b:
            if abs(ob - ib) <= COINCIDE_TOL_BARS:
                if LIKE_ENERGY.get(ol) == LIKE_ENERGY.get(il):
                    score += 2          # like-for-like = best
                else:
                    score += 1          # any coincidence still helps
    return score


def align_pair(o: Track, i: Track) -> Alignment:
    """Align the incoming's bass-in to the best outgoing hand-off, snap to the
    phrase grid, and score the resulting lineup."""
    anchor_in = i.bass_in_bar if i.bass_in_bar is not None else 0.0
    best: Alignment | None = None
    for handoff_bar, kind in _handoff_candidates(o):
        raw_offset = handoff_bar - anchor_in
        arr_offset = round(raw_offset / PHRASE_GRID) * PHRASE_GRID
        overlap = o.n_bars - arr_offset
        score = _score_lineup(o, i, arr_offset)
        # Prefer real bass-out, then higher lineup score, then longer overlap.
        rank = (1 if kind == "bass_out" else 0, score, overlap)
        if best is None or rank > (1 if best.handoff_kind == "bass_out" else 0, best.score, best.overlap_bars):
            best = Alignment(
                out_name=o.name, in_name=i.name,
                handoff_bar_out=handoff_bar, handoff_kind=kind,
                anchor_bar_in=anchor_in, arr_offset_bars=arr_offset,
                overlap_bars=overlap, score=score,
            )
    # Constraint notes (symmetry: last minute out / first minute in)
    if o.n_bars - best.handoff_bar_out > o.last_min_bars + HANDOFF_WINDOW_BARS:
        best.notes.append("WARN: mix point not in outgoing's last minute")
    if anchor_in > i.last_min_bars:
        best.notes.append("WARN: incoming bass-in not in its first minute")
    if o.bass_out_is_end:
        best.notes.append("outgoing bass runs to end -> handed off on a breakpoint")
    return best


def visualize_transition(o: Track, i: Track, al: Alignment, out_png: Path, idx: int):
    """Draw both tracks' sections on one arrangement-bar axis, with the bass
    markers, the bass-swap mix point, and the overlap zone."""
    fig, ax = plt.subplots(figsize=(18, 3.6))
    off = al.arr_offset_bars
    x_end = max(o.n_bars, off + i.n_bars)

    def draw_lane(track: Track, y: float, shift: float, tag: str):
        for s in track.sections:
            x0, x1 = s["start_bar"] + shift, s["end_bar"] + shift
            ax.add_patch(Rectangle((x0, y), x1 - x0, 0.8, facecolor=_seccol(s["label"]),
                                    edgecolor="k", lw=0.6, alpha=0.55))
            nb = s["end_bar"] - s["start_bar"]
            ax.text((x0 + x1) / 2, y + 0.4, f"{s['label']}\n{nb}b", ha="center", va="center",
                    fontsize=6.5, fontweight="bold", zorder=6)
        ax.text(shift, y + 0.9, tag, fontsize=8, fontweight="bold", va="bottom")
        # bass in/out markers for this lane
        for bar, lab, col in ((track.bass_in_bar, "b.in", "#1f4e9e"),
                              (track.bass_out_bar, "b.out", "#1f4e9e")):
            if bar is not None:
                ax.plot([bar + shift, bar + shift], [y - 0.05, y + 0.85], color=col, lw=2.2, zorder=7)

    draw_lane(o, 1.1, 0.0, f"OUT: {o.name[:46]}")
    draw_lane(i, 0.05, off, f"IN:  {i.name[:46]}")

    # The bass-swap mix point (outgoing bar = handoff)
    mix_x = al.handoff_bar_out
    ax.axvline(mix_x, color="#c0007a", lw=2.4, ls="--", zorder=8)
    ax.text(mix_x, 2.05, f"BASS SWAP\n({al.handoff_kind})", ha="center", va="bottom",
            fontsize=8, fontweight="bold", color="#c0007a")
    # Overlap zone
    ax.axvspan(off, o.n_bars, color="#999", alpha=0.10, zorder=0)
    # Outgoing last-minute guide
    ax.axvline(o.n_bars - o.last_min_bars, color="#d6006d", lw=1.0, ls=":", alpha=0.7)

    for bar in range(0, int(x_end) + 1, PHRASE_GRID):
        ax.axvline(bar, color="#dddddd", lw=0.3, zorder=0)

    ax.set_xlim(-4, x_end + 4)
    ax.set_ylim(-0.2, 2.4)
    ax.set_yticks([])
    ax.set_xlabel("arrangement bars")
    title = (f"T{idx}: {o.name[:30]} -> {i.name[:30]}   |   swap@out-bar {mix_x:.0f} "
             f"({al.handoff_kind})  overlap {al.overlap_bars:.0f}b  lineup {al.score}")
    if al.notes:
        title += "   |   " + " ; ".join(al.notes)
    ax.set_title(title, fontsize=9)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=95)
    plt.close(fig)


def _mix_order(project: Path, stems: dict) -> list[str]:
    """Track order from the latest ARRANGEMENT_REPORT.json if present, else sorted."""
    reports = sorted((project / "Output").glob("*ARRANGEMENT_REPORT.json"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    if reports:
        try:
            rep = json.loads(reports[0].read_text(encoding="utf-8"))
            trans = rep.get("transitions", [])
            if trans:
                import html  # report names come from the ALS -> XML-escaped (&apos; etc.)
                order = [html.unescape(trans[0]["out_track"])] + [html.unescape(t["in_track"]) for t in trans]
                # map report names to stem keys (exact, else startswith)
                resolved = []
                for nm in order:
                    if nm in stems:
                        resolved.append(nm)
                    else:
                        hit = next((k for k in stems if k.startswith(nm[:30]) or nm.startswith(k[:30])), None)
                        if hit:
                            resolved.append(hit)
                if len(resolved) == len(order):
                    return resolved
        except Exception:
            pass
    return sorted(stems.keys())


def main():
    if len(sys.argv) < 2:
        print("Usage: python Source/align_engine.py \"<project path>\"")
        return 1
    project = Path(sys.argv[1])
    stem_dir = project / "_Stem Analysis"
    jsons = sorted(stem_dir.glob("SECTIONS_STEM_*.json"))
    if not jsons:
        print(f"No SECTIONS_STEM_*.json in {stem_dir} — run stem_detector first.")
        return 1
    stems = {load_track(j).name: load_track(j) for j in jsons}
    order = _mix_order(project, stems)
    out_dir = project / "_Alignment Review"
    print(f"Alignment over {len(order)} tracks ({len(order)-1} transitions):")
    for idx in range(1, len(order)):
        o, i = stems[order[idx - 1]], stems[order[idx]]
        al = align_pair(o, i)
        png = out_dir / f"ALIGN_{idx:02d}_{o.name[:24]} __ {i.name[:24]}.png"
        visualize_transition(o, i, al, png, idx)
        note = ("  ! " + " ; ".join(al.notes)) if al.notes else ""
        print(f"  T{idx}: {o.name[:26]:26} -> {i.name[:26]:26}  swap@{al.handoff_bar_out:5.0f} "
              f"{al.handoff_kind:11} overlap {al.overlap_bars:4.0f}b lineup {al.score}{note}")
    print(f"-> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
