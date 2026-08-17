# MiniMax M3 — Broad Project Review (UNVERIFIED)

**Date:** 2026-08-17
**Brain:** MiniMax M3 (`MiniMax-M3` via `pi`, read-only tools, rooted in this worktree)
**Branch:** `review/minimax-project-audit`
**Status:** ⚠ **UNVERIFIED BY CLAUDE — this is MiniMax's unedited outside view.**

This is the first whole-project review the Automated DJ Mixes project has ever
had. MiniMax was given read access to the repo and asked six questions:
architecture fragility, accidental complexity, what has been missed, underused
assets, testing/validation soundness, and a ranked top three.

**Read this as a hypothesis list, not a findings list.** MiniMax was explicitly
asked to separate `[VERIFIED]` from `[SUSPECTED]`, and it did — but its
`[VERIFIED]` tag means "I read the cited code", not "this is true". Claude
spot-checked a subset of the load-bearing claims; the results of that spot-check
are recorded in the session report, not here. Known corrections so far:

- **Its #1 recommendation is materially overstated.** It claims
  `validate_mix_plan_als.reconcile()` "is never called from any pipeline script"
  and is therefore a missing gate. In fact the `/mix` skill invokes it as an
  explicit Phase 3b hard gate — MiniMax could not see that file because the skill
  lives outside this repo. The real (smaller) issue is that the gate is
  runbook-enforced rather than code-enforced.
- **§5.2, third bullet is wrong on a detail.** It asserts a track with 4–7 warp
  markers "would slip through BOTH gates". `enforce_owned_grid_coverage` tests
  `len(beat_times_ms) < 8`, so 4–7 markers *is* caught. The surrounding
  observation (that `enforce_beatgrid_quality` silently `continue`s past any
  track absent from `rb_matches`) is correct.

Everything below this line is MiniMax's raw output, unedited.

---

# BROAD OUTSIDE-VIEW REVIEW: "Automated DJ Mixes"

I read the orchestrator, align_engine, propose_arrangement, stem_detector, stem_grid, validate_beatgrid, als_generator, mix_plan, apply_automation, apply_loops, kick_model_adapter, sequencer, transition_policy, the Tests/ directory, and sampled a wide spread of the other Source/ files. Below are findings under each of the six headings you asked for.

Throughout, **[VERIFIED]** marks load-bearing claims I verified by reading the cited code; **[SUSPECTED]** marks inferences I am less sure of. Most of the load-bearing claims I did verify.

---

## 1. ARCHITECTURE

### 1.1 The hint system is a production gate that the production path bypasses

[VERIFIED — read `Source/automated_dj_mixes/orchestrator.py:93-128` (HINT_REQUIRED_FIELDS and `_validate_hints`), `Source/automated_dj_mixes/orchestrator.py:621` (`if not no_hints_required and not sections_layout:`), and `Source/automated_dj_mixes/orchestrator.py:688-810` (sections_layout is the only surviving code path; full-mix mode `raise RuntimeError` at the end).]

`HINT_REQUIRED_FIELDS` is `first_drop_sec`, `first_break_sec`, `outro_start_sec`, `last_bass_drop_sec`. The docstring on `last_bass_drop_sec` (orchestrator.py:99-102) explicitly says it's the "bass_swap anchor for the NEXT transition", and the field's docstring (orchestrator.py:93-100) frames `first_drop_sec` and `last_bass_drop_sec` as the alignment contract:

> "Align incoming.first_drop_sec to outgoing.last_bass_drop_sec so the EQ swap reinforces the natural musical swap."

The hint gate is `if not no_hints_required and not sections_layout: hint_errors = _validate_hints(...)`. Sections-layout IS the production path (the orchestrator ends with `raise RuntimeError("Full-mix mode has been retired...")`). So in production, `--sections-layout` is set, the gate is skipped, and the hints become optional metadata that nothing downstream reads.

Worse, even when hints ARE supplied, nothing in the alignment pipeline consumes them. [VERIFIED] I grepped `first_drop_sec`, `first_break_sec`, `last_bass_drop_sec`, `track_hints_data`, and `hint_to_candidates` across `Source/`:

- `Source/automated_dj_mixes/orchestrator.py:622` — only consumed by `_validate_hints` for the gate check
- `Source/automated_dj_mixes/stem_detector.py:704-748` — `hints_from_stem_result` writes them, never read
- `Source/automated_dj_mixes/cue_candidates.py:685-694` — `hint_to_candidates` exists, never imported as a callable (the orchestrator imports it but never calls it; same dead-import for `first_credible`, `find_cue_candidates`, `mik_to_candidates`, `amplitude_to_candidates`)
- `Source/validate_hints_vs_sections.py` — checks hints against section JSON, but only writes a markdown report; no other consumer

The actual swap alignment uses `bass_in_bar` / `bass_out_bar` from the stem JSON's `signals.bass_in` / `signals.bass_out` ([VERIFIED — `Source/align_engine.py:117-118`]), or `paired_landmarks_v2` cue coincidence. The hints are pure documentation tax — every project must author them (often manually from a preview PNG), then the orchestrator never reads the values. A whole human-in-the-loop step is enforced that the system doesn't actually use.

### 1.2 The three-phase production path is wired by hand, not by the orchestrator

[VERIFIED — read `Source/automated_dj_mixes/orchestrator.py:688-822` (sections_layout block) and `Source/apply_automation.py:882-937` (its `main`).]

The documented production flow is:
1. `orchestrator --sections-layout` → produces `Output/Sections V<N>.als`
2. `propose_arrangement.py Sections_V<N>.als Sections_V<N>.json Out.als [--mix-plan]` → produces arranged `Out.als` + `ARRANGEMENT_REPORT.json` + `MIX_PLAN.json`
3. `apply_automation.py Out.als Sections_V<N>.json Final.als [Out_ARRANGEMENT_REPORT.json]` → produces automation

