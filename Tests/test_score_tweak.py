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


def _track_xml(tid, name, clips, bass, vol=()):
    rows = ['<AudioTrack Id="%d">' % tid, '<EffectiveName Value="%s" />' % html.escape(name, quote=True),
            '<StereoGain Id="0">', "<Gain>", '<Manual Value="1" />', '<AutomationTarget Id="%d">' % (tid * 10 + 1),
            "</AutomationTarget>", "</Gain>", "</StereoGain>",
            '<ChannelEq Id="1">', "<LowShelfGain>", '<Manual Value="1" />', '<AutomationTarget Id="%d">' % (tid * 10 + 2),
            "</AutomationTarget>", "</LowShelfGain>", "</ChannelEq>"]
    envs = [(tid * 10 + 2, bass), (tid * 10 + 1, vol)]
    envs = [e for e in envs if e[1]]
    if envs:
        rows += ["<AutomationEnvelopes>", "<Envelopes>"]
        for n, (target, points) in enumerate(envs):
            rows += ['<AutomationEnvelope Id="%d">' % n, "<EnvelopeTarget>", '<PointeeId Value="%d" />' % target,
                     "</EnvelopeTarget>", "<Automation>", "<Events>", _events(points), "</Events>", "</Automation>",
                     "</AutomationEnvelope>"]
        rows += ["</Envelopes>", "</AutomationEnvelopes>"]
    for j, (a0, a1, s0) in enumerate(clips):
        rows += ['<AudioClip Id="%d" Time="%s">' % (j, a0), '<CurrentEnd Value="%s" />' % a1,
                 '<LoopStart Value="%s" />' % s0, '<LoopEnd Value="%s" />' % (s0 + (a1 - a0)),
                 '<Name Value="clip_%d" />' % (j + 1), '<Color Value="1" />', "</AudioClip>"]
    rows.append("</AudioTrack>")
    return "\n".join(rows)


def write_als(path, specs):
    body = "\n".join(_track_xml(i + 1, s["name"], s["clips"], s["bass"], s.get("vol", ())) for i, s in enumerate(specs))
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
    vols = kw.get("vols") or {}
    return write_als(path, [{"name": n, "clips": c, "bass": b, "vol": vols.get(i, ())}
                            for i, (n, c, b) in enumerate(zip(names, clips, bass))])


@pytest.fixture()
def tops():
    return top_placements()


def test_helper_builds_a_set_the_parsers_read(tmp_path, tops):
    path = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    recs = tr.read_als(path)
    assert [r["name"] for r in recs] == NAMES
    assert all(r["segs"] for r in recs)


# ---- record-level helpers (no ALS file needed) --------------------------------------------------
B = 4.0


def rec(name, segs, bass, vol=()):
    return {"name": name, "segs": [tr.Seg(*g) for g in segs], "problems": [], "bass": list(bass), "vol": list(vol)}


def out_plain(swap=160, end=176):
    return rec("O", [(0, end * B, 0, end * B)], [(swap * B, 1.0), (swap * B, 0.18), (end * B, 0.18)])


INC = rec("I", [(144 * B, 344 * B, 0, 200 * B)], [(144 * B, 0.18), (160 * B, 0.18), (160 * B, 1.0)])


def decode_path(path, n_out=None, i=0):
    recs = tr.read_als(path)
    return tr.decode_transition(recs[i], recs[i + 1], n_out)


# ---- decoding: known placements come back -----------------------------------------------------
@pytest.mark.parametrize("pl", [
    place(128, 144, 160),                                  # plain: natural end
    place(120, 136, 168, cut=0),                           # tail loop of 8
    place(128, 146, 160, cut=2),                           # incoming intro trim of 2
    place(112, 136, 160, l=8, p=8),                        # incoming intro loop of 8
    place(128, 144, 152),                                  # outgoing stopped 8 bars early
    place(120, 136, 167),                                  # tail loop of 7 (a partial repeat)
    place(120, 136, 167.5),                                # tail loop ending on a half bar
    place(128, 144, 152.5),                                # stop on a half bar (a fill start)
    place(128.5, 144.5, 160),                              # entry and swap on half bars
])
def test_decode_recovers_known_placements(tmp_path, pl):
    path = build(tmp_path / "x.als", NAMES[:2], TRACKS, [pl])
    d = decode_path(path, n_out=160.0)
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
    d = decode_path(path, 160.0)
    assert d.s == 144.0                       # the incoming's rise, not the outgoing's fall at 141
    assert any("staggered" in g for g in d.model_gaps)


def test_decode_falls_back_to_the_outgoing_fall_without_an_incoming_rise(tmp_path):
    path = build(tmp_path / "x.als", NAMES[:2], TRACKS, [place(128, 144, 160)], drop_swap_automation=True)
    d = decode_path(path, 160.0)
    assert d.s == 144.0 and "outgoing's bass fall" in " ".join(d.notes)


def test_no_swap_automation_is_not_decodable(tmp_path):
    path = build(tmp_path / "x.als", NAMES[:2], TRACKS, [place(128, 144, 160)],
                 bass_hook=lambda bass, origins, arr_of: bass.__setitem__(slice(None), [[], []]))
    d = decode_path(path, 160.0)
    assert d.undecodable and "no bass swap" in d.undecodable


def test_noise_is_exact_a_fraction_of_a_beat_is_approximate_and_a_whole_beat_is_refused(tmp_path):
    pl = place(128, 144, 160)
    for beats, expect in ((0.05, "exact"), (0.7, "approximate")):
        path = build(tmp_path / ("n%s.als" % beats), NAMES[:2], TRACKS, [pl], shift={0: beats})
        d = decode_path(path, 160.0)
        assert d.undecodable is None and d.a == 128.0
        assert d.exact["entry"] == expect
    path = build(tmp_path / "far.als", NAMES[:2], TRACKS, [pl], shift={0: 1.0})   # a quarter bar: ambiguous
    d = decode_path(path, 160.0)
    assert d.undecodable and "not decodable" in d.undecodable


def test_two_beats_off_is_a_half_bar_entry_not_a_refusal(tmp_path):
    path = build(tmp_path / "half.als", NAMES[:2], TRACKS, [place(128, 144, 160)], shift={0: 2.0})
    d = decode_path(path, 160.0)
    assert d.undecodable is None and d.a == 128.5


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
    d = decode_path(path, n_out=160.0)
    assert d.undecodable is None
    assert any("plays on" in g for g in d.shape_gaps) and not d.scoreable
    assert any("outro cut" in g for g in d.shape_gaps)         # it also stops 22 bars short of the natural end
    r = tr.analyse_transition(1, NAMES[:2], TRACKS[NAMES[0]], TRACKS[NAMES[1]], d, d)
    assert r["position"]["kind"] == tr.K_SHAPE and r["position"]["scored"] is False
    assert r["sam_scored"] is None
    assert r["position"]["nearest"]["end_compared"] is False


