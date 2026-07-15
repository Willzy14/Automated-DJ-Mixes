# Mix Patterns Library

Cross-project evidence library for section-aware DJ arrangement. Finished Ableton sets are
read-only evidence of real mix decisions; they are not copied into the active arranger as rules
until the extraction has been reviewed and the candidate rules pass held-out tests.

## Files

| File | Purpose |
|------|---------|
| `MIX_PATTERNS.md` | Human-readable rules derived from reviewed evidence and corrections |
| `pair_history.jsonl` | Machine-readable record of reviewed transition pairs |
| `genre_priors.json` | Validated style-conditioned priors; stays empty until the gates below pass |

## Data Sources

- `source: "v21_v22_initial"` - First 9 transitions from Black Book x Defected V2
  (Claude V21 proposal vs Sam V22 correction).
- `source: "mix_cd_ground_truth_2026_07_15"` - Sam-approved finished mix CD projects on
  Master Backup 2013-2024 under Mixed CD Projects. This replaces the old `Teaching Mixes/`
  folder for the next learning pass.
- Future sources must carry project, exact ALS filename, file hash, decision provenance, and
  review status.

## Approved Mix CD Ground Truth - 2026-07-15

Use only these named sets for the first pass. Resolve the exact ALS named below and record every
rejected near-match; do not silently choose a backup, an older version, or a similarly named set.

- Amber Shepherd - Radio Mix [Amber Shepherd]
- AN21 Space Mix v5
- VERSIONApple Music Glitterbox Mix 2.1 SW V1
- Bargrooves Ibiza 2014 Side 1 SW V1
- Bargrooves Ibiza 2014 Side 2 SW V1
- Gbox Side 1 CB Final SW V1
- Gbox side 2 CB MIXED SW V1
- Gbox Side 3 Final Mixed SW V1
- Defected Miami 2019 Side 1 SW V1
- Defected Miami 2019 Side 2 SW V1
- Ibiza 2019 Side 1 CB mix v1 SW v2 (Digital)
- Ibiza 2019 Side 2 V2 SW V1 DIGITAL
- SIMON UPDATE Ibiza Side 3 SW V1 DIGITAL V2
- Gbox Feb Side 1 (Final CB Mix) SW V1
- Gbox Feb 2018 Side 2 MASTER CB Mix Down SD Minor tweaks SW V2
- Defected ITH Miami 2017 Side 1 SW V1
- Defected ITH Miami 2017 Side 2 SW V1

## Sol Audit Findings - 2026-07-15

A reproducible read-only schema probe of five approved sets found enough variation to reject a
single heuristic extractor:

| Exact ALS | Creator | Clips | Warp markers | Envelopes | LoopOn | Disabled | Locators | SHA-256 |
|-----------|---------|------:|-------------:|----------:|-------:|---------:|---------:|---------|
| `AN21 Space Mix v5.als` | Live 8.2 | 178 | 799 | 0 | 0 | 0 | 0 | `F5A1CAB9EAE948257E11A90AFE4A9CDA02EA4DBC227145F9C24ED830AC7EEB71` |
| `VERSIONApple Music Glitterbox Mix 2.1 SW V1.als` | Live 10.0.5 | 346 | 1,244 | 29 | 5 | 25 | 0 | `08BADC54FDDB88DBC29D57FA777EB5B80C580B5A16C59654CF3DDD6162416FC5` |
| `Bargrooves Ibiza 2014 Side 1 SW V1.als` | Live 9.1 | 100 | 655 | 0 | 0 | 0 | 0 | `6183A7AC683E7665A0D2BB95D5F6E84EC04CD77A9B2278D6CF7DD731E137ADD9` |
| `Defected Miami 2019 Side 1 SW V1.als` | Live 10.0.5 | 70 | 449 | 16 | 0 | 0 | 0 | `44045B267C3617B34203C9AFF4ED98BFCEC515626104811CDFE0B9B6B89BD4B7` |
| `Gbox Side 1 CB Final SW V1.als` | Live 9.7 | 542 | 27,184 | 0 | 9 | 0 | 0 | `DE7F6089B69BD4B09884A051B847DE6267A9BA7E4F9FE96AD4EB1F071F37D539` |

Source-audio resolution and render availability remain pending Phase 0; they were not inferred
from FileRef text alone. The sample proves that automation cannot be assumed, native `LoopOn` is
rare even in chopped sets, disabled alternatives must be excluded from played truth, and locators
cannot be required. The backup also contains many near-identical versions and folders explicitly
marked do not use. Exact version resolution is part of correctness, not clerical work.

The existing `extract_mix_points.py`, `analyze_teaching.py`, and
`extract_mix_patterns_v2.py` are reference probes only. Their assumptions - longest clip equals
song, adjacent long clips equal a transition, raw overlap equals audibility, and unresolved
automation target IDs are meaningful - are not acceptable for this corpus.

## Objective

Learn distributions of real decisions, not one universal transition recipe. For a held-out pair
of tracks, the useful targets are:

1. Which source section and phrase is used to bring the incoming track in.
2. Which source section and phrase is used to take the outgoing track out.
3. Which musical landmarks are aligned: section boundary, fill, break, drop, bass change, vocal
   entry, or outro.
4. How long the structural blend and effective audible blend last.
5. Where rhythmic, bass, and perceptual dominance actually hand over.
6. Whether a source phrase is looped, duplicated, chopped, extended, or skipped.
7. Whether an FX/vocal/bed layer assists the handover.
8. Which automation shape supports the decision, where that evidence is recoverable.

Track ordering, key selection, and global energy arc may be measured as context, but this pass
does not replace the harmonic sequencing engine.

A finished mix supplies positive evidence for the choice that was made. It does not prove that
every unused section or alignment was bad. Unchosen feasible boundaries remain unlabeled, not
negative examples. Confirmed before/after corrections are stronger comparative evidence than a
final-only ALS.

## Professional DJ Interpretation

The target is not to clone one historic timeline. It is to recover transferable DJ craft from a
studio-built mix while separating it from commission constraints.

For every source bar or phrase, build a **mixability profile** alongside the coarse section label:

- phrase/downbeat phase and pickup/fill proximity;
- kick and groove continuity;
- bass occupancy and bass-change opportunity;
- vocal occupancy and vocal-collision risk;
- lead/hook or dense melodic activity where evidence exists;
- drums-only, bass-safe, vocal-safe, and melody-light runway lengths;
- energy/tension percentile and direction;
- source confidence and whether the evidence is audio-derived, ALS-derived, or human.

For every transition, describe the DJ intention as multi-label evidence: blend, cut/slam, tease,
break-to-drop, double-drop, bass swap, acapella/overlay, loop extension, FX bridge, or studio-only
edit. Also record the main risks a DJ is managing: kick flamming, groove mismatch, bass collision,
vocal-on-vocal, hook-on-hook, harmonic tension, energy cliff, or excessive repetition.

The professional quality question is: **does the handover feel deliberate and preserve listener
attention?** Exact historical boundary error is useful for validating extraction and ranking, but
it is not the final success metric because another nearby phrase may be equally musical.

### Transferable Craft Versus Commission Constraints

