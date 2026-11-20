"""Round 7 tests: implied phrase points (a regular 8 bar grid from each section start), snapping of
lines within a beat of a bar, pairings that use implied points, and the six-alternative cap."""
import json
import sys
from pathlib import Path

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


def tr(name_part):
    name = next(n for n in KEY["tracks"] if name_part in n)
    return KEY["tracks"][name]


def _cost(out, ins, a, s, e):
    return pb._score(out, ins, a, s, e, 0.0, 0.0, pb.phrase_points(out), pb.phrase_points(ins))


# ---- the grid ---------------------------------------------------------------------------------
def test_implied_points_run_every_8_bars_from_each_section_start_16s_are_stronger():
    t = trk(100, [(0, "INTRO"), (30, "DROP")])
    ip = pb.implied_points(t)
    assert ip[8.0] == 8 and ip[16.0] == 16 and ip[24.0] == 8      # from bar 0 until the next section (30)
    assert 32.0 not in ip and ip[38.0] == 8 and ip[46.0] == 16    # the grid RESTARTS at 30: 38, 46, 54 ...
    assert 30.0 not in ip                                          # a marked section start is not repeated


def test_the_grid_restarts_at_every_section_start_not_from_bar_zero():
    # Ashibah's drop returns at 74 (off the grid of 0): 80 is not a point, and 82 is its marked fill end
    ashibah = tr("Ashibah")
    ip = pb.implied_points(ashibah)
    assert 80.0 not in ip and pb.point_type(ashibah, 82) == "marked fill end"
    # Call 911's sections are at 76, 82, 107, 115: from 82 the grid runs 90, 98, 106
    call = tr("Call 911")
    ipc = pb.implied_points(call)
    assert ipc[90.0] == 8 and ipc[98.0] == 16 and 88.0 not in ipc
    assert ipc[139.0] == 8                                         # 115 + 24: the owner's blend 7 swap


def test_implied_points_never_repeat_marked_points_or_reach_the_next_section():
    for name, t in KEY["tracks"].items():
        marked = set(pb.phrase_points(t))
        secs = sorted({x[0] for x in pb._sections(t)})
        for b in pb.implied_points(t):
            assert b not in marked and 0 < b < t["n_bars"]


def test_point_type_names_every_kind():
    t = trk(100, [(0, "INTRO"), (20, "DROP")], fills=[(11, 12)])
    assert pb.point_type(t, 12) == "marked fill end"
    assert pb.point_type(t, 20) == "marked section start"
    assert pb.point_type(t, 0) == "marked section start" and pb.point_type(t, 100) == "marked section start"
    assert pb.point_type(t, 36) == "implied 16" and pb.point_type(t, 28) == "implied 8"
    assert pb.point_type(t, 25) == ""
    assert pb.point_type(t, 8) == "implied 8" and pb.point_type(t, 16) == "implied 16"


# ---- verification of the owner's seven swaps: a type on each side, never none ----------------
OWNER_TYPES = [
    ("marked fill end", "marked section start"),    # blend 1: A11 with B1
    ("marked fill end", "marked fill end"),         # 2
    ("marked fill end", "implied 16"),              # 3: Ninetoes bar 16 is 16 from the start
    ("implied 16", "marked fill end"),              # 4: Ninetoes 176 is 16 after its outro start
    ("marked fill end", "marked section start"),    # 5
    ("marked fill end", "implied 16"),              # 6: Call 911 bar 16
    ("implied 8", "marked fill end"),               # 7: Call 911 139 is 115 + 24
]


def test_every_owner_swap_has_a_phrase_point_type_on_each_side():
    for i, (b, expect) in enumerate(zip(KEY["blends"], OWNER_TYPES), 1):
        o, n = KEY["tracks"][b["out"]], KEY["tracks"][b["in"]]
        sw = b["bass_switch_out_bar"]
        k = sw - b["in_starts_at_out_bar"]
        got = (pb.point_type(o, sw), pb.point_type(n, k))
        assert got == expect, (i, got)
        assert "" not in got