def test_mid_blend_skip_in_the_outgoing_is_a_shape_gap(tmp_path):
    specs = [{"name": NAMES[0], "clips": [(0.0, 4.0 * 136.0, 0.0), (4.0 * 136.0, 4.0 * 152.0, 4.0 * 144.0)],
              "bass": [(4.0 * 144.0, 1.0), (4.0 * 144.0, 0.18)]},
             {"name": NAMES[1], "clips": [(4.0 * 128.0, 4.0 * 128.0 + 4.0 * 144.0, 0.0)],
              "bass": [(4.0 * 128.0, 0.18), (4.0 * 144.0, 0.18), (4.0 * 144.0, 1.0)]}]
    path = write_als(tmp_path / "x.als", specs)
    d = decode_path(path, 160.0)
    assert any("skips source bars 136-144" in g for g in d.shape_gaps)


def test_a_skip_after_the_blend_is_not_a_gap(tmp_path):
    specs = [{"name": NAMES[0], "clips": [(0.0, 4.0 * 160.0, 0.0)],
              "bass": [(4.0 * 144.0, 1.0), (4.0 * 144.0, 0.18)]},
             {"name": NAMES[1], "clips": [(4.0 * 128.0, 4.0 * 128.0 + 4.0 * 70.0, 0.0),
                                          (4.0 * 198.0, 4.0 * 198.0 + 4.0 * 20.0, 4.0 * 80.0)],
              "bass": [(4.0 * 128.0, 0.18), (4.0 * 144.0, 0.18), (4.0 * 144.0, 1.0)]}]
    path = write_als(tmp_path / "x.als", specs)
    d = decode_path(path, 160.0)
    assert d.shape_gaps == [] and d.undecodable is None


def test_incoming_repeat_that_is_not_one_intro_loop_is_a_shape_gap(tmp_path):
    specs = [{"name": NAMES[0], "clips": [(0.0, 4.0 * 160.0, 0.0)],
              "bass": [(4.0 * 144.0, 1.0), (4.0 * 144.0, 0.18)]},
             {"name": NAMES[1], "clips": [(4.0 * 128.0, 4.0 * 136.0, 0.0), (4.0 * 136.0, 4.0 * 140.0, 4.0 * 2.0),
                                          (4.0 * 140.0, 4.0 * 156.0, 4.0 * 10.0), (4.0 * 156.0, 4.0 * 160.0, 4.0 * 3.0)],
              "bass": [(4.0 * 128.0, 0.18), (4.0 * 144.0, 0.18), (4.0 * 144.0, 1.0)]}]
    path = write_als(tmp_path / "x.als", specs)
    d = decode_path(path, 160.0)
    assert d.shape_gaps


def test_a_hole_in_the_incoming_trips_the_k_consistency_check():
    inc = rec("I", [(128 * B, 136 * B, 0, 8 * B), (138 * B, 338 * B, 8 * B, 208 * B)],
              [(128 * B, 0.18), (144 * B, 0.18), (144 * B, 1.0)])
    d = tr.decode_transition(out_plain(144, 160), inc, 160.0)
    assert any("hole" in g for g in d.shape_gaps)
    assert any("not entry, loop and trim made it" in g for g in d.shape_gaps)
    assert d.consistency["incoming bar at the swap = swap - entry - loop + trim"] is False


# ---- outro cut plus a loop (review blocker) ----------------------------------------------------
def test_an_outro_cut_followed_by_a_loop_is_not_a_natural_end_with_a_loop():
    # outgoing plays to bar 160 of 176, then loops bars 152 to 160 twice (heard end 176)
    cutloop = rec("O", [(0, 160 * B, 0, 160 * B), (160 * B, 168 * B, 152 * B, 160 * B),
                        (168 * B, 176 * B, 152 * B, 160 * B)],
                  [(152 * B, 1.0), (152 * B, 0.18), (176 * B, 0.18)])
    inc = rec("I", [(136 * B, 336 * B, 0, 200 * B)], [(136 * B, 0.18), (152 * B, 0.18), (152 * B, 1.0)])
    natural = rec("O", [(0, 176 * B, 0, 176 * B)], [(152 * B, 1.0), (152 * B, 0.18), (176 * B, 0.18)])
    d, ref = tr.decode_transition(cutloop, inc, 176.0), tr.decode_transition(natural, inc, 176.0)
    assert (d.a, d.s, d.e) == (ref.a, ref.s, ref.e) == (136.0, 152.0, 176.0)
    assert any("outro cut of 16" in g and "never loops after a cut" in g for g in d.shape_gaps)
    assert ref.shape_gaps == [] and not d.scoreable
    r = tr.analyse_transition(1, ("o", "i"), TRACKS[NAMES[0]], TRACKS[NAMES[1]], ref, d)
    assert r["kept"] is False                       # never "the planner's placement kept"


def test_a_tail_loop_needs_the_natural_end_to_be_known():
    cutloop = rec("O", [(0, 160 * B, 0, 160 * B), (160 * B, 168 * B, 152 * B, 160 * B)],
                  [(152 * B, 1.0), (152 * B, 0.18)])
    inc = rec("I", [(136 * B, 336 * B, 0, 200 * B)], [(136 * B, 0.18), (152 * B, 0.18), (152 * B, 1.0)])
    d = tr.decode_transition(cutloop, inc)
    assert d.undecodable and "natural end is unknown" in d.undecodable


# ---- bass edges (review probes) ----------------------------------------------------------------
def test_a_rise_drawn_with_a_midpoint_is_the_swap():
    inc = rec("I", [(144 * B, 344 * B, 0, 200 * B)],
              [(144 * B, 0.18), (160 * B, 0.18), (160 * B + 0.5, 0.6), (160 * B + 1, 1.0)])
    d = tr.decode_transition(out_plain(152), inc, 176.0)
    assert d.s == 160.0 and "incoming's bass rise" in " ".join(d.notes)
    assert d.alt is not None and d.alt.s == 152.0          # the outgoing's earlier fall is the other reading


def test_a_partial_rise_is_flagged_not_missed():
    inc = rec("I", [(144 * B, 344 * B, 0, 200 * B)], [(144 * B, 0.18), (160 * B, 0.18), (160 * B, 0.71)])
    d = tr.decode_transition(out_plain(152), inc, 176.0)
    assert d.s == 160.0 and any("only to 0.71" in g for g in d.model_gaps)


def test_a_two_bar_bass_preview_is_not_the_swap():
    inc = rec("I", [(144 * B, 344 * B, 0, 200 * B)],
              [(144 * B, 0.18), (148 * B, 0.18), (148 * B, 1.0), (150 * B, 1.0), (150 * B, 0.18), (160 * B, 0.18),
               (160 * B, 1.0)])
    d = tr.decode_transition(out_plain(160), inc, 176.0)
    assert d.s == 160.0 and any("preview" in n for n in d.notes)


