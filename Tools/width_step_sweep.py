"""Tier A Phase 2 corpus sweep for the stereo-width step boundary candidate
(Signal 2).

Runs stem_detector.detect() twice per track over a corpus of CACHED envelopes
(no audio decode - the npz + SECTIONS json are the inputs, exactly the
Tools/section_soft_rules_sweep.py pattern):

  OFF  - width_cues=False (default, must be byte-identical to current output)
  ON   - width_cues=True

and reports, per track: every width-step cue that fired (bar, pre/post width,
step dB), every section-boundary difference ON vs OFF, and the RMS context at
each new cut (the Revoloution class is "width falls, RMS dead flat" - a new
cut whose RMS also moves was already catchable by energy cues and is worth
calling out).

The acceptance standard is the one 6a717f4 shipped under: the target boundary
found, zero spurious new cuts corpus-wide, with every new boundary listed and
judged here rather than asserted.

Usage (from a repo/worktree root):
    PYTHONPATH=Source python Tools/width_step_sweep.py "<corpus root>" \
        [--json out.json]
where <corpus root> contains "_Stem Analysis" with *__stemenv.npz +
SECTIONS_STEM_*.json pairs. detect() is called write_json=False make_viz=False
so the sweep is read-only against the corpus.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

import stem_detector as sd


def _ascii(s: str) -> str:
    return s.encode("ascii", "replace").decode("ascii")


def _bpm_downbeat(meta: dict) -> tuple[float, float]:
    bpm = float(meta["bpm"])
    sec_per_bar = 4 * 60.0 / bpm
    first = meta["sections"][0]
    downbeat = float(first["start_sec"]) - int(first["start_bar"]) * sec_per_bar
    return bpm, downbeat


def _bounds(res: dict) -> list[tuple[int, str]]:
    return [(int(s["start_bar"]), s["label"]) for s in res["sections"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", type=Path)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    stem_dir = args.corpus / "_Stem Analysis"
    report = []
    n_new = n_moved = n_cues = 0
    for meta_path in sorted(stem_dir.glob("SECTIONS_STEM_*.json")):
        track = meta_path.name[len("SECTIONS_STEM_"):-len(".json")]
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        bpm, downbeat = _bpm_downbeat(meta)
        wav = args.corpus / "Audio" / f"{track}.wav"   # cache hit: never read

        off = sd.detect(wav, args.corpus, bpm=bpm, downbeat=downbeat,
                        make_viz=False, write_json=False)
        on = sd.detect(wav, args.corpus, bpm=bpm, downbeat=downbeat,
                       make_viz=False, write_json=False, width_cues=True)

        # The cues themselves, for the report (recomputed via the helper so the
        # sweep prints them even when a cue did not survive to a boundary).
        from _tier_a_features import (TIERA_WIDTH_KEY, ensure_tier_a_arrays)
        tiera = ensure_tier_a_arrays(wav, stem_dir)
        import numpy as _np
        with _np.load(stem_dir / f"{track}__stemenv.npz") as d:
            hop_t = float(d["hop_t"])
        sec_per_bar = 4 * 60.0 / bpm
        n_bars = int(off["n_bars"])
        width_bar = sd._per_bar(tiera[TIERA_WIDTH_KEY], hop_t, downbeat,
                                sec_per_bar, n_bars)
        mix_env = None
        with _np.load(stem_dir / f"{track}__stemenv.npz") as d:
            mix_env = d["mix"].astype(float)
        mix_bar = sd._per_bar(mix_env, hop_t, downbeat, sec_per_bar, n_bars)
        mix_norm = mix_bar / (mix_bar.max() + 1e-9)
        cues = sd._width_step_cues(width_bar, mix_norm, downbeat, sec_per_bar)
        n_cues += len(cues)

        b_off = _bounds(off)
        b_on = _bounds(on)
        set_off = {b for b, _ in b_off}
        set_on = {b for b, _ in b_on}
        new_bars = sorted(set_on - set_off)
        lost_bars = sorted(set_off - set_on)
        n_new += len(new_bars)
        n_moved += len(lost_bars)

        entry = {"track": track, "cues": cues,
                 "bounds_off": b_off, "bounds_on": b_on,
                 "new_bars": new_bars, "lost_bars": lost_bars}
        # RMS context at each new cut: per-bar mix dB medians 8 bars each side.
        for b in new_bars:
            mdb = 20.0 * np.log10(np.maximum(mix_bar, 1e-12))
            pre = float(np.median(mdb[max(0, b - 8):b])) if b > 0 else None
            post = float(np.median(mdb[b:b + 8]))
            entry.setdefault("rms_context", {})[str(b)] = {
                "pre_db": None if pre is None else round(pre, 2),
                "post_db": round(post, 2),
                "delta_db": None if pre is None else round(post - pre, 2),
            }
        report.append(entry)

        if cues or new_bars or lost_bars:
            print(f"{_ascii(track)[:52]}")
            for c in cues:
                print(f"  cue  bar {c['bar']:6.1f} (detected {c.get('detected_bar')})  "
                      f"width {c['pre_width']:.3f} -> {c['post_width']:.3f}  "
                      f"step {c['step_db']:+.1f} dB  rms {c.get('rms_delta_db'):+.2f} dB")
            for b in new_bars:
                lab = next(l for bb, l in b_on if bb == b)
                rc = entry.get("rms_context", {}).get(str(b), {})
                print(f"  NEW boundary bar {b} ({lab})  rms pre/post "
                      f"{rc.get('pre_db')}/{rc.get('post_db')} dB "
                      f"(delta {rc.get('delta_db')})")
            for b in lost_bars:
                print(f"  LOST/MOVED boundary bar {b}")

    print("-" * 70)
    print(f"tracks: {len(report)}  width cues fired: {n_cues}  "
          f"new boundaries: {n_new}  lost/moved boundaries: {n_moved}")
    if args.json:
        args.json.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
