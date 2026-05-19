# Toolbox — Automated DJ Mixes

Module reference for all pipeline components.

## Modules

### `Source/automated_dj_mixes/orchestrator.py`
Main pipeline controller. Wires analysis → Rekordbox enrichment → sequencing → gain offsets → warping → per-track features (cached) → cue candidates → candidate-driven transition planning → ALS generation → objective validation + transition report. CLI: `python -m automated_dj_mixes.orchestrator --input "Tracks/" --output "Output/"`. Visualize mode: `--visualize` produces colour-coded section ALS + per-track CSV reports.

Key functions: `run_pipeline()` (full pipeline + visualize branch), `_find_template()`, `_next_version()`, `main()` (CLI).

### `Source/automated_dj_mixes/analysis.py`
Reads key/BPM from file tags (mutagen ID3/Vorbis). Transient/downbeat detection (librosa). LUFS measurement (pyloudnorm). Bass section detection (off-beat energy sampling). Phrase-aware break detection. Rekordbox enrichment maps RB phrases → pipeline fields (bass_start/end, break_start/end, intro_end, last_kick).

Key types: `TrackAnalysis` (dataclass with path, key, camelot, bpm, lufs, first_downbeat_sec, duration_sec, sample_rate, bass_start_sec, bass_end_sec, first_break_start_sec, first_break_end_sec, intro_end_sec, last_kick_sec, rekordbox_phrases, analysis_source, warnings).
Key functions: `analyse_track()`, `analyse_folder()`, `enrich_from_rekordbox()`, `_detect_downbeat()`, `_detect_bass_section()`, `_detect_first_break_phrase_aware()`.

### `Source/automated_dj_mixes/sequencer.py`
Full Camelot wheel mapping (24 keys + common aliases like "Am", "Bbm", "F#"). Compatibility scoring: 4=identical, 3=smooth/relative, 2=power, 1=diagonal, 0=clash. Greedy nearest-neighbour harmonic path. **20 tests.**

Key functions: `key_to_camelot()`, `compatibility_score()`, `is_compatible()`, `build_harmonic_path()`.

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
Reads Mixed In Key 11 data — GEOB ID3 tags (cue points, beat grid, energy, key — base64-encoded JSON) plus optional SQLite enrichment (`MIKStore.db` for LUFS, key confidence, per-segment energy timeline). Resilient: DB read failures don't lose tag-derived cues (Codex P2 fix).

Key types: `MikCue`, `MikBeatGrid`, `MikEnergySegment`, `MikTrackData`.
Key functions: `read_mik_from_tags()`, `read_mik_db_track()`, `read_mik_energy_segments()`, `enrich_from_mik()` (combined tag + DB read).

### `Source/automated_dj_mixes/amplitude_analysis.py`
Pure-librosa structural detection from a 1-second RMS envelope. Used as a CANDIDATE SOURCE (not for snap-to-beat). Sam's "look at the picture broadly" rule, baked into numbers: detect the largest amplitude rise in the first 90s (bass_entry), the first significant drop after that (break_start), and the first big drop in the final 90s minus tail (outro_start). Plus a dead-air-free window finder for clean loop content.

Constants: `DROP_SEARCH_START_SEC=8` (skip "music starts" jump), `DROP_MIN_RISE=0.25`, `DROP_MIN_LEVEL_AFTER=0.65`, `OUTRO_TAIL_EXCLUDE_SEC=20` (skip fadeout), `MIK_SNAP_TOLERANCE_SEC=4`.
Key functions: `compute_envelope()`, `find_first_drop()`, `find_first_break()`, `find_outro_start()`, `find_clean_loop_window()`, `snap_to_mik_or_beat()`.

