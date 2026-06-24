"""Our own beat-grid detector — built from the Demucs drum stem.

Pipeline (the design Sam + Claude worked out):
  1. Demucs drum stem -> kick onsets (sub band) AND snare onsets (mid band).
  2. Period from the inter-kick interval HISTOGRAM (drops vote; fills/gaps are
     minorities; intervals taken as integer multiples of the base beat).
  3. Per-beat grid: keep the "spine" kicks (gaps = integer beats), chain them,
     and INTERPOLATE every beat between them -> sample-accurate on the kicks,
     bridged across breakdowns, follows real drift (never one static grid).
  4. Downbeat: drop-entry kicks (first kick after a >=3-beat gap) land on beat 1;
     vote their index mod 4. Snare (on 2 & 4) is the cross-check / fallback.
  5. Confidence: if the kicks don't vote cleanly, flag for the snare fallback.

    python stem_grid.py --build      # detect grids for STEMGRID_PROJ tracks
    python stem_grid.py --compare    # ours vs Rekordbox vs kick ground truth
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
import numpy as np
from scipy.signal import butter, sosfiltfilt

CLICK_FLOOR = float(os.environ.get("CLICK_FLOOR", "0.10"))   # click backtrack threshold (frac of peak)
CLICK_HP = float(os.environ.get("CLICK_HP", "1500"))         # transient band highpass (Hz)

ROOT = Path(__file__).parents[2]          # Source/automated_dj_mixes/stem_grid.py -> root
PROJ = Path(os.environ["STEMGRID_PROJ"]) if os.environ.get("STEMGRID_PROJ") else (ROOT / "Test Project" / "23.06.26")
AUDIO = PROJ / "Audio"
OUT = PROJ / "_Bakeoff"
OUT.mkdir(parents=True, exist_ok=True)
GRID_CACHE = OUT / "stem_grid_cache.json"


def band_onsets(y: np.ndarray, sr: int, lo: float, hi: float, min_gap_s: float) -> np.ndarray:
    """Sample-accurate attack onsets in a frequency band (lo=0 -> lowpass)."""
    if lo <= 0:
        sos = butter(4, hi, btype="low", fs=sr, output="sos")
    else:
        sos = butter(4, [lo, hi], btype="band", fs=sr, output="sos")
    env = np.abs(sosfiltfilt(sos, y))
    win = max(1, int(sr * 0.005))
    env = np.convolve(env, np.ones(win) / win, mode="same")
    thresh = 0.25 * np.percentile(env, 99)
    gap = int(min_gap_s * sr)
    onsets, i = [], win
    while i < len(env) - 1:
        if env[i] >= thresh and env[i] > env[i - 1]:
            seg = min(len(env), i + gap)
            peak_i = i + int(np.argmax(env[i:seg]))
            j = peak_i
            floor = 0.1 * env[peak_i]
            while j > max(0, peak_i - int(0.08 * sr)) and env[j] > floor:
                j -= 1
            onsets.append(j / sr)
            i = peak_i + gap
        else:
            i += 1
    return np.asarray(onsets)


def refine_to_click(drums: np.ndarray, sr: int, kicks: np.ndarray) -> np.ndarray:
    """Sam's fix: the sub band locates the kick, but its body is mushy + LATE.
    Snap each sub-detected kick onset to the sharp high-frequency CLICK (beater
    transient, >1.5kHz) right next to it — that's the true strike instant, what
    Ableton/RB lock to. Sub = identity, click = precise timing."""
    hp = sosfiltfilt(butter(4, CLICK_HP, btype="high", fs=sr, output="sos"), drums)
    env = np.abs(hp)
    w = max(1, int(sr * 0.0006))                      # ~0.6ms = sharp edge, minimal delay
    env = np.convolve(env, np.ones(w) / w, mode="same")
    win = int(0.035 * sr)
    out = []
    for k in kicks:
        i = int(k * sr)
        lo, hi = max(0, i - win), min(len(env), i + win)
        if hi - lo < 4:
            out.append(k); continue
        peak = lo + int(np.argmax(env[lo:hi]))        # the click peak
        floor = CLICK_FLOOR * env[peak]                # backtrack to the FOOT of the rise (true strike)
        j = peak
        while j > lo and env[j] > floor:
            j -= 1
        out.append(j / sr)
    return np.asarray(sorted(out))


def estimate_period(kicks: np.ndarray) -> tuple[float, float]:
    """Beat period from the inter-kick interval histogram. Returns (period, conf)."""
    iv = np.diff(kicks)
    iv = iv[(iv >= 0.28) & (iv <= 0.70)]            # plausible single-beat range (86-214 BPM)
    if len(iv) < 8:
        return 0.0, 0.0
    bins = np.arange(0.28, 0.70, 0.002)
    h, edges = np.histogram(iv, bins=bins)
    peak = edges[np.argmax(h)] + 0.001
    near = iv[np.abs(iv - peak) <= 0.08 * peak]      # refine within +/-8%
    period = float(np.median(near))
    conf = len(near) / len(np.diff(kicks))           # share of all gaps that are clean single beats
    return period, float(conf)


def build_grid(kicks: np.ndarray, period: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Index every kick to a global beat (least-squares refined so a tiny period
    error can't compound), keep the on-beat 'anchor' kicks (reject fills/off-beat),
    and INTERPOLATE every beat between anchors -> sample-accurate on kicks, bridged
    across gaps, follows real drift. Returns (grid_times, anchor_idx, anchor_times)."""
    idx = np.round((kicks - kicks[0]) / period).astype(float)
    a, b = kicks[0], period
    for _ in range(3):                                   # refine index <-> (phase, period)
        A = np.vstack([np.ones(len(idx)), idx]).T
        (a, b), *_ = np.linalg.lstsq(A, kicks, rcond=None)
        idx = np.round((kicks - a) / b)
    resid = np.abs(kicks - (a + b * idx))
    keep = resid < 0.15 * b                              # on-beat kicks only (fills rejected)
    best: dict[int, tuple[float, float]] = {}            # beat index -> (best kick time, its resid)
    for ii, t, r, k in zip(idx, kicks, resid, keep):
        if not k:
            continue
        ii = int(ii)
        if ii not in best or r < best[ii][1]:
            best[ii] = (float(t), float(r))
    aidx = np.array(sorted(best))
    atim = np.array([best[i][0] for i in aidx])
    all_idx = np.arange(aidx[0], aidx[-1] + 1)
    # SMOOTH the grid through the kicks — raw kick onsets carry +-20-40ms attack
    # jitter; using them verbatim as warp markers makes Ableton wobble each beat.
    # A smoothing spline keeps real tempo drift but irons out per-kick jitter
    # (kicks anchor tempo+phase; the grid itself must be smooth, like RB's).
    if len(aidx) >= 8:
        from scipy.interpolate import UnivariateSpline
        spl = UnivariateSpline(aidx, atim, k=3, s=len(aidx) * 0.0006)
        grid = spl(all_idx)
        grid = np.maximum.accumulate(grid)          # guarantee strictly monotonic
    else:
        grid = np.interp(all_idx, aidx, atim)
    return grid, aidx.astype(float), atim


