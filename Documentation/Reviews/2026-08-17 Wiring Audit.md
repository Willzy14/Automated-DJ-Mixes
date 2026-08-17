# Wiring Audit — what we built, what it's actually connected to

**Date:** 2026-08-17 · **Brain:** Claude (Opus 5), with four parallel audit agents
**Scope:** every signal the analysis layer produces, every capability the output layer supports,
and whether each one reaches a decision. Read-only audit; no code changed.
**Commissioned by Sam:** *"if those points are being highlighted and then they're not being wired
into the things that are deciding where cue points and mix points are gonna happen, then they're
literally useless. So let's find out what we've got and what it's wired into and what it should be
wired into."*

---

## 1. The headline

**The aligner chooses mix points from roughly a fifth of the evidence the analysis layer produces.**

`_mix_cues()` (`align_engine.py:256-282`) is the **only door** into the swap decision. It reads
four things:

- `track_start` (weight 5)
- `track_end` (weight 5)
- section starts / ends (weight 6 for drop/outro starts, else 3)
- `musical_landmarks` (weight 4 start / 5 end)

Everything else that has been built — bass regions, loop windows, fills, kick cues, major cues, the
four mandated hint fields, last-kick — is computed, validated, drawn on review pictures, and locked
out of the decision.

**The single most important structural lesson:** adding a field to `align_engine.Track` without
adding a cue in `_mix_cues` reproduces the `loop_windows` outcome exactly — loaded, visible,
decision-free. That has now happened at least five separate times.

### The measurement that settles it

Ablation across all **380 ordered track pairs**: deleting `bass_in`, `bass_out` and
`bass_out_is_end` changes **zero decisions**. Sam's named core model — the bass swap *is* the mix
point — has no influence on the live path.

It *looks* like it works because `section:drop:end` is a decent proxy for bass-out: the swap lands
within 2 bars of `bass_out` on ~50% of pairs, and 156/245 swaps are `paired/section:drop:end->drop`.
Coincidence doing a convincing impression of causation.

**And it is not harmless:** on **28 of 245 transitions (11%)** the swap lands where the outgoing has
no bass at all, so the EQ bass kill writes automation that does nothing. The old path had a "faked
bass drop" warning for exactly this; it is unreachable, so it fails silently.

---

## 2. Signal inventory

`align_engine.load_track` (`align_engine.py:98-126`) is the sole bridge from `SECTIONS_STEM_*.json`
into the arranger. It reads `sections`, `bass_in`, `bass_out`, `loop_windows`, `vocal_regions`,
`fills`, `musical_landmarks` — and nothing else. `propose_arrangement` and `apply_automation` never
see the stem JSON at all; they re-parse the ALS, which carries only label + beats.

