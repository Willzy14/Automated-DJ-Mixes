The biggest likely saving is **fewer transitions needing rearrangement or volume-envelope edits**. Finish the existing listening comparison, fix the remaining forced quick cuts, and make render evidence reliable before expanding the analysis stack.

Read-only sweep against `62c47f5`; no files changed.

## A. Genuinely broken or incomplete pipeline behavior

1. **Production tempo arcs still break without MIK — P1, engineering.**  
   I reproduced `float(None)` on the actual held-out project using `--tempo-arc` in dry-run mode: Phase 2 still obtains its BPM metadata from MIK, despite certified stem grids already existing. Carry the certified BPM into the arranger so an optional metadata application cannot block production.  
   Evidence: `Source/propose_arrangement.py:1125`, `Source/propose_arrangement.py:1299`.

2. **Short overlaps still force an unwanted volume cut — P1, engineering; Sam judges the repair.**  
   Style selection remains entirely length-based, and the current Side A still cuts Freejak’s volume over one beat into HARTY—the behavior Sam already corrected manually. Today’s content-aware margin fix does not change this choice; a bounded fix to automation-style selection deserves attention without assuming the deeper candidate-search reorder is necessary.  
   Evidence: `Source/apply_automation.py:809`, `Source/apply_automation.py:984`; `.github/ai-activity-log.md`, final entry.

3. **Documented correction overrides do nothing in production — P2, engineering.**  
   `intro_skip_bars` is explicitly ignored by the active aligner, while `loop_source_sec` only affects the legacy loop-planning branch. These are particularly useful controls for reducing repeat hand-editing: an approved correction should survive regeneration.  
   Evidence: `Source/propose_arrangement.py:562`, `Source/propose_arrangement.py:664`, `Source/propose_arrangement.py:1151`, `Source/propose_arrangement.py:1190`.

4. **Grid validation still needs a trustworthy diagnosis — P1, validation engineering.**  
   The reported 231.8 ms failure closely resembles a half-beat cluster switch: adding half a beat at 130 BPM to the first two reported phases reduces their spread with the third to **2.6 ms**, a strong aliasing lead rather than proof the audio is correct. Separately, I reproduced every grid probe failing and receiving only `INFO`; insufficient onsets also return a numerical zero, so missing evidence can contribute to a passing verdict.  
   Evidence: `Source/render_check.py:2173`, `Source/render_check.py:2185`, `Source/render_check.py:2215`, `Source/render_check.py:2488`; held-out `Output/RENDER_CHECK.md:17`.

5. **Tempo-arc renders lack two important checks — P2, validation engineering.**  
   On tempo arcs, every boundary-click inspection is skipped and grid drift cannot fail the render. Characterizing the mapping against a known-matching ALS/bounce pair would restore useful automatic checks and reduce the technical faults Sam must find himself.  
   Evidence: `Source/render_check.py:2235`, `Source/render_check.py:2440`.

6. **“Feasible” pairs can still fail to build — P2, engineering.**  
   The feasibility tool tests alignment alone; my replay found **two of 267 alignment-successful historical pairs fail subsequent loop planning**, including Doorly → Christoph’s *The Rise*. Sequence selection also lacks this buildability constraint, leaving avoidable reorder/retry work for the operator.  
   Evidence: `Source/alignment_feasibility.py:54`, `Source/align_engine.py:2009`, `Source/automated_dj_mixes/sequencer.py:192`.

7. **Silent post-swap clips remain visible — P3, engineering.**  
   The known QUICK_SWAP leftover remains: outgoing gain reaches zero before the arranged clip geometry ends, creating unnecessary visual cleanup. Resolve it with the plan/reconciliation contract intact; deleting clips blindly is not an acceptable repair.  
   Evidence: `Source/apply_automation.py:984`, `Source/validate_mix_plan_als.py:309`.

## B. Real opportunities to reduce manual polish work

1. **Finish the existing listening comparison — P1, operator prepares; Sam listens.**  
   This is the strongest immediate opportunity to determine whether the already-built incoming-entry/loop changes reduce Sam’s corrections; prepare fresh renders and short, independently randomized transition excerpts, recording intervention count alongside preference. **All three current sides need fresh render evidence**: Side A’s existing WAV/report are dated September 11, before its latest fixes and rebuild; the sealer randomizes supplied audio but does not extract the transition excerpts itself.  
   Evidence: `Source/build_ab_comparison.py:39`, `Source/seal_listening_test.py:64`; `Documentation/Mix Patterns Library/Heldout Replay Plan V2.md:114`.

2. **Make bouncing and checking one repeatable operation — P2, workflow engineering.**  
   Automated checking currently depends on someone supplying an Ableton bounce, and the render report does not bind its findings to hashes of the ALS and arrangement report. A repeatable export/check workflow with explicit artifact identity would remove operator work and prevent reviewing yesterday’s audio against today’s arrangement.  
   Evidence: `Source/render_check.py:2309`, `Source/render_check.py:2499`; `Claude Code Brain/commands/mix.md:433`, `:552`.

3. **Resolve the two other held-out render warnings — P2 for the dip; P3 for the ending, operator first.**  
   T2 has a **3.23 dB dip with a large sub deficit**, but its minimum is unbracketed, so first measure it on the fresh render with enough surrounding context before choosing any repair. The **7.4-second near-silent ending** coincides with Jewel Kid’s documented fade-out and warrants an ending/trim decision, not automatic treatment as broken internal silence.  
   Evidence: held-out `Output/RENDER_CHECK.md:18`, `:21`; `Source/render_check.py:1738`, `Source/render_check.py:1772`, `Source/render_check.py:1501`.

