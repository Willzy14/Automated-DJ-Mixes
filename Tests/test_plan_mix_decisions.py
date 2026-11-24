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
from phrase_blend import ALT_MARGIN, Alternative, BlendPlan, plan_blend
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
# Every plan below is built with keyword arguments: BlendPlan's fourth and fifth fields are
# out_looped_bars then in_intro_looped_bars (checked against the dataclass), not the reverse.
OUT_BARS, IN_BARS = 184, 200

# What the writer produced for this whole-bar mix before the fallback existed (taken from main).
WHOLE_BAR_MIX_FILE = b'{\n  "transitions": [\n    {\n      "pair_index": 1,\n      "out_track": "A",\n      "in_track": "B",\n      "entry_out_bar": 72.0,\n      "intro_trim_bars": 0.0,\n      "swap_in_bar": 16.0,\n      "swap_cue": "unmarked",\n      "out_cue": "unmarked",\n      "out_end_bar": 100.0,\n      "reason": "First pair."\n    },\n    {\n      "pair_index": 2,\n      "out_track": "B",\n      "in_track": "C",\n      "entry_out_bar": 78.0,\n      "intro_trim_bars": 0.0,\n      "swap_in_bar": 10.0,\n      "swap_cue": "fill end",\n      "out_cue": "drop",\n      "out_end_bar": 105.0,\n      "reason": "Second pair. | Tail repeats.",\n      "intro_loop": {\n        "source_start_bar": 4.0,\n        "source_end_bar": 8.0\n      },\n      "tail_loop": {\n        "source_start_bar": 84.0,\n        "source_end_bar": 92.0,\n        "reps": 0,\n        "partial_bars": 5.0\n      }\n    }\n  ]\n}\n'
WHOLE_BAR_MIX_LOG = 'T1: A -> B\n  Top: entry 72, swap 88, end 100, cost 3.25 [ONE-SIDED / UNPAIRED]\n    Incoming source at swap 16, trim 0, repeat 0 at 0; outgoing extension 0 from None, cut 0\n    First pair.\n  Alternative 1: entry 80.5, swap 90, end 100, cost 0.00 [ONE-SIDED / UNPAIRED]\n    Incoming source at swap 9.5, trim 0, repeat 0 at 0; outgoing extension 0 from None, cut 0\n    r. f\nT2: B -> C\n  Top: entry 78, swap 92, end 105, cost 4.50\n    Incoming source at swap 10, trim 0, repeat 4 at 8; outgoing extension 5 from (84, 92), cut 0\n    Second pair.\n    Tail repeats.\n'


def plan(entry, swap, cost, *, end=184, swap_in=None, **kwargs):
    return BlendPlan(in_starts_at_out_bar=entry, bass_switch_out_bar=swap, out_end_bar=end,
                     out_looped_bars=kwargs.pop("out_looped", 0),
                     in_intro_looped_bars=kwargs.pop("in_looped", 0), cost=cost,
                     swap_incoming_bar=swap - entry if swap_in is None else swap_in, **kwargs)


def fractional_top(cost=7.81, **kwargs):
    kwargs.setdefault("why", ["Top, fill end."])
    return plan(168.5, 176, cost, end=184, swap_in=7.5, **kwargs)


def fractional_plan(cost=8.0):
    return plan(176.5, 184, cost, swap_in=7.5)


def whole_plan(entry, swap, cost, **kwargs):
    kwargs.setdefault("why", [f"Whole entry {entry:g}."])
    return plan(entry, swap, cost, **kwargs)