| Signal | Produced | Reaches a mix decision? | Notes |
|---|---|---|---|
| `sections` (start/end bars) | `stem_detector` | **YES** | The primary anchor source |
| `musical_landmarks` | `musical_landmarks.py` | **YES** | **164/357 candidate cue bars (46%) exist ONLY because of a landmark**; 22/28 paired cues involve one. The one wired signal, carrying the system |
| `fills` (191 across 20 tracks) | `stem_detector` | **NO** | Loaded, then used *only as an exclusion mask* (`align_engine.py:648, 677-678`) — "don't loop here". See §3 |
| `loop_windows` | `stem_detector` | **NO (as anchor)** | Selects loop *content*, never mix *position*. Drives 168/380 loop plans |
| `bass_in` / `bass_out` / `bass_out_is_end` | `stem_detector` | **NO** | Ablation: 0/380 decisions change. Only `_handoff_candidates` treated `bass_out` as a candidate, and that is dead code |
| `bass_regions` | `stem_detector:576` | **NO** | **Zero consumers anywhere in the repo.** `load_track` does not even read it |
| `kick_cues` (136) | `stem_detector` | **PARTIAL** | Values shape sections via `:472-476`, but `_snap_merge` rounds/merges them and the exported precise array has no reader but the DETECT png |
| `major_cues` (40) | `stem_detector` | **NO** | DETECT png only (`:616, 638`) |
| `section_refinements` | `stem_detector` | **NO** | Empty on 20/20 tracks, and no reader even when populated |
| `kick_presence_source` | `stem_detector:580` | **NO** | Read only by tests |
| `vocal_regions` | `stem_detector` | **PARTIAL** | Stops a track's own loop chunk hitting its own vocals. **Cross-track vocal clash avoidance does not exist.** Measured impact is small: 12/245 pairs (5%) stack vocals |
| 4 mandated hints (`first_drop_sec`, `first_break_sec`, `outro_start_sec`, `last_bass_drop_sec`) | hand-authored + `--write-hints` | **NO** | Zero refs in `align_engine.py` / `propose_arrangement.py`. See §4 |
| `intro_skip_bars`, `loop_source_sec` | hints file | **NO** | Dead behind `USE_ALIGN_ENGINE=True`; `propose_arrangement.py:1124` prints a warning saying so. `/mix` still documents both as CLOSED |
| landmark `confidence` (103 high/27 med) | `musical_landmarks.py` | **NO** | Copied to report, read by nobody |
| landmark `candidate_roles` (130) | `musical_landmarks.py` | **NO** | Same |
| landmark `type` | `musical_landmarks.py` | **NO** | `pre_drop_kick_gap` and `kick_dropout` get identical weights; type only changes a label string |
| `last_kick_sec` / `cymbal_tail_end_sec` | `analysis.py`, `diagnose_sections.py` | **NO** | Only consumer is a `print()` |
| MIK key / BPM / energy / LUFS | `mik_reader.py` | **YES** | Reaches track ordering via `build_harmonic_path` + `apply_energy_arc` |
| MIK cue points | — | **N/A** | **Not discarded — none exist.** `mutagen` tags `None` on 20/20 WAVs; `MIKStore.db` has no cue table |
| MIK energy segments | `mik_reader.py` | **NO** | Rendered to a PNG and dropped. The one real MIK loss |
| `cue_candidates.py` (869 lines: 5 cue types, confidence, provenance) | — | **NO** | Import-only dead. Only `load_hints_file` is live |
| `features.py` per-beat RMS/bass/PWV5 | `features.py` | **NO** | Both call sites are in the `elif rb_match:` branch — the non-`--stem-sections` fallback |
| `phrase_viz` Interval / `refine_segments` / `_split_drop_with_fills` | `phrase_viz.py` | **NO** | `orchestrator.py:751-774` branch never runs under mandatory `--stem-sections`. The whole per-beat energy layer is dead |
| `report.write_track_csv` / `write_transition_report` | `report.py` | **NO** | Imported at `orchestrator.py:28`, never called |

### Dead code that changes how the aligner should be reasoned about

- **`_handoff_candidates`, `_score_lineup`, `LIKE_ENERGY` never execute in production.**
  `align_pair:482-483` early-returns to `_align_pair_landmark_aware` whenever *either* track has
  landmarks — and 20/20 corpus tracks do. Any doc claiming "the aligner scores like-energy
  boundaries" describes dead code.
- **`propose_arrangement.compute_natural_positions`** and its helper family
  (`best_swap_source`, `last_natural_swap`, `first_rise_source`, `_SECTION_ENERGY`) are dead behind
  `USE_ALIGN_ENGINE = True` (`:75`).
- **Naming hazard:** `Alignment.fills_cuts` / `FillCutSpec` is an unrelated concept sharing the word
  "fills". Grepping makes the detector signal look far better wired than it is.

---

## 3. The sharpest single example

`stem_detector.py:441` carries a comment recording Sam's own rule:

> *kick dropouts mark changes and are good points to mix*

The `fills` value it computes is then used **exclusively to avoid those regions**, as a loop
exclusion mask. **191 detected fills across 20 tracks; zero can ever be a mix point.**

The fix is two lines, mirroring what `musical_landmarks` already receives at `align_engine.py:277-281`.

---

## 4. Wiring the hints — smallest viable change

**~15 lines.** The plumbing already exists: `compute_aligned_positions` already receives the
`TrackInfo` list carrying hints, and `_sec_to_bar` already exists.

1. Add three hint fields to `TrackInfo` (`propose_arrangement.py:1082`)
2. Copy them as bars onto the aligner's `Track`
3. Emit them in `_mix_cues` (`align_engine.py:256-282`) at **weight 7** — above
   `section:drop:start`'s 6

**Why this is unusually safe:** `add()` merges by `max(weight)` and appends labels. An *agreeing*
hint therefore costs nothing; a *disagreeing* one adds a new reachable bar. **Additive, never
subtractive, and a complete no-op when no hints file exists.**

Optionally also add the hinted first drop to `_incoming_swap_anchors` as rescue-only, so it cannot
re-decide a transition that already works.

**Caveat carried from the plan review:** because `add()` merges by `max(weight)`, any *synthetic*
cue injected into a copy of the dict can silently raise an existing cue's weight. Copy the dict,
never mutate it.