def test_a_rise_that_creeps_from_0_997_to_1_after_reaching_full_is_still_one_step():
    inc = rec("I", [(144 * B, 344 * B, 0, 200 * B)],
              [(144 * B, 0.18), (160 * B, 0.18), (160 * B, 0.997438), (260 * B, 1.0)])
    d = tr.decode_transition(out_plain(160), inc, 176.0)
    assert d.undecodable is None and d.s == 160.0 and d.model_gaps == []


def test_an_incoming_whose_bass_is_never_killed_is_flagged():
    inc = rec("I", [(144 * B, 344 * B, 0, 200 * B)], [])
    d = tr.decode_transition(out_plain(160), inc, 176.0)
    assert d.s == 160.0 and any("no bass kill" in g for g in d.model_gaps)


def test_a_slow_bass_fade_in_is_not_decodable():
    inc = rec("I", [(144 * B, 344 * B, 0, 200 * B)], [(144 * B, 0.18), (160 * B, 1.0)])
    d = tr.decode_transition(out_plain(160), inc, 176.0)
    assert d.undecodable and "fades in over 16 bars" in d.undecodable


def test_an_outgoing_volume_fade_to_silence_is_read_into_the_end_bar():
    out = rec("O", [(0, 176 * B, 0, 176 * B)], [(160 * B, 1.0), (160 * B, 0.18), (176 * B, 0.18)],
              vol=[(160 * B, 1.0), (164 * B, 0.0)])
    d = tr.decode_transition(out, INC, 176.0)
    assert d.e == 164.0 and any("read from the fade" in n for n in d.notes)


def test_a_volume_that_only_reaches_silence_at_the_clip_end_changes_nothing():
    out = rec("O", [(0, 176 * B, 0, 176 * B)], [(160 * B, 1.0), (160 * B, 0.18), (176 * B, 0.18)],
              vol=[(160 * B, 1.0), (176 * B, 0.0)])
    assert tr.decode_transition(out, INC, 176.0).e == 176.0


def test_an_incoming_that_starts_before_its_source_bar_zero_is_not_decodable():
    inc = rec("I", [(143 * B, 343 * B, -1 * B, 199 * B)], [(143 * B, 0.18), (160 * B, 0.18), (160 * B, 1.0)])
    d = tr.decode_transition(out_plain(160), inc, 176.0)
    assert d.undecodable and "before its source bar 0" in d.undecodable


def test_a_swap_inside_the_second_pass_of_an_intro_loop_is_not_decodable():
    inc = rec("I", [(136 * B, 144 * B, 0, 8 * B), (144 * B, 344 * B, 0, 200 * B)],
              [(136 * B, 0.18), (148 * B, 0.18), (148 * B, 1.0)])
    d = tr.decode_transition(out_plain(148), inc, 176.0)
    assert d.undecodable and "second pass" in d.undecodable


def test_several_bass_edges_are_all_listed_and_the_choice_is_flagged():
    inc = rec("I", [(144 * B, 344 * B, 0, 200 * B)],
              [(144 * B, 0.18), (148 * B, 0.18), (148 * B, 1.0), (156 * B, 1.0), (156 * B, 0.18), (160 * B, 0.18),
               (160 * B, 1.0)])
    d = tr.decode_transition(out_plain(160), inc, 176.0)
    rises = [e for e in d.edges if e["kind"] == "rise"]
    assert [e["out_bar"] for e in rises] == [148.0, 160.0] and d.ambiguity and "2 incoming rises" in d.ambiguity
    assert d.s == 148.0                                          # no reference: the first of equal moves
    d2 = tr.decode_transition(out_plain(160), inc, 176.0, ref_swap_bar=160.0)
    assert d2.s == 160.0 and d2.ambiguity                       # nearest the planner's own swap


def test_a_staggered_handover_is_scored_both_ways():
    o, n = _pair()
    top = pb.plan_blend(o, n)
    base = tr.placement_from_plan(top)
    sam = tr.placement_from_plan(top)
    sam.alt = tr.placement_from_plan(top)
    sam.alt.s, sam.alt.k = sam.s - 8.0, sam.k - 8.0
    sam.alt_label = "outgoing's bass fall"
    r = tr.analyse_transition(1, ("o", "i"), o, n, base, sam)
    assert r["position_alt"] is not None and r["position_alt"]["label"] == "outgoing's bass fall"
    assert r["position_alt"]["kind"] != r["position"]["kind"] or r["position_alt"].get("rank") != r["position"].get("rank")
    text = tr.format_report(_result([r]))
    assert "OTHER READING of the swap" in text


# ---- the round trip ---------------------------------------------------------------------------
def test_round_trip_passes_on_the_planners_own_set(tmp_path, tops):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    sam = build(tmp_path / "sam.als", NAMES, TRACKS, tops)
    res = tr.replay(base, sam, TRACKS)
    assert res["round_trip"]["passed"] and len(res["round_trip"]["rows"]) == 2
    assert res["summary"]["unchanged_not_evidence"] == [1, 2] and res["summary"]["changed"] == []
    assert res["summary"]["top_pick_equals_owner_on_changed"] == []     # untouched transitions are not agreement
    assert [r["position"]["kind"] for r in res["transitions"]] == [tr.K_TOP, tr.K_TOP]


def test_round_trip_refuses_when_the_decoder_is_off_by_a_bar(tmp_path, tops, monkeypatch):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    real = tr.decode_transition

    def off_by_one(o, i, *a, **kw):
        d = real(o, i, *a, **kw)
        d.a += 1.0
        return d
    monkeypatch.setattr(tr, "decode_transition", off_by_one)
    with pytest.raises(tr.RoundTripError, match="entry"):
        tr.replay(base, base, TRACKS)


def test_round_trip_refuses_a_set_the_planner_did_not_make(tmp_path, tops):
    other = [dict(tops[0]), dict(tops[1])]
    other[1]["a"] -= 8.0
    other[1]["s"] -= 8.0
    other[1]["e"] -= 8.0
    base = build(tmp_path / "base.als", NAMES, TRACKS, other)
    with pytest.raises(tr.RoundTripError, match=r"transition\(s\) 2"):
        tr.replay(base, base, TRACKS)


def test_round_trip_tolerates_noise_but_not_a_quarter_bar(tmp_path, tops):
    base = build(tmp_path / "ok.als", NAMES, TRACKS, tops, shift={0: 0.4})
    assert tr.replay(base, base, TRACKS)["round_trip"]["passed"]
    far = build(tmp_path / "far.als", NAMES, TRACKS, tops, shift={0: 1.2})
    with pytest.raises(tr.RoundTripError):
        tr.replay(far, far, TRACKS)


@pytest.mark.parametrize("field,delta", [("a", 0.5), ("s", 0.5), ("e", 0.5), ("k", 4.0), ("k", 0.5), ("cut", 0.5),
                                         ("loop_l", 0.5)])
def test_compare_placements_flags_every_decoded_field_and_a_half_bar_is_over_tolerance(field, delta):
    x = tr.Placement(a=100.0, s=116.0, e=132.0, k=16.0, loop_l=0.0, cut=0.0)
    y = tr.Placement(a=100.0, s=116.0, e=132.0, k=16.0, loop_l=0.0, cut=0.0)
    setattr(y, field, getattr(y, field) + delta)
    assert tr.compare_placements(x, y, tr.ROUND_TRIP_BARS) == [field]


