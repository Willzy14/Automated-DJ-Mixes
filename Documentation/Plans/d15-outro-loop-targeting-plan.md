# D15 plan: fix outgoing-outro loop targeting (paired_landmarks_v2)

**Status: DRAFT, not built. This is core arrangement algorithm code
(`align_engine.plan_fill_or_cut`'s outgoing-tail-loop branch, `paired_landmarks_v2` is the
production default policy) — affects every mix built with it, not just the one that surfaced
this. Per this project's own standing trigger for substantial/core changes, this plan needs a
peer-reviewed pass before any code lands. Codex is durably capped (confirmed live earlier today,
0% weekly left, resets ~2026-09-26); routed to MiniMax + a Claude subagent per the standing
capped-seat rule.**

## The problem, restated precisely

Sam, by eye in Ableton before bouncing "22.09.26 Tech House Core Sample" (no code involved —
pure visual inspection), found the outgoing track's outro-loop extension inconsistent across
transitions: correctly reaching the incoming track's next break in some cases, undershooting it
in others, and not firing at all in a third case where it visibly should have. Re-investigated
against the real selection code with instrumentation (not the report JSON alone) and confirmed
**three separate, distinct defects**, not one:

### D1 (bug) — candidate selection has no preference for a named section over a raw kick blip

`Source/align_engine.py:2314-2327` builds `landmark_targets`, a single flat list mixing two kinds
of candidate: clean named section boundaries (`section:break_1`, from `i.sections`) and raw
Kick-Detector-V3 event landmarks (`landmark:kick_gap_157_160:end`, from `i.musical_landmarks`).
`:2341-2369` sorts this list ascending by bar and walks it, taking the **first candidate whose
`pick_cue_bounded_drum_loop` search succeeds**, then `break`s — with no preference for a
`section:*` candidate over a numerically-earlier `landmark:*` one.

Confirmed on T1 (HARTY → Jones) by re-running the real code against this project's actual data:
sorted candidates were `[(40, landmark:kick_gap_157_160:end), (44, section:break_1), ...]`. The
kick-gap landmark at bar 40 succeeded first and got selected — landing HARTY's outro loop at
arrangement beat 768, 16 beats (4 bars) short of Jones's real `break_1` at beat 784, which was
never even attempted despite being reachable within budget. Sam's own by-eye fix (extending the
loop by 3 more reps) closed most of that gap.

### D2 (bug) — a correctly-identified target can be silently abandoned with zero trace

