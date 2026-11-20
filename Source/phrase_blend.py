"""Phrase blend planner: place a DJ blend between two tracks from their phrase points.

Standalone and pure. Not wired into propose_arrangement.py or align_engine.py.

WHAT THE OWNER'S SEVEN ANSWER-KEY BLENDS DO AND DO NOT PROVE (state of round 5, measured; run
Tools/score_blends.py, which prints each of these live):
  - R6 (bottom end) and the measured content are INERT on these blends: with W_FLAT_RUN = 0 the
    unaided score is the same as with it on.  The score comes from geometry (phrase points, the
    one-minute entry, the end target, the swap anchor), not from what is playing.
  - In the first six owner blends the bass switch is exactly entry + 16 bars, so they carry almost
    no information about the switch rule.  The seventh (Call 911 into Back In The Dance, a tricky
    pair the owner placed from a picture) swaps on the incoming's bass-in, entry + 24, and is the
    only one that separates the swap rules.  Tools/blend_candidates.py finds pairs that can too.
  - Round 5 implements three things he said about that seventh blend: the SWAP is the anchor (the
    incoming's bass-in about 30 s before the original end; entry and exit derived), a short TAIL TRIM
    of a non-outro tail is allowed, and there can be MORE THAN ONE right answer (alternatives are
    returned).  Round 5 fixed blend 7 and broke blend 1.
  - Round 6 (owner on blend 1: a swap "just randomly slapped anywhere on the A track" is "unacceptable")
    enumerates PAIRINGS: the swap lines a phrase point of the incoming up with a phrase point of the
    outgoing, and a SANE both-sided pairing always outranks a one-sided swap (a tier, not a weight).
    That fixes blend 1 again and, in BOTTOM END, misses the owner's own one-sided answers for blends 3,
    6 and 7 because a sane pairing exists for each; his stated rule and his accepted placements
    disagree there (Tools/score_blends.py prints the numbers and the term-by-term costs).
  - The weights are hand-set judgements.  A constant set from a single case says which
    (END_SLACK_BARS from blend 2).  Nothing here is "validated": the cases were run many times while
    the rules were written, and the score is reported as measured.

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

# Round 5, from the owner's seventh blend (Call 911 into Back In The Dance, "the rules that we have
# created are not built for that"):
# 1. The SWAP is the anchor.  A swap where the incoming's bass comes in is "probably the most
#    energetic", so a swap that lands on kick-only incoming bars (bass still to come) costs more.
W_SWAP_KICK_ONLY = 2.0   # stated preference: as much as a clock error of 8 bars at 0.25 per bar
BASS_IN_WINDOW = 8       # the incoming's bass-in: first bar where bass is on for 6 of the next 8 bars
BASS_IN_FRACTION = 0.75  # (a fill bar inside the run does not break it: "straight after its own fill")
ENTRY_MAX_SECONDS = 120.0  # a swap-anchored entry may sit well over a minute from the end (his words:
#                          "which is fine") but never more than two minutes
# 2. TAIL TRIM.  Stopping the outgoing at the incoming's next section may drop a few of its last bars
#    when they are not a real outro ("it's just the outro part of it anyway"), unlike cutting a usable
#    outro (picture 4), which stays forbidden.  Defined conservatively: at most MAX_TRIM_BARS bars, no
#    outgoing phrase point strictly inside the dropped span, and the cut does not land inside a section
#    labelled OUTRO.  A trim costs W_TRIM per bar so a natural end of comparable quality still wins.
MAX_TRIM_BARS = 8.0
W_TRIM = 0.25            # 8 bars cost 2.0: about the price of one 8-bar clock error, no more
# 3. More than one right answer: alternatives are distinct swap points whose plan costs within this
#    margin of the best (about a ten bar clock error at 0.5 per bar), with no forbidden cut or long flat run.
# 1b. His words: "do NOT swap late on its bass-in with only 15 seconds left".  A bass-in swap with less
#     than LATE_SWAP_SECONDS before the original end costs W_LATE_SWAP (a stated rule; the weight is set
#     equal to the other stated preference, W_SWAP_KICK_ONLY, not tuned).
LATE_SWAP_SECONDS = 20.0
W_LATE_SWAP = 2.0
ALT_MARGIN = 5.0
# Round 6 (owner on blend 1, rejecting a swap "just randomly slapped anywhere on the A track"):
# the swap is made by LINING UP a phrase point of the incoming (bass-in, kick-in, fill end, section
# start) with a phrase point of the OUTGOING (a fill end or a section start such as the outro start).
# These PAIRINGS are enumerated; entry = outgoing point - incoming point, then the exit rule.  A
# both-sided pairing that keeps the blend sane ALWAYS outranks any other swap (a TIER, not a weight).
# Sane = bottom end kept, no forbidden cut, the swap within PAIR_WINDOW_SECONDS of the 30 s mark, and
# a blend no longer than SANE_BLEND_SECONDS.  A swap on a phrase point of only one track is the
# fallback, used when no sane pairing exists (and `why` says it is one-sided).
PAIR_WINDOW_SECONDS = 20.0  # both swaps he named as fine, A-F7 end (17 s off the 30 s mark) and A-F4 end
#                             in blend 7 (16.5 s off), lie inside it; a swap 30+ s from the mark is not "about 30 s"
SANE_BLEND_SECONDS = 80.0   # a minute plus one phrase: the mix zone; his own blends run 62 to 77 s
# Incoming point quality, in his order: bass-in is "probably the most energetic", then the kick-in,
# then a fill end, then a plain section start; a point that is no phrase point at all is worst.
W_Q_KICKIN = 0.5
W_Q_FILL = 1.0
W_Q_SECTION = 1.5
W_Q_NONE = 2.0
ANCHOR_TOL_BARS = 1.0    # "about 30 seconds": the swap is within one bar of the 30 s mark of the original end
MAX_ALTERNATIVES = 4

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
    out_trim_bars: float = 0.0                      # tail bars dropped under the trim rule (allowed)
    swap_at_bass_in: bool = False                   # the swap lands on the incoming's bass-in bar
    swap_incoming_bar: float = 0.0
    swap_anchored: bool = False                     # round 5: entry and exit derived from the swap
    paired: bool = False                            # round 6: swap is a phrase point of BOTH tracks
    sane: bool = False                              # round 6: a paired swap that keeps the blend sane
    in_point_kind: str = ""                         # what the swap lines up on, on the incoming
    out_point_kind: str = ""                        # ... and on the outgoing ("" when none)
    alternatives: list = field(default_factory=list)  # [Alternative]: other defensible swaps

    def key(self):
        return (self.in_starts_at_out_bar, self.bass_switch_out_bar, self.out_end_bar)

    def full_key(self):
        """key() plus the incoming loop: two plans with the same key can play different bars."""
        return self.key() + (self.in_intro_looped_bars, self.in_loop_phrase_end)


@dataclass
class Alternative:
    """Another defensible placement: a distinct swap point, why it is defensible, and what it is
    likely to feel like.  The planner never pretends there is one answer (owner, round 5)."""
    plan: BlendPlan
    swap_out_bar: float
    reason: str
    feel: str


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


def bass_in(track):
    """The incoming's bass-in bar (round 5): the first bar after bar 0 where measured bass is on and
    stays on, i.e. on for at least BASS_IN_FRACTION of the next BASS_IN_WINDOW bars, normally
    straight after its own fill.  None when the track has no usable measured bass, or has bass from
    bar one (then its first section start or fill end is used, as before)."""
    hb = (track.get("content") or {}).get("has_bass")
    if not hb:
        return None
    w = int(BASS_IN_WINDOW)
    for b in range(len(hb)):
        if hb[b] and sum(1 for v in hb[b:b + w] if v) >= BASS_IN_FRACTION * w:
            return float(b) if b > 0 else None
    return None


def kick_in(track):
    """The incoming's kick-in: the start of its first DROP or BUILD section after bar 0 (the owner's
    "B1").  Label-based on purpose: measured has_kick cannot tell a soft intro kick from the drop's
    full kick.  None when it has no such section."""
    starts = [x[0] for x in _sections(track) if x[1] in ("DROP", "BUILD") and x[0] > 0]
    return min(starts) if starts else None


def in_point_kind(track, k):
    """(name, quality cost) of the incoming point k a swap lines up on, in the owner's order."""
    bi = bass_in(track)
    if bi is not None and abs(k - bi) < 1e-9:
        return "bass-in", 0.0
    ki = kick_in(track)
    if ki is not None and abs(k - ki) < 1e-9:
        return "kick-in", W_Q_KICKIN
    if any(abs(k - float(f[1])) < 1e-9 for f in track.get("fills", [])):
        return "fill end", W_Q_FILL
    if any(abs(k - x[0]) < 1e-9 for x in _sections(track)):
        return "section start", W_Q_SECTION
    return "", W_Q_NONE


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
def _is_trim(out_t, e, out_pts):
    """Tail trim test (round 5): dropping the outgoing's last bars is allowed only when it is plainly
    not a usable outro: at most MAX_TRIM_BARS bars, no outgoing phrase point strictly inside the
    dropped span, and the stop does not land inside a section labelled OUTRO."""
    n_out = float(out_t["n_bars"])
    cut = n_out - e
    if cut > MAX_TRIM_BARS + 1e-9:
        return False
    if any(e + 1e-9 < p < n_out - 1e-9 for p in out_pts):
        return False
    return _section_at(out_t, min(e, n_out - 1e-6))[1] != "OUTRO"


