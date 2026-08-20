# First Render Check - Mix V10 (2026-08-20)

**Brain:** Claude
**Subject:** `Test Project/14.08.26/Output/14.08.26 Mix V10.wav` (860 MB, 44.1 kHz / 24-bit stereo, 143,380,125 frames = 54:11) - Sam's real bounce of the mix he listened to in the car 2026-08-19.
**Why this exists:** the 2026-08-17 audit named "nothing checks the rendered audio" as the biggest structural hole. Every existing gate reads the SOURCE wavs - a prediction from the same arithmetic being checked. This is the first-ever measurement of the render itself. Read-only prototype: no Source/ or Tests/ changes; scratch scripts listed at the bottom.

## TL;DR

**Both of Sam's ear-notes were independently located by measurement.**

1. "The loops will cut in the wrong place" -> three loop-insert splices drop the level off a cliff mid-flow (-6.9 / -8.9 / -7.6 dB in a single beat, at 4:43, 20:15, 49:54), each also jumping 32-40 source beats forward into different outro material; plus the Nappp tail loop is a **3-bar loop played 8 times** (24:24-25:04) - it audibly restarts a bar early against the 4-bar phrase norm every 5.6 s.
2. "One of the loops had a big area of space in it. A silent bit." -> **22:15.5-22:21.5**, floor **-46.7 dBFS** at 22:19.0: Nappp's own breakdown (source beats 385-397, verified against the source WAV), rendered **completely solo** - nothing is layered under it for ~6 seconds. Secondary candidate: the Vente tail loop (41:52-42:15) repeats a 4-beat, -7 dB hole seven times.

**The render is technically clean.** All 26 planned clip/loop boundaries are click-free (a null test against neighbouring beats proves the "steps" at boundaries are kick attacks, not splice clicks), loops repeat verbatim (envelope r = 0.99), the beat grid holds for the full 54 minutes, and there is no unplanned digital silence. Every defect found is a **plan-level musical decision** faithfully rendered - which is exactly why a render gate is needed: the existing document-level checks all passed this mix.

---

## Ground truth used

- Beat clock: flat 128 BPM confirmed from the ALS (`<Tempo><Manual Value="128">` plus a flat two-point tempo envelope) -> arrangement beat * 0.46875 s = render seconds. Arrangement is 6,932 beats = 3249.4 s; render is 3251.25 s (1.9 s render tail). Beat 0 = t 0 verified empirically: kick-onset fold onto the grid gives a constant +35-39 ms median phase in solo regions at 4 min, 33 min and 51 min (the constant is librosa onset-latency bias; its *consistency* across 54 minutes is the proof the grid never drifts).
- Cut list: extracted all **131 arrangement clips** (track, arr start/end, source start/end) directly from `14.08.26 Mix V10.als` - not from the arrangement report. The report's 6 loops and 11 transitions all reconciled with the clip list exactly.
- Plan artifacts: `ARRANGEMENT_REPORT_V10.json`, `Visualisations/REVIEW_V10.md`, AI_CONTEXT 2026-08-19 evening entry.

## Defect table

| # | Time | Arr beat / bar | Where | Class | Measured | Confidence |
|---|------|----------------|-------|-------|----------|-----------|
| D1 | 4:43.1 | 604 / 151 | T1 swap = Soulsearcher loop insert (L1) | Level-cliff splice + content jump | -6.9 dB step in one beat (-17.7 -> -24.5 dBFS); source jumps +40 beats (604 -> 644); ST-LUFS dip -4.6 dB | High |
| D2 | 20:15.0 | 2592 / 648 | T4 swap = Come Get Up loop insert (L3) | Level-cliff splice + content jump | **-8.9 dB** step in one beat (-17.2 -> -26.1); source jumps +32 beats (932 -> 964); ST-LUFS dip -6.1 dB, then a sustained -8 dB sag into Nappp | High |
| D3 | 49:54.4 | 6388 / 1597 | T11 swap = Bad Behaviours loop insert (L6) | Level-cliff splice + content jump | -7.6 dB step in one beat (-18.1 -> -25.6); source jumps +40 beats (540 -> 580); ST-LUFS dip -6.6 dB | High |
| D4 | 24:24.4-25:03.8 | 3124-3208 / 781-802 | L4 Nappp tail loop, under T5 | Wrong-length loop period | 12-beat (3-bar) loop x 8 plays; render beat-energy autocorrelation peaks at lag 12 with r = 1.00 (lag 16: only 0.72) - restarts one bar early vs the 4-bar phrase norm, 7 times in a row | High |
| D5 | 22:15.5-22:21.5 | 2849-2861 / 712-715 | Nappp solo (between T4 end and T5 start) | Exposed source breakdown = "the silent bit" | 6.0 s below -30 dBFS, floor **-46.7 dBFS** at 22:19.0; mapped via clip list to Nappp source beats 385-397 and confirmed present in the source WAV at the predicted position (source floor -35 dBFS) - it is Nappp's own breakdown playing **solo** | High |
| D6 | 41:52.5-42:15.0 | 5360-5408 / 1340-1352 | L5 Vente tail loop | Loop with a hole in it | 2-bar loop x 7 minus nothing technically, but content is 4 loud beats + 4 beats at -24 dBFS (-7 dB below the loud half) repeating every 3.75 s | Medium |
| D7 | 49:54-50:20 + 4:43-4:56 | 6388-6444, 604-632 | L6 / L1 | 7-bar loop length | 28-beat (7-bar) loop; Bad Behaviours plays it twice (period visible in beat-energy autocorr, lag 28 r = 0.75) - one bar short of the 8-bar norm | Medium |
| D8 | exits at 20:22.5, 42:15.0, 50:20.6 | 2608, 5408, 6444 | L3/L5/L6 loop exits | Jump-back splices | +4.3 to +4.7 dB instant recovery, source rewinds 48-68 beats and **replays material already heard** (e.g. Bad Behaviours 580-608 heard twice, then 540-608 replays through it a third time) | Medium (structural fact certain; audibility partly masked by fades) |

