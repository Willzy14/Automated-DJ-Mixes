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
    info, text = run("Ninetoes", "Ashibah")       # only 176 meets any rule
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
    flips = bs.run(25, out=lambda *_: None)
    names = {f[0] for f in flips}
    assert {"MIX_ZONE_SECONDS", "END_SLACK_BARS"} <= names
    # every constant is restored afterwards
    assert pb.MIX_ZONE_SECONDS == 75.0 and pb.END_SLACK_BARS == 1.0
