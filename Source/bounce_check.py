"""What the listener hears at every join of a bounced mix, measured on the rendered WAV. Read only.

Why: kick_alignment_check.py reads the SOURCE audio through the warp markers, not what Ableton renders, so it cannot
see a bass swap with a phase hole, a join where one track's low end is much heavier than the other's, a stereo width
jump, or a loudness step. This module takes the bounce plus the project's MIX_PLAN.json and ARRANGEMENT_REPORT.json (or
an ALS, for a hand-edited arrangement) and measures the audio in the three windows around each join.

Mapping (the lead can check it): the bounce starts at arrangement beat 0. Seconds come from the tempo envelope (MIX_PLAN
tempo.points, or the ALS MainTrack tempo envelope), tempo linear in BPM between breakpoints per beat (render_check.TempoMap).
A join runs from overlap_start_beat to overlap_end_beat (MIX_PLAN transitions; ALS mode: the incoming track's first clip
start to the outgoing track's last clip end). The map is checked against the bounce length (the bounce must not be shorter
than the arrangement) and against the audio (the kick grid offset the flam step finds, a few ms when the mapping is right).

Windows per join: before = the 20 s up to the overlap start, during = the overlap, after = the 20 s from the overlap end;
a side window stops at the neighbouring join so it never holds a second blend, and one under 3 s is not measured.
  loudness  K-weighted (the BS.1770 filters taken from pyloudnorm, applied in streaming blocks so no whole-mix array exists;
            tested against pyloudnorm's own meter) in 0.25 s hops. Window level = ungated power mean; step = after minus before
            in LU; pile-up = the loudest 3 s short-term value inside the overlap minus the loudest 3 s value on either side.
  tonal     mean power of six bands (sub 20-60, low 60-150, low-mid 150-500, mid 500-2k, high-mid 2k-6k, high 6k+, 4th order
            Butterworth) in the three windows; per-band step after minus before in dB and the worst band.
  low end   40-150 Hz in the overlap: level against the louder side, the L/R correlation of that band against the sides, and the
            worst bar (lowest level against the sides, and lowest correlation) with its time in the bounce.
  width     side over mid power above 150 Hz, step after minus before in dB.
  flam      per kick beat inside the overlap, whether a second kick lands 3 to 25 ms after the first, by (a) fitting the two
            tracks' own kick templates (learnt from the beats just outside the overlap) to the beat and (b) the literal
            second-attack test on the >150 Hz envelope. Both are run on control beats where only one track plays.

Limits (not measured away): a flam under 3 ms, or two kicks that are the same sound within the fit tolerance, reads as one
kick; the template fit needs a clean run of kicks of each track just outside the overlap, so a join without one is reported
as not measurable, never as clean. The literal second-attack test fires on beats where only one track plays (a kick has
several peaks in the click band; measured on Tech House 2026: 24 of 24 control beats), so it never decides by itself and
its control rate is printed beside it. The tonal and width steps compare two different tracks, so a step is not a fault by
itself: the verdict only says whether a join is inside the range of joins Sam accepted (build_ranges), never pass or fail
from a limit nobody has set (a flam fraction, the size of a tonal step and a low-end correlation are judged on their one concerning
end only: ONE_SIDED). Fader, EQ and filter automation are heard in the bounce, which is the point; what cannot be told is WHICH
track caused a change, only that the join sounds different. The calibration on Sam's accepted mixes showed the
flam fit also firing on control beats (one track playing) in most joins where it ran, so a flam fraction is a report, not a verdict.

Attention (never a build failure): attention() picks the joins the building AI must open the picture of. A join needs a look only for
a measure with a direct audible meaning (loudness step, blend peak, low-end level, low-end correlation fall, width step) outside the
accepted range by a material margin, or a kick flam SEEN; the margins are starting figures (ATTENTION_MARGINS). Tonal steps and
worst-bar levels stay information. Situations that mean the numbers cannot be trusted (start warning, map that does not fit, a join
not measured, no ranges, no flam run) are listed beside them.
"""
from __future__ import annotations

import html
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy import fft, signal

from render_check import TempoMap, parse_als
import kick_alignment_check as kac

BANDS = (("sub", 20.0, 60.0), ("low", 60.0, 150.0), ("low_mid", 150.0, 500.0), ("mid", 500.0, 2000.0),
         ("high_mid", 2000.0, 6000.0), ("high", 6000.0, None))
LOW_SUM_BAND = (40.0, 150.0)
WIDTH_HP_HZ = 150.0
CLICK_HP_HZ = 150.0
HOP_S = 0.25
SHORT_TERM_S = 3.0
SIDE_S = 20.0
MIN_WINDOW_S = 3.0
WARM_S = 2.0                  # filter warm-up read before every span
BLOCK_HOPS = 20               # 5 s per streaming block
BAR_BEATS = 4
FLOOR_DB = -120.0
LEVEL_FLOOR_DB = -60.0        # a band under this is absent: steps into or out of it are measured from here, not from noise
SILENT_LUFS = -70.0           # the BS.1770 absolute gate: a hop under this is silent; a window mostly silent is not measured
MONO_DB = -40.0               # side more than this far under mid: the window is mono and has no width to step
LOW_BAR_GATE_DB = 30.0        # a bar this far under the loudest bar (or silent) is not a candidate for the lowest correlation
THIN_JOINS = 5                # a range built from fewer joins than this carries a warning

PRE_MS, POST_MS = 40.0, 60.0  # window around a kick beat
FLAM_MIN_MS, FLAM_MAX_MS = 3.0, 25.0
ATTACK_REL = 0.35             # a second attack counts when its rise is this much of the strongest
RHO_MIN = 0.5                 # a template fit counts when the residual still correlates this well with the template
KICK_RATIO_MIN = 3.0
CONTEXT_BEFORE_BEATS = 300    # kicks looked for this far before the overlap (templates and controls)
CONTEXT_AFTER_BEATS = 250
N_CONTROL = 24
MIN_FLAM_BEATS = 4
MIN_FLAM_FRACTION = 0.05
MIN_FIT_BEATS = 8             # fewer beats with both kicks found than this: not measurable
GRID_OFFSET_WARN_MS = 40.0
TAIL_WARN_S = 30.0
START_SILENCE_WARN_S = 2.0    # no sound for this long at the start: the bounce may not begin at beat 0
START_LUFS = -60.0            # first hop louder than this is the first sound
GRID_PHASE_WARN_MS = 60.0     # the kicks attack further than this from a beat line (real bounces: 1 to 22 ms): map and audio are out of step
GRID_PHASE_SPAN_S = 60.0

SCALAR_NAMES = ("loud_step_lu", "loud_peak_vs_sides_lu", "loud_mean_vs_sides_lu",
                "tonal_step_sub_db", "tonal_step_low_db", "tonal_step_low_mid_db", "tonal_step_mid_db",
                "tonal_step_high_mid_db", "tonal_step_high_db", "tonal_worst_abs_db",
                "low_level_vs_sides_db", "low_corr_overlap", "low_corr_drop", "low_worst_bar_level_vs_sides_db",
                "width_step_db", "flam_fraction_of_fitted_beats")


def _db(power, floor=FLOOR_DB):
    return floor if not power > 1e-12 else max(floor, 10.0 * math.log10(power))


def short_name(name):
    """kick_alignment_check.short_name, also without a bit depth ('Come Back 16 Bit MASTER' -> 'Come Back')."""
    return kac.short_name(re.sub(r"\s+\d+\s*Bit(?=\s|$)", "", name, flags=re.IGNORECASE))


def _fmt_time(sec):
    sec = max(0.0, float(sec))
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


# ------------------------------------------------------------------ the joins

@dataclass
class Join:
    index: int                   # 1-based
    out_name: str
    in_name: str
    start_beat: float
    end_beat: float
    start_s: float
    end_s: float
    prev_end_beat: float | None = None
    next_start_beat: float | None = None
    prev_end_s: float | None = None
    next_start_s: float | None = None


def _link_neighbours(joins, tempo):
    for i, j in enumerate(joins):
        if i > 0:
            j.prev_end_beat, j.prev_end_s = joins[i - 1].end_beat, joins[i - 1].end_s
        if i + 1 < len(joins):
            j.next_start_beat, j.next_start_s = joins[i + 1].start_beat, joins[i + 1].start_s
    return joins


