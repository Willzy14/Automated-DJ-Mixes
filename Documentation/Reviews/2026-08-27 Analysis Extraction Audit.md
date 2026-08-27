# Analysis Extraction Audit — what we compute, what we keep, who reads it

**Date:** 2026-08-27 · **Brain:** Claude (evidence by grep; every claim carries a file ref) · **Reviewed:** Codex adversarial completeness pass (verdict NEEDS CORRECTION; all findings verified against source and folded in — see Review trail) · **Commissioned by:** Sam — "audit all the things we extract and throw away, and find places downstream that could be using stuff we already extract."

**Thesis:** the extraction layer is rich and mostly cached; the decision layer reads a narrow slice of it. The main door is `_mix_cues` in [align_engine.py:108-136](../../Source/align_engine.py) — its own comment: it reads "just four things: track_start, track_end, section bounds and musical_landmarks. Everything else the analysis layer produces was loaded, drawn on review pictures, and locked out of the decision." That is *nearly* absolute rather than absolute — `stems_on`, fills-as-loop-hygiene, loop windows and the loop-quality metrics act outside that door — but the per-flag wiring pattern (`CueConfig` + `--cue-signals`, each signal diffable alone against `test_alignment_baseline.py`) means every opportunity below is a small, individually-measurable step.

---

## A. Inventory — signal by signal

Legend: **LIVE** = affects production decisions today · **FLAG-OFF** = wired, default off · **IDLE** = persisted, no decision consumer · **DISCARDED** = computed then thrown away · **DEAD-PATH** = only consumer is code the canonical `/mix` never calls.

### Demucs separation (one pass per track, cached)
| What | Persisted? | Consumers | Status |
|---|---|---|---|
| Drums raw audio | `__drumsstem.npz` | Kick V3 ([kick_model_adapter.py:383](../../Source/kick_model_adapter.py)); beat-grid pass since `98f8d02` ([stem_grid.py:33](../../Source/automated_dj_mixes/stem_grid.py)) | LIVE |
| Bass / vocals / other raw audio | no | — (`detect_beat_grid` discards the bass it is handed) | **DISCARDED** |
| 5 RMS envelopes (drums/bass/vocals/other/mix, 0.1 s hop) | `__stemenv.npz` | section detection ([stem_detector.py:833](../../Source/stem_detector.py)); loop-quality measurements (`align_engine.evaluate_loop_quality` ~[:448-520](../../Source/align_engine.py), cache resolved via [apply_loops.py:586](../../Source/apply_loops.py)); viz | LIVE |

### Tier A augmentation ([_tier_a_features.py:1-60](../../Source/_tier_a_features.py), arrays persisted in `__stemenv.npz`)
| What | Consumers | Status |
|---|---|---|
| Stereo width (side/mid) | `detect(width_cues=True)` boundary cues — reads width + mix RMS only ([stem_detector.py:929](../../Source/stem_detector.py)) | FLAG-OFF |
| 3-band envelopes, L/R correlation | ONLY the tiera loop-similarity gate term ([align_engine.py:58](../../Source/align_engine.py)) — not width_cues | FLAG-OFF |
| Per-bar vocal mask | written to `signals.tier_a.vocal_active_regions` ([stem_detector.py:1193-1197](../../Source/stem_detector.py)); the aligner reads only base `vocal_regions` ([align_engine.py:617](../../Source/align_engine.py)) | **IDLE** (v1 wrongly called it live) |
| Loop self-similarity (base stems) | tiera loop-similarity term | FLAG-OFF (AND-vs-replace decision carded) |

### Beat-grid pass (stem_grid / Audio Analysis Toolkit)
| What | Consumers | Status |
|---|---|---|
| Grid (per-beat times, downbeat, BPM), `grid_vs_kick` | everything; gate | LIVE |
| **Kick attack onsets** (sample-accurate `refine_to_click` times) | grid fit, then gone (bakeoff cache aside) | **DISCARDED** |
| Snare onsets | grid fit only | **DISCARDED** |
| `.asd` ticks — grid snapping upstream | `snap_grid_to_asd` | LIVE |
| `.asd` ticks — gate phase ruler | computed and **reported, deliberately never enforced** for stem-fitted grids: `stem_fitted` forces advisory ([validate_beatgrid.py:248-266](../../Source/validate_beatgrid.py)), per the ruler hierarchy (kicks > ticks > librosa — ticks sit on anticipating percussion). Canonical grids are stem-fitted, so **the 2026-08-17 observation STANDS for production grids**; v1 of this audit wrongly called it stale. Tick quartile spread `_q` is additionally computed and discarded ([:233](../../Source/validate_beatgrid.py)) | informational by design |

