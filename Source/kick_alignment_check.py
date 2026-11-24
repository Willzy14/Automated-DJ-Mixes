"""Kick alignment inside every blend of a finished Ableton set, with a picture. Read only.

Why: the older gates judge each track alone against its own fitted kicks (validate_beatgrid.py,
STEM_KF_FAIL_MS) and check_kick_flam ships disabled, so nothing compared the two tracks inside a
blend. On 2026-11-23 Sam heard a flam in Get Right into No Panties while every gate passed. This
module takes the final ALS, finds each transition, and for every arrangement beat where BOTH tracks
carry a kick it measures how far apart the two kicks land, then draws what Sam saw in Ableton.

Sign: a positive lag means the INCOMING track's kick is LATER than the outgoing track's.

Two estimators, reported side by side, because a kick can line up by one and not the other:
  click      lag of the >150 Hz envelopes, found by cross-correlation of the two windows (+-30 ms,
             parabolic sub-sample peak). Per-track click times (ticks on the picture) are the
             leading edge of that envelope: the first point above 10 percent of the click's rise.
  main hit   the steepest rise of the smoothed >40 Hz envelope, per track, then the difference.
             Chosen over "first reaches 50 percent of peak" because the 50 percent time depends on
             how long each kick's body takes to peak, which differs kick to kick and is not the
             attack; the steepest rise is where the energy arrives.
The verdict judges each estimator by its own figures (starting figures Sam set on 2026-11-24 from five listening
comparisons) and takes the worse: click WARN 2.5 ms, FAIL 5.0 ms; main hit WARN 8.0 ms, FAIL 14.0 ms. The leading
edge is reported only and never decides a verdict. A soft-attack kick (clean click, late main hit) has a click lag near
0 and a main-hit lag well away from it: more than 8 ms apart is flagged SLOW_ATTACK; an edge more than 8 ms from the
click is flagged EDGE_DISAGREES (each only raises a PASS to WARN).

Limits (not measured away): lags are in source milliseconds, not stretched milliseconds (the
difference is the warp ratio times the lag, under about 3 percent). Fader, EQ and filter automation
is ignored: a beat still counts when both wavs have a kick there, even if the mix filters one side
out. In a full mix the steepest rise can land on a bass note or a snare instead of the kick, so an
estimator whose beats spread wider than SCATTER_MS (interquartile range) cannot fail a transition
alone and cannot let it pass: the verdict is then at least WARN and the flag says which one.
Not covered: a third track in a three-way overlap is not looked at (only neighbouring pairs are).
The ALS is only read (the pipeline passes --out beside it, in Output; run by hand with no --out the CLI
makes a "Kick Alignment" folder beside the ALS). A beat counts as a kick only when the 40-120 Hz peak is at least 3 times
the median of its window, so 2 s around the beat is read; never a whole file.
"""
from __future__ import annotations

import gc
import gzip
import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import fft, signal

# Decision figures: starting figures Sam set on 2026-11-24 from five listening comparisons.
CLICK_WARN_MS = 2.5
CLICK_FAIL_MS = 5.0
MAIN_WARN_MS = 8.0
MAIN_FAIL_MS = 14.0
MIN_SHARED_KICKS = 16
SLOW_ATTACK_MS = 8.0          # click and main-hit medians further apart than this
EDGE_DISAGREE_MS = 8.0        # leading-edge and click medians further apart than this (the edge never decides a verdict)
SCATTER_MS = 8.0              # an estimator whose beats spread (interquartile range) wider than this is not trusted
MINORITY_FRACTION = 0.20      # this share of the shared beats at an estimator's fail figure or more apart is a flam even if the median is fine
MIN_ESTIMATOR_BEATS = 3       # an estimator with fewer measured beats than this counts as missing
_WARN = {"CLICK": CLICK_WARN_MS, "MAIN": MAIN_WARN_MS}     # each estimator is judged by its own figures
_FAIL = {"CLICK": CLICK_FAIL_MS, "MAIN": MAIN_FAIL_MS}

KICK_RATIO_MIN = 3.0          # 40-120 Hz peak vs the window median (also the >150 Hz click window)
BODY_FRACTION = 0.3           # a kick's 40-120 Hz peak must be this much of the typical kick in the same overlap
ATTACK_JUMP_MIN = 2.0         # >150 Hz envelope after the attack vs just before it
EDGE_FRACTION = 0.10          # leading edge: first point above this fraction of the click rise

READ_BEFORE_S = 1.0           # window read around each beat: 2 s in all (the kick test needs the local median)
READ_AFTER_S = 1.0
CORE_MS = 300.0               # click and main-hit analysis runs on this much each side of the beat
SEARCH_MS = (-35.0, 35.0)     # where the attack and the main hit are looked for, around the beat
MAIN_BEFORE_MS = 5.0          # main hit is looked for from here before the click's steepest rise ...
MAIN_AFTER_MS = 25.0          # ... to here after it
XCORR_REF_MS = (-40.0, 60.0)  # outgoing window used as the cross-correlation reference
XCORR_MAX_LAG_MS = 30.0
GRID_HZ = 44100.0             # common time grid for the cross-correlation

PIC_COLUMNS = 4
PIC_X_MS = (-40.0, 60.0)
PIC_SIZE_IN = (18.0, 6.0)     # at 100 dpi: 1800 by 600
OUT_COLOUR = "#d62f2f"
IN_COLOUR = "#e0489a"

LOW_BAND = (40.0, 120.0)
HIGH_PASS_CLICK = 150.0
HIGH_PASS_BROAD = 40.0


