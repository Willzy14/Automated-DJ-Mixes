"""Phrase blend planner: place a DJ blend between two tracks from their phrase points.

Standalone and pure. Not wired into propose_arrangement.py or align_engine.py.

WHAT THE SIX ANSWER-KEY BLENDS DO AND DO NOT PROVE (state of round 4, measured, see
Tools/score_blends.py which prints each of these live):
  - R6 (bottom end) and the measured content are INERT on these six blends: with W_FLAT_RUN = 0
    the unaided score is the same as with it on.  The score comes from geometry (phrase points,
    the one-minute entry, the end target), not from what is playing.
  - In all six owner blends the bass switch is exactly entry + 16 bars.  The six therefore carry
    almost no information about the switch rule; they cannot tell "entry + 16" from "30 seconds
    from the end" from "a phrase point of both tracks".  Tools/blend_candidates.py finds pairs
    that can.
  - The weights are hand-set judgements.  Constants that were set from a single case say which
    (END_SLACK_BARS from blend 2).  Nothing here is "not tuned": the six were run many times
    while the rules were written, and the score is reported as measured, not as a validation.

Inputs are dicts shaped like Documentation/Golden Sections/Answer Key 2026-11-20 DFTD.json
-> tracks[name]:  bpm, n_bars, sections = [[start_bar, LABEL] or [start_bar, LABEL, carries]],
fills = [[start_bar, end_bar]].  Bars are 0-based from the first beat.  The optional third
element of a section (a bool, or {"carries": bool}) overrides the content test used by the
flat-blend check (rule R6).  A track may also carry "content": {"has_bass": [...], "has_music":
[...], "has_kick": [...]} (0/1 per bar, from Tools/measure_section_content.py); R6 then uses
MEASURED content.  A track without usable content (none, empty lists, lists of different
lengths, or no has_kick under the bottom-end rule) falls back to labels, and `why` says so.

Blend geometry (all numbers are the OUTGOING track's own bar numbers):
  in_starts_at_out_bar   where the incoming track's bar 0 plays
  bass_switch_out_bar    where the incoming bass takes over and the outgoing bass leaves
  out_end_bar            where the outgoing stops.  > n_bars: its tail is looped.  < n_bars: cut.
TEMPO ASSUMPTION: one bar of either track is one blend bar, because the pipeline warps both
tracks to one tempo.  Clocks (a minute, 30 seconds, the mix zones) use each track's own bpm and
4/4 bars (240 / bpm seconds).  Loops are never part of a track: every clock below measures to
the ORIGINAL outgoing end, min(out_end, n_bars).

The rules (the owner's, a working DJ) are cited as R1..R9 beside the code that uses them.
"""
from dataclasses import dataclass, field

# ---- hand-set weights (cost; lower is better) -------------------------------------------
W_FLAT = 10.0          # R6 v1 (round 2 rule, kept for comparison): flat is a per-bar ban
W_CUT = 12.0           # R5: only orders plans when EVERY candidate cuts; a cut is otherwise excluded
W_LOOP = 1.5           # R4: prefer no loop; a loop is the cheap way to lengthen a blend
W_ENTRY_SECTION = 1.0  # R1: entry "normally" on a fill END; a plain section start is second best
W_ENTRY_OFF = 6.0      # R1 is "normally", not "always"; an unphrased entry is allowed but costs
W_SWITCH_NONE = 12.0   # R2: a switch on a phrase point of NEITHER track is the worst case; a hit
#                        on one track is free and a second hit earns nothing (the owner never said
#                        "both"; his own switches in blends 3, 4 and 6 hit one track only)
W_LEN = 0.5            # R1: aim for about a minute; the clock is soft, the phrase decides
W_SWITCH_TIME = 0.25   # R2: switch roughly 30 s from the end; softest clock term
W_MINUTE_OFF = 0.5     # owner: a phrase point a minute before the end is "an awareness", not a rule,
#                        so an entry that is not one costs less than any other term except a clock tick

