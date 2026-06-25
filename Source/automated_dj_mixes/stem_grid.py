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
from dataclasses import dataclass
from pathlib import Path
import numpy as np
from scipy.signal import butter, sosfiltfilt

CLICK_FLOOR = float(os.environ.get("CLICK_FLOOR", "0.10"))   # click backtrack threshold (frac of peak)
CLICK_HP = float(os.environ.get("CLICK_HP", "1500"))         # transient band highpass (Hz)

ROOT = Path(__file__).parents[2]          # Source/automated_dj_mixes/stem_grid.py -> root
PROJ = Path(os.environ["STEMGRID_PROJ"]) if os.environ.get("STEMGRID_PROJ") else (ROOT / "Test Project" / "23.06.26")
AUDIO = PROJ / "Audio"
OUT = PROJ / "_Bakeoff"                      # bake-off scratch; created lazily by the CLI builders
GRID_CACHE = OUT / "stem_grid_cache.json"
sys.path.insert(0, str(ROOT / "Source"))   # for sibling imports when run as a script


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


def _robust_period(kicks: np.ndarray, period: float) -> float:
    """Period from the MEDIAN of per-segment least-squares slopes. Split the kicks
    at >6-beat gaps (breakdowns); each contiguous segment has no big internal gap so
    its lstsq slope is uncorrupted. Immune to the histogram-mode seed error that
    compounds across a long breakdown and wrecks the whole index assignment
    (Adam Ten: 137-beat gap + a 0.0006s seed error -> 44% of kicks wrongly rejected)."""
    iki = np.diff(kicks)
    breaks = np.where(iki > 6 * period)[0]
    slopes = []
    for s in np.split(np.arange(len(kicks)), breaks + 1):
        if len(s) < 12:
            continue
        kk = kicks[s]
        idx = np.round((kk - kk[0]) / period)
        a, b = kk[0], period
        for _ in range(3):
            A = np.vstack([np.ones(len(idx)), idx]).T
            (a, b), *_ = np.linalg.lstsq(A, kk, rcond=None)
            idx = np.round((kk - a) / b)
        slopes.append(b)
    return float(np.median(slopes)) if slopes else period


