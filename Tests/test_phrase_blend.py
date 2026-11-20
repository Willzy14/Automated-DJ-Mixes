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
    # the owner's placement is not flat under the (round 2) rule once the hand overrides are applied
    assert pb.content_check(o, n, a, s, e, r6=pb.R6_V1)[1] == 0


def _exact_vector(mode):
    key, vec = KEY, []
    for b in key["blends"]:
        o = sb.prepare(key["tracks"][b["out"]], b["out"], mode, CONTENT)
        n = sb.prepare(key["tracks"][b["in"]], b["in"], mode, CONTENT)
        sam = sb.sam_numbers(b, o)
        p = pb.plan_blend(o, n, r6=sb.MODES[mode][1])
        vec.append(sb.matches(p, b, sam) is not None)
    return vec


def test_score_columns_are_pinned_as_measured_in_round_6():
    # Characterisation, not validation.  Seven owner blends: the first six share switch = entry + 16,
    # the seventh (swap on the incoming's bass-in) is entry + 24.
    # Round 6 (sane both-sided pairings outrank one-sided swaps): blend 1 is right again; blends 3, 6
    # and 7 are now missed in BOTTOM END because a sane pairing exists and the owner's own answer is
    # one-sided (his stated rule against his own accepted placements: see the report).
    assert _exact_vector("MEASURED") == [True, False, True, False, True, False, True]
    assert _exact_vector("LABELS") == [True, True, False, False, True, False, False]
    assert _exact_vector("OVERRIDES") == [True, True, True, True, True, False, False]
    assert _exact_vector("BOTTOM END") == [True, True, False, True, True, False, False]


def test_headline_claims_are_true_and_stated():
    # the module docstring says R6 is inert on the six and the switch is entry + 16 in all six
    assert "INERT" in pb.__doc__ and "entry + 16" in pb.__doc__ and "END_SLACK_BARS from blend 2" in pb.__doc__
    assert "not tuned to pass" not in pb.__doc__
    gaps = [b["bass_switch_out_bar"] - b["in_starts_at_out_bar"] for b in KEY["blends"]]
    assert gaps[:6] == [16] * 6 and gaps[6] == 24


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
    assert rows[:6] == [[], [(192.0, 1.0)], [], [], [], []] and rows[6] == []


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
    ins = trk(120, [(0, "INTRO", True), (13, "DROP"), (32, "BREAK")])      # no phrase point at 16: one-sided
    c_fill, c_sec = _cost(o_fill, ins, 168, 184, 200), _cost(o_sec, ins, 168, 184, 200)
    assert not c_fill.paired and not c_sec.paired
    assert c_sec.cost - c_fill.cost > 0.5
    assert "entry" in c_sec.terms and "entry" not in c_fill.terms


def test_r1_entry_cost_order_fill_end_then_section_start_then_unphrased():
    base = [(0, "INTRO"), (16, "DROP")]
    o_fill = trk(200, base, fills=[(168, 169), (183, 184)])
    o_sec = trk(200, base + [(167, "DROP")], fills=[(183, 184)])
    o_off = trk(200, base, fills=[(183, 184)])
    ins = trk(120, [(0, "INTRO", True), (13, "DROP"), (32, "BREAK")])      # one-sided swaps keep the entry cost
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
def test_r2_a_sane_pairing_outranks_a_cheaper_one_sided_swap():
    # round 6 (owner on blend 1): lining the swap up on BOTH tracks beats a cheaper swap that sits
    # on only one, because it is a tier, not a weight
    b = KEY["blends"][0]
    o = sb.prepare(KEY["tracks"][b["out"]], b["out"], "BOTTOM END", CONTENT)
    n = sb.prepare(KEY["tracks"][b["in"]], b["in"], "BOTTOM END", CONTENT)
    cs = pb.candidates(o, n)
    assert cs[0].sane and cs[0].paired
    assert any((not c.sane) and c.cost < cs[0].cost for c in cs)         # a cheaper one-sided plan exists
    assert cs[0].key() == (184.0, 200.0, 215.0)                          # the owner's own answer


