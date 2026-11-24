"""Automatic grid phase fix for a finished Ableton set: a drift fit per track, then a slide per track, both measured by the kick check.

Why: on 2026-11-23 the kick check (kick_alignment_check.py) found a flam in Get Right into No Panties and No Panties
into Taste. Sam cleared it by hand with two moves and said the result was "as tight as I would get it manually":
  1. Get Right's grid drifted (its kicks sat 2 ms early at the start and 6 ms early at the end), so each of its warp
     markers was re-fitted to the measured kick offsets.
  2. No Panties has a slow-attack kick, so one slide of all its markers (+5 ms) balanced its two blends.
This module does both moves itself, on a copy of the ALS, and measures the copy with the kick check.

Sign: a correction or slide of +x ms is added to the SecTime of a warp marker, so that track's kicks land x ms EARLIER
in the arrangement. For the blend between outgoing track k and incoming track k+1 (lag = incoming minus outgoing, as the
kick check has it) the lag changes exactly additively: lag_k = L_k + s_k - s_(k+1). One measurement run therefore
predicts the lag of every blend for any set of corrections and slides.

Steps (see run):
  1. kick check on the input (before);
  2. drift fit per track: the leading-edge click offset on every kick beat the track's clips use, a running median then
     a moving average (flat at the ends); the correction is that offset (kick minus grid, positive = kick late);
  3. blend lags after the virtual drift correction, computed from the measured per-beat lags;
  4. slide solve in two stages: the best worst lag t* any slides could reach, then the least total slide that keeps every
     blend within max(t*, EPS_MS), |s_i| <= MAX_SLIDE_MS;
  5. write a NEW ALS (text patch of the SecTime of WarpMarker lines only), run the kick check on it (after), refuse
     the result when the patch changed nothing or would write a negative SecTime, when any judged blend got worse (by
     more than REFUSE_TOL_MS) or a blend became FAIL, or when a re-measured blend lag differs from the additive
     prediction by more than PREDICT_TOL_MS.
A track that sits in any blend the kick check could not judge is neither slid nor drift-corrected (nothing could check
the move), and a track whose clips carry fewer than one warp marker per 4 source beats is slid but never drift-corrected
(a correction would land on the few marker beats only).
The input is only read. Windows of 2 s are read around each beat, never a whole wav.
The phased file is a suggestion: the MixPlan reconciliation pins the original grid (warp_grid_sha256, source_grid_bpm), so
it covers the unphased original only.
OPEN: apply mode needs the MixPlan reconciliation to accept a recorded grid phase (hash of the original grid plus the
applied corrections)
"""
from __future__ import annotations

import gzip
import json
import math
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import optimize

import kick_alignment_check as kac

# Decision figures: starting figures, Sam sets the real ones (2026-11-23).
WARN_MS = kac.CLICK_WARN_MS      # a mix whose worst judged lag is under this is left alone
# MAX_DRIFT_MS and OUTLIER_MS are separate cuts: a kick beat whose offset is OUTLIER_MS or more from its grid is dropped
# from the fit (it is another sound, not a drifting kick); a fitted correction beyond MAX_DRIFT_MS is refused for the
# track (a grid that far out is more likely a wrong grid or a wrong kick than a drift to repair). OUTLIER_MS is the
# larger, so the refusal is reachable by a track whose kicks all sit 12 to 15 ms from the grid.
MAX_DRIFT_MS = 12.0
MAX_SLIDE_MS = 15.0              # one track is never slid further than this
# EPS_MS: the kick check's lag has a resolution of about 1 to 3 ms (it is a median of cross-correlations of short
# windows, and a re-measure after a shift differed from the additive prediction by 1.1 ms on the real mix), so chasing a
# lag below this is chasing noise. The slide solve brings every blend inside max(t*, EPS_MS) with the least total slide,
# instead of paying slides to shave the last millisecond.
EPS_MS = 1.5
# DRIFT_MIN_MS: a fitted correction smaller than this, in size AND in swing, is noise, not drift: the kick check cannot
# see a few ms (see EPS_MS), and applying such a wobble only adds noise (on the real mix Taste's fit stayed within 1.9 ms,
# swung 3.7 ms, and applying it pushed a good blend 1.2 ms out). The size is max(|largest correction|, high minus low),
# so a track swinging -3.9 to +2.6 (6.5 ms) is drift although neither end reaches 4 ms. Its own 4 ms, not the click warn figure (2.5);
# Get Right, which really drifts, reaches 6.1 ms. A constant offset this large is corrected (the markers are late or early).
DRIFT_MIN_MS = 4.0   # its own figure, not the click warn figure: a noise rule, not a verdict
# MAX_SWING_MS: a fit whose high minus low exceeds this is refused: a mid-track excursion that large is a change of kick
# sound (a different kick, a layered hit), not a grid drifting. Killa (-9.2 to +3.0, a swing of 12.2) passes. A starting
# figure, like the others.
MAX_SWING_MS = 14.0
EDGE_MAIN_WORSE_MS = 4.0         # an edge or main hit lag that got worse by more than this is reported (its own figure)
# REFUSE_TOL_MS: a judged blend counts as worse only when its worst click lag grew by more than this. The kick check's
# re-measure of a shifted blend differs from the additive prediction by 0.2 to 1.1 ms on the real mixes (shifted windows
# hold slightly different content), so a smaller change is not a worse blend.
REFUSE_TOL_MS = 0.4
# PREDICT_TOL_MS: the re-measured median click lag of every judged blend must be within this of the lag the model
# predicted, or the written file is not the file the solve meant (a patch that missed markers, a wrong track).
PREDICT_TOL_MS = 2.5
MIN_MARKER_BEATS = 4             # fewer than one warp marker per this many source beats of a clip: SPARSE_MARKERS
MIN_FIT_KICKS = 40               # fewer usable kick beats than this: the track is CANNOT_FIT and gets no correction
OUTLIER_MS = 15.0                # a kick beat whose offset is this far from its grid is dropped from the fit (see MAX_DRIFT_MS)
MEDIAN_HALF_WINDOW = 48.0        # running median: this many beats each side ...
MEDIAN_MIN_KICKS = 8             # ... widened (doubled) until at least this many kick beats are inside
SMOOTH_BEATS = 31                # moving average over the median, flat at both ends
SLIDE_NOISE_MS = 0.05            # a slide smaller than this is a rounding residue of the solver, not a slide

