# Full Project Wiring, Holes, and Upgrades — Audit

**Date:** 2026-08-18 13:23 BST · **Brain:** Mavis
**Scope:** every action the pipeline performs, where it lives, who invokes it, what's wired, what's orphaned, what's broken, and what could be better. **Read-only audit — no code changed.**
**Commissioned by Sam:** *"map the entire thing, find an action and find out where its wired to, if its wired to the correct place where it should be wired, could it be used somewhere else. look for holes, find orphaned features. suggest upgrades, simplifications or anything that could improve the output, better section detection, better mixes better transient/warp detection anything that would help in programmatically creating a dj mix. as a cherry at the end be creative with your thoughts after you know the knowledge base and suggest new features that arnt in the project."*

**Also Sam, 2026-08-18:** "the leaning back on rekordbox … that software is done away with as far as im concerned." This audit is therefore scoped to the Rekordbox-free canonical path (`--stem-grid --stem-sections --kick-model`). Remaining Rekordbox code is treated as residue to be removed; it is item #10 of the top-10 priority list.

**v2 update (13:34 BST, same session):** §3.3, §4.5b, and §6 updated. Item #2 replaced — the original "bless Golden Sections fixtures" plan was rejected by Sam (2026-08-18) as ambiguous and one-off; the right answer is a property-based kick-cues regression test (no blessed ground truth needed). Top 10 renumbered, Rekordbox removal moved from #1 to #10 so its blast radius is its own focused PR.

---

## 0. Related audits (read first)

- **`Documentation/Reviews/2026-08-17 Wiring Audit.md`** — Claude (Opus 5), four parallel agents. 380-pair ablation across the aligner. Measured the swap decision reads only 4 of ~20 produced signals. Sharper on the align_engine wiring than this audit.
- **`Documentation/Reviews/2026-08-18 Kick-Based Section Detection Investigation.md`** — Claude + MiniMax M3, two-lens verification. Traced the Double Dutch 16-bar dropout erasure to V3's smoothed/bridged path being mandatory despite a documented opt-in gate that was never satisfied. Sharper on that one bug class.

This audit complements those two: the 17 Aug audit covers the aligner wiring in depth; the 18 Aug kick investigation covers one specific signal path in depth; **this audit covers everything else** — the orchestrator's full wiring, every action, every orphan, every detection quality opportunity, and the cherry.

---

## 1. The pipeline in one diagram (what actually runs)

```
/mix <project>            (skill in Claude/Codex Brain; invokes the commands below in order)
  │
  ├─[Phase 0: previews]─►  python -m automated_dj_mixes.orchestrator
  │                        --previews-only  --input <Audio/>  --output <Output/>
  │                           └─► waveform_preview.py → Visualisations/Previews/*.png
  │                           └─► Hints/track_hints.json  (authored by hand from the PNGs)
  │
  ├─[Phase 1a: sections]─►  python -m automated_dj_mixes.orchestrator
  │                        --sections-layout --stem-sections --stem-grid --kick-model
  │                        --input <Audio/> --output <Output/>
  │                           └─► desktop_analyzer (MIK only when --stem-grid; else MIK+RB)
  │                           └─► stem_grid.detect_beat_grid   → rb_matches (one-clock injection)
  │                           └─► kick_model_adapter → stem_detector._model_kick_presence_per_beat
  │                           └─► stem_detector.detect          → sections + signals
  │                           └─► phrase_viz.segments_from_stem_sections
  │                           └─► validate_beatgrid.enforce_beatgrid_quality
  │                           └─► als_generator.generate_session  → Sections V<N>.als
  │
  ├─[Phase 2: arrangement]─► python propose_arrangement.py
  │                        --als-path <Sections V<N>.als> --output <Output/>
  │                           └─► extract_sections_als  (parses V<N> ALS for sections JSON)
  │                           └─► align_engine.compute_aligned_positions
  │                                └─► _align_pair_landmark_aware  (THE swap decision door)
  │                           └─► align_engine.plan_fill_or_cut    (loop / fill planner)
  │                           └─► apply_loops.apply_loops          (clones clips for loops)
  │                           └─► mix_plan.build_mix_plan          (immutable contract)
  │                           └─► validate_arrangement_plan        (16-48 bar / 8 reps / 128 beats)
  │                           └─► als_generator (writes V<N+1>.als)
  │                           └─► ARRANGEMENT_REPORT_V<N+1>.json + MixPlan V<N+1>.json
  │
  ├─[Phase 3: automation]─►  python apply_automation.py
  │                        --als-path <Sections V<N+1>.als>
  │                        --report <ARRANGEMENT_REPORT_V<N+1>.json>
  │                        --audio-dir <Audio/>
  │                           └─► plan_transitions   (STANDARD / LONG_BLEND / QUICK_SWAP)
  │                           └─► build_track_automation  (Utility Gain + ChannelEq LowShelf)
  │                           └─► apply_als_patches
  │                           └─► validate_als.assert_valid  (HARD post-write gate)
  │                           └─► Final V<N+2>.als
  │
  ├─[Phase 3b: reconcile]─►  python validate_mix_plan_als.py
  │                        --mix-plan <MixPlan V<N+1>.json>  --als <Final V<N+2>.als>
  │                        (runbook-enforced, no Python caller — see §3 hole #5)
  │
  └─[Phase 4: visual review]── python transition_review_viz.py ...
                              python loop_review_viz.py ...
                              (MANUAL — never run by any orchestrator; see §3 hole #4)
```

Three things to internalise from this:

- The orchestrator is **only the Phase 0/1a entry point** — every line from `if not sections_layout:` onward is dead code that raises `"Full-mix mode has been retired"` (`orchestrator.py:843-847`).
- The real "wire" of the pipeline lives in **three top-level scripts** (`propose_arrangement.py`, `apply_automation.py`, `apply_loops.py`) plus the orchestrator. Most of the 84 KB of `propose_arrangement.py` and 47 KB of `apply_automation.py` is Phase 2/3 logic.
- The skill commands in `~/.claude/commands/mix.md` (and Codex/Antigravity mirrors) are the canonical entry point. There's **no `mix.md` in the repo** — the skill lives in the brain. `!SKILLS.md:14-16` says `/mix` is still a "future candidate," which is stale.