Mix-CD sets may reflect label-supplied running order, required track exposure, CD/radio duration,
requested versions, client notes, or mastering/approval decisions. Record these as
`sequence_locked`, `runtime_target`, `minimum_exposure`, `source_version`, and
`constraint_basis` where known; otherwise use `unknown`. Do not learn them as general DJ
preferences from timing alone.

## Evidence Hierarchy

Keep each fact with its source and confidence. Never flatten these evidence levels:

1. **ALS structural fact** - enabled clip geometry, source slice, warp map, track/routing state.
2. **ALS gain estimate** - clip fades/gain plus resolved track/group/return automation. This is an
   estimate because third-party devices, return tails, and plugin latency may be unresolved.
3. **Rendered-audio fact** - audible activity measured from an available final render or stem.
4. **Source-audio analysis** - sections, kick/bass/vocal activity, energy, and downbeats mapped
   through the ALS warp clock.
5. **Human annotation** - Sam-confirmed transition identity, role, or section correction.
6. **Metadata hypothesis** - track name, colour, project family, locator, or filename wording.

Use `clip_overlap`, `gain_estimated_overlap`, and `render_confirmed_overlap` as separate
fields. Do not call an interval audible when only clip geometry proves it exists.

Frozen, flattened, consolidated, or resampled material keeps every structural fact that is still
directly observable against its current rendered source. It cannot supply the lost original-song
source mapping, device history, or edit grammar. Mark those fields unavailable and never
reconstruct a plausible history.

## Corpus Selection And Provenance Gate

Before deep extraction, run a cheap availability/provenance pass across all 17 named ALS files:

1. Locate every exact named ALS, record SHA-256, size, modified time, Ableton version, source
   project folder, source-audio resolution rate, render availability, freeze/consolidate evidence,
   tempo/meter structure, and counts of automation/device/routing structures.
2. Record all competing ALS candidates and the reason each was rejected.
3. Mark decision provenance: Sam-authored mix, collaborator mix with Sam amendment, master/check
   only, or unknown. Do not attribute a CB/SD/AD decision to Sam from a filename alone.
4. Resolve source audio and final mix renders without moving originals. Record missing,
   consolidated, frozen, resampled, or relinked sources.
5. Assign eligibility independently for structural extraction, source-section analysis,
   automation analysis, rendered-audio validation, and paired-version comparison.
6. If a clear pre-SW predecessor sits beside the approved ALS, add a quarantined delta lane:
   compare clip boundaries, source offsets, loops/chops, warps, fades, gains, routing, and
   automation. Do not treat the delta as Sam's correction until its provenance is confirmed.

The 17 files are approximately nine independent event-level split units:

1. Amber Shepherd radio mix.
2. AN21 Space Mix.
3. Apple Music Glitterbox Mix.
4. Bargrooves Ibiza 2014 Sides 1-2.
5. Glitterbox A Disco Hi Sides 1-3.
6. Defected Miami 2019 Sides 1-2.
7. Defected Ibiza 2019 Sides 1-3.
8. Glitterbox Disco Heat / Gbox February 2018 Sides 1-2.
9. Defected ITH Miami 2017 Sides 1-2.

Sequential sides from one event never cross train/test boundaries. During Phase 0, build a
source-fingerprint overlap report; event groups sharing source audio are coupled for strict
held-out tests.

## Canonical Data Model

Build a new read-only corpus extractor with version adapters. Do not extend the hard-coded
`Teaching Mixes` scripts.

The normalized model must retain:

- **Project manifest:** selected ALS, schema version, tempo/time-signature maps, arrangement
  bounds, render/source availability, provenance, commission constraints, event-level split unit,
  and extraction warnings.
- **Track graph:** audio/group/return/master tracks, parent groups, input/output routing, sends,
  activator/solo state, crossfader assignment, track delay, devices, freeze/flatten state, and
  role.
- **Source asset:** stable source ID from ALS FileRef plus file hash/fingerprint where available,
  sample rate/channels/duration, and whether it is a song, FX, vocal layer, guide, reference,
  printed mix, resample, or unknown.
- **Clip fragment:** arrangement placement, enabled state, source window, loop state, fades and
  curves, gain, pitch/reverse, warp mode/markers, colour/name, groove, and source identity.
- **Automation lane:** owner track/device/parameter, resolved parameter name and units, static
  value, events, and resolution confidence. Unknown target IDs remain unknown.
- **Activity event:** structural, gain-estimated, and render-confirmed contribution intervals.
- **Mixability profile:** bar/phrase-level kick, bass, vocal, lead-density, energy, phrase phase,
  safe-runway lengths, and confidence.
- **Transition episode:** all sources and landmarks involved in one handover, not pairwise rows
  for every overlapping clip.
- **Section evidence:** V3 result, raw feature confidence, ALS edit cues, human correction, and
  signed offsets between each cue and the nearest section/phrase boundary.

Every derived field must retain `value`, `basis`, `confidence`, and `warnings`. The pilot
schema stays deliberately small; fields with no evidence in Phase 0 remain deferred extensions.

## Clock And Warp Contract

Maintain five explicit coordinates:

1. Source sample seconds.
2. Warped source beats inside the AudioClip.
3. Arrangement beats.
4. Project seconds under the tempo automation map.
5. Musical bar/beat under the active global time signature.

Rules:

- Use one piecewise warp mapping for section cuts, played source windows, loops, and transition
  measurements.
- Probe `Time`, `CurrentStart`, `CurrentEnd`, `LoopStart`, `LoopEnd`, hidden markers,
  and `StartRelative` semantics directly on one real file per Live schema before treating them
  as known.
- Interpolate between warp markers and correctly extrapolate beyond edge markers. Never clamp
  an out-of-range source beat to the first/last marker.
- Model looped playback piecewise with modulo source position; model duplicated clips as separate
  events.
- Build project seconds from the global tempo envelope. Do not confuse clip-local default
  TimeSignature elements with the project's time-signature map.
- Never convert beats to bars by dividing by four unless the active meter is 4/4.
- Round-trip source -> arrangement -> source at every used boundary and record both numerical and
  musical residuals. The pilots establish schema-specific numerical tolerances before scaling.
  Fail immediately on non-monotonic mappings, impossible durations, or a residual large enough to
  change musical beat/phrase phase.

## Extractable Arrangement Signals

### Source Selection

- First and last played source positions for every song.
- Intro skipped, outro skipped, and total source duration used.
- Re-entry into an earlier source phrase, reverse playback, transposition, and consolidated edits.
- Repeated use of the same source interval across `LoopOn`, duplicated clips, or chopped clips.
- Clip cuts that land repeatedly on the same source beat; these are strong loopable-phrase cues.
- Disabled clips as rejected/alternative evidence only, stored separately from played decisions.

### Track And Routing Intent

- Main song tracks versus FX, vocal overlays, beds, guides, reference prints, and final mix prints.
- Group membership and whether automation is applied on the source track, group, return, master,
  or an external control track.
- Effective activation through clip disabled state, track/group activators, crossfader assignment,
  and automation.
- Send changes and return activity around boundaries. Reverb/delay tails may extend beyond the
  source clip and require render confirmation.