Confirmed on T3 (Detlef → Enzo): `section:break_1` (bar 32) genuinely WAS the first candidate
tried this time — correct — but every loop-source window `pick_cue_bounded_drum_loop` tried in
Detlef's own outro (drums-only, 11 bars) failed the quality gate (`insert_level_match`/`period`/
`self_similarity`/`silence_fraction`, traced live: 10+ rejections across two candidate distances).
Every subsequent candidate in the sorted list was also tried and also failed. `nxt` (declared
`None` at `:2328`, only ever reassigned inside the loop's success branch at `:2366`) stayed
`None`.

The consequence is more specific than "no loop was added" — it's that **the existing "last
resort: loop the outro section itself, may carry bass, flagged" fallback (`:2397-2412`) never
runs at all**, because it lives inside `if nxt is not None and outro is not None:` (`:2381`) and
`nxt` requires a successful named-candidate match to ever be set in landmark mode. That fallback
exists specifically to avoid the total-failure case (its own comment: "no clean-drum window
ANYWHERE, e.g. Crusy") — but it is structurally unreachable whenever EVERY named candidate's
`pick_cue_bounded_drum_loop` search fails, which is exactly what happened here. Confirmed by
re-reading the control flow directly, not inferred.

The result — `loop_source: none` in the arrangement report — is indistinguishable from "no loop
was needed" (the correct outcome for 7 of the other 8 unlooped transitions in this same mix).
Nothing in `notes` or any other field records that a target WAS found and an attempt WAS made.

### D3 (transparency gap, lower priority) — the report-only candidate field is structurally incomplete

`report_landmark_candidates` (`:787-829`) only ever reads `track.musical_landmarks` — it never
includes the `section:*` candidates that `plan_fill_or_cut` actually considers internally. A
human reading `ARRANGEMENT_REPORT.json`'s `musical_landmark_candidates` field to sanity-check "was
a good target available" for a case like T1 would never see `section:break_1` listed at all —
the one piece of evidence that would have made the bug visible from the report alone, without
re-running code.

T2 (Jones → Detlef, confirmed correct by Sam) worked for neither principled reason — Detlef's
`break_2` happened to be the first reachable candidate with no closer kick-gap landmark competing
for the slot, and the quality gate happened to pass on that window. A coincidence of ordering,
not designed behaviour.

## What's already confirmed (don't re-derive)

- Both D1 and D2 were reproduced by re-running the real `align_engine.load_track` /
  `align_engine.align_pair` / `align_engine.plan_fill_or_cut` call chain against this project's
  actual `SECTIONS_STEM_*.json` data, with `pick_cue_bounded_drum_loop` monkey-patched to log
  every call, and cross-checked against the real bounced ALS's arrangement-timeline clip
  positions (ground truth extracted directly from `Sections V3.als`, not from any report JSON).
  Full trace is in this session's transcript; not yet promoted to a standalone doc — the reviewer
  should ask for it if the summary above isn't enough to verify against.
- D2's specific mechanism (the last-resort fallback being gated behind `nxt is not None`, which
  landmark mode can only set via a successful candidate) was verified by direct code reading of
  `:2328` and `:2381`, not inferred from behaviour alone.
- `locked_swap_gap` for T3 is `0.0` (the handoff/swap point sits exactly at the outro's own start
  bar) — the quality-gate failures are not a symptom of an unreachable/over-tight swap
  constraint; Detlef's outro material itself is what's failing the checks.

## Design

### Fix D1 — try section boundaries before raw landmarks, not just "whichever bar is smaller"

Split `landmark_targets` into two ordered passes instead of one merged sort: try every reachable
`section:*` candidate (ascending by bar) first; only if none of them produces a working chunk,
fall through to the `landmark:*` candidates (ascending by bar) as today. This directly matches
the function's own docstring intent ("loop the outro forward to REACH the incoming's next
**section marker**") rather than treating a raw kick-detector event as an equally-valid primary
target.

**Open question, flagged for review rather than assumed:** is this safe across the existing
corpus, or does some historical transition rely on a `landmark:*` target being picked ahead of a
farther-away `section:*` one for a good reason not visible from these two examples alone (e.g. a
section boundary that's technically reachable but sits in genuinely bad-sounding material, where
the raw landmark is closer to a clean splice point)? The `candidate_roles` field on musical
landmarks (`transition_boundary`, `automation_pivot`, `transition_end`, `incoming_ownership`,
`bass_swap_candidate`) never lists an outro-loop-target role explicitly, which is circumstantial
support for "these were meant for other purposes and got swept into this candidate pool
incidentally" — but that's an inference, not a confirmed design record. Settle with the full
380-pair (or current equivalent) corpus replay before/after, read every case where the verdict
changes, not just a pass/fail count.

### Fix D2 — make the last-resort fallback reachable in landmark mode

Track the first reachable, swap-gap-satisfying candidate's target bar (`candidate_nxt`) as soon
as it's identified — even if `pick_cue_bounded_drum_loop` fails to find a clean-drum-window chunk
for it — separately from `nxt` (which should still only mean "a clean-drum-window chunk was
found"). If the full candidate loop exhausts without a clean-drum-window success, enter the
existing last-resort branch (`:2397-2412`, "loop the outro section itself, may carry bass,
flagged") using that remembered target bar, instead of skipping it because `nxt` was never set.

**Open question for review:** should the last-resort fallback's own `_assess_loop_candidate`
check be allowed to ALSO fail (meaning genuinely nothing usable exists in the outgoing's outro,
and the transition correctly gets no loop) — yes, that must remain possible; this fix makes the
fallback *reachable*, not *guaranteed to succeed*. Confirm on the real T3 case whether the
last-resort candidate (Detlef's `outro_1`, bars 153-164, bass allowed) actually passes
`_assess_loop_candidate` once reached — if it does not, D2's fix alone doesn't rescue T3 and the
quality-gate calibration itself becomes the open question instead.

### Fix D2b (reporting, do regardless of whether D2's fallback rescues the case) — record why, not just whether

When a named target is identified but no loop is ultimately produced (whether the last-resort
fallback above also fails, or before this fix exists at all), append a note to the transition's
`notes` field or a new structured field recording the target that was sought and why it was
abandoned (e.g. `"outgoing tail loop: target 'section:break_1' (bar 32) identified but no
loop-source passed quality checks"`). This closes the "indistinguishable from not needed" gap
regardless of how far D1/D2 themselves land.

### Fix D3 — make the report-only candidate field complete

Extend `report_landmark_candidates` to also emit `section:*` entries from `i.sections` (mirroring
the `landmark_targets` construction in `plan_fill_or_cut`, without duplicating its bar-arithmetic
by hand — factor the "build candidate list for this pair" logic into one shared helper both
functions call, rather than keeping two independently-maintained copies that can drift again).
Also set `"selected": True` on whichever entry the real decision actually picked, instead of
always `False` — the function's current docstring ("without selecting one") describes the
current limitation, not a design requirement; revise the docstring alongside the fix.

## What this explicitly does NOT change

- The swap-point (bass-to-bass) locking logic — untouched; D1/D2 only affect the OUTGOING-OUTRO
  loop target, never the invariant swap.
- `pick_cue_bounded_drum_loop`'s own quality thresholds (`insert_level_match`/`period`/
  `self_similarity`/`silence_fraction`) — untouched by this plan. If D2's fix reaches the
  last-resort fallback and it ALSO fails on real material, that becomes a separate, later
  question about whether those thresholds are miscalibrated for sparse/drums-only outros — not
  assumed or fixed here.
- The incoming-intro loop logic (item 1/1a in `plan_fill_or_cut`'s docstring) and break-skip
  logic (item 4) — untouched.
- `loop_budget` / `overlap_ceiling` policy constants — untouched.

## Validation plan (before this is considered built, not just coded)

1. **Real-data re-run on this exact mix.** Re-run `propose_arrangement.py` (or the narrower
   `plan_fill_or_cut` call) against "22.09.26 Tech House Core Sample" post-fix and confirm: T1
   now targets `section:break_1` and reaches (or gets as close as the loop budget/quality gate
   allows to) Jones's real break boundary; T3 either gets a loop via the now-reachable last-resort
   fallback or gets an explicit "abandoned, here's why" note instead of silent `loop_source:
   none`; T2 is unchanged (already correct) — prove byte-identical output for T2 specifically, not
   just "no crash".
2. **Full corpus replay, read the diffs, not just the pass/fail count** (same discipline as D9's
   baseline replay). Every transition whose verdict changes needs its before/after read by a
   human (or peer), not just counted — this is exactly the kind of change where "N pairs changed"
   without inspection could hide a regression as easily as it confirms a fix.
3. **Unit tests**: a synthetic pair where a `section:*` candidate and a nearer `landmark:*`
   candidate are both reachable and both pass quality — confirms `section:*` wins post-fix,
   confirms it did NOT win pre-fix (proves the test actually exercises the bug). A synthetic pair
   where every named candidate fails quality but the outro-itself last-resort would pass —
   confirms the last-resort fires post-fix, confirms it silently didn't pre-fix.
4. **Full suite green.**

## Open questions for the reviewers (and ultimately Sam)

1. Is "section boundaries always tried before raw kick-gap landmarks" the right default, or
   should there be a distance/quality tiebreak instead (e.g. only prefer the raw landmark if it's
   meaningfully closer AND the section boundary's own material fails quality)? This plan's
   position: try section first, unconditionally — simpler, matches the docstring, and D2's fix
   already provides a fallback path if the section-targeted attempt itself can't find good source
   material. Flagging as the plan's own judgment call, not a settled fact.
2. If D2's fallback reaches Detlef's own outro material for T3 and it STILL fails
   `_assess_loop_candidate`, is that a sign the quality gate is over-tuned for sparse/drums-only
   outros, or a correct rejection of genuinely bad-sounding material Sam hasn't heard yet? Not
   answerable from static analysis — needs the real audio judged, by ear, after this fix lands
   and produces its actual candidate loop for T3.
