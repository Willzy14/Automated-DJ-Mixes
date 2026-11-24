"""Replay a hand-tweaked mix through the blend planner (Tools/score_tweak.py is the CLI).

For each transition the placement in two Ableton sets (the planner's own set and the owner's
hand-edited copy) is decoded back into the planner's terms (the BlendPlan fields, bars in the
OUTGOING track's own numbering), then the owner's placement is put through phrase_blend:
is it the planner's top pick, one of its alternatives, ranked N of M planner candidates, scored but
not among the candidates, or a shape the planner cannot express at all.

Nothing here changes phrase_blend; it only calls it.  What this does NOT do: fit a scorer.  The
patterns it prints are observations with bar numbers, not rules.

Decoding rests on three facts about these sets (the round trip checks them on the planner's own set):
  - an arranged clip plays source beat `src0 + (t - arr0)` at arrangement beat t, one beat per
    beat, and planner bar = source beat / 4 (the detector clock, bar 0 = the downbeat);
  - the bass swap is the incoming's low-band automation leaving its floor (else the outgoing's
    falling edge); every edge in the window is listed, and a staggered handover is scored both ways;
  - everything else (entry, intro trim, intro loop, tail loop, stop) is read from where clips start,
    replay or end, and the outgoing's volume fade is read into the end bar.
The planner's own granularity is the half bar (fill starts sit on .5), so decoded values snap to the
nearest half bar: "exact" within a quarter beat, "approximate" within a beat, and a value that sits a
beat from both neighbours (or any shape or ramp the planner has no word for) is "not decodable":
the decoder refuses rather than guesses.

ROUND TRIP: decoding the planner's own set must reproduce the planner's own plan on every field the
decoder produces (entry, swap, end, incoming bar at the swap, intro loop, intro trim) to within one
beat, with no shape gap, or the decoder is not trusted and nothing is scored (RoundTripError).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

import phrase_blend as pb
from extract_sections_als import parse_sections_als
from learn_from_correction import (decompress_als, extract_track_automation, find_track_line_ranges,
                                   ordered_tracks_from_clip_records)

BEAT = 0.25            # one beat, in bars
EXACT_BARS = 0.0625    # a quarter beat: closer than this to the half-bar grid is "exact"
JOIN_BEATS = 0.1       # clips closer than this in time and source are one continuous play
MATCH_BARS = 0.125     # two decoded placements are "the same" within half a beat
ROUND_TRIP_BARS = 0.25  # the round trip must agree "to within one beat"
SWAP_WINDOW_BEATS = 4.0
TRIVIAL_BARS = 16.0
LONG_RAMP_BEATS = 8.0   # a bass move longer than two bars is a fade, not a swap
PREVIEW_BEATS = 16.0    # an incoming bass rise that falls back within four bars is a preview, not the swap
RISE_STEP = 0.1         # a point this far above the floor starts a rise
SILENT = 0.02           # an outgoing volume at or under this is silence
FADE_EARLY_BEATS = 4.0  # silence this much before the last clip ends is read into the end bar

K_TOP, K_ALT, K_RANKED = "top pick", "listed alternative", "ranked"
K_SHAPE = "shape not expressible"
K_OUT = "scored but not among the planner's candidates"
K_UND, K_NOSCORE = "not decodable", "not scoreable"
TIE_RULE = ("candidates are ordered by sane-pairing tier (a swap lined up on a phrase point of both tracks, "
            "bottom end kept, no forbidden cut), then cost, then no intro loop, then less tail loop, "
            "then the earlier entry, swap and end")


class RoundTripError(RuntimeError):
    """The decoder could not reproduce the planner's own placement."""

    def __init__(self, message, rows=None):
        super().__init__(message)
        self.rows = rows or []


def grain(x):
    """The planner's own granularity: whole and half bars."""
    return round(x * 2.0) / 2.0 + 0.0


# ---- reading an ALS ---------------------------------------------------------------------------
@dataclass
class Seg:
    arr0: float
    arr1: float
    src0: float
    src1: float


def make_segments(clips):
    """Merge a track's clips into continuous plays.  Returns (segments, problems)."""
    raw, problems = [], []
    for c in clips:
        a0, a1 = c.get("arr_time"), c.get("arr_end")
        s0, s1 = c.get("source_start_beats"), c.get("source_end_beats")
        if None in (a0, a1, s0, s1) or a1 - a0 <= 0.01:
            continue
        if abs((a1 - a0) - (s1 - s0)) > JOIN_BEATS:
            problems.append("clip %s: arranged %.3f beats but source %.3f beats (a looped or stretched clip)"
                            % (c.get("name"), a1 - a0, s1 - s0))
        raw.append(Seg(float(a0), float(a1), float(s0), float(s0) + (float(a1) - float(a0))))
    raw.sort(key=lambda g: (g.arr0, g.src0))
    segs = []
    for g in raw:
        if segs and abs(segs[-1].arr1 - g.arr0) <= JOIN_BEATS and abs(segs[-1].src1 - g.src0) <= JOIN_BEATS:
            segs[-1].arr1, segs[-1].src1 = g.arr1, g.src1
        else:
            if segs and g.arr0 < segs[-1].arr1 - JOIN_BEATS:
                problems.append("clips overlap on the timeline at beat %.2f" % g.arr0)
            segs.append(g)
    return segs, problems


def read_als(path):
    """[{name, segs, bass, vol, problems}] in arrangement order (learn_from_correction's parsers)."""
    path = Path(path)
    infos = ordered_tracks_from_clip_records(parse_sections_als(path))
    lines = decompress_als(path)
    autos = {html.unescape(k): v for k, v in
             extract_track_automation(lines, find_track_line_ranges(lines)).items()}
    out = []
    for t in infos:
        segs, problems = make_segments(t.clips)
        a = autos.get(t.name)
        out.append({"name": t.name, "segs": segs, "problems": problems,
                    "bass": sorted(a.bass_points, key=lambda p: p[0]) if a else [],
                    "vol": sorted(a.volume_points, key=lambda p: p[0]) if a else []})
    return out


# ---- plays: natural, replay, skip --------------------------------------------------------------
@dataclass
class Play:
    seg: Seg
    kind: str             # natural | replay | skip
    top: float            # furthest source beat played before this play
    gap_from: float = 0.0  # skip: where the previous play stopped

    @property
    def replay_beats(self):
        return max(0.0, min(self.seg.src1, self.top) - self.seg.src0) if self.kind == "replay" else 0.0

    @property
    def new_beats(self):
        return max(0.0, self.seg.src1 - max(self.top, self.seg.src0)) if self.kind == "replay" else 0.0


def classify(segs):
    plays, top = [], None
    for g in segs:
        if top is None:
            plays.append(Play(g, "natural", g.src0))
            top = g.src1
        elif g.src0 < top - JOIN_BEATS:
            plays.append(Play(g, "replay", top))
            top = max(top, g.src1)
        elif g.src0 > top + JOIN_BEATS:
            plays.append(Play(g, "skip", top, gap_from=top))
            top = g.src1
        else:
            plays.append(Play(g, "natural", top))
            top = max(top, g.src1)
    return plays


def locate(plays, t):
    """(source beat, play, on_replayed_part) at arrangement beat t, or None outside every clip."""
    hit = next((p for p in plays if p.seg.arr0 <= t < p.seg.arr1), None)
    if hit is None:
        hit = next((p for p in plays if p.seg.arr0 - JOIN_BEATS <= t <= p.seg.arr1 + JOIN_BEATS), None)
    if hit is None:
        return None
    src = hit.seg.src0 + (t - hit.seg.arr0)
    return src, hit, hit.kind == "replay" and (t - hit.seg.arr0) < hit.replay_beats - JOIN_BEATS


# ---- decoding one transition -------------------------------------------------------------------
@dataclass
class Placement:
    a: float | None = None
    s: float | None = None
    e: float | None = None
    k: float | None = None            # incoming bar heard at the swap
    loop_l: float = 0.0
    loop_p: float = 0.0
    cut: float = 0.0
    out_loop_src: tuple | None = None
    exact: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    shape_gaps: list = field(default_factory=list)   # not expressible as a BlendPlan: never scored
    model_gaps: list = field(default_factory=list)   # scored, but the planner does not model it
    edges: list = field(default_factory=list)        # every bass edge found in the blend window
    ambiguity: str | None = None                     # more than one candidate edge for the swap
    consistency: dict = field(default_factory=dict)  # the decoder's own checks: name -> True/False
    alt: "Placement | None" = None                   # the other reading of a staggered handover
    alt_label: str = ""
    undecodable: str | None = None

    @property
    def scoreable(self):
        return self.undecodable is None and not self.shape_gaps

    def key(self):
        return (self.a, self.s, self.e, self.loop_l, self.loop_p, self.cut)

    def to_json(self):
        out = {"entry": self.a, "swap": self.s, "end": self.e, "swap_incoming_bar": self.k,
               "intro_loop_bars": self.loop_l, "intro_loop_phrase_end": self.loop_p,
               "intro_cut_bars": self.cut, "tail_loop_source": self.out_loop_src,
               "precision": dict(self.exact), "raw_bars": {k: round(v, 4) for k, v in self.raw.items()},
               "notes": list(self.notes), "shape_gaps": list(self.shape_gaps),
               "model_gaps": list(self.model_gaps), "bass_edges": list(self.edges),
               "swap_edge_ambiguity": self.ambiguity, "consistency_checks": dict(self.consistency),
               "not_decodable": self.undecodable}
        if self.alt is not None:
            out["alternative_reading"] = {"label": self.alt_label, **self.alt.to_json()}
        return out