**Gate status:** the hint gate is **disabled on the production path** (`orchestrator.py:621`, skipped
under `--sections-layout`). And `orchestrator.py:97-100` contains a comment spelling out the
intended mechanic — *"align incoming.first_drop_sec to outgoing.last_bass_drop_sec"* — directly
above a variable that is loaded and dropped.

**Are the hints independent evidence?** For `Test Project/14.08.26`, yes — the hints carry
`"Visual pass by Claude on 2026-08-14"` notes and were corrected twice by hand. Measured: **15 of 40
outgoing-side hint values (38%) point at a bar that is not any section boundary.** Both genuinely
cue-starved tracks have new hint values. When hints come from `stem_detector --write-hints` instead,
the gate is tautological — the code says so at `validate_hints_vs_sections.py:88`.

---

## 5. Output capabilities

`generate_session()` holds the LP/HP filter and generic `Device.Param` writers, but its one live
caller (`orchestrator.py:829`) passes `transition_automation=None` (`:835`). The only other caller
is the retired full-mix path, now a `RuntimeError`. So the dispatch block at
`als_generator.py:849-871` is **unreachable in production**. All production automation comes from
`apply_automation.py`, which drives **two** parameters: Utility Gain (volume) and ChannelEq
LowShelfGain (bass kill).

| Capability | Driven? | Notes |
|---|---|---|
| Volume crossfade (Utility Gain) | **YES** | |
| EQ bass kill (ChannelEq Low) | **YES** | But a no-op on 11% of transitions (§1) |
| LP / HP filter sweeps | **NO** | **A DELIBERATE DECISION, not an omission** — `ai-activity-log.md:42`: *"dropped LP/HP filter sweeps from default (conflict with bass cuts)"*. The code was never cleaned up, so it still reads as alive |
| Pan / StereoWidth / BassMono / HighShelf / MidGain / Filter_Resonance | **NO** | Never built |
| Sends | **NO** | Template has zero `<Send>` nodes |
| Tempo arc | **PARTIAL** | Opt-in `--tempo-arc` |
| LUFS levelling | **PARTIAL** | Real, but silently skips if `pyloudnorm` or `Audio/` is missing (`:838-845`) |
| Learned Rule 3 (two-stage volume) | **NO** | Explicitly disabled (`:584-589`) pending >=3 observations |
| Learned Rule 1 (boundary avoidance) | **NO** | `find_bass_swap` sits in an `else` reached only when the arrangement report lacks a swap (`:547-561`) — and align_engine always supplies one. **The best-evidenced learned rule (2/2 corrections, `BOUNDARY_MARGIN=64`) never executes** |

### The learning loop is a closed circuit

`pair_history.jsonl` reaches a notes string (`:1182-1184`) and a report field (`:1739-1747`), and
`apply_automation` reads only `swap_beats`/`handoff_kind` from that report — so even the field is
never consumed. `genre_priors.json` and `Data/Ground Truth/Sam Cue Points.yaml` have **zero code
consumers**.

**`TOOLBOX.md:216` claims the ground-truth YAML is "used for threshold tuning + regression testing".
That is false** — nothing reads it, and 14 of 20 values are `null`. The corpus is 18 entries from
one project, all at 129.2 BPM.

**Net: no file in `Mix Patterns Library/` or `Data/Ground Truth/` changes a single output value.**
The three rules that do affect output are hand-transcribed if-statements, and the best-evidenced one
is unreachable. `FABLE_REVIEW_2026-06-10.md:187` predicted this ten weeks ago.

---

## 6. Gates — which are real

| Gate | Status |
|---|---|
| `validate_als` | **Real and unskippable** — raises from `compress_als`. The working pattern |
| `validate_mix_plan_als.reconcile()` | Strong logic, but **no Python caller** — runbook-enforced via `mix.md` Phase 3b, and `mix.md:344` authorises skipping it. Depends on two optional flags. Minor vacuity at `:275` (a transition with no loops appends a `bass_swap` "check" having verified nothing) |
| `regress_section_detection.py` | **FULLY VACUOUS** — `Documentation/Golden Sections/` contains only a README, so it returns 0 checks / PASS (`:151-153`). Worse, `regenerate_sections` omits `--stem-sections --stem-grid --kick-model` (`:106-111`), so even populated it would regress the *retired* path. Listed as enforcement layer #6 in `mix.md:614` |
| `validate_hints_vs_sections` | Self-referential when hints come from `--write-hints` (code admits it at `:88`). **Also reads the wrong file** — `Sections Review/Sections_V<N>.json`, not the `SECTIONS_STEM_*.json` the aligner reads. On 14.08.26 its last run was 15:01 against V1 while V2 was written 15:26, **7/20 tracks differing** — a stale verdict, the exact bug class it exists to catch. Credit: the `rows == 0` vacuity was already found and fixed (`:211-216`) |
| Beatgrid gate (`--stem-grid` mode) | **NEW SELF-REFERENTIAL METRIC.** Reduces to `grid_vs_kick_ms`, computed by the detector from the same kick onsets it fitted the grid to (`stem_grid.py:94,123` -> `orchestrator.py:459` -> `validate_beatgrid.py:394`). The independent `.asd` tick offset **is computed and then deliberately discarded** for exactly these grids (`:249-258`), surviving only as display text. `SKIP` verdicts also pass. This is the same class as the bug Sam heard in the car, where `grid_vs_kick` read 1.11 ms on a genuinely broken grid |
| CI | **None.** `.github/` has no `workflows/` |