def test_r2_no_phrase_point_at_all_is_the_worst_case():
    out = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(167, 168), (183, 184)])
    in_none = trk(120, [(0, "INTRO", True), (13, "DROP"), (32, "BREAK")])
    one = _cost(out, in_none, 168, 184, 200)
    none = _cost(out, in_none, 168, 188, 200)         # 188 is nobody's phrase point; incoming bar 20 neither
    assert none.cost - one.cost > 5 and "switch_phrase" in none.terms


def test_r2_reconciling_the_owners_one_sided_answers_with_the_pairing_rule():
    got = {}
    for idx in (2, 3, 5, 6):
        b = KEY["blends"][idx]
        o = sb.prepare(KEY["tracks"][b["out"]], b["out"], "BOTTOM END", CONTENT)
        n = sb.prepare(KEY["tracks"][b["in"]], b["in"], "BOTTOM END", CONTENT)
        got[idx] = pb.plan_blend(o, n)
    # blend 4: the outgoing offers no phrase point within 20 s of the 30 s mark, so the owner's
    # one-sided swap (176) is the fallback and is chosen, and `why` says so
    assert got[3].key() == (160.0, 176.0, 192.0) and not got[3].paired
    assert any("ONE-SIDED" in w and "fallback" in w for w in got[3].why)
    # blends 3, 6 and 7: a sane pairing exists, so it outranks the owner's one-sided placement
    for idx in (2, 5, 6):
        assert got[idx].paired and got[idx].sane


def test_r2_switch_never_off_every_phrase_point_when_a_phrase_exists():
    for c in pb.candidates(OUT, IN, limit=5):
        assert "switch_phrase" not in c.terms


def test_a_one_sided_swap_says_so_and_names_the_outgoing_points_it_ignored():
    out = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(167, 168), (183, 184)])
    ins = trk(120, [(0, "INTRO", True), (13, "DROP"), (32, "BREAK")])
    p = _cost(out, ins, 168, 184, 200)                     # outgoing fill end 184, incoming bar 16: no point
    assert not p.paired
    assert any("ONE-SIDED" in w and "pairing could use" in w for w in p.why)


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
    best_loop_free = [c for c in cs if c.out_looped_bars == 0 and c.in_intro_looped_bars == 0][0]
    assert cs[0].out_looped_bars == 0 and cs[0].in_intro_looped_bars == 0
    looped = [c for c in cs if c.out_looped_bars > 0 and c.sane == cs[0].sane]
    assert looped and min(c.cost for c in looped) > best_loop_free.cost


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
    p = _cost(o, n, 184, 200, 215)          # the owner's placement (round 5 plans differently)
    assert p.out_looped_bars == 6 and p.out_loop_source_bars == (200.0, 208.0)
    assert p.flat_bars == 0


# ---- R5: never cut a usable outro (HARD) ----------------------------------------------------
def test_r5_best_plan_never_cuts():
    assert pb.plan_blend(OUT, IN).out_cut_bars == 0


CUTTABLE_OUT = trk(100, [(0, "INTRO"), (10, "DROP")], fills=[(67, 68), (83, 84), (95, 96)])
CUTTABLE_IN = trk(100, [(0, "INTRO"), (16, "DROP"), (26, "BREAK"), (30, "DROP"), (32, "DROP")], fills=[(31, 32)])


def test_r5_cuts_are_excluded_whenever_any_non_cutting_plan_exists():
    cs = pb.candidates(CUTTABLE_OUT, CUTTABLE_IN)
    assert cs and all(c.out_cut_bars == 0 for c in cs)
    # a forbidden cut is generable here: incoming bar 26 would stop the outgoing at 94, dropping the
    # outgoing's fill end at 96 (a phrase point), so it is a cut, not a trim
    assert _cost(CUTTABLE_OUT, CUTTABLE_IN, 68, 84, 94).out_cut_bars == 6
    assert any(c.out_end_bar >= 100 for c in cs)
    assert not any(c.out_end_bar == 94 for c in cs)


def test_r5_when_every_plan_cuts_the_cuts_are_returned_cheapest_first():
    out = trk(100, [(0, "INTRO"), (10, "DROP")], fills=[(69, 70)])   # one entry only: 70
    ins = trk(100, [(0, "DROP"), (8, "BREAK"), (20, "DROP")])   # no INTRO: no incoming loops either
    cs = pb.candidates(out, ins)
    assert cs and all(c.out_cut_bars > 0 for c in cs)
    assert [c.cost for c in cs] == sorted(c.cost for c in cs)


