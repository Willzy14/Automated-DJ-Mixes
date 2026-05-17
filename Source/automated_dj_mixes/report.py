"""Debug reports — CSV per track + Markdown per mix.

Two outputs:
  Analysis - {trackname}.csv    — per-interval facts + candidates for verification
  Transition - Mix V{N}.md      — per-transition decisions for "why this transition"

Both include ANALYSIS_MODEL_VERSION so older reports stay identifiable when
thresholds change.
"""

from __future__ import annotations

import csv
from pathlib import Path

from automated_dj_mixes.cue_candidates import CueCandidate
from automated_dj_mixes.features import ANALYSIS_MODEL_VERSION
from automated_dj_mixes.phrase_viz import Interval


# ---------------------------------------------------------------------------
# Per-track CSV
# ---------------------------------------------------------------------------

def write_track_csv(
    track_name: str,
    intervals: list[Interval],
    candidates: list[CueCandidate],
    out_dir: Path,
) -> Path:
    """Write Analysis - {trackname}.csv with intervals + candidate annotations."""
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in track_name[:60])
    out_path = out_dir / f"Analysis - {safe_name}.csv"

    # Build candidate lookup: interval_index -> list of (type, confidence)
    cand_by_iv: dict[int, list[tuple[str, float]]] = {}
    for c in candidates:
        cand_by_iv.setdefault(c.interval_index, []).append((c.cue_type, c.confidence))

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "analysis_model_version",
            "interval", "pssi_start", "pssi_end",
            "source_start_beats", "source_end_beats",
            "rb_label",
            "rms_norm", "bass_norm", "wf_height",
            "rms_band", "bass_band", "wf_height_band",
            "candidates",
        ])
        for iv in intervals:
            cand_str = "; ".join(
                f"{t}({c:.2f})" for t, c in sorted(cand_by_iv.get(iv.index, []), key=lambda x: -x[1])
            )
            w.writerow([
                ANALYSIS_MODEL_VERSION,
                iv.index, iv.pssi_start_beat, iv.pssi_end_beat,
                f"{iv.source_start_beats:.1f}", f"{iv.source_end_beats:.1f}",
                iv.rb_label or "",
                f"{iv.energy.rms_norm:.3f}",
                f"{iv.energy.bass_librosa:.3f}",
                f"{iv.energy.waveform_height:.3f}" if iv.energy.waveform_height is not None else "",
                iv.energy.rms_band,
                iv.energy.bass_band,
                iv.energy.wf_height_band or "",
                cand_str,
            ])
    return out_path


# ---------------------------------------------------------------------------
# Per-mix Markdown
# ---------------------------------------------------------------------------

def write_transition_report(
    mix_version: int,
    track_pairs: list[dict],
    out_dir: Path,
) -> Path:
    """Write Transition - Mix V{N}.md with per-transition decision rationale.

    track_pairs is a list of dicts (one per transition) with keys:
      outgoing_name, incoming_name, bass_swap_beat, bass_swap_candidate,
      chop_beat, chop_candidate, transition_start_beat, transition_end_beat,
      overlap_bars, looped_iterations
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"Transition - Mix V{mix_version}.md"

    lines: list[str] = []
    lines.append(f"# Transition Report — Mix V{mix_version}")
    lines.append("")
    lines.append(f"_analysis_model_version: `{ANALYSIS_MODEL_VERSION}`_")
    lines.append("")

    for idx, pair in enumerate(track_pairs, start=1):
        lines.append(f"## Transition {idx}: {pair['outgoing_name']} → {pair['incoming_name']}")
        lines.append("")
        # Bass swap detail
        swap_cand: CueCandidate | None = pair.get("bass_swap_candidate")
        if swap_cand:
            lines.append(f"**Bass swap** @ beat {pair['bass_swap_beat']:.0f} ({swap_cand.sec:.1f}s)")
            lines.append(f"- Confidence: {swap_cand.confidence:.2f}")
            lines.append(f"- Sources: {', '.join(swap_cand.sources)}")
            for r in swap_cand.reasons:
                lines.append(f"  - {r}")
            lines.append("")
        # Chop detail
        chop_cand: CueCandidate | None = pair.get("chop_candidate")
        if chop_cand:
            lines.append(f"**Chop point** @ outgoing beat {pair['chop_beat']:.0f}")
            lines.append(f"- Confidence: {chop_cand.confidence:.2f}")
            lines.append(f"- Sources: {', '.join(chop_cand.sources)}")
            for r in chop_cand.reasons:
                lines.append(f"  - {r}")
            lines.append("")
        # Overlap stats
        if "transition_start_beat" in pair and "transition_end_beat" in pair:
            lines.append(
                f"**Overlap**: arrangement beats {pair['transition_start_beat']:.0f} → "
                f"{pair['transition_end_beat']:.0f}  ({pair.get('overlap_bars', '?')} bars)"
            )
        if "looped_iterations" in pair and pair["looped_iterations"]:
            lines.append(f"**Outgoing tail loop**: {pair['looped_iterations']} duplicate clips")
        lines.append("")
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
