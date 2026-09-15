"""Tests for the Tier A Phase 2 flag-gated similarity feature set in
Source/align_engine.py.

The Tier A cache adds tiera_band_{low,mid,high} (per-frame 3-band envelopes,
dB-meaned into the beat feature row, same path as the base stems) and
tiera_width + tiera_lr_corr (per-frame stereo descriptors, plain-meaned into
the row) to the loop-self-similarity cosine. LOOP_SELF_SIMILARITY_TIERA
defaults ON (since 2026-09-15, after the AND-semantics rebuild below was
verified). Every test in this module still pins the flag OFF around itself
(the `_flag_off` fixture) and flips it ON explicitly where the flag's own
behaviour is under test, so the 6b40ccf "pin to base stems" invariant for the
OFF state stays covered regardless of the flag's current default.

AND semantics (2026-09-15 rebuild): when the flag is ON, evaluate_loop_quality
computes TWO separate self-similarity terms -- the base 5-key score (always,
identical to the flag-off call) and the tiera-augmented 10-key score -- and
fails the self_similarity check if EITHER measured term is below
LOOP_MIN_SELF_SIMILARITY. This replaces the original design, which computed
ONE blended score and let it REPLACE the base score outright when the flag
was on. The corpus replay that first measured the original design (719be92,
15,268 windows) found the blended score flipped 1,262 verdicts: 791 genuine
new catches, but also 404 evidence-carrying UN-catches -- real bad loops the
base score correctly failed that the blended score let pass, because a
strong tiera signal could outvote a genuine base-score failure. AND semantics
makes that class of regression structurally impossible: the base term is
always computed and always gates on its own, so the flag can only ever ADD a
self_similarity failure, never remove one.

What we pin here:
  a. Flag OFF + tiera-augmented context produces the same score as a context
     WITHOUT tiera keys (the 6b40ccf invariant survives this change).
  b. The base term (self_similarity) is IDENTICAL whether the flag is off or
     on -- the flag never changes what the base term measures or whether it
     can fail on its own.
  c. AND-gate logic, pinned directly via monkeypatch so it does not depend on
     any specific audio fixture: a base-term failure survives even when the
     tiera-augmented term would pass on its own (the exact shape of the
     historical 404-un-catch bug), and a tiera-term failure adds a catch the
     base term alone would have missed.
  d. Flag ON + a real audio-shaped defect visible only in tiera features:
     self_similarity_tiera drops below threshold and "self_similarity" lands
     in failed_checks, while self_similarity (base) is unaffected.
  e. Flag ON + context without tiera keys, or with zero-length tiera arrays
     (the mono wart): self_similarity_tiera is None, the tiera term is
     skipped ("cannot fail a check you could not measure"), and the base
     term + every other check (period/silence/worst_beat_dip/
     insert_level_match) still runs and can still fail.
  f. Cache re-key at the _loop_self_similarity level (unchanged by this
     rebuild -- evaluate_loop_quality now calls it twice per window when the
     flag is on, once per feature set, so this invariant matters more than
     ever): base-only and tiera-augmented calls on the same context return
     distinct, correctly-cached values with no stale cross-set reuse.

Each test that flips the flag uses pytest's monkeypatch, which always
restores it to False after the test.
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
    assert off_aug.self_similarity_tiera is None
    assert off_base.self_similarity_tiera is None


def test_base_term_is_identical_regardless_of_flag_state():
    """AND semantics core invariant: the base term never changes when the
    flag flips. Uses the defect context (a real texture change visible only
    in tiera columns) precisely because under the OLD replacement design
    flag ON would have produced a DIFFERENT self_similarity value here (the
    blended 10-key score) -- this is the discriminating case that proves the
    base term is now genuinely independent of the flag."""
    from align_engine import evaluate_loop_quality
    import align_engine

    defect_ctx = _context_with_tiera([0.1] * 64, tiera_shape="defect")

    align_engine.LOOP_SELF_SIMILARITY_TIERA = False
    off = evaluate_loop_quality(defect_ctx, 16, 32, 16)

    align_engine.LOOP_SELF_SIMILARITY_TIERA = True
    on = evaluate_loop_quality(defect_ctx, 16, 32, 16)

    assert on.self_similarity == off.self_similarity


def test_and_gate_base_failure_survives_a_passing_tiera_term(monkeypatch):
    """The core guarantee this rebuild exists for, pinned directly against
    the gate logic via monkeypatch (no dependency on any specific audio
    fixture managing to reproduce the dilution effect): a base-term failure
    must survive even when the tiera-augmented term independently passes.
    This is exactly the shape of the historical bug (719be92's 404
    evidence-carrying un-catches) -- under the OLD single-blended-score
    design, a healthy tiera-augmented score could outvote a genuine base
    failure outright. Proved-the-test: reverting evaluate_loop_quality to
    call `_loop_self_similarity(context, s, e, use_tiera=LOOP_SELF_SIMILARITY_TIERA)`
    once (the pre-rebuild single-term gate) makes this test fail, because
    with the flag on it would only ever see the healthy 0.90 tiera value and
    never gate on the failing 0.40 base value at all."""
    import align_engine
    from align_engine import evaluate_loop_quality

    ctx = _context([0.1] * 64)

    def fake_selfsim(context, s, e, use_tiera=False):
        return 0.40 if not use_tiera else 0.90

    monkeypatch.setattr(align_engine, "_loop_self_similarity", fake_selfsim)
    monkeypatch.setattr(align_engine, "LOOP_SELF_SIMILARITY_TIERA", True)

    result = evaluate_loop_quality(ctx, 16, 32, 16)

    assert result.self_similarity == 0.40
    assert result.self_similarity_tiera == 0.90
    assert "self_similarity" in result.failed_checks


def test_and_gate_tiera_failure_adds_a_catch_base_alone_would_miss(monkeypatch):
    """The other half of AND semantics: a failing tiera term must ADD a
    self_similarity failure even when the base term independently passes --
    this is the "791 genuine new catches" side of the original evidence,
    which the rebuild must preserve, not just the un-catch fix."""
    import align_engine
    from align_engine import evaluate_loop_quality

    ctx = _context([0.1] * 64)

    def fake_selfsim(context, s, e, use_tiera=False):
        return 0.90 if not use_tiera else 0.40

    monkeypatch.setattr(align_engine, "_loop_self_similarity", fake_selfsim)
    monkeypatch.setattr(align_engine, "LOOP_SELF_SIMILARITY_TIERA", True)

    result = evaluate_loop_quality(ctx, 16, 32, 16)

    assert result.self_similarity == 0.90
    assert result.self_similarity_tiera == 0.40
    assert "self_similarity" in result.failed_checks


def test_and_gate_both_terms_passing_is_a_clean_pass(monkeypatch):
    """Control case: both terms healthy -> no self_similarity failure."""
    import align_engine
    from align_engine import evaluate_loop_quality

    ctx = _context([0.1] * 64)

    def fake_selfsim(context, s, e, use_tiera=False):
        return 0.90

    monkeypatch.setattr(align_engine, "_loop_self_similarity", fake_selfsim)
    monkeypatch.setattr(align_engine, "LOOP_SELF_SIMILARITY_TIERA", True)

    result = evaluate_loop_quality(ctx, 16, 32, 16)

    assert "self_similarity" not in result.failed_checks


def test_flag_on_catches_texture_defect_visible_only_in_tiera_features():
    """Real (non-mocked) audio-shaped case: build beat levels where every
    base envelope is dead flat (no defect visible to the 6b40ccf base set),
    but tiera_width steps hard halfway through and the tiera_band_* +
    tiera_lr_corr follow. Flag OFF: self_similarity_tiera is never computed,
    self_similarity (base) passes. Flag ON: self_similarity_tiera drops below
    threshold and self_similarity (base) is untouched -- the ADD-a-catch
    case with real, non-mocked envelope data."""
    from align_engine import evaluate_loop_quality
    import align_engine

    defect_ctx = _context_with_tiera([0.1] * 64, tiera_shape="defect")

    align_engine.LOOP_SELF_SIMILARITY_TIERA = False
    off = evaluate_loop_quality(defect_ctx, 16, 32, 16)
    assert "self_similarity" not in off.failed_checks
    assert off.self_similarity_tiera is None

    align_engine.LOOP_SELF_SIMILARITY_TIERA = True
    on = evaluate_loop_quality(defect_ctx, 16, 32, 16)
    assert "self_similarity" in on.failed_checks
    assert on.self_similarity_tiera is not None
    assert on.self_similarity_tiera < align_engine.LOOP_MIN_SELF_SIMILARITY
    # The base term is untouched by the flag flip -- the defect is caught by
    # the ADDITIONAL tiera term, not by any change to the base measurement.
    assert on.self_similarity == off.self_similarity


def test_flag_on_unmeasured_when_tiera_keys_absent_keeps_other_checks():
    """Flag ON + context WITHOUT tiera keys: self_similarity_tiera is None,
    the tiera half of the check is skipped, and the OTHER (independent)
    checks still bite. Deliberately isolates insert_level_match rather than
    silence/dip: the window itself is perfectly flat (self-similarity = 1.0,
    a clean base-term pass) while only the beat ADJACENT to the insert point
    is loud, so this fixture exercises "tiera unmeasured does not block an
    unrelated check" without also tripping self_similarity on its own --
    proving genuine independence between the checks rather than two checks
    both firing on the same underlying texture change."""
    from align_engine import evaluate_loop_quality
    import align_engine

    align_engine.LOOP_SELF_SIMILARITY_TIERA = True

    # Beat 15 (the insert-adjacent beat) is loud; beats 16-63 (including the
    # whole 16-32 window under test) are flat and quiet.
    levels = [0.1] * 15 + [1.0] + [0.1] * 48
    ctx = _context(levels)  # no tiera keys at all
    result = evaluate_loop_quality(ctx, 16, 32, 16)

    assert result.self_similarity is not None
    assert result.self_similarity_tiera is None
    assert "self_similarity" not in result.failed_checks
    assert "insert_level_match" in result.failed_checks
    # "Other checks keep running" is evidenced by insert_level_match failing
    # despite the tiera term being unmeasured, while self_similarity (base)
    # independently stays a clean pass -- unmeasured tiera does not suppress
    # any other check, and does not get conflated with the base term either.


def test_flag_on_unmeasured_when_tiera_arrays_are_zero_length():
    """Mono-input wart: the tiera_* arrays are PRESENT in the cache (keys
    exist) but have length 0 (ensure_tier_a_arrays emits empty arrays for a
    mono source). Flag ON must still treat this as unmeasured -- the brief
    is explicit that ANY of the 5 tiera keys being zero-length collapses the
    term. Same handling as the absent case above; no exception, no silent
    fallback that turns the tiera term into a duplicate of the base term."""
    from align_engine import evaluate_loop_quality
    import align_engine

    align_engine.LOOP_SELF_SIMILARITY_TIERA = True

    ctx = _context_with_tiera([0.1] * 64, tiera_shape="zero")
    # No silence defect in this context -- a clean pass-otherwise window.
    result = evaluate_loop_quality(ctx, 16, 32, 16)

    assert result.self_similarity is not None
    assert result.self_similarity_tiera is None
    assert "self_similarity" not in result.failed_checks
    assert result.passed


def test_cache_rekey_prevents_stale_cross_set_reuse():
    """Cache re-key at the _loop_self_similarity level (unchanged by the AND-
    semantics rebuild -- evaluate_loop_quality now calls this function twice
    per window when the flag is on, once per feature set, so this invariant
    matters even more than before): base-only, then tiera-augmented, then
    base-only again on the SAME context must return distinct values for the
    first two calls and the ORIGINAL value for the final base-only call. If
    the cache reused the tiera z-matrix for the second base-only call, that
    call would silently change. The per-feature-set cache key (tuple(keys))
    prevents that."""
    from align_engine import _loop_self_similarity

    # Augmented context with a tiera feature step -- the augmented feature
    # set produces a clearly different cosine from the base-only set.
    defect_ctx = _context_with_tiera([0.1] * 64, tiera_shape="defect")

    off_first = _loop_self_similarity(defect_ctx, 16, 32, use_tiera=False)
    on_value = _loop_self_similarity(defect_ctx, 16, 32, use_tiera=True)
    off_second = _loop_self_similarity(defect_ctx, 16, 32, use_tiera=False)

    # Feature-set flips produce distinct scores on the same context.
    assert on_value != off_first
    # Second base-only call reuses the cached base-only z-matrix -- exact
    # float match.
    assert off_second == off_first
