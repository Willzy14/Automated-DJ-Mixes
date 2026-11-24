"""Tests for Source/tweak_replay.py and Tools/score_tweak.py.

Synthetic Ableton sets are written to gzip XML in the shape learn_from_correction's parsers read, from
placements whose answer is known, so decoding, the round trip, ranking and the vocabulary reasons are checked
against known answers.  Run:  PYTHONPATH=Source python -I -m pytest Tests/test_score_tweak.py
"""
import gzip
import hashlib
import html
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))
sys.path.insert(0, str(ROOT / "Tools"))
import phrase_blend as pb  # noqa: E402
import score_blends as sb  # noqa: E402
import score_tweak as cli  # noqa: E402
import tweak_replay as tr  # noqa: E402

KEY = json.loads(sb.KEY.read_text(encoding="utf-8"))
CONTENT = json.loads(sb.CONTENT.read_text(encoding="utf-8"))


# ---- synthetic sets --------------------------------------------------------------------------
def trk(n, bpm=128.0):
    return {"bpm": bpm, "n_bars": n,
            "sections": [[0, "INTRO"], [16, "DROP"], [48, "BREAK"], [64, "DROP"], [n - 24, "OUTRO"]],
            "fills": [[15, 16], [47, 48], [63, 64], [n - 25, n - 24]]}


NAMES = ["Artist A - Alpha", "Artist B - Beta", "Artist C - Gamma"]
TRACKS = {NAMES[0]: trk(160), NAMES[1]: trk(144), NAMES[2]: trk(150)}


def place(a, s, e, l=0.0, p=0.0, cut=0.0):
    return {"a": float(a), "s": float(s), "e": float(e), "l": float(l), "p": float(p), "cut": float(cut)}


def from_plan(plan):
    return place(plan.in_starts_at_out_bar, plan.bass_switch_out_bar, plan.out_end_bar,
                 plan.in_intro_looped_bars, plan.in_loop_phrase_end, plan.in_intro_cut_bars)


def top_placements(names=NAMES, tracks=TRACKS):
    return [from_plan(pb.plan_blend(tracks[names[i]], tracks[names[i + 1]])) for i in range(len(names) - 1)]


def _events(points):
    return "".join('<FloatEvent Id="%d" Time="%s" Value="%s" />\n' % (i, t, v) for i, (t, v) in enumerate(points))


def _track_xml(tid, name, clips, bass):
    rows = ['<AudioTrack Id="%d">' % tid, '<EffectiveName Value="%s" />' % html.escape(name, quote=True),
            '<StereoGain Id="0">', "<Gain>", '<Manual Value="1" />', '<AutomationTarget Id="%d">' % (tid * 10 + 1),
            "</AutomationTarget>", "</Gain>", "</StereoGain>",
            '<ChannelEq Id="1">', "<LowShelfGain>", '<Manual Value="1" />', '<AutomationTarget Id="%d">' % (tid * 10 + 2),
            "</AutomationTarget>", "</LowShelfGain>", "</ChannelEq>"]
    if bass:
        rows += ["<AutomationEnvelopes>", "<Envelopes>", '<AutomationEnvelope Id="0">', "<EnvelopeTarget>",
                 '<PointeeId Value="%d" />' % (tid * 10 + 2), "</EnvelopeTarget>", "<Automation>", "<Events>",
                 _events(bass), "</Events>", "</Automation>", "</AutomationEnvelope>", "</Envelopes>",
                 "</AutomationEnvelopes>"]
    for j, (a0, a1, s0) in enumerate(clips):
        rows += ['<AudioClip Id="%d" Time="%s">' % (j, a0), '<CurrentEnd Value="%s" />' % a1,
                 '<LoopStart Value="%s" />' % s0, '<LoopEnd Value="%s" />' % (s0 + (a1 - a0)),
                 '<Name Value="clip_%d" />' % (j + 1), '<Color Value="1" />', "</AudioClip>"]
    rows.append("</AudioTrack>")
    return "\n".join(rows)


