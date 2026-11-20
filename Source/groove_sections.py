"""Groove section detector ("groove-1"), selected with detect(section_mode="groove").

Section lines follow the rhythm section, from the owner's answer key of
2026-11-20 (Documentation/Golden Sections/Answer Key 2026-11-20 DFTD.json):
lines where the FULL kick leaves or returns, and where the bass comes or goes
under a full kick. Fills are first-class output and never cut a section.

This module is the whole of the new detector. stem_detector.detect() calls
groove_detect() only when section_mode == "groove"; the default "legacy" mode
never imports it, so the legacy output stays byte-identical to main.

The detector never moves bar one. It measures whether bar one looks wrong
(analysis.bar_one_suspect) and leaves the fix to the grid, upstream.

Fill edges are beat-precise (quarter bars). A fill into a break ends on the
phrase point, which is a bar line, so it is beat-precise too.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

import stem_detector as sd

SECTION_DETECTOR = "groove-1"
SECTIONS_SCHEMA = 2

# --- constants: each number with its reason -------------------------------------
KICK_FULL_FRAC = 0.75     # a heard kick is FULL at >= this fraction of the solid kick level.
                          # Measured on the key: full-groove beats 0.78-1.2; the light kicks of
                          # fills (Eli Brown), soft intros (Passion, PAX) and breaks (Call 911,
                          # Ashibah) 0.2-0.73. Score is flat for 0.65-0.85 on the seven.
KICK_FULL_BAR_BEATS = 3   # a bar is full-kick with 3 of its 4 beats full: one dropped beat is a
                          # syncopation or a one-beat fill, not a change of state
BASS_ON_FRAC = 0.10       # a beat has bass at >= this fraction of the bass stem's solid beat level.
                          # A truly absent bass leaks 0-5% through Demucs; a thinned bass
                          # (Ninetoes 144-160, 14-30%) is still bass and must not cut a line
BASS_BAR_BEATS = 2        # a bar has bass when at least 2 of its 4 beats do: a single note on
                          # beat 1 is the tail of the last phrase (Hannah Wants 168, owner check)
BASS_IN_KICK_FRAC = 0.10  # the bass stem is EMPTY across the groove when even its 90th percentile
                          # over the full-kick bars is below this fraction of its solid level. Then
                          # the bass lives in the kick/sub (PAX Revelry, p90 0.06; every other
                          # track in the key and the holdout is >= 0.98) and the low band is read.
                          # A track whose separate bassline enters late keeps the bass stem.
LOW_ON_FRAC = 0.60        # low band present at >= this fraction of its solid level (kick+sub in a
                          # bass-in-kick track; its fills and breaks fall to 0.1-0.4)
SHORT_FILL_BARS = 2       # Sam: fills are "one or two bar" dropouts. Up to this, a kick-down run is
                          # a fill even with the bass gone; longer with the bass gone is a break
FILL_MAX_BARS_BASS_HELD = 6   # NOT owner-stated: the owner says fills are one or two bars. This
                              # exception (a kick-out up to 6 bars stays a fill while the bass holds
                              # through it) is fitted to ONE key case, Back In The Dance 119-124.
                              # Set it to SHORT_FILL_BARS to drop the exception.
FILL_LEAD_BARS = 2        # a kick drop at most this many bars before a phrase point is the fill INTO
                          # that phrase; the section starts at the phrase point (Eli Brown 30-32)
TRAIL_DROP_FRAC = 0.5     # inside a soft break, a beat at under half the break's own median drum
                          # peak has dropped out: the fill before the kick returns
TRAIL_MAX_BEATS = 8       # that trailing fill is at most two bars (Sam: one or two bar fills)
RESIDUE_FRAC = 1.0 / 3.0  # a bass at under a third of its own solid (drop) level is a residue, not
                          # bass (Christoph - Reachin 176-184: 0.13-0.31 after a 0.9-1.0 drop)
LOW_THIN_FRAC = 0.85      # ...but only when the low band thins with it (Reachin 0.96 -> 0.67): a thin
                          # bass over a held low end is still bass (Ninetoes 144-160, low band 0.98x)
LONG_INTRO_SEC = 75.0     # an opening that runs "well past a minute" with no kick or bass change: the
                          # owner says intros are not longer than a minute (Harry Romero - Renegades)
MINUTE_TOL_SEC = 15.0     # the fill end chosen as the one-minute marker must be within this of 60 s
FILL_END_CUE_BARS = 1     # an energy or width cue within this many bars of a fill end is that fill's
                          # own change (a lead ending on a phrase point), not a section: Double Dutch 128
MIN_LINE_GAP_BARS = 2     # two lines closer than this are one musical event; the one with the
                          # stronger evidence stays (see RANK)
SILENT_BEAT_FRAC = 0.15   # bar-one REPORT only: a leading beat quieter than this fraction of the
                          # median beat peak counts as silence (Call 911 <= 0.09; quietest real
                          # intros in the key >= 0.25)
BAR_ONE_MIN_RETURNS = 3   # bar-one REPORT only: at least this many full-kick returns, and
BAR_ONE_PHASE_SHARE = 0.75  # this share of them on the same off-bar beat, before the grid is
                            # reported as sitting at a fractional bar offset

# Evidence ranks for MIN_LINE_GAP_BARS: a kick return is the strongest line,
# then a break start, a bass change, a tail stop, the one-minute fill, and last the
# older energy/width cues.
RANK = {"kick_return": 5, "outro": 5, "break_start": 4, "bass_change": 4, "tail": 3,
        "minute_fill": 2, "cue": 1}


# --- small helpers ---------------------------------------------------------------

def per_beat(env, hop_t, bpm, downbeat, n_beats, how="mean"):
    env = np.asarray(env, dtype=float)
    beat_sec = 60.0 / float(bpm)
    out = np.zeros(n_beats)
    for k in range(n_beats):
        i0 = int((downbeat + k * beat_sec) / hop_t)
        i1 = min(int((downbeat + (k + 1) * beat_sec) / hop_t), len(env))
        if i1 > i0:
            out[k] = env[i0:i1].max() if how == "max" else env[i0:i1].mean()
    return out


def cached_low_band(wav, cache_dir):
    """The cached low-band envelope (tiera_band_low), else None. Read-only."""
    cache = Path(cache_dir) / f"{Path(wav).stem}__stemenv.npz"
    try:
        with np.load(cache, allow_pickle=False) as d:
            return np.asarray(d[sd._TIERA_BAND_LOW_KEY]) if sd._TIERA_BAND_LOW_KEY in d.files else None
    except (OSError, ValueError):
        return None


def full_kick_beats(kick_on, peaks, ref):
    """Per-beat FULL kick: a kick is heard AND its drum peak reaches
    KICK_FULL_FRAC of the solid kick level."""
    strength = np.asarray(peaks, float) / (float(ref) + 1e-9)
    return np.asarray(kick_on, bool) & (strength >= KICK_FULL_FRAC)


def full_bars(full_beat, n_bars):
    fb = np.zeros(n_bars * 4, bool)
    fb[:min(len(fb), len(full_beat))] = np.asarray(full_beat, bool)[:len(fb)]
    return np.array([fb[b * 4:(b + 1) * 4].sum() >= KICK_FULL_BAR_BEATS for b in range(n_bars)])


def bass_state_per_bar(bass_beat, low_bar, full_bar):
    """Per-bar 'bass present' state, no smoothing (lasting checks debounce).

    Normally the Demucs bass stem, per beat: a bar has bass when BASS_BAR_BEATS
    of its beats reach BASS_ON_FRAC of the stem's solid beat level. Only when the
    bass stem is empty across the WHOLE groove (its 90th percentile over the
    full-kick bars below BASS_IN_KICK_FRAC) is the low band read instead.
    Returns (bass_on[bar], source) with source 'bass_stem' or 'low_band'.
    """
    full = np.asarray(full_bar, bool)
    n_bars = len(full)
    bb = np.zeros(n_bars * 4)
    bass_beat = np.asarray(bass_beat, float)
    bb[:min(len(bb), len(bass_beat))] = bass_beat[:len(bb)]
    solid = sd._solid_kick_level(bb) + 1e-12
    beat_on = bb >= BASS_ON_FRAC * solid
    stem_on = np.array([beat_on[b * 4:(b + 1) * 4].sum() >= BASS_BAR_BEATS for b in range(n_bars)])
    bar_level = np.array([bb[b * 4:(b + 1) * 4].mean() for b in range(n_bars)])
    if (low_bar is not None and full.any()
            and float(np.percentile(bar_level[full], 90)) < BASS_IN_KICK_FRAC * solid):
        low_bar = np.asarray(low_bar, float)[:n_bars]
        return low_bar >= LOW_ON_FRAC * sd._solid_kick_level(low_bar), "low_band"
    return stem_on, "bass_stem"


def refine_fill_beats(full_beat, s_bar, e_bar):
    """Beat-precise edges for a bar-level kick-down run [s_bar, e_bar)."""
    n = len(full_beat)
    k = 4 * s_bar
    while k - 1 >= max(0, 4 * s_bar - 3) and not full_beat[k - 1]:
        k -= 1
    while k < min(n, 4 * e_bar) and full_beat[k]:
        k += 1
    j = min(n, 4 * e_bar)
    while j - 1 > k and full_beat[j - 1]:
        j -= 1
    while j < min(n, 4 * e_bar + 3) and not full_beat[j]:
        j += 1
    return k, j


def one_beat_fills(heard_kick, full_beat, n_bars):
    """Single-beat kick dropouts that end on a bar line: beat 4 of a bar has no
    kick at all, beats 1-3 of that bar and beat 1 of the next are full. The
    owner found one by eye (MASON Collective bar 152 beat 4). Returns bars."""
    hk = np.asarray(heard_kick, bool)
    fb = np.asarray(full_beat, bool)
    out = []
    for b in range(n_bars - 1):
        k = 4 * b + 3
        if k + 1 >= min(len(hk), len(fb)):
            break
        if not hk[k] and fb[4 * b:4 * b + 3].all() and fb[k + 1]:
            out.append(b)
    return out


def trailing_drop_fill(strength, heard_kick, s_beat, e_beat):
    """The fill at the end of a kick-down run that ends in a full-kick return:
    the last beats before the return where even the soft groove stops. A beat
    is dropped when its drum peak falls below TRAIL_DROP_FRAC of the run's
    median, or when no kick is heard there although the run mostly has one.
    Returns (start_beat, e_beat) for 1..TRAIL_MAX_BEATS dropped beats right
    before e_beat, preceded by a beat that is not dropped; else None.
    (Eli Brown 47-48 and 111-112, Passion VIP 15-16 and 95-96, Ashibah 39-40.)"""
    st = np.asarray(strength, float)[s_beat:e_beat]
    hk = np.asarray(heard_kick, bool)[s_beat:e_beat]
    if len(st) < 2 * TRAIL_MAX_BEATS:
        return None
    med = float(np.median(st))
    mostly_heard = hk.mean() >= 0.5
    dropped = (st < TRAIL_DROP_FRAC * med) | (~hk & mostly_heard)
    j = len(st)
    while j > 0 and dropped[j - 1]:
        j -= 1
    n = len(st) - j
    if 1 <= n <= TRAIL_MAX_BEATS and j > 0:
        return s_beat + j, e_beat
    return None


def clip_fills_to_lines(spans, lines):
    """Beat spans [start, end) cut so that no section line sits inside a fill:
    a fill that runs past a line ends at the line (fills lead INTO lines).
    Overlapping spans keep the first. Returns sorted [start, end] beat pairs."""
    clean = []
    for kb, ke in sorted(spans):
        for L in lines:
            if kb < 4 * L < ke:
                ke = 4 * L
        if ke > kb and not any(kb < c[1] and c[0] < ke for c in clean):
            clean.append([kb, ke])
    return clean


def drop_changes_after_outro(cands, bass_changes, o_start, kinds=None):
    """After the bass has left, a bass residue coming and going cuts nothing:
    the bass-change evidence after the outro start is dropped (Reachin 194).
    A bar that also has other evidence (e.g. the kick stopping, Bad Behaviours
    143) keeps its line at the rank of that other evidence."""
    out = dict(cands)
    if o_start is None:
        return out
    for b in bass_changes:
        if b > o_start and b in out:
            other = [RANK[k] for k in (kinds or {}).get(b, set()) if k != "bass_change"]
            if other:
                out[b] = max(other)
            else:
                del out[b]
    return out


def minute_fill_line(lines, fill_end_bars, downbeat, sec_per_bar):
    """In an opening that runs past LONG_INTRO_SEC with no line, the end of the
    fill nearest one minute ends the intro (the fills are the markers when
    nothing else changes). Returns that bar, or None."""
    first = min(lines) if lines else None
    if first is not None and downbeat + first * sec_per_bar <= LONG_INTRO_SEC:
        return None
    limit = first if first is not None else float("inf")
    ends = [e for e in fill_end_bars if 0 < e < limit]
    if not ends:
        return None
    best = min(ends, key=lambda e: abs(downbeat + e * sec_per_bar - 60.0))
    return best if abs(downbeat + best * sec_per_bar - 60.0) <= MINUTE_TOL_SEC else None


def keep_strongest(cands, n_bars):
    """cands: {bar: rank}. Drop lines outside (0, n_bars); of two lines closer
    than MIN_LINE_GAP_BARS keep the higher rank (the earlier one on a tie)."""
    kept = []
    for b in sorted(x for x in cands if 0 < x < n_bars):
        r = cands[b]
        if kept and b - kept[-1][0] < MIN_LINE_GAP_BARS:
            if r > kept[-1][1]:
                kept[-1] = (b, r)
            continue
        kept.append((b, r))
    return [b for b, _ in kept]


# --- structure ---------------------------------------------------------------------

def groove_structure(full_beat, bass_on, n_bars):
    """Section lines and fills from the rhythm section (kick + bass).

      * A kick-down run is a FILL when short: up to SHORT_FILL_BARS always, up
        to FILL_MAX_BARS_BASS_HELD while the bass holds through it. A fill never
        makes a line by itself (Sam: a fill end is a phrase point for mixing,
        not a section, unless the kick or bass state changes there).
      * A longer kick-down run is a BREAK: lines at its start and where the full
        kick returns. The start line goes to the next phrase point (4 bar steps
        from the previous line) when that is at most FILL_LEAD_BARS ahead.
      * A kick-down head (from bar 0) is intro; the line is where the full kick
        arrives. A kick-down tail of MIN_SECTION_BARS or more gets a line where
        the kick stops.
      * Under a full kick, a LASTING bass change (old state for MIN_SECTION_BARS
        before, new state for MIN_SECTION_BARS after) is a line. When the change
        falls inside a fill of at most SHORT_FILL_BARS, or at its end, the line
        goes at the fill end.
      * Kick returns are not snapped to a grid (pickups, Ashibah 74).
    """
    bass_on = np.asarray(bass_on, bool)[:n_bars]
    full_bar = full_bars(full_beat, n_bars)
    cands, fills, breaks, lead_fills, bass_changes = {}, [], [], set(), []
    head_end = tail_start = None

    kinds = {}

    def add(bar, kind):
        cands[bar] = max(cands.get(bar, 0), RANK[kind])
        kinds.setdefault(bar, set()).add(kind)

    for s, e in sd._regions(~full_bar):
        if s == 0:
            head_end = e if e < n_bars else None
            if head_end is not None:
                add(e, "kick_return")
            continue
        if e >= n_bars:
            if e - s >= sd.MIN_SECTION_BARS:
                tail_start = s
                add(s, "tail")
            else:
                fills.append((s, e))
            continue
        length = e - s
        holds = bool(bass_on[s:e].all())
        if length > FILL_MAX_BARS_BASS_HELD or (length > SHORT_FILL_BARS and not holds):
            breaks.append((s, e))
        else:
            fills.append((s, e))

    last_line = 0
    for s, e in sorted(breaks):
        prior = [b for b in cands if b <= s]
        if prior:
            last_line = max(last_line, max(prior))
        step = sd.PHRASE_GRID
        g = last_line + step * int(math.ceil((s - last_line) / step))
        if 0 < g - s <= FILL_LEAD_BARS and g < e:
            fills.append((s, g))
            lead_fills.add((s, g))
            add(g, "break_start")
        else:
            add(s, "break_start")
        add(e, "kick_return")
        last_line = e

    in_break = np.zeros(n_bars, bool)
    for s, e in breaks:
        in_break[s:e] = True
    if head_end is not None:
        in_break[:head_end] = True
    W = sd.MIN_SECTION_BARS
    for b in range(1, n_bars):
        if bass_on[b] == bass_on[b - 1] or in_break[b] or in_break[b - 1]:
            continue
        s_pre, line = b, b
        for s, e in fills:
            if s <= b <= e and e - s <= SHORT_FILL_BARS:
                s_pre, line = s, e
        before = bass_on[max(0, s_pre - W):s_pre]
        after = bass_on[line:line + W]
        if len(before) < W or len(after) < W:
            continue
        old, new = bool(before[-1]), bool(after[0])
        if old != new and (before == old).all() and (after == new).all():
            add(line, "bass_change")
            bass_changes.append(line)

    fills = sorted(set((int(s), int(e)) for s, e in fills))
    return {"cands": cands, "lines": keep_strongest(cands, n_bars), "fills": fills,
            "breaks": breaks, "lead_fills": lead_fills, "head_end": head_end,
            "tail_start": tail_start, "full_bar": full_bar, "bass_changes": bass_changes,
            "kinds": kinds}


def bass_leave_bar(bass_on):
    """The bar after the bass's last bar of playing, or None.

    'Playing' is a bar with bass on BASS_BAR_BEATS of its beats (Hannah Wants:
    a lone note on beat 1 of bar 168 is the tail, the outro starts AT 168). The
    tail after the leave point may keep a one-note hit every few bars (Passion
    VIP 129, 133, 137, 141): the leave point is the start of the final stretch in
    which no 4 bar window has more than one bass bar."""
    bass_on = np.asarray(bass_on, bool)
    n = len(bass_on)
    if not bass_on.any():
        return None
    W = sd.MIN_SECTION_BARS
    b = n
    while b > 0:
        c = b - 1
        if bass_on[c:c + W].sum() > 1:
            break
        b = c
    # b is the earliest bar of the sparse tail; the leave point is right after a
    # playing bar, so walk forward over any leading isolated hits
    while b < n and bass_on[b]:
        b += 1
    return b if b < n else None


def residue_leave_bar(bass_bar, low_bar):
    """The end of the last run of MIN_SECTION_BARS or more bars at RESIDUE_FRAC or
    more of the bass stem's solid level, when from there on the bass stays a
    residue (at most a quarter of the bars back above it) AND the low band has
    thinned (its median over the next 8 bars under LOW_THIN_FRAC of its median
    over the 8 bars before). Else None. A faint residue is not bass."""
    if low_bar is None:
        return None
    bass_bar = np.asarray(bass_bar, float)
    low_bar = np.asarray(low_bar, float)
    n = len(bass_bar)
    solid = sd._solid_kick_level(bass_bar) + 1e-12
    strong = bass_bar >= RESIDUE_FRAC * solid
    runs = sd._regions(strong, sd.MIN_SECTION_BARS)       # the bass really playing
    if not runs:
        return None
    c = int(runs[-1][1])
    if n - c < sd.MIN_SECTION_BARS or c < 8:
        return None
    if strong[c:].mean() > 0.25:          # the tail must stay a residue, odd bars aside
        return None
    before = float(np.median(low_bar[c - 8:c]))
    after = float(np.median(low_bar[c:c + 8]))
    return c if after < LOW_THIN_FRAC * before else None


def outro_start(bass_on, fills, n_bars, low_out_in_fill, residue_bar=None):
    """Outro start: where the bass leaves (bass_leave_bar), moved to the end of
    a fill that contains that point or ends on it. Without a bass exit, the end
    of the last fill in the final OUTRO_CAP_BARS during which the low end also
    drops out. Else None."""
    b = bass_leave_bar(bass_on)
    if residue_bar is not None and (b is None or residue_bar < b):
        b = residue_bar                     # the bass left earlier: what followed was residue
    if b is not None and n_bars - b >= sd.MIN_SECTION_BARS:   # a 1-3 bar tail is the file ending
        for s, e in fills:
            if s <= b <= e and e < n_bars:
                b = e
        return b
    cands = [e for (s, e), out in zip(fills, low_out_in_fill)
             if out and n_bars - e >= sd.MIN_SECTION_BARS and n_bars - e <= sd.OUTRO_CAP_BARS]
    return max(cands) if cands else None


def gate_cue_bars(cue_bars, groove, n_bars, lines, fill_end_bars=()):
    """The older boundary signals (energy dips, width collapses) cut a line only
    where the kick is full: not inside a break, the kick-down head or tail, and
    at least MIN_SECTION_BARS from every groove line."""
    kick_down = np.zeros(n_bars, bool)
    for s0, s1 in groove["breaks"]:
        kick_down[s0:s1] = True
    kick_down[:groove["head_end"] or 0] = True
    if groove["tail_start"] is not None:
        kick_down[groove["tail_start"]:] = True
    inside_fill = np.zeros(n_bars, bool)       # a cue AT a fill's end is a line after the fill
    for s0, s1 in groove["fills"]:
        inside_fill[s0 + 1:s1] = True
    out = []
    for b in cue_bars:
        if any(abs(b - e) <= FILL_END_CUE_BARS for e in fill_end_bars):
            continue
        if (0 < b < n_bars and not kick_down[b] and not kick_down[b - 1] and not inside_fill[b]
                and all(abs(b - x) >= sd.MIN_SECTION_BARS for x in list(lines) + out)):
            out.append(b)
    return out


def label_sections(sections, groove, bass_on, outro_start_bar, soft_intro_outro=True,
                   mix_norm=None):
    """Label sections cut by groove_structure.

    outro: from outro_start on, and a kick-down tail after the last drop.
    intro: the kick-down head, and full-kick bars without bass before the
    first drop. break: inside a break run. drop: full kick with bass. A
    full-kick, bass-less stretch between drops is a build if a drop follows,
    else drop material. Every track starts with an intro. Same-label
    neighbours are NOT merged: every line is a deliberate line.

    R4 (Sam's soft rule): when the track opens with a kick and a head quieter
    than the drops, the FIRST break before the first drop is still intro.
    (R2, a kick-less head extending the intro, needs no rule here: a kick-down
    head is one intro section by construction.)
    """
    full_bar = groove["full_bar"]
    head_end = groove["head_end"] or 0
    in_break = np.zeros(len(full_bar), bool)
    for s, e in groove["breaks"]:
        in_break[s:e] = True
    raw = []
    for sec in sections:
        s0, s1 = sec["start_bar"], sec["end_bar"]
        if outro_start_bar is not None and s0 >= outro_start_bar:
            raw.append("outro")
        elif s1 <= head_end:
            raw.append("intro")
        elif in_break[s0:s1].mean() > 0.5:
            raw.append("break")
        elif groove["tail_start"] is not None and s0 >= groove["tail_start"]:
            raw.append("tail")
        elif np.asarray(bass_on[s0:s1], float).mean() >= 0.5:
            raw.append("drop")
        else:
            raw.append("nobass")
    first_drop = next((i for i, lab in enumerate(raw) if lab == "drop"), None)
    last_drop = max((i for i, lab in enumerate(raw) if lab == "drop"), default=None)
    for i, lab in enumerate(raw):
        if lab == "nobass":
            if first_drop is None or i < first_drop:
                lab = "intro"
            elif last_drop is not None and i > last_drop:
                lab = "outro"
            else:   # you build INTO a drop; elsewhere a bass-less stretch is drop material
                lab = "build" if i + 1 < len(raw) and raw[i + 1] == "drop" else "drop"
        elif lab == "tail":
            lab = "outro" if last_drop is None or i > last_drop else "break"
        sections[i]["label"] = lab
    if sections:
        sections[0]["label"] = "intro"

    def energy(sec):
        s0, s1 = sec["start_bar"], sec["end_bar"]
        return float(np.mean(mix_norm[s0:s1])) if s1 > s0 else 0.0
    head_quiet = (mix_norm is not None and len(sections) > 2
                  and energy(sections[0]) < sd.DROP_REL * max(energy(x) for x in sections))
    if soft_intro_outro and head_quiet and groove["head_end"] is None:
        first_drop = next((i for i in range(1, len(sections)) if sections[i]["label"] == "drop"), None)
        for i in range(1, first_drop or 0):
            if sections[i]["label"] == "break":
                if all(sections[j]["label"] == "intro" for j in range(i)):
                    sections[i]["label"] = "intro"
                break
    counts = {}
    for sec in sections:
        counts[sec["label"]] = counts.get(sec["label"], 0) + 1
        sec["name"] = f"{sec['label']}_{counts[sec['label']]}"


def bar_one_report(mix_env, hop_t, bpm, downbeat, full_beat):
    """Measure whether bar one looks wrong. Never changes anything.

    Two pieces of evidence: leading silent beats before the first full kick,
    and the phase of every full-kick return (the first full beat after two or
    more non-full beats). When most returns sit on the same off-bar beat, the
    grid is a fractional bar away from the music (Call 911: every return on
    beat 3, half a bar)."""
    fb = np.asarray(full_beat, bool)
    n_beats = len(fb)
    peaks = per_beat(mix_env, hop_t, bpm, downbeat, n_beats, how="max")
    ref = float(np.median(peaks)) + 1e-12 if n_beats else 1.0
    silent = 0
    while silent < n_beats and peaks[silent] < SILENT_BEAT_FRAC * ref:
        silent += 1
    first_full = int(np.argmax(fb)) if fb.any() else None
    phases = [0, 0, 0, 0]
    run = 0
    for k in range(n_beats):
        if fb[k]:
            if run >= 2:
                phases[k % 4] += 1
            run = 0
        else:
            run += 1
    total = sum(phases)
    best = int(np.argmax(phases))
    offset_beats = None
    if total >= BAR_ONE_MIN_RETURNS and best != 0 and phases[best] >= BAR_ONE_PHASE_SHARE * total:
        offset_beats = best
    lead_off = (first_full is not None and silent > 0 and first_full == silent and silent % 4 != 0)
    return {
        "suspect": bool(offset_beats is not None or lead_off),
        "leading_silent_beats": int(silent),
        "first_full_kick_beat": first_full,
        "kick_return_phases": phases,
        "offset_beats": offset_beats if offset_beats is not None else (silent % 4 if lead_off else 0),
        "offset_bars": round((offset_beats if offset_beats is not None else (silent % 4 if lead_off else 0)) / 4.0, 2),
        "evidence": ("kick returns on one off-bar beat" if offset_beats is not None
                     else "leading silence before the first full kick" if lead_off else "none"),
    }


def groove_detect(*, wav, project, envs, hop_t, bpm, downbeat, sec_per_bar, n_bars, pb,
                  presence, mix_norm, kick_on, kick_peaks, kick_ref, energy_cues, wcues,
                  width_cues, tier_a, soft_intro_outro, legacy_outro_start):
    """Everything the groove mode adds to detect(). Returns a dict with
    sections, fills (seconds pairs), fill_sections, bass_state, bass_source,
    outro_start, bar_one."""
    n_beats = n_bars * 4
    beat_sec = 60.0 / bpm
    full_beat = full_kick_beats(kick_on, kick_peaks, kick_ref)[:n_beats]
    fb_bar = full_bars(full_beat, n_bars)

    low_env = cached_low_band(wav, project / "_Stem Analysis")
    if low_env is None and (width_cues or tier_a):     # those flags already compute-and-cache it
        low_env = sd.ensure_tier_a_arrays(wav, project / "_Stem Analysis")[sd._TIERA_BAND_LOW_KEY]
    low_bar = sd._per_bar(low_env, hop_t, downbeat, sec_per_bar, n_bars) if low_env is not None else None
    bass_beat = per_beat(envs["bass"], hop_t, bpm, downbeat, n_beats)
    bass_on, bass_source = bass_state_per_bar(bass_beat, low_bar, fb_bar)

    groove = groove_structure(full_beat, bass_on, n_bars)

    if bass_source == "low_band":
        low_ref, low_vals, low_frac = sd._solid_kick_level(low_bar), low_bar, LOW_ON_FRAC
    else:
        low_ref, low_vals, low_frac = sd._solid_kick_level(pb["bass"]), pb["bass"], BASS_ON_FRAC
    low_out = [float(np.mean(low_vals[s0:s1])) < low_frac * low_ref for s0, s1 in groove["fills"]]
    residue = residue_leave_bar(pb["bass"], low_bar) if bass_source == "bass_stem" else None
    o_start = outro_start(bass_on, groove["fills"], n_bars, low_out, residue_bar=residue)
    if residue is not None and o_start is not None and o_start >= residue:
        bass_on = np.asarray(bass_on, bool).copy()
        bass_on[residue:] = False          # a residue is not bass: the strip, bass_out and regions agree
    if o_start is None:
        o_start = legacy_outro_start
    elif n_bars - sd.OUTRO_CAP_BARS > 0 and o_start < n_bars - sd.OUTRO_CAP_BARS:
        o_start = n_bars - sd.OUTRO_CAP_BARS      # Sam 24.06.26: an outro is never more than 32 bars

    # Fill edges, beat-precise (lines are fixed below; fills are cut at them).
    spans = []
    for s0, s1 in groove["fills"]:
        kb, ke = refine_fill_beats(full_beat, s0, s1)
        if (s0, s1) in groove["lead_fills"]:
            ke = 4 * s1
        spans.append([kb, ke])
    for b in one_beat_fills(kick_on, full_beat, n_bars):
        spans.append([4 * b + 3, 4 * b + 4])
    strength = np.asarray(kick_peaks, float)[:n_beats] / (float(kick_ref) + 1e-9)
    runs = list(groove["breaks"]) + ([(0, groove["head_end"])] if groove["head_end"] else [])
    for s0, s1 in runs:
        t = trailing_drop_fill(strength, kick_on, 4 * s0, 4 * s1)
        if t is not None:
            spans.append(list(t))
    fill_end_bars = sorted(set(int(round(ke / 4.0)) for _, ke in spans))

    cands = drop_changes_after_outro(groove["cands"], groove["bass_changes"], o_start, groove["kinds"])
    if o_start is not None:
        if 0 < o_start < n_bars:
            cands[o_start] = max(cands.get(o_start, 0), RANK["outro"])
    lines = keep_strongest(cands, n_bars)
    cue_bars = [int(round(c["bar"])) for c in list(energy_cues) + list(wcues)]
    lines = sorted(set(lines + gate_cue_bars(cue_bars, groove, n_bars, lines, fill_end_bars)))
    m = minute_fill_line(lines, fill_end_bars, downbeat, sec_per_bar)
    if m is not None:
        lines = sorted(set(lines + [m]))
    if o_start is not None and o_start not in lines:
        o_start = min(lines, key=lambda x: abs(x - o_start)) if lines else None

    bounds = [0] + [b for b in lines if 0 < b < n_bars] + [n_bars]
    sections = []
    for s0, s1 in zip(bounds[:-1], bounds[1:]):
        sections.append({
            "start_bar": s0, "end_bar": s1,
            "start_sec": round(downbeat + s0 * sec_per_bar, 2),
            "end_sec": round(downbeat + s1 * sec_per_bar, 2),
            "stems_on": [s for s in sd.STEMS if presence[s][s0:s1].mean() > 0.5],
        })
    label_sections(sections, groove, bass_on, o_start, soft_intro_outro=soft_intro_outro,
                   mix_norm=mix_norm)

    clean = clip_fills_to_lines(spans, lines)
    fill_sections = [{
        "start_bar": round(kb / 4.0, 2), "end_bar": round(ke / 4.0, 2),
        "start_sec": round(downbeat + kb * beat_sec, 2),
        "end_sec": round(downbeat + ke * beat_sec, 2),
    } for kb, ke in clean]

    return {
        "sections": sections,
        "fills": [[f["start_sec"], f["end_sec"]] for f in fill_sections],
        "fill_sections": fill_sections,
        "bass_state": np.asarray(bass_on, bool),
        "bass_source": bass_source,
        "outro_start": o_start,
        "bar_one": bar_one_report(envs["mix"], hop_t, bpm, downbeat, full_beat),
    }