FIT, CANNOT_FIT, REFUSED, NOT_MEASURED, NO_DRIFT, SPARSE_MARKERS = (
    "FIT", "CANNOT_FIT", "REFUSED", "NOT_MEASURED", "NO_DRIFT", "SPARSE_MARKERS")
PHASED, NOTHING, REFUSED_RESULT = "PHASED", "NOTHING_TO_DO", "REFUSED"


# ------------------------------------------------------------------ the drift fit

@dataclass
class DriftFit:
    status: str                  # FIT, NO_DRIFT, CANNOT_FIT, REFUSED, SPARSE_MARKERS or NOT_MEASURED (not read: nothing
                                 # needed fixing, or the track is not moved)
    kicks: int = 0
    first_beat: float = 0.0
    corr: np.ndarray = field(default_factory=lambda: np.zeros(0))   # ms, one per integer source beat from first_beat
    note: str = ""

    def at(self, beat):
        """Correction (ms) at a source beat; flat beyond the measured beats."""
        if self.status != FIT or not len(self.corr):
            return 0.0
        return float(np.interp(beat, self.first_beat + np.arange(len(self.corr)), self.corr))

    @property
    def low(self):
        return float(self.corr.min()) if len(self.corr) else 0.0

    @property
    def high(self):
        return float(self.corr.max()) if len(self.corr) else 0.0


def measure_offsets(track, readers):
    """(source beat, leading-edge click offset in ms) for every kick beat of the source beats the track's clips use.

    Offset = kick minus grid, positive = the kick is late. The kick test is the kick check's own (a clear 40-120 Hz
    peak, a sharp >150 Hz attack, a body at least BODY_FRACTION of the typical kick of the track). Clips the kick
    check does not map (loop on, StartRelative, unwarped) and clips without audio are not read. The beat label is the
    position's own source beat, the same one the blend lags are keyed by. Every beat is read: a thinner sampling
    (every 4th beat) moved Get Right's fit by 0.45 ms and the fit of sparse-kick tracks by up to 9 ms."""
    rows, seen = [], set()
    for clip in track.clips:
        if clip.audio is None or clip.loop_on or clip.start_relative or not clip.warped:
            continue
        reader = readers[str(clip.audio)]
        single = kac.Track(track.name, [clip])
        for source_beat in range(math.ceil(clip.loop_start), math.ceil(clip.loop_start + clip.end - clip.start)):
            pos = kac.source_position(single, clip.start + (source_beat - clip.loop_start))
            key = (str(clip.audio), round(pos.sec, 6)) if pos else None
            if pos is None or key in seen:
                continue
            seen.add(key)
            got = reader.window(pos.sec)
            if got is not None:
                w = kac.analyse_window(got[0], reader.sr, got[1], edge_only=True)
                rows.append((pos.source_beat, w))
    bodies = [w.body for _, w in rows if w.candidate]
    floor = kac.BODY_FRACTION * float(np.median(bodies)) if bodies else math.inf
    return [(b, w.edge_ms) for b, w in rows if w.candidate and w.body >= floor and not math.isnan(w.edge_ms)]


