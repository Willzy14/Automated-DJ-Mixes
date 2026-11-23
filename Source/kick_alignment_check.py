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
The verdict uses the worse of the two median lags. A soft-attack kick (clean click, late main hit)
has a click lag near 0 and a main-hit lag well away from it: that is flagged SLOW_ATTACK.

Limits (not measured away): lags are in source milliseconds, not stretched milliseconds (the
difference is the warp ratio times the lag, under about 3 percent). Fader, EQ and filter automation
is ignored: a beat still counts when both wavs have a kick there, even if the mix filters one side
out. In a full mix the steepest rise can land on a bass note or a snare instead of the kick, so an
estimator whose beats spread wider than SCATTER_MS (interquartile range) cannot fail a transition
alone and cannot let it pass: the verdict is then at least WARN and the flag says which one.
The ALS is only read. A beat counts as a kick only when the 40-120 Hz peak is at least 3 times
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

# Sam sets the real figures (2026-11-23); these are the starting values.
WARN_MS = 4.0
FAIL_MS = 8.0
MIN_SHARED_KICKS = 16
SLOW_ATTACK_MS = 4.0          # click and main-hit medians further apart than this
SCATTER_MS = FAIL_MS          # an estimator whose beats spread (interquartile range) wider than this is not trusted

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
    wav: str                     # file name only


@dataclass
class Track:
    name: str
    clips: list

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
    for track in root.iter("AudioTrack"):
        name = _value(track, "Name/EffectiveName", "")
        events = track.find("DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")
        nodes = events.findall("AudioClip") if events is not None else list(track.iter("AudioClip"))
        clips = []
        for node in nodes:
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
                wav=re.split(r"[\\/]", clip_path)[-1]))
        if clips:
            tracks.append(Track(name, sorted(clips, key=lambda c: c.start)))
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
    """Arrangement beat -> clip, source beat and wav seconds; None outside every clip or marker range."""
    for clip in track.clips:
        if clip.start <= beat < clip.end:
            offset = clip.start_relative + (beat - clip.start)
            if clip.loop_on and clip.loop_end > clip.loop_start:
                offset = offset % (clip.loop_end - clip.loop_start)
            source_beat = clip.loop_start + offset
            m = clip.markers
            if len(m) < 2 or source_beat < m[0, 0] or source_beat > m[-1, 0]:
                return None
            return Position(clip, source_beat, float(np.interp(source_beat, m[:, 0], m[:, 1])))
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
        """Mono samples around `sec`, and the time of the first sample relative to `sec`, in seconds."""
        sr = self.sr
        centre = int(round(sec * sr))
        i0 = centre - int(READ_BEFORE_S * sr)
        i1 = centre + int(READ_AFTER_S * sr)
        if i0 < 0 or i1 > self.file.frames:
            return None
        self.file.seek(i0)
        x = self.file.read(i1 - i0, dtype="float64", always_2d=True).mean(axis=1)
        return x, i0 / sr - sec


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


def analyse_window(x, sr, t0):
    low_sos, high_sos, broad_sos = _filters(float(sr))
    w = Window(sr, t0, None)
    low = _box(_envelope(signal.sosfiltfilt(low_sos, x)), sr, 2.0)
    # the click and main-hit work needs only the middle of the window
    c0, c1 = w.index(-CORE_MS), w.index(CORE_MS)
    xc, tc = x[c0:c1 + 1], t0 + c0 / sr
    hi_env = _envelope(signal.sosfiltfilt(high_sos, xc))
    hi1, hi05 = _box(hi_env, sr, 1.0), _box(hi_env, sr, 0.5)
    broad = _box(_envelope(signal.sosfiltfilt(broad_sos, xc)), sr, 1.5)
    ci = lambda ms: int(round((ms / 1000.0 - tc) * sr))
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
    m0, m1 = k - int(MAIN_BEFORE_MS / 1000.0 * sr), k + int(MAIN_AFTER_MS / 1000.0 * sr)
    db = broad[m0 + h:m1 + 1 + h] - broad[m0 - h:m1 + 1 - h]
    b = int(np.argmax(db))
    if 0 < b < len(db) - 1:
        w.main_ms = ((m0 + b + _parabola(db, b)) / sr + tc) * 1000.0
    return w