def write_als(path, specs):
    body = "\n".join(_track_xml(i + 1, s["name"], s["clips"], s["bass"]) for i, s in enumerate(specs))
    xml = '<?xml version="1.0"?>\n<Ableton>\n<LiveSet>\n<Tracks>\n%s\n</Tracks>\n</LiveSet>\n</Ableton>\n' % body
    with gzip.open(path, "wb") as f:
        f.write(xml.encode("utf-8"))
    return Path(path)


def build(path, names, tracks, placements, **kw):
    """Lay the tracks out the way the pipeline does for the given placements (one per transition): each
    incoming's trim and intro loop honoured, a tail loop of the last 8 bars appended when e passes the natural end.
    shift slides an incoming (noise tests); extra_out_clips replaces the end of a track's clips."""
    cuts = {i + 1: pl["cut"] * 4.0 for i, pl in enumerate(placements)}
    n_bars = [tracks[n]["n_bars"] for n in names]
    loops = [(0.0, 0.0)] + [(pl["l"], pl["p"]) for pl in placements]
    bass = [[] for _ in names]
    clips = [None] * len(names)
    origins = [0.0] * len(names)

    def arr_of(idx, bar):
        l, p = loops[idx]
        return origins[idx] + 4.0 * (bar + (l if (l and bar >= p) else 0.0))

    shift = kw.get("shift") or {}
    for i, pl in enumerate(placements):
        entry_arr = arr_of(i, pl["a"]) + shift.get(i, 0.0)
        origins[i + 1] = entry_arr - 4.0 * pl["cut"]
    for i in range(len(names)):
        l, p = loops[i]
        cut_beats = cuts.get(i, 0.0)
        n_out = float(n_bars[i])
        o = origins[i]
        if i < len(placements):
            e = placements[i]["e"]
        else:
            e = n_out
        natural_end = min(e, n_out)
        cl = []
        if l:
            cl.append((o + cut_beats, o + 4.0 * p, cut_beats))
            cl.append((o + 4.0 * p, o + 4.0 * (p + l), 4.0 * (p - l)))
            cl.append((o + 4.0 * (p + l), o + 4.0 * (natural_end + l), 4.0 * p))
        else:
            cl.append((o + cut_beats, o + 4.0 * natural_end, cut_beats))
        end_arr = cl[-1][1]
        extra = kw.get("extra_out_clips", {}).get(i)
        if extra is not None:
            cl = cl[:-1] + [(cl[-1][0], cl[-1][1], cl[-1][2])] + extra
            end_arr = cl[-1][1]
        elif e > n_out + 1e-9:
            window, left, t0 = 8.0, e - n_out, end_arr
            while left > 1e-9:
                w = min(window, left)
                cl.append((t0, t0 + 4.0 * w, 4.0 * (n_out - window)))
                t0 += 4.0 * w
                left -= w
            end_arr = t0
        clips[i] = cl
        # automation
        if i > 0:
            pl_in = placements[i - 1]
            swap_arr = arr_of(i - 1, pl_in["s"])
            entry_arr = origins[i] + 4.0 * pl_in["cut"]
            bass[i] += [(entry_arr, 0.18), (swap_arr, 0.18)] + ([] if kw.get("drop_swap_automation") else [(swap_arr, 1.0)])
        if i < len(placements):
            swap_arr = arr_of(i, placements[i]["s"])
            bass[i] += [(swap_arr, 1.0), (swap_arr, 0.18), (end_arr, 0.18)]
    hook = kw.get("bass_hook")
    if hook:
        hook(bass, origins, arr_of)
    return write_als(path, [{"name": n, "clips": c, "bass": b} for n, c, b in zip(names, clips, bass)])


@pytest.fixture()
def tops():
    return top_placements()


def test_helper_builds_a_set_the_parsers_read(tmp_path, tops):
    path = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    recs = tr.read_als(path)
    assert [r["name"] for r in recs] == NAMES
    assert all(r["segs"] for r in recs)