# R6 "bottom end" (BE), from Sam's words (2026-11-20): "what you don't want to be doing is
# putting an outro that doesn't have a kick in it, that's just percussion, mixing with an intro
# that doesn't have a kick in it ... There's no bottom end energy in that mix then ... You want
# to try and keep the energy up all the time."  A blend bar is FLAT when neither side gives
# bottom end (kick or bass; after the switch the outgoing's bass is cut, so only its kick).
# Music (vocal, pads) does not rescue a bar; it is only reported.  And, his earlier words,
# drums over drums "sometimes works, you just don't want it to happen for ages", so flatness
# costs by the length of each CONTINUOUS flat run, not per bar:
#   cost = W_FLAT_RUN * max(0, run_bars - FREE_FLAT_BARS) ** 2, summed over runs.
FREE_FLAT_BARS = 8.0   # one phrase (8 bars, about 15 s): "sometimes works" costs nothing up to
#                        here, which also keeps a one or two bar fill (kick drops for a bar) free
W_FLAT_RUN = 0.25      # "ages" = a full minute (32 bars): 0.25 * 24**2 = 144, the same as cutting
#                        12 bars of outro (W_CUT).  Sam gave no numbers; a judgement, not a measurement.
R6_V1 = "v1"           # round 2: bass or music counts; per-bar flat ban (kept for comparison)
R6_BE = "be"           # round 3: bottom end (kick or bass); convex per-run flat cost

# Mix zone, from the owner: "roughly the first and last minute".  Converted with each track's own
# bpm.  Blend 2 (PAX into Passion, entry 33 bars = 63 s from the end) is what stops this being
# tighter than about 63 s; 75 s is a minute plus one phrase of margin.
MIX_ZONE_SECONDS = 75.0
MINUTE_SECONDS = 60.0
MINUTE_TOL_BARS = 2.0   # a "one-minute mark" is a phrase point within 2 bars (about 4 s) of a minute
# n_bars counts a partial final bar (PAX 193): a target within 1 bar before n_bars is "reached
# naturally", not a cut.  SET FROM BLEND 2 (the only case), and sits exactly on its edge: 0.5 flips
# blend 2.  It applies to every track, including one with a full last bar.
END_SLACK_BARS = 1.0
MIN_SIDE_BARS = 4.0     # the switch must sit at least 4 bars after entry and before the end
MAX_TAIL_LOOP = 24.0    # never loop an outgoing tail by more than 24 bars (a bar count, not a clock)
MIN_IN_LOOP = 4.0       # an incoming intro loop is a whole phrase of at least 4 bars
MIN_LOOP_SOURCE = 4.0   # an outgoing tail loops a whole phrase of at least 4 bars
MAX_LOOP_SOURCE = 16.0  # ... and repeats at most its last 16 bars
LOOP_SOURCE_KICK = 0.75  # a loop source phrase must have its kick in at least 75% of its bars

CARRY_LABELS = ("DROP", "BUILD", "BREAK")     # v1: INTRO and OUTRO do not carry unless flagged
CARRY_LABELS_BE = ("DROP", "BUILD")           # BE: a BREAK is not assumed to have a kick


@dataclass
class BlendPlan:
    in_starts_at_out_bar: float
    bass_switch_out_bar: float
    out_end_bar: float
    out_looped_bars: float
    in_intro_looped_bars: float
    out_cut_bars: float = 0.0
    stretches: list = field(default_factory=list)
    flat_bars: float = 0.0
    cost: float = 0.0
    why: list = field(default_factory=list)
    flat_runs: list = field(default_factory=list)   # [(start out bar, length)]
    terms: dict = field(default_factory=dict)       # cost broken down term by term
    out_loop_source_bars: tuple = None              # (start, end) of the repeated outgoing phrase
    in_loop_phrase_end: float = 0.0                 # the incoming intro loop repeats [end - L, end)
    minute_mark: bool = False

    def key(self):
        return (self.in_starts_at_out_bar, self.bass_switch_out_bar, self.out_end_bar)

    def full_key(self):
        """key() plus the incoming loop: two plans with the same key can play different bars."""
        return self.key() + (self.in_intro_looped_bars, self.in_loop_phrase_end)


# ---- track helpers ----------------------------------------------------------------------
def _valid(track):
    try:
        return float(track["bpm"]) > 0 and float(track["n_bars"]) > 0
    except (KeyError, TypeError, ValueError):
        return False


def _bar_seconds(track):
    return 240.0 / float(track["bpm"])


def _zone_bars(track):
    return MIX_ZONE_SECONDS / _bar_seconds(track)


