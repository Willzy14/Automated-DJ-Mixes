"""Regression tests for the 2026-08-20 Codex ROUND-2 adversarial-review BLOCKERs.

BLOCKER 1 - aligner clamp + non-transactional report mutation
  (a) plan_transitions' overlap clamp is a genuine safety bound (the swap
      must sit inside the ARRANGED overlap with fade room after it), but an
      aligner-approved swap satisfies that BY CONSTRUCTION - so a clamp
      trigger on an aligner-policy swap means the report and the arranged
      ALS disagree, and moving the beat silently would break the landmark
      contract while KEEPING its provenance. Aligner swaps therefore ship
      unmoved or HARD-ERROR; legacy swaps still clamp, now with visible
      provenance (swap_transforms) instead of a silent move.
  (b) The final-swap write-back to the arrangement report happens only
      AFTER the output ALS is written and validated: a failed build must
      leave the report byte-identical (it must never describe an attempt
      that never shipped). The write itself is atomic (temp + os.replace).

BLOCKER 3 - rescue transitions and reconcile's clip-boundary proof
  validate_mix_plan_als.reconcile compared alignment_policy against the
  literal "paired_landmarks_v2", so tail_anchor_rescue_v1 transitions
  silently skipped the clip-boundary assertions that exposed V9. Now every
  policy in align_engine.LANDMARK_POLICIES is handled explicitly:
  paired asserts BOTH roles; rescue asserts the INCOMING role (the rescue
  places the incoming anchor - first drop, or grid head - exactly on the
  swap with the same landmark entry machinery as paired) and EXEMPTS the
  outgoing role visibly via a named check entry, because the rescue's
  outgoing anchor is grid-derived (n_bars - 16 run-back), deliberately
  mid-clip, and no arranger machinery splits a clip there today (V10
  ground truth: even paired outgoing boundaries exist only where the swap
  coincides with a loop boundary - 5 of 11 V10 swaps have none; the D5
  split-in-the-arranger build is what will make the outgoing side
  assertable for every landmark policy).

Style mirrors Tests/test_codex_blocker_fixes.py (the round-1 batch).
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import apply_automation  # noqa: E402
from apply_automation import (  # noqa: E402
    TrackInfo,
    _load_arrangement_report,
    _normalise,
    plan_transitions,
    write_final_swaps_to_report,
)


# --- BLOCKER 1a: the clamp respects the aligner ------------------------------

def _sec(name, label, start, end):
    return {"name": name, "label": label, "arr_time": start, "arr_end": end}


def _tight_pair():
    """Overlap 560..605 -> safe clamp bound ov_end - 8 = 597. Codex's
    reproduction: an aligner-approved swap at 600 was moved to 597 while
    keeping its paired/landmark provenance."""
    out_t = TrackInfo("OutT", [_sec("drop_2", "drop", 400.0, 500.0),
                               _sec("outro_1", "outro", 500.0, 605.0)],
                      0.0, 605.0)
    in_t = TrackInfo("InT", [], 560.0, 1200.0)
    return out_t, in_t


def _swaps_for(out_t, in_t, beat, policy, kind):
    return {(_normalise(out_t.name), _normalise(in_t.name)): {
        "swap_beats": beat,
        "handoff_kind": kind,
        "alignment_policy": policy,
    }}


def test_aligner_swap_in_end_margin_is_hard_error():
    """Codex round-2 reproduction (600 -> 597): the aligner-approved swap
    falls inside the 8-beat end margin. It must ERROR, never move - a
    clamp trigger on an aligner swap means report and ALS disagree."""
    out_t, in_t = _tight_pair()
    swaps = _swaps_for(out_t, in_t, 600.0, "paired_landmarks_v2",
                       "paired/landmark:kick_dropout:end->drop")
    with pytest.raises(ValueError, match="outside the safe overlap"):
        plan_transitions([out_t, in_t], swaps)


def test_rescue_swap_in_end_margin_is_hard_error():
    out_t, in_t = _tight_pair()
    swaps = _swaps_for(out_t, in_t, 600.0, "tail_anchor_rescue_v1",
                       "rescue/grid_tail->drop")
    with pytest.raises(ValueError, match="outside the safe overlap"):
        plan_transitions([out_t, in_t], swaps)


def test_aligner_swap_before_overlap_is_hard_error():
    out_t, in_t = _tight_pair()
    swaps = _swaps_for(out_t, in_t, 550.0, "paired_landmarks_v2",
                       "paired/section:drop:end->drop")
    with pytest.raises(ValueError, match="outside the safe overlap"):
        plan_transitions([out_t, in_t], swaps)


def test_aligner_swap_at_bound_ships_unmoved():
    """Exactly ON the clamp bound is inside the safe overlap - no error,
    no move, no transform provenance."""
    out_t, in_t = _tight_pair()
    swaps = _swaps_for(out_t, in_t, 597.0, "paired_landmarks_v2",
                       "paired/section:drop:end->drop")
    plans = plan_transitions([out_t, in_t], swaps)
    assert plans[0].bass_swap == 597.0
    assert plans[0].swap_transforms == []


def test_legacy_swap_still_clamps_with_visible_provenance():
    """The same geometry under the legacy policy label still clamps
    (600 -> 597) - the safety bound is for heuristic/hand-made swaps -
    and the move is recorded on the plan, never silent."""
    out_t, in_t = _tight_pair()
    swaps = _swaps_for(out_t, in_t, 600.0, "legacy_v1", "drop->outro")
    plans = plan_transitions([out_t, in_t], swaps)
    assert plans[0].bass_swap == 597.0
    assert "clamped" in plans[0].swap_transforms


def test_clamp_provenance_written_to_report(tmp_path):
    out_t, in_t = _tight_pair()
    report = tmp_path / "mix_ARRANGEMENT_REPORT.json"
    report.write_text(json.dumps({"transitions": [
        {"out_track": "OutT", "in_track": "InT", "swap_beats": 600.0,
         "handoff_kind": "drop->outro", "alignment_policy": "legacy_v1"},
    ]}), encoding="utf-8")
    swaps = _load_arrangement_report(tmp_path / "mix.als", report)
    plans = plan_transitions([out_t, in_t], swaps)
    write_final_swaps_to_report(report, plans)
    tr = json.loads(report.read_text(encoding="utf-8"))["transitions"][0]
    assert tr["swap_beats"] == 597.0
    assert tr["swap_beats_pre_automation"] == 600.0
    assert tr["swap_transforms"] == ["clamped"]


def test_snap_provenance_written_to_report(tmp_path):
    """A snapped legacy swap carries 'snapped' in the report - every
    transform that moves a beat is visible provenance."""
    out_t = TrackInfo("OutT", [_sec("drop_1", "drop", 400.0, 603.0),
                               _sec("outro_1", "outro", 603.0, 700.0)],
                      0.0, 700.0)
    in_t = TrackInfo("InT", [], 560.0, 1200.0)
    report = tmp_path / "mix_ARRANGEMENT_REPORT.json"
    report.write_text(json.dumps({"transitions": [
        {"out_track": "OutT", "in_track": "InT", "swap_beats": 600.0,
         "handoff_kind": "drop->outro", "alignment_policy": "legacy_v1"},
    ]}), encoding="utf-8")
    swaps = _load_arrangement_report(tmp_path / "mix.als", report)
    plans = plan_transitions([out_t, in_t], swaps)
    assert plans[0].bass_swap == 603.0  # snapped onto the outro edge
    write_final_swaps_to_report(report, plans)
    tr = json.loads(report.read_text(encoding="utf-8"))["transitions"][0]
    assert tr["swap_beats"] == 603.0
    assert tr["swap_beats_pre_automation"] == 600.0
    assert tr["swap_transforms"] == ["snapped"]


# --- BLOCKER 1b: transactional report write-back -----------------------------

def _write_sections_als(path, devices=True):
    """Two overlapping labelled tracks in the line format apply_automation
    expects; optionally with resolvable StereoGain/ChannelEq automation
    targets. Overlap 500..800 -> clamp bound 792."""

    def track(i, name, clip_id, time, end, label):
        dur = end - time
        dev_xml = ""
        if devices:
            dev_xml = (
                f'<Devices>\n'
                f'<StereoGain Id="{2000 + i}">\n'
                f'<Gain>\n'
                f'<AutomationTarget Id="{3000 + i}" />\n'
                f'</Gain>\n'
                f'</StereoGain>\n'
                f'<ChannelEq Id="{2100 + i}">\n'
                f'<LowShelfGain>\n'
                f'<AutomationTarget Id="{3100 + i}" />\n'
                f'</LowShelfGain>\n'
                f'</ChannelEq>\n'
                f'</Devices>\n'
            )
        return (
            f'<AudioTrack Id="{i}">\n'
            f'<Name><EffectiveName Value="{name}" /></Name>\n'
            f'<DeviceChain><MainSequencer><Sample><ArrangerAutomation><Events>\n'
            f'<AudioClip Id="{clip_id}" Time="{time}">\n'
            f'<Name Value="{label}" />\n'
            f'<CurrentStart Value="{time}" />\n'
            f'<CurrentEnd Value="{end}" />\n'
            f'<LoopStart Value="0.0" />\n'
            f'<LoopEnd Value="{dur}" />\n'
            f'<Color Value="10" />\n'
            f'</AudioClip>\n'
            f'</Events></ArrangerAutomation></Sample></MainSequencer>\n'
            + dev_xml +
            f'</DeviceChain>\n'
            f'<AutomationEnvelopes>\n'
            f'<Envelopes />\n'
            f'</AutomationEnvelopes>\n'
            f'</AudioTrack>\n'
        )

    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Ableton MajorVersion="5" MinorVersion="0">\n'
        '<MainTrack>\n<Tempo>\n<Manual Value="128.0" />\n</Tempo>\n</MainTrack>\n'
        + track(1, "Track Alpha", 11, 0.0, 800.0, "drop_1")
        + track(2, "Track Beta", 12, 500.0, 1400.0, "drop_1")
        + '</Ableton>\n'
    )
    with gzip.open(path, "wb") as f:
        f.write(xml.encode("utf-8"))


def _report_for_end_to_end(tmp_path):
    """A legacy report swap at 796 in a 500..800 overlap: clamped to 792,
    so the write-back WOULD mutate the report if it ran."""
    report = tmp_path / "in_ARRANGEMENT_REPORT.json"
    report.write_text(json.dumps({"transitions": [
        {"out_track": "Track Alpha", "in_track": "Track Beta",
         "swap_beats": 796.0, "handoff_kind": "drop->outro",
         "alignment_policy": "legacy_v1"},
    ]}, indent=2), encoding="utf-8")
    return report


def _run_main(tmp_path, monkeypatch, report):
    als_in = tmp_path / "in.als"
    als_out = tmp_path / "out.als"
    monkeypatch.setattr(sys, "argv", [
        "apply_automation.py",
        str(als_in),
        str(tmp_path / "sections.json"),
        str(als_out),
        str(report),
    ])
    (tmp_path / "sections.json").write_text("{}", encoding="utf-8")
    return als_in, als_out


def test_failed_als_write_leaves_report_byte_identical(tmp_path, monkeypatch):
    """Forced ALS-write failure (compress_als raises, simulating disk
    full / validation failure): the arrangement report must be left
    BYTE-IDENTICAL - a failed build must never mutate the paperwork."""
    report = _report_for_end_to_end(tmp_path)
    als_in, als_out = _run_main(tmp_path, monkeypatch, report)
    _write_sections_als(als_in, devices=True)
    before = report.read_bytes()

    def exploding_compress(lines, output_path):
        raise OSError(f"disk full writing {output_path}")

    monkeypatch.setattr(apply_automation, "compress_als", exploding_compress)
    with pytest.raises(OSError, match="disk full"):
        apply_automation.main()
    assert report.read_bytes() == before, (
        "a failed ALS write mutated the arrangement report - the report "
        "now describes an attempt that never shipped"
    )
    assert not als_out.exists()


def test_failed_target_validation_leaves_report_byte_identical(
    tmp_path, monkeypatch
):
    """Same guarantee one stage earlier: unresolvable automation targets
    (stripped device chain) raise before any write - the report must
    stay untouched."""
    report = _report_for_end_to_end(tmp_path)
    als_in, als_out = _run_main(tmp_path, monkeypatch, report)
    _write_sections_als(als_in, devices=False)
    before = report.read_bytes()
    with pytest.raises(ValueError, match="Automation target"):
        apply_automation.main()
    assert report.read_bytes() == before
    assert not als_out.exists()


def test_successful_build_still_writes_final_swaps(tmp_path, monkeypatch):
    """The write-back moved AFTER the ALS write - prove it still fires on
    success: the shipped report carries the clamped beat plus the full
    provenance trail."""
    report = _report_for_end_to_end(tmp_path)
    als_in, als_out = _run_main(tmp_path, monkeypatch, report)
    _write_sections_als(als_in, devices=True)
    apply_automation.main()
    assert als_out.is_file()
    tr = json.loads(report.read_text(encoding="utf-8"))["transitions"][0]
    assert tr["swap_beats"] == 792.0
    assert tr["swap_beats_pre_automation"] == 796.0
    assert tr["swap_transforms"] == ["clamped"]


# --- BLOCKER 3: rescue transitions through reconcile's boundary proof --------
#
# End-to-end against validate_mix_plan_als.reconcile using the synthetic
# plan+report+ALS triple from test_mix_plan_reconcile_fixture (whose own
# smoke tests pin the fixture against the ALREADY-ACTIVE paired path).

from test_mix_plan_reconcile_fixture import build_reconcile_fixture  # noqa: E402
from validate_mix_plan_als import reconcile  # noqa: E402


def test_rescue_good_fixture_checks_incoming_and_exempts_outgoing(tmp_path):
    """A rescue-policy transition must NOT silently skip the boundary
    proof: the incoming role is asserted (rescue places the incoming
    anchor exactly on the swap, same landmark machinery as paired) and
    the outgoing role is EXEMPTED VISIBLY - the grid-derived n_bars-16
    anchor is deliberately mid-clip, so the fixture models it with NO
    outgoing clip edge at the swap and must still pass."""
    result = reconcile(*build_reconcile_fixture(
        tmp_path,
        alignment_policy="tail_anchor_rescue_v1",
        handoff_kind="rescue/grid_tail->drop",
        outgoing_boundary_at_swap=False,
    ))
    assert result["status"] == "PASS"
    assert "rescue_boundary:t1:incoming" in result["checks"]
    assert "rescue_boundary:t1:outgoing_exempt_grid_anchor" in result["checks"]
    # The paired-named checks must NOT appear for a rescue transition.
    assert not any(c.startswith("paired_boundary:") for c in result["checks"])


def test_rescue_broken_incoming_boundary_fails(tmp_path):
    """The check FIRES on a broken rescue fixture: incoming clip starts 8
    beats before the swap -> no clip edge at the frozen swap -> hard
    error naming the role and policy. Pre-fix code skipped rescue
    transitions entirely, so this exact fixture reconciled green."""
    with pytest.raises(ValueError, match="incoming track has no clip boundary"):
        reconcile(*build_reconcile_fixture(
            tmp_path,
            alignment_policy="tail_anchor_rescue_v1",
            handoff_kind="rescue/grid_tail->drop",
            incoming_boundary_at_swap=False,
            outgoing_boundary_at_swap=False,
        ))


def test_paired_policy_still_asserts_outgoing_role(tmp_path):
    """Guard: the rescue exemption must never leak into the paired
    policy - a paired transition with no outgoing clip edge at the swap
    still fails both-role proof."""
    with pytest.raises(ValueError, match="outgoing track has no clip boundary"):
        reconcile(*build_reconcile_fixture(
            tmp_path,
            alignment_policy="paired_landmarks_v2",
            outgoing_boundary_at_swap=False,
        ))
