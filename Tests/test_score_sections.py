"""Tests for Tools/score_sections.py (section detector vs the owner's answer key)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Tools"))

import score_sections as ss  # noqa: E402

BPM = 120.0
SPB = 240.0 / BPM   # 2 s per bar


def _res(starts_bar, fills_bar=(), gaps_bar=(), downbeat=0.0, fill_sections=None):
    secs = []
    bounds = list(starts_bar) + [starts_bar[-1] + 8]
    for a, b in zip(bounds[:-1], bounds[1:]):
        secs.append({"start_bar": a, "end_bar": b, "label": "drop",
                     "start_sec": downbeat + a * SPB, "end_sec": downbeat + b * SPB})
    cues = []
    for a, b in gaps_bar:
        cues.append({"type": "kick_dropout", "start_sec": downbeat + a * SPB})
        cues.append({"type": "kick_return", "start_sec": downbeat + b * SPB})
    sig = {"fills": [[downbeat + a * SPB, downbeat + b * SPB] for a, b in fills_bar],
           "kick_cues": cues}
    if fill_sections is not None:
        sig["fill_sections"] = fill_sections
    return {"sections": secs, "signals": sig}


def _key(sections, fills=(), lead_in=0.0):
    kt = {"bpm": BPM, "n_bars": 100, "sections": [[b, "X"] for b in sections],
          "fills": [list(f) for f in fills]}
    if lead_in:
        kt["lead_in_bars"] = lead_in
    return kt


def test_exact_match_scores_everything_found():
    t = ss.score_track(_key([0, 16, 32]), _res([0, 16, 32]))
    assert (t["lines_key"], t["found1"], t["found0"], t["missed"], t["extra"]) == (2, 2, 2, 0, 0)
    assert t["bar1_offset"] == 0.0


def test_one_bar_off_counts_within_1_not_exact():
    t = ss.score_track(_key([0, 16, 32]), _res([0, 17, 32]))
    assert (t["found1"], t["found0"], t["missed"], t["extra"]) == (2, 1, 0, 0)


def test_two_bars_off_is_missed_and_extra():
    t = ss.score_track(_key([0, 16]), _res([0, 18]))
    assert (t["found1"], t["missed"], t["extra"]) == (0, 1, 1)


def test_matching_is_one_to_one():
    # two detector lines near one key line: one matches, the other is extra
    t = ss.score_track(_key([0, 16]), _res([0, 16, 17]))
    assert (t["found1"], t["missed"], t["extra"]) == (1, 0, 1)


def test_closest_pair_wins():
    pairs, missed, extra = ss.match_lines([16.0, 17.0], [16.9], 1.0)
    assert pairs == [(17.0, 16.9)] and missed == [16.0] and extra == []


def test_lead_in_and_bar_one_offset():
    # key counts from a first beat half a bar into the file; detector grid starts at the file start
    t = ss.score_track(_key([0, 32], lead_in=0.5), _res([0, 32]))
    assert t["bar1_offset"] == -0.5
    assert t["found1"] == 1 and t["found0"] == 0
    # detector that also starts at the first beat lines up exactly
    t2 = ss.score_track(_key([0, 32], lead_in=0.5), _res([0, 32], downbeat=0.5 * SPB))
    assert t2["bar1_offset"] == 0.0 and t2["found0"] == 1


def test_fill_found_by_fill_or_kick_gap_and_extra_counts_fills_only():
    key = _key([0, 16], fills=[(15, 16), (31, 32), (47, 48)])
    res = _res([0, 16], fills_bar=[(15.75, 16), (60, 61)], gaps_bar=[(31, 32), (70, 80)])
    t = ss.score_track(key, res)
    assert (t["fills_key"], t["fills_found"], t["fills_missed"]) == (3, 2, 1)
    assert t["fills_extra"] == 1          # the fill at 60; the 70-80 gap is not a fill claim
    assert t["detail"]["missed_fills"] == [[47.0, 48.0]]


def test_touching_is_not_overlap():
    key = _key([0], fills=[(15, 16)])
    t = ss.score_track(key, _res([0], fills_bar=[(16, 17)]))
    assert t["fills_found"] == 0 and t["fills_extra"] == 1


def test_fill_sections_preferred_over_legacy_fills():
    fs = [{"start_sec": 15 * SPB, "end_sec": 16 * SPB}]
    t = ss.score_track(_key([0], fills=[(15, 16)]), _res([0], fills_bar=[(40, 41)], fill_sections=fs))
    assert t["fills_found"] == 1 and t["fills_extra"] == 0


def test_score_all_and_table(tmp_path):
    key = {"tracks": {"Artist - Song": _key([0, 16], fills=[(15, 16)]),
                      "Other - Missing": _key([0, 8])}}
    (tmp_path / "SECTIONS_STEM_Artist - Song 24 Bit MASTER.json").write_text(
        json.dumps(_res([0, 16], fills_bar=[(15, 16)])), encoding="utf-8")
    scored = ss.score_all(key, tmp_path)
    assert scored["total"]["found1"] == 1 and scored["total"]["fills_found"] == 1
    assert scored["missing"] == ["Other - Missing"]
    table = ss.format_table(scored)
    assert "TOTAL (1 tracks)" in table and "[missing result] Other - Missing" in table
    assert table.isascii()


def test_cli_writes_json(tmp_path):
    keyp = tmp_path / "key.json"
    keyp.write_text(json.dumps({"tracks": {"A - B": _key([0, 16])}}), encoding="utf-8")
    (tmp_path / "SECTIONS_STEM_A - B.json").write_text(json.dumps(_res([0, 16])), encoding="utf-8")
    out = tmp_path / "s.json"
    assert ss.main(["--results", str(tmp_path), "--key", str(keyp), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["total"]["found1"] == 1


def test_real_key_loads_and_has_seven_tracks():
    key = json.loads(ss.DEFAULT_KEY.read_text(encoding="utf-8"))
    assert len(key["tracks"]) == 7
    for kt in key["tracks"].values():
        assert kt["sections"][0][0] == 0
