# Session self-audit: 2026-11-21 14:23 to 2026-11-22 08:38, Home PC

**Brain:** Claude (Opus 5.5). **Why this ran:** the session ran 18 hours (the session-end hard gate).
**Material:** `self_audit_extract.py` on `ae1bb508-91c4-4ee2-9b38-382e0f78228c.jsonl` printed: Sam messages 10, my replies 74, peer calls 0 (the extractor does not see queue_runner launches, so the ledger is the record: 9 peer calls), subagents 0, task notes 19, errors 16, flagged claims 10, gaps over 90 minutes 3.
Timeline by a Sonnet subagent (`self_audit_timeline_facts.md`, beside this file). Outside view by MiniMax, written first and landed at 08:43 (`self_audit_minimax_view.txt`).

## The one-line finding

I started a heavy analysis on the Home PC without checking free memory, the PC froze, and Sam had to hard-restart it: a lesson already written in two memories (`home-pc-memory-ceiling` and `home-pc-commit-memory-exhaustion`, both 2026-09-29) and in CLAUDE.md. Neither memory was in any MEMORY.md index, so neither ever loaded. The note is not working and has to become a check in the pipeline. (Done now: the ceiling note is updated and indexed in both indexes; the check itself is item 1 below. Sam ran `chkdsk D: /f` on 2026-11-22, which repaired the damaged folder.)

## What went well (keep doing it)

- Sam's verdict became shipped work the same day: the planner is the default arranger (merge 10d7c6d) and the first live mix on it passed every gate (planner 8 of 8, 61 MixPlan checks).
- Codex's round 1 answered BLOCKED instead of building the wrong thing: the planner needs groove caches, so the default had to be groove-only. A builder that stops is worth more than one that guesses.
- Peer findings were checked, not obeyed: two round 1 claims (cksum spacing, `-ef` spelling) were rejected after Kimi measured them.
- After the freeze, memory was measured stage by stage (separation 1.9 GB, beat-grid step 6 to 7 GB, previews +0.5 GB per track) before anything else was changed. That found the preview leak and the real cause.
- Sam was asked before closing his apps (Dropbox, Chrome), one question at a time.

## What Sam had to catch or decide

