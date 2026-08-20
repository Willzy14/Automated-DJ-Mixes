"""Regression tests for the 2026-08-20 Codex adversarial-review BLOCKER batch.

1. Entity-safe hint lookup (propose_arrangement._hint_for).
2. --cue-signals never leaks CUE_CONFIG between same-process invocations.
3. Rescued alignments (tail_anchor_rescue_v1) classify as landmark-mode in
   plan_fill_or_cut -- their loops/cuts obey the same CueConfig flags.
4. apply_automation never snaps an aligner-chosen swap; the arrangement
   report always ends up carrying the beat automation actually used.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import align_engine as AE  # noqa: E402
import propose_arrangement as PA  # noqa: E402
from apply_automation import (  # noqa: E402
    TrackInfo,
    _load_arrangement_report,
    _resolve_report_path,
    plan_transitions,
    write_final_swaps_to_report,
)


# --- Fix 1: entity-safe hint lookup -----------------------------------------

def test_hint_lookup_unescapes_ampersand():
    hints = {"A & B.wav": {"first_drop_sec": 61.5}}
    assert PA._hint_for(hints, "A &amp; B")["first_drop_sec"] == 61.5

def test_hint_lookup_unescapes_apostrophe():
    hints = {"Pushin' On.wav": {"loop_source_sec": 12.0}}
    assert PA._hint_for(hints, "Pushin&apos; On")["loop_source_sec"] == 12.0

def test_hint_lookup_raw_key_still_works():
    hints = {"A &amp; B": {"intro_skip_bars": 2}}
    assert PA._hint_for(hints, "A &amp; B")["intro_skip_bars"] == 2

def test_hint_lookup_missing_returns_empty_dict():
    assert PA._hint_for({}, "Nope") == {}


# --- Fix 2: CUE_CONFIG isolation between CLI invocations --------------------

def test_cue_config_never_leaks_between_invocations():
    saved = AE.CUE_CONFIG
    try:
        PA._apply_cue_signals("matched,fills")
        assert AE.CUE_CONFIG.matched_tail_head_swap is True
        assert AE.CUE_CONFIG.emit_fills is True
        PA._apply_cue_signals("")
        assert AE.CUE_CONFIG == AE.CueConfig()
    finally:
        AE.CUE_CONFIG = saved

def test_cue_signals_unknown_name_raises():
    saved = AE.CUE_CONFIG
    try:
        with pytest.raises(ValueError, match="unknown cue signal"):
            PA._apply_cue_signals("bogus")
    finally:
        AE.CUE_CONFIG = saved


# --- Fix 3: rescue policy is landmark-mode in plan_fill_or_cut --------------

def _rescued_fixture():
    o = AE.Track(
        name="OutTrack", bpm=125.0, spb=1.92, downbeat=0.0, n_bars=64,
        sections=[
            {"name": "intro_1", "label": "intro", "start_bar": 0.0, "end_bar": 16.0},
            {"name": "drop_1", "label": "drop", "start_bar": 16.0, "end_bar": 64.0},
        ],
        bass_in_bar=16.0, bass_out_bar=None,
    )
    i = AE.Track(
        name="InTrack", bpm=125.0, spb=1.92, downbeat=0.0, n_bars=64,
        sections=[
            {"name": "intro_1", "label": "intro", "start_bar": 0.0, "end_bar": 8.0},
            {"name": "drop_1", "label": "drop", "start_bar": 8.0, "end_bar": 64.0},
        ],
        bass_in_bar=8.0, bass_out_bar=None,
        loop_windows=[(0.0, 8.0)],
    )
    al = AE.Alignment(
        out_name="OutTrack", in_name="InTrack",
        handoff_bar_out=48.0, handoff_kind="rescue/grid_tail->drop",
        anchor_bar_in=8.0, arr_offset_bars=40.0, overlap_bars=24.0,
        score=0, alignment_policy="tail_anchor_rescue_v1",
    )
    return o, i, al

def test_rescued_pair_with_flags_off_gets_no_loop_or_cut():
    o, i, al = _rescued_fixture()
    saved = AE.CUE_CONFIG
    AE.CUE_CONFIG = AE.CueConfig()  # every flag off
    try:
        specs = AE.plan_fill_or_cut(o, i, al)
    finally:
        AE.CUE_CONFIG = saved
    assert specs == []

def test_fixture_is_potent_legacy_policy_does_loop():
    # Prove-the-test: the SAME geometry under the legacy policy label fires
    # the intro loop, so the empty result above is the classification at
    # work, not a vacuous fixture.
    o, i, al = _rescued_fixture()
    al = replace(al, alignment_policy="legacy_v1", handoff_kind="drop->drop")
    saved = AE.CUE_CONFIG
    AE.CUE_CONFIG = AE.CueConfig()
    try:
        specs = AE.plan_fill_or_cut(o, i, al)
    finally:
        AE.CUE_CONFIG = saved
    assert any(s.kind == "incoming_intro" for s in specs)


# --- Fix 4: snap respects the aligner; the report tells the truth ----------

def _sec(name, label, start, end):
    return {"name": name, "label": label, "arr_time": start, "arr_end": end}

def _pair_tracks():
    out_t = TrackInfo("OutT", [_sec("drop_2", "drop", 400.0, 603.0),
                               _sec("outro_1", "outro", 603.0, 700.0)],
                      0.0, 700.0)
    in_t = TrackInfo("InT", [], 560.0, 1200.0)
    return out_t, in_t

def test_aligner_chosen_swap_is_not_snapped():
    # Section edge at 603 is 3 beats from the aligner's 600 -- must stay put.
    out_t, in_t = _pair_tracks()
    from apply_automation import _normalise
    swaps = {(_normalise(out_t.name), _normalise(in_t.name)): {
        "swap_beats": 600.0,
        "handoff_kind": "paired/landmark:kick_dropout:end->drop",
        "alignment_policy": "paired_landmarks_v2",
    }}
    planned = plan_transitions([out_t, in_t], swaps)
    assert planned[0].bass_swap == 600.0

def test_rescue_swap_is_not_snapped():
    out_t, in_t = _pair_tracks()
    from apply_automation import _normalise
    swaps = {(_normalise(out_t.name), _normalise(in_t.name)): {
        "swap_beats": 600.0,
        "handoff_kind": "rescue/grid_tail->drop",
        "alignment_policy": "tail_anchor_rescue_v1",
    }}
    planned = plan_transitions([out_t, in_t], swaps)
    assert planned[0].bass_swap == 600.0

def test_legacy_report_swap_still_snaps():
    out_t, in_t = _pair_tracks()
    from apply_automation import _normalise
    swaps = {(_normalise(out_t.name), _normalise(in_t.name)): {
        "swap_beats": 600.0,
        "handoff_kind": "drop->outro",
        "alignment_policy": "legacy_v1",
    }}
    planned = plan_transitions([out_t, in_t], swaps)
    assert planned[0].bass_swap == 603.0

def test_report_without_policy_field_still_snaps():
    # Back-compat: an old report with no alignment_policy is treated as legacy.
    out_t, in_t = _pair_tracks()
    from apply_automation import _normalise
    swaps = {(_normalise(out_t.name), _normalise(in_t.name)): {
        "swap_beats": 600.0,
        "handoff_kind": "drop->outro",
    }}
    planned = plan_transitions([out_t, in_t], swaps)
    assert planned[0].bass_swap == 603.0

def test_final_beat_is_written_back_to_report(tmp_path):
    report = tmp_path / "mix_ARRANGEMENT_REPORT.json"
    report.write_text(json.dumps({"transitions": [
        {"out_track": "A", "in_track": "B", "swap_beats": 600.0,
         "handoff_kind": "paired/landmark:kick_dropout:end->drop",
         "alignment_policy": "paired_landmarks_v2"},
        {"out_track": "B", "in_track": "C", "swap_beats": 1600.0,
         "handoff_kind": "drop->outro",
         "alignment_policy": "legacy_v1"},
    ]}), encoding="utf-8")
    tracks = [
        TrackInfo("A", [_sec("drop_1", "drop", 400.0, 603.0),
                        _sec("outro_1", "outro", 603.0, 700.0)], 0.0, 700.0),
        TrackInfo("B", [_sec("drop_1", "drop", 1400.0, 1603.0),
                        _sec("outro_1", "outro", 1603.0, 1700.0)], 560.0, 1700.0),
        TrackInfo("C", [], 1560.0, 2200.0),
    ]
    swaps = _load_arrangement_report(tmp_path / "mix.als", report)
    plans = plan_transitions(tracks, swaps)
    write_final_swaps_to_report(report, plans)
    rep = json.loads(report.read_text(encoding="utf-8"))
    t1, t2 = rep["transitions"]
    # Aligner-chosen: not snapped, report untouched.
    assert plans[0].bass_swap == 600.0
    assert t1["swap_beats"] == 600.0
    assert "swap_beats_pre_automation" not in t1
    # Legacy: snapped onto the 1603 outro edge; report carries the truth.
    assert plans[1].bass_swap == 1603.0
    assert t2["swap_beats"] == 1603.0
    assert t2["swap_beats_pre_automation"] == 1600.0
    # Invariant: report equals automation in ALL cases.
    for plan, tr in zip(plans, rep["transitions"]):
        assert tr["swap_beats"] == plan.bass_swap

def test_resolve_report_path_explicit_missing_raises(tmp_path):
    with pytest.raises(ValueError, match="was not found"):
        _resolve_report_path(tmp_path / "mix.als", tmp_path / "missing.json")