def _bar(pl, name, beats_or_bars, *, beats=False):
    """Snap to the half-bar grid: exact within EXACT_BARS, approximate within a beat, else refuse."""
    x = beats_or_bars / 4.0 if beats else beats_or_bars
    r = grain(x)
    d = abs(x - r)
    pl.raw[name] = x
    if d <= EXACT_BARS:
        pl.exact[name] = "exact"
    elif d < BEAT - 1e-6:
        pl.exact[name] = "approximate"
    else:
        pl.undecodable = pl.undecodable or ("%s is %.2f bars, a beat from both neighbours on the half-bar grid: "
                                            "not decodable" % (name, x))
        return None
    return r


def _rises(points, lo, hi):
    """Every incoming bass rise in the window: leaves the floor, follows the ramp up, flags a short preview."""
    out, n, i = [], len(points), 1
    while i < n:
        (t0, v0), (t1, v1) = points[i - 1], points[i]
        if v0 < 0.5 and v1 > v0 + RISE_STEP and (lo <= t0 <= hi or lo <= t1 <= hi):
            j = i
            while j + 1 < n and points[j][1] < 0.9 and points[j + 1][1] > points[j][1] + 1e-9:
                j += 1                          # follow the ramp up until it reaches full, then stop
            t_end, level = points[j]
            preview = any(points[k][1] < 0.5 for k in range(j + 1, n) if points[k][0] - t_end <= PREVIEW_BEATS)
            out.append({"kind": "rise", "t0": t0, "t1": t_end, "level": level, "dur": t_end - t0,
                        "preview": preview})
            i = j + 1
            continue
        i += 1
    return out


def _falls(points, lo, hi):
    """Every outgoing bass fall in the window: from near full to below 0.8, following the ramp down."""
    out, n, i = [], len(points), 1
    while i < n:
        (t0, v0), (t1, v1) = points[i - 1], points[i]
        if v0 >= 0.8 and v1 < 0.8 and (lo <= t0 <= hi or lo <= t1 <= hi):
            j = i
            while j + 1 < n and points[j][1] > 0.3 and points[j + 1][1] < points[j][1] - 1e-9:
                j += 1                          # follow the ramp down until it reaches the floor
            t_end, level = points[j]
            out.append({"kind": "fall", "t0": t0, "t1": t_end, "level": level, "dur": t_end - t0, "preview": False})
            i = j + 1
            continue
        i += 1
    return out


def _silent_at(vol, from_arr):
    """First arrangement beat after from_arr where the volume reaches silence after being audible."""
    audible = False
    for tm, v in vol:
        if tm < from_arr - 1.0:
            continue
        if v > SILENT:
            audible = True
        elif audible:
            return tm
    return None


def _check_gaps(plays, entry_arr, end_arr, who, pl):
    """A silent hole between clips inside the blend is a shape the planner has no word for."""
    for prev, cur in zip(plays, plays[1:]):
        if cur.seg.arr0 > prev.seg.arr1 + JOIN_BEATS and prev.seg.arr1 >= entry_arr - JOIN_BEATS \
                and cur.seg.arr0 <= end_arr + JOIN_BEATS:
            pl.shape_gaps.append("the %s has a %g-bar hole between clips at timeline bar %g"
                                 % (who, round((cur.seg.arr0 - prev.seg.arr1) / 4.0, 2), prev.seg.arr1 / 4.0))


def _out_bar(o_plays, arr):
    hit = locate(o_plays, arr)
    return None if hit is None else round(hit[0] / 4.0, 2)


def _pick_edge(edges, o_plays, ref_bar):
    """The edge to read: nearest the reference swap bar when one is given, else the biggest move, else the first."""
    if not edges:
        return None
    if ref_bar is not None:
        def dist(e):
            b = _out_bar(o_plays, e["t0"])
            return abs(b - ref_bar) if b is not None else 1e9
        return min(edges, key=lambda e: (dist(e), e["t0"]))
    return min(edges, key=lambda e: (-abs(e["level"] - (0.18 if e["kind"] == "rise" else 1.0)), e["t0"]))


def _read_swap(pl, o_plays, i_plays, swap_arr, label):
    """Fill s and k at an arrangement beat; refuse a swap that sits on a replay."""
    oh, ih = locate(o_plays, swap_arr), locate(i_plays, swap_arr)
    if oh is None or ih is None:
        pl.undecodable = pl.undecodable or "the swap lies outside a clip"
        return
    if oh[2]:
        pl.undecodable = pl.undecodable or "the swap lands on a replay of the outgoing"
        return
    if ih[2]:
        pl.undecodable = pl.undecodable or "the swap lands inside the incoming's second pass of a repeated stretch"
        return
    pl.s = _bar(pl, "swap", oh[0], beats=True)
    pl.k = _bar(pl, "incoming swap bar", ih[0], beats=True)
    pl.notes.append("swap read from the %s at arrangement beat %.2f" % (label, swap_arr))


def _consistency(pl):
    if pl.undecodable or pl.s is None or pl.a is None or pl.k is None:
        return
    expect_k = pl.s - pl.a - pl.loop_l + pl.cut
    ok = abs(pl.k - expect_k) <= BEAT
    pl.consistency["incoming bar at the swap = swap - entry - loop + trim"] = ok
    if not ok:
        pl.shape_gaps.append("the incoming bar at the swap (%g) is not entry, loop and trim made it (%g): "
                             "something between entry and swap is not representable" % (pl.k, expect_k))


