"""Per-transition PNG visualisation.

For each transition, renders ONE PNG showing both tracks aligned in time
with all overlays Claude needs to spot bugs visually:

  - Outgoing waveform (last N bars) with phrase regions colour-coded
  - Incoming waveform (first N bars) with phrase regions colour-coded
  - Volume + EQ-bass automation curves for both tracks
  - Bass-swap as a bold vertical line spanning both tracks
  - Chop point + loop region marked on the outgoing
  - MIK cues + key candidates marked as labelled vertical lines

Output: `Output/Visualisations/Transition_NN_<outgoing>_to_<incoming>.png`

The colour palette matches `phrase_viz.py` (intro green, drop yellow,
break blue, outro red) so it cross-references with the visualisation
.als files Sam already uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import librosa
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from automated_dj_mixes.analysis import TrackAnalysis
from automated_dj_mixes.cue_candidates import CueCandidate
from automated_dj_mixes.transition import TransitionSpec

# Match phrase_viz.py palette (Ableton-style colour-coding)
COLOUR_INTRO  = "#7ec850"   # green
COLOUR_DROP   = "#f0c020"   # yellow
COLOUR_BREAK  = "#5099d8"   # blue
COLOUR_OUTRO  = "#e25f5f"   # red
COLOUR_NEUTRAL = "#888888"

RB_LABEL_COLOUR = {
    "intro":  COLOUR_INTRO,
    "up":     COLOUR_BREAK,
    "down":   COLOUR_BREAK,
    "chorus": COLOUR_DROP,
    "outro":  COLOUR_OUTRO,
}

CONTEXT_BARS = 32   # how many bars on each side of the swap to show
DPI = 110
FIGSIZE_INCHES = (16, 11)


@dataclass
class VizContext:
    """Everything one transition viz needs to know."""
    transition_index: int      # 1-based, for the title
    outgoing: TrackAnalysis
    incoming: TrackAnalysis
    spec: TransitionSpec
    outgoing_total_beats: float
    incoming_total_beats: float
    outgoing_arrangement_start: float
    project_bpm: float
    outgoing_mik_cues_sec: list[float]
    incoming_mik_cues_sec: list[float]
    outgoing_candidates: list[CueCandidate]
    incoming_candidates: list[CueCandidate]
    outgoing_rb_phrases: list | None = None
    incoming_rb_phrases: list | None = None


def _load_waveform(audio_path: Path, sr_target: int = 8000) -> tuple[np.ndarray, int]:
    """Load mono audio at a low SR — fine for visualisation."""
    y, sr = librosa.load(str(audio_path), sr=sr_target, mono=True)
    return y, sr


def _draw_waveform(ax, y: np.ndarray, sr: int, t_start_sec: float, t_end_sec: float,
                   alpha: float = 0.7) -> None:
    """Draw waveform amplitude on `ax` between t_start_sec and t_end_sec."""
    i0 = max(0, int(t_start_sec * sr))
    i1 = min(len(y), int(t_end_sec * sr))
    if i1 <= i0:
        return
    times = np.linspace(t_start_sec, t_end_sec, i1 - i0)
    ax.fill_between(times, -y[i0:i1], y[i0:i1], color="#333333", alpha=alpha, linewidth=0)


def _draw_phrase_bands(ax, phrases, t_start_sec: float, t_end_sec: float,
                       sec_per_beat: float, first_downbeat_sec: float) -> None:
    """Colour-code background by RB phrase label."""
    if not phrases:
        return
    for p in phrases:
        # phrase.start_beat is 1-based PSSI beat
        p_start_sec = first_downbeat_sec + (p.start_beat - 1) * sec_per_beat
        if hasattr(p, "end_beat") and p.end_beat:
            p_end_sec = first_downbeat_sec + (p.end_beat - 1) * sec_per_beat
        else:
            continue
        if p_end_sec <= t_start_sec or p_start_sec >= t_end_sec:
            continue
        clip_start = max(p_start_sec, t_start_sec)
        clip_end = min(p_end_sec, t_end_sec)
        colour = RB_LABEL_COLOUR.get(p.label, COLOUR_NEUTRAL)
        ax.axvspan(clip_start, clip_end, color=colour, alpha=0.18, linewidth=0)


def _draw_mik_cues(ax, cue_times_sec: list[float], t_start_sec: float,
                   t_end_sec: float, y_text: float = 0.92, prefix: str = "MIK") -> None:
    """Vertical lines at MIK cue times (within the displayed window)."""
    for t in cue_times_sec:
        if t_start_sec <= t <= t_end_sec:
            ax.axvline(t, color="#cc44cc", linestyle=":", linewidth=1.0, alpha=0.6)
            ax.text(t, y_text, f"{prefix}@{t:.0f}s", color="#cc44cc",
                    fontsize=7, ha="left", va="top", rotation=90,
                    transform=ax.get_xaxis_transform(), alpha=0.7)


def _draw_candidates(ax, cands: list[CueCandidate], t_start_sec: float,
                     t_end_sec: float, sec_per_beat: float,
                     first_downbeat_sec: float, y_text: float = 0.05) -> None:
    """Bold vertical lines at picked candidates."""
    type_colour = {
        "bass_entry":  "#00aa55",
        "break_start": "#0066cc",
        "break_end":   "#0066cc",
        "outro_start": "#cc3333",
        "chop_point":  "#aa00aa",
    }
    seen: set[str] = set()
    for c in cands:
        if c.cue_type in seen:
            continue
        seen.add(c.cue_type)
        t = c.sec if c.sec else first_downbeat_sec + c.beat * sec_per_beat
        if t_start_sec <= t <= t_end_sec:
            colour = type_colour.get(c.cue_type, "#000000")
            ax.axvline(t, color=colour, linewidth=2.0, alpha=0.9)
            ax.text(t, y_text, f"{c.cue_type}\nconf={c.confidence:.2f}",
                    color=colour, fontsize=8, ha="left", va="bottom",
                    fontweight="bold",
                    transform=ax.get_xaxis_transform(),
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=colour, alpha=0.8))


def _draw_automation(ax, points, t_start_sec: float, t_end_sec: float,
                     beats_per_sec: float, label: str, colour: str) -> None:
    """Draw an automation envelope. `points` are in arrangement beats from
    arrangement beat 0. Values are assumed in [0, 1] (volume + EQ both fit).
    """
    if not points:
        return
    times = []
    values = []
    for p in points:
        t = p.time_beats / beats_per_sec
        if t_start_sec - 5 <= t <= t_end_sec + 5:
            times.append(t)
            values.append(p.value)
    if not times:
        return
    ax.plot(times, values, color=colour, linewidth=1.8, label=label,
            marker="o", markersize=3)


def _draw_grid_ticks(ax, t_start_sec: float, t_end_sec: float,
                     project_bpm: float, label_phrases: bool = False) -> None:
    """Tiered grid lines so phrase boundaries are visually obvious.

    Sam's rule (2026-05): automation MUST land on phrase boundaries. The
    review images need to make off-phrase automation obvious at a glance.
      - Bar lines (4 beats): faint
      - 2-bar lines (8 beats): medium
      - 4-bar phrase lines (16 beats): dark
      - 16-bar section lines (64 beats): bold, with label
    """
    sec_per_beat = 60.0 / project_bpm
    sec_per_bar = 4 * sec_per_beat
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
        elif n_bar % 2 == 0:  # 2-bar
            ax.axvline(t, color="#999999", linewidth=0.7, alpha=0.55)
        else:  # 1-bar
            ax.axvline(t, color="#cccccc", linewidth=0.5, alpha=0.4)


def render_transition(ctx: VizContext, out_dir: Path) -> Path:
    """Render one transition PNG and return the output path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = ctx.spec
    beats_per_sec = ctx.project_bpm / 60.0
    sec_per_beat = 60.0 / ctx.project_bpm

    swap_sec = spec.bass_swap / beats_per_sec
    context_sec = CONTEXT_BARS * 4 / beats_per_sec
    t_lo = swap_sec - context_sec
    t_hi = swap_sec + context_sec

    # --- Audio for outgoing (right edge of the clip aligns with chop) -----
    out_y, out_sr = _load_waveform(ctx.outgoing.path)
    out_arrange_start = ctx.outgoing_arrangement_start
    out_arrange_start_sec = out_arrange_start / beats_per_sec
    out_first_downbeat = ctx.outgoing.first_downbeat_sec or 0.0
    # Map arrangement time → source time for outgoing
    def out_arr_to_src(arr_sec: float) -> float:
        return out_first_downbeat + (arr_sec - out_arrange_start_sec)

    # --- Audio for incoming (left edge of the clip aligns with transition_start)
    in_y, in_sr = _load_waveform(ctx.incoming.path)
    in_arrange_start = spec.transition_start
    in_arrange_start_sec = in_arrange_start / beats_per_sec
    in_first_downbeat = ctx.incoming.first_downbeat_sec or 0.0
    def in_arr_to_src(arr_sec: float) -> float:
        return in_first_downbeat + (arr_sec - in_arrange_start_sec)

    fig, axes = plt.subplots(
        4, 1, figsize=FIGSIZE_INCHES, dpi=DPI, sharex=True,
        gridspec_kw={"height_ratios": [3, 1.2, 3, 1.2], "hspace": 0.08},
    )
    ax_out_wave, ax_out_auto, ax_in_wave, ax_in_auto = axes

    fig.suptitle(
        f"Transition {ctx.transition_index}:  "
        f"{ctx.outgoing.path.stem}  →  {ctx.incoming.path.stem}\n"
        f"swap @ arr-beat {spec.bass_swap:.0f} ({swap_sec:.1f}s)  |  "
        f"overlap {(spec.transition_end - spec.transition_start) / 4:.0f} bars  |  "
        f"loop_src=[{spec.outgoing_loop.loop_source_start:.0f}-"
        f"{spec.outgoing_loop.loop_source_end:.0f}] x{spec.outgoing_loop.num_extra_copies}",
        fontsize=10, y=0.995,
    )

    # ===== OUTGOING WAVEFORM =====
    ax_out_wave.set_title(f"OUTGOING — {ctx.outgoing.path.stem} (BPM {ctx.outgoing.bpm:.0f})",
                          fontsize=9, loc="left")
    _draw_grid_ticks(ax_out_wave, t_lo, t_hi, ctx.project_bpm, label_phrases=True)
    _draw_phrase_bands(
        ax_out_wave, ctx.outgoing_rb_phrases,
        out_arr_to_src(t_lo), out_arr_to_src(t_hi),
        sec_per_beat, out_first_downbeat,
    )
    # Map source times to arrangement axis
    src_lo, src_hi = out_arr_to_src(t_lo), out_arr_to_src(t_hi)
    i0 = max(0, int(src_lo * out_sr))
    i1 = min(len(out_y), int(src_hi * out_sr))
    if i1 > i0:
        seg = out_y[i0:i1].copy()
        peak = float(np.max(np.abs(seg))) or 1.0
        seg = seg / peak * 0.9
        seg_times_src = np.linspace(src_lo, src_hi, len(seg))
        seg_times_arr = out_arrange_start_sec + (seg_times_src - out_first_downbeat)
        ax_out_wave.fill_between(seg_times_arr, -np.abs(seg), np.abs(seg), color="#333333",
                                 alpha=0.6, linewidth=0)
    _draw_mik_cues(
        ax_out_wave,
        [out_arrange_start_sec + (t - out_first_downbeat) for t in ctx.outgoing_mik_cues_sec],
        t_lo, t_hi, prefix="MIK",
    )
    _draw_candidates(
        ax_out_wave, ctx.outgoing_candidates,
        t_lo - 60, t_hi + 60, sec_per_beat, out_first_downbeat,
        y_text=0.05,
    )
    # Chop point on arrangement timeline
    chop_arr_sec = out_arrange_start_sec + spec.outgoing_loop.chop_at_beats / beats_per_sec
    ax_out_wave.axvline(chop_arr_sec, color="#aa00aa", linewidth=2.5, alpha=0.9,
                        label="chop")
    # Loop region as a hatched band on the outgoing
    loop_src_start_arr = out_arrange_start_sec + spec.outgoing_loop.loop_source_start / beats_per_sec
    loop_src_end_arr = out_arrange_start_sec + spec.outgoing_loop.loop_source_end / beats_per_sec
    ax_out_wave.axvspan(loop_src_start_arr, loop_src_end_arr,
                        color="#88dd88", alpha=0.35, hatch="//", linewidth=0,
                        label="loop region")
    ax_out_wave.set_ylim(-1.05, 1.05)
    ax_out_wave.set_yticks([])
    ax_out_wave.grid(False)

    # ===== OUTGOING AUTOMATION =====
    ax_out_auto.set_title("Volume + EQ bass (outgoing)", fontsize=8, loc="left")
    _draw_grid_ticks(ax_out_auto, t_lo, t_hi, ctx.project_bpm)
    _draw_automation(ax_out_auto, spec.outgoing_volume, t_lo, t_hi,
                     beats_per_sec, "volume", "#cc0000")
    _draw_automation(ax_out_auto, spec.outgoing_eq_bass, t_lo, t_hi,
                     beats_per_sec, "eq bass", "#0066aa")
    ax_out_auto.set_ylim(-0.05, 1.05)
    ax_out_auto.set_yticks([0, 0.5, 1.0])
    ax_out_auto.legend(loc="upper right", fontsize=7, framealpha=0.8)
    ax_out_auto.grid(True, alpha=0.2)

    # ===== INCOMING WAVEFORM =====
    ax_in_wave.set_title(f"INCOMING — {ctx.incoming.path.stem} (BPM {ctx.incoming.bpm:.0f})",
                         fontsize=9, loc="left")
    _draw_grid_ticks(ax_in_wave, t_lo, t_hi, ctx.project_bpm)
    _draw_phrase_bands(
        ax_in_wave, ctx.incoming_rb_phrases,
        in_arr_to_src(t_lo), in_arr_to_src(t_hi),
        sec_per_beat, in_first_downbeat,
    )
    src_lo, src_hi = in_arr_to_src(t_lo), in_arr_to_src(t_hi)
    i0 = max(0, int(src_lo * in_sr))
    i1 = min(len(in_y), int(src_hi * in_sr))
    if i1 > i0:
        seg = in_y[i0:i1].copy()
        peak = float(np.max(np.abs(seg))) or 1.0
        seg = seg / peak * 0.9
        seg_times_src = np.linspace(src_lo, src_hi, len(seg))
        seg_times_arr = in_arrange_start_sec + (seg_times_src - in_first_downbeat)
        ax_in_wave.fill_between(seg_times_arr, -np.abs(seg), np.abs(seg), color="#333333",
                                alpha=0.6, linewidth=0)
    _draw_mik_cues(
        ax_in_wave,
        [in_arrange_start_sec + (t - in_first_downbeat) for t in ctx.incoming_mik_cues_sec],
        t_lo, t_hi, prefix="MIK",
    )
    _draw_candidates(
        ax_in_wave, ctx.incoming_candidates,
        t_lo - 60, t_hi + 60, sec_per_beat, in_first_downbeat,
        y_text=0.05,
    )
    ax_in_wave.set_ylim(-1.05, 1.05)
    ax_in_wave.set_yticks([])
    ax_in_wave.grid(False)

    # ===== INCOMING AUTOMATION =====
    ax_in_auto.set_title("Volume + EQ bass (incoming)", fontsize=8, loc="left")
    _draw_grid_ticks(ax_in_auto, t_lo, t_hi, ctx.project_bpm)
    _draw_automation(ax_in_auto, spec.incoming_volume, t_lo, t_hi,
                     beats_per_sec, "volume", "#cc0000")
    _draw_automation(ax_in_auto, spec.incoming_eq_bass, t_lo, t_hi,
                     beats_per_sec, "eq bass", "#0066aa")
    ax_in_auto.set_ylim(-0.05, 1.05)
    ax_in_auto.set_yticks([0, 0.5, 1.0])
    ax_in_auto.legend(loc="upper right", fontsize=7, framealpha=0.8)
    ax_in_auto.grid(True, alpha=0.2)
    ax_in_auto.set_xlabel("Arrangement time (seconds)")

    # Bass-swap vertical across all four panels
    for ax in axes:
        ax.axvline(swap_sec, color="#000000", linewidth=2.0, alpha=0.85,
                   linestyle="--")

    # Legend strip at the bottom
    legend_handles = [
        mpatches.Patch(color=COLOUR_INTRO, alpha=0.35, label="intro"),
        mpatches.Patch(color=COLOUR_DROP, alpha=0.35, label="drop/chorus"),
        mpatches.Patch(color=COLOUR_BREAK, alpha=0.35, label="break"),
        mpatches.Patch(color=COLOUR_OUTRO, alpha=0.35, label="outro"),
        mpatches.Patch(color="#88dd88", alpha=0.35, hatch="//", label="loop region"),
    ]
    fig.legend(handles=legend_handles, loc="lower center", ncol=5,
               fontsize=8, frameon=False, bbox_to_anchor=(0.5, 0.0))

    ax_in_auto.set_xlim(t_lo, t_hi)

    plt.tight_layout(rect=(0.01, 0.04, 0.99, 0.97))

    out_name = (
        f"Transition_{ctx.transition_index:02d}_"
        f"{ctx.outgoing.path.stem[:30].replace(' ', '_')}_to_"
        f"{ctx.incoming.path.stem[:30].replace(' ', '_')}.png"
    )
    out_path = out_dir / out_name
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return out_path