def fit_drift(offsets):
    """Correction per integer source beat from the measured (source beat, offset ms) pairs of one track."""
    kept = [(b, o) for b, o in offsets if abs(o) < OUTLIER_MS]
    if len(kept) < MIN_FIT_KICKS:
        return DriftFit(CANNOT_FIT, len(kept), note=f"{len(kept)} usable kick beats, need {MIN_FIT_KICKS}")
    beats = np.array([b for b, _ in kept], dtype=float)
    offs = np.array([o for _, o in kept], dtype=float)
    first, last = math.floor(beats.min()), math.ceil(beats.max())
    grid = np.arange(first, last + 1, dtype=float)
    raw = np.empty(len(grid))
    for i, beat in enumerate(grid):
        half = MEDIAN_HALF_WINDOW
        while True:
            inside = np.abs(beats - beat) <= half
            if inside.sum() >= MEDIAN_MIN_KICKS or half > last - first + 1:
                break
            half *= 2
        raw[i] = np.median(offs[inside])
    pad = SMOOTH_BEATS // 2
    smooth = np.convolve(np.pad(raw, (pad, pad), mode="edge"), np.ones(SMOOTH_BEATS) / SMOOTH_BEATS, mode="valid")
    fit = DriftFit(FIT, len(kept), float(first), smooth)
    largest, swing = max(abs(fit.low), abs(fit.high)), fit.high - fit.low
    if largest > MAX_DRIFT_MS:
        fit.status = REFUSED
        fit.note = f"correction reaches {largest:.1f} ms, more than {MAX_DRIFT_MS:g} ms"
    elif swing > MAX_SWING_MS:
        fit.status = REFUSED
        fit.note = f"correction swings {swing:.1f} ms, more than {MAX_SWING_MS:g} ms: a change of kick sound, not drift"
    elif max(largest, swing) < DRIFT_MIN_MS:
        fit.status = NO_DRIFT
        fit.note = (f"correction stays within {largest:.1f} ms and swings {swing:.1f} ms, under {DRIFT_MIN_MS:g} ms: "
                    f"noise, not drift")
    return fit


def sparse_markers(track):
    """True when a clip of the track carries fewer than one warp marker per MIN_MARKER_BEATS source beats of the part it
    uses (a hand re-warp with two markers): a drift correction would land on those few marker beats only."""
    for clip in track.clips:
        low, high = clip.loop_start, clip.loop_start + (clip.end - clip.start)
        inside = int(((clip.markers[:, 0] >= low) & (clip.markers[:, 0] <= high)).sum()) if len(clip.markers) else 0
        if inside < (high - low) / MIN_MARKER_BEATS:
            return True
    return False


# ------------------------------------------------------------------ blend lags, computed not re-measured

@dataclass
class Blend:
    index: int                   # the kick check's transition number (1-based)
    out: int                     # position of the outgoing track in the track list
    inc: int
    middle: float
    beats: np.ndarray            # arrangement beats with a click lag
    lags: np.ndarray             # click lag of each (ms, incoming minus outgoing)
    src_out: np.ndarray          # source beat of the outgoing track at each of those beats
    src_in: np.ndarray

    def summary(self, c_out, c_in, s_out=0.0, s_in=0.0):
        """Median click lag and the medians of the two halves, after corrections c (functions of the source beat, ms)
        and slides s of the two tracks."""
        shifted = (self.lags + np.array([c_out(b) for b in self.src_out]) - np.array([c_in(b) for b in self.src_in])
                   + s_out - s_in)
        first, second = self.beats < self.middle, self.beats >= self.middle
        return dict(median=float(np.median(shifted)),
                    first_half=float(np.median(shifted[first])) if first.sum() >= kac.MIN_ESTIMATOR_BEATS else None,
                    second_half=float(np.median(shifted[second])) if second.sum() >= kac.MIN_ESTIMATOR_BEATS else None)


def usable(result):
    """A blend gives a constraint only when the kick check judged it and has a click lag for it (CANNOT_JUDGE gives none,
    and OFF_GRID means the kicks are on different beats, so there is no lag to speak of)."""
    return (result.verdict != "CANNOT_JUDGE" and "OFF_GRID" not in result.flags
            and result.click.get("median") is not None and result.click.get("n", 0) >= kac.MIN_ESTIMATOR_BEATS
            and result.shared_kicks >= kac.MIN_SHARED_KICKS)


def worst_click(result):
    """The worst |click lag| of a blend: the median or either half, whichever is largest."""
    st = result.click
    vals = [abs(st["median"])] if st.get("median") is not None else []
    for half in ("first_half", "second_half"):
        if st.get(half) is not None and st.get("n_" + half, 0) >= kac.MIN_ESTIMATOR_BEATS:
            vals.append(abs(st[half]))
    return max(vals) if vals else math.nan


def build_blends(results, transitions, tracks):
    position = {id(t): i for i, t in enumerate(tracks)}
    blends = []
    for res, tr in zip(results, transitions):
        if not usable(res):
            continue
        rows = [r for r in res.beats if r.shared and not math.isnan(r.click_lag)]
        src_out = [kac.source_position(tr.outgoing, float(r.beat)).source_beat for r in rows]
        src_in = [kac.source_position(tr.incoming, float(r.beat)).source_beat for r in rows]
        blends.append(Blend(res.index, position[id(tr.outgoing)], position[id(tr.incoming)], (tr.start + tr.end) / 2.0,
                            np.array([r.beat for r in rows], dtype=float), np.array([r.click_lag for r in rows]),
                            np.array(src_out), np.array(src_in)))
    return blends


