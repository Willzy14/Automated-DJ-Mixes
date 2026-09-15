# D12 - Claude stand-in reviewer, confirmation round (2026-09-15)

Reviewer: Claude subagent (Codex stand-in, Codex capped until 2026-09-19), agent a6ca2bcb543d52876,
resumed with the two corrections applied. Reviewed hash: `33c07cf54058` (commit 66467db).

The reviewer re-ran its own two counter-example scripts from the CORRECTION round, unmodified,
against the corrected code rather than reading the diff:

1. Finding 1 (reliability judged on a different finder) - confirmed fixed. Its adversarial
   `analyse_transitions()` scenario (short outgoing, true kill on both sides inside a 3-copy tail
   loop, stray earlier ramp point in the wide pre-filter window) now returns
   `bass_swap_reliable=False` and `corrections=[]`; before, `True` and `bass_swap_moved:-4beats`.
   Mechanism checked: `_pinned_swap_event` reproduces `_find_bass_swap_beat`'s selection (+-40
   arrangement pre-filter, source map, first sub-0.8 point in the +-10 source window);
   `_find_swap_arr` requires a falling edge in a matching +-10 arrangement window;
   `_delta_event_reliable` trusts a side only when both land within 1 beat and off a loop.
   `_sides` is popped before the JSON entry is written.
2. Finding 2 (min/max envelope) - confirmed fixed. Its `intro_1(0-256) -> drop_2(500-700) ->
   bridge_x(300-340)` construction now returns `[]` from `_repeat_groups`. `_merge`/`_covered`
   use interval-union coverage; the `tail_repeat` gate (`abs(c1 - p1) < 0.01`, bypassed only
   for reps >= 2) rejects the one-off revisit while the real corpus's T11 (`tail_loop_added:
   1bx1+0b`) still passes - a true positive.

Also verified: the four newly cited tests exist and pass; full suite 830 passed, 6 skipped,
0 failed; the real-pair `--dry-run` correction lists for all 11 transitions are byte-identical
before and after the corrections; the em dash in `_print_geometry` is a plain hyphen.

VERDICT: SOUND

===CLAUDE-REVIEW-DONE exit=0 hash=33c07cf54058===