def test_r5_a_forbidden_cut_never_beats_a_natural_end_even_when_it_is_cheaper():
    # continuous kick; a cut to 94 drops the outgoing's fill end at 96, so it is a forbidden cut
    out = with_content(CUTTABLE_OUT, [1] * 100, [0] * 100, [1] * 100)
    ins = with_content(CUTTABLE_IN, [1] * 100, [0] * 100, [1] * 100)
    allc = pb.candidates(out, ins)
    assert allc and all(c.out_cut_bars == 0 for c in allc)
    cut = _cost(out, ins, 68, 84, 94)
    assert cut.out_cut_bars == 6 and cut.out_trim_bars == 0


def test_r5_cut_reporting_counts_every_removed_bar():
    out = trk(100, [(0, "INTRO"), (10, "DROP")], fills=[(67, 68), (83, 84), (95, 96)])
    ins = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    c = _cost(out, ins, 68, 84, 94)                          # the fill end at 96 is dropped: a cut
    assert c.out_cut_bars == 6 and c.out_trim_bars == 0      # six bars removed, six reported
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
    assert min(c.bass_switch_out_bar for c in cs) >= 162.5               # the swap stays in the 75 s zone
    assert min(c.in_starts_at_out_bar for c in cs) >= 200 - 60 - 1e-9    # a pairing's entry: 120 s / 2 s = 60 bars
    assert any(c.in_starts_at_out_bar == 164 for c in cs)


def test_r9_zone_follows_the_bpm_not_a_bar_count():
    slow = trk(200, [(0, "INTRO"), (50, "DROP")], fills=[(139, 140), (181, 182), (191, 192)], bpm=60.0)  # 18.75 bars
    fast = trk(200, [(0, "INTRO"), (50, "DROP")], fills=[(139, 140), (183, 184)], bpm=240.0)         # 75 bars
    ins = trk(300, [(0, "INTRO", True), (16, "DROP"), (48, "BREAK"), (60, "DROP")], bpm=60.0)
    assert min(c.bass_switch_out_bar for c in pb.candidates(slow, ins)) >= 200 - 18.75
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
        assert c.in_starts_at_out_bar >= 400 - 64 - 1e-9   # a derived entry: 120 s at 128 bpm = 64 bars (literal)
        assert c.bass_switch_out_bar >= 400 - 40 - 1e-9    # the swap: 75 s = 40 bars (literal)


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
    p = _cost(out, quiet, 168, 184, 200)
    assert p.flat_runs == [(184.0, 14.0)] and abs(p.terms["flat"] - 9.0) < 1e-9
    assert _cost(out, loud, 168, 184, 200).terms["flat"] == 0.0


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


# ---- round 5: swap-anchored placement, tail trim, alternatives -------------------------------
def _blend7(mode="BOTTOM END"):
    b = KEY["blends"][6]
    o = sb.prepare(KEY["tracks"][b["out"]], b["out"], mode, CONTENT)
    n = sb.prepare(KEY["tracks"][b["in"]], b["in"], mode, CONTENT)
    return b, o, n


def test_bass_in_is_the_first_bar_where_bass_turns_on_and_stays_on():
    bass = [0] * 24 + [1] * 8 + [0] * 8
    assert pb.bass_in(with_content(trk(40, [(0, "INTRO")]), bass, [0] * 40, [1] * 40)) == 24.0
    # a fill bar inside the run does not break it ("straight after its own fill")
    fill = [0] * 16 + [1] * 7 + [0] + [1] * 6 + [0] * 10
    assert pb.bass_in(with_content(trk(40, [(0, "INTRO")]), fill, [0] * 40, [1] * 40)) == 16.0
    # a blip is not a bass-in; bass from bar one is not one either (the old phrase-point rule applies)
    blip = [0] * 10 + [1] + [0] * 10 + [1] * 10 + [0] * 9
    assert pb.bass_in(with_content(trk(40, [(0, "INTRO")]), blip, [0] * 40, [1] * 40)) == 21.0
    assert pb.bass_in(with_content(trk(40, [(0, "INTRO")]), [1] * 40, [0] * 40, [1] * 40)) is None
    assert pb.bass_in(trk(40, [(0, "INTRO")])) is None                       # no measured content