def needs_fixing(summary):
    """A blend needs fixing when its median click lag is WARN_MS or more, or either half of it is at the kick check's
    CLICK_BEAT_FLAM_MS or more (a drift flam). A blend under both is left alone however far it is from zero."""
    halves = (summary.get("first_half"), summary.get("second_half"))
    return abs(summary["median"]) >= WARN_MS or any(h is not None and abs(h) >= kac.CLICK_BEAT_FLAM_MS for h in halves)


# ------------------------------------------------------------------ the slide solve

def _linprog(cost, rows, rhs, bounds):
    sol = optimize.linprog(cost, A_ub=np.array(rows), b_ub=np.array(rhs), bounds=bounds, method="highs")
    if not sol.success:
        raise RuntimeError("slide solve failed: " + str(sol.message))
    return sol


def solve_slides(lags, pairs, n_tracks, movable=None, was=None, must_fix=None, headroom=None):
    """Slides (ms, one per track) and the worst lag left, in two stages.

    lags[k] is the lag of blend k with no slide (after any drift correction), pairs[k] = (outgoing position, incoming
    position); the lag of blend k after slides is lags[k] + s_out - s_in. Only tracks in `movable` (default: every track
    that sits in a blend) may slide, each within MAX_SLIDE_MS.
      `was[k]` (optional) is the |lag| blend k had in the input, `must_fix[k]` whether it needed fixing (default: its
      was is WARN_MS or more; with no `was` every blend must be fixed).
      do nothing   when the worst lag is under WARN_MS and no blend is further out than max(was, EPS_MS): a good mix is
                   never touched. A blend that was fine and that a drift correction pushed out is NOT left: it is repaired.
      stage 1      minimise t = max |lag| over the blends that must be fixed, with no cost on the slides: the best any
                   slides could do, t*. The blends that did not need fixing are held, meanwhile and in stage 2, to what
                   they were plus REFUSE_TOL_MS (which the refusal rule allows) and never above max(was, EPS_MS): not
                   merely to t*, which can be large. A fix that cannot be had without worsening them is not made. If even
                   those holds cannot be met within the slide limits they are dropped.
      stage 2      eps = max(t*, EPS_MS); minimise the total slide sum |s_i| with every blend that must be fixed inside
                   eps and every other blend inside its hold. The least total slide often has several answers (+4.2 on
                   one track, or +2.6 and -1.6 on two); the tie goes to the answer that weighs the tracks 1, 2, 3 ... by
                   their order in the mix, so the slide sits on one track and the earlier track takes it. A tie on
                   "smallest largest slide" was tried first and split Sam's case over two tracks.
      `headroom[i]` (optional) is how far track i may slide EARLIER-in-the-file (negative) before one of its warp markers
      would get a negative SecTime: its smallest SecTime in ms, after any drift correction.
      Deterministic: HiGHS simplex, no seed."""
    s = np.zeros(n_tracks)
    if not len(lags):
        return s, 0.0
    lags = np.asarray(lags, dtype=float)
    worst = float(np.max(np.abs(lags)))
    if worst < WARN_MS and (was is None or all(abs(l) <= max(w, EPS_MS) for l, w in zip(lags, was))):
        return s, worst
    n, count = n_tracks, len(lags)
    fix = [True] * count if was is None else ([w >= WARN_MS for w in was] if must_fix is None else list(must_fix))
    hold = [None if fix[k] else min(max(was[k], EPS_MS), was[k] + REFUSE_TOL_MS) for k in range(count)]
    can_slide = {i for pair in pairs for i in pair} if movable is None else set(movable)
    width = 2 * n + 1                                        # s_0..s_n-1, u_0..u_n-1 (u_i >= |s_i|), z
    low = [-MAX_SLIDE_MS if headroom is None else max(-MAX_SLIDE_MS, -float(headroom[i])) for i in range(n)]
    reach = [(low[i], MAX_SLIDE_MS) if i in can_slide else (0.0, 0.0) for i in range(n)]
    free_u = [(0.0, None)] * n

    def lag_rows(limits):
        """|lag_k| <= limits[k] (a number), or <= z when limits[k] is None."""
        rows, rhs = [], []
        for k, (lag, (a, b)) in enumerate(zip(lags, pairs)):
            up, down = np.zeros(width), np.zeros(width)
            up[a], up[b], down[a], down[b] = 1.0, -1.0, -1.0, 1.0       # lag + s_a - s_b, and its negative
            if limits[k] is None:
                up[-1] = down[-1] = -1.0
            rows += [up, down]
            rhs += [-lag + (limits[k] or 0.0), lag + (limits[k] or 0.0)]
        return rows, rhs

    def abs_rows():
        """u_i >= |s_i|."""
        rows = []
        for i in range(n):
            for sign in (1.0, -1.0):
                row = np.zeros(width)
                row[i], row[n + i] = sign, -1.0
                rows.append(row)
        return rows, [0.0] * len(rows)

    def cost(u=0.0, z=0.0):
        c = np.zeros(width)
        c[n:2 * n] = u
        c[-1] = z
        return c

    # stage 1: the best worst lag of the blends that must be fixed, the others held
    try:
        rows, rhs = lag_rows(hold)
        sol = _linprog(cost(z=1.0), rows, rhs, reach + free_u + [(0.0, None)])
    except RuntimeError:                                     # the holds cannot all be met within the slide limits
        hold = [None] * count
        fix = [True] * count
        rows, rhs = lag_rows(hold)
        sol = _linprog(cost(z=1.0), rows, rhs, reach + free_u + [(0.0, None)])
    eps = max(float(sol.x[-1]), EPS_MS) + 1e-7
    # stage 2a: the least total slide inside the limits
    more, zero = abs_rows()
    limits = [eps if fix[k] else hold[k] + 1e-7 for k in range(count)]
    rows, rhs = lag_rows(limits)
    rows, rhs = rows + more, rhs + zero
    total = float(_linprog(cost(u=1.0), rows, rhs, reach + free_u + [(0.0, 0.0)]).fun)
    cap = np.zeros(width)
    cap[n:2 * n] = 1.0
    rows, rhs = rows + [cap], rhs + [total + 1e-7]           # from here on, no more total slide than that
    # stage 2b: among solutions with that total, the slide on the lowest track indices
    sol = _linprog(cost(u=np.arange(1.0, n + 1.0)), rows, rhs, reach + free_u + [(0.0, 0.0)])
    s = np.where(np.abs(sol.x[:n]) < SLIDE_NOISE_MS, 0.0, np.round(sol.x[:n], 3))
    left = max(abs(lag + s[a] - s[b]) for lag, (a, b) in zip(lags, pairs))
    return s, float(left)


