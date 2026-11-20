import copy
from dataclasses import replace
import json
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Source"))

import align_engine
import apply_automation
import materialize_section_details
import propose_arrangement
import section_format
import section_placement_viz
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


def _write_hints_review(project, segments, hints):
    review = project / "Sections Review"
    review.mkdir()
    (review / "Sections_V1.json").write_text(json.dumps(
        {"T": [vars(segment) for segment in segments]}), encoding="utf-8")
    (project / "Hints").mkdir()
    (project / "Hints" / "track_hints.json").write_text(
        json.dumps({"T.wav": hints}), encoding="utf-8")


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
    assert track == replace(track, section_mode="groove")


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
    _write_hints_review(tmp_path, segments, hints)
    output = tmp_path / "Output"
    output.mkdir()
    (output / "ARRANGEMENT_REPORT.json").write_text(
        json.dumps({"tracks": [{"name": "T", "bpm": 150}]}), encoding="utf-8")
    code, errors, report = validate(tmp_path, 1)
    assert (code, errors) == (0, [])
    assert "| T | first_drop_sec | 54.0 | 16.0 | 16.0 | +0.0 |" in report
    assert "| T | first_break_sec | 86.0 | 32.0 | 32.0 | +0.0 |" in report
    assert "| T | outro_start_sec | 150.0 | 64.0 | 64.0 | +0.0 |" in report
    assert "| T | last_bass_drop_sec | 148.0 | 63.0 | window 32.0-72.0 | - |" in report
    groove["signals"]["fill_sections"] = []
    assert stem_detector.hints_from_stem_result(groove)["last_bass_drop_sec"] == 150


@pytest.mark.parametrize("detector", [None, "foreign-9"])
def test_legacy_hints_gate_uses_readable_bpm_as_on_main(tmp_path, groove, detector):
    legacy = _legacy(groove)
    legacy["analysis"].update({"section_detector": detector, "downbeat_sec": 22})
    _write_cache(tmp_path, legacy)
    _write_hints_review(tmp_path, segments_from_stem_sections(_legacy(groove)),
                        {"first_drop_sec": 34, "last_bass_drop_sec": 126})
    code, errors, report = validate(tmp_path, 1)
    assert (code, errors) == (0, [])
    assert "| T | first_drop_sec | 34.0 | 17.0 | 16.0 | +1.0 |" in report
    assert "| T | last_bass_drop_sec | 126.0 | 63.0 | window 32.0-72.0 | - |" in report