def joins_from_plan(plan, report):
    """(tempo map, joins, last arrangement beat) from MIX_PLAN.json and ARRANGEMENT_REPORT.json (both already parsed).
    The report must agree with the plan (same count, same overlap length, same start); a stale pair is refused."""
    points = plan["tempo"]["points"]
    tempo = TempoMap(np.array([p[0] for p in points], float), np.array([p[1] for p in points], float),
                     float(plan.get("project_bpm") or points[0][1]))
    names = {t["track_instance_id"]: html.unescape(t.get("display_name", "")) for t in plan["tracks"]}
    rtrans = report.get("transitions", [])
    if len(rtrans) != len(plan["transitions"]):
        raise ValueError(f"report has {len(rtrans)} transitions, plan has {len(plan['transitions'])}: not the same run")
    joins = []
    for i, (tr, rt) in enumerate(zip(plan["transitions"], rtrans)):
        s, e = float(tr["overlap_start_beat"]), float(tr["overlap_end_beat"])
        if abs((e - s) - float(rt["overlap_beats"])) > 1e-6:
            raise ValueError(f"join {i + 1}: plan overlap {e - s} beats, report {rt['overlap_beats']}: not the same run")
        rtracks = report.get("tracks", [])
        if len(rtracks) == len(plan["transitions"]) + 1 and (
                abs(float(rtracks[i + 1]["arr_start"]) - s) > 1e-6 or abs(float(rtracks[i]["arr_end"]) - e) > 1e-6):
            raise ValueError(f"join {i + 1}: plan overlap is beats {s} to {e}, report tracks say "
                             f"{rtracks[i + 1]['arr_start']} to {rtracks[i]['arr_end']}: not the same run")
        out = names.get(tr["out_track_instance_id"]) or html.unescape(rt.get("out_track", ""))
        inc = names.get(tr["in_track_instance_id"]) or html.unescape(rt.get("in_track", ""))
        joins.append(Join(i + 1, out, inc, s, e, float(tempo.beat_to_sec(s)), float(tempo.beat_to_sec(e))))
    last = max(float(t["arrangement_end_beat"]) for t in plan["tracks"])
    return tempo, _link_neighbours(joins, tempo), last


def joins_from_als(als_path):
    """Same, read from an ALS (a hand-edited arrangement has no plan that matches it)."""
    tempo, _clips = parse_als(Path(als_path))
    tracks = kac.read_als(als_path)
    joins = []
    for tr in kac.find_transitions(tracks):
        joins.append(Join(tr.index, tr.outgoing.name, tr.incoming.name, tr.start, tr.end,
                          float(tempo.beat_to_sec(tr.start)), float(tempo.beat_to_sec(tr.end))))
    last = max(c.end for t in tracks for c in t.clips)
    return tempo, _link_neighbours(joins, tempo), float(last)


def check_mapping(tempo, last_beat, bounce_s, joins):
    """The tempo map against the bounce length: the bounce may carry a tail after the last clip, never fall short."""
    end_s = float(tempo.beat_to_sec(last_beat))
    tail = bounce_s - end_s
    problems = []
    if tail < -1.0:
        problems.append(f"bounce is {-tail:.1f} s SHORTER than the arrangement: the map is wrong or the bounce is cut")
    if tail > TAIL_WARN_S:
        problems.append(f"bounce is {tail:.1f} s longer than the arrangement: check it starts at beat 0")
    for j in joins:
        if j.end_s > bounce_s + 0.5:
            problems.append(f"join {j.index} ends at {j.end_s:.1f} s, after the bounce")
    return {"arrangement_end_beat": float(last_beat), "map_end_s": end_s, "bounce_s": float(bounce_s),
            "tail_s": float(tail), "tempo_points": int(tempo.summary["n_points"]), "ok": not problems, "problems": problems}


# ------------------------------------------------------------------ streaming features

@lru_cache(maxsize=4)
def _filters(sr):
    def sos(kind, edges):
        return signal.butter(4, edges, btype=kind, fs=sr, output="sos")
    bands = []
    for _name, lo, hi in BANDS:
        bands.append(sos("highpass", lo) if hi is None else sos("bandpass", [lo, hi]))
    k = np.vstack([signal.tf2sos(f.b * f.passband_gain, f.a) for f in pyln.Meter(sr)._filters.values()])
    return {"bands": bands, "low": sos("bandpass", list(LOW_SUM_BAND)), "width": sos("highpass", WIDTH_HP_HZ), "k": k}


class Hops:
    """Mean-square values per 0.25 s hop for one span of the bounce."""

    def __init__(self, t0, hop_s, arrays):
        self.t0, self.hop_s = t0, hop_s
        self.__dict__.update(arrays)
        self.n = len(self.k_l)

    def span(self, a_s, b_s):
        """Hop index range of the hops that lie wholly inside [a_s, b_s]."""
        i0 = max(0, int(math.ceil((a_s - self.t0) / self.hop_s - 1e-9)))
        i1 = min(self.n, int(math.floor((b_s - self.t0) / self.hop_s + 1e-9)))
        return i0, max(i0, i1)


def check_channels(f, name=""):
    """Mono and stereo only: BS.1770 weights the surround channels differently and nothing here implements that, so more than
    two channels are refused, never silently dropped."""
    if f.channels > 2:
        raise ValueError(f"{name or 'the bounce'} has {f.channels} channels: only mono and stereo bounces are supported "
                         "(BS.1770 surround weights are not implemented)")


def _stereo(x):
    return np.repeat(x, 2, axis=1) if x.shape[1] == 1 else x[:, :2]


def analyse_span(path, t0, t1, hop_s=HOP_S):
    """Hops of mean-square values over [t0, t1] of a WAV, read in blocks of 10 s with filter state carried across."""
    with sf.SoundFile(str(path)) as f:
        check_channels(f, Path(path).name)
        mono = f.channels == 1
        sr, frames = f.samplerate, f.frames
        hop = int(round(hop_s * sr))
        i0 = max(0, int(round(t0 * sr)))
        n_hops = (min(frames, int(round(t1 * sr))) - i0) // hop
        if n_hops <= 0:
            return None
        flt = _filters(sr)
        chain = [("b%d" % i, s) for i, s in enumerate(flt["bands"])] + [("low", flt["low"]), ("width", flt["width"]), ("k", flt["k"])]
        state = {n: np.zeros((s.shape[0], 2, 2)) for n, s in chain}
        out = {"band": [], "k": [], "low": [], "width": []}

        def run(x, keep):
            y = {}
            for n, s in chain:
                y[n], state[n] = signal.sosfilt(s, x, axis=0, zi=state[n])
            if not keep:
                return
            m = x.shape[0] // hop

            def hopmean(a):
                return a[:m * hop].reshape(m, hop, *a.shape[1:]).mean(axis=1)

            out["band"].append(np.stack([hopmean((y["b%d" % i] ** 2).mean(axis=1)) for i in range(len(BANDS))], axis=1))
            kp = hopmean(y["k"] ** 2)
            if mono:                  # one channel, as pyloudnorm reads it: not the same signal counted twice
                kp[:, 1] = 0.0
            out["k"].append(kp)
            lo = y["low"]
            out["low"].append(np.stack([hopmean(lo[:, 0] ** 2), hopmean(lo[:, 1] ** 2), hopmean(lo[:, 0] * lo[:, 1])], axis=1))
            w = y["width"]
            mid, side = (w[:, 0] + w[:, 1]) / 2.0, (w[:, 0] - w[:, 1]) / 2.0
            out["width"].append(np.stack([hopmean(mid ** 2), hopmean(side ** 2)], axis=1))

        warm = min(i0, int(WARM_S * sr))
        f.seek(i0 - warm)
        if warm:
            run(_stereo(f.read(warm, dtype="float64", always_2d=True)), False)
        done, total = 0, n_hops * hop
        while done < total:
            want = min(BLOCK_HOPS * hop, total - done)
            x = _stereo(f.read(want, dtype="float64", always_2d=True))
            if x.shape[0] < want:
                break
            run(x, True)
            done += want
    cat = {k: np.concatenate(v) for k, v in out.items()}
    return Hops(i0 / sr, hop_s, {
        "band": cat["band"], "k_l": cat["k"][:, 0], "k_r": cat["k"][:, 1],
        "low_ll": cat["low"][:, 0], "low_rr": cat["low"][:, 1], "low_lr": cat["low"][:, 2],
        "mid": cat["width"][:, 0], "side": cat["width"][:, 1]})


def _lufs(power_l_plus_r):
    return -0.691 + _db(power_l_plus_r)


def short_term(h):
    """3 s short-term loudness: (start time of each window, LUFS). Windows are 12 hops, sliding by one hop."""
    n = int(round(SHORT_TERM_S / h.hop_s))
    p = h.k_l + h.k_r
    if len(p) < n:
        return np.array([]), np.array([])
    mean = np.convolve(p, np.ones(n) / n, mode="valid")
    lufs = np.array([_lufs(v) for v in mean])
    return h.t0 + np.arange(len(mean)) * h.hop_s, lufs


def _st_max_slice(h, sl):
    """Loudest 3 s short-term value among the windows that lie wholly inside the hops sl, anchored at the window's own first hop."""
    n = int(round(SHORT_TERM_S / h.hop_s))
    p = (h.k_l + h.k_r)[sl[0]:sl[1]]
    if len(p) < n:
        return None
    mean = np.convolve(p, np.ones(n) / n, mode="valid")
    return float(_lufs(float(mean.max())))


# ------------------------------------------------------------------ the measurements of one join

def _windows(join, duration):
    s, e = join.start_s, join.end_s
    b0 = max(0.0, s - SIDE_S)
    if join.prev_end_s is not None:
        b0 = max(b0, join.prev_end_s)
    a1 = min(duration, e + SIDE_S)
    if join.next_start_s is not None:
        a1 = min(a1, join.next_start_s)
    return {"before": (b0, s), "during": (s, e), "after": (e, a1)}