def test_compare_placements_tolerance_is_exactly_one_beat():
    x = tr.Placement(a=100.0, s=116.0, e=132.0, k=16.0)
    assert tr.compare_placements(x, tr.Placement(a=100.25, s=116.0, e=132.0, k=16.0), tr.ROUND_TRIP_BARS) == []
    assert tr.compare_placements(x, tr.Placement(a=100.3, s=116.0, e=132.0, k=16.0), tr.ROUND_TRIP_BARS) == ["a"]
    assert tr.ROUND_TRIP_BARS == 0.25


def test_round_trip_refuses_a_misread_k_even_when_everything_else_matches(tmp_path, tops, monkeypatch):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    real = tr.decode_transition

    def wrong_k(o, i, *a, **kw):
        d = real(o, i, *a, **kw)
        d.k = (d.k or 0) + 4.0
        return d
    monkeypatch.setattr(tr, "decode_transition", wrong_k)
    with pytest.raises(tr.RoundTripError, match="incoming bar at swap"):
        tr.replay(base, base, TRACKS)


def test_round_trip_refuses_any_shape_gap_in_the_baseline(tmp_path, tops, monkeypatch):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    real = tr.decode_transition

    def gapped(o, i, *a, **kw):
        d = real(o, i, *a, **kw)
        d.shape_gaps.append("a bogus gap")
        return d
    monkeypatch.setattr(tr, "decode_transition", gapped)
    with pytest.raises(tr.RoundTripError, match="shape gap"):
        tr.replay(base, base, TRACKS)


def test_round_trip_rows_say_which_fields_were_compared(tmp_path, tops):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    rows = tr.replay(base, base, TRACKS)["round_trip"]["rows"]
    assert {"entry", "swap", "end", "incoming bar at swap", "intro loop", "intro trim"} <= set(rows[0]["compared"])


def test_one_pair_with_no_planner_plan_does_not_abort_the_report(tmp_path, tops, monkeypatch):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    real = pb.plan_blend
    monkeypatch.setattr(pb, "plan_blend", lambda o, n, **kw: None if o is TRACKS[NAMES[1]] else real(o, n, **kw))
    res = tr.replay(base, base, TRACKS)
    assert [r["status"] for r in res["round_trip"]["rows"]] == ["ok", "round trip: no planner plan for this pair"]
    assert len(res["transitions"]) == 2 and res["transitions"][1]["round_trip_status"].startswith("round trip: no planner")


def test_coverage_line_is_computed_from_what_the_baseline_contains():
    plain = [tr.Placement(a=1.0, s=2.0, e=100.0, k=1.0)]
    rows = [{"status": "ok"}]
    line = tr.coverage_line(plain, rows, [100.0])
    assert line.startswith("round trip covered: entry, swap, end, incoming bar at the swap; NOT covered: ")
    for missing in ("intro trim", "intro loop", "tail loop", "outgoing stopped short of its natural end"):
        assert missing in line.split("NOT covered:")[1]
    rich = [tr.Placement(a=1.0, s=2.0, e=108.0, k=1.0, cut=2.0, loop_l=8.0, loop_p=8.0),
            tr.Placement(a=1.0, s=2.0, e=90.0, k=1.0)]
    line = tr.coverage_line(rich, [{"status": "ok"}, {"status": "ok"}], [100.0, 100.0])
    assert line.split("NOT covered:")[1].strip().startswith("nothing")
    assert "tail loop" in line.split("NOT covered:")[0] and "intro loop" in line.split("NOT covered:")[0]


def test_the_report_states_the_round_trip_coverage(tmp_path, tops):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    res = tr.replay(base, base, TRACKS)
    assert "round trip covered: entry, swap, end, incoming bar at the swap" in tr.format_report(res)
    assert "tail loop" in res["round_trip"]["coverage"].split("NOT covered")[0]    # the synthetic tops loop their tails


# ---- loops the pipeline dropped ------------------------------------------------------------------
def _loop_candidates():
    cs = pb.candidates(TRACKS[NAMES[0]], TRACKS[NAMES[1]])
    return [c for c in cs if c.in_intro_looped_bars and c.out_looped_bars][0], \
        [c for c in cs if c.in_intro_looped_bars and not c.out_looped_bars][0]


def test_a_pipeline_dropped_loop_is_expected_only_when_named(tmp_path, monkeypatch):
    _, looped = _loop_candidates()
    applied = place(looped.in_starts_at_out_bar + looped.in_intro_looped_bars, looped.bass_switch_out_bar,
                    looped.out_end_bar)
    base = build(tmp_path / "base.als", NAMES[:2], TRACKS, [applied])
    monkeypatch.setattr(pb, "plan_blend", lambda o, n, **kw: looped)
    with pytest.raises(tr.RoundTripError):
        tr.replay(base, base, TRACKS)
    res = tr.replay(base, base, TRACKS, dropped={1: ["intro_loop"]})
    assert "WEAKER check" in res["round_trip"]["rows"][0]["adjusted"]
    assert "WEAKER check" in tr.format_report(res)
    with pytest.raises(ValueError, match="has none"):
        tr.replay(base, base, TRACKS, dropped={1: ["intro_loop", "tail_loop"]})   # the plan has no tail loop


def test_both_loops_dropped_on_one_transition_are_both_expected(tmp_path, monkeypatch):
    both, _ = _loop_candidates()
    applied = place(both.in_starts_at_out_bar + both.in_intro_looped_bars, both.bass_switch_out_bar, 160)
    base = build(tmp_path / "base.als", NAMES[:2], TRACKS, [applied])
    monkeypatch.setattr(pb, "plan_blend", lambda o, n, **kw: both)
    assert tr.replay(base, base, TRACKS, dropped={1: ["intro_loop", "tail_loop"]})["round_trip"]["passed"]
    with pytest.raises(tr.RoundTripError):
        tr.replay(base, base, TRACKS, dropped={1: ["intro_loop"]})
    assert tr.parse_dropped_loops("T5: dropped intro_loop, a\nT5: dropped tail_loop, b\n") == {
        5: ["intro_loop", "tail_loop"]}


