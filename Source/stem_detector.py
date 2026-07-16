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

Optional:
    --kick-model replaces only the kick presence signal with Kick Detector V3.
    Bass/vocal/loop/fill logic stays on the existing stem-envelope path.
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
from automated_dj_mixes.musical_landmarks import extract_kick_dropout_landmarks
from automated_dj_mixes.display_sections import refine_intro_drop_boundary

STEMS = ("drums", "bass", "vocals", "other")
STEM_COLORS = {"drums": "#e4572e", "bass": "#2e86ab", "vocals": "#8e44ad", "other": "#3a3a3a"}

# Tuneables (calibrating on VLAD - I'm Glued, then generalising)
KICK_ON_FRAC = 0.80       # a beat is kick-IN if its peak > this fraction of the SOLID kick level
                          # (the full-drop drum level, found dynamically per track — see below)
KICK_SMOOTH_BEATS = 3     # median-smooth per-beat kick on/off (kills 1-beat flicker)
MIN_KICK_OUT_BEATS = 2    # ignore kick-out runs shorter than this (syncopation, not a real drop)
FILL_MAX_BARS = 6         # kick-out <= this many bars = fill; longer = break (Sam's rule)
FILL_MAX_BEATS = 8        # a SHORT raw-kick dip up to this many beats is a phrase fill; longer = break
FILL_DIP_FRAC = 0.55      # a fill beat's kick must fall below this fraction of the solid level (real drop, not velocity)
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
OUTRO_CAP_BARS = 32       # Sam's rule (24.06.26): an outro is never more than 32 bars (one phrase) — cap it there
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


def _model_kick_presence_per_beat(wav, bpm, downbeat, n_beats, kick_model_path=None,
                                  kick_model_device="auto", kick_provider=None,
                                  drums_mono=None, drums_sr=None):
    """Optional learned kick-presence path.

    The provider/model returns beat-level kick IN/OUT only. The caller keeps the
    old energy peaks/ref for fills and visual thresholding, so flag-on changes
    only the section-facing kick presence signal.
    """
    provider = kick_provider
    if provider is None:
        from kick_model_adapter import get_provider
        provider = get_provider(model_path=kick_model_path, device=kick_model_device)
    if drums_mono is not None and hasattr(provider, "presence_per_beat_from_drums"):
        readout = provider.presence_per_beat_from_drums(
            np.asarray(drums_mono, dtype=np.float32), int(drums_sr),
            bpm=float(bpm), downbeat=float(downbeat), n_beats=int(n_beats),
        )
        section_on = np.asarray(readout.section, dtype=bool)
        landmark_on = np.asarray(readout.raw, dtype=bool)
    elif hasattr(provider, "presence_per_beat"):
        readout = provider.presence_per_beat(
            Path(wav), bpm=float(bpm), downbeat=float(downbeat), n_beats=int(n_beats)
        )
        section_on = np.asarray(readout.section, dtype=bool)
        landmark_on = np.asarray(readout.raw, dtype=bool)
    elif drums_mono is not None and hasattr(provider, "on_per_beat_from_drums"):
        section_on = np.asarray(provider.on_per_beat_from_drums(
            np.asarray(drums_mono, dtype=np.float32), int(drums_sr),
            bpm=float(bpm), downbeat=float(downbeat), n_beats=int(n_beats),
        ), dtype=bool)
        landmark_on = section_on.copy()
    else:
        section_on = np.asarray(provider.on_per_beat(
            Path(wav), bpm=float(bpm), downbeat=float(downbeat), n_beats=int(n_beats)
        ), dtype=bool)
        landmark_on = section_on.copy()

    def fit(values):
        if len(values) < n_beats:
            return np.pad(values, (0, n_beats - len(values)), constant_values=False)
        return values[:n_beats]

    return fit(section_on), fit(landmark_on)


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