### Kick Detector V3  *(v1 had this row REVERSED)*
Raw presence → drives `kick_on`, section classification, `kick_cues`, landmarks ([stem_detector.py:858-872](../../Source/stem_detector.py): "THE FIX — was the smoothed/section signal") — **LIVE**. Smoothed presence → returned and immediately ignored — **DISCARDED**.

### Section-detection JSON (`SECTIONS_STEM_*.json`, writer at [stem_detector.py:1140-1215](../../Source/stem_detector.py))
| Signal | Stated purpose | Actual consumers | Status |
|---|---|---|---|
| sections (labels+bars) | structure | arrangement, align, viz, hints gate | LIVE |
| `stems_on` per section | which stems present | blocks break-skip when drums persist ([stem_detector.py:961](../../Source/stem_detector.py), [align_engine.py:1784](../../Source/align_engine.py)) | LIVE *(missed in v1)* |
| `kick_cues` | event markers | **upstream**: creates section boundaries ([stem_detector.py:943](../../Source/stem_detector.py)) = LIVE; persisted copy = viz only | split status *(v1 conflated)* |
| fills | event markers | loop-source exclusion ([align_engine.py:1621](../../Source/align_engine.py)) = LIVE; as alignment cues (weight 3) = FLAG-OFF (`emit_fills`) | split status |
| `bass_in/out` bars | bass handover | **fallback aligner path** (no landmarks on either track) anchors on them ([align_engine.py:1381-1389](../../Source/align_engine.py)) = LIVE; admission into `_mix_cues` on the landmark path = FLAG-OFF (`emit_bass_out`; failed held-out 2026-08-19) | split status *(v1 said flag-off globally)* |
| `loop_windows` | clean loop material | material picker ([align_engine.py:1616,2041](../../Source/align_engine.py)); viz (1479) | LIVE for material; **never an anchor** (carded) |
| `vocal_regions` | **"avoid vocal clash"** | loop-material hygiene only ([align_engine.py:1621,1682](../../Source/align_engine.py)) | **partially IDLE — never used for two-track clash over the overlap** |
| `bass_regions` | bass-to-bass points | none ([stem_detector.py:1144](../../Source/stem_detector.py) produces; `_track_from_sig` skips) | **IDLE** — and note: it is the run-encoding of the SAME boolean vector `bass_out` comes from ([:1020](../../Source/stem_detector.py)), so it cannot *corroborate* bass_out |
| `major_cues` | ~1-min in/out anchors | viz only (1228) | **IDLE** |
| hint fields (first_drop/…/last_bass_drop) | anchors | weight-7 cues, FLAG-OFF (`emit_hint_fields`) | derivation **already exists** (`hints_from_stem_result` + `--write-hints`, [stem_detector.py:1362,1418-1452](../../Source/stem_detector.py)); the missing work is invoking/repairing it from canonical `/mix` *(v1 misstated the build)* |
| `musical_landmarks` | kick-dropout evidence | landmark swaps ([align_engine.py:695,787,1386,1974](../../Source/align_engine.py)) | LIVE |
| `kick_presence_source`, `section_refinements`, `soft_intro_outro_hints` | provenance/diagnostics | no decision reader | IDLE *(missed in v1)* |
| `energy_cues` | boundary evidence | changes `raw_bounds` upstream ([stem_detector.py:925,943](../../Source/stem_detector.py)) but **not persisted** into `signals` | LIVE upstream, invisible downstream *(missed in v1)* |

### Amplitude analysis (RMS family)
`find_clean_loop_window` — sole caller sits behind `not USE_ALIGN_ENGINE` ([propose_arrangement.py:75,562](../../Source/propose_arrangement.py)) and production sets it True → **DEAD-PATH** *(v1 said LIVE)*. `find_first_drop/first_break/outro_start` — only via `cue_candidates.py` → DEAD-PATH.