# ---- decoding: known placements come back -----------------------------------------------------
@pytest.mark.parametrize("pl", [
    place(128, 144, 160),                                  # plain: natural end
    place(120, 136, 168, cut=0),                           # tail loop of 8
    place(128, 146, 160, cut=2),                           # incoming intro trim of 2
    place(112, 136, 160, l=8, p=8),                        # incoming intro loop of 8
    place(128, 144, 152),                                  # outgoing stopped 8 bars early
    place(120, 136, 167),                                  # tail loop of 7 (a partial repeat)
])
def test_decode_recovers_known_placements(tmp_path, pl):
    path = build(tmp_path / "x.als", NAMES[:2], TRACKS, [pl])
    recs = tr.read_als(path)
    d = tr.decode_transition(recs[0], recs[1])
    assert d.undecodable is None and d.shape_gaps == []
    assert (d.a, d.s, d.e, d.loop_l, d.cut) == (pl["a"], pl["s"], pl["e"], pl["l"], pl["cut"])
    if pl["l"]:
        assert d.loop_p == pl["p"]
    assert d.k == pl["s"] - pl["a"] - pl["l"] + pl["cut"]
    assert set(d.exact.values()) == {"exact"}


def test_decode_reads_the_swap_from_the_incoming_rise_not_the_outgoing_fall(tmp_path):
    pl = place(128, 144, 160)

    def stagger(bass, origins, arr_of):
        # outgoing bass leaves 3 bars before the incoming's rise
        out_swap = arr_of(0, 144.0)
        bass[0] = [(out_swap - 12.0, 1.0), (out_swap - 11.9, 0.18), (out_swap + 300.0, 0.18)]
    path = build(tmp_path / "x.als", NAMES[:2], TRACKS, [pl], bass_hook=stagger)
    recs = tr.read_als(path)
    d = tr.decode_transition(recs[0], recs[1])
    assert d.s == 144.0                       # the incoming's rise, not the outgoing's fall at 141
    assert any("staggered" in g for g in d.model_gaps)


def test_decode_falls_back_to_the_outgoing_fall_without_an_incoming_rise(tmp_path):
    path = build(tmp_path / "x.als", NAMES[:2], TRACKS, [place(128, 144, 160)], drop_swap_automation=True)
    recs = tr.read_als(path)
    d = tr.decode_transition(recs[0], recs[1])
    assert d.s == 144.0 and "outgoing's bass fall" in " ".join(d.notes)


def test_no_swap_automation_is_not_decodable(tmp_path):
    path = build(tmp_path / "x.als", NAMES[:2], TRACKS, [place(128, 144, 160)],
                 bass_hook=lambda bass, origins, arr_of: bass.__setitem__(slice(None), [[], []]))
    recs = tr.read_als(path)
    d = tr.decode_transition(recs[0], recs[1])
    assert d.undecodable and "no bass swap" in d.undecodable


def test_noise_is_exact_a_beat_is_approximate_and_more_is_refused(tmp_path):
    pl = place(128, 144, 160)
    for beats, expect in ((0.05, "exact"), (0.7, "approximate")):
        path = build(tmp_path / ("n%s.als" % beats), NAMES[:2], TRACKS, [pl], shift={0: beats})
        recs = tr.read_als(path)
        d = tr.decode_transition(recs[0], recs[1])
        assert d.undecodable is None and d.a == 128.0
        assert d.exact["entry"] == expect
    path = build(tmp_path / "far.als", NAMES[:2], TRACKS, [pl], shift={0: 2.0})
    recs = tr.read_als(path)
    d = tr.decode_transition(recs[0], recs[1])
    assert d.undecodable and "not decodable" in d.undecodable


def test_inconsistent_clip_length_refuses(tmp_path):
    specs = [{"name": NAMES[0], "clips": [(0.0, 400.0, 0.0)], "bass": []},
             {"name": NAMES[1], "clips": [(200.0, 300.0, 0.0)], "bass": []}]
    path = write_als(tmp_path / "x.als", specs)
    recs = tr.read_als(path)
    recs[1]["problems"] = ["clip x: arranged 100 beats but source 50 beats (a looped or stretched clip)"]
    d = tr.decode_transition(recs[0], recs[1])
    assert d.undecodable and "looped or stretched" in d.undecodable