def click_lag_ms(w_out, w_in):
    """Lag of the incoming's >150 Hz envelope behind the outgoing's, ms, and the correlation. NaN at the edge."""
    lag_samples = int(round(XCORR_MAX_LAG_MS / 1000.0 * GRID_HZ))
    ref = w_out.grid[lag_samples:len(w_out.grid) - lag_samples]
    wide = w_in.grid
    ref = ref - ref.mean()
    norm = math.sqrt(float((ref ** 2).sum()))
    if norm <= 0:
        return math.nan, math.nan
    m = len(ref)
    c = signal.correlate(wide, ref, mode="valid", method="fft")
    s1 = np.concatenate([[0.0], np.cumsum(wide)])
    s2 = np.concatenate([[0.0], np.cumsum(wide * wide)])
    total = s1[m:] - s1[:-m]
    var = np.maximum((s2[m:] - s2[:-m]) - total * total / m, 1e-30)
    r = c / (norm * np.sqrt(var))
    j = int(np.argmax(r))
    if j == 0 or j == len(r) - 1:
        return math.nan, float(r[j])
    return (j + _parabola(r, j) - lag_samples) / GRID_HZ * 1000.0, float(r[j])


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

    @property
    def label(self):
        return f"T{self.index:02d} {self.outgoing_short} to {self.incoming_short}"


def _stats(values, beats, middle):
    a = np.array(values, dtype=float)
    ok = ~np.isnan(a)
    out = dict(n=int(ok.sum()), median=None, iqr=None, first_half=None, second_half=None)
    if not ok.any():
        return out
    q25, q75 = np.percentile(a[ok], [25, 75])
    out["median"] = round(float(np.median(a[ok])), 3)
    out["iqr"] = round(float(q75 - q25), 3)
    b = np.array(beats)
    for key, sel in (("first_half", ok & (b < middle)), ("second_half", ok & (b >= middle))):
        if sel.any():
            out[key] = round(float(np.median(a[sel])), 3)
    return out


def judge(shared, click, main):
    """Verdict and flags from the shared-kick count and the two lag summaries."""
    flags = []
    cm, mm = click.get("median"), main.get("median")
    if shared < MIN_SHARED_KICKS or cm is None or mm is None:
        seen = [abs(v) for v in (cm, mm) if v is not None]
        if seen and max(seen) >= FAIL_MS:
            flags.append("LARGE_LAG_FEW_KICKS")
        return "CANNOT_JUDGE", flags
    # an estimator whose beats disagree with each other by more than the fail figure has no trustworthy
    # median: it cannot fail the transition alone, and it stops it from passing
    scattered = [(name, st) for name, st in (("CLICK", click), ("MAIN", main)) if st["iqr"] > SCATTER_MS]
    trusted = [abs(st["median"]) for st in (click, main) if st["iqr"] <= SCATTER_MS]
    flags.extend("SCATTER_" + name for name, _ in scattered)
    worst = max(trusted) if trusted else None
    verdict = "WARN" if worst is None else "FAIL" if worst >= FAIL_MS else "WARN" if worst >= WARN_MS else "PASS"
    if scattered and verdict == "PASS":
        verdict = "WARN"
    if not scattered and abs(cm - mm) > SLOW_ATTACK_MS:
        flags.append("SLOW_ATTACK")
        if verdict == "PASS":
            verdict = "WARN"
    return verdict, flags


def measure_transition(tr, readers):
    """readers: wav file name -> WavReader (or None when the audio is missing)."""
    result = TransitionResult(tr.index, tr.outgoing.name, tr.incoming.name,
                              short_name(tr.outgoing.name), short_name(tr.incoming.name), tr.start, tr.end)
    missing = sorted({c.wav for t in (tr.outgoing, tr.incoming) for c in t.clips if readers.get(c.wav) is None})
    if missing:
        result.flags.append("MISSING_AUDIO")
        result.note = "audio not found: " + ", ".join(missing)
        return result
    pairs = []
    for beat in range(int(math.ceil(tr.start)), int(math.ceil(tr.end))):
        po, pi = source_position(tr.outgoing, float(beat)), source_position(tr.incoming, float(beat))
        if po is None or pi is None:
            continue
        got_o, got_i = readers[po.clip.wav].window(po.sec), readers[pi.clip.wav].window(pi.sec)
        if got_o is None or got_i is None:
            continue
        wo = analyse_window(got_o[0], readers[po.clip.wav].sr, got_o[1])
        wi = analyse_window(got_i[0], readers[pi.clip.wav].sr, got_i[1])
        pairs.append((BeatMeasure(beat, po.clip.wav, po.sec, pi.clip.wav, pi.sec, False), wo, wi))
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
                row.click_lag, row.click_r = click_lag_ms(wo, wi)
            row.out_edge, row.out_main, row.in_edge, row.in_main = wo.edge_ms, wo.main_ms, wi.edge_ms, wi.main_ms
            row.edge_lag, row.main_lag = wi.edge_ms - wo.edge_ms, wi.main_ms - wo.main_ms
        result.beats.append(row)
    shared = [r for r in result.beats if r.shared]
    result.shared_kicks = len(shared)
    nums = [r.beat for r in shared]
    middle = (tr.start + tr.end) / 2.0
    result.click = _stats([r.click_lag for r in shared], nums, middle)
    result.main = _stats([r.main_lag for r in shared], nums, middle)
    result.edge = _stats([r.edge_lag for r in shared], nums, middle)
    result.verdict, flags = judge(result.shared_kicks, result.click, result.main)
    result.flags.extend(flags)
    return result


