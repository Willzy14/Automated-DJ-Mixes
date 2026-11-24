"""Phrase blend planner: place a DJ blend between two tracks from their phrase points.

Standalone and pure. Not wired into propose_arrangement.py or align_engine.py.

WHAT THE OWNER'S SEVEN ANSWER-KEY BLENDS SHOW (Tools/score_blends.py recomputes the results):
  - W_FLAT_RUN is INERT on the seven. Measured content affects other decisions, including
    bass-in detection, swap grading, and the legacy last-kick cue allowance.
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
  - Round 7 adds IMPLIED phrase points (a regular 8 bar grid from each section start, "it's always a
    musical phrase").  Every owner swap, in all seven blends, then sits on a point of both tracks (a
    marked point on one side, marked or implied on the other), so the pairing rule no longer disagrees
    with his accepted answers; blend 1 now misses because a cheaper 17 bar pairing beats his looped one.
  - The weights are hand-set judgements.  Nothing here is "validated": the cases were run many
    times while the rules were written, and the score is reported as measured.
  - Round 8 (two CORRECTION reviews, codex_blend_r2 and kimi_blend_r2, both reproduced live):
    (1) leave long incoming fills alone: the outgoing is out by the start of a long fill at
    or after the swap. A swap within a long fill is inadmissible. A two-bar fill can be ridden
    to the section at its end, as in blend 5;
    (2) a loop is valued, not taxed: looping the outgoing up to the exit the pairing calls for
    (the incoming's next section start or fill start, capped by the long-fill bound) is FREE when
    the loop source keeps its kick. Extra bars or a poor source cost. The 24-bar cap rejects
    longer extensions;
    (3) the entry side too: a pairing whose derived entry misses every outgoing cue point gets
    intro-extended (loop intro bars back to the previous cue) or intro-cut (start part-way in, to
    the next cue) variants, both free, and an unadjusted pairing entry off every cue point pays;
    a cut never goes through the incoming fill that leads into the swap;
    (4) the cliffs PAIR_WINDOW_SECONDS and SANE_BLEND_SECONDS are gone: the swap clock and the
    length clock are graded costs, and an early entry is allowed (costed, not banned) when the
    incoming plays a long intro before its bass-in. The sane tier prefers a two-sided pairing
    over a swap on no outgoing cue point;
    (5) end_bar gives the exact audio endpoint when supplied. Old inputs use their counted
    end and retain the legacy kick-based cue allowance;
    (7) a soft minimum on the heard overlap so a ~26 s blend with ~10 s left cannot win;
    (8) no implied grid point before the first real section when it is not at bar 0, and a
    measured bass-in within one bar of a marked section start IS that section start;
    (9) robustness: a one-element fill and a non-numeric section start do not crash.

Inputs are dicts shaped like Documentation/Golden Sections/Answer Key 2026-11-20 DFTD.json
-> tracks[name]:  bpm, n_bars, sections = [[start_bar, LABEL] or [start_bar, LABEL, carries]],
fills = [[start_bar, end_bar]]. Bars are 0-based from the first beat. Optional end_bar is the
exact elapsed audio in bars from that beat (seconds from first beat to audio end * bpm / 240).
A natural end within one beat of an exit cue can use the audio endpoint and reports the
signed offset. Without end_bar, n_bars is the endpoint and the legacy kick-based cue
allowance is retained and disclosed in why. The optional third
element of a section (a bool, or {"carries": bool}) overrides the content test used by the
flat-blend check (rule R6).  A track may also carry "content": {"has_bass": [...], "has_music":
[...], "has_kick": [...]} (0/1 per bar, from Tools/measure_section_content.py); R6 then uses
MEASURED content.  A track without usable content (none, empty lists, lists of different
lengths, or no has_kick under the bottom-end rule) falls back to labels, and `why` says so.
Two more OPTIONAL inputs feed soft clash costs (2026-11-24): content["has_vocal"] (per bar, the same bar
clock and length as the others) and track["key"] (a Camelot code such as "8A").  Without them nothing changes.

Blend geometry (all numbers are the OUTGOING track's own bar numbers):
  in_starts_at_out_bar   where the incoming track's bar 0 plays
  bass_switch_out_bar    where the incoming bass takes over and the outgoing bass leaves
  out_end_bar            where the outgoing stops, including any loop or trim from its natural end
TEMPO ASSUMPTION: one bar of either track is one blend bar, because the pipeline warps both
tracks to one tempo.  Clocks (a minute, 30 seconds, the mix zones) use each track's own bpm and
4/4 bars (240 / bpm seconds).

TWO CLOCKS (round 8):
  - "original phrasing": loops and intro changes never count; this clock measures where the swap
    sits relative to the track's ORIGINAL phrases and original end.  Terms on this clock:
    switch_time (swap vs the original end), late_bass_in_swap (swap on the bass-in vs the
    original end), the anchored test, and the forbidden-cut / trim tests (what is left of the
    original track).  The in_point / out_point quality terms are also original-phrasing terms
    (the swap must line up on original phrase points of both tracks).
  - "actual overlap": what is heard.  Terms on this clock: length (entry to exit as heard,
    loops included), short_blend (a soft minimum on the heard overlap), flat (the bottom-end
    check walks heard bars, mapping looped outgoing bars to their source), and the loop terms
    out_loop / in_loop (audible repetition, priced only beyond what the blend calls for).
  A loop therefore never moves the swap clocks, but it does lengthen the heard blend.

The rules (the owner's, a working DJ) are cited as R1..R9 beside the code that uses them.
"""
from dataclasses import dataclass, field
from math import ceil, isfinite

from automated_dj_mixes.sequencer import _camelot_distance, _parse_camelot, compatibility_score