- Track delay and known device latency as timing warnings.

### Mixability Profile

Reuse the existing audio-analysis and stem paths. Do not build a new all-purpose music model for
the pilot. Per bar/phrase, retain:

- kick occupancy, confidence, local timing residual, and groove/swing stability;
- bass occupancy and low-end change;
- vocal occupancy;
- lead/hook activity only when supported by audio evidence or human annotation, otherwise unknown;
- drums-only, bass-safe, vocal-safe, and melody-light runway lengths;
- energy/tension percentile and local direction;
- phrase phase, pickup/fill proximity, and nearest trusted section boundary.

At an overlap, compute pairwise conflict evidence rather than judging each track independently:
kick-flam risk, simultaneous bass occupancy, simultaneous vocal occupancy, dense-lead collision,
energy discontinuity, and repeated-loop fatigue.

### Edit Grammar

Distinguish:

- Native `LoopOn` repetition.
- Repeated copies of the same source slice.
- Sequential phrase extension.
- Micro-chops/retriggers.
- Alternating or back-jump edits.
- Intro/outro skip.
- Fill insertion.
- FX-assisted boundary.
- Resampled/consolidated composite where the original edit grammar is no longer recoverable.

Count source discontinuities, not raw AudioClip starts. Adjacent fragments that continue the same
source trajectory are one edit, not multiple chops.

### Automation

Resolve parameter targets by device and parameter scope, across tracks, groups, returns, and
master. For the pilots, extract volume, Utility gain, EQ/filter, sends, pan, crossfader,
device on/off, and tempo where resolvable. Rack macros, sidechains, and unknown long-tail devices
remain tag-presence measurements until Phase 0 proves they materially occur. Preserve:

- Static value before the transition.
- First meaningful departure from static.
- Shape, extrema, and return to static.
- Breakpoint distance from section, phrase, and handover landmarks.
- Whether the lane controls an individual source or a shared bus.
- Unknown plugin parameters and unresolved units without guessing.

## Transition Episode Model

A transition is a connected episode in the source-contribution graph where the dominant song
changes. Direction is inferred from before/after dominance, not track order or clip duration.
Three-track handovers, vocal overlays, FX, and return tails remain one episode.

Record separate landmarks:

| Landmark | Meaning |
|----------|---------|
| `episode_start` | First intentional structural action: incoming clip/edit/automation/FX |
| `incoming_first_present` | Incoming source first exists structurally |
| `incoming_first_effective` | Incoming clears the gain/activity threshold |
| `incoming_first_rendered` | Incoming becomes audible in a render, when measurable |
| `rhythmic_handover` | Kick/drum leadership changes |
| `bass_handover` | Low-frequency leadership changes |
| `dominance_crossover` | Incoming becomes the stronger main source |
| `outgoing_last_rhythmic` | Outgoing loses meaningful rhythmic content |
| `outgoing_last_effective` | Outgoing falls below the estimated threshold |
| `outgoing_last_rendered` | Outgoing/tail is no longer audible in a render |
| `episode_end` | Last transition-specific edit/automation/FX action |

This replaces one ambiguous `transition_start/end` pair. The arranger can later choose the
landmark appropriate to volume, bass, or section alignment.

## Section And Energy Learning

Run the current V3 detector only on resolved source audio. Map every source section boundary
through the exact clip warp/loop mapping into arrangement time. Keep Kick Detector V3 as one
feature, not ground truth. Coarse labels such as `intro`, `break`, `drop`, and `outro` are not a
complete description of mixability: two intros can have very different bass, vocal, hook, and
groove content.

For every played source window, measure:

- Section at clip entry, first effective entry, rhythmic handover, bass handover, dominance
  crossover, last rhythmic exit, and source exit.
- Signed bars from each landmark to the nearest section boundary and phrase boundary.
- Section pair and phase relationship: for example outgoing drop -> break while incoming intro ->
  drop.
- Available kick-safe, bass-safe, vocal-safe, and phrase-aligned intro/outro runway.
- Repeated ALS cut/loop/automation/FX boundaries that disagree with V3. These are candidate missing
  section boundaries, not automatic corrections.
- Boundary consensus between V3, kick/bass/vocal changes, novelty/energy change, ALS edits, and
  human annotation.
- Section subrole where evidence supports it: drums-only intro, musical intro, short break, main
  break, build, drop, bridge, drums-only outro, musical outro, or tail. Unsupported subroles stay
  unknown rather than being inferred from position alone.
- Local tonal/harmonic compatibility during the actual overlap window, used as transition-risk
  evidence rather than as a replacement for the sequencing layer's key engine.
- Groove continuity, bassline/low-end overlap, vocal collision, and lead/hook collision around
  each landmark.
- Whether a technique appears reproducible in a live DJ performance or depends on a studio-only
  edit. This is descriptive context, not a quality ranking.

Measure energy from source audio in bar/phrase windows using within-track percentiles, not raw
mastered loudness alone: loudness, low-band energy, kick occupancy/confidence, drum density,
spectral change, vocal activity, and harmonic density. Keep source energy separate from estimated
mix contribution. Record the incoming/outgoing energy delta and the whole-mix trajectory.

Avoid circular validation: ALS edit cues may help propose a missed boundary, but a boundary trained
from those cues cannot be scored as correct against the same cues.

## Sequence Context

Each transition row also carries:

- Mix progress percentile and opening/mid-set/climax/closing role.
- Time each main song has been dominant before and after the handover.
- Previous and next transition lengths/types.
- Local and cumulative energy trajectory.
- BPM/key movement where metadata is available.
- Number and role of concurrent sources.
- Known commission context: sequence lock, runtime target, minimum track exposure, source-version
  choice, and other documented client constraints.

Aggregate by project first, then style family. A 542-clip Glitterbox set must not outweigh a
70-clip Defected set merely because it contains more edits. Keep all sides from the same release
event in one split unit, and do not learn label running order, minimum exposure, or runtime limits
as general DJ preferences.

## Review Table

One row per transition episode, with child rows for each participating source:

| Group | Required fields |
|-------|-----------------|
| Identity | source_mix, ALS hash, transition_id, transition_index, decision provenance |
| Quality | extraction status, source status, render status, section status, warnings, confidence |
| Context | style hypotheses, mix progress, tempo, global meter, key movement, energy trajectory |
| Constraints | event split unit, sequence/runtime/exposure/version constraints and evidence basis |
| Participants | source IDs, track/role, routing path, incoming/outgoing/overlay/FX/return role |
| Geometry | all episode landmarks, arrangement beats/bars/seconds, clip overlap |
| Audibility | gain-estimated overlap, render-confirmed overlap, audibility basis/threshold |
| Source use | first/last source second/beat/bar, intro/outro skipped, played duration |
| Warp | warp mode, marker count, round-trip error, downbeat/phrase phase, off-grid residual |
| Sections | labels/confidence at each landmark, section source, signed boundary offsets |
| Mixability | kick/bass/vocal/lead occupancy, safe runway, phrase/fill proximity, local tonal risk |
| Handover | rhythmic, bass, dominance, vocal, and tail handover positions |
| DJ intent/risk | technique labels, kick flam, bass/vocal/hook collision, energy cliff, loop fatigue |
| Loop/edit | mechanism, source interval, repeats, extension bars, role, chop discontinuities |
| Automation | owner path, parameter, units, shape, extrema, boundary offsets, resolution confidence |
| FX/returns | assisting source/return, trigger range, send changes, tail evidence |
| Human review | accepted/corrected, corrected values, reviewer note |
| Reproducibility | studio-only/live-reproducible/unknown, evidence basis, extraction version |