### `Source/automated_dj_mixes/transition_viz.py`
Per-transition PNG render. Shows last 32 bars of outgoing + first 32 bars of incoming, time-aligned. Overlays: volume + EQ-bass curves for both tracks, dashed bass_swap line spanning all panels, green hatched loop region on outgoing, MIK cues (pink dotted), picked candidates (bold colour-coded). Tiered phrase grid with bar labels (Sam's rule, 2026-05).

Key types: `VizContext`.
Key functions: `render_transition()`, `_draw_grid_ticks()` (tiered styling: 1-bar / 2-bar / 4-bar / 16-bar weighted alphas + labels).

### `Source/automated_dj_mixes/track_viz.py`
Per-track PNG render. Full timeline of one source. Overlays: RB phrase strip at top, waveform, MIK cues, MIK energy strip (colour heatmap 1-10), picked candidates (uses `first_drop_candidate` + `first_credible` so the viz matches what `plan_transition` actually used), loop region green hatched, volume + EQ automation lanes.

Key types: `TrackVizContext`.
Key functions: `render_track()`, `_draw_phrase_grid()`, `_draw_phrase_bands()`, `_draw_energy_strip()`, `_draw_candidates()`.

### `Source/automated_dj_mixes/waveform_preview.py`
Blank-canvas PNG render for the visual-hint authoring workflow. ZERO candidate picks — just waveform + RB phrases + MIK cues (numbered) + MIK energy strip + tiered phrase grid. The image to look at BEFORE writing hints to `track_hints.json`.

Key types: `PreviewContext`.
Key functions: `render_preview()`.

### `Source/automated_dj_mixes/report.py`
Debug reports. Per-track CSV (`Analysis - {track}.csv`) lists every interval's facts + candidate annotations. Per-mix Markdown (`Transition - Mix V{N}.md`) gives a "why this transition" rationale with selected cue, confidence, and reasons.

Key functions: `write_track_csv()`, `write_transition_report()`.
Output dir: `Test Project/May 2026 Mix/Reports/` and `{output_dir}/Reports/`.

### `Source/automated_dj_mixes/transition.py`
Two-phase transition planner with per-track phrase-grid snapping. Phase 1 (transition_start → bass_swap): incoming volume ramps from 0.2 → 1.0, outgoing holds at unity. Phase 2 (bass_swap → transition_end): hard EQ bass swap (0.18 ≈ -15dB / 1.0 = unity), outgoing fades to 0 by transition_end (lands on incoming's first break). Chop-and-duplicate loop fills the post-chop gap; loop sources from outgoing's outro (past chop) or intro (fallback) via `find_loop_region`. Loop selection has dead-air refinement (`amplitude_analysis.find_clean_loop_window`).

Key types: `LoopSpec`, `TransitionSpec`, `PhraseGrid` (origin-aware tiered 16/8/4 snap).
Key functions: `plan_transition()` (main entry), `snap()` (whole-beat), `find_loop_region()` (intro/outro priority with `role` parameter), fallback finders for outgoing_bass_end / chop_point / incoming_bass_start / incoming_first_break.

Hard invariant: `outgoing_arrangement_start % 4 == 0` (raises if violated — chop_at would misalign on source). Per-track grids enforce that each track's phrase boundaries are respected: incoming snaps to outgoing's grid; bass_swap snaps to incoming's grid.

### `Source/automated_dj_mixes/validation.py`
Objective pass/fail checks on the planned mix. Validates from the internal `TransitionSpec` list — NOT by reparsing the generated ALS. Checks:
1. Overlap range (16-48 bars, 1.5-bar tolerance for phrase-snap drift).
2. **Per-track bar alignment (HARD)**: bass_swap on incoming's bar grid, transition_start on outgoing's bar grid, transition_end on incoming's bar grid. Off-bar fails the run.
3. Per-track 4-bar phrase alignment (warning only — informational).
4. Outgoing faded to 0 by transition_end.
5. No dead air before incoming.
6. EQ envelopes present.

Key types: `ValidationCheck`, `ValidationReport`.
Key functions: `validate_mix()`.

### `Source/automated_dj_mixes/warping.py`
Warp marker calculation. Two modes: (1) 2-marker linear from BPM + downbeat (fallback), (2) per-beat grid from Rekordbox — one marker per downbeat using exact ms timestamps (165-252 markers per track, eliminates up to 13-beat drift). **5 tests.**

Key types: `WarpMarker` (beat_time, sample_time).
Key functions: `calculate_warp_markers()`, `calculate_warp_markers_from_beat_grid()`, `choose_warp_mode()`.

### `Source/automated_dj_mixes/automation.py`
Automation primitives + gain offset calc. Gain offsets: match to quietest (min LUFS), cap at max_reduction_db. Transition envelope generation now lives in `transition.py`.

Key types: `AutomationPoint`.
Key functions: `calculate_gain_offsets()`.

### `Source/automated_dj_mixes/als_generator.py`
Template-based ALS XML patching. Decompresses gzip, patches raw lines (not DOM — Ableton rejects reformatted XML), recompresses. Inserts: AudioClip XML (FileRef, WarpMarkers, Complex Pro mode), track names, utility gain, automation envelopes, project BPM. Supports multiple AudioClip elements per track (chop-and-duplicate loops) and per-clip colour/name overrides for visualization mode.

Key types: `TrackPatch` (analysis, track_index, warp_markers, gain_offset_db, arrangement_start_beats, loop_spec, phrase_segments).
Key functions: `generate_session()`, `decompress_als()`, `compress_als()`, `_build_audio_clip_xml()` (emits original + duplicates or per-phrase segments), `_build_single_clip_xml()`, `_find_filter_target_id()`, `_insert_audio_clip()`, `_insert_automation_envelopes()`.

### `Source/automated_dj_mixes/config.py`
Loads settings from `Config/settings.json` with sensible defaults (crossfade_bars=48, max_gain_reduction_db=12, default_project_tempo=128, versioning_prefix="V").

### `Source/automated_dj_mixes/desktop_analyzer.py`
**Added 2026-05-19.** Drives Mixed In Key 11 and Rekordbox 7 desktop UIs to analyse tracks without manual clicks. Uses `pywinauto` for message-based control where possible (cursor doesn't move) and `pyautogui` for the one custom WPF control (MIK Add tracks button — image template match in `templates/`). All clicks save+restore cursor position so Sam can keep working in Ableton. MIK detection via `MIKStore.db` SQLite (`Song` table). RB detection via `pyrekordbox`. Requires Rekordbox Library Protection OFF.

Key functions: `analyze_folder_with_mik(folder)`, `analyze_folder_with_rekordbox(folder)`, `is_mik_analyzed(path)`, `is_rekordbox_analyzed(path)`, `_force_focus(window)` (AttachThreadInput), `_select_folder_in_browse_dialog(folder)`.

Wired into orchestrator pre-analysis (before `analyse_folder`).

### Diagnostic / Research Scripts

- `Source/analyze_real_mix.py` — Decompresses a real Sam DJ mix `.als` and lists tracks/clips. Used 2026-05-19 to learn transition patterns from Bargrooves Summer 2015 Mix 1.
- `Source/inspect_transition.py` — Renders ONE transition as a clip-position timeline image. CLI: `python inspect_transition.py <out_substr> <in_substr> <label>`.
- `Source/test_mik_driver.py` / `Source/test_rb_driver.py` — Smoke tests for `desktop_analyzer.py`.
- `Source/automated_dj_mixes/diag_vlad.py` — Prints VLAD's full Rekordbox phrase + fill data
- `Source/validate_pwv5.py` — Renders PWV5 waveform PNGs side-by-side to compare against Rekordbox UI
- `Source/test_features.py` — Smoke test for `extract_track_features()` on one track
- `Source/diagnose_rekordbox.py` — (legacy) Rekordbox phrase map vs pipeline fields side-by-side
- `Source/analyze_phrase_patterns.py` — (legacy) Structural patterns across all RB-analyzed tracks

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