def decode_transition(out_rec, in_rec, n_out=None, ref_swap_bar=None):
    """The placement of in_rec entering over out_rec, in the planner's terms.  n_out is the outgoing's natural
    end bar (needed to tell a tail loop from a cut outro); ref_swap_bar is the planner's own swap bar, used
    only to choose between several bass edges."""
    pl = Placement()
    for rec in (out_rec, in_rec):
        if rec["problems"]:
            pl.undecodable = "%s: %s" % (rec["name"], rec["problems"][0])
            return pl
    o_plays, i_plays = classify(out_rec["segs"]), classify(in_rec["segs"])
    if not o_plays or not i_plays:
        pl.undecodable = "a track has no usable clips"
        return pl
    entry_arr = i_plays[0].seg.arr0
    end_arr = o_plays[-1].seg.arr1
    if entry_arr >= end_arr - JOIN_BEATS:
        pl.undecodable = "the incoming starts after the outgoing ends (no overlap)"
        return pl

    cut_raw = i_plays[0].seg.src0 / 4.0
    if cut_raw < -EXACT_BARS:
        pl.undecodable = "the incoming starts %.2f bars before its source bar 0: not decodable" % -cut_raw
        return pl
    pl.cut = max(0.0, _bar(pl, "intro cut", i_plays[0].seg.src0, beats=True) or 0.0)
    hit = locate(o_plays, entry_arr)
    if hit is None or hit[2]:
        pl.undecodable = pl.undecodable or "the entry does not land on a natural stretch of the outgoing"
        return pl
    pl.a = _bar(pl, "entry", hit[0], beats=True)
    if pl.undecodable:
        return pl

    # incoming intro loop and other changes inside the blend
    first = i_plays[0]
    inside = [p for p in i_plays[1:] if p.seg.arr0 < end_arr - JOIN_BEATS]
    for n, p in enumerate(inside):
        if (n == 0 and p.kind == "replay" and p.seg.src0 >= first.seg.src0 - JOIN_BEATS
                and abs(p.seg.arr0 - first.seg.arr1) <= JOIN_BEATS and p.seg.src0 < first.seg.src1 - JOIN_BEATS):
            pl.loop_l = _bar(pl, "intro loop", first.seg.src1 - p.seg.src0, beats=True) or 0.0
            pl.loop_p = _bar(pl, "intro loop end", first.seg.src1, beats=True) or 0.0
            if p.seg.src1 < first.seg.src1 - JOIN_BEATS:
                pl.shape_gaps.append("the intro loop stops before its phrase end (source bar %g)"
                                     % (p.seg.src1 / 4.0))
        elif p.kind == "replay":
            pl.shape_gaps.append("the incoming repeats source bars %g-%g at timeline bar %g, not as one intro loop"
                                 % (p.seg.src0 / 4.0, min(p.seg.src1, p.top) / 4.0, p.seg.arr0 / 4.0))
        elif p.kind == "skip":
            pl.shape_gaps.append("the incoming skips source bars %g-%g inside the blend"
                                 % (p.gap_from / 4.0, p.seg.src0 / 4.0))
    if pl.loop_l and pl.cut:
        pl.shape_gaps.append("an intro trim and an intro loop on the same incoming (phrase_blend never offers both "
                             "and the pipeline's decision reader refuses an intro loop with a trim)")
    _check_gaps(i_plays, entry_arr, end_arr, "incoming", pl)
    if pl.undecodable:
        return pl

    # the bass swap: every edge in the window, then one reading (two when the handover is staggered)
    lo, hi = entry_arr - SWAP_WINDOW_BEATS, end_arr + SWAP_WINDOW_BEATS
    all_rises = _rises(in_rec["bass"], lo, hi)
    rises = [r for r in all_rises if not r["preview"]]
    falls = _falls(out_rec["bass"], lo, hi)
    pl.edges = [{"kind": e["kind"], "out_bar": _out_bar(o_plays, e["t0"]), "beats": round(e["dur"], 2),
                 "level": round(e["level"], 3), "preview": e["preview"]} for e in all_rises + falls]
    for r in all_rises:
        if r["preview"]:
            pl.notes.append("a bass preview of the incoming at out bar %s (back to the floor within four bars) "
                            "is not the swap" % _out_bar(o_plays, r["t0"]))
    rise = _pick_edge(rises, o_plays, ref_swap_bar)
    fall = _pick_edge(falls, o_plays, ref_swap_bar)
    if len(rises) > 1 or len(falls) > 1:
        pl.ambiguity = "%d incoming rises (out bars %s) and %d outgoing falls (out bars %s) in the blend window; " \
                       "read the one %s" % (
                           len(rises), ", ".join("%g" % round(_out_bar(o_plays, e["t0"]) or 0, 2) for e in rises) or "none",
                           len(falls), ", ".join("%g" % round(_out_bar(o_plays, e["t0"]) or 0, 2) for e in falls) or "none",
                           "nearest the planner's own swap" if ref_swap_bar is not None else "with the biggest move")
    if rise is not None and rise["dur"] > LONG_RAMP_BEATS:
        pl.undecodable = ("the incoming bass fades in over %g bars (out bar %s onward), not one swap: not decodable"
                          % (round(rise["dur"] / 4.0, 1), _out_bar(o_plays, rise["t0"])))
        return pl
    if rise is not None:
        primary, label, other = rise, "incoming's bass rise", fall
    elif fall is not None and fall["dur"] <= LONG_RAMP_BEATS:
        primary, label, other = fall, "outgoing's bass fall (no incoming rise)", None
    elif fall is not None:
        pl.undecodable = "the outgoing bass fades out over %g bars and the incoming has no rise: not decodable" \
                         % round(fall["dur"] / 4.0, 1)
        return pl
    else:
        pl.undecodable = "no bass swap automation found inside the overlap"
        return pl
    swap_arr = primary["t0"]
    _read_swap(pl, o_plays, i_plays, swap_arr, label)
    if pl.undecodable:
        return pl
    if primary["kind"] == "rise" and primary["level"] < 0.9:
        pl.model_gaps.append("the incoming bass rises only to %.2f, not to full" % primary["level"])
    if primary["kind"] == "fall":
        floor_hit = any(v < 0.5 for t, v in in_rec["bass"] if lo <= t <= swap_arr)
        if not floor_hit:
            pl.model_gaps.append("no bass kill: the incoming's bass is never cut before the swap, so both basses "
                                 "play together (the planner assumes the incoming's lows are cut)")
    other_reading = None
    if other is not None and abs(other["t0"] - swap_arr) > 1.0:
        o_bar_in = _out_bar(o_plays, swap_arr)
        o_bar_out = _out_bar(o_plays, other["t0"])
        pl.model_gaps.append("staggered bass handover: the outgoing's bass starts leaving at out bar %s, the "
                             "incoming's enters at out bar %s (the planner models one swap bar; both readings are "
                             "scored)" % ("%.4g" % round(o_bar_out or 0, 2), "%.4g" % round(o_bar_in or 0, 2)))
        if other["dur"] > 1.0:
            pl.notes.append("the outgoing bass fall is a ramp of %.1f beats (read from its start)" % other["dur"])
        other_reading = other

    # the outgoing: skips, mid replays, tail loop, stop, volume fade
    after_entry = [p for p in o_plays if p.seg.arr0 >= entry_arr - JOIN_BEATS]
    j = len(o_plays)
    while j > 0 and o_plays[j - 1].kind == "replay" and o_plays[j - 1].seg.arr0 >= entry_arr - JOIN_BEATS:
        j -= 1                  # a replay that starts before the entry is the outgoing's own earlier loop
    tail = o_plays[j:]
    for p in after_entry:
        if p.kind == "skip":
            pl.shape_gaps.append("the outgoing skips source bars %g-%g inside the blend"
                                 % (p.gap_from / 4.0, p.seg.src0 / 4.0))
        elif p.kind == "replay" and p not in tail:
            pl.shape_gaps.append("the outgoing replays source bars %g-%g mid-blend and plays on"
                                 % (p.seg.src0 / 4.0, min(p.seg.src1, p.top) / 4.0))
    _check_gaps(o_plays, entry_arr, end_arr, "outgoing", pl)
    stop_arr = end_arr
    silent = _silent_at(out_rec["vol"], swap_arr)
    if silent is not None and silent < end_arr - FADE_EARLY_BEATS:
        stop_arr = silent
        pl.notes.append("the outgoing's volume reaches silence at beat %.2f, %.1f bars before its last clip ends: "
                        "the end is read from the fade" % (silent, (end_arr - silent) / 4.0))
    if tail and stop_arr <= tail[0].seg.arr0 + JOIN_BEATS:
        tail, j = [], len(o_plays)          # the outgoing is silent before its tail loop starts
    if tail and j > 0:
        base_top = max(p.seg.src1 for p in o_plays[:j] if p.kind != "replay")
        looped_beats = stop_arr - tail[0].seg.arr0
        new_bars = max(p.new_beats for p in tail) / 4.0
        if n_out is None:
            pl.undecodable = "the outgoing's natural end is unknown, so a tail loop cannot be told from a cut outro"
            return pl
        if abs(base_top / 4.0 - n_out) > BEAT:
            pl.shape_gaps.append(
                "the outgoing stops at source bar %g of %g (an outro cut of %g bars) and then loops source bars "
                "%g-%g (the planner never loops after a cut)" % (
                    round(base_top / 4.0, 2), n_out, round(n_out - base_top / 4.0, 2),
                    tail[0].seg.src0 / 4.0, round(min(tail[0].seg.src1, base_top) / 4.0, 2)))
        if new_bars * 4.0 > JOIN_BEATS:
            heard_end = (base_top + looped_beats) / 4.0
            pl.e = heard_end
            pl.raw["end"], pl.exact["end"] = heard_end, "not on the planner's end rule (not scored)"
            pl.shape_gaps.append(
                "the outgoing replays source bars %g-%g and then plays on %g bars into new material, stopping at "
                "bar %g of heard time (the planner only appends a loop at the natural end and never replays then continues)"
                % (tail[0].seg.src0 / 4.0, base_top / 4.0, round(new_bars, 2), round(heard_end, 2)))
        else:
            pl.e = _bar(pl, "end", base_top + looped_beats, beats=True)
        pl.out_loop_src = (tail[0].seg.src0 / 4.0, min(tail[0].seg.src1, base_top) / 4.0)
        contiguous = all(abs(tail[n].seg.arr0 - (tail[n - 1].seg.arr1 if n else o_plays[j - 1].seg.arr1)) <= JOIN_BEATS
                         for n in range(len(tail)))
        if not contiguous:
            pl.shape_gaps.append("the outgoing's tail loop is not one continuous stretch")
        if len({(round(p.seg.src0, 2), round(min(p.seg.src1, p.top), 2)) for p in tail}) > 1:
            pl.notes.append("the tail loop repeats more than one source window")
    else:
        hit_end = locate(o_plays, stop_arr - 1e-6) if stop_arr < end_arr else None
        src_end = (hit_end[0] + 1e-6) if hit_end else o_plays[-1].seg.src1
        pl.e = _bar(pl, "end", src_end, beats=True)
    if pl.undecodable:
        return pl
    pre_gaps = list(pl.shape_gaps)
    _consistency(pl)
    if other_reading is not None:
        pl.alt = _alt_reading(pl, o_plays, i_plays, other_reading, pre_gaps)
        pl.alt_label = "outgoing's bass fall" if other_reading["kind"] == "fall" else "incoming's bass rise"
    return pl


def _alt_reading(pl, o_plays, i_plays, edge, gaps):
    """The same blend with the swap read from the other bass edge."""
    alt = Placement(a=pl.a, e=pl.e, loop_l=pl.loop_l, loop_p=pl.loop_p, cut=pl.cut, out_loop_src=pl.out_loop_src,
                    shape_gaps=list(gaps))
    alt.exact = {k: v for k, v in pl.exact.items() if k not in ("swap", "incoming swap bar")}
    _read_swap(alt, o_plays, i_plays, edge["t0"], "outgoing's bass fall" if edge["kind"] == "fall"
               else "incoming's bass rise")
    _consistency(alt)
    return alt


# ---- the planner's own expectation, for the round trip ----------------------------------------
def placement_from_plan(plan):
    pl = Placement(a=plan.in_starts_at_out_bar, s=plan.bass_switch_out_bar, e=plan.out_end_bar,
                   k=plan.swap_incoming_bar, loop_l=plan.in_intro_looped_bars,
                   loop_p=plan.in_loop_phrase_end, cut=plan.in_intro_cut_bars)
    return pl


def placement_from_decision(d):
    """A decisions-file entry as a Placement (the pipeline's own record of the plan)."""
    entry, trim, swap_in = d["entry_out_bar"], d.get("intro_trim_bars") or 0.0, d["swap_in_bar"]
    loop = d.get("intro_loop")
    loop_l = (loop["source_end_bar"] - loop["source_start_bar"]) if loop else 0.0
    return Placement(a=entry, s=entry - trim + loop_l + swap_in, e=d.get("out_end_bar"), k=swap_in,
                     loop_l=loop_l, loop_p=loop["source_end_bar"] if loop else 0.0, cut=trim)


