"""Main pipeline controller — wires analysis, sequencing, warping, transition planning, and ALS generation."""

from __future__ import annotations

import argparse
from pathlib import Path

from automated_dj_mixes.config import load_config
from automated_dj_mixes.analysis import analyse_folder, enrich_from_rekordbox
from automated_dj_mixes.sequencer import build_harmonic_path
from automated_dj_mixes.warping import calculate_warp_markers, calculate_warp_markers_from_beat_grid, choose_warp_mode
from automated_dj_mixes.automation import calculate_gain_offsets, AutomationPoint
from automated_dj_mixes.als_generator import TrackPatch, generate_session
from automated_dj_mixes.transition import plan_transition
from automated_dj_mixes.phrase_viz import build_intervals, segments_from_intervals
from automated_dj_mixes.features import extract_track_features
from automated_dj_mixes.cue_candidates import (
    find_cue_candidates,
    first_credible,
    mik_to_candidates,
    amplitude_to_candidates,
    hint_to_candidates,
    load_hints_file,
)
from automated_dj_mixes.mik_reader import enrich_from_mik
from automated_dj_mixes.report import write_track_csv, write_transition_report
from automated_dj_mixes.track_viz import TrackVizContext, render_track
from automated_dj_mixes.transition_viz import VizContext, render_transition
from automated_dj_mixes.validation import validate_mix
from automated_dj_mixes.waveform_preview import PreviewContext, render_preview


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
    visualize: bool = False,
) -> Path:
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent

    config = load_config(config_path or project_root / "Config" / "settings.json")

    print(f"Analysing tracks in {input_dir}...")
    analyses = analyse_folder(input_dir)
    if not analyses:
        raise ValueError(f"No audio files found in {input_dir}")
    print(f"  Found {len(analyses)} tracks")

    # Enrich with Rekordbox phrase analysis (if available)
    rb_count = 0
    rb_matches: dict[str, object] = {}  # track path → RekordboxAnalysis (for beat grid warp)
    try:
        from automated_dj_mixes.rekordbox_reader import read_rekordbox_library, find_rekordbox_match
        print("Reading Rekordbox library...")
        rb_library = read_rekordbox_library()
        for a in analyses:
            rb_match = find_rekordbox_match(a.path.name, rb_library)
            if rb_match and rb_match.phrases:
                enrich_from_rekordbox(a, rb_match)
                rb_matches[str(a.path)] = rb_match
                rb_count += 1
        print(f"  Rekordbox: {rb_count}/{len(analyses)} tracks enriched with phrase data")
    except Exception as e:
        print(f"  Rekordbox unavailable ({e}), using librosa analysis only")

    # Enrich with Mixed In Key 11 cue points (highest-confidence structural
    # signal). Falls back silently if MIK hasn't been run on a track.
    mik_data: dict[str, object] = {}   # track path → MikTrackData
    mik_count = 0
    print("Reading Mixed In Key 11 cue points...")
    for a in analyses:
        try:
            mik = enrich_from_mik(a.path)
            if mik.cues:
                mik_data[str(a.path)] = mik
                mik_count += 1
        except Exception:
            pass
    print(f"  Mixed In Key: {mik_count}/{len(analyses)} tracks have auto-cue points")

    # Visual hints — human (or Claude) broad-strokes reading of each track's
    # waveform. Highest-confidence cue source: when a track has a hint, the
    # hint wins over MIK/RB/amplitude algorithms.
    hints_dir = input_dir.parent / "Hints"
    hints_path = hints_dir / "track_hints.json"
    track_hints_data = load_hints_file(hints_path)
    if track_hints_data:
        print(f"Visual hints: loaded {len(track_hints_data)} hinted tracks from {hints_path.name}")
    else:
        print(f"Visual hints: none found (looked at {hints_path})")

    for a in analyses:
        src = f"[{a.analysis_source}]" if a.analysis_source != "librosa" else ""
        print(f"  {a.path.name}: {a.camelot or '?'} | {a.bpm:.1f} BPM | {a.lufs:.1f} LUFS {src}")
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
    rb_warp_count = 0
    for a in ordered_analyses:
        rb_match = rb_matches.get(str(a.path))
        if rb_match and hasattr(rb_match, "beat_times_ms") and len(rb_match.beat_times_ms) >= 8:
            markers = calculate_warp_markers_from_beat_grid(
                beat_times_ms=rb_match.beat_times_ms,
                bpm=a.bpm,
                duration_sec=a.duration_sec,
                first_downbeat_offset=getattr(rb_match, "first_downbeat_offset", 0),
            )
            rb_warp_count += 1
        else:
            markers = calculate_warp_markers(
                bpm=a.bpm,
                first_downbeat_sec=a.first_downbeat_sec,
                duration_sec=a.duration_sec,
                project_bpm=project_bpm,
            )
        warp_markers_all.append(markers)
        mode = choose_warp_mode(a.bpm, project_bpm)
        warp_modes.append(mode)
        mode_name = "Repitch" if mode == 6 else "Complex Pro"
        warp_src = f"RB grid ({len(markers)} markers)" if rb_match and hasattr(rb_match, "beat_times_ms") and len(rb_match.beat_times_ms) >= 8 else "2-marker linear"
        print(f"  {a.path.name}: {mode_name}, {warp_src}")
    if rb_warp_count:
        print(f"  Beat grid warp: {rb_warp_count}/{len(ordered_analyses)} tracks using Rekordbox grid")

    # === VISUALIZATION MODE ===
    # Each track starts at arrangement beat 0 (stacked on separate Ableton
    # tracks, no sequencing). Three-signal classification per 8-bar interval:
    # Rekordbox phrase majority + librosa RMS + librosa bass-band RMS.
    if visualize:
        print("VISUALIZATION MODE — multi-signal cue candidates (RB + librosa + PWV5)")
        INTERVAL_BARS = 8
        reports_dir = output_dir / "Reports"
        patches = []
        all_track_features = {}
        all_intervals = {}
        all_candidates = {}
        for i, (analysis, markers, mode) in enumerate(
            zip(ordered_analyses, warp_markers_all, warp_modes)
        ):
            total_beats = markers[-1].beat_time if markers else 0
            rb_match = rb_matches.get(str(analysis.path))
            if not rb_match:
                print(f"  WARNING: no Rekordbox data for {analysis.path.name}")
                continue
            offset = getattr(rb_match, "first_downbeat_offset", 0)
            ext_path = Path(rb_match.ext_path) if rb_match.ext_path else None

            # Per-beat features with cache (Step 2)
            try:
                features = extract_track_features(
                    audio_path=analysis.path,
                    bpm=rb_match.bpm or analysis.bpm,
                    beat_times_ms=rb_match.beat_times_ms,
                    first_downbeat_offset=offset,
                    ext_path=ext_path,
                )
            except Exception as e:
                print(f"  WARNING: feature extraction failed for {analysis.path.name}: {e}")
                continue

            # Factual interval records (Step 3)
            intervals = build_intervals(rb_match, features, interval_bars=INTERVAL_BARS)
            segments = segments_from_intervals(intervals)

            # Ranked cue candidates (Step 4) — pass MIK cues if available
            mik = mik_data.get(str(analysis.path))
            mik_cues_sec = [c.time_sec for c in mik.cues] if mik else None
            candidates = find_cue_candidates(intervals, features, mik_cues_sec)

            # Per-track CSV report (Step 5)
            write_track_csv(analysis.path.stem, intervals, candidates, reports_dir)

            # Cache for later (transition planning, viz markers, etc.)
            all_track_features[analysis.path.name] = features
            all_intervals[analysis.path.name] = intervals
            all_candidates[analysis.path.name] = candidates

            label_counts: dict[str, int] = {}
            for s in segments:
                label_counts[s.label] = label_counts.get(s.label, 0) + 1
            counts_str = " ".join(f"{k}:{v}" for k, v in label_counts.items())
            cand_summary: dict[str, int] = {}
            for c in candidates:
                cand_summary[c.cue_type] = cand_summary.get(c.cue_type, 0) + 1
            cand_str = " ".join(f"{t}:{n}" for t, n in cand_summary.items())
            print(
                f"  {analysis.path.name[:60]} (offset={offset}, "
                f"wf={features.waveform_source}): sections [{counts_str}] cues [{cand_str}]"
            )
            # Top candidate per type for quick scan
            for cue_type in ("bass_entry", "break_start", "break_end", "chop_point", "outro_start"):
                top = next((c for c in candidates if c.cue_type == cue_type), None)
                if top:
                    print(
                        f"     {cue_type:13s} top: beat {top.beat:>5.0f} @ {top.sec:>6.1f}s  "
                        f"conf {top.confidence:.2f}  ({', '.join(top.sources)})"
                    )

            patches.append(TrackPatch(
                analysis=analysis,
                track_index=i,
                warp_markers=markers,
                gain_offset_db=0.0,
                arrangement_start_beats=0,
                warp_mode=mode,
                phrase_segments=segments,
            ))

        # Single tempo point at the project BPM — no per-track tempo automation
        tempo_points = [AutomationPoint(time_beats=0, value=project_bpm)]

        template_path = _find_template(project_root)
        output_dir.mkdir(parents=True, exist_ok=True)
        existing_viz = list(output_dir.glob("Phrase Viz V*.als"))
        version = len(existing_viz) + 1
        output_name = f"Phrase Viz V{version}.als"
        output_path = output_dir / output_name
        print(f"Generating {output_path}...")
        result = generate_session(
            template_path=template_path,
            patches=patches,
            output_path=output_path,
            project_bpm=project_bpm,
            transition_automation=None,
            tempo_automation=tempo_points,
        )
        print(f"Done: {result}")
        return result

    # Extract per-track features + ranked cue candidates (Step 7 wiring).
    # plan_transition() will prefer candidates over RB-phrase fallbacks.
    # Intervals are kept so the transition planner can pick a stripped-down
    # loop region from the outgoing track.
    print("Extracting per-track features + cue candidates...")
    track_candidates: dict[str, list] = {}
    track_intervals: dict[str, list] = {}
    mik_only_count = 0
    for analysis, markers in zip(ordered_analyses, warp_markers_all):
        rb_match = rb_matches.get(str(analysis.path))
        mik = mik_data.get(str(analysis.path))
        total_beats = markers[-1].beat_time if markers else 0.0

        if rb_match:
            ext_path = Path(rb_match.ext_path) if rb_match.ext_path else None
            try:
                features = extract_track_features(
                    audio_path=analysis.path,
                    bpm=rb_match.bpm or analysis.bpm,
                    beat_times_ms=rb_match.beat_times_ms,
                    first_downbeat_offset=getattr(rb_match, "first_downbeat_offset", 0),
                    ext_path=ext_path,
                )
                intervals = build_intervals(rb_match, features)
                mik_cues_sec = [c.time_sec for c in mik.cues] if mik else None
                candidates = find_cue_candidates(intervals, features, mik_cues_sec)
                track_candidates[analysis.path.name] = candidates
                track_intervals[analysis.path.name] = intervals
                continue
            except Exception as e:
                print(f"  WARNING: feature/candidate extraction failed for {analysis.path.name}: {e}")

        # No Rekordbox — fall back to MIK-only candidates if available.
        if mik and mik.cues:
            candidates = mik_to_candidates(
                cue_times_sec=[c.time_sec for c in mik.cues],
                first_downbeat_sec=analysis.first_downbeat_sec or 0.0,
                bpm=analysis.bpm or 128.0,
                total_beats=total_beats,
                mik_energy_segments=mik.energy_segments,
            )
            track_candidates[analysis.path.name] = candidates
            track_intervals[analysis.path.name] = []
            if candidates:
                mik_only_count += 1
        else:
            track_candidates[analysis.path.name] = []
            track_intervals[analysis.path.name] = []

    if mik_only_count:
        print(f"  MIK-only candidates: {mik_only_count} tracks (no Rekordbox phrase data)")

    # Amplitude-envelope structural detection — runs for EVERY track, not
    # just MIK-only. This is the "look at the picture" signal that catches
    # what MIK/RB miss (e.g. drops at silent-intro boundaries that MIK
    # doesn't cue). Candidates merge with existing ones; first_drop_candidate
    # picks the earliest credible bass_entry across all sources.
    amp_added = 0
    for analysis in ordered_analyses:
        mik = mik_data.get(str(analysis.path))
        mik_cues_sec = [c.time_sec for c in mik.cues] if mik else None
        try:
            amp_cands = amplitude_to_candidates(
                audio_path=analysis.path,
                bpm=analysis.bpm or 128.0,
                first_downbeat_sec=analysis.first_downbeat_sec or 0.0,
                duration_sec=analysis.duration_sec or 0.0,
                mik_cues_sec=mik_cues_sec,
            )
        except Exception as e:
            print(f"  WARNING: amplitude analysis failed for {analysis.path.name}: {e}")
            amp_cands = []
        if amp_cands:
            track_candidates.setdefault(analysis.path.name, []).extend(amp_cands)
            amp_added += 1
    if amp_added:
        print(f"  Amplitude candidates: added to {amp_added}/{len(ordered_analyses)} tracks")

    # Visual hints — added LAST so they override (highest confidence).
    # A hinted bass_entry will win first_drop_candidate over any algorithmic
    # pick because hint_confidence (0.95) > algorithmic confidences.
    hint_added = 0
    for analysis in ordered_analyses:
        hint = track_hints_data.get(analysis.path.name)
        if not hint:
            continue
        mik = mik_data.get(str(analysis.path))
        mik_cues_sec = [c.time_sec for c in mik.cues] if mik else None
        hint_cands = hint_to_candidates(
            track_hint=hint,
            bpm=analysis.bpm or 128.0,
            first_downbeat_sec=analysis.first_downbeat_sec or 0.0,
            mik_cues_sec=mik_cues_sec,
        )
        if hint_cands:
            track_candidates.setdefault(analysis.path.name, []).extend(hint_cands)
            hint_added += 1
    if hint_added:
        print(f"  Visual hints applied: {hint_added}/{len(ordered_analyses)} tracks")
    unhinted = [a.path.name for a in ordered_analyses if a.path.name not in track_hints_data]
    if unhinted:
        print(f"  {len(unhinted)} tracks have NO visual hints — algorithmic picks only. "
              f"Review Output/Visualisations/Previews to add hints.")

    # Plan transitions using ranked candidates (with RB fallbacks).
    arrangement_positions = [0]
    transition_specs = []  # one per pair (len = tracks - 1)
    loop_specs: list = [None]  # per-track LoopSpec | None (first track never loops)

    print("Planning transitions (candidate-driven)...")
    for i in range(1, len(ordered_analyses)):
        outgoing = ordered_analyses[i - 1]
        incoming = ordered_analyses[i]
        outgoing_total = warp_markers_all[i - 1][-1].beat_time if warp_markers_all[i - 1] else 0
        incoming_total = warp_markers_all[i][-1].beat_time if warp_markers_all[i] else 0

        outgoing_mik = mik_data.get(str(outgoing.path))
        spec = plan_transition(
            outgoing=outgoing,
            incoming=incoming,
            outgoing_arrangement_start=arrangement_positions[i - 1],
            outgoing_total_beats=outgoing_total,
            incoming_total_beats=incoming_total,
            project_bpm=project_bpm,
            outgoing_rb=rb_matches.get(str(outgoing.path)),
            incoming_rb=rb_matches.get(str(incoming.path)),
            outgoing_candidates=track_candidates.get(outgoing.path.name, []),
            incoming_candidates=track_candidates.get(incoming.path.name, []),
            outgoing_intervals=track_intervals.get(outgoing.path.name, []),
            outgoing_mik_energy_segments=outgoing_mik.energy_segments if outgoing_mik else None,
        )

        arrangement_positions.append(int(spec.incoming_arrangement_start))
        transition_specs.append(spec)

        # Outgoing loop from this transition overwrites what we stored for track i-1
        # (only if we haven't already set one from a previous transition)
        if spec.outgoing_loop and loop_specs[i - 1] is None:
            loop_specs[i - 1] = spec.outgoing_loop
        loop_specs.append(spec.incoming_loop)

        overlap = spec.transition_end - spec.transition_start
        log_str = " | ".join(spec.decision_log)
        print(f"  {outgoing.path.name[:35]} -> {incoming.path.name[:35]}: {overlap/4:.0f}bars swap@{spec.bass_swap:.0f} [{log_str}]")

    # Tempo automation: each track plays at its native BPM in its "solo" region,
    # ramping smoothly into the next track's BPM during the transition zone.
    # All breakpoints snap to whole beats (Sam's grid rule).
    tempo_points: list[AutomationPoint] = []
    for i, (a, pos, markers) in enumerate(zip(ordered_analyses, arrangement_positions, warp_markers_all)):
        track_bpm = round(a.bpm)
        if i == 0:
            tempo_points.append(AutomationPoint(time_beats=0, value=track_bpm))
        else:
            prev_end = round(arrangement_positions[i - 1] + (warp_markers_all[i - 1][-1].beat_time if warp_markers_all[i - 1] else 0))
            transition_start = round(pos)
            transition_end = prev_end
            prev_bpm = round(ordered_analyses[i - 1].bpm)
            tempo_points.append(AutomationPoint(time_beats=transition_start, value=prev_bpm))
            tempo_points.append(AutomationPoint(time_beats=transition_end, value=track_bpm))
        if i == len(ordered_analyses) - 1:
            track_end = round(pos + (markers[-1].beat_time if markers else 0))
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
            loop_spec=loop_specs[i],
        ))

    # Collect automation from transition specs
    transition_auto: dict[int, list[tuple[str, list[AutomationPoint]]]] = {}
    for i, spec in enumerate(transition_specs):
        transition_auto.setdefault(i, []).extend([
            ("volume", spec.outgoing_volume),
            ("eq_bass", spec.outgoing_eq_bass),
        ])
        transition_auto.setdefault(i + 1, []).extend([
            ("volume", spec.incoming_volume),
            ("eq_bass", spec.incoming_eq_bass),
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

    # CLAMP automation to the active clip region. The clip region extends from
    # arrangement_start to either the natural track end, OR (if there's a loop
    # spec) to the chop point plus N duplicate clips.
    #
    # Anchors use the FIRST/LAST automation value, not unity. For an outgoing
    # fade ending at 0.0, the right anchor stays at 0.0 (silent), not 1.0 —
    # otherwise the track jumps back to full volume after the fade.
    #
    # All breakpoints snap to whole Ableton beats (Sam's hard rule, 2026-05).
    def _snap_beat(b: float) -> float:
        return float(round(b))

    for track_idx in transition_auto:
        clip_start = _snap_beat(arrangement_positions[track_idx])
        total_beats = warp_markers_all[track_idx][-1].beat_time if warp_markers_all[track_idx] else 0
        ls = loop_specs[track_idx]
        if ls:
            loop_len = ls.loop_source_end - ls.loop_source_start
            clip_end_beat = _snap_beat(clip_start + ls.chop_at_beats + ls.num_extra_copies * loop_len)
        else:
            clip_end_beat = _snap_beat(clip_start + total_beats)

        clamped: list[tuple[str, list[AutomationPoint]]] = []
        for param_key, points in transition_auto[track_idx]:
            if not points:
                continue
            snapped = [
                AutomationPoint(time_beats=_snap_beat(p.time_beats), value=p.value)
                for p in points
            ]
            filtered = [p for p in snapped if clip_start <= p.time_beats <= clip_end_beat]
            if not filtered:
                continue
            first_time = filtered[0].time_beats
            last_time = filtered[-1].time_beats
            left_value = filtered[0].value
            right_value = filtered[-1].value
            anchored: list[AutomationPoint] = []
            if first_time > clip_start:
                anchored.append(AutomationPoint(time_beats=clip_start, value=left_value))
            anchored.extend(filtered)
            if last_time < clip_end_beat:
                anchored.append(AutomationPoint(time_beats=clip_end_beat, value=right_value))
            # Dedupe consecutive points at the same beat (keep the last value).
            deduped: list[AutomationPoint] = []
            for p in anchored:
                if deduped and deduped[-1].time_beats == p.time_beats:
                    deduped[-1] = p
                else:
                    deduped.append(p)
            clamped.append((param_key, deduped))
        transition_auto[track_idx] = clamped

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

    # Objective validation + transition rationale report (Step 8)
    track_total_beats = [
        warp_markers_all[i][-1].beat_time if warp_markers_all[i] else 0.0
        for i in range(len(ordered_analyses))
    ]
    report = validate_mix(
        mix_version=version,
        transition_specs=transition_specs,
        track_total_beats=track_total_beats,
        arrangement_positions=arrangement_positions,
    )
    print()
    print(report.summary())

    # Build transition rationale pairs for the markdown report
    reports_dir = output_dir / "Reports"
    pairs = []
    for i, spec in enumerate(transition_specs):
        out_name = ordered_analyses[i].path.stem
        in_name = ordered_analyses[i + 1].path.stem
        out_cands = track_candidates.get(ordered_analyses[i].path.name, []) if "track_candidates" in dir() else []
        in_cands = track_candidates.get(ordered_analyses[i + 1].path.name, []) if "track_candidates" in dir() else []
        bass_swap_cand = first_credible(in_cands, "bass_entry", 0.5)
        chop_cand = first_credible(out_cands, "chop_point", 0.5) or first_credible(out_cands, "outro_start", 0.5)
        overlap_bars = (spec.transition_end - spec.transition_start) / 4
        loops = spec.outgoing_loop.num_extra_copies if spec.outgoing_loop else 0
        pairs.append({
            "outgoing_name": out_name,
            "incoming_name": in_name,
            "bass_swap_beat": spec.bass_swap,
            "bass_swap_candidate": bass_swap_cand,
            "chop_beat": spec.outgoing_loop.chop_at_beats if spec.outgoing_loop else 0.0,
            "chop_candidate": chop_cand,
            "transition_start_beat": spec.transition_start,
            "transition_end_beat": spec.transition_end,
            "overlap_bars": f"{overlap_bars:.1f}",
            "looped_iterations": loops,
        })
    md_path = write_transition_report(version, pairs, reports_dir)
    print(f"Transition report: {md_path}")

    # Per-transition PNG visualisations. Lets Claude read what Sam sees in
    # Ableton (waveforms + automation + colour-coded phrases) and self-correct
    # before mix-listening time. Best-effort — render failures don't block.
    viz_dir = output_dir / "Visualisations" / f"Mix V{version}"
    rendered = 0
    failed = 0
    for i, spec in enumerate(transition_specs):
        outgoing = ordered_analyses[i]
        incoming = ordered_analyses[i + 1]
        out_mik = mik_data.get(str(outgoing.path))
        in_mik = mik_data.get(str(incoming.path))
        out_rb = rb_matches.get(str(outgoing.path))
        in_rb = rb_matches.get(str(incoming.path))
        try:
            ctx = VizContext(
                transition_index=i + 1,
                outgoing=outgoing,
                incoming=incoming,
                spec=spec,
                outgoing_total_beats=track_total_beats[i],
                incoming_total_beats=track_total_beats[i + 1],
                outgoing_arrangement_start=arrangement_positions[i],
                project_bpm=project_bpm,
                outgoing_mik_cues_sec=[c.time_sec for c in out_mik.cues] if out_mik else [],
                incoming_mik_cues_sec=[c.time_sec for c in in_mik.cues] if in_mik else [],
                outgoing_candidates=track_candidates.get(outgoing.path.name, []),
                incoming_candidates=track_candidates.get(incoming.path.name, []),
                outgoing_rb_phrases=getattr(out_rb, "phrases", None) if out_rb else None,
                incoming_rb_phrases=getattr(in_rb, "phrases", None) if in_rb else None,
            )
            render_transition(ctx, viz_dir)
            rendered += 1
        except Exception as e:
            failed += 1
            print(f"  WARNING: viz render failed for transition {i + 1}: {e}")
    if rendered:
        print(f"Transition visualisations: {rendered}/{len(transition_specs)} → {viz_dir}")
    if failed:
        print(f"  ({failed} render failures — see warnings above)")

    # Per-track PNGs — the FULL timeline of each source track with every
    # MIK cue, RB phrase, MIK energy segment, picked candidate, and loop
    # region in one image. Lets Claude verify the cue picks per-track,
    # which catches "wrong drop selected" bugs that per-transition viz
    # can't show (it only shows ±32 bars around the swap).
    track_rendered = 0
    track_failed = 0
    for i, analysis in enumerate(ordered_analyses):
        mik = mik_data.get(str(analysis.path))
        rb_match = rb_matches.get(str(analysis.path))
        # Extract this track's automation envelopes from transition_auto.
        vol_pts = None
        eq_pts = None
        for param_key, points in transition_auto.get(i, []):
            if param_key == "volume":
                vol_pts = points
            elif param_key == "eq_bass":
                eq_pts = points
        try:
            ctx = TrackVizContext(
                track_index=i + 1,
                analysis=analysis,
                candidates=track_candidates.get(analysis.path.name, []),
                mik_cues_sec=[c.time_sec for c in mik.cues] if mik else [],
                mik_energy_segments=mik.energy_segments if mik else [],
                rb_phrases=getattr(rb_match, "phrases", None) if rb_match else None,
                project_bpm=project_bpm,
                arrangement_start_beats=arrangement_positions[i],
                loop_spec=loop_specs[i],
                volume_points=vol_pts,
                eq_bass_points=eq_pts,
            )
            render_track(ctx, viz_dir)
            track_rendered += 1
        except Exception as e:
            track_failed += 1
            print(f"  WARNING: track viz render failed for track {i + 1}: {e}")
    if track_rendered:
        print(f"Track visualisations: {track_rendered}/{len(ordered_analyses)} → {viz_dir}")
    if track_failed:
        print(f"  ({track_failed} track-render failures)")

    # Blank-canvas previews — one per track, NO candidates, NO automation.
    # These are the images to review BEFORE making hint decisions. Saved
    # to a shared Previews folder so hints carry across mixes.
    preview_dir = output_dir / "Visualisations" / "Previews"
    preview_rendered = 0
    for i, analysis in enumerate(ordered_analyses):
        mik = mik_data.get(str(analysis.path))
        rb_match = rb_matches.get(str(analysis.path))
        try:
            ctx = PreviewContext(
                track_index=i + 1,
                analysis=analysis,
                mik_cues_sec=[c.time_sec for c in mik.cues] if mik else [],
                mik_energy_segments=mik.energy_segments if mik else [],
                rb_phrases=getattr(rb_match, "phrases", None) if rb_match else None,
                project_bpm=project_bpm,
            )
            render_preview(ctx, preview_dir)
            preview_rendered += 1
        except Exception as e:
            print(f"  WARNING: preview render failed for track {i + 1}: {e}")
    if preview_rendered:
        print(f"Track previews (for hint authoring): {preview_rendered}/{len(ordered_analyses)} → {preview_dir}")

    # ============================================================
    # VISUAL REVIEW GATE — the mix isn't validated by numbers alone.
    # ============================================================
    # The pipeline writes a REVIEW_VNN.md template that Claude (or Sam)
    # must fill in by reading every per-track and per-transition image.
    # Without this, "ALL PASS" is numerical only — the actual mix may
    # still have alignment bugs, loop content issues, or misplaced cues
    # that only become obvious when you look at the picture.
    review_path = output_dir / "Visualisations" / f"REVIEW_V{version}.md"
    track_images = sorted((output_dir / "Visualisations" / f"Mix V{version}").glob("Track_*.png"))
    trans_images = sorted((output_dir / "Visualisations" / f"Mix V{version}").glob("Transition_*.png"))
    if not review_path.exists():
        lines = [
            f"# Visual review — Mix V{version}",
            "",
            "**This file MUST be filled in before the mix is considered validated.**",
            "Numerical validation passed, but that only checks data shapes — not",
            "whether the picks land on the right amplitude points, whether the",
            "loop region is in clean stripped percussion, or whether the bass swap",
            "is visually aligned with the incoming drop.",
            "",
            "For each image below, look at it and write a short verdict:",
            "  - ✓  picks landed correctly, loop content is clean",
            "  - ✗  describe what's wrong (off-by-N beats, loop contains silent gap,",
            "         bass entry at wrong drop, etc.)",
            "",
            "## Per-track images",
            "",
        ]
        for p in track_images:
            lines.append(f"- [ ] `{p.name}` — VERDICT: ")
        lines.extend(["", "## Per-transition images", ""])
        for p in trans_images:
            lines.append(f"- [ ] `{p.name}` — VERDICT: ")
        lines.extend([
            "",
            "## Overall verdict",
            "",
            "_Summarise: is this mix ready, or which fixes are needed?_",
            "",
        ])
        review_path.write_text("\n".join(lines), encoding="utf-8")

    print()
    print("=" * 66)
    print(f"VISUAL REVIEW REQUIRED — Mix V{version} is NOT complete.")
    print("=" * 66)
    print(f"Per-track images ({len(track_images)}):")
    for p in track_images[:3]:
        print(f"  {p}")
    if len(track_images) > 3:
        print(f"  ... and {len(track_images) - 3} more")
    print(f"Per-transition images ({len(trans_images)}):")
    for p in trans_images[:3]:
        print(f"  {p}")
    if len(trans_images) > 3:
        print(f"  ... and {len(trans_images) - 3} more")
    print()
    print("Required actions BEFORE reporting Mix V{} complete:".format(version))
    print(f"  1. Read every image listed above.")
    print(f"  2. Write per-image verdicts to: {review_path}")
    print(f"  3. State 'Visual review complete: <summary>' in your response.")
    print()
    print("The ALL PASS above is numerical only. The mix is NOT validated")
    print("until the visual review is done. (See Documentation/AI_CONTEXT.md")
    print("for the standing rule.)")
    print("=" * 66)

    return result


def main():
    parser = argparse.ArgumentParser(description="Generate an Ableton Live session from tagged dance tracks")
    parser.add_argument("--input", required=True, help="Folder containing audio tracks")
    parser.add_argument("--output", required=True, help="Folder for generated ALS output")
    parser.add_argument("--config", help="Path to settings.json")
    parser.add_argument("--visualize", action="store_true",
                        help="Phrase visualization mode — chop tracks by Rekordbox phrases, color-code")
    args = parser.parse_args()

    als_path = run_pipeline(
        Path(args.input),
        Path(args.output),
        config_path=Path(args.config) if args.config else None,
        visualize=args.visualize,
    )
    print(f"Generated: {als_path}")


if __name__ == "__main__":
    main()