# ------------------------------------------------------------------ the ALS (read only)

@dataclass
class Clip:
    start: float
    end: float
    loop_start: float
    loop_end: float
    loop_on: bool
    start_relative: float
    markers: np.ndarray          # rows of (beat, wav seconds), one per beat
    path: str
    relative_path: str
    wav: str                     # file name only, for messages
    audio: Path | None = None    # the file that will be read, set by check_als
    warped: bool = True

    @property
    def supported(self):
        """Only a plain front-trimmed warped clip is mapped, as render_check.py does: a looped clip, a
        StartRelative offset or an unwarped clip would need a mapping nobody has reviewed, so it is refused."""
        return not self.loop_on and self.start_relative == 0 and self.warped


@dataclass
class Track:
    name: str
    clips: list
    doc_index: int = -1          # position among every AudioTrack of the file, so a text patch can find the track

    @property
    def first_start(self):
        return self.clips[0].start

    @property
    def last_end(self):
        return max(c.end for c in self.clips)


@dataclass
class Transition:
    index: int                   # 1-based
    outgoing: Track
    incoming: Track
    start: float                 # incoming's first clip start, arrangement beats
    end: float                   # outgoing's last clip end


@dataclass
class Position:
    clip: Clip
    source_beat: float
    sec: float                   # seconds into the wav


def _value(node, tag, default=None):
    child = node.find(tag)
    return default if child is None else child.get("Value")


def read_als(path):
    """Audio tracks with arrangement clips, ordered by where they start. Never writes."""
    root = ET.fromstring(gzip.open(path).read())
    tracks = []
    for doc_index, track in enumerate(root.iter("AudioTrack")):
        name = _value(track, "Name/EffectiveName", "")
        events = track.find("DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")
        if events is None:           # session-view clips live in another coordinate space: not read
            continue
        clips = []
        for node in events.findall("AudioClip"):
            loop = node.find("Loop")
            markers = [(float(m.get("BeatTime")), float(m.get("SecTime")))
                       for m in node.find("WarpMarkers")]
            clip_path = _value(node, "SampleRef/FileRef/Path", "")
            clips.append(Clip(
                start=float(_value(node, "CurrentStart")), end=float(_value(node, "CurrentEnd")),
                loop_start=float(_value(loop, "LoopStart")), loop_end=float(_value(loop, "LoopEnd")),
                loop_on=_value(loop, "LoopOn", "false") == "true",
                start_relative=float(_value(loop, "StartRelative", "0")),
                markers=np.array(sorted(markers)), path=clip_path,
                relative_path=_value(node, "SampleRef/FileRef/RelativePath", ""),
                wav=re.split(r"[\\/]", clip_path)[-1],
                warped=_value(node, "IsWarped", "true") != "false"))
        if clips:
            tracks.append(Track(name, sorted(clips, key=lambda c: c.start), doc_index))
    return sorted(tracks, key=lambda t: t.first_start)


def find_transitions(tracks):
    """One transition per neighbouring pair that overlaps: incoming's first clip start to outgoing's end."""
    found = []
    for i in range(len(tracks) - 1):
        out, inc = tracks[i], tracks[i + 1]
        start, end = inc.first_start, out.last_end
        if end > start:
            found.append(Transition(len(found) + 1, out, inc, start, end))
    return found


def source_position(track, beat):
    """Arrangement beat -> clip, source beat and wav seconds; None outside every clip or without two markers.

    Beyond the first or last marker Live keeps the tempo of the nearest segment, so the line is extended
    the same way (a hand re-warp with two markers close together maps the whole clip this way)."""
    for clip in track.clips:
        if clip.start <= beat < clip.end:
            source_beat = clip.loop_start + (beat - clip.start)
            m = clip.markers
            if len(m) < 2:
                return None
            if source_beat < m[0, 0]:
                a, b = m[0], m[1]
            elif source_beat > m[-1, 0]:
                a, b = m[-2], m[-1]
            else:
                return Position(clip, source_beat, float(np.interp(source_beat, m[:, 0], m[:, 1])))
            if b[0] == a[0]:
                return None
            return Position(clip, source_beat, float(a[1] + (source_beat - a[0]) * (b[1] - a[1]) / (b[0] - a[0])))
    return None


_VERSION_TAIL = re.compile(
    r"\s+(?:SW\s*V[\d.]+|(?:24\s*Bit\s+)?MASTER(?:\s+AMENDED(?:\s+V\d+)?)?|AMENDED(?:\s+V\d+)?|V\d+(?:\.\d+)*)\s*$",
    re.IGNORECASE)


def short_name(name):
    """'Jones - Get Right (Extended Mix) SW V6' -> 'Get Right'."""
    s = name.split(" - ", 1)[-1]
    s = re.sub(r"\([^)]*\)", "", s).strip()
    while True:
        t = _VERSION_TAIL.sub("", s).strip()
        if t == s:
            break
        s = t
    s = re.sub(r'[<>:"/\\|?*]', "", s).strip()
    return s or re.sub(r'[<>:"/\\|?*]', "", name).strip() or "track"


# ------------------------------------------------------------------ one window of one track

@lru_cache(maxsize=8)
def _filters(sr):
    return (signal.butter(4, LOW_BAND, btype="bandpass", fs=sr, output="sos"),
            signal.butter(4, HIGH_PASS_CLICK, btype="highpass", fs=sr, output="sos"),
            signal.butter(4, HIGH_PASS_BROAD, btype="highpass", fs=sr, output="sos"))