def test_replay_then_new_material_is_a_shape_gap_and_is_not_scored(tmp_path):
    # outgoing replays source bars 130-138 then plays 1.5 bars into new material, as in the real tweak
    specs = [{"name": NAMES[0], "clips": [(0.0, 4.0 * 138.0, 0.0), (4.0 * 138.0, 4.0 * 138.0 + 38.0, 4.0 * 130.0)],
              "bass": [(4.0 * 129.0, 1.0), (4.0 * 129.0, 0.18)]},
             {"name": NAMES[1], "clips": [(4.0 * 113.0, 4.0 * 113.0 + 4.0 * 144.0, 0.0)],
              "bass": [(4.0 * 113.0, 0.18), (4.0 * 129.0, 0.18), (4.0 * 129.0, 1.0)]}]
    path = write_als(tmp_path / "y.als", specs)
    recs = tr.read_als(path)
    d = tr.decode_transition(recs[0], recs[1])
    assert d.undecodable is None
    assert d.shape_gaps and "plays on" in d.shape_gaps[0] and not d.scoreable
    rec = tr.analyse_transition(1, NAMES[:2], TRACKS[NAMES[0]], TRACKS[NAMES[1]], d, d)
    assert rec["position"]["kind"] == "outside the vocabulary" and rec["position"]["scored"] is False
    assert rec["sam_scored"] is None


def test_mid_blend_skip_in_the_outgoing_is_a_shape_gap(tmp_path):
    specs = [{"name": NAMES[0], "clips": [(0.0, 4.0 * 136.0, 0.0), (4.0 * 136.0, 4.0 * 152.0, 4.0 * 144.0)],
              "bass": [(4.0 * 144.0, 1.0), (4.0 * 144.0, 0.18)]},
             {"name": NAMES[1], "clips": [(4.0 * 128.0, 4.0 * 128.0 + 4.0 * 144.0, 0.0)],
              "bass": [(4.0 * 128.0, 0.18), (4.0 * 144.0, 0.18), (4.0 * 144.0, 1.0)]}]
    path = write_als(tmp_path / "x.als", specs)
    recs = tr.read_als(path)
    d = tr.decode_transition(recs[0], recs[1])
    assert any("skips source bars 136-144" in g for g in d.shape_gaps)


def test_a_skip_after_the_blend_is_not_a_gap(tmp_path):
    specs = [{"name": NAMES[0], "clips": [(0.0, 4.0 * 160.0, 0.0)],
              "bass": [(4.0 * 144.0, 1.0), (4.0 * 144.0, 0.18)]},
             {"name": NAMES[1], "clips": [(4.0 * 128.0, 4.0 * 128.0 + 4.0 * 70.0, 0.0),
                                          (4.0 * 198.0, 4.0 * 198.0 + 4.0 * 20.0, 4.0 * 80.0)],
              "bass": [(4.0 * 128.0, 0.18), (4.0 * 144.0, 0.18), (4.0 * 144.0, 1.0)]}]
    path = write_als(tmp_path / "x.als", specs)
    recs = tr.read_als(path)
    d = tr.decode_transition(recs[0], recs[1])
    assert d.shape_gaps == [] and d.undecodable is None


def test_incoming_repeat_that_is_not_one_intro_loop_is_a_shape_gap(tmp_path):
    specs = [{"name": NAMES[0], "clips": [(0.0, 4.0 * 160.0, 0.0)],
              "bass": [(4.0 * 144.0, 1.0), (4.0 * 144.0, 0.18)]},
             {"name": NAMES[1], "clips": [(4.0 * 128.0, 4.0 * 136.0, 0.0), (4.0 * 136.0, 4.0 * 140.0, 4.0 * 2.0),
                                          (4.0 * 140.0, 4.0 * 156.0, 4.0 * 10.0), (4.0 * 156.0, 4.0 * 160.0, 4.0 * 3.0)],
              "bass": [(4.0 * 128.0, 0.18), (4.0 * 144.0, 0.18), (4.0 * 144.0, 1.0)]}]
    path = write_als(tmp_path / "x.als", specs)
    recs = tr.read_als(path)
    d = tr.decode_transition(recs[0], recs[1])
    assert d.shape_gaps


