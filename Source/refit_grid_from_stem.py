"""Refit a track's beat grid from its Demucs DRUM-STEM kick attacks — the
escalation for percussion-dense tracks where BOTH full-mix onsets and
Ableton's .asd ticks read the anticipating percussion instead of the kick
(first case: La Trumpter — ticks sat one sixteenth early, kicks +113ms off
the tick-fitted grid; Sam's ear caught it in the V2 render).

Method:
  1. Separate drums + bass in memory (htdemucs, same as the section probe).
  2. Sample-accurate kick attack edges (lowpass -> envelope -> 10%-of-peak
     backtrack) — on an isolated stem these are unambiguous.
  3. Least-squares lattice fit (period + first) with outlier rejection.
  4. Downbeat offset from the BASS stem: house bass lands on the one —
     choose the offset that puts the most bass attacks on beat 1 of the bar.
  5. Write the replace_grid override (refuses a loose fit).

Usage:
    python Source/refit_grid_from_stem.py "<wav>" "<project dir>" [--dry-run]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from probe_stem_kick_grid import attack_onsets, stem_audio
from validate_beatgrid import load_grid_overrides, overrides_path


def load_or_separate_stems(wav: Path, project: Path) -> tuple[np.ndarray, np.ndarray, int]:
    """Drums + bass, sidecar-caches first, one Demucs separation at most.

    Cache path: both stem sidecars (drums float32, bass opus/int16 - Sam's
    keep-the-bass decision, 2026-08-27) beat a ~21 s re-separation. Any miss,
    mismatch or exception falls back to `stem_audio`, so a cold or legacy
    corpus behaves exactly as before.

    Fallback path PERSISTS what it computed (Codex blocker, 2026-08-27: the
    first version separated and then THREW THE BASS AWAY, so a legacy track
    with a warm drums cache re-ran Demucs on every single repair). Bass is
    always saved; drums only when its own sidecar misses, so an existing
    valid drums cache is never rewritten. `_Stem Analysis` must already
    exist - a wrong `project` argument must not sprout cache folders in
    arbitrary places.
    """
    try:
        from kick_model_adapter import _load_drums_cache, load_bass_stem
        cache_dir = project / "_Stem Analysis"
        dhit = _load_drums_cache(wav, cache_dir)
        bhit = load_bass_stem(wav, cache_dir)
        if dhit is not None and bhit is not None and dhit[1] == bhit[1]:
            print("  stems from sidecar caches (Demucs skipped)")
            return dhit[0], bhit[0], dhit[1]
    except Exception:
        pass
    drums, bass, sr = stem_audio(wav)
    # Independent try per sidecar: a bass failure must not block the drums
    # backfill (Codex round-2 - one shared try did exactly that).
    try:
        from kick_model_adapter import (_load_drums_cache, _save_bass_cache,
                                        _save_drums_cache)
        cache_dir = project / "_Stem Analysis"
        backfill_ok = cache_dir.is_dir()
    except Exception:
        backfill_ok = False
    if backfill_ok:
        try:
            _save_bass_cache(wav, cache_dir, bass, sr)
        except Exception as e:
            print(f"  warning: bass backfill failed: {type(e).__name__}: {e}")
        try:
            if _load_drums_cache(wav, cache_dir) is None:
                _save_drums_cache(wav, cache_dir, drums, sr)
        except Exception as e:
            print(f"  warning: drums backfill failed: {type(e).__name__}: {e}")
    return drums, bass, sr


def lattice_fit(onsets: np.ndarray, period0: float, first0: float
                ) -> tuple[float, float, np.ndarray]:
    """Least-squares (first, period) so onsets ≈ first + k*period."""
    period, first = period0, first0
    # Coarse phase-centering FIRST: the seed grid may sit a sixteenth off
    # the kicks (the La Trumpter tick trap) — without this, the tight
    # inlier mask locks onto the wrong basin and rejects the real kicks.
    k = np.round((onsets - first) / period)
    coarse = onsets - (first + k * period)
    first += float(np.median(coarse))
    keep = onsets
    for _ in range(4):
        k = np.round((keep - first) / period)
        res = keep - (first + k * period)
        m = np.abs(res) <= 0.040
        if m.sum() < 50:
            break
        kk, tt = k[m], keep[m]
        slope, intercept = np.polyfit(kk, tt, 1)
        period, first = float(slope), float(intercept)
        keep = keep[np.abs(keep - (first + np.round((keep - first) / period)
                                   * period)) <= 0.060]
    k = np.round((onsets - first) / period)
    res = (onsets - (first + k * period)) * 1000.0
    return first, period, res


def main() -> int:
    wav = Path(sys.argv[1])
    project = Path(sys.argv[2])
    dry = "--dry-run" in sys.argv

    overrides = load_grid_overrides(project)
    cur = overrides.get(wav.name, {}).get("replace_grid")
    if cur:
        period0, first0 = 60.0 / float(cur["bpm"]), float(cur["first_ms"]) / 1000.0
    else:
        period0, first0 = None, None

    drums, bass, sr = load_or_separate_stems(wav, project)
    dur = len(drums) / sr
    kicks = attack_onsets(drums, sr)
    print(f"  {len(kicks)} kick attacks")
    if period0 is None:
        ivs = np.diff(kicks)
        period0 = float(np.median(ivs[(ivs > 0.3) & (ivs < 0.7)]))
        first0 = float(kicks[0])

    first, period, res = lattice_fit(kicks, period0, first0)
    bpm = 60.0 / period
    inliers = np.abs(res) <= 40.0
    med = float(np.median(res[inliers]))
    iqr = (np.percentile(res[inliers], 75) - np.percentile(res[inliers], 25))
    # normalize first into [0, period)
    k0 = int(np.floor(first / period))
    first -= k0 * period
    n = max(2, int((dur - first) / period) + 1)
    print(f"  fit: {bpm:.4f} BPM first={first:.3f}s n={n} "
          f"residual med {med:+.1f}ms IQR {iqr:.1f}ms "
          f"inliers {inliers.sum()}/{len(kicks)}")
    ok = inliers.sum() >= 100 and iqr <= 30.0 and abs(med) <= 3.0
    if not ok:
        print("  REFUSING: fit too loose to trust")
        return 2

    # downbeat from bass: most attacks on beat 1
    bons = attack_onsets(bass, sr, lowpass_hz=200.0, min_gap_s=0.4)
    bk = np.round((bons - first) / period).astype(int)
    bres = (bons - (first + bk * period)) * 1000.0
    bk = bk[np.abs(bres) <= 90]
    votes = [int(np.sum((bk - d) % 4 == 0)) for d in range(4)]
    dboff = int(np.argmax(votes))
    print(f"  bass beat-1 votes by downbeat offset: {votes} -> dboff={dboff}")

    if dry:
        print("  dry run — nothing written")
        return 0
    overrides[wav.name] = {
        "replace_grid": {
            "bpm": round(bpm, 4),
            "first_ms": round(first * 1000.0, 1),
            "n_beats": int(n),
            "first_downbeat_offset": dboff,
        },
        "fit_residual_med_ms": round(med, 1),
        "fit_residual_iqr_ms": round(iqr, 1),
        "kick_inliers": int(inliers.sum()),
        "phase_source": "drum-stem-kicks",
        "reason": "grid fitted to Demucs drum-stem kick attacks (ticks/mix "
                  "onsets read the anticipating percussion on this track)",
    }
    p = overrides_path(project)
    p.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
    print(f"  wrote {p.name}: {wav.name} replace_grid @ {bpm:.4f} (stem kicks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