def _envelope(y):
    n = len(y)
    return np.abs(signal.hilbert(y, N=fft.next_fast_len(n)))[:n]


def _box(y, sr, ms):
    w = max(1, int(round(ms / 1000.0 * sr)))
    return np.convolve(y, np.ones(w) / w, mode="same")


def _parabola(y, k):
    d = y[k - 1] - 2.0 * y[k] + y[k + 1]
    return 0.0 if d == 0 else 0.5 * (y[k - 1] - y[k + 1]) / d


class WavReader:
    """Reads 2 s windows around a beat. Never the whole file."""

    def __init__(self, path):
        self.file = sf.SoundFile(str(path))
        self.sr = self.file.samplerate

    def close(self):
        self.file.close()

    def window(self, sec):
        """Mono samples around `sec`, the time of the first sample relative to `sec` in seconds, and
        whether the window ran past the start or end of the file and was zero-padded (the beat is kept).
        None only when the beat itself is outside the file."""
        sr = self.sr
        centre = int(round(sec * sr))
        if centre < 0 or centre >= self.file.frames:
            return None
        i0 = centre - int(READ_BEFORE_S * sr)
        i1 = centre + int(READ_AFTER_S * sr)
        lo, hi = max(i0, 0), min(i1, self.file.frames)
        self.file.seek(lo)
        x = self.file.read(hi - lo, dtype="float64", always_2d=True).mean(axis=1)
        padded = lo > i0 or hi < i1
        if padded:
            x = np.concatenate([np.zeros(lo - i0), x, np.zeros(i1 - hi)])
        return x, i0 / sr - sec, padded


@dataclass
class Window:
    sr: float
    t0: float                    # seconds of sample 0 relative to the beat
    grid: np.ndarray             # >150 Hz envelope (1 ms smooth) on the common grid, for the cross-correlation
    candidate: bool = False      # clear 40-120 Hz peak and a sharp >150 Hz attack
    body: float = 0.0            # the 40-120 Hz peak level
    click_ratio: float = 0.0     # peak over median of the >150 Hz envelope in the reference window
    kick: bool = False           # candidate that also passes the body test, set by measure_transition
    edge_ms: float = math.nan    # click leading edge, ms from the beat
    main_ms: float = math.nan    # main hit (steepest rise of the >40 Hz envelope), ms from the beat

    def index(self, ms):
        return int(round((ms / 1000.0 - self.t0) * self.sr))


GRID_MS = (XCORR_REF_MS[0] - XCORR_MAX_LAG_MS, XCORR_REF_MS[1] + XCORR_MAX_LAG_MS)


def _on_grid(hi, sr, t0, lo_ms, hi_ms):
    lo, up = int(round(lo_ms / 1000.0 * GRID_HZ)), int(round(hi_ms / 1000.0 * GRID_HZ))
    return np.interp(np.arange(lo, up + 1) / GRID_HZ, t0 + np.arange(len(hi)) / sr, hi)


def analyse_window(x, sr, t0, edge_only=False):
    """One window analysed. edge_only skips what the cross-correlation and the main hit need (the common grid, the click
    ratio, the >40 Hz envelope): kick test and click leading edge are identical, in about half the time."""
    low_sos, high_sos, broad_sos = _filters(float(sr))
    w = Window(sr, t0, None)
    low = _box(_envelope(signal.sosfiltfilt(low_sos, x)), sr, 2.0)
    # the click and main-hit work needs only the middle of the window
    c0, c1 = w.index(-CORE_MS), w.index(CORE_MS)
    xc, tc = x[c0:c1 + 1], t0 + c0 / sr
    hi_env = _envelope(signal.sosfiltfilt(high_sos, xc))
    hi1, hi05 = _box(hi_env, sr, 1.0), _box(hi_env, sr, 0.5)
    ci = lambda ms: int(round((ms / 1000.0 - tc) * sr))
    if not edge_only:
        broad = _box(_envelope(signal.sosfiltfilt(broad_sos, xc)), sr, 1.5)
        w.grid = _on_grid(hi1, sr, tc, *GRID_MS)
        core = _on_grid(hi1, sr, tc, *XCORR_REF_MS)
        cm = float(np.median(core))
        w.click_ratio = float(core.max() / cm) if cm > 0 else math.inf

    # is there a kick: a clear 40-120 Hz peak just after the beat, and a sharp >150 Hz attack
    seg = low[w.index(-10.0):w.index(100.0) + 1]
    median = float(np.median(low))
    if seg.size == 0 or (median > 0 and seg.max() / median < KICK_RATIO_MIN):
        return w
    w.body = float(seg.max())
    h = max(1, int(round(0.00025 * sr)))
    lo, hi = ci(SEARCH_MS[0]), ci(SEARCH_MS[1])
    d = hi05[lo + h:hi + 1 + h] - hi05[lo - h:hi + 1 - h]
    a = int(np.argmax(d))
    if a == 0 or a == len(d) - 1:
        return w
    k = lo + a
    pre = float(np.mean(hi05[k - int(0.025 * sr):k - int(0.005 * sr)]))
    post = float(np.max(hi05[k:k + int(0.010 * sr)]))
    if pre > 0 and post / pre < ATTACK_JUMP_MIN:
        return w
    w.candidate = True

    # click leading edge: first point above 10 percent of the rise, searched 15 ms before the steepest point
    peak = float(np.max(hi1[k - int(0.005 * sr):k + int(0.010 * sr)]))
    thr = pre + EDGE_FRACTION * (peak - pre)
    start = k - int(0.015 * sr)
    j = start
    while j < k and hi1[j] < thr:
        j += 1
    if j > start and hi1[j] != hi1[j - 1]:
        frac = (thr - hi1[j - 1]) / (hi1[j] - hi1[j - 1])
        w.edge_ms = ((j - 1 + frac) / sr + tc) * 1000.0

    # main hit: steepest rise of the >40 Hz envelope, from just before the click to 25 ms after it
    if edge_only:
        return w
    m0, m1 = k - int(MAIN_BEFORE_MS / 1000.0 * sr), k + int(MAIN_AFTER_MS / 1000.0 * sr)
    db = broad[m0 + h:m1 + 1 + h] - broad[m0 - h:m1 + 1 - h]
    b = int(np.argmax(db))
    if 0 < b < len(db) - 1:
        w.main_ms = ((m0 + b + _parabola(db, b)) / sr + tc) * 1000.0
    return w


