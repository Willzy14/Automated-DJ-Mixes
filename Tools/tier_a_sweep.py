"""Corpus sweep for the Tier A feature build.

Reads the existing 14.08.26 SECTIONS_STEM_*.json files for bpm + downbeat,
runs ensure_tier_a_arrays on every wav (idempotent — the cache is
populated on first call and reused on subsequent calls), then prints the
per-bar min/max for each band + the width/corr/vocal summary. Flagging
absurd values (all-zero, NaN, width > 1.5) so the orchestrator can see
outliers at a glance.

Read-only against the JSON side-artifacts (write_json=False, make_viz=False
implicitly — we never call detect() here, only the cache layer). The ONLY
write is the __stemenv.npz augmentation, which is part of the documented
Phase 1 deliverable.

Usage:
    PYTHONPATH=Source python Tools/tier_a_sweep.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

import _tier_a_features as taf
from _tier_a_features import (
    TIERA_BAND_LOW_KEY, TIERA_BAND_MID_KEY, TIERA_BAND_HIGH_KEY,
    TIERA_WIDTH_KEY, TIERA_LR_CORR_KEY,
)

CORPUS = Path("Test Project/14.08.26")
STEM_DIR = CORPUS / "_Stem Analysis"


def _ascii_safe(name: str) -> str:
    """Soulsearcher's filename has a unicode dash. cp1252 console would
    raise on print(); replace non-ascii with '?' so the report is safe
    to copy/paste into any terminal."""
    return name.encode("ascii", "replace").decode("ascii")


def _bpm_downbeat(json_path: Path) -> tuple[float, float, int]:
    """Mirror the Tools/section_soft_rules_sweep.py pattern: derive bpm
    + downbeat from the cached SECTIONS_STEM_*.json. The Blind_V stats
    JSONs the brief references aren't in this worktree, so we use the
    cached sections to reconstruct downstream's inputs."""
    d = json.loads(json_path.read_text(encoding="utf-8"))
    bpm = float(d["bpm"])
    n_bars = int(d["n_bars"])
    sec_per_bar = 4 * 60.0 / bpm
    first = d["sections"][0]
    downbeat = float(first["start_sec"]) - int(first["start_bar"]) * sec_per_bar
    return bpm, downbeat, n_bars


def _per_bar(arr: np.ndarray, hop_t: float, downbeat: float, sec_per_bar: float, n_bars: int) -> np.ndarray:
    """Mirror stem_detector._per_bar so the sweep's per-bar conversion
    is identical to the detector's (no off-by-one drift)."""
    out = np.zeros(n_bars)
    for b in range(n_bars):
        i0 = int((downbeat + b * sec_per_bar) / hop_t)
        i1 = min(int((downbeat + (b + 1) * sec_per_bar) / hop_t), len(arr))
        out[b] = arr[i0:i1].mean() if i1 > i0 else 0.0
    return out


def _flag_row(row: dict) -> list[str]:
    """Return the list of absurd-value flags (one per detected problem)."""
    flags = []
    if row["band_low_max"] == 0.0 and row["band_mid_max"] == 0.0 and row["band_high_max"] == 0.0:
        flags.append("ALL_ZERO_BANDS")
    if any(np.isnan(v) for v in (
        row["band_low_max"], row["band_mid_max"], row["band_high_max"],
        row["width_max"], row["lr_corr_min"],
    )):
        flags.append("HAS_NAN")
    if row["width_max"] > 1.5:
        flags.append("WIDTH_HIGH")
    if row["vocal_active_frac"] <= 0.0:
        flags.append("NO_VOCAL")
    if row["vocal_active_frac"] >= 1.0:
        flags.append("ALL_VOCAL")
    return flags