# ---- hand-set weights (cost; lower is better) -------------------------------------------
W_FLAT = 10.0          # R6 v1 (round 2 rule, kept for comparison): flat is a per-bar ban
W_CUT = 12.0           # R5: diagnostic cost for a forbidden outro cut; candidates reject it
W_LOOP = 1.5           # R4, round 8: looping up to the exit the pairing calls for is FREE (fix 2);
#                        this rate applies to extra loop bars or a poor source. MAX_TAIL_LOOP rejects.
W_ENTRY_SECTION = 1.0  # R1: entry "normally" on a fill END; a plain section start is second best
W_ENTRY_OFF = 6.0      # R1 is "normally", not "always"; an unphrased entry is allowed but costs
W_SWITCH_NONE = 12.0   # R2: a switch on neither track's cue costs. The owner requires both cue
#                        points when available. The sane pairing tier prefers those swaps.
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
MIN_SIDE_BARS = 4.0     # the switch must sit at least 4 bars after entry and before the end
MAX_TAIL_LOOP = 24.0    # reject any candidate that loops the outgoing tail by more than 24 bars
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
# Sane = paired, bottom end kept and no forbidden cut.  Round 8, fix 4: the old cliffs
# PAIR_WINDOW_SECONDS and SANE_BLEND_SECONDS are gone - the swap's distance from the 30 s mark and
# the blend's length are graded costs (W_SWITCH_TIME and W_LEN below), not tier gates, and a swap
# 45+ s from the mark (his accepted alternative in blend 7) stays a sane pairing.
# Incoming point quality, in his order: bass-in is "probably the most energetic", then the kick-in,
# then a fill end, then a plain section start; a point that is no phrase point at all is worst.
W_Q_KICKIN = 0.5
W_Q_FILL = 1.0
W_Q_SECTION = 1.5
W_Q_NONE = 2.0
# Round 7 (owner: "it's always a musical phrase"): the phrase runs on a regular grid whether or not
# anything marks it.  IMPLIED phrase points: from each section start, every 8 bars until the next
# section start (multiples of 16 from that start are stronger).  Marked points (section lines, fill
# ends) keep the highest quality, implied 16s next, implied 8s next, anything else is no phrase point.
# A marked point lined up with an implied point on the other track is a real pairing, ranked a little
# below marked-with-marked; implied-with-implied is not (it is anchored to nothing the owner marked).
W_Q_IMPL16 = 1.75        # incoming implied 16: just below a marked section start (1.5)
W_Q_IMPL8 = 1.9          # incoming implied 8: just below an implied 16, still better than none (2.0)
W_OUT_IMPL16 = 0.5       # outgoing side: an implied 16 costs a little more than a marked point (0)
W_OUT_IMPL8 = 1.0        # ... and an implied 8 a little more than that
BEAT_BARS = 0.25         # a line within one beat of a bar line is treated as that bar (Passion 135.75)
ANCHOR_TOL_BARS = 1.0    # "about 30 seconds": the swap is within one bar of the 30 s mark of the original end
MAX_ALTERNATIVES = 6
# The two-bar boundary rests on the owner's confirmed blend 5: Ashibah into Eli Brown rides
# exactly two incoming fill bars and stops at the section at the fill end. Longer fills play alone.
LONG_FILL_BARS = 2.0
# Round 8, fix 4: an entry before the mix zone is not banned; it costs W_ENTRY_EARLY per bar, and is
# allowed at all only when the incoming plays a long intro before the swap (LONG_INTRO_SECONDS of
# audible intro) - "an early entry is allowed for a long incoming intro before its bass-in".
W_ENTRY_EARLY = 0.25
LONG_INTRO_SECONDS = 20.0
# Round 8, fix 7: a soft minimum on the heard overlap (the actual-overlap clock), so a ~26 s blend
# with ~10 s left cannot win on a technicality.  Graded cost per second below the floor, not a ban.
MIN_BLEND_SECONDS = 30.0
W_SHORT_BLEND = 0.25

# Vocal and key clash (2026-11-24, Sam: "make vocal clash and key actually influence the blend").  Two SOFT
# terms, never a ban: a clash that cannot be avoided is still plannable and is reported on the plan.
# Both need data the planner treats as OPTIONAL: content["has_vocal"] (per bar, same clock as has_kick) and
# track["key"] (a Camelot code).  A track without them gives the plan it always gave.
# Weights come from Sam's own blends (Tools/measure_vocal_key.py; the table is in the 2026-11-24 build note):
#  - vocals: on his 7 DFTD answer-key blends and the 7 scoreable Tech House blends he left or made, the
#    overlap bars with a vocal on both tracks are 0 in every one (median 0, maximum 0).  His data caps nothing
#    from above, so the rate is the planner's own per-bar clock rate, 0.25 (W_SWITCH_TIME, W_TRIM): a phrase of
#    8 clashing vocal bars costs 2.0, the price of moving the swap by a phrase.
#  - key: what mixes is the sequencer's own rule (automated_dj_mixes.sequencer.compatibility_score, the one
#    that orders the playlist): same key, a neighbour, the relative key, a diagonal and a power mix all cost 0.
#    A clash costs per wheel step past the nearest pair the sequencer accepts, capped at KEY_STEP_CAP = 4.  On
#    the DFTD blends his worst case is blend 7 (Call 911 into Back In The Dance, 8A to 2A): 6 numbers apart on
#    one letter, accepted out to 2, so 4 beyond, over 3 tonal bars: 4 x 3 = 12 step-bars.  A first weight of
#    0.125 (12 step-bars = 1.5, under the 2.0 a phrase move costs) overturned his own blend 7: his placement
#    wins by 0.94 over the best clash-free plan, so those 12 step-bars must cost under 0.94.  His clash is
#    deliberate, so the weight is the one his own placement survives: 0.0625 (12 step-bars = 0.75).  It is
#    per tonal bar, so a long overlap of clashing music, which he never leaves in, still costs up to
#    MAX_CLASH_COST.  The Tech House set has no keys (8 of 9 tracks are not in the Mixed In Key database), so
#    it adds nothing there.
# MAX_CLASH_COST caps the SUM of the two terms (each alone could reach it): together they stay below W_CUT
# and neither a long overlap nor both clashes at once can veto a blend.  When the cap binds, the two terms
# in BlendPlan.terms are scaled down in proportion so they add up to the capped total; the raw numbers stay
# on the plan (vocal_clash_bars, key_clash).
W_VOCAL_CLASH = 0.25     # cost per overlap bar in which BOTH tracks have a vocal
W_KEY_CLASH = 0.0625     # cost per wheel step past what the sequencer accepts, per overlap bar in which BOTH tracks are tonal
KEY_STEP_CAP = 4         # steps beyond what the sequencer calls compatible stop costing more here
MAX_CLASH_COST = 8.0     # the vocal and key terms together stop here, below W_CUT (12)

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
    in_intro_cut_bars: float = 0.0                # round 8 fix 3: heard incoming starts this many bars in
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
    swap_incoming_bar: float | None = None
    swap_anchored: bool = False                     # round 5: entry and exit derived from the swap
    paired: bool = False                            # round 6: swap is a phrase point of BOTH tracks
    sane: bool = False                              # round 6: a paired swap that keeps the blend sane
    in_point_kind: str = ""                         # what the swap lines up on, on the incoming
    out_point_kind: str = ""                        # ... and on the outgoing ("" when none)
    alternatives: list = field(default_factory=list)  # [Alternative]: other defensible swaps
    vocal_clash_bars: float | None = None           # overlap bars with a vocal on BOTH tracks; None = unknown
    key_clash: float | None = None                  # Camelot steps beyond compatible x tonal bars; None = unknown
    key_steps: int | None = None                    # Camelot wheel steps between the two keys (number steps + a letter flip)
    key_type: str | None = None                     # the sequencer's name for the pair: identical, smooth, relative_key, power_mix, diagonal or clash
    key_tonal_bars: float = 0.0                     # overlap bars in which both tracks have music
    key_pair: tuple = None                          # (outgoing key, incoming key) when both are known

    def __post_init__(self):
        if self.swap_incoming_bar is None:
            self.swap_incoming_bar = (self.bass_switch_out_bar - self.in_starts_at_out_bar
                                      - self.in_intro_looped_bars + self.in_intro_cut_bars)

    def key(self):
        return (self.in_starts_at_out_bar, self.bass_switch_out_bar, self.out_end_bar)

    def full_key(self):
        """key() plus the incoming loop and cut: two plans with the same key can play different bars."""
        return self.key() + (self.in_intro_looped_bars, self.in_loop_phrase_end, self.in_intro_cut_bars)