def parse_dropped_loops(text):
    """{pair index: ['intro_loop', 'tail_loop', ...]} from a pipeline log (phase2.log lines 'T5: dropped intro_loop, ...')."""
    out = {}
    for m in re.finditer(r"T(\d+): dropped (intro_loop|tail_loop)", text):
        kinds = out.setdefault(int(m.group(1)), [])
        if m.group(2) not in kinds:
            kinds.append(m.group(2))
    return out


def apply_dropped_loop(pl, kind, n_out):
    """The pipeline's own rule when its loop quality gate rejects a planned loop (propose_arrangement
    _drop_rejected_decision_loop): a dropped intro loop moves the entry later by the loop length (the swap and
    the incoming bar at the swap stay); a dropped tail loop ends the outgoing at its native end."""
    if kind == "intro_loop":
        if not pl.loop_l:
            raise ValueError("the pipeline log says it dropped an intro loop, but the planner's plan has none")
        pl.a, pl.loop_l, pl.loop_p = pl.a + pl.loop_l, 0.0, 0.0
    else:
        if pl.e is None or pl.e <= n_out + 1e-9:
            raise ValueError("the pipeline log says it dropped a tail loop, but the planner's plan has none")
        pl.e = n_out
    return pl


COMPARED = ("a", "s", "e", "k", "loop_l", "cut")
COMPARED_NAMES = {"a": "entry", "s": "swap", "e": "end", "k": "incoming bar at swap", "loop_l": "intro loop",
                  "loop_p": "intro loop end", "cut": "intro trim"}


def compared_fields(x, y):
    return COMPARED + (("loop_p",) if (x.loop_l or y.loop_l) else ())


def compare_placements(x, y, tol, fields=None):
    """[field names that differ by more than tol] between two Placements; a missing value differs."""
    bad = []
    for name in fields or compared_fields(x, y):
        u, v = getattr(x, name), getattr(y, name)
        if u is None or v is None or abs(u - v) > tol + 1e-9:
            bad.append(name)
    return bad


def check_round_trip(decoded, expected, names, tol=ROUND_TRIP_BARS, notes=None):
    """Decoder of the planner's own set vs the planner's own plans, on every field the decoder produces.  A pair
    with no planner plan is reported and skipped (the others still count).  Returns the per-transition rows;
    raises RoundTripError (with every mismatch and the rows) on any disagreement or shape gap."""
    rows, bad = [], []
    for i, (d, p) in enumerate(zip(decoded, expected), 1):
        row = {"transition": i, "out": names[i - 1], "in": names[i], "decoded": d.key(),
               "planner": p.key() if p else None, "differs": [], "compared": [], "status": "ok",
               "model_gaps": list(d.model_gaps)}
        if notes and i in notes:
            row["adjusted"] = notes[i]
        if d.undecodable:
            row["differs"] = ["not decodable: " + d.undecodable]
        elif p is None:
            row["status"] = "round trip: no planner plan for this pair"
            rows.append(row)
            continue
        else:
            fields = compared_fields(d, p)
            row["compared"] = [COMPARED_NAMES[f] for f in fields]
            row["differs"] = [COMPARED_NAMES[f] for f in compare_placements(d, p, tol, fields)]
            row["differs"] += ["shape gap: " + g for g in d.shape_gaps]
        if row["differs"]:
            row["status"] = "FAILED"
            bad.append(row)
        rows.append(row)
    if bad:
        raise RoundTripError("decoder does not reproduce the planner's own set on transition(s) %s: %s" % (
            ", ".join(str(r["transition"]) for r in bad),
            "; ".join("T%d decoded %s vs planner %s: %s" % (r["transition"], r["decoded"], r["planner"],
                                                           ", ".join(r["differs"])) for r in bad)), rows)
    return rows


def coverage_line(decoded, rows, n_outs):
    """What the round trip actually exercised on this baseline, computed from what the baseline contains."""
    live = [d for d, r in zip(decoded, rows) if r["status"] == "ok"]
    paths = {
        "intro trim": any(d.cut > 0 for d in live),
        "intro loop": any(d.loop_l > 0 for d in live),
        "tail loop": any(d.e is not None and d.e > n + 1e-9 for d, n in zip(decoded, n_outs) if d in live),
        "outgoing stopped short of its natural end": any(
            d.e is not None and d.e < n - ROUND_TRIP_BARS for d, n in zip(decoded, n_outs) if d in live),
    }
    covered = ["entry", "swap", "end", "incoming bar at the swap"] + [p for p, v in paths.items() if v]
    missing = [p for p, v in paths.items() if not v]
    return "round trip covered: %s; NOT covered: %s%s" % (
        ", ".join(covered), ", ".join(missing) or "nothing",
        " (the decode paths for these are exercised on the tweaked set only; only hand checks against the ALS "
        "cover them)" if missing else "")


# ---- scoring a placement with the planner's own cost function ----------------------------------
def score_placement(o, n, pl, r6=pb.R6_BE):
    """The planner's BlendPlan for a placement (same _score the candidates use), or None."""
    if not (pb._valid(o) and pb._valid(n)) or not pl.scoreable:
        return None
    try:
        return pb._score(o, n, pl.a, pl.s, pl.e, pl.loop_p, pl.loop_l, pb.phrase_points(o),
                         pb.phrase_points(n), r6, pl.cut)
    except Exception:       # a placement the cost function cannot take is reported, not scored
        return None


def same_placement(plan, pl):
    """Same placement on the planner's own granularity (whole and half bars), not on raw floats."""
    if pl.a is None or pl.s is None or pl.e is None:
        return False
    same = lambda u, v: abs(grain(u) - v) <= 1e-6
    return (same(plan.in_starts_at_out_bar, pl.a) and same(plan.bass_switch_out_bar, pl.s)
            and same(plan.out_end_bar, pl.e) and same(plan.in_intro_looped_bars, pl.loop_l)
            and (pl.loop_l == 0 or same(plan.in_loop_phrase_end, pl.loop_p))
            and same(plan.in_intro_cut_bars, pl.cut))


def distance(x, y):
    """Bars between two placements: entry, swap, end, and the loop and trim lengths (what a listener hears move)."""
    return (abs(x.a - y.a) + abs(x.s - y.s) + abs(x.e - y.e) + abs(x.loop_l - y.loop_l) + abs(x.cut - y.cut))


def explain_outside(o, n, pl, plan):
    """Hand-written checks of the planner's rules, run on a placement that is not among its candidates.  These are
    likely reasons, not the planner's verdict."""
    why = []
    n_out, n_in = pb.natural_end_bar(o), pb.natural_end_bar(n)
    a, s, e, l, p, cut, k = pl.a, pl.s, pl.e, pl.loop_l, pl.loop_p, pl.cut, pl.k
    zone_lo, zone_in = n_out - pb._zone_bars(o), pb._zone_bars(n)
    out_pts, in_pts = pb.phrase_points(o), pb.phrase_points(n)
    if a < 0 or a >= n_out:
        why.append("entry at out bar %g is outside the outgoing (0 to %g)" % (a, n_out))
    if s - a < pb.MIN_SIDE_BARS:
        why.append("the swap is only %g bars after the entry (planner minimum %g)" % (s - a, pb.MIN_SIDE_BARS))
    if 0 < l < pb.MIN_IN_LOOP:
        why.append("the %g-bar intro loop is under the %g-bar minimum" % (l, pb.MIN_IN_LOOP))
    elif l > 0 and not any(abs(p - lp) < 1e-9 and abs(l - ll) < 1e-9 for lp, ll in pb._intro_loop_options(n)):
        why.append("the intro loop (%g bars ending at incoming bar %g) is not a whole-phrase window of the "
                   "incoming's intro" % (l, p))
    if k is not None and k > min(n_in, zone_in) + 1e-9:
        why.append("the swap is on incoming bar %g, past its mix zone (%g bars)" % (k, min(n_in, zone_in)))
    if a < zone_lo - 1e-9 and k is not None and (k * pb._bar_seconds(n) < pb.LONG_INTRO_SECONDS
                                                or a < n_out - pb.ENTRY_MAX_SECONDS / pb._bar_seconds(o) - 1e-9):
        why.append("the entry is before the outgoing's mix zone without a long incoming intro before the swap")
    if s >= n_out - 1e-9:
        why.append("the swap is in the outgoing's looped tail")
    if e - s < pb.MIN_SIDE_BARS:
        why.append("the outgoing ends %g bars after the swap (planner minimum %g, so a tail stop without a "
                   "loop is outside it)" % (e - s, pb.MIN_SIDE_BARS))
    if e > n_out + pb.MAX_TAIL_LOOP:
        why.append("the tail loop is %g bars (planner maximum %g)" % (e - n_out, pb.MAX_TAIL_LOOP))
    if plan is not None and plan.out_cut_bars > 0:
        why.append("the outgoing's outro is cut by %g bars (R5: a usable outro is never cut)" % plan.out_cut_bars)
    if k is not None:
        n_nat = pb._natural_end_threshold(o, n_out)
        played_end = n_out if n_nat - 1e-9 <= e <= n_out else e
        if not pb._clears_long_fills(played_end - a - l + cut, k, pb._long_fills(n, k),
                                     {x[0] for x in pb._sections(n)}):
            why.append("the outgoing is still playing over a long incoming fill")
        in_cues = set(in_pts) | set(pb.implied_points(n))
        if pb._snap(max(k, 0.0)) not in in_cues and pb.bass_in(n) != k:
            why.append("incoming bar %g is not a phrase point of the incoming" % k)
    out_cues = set(out_pts) | set(pb.implied_points(o))
    if pb._snap(s) not in out_cues:
        why.append("out bar %g is not a phrase point of the outgoing" % s)
    targets = [a + l - cut + t for t in pb.target_points(n)]
    if not any(abs(e - t) <= pb.BEAT_BARS + 1e-9 for t in targets + [n_out]) and e <= n_out + 1e-9 and not (
            plan is not None and plan.out_trim_bars):
        why.append("the outgoing stops at out bar %g, which is neither its natural end nor an incoming "
                   "section or fill start" % e)
    elif not any(abs(e - t) <= pb.BEAT_BARS + 1e-9 for t in targets) and e > n_out + 1e-9:
        why.append("the loop ends at out bar %g, which is not an incoming section or fill start" % e)
    return why