# ------------------------------------------------------------------ the ALS text patch

_SEC = re.compile(r'SecTime="([^"]+)"')
_BEAT = re.compile(r'BeatTime="([^"]+)"')


def _lines(text):
    """Split on newlines only, keeping each line ending (CRLF stays CRLF)."""
    return re.findall(r"[^\n]*\n|[^\n]+", text)


class AlsLayoutError(ValueError):
    """The text patch and the XML disagree about which track is which (or the file is not laid out one element per line)."""


class NegativeSecTime(ValueError):
    """A patch would write a SecTime below zero (a marker at the very start of the file, moved earlier)."""

    def __init__(self, doc_index, beat, value):
        super().__init__(f"marker at beat {beat:g} would get SecTime {value:.6f}")
        self.doc_index, self.beat, self.value = doc_index, beat, value


def patch_als(src, dst, shifts):
    """Copy src to dst with the SecTime of the WarpMarker lines of the chosen tracks moved by shifts[doc_index](beat) ms.

    Text patch of the lines only: nothing else is parsed or written back, line endings are kept, floats are written
    in the shortest positional form that round-trips (never with an exponent). A marker whose shift is exactly 0 is not touched. Returns the markers changed per track, keyed by the track's position in the file (doc_index).
    Raises NegativeSecTime rather than write a SecTime below zero."""
    with gzip.open(src, "rb") as fh:
        text = fh.read().decode("utf-8")
    lines = _lines(text)
    in_text = sum(1 for line in lines if line.lstrip().startswith("<AudioTrack "))
    in_xml = sum(1 for _ in ET.fromstring(text).iter("AudioTrack"))
    if in_text != in_xml:                # doc_index counts the XML elements, the patch counts these lines: they must agree
        raise AlsLayoutError(f"the text patch finds {in_text} '<AudioTrack ' lines but the document has {in_xml} "
                             f"AudioTrack elements (is the ALS laid out one element per line, as Live writes it?)")
    track_no, current, in_markers, changed = -1, None, False, {}
    for i, line in enumerate(lines):
        bare = line.lstrip()
        if bare.startswith("<AudioTrack "):
            track_no += 1
            current = shifts.get(track_no)
        elif bare.startswith("</AudioTrack>"):
            current, in_markers = None, False
        elif current is None:
            continue
        elif bare.startswith("<WarpMarkers>"):
            in_markers = True
        elif bare.startswith("</WarpMarkers>"):
            in_markers = False
        elif in_markers and bare.startswith("<WarpMarker "):
            sec, beat = _SEC.search(line), _BEAT.search(line)
            move = current(float(beat.group(1)))
            if move == 0.0:
                continue
            value = float(sec.group(1)) + move / 1000.0
            if value < 0.0:
                raise NegativeSecTime(track_no, float(beat.group(1)), value)
            lines[i] = line[:sec.start(1)] + np.format_float_positional(value, unique=True, trim="-") + line[sec.end(1):]
            changed[track_no] = changed.get(track_no, 0) + 1
    with open(dst, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as fh:
            fh.write("".join(lines).encode("utf-8"))
    return changed


# ------------------------------------------------------------------ the whole run

@dataclass
class TrackReport:
    name: str
    short: str
    fit: DriftFit
    applied: bool = False
    slide: float = 0.0
    doc_index: int = -1
    note: str = ""                                    # why the track was not moved, when it was not


@dataclass
class Outcome:
    status: str                  # PHASED, NOTHING_TO_DO or REFUSED
    reason: str = ""
    before: list = field(default_factory=list)       # kick check TransitionResults of the input
    after: list = field(default_factory=list)        # ... of the output (empty when nothing was written)
    tracks: list = field(default_factory=list)       # TrackReport per track, in track order
    t_predicted: float | None = None                 # worst median click lag the solve expects
    predicted: dict = field(default_factory=dict)    # transition number -> median click lag the model expects after
    out_path: str = ""
    refused_path: str = ""
    refusals: list = field(default_factory=list)
    changed: dict = field(default_factory=dict)      # markers patched, by the track's position in the file (doc_index)
    still_flamming: list = field(default_factory=list)    # blends whose predicted half is still at the FAIL figure or more
    edge_main_worse: list = field(default_factory=list)   # judged blends whose edge or main hit got worse (see edge_main_worse)


def _open_readers(tracks, als_path, audio_dir, wanted):
    als_dir = Path(als_path).resolve().parent
    readers = {}
    for index in wanted:
        for clip in tracks[index].clips:
            clip.audio = kac.resolve_audio(clip, als_dir, audio_dir)
            if clip.audio is not None and str(clip.audio) not in readers:
                readers[str(clip.audio)] = kac.WavReader(clip.audio)
    return readers


def refusals(before, after):
    """Why the result must not be kept: a judged blend whose worst |click lag| (median or either half) got worse by
    more than REFUSE_TOL_MS, one that can no longer be judged, or a blend that is FAIL now and was not before.
    Blends are matched by transition number, and a different number of transitions is itself a refusal. (One way only:
    a blend that could not be judged before and can be now is not compared, there is nothing to compare it with.)"""
    if len(before) != len(after):
        return [f"the kick check found {len(before)} transitions before and {len(after)} after"]
    later = {a.index: a for a in after}
    out = []
    for b in before:
        a = later.get(b.index)
        if a is None:
            out.append(f"{b.label}: not in the result")
            continue
        name = b.label
        if a.verdict == "FAIL" and b.verdict != "FAIL":
            out.append(f"{name}: {b.verdict} before, FAIL after")
        if usable(b):
            if not usable(a):
                out.append(f"{name}: judged before, {a.verdict} after")
            elif worst_click(a) > worst_click(b) + REFUSE_TOL_MS:
                out.append(f"{name}: worst click lag {worst_click(b):.2f} ms before, {worst_click(a):.2f} ms after")
    return out


def prediction_refusals(blends, predicted, after):
    """The file that was written must be the file the solve meant: every judged blend's re-measured median click lag
    is within PREDICT_TOL_MS of the lag predicted for it."""
    out = []
    for blend in blends:
        res = after[blend.index - 1]
        if usable(res) and blend.index in predicted and abs(res.click["median"] - predicted[blend.index]) > PREDICT_TOL_MS:
            out.append(f"{res.label}: predicted click lag {predicted[blend.index]:+.2f} ms, measured "
                       f"{res.click['median']:+.2f} ms (more than {PREDICT_TOL_MS:g} ms apart)")
    return out


def edge_main_worse(before, after):
    """Judged blends whose edge or main hit lag got worse by more than EDGE_MAIN_WORSE_MS. Reported, never a reason to refuse: the
    solve aligns the click (as the ear chose for the Tech House mix), and the other two are allowed to differ."""
    found = []
    for b, a in zip(before, after):
        if not (usable(b) and usable(a)):
            continue
        for kind in ("edge", "main"):
            was, now = getattr(b, kind).get("median"), getattr(a, kind).get("median")
            if was is not None and now is not None and abs(now) - abs(was) > EDGE_MAIN_WORSE_MS:
                found.append(dict(label=b.label, estimator=kind, before=was, after=now))
    return found


def still_flamming(blends, fits, slides):
    """Blends in which a half is still at FAIL_MS or more after the corrections and slides the solve chose (the solve
    works on the median only, so a drift it cannot fit leaves its halves apart)."""
    found = []
    for b in blends:
        s = b.summary(fits[b.out].at, fits[b.inc].at, slides[b.out], slides[b.inc])
        for half in ("first_half", "second_half"):
            if s[half] is not None and abs(s[half]) >= kac.CLICK_BEAT_FLAM_MS:
                found.append(dict(index=b.index, half=half, predicted=s[half]))
    return found


def run(als_path, audio_dir, out_path, report_dir, progress=lambda text: None):
    """Fix the grid phase of als_path into out_path (a new file) and measure the new file with the kick check."""
    als_path, out_path = Path(als_path), Path(out_path)
    if out_path.resolve() == als_path.resolve():
        raise ValueError("the output must be a new file, never the input")
    refused_path = out_path.with_name(out_path.stem + " REFUSED.als")
    partial = out_path.with_name(out_path.name + ".partial")
    for old in (out_path, refused_path, partial):      # nothing from an earlier run may pass for this run's result
        old.unlink(missing_ok=True)
    tracks = kac.read_als(als_path)
    transitions = kac.find_transitions(tracks)
    progress("kick check on the input")
    before = kac.check_als(als_path, audio_dir)
    outcome = Outcome(NOTHING, before=before, out_path=str(out_path))
    blends = build_blends(before, transitions, tracks)
    in_transition = sorted({tracks.index(t) for tr in transitions for t in (tr.outgoing, tr.incoming)})
    outcome.tracks = [TrackReport(tracks[i].name, kac.short_name(tracks[i].name), DriftFit(NOT_MEASURED),
                                  doc_index=tracks[i].doc_index) for i in range(len(tracks))]

    def nothing(reason):
        outcome.status, outcome.reason = NOTHING, reason
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(als_path, out_path)
        return outcome

    def refuse(reason):
        outcome.status, outcome.reason = REFUSED_RESULT, reason
        if partial.exists():
            partial.replace(refused_path)
            outcome.refused_path = str(refused_path)
        return outcome

    if not blends:
        return nothing("no blend could be judged, so there is nothing to fix")
    zero = lambda beat: 0.0
    untouched = {b.index: b.summary(zero, zero) for b in blends}
    needs_fix = {i for b in blends if needs_fixing(untouched[b.index]) for i in (b.out, b.inc)}
    if not needs_fix:
        return nothing(f"every judged blend is already under {WARN_MS:g} ms (median click lag)")

    # A track in any blend the check could not judge is not moved at all: nothing could verify the move there.
    frozen = {tracks.index(t) for tr, res in zip(transitions, before) if not usable(res) for t in (tr.outgoing, tr.incoming)}
    sparse = {i for i in needs_fix if sparse_markers(tracks[i])}
    for i in frozen:
        outcome.tracks[i].note = "not moved: sits in a blend the check could not judge"
    to_measure = sorted(needs_fix - frozen)              # only these can have a drift correction applied
    readers = _open_readers(tracks, als_path, audio_dir, to_measure)
    try:
        for index in to_measure:
            report = outcome.tracks[index]
            if index in sparse:
                report.fit = DriftFit(SPARSE_MARKERS, note=f"fewer than one warp marker per {MIN_MARKER_BEATS} source beats")
                continue
            progress(f"drift fit: {report.short}")
            report.fit = fit_drift(measure_offsets(tracks[index], readers))
    finally:
        for reader in readers.values():
            reader.close()
    for i, report in enumerate(outcome.tracks):
        report.applied = i in needs_fix and i not in frozen and report.fit.status == FIT
    fits = {i: (outcome.tracks[i].fit if outcome.tracks[i].applied else DriftFit(CANNOT_FIT)) for i in range(len(tracks))}

    # Slides are confined to the blends that need fixing and their neighbours: a track in no such blend stays put, and
    # a blend with no sliding track is not in the problem (its lag cannot change, and chasing a 0.5 ms lag on a blend
    # that is already fine would move tracks for nothing). A blend that touches a sliding track is in the problem, so
    # a slide cannot leave it worse than it was.
    progress("slide solve")
    touching = lambda ids: [b for b in blends if b.out in ids or b.inc in ids]
    movable = {i for b in touching(needs_fix) for i in (b.out, b.inc)} - frozen
    in_play = touching(movable)
    lags = [b.summary(fits[b.out].at, fits[b.inc].at)["median"] for b in in_play]
    was = [abs(untouched[b.index]["median"]) for b in in_play]
    must_fix = [needs_fixing(untouched[b.index]) for b in in_play]
    headroom = [min((sec * 1000.0 + fits[i].at(beat) for clip in tracks[i].clips for beat, sec in clip.markers),
                    default=math.inf) for i in range(len(tracks))]
    slides, outcome.t_predicted = solve_slides(np.array(lags), [(b.out, b.inc) for b in in_play], len(tracks), movable,
                                               was, must_fix, headroom)
    for i, s in enumerate(slides):
        outcome.tracks[i].slide = float(s)
    outcome.predicted = {b.index: b.summary(fits[b.out].at, fits[b.inc].at, slides[b.out], slides[b.inc])["median"]
                         for b in blends}
    outcome.still_flamming = still_flamming(in_play, fits, slides)

    shifts = {}
    for i, report in enumerate(outcome.tracks):
        if report.applied or report.slide != 0.0:
            shifts[tracks[i].doc_index] = (lambda beat, f=fits[i], s=report.slide: f.at(beat) + s)
    if not shifts:
        return nothing("the drift fit and the slide solve found nothing to move")

    names = {r.doc_index: r.short for r in outcome.tracks}
    try:
        outcome.changed = patch_als(als_path, partial, shifts)
    except NegativeSecTime as exc:
        partial.unlink(missing_ok=True)
        return refuse(f"{names[exc.doc_index]}: {exc}; refused rather than write a negative SecTime")
    except AlsLayoutError as exc:
        partial.unlink(missing_ok=True)
        return refuse(str(exc))
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    missed = [names[k] for k in shifts if not outcome.changed.get(k)]
    if missed:
        return refuse("the patch changed no warp marker line on " + ", ".join(missed)
                      + " (is the ALS laid out one element per line, as Live writes it?)")
    try:
        progress("kick check on the result")
        outcome.after = kac.check_als(partial, audio_dir, report_dir)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    outcome.edge_main_worse = edge_main_worse(before, outcome.after)
    outcome.refusals = refusals(before, outcome.after) + prediction_refusals(blends, outcome.predicted, outcome.after)
    if outcome.refusals:
        return refuse("the result is not the improvement that was meant: " + "; ".join(outcome.refusals))
    partial.replace(out_path)
    outcome.status = PHASED
    outcome.reason = "drift corrected and slid" if any(r.slide for r in outcome.tracks) else "drift corrected"
    if outcome.still_flamming:
        blends_left = ", ".join(sorted({f"T{f['index']:02d}" for f in outcome.still_flamming}))
        outcome.reason = f"NOT FIXED, {blends_left} still flamming in a half ({outcome.reason}, but the fix does not reach it)"
    return outcome


# ------------------------------------------------------------------ the report

def _num(v):
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:+.1f}"


