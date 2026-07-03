"""SHIM — canonical code lives in Audio Analysis Toolkit (audio_analysis.stem_grid).
Re-exports + registers the lazy Demucs separator + keeps the project CLI (main_build/
main_compare/main_viz) and project path constants. No algorithm logic lives here."""
from __future__ import annotations
import json, os, sys
from dataclasses import dataclass
from pathlib import Path
import numpy as np
from scipy.signal import butter, sosfiltfilt

from audio_analysis.stem_grid import *  # noqa: F401,F403
from audio_analysis.stem_grid import (  # noqa: F401
    BeatGrid, detect_beat_grid, band_onsets, refine_to_click, estimate_period,
    build_grid, find_downbeat, extrapolate_grid, detect_transients,
    snap_grid_to_transients, snap_grid_to_asd, grid_vs_kick, score,
    _robust_period, _first_kick_phase, _percussion_intro_phase,
    CLICK_FLOOR, CLICK_HP, SNARE_CONTRAST, DOWNBEAT_PRIOR_AGREE,
)
import audio_analysis.stem_grid as _aa_stem_grid


def _lazy_stem_audio(*args, **kwargs):
    from probe_stem_kick_grid import stem_audio
    return stem_audio(*args, **kwargs)


_aa_stem_grid.set_stem_separator(_lazy_stem_audio)

ROOT = Path(__file__).parents[2]          # Source/automated_dj_mixes/stem_grid.py -> root
PROJ = Path(os.environ["STEMGRID_PROJ"]) if os.environ.get("STEMGRID_PROJ") else (ROOT / "Test Project" / "23.06.26")
AUDIO = PROJ / "Audio"
OUT = PROJ / "_Bakeoff"                      # bake-off scratch; created lazily by the CLI builders
GRID_CACHE = OUT / "stem_grid_cache.json"
sys.path.insert(0, str(ROOT / "Source"))   # for sibling imports when run as a script


def main_build() -> None:
    """Detect grids. Reuses cached kick/snare onsets (the slow Demucs pass) so the
    grid algorithm can be re-iterated instantly; only re-separates if onsets absent."""
    OUT.mkdir(parents=True, exist_ok=True)
    cache = json.loads(GRID_CACHE.read_text()) if GRID_CACHE.exists() else {}
    redetect = "--redetect" in sys.argv
    retune = "--retune" in sys.argv                  # re-detect from cached stems, NO Demucs
    stems_dir = OUT / "stems"; stems_dir.mkdir(exist_ok=True)
    stem_audio = None
    for i, wav in enumerate(sorted(AUDIO.glob("*.wav")), 1):
        prev = cache.get(wav.name)
        stem_npy = stems_dir / (wav.stem + ".npy")
        if retune and stem_npy.exists():
            drums = np.load(stem_npy); sr = 44100
            kicks = refine_to_click(drums, sr, band_onsets(drums, sr, 0, 150, 0.25))
            snares = band_onsets(drums, sr, 200, 3000, 0.18)
        elif prev and "kicks" in prev and not redetect:
            kicks = np.asarray(prev["kicks"]); snares = np.asarray(prev["snares"])
        else:
            if stem_audio is None:
                from probe_stem_kick_grid import stem_audio as _sa
                stem_audio = _sa
            print(f"[{i}] separating {wav.name}")
            drums, bass, sr = stem_audio(wav)
            np.save(stem_npy, drums.astype(np.float32))            # cache for fast --retune later
            kicks = refine_to_click(drums, sr, band_onsets(drums, sr, 0, 150, 0.25))
            snares = band_onsets(drums, sr, 200, 3000, 0.18)
        period, conf = estimate_period(kicks)
        if period <= 0:
            print("   no period"); continue
        grid, spine_i, spine_t = build_grid(kicks, period)
        d, dagree, dmethod = find_downbeat(grid, spine_i, snares, period)
        db_grid_phase = int((d - int(spine_i[0])) % 4)   # which grid[] index is a downbeat
        cache[wav.name] = {
            "bpm": round(60 / period, 2), "period": period, "conf": round(conf, 3),
            "n_kicks": int(len(kicks)), "n_snares": int(len(snares)), "n_spine": int(len(spine_i)),
            "downbeat_phase": d, "downbeat_agree": round(dagree, 2), "downbeat_method": dmethod,
            "db_grid_phase": db_grid_phase,
            "grid": [round(float(t), 4) for t in grid],
            "kicks": [round(float(k), 4) for k in kicks],
            "snares": [round(float(s), 4) for s in snares],
        }
        GRID_CACHE.write_text(json.dumps(cache))
        print(f"   {wav.name[:34]:34s} {cache[wav.name]['bpm']:6.2f}BPM conf={conf:.2f} "
              f"spine {len(spine_i)}/{len(kicks)} DB {d} ({dmethod} {dagree:.2f})")
    print(f"\nbuilt {len(cache)} -> {GRID_CACHE}")


