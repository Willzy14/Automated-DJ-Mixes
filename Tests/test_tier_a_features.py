"""Unit tests for the Tier A per-bar feature families.

The Tier A augmentation adds 3-band envelopes, stereo width, L/R correlation,
and a vocal-activity mask to the cached __stemenv.npz. NO test in this file
touches the existing section-boundary / label decisions — Phase 1 is
compute/cache/export/viz only.

Style matches Tests/test_stem_detector_energy_cues.py + test_section_soft_rules.py:
small synthetic inputs, no torch, no Demucs, no corpus dependency for the
synthetic cases. The corpus-gated detect() round-trip at the end skips
cleanly when the Test Project is not in the worktree (the same pattern
test_rekordbox_health.py uses).

The five test cases:
  a. Width synthetic: L==R -> ~0 width, ~+1 corr; L==-R -> large width, ~-1 corr.
  b. Band routing: 100 Hz -> low dominant, 1 kHz -> mid dominant, 6 kHz -> high dominant.
  c. Vocal thresholding: known active stretch returns exactly that region.
  d. npz additivity: load -> ensure_tier_a_arrays -> reload; original arrays
     byte-identical, tiera_ keys present, _separate_envelopes still returns
     exactly the 5 original non-hop_t keys.
  e. Corpus-gated flag-off shape: detect() with default flags has no tier_a
     signal in result["signals"].
"""

from __future__ import annotations

import ast
import io
import os
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

import stem_section_probe as ssp
import _tier_a_features as taf


# --- (a) Width synthetic: stereo wiring -------------------------------------

def _write_stereo_wav(path: Path, L: np.ndarray, R: np.ndarray, sr: int = 44100) -> None:
    """Write a 2-channel float32 WAV. Used by the stereo-routing tests."""
    data = np.stack([L.astype(np.float32), R.astype(np.float32)], axis=1)
    sf.write(str(path), data, sr, subtype="FLOAT")


def _sine_at(freq_hz: float, seconds: float, sr: int = 44100, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(seconds * sr), dtype=np.float64) / sr
    return (amp * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)


def test_width_l_eq_r_yields_zero_width_and_unit_corr(tmp_path):
    """L == R -> mid dominates, side is zero -> width ~ 0; corr ~ +1."""
    sr = 44100
    seconds = 1.0
    x = _sine_at(440.0, seconds, sr=sr)
    L = np.tile(x, 3)  # ~3s so the bandwidth check has plenty of frames
    R = L.copy()
    wav = tmp_path / "mono_sum.wav"
    _write_stereo_wav(wav, L, R, sr=sr)
    width, corr = taf._compute_stereo_envelopes(wav, hop_sec=0.1)
    assert width.shape == corr.shape
    assert len(width) > 0
    # L==R -> mid = L, side = 0 -> width should be ~0 across every frame.
    # Allow a tiny numerical floor; we want no frame above 0.05.
    assert (width <= 0.05).all(), f"width should be ~0 for L==R, got max={width.max():.4f}"
    # corr should be ~+1 everywhere.
    assert (corr >= 0.95).all(), f"corr should be ~1 for L==R, got min={corr.min():.4f}"


def test_width_l_eq_neg_r_yields_large_width_and_negative_corr(tmp_path):
    """L == -R -> mid is zero, side is full -> width shoots up; corr ~ -1."""
    sr = 44100
    x = _sine_at(440.0, 1.0, sr=sr)
    L = np.tile(x, 3)
    R = -L
    wav = tmp_path / "mono_diff.wav"
    _write_stereo_wav(wav, L, R, sr=sr)
    width, corr = taf._compute_stereo_envelopes(wav, hop_sec=0.1)
    assert len(width) > 0
    # The mid channel is identically zero -> mid_rms would be ~0 except for
    # the _frame_rms numerical floor (1e-12). Width is clamped at 50, so
    # every frame should be at the maximum. We just assert the direction.
    assert (width >= 1.0).all(), f"L==-R width should be very large, got min={width.min():.4f}"
    # Pearson of L and -L is exactly -1. floor at 0.95.
    assert (corr <= -0.95).all(), f"L==-R corr should be ~-1, got max={corr.max():.4f}"


def test_mono_source_width_and_corr_are_empty_float32_arrays(tmp_path):
    """A mono source has no side information, so _compute_stereo_envelopes
    returns EMPTY (length 0) float32 arrays for width and corr, not zeros and
    ones. Downstream (align_engine) reads a zero-length tiera_* array as
    'unmeasured', so the shape is the contract and it is pinned here.

    Rewritten for card 216: the old version asserted np.all(width == 0.0) and
    np.all(corr == 1.0), and np.all over an empty array is True for any value,
    so it said nothing about what the mono branch returned (it also passed for
    non-empty zeros/ones, a different contract, and for empty float64)."""
    sr = 44100
    x = _sine_at(440.0, 0.5, sr=sr)
    data = x.astype(np.float32).reshape(-1, 1)  # 1 channel
    wav = tmp_path / "mono.wav"
    sf.write(str(wav), data, sr, subtype="FLOAT")
    width, corr = taf._compute_stereo_envelopes(wav, hop_sec=0.1)
    assert width.shape == (0,), f"mono width should be empty, got shape {width.shape}"
    assert corr.shape == (0,), f"mono corr should be empty, got shape {corr.shape}"
    assert width.dtype == np.float32 and corr.dtype == np.float32
    # Positive control: the same helper on a 2-channel copy of the same signal
    # is NOT empty, so the empty result above is the mono handling and not a
    # helper that always returns nothing.
    stereo = tmp_path / "stereo.wav"
    _write_stereo_wav(stereo, x, x, sr=sr)
    width2, corr2 = taf._compute_stereo_envelopes(stereo, hop_sec=0.1)
    assert len(width2) > 0 and len(corr2) == len(width2)