Non-defects worth recording (checked, clean):

- **Zero splice clicks.** Every loop iteration seam and every swap point: max sample-step within +/-2 ms of the boundary is statistically in line with the on-beat steps of the surrounding 32 beats (kick attacks). Worst global first-difference outliers all sit at half-beat offsets = offbeat percussion.
- **Loops repeat verbatim** (Fish Go Deep iteration-vs-iteration waveform r = 0.92, envelope r = 0.99; Bad Behaviours envelope r = 0.96 with Double Dutch layered under).
- **No unplanned silence.** The only <-60 dBFS run is the final 1.8 s render tail.
- **No confirmed kick flam.** The probe (Source/probe_render_flam.py method) flags a 100-130 ms second cluster on T2/T4/T6/T7/T11 - but a control run on a no-transition window (Renegades solo, 10:43-11:43) produces the identical signature from single-track percussion. The probe as-is has a false-positive mode on shuffled/percussive program; treat those five flags as unproven. (Gate lesson: flam detection needs a per-track control baseline before accusing a transition.)
- **Grid integrity end to end** - kick fold median +35-39 ms (constant detector bias) at 4, 33 and 51 minutes.

## Sam's ear-notes: cross-reference

**Note 1 - "the loops will cut in the wrong place": CONFIRMED, twice over.**
- The *place* is wrong in level: D1/D2/D3 - three of the six tail loops source their loop from outro material 7-9 dB quieter than the drop playing one beat earlier. A human DJ would never cut from a running drop to quiet outro material mid-bar; the plan does it at 4:43.1, 20:15.0 and 49:54.4, and the render proves it is a one-beat cliff each time.
- The *length* is wrong in phrase: D4 - the Nappp loop is 3 bars long and plays 8 times (24 bars of 3-bar periodicity, 24:24-25:04). Its source window (beats 660-672) also starts one bar past a 16-beat boundary. D7 - the 28-beat (7-bar) loops in L1/L6 are the same class, milder.
- REVIEW_V10 already had a nose for this ("Fish Go Deep tail 4b x8 and Nappp tail 12b x7 - both within the x8 cap but worth an ear") - the render check turns "worth an ear" into numbers, and notably clears Fish Go Deep (its 1-bar loop is level-matched, +1.2 dB step, benign) while convicting the ones the cap logic passed.

