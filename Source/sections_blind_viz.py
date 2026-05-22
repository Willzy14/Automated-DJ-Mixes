"""BLIND visual pass renderer — V12 chops + waveform + bar gridlines.

NO Sam V7 overlay. The point: force Claude to identify visible energy steps
in the waveform itself, without leaning on Sam's manual edits as the answer
key. If Claude can't spot a chop error without V7, then the validation isn't
really validation — it's diffing.

Output: same 4-quarter format, one PNG per track quarter. Only V12 chops
shown (yellow dashed) + bar gridlines + waveform envelope.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import librosa
import matplotlib.pyplot as plt
import numpy as np


LABEL_COLOURS = {
    "intro":  "#7ec850",
    "build":  "#5bc0de",
    "drop":   "#f0c020",
    "break":  "#5099d8",
    "fill":   "#e8a04a",
    "outro":  "#e25f5f",
    "unknown": "#888888",
}


def normalize_label(name: str) -> str:
    n = name.lower().strip()
    parts = n.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        n = parts[0]
    first = n.split()[0] if n else ""
    if first in ("intro",): return "intro"
    if first in ("drop",):  return "drop"
    if first in ("break",): return "break"
    if first in ("build",): return "build"
    if first in ("fill", "fil"): return "fill"
    if first in ("outro",): return "outro"
    return first or "unknown"


def src_beat_to_sec(src_beat: float, bpm: float, first_downbeat_sec: float) -> float:
    return first_downbeat_sec + src_beat * 60.0 / bpm


def render_blind_quarters(track_name, audio_path, bpm, first_downbeat_sec,
                          v12_clips, out_dir, quarters=8):
    # Load at 22050 Hz so we can resolve bass (<200 Hz) properly via LP filter.
    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    duration_sec = len(y) / sr

    # Split into three frequency bands so we can see breaks (bass cut, mids
    # stay) vs outros (everything fades) vs builds (mids/highs rising), etc.
    #   low  : <250 Hz   (kick, sub bass, bassline fundamental)
    #   mid  : 250-2500  (chords, vox lows, snare body, lead synths)
    #   high : >2500     (hats, snare top, percussion air, vocal sibilance)
    try:
        import scipy.signal as sps
        sos_low = sps.butter(4, 250, "lowpass", fs=sr, output="sos")
        sos_mid = sps.butter(4, [250, 2500], "bandpass", fs=sr, output="sos")
        sos_hi  = sps.butter(4, 2500, "highpass", fs=sr, output="sos")
        y_low = sps.sosfilt(sos_low, y)
        y_mid = sps.sosfilt(sos_mid, y)
        y_hi  = sps.sosfilt(sos_hi, y)
    except Exception:
        y_low = y * 0.0; y_mid = y * 0.0; y_hi = y * 0.0

    # Max-pooled envelopes at ~50ms bins (visual smoothing).
    bin_sec = 0.05
    samples_per_bin = max(1, int(bin_sec * sr))
    n_bins = len(y) // samples_per_bin
    def _env(sig):
        m = np.abs(sig[: n_bins * samples_per_bin]).reshape(n_bins, samples_per_bin)
        return m.max(axis=1)
    env      = _env(y)
    low_env  = _env(y_low)
    mid_env  = _env(y_mid)
    hi_env   = _env(y_hi)
    # Normalise each band to its own 0..0.9 scale so the overlay is readable.
    def _norm(a):
        peak = a.max()
        return a / peak * 0.9 if peak > 0 else a
    low_env_n = _norm(low_env)
    mid_env_n = _norm(mid_env)
    hi_env_n  = _norm(hi_env)
    # Back-compat alias for code further down
    bass_env  = low_env_n
    env_time = np.arange(n_bins) * bin_sec

    sec_per_beat = 60.0 / bpm
    sec_per_bar = 4 * sec_per_beat

    clips = []
    for c in v12_clips:
        ss = c.get("source_start_beats")
        se = c.get("source_end_beats")
        if ss is None or se is None:
            continue
        clips.append({
            "start_sec": src_beat_to_sec(ss, bpm, first_downbeat_sec),
            "end_sec":   src_beat_to_sec(se, bpm, first_downbeat_sec),
            "label": normalize_label(c["name"]),
            "name": c["name"],
            "src_bar": ss / 4,
            "src_end_bar": se / 4,
        })
    clips.sort(key=lambda c: c["start_sec"])

    # Per-section stats: mean of full + each band, within each section.
    for c in clips:
        m = (env_time >= c["start_sec"]) & (env_time < c["end_sec"])
        c["mean_amp"]  = float(env[m].mean())      if m.any() else 0.0
        c["mean_low"]  = float(low_env_n[m].mean()) if m.any() else 0.0
        c["mean_mid"]  = float(mid_env_n[m].mean()) if m.any() else 0.0
        c["mean_high"] = float(hi_env_n[m].mean())  if m.any() else 0.0
        c["mean_bass"] = c["mean_low"]  # back-compat for overview labels

    # Auto-flag suspicious patterns by comparing section labels to band stats,
    # and propose concrete corrections where the band envelopes show the real
    # boundary. Proposals go into PROPOSED_CORRECTIONS_V<N>.json in the format
    # apply_section_corrections.py understands:
    #   [track_substr, from_clip, to_clip, old_bar, new_bar_or_"DELETE", arr_offset]
    flags = []        # list of human-readable messages
    proposals = []    # list of correction tuples (typed lists for JSON)
    drops = [c for c in clips if c["label"] == "drop"]
    breaks = [c for c in clips if c["label"] == "break"]
    fills = [c for c in clips if c["label"] == "fill"]
    outros = [c for c in clips if c["label"] == "outro"]
    intros = [c for c in clips if c["label"] == "intro"]
    if drops:
        drop_low_med = sum(d["mean_low"] for d in drops) / len(drops)
        drop_amp_med = sum(d["mean_amp"] for d in drops) / len(drops)
    else:
        drop_low_med = drop_amp_med = 0.0

    # arr_offset for this track: arr_time of first clip minus its source start.
    # Equals 0 for a pre-arrangement Sections .als (Phase 1 output); equals the
    # track's arrangement shift after Phase 2. We embed it so corrections can
    # be applied to either phase's .als.
    if clips:
        arr_offset = clips[0]["start_sec"]  # not used — placeholder
        first = v12_clips[0]
        arr_offset_beats = first["arr_time"] - first["source_start_beats"]
    else:
        arr_offset_beats = 0

    # The "track substring" used by apply_section_corrections.py to find the
    # AudioTrack block. Use enough of the name to uniquely disambiguate when
    # an artist has multiple tracks (e.g. 8 Mike Richters tracks would collide
    # on a 2-word substring). We strip leading filename suffixes that
    # apply_section_corrections matches as substring anywhere.
    #
    # Strategy: use everything up to but not including " 24 Bit" or " SW V".
    # That gives the artist+title which is uniquely identifying.
    base = track_name
    for cutoff in (" 24 Bit", " SW V", ".wav"):
        idx = base.find(cutoff)
        if idx > 0:
            base = base[:idx]
            break
    track_substr = base.strip().rstrip(",-")

    def _bar_with_max_neg_derivative(start_sec, end_sec):
        """Find the time inside [start_sec, end_sec] where the band-sum
        envelope drops the most (negative derivative). Returns source bar."""
        m = (env_time >= start_sec) & (env_time < end_sec)
        if not m.any():
            return None
        sum_env = (low_env_n + mid_env_n + hi_env_n)
        # Smooth over ~2 seconds (40 bins) to avoid latching onto single hits.
        window = 40
        if m.sum() < window * 2:
            return None
        sub = sum_env[m]
        sub_t = env_time[m]
        # First-difference of a smoothed envelope.
        kernel = np.ones(window) / window
        smooth = np.convolve(sub, kernel, mode="same")
        deriv = np.diff(smooth, prepend=smooth[0])
        idx = int(np.argmin(deriv))
        t_at_min = sub_t[idx]
        # Convert seconds → source beats → source bar
        src_beat = (t_at_min - first_downbeat_sec) * bpm / 60.0
        src_bar = src_beat / 4.0
        # Snap to nearest 4 bars (natural phrase boundary)
        return round(src_bar / 4.0) * 4.0

    def _bar_with_max_pos_derivative(start_sec, end_sec):
        """Find the time inside [start_sec, end_sec] where the low-band
        envelope rises the most. Returns source bar (snapped to 4-bar grid)."""
        m = (env_time >= start_sec) & (env_time < end_sec)
        if not m.any():
            return None
        window = 40
        if m.sum() < window * 2:
            return None
        sub = low_env_n[m]
        sub_t = env_time[m]
        kernel = np.ones(window) / window
        smooth = np.convolve(sub, kernel, mode="same")
        deriv = np.diff(smooth, prepend=smooth[0])
        idx = int(np.argmax(deriv))
        t_at_max = sub_t[idx]
        src_beat = (t_at_max - first_downbeat_sec) * bpm / 60.0
        return round(src_beat / 4.0 / 4.0) * 4.0

    # Find the section immediately BEFORE a given section (by name)
    def _prev_clip(target):
        prev = None
        for c in clips:
            if c is target:
                return prev
            prev = c
        return None

    # --- Flag: break has bass still on (not a real break) ---
    for b in breaks:
        if drop_low_med > 0 and b["mean_low"] >= 0.8 * drop_low_med:
            flags.append(
                f"⚠  `{b['name']}` is labelled BREAK but low-band energy "
                f"({b['mean_low']:.2f}) is {b['mean_low']/drop_low_med*100:.0f}% "
                f"of the drop average ({drop_low_med:.2f}). Bass not actually cut.")
            prev = _prev_clip(b)
            if prev:
                # Propose DELETE: merge break into the previous clip.
                proposals.append([
                    track_substr, prev["name"], b["name"],
                    int(round(b["src_bar"])), "DELETE", int(round(arr_offset_beats)),
                ])
        if drop_amp_med > 0 and b["mean_amp"] >= drop_amp_med:
            flags.append(
                f"⚠  `{b['name']}` is labelled BREAK but full-amp energy "
                f"({b['mean_amp']:.2f}) is at or above the drop average "
                f"({drop_amp_med:.2f}). Likely mislabel.")

    # --- Flag: outro at full energy — search for the real fade start ---
    # CLAMP: the proposed new outro_start bar must leave at least MIN_SEC_BARS
    # of remaining outro length, otherwise the outro becomes zero-length and
    # Ableton hides the clip. Without this clamp we hit the V4 22.05.26 bug
    # where 7 tracks ended up with collapsed outros.
    MIN_SEC_BARS = 4
    for o in outros:
        if drop_amp_med > 0 and o["mean_amp"] >= 0.85 * drop_amp_med:
            flags.append(
                f"⚠  `{o['name']}` is labelled OUTRO but amp ({o['mean_amp']:.2f}) "
                f"is {o['mean_amp']/drop_amp_med*100:.0f}% of the drop average. "
                f"Outro may start later than the chop.")
            # Search in the labelled outro region for the largest negative
            # band-sum derivative. Restrict to the last 60% to avoid latching
            # onto the existing chop transient.
            search_start = o["start_sec"] + 0.4 * (o["end_sec"] - o["start_sec"])
            new_bar = _bar_with_max_neg_derivative(search_start, o["end_sec"])
            outro_end_bar = o["src_end_bar"]
            max_new_bar = outro_end_bar - MIN_SEC_BARS
            if new_bar is not None and new_bar > max_new_bar:
                flags.append(
                    f"    auto-propose clamped: would-be new_bar={new_bar} "
                    f"is within {MIN_SEC_BARS} bars of outro end "
                    f"({outro_end_bar}); using {max_new_bar} instead so "
                    f"the outro keeps at least {MIN_SEC_BARS} bars of length.")
                new_bar = max_new_bar
            if new_bar is not None and new_bar > o["src_bar"] + MIN_SEC_BARS:
                prev = _prev_clip(o)
                if prev:
                    proposals.append([
                        track_substr, prev["name"], o["name"],
                        int(round(o["src_bar"])), int(new_bar),
                        int(round(arr_offset_beats)),
                    ])

    # --- Flag: intro at full energy — search for the real drop start ---
    # CLAMP: proposed new intro_end must leave at least MIN_SEC_BARS of intro.
    for i in intros:
        if drop_amp_med > 0 and i["mean_amp"] >= 0.85 * drop_amp_med:
            flags.append(
                f"⚠  `{i['name']}` is labelled INTRO but amp ({i['mean_amp']:.2f}) "
                f"is {i['mean_amp']/drop_amp_med*100:.0f}% of the drop average. "
                f"Intro may end earlier than the chop.")
            new_bar = _bar_with_max_pos_derivative(i["start_sec"], i["end_sec"])
            idx = clips.index(i)
            nxt = clips[idx + 1] if idx + 1 < len(clips) else None
            intro_start_bar = i["src_bar"]
            min_new_bar = intro_start_bar + MIN_SEC_BARS
            if new_bar is not None and new_bar < min_new_bar:
                flags.append(
                    f"    auto-propose clamped: would-be new_bar={new_bar} "
                    f"would leave intro < {MIN_SEC_BARS} bars; "
                    f"using {min_new_bar} instead.")
                new_bar = min_new_bar
            if new_bar is not None and nxt and new_bar < nxt["src_bar"] - MIN_SEC_BARS:
                proposals.append([
                    track_substr, i["name"], nxt["name"],
                    int(round(nxt["src_bar"])), int(new_bar),
                    int(round(arr_offset_beats)),
                ])

    # --- Flag: adjacent sections with near-identical stats (likely no boundary) ---
    for a, b in zip(clips, clips[1:]):
        d_amp = abs(a["mean_amp"] - b["mean_amp"])
        d_low = abs(a["mean_low"] - b["mean_low"])
        d_mid = abs(a["mean_mid"] - b["mean_mid"])
        d_hi  = abs(a["mean_high"] - b["mean_high"])
        if a["label"] == "fill" or b["label"] == "fill":
            continue
        if d_amp < 0.04 and d_low < 0.05 and d_mid < 0.05 and d_hi < 0.05:
            flags.append(
                f"⚠  Boundary `{a['name']}` → `{b['name']}` shows near-identical "
                f"stats on both sides (Δamp {d_amp:.2f}, Δlow {d_low:.2f}, "
                f"Δmid {d_mid:.2f}, Δhi {d_hi:.2f}). Likely no real energy step.")
            proposals.append([
                track_substr, a["name"], b["name"],
                int(round(b["src_bar"])), "DELETE", int(round(arr_offset_beats)),
            ])

    out_paths = []
    quarter_dur = duration_sec / quarters
    safe_name = track_name.replace("/", "_").replace("\\", "_").replace("–", "-").replace("'", "_")[:60]

    # ---------- OVERVIEW PNG (whole track in one image) ----------
    fig_ov, ax_ov = plt.subplots(figsize=(28, 7), dpi=110)
    ax_ov.fill_between(env_time, -env, env, color="#444", linewidth=0, zorder=1)
    # Three band overlays. Red = low (<250 Hz), green = mid (250–2500 Hz),
    # blue = high (>2500 Hz). All normalised to 0..0.9 per band.
    ax_ov.plot(env_time,  low_env_n, color="#ff5050", linewidth=0.7, alpha=0.85,
               label="low (<250)", zorder=4)
    ax_ov.plot(env_time, -low_env_n, color="#ff5050", linewidth=0.7, alpha=0.85, zorder=4)
    ax_ov.plot(env_time,  mid_env_n, color="#50d050", linewidth=0.6, alpha=0.8,
               label="mid (250-2500)", zorder=4)
    ax_ov.plot(env_time, -mid_env_n, color="#50d050", linewidth=0.6, alpha=0.8, zorder=4)
    ax_ov.plot(env_time,  hi_env_n,  color="#6080ff", linewidth=0.5, alpha=0.75,
               label="high (>2500)", zorder=4)
    ax_ov.plot(env_time, -hi_env_n,  color="#6080ff", linewidth=0.5, alpha=0.75, zorder=4)
    ax_ov.legend(loc="lower right", fontsize=7, framealpha=0.7)
    # Section bands at the bottom + labels on top
    for c in clips:
        ax_ov.axhspan(-1.0, -0.94,
                      xmin=c["start_sec"] / duration_sec,
                      xmax=c["end_sec"] / duration_sec,
                      color=LABEL_COLOURS.get(c["label"], "#888"),
                      alpha=0.9, zorder=2)
        mid_x = (c["start_sec"] + c["end_sec"]) / 2
        ax_ov.text(mid_x, -0.97, c["name"], ha="center", va="top",
                   fontsize=7, color="black",
                   bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.7),
                   zorder=5)
        # 3-band stats label above the section
        ax_ov.text(mid_x, 0.95,
                   f"amp {c['mean_amp']:.2f}\nL {c['mean_low']:.2f}  M {c['mean_mid']:.2f}  H {c['mean_high']:.2f}",
                   ha="center", va="top", fontsize=6, color="#ffe060",
                   bbox=dict(boxstyle="round,pad=0.1", fc="#222", ec="#555", alpha=0.8),
                   zorder=5)
        ax_ov.axvline(c["start_sec"], color="#ffff20", alpha=0.6, linewidth=1.0, linestyle="--", zorder=3)
    # 16-bar gridlines only on the overview
    bar_end_total = int((duration_sec - first_downbeat_sec) / sec_per_bar + 1)
    for b in range(0, bar_end_total + 16, 16):
        x = first_downbeat_sec + b * sec_per_bar
        if 0 <= x <= duration_sec:
            ax_ov.axvline(x, color="#ffffff", alpha=0.15, linewidth=0.6, zorder=1)
            ax_ov.text(x, 1.02, f"bar {b}", color="#999", fontsize=7,
                       ha="center", va="bottom")
    ax_ov.set_xlim(0, duration_sec)
    ax_ov.set_ylim(-1.05, 1.05)
    ax_ov.set_title(
        f"{track_name}  —  OVERVIEW (full track, {duration_sec:.0f}s, BPM {bpm:.1f})\n"
        f"Grey = full envelope.  Red = low (<250 Hz), green = mid (250-2500), "
        f"blue = high (>2500).  Per-section: amp / L / M / H means.",
        fontsize=10, color="#eee", loc="left")
    ax_ov.set_xlabel("seconds", color="#ddd")
    ax_ov.set_facecolor("#111")
    fig_ov.patch.set_facecolor("#0a0a0a")
    ax_ov.tick_params(colors="#aaa")
    for spine in ax_ov.spines.values():
        spine.set_color("#444")
    ax_ov.set_yticks([])
    overview_path = out_dir / f"{safe_name}_OVERVIEW.png"
    fig_ov.tight_layout()
    fig_ov.savefig(overview_path, facecolor=fig_ov.get_facecolor(), bbox_inches="tight")
    plt.close(fig_ov)
    out_paths.append(overview_path)

    # ---------- STATS JSON ----------
    stats_path = out_dir / f"{safe_name}_stats.json"
    stats_data = {
        "track": track_name,
        "bpm": bpm,
        "duration_sec": duration_sec,
        "first_downbeat_sec": first_downbeat_sec,
        "sections": [
            {
                "name": c["name"], "label": c["label"],
                "src_bar_start": c["src_bar"], "src_bar_end": c["src_end_bar"],
                "start_sec": c["start_sec"], "end_sec": c["end_sec"],
                "mean_amp": c["mean_amp"],
                "mean_low": c["mean_low"],
                "mean_mid": c["mean_mid"],
                "mean_high": c["mean_high"],
            }
            for c in clips
        ],
        "auto_flags": flags,
    }
    stats_path.write_text(json.dumps(stats_data, indent=2), encoding="utf-8")

    for q in range(quarters):
        t0 = q * quarter_dur
        t1 = (q + 1) * quarter_dur

        fig, ax = plt.subplots(figsize=(22, 7), dpi=120)
        mask = (env_time >= t0) & (env_time <= t1)
        ax.fill_between(env_time[mask], -env[mask], env[mask], color="#444",
                        linewidth=0, zorder=1)
        # Three band overlays — red=low, green=mid, blue=high.
        ax.plot(env_time[mask],  low_env_n[mask], color="#ff5050",
                linewidth=0.9, alpha=0.85, zorder=4, label="low <250")
        ax.plot(env_time[mask], -low_env_n[mask], color="#ff5050",
                linewidth=0.9, alpha=0.85, zorder=4)
        ax.plot(env_time[mask],  mid_env_n[mask], color="#50d050",
                linewidth=0.8, alpha=0.8, zorder=4, label="mid 250-2500")
        ax.plot(env_time[mask], -mid_env_n[mask], color="#50d050",
                linewidth=0.8, alpha=0.8, zorder=4)
        ax.plot(env_time[mask],  hi_env_n[mask], color="#6080ff",
                linewidth=0.7, alpha=0.75, zorder=4, label="high >2500")
        ax.plot(env_time[mask], -hi_env_n[mask], color="#6080ff",
                linewidth=0.7, alpha=0.75, zorder=4)
        ax.legend(loc="upper right", fontsize=7, framealpha=0.7)
        ax.set_xlim(t0, t1)
        ax.set_ylim(-1.05, 1.05)

        # Bar gridlines — every 4 bars (light), every 16 (heavy)
        first_bar_sec = first_downbeat_sec
        bar_start = int(max(0, (t0 - first_bar_sec) / sec_per_bar - 1))
        bar_end = int((t1 - first_bar_sec) / sec_per_bar + 2)
        for b in range(bar_start, bar_end):
            x = first_bar_sec + b * sec_per_bar
            if x < t0 or x > t1:
                continue
            if b % 16 == 0:
                ax.axvline(x, color="#ffffff", alpha=0.35, linewidth=1.2, zorder=1)
                ax.text(x, 1.02, f"bar {b}", color="#ddd", fontsize=9,
                        ha="center", va="bottom")
            elif b % 4 == 0:
                ax.axvline(x, color="#888", alpha=0.25, linewidth=0.6, zorder=1)
                if (b % 16) in (4, 8, 12):
                    ax.text(x, 1.02, f"{b}", color="#999", fontsize=7,
                            ha="center", va="bottom")

        # Bottom strip = V12 label colors + per-section amp/bass stats above strip
        for c in clips:
            if c["end_sec"] < t0 or c["start_sec"] > t1:
                continue
            sx = max(c["start_sec"], t0)
            ex = min(c["end_sec"], t1)
            ax.axhspan(-1.0, -0.92,
                       xmin=(sx - t0) / (t1 - t0),
                       xmax=(ex - t0) / (t1 - t0),
                       color=LABEL_COLOURS.get(c["label"], "#888"),
                       alpha=0.9, zorder=2)
            # Stats label (only if section is at least 5% of the visible width)
            visible_frac = (ex - sx) / (t1 - t0)
            if visible_frac > 0.05:
                mid_x = (sx + ex) / 2
                ax.text(mid_x, -0.86,
                        f"{c['name']}\namp {c['mean_amp']:.2f}\n"
                        f"L {c['mean_low']:.2f}  M {c['mean_mid']:.2f}  H {c['mean_high']:.2f}",
                        ha="center", va="top", fontsize=7, color="#ffe060",
                        bbox=dict(boxstyle="round,pad=0.15", fc="#222",
                                  ec="#666", alpha=0.9),
                        zorder=5)

        # V12 chop lines (yellow dashed)
        for c in clips:
            if c["start_sec"] < t0 or c["start_sec"] > t1:
                continue
            ax.axvline(c["start_sec"], color="#ffff20", alpha=0.85,
                       linewidth=1.5, linestyle="--", zorder=3)
            bar_n = (c["start_sec"] - first_bar_sec) / sec_per_bar
            ax.text(c["start_sec"], 0.85,
                    f"V12 {c['label']}\nbar {bar_n:.1f}",
                    color="#ffe060", fontsize=9, ha="left", va="top",
                    rotation=90, alpha=0.95)

        title = (
            f"{track_name}  —  Quarter {q+1}/{quarters}  ({t0:.0f}s..{t1:.0f}s)\n"
            f"BPM {bpm:.1f}  |  Grey=full  red=low(<250)  green=mid(250-2500)  blue=high(>2500)  "
            f"|  Yellow=V12 chop  |  amp/L/M/H = per-section mean"
        )
        ax.set_title(title, fontsize=10, color="#eee", loc="left")
        ax.set_xlabel("seconds", color="#ddd")
        ax.set_facecolor("#111")
        fig.patch.set_facecolor("#0a0a0a")
        ax.tick_params(colors="#aaa")
        for spine in ax.spines.values():
            spine.set_color("#444")
        ax.set_yticks([])

        out_path = out_dir / f"{safe_name}_Q{q+1}.png"
        fig.tight_layout()
        fig.savefig(out_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)
        out_paths.append(out_path)

    return out_paths, flags, clips, proposals


def _find_latest_sections_json(project_dir: Path) -> tuple[int, Path]:
    """Auto-find the latest Sections_V<N>.json in <project>/Sections Review/."""
    candidates = []
    for p in (project_dir / "Sections Review").glob("Sections_V*.json"):
        stem = p.stem
        rest = stem[len("Sections_V"):]
        n = ""
        for c in rest:
            if c.isdigit():
                n += c
            else:
                break
        if n:
            candidates.append((int(n), p))
    if not candidates:
        raise FileNotFoundError(f"No Sections_V<N>.json in {project_dir/'Sections Review'}")
    candidates.sort(key=lambda t: t[0])
    return candidates[-1]


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Render 8-quarter blind PNGs from a sections JSON + audio.")
    parser.add_argument("project_dir", type=Path,
                        help="Project folder containing Audio/ and Sections Review/")
    parser.add_argument("--sections", type=Path, default=None,
                        help="Specific Sections_V<N>.json (default: latest in project)")
    parser.add_argument("--out-name", type=str, default=None,
                        help="Output subfolder name (default: Blind_V<N>)")
    parser.add_argument("--audio-dir", type=Path, default=None,
                        help="Audio folder (default: <project>/Audio)")
    args = parser.parse_args()

    project_dir = args.project_dir
    audio_dir = args.audio_dir or (project_dir / "Audio")
    if not audio_dir.exists():
        raise FileNotFoundError(f"Audio dir not found: {audio_dir}")

    if args.sections:
        sections_path = args.sections
        # Try to parse version from filename
        stem = sections_path.stem
        rest = stem[len("Sections_V"):] if stem.startswith("Sections_V") else ""
        n = ""
        for c in rest:
            if c.isdigit():
                n += c
            else:
                break
        version = int(n) if n else 0
    else:
        version, sections_path = _find_latest_sections_json(project_dir)

    out_name = args.out_name or f"Blind_V{version}"
    out_dir = project_dir / "Sections Review" / out_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Project:  {project_dir}")
    print(f"Sections: {sections_path.name}")
    print(f"Output:   {out_dir}")
    sections_data = json.loads(sections_path.read_text(encoding="utf-8"))

    # BPMs: prefer arrangement report (MIK-enriched), fall back to RB analysis
    bpm_lookup = {}
    first_downbeat_lookup = {}
    report_candidates = list((project_dir / "Output").glob("ARRANGEMENT_REPORT*.json"))
    if report_candidates:
        # Use the most recent report
        report_candidates.sort(key=lambda p: p.stat().st_mtime)
        with open(report_candidates[-1]) as f:
            rep = json.load(f)
        for t in rep.get("tracks", []):
            bpm_lookup[t["name"]] = t["bpm"]
        print(f"BPMs from: {report_candidates[-1].name}")

    # First downbeat from RB analysis if available
    try:
        from automated_dj_mixes.analysis import analyse_folder, enrich_from_rekordbox
        from automated_dj_mixes.rekordbox_reader import read_rekordbox_library, find_rekordbox_match

        print("Loading audio analyses for downbeat...")
        analyses = analyse_folder(audio_dir)
        rb_lib = read_rekordbox_library()
        for a in analyses:
            rb_match = find_rekordbox_match(a.path.name, rb_lib)
            if rb_match:
                enrich_from_rekordbox(a, rb_match)
            first_downbeat_lookup[a.path.stem] = a.first_downbeat_sec or 0.0
            # Fallback BPM if not in report
            if a.path.stem not in bpm_lookup:
                bpm_lookup[a.path.stem] = a.bpm
    except Exception as e:
        print(f"  (RB analysis unavailable: {e}; using 0.0 first_downbeat)")

    rendered = 0
    tracks_for_notes = []  # (clean_name, sections, flags)
    all_proposals: list = []
    for track_name in sections_data:
        if not track_name or "Audio" in track_name:
            continue
        # Decode XML entities preserved by extract_sections_als.py
        clean_name = (track_name
                      .replace("&apos;", "'")
                      .replace("&amp;", "&")
                      .replace("&quot;", '"')
                      .replace("&lt;", "<")
                      .replace("&gt;", ">"))
        bpm = bpm_lookup.get(track_name) or bpm_lookup.get(clean_name)
        if bpm is None:
            print(f"  No BPM for {track_name}")
            continue
        first_downbeat_sec = first_downbeat_lookup.get(track_name) or \
                             first_downbeat_lookup.get(clean_name) or 0.0
        audio_path = audio_dir / f"{clean_name}.wav"
        if not audio_path.exists():
            print(f"  No audio: {clean_name}")
            continue

        print(f"Rendering blind: {clean_name}  (BPM {bpm:.2f}, downbeat {first_downbeat_sec:.3f}s)")
        _, track_flags, _, track_props = render_blind_quarters(
            track_name=clean_name,
            audio_path=audio_path,
            bpm=bpm,
            first_downbeat_sec=first_downbeat_sec,
            v12_clips=sections_data[track_name],
            out_dir=out_dir,
        )
        rendered += 1
        tracks_for_notes.append((clean_name, sections_data[track_name], track_flags))
        all_proposals.extend(track_props)
        if track_flags:
            print(f"  → {len(track_flags)} auto-flag(s):")
            for f in track_flags:
                print(f"      {f}")
        if track_props:
            print(f"  → {len(track_props)} proposed correction(s):")
            for p in track_props:
                print(f"      {p}")

    # Write proposed corrections JSON (consumed by apply_section_corrections.py)
    if all_proposals:
        proposals_path = project_dir / "Sections Review" / \
                         f"PROPOSED_CORRECTIONS_V{version}.json"
        proposals_path.write_text(json.dumps(all_proposals, indent=2),
                                  encoding="utf-8")
        print(f"\nProposed corrections: {proposals_path}")

    # ---------- NOTES SCRATCHPAD TEMPLATE ----------
    # Created if missing. NOT overwritten if the agent has already started writing.
    notes_path = out_dir / "NOTES.md"
    if not notes_path.exists():
        lines = [
            f"# Blind validation notes — {out_name}",
            "",
            "Use this scratchpad to write per-PNG observations as you review.",
            "One section per track. Per-quarter notes go under that track. The",
            "overview PNG is the reference image — refer back to it as you read",
            "each quarter to keep the whole-track context in mind.",
            "",
            "**Workflow:**",
            "1. Read `<Track>_OVERVIEW.png` first — note overall shape, where",
            "   energy peaks/dips actually are vs. where the labels claim.",
            "2. Read `<Track>_Q1.png` through `_Q8.png` in order — for each",
            "   visible V12 chop line, write the bar number and whether the",
            "   waveform/bass envelope shows a step there.",
            "3. Cross-check `<Track>_stats.json` for per-section amp/bass",
            "   averages — drop_X should have higher bass than break/outro.",
            "",
            "**Verdict shorthand:**",
            "- `✓` chop lands on a visible step (amp or bass)",
            "- `⚠ off N` chop is N bars early/late from the visible step",
            "- `⚠ no step` no visible boundary at the chop",
            "- `⚠ mislabel` section type doesn't match the energy (e.g. break has more amp than drop)",
            "- `⚠ missing` there's an obvious step in the waveform with no chop on it",
            "",
            "---",
            "",
        ]
        for name, secs, track_flags in tracks_for_notes:
            lines.append(f"## {name}")
            lines.append("")
            if track_flags:
                lines.append("**Auto-flags from stats:**")
                lines.append("")
                for f in track_flags:
                    lines.append(f"- {f}")
                lines.append("")
            else:
                lines.append("**Auto-flags from stats:** none")
                lines.append("")
            lines.append("**Overview observations:**")
            lines.append("- _(notes about overall structure from the OVERVIEW PNG)_")
            lines.append("")
            lines.append("**Per-chop verdicts:**")
            lines.append("")
            lines.append("| Bar | Boundary type | Quarter | Energy step? | Verdict | Notes |")
            lines.append("|-----|--------------|---------|--------------|---------|-------|")
            prev_label = None
            for s in secs:
                if prev_label is not None:
                    bar = s["source_start_beats"] / 4
                    boundary = f"{prev_label}→{s['label']}"
                    lines.append(f"| {bar:.1f} | {boundary} | Q? | | | |")
                prev_label = s["label"]
            lines.append("")
            lines.append("---")
            lines.append("")
        notes_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nNotes scratchpad: {notes_path}")
    else:
        print(f"\nNotes scratchpad already exists: {notes_path} (not overwritten)")

    print(f"\nRendered {rendered} tracks: {rendered} overview + {rendered * 8} quarter PNGs + {rendered} stats JSON to {out_dir}")


if __name__ == "__main__":
    sys.exit(main() or 0)
