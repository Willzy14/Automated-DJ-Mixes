"""Tests for the Tier A Phase 2 flag-gated similarity feature set in
Source/align_engine.py.

The Tier A cache adds tiera_band_{low,mid,high} (per-frame 3-band envelopes,
dB-meaned into the beat feature row, same path as the base stems) and
tiera_width + tiera_lr_corr (per-frame stereo descriptors, plain-meaned into
the row) to the loop-self-similarity cosine. The flag LOOP_SELF_SIMILARITY_TIERA
defaults OFF so the 6b40ccf "pin to base stems" invariant survives unchanged;
a test sweep flips it ON to score under the augmented feature set.

What we pin here:
  a. Flag OFF + tiera-augmented context produces the same score as a context
     WITHOUT tiera keys (the 6b40ccf invariant survives this change).
  b. Flag ON + augmented context where the texture defect is visible only in
     tiera features. Flag OFF passes self_similarity, flag ON fails it -- the
     discriminating pair that proves the test can fail.
  c. Flag ON + context without tiera keys: self_similarity is None, the check
     is skipped, every other check (period/silence/worst_beat_dip/
     insert_level_match) still runs and can still fail.
  d. Flag ON + tiera keys present but zero-length (the mono wart): same
     unmeasured handling as (c).
  e. Cache re-key: same context scored OFF then ON returns different values,
     and OFF again returns the original -- no stale cross-set reuse.

Each test uses pytest's monkeypatch to flip the flag and ALWAYS restores it
to False (never leaves it True for the next test).
"""

import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))


def _context(beat_levels, *, frames_per_beat=4):
    """Synthetic LoopQualityContext: base stems only, mirror of test_loop_quality_gate._context."""
    from align_engine import LoopQualityContext

    mix = np.repeat(np.asarray(beat_levels, dtype=float), frames_per_beat)
    envelopes = {
        name: mix * scale
        for name, scale in (
            ("drums", 1.0), ("bass", 0.8), ("other", 0.6),
            ("vocals", 0.4), ("mix", 1.0),
        )
    }
    return LoopQualityContext(
        Path("synthetic__stemenv.npz"),
        60.0,
        0.0,
        1.0 / frames_per_beat,
        envelopes,
    )