---

## 2. Action-by-action wiring map

### 2.1 The "I built this, is it alive?" table

| Action | Where the work lives | Where it's invoked | Wiring status | Evidence |
|---|---|---|---|---|
| Desktop analyse (MIK) | `automated_dj_mixes/desktop_analyzer.py::analyze_folder_with_mik` | `orchestrator.py:284-288` | ✅ Wired | Always runs unless `--skip-desktop-analyze` |
| Desktop analyse (Rekordbox) | `desktop_analyzer.analyze_folder_with_rekordbox` | `orchestrator.py:289-295` | ✅ Wired (legacy) | Skipped if `--stem-grid` |
| RB phrase + grid reading | `automated_dj_mixes/rekordbox_reader.py` | `orchestrator.py:329-344` | ✅ Wired (legacy) | Skipped if `--stem-grid`; lazy import |
| Stem-grid (per-beat detector) | `automated_dj_mixes/stem_grid.py::detect_beat_grid` | `orchestrator.py:388-466` | ✅ Wired (--stem-grid) | Replaces RB grid; one-clock injection |
| Tick-snapped timing | `Source/asd_onsets.py::ableton_onsets_sec` | `orchestrator.py:391-405` (lazy import) | ✅ Wired | Ableton .asd is independent ruler |
| Kick Detector V3 | `Source/kick_model_adapter.py::KickPresenceProvider` | `Source/stem_detector.py:157-200` | ✅ Wired (--kick-model) | Section-label path now uses raw V3 per the 18 Aug fix; `_kick_cues` still smoothed — see §3 #1 |
| Section detection (stem) | `Source/stem_detector.py::detect` | `orchestrator.py:706-740` | ✅ Wired (--stem-sections) | DETECT PNGs auto-emitted |
| Section detection (RB phrase) | `automated_dj_mixes/phrase_viz.py::segments_from_intervals` + `refine_segments` | `orchestrator.py:751-774` | ⚠️ DEAD in canonical /mix | Only runs in `--sections-layout` *without* `--stem-sections` — never the production combo |
| MIK enrichment (cues, key, BPM, energy) | `automated_dj_mixes/mik_reader.py::enrich_from_mik` | `orchestrator.py:519-532` (loop) | ✅ Wired | `MIKStore.db` is read; 0/20 tracks actually carry cue tags (per 17 Aug audit §2) |
| Hint loading | `automated_dj_mixes/cue_candidates.py::load_hints_file` | `orchestrator.py:542` | ✅ Wired | But the hint values are then never read by anyone downstream — see §3 #6 |
| Camelot sequencing + energy arc | `automated_dj_mixes/sequencer.py::build_harmonic_path` + `apply_energy_arc` | `orchestrator.py:571, 577` | ✅ Wired | MIK energy drives the arc |
| Master-file gate | inline regex `_MASTER_PATTERN` in `orchestrator.py:257-260` | `orchestrator.py:261-272` | ✅ Wired | Hard-stops on non-master WAVs |
| Rekordbox coverage gate | `orchestrator.enforce_rekordbox_coverage` | `orchestrator.py:493` | ✅ Wired (legacy) | Skipped if `--stem-grid` |
| Owned-grid coverage gate | `orchestrator.enforce_owned_grid_coverage` | `orchestrator.py:491` | ✅ Wired (--stem-grid) | Fail-closed on weak grids |
| Beatgrid quality gate | `Source/validate_beatgrid.py::enforce_beatgrid_quality` | `orchestrator.py:499-507` (lazy import) | ✅ Wired | Self-referential — see §3 #2 |
| Warp marker calc (per-beat) | `automated_dj_mixes/warping.py::calculate_warp_markers_from_beat_grid` | `orchestrator.py:649-656` | ✅ Wired | One clock via `rb_matches` |
| Warp mode selection | `warping.choose_warp_mode` (constants: `WARP_MODE_REPITCH=6`, `WARP_MODE_COMPLEX_PRO=4`) | `orchestrator.py:665-666` | ✅ Wired | Enum fix verified live through Producer Pal |
| Gain offsets (LUFS) | `automated_dj_mixes/automation.py::calculate_gain_offsets` | `orchestrator.py:638-641` | ✅ Wired | Mixes to the quietest, cap from config |
| ALS write (sections) | `automated_dj_mixes/als_generator.py::generate_session` | `orchestrator.py:829-836` | ✅ Wired | The only surviving live call to this |
| ALS write (arrangement/loops) | `Source/apply_loops.py::apply_loops` | `Source/propose_arrangement.py` (CLI) | ✅ Wired | `MAX_LOOP_REPEATS=8`, `MAX_LOOP_EXTENSION_BEATS=128`, preflighted |
| ALS write (automation) | `Source/apply_automation.py::insert_envelopes` | `apply_automation.py` (CLI) | ✅ Wired | Utility Gain + ChannelEq Low only |
| ALS validation | `Source/validate_als.py::assert_valid` | `apply_automation.py`, `apply_loops.py`, `apply_section_corrections.py` | ✅ Wired (3 callers) | The one reliable gate in the system |
| MixPlan building | `automated_dj_mixes/mix_plan.py::build_mix_plan` | `Source/propose_arrangement.py` | ✅ Wired (--mix-plan) | Schema 1.4 freezes tempo arc + grids + source hashes |
| MixPlan/ALS reconciliation | `Source/validate_mix_plan_als.py::reconcile` | runbook (no Python caller) | ⚠️ MANUAL | mix.md authorises skipping |
| Swap decision | `Source/align_engine.py::_align_pair_landmark_aware` | `Source/align_engine.py::align_pair:749` | ✅ Wired (Phase 2) | The only door into mix points. Bass cues, fills, loop windows, hint fields: all NOT in `_mix_cues` (17 Aug audit §1) |
| Loop planning | `align_engine.py::plan_fill_or_cut` | `align_engine.py::compute_aligned_positions:1368` | ✅ Wired (Phase 2) | Matched tail/head rule fires on 11/19 of 14.08.26 pairs |
| DETECT PNGs (per-track) | `Source/stem_detector.py::_visualize` | `stem_detector.detect` (auto, make_viz=True) | ✅ Wired (--stem-sections) | Replaces the 80-PNG blind pass |
| Blank preview PNGs | `automated_dj_mixes/waveform_preview.py::render_preview` | `orchestrator.py:606` | ✅ Wired (--previews-only) | Hint authoring surface |
| Hint-vs-section gate | `Source/validate_hints_vs_sections.py` | `.github/memory.json` references it (no caller in repo) | ⚠️ MANUAL | Reads `Sections Review/Sections_V<N>.json` — wrong file path (17 Aug audit §6) |
| Section regression gate | `Source/regress_section_detection.py` | (none — Golden Sections dir is empty) | ❌ VACUOUS | Returns 0/0 checks / PASS — §3 #3 |
| Pair history / learning | `Source/propose_arrangement.py::load_pair_history` | `propose_arrangement.py:943` | ❌ Loaded, unused | Changes zero output values (17 Aug audit §5) |
| Ground-truth YAML | `Data/Ground Truth/Sam Cue Points.yaml` | (none) | ❌ DEAD | 14/20 values null; TOOLBOX.md:216 claim "used for threshold tuning" is false |
| Cue candidate selection | `automated_dj_mixes/cue_candidates.py` (869 lines, 5 sources) | `orchestrator.py:19-26` (import only — `find_cue_candidates` never called) | ❌ ORPHANED in canonical /mix | The most over-engineered layer that never reaches a decision |
| Phase 4 visual review | `Source/transition_review_viz.py` + `Source/loop_review_viz.py` | (none — manual) | ❌ NOT WIRED | Phase 4 reads source WAVs (a prediction), not the actual rendered audio |
| Reporting | `automated_dj_mixes/report.py` | `orchestrator.py:28` (import only) | ❌ ORPHANED | `write_track_csv` + `write_transition_report` never called |
| Ableton OSC / Win32 UI | `Source/ableton_osc.py` + `Source/ableton_ui.py` | (none — Producer Pal MCP took over 2026-07-03) | ❌ ORPHANED | Last touched 12 Jun 2026 |
| Per-track viz (Phase 4b) | `Source/section_placement_viz.py` | (none) | ❌ ORPHANED | 25 Jun 2026, never used |
| Phase 1c blind validation | `Source/sections_blind_viz.py` | (none — `base = Path("Test Project/Black Book x Defected V2")` hardcoded) | ❌ HARDCODED | Silently skipped for any other project |
| Pre-mix arrangement (old) | `Source/arrange_sections.py` | (none) | ❌ ORPHANED | 20 May 2026 — predecessor to `propose_arrangement` |
| Pre-mix section corrections (old) | `Source/apply_section_corrections.py` | (none) | ❌ ORPHANED | 22 May 2026 — phase pipeline superseded it |
| Pre-mix section review (old) | `Source/validate_sections_review.py`, `Source/sections_compare_viz.py` | (none) | ❌ ORPHANED | All ≤22 May 2026 |
| Single-use debug scripts | `check_bass.py`, `check_returns.py`, `check_vlad_automation.py`, `find_bass_swaps.py`, `diagnose_sections.py`, `diff_sections.py`, `extract_*.py` (6 files), `analyze_batch.py`, `analyze_teaching.py`, `bass_detection.py` | (none) | ❌ ORPHANED | 14-22 May 2026; one-off diagnostic tools |
| Visual hint cross-check | `Source/verify_grid_bar_parity.py`, `Source/probe_als_warp.py`, `Source/probe_onset_lag.py` | (none) | ❌ ORPHANED | Replaced by `validate_beatgrid.py` |
| One-off V2/V5 review tools | `analyze_correction_diff.py`, `extract_musical_landmarks.py`, `isolate_sections_tracks.py`, `materialize_section_details.py` | (none in repo — used on-demand for the 16.07.26 / Final V5 cycles) | 🟡 LIVING WORKBENCH | On-demand, not in any skill, but they're the proof-isolation toolset |
| Review scripts (held-out) | `setup_heldout_replay.py`, `setup_car_mix.py`, `seal_listening_test.py`, `build_ab_comparison.py`, `alignment_feasibility.py` | (none — invoked from `/mix` skill or by hand) | 🟡 LIVING WORKBENCH | 12 Aug 2026, used for the 14.08.26 hold-out |
| Render + LUFS + clipping | (nothing — pipeline never renders) | (none) | ❌ MISSING | Per 17 Aug audit §6 |
| Pre-render kick-flam probe | `Source/probe_render_flam.py` | (none) | ❌ ORPHANED | Script exists, exactly the class of bug Sam heard in the car |
| Kick grid probe (debug) | `probe_grid_vs_ableton.py`, `probe_als_arrangement.py`, `probe_stem_kick_grid.py` | (none) | ❌ ORPHANED | 11-12 Jun 2026, replaced by `validate_beatgrid.py` |

