import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Tools"))

import plan_mix_decisions as tool
from align_engine import alignment_from_decision, fills_from_decision
from phrase_blend import BlendPlan
from section_format import SectionFormatError


def groove_result():
    return {
        "bpm": 120, "n_bars": 3,
        "analysis": {"section_detector": "groove-1", "sections_schema": 2, "downbeat_sec": 2},
        "sections": [{"start_bar": 0, "start_sec": 2, "label": "intro"},
                     {"start_bar": 2, "start_sec": 6, "label": "drop"}],
        "signals": {"fill_sections": [{"start_bar": 1.75, "end_bar": 2,
                                       "start_sec": 5.5, "end_sec": 6}]},
    }


@pytest.mark.parametrize("explicit_lead, expected_lead, expected_bass",
                         [(None, 1, [0, 1, 0]), (0, 0, [0, 0, 1])])
def test_converter_aligns_cached_content_with_groove_clock(
        tmp_path, explicit_lead, expected_lead, expected_bass):
    result = groove_result()
    if explicit_lead is not None:
        result["lead_in_bars"] = explicit_lead
    cache = tmp_path / "track__stemenv.npz"
    np.savez(cache, hop_t=0.25, bass=np.repeat([0, 0, 1, 0], 8),
             drums=np.ones(32), tiera_band_low=np.tile([1, 0], 16),
             vocals=np.zeros(32), other=np.ones(32))
    track = tool.convert_track(result, cache)
    assert track == {
        "bpm": 120, "n_bars": 3, "lead_in_bars": expected_lead,
        "sections": [[0, "INTRO"], [2, "DROP"]], "fills": [[1.75, 2]],
        "content": {"has_bass": expected_bass, "has_kick": [1, 1, 1], "has_music": [1, 1, 1]},
    }


def test_converter_refuses_legacy_sections_before_reading_envelope(tmp_path):
    result = groove_result()
    del result["analysis"]["section_detector"]
    with pytest.raises(SectionFormatError, match="groove"):
        tool.convert_track(result, tmp_path / "missing.npz")


def test_writer_uses_layout_order_and_preserves_planner_edit_geometry(tmp_path, monkeypatch, capsys):
    sections = tmp_path / "Sections_V1.json"
    sections.write_text(json.dumps({
        "B": [{"arr_time": 300, "arr_end": 540, "source_end_beats": 240}],
        "A": [{"arr_time": 0, "arr_end": 400, "source_end_beats": 400}],
    }), encoding="ascii")
    names = tool.track_order(tmp_path, sections)
    assert names == ["A", "B"]
    plan = BlendPlan(78, 92, 105, 5, 4, swap_incoming_bar=10, in_loop_phrase_end=8,
                     out_loop_source_bars=(84, 92), paired=False, why=["For the test."])
    monkeypatch.setattr(tool, "plan_blend", lambda *_: plan)
    decisions = tmp_path / "decisions.json"
    tool.write_decisions(decisions, names, [{"n_bars": 100}, {"n_bars": 60}])
    [decision] = tool.read_json(decisions)["transitions"]
    assert decision["pair_index"] == 1
    assert decision["entry_out_bar"] == 78
    assert decision["out_end_bar"] == 105
    assert decision["intro_loop"] == {"source_start_bar": 4, "source_end_bar": 8}
    assert decision["tail_loop"] == {
        "source_start_bar": 84, "source_end_bar": 92, "reps": 0, "partial_bars": 5,
    }
    out_track = SimpleNamespace(name="A", n_bars=100)
    in_track = SimpleNamespace(name="B", n_bars=60)
    alignment = alignment_from_decision(out_track, in_track, decision)
    assert (alignment.arr_offset_bars, alignment.handoff_bar_out, alignment.anchor_bar_in) == (78, 92, 10)
    assert [edit.kind for edit in fills_from_decision(out_track, in_track, decision)] == [
        "decision_tail_loop", "decision_intro_loop",
    ]
    assert "ONE-SIDED" in capsys.readouterr().out


def test_writer_records_exact_outgoing_and_incoming_cut(tmp_path, monkeypatch):
    plan = BlendPlan(72, 84, 96, 0, 0, out_cut_bars=4, in_intro_cut_bars=4, swap_incoming_bar=16)
    monkeypatch.setattr(tool, "plan_blend", lambda *_: plan)
    [decision] = tool.write_decisions(tmp_path / "decisions.json", ["A", "B"],
                                    [{"n_bars": 100}, {"n_bars": 60}])
    assert (decision["intro_trim_bars"], decision["out_end_bar"]) == (4, 96)
    out_track = SimpleNamespace(name="A", n_bars=100)
    in_track = SimpleNamespace(name="B", n_bars=60)
    assert alignment_from_decision(out_track, in_track, decision).handoff_bar_out == 84
    tail, front = fills_from_decision(out_track, in_track, decision)
    assert (tail.kind, tail.target_marker_bar, front.kind, front.cut_to_bar) == (
        "decision_tail_trim", 96, "decision_intro_trim", 4,
    )


@pytest.mark.parametrize("plan, error", [
    (None, "plan_blend returned None"),
    (BlendPlan(80.25, 92, 100, 0, 0), "entry_out_bar .* not a whole bar"),
])
def test_writer_reports_unusable_plan_without_writing_partial_file(tmp_path, monkeypatch, plan, error):
    monkeypatch.setattr(tool, "plan_blend", lambda *_: plan)
    decisions = tmp_path / "decisions.json"
    with pytest.raises(ValueError, match=error):
        tool.write_decisions(decisions, ["A", "B"], [{"n_bars": 100}, {"n_bars": 60}])
    assert not decisions.exists()


def test_writer_keeps_existing_file_if_atomic_publish_fails(tmp_path, monkeypatch):
    decisions = tmp_path / "decisions.json"
    decisions.write_bytes(b"old proposal")
    monkeypatch.setattr(tool, "plan_blend", lambda *_: BlendPlan(72, 88, 100, 0, 0,
                                                              swap_incoming_bar=16))

    def fail_replace(stage, target):
        assert stage.parent == target.parent and stage != target
        assert tool.read_json(stage)["transitions"][0]["out_end_bar"] == 100
        assert target.read_bytes() == b"old proposal"
        raise OSError("rename failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="rename failed"):
        tool.write_decisions(decisions, ["A", "B"], [{"n_bars": 100}, {"n_bars": 60}])
    assert list(tmp_path.iterdir()) == [decisions]
    assert decisions.read_bytes() == b"old proposal"


def test_writer_refuses_empty_layout_tracks_and_aliased_outputs(tmp_path, capsys):
    sections = tmp_path / "Sections.json"
    sections.write_text('{"A": []}', encoding="ascii")
    with pytest.raises(ValueError, match="empty layout track 'A'"):
        tool.track_order(tmp_path, sections)
    sections.write_text(json.dumps({
        "A": [{"arr_time": 0, "arr_end": 400, "source_end_beats": 400}],
    }), encoding="ascii")
    hardlink = tmp_path / "alias.json"
    os.link(sections, hardlink)
    args = [str(tmp_path), "--sections", str(sections)]
    output = tmp_path / "out.json"
    for outputs in [
        ["--out", str(hardlink)],
        ["--out", str(output), "--content", str(sections)],
        ["--out", str(output), "--content", str(output)],
    ]:
        with pytest.raises(SystemExit) as error:
            tool.main(args + outputs)
        assert error.value.code == 1
        assert "aliases" in capsys.readouterr().err
        assert not output.exists()
