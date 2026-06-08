"""Stem-based section detector (ANALYSIS ONLY).

A section in dance music *is* a combination of which stems are playing. This
detector reads Demucs stems (in-memory, original WAV never touched) and:

  1. Decides per-beat KICK IN / KICK OUT with a hard threshold on the drums
     (draw a line at KICK_ON_FRAC on the normalised drums panel).
  2. Cuts a new section every time the kick drops out and comes back in (a new
     16-beat phrase), plus where the bass turns on/off (bass-to-bass). Snapped
     to the 16-beat grid.
  3. Labels by song-position: everything before the first drop is INTRO (even a
     bass / no-bass split is just two parts of the intro); after the last drop is
     OUTRO; BREAK only exists in the body between drops.
  4. Emits the signals that make mixes lock:
       - kick_cues    : kick drop-outs (exit/fill cues) + returns (mix-in cues)
       - bass_regions : where bass is present  -> bass-to-bass mix points
       - loop_windows : drums-on / bass-off    -> clean loop material
       - vocal_regions: where vocals sit        -> avoid vocal clash
       - major_cues   : ~1 min in / ~1 min to end, snapped to the nearest boundary

BPM + downbeat come from the existing analysis (blind-viz stats JSON here;
Rekordbox in the live pipeline). Stems are cached as tiny envelopes by
stem_section_probe._separate_envelopes — the slow separation runs once per track.

Usage:
    python Source/stem_detector.py "<project-path>" [--track "<wav name>"]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from stem_section_probe import _separate_envelopes, _load_stats, _seccol

STEMS = ("drums", "bass", "vocals", "other")
STEM_COLORS = {"drums": "#e4572e", "bass": "#2e86ab", "vocals": "#8e44ad", "other": "#3a3a3a"}

# Tuneables (calibrating on VLAD - I'm Glued, then generalising)
KICK_ON_FRAC = 0.80       # a beat is kick-IN if its peak > this fraction of the SOLID kick level
                          # (the full-drop drum level, found dynamically per track — see below)
KICK_SMOOTH_BEATS = 3     # median-smooth per-beat kick on/off (kills 1-beat flicker)
MIN_KICK_OUT_BEATS = 2    # ignore kick-out runs shorter than this (syncopation, not a real drop)
FILL_MAX_BARS = 6         # kick-out <= this many bars = fill; longer = break (Sam's rule)
PRESENCE_FRAC = 0.20      # stem "on" if per-bar energy > this fraction of that stem's peak
STEM_ABSENT_FRAC = 0.04   # ignore a stem entirely if its peak < this fraction of the mix peak
SMOOTH_BARS = 3           # median-smooth bass/vocal presence over this many bars
PHRASE_GRID = 4           # snap section boundaries to multiples of this many bars (4 bars = 16 beats)
MIN_SECTION_BARS = 4      # merge sections shorter than this
DROP_REL = 0.85           # a drop must reach this fraction of the track's FULLEST section energy
                          # (so a bass-heavy intro that isn't full-energy yet stays 'intro')
OUTRO_LEAD_FRAC = 0.60    # outro starts where the LEAD (vocals+other) drops below this fraction
                          # of its body level near the end (kick+bass can keep running)
MIN_OUTRO_BARS = 8        # don't push the outro start so late it leaves less than this
MAX_OUTRO_BARS = 48       # a 'last fill' leaving a longer tail than this isn't an outro transition
MIN_LOOP_BARS = 4
MIN_VOCAL_BARS = 2


def _per_bar(env, hop_t, downbeat, sec_per_bar, n_bars):
    out = np.zeros(n_bars)
    for b in range(n_bars):
        i0 = int((downbeat + b * sec_per_bar) / hop_t)
        i1 = min(int((downbeat + (b + 1) * sec_per_bar) / hop_t), len(env))
        out[b] = env[i0:i1].mean() if i1 > i0 else 0.0
    return out


def _median_bool(x, k):
    if k <= 1:
        return x
    pad = k // 2
    xp = np.pad(x.astype(float), pad, mode="edge")
    return np.array([np.median(xp[i:i + k]) for i in range(len(x))]) >= 0.5


def _regions(mask, min_len=1):
    out, n, i = [], len(mask), 0
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            if j - i >= min_len:
                out.append([i, j])
            i = j
        else:
            i += 1
    return out


def _snap_merge(bounds, n_bars):
    snapped = sorted({0, n_bars} | {int(round(b / PHRASE_GRID) * PHRASE_GRID) for b in bounds})
    snapped = [b for b in snapped if 0 <= b <= n_bars]
    merged = [snapped[0]]
    for b in snapped[1:]:
        if b - merged[-1] >= MIN_SECTION_BARS:
            merged.append(b)
    if merged[-1] != n_bars:
        merged[-1] = n_bars
    return merged


def _solid_kick_level(peaks):
    """The 'solid' kick level = the centre of the LOUD cluster of per-beat drums
    peaks (kick fully in, on drops), found by a quick 2-means split. Reads where
    the drums sit when everything's in — dynamically per track — so the kick-in/out
    call is anchored to the track's own drop level, not a fixed percentile."""
    p = peaks[peaks > 0]
    if len(p) < 4:
        return p.max() if len(p) else 1.0
    lo, hi = p.min(), p.max()
    for _ in range(25):
        mid = 0.5 * (lo + hi)
        low, high = p[p <= mid], p[p > mid]
        if len(low) == 0 or len(high) == 0:
            break
        nlo, nhi = low.mean(), high.mean()
        if abs(nlo - lo) < 1e-7 and abs(nhi - hi) < 1e-7:
            break
        lo, hi = nlo, nhi
    return hi