@dataclass
class Alternative:
    """Another defensible placement: a distinct swap point, why it is defensible, and what it is
    likely to feel like.  The planner never pretends there is one answer (owner, round 5)."""
    plan: BlendPlan
    swap_out_bar: float
    reason: str
    feel: str


# ---- track helpers ----------------------------------------------------------------------
def _valid(track, why=None, name="track"):
    data = track if isinstance(track, dict) else {}
    valid = True
    for field_name in ("bpm", "n_bars"):
        value = _num(data.get(field_name))
        if value is None or value <= 0:
            valid = False
            if why is not None:
                why.append("%s %s must be a positive finite number" % (name, field_name))
    return valid


def _num(x):
    """A finite float, or None for an unusable coordinate or track value."""
    try:
        value = float(x)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if isfinite(value) else None


def _bar_seconds(track):
    return 240.0 / float(track["bpm"])


def _zone_bars(track):
    return MIX_ZONE_SECONDS / _bar_seconds(track)


def _snap(x):
    """A line within one beat of a bar line is that bar; other fractions (a half-bar fill start) stay."""
    r = round(float(x))
    return float(r) if abs(float(x) - r) <= BEAT_BARS + 1e-9 else float(x)


def _cue_rows(track, key, why):
    rows = track.get(key)
    if isinstance(rows, (list, tuple)):
        return rows
    if rows is not None and why is not None:
        why.append("skipped %s: expected a list of cues" % key)
    return ()


def _fills(track, why=None):
    """[(start, end)] snapped, malformed entries skipped.  A one-element fill [183] is read as a
    fill at that bar alone (start = end = 183): it marks the point without inventing a length."""
    out = []
    for i, f in enumerate(_cue_rows(track, "fills", why)):
        if isinstance(f, (list, tuple)) and f:
            a, b = _num(f[0]), _num(f[min(1, len(f) - 1)])
            if a is not None and b is not None and a <= b:
                out.append((_snap(a), _snap(b)))
                continue
        if why is not None:
            why.append("skipped fills[%d]: expected finite, ordered bar coordinates" % i)
    return out


def _fill_ends(track):
    return [b for _, b in _fills(track)]


def _fill_starts(track):
    return [a for a, _ in _fills(track)]


def _sections(track, why=None):
    out = []
    for i, s in enumerate(_cue_rows(track, "sections", why)):
        st = _num(s[0]) if isinstance(s, (list, tuple)) and s else None
        if st is None:
            if why is not None:
                why.append("skipped sections[%d]: expected a finite start bar" % i)
            continue
        flag = None
        if len(s) > 2:
            f = s[2]
            flag = f.get("carries") if isinstance(f, dict) else f
            flag = None if flag is None else bool(flag)
        out.append((_snap(st), str(s[1]).upper() if len(s) > 1 else "", flag))
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
    pts = {0.0, natural_end_bar(track)}
    pts.update(s[0] for s in _sections(track))
    pts.update(_fill_ends(track))
    return sorted(pts)


def implied_points(track):
    """{bar: 16 or 8}: from each section start, every 8 bars until the next section start (or the track
    end); multiples of 16 from that start are the stronger 16.  The grid restarts at every section start,
    so it is measured from the LAST section start before a bar, not from bar 0.  Marked points are not
    repeated here.  Round 8, fix 8: bar 0 anchors the grid only when a section confirms it - there is
    no implied point before the first real section when that section does not start at bar 0 (a
    2 bar pickup used to phantom-shift the whole grid into the lead-in)."""
    n = natural_end_bar(track)
    starts = sorted({x[0] for x in _sections(track)}) or [0.0]
    ends = starts[1:] + [n]
    marked = set(phrase_points(track))
    out = {}
    for st, en in zip(starts, ends):
        m = 1
        while st + 8 * m < en - 1e-9:
            b = st + 8.0 * m
            if b not in marked:
                out[b] = max(out.get(b, 0), 16 if m % 2 == 0 else 8)
            m += 1
    return out


def point_type(track, bar):
    """What a bar is on this track: 'marked fill end', 'marked section start', 'implied 16', 'implied 8'
    or '' (no phrase point).  The track start and end count as marked section starts."""
    track_end = natural_end_bar(track)
    at_end = abs(bar - track_end) < 1e-9
    bar = _snap(bar)
    if any(abs(bar - p) < 1e-9 for p in _fill_ends(track)):
        return "marked fill end"
    if abs(bar) < 1e-9 or at_end or abs(bar - track_end) < 1e-9 or any(
            abs(bar - x[0]) < 1e-9 for x in _sections(track)):
        return "marked section start"
    v = implied_points(track).get(bar)
    return {16: "implied 16", 8: "implied 8"}.get(v, "")


def target_points(track):
    """Where an outgoing track may END against this incoming track (R3): a section start
    or the START of a fill."""
    pts = {s[0] for s in _sections(track)}
    pts.update(_fill_starts(track))
    pts.discard(0.0)
    return sorted(pts)


def natural_end_bar(track):
    """The exact audio endpoint when supplied, otherwise the counted number of bars."""
    end = _audio_end_bar(track)
    return end if end is not None else float(track["n_bars"])


def _audio_end_bar(track):
    end = _num(track.get("end_bar"))
    return end if end is not None and end > 0 else None


def _natural_end_threshold(track, n):
    """Old inputs retain the legacy one-bar cue allowance when the last kick flag is off."""
    if _audio_end_bar(track) is not None:
        return n
    kick = (track.get("content") or {}).get("has_kick")
    if kick and len(kick) >= int(n) > 0 and not kick[int(n) - 1]:
        return n - 1.0
    return n


def _long_fills(track, k):
    """Long fills still pending or already playing at the swap. The fill end starts a new phrase."""
    return [(a, b) for a, b in _fills(track)
            if b - a >= LONG_FILL_BARS - 1e-9 and k < b - 1e-9]


def _clears_long_fills(exit_bar, swap_bar, fills, section_starts):
    return all(exit_bar <= start + 1e-9
               or (swap_bar <= start + 1e-9 and abs(end - start - 2.0) < 1e-9
                   and abs(exit_bar - end) < 1e-9 and end in section_starts)
               for start, end in fills)