def test_bass_in_of_the_real_incoming_tracks():
    got = {}
    for name in KEY["tracks"]:
        n = sb.prepare(KEY["tracks"][name], name, "BOTTOM END", CONTENT)
        got[name.split(" - ")[1][:8]] = pb.bass_in(n)
    assert got["Back In "] == 24.0 and got["Better W"] == 16.0 and got["Come Bac"] is None


def test_blend_7_planner_prefers_the_sane_pairing_and_says_so():
    # round 6: A-F4 end (bar 131) lined up with B's bass-in (bar 24) is a sane both-sided pairing, so
    # it outranks the owner's primary, whose swap (139) is on no outgoing phrase point
    b, o, n = _blend7()
    p = pb.plan_blend(o, n)
    assert p.key() == (107.0, 131.0, 147.0) and p.paired and p.sane and p.in_point_kind == "bass-in"
    assert sb.matches(p, b, sb.sam_numbers(b, o)) is None            # entry 107, not his 115
    assert p.bass_switch_out_bar == b["also_acceptable"][0]["bass_switch_out_bar"]   # same swap bar as his second answer
    assert p.out_trim_bars == 8.0 and p.out_cut_bars == 0


def test_blend_7_the_owners_primary_is_a_one_sided_anchored_fallback_in_the_top_3():
    b, o, n = _blend7()
    cs = pb.candidates(o, n)
    assert (115.0, 139.0, 147.0) in [c.key() for c in cs[:3]]
    own = _cost(o, n, 115, 139, 147)
    assert own.swap_anchored and not own.paired and not own.sane
    assert any("ONE-SIDED" in w for w in own.why)


def test_blend_7_the_owners_second_answer_is_a_candidate():
    b, o, n = _blend7()
    ac = b["also_acceptable"][0]
    assert any(c.in_starts_at_out_bar == ac["in_starts_at_out_bar"] and c.bass_switch_out_bar == ac["bass_switch_out_bar"]
               for c in pb.candidates(o, n))


def test_blend_7_the_round_4_pick_is_still_a_candidate():
    b, o, n = _blend7()
    assert (123.0, 147.0, 155.0) in [c.key() for c in pb.candidates(o, n)]


def test_blend_7_pick_no_longer_depends_on_the_late_swap_term(monkeypatch):
    b, o, n = _blend7()
    before = pb.plan_blend(o, n).key()
    monkeypatch.setattr(pb, "W_LATE_SWAP", 0.0)
    assert pb.plan_blend(o, n).key() == before


def test_swap_anchored_plan_waives_the_minute_mark_term_only():
    b, o, n = _blend7()
    anchored = _cost(o, n, 115, 139, 147)
    other = _cost(o, n, 115, 131, 147)
    assert anchored.swap_anchored and "minute_mark" not in anchored.terms
    assert not other.swap_anchored and other.terms["minute_mark"] > 0.1
    assert anchored.terms["entry"] > 0.5                 # R1 entry-type cost still applies


def test_a_swap_on_kick_only_incoming_bars_costs_more_than_one_on_the_bass_in():
    b, o, n = _blend7()
    kick_only = _cost(o, n, 115, 131, 147)               # incoming bar 16: kick, no bass
    on_bass = _cost(o, n, 115, 139, 147)                 # incoming bar 24: bass-in
    assert kick_only.terms["swap_kick_only"] > 1.0 and "swap_kick_only" not in on_bass.terms
    assert on_bass.swap_at_bass_in and not kick_only.swap_at_bass_in


def test_a_late_bass_in_swap_costs_but_only_on_the_bass_in():
    b, o, n = _blend7()
    late = _cost(o, n, 123, 147, 155)                    # bass-in swap, 8 bars (15 s) before the end
    early = _cost(o, n, 115, 139, 155)                   # bass-in swap, 16 bars (31 s) before the end
    assert late.terms["late_bass_in_swap"] > 1.0 and "late_bass_in_swap" not in early.terms
    kick_only = _cost(o, n, 123, 147 - 16, 155)          # not a bass-in swap: no late term
    assert "late_bass_in_swap" not in kick_only.terms