PHASED_NOTE = ("grid phase: phased file written, open it instead of {name} if the report shows it is tighter; "
               "the MixPlan reconciliation covers the unphased original (the phased markers differ from the original by "
               "the corrections shown in the report: drift up to 12 ms, slide up to 15 ms)")
EDGE_MAIN_NOTE = "aligned by click, as chosen by ear for this mix: check the picture"


def report_text(outcome, als_path):
    lines = [f"Grid phase fix of {Path(als_path).name}", f"result: {outcome.status}: {outcome.reason}"]
    if outcome.status == PHASED:
        lines.append(PHASED_NOTE.format(name=Path(als_path).name))
    lines.append("")
    lines.append("Transitions, before -> after (ms: positive = the incoming kick is later)")
    lines.append(f"{'':3}{'transition':36}{'verdict':30}{'edge':16}{'click':16}{'main':16}{'click predicted':>16}")
    after = outcome.after or outcome.before
    for b, a in zip(outcome.before, after):
        pair = lambda key: f"{_num(b.__dict__[key].get('median'))} -> {_num(a.__dict__[key].get('median'))}"
        lines.append(f"{'':3}{b.label[:35]:36}{b.verdict + ' -> ' + a.verdict:30}{pair('edge'):16}{pair('click'):16}{pair('main'):16}"
                     f"{_num(outcome.predicted.get(b.index)):>16}")
    lines += ["", "Tracks", f"{'':3}{'track':28}{'drift fit':34}{'correction range (ms)':26}{'applied':9}{'slide (ms)':>10}  note"]
    for r in outcome.tracks:
        fit = f"{r.fit.status} ({r.fit.kicks} kicks)" if r.fit.kicks else r.fit.status
        rng = f"{r.fit.low:+.1f} to {r.fit.high:+.1f}" if r.fit.status == FIT else "-"
        lines.append(f"{'':3}{r.short[:27]:28}{fit:34}{rng:26}{'yes' if r.applied else 'no':9}{r.slide:+10.2f}" + (f"  {r.note}" if r.note else ""))
    if outcome.t_predicted is not None:
        lines += ["", f"worst median click lag the solve expects after the slides: {outcome.t_predicted:.2f} ms"]
    names = {r.doc_index: r.short for r in outcome.tracks}
    for doc_index, count in outcome.changed.items():
        lines.append(f"markers changed on {names.get(doc_index, doc_index)}: {count}")
    for f in outcome.still_flamming:
        lines += ["", f"STILL FLAMMING (half): the fix does not reach it: T{f['index']:02d} {f['half'].replace('_', ' ')} "
                      f"is predicted at {_num(f['predicted'])} ms (FAIL figure {kac.CLICK_BEAT_FLAM_MS:g} ms)"]
    if outcome.edge_main_worse:
        lines += ["", f"Edge or main hit lag got worse by more than {EDGE_MAIN_WORSE_MS:g} ms ({EDGE_MAIN_NOTE}):"]
        for w in outcome.edge_main_worse:
            lines.append(f"   {w['label']}: {w['estimator']} {_num(w['before'])} -> {_num(w['after'])} ms: {EDGE_MAIN_NOTE}")
    if outcome.refused_path:
        lines += ["", f"REFUSED, kept for inspection only: {outcome.refused_path}"]
    return "\n".join(lines) + "\n"


def report_json(outcome):
    return dict(
        status=outcome.status, reason=outcome.reason, t_predicted=outcome.t_predicted, refusals=outcome.refusals,
        predicted={str(k): round(v, 3) for k, v in outcome.predicted.items()},
        changed_markers={str(k): v for k, v in outcome.changed.items()}, out=outcome.out_path,
        refused=outcome.refused_path, edge_main_worse=outcome.edge_main_worse,
        still_flamming=[dict(f, note='STILL FLAMMING (half): the fix does not reach it') for f in outcome.still_flamming],
        tracks=[dict(name=r.name, doc_index=r.doc_index, note=r.note, fit=r.fit.status, kicks=r.fit.kicks,
                     fit_note=r.fit.note, applied=r.applied,
                     slide=r.slide, first_beat=r.fit.first_beat, correction_ms=[round(float(v), 3) for v in r.fit.corr])
                for r in outcome.tracks],
        before=kac.results_json(outcome.before) if outcome.before else [],
        after=kac.results_json(outcome.after) if outcome.after else [])