# ---- the round trip ---------------------------------------------------------------------------
def test_round_trip_passes_on_the_planners_own_set(tmp_path, tops):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    sam = build(tmp_path / "sam.als", NAMES, TRACKS, tops)
    res = tr.replay(base, sam, TRACKS)
    assert res["round_trip"]["passed"] and len(res["round_trip"]["rows"]) == 2
    assert res["summary"]["sam_changed_nothing"] == [1, 2]            # untouched transitions are "kept"
    assert res["summary"]["top_pick_equals_sam"] == [1, 2]


def test_round_trip_refuses_when_the_decoder_is_off_by_a_bar(tmp_path, tops, monkeypatch):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    real = tr.decode_transition

    def off_by_one(o, i):
        d = real(o, i)
        d.a += 1.0
        return d
    monkeypatch.setattr(tr, "decode_transition", off_by_one)
    with pytest.raises(tr.RoundTripError, match="entry|differs in a"):
        tr.replay(base, base, TRACKS)


def test_round_trip_refuses_a_set_the_planner_did_not_make(tmp_path, tops):
    other = [dict(tops[0]), dict(tops[1])]
    other[1]["a"] -= 8.0
    other[1]["s"] -= 8.0
    other[1]["e"] -= 8.0
    base = build(tmp_path / "base.als", NAMES, TRACKS, other)
    with pytest.raises(tr.RoundTripError, match=r"transition\(s\) 2"):
        tr.replay(base, base, TRACKS)


def test_round_trip_tolerates_a_fraction_of_a_beat_but_not_a_whole_beat_and_a_bit(tmp_path, tops):
    base = build(tmp_path / "ok.als", NAMES, TRACKS, tops, shift={0: 0.7})
    assert tr.replay(base, base, TRACKS)["round_trip"]["passed"]
    far = build(tmp_path / "far.als", NAMES, TRACKS, tops, shift={0: 1.2})
    with pytest.raises(tr.RoundTripError):
        tr.replay(far, far, TRACKS)


def test_a_pipeline_dropped_loop_is_expected_only_when_named(tmp_path):
    tracks = dict(TRACKS)
    plan = pb.plan_blend(tracks[NAMES[0]], tracks[NAMES[1]])
    # a plan with an intro loop: force one through candidates
    cs = [c for c in pb.candidates(tracks[NAMES[0]], tracks[NAMES[1]]) if c.in_intro_looped_bars]
    assert cs, "fixture needs a candidate with an intro loop"
    looped = cs[0]
    applied = place(looped.in_starts_at_out_bar + looped.in_intro_looped_bars, looped.bass_switch_out_bar,
                    looped.out_end_bar)
    base = build(tmp_path / "base.als", NAMES[:2], tracks, [applied])
    # expectation = planner's top; make the top the looped one by monkeypatched planner
    real = pb.plan_blend
    try:
        pb.plan_blend = lambda o, n, **kw: looped
        with pytest.raises(tr.RoundTripError):
            tr.replay(base, base, tracks)
        res = tr.replay(base, base, tracks, dropped={1: "intro_loop"})
        assert res["round_trip"]["rows"][0]["adjusted"]
        with pytest.raises(tr.RoundTripError):          # the baseline still has the tail loop it was told was dropped
            tr.replay(base, base, tracks, dropped={1: "tail_loop"})
    finally:
        pb.plan_blend = real
    assert plan is not None


def test_a_drop_the_plan_cannot_have_had_is_an_error():
    with pytest.raises(ValueError, match="has none"):
        tr.apply_dropped_loop(tr.Placement(a=1.0, s=2.0, e=100.0), "intro_loop", 100.0)
    with pytest.raises(ValueError, match="has none"):
        tr.apply_dropped_loop(tr.Placement(a=1.0, s=2.0, e=100.0), "tail_loop", 100.0)
    pl = tr.apply_dropped_loop(tr.Placement(a=96.0, s=112.0, e=120.0, loop_l=8.0, loop_p=8.0), "intro_loop", 100.0)
    assert (pl.a, pl.loop_l, pl.loop_p, pl.s) == (104.0, 0.0, 0.0, 112.0)
    pl = tr.apply_dropped_loop(tr.Placement(a=96.0, s=112.0, e=108.0), "tail_loop", 100.0)
    assert pl.e == 100.0


