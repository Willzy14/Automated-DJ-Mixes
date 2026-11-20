"""Phrase blend planner: place a DJ blend between two tracks from their phrase points.

Standalone and pure. Not wired into propose_arrangement.py or align_engine.py.

Inputs are dicts shaped like Documentation/Golden Sections/Answer Key 2026-11-20 DFTD.json
-> tracks[name]:  bpm, n_bars, sections = [[start_bar, LABEL] or [start_bar, LABEL, carries]],
fills = [[start_bar, end_bar]].  Bars are 0-based from the first beat.  The optional third
element of a section (a bool, or {"carries": bool}) overrides the content test used by the
flat-blend check (rule R6).  A track may also carry "content": {"has_bass": [...], "has_music":
[...]} (0/1 per bar, from Tools/measure_section_content.py); R6 then uses MEASURED content and
falls back to labels only for a track without it.

Blend geometry (all numbers are the OUTGOING track's own bar numbers):
  in_starts_at_out_bar   where the incoming track's bar 0 plays
  bass_switch_out_bar    where the incoming bass takes over and the outgoing bass leaves
  out_end_bar            where the outgoing stops.  > n_bars: its tail is looped.  < n_bars: cut.
Tempos are matched, so one bar of either track is one blend bar.

The nine rules (the owner's, a working DJ) are cited as R1..R9 beside the code that uses them.
Weights are hand-set; each has a one-line reason.  They were written from the stated rules
BEFORE the six answer-key blends were run and were not tuned to pass them.
"""
from dataclasses import dataclass, field

# ---- hand-set weights (cost; lower is better) -------------------------------------------
W_FLAT = 10.0          # R6 v1 (round 2 rule, kept for comparison): flat is a per-bar ban
W_CUT = 12.0           # R5: never cut a usable outro short; dearest thing a plan can do
W_LOOP = 1.5           # R4: prefer no loop; a loop is the cheap way to lengthen a blend
W_ENTRY_SECTION = 1.0  # R1: entry "normally" on a fill END; a plain section start is second best
W_ENTRY_OFF = 6.0      # R1 is "normally", not "always"; an unphrased entry is allowed but costs
W_SWITCH_ONE = 2.0     # R2: "where possible" on both tracks; one side only is acceptable
W_SWITCH_NONE = 12.0   # R2: a switch on no phrase point of either track is the worst case
W_LEN = 0.5            # R1: aim for about a minute; the clock is soft, the phrase decides
W_SWITCH_TIME = 0.25   # R2: switch roughly 30 s from the end; softest term of all
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
#                        12 bars of outro (W_CUT), the worst thing R5 forbids.  Sam gave no numbers;
#                        this is the one place a number is a judgement, not a measurement.
R6_V1 = "v1"           # round 2: bass or music counts; per-bar flat ban (kept for comparison)
R6_BE = "be"           # round 3: bottom end (kick or bass); convex per-run flat cost
R6_V2 = R6_BE          # alias so older callers keep working

MIX_ZONE_BARS = 64      # R9: first 64 bars of the incoming, last 64 of the outgoing
END_SLACK_BARS = 1.0    # n_bars counts a partial final bar (PAX 193): a target within 1 bar
#                         before n_bars is "reached naturally", not a cut
MIN_SIDE_BARS = 4.0     # switch must sit at least 4 bars after entry and before the end
MAX_TAIL_LOOP = 24.0    # never loop an outgoing tail by more than 24 bars
MIN_IN_LOOP = 4.0       # an incoming intro loop is a whole phrase of at least 4 bars

CARRY_LABELS = ("DROP", "BUILD", "BREAK")  # R6: INTRO and OUTRO do not carry unless flagged


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

    def key(self):
        return (self.in_starts_at_out_bar, self.bass_switch_out_bar, self.out_end_bar)


# ---- track helpers ----------------------------------------------------------------------
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


def _measured(track, bar):
    """Measured (has_bass, has_music, has_kick) at a bar, or None when the track carries no
    content.  track["content"] = {"has_bass": [0/1 per bar], "has_music": [...], "has_kick":
    [...]} as written by Tools/measure_section_content.py (has_kick optional: absent reads as
    no kick).  Bars past the end read as the last bar."""
    c = track.get("content")
    if not c:
        return None
    i = min(max(int(bar + 1e-9), 0), len(c["has_bass"]) - 1)
    k = c.get("has_kick")
    return bool(c["has_bass"][i]), bool(c["has_music"][i]), bool(k[i]) if k else False