def alt(candidate):
    return Alternative(candidate, candidate.bass_switch_out_bar, "an alternative", "a feel")


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
                                       alt(whole_plan(150, 164, 9.10))])
    data = tool.read_json(write_pair(monkeypatch, tmp_path, top))
    [decision] = data["transitions"]
    assert (decision["entry_out_bar"], decision["swap_in_bar"], decision["out_end_bar"]) == (152, 8, 184)
    said = ("T1: top plan (entry 168.5, cost 7.81) has a fractional bar (decision for pair 1: "
            "entry_out_bar 168.5 is not a whole bar - decisions name whole musical bars, not "
            "fractional positions); used alternative 1 (entry 152, swap 160, end 184, "
            "cost 8.37 vs the planner's best 7.81 (+0.56))")
    lines = capsys.readouterr().out.splitlines()
    assert said in lines
    assert lines[-1] == "1 pair used a whole-bar alternative: T1 (+0.56)"
    assert decision["reason"].startswith(said + " | Whole entry 152.")
    assert data["whole_bar_alternative_count"] == 1
    [row] = data["whole_bar_alternatives"]
    assert (row["pair_index"], row["used"], row["top_entry_out_bar"], row["top_cost"]) == (
        1, "alternative 1", 168.5, 7.81)
    assert (row["entry_out_bar"], row["cost"], row["cost_difference"]) == (152, 8.37, 0.56)
    assert row["swap_out_bar"] == 160 != top.bass_switch_out_bar    # the used plan's swap, not the top's
    assert "entry_out_bar 168.5 is not a whole bar" in row["top_error"]
    assert "fallback_pairs" not in data


def test_the_log_names_the_field_that_was_fractional_not_the_entry(tmp_path, monkeypatch, capsys):
    # a whole entry, but the tail loop's source window starts half a bar in
    top = plan(168, 176, 5.0, end=190, swap_in=8, out_looped=6, out_loop_source_bars=(176.5, 184.5),
               alternatives=[alt(whole_plan(160, 168, 6.0))])
    data = tool.read_json(write_pair(monkeypatch, tmp_path, top))
    said = next(line for line in capsys.readouterr().out.splitlines() if "has a fractional bar" in line)
    assert "tail_loop source 176.5 is not a whole bar" in said
    assert "entry_out_bar" not in said
    assert "tail_loop source 176.5 is not a whole bar" in data["transitions"][0]["reason"]


def test_two_pairs_say_pairs_and_a_lower_ranked_cheaper_plan_says_so(tmp_path, monkeypatch, capsys):
    tops = iter([fractional_top(alternatives=[alt(whole_plan(152, 160, 8.37))]),
                 fractional_top(cost=10.0, alternatives=[alt(whole_plan(152, 160, 7.0))])])
    monkeypatch.setattr(tool, "plan_blend", lambda *_, **__: next(tops))
    monkeypatch.setattr(tool, "candidates", lambda *_, **__: [], raising=False)
    tool.write_decisions(tmp_path / "d.json", ["A", "B", "C"], [{"n_bars": OUT_BARS}] * 3)
    lines = capsys.readouterr().out.splitlines()
    assert any(line.endswith("cost 7.00 vs the planner's best 10.00 "
                             "(-3.00, ranked lower by the planner's own order))") for line in lines)
    assert lines[-1] == "2 pairs used a whole-bar alternative: T1 (+0.56), T2 (-3.00)"


def test_fractional_first_alternative_is_skipped_for_the_next_whole_one(tmp_path, monkeypatch, capsys):
    top = fractional_top(alternatives=[alt(fractional_plan()), alt(whole_plan(150, 160, 8.9))])
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top))["transitions"]
    assert decision["entry_out_bar"] == 150
    out = capsys.readouterr().out
    assert "used alternative 2 (entry 150, swap 160, end 184, cost 8.90 vs the planner's best 7.81 (+1.09))" in out
    assert "alternative 1 skipped" in out and "176.5 is not a whole bar" in out


def test_the_planners_own_order_picks_the_first_whole_plan_even_if_listed_last(tmp_path, monkeypatch, capsys):
    top = fractional_top(alternatives=[alt(whole_plan(140, 150, 9.9)), alt(whole_plan(152, 160, 8.4)),
                                       alt(whole_plan(150, 164, 9.1))])
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top))["transitions"]
    assert decision["entry_out_bar"] == 152
    assert "used alternative 2 (entry 152" in capsys.readouterr().out