def _kick_on_per_beat(drums_env, hop_t, bpm, downbeat, n_bars):
    """Per-beat kick IN/OUT, thresholded against the DYNAMIC solid kick level (the
    full-drop drum level from _solid_kick_level) rather than a fixed percentile —
    so one KICK_ON_FRAC generalises across tracks. Median-smoothed to remove
    single-beat flicker. Returns (on[beat], peaks[beat], solid_level)."""
    beat_sec = 60.0 / bpm
    n_beats = n_bars * 4
    peaks = np.zeros(n_beats)
    for k in range(n_beats):
        i0 = int((downbeat + k * beat_sec) / hop_t)
        i1 = min(int((downbeat + (k + 1) * beat_sec) / hop_t), len(drums_env))
        peaks[k] = drums_env[i0:i1].max() if i1 > i0 else 0.0
    ref = _solid_kick_level(peaks) + 1e-9
    on = _median_bool(peaks > KICK_ON_FRAC * ref, KICK_SMOOTH_BEATS)
    return on, peaks, ref


def _kick_cues(kick_on, bpm, downbeat):
    """Kick transitions = cue points, but only for kick-OUT runs of at least
    MIN_KICK_OUT_BEATS (a real drop, not syncopation). Each run yields a
    kick_dropout (exit/fill cue) at its start and a kick_return (mix-in cue, the
    start of a new 16-beat section) at its end."""
    beat_sec = 60.0 / bpm
    n, k, cues = len(kick_on), 0, []
    while k < n:
        if not kick_on[k] and k > 0 and kick_on[k - 1]:
            j = k
            while j < n and not kick_on[j]:
                j += 1
            if j - k >= MIN_KICK_OUT_BEATS:
                cues.append({"type": "kick_dropout", "start_sec": round(downbeat + k * beat_sec, 2),
                             "beat": k, "bar": round(k / 4.0, 2)})
                if j < n:
                    cues.append({"type": "kick_return", "start_sec": round(downbeat + j * beat_sec, 2),
                                 "beat": j, "bar": round(j / 4.0, 2)})
            k = j
        else:
            k += 1
    return cues


def _find_outro_start(pb, n_bars):
    """The outro is the DJ wind-down where the LEAD (vocals + 'other'/effects)
    drops out near the end — even if kick + bass keep running. Returns the start
    bar of the trailing low-lead run, or None if there isn't a clear one."""
    lead = pb["vocals"] + pb["other"]
    lead = lead / (lead.max() + 1e-9)
    hi = lead[lead > np.median(lead)]
    body = np.median(hi) if len(hi) else lead.max()
    thr = OUTRO_LEAD_FRAC * body
    i = n_bars
    while i > 0 and lead[i - 1] < thr:
        i -= 1
    if i >= n_bars or i <= n_bars * 0.5:    # nothing, or too long to be an outro
        return None
    return int(round(i / PHRASE_GRID) * PHRASE_GRID)