def click_lag_ms(w_out, w_in):
    """Lag of the incoming's >150 Hz envelope behind the outgoing's (ms), the correlation, and whether the
    peak sat on the edge of the plus or minus 30 ms range (then the lag is NaN: it is not known)."""
    lag_samples = int(round(XCORR_MAX_LAG_MS / 1000.0 * GRID_HZ))
    ref = w_out.grid[lag_samples:len(w_out.grid) - lag_samples]
    wide = w_in.grid
    ref = ref - ref.mean()
    norm = math.sqrt(float((ref ** 2).sum()))
    if norm <= 0:
        return math.nan, math.nan, False
    m = len(ref)
    c = signal.correlate(wide, ref, mode="valid", method="fft")
    s1 = np.concatenate([[0.0], np.cumsum(wide)])
    s2 = np.concatenate([[0.0], np.cumsum(wide * wide)])
    total = s1[m:] - s1[:-m]
    var = np.maximum((s2[m:] - s2[:-m]) - total * total / m, 1e-30)
    r = c / (norm * np.sqrt(var))
    j = int(np.argmax(r))
    if j == 0 or j == len(r) - 1:
        return math.nan, float(r[j]), True
    return (j + _parabola(r, j) - lag_samples) / GRID_HZ * 1000.0, float(r[j]), False


# ------------------------------------------------------------------ one transition

@dataclass
class BeatMeasure:
    beat: int
    wav_out: str
    sec_out: float
    wav_in: str
    sec_in: float
    shared: bool                 # both tracks carry a kick on this beat
    click_lag: float = math.nan  # cross-correlation of the >150 Hz envelopes
    click_r: float = math.nan
    edge_lag: float = math.nan   # difference of the two leading edges
    main_lag: float = math.nan   # difference of the two main-hit times
    out_edge: float = math.nan
    out_main: float = math.nan
    in_edge: float = math.nan
    in_main: float = math.nan


@dataclass
class TransitionResult:
    index: int
    outgoing: str
    incoming: str
    outgoing_short: str
    incoming_short: str
    start_beat: float
    end_beat: float
    beats: list = field(default_factory=list)
    shared_kicks: int = 0
    click: dict = field(default_factory=dict)
    main: dict = field(default_factory=dict)
    edge: dict = field(default_factory=dict)
    verdict: str = "CANNOT_JUDGE"
    flags: list = field(default_factory=list)
    note: str = ""
    picture: str = ""
    dropped: dict = field(default_factory=dict)   # beats of the overlap that could not be measured, by reason
    padded_windows: int = 0                        # windows zero-padded at the start or end of a wav (kept)
    click_range_edge: int = 0                      # shared beats whose click lag sat on the +-30 ms edge
    kicks_out: int | None = None                   # kick beats of each track on its own inside the overlap
    kicks_in: int | None = None
    kick_span: int | None = None                   # beats where both tracks' kicks mostly are at once

    @property
    def label(self):
        return f"T{self.index:02d} {self.outgoing_short} to {self.incoming_short}"

    @property
    def has_picture_inputs(self):
        return not ({"MISSING_AUDIO", "UNSUPPORTED_CLIP"} & set(self.flags))


def _stats(values, beats, middle, fail):
    """Summary of one estimator's lags; `big` counts the beats at that estimator's own fail figure or more."""
    a = np.array(values, dtype=float)
    ok = ~np.isnan(a)
    out = dict(n=int(ok.sum()), median=None, iqr=None, first_half=None, second_half=None, big=0,
               n_first_half=0, n_second_half=0)
    if not ok.any():
        return out
    q25, q75 = np.percentile(a[ok], [25, 75])
    out["median"] = round(float(np.median(a[ok])), 3)
    out["iqr"] = round(float(q75 - q25), 3)
    out["big"] = int((np.abs(a[ok]) >= fail).sum())
    b = np.array(beats)
    for key, sel in (("first_half", ok & (b < middle)), ("second_half", ok & (b >= middle))):
        out["n_" + key] = int(sel.sum())
        if sel.any():
            out[key] = round(float(np.median(a[sel])), 3)
    return out


def _present(stat):
    return stat is not None and stat.get("median") is not None and stat.get("n", 0) >= MIN_ESTIMATOR_BEATS


def _minority(stat, shared, fail):
    """A fifth of the shared beats or more at the estimator's fail figure while its median stays under it."""
    return stat.get("big", 0) >= round(MINORITY_FRACTION * shared, 9) and abs(stat["median"]) < fail


def _lag_seen(click, main):
    """The |median| of the estimator furthest past its own warn figure, or None when neither reaches it."""
    seen = [(abs(st["median"]) / _WARN[name], abs(st["median"]))
            for name, st in (("CLICK", click), ("MAIN", main)) if st.get("median") is not None]
    reached = [s for s in seen if s[0] >= 1.0]
    return max(reached)[1] if reached else None


