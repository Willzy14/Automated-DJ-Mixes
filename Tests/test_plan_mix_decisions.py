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
from phrase_blend import Alternative, BlendPlan
from propose_arrangement import propose_arrangement
from section_format import SectionFormatError
from test_arrangement_decisions import blend_layout


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


@pytest.fixture
def planner_inputs(tmp_path):
    sections = tmp_path / "Sections_V1.json"
    sections.write_text(json.dumps({
        name: [{"arr_time": i * 40, "arr_end": i * 40 + 12, "source_end_beats": 12}]
        for i, name in enumerate("ABC")
    }), encoding="ascii")
    stem_dir = tmp_path / "_Stem Analysis"
    stem_dir.mkdir()
    for name in "ABC":
        (stem_dir / f"SECTIONS_STEM_{name}.json").write_text(
            json.dumps(groove_result()), encoding="ascii")
        np.savez(stem_dir / f"{name}__stemenv.npz", hop_t=0.25, bass=np.ones(32),
                 drums=np.ones(32), vocals=np.zeros(32), other=np.ones(32),
                 tiera_band_low=np.ones(32))
    output = tmp_path / "decisions.json"
    return [str(tmp_path), "--sections", str(sections), "--out", str(output)], output


@pytest.mark.parametrize("failure, reason", [
    (None, "no admissible blend"),
    (BlendPlan(1.25, 2, 3, 0, 0), "is not a whole bar"),
    (BlendPlan(float("inf"), 2, 3, 0, 0), "cannot convert float infinity to integer"),
])
def test_partial_cli_writes_other_pairs(planner_inputs, monkeypatch, capsys, failure, reason):
    results = iter([BlendPlan(1, 2, 3, 0, 0, swap_incoming_bar=1), failure])

    def plan(*_, why):
        result = next(results)
        if result is None:
            why.append("no admissible blend")
        return result

    monkeypatch.setattr(tool, "plan_blend", plan)
    args, output = planner_inputs
    assert tool.main([*args, "--allow-partial"]) == 0
    data = tool.read_json(output)
    assert [row["pair_index"] for row in data["transitions"]] == [1]
    [fallback] = data["fallback_pairs"]
    assert (fallback["pair_index"], fallback["out_track"], fallback["in_track"]) == (2, "B", "C")
    assert reason in fallback["reason"]
    assert [line for line in capsys.readouterr().out.splitlines() if line.startswith("FALLBACK")] == [
        "FALLBACK T2: " + fallback["reason"],
    ]


def test_partial_cli_refuses_when_every_pair_fails(planner_inputs, monkeypatch, capsys):
    monkeypatch.setattr(tool, "plan_blend", lambda *_, **__: None)
    args, output = planner_inputs
    with pytest.raises(SystemExit) as error:
        tool.main([*args, "--allow-partial"])
    assert error.value.code == 1
    log = capsys.readouterr()
    assert "the planner found no usable pairs" in log.err
    assert log.out.count("FALLBACK T") == 2
    assert not output.exists()


@pytest.mark.parametrize("invalid, reason", [
    ("missing envelope", "C__stemenv.npz"),
    ("bad json", "Expecting value"),
    ("invalid bpm", "invalid bpm or n_bars"),
])
def test_partial_cli_refuses_invalid_inputs(planner_inputs, capsys, invalid, reason):
    args, output = planner_inputs
    stem_dir = output.parent / "_Stem Analysis"
    sections = stem_dir / "SECTIONS_STEM_C.json"
    if invalid == "missing envelope":
        (stem_dir / "C__stemenv.npz").unlink()
    elif invalid == "bad json":
        sections.write_text("bad json", encoding="ascii")
    else:
        sections.write_text(json.dumps({**groove_result(), "bpm": 0}), encoding="ascii")
    with pytest.raises(SystemExit) as error:
        tool.main([*args, "--allow-partial"])
    assert error.value.code == 1
    log = capsys.readouterr()
    assert reason in log.err
    assert "FALLBACK" not in log.out
    assert not output.exists()


def test_phase2_loads_partial_file_and_arranges_omitted_pair(blend_layout):
    als, sections, output, decisions = blend_layout()
    data = tool.read_json(decisions)
    data["transitions"].pop()
    data["fallback_pairs"] = [dict(pair_index=2, out_track="B", in_track="C", reason="test")]
    tool.write_json_atomically(decisions, data)
    plan = propose_arrangement(als, sections, output, decisions_path=decisions)
    assert [ov.decision_exit for ov in plan.overlaps] == [(96, 384), None]
    assert output.is_file()


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