def _mean(arr, sl):
    return float(arr[sl[0]:sl[1]].mean()) if sl[1] > sl[0] else None


def _corr(h, sl):
    ll, rr, lr = (arr[sl[0]:sl[1]].sum() for arr in (h.low_ll, h.low_rr, h.low_lr))
    return float(lr / math.sqrt(ll * rr)) if ll > 0 and rr > 0 else None


def measure_join(h, join, tempo, duration):
    """Loudness, tonal, low end and width of one join from the hops (flam is separate: it needs the audio)."""
    wins = _windows(join, duration)
    sl, ok = {}, {}
    for name, (a, b) in wins.items():
        sl[name] = h.span(a, b)
        ok[name] = (sl[name][1] - sl[name][0]) * h.hop_s >= MIN_WINDOW_S
    res = {"windows": {n: {"start_s": a, "end_s": b, "measured_s": (sl[n][1] - sl[n][0]) * h.hop_s} for n, (a, b) in wins.items()},
           "notes": [f"{n} window is under {MIN_WINDOW_S:.0f} s: not measured" for n in wins if not ok[n]]}
    kpow = h.k_l + h.k_r
    hop_lufs = -0.691 + 10.0 * np.log10(np.maximum(kpow, 1e-12))
    gated, silent = {}, []
    for n in wins:
        if ok[n]:
            keep = hop_lufs[sl[n][0]:sl[n][1]] >= SILENT_LUFS
            if keep.mean() < 0.5:
                ok[n] = False
                silent.append(n)
                res["notes"].append(f"{n} window silent: not measured")
            else:
                gated[n] = float(kpow[sl[n][0]:sl[n][1]][keep].mean())
    res["silent_windows"] = silent
    both = ok["before"] and ok["after"]

    # loudness: the power mean of the hops above the BS.1770 absolute gate, so a silent gap does not pull a window down
    lufs = {n: _lufs(gated[n]) for n in wins if ok[n]}
    st = {n: _st_max_slice(h, sl[n]) for n in wins if ok[n]}
    loud = {"level_lufs": lufs, "peak_short_term_lufs": st}
    if both:
        loud["step_lu"] = lufs["after"] - lufs["before"]
    if ok["during"] and both and None not in (st.get("during"), st.get("before"), st.get("after")):
        loud["peak_vs_sides_lu"] = st["during"] - max(st["before"], st["after"])
    if ok["during"] and both:
        loud["mean_vs_sides_lu"] = lufs["during"] - max(lufs["before"], lufs["after"])
    res["loudness"] = loud

    # tonal
    levels = {name: {n: _db(_mean(h.band[:, i], sl[n]), LEVEL_FLOOR_DB) for n in wins if ok[n]}
              for i, (name, _lo, _hi) in enumerate(BANDS)}
    tonal = {"levels_db": levels}
    if both:
        steps = {b: levels[b]["after"] - levels[b]["before"] for b in levels}
        worst = max(steps, key=lambda b: abs(steps[b]))
        tonal["step_db"], tonal["worst_band"], tonal["worst_step_db"] = steps, worst, steps[worst]
    res["tonal"] = tonal

    # low end
    low = {}
    lvl = {n: _db((_mean(h.low_ll, sl[n]) + _mean(h.low_rr, sl[n])) / 2.0, LEVEL_FLOOR_DB) for n in wins if ok[n]}
    cor = {n: _corr(h, sl[n]) for n in wins if ok[n]}
    low["level_db"], low["corr"] = lvl, cor
    if ok["during"] and both:
        sides = max(lvl["before"], lvl["after"])
        low["level_vs_sides_db"] = lvl["during"] - sides
        if None not in (cor["during"], cor["before"], cor["after"]):
            low["corr_drop"] = cor["during"] - min(cor["before"], cor["after"])
        bars = []
        beat = join.start_beat
        while beat + BAR_BEATS <= join.end_beat + 1e-9:
            a, b = float(tempo.beat_to_sec(beat)), float(tempo.beat_to_sec(beat + BAR_BEATS))
            i0, i1 = h.span(a, b)
            if i1 - i0 >= 2:
                level = _db((_mean(h.low_ll, (i0, i1)) + _mean(h.low_rr, (i0, i1))) / 2.0, LEVEL_FLOOR_DB)
                bars.append({"bar": len(bars) + 1, "start_s": a, "level_db": level, "level_vs_sides_db": level - sides,
                             "corr": _corr(h, (i0, i1))})
            beat += BAR_BEATS
        if bars:
            low["worst_bar"] = min(bars, key=lambda b: b["level_vs_sides_db"])
            loudest = max(b["level_db"] for b in bars)
            withc = [b for b in bars if b["corr"] is not None and b["level_db"] > LEVEL_FLOOR_DB + 1e-9
                     and b["level_db"] >= loudest - LOW_BAR_GATE_DB]
            if withc:
                low["lowest_corr_bar"] = min(withc, key=lambda b: b["corr"])
    res["low_end"] = low

    # width
    ratio = {n: _db(_mean(h.side, sl[n])) - _db(_mean(h.mid, sl[n])) for n in wins if ok[n]}
    mono = {n: bool(r < MONO_DB) for n, r in ratio.items()}
    width = {"side_over_mid_db": ratio, "mono": mono}
    if both and not (mono["before"] or mono["after"]):
        width["step_db"] = ratio["after"] - ratio["before"]
    elif both:
        res["notes"].append("width step not measured: the " + " and ".join(n for n in ("before", "after") if mono[n]) +
                            f" window is mono (side more than {-MONO_DB:.0f} dB under mid)")
    res["width"] = width
    return res


def scalars(result):
    """The flat numbers a range is built from (only those that exist for this join)."""
    out = {}
    if result.get("silent_windows"):          # a join with a silent window says nothing about the mix: it feeds no range
        return out
    loud, tonal, low = result.get("loudness", {}), result.get("tonal", {}), result.get("low_end", {})
    for name, v in (("loud_step_lu", loud.get("step_lu")), ("loud_peak_vs_sides_lu", loud.get("peak_vs_sides_lu")),
                    ("loud_mean_vs_sides_lu", loud.get("mean_vs_sides_lu")),
                    ("tonal_worst_abs_db", abs(tonal["worst_step_db"]) if "worst_step_db" in tonal else None),
                    ("low_level_vs_sides_db", low.get("level_vs_sides_db")), ("low_corr_overlap", low.get("corr", {}).get("during")),
                    ("low_corr_drop", low.get("corr_drop")),
                    ("low_worst_bar_level_vs_sides_db", low.get("worst_bar", {}).get("level_vs_sides_db")),
                    ("width_step_db", result.get("width", {}).get("step_db"))):
        if v is not None:
            out[name] = float(v)
    for b, v in tonal.get("step_db", {}).items():
        out[f"tonal_step_{b}_db"] = float(v)
    flam = result.get("flam", {})
    if flam.get("state") in ("flam_seen", "no_flam_seen"):
        out["flam_fraction_of_fitted_beats"] = float(flam["fraction_of_fit_beats"])
    return {k: v for k, v in out.items() if math.isfinite(v)}      # NaN fails every comparison and would read as inside any range


# ------------------------------------------------------------------ kick flam on the bounce

def _box(x, n):
    n = max(1, int(n))
    return np.convolve(x, np.ones(n) / n, mode="same")


def _ms(ms, sr):
    return max(1, int(round(ms * 1e-3 * sr)))


def _envelope(band, sr, smooth_ms):
    n = len(band)
    return _box(np.abs(signal.hilbert(band, N=fft.next_fast_len(n)))[:n], smooth_ms * 1e-3 * sr)


class ClickRegion:
    """The >150 Hz band of a stretch of the bounce (mono, causal filter), for kicks near beat lines."""

    def __init__(self, path, tempo, beat0, beat1):
        self.tempo = tempo
        with sf.SoundFile(str(path)) as f:
            self.sr = f.samplerate
            sos = signal.butter(4, CLICK_HP_HZ, btype="highpass", fs=self.sr, output="sos")
            i0 = max(0, int(round(float(tempo.beat_to_sec(max(0.0, beat0))) * self.sr)))
            i1 = min(f.frames, int(round(float(tempo.beat_to_sec(beat1)) * self.sr)))
            self.i0 = i0
            self.click = np.zeros(max(0, i1 - i0), dtype=np.float32)
            warm = min(i0, int(1.5 * self.sr))
            f.seek(i0 - warm)
            zi = np.zeros((sos.shape[0], 2))
            done = -warm
            block = 20 * self.sr
            while done < len(self.click):
                want = -done if done < 0 else min(block, len(self.click) - done)
                x = f.read(want, dtype="float64", always_2d=True)
                if x.shape[0] == 0:
                    break
                y, zi = signal.sosfilt(sos, x.mean(axis=1), zi=zi)
                if done >= 0:
                    self.click[done:done + len(y)] = y
                done += len(y)

    def idx(self, beat, ms=0.0):
        return int(round((float(self.tempo.beat_to_sec(beat)) + ms * 1e-3) * self.sr)) - self.i0

    def win(self, beat, a_ms, z_ms):
        a, z = self.idx(beat, a_ms), self.idx(beat, z_ms)
        if a < 0 or z > len(self.click):
            return None
        return self.click[a:z].astype(np.float64)

    def env(self, beat, a_ms, z_ms, pad_ms=50.0):
        """0.5 ms-smoothed envelope of the click band over [a_ms, z_ms] around the beat (padded so the edges are clean)."""
        a, z, p = self.idx(beat, a_ms), self.idx(beat, z_ms), _ms(pad_ms, self.sr)
        if a - p < 0 or z + p > len(self.click):
            return None
        return _envelope(self.click[a - p:z + p].astype(np.float64), self.sr, 0.5)[p:p + (z - a)]