def _phrase_fills(peaks, ref, bpm, downbeat, n_bars):
    """Brief kick dips inside the grooves = phrase-end FILLS (Sam 24.06.26: "those
    dropouts signify a change... good points to make changes"). Detected on the RAW
    per-beat kick, NOT the 3-beat-smoothed kick_on — the smoothing irons out the 1-beat
    fills that mark 16-bar phrase ends (Mr V drops a single kick beat at bars 115/131/147;
    only the 2-beat dip at 99 survived smoothing, so 3 of 4 fills were lost). A run of
    1..FILL_MAX_BEATS soft beats (kick below FILL_DIP_FRAC of the solid level), flanked by
    kick-in and inside the track, is a fill; longer runs are breaks (their own sections)."""
    soft = (np.asarray(peaks, float) / (ref + 1e-9)) < FILL_DIP_FRAC
    beat_sec = 60.0 / bpm
    fills, k, n = [], 0, len(soft)
    while k < n:
        if soft[k] and k > 0 and not soft[k - 1]:
            j = k
            while j < n and soft[j]:
                j += 1
            if 1 <= (j - k) <= FILL_MAX_BEATS and j < n:    # flanked by kick-in, not a trailing fade
                fills.append([round(downbeat + k * beat_sec, 2), round(downbeat + j * beat_sec, 2)])
            k = j
        else:
            k += 1
    return fills


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
        if not any(s["label"] == "outro" for s in sections) and sections[-1]["label"] != "drop":
            # Never relabel a full DROP as outro just to guarantee one (Sam 24.06.26):
            # a track that ends on a drop simply has no outro — the next track mixes in.
            sections[-1]["label"] = "outro"
        # Intro is TOP-ONLY: a later section relabelled 'intro' (a breakdown inside a long
        # intro, before the first drop) is a BUILD into the drop, not a second intro — Sam's
        # corpus flagged "All Parties" reading intro16 break12 intro4 drop32. Keep the first.
        intro_seen = False
        for s in sections:
            if s["label"] == "intro":
                if intro_seen:
                    s["label"] = "build"
                intro_seen = True