def test_the_decisions_file_may_already_hold_the_post_drop_arrangement(tmp_path, monkeypatch):
    _, looped = _loop_candidates()
    a_post = looped.in_starts_at_out_bar + looped.in_intro_looped_bars
    applied = place(a_post, looped.bass_switch_out_bar, looped.out_end_bar)
    base = build(tmp_path / "base.als", NAMES[:2], TRACKS, [applied])
    monkeypatch.setattr(pb, "plan_blend", lambda o, n, **kw: looped)
    post = {"transitions": [{"pair_index": 1, "entry_out_bar": a_post, "intro_trim_bars": 0.0,
                             "swap_in_bar": looped.swap_incoming_bar, "out_end_bar": looped.out_end_bar}]}
    pre = {"transitions": [dict(post["transitions"][0], entry_out_bar=looped.in_starts_at_out_bar,
                                intro_loop={"source_start_bar": looped.in_loop_phrase_end - looped.in_intro_looped_bars,
                                            "source_end_bar": looped.in_loop_phrase_end})]}
    for decisions in (post, pre):                    # shifted once either way, never twice
        res = tr.replay(base, base, TRACKS, decisions=decisions, dropped={1: ["intro_loop"]})
        assert res["round_trip"]["passed"]
    wrong = {"transitions": [dict(post["transitions"][0], entry_out_bar=a_post + 8.0)]}
    with pytest.raises(ValueError, match="does not match"):
        tr.replay(base, base, TRACKS, decisions=wrong, dropped={1: ["intro_loop"]})


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
    assert tr.parse_dropped_loops(text) == {5: ["intro_loop"], 3: ["tail_loop"]}


def test_the_rendered_placement_is_scored_when_a_loop_was_dropped(tmp_path, monkeypatch):
    _, looped = _loop_candidates()
    applied = place(looped.in_starts_at_out_bar + looped.in_intro_looped_bars, looped.bass_switch_out_bar,
                    looped.out_end_bar)
    base = build(tmp_path / "base.als", NAMES[:2], TRACKS, [applied])
    monkeypatch.setattr(pb, "plan_blend", lambda o, n, **kw: looped)
    res = tr.replay(base, base, TRACKS, dropped={1: ["intro_loop"]}, owner="Sam")
    r = res["transitions"][0]
    cs = pb.candidates(TRACKS[NAMES[0]], TRACKS[NAMES[1]])
    delivered = next(c for c in cs if tr.same_placement(c, tr.decode_transition(*tr.read_als(base)[:2], 160.0)))
    assert r["delivered"]["dropped"] == ["intro_loop"] and r["delivered"]["cost"] == pytest.approx(round(delivered.cost, 4))
    assert r["delivered"]["rank"] == cs.index(delivered) + 1
    assert r["planner_cost"] == pytest.approx(delivered.cost)          # the tally uses the delivered cost
    assert res["summary"]["loop_dropped_by_pipeline"] == [1]
    assert "DELIVERED (rendered) placement" in tr.format_report(res)


# ---- planner granularity: half bars and fractional end_bar --------------------------------------
def test_a_half_bar_candidate_round_trips_and_ranks(tmp_path):
    b = KEY["blends"][5]
    o, n = _pair(5)
    cs = pb.candidates(o, n)
    half = next(c for c in cs if any(abs(v - round(v)) > 1e-9 for v in (c.in_starts_at_out_bar,
                                                                         c.bass_switch_out_bar, c.out_end_bar)))
    path = build(tmp_path / "x.als", [b["out"], b["in"]], {b["out"]: o, b["in"]: n}, [from_plan(half)])
    d = decode_path(path, pb.natural_end_bar(o))
    assert d.undecodable is None and d.key()[:3] == half.key() and set(d.exact.values()) == {"exact"}
    r = tr.analyse_transition(1, (b["out"], b["in"]), o, n, d, d)
    assert r["position"]["rank"] == cs.index(half) + 1 and r["position"]["kind"] in (tr.K_ALT, tr.K_RANKED)


def test_the_planners_own_top_pick_with_a_half_bar_does_not_refuse_the_round_trip(tmp_path, monkeypatch):
    b = KEY["blends"][5]
    o, n = _pair(5)
    cs = pb.candidates(o, n)
    half = next(c for c in cs if any(abs(v - round(v)) > 1e-9 for v in (c.in_starts_at_out_bar,
                                                                         c.bass_switch_out_bar, c.out_end_bar)))
    tracks = {b["out"]: o, b["in"]: n}
    base = build(tmp_path / "x.als", [b["out"], b["in"]], tracks, [from_plan(half)])
    monkeypatch.setattr(pb, "plan_blend", lambda a, c, **kw: half)
    res = tr.replay(base, base, tracks)
    assert res["round_trip"]["passed"] and res["transitions"][0]["kept"] is True


def test_a_fractional_end_bar_is_not_reported_as_outside_the_vocabulary(tmp_path):
    b = KEY["blends"][2]
    o, n = _pair(2)
    o = dict(o, end_bar=143.8, n_bars=143.8)
    tracks = {b["out"]: o, b["in"]: n}
    top = pb.plan_blend(o, n)
    assert top.out_end_bar == 143.8
    base = build(tmp_path / "x.als", [b["out"], b["in"]], tracks, [from_plan(top)])
    res = tr.replay(base, base, tracks)
    r = res["transitions"][0]
    assert r["position"]["kind"] == tr.K_TOP and r["kept"] is True
    assert r["sam_scored"]["cost"] == pytest.approx(r["planner_top"]["cost"])
    assert "out_loop" not in r["sam_scored"]["terms"]


# ---- the planner itself (review checks of claims about phrase_blend) ----------------------------
def test_rescoring_a_candidate_through_score_placement_gives_its_own_cost():
    o, n = _pair(5)
    looped = [c for c in pb.candidates(TRACKS[NAMES[0]], TRACKS[NAMES[1]]) if c.in_intro_looped_bars]
    assert looped
    for oo, nn, cs in ((TRACKS[NAMES[0]], TRACKS[NAMES[1]], looped), (o, n, pb.candidates(o, n)[:30])):
        for c in cs:
            assert tr.score_placement(oo, nn, tr.placement_from_plan(c)).cost == pytest.approx(c.cost)


def test_the_planner_never_offers_an_intro_trim_together_with_an_intro_loop():
    for i in range(len(KEY["blends"])):
        for c in pb.candidates(*_pair(i)):
            assert not (c.in_intro_looped_bars and c.in_intro_cut_bars)
    for c in pb.candidates(TRACKS[NAMES[0]], TRACKS[NAMES[1]]):
        assert not (c.in_intro_looped_bars and c.in_intro_cut_bars)
    inc = rec("I", [(136 * B, 144 * B, 2 * B, 10 * B), (144 * B, 344 * B, 4 * B, 204 * B)],
              [(136 * B, 0.18), (148 * B, 0.18), (148 * B, 1.0)])
    d = tr.decode_transition(out_plain(148), inc, 176.0)
    assert d.loop_l and d.cut and any("intro trim and an intro loop" in g for g in d.shape_gaps)


# ---- names ---------------------------------------------------------------------------------------
def test_a_different_version_of_a_track_is_never_matched_by_prefix():
    long_name = "Artist A - A Long Track Title (Extended Mix) SW "     # the first 30 characters are shared
    with pytest.raises(ValueError, match="no planner track data"):
        tr.match_planner_names([long_name + "V3"], {long_name + "V2": TRACKS[NAMES[0]]})


