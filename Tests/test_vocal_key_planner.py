"""Vocal clash and key clash as soft costs in the blend planner (Source/phrase_blend.py).

Both terms read OPTIONAL data: content["has_vocal"] (per bar) and track["key"] (a Camelot code).  A track
without them must give the plan it always gave: the digest test pins every candidate (key, cost, terms, why)
of the pair-3 fixture and the seven DFTD blends against digests made with the planner as it stood before
the terms existed (Tests/Fixtures/planner_goldens_pre_vocal_key.json).
Run:  python -I -m pytest Tests/test_vocal_key_planner.py
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))
sys.path.insert(0, str(ROOT / "Tools"))
import phrase_blend as pb  # noqa: E402
import score_blends as sb  # noqa: E402
from test_phrase_blend import IN, OUT, with_content  # noqa: E402

FIXTURES = Path(__file__).parent / "Fixtures"
GOLDENS = json.loads((FIXTURES / "planner_goldens_pre_vocal_key.json").read_text(encoding="ascii"))


def regions_to_flags(n, regions):
    flags = [0] * n
    for start, end in regions:
        for bar in range(start, end):
            flags[bar] = 1
    return flags


def full(track, kick=1, bass=1, music=1):
    n = int(track["n_bars"])
    return with_content(track, [bass] * n, [music] * n, [kick] * n)


def with_vocal(track, regions):
    t = json.loads(json.dumps(track))
    t["content"]["has_vocal"] = regions_to_flags(int(t["n_bars"]), regions)
    return t


def with_key(track, key):
    t = json.loads(json.dumps(track))
    t["key"] = key
    return t


def score(out, ins, a, s, e):
    return pb._score(out, ins, a, s, e, 0.0, 0.0, pb.phrase_points(out), pb.phrase_points(ins), pb.R6_BE)


def digest(o, n):
    cs = pb.candidates(o, n)
    top = pb.plan_blend(o, n)
    blob = json.dumps([[list(c.full_key()), round(c.cost, 9), sorted((k, round(v, 9)) for k, v in c.terms.items()),
                        c.why] for c in cs])
    return {"candidates": len(cs), "top": list(top.full_key()) if top else None,
            "top_cost": round(top.cost, 9) if top else None,
            "alternatives": [list(a.plan.full_key()) for a in top.alternatives] if top else None,
            "sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest()}


# ---- old inputs are untouched -----------------------------------------------------------------
def _dftd(name):
    key = json.loads(sb.KEY.read_text(encoding="utf-8"))
    content = json.loads(sb.CONTENT.read_text(encoding="utf-8"))
    t = json.loads(json.dumps(key["tracks"][name]))
    c = content["tracks"].get(name)
    if c:
        t["content"] = {k: c[k] for k in ("has_bass", "has_music", "has_kick") if k in c}
    return t


def test_pair3_fixture_gives_exactly_the_old_candidates():
    pair = json.loads((FIXTURES / "planner_pair3.json").read_text(encoding="utf-8"))
    assert "has_vocal" not in pair["outgoing"]["content"] and "key" not in pair["outgoing"]
    assert digest(pair["outgoing"], pair["incoming"]) == GOLDENS["pair3"]


@pytest.mark.parametrize("i", range(1, 8))
def test_dftd_answer_key_blends_without_vocal_or_key_give_exactly_the_old_candidates(i):
    b = json.loads(sb.KEY.read_text(encoding="utf-8"))["blends"][i - 1]
    assert digest(_dftd(b["out"]), _dftd(b["in"])) == GOLDENS["dftd_%d" % i]


def test_unknown_data_leaves_fields_unknown_and_says_nothing():
    plan = score(full(OUT), full(IN), 168, 184, 200)
    assert (plan.vocal_clash_bars, plan.key_clash, plan.key_steps, plan.key_pair) == (None, None, None, None)
    assert "vocal_clash" not in plan.terms and "key_clash" not in plan.terms
    assert not any("vocal" in w or "key" in w for w in plan.why)


# ---- vocal clash ------------------------------------------------------------------------------
# full(OUT) vs full(IN): plans (168, 184, 200) 0.5, (168, 184, 199) 1.25, (160, 184, 192) 3.9, (176, 192, 207) 4.0.
# Vocals on the outgoing at out bars 168-175 and 184-191, on the incoming at its bars 0-7 and 16-23: the
# top plan (entry 168) lines every incoming vocal bar up with an outgoing one (16 bars), entry 160 and 176
# line up none.
OUT_V = [(168, 176), (184, 192)]
IN_V = [(0, 8), (16, 24)]


def vocal_pair():
    return with_vocal(full(OUT), OUT_V), with_vocal(full(IN), IN_V)


def test_vocal_overlap_moves_the_pick_to_an_equal_cost_alternative(monkeypatch):
    o, n = vocal_pair()
    monkeypatch.setattr(pb, "W_VOCAL_CLASH", 0.0)
    old_top = pb.plan_blend(o, n)
    assert old_top.key() == (168, 184, 200) and old_top.vocal_clash_bars == 16
    monkeypatch.undo()
    top = pb.plan_blend(o, n)
    assert top.key() == (160, 184, 192) and top.vocal_clash_bars == 0
    assert top.key() != old_top.key()
    # the clash is still on the list: the old top plan is a ranked candidate carrying the cost
    clash = next(c for c in pb.candidates(o, n) if c.key() == (168, 184, 200))
    assert clash.vocal_clash_bars == 16 and clash.terms["vocal_clash"] == pytest.approx(pb.W_VOCAL_CLASH * 16)
    assert clash.cost == pytest.approx(0.5 + pb.W_VOCAL_CLASH * 16)


def test_every_option_clashing_still_plans_and_reports_the_clash():
    o = with_vocal(full(OUT), [(0, 200)])
    n = with_vocal(full(IN), [(0, 160)])
    top = pb.plan_blend(o, n)
    assert top is not None
    bars = top.out_end_bar - top.in_starts_at_out_bar
    assert top.vocal_clash_bars == bars > 0
    assert top.terms["vocal_clash"] == pytest.approx(min(pb.W_VOCAL_CLASH * bars, pb.MAX_CLASH_COST))
    assert all(c.vocal_clash_bars > 0 for c in pb.candidates(o, n))
    assert any("vocals overlap %g bars" % bars in w for w in top.why)


def test_a_vocal_on_only_one_track_costs_nothing():
    out_only = score(with_vocal(full(OUT), [(0, 200)]), with_vocal(full(IN), []), 168, 184, 200)
    in_only = score(with_vocal(full(OUT), []), with_vocal(full(IN), [(0, 160)]), 168, 184, 200)
    assert out_only.vocal_clash_bars == 0 and in_only.vocal_clash_bars == 0
    assert "vocal_clash" not in out_only.terms and "vocal_clash" not in in_only.terms
    assert out_only.cost == in_only.cost == score(full(OUT), full(IN), 168, 184, 200).cost


def test_the_whole_overlap_counts_not_only_the_bars_before_the_swap():
    # swap at out bar 184: the outgoing vocal at 190-199 meets the incoming vocal at its bars 22-31, all AFTER the swap
    o, n = with_vocal(full(OUT), [(190, 200)]), with_vocal(full(IN), [(22, 32)])
    plan = score(o, n, 168, 184, 200)
    assert plan.vocal_clash_bars == 10
    assert plan.terms["vocal_clash"] == pytest.approx(pb.W_VOCAL_CLASH * 10)
    # and the bars before the swap count too
    early = score(with_vocal(full(OUT), [(170, 180)]), with_vocal(full(IN), [(2, 12)]), 168, 184, 200)
    assert early.vocal_clash_bars == 10


def test_vocals_are_read_from_each_track_on_its_own_clock():
    # the outgoing vocal is at out bars 168-175; the incoming vocal at ITS bars 0-7 plays at out bars 168-175 too
    plan = score(with_vocal(full(OUT), [(168, 176)]), with_vocal(full(IN), [(0, 8)]), 168, 184, 200)
    assert plan.vocal_clash_bars == 8
    # the incoming vocal at ITS bars 8-15 plays at out bars 176-183, clear of the outgoing vocal: no clash.  A planner
    # that read the outgoing's list for both tracks would call this 8 bars.
    clear = score(with_vocal(full(OUT), [(168, 176)]), with_vocal(full(IN), [(8, 16)]), 168, 184, 200)
    assert clear.vocal_clash_bars == 0
    # a looped outgoing tail reads its loop source, an intro loop reads the incoming loop bars
    looped = pb.candidates(with_vocal(full(OUT), [(0, 200)]), with_vocal(full(IN), [(0, 160)]))
    assert any(c.out_looped_bars > 0 and c.vocal_clash_bars == c.out_end_bar - c.in_starts_at_out_bar
               for c in looped)


def test_a_track_without_has_vocal_in_the_pair_gives_no_vocal_number():
    plan = score(with_vocal(full(OUT), [(0, 200)]), full(IN), 168, 184, 200)
    assert plan.vocal_clash_bars is None and "vocal_clash" not in plan.terms


def test_misaligned_vocal_list_is_ignored():
    o = with_vocal(full(OUT), [(0, 200)])
    o["content"]["has_vocal"] = o["content"]["has_vocal"][:50]
    plan = score(o, with_vocal(full(IN), [(0, 160)]), 168, 184, 200)
    assert plan.vocal_clash_bars is None


# ---- key clash --------------------------------------------------------------------------------
def keyed(key_out, key_in, music=1):
    return (with_key(full(OUT, music=music), key_out), with_key(full(IN, music=music), key_in))


@pytest.mark.parametrize("a,b,steps", [
    ("8A", "8A", 0), ("8A", "9A", 1), ("8A", "7A", 1), ("8A", "8B", 1), ("12A", "1A", 1), ("8A", "9B", 2),
    ("8A", "10A", 2), ("8A", "2A", 6), ("8A", "2B", 7), ("1A", "8A", 5),
])
def test_camelot_steps(a, b, steps):
    assert pb.camelot_steps(a, b) == pb.camelot_steps(b, a) == steps


@pytest.mark.parametrize("bad", [None, "", "Am", "13A", "0B", "8C", "A8", 8])
def test_camelot_steps_refuses_anything_that_is_not_a_camelot_code(bad):
    assert pb.camelot_steps(bad, "8A") is None and pb.camelot_steps("8A", bad) is None


def test_compatible_keys_cost_nothing():
    for k in ("8A", "9A", "8B"):
        plan = score(*keyed("8A", k), 168, 184, 200)
        assert plan.key_clash == 0 and "key_clash" not in plan.terms
        assert plan.key_steps <= 1 and plan.key_pair == ("8A", k)


def test_a_far_pair_over_tonal_bars_costs_per_step_and_bar():
    plan = score(*keyed("8A", "2A"), 168, 184, 200)           # 6 steps: 5 beyond compatible, counted as KEY_STEP_CAP
    bars = plan.out_end_bar - plan.in_starts_at_out_bar
    assert plan.key_steps == 6 and plan.key_tonal_bars == bars == 32
    assert plan.key_clash == pb.KEY_STEP_CAP * bars
    assert plan.terms["key_clash"] == pytest.approx(min(pb.W_KEY_CLASH * pb.KEY_STEP_CAP * bars, pb.MAX_CLASH_COST))
    near = score(*keyed("8A", "10A"), 168, 184, 200)           # 2 steps: 1 beyond compatible
    assert near.key_clash == bars and 0 < near.terms["key_clash"] < plan.terms["key_clash"]
    assert plan.cost > near.cost > score(*keyed("8A", "8A"), 168, 184, 200).cost


def test_the_same_far_pair_over_kick_and_bass_only_costs_nothing():
    plan = score(*keyed("8A", "2A", music=0), 168, 184, 200)
    assert plan.key_steps == 6 and plan.key_tonal_bars == 0 and plan.key_clash == 0
    assert "key_clash" not in plan.terms
    assert plan.cost == score(*keyed("8A", "8A", music=0), 168, 184, 200).cost


def test_only_bars_where_both_tracks_are_tonal_count():
    o = with_key(with_content(OUT, [1] * 200, [1 if 180 <= b < 190 else 0 for b in range(200)], [1] * 200), "8A")
    n = with_key(with_content(IN, [1] * 160, [1] * 160, [1] * 160), "2A")
    plan = score(o, n, 168, 184, 200)
    assert plan.key_tonal_bars == 10 and plan.key_clash == pb.KEY_STEP_CAP * 10


def test_unknown_key_costs_nothing_and_says_so():
    base = score(full(OUT), full(IN), 168, 184, 200)
    for k_out, k_in, who in ((None, "2A", "the outgoing"), ("8A", None, "the incoming"), ("Am", "2A", "the outgoing")):
        o, n = with_key(full(OUT), k_out), with_key(full(IN), k_in)
        plan = score(o, n, 168, 184, 200)
        assert plan.key_clash is None and plan.key_pair is None and plan.cost == base.cost
        assert "key_clash" not in plan.terms
        assert any("key unknown for %s" % who in w for w in plan.why)
    both = score(with_key(full(OUT), "zz"), with_key(full(IN), "yy"), 168, 184, 200)
    assert any("key unknown for both" in w for w in both.why)


def test_key_clash_moves_the_pick_to_an_option_with_less_tonal_overlap():
    # same shapes as the vocal test, as music: the top plan (entry 168) lines every incoming music bar up with
    # an outgoing music bar (16 tonal bars); entry 160 lines up none
    o = with_content(OUT, [1] * 200, regions_to_flags(200, OUT_V), [1] * 200)
    n = with_content(IN, [1] * 160, regions_to_flags(160, IN_V), [1] * 160)
    same = pb.plan_blend(with_key(o, "8A"), with_key(n, "8A"))
    far = pb.plan_blend(with_key(o, "8A"), with_key(n, "2A"))
    assert same.key() == (168, 184, 200)
    assert far.key() == (160, 184, 192) and far.key_clash == 0 and far.key_tonal_bars == 0
    clash = next(c for c in pb.candidates(with_key(o, "8A"), with_key(n, "2A")) if c.key() == (168, 184, 200))
    assert clash.key_tonal_bars == 16 and clash.key_clash == pb.KEY_STEP_CAP * 16


# ---- the terms never veto ---------------------------------------------------------------------
def test_clash_terms_are_capped_below_the_forbidden_cut_cost():
    o = with_key(with_vocal(full(OUT), [(0, 200)]), "8A")
    n = with_key(with_vocal(full(IN), [(0, 160)]), "2A")
    plan = score(o, n, 120, 184, 200)                          # an 80 bar overlap that is vocal and tonal throughout
    assert plan.vocal_clash_bars == plan.key_tonal_bars == 80
    assert plan.terms["vocal_clash"] == plan.terms["key_clash"] == pb.MAX_CLASH_COST
    assert pb.MAX_CLASH_COST < pb.W_CUT


def test_weights_stay_inside_sams_own_blends():
    # a phrase (8 bars) of vocal clash costs no more than moving the swap by a phrase (8 x 0.25 = 2.0)
    assert pb.W_VOCAL_CLASH * 8 <= 8 * pb.W_SWITCH_TIME + 1e-9
    # his worst key clash (DFTD blend 7: 3 tonal bars, 6 steps = 12 step-bars) costs less than the 0.94 by which
    # his placement beats the best clash-free plan
    assert pb.W_KEY_CLASH * pb.KEY_STEP_CAP * 3 < 0.94
    assert 0 < pb.W_VOCAL_CLASH and 0 < pb.W_KEY_CLASH and pb.MAX_CLASH_COST < pb.W_CUT


def test_dftd_answer_key_still_scores_seven_of_seven_with_vocals_and_keys_in():
    data = json.loads(sb.VOCAL_KEY.read_text(encoding="utf-8"))["tracks"]
    assert all(t["key"] and any(t["has_vocal"]) for t in data.values())
    exact, rows = sb.score("BOTTOM END", out=lambda *_: None)
    assert exact == 7
    assert any(r["plan"].key_pair for r in rows) and all(r["plan"].vocal_clash_bars is not None for r in rows)


def test_the_tweak_replay_record_carries_the_clash_numbers():
    import tweak_replay as tr
    o, n = vocal_pair()
    record = tr._plan_json(score(with_key(o, "8A"), with_key(n, "2A"), 168, 184, 200))
    assert record["vocal_clash_bars"] == 16 and record["key_steps"] == 6
    assert record["key_clash"] == pb.KEY_STEP_CAP * record["key_tonal_bars"] > 0
    assert tr._plan_json(score(full(OUT), full(IN), 168, 184, 200))["vocal_clash_bars"] is None


def test_measure_tool_tabulates_sams_dftd_blends_against_the_planner(monkeypatch):
    import measure_vocal_key as mvk
    lines = []
    rows = mvk.dftd_table(out=lines.append)
    assert len(rows) == 7 and all(sam.key() == plan.key() for sam, plan in rows)
    assert all(sam.vocal_clash_bars == 0 for sam, _ in rows)            # Sam leaves no vocal overlap in the seven
    assert max(sam.key_steps for sam, _ in rows) == 6
    assert any("8A>2A 6 steps" in line for line in lines) and all(line.isascii() for line in lines)
    monkeypatch.setattr(pb, "W_VOCAL_CLASH", 0.0)
    monkeypatch.setattr(pb, "W_KEY_CLASH", 0.0)
    old = [plan.key() for _, plan in mvk.dftd_table(out=lambda *_: None)]
    assert old == [plan.key() for _, plan in rows]