def nearest_candidate(cs, pl):
    """(rank, plan, distance, end_used) of the candidate closest to a placement.  The end bar counts only when the
    placement is scoreable (a shape the planner cannot express has no end the planner could match)."""
    use_end = pl.scoreable and pl.e is not None
    best = None
    for rank, c in enumerate(cs, 1):
        d = (abs(c.in_starts_at_out_bar - pl.a) + abs(c.bass_switch_out_bar - pl.s)
             + (abs(c.out_end_bar - pl.e) if use_end else 0.0)
             + abs(c.in_intro_looped_bars - pl.loop_l) + abs(c.in_intro_cut_bars - pl.cut))
        if best is None or d < best[0] - 1e-12:
            best = (d, rank, c)
    return (best[1], best[2], best[0], use_end) if best else None


def trivial_placement(o, n):
    """16 bars in, 16 bars out, swap on the outgoing's outro start (Documentation/Plans/v5-sam-tweaks-analysis.md)."""
    n_out = pb.natural_end_bar(o)
    outro = next((x[0] for x in pb._sections(o) if x[1] == "OUTRO"), None)
    note = "swap on the outgoing's outro start (out bar %g)" % outro if outro is not None else None
    if outro is None:
        outro, note = n_out - TRIVIAL_BARS, "no OUTRO section: swap %g bars before the outgoing's end" % TRIVIAL_BARS
    s = float(outro)
    pl = Placement(a=max(0.0, s - TRIVIAL_BARS), s=s, e=s + TRIVIAL_BARS, k=min(TRIVIAL_BARS, s))
    pl.notes.append(note)
    return pl


# ---- one transition ----------------------------------------------------------------------------
def _terms_diff(sam, top):
    diff = {t: sam.terms.get(t, 0.0) - top.terms.get(t, 0.0) for t in set(sam.terms) | set(top.terms)}
    diff = {t: v for t, v in diff.items() if abs(v) > 1e-9}
    decisive = max(diff, key=lambda t: (abs(diff[t]), t)) if diff else None
    return diff, decisive


def _plan_json(plan):
    return {"entry": plan.in_starts_at_out_bar, "swap": plan.bass_switch_out_bar, "end": plan.out_end_bar,
            "intro_loop_bars": plan.in_intro_looped_bars, "intro_loop_phrase_end": plan.in_loop_phrase_end,
            "intro_cut_bars": plan.in_intro_cut_bars, "cost": round(plan.cost, 4),
            "terms": {t: round(v, 4) for t, v in sorted(plan.terms.items())}, "sane": plan.sane,
            "out_cut_bars": plan.out_cut_bars, "out_trim_bars": plan.out_trim_bars,
            "vocal_clash_bars": plan.vocal_clash_bars, "key_clash": plan.key_clash,
            "key_steps": plan.key_steps, "key_tonal_bars": plan.key_tonal_bars}


def _assess(o, n, cs, top, pl, r6):
    """Where one decoded placement sits.  Returns (position dict, the BlendPlan it was scored as, or None)."""
    if pl.undecodable:
        return {"kind": K_UND, "why": [pl.undecodable], "of": len(cs)}, None
    if not pl.scoreable:
        pos = {"kind": K_SHAPE, "rank": None, "of": len(cs), "why": list(pl.shape_gaps), "scored": False}
        near = nearest_candidate(cs, pl) if cs else None
        if near:
            pos["nearest"] = {"rank": near[0], "plan": _plan_json(near[1]), "end_compared": near[3]}
        return pos, None
    hit = next((i for i, c in enumerate(cs) if same_placement(c, pl)), None)
    pos = {"of": len(cs), "scored": True}
    if hit is not None:
        alt = next((i for i, a in enumerate(top.alternatives, 1) if same_placement(a.plan, pl)), None) if top else None
        plan = cs[hit]
        pos.update(kind=K_TOP if hit == 0 else K_ALT if alt else K_RANKED, rank=hit + 1, alternative=alt)
        above = [c for c in cs if pb._rank_key(c) < pb._rank_key(plan)]
        pos["outranked_by_costlier"] = sum(1 for c in above if c.cost > plan.cost + 1e-9)
        return pos, plan
    plan = score_placement(o, n, pl, r6)
    if plan is None:
        return {"kind": K_NOSCORE, "why": ["the planner's cost function rejected the placement"], "of": len(cs)}, None
    pos.update(kind=K_OUT, rank=None, why=explain_outside(o, n, pl, plan))
    near = nearest_candidate(cs, pl)
    if near:
        pos["nearest"] = {"rank": near[0], "plan": _plan_json(near[1]), "distance": round(near[2], 3),
                          "end_compared": near[3]}
    return pos, plan


def analyse_transition(index, names, o, n, base, sam, r6=pb.R6_BE, dropped=None):
    """The full replay record for one transition (see the module docstring).  `dropped` lists the loops the
    pipeline removed after planning: the baseline is then what was RENDERED, not what the planner chose."""
    rec = {"transition": index, "out": names[0], "in": names[1], "baseline": base.to_json(),
           "sam": sam.to_json(), "kept": None, "position": None, "planner_top": None,
           "sam_scored": None, "terms_difference": None, "decisive_term": None, "vocabulary": [],
           "trivial": None, "candidates": 0, "delivered": None, "position_alt": None, "sam_scored_alt": None}
    if not base.undecodable and not sam.undecodable:
        rec["kept"] = (not compare_placements(base, sam, MATCH_BARS) and base.shape_gaps == sam.shape_gaps
                       and base.model_gaps == sam.model_gaps)
    cs = pb.candidates(o, n, r6=r6) if pb._valid(o) and pb._valid(n) else []
    rec["candidates"] = len(cs)
    top = cs[0] if cs else None
    if top:
        top.alternatives = pb.alternatives_for(cs, n)
        rec["planner_top"] = _plan_json(top)
        rec["alternatives"] = [{"swap": a.swap_out_bar, "entry": a.plan.in_starts_at_out_bar,
                                "end": a.plan.out_end_bar, "cost": round(a.plan.cost, 4),
                                "reason": a.reason} for a in top.alternatives]
    # what the planner actually delivered (the rendered placement), which differs from its top pick when the
    # pipeline dropped a loop after planning
    planner_cost = top.cost if top else None
    if base.scoreable:
        pos_b, plan_b = _assess(o, n, cs, top, base, r6)
        rec["delivered"] = {"placement": {"entry": base.a, "swap": base.s, "end": base.e,
                                          "intro_loop_bars": base.loop_l, "intro_cut_bars": base.cut},
                            "cost": round(plan_b.cost, 4) if plan_b else None,
                            "rank": pos_b.get("rank"), "dropped": list(dropped or []),
                            "differs_from_top": bool(top and not same_placement(top, base))}
        if dropped and plan_b is not None:
            planner_cost = plan_b.cost
    rec["planner_cost"] = planner_cost

    triv = trivial_placement(o, n)
    triv_plan = score_placement(o, n, triv, r6)
    n_out = pb.natural_end_bar(o)
    clamped = Placement(a=triv.a, s=triv.s, e=min(triv.e, n_out), k=triv.k)
    clamped_plan = score_placement(o, n, clamped, r6)
    t = {"placement": {"entry": triv.a, "swap": triv.s, "end": triv.e}, "note": triv.notes[0],
         "cost": round(triv_plan.cost, 4) if triv_plan else None,
         "terms": {k: round(v, 4) for k, v in sorted(triv_plan.terms.items())} if triv_plan else None,
         "in_vocabulary": any(same_placement(c, triv) for c in cs),
         "clamped": {"placement": {"entry": clamped.a, "swap": clamped.s, "end": clamped.e},
                     "cost": round(clamped_plan.cost, 4) if clamped_plan else None}}
    if triv_plan and not t["in_vocabulary"]:
        t["outside_because"] = explain_outside(o, n, triv, triv_plan)
    if top and sam.a is not None and sam.s is not None and sam.e is not None:
        ds = distance(sam, triv)
        dp = distance(placement_from_plan(top), triv)
        t.update(sam_distance=ds, planner_distance=dp,
                 closer="owner" if ds < dp - 1e-9 else "planner" if dp < ds - 1e-9 else "tie")
    rec["trivial"] = t
    rec["trivial_plan_cost"] = triv_plan.cost if triv_plan else None
    rec["trivial_clamped_cost"] = clamped_plan.cost if clamped_plan else None

    pos, sam_plan = _assess(o, n, cs, top, sam, r6)
    rec["position"] = pos
    if sam_plan is not None:
        rec["sam_scored"] = _plan_json(sam_plan)
        if top:
            rec["terms_difference"], rec["decisive_term"] = _terms_diff(sam_plan, top)
    rec["vocabulary"] = list(pos.get("why") or []) + list(sam.model_gaps)
    if sam.alt is not None:
        pos_alt, plan_alt = _assess(o, n, cs, top, sam.alt, r6)
        rec["position_alt"] = dict(pos_alt, label=sam.alt_label, swap=sam.alt.s)
        if plan_alt is not None:
            rec["sam_scored_alt"] = _plan_json(plan_alt)
    return rec