### Nothing observes the actual output

**The pipeline never renders.** `/mix` ends at Phase 5 with a path to the `.als`. Phase 4's "final
visual review" reads the *source* WAVs (`transition_review_viz.py:382-387`) — a prediction derived
from the same arithmetic being checked. `probe_render_flam.py` has zero callers.

The proof this matters is already in the log. `ai-activity-log.md:106`: every document-level check
passed — *"override propagation CLEAN, timeline CLEAN, grids grade BETTER than the ear-validated
control"* — and the real cause was found **in the render**: ~80-130 ms kick flam.

Cheaply machine-detectable defects currently reaching only Sam's ears: kick flam between overlapping
tracks (the script exists and caught exactly this on 2026-06-12), cumulative warp drift, clipping
from summing, an unlevelled mix from a missing dependency, bass-kill written to the wrong target.

---

## 7. Recommended order

Sequenced by (value x safety) / blast radius. **Step 0 is not optional.**

**0. Build the regression net FIRST.** The golden fixture pins swap beats, break-skip and loop
counts only — not `paired_cues`, `arr_offset_bars`, `overlap_policy`, `alignment_policy` or
`handoff_kind`. Every change below alters anchor sets, so without this we cannot distinguish
improvement from regression. Also populate `Documentation/Golden Sections/` or delete
`regress_section_detection.py` — a gate that returns PASS on an empty directory is worse than none.

**1. Wire the four hints** (~15 lines, §4). Additive, no-op without a hints file, and it makes
already-authored human judgement count. Independently verifiable per-track.

**2. Wire `fills` as anchors** (~2 lines, §3). 191 signals, zero currently reachable, and the code
already documents them as good mix points.

**3. Give `bass_out` a cue in `_mix_cues`.** Restores Sam's core model to the live path. Larger
behavioural change than 1-2 because it will move swaps — do it after the regression net, and
A/B it.

**4. Add a musical-end value** (last kick / energy cliff) as a sibling field, consumed only by the
new tail anchor. Do **not** correct `n_bars` globally — that could tip currently-passing transitions
over the `[16,48]` edge under fail-closed output.

**5. The 30-second tail anchor** (Sam's rule) as a rescue pass — see the separate tail-anchor plan.
Note steps 1-3 may reduce how often it needs to fire.

**6. Close the observation gap:** render, then run `probe_render_flam.py` and a clipping/LUFS check
automatically. This is the only category where a defect can currently reach Sam's ears unchallenged.

**7. Housekeeping:** delete or clearly quarantine the dead paths (`cue_candidates.py`,
`compute_natural_positions`, the unreachable `generate_session` dispatch, `phrase_viz`'s per-beat
layer) so the next audit does not have to re-derive what is alive. Correct `TOOLBOX.md:216`.

---

## 8. Confidence

Every claim above cites file:line and was produced by an agent instructed to separate verified fact
from suspicion. The two headline quantitative results — the 380-pair bass ablation and the 46%
landmark-only cue-bar figure — were **measured by executing the real engine**, not inferred.

One item is **suspected and unverified**: the two routes into the arranger may disagree about what a
bar is. `segments_from_stem_sections` maps section times through the warp grid (`phrase_viz.py:311-333`
— the June one-clock fix), while `load_track` reads raw `start_bar` off the detector's constant-BPM
clock (`align_engine.py:102,121`). **The one-clock fix never touched the `load_track` route and no
validator compares the two.** It could not be measured because `beat_times_ms` is not persisted in
`_Stem Analysis`. Given this is the class of bug Sam heard in the car, it deserves its own check.
