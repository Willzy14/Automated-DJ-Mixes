"""Tier A per-bar feature families (Phase 1, NO decision changes).

Adds four per-frame dimensions to the cached __stemenv.npz so the detector
can read the music better than the existing loudness-only path:

  * tiera_band_low / tiera_band_mid / tiera_band_high -- full-mix 3-band
    envelopes (low <250 Hz, mid 250-2500 Hz, high >2500 Hz) at the CACHED
    hop rate. Mirror the band split from sections_blind_viz.py (lines 55-73)
    so the visualisation matches the chunk-review convention.

  * tiera_width -- per-frame side/mid RMS ratio from the STEREO wav (native
    sr). mid = 0.5*(L+R), side = 0.5*(L-R), width = side_rms/(mid_rms+1e-12).
    Mono source -> zeros. Matches the Loudness Leveller width measure.

  * tiera_lr_corr -- per-frame Pearson correlation of L and R. Zero-variance
    frames (silence) -> 1.0. Mono source -> ones.

All new arrays are float32 (features, not audio), namespaced under the
"tiera_" prefix so the existing _separate_envelopes loader excludes them
(the loader hygiene change in stem_section_probe enforces this — keeps
the flag-off byte-identical-to-main invariant intact after augmentation).

The five new arrays may differ in length from the existing envelopes by a
frame or two (bands computed at 22050 Hz, width/corr at native sr). They
are trimmed to a common consensus length AFTER computation; the existing
"vocals" / "drums" / etc. arrays are NEVER touched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import soundfile as sf

# Vocal-activity thresholding (Phase 1 only — tunes the eyeball check on
# known-vocal tracks). The point isn't a perfect boundary find, it's a
# plausible bar-level mask the orchestrator can use to gate mix decisions
# that would otherwise walk across a vocal phrase. The MEDIAN-SMOOTHED
# active mask feeds the existing _regions() helper to emit contiguous
# [start_bar, end_bar] blocks, same shape as the existing vocal_regions
# signal so downstream consumers can read either set interchangeably.
VOCAL_ABS_FLOOR = 1e-4       # vocals envelope below this is treated as silence
VOCAL_P95_FRAC = 0.20        # bar is "active" if vocal RMS > max(floor, 0.20 * p95)
TIERA_VOCAL_SMOOTH_BARS = 3  # median-smooth active mask (kills 1-bar flicker)
TIERA_VOCAL_MIN_BARS = 2     # active runs must be at least this long to count as regions

TIERA_BAND_LOW_KEY = "tiera_band_low"
TIERA_BAND_MID_KEY = "tiera_band_mid"
TIERA_BAND_HIGH_KEY = "tiera_band_high"
TIERA_WIDTH_KEY = "tiera_width"
TIERA_LR_CORR_KEY = "tiera_lr_corr"
TIERA_KEYS = (
    TIERA_BAND_LOW_KEY,
    TIERA_BAND_MID_KEY,
    TIERA_BAND_HIGH_KEY,
    TIERA_WIDTH_KEY,
    TIERA_LR_CORR_KEY,
)


def _frame_rms(samples: np.ndarray, hop: int) -> np.ndarray:
    """Per-frame RMS over non-overlapping frames of `hop` samples. Mirrors the
    shape produced by stem_section_probe._separate_envelopes._env."""
    n = len(samples)
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    nfr = n // hop
    if nfr == 0:
        # Very short input — pad to one frame so downstream consumers always
        # see at least one sample.
        fr = np.zeros(hop, dtype=np.float64)
        fr[:n] = samples.astype(np.float64)
        return np.sqrt((fr ** 2).mean() + 1e-12)[np.newaxis]
    fr = samples[: nfr * hop].reshape(nfr, hop)
    return np.sqrt((fr.astype(np.float64) ** 2).mean(axis=1) + 1e-12)


def _frame_corr(L: np.ndarray, R: np.ndarray, hop: int) -> np.ndarray:
    """Per-frame Pearson correlation of L and R over non-overlapping frames.
    Zero-variance frames (silence on either channel) -> 1.0 (defensive default
    — a silent frame has no stereo information, so the +1 is the conventional
    no-op so the curve doesn't break the downstream plot)."""
    n = min(len(L), len(R))
    if n == 0:
        return np.ones(0, dtype=np.float32)
    nfr = n // hop
    if nfr == 0:
        nfr = 1
        # pad to one frame
        def _pad(x):
            out = np.zeros(hop, dtype=np.float64)
            out[: len(x)] = x.astype(np.float64)
            return out
        L = _pad(L)
        R = _pad(R)
    else:
        L = L[: nfr * hop].astype(np.float64).reshape(nfr, hop)
        R = R[: nfr * hop].astype(np.float64).reshape(nfr, hop)
    l_mean = L.mean(axis=1)
    r_mean = R.mean(axis=1)
    l_c = L - l_mean[:, None]
    r_c = R - r_mean[:, None]
    num = (l_c * r_c).sum(axis=1)
    den = np.sqrt((l_c ** 2).sum(axis=1) * (r_c ** 2).sum(axis=1)) + 1e-12
    corr = num / den
    # Numerical noise on a constant frame -> NaN; guard to 1.0.
    corr = np.where(np.isfinite(corr), corr, 1.0)
    # Zero-variance frame (silence on either channel) -> 1.0.
    zero_var = ((l_c ** 2).sum(axis=1) < 1e-20) | ((r_c ** 2).sum(axis=1) < 1e-20)
    corr = np.where(zero_var, 1.0, corr)
    return corr.astype(np.float32)


def _compute_band_envelopes(wav_path: Path, hop_sec: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """3-band envelopes (low/mid/high) at sr=22050 Hz mono.

    Bands mirror sections_blind_viz.py: lowpass 250 Hz, bandpass 250-2500,
    highpass 2500 — 4th-order Butterworth via scipy.signal.sosfilt. The
    result is float32 with frame counts matching the existing 'vocals' env
    only when the WAV happens to be 22050 Hz mono at the same duration.
    In practice these come out slightly shorter (we resample+downmix here)
    so the caller trims to a common length.
    """
    import librosa
    import scipy.signal as sps

    y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
    sos_low = sps.butter(4, 250, "lowpass", fs=sr, output="sos")
    sos_mid = sps.butter(4, [250, 2500], "bandpass", fs=sr, output="sos")
    sos_hi = sps.butter(4, 2500, "highpass", fs=sr, output="sos")
    y_low = sps.sosfilt(sos_low, y).astype(np.float64)
    y_mid = sps.sosfilt(sos_mid, y).astype(np.float64)
    y_hi = sps.sosfilt(sos_hi, y).astype(np.float64)
    hop = max(1, int(sr * hop_sec))
    return (
        _frame_rms(y_low, hop).astype(np.float32),
        _frame_rms(y_mid, hop).astype(np.float32),
        _frame_rms(y_hi, hop).astype(np.float32),
    )


def _compute_stereo_envelopes(wav_path: Path, hop_sec: float) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame width and L/R correlation from the STEREO wav at native sr.

    Mono source -> width=zeros, corr=ones (so the flag-off path through
    downstream code that uses these for plotting is not visibly broken on a
    mono file — Sam may end up with a mono export in a future test).

    width = RMS(side) / (RMS(mid) + 1e-12) -- the linear ratio the
    Revoloution 0.170 -> 0.107 reference was measured in: Loudness Leveller
    uses the same mid/side construction and reports the same ratio.
    """
    data, sr = sf.read(str(wav_path), always_2d=True)
    if data.shape[1] == 1:
        # Mono source -> width is degenerate; default to zeros (and corr to
        # ones so the curve doesn't pretend to measure correlation).
        return np.zeros(0, dtype=np.float32), np.ones(0, dtype=np.float32)
    L = data[:, 0].astype(np.float64)
    R = data[:, 1].astype(np.float64)
    hop = max(1, int(sr * hop_sec))
    mid = 0.5 * (L + R)
    side = 0.5 * (L - R)
    mid_rms = _frame_rms(mid, hop)
    side_rms = _frame_rms(side, hop)
    width = side_rms / (mid_rms + 1e-12)
    # Clamp absurd blowups from a near-zero mid frame (e.g. a single-sample
    # centre-null). Without the cap a single dead frame shoots the width
    # trace to 1e9 and the plot / false-positive flags go nuts. 50 is a sane
    # ceiling — even a heavily-side-panned hard-pan mix won't push the
    # ratio past 10 in practice.
    width = np.clip(width, 0.0, 50.0)
    corr = _frame_corr(L, R, hop)
    return width.astype(np.float32), corr


def _align_lengths(arrays: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Trim all tiera_* arrays to the shortest length. Original arrays are
    never touched here — this is purely for the new keys."""
    if not arrays:
        return arrays
    L = min(len(a) for a in arrays.values())
    return {k: v[:L] for k, v in arrays.items()}


def ensure_tier_a_arrays(wav_path: Path, cache_dir: Path) -> dict[str, np.ndarray]:
    """Ensure the npz cache at <cache_dir>/<wav_stem>__stemenv.npz carries
    the five tiera_* arrays. Returns them as a dict (in the same order as
    TIERA_KEYS for test stability).

    All original keys (hop_t, drums, bass, other, vocals, mix) are preserved
    BYTE-IDENTICALLY: the round-trip is `load -> save_loaded_arrays_verbatim + new`.
    The save path uses np.savez_compressed with the *already loaded* arrays
    (same dtype, same shape, same contents) — no in-place mutation, no
    re-computation of the existing envelopes.

    On a fully-populated cache, returns the loaded tiera_* arrays without
    recomputing (the per-frame audio work is the slow part; the cache
    pattern is the documented Sam directive here).
    """
    cache = cache_dir / f"{wav_path.stem}__stemenv.npz"
    if not cache.exists():
        # The original envelopes aren't there yet either — caller must run
        # stem_section_probe._separate_envelopes first so the canonical
        # envelope cache exists. We don't fan out to Demucs here (Phase 1
        # explicitly forbids torch separation).
        raise FileNotFoundError(
            f"Expected cached envelopes at {cache}. Run "
            "stem_section_probe._separate_envelopes first."
        )
    d = np.load(cache, allow_pickle=False)
    files = set(d.files)
    if all(k in files for k in TIERA_KEYS):
        return {k: d[k] for k in TIERA_KEYS}

    # Pull the hop from the existing cache so the new arrays land at the
    # SAME rate as the existing envelopes (Per-bar conversion happens later
    # in detect() via _per_bar; everything must be at the same hop or the
    # per-bar array alignments get weird).
    hop_sec = float(d["hop_t"])

    band_low, band_mid, band_high = _compute_band_envelopes(wav_path, hop_sec)
    width, lr_corr = _compute_stereo_envelopes(wav_path, hop_sec)

    new_arrays = _align_lengths({
        TIERA_BAND_LOW_KEY: band_low,
        TIERA_BAND_MID_KEY: band_mid,
        TIERA_BAND_HIGH_KEY: band_high,
        TIERA_WIDTH_KEY: width,
        TIERA_LR_CORR_KEY: lr_corr,
    })

    # Round-trip the original arrays BYTE-IDENTICALLY. Re-save with the
    # exact same loaded arrays (no .copy(), no .astype(), no .reshape()).
    save_kwargs: dict[str, np.ndarray] = {k: d[k] for k in d.files}
    save_kwargs.update(new_arrays)
    # Preserve the canonical is_hop_t-first ordering by listing hop_t first
    # explicitly so the file's key order doesn't depend on dict insertion in
    # CPython 3.7+ (which is already insertion-ordered, but the npz
    # archive itself writes keys in the order **kwargs receives them).
    ordered = {}
    if "hop_t" in save_kwargs:
        ordered["hop_t"] = save_kwargs["hop_t"]
    for k in d.files:
        if k == "hop_t":
            continue
        ordered[k] = save_kwargs[k]
    for k in TIERA_KEYS:
        if k not in ordered:
            ordered[k] = new_arrays[k]
    np.savez_compressed(cache, **ordered)
    return {k: ordered[k] for k in TIERA_KEYS}


def vocal_activity_mask(vocals_bar: np.ndarray) -> np.ndarray:
    """Per-bar boolean "vocal is active in this bar" mask.

    Threshold = max(VOCAL_ABS_FLOOR, VOCAL_P95_FRAC * np.percentile(vocals_bar, 95)).
    Median-smoothed over TIERA_VOCAL_SMOOTH_BARS so a 1-bar vocal dip doesn't
    cut a single drone into two regions. Used by threshold_vocal_regions()
    below to emit region blocks.
    """
    vocals_bar = np.asarray(vocals_bar, dtype=np.float64)
    if len(vocals_bar) == 0:
        return np.zeros(0, dtype=bool)
    threshold = max(VOCAL_ABS_FLOOR, VOCAL_P95_FRAC * float(np.percentile(vocals_bar, 95)))
    active = vocals_bar > threshold
    if TIERA_VOCAL_SMOOTH_BARS > 1:
        k = TIERA_VOCAL_SMOOTH_BARS
        pad = k // 2
        xp = np.pad(active.astype(float), pad, mode="edge")
        active = np.array([np.median(xp[i:i + k]) for i in range(len(active))]) >= 0.5
    return active


def threshold_vocal_regions(vocals_bar: np.ndarray,
                             min_bars: int = TIERA_VOCAL_MIN_BARS,
                             downbeat: float = 0.0,
                             sec_per_bar: float = 1.0) -> list[list[float]]:
    """Per-bar vocal regions in the same shape as the existing
    vocal_regions signal: [start_sec, end_sec, start_bar, end_bar] per
    region, with the threshold determined from the input (so the
    orchestrator can see WHICH threshold was used for downstream gating).
    """
    vocals_bar = np.asarray(vocals_bar, dtype=np.float64)
    active = vocal_activity_mask(vocals_bar)
    # _regions() helper isn't imported here to keep this module
    # standalone-testable (the vocal thresholding tests don't want to pull
    # in all of stem_detector). Reimplement the small bit we need.
    out, n, i = [], len(active), 0
    while i < n:
        if active[i]:
            j = i
            while j < n and active[j]:
                j += 1
            if j - i >= min_bars:
                out.append([
                    round(downbeat + i * sec_per_bar, 2),
                    round(downbeat + j * sec_per_bar, 2),
                    i, j,
                ])
            i = j
        else:
            i += 1
    return out


def vocal_threshold_for(vocals_bar: Iterable[float]) -> float:
    """Expose the threshold for the calling detect() so it can be reported
    in signals.tier_a.vocal_threshold. Pure function of the input."""
    arr = np.asarray(list(vocals_bar), dtype=np.float64)
    if len(arr) == 0:
        return float(VOCAL_ABS_FLOOR)
    return float(max(VOCAL_ABS_FLOOR, VOCAL_P95_FRAC * float(np.percentile(arr, 95))))
