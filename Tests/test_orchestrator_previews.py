import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from automated_dj_mixes import orchestrator


def test_no_previews_skips_rendering_and_continues(tmp_path, monkeypatch, capsys):
    class ReachedGainStage(Exception):
        pass

    audio = tmp_path / "Audio"
    output = tmp_path / "Output"
    analysis = SimpleNamespace(path=audio / "Track SW V1.wav", bpm=128, key="Am",
                               camelot="8A", lufs=-8, analysis_source="tags", warnings=[])
    render = Mock(return_value=1)
    monkeypatch.setattr(orchestrator, "_render_previews", render)
    monkeypatch.setattr(orchestrator, "analyse_folder", lambda _: [analysis])
    monkeypatch.setattr(orchestrator, "enforce_owned_grid_coverage", Mock())
    monkeypatch.setattr(orchestrator, "enrich_from_mik", lambda _: SimpleNamespace(
        cues=[], key=None, bpm=None))
    monkeypatch.setattr(orchestrator, "calculate_gain_offsets", Mock(side_effect=ReachedGainStage))
    argv = ["orchestrator", "--input", str(audio), "--output", str(output),
            "--skip-desktop-analyze", "--no-sections-layout", "--no-hints-required"]

    monkeypatch.setattr(sys, "argv", [*argv, "--no-previews"])
    with pytest.raises(ReachedGainStage):
        orchestrator.main()
    render.assert_not_called()
    skip_line = "Track previews skipped (--no-previews)."
    assert capsys.readouterr().out.splitlines().count(skip_line) == 1

    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(ReachedGainStage):
        orchestrator.main()
    render.assert_called_once()
    assert skip_line not in capsys.readouterr().out


def test_conflicting_preview_flags_are_rejected_before_starting_pipeline(monkeypatch, capsys):
    pipeline = Mock()
    monkeypatch.setattr(orchestrator, "run_pipeline", pipeline)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "--input", "Audio", "--output", "Output",
                                     "--previews-only", "--no-previews"])

    with pytest.raises(SystemExit) as exc:
        orchestrator.main()

    assert exc.value.code == 2
    pipeline.assert_not_called()
    captured = capsys.readouterr()
    assert not captured.out
    assert "argument --no-previews: not allowed with argument --previews-only" in captured.err


def test_run_pipeline_rejects_conflicting_preview_flags_before_loading_config(tmp_path, monkeypatch):
    load_config = Mock()
    monkeypatch.setattr(orchestrator, "load_config", load_config)

    with pytest.raises(ValueError, match="--previews-only and --no-previews cannot be used together"):
        orchestrator.run_pipeline(tmp_path / "Audio", tmp_path / "Output",
                                  previews_only=True, no_previews=True)

    load_config.assert_not_called()
