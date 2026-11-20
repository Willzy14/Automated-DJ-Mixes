import copy
import json
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Source"))

import align_engine
import apply_automation
import materialize_section_details
import section_format
import stem_detector
from alignment_feasibility import load_tracks
from automated_dj_mixes import orchestrator
from automated_dj_mixes.phrase_viz import segments_from_stem_sections
from extract_musical_landmarks import refresh_track_landmarks
from validate_hints_vs_sections import validate


@pytest.fixture
def groove():
    def section(label, start, end):
        return {"label": label, "start_bar": start, "end_bar": end,
                "start_sec": start * 2, "end_sec": end * 2}

    return {
        "track": "T", "bpm": 120, "n_bars": 80,
        "sections": [section("intro", 0, 8), section("intro", 8, 16),
                     section("drop", 16, 32), section("break", 32, 40),
                     section("drop", 40, 64), section("outro", 64, 72),
                     section("outro", 72, 80)],
        "signals": {
            "fills": [[112, 112.51]],
            "fill_sections": [section("fill", 56, 56.25), section("fill", 62.75, 63)],
            "bass_in": 32.0, "bass_out": 128.0, "loop_windows": [[0, 32, 0, 16]],
        },
        "analysis": {"kick_model": True, "section_detector": "groove-1",
                     "sections_schema": 2, "bar_one_suspect": {"suspect": True, "offset_beats": 2}},
    }


def _write_cache(project, data):
    directory = project / "_Stem Analysis"
    directory.mkdir(exist_ok=True)
    path = directory / f"SECTIONS_STEM_{data['track']}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _legacy(groove):
    data = copy.deepcopy(groove)
    data["analysis"] = {"kick_model": True}
    data["signals"].pop("fill_sections")
    return data


def test_adapter_keeps_exact_fill_spans_and_rejects_unknown_or_malformed(groove):
    fills = section_format.groove_fills(groove, "test")
    assert fills == [(56, 56.25, 112, 112.5), (62.75, 63, 125.5, 126)]
    bad_schema = copy.deepcopy(groove)
    bad_schema["analysis"]["sections_schema"] = 3
    with pytest.raises(section_format.SectionFormatError, match="unsupported"):
        section_format.groove_fills(bad_schema, "test")
    groove["signals"].pop("fill_sections")
    with pytest.raises(section_format.SectionFormatError, match="fill_sections"):
        section_format.groove_fills(groove, "test")


def test_alignment_uses_groove_fills_as_events_and_respects_the_grid(tmp_path, groove):
    track = align_engine.load_track(_write_cache(tmp_path, groove))
    assert track.downbeat == 0
    assert track.fills == [(56, 56.25), (62.75, 63)]
    assert align_engine._leading_intro_end(track) == 16
    assert track.boundaries() == [(0, "intro"), (8, "intro"), (16, "drop"), (32, "break"),
                                  (40, "drop"), (64, "outro"), (72, "outro")]
    cues = align_engine._mix_cues(track)
    assert cues[63]["labels"] == ["fill:end"]
    assert 56 not in cues and 57 not in cues
    assert align_engine.pick_clean_drum_loop(track, 55, 57, pref=2) is None


def test_legacy_reader_keeps_rounded_seconds_and_the_original_intro_scope(tmp_path, groove):
    track = align_engine.load_track(_write_cache(tmp_path, _legacy(groove)))
    assert track.fills == [(56, 112.51 / 2)]
    assert align_engine._leading_intro_end(track) == 8
    assert 63 not in align_engine._mix_cues(track)


def test_feasibility_and_loop_quality_accept_groove_without_moving_bar_one(tmp_path, groove):
    path = _write_cache(tmp_path, groove)
    np.savez(path.with_name("T__stemenv.npz"), hop_t=0.1, mix=np.ones(8))
    track, bpm = load_tracks(tmp_path)[0]
    assert bpm == 120 and track.section_mode == "groove"
    assert align_engine.load_loop_quality_context(track.envelope_cache_path).downbeat_sec == 0
    _write_cache(tmp_path, {**_legacy(groove), "track": "Legacy"})
    with pytest.raises(section_format.SectionFormatError, match="mixed"):
        load_tracks(tmp_path)