def judge(shared, click, main, edge=None, kicks=None):
    """Verdict and flags from the shared-kick count, the lag summaries and each track's own kick count.

    Each estimator is judged by its own figures (CLICK_* and MAIN_*): the transition is as bad as its worst
    estimator. A scattered estimator (its beats spread wider than SCATTER_MS) may fail the transition only when
    the other estimator corroborates it with a median of at least ITS OWN warn figure; alone it is capped at WARN
    (LARGE_SCATTERED). A half of the blend whose median is at its estimator's fail figure on a trusted or
    corroborated estimator is DRIFT_FLAM and fails. The leading edge never decides a verdict: it only raises a
    PASS to WARN when it sits more than EDGE_DISAGREE_MS from the click (EDGE_DISAGREES)."""
    flags = []
    out_kicks, in_kicks, span = (tuple(kicks) + (None,))[:3] if kicks else (None, None, None)
    have = {"CLICK": _present(click), "MAIN": _present(main)}
    if (shared < MIN_SHARED_KICKS / 2 and out_kicks is not None and min(out_kicks, in_kicks) >= MIN_SHARED_KICKS
            and (span is None or span >= MIN_SHARED_KICKS)):
        # both tracks have kicks over a stretch long enough to judge, but not on the same beats; a hand-over,
        # where one track's kicks stop before the other's start, has no such stretch and stays CANNOT_JUDGE
        return "FAIL", ["OFF_GRID"]
    if shared < MIN_SHARED_KICKS or not any(have.values()):
        if _lag_seen(click, main) is not None:
            flags.append("LARGE_LAG_FEW_KICKS")
        if shared >= MIN_SHARED_KICKS:
            flags += ["NO_CLICK", "NO_MAIN"]
        return "CANNOT_JUDGE", flags
    present = {name: st for name, st in (("CLICK", click), ("MAIN", main)) if have[name]}
    flags += ["NO_" + name for name in have if not have[name]]
    scattered = {name for name, st in present.items() if st["iqr"] > SCATTER_MS}
    flags.extend("SCATTER_" + name for name in present if name in scattered)
    meds = {name: abs(st["median"]) for name, st in present.items()}
    fail = capped = False
    for name, value in meds.items():
        if value >= _FAIL[name]:
            if name not in scattered or any(meds[o] >= _WARN[o] for o in meds if o != name):
                fail = True
            else:
                capped = True
    if capped:
        flags.append("LARGE_SCATTERED")
    # a blend that is fine in one half and flammed in the other
    for half in ("first_half", "second_half"):
        halves = {name: abs(st[half]) for name, st in present.items()
                  if st.get(half) is not None and st.get("n_" + half, st["n"]) >= MIN_ESTIMATOR_BEATS}
        if any(v >= _FAIL[name]
               and (name not in scattered or any(halves.get(o, -1.0) >= _WARN[o] for o in present if o != name))
               for name, v in halves.items()):
            if "DRIFT_FLAM" not in flags:
                flags.append("DRIFT_FLAM")
            fail = True
    verdict = "FAIL" if fail else "WARN" if (any(v >= _WARN[name] for name, v in meds.items()) or scattered) else "PASS"
    if len(present) == 2 and not scattered and abs(click["median"] - main["median"]) > SLOW_ATTACK_MS:
        flags.append("SLOW_ATTACK")
        if verdict == "PASS":
            verdict = "WARN"
    # a flam on a minority of the beats does not move the median: count the beats (only from an estimator
    # that is not scattered, and only while its median is under its own fail figure, so nothing is hidden)
    if any(_minority(st, shared, _FAIL[name]) for name, st in present.items() if name not in scattered):
        flags.append("MINORITY_FLAM")
        if verdict == "PASS":
            verdict = "WARN"
    if _present(edge) and have["CLICK"] and abs(edge["median"] - click["median"]) > EDGE_DISAGREE_MS:
        flags.append("EDGE_DISAGREES")
        if verdict == "PASS":
            verdict = "WARN"
    return verdict, flags


