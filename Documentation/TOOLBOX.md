# Toolbox — Automated DJ Mixes

Module reference for all pipeline components.

## Modules

### `Source/validate_beatgrid.py` (2026-06-11, v2 same day; RB-library CLI removed 2026-08-20)
Hard-stop gate: does each track's beat grid sit ON its audio? Whole-track kick onsets (150Hz lowpass — not mel fmax, which produces empty filters), half-beat-circle phase concentration (R) folds house offbeat-bass stabs so locked grids read high regardless of bassline; mean full-circle phase catches grids whose tempo is right but markers sit between the kicks (the Todd case). Per-track +1% detuned twin acts as a known-bad control. Calibrated on 22 tracks (08.06.26 + 09.06.26) + 12 more (11.06.26). Wired into `--sections-layout`; `--allow-bad-grids` to override.

**v2 — MIK tiebreaker (11.06.26 run):** percussion-heavy genres (Latin house, gospel stabs) smear R below the absolute thresholds even on correct grids. `check_grid(..., independent_bpm, db_bpm)` + `verdict_from(..., tempo_confirmed)`: a track is rescued from the ambiguous band only when R≥0.20, ≥5× its detuned control, the grid is internally consistent (span vs RB DB ≤0.5%) AND MIK agrees with the grid span ≤0.2% AND the phase is clean. Never rescues noise-floor grids; never overrides a bad phase.