def test_an_ambiguous_prefix_is_refused():
    with pytest.raises(ValueError, match="ambiguous"):
        tr.match_planner_names(["Artist A - Alpha (Extended Mix) SW V2"],
                               {"Artist A - Alpha": TRACKS[NAMES[0]], "Artist A - Alpha (Extended": TRACKS[NAMES[1]]})


def test_exact_and_escaped_names_still_match():
    got = tr.match_planner_names(["Hiisak &amp; Reeva - Killa"], {"Hiisak & Reeva - Killa": TRACKS[NAMES[0]]})
    assert got["Hiisak &amp; Reeva - Killa"] is TRACKS[NAMES[0]]


# ---- ranking and the vocabulary ---------------------------------------------------------------
def _pair(idx=0):
    key_blend = KEY["blends"][idx]
    o = sb.prepare(KEY["tracks"][key_blend["out"]], key_blend["out"], "BOTTOM END", CONTENT)
    n = sb.prepare(KEY["tracks"][key_blend["in"]], key_blend["in"], "BOTTOM END", CONTENT)
    return o, n


def _sam_from(plan):
    return tr.placement_from_plan(plan)


def _result(recs, owner="the owner"):
    for r in recs:
        r.setdefault("observations", [])
    return {"owner": owner, "round_trip": {"passed": True, "reference": "x", "rows": [], "coverage": "cov"},
            "transitions": recs, "summary": tr.summarise(recs, owner), "patterns": []}


@pytest.mark.parametrize("target", ["top", "third", "last"])
def test_rank_of_a_known_candidate(target):
    o, n = _pair()
    cs = pb.candidates(o, n)
    idx = {"top": 0, "third": 2, "last": len(cs) - 1}[target]
    sam = _sam_from(cs[idx])
    r = tr.analyse_transition(1, ("o", "i"), o, n, sam, sam)
    assert r["position"]["rank"] == idx + 1 and r["position"]["of"] == len(cs)
    assert r["position"]["kind"] in (tr.K_TOP, tr.K_ALT, tr.K_RANKED)
    assert (r["position"]["kind"] == tr.K_TOP) == (idx == 0)
    if idx:
        assert r["terms_difference"] is not None and r["decisive_term"]
        assert r["sam_scored"]["cost"] >= r["planner_top"]["cost"]


def test_rank_follows_the_planners_tier_not_the_cost_alone():
    o, n = _pair(4)                                  # answer-key blend 5: a cheaper unsane pairing ranks below dearer sane ones
    cs = pb.candidates(o, n)
    idx = next(i for i, c in enumerate(cs) if any(x.cost > c.cost + 1e-9 for x in cs[:i]))
    assert not cs[idx].sane
    sam = _sam_from(cs[idx])
    r = tr.analyse_transition(1, ("o", "i"), o, n, sam, sam)
    by_cost = sorted(cs, key=lambda c: c.cost).index(cs[idx]) + 1
    assert r["position"]["rank"] == idx + 1 != by_cost
    assert r["position"]["outranked_by_costlier"] >= 1
    assert r["sam_scored"]["sane"] is False and r["planner_top"]["sane"] is True
    text = tr.format_report(_result([r]))
    assert "a flat run of bottom end or a cut outro also makes a placement unsane" in text


def test_the_rank_is_printed_as_a_rank_over_the_planners_candidates_with_the_tie_rule():
    o, n = _pair()
    cs = pb.candidates(o, n)
    sam = _sam_from(cs[2])
    r = tr.analyse_transition(1, ("o", "i"), o, n, sam, sam)
    text = tr.format_report(_result([r]))
    assert "rank 3 of %d planner candidates for this pair (not a rank over all possible placements)" % len(cs) in text
    assert "ties and ordering: candidates are ordered by sane-pairing tier" in text


def test_a_listed_alternative_is_named_as_one():
    o, n = _pair()
    cs = pb.candidates(o, n)
    top = pb.plan_blend(o, n)
    assert top.alternatives
    sam = _sam_from(top.alternatives[0].plan)
    r = tr.analyse_transition(1, ("o", "i"), o, n, sam, sam)
    assert r["position"]["kind"] == tr.K_ALT and r["position"]["alternative"] == 1
    assert r["position"]["rank"] == [i for i, c in enumerate(cs, 1) if tr.same_placement(c, sam)][0]


def test_tail_stop_without_a_loop_is_scored_but_not_a_candidate_and_is_not_ranked():
    o, n = _pair()
    cs = pb.candidates(o, n)
    base = tr.placement_from_plan(cs[0])
    sam = tr.placement_from_plan(cs[0])
    sam.e = sam.s                                   # stop dead on the swap
    r = tr.analyse_transition(1, ("o", "i"), o, n, base, sam)
    pos = r["position"]
    assert pos["kind"] == tr.K_OUT and pos["scored"] is True and pos["rank"] is None and "would_rank" not in pos
    assert any("minimum" in w for w in pos["why"])
    assert pos["nearest"]["rank"] >= 1 and pos["nearest"]["end_compared"] is True
    assert r["sam_scored"] is not None and r["decisive_term"]
    text = tr.format_report(_result([r]))
    assert "not ranked" in text
    assert "likely reason (hand-written check, not the planner's verdict)" in text


def test_a_one_bar_intro_cut_the_planner_does_not_offer_is_scored_but_not_a_candidate():
    o, n = _pair()
    cs = pb.candidates(o, n)
    base = tr.placement_from_plan(cs[0])
    sam = tr.placement_from_plan(cs[0])
    sam.cut = 1.0                                   # a 1-bar trim no candidate has
    assert not any(tr.same_placement(c, sam) for c in cs)
    r = tr.analyse_transition(1, ("o", "i"), o, n, base, sam)
    assert r["position"]["kind"] == tr.K_OUT
    assert r["position"]["nearest"]["plan"]["intro_cut_bars"] != 1.0


def test_the_three_kinds_of_outside_are_told_apart_in_text_json_and_the_summary(tmp_path):
    o, n = _pair()
    cs = pb.candidates(o, n)
    base = tr.placement_from_plan(cs[0])
    scored = tr.placement_from_plan(cs[0])
    scored.e = scored.s
    shape = tr.placement_from_plan(cs[0])
    shape.shape_gaps.append("the outgoing skips something")
    undec = tr.Placement(undecodable="no bass swap automation found inside the overlap")
    recs = [tr.analyse_transition(i + 1, ("o", "i"), o, n, base, pl) for i, pl in enumerate((scored, shape, undec))]
    assert [r["position"]["kind"] for r in recs] == [tr.K_OUT, tr.K_SHAPE, tr.K_UND]
    s = tr.summarise(recs)
    assert {i: [e["kind"] for e in v] for i, v in s["vocabulary_gaps"].items()} == {
        1: [tr.K_OUT], 2: [tr.K_SHAPE], 3: [tr.K_UND]}
    text = tr.format_report(_result(recs))
    for phrase in ("SCORED BUT NOT AMONG THE PLANNER'S CANDIDATES", "SHAPE NOT EXPRESSIBLE", "NOT DECODABLE"):
        assert phrase in text