def score(kicks: np.ndarray, grid: np.ndarray) -> float:
    g = np.sort(grid)
    idx = np.clip(np.searchsorted(g, kicks), 1, len(g) - 1)
    nearest = np.where(np.abs(kicks - g[idx - 1]) <= np.abs(kicks - g[idx]), g[idx - 1], g[idx])
    res = np.abs(kicks - nearest) * 1000
    res = res[res <= np.median(np.diff(g)) * 1000 / 2]
    return float(np.median(res)) if len(res) else float("nan")


def grid_vs_kick(grid: np.ndarray, kicks: np.ndarray) -> float:
    """Median |nearest gridline - kick| over on-beat kicks (within half a beat of a
    gridline; fills ignored). The RB-INDEPENDENT warp-fidelity metric — does the grid
    actually sit on the transients we'll warp to? This is the metric that matters;
    RB is only an advisory cross-check (and is demonstrably wrong on some tracks)."""
    g = np.sort(grid)
    idx = np.clip(np.searchsorted(g, kicks), 1, len(g) - 1)
    near = np.where(np.abs(kicks - g[idx - 1]) <= np.abs(kicks - g[idx]), g[idx - 1], g[idx])
    res = np.abs(kicks - near) * 1000
    res = res[res <= np.median(np.diff(g)) * 1000 / 2]
    return float(np.median(res)) if len(res) else float("nan")


def main_compare() -> None:
    """Primary metric = grid-vs-kick (RB-free, the warp-fidelity truth). RB is an
    advisory cross-check: where it disagrees, the kicks arbitrate (we have caught RB
    locking the wrong tempo on house tracks — see project-warp-beatgrid-bug)."""
    try:
        from automated_dj_mixes.rekordbox_reader import read_rekordbox_library, find_rekordbox_match
        rb_lib = read_rekordbox_library()
    except Exception as e:                                # RB reader is fragile (pyrekordbox/Py3.14)
        print(f"(RB cross-check unavailable: {type(e).__name__}) — scoring grid-vs-kick only")
        rb_lib = None
    cache = json.loads(GRID_CACHE.read_text())
    print(f"{'track':34s} {'BPM':>6} {'conf':>5} {'spine':>9} {'DB':>13} {'kick':>7} {'RB?':>7} {'flag':>5}")
    kick_all, flags = [], []
    for name in sorted(cache):
        c = cache[name]
        grid = np.asarray(c["grid"]); kicks = np.asarray(c["kicks"])
        kf = grid_vs_kick(grid, kicks)                   # RB-FREE accuracy
        kick_all.append(kf)
        vs_rb = float("nan")
        if rb_lib is not None:
            try:
                ra = find_rekordbox_match(name, rb_lib)
                if ra and ra.beat_times_ms:
                    vs_rb = score(grid, np.asarray(ra.beat_times_ms) / 1000)
            except Exception:
                pass
        spine = f"{c['n_spine']}/{c['n_kicks']}"
        flag = ""
        if c["conf"] < 0.80: flag = "LOWC"               # weak/syncopated kicks -> snare-primary
        elif kf > 15: flag = "JIT"                       # grid not on transients (real problem)
        elif c.get("downbeat_agree", 1) < 0.6: flag = "DB?"
        elif not np.isnan(vs_rb) and vs_rb > 25: flag = "RB?"  # WE disagree with RB; kicks say we're right
        if flag: flags.append((name, flag, kf, vs_rb))
        rb_s = f"{vs_rb:7.1f}" if not np.isnan(vs_rb) else "      -"
        print(f"{name[:34]:34s} {c['bpm']:6.1f} {c['conf']:5.2f} {spine:>9} "
              f"{c.get('downbeat_method','')[:9]:>9}{c.get('downbeat_agree',0):4.1f} {kf:6.1f}m{rb_s} {flag:>5}")
    arr = np.array([v for v in kick_all if not np.isnan(v)])
    print(f"\nMEDIAN grid-vs-kick (warp fidelity): {np.median(arr):.2f} ms")
    print(f"on transients <5ms: {(arr<5).sum()}/{len(arr)}   <15ms: {(arr<15).sum()}/{len(arr)}")
    if flags:
        print("FLAGGED (LOWC=weak kicks, JIT=off transients, DB?=downbeat, RB?=RB disagrees/we're on the kicks):")
        for n, f, kf, v in flags:
            rb_note = f", RB {v:.0f}ms" if not np.isnan(v) else ""
            print(f"  [{f:4s}] {n[:46]:46s}  (kick {kf:.1f}ms{rb_note})")