### Mixed In Key
| What | Consumers | Status |
|---|---|---|
| Key, BPM | Camelot sequencing, report ([propose_arrangement.py:1087-1112](../../Source/propose_arrangement.py)) | LIVE |
| OverallEnergy (scalar) | harmony-preserving energy-arc tiebreak ([sequencer.py:117-166](../../Source/automated_dj_mixes/sequencer.py)) | LIVE |
| Cue points — **MIK AUTO-cues, machine-generated** ([desktop_analyzer.py:3](../../Source/automated_dj_mixes/desktop_analyzer.py), [cue_candidates.py:338](../../Source/automated_dj_mixes/cue_candidates.py)); not established as Sam-authored | preview PNG only ([orchestrator.py:150](../../Source/automated_dj_mixes/orchestrator.py)) | IDLE in decisions |
| Energy segments (per-section curve) | preview PNG ([orchestrator.py:140](../../Source/automated_dj_mixes/orchestrator.py), [waveform_preview.py:114](../../Source/automated_dj_mixes/waveform_preview.py)); decisions: none | LIVE in viz / IDLE in decisions *(v1 said dead-path)* |
| `key_confidence`, MIK LUFS, MIK beat grid, cue names, provenance ([mik_reader.py:29,198](../../Source/automated_dj_mixes/mik_reader.py)) | none | IDLE/DISCARDED *(missed in v1)* |

### Other
- **Legacy `TrackAnalysis` structural fields** (`intro_end_sec`, `first_break_*`, `last_kick_sec`, `cymbal_tail_end_sec`, `bass_start/end_sec` — toolkit `track_analysis.py:424` via the [analysis.py](../../Source/automated_dj_mixes/analysis.py) shim): consumers are diagnostic scripts only → DEAD-PATH in `/mix` *(missed in v1)*.
- **ARRANGEMENT_REPORT analysis fields** ([propose_arrangement.py:1725,1763-1799](../../Source/propose_arrangement.py)): mostly report/viz-only, but `swap_beats`, `handoff_kind`, `alignment_policy` are consumed by [apply_automation.py:620](../../Source/apply_automation.py) → mixed *(missed in v1)*.
- **MixPlan / warp contract** (marker count, grid hash, source BPM — [warp_contract.py:14](../../Source/automated_dj_mixes/warp_contract.py)): analysis-derived, persisted, actively reconciled ([validate_mix_plan_als.py:209](../../Source/validate_mix_plan_als.py)) → LIVE *(v1 excluded it; exclusion contradicted the "every signal" claim)*.
- **LUFS** → static levelling. LIVE.
- **Filter automation** — writer machinery complete; `apply_automation.py` has zero filter references. Built, never driven.
- **Learning artifacts** (`pair_history.jsonl`, `genre_priors.json`, ground-truth YAML) — zero output effect (carded 2026-08-17).
- **allin1 / self-similarity** — self-sim shipped in the loop gate; allin1 carded, unbuilt.

---

## B. By instrument

| Instrument | Reads today | Idle signals that plausibly improve it |
|---|---|---|
| Sequencer | key, BPM, OverallEnergy | MIK energy segments (per-section arc) — low priority |
| **Aligner** | bounds, sections, landmarks, stems_on, bass_in/out (fallback path) (+ flagged: fills, bass_out-as-cue, hints, rescue) | **vocal clash (O2); major_cues starvation fallback (O4); loop_windows as anchors (carded)** |
| Entry-extension planner | outgoing cues only | **stem envelopes for host-density gating (O1)** |
| Arranger / loops | sections, loop gate, loop windows | best-served consumer |
| Automation | volume, EQ bass kill, swap_beats/handoff_kind from report | **filter sweeps unbuilt-into-policy (O9, craft-gated)** |
| Beatgrid gate | grid, kicks, ticks (informational for stem grids by design) | — |
| Render gate | render, report, tempo map | **source kick attacks as flam-research input (O8)** |

---

## C. Ranked opportunities (order settled after review)

