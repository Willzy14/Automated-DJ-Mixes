# Toolbox — Automated DJ Mixes

Module reference for all pipeline components.

## Modules

### `Source/validate_beatgrid.py` (2026-06-11, v2 same day)
Hard-stop gate: does each track's Rekordbox beat grid sit ON its audio? Whole-track kick onsets (150Hz lowpass — not mel fmax, which produces empty filters), half-beat-circle phase concentration (R) folds house offbeat-bass stabs so locked grids read high regardless of bassline; mean full-circle phase catches grids whose tempo is right but markers sit between the kicks (the Todd case). Per-track +1% detuned twin acts as a known-bad control. Calibrated on 22 tracks (08.06.26 + 09.06.26) + 12 more (11.06.26). Wired into `--sections-layout`; `--allow-bad-grids` to override.

**v2 — MIK tiebreaker (11.06.26 run):** percussion-heavy genres (Latin house, gospel stabs) smear R below the absolute thresholds even on correct grids. `check_grid(..., independent_bpm, db_bpm)` + `verdict_from(..., tempo_confirmed)`: a track is rescued from the ambiguous band only when R≥0.20, ≥5× its detuned control, the grid is internally consistent (span vs RB DB ≤0.5%) AND MIK agrees with the grid span ≤0.2% AND the phase is clean. Never rescues noise-floor grids; never overrides a bad phase.