def _sections(track):
    out = []
    for s in track.get("sections", []):
        flag = None
        if len(s) > 2:
            f = s[2]
            flag = f.get("carries") if isinstance(f, dict) else f
            flag = None if flag is None else bool(flag)
        out.append((float(s[0]), str(s[1]).upper(), flag))
    return sorted(out, key=lambda t: t[0])


def _section_at(track, bar):
    secs = _sections(track)
    cur = secs[0] if secs else (0.0, "INTRO", None)
    for s in secs:
        if s[0] <= bar + 1e-9:
            cur = s
    return cur


def phrase_points(track):
    """Every section start, every fill END, the track start and the track end."""
    pts = {0.0, float(track["n_bars"])}
    pts.update(s[0] for s in _sections(track))
    pts.update(float(f[1]) for f in track.get("fills", []))
    return sorted(pts)


def target_points(track):
    """Where an outgoing track may END against this incoming track (R3): a section start
    or the START of a fill."""
    pts = {s[0] for s in _sections(track)}
    pts.update(float(f[0]) for f in track.get("fills", []))
    pts.discard(0.0)
    return sorted(pts)


def _intro_loop_options(track):
    """R3: whole-phrase loops of the incoming intro: consecutive phrase points inside the
    INTRO labelled stretch, at least MIN_IN_LOOP bars long.  Returns [(p, L)]."""
    secs = _sections(track)
    first_non_intro = next((s[0] for s in secs if s[1] != "INTRO"), float(track["n_bars"]))
    pts = [p for p in phrase_points(track) if p <= first_non_intro]
    return [(b, b - a) for a, b in zip(pts, pts[1:]) if b - a >= MIN_IN_LOOP]


def _measured(track, bar, r6=R6_BE):
    """Measured (has_bass, has_music, has_kick) at a bar, or None when the track has no usable
    content: none, empty lists, lists of different lengths, or (under bottom end) no has_kick.
    Bars past the end read as the last bar; callers map looped bars before calling."""
    c = track.get("content")
    if not c:
        return None
    hb, hm, hk = c.get("has_bass"), c.get("has_music"), c.get("has_kick")
    if not hb or not hm or len(hb) != len(hm):
        return None
    if hk is not None and len(hk) != len(hb):
        return None
    if r6 == R6_BE and not hk:
        return None
    i = min(max(int(bar + 1e-9), 0), len(hb) - 1)
    return bool(hb[i]), bool(hm[i]), bool(hk[i]) if hk else False


def loop_source(track, r6=R6_BE):
    """R4/R3: the outgoing phrase a tail loop repeats: the latest whole phrase (consecutive phrase
    points, at least MIN_LOOP_SOURCE bars, at most the last MAX_LOOP_SOURCE of them) that still has
    its kick in at least LOOP_SOURCE_KICK of its bars.  Not simply the final bar, which may be a fade
    or a fill.  Returns (start, end); without measured kick any whole phrase will do."""
    n = float(track["n_bars"])
    pts = phrase_points(track)
    for p, q in reversed(list(zip(pts, pts[1:]))):
        if q - p < MIN_LOOP_SOURCE:
            continue
        p = max(p, q - MAX_LOOP_SOURCE)
        m = _measured(track, p, r6)
        if m is not None:
            c = track["content"]["has_kick"]
            bars = list(range(int(p), int(q)))
            if bars and sum(1 for b in bars if b < len(c) and c[b]) < LOOP_SOURCE_KICK * len(bars):
                continue
        return (p, q)
    return (max(0.0, n - MIN_LOOP_SOURCE), n)


def _out_bar(track, x, src):
    """Map an out bar to the bar whose content plays: bars past the end repeat the loop source."""
    n = float(track["n_bars"])
    if x < n - 1e-9:
        return x
    length = src[1] - src[0]
    return src[0] + ((x - n) % length) if length > 0 else n - 1


def _label_set(r6):
    return CARRY_LABELS if r6 == R6_V1 else CARRY_LABELS_BE


