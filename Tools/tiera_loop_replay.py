"""Tier A Phase 2 corpus replay for the loop-gate similarity term (Signal 1).

Enumerates EVERY candidate loop window over a corpus and scores its
self-similarity under both feature sets:

  base   - the pinned 5-key base-stem feature set (LOOP_SELF_SIMILARITY_TIERA
           off; also the term evaluate_loop_quality ALWAYS computes now,
           regardless of the flag)
  tiera  - base stems + the FIXED tiera_* set (10 keys)

then derives TWO verdict systems against LOOP_MIN_SELF_SIMILARITY:

  verdict_off       - base score alone (what flag-off production gates on
                      today, and what the base term always gates on
                      regardless of the flag)
  verdict_and       - the AND-semantics gate the 2026-09-15 rebuild made
                      production compute when the flag is on: PASS only if
                      BOTH the base score AND the tiera score (when
                      measurable) clear the threshold. Because this always
                      requires verdict_off to also be PASS, verdict_and can
                      only ever flip PASS->REJECT relative to verdict_off -
                      REJECT->PASS is structurally impossible. Every AND-gate
                      flip found is asserted to be pass->reject; a
                      reject->pass AND-gate flip would mean the gate math
                      itself has a bug, not evidence about the corpus.
  verdict_blended   - the ORIGINAL (2026-08-25, 719be92) design this rebuild
                      replaced: the tiera score REPLACES the base score
                      outright when on, rather than supplementing it. Kept
                      here purely for historical comparison in the summary -
                      production no longer computes this quantity anywhere.
                      This is the one that could regress (a strong tiera
                      score outvoting a real base failure = reject->pass),
                      which is exactly the class of bug the AND-semantics
                      rebuild exists to make structurally impossible.

Enumeration (declared, so the numbers are reproducible): for each track, every
window of an allowed loop period (4/8/16/32 beats) starting on a bar line
(every 4 beats), lying fully inside the beat range measurable by BOTH feature
sets (min consensus length across base+tiera envelope keys). The 2026-08-20
accidental-coupling measurement (315/3738) used an enumeration that was not
preserved; this one is written down instead.

Judgment rule per AND-gate flip (declared up front, evidence printed per row):
  pass->reject (the AND gate rejects what the base score alone passed) is
  CORRECT when the window shows a real mid-window texture change any of these
  independent measures confirms:
    - a detected section boundary strictly inside the window (loop material
      spanning a section change is defective by construction), or
    - per-beat mix level span >= 6 dB inside the window (a level cliff the
      dip-vs-median check under-reports when the cliff splits the window), or
    - per-beat stereo-width span >= 3 dB (a real stereo-texture change - the
      Vente/Revoloution class that loudness measures cannot see), or
    - any tiera band per-beat span >= 6 dB while the mix span stays < 6 dB
      (a spectral swap at constant loudness).
  Flips with none of the above are SPURIOUS (dimensionality moved the cosine
  without any single feature showing a real change).

Usage (from a repo/worktree root), one or more corpus directories:
    PYTHONPATH=Source python Tools/tiera_loop_replay.py \
        "<stem analysis dir 1>" "<stem analysis dir 2>" ... \
        --mode both --out replay.json
    --mode off-only  scores only the base state (for the byte-identical proof:
                     run once with main's Source on PYTHONPATH, once with the
                     branch's, diff the two JSON files).
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

# ADJ_SOURCE_DIR lets the byte-identical proof point one run at main's Source
# and the other at the branch's, unambiguously, from the same tool file.
sys.path.insert(0, os.environ.get(
    "ADJ_SOURCE_DIR",
    str(Path(__file__).resolve().parent.parent / "Source")))

import align_engine as AE

ALLOWED_PERIODS = (4, 8, 16, 32)
TIERA_KEYS = ("tiera_band_low", "tiera_band_mid", "tiera_band_high",
              "tiera_width", "tiera_lr_corr")
BASE_KEYS = ("drums", "bass", "other", "vocals", "mix")


def _ascii(s: str) -> str:
    return s.encode("ascii", "replace").decode("ascii")


def _supports_use_tiera() -> bool:
    return "use_tiera" in inspect.signature(AE._loop_self_similarity).parameters


def _selfsim(context, s, e, tiera: bool) -> float | None:
    if tiera:
        return AE._loop_self_similarity(context, s, e, use_tiera=True)
    return AE._loop_self_similarity(context, s, e)


def _common_end_beat(context) -> int:
    keys = [k for k in BASE_KEYS if k in context.envelopes]
    keys += [k for k in TIERA_KEYS if k in context.envelopes
             and len(context.envelopes[k]) > 0]
    length = min(len(context.envelopes[k]) for k in keys)
    return int(math.floor(
        (length * context.hop_sec - context.downbeat_sec)
        * context.bpm / 60.0
    ))


def _per_beat_series(context, key, s, e):
    """Per-beat mean of an envelope over [s, e) beats (dB for energy-style
    keys is applied by the caller)."""
    out = []
    env = context.envelopes[key]
    for beat in range(int(s), int(e)):
        mask = AE._quality_frame_slice(context, beat, beat + 1, len(env))
        vals = env[mask]
        out.append(float(np.mean(vals)) if vals.size else 0.0)
    return np.asarray(out, dtype=float)


def _evidence(context, sections, s, e):
    ev = {}
    mix = _per_beat_series(context, "mix", s, e)
    mix_db = 20.0 * np.log10(np.maximum(mix, 1e-12))
    ev["mix_span_db"] = round(float(mix_db.max() - mix_db.min()), 2)
    boundary_beats = sorted({int(sec["start_bar"]) * 4 for sec in sections})
    ev["section_boundary_inside"] = any(s < b < e for b in boundary_beats)
    if "tiera_width" in context.envelopes and len(context.envelopes["tiera_width"]):
        w = _per_beat_series(context, "tiera_width", s, e)
        w = np.maximum(w, 1e-6)
        ev["width_span_db"] = round(float(
            20.0 * np.log10(w.max() / w.min())), 2)
    else:
        ev["width_span_db"] = None
    band_spans = {}
    for key in ("tiera_band_low", "tiera_band_mid", "tiera_band_high"):
        if key in context.envelopes and len(context.envelopes[key]):
            b = _per_beat_series(context, key, s, e)
            b_db = 20.0 * np.log10(np.maximum(b, 1e-12))
            band_spans[key] = round(float(b_db.max() - b_db.min()), 2)
    ev["band_spans_db"] = band_spans
    return ev


def _judge(direction: str, ev: dict) -> tuple[str, str]:
    reasons = []
    if ev["section_boundary_inside"]:
        reasons.append("section boundary inside window")
    if ev["mix_span_db"] >= 6.0:
        reasons.append(f"mix level span {ev['mix_span_db']} dB")
    if ev["width_span_db"] is not None and ev["width_span_db"] >= 3.0:
        reasons.append(f"width span {ev['width_span_db']} dB")
    if ev["mix_span_db"] < 6.0:
        big_band = {k: v for k, v in ev["band_spans_db"].items() if v >= 6.0}
        if big_band:
            reasons.append("band span >= 6 dB at flat mix level: "
                           + ", ".join(f"{k}={v}" for k, v in big_band.items()))
    has_evidence = bool(reasons)
    why = "; ".join(reasons) if reasons else "no independent evidence"
    if direction == "pass->reject":
        return ("CORRECT" if has_evidence else "SPURIOUS"), why
    return ("REGRESSION" if has_evidence else "BENIGN"), why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stem_dirs", type=Path, nargs="+",
                     help="one or more _Stem Analysis directories; results "
                          "are pooled across all of them")
    ap.add_argument("--mode", choices=("both", "off-only"), default="both")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    if args.mode == "both" and not _supports_use_tiera():
        raise SystemExit("this align_engine has no use_tiera support; "
                         "use --mode off-only")

    rows = []
    and_flips = []
    blended_flips_by_direction = {"pass->reject": 0, "reject->pass": 0}
    n_windows = 0
    n_windows_8beat = 0
    n_tracks = 0
    n_missing_sections = 0
    seen_track_names = {}
    for stem_dir in args.stem_dirs:
        caches = sorted(stem_dir.glob("*__stemenv.npz"))
        n_tracks += len(caches)
        for cache in caches:
            context = AE.load_loop_quality_context(cache)
            track = cache.name[: -len("__stemenv.npz")]
            seen_track_names.setdefault(track, []).append(str(stem_dir))
            sections_path = stem_dir / f"SECTIONS_STEM_{track}.json"
            sections = []
            if sections_path.exists():
                meta = json.loads(sections_path.read_text(encoding="utf-8"))
                sections = meta.get("sections") or []
            else:
                # NOT enforced/fatal (a corpus dir may genuinely lack section
                # maps for some tracks) -- but silent here would mean
                # "section boundary inside window" evidence is unavailable
                # for this track without anyone noticing, which can make a
                # genuinely CORRECT flip read as SPURIOUS for lack of that
                # one evidence source. Surfaced instead of hidden.
                n_missing_sections += 1
                print(f"WARNING: no SECTIONS_STEM json for {track!r} in "
                      f"{stem_dir} -- section-boundary evidence unavailable "
                      f"for this track's flips")
            end_beat = _common_end_beat(context)
            for s in range(0, end_beat, 4):
                for p in ALLOWED_PERIODS:
                    e = s + p
                    if e > end_beat:
                        continue
                    n_windows += 1
                    if p == 8:
                        n_windows_8beat += 1
                    off = _selfsim(context, s, e, tiera=False)
                    row = {"dir": str(stem_dir), "track": track,
                           "beat_start": s, "beat_end": e, "period": p,
                           "selfsim_off": off,
                           "verdict_off": bool(off >= AE.LOOP_MIN_SELF_SIMILARITY)}
                    if args.mode == "both":
                        on = _selfsim(context, s, e, tiera=True)
                        verdict_on = (
                            None if on is None
                            else bool(on >= AE.LOOP_MIN_SELF_SIMILARITY))
                        # verdict_and: the REAL AND-semantics gate production
                        # computes today. Can only ever be a stricter subset
                        # of verdict_off (pass->reject only) by construction.
                        verdict_and = row["verdict_off"] and (
                            verdict_on is None or verdict_on)
                        row["selfsim_on"] = on
                        row["verdict_on"] = verdict_on
                        row["verdict_and"] = verdict_and
                        if verdict_and != row["verdict_off"]:
                            direction = ("pass->reject" if row["verdict_off"]
                                         else "reject->pass")
                            if direction == "reject->pass":
                                raise AssertionError(
                                    "AND-gate produced a reject->pass flip - "
                                    "this contradicts the gate's own "
                                    "construction and means the replay or "
                                    "the production gate has a real bug: "
                                    f"{track} beats {s}-{e}")
                            ev = _evidence(context, sections, s, e)
                            judgment, why = _judge(direction, ev)
                            flip = dict(row)
                            flip["direction"] = direction
                            flip["evidence"] = ev
                            flip["judgment"] = judgment
                            flip["why"] = why
                            and_flips.append(flip)
                        # Historical-comparison-only: what the ORIGINAL
                        # (replaced) blended-score design would have done.
                        # Not gated on today; not written into `and_flips`.
                        if verdict_on is not None and verdict_on != row["verdict_off"]:
                            bdir = ("pass->reject" if row["verdict_off"]
                                    else "reject->pass")
                            blended_flips_by_direction[bdir] += 1
                    rows.append(row)

    print(f"windows enumerated: {n_windows} "
          f"(8-beat subset: {n_windows_8beat}) over {n_tracks} track-entries "
          f"({len(seen_track_names)} unique track names) across "
          f"{len(args.stem_dirs)} corpus dir(s)")
    dupes = {name: dirs for name, dirs in seen_track_names.items() if len(dirs) > 1}
    if dupes:
        print(f"NOTE: {len(dupes)} track name(s) appear in more than one corpus "
              f"dir (not deduplicated -- windows from each appearance are all "
              f"counted, so track-entries overstates independent-track "
              f"coverage):")
        for name in sorted(dupes)[:10]:
            print(f"  {_ascii(name)[:60]}: {len(dupes[name])} dirs")
        if len(dupes) > 10:
            print(f"  ... and {len(dupes) - 10} more")
    if n_missing_sections:
        print(f"NOTE: {n_missing_sections} track(s) had no SECTIONS_STEM json "
              f"(section-boundary evidence unavailable for those flips -- see "
              f"WARNING lines above)")
    if args.mode == "both":
        by_judgment = {}
        for f in and_flips:
            key = f["direction"] + ":" + f["judgment"]
            by_judgment[key] = by_judgment.get(key, 0) + 1
        print(f"\nAND-GATE (production, 2026-09-15 rebuild) flips vs base-only: "
              f"{len(and_flips)}")
        print(f"  pass->reject: {len(and_flips)}")
        print(f"  reject->pass: 0 (structurally impossible by construction)")
        for k in sorted(by_judgment):
            print(f"  {k}: {by_judgment[k]}")
        for f in and_flips:
            print(f"  FLIP {_ascii(f['track'])[:44]:44} beats "
                  f"{f['beat_start']:4d}-{f['beat_end']:4d} "
                  f"off={f['selfsim_off']:.3f} on={f['selfsim_on']:.3f} "
                  f"{f['direction']:12} {f['judgment']:10} {_ascii(f['why'])}")
        print(f"\nHISTORICAL COMPARISON ONLY - the original (replaced) "
              f"blended-score design (719be92), not what production computes "
              f"today:")
        print(f"  pass->reject: {blended_flips_by_direction['pass->reject']}")
        print(f"  reject->pass: {blended_flips_by_direction['reject->pass']} "
              f"(the un-catch class the AND-gate rebuild eliminates)")
    if args.out:
        payload = {
            "n_windows": n_windows, "n_tracks": n_tracks,
            "rows": rows, "and_flips": and_flips,
            "blended_flips_by_direction": blended_flips_by_direction,
        }
        args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
