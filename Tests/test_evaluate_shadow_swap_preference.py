"""Tests for burn list C7 Step 1's held-out evaluation:
Source/evaluate_shadow_swap_preference.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

from canonicalize_pair_history import CanonicalPair, load_records, canonicalize  # noqa: E402
from evaluate_shadow_swap_preference import evaluate, summarise  # noqa: E402

REAL_PAIR_HISTORY = ROOT / "Documentation" / "Mix Patterns Library" / "pair_history.jsonl"


def _canonical(project, pair_index, delta, bpm=128.0,
              out_structure=("intro_1", "drop_1"), in_structure=("drop_1",)):
    return CanonicalPair(
        project=project, pair_index=pair_index, delta_beats=delta,
        verdict="corrected" if delta else "correct", bpm_out=bpm, bpm_in=bpm,
        out_structure=tuple(out_structure), in_structure=tuple(in_structure),
        source="corpus", n_records=1,
    )


def test_evaluate_never_lets_a_pair_predict_itself():
    # Two projects, IDENTICAL structure/BPM so similarity would be perfect -
    # if project A's own pair leaked into its own prediction, the shadow
    # delta would exactly equal A's true delta every time. It must not.
    a1 = _canonical("A", 1, delta=10.0)
    a2 = _canonical("A", 2, delta=10.0)  # same project, same shape as a1
    b1 = _canonical("B", 1, delta=40.0)  # different project, same shape
    rows = evaluate([a1, a2, b1])
    row_a1 = next(r for r in rows if r.project == "A" and r.pair_index == 1)
    # a1's only possible source is B (a2 is same project, excluded too) -
    # so the shadow prediction must come from B's delta (40), not A's own.
    assert row_a1.shadow_delta == 40.0


def test_evaluate_reports_no_prediction_as_none_not_a_fabricated_zero():
    lonely = _canonical("Solo", 1, delta=99.0)
    rows = evaluate([lonely])
    assert rows[0].shadow_delta is None
    assert rows[0].shadow_within_tolerance is None
    # The baseline ("always predict 0") still applies even with no shadow
    # coverage - it's a separate, always-available comparison point.
    assert rows[0].baseline_within_tolerance is False  # |0 - 99| > tolerance


def test_evaluate_within_tolerance_flag_is_hand_verified():
    a = _canonical("A", 1, delta=10.0)
    b = _canonical("B", 1, delta=12.0)  # within DELTA_TOLERANCE_BEATS (4.0) of a shadow ~ close
    rows = evaluate([a, b])
    row_a = next(r for r in rows if r.project == "A")
    # a's shadow prediction is entirely from B (delta=12); |12-10|=2 <= 4.0
    assert row_a.shadow_delta == 12.0
    assert row_a.shadow_within_tolerance is True


def test_summarise_hit_rates_and_baseline_comparison():
    from evaluate_shadow_swap_preference import EvalRow
    rows = [
        EvalRow("A", 1, true_delta=10.0, shadow_delta=10.0, shadow_confidence=0.9,
                shadow_within_tolerance=True, baseline_within_tolerance=False),
        EvalRow("A", 2, true_delta=0.0, shadow_delta=2.0, shadow_confidence=0.5,
                shadow_within_tolerance=True, baseline_within_tolerance=True),
        EvalRow("B", 1, true_delta=50.0, shadow_delta=None, shadow_confidence=None,
                shadow_within_tolerance=None, baseline_within_tolerance=False),
    ]
    s = summarise(rows)
    assert s["total_pairs"] == 3
    assert s["covered_pairs"] == 2
    assert s["uncovered_pairs"] == 1
    assert s["shadow_hit_rate_on_covered"] == pytest.approx(1.0)   # 2/2
    assert s["baseline_hit_rate_on_covered"] == pytest.approx(0.5)  # 1/2
    assert s["shadow_beats_baseline_on_covered"] == 1
    assert s["baseline_hit_rate_overall"] == pytest.approx(1 / 3)
    assert s["by_project"]["A"] == {"total": 2, "covered": 2, "shadow_hits": 2}
    assert s["by_project"]["B"] == {"total": 1, "covered": 0, "shadow_hits": 0}


def test_summarise_handles_zero_covered_pairs_without_dividing_by_zero():
    from evaluate_shadow_swap_preference import EvalRow
    rows = [EvalRow("A", 1, true_delta=5.0, shadow_delta=None, shadow_confidence=None,
                    shadow_within_tolerance=None, baseline_within_tolerance=False)]
    s = summarise(rows)
    assert s["covered_pairs"] == 0
    assert s["shadow_hit_rate_on_covered"] is None
    assert s["baseline_hit_rate_on_covered"] is None


@pytest.mark.skipif(not REAL_PAIR_HISTORY.exists(),
                    reason="real pair_history.jsonl unavailable")
def test_real_corpus_evaluation_runs_and_every_row_is_internally_consistent():
    """Not a pinned pass-rate (too fragile as the corpus grows) - a
    structural check that the real evaluation runs end to end and every
    row's own fields agree with each other, the same discipline
    test_canonicalize_pair_history.py's real-corpus test uses for shape
    rather than for one specific number that will drift."""
    records, _malformed = load_records(REAL_PAIR_HISTORY)
    canonical_pairs = list(canonicalize(records).canonical)
    assert len(canonical_pairs) > 10  # sanity: this IS the real, non-trivial corpus

    rows = evaluate(canonical_pairs)
    assert len(rows) == len(canonical_pairs)

    for r in rows:
        if r.shadow_delta is None:
            assert r.shadow_within_tolerance is None
            assert r.shadow_confidence is None
        else:
            assert r.shadow_within_tolerance is (
                abs(r.shadow_delta - r.true_delta) <= 4.0)
        assert r.baseline_within_tolerance is (abs(r.true_delta) <= 4.0)

    s = summarise(rows)
    assert s["total_pairs"] == len(canonical_pairs)
    assert sum(v["total"] for v in s["by_project"].values()) == len(canonical_pairs)
