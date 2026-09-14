"""build_ab_comparison.py's SIDES wiring.

Found in Codex's review of the SAM_V2 candidate plan (2026-09-10): the
comparison harness passed only --transition-policy to each subprocess, so
CUE_CONFIG.incoming_intro_loop could never be enabled by ANY side it built -
the flag existed, was directly evidenced (Fresh Mix V2), and this harness
would have silently tested nothing about it. Pins that side C actually
carries --cue-signals introloop through to the propose_arrangement.py
subprocess command, and that sides A/B are unaffected.

Mocks build_ab_comparison.run() rather than spawning real subprocesses -
this pins the COMMAND CONSTRUCTION, not a full pipeline run (which belongs
in a real project fixture, out of scope here).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import build_ab_comparison as BAC  # noqa: E402


def test_sides_includes_the_sam_v2_combination():
    labels = {(side, policy, cue) for side, policy, cue in BAC.SIDES}
    assert ("C", "sam_v1", "introloop") in labels
    assert ("A", "interim_v1", "") in labels
    assert ("B", "sam_v1", "") in labels


def test_cue_signals_only_passed_to_the_subprocess_when_set(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, log):
        calls.append(cmd)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("", encoding="utf-8")
        # Pretend both stages of every side succeeded.
        return 0, ""

    monkeypatch.setattr(BAC, "run", fake_run)
    # This test is about subprocess COMMAND CONSTRUCTION (per the module
    # docstring), not MixPlan reconciliation (burn list A4, 2026-09-14) -
    # the fake run() never creates real MixPlan/report/ALS files, so a real
    # reconcile() call would correctly fail closed on missing files. Stub
    # it out, matching the same "mock the sibling step, pin only what this
    # test is actually about" approach already used for run().
    monkeypatch.setattr(BAC, "reconcile",
                        lambda plan, report, als: {"checks": ["stubbed"]})

    project = tmp_path / "Proj"
    sections_als = tmp_path / "Sections.als"
    sections_json = tmp_path / "Sections.json"
    sections_als.write_bytes(b"")
    sections_json.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "build_ab_comparison.py", str(project), str(sections_als), str(sections_json),
    ])
    rc = BAC.main()
    assert rc == 0

    arrange_calls = [c for c in calls if "propose_arrangement.py" in c[1]]
    assert len(arrange_calls) == len(BAC.SIDES)

    # Exactly one side (C) should carry --cue-signals through to the
    # subprocess command - A and B must never see it.
    cue_signal_calls = [c for c in arrange_calls if "--cue-signals" in c]
    assert len(cue_signal_calls) == 1
    idx = cue_signal_calls[0].index("--cue-signals")
    assert cue_signal_calls[0][idx + 1] == "introloop"
    policy_idx = cue_signal_calls[0].index("--transition-policy")
    assert cue_signal_calls[0][policy_idx + 1] == "sam_v1"


def test_reconciliation_failure_fails_that_side_not_the_whole_build(tmp_path, monkeypatch):
    """Burn list A4 (2026-09-14): MixPlan reconciliation is now a real gate,
    called in-process after each side's automation stage succeeds. A side
    whose MixPlan does not reconcile against its ALS must be marked not-ok
    (stage="reconcile") and the overall exit code must reflect it - "the
    subprocesses succeeded" is no longer sufficient for main() to return 0."""
    def fake_run(cmd, log):
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("", encoding="utf-8")
        return 0, ""

    monkeypatch.setattr(BAC, "run", fake_run)

    def fake_reconcile(plan, report, als):
        raise ValueError("Project tempo does not match MixPlan nan")

    monkeypatch.setattr(BAC, "reconcile", fake_reconcile)

    project = tmp_path / "Proj"
    sections_als = tmp_path / "Sections.als"
    sections_json = tmp_path / "Sections.json"
    sections_als.write_bytes(b"")
    sections_json.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "build_ab_comparison.py", str(project), str(sections_als), str(sections_json),
    ])
    rc = BAC.main()
    assert rc == 1, "a reconciliation failure on every side must not report success"

    results = json.loads((project / "Output" / "AB" / "_audit" / "build_results.json")
                         .read_text(encoding="utf-8"))
    for side in ("A", "B", "C"):
        assert results[side]["ok"] is False
        assert results[side]["stage"] == "reconcile"
        assert "Project tempo" in results[side]["reconciliation_error"]