def _out_state(track, x, switch, r6=R6_BE, src=None):
    """R6: does the outgoing give what the blend needs at out bar x?  Priority: an explicit
    section `carries` flag, then MEASURED content, then labels.
    Bottom end (BE): before the switch the outgoing needs kick or bass; after it its bass has
    been cut, so only its kick counts.  v1: bass or music before, music only after.
    Labels: DROP/BUILD carry (BE) or DROP/BUILD/BREAK (v1), and only before the switch."""
    n = float(track["n_bars"])
    if src is None and x >= n - 1e-9:
        src = loop_source(track, r6)
    xm = _out_bar(track, x, src)
    sec = _section_at(track, min(xm, n - 1e-6))
    if sec[2] is not None:
        return sec[2]
    m = _measured(track, xm, r6)
    if m is not None:
        if r6 == R6_V1:
            return (m[0] or m[1]) if x < switch - 1e-9 else m[1]
        return (m[0] or m[2]) if x < switch - 1e-9 else m[2]
    return x < switch - 1e-9 and sec[1] in _label_set(r6)


def _in_state(track, j, x, switch, r6=R6_BE):
    """R6/R7: the incoming's lows are cut before the switch, so it gives no bottom end there
    (only percussion).  After the switch: BE counts kick or bass; v1 counts bass or music.
    A section `carries` flag, then labels, apply when there is no measured content."""
    if x < switch - 1e-9:
        return False
    n = float(track["n_bars"])
    sec = _section_at(track, min(max(j, 0.0), n - 1e-6))
    if sec[2] is not None:
        return sec[2]
    m = _measured(track, max(j, 0.0), r6)
    if m is not None:
        return (m[0] or m[1]) if r6 == R6_V1 else (m[0] or m[2])
    return sec[1] in _label_set(r6)


def _in_bar(x, a, loop_p, loop_l):
    """Incoming bar playing at out bar x, with an optional intro loop of [p-L, p)."""
    if loop_l <= 0 or x < a + loop_p:
        return x - a
    if x < a + loop_p + loop_l:
        return loop_p - loop_l + (x - a - loop_p)
    return x - a - loop_l


def content_check(out_t, in_t, a, s, e, loop_p=0.0, loop_l=0.0, r6=R6_BE, out_src=None):
    """R6 per-stretch content check.  Walks the blend bar by bar and merges runs of the same
    (outgoing gives, incoming gives) state, so adjacent flat bars form one flat run.  Returns
    (stretches, flat_bars); a stretch is {start, end, out, in, flat, music_bars} where music_bars
    counts bars in it where either side has measured music (reported, never rescues a flat bar).
    Looped outgoing bars (x >= n_bars) read their content from the loop source phrase."""
    stretches, flat = [], 0.0
    if out_src is None and e > float(out_t["n_bars"]) + 1e-9:
        out_src = loop_source(out_t, r6)
    x = a
    while x < e - 1e-9:
        nxt = min(x + 1.0, e)
        j = _in_bar(x, a, loop_p, loop_l)
        o = _out_state(out_t, x, s, r6, out_src)
        i = _in_state(in_t, j, x, s, r6)
        f = (not o) and (not i)
        mo = _measured(out_t, _out_bar(out_t, x, out_src or (0.0, 1.0)), r6)
        mi = _measured(in_t, max(j, 0.0), r6)
        music = bool((mo and mo[1]) or (mi and mi[1] and x >= s - 1e-9))
        if f:
            flat += nxt - x
        if stretches and stretches[-1]["out"] == o and stretches[-1]["in"] == i:
            stretches[-1]["end"] = nxt
            stretches[-1]["music_bars"] += (nxt - x) if music else 0.0
        else:
            stretches.append({"start": x, "end": nxt, "out": o, "in": i, "flat": f,
                              "music_bars": (nxt - x) if music else 0.0})
        x = nxt
    return stretches, flat


def flat_runs(stretches):
    """Maximal continuous flat runs as (start out bar, length).  Flat means both sides are
    False, a single state, so adjacent flat stretches are already merged."""
    return [(st["start"], st["end"] - st["start"]) for st in stretches if st["flat"]]


def flat_cost(runs, r6=R6_BE):
    """v1: per-bar ban.  BE: convex per continuous run, free up to one phrase."""
    if r6 == R6_V1:
        return W_FLAT * sum(n for _, n in runs)
    return W_FLAT_RUN * sum(max(0.0, n - FREE_FLAT_BARS) ** 2 for _, n in runs)