def test_the_owners_second_blend_7_answer_and_the_rejected_swap():
    b = KEY["blends"][6]
    o, n = KEY["tracks"][b["out"]], KEY["tracks"][b["in"]]
    ac = b["also_acceptable"][0]
    assert pb.point_type(o, ac["bass_switch_out_bar"]) == "marked fill end"
    assert pb.point_type(n, ac["bass_switch_out_bar"] - ac["in_starts_at_out_bar"]) == "implied 16"
    bitd = tr("Back In The Dance")
    assert pb.point_type(bitd, 193) == ""                          # "randomly slapped anywhere": on no grid
    assert pb.point_type(bitd, 192) == "implied 16"


# ---- snapping ---------------------------------------------------------------------------------
def test_a_line_within_one_beat_of_a_bar_is_that_bar_other_fractions_stay():
    assert pb._snap(135.75) == 136.0 and pb._snap(23.75) == 24.0 and pb._snap(24.2) == 24.0
    assert pb._snap(23.5) == 23.5 and pb._snap(106.4) == 106.4


def test_passions_fractional_outro_line_is_the_bar_line_136_in_every_point_set():
    passion = tr("Passion")
    assert 136.0 in pb.phrase_points(passion) and 135.75 not in pb.phrase_points(passion)
    assert pb.point_type(passion, 135.75) == "marked section start"
    for c in pb.candidates(passion, tr("Ninetoes")):
        assert abs(c.bass_switch_out_bar - round(c.bass_switch_out_bar)) < 1e-9 or c.bass_switch_out_bar % 0.25 != 0


def test_half_bar_fill_starts_stay_fractional_as_end_targets():
    call = tr("Call 911")
    assert 23.5 in pb.target_points(call)


# ---- pairings that use implied points ---------------------------------------------------------
def test_a_marked_point_with_an_implied_point_is_a_real_pairing():
    out = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(183, 184)])
    ins = trk(120, [(0, "INTRO", True), (30, "DROP"), (46, "BREAK")])
    p = _cost(out, ins, 168, 184, 200)                   # A fill end (marked) with B bar 16 (implied 16)
    assert p.paired and p.out_point_kind == "marked fill end" and p.in_point_kind == "implied 16"


def test_implied_with_implied_is_not_a_pairing():
    out = trk(200, [(0, "INTRO"), (16, "DROP")])          # no fills: bar 176 is implied
    ins = trk(120, [(0, "INTRO", True), (30, "DROP"), (46, "BREAK")])
    p = _cost(out, ins, 160, 176, 192)                   # 176 implied 16 on A, B bar 16 implied 16
    assert pb.point_type(out, 176) == "implied 16" and pb.point_type(ins, 16) == "implied 16"
    assert not p.paired and not p.sane
    assert any("implied on both" in w for w in p.why)


def test_an_implied_outgoing_point_costs_a_little_more_than_a_marked_one():
    ins = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    marked = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(183, 184)])
    implied = trk(200, [(0, "INTRO"), (16, "DROP")])
    a = _cost(marked, ins, 168, 184, 200)
    b = _cost(implied, ins, 168, 184, 200)               # 184 = 16 + 21*8: implied 8 (or 16)
    assert "out_point" not in a.terms and b.terms["out_point"] > 0.4
    t16 = trk(200, [(0, "INTRO"), (40, "DROP")])          # 184 = 40 + 144: implied 16
    c = _cost(t16, ins, 168, 184, 200)
    assert 0.4 < c.terms["out_point"] < b.terms["out_point"] + 1e-9 or c.terms["out_point"] == pb.W_OUT_IMPL16


def test_incoming_quality_order_with_implied_points_in_it():
    n = trk(120, [(0, "INTRO"), (24, "DROP"), (40, "BREAK")], fills=[(11, 12)])
    kinds = [pb.in_point_kind(n, k) for k in (24, 12, 40, 16, 8, 5)]
    assert [k[0] for k in kinds] == ["kick-in", "fill end", "section start", "implied 16", "implied 8", ""]
    costs = [k[1] for k in kinds]
    assert costs == sorted(costs) and len(set(costs)) == 6


