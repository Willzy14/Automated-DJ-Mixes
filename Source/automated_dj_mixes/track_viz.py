"""Per-track PNG visualisation — the full timeline of a single source.

Renders ONE PNG per track showing:
  - Full waveform (entire track length)
  - All MIK auto-cues as pink dotted lines (numbered)
  - MIK energy segments as a coloured heatmap strip below the waveform
  - Rekordbox phrases as coloured background bands
  - The candidates Claude picked (bass_entry, outro_start, chop_point)
    as bold labelled vertical lines
  - Loop region as a green hatched band (if the track is used as an
    outgoing somewhere)
  - Volume + EQ-bass automation lanes (sparse — only have content during
    transitions involving this track)

Lets a reviewer see ALL of the structural information in one image so
they can verify the cue picks are sensible.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import librosa
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from automated_dj_mixes.analysis import TrackAnalysis
from automated_dj_mixes.automation import AutomationPoint
from automated_dj_mixes.cue_candidates import CueCandidate, first_credible, first_drop_candidate
from automated_dj_mixes.transition import LoopSpec, MIN_CANDIDATE_CONFIDENCE

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

# MIK energy 1-10 → red (low) through green (high)
ENERGY_COLOURS = [
    "#1a4d8f", "#2c6fb5", "#4798d6", "#71b8e0",
    "#a3d5c2", "#c8e598", "#e5dc6a", "#e8b04a",
    "#e07c2a", "#c93838",
]


@dataclass
class TrackVizContext:
    """All data needed to render one per-track PNG."""
    track_index: int                      # 1-based position in sequence
    analysis: TrackAnalysis
    candidates: list[CueCandidate]
    mik_cues_sec: list[float]
    mik_energy_segments: list             # list[MikEnergySegment]
    rb_phrases: list | None = None        # list[PhraseEntry] | None
    rb_first_downbeat_offset: int = 0
    loop_spec: LoopSpec | None = None     # set when this track is an outgoing
    arrangement_start_beats: float = 0.0  # for automation arrangement→source mapping
    project_bpm: float = 128.0
    # Automation curves are stored in arrangement-beat space. We convert
    # back to source-seconds when plotting on the per-track axis.
    volume_points: list[AutomationPoint] | None = None
    eq_bass_points: list[AutomationPoint] | None = None


def _load_waveform(audio_path: Path, sr_target: int = 4000) -> tuple[np.ndarray, int]:
    """Low-SR mono load — enough resolution for full-track visualisation."""
    y, sr = librosa.load(str(audio_path), sr=sr_target, mono=True)
    return y, sr


def _draw_phrase_grid(ax, t_start_sec: float, t_end_sec: float,
                      project_bpm: float, label_phrases: bool = False) -> None:
    """Tiered grid: bar/2-bar/4-bar/16-bar lines, visually weighted.

    Same styling as transition_viz._draw_grid_ticks. Sam's rule (2026-05):
    automation must hit phrase boundaries — the grid lines need to be
    obvious enough that off-phrase picks jump out visually.
    """
    sec_per_bar = 4 * 60.0 / project_bpm
    first_bar_n = int(t_start_sec / sec_per_bar)
    last_bar_n = int(t_end_sec / sec_per_bar) + 1
    for n_bar in range(first_bar_n, last_bar_n + 1):
        t = n_bar * sec_per_bar
        if t < t_start_sec - sec_per_bar or t > t_end_sec + sec_per_bar:
            continue
        if n_bar % 16 == 0:  # 16-bar section
            ax.axvline(t, color="#222222", linewidth=1.3, alpha=0.85)
            if label_phrases:
                ax.text(t, 1.02, f"bar {n_bar}", fontsize=8, fontweight="bold",
                        ha="center", va="bottom", color="#222222",
                        transform=ax.get_xaxis_transform())
        elif n_bar % 4 == 0:  # 4-bar phrase
            ax.axvline(t, color="#666666", linewidth=1.0, alpha=0.7)
            if label_phrases:
                ax.text(t, 1.01, f"{n_bar}", fontsize=6,
                        ha="center", va="bottom", color="#666666",
                        transform=ax.get_xaxis_transform())
        elif n_bar % 2 == 0:
            ax.axvline(t, color="#999999", linewidth=0.7, alpha=0.55)
        else:
            ax.axvline(t, color="#cccccc", linewidth=0.5, alpha=0.4)


def _draw_phrase_bands(ax, phrases, first_downbeat_sec: float,
                       sec_per_beat: float, ylim: float = 1.05) -> None:
    if not phrases:
        return
    for p in phrases:
        p_start_sec = first_downbeat_sec + (p.start_beat - 1) * sec_per_beat
        if not hasattr(p, "end_beat") or not p.end_beat:
            continue
        p_end_sec = first_downbeat_sec + (p.end_beat - 1) * sec_per_beat
        colour = RB_LABEL_COLOUR.get(p.label, COLOUR_NEUTRAL)
        ax.axvspan(p_start_sec, p_end_sec, color=colour, alpha=0.18, linewidth=0)


def _draw_energy_strip(ax, energy_segments, duration_sec: float) -> None:
    """MIK energy as a coloured horizontal strip (1=blue/low, 10=red/high)."""
    if not energy_segments:
        ax.text(duration_sec / 2, 0.5, "no MIK energy data",
                ha="center", va="center", fontsize=9, color="#999999",
                transform=ax.transData if False else ax.transAxes)
        return
    for s in energy_segments:
        e = max(1, min(10, int(s.energy)))
        colour = ENERGY_COLOURS[e - 1]
        ax.axvspan(s.start_sec, s.end_sec, color=colour, alpha=0.85, linewidth=0)
        # Label the segment with the energy value
        if s.end_sec - s.start_sec > 5:
            mid = (s.start_sec + s.end_sec) / 2
            ax.text(mid, 0.5, f"E{e}", ha="center", va="center",
                    fontsize=8, fontweight="bold", color="white",
                    transform=ax.get_xaxis_transform())


def _draw_mik_cues(ax, cue_times_sec: list[float], ylim_top: float = 0.92) -> None:
    for i, t in enumerate(sorted(cue_times_sec), start=1):
        ax.axvline(t, color="#cc44cc", linestyle=":", linewidth=1.0, alpha=0.7)
        ax.text(t, ylim_top, f"MIK{i}\n{t:.0f}s", color="#cc44cc",
                fontsize=7, ha="left", va="top", rotation=90,
                transform=ax.get_xaxis_transform(), alpha=0.8)


def _draw_candidates(ax, cands: list[CueCandidate],
                     first_downbeat_sec: float, sec_per_beat: float) -> None:
    """Bold lines + labels at the PICKED candidates — one per cue_type, chosen
    by the SAME selectors plan_transition uses (so the viz matches the actual
    transition decisions, not just "highest confidence").
    """
    type_colour = {
        "bass_entry":  "#00aa55",
        "break_start": "#0066cc",
        "break_end":   "#0066cc",
        "outro_start": "#cc3333",
        "chop_point":  "#aa00aa",
    }
    cands = list(cands)
    picks: dict[str, CueCandidate] = {}
    # bass_entry: dance-music structural prior — earliest credible
    bass_entry_pick = first_drop_candidate(cands, MIN_CANDIDATE_CONFIDENCE)
    if bass_entry_pick:
        picks["bass_entry"] = bass_entry_pick
    # Other cue types: highest credible
    for ctype in ("break_start", "break_end", "outro_start", "chop_point"):
        pick = first_credible(cands, ctype, MIN_CANDIDATE_CONFIDENCE)
        if pick:
            picks[ctype] = pick

    # Stagger label vertical positions per type so overlapping picks don't
    # collide visually.
    label_y_for = {
        "bass_entry":  0.04,
        "break_end":   0.18,
        "break_start": 0.32,
        "outro_start": 0.04,
        "chop_point":  0.18,
    }
    for ctype, c in picks.items():
        t = c.sec if c.sec else first_downbeat_sec + c.beat * sec_per_beat
        colour = type_colour.get(ctype, "#000000")
        ax.axvline(t, color=colour, linewidth=2.0, alpha=0.9)
        y = label_y_for.get(ctype, 0.04)
        ax.text(t, y, f"{ctype}\nconf={c.confidence:.2f}",
                color=colour, fontsize=8, ha="left", va="bottom",
                fontweight="bold",
                transform=ax.get_xaxis_transform(),
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=colour, alpha=0.85))


def _draw_loop_region(ax, loop_spec: LoopSpec | None,
                      first_downbeat_sec: float, sec_per_beat: float) -> None:
    if loop_spec is None:
        return
    start_sec = first_downbeat_sec + loop_spec.loop_source_start * sec_per_beat
    end_sec = first_downbeat_sec + loop_spec.loop_source_end * sec_per_beat
    ax.axvspan(start_sec, end_sec, color="#88dd88", alpha=0.40,
               hatch="//", linewidth=0)
    chop_sec = first_downbeat_sec + loop_spec.chop_at_beats * sec_per_beat
    ax.axvline(chop_sec, color="#aa00aa", linewidth=1.5, alpha=0.6,
               linestyle="--")
    ax.text(chop_sec, 0.97, f"chop\n×{loop_spec.num_extra_copies} loops",
            color="#aa00aa", fontsize=7, ha="right", va="top",
            transform=ax.get_xaxis_transform(), alpha=0.85)


def _draw_automation_on_track(ax, points: list[AutomationPoint] | None,
                              arrangement_start_beats: float,
                              first_downbeat_sec: float,
                              sec_per_beat: float,
                              label: str, colour: str) -> None:
    """Plot automation against the track's source-time axis.

    Arrangement beat X corresponds to source second = first_downbeat_sec +
    (X - arrangement_start_beats) * sec_per_beat.
    """
    if not points:
        return
    times = []
    values = []
    for p in points:
        src_sec = first_downbeat_sec + (p.time_beats - arrangement_start_beats) * sec_per_beat
        times.append(src_sec)
        values.append(p.value)
    ax.plot(times, values, color=colour, linewidth=1.8, label=label,
            marker="o", markersize=3)


def render_track(ctx: TrackVizContext, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    y, sr = _load_waveform(ctx.analysis.path)
    duration_sec = len(y) / sr
    first_downbeat_sec = ctx.analysis.first_downbeat_sec or 0.0
    bpm = ctx.analysis.bpm or 128.0
    sec_per_beat = 60.0 / bpm

    fig, axes = plt.subplots(
        4, 1, figsize=(22, 8.5), dpi=110, sharex=True,
        gridspec_kw={"height_ratios": [0.5, 5, 0.6, 1.4], "hspace": 0.10},
    )
    ax_phrases, ax_wave, ax_energy, ax_auto = axes

    title = (
        f"Track {ctx.track_index}:  {ctx.analysis.path.stem}    "
        f"BPM {ctx.analysis.bpm:.0f}  |  Key {ctx.analysis.camelot or '?'}  "
        f"|  LUFS {ctx.analysis.lufs:.1f}  |  Duration {duration_sec:.0f}s "
        f"({duration_sec / 60:.1f}min)"
    )
    fig.suptitle(title, fontsize=11, y=0.995)

    # ===== PHRASE STRIP (dedicated full-opacity strip above the waveform) =====
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
    # Faint phrase tint behind the waveform so they cross-reference
    _draw_phrase_bands(ax_wave, ctx.rb_phrases, first_downbeat_sec, sec_per_beat)
    # Normalise per-track for visibility
    peak = float(np.max(np.abs(y))) or 1.0
    yn = y / peak * 0.9
    seg_times = np.linspace(0, duration_sec, len(yn))
    ax_wave.fill_between(seg_times, -np.abs(yn), np.abs(yn),
                         color="#333333", alpha=0.65, linewidth=0)
    _draw_phrase_grid(ax_wave, 0, duration_sec, ctx.project_bpm, label_phrases=True)
    _draw_loop_region(ax_wave, ctx.loop_spec, first_downbeat_sec, sec_per_beat)
    _draw_mik_cues(ax_wave, ctx.mik_cues_sec)
    _draw_candidates(ax_wave, ctx.candidates, first_downbeat_sec, sec_per_beat)
    ax_wave.set_ylim(-1.05, 1.05)
    ax_wave.set_yticks([])
    ax_wave.set_xlim(0, duration_sec)
    ax_wave.set_title("Waveform + RB phrases (intro=green, drop=yellow, break=blue, outro=red) + MIK cues + picked candidates",
                      fontsize=9, loc="left")

    # ===== MIK ENERGY STRIP =====
    _draw_energy_strip(ax_energy, ctx.mik_energy_segments, duration_sec)
    ax_energy.set_yticks([])
    ax_energy.set_xlim(0, duration_sec)
    ax_energy.set_title("MIK energy (1=blue/quiet → 10=red/loud)",
                        fontsize=9, loc="left")

    # ===== AUTOMATION =====
    _draw_automation_on_track(
        ax_auto, ctx.volume_points,
        ctx.arrangement_start_beats, first_downbeat_sec, sec_per_beat,
        "volume", "#cc0000",
    )
    _draw_automation_on_track(
        ax_auto, ctx.eq_bass_points,
        ctx.arrangement_start_beats, first_downbeat_sec, sec_per_beat,
        "eq bass", "#0066aa",
    )
    ax_auto.set_ylim(-0.05, 1.10)
    ax_auto.set_yticks([0, 0.5, 1.0])
    ax_auto.set_xlim(0, duration_sec)
    ax_auto.set_xlabel("Source time (seconds)")
    ax_auto.legend(loc="upper right", fontsize=8, framealpha=0.9)
    ax_auto.grid(True, alpha=0.2)
    ax_auto.set_title("Volume + EQ bass automation (mapped from arrangement back to source time)",
                      fontsize=9, loc="left")

    # Bar tick marks every 16 bars on the bottom axis
    sec_per_bar = 4 * sec_per_beat
    bar_secs = np.arange(0, duration_sec, sec_per_bar * 16)
    ax_auto.set_xticks(bar_secs)
    ax_auto.set_xticklabels([f"{int(t)}s\n({int(t / sec_per_bar)})" for t in bar_secs],
                            fontsize=7)

    plt.tight_layout(rect=(0.01, 0.02, 0.99, 0.97))

    out_name = f"Track_{ctx.track_index:02d}_{ctx.analysis.path.stem[:60].replace(' ', '_')}.png"
    out_path = out_dir / out_name
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return out_path
