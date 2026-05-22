"""Render per-loop PNGs from an arrangement report + audio.

For each loop in `ARRANGEMENT_REPORT*.json`, renders one PNG showing the
source region with 3-band envelopes so you can verify the loop is on
clean stripped content (drums + bass, no dissipating tail, no muddy
melody). Also computes a simple quality metric so borderline loops
stand out.

Output: <project>/Output/Visualisations/Loops_V<N>/L<NN>_<track>_<type>.png

CLI:
  python loop_review_viz.py <project_dir> [--report ARRANGEMENT_REPORT_VN.json]
                                          [--audio-dir AUDIO]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import librosa
import matplotlib.pyplot as plt
import numpy as np


# Reuse the same 3-band bands as the section viz so the look is consistent.
def _compute_bands(audio: np.ndarray, sr: int):
    try:
        import scipy.signal as sps
        sos_low = sps.butter(4, 250, "lowpass", fs=sr, output="sos")
        sos_mid = sps.butter(4, [250, 2500], "bandpass", fs=sr, output="sos")
        sos_hi  = sps.butter(4, 2500, "highpass", fs=sr, output="sos")
        a_low = sps.sosfilt(sos_low, audio)
        a_mid = sps.sosfilt(sos_mid, audio)
        a_hi  = sps.sosfilt(sos_hi, audio)
    except Exception:
        a_low = audio * 0.0; a_mid = audio * 0.0; a_hi = audio * 0.0

    bin_sec = 0.05
    samples_per_bin = max(1, int(bin_sec * sr))
    n_bins = len(audio) // samples_per_bin
    def _e(sig):
        m = np.abs(sig[: n_bins * samples_per_bin]).reshape(n_bins, samples_per_bin)
        return m.max(axis=1)
    def _n(a):
        p = a.max()
        return a / p * 0.9 if p > 0 else a
    full = _e(audio)
    if full.max() > 0:
        full = full / full.max() * 0.9
    return full, _n(_e(a_low)), _n(_e(a_mid)), _n(_e(a_hi)), bin_sec


def _quality(full_env: np.ndarray, low_env: np.ndarray) -> tuple[float, str]:
    """Cheap quality heuristic, ranged ~0..1. Higher = better loop content.

    Rewards:
      - High peak-to-mean ratio in the full envelope (transient drums)
      - Non-zero low band (kick presence)
    Penalises:
      - Very low mean amplitude (silent / dissipating tail)
    """
    if len(full_env) == 0:
        return 0.0, "empty region"
    mean_amp = float(full_env.mean())
    peak_amp = float(full_env.max())
    transient_ratio = peak_amp / (mean_amp + 1e-6)  # high when peaky (drums)
    low_mean = float(low_env.mean())

    # Normalise components into 0..1-ish
    amp_ok = min(1.0, mean_amp / 0.35)                # 0.35 = comfortable mean
    trans_ok = min(1.0, max(0.0, (transient_ratio - 1.5) / 2.5))
    low_ok = min(1.0, low_mean / 0.5)
    score = (amp_ok * 0.4 + trans_ok * 0.35 + low_ok * 0.25)

    if mean_amp < 0.10:
        verdict = "⚠ silent/dissipating"
    elif score < 0.3:
        verdict = "⚠ borderline"
    elif score < 0.5:
        verdict = "ok"
    else:
        verdict = "✓ clean"
    return score, verdict


def render_loop(audio: np.ndarray, sr: int, bpm: float,
                src_beat_start: float, src_beat_end: float,
                track_name: str, loop_type: str, count: int,
                out_path: Path, loop_index: int):
    sec_per_beat = 60.0 / bpm
    sec_start = src_beat_start * sec_per_beat
    sec_end = src_beat_end * sec_per_beat
    # Context: render 4 bars (=16 beats) before and after the loop region
    ctx_sec = 16 * sec_per_beat
    view_start = max(0, sec_start - ctx_sec)
    view_end = min(len(audio) / sr, sec_end + ctx_sec)
    if view_end <= view_start:
        return

    s_lo = int(view_start * sr)
    s_hi = int(view_end * sr)
    clip = audio[s_lo:s_hi]

    full, low, mid, hi, bin_sec = _compute_bands(clip, sr)
    env_time = np.arange(len(full)) * bin_sec + view_start  # back to file time

    # Quality is computed over the LOOP REGION only, not the context.
    loop_mask = (env_time >= sec_start) & (env_time < sec_end)
    q_score, q_verdict = _quality(full[loop_mask], low[loop_mask])

    fig, ax = plt.subplots(figsize=(22, 5), dpi=110)
    ax.fill_between(env_time, -full, full, color="#444", linewidth=0, zorder=1)
    ax.plot(env_time,  low, color="#ff5050", linewidth=0.9, alpha=0.85, zorder=4,
            label="low <250")
    ax.plot(env_time, -low, color="#ff5050", linewidth=0.9, alpha=0.85, zorder=4)
    ax.plot(env_time,  mid, color="#50d050", linewidth=0.7, alpha=0.8,  zorder=4,
            label="mid 250-2500")
    ax.plot(env_time, -mid, color="#50d050", linewidth=0.7, alpha=0.8,  zorder=4)
    ax.plot(env_time,  hi,  color="#6080ff", linewidth=0.6, alpha=0.75, zorder=4,
            label="high >2500")
    ax.plot(env_time, -hi,  color="#6080ff", linewidth=0.6, alpha=0.75, zorder=4)

    # Highlight the loop region itself
    ax.axvspan(sec_start, sec_end, color="#ffff20", alpha=0.12, zorder=0)
    ax.axvline(sec_start, color="lime", lw=2, alpha=0.85,
               label=f"loop start (src beat {src_beat_start:.0f})")
    ax.axvline(sec_end, color="red", lw=2, alpha=0.85,
               label=f"loop end (src beat {src_beat_end:.0f})")

    # Bar gridlines inside the visible region
    sec_per_bar = 4 * sec_per_beat
    first_bar_sec = 0  # source bar 0 is at file time 0
    bar_lo = int(view_start / sec_per_bar)
    bar_hi = int(view_end / sec_per_bar) + 1
    for b in range(bar_lo, bar_hi + 1):
        x = b * sec_per_bar
        if view_start <= x <= view_end:
            colour = "#ffffff" if b % 4 == 0 else "#888"
            alpha = 0.25 if b % 4 == 0 else 0.12
            lw = 0.9 if b % 4 == 0 else 0.5
            ax.axvline(x, color=colour, alpha=alpha, linewidth=lw, zorder=2)

    ax.set_xlim(view_start, view_end)
    ax.set_ylim(-1.05, 1.05)
    ax.set_yticks([])
    ax.set_xlabel("seconds (source time)")
    loop_bars = (src_beat_end - src_beat_start) / 4
    total_beats = (src_beat_end - src_beat_start) * count
    title = (
        f"Loop L{loop_index:02d}: {track_name[:60]}  —  {loop_type.upper()}\n"
        f"region {loop_bars:.0f} bars × {count} copies = "
        f"{total_beats:.0f} beats inserted  |  quality {q_score:.2f} ({q_verdict})  |  "
        f"grey=full, red=low, green=mid, blue=high"
    )
    ax.set_title(title, fontsize=10, color="#eee", loc="left")
    ax.set_facecolor("#111")
    fig.patch.set_facecolor("#0a0a0a")
    ax.tick_params(colors="#aaa")
    for spine in ax.spines.values():
        spine.set_color("#444")
    ax.legend(loc="upper right", fontsize=7, framealpha=0.75)
    fig.tight_layout()
    fig.savefig(out_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--report", type=Path, default=None,
                        help="ARRANGEMENT_REPORT_V<N>.json (default: latest)")
    parser.add_argument("--audio-dir", type=Path, default=None,
                        help="Audio folder (default: <project>/Audio)")
    args = parser.parse_args()

    project_dir = args.project_dir
    audio_dir = args.audio_dir or (project_dir / "Audio")

    # Find the report
    if args.report and args.report.exists():
        report_path = args.report
    else:
        candidates = sorted((project_dir / "Output").glob("ARRANGEMENT_REPORT*.json"),
                            key=lambda p: p.stat().st_mtime)
        if not candidates:
            print(f"ERROR: no ARRANGEMENT_REPORT*.json in {project_dir/'Output'}",
                  file=sys.stderr)
            return 1
        report_path = candidates[-1]

    rep = json.loads(report_path.read_text(encoding="utf-8"))
    loops = rep.get("loops", []) or []
    if not loops:
        print(f"No loops in {report_path.name} — nothing to render.")
        return 0

    # Derive version from filename
    stem = report_path.stem  # ARRANGEMENT_REPORT_V12
    version = "V?"
    for token in stem.split("_"):
        if token.startswith("V") and token[1:].isdigit():
            version = token
            break

    out_dir = project_dir / "Output" / "Visualisations" / f"Loops_{version}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # BPMs from the same report
    bpm_lookup = {t["name"]: t["bpm"] for t in rep.get("tracks", [])}

    print(f"Found {len(loops)} loop(s). Rendering to {out_dir}")
    for i, ls in enumerate(loops, 1):
        track = ls["track"]
        ltype = ls["type"]   # "tail" or "intro"
        # source_beats is encoded as "X-Y" in the report
        try:
            beat_a, beat_b = map(float, str(ls["source_beats"]).split("-"))
        except Exception:
            print(f"  L{i:02d}: cannot parse source_beats={ls['source_beats']}, skip")
            continue
        count = int(ls.get("count", 1))
        bpm = bpm_lookup.get(track)
        if not bpm:
            print(f"  L{i:02d}: no BPM for {track}, skip")
            continue

        wav = audio_dir / f"{track}.wav"
        if not wav.exists():
            print(f"  L{i:02d}: no audio at {wav}, skip")
            continue
        print(f"  L{i:02d} {track[:40]} {ltype} beats {beat_a:.0f}-{beat_b:.0f} ×{count}")
        y, sr = librosa.load(str(wav), sr=22050, mono=True)
        safe_track = track.replace("/", "_").replace("\\", "_")[:50]
        out_path = out_dir / f"L{i:02d}_{safe_track}_{ltype}.png"
        render_loop(y, sr, bpm, beat_a, beat_b, track, ltype, count, out_path, i)
        print(f"     → {out_path.name}")

    print(f"\nDone. {len(loops)} loop PNG(s) in {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