Classification remains orthogonal: handover mechanism, section relationship, loop mechanism,
chop density, FX assistance, concurrency, bass-handover shape, energy motion, automation shape,
and sequence role. Do not force one mixed label such as `long_bass_loop_mix`.

## DJ Review Artifact

Sam should review a compact transition card rather than the raw feature table. Each card contains:

- a phrase-aligned 32/64-bar timeline around the handover;
- outgoing and incoming section/subrole lanes;
- kick, bass, vocal, lead/hook, energy, and tension lanes with confidence/missingness visible;
- clip, gain, automation, return-tail, and render-confirmed activity lanes;
- episode, rhythmic, bass, dominance, and section landmarks;
- loop/chop/FX annotations and known commission constraints; and
- a 20-40 second rendered audio preview when a render or safe reconstruction is available.

The review records four answers and elapsed review time:

1. Are the participants and episode boundaries correct?
2. Are the rhythmic, bass, vocal, and dominance landmarks correct?
3. Are the inferred technique, intent, and collision risks correct?
4. Is this a musically acceptable mix, and what correction or preference note is needed?

Review-time measurement determines whether full-corpus review is practical or must remain
exhaustive for pilots and stratified for the remaining corpus.

## Formula-Backed Metrics

- `clip_overlap`: intersection of enabled clip intervals.
- `gain_estimated_overlap`: intersection after all resolved clip/track/group gain and activation
  states. Its audibility threshold is versioned and frozen in each MixPlan from a rendered pilot;
  later gold-set calibration may replace it for later plans but never retroactively changes an
  accepted build. The 48-bar hard cap uses structural clip overlap and cannot be weakened by an
  audibility threshold.
- `render_confirmed_overlap`: intersection measured from an available final render/stems.
- `section_offset_bars`: signed musical bars from a landmark to the nearest boundary under the
  active meter.
- `downbeat_phase_error`: distance to the intended bar phase under the active meter.
- `loop_repeats`: repetitions of a stable source interval, whether expressed by `LoopOn` or
  duplicated slices. Keep the mechanism separately.
- `loop_presence`: none = 0, light = 1-2, heavy = 3-7, super = 8+ repeats. These are reporting
  buckets, not deployment rules.
- `chop_starts_per_bar`: genuine source discontinuities inside the episode divided by episode
  bars. Derive buckets only after reviewing the distribution.
- Energy features are normalized within source track and summarized per project before any
  cross-project aggregation.

## Execution Plan And Gates

1. **Phase 0 corpus and provenance scan.** Across all 17 named ALS files, produce exact hashes,
   event-level split groups, source-resolution/render availability, freeze/consolidation evidence,
   tempo/meter/schema, routing/device/automation counts, and audio-fingerprint overlap. Sam freezes
   the eligible manifest before interpretation begins.
2. **Pilot A - simple transition project.** Use `Defected Miami 2019 Side 1 SW V1` (70 clips,
   449 warp markers, 16 automation envelopes) to build raw extraction, one-clock mapping, routing,
   and two or three complete DJ review cards.
3. **Pilot A review gate.** Sam reviews the cards and the review time is recorded. Fix extractor,
   landmark, language, and display errors before broad extraction; do not normalize known errors
   into the dataset.
4. **Pilot A descriptive pack.** Complete transition episodes, source/section/mixability evidence,
   missingness, and project summary for the simple project.
5. **Pilot B - edit-density stress project.** Use `Gbox Side 1 CB Final SW V1` (542 clips,
   27,184 warp markers, no automation envelopes) to test duplicate slices, chops, loops, source
   discontinuities, multi-source episodes, and the orthogonal technique taxonomy.
6. **Freeze the minimum contract.** After both pilots, freeze the smallest useful schema, clock
   tolerances, review protocol, provisional extractor metrics, and quarantine rules.
7. **Representative schema probes.** Validate at least one real project for each observed Live
   8.2, 9.1, 9.7, and 10.0.5 schema before scaling its adapter.
8. **Scale deterministic extraction.** Emit raw project/track/source/clip/routing/automation JSON,
   resolve/hash audio, deduplicate source variants, and cache V3 plus shared audio descriptors by
   content hash and detector version.
9. **Reconstruct episodes and mixability.** Derive structural, gain-estimated, and render-confirmed
   activity separately; reconstruct participants and landmarks; map sections and mixability into
   each played window; compute pairwise collision evidence.
10. **Human gold set.** Review every transition in both pilots. Expand exhaustively or by a frozen
    stratified scheme for the remaining event groups according to measured review workload and
    representation.
11. **Descriptive pack.** Produce transition tables/cards, per-event and per-style summaries,
    missingness/confidence reports, constraint analysis, and paired-version deltas where genuine
    versions exist.
12. **Held-out evaluation.** Rank the historical choice among safe candidates with event-grouped
    splits; leave one event out and remove the largest event as robustness tests. Treat
    leave-one-family-out as transfer evidence, not a requirement for style-specific rules.
13. **Behavior proposal.** Only after Sam reviews the evidence and the falsification protocol is
    frozen, propose changes to section detection, alignment, overlap selection, loop planning, or
    automation in a separate implementation diff.
14. **Deployment proof.** Preserve all hard arrangement limits, then compare newly generated Car
    Mix and style-diverse held-out transitions against the safe baseline under blinded Sam review,
    ALS validation, and transition-card inspection.

## Implementation Ownership

This is a multi-evidence implementation, not one unconstrained multimodal model run:

- **Sol leads:** architecture, canonical schema, clock/warp/routing semantics, pilot extractor,
  integration decisions, high-risk code, and adversarial review.
- **Terra/Luna or MiniMax assist after contracts freeze:** filesystem inventory, repetitive schema
  probes, bounded extraction work, fixtures, and test expansion. They do not independently change
  the canonical model or timing semantics.
- **Specialist signal tools provide evidence:** Kick Detector V3, the shared Audio Analysis Toolkit,
  Demucs/stem paths, render measurements, and metadata/key systems. Their versions and confidence
  stay attached to every derived field.
- **Sam supplies the musical gate:** timed review cards and blinded audio preference determine
  whether the learned behavior is professionally useful.
- **Claude is a scarce milestone reviewer:** use one targeted read-only architecture/diff review
  after the pilot contract and before arranger integration, not for bulk extraction.

One Sol-owned implementation branch/thread integrates all changes. Peer outputs arrive as
read-only findings or bounded diffs and are reviewed before integration. Visual timelines can be
inspected by a vision-capable model, but rendered audio is judged through measured descriptors and
Sam's listening review rather than an LLM claiming to hear it.

## Falsification Record