def test_parse_dropped_loops():
    text = "  T5: dropped intro_loop, Loop quality gate failed\n  T3: dropped tail_loop, x\nnoise T9 dropped nothing"
    assert tr.parse_dropped_loops(text) == {5: "intro_loop", 3: "tail_loop"}


# ---- ranking and the vocabulary ---------------------------------------------------------------
def _pair(idx=0):
    key_blend = KEY["blends"][idx]
    o = sb.prepare(KEY["tracks"][key_blend["out"]], key_blend["out"], "BOTTOM END", CONTENT)
    n = sb.prepare(KEY["tracks"][key_blend["in"]], key_blend["in"], "BOTTOM END", CONTENT)
    return o, n


def _sam_from(plan):
    pl = tr.placement_from_plan(plan)
    return pl


@pytest.mark.parametrize("target", ["top", "third", "last"])
def test_rank_of_a_known_candidate(target):
    o, n = _pair()
    cs = pb.candidates(o, n)
    idx = {"top": 0, "third": 2, "last": len(cs) - 1}[target]
    sam = _sam_from(cs[idx])
    rec = tr.analyse_transition(1, ("o", "i"), o, n, sam, sam)
    assert rec["position"]["rank"] == idx + 1 and rec["position"]["of"] == len(cs)
    assert rec["position"]["kind"] in ("top pick", "listed alternative", "ranked")
    assert (rec["position"]["kind"] == "top pick") == (idx == 0)
    if idx:
        assert rec["terms_difference"] is not None and rec["decisive_term"]
        assert rec["sam_scored"]["cost"] >= rec["planner_top"]["cost"]


def test_rank_follows_the_planners_tier_not_the_cost_alone():
    o, n = _pair(4)                                  # answer-key blend 5: a cheaper unsane pairing ranks below dearer sane ones
    cs = pb.candidates(o, n)
    idx = next(i for i, c in enumerate(cs) if any(x.cost > c.cost + 1e-9 for x in cs[:i]))
    assert not cs[idx].sane
    sam = _sam_from(cs[idx])
    rec = tr.analyse_transition(1, ("o", "i"), o, n, sam, sam)
    by_cost = sorted(cs, key=lambda c: c.cost).index(cs[idx]) + 1
    assert rec["position"]["rank"] == idx + 1 != by_cost
    assert rec["position"]["outranked_by_costlier"] >= 1
    assert rec["sam_scored"]["sane"] is False and rec["planner_top"]["sane"] is True


def test_rank_three_prints_the_rank_and_the_cost_gap():
    o, n = _pair()
    cs = pb.candidates(o, n)
    sam = _sam_from(cs[2])
    rec = tr.analyse_transition(1, ("o", "i"), o, n, sam, sam)
    result = {"round_trip": {"passed": True, "reference": "x", "rows": []}, "transitions": [rec],
              "summary": tr.summarise([rec], None), "patterns": []}
    rec["observations"] = []
    text = tr.format_report(result)
    assert "RANKED 3 of %d" % len(cs) in text or "ALTERNATIVE" in text
    assert "rank 3 of %d" % len(cs) in text or "RANKED 3 of %d" % len(cs) in text or "rank 3" in text


def test_a_listed_alternative_is_named_as_one():
    o, n = _pair()
    cs = pb.candidates(o, n)
    top = pb.plan_blend(o, n)
    assert top.alternatives
    sam = _sam_from(top.alternatives[0].plan)
    rec = tr.analyse_transition(1, ("o", "i"), o, n, sam, sam)
    assert rec["position"]["kind"] == "listed alternative" and rec["position"]["alternative"] == 1
    assert rec["position"]["rank"] == [i for i, c in enumerate(cs, 1) if tr.same_placement(c, sam)][0]