@pytest.mark.parametrize("with_fill", [False, True])
def test_hints_cli_writes_and_gate_warns_on_each_synthetic_hint(
        tmp_path, monkeypatch, capsys, groove, with_fill):
    for section in groove["sections"]:
        if section["label"] == "outro":
            section["label"] = "drop"
    if not with_fill:
        groove["signals"]["fill_sections"] = []
    _write_cache(tmp_path, groove)
    audio = tmp_path / "Audio"
    audio.mkdir()
    (audio / "T.wav").touch()
    monkeypatch.setattr(sys, "argv", ["stem_detector.py", str(tmp_path), "--write-hints",
                                      "--section-mode", "groove"])
    stem_detector.main()
    hints_file = tmp_path / "Hints" / "track_hints.json"
    hints = json.loads(hints_file.read_text(encoding="utf-8"))["T.wav"]
    assert hints["outro_start_sec"] == 96
    assert hints["last_bass_drop_sec"] == (126 if with_fill else 96)
    expected = {"outro_start_sec"}
    if not with_fill:
        expected.add("last_bass_drop_sec")
    assert hints["fallback_hints"].keys() == expected
    review = tmp_path / "Sections Review"
    review.mkdir()
    (review / "Sections_V1.json").write_text(json.dumps(
        {"T": [vars(s) for s in segments_from_stem_sections(groove)]}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["validate_hints_vs_sections.py", str(tmp_path), "--version", "1"])
    from validate_hints_vs_sections import main
    assert main() == 0
    warnings = [line for line in capsys.readouterr().out.splitlines() if line.startswith("WARN:")]
    assert len(warnings) == len(expected)
    for key, metadata in hints["fallback_hints"].items():
        assert metadata["fallback"] is True and metadata["reason"]
        assert f"WARN: T / {key}: {metadata['reason']}" in warnings


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
    with pytest.raises(section_format.SectionFormatError, match="--kick-model"):
        stem_detector._may_overwrite_sections_json(path, False, "groove")


@pytest.mark.parametrize("detector", [None, "groove-1", "foreign-9"])
@pytest.mark.parametrize("cached_kick_on", [True, False])
def test_legacy_overwrite_guard_follows_main_provenance(
        tmp_path, groove, capsys, detector, cached_kick_on):
    groove["analysis"].update({"section_detector": detector, "kick_model": cached_kick_on})
    path = _write_cache(tmp_path, groove)
    assert stem_detector._may_overwrite_sections_json(path, False) is not cached_kick_on
    warning = capsys.readouterr().out
    assert ("so the file was NOT overwritten" in warning) is cached_kick_on
    assert stem_detector._may_overwrite_sections_json(path, True) is True


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


@pytest.mark.parametrize(("requested", "cache", "arm"), [
    (None, "groove", True),
    ("groove", "groove", True),
    (None, "foreign", False),
    (None, "no_dir", False),
    ("legacy", "groove", None),
    ("groove", "no_dir", None),
    ("groove", "missing", None),
    ("groove", "corrupt", None),
    ("groove", "legacy", None),
    (None, "mixed", None),
])
def test_phase_three_cli_arms_report_swaps_only_after_reading_the_project(
        tmp_path, monkeypatch, groove, requested, cache, arm):
    als_path = tmp_path / "Output" / "Arranged.als"
    output = als_path.with_name("Final.als")
    stem_dir = tmp_path / "_Stem Analysis"
    if cache != "no_dir":
        stem_dir.mkdir()
    if cache in {"groove", "legacy", "mixed", "foreign"}:
        data = _legacy(groove) if cache in {"legacy", "foreign"} else groove
        if cache == "foreign":
            data["analysis"]["section_detector"] = "other-1"
        _write_cache(tmp_path, data)
    if cache == "corrupt":
        (stem_dir / "SECTIONS_STEM_T.json").write_text("{", encoding="utf-8")
    sections = {"T": [{"arr_time": 0, "arr_end": 100, "source_end_beats": 100}]}
    if cache == "mixed":
        _write_cache(tmp_path, {**_legacy(groove), "track": "Second"})
        sections["Second"] = [{"arr_time": 60, "arr_end": 160, "source_end_beats": 100}]
    monkeypatch.setattr(apply_automation, "decompress_als", lambda _: [])
    monkeypatch.setattr("extract_sections_als.parse_sections_als", lambda _: sections)
    monkeypatch.setattr(apply_automation, "find_track_line_ranges",
                        lambda _: [(0, 10, name) for name in sections])
    args = ["apply_automation.py", str(als_path), str(tmp_path / "sections.json"), str(output)]
    if requested:
        args += ["--section-mode", requested]
    monkeypatch.setattr(sys, "argv", args)

    class ReachedPlanner(Exception):
        pass

    def inspect_plan(tracks, swaps, *, require_report_swaps):
        assert require_report_swaps is arm
        raise ReachedPlanner

    monkeypatch.setattr(apply_automation, "plan_transitions", inspect_plan)
    if arm is None:
        expected = {"groove": "groove analysis found", "no_dir": "_Stem Analysis directory",
                    "missing": "SECTIONS_STEM_T.json",
                    "corrupt": "cannot read section analysis", "legacy": "T.*legacy sections",
                    "mixed": "Second.*legacy sections"}
        with pytest.raises(section_format.SectionFormatError, match=expected[cache]):
            apply_automation.main()
    else:
        with pytest.raises(ReachedPlanner):
            apply_automation.main()
    assert not output.exists()


def test_propose_arrangement_reads_groove_analysis_with_align_engine(tmp_path, monkeypatch, groove):
    _write_cache(tmp_path, groove)
    sections = [{**vars(s), "arr_time": 64 + s.source_start_beats,
                 "arr_end": 64 + s.source_end_beats} for s in segments_from_stem_sections(groove)]
    section_json = tmp_path / "Sections.json"
    section_json.write_text(json.dumps({"T": sections}), encoding="utf-8")
    layout = tmp_path / "Output" / "Sections.als"
    arranged = layout.with_name("Arranged.als")
    monkeypatch.setattr(propose_arrangement, "USE_ALIGN_ENGINE", True)
    plan = propose_arrangement.propose_arrangement(layout, section_json, arranged, dry_run=True)
    assert [(t.name, t.bpm) for t in plan.tracks] == [("T", 120)]
    assert plan.shifts == [("T", -64)]
    monkeypatch.setattr(propose_arrangement, "USE_ALIGN_ENGINE", False)
    with pytest.raises(section_format.SectionFormatError, match="Groove sections require align_engine"):
        propose_arrangement.propose_arrangement(layout, section_json, arranged, dry_run=True)
    _write_cache(tmp_path, {**_legacy(groove), "track": "Second"})
    section_json.write_text(json.dumps({"T": sections, "Second": sections}), encoding="utf-8")
    monkeypatch.setattr(propose_arrangement, "USE_ALIGN_ENGINE", True)
    with pytest.raises(section_format.SectionFormatError, match="mixed legacy and groove"):
        propose_arrangement.propose_arrangement(layout, section_json, arranged, dry_run=True)


def test_section_placement_viz_draws_groove_fill_spans_from_cached_envelopes(
        tmp_path, monkeypatch, groove):
    from matplotlib.axes import Axes

    path = _write_cache(tmp_path, groove)
    np.savez(path.with_name("T__stemenv.npz"), hop_t=0.1, mix=np.ones(1600), drums=np.ones(1600))
    spans = []
    draw_span = Axes.axvspan

    def record_span(ax, start, end, **kwargs):
        if kwargs.get("zorder") == 5:
            spans.append((start, end))
        return draw_span(ax, start, end, **kwargs)

    monkeypatch.setattr(Axes, "axvspan", record_span)
    png = section_placement_viz.render(tmp_path, tmp_path / "T.wav", groove, tmp_path)
    assert spans == [(112, 112.5), (125.5, 126)]
    assert png.read_bytes().startswith(b"\x89PNG")