```
CLAIM:        Reviewed ALS-derived priors improve the musical acceptability/preference of unseen
              generated transitions versus the current safe heuristic while preserving hard safety.
NULL:         Current safe heuristic arranger.
NOISE TWIN:   Random safe feasible candidate, preserving timing, meter, runway, and hard caps.
PILOT:        Measure extractor correctness, historical-choice candidate rank, missingness, and
              human review time. Pilot results cannot by themselves prove deployment value.
PRIMARY:      Blinded, event-grouped Sam preference/acceptability on newly generated transitions.
SECONDARY:    Historical chosen-candidate rank/top-k percentile, landmark/boundary error, overlap
              class, style robustness, and hard-safety results.
KILL:         Before the full test, freeze the minimum practical effect and review protocol from
              the pilots. Do not deploy if its confidence interval includes no improvement, it
              misses the frozen practical effect, any supported style materially regresses, or
              any hard safety invariant is violated.
SAMPLE:       Set after Phase 0 and both pilots from the number of independent event groups and
              measured review variance. Results remain descriptive where support is insufficient;
              multiple sides from one release do not count as independent projects.
ATTRIBUTION:  Test section-pair, energy, style, loop need, and sequence context as ablations.
              A complex rule is rejected if simpler subsets retain none of its gain.
REGIME:       Leave one event out; re-run without the largest event; use leave-one-family-out to
              measure transfer. No release event or repeated source fingerprint may carry the result.
VERDICT:      NOT YET TESTED. The corpus and gold annotations do not exist yet.
```

Extractor acceptance precedes model acceptance:

- Pilot A and B establish provisional numeric extractor thresholds before scale-out; freeze them
  before evaluating the remaining corpus, with every miss listed.
- Valid clock round trips for every included clip boundary; failures are quarantined.
- Deterministic outputs from the same ALS hashes and extractor version.
- Missing data and unresolved automation never converted to zero/none.

## Candidate Retrieval And Priors

Retire the fixed `0.3 BPM + 0.7 section shape` similarity rule as an unvalidated learned
placeholder. Mix one uses a deterministic `interim_v1` scorer after hard feasibility gates - timing
integrity, phrase grid, section confidence, overlap/loop safety, and available source runway. It
orders safe candidates lexicographically by reviewed section/phrase alignment, known collision
risk, required loop/edit intervention, warp cost, and then the shortest overlap that supplies the
required runway. Unknown evidence cannot improve a rank. Sam's transition review remains the final
choice; corpus-derived weights replace `interim_v1` only after held-out evaluation. Reviewed
features include:

- Incoming/outgoing section relationship and phrase phase.
- Kick/groove continuity, bass overlap, vocal and lead/hook collision, local tonal risk, loop
  fatigue, and energy compatibility around each candidate landmark.
- Desired sequence role and current mix energy trajectory.
- Tempo and global key compatibility already provided by the sequencing layer; local harmonic
  overlap remains a transition-risk feature rather than a second sequencing engine.
- Style/method evidence, loop/edit need, and concurrency.
- Confidence and missingness penalties.

For each observed transition, compare the selected decision with its safe feasible candidate set.
Use ranking or calibrated empirical distributions; do not train a binary classifier that labels
every unchosen boundary as wrong. Start with explainable project/style-conditioned distributions
and confidence intervals, using partial pooling only where support justifies it.

Weights remain unset until the held-out tests. Group splits by release event and exact mix version;
also run a strict test that prevents the same source-audio fingerprint appearing on both sides
where the corpus allows it. Score the rank of the real historical choice within its feasible set,
not merely whether it matches one exact boundary. Low-confidence or unsupported cases fall back
to the safe heuristic arranger; learned priors may narrow or rank safe candidates, never bypass
hard caps.

## Production And Mix Acceptance Plan

The learning corpus is not the deliverable. The deliverable is one complete Ableton DJ mix whose
beat grids, sections, tempo journey, warp quality, arrangement, transitions, automation, render,
and whole-mix flow have all passed their own gates. Production safety work can begin alongside
Phase 0, but learned preferences cannot change arrangement behaviour until the pilots support them.
Unsupported decisions continue through the safe heuristic and human-review path, so the first
finished mix does not have to wait for every corpus hypothesis to mature.

### Canonical MixPlan And Completion Predicate

Create one immutable, versioned, machine-readable `MixPlan` before writing the production ALS.
Sequencing, tempo, warp assignment, clip placement/cuts, loops, transition landmarks, automation,
FX/returns, rendering, review cards, and approval invalidation all consume this same plan. Later
phases may propose a new version but may not independently reconstruct or clamp decisions from the
ALS, filenames, or partial reports. Parsing the ALS proves implementation against intent; it never
becomes the source from which intent is re-derived.

Use stable hash-backed `source_id`, `track_instance_id`, `clip_id`, `transition_id`, `tempo_plan_id`,
and `section_map_id`. Names and substrings are display/search aids only and cannot join production
records. Every MixPlan carries its parent/version, complete input hashes, policy/tool versions,
human overrides, and a canonical `plan_hash`.

The MixPlan freezes `main_track_sequence: [track_instance_id, ...]` at Stage 3. `N` and `N-1` are
computed only from this field; no component may reclassify a main song as an overlay/FX/bed from
its name, duration, position, or activity. Changing the sequence creates a new MixPlan version and
invalidates affected approvals.

For a linear mix of `N` main songs, production completion requires:

- `N` certified played-source instances and exactly `N-1` main handover contracts; overlays, FX,
  and returns remain child participants rather than hiding a missing handover;
- every intended clip, loop, automation target, tempo event, and transition present in the final
  ALS and matched back to its contract by stable ID;
- exactly `N-1` **final accepted** candidate-ALS previews, one per main handover, rendered through
  the intended chain. Correction renders may coexist, but only the final record counts; it stores
  structured `ACCEPT` values for rhythmic feel, bass swap, phrasing, musical intent, energy,
  warp sound, transition length, and overall quality, plus reviewer, timestamp, `MixPlan` hash,
  ALS hash, and render hash;
- no unresolved blocker or unclassified production warning, no stale approval, and no quarantined
  item included in the build;
- structural/audible overlap, loop counts, clip continuity, tempo/warp policy, and automation
  revalidated from the **final post-mutation clip graph and render**, not only the pre-write plan;
- the ALS corruption/intent gates, Ableton-open proof, both whole-mix passes, and final render checks
  all passed.

Production mode is fail-closed. Missing grids, section coverage, source identity, transition overlap,
clip/loop targets, automation parameters, render previews, validators, or approval records are fatal
unless the MixPlan contains an explicit Sam-approved fallback contract for that exact item.

### First-Mix Critical Path

The historical-learning lane and production lane run in parallel. Corpus Execution Plan steps 1-14
may improve or replace Stage 3 ranking, but they do not gate the production framework or the first
finished mix. Until reviewed priors exist, `interim_v1` plus explicit fallbacks and Sam's acceptance
loop provide the decisions.

Implement production as three vertical proofs rather than all schemas at once:

1. **One-transition slice:** two certified sources -> minimal MixPlan -> final ALS -> actual Ableton
   render -> DJ card -> structured acceptance -> local correction -> freeze proof.