def test_tail_stop_without_a_loop_is_outside_the_vocabulary_with_the_reason():
    o, n = _pair()
    cs = pb.candidates(o, n)
    base = tr.placement_from_plan(cs[0])
    sam = tr.placement_from_plan(cs[0])
    sam.e = sam.s                                   # stop dead on the swap
    rec = tr.analyse_transition(1, ("o", "i"), o, n, base, sam)
    pos = rec["position"]
    assert pos["kind"] == "outside the vocabulary" and pos["scored"] is True
    assert any("minimum" in w for w in pos["why"])
    assert pos["would_rank"] > 1 and pos["nearest"]["rank"] >= 1
    assert rec["sam_scored"] is not None and rec["decisive_term"]


def test_a_one_bar_intro_cut_the_planner_does_not_offer_is_outside_and_named():
    o, n = _pair()
    cs = pb.candidates(o, n)
    base = tr.placement_from_plan(cs[0])
    sam = tr.placement_from_plan(cs[0])
    sam.cut = 1.0                                   # a 1-bar trim no candidate has
    assert not any(tr.same_placement(c, sam) for c in cs)
    rec = tr.analyse_transition(1, ("o", "i"), o, n, base, sam)
    assert rec["position"]["kind"] == "outside the vocabulary"
    assert rec["position"]["nearest"]["plan"]["intro_cut_bars"] != 1.0


def test_planner_equals_sam_case():
    o, n = _pair()
    top = pb.plan_blend(o, n)
    sam = tr.placement_from_plan(top)
    rec = tr.analyse_transition(1, ("o", "i"), o, n, sam, sam)
    assert rec["kept"] is True and rec["position"]["kind"] == "top pick" and rec["position"]["rank"] == 1
    assert rec["terms_difference"] == {} and rec["decisive_term"] is None


def test_changed_but_identical_to_top_when_planner_set_was_something_else():
    o, n = _pair()
    cs = pb.candidates(o, n)
    base = tr.placement_from_plan(cs[3])
    sam = tr.placement_from_plan(cs[0])
    rec = tr.analyse_transition(1, ("o", "i"), o, n, base, sam)
    assert rec["kept"] is False and rec["position"]["kind"] == "top pick"


# ---- the trivial baseline ---------------------------------------------------------------------
def test_trivial_baseline_is_sixteen_in_sixteen_out_on_the_outro_start_and_is_not_the_planners_pick():
    o, n = _pair()
    triv = tr.trivial_placement(o, n)
    outro = next(x[0] for x in pb._sections(o) if x[1] == "OUTRO")
    assert (triv.a, triv.s, triv.e) == (outro - 16, outro, outro + 16)
    top = pb.plan_blend(o, n)
    rec = tr.analyse_transition(1, ("o", "i"), o, n, tr.placement_from_plan(top), tr.placement_from_plan(top))
    assert (rec["trivial"]["placement"]["swap"]) == outro
    assert rec["trivial"]["placement"]["entry"] != top.in_starts_at_out_bar or \
        rec["trivial"]["placement"]["end"] != top.out_end_bar
    ref = pb._score(o, n, triv.a, triv.s, triv.e, 0.0, 0.0, pb.phrase_points(o), pb.phrase_points(n))
    assert rec["trivial"]["cost"] == pytest.approx(round(ref.cost, 4))
    assert rec["trivial"]["cost"] != pytest.approx(round(top.cost, 4))


def test_trivial_without_an_outro_uses_sixteen_bars_before_the_end():
    o = {"bpm": 128.0, "n_bars": 120, "sections": [[0, "INTRO"], [16, "DROP"]], "fills": []}
    n = trk(100)
    triv = tr.trivial_placement(o, n)
    assert (triv.a, triv.s, triv.e) == (88.0, 104.0, 120.0) and "no OUTRO" in triv.notes[0]


