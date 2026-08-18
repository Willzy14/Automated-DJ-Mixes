# Kick-Based Section Detection Investigation

**Date:** 2026-08-18 · **Brain:** Claude, with MiniMax M3 as independent second reader on the
git-archaeology and doc cross-referencing (two-lens verification on the regression question).
**Scope:** does Sam's "kick in/kick out on the Demucs tracks" mental model exist in the codebase
today; did it regress; why did it miss a real, measured case on `Sam Leagas - Double Dutch`.
Read-only investigation. No code changed.

**Method note:** Claude did the initial code read/scoping and the numeric reproduction against the
cached Double Dutch stem envelope directly (this needed Python execution, which MiniMax's read-only
sandbox doesn't have). MiniMax independently read the full `git log -p` history and the full text of
`AI_CONTEXT.md` / `ai-activity-log.md` / `TOOLBOX.md` and returned its own citations — used here as a
second, independent confirmation of the timeline and the "was V3 ever formally promoted" question.
Where the two disagree or one adds precision the other missed, that's called out explicitly.

---

## 1. What exists today, with file:line

Sam's model ("kick out 1-2 bars = fill; longer = break; no kick in the first 16 bars = probably
intro; no kick in the last 16 = probably outro; a track that starts with a kick and then drops it at
a fill/break point means everything before was intro") is implemented, close to verbatim, in
`Source/stem_detector.py`:

- **`FILL_MAX_BARS = 6`** (`stem_detector.py:56`) — "kick-out <= this many bars = fill; longer =
  break (Sam's rule)".
- **`_assign_labels(sections, kick_on_bar, bass_pres, mix_norm, outro_start)`**
  (`stem_detector.py:268-327`) is where the rule lives:
  - `stat(s)` (`:272-277`) computes `kf`/`bf`/`ef` as the **mean** of `kick_on_bar` / `bass_pres` /
    `mix_norm` over `s["start_bar"]:s["end_bar"]` — i.e. over whatever span the section-boundary
    step already produced, not a fixed window.
  - `is_drop(s)` (`:283-285`): `kf > 0.6 and bf > 0.5 and ef >= drop_thr`.
  - Pre-first-drop (`:296-300`): `label = "break" if (kf < 0.4 and is_long) else "intro"`, where
    `is_long = (end_bar - start_bar) > FILL_MAX_BARS`.
  - Post-first-drop (`:301-305`): `label = "fill" if span <= FILL_MAX_BARS else "break"` when
    `bf < 0.4 or kf < 0.4`.
  - Hard rules (`:310-326`): every track gets an intro and an outro; intro is top-only (a second
    intro-like pre-drop stretch becomes `build`, not a second intro).
- **Section boundaries** (`stem_detector.py:470-495`): `raw_bounds` = kick-dropout/return cues
  (`_kick_cues`, `:203-224`, fires only for kick-out runs `>= MIN_KICK_OUT_BEATS` = 2 beats) **plus**
  bass-presence toggles (`:474-475`), snapped to a 4-bar grid and merged if shorter than
  `MIN_SECTION_BARS = 4` bars (`_snap_merge`, `:107-116`).
- **`kick_on_bar`** (`:437`) is a per-bar boolean built from whichever kick-presence signal is
  active: the **default stem-energy path** — `_kick_on_per_beat` (`:140-154`), which thresholds the
  Demucs `drums` stem envelope at `KICK_ON_FRAC = 0.80` of a per-track dynamically-found "solid kick
  level" (`_solid_kick_level`, `:119-137`) — **or**, if `--kick-model` is active, the
  **Kick Detector V3** readout via `_model_kick_presence_per_beat` (`:157-200`).

This is all real, current code, and it is a faithful implementation of Sam's model **when the
default stem-energy signal drives `kick_on_bar`**. The question is what drives it in practice.

---

## 2. Regression verdict: architectural, not threshold drift

**No threshold has ever changed value.** Verified independently by Claude (grep over the full
`git log -p`) and by MiniMax (full read of the same log): `FILL_MAX_BARS=6`, `KICK_ON_FRAC=0.80`,
`DROP_REL=0.85`, `MIN_KICK_OUT_BEATS=2`, and the `0.4`/`0.5`/`0.6` constants in `_assign_labels`/
`is_drop` were all introduced once, in `c86240a` (2026-06-08, "stem-based section detector, tuned on
VLAD") or shortly after, and have not been edited since — through the current commit touching this
file, `5de4220` (2026-07-16). `KICK_ON_FRAC` briefly lost a *different* constant it depended on
(`KICK_REF_PCT=90`, a fixed percentile, removed in `37eb2e4` 2026-06-08) in favour of the dynamic
`_solid_kick_level` — but `KICK_ON_FRAC` itself stayed `0.80`. So "the thresholds got weakened" —
the most obvious regression story — **did not happen**.

**What did happen is an architectural swap of the input signal.** Timeline, commit hashes and dates
verified against `git log`:

| Date | Event | Source |
|---|---|---|
| 2026-06-08 | `c86240a` introduces the stem-energy detector with Sam's rule implemented against the raw Demucs `drums` envelope. This is the version whose numbers this report reproduces in §3. | `git log` |
| 2026-07-09 | Activity log records Kick Detector V3 wired in "behind default-off `--kick-model` flag" | `.github/ai-activity-log.md:127-128` |
| 2026-07-15 | `5bb9dd1` is the commit that actually introduces `Source/kick_model_adapter.py` in this repo's history (git shows no earlier commit touching that file) — **6-7 days after** the activity log's 2026-07-09 date for "wired in". This gap is a genuine discrepancy in the record; possible explanations (batched/squashed commits from a longer working session) are plausible but unverified — flagged here rather than papered over. | `git log --format="%h %ad" -- Source/kick_model_adapter.py` |
| 2026-07-15 | `AI_CONTEXT.md:100` (NOTE): *"V3 remains default OFF pending visual/ear verdicts for `Back in the Days`, `Beautiful Mess`, and `Blues`."* Also `AI_CONTEXT.md:976`: *"Keep the model opt-in until `Back in the Days`, `Beautiful Mess`, and `Blues` receive paired DETECT-picture + ear verdicts. Promotion requires no unapproved regression, not just fewer cues."* — an explicit, named promotion gate. | `AI_CONTEXT.md` |
| **2026-07-16** | `ai-activity-log.md:168`: *"corrected the canonical `/mix` boundary after the stale skill launched Rekordbox. Both frozen-sync skills now require MIK-only previews plus `--stem-grid --stem-sections --kick-model`."* **One day** after the opt-in note, `--kick-model` became mandatory in the canonical `/mix` skill (both Claude and Codex brains) — bundled into an unrelated Rekordbox-launch bugfix, not into a dedicated "V3 promotion" decision. `TOOLBOX.md:20` still states this as the canonical path today: *"The canonical `/mix` path uses `--stem-grid --stem-sections --kick-model`."* | `ai-activity-log.md:168`, `TOOLBOX.md:20` |
| 2026-07-16 | `TOOLBOX.md:184` / `AI_CONTEXT.md:98` (NOTE): the V3 adapter is split into a dual readout — **smoothed presence stays "the stable coarse-section clock"**; raw per-beat presence becomes **report-only** `signals.musical_landmarks` for pre-drop gaps *and* "longer kick dropouts". This is a real, documented partial mitigation of exactly the bridging problem in §3 — but it only exposes long dropouts as unselected candidate landmarks, never feeds them back into the section label or `kick_cues`. | `TOOLBOX.md:184,189`, `AI_CONTEXT.md:98` |
| — | **No entry after 2026-07-15, in either `AI_CONTEXT.md` or `ai-activity-log.md`, records "Back in the Days", "Beautiful Mess", or "Blues" actually receiving their paired DETECT-picture + ear verdicts.** Verified independently by both Claude and MiniMax (full-file search of both docs). | absence, both files |

**Verdict: this is a real regression, but not the kind the brief speculated about.** No threshold
was loosened. What changed is that the canonical `/mix` pipeline started feeding `kick_on_bar` (and
therefore `kf`, and therefore `_kick_cues`'s boundary-cutting) from Kick Detector V3's *smoothed*
presence signal instead of the raw stem-energy signal Sam's rule was originally built and tuned
against — and that swap became mandatory in practice one day after its own stated promotion gate
was set, with no record that gate was ever satisfied. Precisely stated (MiniMax's phrasing, which
Claude agrees is the accurate one): *"V3's section-level (smoothed) presence became mandatory in the
canonical `/mix` skill on 2026-07-16, one day after the 2026-07-15 holding note said to keep it
opt-in pending named held-out verdicts, and no later entry in either file records those verdicts
being satisfied."* One caveat that keeps this precise: the bare `orchestrator.py` CLI flag
(`:893`, `action="store_true"`) is still opt-in for a direct/manual invocation — the promotion is at
the *skill* level (what a normal `/mix` run actually does), not the underlying tool's default.

---

## 3. The Double Dutch mechanism — traced and numerically reproduced, not guessed

Track: `Sam Leagas - Double Dutch (Extended Mix) SW V1`, `Test Project/14.08.26/_Stem Analysis/`.

**The cached facts** (`SECTIONS_STEM_Sam Leagas - Double Dutch (Extended Mix) SW V1.json`):
- Sections: `intro 0-16, drop 16-44, build 44-48, drop 48-92, fill 92-96, drop 96-152`.
- `"kick_presence_source": "kick-detector-v3"` — this track was analysed with `--kick-model` active.
- `"kick_cues": []` — **zero** kick dropout/return cues anywhere in the entire 281-second track.
- `"musical_landmarks"`: only two `pre_drop_kick_gap` entries, at bar 47.25-48 (3 beats) and bar
  95-96 (4 beats) — the raw V3 signal barely registers anything in the 31-47 window, only a tiny
  blip at its very tail.

**Claude re-ran `stem_detector.py`'s own, unmodified default-path functions**
(`_per_bar`, `_kick_on_per_beat`, `_median_bool`) against the cached
`__stemenv.npz` envelope for this track (bpm=130.06, downbeat=0.02s, both derived exactly from the
JSON's own bar-to-second mapping). Results:

- **Default stem-energy `kick_on_bar`:** ON for bars 16-30, **OFF for every bar 31 through 47
  inclusive**, ON again from bar 48. This is a clean, correct detection of exactly the dip Sam
  measured by ear (drums envelope 0.41 at bar 30 → 0.06-0.10 through bar 47 → 0.29 at bar 48). Had
  this signal been what drove classification, `_kick_cues` would have cut a boundary at bar 31 and
  the resulting sub-section would have failed `is_drop` cleanly (`kf=0.0` over 31:47).
- **Bass presence stayed ON for the entire dip** (bars 31-43), only toggling off at bar 44 — so the
  *only* boundary actually cut near this dip (bar 44) came from the bass toggle, not from a kick
  cue, and it lands 13 bars into the real dip rather than at its start (bar 31).
- **`stat()` on the actual section as classified** (bars 16:44, i.e. before the bass-driven
  boundary), using the DEFAULT signal for illustration: `kf = kick_on_bar[16:44].mean() = 0.500,
  bf = 1.000, ef = 0.609` — already below `is_drop`'s `kf > 0.6` bar under the honest signal. But the
  real run was classified `drop` (`is_drop()` returned True), which is only possible if the *actual*
  V3-derived `kick_on_bar` reads meaningfully more "on" across bars 16-44 than the raw drums envelope
  shows — i.e. V3's smoothed signal disagreed with the raw envelope for this stretch.
- **Why:** `Source/kick_model_adapter.py:271-276` (`presence_per_beat`) does correctly isolate drums
  via its own Demucs pass first (`_drums_from_mix`, `:221-247`) — it is **not** reading the raw mix
  directly; that hypothesis is ruled out. But its output then passes through
  `smooth_presence(raw, fill_off_beats=6, drop_on_beats=1)` (Kick Detector project,
  `Source/presence_postprocess.py:29-45`, sibling repo): this **first bridges every OFF-run of
  length <= 6 beats to ON**, **then** removes any remaining ON-run of length <= 1 beat. For
  `kick_cues` to be empty across the *entire* 281-second track (not just this one dip), every
  off-run in V3's raw per-beat activation for the whole file must have been <= 6 beats after any
  chained bridging — i.e. the raw activation must flicker (brief on-blips breaking up what should be
  one continuous 16-bar dip) rather than producing one sustained low run. **This last link is
  inferred from the smoothing math and the observed zero-cues output, not independently measured**
  — the raw per-beat activation values aren't cached anywhere, and reproducing them would require
  running the CRNN inference itself, which was out of scope for a read-only investigation.

**Mechanism, one paragraph:** the dip is real and the default Demucs-drums-stem signal detects it
perfectly, but this run used Kick Detector V3, whose per-beat activation feeds a debouncing filter
(`fill_off_beats=6, drop_on_beats=1`) that is designed to smooth kick presence into stable stretches
for section-cutting — and for Double Dutch that filter fully erased a genuine 16-bar kick-out,
producing zero kick-dropout cues anywhere in the track, so no section boundary was ever cut at bar
31. The only boundary near the dip came from a bass-presence toggle at bar 44 (bass never actually
left during the dip), so 13 of the dip's 16 bars stayed folded into the surrounding "drop" section,
and `kf` for that block was computed as V3's own (apparently still mostly-"on") signal rather than
the true, mostly-"off" drums-envelope reading — clearing the `kf > 0.6` bar for `is_drop`. This is
exactly the class of loss the 2026-07-16 dual-readout fix (`TOOLBOX.md:184`) was aware of and
partially addressed — it preserves the raw dropout as a report-only `musical_landmark` — but a
report-only, unselected candidate does not change the section label Sam actually looks at on the
DETECT picture, and (per the separate 2026-08-17 Wiring Audit, `Documentation/Reviews/2026-08-17
Wiring Audit.md:60,64`) `musical_landmarks` and `kick_cues` reach different, mostly-disconnected
downstream consumers anyway.

**Also confirmed:** `Documentation/Golden Sections/` contains only a `README.md` — the regression
gate (`regress_section_detection.py`) that should have caught a change like this returns 0 checks /
PASS on an empty fixture set (already found by the 2026-08-17 Wiring Audit, independently confirmed
here by directory listing). Nothing would have caught this even if the promotion gate had been
checked mechanically instead of by memory.

---

## 4. Recommendation — smallest change that fixes Double Dutch and its class

**Do not touch any threshold** — none are wrong, and none have drifted. The fix is at the signal
level:

**Feed `_kick_cues` (boundary-cutting) and `_assign_labels`'s `kick_on_bar` (classification) from
the RAW V3 per-beat presence, not the smoothed/bridged one, when `--kick-model` is active** — i.e.
swap which half of `KickPresenceReadout` (`raw` vs `section`, `kick_model_adapter.py:30-32`) feeds
`stem_detector.py:437`'s `kick_on_bar`. This is a small, local change (the dual readout already
exists and is already plumbed through `stem_detector.py:395-436`) and it makes V3's section behavior
match what the DEFAULT stem-energy path already does correctly for Double Dutch: neither the default
path nor `_kick_cues`/`_assign_labels` currently do any bridging of their own, so switching V3 onto
its raw signal brings it into the same regime that already passes this exact case.

The `fill_off_beats=6`/`drop_on_beats=1` smoothing is not wrong in isolation — it is designed to
kill 1-beat flicker (the DEFAULT path does the same thing differently, via `KICK_SMOOTH_BEATS=3`
median-smoothing at `stem_detector.py:84-89,153`) — the bug is specifically that `fill_off_beats=6`
bridges gaps up to **1.5 bars**, which is far short of the 16-bar break Sam's own rule (`FILL_MAX_BARS
= 6 bars`) says should already read as a break, not a fill. If raw-signal flicker turns out to be the
real problem (unverified per §3), the more targeted fix is tightening `fill_off_beats` toward
`stem_detector.py`'s own existing constants (`MIN_KICK_OUT_BEATS=2` beats, not 6) rather than
bypassing V3's smoothing entirely — but confirming that requires actually inspecting the raw
per-beat activation for this track, which this investigation did not do.

Either way, before shipping any change here: populate `Documentation/Golden Sections/` (currently
empty) with at least the Double Dutch case as a blessed fixture, so `regress_section_detection.py`
stops being a vacuous PASS and this specific regression class gets a permanent regression test.
