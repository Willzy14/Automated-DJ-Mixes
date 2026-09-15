"""Tests for burn list D7: Source/alignment_feasibility.py's `feasible()`.

Previously `feasible()` only tested whether `align_pair`/
`_align_pair_landmark_aware` succeeded - a pair that aligns but then fails
the loop/cut planning stage (`plan_fill_or_cut`) read as "feasible" when it
was not actually usable. `feasible()` now requires BOTH stages to succeed.

Honest limitation, documented in the function's own docstring and in
Documentation/BURN_LIST.md item D7: this closes the "plan_fill_or_cut
raises" failure mode, which is real, but direct re-testing of the item's
own cited real-world example (Doorly & Harry Choo Choo Romero -> Christoph
- The Rise, from a claimed "2 of 267" corpus finding) found that pair's
`plan_fill_or_cut` does NOT raise today, and a full corpus re-run (all 380
ordered pairs in the 14.08.26 baseline) found ZERO pairs whose verdict
changes with this fix - the original finding's real mechanism is not
reproduced by this check as scoped. These tests therefore verify the
WIRING (feasible() correctly requires both stages, and correctly returns
False the moment either one raises) via monkeypatching, since no real
geometric fixture in this project's existing corpora is currently known to
make plan_fill_or_cut itself raise.
"""
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import align_engine as AE  # noqa: E402
import alignment_feasibility as AF  # noqa: E402


def _track(name, n_bars=64):
    return AE.Track(
        name=name, bpm=128.0, spb=4 * 60.0 / 128.0, downbeat=0.0,
        n_bars=n_bars, sections=[
            {"name": "drop_1", "label": "drop", "start_bar": 0.0, "end_bar": n_bars - 16.0},
            {"name": "outro_1", "label": "outro", "start_bar": n_bars - 16.0, "end_bar": float(n_bars)},
        ],
        bass_in_bar=0.0, bass_out_bar=None, last_min_bars=32,
    )


def test_feasible_true_when_both_stages_succeed(monkeypatch):
    o, i = _track("Out"), _track("In")
    dummy_alignment = object()
    monkeypatch.setattr(AF, "_align_pair_landmark_aware",
                        lambda outgoing, incoming, policy: dummy_alignment)
    monkeypatch.setattr(AF, "plan_fill_or_cut",
                        lambda outgoing, incoming, al, policy: [])

    assert AF.feasible(o, i, AE._DEFAULT_POLICY) is True


def test_feasible_false_when_alignment_itself_fails(monkeypatch):
    """Unchanged pre-existing behaviour: an alignment failure alone (never
    reaching plan_fill_or_cut at all) must still read as infeasible."""
    o, i = _track("Out"), _track("In")

    def raises(*a, **k):
        raise ValueError("no feasible overlap")

    monkeypatch.setattr(AF, "_align_pair_landmark_aware", raises)
    plan_called = []
    monkeypatch.setattr(AF, "plan_fill_or_cut",
                        lambda *a, **k: plan_called.append(1))

    assert AF.feasible(o, i, AE._DEFAULT_POLICY) is False
    assert plan_called == [], "plan_fill_or_cut must not run after alignment itself failed"


def test_feasible_false_when_alignment_succeeds_but_planning_raises(monkeypatch):
    """The actual fix: a pair that aligns cleanly but whose loop/cut
    planning stage raises must now read as infeasible - this is exactly
    the class of gap burn list D7 names (a "feasible" pair that fails one
    stage later)."""
    o, i = _track("Out"), _track("In")
    dummy_alignment = object()
    monkeypatch.setattr(AF, "_align_pair_landmark_aware",
                        lambda outgoing, incoming, policy: dummy_alignment)

    def raises(*a, **k):
        raise RuntimeError("no clean loop region available")

    monkeypatch.setattr(AF, "plan_fill_or_cut", raises)

    assert AF.feasible(o, i, AE._DEFAULT_POLICY) is False


def test_real_14_08_26_corpus_feasibility_unchanged_by_this_fix():
    """Real-data regression, not just mocks: on the actual 14.08.26 corpus
    (the same one burn list D7's own citation is drawn from), this fix
    changes ZERO of the 380 ordered pairs' feasibility verdicts - pinned so
    a future change to plan_fill_or_cut that DOES start raising for a real
    pair is visible as a deliberate change to this count, not a silent
    drift. 267 is the same "align, not raise" figure
    Tests/test_alignment_baseline.py's own captured baseline uses."""
    stem_dir = ROOT / "Test Project" / "14.08.26" / "_Stem Analysis"
    if not list(stem_dir.glob("SECTIONS_STEM_*.json")):
        pytest.skip("14.08.26 stem JSONs unavailable")

    tracks = AF.load_tracks(stem_dir.parent)
    policy = AE._DEFAULT_POLICY
    n_feasible = sum(
        1
        for a, (ta, _) in enumerate(tracks)
        for b, (tb, _) in enumerate(tracks)
        if a != b and AF.feasible(ta, tb, policy)
    )
    assert n_feasible == 267
