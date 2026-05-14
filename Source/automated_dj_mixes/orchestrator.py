"""Main pipeline controller — wires analysis, sequencing, warping, automation, and ALS generation."""

from __future__ import annotations

import argparse
from pathlib import Path

from automated_dj_mixes.config import load_config
from automated_dj_mixes.analysis import analyse_folder, TrackAnalysis
from automated_dj_mixes.sequencer import build_harmonic_path
from automated_dj_mixes.warping import calculate_warp_markers
from automated_dj_mixes.automation import calculate_gain_offsets, generate_transition
from automated_dj_mixes.als_generator import TrackPatch, generate_session


def _find_template(project_root: Path) -> Path:
    """Find the ALS template file."""
    templates_dir = project_root / "Templates"
    als_files = list(templates_dir.glob("*.als"))
    if not als_files:
        raise FileNotFoundError(f"No .als template found in {templates_dir}")
    return als_files[0]


def _next_version(output_dir: Path, prefix: str = "V") -> int:
    """Find the next version number based on existing ALS files."""
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
    """Execute the full mix pipeline on tracks in input_dir, write ALS to output_dir."""
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent

    config = load_config(config_path or project_root / "Config" / "settings.json")
    project_bpm = config["default_project_tempo"]

    # 1. Analyse all tracks
    print(f"Analysing tracks in {input_dir}...")
    analyses = analyse_folder(input_dir)
    if not analyses:
        raise ValueError(f"No audio files found in {input_dir}")
    print(f"  Found {len(analyses)} tracks")

    for a in analyses:
        print(f"  {a.path.name}: {a.camelot or '?'} | {a.bpm:.1f} BPM | {a.lufs:.1f} LUFS")
        for w in a.warnings:
            print(f"    WARNING: {w}")

    # 2. Sequence harmonically
    print("Sequencing by Camelot wheel...")
    track_dicts = [
        {"camelot": a.camelot or "1A", "analysis": a}
        for a in analyses
    ]
    sequenced = build_harmonic_path(track_dicts)
    ordered_analyses = [t["analysis"] for t in sequenced]

    for i, a in enumerate(ordered_analyses):
        print(f"  {i+1}. {a.path.name} ({a.camelot})")

    # 3. Calculate gain offsets (match to quietest)
    lufs_values = [a.lufs for a in ordered_analyses]
    gain_offsets = calculate_gain_offsets(
        lufs_values, max_reduction_db=config["max_gain_reduction_db"]
    )
    print("Gain offsets:")
    for a, offset in zip(ordered_analyses, gain_offsets):
        print(f"  {a.path.name}: {offset:+.1f} dB")

    # 4. Calculate warp markers
    print("Calculating warp markers...")
    all_warp_markers = []
    for a in ordered_analyses:
        markers = calculate_warp_markers(
            bpm=a.bpm,
            first_downbeat_sec=a.first_downbeat_sec,
            duration_sec=a.duration_sec,
            project_bpm=project_bpm,
        )
        all_warp_markers.append(markers)

    # 5. Calculate arrangement positions (sequential, with crossfade overlap)
    crossfade_bars = config["crossfade_bars"]
    crossfade_beats = crossfade_bars * 4
    arrangement_positions = []
    cursor = 0.0
    for i, markers in enumerate(all_warp_markers):
        arrangement_positions.append(cursor)
        track_beats = markers[-1].beat_time if markers else 0.0
        if i < len(all_warp_markers) - 1:
            cursor += max(track_beats - crossfade_beats, track_beats * 0.5)
        else:
            cursor += track_beats

    # 6. Build patches
    patches = []
    for i, (analysis, markers, gain_db) in enumerate(
        zip(ordered_analyses, all_warp_markers, gain_offsets)
    ):
        patches.append(TrackPatch(
            analysis=analysis,
            track_index=i,
            warp_markers=markers,
            gain_offset_db=gain_db,
            arrangement_start_beats=arrangement_positions[i],
        ))

    # 7. Generate transition automation
    transition_auto = {}
    for i in range(len(ordered_analyses) - 1):
        outgoing_end_beats = arrangement_positions[i] + (all_warp_markers[i][-1].beat_time if all_warp_markers[i] else 0)
        incoming_start_beats = arrangement_positions[i + 1]
        transition_start = incoming_start_beats

        trans = generate_transition(
            transition_start_beats=transition_start,
            transition_bars=crossfade_bars,
        )

        if i not in transition_auto:
            transition_auto[i] = []
        transition_auto[i].extend([
            ("lp_filter", trans.outgoing_lp_filter),
        ])

        if (i + 1) not in transition_auto:
            transition_auto[i + 1] = []
        transition_auto[i + 1].extend([
            ("hp_filter", trans.incoming_hp_filter),
        ])

    print("Arrangement positions:")
    for a, pos in zip(ordered_analyses, arrangement_positions):
        print(f"  {a.path.name}: beat {pos:.0f}")

    # 8. Generate ALS
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