# ---- the planner's whole-bar fallback (found by the fresh mix, 2026-11-24) ----------------
# T3 of that mix: the top plan lined the swap up on a fill end half a bar in (entry 168.5), a
# decisions file names whole bars only, and the pair fell to the rule-based aligner, which stopped.
# The outgoing is 184 bars long, so a plan that ends at bar 184 needs no tail edit.
OUT_BARS, IN_BARS = 184, 200

# What the writer produced for this whole-bar mix before the fallback existed (taken from main).
WHOLE_BAR_MIX_FILE = b'{\n  "transitions": [\n    {\n      "pair_index": 1,\n      "out_track": "A",\n      "in_track": "B",\n      "entry_out_bar": 72.0,\n      "intro_trim_bars": 0.0,\n      "swap_in_bar": 16.0,\n      "swap_cue": "unmarked",\n      "out_cue": "unmarked",\n      "out_end_bar": 100.0,\n      "reason": "First pair."\n    },\n    {\n      "pair_index": 2,\n      "out_track": "B",\n      "in_track": "C",\n      "entry_out_bar": 78.0,\n      "intro_trim_bars": 0.0,\n      "swap_in_bar": 10.0,\n      "swap_cue": "fill end",\n      "out_cue": "drop",\n      "out_end_bar": 105.0,\n      "reason": "Second pair. | Tail repeats.",\n      "intro_loop": {\n        "source_start_bar": 4.0,\n        "source_end_bar": 8.0\n      },\n      "tail_loop": {\n        "source_start_bar": 84.0,\n        "source_end_bar": 92.0,\n        "reps": 0,\n        "partial_bars": 5.0\n      }\n    }\n  ]\n}\n'
WHOLE_BAR_MIX_LOG = 'T1: A -> B\n  Top: entry 72, swap 88, end 100, cost 3.25 [ONE-SIDED / UNPAIRED]\n    Incoming source at swap 16, trim 0, repeat 0 at 0; outgoing extension 0 from None, cut 0\n    First pair.\n  Alternative 1: entry 80.5, swap 90, end 100, cost 0.00 [ONE-SIDED / UNPAIRED]\n    Incoming source at swap 9.5, trim 0, repeat 0 at 0; outgoing extension 0 from None, cut 0\n    r. f\nT2: B -> C\n  Top: entry 78, swap 92, end 105, cost 4.50\n    Incoming source at swap 10, trim 0, repeat 4 at 8; outgoing extension 5 from (84, 92), cut 0\n    Second pair.\n    Tail repeats.\n'


def fractional_top(**kwargs):
    return BlendPlan(168.5, 176, 184, 0, 0, swap_incoming_bar=7.5, cost=7.81,
                     why=["Top, fill end."], **kwargs)


def fractional_plan(cost=8.0):
    return BlendPlan(176.5, 184, 184, 0, 0, swap_incoming_bar=7.5, cost=cost)


def whole_plan(entry, swap, cost):
    return BlendPlan(entry, swap, 184, 0, 0, swap_incoming_bar=swap - entry, cost=cost,
                     why=[f"Whole entry {entry:g}."])


def alt(plan):
    return Alternative(plan, plan.bass_switch_out_bar, "an alternative", "a feel")


def write_pair(monkeypatch, tmp_path, top, ranked=()):
    monkeypatch.setattr(tool, "plan_blend", lambda *_, **__: top)
    monkeypatch.setattr(tool, "candidates", lambda *_, **__: list(ranked), raising=False)
    path = tmp_path / "decisions.json"
    tool.write_decisions(path, ["A", "B"], [{"n_bars": OUT_BARS}, {"n_bars": IN_BARS}])
    return path


def fractional_numbers(node):
    if isinstance(node, dict):
        return [bad for key, value in node.items() if key != "reason" for bad in fractional_numbers(value)]
    if isinstance(node, list):
        return [bad for value in node for bad in fractional_numbers(value)]
    return [node] if isinstance(node, float) and node != int(node) else []