def find_attacks(env, sr, t0_ms, step_w_ms=0.8, rel=0.35, lead=0.15, min_sep_ms=2.0, floor_rel=0.04):
    """Attacks of an envelope: a step up D(t) = mean(env[t, t+w]) - mean(env[t-w, t]), local maxima of D at or above rel times
    the largest; each attack's onset is where the envelope first passes lead of the rise. Sorted by time."""
    n, w = len(env), _ms(step_w_ms, sr)
    if n <= 2 * w + 2:
        return []
    cs = np.concatenate([[0.0], np.cumsum(env)])
    idx = np.arange(w, n - w)
    d = np.zeros(n)
    d[idx] = (cs[idx + w] - cs[idx]) / w - (cs[idx] - cs[idx - w]) / w
    if d.max() <= floor_rel * env.max():
        return []
    peaks, _ = signal.find_peaks(d, height=rel * d.max(), distance=_ms(min_sep_ms, sr))
    out = []
    for p in peaks:
        base = env[max(0, p - _ms(1.5, sr)):p + 1].min()
        post = env[p:min(n, p + _ms(2.0, sr))].max()
        thr = base + lead * (post - base)
        a, z = max(0, p - _ms(1.2, sr)), min(n - 1, p + _ms(0.6, sr))
        j = a
        while j < z and env[j] < thr:
            j += 1
        if j > a and env[j] != env[j - 1]:
            on = (j - 1) + min(1.0, max(0.0, (thr - env[j - 1]) / (env[j] - env[j - 1])))
        else:
            on = float(j)
        out.append({"t_ms": float(t0_ms + on * 1000.0 / sr), "rise": float(post - base)})
    out.sort(key=lambda a: a["t_ms"])
    return out


def attack_pair(attacks):
    """Two attacks, each at least ATTACK_REL of the strongest, FLAM_MIN_MS to FLAM_MAX_MS apart: the pair, else None."""
    if len(attacks) < 2:
        return None
    top = max(a["rise"] for a in attacks)
    strong = [a for a in attacks if a["rise"] >= ATTACK_REL * top]
    best = None
    for a, b in zip(strong, strong[1:]):
        if FLAM_MIN_MS <= b["t_ms"] - a["t_ms"] <= FLAM_MAX_MS and (best is None or a["rise"] + b["rise"] > best[0]):
            best = (a["rise"] + b["rise"], a, b)
    return None if best is None else (best[1], best[2])


def _first_onset_ms(env, sr, t0_ms, lo=-15.0, hi=25.0, lead=0.15):
    a, z = int(round((lo - t0_ms) * 1e-3 * sr)), int(round((hi - t0_ms) * 1e-3 * sr))
    seg = env[a:z]
    if len(seg) < 10:
        return None
    thr = lead * seg.max()
    j = int(np.argmax(seg >= thr))
    if j == 0:
        return None
    fr = (thr - seg[j - 1]) / (seg[j] - seg[j - 1]) if seg[j] != seg[j - 1] else 1.0
    return float(lo + (j - 1 + fr) * 1000.0 / sr)


def _kick_ratio(env):
    return float(env.max() / (np.median(env) + 1e-12))


def _is_kick(reg, beat):
    env = reg.env(beat, -PRE_MS, POST_MS)
    return env is not None and _kick_ratio(env) >= KICK_RATIO_MIN


def _kick_candidates(reg, start, step, lo, hi, scan=256, loud=0.5):
    """Loud kick beats walking from `start` in direction `step` inside [lo, hi): the nearest first."""
    found, beat, n = [], start, 0
    while lo <= beat < hi and n < scan:
        env = reg.env(beat, -PRE_MS, POST_MS)
        if env is not None and _kick_ratio(env) >= KICK_RATIO_MIN:
            onset = _first_onset_ms(env, reg.sr, -PRE_MS)
            if onset is not None and -15 <= onset <= 20:
                found.append((beat, float(env.max())))
        beat += step
        n += 1
    if not found:
        return []
    cut = loud * np.percentile([p for _, p in found], 90)
    return [b for b, p in found if p >= cut]


def _xcorr_norm(seg, ref, maxlag):
    """Best lag in samples (+-maxlag) of ref inside seg by normalised cross-correlation: (lag, correlation)."""
    n = len(ref)
    r = ref - ref.mean()
    c = signal.correlate(seg, r, mode="valid", method="fft")
    cs, cs2 = np.concatenate([[0.0], np.cumsum(seg)]), np.concatenate([[0.0], np.cumsum(seg ** 2)])
    k = np.arange(len(c))
    mean = (cs[k + n] - cs[k]) / n
    var = (cs2[k + n] - cs2[k]) - n * mean ** 2
    nc = c / (np.sqrt(np.maximum(var, 1e-18)) * np.linalg.norm(r) + 1e-18)
    j = int(np.argmax(nc))
    return j - maxlag, float(nc[j])


def _template_onset(template, sr):
    pad = int(0.01 * sr)
    env = _envelope(np.concatenate([np.zeros(pad), template, np.zeros(pad)]), sr, 0.3)
    return (int(np.argmax(env >= 0.15 * env.max())) - pad) * 1000.0 / sr


def build_bank(reg, beats, pre_ms=-3.0, post_ms=35.0, corr_keep=0.85, maxlag_ms=4.0, max_k=6, min_members=3):
    """Aligned-mean click templates of one track's kicks (up to max_k clusters), from single-track kick beats."""
    sr = reg.sr
    onsets = []
    for beat in beats:
        env = reg.env(beat, -PRE_MS, POST_MS)
        o = _first_onset_ms(env, sr, -PRE_MS) if env is not None else None
        if o is not None:
            onsets.append(o)
    if len(onsets) < min_members + 1:
        return []
    o_med = float(np.median(onsets))
    ml = int(round(maxlag_ms * 1e-3 * sr))
    a_ms, z_ms = o_med + pre_ms, o_med + post_ms
    length = int(round((z_ms - a_ms) * 1e-3 * sr))
    segs = []
    for beat in beats:
        a = reg.idx(beat, a_ms) - ml
        if a < 0 or a + length + 2 * ml > len(reg.click):
            continue
        segs.append(reg.click[a:a + length + 2 * ml].astype(np.float64))
    left, bank, rng = list(range(len(segs))), [], np.random.default_rng(0)
    while len(left) >= min_members and len(bank) < max_k:
        trial = list(rng.choice(left, size=min(12, len(left)), replace=False))
        best = None
        for i in trial:
            ref = segs[i][ml:ml + length]
            count = sum(_xcorr_norm(segs[j], ref, ml)[1] >= corr_keep for j in left)
            if best is None or count > best[0]:
                best = (count, i)
        seed = segs[best[1]][ml:ml + length].copy()
        for _ in range(2):
            members, lags, cors = [], [], []
            for j in left:
                lag, c = _xcorr_norm(segs[j], seed, ml)
                if c >= corr_keep:
                    members.append(j), lags.append(lag), cors.append(c)
            if len(members) < min_members:
                break
            seed = np.mean([segs[j][ml + lag:ml + lag + length] for j, lag in zip(members, lags)], axis=0)
        if len(members) < min_members:
            break
        template = seed - seed.mean()
        shift = float(np.median(lags)) * 1000.0 / sr
        tau = _template_onset(template, sr)
        bank.append({"template": template, "start_ms": float(a_ms + shift), "tau_ms": float(tau),
                     "onset_ms": float(a_ms + shift + tau), "n": len(members), "mean_corr": float(np.mean(cors))})
        left = [j for j in left if j not in members]
    bank.sort(key=lambda d: -d["n"])
    return bank


