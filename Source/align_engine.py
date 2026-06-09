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

PHRASE_GRID = 16          # 16-bar phrase grid (viz gridlines)
SNAP_BARS = 4             # snap the incoming's stagger to the 4-bar (16-beat) grid the
                          # detector's section markers live on. 8 was COARSER than the
                          # markers, so a marker-aligned offset like 116 got rounded to
                          # 112 (off every marker -> lineup 0, the Jaz->Route 94 case).
                          # 4 still reaches every 8/16-bar offset, just no longer rounds a
                          # valid marker alignment off the grid.
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
    loop_windows: list = field(default_factory=list)   # (start_bar,end_bar) clean drums = drums-on/bass-off
    vocal_regions: list = field(default_factory=list)   # (start_bar,end_bar) vocals present
    fills: list = field(default_factory=list)           # (start_bar,end_bar) kick-out fills

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
    # loop_windows / vocal_regions carry [start_sec, end_sec, start_bar, end_bar] —
    # use the bar fields directly (no sec re-conversion, avoids downbeat rounding).
    loop_windows = [(w[2], w[3]) for w in sig.get("loop_windows", []) if len(w) >= 4]
    vocal_regions = [(w[2], w[3]) for w in sig.get("vocal_regions", []) if len(w) >= 4]
    fills = []
    for f in sig.get("fills", []):
        a, b = _sec_to_bar(f[0], downbeat, spb), _sec_to_bar(f[1], downbeat, spb)
        if a is not None and b is not None:
            fills.append((a, b))
    n_bars = d["n_bars"]
    last_min = max(8, round(60.0 / spb))
    return Track(
        name=d["track"], bpm=bpm, spb=spb, downbeat=downbeat, n_bars=n_bars,
        sections=secs, bass_in_bar=bass_in, bass_out_bar=bass_out,
        last_min_bars=last_min,
        bass_out_is_end=(bass_out is not None and (n_bars - bass_out) <= 4),
        loop_windows=loop_windows, vocal_regions=vocal_regions, fills=fills,
    )


@dataclass
class FillCutSpec:
    """A loop or cut around a transition's (locked) bass-swap. Track-native bars."""
    kind: str                          # 'outgoing_tail' | 'incoming_intro' | 'intro_cut'
    reps: int = 0                      # loop repeats (loops only)
    source_start_bar: float = 0.0      # clean-drum loop source (loops only)
    source_end_bar: float = 0.0
    cut_to_bar: float = 0.0            # intro_cut: drop incoming clips ending at/before this bar
    target_marker_bar: float = 0.0     # the marker being reached (audit)
    note: str = ""


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
    swap_beats: float | None = None  # absolute arrangement-beat of the bass swap (set by compute_aligned_positions)
    fills_cuts: list = field(default_factory=list)   # FillCutSpec list (loops/cuts around the swap)
    intro_cut_bars: float = 0.0
    notes: list = field(default_factory=list)


