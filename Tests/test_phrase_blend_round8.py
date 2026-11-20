"""Round 8 tests: the nine corrections from brief_blendfix_r1, one test per fix.

Each of these failed at commit 01d75a1 (the state before the round-8 fixes) and passes
after them.  Run:  python -I -m pytest Tests/test_phrase_blend_round8.py
"""
import json
import subprocess
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


def with_content(track, bass, music, kick=None):
    t = json.loads(json.dumps(track))
    t["content"] = {"has_bass": list(bass), "has_music": list(music)}
    if kick is not None:
        t["content"]["has_kick"] = list(kick)
    return t


def _cost(out, ins, a, s, e):
    return pb._score(out, ins, a, s, e, 0.0, 0.0, pb.phrase_points(out), pb.phrase_points(ins))


def _prepared(idx):
    b = KEY["blends"][idx]
    o = sb.prepare(KEY["tracks"][b["out"]], b["out"], "BOTTOM END", CONTENT)
    n = sb.prepare(KEY["tracks"][b["in"]], b["in"], "BOTTOM END", CONTENT)
    return b, o, n


# ---- fix 1: a long incoming fill bounds the exit ------------------------------------------------
def test_fix1_the_exit_may_not_run_past_the_next_long_fill_after_the_swap():
    # a codex-r2-shaped pair: swap at incoming 16, a 4 bar fill at 20-24, the next section at 56.
    # The old planner looped the outgoing through the fill to 56; the fill now bounds the exit of
    # every swap that sits at/before the fill.
    out = trk(128, [(0, "INTRO"), (102, "OUTRO")], fills=[(101, 102)], bpm=124.0)
    ins = trk(128, [(0, "INTRO"), (16, "DROP"), (56, "DROP")], fills=[(15, 16), (20, 24)])
    cs = pb.candidates(out, ins)
    assert cs
    for c in cs:
        if c.swap_incoming_bar <= 20.0 + 1e-9:      # a swap at or before the long fill start
            t_exit = c.out_end_bar - c.in_starts_at_out_bar - c.in_intro_looped_bars + c.in_intro_cut_bars
            assert t_exit <= 24.0 + 1e-9, c.key()
    exits = {c.out_end_bar - c.in_starts_at_out_bar - c.in_intro_looped_bars + c.in_intro_cut_bars
             for c in cs if c.swap_incoming_bar <= 20.0 + 1e-9}
    assert 20.0 in exits                          # exiting at the fill start (the fade point) is offered


def test_fix1_a_one_bar_fill_does_not_bound_the_exit():
    out = trk(128, [(0, "INTRO"), (102, "OUTRO")], fills=[(101, 102)], bpm=124.0)
    ins = trk(128, [(0, "INTRO"), (16, "DROP"), (40, "DROP")], fills=[(30, 31)])
    cs = pb.candidates(out, ins)
    exits = {c.out_end_bar - c.in_starts_at_out_bar - c.in_intro_looped_bars + c.in_intro_cut_bars
             for c in cs}
    assert 40.0 in exits


# ---- fix 2: loops are valued, not taxed ---------------------------------------------------------
def test_fix2_a_loop_up_to_the_called_for_exit_is_free():
    # the owner's blend 1: a 6 bar outro loop that carries the outgoing to the incoming's next
    # fill start (31).  That loop exists only to make the blend longer: it must cost nothing.
    b, o, n = _prepared(0)
    own = _cost(o, n, 184, 200, 215)
    assert own.out_looped_bars == 6
    assert own.terms.get("out_loop", 0.0) == 0.0


def test_fix2_loop_beyond_the_called_for_exit_still_costs():
    b, o, n = _prepared(0)
    far = _cost(o, n, 176, 192, 224)           # loops to the farthest section, past the next fill start
    assert far.terms.get("out_loop", 0.0) > 10.0