def test_swap_anchored_entries_may_be_early_but_not_beyond_the_cap():
    b, o, n = _blend7()
    cap_bars = pb.ENTRY_MAX_SECONDS / (240.0 / o["bpm"])
    for c in pb.candidates(o, n):
        assert c.in_starts_at_out_bar >= o["n_bars"] - cap_bars - 1e-9
    assert any(c.in_starts_at_out_bar < o["n_bars"] - pb._zone_bars(o) for c in pb.candidates(o, n))


def test_trim_is_allowed_for_short_tails_without_a_phrase_point_or_outro():
    out = trk(100, [(0, "INTRO"), (10, "DROP")], fills=[(67, 68), (83, 84)])
    ins = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    t = _cost(out, ins, 68, 84, 92)                      # 8 bars, nothing in (92, 100)
    assert t.out_trim_bars == 8 and t.out_cut_bars == 0 and t.terms["trim"] > 1.0
    assert any("tail trim" in w for w in t.why)


def test_trim_is_refused_beyond_eight_bars_with_a_phrase_point_or_inside_an_outro():
    ins = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    out = trk(100, [(0, "INTRO"), (10, "DROP")], fills=[(67, 68), (83, 84)])
    assert _cost(out, ins, 68, 84, 91).out_cut_bars == 9            # 9 bars: too many
    out2 = trk(100, [(0, "INTRO"), (10, "DROP")], fills=[(67, 68), (83, 84), (95, 96)])
    assert _cost(out2, ins, 68, 84, 93).out_cut_bars == 7           # a fill end at 96 is dropped
    out3 = trk(100, [(0, "INTRO"), (10, "DROP"), (90, "OUTRO")], fills=[(67, 68), (83, 84)])
    assert _cost(out3, ins, 68, 84, 94).out_cut_bars == 6           # lands inside an OUTRO section
    assert _cost(out3, ins, 68, 84, 94).out_trim_bars == 0


def test_a_trim_costs_so_a_natural_end_of_comparable_quality_wins():
    out = trk(100, [(0, "INTRO"), (10, "DROP")], fills=[(67, 68), (83, 84)])
    ins = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    natural = _cost(out, ins, 68, 84, 100)
    trim = _cost(out, ins, 68, 84, 96)
    assert trim.terms["trim"] > 0.5 and "trim" not in natural.terms
    assert trim.terms["trim"] == pytest.approx(pb.W_TRIM * 4)


def test_the_first_six_never_trim_in_the_plans_chosen():
    for idx in range(6):
        b = KEY["blends"][idx]
        o = sb.prepare(KEY["tracks"][b["out"]], b["out"], "BOTTOM END", CONTENT)
        n = sb.prepare(KEY["tracks"][b["in"]], b["in"], "BOTTOM END", CONTENT)
        assert pb.plan_blend(o, n).out_trim_bars == 0


def test_alternatives_are_distinct_defensible_and_explained():
    b, o, n = _blend7()
    p = pb.plan_blend(o, n)
    assert 1 <= len(p.alternatives) <= pb.MAX_ALTERNATIVES
    seen = [(p.bass_switch_out_bar, p.in_starts_at_out_bar)]
    for a in p.alternatives:
        assert a.plan.cost <= p.cost + pb.ALT_MARGIN and a.plan.out_cut_bars == 0
        assert a.plan.terms.get("flat", 0.0) == 0
        assert a.reason and a.feel and chr(0x2014) not in a.reason + a.feel
        pair = (a.swap_out_bar, a.plan.in_starts_at_out_bar)
        assert pair not in seen
        seen.append(pair)
    assert any("more energy" in a.feel for a in p.alternatives)
    assert any("less energy" in a.feel for a in p.alternatives)


def test_alternatives_stay_inside_the_margin():
    b, o, n = _blend7()
    p = pb.plan_blend(o, n)
    assert all(a.plan.cost - p.cost <= pb.ALT_MARGIN + 1e-9 for a in p.alternatives)
    assert all(a.plan.cost - p.cost > 0 or a.plan.key() != p.key() for a in p.alternatives)