**Grid overrides** (`<project>/Hints/grid_overrides.json`, applied by the orchestrator before enrichment so warp/cuts/gate all see the corrected grid):
- `shift_ms` — phase slide (the Todd fix). Written by CLI `--write-override <substr>` (measures, composes with existing shifts).
- `replace_grid` — full constant-grid synthesis for unusable grids (first case: La Trumpter — internally inconsistent RB grid, true 126 BPM confirmed by MIK + Sam). `_fit_anchor` kick-fits the anchor (bar-phase inherited from the old grid's downbeat); `write_grid_replacement(project, wav, rb, true_bpm)` PROVES the fit with the gate before writing — a failing fit is refused.

Library: `check_grid`, `enforce_beatgrid_quality`, `load_grid_overrides`, `apply_grid_override`, `write_grid_replacement`, `_fit_anchor`, `verdict_from` (pure). The Rekordbox-library CLI mode (`--write-override` against the RB library, incl. `write_phase_override`) was removed with the Rekordbox removal (2026-08-20); running the script now prints a retirement message. The live gate functions are unchanged.


### `Source/automated_dj_mixes/orchestrator.py`
Main pipeline controller. The canonical `/mix` path uses `--stem-grid --stem-sections --kick-model`: owned per-beat grids, Demucs structure and Kick Detector V3 evidence. The orchestrator runs MIK only for optional key/energy metadata. **Rekordbox was removed entirely (2026-08-20, Sam 2026-08-18: "done away with")**: no desktop driving, no library reads, no phrase enrichment; `enforce_owned_grid_coverage` is the coverage gate for every mode (owned stem grids or `.asd` tick-fit shells; no fallback). `--allow-partial-rekordbox` is accepted-but-ignored with a deprecation warning so documented invocations don't break. CLI: `python -m automated_dj_mixes.orchestrator --input "Tracks/" --output "Output/"`.

Key functions: `run_pipeline()`, `enforce_owned_grid_coverage()`, `_find_template()`, `_next_version()`, `main()` (CLI).

### `Source/automated_dj_mixes/grid_carrier.py`
**Added 2026-08-20 (Rekordbox removal).** `TrackGrid` — the one per-track beat-grid carrier every grid consumer reads (warp markers, one-clock section cuts, beatgrid gate, grid overrides). Field-compatible with the old `RekordboxAnalysis` (file_path, title, bpm, key_name, mood, end_beat, phrases (always empty), beat_times_ms, first_downbeat_offset, ext_path). Populated by the owned stem-grid detector and `.asd` tick-fit shells.

### `Source/automated_dj_mixes/analysis.py`
SHIM over the shared `audio_analysis` package. Reads key/BPM from file tags (mutagen ID3/Vorbis). Transient/downbeat detection (librosa). LUFS measurement (pyloudnorm). Bass section detection (off-beat energy sampling). Phrase-aware break detection. (`enrich_from_rekordbox` was deleted in the Rekordbox removal, 2026-08-20.)

Key types: `TrackAnalysis` (dataclass with path, key, camelot, bpm, lufs, first_downbeat_sec, duration_sec, sample_rate, bass_start_sec, bass_end_sec, first_break_start_sec, first_break_end_sec, intro_end_sec, last_kick_sec, analysis_source, warnings).
Key functions: `analyse_track()`, `analyse_folder()`, `_detect_downbeat()`, `_detect_bass_section()`, `_detect_first_break_phrase_aware()`.

### `Source/automated_dj_mixes/sequencer.py`
Full Camelot wheel mapping (24 keys + common aliases like "Am", "Bbm", "F#"). Compatibility scoring: 4=identical, 3=smooth/relative, 2=power, 1=diagonal, 0=clash. Greedy nearest-neighbour harmonic path with **composite scoring**: `(camelot_norm * 0.6) + (bpm_norm * 0.4)`, both normalized to 0-1. **Energy arc post-pass**: `apply_energy_arc()` divides tracks into build/peak/cooldown thirds, sorts by MIK OverallEnergy (0-10), with BPM-gap guard (rejects reorder if 15+ BPM gap). **20 tests.**

Key functions: `key_to_camelot()`, `compatibility_score()`, `is_compatible()`, `build_harmonic_path()`, `apply_energy_arc()`, `_bpm_proximity()`.

### Archived 2026-08-20 — the Rekordbox removal (Sam 2026-08-18: "done away with")
Moved to `Source/Archive/` (history preserved via `git mv`): `rekordbox_reader.py` (ANLZ `.DAT`/`.EXT` + PSSI parser, library matcher), `rekordbox_waveform.py` (PWV5/PWV4 waveform parser), `features.py` (per-beat RB-waveform feature extraction + disk cache), `report.py` (interval CSV / transition Markdown reports), `test_rb_driver.py` (RB desktop-driver smoke test), `regress_section_detection.py` (golden-sections regression harness for the retired RB-phrase mode; superseded by the property-based kick-cues test per the 2026-08-18 review item #2). The grid-carrier dataclass survives as `grid_carrier.TrackGrid`. Also deleted outright (not archived): `analysis.enrich_from_rekordbox`, `orchestrator.enforce_rekordbox_coverage` + its RB-enrichment loop, `desktop_analyzer`'s entire RB driving/launch/agent-health half, `phrase_viz`'s Interval/refinement layer, `cue_candidates.find_cue_candidates`/`first_drop_candidate`, and `validate_beatgrid`'s RB-library CLI.

### `Source/automated_dj_mixes/phrase_viz.py`
Section clips for Ableton display. `PhraseSegment` is the colour-coded clip record (intro green / build cyan / break blue / drop yellow / fill orange / outro red / beat_dropout purple); `segments_from_stem_sections()` is the ONLY producer — maps `stem_detector.detect()` sections onto warp-beat coordinates through the beat grid (one-clock rule) and bar-snaps with contiguity/zero-length guards. `validate_bar_math()` flags chops off the 4-bar grid. (The RB interval/refinement layer was deleted 2026-08-20.)

Key types: `PhraseSegment`. Key functions: `segments_from_stem_sections()`, `validate_bar_math()`; `LABEL_TO_COLOR` map.

### `Source/automated_dj_mixes/cue_candidates.py`
Cue candidate records + MIK/amplitude/visual-hint candidate synthesis. Emits ranked `CueCandidate` records with confidence (0-1) + sources list + human-readable reasons. Five cue types: `bass_entry`, `break_start`, `break_end`, `chop_point`, `outro_start`. The RB interval-based detector (`find_cue_candidates`, `first_drop_candidate`) was deleted 2026-08-20; `ANALYSIS_MODEL_VERSION` ("cue-candidates-v1") now lives here.

Candidate sources (selection precedence highest first):
1. **`hint_to_candidates`** (conf 0.95) — from `Hints/track_hints.json`, the visual-hint workflow. Wins over all other sources via `_is_visual_hint` check in selectors.
2. **`mik_to_candidates`** (conf 0.65–0.85) — synthesises bass_entry/outro_start/chop_point from MIK cues directly.
3. **`amplitude_to_candidates`** (conf 0.70–0.85) — librosa amplitude envelope; produces bass_entry/break_start/outro_start when other signals miss.
4. Position fallback in mik_to_candidates if no signals corroborate.

Key types: `CueCandidate` (beat, sec, cue_type, confidence, sources, reasons, interval_index, region, penalty).
Key functions: `mik_to_candidates()`, `amplitude_to_candidates()`, `hint_to_candidates()`, `load_hints_file()`, `candidates_for()`, `first_credible()` (visual_hint wins).

### `Source/automated_dj_mixes/mik_reader.py`
Reads Mixed In Key 11 data — GEOB ID3 tags (cue points, beat grid, energy, key — base64-encoded JSON) plus SQLite enrichment (`MIKStore.db` for key, BPM, LUFS, key confidence, overall energy, per-segment energy timeline). `enrich_from_mik()` now copies key + BPM from DB back to `MikTrackData` (was missing — WAV files showed "?" for key). MIK's `MainKey` is stored in Camelot format (e.g. "8A"). Resilient: DB read failures don't lose tag-derived cues (Codex P2 fix).

Key types: `MikCue`, `MikBeatGrid`, `MikEnergySegment`, `MikTrackData`.
Key functions: `read_mik_from_tags()`, `read_mik_db_track()`, `read_mik_energy_segments()`, `enrich_from_mik()` (combined tag + DB read — copies key, bpm, lufs, key_confidence, energy).

### `Source/automated_dj_mixes/amplitude_analysis.py`
Pure-librosa structural detection from a 1-second RMS envelope. Used as a CANDIDATE SOURCE (not for snap-to-beat). Sam's "look at the picture broadly" rule, baked into numbers: detect the largest amplitude rise in the first 90s (bass_entry), the first significant drop after that (break_start), and the first big drop in the final 90s minus tail (outro_start). Plus a dead-air-free window finder for clean loop content.

Constants: `DROP_SEARCH_START_SEC=8` (skip "music starts" jump), `DROP_MIN_RISE=0.25`, `DROP_MIN_LEVEL_AFTER=0.65`, `OUTRO_TAIL_EXCLUDE_SEC=20` (skip fadeout), `MIK_SNAP_TOLERANCE_SEC=4`.
Key functions: `compute_envelope()`, `find_first_drop()`, `find_first_break()`, `find_outro_start()`, `find_clean_loop_window()`, `snap_to_mik_or_beat()`.

### `Source/automated_dj_mixes/waveform_preview.py`
Blank-canvas PNG render for the visual-hint authoring workflow. ZERO candidate picks — just waveform + MIK cues (numbered) + MIK energy strip + tiered phrase grid (the RB phrase strip was removed 2026-08-20). The image to look at BEFORE writing hints to `track_hints.json`.

Key types: `PreviewContext`.
Key functions: `render_preview()`.

### `Source/automated_dj_mixes/warping.py`
Warp marker calculation. Two modes: (1) 2-marker linear from BPM + downbeat (fallback), (2) per-beat grid (`beat_times_ms` from the owned stem grid / tick fits) — one marker per downbeat using exact ms timestamps (165-252 markers per track, eliminates up to 13-beat drift). Now also the home of the **one-clock converter** that fixes the 2026-06-11 warp/cut regression: `grid_bpm_and_downbeat(beat_times_ms, first_downbeat_offset, db_bpm)` returns the effective constant BPM + true-downbeat anchor seconds; `sec_to_clip_beats(sec, beat_times_ms, first_downbeat_offset)` maps audio time → clip warp-beat coordinate via the same grid the warp markers use, so section cuts land on warped audio by construction. **5+ tests in Tests/test_one_clock.py.**

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
**Added 2026-05-19, major rewrite 2026-05-21; Rekordbox half deleted 2026-08-20.** Drives the Mixed In Key 11 desktop UI to analyse tracks without manual clicks via `pywinauto` + Win32 API. (The entire RB driving/launch/agent-health machinery — `analyze_folder_with_rekordbox`, `is_rekordbox_analyzed`, `RekordboxAgentError`, launch/kill/agent-reset helpers, menu navigation — was removed; see Source/Archive/ and git history.)

**Architecture — two Windows folder dialog types (auto-detected by `_select_folder_in_browse_dialog`):**

| Dialog type | Win32 API | Key child control | Strategy |
|-------------|-----------|-------------------|----------|
| Old-style `SHBrowseForFolder` (MIK's usual) | `#32770` with `SysTreeView32` | TreeView (OK follows tree selection, ignores Edit text) | `_drive_old_style_browse_dialog()` — pywinauto `tree.get_item("\\Desktop\\_Pipeline_Import")` selects node, then `BM_CLICK` on OK |
| Modern `IFileDialog` (Vista+) | `#32770` with `ComboBoxEx32`/`ToolbarWindow32` address bar | "Folder:" Edit field + "Select Folder" button | `_drive_modern_folder_dialog()` — set path in Edit via `SendMessage`, `Enter` to navigate in, `WM_COMMAND IDOK` to confirm. **Kept** in the removal: reachable from the MIK path via `_select_folder_in_browse_dialog`'s auto-detect (`analyze_folder_with_mik` → :679) |

**Staging folder pattern**: `Desktop/_Pipeline_Import/` — shallow path both dialog types can reach. Created BEFORE dialog opens (tree populates on open). Cleaned up in `finally` block after analysis completes.

**Focus-stealing bypass**: `_force_focus()` uses Alt-tap trick (`keybd_event(VK_MENU)`) before `SetForegroundWindow`. `AttachThreadInput` as belt-and-suspenders.

**MIK DB**: `MIKStore.db` at `%LOCALAPPDATA%\Mixed In Key\Mixed In Key\11.0\MIKStore.db`. `is_mik_analyzed()` checks exact path, then filename fallback (`WHERE File LIKE '%filename.wav'`) for staging paths. Master-file gate (`_MASTER_PATTERN`) refuses non-master files.

Key functions: `analyze_folder_with_mik(folder)`, `is_mik_analyzed(path)`, `_force_focus(window)`, `_select_folder_in_browse_dialog(folder)` (auto-detects dialog type → delegates), `_drive_old_style_browse_dialog()` (TreeView), `_drive_modern_folder_dialog()` (IFileDialog), `_create_staging_folder()`, `_copy_mik_tags_to_originals()`.

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

### `Source/analyze_correction_diff.py`
**Added 2026-07-16.** Read-only, source-aware comparison for a generated full-mix ALS and Sam's manually corrected copy. Reconstructs actual clip geometry and source ranges, proves warp-grid/mode preservation, remaps stale corrected clip names through the baseline section map, detects repeated source phrases, rebuilds each corrected overlap, and compares entry/swap/exit cues plus Utility/bass automation. Writes a machine-readable `correction_diff_v1` JSON. Use this before `learn_from_correction.py` whenever arrangement positions or loops changed; the older learner assumes one fixed arrangement and otherwise confuses movement with automation correction.

Key functions: `load_snapshot()`, `analyse()`, `_transition_snapshot()`, `_repeat_groups()`, `_source_at()`.

### `Source/stem_detector.py`
**2026-08-19 update (Claude):** two additions, both corpus-validated. (1) `_energy_cues()` — sustained low-`mix_norm` runs cut section boundaries the kick/bass paths miss (`MIX_ENERGY_BREAK_FRAC=0.40`, `MIN_ENERGY_RUN_BARS=12`; default-ON, 20-track sweep: 1 correct new cut, 0 false positives; closes the Double Dutch attenuated-kick break). `ef` also joins `_assign_labels`'s break/fill trigger. (2) Sam's soft rules behind a default-OFF flag — R2 kick-less head→intro, R3 kick-less tail→outro, R4 kick-drop cascade (`Tests/test_section_soft_rules.py`; R4 proven, R2 unproven pending a discriminating test).

**2026-07-16 update:** model mode uses smoothed V3 presence for coarse sections/cues and raw V3 presence for `signals.musical_landmarks`; dedicated dropout spans no longer disappear when short gaps are bridged for section stability. DETECT images show the raw pre-drop/dropout strip. Default OFF and bass/vocal/loop/fill behavior remain unchanged. Orchestrator model use requires `--sections-layout --stem-sections --kick-model`.

**Added 2026-06-08.** Stem-based section detector (the new section source — Demucs stems, ANALYSIS-ONLY, original WAV untouched, envelopes cached as `.npz`). `detect(wav, project, bpm=, downbeat=, make_viz=, write_json=)` → `{track, bpm, n_bars, sections, signals}`; `--write-hints` auto-generates the 4 production-gate hints; renders `DETECT_*.png` (track + 4 stems, labelled sections + bar counts + bass-IN/OUT markers + kick cues). Calibration rules + signals in memory `reference-stem-section-detector`. Wired into the orchestrator via `--stem-sections`.

### `Source/kick_model_adapter.py`
**Added 2026-07-09; dual readout 2026-07-16.** Lazy adapter for the sibling Kick Detector project. Loads `Kick Detector/Models/kick_crnn_V3.pt` and Kick Detector's reference `model.py` / `presence_postprocess.py` only when `--kick-model` is enabled. One inference now returns `KickPresenceReadout(raw, section)`: raw beat presence feeds contextual musical landmarks, while the validated threshold/smoothing (`0.30`, `fill_off_beats=6`, `drop_on_beats=1`) remains the coarse-section signal. The single Demucs pass still yields normal stem envelopes plus raw drums without double separation.

### `Source/automated_dj_mixes/musical_landmarks.py`
Extracts two-beat-or-longer raw Kick Detector V3 dropout spans, classifies short gaps immediately before drops, attaches section/energy context and candidate roles, and deliberately makes no arrangement selection.

### `Source/extract_musical_landmarks.py`
Safe standalone landmark refresh for certified stem JSONs. Hashes section geometry before/after persistence, refuses any section mutation, runs one V3 inference per track, and writes dedicated `LANDMARKS_*.png` views.

### `Source/align_engine.py`
**Added 2026-06-08; paired-landmark V2 2026-07-16; tail-loop swap gate 2026-08-14.** Bass-to-bass alignment engine used by `propose_arrangement`. `paired_landmarks_v2` preserves odd-bar cues, requires paired incoming/outgoing landmarks, suppresses arbitrary incoming-intro loops, and can extend to a named cue up to 64 bars. Cue-bounded tail loops select a clean phrase length that preserves an intermediate swap boundary as well as the final target; candidates whose actual loop interval ends before the locked swap are skipped, with a clear failure if no later named cue fits the repeat/extension caps. Legacy selection retains the 16-48 bar safety gate. Reads `SECTIONS_STEM_*.json` and retains the transition visualizer.

### Diagnostic / Research Scripts

- `Source/analyze_real_mix.py` — Decompresses a real Sam DJ mix `.als` and lists tracks/clips. Used 2026-05-19 to learn transition patterns from Bargrooves Summer 2015 Mix 1.
- `Source/inspect_transition.py` — Renders ONE transition as a clip-position timeline image. CLI: `python inspect_transition.py <out_substr> <in_substr> <label>`.
- `Source/test_mik_driver.py` — Smoke test for `desktop_analyzer.py` (MIK). (`test_rb_driver.py` moved to `Source/Archive/` in the Rekordbox removal, 2026-08-20; `diag_vlad.py`, `validate_pwv5.py`, `diagnose_rekordbox.py`, `analyze_phrase_patterns.py`, `test_features.py` were already archived/removed earlier.)

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
| matplotlib | Visualisation renders (previews, DETECT images) |
| numpy | Percentile stats + smoothing |
| ffmpeg-python | Audio format handling |
| pywinauto | Desktop UI automation (MIK) via Windows messages |
| pyautogui | Mouse/keyboard fallback for non-message-responsive controls |
| pyperclip | Clipboard support for `desktop_analyzer.py` path pasting |

## Added 2026-08-14

| Module | What it does |
|---|---|
| `Source/automated_dj_mixes/tempo_curve.py` | Smoothed tempo arc: per-track held tempos, ramps across transitions, outliers absorbed not chased. Also `span_stretch_percent` (the honest full-span metric), `ramp_exposure`, and `suggest_resequence`. |
| `Source/automated_dj_mixes/transition_policy.py` | Single source of truth for overlap/loop caps + frozen `TransitionPolicy` (interim_v1 / sam_v1). Replaces three independent declarations. |
| `Source/alignment_feasibility.py` | Pairwise "can these two tracks align" matrix + longest BPM-ascending chain. Pick a workable running order BEFORE building. |
| `Source/build_ab_comparison.py` | Builds two policy sides from one input, separate dirs and subprocesses so neither can contaminate the other. |
| `Source/seal_listening_test.py` | Randomised blind clips + sealed mapping + an A-vs-A noise twin. |
| `Source/setup_heldout_replay.py` | Stages verified held-out tracks (copies, never moves). |