# ---- fix 3: the intro side too ------------------------------------------------------------------
def test_fix3_the_entry_is_extended_or_cut_to_meet_an_outgoing_cue_point():
    # the pairing (swap 184 with incoming bar 13) derives entry 171, which is no outgoing cue
    # point (the outgoing grid runs 16+8m with 168 and 184 marked; 171 is on none of them).  The
    # intro may be looped back so the entry meets 168, or cut so it meets 176; both are free.
    out = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(167, 168), (183, 184)])
    ins = trk(120, [(0, "INTRO"), (13, "DROP"), (48, "BREAK")], fills=[(12, 13)])
    cs = pb.candidates(out, ins)
    ext = [c for c in cs if c.in_starts_at_out_bar == 168.0 and c.bass_switch_out_bar == 184.0
           and c.in_intro_looped_bars == 3.0]
    cut = [c for c in cs if c.in_starts_at_out_bar == 176.0 and c.bass_switch_out_bar == 184.0
           and c.in_intro_cut_bars == 5.0]
    assert ext and "entry" not in ext[0].terms
    assert cut and "entry" not in cut[0].terms
    assert any("cue point" in w for w in ext[0].why)
    # the unadjusted pairing (entry 171, off every cue) pays for its entry
    raw = [c for c in cs if c.in_starts_at_out_bar == 171.0 and c.bass_switch_out_bar == 184.0
           and c.in_intro_looped_bars == 0.0 and c.in_intro_cut_bars == 0.0]
    assert raw and raw[0].terms.get("entry", 0.0) > 1.0


def test_fix3_a_cut_never_goes_through_the_fill_before_the_swap():
    out = trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(167, 168), (183, 184)])
    ins = trk(120, [(0, "INTRO"), (13, "DROP"), (48, "BREAK")], fills=[(12, 13)])
    for c in pb.candidates(out, ins):
        if c.in_intro_cut_bars > 0:
            fills_ending_at_swap = [f for f in ins["fills"] if f[1] == c.swap_incoming_bar]
            for f in fills_ending_at_swap:
                assert c.in_intro_cut_bars <= f[0] + 1e-9


# ---- fix 4: no cliffs ----------------------------------------------------------------------------
def test_fix4_the_window_and_length_cliffs_are_gone():
    assert not hasattr(pb, "PAIR_WINDOW_SECONDS")
    assert not hasattr(pb, "SANE_BLEND_SECONDS")
    out = with_content(trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(167, 168), (183, 184)]),
                       [1] * 200, [0] * 200, [1] * 200)
    ins = with_content(trk(120, [(0, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)]),
                       [1] * 120, [0] * 120, [1] * 120)
    far = _cost(out, ins, 152, 168, 200)       # the swap is 60 s from the end: outside the old 20 s window
    assert far.paired and far.sane
    long_blend = _cost(out, ins, 144, 160, 200)  # a 105 s blend: over the old 80 s cliff
    assert long_blend.paired and long_blend.sane


def test_fix4_the_owners_46_second_swap_stays_a_sane_pairing():
    b, o, n = _prepared(6)
    own2 = _cost(o, n, 115, 131, 147)          # his also_acceptable: about 46 s of switch time
    assert own2.paired and own2.sane


# ---- fix 5: END_SLACK_BARS is gone ---------------------------------------------------------------
def test_fix5_no_end_slack_constant_a_partial_final_bar_ends_naturally():
    assert not hasattr(pb, "END_SLACK_BARS")
    # PAX-like: the last counted bar has no kick (the bar count includes a partial bar), so a stop
    # at bar 192 is the natural end, not a cut.  (PAX has 193 bars against a target of 192.)
    pax = with_content(trk(193, [(0, "INTRO"), (16, "DROP"), (176, "OUTRO")], fills=[(175, 176)]),
                       [0] * 193, [0] * 193, [1] * 192 + [0])
    ins = trk(120, [(0, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)])
    c = _cost(pax, ins, 160, 176, 192)
    assert c.out_cut_bars == 0 and c.out_end_bar == 193
    # a track whose last bar really has its kick gets no such mercy: one bar short is a trim
    full = with_content(trk(193, [(0, "INTRO"), (16, "DROP")], fills=[(175, 176), (183, 184)]),
                        [1] * 193, [0] * 193, [1] * 193)
    c2 = _cost(full, ins, 160, 176, 192)
    assert c2.out_end_bar == 192 and c2.out_trim_bars + c2.out_cut_bars >= 1


# ---- fix 6: two clocks ---------------------------------------------------------------------------
def test_fix6_the_two_clocks_are_documented():
    assert "original phrasing" in pb.__doc__ and "actual overlap" in pb.__doc__


def test_fix6_the_original_clocks_ignore_loops_the_heard_length_counts_them():
    b, o, n = _prepared(0)
    looped = _cost(o, n, 184, 200, 215)        # the owner's: 6 loop bars past the original end
    nat = _cost(o, n, 184, 200, 209)           # same entry and swap, no loop
    assert looped.terms["switch_time"] == nat.terms["switch_time"]   # original phrasing: the swap
    assert looped.terms["length"] < nat.terms["length"]              # clock ignores the loop;
    assert (looped.out_end_bar - looped.in_starts_at_out_bar
            > nat.out_end_bar - nat.in_starts_at_out_bar)            # the heard blend IS longer