def explain(res):
    """Plain-words reasons behind the flags, for the summary line."""
    out = []
    flags, cm, mm = set(res.flags), res.click.get("median"), res.main.get("median")
    window = f"search window of plus or minus {SEARCH_MS[1]:g} ms"
    if res.note:
        return [res.note]
    known = res.kicks_out is not None and res.kicks_in is not None
    few_tracks = [(name, k) for name, k in ((res.outgoing_short, res.kicks_out), (res.incoming_short, res.kicks_in))
                  if known and k < MIN_ESTIMATOR_BEATS]
    if "OFF_GRID" in flags:
        out.append(f"both tracks have kicks ({res.kicks_out} and {res.kicks_in} beats) but only {res.shared_kicks} "
                   f"on the same beats over the {res.kick_span} beats where both play: the kicks are on different beats "
                   f"or far apart")
    elif res.verdict == "CANNOT_JUDGE" and few_tracks:
        for name, k in few_tracks:
            out.append(f"{name} has {k} kick beats in this overlap within plus or minus {SEARCH_MS[1]:g} ms of the beat "
                       f"(a kick further off the beat than that cannot be measured)")
    elif res.verdict == "CANNOT_JUDGE" and res.shared_kicks == 0:
        out.append(f"no beat where both tracks have a kick in the {window}; a lag larger than that cannot be measured")
    elif res.verdict == "CANNOT_JUDGE" and res.shared_kicks < MIN_SHARED_KICKS:
        out.append(f"only {res.shared_kicks} beats where both tracks have a kick in the {window} "
                   f"(need {MIN_SHARED_KICKS}); a lag larger than that cannot be measured")
    elif res.verdict == "CANNOT_JUDGE":
        out.append("no usable click lag and no usable main hit lag")
    if res.verdict == "CANNOT_JUDGE" and not few_tracks and known and res.kick_span is not None \
            and res.kick_span < MIN_SHARED_KICKS:
        out.append("the two tracks' kicks hardly play at the same time here (a hand-over)")
    if "LARGE_LAG_FEW_KICKS" in flags and not few_tracks:
        if res.click_range_edge:
            out.append("lag outside the measurable range")
        else:
            out.append(f"a lag of {_lag_seen(res.click, res.main):.1f} ms was seen but on too few kicks to judge")
    if "NO_CLICK" in flags and res.verdict != "CANNOT_JUDGE":
        out.append("no usable click lag; judged on the main hit alone")
    if "NO_MAIN" in flags and res.verdict != "CANNOT_JUDGE":
        out.append("no usable main hit lag; judged on the click alone")
    if "SLOW_ATTACK" in flags:
        if abs(cm) < CLICK_WARN_MS and abs(mm) >= MAIN_WARN_MS:
            out.append("click lined up, main hit late: look at the picture")
        elif abs(mm) < MAIN_WARN_MS and abs(cm) >= CLICK_WARN_MS:
            out.append("main hit lined up, click late: look at the picture")
        else:
            out.append(f"click and main hit disagree by {abs(cm - mm):.1f} ms: look at the picture")
    if "EDGE_DISAGREES" in flags:
        out.append(f"leading edge {res.edge['median']:+.1f} ms but click {cm:+.1f} ms: the two clicks disagree, "
                   f"look at the picture")
    for name, key, st in (("click", "CLICK", res.click), ("main hit", "MAIN", res.main)):
        if st.get("median") is None:
            continue
        if "DRIFT_FLAM" in flags and any(
                st.get(h) is not None and st.get("n_" + h, st.get("n", 0)) >= MIN_ESTIMATOR_BEATS
                and abs(st[h]) >= _FAIL[key] for h in ("first_half", "second_half")):
            out.append(f"one half of the blend is {_FAIL[key]:g} ms or more off on {name}")
        if "LARGE_SCATTERED" in flags and st["iqr"] > SCATTER_MS and abs(st["median"]) >= _FAIL[key]:
            out.append(f"{name} median {st['median']:+.1f} ms but the beats spread {st['iqr']:.1f} ms: not a clean number")
        if "MINORITY_FLAM" in flags and st["iqr"] <= SCATTER_MS and _minority(st, res.shared_kicks, _FAIL[key]):
            out.append(f"{st['big']} of {res.shared_kicks} shared beats are {_FAIL[key]:g} ms or more apart on {name}")
        fh, sh = st.get("first_half"), st.get("second_half")
        if fh is not None and sh is not None and abs(sh - fh) > _WARN[key]:
            out.append(f"{name} drifts {fh:+.1f} ms to {sh:+.1f} ms")
    if res.click_range_edge:
        out.append(f"{res.click_range_edge} click lags sat on the +-{XCORR_MAX_LAG_MS:g} ms edge and are not counted")
    return out


def _clips_in(track, start, end):
    return [c for c in track.clips if c.end > start and c.start < end]


