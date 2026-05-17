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

Key types: `CueCandidate` (beat, sec, cue_type, confidence, sources, reasons, interval_index, region, penalty).
Key functions: `find_cue_candidates()`, `candidates_for()` (ranked list), `first_credible()` (top match with min_confidence floor).

### `Source/automated_dj_mixes/report.py`
Debug reports. Per-track CSV (`Analysis - {track}.csv`) lists every interval's facts + candidate annotations. Per-mix Markdown (`Transition - Mix V{N}.md`) gives a "why this transition" rationale with selected cue, confidence, and reasons.

Key functions: `write_track_csv()`, `write_transition_report()`.
Output dir: `Test Project/May 2026 Mix/Reports/` and `{output_dir}/Reports/`.

### `Source/automated_dj_mixes/transition.py`
Bass-to-bass transition planner. Accepts ranked `CueCandidate` lists and prefers them over RB-phrase fallbacks (chop_point > outro_start for outgoing; bass_entry for incoming bass swap; break_start for transition end). Chop-and-duplicate loop generation. Volume holds at 1.0 until bass_swap then fades to 0 by transition_end. EQ bass hard-swap at bass_swap (0.18 ≈ -15dB, 1.0 = unity).

Key types: `LoopSpec`, `TransitionSpec`.
Key functions: `plan_transition()` (main entry), fallback finders for outgoing_bass_end / chop_point / incoming_bass_start / incoming_first_break.

### `Source/automated_dj_mixes/validation.py`
Objective pass/fail checks on the planned mix. Validates from the internal `TransitionSpec` list — NOT by reparsing the generated ALS. Five checks: overlap range (16-48 bars), bass-swap grid alignment (8/16-bar boundary), outgoing faded out, no dead air before incoming, EQ envelopes present.

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

### Diagnostic Scripts

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