def _score(out_t, in_t, a, s, e, loop_p, loop_l, out_pts, in_pts, r6=R6_BE):
    n_out = float(out_t["n_bars"])
    why, cost, terms = [], 0.0, {}
    fill_ends = {float(f[1]) for f in out_t.get("fills", [])}
    sec_starts = {x[0] for x in _sections(out_t)}
    bars_min = MINUTE_SECONDS / _bar_seconds(out_t)       # bars in a minute, outgoing tempo

    # round 5: is this a SWAP-ANCHORED plan?  The swap is on the incoming's bass-in and about 30 s
    # before the ORIGINAL outgoing end.  Then the entry is derived from the swap, not chosen, so the
    # one-minute-mark awareness does not apply to it (the entry-type cost still does: R1 "normally").
    k = s - a - loop_l
    bi = bass_in(in_t)
    forbidden_cut = (e < n_out - END_SLACK_BARS - 1e-9) and not _is_trim(out_t, e, out_pts)
    e_swap = e if forbidden_cut else n_out
    anchored = (bi is not None and abs(k - bi) < 1e-9
                and abs((e_swap - s) - bars_min / 2.0) <= ANCHOR_TOL_BARS + 1e-9)

    out_hit = any(abs(s - p) < 1e-9 for p in out_pts)
    in_hit = any(abs(k - p) < 1e-9 for p in in_pts)
    paired = out_hit and in_hit
    # R1: entry on an outgoing phrase point, normally a fill end.  For a PAIRING the entry is derived
    # (outgoing point minus incoming point), not chosen, so the entry-type cost does not apply to it.
    if paired:
        why.append("round 6: pairing: the entry (out bar %g) follows from lining the swap up on both tracks" % a)
    if anchored:
        why.append("round 5: swap-anchored: the swap is the incoming's bass-in (incoming bar %g) about 30 s "
                   "before the original end, so the entry (out bar %g) is derived from it" % (k, a))
    if a in fill_ends:
        why.append("R1: entry at out bar %g is the end of an outgoing fill" % a)
    elif a in sec_starts:
        if not paired:
            cost += W_ENTRY_SECTION
            terms["entry"] = W_ENTRY_SECTION
        why.append("R1: entry at out bar %g is an outgoing section start (not a fill end)" % a)
    else:
        if not paired:
            cost += W_ENTRY_OFF
            terms["entry"] = W_ENTRY_OFF
        why.append("R1: entry at out bar %g is NOT an outgoing phrase point (placed back from the switch)" % a)

    # owner awareness (soft): a phrase point about a minute before the outgoing END
    mark = n_out - bars_min
    is_phrase = any(abs(a - p) < 1e-9 for p in out_pts)
    minute = is_phrase and abs(a - mark) <= MINUTE_TOL_BARS
    if anchored or paired:
        pass
    elif minute:
        why.append("awareness: entry at out bar %g is a one-minute mark (a phrase point about a minute "
                   "before the outgoing end, %g)" % (a, mark))
    else:
        cost += W_MINUTE_OFF
        terms["minute_mark"] = W_MINUTE_OFF

    # R2: switch on a phrase point of at least one track
    kind, qcost = in_point_kind(in_t, k)
    if qcost:
        cost += qcost
        terms["in_point"] = qcost
    if out_hit or in_hit:
        who = "both tracks" if paired else ("the outgoing only" if out_hit else "the incoming only")
        why.append("R2: switch is a phrase point of %s (out bar %g, incoming bar %g%s)" % (
            who, s, k, ", the incoming's %s" % kind if kind else ""))
        if not paired:
            mark_s = n_out - 30.0 / _bar_seconds(out_t)
            near = [p for p in out_pts if p < n_out - 1e-9
                    and abs(p - mark_s) * _bar_seconds(out_t) <= PAIR_WINDOW_SECONDS]
            why.append("ONE-SIDED swap: it lines up with a phrase point of only one track; "
                       + ("the outgoing offers phrase points near the 30 s mark (out bars %s) that a pairing could use"
                          % ", ".join("%g" % p for p in near) if near
                          else "the outgoing offers no phrase point within %g s of the 30 s mark, so this is the fallback"
                          % PAIR_WINDOW_SECONDS))
    else:
        cost += W_SWITCH_NONE
        terms["switch_phrase"] = W_SWITCH_NONE
        why.append("R2: switch is on no phrase point of either track (out bar %g, incoming bar %g)" % (s, k))

    # R3/R4/R5: end, loops, cuts.  Loops are never part of the track: clocks use e0.
    looped = max(0.0, e - n_out)
    out_end = e
    cut = 0.0
    trim = 0.0
    if looped > 0:
        cost += W_LOOP * looped
        terms["out_loop"] = W_LOOP * looped
        why.append("R3/R4: outgoing tail looped %g bars to reach the incoming phrase point at incoming bar %g"
                   % (looped, e - a - loop_l))
    elif n_out - e > END_SLACK_BARS + 1e-9 and _is_trim(out_t, e, out_pts):
        trim = n_out - e
        cost += W_TRIM * trim
        terms["trim"] = W_TRIM * trim
        why.append("tail trim: the outgoing stops at out bar %g, dropping its last %g bars, which hold "
                   "no phrase point and are not an outro section (not a usable outro)" % (e, trim))
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
    e_sw = e0 if cut > 0 else n_out     # swap clock: to the ORIGINAL end unless a forbidden cut moved it
    if loop_l > 0:
        cost += W_LOOP * loop_l
        terms["in_loop"] = W_LOOP * loop_l
        why.append("R3/R4: incoming intro looped %g bars (phrase ending at incoming bar %g)" % (loop_l, loop_p))

    # R1/R2 clocks, soft, to the ORIGINAL end
    terms["length"] = W_LEN * abs((e0 - a) - bars_min)
    terms["switch_time"] = W_SWITCH_TIME * abs((e_sw - s) - bars_min / 2.0)
    cost += terms["length"] + terms["switch_time"]
    bs = _bar_seconds(out_t)
    why.append("blend %g bars (%.0f s), switch %g bars (%.0f s) before the original end"
               % (e0 - a, (e0 - a) * bs, e_sw - s, (e_sw - s) * bs))
    if _valid(in_t):
        in_min = MINUTE_SECONDS / _bar_seconds(in_t)
        end_in = e - a - loop_l
        why.append("incoming one-minute mark is its bar %g; the blend ends at incoming bar %g and the "
                   "switch is at incoming bar %g" % (in_min, end_in, k))

    # round 5: a swap on kick-only incoming bars (bass still to come) is the less energetic choice
    mk = _measured(in_t, max(k, 0.0), R6_BE)
    at_bass_in = bi is not None and abs(k - bi) < 1e-9
    if at_bass_in:
        why.append("swap is on the incoming's bass-in (incoming bar %g): probably the most energetic" % k)
        late = (e_swap - s) * _bar_seconds(out_t)
        if late < LATE_SWAP_SECONDS:
            cost += W_LATE_SWAP
            terms["late_bass_in_swap"] = W_LATE_SWAP
            why.append("but the swap leaves only %.0f s before the original end: the owner says not to swap "
                       "late on the bass-in" % late)
    elif mk is not None and mk[2] and not mk[0]:
        cost += W_SWAP_KICK_ONLY
        terms["swap_kick_only"] = W_SWAP_KICK_ONLY
        why.append("swap lands on kick-only incoming bars (incoming bar %g, bass-in is %s)"
                   % (k, "bar %g" % bi if bi is not None else "not measured"))

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

    mark_s = n_out - 30.0 / _bar_seconds(out_t)
    bs_ = _bar_seconds(out_t)
    sane = (paired and terms.get("flat", 0.0) == 0.0 and cut == 0.0
            and abs(s - mark_s) * bs_ <= PAIR_WINDOW_SECONDS + 1e-9
            and (e0 - a) * bs_ <= SANE_BLEND_SECONDS + 1e-9)
    if paired:
        why.append("pairing: %s" % ("sane, so it outranks every one-sided swap" if sane else
                                    "both-sided but not sane (flat run, forbidden cut, swap far from the 30 s mark, "
                                    "or a blend longer than %g s)" % SANE_BLEND_SECONDS))
    return BlendPlan(in_starts_at_out_bar=a, bass_switch_out_bar=s, out_end_bar=out_end,
                     out_looped_bars=looped, in_intro_looped_bars=loop_l, out_cut_bars=cut,
                     stretches=stretches, flat_bars=flat, cost=cost, why=why,
                     flat_runs=runs, terms=terms, out_loop_source_bars=src,
                     in_loop_phrase_end=loop_p if loop_l > 0 else 0.0, minute_mark=minute,
                     out_trim_bars=trim, swap_at_bass_in=at_bass_in, swap_incoming_bar=k,
                     swap_anchored=anchored, paired=paired, sane=sane, in_point_kind=kind,
                     out_point_kind=("fill end" if any(abs(s - float(f[1])) < 1e-9 for f in out_t.get("fills", []))
                                     else "section start" if s in sec_starts else ""))