def test_a_sane_plan_is_preferred_over_a_cheaper_one_sided_plan(tmp_path, monkeypatch, capsys):
    one_sided = whole_plan(150, 164, 8.0, paired=False, sane=False)
    sane = whole_plan(152, 160, 9.5, paired=True, sane=True)
    top = fractional_top(paired=True, sane=True, alternatives=[alt(one_sided), alt(sane)])
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top))["transitions"]
    assert decision["entry_out_bar"] == 152                 # not the cheaper one-sided 150
    assert "used alternative 2" in capsys.readouterr().out


def test_the_sane_tier_orders_the_search_even_when_the_top_is_not_sane(tmp_path, monkeypatch, capsys):
    # nothing refuses the cheaper one-sided plan here (the top is not sane), so only the planner's
    # own order, sane pairings first, keeps the pricier sane plan ahead of it
    one_sided = whole_plan(150, 164, 8.0, paired=False, sane=False)
    sane = whole_plan(152, 160, 9.5, paired=True, sane=True)
    top = fractional_top(alternatives=[alt(one_sided), alt(sane)])
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top))["transitions"]
    assert decision["entry_out_bar"] == 152
    assert "used alternative 2" in capsys.readouterr().out


def test_an_unsane_top_may_use_an_unsane_replacement_within_the_limit(tmp_path, monkeypatch, capsys):
    top = fractional_top(alternatives=[alt(whole_plan(150, 164, 8.0, paired=False, sane=False))])
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top))["transitions"]
    assert decision["entry_out_bar"] == 150
    assert "used alternative 1" in capsys.readouterr().out


@pytest.mark.parametrize("paired, reason", [(False, "one-sided"), (True, "not a sane pairing")])
def test_a_sane_top_refuses_a_replacement_that_is_not_sane(tmp_path, monkeypatch, capsys, paired, reason):
    top = fractional_top(paired=True, sane=True,
                         alternatives=[alt(whole_plan(150, 164, 8.0, paired=paired, sane=False))])
    with pytest.raises(ValueError, match="entry_out_bar 168.5 is not a whole bar"):
        write_pair(monkeypatch, tmp_path, top)
    out = capsys.readouterr().out
    assert "no whole-bar plan qualified" in out and f"nearest was +0.2, {reason}" in out
    assert "used alternative" not in out


def test_a_plan_beyond_the_alt_margin_is_refused_and_the_log_says_how_far(tmp_path, monkeypatch, capsys):
    top = fractional_top(cost=8.0, alternatives=[alt(whole_plan(152, 160, 8.0 + ALT_MARGIN + 0.5))])
    with pytest.raises(ValueError, match="not a whole bar"):
        write_pair(monkeypatch, tmp_path, top)
    out = capsys.readouterr().out
    assert (f"T1: no whole-bar plan qualified (limit +{ALT_MARGIN:.1f} over the best 8.00, "
            "sane if the best is sane, no outro cut, no flat stretch); nearest was +5.5, over the limit") in out


def test_a_plan_exactly_at_the_alt_margin_is_still_allowed(tmp_path, monkeypatch, capsys):
    top = fractional_top(cost=8.0, alternatives=[alt(whole_plan(152, 160, 8.0 + ALT_MARGIN))])
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top))["transitions"]
    assert decision["entry_out_bar"] == 152
    assert "(+5.00)" in capsys.readouterr().out


def test_the_nearest_refused_plan_is_the_one_the_log_names(tmp_path, monkeypatch, capsys):
    top = fractional_top(cost=8.0, alternatives=[alt(whole_plan(152, 160, 20.0)),
                                                 alt(whole_plan(150, 164, 14.0))])
    with pytest.raises(ValueError, match="not a whole bar"):
        write_pair(monkeypatch, tmp_path, top)
    assert "nearest was +6.0, over the limit" in capsys.readouterr().out


