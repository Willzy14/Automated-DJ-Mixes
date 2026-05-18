"""Blank-canvas track preview — the image Claude looks at BEFORE deciding.

Renders one PNG per track containing ONLY the raw signals — waveform,
MIK cues, MIK energy strip, Rekordbox phrase bands. NO candidate picks.
NO automation. NO loop region.

The purpose: give Claude (or Sam) a clean view of the source material
so a visual broad-strokes pass can identify where the drops, breaks,
and outro live BEFORE the pipeline tries to compute anything. Those
visual hints then drive the pipeline's precision step.

Saved to `Output/Visualisations/Previews/Preview_<track>.png`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import librosa
import matplotlib.pyplot as plt
import numpy as np

from automated_dj_mixes.analysis import TrackAnalysis

# Match phrase_viz palette
COLOUR_INTRO  = "#7ec850"
COLOUR_DROP   = "#f0c020"
COLOUR_BREAK  = "#5099d8"
COLOUR_OUTRO  = "#e25f5f"
COLOUR_NEUTRAL = "#888888"

RB_LABEL_COLOUR = {
    "intro":  COLOUR_INTRO,
    "up":     COLOUR_BREAK,
    "down":   COLOUR_BREAK,
    "chorus": COLOUR_DROP,
    "outro":  COLOUR_OUTRO,
}

ENERGY_COLOURS = [
    "#1a4d8f", "#2c6fb5", "#4798d6", "#71b8e0",
    "#a3d5c2", "#c8e598", "#e5dc6a", "#e8b04a",
    "#e07c2a", "#c93838",
]


@dataclass
class PreviewContext:
    """All data needed to render a blank-canvas preview."""
    track_index: int
    analysis: TrackAnalysis
    mik_cues_sec: list[float]
    mik_energy_segments: list           # list[MikEnergySegment]
    rb_phrases: list | None = None
    project_bpm: float = 128.0


def render_preview(ctx: PreviewContext, out_dir: Path) -> Path:
    """Render the blank-canvas preview and return the output path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    y, sr = librosa.load(str(ctx.analysis.path), sr=4000, mono=True)
    duration_sec = len(y) / sr
    first_downbeat_sec = ctx.analysis.first_downbeat_sec or 0.0
    bpm = ctx.analysis.bpm or 128.0
    sec_per_beat = 60.0 / bpm

    fig, axes = plt.subplots(
        3, 1, figsize=(22, 6), dpi=110, sharex=True,
        gridspec_kw={"height_ratios": [0.5, 5, 0.6], "hspace": 0.10},
    )
    ax_phrases, ax_wave, ax_energy = axes

    title = (
        f"PREVIEW — Track {ctx.track_index}:  {ctx.analysis.path.stem}\n"
        f"BPM {bpm:.0f}  |  Key {ctx.analysis.camelot or '?'}  |  "
        f"LUFS {ctx.analysis.lufs:.1f}  |  Duration {duration_sec:.0f}s "
        f"({duration_sec / 60:.1f}min)"
    )
    fig.suptitle(title, fontsize=11, y=0.995)

    # ===== PHRASE STRIP =====
    if ctx.rb_phrases:
        for p in ctx.rb_phrases:
            p_start_sec = first_downbeat_sec + (p.start_beat - 1) * sec_per_beat
            if not hasattr(p, "end_beat") or not p.end_beat:
                continue
            p_end_sec = first_downbeat_sec + (p.end_beat - 1) * sec_per_beat
            colour = RB_LABEL_COLOUR.get(p.label, COLOUR_NEUTRAL)
            ax_phrases.axvspan(p_start_sec, p_end_sec, color=colour, alpha=0.9, linewidth=0)
            if p_end_sec - p_start_sec > 6:
                ax_phrases.text((p_start_sec + p_end_sec) / 2, 0.5,
                                str(p.label), ha="center", va="center",
                                fontsize=8, fontweight="bold", color="white",
                                transform=ax_phrases.get_xaxis_transform())
    else:
        ax_phrases.text(duration_sec / 2, 0.5, "no Rekordbox phrase data",
                        ha="center", va="center", fontsize=9, color="#999999",
                        transform=ax_phrases.get_xaxis_transform())
    ax_phrases.set_yticks([])
    ax_phrases.set_xlim(0, duration_sec)
    ax_phrases.set_title("Rekordbox phrases (intro=green, drop=yellow, break=blue, outro=red)",
                         fontsize=9, loc="left")

    # ===== WAVEFORM =====
    peak = float(np.max(np.abs(y))) or 1.0
    yn = y / peak * 0.9
    seg_times = np.linspace(0, duration_sec, len(yn))
    ax_wave.fill_between(seg_times, -np.abs(yn), np.abs(yn),
                         color="#333333", alpha=0.7, linewidth=0)

    # MIK cues (numbered, labelled)
    for i, t in enumerate(sorted(ctx.mik_cues_sec), start=1):
        ax_wave.axvline(t, color="#cc44cc", linestyle=":", linewidth=1.2, alpha=0.75)
        ax_wave.text(t, 0.97, f"MIK{i}\n{t:.0f}s", color="#cc44cc",
                     fontsize=8, ha="left", va="top", rotation=90,
                     transform=ax_wave.get_xaxis_transform(), alpha=0.85,
                     fontweight="bold")

    # Tiered phrase grid — Sam's rule (2026-05): bar / 2-bar / 4-bar / 16-bar
    # lines visually weighted so off-phrase positions jump out.
    sec_per_bar = 4 * sec_per_beat
    for n_bar in range(0, int(duration_sec / sec_per_bar) + 1):
        t = n_bar * sec_per_bar
        if n_bar % 16 == 0:
            ax_wave.axvline(t, color="#222222", linewidth=1.3, alpha=0.85)
            ax_wave.text(t, 1.02, f"bar {n_bar}", fontsize=8, fontweight="bold",
                         ha="center", va="bottom", color="#222222",
                         transform=ax_wave.get_xaxis_transform())
        elif n_bar % 4 == 0:
            ax_wave.axvline(t, color="#666666", linewidth=1.0, alpha=0.7)
            ax_wave.text(t, 1.01, f"{n_bar}", fontsize=6,
                         ha="center", va="bottom", color="#666666",
                         transform=ax_wave.get_xaxis_transform())
        elif n_bar % 2 == 0:
            ax_wave.axvline(t, color="#999999", linewidth=0.7, alpha=0.55)
        else:
            ax_wave.axvline(t, color="#cccccc", linewidth=0.5, alpha=0.4)

    ax_wave.set_ylim(-1.05, 1.05)
    ax_wave.set_yticks([])
    ax_wave.set_xlim(0, duration_sec)
    ax_wave.set_title("Waveform + MIK cues (NO picks yet — this is the canvas for visual review)",
                      fontsize=9, loc="left")

    # ===== MIK ENERGY STRIP =====
    if ctx.mik_energy_segments:
        for s in ctx.mik_energy_segments:
            e = max(1, min(10, int(s.energy)))
            colour = ENERGY_COLOURS[e - 1]
            ax_energy.axvspan(s.start_sec, s.end_sec, color=colour, alpha=0.9, linewidth=0)
            if s.end_sec - s.start_sec > 5:
                mid = (s.start_sec + s.end_sec) / 2
                ax_energy.text(mid, 0.5, f"E{e}", ha="center", va="center",
                               fontsize=8, fontweight="bold", color="white",
                               transform=ax_energy.get_xaxis_transform())
    else:
        ax_energy.text(duration_sec / 2, 0.5, "no MIK energy data",
                       ha="center", va="center", fontsize=9, color="#999999",
                       transform=ax_energy.get_xaxis_transform())
    ax_energy.set_yticks([])
    ax_energy.set_xlim(0, duration_sec)
    ax_energy.set_title("MIK energy (1=blue → 10=red)", fontsize=9, loc="left")
    ax_energy.set_xlabel("Source time (seconds)")

    plt.tight_layout(rect=(0.01, 0.02, 0.99, 0.96))

    out_name = f"Preview_{ctx.track_index:02d}_{ctx.analysis.path.stem[:60].replace(' ', '_')}.png"
    out_path = out_dir / out_name
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return out_path