def _handoff_candidates(o: Track) -> list[tuple[float, str, str]]:
    """EVERY section boundary in the outgoing's last-minute window. The bass
    switch can be faked at ANY natural marker (Sam: drop the outgoing's bass
    whenever you like, as long as it's on a natural marker), so the switch point
    is chosen by best lineup, not by the literal bass-out. Returns
    (bar, before_label, after_label)."""
    window_start = o.n_bars - o.last_min_bars - HANDOFF_WINDOW_BARS
    cands: list[tuple[float, str, str]] = []
    for k in range(len(o.sections) - 1):
        bar = float(o.sections[k]["end_bar"])
        if window_start <= bar <= o.n_bars:
            cands.append((bar, o.sections[k]["label"], o.sections[k + 1]["label"]))
    # The real bass-out is also a candidate even if it doesn't fall on a section
    # boundary (Sam: align to where the bass naturally comes in/out — the cleanest
    # non-faked swap). Tag it so it ranks as a real bass-out.
    if o.bass_out_bar is not None and not o.bass_out_is_end and window_start <= o.bass_out_bar <= o.n_bars:
        lbl = next((s["label"] for s in o.sections
                    if s["start_bar"] <= o.bass_out_bar < s["end_bar"]), "drop")
        cands.append((o.bass_out_bar, lbl, "bass_out"))
    if not cands and len(o.sections) >= 2:   # fallback: the last boundary
        cands.append((float(o.sections[-1]["start_bar"]),
                      o.sections[-2]["label"], o.sections[-1]["label"]))
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
    """Slide the incoming so its bass-in lands on an outgoing natural marker, and
    pick the marker that maximises section-boundary lineup (energy-matched). The
    bass switch happens there — faked early on the outgoing if it's before the
    outgoing's natural bass-out. Marker-coincidence BEATS literal bass-to-bass."""
    anchor_in = i.bass_in_bar if i.bass_in_bar is not None else 0.0
    best: Alignment | None = None
    best_rank = None
    for bar, before, after in _handoff_candidates(o):
        arr_offset = round((bar - anchor_in) / SNAP_BARS) * SNAP_BARS
        overlap = o.n_bars - arr_offset
        if overlap < PHRASE_GRID:                  # need at least a phrase of blend
            continue
        score = _score_lineup(o, i, arr_offset)
        natural = before in ("drop", "build")      # high->low = a natural bass-drop point
        is_bassout = (o.bass_out_bar is not None and abs(bar - o.bass_out_bar) <= 2
                      and not o.bass_out_is_end)
        # Lineup first; then prefer a natural bass-drop / real bass-out; then longer overlap.
        rank = (score, int(natural) + int(is_bassout), overlap)
        if best_rank is None or rank > best_rank:
            best_rank = rank
            kind = f"{before}->{after}" + ("/bass_out" if is_bassout else "")
            best = Alignment(
                out_name=o.name, in_name=i.name,
                handoff_bar_out=bar, handoff_kind=kind,
                anchor_bar_in=anchor_in, arr_offset_bars=arr_offset,
                overlap_bars=overlap, score=score,
            )
    if best is None:                               # everything too short: last boundary
        bar = float(o.sections[-1]["start_bar"])
        arr_offset = round((bar - anchor_in) / SNAP_BARS) * SNAP_BARS
        best = Alignment(o.name, i.name, bar, "fallback", anchor_in, arr_offset,
                         o.n_bars - arr_offset, 0)
    # Notes
    if o.bass_out_bar is not None and not o.bass_out_is_end and best.handoff_bar_out < o.bass_out_bar - 2:
        best.notes.append(f"faked bass drop ({o.bass_out_bar - best.handoff_bar_out:.0f}b early)")
    if o.bass_out_is_end:
        best.notes.append("outgoing bass runs to end")
    if anchor_in > i.last_min_bars:
        best.notes.append("WARN: incoming bass-in past its 1st minute")
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
        # clean-drum windows (loop sources) as a green strip under the lane
        for ws, we in track.loop_windows:
            ax.add_patch(Rectangle((ws + shift, y - 0.11), we - ws, 0.08,
                         facecolor="#2e9e5b", edgecolor="none", alpha=0.85, zorder=5))

    draw_lane(o, 1.1, 0.0, f"OUT: {o.name[:46]}")
    draw_lane(i, 0.05, off, f"IN:  {i.name[:46]}")

    # loops/cuts decisions (FillCutSpec): red hatch = intro cut, green hatch = outro loop
    for fc in getattr(al, "fills_cuts", None) or []:
        if fc.kind == "intro_cut":
            ax.axvspan(off, off + fc.cut_to_bar, ymin=0.02, ymax=0.42,
                       color="#d11", alpha=0.18, zorder=3, hatch="//")
            ax.text(off + fc.cut_to_bar / 2, 0.92, f"CUT {fc.cut_to_bar:.0f}b",
                    ha="center", fontsize=7, color="#900", fontweight="bold", zorder=8)
        elif fc.kind == "outgoing_tail":
            outro = next((s for s in o.sections if s["label"] == "outro"), None)
            clen = fc.source_end_bar - fc.source_start_bar
            ext = fc.reps * clen
            x0 = outro["start_bar"] if outro else o.n_bars
            ax.axvspan(x0, x0 + ext, ymin=0.55, ymax=0.97,
                       color="#2e9e5b", alpha=0.22, zorder=3, hatch="//")
            ax.text(x0 + ext / 2, 1.96, f"LOOP {clen:.0f}b x{fc.reps}",
                    ha="center", fontsize=7, color="#176", fontweight="bold", zorder=8)

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