def _fit_two(x, tw0_ms, sr, co, ci, r_ms=16.0, iters=8):
    """x = a_o * template_o + a_i * template_i, each start held within r_ms of where that track's kick sits alone."""
    to, ti = co["template"], ci["template"]
    no, ni = float(to @ to), float(ti @ ti)
    xc = x - x.mean()

    def bounds(c, length):
        lo = int(round((c["onset_ms"] - r_ms - c["tau_ms"] - tw0_ms) * 1e-3 * sr))
        hi = int(round((c["onset_ms"] + r_ms - c["tau_ms"] - tw0_ms) * 1e-3 * sr))
        return max(0, lo), min(len(xc) - length, hi)

    lo_o, hi_o = bounds(co, len(to))
    lo_i, hi_i = bounds(ci, len(ti))
    if hi_o <= lo_o or hi_i <= lo_i:
        return None

    def place(t, m):
        y = np.zeros(len(xc))
        y[m:m + len(t)] = t
        return y

    def xc_of(sig, t):
        return signal.correlate(sig, t, mode="valid", method="fft")

    c_o, c_i = xc_of(xc, to), xc_of(xc, ti)
    mo = lo_o + int(np.argmax(c_o[lo_o:hi_o + 1]))
    ao = max(c_o[mo] / no, 0.0)
    mi = lo_i + int(np.argmax(c_i[lo_i:hi_i + 1]))
    ai = max(c_i[mi] / ni, 0.0)
    for _ in range(iters):
        c = xc_of(xc - ai * place(ti, mi), to)
        nmo = lo_o + int(np.argmax(c[lo_o:hi_o + 1]))
        nao = max(c[nmo] / no, 0.0)
        c = xc_of(xc - nao * place(to, nmo), ti)
        nmi = lo_i + int(np.argmax(c[lo_i:hi_i + 1]))
        nai = max(c[nmi] / ni, 0.0)
        same = nmo == mo and nmi == mi
        mo, ao, mi, ai = nmo, nao, nmi, nai
        if same:
            break
    yo, yi = ao * place(to, mo), ai * place(ti, mi)
    res = {"explained": float(1 - np.sum((xc - yo - yi) ** 2) / (np.sum(xc ** 2) + 1e-12))}
    for name, (t, m, other, c, y) in {"out": (to, mo, yi, co, yo), "in": (ti, mi, yo, ci, yi)}.items():
        r = (xc - other)[m:m + len(t)]
        rho = float((r @ t) / (np.linalg.norm(r) * np.linalg.norm(t) + 1e-12))
        res[name] = {"onset_ms": float(tw0_ms + m * 1000.0 / sr + c["tau_ms"]), "rho": rho,
                     "strength": float(_envelope(y, sr, 0.5).max())}
    return res


def _classify_fit(rec, rel=0.35):
    if rec is None:
        return "no_fit"
    if rec["out"]["rho"] < RHO_MIN:
        return "out_not_found"
    if rec["in"]["rho"] < RHO_MIN:
        return "in_not_found"
    so, si = rec["out"]["strength"], rec["in"]["strength"]
    if min(so, si) < rel * max(so, si):
        return "weak_second"
    lag = abs(rec["in"]["onset_ms"] - rec["out"]["onset_ms"])
    return "flam" if FLAM_MIN_MS <= lag <= FLAM_MAX_MS else "too_close"


def _analyse_beat(reg, beat, bank_out, bank_in):
    out = {"beat": int(beat)}
    env = reg.env(beat, -PRE_MS, POST_MS)
    pair = attack_pair(find_attacks(env, reg.sr, -PRE_MS))
    out["onset_pair"] = pair is not None
    out["onset_spacing_ms"] = float(pair[1]["t_ms"] - pair[0]["t_ms"]) if pair else None
    x = reg.win(beat, -50.0, 110.0)
    best = None
    if x is not None:
        for co in bank_out:
            for ci in bank_in:
                fit = _fit_two(x, -50.0, reg.sr, co, ci)
                if fit is not None and (best is None or fit["explained"] > best["explained"]):
                    best = fit
    out["class"] = _classify_fit(best)
    if best is not None:
        out["lag_ms"] = float(best["in"]["onset_ms"] - best["out"]["onset_ms"])
        out["fit"] = best
    return out


def _median(values):
    values = [v for v in values if v is not None]
    return float(np.median(values)) if values else None


def measure_flam(path, tempo, join, n_control=N_CONTROL):
    """Kick flam inside one join, on the bounce. See the module docstring for what it can and cannot see."""
    s, e = join.start_beat, join.end_beat
    lo_b = max(0.0, s - CONTEXT_BEFORE_BEATS)
    if join.prev_end_beat is not None:
        lo_b = max(lo_b, join.prev_end_beat)
    hi_b = e + CONTEXT_AFTER_BEATS
    if join.next_start_beat is not None:
        hi_b = min(hi_b, join.next_start_beat)
    with sf.SoundFile(str(path)) as f:
        file_beat = float(tempo.sec_to_beat(f.frames / f.samplerate))
    hi_b = min(hi_b, file_beat - 1)
    reg = ClickRegion(path, tempo, lo_b - 2, hi_b + 3)
    notes = []
    ctrl_pre = _kick_candidates(reg, s - 1, -1, lo_b, s)[:n_control]
    ctrl_post = _kick_candidates(reg, e, 1, e, hi_b)[:n_control]
    pre_far = (min(ctrl_pre) - 1) if ctrl_pre else s - 1
    post_far = (max(ctrl_post) + 1) if ctrl_post else e
    cand_out = _kick_candidates(reg, pre_far, -1, lo_b, pre_far + 1)[:96]
    cand_in = _kick_candidates(reg, post_far, 1, post_far, hi_b)[:96]
    independent = len(cand_out) >= 8 and len(cand_in) >= 8
    if len(cand_out) < 8:
        cand_out = _kick_candidates(reg, s - 1, -1, lo_b, s)[:96]
        notes.append("outgoing templates built from the same beats as the control (few clean kicks outside the overlap)")
    if len(cand_in) < 8:
        cand_in = _kick_candidates(reg, e, 1, e, hi_b)[:96]
        notes.append("incoming templates built from the same beats as the control (few clean kicks outside the overlap)")
    bank_out = build_bank(reg, cand_out) if len(cand_out) >= 4 else []
    bank_in = build_bank(reg, cand_in) if len(cand_in) >= 4 else []
    overlap_beats = [b for b in range(int(math.ceil(s)), int(math.floor(e))) if _is_kick(reg, b)]
    res = {"state": "not_measurable", "n_kick_beats": len(overlap_beats), "notes": notes,
           "bank_out": [(d["n"], round(d["onset_ms"], 2)) for d in bank_out],
           "bank_in": [(d["n"], round(d["onset_ms"], 2)) for d in bank_in]}
    if bank_out and bank_in:
        res["grid_offset_ms"] = {"out": bank_out[0]["onset_ms"], "in": bank_in[0]["onset_ms"]}
        res["alone_onset_difference_ms"] = bank_in[0]["onset_ms"] - bank_out[0]["onset_ms"]
    if not bank_out or not bank_in or not overlap_beats:
        res["notes"].append("no clean kick templates for both tracks outside the overlap: flam cannot be judged on this bounce"
                            if not (bank_out and bank_in) else "no kick beats inside the overlap")
        return res
    rows = [_analyse_beat(reg, b, bank_out, bank_in) for b in overlap_beats]
    ctrl = [_analyse_beat(reg, b, bank_out, bank_in) for b in ctrl_pre + ctrl_post]
    counts = {}
    for r in rows:
        counts[r["class"]] = counts.get(r["class"], 0) + 1
    fitted = ("flam", "weak_second", "too_close")
    flams = [r for r in rows if r["class"] == "flam"]
    fit_both = [r for r in rows if r["class"] in fitted]
    ctrl_fit = [r for r in ctrl if r["class"] in fitted]
    ctrl_flams = [r for r in ctrl if r["class"] == "flam"]
    n = len(rows)
    frac = len(flams) / len(fit_both) if fit_both else 0.0               # over the beats the fit could place, not all kick beats
    ctrl_frac = len(ctrl_flams) / len(ctrl_fit) if ctrl_fit else 0.0
    onset_overlap = sum(r["onset_pair"] for r in rows) / n
    onset_ctrl = (sum(r["onset_pair"] for r in ctrl) / len(ctrl)) if ctrl else None
    res.update({
        "n_fit_both": len(fit_both), "n_flam": len(flams), "fraction_of_fit_beats": frac if fit_both else None,
        "median_spacing_ms": _median([abs(r["lag_ms"]) for r in flams]),
        "median_lag_ms": _median([r["lag_ms"] for r in flams]),
        "incoming_later_fraction": (sum(r["lag_ms"] > 0 for r in flams) / len(flams)) if flams else None,
        "class_counts": counts,
        "control": {"n_beats": len(ctrl), "n_fit": len(ctrl_fit), "n_flam": len(ctrl_flams), "fraction_of_fit_beats": ctrl_frac},
        "onset_test": {"overlap_fraction": onset_overlap, "control_fraction": onset_ctrl,
                       "discriminates": bool(onset_ctrl is not None and onset_overlap - onset_ctrl >= 0.25)}})
    ctrl_bad = bool(ctrl_flams) and ctrl_frac >= max(frac / 2, MIN_FLAM_FRACTION)
    if not independent:
        res["notes"].append("too few clean kicks outside the overlap to hold any back as a control: the templates were learnt from the "
                            "beats they are checked on, so the fit is not judged")
    elif len(fit_both) < MIN_FIT_BEATS:
        res["notes"].append(f"only {len(fit_both)} beats had both kicks found: the flam cannot be judged on this bounce")
    elif ctrl_bad:
        res["notes"].append(f"{len(ctrl_flams)} of {len(ctrl_fit)} fitted control beats (one track playing) fit as flams too "
                            f"({ctrl_frac:.0%}), so the fit cannot tell this blend from a single track, or the incoming track is "
                            "already playing there: not discriminating, not judged")
    elif len(flams) >= MIN_FLAM_BEATS and frac >= MIN_FLAM_FRACTION:
        res["state"] = "flam_seen"
    elif flams:
        res["state"] = "too_few_beats"
        res["notes"].append(f"{len(flams)} of {len(fit_both)} fitted beats ({frac:.0%}) fit as flams, under the minimum of "
                            f"{MIN_FLAM_BEATS} beats and {MIN_FLAM_FRACTION:.0%} to call one: observed, not judged, and not counted "
                            "as none seen")
    else:
        res["state"] = "no_flam_seen"
        res["notes"].append(f"no flam seen in {len(fit_both)} fitted beats of {n} kick beats; a flam under {FLAM_MIN_MS:.0f} ms, or "
                            f"one the fit cannot separate, would look the same")
    return res