# ---- observations and summary ------------------------------------------------------------------
def _section_at_start(track, bar):
    return next((x[1] for x in pb._sections(track) if abs(x[0] - bar) < 1e-6), None)


def observations(index, o, n, base, sam, owner="the owner"):
    """Facts about what the owner changed, with bar numbers.  Not rules."""
    t = "T%d" % index
    if base.undecodable or sam.undecodable:
        return []
    out = []
    n_out = pb.natural_end_bar(o)
    if abs(sam.cut - base.cut) > MATCH_BARS:
        out.append("%s: intro trim %g -> %g bars" % (t, base.cut, sam.cut))
    if abs(sam.loop_l - base.loop_l) > MATCH_BARS:
        out.append("%s: incoming intro loop %g -> %g bars" % (t, base.loop_l, sam.loop_l))
    if abs(sam.a - base.a) > MATCH_BARS:
        out.append("%s: entry moved from out bar %g to %g (%+g)" % (t, base.a, sam.a, sam.a - base.a))
    if abs(sam.s - base.s) > MATCH_BARS or abs(sam.k - base.k) > MATCH_BARS:
        out.append("%s: swap moved from out bar %g (incoming bar %g) to out bar %g (incoming bar %g)"
                   % (t, base.s, base.k, sam.s, sam.k))
    if abs(sam.e - base.e) > MATCH_BARS:
        loop = ("; tail loop %g bars" % (sam.e - n_out)) if sam.e > n_out + 1e-9 and sam.scoreable else ""
        out.append("%s: outgoing end moved from out bar %g to %g (natural end %g)%s" % (t, base.e, sam.e, n_out, loop))
        if sam.scoreable:
            end_in = sam.e - sam.a - sam.loop_l + sam.cut
            kind = _section_at_start(n, end_in)
            fill = any(abs(f[0] - end_in) < 1e-6 for f in pb._fills(n))
            out.append("%s: the outgoing ends on incoming bar %g (%s)" % (
                t, end_in, "the start of a %s section" % kind if kind else "the start of a fill" if fill
                else "not a section or fill start"))
    if abs((sam.e - sam.a) - (base.e - base.a)) > MATCH_BARS:
        out.append("%s: heard blend %g -> %g bars (entry to end)" % (t, base.e - base.a, sam.e - sam.a))
    if abs((sam.e - sam.s) - (base.e - base.s)) > MATCH_BARS:
        out.append("%s: bars after the swap %g -> %g" % (t, base.e - base.s, sam.e - sam.s))
    label = _section_at_start(n, sam.k)
    if label and (abs(sam.k - base.k) > MATCH_BARS):
        out.append("%s: %s's swap lands on incoming bar %g, the start of a %s section" % (t, owner, sam.k, label))
    out.extend("%s: %s" % (t, g) for g in sam.shape_gaps + sam.model_gaps)
    if sam.ambiguity:
        out.append("%s: swap edge ambiguous: %s" % (t, sam.ambiguity))
    return out


def _gap_entries(r):
    """Named gaps for one transition: kind plus reasons, one entry per swap reading."""
    out = []
    for label, pos in (("", r["position"]), (r["position_alt"] and "read from the %s" % r["position_alt"]["label"],
                                             r["position_alt"])):
        if pos and pos.get("kind") in (K_SHAPE, K_OUT, K_UND, K_NOSCORE):
            out.append({"reading": label or "", "kind": pos["kind"], "reasons": list(pos.get("why") or [])})
    return out


def summarise(records, owner="the owner"):
    n = len(records)
    changed = [r for r in records if r["kept"] is False]
    unchanged = [r["transition"] for r in records if r["kept"]]
    unknown = [r["transition"] for r in records if r["kept"] is None]
    kind = lambda r: (r["position"] or {}).get("kind")
    ch = [r["transition"] for r in changed]
    top_equal = [r["transition"] for r in changed if kind(r) == K_TOP]
    top_or_alt = [r["transition"] for r in changed if kind(r) in (K_TOP, K_ALT)]
    top3 = [r["transition"] for r in changed if (r["position"] or {}).get("rank") and r["position"]["rank"] <= 3]
    gaps = {r["transition"]: _gap_entries(r) for r in records if _gap_entries(r)}

    def tally(trivial_key):
        low = high = eq = ns = 0
        for r in records:
            pc, tc = r.get("planner_cost"), r.get(trivial_key)
            if pc is None or tc is None:
                ns += 1
            elif pc < tc - 1e-9:
                low += 1
            elif pc > tc + 1e-9:
                high += 1
            else:
                eq += 1
        return {"planner_lower": low, "planner_higher": high, "equal": eq, "not_scoreable": ns}

    cost, cost_clamped = tally("trivial_plan_cost"), tally("trivial_clamped_cost")
    triv_equal = []
    for r in changed:
        s, tp = r["sam"], r["trivial"]["placement"]
        if None not in (s["entry"], s["swap"], s["end"]):
            if (abs(s["entry"] - tp["entry"]) < MATCH_BARS and abs(s["swap"] - tp["swap"]) < MATCH_BARS
                    and abs(s["end"] - tp["end"]) < MATCH_BARS):
                triv_equal.append(r["transition"])
    closer = [r["trivial"].get("closer") for r in changed if r["trivial"].get("closer")]
    cost_beat = cost["planner_lower"] > cost["planner_higher"]
    match_beat = len(top_equal) > len(triv_equal)
    dropped = [r["transition"] for r in records if r.get("delivered") and r["delivered"]["dropped"]]
    summary = {
        "owner": owner, "transitions": n, "changed": ch, "unchanged_not_evidence": unchanged, "undecidable": unknown,
        "top_pick_equals_owner_on_changed": top_equal, "top_pick_or_listed_alternative_on_changed": top_or_alt,
        "owner_in_top_3_on_changed": top3, "vocabulary_gaps": gaps,
        "planner_delivered_cost_vs_trivial": cost, "planner_delivered_cost_vs_trivial_clamped": cost_clamped,
        "loop_dropped_by_pipeline": dropped,
        "trivial_equals_owner_on_changed": triv_equal,
        "closer_to_trivial_on_changed": {"owner": closer.count("owner"), "planner": closer.count("planner"),
                                         "tie": closer.count("tie")},
        "planner_beats_trivial_on_cost": bool(cost_beat),
        "planner_matches_owner_more_than_trivial": bool(match_beat), "plain_verdict": None}
    if not cost_beat and not match_beat:
        summary["plain_verdict"] = (
            "The planner does NOT beat the trivial baseline (16 in, 16 out, swap on the outro start): on its "
            "delivered placements it is cheaper on %d transitions and dearer on %d, and on the %d changed "
            "transitions its top pick equals %s's on %d where the trivial baseline does on %d."
            % (cost["planner_lower"], cost["planner_higher"], len(changed), owner, len(top_equal), len(triv_equal)))
    return summary


def pattern_lines(records, obs=None):
    """Observations across the set (counts of what changed), still not fitted rules."""
    lines = []
    trims = [(r["transition"], r["baseline"]["intro_cut_bars"], r["sam"]["intro_cut_bars"]) for r in records
             if r["baseline"]["intro_cut_bars"] and not r["sam"]["not_decodable"] and not r["baseline"]["not_decodable"]]
    if trims:
        lines.append("Intro trims in the planner's set: " + ", ".join(
            "T%d (%g bars, tweak %g)" % t for t in trims))
    loops = [(r["transition"], r["baseline"]["intro_loop_bars"], r["sam"]["intro_loop_bars"]) for r in records
             if r["baseline"]["intro_loop_bars"] != r["sam"]["intro_loop_bars"]]
    if loops:
        lines.append("Incoming intro loop changed: " + ", ".join("T%d (%g -> %g bars)" % t for t in loops))
    heard = [(r["transition"], r["baseline"], r["sam"]) for r in records
             if r["baseline"]["end"] is not None and r["sam"]["end"] is not None
             and r["baseline"]["entry"] is not None and r["sam"]["entry"] is not None]
    if heard:
        lines.append("Heard blend length in bars, planner set -> tweak: " + ", ".join(
            "T%d %g -> %g" % (i, b["end"] - b["entry"], s["end"] - s["entry"]) for i, b, s in heard))
        lines.append("Bars after the swap, planner set -> tweak: " + ", ".join(
            "T%d %g -> %g" % (i, b["end"] - b["swap"], s["end"] - s["swap"]) for i, b, s in heard))
        lines.append("Bars from entry to swap, planner set -> tweak: " + ", ".join(
            "T%d %g -> %g" % (i, b["swap"] - b["entry"], s["swap"] - s["entry"]) for i, b, s in heard))
    return lines


# ---- the whole replay --------------------------------------------------------------------------
def match_planner_names(als_names, planner_tracks):
    """{als name: planner track}: an exact (unescaped, case-folded) name, else the one planner name that is a
    complete prefix of the ALS name or the other way round.  Anything ambiguous or different (SW V2 against
    SW V3) is refused, never guessed."""
    out, low = {}, {html.unescape(k).strip().lower(): v for k, v in planner_tracks.items()}
    for name in als_names:
        key = html.unescape(name).strip().lower()
        v = low.get(key)
        if v is None:
            hits = [t for k, t in low.items() if k.startswith(key) or key.startswith(k)]
            if len(hits) > 1:
                raise ValueError("%r matches %d planner tracks by prefix: ambiguous, name it exactly" % (name, len(hits)))
            v = hits[0] if hits else None
        if v is None:
            raise ValueError("no planner track data for %r" % name)
        out[name] = v
    return out


