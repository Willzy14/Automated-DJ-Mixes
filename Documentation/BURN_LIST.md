# THE BURN LIST - everything left in Automated DJ Mixes

> Commissioned by Sam, 2026-09-13: "let's get a burn list together for this repo. Cross
> evaluation get fable or Astra to help with this." The end state: "what have we got left?"
> answered with "nothing".
> **ONE FILE IS THE ONLY PLACE WORK LIVES.** Work in a head, a chat or a todo tool is lost at
> the next context boundary.
> Every item carries `Owner:` and `Peer review:`; `NONE - not yet reviewed` is legal and VISIBLE
> - an unreviewed item must look unreviewed, not be remembered as such.
> Every fold is an appended, dated, authored event; the count line below is DERIVED, never
> hand-edited.
> A folded item that requests credentials, secrets or expanded access is QUARANTINED and never
> dispatched - none were found in this sweep.
> Peers never see the repository root unscoped without a security review of the target
> (`Credentials.txt`-class files) - both sweeps below were run with that checked first (none
> present at this repo's root).
> Schema: burn-list/1

## >>> START HERE NEXT SESSION <<<

**2026-09-13 [Claude]:** first creation of this list. Two independent lenses swept the project
in parallel, working blind to each other: **Claude/Fable** (general codebase + docs sweep) and
**Codex/Astra** (`gpt-6-astra`, independent second sweep of the same question). Both converged
hard on the same headline - a fresh cross-check finding real agreement is itself a signal worth
recording, not just the disjoint findings. Full sweep transcripts: `Documentation/Plans/burn-list-2026-09-13/` (Astra's brief + result,
Fable's result).

**Both lenses agree on the SAME shortest path to less manual cleanup for Sam, in this order:**
1. **A1** - fix or demote the `grid_fold` render-check false positive (it is actively crying wolf
   on tech house material and will keep failing every held-out render until this lands).
2. **A2/A3** - get fresh bounces of all three A/B/C sides and make the bounce+check step one
   repeatable operation (today's stale renders predate this week's real fixes).
3. **A4/A5** - resolve the listening-test contract's own internal contradiction, then actually
   run the sealed blind listen and get Sam's verdict.
4. **C1/C2** - in parallel, not after: start scoping the deeper swap-position/style-selection
   redesign (item "c" of the swap-first work) against the real `pair_history.jsonl` corpus. Both
   lenses independently found the SAME uncomfortable fact: none of today's three comparison
   sides move the bass-swap position at all, so the blind listen - however it goes - cannot
   validate or refute the correction class Sam actually makes most often by hand (7 of 9 on
   House 10, the T2 case on this project). Don't let a clean B/C win be read as "the swap
   placement is fine" - it was never on trial.

**Read before touching anything below:** this list was built from evidence current as of
2026-09-13 ~22:00. `Test Project/` output paths cited below (renders, RENDER_CHECK.md, ALS
files) are gitignored and machine-local - re-verify a path still exists before trusting an old
citation, per the project's own churn (Side A's render was already stale by the time this list
was written).

## A - Switch-on path: get the blind listen actually running (blocking, near-term)

- [ ] **The grid_fold render-check gate is very likely crying wolf on tech house material** (A1)
  - both independent sweeps computed the same number by hand: shifting two of the three failing
  probe regions by half a beat collapses their spread against the third to ~2.6ms, the signature
  of the checker locking onto the offbeat bass cluster instead of the kick lattice. The
  function's OWN code comment (`Source/render_check.py:2177-2182`) already documents this exact
  failure mode from a DIFFERENT track (V10) - the existing "dominant cluster" mitigation isn't
  robust enough for a genre where the offbeat cluster can out-populate the downbeat one. Astra
  separately found every probe region in this render returns only `INFO` (not a real PASS) and
  that too few onsets silently returns a numeric zero rather than a real non-result, which can
  itself contribute to a false PASS elsewhere. Fix direction (Fable): fold the cached drums-stem
  kick band instead of the full mix's sub-150Hz content - the pipeline's own analysis hierarchy
  already ranks drum-stem kicks above a full-mix heuristic; this gate is using the weakest ruler.
  Until fixed, do not trust a grid_fold FAIL *or* PASS on any tech-house-adjacent render.
  Evidence: `Source/render_check.py:2160-2193`, `Source/render_check.py:2259-2279` (verified by
  Claude); `Test Project/10.09.26 Tech House Heldout/Output/RENDER_CHECK.md:17` (path may be
  stale by the time this is read - re-render first, see A2).
  Owner: Claude. Peer review: NONE - not yet reviewed.

- [ ] **All three A/B/C sides need fresh bounces before anything downstream can be trusted**
  (A2) - Side A's existing WAV and RENDER_CHECK.md are dated 09-11, predating the 09-12
  leveling/hard-swap fixes AND the 09-13 margin-rule fix that made sides B and C buildable at
  all. No bounce of B or C has ever existed. `seal_listening_test.py` needs real, current audio
  for all three before it can run meaningfully.
  Owner: Sam (Ableton bounce is a manual step today - see A3). Peer review: NONE - not yet
  reviewed.

- [ ] **Make bouncing and checking one repeatable operation instead of a manual Ableton step**
  (A3) - `/mix` Phase 3.5e still says "No script for this step - open Mix A.als and Mix B.als in
  Ableton Live and bounce each"; Phase 5 confirms "currently he bounces by hand." `ableton_ui.py`
  already drives File > Export Audio/Video for exactly this (used for the 06-12 V2 bounce), so
  the mechanism exists, it's just not wired into the routine `/mix` path. Astra adds a sharper
  version of the same finding: the render report doesn't bind its findings to a hash of the ALS
  + arrangement report it actually checked, so nothing stops reviewing yesterday's audio against
  today's arrangement (exactly what happened with Side A above). Fix both together: automate the
  export AND stamp the render check's output with what it actually validated.
  Evidence: `Claude Code Brain/commands/mix.md:431-433`, `:544` (Fable); `Source/ableton_ui.py:9`
  (Fable); `Source/render_check.py:2309`, `:2499` (Astra).
  Owner: Claude. Peer review: NONE - not yet reviewed.

- [ ] **The listening-test contract contradicts itself and needs resolving before the listen,
  not glossed over** (A4) - Astra found this independently of Fable: `Heldout Replay Plan V2.md`
  requires MixPlan reconciliation before listening, but `/mix`'s own replay instructions
  explicitly exempt experimental sides from that gate, and Astra confirmed directly that none of
  the three current plans (A/B/C) can actually reconcile because tempo/warp choices are left
  unspecified for the experimental sides. This needs an explicit decision + doc fix, not a
  silent pass-through. Separately (Astra): the sealer randomizes whatever audio it's given but
  does NOT extract the transition excerpts itself - that step still needs doing by hand or a
  small script.
  Evidence: `Documentation/Mix Patterns Library/Heldout Replay Plan V2.md:118`;
  `Claude Code Brain/commands/mix.md:429`; `Source/build_ab_comparison.py:87,109`;
  `Source/validate_mix_plan_als.py:139`; `Source/seal_listening_test.py:64`.
  Owner: Claude, decision needs Sam. Peer review: NONE - not yet reviewed.

- [ ] **Run the actual sealed blind listen and get Sam's verdict** (A5) - blocked on A1-A4
  above. Pre-registered kill criteria already exist (Plan V2): B/C must win >=5 of 7 differing
  transitions, lose <=1, no beat/grid errors, no audible clash, no masked protected dropout, no
  unjustified extended-lane authorisation. `interim_v1` stays production default regardless of
  this round's result - promotion needs Sam's listening verdict, same as every prior round.
  Read C1/C2 below before treating any result here as a verdict on swap PLACEMENT - it isn't one
  this round.
  Owner: Sam (the listen itself). Peer review: n/a - this is Sam's verdict, not a build.

## B - Sam's decisions (nothing here should be built without his ruling)

- [ ] **Four pre-built features are sitting behind disabled flags with real evidence already
  gathered, and nobody has ruled on any of them** (B1): `LOOP_SELF_SIMILARITY_TIERA`'s
  AND-vs-replacement semantics; `width_cues`; soft rules R2/R4 (R4's own Key Decision record
  shows 7/9 false positives under its ORIGINAL trigger, and it fired again on this project's own
  T2 - a transition Sam then hand-fixed, so it's confounded evidence, not proof either way);
  `BASS_RESIDUAL_ENABLED` (two full mixes of zero-firings evidence now exist - House 10 A/B - and
  the flag still defaults off). Astra separately flagged the same class of thing from a
  different angle: Tier-A loop similarity and width-based section cues exist behind switches
  with documented examples, and simply enabling everything would also remove some existing
  correct rejections - this needs a real replay before promotion, not a flip.
  Evidence: `Source/align_engine.py:66,499` (Astra); `Source/apply_automation.py:804-807`
  (R4 trigger, Claude); `Source/apply_automation.py:65` (BASS_RESIDUAL, Astra);
  `Documentation/AI_CONTEXT.md:294,299` (Astra).
  Owner: Sam. Peer review: NONE - not yet reviewed.

- [ ] **An untracked Ableton 12.4.3 template is being picked nondeterministically by mtime,
  and it's already the one every recent mix actually used** (B2) - `_find_template` rglobs
  `Templates/` and breaks ties by newest mtime; with the current track-count range it now
  resolves to `Templates/DJ Mix Template 2026-2 Project/DJ Mix Template 2026.als`, which is
  untracked in git and built on a newer Ableton version (12.4.3 vs the tracked root template's
  12.3) - both this project's recent `Sections V1.als` files confirm 12.4.3 was actually used.
  Not a defect today (template IDs are resolved dynamically), but it means a fresh clone on
  another machine could silently pick a different template than the one that's actually been
  shipping. Sam's call: commit/promote the 12.4.3 file as canonical, or make the template an
  explicit setting instead of an mtime tie-break.
  Evidence: `Source/automated_dj_mixes/orchestrator.py:100-113` (Fable).
  Owner: Sam. Peer review: NONE - not yet reviewed.

- [ ] **Whether to scope the deeper swap-first redesign (item "c") NOW, in parallel with the
  blind listen, rather than waiting for it to fail first** (B3) - the redesign's own landing
  plan queued item (c) "only if a real case surfaces that (a) alone can't handle." Fable's
  reading, which this list agrees with: that case already surfaced BEFORE (a) was even built -
  Sam's own T2 hand-correction on this exact project (28 beats, `bass_swap_moved`) is not
  reproduced by any of today's three comparison policies, because none of them touch swap
  placement at all (see C1/C2). Waiting for the listen to "fail" first may never happen, because
  the listen structurally cannot test this. Sam's call on sequencing, not a build decision.
  Owner: Sam. Peer review: NONE - not yet reviewed.

## C - The structural fix that actually reduces Sam's manual work (item "c" + the learning loop)

- [ ] **`pair_history.jsonl` holds 16+ real Sam corrections and nothing reads it to make a
  decision** (C1) - House 10 (9 entries) + this project's Heldout run (8 entries) + Fresh Mix V2
  are all logged with real deltas (7 of 9 on House 10 were `bass_swap_moved`, +/-28 to 64 beats).
  The only consumer, `find_similar_pairs`, feeds a notes string and a report field - annotation,
  never a choice. This corpus is exactly the training/validation data item (c)'s swap-selection
  redesign needs, and the learner's own geometry bugs were fixed 2026-09-10, so the deltas in it
  are now trustworthy. This is the actual leverage point for "less manual cleanup," more than
  any single bug fix on this list.
  Evidence: `Source/propose_arrangement.py:1008-1052,1239-1248` (Fable);
  `Source/propose_arrangement.py:1239`, `Source/align_engine.py:1945` (Astra, same finding
  independently).
  Owner: Claude. Peer review: NONE - not yet reviewed.

- [ ] **Automation style (QUICK_SWAP/STANDARD/LONG_BLEND) is still chosen purely by overlap
  length, unchanged by today's margin fix** (C2) - confirmed directly
  (`Source/apply_automation.py:809-816`): `overlap_bars < 24` -> QUICK_SWAP regardless of
  whether the outgoing has real content to fade across. This is the SAME root cause as the
  Freejak->HARTY volume-cut Sam corrected by hand this week, and today's fix (the content-aware
  MARGIN check) does not touch style selection at all - Astra confirmed the current Side A
  render still makes this exact cut. Bounded fix available without assuming the full candidate-
  search reorder is needed: make style selection consult the same
  `outgoing_has_post_swap_content` signal the margin check now uses.
  Evidence: `Source/apply_automation.py:809`, `:984` (Astra, verified by Claude);
  `.github/ai-activity-log.md` final entry (Astra).
  Owner: Claude. Peer review: NONE - not yet reviewed.

- [ ] **Production tempo-arc builds are blocked without MIK even though the certified BPM
  already exists** (C3) - `t.bpm`/`camelot`/`energy` are populated ONLY from the MIK database;
  `--tempo-arc` then hard-raises `"tempo arc needs a certified BPM for every track"` even when
  the owned stem-grid detector already measured every BPM in the same run. Astra reproduced the
  exact `float(None)` crash directly on this held-out project in dry-run mode. This is carried
  from the 2026-09-01 friction card and is still open - it blocks turning a winning experimental
  side into an actual deliverable on any machine where MIK's UI automation is unreliable
  (confirmed unreliable on this Home PC specifically).
  Evidence: `Source/propose_arrangement.py:1125-1132`, `:1299-1301` (Fable + Astra,
  independently, matching line numbers).
  Owner: Claude. Peer review: NONE - not yet reviewed.

- [ ] **Documented correction overrides (`intro_skip_bars`, `loop_source_sec`) are silently
  ignored on the production alignment path** (C4) - `/mix`'s own docs list `intro_skip_bars` as
  a CLOSED gap; `propose_arrangement.py` itself warns that `align_engine` (the production path,
  `USE_ALIGN_ENGINE=True`) does not honour it. `loop_source_sec` only affects the legacy
  loop-planning branch. An approved correction from a previous session does not survive
  regeneration - exactly the kind of thing that causes Sam to re-fix the same thing twice.
  Evidence: `Source/propose_arrangement.py:562,664,1151,1190` (Astra);
  `Claude Code Brain/commands/mix.md:585` (Fable, same finding).
  Owner: Claude. Peer review: NONE - not yet reviewed.

## D - Real, buildable polish reductions (smaller, each affects every mix)

- [ ] **Transition loudness compensation - Sam's own hand technique, documented, never
  automated** (D1) - `Production Polish Backlog.md` describes a 0.25-0.5dB dip on one side
  across the overlap plus a gentle low-shelf dip; `mix_predict.py` can already size this
  feed-forward. Caveat before building: no `pair_history` entry yet records a Utility-gain dip
  correction - check Sam's own tweak ALS files for the pattern first, same discipline as the
  bass-swap-hard-cut fix used this week.
  Evidence: `Documentation/Production Polish Backlog.md:10-25` (Fable);
  `Source/bass_residual.py:36`, `Source/apply_automation.py:1013`, `Source/render_check.py:1755`
  (Astra, related loudness-compensation gap).
  Owner: Claude. Peer review: NONE - not yet reviewed.

- [ ] **Mix endings need a real trim/fade decision, not silent-tail warnings every time** (D2) -
  the held-out render's last 7.4s sit at -62dBFS (Jewel Kid's own documented fade-out), and
  `render_check`'s `exposed_solo` check flags it every time with no trimming mechanism to act on
  it. Astra frames this correctly: this needs an ending/trim policy decision, not automatic
  treatment as broken internal silence (which it isn't).
  Evidence: `Test Project/10.09.26 Tech House Heldout/Output/RENDER_CHECK.md:21` (Fable, path may
  be stale - re-render first); `Source/render_check.py:1501` (Astra).
  Owner: Claude, decision needs Sam on the policy. Peer review: NONE - not yet reviewed.

- [ ] **Two other real render warnings on this project need fresh-render re-measurement before
  any repair is sized** (D3): the T2 transition_dip (3.23dB, sub-band deficit) is UNBRACKETED -
  its true minimum lies outside the measured window, so don't size a fix from the current
  numbers; re-measure with more context on the fresh render from A2 first.
  Evidence: `Test Project/.../RENDER_CHECK.md:18` (path may be stale); `Source/render_check.py:1738,1772`
  (Astra).
  Owner: Claude. Peer review: NONE - not yet reviewed.

- [ ] **Restore reliable key/harmonic metadata without re-depending on MIK's UI automation**
  (D4) - this held-out run had no key data at all, so harmonic sequencing never ran; Astra found
  the sequencer substitutes a hardcoded `1A` for missing keys in a mixed known/unknown pool
  rather than preserving "unknown" honestly, which can silently mis-sequence. A chroma/Essentia
  fallback estimate would close this without adding back a Rekordbox-style dependency.
  Evidence: held-out `Output/Visualisations/REVIEW_A.md` "Known limitations" (both);
  `Source/automated_dj_mixes/orchestrator.py:638` (Astra).
  Owner: Claude. Peer review: NONE - not yet reviewed.

- [ ] **Wire the already-built `hints_from_stem_result` into `/mix`'s manual hint-authoring
  step** (D5) - the derivation exists and has a `--write-hints` flag, but Phase 1f still has Sam
  or Claude read four timestamps per track off the DETECT picture by eye. A prior audit measured
  ~20 min/project and ~2 misreads per 10 tracks for the manual version. Derive-then-adjust
  removes the misread class entirely; this has had no owner since it was carded 2026-09-02.
  Evidence: `Source/stem_detector.py:1362,1418` (Fable); `Claude Code Brain/commands/mix.md:124-154`
  (Fable).
  Owner: Claude. Peer review: NONE - not yet reviewed.

- [ ] **Surface vocal/density clash evidence to Sam as short suspect passages instead of leaving
  it as shadow-only logging** (D6) - vocal regions already gate loop-source selection, but the
  pipeline never compares both tracks' audible vocals ACROSS a transition, and outgoing density
  (the signal behind the entry-extension "chilled-out break" rule) is measured but never acted
  on. Don't introduce an unvalidated automatic penalty - produce a short list of suspect
  passages for Sam to listen to first.
  Evidence: `Source/align_engine.py:620,1770,1987` (Astra);
  `Documentation/Reviews/2026-08-27 Analysis Extraction Audit.md:98` (Astra).
  Owner: Claude. Peer review: NONE - not yet reviewed.

- [ ] **"Feasible" alignment pairs can still fail at the next stage (loop planning), and
  sequencing doesn't know that** (D7) - Astra's own corpus replay found 2 of 267
  alignment-successful historical pairs fail subsequent loop planning (e.g. Doorly -> Christoph,
  The Rise). The feasibility checker and the sequencer both only test alignment, leaving an
  avoidable reorder/retry for whoever's running the build.
  Evidence: `Source/alignment_feasibility.py:54`, `Source/align_engine.py:2009`,
  `Source/automated_dj_mixes/sequencer.py:192` (Astra).
  Owner: Claude. Peer review: NONE - not yet reviewed.

- [ ] **Playlist-complete recovery for borderline-beatgrid tracks is never auto-attempted**
  (D8) - `refit_grid_from_stem.py` is the documented escalation path for exactly the kind of
  near-miss that excluded Arielle Free/Idris Elba from this held-out set (16ms vs a 15ms gate),
  but nothing tries it automatically before excluding a track. In commissioned work this is
  either a track Sam mixes in by hand, or a client conversation that shouldn't be necessary.
  Evidence: held-out `REVIEW_A.md` (Fable); carded 2026-07-16.
  Owner: Claude. Peer review: NONE - not yet reviewed.

## E - Hygiene / technical debt (does not affect output quality today)

- [ ] **Render-check has real blind spots on every production (tempo-arc) mix, currently
  invisible because the flat experimental builds are the only ones exercising the checks fully**
  (E1) - on a tempo arc, `boundary_click` skips every boundary by name and grid drift cannot fail
  the render at all. No click has ever been reported on a flat render either (the only case where
  the check actually runs), so this is untested territory on the exact check most likely to
  catch an audible defect.
  Evidence: `Source/render_check.py:2235,2440` (Astra); Master Board line 27 (Fable, prior
  carding).
  Owner: Claude. Peer review: NONE - not yet reviewed.

- [ ] **`AI_CONTEXT.md` and `/mix` have drifted from what the code actually does** (E2): "What's
  Next" still opens with 2026-09-10 and 2026-09-01 TOPs and carries May-era items;
  `seal_listening_test.py`'s documented CLI (two positional WAVs) doesn't match its real one
  (`--side LABEL=path`, repeatable, plus `--twin-of`/`--out-dir`/`--seed`); `/mix` says A/B, the
  code builds A/B/C; `/mix` says to commit the held-out project, `.gitignore` ignores
  `Test Project/`; two hint overrides are documented as closed features that are actually inert
  (see C4). Several May-2026 planning docs (`TOMORROW.md`, `TODO_ARRANGE_MIX.md`,
  `PIPELINE_AUDIT.md`, `CODEX_REVIEW.md`) still sit at the documentation root.
  Evidence: `Documentation/AI_CONTEXT.md:1290-1411` (Fable); `Claude Code Brain/commands/mix.md:427,439-446,458,585`
  (Fable + Astra).
  Owner: Claude. Peer review: NONE - not yet reviewed.

- [ ] **`extract_sections_als.py`'s parser is silently broken by XML attribute reordering** (E3)
  - valid XML, but all clips vanish from its parsed result if `<AudioClip>`'s attributes are
  reordered, which would fall `apply_automation` back to the (potentially stale) sections JSON
  without any error. Found by Codex during this week's margin-fix review, logged not fixed at
  the time (commit 98efcbb) - carried here so it doesn't get lost.
  Evidence: `Source/extract_sections_als.py:44`.
  Owner: Claude. Peer review: NONE - not yet reviewed.

- [ ] **6 skipped tests are explained, not defects - but the explanation itself points at a real
  gap** (E4): 4 skips are a missing June golden fixture, 2 are intentional non-applicable cases,
  no `xfail` markers found anywhere. Recovering the missing golden fixture would restore real
  regression coverage; the skips themselves are not 6 outstanding mix defects and should not be
  read as such.
  Evidence: `Tests/test_align_engine_golden.py:24`, `Tests/test_swap_selection_replay.py:64`
  (Astra).
  Owner: Claude. Peer review: NONE - not yet reviewed.

- [ ] **Stale artifacts sitting beside this week's rebuilt files** (E5): `Mix A_pre-fix-
  backup.als`, a 614MB stale `Mix A.wav` no RENDER_CHECK.md any longer describes, and
  RENDER_CHECK.md itself describing an artifact that's already been superseded twice this week.
  Cheap cleanup once A2 lands.
  Owner: Claude. Peer review: NONE - not yet reviewed.

## F - Carried, deferred on purpose (not open work - listed so they are not silently rediscovered)

- Demucs stem-cache reuse across projects (every project currently re-runs Demucs separation
  even on a track already processed elsewhere). Sam, 2026-09-12: "not now, just one for the
  list." Do not re-propose without Sam's go.
- Cataloguing different valid "mix styles" and the track markers that should trigger each. Sam's
  own idea, 2026-09-12/13, deliberately waiting on the blind listen's real evidence (see A5)
  before scoping - both sweeps independently agreed this should stay deferred.

## G - Standing governance (never closes; listed so it is visible)

- Peers never see the repository root without a credential check first - `Credentials.txt`-class
  files were checked for and found absent before both sweeps in this round; re-check on any
  future sweep, don't assume it stays true.
- `interim_v1` remains the production default regardless of any experimental comparison's
  result - promotion needs Sam's own listening verdict, every round, no exception.
- Nothing here is armed - this list moves lines in a file, not money or credentials.
- One session per checkout; this list is the only place open work lives - not a chat, not a head.

Last item update: 2026-09-13 22:10 [Claude] - create: 25 items from a Fable sweep + an
independent Astra (`gpt-6-astra`) sweep, folded and cross-verified (2 headline citations spot-
checked directly against source, both confirmed accurate); rev (none) -> (initial).

## THE COUNT: 25 open, 0 done (last update 2026-09-13 22:10 [Claude]: first creation, two-lens sweep, nothing yet started)