# --- (b) Band routing: low / mid / high --------------------------------------

def _write_mono_wav(path: Path, samples: np.ndarray, sr: int = 22050) -> None:
    sf.write(str(path), samples.astype(np.float32), sr, subtype="FLOAT")


def test_band_low_dominant_for_100hz_sine(tmp_path):
    """A 100 Hz sine belongs in the low band (<250 Hz)."""
    seconds = 1.0
    sr = 22050
    y = _sine_at(100.0, seconds, sr=sr)
    wav = tmp_path / "low.wav"
    _write_mono_wav(wav, y, sr=sr)
    low, mid, high = taf._compute_band_envelopes(wav, hop_sec=0.1)
    assert len(low) > 0 and len(mid) > 0 and len(high) > 0
    # Low-band RMS should be substantially larger than mid and high.
    # The sine is 100 Hz; the mid band (250-2500) and high band (>2500)
    # should both be near-zero. Allow a small leakage margin.
    assert low.mean() > mid.mean() * 5, (
        f"low.mean()={low.mean():.6f} should dominate mid.mean()={mid.mean():.6f}"
    )
    assert low.mean() > high.mean() * 5, (
        f"low.mean()={low.mean():.6f} should dominate high.mean()={high.mean():.6f}"
    )


def test_band_mid_dominant_for_1khz_sine(tmp_path):
    """A 1 kHz sine belongs in the mid band (250-2500 Hz)."""
    seconds = 1.0
    sr = 22050
    y = _sine_at(1000.0, seconds, sr=sr)
    wav = tmp_path / "mid.wav"
    _write_mono_wav(wav, y, sr=sr)
    low, mid, high = taf._compute_band_envelopes(wav, hop_sec=0.1)
    assert mid.mean() > low.mean() * 5, (
        f"mid.mean()={mid.mean():.6f} should dominate low.mean()={low.mean():.6f}"
    )
    assert mid.mean() > high.mean() * 5, (
        f"mid.mean()={mid.mean():.6f} should dominate high.mean()={high.mean():.6f}"
    )


def test_band_high_dominant_for_6khz_sine(tmp_path):
    """A 6 kHz sine belongs in the high band (>2500 Hz)."""
    seconds = 1.0
    sr = 22050
    y = _sine_at(6000.0, seconds, sr=sr)
    wav = tmp_path / "high.wav"
    _write_mono_wav(wav, y, sr=sr)
    low, mid, high = taf._compute_band_envelopes(wav, hop_sec=0.1)
    assert high.mean() > low.mean() * 5, (
        f"high.mean()={high.mean():.6f} should dominate low.mean()={low.mean():.6f}"
    )
    assert high.mean() > mid.mean() * 5, (
        f"high.mean()={high.mean():.6f} should dominate mid.mean()={mid.mean():.6f}"
    )


# --- (c) Vocal thresholding -------------------------------------------------

def test_vocal_active_mask_and_region_emit_only_active_stretch():
    """A clear programmatic stretch of high vocal bars against a quiet
    baseline MUST come back as exactly one region, with the right span."""
    # 30 bars: first 5 quiet, then 10 loud, then 15 quiet.
    bar = np.zeros(30, dtype=float)
    bar[5:15] = 1.0
    mask = taf.vocal_activity_mask(bar)
    assert mask.dtype == bool
    assert mask.sum() == 10, f"expected 10 active bars, got {mask.sum()}"
    assert mask[0] == False and mask[4] == False
    assert mask[5] == True and mask[14] == True
    assert mask[15] == False and mask[29] == False
    # Regionhelper returns the to_sec() shape: [start_sec, end_sec, start_bar, end_bar].
    regs = taf.threshold_vocal_regions(bar, min_bars=2, downbeat=0.0, sec_per_bar=1.0)
    assert len(regs) == 1, f"expected exactly one region, got {len(regs)}: {regs}"
    s_sec, e_sec, s_bar, e_bar = regs[0]
    assert s_bar == 5 and e_bar == 15
    assert s_sec == 5.0 and e_sec == 15.0