def test_an_alternative_the_decision_check_refuses_is_skipped(tmp_path, monkeypatch, capsys):
    # whole bars, but the swap cannot be reproduced (160 != 152 + 7): the normal check refuses it
    broken = plan(152, 160, 8.0, swap_in=7)
    top = fractional_top(alternatives=[alt(broken), alt(whole_plan(150, 160, 8.9))])
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top))["transitions"]
    assert decision["entry_out_bar"] == 150
    out = capsys.readouterr().out
    assert "alternative 1 skipped" in out and "cannot reproduce the planner swap" in out
    assert "used alternative 2" in out


def cutting_plan(entry, swap, cost):
    cut = whole_plan(entry, swap, cost, end=182)
    cut.out_cut_bars = 2                      # 182 - 184 == 0 - 2 - 0: valid for the decision check
    return cut


@pytest.mark.parametrize("listed", ["alternative", "ranked option"])
def test_a_plan_that_cuts_the_outro_is_refused_wherever_it_comes_from(tmp_path, monkeypatch, capsys, listed):
    cut = cutting_plan(146, 160, 8.0)
    fine = whole_plan(152, 160, 9.0)
    top = fractional_top(alternatives=[alt(cut)] if listed == "alternative" else [])
    ranked = [top, cut, fine] if listed == "ranked option" else [top, fine]
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top, ranked))["transitions"]
    assert decision["entry_out_bar"] == 152
    assert "cuts the outgoing's outro" in capsys.readouterr().out


def test_a_plan_with_a_flat_stretch_is_refused(tmp_path, monkeypatch, capsys):
    flat = whole_plan(148, 160, 8.0)
    flat.terms["flat"] = 2.0
    top = fractional_top(alternatives=[alt(flat), alt(whole_plan(152, 160, 9.0))])
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top))["transitions"]
    assert decision["entry_out_bar"] == 152
    assert "has a flat stretch" in capsys.readouterr().out


def test_ranked_options_are_used_when_no_alternative_is_whole(tmp_path, monkeypatch, capsys):
    top = fractional_top(alternatives=[alt(fractional_plan())])
    ranked = [top, whole_plan(144, 160, 9.5), whole_plan(152, 160, 8.5)]
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top, ranked))["transitions"]
    assert decision["entry_out_bar"] == 152
    out = capsys.readouterr().out
    assert "used ranked option 3 (entry 152, swap 160, end 184, cost 8.50 vs the planner's best 7.81 (+0.69))" in out
    assert "candidate" not in out


def test_alternatives_and_ranked_options_are_one_list_in_the_planners_order(tmp_path, monkeypatch, capsys):
    # a whole alternative AND a whole ranked option: the one the planner ranks first wins
    top = fractional_top(alternatives=[alt(whole_plan(150, 164, 8.4))])
    ranked = [top, whole_plan(152, 160, 9.5)]
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top, ranked))["transitions"]
    assert decision["entry_out_bar"] == 150 and "used alternative 1" in capsys.readouterr().out
    top = fractional_top(alternatives=[alt(whole_plan(150, 164, 9.5))])
    ranked = [top, whole_plan(152, 160, 8.4)]
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top, ranked))["transitions"]
    assert decision["entry_out_bar"] == 152 and "used ranked option 2" in capsys.readouterr().out


@pytest.mark.parametrize("allow_partial", [False, True])
def test_no_whole_plan_anywhere_falls_back_exactly_as_before(tmp_path, monkeypatch, capsys, allow_partial):
    top = fractional_top(alternatives=[alt(fractional_plan())])
    results = iter([plan(72, 88, 1.0, swap_in=16), top])
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
    assert "T2: no whole-bar plan among 1 ranked options" in lines
    assert not any("used a whole-bar alternative" in line for line in lines)


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


def non_fractional_tops():
    return {
        "infinity": plan(float("inf"), 176, 7.81, swap_in=7.5),
        "swap that cannot be reproduced": plan(152, 160, 7.81, swap_in=7),
        "tail loop without source bars": plan(152, 160, 7.81, end=190, out_looped=6),
    }


