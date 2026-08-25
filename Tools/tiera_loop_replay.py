"""Tier A Phase 2 corpus replay for the loop-gate similarity term (Signal 1).

Enumerates EVERY candidate loop window over a corpus and scores its
self-similarity in both flag states:

  OFF  - the pinned base-stem feature set (current production behaviour)
  ON   - LOOP_SELF_SIMILARITY_TIERA=True: base stems + the FIXED tiera_* set

then reports every verdict flip against LOOP_MIN_SELF_SIMILARITY with the
measured evidence needed to judge each flip correct or spurious.

Enumeration (declared, so the numbers are reproducible): for each track, every
window of an allowed loop period (4/8/16/32 beats) starting on a bar line
(every 4 beats), lying fully inside the beat range measurable by BOTH flag
states (min consensus length across base+tiera envelope keys). The 2026-08-20
accidental-coupling measurement (315/3738) used an enumeration that was not
preserved; this one is written down instead.

Judgment rule per flip (declared up front, evidence printed per row):
  pass->reject (ON rejects what OFF passed) is CORRECT when the window shows a
  real mid-window texture change any of these independent measures confirms:
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
  reject->pass flips are graded the other way round: evidence present means a
  real defect got UN-caught (regression, bad), no evidence means the extra
  correlated features damped a noisy clean score (benign).

Usage (from a repo/worktree root):
    PYTHONPATH=Source python Tools/tiera_loop_replay.py "<stem analysis dir>" \
        --mode both --out replay.json
    --mode off-only  scores only the OFF state (for the byte-identical proof:
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
    ap.add_argument("stem_dir", type=Path)
    ap.add_argument("--mode", choices=("both", "off-only"), default="both")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    if args.mode == "both" and not _supports_use_tiera():
        raise SystemExit("this align_engine has no use_tiera support; "
                         "use --mode off-only")

    rows = []
    flips = []
    n_windows = 0
    n_windows_8beat = 0
    for cache in sorted(args.stem_dir.glob("*__stemenv.npz")):
        context = AE.load_loop_quality_context(cache)
        track = cache.name[: -len("__stemenv.npz")]
        meta = json.loads(
            (args.stem_dir / f"SECTIONS_STEM_{track}.json").read_text(
                encoding="utf-8"))
        sections = meta.get("sections") or []
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
                row = {"track": track, "beat_start": s, "beat_end": e,
                       "period": p, "selfsim_off": off,
                       "verdict_off": bool(off >= AE.LOOP_MIN_SELF_SIMILARITY)}
                if args.mode == "both":
                    on = _selfsim(context, s, e, tiera=True)
                    row["selfsim_on"] = on
                    row["verdict_on"] = (
                        None if on is None
                        else bool(on >= AE.LOOP_MIN_SELF_SIMILARITY))
                    if row["verdict_on"] is not None and (
                            row["verdict_on"] != row["verdict_off"]):
                        direction = ("pass->reject" if row["verdict_off"]
                                     else "reject->pass")
                        ev = _evidence(context, sections, s, e)
                        judgment, why = _judge(direction, ev)
                        flip = dict(row)
                        flip["direction"] = direction
                        flip["evidence"] = ev
                        flip["judgment"] = judgment
                        flip["why"] = why
                        flips.append(flip)
                rows.append(row)

    print(f"windows enumerated: {n_windows} "
          f"(8-beat subset: {n_windows_8beat}) over "
          f"{len(list(args.stem_dir.glob('*__stemenv.npz')))} tracks")
    if args.mode == "both":
        by_dir = {}
        by_judgment = {}
        for f in flips:
            by_dir[f["direction"]] = by_dir.get(f["direction"], 0) + 1
            key = f["direction"] + ":" + f["judgment"]
            by_judgment[key] = by_judgment.get(key, 0) + 1
        print(f"verdict flips ON vs OFF: {len(flips)}")
        for k in sorted(by_dir):
            print(f"  {k}: {by_dir[k]}")
        for k in sorted(by_judgment):
            print(f"  {k}: {by_judgment[k]}")
        for f in flips:
            on = f["selfsim_on"]
            print(f"  FLIP {_ascii(f['track'])[:44]:44} beats "
                  f"{f['beat_start']:4d}-{f['beat_end']:4d} "
                  f"off={f['selfsim_off']:.3f} on={on:.3f} "
                  f"{f['direction']:12} {f['judgment']:10} {_ascii(f['why'])}")
    if args.out:
        payload = {"n_windows": n_windows, "rows": rows, "flips": flips}
        args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
