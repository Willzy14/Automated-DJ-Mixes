# Stem-Based Section Detection — Spike Findings (2026-06-08)

## Why
The whole pipeline's correctness rests on **section detection** — once each track is
cut into correctly-labelled pieces (intro/build/drop/break/fill/outro), the rest
(arrangement, loops, automation) is a deterministic jigsaw. The current detector
(Rekordbox phrases + a 3-band low/mid/high amplitude heuristic) has a ceiling and
needs per-run human visual validation. The 3-band split is a *crude proxy* for
"which instruments are playing." This spike tested whether real stem separation
(Demucs) gives a cleaner, more reliable substrate.

## Hard constraint (locked)
**Analysis only.** Stems are derived purely to *read* structure. Separation runs
in-memory; no stem audio is written to disk (only tiny per-stem RMS envelopes are
cached). The **original WAV is never altered and is the only audio that goes in a
mix** — Demucs separation is lossy, so it must never touch the actual audio.

## Result — stems clearly beat the amplitude detector
Tested two flagged tracks (`Source/stem_section_probe.py`):

- **VLAD – I'm Glued** — the current detector found only **4 sections** for a 287s
  track, labelling a **148s stretch as one "build"** and a **109s "outro."** The
  stem panels show that "build" is full of real drops, breaks and re-entries. The
  structure was always there; the amplitude detector couldn't see it. **Stem
  detection would fix a genuine failure case.**
- **Call Me – Dunmore Brothers** — confirmed the auto-flagger's proposed DELETE of
  the bar-80 "break": stems show bass + drums barely move there (Δbass −0.10,
  Δdrums +0.11); only vocals drop (−0.99). It's a vocal change, not a DJ break.
  Independent signals agreeing = the confidence mechanism we want.

PNGs: `Test Project/08.06.26 Mix/_Stem Analysis/PROBE_*.png` (original track on top,
4 stems below, section zones coloured + labelled on a shared timeline).

## What stems unlock that the old detector never could (Sam, 2026-06-08)
A section literally *is* a combination of which stems are on. That makes three real
DJ techniques programmable for the first time:
1. **Bass-to-bass lock** — the bass stem marks where bass ends on the outgoing track
   and begins on the incoming one: the cleanest possible mix point.
2. **Loop windows** — "drums on, bass off" regions are clean loop material for
   extending mixes. Stems locate them directly.
3. **Vocal-clash avoidance** — knowing where vocals sit means never stacking two
   thick vocal sections over each other.

## Engineering notes
- **Separation:** Demucs `htdemucs`, in-memory, I/O via `soundfile` (NOT torchaudio
  — torchaudio 2.x routes WAV writes through torchcodec, which needs FFmpeg DLLs not
  present on this Windows box). Avoiding torchaudio I/O is cleaner for productisation
  too.
- **Compute:** ~76s/track on CPU (torch is the `+cpu` build). The machine has an
  **RTX 3050** — when we build this out, install the CUDA torch build so it runs on
  GPU from the start (~10–15s/track), cached once per track.
- The per-boundary verdict heuristic in the probe is still naive (too bass-centric —
  it flagged a legit drums-out break as "doubtful"). The stem *signal* is the win;
  the rules to read it need proper design.

## Productisation angle
The biggest liability in the pipeline is its dependence on driving **Rekordbox** and
**Mixed In Key** via Windows UI automation — borrowed, brittle, and unsellable.
- **allin1** (trained beat/downbeat/tempo + functional segmentation) + **stems**
  could eventually **replace Rekordbox** (beat grid + phrases). Big speed + ownership
  win.
- **Mixed In Key** is still needed for **key → Camelot** harmonic mixing (could later
  be replaced by an open-source key detector, lower priority).

## Next steps
1. Try **allin1** as a trained second opinion (install its deps — `natten`, `madmom`
   — may be painful on Python 3.14; a dedicated 3.11/3.12 env may be needed).
2. Build a proper **stem-based detector**: per-stem presence envelopes → boundaries
   from stem on/off + energy steps → labels from stem-presence rules → emit
   bass-to-bass points, loop windows, and vocal regions.
3. **Ensemble + confidence:** vote stems vs Rekordbox phrases vs amplitude vs MIK;
   auto-trust on agreement, only surface disagreements for review.