def test_vocal_active_mask_short_blip_is_smoothed_out():
    """A 1-bar vocal blip in the middle of a quiet stretch should be
    dropped by the median-smooth so the region list stays empty."""
    bar = np.zeros(20, dtype=float)
    bar[10] = 1.0
    mask = taf.vocal_activity_mask(bar)
    assert mask.sum() == 0, f"1-bar blip should be smoothed away, got {mask.sum()}"
    # But if the threshold is dropped to floor alone (e.g. a very tiny
    # floor), the mask still produces no region because MIN_BARS=2.
    regs = taf.threshold_vocal_regions(bar, min_bars=2, downbeat=0.0, sec_per_bar=1.0)
    assert regs == []


def test_vocal_threshold_for_is_documented():
    """The threshold must be max(VOCAL_ABS_FLOOR, VOCAL_P95_FRAC * p95) and
    is exposed via vocal_threshold_for so detect() can report it."""
    bar = np.linspace(0.0, 1.0, 100)
    t = taf.vocal_threshold_for(bar)
    # p95 of [0..1] is 0.95, 0.20 * 0.95 = 0.19 -> threshold = 0.19.
    assert abs(t - 0.19) < 1e-9, f"expected 0.19, got {t}"
    # Tiny input -> threshold should clamp to VOCAL_ABS_FLOOR.
    assert taf.vocal_threshold_for([0.0, 0.0, 0.0]) == taf.VOCAL_ABS_FLOOR


# --- (d) npz additivity ---------------------------------------------------

def test_ensure_tier_a_arrays_preserves_original_keys_byte_identically(tmp_path):
    """Build a fake canonical npz, run ensure_tier_a_arrays, reload, and
    verify every ORIGINAL key (hop_t, drums, bass, other, vocals, mix) is
    byte-identical to the pre-augmentation version. This is the audit
    trap from the brief: the loader change MUST exclude tiera_ keys while
    leaving the originals untouched."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    wav = tmp_path / "fake.wav"
    # 1-second stereo 440 Hz sine so the band routing + width/corr routines
    # have something to chew on.
    sr = 44100
    x = _sine_at(440.0, 1.0, sr=sr)
    _write_stereo_wav(wav, x, x, sr=sr)
    # Build a fake canonical npz with the same shape as the real cache.
    hop_sec = 0.1
    n_frames = int(1.0 / hop_sec)  # 10 frames
    canonical = {
        "hop_t": np.array(hop_sec, dtype=np.float64),
        "drums": np.linspace(0.1, 0.5, n_frames).astype(np.float64),
        "bass": np.linspace(0.2, 0.4, n_frames).astype(np.float64),
        "other": np.linspace(0.05, 0.3, n_frames).astype(np.float64),
        "vocals": np.linspace(0.0, 0.7, n_frames).astype(np.float64),
        "mix": np.linspace(0.3, 0.9, n_frames).astype(np.float64),
    }
    cache_npz = cache_dir / f"{wav.stem}__stemenv.npz"
    np.savez_compressed(cache_npz, **canonical)

    # First call: missing tiera_ keys -> compute + augment.
    new_arrays = taf.ensure_tier_a_arrays(wav, cache_dir)
    assert set(new_arrays.keys()) == set(taf.TIERA_KEYS)
    # All tiera_ arrays should be float32.
    for k, v in new_arrays.items():
        assert v.dtype == np.float32, f"{k} dtype should be float32, got {v.dtype}"

    # Reload the npz and verify the original keys are BYTE-IDENTICAL.
    d_reload = np.load(cache_npz, allow_pickle=False)
    for k in canonical.keys():
        assert k in d_reload.files, f"original key {k} missing after augmentation"
        a, b = canonical[k], d_reload[k]
        assert a.dtype == b.dtype, f"dtype mismatch on {k}: {a.dtype} vs {b.dtype}"
        assert a.shape == b.shape, f"shape mismatch on {k}: {a.shape} vs {b.shape}"
        # tobytes() is the strict byte-comparison the brief asks for.
        assert a.tobytes() == b.tobytes(), f"contents differ on {k}"
    # And the tiera_ keys must now be present.
    for k in taf.TIERA_KEYS:
        assert k in d_reload.files, f"tiera_ key {k} missing after augmentation"

    # Second call: cache hit -> no recompute, returns the same arrays.
    second = taf.ensure_tier_a_arrays(wav, cache_dir)
    for k in taf.TIERA_KEYS:
        np.testing.assert_array_equal(second[k], new_arrays[k])


def test_separate_envelopes_excludes_tiera_keys_after_augmentation(tmp_path):
    """The _separate_envelopes loader hygiene change MUST drop tiera_ keys
    so the existing envs dict shape is unchanged. After augmentation, the
    loader returns EXACTLY the 5 original non-hop_t keys."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    wav = tmp_path / "fake.wav"
    sr = 44100
    x = _sine_at(440.0, 1.0, sr=sr)
    _write_stereo_wav(wav, x, x, sr=sr)
    hop_sec = 0.1
    n_frames = int(1.0 / hop_sec)
    canonical = {
        "hop_t": np.array(hop_sec, dtype=np.float64),
        "drums": np.linspace(0.1, 0.5, n_frames).astype(np.float64),
        "bass": np.linspace(0.2, 0.4, n_frames).astype(np.float64),
        "other": np.linspace(0.05, 0.3, n_frames).astype(np.float64),
        "vocals": np.linspace(0.0, 0.7, n_frames).astype(np.float64),
        "mix": np.linspace(0.3, 0.9, n_frames).astype(np.float64),
    }
    cache_npz = cache_dir / f"{wav.stem}__stemenv.npz"
    np.savez_compressed(cache_npz, **canonical)

    # Augment.
    taf.ensure_tier_a_arrays(wav, cache_dir)

    # Read via the canonical loader (the one downstream stem_detector uses).
    envs, hop_t = ssp._separate_envelopes(wav, cache_dir)
    assert hop_t == hop_sec
    assert set(envs.keys()) == {"drums", "bass", "other", "vocals", "mix"}, (
        f"loader returned unexpected keys: {sorted(envs.keys())}"
    )
    # And the lengths are still the canonical n_frames.
    for k, v in envs.items():
        assert len(v) == n_frames, f"{k} length changed from {n_frames} to {len(v)}"