def measure_transition(tr, readers):
    """readers: resolved wav path (str) -> WavReader. A clip whose audio was not found has audio None."""
    result = TransitionResult(tr.index, tr.outgoing.name, tr.incoming.name,
                              short_name(tr.outgoing.name), short_name(tr.incoming.name), tr.start, tr.end)
    clips = _clips_in(tr.outgoing, tr.start, tr.end) + _clips_in(tr.incoming, tr.start, tr.end)
    missing = sorted({c.wav for c in clips if c.audio is None})
    if missing:
        result.flags.append("MISSING_AUDIO")
        result.note = "audio not found: " + ", ".join(missing)
        return result
    if any(not c.supported for c in clips):
        result.flags.append("UNSUPPORTED_CLIP")
        result.note = ("a clip in this blend uses LoopOn or StartRelative or is not warped, which is not mapped "
                       "(refused, not guessed)")
        return result
    pairs = []
    dropped = {"no_position": 0, "no_window": 0}
    for beat in range(int(math.ceil(tr.start)), int(math.ceil(tr.end))):
        po, pi = source_position(tr.outgoing, float(beat)), source_position(tr.incoming, float(beat))
        if po is None or pi is None:
            dropped["no_position"] += 1
            continue
        ro, ri = readers[str(po.clip.audio)], readers[str(pi.clip.audio)]
        got_o, got_i = ro.window(po.sec), ri.window(pi.sec)
        if got_o is None or got_i is None:
            dropped["no_window"] += 1
            continue
        result.padded_windows += int(got_o[2]) + int(got_i[2])
        wo = analyse_window(got_o[0], ro.sr, got_o[1])
        wi = analyse_window(got_i[0], ri.sr, got_i[1])
        pairs.append((BeatMeasure(beat, str(po.clip.audio), po.sec, str(pi.clip.audio), pi.sec, False), wo, wi))
    result.dropped = dropped
    # the body test: a kick's 40-120 Hz peak is a fair fraction of the typical kick of that track here
    floor = []
    for which in (1, 2):
        peaks = [p[which].body for p in pairs if p[which].candidate]
        floor.append(BODY_FRACTION * float(np.median(peaks)) if peaks else math.inf)
    for row, wo, wi in pairs:
        wo.kick = wo.candidate and wo.body >= floor[0]
        wi.kick = wi.candidate and wi.body >= floor[1]
        row.shared = wo.kick and wi.kick
        if row.shared:
            if wo.click_ratio >= KICK_RATIO_MIN and wi.click_ratio >= KICK_RATIO_MIN:
                row.click_lag, row.click_r, at_edge = click_lag_ms(wo, wi)
                result.click_range_edge += int(at_edge)
            row.out_edge, row.out_main, row.in_edge, row.in_main = wo.edge_ms, wo.main_ms, wi.edge_ms, wi.main_ms
            row.edge_lag, row.main_lag = wi.edge_ms - wo.edge_ms, wi.main_ms - wo.main_ms
        result.beats.append(row)
    result.kicks_out = sum(wo.kick for _, wo, _ in pairs)
    result.kicks_in = sum(wi.kick for _, _, wi in pairs)
    out_at = [row.beat for row, wo, _ in pairs if wo.kick]
    in_at = [row.beat for row, _, wi in pairs if wi.kick]
    if out_at and in_at:                     # where each track's kicks mostly are (10th to 90th percentile of the beats)
        lo_o, hi_o = np.percentile(out_at, [10, 90])
        lo_i, hi_i = np.percentile(in_at, [10, 90])
        result.kick_span = int(max(0.0, min(hi_o, hi_i) - max(lo_o, lo_i) + 1))
    else:
        result.kick_span = 0
    shared = [r for r in result.beats if r.shared]
    result.shared_kicks = len(shared)
    nums = [r.beat for r in shared]
    middle = (tr.start + tr.end) / 2.0
    result.click = _stats([r.click_lag for r in shared], nums, middle, CLICK_FAIL_MS)
    result.main = _stats([r.main_lag for r in shared], nums, middle, MAIN_FAIL_MS)
    result.edge = _stats([r.edge_lag for r in shared], nums, middle, math.inf)      # the edge never decides: no fail figure
    result.verdict, flags = judge(result.shared_kicks, result.click, result.main, result.edge,
                                  (result.kicks_out, result.kicks_in, result.kick_span))
    result.flags.extend(flags)
    return result


# ------------------------------------------------------------------ the picture

def _score(row):
    """How far apart the two kicks are on this beat, as a multiple of each estimator's own warn figure (the worse of
    click and main hit; the edge is not scored), so the main hit's bigger numbers do not always win; -1 when not measured."""
    vals = [abs(v) / _WARN[name] for name, v in (("CLICK", row.click_lag), ("MAIN", row.main_lag)) if not math.isnan(v)]
    return max(vals) if vals else -1.0


def worst_beats(rows, k=2):
    """The k shared beats with the largest lag (only beats that were measured)."""
    measured = [r for r in rows if r.shared and _score(r) >= 0]
    return sorted(measured, key=lambda r: (-_score(r), r.beat))[:k]


def pick_beats(rows, count=PIC_COLUMNS):
    """The two worst shared beats, then evenly spread others early to late, in beat order."""
    beats = [r for r in rows if r.shared]
    if len(beats) <= count:
        return beats
    chosen = {r.beat: r for r in worst_beats(rows)}
    rest = [r for r in beats if r.beat not in chosen]
    slots = count - len(chosen)
    seen = set()
    for q in np.linspace(0.08, 0.92, slots):
        i = int(round(q * (len(rest) - 1)))
        if i not in seen:
            seen.add(i)
            chosen[rest[i].beat] = rest[i]
    return [chosen[b] for b in sorted(chosen)]


def _fmt(v):
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:+.1f} ms"


def _short(v):
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:+.1f}"