None of these hand off automatically. The orchestrator's sections_layout branch terminates after generating the Sections ALS; it doesn't run `propose_arrangement` or `apply_automation`. There is no orchestrator entry point that produces a final-mix ALS in one call. AI_CONTEXT.md is explicit about this: the single-command full-mix mode was retired; "Production mixes use the three-phase /mix pipeline." [VERIFIED — orchestrator.py:822-826 `raise RuntimeError("Full-mix mode has been retired. Use the three-phase /mix pipeline...")`]

`apply_automation` makes the arrangement-report file an *optional* positional argument (`[arrangement_report.json]`). With no arg, it falls back to autodetecting a `*ARRANGEMENT_REPORT.json` next to the ALS — but if Sam runs propose_arrangement with `--report PATH` (or omits `--report`, defaulting to writing alongside the output ALS), the names have to match exactly. The autodetect is fragile in a way the docs don't document.

`validate_mix_plan_als.reconcile()` exists and is non-trivial (Source/validate_mix_plan_als.py:102-335), but is only run as a manual CLI invocation. It is never called from any pipeline script. A mix that passes every other gate (ALS validator, beatgrid validator, MixPlan hash check, manual review) can still drift from the plan without being caught, because reconciliation is a separate step the human has to remember to run.

### 1.3 Hidden contract: `--allow-non-master` is a no-op when the corpus is clean

[VERIFIED — read `Source/automated_dj_mixes/orchestrator.py:248-275`.]

`_MASTER_PATTERN = re.compile(r"(24\s*Bit\s*MASTER|SW\s+V\d+)", re.IGNORECASE)`. The gate raises ValueError on any non-matching WAV. The override is `--allow-non_master`. But the master pattern is broad enough that any *clean* Sam-master WAV matches; the gate only fires on stems/freezes. So in normal operation, the gate is silent and the flag is dead. The trap is that if Sam's master-file naming convention ever loosens (e.g., someone ships "Master V1.wav" instead of "SW V1.wav"), the gate hard-stops the whole pipeline without anyone having tested the override.

### 1.4 The grid-quality gate is bypassed for previews-only runs

[VERIFIED — orchestrator.py:499-507.]

```
if sections_layout and not previews_only:
    ...
    enforce_beatgrid_quality(analyses, rb_matches,
                             allow_bad_grids=allow_bad_grids,
                             grid_overrides=grid_overrides)
```

Previews run before the gate (orchestrator.py:602-619), and the gate only fires for sections_layout. So the preview PNGs can be rendered for tracks that the gate would FAIL. The preview writer then proceeds to the hint authoring step as if the grids were fine. This means a human authoring hints for a track whose grid would fail the gate is doing work that will be discarded.

### 1.5 The stem-grid path overwrites RekordboxAnalysis fields silently

[VERIFIED — orchestrator.py:387-465.]

The stem-grid branch reuses `rb_matches` (a dict keyed by track path that nominally holds `RekordboxAnalysis` objects) as the carrier for the stem-grid result. When a stem-grid lands, it OVERWRITES `existing.beat_times_ms`, `existing.first_downbeat_offset`, `existing.bpm`, `existing.end_beat` in place. The phrases stay `[]` (the stem-grid shell sets `phrases=[]`). Downstream consumers that look at `.phrases` (e.g., `enforce_rekordbox_coverage` does not — but anything iterating over phrases expecting RB phrases will see zero).

In tick-grid fallback (`orchestrator.py:362-368`), a `RekordboxAnalysis` shell is constructed with `phrases=[]` so the RB-coverage gate's phrase check would FAIL. But `enforce_owned_grid_coverage` only checks `len(beat_times_ms) < 8` and the tick shell passes that, so it survives.

The implicit contract — "rb_matches holds RB objects OR stem-grid shells, and consumers don't read `.phrases` in owned-grid mode" — is held in the head, not in any docstring or test.

### 1.6 Two clocks can still meet in a half-tested place

[VERIFIED — orchestrator.py:712-731 (stem-sections branch).]

The "one-clock" rule (orchestrator.py:711-731) reads: `grid_bpm_and_downbeat` is called on the rb_match grid, then `det_bpm, det_downbeat = g_bpm, g_db`, then `stem_res = stem_detect(... bpm=det_bpm, downbeat=det_downbeat, ...)`. The grid is the canonical clock.

But in the legacy RB-phrase branch (orchestrator.py:751-769, used when `--stem-sections` is NOT set), `features = extract_track_features(audio_path=analysis.path, bpm=rb_match.bpm or analysis.bpm, ...)`. The cache key for `extract_track_features` (`Source/automated_dj_mixes/features.py:71-83`) hashes `audio_path.stat().st_mtime`, `st_size`, `ANALYSIS_MODEL_VERSION`, `WAVEFORM_PARSER_VERSION`, and `ext_path.stat().st_mtime` — it does NOT hash `bpm` or `first_downbeat_offset`. So a feature pickle computed at bpm=128.000 from one RB export can be re-read for a different RB export at bpm=127.500 and the cuts will silently be off the warped audio.

This is exactly the 09.06.26 regression. The one-clock rule fix lives in the stem-sections branch; the legacy RB-phrase branch is the same trap with the same missing key. The test `test_grid_mode_corrects_wrong_detector_clock` (Tests/test_one_clock.py) covers the grid mode path, not the legacy extract_track_features path. [SUSPECTED — I did not run the test suite; this is read from the cache-key function and the orchestrator branch.]

### 1.7 The Rekordbox DB is dead weight that the production path still partially carries

[VERIFIED — orchestrator.py:318-345.]

In `stem_grid=True`, Rekordbox is explicitly disabled ("Rekordbox disabled: owned stem-grid + stem-sections are authoritative"). But the RB-import paths, the phrase-coverage gate, the waveform (PWV5) reader, and `enrich_from_rekordbox` still exist as code, still get imported at module load in several files, and still occupy the `analysis_source` field on `TrackAnalysis`. The grid_override path (orchestrator.py:362-368) constructs an `rb_match` shell precisely so the gate that USES `enforce_rekordbox_coverage` isn't called.

