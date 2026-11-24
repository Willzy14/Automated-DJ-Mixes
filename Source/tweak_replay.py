"""Replay a hand-tweaked mix through the blend planner (Tools/score_tweak.py is the CLI).

For each transition the placement in two Ableton sets (the planner's own set and the owner's
hand-edited copy) is decoded back into the planner's terms (the BlendPlan fields, bars in the
OUTGOING track's own numbering), then the owner's placement is put through phrase_blend:
is it the planner's top pick, one of its alternatives, ranked N of M, or outside its vocabulary.

Nothing here changes phrase_blend; it only calls it.  What this does NOT do: fit a scorer.  The
patterns it prints are observations with bar numbers, not rules.

Decoding rests on three facts about these sets (all checked by the round trip, below):
  - an arranged clip plays source beat `src0 + (t - arr0)` at arrangement beat t, one beat per
    beat, and planner bar = source beat / 4 (the detector clock, bar 0 = the downbeat);
  - the bass swap is the incoming's low-band automation rising from a held kill to full (else the
    outgoing's falling edge);
  - everything else (entry, intro trim, intro loop, tail loop, stop) is read from where clips start,
    replay or end.
Values within a quarter beat of a whole bar are "exact", within one beat "approximate" (snapped),
and anything further off the bar grid is "not decodable": the decoder refuses rather than guesses.

ROUND TRIP: decoding the planner's own set must reproduce the planner's own plan to within one
beat, or the decoder is not trusted and nothing is scored (RoundTripError).
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
EXACT_BARS = 0.0625    # a quarter beat: closer than this to a whole bar is "exact"
JOIN_BEATS = 0.1       # clips closer than this in time and source are one continuous play
MATCH_BARS = 0.125     # two decoded/planned placements are "the same" within half a beat
ROUND_TRIP_BARS = 0.25  # the round trip must agree "to within one beat"
SWAP_WINDOW_BEATS = 4.0
TRIVIAL_BARS = 16.0


class RoundTripError(RuntimeError):
    """The decoder could not reproduce the planner's own placement."""


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
    plays, top, prev_end = [], None, None
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
        prev_end = g.src1
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
    undecodable: str | None = None

    @property
    def scoreable(self):
        return self.undecodable is None and not self.shape_gaps

    def key(self):
        return (self.a, self.s, self.e, self.loop_l, self.loop_p, self.cut)

    def to_json(self):
        return {"entry": self.a, "swap": self.s, "end": self.e, "swap_incoming_bar": self.k,
                "intro_loop_bars": self.loop_l, "intro_loop_phrase_end": self.loop_p,
                "intro_cut_bars": self.cut, "tail_loop_source": self.out_loop_src,
                "precision": dict(self.exact), "raw_bars": {k: round(v, 4) for k, v in self.raw.items()},
                "notes": list(self.notes), "shape_gaps": list(self.shape_gaps),
                "model_gaps": list(self.model_gaps), "not_decodable": self.undecodable}


def _bar(pl, name, beats_or_bars, *, beats=False):
    """Snap to a whole bar: exact within EXACT_BARS, approximate within one beat, else refuse."""
    x = beats_or_bars / 4.0 if beats else beats_or_bars
    r = round(x)
    d = abs(x - r)
    pl.raw[name] = x
    if d <= EXACT_BARS:
        pl.exact[name] = "exact"
    elif d <= BEAT:
        pl.exact[name] = "approximate"
    else:
        pl.undecodable = pl.undecodable or "%s is %.2f bars, %.2f off the bar grid: not decodable" % (name, x, d)
        return None
    return float(r) + 0.0


def _edge(points, lo, hi, kind):
    prev = None
    for t, v in points:
        if prev is not None and (lo <= prev[0] <= hi or lo <= t <= hi):
            if kind == "rise" and prev[1] < 0.5 and v >= 0.9:
                return prev[0], t
            if kind == "fall" and prev[1] >= 0.8 and v < 0.8:
                return prev[0], t
        prev = (t, v)
    return None