# --- (e) Corpus-gated flag-off shape ---------------------------------------

CORPUS = Path("Test Project/14.08.26")


def _corpus_wavs():
    if not CORPUS.exists():
        return []
    return sorted((CORPUS / "Audio").glob("*.wav"))


@pytest.mark.skipif(not CORPUS.exists(), reason="Test Project/14.08.26 corpus not in worktree")
def test_flag_off_omits_tier_a_signal_in_detect(tmp_path):
    """detect() with the default flags (tier_a=False) MUST NOT emit a
    'tier_a' key in result['signals']. Pinning the flag-OFF shape ensures
    the JSON stays byte-identical to main @ 4103ccf once the npz caches are
    augmented (the augmentation is in-place; the loader hygiene change
    isolates it from the existing flow)."""
    from stem_detector import detect
    wavs = _corpus_wavs()
    assert wavs, "expected at least one corpus wav"
    # A scanned-from-cache JSON gives us bpm + downbeat without running the
    # stats JSON path (those files aren't on disk in this worktree).
    wav = wavs[0]
    # We need a place to write the per-frame npz cache so the existing
    # _separate_envelopes can read it back. Inject a fake envs dict via
    # monkeypatching to skip the Demucs path entirely.
    stub_envs = {
        "drums": np.linspace(0.1, 0.5, 3000),
        "bass": np.linspace(0.2, 0.4, 3000),
        "other": np.linspace(0.05, 0.3, 3000),
        "vocals": np.linspace(0.0, 0.7, 3000),
        "mix": np.linspace(0.3, 0.9, 3000),
    }
    import stem_detector as sd
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sd, "_separate_envelopes", lambda *_a, **_k: (stub_envs, 0.1))
    try:
        # write_json=False, make_viz=False keeps the shared corpus untouched.
        # Tier-a flag is default False.
        res = sd.detect(wav, CORPUS, bpm=125.0, downbeat=0.0,
                        make_viz=False, write_json=False)
    finally:
        monkey.undo()
    assert res is not None
    assert "tier_a" not in res["signals"], (
        "flag-OFF detect() must OMIT the 'tier_a' signal key entirely "
        "(not emit null). Otherwise the OFF JSON can't be byte-identical to main."
    )


@pytest.mark.skipif(not CORPUS.exists(), reason="Test Project/14.08.26 corpus not in worktree")
def test_flag_on_emits_tier_a_signal_with_documented_keys(tmp_path):
    """When tier_a=True, detect() must attach a 'tier_a' signal with the
    five documented keys. The signal must NOT change any of the existing
    signals (byte-identical outside the new key)."""
    from stem_detector import detect
    wavs = _corpus_wavs()
    assert wavs
    wav = wavs[0]
    stub_envs = {
        "drums": np.linspace(0.1, 0.5, 3000),
        "bass": np.linspace(0.2, 0.4, 3000),
        "other": np.linspace(0.05, 0.3, 3000),
        "vocals": np.linspace(0.0, 0.7, 3000),
        "mix": np.linspace(0.3, 0.9, 3000),
    }
    import stem_detector as sd
    # Cache dir for the fake npz lives in a temp dir so the shared corpus
    # stays untouched.
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    # Pre-populate a stub __stemenv.npz so ensure_tier_a_arrays has
    # something to round-trip. The wav points at the real corpus audio
    # file, but the cache is local so the round-trip is hermetic.
    hop_sec = 0.1
    n_frames = 3000
    canonical = {
        "hop_t": np.array(hop_sec, dtype=np.float64),
        "drums": np.linspace(0.1, 0.5, n_frames).astype(np.float64),
        "bass": np.linspace(0.2, 0.4, n_frames).astype(np.float64),
        "other": np.linspace(0.05, 0.3, n_frames).astype(np.float64),
        "vocals": np.linspace(0.0, 0.7, n_frames).astype(np.float64),
        "mix": np.linspace(0.3, 0.9, n_frames).astype(np.float64),
    }
    np.savez_compressed(cache_dir / f"{wav.stem}__stemenv.npz", **canonical)
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sd, "_separate_envelopes", lambda *_a, **_k: (stub_envs, 0.1))
    try:
        # Point detect() at a project dir that owns only the temp cache dir
        # so the wav-resolution path doesn't walk outside tmp_path.
        project_root = tmp_path / "project"
        project_root.mkdir()
        # Mirror the cache dir under _Stem Analysis so ensure_tier_a_arrays
        # finds it via project / "_Stem Analysis".
        cache_target = project_root / "_Stem Analysis"
        cache_target.mkdir()
        np.savez_compressed(cache_target / f"{wav.stem}__stemenv.npz", **canonical)
        res = sd.detect(wav, project_root, bpm=125.0, downbeat=0.0,
                        make_viz=False, write_json=False, tier_a=True)
    finally:
        monkey.undo()
    assert res is not None
    assert "tier_a" in res["signals"], "flag-ON detect() must emit tier_a"
    t = res["signals"]["tier_a"]
    # Documented keys must all be present.
    expected = {"band_low_bar", "band_mid_bar", "band_high_bar",
                "width_bar", "lr_corr_bar",
                "vocal_active_bar", "vocal_active_regions", "vocal_threshold"}
    assert expected <= set(t.keys()), f"missing tier_a keys: {expected - set(t.keys())}"
    # vocal_threshold is a float; the others are lists of numbers.
    assert isinstance(t["vocal_threshold"], float)
    # Length consistency across the per-bar arrays.
    n = len(t["band_low_bar"])
    assert len(t["band_mid_bar"]) == n
    assert len(t["band_high_bar"]) == n
    assert len(t["width_bar"]) == n
    assert len(t["lr_corr_bar"]) == n
    assert len(t["vocal_active_bar"]) == n
    # vocal_active_bar must be 0 or 1 ints.
    for v in t["vocal_active_bar"]:
        assert v in (0, 1)
    # The stamps must be inside the cached n_bars (sanity check).
    assert n >= 1