# ------------------------------------------------------------------ ranges from Sam's accepted joins

def distribution(values):
    values = sorted(float(v) for v in values)
    if not values:
        return None
    return {"n": len(values), "min": values[0], "median": float(np.median(values)), "max": values[-1], "thin": len(values) < THIN_JOINS}


def ranges_from_scalars(scalar_dicts):
    """Per measurement, the min, median and max across joins (each a dict from scalars()) and the join count; under THIN_JOINS joins is
    marked thin. The tool PROPOSES these."""
    by = {}
    for d in scalar_dicts:
        for name, v in d.items():
            by.setdefault(name, []).append(v)
    return {name: distribution(v) for name, v in sorted(by.items())}


def build_ranges(join_results):
    return ranges_from_scalars([scalars(r) for r in join_results])


# Measures where only one end is a concern: a flam fraction or a tonal step that is smaller than any accepted join is not a
# fault, and neither is a low-end correlation that is higher. They are judged against that one end only.
# low_corr_drop is the overlap's L/R correlation minus the lower of the two sides', so a FALL is NEGATIVE: opposite-polarity or detuned
# basses across the channels read -1 or below, and that (below the range minimum) is the concerning end. A correlation that is higher
# in the overlap than on the sides (positive) is not a fault. Verified both ways on synthetic bounces (test_the_corr_drop_names_...).
ONE_SIDED = {"flam_fraction_of_fitted_beats": "max", "tonal_worst_abs_db": "max", "low_corr_overlap": "min", "low_corr_drop": "min"}


def _range_figures(rng):
    return {"min": rng["min"], "median": rng["median"], "max": rng["max"]}


def judge(result, ranges):
    """Per measurement: 'inside', or 'outside by X' (X in the measurement's own unit, from the nearest end of the range)."""
    out = {}
    for name, v in scalars(result).items():
        rng = ranges.get(name)
        if not rng:
            continue
        if v < rng["min"] and ONE_SIDED.get(name) != "max":
            out[name] = {"value": v, "verdict": "outside", "by": rng["min"] - v, "below": True}
            out[name].update(_range_figures(rng))
        elif v > rng["max"] and ONE_SIDED.get(name) != "min":
            out[name] = {"value": v, "verdict": "outside", "by": v - rng["max"], "below": False}
            out[name].update(_range_figures(rng))
        else:
            out[name] = {"value": v, "verdict": "inside", "by": 0.0}
            out[name].update(_range_figures(rng))
    return out


def verdict_text(judged, ranges):
    if not judged:
        return "no range to judge against"
    n = min(ranges[k]["n"] for k in judged)
    thin = [f"{k} {ranges[k]['n']} joins" for k in judged if ranges[k].get("thin")]
    out = [f"{k} {'below' if v['below'] else 'above'} by {v['by']:.2f} (accepted min {v['min']:.2f}, median {v['median']:.2f}, max {v['max']:.2f})"
           for k, v in judged.items() if v["verdict"] == "outside"]
    base = f"{len(judged)} measurements against the range of the joins Sam accepted (n down to {n})"
    if thin:
        base += f"; THIN, built from under {THIN_JOINS} joins: " + ", ".join(thin)
    return f"inside the observed range ({base})" if not out else "outside: " + "; ".join(out) + f" ({base})"


# ------------------------------------------------------------------ the attention rule

# STARTING FIGURES that Sam will set (like the kick check's constants above). A join "needs a look" only for a measure with a direct
# audible meaning, and only when it is outside the range of the joins Sam accepted by at least the margin. Per-band tonal steps (sub
# included) and worst-bar levels are NOT here: two different tracks differ in tone at every join, so they stay in the report as
# information, and a kick flam that was not measurable, not discriminating or too few beats is not a flag either. The margins were
# not fitted to any bounce.
LOUD_ATTENTION_LU = 1.0         # loudness step and blend peak against the sides
LOW_LEVEL_ATTENTION_DB = 3.0    # 40-150 Hz in the blend against the louder side: a phase hole or a double bass pile-up
LOW_CORR_ATTENTION = 0.05       # fall of the low-end L/R correlation (the concerning end only, ONE_SIDED)
WIDTH_ATTENTION_DB = 3.0
ATTENTION_MARGINS = {"loud_step_lu": LOUD_ATTENTION_LU, "loud_peak_vs_sides_lu": LOUD_ATTENTION_LU,
                     "low_level_vs_sides_db": LOW_LEVEL_ATTENTION_DB, "low_corr_drop": LOW_CORR_ATTENTION,
                     "width_step_db": WIDTH_ATTENTION_DB}
# measure -> (words, unit, decimals)
ATTENTION_WORDS = {"loud_step_lu": ("loud step", " LU", 1), "loud_peak_vs_sides_lu": ("blend peak vs sides", " LU", 1),
                   "low_level_vs_sides_db": ("low end (40-150 Hz) vs sides", " dB", 1),
                   "low_corr_drop": ("low-end L/R correlation change vs sides (negative = fell)", "", 2),
                   "width_step_db": ("width step", " dB", 1)}
# measure -> where the raw figure sits in a join result
ATTENTION_SOURCE = {"loud_step_lu": ("loudness", "step_lu"), "loud_peak_vs_sides_lu": ("loudness", "peak_vs_sides_lu"),
                    "low_level_vs_sides_db": ("low_end", "level_vs_sides_db"), "low_corr_drop": ("low_end", "corr_drop"),
                    "width_step_db": ("width", "step_db")}
MARGIN_EPS = 1e-9               # a margin met to float rounding counts


_TRANSLIT = {"\N{LATIN SMALL LETTER O WITH STROKE}": "o", "\N{LATIN CAPITAL LETTER O WITH STROKE}": "O",
             "\N{LATIN SMALL LETTER AE}": "ae", "\N{LATIN CAPITAL LETTER AE}": "AE", "\N{LATIN SMALL LIGATURE OE}": "oe",
             "\N{LATIN CAPITAL LIGATURE OE}": "OE", "\N{LATIN SMALL LETTER SHARP S}": "ss", "\N{LATIN SMALL LETTER D WITH STROKE}": "d",
             "\N{LATIN CAPITAL LETTER D WITH STROKE}": "D", "\N{LATIN SMALL LETTER L WITH STROKE}": "l",
             "\N{LATIN CAPITAL LETTER L WITH STROKE}": "L", "\N{LATIN SMALL LETTER THORN}": "th", "\N{LATIN SMALL LETTER ETH}": "d",
             "\N{EM DASH}": "-", "\N{EN DASH}": "-", "\N{LEFT SINGLE QUOTATION MARK}": "'", "\N{RIGHT SINGLE QUOTATION MARK}": "'",
             "\N{LEFT DOUBLE QUOTATION MARK}": '"', "\N{RIGHT DOUBLE QUOTATION MARK}": '"', "\N{HORIZONTAL ELLIPSIS}": "..."}


def ascii_text(text):
    """The nearest ASCII spelling (accents dropped from their letter, o-with-stroke to o, dashes to a hyphen), so the md file, the json and the
    screen carry the same words; anything else non-ASCII becomes ?."""
    text = "".join(_TRANSLIT.get(ch, ch) for ch in text)
    plain = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return plain.encode("ascii", "replace").decode("ascii")


def look_reasons(res, ranges=None):
    """Why one join needs a look, in words: each attention measure outside the accepted range by its margin, and a kick flam SEEN."""
    out = []
    judged = judge(res, ranges) if ranges else {}
    for name, margin in ATTENTION_MARGINS.items():
        j = judged.get(name)
        if j and j["verdict"] == "outside" and j["by"] >= margin - MARGIN_EPS:
            words, unit, nd = ATTENTION_WORDS[name]
            end = "min" if j["below"] else "max"
            out.append(f"{words} {j['value']:+.{nd}f}{unit}, outside Sam's accepted range ({end} {j[end]:+.{nd}f}) by {j['by']:.{nd}f}")
    flam = res.get("flam", {})
    if flam.get("state") == "flam_seen":
        out.append(f"kick flam SEEN on {flam['n_flam']}/{flam['n_fit_both']} fitted beats, median {flam['median_spacing_ms']:.1f} ms")
    return out