def decode_transition(out_rec, in_rec):
    """The placement of in_rec entering over out_rec, in the planner's terms."""
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

    pl.cut = _bar(pl, "intro cut", i_plays[0].seg.src0, beats=True)
    hit = locate(o_plays, entry_arr)
    if hit is None or hit[2]:
        pl.undecodable = pl.undecodable or "the entry does not land on a natural stretch of the outgoing"
        return pl
    pl.a = _bar(pl, "entry", hit[0], beats=True)

    # incoming intro loop and other changes inside the blend
    first = i_plays[0]
    inside = [p for p in i_plays[1:] if p.seg.arr0 < end_arr - JOIN_BEATS]
    for n, p in enumerate(inside):
        if n == 0 and p.kind == "replay" and p.seg.src0 >= first.seg.src1 - (first.seg.src1 - first.seg.src0) - JOIN_BEATS \
                and p.seg.src0 >= first.seg.src0 - JOIN_BEATS and abs(p.seg.arr0 - first.seg.arr1) <= JOIN_BEATS \
                and p.seg.src0 < first.seg.src1 - JOIN_BEATS:
            pl.loop_l = _bar(pl, "intro loop", first.seg.src1 - p.seg.src0, beats=True)
            pl.loop_p = _bar(pl, "intro loop end", first.seg.src1, beats=True)
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
        pl.shape_gaps.append("an intro trim and an intro loop on the same incoming (the planner has one or the other)")

    # the bass swap
    lo, hi = entry_arr - SWAP_WINDOW_BEATS, end_arr + SWAP_WINDOW_BEATS
    rise = _edge(in_rec["bass"], lo, hi, "rise")
    fall = _edge(out_rec["bass"], lo, hi, "fall")
    pick = rise or fall
    if pick is None:
        pl.undecodable = pl.undecodable or "no bass swap automation found inside the overlap"
        return pl
    swap_arr = pick[0]
    pl.notes.append("swap read from the %s at arrangement beat %.2f" %
                    ("incoming's bass rise" if rise else "outgoing's bass fall (no incoming rise)", swap_arr))
    for who, edge in (("incoming bass rise", rise), ("outgoing bass fall", fall)):
        if edge and edge[1] - edge[0] > 1.0:
            pl.notes.append("%s is a ramp of %.1f beats (read from its start)" % (who, edge[1] - edge[0]))
    if rise and fall and abs(rise[0] - fall[0]) > 1.0:
        pl.model_gaps.append("staggered bass handover: the outgoing's bass leaves at out bar %s, the incoming's "
                             "enters at out bar %s (the planner models one swap bar)"
                             % ("%.4g" % round((locate(o_plays, fall[0]) or (fall[0],))[0] / 4.0, 2),
                                "%.4g" % round((locate(o_plays, rise[0]) or (rise[0],))[0] / 4.0, 2)))
    oh, ih = locate(o_plays, swap_arr), locate(i_plays, swap_arr)
    if oh is None or ih is None:
        pl.undecodable = pl.undecodable or "the swap lies outside a clip"
        return pl
    if oh[2]:
        pl.undecodable = pl.undecodable or "the swap lands on a replay of the outgoing"
        return pl
    pl.s = _bar(pl, "swap", oh[0], beats=True)
    pl.k = _bar(pl, "incoming swap bar", ih[0], beats=True)

    # the outgoing: skips, mid replays, tail loop, stop
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
    if tail and j > 0:
        base_top = max(p.seg.src1 for p in o_plays[:j] if p.kind != "replay")
        looped_beats = end_arr - tail[0].seg.arr0
        new_bars = max(p.new_beats for p in tail) / 4.0
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
        pl.e = _bar(pl, "end", o_plays[-1].seg.src1, beats=True)
    if pl.undecodable:
        return pl
    expect_k = pl.s - pl.a - pl.loop_l + pl.cut
    if abs(pl.k - expect_k) > BEAT:
        pl.shape_gaps.append("the incoming bar at the swap (%g) is not entry, loop and trim made it (%g): "
                             "something between entry and swap is not representable" % (pl.k, expect_k))
    return pl


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
    """{pair index: 'intro_loop' | 'tail_loop'} from a pipeline log (phase2.log lines 'T5: dropped intro_loop, ...')."""
    return {int(m.group(1)): m.group(2) for m in re.finditer(r"T(\d+): dropped (intro_loop|tail_loop)", text)}


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