**Note 2 - "one of the loops had a big area of space in it, a silent bit": FOUND - D5 at 22:15.5-22:21.5.**
- Measured: ~6 s below -30 dBFS bottoming at -46.7 dBFS - the quietest interior moment of the whole mix (ST-LUFS -31.8; the next-quietest moment anywhere in the 54 minutes is 4.3 dB louder, at 35:05 inside Emotions' own break). In a car this is silence.
- It is not technically *in* a loop - it is Nappp's own breakdown, playing solo 96 bars after Nappp enters, in the stretch between the T4 blend and the T5/Nappp tail loop. From the driver's seat, mid-long-blend territory with loop inserts on both sides, "one of the loops" is exactly how it would file itself in memory. The measurable, nameable defect: **the arrangement leaves a 6-second near-silent source breakdown fully exposed with nothing underneath it.**
- If Sam's memory really does mean "inside a looped region", the runner-up is D6: the Vente tail loop repeats a 4-beat, -7 dB hole seven times (41:52-42:15). Both are real; both are "space"; D5 is the only one that is *silent*.

## What the render shows that no document-level check caught

1. **Loop-source level mismatch (D1/D2/D3).** The loop visualiser checked loop content in isolation ("no silent/dissipating flags") - true, and useless here: the defect is the *step* between the material before the splice and the loop source, which only exists in the assembled render (or in a source-level diff the pipeline never does).
2. **Solo-exposure of a source breakdown (D5).** Section detection knows Nappp has a break; nothing checks what else is playing when a break goes past. Rendered LUFS floor -46.7 dBFS solo. The stored memory note ("long quiet looped intro entries work over a chilled break, not a busy one") is about using breaks as entry beds - this is the inverse failure: a break with no bed at all.
3. **Loudness trajectory.** Integrated approx **-16.6 LUFS**; ST-LUFS dips > 1.5 dB at 7 of 11 transitions (T1 -4.6, T2 -2.1, T4 -6.1, T5 -4.1, T8 -2.8, T9 -2.1, T11 -6.6), the three worst coinciding with D1/D2/D3. T4 is the energy low-point of the mix: cliff, dip, then a sustained -8 dB sag into Nappp solo territory that does not recover for ~2 minutes.
4. **Replay structure audibility (D8).** The clip list shows four insert-then-rewind constructions that replay 28-68 beats of already-heard material; the render shows the rewind moments as +4-5 dB instant recoveries. Plan reports list loops but never state "this material will be heard twice".
5. **A probe calibration fact:** the kick-flam detector needs a control baseline (see non-defects) - discoverable only by running it against a real render.

## Render gate verdict (Phase 5 proposal)

**Cost measured on this machine (STUDIO-2, CPU only):** the full-file streaming sweep (per-beat RMS, 100 ms RMS/peak, K-weighted loudness, sample-diff click metric - one pass, constant memory) took **15 s** for 54 minutes of 24-bit audio. Targeted boundary re-reads, null tests, loop autocorrelation and LUFS trajectory add ~2 min. The flam probe is the slow item (librosa loads per transition, ~3 min for 11). **Whole gate < 5 min, no GPU.**

**Where it hooks in:** `/mix` Phase 5, immediately after the bounce lands (the pipeline already knows the ALS path and the report path; the WAV path is the bounce log's output). Inputs: render WAV + Mix ALS (clip list is the ground truth - read it, do not re-derive geometry from the report) + arrangement report (for transition metadata). Output: `Output/RENDER_CHECK_V<N>.md` + `.json` defect table in this format, gate status PASS / WARN / FAIL.

**Proposed checks and thresholds** (all validated against this render):

| Check | Source | Threshold | Status |
|---|---|---|---|
| Hard silence interior | 100 ms RMS | < -60 dBFS for > 0.5 s outside head/tail | FAIL |
| Boundary click | +/-2 ms sample-step vs 32-beat on-beat null | > 1.5x neighbour max AND z > 4 AND click-shaped | FAIL |
| Level-cliff splice | per-beat RMS step at every ALS clip boundary | worse than -6 dB into a loop insert | WARN (caught D1/D2/D3) |
| Exposed quiet solo | ST-LUFS + clip overlap count | < -30 dBFS for > 3 s with exactly one clip playing | WARN (caught D5) |
| Loop period sanity | beat-energy autocorr over loop span | dominant lag not 4/8/16/32 beats | WARN (caught D4; plan-time preventable too - see below) |
| ST-LUFS transition dip | K-weighted 3 s window | dip > 3 dB vs pre-overlap baseline | WARN (caught T1/T4/T5/T11) |
| Loop verbatim + grid fold | iteration envelope corr; kick fold consistency | r < 0.9; fold median drift > 30 ms between probes | FAIL |
| Kick flam | probe + per-track control window | flam cluster present in overlap AND absent in both tracks' solo controls | WARN (not yet trustworthy without the control - do not ship the probe bare) |

**Promotable from today's scratch (paths below):** `pass1_envelopes.py` (the sweep - promotable nearly as-is), `extract_clips.py` (ALS clip-list ground truth), the null-test logic in `pass2_click_null.py`, and the loop-period/level-step/LUFS checks in `pass2_silence_loops.py` / `pass3_flam_lufs.py`. The flam probe needs the control-baseline fix first.

**Two of the four headline defects are cheaper to kill at plan time** and should ALSO become Phase 2 rules, with the render gate as the backstop: (a) reject/penalise tail-loop windows whose mean RMS is > 4-5 dB below the material at the insert point (kills D1/D2/D3 - the analysis cache already has per-beat RMS of every source); (b) reject loop lengths that are not 4/8/16/32 beats (kills D4/D7 - the 12-beat and 28-beat windows should never have been candidates).

## Scratch artifacts (session scratchpad)

`C:\Users\Carillon\AppData\Local\Temp\claude\C--Users-Carillon-Wired-Masters-Dropbox-Sam-Wills-0-1---GIT-HUB----Automated-DJ-Mixes\3226d5df-29b2-4c80-a16a-ed8ed4b6e472\scratchpad\`

- `pass1_envelopes.py` - streaming sweep -> `env_rms100.npy`, `env_peak100.npy`, `env_kms100.npy` (K-weighted), `env_click10.npy`, `env_beat_ms.npy`, `env_meta.json`
- `extract_clips.py` -> `v10_clips.json` (131-clip ground-truth cut list from the ALS)
- `pass2_silence_loops.py` - silence scan + per-beat loop tables
- `pass2_clicks.py` - global click outliers + boundary discontinuity first pass
- `pass2_click_null.py` - neighbour-beat null test + click-shape classifier
- `pass3_flam_lufs.py` - flam probe x 11 transitions, grid-fold checks, ST/integrated LUFS
- `pass4_controls_source.py` - flam control windows, Nappp source-dip confirmation, replay correlation, dip contour

Scratchpad is session-scoped: anything worth keeping must be promoted before it evaporates.