# --- Hot-cache invariant: same detect() input -> same output post-augment ---

def test_corpus_detect_off_is_stable_after_npzaugment(tmp_path):
    """If the npz cache is already augmented with tiera_ keys, _separate_envelopes
    must still return exactly the 5 original non-hop_t keys (no tiera_ keys
    leaking into the envs dict). Pinning this prevents the future regression
    where someone adds a new key without 'tiera_' prefix and silently changes
    the env dict shape."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    wav = tmp_path / "fake.wav"
    sr = 44100
    x = _sine_at(440.0, 1.0, sr=sr)
    _write_stereo_wav(wav, x, x, sr=sr)
    canonical = {
        "hop_t": np.array(0.1, dtype=np.float64),
        "drums": np.linspace(0.1, 0.5, 10).astype(np.float64),
        "bass": np.linspace(0.2, 0.4, 10).astype(np.float64),
        "other": np.linspace(0.05, 0.3, 10).astype(np.float64),
        "vocals": np.linspace(0.0, 0.7, 10).astype(np.float64),
        "mix": np.linspace(0.3, 0.9, 10).astype(np.float64),
    }
    np.savez_compressed(cache_dir / f"{wav.stem}__stemenv.npz", **canonical)
    taf.ensure_tier_a_arrays(wav, cache_dir)
    envs, hop_t = ssp._separate_envelopes(wav, cache_dir)
    # The future-most-regression-amenable assertion: the keys are exactly the
    # set downstream consumers depend on.
    assert set(envs.keys()) == {"drums", "bass", "other", "vocals", "mix"}
    assert hop_t == 0.1


# --- Hardening: atomic cache write, closed load handle, mono, Rekordbox pin ---
# Added for Master Board card 216 (Tier A cache hardening). The cache tests use
# stubbed envelope computes (the real compute is covered above) so they isolate
# the cache read/write behaviour and stay fast.

def _canonical_cache_arrays(n_frames: int = 10) -> dict:
    return {
        "hop_t": np.array(0.1, dtype=np.float64),
        "drums": np.linspace(0.1, 0.5, n_frames).astype(np.float64),
        "bass": np.linspace(0.2, 0.4, n_frames).astype(np.float64),
        "other": np.linspace(0.05, 0.3, n_frames).astype(np.float64),
        "vocals": np.linspace(0.0, 0.7, n_frames).astype(np.float64),
        "mix": np.linspace(0.3, 0.9, n_frames).astype(np.float64),
    }


def _make_cache(tmp_path: Path, mono: bool = False):
    """A wav plus a canonical (un-augmented) cache beside it. Returns
    (wav, cache_dir, cache_path, canonical_arrays)."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    wav = tmp_path / "fake.wav"
    x = _sine_at(440.0, 1.0, sr=44100)
    if mono:
        sf.write(str(wav), x.reshape(-1, 1), 44100, subtype="FLOAT")
    else:
        _write_stereo_wav(wav, x, x, sr=44100)
    canonical = _canonical_cache_arrays()
    cache = cache_dir / f"{wav.stem}__stemenv.npz"
    np.savez_compressed(cache, **canonical)
    return wav, cache_dir, cache, canonical


