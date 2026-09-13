# Sweep result: Automated DJ Mixes, 2026-09-13 (Claude/Fable lens)

Repo root `R` = `G:\Wired Masters Dropbox\Sam Wills\0.1---GIT HUB---\Automated DJ Mixes` (all
`R\...` citations below are under it). Evidence base: AI_CONTEXT.md in full, TOOLBOX.md, the
last 71 log entries, the swap-first review trail, Heldout Replay Plan V2 + Result 01, tonight's
(21:15-21:16) A/B/C build logs and reports, RENDER_CHECK.md / REVIEW_A.md, pair_history.jsonl,
and the code sites named below. Nothing in `Documentation/Plans/burn-list-2026-09-13/` was read
beyond the directory listing, to keep this lens independent.

**Headline.** The gates now catch the *defect* classes Sam used to find by ear (silent bits, bad
loops, unity faders, bass ramps). What is left of his manual cleanup is overwhelmingly *taste
geometry* - where the bass swaps and how long the overlap is - and that is not on trial in the
blind listen as built, because all three sides share every swap beat. The shortest path to less
cleanup is therefore two parallel lanes, not one: (1) get the listen done (it is blocked on
three unbounced ALS files and one false-positive gate FAIL), and (2) start item (c) now, because
its "real case" has already surfaced.

---

## (A) Genuinely broken or incomplete pipeline behaviour

**A1. The grid_fold FAIL on Side A is almost certainly a measurement artefact, and it will fail
B and C too.** The three probe regions in RENDER_CHECK.md line 17 map (at the flat 130 BPM) to
beats 740-868, 2036-2196 and 4112-4240 = solo Freejak, solo Jay de Lys, solo Enzo. Two of them
read -186.4 / -189.0 ms (agreeing within 2.6 ms, = -0.40 beat, the fold of a swung offbeat), the
third +42.8 ms (the librosa constant bias the docstring already says is fine). A real 186 ms
warp offset on Freejak would make T1's 40-bar blend with Yellody a half-beat train wreck; Sam's
ear, REVIEW_A's 8/8 visual verdict, and a clean `boundary_click` all say it is not. Cause:
`_grid_fold_median` (`R\Source\render_check.py:2160-2193`) takes a full-mix <150 Hz onset
histogram and keeps the dominant cluster - on tech house the dominant sub-150 Hz onset is the
offbeat bass, not the kick, and this is the first tech-house render the gate has ever measured.
This is the already-carded "offbeat-lattice aliasing" item, now with a reproducing case. Fix
direction: fold the cached drums-stem kick band (the `__drumsstem.npz` sidecar already exists
for every track) instead of the full mix, or reject clusters far from the cross-region
consensus; the pipeline's own ruler hierarchy is drum-stem kicks > ticks > librosa and the gate
is using the lowest ruler. Because `gates=True` on flat maps (`render_check.py:2259-2279`), this
one check turns the whole verdict to FAIL, and Plan V2 requires all automated checks pass before
anything is heard. **Priority: HIGH for the immediate step** (it blocks/muddies the listen and
erodes trust in the gate), medium for cleanup directly.

**A2. Every bounce on disk is stale; none of the three sides that will be listened to has been
rendered.** `Output\10.09.26 Tech House Heldout Mix A.wav` is dated 09-11 18:09; it predates the
09-12 leveling + hard-swap fixes (`Mix A_pre-fix-backup.als` 09-12 10:44) and tonight's rebuild
of all three sides (`Output\AB\{A,B,C}\Mix *.als` 21:15-21:16). RENDER_CHECK.md is a verdict on
that stale render. A, B and C all need fresh bounces before `seal_listening_test.py` can run.
**Priority: HIGH - it is literally the next thing.**

