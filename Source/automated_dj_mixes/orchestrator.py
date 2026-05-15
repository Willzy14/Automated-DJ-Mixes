"""Main pipeline controller — wires analysis, sequencing, warping, skills engine, and ALS generation."""

from __future__ import annotations

import argparse
from pathlib import Path

from automated_dj_mixes.config import load_config
from automated_dj_mixes.analysis import analyse_folder
from automated_dj_mixes.sequencer import build_harmonic_path
from automated_dj_mixes.warping import calculate_warp_markers, choose_warp_mode
from automated_dj_mixes.automation import calculate_gain_offsets, AutomationPoint
from automated_dj_mixes.als_generator import TrackPatch, generate_session
from automated_dj_mixes.skills import (
    DEFAULT_SKILLS,
    SkillsEngine,
    TransitionContext,
)


def _find_template(project_root: Path) -> Path:
    """Find the most recently modified ALS template (searches subfolders too)."""
    templates_dir = project_root / "Templates"
    als_files = list(templates_dir.rglob("*.als"))
    if not als_files:
        raise FileNotFoundError(f"No .als template found in {templates_dir}")
    return max(als_files, key=lambda p: p.stat().st_mtime)


def _next_version(output_dir: Path, prefix: str = "V") -> int:
    existing = list(output_dir.glob(f"*{prefix}*.als"))
    if not existing:
        return 1
    versions = []
    for f in existing:
        for part in f.stem.split():
            if part.startswith(prefix) and part[len(prefix):].isdigit():
                versions.append(int(part[len(prefix):]))
    return max(versions, default=0) + 1


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    project_root: Path | None = None,
    config_path: Path | None = None,
) -> Path:
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent

    config = load_config(config_path or project_root / "Config" / "settings.json")

    print(f"Analysing tracks in {input_dir}...")
    analyses = analyse_folder(input_dir)
    if not analyses:
        raise ValueError(f"No audio files found in {input_dir}")
    print(f"  Found {len(analyses)} tracks")

    for a in analyses:
        print(f"  {a.path.name}: {a.camelot or '?'} | {a.bpm:.1f} BPM | {a.lufs:.1f} LUFS")
        for w in a.warnings:
            print(f"    WARNING: {w}")

    # Project BPM = MODE (most common BPM) — if 8 tracks at 130 and 4 at 124, use 130.
    # Falls back to median if no clear mode.
    from collections import Counter
    rounded_bpms = [round(a.bpm) for a in analyses]
    bpm_counts = Counter(rounded_bpms)
    most_common = bpm_counts.most_common(1)[0]  # (bpm, count)
    if most_common[1] >= 2:
        project_bpm = most_common[0]
        print(f"Project BPM (mode, {most_common[1]} tracks): {project_bpm}")
    else:
        # No mode — use median
        sorted_bpms = sorted(rounded_bpms)
        project_bpm = sorted_bpms[len(sorted_bpms) // 2]
        print(f"Project BPM (median, no mode): {project_bpm}")

    print("Sequencing by Camelot wheel...")
    track_dicts = [{"camelot": a.camelot or "1A", "analysis": a} for a in analyses]
    sequenced = build_harmonic_path(track_dicts)
    ordered_analyses = [t["analysis"] for t in sequenced]

    for i, a in enumerate(ordered_analyses):
        print(f"  {i+1}. {a.path.name} ({a.camelot})")

    # Gain offsets
    lufs_values = [a.lufs for a in ordered_analyses]
    gain_offsets = calculate_gain_offsets(
        lufs_values, max_reduction_db=config["max_gain_reduction_db"]
    )

    # Warp markers + warp mode per track
    warp_markers_all = []
    warp_modes = []
    for a in ordered_analyses:
        markers = calculate_warp_markers(
            bpm=a.bpm,
            first_downbeat_sec=a.first_downbeat_sec,
            duration_sec=a.duration_sec,
            project_bpm=project_bpm,
        )
        warp_markers_all.append(markers)
        # With tempo automation matching each track's BPM, every track can use Repitch
        # (no time-stretch, no pitch artifacts). choose_warp_mode is now a fallback.
        mode = choose_warp_mode(a.bpm, a.bpm)  # always matches → Repitch
        warp_modes.append(mode)
        mode_name = "Repitch" if mode == 6 else "Complex Pro"
        print(f"  {a.path.name}: {mode_name}")

    # Arrangement positions — base-to-base alignment with phrase-grid snap:
    # Outgoing's bass_end aligns with incoming's bass_start AT a 32-bar phrase
    # boundary. Music is built on phrases (8/16/32 bars) — changes must land
    # on those marks or it sounds off-grid.
    PHRASE_BEATS = 32 * 4  # 128 beats = 32 bars

    def _sec_to_beats(seconds: float | None, bpm: float, fallback_beats: float) -> float:
        if seconds is None:
            return fallback_beats
        return seconds * bpm / 60.0

    arrangement_positions = [0]
    alignment_strategies: list[str] = ["start"]
    bass_swap_beats: list[float | None] = [None]
    for i in range(1, len(ordered_analyses)):
        outgoing = ordered_analyses[i - 1]
        incoming = ordered_analyses[i]
        outgoing_pos = arrangement_positions[i - 1]
        outgoing_total_beats = warp_markers_all[i - 1][-1].beat_time if warp_markers_all[i - 1] else 0

        # Strategy selection (in order of preference):
        # A) bass_to_bass: outgoing.bass_end -> incoming.bass_start (energetic swap)
        # B) tail_into_break: outgoing's audible end -> incoming.first_break_start
        #    Then bass restores on incoming at break_end (the drop)
        # C) end_to_end: last resort (boring beats-into-beats)
        if outgoing.bass_end_sec is not None and incoming.bass_start_sec is not None:
            strategy = "bass_to_bass"
            outgoing_anchor_beats = _sec_to_beats(outgoing.bass_end_sec, outgoing.bpm, outgoing_total_beats)
            incoming_anchor_beats = _sec_to_beats(incoming.bass_start_sec, incoming.bpm, 0.0)
            # Bass swap = incoming.bass_start lands at the alignment beat
            # No secondary swap point needed
            secondary_swap_beats = None
        elif incoming.first_break_start_sec is not None and incoming.first_break_end_sec is not None:
            # Outgoing's audible end aligns with incoming.first_break_start.
            # Bass restores on incoming at first_break_end (the drop after the break).
            strategy = "tail_into_break"
            # Outgoing's anchor for alignment is its clip end (its tail finishes here)
            outgoing_anchor_beats = outgoing_total_beats
            incoming_anchor_beats = _sec_to_beats(incoming.first_break_start_sec, incoming.bpm, 0.0)
            # Secondary swap point for bass restore (on incoming) = break_end in arrangement
            secondary_swap_beats = _sec_to_beats(incoming.first_break_end_sec, incoming.bpm, 0.0)
        else:
            strategy = "end_to_end"
            outgoing_anchor_beats = _sec_to_beats(outgoing.last_kick_sec, outgoing.bpm, outgoing_total_beats)
            incoming_anchor_beats = _sec_to_beats(incoming.first_downbeat_sec, incoming.bpm, 0.0)
            secondary_swap_beats = None

        # Natural alignment point in arrangement time
        natural_swap_beat = outgoing_pos + outgoing_anchor_beats

        # PHRASE SNAP: round to nearest 32-bar boundary (Sam's master rule).
        # Clamp to within outgoing's clip duration so the swap actually fires.
        outgoing_clip_end_beat = outgoing_pos + outgoing_total_beats
        snapped_swap_beat = round(natural_swap_beat / PHRASE_BEATS) * PHRASE_BEATS
        if snapped_swap_beat >= outgoing_clip_end_beat:
            snapped_swap_beat = (int(outgoing_clip_end_beat // PHRASE_BEATS)) * PHRASE_BEATS

        # Position incoming so its anchor lands on the snapped phrase boundary
        natural_incoming_pos = snapped_swap_beat - incoming_anchor_beats

        # Bound overlap to 4-96 bars
        max_overlap_beats = 96 * 4
        min_overlap_beats = 4 * 4
        earliest_incoming = outgoing_pos + outgoing_total_beats - max_overlap_beats
        latest_incoming = outgoing_pos + outgoing_total_beats - min_overlap_beats
        clamped = max(earliest_incoming, min(natural_incoming_pos, latest_incoming))
        snapped_incoming = round(clamped / 4) * 4
        arrangement_positions.append(int(snapped_incoming))
        alignment_strategies.append(strategy)

        # Bass swap point:
        # - For bass_to_bass: incoming's bass_start lands at the alignment beat
        # - For tail_into_break: the BASS swap is at break_end (when incoming's bass returns)
        # - For end_to_end: at the alignment beat (which is last_kick = first_kick)
        if strategy == "tail_into_break" and secondary_swap_beats is not None:
            bass_swap_beats.append(float(snapped_incoming) + secondary_swap_beats)
        else:
            bass_swap_beats.append(float(snapped_incoming) + incoming_anchor_beats)

    print("Arrangement positions (base-to-base, phrase-grid snap):")
    for i, (a, pos, strat) in enumerate(zip(ordered_analyses, arrangement_positions, alignment_strategies)):
        bass_str = (
            f"bass={a.bass_start_sec:.1f}-{a.bass_end_sec:.1f}s"
            if (a.bass_start_sec is not None and a.bass_end_sec is not None)
            else "bass=N/A"
        )
        swap_str = ""
        if i > 0 and bass_swap_beats[i] is not None:
            swap_str = f"  swap@beat{bass_swap_beats[i]:.0f}(bar{bass_swap_beats[i]/4:.0f})"
        print(f"  {a.path.name[:50]}: beat {pos}  [{strat}]  {bass_str}{swap_str}")

    # Tempo automation: each track plays at its native BPM in its "solo" region,
    # ramping smoothly into the next track's BPM during the transition zone.
    tempo_points: list[AutomationPoint] = []
    for i, (a, pos, markers) in enumerate(zip(ordered_analyses, arrangement_positions, warp_markers_all)):
        track_bpm = round(a.bpm)
        if i == 0:
            tempo_points.append(AutomationPoint(time_beats=0, value=track_bpm))
        else:
            # End of previous track's solo = start of this transition
            prev_end = arrangement_positions[i - 1] + (warp_markers_all[i - 1][-1].beat_time if warp_markers_all[i - 1] else 0)
            transition_start = pos
            transition_end = prev_end
            prev_bpm = round(ordered_analyses[i - 1].bpm)
            # Outgoing BPM held until transition starts, then ramp to incoming BPM
            tempo_points.append(AutomationPoint(time_beats=transition_start, value=prev_bpm))
            tempo_points.append(AutomationPoint(time_beats=transition_end, value=track_bpm))
        if i == len(ordered_analyses) - 1:
            track_end = pos + (markers[-1].beat_time if markers else 0)
            tempo_points.append(AutomationPoint(time_beats=track_end, value=track_bpm))

    print("Tempo automation:")
    for p in tempo_points:
        print(f"  beat {p.time_beats:.0f}: {p.value} BPM")

    # Build patches
    patches = []
    for i, (analysis, markers, gain_db, mode) in enumerate(
        zip(ordered_analyses, warp_markers_all, gain_offsets, warp_modes)
    ):
        patches.append(TrackPatch(
            analysis=analysis,
            track_index=i,
            warp_markers=markers,
            gain_offset_db=gain_db,
            arrangement_start_beats=arrangement_positions[i],
            warp_mode=mode,
        ))

    # Skills engine — plan transitions
    engine = SkillsEngine(DEFAULT_SKILLS)
    transition_auto: dict[int, list[tuple[str, list[AutomationPoint]]]] = {}

    print("Planning transitions...")
    for i in range(len(ordered_analyses) - 1):
        outgoing = ordered_analyses[i]
        incoming = ordered_analyses[i + 1]

        outgoing_end = arrangement_positions[i] + (
            warp_markers_all[i][-1].beat_time if warp_markers_all[i] else 0
        )
        incoming_start = arrangement_positions[i + 1]
        overlap = max(0, outgoing_end - incoming_start)

        # The bass swap point for transition i (between track i and i+1) lives
        # at bass_swap_beats[i+1] — we computed it during arrangement positioning.
        swap_beat = bass_swap_beats[i + 1] if (i + 1) < len(bass_swap_beats) else None

        ctx = TransitionContext(
            outgoing=outgoing,
            incoming=incoming,
            outgoing_arrangement_start_beats=arrangement_positions[i],
            outgoing_arrangement_end_beats=outgoing_end,
            incoming_arrangement_start_beats=incoming_start,
            available_overlap_beats=overlap,
            project_bpm=project_bpm,
            bass_swap_beat=swap_beat,
        )

        skill = engine.pick_skill(ctx)
        plan = skill.generate(ctx)
        bars = plan.transition_length_beats / 4
        print(f"  {outgoing.path.name[:40]} -> {incoming.path.name[:40]}: {plan.skill_name} ({bars:.0f} bars)")

        # Default: volume + bass cut only. Filter sweeps (lp/hp) are intentionally
        # OMITTED — Sam's mixes use them rarely and they conflict with the bass cut
        # when both try to manage the lows. Re-enable for opt-in filter-heavy skills.
        transition_auto.setdefault(i, []).extend([
            ("volume", plan.outgoing_volume),
            ("eq_bass", plan.outgoing_eq_bass),
        ])
        transition_auto.setdefault(i + 1, []).extend([
            ("volume", plan.incoming_volume),
            ("eq_bass", plan.incoming_eq_bass),
        ])

    # MERGE multi-envelope automation per (track, param). When a track is the
    # incoming of transition N AND the outgoing of transition N+1, it gets two
    # envelopes on the same parameter. Ableton uses only the first — fix is to
    # merge all points into a single envelope, sorted by time.
    for track_idx in transition_auto:
        merged: dict[str, list[AutomationPoint]] = {}
        for param_key, points in transition_auto[track_idx]:
            merged.setdefault(param_key, []).extend(points)
        for param_key in merged:
            merged[param_key].sort(key=lambda p: p.time_beats)
        transition_auto[track_idx] = [(k, v) for k, v in merged.items()]

    # Generate ALS
    template_path = _find_template(project_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    version = _next_version(output_dir, config["versioning_prefix"])
    prefix = config["versioning_prefix"]
    output_name = f"Mix {prefix}{version}.als"
    output_path = output_dir / output_name

    print(f"Generating {output_path}...")
    result = generate_session(
        template_path=template_path,
        patches=patches,
        output_path=output_path,
        project_bpm=project_bpm,
        transition_automation=transition_auto,
        tempo_automation=tempo_points,
    )
    print(f"Done: {result}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Generate an Ableton Live session from tagged dance tracks")
    parser.add_argument("--input", required=True, help="Folder containing audio tracks")
    parser.add_argument("--output", required=True, help="Folder for generated ALS output")
    parser.add_argument("--config", help="Path to settings.json")
    args = parser.parse_args()

    als_path = run_pipeline(
        Path(args.input),
        Path(args.output),
        config_path=Path(args.config) if args.config else None,
    )
    print(f"Generated: {als_path}")


if __name__ == "__main__":
    main()