def _stub_compute(monkeypatch) -> dict:
    """Replace the two envelope computes with cheap deterministic arrays (bands
    one frame longer than width/corr, so the trim-to-shortest still runs).
    Returns the arrays ensure_tier_a_arrays is expected to hand back."""
    band_low = np.linspace(0.1, 0.2, 9).astype(np.float32)
    band_mid = np.linspace(0.2, 0.3, 9).astype(np.float32)
    band_high = np.linspace(0.3, 0.4, 9).astype(np.float32)
    width = np.linspace(0.0, 1.0, 8).astype(np.float32)
    corr = np.linspace(1.0, 0.5, 8).astype(np.float32)
    monkeypatch.setattr(taf, "_compute_band_envelopes",
                        lambda _w, _h: (band_low, band_mid, band_high))
    monkeypatch.setattr(taf, "_compute_stereo_envelopes",
                        lambda _w, _h: (width, corr))
    return {
        taf.TIERA_BAND_LOW_KEY: band_low[:8],
        taf.TIERA_BAND_MID_KEY: band_mid[:8],
        taf.TIERA_BAND_HIGH_KEY: band_high[:8],
        taf.TIERA_WIDTH_KEY: width,
        taf.TIERA_LR_CORR_KEY: corr,
    }


def _listing(folder: Path) -> list:
    return sorted(p.name for p in folder.iterdir())


def _assert_old_cache_intact(cache: Path, before: bytes, canonical: dict) -> None:
    assert cache.read_bytes() == before, (
        "the previous cache must be byte-for-byte intact after a failed write"
    )
    with np.load(cache, allow_pickle=False) as z:
        assert z.files == list(canonical)
        for k, v in canonical.items():
            np.testing.assert_array_equal(z[k], v)


def test_module_under_test_is_this_checkout():
    """Guard against importing a stale copy of the module (the repo also has
    a main checkout and other worktrees). Prints the path (run with -s)."""
    expected = (Path(__file__).resolve().parent.parent / "Source").resolve()
    print(f"_tier_a_features imported from: {taf.__file__}")
    assert Path(taf.__file__).resolve().parent == expected


