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
    y, sr = librosa.load(str(audio_path), sr=4000, mono=True)
    duration_sec = len(y) / sr

    samples_per_bin = max(1, int(0.05 * sr))
    n_bins = len(y) // samples_per_bin
    env = np.abs(y[: n_bins * samples_per_bin]).reshape(n_bins, samples_per_bin).max(axis=1)
    env_time = np.arange(n_bins) * samples_per_bin / sr

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
        })
    clips.sort(key=lambda c: c["start_sec"])

    out_paths = []
    quarter_dur = duration_sec / quarters
    safe_name = track_name.replace("/", "_").replace("\\", "_").replace("–", "-").replace("'", "_")[:60]

    for q in range(quarters):
        t0 = q * quarter_dur
        t1 = (q + 1) * quarter_dur

        fig, ax = plt.subplots(figsize=(22, 7), dpi=120)
        mask = (env_time >= t0) & (env_time <= t1)
        ax.fill_between(env_time[mask], -env[mask], env[mask], color="#444", linewidth=0)
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

        # Bottom strip = V12 label colors
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
            f"BPM {bpm:.1f}  |  BLIND PASS: Only V12 chops shown (yellow dashed)  "
            f"|  Identify energy steps from waveform alone"
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

    return out_paths


def main():
    base = Path("Test Project/Black Book x Defected V2")
    audio_dir = base / "Audio"

    # Auto-find the latest Sections_V<N>.json (skip the V1_baseline.json)
    candidates = []
    for p in (base / "Sections Review").glob("Sections_V*.json"):
        stem = p.stem  # Sections_V13 or Sections_V13_(...)
        # Strip "Sections_V" prefix
        rest = stem[len("Sections_V"):]
        # Take leading digits
        n = ""
        for c in rest:
            if c.isdigit():
                n += c
            else:
                break
        if n:
            candidates.append((int(n), p))
    if not candidates:
        raise FileNotFoundError(f"No Sections_V<N>.json in {base/'Sections Review'}")
    candidates.sort(key=lambda t: t[0])
    version, v12_path = candidates[-1]
    out_dir = base / "Sections Review" / f"Blind_V{version}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Using {v12_path.name} → {out_dir.name}/")
    v12_data = json.loads(v12_path.read_text(encoding="utf-8"))

    from automated_dj_mixes.analysis import analyse_folder, enrich_from_rekordbox
    from automated_dj_mixes.rekordbox_reader import read_rekordbox_library, find_rekordbox_match

    print("Loading audio analyses...")
    analyses = analyse_folder(audio_dir)
    rb_lib = read_rekordbox_library()
    by_stem = {}
    for a in analyses:
        rb_match = find_rekordbox_match(a.path.name, rb_lib)
        if rb_match:
            enrich_from_rekordbox(a, rb_match)
        by_stem[a.path.stem] = (a.bpm, a.first_downbeat_sec or 0.0)

    rendered = 0
    for v12_name in v12_data:
        if not v12_name or "Audio" in v12_name:
            continue
        clean_name = v12_name.replace("&apos;", "'")
        info = by_stem.get(v12_name) or by_stem.get(clean_name)
        if not info:
            print(f"  No analysis for {v12_name}")
            continue
        bpm, first_downbeat_sec = info
        audio_path = audio_dir / f"{clean_name}.wav"
        if not audio_path.exists():
            print(f"  No audio: {clean_name}")
            continue

        print(f"Rendering blind: {clean_name}")
        render_blind_quarters(
            track_name=clean_name,
            audio_path=audio_path,
            bpm=bpm,
            first_downbeat_sec=first_downbeat_sec,
            v12_clips=v12_data[v12_name],
            out_dir=out_dir,
        )
        rendered += 1

    print(f"\nRendered {rendered} tracks ({rendered * 8} blind PNGs) to {out_dir}")


if __name__ == "__main__":
    sys.exit(main() or 0)
