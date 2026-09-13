# Review request: swap-first redesign, landing-order item (a)

Repo root: `G:\Wired Masters Dropbox\Sam Wills\0.1---GIT HUB---\Automated DJ Mixes`
Branch: `feat/swap-first-stage-ab` (one commit on top of `main`, hash `8913694`)
Diff for the exact change under review: `stage-a-diff.patch` (same folder as
this brief) - please open and read it in full, plus the three touched
source files directly in the repo for surrounding context
(`Source/align_engine.py`, `Source/apply_automation.py`,
`Source/propose_arrangement.py`) and the new test file
`Tests/test_outgoing_post_swap_content.py`.

## Background

Sam (the project owner) corrected `apply_automation.py`'s bass-swap safety
margin rule (2026-09-12): it picks a fixed margin (4 beats vs 8 beats)
purely from overlap LENGTH (<24 bars vs >=24 bars), with no check on
whether the outgoing track actually has real content left to fade across
after the swap. This caused a real, reproducible build failure: building
the `sam_v1` transition policy on a real held-out project
(`Test Project/10.09.26 Tech House Heldout`) raised

```
ValueError: Aligner-approved swap for 'Freejak...' -> 'HARTY...' is
outside the safe overlap: swap 1204.0 not in [1012.0, 1200.0]
(policy paired_landmarks_v2, handoff paired/landmark:kick_dropout:start->drop)
```

A separate peer (MiniMax) reviewed the failure and the underlying
`align_engine.py`/`apply_automation.py` alignment logic beforehand and
proposed three staged fixes, landing order (a) then (b) then (c):

- (a) a new `_outgoing_has_post_swap_content` detector + a margin rule that
  consults it, instead of overlap length alone
- (b) `plan_fill_or_cut` should report the POST-loop-extension overlap
  geometry to `apply_automation`, because MiniMax believed
  `apply_automation`'s `ov_end` was sourced from a stale, PRE-loop-extension
  value (read from "the sections JSON")
- (c) the full "swap-first" reorder of `align_engine`'s candidate search
  (pick the swap on musical merit first, derive overlap geometry after -
  currently overlap-window filtering happens before swap selection)

This commit builds ONLY (a). Please review it standing alone.

## What I need adversarially checked

1. **Correctness of `_outgoing_has_post_swap_content`** (`align_engine.py`).
   Two signals: `bass_out_is_end` (bass never returns before the file ends)
   and distance to the track's own `n_bars`. Is either signal wrong, or
   wrongly combined? Is `MIN_REMAINING_CONTENT_BARS = 1.0` (bars) a
   defensible floor, or does it need to be tied to tempo/section length
   instead of a flat constant?

2. **Correctness of the margin rule change** in `apply_automation.py`'s
   `plan_transitions` - the new three-way rule (cold-ending OR short
   overlap -> 4 beats; else 8 beats) and its wiring through the
   arrangement-report round trip (`propose_arrangement.py` writes
   `outgoing_has_post_swap_content` onto the transition entry;
   `apply_automation.py`'s `_load_arrangement_report` reads it back,
   defaulting to `True` when absent for back-compat with old reports).

3. **The interim_v1 safety claim - please verify, don't take my word for
   it.** I claim this change cannot alter ANY of interim_v1's existing
   production output, based on:
   - the frozen 380+113-pair alignment baseline
     (`Tests/test_alignment_baseline.py`, diffed against
     `Documentation/Plans/arranger-signal-rewiring/baseline_alignments.json`)
     still passes unmodified, because its PINNED field list doesn't include
     `outgoing_has_post_swap_content` and nothing about `align_pair`'s own
     decision logic changed - only `Alignment` gained a new field with a
     safe default (`True`) and `compute_aligned_positions` populates it in
     one place after `align_pair` returns.
   - a direct sweep I ran of all 380 real historical pairs in that same
     corpus (14.08.26 stem JSONs) found ZERO pairs where the actual
     clamp/error outcome would differ under the new rule vs the old one -
     every cold-ending + overlap>=24-bars pair in the corpus sits at or
     beyond the OLD 8-beat margin boundary already, so nothing in real
     history was ever affected by the distinction this change makes.
   Please try to find a counterexample to this claim, or a category of case
   my sweep wouldn't have caught (e.g. something dependent on loop
   insertion changing `ov_end` in a way my native-bar approximation
   couldn't see - see point 4).

4. **My challenge to MiniMax's hazard (b) - please adjudicate.** MiniMax's
   review said `apply_automation.py`'s `ov_end` (from `secs[-1]["arr_end"]`)
   is "sourced from the upstream sections JSON" and reflects
   pre-loop-extension geometry, so a transition whose overlap got extended
   by an outgoing-tail loop would still be checked against the STALE,
   un-extended overlap end.

   I read the actual code path and believe this claim is WRONG for the
   current codebase: `apply_automation.py`'s `main()` derives
   `sections_data` via `parse_sections_als(als_path)` on the ARRANGED ALS
   directly (the sections JSON is only a fallback when that parse is
   empty, which it never is in practice), and `parse_sections_als` treats
   loop-repeat clips as their own entries in a track's `sections` list, so
   `secs[-1]["arr_end"]` already reflects any tail-loop extension. I
   verified this empirically on a real transition with an outgoing tail
   loop (Yellody -> Freejak, `Test Project/10.09.26 Tech House Heldout`,
   Side A / interim_v1): the outgoing's `arr_end` (708.0) exactly matches
   the loop-extended clip layout, not the pre-loop section end.

   I then re-ran the ACTUAL originally-failing build
   (`build_ab_comparison.py` on the same held-out project) with only this
   commit's fix applied, and BOTH previously-failing sides (`sam_v1` and
   `sam_v1+introloop`) now build clean end to end (`validate_als.py` PASS
   on both), with the Freejak transition's report correctly showing
   `outgoing_has_post_swap_content: False` and a 4-beat margin. I did NOT
   build MiniMax's item (b) at all.

   **Question:** does this evidence actually settle it, or is there a
   scenario where MiniMax's hazard (b) is still real and just didn't fire
   on this specific project/corpus? If you find one, say so explicitly -
   don't just take "it built successfully once" as proof of general
   correctness.

5. **The corrected error message** - I removed the claim that a clamp
   trigger "can only mean the report and the arranged ALS disagree" (false,
   per this exact bug) and reworded it to state the geometry without
   asserting a single cause. Is the new wording accurate and useful for
   whoever hits it next, or did I lose something load-bearing from the old
   message?

6. **Anything else** - test coverage gaps, a case the new detector
   misclassifies that I haven't considered, interaction with the
   `two_stage_bass` / `QUICK_SWAP` style-selection logic downstream in
   `apply_automation.py` that I might have missed.

Please read the actual code, not just this brief, and ground every finding
in a specific file:line citation and (where possible) a concrete
counterexample scenario, not general concerns.