def _missing_why(res, name):
    """Why one attention measure could not be computed for a join."""
    if "loudness" not in res:
        return "; ".join(res.get("notes", ["not measured"]))
    notes = res.get("notes", [])
    section, key = ATTENTION_SOURCE[name]
    raw = res.get(section, {}).get(key)
    if raw is not None and not math.isfinite(raw):
        return "not a number"
    if name == "width_step_db":
        width = [n for n in notes if n.startswith("width step not measured")]
        if width:
            return width[0]
    if name == "low_corr_drop":
        empty = [w for w, c in res.get("low_end", {}).get("corr", {}).items() if c is None]
        if empty:
            return "no 40-150 Hz energy in one channel of the " + ", ".join(empty) + (" windows" if len(empty) > 1 else " window")
    window = [n for n in notes if "window" in n and "not measured" in n and not n.startswith("width step")]
    return "; ".join(window) if window else "not computed"


def attention(report, ranges=None, out_dir=None):
    """What the building AI must look at: {flagged joins with their reasons, caveats, text}. The check never fails a build; this says
    which joins to open the picture of, and which situations mean the numbers cannot be taken at face value and must never read as clean:
    a start warning, a map that does not fit, a measure that could not be computed for a join (silent or short window, mono side, a
    channel with no low end), any other per-join note, no ranges or a range missing, a thin range, no flam run or none judgeable."""
    joins = report.get("joins", [])
    flagged, caveats = [], []

    def name_of(res):
        j = res["join"]
        return (f"join {j['index']} {short_name(j['out'])} > {short_name(j['in'])} "
                f"({_fmt_time(j['start_s'])}-{_fmt_time(j['end_s'])})")

    def caveat(kind, text):
        caveats.append({"kind": kind, "text": ascii_text(text)})

    mapping = report.get("mapping", {})
    for problem in mapping.get("problems", []):
        caveat("MAP", f"bounce does not fit the arrangement: {problem}")
    for warning in mapping.get("start", {}).get("warnings", []):
        caveat("START WARNING", f"START WARNING: {warning}")
    if not joins:
        caveat("NOT MEASURED", "NOT MEASURED: the arrangement has no joins")
    if not ranges:
        caveat("NOT JUDGED", "NOT JUDGED: no accepted ranges were loaded, so loudness, low end and width were not "
                             "checked against Sam's mixes; only a kick flam can flag")
    else:
        absent = [m for m in ATTENTION_MARGINS if m not in ranges]
        if absent:
            caveat("NOT JUDGED", "NOT JUDGED: the accepted ranges have no entry for " + ", ".join(ATTENTION_WORDS[m][0] for m in absent))
        thin = [m for m in ATTENTION_MARGINS if m in ranges and (ranges[m].get("thin") or ranges[m].get("n", THIN_JOINS) < THIN_JOINS)]
        if thin:
            caveat("THIN", f"THIN: the accepted range is built from under {THIN_JOINS} joins for " +
                   ", ".join(f"{ATTENTION_WORDS[m][0]} ({ranges[m].get('n', '?')} joins)" for m in thin) +
                   ", so a flag or a clean read on it is weak")
    states = [r["flam"].get("state", "not_run") for r in joins if "flam" in r]
    if states and all(s == "not_run" for s in states):
        caveat("NOT RUN", "NOT RUN: kick flam was skipped, so a flam cannot be flagged")
    elif states and not any(s in ("flam_seen", "no_flam_seen") for s in states):
        caveat("FLAM NOT MEASURABLE", "FLAM NOT MEASURABLE: the kick flam could not be judged on any join (information, not a flag): "
                                      "no flam flagged here does not mean none is there")
    for res in joins:
        present = scalars(res) if "loudness" in res else {}
        groups = {}
        for m in ATTENTION_MARGINS:
            if m not in present:
                groups.setdefault(_missing_why(res, m), []).append(ATTENTION_WORDS[m][0])
        if groups:
            caveat("NOT MEASURED", f"NOT MEASURED: {name_of(res)}: " +
                   "; ".join(f"{', '.join(words)} ({why})" for why, words in groups.items()))
        if "loudness" in res:
            for note in res.get("notes", []):
                if "not measured" not in note:
                    caveat("WARNING", f"WARNING: {name_of(res)}: {note}")
        reasons = look_reasons(res, ranges) if "loudness" in res else []
        if reasons:
            pic = str(Path(out_dir) / res["picture"]) if out_dir and res.get("picture") else None
            text = ascii_text(f"NEEDS A LOOK: {name_of(res)}: " + "; ".join(reasons) + (f"; picture: {pic}" if pic else ""))
            flagged.append({"join": res["join"]["index"], "reasons": [ascii_text(r) for r in reasons], "picture": pic, "text": text})
    lines = [c["text"] for c in caveats] + [f["text"] for f in flagged]
    if not flagged:
        lines.append(f"no join needs a look ({len(joins)} joins checked)" +
                     (", but see the warnings above: the check is incomplete or its numbers cannot be trusted" if caveats else ""))
    return {"flagged": flagged, "caveats": caveats, "n_joins": len(joins), "margins": dict(ATTENTION_MARGINS),
            "text": "\n".join(lines) + "\n"}


def attention_summary(att, path=None):
    """The one line that ends the CLI output (and the pipeline log)."""
    n, flagged = att["n_joins"], att["flagged"]
    if flagged:
        line = f"BOUNCE CHECK: {len(flagged)} of {n} joins need a look ({', '.join('J%02d' % f['join'] for f in flagged)})"
    else:
        line = "BOUNCE CHECK: no join needs a look"
    if att["caveats"]:
        line += f"; WARNINGS ({', '.join(dict.fromkeys(c['kind'] for c in att['caveats']))})"
    return line + (f": see {path}" if path and (flagged or att["caveats"]) else "")


def write_attention(att, path):
    Path(path).write_text(ascii_text(att["text"]), encoding="ascii")


# ------------------------------------------------------------------ picture and the whole run