def _assign_labels(sections, kick_on_bar, bass_pres, mix_norm, outro_start):
    """Label by song position: intro before the first drop; outro from the lead
    drop-off near the end; drop/break/build in the body. A bass / no-bass split
    inside the intro stays INTRO. Every track is guaranteed an intro and outro."""
    def stat(s):
        s0, s1 = s["start_bar"], s["end_bar"]
        kf = kick_on_bar[s0:s1].mean() if s1 > s0 else 0.0
        bf = bass_pres[s0:s1].mean() if s1 > s0 else 0.0
        ef = mix_norm[s0:s1].mean() if s1 > s0 else 0.0
        return kf, bf, ef

    n = len(sections)
    full = max((stat(s)[2] for s in sections), default=1.0)
    drop_thr = DROP_REL * full

    def is_drop(s):
        kf, bf, ef = stat(s)
        return kf > 0.6 and bf > 0.5 and ef >= drop_thr

    first_drop = next((i for i in range(n) if is_drop(sections[i])), None)

    for i, s in enumerate(sections):
        kf, bf, ef = stat(s)
        if outro_start is not None and s["start_bar"] >= outro_start:
            label = "outro"
        elif first_drop is None:
            label = "intro" if i < n / 2 else "outro"
        elif i < first_drop:
            # Pre-drop is intro — EXCEPT a long kick drop-out, which is a 'first
            # break' (the drums all come out) even with no bass before it. A short
            # kick-out stays a fill; an intro where the kick never drops stays intro.
            is_long = (s["end_bar"] - s["start_bar"]) > FILL_MAX_BARS
            label = "break" if (kf < 0.4 and is_long) else "intro"
        elif is_drop(s):
            label = "drop"
        elif bf < 0.4 or kf < 0.4:
            # kick/bass out: short = fill, long = break (Sam's 6-bar rule of thumb)
            label = "fill" if (s["end_bar"] - s["start_bar"]) <= FILL_MAX_BARS else "break"
        else:
            label = "build"
        s["label"] = label

    # Hard rule: every track has an intro AND an outro.
    if n:
        if sections[0]["label"] != "intro":
            sections[0]["label"] = "intro"
        if not any(s["label"] == "outro" for s in sections):
            sections[-1]["label"] = "outro"


def _merge_same_label(sections):
    """Merge consecutive same-label sections into one block (internal stem/kick
    changes survive as cue points, not as extra section splits)."""
    if not sections:
        return sections
    kickout = {"break", "fill"}
    merged = [dict(sections[0])]
    for s in sections[1:]:
        prev = merged[-1]
        # Merge consecutive same-label blocks, AND adjacent kick-out sections
        # (break+fill are one contiguous kick-out region) — but never drops, which
        # stay split at their fills. So an edge 'fill' touching a break folds into
        # the break; only a short kick-out flanked by drops stays a fill.
        mergeable = (
            prev["label"] != "drop" and s["label"] != "drop"
            and (s["label"] == prev["label"]
                 or (s["label"] in kickout and prev["label"] in kickout))
        )
        if mergeable:
            prev["end_bar"] = s["end_bar"]
            prev["end_sec"] = s["end_sec"]
            prev["stems_on"] = sorted(set(prev["stems_on"]) | set(s["stems_on"]))
        else:
            merged.append(dict(s))
    for s in merged:   # a contiguous kick-out region is a fill if short, else a break
        if s["label"] in kickout:
            s["label"] = "fill" if (s["end_bar"] - s["start_bar"]) <= FILL_MAX_BARS else "break"
    counts = {}
    for s in merged:
        counts[s["label"]] = counts.get(s["label"], 0) + 1
        s["name"] = f"{s['label']}_{counts[s['label']]}"
    return merged