def build_figure(result, readers):
    """Outgoing above, incoming below, one column per chosen kick, beat line at 0. For a human to look at."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    rows = pick_beats(result.beats)
    worst = {r.beat for r in worst_beats(result.beats) if _score(r) >= 1.0}
    fig = Figure(figsize=PIC_SIZE_IN, dpi=100)
    FigureCanvasAgg(fig)
    title = (f"{result.label}   {result.verdict}   edge {_fmt(result.edge.get('median'))}   "
             f"click {_fmt(result.click.get('median'))}   main hit {_fmt(result.main.get('median'))}   "
             f"shared kicks {result.shared_kicks}" + ("   " + " ".join(result.flags) if result.flags else ""))
    fig.suptitle(title, fontsize=15, fontweight="bold")
    if not rows:
        fig.text(0.5, 0.5, "no beat where both tracks have a kick: nothing to draw", ha="center", fontsize=16)
        return fig
    axes = fig.subplots(2, len(rows), sharex=True, squeeze=False)
    for col, r in enumerate(rows):
        for row, (wav, sec, edge, main, colour, name) in enumerate((
                (r.wav_out, r.sec_out, r.out_edge, r.out_main, OUT_COLOUR, result.outgoing_short),
                (r.wav_in, r.sec_in, r.in_edge, r.in_main, IN_COLOUR, result.incoming_short))):
            ax = axes[row][col]
            x, t0, _ = readers[wav].window(sec)
            ms = (t0 + np.arange(len(x)) / readers[wav].sr) * 1000.0
            sel = (ms >= PIC_X_MS[0] - 5) & (ms <= PIC_X_MS[1] + 5)
            y = x[sel] / max(float(np.max(np.abs(x[sel]))), 1e-9)
            ax.fill_between(ms[sel], y, color=colour, alpha=0.35, linewidth=0)
            ax.plot(ms[sel], y, color=colour, linewidth=0.8)
            ax.axvline(0.0, color="black", linestyle="--", linewidth=1.2)
            if not math.isnan(edge):
                ax.axvline(edge, ymin=0.0, ymax=0.14, color="black", linewidth=2.5)
            if not math.isnan(main):
                ax.axvline(main, ymin=0.86, ymax=1.0, color="#1f5fbf", linewidth=2.5)
            ax.set_xlim(*PIC_X_MS)
            ax.set_ylim(-1.15, 1.15)
            ax.set_yticks([])
            ax.grid(axis="x", alpha=0.25)
            if col == 0:
                ax.set_ylabel(name, color=colour, fontsize=12, fontweight="bold")
            if row == 1:
                ax.set_xlabel("ms from the beat line")
        axes[0][col].set_title(f"{'WORST ' if r.beat in worst else ''}beat {r.beat}   edge {_short(r.edge_lag)}   "
                               f"click {_short(r.click_lag)}   main {_short(r.main_lag)} ms",
                               fontsize=10, color="#b00020" if r.beat in worst else "black")
    fig.text(0.5, 0.005, "dashed = beat line   black tick (bottom) = click edge, leading edge of the >150 Hz envelope   "
             "blue tick (top) = main hit, steepest rise of the >40 Hz envelope   lag > 0: incoming later",
             ha="center", fontsize=9)
    fig.subplots_adjust(left=0.04, right=0.995, top=0.87, bottom=0.12, wspace=0.08, hspace=0.12)
    return fig


def draw_transition(result, readers, path):
    build_figure(result, readers).savefig(path, dpi=100)


# ------------------------------------------------------------------ the whole set

def resolve_audio(clip, als_dir, audio_dir):
    """The audio folder wins when it holds the file name; then the path the ALS stores (absolute as given,
    relative to the ALS's own folder); then the ALS's relative path."""
    candidates = []
    if audio_dir:
        candidates.append(Path(audio_dir) / clip.wav)
    for stored in (clip.path, clip.relative_path):
        if stored:
            p = Path(stored)
            candidates.append(p if p.is_absolute() else Path(als_dir) / p)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


_OWN_PICTURE = re.compile(r"T\d\d .*\.png")


def clear_old_outputs(out_dir):
    """Delete the pictures and the json an earlier run of this tool left, and nothing else."""
    out = Path(out_dir)
    if not out.is_dir():
        return
    for f in out.iterdir():
        if f.is_file() and (_OWN_PICTURE.fullmatch(f.name) or f.name == "kick_alignment.json"):
            f.unlink()


def check_als(als_path, audio_dir=None, out_dir=None):
    """Measure every transition of the ALS; draw one picture each into out_dir when given."""
    if out_dir is not None:
        clear_old_outputs(out_dir)
    tracks = read_als(als_path)
    transitions = find_transitions(tracks)
    als_dir = Path(als_path).resolve().parent
    readers = {}
    try:
        for tr in transitions:
            for track in (tr.outgoing, tr.incoming):
                for clip in track.clips:
                    clip.audio = resolve_audio(clip, als_dir, audio_dir)
                    if clip.audio is not None and str(clip.audio) not in readers:
                        readers[str(clip.audio)] = WavReader(clip.audio)
        results = [measure_transition(tr, readers) for tr in transitions]
        if out_dir is not None:
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            for res in results:
                if not res.has_picture_inputs:
                    continue
                target = out / f"{res.label}.png"
                draw_transition(res, readers, target)
                res.picture = str(target)
                gc.collect()
    finally:
        for reader in readers.values():
            reader.close()
    return results


def summary_line(res):
    text = f"{res.label}: {res.verdict}"
    if not res.note:
        text += (f" edge {_fmt(res.edge.get('median'))} click {_fmt(res.click.get('median'))} "
                 f"main {_fmt(res.main.get('median'))} shared kicks {res.shared_kicks}")
    if res.flags:
        text += " " + " ".join(res.flags)
    reasons = explain(res)
    return text + (" | " + "; ".join(reasons) if reasons else "")


def count_line(results):
    counts = {v: sum(r.verdict == v for r in results) for v in ("PASS", "WARN", "FAIL", "CANNOT_JUDGE")}
    return (f"{len(results)} transitions: {counts['PASS']} PASS, {counts['WARN']} WARN, "
            f"{counts['FAIL']} FAIL, {counts['CANNOT_JUDGE']} CANNOT_JUDGE")


def nothing_judged(results):
    return all(r.verdict == "CANNOT_JUDGE" for r in results)


def results_json(results):
    def clean(v):
        if isinstance(v, float) and math.isnan(v):
            return None
        if isinstance(v, float):
            return round(v, 4)
        return v

    return [dict(index=r.index, outgoing=r.outgoing, incoming=r.incoming, start_beat=r.start_beat,
                 end_beat=r.end_beat, shared_kicks=r.shared_kicks, verdict=r.verdict, flags=r.flags,
                 note=r.note, click=r.click, main=r.main, edge=r.edge, picture=r.picture,
                 dropped=r.dropped, padded_windows=r.padded_windows, click_range_edge=r.click_range_edge,
                 kicks_out=r.kicks_out, kicks_in=r.kicks_in, kick_span=r.kick_span,
                 beats=[{k: clean(v) for k, v in vars(b).items()} for b in r.beats if b.shared])
            for r in results]


def write_json(results, path):
    Path(path).write_text(json.dumps(results_json(results), indent=1), encoding="ascii")