def compare_placements(x, y, tol):
    """[field names that differ by more than tol] between two Placements; a missing value differs."""
    bad = []
    for name in ("a", "s", "e", "loop_l", "cut") + (("loop_p",) if (x.loop_l or y.loop_l) else ()):
        u, v = getattr(x, name), getattr(y, name)
        if u is None or v is None or abs(u - v) > tol + 1e-9:
            bad.append(name)
    return bad


def check_round_trip(decoded, expected, names, tol=ROUND_TRIP_BARS):
    """Decoder of the planner's own set vs the planner's own plans.  Returns the per-transition rows;
    raises RoundTripError (with every mismatch) when any transition disagrees by more than one beat."""
    rows, bad = [], []
    for i, (d, p) in enumerate(zip(decoded, expected), 1):
        if d.undecodable:
            diff = ["not decodable: " + d.undecodable]
        elif p is None:
            diff = ["the planner has no plan for this pair"]
        else:
            diff = compare_placements(d, p, tol)
        rows.append({"transition": i, "out": names[i - 1], "in": names[i], "decoded": d.key(),
                     "planner": p.key() if p else None, "differs": diff})
        if diff:
            bad.append(rows[-1])
    if bad:
        raise RoundTripError("decoder does not reproduce the planner's own set on transition(s) %s: %s" % (
            ", ".join(str(r["transition"]) for r in bad),
            "; ".join("T%d decoded %s vs planner %s differs in %s" % (r["transition"], r["decoded"], r["planner"],
                                                                     ", ".join(r["differs"])) for r in bad)))
    return rows


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


def same_placement(plan, pl, tol=MATCH_BARS):
    if pl.a is None or pl.s is None or pl.e is None:
        return False
    close = lambda u, v: abs(u - v) <= tol + 1e-9
    return (close(plan.in_starts_at_out_bar, pl.a) and close(plan.bass_switch_out_bar, pl.s)
            and close(plan.out_end_bar, pl.e) and close(plan.in_intro_looped_bars, pl.loop_l)
            and (pl.loop_l == 0 or close(plan.in_loop_phrase_end, pl.loop_p))
            and close(plan.in_intro_cut_bars, pl.cut))


def distance(x, y):
    """Bars between two placements: entry + swap + end (what a listener hears move)."""
    return sum(abs(u - v) for u, v in ((x.a, y.a), (x.s, y.s), (x.e, y.e)))


def explain_outside(o, n, pl, plan):
    """Why a placement is not in candidates(): the planner's own checks, in its order, run on it."""
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
    """(rank, plan) of the candidate closest to a placement (entry, swap, end, loop, cut)."""
    best = None
    for rank, c in enumerate(cs, 1):
        d = (abs(c.in_starts_at_out_bar - pl.a) + abs(c.bass_switch_out_bar - pl.s)
             + (abs(c.out_end_bar - pl.e) if pl.e is not None and pl.scoreable else 0.0)
             + abs(c.in_intro_looped_bars - pl.loop_l) + abs(c.in_intro_cut_bars - pl.cut))
        if best is None or d < best[0] - 1e-12:
            best = (d, rank, c)
    return (best[1], best[2], best[0]) if best else None


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
            "out_cut_bars": plan.out_cut_bars, "out_trim_bars": plan.out_trim_bars}