def sweep_all() -> list[dict]:
    out = []
    for jp in sorted(STEM_DIR.glob("SECTIONS_STEM_*.json")):
        track = jp.stem[len("SECTIONS_STEM_"):]
        wav = CORPUS / "Audio" / f"{track}.wav"
        bpm, downbeat, n_bars = _bpm_downbeat(jp)
        sec_per_bar = 4 * 60.0 / bpm
        cascades = taf.ensure_tier_a_arrays(wav, STEM_DIR)
        hop_t = 0.1   # canonical from the existing envelope cache
        bands = {}
        for key, slot in (
            (TIERA_BAND_LOW_KEY, "band_low"),
            (TIERA_BAND_MID_KEY, "band_mid"),
            (TIERA_BAND_HIGH_KEY, "band_high"),
        ):
            pb = _per_bar(cascades[key], hop_t, downbeat, sec_per_bar, n_bars)
            bands[f"{slot}_min"] = float(pb.min())
            bands[f"{slot}_max"] = float(pb.max())
        width_pb = _per_bar(cascades[TIERA_WIDTH_KEY], hop_t, downbeat, sec_per_bar, n_bars)
        corr_pb = _per_bar(cascades[TIERA_LR_CORR_KEY], hop_t, downbeat, sec_per_bar, n_bars)
        # Vocal active fraction uses the documented helpers.
        vocals_pb = _per_bar(cascades[TIERA_BAND_LOW_KEY] * 0  # placeholder
                             if False else np.zeros(0), hop_t, downbeat, sec_per_bar, n_bars)
        # The cached vocals envelope is loaded by the detector's own
        # _separate_envelopes path; we can read it directly from the same
        # npz for the sweep (the cached vocals is the canonical source).
        import numpy as _np
        d = _np.load(STEM_DIR / f"{wav.stem}__stemenv.npz", allow_pickle=False)
        vocals_env = d["vocals"]
        vocals_pb = _per_bar(vocals_env, hop_t, downbeat, sec_per_bar, n_bars)
        active_mask = taf.vocal_activity_mask(vocals_pb)
        voc_frac = float(active_mask.mean()) if len(active_mask) else 0.0
        row = {
            "track": track,
            "bpm": bpm,
            "n_bars": n_bars,
            **bands,
            "width_min": float(width_pb.min()),
            "width_median": float(_np.median(width_pb)),
            "width_max": float(width_pb.max()),
            "lr_corr_min": float(corr_pb.min()),
            "lr_corr_median": float(_np.median(corr_pb)),
            "vocal_active_frac": voc_frac,
        }
        out.append(row)
    return out


def _fmt_row(row: dict) -> str:
    return (
        f"  {row['track'][:56]:56}  "
        f"bpm={row['bpm']:6.2f}  "
        f"low=[{row['band_low_min']:.4f},{row['band_low_max']:.4f}]  "
        f"mid=[{row['band_mid_min']:.4f},{row['band_mid_max']:.4f}]  "
        f"high=[{row['band_high_min']:.4f},{row['band_high_max']:.4f}]  "
        f"width=[{row['width_min']:.4f},{row['width_median']:.4f},{row['width_max']:.4f}]  "
        f"corr_min={row['lr_corr_min']:.4f}  "
        f"vocal={row['vocal_active_frac']:.3f}"
    )


def main():
    if not CORPUS.exists():
        print(f"Corpus not found: {CORPUS}", file=sys.stderr)
        sys.exit(1)
    rows = sweep_all()
    print(f"TIER A corpus sweep -- {len(rows)} tracks")
    print("-" * 130)
    for row in rows:
        print(_ascii_safe(_fmt_row(row)))
    print("-" * 130)
    flagged = sum(1 for r in rows if _flag_row(r))
    if flagged:
        print(f"FLAGGED ROWS ({flagged}):")
        for r in rows:
            fl = _flag_row(r)
            if fl:
                print(f"  {_ascii_safe(r['track'][:60])}  -> {','.join(fl)}")
    else:
        print("NO FLAGS. All values within sane bounds.")


if __name__ == "__main__":
    main()