def detect(wav: Path, project: Path):
    stats = _load_stats(next((project / "Sections Review").glob("Blind_V*")), wav.stem)
    if not stats:
        print(f"  [skip] no stats (bpm/downbeat) for {wav.stem}")
        return None
    bpm = stats["bpm"]
    downbeat = stats.get("first_downbeat_sec", 0.0)
    sec_per_bar = 4 * 60.0 / bpm

    envs, hop_t = _separate_envelopes(wav, project / "_Stem Analysis")
    dur_real = len(envs["mix"]) * hop_t
    n_bars = max(1, int((dur_real - downbeat) / sec_per_bar))

    pb = {s: _per_bar(envs[s], hop_t, downbeat, sec_per_bar, n_bars) for s in STEMS}
    mix_norm = _per_bar(envs["mix"], hop_t, downbeat, sec_per_bar, n_bars)
    mix_norm = mix_norm / (mix_norm.max() + 1e-9)

    mix_peak = envs["mix"].max() + 1e-9
    presence = {}
    for s in STEMS:
        peak = pb[s].max()
        if peak < STEM_ABSENT_FRAC * mix_peak:
            presence[s] = np.zeros(n_bars, dtype=bool)
        else:
            presence[s] = _median_bool(pb[s] > PRESENCE_FRAC * peak, SMOOTH_BARS)

    # Per-beat kick IN/OUT (robust threshold) + per-bar kick presence + cues.
    kick_on, kick_peaks, kick_ref = _kick_on_per_beat(envs["drums"], hop_t, bpm, downbeat, n_bars)
    kick_on_bar = np.array([kick_on[b * 4:(b + 1) * 4].mean() >= 0.5 for b in range(n_bars)])
    kick_cues = _kick_cues(kick_on, bpm, downbeat)

    # A fill = a SHORT kick drop-out then return. Sam's rule: longer than
    # FILL_MAX_BARS of kick-out is a break, not a fill (the break is its own section).
    max_fill_sec = FILL_MAX_BARS * sec_per_bar
    fills = [[a["start_sec"], b["start_sec"]] for a, b in zip(kick_cues, kick_cues[1:])
             if a["type"] == "kick_dropout" and b["type"] == "kick_return"
             and b["start_sec"] - a["start_sec"] <= max_fill_sec]

    # Outro start: the lead (vocals+other) drop near the end. But Sam's rule of
    # thumb — the END OF THE LAST FILL is the outro start — wins when that fill is a
    # plausible outro transition: after any lead-drop, OR (no lead-drop at all) just
    # near the end, leaving a sensible-length tail. The start snaps UP to the phrase
    # line after the fill, so the fill stays in the preceding section and the outro
    # begins cleanly after it. (VLAD's last fill precedes its lead-drop, so its
    # lead-drop still wins.)
    outro_start = _find_outro_start(pb, n_bars)
    if fills:
        lf = int(np.ceil((fills[-1][1] - downbeat) / sec_per_bar / PHRASE_GRID)) * PHRASE_GRID
        outro_len = n_bars - lf
        # Use the fill if there's no lead-drop, or the fill is at/after it, or it's
        # within ~a phrase BEFORE it (same transition, snapping noise apart). A fill
        # far before the lead-drop is mid-body, so the lead-drop wins (VLAD).
        if (MIN_OUTRO_BARS <= outro_len <= MAX_OUTRO_BARS
                and (outro_start is None or lf >= outro_start - 2 * PHRASE_GRID)):
            outro_start = lf

    # Boundaries: a kick drop-out + return marks a new 16-beat section (Sam's
    # rule), plus bass on/off (bass-to-bass) and the outro lead-drop. Snapped to grid.
    raw_bounds = [int(round(c["bar"])) for c in kick_cues]
    for b in range(1, n_bars):
        if presence["bass"][b] != presence["bass"][b - 1]:
            raw_bounds.append(b)
    if outro_start:
        raw_bounds.append(outro_start)
    bounds = _snap_merge(sorted({b for b in raw_bounds if 0 < b < n_bars}), n_bars)

    sections = []
    for i in range(len(bounds) - 1):
        s0, s1 = bounds[i], bounds[i + 1]
        sections.append({
            "start_bar": s0, "end_bar": s1,
            "start_sec": round(downbeat + s0 * sec_per_bar, 2),
            "end_sec": round(downbeat + s1 * sec_per_bar, 2),
            "stems_on": [s for s in STEMS if presence[s][s0:s1].mean() > 0.5],
        })
    _assign_labels(sections, kick_on_bar, presence["bass"], mix_norm, outro_start)
    sections = _merge_same_label(sections)

    # A tiny drop fragment (<= one phrase) right before the outro is the last
    # fill's block, not a real drop — fold it into the preceding drop so it reads
    # drop -> outro (the fill stays at the drop's tail).
    if (len(sections) >= 3 and sections[-1]["label"] == "outro"
            and sections[-2]["label"] == "drop" and sections[-3]["label"] == "drop"
            and sections[-2]["end_bar"] - sections[-2]["start_bar"] <= PHRASE_GRID):
        sections[-3]["end_bar"] = sections[-2]["end_bar"]
        sections[-3]["end_sec"] = sections[-2]["end_sec"]
        sections[-3]["stems_on"] = sorted(set(sections[-3]["stems_on"]) | set(sections[-2]["stems_on"]))
        del sections[-2]
        counts = {}
        for s in sections:
            counts[s["label"]] = counts.get(s["label"], 0) + 1
            s["name"] = f"{s['label']}_{counts[s['label']]}"

    def to_sec(regs):
        return [[round(downbeat + a * sec_per_bar, 2), round(downbeat + b * sec_per_bar, 2), a, b]
                for a, b in regs]

    boundaries_sec = [s["start_sec"] for s in sections] + ([sections[-1]["end_sec"]] if sections else [])

    def _nearest(target):
        return min(boundaries_sec, key=lambda x: abs(x - target)) if boundaries_sec else target

    major_cues = []
    if dur_real > 130 and boundaries_sec:
        major_cues = [
            {"type": "one_min_in", "sec": _nearest(60.0), "guide": 60.0},
            {"type": "one_min_to_end", "sec": _nearest(dur_real - 60.0), "guide": round(dur_real - 60.0, 2)},
        ]

    signals = {
        "bass_regions": to_sec(_regions(presence["bass"], 1)),
        "loop_windows": to_sec(_regions(presence["drums"] & ~presence["bass"], MIN_LOOP_BARS)),
        "vocal_regions": to_sec(_regions(presence["vocals"], MIN_VOCAL_BARS)),
        "kick_cues": kick_cues,
        "fills": fills,
        "major_cues": major_cues,
    }

    result = {"track": wav.stem, "bpm": round(bpm, 2), "n_bars": n_bars,
              "sections": sections, "signals": signals}
    (project / "_Stem Analysis" / f"SECTIONS_STEM_{wav.stem}.json").write_text(
        json.dumps(result, indent=1), encoding="utf-8")

    _visualize(wav, project, envs, hop_t, downbeat, sec_per_bar, n_bars, sections, signals, kick_ref)

    old_n = len(stats["sections"])
    n_drop = sum(1 for c in kick_cues if c["type"] == "kick_dropout")
    print(f"  {wav.stem[:46]:46}  old {old_n:2d} -> stem {len(sections):2d} secs | "
          f"kick-drops {n_drop} loops {len(signals['loop_windows'])} vocals {len(signals['vocal_regions'])}")
    return result