def analyse_transition(index, names, o, n, base, sam, r6=pb.R6_BE):
    """The full replay record for one transition (see the module docstring)."""
    rec = {"transition": index, "out": names[0], "in": names[1], "baseline": base.to_json(),
           "sam": sam.to_json(), "kept": None, "position": None, "planner_top": None,
           "sam_scored": None, "terms_difference": None, "decisive_term": None, "vocabulary": [],
           "trivial": None, "candidates": 0}
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

    triv = trivial_placement(o, n)
    triv_plan = score_placement(o, n, triv, r6)
    t = {"placement": {"entry": triv.a, "swap": triv.s, "end": triv.e}, "note": triv.notes[0],
         "cost": round(triv_plan.cost, 4) if triv_plan else None,
         "terms": {k: round(v, 4) for k, v in sorted(triv_plan.terms.items())} if triv_plan else None,
         "in_vocabulary": any(same_placement(c, triv) for c in cs)}
    if triv_plan and not t["in_vocabulary"]:
        t["outside_because"] = explain_outside(o, n, triv, triv_plan)
    if top and sam.a is not None and sam.s is not None and sam.e is not None:
        ds = distance(sam, triv)
        dp = distance(placement_from_plan(top), triv)
        t.update(sam_distance=ds, planner_distance=dp,
                 closer="Sam's placement" if ds < dp - 1e-9 else "the planner's pick" if dp < ds - 1e-9 else "neither (tie)")
    rec["trivial"] = t
    rec["trivial_plan_cost"] = triv_plan.cost if triv_plan else None

    if sam.undecodable:
        rec["position"] = {"kind": "not decodable", "why": sam.undecodable}
        return rec
    if not sam.scoreable:
        near = nearest_candidate(cs, sam) if cs else None
        rec["position"] = {"kind": "outside the vocabulary", "rank": None, "of": len(cs),
                           "why": list(sam.shape_gaps), "scored": False}
        if near:
            rec["position"]["nearest"] = {"rank": near[0], "plan": _plan_json(near[1])}
        rec["vocabulary"] = list(sam.shape_gaps) + list(sam.model_gaps)
        return rec

    sam_plan = score_placement(o, n, sam, r6)
    if sam_plan is None:
        rec["position"] = {"kind": "not scoreable", "why": "the planner's cost function rejected the placement"}
        return rec
    rec["sam_scored"] = _plan_json(sam_plan)
    hit = next((i for i, c in enumerate(cs) if same_placement(c, sam)), None)
    alt = next((i for i, a in enumerate(top.alternatives, 1) if same_placement(a.plan, sam)), None) if top else None
    pos = {"of": len(cs), "scored": True}
    if hit is None:
        better = sum(1 for c in cs if pb._rank_key(c) < pb._rank_key(sam_plan))
        pos.update(kind="outside the vocabulary", rank=None, would_rank=better + 1,
                   why=explain_outside(o, n, sam, sam_plan))
        near = nearest_candidate(cs, sam)
        if near:
            pos["nearest"] = {"rank": near[0], "plan": _plan_json(near[1]), "distance": round(near[2], 3)}
    else:
        pos.update(kind="top pick" if hit == 0 else "listed alternative" if alt else "ranked",
                   rank=hit + 1, alternative=alt)
    rec["position"] = pos
    if top:
        rec["terms_difference"], rec["decisive_term"] = _terms_diff(sam_plan, top)
        above = [c for c in cs if pb._rank_key(c) < pb._rank_key(sam_plan)]
        pos["outranked_by_costlier"] = sum(1 for c in above if c.cost > sam_plan.cost + 1e-9)
    rec["vocabulary"] = (pos.get("why") or []) + list(sam.model_gaps)
    return rec


# ---- observations and summary ------------------------------------------------------------------
def _section_at_start(track, bar):
    return next((x[1] for x in pb._sections(track) if abs(x[0] - bar) < 1e-6), None)


def observations(index, o, n, base, sam):
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
        out.append("%s: Sam's swap lands on incoming bar %g, the start of a %s section" % (t, sam.k, label))
    out.extend("%s: %s" % (t, g) for g in sam.shape_gaps + sam.model_gaps)
    return out