### 2.2 The call graph

```
                                skill /mix
                                    │
        ┌───────────────────────────┼──────────────────────────────────┐
        │                           │                                  │
   orchestrator.py              propose_arrangement.py          apply_automation.py
   (Phase 0 / 1a)               (Phase 2)                       (Phase 3)
        │                           │                                  │
        ├── desktop_analyzer       ├── align_engine                   ├── find_bass_swap
        │     ├ analyze_with_mik   │     ├ align_pair                ├── plan_transitions
        │     └ analyze_with_rb    │     │   └ _align_pair_landmark  ├── build_track_automation
        │       (REKORDBOX DISABLED├ _mix_cues  ◄── THE SWAP DOOR    └── insert_envelopes
        │        if --stem-grid)   │     ├ plan_fill_or_cut                │
        │                          │     └ compute_aligned_positions       ├─ validate_als (gate)
        ├── stem_grid            │                                       │
        │     └ detect_beat_grid ├── apply_loops                          └→ Final V<N+2>.als
        │                          │     └ apply_loops  (preflighted)
        ├── kick_model_adapter    │
        │     └ (V3)            ├── mix_plan
        │                          │     └ build_mix_plan
        ├── stem_detector         │
        │     └ detect           ├── arrange
        │         ├ _model_kick   └── report (manual, sometimes)
        │         └ _visualize
        │
        ├── als_generator.generate_session
        │
        ├── validate_beatgrid (gate)
        │
        ├── waveform_preview (Phase 0)
        │
        └── ❌ cue_candidates (imported, never called)
            ❌ report          (imported, never called)
```