def _visualize(wav, project, envs, hop_t, downbeat, sec_per_bar, n_bars, sections, signals, kick_ref=None):
    order = [s for s in STEMS if s in envs]
    L = min(len(envs[s]) for s in order + ["mix"])
    t = np.arange(L) * hop_t
    dur = L * hop_t
    bpm = 240.0 / sec_per_bar
    mmss = lambda s: f"{int(s) // 60}:{int(s) % 60:02d}"
    kick_out = [c["start_sec"] for c in signals.get("kick_cues", []) if c["type"] == "kick_dropout"]
    kick_in = [c["start_sec"] for c in signals.get("kick_cues", []) if c["type"] == "kick_return"]
    major = signals.get("major_cues", [])

    fig, axes = plt.subplots(len(order) + 1, 1, figsize=(20, 10), sharex=True,
                             gridspec_kw={"height_ratios": [2.6] + [1] * len(order)})

    def overlays(ax, label_sections=False):
        for bar in range(0, n_bars + 1, PHRASE_GRID):
            ax.axvline(downbeat + bar * sec_per_bar, color="#d2d2d2", lw=0.3, alpha=0.6, zorder=0)
        for sec in sections:
            ax.axvspan(sec["start_sec"], sec["end_sec"], color=_seccol(sec["label"]), alpha=0.15, lw=0, zorder=1)
            ax.axvline(sec["start_sec"], color="k", lw=0.7, alpha=0.5, zorder=2)
            if label_sections:
                nb = sec["end_bar"] - sec["start_bar"]
                ax.text(0.5 * (sec["start_sec"] + sec["end_sec"]), 0.5,
                        f"{sec['label']}\n{nb}b", rotation=0, fontsize=8,
                        va="center", ha="center", zorder=7, fontweight="bold",
                        bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="none", alpha=0.78))
        for x in kick_out:
            ax.axvline(x, color="#d4a017", lw=1.0, alpha=0.9, ls=(0, (2, 1)), zorder=3)   # gold = kick OUT
        for x in kick_in:
            ax.axvline(x, color="#17a2b8", lw=1.0, alpha=0.8, ls=(0, (1, 1)), zorder=3)   # teal = kick IN
        for mc in major:
            ax.axvline(mc["sec"], color="#d6006d", lw=1.7, alpha=0.95, ls="--", zorder=4)

    mix = envs["mix"][:L]
    axes[0].fill_between(t, mix / (mix.max() + 1e-9), color="#222", alpha=0.22, zorder=1)
    axes[0].set_ylabel("TRACK", fontsize=9, fontweight="bold")
    axes[0].set_ylim(0, 1.05)
    axes[0].set_title(f"{wav.stem}    [{len(sections)} sections · {mmss(dur)} · {bpm:.0f} BPM]", fontsize=11)
    overlays(axes[0], label_sections=True)
    for a, b, *_ in signals.get("loop_windows", []):
        axes[0].axvspan(a, b, ymin=0.0, ymax=0.03, color="#2e9e5b", alpha=0.95, lw=0, zorder=6)
    for a, b, *_ in signals.get("vocal_regions", []):
        axes[0].axvspan(a, b, ymin=0.95, ymax=1.0, color="#8e44ad", alpha=0.75, lw=0, zorder=6)
    for a, b in signals.get("fills", []):                       # fill = kick-out -> kick-in
        axes[0].axvspan(a, b, color="#ff8c00", alpha=0.6, lw=0, zorder=6)
        axes[0].text(0.5 * (a + b), 0.5, "fill", rotation=90, fontsize=6, color="#a65300",
                     ha="center", va="center", fontweight="bold", zorder=7)
    for mc in major:
        lab = "~1:00 in" if mc["type"] == "one_min_in" else "~1:00 to end"
        axes[0].text(mc["sec"], 1.07, lab, fontsize=7, color="#d6006d", ha="center", va="bottom", fontweight="bold")
    for bar in range(0, n_bars + 1, 16):
        axes[0].text(downbeat + bar * sec_per_bar, 0.07, str(bar), fontsize=9, color="#000",
                     ha="center", va="bottom", zorder=8, fontweight="bold",
                     bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.75))

    for i, s in enumerate(order):
        ax = axes[i + 1]
        e = envs[s][:L]
        # drums normalised by the kick reference so the 0.55 line means kick in/out
        norm = (kick_ref if (s == "drums" and kick_ref) else e.max() + 1e-9)
        ax.fill_between(t, np.clip(e / norm, 0, 1.05), color=STEM_COLORS[s], alpha=0.85, zorder=1)
        if s == "drums":
            ax.axhline(KICK_ON_FRAC, color="#000", lw=0.9, ls=":", alpha=0.8, zorder=4)  # kick in/out line
        ax.set_ylabel(s, fontsize=9, color=STEM_COLORS[s], fontweight="bold")
        ax.set_ylim(0, 1.05)
        overlays(ax)

    xt = np.arange(0, dur, 30)
    axes[-1].set_xticks(xt)
    axes[-1].set_xticklabels([mmss(x) for x in xt], fontsize=8)
    axes[-1].set_xlabel("time (mm:ss)   ·   orange=fill (kick out→in)   ·   gold=kick OUT   ·   teal=kick IN   ·   "
                        "magenta=~1min in/out   ·   green=loop window   ·   purple=vocals   ·   "
                        f"dotted line on drums = {KICK_ON_FRAC} kick threshold")
    fig.tight_layout()
    fig.savefig(project / "_Stem Analysis" / f"DETECT_{wav.stem}.png", dpi=95)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--track", type=str, default=None)
    args = ap.parse_args()
    audio = args.project / "Audio"
    wavs = [audio / args.track] if args.track else sorted(audio.glob("*.wav"))
    print(f"Stem section detection on {len(wavs)} track(s):")
    for w in wavs:
        detect(w, args.project)


if __name__ == "__main__":
    main()
