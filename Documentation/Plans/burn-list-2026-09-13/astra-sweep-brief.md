# Independent sweep: what's still outstanding in this DJ-mix pipeline

Repo root: `G:\Wired Masters Dropbox\Sam Wills\0.1---GIT HUB---\Automated DJ Mixes` (an Ableton
DJ-mix-generation Python pipeline: three phases, sections -> arrangement -> automation, using
owned Demucs-stem-based analysis, no Rekordbox in the canonical path).

This is one of TWO independent sweeps being run in parallel for a fresh BURN_LIST.md (the
project has none yet) - a separate Claude/Fable agent is doing the same sweep, working blind to
this brief and this answer. You are not reviewing its output; you are an independent second
lens. Sam explicitly wants two brains cross-checking each other here, not one building and one
reviewing.

**This brief may be wrong or incompletely framed. Say so explicitly if you think it is, and
answer what you think the right question is too.**

## The actual question

"What is genuinely still outstanding in this pipeline, and what would most reduce Sam's own
manual clean-up work on the mixes it produces?" Read the real code and real project files -
don't take this brief's characterization of anything as ground truth, verify it yourself.

## Settled context (do not re-litigate; flag if you find it's actually wrong)

- `interim_v1` is the ONLY production policy today. `sam_v1` and `sam_v1+introloop` are
  experimental comparison policies (`Source/build_ab_comparison.py`) under evaluation, not
  promoted - promotion needs Sam's own blind-listen verdict
  (`Documentation/Mix Patterns Library/Heldout Replay Plan V2.md` has the pre-registered kill
  criteria). That blind listen has not happened yet.
- TODAY (2026-09-13): a fix landed making `apply_automation.py`'s bass-swap safety margin
  content-aware (`Source/align_engine.py`'s `_outgoing_has_post_swap_content`,
  `Source/apply_automation.py`'s `plan_transitions`) instead of purely overlap-length-based.
  Reviewed by Codex already (a separate call from this one), corrected twice, re-verified against
  a 380-pair historical alignment corpus (zero changed outcomes) and a real rebuild of the
  held-out Tech House project's A/B/C sides (all three now build clean, where B/C previously
  crashed). `Documentation/Plans/swap-first-redesign/` has the review trail; `git log` has the
  actual commits (search "swap-first" / "outgoing_has_post_swap_content").
- A further, deeper "swap-first" reorder of `align_engine`'s candidate search (pick the swap
  point on musical merit BEFORE deriving overlap geometry, instead of today's fit-inside-a-
  precomputed-window order) is explicitly NOT built - queued only if a real case surfaces that
  today's fix alone can't handle. Don't re-propose it without new evidence it's needed.
- KNOWN, NOT YET FIXED: (1) `Source/render_check.py`'s `grid_fold` check FAILED on the held-out
  project's Side A render (231.8ms beat-grid drift across the mix) - never root-caused, see
  `Test Project/10.09.26 Tech House Heldout/Output/RENDER_CHECK.md`. (2) A provably-silent
  leftover clip after a QUICK_SWAP transition (Freejak->HARTY in that same project) -
  root-caused (Phase 2 arrangement freezes geometry before Phase 3 picks the automation style)
  but deliberately not patched, since a naive fix risks breaking
  `validate_mix_plan_als.reconcile`'s clip-boundary safety check on production `--tempo-arc`
  builds.
- DEFERRED ON PURPOSE, do not re-propose without new reasoning: Demucs stem-cache reuse across
  projects (every project currently re-runs Demucs separation even when the same track was
  already processed elsewhere - Sam said "not now, just one for the list"). Cataloging different
  valid "mix styles" and what track markers should trigger each - deliberately waiting on the
  blind-listen result first.
- The blind listening test (`Source/seal_listening_test.py`) has not run - sides B and C now
  build (as of today) but have not been through `render_check.py` or an actual Ableton bounce.

## What to actually do

1. Read `Documentation/AI_CONTEXT.md` in full - its "What's Next" section is STALE (predates
   most of the above), treat it as a lead to verify against the real code, not ground truth.
2. Grep `Source/` for `TODO`, `FIXME`, `XXX`, `HACK`, `NotImplemented`, "for now", "not wired",
   "not yet", and skipped/xfail tests in `Tests/`.
3. Read `Test Project/10.09.26 Tech House Heldout/Output/RENDER_CHECK.md` and `REVIEW_A.md` in
   full for anything flagged WARN or FAIL beyond the grid_fold issue already named.
4. Distinguish, for each finding: genuinely broken/incomplete pipeline behavior vs. a real
   opportunity to reduce Sam's manual polish work vs. hygiene/tech-debt that doesn't touch
   output quality. These need different owners and urgency.
5. Report anything found unasked - a contradiction between docs and actual code behavior, a
   stale claim, a deferred item you think should be reprioritized, a real defect nobody pointed
   you at.

## Output format

Print your complete answer as your final message - do not write it to a file. For each item: a
short plain-English name, what and why in one or two sentences, a file:line citation for
anything you claim exists in the code, and your own severity/priority judgement specifically
through the "reduces Sam's manual work" lens (not generic code-quality severity). Sections: (A)
genuinely broken/incomplete pipeline behavior, (B) real opportunities to reduce manual polish
work, (C) hygiene/tech-debt that doesn't affect output quality, (D) anything you think is
misframed in this brief or wrong in the settled context above. If a section is empty, say so
explicitly rather than inventing filler.