---

## 3. Holes (with file:line evidence)

### 3.1 Kick Detector V3 still using the smoothed (bridged) path for `_kick_cues` — regression unfixed in one place

The 18 Aug commit `def5062` fixed `_assign_labels` to read V3's raw presence. But **`_kick_cues` at `stem_detector.py:203-224` still reads `_model_kick_presence_per_beat` via `_kick_on_bar`** (built at `stem_detector.py:437` before the assignment fix) — and per the 18 Aug investigation, the smoothed `section` field with `fill_off_beats=6` erases 16-bar genuine dropouts. The `kick_cues` array for Double Dutch is `[]` (zero dropouts anywhere in 281 s). Section-label classification is now correct; boundary-cutting isn't. **Smallest fix:** in `stem_detector.py:437`, branch on the active presence source and use `.raw` for V3 the way the 18 Aug commit switched the label path to use `.raw`.

### 3.2 Beatgrid gate in `--stem-grid` mode is self-referential (17 Aug audit §6 confirmed)

`grid_vs_kick_ms` is computed by `stem_grid.detect_beat_grid` from the same kick onsets it fitted the grid to (`stem_grid.py:94,123`). It's then handed to `validate_beatgrid.py:394` as the score. The independent `.asd` tick ruler *is* computed and then **deliberately discarded** for stem grids (`:249-258`). Worse, `--allow-bad-grids` overrides the gate and `SKIP` verdicts pass. **Fix:** in `validate_beatgrid.py`, require a minimum `.asd` disagreement offset below threshold even for `stem_fitted=True`; currently the gate just trusts the self-score.

### 3.3 Section-detection regression layer is vacuous (Golden Sections path was the wrong answer)

`regress_section_detection.py:151-153` returns `0 checks / PASS` when `Documentation/Golden Sections/` contains only a README. The original plan was to bless Sam-accepted sections from 16.07.26 V2 and 14.08.26 V3 as ground truth, but Sam pushed back (2026-08-18): section detection is genuinely ambiguous, the 16.07.26 V2 specifically had subjective judgments baked into Sam's blessing ("roadblock consolidated from 30 visual fragments", "missed 16-beat dropout manually added"), and the script was originally written for a 22.05 one-off — not as a permanent gate. **Better fix (property-based, not ground-truth-based):** new file `Tests/test_kick_cues_property.py` asserts `len(kick_cues) > 0` for any track where the cached Demucs drums envelope has a contiguous run of ≥8 bars below the track's `_solid_kick_level * KICK_ON_FRAC`. Catches the Double Dutch class of regression (signal exists, detector emits nothing) without requiring blessed ground truth. Run before and after the `stem_detector.py:437` fix (§3.1) to prove both. See §6 #2 for the priority-order item.

### 3.4 Phase 4 visual review is not invoked by the orchestrator

`transition_review_viz.py` and `loop_review_viz.py` are standalone scripts — neither orchestrator nor propose_arrangement nor apply_automation invokes them. Per 17 Aug audit §6, the "final visual review" reads source WAVs (a prediction), not the rendered output. The most valuable Phase 4 PNG is the per-transition image showing the actual kick column alignment of the post-write ALS — but no one calls the script. **Fix:** the `/mix` skill Phase 4 should auto-invoke `transition_review_viz.py` for every transition; the loop viz for every transition with loops. The cost is a 10-second PNG render per transition.

### 3.5 `validate_mix_plan_als.reconcile()` has no Python caller

`mix.md:344` (per 17 Aug audit) authorises skipping it. So reconciliation is runbook-enforced; a tired Sam or a delegated run can produce a mix the planner said yes to but the post-mutation verifier said no to. **Fix:** `apply_automation.py`'s post-write should `subprocess.run` `validate_mix_plan_als.py` (or import the function) as the last line — same as it already does for `validate_als.assert_valid`.

### 3.6 Hints are loaded, then dropped (17 Aug audit §4 confirmed)

`orchestrator.py:540-546` loads `track_hints_data`. The hint values (`first_drop_sec`, `first_break_sec`, `outro_start_sec`, `last_bass_drop_sec`) **never reach `align_engine`** — `propose_arrangement.py:1124` explicitly prints a warning that `intro_skip_bars` and `loop_source_sec` are dead behind `USE_ALIGN_ENGINE=True`. On 14.08.26, 15/40 outgoing hint values (38%) point at a bar that is not a section boundary. The hint authoring was real work; the data is just being thrown away. **Fix (17 Aug audit §4, ~15 lines):** add hint fields to `TrackInfo`, copy as bars onto the aligner's `Track`, emit at weight 7 above `section:drop:start`'s 6 in `_mix_cues`. Additive (`add()` merges by `max(weight)`), no-op when no hints file exists.

### 3.7 The cue-candidates layer is structurally orphaned in canonical /mix

869 lines of `cue_candidates.py` (5 sources, 5 cue types, confidence + reasons + visual_hint precedence) are imported by the orchestrator (`:19-26`) but **`find_cue_candidates` is never called** by any of {orchestrator, propose_arrangement, align_engine}. The 17 Aug audit's measurement is sharp: `_mix_cues` reads 4 fields, and cue candidates are dead. **Right now, the layer is misleading documentation** — TOOLBOX.md:60-71 describes it as if it's live.

### 3.8 `cue_candidates._is_visual_hint` precedence rule is preserved but the visual hints are never consulted