def _out_state(track, x, switch, r6=R6_BE):
    """R6: does the outgoing give what the blend needs at out bar x?  Priority: an explicit
    section `carries` flag, then MEASURED content, then labels.
    Bottom end (BE): before the switch the outgoing needs kick or bass; after it its bass has
    been cut, so only its kick counts.  v1: bass or music before, music only after.
    Labels: DROP/BUILD/BREAK carry, and only before the switch."""
    n = float(track["n_bars"])
    sec = _section_at(track, min(x, n - 1e-6))
    if sec[2] is not None:
        return sec[2]
    m = _measured(track, x)
    if m is not None:
        if r6 == R6_V1:
            return (m[0] or m[1]) if x < switch - 1e-9 else m[1]
        return (m[0] or m[2]) if x < switch - 1e-9 else m[2]
    return x < switch - 1e-9 and sec[1] in CARRY_LABELS


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
    m = _measured(track, max(j, 0.0))
    if m is not None:
        return (m[0] or m[1]) if r6 == R6_V1 else (m[0] or m[2])
    return sec[1] in CARRY_LABELS


def _in_bar(x, a, loop_p, loop_l):
    """Incoming bar playing at out bar x, with an optional intro loop of [p-L, p)."""
    if loop_l <= 0 or x < a + loop_p:
        return x - a
    if x < a + loop_p + loop_l:
        return loop_p - loop_l + (x - a - loop_p)
    return x - a - loop_l


def content_check(out_t, in_t, a, s, e, loop_p=0.0, loop_l=0.0, r6=R6_BE):
    """R6 per-stretch content check.  Walks the blend bar by bar and merges runs of the same
    (outgoing gives, incoming gives) state, so adjacent flat bars form one flat run.  Returns
    (stretches, flat_bars); a stretch is {start, end, out, in, flat, music_bars} where music_bars
    counts bars in it where either side has measured music (reported, never rescues a flat bar)."""
    stretches, flat = [], 0.0
    x = a
    while x < e - 1e-9:
        nxt = min(x + 1.0, e)
        j = _in_bar(x, a, loop_p, loop_l)
        o = _out_state(out_t, x, s, r6)
        i = _in_state(in_t, j, x, s, r6)
        f = (not o) and (not i)
        mo, mi = _measured(out_t, x), _measured(in_t, max(j, 0.0))
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
    """v1: per-bar ban.  v2: convex per continuous run, free up to one phrase."""
    if r6 == R6_V1:
        return W_FLAT * sum(n for _, n in runs)
    return W_FLAT_RUN * sum(max(0.0, n - FREE_FLAT_BARS) ** 2 for _, n in runs)