@pytest.mark.parametrize("kind, error", [
    ("infinity", "cannot convert float infinity to integer"),
    ("swap that cannot be reproduced", "cannot reproduce the planner swap"),
    ("tail loop without source bars", "tail_loop is missing its source bars"),
])
def test_only_a_fractional_bar_sends_the_planner_to_its_alternatives(
        tmp_path, monkeypatch, capsys, kind, error):
    top = non_fractional_tops()[kind]
    top.alternatives = [alt(whole_plan(150, 164, 8.37))]
    with pytest.raises(ValueError, match=error):
        write_pair(monkeypatch, tmp_path, top, [top, whole_plan(144, 160, 9.0)])
    out = capsys.readouterr().out
    assert "used alternative" not in out and "used ranked" not in out
    assert "no whole-bar plan" not in out


def odd_plan(failure):
    odd = whole_plan(146, 160, 8.0)
    if failure == "terms is None":
        odd.terms = None                                  # AttributeError in the screen
    elif failure == "cost is text":
        odd.cost = "cheap"                                # TypeError in the rank key
    else:
        odd.out_looped_bars = None                        # TypeError in the decision check
    return odd


@pytest.mark.parametrize("failure", ["terms is None", "cost is text", "looped is None"])
def test_an_odd_replacement_is_skipped_with_its_reason_and_never_crashes(
        tmp_path, monkeypatch, capsys, failure):
    top = fractional_top()
    ranked = [top, odd_plan(failure), whole_plan(152, 160, 9.0)]
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top, ranked))["transitions"]
    assert decision["entry_out_bar"] == 152
    out = capsys.readouterr().out
    assert "ranked option 2 skipped:" in out and "used ranked option 3" in out


def test_ranked_options_that_cannot_be_listed_are_reported_and_the_pair_falls_back(
        tmp_path, monkeypatch, capsys):
    def boom(*_, **__):
        raise KeyError("n_bars")

    top = fractional_top(alternatives=[alt(fractional_plan())])
    monkeypatch.setattr(tool, "plan_blend", lambda *_, **__: top)
    monkeypatch.setattr(tool, "candidates", boom, raising=False)
    with pytest.raises(ValueError, match="not a whole bar"):
        tool.write_decisions(tmp_path / "d.json", ["A", "B"], [{"n_bars": OUT_BARS}, {"n_bars": IN_BARS}])
    assert "the ranked options could not be listed: 'n_bars'" in capsys.readouterr().out


def test_skipped_lines_are_capped_at_five_per_pair_and_the_rest_counted(tmp_path, monkeypatch, capsys):
    ranked = [fractional_plan(8.0 + i / 1000) for i in range(8)]
    for i, option in enumerate(ranked):
        option.in_starts_at_out_bar = 100.5 + i
    top = fractional_top()
    ranked = [top, *ranked, whole_plan(152, 160, 9.0)]
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top, ranked))["transitions"]
    assert decision["entry_out_bar"] == 152
    out = capsys.readouterr().out
    assert out.count(" skipped: ") == 5 and "  and 3 more not whole-bar" in out.splitlines()


def test_the_search_stops_after_200_ranked_options_and_says_so(tmp_path, monkeypatch, capsys):
    ranked = []
    for i in range(250):
        option = fractional_plan(8.0 + i / 1000)
        option.in_starts_at_out_bar = 100.5 + i
        ranked.append(option)
    late = whole_plan(152, 160, 8.9)           # ranks 251st: beyond the search
    top = fractional_top()
    with pytest.raises(ValueError, match="not a whole bar"):
        write_pair(monkeypatch, tmp_path, top, [top, *ranked, late])
    out = capsys.readouterr().out
    assert "stopped after 200 ranked options for T1; 51 not tried" in out
    assert out.count(" skipped: ") == 5 and "and 195 more not whole-bar" in out
    assert "used ranked option" not in out


def test_a_whole_plan_inside_the_200_is_still_found(tmp_path, monkeypatch, capsys):
    ranked = []
    for i in range(150):
        option = fractional_plan(8.0 + i / 1000)
        option.in_starts_at_out_bar = 100.5 + i
        ranked.append(option)
    top = fractional_top()
    [decision] = tool.read_json(write_pair(monkeypatch, tmp_path, top,
                                           [top, *ranked, whole_plan(152, 160, 9.0)]))["transitions"]
    assert decision["entry_out_bar"] == 152
    assert "stopped after" not in capsys.readouterr().out