def test_matches_reads_primary_acceptable_and_none():
    b, o, n = _blend7()
    sam = sb.sam_numbers(b, o)
    mk = lambda a_, s_, e_: pb.BlendPlan(a_, s_, e_, 0.0, 0.0)
    assert sb.matches(mk(115, 139, 147), b, sam) == "primary"
    assert sb.matches(mk(115, 131, 160), b, sam) == "acceptable #1"      # the end is the planner's own
    assert sb.matches(mk(115, 131, 147), b, sam) == "acceptable #1"
    assert sb.matches(mk(123, 147, 155), b, sam) is None
    assert sb.matches(None, b, sam) is None
    looped = pb.BlendPlan(115, 139, 147, 0.0, 8.0)
    assert sb.matches(looped, b, sam) is None                            # an incoming loop plays other bars


def test_the_scorer_reports_three_numbers_and_the_acceptable_offer():
    lines = []
    exact, rows = sb.score("BOTTOM END", out=lines.append)
    text = "\n".join(lines)
    assert "top pick = primary" in text and "primary or also_acceptable" in text and "primary in top 3" in text
    assert "(info) swap bar equals one of his" in text
    assert sum(r["primary"] for r in rows) == 4 and exact == 4
    assert sum(r["primary_top3"] for r in rows) == 6
    assert rows[6]["which"] is None and rows[6]["primary_top3"] and rows[6]["swap_only"]


# ---- round 5, mutation follow-ups ------------------------------------------------------------
def test_no_kick_penalty_when_the_swap_lands_on_bars_with_no_kick_at_all():
    out = with_content(trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(167, 168), (183, 184)]),
                       [1] * 200, [0] * 200, [1] * 200)
    ins = with_content(trk(120, [(0, "INTRO"), (16, "DROP"), (32, "BREAK")]),
                       [0] * 12 + [1] * 108, [0] * 120, [0] * 16 + [1] * 104)   # bars 8..15: no kick, no bass
    c = _cost(out, ins, 176, 184, 200)                    # incoming bar 8: nothing but percussion
    assert "swap_kick_only" not in c.terms


def test_anchored_needs_the_swap_within_a_bar_of_the_30_second_mark():
    b, o, n = _blend7()
    late = _cost(o, n, 123, 147, 155)                     # on the bass-in, but only 15 s from the end
    assert late.swap_at_bass_in and not late.swap_anchored
    assert _cost(o, n, 115, 139, 147).swap_anchored       # 31 s from the original end
    assert _cost(o, n, 116, 140, 148).swap_anchored       # on the bass-in, within a bar of the 30 s mark (139.5)


def test_anchored_needs_the_swap_on_the_bass_in():
    b, o, n = _blend7()
    c = _cost(o, n, 123, 139, 155)                        # the 30 s mark, but incoming bar 16: kick only
    assert not c.swap_at_bass_in and not c.swap_anchored


def test_derived_entries_are_capped_at_two_minutes_before_the_end():
    # outgoing 60 bpm (4 s bars), incoming 240 bpm (1 s bars) with a bass-in at its bar 40: the swap
    # sits ~30 s from the end, so the derived entry would be 40 incoming bars (40 s) earlier... in
    # outgoing bars that is 40, i.e. 160 s: beyond the 120 s cap of 30 bars
    out = trk(300, [(0, "INTRO"), (50, "DROP")], fills=[(289, 290), (297, 298)], bpm=60.0)
    ins = with_content(trk(300, [(0, "INTRO"), (12, "BREAK"), (40, "DROP"), (48, "BREAK"), (60, "DROP")], bpm=240.0),
                       [0] * 40 + [1] * 260, [0] * 300, [1] * 300)
    cs = pb.candidates(out, ins)
    assert cs
    assert all(c.in_starts_at_out_bar >= 300 - 30 - 1e-9 for c in cs)       # literal: 120 s / 4 s


def test_alternatives_exclude_forbidden_cuts_costly_flat_and_respect_the_limits():
    best = pb.BlendPlan(100, 116, 132, 0.0, 0.0, cost=1.0, terms={})
    mk = lambda sw, cost, cut=0.0, flat=0.0: pb.BlendPlan(100 + sw, 116 + sw * 3, 132, 0.0, 0.0, out_cut_bars=cut,
                                                      cost=cost, terms={"flat": flat})
    cs = [best, mk(10, 2.0, cut=3.0), mk(20, 2.1, flat=5.0), mk(30, 2.2), mk(90, 50.0)]
    alts = pb.alternatives_for(cs, trk(100, [(0, "INTRO")]))
    got = [a.plan.in_starts_at_out_bar for a in alts]
    assert got == [130]                                      # not the cut (110), the flat run (120), or 190
    many = [best] + [mk(10 * i, 1.0 + 0.1 * i) for i in range(1, 11)]
    assert len(pb.alternatives_for(many, trk(100, [(0, "INTRO")]))) <= 4     # literal limit
    assert all(a.plan.cost <= best.cost + 5.0 for a in alts)  # literal: about a ten bar clock error