4. **Finish bass-residual validation, then assess overlap loudness — P2, audio engineering plus Sam.**  
   The outgoing-bass residual feature already exists but still lacks calibration for the actual share-difference quantity and its full-mix trial; that is the outstanding work, not rebuilding bass compensation. Subtle overlap loudness compensation remains separate: static track leveling and the current dip check do not establish that transitions avoid upward loudness bumps.  
   Evidence: `Source/bass_residual.py:36`, `Source/apply_automation.py:65`, `Source/apply_automation.py:1013`, `Source/render_check.py:1755`; `Documentation/Production Polish Backlog.md:12`.

5. **Use existing vocal/density evidence to direct listening — P2, analysis engineering.**  
   Vocal regions currently protect loop-source selection, but the pipeline does not compare both tracks’ audible vocals across a transition; outgoing density is measured only as shadow information. Produce gain-aware clash/density reports first, giving Sam short suspect passages to hear rather than introducing an unvalidated automatic penalty.  
   Evidence: `Source/align_engine.py:620`, `Source/align_engine.py:1770`, `Source/align_engine.py:1987`; `Documentation/Reviews/2026-08-27 Analysis Extraction Audit.md:98`.

6. **Finish the existing loop/width experiments — P2, analysis engineering.**  
   Tier A loop similarity and width-based section cues already exist behind disabled switches, with documented examples addressing loops and structural changes Sam previously corrected. Resolve the loop test’s replacement-versus-additional-check semantics and replay the candidates before promotion; simply enabling everything would also remove some existing rejections.  
   Evidence: `Source/align_engine.py:66`, `Source/align_engine.py:499`, `Source/stem_detector.py:769`, `Source/stem_detector.py:929`; `Documentation/AI_CONTEXT.md:294`, `:299`.

7. **Restore useful key metadata on this machine — P2, integration owner.**  
   This held-out run lacked MIK metadata and therefore harmonic scoring; in mixed known/unknown pools, the sequencer substitutes `1A` for missing keys rather than preserving uncertainty. Reliable metadata recovery and honest unknown-key handling could prevent harmonic cleanup without changing the owned beat-grid path.  
   Evidence: held-out `Output/Visualisations/REVIEW_A.md`, “Known limitations”; `Source/automated_dj_mixes/orchestrator.py:638`.

## C. Hygiene/tech-debt that does not directly affect output quality

1. **Separate current instructions from historical claims — P2 for operator clarity; P3 refactoring.**  
   `AI_CONTEXT.md` contains contradictory generations of pipeline instructions, and `/mix` still describes the two inactive hint overrides as closed features. Production-dead helpers and duplicated ALS I/O are maintenance work; they should not outrank audible transition problems.  
   Evidence: `Documentation/AI_CONTEXT.md:1290`; `Claude Code Brain/commands/mix.md:585`; `Source/propose_arrangement.py:562`, `Source/apply_automation.py:107`, `Source/apply_loops.py`.

2. **Make fixture coverage explicit — P3, test owner.**  
   The focused run passed seven tests and skipped six: **four lacked the June golden fixture; two were intentional non-applicable cases**, with no `xfail` markers found. Recovering the missing golden fixture improves regression confidence, but these skips are not six outstanding mix defects.  
   Evidence: `Tests/test_align_engine_golden.py:24`, `Tests/test_swap_selection_replay.py:64`.

## D. Misframing, contradictions, and settled-context corrections

1. **The review is historical visual evidence, not current audio acceptance.**  
   `REVIEW_A.md` actually lives under `Output/Visualisations/`, says no render exists, and predates the faults subsequently heard and fixed. Its 0.58–0.65 loop “quality” scores come from a display heuristic, not the production self-similarity gate—those numbers should not be treated as equivalent certifications.  
   Evidence: `Source/loop_review_viz.py:57`, `Source/align_engine.py:499`; held-out `Output/Visualisations/REVIEW_A.md`.

2. **The experiment’s gate requirements contradict each other.**  
   Plan V2 requires MixPlan reconciliation before listening, while the current `/mix` replay instructions explicitly exempt experimental sides; the builder follows that exemption, and I confirmed all three current plans cannot reconcile because tempo/warp choices remain unspecified. Resolve and document that experiment contract before the listen; this is not evidence that the existing production reconciliation gate is broken.  
   Evidence: `Documentation/Mix Patterns Library/Heldout Replay Plan V2.md:118`; `Claude Code Brain/commands/mix.md:429`; `Source/build_ab_comparison.py:87`, `:109`; `Source/validate_mix_plan_als.py:139`.

3. **The candidate is narrower than the full musical redesign.**  
   All eight current held-out bass-swap positions are identical across A/B/C: the comparison exercises changed entry/loop behavior, not a demonstrated improvement in swap placement, and populated correction history currently supplies report annotations rather than automatic learned decisions. The actual test has eight transitions versus the plan’s seven-transition example, and the plan also requires a second structurally different held-out mix before default promotion.  
   Evidence: `Source/propose_arrangement.py:1239`, `Source/align_engine.py:1945`; `Documentation/Mix Patterns Library/Heldout Replay Plan V2.md:129`, `:135`.

The other settled points check out: `interim_v1` remains production, today’s margin fix is present, and the deeper swap-first reorder remains unbuilt. Cross-project Demucs reuse and the style catalogue should remain deferred.

_Validated: 7 focused tests passed; 6 explained skips; additional read-only reproductions and corpus checks ✓_
===CODEX-ASK-DONE exit=0 nonce=9431000b107f4154a5303d2730b8c0eb===