2. **Shared-middle slice:** three sources/two transitions, proving a middle track with incoming and
   outgoing dependencies, one tempo event, Warp Mode recalculation, loops/automation, and selective
   invalidation/re-review.
3. **Full-mix slice:** the complete `main_track_sequence`, exactly `N-1` accepted handovers, both
   whole-mix passes, Ableton-open reconciliation, and frozen delivery.

Stage 0 is optional while production retains the current `0.05 BPM` Re-Pitch ceiling and uses
Complex/Complex Pro beyond it. It becomes mandatory before any wider Re-Pitch allowance is used.
Historical Live 8/9/10 adapters, new hook/subrole detectors, and corpus-only descriptive fields can
continue after the one-transition proof; stable MixPlan IDs, fail-closed gates, one-clock validation,
actual render/review storage, and contract-to-ALS reconciliation cannot be deferred.

### BPM Trajectory And Warp-Mode Contract

Keep four tempo quantities separate:

- `native_bpm`: robust track-level grid tempo for reporting/sequencing, with provenance/confidence;
- `source_bpm(s) = 60 * d(BeatTime) / d(SecTime)`: local source tempo from the piecewise warp-map
  slope at source position `s`;
- `set_bpm(t)`: the project tempo automation at arrangement time; and
- `playback_ratio(t) = set_bpm(t) / source_bpm(source_time(t))`: the instantaneous speed change for
  an audible clip. This reduces to `set_bpm/native_bpm` only for a constant-tempo source.

Express Re-Pitch cost in cents, not only raw BPM difference:

`pitch_shift_cents(t) = 1200 * log2(playback_ratio(t))`

This makes the threshold comparable at 118 BPM and 132 BPM. The production brief must explicitly
select or approve one of four tempo strategies before sequencing is frozen:

| Strategy | Behaviour | Best use | Main risk |
|----------|-----------|----------|-----------|
| `fixed_center` | One project BPM chosen to minimise catalogue-wide stretch | Narrow-BPM, steady club mix | Can flatten a natural energy rise and over-stretch edge tracks |
| `progressive_arc` | Deliberate start BPM and end BPM, usually slow-to-fast | Building energy over a long mix | Can force tracks into the wrong place merely to preserve monotonic tempo |
| `local_follow` | Tempo follows the native BPM of the dominant/next track | Explicitly approved wide-BPM experiments only | Can create audible tempo yo-yo and an incoherent whole-mix arc |
| `hybrid` | Global creative arc with local, bounded movement toward active tracks | Default candidate for finished mixes | Needs joint sequencing, transition, and ramp optimisation |

For `hybrid`, generate at least fixed-center and progressive/local-follow comparison curves. Score
them on cumulative warp cost, maximum per-track cost, number/direction of tempo reversals, ramp
audibility risk, energy shape, harmonic ordering, track exposure, and transition feasibility. Sam
approves the creative curve from the brief/overview before final arrangement. The planner must:

- prefer ordering that reduces unnecessary tempo damage rather than fixing a poor order with
  extreme warping;
- keep a stable tempo through exposed vocals, hooks, held notes, and critical handovers where
  possible;
- place tempo movement in phrase-aligned drums-only, breakdown, build, or low-conflict windows;
- prevent short-term up/down oscillation unless deliberately approved;
- preserve the outgoing track near its established tempo while it dominates, then move toward the
  incoming track as dominance transfers; and
- record every tempo point, ramp window, rate, reason, and affected source.

Warp mode is selected from the maximum absolute pitch shift across each clip's **final audible
window**, not from one global project BPM:

- Prefer **Re-Pitch** because it avoids the continuous full-spectrum processing of Complex modes
  and behaves like changing turntable speed. Sam's proposed listening hypothesis is about
  `15 cents`, roughly `+/-1 BPM` at 126 BPM. Until blind calibration replaces it, the operative
  production ceiling remains the existing `0.05 BPM` (`~0.7 cents`) regression guard. Raise the
  ceiling only to the largest tested shift Sam cannot reliably distinguish or prefers to the
  Complex alternatives.
- Above the frozen Re-Pitch ceiling, use **Complex or Complex Pro** so a two-, three-, or larger-BPM
  move does not make the track audibly sharp or flat. Complex Pro is the starting full-song
  candidate; Complex remains an explicit A/B alternative because either algorithm may preserve a
  particular master better.
- If a tempo ramp would cross the mode boundary while a track is audible, either use the safer
  time-stretch mode for the whole audible clip or split only at an approved phrase boundary with a
  click-free, render-reviewed handover. Never switch modes invisibly mid-phrase.
- Re-Pitch changes the effective musical pitch and therefore the effective key. Candidate scoring
  must use the shifted key/cents during the overlap, not only the source metadata key.
- The final policy applies both a calibrated cents ceiling and a calibrated raw-BPM ceiling. A
  candidate must pass both; this preserves Sam's BPM-based creative limit while making pitch cost
  comparable across source tempos.
- Every played clip records native BPM, minimum/maximum set BPM while audible, maximum cents,
  selected Warp Mode, decision threshold/version, and fallback reason.
- Tempo, cut, loop, source-window, or clip-boundary changes invalidate Warp Mode assignment. Joint
  planning iterates sequence, tempo curve, audible windows, and modes until they reach a stable,
  feasible MixPlan; the final ALS is checked against that fixed point.

An approved Warp Mode split point must be a downbeat on the certified source/arrangement clock,
sit on a trusted phrase or section boundary, avoid exposed vocal/hook/held-note content around the
cut, preserve source continuity exactly, include clip-edge click protection, and pass an A/B render
against using one conservative mode for the whole audible clip. Detector confidence alone cannot
approve the split.

### 0. Warp Mode Calibration And Policy Freeze

Before any MixBrief can widen Re-Pitch beyond the current `0.05 BPM` ceiling, render a blind Warp
Mode calibration set from representative masters
(strong vocals, exposed bass, dense full mix, transient-heavy drums) at `0`, `+/-0.05`, `+/-0.1`,
`+/-0.25`, `+/-0.5`, `+/-1`, `+/-2`, and `+/-3 BPM`. Randomise presentation; include the original,
Re-Pitch, Complex, and Complex Pro where applicable; collect repeated judgements over two sessions;
and record monitoring chain/level before unblinding. Run the decisive comparison through the
intended final render chain as well as a clean chain when nonlinear bus processing could change
the result. Sam chooses by quality and flags the audible threshold. The frozen policy is
regression-tested; it is not inferred from CPU cost or model opinion.

The calibration produces a versioned `WarpPolicy` with:

- `current_safe_bpm_limit = 0.05`, which remains operative until calibration is accepted;
- `calibrated_repitch_cents_limit`, the largest tested shift where Re-Pitch remains acceptable or
  preferable to Complex/Complex Pro under Sam's blind judgement;
- `calibrated_repitch_bpm_limit`, the corresponding raw-BPM guard Sam approves; and
- stimuli/source hashes, render-chain versions, monitoring conditions, judgements,
  reviewer/session IDs, and uncertainty/unsupported cases.

