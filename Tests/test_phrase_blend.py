"""Tests for Source/phrase_blend.py: the six answer-key blends pinned (as characterisation, not as
validation: see the module docstring), plus unit tests per rule R1..R9 and per round-4 change.
Run:  python -I -m pytest Tests/test_phrase_blend.py

Synthetic tracks are small on purpose so each test isolates one rule.  Where a test compares
costs it uses literals or relations that survive zeroing a weight, so removing a rule fails it.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))
sys.path.insert(0, str(ROOT / "Tools"))
import phrase_blend as pb  # noqa: E402
import score_blends as sb  # noqa: E402

KEY = json.loads(sb.KEY.read_text(encoding="utf-8"))
CONTENT = json.loads(sb.CONTENT.read_text(encoding="utf-8"))


def trk(n, sections, fills=(), bpm=128.0):
    return {"bpm": bpm, "n_bars": n, "sections": [list(s) for s in sections],
            "fills": [list(f) for f in fills]}


def with_content(track, bass, music, kick=None):
    t = json.loads(json.dumps(track))
    t["content"] = {"has_bass": list(bass), "has_music": list(music)}
    if kick is not None:
        t["content"]["has_kick"] = list(kick)
    return t


# A plain outgoing track: 200 bars, bass and drop to its very last bar (no outro, R8) with fills.
OUT = trk(200, [(0, "INTRO"), (16, "DROP"), (100, "BREAK"), (120, "DROP")],
          fills=[(135, 136), (151, 152), (167, 168), (183, 184)])
# A plain incoming track: short quiet intro, drop at 16, break at 32, drop at 48.
IN = trk(160, [(0, "INTRO"), (16, "DROP"), (32, "BREAK"), (48, "DROP")], fills=[(31, 32)])


def _cost(out, ins, a, s, e, r6=pb.R6_BE):
    return pb._score(out, ins, a, s, e, 0.0, 0.0, pb.phrase_points(out), pb.phrase_points(ins), r6)


# ---- the six answer-key blends, pinned -----------------------------------------------------
SIX = [  # (blend index, in, switch, end)
    (0, 184, 200, 215),
    (1, 160, 176, 193),
    (2, 112, 128, 144),
    (3, 160, 176, 192),
    (4, 122, 138, 154),
    (5, 144, 160, 176),
]


@pytest.mark.parametrize("idx,a,s,e", SIX)
def test_six_blends_with_carries_overrides(idx, a, s, e):
    b = KEY["blends"][idx]
    o = sb.apply_overrides(KEY["tracks"][b["out"]], b["out"], sb.OVERRIDES)
    n = sb.apply_overrides(KEY["tracks"][b["in"]], b["in"], sb.OVERRIDES)
    plan = pb.plan_blend(o, n, r6=pb.R6_V1)   # the OVERRIDES column is the round 2 rule
    assert plan.key() == (a, s, e)
    assert plan.flat_bars == 0


def _exact_vector(mode):
    key, vec = KEY, []
    for b in key["blends"]:
        o = sb.prepare(key["tracks"][b["out"]], b["out"], mode, CONTENT)
        n = sb.prepare(key["tracks"][b["in"]], b["in"], mode, CONTENT)
        sam = sb.sam_numbers(b, o)
        p = pb.plan_blend(o, n, r6=sb.MODES[mode][1])
        vec.append(bool(p and p.in_intro_looped_bars == 0 and p.key() == tuple(float(x) for x in sam)))
    return vec


def test_score_columns_are_pinned_as_measured_in_round_4():
    # Characterisation, not validation: the six are few, the rules were written from them, and
    # in all six the switch is entry + 16.  Pinned so any change in what the planner picks shows.
    assert _exact_vector("MEASURED") == [True] * 6
    assert _exact_vector("LABELS") == [True, True, False, True, True, True]
    assert _exact_vector("OVERRIDES") == [True] * 6
    assert _exact_vector("BOTTOM END") == [True] * 6


def test_headline_claims_are_true_and_stated():
    # the module docstring says R6 is inert on the six and the switch is entry + 16 in all six
    assert "INERT" in pb.__doc__ and "entry + 16" in pb.__doc__ and "END_SLACK_BARS from blend 2" in pb.__doc__
    assert "not tuned to pass" not in pb.__doc__
    assert {b["bass_switch_out_bar"] - b["in_starts_at_out_bar"] for b in KEY["blends"]} == {16}


def test_r6_is_inert_on_the_six_with_the_flat_cost_removed(monkeypatch):
    base = _exact_vector("BOTTOM END")
    monkeypatch.setattr(pb, "W_FLAT_RUN", 0.0)
    assert _exact_vector("BOTTOM END") == base


def test_bottom_end_flat_runs_on_sams_placements_are_pinned():
    rows = []
    for b in KEY["blends"]:
        o = sb.prepare(KEY["tracks"][b["out"]], b["out"], "BOTTOM END", CONTENT)
        n = sb.prepare(KEY["tracks"][b["in"]], b["in"], "BOTTOM END", CONTENT)
        sam = sb.sam_numbers(b, o)
        rows.append(pb.flat_runs(pb.content_check(o, n, *sam, r6=pb.R6_BE)[0]))
    # blend 2: only PAX's last, partial bar 192, where nothing is left
    assert rows == [[], [(192.0, 1.0)], [], [], [], []]


def test_blends_3_4_6_are_flat_by_label_without_overrides():
    flat = {}
    for idx, a, s, e in (SIX[2], SIX[3], SIX[5]):
        b = KEY["blends"][idx]
        o, n = KEY["tracks"][b["out"]], KEY["tracks"][b["in"]]
        flat[idx] = pb.content_check(o, n, a, s, e, r6=pb.R6_V1)[1]
    assert flat == {2: 16.0, 3: 32.0, 5: 16.0}


def test_key_file_is_not_edited_by_overrides():
    t = KEY["tracks"]["Ninetoes - Come Back"]
    assert all(len(s) == 2 for s in t["sections"])
    c = sb.apply_overrides(t, "Ninetoes - Come Back", sb.OVERRIDES)
    assert any(len(s) == 3 for s in c["sections"]) and all(len(s) == 2 for s in t["sections"])


# ---- R1: entry on an outgoing phrase point, normally a fill end; the one-minute awareness -----
def test_r1_entry_prefers_fill_end():
    plan = pb.plan_blend(OUT, IN)
    assert plan.in_starts_at_out_bar in {f[1] for f in OUT["fills"]}


def test_r1_fill_end_costs_less_than_a_section_start_at_identical_geometry():
    base = [(0, "INTRO"), (16, "DROP")]
    o_fill = trk(200, base, fills=[(167, 168), (183, 184)])
    o_sec = trk(200, base + [(168, "DROP")], fills=[(183, 184)])
    ins = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    c_fill, c_sec = _cost(o_fill, ins, 168, 184, 200), _cost(o_sec, ins, 168, 184, 200)
    assert c_sec.cost - c_fill.cost > 0.5            # literal: survives zeroing W_ENTRY_SECTION? no
    assert "entry" in c_sec.terms and "entry" not in c_fill.terms


def test_r1_entry_cost_order_fill_end_then_section_start_then_unphrased():
    base = [(0, "INTRO"), (16, "DROP")]
    o_fill = trk(200, base, fills=[(168, 169), (183, 184)])
    o_sec = trk(200, base + [(167, "DROP")], fills=[(183, 184)])
    o_off = trk(200, base, fills=[(183, 184)])
    ins = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    c_fill = _cost(o_fill, ins, 169, 184, 200).cost
    c_sec = _cost(o_sec, ins, 167, 184, 200).cost
    c_off = _cost(o_off, ins, 167, 184, 200).cost
    assert c_fill < c_sec < c_off
    assert c_off - c_sec > 3 and c_sec - c_fill > 0.5


def test_r1_aims_for_about_a_minute():
    plan = pb.plan_blend(OUT, IN)
    assert plan.in_starts_at_out_bar == 168            # 32 bars from the end, a fill end
    assert 28 <= plan.out_end_bar - plan.in_starts_at_out_bar <= 36


def test_awareness_one_minute_mark_is_reported_and_mildly_preferred():
    # 128 bpm: a minute is 32 bars; out n = 200, so the mark is bar 168
    out = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(167, 168), (171, 172), (183, 184)])
    ins = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    at_mark = _cost(out, ins, 168, 184, 200)
    off_mark = _cost(out, ins, 172, 184, 200)
    assert at_mark.minute_mark and any("one-minute mark" in w for w in at_mark.why)
    assert not off_mark.minute_mark and "minute_mark" not in at_mark.terms
    assert off_mark.terms["minute_mark"] > 0.1       # a soft cost, literal
    assert pb.plan_blend(out, ins).minute_mark


def test_awareness_is_soft_a_big_geometry_gain_beats_it():
    assert pb.W_MINUTE_OFF < pb.W_ENTRY_OFF and pb.W_MINUTE_OFF <= 1.0


def test_awareness_the_incoming_minute_is_noted_in_why_without_a_cost():
    p = pb.plan_blend(OUT, IN)
    assert any("incoming one-minute mark" in w for w in p.why)


# ---- R2: switch on a phrase point of at least one track; no premium for both ----------------
def test_r2_one_track_hit_costs_the_same_as_both():
    out = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(167, 168), (183, 184)])
    in_both = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    in_none = trk(120, [(0, "INTRO", True), (13, "DROP"), (32, "BREAK")])
    both = _cost(out, in_both, 168, 184, 200)         # outgoing 184 and incoming 16 are phrase points
    one = _cost(out, in_none, 168, 184, 200)          # outgoing 184 only
    assert abs(both.cost - one.cost) < 1e-9           # the owner never said "both"
    assert "switch_phrase" not in both.terms and "switch_phrase" not in one.terms


def test_r2_no_phrase_point_at_all_is_the_worst_case():
    out = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(167, 168), (183, 184)])
    in_none = trk(120, [(0, "INTRO", True), (13, "DROP"), (32, "BREAK")])
    one = _cost(out, in_none, 168, 184, 200)
    none = _cost(out, in_none, 168, 188, 200)         # 188 is nobody's phrase point; incoming bar 20 neither
    assert none.cost - one.cost > 5 and "switch_phrase" in none.terms


def test_r2_the_owners_one_sided_switches_are_chosen():
    for idx in (2, 3, 5):                              # blends 3, 4, 6 hit one track only
        b = KEY["blends"][idx]
        o = sb.prepare(KEY["tracks"][b["out"]], b["out"], "BOTTOM END", CONTENT)
        n = sb.prepare(KEY["tracks"][b["in"]], b["in"], "BOTTOM END", CONTENT)
        assert pb.plan_blend(o, n).bass_switch_out_bar == b["bass_switch_out_bar"]


def test_r2_switch_never_off_every_phrase_point_when_a_phrase_exists():
    for c in pb.candidates(OUT, IN, limit=5):
        assert "switch_phrase" not in c.terms


# ---- R3: end on the incoming's next section; loop the tail up to a phrase point -------------
def test_r3_outgoing_ends_naturally_on_incoming_section_start():
    out = trk(160, [(0, "INTRO"), (16, "DROP"), (128, "OUTRO")], fills=[(111, 112)])
    ins = trk(160, [(0, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)])
    plan = pb.plan_blend(out, ins)
    assert plan.out_end_bar == 160 and plan.out_looped_bars == 0
    assert plan.out_end_bar - plan.in_starts_at_out_bar == 32  # incoming BREAK at 32


def test_r3_short_outro_loops_tail_to_incoming_fill_start():
    out = trk(209, [(0, "INTRO"), (24, "DROP"), (200, "OUTRO")], fills=[(183, 184)])
    ins = trk(100, [(0, "INTRO"), (16, "DROP"), (48, "DROP")], fills=[(31, 32)])
    plan = pb.plan_blend(out, ins)
    assert plan.in_starts_at_out_bar == 184
    assert plan.out_end_bar == 184 + 31
    assert plan.out_looped_bars == 6


def test_r3_end_target_is_section_or_fill_start_never_fill_end():
    t = trk(100, [(0, "INTRO"), (16, "DROP")], fills=[(7, 8)])
    assert pb.target_points(t) == [7.0, 16.0]  # fill START 7 yes, fill END 8 no


def test_r3_incoming_intro_loops_are_whole_phrases_inside_the_intro_only():
    t = trk(120, [(0, "INTRO"), (8, "INTRO"), (24, "DROP"), (40, "BREAK")], fills=[(31, 32)])
    # phrase points 0, 8, 24, 32, 40, 120: only phrases ending at or before the first non-INTRO
    # section (24) loop: (8, 8) and (24, 16); never (32, 8) inside the drop
    assert pb._intro_loop_options(t) == [(8.0, 8.0), (24.0, 16.0)]


def test_r3_incoming_intro_loop_needs_a_phrase_of_at_least_four_bars():
    t = trk(120, [(0, "INTRO"), (3, "INTRO"), (8, "INTRO"), (24, "DROP")])
    assert pb._intro_loop_options(t) == [(8.0, 5.0), (24.0, 16.0)]   # the 3-bar phrase is skipped


def test_r3_incoming_intro_loop_is_a_candidate_for_a_long_gap():
    out = trk(120, [(0, "INTRO"), (24, "DROP"), (88, "OUTRO")], fills=[(87, 88)])
    ins = trk(120, [(0, "INTRO"), (8, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)])
    assert any(c.in_intro_looped_bars > 0 for c in pb.candidates(out, ins))


def test_r3_incoming_intro_loop_shifts_incoming_time():
    assert [pb._in_bar(x, 0, 8, 8) for x in (0, 7, 8, 15, 16, 20)] == [0, 7, 0, 7, 8, 12]


def test_r3_the_switch_comes_after_the_incoming_loop_completes():
    out = trk(160, [(0, "INTRO"), (24, "DROP")], fills=[(127, 128), (143, 144)])
    ins = trk(160, [(0, "INTRO"), (8, "INTRO"), (24, "DROP"), (40, "BREAK")], fills=[(39, 40)])
    looped = [c for c in pb.candidates(out, ins) if c.in_intro_looped_bars > 0]
    assert looped
    for c in looped:
        k = c.bass_switch_out_bar - c.in_starts_at_out_bar - c.in_intro_looped_bars
        assert c.in_loop_phrase_end >= c.in_intro_looped_bars > 0      # the loop's phrase is recorded
        assert k >= c.in_loop_phrase_end - 1e-9


# ---- R4: switch from ORIGINAL phrase points; prefer no loop; loops are in no clock -----------
def test_r4_prefers_a_plan_with_no_loop_when_one_fits():
    plan = pb.plan_blend(OUT, IN)
    assert plan.out_looped_bars == 0 and plan.in_intro_looped_bars == 0


def test_r4_no_loop_beats_a_looped_plan_with_a_better_clock():
    out = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(175, 176), (183, 184)])
    ins = trk(120, [(0, "INTRO"), (8, "DROP"), (24, "BREAK"), (32, "DROP")])
    cs = pb.candidates(out, ins)
    assert cs[0].key() == (176.0, 184.0, 200.0) and cs[0].out_looped_bars == 0


def test_r4_a_tail_loop_adds_only_its_own_cost_the_clocks_ignore_it():
    out = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(167, 168), (183, 184)])
    ins = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    nat = _cost(out, ins, 168, 184, 200)
    looped = _cost(out, ins, 168, 184, 206)
    diff = looped.cost - nat.cost
    assert diff > 5                                   # literal: a loop is not free
    assert abs(diff - pb.W_LOOP * 6) < 1e-9           # and it is exactly its own term
    assert looped.terms["length"] == nat.terms["length"]
    assert looped.terms["switch_time"] == nat.terms["switch_time"]


def test_r4_the_clocks_use_the_outgoing_bpm_not_the_incomings():
    # out 100 bpm: a minute is 25 bars.  In bpm 140 would make it 35.
    out = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(175, 176), (187, 188)], bpm=100.0)
    ins = trk(120, [(0, "INTRO", True), (16, "DROP"), (25, "BREAK")], bpm=140.0)
    p = _cost(out, ins, 175, 187, 200)                # blend 25 bars, switch 13 bars from the end
    assert p.terms["length"] == 0.0
    assert abs(p.terms["switch_time"] - 0.125) < 1e-9     # |13 - 12.5| * 0.25; incoming bpm would give 1.125


def test_r4_switch_is_never_a_loop_derived_point():
    cs = pb.candidates(OUT, IN)
    assert cs
    for c in cs:
        assert c.bass_switch_out_bar < OUT["n_bars"]


def test_r4_tie_break_prefers_no_loop_then_less_looping_then_earlier():
    mk = lambda cost, il, ol, a: pb.BlendPlan(a, a + 16, a + 32, ol, il, cost=cost)
    plans = [mk(5.0, 8.0, 0.0, 100), mk(5.0, 0.0, 6.0, 100), mk(5.0, 0.0, 0.0, 120), mk(5.0, 0.0, 0.0, 110)]
    plans.sort(key=pb._rank_key)
    assert [(p.in_intro_looped_bars, p.out_looped_bars, p.in_starts_at_out_bar) for p in plans] == [
        (0.0, 0.0, 110), (0.0, 0.0, 120), (0.0, 6.0, 100), (8.0, 0.0, 100)]


def test_r4_full_key_tells_apart_plans_that_play_different_incoming_bars():
    a = pb.BlendPlan(1, 2, 3, 0, 0)
    b = pb.BlendPlan(1, 2, 3, 0, 8.0, in_loop_phrase_end=16.0)
    assert a.key() == b.key() and a.full_key() != b.full_key()


# ---- R4/R3: the tail loop repeats real outro bars -------------------------------------------
def test_loop_source_is_the_last_whole_phrase_that_still_has_its_kick():
    # BITD-like: outro section at 200, fill end at 208, track ends 209; kick dies at bar 207
    out = trk(209, [(0, "INTRO"), (24, "DROP"), (200, "OUTRO")], fills=[(207, 208)])
    kick = [1] * 207 + [0, 0]
    out = with_content(out, [0] * 209, [0] * 209, kick)
    assert pb.loop_source(out) == (200.0, 208.0)


def test_loop_source_skips_a_phrase_with_too_little_kick():
    out = trk(220, [(0, "INTRO"), (24, "DROP"), (196, "OUTRO"), (210, "DROP")], fills=[(207, 208)])
    kick = [1] * 200 + [0] * 20          # kick gone from 200: phrase [210,220) has none, [196,207) 4/11
    out = with_content(out, [0] * 220, [0] * 220, kick)
    src = pb.loop_source(out)
    assert src[1] <= 196.0 + 1e-9 or src == (max(0.0, 220 - pb.MIN_LOOP_SOURCE), 220.0)


def test_loop_source_never_longer_than_the_cap():
    out = trk(300, [(0, "INTRO"), (24, "DROP"), (250, "OUTRO")])
    out = with_content(out, [0] * 300, [0] * 300, [1] * 300)
    s, e = pb.loop_source(out)
    assert e - s <= pb.MAX_LOOP_SOURCE and e == 300.0


def test_looped_bars_read_their_content_from_the_source_not_the_last_bar():
    out = trk(209, [(0, "INTRO"), (24, "DROP"), (200, "OUTRO")], fills=[(207, 208)])
    out = with_content(out, [0] * 209, [0] * 209, [1] * 207 + [0, 0])
    ins = with_content(trk(120, [(0, "INTRO"), (16, "DROP")]), [0] * 120, [0] * 120, [0] * 120)
    src = pb.loop_source(out)
    # the looped tail bars 209..214 map to source bars, which have kick; the final bar 208 does not
    assert all(pb._out_state(out, x, 200, pb.R6_BE, src) for x in range(209, 215))
    assert not pb._out_state(out, 208, 200, pb.R6_BE, src)


def test_plan_reports_its_loop_source():
    b = KEY["blends"][0]
    o = sb.prepare(KEY["tracks"][b["out"]], b["out"], "BOTTOM END", CONTENT)
    n = sb.prepare(KEY["tracks"][b["in"]], b["in"], "BOTTOM END", CONTENT)
    p = pb.plan_blend(o, n)
    assert p.out_looped_bars == 6 and p.out_loop_source_bars == (200.0, 208.0)
    assert p.flat_bars == 0


# ---- R5: never cut a usable outro (HARD) ----------------------------------------------------
def test_r5_best_plan_never_cuts():
    assert pb.plan_blend(OUT, IN).out_cut_bars == 0


CUTTABLE_OUT = trk(100, [(0, "INTRO"), (10, "DROP")], fills=[(67, 68), (83, 84)])
CUTTABLE_IN = trk(100, [(0, "INTRO"), (16, "DROP"), (30, "BREAK"), (32, "DROP")], fills=[(31, 32)])


def test_r5_cuts_are_excluded_whenever_any_non_cutting_plan_exists():
    cs = pb.candidates(CUTTABLE_OUT, CUTTABLE_IN)
    assert cs and all(c.out_cut_bars == 0 for c in cs)
    # a cutting plan is genuinely generable here: bar 30 of the incoming ends the outgoing at 98
    assert _cost(CUTTABLE_OUT, CUTTABLE_IN, 68, 84, 98).out_cut_bars == 2
    assert any(c.out_end_bar >= 100 for c in cs)


def test_r5_when_every_plan_cuts_the_cuts_are_returned_cheapest_first():
    out = trk(100, [(0, "INTRO"), (10, "DROP")], fills=[(69, 70), (75, 76)])
    ins = trk(100, [(0, "DROP"), (8, "BREAK"), (20, "DROP")])   # no INTRO: no incoming loops either
    cs = pb.candidates(out, ins)
    assert cs and all(c.out_cut_bars > 0 for c in cs)
    assert [c.cost for c in cs] == sorted(c.cost for c in cs)


def test_r5_the_codex_case_a_cut_never_beats_a_natural_end():
    # continuous kick; a cut at 98 used to cost 13.5 against 18.0 for the natural end at 100
    out = with_content(trk(100, [(0, "INTRO"), (10, "DROP")], fills=[(67, 68), (83, 84)]),
                       [1] * 100, [0] * 100, [1] * 100)
    ins = with_content(CUTTABLE_IN, [1] * 100, [0] * 100, [1] * 100)
    allc = pb.candidates(out, ins)
    assert allc and all(c.out_cut_bars == 0 for c in allc) and allc[0].out_end_bar >= 100
    # the cut is a real candidate when scored directly, and would have been the cheaper one
    cut = _cost(out, ins, 68, 84, 98)
    assert cut.out_cut_bars == 2 and cut.cost < allc[0].cost + 100


def test_r5_cut_reporting_counts_every_removed_bar():
    out = trk(100, [(0, "INTRO"), (10, "DROP")], fills=[(67, 68), (83, 84)])
    ins = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    assert _cost(out, ins, 68, 84, 98).out_cut_bars == 2     # two bars removed, two reported
    assert _cost(out, ins, 68, 84, 99).out_cut_bars == 0     # one bar of slack is not a cut
    assert _cost(out, ins, 68, 84, 99).out_end_bar == 100


def test_r5_one_bar_slack_is_not_a_cut_on_pax():
    b = KEY["blends"][1]
    plan = pb.plan_blend(KEY["tracks"][b["out"]], KEY["tracks"][b["in"]])
    assert plan.out_end_bar == 193 and plan.out_cut_bars == 0


def test_r5_the_slack_case_prices_the_natural_end_the_same_for_planner_and_owner():
    b = KEY["blends"][1]
    o = sb.prepare(KEY["tracks"][b["out"]], b["out"], "BOTTOM END", CONTENT)
    n = sb.prepare(KEY["tracks"][b["in"]], b["in"], "BOTTOM END", CONTENT)
    plan = pb.plan_blend(o, n)
    sam = _cost(o, n, 160, 176, 193)
    assert abs(plan.cost - sam.cost) < 1e-9          # both priced at the natural end 193


def test_the_tail_loop_is_capped_in_bars():
    out = trk(100, [(0, "INTRO"), (10, "DROP")], fills=[(67, 68), (83, 84)])
    ins = trk(300, [(0, "INTRO", True), (16, "DROP"), (40, "BREAK"), (70, "DROP")])
    cs = pb.candidates(out, ins)
    assert cs and max(c.out_looped_bars for c in cs) <= 24.0 + 1e-9
    assert any(c.out_looped_bars > 0 for c in cs)


# ---- R6: flat check ------------------------------------------------------------------------
def test_r6_flat_when_outro_over_intro_by_label_v1():
    out = trk(100, [(0, "INTRO"), (20, "DROP"), (68, "OUTRO")])
    ins = trk(100, [(0, "INTRO"), (32, "DROP")])
    _, flat = pb.content_check(out, ins, 68, 84, 100, r6=pb.R6_V1)
    assert flat == 32


def test_r6_not_flat_when_incoming_carries_after_switch_v1():
    out = trk(100, [(0, "INTRO"), (20, "DROP"), (68, "OUTRO")])
    ins = trk(100, [(0, "INTRO"), (16, "DROP")])
    _, flat = pb.content_check(out, ins, 68, 84, 100, r6=pb.R6_V1)
    assert flat == 16


def test_r6_carries_flag_overrides_label():
    out = trk(100, [(0, "INTRO"), (20, "DROP"), (68, "OUTRO", True)])
    ins = trk(100, [(0, "INTRO"), (32, "DROP")])
    _, flat = pb.content_check(out, ins, 68, 84, 100, r6=pb.R6_V1)
    assert flat == 0
    out2 = trk(100, [(0, "INTRO"), (20, "DROP", False), (68, "OUTRO")])
    assert pb._out_state(out2, 30, 80, pb.R6_V1) is False


def test_r6_after_the_switch_the_outgoing_is_drums_only():
    out = trk(100, [(0, "INTRO"), (20, "DROP")])
    assert pb._out_state(out, 50, 60) is True
    assert pb._out_state(out, 60, 60) is False


def test_r6_planner_avoids_flat_stretch_v1():
    b = KEY["blends"][2]
    o, n = KEY["tracks"][b["out"]], KEY["tracks"][b["in"]]
    cs = pb.candidates(o, n, r6=pb.R6_V1)
    sam = [c for c in cs if c.key() == (112.0, 128.0, 144.0)]
    assert sam and sam[0].flat_bars == 16
    assert cs[0].key() != sam[0].key() and cs[0].flat_bars < sam[0].flat_bars


def test_r6_stretch_report_lists_what_plays():
    st, _ = pb.content_check(OUT, IN, 152, 168, 200)
    assert st[0]["start"] == 152 and st[-1]["end"] == 200
    assert {"out", "in", "flat", "music_bars"} <= set(st[0])


# ---- R7: the incoming enters with lows cut; no quiet intro needed ---------------------------
def test_r7_pre_switch_incoming_never_counts_as_carrying():
    ins = trk(100, [(0, "DROP")])
    assert pb._in_state(ins, 3, 100, 116) is False   # before the switch: percussion only
    assert pb._in_state(ins, 19, 116, 116) is True   # after: its drop carries


def test_r7_incoming_with_full_kick_from_bar_one_plans_fine():
    ins = trk(160, [(0, "DROP"), (32, "BREAK"), (64, "DROP")], fills=[(31, 32)])
    plan = pb.plan_blend(OUT, ins)
    assert plan is not None and plan.flat_bars == 0


# ---- R8: outgoing with bass to its last bar ------------------------------------------------
def test_r8_no_outro_track_uses_fill_ends_in_its_last_minute():
    out = trk(176, [(0, "INTRO"), (16, "DROP"), (88, "BREAK"), (112, "DROP"), (144, "DROP")],
              fills=[(111, 112), (126, 128), (135, 136), (142, 144), (151, 152), (158, 160),
                     (167, 168)], bpm=125.0)
    ins = trk(155, [(0, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(23, 24)], bpm=124.0)
    plan = pb.plan_blend(out, ins)
    assert plan.in_starts_at_out_bar in {f[1] for f in out["fills"]}
    assert 28 <= 176 - plan.in_starts_at_out_bar <= 36          # entry about a minute out
    assert 12 <= 176 - plan.bass_switch_out_bar <= 20           # switch about 30 s out
    assert plan.out_cut_bars == 0


# ---- R9: only phrase points inside the mix zones (75 s, each track's own bpm) ----------------
def test_r9_zone_is_seconds_converted_with_the_outgoing_bpm():
    # 120 bpm: bar = 2 s, zone = 37.5 bars; out n = 200 so entries/switches are >= 162.5
    out = trk(200, [(0, "INTRO"), (50, "DROP")], fills=[(159, 160), (163, 164), (183, 184)], bpm=120.0)
    ins = trk(160, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)], bpm=120.0)
    cs = pb.candidates(out, ins)
    assert cs
    assert min(c.in_starts_at_out_bar for c in cs) >= 162.5
    assert min(c.bass_switch_out_bar for c in cs) >= 162.5
    assert any(c.in_starts_at_out_bar == 164 for c in cs)


def test_r9_zone_follows_the_bpm_not_a_bar_count():
    slow = trk(200, [(0, "INTRO"), (50, "DROP")], fills=[(139, 140), (181, 182), (191, 192)], bpm=60.0)  # 18.75 bars
    fast = trk(200, [(0, "INTRO"), (50, "DROP")], fills=[(139, 140), (183, 184)], bpm=240.0)         # 75 bars
    ins = trk(300, [(0, "INTRO", True), (16, "DROP"), (48, "BREAK"), (60, "DROP")], bpm=60.0)
    assert min(c.in_starts_at_out_bar for c in pb.candidates(slow, ins)) >= 200 - 18.75
    ins2 = dict(ins, bpm=240.0)
    assert any(c.in_starts_at_out_bar == 140 for c in pb.candidates(fast, ins2))


def test_r9_incoming_zone_uses_the_incoming_bpm():
    # outgoing 240 bpm allows a 75-bar zone; incoming 60 bpm has bars 4 s long, so 75 s is 18.75 of
    # its bars.  A switch 50 incoming bars in is a phrase point of the outgoing (280) but outside the
    # incoming's own first 75 seconds.
    out = trk(300, [(0, "INTRO"), (50, "DROP")], fills=[(229, 230), (279, 280)], bpm=240.0)
    ins = trk(300, [(0, "INTRO", True), (16, "DROP"), (50, "BREAK"), (60, "DROP")], bpm=60.0)
    cs = pb.candidates(out, ins)
    assert cs
    for c in cs:
        k = c.bass_switch_out_bar - c.in_starts_at_out_bar - c.in_intro_looped_bars
        assert k <= 18.75 + 1e-9


def test_r9_far_phrase_points_are_not_entries():
    out = trk(400, [(0, "INTRO"), (50, "DROP"), (336, "OUTRO")], fills=[(49, 50), (335, 336)])
    ins = trk(160, [(0, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)])
    for c in pb.candidates(out, ins):
        assert c.in_starts_at_out_bar >= 400 - 40 - 1e-9   # 75 s at 128 bpm = 40 bars (literal)
        assert c.bass_switch_out_bar >= 400 - 40 - 1e-9


def test_r9_incoming_phrase_points_beyond_the_zone_are_not_used_for_the_switch():
    ins = trk(300, [(0, "INTRO"), (100, "DROP"), (140, "BREAK")], fills=[(139, 140)])
    for c in pb.candidates(OUT, ins):
        k = c.bass_switch_out_bar - c.in_starts_at_out_bar - c.in_intro_looped_bars
        assert k <= 40


# ---- round 4 guards ---------------------------------------------------------------------------
def test_no_plan_ever_starts_before_the_outgoing_begins():
    out = trk(16, [(0, "INTRO"), (8, "DROP")], fills=[(7, 8)])
    ins = trk(160, [(0, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)])
    for c in pb.candidates(out, ins):
        assert c.in_starts_at_out_bar >= 0


def test_unusable_tracks_give_no_plan_and_do_not_raise():
    assert pb.plan_blend(dict(OUT, bpm=0), IN) is None
    assert pb.plan_blend(OUT, dict(IN, bpm=0)) is None
    assert pb.plan_blend({}, IN) is None
    assert pb.candidates(dict(OUT, n_bars=0), IN) == []


def test_an_incoming_with_no_end_target_gives_none_and_the_scorer_survives(monkeypatch):
    ins = trk(100, [(0, "INTRO")])
    assert pb.plan_blend(OUT, ins) is None
    monkeypatch.setattr(pb, "plan_blend", lambda *a, **k: None)
    exact, rows = sb.score("BOTTOM END", out=lambda *_: None)
    assert exact == 0 and all(r["plan"] is None for r in rows)
    sb.table(out=lambda *_: None)


def test_empty_or_mismatched_content_falls_back_to_labels_without_raising():
    empty = with_content(OUT, [], [], [])
    assert pb._measured(empty, 5) is None
    assert pb._measured(with_content(OUT, [], []), 5, pb.R6_V1) is None      # no has_kick, v1: still None
    assert pb.plan_blend(empty, IN) is not None
    short = with_content(OUT, [1] * 10, [1] * 9, [1] * 10)
    assert pb._measured(short, 5) is None
    assert pb._measured(with_content(OUT, [1] * 10, [1] * 10, [1] * 9), 5) is None


def test_missing_kick_field_falls_back_to_labels_and_says_so():
    t = with_content(OUT, [1] * 200, [0] * 200)          # no has_kick
    assert pb._measured(t, 5, pb.R6_BE) is None            # not silently "no kick"
    assert pb._measured(t, 5, pb.R6_V1) is not None        # the old rule never needed it
    p = _cost(t, IN, 168, 184, 200)
    assert any("no has_kick" in w and "fell back to labels" in w for w in p.why)


def test_be_label_fallback_does_not_assume_a_break_has_a_kick():
    t = trk(100, [(0, "DROP"), (40, "BREAK")])
    assert pb._out_state(t, 50, 90, pb.R6_BE) is False     # BE: a BREAK is not assumed to have a kick
    assert pb._out_state(t, 50, 90, pb.R6_V1) is True      # v1 kept its old label set
    assert pb._out_state(t, 20, 90, pb.R6_BE) is True      # a DROP still does


# ---- R6 on MEASURED content (rounds 2 and 3) -----------------------------------------------
def test_measured_pre_switch_outgoing_needs_bass_or_music_v1():
    out = with_content(trk(8, [(0, "INTRO")]), [0, 0, 1, 1, 0, 0, 0, 0], [0, 0, 0, 0, 1, 0, 0, 0])
    assert [pb._out_state(out, x, 6, pb.R6_V1) for x in range(6)] == [False, False, True, True, True, False]


def test_measured_post_switch_outgoing_needs_music_not_bass_v1():
    out = with_content(trk(8, [(0, "INTRO")]), [1] * 8, [0, 0, 0, 0, 0, 0, 1, 0])
    v1 = pb.R6_V1
    assert pb._out_state(out, 3, 4, v1) is True
    assert pb._out_state(out, 5, 4, v1) is False
    assert pb._out_state(out, 6, 4, v1) is True


def test_measured_incoming_counts_bass_or_music_after_the_switch_only_v1():
    ins = with_content(trk(8, [(0, "INTRO")]), [1, 0, 0, 0, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0, 0, 0])
    v1 = pb.R6_V1
    assert pb._in_state(ins, 0, 0, 4, v1) is False
    assert pb._in_state(ins, 0, 4, 4, v1) is True
    assert pb._in_state(ins, 1, 4, 4, v1) is False
    assert pb._in_state(ins, 2, 4, 4, v1) is True


def test_measured_content_beats_the_label():
    ins = with_content(trk(8, [(0, "DROP")]), [0] * 8, [0] * 8, [0] * 8)
    assert pb._in_state(ins, 3, 10, 4) is False
    ins2 = with_content(trk(8, [(0, "INTRO")]), [1] * 8, [0] * 8, [0] * 8)
    assert pb._in_state(ins2, 3, 10, 4) is True


def test_flag_beats_measured_content():
    out = with_content(trk(8, [(0, "INTRO", False)]), [1] * 8, [1] * 8, [1] * 8)
    assert pb._out_state(out, 2, 6) is False
    ins = with_content(trk(8, [(0, "INTRO", True)]), [0] * 8, [0] * 8, [0] * 8)
    assert pb._in_state(ins, 3, 10, 4) is True


def test_measured_content_beyond_the_track_reads_as_the_last_bar():
    out = with_content(trk(4, [(0, "DROP")]), [1, 1, 1, 0], [0, 0, 0, 1], [1, 1, 1, 0])
    assert pb._measured(out, 9) == (False, True, False) and pb._measured(out, -1) == (True, False, True)


def test_measured_flat_counts_each_side_v1():
    v1 = pb.R6_V1
    out = with_content(trk(40, [(0, "DROP")]), [1] * 20 + [0] * 20, [0] * 40)
    ins = with_content(trk(40, [(0, "DROP")]), [0] * 40, [0] * 40)
    _, flat = pb.content_check(out, ins, 24, 32, 40, r6=v1)
    assert flat == 16
    ins2 = with_content(trk(40, [(0, "DROP")]), [0] * 8 + [1] * 32, [0] * 40)
    _, flat2 = pb.content_check(out, ins2, 24, 32, 40, r6=v1)
    assert flat2 == 8


# ---- R6 bottom end (Sam, 2026-11-20) -------------------------------------------------------
def bottom(kick, bass, music=None):
    n = len(kick)
    return with_content(trk(n, [(0, "INTRO")]), bass, music or [0] * n, kick)


def test_be_outgoing_before_switch_needs_kick_or_bass_after_switch_only_kick():
    out = bottom([0, 1, 0, 0, 0, 1, 0, 0], [0, 0, 1, 0, 0, 0, 1, 0], music=[1] * 8)
    be = pb.R6_BE
    assert [pb._out_state(out, x, 4, be) for x in range(4)] == [False, True, True, False]
    assert [pb._out_state(out, x, 4, be) for x in range(4, 8)] == [False, True, False, False]


def test_be_incoming_gives_nothing_before_the_switch_then_kick_or_bass():
    ins = bottom([1, 0, 1, 0, 0, 0], [1, 1, 0, 0, 0, 0], music=[1] * 6)
    be = pb.R6_BE
    assert pb._in_state(ins, 0, 0, 3, be) is False and pb._in_state(ins, 1, 2, 3, be) is False
    assert pb._in_state(ins, 0, 3, 3, be) is True
    assert pb._in_state(ins, 1, 4, 3, be) is True
    assert pb._in_state(ins, 3, 6, 3, be) is False


def test_be_music_does_not_rescue_a_bar_but_is_reported():
    out = bottom([0] * 12, [0] * 12, music=[1] * 12)
    ins = bottom([0] * 12, [0] * 12, music=[1] * 12)
    st, flat = pb.content_check(out, ins, 0, 6, 12, r6=pb.R6_BE)
    assert flat == 12 and len(st) == 1 and st[0]["flat"] and st[0]["music_bars"] == 12


def test_be_a_one_bar_kick_drop_is_a_free_flat_run():
    out = bottom([1] * 5 + [0] + [1] * 6, [0] * 12)
    ins = bottom([1] * 12, [0] * 12)
    st, flat = pb.content_check(out, ins, 0, 8, 12, r6=pb.R6_BE)
    runs = pb.flat_runs(st)
    assert runs == [(5.0, 1.0)] and pb.flat_cost(runs) == 0


def test_be_long_flat_run_is_costly():
    out = bottom([1] * 4 + [0] * 28, [0] * 32)
    ins = bottom([0] * 20 + [1] * 12, [0] * 32)
    st, flat = pb.content_check(out, ins, 4, 16, 32, r6=pb.R6_BE)
    runs = pb.flat_runs(st)
    assert runs and runs[0][1] >= 16 and pb.flat_cost(runs) >= pb.W_FLAT_RUN * (runs[0][1] - pb.FREE_FLAT_BARS) ** 2 > 16


def test_be_end_to_end_flat_run_cost_follows_the_convex_curve():
    # outgoing kick stops at 184 (the switch); incoming has no kick or bass until its bar 30, so
    # after the switch bars 184-198 give no bottom end from either side: one 14-bar run, which costs
    # 0.25 * (14 - 8) ** 2 = 9.0.  With a kick from bar 0 the same blend has no flat run.
    out = with_content(trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(167, 168), (183, 184)]),
                       [1] * 168 + [0] * 32, [0] * 200, [1] * 184 + [0] * 16)
    quiet = with_content(trk(120, [(0, "INTRO"), (32, "DROP"), (48, "BREAK")], fills=[(31, 32)]),
                         [0] * 30 + [1] * 90, [0] * 120, [0] * 30 + [1] * 90)
    loud = with_content(trk(120, [(0, "INTRO"), (32, "DROP"), (48, "BREAK")], fills=[(31, 32)]),
                        [1] * 120, [0] * 120, [1] * 120)
    p = pb.plan_blend(out, quiet)
    assert p.flat_runs == [(184.0, 14.0)] and abs(p.terms["flat"] - 9.0) < 1e-9
    assert pb.plan_blend(out, loud).terms["flat"] == 0.0


def test_flat_cost_is_free_up_to_one_phrase_then_convex():
    f = pb.FREE_FLAT_BARS
    assert pb.flat_cost([(0, f)]) == 0 and pb.flat_cost([(0, 1)]) == 0
    c1, c2, c3 = (pb.flat_cost([(0, f + d)]) for d in (4, 8, 16))
    assert 0 < c1 < c2 < c3
    assert c2 > 2 * c1 and c3 > 2 * c2


def test_flat_cost_charges_each_continuous_run_not_the_total():
    f = pb.FREE_FLAT_BARS
    assert pb.flat_cost([(0, f), (20, f)]) == 0
    assert pb.flat_cost([(0, 2 * f)]) > 0


def test_v1_flat_cost_is_the_per_bar_ban():
    assert pb.flat_cost([(0, 3), (9, 2)], pb.R6_V1) == pb.W_FLAT * 5


def test_flat_weight_makes_a_minute_of_drums_over_drums_as_dear_as_a_12_bar_cut():
    assert pb.flat_cost([(0, 32)]) == pytest.approx(pb.W_CUT * 12)


def test_plan_cost_is_the_sum_of_its_terms_and_reports_flat_runs():
    for c in pb.candidates(OUT, IN, limit=8):
        assert c.cost == pytest.approx(sum(c.terms.values()))
        assert c.flat_bars == sum(n for _, n in c.flat_runs)


# ---- data model and helpers ----------------------------------------------------------------
def test_phrase_points_are_section_starts_fill_ends_start_and_end():
    t = trk(50, [(0, "INTRO"), (16, "DROP")], fills=[(7, 8)])
    assert pb.phrase_points(t) == [0.0, 8.0, 16.0, 50.0]


def test_carries_flag_accepts_bool_or_dict():
    t = trk(50, [(0, "INTRO", True), (16, "DROP", {"carries": False})])
    assert [s[2] for s in pb._sections(t)] == [True, False]


def test_plan_has_the_documented_fields():
    p = pb.plan_blend(OUT, IN)
    for f in ("in_starts_at_out_bar", "bass_switch_out_bar", "out_end_bar", "out_looped_bars",
              "in_intro_looped_bars", "stretches", "why", "out_loop_source_bars", "minute_mark"):
        assert hasattr(p, f)
    assert p.why and all(isinstance(w, str) for w in p.why)
    assert not any(chr(0x2014) in w for w in p.why)


def test_candidates_hold_no_duplicate_plans():
    # a target 1 bar before the end and one on it are the same plan (the slack case)
    cs = pb.candidates(CUTTABLE_OUT, CUTTABLE_IN)
    keys = [c.full_key() for c in cs]
    assert len(keys) == len(set(keys))


def test_candidates_are_ranked_best_first_and_deterministic():
    c1 = pb.candidates(OUT, IN)
    c2 = pb.candidates(OUT, IN)
    assert [c.cost for c in c1] == sorted(c.cost for c in c1)
    assert [c.full_key() for c in c1] == [c.full_key() for c in c2]
