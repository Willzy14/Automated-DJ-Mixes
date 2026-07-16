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
    """Count the USABLE mix tracks in a template = <AudioTrack> elements minus the
    reserved first track (the 'Session Time' marker track that als_generator skips
    via track_ranges[1:]). Without the -1 a 12-AudioTrack template (only 11 mixable)
    gets picked for a 12-track mix and the 12th track is silently dropped — that's
    why Huxley fell off the 09.06.26 mix (Sam 2026-06-10). Keep in sync with
    als_generator._find_track_line_ranges / the track_ranges[1:] reservation."""
    import gzip
    try:
        data = gzip.open(str(als_path)).read().decode("utf-8", errors="replace")
        n = data.count("<AudioTrack ")
        return max(0, n - 1)   # exclude the reserved Session Time track
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


def enforce_owned_grid_coverage(analyses, grid_matches):
    """Hard gate: owned stem-grid mode must produce a full per-beat grid."""
    missing = [
        analysis.path.name
        for analysis in analyses
        if len(getattr(grid_matches.get(str(analysis.path)), "beat_times_ms", [])) < 8
    ]
    if missing:
        listing = "\n".join(f"    - {name}" for name in missing)
        raise RuntimeError(
            f"\nOwned stem-grid MISSING for {len(missing)}/{len(analyses)} track(s):\n"
            f"{listing}\n\n"
            "The production path does not fall back to Rekordbox or a two-marker "
            "constant grid. Fix or exclude these tracks, then rerun."
        )
    print(f"  Owned stem-grid coverage: {len(analyses)}/{len(analyses)} tracks")
    return missing


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
    allow_bad_grids: bool = False,
    allow_non_master: bool = False,
    stem_sections: bool = False,
    stem_grid: bool = False,
    kick_model: bool = False,
    kick_model_path: Path | None = None,
    kick_model_device: str = "auto",
    track_order: list[str] | None = None,
) -> Path | None:
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent

    config = load_config(config_path or project_root / "Config" / "settings.json")

    # --stem-grid is only one-clock-safe on the stem-section cut path
    # (segments_from_stem_sections -> sec_to_clip_beats reads the injected grid). The
    # RB-phrase cut path uses extract_track_features, whose disk cache key omits the
    # beat grid — so cuts could be computed on a stale RB grid while audio warps to the
    # stem grid (two clocks ~1% apart = the bug the one-clock rule forbids). Require
    # --stem-sections so the safe cut path is the one that runs.
    if stem_grid and not previews_only and not (sections_layout and stem_sections):
        raise RuntimeError(
            "--stem-grid requires --sections-layout --stem-sections (the one-clock-safe "
            "cut path). Without it, section cuts read grid-unaware cached features while "
            "audio warps to the stem grid — a two-clock split. Add both flags, or drop "
            "--stem-grid to use the Rekordbox/.asd grid.")

    if kick_model and not (sections_layout and stem_sections):
        raise RuntimeError(
            "--kick-model only applies to --sections-layout --stem-sections. "
            "Add both flags, or drop --kick-model to keep the legacy section path.")

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
    if non_masters and not allow_non_master:
        raise ValueError(
            f"\nAudio folder contains {len(non_masters)} non-master file(s):\n"
            + "\n".join(f"  - {n}" for n in non_masters) + "\n\n"
            "Only mastered WAVs are allowed (filename must contain '24 Bit MASTER' or 'SW V<N>').\n"
            "Remove stems, freezes, and pre-masters from Audio/ before running the pipeline,\n"
            "OR pass --allow-non-master if these are finished third-party tracks (a promo/"
            "curated set). The gate exists to keep stems/freezes/pre-masters out — only "
            "bypass it when you've confirmed every file is a finished release."
        )
    if non_masters and allow_non_master:
        print(f"Master-file gate: --allow-non-master set — proceeding with "
              f"{len(non_masters)} non-Sam-master WAV(s) (confirmed finished tracks).")
    if audio_wavs:
        print(f"Master-file gate: {len(audio_wavs)} WAVs OK")

    # Owned-grid mode drives MIK only. Legacy mode still drives both apps.
    if not skip_desktop_analyze:
        mode = "MIK only; Rekordbox disabled" if stem_grid else "MIK + Rekordbox"
        print(f"Desktop analysis ({mode})...")
        try:
            from automated_dj_mixes.desktop_analyzer import analyze_folder_with_mik
            audio_paths = sorted(input_dir.glob("*.wav"))
            if audio_paths:
                analyze_folder_with_mik(input_dir, expected_tracks=audio_paths,
                                        allow_non_master=allow_non_master)
                if not stem_grid:
                    from automated_dj_mixes.desktop_analyzer import analyze_folder_with_rekordbox
                    analyze_folder_with_rekordbox(
                        input_dir,
                        expected_tracks=audio_paths,
                        allow_non_master=allow_non_master,
                    )
        except Exception as e:
            # Surface metadata-analysis failures; grid coverage is gated later.
            print("  WARNING: desktop analysis did not complete cleanly:")
            for line in str(e).splitlines():
                print(f"    {line}")

    print(f"Analysing tracks in {input_dir}...")
    analyses = analyse_folder(input_dir)
    if not analyses:
        raise ValueError(f"No audio files found in {input_dir}")
    print(f"  Found {len(analyses)} tracks")

    # Grid overrides apply BEFORE enrichment so the warp markers, downbeat
    # anchor, one-clock cuts and the gate all see the corrected grid. Loaded
    # up front because replace_grid overrides (fitted from Ableton .asd
    # ticks) are also the RB-LESS grid source when the local Rekordbox DB
    # doesn't know these tracks — RB/MIK DBs are machine-local and don't
    # follow the project between Sam's machines; the .asd files do.
    grid_overrides: dict = {}
    apply_grid_override = None
    try:
        from validate_beatgrid import load_grid_overrides, apply_grid_override
        grid_overrides = load_grid_overrides(input_dir.parent)
    except ImportError:
        pass

    # Legacy Rekordbox enrichment is bypassed entirely in owned-grid mode.
    rb_count = 0
    rb_matches: dict[str, object] = {}  # track path → RekordboxAnalysis (for beat grid warp)
    if stem_grid:
        print("Rekordbox disabled: owned stem-grid + stem-sections are authoritative")
    else:
        try:
            from automated_dj_mixes.rekordbox_reader import read_rekordbox_library, find_rekordbox_match
            print("Reading Rekordbox library...")
            rb_library = read_rekordbox_library()
            for a in analyses:
                rb_match = find_rekordbox_match(a.path.name, rb_library)
                if rb_match and rb_match.phrases:
                    if apply_grid_override and a.path.name in grid_overrides:
                        ov = grid_overrides[a.path.name]
                        apply_grid_override(rb_match, ov)
                        kind = ("replace_grid" if ov.get("replace_grid")
                                else f"shift {ov.get('shift_ms', 0):+.1f}ms")
                        print(f"  [grid-override] {a.path.name[:50]}: {kind}")
                    enrich_from_rekordbox(a, rb_match)
                    rb_matches[str(a.path)] = rb_match
                    rb_count += 1
            print(f"  Rekordbox: {rb_count}/{len(analyses)} tracks enriched with phrase data")
        except Exception as e:
            print(f"  Rekordbox unavailable ({e}), using librosa analysis only")

    # RB-LESS grid source: synthesize a grid carrier from a replace_grid
    # override for any track Rekordbox didn't cover. Stem-sections mode only —
    # these shells carry a beat grid but NO phrase data, so they must not
    # satisfy the phrase-coverage gate for the retired RB-phrase path.
    if stem_sections and apply_grid_override and not stem_grid:
        from automated_dj_mixes.rekordbox_reader import RekordboxAnalysis
        tick_count = 0
        for a in analyses:
            if str(a.path) in rb_matches:
                continue
            ov = grid_overrides.get(a.path.name)
            if not ov or not ov.get("replace_grid"):
                continue
            shell = RekordboxAnalysis(
                file_path=str(a.path), title=a.path.stem, bpm=0.0,
                key_name=None, mood=1, end_beat=0, phrases=[],
                beat_times_ms=[], first_downbeat_offset=0)
            apply_grid_override(shell, ov)
            shell.end_beat = len(shell.beat_times_ms)
            rb_matches[str(a.path)] = shell
            tick_count += 1
            print(f"  [tick-grid] {a.path.name[:50]}: "
                  f"{ov['replace_grid']['bpm']} BPM grid from Ableton ticks")
        if tick_count:
            print(f"  Tick grids: {tick_count}/{len(analyses)} tracks gridded "
                  f"from .asd-fitted overrides (RB-less)")

    # OUR-OWN-GRID source: build the beat grid from the stem-kick detector.
    # detector (Source/automated_dj_mixes/stem_grid.py). Injected into rb_matches
    # here — BEFORE the BPM-authority loop, the beatgrid gate, the warp loop and
    # the section-cut path — so every grid consumer reads ONE grid object per
    # track and the one-clock invariant holds by construction.
    #   - confident grid (flag "") -> stem-grid is the sole authority.
    #   - per-beat TIMING is snapped to Ableton's .asd transients where present
    #     (sample-accurate, fixes soft-kick lag — Eli sat 7.6ms late, snaps to 0);
    #     our detector keeps the structure, Ableton refines the timing.
    #   - weak/syncopated (LOWC/JIT) grids are judged by the hard grid gate;
    #     production never falls back to Rekordbox or a two-marker grid.
    # Separates its own drum stem (GPU Demucs); the Phase-1a reuse is a TODO.
    if stem_grid and not previews_only:
        from automated_dj_mixes.stem_grid import detect_beat_grid
        from automated_dj_mixes.rekordbox_reader import RekordboxAnalysis
        try:
            from asd_onsets import ableton_onsets_sec
        except ImportError:
            ableton_onsets_sec = lambda _p: None
        import numpy as _np
        def _disagree_ms(a_ms, b_ms):
            a = _np.asarray(a_ms, float); b = _np.sort(_np.asarray(b_ms, float))
            idx = _np.clip(_np.searchsorted(b, a), 1, len(b) - 1)
            near = _np.where(_np.abs(a - b[idx - 1]) <= _np.abs(a - b[idx]), b[idx - 1], b[idx])
            res = _np.abs(a - near)
            res = res[res <= _np.median(_np.diff(b)) / 2]
            return float(_np.median(res)) if len(res) else float("nan")
        n_stem = n_snap = 0
        for a in analyses:
            try:
                ticks = ableton_onsets_sec(a.path)       # Ableton's transients if analysed
                bg = detect_beat_grid(a.path, asd_ticks=ticks)
            except Exception as e:
                print(f"  [stem-grid] {a.path.name[:46]}: detection failed "
                      f"({type(e).__name__}) — keeping existing grid")
                continue
            existing = rb_matches.get(str(a.path))
            has_rb = existing is not None and len(getattr(existing, "beat_times_ms", [])) >= 8
            if bg.flag in ("LOWC", "JIT") and not bg.snapped_to_asd and has_rb:
                print(f"  [stem-grid] {a.path.name[:46]}: flagged {bg.flag}, no .asd to "
                      f"rescue timing — keeping RB as fallback")
                continue
            note = ""
            if has_rb:
                dis = _disagree_ms(bg.beat_times_ms, existing.beat_times_ms)
                note = (f" — OVERRIDES RB (disagree {dis:.0f}ms; we sit {bg.grid_vs_kick_ms}ms "
                        f"on the kicks)" if not _np.isnan(dis) and dis > 25
                        else f" — agrees w/ RB ({dis:.0f}ms)")
                existing.beat_times_ms = bg.beat_times_ms
                existing.first_downbeat_offset = bg.first_downbeat_offset
                existing.bpm = bg.bpm
                existing.end_beat = len(bg.beat_times_ms)
            else:
                rb_matches[str(a.path)] = RekordboxAnalysis(
                    file_path=str(a.path), title=a.path.stem, bpm=bg.bpm,
                    key_name=None, mood=1, end_beat=len(bg.beat_times_ms), phrases=[],
                    beat_times_ms=bg.beat_times_ms,
                    first_downbeat_offset=bg.first_downbeat_offset)
                note = " — no RB; stem-grid sole source"
            if bg.flag == "JIT":
                note += (f" [OFF THE KICKS by {bg.grid_vs_kick_ms:.0f}ms — out of range, "
                         f"the beatgrid gate will reject this]")
            elif bg.snapped_to_asd:
                note += " [.asd-snapped]"; n_snap += 1
            elif bg.timing_src == "own-transients":
                note += " [own-transient timing ~1ms]"
            # Tell the beatgrid gate this grid is STEM-derived (built FROM the kicks):
            # without provenance the gate judges it by the librosa whole-mix R test,
            # which smears on percussion-heavy house and FALSE-FAILS perfect grids
            # (10/35 corpus tracks hard-stop, universally where R < the rescue floor).
            # stem_fitted=True makes the gate judge tempo confirmed + phase advisory.
            # ALSO pass grid_vs_kick_ms so the gate can FAIL a stem grid that's off its
            # own kicks (Afro/Latin / jackin' out-of-range -> 88ms): provenance must not
            # be a blanket pass — the grid still has to sit on the transients.
            ov_entry = grid_overrides.setdefault(a.path.name, {})
            ov_entry["phase_source"] = "drum-stem-kicks"
            ov_entry["grid_vs_kick_ms"] = bg.grid_vs_kick_ms
            # Keep the downbeat clock single: a.first_downbeat_sec was set from the RB/
            # librosa grid earlier; realign it to OUR grid so no later reader can split it.
            a.first_downbeat_sec = bg.beat_times_ms[bg.first_downbeat_offset] / 1000.0
            n_stem += 1
            flag_s = f" [{bg.flag}]" if bg.flag else ""
            print(f"  [stem-grid] {a.path.name[:46]}: {bg.bpm}bpm, "
                  f"{bg.grid_vs_kick_ms}ms on kicks{flag_s}{note}")
        if n_stem:
            print(f"  Stem-grid: {n_stem}/{len(analyses)} tracks gridded from our own "
                  f"kick detector ({n_snap} timing-snapped to Ableton .asd transients; "
                  f"Rekordbox disabled)")

    # The grid is the BPM AUTHORITY for every track that has one. Without
    # this, a.bpm stays librosa's quantized lattice whenever the MIK DB is
    # absent (machine-local) — on 2026-06-12 those lattice values matched
    # project_bpm within 0.05 and selected Repitch, which would have
    # detuned 7 tracks of an in-key mix. MIK enrichment may still refine
    # a.bpm later where its DB exists.
    from automated_dj_mixes.warping import grid_bpm_and_downbeat
    for a in analyses:
        rb = rb_matches.get(str(a.path))
        if rb and len(getattr(rb, "beat_times_ms", [])) >= 8:
            g_bpm, _ = grid_bpm_and_downbeat(
                rb.beat_times_ms, getattr(rb, "first_downbeat_offset", 0),
                getattr(rb, "bpm", None))
            if g_bpm and g_bpm > 40.0:
                a.bpm = g_bpm

    # HARD GATE: require complete coverage from the selected grid authority.
    if not previews_only:
        if stem_grid:
            enforce_owned_grid_coverage(analyses, rb_matches)
        else:
            enforce_rekordbox_coverage(analyses, rb_matches, allow_partial_rekordbox)

    # Beatgrid quality gate — sections-layout is the production entry point
    # where warp markers + cuts get baked, so bad grids must hard-stop HERE
    # (the 09.06.26 'Todd' bug shipped because nothing checked the grids
    # against the audio). Previews don't warp; skip there.
    if sections_layout and not previews_only:
        try:
            from validate_beatgrid import enforce_beatgrid_quality
        except ImportError as e:
            print(f"  WARNING: beatgrid gate unavailable ({e}) — proceeding unchecked")
        else:
            enforce_beatgrid_quality(analyses, rb_matches,
                                     allow_bad_grids=allow_bad_grids,
                                     grid_overrides=grid_overrides)

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

    # Optional manual order override (testing different arrangements): each entry
    # is a case-insensitive substring matched against the track filename stem.
    if track_order:
        remaining = list(ordered_analyses)
        reordered = []
        for key in track_order:
            a = next((x for x in remaining if key.lower() in x.path.stem.lower()), None)
            if a is not None:
                reordered.append(a)
                remaining.remove(a)
        if len(reordered) == len(ordered_analyses):
            ordered_analyses = reordered
            print(f"Custom track order applied ({len(reordered)} tracks)")
        else:
            print(f"  WARNING: custom order matched {len(reordered)}/{len(ordered_analyses)} "
                  f"tracks — keeping sequencer order")

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
        if rb_match and hasattr(rb_match, "beat_times_ms") and len(rb_match.beat_times_ms) >= 8:
            grid_label = "stem-grid" if (stem_grid and not getattr(rb_match, "phrases", None)) else "beat grid"
            warp_src = f"{grid_label} ({len(markers)} markers)"
        else:
            warp_src = "2-marker linear"
        print(f"  {a.path.name}: {mode_name}, {warp_src}")
    if rb_warp_count:
        source = "owned stem grid" if stem_grid else "Rekordbox grid"
        print(f"  Beat grid warp: {rb_warp_count}/{len(ordered_analyses)} tracks using {source}")

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
                    from automated_dj_mixes.warping import grid_bpm_and_downbeat

                    # ONE CLOCK: the detector's constant bpm/downbeat MUST come
                    # from the same grid the warp markers use — analysis.bpm is
                    # librosa's quantized lattice when tags are absent (cannot
                    # say 128.00; caused the 09.06.26 cuts-off regression).
                    det_bpm, det_downbeat = analysis.bpm, analysis.first_downbeat_sec
                    grid_times, grid_offset = None, 0
                    if rb_match and len(getattr(rb_match, "beat_times_ms", [])) >= 8:
                        g_bpm, g_db = grid_bpm_and_downbeat(
                            rb_match.beat_times_ms,
                            getattr(rb_match, "first_downbeat_offset", 0),
                            getattr(rb_match, "bpm", None),
                        )
                        if g_bpm:
                            det_bpm, det_downbeat = g_bpm, g_db
                            grid_times = rb_match.beat_times_ms
                            grid_offset = getattr(rb_match, "first_downbeat_offset", 0)
                            grid_name = "owned stem grid" if stem_grid else "Rekordbox grid"
                            print(f"  [one-clock] {analysis.path.name[:46]}: "
                                  f"detector on {grid_name} ({g_bpm:.2f} BPM, downbeat {g_db:.3f}s)")
                    stem_res = stem_detect(
                        analysis.path, input_dir.parent,
                        bpm=det_bpm, downbeat=det_downbeat,
                        kick_model=kick_model,
                        kick_model_path=kick_model_path,
                        kick_model_device=kick_model_device,
                        make_viz=True,   # DETECT_<track>.png = the per-track sanity check
                                         # (full track + 4 stem panels + section/beat annotations).
                                         # Replaces the old 80-PNG blind pass (Sam 2026-06-10).
                    )
                    if stem_res:
                        segments = segments_from_stem_sections(
                            stem_res,
                            beat_times_ms=grid_times,
                            first_downbeat_offset=grid_offset,
                        )
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

        # Sections-layout is a per-track section-chop artifact for REVIEW only.
        # The mix ARRANGEMENT (track positions, swaps, loops/cuts) is computed
        # SOLELY by align_engine in propose_arrangement, which re-positions every
        # clip from a 0 baseline. So we lay tracks out END-TO-END here — a plain,
        # deterministic, order-preserving layout with NO arrangement model.
        # (The old natural-fill positioner — last_natural_swap_source/first_drop_
        # source — was PURGED 2026-06-09 so no old positioning code can run.)
        def first_drop_source(segs):    # logging only, not positioning
            return next((s.source_start_beats for s in segs if s.label == "drop"), 0.0)

        cumulative_arr = 0.0
        for i, (analysis, markers, mode, segments, total_beats) in enumerate(track_data):
            arr_start = cumulative_arr
            cumulative_arr += max(float(total_beats), 4.0)   # end-to-end, no overlap

            if segments:
                label_counts: dict[str, int] = {}
                for s in segments:
                    label_counts[s.label] = label_counts.get(s.label, 0) + 1
                counts_str = " ".join(f"{k}:{v}" for k, v in label_counts.items())
                print(f"  {i+1}. {analysis.path.name[:60]} @ arr-beat {arr_start:.0f}  drop_1@src {first_drop_source(segments):.0f}  [{counts_str}]")
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
    parser.add_argument("--allow-bad-grids", action="store_true",
                        help="Proceed even if the beatgrid gate finds grids that "
                             "don't sit on their audio (warp WILL drift on those "
                             "tracks — the 09.06.26 'Todd' bug).")
    parser.add_argument("--allow-partial-rekordbox", action="store_true",
                        help="Proceed even if some tracks lack Rekordbox phrase data "
                             "(knowingly degraded — librosa fallback). Default: hard-stop.")
    parser.add_argument("--allow-non-master", action="store_true",
                        help="Allow WAVs that don't match Sam's master naming "
                             "('24 Bit MASTER' / 'SW V<N>') — for mixing finished "
                             "THIRD-PARTY tracks (promo/curated sets). The gate still "
                             "exists to keep stems/freezes out; only set this when every "
                             "file is a confirmed finished release.")
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
    parser.add_argument("--stem-grid", action="store_true",
                        help="Use our own stem-kick beat detector "
                             "(automated_dj_mixes.stem_grid) as the sole beat-grid authority. "
                             "Disables Rekordbox desktop analysis and library reads; weak grids "
                             "fail closed instead of falling back. Separates a drum stem on GPU.")
    parser.add_argument("--kick-model", action="store_true",
                        help="Use Kick Detector V3 for stem-section kick IN/OUT presence. "
                             "Requires --sections-layout --stem-sections. Default is off.")
    parser.add_argument("--kick-model-path", type=Path, default=None,
                        help="Path to Kick Detector weights. Defaults to sibling "
                             "'Kick Detector/Models/kick_crnn_V3.pt'.")
    parser.add_argument("--kick-model-device", default="auto",
                        help="Torch device for Kick Detector and its Demucs pass: auto, cpu, or cuda.")
    parser.add_argument("--order", type=str, default=None,
                        help="Manual track order override (testing). Comma-separated, case-"
                             "insensitive substrings of filenames, e.g. \"Samm,Call Me,Crusy\". "
                             "Bypasses the auto-sequencer.")
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
        allow_bad_grids=args.allow_bad_grids,
        allow_non_master=args.allow_non_master,
        stem_sections=args.stem_sections,
        stem_grid=args.stem_grid,
        kick_model=args.kick_model,
        kick_model_path=args.kick_model_path,
        kick_model_device=args.kick_model_device,
        track_order=[k.strip() for k in args.order.split(",")] if args.order else None,
    )
    if als_path is not None:
        print(f"Generated: {als_path}")


if __name__ == "__main__":
    main()