def test_a_placement_the_cost_function_cannot_take_prints_one_reason_line_not_one_per_letter():
    o, n = _pair()
    bad_o = dict(o, bpm=0)                           # an unusable outgoing: no candidates, no score
    sam = tr.placement_from_plan(pb.plan_blend(o, n))
    r = tr.analyse_transition(1, ("o", "i"), bad_o, n, sam, sam)
    assert r["position"]["kind"] == tr.K_NOSCORE
    text = tr.format_report(_result([r]))
    assert "NOT SCOREABLE: the planner's cost function rejected the placement" in text
    assert len(text.splitlines()) < 60


def test_planner_equals_owner_case():
    o, n = _pair()
    top = pb.plan_blend(o, n)
    sam = tr.placement_from_plan(top)
    r = tr.analyse_transition(1, ("o", "i"), o, n, sam, sam)
    assert r["kept"] is True and r["position"]["kind"] == tr.K_TOP and r["position"]["rank"] == 1
    assert r["terms_difference"] == {} and r["decisive_term"] is None


def test_changed_but_identical_to_top_when_planner_set_was_something_else():
    o, n = _pair()
    cs = pb.candidates(o, n)
    base = tr.placement_from_plan(cs[3])
    sam = tr.placement_from_plan(cs[0])
    r = tr.analyse_transition(1, ("o", "i"), o, n, base, sam)
    assert r["kept"] is False and r["position"]["kind"] == tr.K_TOP


def test_distance_counts_loops_and_trims():
    x = tr.Placement(a=1.0, s=2.0, e=3.0)
    assert tr.distance(x, tr.Placement(a=1.0, s=2.0, e=3.0)) == 0.0
    assert tr.distance(x, tr.Placement(a=1.0, s=2.0, e=3.0, loop_l=8.0)) == 8.0
    assert tr.distance(x, tr.Placement(a=1.0, s=2.0, e=3.0, cut=2.0)) == 2.0


def test_the_consistency_checks_are_reported_for_both_sets(tmp_path, tops):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    res = tr.replay(base, base, TRACKS)
    r = res["transitions"][0]
    assert r["baseline"]["consistency_checks"] and r["sam"]["consistency_checks"]
    assert "decoder consistency checks: planner's set ok; tweak ok" in tr.format_report(res)


# ---- the trivial baseline ---------------------------------------------------------------------
def test_trivial_baseline_is_sixteen_in_sixteen_out_on_the_outro_start_and_is_not_the_planners_pick():
    o, n = _pair()
    triv = tr.trivial_placement(o, n)
    outro = next(x[0] for x in pb._sections(o) if x[1] == "OUTRO")
    assert (triv.a, triv.s, triv.e) == (outro - 16, outro, outro + 16)
    top = pb.plan_blend(o, n)
    r = tr.analyse_transition(1, ("o", "i"), o, n, tr.placement_from_plan(top), tr.placement_from_plan(top))
    assert (r["trivial"]["placement"]["swap"]) == outro
    assert r["trivial"]["placement"]["entry"] != top.in_starts_at_out_bar or \
        r["trivial"]["placement"]["end"] != top.out_end_bar
    ref = pb._score(o, n, triv.a, triv.s, triv.e, 0.0, 0.0, pb.phrase_points(o), pb.phrase_points(n))
    assert r["trivial"]["cost"] == pytest.approx(round(ref.cost, 4))
    assert r["trivial"]["cost"] != pytest.approx(round(top.cost, 4))


def test_trivial_is_also_reported_with_its_end_clamped_to_the_natural_end():
    o, n = _pair()
    r = tr.analyse_transition(1, ("o", "i"), o, n, tr.placement_from_plan(pb.plan_blend(o, n)),
                              tr.placement_from_plan(pb.plan_blend(o, n)))
    n_out = pb.natural_end_bar(o)
    assert r["trivial"]["clamped"]["placement"]["end"] == min(r["trivial"]["placement"]["end"], n_out)
    triv = r["trivial"]["placement"]
    ref = tr.score_placement(o, n, tr.Placement(a=triv["entry"], s=triv["swap"], e=min(triv["end"], n_out), k=16.0))
    assert r["trivial"]["clamped"]["cost"] == pytest.approx(round(ref.cost, 4))
    assert "with its end clamped to the natural end" in tr.format_report(_result([r]))


def test_the_trivial_baselines_outside_reason_is_printed():
    for i in range(len(KEY["blends"])):
        o, n = _pair(i)
        r = tr.analyse_transition(1, ("o", "i"), o, n, tr.placement_from_plan(pb.plan_blend(o, n)),
                                  tr.placement_from_plan(pb.plan_blend(o, n)))
        if not r["trivial"]["in_vocabulary"] and r["trivial"].get("outside_because"):
            text = tr.format_report(_result([r]))
            assert "likely reason (hand-written check, not the planner's verdict): " + r["trivial"]["outside_because"][0] in text
            return
    pytest.fail("no answer-key pair has a trivial baseline outside the planner's candidates")


def test_trivial_without_an_outro_uses_sixteen_bars_before_the_end():
    o = {"bpm": 128.0, "n_bars": 120, "sections": [[0, "INTRO"], [16, "DROP"]], "fills": []}
    n = trk(100)
    triv = tr.trivial_placement(o, n)
    assert (triv.a, triv.s, triv.e) == (88.0, 104.0, 120.0) and "no OUTRO" in triv.notes[0]


# ---- the summary --------------------------------------------------------------------------------
def _fake(i, kept, kind, planner_cost=5.0, trivial=4.0, clamped=4.0, rank=None, entry=1.0):
    return {"transition": i, "kept": kept, "position": {"kind": kind, "rank": rank}, "position_alt": None,
            "vocabulary": [], "planner_top": {"cost": planner_cost}, "planner_cost": planner_cost,
            "trivial_plan_cost": trivial, "trivial_clamped_cost": clamped, "sam_scored": {"cost": 1.0},
            "sam": {"entry": entry, "swap": 2.0, "end": 3.0},
            "trivial": {"placement": {"entry": 1.0, "swap": 2.0, "end": 3.0}, "closer": "tie"}, "delivered": None}


def test_unchanged_transitions_are_never_counted_as_agreement():
    recs = [_fake(1, True, tr.K_TOP), _fake(2, True, tr.K_TOP), _fake(3, False, tr.K_RANKED, rank=9),
            _fake(4, False, tr.K_OUT)]
    s = tr.summarise(recs, "Sam")
    assert s["changed"] == [3, 4] and s["unchanged_not_evidence"] == [1, 2]
    assert s["top_pick_equals_owner_on_changed"] == [] and s["top_pick_or_listed_alternative_on_changed"] == []


def test_the_summary_sentence_says_changed_of_total_and_that_unchanged_are_not_evidence(tmp_path, tops):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    changed = [dict(tops[0]), dict(tops[1])]
    changed[1]["a"] += 8.0
    sam = build(tmp_path / "sam.als", NAMES, TRACKS, changed)
    res = tr.replay(base, sam, TRACKS, owner="Sam")
    text = tr.format_report(res)
    assert ("Sam changed 1 of 2 (T2); the planner's top pick equals his on 0 of the 1 changed; "
            "1 unchanged transitions (T1) are not evidence") in text