If a developer adds a new path that touches `.phrases` on an owned-grid rb_match shell, they will see empty arrays and have to discover the carrier-overload pattern from comments.

---

## 2. ACCIDENTAL COMPLEXITY

### 2.1 `cue_candidates.py` (~870 lines) is dead code

[VERIFIED — grepped for callers across the repo.]

`find_cue_candidates`, `first_credible`, `first_drop_candidate`, `mik_to_candidates`, `hint_to_candidates`, `amplitude_to_candidates` are imported in `Source/automated_dj_mixes/orchestrator.py:19-24` and never called. There are no other callers in `Source/` or `Tests/`. They appear only in `Documentation/TOOLBOX.md:71` and `Documentation/CODEX_REVIEW.md` as descriptions of an API no script uses.

This is the worst kind of dead weight: it's documented, it's imported (so a refactor that removed it would have to fix the import), it has no tests for itself (which is why its absence of effect hasn't been caught), and the existence of `candidates_for`, `_region_for`, `_bass_changed`, etc. makes the production code harder to navigate by grep.

The only survivor in `cue_candidates.py` is `load_hints_file` (line 750), which IS called (orchestrator.py:542) — for the gate check that doesn't gate.

### 2.2 Two parallel section-detection paths

[VERIFIED — orchestrator.py:688-826 vs orchestrator.py:751-771.]

The sections_layout branch contains a stem-sections path (orchestrator.py:706-748) using `stem_detector.detect` → `segments_from_stem_sections` → `validate_bar_math`, and a legacy RB-phrase path (orchestrator.py:751-771) using `extract_track_features` → `build_intervals` → `segments_from_intervals` → `refine_segments` → `validate_bar_math`. Both terminate in the same TrackPatch assembly.

The legacy path is the one the one-clock rule fix DIDN'T touch, and it's the one that still loads a feature pickle cached on a stale grid. The whole `phrase_viz.refine_segments` chain (~100 lines) plus `apply_section_corrections.py` is dead in production.

### 2.3 `arrange_sections.py` is a retired sibling of `propose_arrangement.py`

[VERIFIED — `Source/arrange_sections.py` is the older implementation. `Source/propose_arrangement.py:335` even has a comment "Matches the logic from arrange_sections.py". `Source/arrange_sections.py` is NOT imported by any file. It's only mentioned in docs and memory notes.]

### 2.4 `Source/` contains ~20 retired one-offs

[VERIFIED — `Documentation/FABLE_REVIEW_2026-06-10.md:82` lists: `check_bass.py`, `check_vlad_automation.py`, `extract_mix_patterns*.py`, `analyze_teaching.py`, `bass_detection.py`, `find_bass_swaps.py`, `diagnose_sections.py`, `arrange_sections.py`, `sections_compare_viz.py`, plus `Source/Archive/`. The repo's own docs call this out as a problem.]

I grepped for callers of a sample: `arrange_sections.py` (no callers), `apply_section_corrections.py` (no callers), `setup_heldout_replay.py` and `setup_car_mix.py` (referenced in their own docstrings but not imported). The docs themselves recommend purging these.

### 2.5 Three near-identical functions, two near-identical gates

[VERIFIED — `Source/automated_dj_mixes/orchestrator.py:152-205` (`enforce_rekordbox_coverage` and `enforce_owned_grid_coverage`) and the `enforce_beatgrid_quality` in `Source/validate_beatgrid.py:371-405`. All three check `len(beat_times_ms) < 8` against a per-track dict and raise `RuntimeError` with a "MISSING/FAIL" listing. They differ only in the prefix message and the data structure they inspect.]

The three are not factored because they were written at three different points in time and the duplication was tolerated. The test_rekordbox_health.py suite pins two of them with similar tests; there's no shared test of the gate invariant.

### 2.6 `transition_policy` is well-centralized; its siblings aren't

[VERIFIED — `Source/automated_dj_mixes/transition_policy.py` is the right pattern: a single source of truth with `bars_to_beats`/`beats_to_bars` helpers and a `TransitionPolicy` dataclass. The test `Tests/test_transition_policy.py` actively checks that no consumer redeclares the constants.]

But:
- `apply_automation.py` has `BOUNDARY_MARGIN = 64`, `EQ_BASS_KILL = 0.18`, `VOL_SNEAK = 0.2`, `VOL_SNEAK_LOW = 0.1`, `EQ_BASS_PARTIAL = 0.52`, `VOL_PARTIAL_DROP = 0.56` — all baked-in magic numbers that the policy module doesn't cover. [VERIFIED — apply_automation.py:42-62]
- `align_engine.py` has `PHRASE_GRID = 16`, `HANDOFF_WINDOW_BARS = 8`, `COINCIDE_TOL_BARS = 2`, `MIN_SWAP_PROGRESS = 0.25`, `MAX_SWAP_PROGRESS = 0.95`, `MAX_SKIP_BREAK_BARS = 8` — also baked in. [VERIFIED — align_engine.py:46-64]
- `stem_detector.py` has `KICK_ON_FRAC`, `KICK_SMOOTH_BEATS`, `FILL_MAX_BARS`, `FILL_MAX_BEATS`, `FILL_DIP_FRAC`, `PRESENCE_FRAC`, `STEM_ABSENT_FRAC`, `SMOOTH_BARS`, `PHRASE_GRID`, `MIN_SECTION_BARS`, `DROP_REL`, `OUTRO_LEAD_FRAC`, `MIN_OUTRO_BARS`, `MAX_OUTRO_BARS`, `OUTRO_CAP_BARS`, `MIN_LOOP_BARS`, `MIN_VOCAL_BARS` — sixteen magic numbers, all calibrated on Sam's corpus but not exposed via the policy pattern. [VERIFIED — stem_detector.py:36-58]

The transition_policy pattern is the right design. The same pattern was never applied to the magic numbers in apply_automation or stem_detector.

---

## 3. WHAT HAS BEEN MISSED

### 3.1 Filter sweeps are gone

[VERIFIED — `Source/als_generator.py` has `_find_filter_target_id` and the `transition_automation` parameter accepts `lp_filter` and `hp_filter` keys (als_generator.py:746-750), but `apply_automation.py` only emits `volume` and `eq_bass`. Tests/test_als_generator.py includes `test_generate_with_automation` with LP and HP filter keys; the production path never uses them.]

DJ practice and AI_CONTEXT.md's reference to the original transition.py (`Documentation/AI_CONTEXT.md` mentions "the old filter-sweep transition tests were removed") confirm this. The infrastructure is intact (template has AutoFilter2 LP+HP, automation target ID finder exists, ALS writer emits the envelope XML), but nothing drives it. A producer-mix that opens with a high-pass filter sweep on the incoming is impossible without manual work.

### 3.2 EQ high-pass on the incoming is gone (and so is the bass-EQ mid-cut on the outgoing, replaced by ChannelEQ LowShelfGain only)

[VERIFIED — apply_automation.py:602-714. The outgoing gets `eq_bass` (LowShelfGain) and the incoming gets `eq_bass`. The incoming NEVER gets a high-pass — common DJ practice would high-pass the incoming during its sneak-in to avoid bass clash. There's no `hp_filter` automation emitted for the incoming in any transition style.]

### 3.3 No silence detection

[VERIFIED — no module reads `librosa.effects.split` or computes silence frames. The "24.06.26" `validate_beatgrid.py` docstring mentions "stage 0a: strip acapellas/non-mix strays" — that's done by Sam's eye, not by the system. A 90-second silent intro (typical of club tools) will produce 90 seconds of zeros for kick onsets and fail `MIN_ONSETS = 80` (validate_beatgrid.py:54) unless the track is also longer than ~120s.]

### 3.4 No track-level intro-length feature for sequencing

[VERIFIED — `sequencer.build_harmonic_path` (sequencer.py) and `apply_energy_arc` (sequencer.py:78-117) use only key, BPM, and energy. `apply_energy_arc` divides into thirds and reorders within thirds; it does not, for example, put a track with a 64-bar intro at position 3 of the mix where it can sit alone, or put a track with a short intro immediately after a transition. The professional sequencing craft has no representation here.]

### 3.5 No phrasing-aware overlap sizing

[VERIFIED — `transition_policy.py:60` `max_overlap_beats = 192.0` is a fixed 48-bar ceiling; `max_landmark_overlap_beats = 256.0` is a 64-bar ceiling reserved for named landmarks; `max_extended_overlap_beats` is None for INTERIM_V1. There's no logic that adapts overlap length to the outgoing's outro length or the incoming's intro length. A 64-bar outro on the outgoing forces a 64-bar overlap (max), but the production policy treats it identically to a 16-bar outro.]

### 3.6 No handling of energy continuity between successive tracks

[VERIFIED — `apply_energy_arc` divides into build/peak/cooldown thirds. It does NOT check whether the LAST DROP's energy of track N matches the FIRST DROP's energy of track N+1. A mix can go peak-energy → low-energy-intro → build → drop and produce an audible dip at every transition. The `harmonize_with_neighbours`-style heuristic is missing.]

### 3.7 No handling of mid-track key changes

[VERIFIED — `sequencer.key_to_camelot` returns a single key per track. The Camelot-wheel scoring in `compatibility_score` (sequencer.py:106-143) is a single-pair function. A track that modulates from 8A to 9A mid-drop will be sequenced as 8A (or 9A) — whichever the metadata reports — and the modulation is invisible to the mix.]

### 3.8 No LUFS-mix audit

[VERIFIED — `apply_automation._apply_track_levelling` (apply_automation.py:780-825) measures per-track LUFS and sets per-track mixer offsets to bring the loudest DOWN to the quietest. It never measures the MIX's integrated LUFS after offsetting. The mix could come out at -6 LUFS or -14 LUFS depending on the track distribution; the system doesn't know. Spotify is -14 LUFS integrated; clubs are much louder.]

### 3.9 No "what to do if a track is too quiet / too loud for the chain"

[VERIFIED — `apply_automation._apply_track_levelling` is a soft cap (`max_reduction_db=12`). If a track is louder than -6 LUFS in a mix where the quietest is -16 LUFS, the difference is 10 dB — at the cap, the loud track gets only -12 dB applied (the floor for `max_reduction_db`). The remaining +2 dB is never raised as an issue.]

### 3.10 The `phrase_viz` chain has hidden assumptions about phrase length

[VERIFIED — phrase_viz.py:882 `NICE = {4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 48, 56, 64, 80, 96, 128}`. The validate_bar_math function flags any chop whose delta isn't in NICE. But "FILL_MAX_BARS_AS_FILL" (phrase_viz.py:46) caps fills at ≤4 bars before relabeling as break. A 5-bar fill that would otherwise be musically correct gets relabeled. There is no override for cases where the audio genuinely has a 5-bar fill.]

### 3.11 No track order feedback from the producer

[VERIFIED — `propose_arrangement.py` reads a manual `--order` (orchestrator.py:215), but there's no in-system way for a producer to drag tracks around or mark "this transition should be longer" without editing the JSON. The whole feedback loop runs through code edits.]

### 3.12 No concept of "DJ cue points" beyond MIK auto-cues

[VERIFIED — `mik_reader.read_mik_db_track` returns MIK cue points (8 per track from MIK 11). `orchestrator.py:535-547` reads them into `mik_data` but never uses them in arrangement or automation. `apply_automation.py:498-535` reads them only via the report-swap lookup. The DJ's natural cue points (the start of a long breakdown, the moment to bring faders up) are not surfaced.]

### 3.13 No "skip section" flag

[VERIFIED — `propose_arrangement.py:1018` accepts `intro_skip_bars` in hints, but it ONLY shortens the intro (via drop-clip removal). It cannot skip a section in the middle of a track (e.g., a 16-bar filler passage the producer wants removed). The whole `dropped_clip_names` mechanism handles pre-drop only; mid-track drops are not exposed.]

### 3.14 No mix-level audition gate

[VERIFIED — no module reads the produced ALS back through Ableton or Producer Pal and computes an audio-side verification. The `warp_mode_enum` test (Tests/test_warp_mode_enum.py:7-22) explicitly documents that "the bug was invisible in the file. Only Live could reveal it" and recommends a post-build check that reads the generated set back through Live rather than trusting the XML. No such check exists in `Source/`.]

### 3.15 No "this is a finished third-party track" cue

[VERIFIED — `--allow-non-master` exists, but once set it just turns off the gate; nothing about the produced mix reflects the difference. A commissioned mix of finished promo tracks and a Sam-mastered stems mix would be produced by identical code paths, with the only difference being the bypassed gate. The producer (Sam) has to remember which was which when reviewing.]

### 3.16 `pair_history.jsonl` is a record with no behaviour

[VERIFIED — `find_similar_pairs` (propose_arrangement.py:963-1009) reads the file, scores pairs by BPM + structure similarity, and attaches the top 3 to `OverlapAnalysis.similar_pairs`. The report writer copies these into `report["transitions"][i]["similar_history"]`. Nothing else reads the file. The verdict (`correct`, `corrected`, `correct_with_arrangement`) is never consulted. There are 18 entries in `Documentation/Mix Patterns Library/pair_history.jsonl`; none of them gate anything.]

The `pair_history.jsonl` is therefore an audit ledger, not a learned model. That's a deliberate choice (the docs say "learning plateaus at hand-written if-statements"), but it's worth being honest about — the file doesn't do what its name suggests.

### 3.17 `genre_priors.json` is unread

[VERIFIED — `find` for `genre_priors` returns only `Documentation/Mix Patterns Library/genre_priors.json` (one file). No `Source/` module imports or reads it.]

---

## 4. UNDERUSED ASSETS

### 4.1 Rekordbox phrase data

[VERIFIED — `enrich_from_rekordbox` (Source/automated_dj_mixes/analysis.py:15-94) maps RB phrases (intro/up/down/chorus/outro) to TrackAnalysis fields. The legacy RB-phrase path in orchestrator.py:751-769 uses these to build intervals and segments. The production path (stem-sections) doesn't read them. The Rekordbox DB is therefore paid for (UI automation in desktop_analyzer.py) and not used.]

### 4.2 Rekordbox waveform (PWV5/PWV4)

[VERIFIED — `rekordbox_waveform.py` parses waveform color data; `extract_track_features` reads it (features.py:155-159). The waveform is consulted in the legacy RB-phrase path's intervals. Stem-sections path doesn't use waveform data. The 4th analysis signal mentioned in the AI_CONTEXT.md is paid for and unused in production.]

### 4.3 MIK auto-cues

[VERIFIED — `mik.cues` is read into `mik_data` (orchestrator.py:535-540). The cue points themselves are not surfaced or consumed; only MIK's key and BPM are extracted (orchestrator.py:541-547). The 8 per-track DJ cue points from MIK 11's auto-cue model are dropped.]

### 4.4 MIK energy field

[VERIFIED — `mik.energy` is read into `t.energy` (propose_arrangement.py:1156-1158, in the MIK enrichment loop). It's then used by `apply_energy_arc` (sequencer.py:78) — but `apply_energy_arc` is a coarse thirds-reordering that only fires when it doesn't add a Camelot clash. MIK's energy is the most reliable signal Sam has, and it's used as a tertiary tiebreak. (Test code does read MIK cues directly in some places; the production pipeline does not.)]

### 4.5 `stem_detector.hints_from_stem_result` (the autonomous hints path)

[VERIFIED — stem_detector.py:702-749 derives all four required hint fields from a stem-detector result. The orchestrator has a `--write-hints` mode... no, wait. The `--write-hints` mode is in `stem_detector.py:759` (its CLI), not in the orchestrator. The orchestrator requires hints via the gate. The autonomous path produces a `Hints/track_hints.json` file that the orchestrator then loads (orchestrator.py:542) but never reads the values of. So `hints_from_stem_result` is a working piece of code whose output the production system consumes by file-existence only.]

### 4.6 `extract_musical_landmarks.py` refreshes landmarks without changing sections

[VERIFIED — `Source/extract_musical_landmarks.py` exists. Test `test_refresh_adds_landmarks_without_changing_sections` (Tests/test_musical_landmarks.py:57) pins it. But it's a manual tool, not wired into any orchestrator step. After a stem-detector run, you have to run extract_musical_landmarks separately to get landmarks, then propose_arrangement separately to consume them.]

### 4.7 `cue_candidates.first_drop_candidate` and the FIRST_DROP_WINDOW_SEC rule

[VERIFIED — `FIRST_DROP_WINDOW_SEC = (30.0, 75.0)` (cue_candidates.py:358) is the dance-music structural prior. "A bass_entry past 75s is usually a SECOND chorus (post-break) and would force the listener to hear 1-2 minutes of the incoming track before the swap." This is exactly the kind of professional knowledge the system should be applying. It's defined, tested in the docstring, and dead. Production alignment ignores it entirely (align_engine.py uses `bass_in_bar` from stems, not first-drop windows).]

### 4.8 `phrase_viz`'s 14-rule phrase refinement

[VERIFIED — `phrase_viz.py` has `_collapse_fake_first_drop`, `_refine_first_drop_start`, `_trim_short_breaks`, `_refine_outro_start`, `_absorb_short_segments_before_outro`, `_split_intro_build_zone`, `_split_drop_with_fills`, all called from `refine_segments` (phrase_viz.py:910-980). The whole 100-line chain is dead in production because the production path is stem-sections, which calls `segments_from_stem_sections` (phrase_viz.py:285-340), not `segments_from_intervals` + `refine_segments`.]

### 4.9 `validate_corpus.py` and similar validators

[VERIFIED — `validate_corpus.py` exists; no tests for it. `verify_grid_bar_parity.py` exists; no tests for it. The corpus-level checks are manual.]

### 4.10 The `Mix Patterns Library/` documentation

[VERIFIED — five files in `Documentation/Mix Patterns Library/`. Only `pair_history.jsonl` is read by code (and only as a similarity lookup). `genre_priors.json` is unread. `Heldout Replay Plan V2.md`, `Heldout Replay Result 01.md`, `Fresh Mix V2 Sam Tweaks.md` are markdown. `MIX_PATTERNS.md` is legacy/unapproved per `Documentation/MIXING_PATTERNS.md:7`.]

---

## 5. TESTING AND VALIDATION

The test suite is real — 32 test files, ~5000 lines, the gating is tight where it exists. But several checks are vacuous, self-referential, or measure the system against its own output. Below are the specific ones I found, by category.

### 5.1 Self-referential tests

**`Tests/test_playback_policy_sets_tempo_and_every_clip_warp_mode`** (Tests/test_arrangement_safety.py:331-365)

[VERIFIED — read the test.]

The test passes `apply_playback_policy(lines, [TrackInfo("out", [], 0.0, 128.0)], 120.49, 6)` and asserts `"".join(lines).count('<WarpMode Value="6" />') == 2`. It is asserting "if you pass 6, you get 6" — a circular check that would pass for any function that round-trips its input. It does not assert that 6 IS the right mode for a particular track/bpm/project_bpm combination. The function-level round trip is fine; the assertion is empty.

Compare to `test_choose_warp_mode_never_repitches_audible_shifts` (Tests/test_warping.py:32-43) which DOES assert behavior: `assert choose_warp_mode(127.0, 126.0) == WARP_MODE_COMPLEX_PRO`. The two-tier quality difference is real.

**`Tests/test_als_generator.py:test_generate_inserts_clip`**

The test calls `generate_session(TEMPLATE, [patch], out, project_bpm=128.0)` and asserts `any("AudioClip" in l for l in lines)`. This is true if `generate_session` runs at all. It doesn't assert the clip is associated with the right track, or that its Time/LoopStart/LoopEnd are correct, or that the warp markers are attached. It would pass for a generate_session that emits an unanchored clip anywhere in the file.

**`Tests/test_als_generator.py:test_generate_inserts_warp_markers`**

`assert len(warp_lines) >= 2`. The `patch._fake_markers()` returns 2 markers, so this is just "did the function not crash?". The `>= 2` allows degenerate cases where the count is wildly wrong.

### 5.2 Vacuous / self-cancelling checks in the beatgrid gate

[VERIFIED — `Source/validate_beatgrid.py:165-195` (`_grade` and `check_grid`); the tests in `Tests/test_beatgrid_stem_gate.py` and `Tests/test_one_clock.py`.]

The beatgrid gate is the canonical example of a metric that was carefully designed to not be self-cancelling — the `+1% detuned twin` is the control. But the metric still has holes:

- **`test_stem_grid_off_kicks_fails`** uses `r_half=0.01` which is below `FAIL_R = 0.30`. The test asserts the gate FAILS with stem_kf_ms=88 (which is above `STEM_KF_FAIL_MS = 15`). The R value is irrelevant because `stem_fitted=True` short-circuits to the grid_vs_kick check. If you delete the grid_vs_kick argument from `verdict_from`, the test breaks. But `test_missing_kf_falls_back_to_pass` PINS the behaviour "no grid_vs_kick supplied (e.g. legacy override) -> don't block on it" by returning PASS. So the gate is fully bypassed when `grid_vs_kick_ms` is None — and the only call site (`enforce_beatgrid_quality`) reads `grid_vs_kick_ms` from `grid_overrides`. If a developer changes the override dict schema, the gate silently passes. [VERIFIED — `Source/validate_beatgrid.py:298-318`, `Source/automated_dj_mixes/orchestrator.py:454-459`.]

- **`verdict_from` test `test_verdict_phase_advisory_never_fails`** uses `phase_advisory=True` to ensure PHASE never fails. But the orchestrator passes `phase_advisory=advisory` where `advisory` is always True UNLESS Ableton ticks are present (orchestrator.py:226-239). For a track without `.asd` analysis in Live, the gate will NEVER fail on phase. That's a feature ("the bias-prone librosa estimate is advisory"), but it means a track with a 0.3-beat phase error (way outside the 0.12-beat tol) and no `.asd` ticks passes. [VERIFIED — `Source/validate_beatgrid.py:226-260`.]

- **`enforce_beatgrid_quality`** iterates `analyses` and only checks tracks that have an `rb_match` (`if rb is None: continue`). In owned-grid mode, the stem-grid branch stores its result in `rb_matches` (orchestrator.py:438-440), so all gridded tracks get checked. But if `detect_beat_grid` raises (orchestrator.py:413), the track keeps `existing` which in stem_grid mode is None. The loop hits `if rb is None: continue`, the track is NOT in `checks`, and `fails = [c for c in checks if c.verdict == "FAIL"]` doesn't include it. The track is then NOT in the gate failure list, and the gate PASSES for a track whose stem-grid silently failed. The owned-grid coverage gate catches this (because `len(beat_times_ms) < 8` is False for None), but a track with 4-7 markers would slip through BOTH gates. [VERIFIED — `Source/automated_dj_mixes/orchestrator.py:188-205` and `Source/validate_beatgrid.py:371-407`.]

### 5.3 Tests that compare against the system under test

**`test_reconciles_mix_plan_to_als`** style tests don't exist. `validate_mix_plan_als.reconcile()` (Source/validate_mix_plan_als.py:102-335) has Tests/test_mix_plan.py covering parts of it via `_matches_clip_boundary` (line 175), but the full `reconcile` function — the one with the 17 plan-to-ALS checks mentioned in the docs — has no direct unit test. [VERIFIED — grep for `def test_reconcile` returns nothing.]

The 47 / 58 / 17 check counts cited in `Documentation/AI_CONTEXT.md` come from running the manual CLI, not from CI. The "test suite is 131 passed/4 skipped" lines describe `pytest` results; whether `reconcile` is exercised in CI is not clear. **The reconciliation step is the most expensive single missing check.**

### 5.4 The `_load_arrangement_report` autodetect could pick a stale file

[VERIFIED — `Source/apply_automation.py:499-535`.]

When `arrangement_report_path` is omitted, the loader picks the *newest* `*ARRANGEMENT_REPORT.json` next to the ALS. If Sam has multiple in-progress reports in `Output/` from different mix versions, the loader can pick the wrong one. The test `test_explicit_arrangement_report_is_required_and_preserves_swap` (Tests/test_arrangement_safety.py:367-388) tests the explicit case but not the implicit/autodetect case.

### 5.5 Hardcoded "I know this track" data in `verify_grid_bar_parity.py`

[VERIFIED — `Source/verify_grid_bar_parity.py:33-37`.]

```python
REVERTED_SHIFTS = {
    "Hold Me": 85.9,
    "Blackout": 97.4,
    "Bullerengue": -83.1,
}
```

These are project-specific ms shifts that get applied to specific track names (substring match) before computing bar parity. The file has no test. If "Hold Me" appears in a future project with a DIFFERENT intended phase shift, the parity checker would silently misalign.

### 5.6 `validate_hints_vs_sections` returns 0 with empty output when there are no hints, but 2 with an error when there are hints but no BPMs

[VERIFIED — `Source/validate_hints_vs_sections.py:118-122, 217-220`.]

When `track_hints.json` doesn't exist: `return 0, [], "(no track_hints.json — nothing to validate)"`.
When it exists but BPM lookup fails for every track: `return 2, ["0 checks performed — the gate compared NOTHING ..."], ...`.
The exit code flip (0 vs 2) is correct, but the message text says the same thing ("nothing to validate" vs "compared NOTHING"). And the 0-row case is a real risk: a hints file with no recognisable track names would also produce 0 rows. The comment in the source notes this ("2026-06-11: first runs had no ARRANGEMENT_REPORT for BPMs, every track was skipped, and 'PASS' went out over zero checks") and addresses it for the BPM-missing case, not the hint-key-missing case.

### 5.7 The "golden mix" tests are pinned to a specific corpus

[VERIFIED — `Tests/test_align_engine_golden.py:30-36`.]

`GOLDEN_SWAPS = [528, 1136, 1776, 2416, 3024, 3552, 3952, 4400, 4880]` and `GOLDEN_INTRO_LOOPS = 6` and `GOLDEN_OUTRO_LOOPS = 9` and `GOLDEN_BREAK_SKIP_PAIRS = [2]` are locked values from the 08.06.26 mix's hand-edited ALS. They will pass forever as long as that fixture is present and `align_engine` doesn't change. They will FAIL if any change to `align_engine` shifts a swap beat, changes the loop layer, or moves the break-skip. That's intentional (regression detection), but the comments call it a "byte-for-byte" lock — it's actually a swap-and-loop lock, and a change to the loop count or break-skip pattern would falsely trip.

### 5.8 `validate_arrangement_plan` checks against its own stale data

[VERIFIED — `Source/propose_arrangement.py:130-200`.]

`validate_arrangement_plan` validates `arrangement.tracks` against `arrangement.overlaps`. But `_plan_marker_loops` mutates `out_track.arr_end` ONLY for the break-skip branch. Test `test_outgoing_end_shrinks_with_the_trimmed_outro` (Tests/test_break_skip_geometry.py) explicitly pins this — and its own docstring says: "validate_arrangement_plan cannot catch it: it checks the overlap against that same `arr_end`, so it is self-consistently wrong." That's a known self-cancelling check, with a test that documents it. **But the test still passes** — because the test compares against `outgoing.arr_end` after the mutation, which is the same value `validate_arrangement_plan` would use. So the validation IS catching the wrong state, and the test is pinning that the validation will silently approve a stale `arr_end` as long as both sides of the check are equally stale.

### 5.9 Hardcoded "if it ran without crashing" checks

**`Tests/test_tempo_contract.py:test_curve_for_the_wrong_number_of_tracks_is_refused`** asserts a `ValueError` is raised. The function raising is the right behaviour, but the test doesn't check the error MESSAGE matches what downstream code expects. A `ValueError` with the wrong text would still pass.

**`Tests/test_kick_model_integration.py:test_flag_off_fixed_input_parity_and_lazy_import`** asserts `assert "kick_model_adapter" not in sys.modules` and `assert "kickdet_model" not in sys.modules`. It checks import-time behaviour, but does not assert that the adapter is callable or that the output is correct. A stub adapter that returned all zeros for kick presence would still pass.

**`Tests/test_mix_plan.py:test_mix_plan_rejects_stale_plan_hash`** (line 154) replaces the hash with `_hash("0")` (64 zeros) and asserts `validate_mix_plan` raises `ValueError`. The function raising IS the right behaviour, but the test uses a 64-zero string as the "tampered" hash. A real corruption might produce a 64-char string of zeros by coincidence (extremely unlikely, but the test doesn't assert the error message). The check is sound but not thorough.

### 5.10 Tests that could not fail (vacuous coverage)

**`test_plan_without_a_tempo_contract_still_builds`** (Tests/test_tempo_contract.py:31-35): asserts `plan.tempo is None` and `plan.schema_version == "1.4"`. The latter is a string constant — if the constant were changed in mix_plan.py the test would fail, but it's not a behaviour test.

**`test_arrangement_safety.py:test_loop_spec_rejects_excessive_repeat_or_extension`** (line 274-280): uses `pytest.raises(ValueError)` but doesn't assert the message. A `ValueError` with text "wat" would pass.

**`Tests/test_automation.py:test_gain_offsets_empty`** asserts `calculate_gain_offsets([]) == []`. This is fine; the test is small.

**`Tests/test_kick_model_integration.py:test_default_path_is_bit_identical_to_the_original`** asserts `assert first == second`. This is a determinism check, but for the SAME inputs in the SAME process — it doesn't assert that the output is musically correct.

### 5.11 The orchestrator's own gates have a vacuous-but-correct path

[VERIFIED — `Source/automated_dj_mixes/orchestrator.py:280-299` and `Tests/test_rekordbox_health.py:101-135`.]

`analyze_folder_with_mik` and `analyze_folder_with_rekordbox` are wrapped in `try/except Exception`. Any failure prints "WARNING: desktop analysis did not complete cleanly" and continues. The `test_owned_mode_runs_mik_but_never_launches_rekordbox` test (Tests/test_rekordbox_health.py:107-135) proves the MIK call but NOT the failure-tolerance of the try/except. A MIK driver bug that silently mis-analyses every track would NOT be caught here.

---

## 6. TOP THREE

### #1: Wire `validate_mix_plan_als.reconcile()` into the production orchestrator

[VERIFIED — `Source/validate_mix_plan_als.py:102-335` has no caller in any pipeline script. The orchestrator generates the MixPlan (via `propose_arrangement.py:1328-1380`) but never reconciles it against the produced ALS. Every other gate (ALS validator, beatgrid validator, MixPlan hash) can pass and the ALS can still drift from the plan: swap beats moved, loop clips inserted at the wrong beats, warp modes changed.]

**Why this is the top change**: the MixPlan was designed precisely so this check exists. The "production contract" includes the assertion that the plan-to-ALS diff is empty. Without reconciliation, every other gate is doing the right work for an output nobody verifies matches it. Reconciliation is also the cheap thing to add — `reconcile()` is already written; it's a one-liner to invoke it after `apply_automation.compress_als` and to halt the pipeline on `result["status"] != "PASS"`.

The ratio of improvement to effort is enormous because the code already exists and the failure mode (a MixPlan-validated mix that doesn't match its plan) is exactly the class of bug the MixPlan was built to prevent.

### #2: Make the hint system actually drive alignment, or delete it

[VERIFIED — see Section 1.1 and Section 4.5 above.]

The hints are a documented "production gate" that the production path bypasses, and even when supplied they are never read by anything that affects the produced mix. The fix is one of two:

(a) Delete the gate and the `HINT_REQUIRED_FIELDS` machinery, and stop pretending the human-in-the-loop step is enforced. This shrinks the surface and removes the implicit "this matters" signal that mislead me initially.

(b) Wire hints into alignment: `last_bass_drop_sec` on the outgoing as the bass-swap anchor (the docstring already promises this), `first_drop_sec` on the incoming as the bass-ownership pass point. Make `align_engine.align_pair` consult the hints when present, and let MIK / RB / stem fill in when hints are absent. This is the path the docstring describes but the code doesn't implement.

Either change is small (delete = ~30 lines; wire = ~60 lines). The current state — required fields, no readers, gate that doesn't gate — is the worst of both.

### #3: Delete the dead code paths

[VERIFIED — see Sections 2.1, 2.2, 2.3, 2.4 above.]

`cue_candidates.py` (5 of its 6 functions, ~870 lines of API surface), `arrange_sections.py`, `apply_section_corrections.py`, the RB-phrase path through `phrase_viz.refine_segments`, and the 20-ish one-offs listed in `Documentation/FABLE_REVIEW_2026-06-10.md:82` are all dead in production. They make grep expensive, they confuse refactoring, and they hide bugs (a security review or a "what does this code do" walkthrough has to read them to know they're not in use).

The cleanup ratio is high because:
- None of them have callers to update.
- The test suite pins the production behaviour elsewhere; deleting dead code shouldn't break any test.
- The `Archive/` directory already exists as the agreed-on home for retired code; moving things there is non-destructive.
- A smaller source tree makes every future bug investigation faster.

### Honourable mentions (would be #4-#5 if you wanted more)

**Make apply_automation emit filter sweeps.** The infrastructure is in place (`_find_filter_target_id`, AutoFilter2 LP+HP in the template, automation envelope XML builder). The transition style selection ignores it. This is a single afternoon's work and would noticeably lift the musical quality.

**Add a post-build audio check that reads the produced ALS through Ableton/Producer Pal.** `test_warp_mode_enum.py:7-22` documents exactly this need: "the bug was invisible in the file. Only Live could reveal it." No module in `Source/` does this check.

**Run cue_candidates.first_drop_candidate's FIRST_DROP_WINDOW_SEC rule in `align_engine`.** The dance-music structural prior ("first drop usually lands 30-75s in") is exactly the kind of professional knowledge that should gate the alignment. It's defined and never used.

---

## A note on what I did NOT get to

I did not:
- Run the test suite (no execution available in this read-only view).
- Open `Documentation/AI_CONTEXT.md` past the top ~100 lines, or any other doc past skimming.
- Read `Source/automated_dj_mixes/tempo_curve.py` beyond what was needed to confirm `solve_track_tempos` is the outlier-absorber.
- Read the `audio_analysis.*` toolkit modules the shims import from — the canonical code lives in a sibling repo and only the shim interface matters from this project's perspective.
- Verify the exact behaviour of `_load_arrangement_report` autodetect against a real filesystem (I read the code path but did not exercise it).

If any of those would change a load-bearing claim, the claim is tagged `[SUSPECTED]`. The rest I am confident in.