**O1 — Entry-extension host-density gate.** The strongest card: Sam-validated by blind A/B (2026-08-12 — quiet looped intro works over a *chilled* break, mush over a busy one). The per-stem envelopes to measure "busy" are cached and idle. Wiring only.

**O2 — Vocal-clash: report first, then scoring.** `vocal_regions` exists per track with "avoid vocal clash" as its stated purpose; the aligner never compares the two tracks over a candidate overlap. Measured incidence: **12/245 pairs** overlap vocals-on-vocals ([2026-08-17 Wiring Audit.md:68](2026-08-17%20Wiring%20Audit.md)). Codex's demotion argument is accepted: no *demonstrated audible* failure yet, so step 1 is a clash REPORT on existing builds (zero risk, produces the evidence), step 2 a scoring penalty only if the report shows real hits on real mixes.

**O3 — Invoke the existing hint-field derivation from `/mix`.** Not "build derivation" (v1 error): `hints_from_stem_result` + `--write-hints` already exist. The work is repairing/invoking that path canonically and flipping `emit_hint_fields` once measured. Directly addresses the cue-starved class (Emotions, Double Dutch).

**O4 — `major_cues` as starvation-only fallback.** Emitted only when a track's cue set is empty in the legal zone; low weight. Cheap, targeted at a carded failure class, cannot touch healthy tracks.

**O5 — Keep the bass stem beside the drums sidecar. SAM'S DECISION.** Canonical pipeline never re-separates now, but bass/vocals/other raw audio is discarded, so grid-repair's bass downbeat vote and any future bass-aware feature re-separate. ~70 MB/track (~1.4 GB/corpus). Deliberate "no mixable stem audio on disk" line in the code — policy call, not code call.

**O6 — MIK cue points: establish provenance BEFORE wiring.** v1 assumed hand-placed; the code calls them **auto-cues**. Question for Sam: does he hand-edit/place cues in MIK? If yes → hint-tier wiring is justified; if they're MIK's machine cues → wire low or not at all.

**O7 — `bass_regions` for incoming-bass occupancy validation only.** Demoted (v1 error): it is the run-encoding of the same boolean `bass_out` derives from, so "corroborating bass_out" was circular. Surviving use: validating that a planned swap lands where outgoing bass ends *and* incoming bass exists.

**O8 — Persist per-track kick attacks as flam-research input.** Demoted and narrowed (v1 overclaimed): it does NOT unblock `boundary_click` (skipped for tempo-map uncertainty, not missing baselines — [render_check.py:1813](../../Source/render_check.py)). It plausibly gives `probe_render_flam` its per-track baseline, IF source attacks can be shown to survive warp/loop/tempo mapping. Research input, not a wiring win.

**O9 — Filter sweeps.** Machinery complete, undriven. Craft-gated: needs Sam's model of *when* he sweeps (pairs naturally with O2's vocal overlap case). No code first.

**O10 — MIK energy segments for transition energy-matching.** `LIKE_ENERGY` is label-based and working. Lowest priority.

---

## D. Method and limits

Static audit of `Source/` (+ the two toolkit modules behind shims) by grep + read, 2026-08-27, main @ `4daec05`. Viz/Tests consumers don't count as "wired" (drawing a signal is not deciding with it) — but viz-LIVE is now stated where true (MIK segments). Flag defaults read from `CueConfig` ([align_engine.py:140-172](../../Source/align_engine.py)); `--cue-signals` tokens: fills/phrase/deep/bassout/hints/rescue ([propose_arrangement.py:1818-1845](../../Source/propose_arrangement.py)). MixPlan/warp-contract fields are IN scope (v1 wrongly excluded them). Not swept: `Tools/`, `Archive/`.

## Review trail

v1 went to Codex for adversarial completeness (read access to the repo, brief: "an omission never surfaces"). Verdict: **NEEDS CORRECTION** — 6 missed producer/consumer groups, 8 wrong statuses, 5 ranking challenges. Every load-bearing claim was then re-verified against source by Claude (8/8 confirmed, including two reversals of v1's own "corrections": the `.asd` ruler finding stands for stem-fitted grids, and Kick V3's raw-not-smoothed signal is the live one). All findings folded into this v2; the ranking order is the post-review one. Raw review: session scratchpad `codex_audit_review_out.md`.
