"""Main pipeline controller — wires analysis, sequencing, warping, transition planning, and ALS generation."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from automated_dj_mixes.config import load_config
from automated_dj_mixes.analysis import analyse_folder, enrich_from_rekordbox
from automated_dj_mixes.sequencer import build_harmonic_path, key_to_camelot, apply_energy_arc
from automated_dj_mixes.warping import calculate_warp_markers, calculate_warp_markers_from_beat_grid, choose_warp_mode
from automated_dj_mixes.automation import calculate_gain_offsets, AutomationPoint
from automated_dj_mixes.als_generator import TrackPatch, generate_session
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
from automated_dj_mixes.waveform_preview import PreviewContext, render_preview


def _count_audio_tracks(als_path: Path) -> int:
    """Count <AudioTrack elements in a gzipped ALS file."""
    import gzip
    try:
        data = gzip.open(str(als_path)).read().decode("utf-8", errors="replace")
        return data.count("<AudioTrack ")
    except Exception:
        return 0


def _find_template(project_root: Path, min_tracks: int = 0) -> Path:
    """Find the best ALS template for a mix of `min_tracks` tracks.

    Picks the SMALLEST template whose audio-track count is >= min_tracks, so
    a 10-track mix loads the 12-track template instead of the 35-track one
    (which would leave 20+ empty tracks). If none is large enough, picks the
    largest available and warns. When min_tracks is 0 (caller doesn't know
    the count) the old behaviour is kept: pick the largest. Ties broken by
    most-recently-modified.
    """
    templates_dir = project_root / "Templates"
    als_files = list(templates_dir.rglob("*.als"))
    if not als_files:
        raise FileNotFoundError(f"No .als template found in {templates_dir}")
    scored = [(p, _count_audio_tracks(p)) for p in als_files]

    if not min_tracks:
        scored.sort(key=lambda x: (x[1], x[0].stat().st_mtime), reverse=True)
        return scored[0][0]

    fits = [(p, c) for (p, c) in scored if c >= min_tracks]
    if fits:
        # smallest template that fits; newest as tie-break
        fits.sort(key=lambda x: (x[1], -x[0].stat().st_mtime))
        return fits[0][0]

    scored.sort(key=lambda x: (x[1], x[0].stat().st_mtime), reverse=True)
    chosen, count = scored[0]
    print(f"  WARNING: largest template {chosen.name} has {count} audio "
          f"tracks but mix needs {min_tracks}")
    return chosen


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


HINT_REQUIRED_FIELDS = (
    "first_drop_sec",       # incoming role: where the bass first enters
    "first_break_sec",      # informational: first energy drop after first_drop
    "outro_start_sec",      # outgoing role: where the outro begins (loop region anchor)
    "last_bass_drop_sec",   # outgoing role: the natural bass-drop / fill near the end
                            #   where the music itself swaps bass. This is the bass_swap
                            #   anchor for the NEXT transition. Sam's rule (2026-05-19):
                            #   align incoming.first_drop_sec to outgoing.last_bass_drop_sec
                            #   so the EQ swap reinforces the natural musical swap.
)


def _validate_hints(audio_dir: Path, hints: dict) -> list[str]:
    """Return error strings for any .wav in audio_dir missing a complete hint
    entry. Exact filename match (including extension). All three required
    fields must be present and positive numeric.

    Production gate (Sam, 2026-05-19). Use `--no-hints-required` to bypass
    during debugging.
    """
    errors: list[str] = []
    for audio_path in sorted(audio_dir.glob("*.wav")):
        filename = audio_path.name
        entry = hints.get(filename)
        if entry is None:
            errors.append(f"{filename}: no hint entry")
            continue
        for field in HINT_REQUIRED_FIELDS:
            value = entry.get(field)
            if value is None:
                errors.append(f"{filename}: missing field '{field}'")
            elif not isinstance(value, (int, float)) or value <= 0:
                errors.append(f"{filename}: field '{field}' must be positive number, got {value!r}")
    return errors


def _render_previews(ordered_analyses, mik_data, rb_matches, project_bpm, preview_dir: Path) -> int:
    """Render blank-canvas preview PNGs for hint authoring. Returns count rendered."""
    preview_dir.mkdir(parents=True, exist_ok=True)
    rendered = 0
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
            rendered += 1
        except Exception as e:
            print(f"  WARNING: preview render failed for track {i + 1}: {e}")
    return rendered


def enforce_rekordbox_coverage(analyses, rb_matches, allow_partial_rekordbox):
    """Hard gate: stop the pipeline if any track lacks Rekordbox phrase data.

    Phrase grids and per-beat warp markers come from Rekordbox; without them a
    track falls back to librosa (looser warping, weaker section detection).
    The old behaviour caught a desktop-analysis failure, printed "continuing",
    and produced a degraded mix with no warning — the exact failure of
    2026-06-08. This converts that silent degrade into a loud, decidable stop.

    Returns the list of track names missing RB data (empty == full coverage).
    Raises RuntimeError when coverage is partial and the caller has not opted
    into librosa fallback via allow_partial_rekordbox.
    """
    missing_rb = [a.path.name for a in analyses if str(a.path) not in rb_matches]
    if missing_rb and not allow_partial_rekordbox:
        listing = "\n".join(f"    - {n}" for n in missing_rb)
        raise RuntimeError(
            f"\nRekordbox phrase data MISSING for {len(missing_rb)}/{len(analyses)} track(s):\n"
            f"{listing}\n\n"
            "Building a mix without RB phrase/beat-grid data degrades section\n"
            "detection and beat-matching, so the pipeline STOPS here rather than\n"
            "silently producing a lower-quality mix.\n\n"
            "FIX (recommended): open rekordbox 7, turn Library Protection OFF,\n"
            "import these tracks, let analysis finish, then re-run the pipeline.\n"
            "(If RB already analysed them, re-run with --skip-desktop-analyze.)\n\n"
            "OR re-run with --allow-partial-rekordbox to knowingly proceed on\n"
            "librosa fallback for the un-analysed tracks."
        )
    if missing_rb and allow_partial_rekordbox:
        print(f"  WARNING: --allow-partial-rekordbox set — proceeding with "
              f"{len(missing_rb)}/{len(analyses)} track(s) on librosa fallback:")
        for n in missing_rb:
            print(f"    - {n}")
    return missing_rb


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    project_root: Path | None = None,
    config_path: Path | None = None,
    skip_desktop_analyze: bool = False,
    previews_only: bool = False,
    no_hints_required: bool = False,
    sections_layout: bool = False,
    allow_partial_rekordbox: bool = False,
    stem_sections: bool = False,
) -> Path | None:
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent

    config = load_config(config_path or project_root / "Config" / "settings.json")

    # ------------------------------------------------------------------
    # Master-file gate — refuse to feed stems, freezes, or raw audio into
    # the pipeline.  Only mastered WAVs belong here:
    #   • "24 Bit MASTER" (with optional AMENDED / AMENDED V2 etc.)
    #   • "SW V1", "SW V2", … (Sam Wills stem-master renders)
    # Anything else (stems, freezes, pre-masters, bounces) is rejected
    # hard so MIK / Rekordbox never see them.
    # ------------------------------------------------------------------
    _MASTER_PATTERN = re.compile(
        r"(24\s*Bit\s*MASTER|SW\s+V\d+)",
        re.IGNORECASE,
    )
    audio_wavs = sorted(input_dir.glob("*.wav"))
    non_masters = [p.name for p in audio_wavs if not _MASTER_PATTERN.search(p.stem)]
    if non_masters:
        raise ValueError(
            f"\nAudio folder contains {len(non_masters)} non-master file(s):\n"
            + "\n".join(f"  - {n}" for n in non_masters) + "\n\n"
            "Only mastered WAVs are allowed (filename must contain '24 Bit MASTER' or 'SW V<N>').\n"
            "Remove stems, freezes, and pre-masters from Audio/ before running the pipeline."
        )
    if audio_wavs:
        print(f"Master-file gate: {len(audio_wavs)} WAVs, all verified as masters OK")

    # Drive MIK + Rekordbox desktop apps to analyze any tracks they haven't
    # seen yet. Skips quickly when everything is already analyzed.
    if not skip_desktop_analyze:
        print("Desktop analysis (MIK + Rekordbox)...")
        try:
            from automated_dj_mixes.desktop_analyzer import (
                analyze_folder_with_mik, analyze_folder_with_rekordbox,
            )
            audio_paths = sorted(input_dir.glob("*.wav"))
            if audio_paths:
                analyze_folder_with_mik(input_dir, expected_tracks=audio_paths)
                analyze_folder_with_rekordbox(input_dir, expected_tracks=audio_paths)
        except Exception as e:
            # Don't bury this — the Rekordbox-coverage gate below decides
            # whether partial analysis is acceptable. Surface the full message
            # (RekordboxAgentError carries manual-recovery steps).
            print("  WARNING: desktop analysis did not complete cleanly:")
            for line in str(e).splitlines():
                print(f"    {line}")

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

    # HARD GATE — never silently build a mix on partial Rekordbox data
    # (see enforce_rekordbox_coverage). previews_only doesn't need RB.
    if not previews_only:
        enforce_rekordbox_coverage(analyses, rb_matches, allow_partial_rekordbox)

    # Enrich with Mixed In Key 11 data — cue points (highest-confidence
    # structural signal) AND key/BPM from the MIK SQLite DB (critical for
    # WAV files that have no ID3 tags). Falls back silently if MIK hasn't
    # been run on a track.
    mik_data: dict[str, object] = {}   # track path → MikTrackData
    mik_count = 0
    mik_key_count = 0
    print("Reading Mixed In Key 11 data (cues + key/BPM from DB)...")
    for a in analyses:
        try:
            mik = enrich_from_mik(a.path)
            if mik.cues:
                mik_data[str(a.path)] = mik
                mik_count += 1
            elif mik.key or mik.bpm:
                mik_data[str(a.path)] = mik
            if mik.key and not a.key:
                a.key = mik.key
                a.camelot = key_to_camelot(mik.key)
                mik_key_count += 1
            if mik.bpm and not a.bpm:
                a.bpm = mik.bpm
        except Exception:
            pass
    print(f"  Mixed In Key: {mik_count}/{len(analyses)} tracks have auto-cue points")
    if mik_key_count:
        print(f"  Mixed In Key: {mik_key_count}/{len(analyses)} tracks got key from MIK DB (WAV enrichment)")

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

    print("Sequencing by Camelot wheel + BPM proximity...")
    track_dicts = [{"camelot": a.camelot or "1A", "bpm": a.bpm, "analysis": a} for a in analyses]
    sequenced = build_harmonic_path(track_dicts)

    # Energy arc post-pass: reorder within build/peak/cooldown thirds
    for td in sequenced:
        mik = mik_data.get(str(td["analysis"].path))
        td["energy"] = mik.energy if mik else None
    sequenced = apply_energy_arc(sequenced)
    ordered_analyses = [t["analysis"] for t in sequenced]

    for i, a in enumerate(ordered_analyses):
        mik = mik_data.get(str(a.path))
        energy_str = f" E{mik.energy}" if mik and mik.energy is not None else ""
        print(f"  {i+1}. {a.path.name} ({a.camelot or '?'} | {a.bpm:.0f}BPM{energy_str})")

    # Render blank-canvas previews early — these are how visual hints get
    # authored. The --previews-only flag exits here, before transition
    # planning. Production gate below also runs after this point.
    preview_dir = output_dir / "Visualisations" / "Previews"
    preview_count = _render_previews(ordered_analyses, mik_data, rb_matches, project_bpm, preview_dir)
    if preview_count:
        print(f"Track previews (for hint authoring): {preview_count}/{len(ordered_analyses)} -> {preview_dir}")

    if previews_only:
        print("--previews-only: stopping before transition planning.")
        print(f"Author hints at: {input_dir.parent / 'Hints' / 'track_hints.json'}")
        print(f"Required per track: {', '.join(HINT_REQUIRED_FIELDS)}")
        return None

    # Production gate — refuse to plan transitions if hints are missing or
    # incomplete. Bypass with --no-hints-required, --previews-only, or
    # --sections-layout (the last two don't plan transitions at all). The
    # /mix skill is the canonical production path that authors hints before
    # reaching this gate.
    if not no_hints_required and not sections_layout:
        hint_errors = _validate_hints(input_dir, track_hints_data)
        if hint_errors:
            err_block = "\n".join(f"  - {e}" for e in hint_errors)
            raise RuntimeError(
                f"\nCannot run mix pipeline — visual hints missing or incomplete.\n"
                f"{len(hint_errors)} problem(s):\n{err_block}\n\n"
                f"To author hints, run the /mix skill, or generate previews and edit hints manually:\n"
                f"  1. python -m automated_dj_mixes.orchestrator --input <audio> --output <output> --previews-only\n"
                f"  2. Read each PNG in {preview_dir}\n"
                f"  3. Write {input_dir.parent / 'Hints' / 'track_hints.json'} with one entry per track\n"
                f"     containing all of: {', '.join(HINT_REQUIRED_FIELDS)}\n"
                f"  4. Re-run without --previews-only.\n\n"
                f"To bypass for debugging: add --no-hints-required.\n"
            )

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

    # === SECTIONS LAYOUT MODE ===
    # Sam's 2026-05-19 request: lay tracks in mix order with colour-coded
    # sections so the mix structure is visible at a glance. No automation.
    #   intro → green   build → cyan   break → blue   drop → yellow
    #   fill (short low-energy / middle-8s) → orange   outro → red
    #
    # 2026-05-19 update — V2 layout: position each incoming so its first
    # `drop` segment aligns with the outgoing's last `fill`/`break` before
    # outro (the natural bass-drop moment). Section cuts should visually
    # line up across the seam.
    if sections_layout:
        print("SECTIONS LAYOUT MODE — natural-fill aligned, section colour-code, no automation")
        INTERVAL_BARS = 8
        patches = []
        # Per-track: (analysis, markers, mode, segments, total_beats)
        track_data = []
        for i, (analysis, markers, mode) in enumerate(
            zip(ordered_analyses, warp_markers_all, warp_modes)
        ):
            total_beats = markers[-1].beat_time if markers else 0.0
            rb_match = rb_matches.get(str(analysis.path))
            segments = None
            features = None
            if stem_sections:
                # Stem-based detector as the section source — needs no RB phrases
                # (only the audio + bpm + downbeat). The .npz envelope cache makes
                # re-runs on known tracks instant.
                try:
                    from stem_detector import detect as stem_detect
                    from automated_dj_mixes.phrase_viz import (
                        segments_from_stem_sections, validate_bar_math,
                    )
                    stem_res = stem_detect(
                        analysis.path, input_dir.parent,
                        bpm=analysis.bpm, downbeat=analysis.first_downbeat_sec,
                        make_viz=False,
                    )
                    if stem_res:
                        segments = segments_from_stem_sections(stem_res)
                        for w in validate_bar_math(segments, analysis.path.stem[:40]):
                            print(f"  BAR-MATH: {w}")
                except Exception as e:
                    print(f"  WARNING: stem section detection failed for {analysis.path.name}: {e}")
            elif rb_match:
                offset = getattr(rb_match, "first_downbeat_offset", 0)
                ext_path = Path(rb_match.ext_path) if rb_match.ext_path else None
                try:
                    features = extract_track_features(
                        audio_path=analysis.path,
                        bpm=rb_match.bpm or analysis.bpm,
                        beat_times_ms=rb_match.beat_times_ms,
                        first_downbeat_offset=offset,
                        ext_path=ext_path,
                    )
                    intervals = build_intervals(rb_match, features, interval_bars=INTERVAL_BARS)
                    segments = segments_from_intervals(intervals)
                    # 2026-05-19: refine with per-beat data — split intro
                    # build-zones, find 1-4 bar fills inside drops.
                    from automated_dj_mixes.phrase_viz import refine_segments, validate_bar_math
                    segments = refine_segments(segments, features)
                    # Bar-math validation — print warnings for any chop whose
                    # delta from previous chop isn't on a 4-bar grid (Sam's
                    # rule: every event should land at 4/8/12/16/24/32/...).
                    for w in validate_bar_math(segments, analysis.path.stem[:40]):
                        print(f"  BAR-MATH: {w}")
                except Exception as e:
                    print(f"  WARNING: section analysis failed for {analysis.path.name}: {e}")
            else:
                print(f"  WARNING: no Rekordbox data for {analysis.path.name} — single uncoloured clip")
            track_data.append((analysis, markers, mode, segments, total_beats))

        # Compute arrangement positions using natural-fill alignment.
        def first_drop_source(segs):
            return next((s.source_start_beats for s in segs if s.label == "drop"), 0.0)

        def last_natural_swap_source(segs, total_beats):
            """Outgoing's natural bass-drop point: the LAST fill or break
            before the outro. Falls back to outro start, then 75% of total."""
            outro_idx = next((i for i, s in enumerate(segs) if s.label == "outro"), len(segs))
            for s in reversed(segs[:outro_idx]):
                if s.label in ("fill", "break"):
                    return s.source_start_beats
            if outro_idx < len(segs):
                return segs[outro_idx].source_start_beats
            return total_beats * 0.75

        cumulative_arr = 0.0
        for i, (analysis, markers, mode, segments, total_beats) in enumerate(track_data):
            if i == 0:
                arr_start = 0.0
            else:
                prev_analysis, prev_markers, prev_mode, prev_segs, prev_total = track_data[i - 1]
                prev_arr_start = patches[-1].arrangement_start_beats
                if prev_segs and segments:
                    swap_src = last_natural_swap_source(prev_segs, prev_total)
                    drop_src = first_drop_source(segments)
                    arr_start = prev_arr_start + swap_src - drop_src
                else:
                    arr_start = prev_arr_start + prev_total
                # Clamp to ≥ prev_arr_start (no negative overlap with rewinding)
                arr_start = max(arr_start, prev_arr_start)

            if segments:
                label_counts: dict[str, int] = {}
                for s in segments:
                    label_counts[s.label] = label_counts.get(s.label, 0) + 1
                counts_str = " ".join(f"{k}:{v}" for k, v in label_counts.items())
                first_drop = first_drop_source(segments)
                print(f"  {i+1}. {analysis.path.name[:60]} @ arr-beat {arr_start:.0f}  drop_1@src {first_drop:.0f}  [{counts_str}]")
            else:
                print(f"  {i+1}. {analysis.path.name[:60]} @ arr-beat {arr_start:.0f}  [no sections — full clip]")

            patches.append(TrackPatch(
                analysis=analysis,
                track_index=i,
                warp_markers=markers,
                gain_offset_db=0.0,
                arrangement_start_beats=arr_start,
                warp_mode=mode,
                phrase_segments=segments,
            ))

        # Single tempo point — sections layout is a visual reference, no per-track ramps.
        tempo_points = [AutomationPoint(time_beats=0, value=project_bpm)]

        template_path = _find_template(project_root, min_tracks=len(ordered_analyses))
        output_dir.mkdir(parents=True, exist_ok=True)
        # Find max existing Sections V<N> in both root and *Project subfolders
        # (Sam saves manual edits as "Sections V<N> ... Project/<name>.als").
        existing_versions: list[int] = []
        for p in list(output_dir.glob("Sections V*.als")) + list(output_dir.glob("Sections V* Project/*.als")):
            for part in p.stem.split():
                if part.startswith("V") and part[1:].isdigit():
                    existing_versions.append(int(part[1:]))
                    break
        version = max(existing_versions, default=0) + 1
        output_path = output_dir / f"Sections V{version}.als"
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

    # The single-command full-mix path (Path A) has been retired.
    # Production mixes use the three-phase /mix pipeline: --sections-layout
    # here, then propose_arrangement.py, then apply_automation.py.
    raise RuntimeError(
        "Full-mix mode has been retired. Use the three-phase /mix "
        "pipeline: --sections-layout, then propose_arrangement.py, "
        "then apply_automation.py."
    )


def main():
    parser = argparse.ArgumentParser(description="Generate an Ableton Live session from tagged dance tracks")
    parser.add_argument("--input", required=True, help="Folder containing audio tracks")
    parser.add_argument("--output", required=True, help="Folder for generated ALS output")
    parser.add_argument("--config", help="Path to settings.json")
    parser.add_argument("--skip-desktop-analyze", action="store_true",
                        help="Skip driving MIK and Rekordbox UI (use only existing analysis data)")
    parser.add_argument("--allow-partial-rekordbox", action="store_true",
                        help="Proceed even if some tracks lack Rekordbox phrase data "
                             "(knowingly degraded — librosa fallback). Default: hard-stop.")
    parser.add_argument("--previews-only", action="store_true",
                        help="Render blank-canvas preview PNGs and exit before transition "
                             "planning. Used by the /mix skill so Claude can read previews "
                             "and author Hints/track_hints.json. Bypasses the hint gate.")
    parser.add_argument("--no-hints-required", action="store_true",
                        help="Allow the pipeline to run without complete visual hints. "
                             "Production runs require hints (gated by /mix skill); use this "
                             "flag for debugging only.")
    parser.add_argument("--sections-layout", action="store_true",
                        help="Sections-layout mode: lay tracks SEQUENTIALLY in mix order, "
                             "colour-code each section by type (intro=green, break=blue, "
                             "drop=yellow, outro=red, fill=orange). No transitions, no "
                             "automation, no hints required. Output is 'Sections V<N>.als'.")
    parser.add_argument("--stem-sections", action="store_true",
                        help="In --sections-layout mode, use the Demucs stem-based detector "
                             "(Source/stem_detector.py) as the section source instead of "
                             "Rekordbox phrases. Analysis-only; envelope cache makes re-runs "
                             "on known tracks instant.")
    args = parser.parse_args()

    als_path = run_pipeline(
        Path(args.input),
        Path(args.output),
        config_path=Path(args.config) if args.config else None,
        skip_desktop_analyze=args.skip_desktop_analyze,
        previews_only=args.previews_only,
        no_hints_required=args.no_hints_required,
        sections_layout=args.sections_layout,
        allow_partial_rekordbox=args.allow_partial_rekordbox,
        stem_sections=args.stem_sections,
    )
    if als_path is not None:
        print(f"Generated: {als_path}")


if __name__ == "__main__":
    main()