def _called_for_exit(in_t, k):
    """Round 8, fix 2: the exit target the pairing calls for - the incoming's first section start
    or fill start after the swap (the owner's "looped up to the START of the next fill"), capped by
    the long-fill bound (fix 1): the outgoing is faded by the START of the next long fill, so a loop
    is never needed past it.  None when no target exists after the swap."""
    tgts = [t for t in target_points(in_t) if t > k + 1e-9]
    if not tgts:
        return None
    called = tgts[0]
    return min([called] + [start for start, _ in _long_fills(in_t, k)])


def _intro_loop_options(track):
    """R3: whole-phrase loops of the incoming intro: consecutive phrase points inside the
    INTRO labelled stretch, at least MIN_IN_LOOP bars long.  Returns [(p, L)]."""
    secs = _sections(track)
    first_non_intro = next((s[0] for s in secs if s[1] != "INTRO"), natural_end_bar(track))
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
    straight after its own fill.  Round 8, fix 8: a measured bass-in within one bar of a marked
    section start IS that section start (a measured 15 against a section line at 16 is the line, so
    a pairing there is two-sided against a marked point, not one bar off it).  None when the track
    has no usable measured bass, or has bass from bar one (then its first section start or fill end
    is used, as before)."""
    hb = (track.get("content") or {}).get("has_bass")
    if not hb:
        return None
    w = int(BASS_IN_WINDOW)
    for b in range(len(hb)):
        if hb[b] and sum(1 for v in hb[b:b + w] if v) >= BASS_IN_FRACTION * w:
            if b == 0:
                return None
            near = [x[0] for x in _sections(track) if abs(x[0] - b) <= 1.0 + 1e-9]
            return min(near, key=lambda x: abs(x - b)) if near else float(b)
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
    if any(abs(k - e) < 1e-9 for e in _fill_ends(track)):
        return "fill end", W_Q_FILL
    if any(abs(k - x[0]) < 1e-9 for x in _sections(track)):
        return "section start", W_Q_SECTION
    t = point_type(track, k)
    if t == "implied 16":
        return t, W_Q_IMPL16
    if t == "implied 8":
        return t, W_Q_IMPL8
    return "", W_Q_NONE


def loop_source(track, r6=R6_BE):
    """R4/R3: the outgoing phrase a tail loop repeats: the latest whole phrase (consecutive phrase
    points, at least MIN_LOOP_SOURCE bars, at most the last MAX_LOOP_SOURCE of them) that still has
    its kick in at least LOOP_SOURCE_KICK of its bars.  Not simply the final bar, which may be a fade
    or a fill.  Returns (start, end); without measured kick any whole phrase will do."""
    n = natural_end_bar(track)
    pts = [p for p in phrase_points(track) if p <= n]
    for p, q in reversed(list(zip(pts, pts[1:]))):
        if q - p < MIN_LOOP_SOURCE:
            continue
        p = max(p, q - MAX_LOOP_SOURCE)
        m = _measured(track, p, R6_BE)
        if m is not None:
            c = track["content"]["has_kick"]
            bars = list(range(int(p), int(q)))
            if bars and sum(1 for b in bars if b < len(c) and c[b]) < LOOP_SOURCE_KICK * len(bars):
                continue
        return (p, q)
    return (max(0.0, n - MIN_LOOP_SOURCE), n)


def _loop_source_good(track, src, r6=R6_BE):
    """Round 8, fix 2: whether the loop source phrase keeps its kick (the existing loop_source
    rule).  A loop is free only when the source is good; without measured kick any whole phrase
    counts, as loop_source assumes."""
    m = _measured(track, src[0], R6_BE)
    if m is None:
        return True
    c = track["content"]["has_kick"]
    bars = list(range(int(src[0]), int(src[1])))
    return bool(bars) and sum(1 for b in bars if b < len(c) and c[b]) >= LOOP_SOURCE_KICK * len(bars)


def _out_bar(track, x, src):
    """Map an out bar to the bar whose content plays: bars past the end repeat the loop source."""
    n = natural_end_bar(track)
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
    n = natural_end_bar(track)
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
    n = natural_end_bar(track)
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


def content_check(out_t, in_t, a, s, e, loop_p=0.0, loop_l=0.0, r6=R6_BE, out_src=None, in_cut=0.0):
    """R6 per-stretch content check.  Walks the blend bar by bar and merges runs of the same
    (outgoing gives, incoming gives) state, so adjacent flat bars form one flat run.  Returns
    (stretches, flat_bars); a stretch is {start, end, out, in, flat, music_bars} where music_bars
    counts bars in it where either side has measured music (reported, never rescues a flat bar).
    Looped outgoing bars read their content from the loop source phrase; a cut
    incoming (round 8 fix 3) is heard from its bar in_cut."""
    stretches, flat = [], 0.0
    n_out = natural_end_bar(out_t)
    if out_src is None and e > n_out + 1e-9:
        out_src = loop_source(out_t, r6)
    x = a
    while x < e - 1e-9:
        nxt = min(x + 1.0, e)
        if x < n_out - 1e-9 and n_out < nxt:
            nxt = n_out
        j = _in_bar(x, a, loop_p, loop_l) + in_cut
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


# ---- vocal and key clash (optional data: has_vocal per bar, key as a Camelot code) -------
def _bar_flags(track, name, why=None, who="track"):
    """The per-bar 0/1 list `name` from track["content"], or None when it is absent, empty, holds anything
    but finite numbers or booleans (a NaN is missing data, not a vocal), or is not on the track's bar clock:
    its length must be ceil(n_bars) and equal that of every other per-bar list the track carries.  When
    the data is there but unusable, the reason goes on `why`."""
    c = track.get("content")
    flags = c.get(name) if isinstance(c, dict) else None
    if not flags:
        return None
    problem = None
    if not isinstance(flags, (list, tuple)) or not all(
            isinstance(v, (bool, int, float)) and isfinite(v) for v in flags):
        problem = "holds values that are not finite 0/1 flags"
    else:
        n = _num(track.get("n_bars"))
        others = [len(c[k]) for k in ("has_kick", "has_bass", "has_music", "has_vocal")
                  if k != name and isinstance(c.get(k), (list, tuple)) and c[k]]
        if n is None or len(flags) != int(ceil(n)) or any(length != len(flags) for length in others):
            problem = "has %d bars for a %s bar track (other lists: %s)" % (
                len(flags), "%g" % n if n is not None else "unknown", others or "none")
    if problem:
        if why is not None:
            why.append("%s %s %s: no %s cost" % (who, name, problem, "vocal" if name == "has_vocal" else "key"))
        return None
    return flags


def _flag_at(flags, bar):
    return bool(flags[min(max(int(bar + 1e-9), 0), len(flags) - 1)])


def overlap_walk(out_t, in_t, a, e, loop_p=0.0, loop_l=0.0, out_src=None, in_cut=0.0, r6=R6_BE):
    """(length, outgoing bar, incoming bar) for each stretch of the heard overlap [a, e), stepped exactly as
    content_check steps it: looped outgoing bars read their loop source, a cut incoming starts in_cut bars in."""
    n_out = natural_end_bar(out_t)
    if out_src is None and e > n_out + 1e-9:
        out_src = loop_source(out_t, r6)
    x = a
    while x < e - 1e-9:
        nxt = min(x + 1.0, e)
        if x < n_out - 1e-9 and n_out < nxt:
            nxt = n_out
        yield nxt - x, _out_bar(out_t, x, out_src or (0.0, 1.0)), max(_in_bar(x, a, loop_p, loop_l) + in_cut, 0.0)
        x = nxt


def vocal_clash_bars(out_t, in_t, a, e, loop_p=0.0, loop_l=0.0, out_src=None, in_cut=0.0, why=None, r6=R6_BE):
    """Overlap bars in which BOTH tracks have a vocal (the whole overlap, before and after the swap: the
    outgoing's vocal still plays when its low end has gone).  None when either track has no has_vocal."""
    ov, iv = _bar_flags(out_t, "has_vocal", why, "outgoing"), _bar_flags(in_t, "has_vocal", why, "incoming")
    if ov is None or iv is None:
        return None
    return sum(n for n, ob, ib in overlap_walk(out_t, in_t, a, e, loop_p, loop_l, out_src, in_cut, r6)
               if _flag_at(ov, ob) and _flag_at(iv, ib))


def tonal_overlap_bars(out_t, in_t, a, e, loop_p=0.0, loop_l=0.0, out_src=None, in_cut=0.0, why=None, r6=R6_BE):
    """Overlap bars in which BOTH tracks have measured music.  A kick-and-bass-only bar cannot clash in key,
    and a key clash needs both tracks tonal at once (content_check, by contrast, counts either side's music
    after the swap because it asks whether the blend has anything on top).  None when either track has no
    usable has_music."""
    om, im = _bar_flags(out_t, "has_music", why, "outgoing"), _bar_flags(in_t, "has_music", why, "incoming")
    if om is None or im is None:
        return None
    return sum(n for n, ob, ib in overlap_walk(out_t, in_t, a, e, loop_p, loop_l, out_src, in_cut, r6)
               if _flag_at(om, ob) and _flag_at(im, ib))


def camelot_code(key):
    """The key as a normalised Camelot code ("8A"), or None when it is not one.  ASCII digits only."""
    try:
        k = str(key).strip().upper() if key is not None else ""
        if len(k) < 2 or not k.isascii() or k[-1] not in "AB" or not k[:-1].isdigit() or not 1 <= int(k[:-1]) <= 12:
            return None
        return "%d%s" % (int(k[:-1]), k[-1])
    except (TypeError, ValueError):
        return None


def camelot_steps(key_a, key_b):
    """Moves between two Camelot codes: one per number step round the wheel, one for a letter flip.  0 is the
    same key.  None when either is not a Camelot code."""
    a, b = camelot_code(key_a), camelot_code(key_b)
    if a is None or b is None:
        return None
    (na, la), (nb, lb) = _parse_camelot(a), _parse_camelot(b)
    return _camelot_distance(na, nb) + (la != lb)


def key_type(key_a, key_b):
    """The repo's own name for how two keys mix (automated_dj_mixes.sequencer.compatibility_score, the rule
    that orders the playlist): identical, smooth, relative_key, power_mix and diagonal all mix; clash does not.
    None when either is not a Camelot code."""
    a, b = camelot_code(key_a), camelot_code(key_b)
    return None if a is None or b is None else compatibility_score(a, b)[1]


def key_excess(key_a, key_b):
    """How far a pair is beyond what the sequencer calls compatible, in wheel steps, capped at KEY_STEP_CAP:
    0 for every pair the sequencer accepts (same key, a neighbour, the relative key, a diagonal, a power mix);
    for a clash, the distance past the nearest accepted pair (a same-letter pair is accepted out to 2 numbers,
    a letter-flipped one out to 1).  None when either is not a Camelot code."""
    a, b = camelot_code(key_a), camelot_code(key_b)
    if a is None or b is None:
        return None
    score, _ = compatibility_score(a, b)
    if score >= 1:
        return 0
    (na, la), (nb, lb) = _parse_camelot(a), _parse_camelot(b)
    return min(_camelot_distance(na, nb) - (1 if la != lb else 2), KEY_STEP_CAP)


# ---- candidate generation and scoring ---------------------------------------------------
def _is_trim(out_t, e, out_pts):
    """Tail trim test (round 5): dropping the outgoing's last bars is allowed only when it is plainly
    not a usable outro: at most MAX_TRIM_BARS bars, no outgoing phrase point strictly inside the
    dropped span, and the stop does not land inside a section labelled OUTRO."""
    n_out = natural_end_bar(out_t)
    cut = n_out - e
    if cut > MAX_TRIM_BARS + 1e-9:
        return False
    if any(e + 1e-9 < p < n_out - 1e-9 for p in out_pts):
        return False
    return _section_at(out_t, min(e, n_out - 1e-6))[1] != "OUTRO"


def _score(out_t, in_t, a, s, e, loop_p, loop_l, out_pts, in_pts, r6=R6_BE, in_cut=0.0, *, cue_end=None):
    n_out = natural_end_bar(out_t)
    n_nat = _natural_end_threshold(out_t, n_out)
    why, cost, terms = [], 0.0, {}
    cue_end = e if cue_end is None else cue_end
    if _audio_end_bar(out_t) is None:
        why.append("outgoing end_bar %s: using n_bars=%g%s" %
                   ("invalid" if "end_bar" in out_t else "absent", n_out,
                    " with legacy last-kick cue allowance" if n_nat < n_out else ""))
    else:
        why.append("outgoing audio end %g, intended cue %g, offset %+.6g bars (audio end minus cue)" %
                   (n_out, cue_end, n_out - cue_end))
    fill_ends = set(_fill_ends(out_t))
    sec_starts = {x[0] for x in _sections(out_t)}
    out_cues = set(out_pts) | set(implied_points(out_t))   # every outgoing cue point, marked or implied
    bars_min = MINUTE_SECONDS / _bar_seconds(out_t)       # bars in a minute, outgoing tempo

    # round 5: is this a SWAP-ANCHORED plan?  The swap is on the incoming's bass-in and about 30 s
    # before the ORIGINAL outgoing end.  Then the entry is derived from the swap, not chosen, so the
    # one-minute-mark awareness does not apply to it (the entry-type cost still does: R1 "normally").
    k = s - a - loop_l + in_cut             # the incoming bar heard at the swap (fix 3: a cut shifts it back)
    bi = bass_in(in_t)
    forbidden_cut = (e < n_nat - 1e-9) and not _is_trim(out_t, e, out_pts)
    e_swap = e if forbidden_cut else n_out
    anchored = (bi is not None and abs(k - bi) < 1e-9
                and abs((e_swap - s) - bars_min / 2.0) <= ANCHOR_TOL_BARS + 1e-9)

    s_type, k_type = point_type(out_t, s), point_type(in_t, max(k, 0.0))
    out_hit, in_hit = bool(s_type), bool(k_type)
    # a pairing needs a point on both tracks and at least one MARKED (implied with implied is anchored
    # to nothing the owner marked)
    paired = out_hit and in_hit and (s_type.startswith("marked") or k_type.startswith("marked"))
    # R1, round 8 fix 3: the entry of a pairing should MEET an outgoing cue point (fill end, section
    # start, else the implied grid).  A pairing whose derived entry misses every cue pays; the
    # planner also generates intro-extended / intro-cut variants whose entry meets one (free).
    if paired:
        if any(abs(a - c) < 1e-9 for c in out_cues):
            why.append("round 8: pairing entry at out bar %g meets an outgoing cue point (%s)"
                       % (a, point_type(out_t, a) or "marked"))
        else:
            cost += W_ENTRY_OFF
            terms["entry"] = W_ENTRY_OFF
            why.append("round 8: pairing entry at out bar %g meets NO outgoing cue point (a phrase "
                       "point of either kind); the intro could be extended or cut to reach one" % a)
        why.append("round 6: pairing: the entry (out bar %g) follows from lining the swap up on both tracks" % a)
    if anchored:
        why.append("round 5: swap-anchored: the swap is the incoming's bass-in (incoming bar %g) about 30 s "
                   "before the original end, so the entry (out bar %g) is derived from it" % (k, a))
    if not paired:
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
    if anchored or paired:
        pass
    elif minute:
        why.append("awareness: entry at out bar %g is a one-minute mark (a phrase point about a minute "
                   "before the outgoing end, %g)" % (a, mark))
    else:
        cost += W_MINUTE_OFF
        terms["minute_mark"] = W_MINUTE_OFF

    # R2: prefer a two-sided cue pairing. One-sided swaps are a fallback.
    kind, qcost = in_point_kind(in_t, k)
    if qcost:
        cost += qcost
        terms["in_point"] = qcost
    ocost = {"implied 16": W_OUT_IMPL16, "implied 8": W_OUT_IMPL8}.get(s_type, 0.0)
    if ocost:
        cost += ocost
        terms["out_point"] = ocost
    if out_hit or in_hit:
        who = "both tracks" if paired else ("the outgoing only" if out_hit and not in_hit
                                            else "the incoming only" if in_hit and not out_hit
                                            else "both tracks, but implied on both (no marked anchor)")
        why.append("R2: switch is a phrase point of %s (out bar %g is a %s, incoming bar %g is %s)" % (
            who, s, s_type or "non-point", k, ("the incoming's %s" % kind if kind else "a non-point")))
        if not paired:
            mark_s = n_out - 30.0 / _bar_seconds(out_t)
            near = [p for p in sorted(out_cues) if p < n_out - 1e-9
                    and abs(p - mark_s) * _bar_seconds(out_t) <= 20.0]
            why.append("ONE-SIDED swap: it lines up with a phrase point of only one track; "
                       + ("the outgoing offers phrase points near the 30 s mark (out bars %s) that a pairing could use"
                          % ", ".join("%g" % p for p in near) if near
                          else "the outgoing offers no phrase point near the 30 s mark, so this is the fallback"))
    else:
        cost += W_SWITCH_NONE
        terms["switch_phrase"] = W_SWITCH_NONE
        why.append("R2: switch is on no phrase point of either track (out bar %g, incoming bar %g)" % (s, k))

    # R3/R4/R5: end, loops, cuts.  Two clocks (round 8, see the module docstring): the cut/trim and
    # swap tests measure the ORIGINAL track; the length and short_blend clocks measure what is heard.
    looped = max(0.0, e - n_out)
    out_end = e
    cut = 0.0
    trim = 0.0
    if looped > 0:
        # round 8, fix 2: a loop is valued, not taxed.  Looping up to the length needed to reach the
        # exit the pairing calls for (the incoming's next section start or fill start, capped by the
        # long-fill bound) is FREE when the source keeps its kick. Extra bars or a poor source cost.
        # MAX_TAIL_LOOP rejects candidates during enumeration.
        src = loop_source(out_t, r6)
        t_called = _called_for_exit(in_t, k)
        free_len = max(0.0, a + loop_l + t_called - in_cut - n_out) if t_called is not None else 0.0
        if not _loop_source_good(out_t, src, r6):
            free_len = 0.0
        extra = max(0.0, looped - free_len)
        if extra > 0:
            cost += W_LOOP * extra
            terms["out_loop"] = W_LOOP * extra
        why.append("R3/R4: outgoing tail looped %g bars to reach the incoming phrase point at incoming bar %g; "
                   "the blend calls for %g of them (up to the exit at incoming bar %s) and %g bars repeat"
                   % (looped, e - a - loop_l + in_cut, min(looped, free_len),
                      "%g" % t_called if t_called is not None else "none", extra))
    elif e >= n_nat - 1e-9:
        out_end = n_out
        why.append("R3: outgoing ends naturally at incoming bar %g (intended cue at incoming bar %g)"
                   % (out_end - a - loop_l + in_cut, cue_end - a - loop_l + in_cut))
    elif _is_trim(out_t, e, out_pts):
        trim = n_out - e
        cost += W_TRIM * trim
        terms["trim"] = W_TRIM * trim
        why.append("tail trim: the outgoing stops at out bar %g, dropping its last %g bars, which hold "
                   "no phrase point and are not an outro section (not a usable outro)" % (e, trim))
    else:
        cut = n_out - e
        cost += W_CUT * cut
        terms["cut"] = W_CUT * cut
        why.append("R5: outgoing outro CUT by %g bars" % cut)
    e_sw = e if cut > 0 else n_out     # swap clock (original phrasing): to the ORIGINAL end unless a forbidden cut moved it
    if loop_l > 0:
        # round 8, fix 3: an incoming intro loop exists only to serve the blend (meet a phrase point
        # with the swap or the entry), exactly like an outgoing loop: it is free.  It is capped by
        # construction at whole phrases inside the intro that complete before the swap.
        why.append("R3/R4 (round 8): incoming intro looped %g bars (phrase ending at incoming bar %g): free, "
                   "it only makes the blend meet the phrase points" % (loop_l, loop_p))
    if in_cut > 0:
        why.append("round 8: incoming intro cut by %g bars so the entry meets an outgoing cue point" % in_cut)

    # R1/R2 clocks, soft.  length is the ACTUAL overlap heard (loops count - they make the blend
    # longer, which is what a loop is for); switch_time stays on the ORIGINAL phrasing clock.
    terms["length"] = W_LEN * abs((out_end - a) - bars_min)
    terms["switch_time"] = W_SWITCH_TIME * abs((e_sw - s) - bars_min / 2.0)
    cost += terms["length"] + terms["switch_time"]
    bs = _bar_seconds(out_t)
    why.append("blend %g bars (%.0f s) as heard, switch %g bars (%.0f s) before the original end"
               % (out_end - a, (out_end - a) * bs, e_sw - s, (e_sw - s) * bs))
    # round 8, fix 7: a soft minimum on the heard overlap, so a ~26 s blend with ~10 s left cannot win
    heard_s = (out_end - a) * bs
    if heard_s < MIN_BLEND_SECONDS - 1e-9:
        terms["short_blend"] = W_SHORT_BLEND * (MIN_BLEND_SECONDS - heard_s)
        cost += terms["short_blend"]
        why.append("round 8: heard blend is only %.0f s (soft minimum %g s)" % (heard_s, MIN_BLEND_SECONDS))
    # round 8, fix 4: an entry before the mix zone is costed, not banned (allowed at all only for a
    # long incoming intro before its bass-in - the candidates() filter)
    zone_lo = n_out - _zone_bars(out_t)
    if a < zone_lo - 1e-9:
        terms["entry_early"] = W_ENTRY_EARLY * (zone_lo - a)
        cost += terms["entry_early"]
        why.append("round 8: entry %.0f s before the mix zone (a long incoming intro)" % ((zone_lo - a) * bs))
    if _valid(in_t):
        in_min = MINUTE_SECONDS / _bar_seconds(in_t)
        end_in = out_end - a - loop_l + in_cut
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
    stretches, flat = content_check(out_t, in_t, a, s, out_end, loop_p, loop_l, r6, src, in_cut)
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

    # vocal and key clash: optional soft terms, zero and silent without the data
    vocal_bars = vocal_clash_bars(out_t, in_t, a, out_end, loop_p, loop_l, src, in_cut, why, r6)
    k_out, k_in = out_t.get("key"), in_t.get("key")
    steps, excess, k_type = camelot_steps(k_out, k_in), key_excess(k_out, k_in), key_type(k_out, k_in)
    tonal = (tonal_overlap_bars(out_t, in_t, a, out_end, loop_p, loop_l, src, in_cut, why, r6)
             if steps is not None else None)
    key_clash = None if steps is None or tonal is None else excess * tonal
    raw_v = W_VOCAL_CLASH * vocal_bars if vocal_bars is not None else 0.0
    raw_k = W_KEY_CLASH * key_clash if key_clash is not None else 0.0
    scale = min(1.0, MAX_CLASH_COST / (raw_v + raw_k)) if raw_v + raw_k > 0 else 1.0
    if scale < 1.0:
        why.append("vocal and key clash together would cost %.2f; capped at %g so they cannot veto the blend"
                   % (raw_v + raw_k, MAX_CLASH_COST))
    if raw_v * scale:
        cost += raw_v * scale
        terms["vocal_clash"] = raw_v * scale
    if raw_k * scale:
        cost += raw_k * scale
        terms["key_clash"] = raw_k * scale
    if vocal_bars:
        why.append("vocals overlap %g bars (both tracks have a vocal)" % vocal_bars)
    if steps is not None and tonal is not None:
        why.append("key %s to %s: %s over %g tonal bars" % (
            k_out, k_in, "%s, compatible" % k_type if excess == 0 else "clash, %d steps apart" % steps, tonal))
    elif steps is not None:
        why.append("key %s to %s: tonal content unknown, no key cost" % (k_out, k_in))
    elif k_out is not None or k_in is not None:
        why.append("key unknown for %s: no key cost" % ("both" if camelot_code(k_out) is None
                                                         and camelot_code(k_in) is None else
                                                         "the outgoing" if camelot_code(k_out) is None
                                                         else "the incoming"))

    # round 6 sane tier, round 8 fix 4: the window and length cliffs are gone; sane means a
    # both-sided pairing that keeps bottom end and does not cut the outgoing's usable outro.  The
    # swap's distance from the 30 s mark and the blend's length are graded costs, not tier gates.
    sane = paired and terms.get("flat", 0.0) == 0.0 and cut == 0.0
    if paired:
        why.append("pairing: %s" % ("sane, so it outranks every one-sided swap" if sane else
                                    "both-sided but not sane (a flat run or a forbidden cut)"))
    return BlendPlan(in_starts_at_out_bar=a, bass_switch_out_bar=s, out_end_bar=out_end,
                     out_looped_bars=looped, in_intro_looped_bars=loop_l, out_cut_bars=cut,
                     in_intro_cut_bars=in_cut,
                     stretches=stretches, flat_bars=flat, cost=cost, why=why,
                     flat_runs=runs, terms=terms, out_loop_source_bars=src,
                     in_loop_phrase_end=loop_p if loop_l > 0 else 0.0, minute_mark=minute,
                     out_trim_bars=trim, swap_at_bass_in=at_bass_in, swap_incoming_bar=k,
                     swap_anchored=anchored, paired=paired, sane=sane, in_point_kind=kind or k_type,
                     out_point_kind=s_type, vocal_clash_bars=vocal_bars, key_clash=key_clash,
                     key_steps=steps, key_type=k_type, key_tonal_bars=tonal or 0.0,
                     key_pair=(k_out, k_in) if steps is not None else None)


def _rank_key(b):
    """Sane both-sided pairings first (a tier, round 6), then cheapest first; on an exact tie prefer
    no incoming loop, then less outgoing loop, then the earlier entry, switch and end."""
    return (0 if b.sane else 1, round(b.cost, 9), b.in_intro_looped_bars, b.out_looped_bars,
            b.in_starts_at_out_bar, b.bass_switch_out_bar, b.out_end_bar)


def _drop_forbidden_cuts(plans):
    """R5 excludes every plan that cuts a usable outro."""
    return [b for b in plans if b.out_cut_bars == 0]


def candidates(out_t, in_t, limit=None, r6=R6_BE, *, why=None):
    """Ranked list of BlendPlan, best first.  Switch points come from ORIGINAL phrase points
    only (R4); loops only lengthen the blend.  Entries and switches outside the mix zones
    are not considered (R9). R5 excludes cuts of usable outros. Pass a list as `why` to collect
    input warnings and the reason when no plan can be returned."""
    out_valid = _valid(out_t, why, "outgoing")
    in_valid = _valid(in_t, why, "incoming")
    if not (out_valid and in_valid):
        return []
    notes = []
    for name, track in (("outgoing", out_t), ("incoming", in_t)):
        warnings = []
        _fills(track, warnings)
        _sections(track, warnings)
        notes.extend("%s: %s" % (name, warning) for warning in warnings)
    if why is not None:
        why.extend(notes)
    n_out = natural_end_bar(out_t)
    n_in = natural_end_bar(in_t)
    out_pts = phrase_points(out_t)
    in_pts = phrase_points(in_t)
    zone_lo = n_out - _zone_bars(out_t)
    zone_in = _zone_bars(in_t)
    out_zone = [p for p in out_pts if zone_lo - 1e-9 <= p < n_out - 1e-9]
    in_zone = [p for p in in_pts if 0 < p <= min(n_in, zone_in) + 1e-9]
    out_impl = [p for p in sorted(implied_points(out_t)) if zone_lo - 1e-9 <= p < n_out - 1e-9]
    in_impl = [p for p in sorted(implied_points(in_t)) if 0 < p <= zone_in + 1e-9]
    tgts = [t for t in target_points(in_t) if t <= n_in + 1e-9]
    in_sections = {x[0] for x in _sections(in_t)}
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
        for s in out_zone + out_impl:           # switch on an outgoing phrase point (marked or implied)
            for k in in_zone + in_impl:         # ... and an incoming one: a PAIRING, entry derived
                if s in out_impl and k in in_impl:
                    continue                    # implied with implied: no marked anchor
                a_pair = s - k - l
                trips.add((a_pair, s, p, l))
                if a_pair >= n_out - ENTRY_MAX_SECONDS / _bar_seconds(out_t):
                    derived.add((a_pair, s, p, l))
        for s in out_zone:                      # switch on an outgoing phrase point
            for k in in_zone:
                trips.add((s - k - l, s, p, l))
            for a in out_zone:                  # ... entry on an outgoing phrase point
                if a < s:
                    trips.add((a, s, p, l))
        for a in out_zone:                      # entry on a phrase point, switch on an incoming one
            for k in in_zone:
                trips.add((a, a + l + k, p, l))

    # round 8, fix 3: for every trip whose swap is a two-sided pairing (a phrase point of both
    # tracks), also offer intro-extended and intro-cut variants whose ENTRY meets the previous or
    # next outgoing cue point.  Both are free: the loop/cut exists only to make the entry meet the
    # outgoing's phrasing ("this counts for the intro as well").
    out_cues = set(out_pts) | set(implied_points(out_t))
    in_cues = set(in_pts) | set(implied_points(in_t))
    first_non_intro = next((x[0] for x in _sections(in_t) if x[1] != "INTRO"), n_in)
    trips5 = set()  # (a, s, loop_p, loop_l, in_cut)
    for a, s, p, l in trips:
        trips5.add((a, s, p, l, 0.0))
        k = s - a - l
        if k < -1e-9 or _snap(max(k, 0.0)) not in in_cues or _snap(s) not in out_cues:
            continue                            # only pairings: the swap line-up is the fixed thing
        if any(abs(a - c) < 1e-9 for c in out_cues):
            continue                            # the entry already meets a cue point
        prev = max([c for c in out_cues if c < a - 1e-9], default=None)
        nxt = min([c for c in out_cues if c > a + 1e-9], default=None)
        if prev is not None:
            # EXTEND: loop intro bars so the entry moves back to the previous cue point
            d = a - prev
            l2, p2 = l + d, (p if p > 0 else k)
            if l2 >= MIN_IN_LOOP and p2 - l2 >= -1e-9 and p2 <= first_non_intro + 1e-9:
                trips5.add((prev, s, p2, l2, 0.0))
        if nxt is not None:
            # CUT: start part-way into the intro so the entry moves forward to the next cue point;
            # never cut through the incoming fill that leads into the swap
            c = nxt - a
            l2 = l - c
            cut = max(0.0, -l2)
            fill_starts = [fs for fs, fe in _fills(in_t) if abs(fe - k) < 1e-9]
            if cut <= min([first_non_intro] + fill_starts) + 1e-9:
                trips5.add((nxt, s, p if l2 > 1e-9 else 0.0, max(0.0, l2), cut))

    n_nat = _natural_end_threshold(out_t, n_out)
    exact_end = _audio_end_bar(out_t) is not None
    plans, seen = [], set()
    for a, s, p, l, cut_in in trips5:
        if a < 0 or a >= n_out or s - a < MIN_SIDE_BARS:
            continue
        if 0 < l < MIN_IN_LOOP:
            continue
        k = s - a - l + cut_in
        if k - cut_in < p - 1e-9 or k > min(n_in, zone_in) + 1e-9:
            continue
        entry_floor = n_out - ENTRY_MAX_SECONDS / _bar_seconds(out_t)
        if a < zone_lo - 1e-9 and (a, s, p, l) not in derived:
            # round 8, fix 4: an early entry is allowed (and costed, in _score) when the incoming
            # plays a long intro before the swap; ENTRY_MAX_SECONDS stays as an enumeration bound
            if k * _bar_seconds(in_t) < LONG_INTRO_SECONDS or a < entry_floor - 1e-9:
                continue
        if s >= n_out - 1e-9:  # R4: a switch in the looped tail would be a loop-derived point
            continue
        long_fills = _long_fills(in_t, k)
        exit_targets = sorted(tgts, key=lambda t: abs(a + l + t - cut_in - n_out)) if exact_end else tgts
        for t in exit_targets:
            cue_end = a + l + t - cut_in
            e = cue_end
            if (exact_end and abs(cue_end - n_out) <= BEAT_BARS + 1e-9
                    and n_out - s >= MIN_SIDE_BARS
                    and _clears_long_fills(n_out - a - l + cut_in, k, long_fills, in_sections)):
                e = n_out
            if e - s < MIN_SIDE_BARS or e > n_out + MAX_TAIL_LOOP:
                continue
            played_end = n_out if n_nat - 1e-9 <= e <= n_out else e
            if not _clears_long_fills(played_end - a - l + cut_in, k, long_fills, in_sections):
                continue
            sig = (round(a, 6), round(s, 6), round(played_end, 6), p, l, cut_in)
            if sig in seen:
                continue
            seen.add(sig)
            plan = _score(out_t, in_t, a, s, e, p, l, out_pts, in_pts, r6, cut_in, cue_end=cue_end)
            plan.why.extend(notes)
            plans.append(plan)
    legal = _drop_forbidden_cuts(plans)
    if not legal and why is not None:
        why.append("every candidate cuts the outgoing's usable outro" if plans else
                   "no candidate fits the cue points and mix bounds")
    plans = legal
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


def plan_blend(out_track, in_track, r6=R6_BE, *, why=None):
    """The best blend, with `.alternatives` (other defensible swap points), or None if no plan fits
    the rules. Pass a list as `why` to collect input warnings and the reason for a None result."""
    cs = candidates(out_track, in_track, r6=r6, why=why)
    if not cs:
        return None
    cs[0].alternatives = alternatives_for(cs, in_track)
    return cs[0]