The precedence rule (visual_hint wins over MIK/RB/amplitude) is implemented in `cue_candidates.py:331`. Combined with §3.6, the hint system is currently scaffolding for a feature that's not built.

### 3.9 `validate_hints_vs_sections.py` reads the wrong file (17 Aug audit §6 confirmed)

Opens `Sections Review/Sections_V<N>.json`. The aligner reads `_Stem Analysis/SECTIONS_STEM_*.json`. On 14.08.26 the hint-gate's last run was 15:01 against V1 while V2 was written 15:26 — 7/20 tracks differing. **Fix:** swap the path to the SECTIONS_STEM glob, and add a freshness check (mtime of hint vs mtime of stem JSON) to fail-closed on stale.

### 3.10 `phrase_viz` per-beat energy layer is dead in canonical /mix

`orchestrator.py:751-774` (the `elif rb_match:` branch with `extract_track_features`, `build_intervals`, `refine_segments`) only runs when `--sections-layout` is set *without* `--stem-sections`. The canonical `/mix` is the opposite combination. So `features.py`, `phrase_viz.build_intervals`, `refine_segments`, `_split_drop_with_fills`, `_split_intro_build_zone`, and `rekordbox_waveform.parse_waveform` are all **dead in production** — that's ~50% of the analysis infrastructure with no production consumer. Per-beat bass energy could be a real "is this drop really a drop" check, but it's currently invisible to the aligner.

### 3.11 `compute_natural_positions` family in `propose_arrangement.py` is dead behind `USE_ALIGN_ENGINE=True`

Lines 461, 433, 446 all dead in the canonical path. 17 Aug audit §2 confirmed. **Fix:** delete or quarantine.

### 3.12 `_handoff_candidates` and `_score_lineup` in `align_engine.py` are unreachable

`_align_pair_landmark_aware` early-returns to itself when either track has landmarks (`:482-483`); 20/20 corpus tracks have landmarks; therefore `_handoff_candidates` (`:270-293`) and `_score_lineup` (`:295-309`) **never execute in production**. The `LIKE_ENERGY` concept is dead. The same `fill` ambiguity hazard (`:441` "kick dropouts mark changes and are good points to mix" above a value used only to *avoid* those regions) means 191 fills currently reach zero mix decisions. **Fix (~2 lines per 17 Aug audit §3):** wire `fills` as anchors.

### 3.13 Phase 1d (visual blind validation) has no enforcement

The pipeline never asserts that a BLIND_VALIDATION_V<N>.md exists with zero `⚠ off N` rows. A project can land Phase 1a with bad chops and silently march into Phase 2.

### 3.14 The orchestrator's default mode is a RuntimeError

`python -m automated_dj_mixes.orchestrator --input ... --output ...` with no flags raises `RuntimeError("Full-mix mode has been retired. Use the three-phase /mix pipeline...")` at `orchestrator.py:843-847`. **Fix:** default to `--sections-layout` (the only surviving mode) and let `--no-sections-layout` raise.

### 3.15 The hint gate is bypassed under `--sections-layout`

`orchestrator.py:621` reads `if not no_hints_required and not sections_layout:`. A `python -m automated_dj_mixes.orchestrator --sections-layout` run with no `Hints/track_hints.json` and no `--previews-only` and no `--no-hints-required` sails through to the ALS write. Combined with §3.6, hints do nothing in the canonical path today.

### 3.16 `desktop_analyzer` exceptions are swallowed, with a misleading "WARNING" prefix

`orchestrator.py:296-300`:
```python
except Exception as e:
    print("  WARNING: desktop analysis did not complete cleanly:")
```
The downstream master-file gate then runs against un-enriched tracks. The pipeline then either fails the gate (if you didn't `--allow-non-master`) or proceeds with empty MIK enrichment (if you did). **This is the silent-degrade class.** The 17 Aug audit caught a duplicate of this bug for the `enforce_rekordbox_coverage` path.

### 3.17 `_MASTER_PATTERN` is brittle and only matches two families

`orchestrator.py:257-260`: `r"(24\s*Bit\s*MASTER|SW\s+V\d+)"`. Will reject `Final Master.wav`, `Master 24bit.wav`, `Master 2024.wav`, `Mastered.wav`. The 23.06.26 run had to use `--allow-non-master` because the third-party tracks didn't match. **Fix:** add a positive MIK-DB presence check — if the track appears in `MIKStore.db` with key + BPM, it's master-quality regardless of filename.

### 3.18 `validate_als` is the only reliable gate

`apply_automation.py`, `apply_loops.py`, `apply_section_corrections.py` all call `validate_als.assert_valid` after write. The orchestrator at `orchestrator.py:829` does *not* — its `generate_session` is the only Phase 1a write path and bypasses the gate. **Fix:** orchestrator's `generate_session` should call `validate_als.assert_valid` after the write.

### 3.19 BPM-mode strategy is the single point of failure for tempo-arc adoption

`Source/tempo_curve.py` exists, `MixPlan` schema 1.4 freezes the tempo arc. But `--tempo-arc` is opt-in and the canonical `/mix` doesn't pass it. Only `fixed_center` is implemented. `progressive_arc`, `local_follow`, `hybrid` are described in the plan but not built.

### 3.20 No CI on `.github/workflows/`

`ls .github/` shows only `ai-activity-log.md`, `copilot-instructions.md`, `memory.json`. No `workflows/` directory. The full test suite (231 passed, 6 skipped) runs only on Sam's command. **Fix:** 6-line `pytest -q` workflow on PR.

### 3.21 `ableton_osc.py` and `ableton_ui.py` are abandoned

Last touched 12 Jun 2026. Producer Pal MCP replaced them. 4-6 KB each. Quarantine to `Source/Archive/`.

### 3.22 The `__pycache__` carries phantom compiled modules

`Source/automated_dj_mixes/__pycache__/` contains `auto_analyze.cpython-314.pyc`, `track_viz.cpython-314.pyc`, `transition.cpython-314.pyc`, `transition_viz.cpython-314.pyc`, `validation.cpython-314.pyc` — no corresponding `.py` files. Leftovers from deleted/renamed modules.

### 3.23 The "30-second tail anchor" rescue pass + the "sustained-to-end outgoing" rule are not built

The 14 Aug Revoloution blocker: outgoing track with no outro, no break within final 32 bars, hits `MIN/MAX_SWAP_PROGRESS` ceiling because `track_end` is rejected as a valid outgoing anchor. **Fix (per 17 Aug audit §7 step 5 + 14 Aug NOTE):** either raise `MAX_SWAP_PROGRESS` for sustained-to-end tracks, or add a dedicated fallback path.

### 3.24 The "sustained-to-end outgoing" rule (14 Aug) is not built

Same as §3.23 with the other half: for an outgoing with full energy to the file's end, the swap should be allowed to anchor near `track_end` using the incoming's first drop as the pairing anchor, without routing through the general progress-ceiling check.

### 3.25 Bass-kill-to-silence is the 11% silent failure class

17 Aug audit §1: 28/245 transitions (11%) write bass-kill automation to a track with no bass to kill. A 20-line pre-check on `align_engine.Track.bass_regions` for the overlap window would catch this and downgrade to a no-bass-kill transition style.

---

## 4. Upgrades and quality improvements

### 4.1 Section detection — better with what's already in the codebase

**a) Use V3 raw for `kick_on_bar` in `_kick_cues`** (see §3.1). One if-branch in `stem_detector.py:437`.