@pytest.mark.parametrize("exc_type", [OSError, KeyboardInterrupt])
def test_interrupted_cache_write_keeps_old_cache_and_leaves_no_partial(
        tmp_path, monkeypatch, exc_type):
    """Half the new npz reaches disk, then the write dies. The previous cache
    must be untouched (it loads, byte-for-byte) and no partial file remains.
    The original exception propagates, as it does today."""
    wav, cache_dir, cache, canonical = _make_cache(tmp_path)
    _stub_compute(monkeypatch)
    before = cache.read_bytes()
    real_savez = np.savez_compressed

    def interrupted(target, **arrays):
        buf = io.BytesIO()
        real_savez(buf, **arrays)
        blob = buf.getvalue()
        half = blob[: len(blob) // 2]
        if hasattr(target, "write"):
            target.write(half)
            target.flush()
        else:
            Path(target).write_bytes(half)
        raise exc_type("simulated interruption mid-write")

    monkeypatch.setattr(np, "savez_compressed", interrupted)
    with pytest.raises(exc_type):
        taf.ensure_tier_a_arrays(wav, cache_dir)
    _assert_old_cache_intact(cache, before, canonical)
    assert _listing(cache_dir) == [cache.name], f"leftover files: {_listing(cache_dir)}"


def test_failed_replace_raises_and_keeps_old_cache_and_leaves_no_partial(
        tmp_path, monkeypatch):
    """The npz is fully written but the swap into place fails (a reader holds
    the cache open on Windows). The PermissionError propagates, the old cache
    is intact and the partial is removed."""
    wav, cache_dir, cache, canonical = _make_cache(tmp_path)
    _stub_compute(monkeypatch)
    before = cache.read_bytes()
    real_replace = os.replace

    def locked(src, dst, *args, **kwargs):
        if Path(dst) == cache:
            raise PermissionError(13, "simulated sharing violation")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(PermissionError):
        taf.ensure_tier_a_arrays(wav, cache_dir)
    _assert_old_cache_intact(cache, before, canonical)
    assert _listing(cache_dir) == [cache.name], f"leftover files: {_listing(cache_dir)}"


def test_successful_cache_write_round_trips_same_arrays_and_leaves_no_partial(
        tmp_path, monkeypatch):
    """Regression pin (passes on the old and the new code by design): a
    successful write then a load returns the same arrays, original keys stay
    byte-identical and in order, the final file name is unchanged, nothing else
    is left in the folder, and a second call is a cache hit."""
    wav, cache_dir, cache, canonical = _make_cache(tmp_path)
    expected = _stub_compute(monkeypatch)
    out = taf.ensure_tier_a_arrays(wav, cache_dir)
    assert list(out) == list(taf.TIERA_KEYS)
    for k in taf.TIERA_KEYS:
        assert out[k].dtype == np.float32
        np.testing.assert_array_equal(out[k], expected[k])
    assert _listing(cache_dir) == [cache.name]
    with np.load(cache, allow_pickle=False) as z:
        assert z.files == list(canonical) + list(taf.TIERA_KEYS)
        for k, v in canonical.items():
            assert z[k].dtype == v.dtype and z[k].tobytes() == v.tobytes()
        for k in taf.TIERA_KEYS:
            np.testing.assert_array_equal(z[k], expected[k])

    def must_not_recompute(*_a, **_k):
        raise AssertionError("cache hit must not recompute")

    monkeypatch.setattr(taf, "_compute_band_envelopes", must_not_recompute)
    monkeypatch.setattr(taf, "_compute_stereo_envelopes", must_not_recompute)
    again = taf.ensure_tier_a_arrays(wav, cache_dir)
    for k in taf.TIERA_KEYS:
        np.testing.assert_array_equal(again[k], expected[k])
    assert _listing(cache_dir) == [cache.name]


def test_successful_cache_write_goes_through_a_same_folder_partial_then_replace(
        tmp_path, monkeypatch):
    """The new cache is built under a temporary '.partial' name in the SAME
    folder (so the swap stays on one volume) and swapped in by os.replace."""
    wav, cache_dir, cache, _canonical = _make_cache(tmp_path)
    _stub_compute(monkeypatch)
    calls = []
    real_replace = os.replace

    def spy(src, dst, *args, **kwargs):
        calls.append((Path(src), Path(dst), Path(src).exists()))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", spy)
    taf.ensure_tier_a_arrays(wav, cache_dir)
    assert len(calls) == 1, f"expected exactly one os.replace, saw {len(calls)}"
    src, dst, src_existed = calls[0]
    assert dst == cache
    assert src.parent == cache_dir and src != cache
    assert src.name.endswith(".partial")
    assert src_existed
    assert _listing(cache_dir) == [cache.name]


def test_cache_load_closes_its_file_handle(tmp_path, monkeypatch):
    """np.load of an npz is lazy and holds the file open until it is closed.
    Keep a reference to every NpzFile the module opens (so garbage collection
    cannot close it for us) and require each to be closed already, on the
    cache-miss path and on the cache-hit path."""
    wav, cache_dir, _cache, _canonical = _make_cache(tmp_path)
    _stub_compute(monkeypatch)
    opened = []
    real_load = np.load

    def spy(*args, **kwargs):
        z = real_load(*args, **kwargs)
        opened.append(z)
        return z

    monkeypatch.setattr(np, "load", spy)
    first = taf.ensure_tier_a_arrays(wav, cache_dir)
    second = taf.ensure_tier_a_arrays(wav, cache_dir)
    assert len(opened) == 2
    for z in opened:
        assert z.zip is None, "np.load handle still open after ensure_tier_a_arrays returned"
    # The arrays handed back are plain in-memory arrays, usable after the close.
    for k in taf.TIERA_KEYS:
        np.testing.assert_array_equal(first[k], second[k])


def test_cache_is_free_to_replace_and_delete_while_a_failure_traceback_is_alive(
        tmp_path, monkeypatch):
    """Windows proof of the closed handle. A compute failure leaves its
    traceback (and so every local of ensure_tier_a_arrays) alive in excinfo.
    If the cache were still open there, os.replace / unlink on it would raise a
    PermissionError sharing violation on Windows. (On other platforms this
    passes either way; the handle test above is the portable proof.)"""
    wav, cache_dir, cache, canonical = _make_cache(tmp_path)
    _stub_compute(monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("compute failed")

    monkeypatch.setattr(taf, "_compute_band_envelopes", boom)
    with pytest.raises(RuntimeError, match="compute failed") as excinfo:
        taf.ensure_tier_a_arrays(wav, cache_dir)
    replacement = cache_dir / "replacement.npz"
    np.savez_compressed(replacement, **canonical)
    os.replace(replacement, cache)
    cache.unlink()
    assert not cache.exists()
    assert excinfo.value is not None


def test_mono_source_collapses_all_five_tiera_arrays_to_empty_and_caches_them(tmp_path):
    """End to end through the real computes on a mono wav: the empty width and
    corr make the trim-to-shortest collapse ALL five tiera_* arrays to length 0
    (the mono wart align_engine reads as 'unmeasured'). They are written to the
    cache as empty float32 arrays, the original keys survive, and a second call
    is a cache hit returning the same empties."""
    wav, cache_dir, cache, canonical = _make_cache(tmp_path, mono=True)
    out = taf.ensure_tier_a_arrays(wav, cache_dir)
    assert list(out) == list(taf.TIERA_KEYS)
    for k, v in out.items():
        assert v.shape == (0,) and v.dtype == np.float32, f"{k}: {v.shape} {v.dtype}"
    with np.load(cache, allow_pickle=False) as z:
        for k in canonical:
            assert z[k].tobytes() == canonical[k].tobytes()
        for k in taf.TIERA_KEYS:
            assert z[k].shape == (0,) and z[k].dtype == np.float32
    again = taf.ensure_tier_a_arrays(wav, cache_dir)
    for k in taf.TIERA_KEYS:
        assert again[k].shape == (0,)
    assert _listing(cache_dir) == [cache.name]


# --- Rekordbox removal pin: any spelling of an import of pyrekordbox ---------
# Tests/test_rekordbox_health.py::test_no_rb_imports_in_package only greps the
# package text for two module names, so `import pyrekordbox as pr` passes it.
# This pin reads the syntax tree instead. (That file is outside the files this
# card may edit, so the stronger pin lives here.)

_RB_ROOT = "pyrekordbox"


def _is_rb(name) -> bool:
    return isinstance(name, str) and name.split(".")[0] == _RB_ROOT


def _pyrekordbox_import_hits(source, label: str = "<source>") -> list:
    """Every place `source` (text or bytes) imports pyrekordbox, however it is
    spelled: import / import-as / from-import / submodule forms, and the
    dynamic importlib.import_module("...") and __import__("...") calls with a
    literal name. A fully dynamic name (a variable) cannot be seen statically."""
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_rb(alias.name):
                    hits.append(f"{label}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if _is_rb(node.module):
                hits.append(f"{label}:{node.lineno}: from {node.module} import ...")
            else:
                for alias in node.names:
                    if _is_rb(alias.name):
                        hits.append(f"{label}:{node.lineno}: from {node.module} import {alias.name}")
        elif isinstance(node, ast.Call):
            fn = node.func
            fn_name = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else None)
            if fn_name in ("import_module", "__import__"):
                args = list(node.args[:1]) + [
                    kw.value for kw in node.keywords if kw.arg == "name"]
                for arg in args:
                    if isinstance(arg, ast.Constant) and _is_rb(arg.value):
                        hits.append(f"{label}:{node.lineno}: {fn_name}({arg.value!r})")
    return hits


_RB_ALIAS_SOURCES = {
    "plain import": "import pyrekordbox\n",
    "import as": "import pyrekordbox as pr\n",
    "from import": "from pyrekordbox import Rekordbox6Database\n",
    "from submodule import": "from pyrekordbox.db6 import Rekordbox6Database\n",
    "import submodule": "import pyrekordbox.db\n",
    "import submodule as": "import pyrekordbox.db as rbdb\n",
    "import in a list": "import os, pyrekordbox\n",
    "importlib.import_module": 'import importlib\nrb = importlib.import_module("pyrekordbox")\n',
    "import_module submodule": 'import importlib\nrb = importlib.import_module("pyrekordbox.db")\n',
    "import_module keyword": 'import importlib\nrb = importlib.import_module(name="pyrekordbox")\n',
    "bare import_module": "from importlib import import_module\nrb = import_module('pyrekordbox')\n",
    "__import__": 'rb = __import__("pyrekordbox")\n',
    "inside a function": "def f():\n    import pyrekordbox as pr\n    return pr\n",
    "inside try": "try:\n    import pyrekordbox as pr\nexcept ImportError:\n    pr = None\n",
    "relative from": "from . import pyrekordbox\n",
}

_RB_CLEAN_SOURCES = {
    "no rekordbox": "import os\n",
    "name only in a string": 'NAME = "pyrekordbox"\n',
    "name only in a comment": "# import pyrekordbox as pr\nimport os\n",
    "lookalike module name": "import pyrekordbox_stub\nfrom pyrekordbox_stub import x\n",
    "import_module of something else": 'import importlib\nimportlib.import_module("numpy")\n',
    "dynamic name": "name = 'x'\n__import__(name)\n",
}


@pytest.mark.parametrize("label", sorted(_RB_ALIAS_SOURCES))
def test_pyrekordbox_pin_flags_every_alias_of_the_import(label):
    hits = _pyrekordbox_import_hits(_RB_ALIAS_SOURCES[label], label)
    assert hits, f"the pin missed this spelling of a pyrekordbox import: {label}"


@pytest.mark.parametrize("label", sorted(_RB_CLEAN_SOURCES))
def test_pyrekordbox_pin_does_not_flag_clean_sources(label):
    hits = _pyrekordbox_import_hits(_RB_CLEAN_SOURCES[label], label)
    assert hits == [], f"false positive on {label}: {hits}"


def _source_root() -> Path:
    return Path(__file__).resolve().parent.parent / "Source"


def test_no_module_in_source_imports_pyrekordbox_under_any_alias():
    root = _source_root()
    files = [
        p for p in sorted(root.rglob("*.py"))
        if "Archive" not in p.relative_to(root).parts
        and "__pycache__" not in p.relative_to(root).parts
    ]
    assert len(files) > 10, f"scanner saw only {len(files)} files under {root}"
    hits = []
    for p in files:
        hits.extend(_pyrekordbox_import_hits(p.read_bytes(), str(p.relative_to(root))))
    assert hits == [], "pyrekordbox imported outside Source/Archive:\n" + "\n".join(hits)


def test_pyrekordbox_pin_flags_the_real_archived_reader():
    """Positive control on a real file: the archived reader really imports
    pyrekordbox, so the scanner must flag it (proves it works on real source,
    not just on synthetic strings)."""
    archived = _source_root() / "Archive" / "rekordbox_reader.py"
    if not archived.exists():
        pytest.skip("Source/Archive/rekordbox_reader.py is gone")
    assert _pyrekordbox_import_hits(archived.read_bytes(), archived.name)