def _merge_same_label(sections):
    """Merge consecutive same-label sections into one block (internal stem/kick
    changes survive as cue points, not as extra section splits)."""
    if not sections:
        return sections
    kickout = {"break", "fill"}
    # Pre-pass: a 'build' that doesn't lead INTO a drop is a mis-detection (you
    # don't build into an outro/break) — relabel it 'drop' so it folds into the
    # surrounding drop (James Poole's phantom build128-132 before the outro).
    sections = [dict(s) for s in sections]
    for k in range(len(sections)):
        if sections[k]["label"] == "build":
            nxt = sections[k + 1]["label"] if k + 1 < len(sections) else None
            if nxt != "drop":
                sections[k]["label"] = "drop"
    merged = [dict(sections[0])]
    for s in sections[1:]:
        prev = merged[-1]
        # Merge consecutive same-label blocks (INCLUDING drops — a long drop split
        # into 8-bar chunks is one drop, Sam 2026-06-09), AND adjacent kick-out
        # sections (break+fill are one contiguous kick-out region). A drop broken
        # by a real kick-out keeps that kick-out as a separate fill/break marker
        # between the two drops; only truly adjacent same-label blocks fold together.
        mergeable = (
            s["label"] == prev["label"]
            or (s["label"] in kickout and prev["label"] in kickout)
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


def detect(wav: Path, project: Path, bpm=None, downbeat=None, make_viz=True, write_json=True,
           kick_model=False, kick_model_path=None, kick_model_device="auto",
           kick_provider=None):
    """Detect sections + mix signals for one track.

    bpm/downbeat may be passed in (pipeline use — they come from Rekordbox/analysis);
    if omitted they're read from the Blind_V stats JSON (standalone CLI). make_viz /
    write_json let a caller skip the PNG / JSON side-artifacts.
    """
    stats = None
    if bpm is None or downbeat is None:
        review = project / "Sections Review"
        blind = next(review.glob("Blind_V*"), None) if review.exists() else None
        stats = _load_stats(blind, wav.stem) if blind else None
        if not stats:
            print(f"  [skip] no stats (bpm/downbeat) for {wav.stem}")
            return None
        bpm = stats["bpm"]
        downbeat = stats.get("first_downbeat_sec", 0.0)
    sec_per_bar = 4 * 60.0 / bpm

    model_drums = None
    model_drums_sr = None
    model_provider = kick_provider
    if kick_model and kick_provider is None:
        from kick_model_adapter import get_provider, separate_envelopes_and_drums
        model_provider = get_provider(model_path=kick_model_path, device=kick_model_device)
        envs, hop_t, model_drums, model_drums_sr = separate_envelopes_and_drums(
            wav, project / "_Stem Analysis", device=kick_model_device
        )
    else:
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

    # Per-beat kick IN/OUT + per-bar kick presence + cues. The default remains
    # the robust energy threshold. --kick-model replaces only kick_on with the
    # learned V3 presence readout; kick_peaks/ref stay energy-based so fills and
    # the visual threshold line remain unchanged.
    kick_on, kick_peaks, kick_ref = _kick_on_per_beat(envs["drums"], hop_t, bpm, downbeat, n_bars)
    landmark_kick_on = kick_on.copy()
    kick_source = "stem-energy-threshold"
    if kick_model or kick_provider is not None:
        kick_on, landmark_kick_on = _model_kick_presence_per_beat(
            wav, bpm, downbeat, n_bars * 4,
            kick_model_path=kick_model_path,
            kick_model_device=kick_model_device,
            kick_provider=model_provider,
            drums_mono=model_drums,
            drums_sr=model_drums_sr,
        )
        kick_source = "kick-detector-v3"
    kick_on_bar = np.array([kick_on[b * 4:(b + 1) * 4].mean() >= 0.5 for b in range(n_bars)])
    kick_cues = _kick_cues(kick_on, bpm, downbeat)

    # Fills = brief raw-kick dips inside the grooves (1-beat phrase-end fills included —
    # see _phrase_fills). Sam: kick dropouts mark changes and are good points to mix.
    fills = _phrase_fills(kick_peaks, kick_ref, bpm, downbeat, n_bars)

    # Outro start: the lead (vocals+other) drop near the end. But Sam's rule of
    # thumb — the END OF THE LAST FILL is the outro start — wins when that fill is a
    # plausible outro transition: after any lead-drop, OR (no lead-drop at all) just
    # near the end, leaving a sensible-length tail. The start snaps UP to the phrase
    # line after the fill, so the fill stays in the preceding section and the outro
    # begins cleanly after it. (VLAD's last fill precedes its lead-drop, so its
    # lead-drop still wins.)
    # OUTRO = everything after the last BODY bar (kick AND bass both on). Sam (24.06.26):
    # the outro starts where the bass finishes and the drums carry on ("lots of beats, no
    # bass = outro"); a kick+bass groove with the vocal stripped is still the BODY (Mr V
    # bars 149-163); and you NEVER get "a break then an outro" at the end — it's all just
    # one outro. A break has kick OR bass out, so it falls after the last body bar and the
    # protected boundary below sweeps the whole tail (break + drums-tail) into one outro.
    body_idx = np.where(kick_on_bar & presence["bass"])[0]
    outro_start = (int(body_idx[-1]) + 1) if (len(body_idx) and int(body_idx[-1]) + 1 < n_bars) else None

    # Sam's rule (24.06.26): an outro is never more than 32 bars (one phrase). The bass can
    # finish many bars deep (a long bass-out drums/perc passage), but THAT is a body section,
    # not the outro — an 80-bar 'outro' is ~2.7 min, clearly wrong. Cap the outro to the final
    # OUTRO_CAP_BARS: pull the start forward to that window (which also lands it on a phrase
    # line); whatever bass-out section sits before it stays body. Short outros are untouched.
    if outro_start is not None:
        cap = n_bars - OUTRO_CAP_BARS
        if cap > 0 and outro_start < cap:
            outro_start = cap

    # Boundaries: a kick drop-out + return marks a new 16-beat section (Sam's
    # rule), plus bass on/off (bass-to-bass) and the outro lead-drop. Snapped to grid.
    raw_bounds = [int(round(c["bar"])) for c in kick_cues]
    for b in range(1, n_bars):
        if presence["bass"][b] != presence["bass"][b - 1]:
            raw_bounds.append(b)
    bounds = _snap_merge(sorted({b for b in raw_bounds if 0 < b < n_bars}), n_bars)
    if outro_start is not None and 0 < outro_start < n_bars:
        # Protected, UNSNAPPED final boundary AT the bass-finish point — Sam wants the
        # outro exactly where the bass ends, and the file rarely ends on a phrase line,
        # so don't let _snap_merge round it onto the body or MIN_SECTION_BARS-merge the
        # (often short) tail away — which then let the hard rule relabel the final DROP
        # as outro (Delacour/Discosteps).
        bounds = [b for b in bounds if b < outro_start] + [outro_start, n_bars]

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

    # Bass IN / OUT — the single biggest natural mix markers (Sam, 2026-06-08):
    # bass-to-bass is the best mix point, and bass entry/exit do NOT track the
    # drop/outro (bass can enter mid-intro, leave mid-outro / on a fill). Read
    # straight from the bass envelope: first bass-on bar, end of last bass-on bar.
    bass_on = np.where(presence["bass"])[0]
    bass_in_sec = round(downbeat + int(bass_on[0]) * sec_per_bar, 2) if len(bass_on) else None
    bass_out_sec = round(downbeat + (int(bass_on[-1]) + 1) * sec_per_bar, 2) if len(bass_on) else None

    musical_landmarks = extract_kick_dropout_landmarks(
        landmark_kick_on,
        kick_on,
        sections,
        bpm=bpm,
        downbeat=downbeat,
        kick_peaks=kick_peaks,
        kick_reference=kick_ref,
        source=("kick-detector-v3-raw" if kick_source == "kick-detector-v3"
                else "stem-energy-threshold"),
    )
    sections, intro_refinement = refine_intro_drop_boundary(
        sections,
        musical_landmarks,
        bpm=bpm,
        downbeat=downbeat,
    )
    if intro_refinement is not None:
        musical_landmarks = extract_kick_dropout_landmarks(
            landmark_kick_on,
            kick_on,
            sections,
            bpm=bpm,
            downbeat=downbeat,
            kick_peaks=kick_peaks,
            kick_reference=kick_ref,
            source=("kick-detector-v3-raw" if kick_source == "kick-detector-v3"
                    else "stem-energy-threshold"),
        )
        boundaries_sec = [s["start_sec"] for s in sections] + [sections[-1]["end_sec"]]
        if dur_real > 130:
            major_cues = [
                {"type": "one_min_in", "sec": _nearest(60.0), "guide": 60.0},
                {"type": "one_min_to_end", "sec": _nearest(dur_real - 60.0),
                 "guide": round(dur_real - 60.0, 2)},
            ]

    signals = {
        "bass_in": bass_in_sec,
        "bass_out": bass_out_sec,
        "bass_regions": to_sec(_regions(presence["bass"], 1)),
        "loop_windows": to_sec(_regions(presence["drums"] & ~presence["bass"], MIN_LOOP_BARS)),
        "vocal_regions": to_sec(_regions(presence["vocals"], MIN_VOCAL_BARS)),
        "kick_cues": kick_cues,
        "kick_presence_source": kick_source,
        "musical_landmarks": musical_landmarks,
        "section_refinements": (
            [intro_refinement] if intro_refinement is not None else []
        ),
        "fills": fills,
        "major_cues": major_cues,
    }

    result = {"track": wav.stem, "bpm": round(bpm, 2), "n_bars": n_bars,
              "sections": sections, "signals": signals}
    if write_json:
        (project / "_Stem Analysis").mkdir(parents=True, exist_ok=True)
        (project / "_Stem Analysis" / f"SECTIONS_STEM_{wav.stem}.json").write_text(
            json.dumps(result, indent=1), encoding="utf-8")

    if make_viz:
        _visualize(wav, project, envs, hop_t, downbeat, sec_per_bar, n_bars, sections, signals, kick_ref)

    n_drop = sum(1 for c in kick_cues if c["type"] == "kick_dropout")
    old_n = f"old {len(stats['sections']):2d} -> " if stats else ""
    print(f"  {wav.stem[:46]:46}  {old_n}stem {len(sections):2d} secs | "
          f"kick-drops {n_drop} landmarks {len(musical_landmarks)} "
          f"loops {len(signals['loop_windows'])} vocals {len(signals['vocal_regions'])}")
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
    landmarks = signals.get("musical_landmarks", [])

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
        for x in (signals.get("bass_in"), signals.get("bass_out")):   # biggest mix markers
            if x is not None:
                ax.axvline(x, color="#1f4e9e", lw=2.4, alpha=0.95, zorder=5)

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
    for landmark in landmarks:
        a, b = landmark["start_sec"], landmark["end_sec"]
        colour = "#c2185b" if landmark["type"] == "pre_drop_kick_gap" else "#6a1b9a"
        axes[0].axvspan(a, b, ymin=0.82, ymax=0.94, color=colour, alpha=0.8, lw=0, zorder=7)
        label = "pre-drop" if landmark["type"] == "pre_drop_kick_gap" else "kick gap"
        axes[0].text(0.5 * (a + b), 0.87, f"{label} {landmark['duration_beats']}b",
                     fontsize=6, color="white", ha="center", va="center",
                     fontweight="bold", zorder=8)
    for mc in major:
        lab = "~1:00 in" if mc["type"] == "one_min_in" else "~1:00 to end"
        axes[0].text(mc["sec"], 1.07, lab, fontsize=7, color="#d6006d", ha="center", va="bottom", fontweight="bold")
    for key, lab in (("bass_in", "BASS IN"), ("bass_out", "BASS OUT")):
        x = signals.get(key)
        if x is not None:
            axes[0].text(x, 1.13, lab, fontsize=8, color="#1f4e9e", ha="center", va="bottom", fontweight="bold")
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
                        "top strip=kick-gap landmark   ·   "
                        f"dotted line on drums = {KICK_ON_FRAC} kick threshold")
    fig.tight_layout()
    fig.savefig(project / "_Stem Analysis" / f"DETECT_{wav.stem}.png", dpi=95)
    plt.close(fig)