# ---- fix 7: a soft minimum overlap ----------------------------------------------------------------
def test_fix7_a_very_short_blend_costs():
    out = with_content(trk(200, [(0, "INTRO"), (16, "DROP")], fills=[(183, 184)]),
                       [1] * 200, [0] * 200, [1] * 200)
    ins = with_content(trk(120, [(0, "INTRO"), (16, "DROP"), (32, "BREAK")], fills=[(31, 32)]),
                       [1] * 120, [0] * 120, [1] * 120)
    short = _cost(out, ins, 174, 184, 188)     # 14 bars heard, about 26 s
    long_ = _cost(out, ins, 168, 184, 200)     # 32 bars heard, about a minute
    assert short.terms.get("short_blend", 0.0) > 0
    assert long_.terms.get("short_blend", 0.0) == 0.0


# ---- fix 8: implied grid --------------------------------------------------------------------------
def test_fix8_no_implied_point_before_the_first_section():
    t = trk(100, [(12, "DROP")])               # the first section does not start at bar 0
    ip = pb.implied_points(t)
    assert ip and min(ip) >= 12
    t0 = trk(100, [(0, "INTRO"), (12, "DROP")])
    assert min(pb.implied_points(t0)) == 8.0   # a confirmed section at bar 0 anchors the grid as before


def test_fix8_a_measured_bass_in_within_a_bar_of_a_section_start_is_that_start():
    bass = [0] * 15 + [1] * 25 + [0] * 10
    t = with_content(trk(50, [(0, "INTRO"), (16, "DROP")]), bass, [0] * 50, [1] * 50)
    assert pb.bass_in(t) == 16.0               # measured at 15, one bar before the section: that start


# ---- fix 9: robustness ------------------------------------------------------------------------------
def test_fix9_a_single_number_fill_does_not_crash():
    out = trk(100, [(0, "INTRO"), (16, "DROP")], fills=[(183,)])
    ins = trk(80, [(0, "INTRO"), (16, "DROP")])
    plan = pb.plan_blend(out, ins)
    assert plan is None or plan.key()


def test_fix9_a_non_numeric_section_start_does_not_crash():
    out = trk(100, [(0, "INTRO"), ("x", "DROP"), (16, "DROP")], fills=[(83, 84)])
    ins = trk(80, [(0, "INTRO"), (16, "DROP")])
    plan = pb.plan_blend(out, ins)
    assert plan is not None


def test_fix9_data_files_are_located_relative_to_the_repo_with_a_glob_fallback():
    scratch = ROOT / "_scratch" / "paths_test" / "Documentation" / "Anywhere"
    scratch.mkdir(parents=True, exist_ok=True)
    alt = scratch / "Answer Key RENAMED.json"
    alt.write_text(sb.KEY.read_text(encoding="utf-8"), encoding="utf-8")
    try:
        found = sb.find_data_file(scratch.parents[1], Path("Documentation") / "Golden Sections"
                                  / "Answer Key 2026-11-20 DFTD.json", "Answer Key*.json")
        assert found == alt
    finally:
        alt.unlink()


def test_fix9_the_scorer_runs_from_any_working_directory():
    scratch = ROOT / "_scratch"
    scratch.mkdir(exist_ok=True)
    r = subprocess.run([sys.executable, "-I", str(ROOT / "Tools" / "score_blends.py")],
                       cwd=str(scratch), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "TOTAL BOTTOM END" in r.stdout


def test_fix9_the_scorer_and_planner_agree_about_incoming_loops():
    # a plan equal to the owner's answer plus a transparent intro loop (inside the intro, done
    # with before the swap) is the same answer, not a different species
    b, o, n = _prepared(0)
    sam = sb.sam_numbers(b, o)
    looped = pb.BlendPlan(184.0, 200.0, 215.0, 6.0, 8.0, in_loop_phrase_end=16.0)
    assert sb.matches(looped, b, sam) == "primary"


# ---- the success criterion ------------------------------------------------------------------------
def test_the_seven_owner_blends_all_match_under_bottom_end():
    exact, rows = sb.score("BOTTOM END", out=lambda *_: None)
    assert exact == 7
    assert all(r["which"] == "primary" for r in rows)