def summarise(records, tracks_by_pair):
    n = len(records)
    pos = [r["position"] or {} for r in records]
    kept = [r["transition"] for r in records if r["kept"]]
    same_top = [r["transition"] for r, p in zip(records, pos) if p.get("kind") == "top pick"]
    top3 = [r["transition"] for r, p in zip(records, pos) if p.get("rank") and p["rank"] <= 3]
    top_or_alt = [r["transition"] for r, p in zip(records, pos) if p.get("kind") in ("top pick", "listed alternative")]
    gaps = {}
    for r in records:
        if r["position"] and r["position"].get("kind") in ("outside the vocabulary", "not decodable"):
            gaps[r["transition"]] = r["vocabulary"] or [r["position"].get("why")]
    wins = loses = ties = 0
    tm = []
    for r in records:
        top, tc = r["planner_top"], r["trivial_plan_cost"]
        if top and tc is not None:
            wins += top["cost"] < tc - 1e-9
            loses += top["cost"] > tc + 1e-9
            ties += abs(top["cost"] - tc) <= 1e-9
        tr = r["trivial"]
        if r["sam_scored"] is not None or (r["sam"]["end"] is not None and r["sam"]["swap"] is not None):
            s = r["sam"]
            tp = tr["placement"]
            tm.append(abs(s["entry"] - tp["entry"]) < MATCH_BARS and abs(s["swap"] - tp["swap"]) < MATCH_BARS
                      and abs(s["end"] - tp["end"]) < MATCH_BARS)
    trivial_matches = sum(tm)
    closer = [r["trivial"].get("closer") for r in records if r["trivial"].get("closer")]
    cost_beat = wins > loses
    match_beat = len(same_top) > trivial_matches
    summary = {
        "transitions": n, "sam_changed_nothing": kept, "top_pick_equals_sam": same_top,
        "top_pick_or_listed_alternative": top_or_alt, "sam_in_top_3": top3, "vocabulary_gaps": gaps,
        "planner_cost_vs_trivial": {"planner_lower": int(wins), "planner_higher": int(loses), "equal": int(ties)},
        "trivial_equals_sam": int(trivial_matches), "closer_to_trivial": {
            "Sam's placement": closer.count("Sam's placement"), "the planner's pick": closer.count("the planner's pick"),
            "neither (tie)": closer.count("neither (tie)")},
        "planner_beats_trivial_on_cost": bool(cost_beat), "planner_matches_sam_more_than_trivial": bool(match_beat),
        "plain_verdict": None}
    if not cost_beat and not match_beat:
        summary["plain_verdict"] = ("The planner does NOT beat the trivial baseline (16 in, 16 out, swap on the "
                                    "outro start): it is not cheaper on more transitions (%d lower, %d higher) and "
                                    "it matches Sam on %d of %d where the trivial baseline matches on %d."
                                    % (wins, loses, len(same_top), n, trivial_matches))
    return summary


def pattern_lines(records, obs):
    """Observations across the set (counts of what changed), still not fitted rules."""
    lines = []
    trims = [(r["transition"], r["baseline"]["intro_cut_bars"], r["sam"]["intro_cut_bars"]) for r in records
             if r["baseline"]["intro_cut_bars"] and not r["sam"]["not_decodable"] and not r["baseline"]["not_decodable"]]
    if trims:
        lines.append("Intro trims in the planner's set: " + ", ".join(
            "T%d (%g bars, Sam %g)" % t for t in trims))
    loops = [(r["transition"], r["baseline"]["intro_loop_bars"], r["sam"]["intro_loop_bars"]) for r in records
             if r["baseline"]["intro_loop_bars"] != r["sam"]["intro_loop_bars"]]
    if loops:
        lines.append("Incoming intro loop changed: " + ", ".join("T%d (%g -> %g bars)" % t for t in loops))
    heard = [(r["transition"], r["baseline"], r["sam"]) for r in records
             if r["baseline"]["end"] is not None and r["sam"]["end"] is not None
             and r["baseline"]["entry"] is not None and r["sam"]["entry"] is not None]
    if heard:
        lines.append("Heard blend length in bars, planner set -> Sam: " + ", ".join(
            "T%d %g -> %g" % (i, b["end"] - b["entry"], s["end"] - s["entry"]) for i, b, s in heard))
        lines.append("Bars after the swap, planner set -> Sam: " + ", ".join(
            "T%d %g -> %g" % (i, b["end"] - b["swap"], s["end"] - s["swap"]) for i, b, s in heard))
        lines.append("Bars from entry to swap, planner set -> Sam: " + ", ".join(
            "T%d %g -> %g" % (i, b["swap"] - b["entry"], s["swap"] - s["entry"]) for i, b, s in heard))
    return lines


# ---- the whole replay --------------------------------------------------------------------------
def match_planner_names(als_names, planner_tracks):
    """{als name: planner track} by exact (unescaped) name, else a 30-character prefix."""
    out, low = {}, {html.unescape(k).strip().lower(): v for k, v in planner_tracks.items()}
    for name in als_names:
        key = html.unescape(name).strip().lower()
        v = low.get(key)
        if v is None:
            hits = [t for k, t in low.items() if k.startswith(key[:30]) or key.startswith(k[:30])]
            v = hits[0] if len(hits) == 1 else None
        if v is None:
            raise ValueError("no planner track data for %r" % name)
        out[name] = v
    return out