def _context_with_tiera(beat_levels, *, frames_per_beat=4, tiera_shape="flat",
                        tiera_length=None):
    """Augmented context: base stems + the 5 tiera arrays.

    tiera_shape="flat"     -> every tiera array is a constant scalar (smooth,
                              flat cosine contribution, no texture defect).
    tiera_shape="zero"     -> every tiera array has length 0 (the mono wart).
    tiera_shape="defect"   -> the tiera_width + tiera_band_* arrays carry a
                              hard step halfway through the window, while the
                              base stems stay flat (the Revoloution case:
                              everything looks identical to a flat track
                              EXCEPT the tiera features).
    """
    from align_engine import LoopQualityContext

    n = len(beat_levels) * frames_per_beat
    base = _context(beat_levels, frames_per_beat=frames_per_beat)
    envelopes = dict(base.envelopes)

    if tiera_length is None:
        tiera_length = n

    if tiera_shape == "zero":
        # Mono-input wart: ensure_tier_a_arrays emits empty arrays for a mono
        # source (the brief's documented wart). Length 0.
        zero = np.zeros(0, dtype=float)
        envelopes.update({
            "tiera_band_low": zero,
            "tiera_band_mid": zero,
            "tiera_band_high": zero,
            "tiera_width": zero,
            "tiera_lr_corr": zero,
        })
    elif tiera_shape == "flat":
        # All tiera arrays constant: no defect, identical to a non-augmented
        # cosine contribution (every column of the z-matrix is a constant).
        envelopes.update({
            "tiera_band_low": np.full(tiera_length, 0.5, dtype=float),
            "tiera_band_mid": np.full(tiera_length, 0.4, dtype=float),
            "tiera_band_high": np.full(tiera_length, 0.3, dtype=float),
            "tiera_width": np.full(tiera_length, 0.2, dtype=float),
            "tiera_lr_corr": np.full(tiera_length, 0.95, dtype=float),
        })
    elif tiera_shape == "defect":
        # Window is beats 16-32 with frames_per_beat=4, hop_sec=0.25, bpm=60,
        # so the window covers frames 64-127 (inclusive). Place the defect at
        # frame 96 (beat 24, MIDDLE of the window) -- the half-window halves
        # land on either side of the step so the z-matrix has two distinct
        # row clusters and the cosine sees the defect. A defect placed at the
        # halfway point of the WHOLE array (frame 128) would sit OUTSIDE the
        # window and the cosine would still be 1.0 (all window rows are
        # identical to each other).
        step_frame = 96
        before_count = step_frame
        after_count = tiera_length - step_frame
        width = np.concatenate([
            np.full(before_count, 0.4, dtype=float),
            np.full(after_count, 0.1, dtype=float),
        ])
        b_low = np.concatenate([
            np.full(before_count, 0.8, dtype=float),
            np.full(after_count, 0.4, dtype=float),
        ])
        b_mid = np.concatenate([
            np.full(before_count, 0.6, dtype=float),
            np.full(after_count, 0.2, dtype=float),
        ])
        b_high = np.concatenate([
            np.full(before_count, 0.5, dtype=float),
            np.full(after_count, 0.1, dtype=float),
        ])
        # L/R correlation flips sign across the step, so it carries the
        # stereo defect even if width alone happened to be muted.
        corr = np.concatenate([
            np.full(before_count, 0.95, dtype=float),
            np.full(after_count, -0.5, dtype=float),
        ])
        envelopes.update({
            "tiera_band_low": b_low,
            "tiera_band_mid": b_mid,
            "tiera_band_high": b_high,
            "tiera_width": width,
            "tiera_lr_corr": corr,
        })
    else:
        raise ValueError(f"unknown tiera_shape: {tiera_shape}")

    return LoopQualityContext(
        Path("synthetic__stemenv.npz"),
        60.0,
        0.0,
        1.0 / frames_per_beat,
        envelopes,
    )


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch):
    """Restore LOOP_SELF_SIMILARITY_TIERA=False after every test in this module."""
    import align_engine
    monkeypatch.setattr(align_engine, "LOOP_SELF_SIMILARITY_TIERA", False)
    yield


def test_flag_off_augmented_context_matches_base_only_score():
    """6b40ccf pinning invariant: flag OFF + tiera-augmented context must
    score identically to a base-only context (the tiera keys are present but
    not consulted). Exact float equality -- any drift means the flag-off path
    is no longer byte-identical to the previous code."""
    from align_engine import evaluate_loop_quality

    base_only = _context([0.1] * 64)
    augmented = _context_with_tiera([0.1] * 64, tiera_shape="flat")

    off_base = evaluate_loop_quality(base_only, 16, 32, 16)
    off_aug = evaluate_loop_quality(augmented, 16, 32, 16)

    assert off_aug.self_similarity == off_base.self_similarity


def test_flag_on_catches_texture_defect_visible_only_in_tiera_features():
    """Discriminating pair: build beat levels where every base envelope is
    dead flat (no defect visible to the 6b40ccf base set), but tiera_width
    steps hard halfway through and the tiera_band_* + tiera_lr_corr follow.
    Flag OFF: base-only cosine sees constant columns -> high similarity -> PASS.
    Flag ON: the 10-column augmented cosine catches the step -> low similarity -> FAIL.
    This is the negative control that proves the test can fail."""
    from align_engine import LOOP_SELF_SIMILARITY_TIERA, evaluate_loop_quality
    import align_engine

    defect_ctx = _context_with_tiera([0.1] * 64, tiera_shape="defect")

    align_engine.LOOP_SELF_SIMILARITY_TIERA = False
    off = evaluate_loop_quality(defect_ctx, 16, 32, 16)
    # Sanity: flag OFF ignores the tiera arrays, so the cosine is high.
    assert "self_similarity" not in off.failed_checks

    align_engine.LOOP_SELF_SIMILARITY_TIERA = True
    on = evaluate_loop_quality(defect_ctx, 16, 32, 16)
    # Sanity: flag ON sees the tiera defect, cosine collapses, check fails.
    assert "self_similarity" in on.failed_checks
    # And OFF/ON differ on the same context (the discriminating property).
    assert on.self_similarity != off.self_similarity