# ---- candidate generation and scoring ---------------------------------------------------
def _score(out_t, in_t, a, s, e, loop_p, loop_l, out_pts, in_pts, r6=R6_BE):
    n_out = float(out_t["n_bars"])
    why, cost, terms = [], 0.0, {}
    fill_ends = {float(f[1]) for f in out_t.get("fills", [])}
    sec_starts = {x[0] for x in _sections(out_t)}
    bars_min = MINUTE_SECONDS / _bar_seconds(out_t)       # bars in a minute, outgoing tempo

    # R1: entry on an outgoing phrase point, normally a fill end
    if a in fill_ends:
        why.append("R1: entry at out bar %g is the end of an outgoing fill" % a)
    elif a in sec_starts:
        cost += W_ENTRY_SECTION
        terms["entry"] = W_ENTRY_SECTION
        why.append("R1: entry at out bar %g is an outgoing section start (not a fill end)" % a)
    else:
        cost += W_ENTRY_OFF
        terms["entry"] = W_ENTRY_OFF
        why.append("R1: entry at out bar %g is NOT an outgoing phrase point (placed back from the switch)" % a)

    # owner awareness (soft): a phrase point about a minute before the outgoing END
    mark = n_out - bars_min
    is_phrase = any(abs(a - p) < 1e-9 for p in out_pts)
    minute = is_phrase and abs(a - mark) <= MINUTE_TOL_BARS
    if minute:
        why.append("awareness: entry at out bar %g is a one-minute mark (a phrase point about a minute "
                   "before the outgoing end, %g)" % (a, mark))
    else:
        cost += W_MINUTE_OFF
        terms["minute_mark"] = W_MINUTE_OFF

    # R2: switch on a phrase point of at least one track
    k = s - a - loop_l
    out_hit = any(abs(s - p) < 1e-9 for p in out_pts)
    in_hit = any(abs(k - p) < 1e-9 for p in in_pts)
    if out_hit or in_hit:
        who = "both tracks" if out_hit and in_hit else ("the outgoing only" if out_hit else "the incoming only")
        why.append("R2: switch is a phrase point of %s (out bar %g, incoming bar %g)" % (who, s, k))
    else:
        cost += W_SWITCH_NONE
        terms["switch_phrase"] = W_SWITCH_NONE
        why.append("R2: switch is on no phrase point of either track (out bar %g, incoming bar %g)" % (s, k))

    # R3/R4/R5: end, loops, cuts.  Loops are never part of the track: clocks use e0.
    looped = max(0.0, e - n_out)
    out_end = e
    cut = 0.0
    if looped > 0:
        cost += W_LOOP * looped
        terms["out_loop"] = W_LOOP * looped
        why.append("R3/R4: outgoing tail looped %g bars to reach the incoming phrase point at incoming bar %g"
                   % (looped, e - a - loop_l))
    elif n_out - e > END_SLACK_BARS + 1e-9:
        cut = n_out - e
        cost += W_CUT * cut
        terms["cut"] = W_CUT * cut
        why.append("R5: outgoing outro CUT by %g bars" % cut)
    else:
        out_end = n_out
        why.append("R3: outgoing ends naturally on the incoming phrase point at incoming bar %g"
                   % (e - a - loop_l))
    e0 = min(out_end, n_out)
    if loop_l > 0:
        cost += W_LOOP * loop_l
        terms["in_loop"] = W_LOOP * loop_l
        why.append("R3/R4: incoming intro looped %g bars (phrase ending at incoming bar %g)" % (loop_l, loop_p))

    # R1/R2 clocks, soft, to the ORIGINAL end
    terms["length"] = W_LEN * abs((e0 - a) - bars_min)
    terms["switch_time"] = W_SWITCH_TIME * abs((e0 - s) - bars_min / 2.0)
    cost += terms["length"] + terms["switch_time"]
    bs = _bar_seconds(out_t)
    why.append("blend %g bars (%.0f s), switch %g bars (%.0f s) before the original end"
               % (e0 - a, (e0 - a) * bs, e0 - s, (e0 - s) * bs))
    if _valid(in_t):
        in_min = MINUTE_SECONDS / _bar_seconds(in_t)
        end_in = e - a - loop_l
        why.append("incoming one-minute mark is its bar %g; the blend ends at incoming bar %g and the "
                   "switch is at incoming bar %g" % (in_min, end_in, k))

    # R6: flat check
    src = loop_source(out_t, r6) if looped > 0 else None
    stretches, flat = content_check(out_t, in_t, a, s, out_end, loop_p, loop_l, r6, src)
    runs = flat_runs(stretches)
    flat_c = flat_cost(runs, r6)
    cost += flat_c
    terms["flat"] = flat_c
    why.append("R6: %g flat bars in runs %s" % (flat, ["%g+%g" % r for r in runs]) if flat
               else "R6: no flat stretch (one side gives bottom end throughout)")
    if r6 == R6_BE:
        for nm, t in (("outgoing", out_t), ("incoming", in_t)):
            c = t.get("content")
            if c and _measured(t, 0, R6_BE) is None:
                why.append("R6: %s content unusable under bottom end (no has_kick, empty or mismatched "
                           "lists); fell back to labels for it" % nm)

    return BlendPlan(in_starts_at_out_bar=a, bass_switch_out_bar=s, out_end_bar=out_end,
                     out_looped_bars=looped, in_intro_looped_bars=loop_l, out_cut_bars=cut,
                     stretches=stretches, flat_bars=flat, cost=cost, why=why,
                     flat_runs=runs, terms=terms, out_loop_source_bars=src,
                     in_loop_phrase_end=loop_p if loop_l > 0 else 0.0, minute_mark=minute)