| Sam's note | What was missing | Next time: the check, and who owns it |
|---|---|---|
| 18:28 "the computer froze ... I left the computer on ... before turning it off" | No free-memory check before the 14:59 start; no guard. The rule existed (memory 2026-09-29, CLAUDE.md "check free commit memory before a long local job"). | Phase 1a refuses to start below a free-memory floor and runs its own guard (kill below a floor, resume from per-track caches). Moves `run_guarded_command.py` from D: scratch into the repo. Builder: MiniMax or Kimi (Codex is capped until 2026-11-27), Claude reviews. |
| 20:58 "Quit Dropbox", 22:03 "Close what you need" | The memory budget was found track by track, so Sam was asked twice, hours apart. | The same preflight prints the five largest processes and the free memory against the measured need (about 7 GB for the beat-grid step), so one ask happens at the start. Same owner. |
| 18:34 "ok let me know when it's done" | Four finish times given and all wrong (see claims). | ETA only from `Automation/render_eta.py` reading the guard log (CLAUDE.md already says so); owner Claude. A Stop-hook check is proposed below (needs Sam's yes). |

## Claims that turned out wrong

- 18:33 "finishing around 8 to 8:30pm"; 19:07 "around 19:40"; 19:10 "roughly 35 minutes"; 20:58 "35 to 40 minutes for 3 tracks". Real: 9 of 9 at 22:26. Each was worked out from an earlier per-track time while guard kills and memory changed under it. This repeats the CLAUDE.md rule "say only timings you have measured ... run with render_eta.py", which was not used.
- 18:55 "running with room to spare": the guard stopped it at 19:09 when a Kimi review started on the same PC.
- 20:09 "quit Dropbox for about 20 minutes": Dropbox restarted itself; found at 21:26.
- Not wrong (the timeline listed it): "430 tests" then "453" were different test scopes.

## Area notes

**Code.** Two merges, both built by Codex and reviewed outside the builder: planner default (Kimi and MiniMax round 1 CORRECTIONS, Kimi round 3 SOUND) and `--no-previews` (Kimi SOUND plus my review). The queue_runner ledger says "VERIFY-SKIPPED: no declared validator" on all three collections because it only knows TypeScript; I ran the suites myself (453 passed after the planner merge), so the code was verified, but the ledger line does not show it.

**Ops (memory).** The freeze cost 3h11m (15:10 to 18:22) and damaged one directory entry on the exFAT D: drive. The guarded analysis then took 3h54m (18:32 to 22:26) because other jobs on the same PC (another session's Codex and Kimi runs, Dropbox, Chrome) kept pushing free memory under the floor. The guard I wrote watches free physical memory; the 2026-09-29 note says commit memory is what fails first on this PC, so the repo version should check both.

**Mix.** Built and validated; not heard. Two hints-gate warnings (no outro found on We Met Offline and on VLAD Interlude) are the places to listen first.

## The team

| Seat | Builds | Reviews | Notes |
|---|---|---|---|
| Codex | 3 collected (planner r2 retry, r3, no-previews) + 1 BLOCKED (correct) + 1 empty (r2, retried) | 0 | Capped at 00:46 by another session's builds; resets 2026-11-27 11:56. |
| Kimi K2.7 | 0 | 3 (r1 CORRECTIONS, r3 SOUND, no-previews SOUND) | Up for all three; another session used its window up at 01:26. |
| MiniMax M3 | 0 | 1 (r1 CORRECTIONS, 43 KB) + this outside view | Up all session. |
| Claude subagents | 0 | 0 (+1 timeline for this audit) | |
| Claude alone | the two guard scripts (about 150 lines, D: scratch), `resolve_masters.py`, memory samplers, the absolute-path fix and test (6500023), merges and docs | final review of every merge | |

**Under-used:** MiniMax as a builder. The guard scripts were buildable, Codex and MiniMax were both up at 18:30, and I wrote them alone because they were urgent and lived in scratch. That reason holds for a first version, not for the repo version, which goes to a peer.

## Where the outside view and I differ

- Agree: the memory gate (its point 1 and answer b), the ETA rule (9), the ledger recording what the seat actually said (7: queue_runner logged "NOTHING WRITTEN - suspect the launcher" when Codex had answered BLOCKED), and MiniMax being under-used as a builder.
- Disagree, resume from disk (3): already the behaviour. The 22:04 rerun was the guard killing the job below its floor; per-track caches meant finished tracks were not redone.
- Disagree, two reviewers before the no-previews merge (8): the rule is two brains other than the builder, and it had Kimi and me.
- Disagree, zero-byte copy check (12): the copy did fail loudly (OSError 22 on an online-only Dropbox file) and the track was dropped on purpose; the hash check already exists.
- Disagree, MiniMax for copying nine files (11): a one-command copy with a hash check costs less to run than to brief. The real under-use was the guard build.
- Partly: a peak table file with its own test (2) is more than needed; the measured floor as one constant in the guard, with the numbers in BURN_LIST, does the job.

## The changes that matter most, in order

1. **Check, in the repo:** Phase 1a and `Tools/run_mix_pipeline.sh` refuse to start below a free-memory floor (physical and commit) and run under the guard, printing the top five processes at the start. Replaces the note that failed (memory `home-pc-commit-memory-exhaustion`, now pointing at this check). Builder: MiniMax or Kimi until Codex is back on 2026-11-27; Claude and one other seat review.
2. **Routing rule:** on the Home PC, never start a heavy analysis while another session's peer jobs are running; read `room-usage-history.jsonl` and running processes first. Owner: Claude, applied in the same preflight.
3. **Trigger (global, needs Sam's yes):** the reply-style Stop hook flags a finish time or "minutes left" that is not followed by a `render_eta.py` reading.
4. **Check (global, needs Sam's yes):** queue_runner reads a per-project validator command (for this repo `python -m pytest Tests -q`), so Python collections are verified, not skipped; and it logs BLOCKED when the answer says BLOCKED instead of "suspect the launcher".
5. **Count:** the self-audit extractor counts queue_runner launches as peer calls (it printed 0 here against 9 in the ledger). Global skill, needs Sam's yes.

_Validated: extractor exit 0 with the counts above; timeline file and MiniMax view exist beside this report; builds per seat counted from the ledger._