def test_whole_bar_top_never_looks_at_alternatives_and_writes_the_same_bytes(tmp_path, monkeypatch, capsys):
    plans = iter([
        BlendPlan(in_starts_at_out_bar=72, bass_switch_out_bar=88, out_end_bar=100, out_looped_bars=0,
                  in_intro_looped_bars=0, swap_incoming_bar=16, cost=3.25, why=["First pair."],
                  alternatives=[Alternative(BlendPlan(in_starts_at_out_bar=80.5, bass_switch_out_bar=90,
                                                      out_end_bar=100, out_looped_bars=0,
                                                      in_intro_looped_bars=0, swap_incoming_bar=9.5),
                                            90, "r", "f")]),
        BlendPlan(in_starts_at_out_bar=78, bass_switch_out_bar=92, out_end_bar=105, out_looped_bars=5,
                  in_intro_looped_bars=4, swap_incoming_bar=10, in_loop_phrase_end=8,
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
    results = iter([whole_plan(1, 2, 1.0, end=3),
                    plan(1.5, 2, 1.0, end=3, swap_in=0.5,
                         alternatives=[alt(whole_plan(1, 2, 1.5, end=3))])])
    monkeypatch.setattr(tool, "plan_blend", lambda *_, **__: next(results))
    monkeypatch.setattr(tool, "candidates", lambda *_, **__: [], raising=False)
    args, output = planner_inputs
    assert tool.main([*args, "--allow-partial"]) == 0
    data = tool.read_json(output)
    assert [row["pair_index"] for row in data["transitions"]] == [1, 2]
    assert data["fallback_pairs"] == []
    assert data["whole_bar_alternative_count"] == 1
    assert [(row["pair_index"], row["cost_difference"]) for row in data["whole_bar_alternatives"]] == [(2, 0.5)]
    assert "1 pair used a whole-bar alternative: T2 (+0.50)" in capsys.readouterr().out.splitlines()


# ---- the real planner, no stubs ------------------------------------------------------------
PAIR3 = Path(__file__).parent / "Fixtures" / "planner_pair3.json"


def pair3():
    data = json.loads(PAIR3.read_text(encoding="ascii"))
    return data["outgoing"], data["incoming"]


def test_real_planner_pair3_ends_on_the_whole_bar_alternative(tmp_path, capsys):
    outgoing, incoming = pair3()
    top = plan_blend(outgoing, incoming)
    assert top.in_starts_at_out_bar == 168.5                  # the failure the fresh mix found
    path = tmp_path / "decisions.json"
    tool.write_decisions(path, ["A", "B"], [outgoing, incoming])
    [decision] = tool.read_json(path)["transitions"]
    assert (decision["entry_out_bar"], decision["out_end_bar"]) == (152, 184)
    assert decision["entry_out_bar"] + decision["swap_in_bar"] == 160
    assert ("used alternative 1 (entry 152, swap 160, end 184, cost 8.37 vs the planner's best 7.81 (+0.56))"
            in capsys.readouterr().out)


def test_real_ranked_options_find_the_same_plan_when_the_alternatives_are_gone(tmp_path, monkeypatch, capsys):
    outgoing, incoming = pair3()

    def without_alternatives(out_track, in_track, **kwargs):
        top = plan_blend(out_track, in_track, **kwargs)
        top.alternatives = []
        return top

    monkeypatch.setattr(tool, "plan_blend", without_alternatives)
    path = tmp_path / "decisions.json"
    tool.write_decisions(path, ["A", "B"], [outgoing, incoming])
    [decision] = tool.read_json(path)["transitions"]
    assert (decision["entry_out_bar"], decision["out_end_bar"]) == (152, 184)
    assert "used ranked option" in capsys.readouterr().out


# ---- the consumers ---------------------------------------------------------------------------
def test_the_arranger_reads_a_decisions_file_with_the_substitution_record(blend_layout):
    als, sections, output, decisions = blend_layout()
    plain = propose_arrangement(als, sections, output, decisions_path=decisions)
    data = tool.read_json(decisions)
    said = ("T1: top plan (entry 168.5, cost 7.81) has a fractional bar (decision for pair 1: "
            "entry_out_bar 168.5 is not a whole bar); used alternative 1 (entry 152, swap 160, "
            "end 184, cost 8.37 vs the planner's best 7.81 (+0.56))")
    data["transitions"][0]["reason"] = said + " | the planner's own reasons"
    data["whole_bar_alternative_count"] = 1
    data["whole_bar_alternatives"] = [{"pair_index": 1, "used": "alternative 1", "top_entry_out_bar": 168.5,
                                       "top_error": "entry_out_bar 168.5 is not a whole bar",
                                       "cost_difference": 0.5563}]
    tool.write_json_atomically(decisions, data)
    marked = propose_arrangement(als, sections, output.with_name("marked.als"), decisions_path=decisions)
    assert [ov.decision_exit for ov in marked.overlaps] == [ov.decision_exit for ov in plain.overlaps]
    assert [ov.decision_exit for ov in marked.overlaps][0] is not None


# ---- vocal flags and keys for the planner's clash terms ----------------------------------------
def lead_two_result():
    return {
        "bpm": 120, "n_bars": 20, "lead_in_bars": 2,
        "analysis": {"section_detector": "groove-1", "sections_schema": 2},
        "sections": [{"start_bar": 0, "start_sec": 4, "label": "intro"},
                     {"start_bar": 16, "start_sec": 36, "label": "drop"}],
        "signals": {"fill_sections": [], "vocal_regions": [[20.0, 36.0, 8, 16], [60.0, 62.0, 28, 29]]},
    }


def test_converter_marks_vocal_bars_on_the_same_bar_clock_as_the_other_content(tmp_path):
    cache = tmp_path / "track__stemenv.npz"
    np.savez(cache, hop_t=0.25, bass=np.ones(176), drums=np.ones(176), tiera_band_low=np.ones(176),
             vocals=np.zeros(176), other=np.ones(176))
    track = tool.convert_track(lead_two_result(), cache)
    flags = track["content"]["has_vocal"]
    assert track["lead_in_bars"] == 2
    assert len(flags) == len(track["content"]["has_kick"]) == 20
    assert [bar for bar, flag in enumerate(flags) if flag] == list(range(8, 16))   # the region at bars 8-16, exactly
    assert track["content"]["has_music"] == [1] * 20


def test_converter_leaves_has_vocal_out_when_the_detector_recorded_no_vocal_signal(tmp_path):
    result = lead_two_result()
    del result["signals"]["vocal_regions"]
    cache = tmp_path / "track__stemenv.npz"
    np.savez(cache, hop_t=0.25, bass=np.ones(176), drums=np.ones(176), tiera_band_low=np.ones(176),
             vocals=np.zeros(176), other=np.ones(176))
    assert "has_vocal" not in tool.convert_track(result, cache)["content"]
    result["signals"]["vocal_regions"] = []
    assert tool.convert_track(result, cache)["content"]["has_vocal"] == [0] * 20


def test_vocal_regions_past_the_track_or_malformed_are_clipped_or_skipped():
    result = {"signals": {"vocal_regions": [[0, 0, 17, 40], [0, 0], [0, 0, float("nan"), 3], [0, 0, -3, 2]]}}
    assert tool.vocal_bars(result, 20) == [1, 1] + [0] * 15 + [1] * 3
    assert tool.vocal_bars({}, 20) is None


def test_read_camelot_uses_the_file_tags_then_the_mik_database(tmp_path, monkeypatch):
    import automated_dj_mixes.mik_reader as mik
    wav = tmp_path / "track.wav"
    wav.write_bytes(b"")
    tags = SimpleNamespace(key="Am")
    monkeypatch.setattr(mik, "read_mik_from_tags", lambda path: tags)
    monkeypatch.setattr(mik, "read_mik_db_track", lambda path: SimpleNamespace(key="12A"))
    assert tool.read_camelot(wav) == "8A"
    tags.key = None
    assert tool.read_camelot(wav) == "12A"
    monkeypatch.setattr(mik, "read_mik_db_track", lambda path: None)
    assert tool.read_camelot(wav) is None
    monkeypatch.setattr(mik, "read_mik_db_track", lambda path: SimpleNamespace(key="not a key"))
    assert tool.read_camelot(wav) is None
    assert tool.read_camelot(tmp_path / "missing.wav") is None


def test_read_camelot_gives_none_when_the_mik_reader_fails(tmp_path, monkeypatch):
    import automated_dj_mixes.mik_reader as mik
    wav = tmp_path / "track.wav"
    wav.write_bytes(b"")

    def broken(path):
        raise OSError("database is locked")

    monkeypatch.setattr(mik, "read_mik_from_tags", broken)
    assert tool.read_camelot(wav) is None


def test_the_blend_line_says_vocal_overlap_and_key_or_that_it_is_unknown():
    unknown = BlendPlan(1, 2, 3, 0, 0)
    assert tool.clash_line(unknown) == "vocals unknown; key unknown"
    clash = BlendPlan(1, 2, 3, 0, 0, vocal_clash_bars=6, key_clash=48.0, key_steps=6, key_tonal_bars=12,
                      key_pair=("8A", "3B"))
    assert tool.clash_line(clash) == "vocals overlap 6 bars; key 8A to 3B clash (6 steps) over 12 tonal bars"
    fine = BlendPlan(1, 2, 3, 0, 0, vocal_clash_bars=0, key_clash=0.0, key_steps=1, key_pair=("8A", "9A"))
    assert tool.clash_line(fine) == "vocals overlap 0 bars; key 8A to 9A compatible"


def test_the_report_prints_the_line_for_the_top_plan_and_every_alternative(capsys):
    top = BlendPlan(1, 2, 3, 0, 0, vocal_clash_bars=4, key_steps=3, key_pair=("8A", "11B"), key_tonal_bars=5)
    tool.print_plan(top, "Top")
    assert "vocals overlap 4 bars; key 8A to 11B clash (3 steps) over 5 tonal bars" in capsys.readouterr().out
    vocals_only = BlendPlan(1, 2, 3, 0, 0, vocal_clash_bars=0)
    tool.print_plan(vocals_only, "Alternative 1")
    assert "vocals overlap 0 bars; key unknown" in capsys.readouterr().out


def test_a_plan_made_without_vocal_or_key_data_prints_no_clash_line(capsys):
    tool.print_plan(BlendPlan(1, 2, 3, 0, 0), "Top")
    assert "vocals" not in capsys.readouterr().out


def test_cli_hands_the_planner_the_vocal_flags_and_the_key(planner_inputs, monkeypatch, tmp_path):
    args, output = planner_inputs
    result = groove_result()
    result["signals"]["vocal_regions"] = [[0, 2, 0, 1]]
    for name in "ABC":
        (tmp_path / "_Stem Analysis" / f"SECTIONS_STEM_{name}.json").write_text(json.dumps(result), encoding="ascii")
    seen = []
    keys = {"A.wav": "8A", "B.wav": None, "C.wav": "3B"}
    monkeypatch.setattr(tool, "read_camelot", lambda path: keys[path.name])
    monkeypatch.setattr(tool, "plan_blend",
                        lambda out, inc, **_: seen.append((out, inc)) or BlendPlan(1, 2, 3, 0, 0, swap_incoming_bar=1))
    content = tmp_path / "planner_content.json"
    assert tool.main([*args, "--content", str(content)]) == 0
    tracks = tool.read_json(content)["tracks"]
    assert [tracks[n]["key"] for n in "ABC"] == ["8A", None, "3B"]
    assert tracks["A"]["content"]["has_vocal"] == [1, 0, 0]
    assert seen[0][0]["key"] == "8A" and seen[0][1]["key"] is None