def _resolve_stem_key(name: str, stems: dict) -> str | None:
    """Match a propose_arrangement track name (possibly XML-escaped from the ALS,
    e.g. 'I&apos;m') to a stem-JSON key (load_track().name, real apostrophe)."""
    import html
    n = html.unescape(name)
    if n in stems:
        return n
    # Prefix fallback — but ONLY if unambiguous. Real filenames share long
    # prefixes ('... (Original Mix) SW V2' vs '... (Extended Mix) SW V2'), so a
    # first-match would silently align the wrong track. Require exactly one match,
    # else None so compute_aligned_positions raises instead of mis-aligning.
    matches = [k for k in stems if k.startswith(n[:30]) or n.startswith(k[:30])]
    return matches[0] if len(matches) == 1 else None


def pick_clean_drum_loop(track, sec_start, sec_end):
    """Pick a clean 4-bar (fallback 2-bar) drum chunk from the track's loop_windows
    (drums-on/bass-off) overlapping [sec_start, sec_end), avoiding vocals + fills.
    Returns (start_bar, end_bar) in track-native bars, else None."""
    def _blocked(a, b):
        for rs, re_ in list(track.vocal_regions) + list(track.fills):
            if a < re_ and b > rs:
                return True
        return False
    for length in (4, 2):                            # prefer 4 bars, then 2
        for ws, we in track.loop_windows:
            lo, hi = max(ws, sec_start), min(we, sec_end)
            for skip_first in (1, 0):                # skip the window's 1st bar first
                for s in range(int(lo) + skip_first, int(hi) - length + 1):
                    if not _blocked(s, s + length):
                        return (float(s), float(s + length))
    return None