def test_fractional_top_uses_first_whole_alternative_and_says_so(tmp_path, monkeypatch, capsys):
    top = fractional_top(alternatives=[alt(whole_plan(152, 160, 8.37)),
                                       alt(whole_plan(150, 160, 9.10))])
    data = tool.read_json(write_pair(monkeypatch, tmp_path, top))
    [decision] = data["transitions"]
    assert (decision["entry_out_bar"], decision["swap_in_bar"], decision["out_end_bar"]) == (152, 8, 184)
    said = ("T1: top plan (entry 168.5, cost 7.81) has a fractional bar; used alternative 1 "
            "(entry 152, swap 160, end 184, cost 8.37, +0.56)")
    lines = capsys.readouterr().out.splitlines()
    assert said in lines
    assert lines[-1] == "1 pairs used a whole-bar alternative: T1 (+0.56)"
    assert decision["reason"].startswith(said + " | Whole entry 152.")
    assert data["whole_bar_alternative_count"] == 1
    [row] = data["whole_bar_alternatives"]
    assert (row["pair_index"], row["used"], row["top_entry_out_bar"], row["top_cost"]) == (
        1, "alternative 1", 168.5, 7.81)
    assert (row["entry_out_bar"], row["cost"], row["cost_difference"]) == (152, 8.37, 0.56)
    assert "fallback_pairs" not in data


def test_fractional_first_alternative_is_skipped_for_the_next_whole_one(tmp_path, monkeypatch, capsys):
    top = fractional_top(alternatives=[alt(fractional_plan()), alt(whole_plan(150, 160, 8.9))])
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top))["transitions"]
    assert decision["entry_out_bar"] == 150
    out = capsys.readouterr().out
    assert "used alternative 2 (entry 150, swap 160, end 184, cost 8.90, +1.09)" in out
    assert "alternative 1 skipped" in out and "176.5 is not a whole bar" in out


def test_cheapest_whole_alternative_wins_even_when_listed_last(tmp_path, monkeypatch, capsys):
    top = fractional_top(alternatives=[alt(whole_plan(140, 150, 9.9)), alt(whole_plan(152, 160, 8.4)),
                                       alt(whole_plan(150, 160, 9.1))])
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top))["transitions"]
    assert decision["entry_out_bar"] == 152
    assert "used alternative 2 (entry 152" in capsys.readouterr().out


def test_alternative_the_decision_check_refuses_is_skipped(tmp_path, monkeypatch, capsys):
    # whole bars, but the swap cannot be reproduced (160 != 152 + 7): the normal check refuses it
    broken = BlendPlan(152, 160, 184, 0, 0, swap_incoming_bar=7, cost=8.0)
    top = fractional_top(alternatives=[alt(broken), alt(whole_plan(150, 160, 8.9))])
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top))["transitions"]
    assert decision["entry_out_bar"] == 150
    out = capsys.readouterr().out
    assert "alternative 1 skipped" in out and "cannot reproduce the planner swap" in out
    assert "used alternative 2" in out


def test_ranked_candidates_are_used_when_no_alternative_is_whole(tmp_path, monkeypatch, capsys):
    flat = whole_plan(148, 160, 8.0)
    flat.terms["flat"] = 2.0
    cut = whole_plan(146, 160, 8.1)
    cut.out_cut_bars = 2
    top = fractional_top(alternatives=[alt(fractional_plan())])
    ranked = [top, whole_plan(144, 160, 9.5), flat, cut, whole_plan(152, 160, 8.5)]
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top, ranked))["transitions"]
    assert decision["entry_out_bar"] == 152
    assert "used candidate 5 (entry 152, swap 160, end 184, cost 8.50, +0.69)" in capsys.readouterr().out


@pytest.mark.parametrize("allow_partial", [False, True])
def test_no_whole_plan_anywhere_falls_back_exactly_as_before(tmp_path, monkeypatch, capsys, allow_partial):
    top = fractional_top(alternatives=[alt(fractional_plan())])
    results = iter([BlendPlan(72, 88, 184, 0, 0, swap_incoming_bar=16), top])
    monkeypatch.setattr(tool, "plan_blend", lambda *_, **__: next(results))
    monkeypatch.setattr(tool, "candidates", lambda *_, **__: [top, fractional_plan()], raising=False)
    path = tmp_path / "decisions.json"
    names, tracks = ["A", "B", "C"], [{"n_bars": OUT_BARS}, {"n_bars": IN_BARS}, {"n_bars": IN_BARS}]
    if not allow_partial:
        with pytest.raises(ValueError, match=r"T2: decision for pair 2: entry_out_bar 168\.5 is not a whole bar"):
            tool.write_decisions(path, names, tracks)
        assert not path.exists()
        return
    tool.write_decisions(path, names, tracks, allow_partial=True)
    data = tool.read_json(path)
    [fallback] = data["fallback_pairs"]
    assert fallback["pair_index"] == 2 and "entry_out_bar 168.5 is not a whole bar" in fallback["reason"]
    assert [row["pair_index"] for row in data["transitions"]] == [1]
    assert "whole_bar_alternatives" not in data and "whole_bar_alternative_count" not in data
    lines = capsys.readouterr().out.splitlines()
    assert "FALLBACK T2: " + fallback["reason"] in lines
    assert not any("pairs used a whole-bar alternative" in line for line in lines)


