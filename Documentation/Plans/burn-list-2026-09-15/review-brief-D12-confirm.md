VERDICT FORMAT: the FIRST line of your reply must be exactly one of:
VERDICT: SOUND
VERDICT: CORRECTION
VERDICT: DROP
and the LAST line of your reply must be exactly: ===REVIEW-COMPLETE===

CONSUMED-BY: Claude, deciding whether burn list item D12 (Automated DJ Mixes, Documentation/BURN_LIST.md) can be checked off. Your earlier review (minimax-review-D12.md, staged here) returned SOUND on the FIRST build of this change, sha256 168b6051b358. The code has since changed - two corrections from a second reviewer were applied - so your SOUND is bound to a stale hash. This round asks only: does the CORRECTED file (sha256 33c07cf54058, staged as learn_from_correction.py) still deserve SOUND?

This brief may be wrong. If a premise below is false, say so.

## What changed since your review (both in learn_from_correction.py; diff staged as review-D12-confirm.diff)

1. Reliability of the bass-swap delta. Before: `bass_swap_reliable` was judged from `_find_swap_arr`, a second finder that could certify a point the delta was not built from. Now `_pinned_swap_event` reproduces the source-space finder's (`_find_bass_swap_beat`) own selection step for step (the +-40-beat arrangement pre-filter, the source map, the first sub-0.8 point in the +-10 source window); `_find_swap_arr` requires a falling edge (prev >= 0.8 -> v < 0.8) inside a +-10 arrangement window rather than any low point; `_delta_event_reliable` marks a side reliable only when both finders land within 1 beat of each other AND that point is on a section clip, not a loop clip.
2. `_repeat_groups`: "already played" was checked against the min-max span of preceding clips' source; a one-off clip revisiting source from inside a skipped gap read as a loop. Now `_merge`/`_covered` use the union of played intervals, and a single copy (reps == 1) counts only when it repeats the previous clip's own tail (`abs(c1 - p1) < 0.01`).

Real-pair result: the 11 transitions' correction labels are byte-identical before and after both corrections (the second reviewer re-ran the real pair). Tests: `Tests/test_learn_geometry_corrections.py` is now 15 tests (staged); the three learner test files pass (45 passed); full suite 830 passed, 6 skipped, 0 failed.

## What to check

- Does `_pinned_swap_event` really reproduce `_find_bass_swap_beat`'s selection? Any input where they diverge would make `bass_swap_reliable` wrong in the direction of TRUE (the dangerous direction).
- `_find_swap_arr`'s falling-edge rule: can a real kill be missed (edge outside the +-10 window, or a kill that steps down in two moves neither of which crosses 0.8)? What does the geometry layer then report?
- `_repeat_groups` with the union check plus the tail-repeat gate: can a real single tail repeat be rejected, or a non-loop still slip through?
- Anything in the diff that changes an EXISTING pair_history field's meaning. The intent is additive only.

## Standing decisions (do not reopen without new evidence)

- The 2026-09-10 source-anchoring of the automation diff stays (`bass_swap_delta` is the raw source difference; a test pins it).
- Withholding the `bass_swap_moved` LABEL when a swap sits on a loop clip is the agreed behaviour; the field itself is never suppressed.

FOUND UNASKED: if you see a defect outside these questions, report it under this heading with file:line.
