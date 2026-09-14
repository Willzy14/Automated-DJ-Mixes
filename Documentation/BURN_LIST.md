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

- [ ] **Every AB-comparison ALS bakes in a machine-specific absolute path AND a relative path one
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
  Owner: Claude. Peer review: NONE - not yet reviewed.

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
state is a human judgment call rather than a build.

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

## THE COUNT: 21 open, 5 done (last update 2026-09-14 15:45 [Claude]: A1+A2+A3+A4+A5 DONE - Lane A (switch-on path) fully closed; A5's verdict is written up in Heldout Replay Result 02.md - 21 open, 5 done)