def hints_from_stem_result(res: dict) -> dict:
    """Derive the four mix hints the pipeline's production gate requires —
    first_drop_sec, first_break_sec, outro_start_sec, last_bass_drop_sec — from a
    detect() result. All absolute seconds, all positive (the gate rejects None /
    non-positive). This is what makes a fully-autonomous mix possible: the cues
    Sam used to hand-author from the waveform now come straight from the stems.
    """
    secs = res["sections"]
    sig = res.get("signals", {})
    sec_per_bar = 4 * 60.0 / res["bpm"]
    end_sec = secs[-1]["end_sec"] if secs else 0.0

    drops = [s for s in secs if s["label"] == "drop"]
    breaks = [s for s in secs if s["label"] == "break"]
    outro = next((s for s in secs if s["label"] == "outro"), None)

    first_drop = drops[0]["start_sec"] if drops else None
    outro_start = outro["start_sec"] if outro else None

    # first_break = the first break AFTER the first drop (the energy drop the hint
    # describes), falling back to the first break of any kind.
    first_break = None
    if breaks:
        after = [b["start_sec"] for b in breaks if first_drop is None or b["start_sec"] > first_drop]
        first_break = after[0] if after else breaks[0]["start_sec"]

    # last_bass_drop = the natural bass swap near the end: the last fill drop-out
    # before the outro, else the last break before it.
    pre = outro_start if outro_start is not None else end_sec
    window_start = pre - 32 * sec_per_bar   # only a swap NEAR the end counts
    cand = [f[0] for f in sig.get("fills", []) if window_start <= f[0] < pre]
    cand += [b["start_sec"] for b in breaks if window_start <= b["start_sec"] < pre]
    last_bass_drop = max(cand) if cand else None

    # Fallbacks — guarantee all four present + positive for the hint gate.
    if not first_drop or first_drop <= 0:
        first_drop = 16 * sec_per_bar
    if not outro_start or outro_start <= 0:
        outro_start = max(first_drop + sec_per_bar, end_sec - 32 * sec_per_bar)
    if not first_break or first_break <= 0:
        first_break = first_drop + 16 * sec_per_bar
    if not last_bass_drop or last_bass_drop <= 0:
        last_bass_drop = max(first_drop, outro_start - 16 * sec_per_bar)

    return {
        "first_drop_sec": round(first_drop, 2),
        "first_break_sec": round(first_break, 2),
        "outro_start_sec": round(outro_start, 2),
        "last_bass_drop_sec": round(last_bass_drop, 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--track", type=str, default=None)
    ap.add_argument("--write-hints", action="store_true",
                    help="Auto-write Hints/track_hints.json from stem sections (the "
                         "fully-autonomous path — no manual visual-pass needed). No PNGs.")
    ap.add_argument("--kick-model", action="store_true",
                    help="Use Kick Detector V3 for kick IN/OUT presence. Default is off.")
    ap.add_argument("--kick-model-path", type=Path, default=None,
                    help="Path to Kick Detector weights. Defaults to sibling "
                         "'Kick Detector/Models/kick_crnn_V3.pt'.")
    ap.add_argument("--kick-model-device", default="auto",
                    help="Torch device for Kick Detector and its Demucs pass: auto, cpu, or cuda.")
    args = ap.parse_args()
    audio = args.project / "Audio"
    wavs = [audio / args.track] if args.track else sorted(audio.glob("*.wav"))

    if args.write_hints:
        hints = {}
        print(f"Auto-generating hints from stem detection for {len(wavs)} track(s):")
        for w in wavs:
            res = detect(
                w, args.project, make_viz=False,
                kick_model=args.kick_model,
                kick_model_path=args.kick_model_path,
                kick_model_device=args.kick_model_device,
            )
            if not res:
                continue
            h = hints_from_stem_result(res)
            hints[w.name] = h
            print(f"  {w.stem[:42]:42} drop {h['first_drop_sec']:6.1f}  break {h['first_break_sec']:6.1f}  "
                  f"bass-swap {h['last_bass_drop_sec']:6.1f}  outro {h['outro_start_sec']:6.1f}")
        hints_dir = args.project / "Hints"
        hints_dir.mkdir(parents=True, exist_ok=True)
        out = hints_dir / "track_hints.json"
        out.write_text(json.dumps(hints, indent=2), encoding="utf-8")
        print(f"Wrote {len(hints)} hints -> {out}")
        return

    print(f"Stem section detection on {len(wavs)} track(s):")
    for w in wavs:
        detect(
            w, args.project,
            kick_model=args.kick_model,
            kick_model_path=args.kick_model_path,
            kick_model_device=args.kick_model_device,
        )


if __name__ == "__main__":
    main()