def draw_join(result, hops, path):
    """Band levels, short-term loudness, low-end level and correlation, and width over the join; the overlap shaded."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    join = result["join"]
    s, e = join["start_s"], join["end_s"]
    fig = Figure(figsize=(14, 9))
    FigureCanvasAgg(fig)
    t = hops.t0 + (np.arange(hops.n) + 0.5) * hops.hop_s
    smooth = max(1, int(round(1.0 / hops.hop_s)))

    def sm(v):
        return np.convolve(v, np.ones(smooth) / smooth, mode="same")

    axes = fig.subplots(4, 1, sharex=True)
    for i, (name, _lo, _hi) in enumerate(BANDS):
        axes[0].plot(t, [_db(v) for v in sm(hops.band[:, i])], lw=1.0, label=name)
    axes[0].set_ylabel("band level dB")
    axes[0].legend(ncol=6, fontsize=7, loc="lower center")
    st_t, st = short_term(hops)
    axes[1].plot(st_t + SHORT_TERM_S, st, color="k", lw=1.0)
    axes[1].set_ylabel("short-term LUFS (3 s)")
    low_level = [_db(v) for v in sm((hops.low_ll + hops.low_rr) / 2.0)]
    axes[2].plot(t, low_level, color="tab:red", lw=1.0)
    axes[2].set_ylabel("40-150 Hz level dB", color="tab:red")
    twin = axes[2].twinx()
    win = max(2, int(round(2.0 / hops.hop_s)))
    ones = np.ones(win)
    corr = np.convolve(hops.low_lr, ones, "same") / np.sqrt(np.maximum(np.convolve(hops.low_ll, ones, "same") *
                                                                    np.convolve(hops.low_rr, ones, "same"), 1e-24))
    twin.plot(t, corr, color="tab:blue", lw=0.9)
    twin.set_ylim(-1.05, 1.05)
    twin.set_ylabel("L/R correlation", color="tab:blue")
    axes[3].plot(t, [_db(a) - _db(b) for a, b in zip(sm(hops.side), sm(hops.mid))], color="tab:green", lw=1.0)
    axes[3].set_ylabel("side/mid dB (>150 Hz)")
    axes[3].set_xlabel("seconds in the bounce")
    for ax in axes:
        ax.axvspan(s, e, color="0.85", alpha=0.6, zorder=0)
        ax.axvline(s, color="0.3", lw=0.8)
        ax.axvline(e, color="0.3", lw=0.8)
        ax.grid(alpha=0.25)
    bar = result.get("low_end", {}).get("worst_bar")
    if bar:
        axes[2].axvline(bar["start_s"], color="tab:orange", lw=1.5)
        axes[2].text(bar["start_s"], axes[2].get_ylim()[1], " worst bar", fontsize=7, color="tab:orange", va="top")
    flam = result.get("flam", {})
    flam_text = {"flam_seen": f"kick flam SEEN on {flam.get('n_flam')}/{flam.get('n_fit_both')} fitted beats, median {flam.get('median_spacing_ms') or 0:.1f} ms",
                 "no_flam_seen": "no kick flam seen",
                 "too_few_beats": f"kick flam: {flam.get('n_flam')} of {flam.get('n_fit_both')} fitted beats, too few to call",
                 "not_measurable": "kick flam not measurable on this bounce"}.get(flam.get("state"), "kick flam not run")
    fig.suptitle(f"Join {join['index']}: {join['out']}  ->  {join['in']}   (overlap {_fmt_time(s)} to {_fmt_time(e)}, shaded)" + "\n" + flam_text, fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(str(path), dpi=100)


def first_sound_s(wav, limit_s=GRID_PHASE_SPAN_S):
    """Seconds until the first 0.25 s hop louder than START_LUFS in the first limit_s, or None when there is none."""
    h = analyse_span(wav, 0.0, limit_s)
    if h is None:
        return None
    loud = np.nonzero(-0.691 + 10.0 * np.log10(np.maximum(h.k_l + h.k_r, 1e-12)) > START_LUFS)[0]
    return float(h.t0 + loud[0] * h.hop_s) if len(loud) else None


def grid_phase_ms(wav, tempo, t0, t1):
    """Where the attacks of the >150 Hz band fall against the beat lines of the map over [t0, t1], folded on the beat: milliseconds from a
    beat line, wrapped to half a beat either way (the rise of a 1 ms envelope; the body of a kick peaks 40 to 150 ms late and is no guide).
    None when the folded profile has no clear peak (no steady kick to read). Only an offset over GRID_PHASE_WARN_MS is told apart from the
    few ms real bounces show, and a shift of whole beats is invisible to it."""
    with sf.SoundFile(str(wav)) as f:
        sr = f.samplerate
        i0, i1 = max(0, int(round(t0 * sr))), min(f.frames, int(round(t1 * sr)))
        if i1 - i0 < 8 * sr:
            return None
        sos = signal.butter(4, CLICK_HP_HZ, btype="highpass", fs=sr, output="sos")
        f.seek(i0)
        x = f.read(i1 - i0, dtype="float64", always_2d=True).mean(axis=1)
    env = _box(np.abs(signal.sosfilt(sos, x)), 0.001 * sr)
    env = np.maximum(np.diff(env, prepend=env[0]), 0.0)
    beats = np.asarray(tempo.sec_to_beat(i0 / sr + np.arange(len(env)) / sr))
    frac = (beats + 0.5) % 1.0 - 0.5
    bins = 200
    k = np.minimum(((frac + 0.5) * bins).astype(int), bins - 1)
    prof = np.bincount(k, weights=env, minlength=bins) / np.maximum(np.bincount(k, minlength=bins), 1)
    prof = np.convolve(np.concatenate([prof[-2:], prof, prof[:2]]), np.ones(5) / 5, mode="valid")
    peak = int(np.argmax(prof))
    if prof[peak] < 1.5 * np.median(prof):
        return None
    mid = (i0 + i1) / 2 / sr
    return float(((peak + 0.5) / bins - 0.5) * 60000.0 / float(tempo.bpm_at(float(tempo.sec_to_beat(mid)))))


def check_start(wav, tempo, joins):
    """Is there any sign the bounce does not begin at arrangement beat 0? Leading silence, or kicks that sit off the beat lines."""
    out = {"first_sound_s": first_sound_s(wav), "grid_phase_ms": None, "warnings": []}
    if out["first_sound_s"] is None or out["first_sound_s"] >= START_SILENCE_WARN_S:
        out["warnings"].append("the bounce starts with " + ("no sound in the first minute" if out["first_sound_s"] is None else
                               f"{out['first_sound_s']:.1f} s of silence") + ": if it does not begin at arrangement beat 0 every join is misplaced")
    if joins:
        a = max(0.0, joins[0].start_s - GRID_PHASE_SPAN_S)
        out["grid_phase_ms"] = grid_phase_ms(wav, tempo, a, joins[0].start_s)
        if out["grid_phase_ms"] is not None and abs(out["grid_phase_ms"]) > GRID_PHASE_WARN_MS:
            out["warnings"].append(f"the kick grid sits {out['grid_phase_ms']:+.0f} ms from the beat lines of the map before the first join: "
                                   "the bounce may not start at beat 0")
    return out


def peak_memory_mb():
    try:
        import psutil
        info = psutil.Process().memory_info()
        return float(getattr(info, "peak_wset", info.rss)) / 2 ** 20
    except Exception:
        return None


def check_bounce(wav, tempo, joins, last_beat, out_dir=None, flam=True, ranges=None, progress=None):
    """Measure every join of a bounce. Returns the report dict (pictures written when out_dir is given)."""
    with sf.SoundFile(str(wav)) as f:
        check_channels(f, Path(wav).name)
        sr, duration = f.samplerate, f.frames / f.samplerate
    report = {"bounce": str(wav), "duration_s": duration, "sample_rate": sr,
              "mapping": check_mapping(tempo, last_beat, duration, joins), "joins": []}
    report["mapping"]["start"] = check_start(wav, tempo, joins)
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
    for join in joins:
        if progress:
            progress(f"join {join.index}")
        res = {"join": {"index": join.index, "out": join.out_name, "in": join.in_name, "start_beat": join.start_beat,
                        "end_beat": join.end_beat, "start_s": join.start_s, "end_s": join.end_s}}
        t0 = max(0.0, join.start_s - SIDE_S)
        t1 = min(duration, join.end_s + SIDE_S)
        hops = analyse_span(wav, t0, t1)
        if hops is None:
            res["notes"] = ["no audio for this join in the bounce"]
            report["joins"].append(res)
            continue
        res.update(measure_join(hops, join, tempo, duration))
        res["flam"] = measure_flam(wav, tempo, join) if flam else {"state": "not_run", "notes": []}
        grid = res["flam"].get("grid_offset_ms")
        if grid and max(abs(grid["out"]), abs(grid["in"])) > GRID_OFFSET_WARN_MS:
            res["notes"].append(f"kick grid offset {grid['out']:+.0f} / {grid['in']:+.0f} ms: the beat to seconds map may be off")
        res["scalars"] = scalars(res)
        if ranges:
            res["judged"] = judge(res, ranges)
        if out_dir:
            pic = Path(out_dir) / f"join_{join.index:02d}.png"
            draw_join(res, hops, pic)
            res["picture"] = pic.name
        report["joins"].append(res)
    report["peak_memory_mb"] = peak_memory_mb()
    return report


def summary_line(res, ranges=None):
    j = res["join"]
    head = f"J{j['index']:02d} {short_name(j['out'])} > {short_name(j['in'])}  {_fmt_time(j['start_s'])}-{_fmt_time(j['end_s'])}"
    if "loudness" not in res:
        return head + "  " + "; ".join(res.get("notes", ["not measured"]))
    parts = [head]
    loud, tonal, low, width, flam = res["loudness"], res["tonal"], res["low_end"], res["width"], res.get("flam", {})
    parts.append("loud " + (f"step {loud['step_lu']:+.1f} LU" if "step_lu" in loud else "step n/a") +
                 (f", blend peak {loud['peak_vs_sides_lu']:+.1f} LU vs sides" if "peak_vs_sides_lu" in loud else ""))
    parts.append("tone " + (f"worst {tonal['worst_band']} {tonal['worst_step_db']:+.1f} dB" if "worst_band" in tonal else "n/a"))
    if "level_vs_sides_db" in low:
        bar = low.get("worst_bar")
        corr = low["corr"]["during"]
        parts.append(f"low {low['level_vs_sides_db']:+.1f} dB vs sides, corr " + ("n/a" if corr is None else f"{corr:.2f}") +
                     (f" (change vs sides {low['corr_drop']:+.2f}, negative = fell)" if "corr_drop" in low else "") +
                     (f", worst bar {_fmt_time(bar['start_s'])} {bar['level_vs_sides_db']:+.1f} dB" if bar else ""))
    else:
        parts.append("low n/a")
    parts.append("width " + (f"{width['step_db']:+.1f} dB" if "step_db" in width else "n/a"))
    state = flam.get("state", "not_run")
    if state == "flam_seen":
        parts.append(f"flam SEEN on {flam['n_flam']}/{flam['n_fit_both']} fitted beats ({flam['n_kick_beats']} kick beats), "
                     f"median {flam['median_spacing_ms']:.1f} ms")
    elif state == "no_flam_seen":
        parts.append(f"flam none seen ({flam['n_fit_both']}/{flam['n_kick_beats']} beats fitted)")
    elif state == "too_few_beats":
        parts.append(f"flam too few beats to call: {flam['n_flam']} of {flam['n_fit_both']} fitted beats ({flam['n_kick_beats']} kick beats) "
                     f"fit as flams ({flam['fraction_of_fit_beats']:.0%}), observed not judged")
    else:
        parts.append(f"flam {state.replace('_', ' ')}")
    if ranges and "judged" in res:
        parts.append(verdict_text(res["judged"], ranges))
    for note in res.get("notes", []):
        parts.append(f"[{note}]")
    return " | ".join(parts)


def write_json(report, path):
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items() if k != "fit"}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        if isinstance(o, (np.floating, np.integer, np.bool_)):
            return o.item()
        return o
    Path(path).write_text(json.dumps(clean(report), indent=1), encoding="ascii")
