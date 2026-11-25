# Session self-audit: Automated DJ Mixes, 2026-11-23 to 2026-11-25 (STUDIO-2)

Written 2026-11-25 by Claude (Sonnet 5.5). Why it ran: a session of about 20 hours of active work (47 hours elapsed), and the hard gate in `/session-end`. Log used: `fdc9bde8-30e3-46e2-981c-f0cef04bee2e.jsonl` (found by Sam's own words "make vocal clash and key actually influence the blend"). The extractor printed: sam_messages 63, my_replies 242, peer_calls 14 (minimax 14, codex 0, kimi 0), subagents 26, task_notes 154, errors 9, claims_flagged 10, gaps_over_90_min 2, bare_code_replies 0 of 242. Times are the log's own UTC, which is London time in November.

Beside this file: `self_audit_timeline_facts.md` (built by a Sonnet subagent before any judging) and `self_audit_minimax_view.txt` (MiniMax, written from the same material and landed at 08:52 on 2026-11-25, before this report). Section 6 says where the two views disagree.

## The one-line finding

The machine now builds a whole mix that Sam rates good on all nine blends with the cues "bang on", but we got there with two avoidable misses on my side: I picked the tracklist badly and had to be told three times, and I answered Sam's "go ahead with improvements" with "No response requested" and then did nothing for 13 hours.

## What went well (keep doing it)

- **An executing reviewer beside a reading reviewer.** Every build got a MiniMax read and an Opus subagent that ran the code. Opus found what reading could not: four forged files that passed the apply-mode gate, a bounce gate hook that could never see a fresh bounce, a data-loss glob in the pipeline. MiniMax found things Opus did not (a 3/4 track read at 116 BPM). Ten merges, no regression on the full suite (3224 passed on merged main).
- **Parallel worktrees, one builder per problem.** Five builds ran at once on 11-24 morning, each in its own locked worktree, each cleared the turn it merged. No work was lost.
- **Checking the picture, not the log.** The new kick check and bounce check were run on the real mix. The kick pictures showed why the first rebuild failed (one track's grid drifting in two blends), and swapping that track removed the FAIL.
- **Stopping at a gate.** The beat-grid gate rejected two non-four-to-the-floor records and I replaced them instead of overriding it. The planner's new vocal cost found a real 7-bar vocal overlap (cost 18.4); the swap removed it.
- **Reporting what the checks do not know.** The blend the checks flagged hardest (join 7) was the one Sam liked best, and the report had said only "look at it". Sam's ear stayed the judge.

## What Sam had to catch

Every row has the check that would have caught it and who owns it. "Built" means the check now exists in the repo.

| When | Sam's note | What was missing | Next time: the check, and the owner |
|---|---|---|---|
| 11-23 09:19, 10:21, 11:00 | A flam in mix 1; "the warp on both is off in opposite directions"; why is the visual check not in the pipeline | No two-track check inside a blend; the flam probe ships disabled in code | Built: `Tools/check_kick_alignment.py` plus the pipeline hook. Owner: Sonnet builder (done, merged 12:47). |
| 11-24 09:38 | "the full leveller also affects EQ and stereo width" | I answered from the registry entry, not the source, and said I could not see it touching EQ or width | Rule: before describing what one of Sam's tools does, read its source and cite file and function. Owner: Claude lead. Memory `read-source-before-describing-sams-tools` (written today). |
| 11-24 10:13 | "keys are not in the database, do not search elsewhere" | No coverage count before building a feature on key data (2,516 of 13,175 credits, 8 of 9 Tech House tracks missing) | Every builder brief for a feature that reads data states its coverage count. Owner: Claude lead. `Documentation/Plans/BRIEF_CHECKLIST.md` (written today). |
| 11-24 15:42 | "is all of this in the /mix skill now?" | The planner paragraph was absent from `mix.md` for 5 hours after the vocal-key merge; four other features were added the hour they merged | Merge procedure line: if the work changes what a mix run does or prints, update `commands/mix.md` in both brains in the same turn and `cmp` them. Owner: Claude lead, in `/orchestrate`. Needs Sam's yes (global skill). |
| 11-24 15:59 | "12 tracks" | I built 10 and never said why. The template supports 12 | The picker prints requested n against built n; a mismatch is said in the first line of the reply. Owner: Sonnet builder, in `Tools/playlist_builder.py` (card on the Master Board). |
| 11-24 16:07 | "how have you picked these ... such a mish mash ... a credible cool mix" | `playlist_builder.py` with `--preset any` ranks by stream percentile and gives each slot one genre, so it returns the most-streamed records across eight styles | Pick manifest printed before any stem run: active filters, pool size, genre and era spread, repeats against earlier mixes. A "credible" request never uses `--preset any`. Owner: Sonnet builder, in `Tools/playlist_builder.py`. |
| 11-24 16:08 to 16:11 | "why go with ones that are not 24 bit", "use all years", then "bit depth does not matter" | I turned a complaint into a filter (24-bit only), which cut the pool to 2019 onwards | A complaint becomes a query to the pool, never a filter. Memory `bit-depth-does-not-matter` (written 11-24). Owner: Claude lead. |
| 11-24 18:08 | "can you open the file so I can bounce" | `reveal_path` refused a path outside the session folders and I told Sam the session could not open it | Known Workaround: `Start-Process <file.als>` opens an ALS in Ableton for any path. Owner: Claude lead, in `Known Workarounds.md`. |
| 11-24 18:48, 19:05 | "flam with an F" (I recorded "slam"); "I am still in Physical" | I filed a note before confirming which blend it belonged to and which word it was | Every listening note is tied to a blend number and written into the calibration table before I react to it. Owner: Claude lead. `Documentation/Mix Patterns Library/listening_calibration.md` (written today). |
| 11-24 19:11 | "If you are able to make any adjustments ... go ahead and do it" | I read the line "[Request interrupted by user]" as a cancel and answered "No response requested". The grant stood and 13 hours went unused | An interrupted marker never cancels the text before it. A grant to act unattended gets a written plan within 15 minutes and the work starts that night. Owner: Claude lead. Memory `interrupted-marker-is-not-a-cancel` (written today); a one-line `CLAUDE.md` rule needs Sam's yes. |

Sam also chased progress twice ("how you getting on?" at 11-23 14:41 and "I need to move fast today" at 11-24 06:43): see time sinks.

## By area

**Code and gates.** All 10 Sonnet builds needed at least one fix round (14 in total). The reviewers, not the builders, found the real faults every time. Two of those faults were in the lead's own briefs: the expected-verdict table for the thresholds (3 of 6 real sets disagreed on first build) and the bounce gate hook that ran before any bounce could exist (a 69 minute redo). Check: `Documentation/Plans/BRIEF_CHECKLIST.md` now asks each brief to state what exists at hook time, the coverage count, and a verdict matrix on at least six real sets, and any gate or verdict code gets its last fix round re-reviewed by an executing reviewer.

**Mix building.** The first full run of the finished skill produced a mix Sam liked. The pipeline is not the weak part; the tracklist picker was. The kick check and Sam's ear agreed on blend 1 only through the edge reading (-7.3 ms) while the click said tight; on blend 5 the check warned at 3.3 ms and Sam heard tight; on join 7 the bounce check flagged a deliberate break-into-break. Three disagreements on nine blends is too few to retune anything, so nothing was retuned. They are filed in `listening_calibration.md`, which changes a threshold only when its rows give a counted rule.

**Ops.** The first pipeline run on the fresh mix used 22 minutes before it hit the grid gate on two tracks I had not checked first. Check: run the beat-grid gate on the pool before any stem run (one line in the pick manifest card). A stray background `find` on the Romero diagnosis ran 262 minutes; background jobs get a timeout.

## The team

| Seat | Builds | Reviews | Notes |
|---|---|---|---|
| Codex | 0 | 0 | Capped until 2026-11-27. Not available. |
| MiniMax | 0 | 10 delivered (14 launched, 4 failed to start) | Available all session. The 4 failures: a forward-slash path, two console-interrupt exits, a missing `CONSUMED-BY` line. Each relaunched. |
| Kimi | 0 | 0 | Weekly cap hit 2026-11-22. Not available. |
| Claude subagents | 10 Sonnet builds, 14 fix rounds | 10 Opus reviews | Plus 5 research agents and 1 Fable plan. |
| Claude lead | the picks, 5 slide copies, the worktree move, the Get Right refit, 7 suite runs | | Mechanical hand work listed in the timeline section 8. |

**Peer builds this session: Codex 0, MiniMax 0, Kimi 0, Claude subagents 10.** Codex and Kimi were down, which explains their zeros. MiniMax was up and built nothing: the work was repo-reasoned code where its output must be read before use, so review was its right job. The mechanical hand work (five slide copies, 12 worktree folders, repeated suite runs) should have gone to a Haiku subagent; that is the under-use. Every review pair was MiniMax plus an executing Opus, as the briefs said, followed by the lead's own run.

## Claims that turned out wrong

The check, not a second note, is the fix (details in the timeline section 4): the "18 seconds" figure was 24 to 35; the replay was "cheaper on 5 of 8" and was 4 cheaper, 1 dearer; the apply-mode gate was "forgery proof" and four forgeries passed; join 7 was "a real problem you will hear" and Sam liked it. Check: a timing or count in a report is read from the same run's output in the same message; a report-only check's flag is worded "measured X", never "a real problem". Owner: Claude lead.

## Disagreements with the outside view

1. **MiniMax F4, "one theme and a started build".** Wrong in the part about themes: I offered four themes with a question card and Sam chose "Rise: house to techno". The timeline said "not in the material" because the card is not in my logged replies. Right in the part about "12 tracks": I built 10 and never said so.
2. **Edge estimator gets verdict authority.** I disagree. The edge reading matched a flam on blend 1, but an earlier copy with edge +9.0 was the one Sam rated tightest of three, so it is not a clean signal. One blend is not a rule. The calibration table decides.
3. **A review of every fix round.** Half right. The final fix commits were checked only by my own runs, so gate and verdict code gets a re-review. Doing it on every round costs about 10 minutes each for builds that were already clean.
4. **"Claude runs it alone" caused the mish mash.** The bypass was not the cause: no peer would have chosen better tracks. The cause was the picker's defaults and no manifest, which the manifest card fixes.
5. **A heartbeat every 15 minutes.** I agree with the 15 minute plan for unattended work. For waiting notices I adopt a count: 88 notices arrived and 31 got a "nothing new" reply; the number to reach is zero replies to a notice that adds nothing.

## The changes that matter most, in order

1. **Pool manifest in `Tools/playlist_builder.py`** (active filters, pool size, genre and era spread, repeats against earlier mixes, requested n against built n, grid-gate result per pick). Owner: Sonnet builder, MiniMax and lead review. Card on the Master Board.
2. **Interrupted marker rule.** Memory written today; one line in `CLAUDE.md` needs Sam's yes.
3. **Merge-to-skill line in `/orchestrate`.** Needs Sam's yes (global skill).
4. **`listening_calibration.md`**: written today with the nine blends; updated after every listening; a threshold changes only when the table gives a counted rule. Owner: Claude lead.
5. **`BRIEF_CHECKLIST.md`**: written today; hook precondition, data coverage count, real-set verdict matrix, executing re-review of the last fix round on gate code. Owner: Claude lead.
6. **Mechanical hand work to a Haiku subagent** and a timeout on background jobs. Owner: Claude lead.

## Not verified

The timeline marks as "not in the material" what Sections V4 and V5 were in the new-mix run and the cause of the 16:08 "search glitch" (my reading: the picker's design, see above). The standings table in `Brain Performance.md` was not re-tallied, as in the last six sessions.