def plan_fill_or_cut(o, i, al):
    """Decide loops/cuts around the LOCKED swap (Sam's rules). NEVER alters
    al.arr_offset_bars / al.swap_beats. Returns a list of FillCutSpec:
      1. incoming intro front lands in an outgoing break/fill -> CUT it to the
         drop-after-break (the intro's drums clash over the low break);
      2. else the outgoing runs out before the incoming's next marker -> LOOP the
         outgoing outro (clean drums) forward to reach it.
    (Incoming-intro looping deferred until a real case needs it.)"""
    arr = al.arr_offset_bars
    first_drop_in = next((s["start_bar"] for s in i.sections if s["label"] == "drop"), None)

    # (1) CUT — only when the intro lands in a LOW-energy break/fill (drop masks it otherwise)
    if first_drop_in and first_drop_in > 0:
        host = next((s for s in o.sections if s["start_bar"] <= arr < s["end_bar"]), None)
        if host and host["label"] in ("break", "fill"):
            cut_to = min(round((host["end_bar"] - arr) / SNAP_BARS) * SNAP_BARS, first_drop_in)
            if cut_to > 0:
                al.intro_cut_bars = float(cut_to)
                return [FillCutSpec(kind="intro_cut", cut_to_bar=float(cut_to),
                        target_marker_bar=float(host["end_bar"]),
                        note=f"intro in {host['label']} -> cut to bar {cut_to:.0f}")]

    # (2) OUTGOING-TAIL LOOP — outgoing ends before the incoming's next marker
    anchor = i.bass_in_bar or 0.0
    next_marker_in = next((s["start_bar"] for s in i.sections if s["start_bar"] > anchor + 1), None)
    if next_marker_in is not None and not o.bass_out_is_end:
        gap = round(((arr + next_marker_in) - o.n_bars) / SNAP_BARS) * SNAP_BARS
        if gap >= SNAP_BARS:
            outro = next((s for s in o.sections if s["label"] == "outro"), None)
            if outro:
                chunk = pick_clean_drum_loop(o, outro["start_bar"], outro["end_bar"])
                if chunk:
                    clen = chunk[1] - chunk[0]
                    reps = int(gap // clen)              # undershoot, never overshoot the marker
                    if reps >= 1:
                        return [FillCutSpec(kind="outgoing_tail", reps=reps,
                                source_start_bar=chunk[0], source_end_bar=chunk[1],
                                target_marker_bar=float(arr + next_marker_in),
                                note=f"loop outro {clen:.0f}bx{reps} to incoming marker")]
    return []


def compute_aligned_positions(tracks, stem_dir, order=None):
    """Bass-to-bass ABSOLUTE arrangement positions for the whole mix — a drop-in
    replacement for propose_arrangement.compute_natural_positions().

    `tracks` is the propose_arrangement TrackInfo list, already in mix order, with
    arr_start/arr_end in arrangement BEATS. Reads the SECTIONS_STEM_*.json the
    aligner understands, aligns each adjacent pair (align_pair), and accumulates
    absolute positions: arr_pos[k] = arr_pos[k-1] + arr_offset_bars*4 (anti-rewind
    clamped). bars->beats is exactly *4 (4/4); stem bar 0 == the track's downbeat
    == the sections-JSON zero point, so no per-track offset correction is needed.

    Returns (positions, alignments):
      positions  = [(name, current_arr_start, new_arr_start, delta_beats), ...]
      alignments = per-pair Alignment objects (k = transition tracks[k] -> tracks[k+1]),
                   each with .swap_beats = the absolute arrangement-beat of the bass
                   swap (outgoing's final position + handoff_bar_out*4) for apply_automation.
    """
    stem_dir = Path(stem_dir)
    stems = {}
    for j in sorted(stem_dir.glob("SECTIONS_STEM_*.json")):
        t = load_track(j)
        stems[t.name] = t

    names = order or [t.name for t in tracks]
    resolved = []
    for nm in names:
        key = _resolve_stem_key(nm, stems)
        if key is None:
            raise ValueError(
                f"align_engine.compute_aligned_positions: no stem JSON for track '{nm}' "
                f"in {stem_dir}. Run the stem detector (--stem-sections) first.")
        resolved.append(key)

    arr_pos = {0: tracks[0].arr_start}                       # keyed by INDEX (names may repeat)
    alignments = []
    for k in range(1, len(tracks)):
        o, i = stems[resolved[k - 1]], stems[resolved[k]]
        al = align_pair(o, i)
        prev = arr_pos[k - 1]
        new = max(prev + al.arr_offset_bars * 4.0, prev)     # anti-rewind clamp
        arr_pos[k] = new
        al.swap_beats = prev + al.handoff_bar_out * 4.0      # outgoing final pos + handoff
        al.fills_cuts = plan_fill_or_cut(o, i, al)           # loops/cuts around the swap
        alignments.append(al)

    positions = [(t.name, t.arr_start, arr_pos[idx], arr_pos[idx] - t.arr_start)
                 for idx, t in enumerate(tracks)]
    return positions, alignments


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
        al.fills_cuts = plan_fill_or_cut(o, i, al)
        png = out_dir / f"ALIGN_{idx:02d}_{o.name[:24]} __ {i.name[:24]}.png"
        visualize_transition(o, i, al, png, idx)
        note = ("  ! " + " ; ".join(al.notes)) if al.notes else ""
        print(f"  T{idx}: {o.name[:26]:26} -> {i.name[:26]:26}  swap@{al.handoff_bar_out:5.0f} "
              f"{al.handoff_kind:11} overlap {al.overlap_bars:4.0f}b lineup {al.score}{note}")
    print(f"-> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