def _normalise_drops(dropped):
    return {int(k): ([v] if isinstance(v, str) else list(v)) for k, v in (dropped or {}).items()}


def replay(baseline_als, tweak_als, planner_tracks, decisions=None, r6=pb.R6_BE, dropped=None,
           owner="the owner"):
    """Decode both sets, run the round trip on the baseline, then replay the tweak.  `planner_tracks` is
    {track name: planner track dict}; `decisions` optionally supplies the round-trip reference instead of
    re-planning ({'transitions': [...]}); `dropped` is {pair: [kinds]} for loops the pipeline's own quality gate
    removed after planning.  Raises RoundTripError before scoring anything on a mismatch."""
    dropped = _normalise_drops(dropped)
    base_tracks, sam_tracks = read_als(baseline_als), read_als(tweak_als)
    names = [t["name"] for t in base_tracks]
    if names != [t["name"] for t in sam_tracks]:
        raise ValueError("the two sets do not hold the same tracks in the same order:\n  %s\n  %s"
                         % (names, [t["name"] for t in sam_tracks]))
    if len(names) < 2:
        raise ValueError("a set needs at least two tracks")
    plan = match_planner_names(names, planner_tracks)
    n_outs = [pb.natural_end_bar(plan[nm]) for nm in names[:-1]]
    base = [decode_transition(base_tracks[i], base_tracks[i + 1], n_outs[i]) for i in range(len(names) - 1)]
    sam = [decode_transition(sam_tracks[i], sam_tracks[i + 1], n_outs[i], ref_swap_bar=base[i].s)
           for i in range(len(names) - 1)]

    tops = [pb.plan_blend(plan[names[i]], plan[names[i + 1]], r6=r6) for i in range(len(names) - 1)]
    if decisions is not None:
        by_pair = {d["pair_index"]: d for d in decisions["transitions"]}
        expected = [placement_from_decision(by_pair[i]) if i in by_pair else None for i in range(1, len(names))]
        reference = "the decisions file"
    else:
        expected = [placement_from_plan(t) if t else None for t in tops]
        reference = "plan_blend on the same pair"
    notes = {}
    for pair, kinds in sorted(dropped.items()):
        if not 1 <= pair < len(names) or expected[pair - 1] is None:
            raise ValueError("pipeline drop for pair %d has no planner placement" % pair)
        exp, top = expected[pair - 1], tops[pair - 1]
        for kind in kinds:
            if decisions is not None and kind == "intro_loop" and not exp.loop_l:
                # the decisions file already holds the post-drop arrangement: check it against the plan, shift once
                if top is None or not top.in_intro_looped_bars or abs(exp.a - (top.in_starts_at_out_bar
                                                                               + top.in_intro_looped_bars)) > ROUND_TRIP_BARS:
                    raise ValueError("pipeline log says T%d dropped an intro loop, but the decisions file's entry "
                                     "does not match the planner's plan with that loop dropped" % pair)
                notes[pair] = "the decisions file already holds the post-drop arrangement of the dropped intro_loop"
                continue
            if decisions is not None and kind == "tail_loop" and exp.e is not None and exp.e <= n_outs[pair - 1] + 1e-9:
                if top is None or top.out_looped_bars <= 0:
                    raise ValueError("pipeline log says T%d dropped a tail loop, but the planner's plan has none" % pair)
                notes[pair] = "the decisions file already holds the post-drop arrangement of the dropped tail_loop"
                continue
            apply_dropped_loop(exp, kind, n_outs[pair - 1])
            notes[pair] = ("the pipeline dropped this transition's %s after planning: WEAKER check, the expectation is "
                           "the planner's plan with the pipeline's drop rule applied, not read from the planner"
                           % ", ".join(kinds))
    round_trip = check_round_trip(base, expected, names, notes=notes)
    coverage = coverage_line(base, round_trip, n_outs)

    records, obs = [], []
    for i in range(len(names) - 1):
        o, n = plan[names[i]], plan[names[i + 1]]
        rec = analyse_transition(i + 1, (names[i], names[i + 1]), o, n, base[i], sam[i], r6,
                                 dropped=dropped.get(i + 1))
        rec["round_trip_status"] = round_trip[i]["status"]
        rec["observations"] = observations(i + 1, o, n, base[i], sam[i], owner)
        obs.extend(rec["observations"])
        records.append(rec)
    return {"owner": owner, "tracks": names,
            "round_trip": {"passed": True, "reference": reference, "rows": round_trip, "coverage": coverage},
            "transitions": records, "summary": summarise(records, owner), "patterns": pattern_lines(records, obs)}


# ---- the report --------------------------------------------------------------------------------
def _fmt(p, show_end=True):
    if p is None or p.get("entry") is None or p.get("swap") is None or (show_end and p.get("end") is None):
        return "not decodable" + ("" if p is None or p.get("entry") is None else " (entry %g only)" % p["entry"])
    s = "entry %g, swap %g" % (p["entry"], p["swap"]) + (", end %g" % p["end"] if show_end else "")
    if p.get("intro_loop_bars"):
        s += ", intro loop %g (to incoming bar %g)" % (p["intro_loop_bars"], p.get("intro_loop_phrase_end", 0))
    if p.get("intro_cut_bars"):
        s += ", intro cut %g" % p["intro_cut_bars"]
    return s


def _terms(x):
    return " ".join("%s=%.2f" % (k, v) for k, v in sorted(x["terms"].items())) or "all terms zero"


def _as_list(why):
    return [why] if isinstance(why, str) else list(why or [])


def format_failed_round_trip(rows, message):
    lines = ["ROUND TRIP FAILED: %s" % message, "Nothing was scored.  The decoder is not trusted.", ""]
    for r in rows:
        lines.append("T%d %s -> %s: decoded %s | planner %s | %s" % (
            r["transition"], r["out"][:28], r["in"][:28], r["decoded"], r["planner"],
            "differs in " + ", ".join(r["differs"]) if r["differs"] else r["status"]))
    return "\n".join(lines)


def _position_lines(pos, plan_json, top, who):
    out = []
    kind = pos["kind"]
    if kind == K_UND:
        out.append("%s: NOT DECODABLE: %s" % (who, "; ".join(_as_list(pos["why"]))))
    elif kind in (K_TOP, K_ALT, K_RANKED):
        rank = "rank %d of %d planner candidates for this pair (not a rank over all possible placements)" % (
            pos["rank"], pos["of"])
        head = {K_TOP: "the planner's TOP PICK", K_ALT: "a listed ALTERNATIVE (#%s)" % pos.get("alternative"),
                K_RANKED: "reachable"}[kind]
        out.append("%s: %s, %s" % (who, head, rank))
    elif kind == K_SHAPE:
        out.append("%s: SHAPE NOT EXPRESSIBLE in the planner's terms (not scored)" % who)
        out.extend("      the shape: %s" % w for w in _as_list(pos["why"]))
    elif kind == K_OUT:
        out.append("%s: SCORED BUT NOT AMONG THE PLANNER'S CANDIDATES (%d candidates for this pair; not ranked)"
                   % (who, pos["of"]))
        out.extend("      likely reason (hand-written check, not the planner's verdict): %s" % w
                   for w in _as_list(pos.get("why")))
    else:
        out.append("%s: NOT SCOREABLE: %s" % (who, "; ".join(_as_list(pos.get("why")))))
    if pos.get("nearest"):
        nr = pos["nearest"]
        out.append("      nearest candidate: rank %d, %s%s" % (nr["rank"], _fmt(nr["plan"], nr.get("end_compared", True)),
                                                               "" if nr.get("end_compared", True) else
                                                               " (end bar not compared)"))
    if plan_json is not None and top:
        out.append("      cost: %s %.2f, planner's top %.2f  (sane pairing: %s %s, top %s)" % (
            who.split(":")[0], plan_json["cost"], top["cost"], "this one", "yes" if plan_json["sane"] else "no",
            "yes" if top["sane"] else "no"))
        out.append("      terms          : " + _terms(plan_json))
        out.append("      planner's terms: " + _terms(top))
        diff = {k: plan_json["terms"].get(k, 0.0) - top["terms"].get(k, 0.0)
                for k in set(plan_json["terms"]) | set(top["terms"])}
        diff = {k: v for k, v in diff.items() if abs(v) > 1e-9}
        if diff:
            out.append("      minus planner by term: " + " ".join("%s=%+.2f" % (k, v) for k, v in sorted(diff.items())))
            if kind != K_TOP:
                dk = max(diff, key=lambda k: (abs(diff[k]), k))
                out.append("      term that makes the difference: %s (%+.2f)" % (dk, diff[dk]))
        if pos.get("outranked_by_costlier"):
            out.append("      %d candidate(s) ranked above it cost MORE: the sane-pairing tier (a swap lined up on a "
                       "phrase point of both tracks; a flat run of bottom end or a cut outro also makes a placement "
                       "unsane), not the cost, orders them" % pos["outranked_by_costlier"])
    return out