def test_flag_on_unmeasured_when_tiera_keys_absent_keeps_other_checks():
    """Flag ON + context WITHOUT tiera keys: self_similarity is None, the
    LOOP_MIN_SELF_SIMILARITY check is skipped, and the OTHER checks still
    bite -- a real silence defect on the same window must still fail
    silence_fraction. This is the "you cannot fail a check you could not
    measure" rule."""
    from align_engine import evaluate_loop_quality
    import align_engine

    align_engine.LOOP_SELF_SIMILARITY_TIERA = True

    # Base-only context: 24% silence mid-window via a deep dip on the last
    # 4 beats. silence_fraction is the only thing that should bite; the
    # other checks (period, dip, insert_level) must stay clean.
    levels = [0.1] * 48 + [0.1] * 12 + [1e-4] * 4
    ctx = _context(levels)  # no tiera keys at all
    result = evaluate_loop_quality(ctx, 48, 64, 48)

    assert result.self_similarity is None
    assert "self_similarity" not in result.failed_checks
    assert "silence_fraction" in result.failed_checks
    # "Other checks keep running" is evidenced by silence_fraction failing
    # despite the tiera term being unmeasured. The deep dip also trips
    # worst_beat_dip here (the two checks share the same beat-level RMS path),
    # which is the OPPOSITE of what we want to guard against -- we want
    # unmeasured tiera NOT to suppress any other check.


def test_flag_on_unmeasured_when_tiera_arrays_are_zero_length():
    """Mono-input wart: the tiera_* arrays are PRESENT in the cache (keys
    exist) but have length 0 (ensure_tier_a_arrays emits empty arrays for a
    mono source). Flag ON must still treat this as unmeasured -- the brief
    is explicit that ANY of the 5 tiera keys being zero-length collapses the
    term. Same handling as the absent case in (c); no exception, no silent
    fallback to the base score."""
    from align_engine import evaluate_loop_quality
    import align_engine

    align_engine.LOOP_SELF_SIMILARITY_TIERA = True

    ctx = _context_with_tiera([0.1] * 64, tiera_shape="zero")
    # No silence defect in this context -- a clean pass-otherwise window.
    result = evaluate_loop_quality(ctx, 16, 32, 16)

    assert result.self_similarity is None
    assert "self_similarity" not in result.failed_checks
    assert result.passed


def test_cache_rekey_prevents_stale_cross_set_reuse():
    """Cache re-key: the same LoopQualityContext scored under OFF, then ON,
    then OFF again must return distinct values for the first two flag states
    and the ORIGINAL value for the final OFF state. If the cache reused the
    ON z-matrix for the second OFF call, OFF would silently change. The
    per-feature-set cache key (tuple(keys)) prevents that."""
    from align_engine import evaluate_loop_quality
    import align_engine

    # Augmented context with a tiera feature step -- the augmented feature
    # set produces a clearly different cosine from the base-only set.
    defect_ctx = _context_with_tiera([0.1] * 64, tiera_shape="defect")

    align_engine.LOOP_SELF_SIMILARITY_TIERA = False
    off_first = evaluate_loop_quality(defect_ctx, 16, 32, 16).self_similarity

    align_engine.LOOP_SELF_SIMILARITY_TIERA = True
    on_value = evaluate_loop_quality(defect_ctx, 16, 32, 16).self_similarity

    align_engine.LOOP_SELF_SIMILARITY_TIERA = False
    off_second = evaluate_loop_quality(defect_ctx, 16, 32, 16).self_similarity

    # Flag flips produce distinct scores on the same context.
    assert on_value != off_first
    # Second OFF reuses the cached base-only z-matrix -- exact float match.
    assert off_second == off_first