**Grid overrides** (`<project>/Hints/grid_overrides.json`, applied by the orchestrator before enrichment so warp/cuts/gate all see the corrected grid):
- `shift_ms` — phase slide (the Todd fix). Written by CLI `--write-override <substr>` (measures, composes with existing shifts).
- `replace_grid` — full constant-grid synthesis for unusable grids (first case: La Trumpter — internally inconsistent RB grid, true 126 BPM confirmed by MIK + Sam). `_fit_anchor` kick-fits the anchor (bar-phase inherited from the old grid's downbeat); `write_grid_replacement(project, wav, rb, true_bpm)` PROVES the fit with the gate before writing — a failing fit is refused.

Library: `check_grid`, `enforce_beatgrid_quality`, `load_grid_overrides`, `apply_grid_override`, `write_phase_override`, `write_grid_replacement`, `_fit_anchor`, `verdict_from` (pure). CLI: project table + `--write-override`.


### `Source/automated_dj_mixes/orchestrator.py`
Main pipeline controller. The canonical `/mix` path uses `--stem-grid --stem-sections --kick-model`: owned per-beat grids, Demucs structure and Kick Detector V3 evidence. In this mode the orchestrator runs MIK only for optional key/energy metadata, never launches or reads Rekordbox, requires complete owned-grid coverage, and fails weak grids closed. Legacy non-stem-grid callers retain the old Rekordbox path. CLI: `python -m automated_dj_mixes.orchestrator --input "Tracks/" --output "Output/"`.

Key functions: `run_pipeline()`, `enforce_owned_grid_coverage()`, `_find_template()`, `_next_version()`, `main()` (CLI).

### `Source/automated_dj_mixes/analysis.py`
Reads key/BPM from file tags (mutagen ID3/Vorbis). Transient/downbeat detection (librosa). LUFS measurement (pyloudnorm). Bass section detection (off-beat energy sampling). Phrase-aware break detection. Rekordbox enrichment maps RB phrases → pipeline fields (bass_start/end, break_start/end, intro_end, last_kick).

Key types: `TrackAnalysis` (dataclass with path, key, camelot, bpm, lufs, first_downbeat_sec, duration_sec, sample_rate, bass_start_sec, bass_end_sec, first_break_start_sec, first_break_end_sec, intro_end_sec, last_kick_sec, rekordbox_phrases, analysis_source, warnings).
Key functions: `analyse_track()`, `analyse_folder()`, `enrich_from_rekordbox()`, `_detect_downbeat()`, `_detect_bass_section()`, `_detect_first_break_phrase_aware()`.

### `Source/automated_dj_mixes/sequencer.py`
Full Camelot wheel mapping (24 keys + common aliases like "Am", "Bbm", "F#"). Compatibility scoring: 4=identical, 3=smooth/relative, 2=power, 1=diagonal, 0=clash. Greedy nearest-neighbour harmonic path with **composite scoring**: `(camelot_norm * 0.6) + (bpm_norm * 0.4)`, both normalized to 0-1. **Energy arc post-pass**: `apply_energy_arc()` divides tracks into build/peak/cooldown thirds, sorts by MIK OverallEnergy (0-10), with BPM-gap guard (rejects reorder if 15+ BPM gap). **20 tests.**

Key functions: `key_to_camelot()`, `compatibility_score()`, `is_compatible()`, `build_harmonic_path()`, `apply_energy_arc()`, `_bpm_proximity()`.

### `Source/automated_dj_mixes/rekordbox_reader.py`
Reads Rekordbox 7 ANLZ files (`.DAT`, `.EXT`) for beat grids, phrase analysis, and key data. Manual PSSI binary parser (pyrekordbox doesn't expose phrase data; construct.ConstError on RB7 files). Matches tracks by filename against Rekordbox library.

Key types: `PhraseEntry` (start_beat, label, kind, fill, fill_beat), `RekordboxAnalysis` (title, bpm, key, beat_times_ms, first_downbeat_offset, phrases, ext_path, + helpers).
Key functions: `read_rekordbox_library()`, `find_rekordbox_match()`, `beat_to_sec()`, `phrase_end_beat()`, `first_phrase_of()`.

### `Source/automated_dj_mixes/rekordbox_waveform.py`
Parses Rekordbox's purpose-built waveform colour data (PWV5 / PWV4) from the same `.EXT` files. Each PWV5 entry is a 16-bit big-endian word with LSB-first packing: 3-bit R + 3-bit G + 3-bit B + 5-bit height. Neutral colour/height fields — frequency-band correlation is the Rekordbox UI convention, not formally validated against spectral separation.

Key types: `WaveformEntry` (color_r/g/b 0-7, height 0-31).
Key functions: `parse_pwv5()`, `parse_pwv4()`, `parse_waveform()` (PWV5 first, PWV4 fallback), `waveform_per_beat()` (aggregates per-pixel data into beat-aligned arrays).

### `Source/automated_dj_mixes/features.py`
Per-beat feature extraction with disk cache. Combines librosa (overall RMS + 40-180Hz bass band) with PWV5 waveform data. Cache key includes audio path/mtime/size/ANALYSIS_MODEL_VERSION — avoids re-running librosa on every viz iteration. Stores `BeatFeatures` per beat plus track-local p30/p50/p70 percentile stats per signal.

Key types: `BeatFeatures` (beat_index, sec, rms, bass, wf_height, wf_r/g/b), `FeatureStats` (p30, p50, p70), `TrackFeatures` (whole-track container).
Key functions: `extract_track_features()` (cached entry point), `smooth_window()` (rolling-mean smoothing).
Cache dir: `Test Project/May 2026 Mix/Analysis Cache/`.

### `Source/automated_dj_mixes/phrase_viz.py`
Builds factual `Interval` records (one per 8-bar slot) from Rekordbox phrases + per-beat features. No labels or cue flags on `Interval` — those live in cue_candidates.py. `segments_from_intervals()` is the visualization-only collapse into colour-coded clips (intro green / drop yellow / break blue / outro red).

Key types: `IntervalEnergy`, `Interval`, `PhraseSegment`.
Key functions: `build_intervals()`, `segments_from_intervals()`.

### `Source/automated_dj_mixes/cue_candidates.py`
Interpretation layer. Reads `Interval` lists and emits ranked `CueCandidate` records with confidence (0-1) + sources list + human-readable reasons. Five cue types: `bass_entry`, `break_start`, `break_end`, `chop_point`, `outro_start`. Pre-chorus candidates penalized 15% but never hidden (Harry Romero fix).

Five candidate sources (selection precedence highest first):
1. **`hint_to_candidates`** (conf 0.95) — from `Hints/track_hints.json`, the visual-hint workflow. Wins over all other sources via `_is_visual_hint` check in selectors.
2. **`find_cue_candidates`** (conf 0.55–1.00) — RB+librosa+PWV5 path; +0.25 MIK corroboration when a MIK cue is within the same 8-bar interval.
3. **`mik_to_candidates`** (conf 0.65–0.85) — synthesises bass_entry/outro_start/chop_point from MIK cues directly (used for tracks without RB phrase data).
4. **`amplitude_to_candidates`** (conf 0.70–0.85) — librosa amplitude envelope; produces bass_entry/break_start/outro_start when other signals miss.
5. Position fallback in mik_to_candidates if no signals corroborate.

Key types: `CueCandidate` (beat, sec, cue_type, confidence, sources, reasons, interval_index, region, penalty).
Key functions: `find_cue_candidates()`, `mik_to_candidates()`, `amplitude_to_candidates()`, `hint_to_candidates()`, `load_hints_file()`, `candidates_for()`, `first_credible()` (visual_hint wins), `first_drop_candidate()` (earliest credible bass_entry — dance-music structural prior).

### `Source/automated_dj_mixes/mik_reader.py`
Reads Mixed In Key 11 data — GEOB ID3 tags (cue points, beat grid, energy, key — base64-encoded JSON) plus SQLite enrichment (`MIKStore.db` for key, BPM, LUFS, key confidence, overall energy, per-segment energy timeline). `enrich_from_mik()` now copies key + BPM from DB back to `MikTrackData` (was missing — WAV files showed "?" for key). MIK's `MainKey` is stored in Camelot format (e.g. "8A"). Resilient: DB read failures don't lose tag-derived cues (Codex P2 fix).

Key types: `MikCue`, `MikBeatGrid`, `MikEnergySegment`, `MikTrackData`.
Key functions: `read_mik_from_tags()`, `read_mik_db_track()`, `read_mik_energy_segments()`, `enrich_from_mik()` (combined tag + DB read — copies key, bpm, lufs, key_confidence, energy).

### `Source/automated_dj_mixes/amplitude_analysis.py`
Pure-librosa structural detection from a 1-second RMS envelope. Used as a CANDIDATE SOURCE (not for snap-to-beat). Sam's "look at the picture broadly" rule, baked into numbers: detect the largest amplitude rise in the first 90s (bass_entry), the first significant drop after that (break_start), and the first big drop in the final 90s minus tail (outro_start). Plus a dead-air-free window finder for clean loop content.

Constants: `DROP_SEARCH_START_SEC=8` (skip "music starts" jump), `DROP_MIN_RISE=0.25`, `DROP_MIN_LEVEL_AFTER=0.65`, `OUTRO_TAIL_EXCLUDE_SEC=20` (skip fadeout), `MIK_SNAP_TOLERANCE_SEC=4`.
Key functions: `compute_envelope()`, `find_first_drop()`, `find_first_break()`, `find_outro_start()`, `find_clean_loop_window()`, `snap_to_mik_or_beat()`.

### `Source/automated_dj_mixes/waveform_preview.py`
Blank-canvas PNG render for the visual-hint authoring workflow. ZERO candidate picks — just waveform + RB phrases + MIK cues (numbered) + MIK energy strip + tiered phrase grid. The image to look at BEFORE writing hints to `track_hints.json`.

Key types: `PreviewContext`.
Key functions: `render_preview()`.

### `Source/automated_dj_mixes/report.py`
Debug reports. Per-track CSV (`Analysis - {track}.csv`) lists every interval's facts + candidate annotations. Per-mix Markdown (`Transition - Mix V{N}.md`) gives a "why this transition" rationale with selected cue, confidence, and reasons.

Key functions: `write_track_csv()`, `write_transition_report()`.
Output dir: `Test Project/May 2026 Mix/Reports/` and `{output_dir}/Reports/`.

### `Source/automated_dj_mixes/warping.py`
Warp marker calculation. Two modes: (1) 2-marker linear from BPM + downbeat (fallback), (2) per-beat grid from Rekordbox — one marker per downbeat using exact ms timestamps (165-252 markers per track, eliminates up to 13-beat drift). Now also the home of the **one-clock converter** that fixes the 2026-06-11 warp/cut regression: `grid_bpm_and_downbeat(beat_times_ms, first_downbeat_offset, db_bpm)` returns the effective constant BPM + true-downbeat anchor seconds; `sec_to_clip_beats(sec, beat_times_ms, first_downbeat_offset)` maps audio time → clip warp-beat coordinate via the same grid the warp markers use, so section cuts land on warped audio by construction. **5+ tests in Tests/test_one_clock.py.**

Key types: `WarpMarker` (beat_time, sample_time).
Key functions: `calculate_warp_markers()`, `calculate_warp_markers_from_beat_grid()`, `choose_warp_mode()`, `choose_dj_mix_warp_mode()` (nominal +/-1 BPM Re-Pitch with 0.05 BPM grid tolerance for the MixPlan proof path).

### `Source/automated_dj_mixes/automation.py`
Automation primitives + gain offset calc. Gain offsets: match to quietest (min LUFS), cap at max_reduction_db. Transition envelope generation now lives in `transition.py`.

Key types: `AutomationPoint`.
Key functions: `calculate_gain_offsets()`.

### `Source/automated_dj_mixes/als_generator.py`
Template-based ALS XML patching. Decompresses gzip, patches raw lines (not DOM — Ableton rejects reformatted XML), recompresses. Inserts: AudioClip XML (FileRef, WarpMarkers, Complex Pro mode), track names, utility gain, automation envelopes, project BPM. Supports multiple AudioClip elements per track (chop-and-duplicate loops) and per-clip colour/name overrides for visualization mode.

Key types: `TrackPatch` (analysis, track_index, warp_markers, gain_offset_db, arrangement_start_beats, loop_spec, phrase_segments).
Key functions: `generate_session()`, `decompress_als()`, `compress_als()`, `_build_audio_clip_xml()` (emits original + duplicates or per-phrase segments), `_build_single_clip_xml()`, `_find_filter_target_id()`, `_insert_audio_clip()`, `_insert_automation_envelopes()`.

### `Source/automated_dj_mixes/mix_plan.py`
Immutable, versioned N-track production intent. Schema 1.3 freezes exact per-track warp marker count, canonical marker-pair hash, encoded source-grid BPM, independent warp mode, source/section hashes, sequence, N-1 transition ownership, overlap policy, loop geometry, project BPM, policies, and canonical `plan_hash`.

Key types: `MixPlan`, `SourceContract`, `TrackInstanceContract`, `TransitionContract`, `LoopContract`. Key functions: `build_mix_plan()`, compatibility wrapper `build_one_transition_mix_plan()`, `validate_mix_plan()`, `write_mix_plan()`.

### `Source/validate_mix_plan_als.py`
Post-mutation reconciliation gate for N-track proofs. Verifies canonical plan hash, active main-track sequence, arrangement geometry, full and partial loop placement, fixed project tempo, absence of a tempo override, every track's explicit WarpMode, exact source warp grids, every bass-swap boundary, and automation on both sides of every transition. Paired-landmark swaps must be real clip boundaries on both tracks; outgoing loops may use any frozen repeat boundary. Writes a hash-backed reconciliation JSON and fails on any mismatch.

### `Source/automated_dj_mixes/warp_contract.py`
Canonical read-only ALS warp-grid fingerprinting. Summarises marker count, semantic marker-pair SHA-256, and effective source-grid BPM; rejects tracks whose clips do not share one grid.

### `Source/isolate_sections_tracks.py`
Builds a focused Sections proof without recreating target tracks. It empties non-target arrangement Events, verifies each retained AudioTrack block remains byte-identical, validates the output ALS, and can emit the matching sections JSON from that output.

### `Source/automated_dj_mixes/config.py`
Loads settings from `Config/settings.json` with sensible defaults (crossfade_bars=48, max_gain_reduction_db=12, default_project_tempo=128, versioning_prefix="V").

### `Source/automated_dj_mixes/desktop_analyzer.py`
**Added 2026-05-19, major rewrite 2026-05-21.** Drives Mixed In Key 11 and Rekordbox 7 desktop UIs to analyse tracks without manual clicks via `pywinauto` + Win32 API.

**Architecture — two Windows folder dialog types (auto-detected by `_select_folder_in_browse_dialog`):**

| Dialog type | Used by | Win32 API | Key child control | Strategy |
|-------------|---------|-----------|-------------------|----------|
| Old-style `SHBrowseForFolder` | MIK | `#32770` with `SysTreeView32` | TreeView (OK follows tree selection, ignores Edit text) | `_drive_old_style_browse_dialog()` — pywinauto `tree.get_item("\\Desktop\\_Pipeline_Import")` selects node, then `BM_CLICK` on OK |
| Modern `IFileDialog` (Vista+) | Rekordbox | `#32770` with `ComboBoxEx32`/`ToolbarWindow32` address bar | "Folder:" Edit field + "Select Folder" button | `_drive_modern_folder_dialog()` — set path in Edit via `SendMessage`, `Enter` to navigate in, `WM_COMMAND IDOK` to confirm |

**Staging folder pattern**: `Desktop/_Pipeline_Import/` — shallow path both dialog types can reach. Created BEFORE dialog opens (tree populates on open). Cleaned up in `finally` block after analysis completes.

**Focus-stealing bypass**: `_force_focus()` uses Alt-tap trick (`keybd_event(VK_MENU)`) before `SetForegroundWindow`. `AttachThreadInput` as belt-and-suspenders.

**RB launch**: Desktop shortcut `rekordbox 7.lnk` via `cmd /c start` (versioned subfolder changes with updates, direct exe path breaks). Retry logic: kill+relaunch on menu navigation failure.

**MIK DB**: `MIKStore.db` at `%LOCALAPPDATA%\Mixed In Key\Mixed In Key\11.0\MIKStore.db`. `is_mik_analyzed()` checks exact path, then filename fallback (`WHERE File LIKE '%filename.wav'`) for staging paths. Master-file gate (`_MASTER_PATTERN`) refuses non-master files.

Key functions: `analyze_folder_with_mik(folder)`, `analyze_folder_with_rekordbox(folder)`, `is_mik_analyzed(path)`, `is_rekordbox_analyzed(path)`, `_force_focus(window)`, `_select_folder_in_browse_dialog(folder)` (auto-detects dialog type → delegates), `_drive_old_style_browse_dialog()` (MIK TreeView), `_drive_modern_folder_dialog()` (RB IFileDialog), `_create_staging_folder()`, `_copy_mik_tags_to_originals()`.

Prerequisites: Rekordbox Library Protection OFF. Mouse clicks required for RB menu navigation — warn user before running.

### `Source/propose_arrangement.py`
**Added 2026-05-21; N-track MixPlan/playback gate 2026-07-16.** Arrangement orchestrator for the `/arrange-mix` skill. The active align-engine path recomputes final loop-adjusted geometry and rejects transitions outside 16-48 bars before ALS mutation. `--mix-plan PATH --project-bpm N --warp-mode auto` freezes exact grids plus per-track playback policy before the ALS writer runs. Reports preserve raw kick-dropout candidates without selecting them and remap original/repeated landmarks through final loop geometry. Supports `--hints` for `intro_skip_bars` and `loop_source_sec`; produces arranged ALS plus the arrangement report.

Key types: `TrackInfo` (sections + positions + camelot/bpm/energy/intro_skip_bars), `OverlapAnalysis` (per-pair overlap details + loop specs), `ArrangementPlan` (full plan container).
Key functions: `propose_arrangement()` (accepts `hints_path` and `mix_plan_path`), `validate_arrangement_plan()` (hard final-geometry gate), `analyse_overlap()` (loop planning + recomputation), `find_similar_pairs()`, `generate_report()`.

### `Source/apply_loops.py`
**Added 2026-05-21; hardened 2026-07-15.** Mechanical line-based clip cloning for loop extensions. `LoopSpec` is fail-closed at 8 repeats and 128 extension beats, rejects negative/non-finite geometry, and the entire batch preflights every track, Events block, template clip, and shift target before the first mutation. Post-write ALS validation is mandatory.

Key types: `LoopSpec` (track_name, source_beat_start/end, count, insert_at_beat, clip_name).
Key functions: `validate_loop_spec()`, `apply_loops()` (preflighted batch), `clone_clip()`, `decompress_als()` / `compress_als()`, `find_track_line_ranges()`, `shift_track_clips()`.

### `Source/apply_automation.py`
**Added 2026-05-21; contract fix 2026-07-16.** Volume crossfades (Utility Gain) + EQ bass kills (ChannelEQ LowShelfGain) applied to an arranged Sections .als. Three transition styles auto-selected by overlap length: **STANDARD** (24-36 bars, existing two-phase model), **LONG_BLEND** (>36 bars, linear crossfade, partial EQ, delayed bass swap by 32 beats), **QUICK_SWAP** (<24 bars, instant swap, no sneak, full EQ kill). Explicit arrangement-report swaps are preserved at valid overlap-start/loop boundaries; only the overlap end carries the fade-room guard. This keeps automation identical to the frozen report and MixPlan reconciliation.

Key types: `TransitionStyle` (enum: STANDARD/LONG_BLEND/QUICK_SWAP), `TrackInfo`, `TransitionPlan` (with style, two_stage_bass, low_sneak flags).
Key functions: `find_bass_swap()` (priority-ordered swap point selection), `plan_transitions()` (style selection + rule application), `build_track_automation()` (style-specific envelope point generation), `insert_envelopes()` (ALS patching).

### `Source/learn_from_correction.py`
Automated diff tool for PROPOSE-LEARN cycle. Extracts automation envelopes from two ALS files, scopes comparison to each transition's overlap zone, detects bass_swap_moved / two_stage_bass / sneak_changed patterns, **classifies which TransitionStyle Sam's corrections most closely match** (standard/long_blend/quick_swap), appends to pair_history.jsonl with `classified_style` field.

Key types: `TrackAutomation`, `ParamDiff`, `TransitionDiff` (with `classified_style`).
Key functions: `extract_track_automation()`, `analyse_transitions()`, `_classify_style()` (sneak level + bass kill depth + instant swap detection), `diff_to_jsonl_entry()`, `print_report()`, `main()`.

### `Source/stem_detector.py`
**2026-07-16 update:** model mode uses smoothed V3 presence for coarse sections/cues and raw V3 presence for `signals.musical_landmarks`; dedicated dropout spans no longer disappear when short gaps are bridged for section stability. DETECT images show the raw pre-drop/dropout strip. Default OFF and bass/vocal/loop/fill behavior remain unchanged. Orchestrator model use requires `--sections-layout --stem-sections --kick-model`.

**Added 2026-06-08.** Stem-based section detector (the new section source — Demucs stems, ANALYSIS-ONLY, original WAV untouched, envelopes cached as `.npz`). `detect(wav, project, bpm=, downbeat=, make_viz=, write_json=)` → `{track, bpm, n_bars, sections, signals}`; `--write-hints` auto-generates the 4 production-gate hints; renders `DETECT_*.png` (track + 4 stems, labelled sections + bar counts + bass-IN/OUT markers + kick cues). Calibration rules + signals in memory `reference-stem-section-detector`. Wired into the orchestrator via `--stem-sections`.

### `Source/kick_model_adapter.py`
**Added 2026-07-09; dual readout 2026-07-16.** Lazy adapter for the sibling Kick Detector project. Loads `Kick Detector/Models/kick_crnn_V3.pt` and Kick Detector's reference `model.py` / `presence_postprocess.py` only when `--kick-model` is enabled. One inference now returns `KickPresenceReadout(raw, section)`: raw beat presence feeds contextual musical landmarks, while the validated threshold/smoothing (`0.30`, `fill_off_beats=6`, `drop_on_beats=1`) remains the coarse-section signal. The single Demucs pass still yields normal stem envelopes plus raw drums without double separation.

### `Source/automated_dj_mixes/musical_landmarks.py`
Extracts two-beat-or-longer raw Kick Detector V3 dropout spans, classifies short gaps immediately before drops, attaches section/energy context and candidate roles, and deliberately makes no arrangement selection.

### `Source/extract_musical_landmarks.py`
Safe standalone landmark refresh for certified stem JSONs. Hashes section geometry before/after persistence, refuses any section mutation, runs one V3 inference per track, and writes dedicated `LANDMARKS_*.png` views.

### `Source/align_engine.py`
**Added 2026-06-08; paired-landmark V2 2026-07-16.** Bass-to-bass alignment engine used by `propose_arrangement`. `paired_landmarks_v2` preserves odd-bar cues, requires paired incoming/outgoing landmarks, suppresses arbitrary incoming-intro loops, and can extend to a named cue up to 64 bars. Cue-bounded tail loops select a clean phrase length that preserves an intermediate swap boundary as well as the final target. Legacy selection retains the 16-48 bar safety gate. Reads `SECTIONS_STEM_*.json` and retains the transition visualizer.

### Diagnostic / Research Scripts

- `Source/analyze_real_mix.py` — Decompresses a real Sam DJ mix `.als` and lists tracks/clips. Used 2026-05-19 to learn transition patterns from Bargrooves Summer 2015 Mix 1.
- `Source/inspect_transition.py` — Renders ONE transition as a clip-position timeline image. CLI: `python inspect_transition.py <out_substr> <in_substr> <label>`.
- `Source/test_mik_driver.py` / `Source/test_rb_driver.py` — Smoke tests for `desktop_analyzer.py`.
- `Source/automated_dj_mixes/diag_vlad.py` — Prints VLAD's full Rekordbox phrase + fill data
- `Source/validate_pwv5.py` — Renders PWV5 waveform PNGs side-by-side to compare against Rekordbox UI
- `Source/test_features.py` — Smoke test for `extract_track_features()` on one track
- `Source/diagnose_rekordbox.py` — (legacy) Rekordbox phrase map vs pipeline fields side-by-side
- `Source/analyze_phrase_patterns.py` — (legacy) Structural patterns across all RB-analyzed tracks

- `Source/transition_review_viz.py` - Renders zoom + full-context evidence for every transition. Since 2026-07-16, waveform sampling maps every arrangement point through the actual clip's source range, so repeated intro/tail loops display their real audio instead of false silence. Includes color-55 `beat_dropout` bands and frozen swap/landmark overlays.
- `Source/materialize_section_details.py` - Converts stable coarse sections plus every raw Kick V3 gap up to 16 beats into a separate review ALS/JSON with color-55 `beat_dropout` clips. Proves source warp-grid summaries are unchanged before accepting output.

### Data files

- `Data/Ground Truth/Sam Cue Points.yaml` — Sam-validated cue beats per problem track. Used for threshold tuning + regression testing. Currently 5 tracks × 4 cues (most still null pending Sam's review)

## Dependencies

| Package | Purpose |
|---------|---------|
| librosa | Transient/downbeat detection, energy analysis (fallback) |
| pyloudnorm | LUFS measurement |
| mutagen | Reading ID3/Vorbis tags |
| pyrekordbox | Reading Rekordbox ANLZ files (beat grids, key data) — PSSI/PWV5 parsed manually |
| matplotlib | PWV5 visual validation renders |
| numpy | Percentile stats + smoothing in `features.py` |
| ffmpeg-python | Audio format handling |
| pywinauto | Desktop UI automation (MIK + RB) via Windows messages |
| pyautogui | Mouse/keyboard fallback for non-message-responsive controls |
| pyperclip | Clipboard support for `desktop_analyzer.py` path pasting |