The MixBrief may choose stricter creative limits but cannot exceed either calibrated limit without
an explicit per-mix exception and blind render approval. This calibration owns Warp Mode thresholds
only; section, stem-absence, loop-safety, and mixability thresholds are frozen separately by their
own pilots and evidence.

### 1. Foundation Hardening And Mix Brief

- Fix the known arrangement defects before learned priors are integrated: enforce the 48-bar
  maximum after alignment **and** after loop insertion, freeze loop-count/duration caps, remove the
  larger-overlap tiebreak, and test intro loops, tail loops, partial loops, and multi-transition
  middle tracks.
- Preserve one beat/warp/section clock from source grid through clip cuts, automation, tempo map,
  visuals, and render measurements. No fallback may silently create a second clock.
- The first production release is certified for fixed global 4/4 meter only. Any non-4/4 project
  or meter change is rejected until the arrangement, phrase, overlap, loop, and visual maths are
  meter-aware; do not silently divide beats by four.
- Create a versioned `MixBrief` before sequencing: intended audience/context, runtime, track pool,
  locked/optional order, required exposure, energy shape, tempo strategy/start/end/range, allowed
  tempo reversals, Re-Pitch ceiling, Complex/Pro policy, transition density, loop/edit appetite,
  automation style, opening/closing intent, and hard exclusions.
- The brief also freezes monitoring/review conditions, mastering scope, final loudness and true-peak
  targets, render sample rate/bit depth, and whether the approved listening render is itself the
  delivery master or a pre-master with a separately reviewed mastering stage.
- Emit an immutable build manifest containing source hashes, analysis versions, selected grids,
  native BPM/key, and every human override. The same inputs and brief must reproduce the same plan.
- Freeze a `RenderProvider` contract: Ableton/template version and hash, required plugin/device
  identities and states, routing, master chain, render start/end and tail policy, sample rate, bit
  depth, dither policy, normalization state, and expected output channels. Nondeterministic plugins
  may prevent bit-identical independent renders, so reproduce structure/measurements and hash the
  one accepted delivery file rather than claiming universal byte identity.

**Gate:** all safety tests pass, every track has an accepted grid/native BPM, and Sam approves the
creative brief plus proposed tempo curve before arrangement is frozen. Without a new accepted
`WarpPolicy`, variable-tempo production remains valid but must retain Re-Pitch only within
`0.05 BPM` and use the approved Complex/Complex Pro fallback beyond it.

### 2. Section And Source Certification

- Produce one section certificate per source: phrase/downbeat grid, trusted section/subrole map,
  fills/pickups, drums/bass/vocal/hook activity, loop-safe windows, energy/tension curve, source
  start/end options, confidence, and evidence provenance.
- Store the certificate in machine-readable form and prove that every played source interval and
  every transition landmark is covered by a certified section/mixability interval.
- Every non-human section/mixability field names its detector/measurement, version, input artifact,
  threshold/profile, confidence, and validation basis. Phrase, loop-safe, bass/vocal-safe,
  melody-light, subrole, lead/hook, and harmonic-overlap evidence remain `unknown` when no validated
  source supports them. A generic stem band or filename/position heuristic cannot manufacture a
  hook, melody, or section subrole.
- Render the existing section visual and a short audio check around every low-confidence or
  detector-disagreement boundary. Human corrections become explicit overrides with reason and
  source time; they never mutate detector output invisibly.
- Certify the exact played source version. A radio edit, extended mix, remaster, frozen bounce, or
  consolidated edit cannot inherit another version's sections without fingerprint/time mapping.
- Reject or quarantine tracks with unresolved BPM/grid drift, wrong downbeat phase, missing audio,
  impossible warp mapping, or sections too uncertain to arrange safely.

**Gate:** every included track is `CERTIFIED` or carries an approved, bounded human override. No
uncertified section may become an automatic transition anchor.

### 3. Joint Sequence, Tempo, And Transition Construction

- Jointly choose order and tempo trajectory; do not sequence solely by key/BPM and bolt a tempo
  curve on afterwards. Evaluate harmonic movement, native BPM, effective Re-Pitch key, energy arc,
  style, track exposure, available mixable runway, and transition compatibility together.
- Score adjacent order candidates using predicted Re-Pitch cents/effective keys in their proposed
  audible overlap under the candidate tempo strategy. Every order proposal reports native-key and
  effective-key compatibility and flags where the verdict changes; sequence, tempo, and mode are
  revised together until feasible.
- For each adjacent pair, generate a safe feasible candidate set and select explicit landmarks:
  incoming first presence, rhythmic handover, bass handover, dominance crossover, outgoing rhythmic
  exit, and episode end.
- Assign Warp Mode against the audible tempo range, then construct clips/cuts, approved loops,
  section skips, gain/EQ/filter/send/pan/device automation, FX/return tails, and tempo ramps. Every
  operation remains phrase-aligned and traceable to the brief or reviewed evidence.
- Treat the construction as iterative: draft clips/loops establish audible windows; tempo and Warp
  Modes are recomputed; any changed window or mode regenerates the affected contract until the plan
  is stable. No downstream script may silently move a swap or clamp an automation point.
- Before writing any mid-track Warp Mode split, blind-compare a render using one conservative mode
  for the whole audible clip against the proposed phrase-boundary split. Record Sam's preference,
  reason, source/ALS/render hashes, and split boundary; no preference means no split.
- Build a transition contract before ALS writing: participants, source windows, sections/subroles,
  tempo range, warp cost/modes, overlap, landmarks, loop/edit plan, automation plan, collision risks,
  expected energy motion, confidence, and fallback.
- Label every repeated-source operation by intent: deliberate phrase extension, performance-style
  tease/retrigger, repair for insufficient runway, or emergency fallback. A repair loop is not
  evidence of a preferred DJ technique.
- Unsupported learned priors may rank nothing; the safe heuristic remains the deterministic
  fallback and never bypasses hard caps.

**Gate:** every transition contract is internally feasible before the ALS is written, and ALS intent
validation proves the generated clips/warps/automation match those contracts. After every cut,
shift, loop, and automation write, recompute the final clip graph and fail if structural/audible
overlap, continuity, loop caps, landmarks, or required targets differ from the MixPlan.

### 4. Transition-By-Transition Acceptance

Each transition receives a rendered preview and DJ review card generated from the actual candidate
ALS and intended render/master chain, never from a parallel reconstruction. Automated checks must
pass before Sam listens:

- beatgrid/downbeat/phrase alignment and source-to-arrangement round trips;
- correct source sections and landmark order;
- overlap and loop safety, clip continuity, no clicks/gaps, and no stale or duplicate automation;
- kick-flam, bass collision, vocal collision, hook collision, local harmonic tension, and loop
  fatigue risk;
- tempo ramp location/rate, per-track cents, Warp Mode choice, and effective overlap key;
- gain, EQ/filter, return-tail, low-end summation/cancellation, mono compatibility,
  true-peak/headroom, and perceived-loudness continuity; and
- structural ALS validation plus visual agreement with the transition contract.

Call a low-shelf reduction **bass attenuation** unless rendered low-band contribution proves the
intended bass handover. Each automation contract identifies the exact owner/device/parameter and
required result; target resolution is unique, stale envelopes for that target are removed, and the
final ALS plus render are compared with the contract.