def test_a_fractional_bar_is_never_rounded_into_the_file(tmp_path, monkeypatch):
    top = fractional_top(alternatives=[alt(whole_plan(152, 160, 8.37))])
    data = tool.read_json(write_pair(monkeypatch, tmp_path, top))
    assert fractional_numbers(data["transitions"]) == []
    assert data["transitions"][0]["entry_out_bar"] == 152          # not round(168.5), 168
    top = fractional_top(alternatives=[alt(fractional_plan())])
    (tmp_path / "none").mkdir()
    with pytest.raises(ValueError, match="not a whole bar"):
        write_pair(monkeypatch, tmp_path / "none", top, [top, fractional_plan()])
    assert list((tmp_path / "none").iterdir()) == []


def test_only_a_fractional_bar_sends_the_planner_to_its_alternatives(tmp_path, monkeypatch, capsys):
    top = BlendPlan(float("inf"), 176, 184, 0, 0, swap_incoming_bar=7.5, cost=7.81,
                    alternatives=[alt(whole_plan(152, 160, 8.37))])
    with pytest.raises(ValueError, match="cannot convert float infinity to integer"):
        write_pair(monkeypatch, tmp_path, top)
    assert "used alternative" not in capsys.readouterr().out


def test_whole_bar_top_never_looks_at_alternatives_and_writes_the_same_bytes(tmp_path, monkeypatch, capsys):
    plans = iter([
        BlendPlan(72, 88, 100, 0, 0, swap_incoming_bar=16, cost=3.25, why=["First pair."],
                  alternatives=[Alternative(BlendPlan(80.5, 90, 100, 0, 0, swap_incoming_bar=9.5), 90, "r", "f")]),
        BlendPlan(78, 92, 105, 5, 4, swap_incoming_bar=10, in_loop_phrase_end=8,
                  out_loop_source_bars=(84, 92), paired=True, in_point_kind="fill end",
                  out_point_kind="drop", cost=4.5, why=["Second pair.", "Tail repeats."]),
    ])
    monkeypatch.setattr(tool, "plan_blend", lambda *_: next(plans))

    def refuse(*_, **__):
        raise AssertionError("a whole-bar top must not ask for the candidate list")

    monkeypatch.setattr(tool, "candidates", refuse, raising=False)
    path = tmp_path / "decisions.json"
    tool.write_decisions(path, ["A", "B", "C"], [{"n_bars": 100}, {"n_bars": 100}, {"n_bars": 60}])
    assert path.read_bytes() == WHOLE_BAR_MIX_FILE
    assert capsys.readouterr().out == WHOLE_BAR_MIX_LOG


def test_cli_counts_whole_bar_alternatives_in_the_planner_json(planner_inputs, monkeypatch, capsys):
    results = iter([BlendPlan(1, 2, 3, 0, 0, swap_incoming_bar=1),
                    BlendPlan(1.5, 2, 3, 0, 0, swap_incoming_bar=0.5, cost=1.0, alternatives=[
                        alt(BlendPlan(1, 2, 3, 0, 0, swap_incoming_bar=1, cost=1.5))])])
    monkeypatch.setattr(tool, "plan_blend", lambda *_, **__: next(results))
    args, output = planner_inputs
    assert tool.main([*args, "--allow-partial"]) == 0
    data = tool.read_json(output)
    assert [row["pair_index"] for row in data["transitions"]] == [1, 2]
    assert data["fallback_pairs"] == []
    assert data["whole_bar_alternative_count"] == 1
    assert [(row["pair_index"], row["cost_difference"]) for row in data["whole_bar_alternatives"]] == [(2, 0.5)]
    assert "1 pairs used a whole-bar alternative: T2 (+0.50)" in capsys.readouterr().out.splitlines()