def build_grid(kicks: np.ndarray, period: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Index every kick to a global beat (least-squares refined so a tiny period
    error can't compound), keep the on-beat 'anchor' kicks (reject fills/off-beat),
    and INTERPOLATE every beat between anchors -> sample-accurate on kicks, bridged
    across gaps, follows real drift. Returns (grid_times, anchor_idx, anchor_times)."""
    b0 = _robust_period(kicks, period)                   # seed with the breakdown-immune period
    idx = np.round((kicks - kicks[0]) / b0).astype(float)
    a, b = kicks[0], b0
    for _ in range(3):                                   # refine index <-> (phase, period)
        A = np.vstack([np.ones(len(idx)), idx]).T
        (a, b), *_ = np.linalg.lstsq(A, kicks, rcond=None)
        b = float(np.clip(b, b0 * 0.997, b0 * 1.003))    # stay within house drift; block corruption runaway
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


SNARE_CONTRAST = 1.25      # min parity-pair energy ratio to trust the snare backbeat
DOWNBEAT_PRIOR_AGREE = 0.6   # below this drum-cue agreement (the DB? threshold), fall back to the house "first kick = downbeat" prior


def find_downbeat(grid: np.ndarray, aidx: np.ndarray, snares: np.ndarray,
                  period: float) -> tuple[int, float, str]:
    """Fuse the two cues: the SNARE backbeat (on 2 & 4) narrows the downbeat to the
    two non-backbeat phases; the DROP-ENTRY kicks (first anchor after a >=3-beat gap,
    which land on beat 1) pick between them. Returns (phase 0-3, agreement, method).

    The backbeat is always a PARITY PAIR — beats 2 & 4 are two beats apart, so they
    share parity: {1,3} (=> downbeat even) or {0,2} (=> downbeat odd). We only trust
    the snare veto when one parity CLEARLY dominates (ratio >= SNARE_CONTRAST). A flat
    snare histogram (hats/perc firing on every beat) must NOT manufacture a veto — that
    was the bug that silently inverted a confident downbeat by one beat (the snares-on-
    all-four-beats clash Sam heard in V3). When the snare and entry cues CONFLICT, the
    agreement is forced low so DB? fires and the pipeline can fall back."""
    gstart = int(aidx[0])
    back = None
    if len(snares) > 8:
        pos = np.clip(np.searchsorted(grid, snares), 1, len(grid) - 1)
        nearest_j = np.where(np.abs(snares - grid[pos - 1]) <= np.abs(snares - grid[pos]), pos - 1, pos)
        dist = np.minimum(np.abs(snares - grid[pos - 1]), np.abs(snares - grid[pos]))
        on = dist < 0.20 * period
        sp = ((gstart + nearest_j[on]) % 4).astype(int)
        if len(sp) > 8:
            c = np.bincount(sp, minlength=4).astype(float)
            even, odd = c[0] + c[2], c[1] + c[3]        # backbeat is a PARITY pair
            if max(even, odd) >= SNARE_CONTRAST * (min(even, odd) + 1e-9):
                back = {1, 3} if odd > even else {0, 2}  # only veto when genuinely bimodal
    # drop-entry kicks land on beat 1
    entries = [int(aidx[a] % 4) for a in range(1, len(aidx)) if aidx[a] - aidx[a - 1] >= 3]
    if entries:
        vals, counts = np.unique(entries, return_counts=True)
        raw_winner = int(vals[np.argmax(counts)])
        raw_agree = float(counts.max() / len(entries))   # agreement on the FULL vote, pre-veto
        if back is not None:
            cands = [p for p in range(4) if p not in back]
            in_cands = [e for e in entries if e in cands]
            if in_cands:
                v2, c2 = np.unique(in_cands, return_counts=True)
                d = int(v2[np.argmax(c2)])
            else:
                d = cands[0]
            if d != raw_winner:                          # snare veto overruled the entry plurality
                agree = min(raw_agree, 0.5); method = "snare!=entry"   # cues CONFLICT -> DB?
            else:
                agree = raw_agree; method = "snare+entry"
        else:
            d = raw_winner; agree = raw_agree; method = "entry"
    elif back is not None:
        d = [p for p in range(4) if p not in back][0]; agree = 0.5; method = "snare-only"
    else:
        d = int(aidx[0] % 4); agree = 0.0; method = "weak"
    return d, agree, method


def _first_kick_phase(kicks: np.ndarray, full_grid: np.ndarray):
    """Full-grid index (mod 4) of the beat nearest the FIRST detected kick onset — the
    downbeat in house (tracks start on the 1). Resolves a DB? track where find_downbeat
    landed the offset on a beat with NO kick (Discosteps' sparse intro kicks the 1 & 3 on
    even beats; the grid's first ANCHOR sits on an odd beat). Uses the SUB-BAND kick onsets
    (not broadband drum energy), so a filter-sweep intro with no sub-bass can't masquerade
    as a kick — Delacour stays on its real first kick (1.01s), not the sweep. None if no kicks."""
    if kicks is None or not len(kicks):
        return None
    g = np.asarray(full_grid, float)
    return int(np.argmin(np.abs(g - float(kicks[0])))) % 4


def _percussion_intro_phase(drums: np.ndarray, sr: int, full_grid: np.ndarray, period: float):
    """Earliest grid beat (mod 4) that begins a REGULAR on-grid drum pattern — the downbeat.
    Catches a percussion intro that starts BEFORE the sub-bass kick (Discosteps: claps on the
    1 & 3 for a bar before the kick drops), which the kick-based cues miss because they anchor
    to where the kick enters. A filter-sweep intro (Delacour) produces no regular discrete
    on-grid onsets, so the earliest regular pattern is the kick itself -> downbeat unchanged.
    Broadband onsets (kick harmonics + claps/perc); only the first ~40 beats matter for bar 1."""
    g = np.asarray(full_grid, float)
    nbeats = min(len(g), 41)
    if nbeats < 8:
        return None
    ons = band_onsets(drums, sr, 150, 12000, 0.10)
    ons = ons[ons <= g[nbeats - 1]]
    if len(ons) < 6:
        return None
    hit = np.zeros(nbeats, bool)
    for o in ons:
        idx = int(np.argmin(np.abs(g[:nbeats] - o)))
        if abs(g[idx] - o) < 0.18 * period:
            hit[idx] = True
    candidates = []
    for step in (1, 2):                    # 4-on-floor (every beat) or a 1&3 / 2&4 intro
        for start in np.where(hit)[0]:
            if all(start + i * step < nbeats and hit[start + i * step] for i in range(4)):
                candidates.append(int(start))
                break
    # Only override when the regular groove begins at the VERY FIRST grid beat — i.e. the
    # track starts on its downbeat (Discosteps' claps from the top). A pattern that starts a
    # beat or two in is intro decoration over a sweep (Delacour, pattern at beat 1) whose real
    # downbeat is the kick -> return None and fall back to the kick-anchored prior.
    return 0 if (candidates and min(candidates) == 0) else None


def extrapolate_grid(grid: np.ndarray, duration_sec: float) -> tuple[np.ndarray, int]:
    """Extend the kick-spanning grid to cover the whole file [0, duration].

    The detected grid runs first-kick -> last-kick; Ableton wants warp markers from
    the clip start (intros are often played). We extrapolate at the EDGE tempo — a
    constant interval taken from the first/last few beats — purely across the
    transient-free intro/outro. This does NOT violate Sam's no-static-grid rule:
    the BODY stays the per-beat detected grid (which follows real drift); only the
    kick-less head/tail (where there's nothing to drift against) is filled, exactly
    as Rekordbox does. Returns (full_grid, n_added_before) so the downbeat offset
    can be shifted by the beats prepended at the front."""
    g = list(map(float, grid))
    if len(g) < 2:                                   # degenerate: can't infer an interval
        raise ValueError("grid too short to extrapolate (<2 beats)")
    # the spline can undershoot a few ms below 0 at the first anchor -> a negative
    # first beat = an invalid SecTime (before the clip head). Floor the negative head
    # to an even ramp up from 0 so warp markers stay >= 0 and strictly increasing.
    ga = np.asarray(g, float)
    if ga[0] < 0.0:
        first_pos = int(np.argmax(ga > 0.0))
        if first_pos > 0:
            ga[:first_pos] = np.linspace(0.0, ga[first_pos], first_pos, endpoint=False)
        g = list(ga)
    head_iv = float(np.median(np.diff(g[:9]))) if len(g) >= 3 else (g[1] - g[0])
    tail_iv = float(np.median(np.diff(g[-9:]))) if len(g) >= 3 else (g[-1] - g[-2])
    added_before = 0
    while g[0] - head_iv >= 0.0:                      # clamp at >= 0 — no negative SecTime in the ALS
        g.insert(0, g[0] - head_iv)
        added_before += 1
    while g[-1] + tail_iv < duration_sec:
        g.append(g[-1] + tail_iv)
    return np.asarray(g), added_before


@dataclass
class BeatGrid:
    """Drop-in replacement for the Rekordbox grid at orchestrator.py:504. Carries
    exactly the contract the warp/section code consumes — beat_times_ms (full-file,
    int ms) + first_downbeat_offset (index of the first true downbeat) — plus the
    self-assessment the pipeline cross-check needs."""
    beat_times_ms: list[int]
    first_downbeat_offset: int
    bpm: float
    confidence: float
    grid_vs_kick_ms: float
    downbeat_method: str
    downbeat_agree: float
    flag: str                       # "" | LOWC | JIT | DB? — empty = trust outright
    snapped_to_asd: bool = False    # per-beat timing refined to Ableton's transients
    timing_src: str = "detector"    # "asd" | "own-transients" | "detector"


def detect_transients(drums: np.ndarray, sr: int, mix: np.ndarray | None = None,
                      hop: int = 64) -> np.ndarray:
    """Our OWN broadband transient detector — spectral flux (the method Ableton uses),
    peak-picked and sample-backtracked to the onset foot. Run on the drum stem (clean
    kicks) UNION the full mix (Ableton analyses the mix, so matching its input rescues
    soft kicks the stem separation softens — Eli). Median ~1ms vs Ableton's .asd ticks
    across the corpus, so the .asd becomes an optional precision cross-check, not a
    requirement (works headless, on any machine, productisable)."""
    import librosa
    def flux(y):
        oe = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
        return librosa.onset.onset_detect(onset_envelope=oe, sr=sr, hop_length=hop,
                                          backtrack=True, units="time")
    tr = flux(drums)
    if mix is not None and len(mix) > sr:
        tr = np.concatenate([tr, flux(mix)])
    return np.sort(tr)


def snap_grid_to_transients(grid: np.ndarray, ticks: np.ndarray, win: float = 0.015) -> np.ndarray:
    """Refine per-beat TIMING by snapping each beat to the nearest transient within
    +-win, then RE-SMOOTH. The transient set is Ableton's sample-accurate .asd ticks
    when present, else our own detect_transients() output. Snapping removes systematic
    detection biases (the soft-kick lag — Eli sat 7.6ms late) while we keep the
    structure (period/downbeat/breakdown-bridge). The snap alone reintroduces per-beat
    jitter (~5ms); the spline irons that back out to a smooth, warp-clean grid (Sam's
    rule: smooth beats jittery for warping)."""
    grid = np.asarray(grid, float)
    if ticks is None or len(ticks) < 8 or len(grid) < 8:
        return grid
    ticks = np.sort(np.asarray(ticks, float))
    snap = grid.copy()
    for i, b in enumerate(grid):
        nt = ticks[np.argmin(np.abs(ticks - b))]
        if abs(b - nt) < win:
            snap[i] = nt
    from scipy.interpolate import UnivariateSpline
    idx = np.arange(len(snap))
    out = UnivariateSpline(idx, snap, k=3, s=len(snap) * 0.0008)(idx)
    return np.maximum.accumulate(out)


# Back-compat alias (the .asd-specific name); same generic snapper.
snap_grid_to_asd = snap_grid_to_transients


def detect_beat_grid(wav: str | Path, drums: np.ndarray | None = None,
                     sr: int | None = None, asd_ticks: np.ndarray | None = None,
                     mix: np.ndarray | None = None, refine_timing: bool = True) -> BeatGrid:
    """Pipeline entry point: detect the beat grid for one track and return the
    Rekordbox-compatible contract. Pass a pre-separated drum stem (drums, sr) to
    skip Demucs (e.g. reuse the Phase-1a separation); otherwise it separates.

    Per-beat TIMING is refined to broadband transients (Ableton's method):
      - asd_ticks (Ableton's .asd transients) when present -> sample-accurate, 0ms;
      - else our OWN detect_transients() (spectral flux on stem + mix) -> ~1ms vs
        Ableton, NO Ableton dependency (the standalone, productisable path).
    Pass mix (full-mix mono) to avoid re-reading the WAV; else it's read from `wav`."""
    if drums is None:
        from probe_stem_kick_grid import stem_audio
        drums, _bass, sr = stem_audio(Path(wav))
    sr = int(sr or 44100)
    if drums is None or len(drums) < sr:             # < 1s of audio: degenerate input
        raise ValueError(f"drum stem too short for {Path(wav).name} "
                         f"({0 if drums is None else len(drums)} samples) — fall back to the .asd ruler")
    duration_sec = len(drums) / sr
    kicks = refine_to_click(drums, sr, band_onsets(drums, sr, 0, 150, 0.25))
    snares = band_onsets(drums, sr, 200, 3000, 0.18)
    period, conf = estimate_period(kicks)
    if period <= 0 or len(kicks) < 8:
        raise ValueError(f"no usable kick period for {Path(wav).name} "
                         f"({len(kicks)} kicks) — fall back to the .asd ruler")
    grid, aidx, atim = build_grid(kicks, period)
    snapped = False
    timing_src = "detector"
    if asd_ticks is not None and len(np.asarray(asd_ticks)) >= 8:
        grid = snap_grid_to_transients(grid, np.asarray(asd_ticks))   # Ableton .asd: 0ms
        snapped = True; timing_src = "asd"
    elif refine_timing:
        if mix is None:                               # our own transients need the full mix too
            try:
                import soundfile as _sf
                m, _msr = _sf.read(str(wav))
                mix = m.mean(1) if getattr(m, "ndim", 1) > 1 else m
            except Exception:
                mix = None
        tr = detect_transients(drums, sr, mix)
        if len(tr) >= 8:
            grid = snap_grid_to_transients(grid, tr)  # our broadband flux: ~1ms vs Ableton
            timing_src = "own-transients"
    # Measure grid-vs-kick on the FINAL (post-snap) grid — that is the real warp quality.
    # A ±15ms snap CANNOT rescue a structurally-wrong grid (Afro/Latin congas in the kick
    # band, jackin'/syncopated kicks -> 88ms off), so refinement must NOT suppress the JIT
    # flag (the V3 hole that shipped 88ms grids as DB?/LOWC and silently baked a bad warp).
    kf = grid_vs_kick(grid, kicks)
    d, dagree, dmethod = find_downbeat(grid, aidx, snares, period)
    db_grid_phase = int((d - int(aidx[0])) % 4)
    full_grid, added_before = extrapolate_grid(grid, duration_sec)
    # Anchor bar 1 to the FIRST downbeat IN THE FILE, not the first downbeat after
    # the first DETECTED kick. extrapolate_grid extends the grid back over the intro
    # to time ~0 (added_before beats prepended); (db_grid_phase + added_before) is the
    # first downbeat at/after the first kick, so stem_detector — which sections from
    # this downbeat to the end — discarded everything before it. Real (often quieter /
    # kick-only) intros were thrown away: Delacour lost ~16 bars, Mr V / Discosteps
    # their kick-only heads (Sam's 24.06.26 review). The downbeat PHASE is fixed mod 4,
    # so the earliest downbeat in the full grid is (offset % 4) — within the first bar.
    first_downbeat_offset = (db_grid_phase + added_before) % 4
    # Low-confidence tiebreaker (Discosteps): drums alone can't place a 4-on-floor downbeat
    # when the snare histogram is flat (tied drop-entries, agree 0.5 -> DB?), and here the
    # offset landed on an odd beat with NO kick on it (the sparse intro kicks the 1 & 3 on
    # even beats). House tracks start ON the downbeat, so phase bar 1 to the FIRST beat that
    # actually carries a kick. Tracks whose first kick already sits on their downbeat
    # (Delacour) are unchanged; the DB? flag stays (a prior, not a confident read).
    if dagree < DOWNBEAT_PRIOR_AGREE:
        # First the percussion-intro detector (catches a clap intro before a late kick —
        # Discosteps); else the plain first-kick prior (tracks that start on the kick).
        pip = _percussion_intro_phase(drums, sr, full_grid, period)
        if pip is not None:
            first_downbeat_offset, dmethod = pip, "perc-intro"
        else:
            fkp = _first_kick_phase(kicks, full_grid)
            if fkp is not None:
                first_downbeat_offset, dmethod = fkp, "first-kick"
    # JIT (grid genuinely off its own kicks) is the most actionable problem — it wins over
    # LOWC/DB?, and fires whatever the timing source.
    flag = ("JIT" if kf > 15
            else "LOWC" if conf < 0.80
            else "DB?" if dagree < 0.6 else "")
    return BeatGrid(
        beat_times_ms=[int(round(t * 1000)) for t in full_grid],
        first_downbeat_offset=first_downbeat_offset,
        bpm=round(60.0 / period, 2),
        confidence=round(conf, 3),
        grid_vs_kick_ms=round(kf, 2),
        downbeat_method=dmethod,
        downbeat_agree=round(dagree, 2),
        flag=flag,
        snapped_to_asd=snapped,
        timing_src=timing_src,
    )


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