def format_report(result):
    owner = result.get("owner", "the owner")
    own = owner + "'s"
    out = []
    rt = result["round_trip"]
    out.append("TWEAK REPLAY: %s hand edit put through the blend planner" % own)
    ok_rows = [r for r in rt["rows"] if r["status"] == "ok"]
    out.append("Round trip (decoding the planner's own set vs %s, tolerance one beat, every decoded field): "
               "PASSED on %d of %d transitions" % (rt["reference"], len(ok_rows), len(rt["rows"])))
    for r in rt["rows"]:
        out.append("  T%d decoded %s | planner %s | %s%s%s" % (
            r["transition"], r["decoded"], r["planner"], r["status"],
            " | compared: " + ", ".join(r["compared"]) if r["compared"] else "",
            " [" + r["adjusted"] + "]" if r.get("adjusted") else ""))
    out.append("  " + rt["coverage"])
    out.append("")
    for r in result["transitions"]:
        out.append("T%d. %s -> %s" % (r["transition"], r["out"][:34], r["in"][:34]))
        b, sm = r["baseline"], r["sam"]
        pick = lambda d: {k: d[k] for k in ("entry", "swap", "end", "intro_loop_bars", "intro_loop_phrase_end",
                                            "intro_cut_bars")}
        out.append("   planner's set : %s" % _fmt(pick(b)))
        out.append("   %s tweak   : %s" % (owner, _fmt(pick(sm))))
        prec = sm["precision"]
        out.append("   decoded       : %s%s" % (
            ", ".join("%s %s" % (k, v) for k, v in sorted(prec.items())) or "nothing",
            "; " + "; ".join(sm["notes"]) if sm["notes"] else ""))
        out.append("   decoder consistency checks: planner's set %s; tweak %s" % (
            "ok" if b["consistency_checks"] and all(b["consistency_checks"].values()) else "n/a" if not b["consistency_checks"]
            else "FLAGGED", "ok" if sm["consistency_checks"] and all(sm["consistency_checks"].values()) else
            "n/a" if not sm["consistency_checks"] else "FLAGGED"))
        if sm["swap_edge_ambiguity"]:
            out.append("   SWAP EDGE AMBIGUOUS: %s" % sm["swap_edge_ambiguity"])
        if sm["swap_edge_ambiguity"] or sm.get("alternative_reading"):
            out.append("   bass edges found: " + "; ".join("%s at out bar %s (%s beats, level %s%s)" % (
                e["kind"], e["out_bar"], e["beats"], e["level"], ", preview" if e["preview"] else "")
                for e in sm["bass_edges"]))
        out.append("   %s vs the planner's set: %s" % (owner, (
            "unchanged (the planner's placement kept; not evidence of agreement)" if r["kept"] else
            "changed" if r["kept"] is False else "cannot say (not decodable)")))
        pos, top = r["position"], r["planner_top"]
        if top:
            out.append("   planner's top : %s  cost %.2f  %d planner candidates" % (_fmt(top), top["cost"], r["candidates"]))
        else:
            out.append("   planner's top : NO PLAN")
        dl = r.get("delivered")
        if dl and dl["dropped"]:
            out.append("   DELIVERED (rendered) placement: %s  cost %s, rank %s of %d; the planner's top pick could "
                       "not be rendered because the pipeline dropped its %s" % (
                           _fmt(dl["placement"]), "%.2f" % dl["cost"] if dl["cost"] is not None else "n/a",
                           dl["rank"], r["candidates"], ", ".join(dl["dropped"])))
        for line in _position_lines(pos, r["sam_scored"], top, "   %s's placement" % owner):
            out.append(line)
        if r["position_alt"]:
            pa = r["position_alt"]
            out.append("   OTHER READING of the swap (%s, out bar %g):" % (pa["label"], pa["swap"]))
            for line in _position_lines(pa, r["sam_scored_alt"], top, "   %s's placement (this reading)" % owner):
                out.append(line)
        for alt in r.get("alternatives") or []:
            out.append("      listed alternative: swap %g, entry %g, end %g, cost %.2f" % (
                alt["swap"], alt["entry"], alt["end"], alt["cost"]))
        tr_ = r["trivial"]
        out.append("   trivial (16 in, 16 out, swap on the outro start): entry %g, swap %g, end %g, cost %s%s" % (
            tr_["placement"]["entry"], tr_["placement"]["swap"], tr_["placement"]["end"],
            "%.2f" % tr_["cost"] if tr_["cost"] is not None else "not scoreable",
            "" if tr_["in_vocabulary"] else " (not among the planner's candidates)"))
        out.append("      terms: " + (" ".join("%s=%.2f" % kv for kv in sorted(tr_["terms"].items())) if tr_["terms"] else "n/a")
                   + "; " + tr_["note"])
        for w in tr_.get("outside_because") or []:
            out.append("      likely reason (hand-written check, not the planner's verdict): %s" % w)
        cl = tr_["clamped"]
        out.append("      with its end clamped to the natural end (end %g): cost %s" % (
            cl["placement"]["end"], "%.2f" % cl["cost"] if cl["cost"] is not None else "not scoreable"))
        if tr_.get("closer"):
            out.append("      closer to the trivial placement: %s (%s %g bars away, planner's pick %g)" % (
                {"owner": own + " placement", "planner": "the planner's pick", "tie": "neither (tie)"}[tr_["closer"]],
                owner, tr_["sam_distance"], tr_["planner_distance"]))
        out.append("")
    s = result["summary"]
    n = s["transitions"]
    names = lambda v: ", ".join("T%d" % i for i in v) or "none"
    nc = len(s["changed"])
    out.append("SUMMARY (%d transitions)" % n)
    out.append("  %s changed %d of %d (%s); the planner's top pick equals %s on %d of the %d changed; "
               "%d unchanged transitions (%s) are not evidence%s" % (
                   owner[0].upper() + owner[1:], nc, n, names(s["changed"]), "his" if owner != "the owner" else "theirs",
                   len(s["top_pick_equals_owner_on_changed"]), nc, len(s["unchanged_not_evidence"]),
                   names(s["unchanged_not_evidence"]),
                   "; %d could not be compared (%s)" % (len(s["undecidable"]), names(s["undecidable"])) if s["undecidable"] else ""))
    out.append("  on the %d changed: top pick or listed alternative %d (%s); in the planner's top 3: %d (%s)" % (
        nc, len(s["top_pick_or_listed_alternative_on_changed"]), names(s["top_pick_or_listed_alternative_on_changed"]),
        len(s["owner_in_top_3_on_changed"]), names(s["owner_in_top_3_on_changed"])))
    out.append("  vocabulary gaps / not decodable on %d transitions (kind and reasons):" % len(s["vocabulary_gaps"]))
    for i, entries in sorted(s["vocabulary_gaps"].items()):
        for e in entries:
            out.append("      T%d%s: %s%s" % (i, " (%s)" % e["reading"] if e["reading"] else "", e["kind"],
                                             ": " + "; ".join(e["reasons"]) if e["reasons"] else ""))
    c, cc = s["planner_delivered_cost_vs_trivial"], s["planner_delivered_cost_vs_trivial_clamped"]
    out.append("  planner's DELIVERED placement vs the trivial baseline on cost: lower on %d, higher on %d, equal on %d, "
               "not scoreable on %d" % (c["planner_lower"], c["planner_higher"], c["equal"], c["not_scoreable"]))
    out.append("  same with the trivial end clamped to the natural end: lower on %d, higher on %d, equal on %d, not "
               "scoreable on %d" % (cc["planner_lower"], cc["planner_higher"], cc["equal"], cc["not_scoreable"]))
    if s["loop_dropped_by_pipeline"]:
        out.append("  a loop was dropped by the pipeline after planning on %s: the delivered placement is scored there, "
                   "not the planner's top pick" % names(s["loop_dropped_by_pipeline"]))
    out.append("  on the %d changed: the trivial baseline equals %s's placement on %d (%s); the planner's top pick on %d" % (
        nc, owner, len(s["trivial_equals_owner_on_changed"]), names(s["trivial_equals_owner_on_changed"]),
        len(s["top_pick_equals_owner_on_changed"])))
    cl = s["closer_to_trivial_on_changed"]
    out.append("  on the changed ones, closer to the trivial placement: %s's %d, the planner's pick %d, tie %d" % (
        owner, cl["owner"], cl["planner"], cl["tie"]))
    if s["plain_verdict"]:
        out.append("  PLAIN VERDICT: " + s["plain_verdict"])
    else:
        out.append("  Trivial baseline check: the planner beats it on %s." % (
            "both cost and matching %s" % owner if s["planner_beats_trivial_on_cost"]
            and s["planner_matches_owner_more_than_trivial"]
            else "cost only (its own cost function) and not on matching %s" % owner if s["planner_beats_trivial_on_cost"]
            else "matching %s only" % owner))
    out.append("  What this can and cannot show: one mix, one listener, %d changed transitions. It shows where this "
               "one edit sits among the planner's candidates and which cost terms separate them. It does not show "
               "that the planner or the edit is right, and the unchanged transitions say only that nothing was "
               "changed. The cost comparison uses the planner's own cost function." % nc)
    out.append("  Ranks are over the planner's candidates for the pair; ties and ordering: " + TIE_RULE + ".")
    out.append("")
    out.append("OBSERVATIONS (what %s changed, with bar numbers; observations, not fitted rules)" % owner)
    for r in result["transitions"]:
        for o_ in r["observations"]:
            out.append("  " + o_)
    for p in result["patterns"]:
        out.append("  across the set: " + p)
    return "\n".join(out)