def _rank_key(b):
    """Sane both-sided pairings first (a tier, round 6), then cheapest first; on an exact tie prefer
    no incoming loop, then less outgoing loop, then the earlier entry, switch and end."""
    return (0 if b.sane else 1, round(b.cost, 9), b.in_intro_looped_bars, b.out_looped_bars,
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
    derived = set()  # swap-anchored trips: entry is derived from the swap, so the entry zone is waived
    bi = bass_in(in_t)
    if bi is not None and bi <= zone_in + 1e-9:
        mark = n_out - 30.0 / _bar_seconds(out_t)
        anchors = set(out_zone) | {float(round(mark)) + d for d in (-2.0, -1.0, 0.0, 1.0, 2.0)}
        for s in anchors:
            a = s - bi
            if a >= 0 and a >= n_out - ENTRY_MAX_SECONDS / _bar_seconds(out_t) and zone_lo - 1e-9 <= s < n_out - 1e-9:
                trips.add((a, s, 0.0, 0.0))
                derived.add((a, s, 0.0, 0.0))
                # the same derived entry with the swap on an outgoing phrase point instead: another
                # defensible answer (owner: "you could do the bass switch after A-F4, or at B1")
                for s2 in out_zone:
                    if s2 - a >= MIN_SIDE_BARS:
                        trips.add((a, s2, 0.0, 0.0))
                        derived.add((a, s2, 0.0, 0.0))
    for p, l in loops:
        for s in out_zone:                      # switch on an outgoing phrase point
            for k in in_zone:                   # ... and an incoming one: a PAIRING, entry derived
                a_pair = s - k - l
                trips.add((a_pair, s, p, l))
                if a_pair >= n_out - ENTRY_MAX_SECONDS / _bar_seconds(out_t):
                    derived.add((a_pair, s, p, l))
            for a in out_zone:                  # ... entry on an outgoing phrase point
                if a < s:
                    trips.add((a, s, p, l))
        for a in out_zone:                      # entry on a phrase point, switch on an incoming one
            for k in in_zone:
                trips.add((a, a + l + k, p, l))

    plans, seen = [], set()
    for a, s, p, l in trips:
        if a < 0 or a >= n_out or s - a < MIN_SIDE_BARS:
            continue
        if a < zone_lo - 1e-9 and (a, s, p, l) not in derived:
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


def _describe(plan, best, in_t):
    """One-line reason and the likely feel of an alternative, relative to the best plan."""
    if plan.swap_at_bass_in:
        reason = "swap on the incoming's bass-in (incoming bar %g)" % plan.swap_incoming_bar
        feel = "more energy: the new bass arrives on the swap"
    else:
        reason = "swap on a phrase point (out bar %g, incoming bar %g) before the incoming's bass-in" % (
            plan.bass_switch_out_bar, plan.swap_incoming_bar)
        feel = "less energy: the incoming is still kick only at the swap"
    d_swap = best.bass_switch_out_bar - plan.bass_switch_out_bar
    if d_swap > 0:
        feel += "; the swap comes %g bars earlier, so more time with the new track's bass" % d_swap
    elif d_swap < 0:
        feel += "; the swap comes %g bars later, so a shorter finish" % -d_swap
    d_len = (plan.out_end_bar - plan.in_starts_at_out_bar) - (best.out_end_bar - best.in_starts_at_out_bar)
    if abs(d_len) >= 1:
        feel += "; %s blend by %g bars" % ("longer" if d_len > 0 else "shorter", abs(d_len))
    if plan.out_trim_bars:
        feel += "; trims the outgoing's last %g bars" % plan.out_trim_bars
    return reason, feel


def alternatives_for(cs, in_t):
    """Other defensible placements: distinct (swap, entry) pairs, each within ALT_MARGIN cost of the
    best, with no forbidden cut and no costly flat run.  A candidate within one bar of an already
    listed swap AND entry is a near-duplicate and is skipped.  At most MAX_ALTERNATIVES, cheapest
    first."""
    if not cs:
        return []
    best, out = cs[0], []
    seen = [(cs[0].bass_switch_out_bar, cs[0].in_starts_at_out_bar)]
    for c in cs[1:]:
        if c.cost > best.cost + ALT_MARGIN:
            break
        if c.out_cut_bars > 0 or c.terms.get("flat", 0.0) > 0:
            continue
        if any(abs(c.bass_switch_out_bar - s_) <= 1.0 and abs(c.in_starts_at_out_bar - a_) <= 1.0
               for s_, a_ in seen):
            continue
        seen.append((c.bass_switch_out_bar, c.in_starts_at_out_bar))
        reason, feel = _describe(c, best, in_t)
        out.append(Alternative(c, c.bass_switch_out_bar, reason, feel))
        if len(out) >= MAX_ALTERNATIVES:
            break
    return out


def plan_blend(out_track, in_track, r6=R6_BE):
    """The best blend, with `.alternatives` (other defensible swap points), or None if no plan fits
    the mix zones (or a track is unusable).  There is often more than one right answer."""
    cs = candidates(out_track, in_track, r6=r6)
    if not cs:
        return None
    cs[0].alternatives = alternatives_for(cs, in_track)
    return cs[0]