**b) Use `musical_landmarks` to inform `kick_cues`.** A section that ends with a 16-beat dropout should have its boundary at the dropout, not the next bass-presence toggle. In `_snap_merge` (`stem_detector.py:107-116`), prefer boundaries that align with `musical_landmarks` dropout/return points if they're within 4 bars.

**c) Bring back the per-beat RB path as a cross-check.** `phrase_viz.segments_from_intervals` + `refine_segments` is dead, but the *per-beat bass band* from `features.py` is genuinely useful as "is this drop really energetic." A `bass_energy_consistency_check(track_features, sections)` that emits a warning when a labelled "drop" has low per-beat bass would catch the "fake first drop" case the existing `_collapse_fake_first_drop` is trying to handle (`phrase_viz.py:608`).

**d) Cure-style tempo wobble** (24 Jun NOTE: "variable stem grid for Cure's wobble"). `detect_beat_grid` produces a single constant BPM. A 32-beat sliding-window BPM estimator on the post-snap per-beat activations would let Cure's wobble become Complex (not Re-Pitch) without losing the grid.

**e) Half-time / 4x4 tracks.** Half-time house and trap fool the kick detector because the kick lands on every other beat. A `kick_median_period` check: if median period > 1.5 × expected, this is half-time, the detector should look at bar-2 kicks not beat kicks.

**f) Cross-track vocal clash detection.** A 30-line `vocal_clash_check` that takes a list of `SECTIONS_STEM_*.json` files + an `ArrangementPlan` and emits "T3 vocals 0:30-1:00 overlap T4 vocals 0:45-1:15" warnings. The 17 Aug audit notes 12/245 pairs (5%) stack vocals.

### 4.2 Transient / warp detection improvements

**a) Snap to Ableton .asd, then re-derive BPM.** Currently the snap is final. The median period post-snap can shift by ~0.5 ms, which can flip the BPM authority. A `refit_bpm_after_snap(beat_times_ms)` that recomputes the median period *post-snap* would tighten the grid.

**b) Validate the stem grid against the independent .asd ruler, even when stem-fitted.** Per §3.2, the gate discards the .asd ruler for stem-fitted grids. The right rule is: stem grid is authority *if and only if* it agrees with .asd within 5 ms.

**c) The `bass_stem` from Demucs is unused.** `kick_model_adapter.separate_envelopes_and_drums` already returns `drums_env` and `bass_env` separately. The bass env could be the authoritative "is the bass actually playing here" signal — independent of the drums envelope — for `_assign_labels.is_drop`.

**d) Warp mode tiebreaker for borderline BPMs.** `choose_warp_mode` uses +/- 1 BPM. A 2-bar sliding window on `beat_times_ms` → local slope → Re-Pitch cost is computable in 10 lines and is more honest than constant.

### 4.3 Transition quality — closes the loop on 17 Aug audit §1

**a) Wire the four hints at weight 7** (audit §4, ~15 lines). Single highest-leverage, lowest-risk change.

**b) Wire `fills` as anchors** (audit §3, ~2 lines). 191 signals currently reach zero mix decisions.

**c) Give `bass_out` a cue in `_mix_cues` at weight 6-7** (audit §3, larger behavioural change). The ablation shows 0/380 decisions change when `bass_out` is deleted.

**d) Build the "30-second tail anchor" rescue pass** + **"sustained-to-end outgoing" rule** (see §3.23/§3.24).

**e) `check_bass_swap_writes_to_silence`** (17 Aug audit §1). 20-line pre-check on `align_engine.Track.bass_regions` for the overlap window.

### 4.4 Automation / Phase 3 improvements

**a) Loudness-at-overlap smoothing** (Production Polish Backlog §1). 0.25-0.5 dB dip on the louder side of an overlap + gentle low-shelf dip. Requires reading both sides' LUFS at the swap.

**b) Bass-switch energy preservation** (Production Polish Backlog §2). Measure outgoing bass vs incoming bass at the swap beat; boost the incoming's low-shelf to match; fade the boost back to 0 dB over N bars.

**c) Snap bass swap to phrase lines (16-beat)** when in a drop, in addition to existing `apply_automation.snap_to_section_boundary`.

**d) Auto-bounce + post-render validation** (17 Aug audit §6 step 6). A `render_and_validate.py` that calls Producer Pal to render, runs `probe_render_flam.py` + a LUFS-over-time check + a clipping detector, writes `RENDER_REPORT.md`, and fails the build on threshold exceedance.

**e) Enable Rule 3 (two-stage volume)** (per AI_CONTEXT 11 Jun: "explicitly disabled pending ≥3 observations"). The held-out corpora (16.07.26 V2, 14.08.26 V3, 25.06.26 V3) probably have enough observations now.