# ------------------------------------------------------------------ the picture

def pick_beats(rows, count=PIC_COLUMNS):
    """Spread early, middle and late over the shared kick beats."""
    beats = [r for r in rows if r.shared]
    if len(beats) <= count:
        return beats
    chosen, seen = [], set()
    for q in np.linspace(0.08, 0.92, count):
        i = int(round(q * (len(beats) - 1)))
        if i not in seen:
            seen.add(i)
            chosen.append(beats[i])
    return chosen


def _fmt(v):
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:+.1f} ms"


def draw_transition(result, readers, path):
    """Outgoing above, incoming below, one column per chosen kick, beat line at 0. For a human to look at."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    rows = pick_beats(result.beats)
    fig = Figure(figsize=PIC_SIZE_IN, dpi=100)
    FigureCanvasAgg(fig)
    title = (f"{result.label}   {result.verdict}   click {_fmt(result.click.get('median'))}   "
             f"main hit {_fmt(result.main.get('median'))}   shared kicks {result.shared_kicks}"
             + ("   " + " ".join(result.flags) if result.flags else ""))
    fig.suptitle(title, fontsize=15, fontweight="bold")
    if not rows:
        fig.text(0.5, 0.5, "no beat where both tracks have a kick: nothing to draw", ha="center", fontsize=16)
        fig.savefig(path, dpi=100)
        return
    axes = fig.subplots(2, len(rows), sharex=True, squeeze=False)
    for col, r in enumerate(rows):
        for row, (wav, sec, edge, main, colour, name) in enumerate((
                (r.wav_out, r.sec_out, r.out_edge, r.out_main, OUT_COLOUR, result.outgoing_short),
                (r.wav_in, r.sec_in, r.in_edge, r.in_main, IN_COLOUR, result.incoming_short))):
            ax = axes[row][col]
            x, t0 = readers[wav].window(sec)
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
        axes[0][col].set_title(f"beat {r.beat}   click {_fmt(r.click_lag)}   main hit {_fmt(r.main_lag)}", fontsize=11)
    fig.text(0.5, 0.005, "dashed = beat line   black tick (bottom) = click, leading edge of the >150 Hz envelope   "
             "blue tick (top) = main hit, steepest rise of the >40 Hz envelope   lag > 0: incoming later",
             ha="center", fontsize=9)
    fig.subplots_adjust(left=0.04, right=0.995, top=0.87, bottom=0.12, wspace=0.08, hspace=0.12)
    fig.savefig(path, dpi=100)


# ------------------------------------------------------------------ the whole set

def resolve_audio(clip_wav, clip_path, audio_dir):
    for candidate in ((Path(audio_dir) / clip_wav) if audio_dir else None, Path(clip_path) if clip_path else None):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def check_als(als_path, audio_dir, out_dir=None):
    """Measure every transition of the ALS; draw one picture each into out_dir when given."""
    tracks = read_als(als_path)
    transitions = find_transitions(tracks)
    readers = {}
    try:
        for tr in transitions:
            for track in (tr.outgoing, tr.incoming):
                for clip in track.clips:
                    if clip.wav not in readers:
                        found = resolve_audio(clip.wav, clip.path, audio_dir)
                        readers[clip.wav] = WavReader(found) if found else None
        results = [measure_transition(tr, readers) for tr in transitions]
        if out_dir is not None:
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            for res in results:
                if res.flags and "MISSING_AUDIO" in res.flags:
                    continue
                target = out / f"{res.label}.png"
                draw_transition(res, readers, target)
                res.picture = str(target)
                gc.collect()
    finally:
        for reader in readers.values():
            if reader is not None:
                reader.close()
    return results


def summary_line(res):
    if res.note:
        return f"{res.label}: {res.verdict} {res.note}"
    text = (f"{res.label}: {res.verdict} click {_fmt(res.click.get('median'))} "
            f"main {_fmt(res.main.get('median'))} shared kicks {res.shared_kicks}")
    return text + (" " + " ".join(res.flags) if res.flags else "")


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
                 beats=[{k: clean(v) for k, v in vars(b).items()} for b in r.beats if b.shared])
            for r in results]


def write_json(results, path):
    Path(path).write_text(json.dumps(results_json(results), indent=1), encoding="ascii")