# ---- round 6: pairings (lining the swap up on a phrase point of BOTH tracks) ------------------
def _blend1(mode="BOTTOM END"):
    b = KEY["blends"][0]
    return (b, sb.prepare(KEY["tracks"][b["out"]], b["out"], mode, CONTENT),
            sb.prepare(KEY["tracks"][b["in"]], b["in"], mode, CONTENT))


def test_pairing_is_a_swap_on_a_phrase_point_of_both_tracks_and_the_entry_follows():
    b, o, n = _blend1()
    owner = _cost(o, n, 184, 200, 215)                    # A11 (outro start) with B1 (kick-in), outro looped
    assert owner.paired and owner.sane and owner.in_point_kind == "kick-in"
    assert "entry" not in owner.terms and "minute_mark" not in owner.terms     # entry is derived, not chosen
    assert any("pairing" in w for w in owner.why)


def test_blend_1_the_owners_named_pairings_are_candidates_and_his_own_answer_ranks_first():
    b, o, n = _blend1()
    cs = pb.candidates(o, n)
    keys = [c.key() for c in cs]
    assert keys[0] == (184.0, 200.0, 215.0)               # A11 with B1, outro looped a little
    assert keys.index((168.0, 184.0, 216.0)) < 6          # A-F7 end with B1 (kick-in)
    assert (168.0, 200.0, 216.0) in keys                  # A11 with B-F1 end (bass-in)
    # A-F7 end with B-F1 end would stop the outgoing at its outro start, dropping all 9 outro bars
    assert (152.0, 184.0, 200.0) not in keys
    assert _cost(o, n, 152, 184, 200).out_cut_bars == 9
    assert not any(c.key() == (161.0, 193.0, 209.0) and c.sane for c in cs)   # the round 5 pick is one-sided


def test_a_sane_pairing_outranks_every_one_sided_plan_even_a_far_cheaper_one():
    plans = [pb.BlendPlan(1, 2, 3, 0, 0, cost=0.1, sane=False), pb.BlendPlan(1, 2, 3, 0, 0, cost=90.0, sane=True)]
    plans.sort(key=pb._rank_key)
    assert plans[0].sane


def test_sane_needs_bottom_end_no_forbidden_cut_a_swap_near_the_mark_and_a_sane_length():
    out = with_content(trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(167, 168), (183, 184)]),
                       [1] * 200, [0] * 200, [1] * 200)
    ins = with_content(trk(120, [(0, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)]),
                       [1] * 120, [0] * 120, [1] * 120)
    good = _cost(out, ins, 168, 184, 200)                 # swap 16 bars (30 s) from the end, 32 bar blend
    assert good.paired and good.sane
    out_far = with_content(trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(159, 160), (167, 168), (183, 184)]),
                           [1] * 200, [0] * 200, [1] * 200)
    far = _cost(out_far, ins, 144, 160, 200)              # swap 40 bars (75 s) before the end, blend 105 s
    assert far.paired and not far.sane
    kickless = with_content(out, [1] * 168 + [0] * 32, [0] * 200, [1] * 168 + [0] * 32)
    quiet = with_content(trk(120, [(0, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)]),
                         [0] * 120, [0] * 120, [0] * 120)
    flat = _cost(kickless, quiet, 168, 184, 200)
    assert flat.paired and flat.terms["flat"] > 0 and not flat.sane
    cutter = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(167, 168), (183, 184), (195, 196)])
    cut = _cost(cutter, ins, 168, 184, 190)
    assert cut.out_cut_bars > 0 and not cut.sane


def test_sane_window_and_blend_length_are_in_seconds():
    out = with_content(trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(167, 168), (183, 184)]),
                       [1] * 200, [0] * 200, [1] * 200)
    ins = with_content(trk(120, [(0, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)]),
                       [1] * 120, [0] * 120, [1] * 120)
    # 128 bpm: 1.875 s a bar.  Swap 168 is 32 bars (60 s) from the end: 30 s off the mark: not sane
    assert not _cost(out, ins, 152, 168, 184).sane
    assert _cost(out, ins, 168, 184, 200).sane
    assert pb.PAIR_WINDOW_SECONDS == 20.0 and pb.SANE_BLEND_SECONDS == 80.0