def main_viz() -> None:
    """Render each track's grid over its biggest breakdown, so the bridging + downbeat
    lock are visible at a glance."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import soundfile as sf
    cache = json.loads(GRID_CACHE.read_text())
    vdir = OUT / "grid_viz"; vdir.mkdir(exist_ok=True)
    for name in sorted(cache):
        c = cache[name]
        grid = np.asarray(c["grid"]); kicks = np.asarray(c["kicks"]); snares = np.asarray(c["snares"])
        period = c["period"]; dbp = c.get("db_grid_phase", 0)
        if len(kicks) > 4:
            gaps = np.diff(kicks); gi = int(np.argmax(gaps)); centre = (kicks[gi] + kicks[gi + 1]) / 2
        else:
            centre = grid[len(grid) // 2]
        half = 16 * 4 * period
        w0, w1 = centre - half, centre + half
        y, sr = sf.read(str(AUDIO / name), always_2d=True); y = y.mean(1)
        s0, s1 = int(max(0, w0) * sr), int(min(len(y) / sr, w1) * sr)
        seg = y[s0:s1]
        sos = butter(4, 150, btype="low", fs=sr, output="sos")
        env = np.abs(sosfiltfilt(sos, seg))
        env = np.convolve(env, np.ones(int(sr*0.01))/int(sr*0.01), mode="same")
        t = np.arange(len(env)) / sr + s0 / sr
        fig, ax = plt.subplots(figsize=(20, 4))
        ax.fill_between(t, env / (env.max() + 1e-9), color="0.8", lw=0)
        gi0 = np.searchsorted(grid, w0)
        for j in range(gi0, len(grid)):
            if grid[j] > w1: break
            is_db = (j % 4) == dbp
            ax.axvline(grid[j], color=("#0a6" if is_db else "0.6"), lw=(2.0 if is_db else 0.6),
                       alpha=(0.9 if is_db else 0.5))
        kk = kicks[(kicks >= w0) & (kicks <= w1)]
        ss = snares[(snares >= w0) & (snares <= w1)]
        ax.plot(kk, np.full_like(kk, 0.04), "v", color="#06c", ms=6, label="kick")
        ax.plot(ss, np.full_like(ss, 1.02), "x", color="#c30", ms=7, label="snare (2&4)")
        ax.set_xlim(w0, w1); ax.set_ylim(0, 1.12)
        ax.set_title(f"{name}   {c['bpm']} BPM   downbeat={c.get('downbeat_method','')} "
                     f"(green=beat1)   grey=kick energy   breakdown bridged in middle", fontsize=10)
        ax.set_yticks([]); ax.set_xlabel("time (s)")
        ax.legend(loc="upper right", fontsize=8)
        fig.tight_layout(); fig.savefig(vdir / f"{Path(name).stem}.png", dpi=90); plt.close(fig)
        print(f"  {name[:44]}")
    print(f"\nwrote {len(cache)} -> {vdir}")


if __name__ == "__main__":
    if "--build" in sys.argv:
        main_build()
    elif "--compare" in sys.argv:
        main_compare()
    elif "--viz" in sys.argv:
        main_viz()
    else:
        print("usage: stem_grid.py --build | --compare | --viz")