**A3. A production (`--tempo-arc`) build is impossible on any project without Mixed In Key
metadata - including this held-out project.** `t.bpm`, `t.camelot`, `t.energy` are populated
only from the MIK DB (`R\Source\propose_arrangement.py:1125-1132`); `--tempo-arc` then raises
`"tempo arc needs a certified BPM for every track"` (`:1299-1301`). Tonight's
`Arranged A_ARRANGEMENT_REPORT.json` has `bpm=None, camelot=None, energy=None` for all 9 tracks
(MIK's UI automation failed on this Home PC, REVIEW_A "Known limitations"). The owned stem grid
already measured every BPM (SECTIONS_STEM JSON). This is the carded 09-01 friction (a); it is
cheap and it blocks turning a winning side into a deliverable. **Priority: MEDIUM-HIGH** (a real
commissioned job on this machine cannot be built to production spec today).

**A4. Sam's one real correction on this project - the T2 swap moved 28 beats earlier - is
reproduced by none of the three sides, and the blind listen cannot detect that.** pair_history
(project `10.09.26 Tech House Heldout`, pair_index 2): claude swap 656 -> sam 628,
`bass_swap_moved:-28beats`, overlap 17 -> 25 bars. Tonight's logs
(`Output\AB\_audit\{A,B,C}_automation.log`) put the T2 swap at arrangement beat 1204 (4 beats
before Freejak's file end) on all three sides; in fact all eight swap beats
(612/1204/1812/2548/3024/3504/3952/4368) are identical across A, B and C - B and C vary only
overlap length / entry. That is by design: `R\Source\automated_dj_mixes\transition_policy.py:84-86`
("The bass swap does not move - only the entry does"). Style selection is likewise still purely
length-based (`R\Source\apply_automation.py:810-816`, confirmed by Codex's review). The House 10
corrections say the same thing: 7 of 9 transitions were `bass_swap_moved` (+/-28..64 beats).
This is the dominant cleanup class and it is untouched. **Priority: HIGHEST for "reduces Sam's
manual work"** - see D2/D3 and B1.

**A5. `boundary_click` has never run on a real production mix.** Every production mix is a
tempo arc; on an arc the check skips every boundary by name (`render_check.py` arc handling;
Master Board line 27: "arguably the biggest remaining hole in the gate"). Tonight's flat A/B/C
builds are the rare case where it does run (clean). Latent: no click has yet been reported on a
flat render. **Priority: LOW-MEDIUM** (latent, but it is the most audible defect class with zero
coverage on real deliverables).

---

## (B) Real opportunities to reduce manual polish work

**B1. The learning loop is still write-only: 16+ real Sam-corrected transitions exist and
nothing reads them to make a decision.** `pair_history.jsonl` now holds House 10 (9), Heldout
(8) and Fresh Mix V2 entries. The only consumer is `find_similar_pairs`
(`R\Source\propose_arrangement.py:1008-1052`), whose result lands in a notes string and a
`similar_history` report field (`:1239-1248`, `:1812-1820`) - annotation, never a choice.
Hardening Tracker line 42 / 86-87 already calls it "a closed circuit". The swap-position rule
that item (c) needs is exactly what this corpus can now be designed and back-tested against
(the learner's geometry bugs were fixed 09-10, so the deltas are trustworthy). **Priority:
HIGHEST long-term; this is the (c) redesign's input data.**

**B2. Automate the bounce - it saves Sam three attended exports now and keeps him blind.**
`/mix` 3.5e says "No script for this step - open Mix A.als and Mix B.als in Ableton Live and
bounce each" (`C:\Users\Carillon AC-1\.claude\commands\mix.md:431-433`), and Phase 5 notes
"currently he bounces by hand" (`:544`). The stated rationale - opening the projects visually
discloses arrangement shape - is an argument for Sam *not* opening them, i.e. for an automated
export: `R\Source\ableton_ui.py:9` already drove File > Export Audio/Video for the 2026-06-12 V2
bounce. Every future render-gate cycle pays the same cost. **Priority: HIGH** (Sam-time +
protocol integrity, every mix).

**B3. Owned key detection as a fallback so `camelot` is never None.** On this project the
sequencer had no keys at all (A3 evidence), so there was no harmonic sequencing; for a
commissioned mix Sam would reorder by key by hand or accept clashes. MIK is the last
desktop-UI-automation dependency in an otherwise "owned, Rekordbox-free" stack, and it is
machine-fragile (works via DB on STUDIO-2 for catalog tracks, fails on the Home PC). Key from
tags already works when MIK tagged the file; a chroma/Essentia estimate as a last resort closes
the hole. **Priority: MEDIUM.**

**B4. Wire `hints_from_stem_result` into `/mix` (2026-09-02 audit item #12, still no owner).**
The derivation exists (`R\Source\stem_detector.py:1362`, `--write-hints` at `:1418`), but Phase
1f still says read four timestamps per track off the DETECT picture by eye (`mix.md:124-154`);
the audit measured ~20 min per project and 2 misreads per 10 tracks. Derive-then-adjust removes
the misread class. **Priority: MEDIUM** (Claude time and error class on every project, on the
path to Sam).

**B5. Playlist-complete recovery lane (carded 2026-07-16, not built).** Arielle Free/Idris Elba
was excluded at 16 ms on a 15 ms gate (REVIEW_A line 4; `_Excluded Audio\`).
`refit_grid_from_stem.py` is the documented escalation for exactly this and is never tried
automatically. In commissioned mode that is one track Sam mixes in by hand, or a client
conversation. **Priority: MEDIUM for commissioned work, nil for the listen.**

**B6. Density gate on entry extension - Result 01's own finding, now testable, and a prediction
worth pre-registering.** Result 01 (`R\Documentation\Mix Patterns Library\Heldout Replay Result
01.md`): Sam's qualification was that the extended entry works "because the break it goes over
is quite chilled out". Tonight's B build extended T2's entry over a host measured at `density
+18.7dB` (`Output\AB\_audit\B_arrange.log:40`) - a dense host by that logic. If Sam dislikes B's
T2, the density hypothesis is corroborated and the shadow gate should go live. Friction: the
density number lives only in the free-text notes; the transition report carries no structured
density field (checked all three `Arranged *_ARRANGEMENT_REPORT.json` - only `in_intro_loop`),
so per-transition attribution after the listen means parsing prose. **Priority: MEDIUM.**

**B7. Transition loudness compensation (Production Polish Backlog #1) - Sam's own hand
technique on every mix, never built.** `R\Documentation\Production Polish Backlog.md:10-25`:
0.25-0.5 dB dip on one side across the overlap plus a gentle low-shelf dip. `mix_predict.py` can
now size it feed-forward. Caveat: no `pair_history` entry records a Utility-gain dip correction,
so check Sam's tweak ALS files for it before building. **Priority: LOW-MEDIUM.**

**B8. End-of-mix tail: the last 7.4 s of the mix are the final master's own fade to -62 dBFS.**
RENDER_CHECK.md line 21 (`exposed_solo` WARN at 38:33-38:40, beats 5011-5028 = Jewel Kid's last
4 bars); the source WAV's last second sits at -62 dBFS (measured). No trailing-silence handling
exists in `als_generator.py` (grep for silence/trim: none). Either trim/fade the final clip's
near-silent tail or exempt a final-track fade from the check. **Priority: LOW** (a few seconds of
manual trim per mix, but every mix).

---

## (C) Hygiene / technical debt (no output-quality effect today)

**C1. Every mix since 2026-08-13 has been built on a git-untracked template picked by an mtime
tie-break.** `_find_template` rglobs `Templates/` and breaks ties by newest mtime
(`R\Source\automated_dj_mixes\orchestrator.py:100-113`). With `min_tracks` 9-11 it now returns
`R\Templates\DJ Mix Template 2026-2 Project\DJ Mix Template 2026.als` (untracked; Live 12.4.3;
1,628 decompressed XML lines differ from the tracked 12.3 root template), and both the House 10
and Heldout `Sections V1.als` carry `Creator="Ableton Live 12.4.3"`, confirming it. rglob also
sees Ableton's `Backup\*.als`. Not a defect (IDs are found dynamically) but nondeterministic
across clones, and `Templates\README.md` is stale (still says a template "is required before the
ALS generator can be built"). Fix: commit or promote the 12.4.3 file and make the template an
explicit setting.

**C2. Docs drift at exactly the next step.** `mix.md:439-446` shows `seal_listening_test.py`
with two positional WAVs; the real CLI is `--side LABEL=path` (repeatable), `--twin-of`,
`--out-dir`, `--seed` (`R\Source\seal_listening_test.py:63-70`). `mix.md:427` says A/B; the code
builds A/B/C. `mix.md:458` says commit the held-out project; `.gitignore:45` ignores
`Test Project/`. AI_CONTEXT "What's Next" (`:1290-1411`) still opens with the 09-10 and 09-01
TOPs and carries May-era items. `Documentation\TOMORROW.md`, `TODO_ARRANGE_MIX.md`,
`PIPELINE_AUDIT.md`, `CODEX_REVIEW.md` are May 2026 artefacts at the documentation root.

**C3. `extract_sections_als.py` parser is silent-broken by XML attribute reordering** (Codex
finding 4, logged-not-fixed in commit 98efcbb; `R\Source\extract_sections_als.py:44`) - would
fall `apply_automation` back to the stale sections JSON.

**C4. `intro_skip_bars` is documented CLOSED but silently ignored in production.**
`mix.md:585` lists it as a closed gap; `propose_arrangement.py:1188-1192` warns that
`align_engine` (the production path, `USE_ALIGN_ENGINE=True`) does not honour it.

**C5. Rule R4 (low sneak) is live and never re-validated.** Trigger `overlap_len <= 80` at
`apply_automation.py:804-807`; the Key Decisions record R4 as 7/9 false positives under its
earlier trigger. It fired on A's T2 (the transition Sam then hand-fixed - confounded, not
proof).

**C6. Flag-OFF decisions still pending:** `LOOP_SELF_SIMILARITY_TIERA` AND-vs-replacement,
`width_cues`, soft rules R2/R4, and `BASS_RESIDUAL` - the flag-flip now has two full mixes of
"zero firings" evidence (House 10 A/B; not set for tonight's heldout builds, none of the three
logs show a residual pass). A decision, not a build.

**C7. Housekeeping beside the rebuilt ALS:** `Mix A_pre-fix-backup.als`, the 614 MB stale
`Mix A.wav`, and a `RENDER_CHECK.md` that no longer describes any current artifact. Also: Phase
1a on this Home PC (GTX 750) is ~9 min/track vs ~90 s on STUDIO-2 - noted only so the wall-clock
is attributed correctly, not re-proposing the deferred cache-reuse item.

**C8. Held-Karp segfault above 15 tracks** (Master Board line 80) - deferred by Sam, unchanged.

---

## (D) Misframed in the brief, or wrong in the settled context

**D1. "That blind listen has NOT happened yet" is only true of this round.** A sealed blind
listen of `sam_v1` vs `interim_v1` was run on 2026-08-12 (`Heldout Replay Result 01.md`): twin
passed, Sam correctly identified the new clip, verdict "technique validated, not promoted, not
shown superior", only 1 of 7 transitions differed so the 5-of-7 criteria never went live.
Tonight's builds are the first where they *will*: B differs from A on 4 of 8 transitions
(T2/T3/T4/T6), C on 7 of 8. Result 01's carried-forward action (density gate) is what B6 is
about.

**D2. The listen cannot adjudicate the thing Sam corrects most.** See A4: identical swap beats
on all sides, by policy design. Run it - it is the gate for entry extension and intro loops,
which are the best-evidenced lessons from Fresh Mix V2 - but set the expectation now that even a
clean B/C win leaves the swap-position correction class (7/9 on House 10, T2 here) unaddressed.

**D3. Item (c) is not "queued only if a real case surfaces that (a) can't handle" - the case has
surfaced.** (a) fixed the crash; the musical choice at T2 is unchanged across all three sides,
and Sam moved it by hand on 09-12. With the learner now trustworthy and 16+ corrected
transitions on file, (c) should be scoped in parallel with the listen, not after it. That also
subsumes settled item (2) (the QUICK_SWAP leftover clip) and the length-only style selection,
all of which are the same Phase-2-freezes-before-Phase-3-decides causality problem.

**D4. The framing question.** "What reduces Sam's manual cleanup" is right, but the evidence
answers it differently from a defect list: defects are now largely gated; cleanup is taste
geometry. So the shortest path is (i) A1 (fix or demote grid_fold on flat maps so the gate stops
crying wolf), (ii) B2 (automate the bounce), (iii) A2 (bounce A/B/C, seal, listen), and in
parallel (iv) B1+(c) designed against `pair_history`. Everything else on this list is
second-order to those four.

**D5. Minor correction to settled item (2):** in B and C, T2 is a 49-bar LONG_BLEND, so the
specific Freejak->HARTY leftover-silent-clip instance will not recur on those sides; the defect
class remains and is still owned by (c).

Nothing found for: "settled context (b) should be revisited" - I agree with both prior verdicts
that (b) is unnecessary for the generated-ALS path.