### 4.5 Simplifications and cleanups (low blast radius)

**a) Delete or quarantine the orphan scripts** (§3.7, §3.10, §3.11, §3.12, §3.21). `Source/Archive/` exists; the dead code is ~150 KB of the ~250 KB source.

**b) Build the property-based kick-cues regression test** (per §3.3 and §6 #2). `Tests/test_kick_cues_property.py` asserts the property that would have caught Double Dutch. ~40 lines + a corpus-loader helper. Replaces the older plan of blessing sections as ground truth (which doesn't work for ambiguous detection outputs).

**c) Make the orchestrator's default mode sections-layout.** Per §3.14.

**d) Wire `validate_als.assert_valid` into orchestrator's `generate_session` call.** Per §3.18.

**e) Fix the desktop_analyzer exception handler to be loud.** Per §3.16. Either re-raise (let the gate catch) or set a sentinel the rest of the pipeline checks.

**f) Clean up the `__pycache__` orphans.** Per §3.22.

**g) Add a CI workflow that runs `pytest -q` on PR.** Per §3.20.

**h) Update `Documentation/.github/copilot-instructions.md`** — it explicitly says "stale, May 2026 priorities below."

---

## 5. The cherry — new features not in the project

### 5.1 Mix Review dashboard (small, high-impact)

A single HTML page per mix: DETECT PNGs + per-transition review PNGs + per-loop review PNGs + ARRANGEMENT_REPORT + MixPlan + Reconciliation tables + LUFS-over-time line chart + per-transition swap / overlap / loop budget summary + vocal clash warnings + bass-kill-to-silence warnings. One command: `python render_mix_dashboard.py --project <path> --output <html>`. Replaces the 3 separate PNG folders and the Markdown REVIEW_VN.md template.

### 5.2 BPM mode: progressive arc (medium, unlocks new mix shapes)

The plan describes `progressive_arc` (BPM walks 122 → 126 across the mix), `local_follow` (each track plays near its native BPM with tiny ramps), `hybrid` (mix of the two). Only `fixed_center` is implemented. The tempo_curve + MixPlan freeze work supports the strategy; the write path into the ALS doesn't.

### 5.3 Auto-bounce with audio gate (medium, closes the largest hole)

Pipeline writes the ALS but never renders. The defects that reach Sam's ears unchallenged are: kick flam between overlapping tracks, cumulative warp drift, clipping from summing, unlevelled mix, bass-kill written to the wrong target. A `render_and_validate.py` that:
1. Calls Producer Pal to render the ALS to WAV
2. Runs `probe_render_flam.py` to flag kick flams
3. Runs a LUFS-over-time check
4. Runs a clipping detector
5. Writes a `RENDER_REPORT.md` and FAILS the build on threshold exceedance

…would close this.

### 5.4 Trust ledger per track (medium, opens the corpus)

A per-track JSON ledger in `Data/Track Trust/` that records mix version, position in sequence, transition style, overlap bars, loop usage, hand-corrections (from `analyze_correction_diff` output), time-since-last-used, mean / median per-corpus metric. Then `build_harmonic_path` and `apply_energy_arc` can bias toward "tracks we haven't sequenced recently" and "tracks that historically worked as openers / closers."

### 5.5 Listen-test variance (small, surfaces hidden assumptions)

A scripted pre-listening test that runs on every mix: render 4 candidate bass-swap positions (the chosen one + 3 nearby), play them back-to-back as a single WAV with a 1-second dead-air marker between, log the file. The operator listens blind, picks which one Sam's ear actually prefers, the pick is recorded as a new transition in the trust ledger.

### 5.6 Phrase-aware genre tagging (small, opens Phase 2 style selection)

A 30-line classifier that takes `SECTIONS_STEM_*.json` features (drop count, break frequency, outro length, intro length, total length) and emits one of: `{deep, tech, afro-latin, soulful, classic, disco, dnb-derivative, half-time, jackin, melodic}`. Then `propose_arrangement` can bias transition style by genre. No new detector needed; the data is already in the JSONs.

### 5.7 Cross-track vocal clash detector (small)

30 lines, addresses the 5% failure case the 17 Aug audit identified. Reads `signals.vocal_regions` from each track's SECTIONS_STEM JSON, intersects with the `ArrangementPlan.overlap_windows`, emits warnings. Gate on the overlap window only. Doesn't need to render.

### 5.8 Beat-grid confidence ledger (small)

A `grid_confidence.json` artifact that records, per track: `grid_source` (stem | RB | tick-fitted), `grid_vs_kick_ms`, `.asd_disagreement_ms` (when available), `phase_R`, `bpm` with `tempo_confirmed` boolean. Sam-facing page can show "12/12 grids PASS but 3 of them are at 1.1 ms — your ear may catch what the ruler didn't."

### 5.9 Mix in reverse mode (small, useful for client A/B)

A `reverse_mix.py` that takes a finished Sections V<N>.als and produces V<N+1>_reversed.als with the same tracks in reverse sequence, transitions adjusted, intros/outros respected.

### 5.10 Pre-bounce rehearsal (small, fast)

A dry-run mode that doesn't write the ALS but computes and prints: total mix bars, every transition's overlap, every loop's clip count, the worst-case LUFS, the max bass boost needed, the BPM-mode decision, the template track count required. The operator gets a "would-be mix" preview before commit.

### 5.11 Ableton-side auto-mix (ambitious, the real product)

Once the ALS is correct, the natural next step is *automating* Ableton to render the bounce — the per-track warps are written, the loop extensions are cloned clips, the bass-kill automation is in place. The render queue is one Producer Pal call. Once that's automated, the next is *automating the listen-and-tweak*: render, run a kick-flam + LUFS check, identify the worst transition, propose a one-parameter tweak, re-render, compare, accept or reject. This is the closed loop that turns the project from "Automated DJ Mixes" into "Automated DJ Mix Engineer." It's the only path to the 5M-track BGM market (Stephan @ mbc.eu.com) and the curated-mix labels (Defected, Perfect Havoc, Good Company, Perfecto, Black Book, Catch & Release) at the velocity the market needs.