def test_layout_and_hints_gate_consume_separate_groove_events(tmp_path, groove):
    groove["analysis"]["downbeat_sec"] = 22
    for row in groove["sections"] + groove["signals"]["fill_sections"]:
        row["start_sec"] += 22
        row["end_sec"] += 22
    _write_cache(tmp_path, groove)
    segments = segments_from_stem_sections(groove)
    assert [segment.name for segment in segments] == [
        "intro_1", "intro_2", "drop_1", "break_1", "drop_2", "outro_1", "outro_2"]
    assert segments[-2].source_start_beats == 256
    hints = stem_detector.hints_from_stem_result(groove)
    assert hints == {"first_drop_sec": 54, "first_break_sec": 86,
                     "outro_start_sec": 150, "last_bass_drop_sec": 148}
    review = tmp_path / "Sections Review"
    review.mkdir()
    (review / "Sections_V1.json").write_text(json.dumps(
        {"T": [vars(segment) for segment in segments]}), encoding="utf-8")
    (tmp_path / "Hints").mkdir()
    (tmp_path / "Hints" / "track_hints.json").write_text(json.dumps({"T.wav": hints}), encoding="utf-8")
    assert validate(tmp_path, 1)[0] == 0
    groove["signals"]["fill_sections"] = []
    assert stem_detector.hints_from_stem_result(groove)["last_bass_drop_sec"] == 150


def test_hint_cli_refuses_other_mode_and_leaves_cache_untouched(tmp_path, monkeypatch, groove):
    path = _write_cache(tmp_path, groove)
    audio = tmp_path / "Audio"
    audio.mkdir()
    (audio / "T.wav").touch()
    before = path.read_bytes()
    args = ["stem_detector.py", str(tmp_path), "--write-hints"]
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as exc:
        stem_detector.main()
    assert exc.value.code == 2
    assert not (tmp_path / "Hints").exists()
    monkeypatch.setattr(sys, "argv", [*args, "--section-mode", "groove"])
    stem_detector.main()
    assert json.loads((tmp_path / "Hints" / "track_hints.json").read_text())["T.wav"][
        "last_bass_drop_sec"] == 126
    assert path.read_bytes() == before
    with pytest.raises(section_format.SectionFormatError, match="--section-mode legacy"):
        stem_detector._may_overwrite_sections_json(path, False, "legacy")
    assert stem_detector._may_overwrite_sections_json(path, True, "legacy")


@pytest.mark.parametrize("mode", [None, "groove"])
def test_orchestrator_cli_forwards_section_mode(monkeypatch, tmp_path, mode):
    detected = {}
    monkeypatch.setattr(orchestrator, "run_pipeline",
                        lambda *args, **kwargs: detected.update(kwargs))
    args = ["orchestrator", "--input", str(tmp_path), "--output", str(tmp_path)]
    if mode:
        args += ["--section-mode", mode]
    monkeypatch.setattr(sys, "argv", args)
    orchestrator.main()
    assert detected["section_mode"] == (mode or "legacy")


def test_legacy_detail_refinement_refuses_groove_before_touching_the_set(groove):
    with pytest.raises(section_format.SectionFormatError, match="only legacy"):
        materialize_section_details.materialize_track([], "T", groove)


def test_landmark_refresh_preserves_groove_sections_and_fills(monkeypatch, tmp_path, groove):
    path = _write_cache(tmp_path, groove)
    audio = tmp_path / "Audio"
    audio.mkdir()
    (audio / "T.wav").touch()

    class Provider:
        def presence_per_beat(self, _wav, *, bpm, downbeat, n_beats):
            assert (bpm, downbeat, n_beats) == (120, 0, 320)
            return type("Readout", (), {"raw": [], "section": []})()

    monkeypatch.setattr("extract_musical_landmarks.extract_kick_dropout_landmarks",
                        lambda *args, **kwargs: [{"type": "kick_dropout"}])
    refresh_track_landmarks(tmp_path, "T", provider=Provider(), render=False)
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["sections"] == groove["sections"]
    assert after["analysis"] == groove["analysis"]
    assert after["signals"]["fill_sections"] == groove["signals"]["fill_sections"]
    assert after["signals"]["musical_landmarks"] == [{"type": "kick_dropout"}]


def test_phase_three_requires_the_arranger_swap_for_groove():
    class Track:
        def __init__(self, name, start, end):
            self.name = name
            self.arr_start = start
            self.arr_end = end

    tracks = [Track("Out", 0, 100), Track("In", 60, 160)]
    with pytest.raises(ValueError, match="require a report swap"):
        apply_automation.plan_transitions(tracks, require_report_swaps=True)