def replay(baseline_als, tweak_als, planner_tracks, decisions=None, r6=pb.R6_BE, dropped=None):
    """Decode both sets, run the round trip on the baseline, then replay the tweak.  `planner_tracks` is
    {track name: planner track dict}; `decisions` optionally supplies the round-trip reference instead of
    re-planning ({'transitions': [...]}); `dropped` is {pair: 'intro_loop'|'tail_loop'} for loops the pipeline's
    own quality gate removed after planning.  Raises RoundTripError before scoring anything on a mismatch."""
    base_tracks, sam_tracks = read_als(baseline_als), read_als(tweak_als)
    names = [t["name"] for t in base_tracks]
    if names != [t["name"] for t in sam_tracks]:
        raise ValueError("the two sets do not hold the same tracks in the same order:\n  %s\n  %s"
                         % (names, [t["name"] for t in sam_tracks]))
    if len(names) < 2:
        raise ValueError("a set needs at least two tracks")
    plan = match_planner_names(names, planner_tracks)
    base = [decode_transition(base_tracks[i], base_tracks[i + 1]) for i in range(len(names) - 1)]
    sam = [decode_transition(sam_tracks[i], sam_tracks[i + 1]) for i in range(len(names) - 1)]

    if decisions is not None:
        by_pair = {d["pair_index"]: d for d in decisions["transitions"]}
        expected = [placement_from_decision(by_pair[i]) if i in by_pair else None for i in range(1, len(names))]
        reference = "the decisions file"
    else:
        expected = []
        for i in range(len(names) - 1):
            top = pb.plan_blend(plan[names[i]], plan[names[i + 1]], r6=r6)
            expected.append(placement_from_plan(top) if top else None)
        reference = "plan_blend on the same pair"
    adjusted = {}
    for pair, kind in sorted((dropped or {}).items()):
        if not 1 <= pair < len(names) or expected[pair - 1] is None:
            raise ValueError("pipeline drop for pair %d has no planner placement" % pair)
        apply_dropped_loop(expected[pair - 1], kind, pb.natural_end_bar(plan[names[pair - 1]]))
        adjusted[pair] = kind
    round_trip = check_round_trip(base, expected, names)
    for row in round_trip:
        if row["transition"] in adjusted:
            row["adjusted"] = "the pipeline dropped this transition's %s after planning" % adjusted[row["transition"]]

    records, obs = [], []
    for i in range(len(names) - 1):
        o, n = plan[names[i]], plan[names[i + 1]]
        rec = analyse_transition(i + 1, (names[i], names[i + 1]), o, n, base[i], sam[i], r6)
        rec["observations"] = observations(i + 1, o, n, base[i], sam[i])
        obs.extend(rec["observations"])
        records.append(rec)
    return {"tracks": names, "round_trip": {"passed": True, "reference": reference, "rows": round_trip},
            "transitions": records, "summary": summarise(records, None), "patterns": pattern_lines(records, obs)}


# ---- the report --------------------------------------------------------------------------------
def _fmt(p):
    if p is None or p.get("entry") is None:
        return "not decodable"
    s = "entry %g, swap %g, end %g" % (p["entry"], p["swap"], p["end"])
    if p.get("intro_loop_bars"):
        s += ", intro loop %g (to incoming bar %g)" % (p["intro_loop_bars"], p["intro_loop_phrase_end"])
    if p.get("intro_cut_bars"):
        s += ", intro cut %g" % p["intro_cut_bars"]
    return s


def _terms(x):
    return " ".join("%s=%.2f" % (k, v) for k, v in sorted(x["terms"].items())) or "all terms zero"


def format_failed_round_trip(rows, message):
    lines = ["ROUND TRIP FAILED: %s" % message, "Nothing was scored.  The decoder is not trusted.", ""]
    for r in rows:
        lines.append("T%d %s -> %s: decoded %s | planner %s | %s" % (
            r["transition"], r["out"][:28], r["in"][:28], r["decoded"], r["planner"],
            "differs in " + ", ".join(r["differs"]) if r["differs"] else "agrees"))
    return "\n".join(lines)