---

## 6. Top-10 priority order

The 10 highest-leverage, lowest-blast-radius items, in execution order. Each item is small (1 line to a few dozen), addresses a hole the audit caught, and is sized to fit a single focused agent turn.

**Full context for each item is in §3 and §4 above. Read both before acting.**

1. **Fix `stem_detector.py:437` to feed `_kick_cues` from V3 raw presence** (audit §3.1, 18 Aug kick investigation). When the active presence source is V3, read `KickPresenceReadout.raw` not `.section` for the `kick_on_bar` that drives `_kick_cues`. **Correction (Claude, 2026-08-18): does NOT close the Double Dutch 16-bar dropout on its own** — the attenuation diagnosis measured V3's raw confidence at P(kick ON)=0.94 throughout bars 31-47 (mean 0.660, never below the 0.30 threshold); the kick's transient survives even though its sub-bass body is gone, so raw vs smoothed makes no difference there. Still a real, separate bug worth fixing on its own merits. Double Dutch's actual closure is the mix-energy break detector, now built and verified: `_energy_cues()` in `stem_detector.py`, commit `6a717f4` on `feat/mix-energy-break-detection` (off `10bd081`) — corpus-wide sweep across all 20 tracks found 1 new cut (Double Dutch bars 32-48, the intended target) and 0 false positives, 245/6 passed. Single-branch edit for the raw-presence half; the energy half is done and just needs merging to main.

2. **Property-based kick-cues regression test, new file `Tests/test_kick_cues_property.py`** (audit §3.3, supersedes the 17 Aug §7 step 0 "bless Golden Sections" plan which Sam rejected 2026-08-18 as ambiguous and one-off). For the 14.08.26 corpus (20 tracks), assert: `len(kick_cues) > 0` for any track where the cached Demucs drums envelope has a contiguous run of ≥8 bars below `_solid_kick_level * KICK_ON_FRAC`. Catches the Double Dutch class of regression (signal exists, detector emits nothing) without requiring blessed ground truth. ~40 lines + corpus-loader helper. Run before and after #1 to prove both: pre-fix Double Dutch fails; post-fix passes.

3. **Wire the 4 hint fields at weight 7 in `align_engine._mix_cues`** (audit §3.6, 17 Aug §4). Add `first_drop_sec` / `first_break_sec` / `outro_start_sec` / `last_bass_drop_sec` to `propose_arrangement.TrackInfo`; copy as bars onto `align_engine.Track`; emit in `_mix_cues` at weight 7 above `section:drop:start`'s 6. Additive (`add()` merges by `max(weight)`), no-op when no hints file exists. ~15 lines.

4. **Wire `fills` as anchors** (audit §3.12, 17 Aug §3). 191 detected fills currently reach zero mix decisions. ~2 lines: register the fill cue in `_mix_cues` the same way `musical_landmarks` is already registered at `align_engine.py:277-281`.

5. **Give `bass_out` a cue in `_mix_cues`** (audit §3.7, 17 Aug §1). Ablation shows 0/380 decisions change when `bass_out` is deleted. Wire it in. Larger behavioural change than #3-4; do after the regression net (#2) and A/B against held-out corpus.

6. **Add `validate_als.assert_valid` after orchestrator's `generate_session` + auto-call `validate_mix_plan_als.reconcile()` from `apply_automation.py`** (audit §3.5, §3.18). The orchestrator's only surviving live write path bypasses the gate; the reconciliation has no Python caller. Two ~3-line changes.

7. **Auto-invoke Phase 4 viz in the `/mix` skill** (audit §3.4). The skill's Phase 4 should call `transition_review_viz.py` for every transition and `loop_review_viz.py` for every transition with loops. Adds ~10s of PNG render per transition. The skill edit lives in `~/.claude/commands/mix.md` + Codex mirror (frozen sync list).

8. **Make sections-layout the orchestrator's default; raise on `--no-sections-layout`** (audit §3.14). The bare CLI entrypoint is currently a RuntimeError. Default to `--sections-layout` (the only surviving mode); let `--no-sections-layout` raise. One line in `main()` + one argparse change.

9. **Build the "30-second tail anchor" rescue pass + the "sustained-to-end outgoing" rule** (audit §3.23/§3.24, 17 Aug §7 step 5, 14 Aug Revoloution blocker). Either raise `MAX_SWAP_PROGRESS` for sustained-to-end tracks, or add a dedicated fallback path that anchors the swap on the incoming's first drop when the outgoing has no outro and no break in its final 32 bars. Held-out replay before becoming policy-default (same discipline as `sam_v1`).

10. **Complete the Rekordbox removal** (Sam, 2026-08-18: "done away with"). Quarantine or delete every RB-dependent code path the canonical `/mix` no longer uses. Includes: `Source/rekordbox_reader.py` → `Source/Archive/`; `automated_dj_mixes/rekordbox_waveform.py` → `Source/Archive/`; `desktop_analyzer.analyze_folder_with_rekordbox` + `_drive_modern_folder_dialog` → delete; `enforce_rekordbox_coverage` → delete; the orchestrator's RB-enrichment loop at `orchestrator.py:323-346` → delete; `analysis.enrich_from_rekordbox` → delete the function (shim keeps the rest of `analysis` via the audio_analysis package); the `phrase_viz` per-beat RB path (`build_intervals`, `refine_segments`, `segments_from_intervals`, `_split_intro_build_zone`, `_split_drop_with_fills`, `_refine_first_drop_start`, `_collapse_fake_first_drop`, `_trim_short_breaks`, `_refine_outro_start`, `_absorb_short_segments_before_outro`) → delete; `phrase_viz.segments_from_stem_sections` is the only surviving path. **Blast radius:** high (touches 7 files), but every touched line is dead. **No code should remain in the active pipeline that depends on a Rekordbox desktop install or `.DAT`/`.EXT` files.** This is the last item by design — its own focused PR.