def test_no_sentence_calls_matching_the_owner_independent_evidence_and_the_limits_are_stated(tmp_path, tops):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    text = tr.format_report(tr.replay(base, base, TRACKS))
    assert "independent evidence" not in text.lower()
    assert "What this can and cannot show" in text and "does not show that the planner or the edit is right" in text


def test_the_report_does_not_hard_code_the_owner_name(tmp_path, tops):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    changed = [dict(tops[0]), dict(tops[1])]
    changed[1]["a"] += 8.0
    sam = build(tmp_path / "sam.als", NAMES, TRACKS, changed)
    assert "Sam" not in tr.format_report(tr.replay(base, sam, TRACKS))
    assert "Sam" in tr.format_report(tr.replay(base, sam, TRACKS, owner="Sam"))


def test_plain_verdict_is_said_when_the_planner_beats_neither():
    recs = [_fake(1, False, tr.K_RANKED, rank=9), _fake(2, False, tr.K_RANKED, rank=9)]
    s = tr.summarise(recs, "Sam")
    assert s["plain_verdict"] and "does NOT beat" in s["plain_verdict"]


def test_a_trivial_baseline_with_no_cost_still_counts_in_the_denominator():
    recs = [_fake(1, False, tr.K_RANKED, rank=3), _fake(2, False, tr.K_RANKED, rank=3, trivial=None)]
    s = tr.summarise(recs)
    c = s["planner_delivered_cost_vs_trivial"]
    assert c["not_scoreable"] == 1 and c["planner_higher"] == 1
    assert sum(c.values()) == 2


def test_summary_counts_on_a_known_set():
    o, n = _pair()
    cs = pb.candidates(o, n)
    recs = []
    for i, c in enumerate((cs[0], cs[2], cs[0])):
        sam = tr.placement_from_plan(c)
        recs.append(tr.analyse_transition(i + 1, ("o", "i"), o, n, tr.placement_from_plan(cs[0]), sam))
    s = tr.summarise(recs)
    assert s["changed"] == [2] and s["unchanged_not_evidence"] == [1, 3]
    assert s["top_pick_equals_owner_on_changed"] == [] and s["owner_in_top_3_on_changed"] == [2]


# ---- the CLI ----------------------------------------------------------------------------------
def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _content(tmp_path, tracks=None, name="content.json"):
    content = tmp_path / name
    content.write_text(json.dumps({"tracks": tracks or TRACKS}), encoding="utf-8")
    return content


def test_cli_writes_only_the_two_outputs_and_never_touches_inputs(tmp_path, tops):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    changed = [dict(tops[0]), dict(tops[1])]
    changed[1]["a"] += 8.0
    sam = build(tmp_path / "sam.als", NAMES, TRACKS, changed)
    content = _content(tmp_path)
    before = {p: _sha(p) for p in (base, sam, content)}
    out = tmp_path / "out"
    assert cli.main(["--baseline", str(base), "--tweak", str(sam), "--content", str(content), "--out", str(out),
                     "--owner-name", "Sam"]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["tweak_replay.json", "tweak_replay_report.txt"]
    assert before == {p: _sha(p) for p in before}
    data = json.loads((out / "tweak_replay.json").read_text())
    assert data["summary"]["unchanged_not_evidence"] == [1] and data["transitions"][1]["kept"] is False
    text = (out / "tweak_replay_report.txt").read_text()
    assert "PASSED on 2 of 2 transitions" in text and "SUMMARY (2 transitions)" in text
    assert text.isascii()


def test_cli_exit_code_two_and_a_readable_failed_report_when_the_round_trip_refuses(tmp_path, tops):
    other = [dict(tops[0]), dict(tops[1])]
    other[0]["a"] -= 8.0
    other[0]["s"] -= 8.0
    other[0]["e"] -= 8.0
    base = build(tmp_path / "base.als", NAMES, TRACKS, other)
    out = tmp_path / "out"
    assert cli.main(["--baseline", str(base), "--tweak", str(base), "--content", str(_content(tmp_path)),
                     "--out", str(out)]) == 2
    text = (out / "tweak_replay_report.txt").read_text()
    assert "ROUND TRIP FAILED" in text and "Nothing was scored" in text
    assert "T1 Artist A - Alpha -> Artist B - Beta: decoded" in text and "differs in" in text   # one row per transition
    assert json.loads((out / "tweak_replay.json").read_text())["round_trip"]["passed"] is False


def test_cli_refuses_to_overwrite_an_input_with_one_line(tmp_path, tops, capsys):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    content = tmp_path / "tweak_replay.json"
    content.write_text(json.dumps({"tracks": TRACKS}), encoding="utf-8")
    assert cli.main(["--baseline", str(base), "--tweak", str(base), "--content", str(content),
                     "--out", str(tmp_path)]) == 2
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and "overwrite an input" in out[0]


def test_cli_prints_one_plain_line_and_exits_two_for_input_problems(tmp_path, tops, capsys):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    two = build(tmp_path / "two.als", NAMES[:2], TRACKS, tops[:1])
    missing = _content(tmp_path, {NAMES[0]: TRACKS[NAMES[0]], NAMES[1]: TRACKS[NAMES[1]]}, "missing.json")
    log = tmp_path / "phase2.log"
    log.write_text("  T1: dropped intro_loop, gate\n", encoding="utf-8")
    cases = [
        ["--baseline", str(base), "--tweak", str(two), "--content", str(_content(tmp_path))],          # different tracks
        ["--baseline", str(base), "--tweak", str(base), "--content", str(missing)],                    # no planner data
        ["--baseline", str(base), "--tweak", str(base), "--content", str(_content(tmp_path)),
         "--pipeline-log", str(log)],                                                                   # drop the plan lacks
        ["--baseline", str(base), "--tweak", str(base), "--project", str(tmp_path / "nowhere")],      # no cache
    ]
    for args in cases:
        capsys.readouterr()
        assert cli.main(args + ["--out", str(tmp_path / "o")]) == 2
        out = capsys.readouterr().out.strip().splitlines()
        assert len(out) == 1 and out[0].startswith("score_tweak: ") and "Traceback" not in out[0], args


def test_the_two_sets_must_hold_the_same_tracks(tmp_path, tops):
    base = build(tmp_path / "base.als", NAMES, TRACKS, tops)
    two = build(tmp_path / "two.als", NAMES[:2], TRACKS, tops[:1])
    with pytest.raises(ValueError, match="same tracks"):
        tr.replay(base, two, TRACKS)


def test_no_em_dash_in_the_new_files():
    for rel in ("Source/tweak_replay.py", "Tools/score_tweak.py", "Tests/test_score_tweak.py"):
        assert chr(0x2014) not in (ROOT / rel).read_text(encoding="utf-8"), rel