def test_the_owner_swaps_in_blends_3_4_6_7_are_sane_pairings_and_top_ranked():
    for idx in (2, 3, 5, 6):
        b = KEY["blends"][idx]
        o = sb.prepare(KEY["tracks"][b["out"]], b["out"], "BOTTOM END", CONTENT)
        n = sb.prepare(KEY["tracks"][b["in"]], b["in"], "BOTTOM END", CONTENT)
        sam = sb.sam_numbers(b, o)
        top = pb.candidates(o, n)[0]
        assert top.key() == tuple(float(x) for x in sam) and top.paired and top.sane


def test_blend_1_the_owner_is_first_the_loop_term_that_taxed_him_is_gone():
    b = KEY["blends"][0]
    o = sb.prepare(KEY["tracks"][b["out"]], b["out"], "BOTTOM END", CONTENT)
    n = sb.prepare(KEY["tracks"][b["in"]], b["in"], "BOTTOM END", CONTENT)
    cs = pb.candidates(o, n)
    top, own = cs[0], pb._score(o, n, 184, 200, 215, 0.0, 0.0, pb.phrase_points(o), pb.phrase_points(n))
    assert top.key() == own.key() == (184.0, 200.0, 215.0)
    assert own.terms.get("out_loop", 0.0) == 0.0          # round 8, fix 2: the 6 bar loop to the next
    assert own.out_looped_bars == 6                     # fill start is free, exactly as he prescribed


def test_the_scorer_prints_the_owner_rank_among_sane_pairings_and_the_types():
    lines = []
    sb.score("BOTTOM END", out=lines.append)
    text = "\n".join(lines)
    assert "owner's primary among the" in text and "sane pairings: rank 1" in text
    assert "types: A bar 139 = implied 8, B bar 24 = marked fill end" in text
    # round 8: every owner answer is the top pick, so the "terms separating" line never prints
    assert "terms separating the owner's primary from the top pick" not in text


# ---- six alternatives -------------------------------------------------------------------------
def test_up_to_six_alternatives_and_the_owners_acceptable_answer_is_offered():
    assert pb.MAX_ALTERNATIVES == 6
    b = KEY["blends"][6]
    o = sb.prepare(KEY["tracks"][b["out"]], b["out"], "BOTTOM END", CONTENT)
    n = sb.prepare(KEY["tracks"][b["in"]], b["in"], "BOTTOM END", CONTENT)
    p = pb.plan_blend(o, n)
    assert 1 <= len(p.alternatives) <= 6
    assert (115.0, 131.0, 147.0) in [a.plan.key() for a in p.alternatives]


def test_implied_with_implied_pairs_are_never_generated():
    for idx in (3, 6):
        b = KEY["blends"][idx]
        o = sb.prepare(KEY["tracks"][b["out"]], b["out"], "BOTTOM END", CONTENT)
        n = sb.prepare(KEY["tracks"][b["in"]], b["in"], "BOTTOM END", CONTENT)
        for c in pb.candidates(o, n):
            assert not (c.out_point_kind.startswith("implied") and c.in_point_kind.startswith("implied"))


def test_a_swap_on_an_implied_outgoing_point_is_generated_when_nothing_marked_leads_to_it():
    out = trk(200, [(0, "INTRO"), (16, "DROP")])          # no fills: 184 and 192 are implied points only
    ins = trk(120, [(0, "INTRO", True), (16, "DROP"), (32, "BREAK")])
    cs = pb.candidates(out, ins)
    hit = [c for c in cs if c.bass_switch_out_bar == 184.0 and c.in_starts_at_out_bar == 168.0]
    assert hit and hit[0].paired and hit[0].out_point_kind.startswith("implied")


def test_a_fill_end_within_a_beat_of_a_bar_is_that_bar():
    t = trk(100, [(0, "INTRO"), (40, "DROP")], fills=[(23, 24.2)])
    assert 24.0 in pb.phrase_points(t) and 24.2 not in pb.phrase_points(t)
    assert pb.point_type(t, 24) == "marked fill end"