def format_report(result):
    out = []
    rt = result["round_trip"]
    out.append("TWEAK REPLAY: the owner's hand edit put through the blend planner")
    out.append("Round trip (decoding the planner's own set vs %s, tolerance one beat): %s" % (
        rt["reference"], "PASSED on all %d transitions" % len(rt["rows"])))
    for r in rt["rows"]:
        out.append("  T%d decoded %s | planner %s%s" % (r["transition"], r["decoded"], r["planner"],
                                                       " [" + r["adjusted"] + "]" if r.get("adjusted") else ""))
    out.append("")
    for r in result["transitions"]:
        out.append("T%d. %s -> %s" % (r["transition"], r["out"][:34], r["in"][:34]))
        out.append("   planner's set : %s" % _fmt(r["baseline"] and {"entry": r["baseline"]["entry"], "swap": r["baseline"]["swap"],
                                                                      "end": r["baseline"]["end"],
                                                                      "intro_loop_bars": r["baseline"]["intro_loop_bars"],
                                                                      "intro_loop_phrase_end": r["baseline"]["intro_loop_phrase_end"],
                                                                      "intro_cut_bars": r["baseline"]["intro_cut_bars"]}))
        sam = r["sam"]
        out.append("   Sam's tweak   : %s" % _fmt({"entry": sam["entry"], "swap": sam["swap"], "end": sam["end"],
                                                    "intro_loop_bars": sam["intro_loop_bars"],
                                                    "intro_loop_phrase_end": sam["intro_loop_phrase_end"],
                                                    "intro_cut_bars": sam["intro_cut_bars"]}))
        prec = sam["precision"]
        out.append("   decoded       : %s%s" % (
            ", ".join("%s %s" % (k, v) for k, v in sorted(prec.items())) or "nothing",
            "; " + "; ".join(sam["notes"]) if sam["notes"] else ""))
        out.append("   Sam vs the planner's set: %s" % (
            "unchanged (Sam kept the planner's placement)" if r["kept"] else
            "changed" if r["kept"] is False else "cannot say (not decodable)"))
        pos = r["position"]
        top = r["planner_top"]
        if top:
            out.append("   planner's top : %s  cost %.2f  %d candidates" % (_fmt(top), top["cost"], r["candidates"]))
        else:
            out.append("   planner's top : NO PLAN")
        if pos["kind"] == "not decodable":
            out.append("   Sam's placement: NOT DECODABLE: %s" % pos["why"])
        elif pos["kind"] == "top pick":
            out.append("   Sam's placement: the planner's TOP PICK (rank 1 of %d)" % pos["of"])
        elif pos["kind"] == "listed alternative":
            out.append("   Sam's placement: a listed ALTERNATIVE (#%d), rank %d of %d" % (pos["alternative"], pos["rank"], pos["of"]))
        elif pos["kind"] == "ranked":
            out.append("   Sam's placement: reachable, RANKED %d of %d" % (pos["rank"], pos["of"]))
        else:
            out.append("   Sam's placement: OUTSIDE THE VOCABULARY%s" % (
                "" if pos.get("scored") else " (shape not expressible: not scored)"))
            for w in pos.get("why") or []:
                out.append("      because: %s" % w)
            if pos.get("would_rank"):
                out.append("      if it were a candidate it would rank %d of %d" % (pos["would_rank"], pos["of"] + 1))
            if pos.get("nearest"):
                out.append("      nearest candidate: rank %d, %s" % (pos["nearest"]["rank"], _fmt(pos["nearest"]["plan"])))
        if r["sam_scored"] and top:
            out.append("      cost: Sam %.2f%s, planner's top %.2f  (sane pairing: Sam %s, top %s)" % (
                r["sam_scored"]["cost"], "", top["cost"],
                "yes" if r["sam_scored"]["sane"] else "no", "yes" if top["sane"] else "no"))
            out.append("      Sam's terms   : " + _terms(r["sam_scored"]))
            out.append("      planner's terms: " + _terms(top))
            if r["terms_difference"]:
                out.append("      Sam minus planner by term: " + " ".join(
                    "%s=%+.2f" % (k, v) for k, v in sorted(r["terms_difference"].items())))
                if r["position"]["kind"] != "top pick":
                    out.append("      term that makes the difference: %s (%+.2f)" % (
                        r["decisive_term"], r["terms_difference"][r["decisive_term"]]))
            if pos.get("outranked_by_costlier"):
                out.append("      %d candidate(s) ranked above Sam's placement cost MORE than it does: the sane-pairing "
                           "tier (a swap lined up on a phrase point of both tracks), not the cost, orders them"
                           % pos["outranked_by_costlier"])
        elif r["sam_scored"] is None and pos["kind"] not in ("not decodable",):
            out.append("      Sam's cost: not scored")
        for alt in r.get("alternatives") or []:
            out.append("      listed alternative: swap %g, entry %g, end %g, cost %.2f" % (
                alt["swap"], alt["entry"], alt["end"], alt["cost"]))
        tr = r["trivial"]
        out.append("   trivial (16 in, 16 out, swap on the outro start): entry %g, swap %g, end %g, cost %s%s" % (
            tr["placement"]["entry"], tr["placement"]["swap"], tr["placement"]["end"],
            "%.2f" % tr["cost"] if tr["cost"] is not None else "n/a",
            "" if tr["in_vocabulary"] else " (outside the planner's vocabulary)"))
        out.append("      terms: " + (" ".join("%s=%.2f" % kv for kv in sorted(tr["terms"].items())) if tr["terms"] else "n/a")
                   + "; " + tr["note"])
        if tr.get("closer"):
            out.append("      closer to the trivial placement: %s (Sam %g bars away, planner's pick %g)" % (
                tr["closer"], tr["sam_distance"], tr["planner_distance"]))
        out.append("")
    s = result["summary"]
    n = s["transitions"]
    names = lambda v: ", ".join("T%d" % i for i in v) or "none"
    out.append("SUMMARY (%d transitions)" % n)
    out.append("  Sam changed nothing (planner's placement kept): %d of %d (%s)" % (
        len(s["sam_changed_nothing"]), n, names(s["sam_changed_nothing"])))
    out.append("  planner's top pick equals Sam's: %d of %d (%s)" % (
        len(s["top_pick_equals_sam"]), n, names(s["top_pick_equals_sam"])))
    out.append("  Sam's placement is the top pick or a listed alternative: %d of %d (%s)" % (
        len(s["top_pick_or_listed_alternative"]), n, names(s["top_pick_or_listed_alternative"])))
    out.append("  Sam's placement is in the planner's top 3: %d of %d (%s)" % (
        len(s["sam_in_top_3"]), n, names(s["sam_in_top_3"])))
    out.append("  vocabulary gaps / not decodable: %d (%s)" % (
        len(s["vocabulary_gaps"]), names(sorted(s["vocabulary_gaps"]))))
    for i, why in sorted(s["vocabulary_gaps"].items()):
        for w in why:
            out.append("      T%d: %s" % (i, w))
    c = s["planner_cost_vs_trivial"]
    out.append("  planner's top pick vs the trivial baseline on cost: lower on %d, higher on %d, equal on %d" % (
        c["planner_lower"], c["planner_higher"], c["equal"]))
    out.append("  trivial baseline equals Sam's placement: %d of %d; planner's top pick equals it: %d of %d" % (
        s["trivial_equals_sam"], n, len(s["top_pick_equals_sam"]), n))
    out.append("  closer to the trivial placement: %s" % ", ".join("%s %d" % kv for kv in s["closer_to_trivial"].items()))
    if s["plain_verdict"]:
        out.append("  PLAIN VERDICT: " + s["plain_verdict"])
    else:
        out.append("  Trivial baseline check: the planner beats it on %s." % (
            "both cost and matching Sam" if s["planner_beats_trivial_on_cost"] and s["planner_matches_sam_more_than_trivial"]
            else "cost only" if s["planner_beats_trivial_on_cost"] else "matching Sam only"))
    out.append("  (the cost comparison uses the planner's own cost function, so the planner winning on it is expected;"
               " matching Sam is the independent evidence)")
    out.append("")
    out.append("OBSERVATIONS (what Sam changed, with bar numbers; observations, not fitted rules)")
    for r in result["transitions"]:
        for o_ in r["observations"]:
            out.append("  " + o_)
    for p in result["patterns"]:
        out.append("  across the set: " + p)
    return "\n".join(out)
