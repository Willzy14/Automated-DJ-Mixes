"""Tests for Tools/blend_candidates.py and Tools/blend_sensitivity.py."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))
sys.path.insert(0, str(ROOT / "Tools"))
import blend_candidates as bc  # noqa: E402
import blend_sensitivity as bs  # noqa: E402
import phrase_blend as pb  # noqa: E402


def run(out_part, in_part):
    lines = []
    key = json.loads(bc.KEY.read_text(encoding="utf-8"))["tracks"]
    content = json.loads(bc.CONTENT.read_text(encoding="utf-8"))["tracks"]
    names = (bc.find(key, out_part), bc.find(key, in_part))
    ts = []
    for n in names:
        t = json.loads(json.dumps(key[n]))
        t["content"] = {k: content[n][k] for k in ("has_bass", "has_music", "has_kick")}
        ts.append(t)
    info = bc.report(ts[0], ts[1], names[0], names[1], 5, pb.R6_BE, out=lines.append)
    return info, "\n".join(lines)


def test_prints_five_candidates_with_every_cost_term():
    info, text = run("Eli Brown", "Call 911")
    assert text.count("terms:") == 5 and "switch=" not in text
    for term in ("length=", "switch_time="):
        assert term in text


def test_flags_a_pair_where_the_switch_rules_disagree():
    info, text = run("Eli Brown", "Call 911")     # entry+16 and 30 s say 160, both-track says 168
    assert info["disagree"] and "DISAGREE" in text


def test_says_when_the_rules_agree():
    info, text = run("Eli Brown", "Ashibah")      # every switch rule points at the same bar
    assert not info["disagree"] and "cannot discriminate" in text


def test_main_runs_and_rejects_an_ambiguous_name(capsys):
    assert bc.main(["--out", "Eli Brown", "--in", "Call 911"]) == 0
    try:
        bc.main(["--out", "a", "--in", "Call 911"])
    except SystemExit as e:
        assert "matches" in str(e)
    else:
        raise AssertionError("an ambiguous name must stop the tool")


def test_rules_for_reads_entry_plus_16_the_half_minute_and_both_tracks():
    key = json.loads(bc.KEY.read_text(encoding="utf-8"))["tracks"]
    o = key["Eli Brown - Better When"]
    n = key["Adam Ten & Rafael - Call 911"]
    sam = pb.BlendPlan(144.0, 160.0, 176.0, 0.0, 0.0)
    r = bc.rules_for(sam, o, n)
    assert r["ENTRY+16"] and r["30 SECONDS"] and not r["BOTH"]


def test_sensitivity_flips_are_reported_for_the_geometry_constants():
    flips = bs.run(25, out=lambda *_: None, constants=["MIX_ZONE_SECONDS", "END_SLACK_BARS", "W_FLAT_RUN"])
    names = {f[0] for f in flips}
    assert "END_SLACK_BARS" in names and "W_FLAT_RUN" not in names
    # every constant is restored afterwards
    assert pb.MIX_ZONE_SECONDS == 75.0 and pb.END_SLACK_BARS == 1.0 and pb.W_FLAT_RUN == 0.25


def test_the_owners_names_for_points_and_pairings():
    key = json.loads(bc.KEY.read_text(encoding="utf-8"))["tracks"]
    a = key["Darius Syrossian & George Smeddles - Back In The Dance"]
    b = key["PAX - Revelry"]
    assert bc.point_name("A", a, 184) == "A-F7 end (bar 184)"
    assert bc.point_name("A", a, 200) == "A-F8 end = A11 (bar 200)"      # the outro start is also a fill end
    assert bc.point_name("B", b, 16) == "B1 (bar 16)"
    assert bc.point_name("B", b, 32) == "B-F1 end (bar 32)"
    assert bc.point_name("A", a, 209) == "A end (bar 209)" and bc.point_name("A", a, 0) == "A start (bar 0)"
    assert "no phrase point" in bc.point_name("A", a, 193)


def test_report_prints_pairings_by_name():
    info, text = run("Back In", "PAX")
    assert "swap: A-F8 end = A11 (bar 200) with B1 (bar 16) [B kick-in]  <sane PAIRING>" in text


def test_scan_ranks_unplaced_pairs_and_skips_the_owners_own():
    keydata = json.loads(bc.KEY.read_text(encoding="utf-8"))
    rows = bc.scan(keydata, json.loads(bc.CONTENT.read_text(encoding="utf-8"))["tracks"], 3, out=lambda *_: None)
    assert [r[0] for r in rows] == sorted((r[0] for r in rows), reverse=True)
    placed = {(b["out"], b["in"]) for b in keydata["blends"]}
    assert not any((r[1], r[2]) in placed for r in rows)
