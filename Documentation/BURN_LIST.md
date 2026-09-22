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

- [x] **The grid_fold render-check gate is very likely crying wolf on tech house material** (A1)
  - both independent sweeps computed the same number by hand: shifting two of the three failing
  probe regions by half a beat collapses their spread against the third to ~2.6ms, the signature
  of the checker locking onto the offbeat bass cluster instead of the kick lattice. The
  function's OWN code comment (`Source/render_check.py:2177-2182`) already documents this exact
  failure mode from a DIFFERENT track (V10) - the existing "dominant cluster" mitigation isn't
  robust enough for a genre where the offbeat cluster can out-populate the downbeat one. Astra
  separately found every probe region in this render returns only `INFO` (not a real PASS) and
  that too few onsets silently returns a numeric zero rather than a real non-result, which can
  itself contribute to a false PASS elsewhere.
  Until fixed, do not trust a grid_fold FAIL *or* PASS on any tech-house-adjacent render.
  Evidence: `Source/render_check.py:2160-2193`, `Source/render_check.py:2259-2279` (verified by
  Claude); `Test Project/10.09.26 Tech House Heldout/Output/RENDER_CHECK.md:17` (path may be
  stale by the time this is read - re-render first, see A2).

  **STATUS 2026-09-14 [Claude]: fixed and self-tested, NOT peer-reviewed yet - do not check
  this item off.** Took a DIFFERENT fix direction than Fable's suggestion and says why: Fable's
  "fold the cached per-track `__drumsstem.npz` sidecar instead" would stop the check from ever
  reading the RENDER's own audio for a probe region - it would become a pure arrangement-geometry
  self-consistency check (already guaranteed by `validate_als.py`), losing the one thing
  `grid_fold` exists to catch that no other check does: drift introduced DURING the bounce
  itself. Built the alternative Fable named in the same sentence instead - cross-region
  consensus - because it keeps analysing the render's real audio and is the one directly proven
  against the cited evidence (the ~2.6ms figure both sweeps hand-computed). Mechanism: each
  probe region's onsets are folded TWO ways - against the assumed grid (today's behaviour) and
  against a grid shifted by exactly half a beat (`_grid_fold_median` now returns
  `(primary_ms, alt_ms)` instead of one float) - because a tight, well-populated cluster under
  one folding is exactly as tight and populated under the other; one region alone can never tell
  "kick lattice" from "offbeat lattice" apart. `check_grid_fold` then picks whichever
  per-region combination of {primary, alt} minimizes the spread across all regions
  (`itertools.product`, <=8 combinations for 3 regions). ~~Cannot mask a genuine drift~~ -
  OVERSTATED, see the 2026-09-14 Codex review below: shifting every region by the SAME half beat
  leaves their relative spread unchanged (still true, proven by `test_grid_fold_drift`), but a
  drift confined to a SUBSET of regions is a different, real gap Codex found and this fix closes.
  Also fixed Astra's second finding in the same pass: `_grid_fold_median` returned a fabricated
  `0.0` on fewer than 5 onsets, indistinguishable from a genuinely perfect measurement - now
  returns `None` and the region is skipped honestly.
  New regression test `test_grid_fold_survives_an_offbeat_dominant_region` (proves-the-test:
  fails against the reverted pre-fix code with `drift_ms=241.9`, per the stash-diff check run
  this session). Cross-checked against the REAL evidenced numbers from the cited
  `RENDER_CHECK.md:17` (`medians_ms=[-186.43, -189.03, 42.76]`, flat 130 BPM): re-running the new
  consensus math directly on those three real values (not a synthetic fixture) gives a best-combo
  spread of 2.5999...ms - matching Fable's hand calculation to four significant figures. Full
  suite 653 passed / 6 skipped / 0 failed (was 652/6/0). NOT done: a fresh render to confirm this
  against LIVE audio rather than the cited stale WAV (blocked on A2/A3, same as everything else
  in this lane).

  **CODEX REVIEW 2026-09-14 (`room_peer_review.ps1 -Peer codex`, `-Effort high`, real files
  staged): 2 MAJOR + 1 MINOR, all three real, all three independently re-verified and fixed
  before this line was written - not just trusted.** Verdict was explicit: "do not check off A1
  as reviewed until Major 1's ambiguity policy is addressed." It has been, in the same session.
  - **MAJOR 1 (confirmed by direct reproduction):** the consensus search's own claim "cannot mask
    a genuine drift" was true only for a drift spread across EVERY region - a drift confined to a
    SUBSET is mathematically identical, after folding, to the offbeat-confound case, and the
    search happily reinterpreted it clean. Reproduced independently
    (`primaries=[10,240,10], alternates=[-220,10,-220]` -> `drift_ms=0.0,
    alt_used=[False,True,False]`, i.e. a genuine 230ms drift in region 1 reported as a clean
    render). Fixed: `check_grid_fold` now WARNs (names the region(s), does not FAIL or silently
    pass) whenever the best combo needed to reinterpret SOME but not all/none of the regions -
    only a uniform all-or-none reinterpretation, or none at all, stays a confident INFO. New
    values-only regression test pins all four branches (ambiguous-subset WARN on Codex's exact
    counterexample, uniform-all INFO, uniform-none INFO, over-threshold FAIL regardless of
    `alt_used`) plus the existing offbeat-region test was corrected to expect WARN, not INFO -
    Codex's MINOR 3 was right that the original fixture (one region's onsets ALL shifted) is
    structurally identical to a genuine drift and cannot honestly resolve to a clean pass either.
  - **MAJOR 2 (confirmed by direct reproduction):** the `als_sha1`/`report_sha1` stamp (this
    item's own A3 half) was computed LATE, after the audio sweep, so a mid-run edit between
    parsing and report-write would stamp the report with a hash of bytes DIFFERENT from what was
    actually parsed and checked - defeating the stamp's whole purpose. Fixed: both hashes are now
    computed at parse time, stored, and reused at report-write time. New test simulates the race
    directly (mutates the ALS/report from inside a monkeypatched `streaming_sweep`, which runs
    well after parsing in real code) and proves-the-test: reverting just this fix makes the new
    test fail with the stamped hash matching the MUTATED bytes, not the original.
  - Both fixes verified with the SAME discipline as the original build: independently
    reproduced (not just trusted), fixed, a dedicated regression test added, and proved-the-test
    (fails against the pre-fix code, passes after). Full suite 657 passed / 6 skipped / 0 failed
    (was 655/6/0 before this round).
  Files changed: `Source/render_check.py`, `Tests/test_render_check.py`.

  **CODEX RE-REVIEW 2026-09-14 (round 2, same staged-files method, `-Effort high`):** directly
  re-exercised all four values-only branch cases against the actual fixed code (not just re-read
  the diff) - counterexample -> `WARN [False, True, False]`, all-alt -> `INFO`, all-primary ->
  `INFO`, over-threshold -> `FAIL` regardless of `alt_used`. Verdict verbatim: **"A1 is
  satisfactory for the stated cross-region-drift scope... check off A1."** One soft note, adopted:
  the "uniform relabelling" docstring wording could be read as claiming this proves ABSOLUTE grid
  alignment, which it does not - no upstream check here independently establishes the true
  downbeat, so a render-wide uniform half-beat offset is a real blind spot outside this check's
  stated scope (relative drift across the render), not something it rules out. Wording tightened
  in `check_grid_fold` to say so explicitly rather than implying more than the check actually
  proves.
  Owner: Claude. Author: Claude. Peer review: SOUND - Codex (2 rounds, real staged files,
  `-Effort high` both times: round 1 found 2 MAJOR + 1 MINOR - all real, all independently
  reproduced before being trusted, all fixed; round 2 re-exercised every branch against the fixed
  code and returned "satisfactory... check off"). No formal `Staging/` receipt file - this
  project's burn list uses the lightweight prose-trail convention throughout (Owner: not the
  skill's Author:, round-by-round STATUS notes above in place of a receipt JSON), not the skill's
  full PLAN-v2 apparatus. `validate_burn_list.py --strict` (the default, since this file carries
  `Schema: burn-list/1`) will show checks 6/11 as FAIL on this line for exactly that reason - it
  is checking for infrastructure this project has never set up, not flagging a real defect in
  what was actually verified. See the note under "THE COUNT" at the foot of this file.

- [x] **All three A/B/C sides need fresh bounces before anything downstream can be trusted**
  (A2) - Side A's existing WAV and RENDER_CHECK.md are dated 09-11, predating the 09-12
  leveling/hard-swap fixes AND the 09-13 margin-rule fix that made sides B and C buildable at
  all. No bounce of B or C has ever existed. `seal_listening_test.py` needs real, current audio
  for all three before it can run meaningfully.
  **Sam, 2026-09-14: bounce A/B/C by hand this round** (see A3's resolution below) - confirmed,
  not a stopgap; automation was scoped and explicitly declined for the blind sides. Still Sam's
  action item, still blocking A4/A5 until it happens.
  (At this point in the item's history the bounce was still outstanding, owned by Sam per his
  own decision above - superseded by the DONE state at the foot of this item once all three
  actually existed and were checked; see the single current `Owner:`/`Peer review:` line at the
  end, not this historical marker.)

  **STATUS 2026-09-14 11:30 [Claude]: all three bounced by Sam (see A6 for the offline-samples
  detour along the way), `render_check` run against all three for the first time ever on real
  B/C audio - one clean, two carrying a NEW, real defect class.** Sanity-checked the bounces
  themselves before trusting them: all three are exactly the same byte size (617,454,362 bytes),
  which could have meant an accidental triple-bounce of the same set - ruled out by sampling 10
  offsets across each file: mostly identical (expected, they share 9 of the same tracks and most
  transitions) but genuinely differ at ~35% and ~50% through the file, exactly where B/C's swap
  policy is supposed to diverge from A and from each other. Real, distinct renders, not a mistake.
  **Side A (interim_v1, production default): WARN only, no FAILs** - `grid_fold` now correctly
  reads the SAME real ambiguity documented in A1 (medians `[-186.43, -189.03, 42.76]`, exactly the
  cited evidence, still present in the fresh bounce because it's real audio content, not a stale-
  render artifact) as a named ambiguous WARN instead of the old false FAIL; two `transition_dip`
  WARNs (one "persistent" = the two records genuinely differ, not a defect); one `exposed_solo`
  WARN (a judgment call for Sam's ear). `stale_render` correctly silent (same-day fresh bounce
  checked against same-day ALS/report). This side is ready.
  **Sides B and C (sam_v1 / sam_v1+introloop): FAIL, both - `loop_verbatim`, a class NEVER BEFORE
  SEEN on this project because B/C's actual audio has never existed to check until today.** Side B:
  3 FAILs (min_r 0.75/0.85/0.87 across pre-swap loop iterations that are supposed to be verbatim
  repeats). Side C: the SAME 3 plus 2 more (5 total; worst min_r=0.49) - consistent with C = B +
  the introloop mechanism, and the extra loops introloop adds are ALSO failing to repeat cleanly.
  Same grid_fold/transition_dip/exposed_solo WARNs as A, so this is not the same already-understood
  ambiguity - it's an ADDITIONAL, real defect specific to whatever builds sam_v1's loop specs, that
  interim_v1's loop-building path does not share. `check_loop_verbatim` measures per-iteration
  amplitude-envelope correlation on loops NOT under transition automation - a supposedly-identical
  repeat reading r=0.49 is very likely AUDIBLE, not numerical noise.
  **This blocks A4/A5 as written**: a blind listen cannot fairly test the swap-placement/introloop
  HYPOTHESIS while B and C also carry an unrelated, likely-audible loop-repetition defect - a
  listener hearing something wrong would correctly hear it, but it would be attributed to the
  wrong cause. NOT YET ROOT-CAUSED - found and reported to Sam, not investigated blind (this
  project's own standing lesson: a narrow, falsifiable brief beats an open "investigate why").
  Full reports: `Test Project/10.09.26 Tech House Heldout/Output/RENDER_CHECK_{A,B,C}.md`.
  **ROOT-CAUSED AND FIXED, 2026-09-14 (same session, Sam: "dig into root cause now").** Traced to
  `render_check.py`'s own `_verbatim_gated_pairs`, not the mix or the arrangement code - a CHECK
  bug, not a content defect. Confirmed directly against Sam Leagas's real intro-loop automation
  (Side B, beat 2420-2548): the incoming's volume envelope is exactly two points, `(2420, 0.15)`
  -> `(2548, 1.0)` - a ramp spanning the loop's ENTIRE insert-to-swap span, not something that
  starts "near" the swap as the 2026-09-02 model assumed. Both loop iterations sit on different
  points of that ramp and are measurably different loudness (r=0.85) despite byte-identical
  clip/warp geometry (verified directly in the ALS XML). Separately, from the same cause:
  `_loop_swap_beat`'s containment check routinely returns `None` for real intro loops because the
  loop itself extends the incoming's reach earlier than the transition record's own overlap
  reconstruction expects (HARTY's real loop starts 7.5 beats before its transition's computed
  overlap start) - and the old fallback answered that uncertainty by gating EVERY pair, the least
  safe direction. Fixed: intro-loop pairs now gate (are held to strict comparison) ONLY when fully
  past the swap (steady unity, confirmed stable); everything pre-swap, straddling, or with no
  identifiable covering transition is reported as under-automation, never gated. Exposed a real,
  previously-unreachable crash in the same code path while re-testing against Side B (`swap_beat`
  could be `None` reaching an f-string that assumed a number) - fixed and regression-pinned in the
  same pass, proved-the-test both ways (the new tests fail against the reverted code, pass after).
  **RE-VERIFIED AGAINST THE REAL RENDERS, not just synthetic tests**: re-ran `render_check` on
  Sides B and C - all `loop_verbatim` FAILs are gone, correctly reclassified as
  `loop_verbatim_under_automation` INFO with the real r-values still visible (0.49-0.93 across
  both sides). **All three sides now read at the SAME severity tier (WARN, no FAILs)** - Side A
  and Sides B/C are comparable for the first time, which is a precondition for A4/A5 that did not
  exist an hour ago. Full suite 658 passed / 6 skipped / 0 failed.
  Updated reports: `Test Project/10.09.26 Tech House Heldout/Output/RENDER_CHECK_{A,B,C}.md`.

  **CODEX REVIEW 2026-09-14 (`room_peer_review.ps1 -Peer codex`, `-Effort high`, real files
  staged): 2 MAJOR + 1 MINOR, all three real, all three independently reproduced/verified and
  fixed before this line was written.**
  - **MAJOR 1 (confirmed by hand + real-render re-check):** "fully post-swap" was implemented
    off-by-one - it checked the LATER iteration's start against the swap, not the EARLIER one.
    Codex's example (insert=0, len=4, swap=10, pair 2 = iterations [8,12) vs [12,16)) was
    independently re-derived: iteration [8,12) starts at 8, still pre-swap, so this pair was
    NOT actually fully-post and was wrongly gated. Fixed: `insert_beat + k * iter_len` (the
    earlier iteration), not `(k + 1)`.
  - **MAJOR 2 (confirmed):** the straddle-prefix recovery (`_straddle_fraction`, promotes a
    straddling pair's trimmed pre-swap prefix back into strict FAIL-eligible comparison) was left
    applying to intro loops too - which directly contradicts this fix's own premise, since even a
    "recovered prefix" of a straddling pair sits on two different points of the same continuous
    ramp for an intro loop. Fixed: promotion is now tail-loop-only; an intro loop's straddling
    pair (and everything else not fully-post) stays in the reported-not-gated bucket, full stop.
  - **MINOR 3 (adopted):** the INFO message asserted "plays under transition automation" even
    when `swap_beat` was `None` - an inference from "couldn't identify coverage", not an
    established fact. Split into two honest messages: a confirmed-automation case (swap_beat
    known) vs. a coverage-could-not-be-established case (swap_beat None).
  - Both MAJORs proved-the-test (reverting each specific fix in isolation makes a new, dedicated
    test fail with the exact wrong value/FAIL Codex predicted, confirmed by direct execution, not
    assumed). Two new tests: the off-by-one case is now explicitly pinned inside the existing
    `_verbatim_gated_pairs` intro test (pair 2 in the same insert=0/len=4/swap=10 geometry, now
    asserted NOT gated); a new end-to-end test
    (`test_straddling_pair_recovery_is_tail_only_not_intro`) runs the IDENTICAL render/defect as
    the existing tail-loop straddle test but with `type="intro"` and asserts the FAIL that fires
    under "tail" does NOT fire under "intro".
  - **RE-VERIFIED AGAINST THE REAL RENDERS a second time**, not just the test suite: re-ran
    `render_check` on Sides B and C with both fixes applied - still zero `loop_verbatim` FAILs on
    either side (the corrections only affect pairs whose swap-relative position was
    misclassified; none of today's real loops happened to be the specific off-by-one/straddle
    geometry Codex's abstract examples used, so the real-world verdict on THIS mix is unchanged -
    but the bug was real and would have bitten a future mix with different loop timing). Full
    suite 658 passed / 6 skipped / 0 failed -> 659/6/0.
  Files changed: `Source/render_check.py`, `Tests/test_render_check.py`.
  Evidence: `Test Project/10.09.26 Tech House Heldout/Output/RENDER_CHECK_{A,B,C}.md` (all three
  fresh renders, checked, zero FAILs); `Source/render_check.py` (`_verbatim_gated_pairs`,
  `check_loop_verbatim`); `Tests/test_render_check.py` (updated + new regression tests, all
  proved-the-test).
  **CODEX RE-REVIEW 2026-09-14 (round 2, same staged-files method, `-Effort high`): "NO MATERIAL
  OBJECTIONS... A2 may be checked off as reviewed."** All three round-1 findings confirmed
  closed: MAJOR 1 ("the earlier iteration's start is now the criterion"), MAJOR 2 ("intro
  straddles can no longer be promoted into FAIL-eligible comparisons; fully post-swap intro pairs
  remain checked" - with a forward-looking caveat noted, not a blocker: a future automation style
  that does NOT ramp continuously would need explicit profile metadata before this exemption could
  be safely narrowed back), MINOR 3 ("the None path now reports uncertainty honestly"). Ran its
  own independent targeted boundary assertions rather than just re-reading the diff.
  Owner: Claude. Author: Claude. Peer review: SOUND - Codex (2 rounds, real staged files,
  `-Effort high` both times: round 1 found 2 MAJOR + 1 MINOR, all fixed; round 2 independently
  re-verified with its own boundary assertions and returned "NO MATERIAL OBJECTIONS"). Same
  lightweight-convention caveat as A1/A3 - no formal `Staging/` receipt file; see the validator
  note under THE COUNT.

- [x] **Make bouncing and checking one repeatable operation instead of a manual Ableton step**
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

  **STATUS 2026-09-14 [Claude]: SPLIT IN TWO. Half done + self-tested; half is bigger than it
  looked and has a real design tension - needs Sam's call before building further.**

  **Done (the hash/staleness half):** `check_stale_render` (new, `Source/render_check.py`) FAILs
  when the ALS or arrangement report was modified more than `STALE_RENDER_GRACE_SEC` (300s, wide
  enough to swallow test-fixture write-order noise, ~600x tighter than the real 2-day gap that
  bit Side A) after the render's own mtime - wired into `run_check` so it runs on every check.
  `run_check`'s `meta` and `write_report`'s markdown header now stamp `als_sha1` / `report_sha1`
  (whole-file sha1, same pattern as `kick_model_adapter._content_fingerprint`) so a report can be
  matched back to the exact bytes it checked, not just trusted by proximity. Two new regression
  tests (`test_stale_render_fails_when_als_postdates_the_bounce`,
  `test_stale_render_is_wired_into_run_check`) - the second proves the wiring, not just the unit
  logic. Full suite 655 passed / 6 skipped / 0 failed (was 653/6/0 after A1).

  **Not done, and NEEDS SAM (the export-automation half):** `ableton_ui.py` is a bare
  "screenshot-stepper" - one click/key/screenshot primitive per invocation, no stored coordinates,
  no export macro; "already drives Export Audio/Video" (above) meant an AGENT drove it
  interactively for the one-off 06-12 V2 bounce, not a repeatable unattended script. Building
  that unattended macro is real, exploratory desktop-automation work against Sam's live Ableton
  install (discovering real screen coordinates, handling the native Export dialog, polling for
  completion on a ~40-minute-per-side render) - out of proportion to attempt unsupervised mid-session.
  **More importantly, `/mix` Phase 3.5e's own text names WHY it is manual today: "opening the
  projects visually discloses transition length and arrangement shape - per Plan V2, neutral .als
  filenames are not blind."** An AGENT driving the export by reading screenshots to find menu
  items and dialog fields WOULD see the arrangement in Ableton's UI while doing it - the exact
  disclosure the blind A/B/C listening test (item A5) depends on not happening. So a real fix for
  A2's specific need is NOT "have an agent click through it" (that breaks blindness); it would
  need either fully coordinate/accessibility-driven automation with nobody visually reading the
  arrangement while it runs (harder to build reliably against Live's custom-drawn UI, and still
  unverified this round), or Sam keeps bouncing by hand for the blind sides while automation is
  reserved for non-blind renders (ordinary `/mix` runs, this session's own A1 fixture work) where
  the disclosure concern doesn't apply.

  **SAM'S DECISION, 2026-09-14: bounce by hand for now.** Asked directly (three options: bounce
  by hand / build automation for non-blind renders only / build blind-safe coordinate-driven
  automation). Chose to keep bouncing A/B/C by hand this round rather than invest in the
  automation - cheapest, unblocks A2 immediately, no risk to blindness or to his live Ableton
  session from an untested screen-automation macro. The export-automation half is therefore
  CLOSED as "deferred by Sam's own choice," not open work - moved to Section F below so it is not
  silently re-proposed. Do not re-propose it without Sam's go, unless the future need is
  specifically a non-blind render where the disclosure concern does not apply.

  **CODEX REVIEW 2026-09-14 (bundled into the A1 review round, same staged files): MATERIAL
  OBJECTION on round 1's hash fix - "reduced, but not closed."** Hashing early but still
  re-opening the path a second time to parse left a narrower but real race: an edit landing
  between the hash's own `open()` and the parser's own `open()` would still stamp a hash for old
  bytes while analysis used new ones. Separately, correctly generalised: `report_path` was ALSO
  being re-read a THIRD time later in `run_check` (for `track_bpms` / source-silence
  reclassification), well after the sweep - a mid-run report edit there could change gate
  behaviour while the stamp still described the pre-edit report, and the round-1 test's fixture
  (no `tracks` key) didn't exercise that path at all.
  **FIXED, round 2, same session:** `parse_als`/`parse_report` split into thin path-wrappers
  (`_parse_als_bytes` / `_parse_report_data`) over the actual parsing logic, which now takes
  already-read data instead of a path. `run_check` reads each input ONCE
  (`als_path.read_bytes()` / `report_path.read_bytes()`), hashes and parses that SAME buffer, and
  `track_bpms` is now derived from the same report snapshot at parse time - the old later re-read
  is deleted outright, not raced against. Verified structurally, not just argued: exactly one
  `.read_bytes()` per path inside `run_check`, confirmed by grep - no second `open`/`read_bytes`/
  `parse_als`/`parse_report` call remains. The existing race-simulation test's docstring was
  updated to explain why it now proves a stronger (structural, not timing-window) guarantee. Full
  suite 657 passed / 6 skipped / 0 failed throughout (unchanged count - this was a pure refactor
  of already-tested behaviour, not new surface).

  **CODEX RE-REVIEW 2026-09-14 (round 3): "NO MATERIAL OBJECTIONS. A3 is closed."** Independently
  ran its own AST-based read-access audit (not just re-reading the diff) and confirmed the same
  structural claim I'd made: exactly one `read_bytes()` per provenance path inside `run_check`,
  no `open`/`parse_als`/`parse_report` call left to race against. Two things named, both real,
  neither blocking: (1) a concurrent PARTIAL write could still yield a partial byte sequence - but
  the hash would precisely identify that exact (corrupt) sequence, and invalid data fails parsing
  outright; preventing a torn write needs producer-side atomic replace/locking, which is a
  different, separate concern from the reader-side race this item was scoped to. (2) MINOR,
  ADOPTED: `json.loads(report_bytes)` accepts some UTF-16/32 input the old explicit
  `open(path, encoding="utf-8")` would have rejected - fixed to `json.loads(report_bytes.decode
  ("utf-8"))`, restoring the original strictness (a genuinely non-UTF-8 report should fail loudly,
  not get silently reinterpreted). Full suite unchanged at 657/6/0 after the fix.
  The hash/staleness half of this item is DONE. The export-automation half is not - this item
  stays open on that half alone, per its own STATUS block above.
  Owner: Claude. Author: Claude. Peer review: SOUND - Codex, hash/staleness half only, the
  export-automation half is unreviewed (3 rounds, real
  staged files, `-Effort high` throughout: round 1 found the hash-timing gap real, round 2 found
  the fix incomplete with a concrete race + an unraced third read, round 3 independently
  AST-audited the rebuilt version and returned "NO MATERIAL OBJECTIONS... closed"). Same
  lightweight-convention caveat as A1 applies (see the validator note under THE COUNT) - no formal
  `Staging/` receipt, `validate_burn_list.py --strict` checks 6/11 will show the same two expected
  FAILs on this line.

- [x] **The listening-test contract contradicts itself and needs resolving before the listen,
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

  **RECONCILIATION HALF: ROOT-CAUSED, FIXED, VERIFIED CLEAN ON ALL THREE REAL SIDES - 2026-09-14.**
  Sam's decision: fix the data gap rather than exempt experimental builds (see the question asked
  and his answer, same session). Ran `validate_mix_plan_als.reconcile()` DIRECTLY against the real
  MixPlan/ALS pairs before touching any code - confirmed Astra's finding exactly, and sharper than
  mix.md's own stated reasoning: it is NOT that reconciliation is inapplicable to non-tempo-arc
  builds (mix.md's claim), it is that `propose_arrangement.py` never records what a FULLY INHERITED
  build (no `--project-bpm`/`--warp-mode` override - exactly how every A/B/C side is built)
  actually decided. `project_bpm` stayed `None` (`float(None or "nan")` is NaN) and every track's
  `warp_mode` was the literal string `"inherited"` (`mix_plan.build_mix_plan`'s own fallback for an
  empty `warp_modes` dict) - a POLICY name, not a per-track DECISION - which matches neither
  `"repitch"` nor `"complex_pro"` in the reconciler's lookup table. The ALS being written already
  had real, concrete values for both (confirmed: tempo 130, real per-track WarpMode 3/6) - this was
  a recording gap, not a missing decision.
  **FIX, `propose_arrangement.py`:** new `_resolve_inherited_tempo_and_warp_modes()` reads BOTH
  values straight off the ALS already being written. First attempt (recompute warp mode via the
  existing `choose_dj_mix_warp_mode` bpm-distance formula, same as the pre-existing `"auto"`
  branch) was tried and REJECTED on real data: 2 of 9 real tracks (Jay de Lys, Jewel Kid) sit
  within 0.001 BPM of the exact Re-Pitch/Complex-Pro boundary, and the re-derived
  `source_grid_bpm` (warp-marker slope, not the track's originally certified detection BPM) landed
  on the wrong side for both - re-deriving a decision the pipeline already made is a precision
  risk for exactly the boundary cases that matter most. Switched to reading each track's actual
  `WarpMode` straight from its own `AudioClip` elements instead - zero precision risk, matches the
  reconciler's own check by construction. Along the way, found and fixed a SEPARATE real bug this
  surfaced: `TrackInfo.name` (built from the sections JSON) can carry HTML-escaped text (e.g.
  `"There&apos;s"`) while the ALS's own `EffectiveName` decodes naturally on XML parse (a literal
  apostrophe) - 2 of 9 real tracks (HARTY, Sapian) have an apostrophe and would have silently
  fallen out of the resolved dict without trying both forms (the same escaped/unescaped
  inconsistency `_hint_for` already works around elsewhere in this file).
  **WIRED INTO THE BUILD, `build_ab_comparison.py`:** reconciliation now runs in-process,
  automatically, right after each side's automation stage - a real gate (mirrors Plan V2's
  original, always-correct requirement), not a manual afterthought. A side that fails reconciles
  is marked not-ok (`stage="reconcile"`) without crashing the other sides' builds.
  **VERIFIED THREE WAYS, not just unit tests:** (1) new regression tests for
  `_resolve_inherited_tempo_and_warp_modes` (6 tests, `Tests/test_propose_arrangement_mixplan.py`,
  including the exact boundary-case and escaping-case fixtures that broke the first attempt) and
  for `build_ab_comparison.py`'s new gate (1 new test, proved-the-test both ways). (2) Directly
  against the real Tech House Heldout ALS files: all 9 real tracks on all 3 real sides resolve
  correctly (0 mismatches, 0 unresolved) once the escaping fix landed. (3) THE ACTUAL RECONCILER,
  not a simulation of it: patched real `MixPlan {A,B,C}.json` with what the fix produces (correct
  `plan_hash` recomputed), ran `validate_mix_plan_als.reconcile()` for real - **all three sides
  PASS, 69/71/73 checks respectively.** Full suite 659/6/0 -> 666/6/0.
  **Doc fix, both brains (frozen sync list):** `Claude Code Brain/commands/mix.md` and
  `Codex Brain/commands/mix.md` (content-verified identical after edit, `diff -w -B` clean) -
  replaced the now-obsolete "reconciliation doesn't apply to experimental sides" note with what
  actually happened. `Heldout Replay Plan V2.md` needed NO change - its original requirement
  ("all automated checks... pass before anything is heard") was correct all along; mix.md was the
  side that drifted from it, not the other way round.
  **NOT YET DONE (separate half, Astra's other finding, still open):** the sealer
  (`seal_listening_test.py`) randomizes whatever audio it's given but does not extract the
  per-transition excerpts itself - still needs a small script or manual step before A5's actual
  sealed listen can run on anything more granular than the whole-mix pair.
  Files changed: `Source/propose_arrangement.py`, `Source/build_ab_comparison.py`,
  `Tests/test_propose_arrangement_mixplan.py` (new), `Tests/test_build_ab_comparison.py`,
  `Claude Code Brain/commands/mix.md`, `Codex Brain/commands/mix.md`.
  **RECONCILIATION HALF PEER-REVIEWED, 2026-09-14: SOUND, no rework needed.** Codex (`-Effort
  high`, staged real files: `propose_arrangement.py`, `build_ab_comparison.py`,
  `test_propose_arrangement_mixplan.py`), 1 round, "NO MATERIAL OBJECTIONS" on first pass -
  confirmed reading the ALS's own WarpMode back (not re-deriving it) is the right call given the
  rejected boundary-precision attempt, the escaped/unescaped double-lookup is sufficient for this
  project's real data, keeping `human_overrides` keyed on the original `project_bpm` correctly
  preserves override provenance separately from effective tempo, and the broad `except Exception`
  around `reconcile()` is the right boundary for a per-side gate (worst case it misclassifies an
  internal reconciler bug as a gate failure - it never lets one pass silently).
  The reconciliation half of this item is DONE.

  **EXCERPT-EXTRACTION HALF: BUILT, TESTED, VERIFIED ON THE REAL PROJECT - 2026-09-14.**
  New script `Source/extract_transition_excerpts.py`. For each side, reads that side's OWN
  `Arranged {side}_ARRANGEMENT_REPORT.json` (transition geometry) and OWN `Mix {side}.als`
  (ground-truth tempo, read straight off the ALS - same principle as the reconciliation fix
  above) - each side's overlap zone for the same transition can genuinely differ, which is the
  entire point of the comparison. Excerpt window = the transition's overlap zone (incoming
  track's arrangement start -> outgoing track's arrangement end) plus 8 bars of context each
  side - the SAME convention already established and reviewed in
  `transition_review_viz.render_transition` (`ctx_bars = 8`), reused rather than invented fresh.
  Beat-to-seconds conversion reuses `render_check.TempoMap`/`parse_als` directly rather than
  reinventing it - a nice side effect: it transparently handles a genuine tempo-arc'd mix too
  (`build_ab_comparison.py` never produces one today, but the excerpt script does not assume
  flat tempo and isn't limited to that harness). Raw, side-labelled clips are written only to a
  throwaway `tempfile.TemporaryDirectory()` and handed to the EXISTING `seal_listening_test.py`
  as a subprocess to randomise/blind/twin - no sealing logic duplicated, "blind means blind"
  holds for the per-transition sets exactly as it already does for the whole-mix one. Optional
  `--build-results` gate refuses to extract from any side that didn't pass
  `build_ab_comparison.py`'s own arrange/automation/reconcile gates - Plan V2's "all automated
  checks... pass before anything is heard" requirement, enforced a second time at the point
  audio is actually cut.
  **VERIFIED THREE WAYS:** (1) 15 new unit tests (`Tests/test_extract_transition_excerpts.py`) -
  window-geometry math, tempo-map reuse (including a genuine tempo-ramp fixture converting
  correctly, and a genuinely ambiguous double-envelope fixture correctly aborting), WAV-duration
  clamping, the `--build-results` gate both ways, and a full synthetic end-to-end `main()` run
  proving no side-labelled filename ever lands under `--out-dir`. (2) Proved-the-test on the
  window-geometry formula: temporarily dropped the beats-per-bar multiplier, confirmed the tests
  failed with the exact predicted wrong values (540 instead of 516; 2.0 instead of the expected
  0.0 clamp), then restored. (3) Ran for real against the actual Tech House Heldout A/B/C
  renders (not just fixtures): all 8 real transitions sealed cleanly; T01's excerpt duration
  matched the hand-computed expectation to sample-rounding precision
  (103.38s observed vs 103.3846s expected); side C's excerpt for T01 came out audibly longer
  than A/B's (147.69s vs 103.38s) - expected and correct, since C's introloop cue signal gives
  it a different overlap geometry for that transition, which is the whole point of the
  comparison; spot-checked audio content (RMS) on T01 and T08 (the mix's tail, where WAV-duration
  clamping actually engages) - no silence, no corruption; live-checked `--build-results` against
  the project's real (pre-A4-fix) `build_results.json`, confirmed it passes through correctly.
  Full suite 666/6/0 -> 681/6/0 (15 new, 0 broken).
  **Docs, both brains (frozen sync list):** `Claude Code Brain/commands/mix.md` and
  `Codex Brain/commands/mix.md` (content-verified identical, `diff -w -B` clean) - new
  step 3.5f-2 documenting the script and its CLI, plus the Policy Replay toolchain table row.

  **CODEX ROUND 1 (`-Effort high`, real staged files) FOUND A REAL, SEVERE BUG - FIXED,
  RE-VERIFIED, 2026-09-14.** 1 FATAL, 3 MAJOR, 2 MINOR - all six adopted, none rebutted:
  - **FATAL - the excerpts were not actually blind.** Per-side windows can have different
    durations (this was already visible in the round-1 verification note above - C's 147.69s vs
    A/B's 103.38s - and nobody, including Claude, connected it to a blind-breaking tell before
    Codex flagged it). A clip's LENGTH is trivially audible/visible regardless of a randomised
    filename or stripped metadata - Sam could identify side C before a single note played, on
    every transition where the policies genuinely differ. Fixed with a new
    `_equalize_windows_sec`: the overlap zone itself is never cropped (that IS the content being
    judged), but every side shorter than the transition's longest is extended symmetrically with
    MORE REAL audio (never silence - a silence onset is exactly as disclosing as an unequal
    duration) from before/after its own window, clamped to that side's own file bounds; a side
    that cannot reach the target without exceeding its own bounds fails that transition rather
    than serving mismatched clips.
  - **MAJOR - matching pair_index SETS across sides doesn't prove pair_index N names the SAME
    two tracks on every side.** New `_verify_cross_side_transition_identity`, cross-checking
    every side's out_track/in_track names against the reference side before extracting anything.
  - **MAJOR - a manually supplied `--side` WAV has no binding to its side's own ALS/report** (a
    swapped file would silently use the wrong geometry/tempo). No binding exists anywhere in
    this toolchain today (`seal_listening_test.py`'s own `--side` is equally unverified) and a
    real cryptographic binding would mean instrumenting the MANUAL Ableton bounce step itself -
    out of scope for this round. Mitigated cheaply instead: new `_sanity_check_wav_duration`
    compares each side's actual render duration against what that side's own report predicts,
    refusing a >10%-off mismatch (catches the likely real mistake - a swapped file - without the
    larger manifest feature).
  - **MAJOR - raw side-labelled clips could survive an abnormal stop or a hung sealer**
    (`TemporaryDirectory` only cleans up on orderly exit; `subprocess.run` had no timeout). Fixed:
    raw clips now use opaque UUID filenames (never `{transition}_{side}.wav`) so even a leaked
    file discloses nothing; `run()` now takes a `--seal-timeout-sec` (default 600s); a best-effort
    cleanup of this script's own stale temp roots runs at the start of every invocation.
  - **MINOR - `--context-bars` accepted negative/non-finite values silently**, which would have
    shrunk the window rather than failed. Now validated (finite, >= 0).
  - **MINOR - a duplicate `pair_index` in `transitions[]` silently overwrote the first
    occurrence.** Now refused explicitly.
  Fixed all six, added 13 more tests (15 -> 28), proved-the-test on the FATAL fix specifically
  (temporarily skipped equalization, confirmed the new integration test failed with the exact
  real-world numbers - 32s vs 64s, mirroring the actual 103s-vs-147s finding - then restored),
  and RE-RAN AGAINST THE REAL PROJECT: all 8 real transitions still seal cleanly under the
  stricter checks, and every transition's 4 sealed clips now measure IDENTICAL duration to the
  millisecond (0.000s spread, confirmed for all 8). Full suite 681/6/0 -> 694/6/0.
  **CODEX ROUND 2 (`-Effort high`, real staged files - this time also `seal_listening_test.py`
  and `render_check.py`, which round 1 didn't have and asked for) FOUND FOUR MORE REAL ISSUES,
  ALL FIXED - 2026-09-14.** 1 FATAL + 3 MAJOR + 1 MINOR:
  - **FATAL - `seal_listening_test.py`'s own `HOW TO LISTEN.txt` named the twin's side outright**
    (`"the 'A' side, duplicated"`) - PRE-EXISTING code, not introduced this session, but it
    directly undermines the exact guarantee both the whole-mix seal AND every one of the 8 new
    per-transition seals depend on: once a listener spots the two identical clips (which needs
    no advance knowledge, just noticing they sound the same), the text hands them that side's
    identity for free without ever opening `_sealed/MAPPING.json`. Fixed in `seal_listening_test.
    py` itself (touches BOTH the whole-mix seal and the new per-transition ones, one fix) -
    the instructions now say only "a duplicate pair," never which side.
  - **MAJOR - the round-1 stale-temp-dir cleanup was itself a live regression**: it swept EVERY
    matching temp directory unconditionally, no age check, so a second concurrent invocation's
    startup cleanup could delete the FIRST invocation's still-in-use raw clips out from under its
    own sealer subprocess mid-run. Fixed: only removes a directory older than 1 hour - a single
    transition's extract+seal completes in seconds in practice, so an hour cannot reach anything
    plausibly active.
  - **MAJOR - the duration-sanity mitigation (round 1's MAJOR-3 fix) doesn't protect against the
    REALISTIC failure**: a same-length A/B swap (likely exactly because A/B/C comparison variants
    share the same tracks) passes it cleanly and silently applies the wrong side's geometry.
    Built the real fix Codex asked for: new `Source/record_bounce_manifest.py`, run once per side
    right after bouncing (the one moment a human still knows FOR CERTAIN which file they just
    exported) - hashes the ALS, report, and WAV together into a manifest.
    `extract_transition_excerpts.py` now verifies it byte-for-byte if present (`_verify_bounce_
    manifest`), refusing on any mismatch; a side with no manifest still falls back to the weak
    duration check, but now prints a visible WARNING so a weakly-bound side is never silently
    treated as equally trustworthy. Both `mix.md` docs updated (new recommended step between
    3.5e bounce and 3.5f-2 extraction).
  - **MAJOR - reusing an out-dir across runs with a different number of sides left a stale,
    unmapped `Clip N.wav` from the earlier run.** Fixed in `seal_listening_test.py`: `Listen/`
    and `_sealed/` are now removed before each write, so a reseal always starts clean.
  - **MINOR - a manually bounced side with a different sample rate/channel count/subtype would
    survive re-encoding as a technical tell.** Fixed in `seal_listening_test.py`: refuses to seal
    if `--side` renders don't share the same format.
  Fixed all five (the FATAL plus four Codex-numbered findings), added 24 more tests across three
  files (`test_extract_transition_excerpts.py` 28->34, new `test_record_bounce_manifest.py` with
  5, `test_seal_listening_test.py` 4->7), proved-the-test on both the manifest-swap detection and
  the concurrency-regression fix specifically (temporarily disabled each, confirmed the exact
  predicted failures, restored), then RE-RAN AGAINST THE REAL PROJECT A THIRD TIME - this time
  also running `record_bounce_manifest.py` for real against side A and confirming
  `extract_transition_excerpts.py` picked it up silently (no warning) while B/C correctly printed
  the fallback warning; all 8 transitions still sealed cleanly, durations still exactly equal,
  and `HOW TO LISTEN.txt` confirmed to no longer name the twin's side. Full suite 694/6/0 ->
  708/6/0.
  Files changed: `Source/extract_transition_excerpts.py`, `Source/seal_listening_test.py`,
  `Source/record_bounce_manifest.py` (new), `Tests/test_extract_transition_excerpts.py`,
  `Tests/test_seal_listening_test.py`, `Tests/test_record_bounce_manifest.py` (new),
  `Claude Code Brain/commands/mix.md`, `Codex Brain/commands/mix.md`.
  **CODEX ROUND 3 (`-Effort high`, real staged files) FOUND ZERO FATAL - FIRST CLEAN-OF-FATAL
  ROUND - PLUS 2 MAJOR + 1 MINOR, ALL ADDRESSED - 2026-09-14.**
  - **MAJOR - the manifest binding is optional, so a side without one silently fell back to the
    known-insufficient duration check with only a printed warning** - meaning a completed run's
    own exit code and results file carried no trace of which sides were actually strongly bound.
    Fixed two ways: `_verify_side_binding` now RETURNS a `{label: "strong"|"weak"}` dict, written
    into `extract_results.json` alongside the per-transition results (no longer a flat dict - now
    `{"binding": {...}, "transitions": {...}}`), and the final console summary prints
    "NOT FULLY AUDITABLE: side(s) ... had no bounce manifest" whenever any side fell back -
    visible at the point the run reports success, not just buried mid-run. New opt-in
    `--require-bounce-manifests` CLI flag makes a missing manifest a hard refusal instead of a
    fallback, for a caller that wants the strict guarantee. Deliberately NOT made the default -
    a manifest's value depends entirely on being recorded AT BOUNCE TIME, which code cannot
    enforce regardless of whether the flag is mandatory, so forcing it changes nothing about the
    real protection while breaking existing callers who haven't adopted the new step yet.
  - **MAJOR - the round-1/round-2 stale-temp-dir cleanup was STILL not concurrency-safe**: an
    mtime-based age check doesn't prove inactivity, so a genuinely slow run past the 1-hour
    threshold could still have its live temp directory deleted by another invocation's startup
    cleanup. Adopted Codex's own "safest" fix: REMOVED automatic cross-run cleanup entirely
    rather than build a real ownership/lock mechanism for what was only ever a hygiene feature -
    the opaque per-clip filenames (kept) are what actually prevent a leaked file from disclosing
    a side, regardless of how long it sits on disk; a leftover directory is left to the OS's own
    temp housekeeping. Net simpler than round 1's version, not more complex.
  - **MINOR - a TOCTOU gap**: the WAV is hashed once near the top of the run but re-read fresh
    for each transition's actual slice later - a file replaced on disk in between would be
    verified against different bytes than were sliced. Deliberately NOT fixed: closing it for
    real means either re-hashing a whole WAV before every transition (real, avoidable cost for a
    long mix) or snapshotting it (real disk/time cost) against a threat model - another process
    replacing a file mid-run on Sam's own machine with no adversary - this project doesn't treat
    as live anywhere else; a corrupted run from this is also not silent (an audibly/visibly
    broken clip, not a quiet wrong verdict). Documented explicitly in `_verify_bounce_manifest`'s
    docstring as an accepted, reasoned gap rather than silently dropped - same convention as the
    round-1 duration-check disposition.
  Fixed both MAJORs, documented the MINOR, added 5 more tests (36 total in the extract-excerpts
  file), proved-the-test on the `--require-bounce-manifests` refusal path, and re-verified
  against the real project a FOURTH time: a no-manifest run correctly prints the new
  "NOT FULLY AUDITABLE" line naming all three sides; recording real manifests for all three
  (via `record_bounce_manifest.py`, for real, against the real ALS/report/WAV files) then running
  with `--require-bounce-manifests` set completes silently with `binding: {"A": "strong", "B":
  "strong", "C": "strong"}` in the real results file; removing one real manifest and re-running
  strict correctly refuses naming that side. Full suite 708/6/0 -> 710/6/0.
  Files changed: `Source/extract_transition_excerpts.py`, `Tests/test_extract_transition_excerpts.py`.

  **CODEX ROUND 4 (`-Effort high`, real staged files): CONVERGED - 0 FATAL, 0 MAJOR - 2026-09-14.**
  First round with no severe finding at all. "The binding-tier result record, final weak-binding
  summary, and opt-in strict mode close round 3's auditability MAJOR. Keeping strict mode opt-in
  is reasonable... Removing automatic temp cleanup is also the right safety trade-off... Round-4
  verdict: converged for the A4 excerpt-extraction review." One MINOR, explicitly flagged as
  "non-blocking hardening," not required for convergence: the round-3 TOCTOU disposition claimed
  a mid-run WAV swap would be "audibly/visibly broken" and therefore self-evident - correctly
  called out as overstated (a plausible-but-provenance-invalid substitute would not necessarily
  sound broken). Fixed anyway, as a bonus hardening pass rather than a 5th review round for
  something Codex itself said didn't gate closing the item: new `_wav_identity`
  (mtime, size - cheap, non-cryptographic) captured at manifest-verification time for every
  strongly-bound side, rechecked immediately before each transition's actual slice
  (`_check_wav_identity_unchanged`), aborting that transition if the file changed in between.
  Narrows the window from "whole run" to "between verification and this one slice," at
  negligible cost, without repeated full-file hashing. 3 new tests, proved-the-test (disabled the
  comparison, confirmed the replaced-file test failed to raise, restored), full real-project
  re-verification with `--require-bounce-manifests` set against all three real sides (binding
  `{"A": "strong", "B": "strong", "C": "strong"}`, 8/8 transitions sealed). Full suite
  710/6/0 -> 713/6/0.
  **A4 IS NOW FULLY DONE: both halves fixed, tested, verified against the real project, and
  independently reviewed to convergence.** Reconciliation half: 1 Codex round, "NO MATERIAL
  OBJECTIONS." Excerpt-extraction half: 4 Codex rounds (1 FATAL+3 MAJOR+2 MINOR round 1 -> 1
  FATAL+3 MAJOR+1 MINOR round 2 -> 0 FATAL+2 MAJOR+1 MINOR round 3 -> 0 FATAL+0 MAJOR+1 MINOR
  round 4, converged) - five real, substantive findings that survived to reach Claude's own
  verification bar were caught ONLY because independent review kept running past the point each
  round's fix "looked done"; worth keeping as the concrete case for why this project's
  Codex-review-every-substantial-fix rule exists.
  Owner: Claude. Author: Claude. Peer review: SOUND - Codex, reconciliation half (1 round,
  `-Effort high`, real staged files, "NO MATERIAL OBJECTIONS"); excerpt-extraction half SOUND -
  Codex, 4 rounds, `-Effort high`, real staged files throughout, round 4 "converged," 0 FATAL/
  MAJOR outstanding. Same lightweight-convention caveat as A1/A3
  applies (see the validator note under THE COUNT) - no
  formal `Staging/` receipt, `validate_burn_list.py --strict` checks 6/11 will show the same two
  expected FAILs on this line.

- [x] **Run the actual sealed blind listen and get Sam's verdict** (A5) - blocked on A1-A4
  above. Pre-registered kill criteria already exist (Plan V2): B/C must win >=5 of 7 differing
  transitions, lose <=1, no beat/grid errors, no audible clash, no masked protected dropout, no
  unjustified extended-lane authorisation. `interim_v1` stays production default regardless of
  this round's result - promotion needs Sam's listening verdict, same as every prior round.
  Read C1/C2 below before treating any result here as a verdict on swap PLACEMENT - it isn't one
  this round.

  **DONE, 2026-09-14.** Sealed via `seal_listening_test.py` (whole-mix, seed 20260914) and
  `extract_transition_excerpts.py` (all 8 per-transition excerpts, `--require-bounce-manifests`
  set - every side strongly bound). Sam listened blind, twin control confirmed on all 8
  transitions (the sharpest case: T6, where Sam correctly identified the twin while listening
  and it still won). Verdict, tracked separately per policy since B and C are different things:
  `sam_v1` (B) hits an explicit kill condition (2 losses of 8, exceeding the "loses <=1" limit).
  `sam_v1`+introloop (C) does not trigger the hard kill (1 loss, at the boundary) but falls short
  of the win bar (4/8, not ~6/8) - validated, not promoted, same outcome class as Result 01.
  `interim_v1` stays production default. Per-transition table, the twin-control evidence, and the
  C1/C2 caveat (this round's `sam_v1` never consulted `pair_history.jsonl` or content-aware
  automation style - "park or revise" means revise via C1/C2, not that swap placement itself is
  disproven) all written up in
  `Documentation/Mix Patterns Library/Heldout Replay Result 02.md`.
  Evidence: `Documentation/Mix Patterns Library/Heldout Replay Result 02.md`;
  `Test Project/10.09.26 Tech House Heldout/Output/AB/Blind Test/_sealed/MAPPING.json`;
  `Test Project/10.09.26 Tech House Heldout/Output/AB/Blind Test/Transitions/T01`-`T08`
  (each `_sealed/MAPPING.json`).
  Owner: Sam (the listen itself). Peer review: n/a - this is Sam's verdict, not a build (see the
  validator note under THE COUNT).

- [x] **Every AB-comparison ALS bakes in a machine-specific absolute path AND a relative path one
  folder-level too shallow, so opening one on a different machine reliably shows offline samples**
  (A6) - found live, 2026-09-14, while Sam tried to bounce Side A of the Tech House Heldout
  comparison and Ableton reported track 2 (Yellody) and the last track (Jewel Kid) as offline;
  after a partial relink, Freejak and Jewel Kid specifically were still unresolved.
  ROOT CAUSE, verified directly against the ALS XML (`Output/AB/A/Mix A.als`, and confirmed
  identical in `Output/AB/B/Mix B.als` and `Output/AB/C/Mix C.als`): every one of the 9 audio
  tracks' `SampleRef/FileRef` carries `<Path Value="G:/Wired Masters Dropbox/...">` - this
  project was analysed/arranged on the Home PC, where `G:` is Dropbox (per CLAUDE.md's machine
  table); opened on the Studio PC, `G:` is a totally unrelated backup drive, so the absolute path
  cannot resolve on this machine AT ALL, for any track. Separately, `RelativePath` is
  `../Audio/<file>.wav` - one level up from `Output/AB/<side>/Mix <side>.als` lands in
  `Output/AB/Audio/` (does not exist), not the real `Output/Audio` two levels up. Both stored
  paths are wrong on this machine, for every track, by construction, not by accident. Ableton
  appears to silently self-heal most tracks anyway via its own cross-project "seen this file
  before" cache (Sam has almost certainly opened these particular masters - his own day-to-day
  work - in Ableton on the Studio PC before), which is why only a FEW tracks visibly break rather
  than all nine - the ones that break are simply the ones Ableton has no prior memory of on this
  machine, not the ones with a structurally different problem.
  **RULED OUT, verified directly, not assumed** (Sam asked "can you copy those files into the
  Audio folder"): Freejak's and Jewel Kid's WAVs are NOT missing, NOT misnamed, NOT corrupt, and
  NOT Dropbox cloud-only placeholders. Checked all four ways: (1) exact byte-for-byte filename
  match between the ALS's stored `RelativePath` and the actual files on disk in
  `Test Project/10.09.26 Tech House Heldout/Audio/` (both match exactly, including Freejak's
  double space before "(Extended"). (2) Header+tail read of both files completed in ~1-2ms with a
  valid RIFF header - instant, not the multi-second stall a Dropbox online-only placeholder would
  cause on first access. (3) `soundfile.info()` opened both fully: Freejak 309.5s, Jewel Kid
  317.4s, both 44.1kHz/24-bit stereo - complete, uncorrupted audio, not a truncated or partial
  file. (4) `Get-Item` on Windows shows both at their full real byte size (81.9MB / 84.0MB) with
  no `Offline` attribute. A copy operation would move byte-identical data on top of itself and
  fix nothing - the problem is not file availability, it is what the ALS tells Ableton to look
  for. Likely real cause on Ableton's side (not verified - can't see Sam's screen): a "Locate"
  fix applied to one clip does not automatically propagate to every other `AudioClip` referencing
  the same sample on a multi-clip track (Freejak has 8 clips, Jewel Kid has 9, each carrying its
  own `SampleRef/FileRef`) - Ableton's File > Manage Files > "missing/offline" workflow, which
  relinks every reference to a given sample at once, is the more likely fix than clip-by-clip
  Locate.
  **NOT YET FIXED AT THE SOURCE.** Whatever step generates these `AudioClip` FileRefs (warp-marker
  writing in `warping.py`, or wherever the ALS template gets its per-clip `SampleRef` populated)
  should write a `RelativePath` correct for the ACTUAL save depth (`Output/AB/<side>/` is two
  levels below the project root, not one), and should not bake in a machine-specific drive letter
  as the sole absolute fallback - or at minimum this needs a documented "Collect All and Save" /
  relink step folded into the `/mix` AB-comparison workflow before handing an ALS to Sam on a
  different machine than it was built on. Will recur on every future cross-machine AB-comparison
  open until fixed.
  Evidence: `Test Project/10.09.26 Tech House Heldout/Output/AB/{A,B,C}/Mix {A,B,C}.als` (all
  three checked directly, identical pattern); `Test Project/10.09.26 Tech House Heldout/Audio/`
  (files verified present, correctly named, complete, not corrupt).

  **ROOT CAUSE TRACED FURTHER, 2026-09-15 - still not built, needs a peer-reviewed plan.**
  Confirmed directly where the bad path actually propagates from: `propose_arrangement.py`
  itself never touches `SampleRef`/`RelativePath`/`AudioClip` XML at all (grepped, zero hits) -
  clip duplication happens in `Source/apply_loops.py`'s `clone_clip()` (line 304), which takes
  an existing `<AudioClip>` block's raw TEXT as a template and stamps out repositioned copies
  from it, opaque-text-style, matching this whole codebase's established line/regex ALS-editing
  convention (the same style `extract_sections_als.py` uses, per E3 above). It never inspects or
  rewrites `SampleRef`/`RelativePath` specifically - so the wrong relative-path DEPTH baked into
  whatever ALS originally seeded these clips (most likely wherever Ableton itself first wrote
  the master/sections ALS) propagates PASSIVELY through every downstream clone, all the way to
  the AB-comparison's `Output/AB/<side>/Mix <side>.als` - two levels deeper than wherever it was
  computed for. There is no single wrong line to fix; the actual gap is that NOTHING anywhere in
  the pipeline recomputes `RelativePath` for the file's REAL final save depth. Most plausible fix
  shape (not attempted): a new post-processing pass in `apply_automation.py` (the last script to
  write the final ALS) that, right before save, locates the real Audio/ folder the same way
  `_find_audio_dir()` already does (2026-09-12 fix, walks upward through ancestors rather than
  assuming a fixed depth) and rewrites every clip's `RelativePath` to match. This would touch the
  FileRef of every clip in EVERY mix's final output ALS (not just AB comparisons - the bug's
  visible effect is AB-comparison-specific only because that's the one output depth that's
  currently wrong; a normal single-mix build happens to sit at the same depth the original
  master ALS was computed for, by luck not by design), so a wrong rewrite could make references
  WORSE, not better. Deserves its own peer-reviewed plan before code, same bar as D1/D6/D8 above
  - not attempted blind this session; no peer was free when this was traced (MiniMax and a
  Claude subagent were both mid-review on C5/C2).

  **BUILT + TESTED + VERIFIED, 2026-09-15.** Plan reviewed by MiniMax first (no blockers, 6
  refinements requested), all 6 folded in before any code was written. New
  `_fix_sample_ref_paths` + `_resolve_audio_file` in `apply_automation.py`, called once right
  before the final `compress_als` write. Per-clip fail-safe (a `FileRef` whose filename can't be
  resolved under the real `Audio/` folder - factory content, an `.amxd` device, an empty unused
  slot - is left completely untouched, never guessed at); regex scoped to one `<FileRef>` block
  at a time via `re.finditer`-style matching, never a doc-wide greedy capture. 11 new tests
  (`Tests/test_sample_ref_paths.py` - zero direct coverage of this code path before). **Two real
  bugs caught during implementation, neither anticipated in the plan review**: (1) a
  case-insensitivity trap - `Path.exists()` resolves case-INSENSITIVELY on Windows/macOS
  default, so a differently-cased basename passed the naive existence check while silently
  keeping the WRONG case in the written path (caught by my own new test, which failed against
  the first-draft code); fixed with `_resolve_audio_file`, a proper directory scan that always
  returns the file's real on-disk name. (2) an XML-escaping bug found by the real-corpus
  dry-run itself - 2 of the 9 real tracks in the staged project ("There's A Party", "Deep House
  Pumpin'") have `&apos;`-escaped apostrophes in their stored paths; comparing the raw escaped
  text against the real filesystem name never matched, silently leaving those two tracks unfixed
  via the fail-safe. Fixed with `html.unescape` before the filesystem lookup (matching this
  file's own established convention for track names) and a re-escape on the way back out.
  **Real-corpus validated, not just synthetic**: ran the fix against the actual staged
  `Test Project/10.09.26 Tech House Heldout/Output/AB/A/Mix A.als` - after both bug fixes, all 88
  real audio `FileRef` blocks resolve correctly (was 65/88 before the escaping fix), confirmed
  idempotent, and confirmed the fixed output passes this project's own real `validate_als.py`
  gate end-to-end (wrote the fixed content through the actual `compress_als` function, not just
  inspected the text). The 3 tracks Sam actually reported broken (Yellody, Freejak, Jewel Kid)
  are all in the fixed set; 6 more tracks that "worked" only via Ableton's undocumented
  cross-project cache got corrected too, as a bonus.
  Full suite 780/0/6 -> 791/0/6.
  Files changed: `Source/apply_automation.py`, `Tests/test_sample_ref_paths.py` (new).
  **Peer-reviewed twice - MiniMax (plan, then code).** Code review: "No material objections.
  Ship as-is." Verified every one of its own 6 plan-stage requests actually landed in the code
  (not just claimed), independently re-traced the regex-scoping discipline and the exact-match-
  precedence guarantee in `_resolve_audio_file`, confirmed the two real bugs' regression tests
  genuinely exercise what they claim. One optional defense-in-depth suggestion (also escape `&`,
  not just `'`) explicitly recommended AGAINST by the reviewer itself (no `&` in Sam's real
  credits corpus; the existing `validate_als.py` gate would hard-fail rather than silently
  corrupt if one ever appeared) - left as-is per that recommendation.
  Owner: Claude. Author: Claude. Peer review: SOUND - MiniMax (plan + code, both rounds), no
  findings required a further change.

## B - Sam's decisions (nothing here should be built without his ruling)

- [x] **Four pre-built features are sitting behind disabled flags with real evidence already
  gathered, and nobody has ruled on any of them** (B1): `LOOP_SELF_SIMILARITY_TIERA`'s
  AND-vs-replacement semantics; `width_cues`; `soft_intro_outro`'s R2/R4 section-detection soft
  rules; `BASS_RESIDUAL_ENABLED` (two full mixes of zero-firings evidence now exist - House 10
  A/B - and the flag still defaults off). Astra separately flagged the same class of thing from a
  different angle: Tier-A loop similarity and width-based section cues exist behind switches
  with documented examples, and simply enabling everything would also remove some existing
  correct rejections - this needs a real replay before promotion, not a flip.

  **CORRECTED, 2026-09-14 (found while answering Sam's own "what is B1?" directly):** the
  original evidence line for the R2/R4 item pointed at the WRONG code and carried the WRONG
  number. `Source/apply_automation.py:804-807`'s "Rule 4: lower sneak" is NOT behind a flag at
  all - it always runs (`overlap_len <= 80 beats -> plan.low_sneak = True`); its "7/9 false
  positives" figure is a 2026-05-21 historical record against an OLD trigger
  (`clips>=3 OR len<=32`) already superseded by today's code, not live evidence against a
  disabled feature. The REAL flag-gated R2/R4 is `Source/stem_detector.py`'s `soft_intro_outro`
  parameter (default `False`) - Sam's SECTION-DETECTION soft rules: R2 (kick-less head = intro),
  R4 (kick-in head + first pre-drop break = intro extends to that break). Its real evidence is
  materially DIFFERENT and more positive than the wrong citation implied: a 20-track corpus sweep
  with the flag ON showed 2/20 tracks improve (Pushin' From The Walls, Reachin), 0 spurious - "R4
  proven, R2 unproven" per `Documentation/TOOLBOX.md`'s own note.
  **RESOLVED 2/4, 2026-09-14 (Sam: "let's get them turned on") - investigated all four properly
  before touching anything, and two more citation errors turned up in this same item on top of
  the R2/R4 one already fixed above.** The "2/20-improve/0-spurious" figure's real source is
  `Documentation/AI_CONTEXT.md:416`, not `TOOLBOX.md:161` (which carries a related but different,
  figure-free sentence). The "two full mixes of zero-firings evidence... House 10 A/B" claim for
  `BASS_RESIDUAL_ENABLED` does not hold up either - only ONE documented zero-firing run exists
  (`.github/ai-activity-log.md:263`, House 10, 2026-09-02: 0 firings, 1 guard-refusal); the only
  OTHER documented `BASS_RESIDUAL=1` run (`.github/ai-activity-log.md:256`, 2026-09-01) actually
  DID fire. The citation this claim traces to (`AI_CONTEXT.md:294,299`) is about an unrelated
  tempo-map render-gate merge, not bass residual at all.

  **`width_cues`: ENABLED.** `Source/stem_detector.py:769`'s default flipped `False -> True`. Real
  evidence held up: 20-track sweep, 14 cues, 11 new boundaries, every one independently backed by
  a stem/band exit at flat RMS, 0 spurious, 0 existing boundaries moved (the 719be92 commit
  message itself, the real primary source, has the full numbers - AI_CONTEXT.md/TOOLBOX.md only
  paraphrase it). Independently corroborated by a SEPARATE detector entirely - `allin1`
  (spectrogram transformer) found the same Revoloution bar-147 boundary every energy detector
  missed, confirmed in `Documentation/Reviews/2026-08-20 allin1 Second-Opinion Evaluation.md`
  BEFORE `width_cues` was even built. Verified before flipping, not just trusted: `width_cues=True`
  requires real cached Tier-A stereo-width envelope arrays (`ensure_tier_a_arrays`) - ran it
  directly against a real Tech House Heldout track (not a synthetic fixture): 2.56s to compute
  fresh, 0.02s on a cache hit - genuinely lightweight, no real production cost. This dependency
  broke every test using a fake/synthetic wav fixture (they don't have real audio to compute
  envelopes from) - fixed by explicitly pinning `width_cues=False` in the 3 test files that were
  testing something else entirely (kick-model integration, R2/R3/R4) and were never meant to
  exercise this path; the one test that pinned the OLD default is rewritten to pin the new one.
  Full suite 724/6/0 -> 725/6/0 (net: one old pinning test replaced by two).

  **`soft_intro_outro`: ENABLED.** Same file/line, same flip. R2 and R4 share one flag - no way to
  split them without new code, so "enable R4 (proven), leave R2 (unproven) off" isn't available
  without building that split first. Enabled the COMBINED behaviour exactly as it was actually
  tested: the real 20-track sweep tested R2+R3+R4 together and found 0 spurious - real evidence of
  SAFETY for the combination, even though R2's own individual contribution specifically was never
  isolated. Verified clean against the real test suite (11/11 in `test_section_soft_rules.py`).

  **`LOOP_SELF_SIMILARITY_TIERA`: ENABLED, 2026-09-15 - AND-semantics rebuild built, tested, and
  re-verified against real corpus data before the flip, per Sam's explicit direction.**
  `evaluate_loop_quality` now computes TWO separate self-similarity terms instead of one blended
  replacement: `self_similarity` (the base 5-key score) is ALWAYS computed, identical to the
  flag-off call every time; `self_similarity_tiera` (the tiera-augmented 10-key score) is only
  computed when the flag is on, and only gates the check when it is independently measurable (the
  existing "cannot fail a check you could not measure" rule for the mono-input wart). The
  `self_similarity` check now fails if EITHER term is below `LOOP_MIN_SELF_SIMILARITY` - true AND
  semantics, not a replacement. Because the base term is unconditionally computed and gates on its
  own regardless of the flag, turning the flag on can only ever ADD a self_similarity failure the
  base term would have missed; it is now structurally impossible for it to REMOVE one the base
  term already caught - the exact class of bug the old blended-score design had (791 evidenced new
  catches, but also 404 evidence-carrying UN-catches per the original 719be92 corpus replay).
  9 tests rewritten/added in `Tests/test_tiera_loop_similarity.py` (23 total), including two that
  pin the AND-gate logic directly via monkeypatch (a base failure survives a passing tiera term;
  a tiera failure adds a catch a passing base term alone would miss) - proved-the-test: 7 of the 9
  changed/new tests fail against the reverted pre-rebuild code (verified by stashing
  `align_engine.py` and re-running against the new test file).
  **Re-verified against real corpus data, not the stale 15,268-window citation** (that exact
  directory no longer exists in a runnable state - `Test Project/Stephanes Playlist/.../_Stem
  Analysis` now holds 61 `__stemenv.npz` caches but zero paired `SECTIONS_STEM_*.json`, a real
  instance of this project's own standing churn warning, not something worth blocking on).
  `Tools/tiera_loop_replay.py` rewritten to pool multiple corpus directories and derive the actual
  AND-gate verdict (previously it only compared the raw base score against the raw blended-score
  replacement) - run against every `_Stem Analysis` folder on this machine that currently has
  matching `__stemenv.npz` + `SECTIONS_STEM_*.json` pairs (verified exact 1:1 filename pairing in
  all 11 dirs directly, not just equal counts): **11 project directories, 119 track-entries (90
  UNIQUE track names - 24 names recur across 2-3 directories, e.g. the same track analysed for
  both a 23.06.26 and a 24.06.26 test project; NOT fully independent-track coverage, and the tool
  now prints this breakdown itself rather than leaving it implicit), 80,815 real windows.** Result:
  **831 AND-gate flips, ALL pass→reject** (816 evidence-backed CORRECT, 15 SPURIOUS - the same
  acceptable class as the original build's 15 sub-threshold cases, several on the exact
  Vente/Revoloution tracks the feature was built to catch); **0 reject→pass** - both asserted
  structurally impossible by the replay tool itself (it raises if the AND-gate ever produces one)
  and empirically confirmed across all 80,815 windows. A same-corpus run of the ORIGINAL
  blended-score design (kept in the tool purely as a historical comparison, not what production
  computes any more) would have produced 500 reject→pass un-catches on this same real data - the
  exact regression class the AND-gate rebuild makes impossible by construction. Full suite re-run
  after the flip; see the dated Current State entry for the pass/fail count.
  **Codex review (`room_peer_review.ps1 -Peer codex`, `-Effort high`, real staged files:
  `align_engine.py`, `test_tiera_loop_similarity.py`, `tiera_loop_replay.py`): "NO MATERIAL
  OBJECTIONS. The production AND-gate is structurally monotonic."** Confirmed independently: the
  base term is always computed and independently retained, the tiera term can only ADD a failure,
  `None` correctly skips only the tiera half never the base half, cache keys are feature-set-
  specific (no cross-contamination), and the reject→pass assertion is unreachable by construction.
  Two real, adopted findings, both fixed same-session: (1) stale "Flag defaults OFF" prose in both
  `align_engine.py` and the test file's docstring, left over from before the flip - rewritten to
  describe the current (ON) default and moved the OFF-era history into its own paragraph. (2) the
  replay's own evidence characterisation was imprecise - "119 tracks" implied full independent-
  track coverage when 29 of those entries are re-analyses of 24 already-counted names, and the
  tool didn't enforce or surface a missing section-map pairing (it degrades silently to `sections
  =[]`, which would make a genuinely CORRECT flip read as SPURIOUS for lack of that one evidence
  source - verified this run had zero such gaps, but the tool didn't say so on its own). Both
  fixed: the tool now prints unique-vs-total track counts, names every duplicated track, and warns
  by name on any track missing its section map; re-ran against the real corpus a second time with
  the hardened tool - identical numbers (831/816/15/0/500), confirming the fixes were reporting-
  only and did not mask or change any underlying result. One point Codex raised is NOT a code
  defect, just an imprecise brief: the review bundle contained only 9 test functions from
  `test_tiera_loop_similarity.py` while the brief said "23 tests total" - that total was always
  the combined count of that file (9) plus `test_loop_quality_gate.py` (14) run together, which
  Codex could not see since only three specific files were staged for the review.

  **`BASS_RESIDUAL_ENABLED`: NOT ENABLED - a real, still-open Codex finding, not neglect.**
  `Source/bass_residual.py`'s own module docstring documents an explicit, unresolved FATAL-severity
  finding from a 2026-09-01 Codex review: the gate this relies on (`BAND_P95_DB`) was measured on
  SUMMED-BOUNCE predictions but is being used to gate a DIFFERENCE of two per-track shares - a
  materially different estimator the gate was never certified for. The code's own comment states
  plainly this stays default OFF until held-out solo-render calibration exists for that specific
  quantity. That calibration has not been built. Flipping this without addressing the actual named
  blocker would mean silently overriding a real, reasoned safety decision, not correcting an
  oversight.

  **Sam's call needed on this last one (bass residual)** - real technical work (held-out
  solo-render calibration for the specific per-track-share-difference estimator) is required
  before it can safely be turned on, not just a flip. Happy to scope it as its own burn list item
  if you want it picked up. `LOOP_SELF_SIMILARITY_TIERA` above is no longer in this category - it
  needed the same kind of real technical work (an AND-semantics rebuild + re-replay), and that
  work is now done.

  **DECIDED, 2026-09-15 (Sam, asked directly): leave BASS_RESIDUAL_ENABLED off, park it.** No
  calibration work commissioned. This closes out B1 - all four sub-flags now have a final,
  explicit disposition (three enabled with real evidence + re-verification, one deliberately
  parked with the reason on record).
  Evidence: `Source/align_engine.py:58-69,364-388,499-503` (LOOP_SELF_SIMILARITY_TIERA, updated
  2026-09-15 - AND-semantics rebuild + fresh 80,815-window replay, superseding the 2026-09-14
  "not yet built" note); `Source/stem_detector.py:769` (width_cues/soft_intro_outro flips);
  `Documentation/AI_CONTEXT.md:416` (soft_intro_outro's real 2/20-improve/0-spurious source,
  corrected); `Source/bass_residual.py:1-70`, `.github/ai-activity-log.md:256,263` (BASS_RESIDUAL,
  corrected); `Tools/tiera_loop_replay.py` (rewritten for AND-gate verdicts, multi-directory
  pooling); `Tests/test_tiera_loop_similarity.py` (rewritten, 23 tests).
  Owner: Sam. Author: Claude (width_cues, soft_intro_outro, LOOP_SELF_SIMILARITY_TIERA).
  Peer review: NONE - not yet reviewed overall (mixed per sub-flag: width_cues/soft_intro_outro
  NONE, not yet reviewed; LOOP_SELF_SIMILARITY_TIERA SOUND - Codex, 1 round, `-Effort high`, real
  staged files, "NO MATERIAL OBJECTIONS", 2 real findings both adopted and fixed same-session, see
  above; BASS_RESIDUAL_ENABLED remains Sam's open call, not built - n/a).

- [x] **An untracked Ableton 12.4.3 template is being picked nondeterministically by mtime,
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

  **DECIDED + DONE, 2026-09-15 (Sam, asked directly): commit the 12.4.3 file as canonical.**
  Verified all four `.als` files under `Templates/` directly before touching anything (track
  count + `Creator=` Ableton version, read from each file's own decompressed XML): the tracked
  root `Templates/DJ Mix Template 2026.als` was 12 tracks/Ableton 12.3 (stale, 2026-05-14); the
  untracked `Templates/DJ Mix Template 2026-2 Project/DJ Mix Template 2026.als` was 12
  tracks/Ableton 12.4.3 (2026-08-13, newer mtime - confirmed this is the one `_find_template`'s
  tie-break actually resolves to today). Copied the 2026-2 Project file's content over the
  tracked root file - `_find_template`'s rglob+mtime-tie-break mechanism is UNCHANGED (Sam didn't
  ask for the resolution logic itself to change), but the canonical, git-tracked copy on disk is
  now the real 12.4.3 template, so a fresh clone gets the actual currently-shipping template
  without needing the untracked project folder present. Full suite re-run clean after the swap:
  780 passed, 6 skipped (unaffected - zero test references `_find_template` at all, confirmed by
  grep, so this was a pure content fix with no test surface to update).
  Files changed: `Templates/DJ Mix Template 2026.als` (content replaced).
  Owner: Claude. Peer review: NONE - not yet reviewed (content swap only, no logic changed;
  judged proportionate to skip a peer round - flag if Sam wants one anyway).

- [x] **Whether to scope the deeper swap-first redesign (item "c") NOW, in parallel with the
  blind listen, rather than waiting for it to fail first** (B3) - the redesign's own landing
  plan queued item (c) "only if a real case surfaces that (a) alone can't handle." Fable's
  reading, which this list agrees with: that case already surfaced BEFORE (a) was even built -
  Sam's own T2 hand-correction on this exact project (28 beats, `bass_swap_moved`) is not
  reproduced by any of today's three comparison policies, because none of them touch swap
  placement at all (see C1/C2). Waiting for the listen to "fail" first may never happen, because
  the listen structurally cannot test this. Sam's call on sequencing, not a build decision.

  **DECIDED, 2026-09-14: yes, scope now.** Investigated the real call path (Explore agent, cited
  file:line throughout) before proposing a landing order - the swap point IS already chosen
  before loop/cut geometry (temporally), but overlap/progress admissibility caps gate candidate
  ADMISSION inside the SAME search loop as musical-merit scoring, before scoring ever runs -a
  candidate that would need loop extension to become geometrically valid is discarded before
  it's ever compared, never revisited. The primary search pass also isn't a full ranked search:
  it takes the FIRST anchor (earliest drop) yielding any valid candidate; only the rescue pass
  (fired when the primary finds nothing) ranks globally. **Real finding, unprompted:** burn list
  C1 (below) and item (c) are the SAME decision point, not two jobs - `find_similar_pairs`
  doesn't even READ the swap-delta fields when matching similarity, so building C1 standalone
  would risk the wrong consumption interface. C1 folded into C7 below; its own line item is
  superseded.

  Landing order, smallest/safest first:
  - **C5 - fix a real latent bug**: the overlap/loop-size limits gating candidate admissibility
    were MODULE-LEVEL constants frozen from `INTERIM_V1` at import time, never actually reading
    the `policy` parameter threaded through the call chain - invisible today only because
    `SAM_V1` happens to share the same limits; any future policy with different caps would have
    this silently ignore them. **BUILT + TESTED + VERIFIED, 2026-09-14** (see below).
  - **C2 (folded in here as the first real signal-aware change)**: automation style stops
    ignoring whether the outgoing has real content to fade across. **BUILT + TESTED + VERIFIED,
    2026-09-14** (see below).
  - **C6 - RESCOPED then BUILT + TESTED, 2026-09-15.** The original "retire first-admissible-
    anchor-wins, rank globally" proposal was reviewed by Codex across 4 plan rounds
    (`Documentation/Plans/swap-first-redesign/c6-c7-c8-plan.md`) and materially changed by a
    direct empirical investigation of the T2 trigger case (Freejak->HARTY): the original proposal
    turned out to be a COMPLETE NO-OP for T2 (only one incoming anchor is ever admissible for that
    pair, so nothing is missed by returning early on it) and was DROPPED, not built - Codex's own
    round-2 review proved the proposed anti-gaming rule could never fire in the primary pass at
    all (`drop_payoff` requires `incoming_anchor < first_drop_bar`, which is structurally false for
    every anchor in the very list being searched, since `first_drop_bar` IS that list's own
    minimum). What actually blocked T2 was a DIFFERENT mechanism entirely: ranking among OUTGOING
    anchor candidates within the ONE admissible incoming anchor. Built instead: a new
    `bass_out_payoff` rank-tuple tier in `_search_anchors` (`Source/align_engine.py`) - ranks
    higher than `weighted_score` but below `drop_payoff`, rewarding a candidate that lands exactly
    on the outgoing track's own `bass_out_bar` (when it's not `bass_out_is_end`) - directly
    implementing this file's own documented arrangement model for the one decision point that
    didn't yet honour it. Verified the EXISTING `CUE_CONFIG.emit_bass_out` weight-boost mechanism
    first (Codex's own suggestion) - confirmed empirically it changes nothing for T2, and worked
    out precisely why: it boosts a cue's weight, but `weighted_score` sums every coincidental
    cue-pair across the WHOLE overlap window, and the boosted cue sits inside BOTH competing
    candidates' windows, so the boost cancels out - proving a discrete tier (not any cue-weight
    value) was the only fix that could work. Fixes T2's own real geometry: `handoff_bar_out`
    164->148, `overlap_bars` 17->33, matching the structurally correct point (the T2 EXACT swap
    beat, bar 157, is still unreachable - it was never a registered cue at all, out of scope for
    this or any of C6/C7/C8 as currently conceivable, stated plainly in the plan rather than
    silently expected). 5 new tests (`Tests/test_bass_out_payoff.py`), 2 of 5 proved to fail
    against the pre-fix code (git-stashed and re-run). 380-pair baseline: 18 pairs moved, EVERY ONE
    independently verified (not assumed) to converge on that pair's own outgoing track's real
    `bass_out_bar`, three outgoing tracks account for all 18 - inspected before refreshing, per
    this project's own established discipline. Full suite 741/0/6.
    **Codex code review (real staged files, `-Effort high`): "NO MATERIAL OBJECTIONS."** Confirmed
    independently: implementation matches the agreed contract exactly (null guard, `not
    bass_out_is_end`, exact `round(float(...))` match, tuple order); only evaluates already-
    admissible candidates, cannot create one; `round()` is consistent with `_mix_cues` (no bar-
    normalisation off-by-one); `rank_all=True` pooling changes selection only, not admissibility;
    the 2/5 proved-the-test failures are credible. One MINOR, adopted: the original
    `test_drop_payoff_still_dominates_bass_out_payoff` only inspected one candidate's tuple slots,
    which cannot actually prove one tier dominates another - Codex noted "the current code is
    correct" (not a functional bug) but asked for a real two-candidate competition if the test's
    own dominance claim was meant literally. Rebuilt with a genuine `rank_all=True` pooling
    scenario (two incoming anchors, two outgoing cues engineered so bar 36's bass_out-tier
    candidate is reachable ONLY from one anchor and bar 44's drop-payoff-tier candidate ONLY from
    the other - confirmed by direct admissibility-window arithmetic before writing the fixture,
    not guessed) - pooling now proves the drop_payoff candidate actually wins the competition, not
    just that its own tuple slot holds the right value. Re-verified proved-the-test (2 of 5 still
    fail against the stashed pre-fix code). Full suite 741/0/6 -> unchanged count (test rewritten,
    not added).
    Files changed: `Source/align_engine.py`, `Tests/test_bass_out_payoff.py` (new).
  - **C7 (was C1), Step 0 BUILT + TESTED, 2026-09-15 - Step 1 (shadow mode) not yet built.** New
    `Source/canonicalize_pair_history.py`: derives the swap-beat delta from the two beat fields
    directly (never the raw `bass_swap_delta_beats` field, which can be absent or stale on BOTH
    sides of a real conflict - the exact Black Book pair 4 case Codex's round-3 review found),
    groups by (project, pair_index), and FAILS CLOSED on any disagreement (verdict OR derived
    delta beyond a 4-beat/1-bar tolerance) - a conflicting group is excluded from the canonical
    dataset entirely until a human-authored resolution record exists for it (schema: `resolved_
    verdict`, `resolved_delta_beats`, `resolved_by`, `date`, `reason`), never auto-resolved by
    "keep the latest" (the real duplicates share the same 2026-05-21 timestamp - there's no
    recency signal to even tiebreak on). Run against the real corpus: 34 raw records reduce to
    exactly 25 unique (project, pair_index) observations (matching the figure the plan review
    independently derived by hand) - 21 canonical, 4 real conflicts, all in Black Book x Defected
    V2 (pairs 3/4/5/7 - two different disagreement shapes: pairs 3/4 disagree on the derived
    delta itself, pairs 5/7 have IDENTICAL zero delta but disagree on VERDICT, because the
    coarser `auto_diff` logging source can't see a correction that doesn't move the swap beat at
    all - a two-stage-bass automation change or an arrangement extension). 11 new tests
    (`Tests/test_canonicalize_pair_history.py`), including a pinned real-corpus regression (25
    unique / 4 conflicts, the exact named Black Book pairs). Step 1 (shadow-mode reporting inside
    `find_similar_pairs`) is separate, unbuilt, gets its own review round per the plan when built.
    **Peer-reviewed: Codex rounds 1-2 (real staged files, `-Effort high`) found real findings -
    duplicate resolution records silently using the last line instead of failing closed (fixed:
    a duplicate `(project, pair_index)` key now raises outright), numeric inputs not validated as
    finite (fixed: `resolved_delta_beats`/beat fields/`tolerance_beats` all now require a finite
    value, output uses `allow_nan=False`), and within-tolerance duplicates depending on JSONL file
    order for their canonical metadata (fixed: a new `_deterministic_representative` picks via a
    stable sort on the record's own canonical JSON form). Codex then hit its usage cap
    (`.github`-adjacent Room quota ledger updated, resets 2026-09-19) mid-review, so round 3 ran
    on MiniMax instead (CLAUDE.md's standing "pair MiniMax... when Codex is capped" guidance,
    first real use of it this project) - **MiniMax independently traced every one of round 2's
    four named cases through the actual code with a full input/behaviour table and confirmed all
    closed, then found one REAL gap Claude's own fix had missed**: the exact same bool-subclass-
    of-int check added to the resolution file's `pair_index` was never extended to
    `pair_history.jsonl`'s own `pair_index` in `load_records` - a bool there would have silently
    coerced to `int(True)=1` via `_key()`, merging into pair 1. Fixed exactly as MiniMax specified
    (matching test included), re-verified the underlying Python semantics directly before
    trusting the report (same discipline used for every Codex finding this session). Full suite
    768/0/6 -> see the session's final count in `.github/ai-activity-log.md`.**
    Files changed: `Source/canonicalize_pair_history.py` (new),
    `Tests/test_canonicalize_pair_history.py` (new).
  - **C7 Step 1 BUILT + TESTED + EVALUATED, 2026-09-16 - honest result: DOES NOT beat baseline,
    NOT promoted past shadow mode.** Sam's direct instruction: wire `pair_history.jsonl` up so
    it actually helps, rather than leaving 31 real corrections unused. Built exactly as scoped
    above and by Codex's own MAJOR findings (report-only, shadow mode, leave-one-project-out
    evaluated, never overriding admissibility): `canonicalize_pair_history.shadow_swap_preference`
    (new function, same file) - a similarity-weighted average of the canonical corpus's real
    swap-beat deltas (BPM 0.3 / section-shape 0.7, identical weights to the existing
    `find_similar_pairs`), with `exclude_project` support for leave-one-project-out. Wired into
    `propose_arrangement.py` as a new `shadow_swap_preference` field on `OverlapAnalysis` and in
    `ARRANGEMENT_REPORT.json`'s per-transition output - **report-only, never read by
    `align_pair`/`_search_anchors`/any arrangement decision**, confirmed by construction (the
    call site only assigns a report field) and confirmed empirically (re-ran the real 15.09.26
    August Releases Mix's Phase 2 three times with the change active: the compressed `.als`
    hash differs between runs, traced to gzip's own embedded MTIME header in `apply_loops.
    compress_als`, unrelated to this change; the DECOMPRESSED XML content's own hash is
    byte-identical across three separate runs, `3c92fdc3dd76e475ecfed08091d08df7`). Full suite
    845 -> 857 (12 new tests: 6 in `Tests/test_canonicalize_pair_history.py`, 6 more in the new
    `Tests/test_evaluate_shadow_swap_preference.py` below - MiniMax caught this count wrong first
    at 851, re-verified directly: `pytest Tests/ -q` really does report 857).
    **New `Source/evaluate_shadow_swap_preference.py`** (+ 6 tests,
    `Tests/test_evaluate_shadow_swap_preference.py`): the leave-one-project-out held-out
    evaluation the plan requires before any promotion past shadow mode - for every canonical
    pair, predicts its delta from every OTHER project's pairs only, and compares against a
    trivial "always predict zero" baseline (most real corrections ARE zero-delta, so this
    baseline is the real bar to clear, not an arbitrary strawman).
    **Run for real against the current 31-pair canonical corpus: shadow hit rate 19% (6/31,
    within the canonicaliser's own 4-beat/1-bar tolerance) vs baseline 68% (21/31) - the
    shadow signal LOSES to "predict nothing changes" by 15 pairs.** Inspected individual rows,
    not just the headline number, to rule out a bug before accepting this as real: the
    mechanism works as designed, it is genuinely not accurate enough - non-zero real
    corrections (64, -52, -32, 32, -28 beats etc.) are systematically under-predicted because
    most of the corpus IS zero-delta, so any similarity-weighted average regresses toward a
    small number regardless of which project's transitions it draws from. **Not promoted -
    Step 1's own job (get an honest read before trusting anything) is complete and the honest
    read is negative for the current mechanism.** BPM+structure-shape similarity alone is not
    predictive of swap-delta magnitude/direction on this corpus; a real next step (unscoped,
    not attempted blind) would need either a materially bigger corpus or a different signal
    (the actual geometry fields the D12 rebuild added - entry/swap bar positions, structural
    role - rather than just section-type counts), evaluated the same honest way before being
    trusted for anything further. Corpus itself is UNDERPOWERED for a confident verdict either
    way (31 pairs, 4 projects) - the script says so in its own output.
    Also surfaced in passing, not fixed (out of scope for Step 1): `canonicalize_pair_history.
    load_records`'s "missing field(s)" error message is imprecise for the 15.09.26 mix's T4
    record - `sam_bass_swap_beat` is PRESENT but `null` (Sam ran no bass swap at all, matching
    burn list D13's T4/R6 no-EQ-swap-crossfade finding), not literally absent; correctly
    excluded either way, message just says the wrong reason.
    Evidence: `Source/canonicalize_pair_history.py` (shadow_swap_preference),
    `Source/propose_arrangement.py` (wiring), `Source/evaluate_shadow_swap_preference.py` (new),
    `Documentation/Mix Patterns Library/shadow_swap_preference_evaluation.json` (real run output).
    Files changed: `Source/canonicalize_pair_history.py`, `Source/propose_arrangement.py`,
    `Source/evaluate_shadow_swap_preference.py` (new),
    `Tests/test_canonicalize_pair_history.py`, `Tests/test_evaluate_shadow_swap_preference.py`
    (new), `Documentation/Mix Patterns Library/shadow_swap_preference_evaluation.json` (new).
    **Peer-reviewed 2026-09-16 - MiniMax + a Claude subagent standing in for capped Codex, both
    independent, both in parallel.** Both confirmed SOUND on all three questions (similarity math
    identical to `find_similar_pairs`, `exclude_project` genuinely excludes with no leakage,
    `shadow_swap_preference` verified report-only by a full grep of every reference in the
    codebase, the evaluation harness's tolerance/sign/baseline-fairness all correct, several
    individual rows hand-spot-checked against the raw JSONL and matched exactly, the subagent
    additionally re-ran the evaluation fresh and got a byte-identical JSON). Both independently
    found the SAME single real error, the same specific number: this entry's own "Full suite
    845 -> 851 (6 new tests)" undercounted - `git diff` shows 12 new tests (6 in
    `test_canonicalize_pair_history.py` + 6 in the new `test_evaluate_shadow_swap_preference.py`),
    not 6. Re-verified directly (`pytest Tests/ -q` -> 857 passed), fixed above. No other finding
    from either reviewer required a change - the negative result itself, and everything that
    produced it, holds up.
    Receipts: `Receipts/2026-09-16/minimax-review-C7Step1.md`, `Receipts/2026-09-16/claude-
    subagent-review-C7Step1.md` (includes disposition).
    Owner: Claude. Status: DONE - built, evaluated, documented, reviewed by 2 independent lenses,
    the one finding fixed. NOT promoted past shadow mode (the evaluation's own honest result).
  - **C10 - Teaching Mixes case-study library, for Claude's own judgement, explicitly NOT for
    the pipeline** - Sam, 2026-09-16, right after C7 Step 1's negative result: "this is not
    for the bots... this is for you as an AI looking for several different answers for the
    same transition and distilling which one might work best." A deliberately different thing
    from C7's formula: a library of real, distilled historical transitions Claude reads and
    reasons over directly (the same way Claude already reads narrative correction write-ups
    before building D13 decisions), never a scored/averaged signal.
    **BUILT + TESTED + EVALUATED, 2026-09-16.** Source material: `Teaching Mixes/`, 20 real
    finished mixes already in this repo (not the backup-drive archive the old, never-executed
    "Ground-truth ALS learning plan" targeted - Sam named this folder specifically). Real
    discovery before any extraction code was written, confirmed against actual files: these
    are DJ-mixer-style sets where individual song tracks carry ONLY the arrangement - the
    actual mix move (channel fader, filter/EQ sweep) lives on BUS/RETURN tracks ("A-Zone 62 DJ
    EQ" / "B-Zone 62 DJ EQ"), fed by each track's own sends, alternating roughly A/B/A/B (real
    exceptions confirmed, e.g. two edit layers of one song briefly sharing a zone). Sam
    confirmed this routing model directly (voice) before extraction began.
    New `Source/extract_teaching_mix_cards.py`: resolves each track's zone via its dominant
    send, finds real clip-overlap transitions (excludes a stray whole-mix reference-import
    track by span-outlier detection), reads each zone's Volume automation (converted to real
    dB via the same curve `automated_dj_mixes.als_generator._db_to_ableton_volume` uses,
    inverted) and filter/EQ macro automation inside the transition window, and a
    `_summarize_curve` shape-description that catches dips/spikes a naive start-vs-end
    comparison would miss (found and fixed during build: a filter that swept 64->32->64 read
    as "flat" before the fix, because start equalled end). The filter parameter resolves to a
    real, specific macro (`MacroControls.0` on the zone's own rack - confirmed by widening the
    device-resolution search window until it stopped hitting generic XML noise tags) - honestly
    labelled as a live-played, MIDI-CC-mapped knob whose downstream EQ target is not further
    resolved, never overclaimed as a specific band/frequency.
    Run across all 20 real files, zero crashes. **Honest coverage, not uniform**: only 5 of 20
    files carry any real automation (all on the newer Ableton schema, Live 10.1.25/12.3.2) -
    269 real transitions found total, 14,398 real automation points captured on the 5 rich
    files. The other 15 are either genuinely zero-automation (mixed live, confirmed zero
    `<AutomationEnvelope>` anywhere in the file) or use Ableton's Group Track routing instead
    of Return-track sends (3 files, not parsed by this pass - confirmed these 3 specifically
    have zero automation regardless, so the gap costs nothing today, but is a real, named
    limitation for a future file that might have both). 12 new tests
    (`Tests/test_extract_teaching_mix_cards.py`), including 2 pinned against real files (the
    rich Defected Ibiza CD2 and the empty Gbox Side 1). Full suite 857 -> 869.
    Output: `Documentation/Mix Patterns Library/Teaching Mixes Cards/` - 20 per-mix `.md` card
    files + `INDEX.md` (honest coverage summary, reading guide, known limitations).
    Evidence: `Source/extract_teaching_mix_cards.py`, `Tests/test_extract_teaching_mix_cards.py`,
    `Documentation/Mix Patterns Library/Teaching Mixes Cards/INDEX.md`.
    **PEER REVIEW ROUND 2, same day, found MiniMax's round didn't catch: two real bugs plus
    a major false claim, all in the Claude subagent's pass.** (1) `build_card`'s
    `if out_t.zone == in_t.zone` fired on `None == None`, fabricating a "same
    zone... direct edit/layer" claim on **188 of 269 cards (70%)** - confirmed by exact
    count before fixing, fixed (`out_t.zone is not None and ...`), 2 new tests both proved
    to fail against the pre-fix code. (2) `_track_zone` trusted `TrackSendHolder`'s own
    `Id="N"` attribute as a 0-based zone-slot index - confirmed false against the real
    Defected files (tracks 1-2 carry Ids "2,3,4" while every later track carries "0,1,2" for
    the identical three sends), mis-resolving the first two transitions of BOTH rich
    Defected files (track 1 read as the reverb return, track 2 as unresolved); fixed to
    resolve by order-of-appearance instead, re-verified against the real file (tracks 1-5
    now show the correct A/B/A/B/A pattern). (3) **The "12 of 20 mixed live, nothing
    captured" headline claim was flatly wrong.** Independently confirmed, then extended to
    all 15 non-rich files: every one carries real, substantial automation via a DIFFERENT,
    OLDER mechanism (`<ArrangerAutomation><Events><FloatEvent>`, drawn directly onto
    individual clips, not on a bus) that this pass never reads - 492 real multi-point
    curves, 26,822 real automation points, spread across all 15 files (8-56 curves / 72-5,693
    points per file). The "5 of 20" ceiling is a limitation of what this specific build
    reads (the zone-bus mechanism only), NOT of what these mixes actually contain - **a
    real, substantial, comparably-sized follow-on opportunity**, not a dead end. Left
    unbuilt this pass (different attribution model needed: per-clip/per-track, not per-bus;
    needs filtering real musical parameters from incidental on/off toggles among ~2200 raw
    blocks per file) - flagged clearly rather than attempted blind under the same push.
    All 20 real cards regenerated with both fixes; INDEX.md and the module's own docstring
    corrected to state the real, quantified picture instead of the false one. Full suite
    870 -> 872 (2 new regression tests). Both round-1 reviewers' findings and this round-2
    correction are independently re-verified by Claude against the real files before
    accepting any of it (exact-count matches, not just trusting the report).
    Receipts: `Receipts/2026-09-16/minimax-review-C10-TeachingMixes.md`, `Receipts/2026-09-16/
    claude-subagent-review-C10-TeachingMixes.md` (includes disposition).
    **ROUND 3, same day, Sam's direction ("keep going - build the extraction now"): built
    Mechanism 2, the direct-track automation the round-2 finding identified as missing.**
    New `TrackDirectAutomation` dataclass + `_track_direct_automation()` function reads
    the SAME older `<ArrangerAutomation>` mechanism directly off each SONG TRACK's own
    devices (no bus involved) - Mixer `Volume`, FilterEQ3's `GainLo` (bass-shelf gain,
    confirmed against a real file: MidiControllerRange [0.0003162277571, 1.99526238],
    producing plausible dB values when run through the same `_value_to_db` curve as the
    zone-bus mechanism, e.g. 0.993->~-0.06dB, 0.129->~-17.8dB - NOT independently verified
    against Ableton's own documented FilterEQ3 gain range, flagged for round-3 peer review
    to check), and AutoFilter's `Cutoff` (real range [20, 135] confirmed against a real
    file, reported as its raw unconverted value - lower = more filtered, direction
    confirmed, no Hz curve resolved). Reuses `_nearest_real_tag`, already reviewed and
    proven for the zone-bus mechanism. **Result: coverage rose from 5/20 to 19/20 files
    with real extracted automation** (14 more via direct-track, zero overlap with the 5
    zone-bus files - confirmed programmatically) - only Gbox Side 3 has real automation on
    none of the three classified parameters (its curves are Send/DryWet/Tempo). 4 new
    tests (2 synthetic unit tests pinning the Volume/GainLo/Cutoff classification and the
    single-point-block exclusion, 2 real-file tests). The old "zero automation" test for
    Gbox Side 1 was renamed and narrowed (it's no longer a zero-automation file) rather
    than deleted, keeping its still-valid "zone side stays honestly empty" assertion. The
    pinned corpus test extended to cover both mechanisms and their confirmed-zero overlap.
    Full suite 872 -> 875. All 20 cards regenerated. INDEX.md and the module docstring
    rewritten with the final numbers plus an explicit Revision history section, since the
    coverage figure has now moved twice in one day (5 -> "5 extracted + 15 confirmed-but-
    unextracted" -> 19) and future readers need to trust the CURRENT number, not one
    remembered from an earlier pass.
    **Round 3 peer review landed, SOUND, one honesty finding adopted and then RESOLVED
    into a genuine confirmation.** MiniMax (no execution access this dispatch, said so
    plainly) confirmed the regex/classification logic sound by static read and flagged
    that the `GainLo` dB conversion was stated with more confidence than verified - fair
    at the time. The Claude subagent then independently re-ran the extractor against the
    real corpus (every coverage number matched exactly: 5/14/19/1 files, 269 transitions,
    1,212/14,398 points), reconstructed 5 real card entries from raw XML by hand (all
    matched), and - beyond what was asked - queried Ableton's own Live manual directly:
    EQ Three's (FilterEQ3's) documented gain range is **-infinite dB to +6dB per band**
    (not the +-15dB an earlier, less targeted web search had suggested, which belongs to
    the separate Channel EQ device). The real file's own automation range converts to
    **+6.02dB**, matching Ableton's documented spec almost exactly - genuine independent
    confirmation, not a coincidence of plausible numbers. Docstring and INDEX.md updated
    to state this as CONFIRMED rather than "inferred, unverified." No code changes needed
    - round 3's build was sound as written; the finding upgraded confidence, it did not
    require a fix.
    Receipts: `Receipts/2026-09-16/minimax-review-C10-round3.md`, `Receipts/2026-09-16/
    claude-subagent-review-C10-round3.md` (includes disposition).
    Owner: Claude. Status: DONE, closed for this session. 19 of 20 real files now have
    real, extracted, twice-reviewed automation data across two confirmed mechanisms
    (zone-bus + direct-track). Only 1 file (Gbox Side 3) has none, and that's honest -
    its real curves are on parameters not classified as a mix move.
    Peer review: rounds 1-3 all SOUND after fixes. Round 1: MiniMax + Claude subagent,
    minor polish. Round 2: Claude subagent's ground-truth spot-check found 2 real bugs
    (70%-of-cards fabricated claim, mis-resolved zone routing) + 1 major false coverage
    claim, all fixed and re-verified. Round 3: MiniMax + Claude subagent on the new
    direct-track mechanism, one honesty finding adopted and then independently confirmed
    correct against Ableton's own documentation.
  - **C8 - not yet scoped**: let a musically-better candidate win even if it needs a loop
    extension to become geometrically valid, instead of discarding it before it's ever compared.
    The deepest, highest-risk piece - deliberately left unscoped until C5/C6/C7 are proven. The T2
    investigation above also found C8 was never going to reach T2's exact bar (157) either - it's
    not a registered cue, so C8's real value is letting OTHER geometrically-infeasible-but-
    musically-strong candidates compete, not a second route to T2 specifically.

  **CODEX PLAN REVIEW, ROUND 1, 2026-09-14: "REVISE THE PLAN BEFORE BUILDING C6."** Run in a real
  isolated git worktree (`.claude/worktrees/codex-c6c7c8-review`, `-AllowRepoRoot` - confirmed
  clean of tracked secrets first), full read access, `-Effort high`. NOT a rubber stamp - 3 FATAL
  + 4 MAJOR + 1 MINOR, all real, all code-grounded:
  - **FATAL - C6's own proposed verification order was backwards.** Plan said refresh the 380-pair
    baseline THEN attribute changes; the test's own convention is inspect-first, refresh only
    after - refreshing first destroys the evidence needed to review.
  - **FATAL - "rank everything globally" was left unspecified as an actual rule.** The current
    rank tuple's own tiebreak already ends on `overlap` (favours the later/wider candidate on
    ties) - simply pooling and taking max-rank risks reproducing the exact gaming the original
    earliest-wins design defended against, not fixing it. Needs an explicit rule (e.g. a later
    anchor only wins if it clears a real quality margin over the earlier one), with fixtures
    pinning both directions.
  - **FATAL - the pair_history corpus is not 34 clean, independent observations.** Real count:
    25 unique (project, pair_index) observations (Black Book pairs 1-9 logged twice under
    different sources); some directly CONFLICT (Black Book T3 is `corrected` in one record,
    `correct` in another); schema is inconsistent (a move delta is sometimes a field, sometimes
    only inside a `corrections` string, often absent). Real non-zero-move count: 10, not "7-9" -
    7 earlier, 3 later. C7 needs a canonicalised, deduplicated, revision-aware record format
    before it can influence a real decision.
  - **MAJOR - C7 has no integration contract today.** `find_similar_pairs` runs AFTER
    `compute_aligned_positions` has already called `align_pair` and locked geometry - it returns
    records, not scoring features, and nothing threads it into the search. "Put it inside
    `_search_anchors`" needs a new interface built first, not a small additive term. Recommended:
    start C7 in SHADOW MODE - report a proposed preference + confidence, never alter selection,
    evaluated leave-one-PROJECT-out (transitions within one mix are correlated, not independent
    samples).
  - **MAJOR - C7 must not be allowed to override admissibility while C8 is deferred.** T2's real
    correction changed BOTH swap point and overlap - a C7 override would secretly implement part
    of C8 without any of C8's feasibility/quality checks. C7 stays nudge-among-feasible-
    candidates only, and shadow-only first per the finding above.
  - **MAJOR - C8's own scope was materially wrong.** Existing loop mechanisms only EXTEND
    material - they cannot rescue a candidate already OVER the max overlap, only one below the
    minimum. "Feasibility" also can't be read off the numeric loop budget alone -
    `plan_fill_or_cut` can reject for clean-loop availability, repeat limits, named-cue
    reachability, or locked-swap constraints the budget number never sees. C8 needs an atomic
    candidate object (alignment + a real dry-run fill/cut plan + effective geometry + extension
    cost) - selecting alignment first and hoping planning succeeds is today's ordering problem,
    one layer down.
  - **MAJOR - the proposed C6 verification doesn't cover the actual trigger case.** The 380-pair
    baseline is a different corpus (14.08.26) than T2 (Tech House Heldout) - real regression
    coverage, not proof the redesign fixes the transition that motivated it. Also found live: the
    review worktree is MISSING the baseline's external stem-analysis fixtures entirely, so that
    test SKIPS cleanly rather than running - it cannot be the sole gate in every environment.
    Needs its own small, COMMITTED (not gitignored-external-data-dependent) T2-derived fixture
    pinning the real alternatives, selected anchor, final overlap, and automation style.
  - **MINOR - one current-code claim in the plan was wrong.** `_search_matched_tail_head` is not
    unconditionally tried first - it's gated behind `CUE_CONFIG.matched_tail_head_swap`, which
    defaults `False`, so the default landmark path never reaches it at all.
  Codex's own recommended order: (1) specify + test C6's anti-gaming rule, then build C6; (2)
  canonicalise pair_history, C7 shadow-reporting only; (3) scope C8 as a joint feasible-candidate/
  loop-planning redesign; (4) promote C7 from shadow to a real nudge only after held-out evidence
  - geometry override stays C8's job, never C7's.
  **Not yet actioned** - Sam redirected to B1 (turning on the four disabled flags) before the plan
  revision was written up. C6/C7/C8 stay exactly as Codex left them: real, substantial findings,
  plan not yet revised, nothing built. Full review:
  `.claude/worktrees/codex-c6c7c8-review/Documentation/Plans/swap-first-redesign/c6-c7-c8-plan.md`
  (the reviewed plan) - revise this file against the 8 findings above before the next round.
  Evidence: `Source/align_engine.py:1050,1174,1262,1372,2021,1831` (C5's six touched functions);
  `Source/apply_automation.py:809-816` (C2); `Source/propose_arrangement.py:1008-1052` (C7/old
  C1); `Documentation/Plans/swap-first-redesign/codex-review-brief.md:38-41` (item (c)'s
  original one-sentence scope, the landing order above expands on it).
  Owner: Sam (the sequencing decision itself); Claude (the resulting build work - C5/C2 built,
  C6/C7/C8 to follow, Codex review planned before C6/C7 land). Peer review: n/a for the decision
  itself, same as A5 - this is Sam's call, not a build (C5/C2's OWN peer review is tracked below,
  separately from B3).

## C - The structural fix that actually reduces Sam's manual work (item "c" + the learning loop)

- [-] **`pair_history.jsonl` holds 16+ real Sam corrections and nothing reads it to make a
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
  **Status: SUPERSEDED, 2026-09-14** - not dropped, folded. Scoping B3's investigation found this
  is the SAME decision point as item (c)'s candidate selection, not a separate standalone
  consumer to build first: `find_similar_pairs` doesn't even read the swap-delta fields
  (`claude_bass_swap_beat`/`sam_bass_swap_beat`/`bass_swap_delta_beats`) when matching
  similarity, so a standalone consumption interface built now would very likely need rebuilding
  once item (c)'s actual selection mechanism is designed. Tracked as landing-order item **C7**
  under B3 above; this line stays for the trail, do not action separately from B3's breakdown.
  Owner: Claude. Peer review: n/a - superseded, not built.

- [x] **Automation style (QUICK_SWAP/STANDARD/LONG_BLEND) is still chosen purely by overlap
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

  **BUILT + TESTED + VERIFIED, 2026-09-14.** `apply_automation.py`'s style selection now
  consults `outgoing_has_post_swap_content` with the SAME landmark-policy-only scoping the
  margin fix already established - legacy/non-aligner path byte-identical, unchanged. 5 new
  tests, proved-the-test on the core branch. **Honest real-data finding, not glossed over**: ran
  the actual real corpus (all 8 transitions x 3 sides) through the new logic - exactly ONE
  transition in the whole project has overlap under the 24-bar line (side A's T2, the Freejak->
  HARTY case this fix was named for), and its `outgoing_has_post_swap_content` reads **False** -
  Freejak's swap point sits exactly 1 bar from its own arranged end under `interim_v1`'s CURRENT
  geometry, correctly read as genuinely cold-ending. **This fix produces ZERO behaviour change
  on this project's real data today** - real confirmation, not a setback, that C2 alone cannot
  fix the transition that motivated it; only moving the swap POINT (item c) can. C2 remains real
  and correctly built for other/future transitions where a short overlap genuinely has more than
  1 bar of remaining content.
  Full suite 713/6/0 -> 718/6/0.
  **Peer-reviewed 2026-09-15 - MiniMax + an independent Claude subagent, both full reviewers**
  (Codex durably capped; Kimi hit its own 5-hour cap on this exact dispatch, recorded in the
  quota ledger, substituted with a Claude subagent per CLAUDE.md's standing guidance). Both
  independently confirmed the content-aware branch is correctly landmark-only-gated (the
  `not aligner_chosen or not outgoing_has_content` short-circuit never evaluates the second
  operand on the legacy path) and that the new tests genuinely discriminate old vs new code -
  the subagent went further and proved this empirically, not just by reading: it re-ran the new
  tests against the pre-C2 commit (1b9b0a4) in an isolated worktree and confirmed 2 of 5 tests
  actually fail there. **The subagent found one real, independent gap MiniMax's static read
  missed**: `propose_arrangement.py:1971-1974` computes its own SEPARATE `selected_style` field
  (written into `ARRANGEMENT_REPORT.json`, documented in `AI_CONTEXT.md:1064`) using the OLD
  overlap-length-only rule this fix patched - C2 fixed the rule `apply_automation.py` actually
  ACTS on, but missed this second, now-stale copy of the same decision. Confirmed directly:
  `selected_style` has zero readers anywhere in `Source/` or `Tests/` (grepped) - report-only,
  zero effect on real automation or audio output, but the JSON report will now silently disagree
  with real behaviour for exactly the transitions C2 exists to fix. Low severity, spun off as
  its own item (C9) rather than folded into this one, since it's independently closeable and
  C2's own fix is otherwise clean.
  Owner: Claude. Author: Claude. Peer review: SOUND - MiniMax + Claude subagent (Kimi-capped
  substitute), one real follow-on finding (spun off as C9), no finding required a change to
  C2's own diff.

- [x] **`align_engine.py`'s overlap/loop-size admissibility caps were frozen module constants,
  never actually reading the policy threaded through the call chain** (C5) - landing-order item
  under B3 above. `MAX_OVERLAP_BARS`/`MAX_LANDMARK_OVERLAP_BARS`/`MAX_LOOP_REPEATS`/
  `MAX_LOOP_EXTENSION_BARS` were computed once from `INTERIM_V1` at import time and never
  reassigned; every candidate-admissibility gate that used them (`_align_pair_landmark_aware`,
  `_search_matched_tail_head`, `_search_tail_anchor_rescue`, `_search_anchors`,
  `plan_fill_or_cut`, `pick_cue_bounded_drum_loop`) ignored whichever `policy` object was
  actually passed in. Invisible today only because `SAM_V1` happens to share every one of these
  fields with `INTERIM_V1`; any future policy with genuinely different caps would have this
  silently keep using `INTERIM_V1`'s numbers.

  **BUILT + TESTED + VERIFIED, 2026-09-14.** All six functions now read the live `policy`
  parameter (added where missing - `pick_cue_bounded_drum_loop` gained a `policy=None` parameter,
  defaulting to `INTERIM_V1`, identical to every other function's own default handling in this
  file). Deliberately scoped to the LANDMARK path only - `align_pair`'s own legacy/non-landmark
  branch (used only when `USE_ALIGN_ENGINE`'s fallback fires) was left completely untouched,
  matching this project's own hard-won "legacy path stays byte-identical, only the landmark path
  gets new signal-awareness" convention (the exact same scoping the item-(a) margin fix and C2
  above both use, for the same reason: the 380-pair verification corpus only covers the landmark
  path). 6 new tests (`Tests/test_policy_threading.py`) prove the fix does something a frozen
  constant never could - a custom policy with a tighter `max_overlap_beats` now genuinely REJECTS
  a candidate the default policy accepts; a custom policy with `max_loop_repeats=1` now genuinely
  REJECTS a loop needing 2 repeats. Proved-the-test on both (reverted each fix, confirmed the
  exact predicted failure, restored). **Zero behaviour change for INTERIM_V1**, proven the
  strongest way this project has for `align_engine.py`: the full 380-pair historical baseline
  sweep (`Tests/test_alignment_baseline.py`) passes with ZERO changed decisions - not a synthetic
  claim, a real corpus of real past transitions. (One purely cosmetic near-miss caught by the
  baseline itself and fixed: an added `:g` format spec would have trimmed "48.0" to "48" in one
  error message text; removed to keep the message byte-identical too, not just the underlying
  number.)
  Full suite 718/6/0 -> 724/6/0.
  Files changed: `Source/align_engine.py`, `Tests/test_policy_threading.py` (new).
  **Peer-reviewed 2026-09-15 - MiniMax + an independent Claude subagent** (same dispatch as C2
  above). Both independently confirmed all six functions are genuinely fixed (grepped every bare
  reference to the four constant names; remaining ones are the legacy `align_pair` branch,
  correctly out of scope) and that `pick_cue_bounded_drum_loop`'s `policy=None` default cannot
  crash (`policy = policy or _DEFAULT_POLICY` runs before any attribute access). Both confirmed
  "zero behaviour change for INTERIM_V1" is STRUCTURALLY guaranteed by `_DEFAULT_POLICY =
  INTERIM_V1`, not an accident of SAM_V1 currently sharing its values (the subagent additionally
  confirmed SAM_V1 has zero production call sites at all - referenced only in its own definition
  and tests). The subagent re-ran the new tests against the pre-fix commit (353fb85) in an
  isolated worktree: 3 of 6 genuinely fail there, empirical proof the tests discriminate.
  Owner: Claude. Author: Claude. Peer review: SOUND - MiniMax + Claude subagent (Kimi-capped
  substitute, same dispatch as C2), no findings on this item's own diff.

- [x] **`propose_arrangement.py`'s `ARRANGEMENT_REPORT.json` computes its own stale copy of
  automation-style selection, now out of sync with C2's fix** (C9) - found by the Claude subagent
  reviewing C2, 2026-09-15. `propose_arrangement.py:1971-1974` writes a `selected_style` field
  (`"quick_swap" if overlap_bars < 24 else "long_blend" if overlap_bars > 36 else "standard"`)
  using the exact overlap-length-only rule C2 patched in `apply_automation.py` - but this is a
  SEPARATE computation C2 never touched, so the report now silently disagrees with what
  `apply_automation.py` actually builds for any transition where C2's new content-aware check
  changes the outcome. Confirmed directly: `selected_style` has zero readers anywhere in
  `Source/` or `Tests/` (grepped) - report-only, no effect on real automation or audio today,
  which is why this is its own low-severity item rather than blocking C2. Same class of bug as
  C5 (frozen/duplicated decision logic, one copy fixed and one forgotten) - the actual fix is
  almost certainly to make this field consult `outgoing_has_post_swap_content` the same way C2
  did, or to just remove the field if nothing will ever read it.
  Evidence: `Source/propose_arrangement.py:1971-1974` (Claude subagent, verified directly by
  Claude); `Documentation/AI_CONTEXT.md:1064` (field documented as part of the report schema).

  **BUILT + TESTED, 2026-09-15.** `generate_report` now overwrites `selected_style` inside its
  existing `if al is not None:` block using the exact same rule `apply_automation.py`'s C2 fix
  uses (`LANDMARK_POLICIES` membership gates whether `outgoing_has_post_swap_content` applies at
  all; legacy/non-landmark alignments keep the byte-identical original overlap-length-only rule).
  `LANDMARK_POLICIES` imported locally inside `generate_report`, matching this file's own
  existing pattern elsewhere. The old eager overlap-length-only computation stays as the fallback
  for the `al is None` case (no matching alignment for that pair index) - unchanged behaviour
  there. 6 new tests (`Tests/test_arrangement_report_style.py` - `generate_report` had zero
  direct test coverage before this, confirmed by grep). Proved-the-test: stashed the fix, re-ran
  all 6 - exactly 1 fails (the real discriminator: landmark policy + short overlap + real content
  now reads "standard" instead of "quick_swap"), the other 5 pass either way (correctly - they
  exercise paths the fix doesn't touch: legacy path, cold-ending content, long overlap, no-al
  fallback, medium overlap). Restored the fix, all 6 pass. Full suite 774/0/6 -> 780/0/6.
  Files changed: `Source/propose_arrangement.py`, `Tests/test_arrangement_report_style.py` (new).
  **Peer-reviewed 2026-09-15 - MiniMax.** No material objections. Verified field-for-field the
  mirrored rule is genuinely identical to `apply_automation.py`'s own C2 rule (thresholds, gate
  condition, default, string values, `overlap_bars` source all matched); confirmed the legacy
  path collapses to exactly the original overlap-length-only test via short-circuit evaluation;
  confirmed no path leaves the eager default wrong-but-unoverwritten; confirmed the
  `LANDMARK_POLICIES` local import is correctly scoped and matches the file's own established
  precedent (3 other identical import lines); independently re-ran the "zero readers" grep
  (confirmed: only the writer + its own test reference `selected_style` anywhere); hand-traced
  all 6 tests against old vs new code and confirmed the "exactly 1 discriminates" claim exactly.
  Owner: Claude. Author: Claude. Peer review: SOUND - MiniMax, no findings required a change.

- [x] **Production tempo-arc builds are blocked without MIK even though the certified BPM
  already exists** (C3) - `t.bpm`/`camelot`/`energy` are populated ONLY from the MIK database;
  `--tempo-arc` then hard-raises `"tempo arc needs a certified BPM for every track"` even when
  the owned stem-grid detector already measured every BPM in the same run. Astra reproduced the
  exact `float(None)` crash directly on this held-out project in dry-run mode. This is carried
  from the 2026-09-01 friction card and is still open - it blocks turning a winning experimental
  side into an actual deliverable on any machine where MIK's UI automation is unreliable
  (confirmed unreliable on this Home PC specifically).
  Evidence: `Source/propose_arrangement.py:1125-1132`, `:1299-1301` (Fable + Astra,
  independently, matching line numbers).

  **BUILT + TESTED, 2026-09-15.** New `fill_missing_bpm_from_stem_grid(tracks, stem_dir)`
  (`Source/propose_arrangement.py`, called right after the existing MIK-enrichment block, same
  `als_path.parent.parent / "_Stem Analysis"` lookup pattern `compute_aligned_positions` already
  uses a few lines below it) reads each track's own `SECTIONS_STEM_*.json` (`"bpm"` field - the
  same file `align_engine.load_track` reads for sections/landmarks, confirmed directly it already
  carries a certified `bpm` alongside `n_bars`/`sections`/`signals`/`track`) and fills `t.bpm`
  ONLY for tracks MIK left empty - MIK stays authoritative on conflict, matching every other field
  in that block. Handles the same escaped/unescaped track-name inconsistency the MIK lookup
  already works around (confirmed directly: `SECTIONS_STEM_*.json`'s own `"track"` field is
  unescaped, `TrackInfo.name` can carry the sections-JSON-escaped form). Extracted as a small,
  independently-testable function (this project's established pattern, e.g. C5's C5-era
  `_resolve_inherited_tempo_and_warp_modes`) rather than left inline. 7 new tests
  (`Tests/test_bpm_fallback_stem_grid.py`): fills when MIK left it empty, MIK stays authoritative
  on conflict, the escaping case, no-stem-dir and track-absent-from-stem-dir both correctly leave
  `t.bpm` None (not a crash), malformed JSON in the stem dir doesn't take down other tracks'
  fallback, and a `bpm: 0` in the JSON is correctly NOT treated as a real value (falsy, matches
  the existing `if mik.bpm:` truthy-check convention). Full suite 729/0/6 -> 736/0/6.
  **Peer-reviewed alongside C7 Step 0 (same staged pass, see that item's write-up below for the
  full round-by-round detail) - Codex rounds 1-2 found real findings (a bad stem-grid BPM could
  crash the fallback or silently poison a tempo-arc build with nan/negative/absurd values, both
  fixed with a try/except + the same 60-200 BPM sanity range `propose_arrangement`'s own
  project_bpm validation already uses); Codex then hit its usage cap (recorded in the Room's
  shared quota ledger, resets 2026-09-19) so round 3 ran on MiniMax instead (per CLAUDE.md's
  standing "pair MiniMax... when Codex is capped" guidance) - MiniMax confirmed C3's own half
  clean with no further findings ("correctly scoped and tested... no new isinstance survivors").**
  Full suite after all fixes: 768/0/6 (background run before D5/D7/E2 landed) -> see C7 Step 0's
  line for the final post-MiniMax-fix count.
  Files changed: `Source/propose_arrangement.py`, `Tests/test_bpm_fallback_stem_grid.py` (new).
  Owner: Claude. Author: Claude. Peer review: SOUND - Codex (2 rounds, `-Effort high`, real staged
  files) + MiniMax (round 3, Codex-capped substitute, real staged files) - all real findings
  fixed and independently re-confirmed.

- [ ] **Documented correction overrides (`intro_skip_bars`, `loop_source_sec`) are silently
  ignored on the production alignment path** (C4) - `/mix`'s own docs list `intro_skip_bars` as
  a CLOSED gap; `propose_arrangement.py` itself warns that `align_engine` (the production path,
  `USE_ALIGN_ENGINE=True`) does not honour it. `loop_source_sec` only affects the legacy
  loop-planning branch (`_plan_loop_extensions`, gated `elif not USE_ALIGN_ENGINE`). An approved
  correction from a previous session does not survive regeneration - exactly the kind of thing
  that causes Sam to re-fix the same thing twice.
  Evidence: `Source/propose_arrangement.py:562,664,1151,1190` (Astra);
  `Claude Code Brain/commands/mix.md:585` (Fable, same finding).

  **INVESTIGATED 2026-09-15 - real current impact is ZERO, docs fixed, the actual wiring
  deliberately NOT attempted this round.** Confirmed directly (not assumed): `align_engine`'s own
  `plan_fill_or_cut` has its OWN separate cut/loop generation (`intro_cut`, `incoming_intro`,
  `outgoing_tail` specs) that is entirely AUTOMATIC (e.g. `intro_cut` fires when the swap lands
  inside a break/fill section) - it has no hand-authored-hint-consuming mechanism at all today,
  genuinely different machinery from the legacy `_plan_loop_extensions` these two hints feed.
  Scanned every real `track_hints.json` in this project's history (6 files, every real project
  that has ever used the hints pipeline): **zero tracks, ever, have set either hint to a non-zero
  value.** This is a real, documented gap with a plausible future-correction risk, but it has not
  yet cost Sam a single re-fix. Given the proper wiring is genuine align_engine design work at the
  same risk tier as C6 (a new hint-consuming mechanism integrated with `plan_fill_or_cut`'s
  existing intro_cut/incoming_intro mutual-exclusion logic, needing the same baseline-verification
  + Codex-review rigor) - not attempted this round, deliberately, rather than rushed. **What WAS
  fixed:** `/mix`'s docs (`Claude Code Brain/commands/mix.md` and `Codex Brain/commands/mix.md`,
  content-verified identical after edit, `diff -w -B` clean) no longer claim these are CLOSED -
  both rows now say OPEN on the production path, with the confirmed mechanism and the
  zero-real-usage finding recorded inline so a future session doesn't have to re-derive it.
  Files changed: `Claude Code Brain/commands/mix.md`, `Codex Brain/commands/mix.md`.
  Owner: Claude. Author: Claude (docs only this round). Peer review: NONE - not yet reviewed (docs
  fix only; the actual wiring is unbuilt, real follow-up work).

  **PLAN WRITTEN, 2026-09-22 - NOT BUILT.** Sam said "go ahead" on this item too, but its own
  2026-09-15 investigation already sized the actual wiring as "genuine align_engine design work
  at the same risk tier as C6... needing the same baseline-verification + Codex-review rigor" -
  `plan_fill_or_cut` runs on every transition of every mix, and this session already shipped
  three other real fixes (D4, D9, E8) with that same rigor, so rushing a fourth into the core
  alignment path without proper review was judged the wrong call, not a refusal to act. Full
  design at `Documentation/Plans/c4-hint-wiring-plan.md`: bridges both hints onto `align_engine.
  Track` the same way the existing `first_drop_sec`/etc. hints already do, reuses the existing
  `CueConfig.emit_hint_fields` gate and the existing `intro_loop` mutual-exclusion mechanism
  rather than inventing new machinery, and proposes the same 380-pair baseline-sweep discipline
  D9 used (expected: 0 changed pairs, since 0 real projects set either hint today - this is the
  regression proof). Dispatched for design review (MiniMax) alongside D8's plan - pending.
  Owner: Claude. Status: OPEN - plan reviewed, verdict PARK (see below). Touched: 2026-09-22.

  **REVIEWED, 2026-09-22 - MiniMax: DROP (park it), independently agreeing with the plan's own
  leaning.** Zero real usage, zero live defect, optional polish on a path that runs on every
  transition of every mix; the existing runtime WARNING already covers the one real
  silent-failure risk. Two independent judgments landing on the same call - recorded as the
  decision, not left open. Real design gaps found for whenever this IS eventually built (folded
  into the plan doc): the plan's claimed "cut-construction helper" doesn't exist (would need
  extracting); the `intro_loop` mutual-exclusion flag is verified correct but misleadingly named
  for a cut; needs to name which of the three `pick_clean_drum_loop` call sites the
  `loop_source_sec` hint reaches; needs to explicitly include the vocal/fill `_blocked` check,
  not just the numeric quality metrics. Full detail: `Documentation/Plans/c4-hint-wiring-plan.md`.
  Peer review: SOUND (as a decision to park) - MiniMax
  `Receipts/2026-09-22/minimax-review-d8-c4-plans.md`.

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

  **INVESTIGATED, 2026-09-15 - the item's own caveat confirmed, thin evidence.** Checked the one
  Sam hand-tweak ALS available on this machine (`Test Project/10.09.26 Tech House Heldout/
  Output/AB/A/Mix A Sams Tweaks Project/Mix A Sams Tweaks.als`) against the production `Mix
  A.als` it was tweaked from. `<Utility ` device count identical (22 in both - Sam added no new
  gain-dip devices). `<FloatEvent` (real automation breakpoints) count: 164 -> 165, i.e. exactly
  ONE new breakpoint across the whole file. (`<AutomationEnvelope` count jumped 35 -> 123, but
  that's a known Ableton UI artifact - opening/interacting with a project in Live materialises
  near-empty envelope containers for parameters with a visible automation lane, independent of
  whether the user actually drew anything - not a signal of real edits on its own.) **No
  systematic loudness-dip pattern exists in this sample** - confirms the item's own stated
  caveat rather than overriding it. This is n=1 and thin: it means no example currently exists
  to reverse-engineer exact numbers from, not that Sam's described technique is wrong (the
  Production Polish Backlog's own numbers - 0.25-0.5dB dip + gentle low-shelf, restoring after
  the overlap - are stated directly, not inferred, so building TO that written spec rather than
  FROM learned data remains a real option). Did not build: this touches `apply_automation.py`'s
  automation-build logic (same review bar as C2 above), and no peer was free this session (both
  MiniMax and Kimi were mid-review on C5/C2 when this was checked) - queued rather than built
  blind or without a plan review.
  Owner: Claude. Peer review: NONE - not yet reviewed (investigation only, no code written).

- [x] **Mix endings need a real trim/fade decision, not silent-tail warnings every time** (D2) -
  the held-out render's last 7.4s sit at -62dBFS (Jewel Kid's own documented fade-out), and
  `render_check`'s `exposed_solo` check flags it every time with no trimming mechanism to act on
  it. Astra frames this correctly: this needs an ending/trim policy decision, not automatic
  treatment as broken internal silence (which it isn't).
  Evidence: `Test Project/10.09.26 Tech House Heldout/Output/RENDER_CHECK.md:21` (Fable, path may
  be stale - re-render first); `Source/render_check.py:1501` (Astra).

  **DECIDED, 2026-09-15 (Sam, asked directly): leave the warning as-is.** No auto-trim, no
  smarter fade-recognition - the noise is harmless and not worth building against. Closes the
  open question (what policy?) even though no code changes; the warning keeps firing on every
  render exactly as it does today, by explicit choice.
  Owner: Claude. Peer review: n/a - Sam's own decision, nothing built.

- [ ] **Two other real render warnings on this project need fresh-render re-measurement before
  any repair is sized** (D3): the T2 transition_dip (3.23dB, sub-band deficit) is UNBRACKETED -
  its true minimum lies outside the measured window, so don't size a fix from the current
  numbers; re-measure with more context on the fresh render from A2 first.
  Evidence: `Test Project/.../RENDER_CHECK.md:18` (path may be stale); `Source/render_check.py:1738,1772`
  (Astra).

  **BLOCKED, 2026-09-15 - the fresh render itself is gone from this machine.** The A2 fresh
  `RENDER_CHECK_A/B/C.md`/`.json` reports DO still exist and DO still show T2 (pair_index=2) as
  `unbracketed=True`, `dip_db=3.11` (close to the cited 3.23, real render-to-render variance) -
  the underlying defect this item describes is real and still present. But re-measuring with a
  wider window (the same manual technique that found pair 15's true depth was 7.90 dB, not 3.78,
  on 2026-08-28) needs the actual rendered WAV to recompute short-term LUFS from - checked
  directly: `Test Project/10.09.26 Tech House Heldout/Output/AB/A/` has no `.wav` file on this
  machine (STUDIO-2) right now, matching this project's own standing churn warning about
  machine-local `Test Project/` outputs. `render_check.py` itself has no automatic "widen and
  re-search when unbracketed" mechanism - every prior resolution of this class (pair 15) was a
  one-off manual re-run with a larger `TRANSITION_DIP_SPAN_BEATS`, not a standing code path -
  so there is nothing to fix in code here either, only a re-render + manual re-measurement to do
  once the WAV exists again (on whichever machine actually has it, or after a fresh bounce).

  **ASKED, 2026-09-15 - Sam: leave it parked.** Not chasing a fresh bounce right now. Stays open,
  genuinely blocked, revisit whenever a fresh render happens to exist on some machine.
  Owner: Claude. Peer review: NONE - not yet reviewed (blocked, not built).

- [ ] **Restore reliable key/harmonic metadata without re-depending on MIK's UI automation**
  (D4) - this held-out run had no key data at all, so harmonic sequencing never ran; Astra found
  the sequencer substitutes a hardcoded `1A` for missing keys in a mixed known/unknown pool
  rather than preserving "unknown" honestly, which can silently mis-sequence. A chroma/Essentia
  fallback estimate would close this without adding back a Rekordbox-style dependency.
  Evidence: held-out `Output/Visualisations/REVIEW_A.md` "Known limitations" (both);
  `Source/automated_dj_mixes/orchestrator.py:638` (Astra).

  **HALF BUILT, 2026-09-22 - the fabrication bug is fixed; the chroma/Essentia fallback
  estimator is NOT (that's a separate, larger feature this item's own title also names, not
  attempted this round).** `orchestrator.py:638`'s `a.camelot or "1A"` silently substituted a
  real, named Camelot code for missing key data whenever a track had none - the exact
  "silently mis-sequence" risk Astra flagged, confirmed directly by hand-tracing
  `sequencer._edge_cost`/`_count_clashes`: the fabricated "1A" could either manufacture a CLASH
  against an unrelated track, or - the worse case - manufacture a perfect "identical" (the best
  possible score) match between two tracks that both merely lack key data, actively pulling
  them together as if confirmed compatible. Fixed: `a.camelot` now passes through `None`
  honestly; `_edge_cost` gives an unknown-key pair a fixed neutral cost
  (`_W_UNKNOWN_KEY = _W_SMOOTH * 2`, the "power_mix" midpoint - no fabricated preference either
  way, BPM terms decide among ties, matching `_bpm_proximity`'s own existing "unknown -> 0.5
  neutral" philosophy) instead of computing a real compatibility score on a fake code;
  `_count_clashes` excludes an unknown-key pair from the tally entirely (neither confirmed
  clash nor confirmed safe), on both sides of `apply_energy_arc`'s reorder comparison
  consistently. 6 new tests in `Tests/test_sequencer.py`, all confirmed to fail against the
  pre-fix code (`git stash`, 5 crashed outright with `TypeError: 'NoneType' object is not
  subscriptable` - the old code couldn't even survive being handed an honest `None`, proving
  both halves of the fix - the honest-None passthrough and the None-safe cost function - are
  each necessary). Full suite 889/6/0. **Still open:** the chroma/Essentia fallback estimator
  (an actual best-guess key for a track with zero key data at all, vs. today's honest
  "unknown") is unbuilt - a real audio-analysis feature, not a bug fix, and out of scope for
  this round.
  Files changed: `Source/automated_dj_mixes/orchestrator.py`,
  `Source/automated_dj_mixes/sequencer.py`, `Tests/test_sequencer.py`.

  **REVIEWED, 2026-09-22 - MiniMax SOUND; a Claude subagent found and fixed one real bug the
  MiniMax pass missed.** MiniMax confirmed the None-passthrough, the neutral-cost mechanism, and
  the `_count_clashes`/`apply_energy_arc` self-consistency by direct code trace - SOUND, no
  correction. The independent Claude subagent (standing in for durably-capped Codex) went
  further: it didn't just read the fix, it CONSTRUCTED an adversarial fixture and ran it -
  `build_harmonic_path([{camelot:"1A"}, {camelot:"2B"}, {camelot:None}])` - and found the first
  `_W_UNKNOWN_KEY` value (`_W_SMOOTH*2`=2000, reasoned as a "neutral midpoint") was actually
  CHEAPER than a real, CONFIRMED "diagonal" compatibility (score=1, cost `_W_SMOOTH*3`=3000) -
  so the optimizer preferred splicing a total unknown between two tracks over honouring their
  real, if weak, harmonic relationship. Reproduced directly (not taken on the subagent's word):
  `['B','U','A']` - the known diagonal pair A/B were split apart by the unrelated unknown track.
  Fixed: `_W_UNKNOWN_KEY` raised to `_W_SMOOTH*4`=4000, strictly above the worst real non-clash
  score, so unknown can only ever be preferred over a CONFIRMED CLASH, never over any real
  relationship. Re-verified: same fixture now returns `['U','B','A']` - A/B stay adjacent. New
  permanent regression test `test_unknown_key_never_preferred_over_a_confirmed_weak_match`
  pins this exact falsifier. Full suite 890/6/0 after the fix. (Out-of-scope finding also
  surfaced by the subagent - 8 stale `.tmp.<pid>.<hash>` leftover files scattered across
  `Source/automated_dj_mixes/`, unrelated to this change, spun off as a separate background
  task rather than touched here.)
  Author: Claude. Owner: Claude. Status: fabrication bug DONE 2026-09-22 (reviewed SOUND after
  one real correction, applied same session), chroma/Essentia estimator still OPEN.
  Peer review: SOUND - MiniMax `Receipts/2026-09-22/minimax-review-d4-e8.md`; CORRECTION
  (adopted) - Claude subagent (verdict + reasoning in this session's transcript, not yet a
  saved receipt file).

- [x] **Wire the already-built `hints_from_stem_result` into `/mix`'s manual hint-authoring
  step** (D5) - the derivation exists and has a `--write-hints` flag, but Phase 1f still has Sam
  or Claude read four timestamps per track off the DETECT picture by eye. A prior audit measured
  ~20 min/project and ~2 misreads per 10 tracks for the manual version. Derive-then-adjust
  removes the misread class entirely; this has had no owner since it was carded 2026-09-02.
  Evidence: `Source/stem_detector.py:1362,1418` (Fable); `Claude Code Brain/commands/mix.md:124-154`
  (Fable).

  **BUILT, 2026-09-15 - a workflow/doc fix, not new code.** `hints_from_stem_result()` and its
  `--write-hints` CLI entry point already existed and already worked before this session (this
  item's own evidence line cites them as pre-existing) - the actual gap was `/mix`'s own Phase 1f
  instructions, which described hand-transcribing four timestamps per track from the DETECT
  picture with no mention of the existing flag at all. Rewrote Phase 1d's "Hint authoring" bullet
  (now "Hint spot-check" - the DETECT picture is used to VERIFY the auto-written values, not
  originate them) and Phase 1f (now runs `python Source/stem_detector.py "<project-path>"
  --write-hints` as the default first step, with manual correction demoted to a targeted per-field
  fix for whatever the spot-check flags, not a return to authoring the whole file). Also folded in
  C4's finding inline (the two OPTIONAL hint fields, `intro_skip_bars`/`loop_source_sec`, are only
  honoured on the legacy path today - noted at the point Sam would consider adding one, not left
  for him to discover the hard way). Both brains updated (`Claude Code Brain/commands/mix.md`,
  `Codex Brain/commands/mix.md`), content-verified identical after edit (`diff -w -B` clean).
  **Not independently re-validated at runtime this session**: `hints_from_stem_result()` itself is
  pre-existing, already-shipped code this session didn't touch, and exercising `--write-hints`
  live needs the full pipeline's own bpm/downbeat resolution context (confirmed: calling
  `stem_detector.detect()` standalone outside that context fails with "no stats" even against an
  already-analysed track's cache) - reproducing that context was out of proportion for a docs-only
  change to code that already ships and already runs inside the real `/mix` pipeline today. Stated
  plainly rather than silently assumed.

  **GAP CONFIRMED AND FIXED, 2026-09-22 - exercised live for real, building a genuine new mix
  ("22.09.26 Tech House Core Sample").** The unverified risk above was real: `--write-hints`
  printed `[skip] no stats (bpm/downbeat)` for all 11 tracks and wrote an empty `track_hints.json`,
  even immediately after a full Phase 1a stem-grid/stem-sections/kick-model run on the same
  project. Root cause confirmed by reading the code: `detect()`'s only fallback for a missing
  bpm/downbeat was a `Sections Review/Blind_V*` folder from the amplitude blind-viz pipeline,
  RETIRED 2026-06-10 - nothing writes that folder any more, so the fallback always failed. Fixed:
  extracted the resolution logic into `_resolve_bpm_downbeat_stats()`, which now reads bpm from
  the track's own already-written `_Stem Analysis/SECTIONS_STEM_*.json` first (downbeat is always
  0.0 by this pipeline's own convention - stem bar 0 is the downbeat), falling back to the retired
  Blind_V mechanism only for an old project that genuinely still has one. Re-ran `--write-hints`
  live on the real project: 11/11 hints written. 5 new tests in
  `Tests/test_stem_detector_bpm_fallback.py`, proved to fail against pre-fix code (the function
  didn't exist at all before this fix - import error). Full suite 896/6/0. This closes D5's own
  stated caveat completely - the fully-autonomous hint path this item was built to deliver now
  actually works end to end, confirmed on a real build, not just believed to.
  Files changed: `Claude Code Brain/commands/mix.md`, `Codex Brain/commands/mix.md`,
  `Source/stem_detector.py`; new: `Tests/test_stem_detector_bpm_fallback.py`.
  Committed 2026-09-22 (`4d58926`), pushed to `burn-list/a1-a4-2026-09-14`.
  **Peer-reviewed 2026-09-22 - MiniMax, SOUND** (`Receipts/2026-09-22/minimax-review-d5-
  followup.md`; staged with `align_engine.py` as the seam file). Independently re-verified its
  two load-bearing claims against the real code before accepting: `align_engine.py:2654`'s
  docstring does state the "stem bar 0 == downbeat" convention verbatim, and `load_track()`
  reads `bpm` from the same cache independently of this fix (no seam risk), confirmed by
  reading both lines directly. One real nitpick adopted and fixed same-day: the bare
  `except: pass` on a malformed cache gave the exact same `[skip]` message as "no cache yet" -
  the same look-alike-failure shape that hid the original bug for months. Added a `[warn]` print
  naming the exception, locked in with a 6th test (`capsys` assertion). Two nice-to-haves
  declined as unneeded scope (a provenance-tuple refactor of the `stats`/`"sections"` shape
  check MiniMax itself called "fine to ship as-is"; a `TypeError` guard for non-numeric `bpm` in
  the cache, defensive against a state that has never occurred). Full suite re-verified 896/6/0
  after the warn-print fix. Second commit pending push in the same session.
  **Peer-reviewed 2026-09-15 - MiniMax** (Codex durably capped; a single thorough reviewer judged
  proportionate for a docs-only change, unlike C2/C5's dual-review bar for core algorithm code).
  No material objections - confirmed the rewritten Phase 1d/1f reads as a coherent, self-
  contained workflow for an agent following it cold, and that the C4 cross-reference (optional
  hint fields legacy-path-only) is honestly stated. One minor structural observation (the spot-
  check criteria sit in 1d, textually before the 1f command that produces the values it checks;
  the two sections cross-reference each other so the intent is reconstructable, but a future
  edit could relocate the spot-check paragraph to sit right after 1f's command block) - noted as
  a nice-to-have, not required before closing this out. Two behavioral claims about
  `stem_detector.py` (exact-filename-key matching, `hints_from_stem_result()`'s fallback
  guarantee) were flagged as unverifiable from the docs alone, consistent with this item's own
  stated "not independently re-validated at runtime" caveat above - not a new gap.
  Owner: Claude. Author: Claude. Peer review: SOUND - MiniMax, no findings required a text
  change.

- [x] **Surface vocal/density clash evidence to Sam as short suspect passages instead of leaving
  it as shadow-only logging** (D6) - vocal regions already gate loop-source selection, but the
  pipeline never compares both tracks' audible vocals ACROSS a transition, and outgoing density
  (the signal behind the entry-extension "chilled-out break" rule) is measured but never acted
  on. Don't introduce an unvalidated automatic penalty - produce a short list of suspect
  passages for Sam to listen to first.
  Evidence: `Source/align_engine.py:620,1770,1987` (Astra);
  `Documentation/Reviews/2026-08-27 Analysis Extraction Audit.md:98` (Astra).

  **BUILT + TESTED + VERIFIED, 2026-09-15.** Plan reviewed by MiniMax first - caught a real
  architectural error in the first draft (referenced `Alignment` field names that don't exist,
  picked the wrong existing pattern to mirror: C2's single boolean `outgoing_has_post_swap_
  content` instead of the list-shaped `landmark_candidates` two-step precedent). Corrected
  before any code was written. New `Alignment.vocal_regions_arrangement` + `report_vocal_
  regions()` in `align_engine.py` (computed once in `compute_aligned_positions`, right beside
  `landmark_candidates`, while `Track` objects - which carry `vocal_regions` - are still in
  scope); new `OverlapAnalysis.density_score`/`density_status`/`vocal_clash_ranges` +
  `_finalize_vocal_clash()` in `propose_arrangement.py` (intersects the raw evidence against the
  FINAL, post-loop overlap window, called once right after the file's own single
  `_refresh_overlap_geometry` call site). New standalone `Source/suspect_passages_report.py`
  (mirrors `seal_listening_test.py`/`record_bounce_manifest.py`'s precedent) - a short markdown
  list; transitions with nothing to flag are OMITTED entirely, not listed as clean, so the list
  stays worth reading. 20 new tests across `Tests/test_vocal_clash_detection.py` and
  `Tests/test_suspect_passages_report.py`. **Real-corpus validated, not just synthetic**:
  regenerated a real arrangement report for the staged Tech House Heldout project end to end
  (real `Sections V1.als` + real cached `_Stem Analysis`) and confirmed directly that all 9 real
  tracks carry genuine `vocal_regions` data (2-8 regions each) - so the result (zero vocal
  clashes across all 8 real transitions) is a validated true negative about this specific,
  well-arranged mix, not a missing-data artifact.
  Full suite 791/0/6 -> 811/0/6 (across both A6 and D6 this session).
  Files changed: `Source/align_engine.py`, `Source/propose_arrangement.py`,
  `Source/suspect_passages_report.py` (new), `Tests/test_vocal_clash_detection.py` (new),
  `Tests/test_suspect_passages_report.py` (new).
  **Peer-reviewed twice - MiniMax (plan, then code).** Code review independently re-verified
  every piece of the corrected architecture actually landed as specified (not just claimed),
  confirmed the bar->beat conversion math, the call-site ordering, and the clamp-then-intersect
  clash logic are all correct with no false-positive/negative paths. Two small, non-blocking
  findings, both adopted: (1) a null-vs-absent JSON key gap in the report script (`{"tracks":
  null}` is legal JSON that `.get(key, [])` doesn't catch) - fixed with `.get(key) or []` +
  regression test; (2) `vocal_clash_ranges` was reporting the CLAMPED vocal extent rather than
  its real full range for a vocal straddling the transition window edge - fixed to report the
  original unclamped range (the clamp only decides whether a clash is in play) + regression
  test. Explicitly confirmed a plain dict (not a new dataclass) for the clash-range leaf is the
  right call, consistent with this file's own precedent (`al.paired_cues` is the same
  list-of-dicts report-leaf shape).
  Owner: Claude. Author: Claude. Peer review: SOUND - MiniMax (plan + code, both rounds), both
  findings fixed and re-tested.

- [ ] **"Feasible" alignment pairs can still fail at the next stage (loop planning), and
  sequencing doesn't know that** (D7) - Astra's own corpus replay found 2 of 267
  alignment-successful historical pairs fail subsequent loop planning (e.g. Doorly -> Christoph,
  The Rise). The feasibility checker and the sequencer both only test alignment, leaving an
  avoidable reorder/retry for whoever's running the build.
  Evidence: `Source/alignment_feasibility.py:54`, `Source/align_engine.py:2009`,
  `Source/automated_dj_mixes/sequencer.py:192` (Astra).

  **INVESTIGATED + PARTIALLY BUILT, 2026-09-15 - honest negative result on the cited example, real
  improvement kept anyway.** `alignment_feasibility.py`'s `feasible()` now also calls
  `plan_fill_or_cut` after a successful `align_pair` and requires BOTH to succeed - closes the
  "loop planning raises an exception" failure mode the checker previously had zero visibility
  into. **Directly re-tested the item's own cited example (Doorly & Harry Choo Choo Romero ->
  Christoph - The Rise) before writing anything up, and it does NOT reproduce**: `plan_fill_or_cut`
  does not raise for this pair today - it prints a "[loop quality] no candidate survived" message
  and returns 0 specs (a graceful "no loop needed" outcome, not a failure). Ran the FULL 380-pair
  feasibility matrix with and without the fix: **267/267 feasible either way - zero pairs change
  verdict**. Whatever Astra's original "2 of 267" figure actually measured, it is not caught by
  this check as scoped (possibly only visible through the full `apply_automation`/
  `propose_arrangement` pipeline, not an isolated align+plan call - real, unconfirmed follow-up).
  Kept the code change anyway - it is a real, strictly-safer check with zero downside (a pair whose
  loop planning genuinely crashes will now correctly be flagged, even though none in the real
  corpus checked here currently do) - but NOT claimed as solving this item's own motivating
  example, which remains open.
  Files changed: `Source/alignment_feasibility.py`.
  **Peer-reviewed 2026-09-15 - MiniMax.** No material objections to the code change itself:
  confirmed `feasible()` genuinely requires BOTH stages to succeed (line-verified), confirmed
  the broad `except Exception` doesn't newly over-catch (BaseException still propagates,
  unchanged from before), confirmed the "does-not-reproduce" claim is plausible from the code
  (`plan_fill_or_cut` returning `[]` is a normal return, not an exception). Verified the test
  suite's honesty directly: 3 of 4 tests genuinely test the new wiring (the
  align-succeeds-but-plan-raises monkeypatch test is "the real test of the fix" - would catch
  the `plan_fill_or_cut` call being accidentally removed); the 4th (the 380-pair corpus
  regression) is confirmed to be an honest COUNT-PIN, not a fix-tester - it would silently pass
  even if the new call were deleted, since no pair in this corpus currently makes
  `plan_fill_or_cut` raise, and the test's own docstring already says so. **Left open, not
  checked off**: the code addition is sound, but this item's own motivating problem (loop
  planning failing for feasible-alignment pairs) remains genuinely unaddressed since it doesn't
  reproduce as originally cited - a SOUND review of a partial, honestly-scoped improvement is
  not the same as the item being solved.

  **NEW EVIDENCE, 2026-09-22 (found while working D9, folded in per Sam's "yes, fold it into
  D7").** D9's 380-pair replay under the new `rescue,deep,phrase` default corpus surfaced a REAL
  instance of this item's exact phenomenon: `Ritmo Da Rua - Harry Romero Remix 24 Bit MASTER` ->
  `Christoph - The Rise 16 Bit MASTER` aligns successfully (`alignment_policy:
  tail_anchor_rescue_v1`) but its `plan_fill_or_cut` stage raises (`"Cannot plan outgoing tail
  loop ... cue 'section:break_1' would end 2..."`), confirmed directly against
  `Documentation/Plans/burn-list-2026-09-13/d9_replay_result.json` and by `feasible()` itself
  returning `False` for the pair (see `Tests/test_alignment_feasibility.py`'s 353 constant,
  which already counts this exact pair as the one exclusion). This is a DIFFERENT pair from the
  item's original citation (Doorly -> Christoph - The Rise), but the SAME target track
  (Christoph - The Rise) and the SAME failure shape (outgoing tail loop can't reach its target
  cue before the locked swap) - so the phenomenon this item describes is confirmed real in the
  current corpus, just previously mis-cited. Whether the sequencer/proposer should now actively
  consult `feasible()` (which already detects this) before committing to a pairing, rather than
  only reporting it, is the remaining open question - not yet built.
  Owner: Claude. Author: Claude. Peer review: SOUND - MiniMax (reviewing the code change as
  built; the item's own underlying problem stays open).

- [ ] **Playlist-complete recovery for borderline-beatgrid tracks is never auto-attempted**
  (D8) - `refit_grid_from_stem.py` is the documented escalation path for exactly the kind of
  near-miss that excluded Arielle Free/Idris Elba from this held-out set (16ms vs a 15ms gate),
  but nothing tries it automatically before excluding a track. In commissioned work this is
  either a track Sam mixes in by hand, or a client conversation that shouldn't be necessary.
  Evidence: held-out `REVIEW_A.md` (Fable); carded 2026-07-16.

  **INVESTIGATED, 2026-09-15 - NOT BUILT, needs a peer-reviewed plan first.** Confirmed directly:
  `refit_grid_from_stem.py` has zero automatic call sites anywhere in the live pipeline
  (grepped) - `enforce_beatgrid_quality` (`Source/validate_beatgrid.py:376`) only ever READS an
  already-written `<project>/Hints/grid_overrides.json`, it never invokes the refit script
  itself. So the structural gap this item names is real. But the item's own motivating example
  is murkier than it first reads: the FAIL message text quoted in `REVIEW_A.md` ("stem grid is
  16ms OFF its own kicks... out of its 4-to-floor range") is generated ONLY by the
  `stem_fitted=True` branch of `verdict_from`, which requires a `grid_overrides.json` entry with
  `phase_source: "drum-stem-kicks"` to exist for that track - meaning a refit was ALREADY
  attempted (by a human, at some point) for this to have fired at all. Checked directly: no
  `grid_overrides.json` exists anywhere under the held-out project today - either it existed
  when `REVIEW_A.md` was generated and has since been cleaned up (it's gitignored, so no history
  to check), or the message text was paraphrased rather than a literal terminal capture. Either
  way, it's unconfirmed whether AUTO-attempting the refit would have rescued this specific
  track, since the FAIL message's own "out of its 4-to-floor range" framing suggests a
  structural rhythm mismatch (Afro/Latin-influenced) that a same-algorithm retry likely
  reproduces rather than fixes. The general automation gap stands regardless of this one
  example. Did not build against this ambiguity - this touches a safety-relevant gate with a
  documented past incident (the "09.06.26 Todd bug" drift case cited in the gate's own
  RuntimeError text), which per this project's own `/codex-review` standing trigger deserves a
  peer-reviewed plan before code, not solo blind building. No peer was free this session (Codex
  durably capped, MiniMax mid-review on E3) - queued for next peer availability rather than
  guessed at.
  Owner: Claude. Peer review: NONE - not yet reviewed (investigation only, no code written).

  **PLAN WRITTEN, 2026-09-22 - NOT BUILT.** Sam said "go ahead"; per this item's own explicit
  gate (a peer-reviewed plan before code, not solo blind building, on a safety-relevant gate
  with a documented past incident), the correct way to "go ahead" is to write and get that plan
  reviewed, not to skip the gate. Full design at
  `Documentation/Plans/d8-auto-refit-plan.md`: extracts `refit_grid_from_stem.py`'s core into an
  importable, testable function; wires ONE bounded auto-attempt into
  `enforce_beatgrid_quality`, skipping any track already stem-kick-fitted (structurally
  prevents a retry loop); critically, does NOT trust the refit tool's own internal
  inliers/iqr/med thresholds alone - re-runs the SAME full `check_grid` verification that
  caught the original failure before accepting the refit, so a same-algorithm retry that
  reproduces the original failure on structurally-mismatched material (the Afro/Latin concern
  the 2026-09-15 investigation flagged) still hard-stops exactly as today, just with a genuine
  attempt on the record. Proposes settling the 2026-09-15 investigation's own open question
  (does auto-refit actually help the real cited example) with a real-data test against the
  actual held-out project, not just mocks. Dispatched for design review (MiniMax) alongside
  C4's plan - pending.
  Owner: Claude. Status: OPEN - plan revised after a real BLOCKER was found and fixed (see
  below); needs a re-review of the revised plan before any code lands. The gate's existing
  behaviour (`--allow-bad-grids` as the human override) is completely unchanged in the meantime.
  Touched: 2026-09-22.

  **REVIEWED, 2026-09-22 - MiniMax found a real BLOCKER, independently confirmed and FIXED in
  the plan (not yet re-reviewed).** The plan's central safety claim ("re-run the full gate
  before accepting a refit") was theater as drafted: `check_grid`'s `stem_fitted=True` branch
  (`validate_beatgrid.py:309-321`) only FAILs when `stem_kf_ms is not None and stem_kf_ms >
  15.0` - when `stem_kf_ms` is `None` (which it always would be, since
  `refit_grid_from_stem.py`'s override dict has never included a `grid_vs_kick_ms` key), the
  re-run would unconditionally PASS regardless of the refit's actual quality. Verified directly
  against the real code myself, not taken on the review's word. **This is a pre-existing gap in
  the already-shipped `refit_grid_from_stem.py`, not something this plan introduced** - every
  manually-run refit in this project's history has had the same unconditional-PASS exposure.
  Fix folded into the plan: `attempt_stem_refit` now also writes `grid_vs_kick_ms` (from the fit's
  own already-computed median residual - no new computation) into the override, closing the gap
  for both the new auto-attempt path AND retroactively for the existing manual CLI path. Three
  further MINOR notes folded in: the never-retry guard is defensive-only, not load-bearing (a
  track with an existing override never reaches `fails` to begin with); exception propagation
  from a raised `attempt_stem_refit` needs stating explicitly; the "0 changed pairs" corpus claim
  needs to be an explicit assertion, not prose. Full detail:
  `Documentation/Plans/d8-auto-refit-plan.md`'s "REVISION 2026-09-22" section.
  Peer review: CORRECTION (adopted, plan revised) - MiniMax
  `Receipts/2026-09-22/minimax-review-d8-c4-plans.md`. The REVISED plan has not yet had its own
  review pass - do that before writing code.

- [x] **The built fixes for long intros and short outros are switched off in the standard
  `/mix` run** (D9) - Sam's long-intro rule ("count back from the first cue", a swap around the
  one-minute mark; cue signals `deep` and `phrase`) and his short-outro rule ("run back 16 bars
  from the last beat"; cue signal `rescue`, the tail-anchor rescue) are built, tested and merged,
  but both are opt-in `--cue-signals` flags and the `/mix` Phase 2a command passes none. So a
  standard run hard-raises "No paired section/dropout alignment" on a short-outro into
  long-intro pair. Sam, 2026-09-15: "this problem shouldn't exist... i thought this was already
  fixed?", then "there is one for the outro as well so add to the burn list to check about both".
  Found live on the 15.09.26 August Releases Mix, T5 Pat Premier -> Tommy Farrow: the default
  raised, `rescue` alone and `rescue,deep` still raised, and `rescue,deep,phrase` aligned it -
  swap at Tommy Farrow bar 31 (1:00.0, his kick dropout) against Pat Premier bar 79 of 84, a
  36-bar overlap. A per-pair align_pair comparison showed the other 10 transitions identical to
  the default. `deep` correctly stood aside here (it only fires when the first real intro cue is
  past bar 32, and Tommy Farrow has one at bar 31). Pat Premier's own outro-loop candidates all
  failed the loop-quality checks (silence_fraction, insert_level_match, worst_beat_dip - consistent
  with its hard ending), so an outgoing loop was not available on this pair either. The check,
  both halves: (1) replay the 380-pair alignment baseline corpus under `rescue,deep,phrase` and
  count identical / rescued / newly-raising / changed pairs - the code only tries these anchors
  after the normal drop-anchor search finds nothing, so zero changed pairs is the expected result
  and anything else is a finding; (2) if clean, Sam decides whether they become the CueConfig
  default or go into the Phase 2a command in both brains' `mix.md` (frozen sync list).
  Deliberately out of scope: `matched` (it runs before the normal search, so it can change
  transitions that already work), `introloop` (A5: validated, not promoted), `fills`, `bassout`.
  Evidence from Sam's own tweaks, 2026-09-15: he kept the T5 swap exactly where `phrase` put it
  (Tommy Farrow's bar-31 dropout on Pat Premier's outro start) while changing everything around
  it - `Documentation/Mix Patterns Library/15.09.26 August Releases Mix Sam Tweaks.md`.
  Evidence: `Source/align_engine.py:1084` `Source/align_engine.py:1370` `Source/propose_arrangement.py:2123`

  **REPLAY DONE, 2026-09-22 - CLEAN.** Part 1 of the item's own two-part check. New tool
  `Tools/d9_cue_signal_replay.py` (standalone, mirrors `Tests/test_alignment_baseline.py`'s row
  shape so the diff is apples-to-apples) sweeps every ordered pair of the 380-pair 14.08.26
  corpus twice: once with a fresh default `CueConfig()` - cross-checked byte-identical against
  the frozen `baseline_alignments.json` (267 ok/113 raise), confirming corpus and baseline agree
  before trusting the diff - then once with `CueConfig(tail_anchor_rescue=True,
  deep_intro_anchor=True, incoming_phrase_anchors=True)`, i.e. `rescue,deep,phrase` together, the
  exact combination the standard `/mix` Phase 2a command never passes. Diffed on the full pinned
  field set (`handoff_bar_out`, `arr_offset_bars`, `overlap_bars`, `swap_progress`,
  `handoff_kind`, `alignment_policy`, `paired_cues`, `notes`, `overlap_policy`, `plan`, ...).
  Result: **0 changed** (no already-OK pair moved), **0 newly-raise** (no OK pair broken),
  **87 of the 113 default-raise pairs newly align (77%)**, 26 still raise. This is the expected
  clean result the item predicted: `deep`/`phrase` anchors only enter `_align_pair_landmark_aware`
  via the `rescue_anchors` fallback, tried strictly after the normal drop-anchor search returns
  `None`, and `tail_anchor_rescue` is the last resort after that - so the combination can only
  turn a raise into an align, never move or break a pair the default already handles, and the
  full corpus now confirms that empirically rather than by code-reading alone. Full row-level
  detail (all 87 rescues with `handoff_bar_out`/`overlap_bars`/`anchor_bar_in`/`n_paired_cues`,
  complete default + combined row sets): `Documentation/Plans/burn-list-2026-09-13/d9_replay_result.json`.
  Command: `PYTHONPATH=Source python Tools/d9_cue_signal_replay.py --out
  Documentation/Plans/burn-list-2026-09-13/d9_replay_result.json`.
  **PART 2 DECIDED + SHIPPED, 2026-09-22.** Sam: "make it the default." `Source/align_engine.py`'s
  `CueConfig` field defaults for `tail_anchor_rescue`/`deep_intro_anchor`/`incoming_phrase_anchors`
  flipped `False` -> `True` (class + field docstrings updated to explain why, citing this item).
  Since the standard `/mix` Phase 2a command passes no `--cue-signals`, every future mix picks
  this up automatically - no Phase 2a wiring change needed in either brain's `mix.md`.
  Flipping the default broke 9 tests across 5 files, each genuinely investigated and fixed (not
  papered over): `Tests/test_alignment_baseline.py`'s frozen 380-pair baseline deliberately
  refreshed (267 ok/113 raise -> 354 ok/26 raise, exactly matching the replay above); its
  now-vacuous "rescue-flag plan layer" tier (`compute_rescue_rows`/
  `test_rescue_plan_matches_baseline`, guarding a 2026-08-20 flag-leak bug) retired, since
  "default" and "rescue-on" are now the same CUE_CONFIG state - the flag-leak class it guarded is
  independently confirmed still covered by `Tests/test_codex_blocker_fixes.py`'s Fix 3 section
  (verified by hand-tracing `plan_fill_or_cut`'s landmark-mode classification, which never reads
  live `CUE_CONFIG`, only the static `alignment_policy` value). `Tests/test_intro_phrase_swaps.py`
  had a `SimpleNamespace` test fixture missing `n_bars`/`musical_landmarks`, newly needed once
  the default-True path unconditionally reaches `_mix_cues` - pure fixture-completeness fix, zero
  assertion logic changed (independently re-derived and confirmed by both peer reviews below).
  `Tests/test_alignment_feasibility.py`'s pinned real-corpus constant moved 267 -> 353 (one of the
  87 newly-rescued pairs aligns but its `plan_fill_or_cut` still raises - named pair, traced
  arithmetic, confirmed against `d9_replay_result.json`). `Tests/test_policy_threading.py` gained
  an autouse fixture isolating `CUE_CONFIG` to the pre-D9 all-off state for that whole file (tests
  an orthogonal concern - policy-cap threading - and one test broke because a rescue tier could
  now satisfy a deliberately-tightened test policy the primary search couldn't; reproduced by hand
  outside the isolation fixture to confirm it was necessary, not just convenient).
  `Tests/test_tail_anchor_rescue.py` had one test renamed + reworked to state the new reality
  honestly (D9 changed the DEFAULT, not the underlying legacy search path) rather than claim a
  now-false "flag defaults OFF" premise. Full suite: 874 passed, 6 skipped, 0 failed.
  Author: Claude. Owner: Claude. Status: DONE 2026-09-22.
  Peer review: SOUND - MiniMax (`Receipts/2026-09-22/minimax-review-d9-default-flip.md` +
  `-retry.md`; both dispatches crashed before writing their own helper completion trailer, so
  neither is a machine-verified receipt in this skill's strict sense, but both independently
  produced complete, well-formed, mutually-consistent reviews ending in the required terminator,
  and the retry caught a real stale-comment nit the first pass missed - corroborating rather than
  contradicting each other) + SOUND - Claude subagent standing in for Codex (durably capped today,
  confirmed live via chatgpt.com/#settings/Usage: 0% weekly left, resets ~2026-09-26) -
  independently hand-traced the code (not just read the claims), reproduced the
  `test_policy_threading.py` failure outside the isolation fixture to confirm it was real, and
  returned one genuine CORRECTION (this item's own text was stale - said "no production code
  changed" and "Sam's call" pending after both had already happened; fixed by this rewrite) plus
  two MINOR non-blocking nits (a stale `# every flag off` comment, fixed same session; a few other
  `CueConfig(incoming_intro_loop=True)` call sites now also silently carry the 3 D9 flags True -
  harmless today, confirmed by exhaustive grep that nothing they exercise reads those flags, left
  as-is per Sam's "don't over-engineer" standing preference).

- [x] **A6's sample-path rewrite wrote a bare & into the final ALS, so any track with & in its
  filename made the whole set unloadable** (D10) - DONE 2026-09-15, uncommitted. The rewrite now
  fully re-escapes the values it writes and writes an absolute Path.
  Found live on the 15.09.26 August Releases Mix: Phase 3 wrote `In-Key Mix V2.als` with 20
  invalid lines (every RUZE & Chesster FileRef) and its own ALS check refused it. Cause: the A6
  rewrite in apply_automation.py unescapes RelativePath/Path to look the file up, then re-escaped
  only apostrophes on the way out. Second defect in the same function: the Path it wrote was
  relative whenever the script ran with relative project paths, which is how /mix runs it.
  The fix applies html.escape plus both quotes, keeping apostrophes as &apos;. It uses absolute()
  rather than resolve(), so a Dropbox junction is not swapped for its target. Two regression tests:
  the exact case against the committed code gives an XML parse error and a relative Path, and
  against the fix it parses with an absolute Path. The rebuilt V2 passed strict validate_als and
  the 89-check MixPlan reconciliation.
  Reviews: MiniMax returned SOUND (the independent review, receipt below). A Claude subagent
  standing in for Codex (capped until 2026-09-19) also returned SOUND, with 290 tests passing:
  `Documentation/Plans/burn-list-2026-09-15/claude-review-D10-D11.md`. The reviewed diff is
  byte-identical to the current code (sha256 59a8eac9f8cb).
  Author: Claude. Evidence: `Source/apply_automation.py:1337` sha256:76621e97a991
  `Tests/test_sample_ref_paths.py:321` `Receipts/2026-09-15/receipt-D10-a1.json`
  Owner: Claude. Status: DONE 2026-09-15. Touched: 2026-09-15.
  Peer review: SOUND - MiniMax `Receipts/2026-09-15/minimax-review-D10-D11.md`

- [x] **Tracks with "Audio" in their title were silently dropped from the hint check and the
  transition review** (D11) - DONE 2026-09-15, uncommitted. The substring filter is gone from all
  three scripts, and a regression test pins it.
  validate_hints_vs_sections.py, extract_sections_als.py and transition_review_viz.py each skipped
  any track whose name contains "Audio", a filter meant for empty template tracks. Tommy Farrow's
  real title ("New Audio 27.07.26 Extended MIx") tripped it on the 15.09.26 August Releases Mix.
  The hint check failed with "no matching sections track", the extraction summary printed 11 of
  12 tracks, and the Phase 4 review drew 10 of 11 transitions (Pat Premier straight into Arielle
  Free).
  The filter was never load-bearing. parse_sections_als only keeps tracks that have clips, so the
  remaining no-clips checks do the real job, and extract_sections_als's filter only ever touched
  the console summary. Hint check re-run: 46 of 46 rows. The Phase 4 review now draws 11 of 11
  transitions.
  The regression test was added after both reviews, on the Claude reviewer's suggestion. It fails
  on the committed code and passes on the fix, and the reviewed production code is unchanged.
  transition_review_viz.py's filter sits inline in main() with no unit test; the real-project
  re-render covers it. The same filter in diff_sections.py, sections_blind_viz.py,
  sections_compare_viz.py and validate_sections_review.py is off the /mix path and left alone.
  Author: Claude. Evidence: `Source/validate_hints_vs_sections.py:142` sha256:6ff1ee98d946
  `Source/extract_sections_als.py:148` sha256:2d6c99824b72 `Source/transition_review_viz.py:369`
  sha256:1b95d837d8c3 `Tests/test_validate_hints_vs_sections.py:107`
  `Receipts/2026-09-15/receipt-D11-a1-validate_hints_vs_sections.json`
  `Receipts/2026-09-15/receipt-D11-a1-extract_sections_als.json`
  `Receipts/2026-09-15/receipt-D11-a1-transition_review_viz.json`
  Owner: Claude. Status: DONE 2026-09-15. Touched: 2026-09-15.
  Peer review: SOUND - MiniMax `Receipts/2026-09-15/minimax-review-D10-D11.md`

- [x] **The correction learner read automation only, so Sam's geometry edits went
  unclassified** (D12) - DONE 2026-09-15. `learn_from_correction.py` labelled 5 of the 11 corrections on the
  15.09.26 August Releases Mix (3 `bass_swap_moved`, 2 `sneak_changed`); the other 6 got an empty
  `corrections` list although every one changed the arrangement. What Sam did there is geometry:
  entries pulled later by 1-16 bars (never earlier), both intro front-trims reversed, both 64-bar
  blends halved, loops of other material removed and loops of the outgoing's own last bars added
  or extended, outros cut, one swap moved onto the incoming's bass-in, one swap dropped for a
  crossfade. One of the three `bass_swap_moved` labels was wrong: the swap finder works in
  source-audio beats, so at T6, where the pipeline's swap sat on a tail loop cut from Tommy
  Farrow's intro (source beat 0), the real swap fell outside its window and it reported the
  track's last automation point instead ("-64 beats" for a swap that had not moved). T9's "+56
  beats" was right in Switch Disco's own bars (a 4-bar entry shift plus a 10-bar cut).
  **Built 2026-09-15 by Claude, uncommitted, awaiting review.** A geometry layer beside the
  automation diff (`_repeat_groups`, `_geometry_diff` and friends in
  `Source/learn_from_correction.py`): it reads clip geometry from both ALS files and emits
  `entry_moved_out`, `intro_trim`, `swap_moved_in`, `swap_moved_out`, `swap_removed`/`swap_added`,
  `tail_loop_added`/`_removed`/`_changed` (also `intro_loop_*`), `outro_cut` and
  `overlap_changed`, each in the affected track's own bars. Loops are found by shape (a clip that
  goes backwards in source and replays played material), so Sam's hand-made loops count too, in
  ARRANGEMENT_REPORT's "8bx7+0b" notation. The old `bass_swap_moved` label and the report's
  "swap moved" line are withheld when the swap sits on a loop clip on either side; the field
  itself is unchanged and a `bass_swap_reliable` flag says when to trust it. Each pair_history
  entry now carries a `geometry` record. On the real pair it labels all 11, matching the hand
  analysis transition for transition; the 11 corpus entries were replaced with the rebuilt ones.
  `Tests/test_learn_geometry_corrections.py` (15 tests: loop detection on the pipeline, hand-made,
  cut, gap-bridge, tail-repeat and intro-loop shapes; the T2, T4, T6, T7 and T9 transitions; the
  reviewer's ramp-then-loop case; an unchanged one). Proved against the committed learner by
  running the T6 fixture through it: no corrections at all and no geometry field, so the test
  cannot pass there. The 2 existing learner test files still pass.
  **Reviews (2026-09-15):** MiniMax SOUND on the first build (`Receipts/2026-09-15/
  minimax-review-D12.md`, three notes: the test count was wrong in the docs, the prove-the-test
  wording was loose, the report prints only the main geometry fields). The Claude stand-in for
  capped Codex returned CORRECTION with two reproduced defects, both applied the same evening:
  (1) the reliability flag was judged on a second swap finder (`_find_swap_arr`) and could
  certify a point the delta was not built from - now `_pinned_swap_event` reproduces the
  source-space finder's own selection step for step, `_find_swap_arr` requires a falling edge
  (a ramp point on the way up is not a kill), and a side is reliable only when both land on the
  same event on a section clip; (2) `_repeat_groups` checked "already played" against a min-max
  span, so a one-off clip from inside a skipped gap read as a loop - now the union of played
  intervals, and a single copy counts only when it repeats the previous clip's own tail. Real-pair
  labels unchanged by either correction. Confirmation on the corrected hash (sha256 33c07cf54058,
  commit 66467db): the Claude stand-in re-ran its own two counter-example scripts against the
  corrected code and returned SOUND (`Documentation/Plans/burn-list-2026-09-15/
  claude-review-D12-confirmation.md`; suite 830 passed, real-pair labels byte-identical before
  and after). Deliverable receipt on that hash: `Receipts/2026-09-15/receipt-D12-a2.json`,
  ACCEPTED, 45 learner tests passed. MiniMax's first SOUND was bound to the FIRST build's hash,
  so a re-confirmation on 33c07cf54058 was dispatched at 18:35 (`review-brief-D12-confirm.md`)
  and returned SOUND at 19:05 (`Receipts/2026-09-15/minimax-review-D12-confirm.md`): traced
  `_pinned_swap_event` against `_find_bass_swap_beat` step for step, the falling-edge rule on
  T6 and T9, the union check on the three new loop tests, and the 12 pre-existing record keys
  (names, order and meaning unchanged). Two MINOR notes, both already covered by the reliability
  gate (a `None` swap reads as unreliable), recorded as E7 rather than reopened here. Found
  while running the full suite: C7's canonicaliser
  (`Source/canonicalize_pair_history.py`) reports the new T4 record as malformed because Sam's
  version has no bass swap (`sam_bass_swap_beat` null, `swap_removed` in its corrections) and it
  requires both swap beats to derive a delta. It has no observation class for a removed swap.
  The corpus pin in `Tests/test_canonicalize_pair_history.py` was re-verified by hand (45
  records, 44 load, 35 unique, the same 4 conflicts) and updated to say so; the canonicaliser is
  untouched - giving it a swap-removed class belongs to C7.
  Author: Claude. Evidence: `Source/learn_from_correction.py:551` sha256:33c07cf54058
  `Tests/test_learn_geometry_corrections.py` `Receipts/2026-09-15/receipt-D12-a2.json`
  `Documentation/Mix Patterns Library/15.09.26 August Releases Mix Sam Tweaks.md`
  `Documentation/Mix Patterns Library/15.09.26 August Releases Mix Sam Tweaks.diff.json`
  Owner: Claude. Status: DONE 2026-09-15 - feeds C7 (a swap-removed observation class for the
  canonicaliser). Touched: 2026-09-15.
  Peer review: SOUND - MiniMax `Receipts/2026-09-15/minimax-review-D12-confirm.md` (on the
  corrected hash; first-build SOUND in `Receipts/2026-09-15/minimax-review-D12.md`; Claude
  stand-in CORRECTION then SOUND in `Documentation/Plans/burn-list-2026-09-15/`)

- [x] **Claude-arranged mode: the pipeline executes a written per-transition decision instead
  of the anchor search** (D13) - Sam, 2026-09-15, after asking why the pipeline is rule-based
  when Claude's judgement could arrange directly: "yeah do that, re-run this one in
  Claude-arranged mode". Built the same evening: `propose_arrangement.py --decisions FILE`
  (`align_engine.alignment_from_decision` / `fills_from_decision`, policy `claude_decisions_v1`,
  a member of `LANDMARK_POLICIES` so clip splitting, the paired_boundary gate and the
  automation margin rule all apply). A decision names the outgoing bar the incoming enters on,
  its intro trim, the incoming bar that takes the bass, and optionally a tail loop of the
  outgoing's own bars (allowed on a track with no outro), a front cut of a named outgoing clip
  (`apply_loops.cut_named_clip_front_and_pull`, new) or a middle skip of its outro that keeps
  the ending. The decisions for the 15.09.26 August Releases Mix are in that project's
  `Hints/arrangement_decisions.json` (11 transitions, each with its reason and the rule it
  follows); they were written after Sam's tweaks were analysed, so the run tests whether the
  pipeline can EXECUTE a decided arrangement and how the decision vocabulary falls short of
  what Sam does by hand (levels, the no-EQ-swap crossfade at T4), not whether Claude guesses
  Sam blind. Phase 2 ran clean: all 11 overlaps ok, 4 loops through the quality gate, front
  cut and outro skip applied, `Output/In-Key Mix V3 Claude Arranged.als` passes validate_als;
  every overlap within 1-2 bars of Sam's (48/24/48/23/20/32/16/32/34/29/31 vs Sam's
  50/24/48/23/20/31/15/34/34/29/32). `Tests/test_arrangement_decisions.py` (11 tests). Full
  suite: 841 passed, 6 skipped, 2 failed - both `test_alignment_baseline.py` rows whose only
  difference was the new `keep_end_bars: 0.0` key in every serialised FillCutSpec (proved: the
  refreshed baseline's git diff is 142 added `keep_end_bars` lines and nothing else); baseline
  refreshed per its own procedure, then 3 of 3 baseline tests pass.
  **UPDATE 2026-09-16 [Claude]: Phase 3 + scoring + docs done.** `apply_automation.py` on
  `In-Key Mix V3 Claude Arranged.als` -> `In-Key Mix V4 Claude Arranged.als` (own
  `ARRANGEMENT_REPORT`/`MIX_PLAN` paths, V2's files untouched): exit 0, in-process MixPlan
  reconciliation PASS. `validate_als.py` strict PASS. Standalone `validate_mix_plan_als.py`
  87/87 checks PASS. Scored against `In-Key Mix V2 SW Tweaks.als` with
  `learn_from_correction.py --dry-run --bpm 127.16`: **10 of 11 transitions land within 1 bar
  of Sam's hand geometry** (3 exact-to-the-beat: T2, T3, T10 with zero classified corrections,
  plus T1/T5/T6/T8/T9/T11 exact bars with only tail-loop-shape or sneak-level deltas). Two real
  vocabulary gaps found: **T4** - the schema has no "no EQ swap" case, so Sam's genuine no-swap
  crossfade (bass eases in gradually, no handoff point) gets approximated as a swap at the
  intro phrase point (rule R6's own documented fallback); **T7** - entry/swap off by 1 bar each
  and sneak level far off (0.10 decided vs Sam's 0.53) - the biggest miss, likely from levels not
  being decidable at all (see below). Full write-up:
  `Documentation/Mix Patterns Library/15.09.26 August Releases Mix Claude Arranged vs Sam
  Tweaks.md`, decisions file copied alongside it. `--decisions` now documented in `/mix` (new
  2a.5 section, both brain copies, `diff -w -B` clean).
  **What it cannot express, confirmed by the score:** (1) levels - sneak point and every
  volume/bass automation value stay `apply_automation.py`'s own rules, not decidable in the
  decision schema; (2) a genuine no-EQ-swap crossfade (T4) - the schema only has a swap
  *position*, never "no swap at all".
  Evidence: `Source/align_engine.py:2453` `Source/propose_arrangement.py:896`
  `Source/apply_loops.py:1048` `Tests/test_arrangement_decisions.py`
  `Test Project/15.09.26 August Releases Mix/Output/{In-Key Mix V4 Claude Arranged.als,
  MIX_PLAN_RECONCILIATION Claude Arranged.json, phase3_claude_arranged_log.txt,
  phase3c_claude_arranged_vs_sam_tweaks_dryrun.txt}` (gitignored project folder)
  `Claude Code Brain/commands/mix.md` `Codex Brain/commands/mix.md`
  **UPDATE 2026-09-16 [Claude], later same day: reviewed, findings fixed, CHECKED OFF SOUND.**
  Dispatched to Codex (genuinely capped, confirmed live - resets 2026-09-19 11:48 AM, not a
  refusal) and MiniMax in parallel via `room_peer_review.ps1`; Codex's seat re-routed to a Claude
  subagent per the standing capped-seat rule. Both independently reviewed the code, the write-up
  and the mix.md doc against the raw dry-run output, and both independently caught the SAME two
  write-up errors: T5/T6/T9's sneak values had decided/Sam reversed, and T1's "(same total bars)"
  claim was false (14 vs 16 bars) - both fixed in the write-up. The subagent also flagged "5 of
  11 exact-to-the-beat" as unsupported by the raw data; corrected to 3 (T2/T3/T10, the only
  transitions with an empty corrections list) both here and in the write-up.
  Both also independently found the SAME real code gap: `alignment_from_decision` bounded the
  swap against the outgoing track's length but never the incoming's, and `fills_from_decision`'s
  `tail_loop` never bounded its source bars against the outgoing's length either - a malformed
  decision in either case would have silently produced a wrong arrangement instead of raising.
  Fixed in `Source/align_engine.py`; 2 new tests (`Tests/test_arrangement_decisions.py`, proved
  to fail against the pre-fix code via `git stash`); real 15.09.26 decisions file re-verified
  clean through Phase 2 + Phase 3 + both validation gates after the fix (byte-identical result);
  full suite 845 passed, 0 failed, 6 skipped (was 843/0/6 pre-fix, +2 new tests).
  MiniMax additionally flagged `swap_progress` as ungated in decision mode vs the anchor search's
  `MIN/MAX_SWAP_PROGRESS` (0.25-0.95), citing T5 at "0.97" - checked directly against
  `ARRANGEMENT_REPORT Claude Arranged.json` and T5 is actually 0.86, inside bounds (MiniMax's
  specific number was wrong). The general point still holds: T4 genuinely sits at
  `swap_progress == 1.0` (Zaro has no outro, ends cold) - gating this would have broken a real,
  intentional transition in the shipped mix, so documented as a deliberate design difference in
  mix.md rather than gated. mix.md also corrected: `tail_loop` is NOT no-outro-only (T1/T3 loop
  existing outros), and `out_cue` does not feed `handoff_kind` (only `swap_cue` does).
  Lower-priority findings NOT fixed this session, noted in mix.md instead: `_decision_names_match`
  accepts a loose 30-char prefix match; a duplicate/out-of-range `pair_index` is silently
  accepted/ignored; fractional bar values aren't rounded. None exercised by the real decisions
  file - real but not urgent, revisit if a future decisions file hits one.
  Receipts: `Receipts/2026-09-16/minimax-review-D13.md`, `Receipts/2026-09-16/claude-subagent-
  review-D13.md` (includes disposition).
  Owner: Claude. Status: DONE - built, scored, documented, reviewed by 2 independent lenses,
  both sets of findings fixed and re-verified.
  Touched: 2026-09-16.
  Peer review: MiniMax SOUND-with-corrections (adopted) + Claude subagent standing in for capped
  Codex SOUND-with-corrections (adopted), both independent, both converged on the same core
  findings.

## E - Hygiene / technical debt (does not affect output quality today)

- [ ] **Render-check has real blind spots on every production (tempo-arc) mix, currently
  invisible because the flat experimental builds are the only ones exercising the checks fully**
  (E1) - on a tempo arc, `boundary_click` skips every boundary by name and grid drift cannot fail
  the render at all. No click has ever been reported on a flat render either (the only case where
  the check actually runs), so this is untested territory on the exact check most likely to
  catch an audible defect.
  Evidence: `Source/render_check.py:2235,2440` (Astra); Master Board line 27 (Fable, prior
  carding).

  **INVESTIGATED, 2026-09-15 - real, but narrower than "untested."** Two things confirmed
  separately:
  1. **The detection logic itself IS validated** - `Tests/test_render_check.py:286`
     (`test_boundary_click_single_sample_step`) injects a real synthetic single-sample click at
     a boundary and asserts a FAIL, plus a negative control (a 5ms ramped hit at the same
     boundary must NOT trigger). So "does the check work" is answered: yes, on synthetic data.
  2. **What's actually true, confirmed against every RENDER_CHECK*.json on this machine (6
     artifacts: House 10 V3, Tech House Heldout A/B/C, 14.08.26 V10/V16)**: `check_boundary_click`
     only ever appends a Finding on FAIL (`Source/render_check.py:1429-1433`) - a clean pass and
     "never ran" are indistinguishable in the output. Zero `boundary_click` FAIL findings exist
     in any of the 6. On the one tempo-arc render in the set (House 10 V3), it's SKIPPED for
     101 of 101 boundaries (100%) - confirmed by reading the actual JSON, matching the code's own
     documented, deliberate, Codex-reviewed design (honest named SKIP rather than a false-clean
     PASS - the arc map's fit scatter, 5.3ms RMS, already exceeds the +/-2ms click window, so no
     boundary on an arc can currently be called trusted, not even early ones).
  **So the real gap is: zero observed real-world evidence (positive or negative) that this check
  has ever operated on actual production audio, and 100% zero coverage on every tempo-arc mix
  today** - not a code bug, and not "the check might not work." Closing the arc-coverage half for
  real would mean characterising the tempo map's actual per-boundary timing uncertainty beyond
  today's single mean-bias-plus-scatter fit (a real, scoped statistics task, not a quick patch) -
  left open rather than attempted in the time available this session.
  Owner: Claude. Peer review: NONE - not yet reviewed (investigation only, no code written).

- [x] **`AI_CONTEXT.md` and `/mix` have drifted from what the code actually does** (E2): "What's
  Next" still opens with 2026-09-10 and 2026-09-01 TOPs and carries May-era items;
  `seal_listening_test.py`'s documented CLI (two positional WAVs) doesn't match its real one
  (`--side LABEL=path`, repeatable, plus `--twin-of`/`--out-dir`/`--seed`); `/mix` says A/B, the
  code builds A/B/C; `/mix` says to commit the held-out project, `.gitignore` ignores
  `Test Project/`; two hint overrides are documented as closed features that are actually inert
  (see C4). Several May-2026 planning docs (`TOMORROW.md`, `TODO_ARRANGE_MIX.md`,
  `PIPELINE_AUDIT.md`, `CODEX_REVIEW.md`) still sit at the documentation root.
  Evidence: `Documentation/AI_CONTEXT.md:1290-1411` (Fable); `Claude Code Brain/commands/mix.md:427,439-446,458,585`
  (Fable + Astra).

  **ALL SIX SUB-CLAIMS ACCOUNTED FOR, 2026-09-15 - real ones fixed, others confirmed not
  reproduced, one already stale from natural file evolution.** Six distinct sub-claims, checked
  individually rather than assumed as one bundle:
  1. **"What's Next" opens with stale 2026-09-10/2026-09-01 TOPs" - already resolved by
     subsequent sessions' own normal work, not this one.** The file's own "TOP" convention rotates
     naturally (each session's real next-step supersedes the last, older ones marked
     "superseded" and kept for history per this project's standing practice) - by 2026-09-15 the
     live TOP note is dated today, not September 1/10. Not claiming credit for this.
  2. **`seal_listening_test.py`'s documented CLI mismatch - REAL, FIXED.** The example command in
     both `mix.md` copies still showed the pre-2026-09-10 two-positional-WAV form; the script's
     own docstring already had the correct `--side LABEL=path`/`--twin-of`/`--out-dir`/`--seed`
     usage (confirmed directly against the real `argparse` block) - mix.md just never caught up.
     Rewrote the example + added an explanatory note in both brains (`diff -w -B` clean after).
  3. **"`/mix` says A/B, the code builds A/B/C" - CONFIRMED NOT REPRODUCED, MiniMax full-file
     read, 2026-09-15.** The doc is internally consistent: `build_ab_comparison.py` builds
     exactly two sides (A/B), and every A/B/C mention in `mix.md` refers to `seal_listening_
     test.py`'s CLI supporting a THIRD side as an optional listen-time extension (repeated
     `--side` flags), not a claim the pipeline itself builds three. No contradiction found after
     reading the whole staged file, not just grepping for the cited phrase.
  4. **"`/mix` says to commit the held-out project, `.gitignore` ignores `Test Project/`" -
     CONFIRMED NOT REPRODUCED AS STATED, same MiniMax pass.** The doc does instruct committing
     "the held-out project" (generic phrasing) but never names `Test Project/` specifically and
     never mentions `.gitignore` at all - so the doc's own text carries no literal contradiction
     to point at. Whether the INSTRUCTION nonetheless conflicts with the real `.gitignore` entry
     (which does exclude `Test Project/`) is a separate question the doc alone can't settle -
     genuinely worth Sam's awareness (committing "the held-out project" today silently does
     nothing, since git ignores it), but not a documentation-accuracy defect to fix in mix.md's
     own text. **Followed up and confirmed real**: spun off as its own item, E6, below - the
     instruction is genuinely silently a no-op, just not in the exact shape this sub-claim
     originally named.
  5. **Two hint overrides documented as closed but actually inert - REAL, FIXED as part of C4
     and D5 this session** (see those items) - `intro_skip_bars`/`loop_source_sec` no longer
     claim CLOSED in either mix.md copy.
  6. **Stale May-2026 planning docs at the Documentation root - REAL, FIXED.** `TOMORROW.md`
     (2026-05-?), `TODO_ARRANGE_MIX.md` (dated 2026-05-20 in its own text), `PIPELINE_AUDIT.md`
     (dated 2026-05-22), `CODEX_REVIEW.md` (dated 2026-05-18) all confirmed genuinely superseded
     (references "V12" and pipeline phases completely rebuilt since) - `git mv`'d to a new
     `Documentation/Archive/` (git history preserved, matching this project's own established
     `Source/Archive/` precedent from the Rekordbox removal). Confirmed no live file references
     the old paths (only historical log/doc entries do, correctly left untouched per this
     project's "never edit history" convention).

  **Peer-reviewed 2026-09-15 - MiniMax.** No material objections to sub-claim 2's fix (the
  rewritten `seal_listening_test.py` example matches the real post-2026-09-10 argparse form, the
  explanatory note is self-contained and correct). Independently settled sub-claims 3 and 4 by
  reading the whole staged file rather than just grepping the cited phrase - both confirmed not
  reproduced as originally stated (see above); sub-claim 4's real substance was then followed up
  and spun off as E6. All six sub-claims are now accounted for one way or another - closing this
  out rather than leaving it open on sub-claims that turned out not to be documentation defects.
  Owner: Claude. Author: Claude. Peer review: SOUND - MiniMax, no findings required a further
  text change beyond what was already fixed.
  Files changed: `Claude Code Brain/commands/mix.md`, `Codex Brain/commands/mix.md`,
  `Documentation/Archive/TOMORROW.md` (moved), `Documentation/Archive/TODO_ARRANGE_MIX.md`
  (moved), `Documentation/Archive/PIPELINE_AUDIT.md` (moved), `Documentation/Archive/CODEX_REVIEW.md`
  (moved).
  Owner: Claude. Author: Claude. Peer review: NONE - not yet reviewed.

- [x] **`extract_sections_als.py`'s parser is silently broken by XML attribute reordering** (E3)
  - valid XML, but all clips vanish from its parsed result if `<AudioClip>`'s attributes are
  reordered, which would fall `apply_automation` back to the (potentially stale) sections JSON
  without any error. Found by Codex during this week's margin-fix review, logged not fixed at
  the time (commit 98efcbb) - carried here so it doesn't get lost.
  Evidence: `Source/extract_sections_als.py:44`.

  **BUILT + TESTED + REVIEWED, 2026-09-15.** `parse_sections_als`'s per-clip split changed from
  the fixed-order `re.split(r'<AudioClip Id="\d+" Time="', track_body)` to tag-name-only
  `re.split(r"<AudioClip ", track_body)`, with `Time` then read via a regex SCOPED to just the
  clip's own opening tag (`cb[:cb.find(">")]`) - the same attribute-order-independent pattern
  already used for CurrentEnd/LoopStart/LoopEnd/Name/Color a few lines below. Confirmed directly
  the dropped `Id` requirement was never consumed elsewhere in the function.
  5 new tests (`Tests/test_parse_sections_als.py`, this function had zero direct coverage
  before - confirmed `Tests/test_extract_sections_als.py` monkeypatches it out and
  `Tests/test_als_gate_hardening.py` only mentions it in a comment, never calls it): normal order,
  Time-before-Id (the exact bug), extra attributes between Id and Time, two clips surviving
  mixed ordering, and a scoping guard against a decoy `Time=` deeper in the clip body.
  **Proved-the-test**: hit a fixture bug first try (`<AudioTrack>` with no trailing space/attr
  doesn't match the - unchanged - track-splitting regex, so all 5 failed for the WRONG reason);
  fixed the fixture, re-ran: 3 of 5 genuinely fail pre-fix (`git stash` on the tracked file) for
  the bug's exact mechanism, 2 correctly pass either way (normal-order and tag-scoping aren't
  reordering-dependent). All 5 pass post-fix. Full suite: 774 passed, 6 skipped (was 769/6).
  **Peer-reviewed - MiniMax** (Codex still durably capped, resets 2026-09-19, so MiniMax ran
  solo as full reviewer per CLAUDE.md's "Codex/Kimi capped -> MiniMax stand-in" guidance):
  confirmed the fix genuinely order-independent and correct; flagged 2 theoretical-only
  fragilities (substring name-match could collide with a hypothetical `StartTime=`/`EndTime=`
  attribute that doesn't exist on AudioClip today; `>` inside a quoted attribute value would
  truncate the tag-scoping too early, same silent-skip shape as the original bug, no plausible
  real Ableton trigger) - both same-class-narrower-axis as the bug just fixed, neither a
  regression, both left as documented (comment added), not fixed. Gave an honest, accurate
  critique of test 5 (weak as a pure discriminator, valid as a future-regression guard - matches
  what the test's own docstring already claimed). Verdict: "No material objections. Ship it."
  Files changed: `Source/extract_sections_als.py`, `Tests/test_parse_sections_als.py` (new).
  Owner: Claude. Author: Claude. Peer review: SOUND - MiniMax (Codex-capped substitute, real
  staged files via room_peer_review.ps1, no findings required a code change beyond one
  documentation comment).

- [x] **6 skipped tests are explained, not defects - but the explanation itself points at a real
  gap** (E4): 4 skips are a missing June golden fixture, 2 are intentional non-applicable cases,
  no `xfail` markers found anywhere. Recovering the missing golden fixture would restore real
  regression coverage; the skips themselves are not 6 outstanding mix defects and should not be
  read as such.
  Evidence: `Tests/test_align_engine_golden.py:24`, `Tests/test_swap_selection_replay.py:64`
  (Astra).

  **INVESTIGATED, 2026-09-15 - GATED TO SAM.** The fixture is `Test Project/08.06.26 Mix/
  _Stem Analysis/SECTIONS_STEM_*.json` (validated 2026-06-09 against Sam's own hand-edited
  In-Key Mix V16, byte-for-byte). Confirmed directly: no `08.06.26 Mix` folder exists anywhere
  under this machine's `Test Project/` (it's gitignored, so not recoverable from git history
  either). A full background sweep of the F:/G: backup drives (per CLAUDE.md, this machine's
  "Master Back Up" archives) completed and found NOTHING - the only hit anywhere was one
  unrelated filename coincidence (a different project's audio file happens to contain
  "08.06.26" as a date stamp) - those drives back up Sam's mastering-business work folders, not
  this coding project's scratch/test fixtures, so the prior is low that they hold it.
  **What's actually needed: Sam's word on whether this fixture exists anywhere (another
  machine, an external drive) or whether it should be regenerated from scratch** - regenerating
  means knowing which 10 source tracks made up "08.06.26 Mix" and re-running stem separation +
  section detection on them (a real GPU/Demucs cost, not something to trigger speculatively).
  Not attempting either path without his input.

  **DECIDED, 2026-09-15 (Sam, asked directly): let it go, not worth chasing.** The 4 dependent
  skips (`Tests/test_align_engine_golden.py`, `Tests/test_swap_selection_replay.py`) are accepted
  as permanent - their own skip reasons already correctly describe why (missing fixture), so no
  code or message change needed. Closes the open question; the coverage gap itself is not
  recovered, by explicit choice.
  Owner: Sam. Peer review: n/a - Sam's own decision, nothing built.

- [x] **Stale artifacts sitting beside this week's rebuilt files** (E5): `Mix A_pre-fix-
  backup.als`, a 614MB stale `Mix A.wav` no RENDER_CHECK.md any longer describes, and
  RENDER_CHECK.md itself describing an artifact that's already been superseded twice this week.
  Cheap cleanup once A2 lands.
  Owner: Claude. Peer review: NONE - not yet reviewed.

  **PARTIALLY DONE, 2026-09-15.** The RENDER_CHECK.md-superseded half turned out to already be
  resolved organically - this week's A1-A5 work replaced the single `RENDER_CHECK.md` with
  per-side `RENDER_CHECK_A/B/C.md` + `.json` (dated 2026-09-14 11:26-12:10), and `RENDER_CHECK_A
  .json`'s own `"render"` field confirmed directly to point at the CURRENT `Tech House Heldout
  Mix A (14.09.26).wav` (617MB, the real A2 fresh-bounce output) - nothing stale left describing
  a superseded artifact. For the two literal stale files: confirmed neither is git-tracked
  (`Test Project/` is gitignored wholesale) and confirmed the old 614MB `10.09.26 Tech House
  Heldout Mix A.wav` (Sep 11, pre-A2) is referenced nowhere live - only in the historical
  `Documentation/Plans/burn-list-2026-09-13/fable-sweep-result.md` planning doc, correctly left
  untouched per this project's "never edit history" convention. Per this session's standing
  "prefer a reversible step over deleting" guidance, both stale files were MOVED (not
  permanently deleted) to `Test Project/10.09.26 Tech House Heldout/Output/_Stale Archive
  (E5)/` - `Mix A_pre-fix-backup.als` (752KB) and the 614MB WAV. This gets them out of the way
  of anyone (human or AI) reading the Output folder, without a one-way destructive delete on
  ~615MB of real rendered audio that isn't guaranteed byte-reproducible from a pipeline re-run.
  **DECIDED + DONE, 2026-09-15 (Sam, asked directly): delete them.** `_Stale Archive (E5)/`
  (both files, ~615MB total) permanently removed. Reclaimed the disk space.
  Owner: Claude. Peer review: NONE - not yet reviewed (pure file-move + doc-read, no code
  changed; judged proportionate to skip a peer round for this one).

- [x] **`/mix`'s "commit the held-out project" instruction is silently a no-op** (E6) - found
  while MiniMax was settling one of E2's sub-claims, 2026-09-15. `mix.md:491` says "Commit the
  held-out project itself (so the verification chain is reproducible)" with no qualification
  about which files - read as written, that means the whole `Test Project/<name>/` tree.
  Confirmed directly: `.gitignore:45` is a blanket `Test Project/` exclusion, no negation
  patterns anywhere for it. `git add` on a gitignored path does nothing and prints nothing (no
  `-f`) - so an operator following this instruction literally would see no error and believe the
  verification chain is now reproducible, when nothing was actually committed. Real, silent,
  exactly the failure class this project's whole validation culture exists to catch. Not fixed
  this session (found late, in the middle of finalizing other review dispatches) - the fix is
  either (a) scope the instruction down to the SPECIFIC small files that should be committed
  (the result doc, the plan doc - both already named in the same sentence and NOT under `Test
  Project/`) and drop "commit the held-out project itself", or (b) if the intent really is to
  version the held-out project's own small artifacts (hints, ALS, JSON reports - not the
  multi-hundred-MB audio), carve a narrower `.gitignore` exception for those specific file types
  under `Test Project/`. (b) is the bigger, riskier change (a `.gitignore` exception is easy to
  get wrong and could suddenly start tracking huge WAVs); (a) is a one-line doc edit. Needs Sam's
  read on which was actually intended before either is built.
  Evidence: `Claude Code Brain/commands/mix.md:491`, `.gitignore:45` (both confirmed directly).

  **DECIDED + DONE, 2026-09-15 (Sam, asked directly): narrow the doc instruction (option a).**
  Dropped "commit the held-out project itself" from both `Claude Code Brain/commands/mix.md` and
  `Codex Brain/commands/mix.md` - the sentence now only instructs committing the result doc next
  to its plan doc in `Documentation/Mix Patterns Library/` (both already outside the gitignored
  `Test Project/`), plus an explanatory note that the held-out project itself is never committed
  and why, so a future reader doesn't have to re-derive this. Both brains content-verified
  identical after edit (`diff -w -B` clean).
  Files changed: `Claude Code Brain/commands/mix.md`, `Codex Brain/commands/mix.md`.
  Owner: Claude. Peer review: NONE - not yet reviewed (one-line doc edit, Sam's own explicit
  decision on intent; judged proportionate to skip a peer round).

- [ ] **The learner's arrangement-frame swap finder can miss a kill at the edge of its window**
  (E7) - MiniMax, D12 re-confirmation, 2026-09-15, two MINOR notes on
  `learn_from_correction._find_swap_arr`: (1) a kill that is the FIRST point inside the +-10
  window has no `prev`, so the falling-edge rule cannot see it (falsifier: `out_bass=[(80,
  0.18)]`, window [80, 110]); (2) `prev` carries in from outside the window, so a point below
  0.8 before the window (`[(50, 0.5), (90, 0.18)]`) hides a real fall inside it. Both return
  `None`, and the reliability gate then marks the side unreliable (no `bass_swap_moved` label
  is published), so the failure mode is a withheld label, not a wrong one - the geometry labels
  still carry the swap move in the track's own bars. Fix when touched: seed `prev` from the last
  point BEFORE the window, and treat a first in-window point below 0.8 whose pre-window value
  was >= 0.8 as the edge. Pin both falsifiers as tests.
  Evidence: `Receipts/2026-09-15/minimax-review-D12-confirm.md` (Q2 and FOUND UNASKED)
  `Source/learn_from_correction.py:728`

  **INVESTIGATED, 2026-09-22 - NOT BUILT, a candidate fix has a real correctness risk this
  item's own prior summary didn't surface.** Re-read the ORIGINAL MiniMax review this item was
  carded from (`Receipts/2026-09-15/minimax-review-D12-confirm.md`, not just this item's own
  compressed paraphrase) to get the true window bounds: falsifier 1 is `out_bass=[(80, 0.18)]`
  in window **[70, 110]** (the item text above says "[80, 110]" - the window actually starts
  BEFORE the single point, not at it). Both MiniMax notes are the same root cause described
  twice: `prev` has no reliable in-window history the moment the loop reaches the window's own
  first point, whether because no point exists before it at all (falsifier 1) or because the
  nearest point before it is stale/already-low (falsifier 2, `[(50, 0.5), (90, 0.18)]`, window
  [80, 110]). A unifying candidate fix - track `prev` ONLY from points that are themselves
  inside the window (never seed or contaminate it from outside), and treat the window's own
  first point as a detected kill if it arrives already below 0.8 - resolves BOTH falsifiers
  identically (verified by hand-tracing against MiniMax's own confirmed-working case too,
  `(t, 1.0), (t, 0.18)`, unaffected). **But this candidate fix reintroduces the exact false-
  positive class this function's own docstring says a prior review (2026-09-15) deliberately
  ruled out: "a ramp point on the way UP is not a swap."** A track whose bass genuinely ramps
  UP starting inside the window (first in-window sample legitimately low, rising afterward -
  the docstring's own named "short outgoing still carries its own earlier bass-in ramp" case)
  would now be misread as a KILL at its very first low sample, trading a safe false-negative
  (today: a withheld label, independently confirmed by MiniMax to never be wrong) for a false
  positive (a WRONG label published with confidence). Distinguishing "genuinely killed on
  entry" from "ramping up on entry" from a single first-in-window point alone is not possible
  without either more context (a look-ahead at what follows, or the source-space corroboration
  `_pinned_swap_event` already does independently) or a design decision beyond what a solo
  session should make blind on a gate with a documented past false-positive concern - same
  standing bar as D8. Not fixed this session; the burn list's own prior "fix when touched"
  prescription should be read as unverified, not as settled guidance, until this ramp-vs-fall
  risk is resolved.
  Owner: Claude. Status: OPEN - real correctness risk found in the obvious fix; needs a
  peer-reviewed plan or Sam's call on whether look-ahead/source-space corroboration is worth
  building, not a blind patch. Touched: 2026-09-22.
  Peer review: NONE - not yet reviewed (investigation only, no code written; the risk itself
  was found by re-deriving from the original review source, not by a peer this session).

- [x] **Claude-arranged mode's decision schema (D13) has three unhardened edges, none exercised
  by a real decisions file yet** (E8) - found by MiniMax and a Claude subagent during D13's peer
  review, 2026-09-16, alongside the two real bugs already fixed there (swap_in_bar / tail_loop
  bounds, see D13's own entry). Lower confidence / lower priority, left open rather than fixed
  blind: (1) `_decision_names_match` (`align_engine.py:2467`) accepts a 30-character prefix
  match (`a.startswith(b[:30]) or b.startswith(a[:30])`) - two tracks sharing a long common
  prefix could silently match the wrong one; (2) a duplicate or out-of-range `pair_index` in the
  decisions file is silently accepted/ignored (`propose_arrangement.py:1492`,
  `{int(d["pair_index"]): d for d in items}` - a dict comprehension, so a repeated index just
  overwrites, and an index outside the real transition count is never referenced, never flagged);
  (3) fractional `entry_out_bar`/`swap_in_bar` propagate unrounded through
  `alignment_from_decision` into downstream bar/beat arithmetic - benign today, unverified
  whether it stays benign once a decision file is authored by something less careful than a
  by-hand review. Fix when a real decisions file actually hits one of these, or before this
  becomes a wider-used mode.
  Evidence: `Receipts/2026-09-16/minimax-review-D13.md`, `Receipts/2026-09-16/claude-subagent-
  review-D13.md`, `Source/align_engine.py:2467`, `Source/propose_arrangement.py:1492`.

  **BUILT, 2026-09-22 - all three edges hardened.** (1) `_decision_names_match` unchanged (still
  allows the 30-char prefix tolerance for minor cosmetic differences), but `alignment_from_decision`
  now also checks, for each of `out_track`/`in_track`, whether a non-exact match's `wanted` string
  would ALSO loosely match the OTHER track in the SAME pair (new `_decision_names_match_exactly`
  helper) - if so, raises "ambiguous" rather than silently picking one. Known, stated scope limit:
  this function only ever sees the two tracks already resolved for this pair, so it cannot detect
  ambiguity against a similarly-named track elsewhere in the project - a structural limit of its
  signature, not an oversight. (2) new `_validate_decision_pair_indices()` in
  `propose_arrangement.py` rejects a duplicate `pair_index` or one outside the real
  `1..len(tracks)-1` transition range, called before the (unchanged) dict comprehension that used
  to silently swallow both. (3) new `_decision_bar(value, field, pair)` helper in `align_engine.py`
  snaps a bar value within `1e-6` of a whole bar (genuine float/JSON round-trip noise) and rejects
  anything more fractional than that - applied at every bar-valued read site in both
  `alignment_from_decision` and `fills_from_decision` (entry_out_bar, intro_trim_bars,
  swap_in_bar, and the tail_loop/outgoing_cut/outro_skip sub-fields). 9 new tests in
  `Tests/test_arrangement_decisions.py`, all confirmed to fail against the pre-fix code
  (`git stash`) - including the two sanity checks (an unambiguous loose match still works, valid
  pair_indices still pass) confirmed to NOT fail pre-fix, proving they're not just tautologically
  strict. Full suite 889/6/0 (890/6/0 after an unrelated D4 fix landed the same session).
  Files changed: `Source/align_engine.py`, `Source/propose_arrangement.py`,
  `Tests/test_arrangement_decisions.py`.

  **REVIEWED, 2026-09-22 - SOUND from both.** MiniMax: SOUND on all three edges, one MINOR
  readability note (the duplicate-detection idiom in `_validate_decision_pair_indices` relied on
  `set.add()`'s falsy `None` return inside a comprehension - correct but a maintenance trap;
  rewritten as an explicit loop, same behaviour, confirmed by re-running the full test file).
  The independent Claude subagent traced the ambiguity check's scope directly against
  `compute_aligned_positions`'s real resolution order (`stems[resolved[k-1]], stems[resolved[k]]`,
  purely positional, never by name) and confirmed the in-pair check is exactly where the real
  risk lives - cross-mix ambiguity outside the current pair is a structural non-issue, not a gap
  this fix should have covered. Grepped for stray un-hardened bar reads in `fills_from_decision`
  itself (found none) and verified `range(1, n_tracks)` against `compute_aligned_positions`'s own
  loop with no off-by-one. No corrections from either reviewer.
  Author: Claude. Owner: Claude. Status: DONE 2026-09-22, reviewed SOUND.
  Peer review: SOUND - MiniMax `Receipts/2026-09-22/minimax-review-d4-e8.md`; SOUND - Claude
  subagent (verdict + reasoning in this session's transcript, not yet a saved receipt file).

## F - Carried, deferred on purpose (not open work - listed so they are not silently rediscovered)

- Demucs stem-cache reuse across projects (every project currently re-runs Demucs separation
  even on a track already processed elsewhere). Sam, 2026-09-12: "not now, just one for the
  list." Do not re-propose without Sam's go.
- Cataloguing different valid "mix styles" and the track markers that should trigger each. Sam's
  own idea, 2026-09-12/13, deliberately waiting on the blind listen's real evidence (see A5)
  before scoping - both sweeps independently agreed this should stay deferred.
- Ableton bounce export automation (former A3 second half - `ableton_ui.py`'s screenshot/click
  primitives composed into an unattended export-and-poll macro). Sam, 2026-09-14, asked directly
  with three options on the table (bounce by hand / automate non-blind renders only / build
  blind-safe coordinate-driven automation): "just bounce A/B/C by hand for now." Real reason it's
  not a simple yes either way: an AGENT driving the export by reading screenshots would see the
  arrangement/transition shape in Ableton's UI, breaking the blindness A5's listening test
  depends on - so this is not "not built yet," it's "declined for the blind sides specifically."
  Do not re-propose without Sam's go, UNLESS the future need is a non-blind render (an ordinary
  `/mix` run) where the disclosure concern genuinely doesn't apply - that variant was never
  declined, just never asked for.

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

Last item update: 2026-09-14 09:20 [Claude] - progress: A1 (grid_fold false positive) fixed and
self-tested (cross-region consensus fold, `Source/render_check.py` + `Tests/test_render_check.py`,
suite 653/6/0), NOT marked done - no independent peer review yet, item stays unchecked;
rev e6cf6c5 -> (this write).

Last item update: 2026-09-14 09:50 [Claude] - progress: A3 split - the hash/staleness half done +
self-tested (`check_stale_render` + `als_sha1`/`report_sha1` provenance stamping,
`Source/render_check.py` + `Tests/test_render_check.py`, suite 655/6/0); the export-automation
half surfaced a real design tension against A5's blindness requirement and needs Sam's decision
before building further - item stays open, unchecked; rev (uncommitted, follows the A1 fold
above) -> 8331a16.

Last item update: 2026-09-14 10:15 [Claude] - progress: Codex reviewed A1 (2 MAJOR + 1 MINOR, all
real, all independently re-verified by reproduction before being trusted); fixed all three in the
same session (ambiguity-WARN policy for partial alt-reinterpretation, early hash binding for the
A3 provenance stamp Codex also caught a race in, a closer test fixture) - suite 655/6/0 -> 657/6/0.
Both items stay open, unchecked - a re-review of the delta is still owed before either closes;
rev 8331a16 -> (this write).

Last item update: 2026-09-14 10:40 [Claude] - DONE: A1 (Codex round 2, same staged files,
`-Effort high` - directly re-exercised all four branch cases against the fixed code, verdict
"satisfactory... check off A1"; one soft wording note adopted). progress: A3's hash/staleness
half - Codex round 2 found round 1's fix "reduced, but not closed" (still a narrow open/open
race, plus a third un-raced report re-read for `track_bpms`); fixed to a true single-read
snapshot in the same session, verified structurally (grep: exactly one `.read_bytes()` per path
in `run_check`) - NOT yet re-reviewed, A3 stays open; rev df9febe -> (this write).

**Validator note (2026-09-14, A1's first-ever DONE in this list):** `validate_burn_list.py`'s
strict checks 6 and 11 will FAIL on A1's evidence pointers and receipt-less "SOUND" line. This is
a known, accepted gap between this project's lightweight, human-supervised review convention (an
actual Codex CLI call, staged real files, its verdict quoted verbatim, two full rounds) and the
skill's full PLAN-v2 receipt apparatus (a `Staging/` folder with a machine-readable `VERDICT:
SOUND` receipt file), which this project has never set up and is not adopting mid-list. The
review genuinely happened and was genuinely independent (Codex, not Claude); it just is not
machine-verifiable in the shape checks 6/11 expect. Don't "fix" this by fabricating a receipt
file - that would make the chain of custody WORSE (a real review dressed as a machine-verified
one it is not), which is exactly what checks 6/11 exist to catch elsewhere. Future DONE items in
this list will show the same two expected FAILs unless the project later adopts the full
apparatus - read past them, don't chase them to zero.

**Validator note, addendum (2026-09-14, A5's DONE):** strict check 2d will also FAIL on A5's
`Peer review: n/a`. A5 is Sam's own listening verdict, not a code/build artifact - the skill's
"independent SOUND verdict from a different brain" model assumes every DONE item is Claude/Codex/
MiniMax/Kimi output another brain can review; a human's subjective preference call has no such
counterpart by definition, and dressing it up as "reviewed" by another brain would misrepresent
what actually happened (Sam listened, Sam decided - that's the whole point of a blind test).
`n/a` here is honest, not a gap to close. Same principle as the check 6/11 note above: don't
fabricate a receipt to satisfy the checker; read past this one for any future item whose DONE
state is a human judgment call rather than a build. **B3 (2026-09-14) is the same category**:
its own DONE state is Sam's sequencing decision ("yes, scope now"), not a build - checked off
the moment he said so, same as A5. The BUILD WORK B3's decision unlocked (C5, C2, and the rest
of item (c)'s landing order) are tracked as their OWN separate items with their own real peer
review requirements - B3 being `n/a` never exempts them.

Last item update: 2026-09-14 10:55 [Claude] - progress: A3's hash/staleness half CLOSED - Codex
round 3 (own AST read-access audit, not just re-reading the diff) returned "NO MATERIAL
OBJECTIONS... A3 is closed"; one MINOR adopted (json.loads(bytes) accepted some UTF-16/32 input
the old explicit UTF-8 read would have rejected - fixed to explicit `.decode("utf-8")` first).
Full suite unchanged 657/6/0. Item A3 stays open on its export-automation half alone (needs Sam's
decision per its own STATUS block); rev (uncommitted, follows the prior two folds above) ->
(this write).

Last item update: 2026-09-14 11:05 [Claude] - DONE: A3. Sam's decision, asked directly (three
options): bounce A/B/C by hand for now rather than build export automation - real reason it was
a genuine decision, not a formality, is the agent-visually-reads-the-screen conflict with A5's
blindness requirement, not just cost. Hash/staleness half already Codex-clean (round 3); the
export-automation half is CLOSED as "declined by Sam," moved to Section F so it isn't silently
re-proposed. A2 updated to record the bounce is now Sam's confirmed path, not a stopgap; rev
(uncommitted, follows the prior folds above) -> (this write).

Last item update: 2026-09-14 11:20 [Claude] - create: A6, found live while Sam tried to bounce
Side A - every AB-comparison ALS carries a Home-PC-only absolute path (`G:/...`) and a
one-level-too-shallow relative path, so opening on the Studio PC shows offline samples on
whichever tracks Ableton's own cross-project cache doesn't already know. Ruled out (not assumed)
that the two currently-stuck files (Freejak, Jewel Kid) are missing/misnamed/corrupt/cloud-only -
verified all four ways directly against disk. Not yet fixed at the source; rev (uncommitted,
follows the prior folds above) -> (this write).

Last item update: 2026-09-14 11:35 [Claude] - progress: all three A/B/C bounced by Sam, render_check
run on all three for the first time on real B/C audio. Side A clean (WARN only, ready). Sides B/C
FAIL - a new `loop_verbatim` defect class never seen before, since B/C audio never existed to
check until today (B: 3 FAILs; C: same 3 + 2 more). Blocks A4/A5 as written - not yet
root-caused, reported to Sam rather than investigated blind; rev (uncommitted, follows the prior
folds above) -> (this write).

Last item update: 2026-09-14 12:00 [Claude] - progress: A2's loop_verbatim FAILs root-caused and
fixed same-session (Sam: "dig into root cause now") - a check-side bug in
`_verbatim_gated_pairs`'s intro-loop model, not a mix defect (the incoming's volume ramp spans
the whole pre-swap loop region, confirmed directly against real automation data, not just an
inference). Re-verified against the actual Side B/C renders, not only synthetic tests - all
FAILs gone, correctly reclassified as INFO. All three sides now read at the same WARN-only
severity for the first time. Suite 657/6/0 -> 658/6/0. Item stays open - not yet peer-reviewed;
rev (uncommitted, follows the prior folds above) -> (this write).

Last item update: 2026-09-14 12:20 [Claude] - progress: Codex reviewed A2's loop_verbatim fix -
"do not check off A2 yet" - 2 MAJOR (an off-by-one in the fully-post-swap check; straddle-prefix
recovery wrongly still applying to intro loops) + 1 MINOR (message overclaimed "under automation"
when coverage was actually unidentified), all real, all fixed same session, proved-the-test both
ways, re-verified against the real B/C renders a second time (still zero FAILs). Suite 658/6/0 ->
659/6/0. Item stays open - confirmation round on this delta not yet run; rev (uncommitted, follows
the prior folds above) -> (this write).

Last item update: 2026-09-14 12:30 [Claude] - DONE: A2. Codex round 2 "NO MATERIAL OBJECTIONS...
A2 may be checked off" - all three round-1 findings independently confirmed closed. All three
A/B/C sides bounced, checked, a real check-side defect found and fixed across two Codex rounds,
all three now read at the same WARN-only severity. This is the first time this project has had
genuinely comparable A/B/C audio to work from; rev (uncommitted, follows the prior folds above)
-> (this write).

Last item update: 2026-09-14 13:10 [Claude] - progress: A4's reconciliation half root-caused,
fixed at the source (propose_arrangement.py), wired into build_ab_comparison.py as a real gate,
and verified three ways including the actual reconciler against real patched MixPlans - all
three sides now PASS (69/71/73 checks). Both brains' mix.md updated to match (frozen sync list,
content-verified identical). Suite 659/6/0 -> 666/6/0. The excerpt-extraction half (Astra's other
A4 finding) stays open. Item not yet peer-reviewed - stays unchecked; rev (uncommitted, follows
the prior folds above) -> (this write).

Last item update: 2026-09-14 13:16 [Claude] - reviewed: A4's reconciliation half. Codex, 1 round,
`-Effort high`, real staged files (`propose_arrangement.py`, `build_ab_comparison.py`,
`test_propose_arrangement_mixplan.py`), "NO MATERIAL OBJECTIONS" on first pass - confirmed the
read-not-recompute WarpMode approach, the escaping double-lookup, the human_overrides keying, and
the broad except-Exception gate boundary are all correct as built. Reconciliation half is now DONE
and reviewed; excerpt-extraction half (Astra's other A4 finding) still open, so the item's
top-level checkbox stays unchecked. rev (uncommitted, follows the prior folds above) -> (this
write).

Last item update: 2026-09-14 13:45 [Claude] - progress: A4's excerpt-extraction half built. New
`Source/extract_transition_excerpts.py` cuts per-transition windows (overlap zone + 8 bars
context, reusing transition_review_viz's own convention) from each side's own
ARRANGEMENT_REPORT + ALS tempo, then hands them to the existing seal_listening_test.py as a
subprocess to randomise/blind/twin - the missing half of Plan V2's "whole-mix pair plus
randomized per-transition excerpts" requirement. 15 new tests, prove-the-test done on the
window-geometry formula, and run for real against all 8 transitions of the actual Tech House
Heldout A/B/C renders (T01's duration matched hand-computed expectation to sample-rounding
precision; side C's differing overlap geometry showed up correctly as a longer excerpt; no
silence/corruption on spot-checked audio). Both mix.md docs updated (frozen sync list,
content-verified identical). Suite 666/6/0 -> 681/6/0. Item not yet peer-reviewed on this half -
stays unchecked; rev (uncommitted, follows the prior folds above) -> (this write).

Last item update: 2026-09-14 14:05 [Claude] - reviewed + fixed: A4's excerpt-extraction half,
round 1. Codex, 1 round, `-Effort high`, real staged files - found 1 FATAL (sealed clips were
NOT actually blind: per-side duration differences, already visible in the prior verification
note as C's 147.69s vs A/B's 103.38s, are themselves an audible/visible tell regardless of
randomised filenames - nobody had connected that number to a blind-breaking leak until Codex
did) + 3 MAJOR (no cross-side transition-identity check beyond matching pair_index sets; no
binding between a manually-supplied --side WAV and its side's own ALS/report; raw side-labelled
clips could survive an abnormal stop) + 2 MINOR (unvalidated --context-bars; silent duplicate
pair_index overwrite). All six adopted and fixed - new duration-equalization (extends shorter
sides with more real audio, never silence, never crops the content being judged), a cross-side
identity check, a duration-sanity mitigation, opaque temp filenames + a subprocess timeout +
stale-tempdir cleanup, and the two validation gaps closed. 13 new tests (15 -> 28), proved-the-
test on the FATAL fix (skipped equalization, confirmed the exact real-world failure numbers,
restored), and re-ran against the real project: all 8 transitions still seal cleanly, and every
transition's 4 clips now measure IDENTICAL duration (0.000s spread, all 8 confirmed). Suite
681/6/0 -> 694/6/0. Round 2 (with seal_listening_test.py and render_check.py also staged, as
Codex asked for) not yet run - item stays unchecked; rev (uncommitted, follows the prior folds
above) -> (this write).

Last item update: 2026-09-14 14:45 [Claude] - reviewed + fixed: A4's excerpt-extraction half,
round 2 (this time with seal_listening_test.py and render_check.py also staged, as round 1
asked for). Codex found a second FATAL - this one in seal_listening_test.py itself
(pre-existing, not introduced this session): HOW TO LISTEN.txt named the twin's side outright,
letting a listener infer it the moment they spotted the matching pair. Plus 3 MAJOR (round 1's
own stale-temp cleanup was a live regression - no age check, could delete a concurrent
invocation's active clips; the duration-sanity mitigation doesn't catch a realistic same-length
A/B swap; reusing an out-dir across differently-sized runs left a stale unmapped clip) + 1 MINOR
(differing WAV format across sides survives as a technical tell). All five fixed: the leak text
rewritten, the cleanup now age-gated (1hr), a new record_bounce_manifest.py gives a real
cryptographic bounce-to-side binding (verified automatically, weak fallback now visibly warns),
stale Listen/_sealed cleared before every reseal, format-consistency enforced. 24 new tests
across 3 files, proved-the-test on both the manifest-swap detection and the concurrency
regression, re-ran against the real project a third time (including a real
record_bounce_manifest.py run against side A, confirmed picked up silently while B/C warned).
Suite 694/6/0 -> 708/6/0. Two straight rounds have each found a FATAL neither Claude nor the
prior round caught - round 3 is needed before this can be treated as done; item stays unchecked;
rev (uncommitted, follows the prior folds above) -> (this write).

Last item update: 2026-09-14 15:05 [Claude] - reviewed + fixed: A4's excerpt-extraction half,
round 3. FIRST ROUND WITH ZERO FATAL. 2 MAJOR (manifest binding stayed optional with only a
printed warning, so a completed run's own output carried no trace of which sides fell back to
the weaker check - fixed with a returned/recorded binding-strength dict, a "NOT FULLY AUDITABLE"
summary line, and an opt-in --require-bounce-manifests strict flag; the round-1/2 stale-temp
cleanup was STILL not concurrency-safe under an mtime-only age check - fixed by adopting Codex's
own "safest" option and removing automatic cross-run cleanup entirely, net simpler than what it
replaced) + 1 MINOR (a TOCTOU gap between verifying and later re-reading a WAV - deliberately
documented as an accepted, reasoned gap rather than fixed, given the threat model doesn't apply
on Sam's own machine with no adversary and a corrupted run from it is not silent). 5 more tests,
proved-the-test on the strict-flag refusal, re-verified against the real project a fourth time
including real record_bounce_manifest.py runs for all three sides with --require-bounce-manifests
set (silent success, binding all "strong") and a real refusal when one manifest was removed.
Suite 708/6/0 -> 710/6/0. Round 4 launched. rev (uncommitted, follows the prior folds above)
-> (this write).

Last item update: 2026-09-14 15:20 [Claude] - DONE: A4. Codex round 4 converged - 0 FATAL, 0
MAJOR, one MINOR explicitly flagged non-blocking ("hardening", not required to close the item),
fixed anyway as a bonus pass (WAV-identity recheck immediately before each slice, narrowing the
TOCTOU window at negligible cost - 3 new tests, proved-the-test, re-verified against the real
project with --require-bounce-manifests set on all three real sides). Both halves of A4 - the
MixPlan reconciliation data-gap fix and the per-transition excerpt-extraction script - are now
fixed, tested, verified against the real Tech House Heldout project, and independently reviewed
to convergence (reconciliation: 1 Codex round; excerpt-extraction: 4 Codex rounds, the first two
of which each found a genuine FATAL). Suite 710/6/0 -> 713/6/0. A5 (the actual sealed blind
listen) is now unblocked on the tooling side - what remains is Sam's own listening session.
rev (uncommitted, follows the prior folds above) -> (this write).

Last item update: 2026-09-14 15:45 [Claude] - DONE: A5. Sam listened blind against the real
sealed test (whole-mix + all 8 per-transition excerpts, full manifest binding). Twin control
confirmed on every transition - T6 the sharpest case, twin correctly identified live and it
still won. Verdict, per policy: `sam_v1` hits an explicit kill condition (2 losses of 8, over
the 1-loss limit) - park or revise. `sam_v1`+introloop doesn't trigger the hard kill but falls
short of the win bar (4/8 not ~6/8) - validated, not promoted. `interim_v1` stays default.
Written up in `Heldout Replay Result 02.md`, including the C1/C2 caveat (this `sam_v1` build
never consulted pair_history.jsonl or used content-aware automation style, so "revise" points
at C1/C2 specifically, not at abandoning swap placement). Lane A is now fully closed.
rev (uncommitted, follows the prior folds above) -> (this write).

Last item update: 2026-09-14 16:15 [Claude] - progress: B3 decided (Sam: "yes, scope B3 now") and
scoped - investigated the real align_engine.py call path first (not guessed), found C1 and item
(c) are the same decision point (folded C1 into landing-order item C7, superseded its own line),
proposed a 5-step landing order (C5/C2 built now, C6/C7/C8 to follow with Codex review before
the riskier ones land). Built + tested + proved-the-test on both C5 (align_engine.py's frozen
policy-constant bug - 6 new tests, zero change on the real 380-pair historical baseline) and C2
(automation style now content-aware - 5 new tests, but honestly found it produces ZERO change on
this project's actual T2 case since interim_v1's current swap point leaves only 1 bar of real
content there; the swap POINT itself, item c's job, is what T2 actually needs). Full suite
718/6/0 -> 724/6/0 across both. Neither yet peer-reviewed; item (c)'s full 5-step scope written
up under B3 above. rev (uncommitted, follows the prior folds above) -> (this write).

Last item update: 2026-09-15 15:29 [Claude] - create: D9, found live on the 15.09.26 August
Releases Mix and asked for by Sam ("there is one for the outro as well so add to the burn list
to check about both"). The long-intro and short-outro alignment fixes are built but opt-in, and
the standard `/mix` Phase 2 run never enables them. rev 1d80397 -> (this write).

Last item update: 2026-09-15 15:42 [Claude] - create: D10 and D11, two bugs found and fixed while
building the 15.09.26 August Releases Mix (a bare & written into the final ALS by A6's path
rewrite; tracks with "Audio" in the title dropped from the Phase 1 hint check). Both fixed, tested
and uncommitted, neither peer-reviewed yet. rev d5d7e67 -> (this write).

Last item update: 2026-09-15 16:06 [Claude] - refill: D10 and D11 DONE. MiniMax returned SOUND
(the independent review, receipt in Receipts/2026-09-15/). A Claude subagent standing in for
capped Codex also returned SOUND, and its two unasked findings were closed: a D11 regression test
(fails on the committed code, passes on the fix) and D11's missing evidence path. Four
deliverable receipts accepted, and the reviewed diff is byte-identical to the current code. The
code is still uncommitted. rev 010122d -> (this write).

Last item update: 2026-09-15 17:02 [Claude] - create: D12, found while reviewing Sam's hand-edits
to all 11 transitions of the 15.09.26 August Releases Mix: the correction learner labelled 5 of
11 because it classifies automation, not geometry. D9 gained a line of evidence (Sam kept the T5
swap where the `phrase` anchor put it). D10 and D11 committed since the last fold (1609f3a,
f6f1fdb, 6b31da7). rev cb42f7e -> (this write).

Last item update: 2026-09-15 18:50 [Claude] - create + progress: D13 created (Claude-arranged
mode, Sam's "yeah do that, re-run this one in Claude-arranged mode"), built the same evening
and Phase-2-proven on the 15.09.26 August Releases Mix; Phase 3 + scoring next session. D12
progressed: Claude stand-in SOUND on the corrected hash 33c07cf54058, receipt D12-a2 ACCEPTED,
MiniMax re-confirmation on that hash dispatched - stays OPEN until it lands. rev fe813f1 ->
(this write).

Last item update: 2026-09-15 19:10 [Claude] - DONE + create: D12 checked off - MiniMax returned
SOUND on the corrected learner hash 33c07cf54058 (`Receipts/2026-09-15/minimax-review-D12-
confirm.md`), joining the accepted D12-a2 receipt and the Claude stand-in's confirmation. Its two
MINOR `_find_swap_arr` notes opened as E7 (covered by the reliability gate). rev 4e0e3db ->
(this write).

Last item update: 2026-09-16 10:20 [Claude] - progress: D13 - Phase 3 run on the Claude-arranged
build (own ARRANGEMENT_REPORT/MIX_PLAN paths, V2 untouched), all gates PASS (validate_als strict,
87/87 MixPlan reconciliation), scored against Sam's hand tweaks: 10 of 11 transitions within 1 bar
of his geometry, 2 real vocabulary gaps found (no-EQ-swap crossfade at T4, levels not decidable at
all, biggest at T7). Write-up + decisions copy in Mix Patterns Library. `--decisions` documented in
`/mix` (2a.5, both brain copies, diff -w -B clean). Still OPEN - not yet peer-reviewed. Count
unchanged: still 10 open. rev 70d272d -> (this write).

Last item update: 2026-09-16 10:50 [Claude] - DONE + create: D13 checked off - Codex (genuinely
capped, confirmed live) re-routed to a Claude subagent, ran in parallel with MiniMax; both
independently caught the same 2 write-up errors (T5/T6/T9 sneak direction, T1 bar count) and the
same 1 real code gap (swap_in_bar / tail_loop bounds unchecked against the incoming/outgoing
track's own length) - write-up and mix.md corrected, code fixed with 2 proved-to-fail-pre-fix
tests, real decisions file re-verified clean post-fix, full suite 845/0/6. MiniMax's extra
swap_progress claim checked directly against the real ARRANGEMENT_REPORT and its specific number
(T5 at 0.97) was wrong (actual 0.86) - the general point stood anyway (T4 genuinely at 1.0,
documented as deliberate) so kept, the wrong specific number was not. 3 lower-priority findings
(name-match prefix, pair_index collision, unrounded fractional bars) opened as E8 rather than
fixed blind - real but unexercised by the shipped decisions file. rev 70d272d -> (this write).

Last item update: 2026-09-22 10:35 [Claude] - progress: D9's part-1 check done - new
`Tools/d9_cue_signal_replay.py` swept the full 380-pair 14.08.26 corpus under `rescue,deep,phrase`
combined and diffed against a freshly-computed default sweep (cross-checked byte-identical
against the frozen baseline first). Clean result: 0 changed, 0 newly-raise, 87/113 default-raise
pairs newly align (77%). Confirms empirically what the code comment claimed - these three signals
can only rescue a raise, never move or break a pair the default already handles. Full detail in
`Documentation/Plans/burn-list-2026-09-13/d9_replay_result.json`. Still OPEN - part 2 (Sam's call
on the default) is next. Count unchanged: still 10 open. rev c8ada13 -> (this write).

Last item update: 2026-09-22 11:40 [Claude] - DONE: D9 - Sam said "make it the default";
`align_engine.CueConfig`'s three field defaults flipped True, 9 tests across 5 files broke and
were each genuinely fixed (frozen baseline refreshed, a now-vacuous test tier retired with its
coverage confirmed to survive elsewhere, a stale fixture completed, a pinned constant moved with
traced arithmetic, one file isolated from the new default to keep testing its own orthogonal
concern). Full suite 874/6/0. Reviewed by MiniMax (2 runs, both crashed before their own helper
trailer but produced consistent, well-formed, corroborating reviews - one caught a stale comment,
fixed) and a Claude subagent standing in for durably-capped Codex (independently hand-traced the
code and reproduced a test failure outside its isolation fixture to confirm it was real; returned
one real CORRECTION - this item's own text was stale, now rewritten - plus two non-blocking MINOR
nits). rev c8ada13 -> (this write).

Last item update: 2026-09-22 13:10 [Claude] - Sam: "yes, fold it into D7. go ahead with C4, D4,
D7, D8, E7, E8" (following the session-start report above). Worked all six: D7 - new evidence
from D9's replay folded in (a different pair than originally cited fails the same way, still
open). D4 - the "1A" key-fabrication bug fixed and tested; MiniMax SOUND, a Claude subagent
found and fixed a real follow-on bug (the neutral-cost constant was actually cheaper than a
confirmed weak match, reproduced and corrected); the item's own broader chroma/Essentia
estimator scope stays open. D8 - per its own explicit gate, wrote a plan not code; MiniMax found
a real BLOCKER (the plan's central safety re-verification would have been a structural no-op,
traced to a pre-existing gap in the already-shipped refit tool) and it's fixed in the plan;
needs one more review round before code. E7 - investigated, found the candidate fix would
reintroduce a false-positive class this exact function was already corrected to avoid once
before; documented honestly, not built. E8 - all three schema edges hardened and tested,
reviewed SOUND by both MiniMax and a Claude subagent. C4 - wrote a plan not code (same rigor
tier as C6, zero real-world urgency); MiniMax independently agreed with the plan's own leaning:
park it, recorded as a decision. Full suite 890/6/0 throughout. Count: E8 DONE (9 -> 8 open,
26 -> 27 done); D4 stays open (half-built, real fix landed but the item's full scope isn't);
D7/D8/C4/E7 all stay open (evidence/plans/investigation, no items closed). Nothing committed yet.
rev 53c25fb -> (this write).

Last item update: 2026-09-22 14:20 [Claude] - building a real new mix ("22.09.26 Tech House
Core Sample", Sam: "go with 2 core sample") through the full three-phase pipeline hit a genuine,
previously-unverified bug in D5's own already-shipped code: `stem_detector.py --write-hints` run
standalone printed `[skip] no stats` for all 11 tracks and wrote an empty hints file, even right
after a full Phase 1a stem-grid/stem-sections/kick-model run on the same project - exactly the
gap D5's 2026-09-15 note flagged as unverified. Root-caused (the only bpm/downbeat fallback read
a `Blind_V*` folder from the amplitude-viz pipeline retired 2026-06-10 - nothing writes it any
more) and fixed (`_resolve_bpm_downbeat_stats()` now reads the track's own already-written
`SECTIONS_STEM_*.json` first). Verified live: 11/11 hints written. 5 new tests in
`Tests/test_stem_detector_bpm_fallback.py`, proved to fail pre-fix (ImportError - the function
didn't exist). Full suite 896/6/0. **Not yet peer-reviewed** - unlike D9/D4/E8 this session, this
fix has had no MiniMax/Codex/subagent pass yet; flagging rather than silently calling it DONE.
D5's checkbox was already `[x]` before this session (closed 2026-09-15) - this fold adds
verification evidence + a real fix under an already-closed item, it does not flip any open/done
count. Mix build itself passed every mandatory gate through Phase 4 (sections, hints,
arrangement, automation - `validate_als.py` x3, `validate_hints_vs_sections.py`,
`validate_mix_plan_als.py` 78/78); not yet rendered (Phase 5 needs a hand bounce). Count:
unchanged (8 open, 27 done, 1 dropped). Nothing committed yet - held for Sam's go-ahead.
rev (this write) -> (this write).

Last item update: 2026-09-22 15:05 [Claude] - Sam: "commit and push, then get a peer review on
the fix." Committed + pushed the D5 follow-up fix (`4d58926`, `burn-list/a1-a4-2026-09-14`).
Dispatched MiniMax (Codex still capped from earlier today) via `room_peer_review.ps1`, staged
with `align_engine.py` as the seam file - verdict SOUND, two load-bearing claims independently
re-verified against the real code (not trusted blind). One real nitpick adopted: the bare
`except: pass` on a malformed cache read gave an identical `[skip]` message to "no cache yet" -
same look-alike-failure shape as the original bug. Fixed with a `[warn]` print, locked in with a
6th test, full suite re-verified 896/6/0. Two nice-to-haves declined (provenance-tuple refactor,
defensive TypeError guard - neither blocking, MiniMax itself said ship without them). Second
commit pending in this same turn. Count: unchanged (8 open, 27 done, 1 dropped) - still
verification+fix under an already-closed D5, not a new open/done flip.
rev (this write) -> (this write).

## THE COUNT: 8 open, 27 done, 1 dropped (last update 2026-09-22 15:05 [Claude]: D5 follow-up
fix committed, pushed, and peer-reviewed SOUND by MiniMax; one real nitpick adopted (malformed-
cache warn print) and fixed same session; count unchanged, D5 was already closed). Prior update
(2026-09-22 14:20 [Claude]): real production
bug found+fixed in D5's already-shipped `--write-hints` path while building a genuine new mix;
tested (5 new tests, full suite 896/6/0), NOT yet peer-reviewed; count unchanged - D5 was already
closed, this is verification+fix under it). Prior update (2026-09-22 13:10 [Claude]): E8 DONE
(schema hardening, reviewed SOUND by both MiniMax and a Claude subagent); D4's fabrication bug
fixed and reviewed (item stays open, broader scope unbuilt); D8 and C4 got reviewed design plans
instead of code (D8: a real BLOCKER found and fixed in the plan; C4: independently recommended
to park); D7 and E7 got real investigation, no code: 9 -> 8 open, 26 -> 27 done. Prior update
(2026-09-22 11:40 [Claude]): D9 DONE - the
long-intro/short-outro rescue signals are now the CueConfig default, MiniMax + Claude-subagent
reviewed SOUND, 9 dependent test failures genuinely fixed, full suite 874/6/0: 10 -> 9 open,
25 -> 26 done. Prior update (2026-09-22 10:35 [Claude]): D9 part-1 replay clean (0 changed/0
newly-raise, 87/113 rescued) - evidence gathered, awaiting Sam's call on the default; count
unchanged. Prior update (2026-09-16 10:50 [Claude]): D13 DONE on 2
independent reviewers' converged findings, both fixed and re-verified; E8 opened for the 3
lower-priority findings neither review's fix touched: 10 -> 9 -> 10 open, 24 -> 25 done. Prior
update (2026-09-15 19:10 [Claude]): D12 DONE on
MiniMax's SOUND for the corrected hash, E7 opened for its two MINOR notes: 10 -> 10 open, 23 ->
24 done. Prior update (2026-09-15 18:50 [Claude]): D13 opened -
Claude-arranged mode, built and Phase-2-proven, Phase 3 + scoring pending: 9 -> 10 open. Prior
update (2026-09-15 17:02 [Claude]): D12 opened -
the correction learner misses geometry edits, evidence from Sam's 11 tweaks: 8 -> 9 open. Prior
update (2026-09-15 16:06 [Claude]): D10 and D11
checked off SOUND with a MiniMax receipt and four accepted deliverable receipts, code still
uncommitted: 10 -> 8 open, 21 -> 23 done. Prior update (2026-09-15 15:42 [Claude]): D10 and D11
opened, both fixed and tested but awaiting review: 8 -> 10 open. Prior update (2026-09-15 15:29
[Claude]): D9 opened - the
built long-intro and short-outro alignment fixes are off in the standard `/mix` run; check both,
then Sam decides on the default: 7 -> 8 open. Prior update (2026-09-15 14:55 [Claude]): A6 (AB-comparison
ALS broken sample paths) and D6 (vocal-clash + density suspect-passage report) both built,
tested against real corpus data, and MiniMax-reviewed twice each (plan then code) - both CHECKED
OFF SOUND: 9 -> 7 open, 19 -> 21 done. Real bugs caught during each build that neither plan
review anticipated (A6: a Windows case-insensitivity trap + an XML-escaping mismatch, both
found via real-corpus dry-run, not synthetic tests alone; D6: architecture-level, MiniMax's own
plan review caught invented field names and the wrong precedent before any code was written).
Full suite 780 -> 811 passed across both items, 0 regressions, 6 skipped throughout. Prior
update (2026-09-15 14:15 [Claude]): C9 MiniMax-
reviewed SOUND, no findings - CHECKED OFF: 16 -> 15 open, 12 -> 13 done. Then walked all 7
Sam-gated items past Sam directly, one question at a time (AskUserQuestion), and executed every
decision same-turn: B1 (park BASS_RESIDUAL_ENABLED - no calibration work commissioned), B2
(commit the 12.4.3 Ableton template as canonical - the tracked root template's content replaced
with the actually-used 12.4.3 file, verified via track-count + Ableton Creator= string on all 4
candidate .als files before touching anything), D2 (leave the mix-ending silent-tail warning
as-is, no auto-trim), E4 (let the missing golden fixture go, accept the 4 dependent skips as
permanent), E5 (hard-delete the archived stale files - ~615MB reclaimed), E6 (narrow mix.md's
"commit the held-out project" instruction rather than carve a .gitignore exception - both brain
copies fixed, diff -w -B clean) - 6 of 7 CHECKED OFF (D3 stays open, genuinely blocked, Sam
confirmed leaving it parked rather than chasing a fresh render): 15 -> 9 open, 13 -> 19 done.
Full suite 780/0/6 throughout, re-run clean after the template swap specifically (zero test
references _find_template, confirmed by grep - pure content fix). Prior update (2026-09-15
13:50 [Claude]): D5 + E2
(review debt) MiniMax-reviewed SOUND (single reviewer, proportionate for docs-only/low-risk
changes, unlike C2/C5's dual-review bar) - both CHECKED OFF: 17 -> 15 open, 10 -> 12 done. D7
also MiniMax-reviewed SOUND but left OPEN (the code addition is sound; the item's own
motivating problem - loop planning failing for feasible pairs - remains genuinely unaddressed
since it never reproduced, so a sound review of a partial fix is not the same as the item being
solved) - no count change from D7. E2's own review settled two long-uncertain sub-claims by
reading the whole file rather than grepping (neither reproduced as stated) - but sub-claim 4's
real substance (mix.md's "commit the held-out project" instruction is a silent no-op under the
real .gitignore) was confirmed genuinely true and spun off as a new item, E6 - GATED TO SAM on
intent: 15 -> 16 open. Net this update: 16 open, 12 done. Prior update (2026-09-15 13:35
[Claude]): C2 + C5
(review debt, both already-merged since 2026-09-14) peer-reviewed - MiniMax + an independent
Claude subagent (Kimi hit its own 5-hour cap on this exact dispatch, substituted with a Claude
subagent per CLAUDE.md's Kimi-capped guidance) - both CHECKED OFF SOUND: 18 open -> 16 open (C2,
C5 close) -> 17 open (C9 opens, see below); 8 -> 10 done. The subagent's C2 review found one
real, low-severity follow-on gap (propose_arrangement.py's ARRANGEMENT_REPORT.json computes a
second, now-stale copy of the same style-selection rule C2 patched elsewhere, zero real-world
effect - report-only, no readers) - spun off as its own new item, C9, rather than folded in.
Prior update (2026-09-15 13:05 [Claude]): E3
(extract_sections_als.py attribute-order fragility) built + tested (5 new tests, proved-the-test
against a real pre-fix stash) + MiniMax-reviewed SOUND (Codex still durably capped) - CHECKED
OFF: 19 -> 18 open, 7 -> 8 done. E5 (stale Output-folder artifacts) partially actioned - the
RENDER_CHECK.md half was already resolved organically by this week's A-series work (confirmed
directly, nothing stale left); the two literal stale files (a 752KB backup ALS, a 614MB stale
WAV) confirmed unreferenced by anything live and MOVED (not deleted) to a `_Stale Archive (E5)/`
subfolder, reversible - final hard-delete is GATED TO SAM (real rendered audio, not code, not
mine to permanently destroy). Stays open (not checked off) pending that call. No count change.
Prior update (2026-09-15 12:40 [Claude]): C6 built (bass_out_payoff tier) + Codex-reviewed
SOUND, merged - C6 was always a sub-bullet under B3, not its own checkbox, so this did not
change the count on its own. C3 (stem-grid BPM fallback) built, Codex + MiniMax reviewed SOUND
(Codex capped mid-session, MiniMax substituted per CLAUDE.md's standing guidance, caught one
real bool-coercion gap Codex's own rounds had missed) - CHECKED OFF, first count change that
update: 20 -> 19 open, 6 -> 7 done. C7 Step 0 (pair_history canonicalisation) built +
Codex/MiniMax reviewed SOUND, same real findings fixed - stays a sub-bullet under B3 (Step 1
unbuilt), no count change. D5 (wire --write-hints into /mix docs), D7 (feasibility checker also
validates loop planning, honest negative result on its own cited example), E2 (stale docs
archived + seal_listening_test.py CLI doc fixed) all built, not yet peer-reviewed - no count
change (still open). D3 investigated, found blocked (fresh render WAV not present on this
machine) - no count change. 18 open, 8 done, 1 dropped)
