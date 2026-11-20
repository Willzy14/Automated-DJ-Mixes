"""Tests for Tools/score_sections.py (section detector vs the owner's answer key)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Tools"))

import score_sections as ss  # noqa: E402

BPM = 120.0
SPB = 240.0 / BPM   # 2 s per bar


def _res(starts_bar, fills_bar=(), gaps_bar=(), downbeat=0.0, labels=None, fill_sections=None,
         analysis=None):
    secs = []
    bounds = list(starts_bar) + [starts_bar[-1] + 8]
    for i, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])):
        secs.append({"start_bar": a, "end_bar": b, "label": (labels or ["drop"] * 99)[i],
                     "start_sec": downbeat + a * SPB, "end_sec": downbeat + b * SPB})
    cues = []
    for a, b in gaps_bar:
        cues.append({"type": "kick_dropout", "start_sec": downbeat + a * SPB})
        cues.append({"type": "kick_return", "start_sec": downbeat + b * SPB})
    sig = {"fills": [[downbeat + a * SPB, downbeat + b * SPB] for a, b in fills_bar], "kick_cues": cues}
    if fill_sections is not None:
        sig["fill_sections"] = fill_sections
    out = {"bpm": BPM, "sections": secs, "signals": sig}
    if analysis is not None:
        out["analysis"] = analysis
    return out


def _key(sections, fills=(), lead_in=0.0, labels=None):
    kt = {"bpm": BPM, "n_bars": 100,
          "sections": [[b, (labels or ["DROP"] * 99)[i]] for i, b in enumerate(sections)],
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
    t = ss.score_track(_key([0, 16]), _res([0, 16, 17]))
    assert (t["found1"], t["missed"], t["extra"]) == (1, 0, 1)


def test_matching_finds_the_most_pairs_not_the_greedy_closest():
    # greedy closest-first would pair 16.2 with 16 and leave 17 unmatched
    pairs, missed, extra = ss.match_lines([16.0, 17.0], [15.2, 16.2], 1.0)
    assert len(pairs) == 2 and missed == [] and extra == []


def test_lead_in_and_bar_one_offset_read_from_analysis():
    t = ss.score_track(_key([0, 32], lead_in=0.5), _res([0, 32]))
    assert t["bar1_offset"] == -0.5 and t["found1"] == 1 and t["found0"] == 0
    res = _res([0, 32], downbeat=0.5 * SPB, analysis={"downbeat_sec": 0.5 * SPB})
    t2 = ss.score_track(_key([0, 32], lead_in=0.5), res)
    assert t2["bar1_offset"] == 0.0 and t2["found0"] == 1


def test_suspect_offset_is_reported():
    res = _res([0, 32], analysis={"bar_one_suspect": {"suspect": True, "offset_bars": 0.5}})
    assert ss.score_track(_key([0, 32]), res)["bar1_suspect_offset"] == 0.5


def test_fills_count_only_from_the_detector_fill_output():
    key = _key([0, 16], fills=[(15, 16), (31, 32), (47, 48)])
    res = _res([0, 16], fills_bar=[(15.75, 16), (60, 61)], gaps_bar=[(31, 32), (70, 80)])
    t = ss.score_track(key, res)
    assert (t["fills_found"], t["fills_missed"], t["fills_gap_only"]) == (1, 2, 1)
    assert t["fills_extra"] == 1
    assert t["detail"]["gap_only_fills"] == [[31.0, 32.0]]


def test_one_detector_fill_can_find_only_one_key_fill():
    key = _key([0], fills=[(15, 16), (16, 17)])
    t = ss.score_track(key, _res([0], fills_bar=[(15, 17)]))
    assert t["fills_found"] == 1 and t["fills_missed"] == 1 and t["fills_extra"] == 0


def test_fill_edges_within_a_beat_count_as_exact():
    key = _key([0], fills=[(15, 16), (31, 32)])
    t = ss.score_track(key, _res([0], fills_bar=[(15.25, 16), (30.5, 32)]))
    assert t["fills_found"] == 2 and t["fills_exact"] == 1


def test_touching_is_not_overlap():
    t = ss.score_track(_key([0], fills=[(15, 16)]), _res([0], fills_bar=[(16, 17)]))
    assert t["fills_found"] == 0 and t["fills_extra"] == 1


def test_fill_sections_preferred_over_legacy_fills():
    fs = [{"start_sec": 15 * SPB, "end_sec": 16 * SPB}]
    t = ss.score_track(_key([0], fills=[(15, 16)]), _res([0], fills_bar=[(40, 41)], fill_sections=fs))
    assert t["fills_found"] == 1 and t["fills_extra"] == 0


def test_label_agreement_on_matched_lines():
    key = _key([0, 16, 32], labels=["INTRO", "DROP", "BREAK"])
    res = _res([0, 16, 32], labels=["intro", "intro", "break"])
    t = ss.score_track(key, res)
    assert (t["labels_agree"], t["labels_checked"]) == (1, 2)
    assert t["detail"]["label_disagree"] == [[16.0, "DROP", "INTRO"]]


def test_track_identity_is_exact():
    assert ss.track_matches("PAX - Revelry 24 Bit MASTER", "PAX - Revelry")
    assert ss.track_matches("PAX - Revelry", "PAX - Revelry")
    assert ss.track_matches("Sam Leagas - Double Dutch (Extended Mix) SW V1", "Sam Leagas - Double Dutch (Extended Mix)")
    assert not ss.track_matches("PAX - Revelry (Someone Remix) 24 Bit MASTER", "PAX - Revelry")
    assert not ss.track_matches("PAX - Revelry Dub 24 Bit MASTER", "PAX - Revelry")


def test_remix_file_is_never_scored_as_the_original(tmp_path):
    key = {"tracks": {"A - B": _key([0, 16])}}
    (tmp_path / "SECTIONS_STEM_A - B (X Remix) 24 Bit MASTER.json").write_text(
        json.dumps(_res([0, 16])), encoding="utf-8")
    scored = ss.score_all(key, tmp_path)
    assert scored["missing"] == ["A - B"]


def test_two_versions_of_one_track_is_an_error(tmp_path):
    for v in ("24 Bit MASTER", "24 Bit MASTER AMENDED"):
        (tmp_path / f"SECTIONS_STEM_A - B {v}.json").write_text(json.dumps(_res([0, 16])), encoding="utf-8")
    with pytest.raises(ValueError):
        ss.score_all({"tracks": {"A - B": _key([0, 16])}}, tmp_path)


def test_score_all_table_and_partial_exit(tmp_path):
    key = {"tracks": {"Artist - Song": _key([0, 16], fills=[(15, 16)]),
                      "Other - Missing": _key([0, 8])}}
    (tmp_path / "SECTIONS_STEM_Artist - Song 24 Bit MASTER.json").write_text(
        json.dumps(_res([0, 16], fills_bar=[(15, 16)])), encoding="utf-8")
    keyp = tmp_path / "key.json"
    keyp.write_text(json.dumps(key), encoding="utf-8")
    scored = ss.score_all(key, tmp_path)
    assert scored["total"]["found1"] == 1 and scored["total"]["fills_found"] == 1
    table = ss.format_table(scored)
    assert "TOTAL (1 tracks)" in table and "[missing result] Other - Missing" in table and table.isascii()
    assert ss.main(["--results", str(tmp_path), "--key", str(keyp)]) == 3
    assert ss.main(["--results", str(tmp_path), "--key", str(keyp), "--allow-partial"]) == 0


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
