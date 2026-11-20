# Session self-audit - Automated DJ Mixes, 2026-11-20 (Claude Fable 5.2, then Sonnet 5.5)

Written from the session itself, not a log-mining run (Claude's weekly usage had fallen to about 2 percent; Sam asked for the work to be boxed off).

## 1. What did Sam have to catch that we should have?
- **Blend rules written before examples.** The planner went through nine rounds because I wrote rules (a drums-only flat rule, a both-tracks bonus, loops taxed) before having more than six blends. Sam caught: "drums over drums sometimes works", "bottom end, not music", "loops exist to lengthen a blend", the swap must line up TWO cue points ("unacceptable" otherwise), the intro can be extended or cut too. The check that would have caught it: the answer key plus `Tools/score_blends.py` and `Tools/blend_candidates.py` (now committed), and a rule that a reviewer's argument against the owner's stated rule is not accepted without asking him. Owner: Claude.
- **I removed the both-track swap preference on a reviewer's argument** (round 4) against Sam's own practice; he rejected the result on sight. Lesson: the owner's words outrank a peer's argument.
- **Pictures:** the one-minute marker was drawn 29 seconds from the end (Reachin); early pictures had no fill times, no track end, no legend (he read the purple vocal trace as bass). Fixed in `Tools/section_key_picture.py`; test added.
- **Sam's challenge "is there any point if we don't have the new planner?"** was right: I was showing him output of a planner we were replacing.

## 2. What got redone, and why
- Section detector: four rounds (reviewers: bar-one shift split the clock; fill score flattered; fitted rules; format guards; then Sam's verdicts on three wider tracks; then his rule that a fill end is not a section).
- Blend planner: nine rounds (reviewers and Sam's rules above). Parked after several rounds each: grid-period rescue (a wrong rescue is worse than a visible failure), arc click locator (a 30 ms late cut passed), key estimator (47 percent exact), bar-one correction (the safe version moves nothing).
- Planned-mix wiring: exits came from a detector delta instead of the real clips (Codex review); rebuilt. The earlier sets turned out to match the corrected V3 beat for beat, so Sam's bounce stands.

## 3. Who built what (team ledger, this project's briefs, 2026-11-20; launch counts include failed starts)
- Codex: 17 builds, 28 reviews. Kimi: 2 builds, 9 reviews. MiniMax: 0 builds, 14 reviews (plan quota ran out mid-day). Claude subagents: about 14 builds (D21 x3, E1 x2, key estimator x2, blend planner x6 rounds, section detector x4).
- **Finding:** Claude subagent builds (Opus and Sonnet) drained the weekly allowance to 2 percent by the evening. Only after Sam said so did builds move to Codex and Kimi, which delivered equally well. Next time: name Codex or Kimi as builder first and keep Claude for briefs, judgement and review; Claude subagents only with a stated reason.

## 4. What did we claim that turned out wrong?
- "Call 911 and Kyle Watson are grid faults": Call 911 was my replay (it threw away the grid's bar one); Kyle's bar one is unproven either way (the safe rule moves nothing).
- "13 of 13 tail loops start at or after the swap": not provable from the records; the plan was parked.
- "5 of 6, then 6 of 7, then 7 of 7 blends right": in-sample, and the scorer showed the bottom-end rule inert and the measured content carrying three blends.
- "Quick" for the fresh mix: measured later at 11 minutes 13 seconds including a rejected lineup; the final replay chain took 21.7 s.
- The earlier planned sets "might be wrong at the end": measured, they were not; the V3 rebuild is identical in clip positions.

## 5. Repeats (key words searched in the memory index and Known Workarounds)
- Peer launcher quirks (launch from the PowerShell tool; MiniMax needs -Wait; Codex and Kimi refuse a worktree containing a Config folder without -AllowRepoRoot): already known, hit again.
- **New and now written down:** `kimi login` opens the Chinese site, approve on kimi.ai (Known Workarounds). PowerShell variables are case-insensitive: `$B` and `$b` clobbered each other and broke a queued job (Known Workarounds added).
- A Kimi review pack over about 120 KB crashes it (context ceiling): trim to what the question needs.