def _rank_key(b):
    """Cheapest first; on an exact tie prefer no incoming loop, then less outgoing loop, then the
    earlier entry, switch and end."""
    return (round(b.cost, 9), b.in_intro_looped_bars, b.out_looped_bars,
            b.in_starts_at_out_bar, b.bass_switch_out_bar, b.out_end_bar)


def candidates(out_t, in_t, limit=None, r6=R6_BE):
    """Ranked list of BlendPlan, best first.  Switch points come from ORIGINAL phrase points
    only (R4); loops only lengthen the blend.  Entries and switches outside the mix zones
    are not considered (R9).  R5 is HARD: a plan that cuts the outgoing before its natural end is
    excluded whenever any non-cutting plan (natural end or looped tail) exists."""
    if not (_valid(out_t) and _valid(in_t)):
        return []
    n_out = float(out_t["n_bars"])
    out_pts = phrase_points(out_t)
    in_pts = phrase_points(in_t)
    zone_lo = n_out - _zone_bars(out_t)
    zone_in = _zone_bars(in_t)
    out_zone = [p for p in out_pts if zone_lo - 1e-9 <= p < n_out - 1e-9]
    in_zone = [p for p in in_pts if 0 < p <= zone_in + 1e-9]
    tgts = target_points(in_t)
    loops = [(0.0, 0.0)] + _intro_loop_options(in_t)

    trips = set()  # (a, s, loop_p, loop_l)
    for p, l in loops:
        for s in out_zone:                      # switch on an outgoing phrase point
            for k in in_zone:                   # ... and an incoming one: entry derived
                trips.add((s - k - l, s, p, l))
            for a in out_zone:                  # ... entry on an outgoing phrase point
                if a < s:
                    trips.add((a, s, p, l))
        for a in out_zone:                      # entry on a phrase point, switch on an incoming one
            for k in in_zone:
                trips.add((a, a + l + k, p, l))

    plans, seen = [], set()
    for a, s, p, l in trips:
        if a < 0 or a < zone_lo - 1e-9 or a >= n_out or s - a < MIN_SIDE_BARS:
            continue
        if s >= n_out - 1e-9:  # R4: a switch in the looped tail would be a loop-derived point
            continue
        k = s - a - l
        if k < p - 1e-9 or k > zone_in + 1e-9:
            continue
        for t in tgts:
            e = a + l + t
            if e - s < MIN_SIDE_BARS or e > n_out + MAX_TAIL_LOOP:
                continue
            e_eff = n_out if 0 <= n_out - e <= END_SLACK_BARS + 1e-9 else e   # slack: same plan
            sig = (round(a, 6), round(s, 6), round(e_eff, 6), p, l)
            if sig in seen:
                continue
            seen.add(sig)
            plans.append(_score(out_t, in_t, a, s, e, p, l, out_pts, in_pts, r6))
    if any(b.out_cut_bars == 0 for b in plans):
        plans = [b for b in plans if b.out_cut_bars == 0]
    plans.sort(key=_rank_key)
    return plans[:limit] if limit else plans


def plan_blend(out_track, in_track, r6=R6_BE):
    """The single best blend, or None if no plan fits the mix zones (or a track is unusable)."""
    c = candidates(out_track, in_track, limit=1, r6=r6)
    return c[0] if c else None