Sam then marks `ACCEPT`, `CORRECT`, or `REJECT` for rhythmic feel, bass swap, phrasing, musical
intent, energy, warp sound, transition length, and overall listening quality. **Every transition
must pass individually; an average score cannot hide one failed mix.** `TRANSITION_ACCEPTED`
requires explicit `ACCEPT`, not merely absence of `REJECT`, for rhythmic feel, bass swap, phrasing,
musical intent, energy, warp sound, transition length, and overall listening quality. Every prior
`CORRECT` must be re-reviewed to `ACCEPT` against the superseding render. Repeated failure of the
same blocking dimension marks the candidate/sequence infeasible rather than leaving it indefinitely
in `CORRECT`.

A `CORRECT` or `REJECT` returns the affected episode and its declared dependencies to Stage 3, then
re-enters Stage 4 with a new MixPlan/ALS/render version. The loop terminates only when the structured
acceptance record contains all eight required `ACCEPT` values.

### 5. Local Correction And Rebuild Loop

- A failed review produces structured reason codes and corrected landmarks/settings, not only a
  prose note. Examples: wrong section, late entry, early bass, vocal clash, loop fatigue, tempo-ramp
  audibility, Re-Pitch too sharp/flat, Complex smear, weak energy handover, or automation shape.
- Re-plan and render only the affected episode plus protected context on either side. Preserve all
  previously accepted transitions unless the correction changes sequence, global tempo, or shared
  track exposure; those dependencies are explicitly invalidated and re-reviewed.
- Compare each revision against its previous render and contract. Keep rejected versions for
  evidence but never allow them into the final ALS.
- Store an approval dependency graph. Every approval records the exact source hash/version,
  sequence neighbours/position, tempo-curve version, warp-policy version, section-map version,
  arrangement contract, automation/loop plan, ALS build, and render hash on which it depends.
  Changing any dependency invalidates that approval automatically.
- Recompute the dependency graph on every edit. A tempo event change invalidates every transition
  whose audible window crosses it; a Warp Mode/clip change invalidates both adjacent transitions
  sharing that clip; and source version, section map, or shared exposure changes invalidate every
  transition containing that `track_instance_id`.
- Stop only when the transition is accepted or the track/order is declared infeasible and the
  sequence is replanned.

**Gate:** zero unresolved transition failures and zero stale approvals after dependency changes.

### 6. Whole-Mix Acceptance

- Render and inspect the uninterrupted mix in two recorded passes. The **musical-arc pass** covers
  opening, pacing, track exposure, transition variety, key journey, tempo curve, energy/tension
  arc, climax placement, cooldown/ending, runtime, attention retention at fixed points through the
  mix, and whether any locally good transition harms the larger story. The **transition/technical
  pass** revisits every cue without the review visuals and may reopen any local approval.
- Measure integrated/short-term loudness, true peak, headroom during overlaps, gain discontinuities,
  silence/gaps/clicks, render length, and correspondence between final WAV and approved ALS against
  the MixBrief targets rather than a universal club-loudness number.
- Review the BPM/warp report across the whole timeline: no accidental reversals, excessive ramps,
  out-of-policy Re-Pitch, avoidable Complex processing, or effective-key clashes.
- Run the musical-arc pass uninterrupted after all transition approvals, with listening conditions
  recorded and fatigue managed between the two passes. Any issue reopens the relevant local and
  downstream approvals; the mix is not accepted by checklist alone.

The whole-mix record stores separate `ACCEPT/CORRECT/REJECT` decisions for opening intent, pacing,
track-exposure balance, transition variety, key journey, tempo-curve legitimacy, energy arc, climax,
cooldown/ending, attention retention, and final sound. Any non-`ACCEPT` reopens the responsible
episodes/dependencies and requires new transition plus whole-mix records.

A reopen discovered in either whole-mix pass invalidates **both** pass records. Stage 6 closes only
when one clean musical-arc pass and one clean transition/technical pass run against the identical
final MixPlan, ALS, and render hashes with zero intervening changes or reopens.

**Gate:** every transition remains accepted, all objective render/ALS checks pass, and Sam approves
the complete musical flow and final sound.

### 7. Freeze, Delivery, And Reproduction

- Freeze the approved `.als`, collected source references or project bundle as policy permits,
  final 24-bit WAV, review render, transition cards/previews, MixBrief, tempo/warp report, section
  certificates, arrangement contracts, automation/loop plans, validation reports, source hashes,
  tool/model versions, overrides, and decision provenance.
- Open the frozen ALS in Ableton and confirm clips, Warp Modes, tempo automation, devices, routing,
  and automation render as expected. Re-render a checksum-labelled proof from the frozen set when
  practical.
- Complete an Ableton-open checklist in the declared Live version that maps every MixPlan clip,
  loop, Warp Mode, tempo event, automation lane/target, and transition landmark to the visible saved
  set. Any missing/mismatched row blocks delivery.
- Re-read the Ableton-saved ALS and reconcile semantic identities using source hash, played source
  window, arrangement interval, owner/routing path, and parameter target. Ableton's internal XML IDs
  may change on save; they are not the stable MixPlan IDs. Ambiguous or unmatched elements block
  delivery with the divergent element named.
- Record unsupported/fallback decisions separately from learned decisions so the next mix can
  improve without rewriting history.
- A release is complete only when the frozen artifacts reproduce the approved timeline and the
  delivered WAV hash matches the accepted master.

## Non-Negotiable Guards

- Read approved ALS files only; never write, resave, relocate, or modify them.
- Do not use the old `Teaching Mixes/` corpus.
- Do not use backups or near-name versions without explicit manifest selection.
- Do not infer audibility from clip overlap alone.
- Do not assume one song per track, longest clip equals song, two-track transitions, constant
  tempo, 4/4 meter, resolved source audio, or available automation.
- Do not use Kick Detector V3, locators, colours, filenames, or ALS edits as sole ground truth.
- Do not pool transitions across projects before project-balanced summaries.
- Do not let sequential sides, multiple versions, or source-fingerprint duplicates from the same
  release event leak across train/test splits.
- Do not treat an unused section or boundary as a negative example.
- Do not treat exact historical boundary error as the only measure of a good DJ transition.
- Do not learn commission running order, runtime, exposure, or source-version constraints as
  transferable DJ preferences.
- Do not invent lead/hook, section-subrole, or harmonic evidence when the pilot cannot support it.
- Do not treat studio-only or live-reproducible techniques as inherently better; retain the flag
  as descriptive context.
- Do not generate deployment priors before the human gold set and falsification gates.
- Do not let any learned preference override the 48-bar overlap cap, loop-count caps, timing
  integrity, or ALS validation.
- Do not select one fixed BPM, tempo arc, or Warp Mode without recording the creative strategy and
  the audible tempo range of every clip.
- Do not use raw BPM difference as the only Re-Pitch criterion; calculate the actual cents change
  over the clip's audible tempo range.
- Do not change a tempo curve, source version, section map, sequence, or shared-track edit without
  invalidating every dependent transition approval.
- Do not declare a mix complete from aggregate metrics. Every transition and one uninterrupted
  final listening pass must be accepted.