def test_incoming_point_quality_runs_bass_in_kick_in_fill_end_section_start_none():
    n = with_content(trk(120, [(0, "INTRO"), (16, "DROP"), (24, "BREAK"), (40, "DROP")], fills=[(7, 8)]),
                     [0] * 24 + [1] * 96, [0] * 120, [1] * 120)
    kinds = [pb.in_point_kind(n, k) for k in (24, 16, 8, 40, 11)]
    assert [k[0] for k in kinds] == ["bass-in", "kick-in", "fill end", "section start", ""]
    costs = [k[1] for k in kinds]
    assert costs == sorted(costs) and len(set(costs)) == 5 and costs[0] == 0.0


def test_kick_in_is_the_first_drop_or_build_after_the_start():
    assert pb.kick_in(trk(100, [(0, "INTRO"), (24, "BUILD"), (40, "DROP")])) == 24.0
    assert pb.kick_in(trk(100, [(0, "DROP"), (30, "BREAK")])) is None
    assert pb.kick_in(trk(100, [(0, "INTRO"), (16, "DROP")])) == 16.0


def test_the_in_point_term_prefers_a_bass_in_pairing_to_a_section_start_pairing():
    n = with_content(trk(120, [(0, "INTRO"), (16, "DROP"), (24, "BREAK"), (40, "BREAK")]),
                     [0] * 24 + [1] * 96, [0] * 120, [1] * 120)
    out = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(183, 184)])
    a = _cost(out, n, 160, 184, 200)                      # k = 24: bass-in
    b = _cost(out, n, 144, 184, 200)                      # k = 40: a plain section start
    assert a.in_point_kind == "bass-in" and b.in_point_kind == "section start"
    assert a.terms.get("in_point", 0.0) < b.terms["in_point"]


def test_a_pairing_waives_the_entry_type_and_minute_mark_terms_a_one_sided_swap_keeps_them():
    out = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(183, 184)])
    paired_in = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    one_in = trk(120, [(0, "INTRO", True), (13, "DROP"), (32, "BREAK")])
    p = _cost(out, paired_in, 168, 184, 200)              # entry 168 is no outgoing phrase point
    q = _cost(out, one_in, 168, 184, 200)
    assert p.paired and "entry" not in p.terms
    assert not q.paired and q.terms["entry"] > 5


def test_every_swap_in_the_candidates_is_inside_the_mix_zone():
    b, o, n = _blend1()
    assert all(c.bass_switch_out_bar >= o["n_bars"] - 40 - 1e-9 for c in pb.candidates(o, n))   # 75 s = 40 bars


def test_the_scorer_prints_three_numbers_per_column_and_the_info_line():
    lines = []
    sb.table(out=lines.append)
    text = "\n".join(lines)
    for head in ("primary           |", "primary or accept.|", "primary in top 3  |", "(info) swap bar equals his"):
        assert head in text


def test_a_pairing_on_an_outgoing_section_start_entry_pays_no_entry_cost_either():
    out = trk(200, [(0, "INTRO"), (16, "DROP"), (168, "DROP")], fills=[(183, 184)])
    ins = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    p = _cost(out, ins, 168, 184, 200)                    # entry on the outgoing section start 168, swap paired
    assert p.paired and "entry" not in p.terms
    assert _cost(out, trk(120, [(0, "INTRO", True), (13, "DROP"), (32, "BREAK")]), 168, 184, 200).terms["entry"] > 0.5


def test_the_scorer_separates_primary_from_acceptable_and_swap_only(monkeypatch):
    monkeypatch.setattr(pb, "plan_blend", lambda *a, **k: pb.BlendPlan(115.0, 131.0, 147.0, 0.0, 0.0))
    exact, rows = sb.score("BOTTOM END", out=lambda *_: None)
    assert rows[6]["which"] == "acceptable #1" and rows[6]["ok"] and not rows[6]["primary"]
    assert rows[6]["swap_only"] and rows[0]["swap_only"] is False
