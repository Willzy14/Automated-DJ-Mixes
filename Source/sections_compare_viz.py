"""Render zoomed comparison waveform PNGs — Sam's V7 truth vs Claude's V8.

Each track gets 4 PNGs (quarters of the duration) so 1-bar fills are visible.
Overlays:
  - V7 (Sam's truth): RED solid line per chop, cyan dot for Fill markers
  - V8 (mine):        YELLOW dashed line per chop
  - Bar gridlines:    4-bar (light), 16-bar (heavy)
  - Bar-math:         delta-from-previous-event annotated above each chop
                      ✓ if delta ∈ {4, 8, 12, 16, 24, 32, ...}, ⚠ otherwise

Used to diagnose where the algorithm puts chops at wrong amplitude points or
at non-multiple-of-4-bar boundaries. Claude reads these PNGs to drive fixes.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import librosa
import matplotlib.pyplot as plt
import numpy as np


LABEL_COLOURS = {
    "intro":  "#7ec850",  # green
    "build":  "#5bc0de",  # cyan
    "drop":   "#f0c020",  # yellow
    "break":  "#5099d8",  # blue
    "fill":   "#e8a04a",  # orange
    "outro":  "#e25f5f",  # red
    "unknown": "#888888",
}

NICE_DELTAS = {4, 8, 12, 16, 20, 24, 28, 32, 40, 48, 56, 64, 80, 96, 128}


@dataclass
class TrackBPMInfo:
    bpm: float
    first_downbeat_sec: float


def normalize_label(name: str) -> str:
    n = name.lower().strip()
    # Strip _N suffix
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


def is_nice_delta(delta_bars: float) -> bool:
    rounded = round(delta_bars)
    return abs(delta_bars - rounded) < 0.3 and rounded in NICE_DELTAS


def render_track_quarters(
    track_name: str,
    audio_path: Path,
    bpm: float,
    first_downbeat_sec: float,
    v7_clips: list,
    v8_clips: list,
    out_dir: Path,
    quarters: int = 4,
) -> list[Path]:
    """Render `quarters` PNGs covering equal-time slices of one track."""
    print(f"  Loading audio: {audio_path.name}")
    y, sr = librosa.load(str(audio_path), sr=4000, mono=True)
    duration_sec = len(y) / sr

    # Envelope: peak abs per ~100ms window
    samples_per_bin = max(1, int(0.05 * sr))
    n_bins = len(y) // samples_per_bin
    env = np.abs(y[: n_bins * samples_per_bin]).reshape(n_bins, samples_per_bin).max(axis=1)
    env_time = np.arange(n_bins) * samples_per_bin / sr

    sec_per_beat = 60.0 / bpm
    sec_per_bar = 4 * sec_per_beat

    # Pre-convert clips to (start_sec, end_sec, label, name) tuples
    def prep_clips(clips):
        out = []
        for c in clips:
            ss = c.get("source_start_beats")
            se = c.get("source_end_beats")
            if ss is None or se is None:
                continue
            lbl = normalize_label(c["name"])
            start_sec = src_beat_to_sec(ss, bpm, first_downbeat_sec)
            end_sec = src_beat_to_sec(se, bpm, first_downbeat_sec)
            out.append({
                "start_sec": start_sec,
                "end_sec": end_sec,
                "label": lbl,
                "name": c["name"],
                "src_beat": ss,
            })
        return sorted(out, key=lambda c: c["start_sec"])

    v7 = prep_clips(v7_clips)
    v8 = prep_clips(v8_clips)

    # Bar-math: compute delta from previous V8 chop for each V8 chop
    for i, c in enumerate(v8):
        if i == 0:
            c["delta_bars"] = None
            c["delta_ok"] = True
        else:
            d = (c["start_sec"] - v8[i - 1]["start_sec"]) / sec_per_bar
            c["delta_bars"] = d
            c["delta_ok"] = is_nice_delta(d)

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

        # Bar gridlines
        first_bar_sec = first_downbeat_sec
        # Compute bar numbers in this quarter
        bar_start = int(max(0, (t0 - first_bar_sec) / sec_per_bar - 1))
        bar_end = int((t1 - first_bar_sec) / sec_per_bar + 2)
        for b in range(bar_start, bar_end):
            x = first_bar_sec + b * sec_per_bar
            if x < t0 or x > t1:
                continue
            if b % 16 == 0:
                ax.axvline(x, color="#ffffff", alpha=0.35, linewidth=1.2, zorder=1)
                ax.text(x, 1.02, f"bar {b}", color="#ddd", fontsize=8,
                        ha="center", va="bottom", rotation=0)
            elif b % 4 == 0:
                ax.axvline(x, color="#888", alpha=0.25, linewidth=0.6, zorder=1)

        # V7 (Sam's truth) — RED solid chop lines, plus colored region underline
        for c in v7:
            if c["end_sec"] < t0 or c["start_sec"] > t1:
                continue
            sx = max(c["start_sec"], t0)
            ex = min(c["end_sec"], t1)
            # Underline shows the section type
            ax.axhspan(-1.0, -0.92, xmin=(sx - t0) / (t1 - t0),
                       xmax=(ex - t0) / (t1 - t0),
                       color=LABEL_COLOURS.get(c["label"], "#888"), alpha=0.9, zorder=2)
            # Chop line at start
            if t0 <= c["start_sec"] <= t1:
                ax.axvline(c["start_sec"], color="#ff3030", alpha=0.85,
                           linewidth=2.0, zorder=4)
                bar_n = (c["start_sec"] - first_bar_sec) / sec_per_bar
                # Mark Fill specially (small marker, cyan dot)
                if c["label"] == "fill":
                    ax.plot(c["start_sec"], 0.92, marker="v", color="#5bc0de",
                            markersize=14, zorder=5)
                ax.text(c["start_sec"], -0.78,
                        f"V7 {c['label']}\nbar {bar_n:.1f}",
                        color="#ff5050", fontsize=8, ha="left", va="top",
                        rotation=90, alpha=0.95)

        # V8 (mine) — YELLOW dashed chop lines
        for c in v8:
            if c["start_sec"] < t0 or c["start_sec"] > t1:
                continue
            ax.axvline(c["start_sec"], color="#ffff20", alpha=0.85,
                       linewidth=1.5, linestyle="--", zorder=3)
            bar_n = (c["start_sec"] - first_bar_sec) / sec_per_bar
            delta_str = ""
            if c["delta_bars"] is not None:
                tick = "OK" if c["delta_ok"] else "BAD"
                delta_str = f"\nΔ{c['delta_bars']:.1f}b {tick}"
            ax.text(c["start_sec"], 0.78,
                    f"V8 {c['label']}\nbar {bar_n:.1f}{delta_str}",
                    color="#ffe060", fontsize=8, ha="right", va="top",
                    rotation=90, alpha=0.95)

        title = (
            f"{track_name}  —  Quarter {q+1}/{quarters}  ({t0:.0f}s..{t1:.0f}s)\n"
            f"BPM {bpm:.1f}  |  RED=Sam V7 truth  |  YELLOW dashed=Claude V8 mine  "
            f"|  Cyan ▼=Sam Fill event"
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
    v7_path = base / "Output" / "Sections Review" / "Sections_V7_Sams_Changes.json"
    # Default to latest Sections V<N>.json
    candidates = sorted(
        (base / "Sections Review").glob("Sections_V*.json"),
        key=lambda p: int(p.stem.split("V")[1].split("_")[0]) if p.stem.split("V")[1].split("_")[0].isdigit() else 0,
    )
    v8_path = candidates[-1] if candidates else (base / "Sections Review" / "Sections_V11.json")
    out_dir = base / "Sections Review" / f"Compare_{v8_path.stem.replace('Sections_', '')}_vs_V7"
    out_dir.mkdir(parents=True, exist_ok=True)

    v7_data = json.loads(v7_path.read_text(encoding="utf-8"))
    v8_data = json.loads(v8_path.read_text(encoding="utf-8"))

    # Get BPM + first_downbeat_sec per track via rekordbox.
    from automated_dj_mixes.analysis import analyse_folder, enrich_from_rekordbox
    from automated_dj_mixes.rekordbox_reader import read_rekordbox_library, find_rekordbox_match

    print("Loading audio analyses...")
    analyses = analyse_folder(audio_dir)
    rb_lib = read_rekordbox_library()
    by_stem: dict[str, TrackBPMInfo] = {}
    for a in analyses:
        rb_match = find_rekordbox_match(a.path.name, rb_lib)
        if rb_match:
            enrich_from_rekordbox(a, rb_match)
        by_stem[a.path.stem] = TrackBPMInfo(
            bpm=a.bpm,
            first_downbeat_sec=a.first_downbeat_sec or 0.0,
        )

    track_names_v7 = list(v7_data.keys())
    track_names_v8 = list(v8_data.keys())

    print(f"V7 tracks: {len(track_names_v7)}, V8 tracks: {len(track_names_v8)}")

    rendered = 0
    for v8_name in track_names_v8:
        if not v8_name or "Audio" in v8_name:
            continue
        # Match V7 name (V8 has HTML-escaped apostrophes from XML, V7 doesn't)
        candidates = [v8_name, v8_name.replace("&apos;", "'"), v8_name.replace("'", "&apos;")]
        v7_clips = None
        for c in candidates:
            if c in v7_data:
                v7_clips = v7_data[c]
                break
        if v7_clips is None:
            print(f"  No V7 entry for {v8_name}, skipping")
            continue

        info = by_stem.get(v8_name) or by_stem.get(v8_name.replace("&apos;", "'"))
        if not info:
            print(f"  No analysis for {v8_name}, skipping")
            continue

        # Audio file uses literal apostrophe, not HTML entity
        audio_path = audio_dir / f"{v8_name.replace('&apos;', chr(39))}.wav"
        if not audio_path.exists():
            print(f"  No audio file for {v8_name}")
            continue

        print(f"\nRendering: {v8_name}")
        render_track_quarters(
            track_name=v8_name,
            audio_path=audio_path,
            bpm=info.bpm,
            first_downbeat_sec=info.first_downbeat_sec,
            v7_clips=v7_clips,
            v8_clips=v8_data[v8_name],
            out_dir=out_dir,
        )
        rendered += 1

    print(f"\nRendered {rendered} tracks ({rendered * 4} PNGs) to {out_dir}")


if __name__ == "__main__":
    sys.exit(main() or 0)