# ---- candidate generation and scoring ---------------------------------------------------
def _score(out_t, in_t, a, s, e, loop_p, loop_l, out_pts, in_pts, r6=R6_BE):
    n_out = float(out_t["n_bars"])
    why, cost, terms = [], 0.0, {}
    fill_ends = {float(f[1]) for f in out_t.get("fills", [])}
    sec_starts = {x[0] for x in _sections(out_t)}

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

    # R2: switch on a phrase point of both tracks where possible
    k = s - a - loop_l
    out_hit = any(abs(s - p) < 1e-9 for p in out_pts)
    in_hit = any(abs(k - p) < 1e-9 for p in in_pts)
    if out_hit and in_hit:
        why.append("R2: switch is a phrase point of both tracks (out bar %g, incoming bar %g)" % (s, k))
    elif out_hit or in_hit:
        cost += W_SWITCH_ONE
        terms["switch_phrase"] = W_SWITCH_ONE
        why.append("R2: switch is a phrase point of the %s only (out bar %g, incoming bar %g)"
                   % ("outgoing" if out_hit else "incoming", s, k))
    else:
        cost += W_SWITCH_NONE
        terms["switch_phrase"] = W_SWITCH_NONE
        why.append("R2: switch is on no phrase point of either track (out bar %g, incoming bar %g)" % (s, k))

    # R1/R2 clock terms, soft
    bars_min = float(out_t["bpm"]) / 4.0   # bars in 60 s
    terms["length"] = W_LEN * abs((e - a) - bars_min)
    terms["switch_time"] = W_SWITCH_TIME * abs((e - s) - bars_min / 2.0)
    cost += terms["length"] + terms["switch_time"]
    why.append("blend %g bars (%.0f s), switch %g bars (%.0f s) before the end"
               % (e - a, (e - a) * 240.0 / float(out_t["bpm"]), e - s, (e - s) * 240.0 / float(out_t["bpm"])))

    # R3/R4/R5: end, loops, cuts
    looped = max(0.0, e - n_out)
    cut = max(0.0, n_out - e - END_SLACK_BARS)
    out_end = e
    if looped > 0:
        cost += W_LOOP * looped
        terms["out_loop"] = W_LOOP * looped
        why.append("R3/R4: outgoing tail looped %g bars to reach the incoming phrase point at incoming bar %g"
                   % (looped, e - a - loop_l))
    elif cut > 0:
        cost += W_CUT * cut
        terms["cut"] = W_CUT * cut
        why.append("R5: outgoing outro CUT by %g bars" % cut)
    else:
        out_end = n_out
        why.append("R3: outgoing ends naturally on the incoming phrase point at incoming bar %g"
                   % (e - a - loop_l))
    if loop_l > 0:
        cost += W_LOOP * loop_l
        terms["in_loop"] = W_LOOP * loop_l
        why.append("R3/R4: incoming intro looped %g bars (phrase ending at incoming bar %g)" % (loop_l, loop_p))

    # R6: flat check
    stretches, flat = content_check(out_t, in_t, a, s, out_end, loop_p, loop_l, r6)
    runs = flat_runs(stretches)
    flat_c = flat_cost(runs, r6)
    cost += flat_c
    terms["flat"] = flat_c
    why.append("R6: %g flat bars in runs %s" % (flat, ["%g+%g" % r for r in runs]) if flat
               else "R6: no flat stretch (one side carries throughout)")

    return BlendPlan(in_starts_at_out_bar=a, bass_switch_out_bar=s, out_end_bar=out_end,
                     out_looped_bars=looped, in_intro_looped_bars=loop_l, out_cut_bars=cut,
                     stretches=stretches, flat_bars=flat, cost=cost, why=why,
                     flat_runs=runs, terms=terms)


def candidates(out_t, in_t, limit=None, r6=R6_BE):
    """Ranked list of BlendPlan, best first.  Switch points come from ORIGINAL phrase points
    only (R4); loops only lengthen the blend.  Entries and switches outside the mix zones
    are not considered (R9)."""
    n_out = float(out_t["n_bars"])
    out_pts = phrase_points(out_t)
    in_pts = phrase_points(in_t)
    zone_lo = n_out - MIX_ZONE_BARS
    out_zone = [p for p in out_pts if zone_lo - 1e-9 <= p < n_out - 1e-9]
    in_zone = [p for p in in_pts if 0 < p <= MIX_ZONE_BARS]
    tgts = [t for t in target_points(in_t) if t <= MIX_ZONE_BARS + 16]
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
        if a < zone_lo - 1e-9 or a >= n_out or s - a < MIN_SIDE_BARS:
            continue
        if s >= n_out - 1e-9:  # R4: a switch in the looped tail would be a loop-derived point
            continue
        k = s - a - l
        if k < p - 1e-9 or k > MIX_ZONE_BARS:
            continue
        for t in tgts:
            e = a + l + t
            if e - s < MIN_SIDE_BARS or e > n_out + MAX_TAIL_LOOP or e < n_out - MAX_TAIL_LOOP:
                continue
            sig = (round(a, 6), round(s, 6), round(e, 6), p, l)
            if sig in seen:
                continue
            seen.add(sig)
            plans.append(_score(out_t, in_t, a, s, e, p, l, out_pts, in_pts, r6))
    plans.sort(key=lambda b: (round(b.cost, 9), b.in_intro_looped_bars, b.out_looped_bars,
                              b.in_starts_at_out_bar, b.bass_switch_out_bar, b.out_end_bar))
    return plans[:limit] if limit else plans


def plan_blend(out_track, in_track, r6=R6_BE):
    """The single best blend, or None if no plan fits the mix zones."""
    c = candidates(out_track, in_track, limit=1, r6=r6)
    return c[0] if c else None
