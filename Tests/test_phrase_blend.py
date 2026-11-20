"""Tests for Source/phrase_blend.py: the six answer-key blends pinned, plus one or more unit
tests per rule R1..R9.  Run:  python -I -m pytest Tests/test_phrase_blend.py

Synthetic tracks are small on purpose so each test isolates one rule.  Every rule test
must fail if its rule is removed (see the mutation table in the build report).
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


def trk(n, sections, fills=(), bpm=128.0):
    return {"bpm": bpm, "n_bars": n, "sections": [list(s) for s in sections],
            "fills": [list(f) for f in fills]}


# A plain outgoing track: 200 bars, bass and drop to its very last bar (no outro, R8) with fills.
OUT = trk(200, [(0, "INTRO"), (16, "DROP"), (100, "BREAK"), (120, "DROP")],
          fills=[(135, 136), (151, 152), (167, 168), (183, 184)])
# A plain incoming track: short quiet intro, drop at 16, break at 32, drop at 48.
IN = trk(160, [(0, "INTRO"), (16, "DROP"), (32, "BREAK"), (48, "DROP")], fills=[(31, 32)])


# ---- the six answer-key blends, pinned ----------------------------------------------------
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


@pytest.mark.parametrize("idx,a,s,e", [SIX[0], SIX[1], SIX[4]])
def test_blends_1_2_5_need_no_override(idx, a, s, e):
    b = KEY["blends"][idx]
    for r6 in (pb.R6_V1, pb.R6_BE):
        plan = pb.plan_blend(KEY["tracks"][b["out"]], KEY["tracks"][b["in"]], r6=r6)
        assert plan.key() == (a, s, e)


def test_blends_3_4_6_are_flat_by_label_without_overrides():
    # R6 read from labels alone calls Sam's confirmed blends 3, 4 and 6 flat: Sam's own
    # placement scores 16 / 32 / 16 flat bars.  The carries override is the mechanism.
    flat = {}
    for idx, a, s, e in (SIX[2], SIX[3], SIX[5]):
        b = KEY["blends"][idx]
        o, n = KEY["tracks"][b["out"]], KEY["tracks"][b["in"]]
        _, f = pb.content_check(o, n, a, s, e)
        flat[idx] = f
    assert flat == {2: 16.0, 3: 32.0, 5: 16.0}


def test_key_file_is_not_edited_by_overrides():
    t = KEY["tracks"]["Ninetoes - Come Back"]
    assert all(len(s) == 2 for s in t["sections"])
    c = sb.apply_overrides(t, "Ninetoes - Come Back", sb.OVERRIDES)
    assert any(len(s) == 3 for s in c["sections"]) and all(len(s) == 2 for s in t["sections"])


# ---- R1: entry on an outgoing phrase point, normally a fill end ---------------------------
def test_r1_entry_prefers_fill_end():
    plan = pb.plan_blend(OUT, IN)
    fill_ends = {f[1] for f in OUT["fills"]}
    assert plan.in_starts_at_out_bar in fill_ends


def test_r1_fill_end_beats_plain_section_start_at_equal_geometry():
    # Out 100 bars, outro at 68, fill end at 68 AND 69: two entries one bar apart; only the
    # fill-end one is a fill end, a section-start-only entry must cost more.
    out = trk(100, [(0, "INTRO"), (36, "DROP"), (68, "OUTRO")], fills=[(35, 36)])
    ins = trk(100, [(0, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)])
    cands = pb.candidates(out, ins)
    same = {}
    for c in cands:
        same.setdefault((c.bass_switch_out_bar, c.out_end_bar), []).append(c)
    a_fill = [c for c in cands if c.in_starts_at_out_bar == 36][0]
    a_sec = [c for c in cands if c.in_starts_at_out_bar == 68][0]
    assert any("end of an outgoing fill" in w for w in a_fill.why)
    assert any("section start (not a fill end)" in w for w in a_sec.why)


def test_r1_aims_for_about_a_minute():
    plan = pb.plan_blend(OUT, IN)
    assert plan.in_starts_at_out_bar == 168            # 32 bars from the end, a fill end
    assert 28 <= plan.out_end_bar - plan.in_starts_at_out_bar <= 36


# ---- R1/R2 cost ordering, tested on _score directly so the clock terms are equal -----------
def _cost(out, ins, a, s, e):
    return pb._score(out, ins, a, s, e, 0.0, 0.0, pb.phrase_points(out), pb.phrase_points(ins)).cost


def test_r1_entry_cost_order_fill_end_then_section_start_then_unphrased():
    # entries 169 and 167 are both one bar off a minute; only what sits there differs
    base = [(0, "INTRO"), (16, "DROP")]
    o_fill = trk(200, base, fills=[(168, 169), (183, 184)])
    o_sec = trk(200, base + [(167, "DROP")], fills=[(183, 184)])
    o_off = trk(200, base, fills=[(183, 184)])
    ins = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    c_fill = _cost(o_fill, ins, 169, 184, 200)
    c_sec = _cost(o_sec, ins, 167, 184, 200)
    c_off = _cost(o_off, ins, 167, 184, 200)
    assert c_fill < c_sec < c_off
    assert c_off - c_sec > 3 and c_sec - c_fill > 0.5


def test_r2_switch_cost_order_both_then_one_then_none():
    out = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(167, 168), (183, 184)])
    in_both = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    in_none = trk(120, [(0, "INTRO", True), (13, "DROP"), (32, "BREAK")])
    both = _cost(out, in_both, 168, 184, 200)      # out phrase 184, incoming phrase 16
    one_out = _cost(out, in_none, 168, 184, 200)   # out phrase 184 only
    none = _cost(out, in_none, 168, 188, 200)      # neither
    assert both < one_out < none
    assert none - one_out > 5
    assert one_out - both > 0.5


# ---- R2: switch on a phrase point of both tracks ------------------------------------------
def test_r2_double_phrase_switch_wins():
    plan = pb.plan_blend(OUT, IN)
    k = plan.bass_switch_out_bar - plan.in_starts_at_out_bar - plan.in_intro_looped_bars
    assert plan.bass_switch_out_bar in pb.phrase_points(OUT)
    assert k in pb.phrase_points(IN)
    assert any("phrase point of both tracks" in w for w in plan.why)


def test_r2_switch_never_off_every_phrase_point_when_a_phrase_exists():
    for c in pb.candidates(OUT, IN, limit=5):
        assert not any("no phrase point of either" in w for w in c.why)


# ---- R3: end on the incoming's next section; loop the tail up to a phrase point -----------
def test_r3_outgoing_ends_naturally_on_incoming_section_start():
    out = trk(160, [(0, "INTRO"), (16, "DROP"), (128, "OUTRO")], fills=[(111, 112)])
    ins = trk(160, [(0, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)])
    plan = pb.plan_blend(out, ins)
    assert plan.out_end_bar == 160 and plan.out_looped_bars == 0
    assert plan.out_end_bar - plan.in_starts_at_out_bar == 32  # incoming BREAK at 32


def test_r3_short_outro_loops_tail_to_incoming_fill_start():
    # Out ends 25 bars after its last fill end; incoming has a fill starting at bar 31.
    out = trk(209, [(0, "INTRO"), (24, "DROP"), (200, "OUTRO")], fills=[(183, 184)])
    ins = trk(100, [(0, "INTRO"), (16, "DROP"), (48, "DROP")], fills=[(31, 32)])
    plan = pb.plan_blend(out, ins)
    assert plan.in_starts_at_out_bar == 184
    assert plan.out_end_bar == 184 + 31
    assert plan.out_looped_bars == 6


def test_r3_end_target_is_section_or_fill_start_never_fill_end():
    t = trk(100, [(0, "INTRO"), (16, "DROP")], fills=[(7, 8)])
    assert pb.target_points(t) == [7.0, 16.0]  # fill START 7 yes, fill END 8 no


def test_r3_incoming_intro_loop_is_a_candidate_for_a_long_gap():
    # Out phrase points only far from the end and an 8-bar incoming intro: the planner
    # must be able to lengthen with an incoming intro loop.
    out = trk(120, [(0, "INTRO"), (24, "DROP"), (88, "OUTRO")], fills=[(87, 88)])
    ins = trk(120, [(0, "INTRO"), (8, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)])
    assert any(c.in_intro_looped_bars > 0 for c in pb.candidates(out, ins))


def test_r3_incoming_intro_loop_shifts_incoming_time():
    assert [pb._in_bar(x, 0, 8, 8) for x in (0, 7, 8, 15, 16, 20)] == [0, 7, 0, 7, 8, 12]


# ---- R4: switch from ORIGINAL phrase points; prefer no loop -------------------------------
def test_r4_prefers_a_plan_with_no_loop_when_one_fits():
    plan = pb.plan_blend(OUT, IN)
    assert plan.out_looped_bars == 0 and plan.in_intro_looped_bars == 0


def test_r4_switch_is_never_a_loop_derived_point():
    # No candidate's switch may sit at or beyond the outgoing's own last bar: loops lengthen
    # only.  (Entry 184 + incoming phrase 16 = 200 = n_bars used to slip through.)
    cs = pb.candidates(OUT, IN)
    assert cs
    for c in cs:
        assert c.bass_switch_out_bar < OUT["n_bars"]


def test_r4_no_loop_beats_a_looped_plan_that_has_a_better_clock():
    # Unlooped: entry 176, switch 184 (a phrase point of BOTH tracks), end 200 = incoming
    # bar 24: a 24-bar blend, 8 short of a minute.  Looped: same entry and switch, tail
    # looped 8 bars to 208 = incoming bar 32: a perfect minute.  R4: the unlooped plan wins.
    out = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(175, 176), (183, 184)])
    ins = trk(120, [(0, "INTRO"), (8, "DROP"), (24, "BREAK"), (32, "DROP")])
    cs = pb.candidates(out, ins)
    assert cs[0].key() == (176.0, 184.0, 200.0) and cs[0].out_looped_bars == 0
    looped = [c for c in cs if c.key() == (176.0, 184.0, 208.0)][0]
    assert looped.out_looped_bars == 8 and looped.cost > cs[0].cost


def test_r4_loop_costs_more_than_no_loop():
    base = pb.plan_blend(OUT, IN)
    assert base.out_looped_bars == 0
    looped = [c for c in pb.candidates(OUT, IN) if c.out_looped_bars > 0 and c.flat_bars == 0]
    assert looped and min(c.cost for c in looped) > base.cost
    # a loop of n bars adds exactly W_LOOP * n on top of an otherwise identical plan
    ref = {c.key(): c for c in pb.candidates(OUT, IN)}
    assert all(c.cost >= pb.W_LOOP * c.out_looped_bars for c in looped)


# ---- R5: never cut a usable outro short ---------------------------------------------------
def test_r5_best_plan_never_cuts():
    assert pb.plan_blend(OUT, IN).out_cut_bars == 0


def test_r5_cut_is_costed_above_an_equivalent_loop():
    cuts = [c for c in pb.candidates(OUT, IN) if c.out_cut_bars >= 4]
    loops = [c for c in pb.candidates(OUT, IN) if c.out_looped_bars >= 4]
    assert cuts and loops
    assert min(c.cost for c in cuts) > pb.W_CUT * 3
    assert all(any("CUT" in w for w in c.why) for c in cuts)


def test_r5_one_bar_slack_is_not_a_cut():
    # PAX is 193 bars; an incoming section at out bar 192 is reached naturally.
    b = KEY["blends"][1]
    plan = pb.plan_blend(KEY["tracks"][b["out"]], KEY["tracks"][b["in"]])
    assert plan.out_end_bar == 193 and plan.out_cut_bars == 0


# ---- R6: flat check ------------------------------------------------------------------------
def test_r6_flat_when_outro_over_intro():
    out = trk(100, [(0, "INTRO"), (20, "DROP"), (68, "OUTRO")])
    ins = trk(100, [(0, "INTRO"), (32, "DROP")])
    _, flat = pb.content_check(out, ins, 68, 84, 100)
    assert flat == 32  # 68-84: out OUTRO, in lows cut; 84-100: out bass gone, in INTRO


def test_r6_not_flat_when_incoming_carries_after_switch():
    out = trk(100, [(0, "INTRO"), (20, "DROP"), (68, "OUTRO")])
    ins = trk(100, [(0, "INTRO"), (16, "DROP")])
    _, flat = pb.content_check(out, ins, 68, 84, 100)
    assert flat == 16  # only 68-84 is flat; 84-100 incoming DROP carries


def test_r6_carries_flag_overrides_label():
    out = trk(100, [(0, "INTRO"), (20, "DROP"), (68, "OUTRO", True)])
    ins = trk(100, [(0, "INTRO"), (32, "DROP")])
    _, flat = pb.content_check(out, ins, 68, 84, 100)
    assert flat == 0
    out2 = trk(100, [(0, "INTRO"), (20, "DROP", False), (68, "OUTRO")])
    assert pb._out_state(out2, 30, 80) is False  # False flag beats a carrying label


def test_r6_after_the_switch_the_outgoing_is_drums_only():
    out = trk(100, [(0, "INTRO"), (20, "DROP")])
    assert pb._out_state(out, 50, 60) is True
    assert pb._out_state(out, 60, 60) is False


def test_r6_planner_avoids_flat_stretch():
    # Blend 3 without the carries override: Sam's placement is 16 bars flat by label, and
    # on every term but R6 it is the cheapest plan.  R6 alone moves the planner off it.
    b = KEY["blends"][2]
    o, n = KEY["tracks"][b["out"]], KEY["tracks"][b["in"]]
    cs = pb.candidates(o, n)
    sam = [c for c in cs if c.key() == (112.0, 128.0, 144.0)][0]
    assert sam.flat_bars == 16
    assert sam.cost - pb.W_FLAT * sam.flat_bars < cs[0].cost   # cheaper before R6 is applied
    assert cs[0].key() != sam.key() and cs[0].flat_bars == 0


def test_r6_flat_penalty_dominates_loop_and_clock():
    assert pb.W_FLAT > pb.W_LOOP * 4 and pb.W_FLAT > pb.W_LEN * 8


def test_r6_stretch_report_lists_what_plays():
    st, _ = pb.content_check(OUT, IN, 152, 168, 200)
    assert st[0]["start"] == 152 and st[-1]["end"] == 200
    assert {"out", "in", "flat"} <= set(st[0])


# ---- R7: the incoming enters with lows cut; no quiet intro needed -------------------------
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


# ---- R9: only phrase points inside the mix zones -------------------------------------------
def test_r9_far_phrase_points_are_not_entries():
    out = trk(400, [(0, "INTRO"), (50, "DROP"), (336, "OUTRO")], fills=[(49, 50), (335, 336)])
    ins = trk(160, [(0, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)])
    for c in pb.candidates(out, ins):
        assert c.in_starts_at_out_bar >= 336   # literal 400 - 64, not the module constant
        assert c.bass_switch_out_bar >= 336


def test_r9_incoming_phrase_points_beyond_64_bars_are_not_used_for_the_switch():
    ins = trk(300, [(0, "INTRO"), (100, "DROP"), (140, "BREAK")], fills=[(139, 140)])
    for c in pb.candidates(OUT, ins):
        k = c.bass_switch_out_bar - c.in_starts_at_out_bar - c.in_intro_looped_bars
        assert k <= 64


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
              "in_intro_looped_bars", "stretches", "why"):
        assert hasattr(p, f)
    assert p.why and all(isinstance(w, str) for w in p.why)
    assert not any(chr(0x2014) in w for w in p.why)


def test_candidates_are_ranked_best_first_and_deterministic():
    c1 = pb.candidates(OUT, IN)
    c2 = pb.candidates(OUT, IN)
    assert [c.cost for c in c1] == sorted(c.cost for c in c1)
    assert [c.key() for c in c1] == [c.key() for c in c2]


# ---- R6 on MEASURED content (round 2) ------------------------------------------------------
def with_content(track, bass, music):
    t = json.loads(json.dumps(track))
    t["content"] = {"has_bass": list(bass), "has_music": list(music)}
    return t


def test_measured_pre_switch_outgoing_needs_bass_or_music():
    out = with_content(trk(8, [(0, "INTRO")]), [0, 0, 1, 1, 0, 0, 0, 0], [0, 0, 0, 0, 1, 0, 0, 0])
    assert [pb._out_state(out, x, 6, pb.R6_V1) for x in range(6)] == [False, False, True, True, True, False]


def test_measured_post_switch_outgoing_needs_music_not_bass():
    out = with_content(trk(8, [(0, "INTRO")]), [1] * 8, [0, 0, 0, 0, 0, 0, 1, 0])
    assert pb._out_state(out, 3, 4, pb.R6_V1) is True      # before the switch: bass counts
    assert pb._out_state(out, 5, 4, pb.R6_V1) is False     # after: its bass has left, no music
    assert pb._out_state(out, 6, 4, pb.R6_V1) is True      # after: music still plays


def test_measured_incoming_counts_bass_or_music_after_the_switch_only():
    ins = with_content(trk(8, [(0, "INTRO")]), [1, 0, 0, 0, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0, 0, 0])
    v1 = pb.R6_V1
    assert pb._in_state(ins, 0, 0, 4, v1) is False   # pre-switch: percussion only (R7)
    assert pb._in_state(ins, 0, 4, 4, v1) is True    # bass
    assert pb._in_state(ins, 1, 4, 4, v1) is False
    assert pb._in_state(ins, 2, 4, 4, v1) is True    # music


def test_measured_content_beats_the_label():
    ins = with_content(trk(8, [(0, "DROP")]), [0] * 8, [0] * 8)    # label says carries; stems say not
    assert pb._in_state(ins, 3, 10, 4) is False
    ins2 = with_content(trk(8, [(0, "INTRO")]), [1] * 8, [0] * 8)  # label says not; stems say bass
    assert pb._in_state(ins2, 3, 10, 4) is True


def test_flag_beats_measured_content():
    out = with_content(trk(8, [(0, "INTRO", False)]), [1] * 8, [1] * 8)
    assert pb._out_state(out, 2, 6) is False
    ins = with_content(trk(8, [(0, "INTRO", True)]), [0] * 8, [0] * 8)
    assert pb._in_state(ins, 3, 10, 4) is True


def test_measured_content_beyond_the_track_reads_as_the_last_bar():
    out = with_content(trk(4, [(0, "DROP")]), [1, 1, 1, 0], [0, 0, 0, 1])
    assert pb._measured(out, 9) == (False, True, False) and pb._measured(out, -1) == (True, False, False)


def test_measured_flat_counts_each_side():
    v1 = pb.R6_V1
    out = with_content(trk(40, [(0, "DROP")]), [1] * 20 + [0] * 20, [0] * 40)
    ins = with_content(trk(40, [(0, "DROP")]), [0] * 40, [0] * 40)
    _, flat = pb.content_check(out, ins, 24, 32, 40, r6=v1)   # outgoing has nothing from bar 20
    assert flat == 16
    ins2 = with_content(trk(40, [(0, "DROP")]), [0] * 8 + [1] * 32, [0] * 40)
    _, flat2 = pb.content_check(out, ins2, 24, 32, 40, r6=v1)  # incoming bass from its bar 8 = out bar 32
    assert flat2 == 8     # only the 8 pre-switch bars, where the outgoing has nothing


def test_measured_planner_avoids_a_stretch_the_labels_would_pass():
    # labels call this outgoing DROP to the end (carries); the stems say its bass stops at 76
    out = trk(100, [(0, "INTRO"), (10, "DROP")], fills=[(67, 68), (83, 84)])
    out_m = with_content(out, [0] * 10 + [1] * 66 + [0] * 24, [0] * 100)
    ins = trk(100, [(0, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)])
    ins_m = with_content(ins, [0] * 16 + [1] * 84, [0] * 100)
    by_label = pb.plan_blend(out, ins, r6=pb.R6_V1)
    by_stems = pb.plan_blend(out_m, ins_m, r6=pb.R6_V1)
    assert by_label.flat_bars == 0
    seen_by_stems = pb.content_check(out_m, ins_m, *by_label.key(), r6=pb.R6_V1)[1]
    assert seen_by_stems > 0                       # the label plan IS flat on the stems
    assert by_stems.flat_bars == seen_by_stems == 8   # no phrase plan escapes: reported, not hidden


def _measured_rows(mode):
    key = json.loads(sb.KEY.read_text(encoding="utf-8"))
    content = json.loads(sb.CONTENT.read_text(encoding="utf-8"))
    r6 = sb.MODES[mode][1]
    rows = []
    for b in key["blends"]:
        o = sb.prepare(key["tracks"][b["out"]], b["out"], mode, content)
        n = sb.prepare(key["tracks"][b["in"]], b["in"], mode, content)
        sam = sb.sam_numbers(b, o)
        plan = pb.plan_blend(o, n, r6=r6)
        sam_plan = pb.content_check(o, n, *sam, r6=r6)
        rows.append((plan.key() == tuple(float(x) for x in sam), sam_plan[1],
                     pb.flat_runs(sam_plan[0])))
    return rows


def test_measured_v1_score_is_pinned_unaided():
    # Round 2 rule, real stems, NO overrides, no tuning: blends 1, 3, 5, 6 exact; blends 2 and 4
    # not.  Flat bars the planner sees on Sam's own placement: blend 2 = 11, blend 4 = 14.
    rows = _measured_rows("MEASURED")
    assert [r[0] for r in rows] == [True, False, True, False, True, True]
    assert [r[1] for r in rows] == [0, 11, 0, 14, 0, 0]


def test_bottom_end_score_is_pinned_unaided():
    # Sam's statement of R6 (bottom end), real stems, no overrides, no tuning.  Blends 1-5 exact.
    # Blend 6 misses, and not because of R6: Sam's placement and the planner's pick both have
    # no flat bar; the planner prefers a switch at out bar 168 that is a phrase point of both
    # tracks over Sam's at 160 (outgoing phrase point only), a 2.0 vs 1.81 trade of R2 terms.
    # Sam's placement has no flat run before its switch in blend 4 and none after it in blend 2
    # (only the last, partial bar 192 of PAX, where nothing is left).
    rows = _measured_rows("BOTTOM END")
    assert [r[0] for r in rows] == [True, True, True, True, True, False]
    assert [r[2] for r in rows] == [[], [(192.0, 1.0)], [], [], [], []]


# ---- R6 bottom end (Sam, 2026-11-20) -------------------------------------------------------
def bottom(track_kick, track_bass, music=None, n=None):
    n = n or len(track_kick)
    t = with_content(trk(n, [(0, "INTRO")]), track_bass, music or [0] * n)
    t["content"]["has_kick"] = list(track_kick)
    return t


def test_be_outgoing_before_switch_needs_kick_or_bass_after_switch_only_kick():
    out = bottom([0, 1, 0, 0, 0, 1, 0, 0], [0, 0, 1, 0, 0, 0, 1, 0], music=[1] * 8)
    be = pb.R6_BE
    assert [pb._out_state(out, x, 4, be) for x in range(4)] == [False, True, True, False]
    assert [pb._out_state(out, x, 4, be) for x in range(4, 8)] == [False, True, False, False]
    # bass after the switch is cut; music never counts


def test_be_incoming_gives_nothing_before_the_switch_then_kick_or_bass():
    ins = bottom([1, 0, 1, 0, 0, 0], [1, 1, 0, 0, 0, 0], music=[1] * 6)
    be = pb.R6_BE
    assert pb._in_state(ins, 0, 0, 3, be) is False and pb._in_state(ins, 1, 2, 3, be) is False
    assert pb._in_state(ins, 0, 3, 3, be) is True      # kick
    assert pb._in_state(ins, 1, 4, 3, be) is True      # bass
    assert pb._in_state(ins, 3, 6, 3, be) is False     # music alone does not give bottom end


def test_be_music_does_not_rescue_a_bar_but_is_reported():
    out = bottom([0] * 12, [0] * 12, music=[1] * 12)
    ins = bottom([0] * 12, [0] * 12, music=[1] * 12)
    st, flat = pb.content_check(out, ins, 0, 6, 12, r6=pb.R6_BE)
    assert flat == 12 and len(st) == 1 and st[0]["flat"] and st[0]["music_bars"] == 12


def test_be_a_one_bar_kick_drop_is_a_free_flat_run():
    # outgoing kick drops for one bar (a fill) before the switch
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


def test_be_planner_prefers_a_plan_with_kicks_over_a_kickless_one():
    out = bottom([1] * 100, [0] * 100)
    out["sections"] = [[0, "INTRO"], [10, "DROP"]]
    out["fills"] = [[67, 68], [83, 84]]
    out["bpm"] = 128.0
    # incoming has no kick before its bar 16 and then a full kick; any plan is fine for the
    # outgoing (kick everywhere).  The planner must still produce a plan with no flat run.
    ins = bottom([0] * 16 + [1] * 84, [0] * 100)
    ins["sections"] = [[0, "INTRO"], [16, "DROP"], [32, "BREAK"]]
    ins["fills"] = [[31, 32]]
    ins["bpm"] = 128.0
    p = pb.plan_blend(out, ins)
    assert p.flat_runs == [] and p.flat_bars == 0


def test_flat_cost_is_free_up_to_one_phrase_then_convex():
    f = pb.FREE_FLAT_BARS
    assert pb.flat_cost([(0, f)]) == 0 and pb.flat_cost([(0, 1)]) == 0
    c1, c2, c3 = (pb.flat_cost([(0, f + d)]) for d in (4, 8, 16))
    assert 0 < c1 < c2 < c3
    assert c2 > 2 * c1 and c3 > 2 * c2


def test_flat_cost_charges_each_continuous_run_not_the_total():
    f = pb.FREE_FLAT_BARS
    assert pb.flat_cost([(0, f), (20, f)]) == 0     # 16 flat bars in total, never 9 in a row
    assert pb.flat_cost([(0, 2 * f)]) > 0


def test_v1_flat_cost_is_the_per_bar_ban():
    assert pb.flat_cost([(0, 3), (9, 2)], pb.R6_V1) == pb.W_FLAT * 5


def test_flat_weight_makes_a_minute_of_drums_over_drums_as_dear_as_a_12_bar_cut():
    assert pb.flat_cost([(0, 32)]) == pytest.approx(pb.W_CUT * 12)


def test_plan_cost_is_the_sum_of_its_terms_and_reports_flat_runs():
    for c in pb.candidates(OUT, IN, limit=8):
        assert c.cost == pytest.approx(sum(c.terms.values()))
        assert c.flat_bars == sum(n for _, n in c.flat_runs)