def find_downbeat(grid: np.ndarray, aidx: np.ndarray, snares: np.ndarray,
                  period: float) -> tuple[int, float, str]:
    """Fuse the two cues: the SNARE backbeat (on 2 & 4) narrows the downbeat to the
    two non-backbeat phases; the DROP-ENTRY kicks (first anchor after a >=3-beat gap,
    which land on beat 1) pick between them. Returns (phase 0-3, agreement, method)."""
    gstart = int(aidx[0])
    # snare beat-phase histogram (only snares that sit on a gridline)
    back = None
    if len(snares) > 8:
        pos = np.clip(np.searchsorted(grid, snares), 1, len(grid) - 1)
        nearest_j = np.where(np.abs(snares - grid[pos - 1]) <= np.abs(snares - grid[pos]), pos - 1, pos)
        dist = np.minimum(np.abs(snares - grid[pos - 1]), np.abs(snares - grid[pos]))
        on = dist < 0.20 * period
        sp = ((gstart + nearest_j[on]) % 4).astype(int)
        if len(sp) > 8:
            c = np.bincount(sp, minlength=4)
            back = set(np.argsort(c)[-2:])              # two phases the snare lands on = 2 & 4
    # drop-entry kicks land on beat 1
    entries = [int(aidx[a] % 4) for a in range(1, len(aidx)) if aidx[a] - aidx[a - 1] >= 3]
    cands = [p for p in range(4) if back is None or p not in back]  # non-backbeat phases
    if entries:
        votes = [e for e in entries if e in cands] or entries
        vals, counts = np.unique(votes, return_counts=True)
        d = int(vals[np.argmax(counts)]); agree = float(counts.max() / len(votes))
        method = "snare+entry" if back is not None else "entry"
    elif back is not None and cands:
        d = cands[0]; agree = 0.5; method = "snare-only"
    else:
        d = int(aidx[0] % 4); agree = 0.0; method = "weak"
    return d, agree, method


def main_build() -> None:
    """Detect grids. Reuses cached kick/snare onsets (the slow Demucs pass) so the
    grid algorithm can be re-iterated instantly; only re-separates if onsets absent."""
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


def main_compare() -> None:
    from automated_dj_mixes.rekordbox_reader import read_rekordbox_library, find_rekordbox_match
    cache = json.loads(GRID_CACHE.read_text())
    rb_lib = read_rekordbox_library()
    print(f"{'track':34s} {'BPM':>6} {'conf':>5} {'spine':>9} {'DB':>13} {'ours_vs_RB':>10} {'flag':>5}")
    vs_rb_all, flags = [], []
    for name in sorted(cache):
        c = cache[name]
        grid = np.asarray(c["grid"])
        ra = find_rekordbox_match(name, rb_lib)
        if ra and ra.beat_times_ms:
            vs_rb = score(grid, np.asarray(ra.beat_times_ms) / 1000)
        else:
            vs_rb = float("nan")
        vs_rb_all.append(vs_rb)
        spine = f"{c['n_spine']}/{c['n_kicks']}"
        flag = ""
        if c["conf"] < 0.80: flag = "LOWC"
        elif c.get("downbeat_agree", 1) < 0.6: flag = "DB?"
        elif not np.isnan(vs_rb) and vs_rb > 25: flag = "FAR"
        if flag: flags.append((name, flag, vs_rb))
        print(f"{name[:34]:34s} {c['bpm']:6.1f} {c['conf']:5.2f} {spine:>9} "
              f"{c.get('downbeat_method','')[:9]:>9}{c.get('downbeat_agree',0):4.1f} {vs_rb:10.1f} {flag:>5}")
    arr = np.array([v for v in vs_rb_all if not np.isnan(v)])
    print(f"\nMEDIAN our-grid vs RB-grid: {np.median(arr):.1f} ms   (how closely we track RB across the set)")
    print(f"within 10ms of RB: {(arr<=10).sum()}/{len(arr)}   within 20ms: {(arr<=20).sum()}/{len(arr)}")
    if flags:
        print("FLAGGED:")
        for n, f, v in flags:
            print(f"  [{f}] {n[:50]}  (vs RB {v:.0f}ms)")


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