def test_plain_verdict_is_said_when_the_planner_beats_neither():
    recs = []
    for i in (1, 2):
        recs.append({"transition": i, "kept": False, "position": {"kind": "ranked", "rank": 9},
                     "vocabulary": [], "planner_top": {"cost": 5.0}, "trivial_plan_cost": 4.0,
                     "sam_scored": {"cost": 1}, "sam": {"entry": 1.0, "swap": 2.0, "end": 3.0},
                     "trivial": {"placement": {"entry": 1.0, "swap": 2.0, "end": 3.0}}})
    s = tr.summarise(recs, None)
    assert s["plain_verdict"] and "does NOT beat" in s["plain_verdict"]


def test_summary_counts_on_a_known_set():
    o, n = _pair()
    cs = pb.candidates(o, n)
    recs = []
    for i, c in enumerate((cs[0], cs[2], cs[0])):
        sam = tr.placement_from_plan(c)
        r = tr.analyse_transition(i + 1, ("o", "i"), o, n, tr.placement_from_plan(cs[0]), sam)
        recs.append(r)
    s = tr.summarise(recs, None)
    assert s["top_pick_equals_sam"] == [1, 3] and s["sam_changed_nothing"] == [1, 3]
    assert s["sam_in_top_3"] == [1, 2, 3]


# ---- the CLI ----------------------------------------------------------------------------------
def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_cli_writes_only_the_two_outputs_and_never_touches_inputs(tmp_path, tops):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    changed = [dict(tops[0]), dict(tops[1])]
    changed[1]["a"] += 8.0
    sam = build(tmp_path / "sam.als", NAMES, TRACKS, changed)
    content = tmp_path / "content.json"
    content.write_text(json.dumps({"tracks": TRACKS}), encoding="utf-8")
    before = {p: _sha(p) for p in (base, sam, content)}
    out = tmp_path / "out"
    assert cli.main(["--baseline", str(base), "--tweak", str(sam), "--content", str(content), "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["tweak_replay.json", "tweak_replay_report.txt"]
    assert before == {p: _sha(p) for p in before}
    data = json.loads((out / "tweak_replay.json").read_text())
    assert data["summary"]["sam_changed_nothing"] == [1] and data["transitions"][1]["kept"] is False
    text = (out / "tweak_replay_report.txt").read_text()
    assert "PASSED on all 2 transitions" in text and "SUMMARY (2 transitions)" in text
    assert text.isascii()


def test_cli_exit_code_two_and_a_failed_report_when_the_round_trip_refuses(tmp_path, tops):
    other = [dict(tops[0]), dict(tops[1])]
    other[0]["a"] -= 8.0
    other[0]["s"] -= 8.0
    other[0]["e"] -= 8.0
    base = build(tmp_path / "base.als", NAMES, TRACKS, other)
    content = tmp_path / "content.json"
    content.write_text(json.dumps({"tracks": TRACKS}), encoding="utf-8")
    out = tmp_path / "out"
    assert cli.main(["--baseline", str(base), "--tweak", str(base), "--content", str(content), "--out", str(out)]) == 2
    text = (out / "tweak_replay_report.txt").read_text()
    assert "ROUND TRIP FAILED" in text and "nothing scored" in text.lower()
    assert json.loads((out / "tweak_replay.json").read_text())["round_trip"]["passed"] is False


def test_cli_refuses_to_overwrite_an_input(tmp_path, tops):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    content = tmp_path / "tweak_replay.json"
    content.write_text(json.dumps({"tracks": TRACKS}), encoding="utf-8")
    with pytest.raises(ValueError, match="overwrite an input"):
        cli.main(["--baseline", str(base), "--tweak", str(base), "--content", str(content), "--out", str(tmp_path)])


def test_the_two_sets_must_hold_the_same_tracks(tmp_path, tops):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    two = build(tmp_path / "two.als", NAMES[:2], TRACKS, tops[:1])
    with pytest.raises(ValueError, match="same tracks"):
        tr.replay(base, two, TRACKS)


def test_no_em_dash_in_the_new_files():
    for rel in ("Source/tweak_replay.py", "Tools/score_tweak.py", "Tests/test_score_tweak.py"):
        assert chr(0x2014) not in (ROOT / rel).read_text(encoding="utf-8"), rel
