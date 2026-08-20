"""Rekordbox-removal health: the pipeline must never launch or import Rekordbox.

The RB desktop driver, library reader, enrichment loop and coverage gate were
removed 2026-08-18 (Sam: "done away with") - see Source/Archive/ and git
history. These tests pin the surviving owned-grid coverage gate, prove
run_pipeline drives MIK only, and fail if an RB import ever creeps back into
the package.
"""

import inspect
from pathlib import Path

import pytest

import automated_dj_mixes
import automated_dj_mixes.desktop_analyzer as d
import automated_dj_mixes.orchestrator as o


class _FakeAnalysis:
    def __init__(self, path):
        self.path = Path(path)


class _FakeGrid:
    def __init__(self, count):
        self.beat_times_ms = list(range(count))


# ---------- owned-grid hard gate (the only surviving coverage gate) ----------

def test_owned_grid_gate_requires_full_coverage():
    a1, a2 = _FakeAnalysis("C:/x/T1.wav"), _FakeAnalysis("C:/x/T2.wav")
    grids = {str(a1.path): _FakeGrid(32)}
    with pytest.raises(RuntimeError, match="Owned stem-grid MISSING"):
        o.enforce_owned_grid_coverage([a1, a2], grids)


def test_owned_grid_gate_passes_complete_grids():
    a1 = _FakeAnalysis("C:/x/T1.wav")
    assert o.enforce_owned_grid_coverage(
        [a1], {str(a1.path): _FakeGrid(32)}
    ) == []


# ---------- Rekordbox is gone ----------

def test_rekordbox_symbols_removed():
    assert not hasattr(o, "enforce_rekordbox_coverage")
    assert not hasattr(d, "analyze_folder_with_rekordbox")
    assert not hasattr(d, "RekordboxAgentError")


def test_no_rb_imports_in_package():
    pkg = Path(automated_dj_mixes.__file__).parent
    for py in pkg.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        assert "rekordbox_reader" not in text, py.name
        assert "rekordbox_waveform" not in text, py.name


def test_allow_partial_flag_accepted_and_ignored():
    # Compat shim: documented invocations still pass --allow-partial-rekordbox.
    assert "allow_partial_rekordbox" in inspect.signature(o.run_pipeline).parameters


def test_pipeline_runs_mik_only(tmp_path, monkeypatch):
    audio = tmp_path / "Audio"
    audio.mkdir()
    (audio / "Track SW V1.wav").write_bytes(b"placeholder")
    calls = []
    monkeypatch.setattr(
        d, "analyze_folder_with_mik", lambda *_args, **_kwargs: calls.append("mik")
    )
    monkeypatch.setattr(
        o,
        "analyse_folder",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("stop-after-desktop")
        ),
    )
    with pytest.raises(RuntimeError, match="stop-after-desktop"):
        o.run_pipeline(
            audio,
            tmp_path / "Output",
            project_root=Path(__file__).resolve().parent.parent,
            previews_only=True,
            stem_grid=True,
        )
    assert calls == ["mik"]


def test_deprecated_allow_partial_warns(tmp_path, monkeypatch, capsys):
    audio = tmp_path / "Audio"
    audio.mkdir()
    (audio / "Track SW V1.wav").write_bytes(b"placeholder")
    monkeypatch.setattr(
        o,
        "analyse_folder",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("stop-after-desktop")
        ),
    )
    with pytest.raises(RuntimeError, match="stop-after-desktop"):
        o.run_pipeline(
            audio,
            tmp_path / "Output",
            project_root=Path(__file__).resolve().parent.parent,
            previews_only=True,
            stem_grid=True,
            skip_desktop_analyze=True,
            allow_partial_rekordbox=True,
        )
    assert "deprecated" in capsys.readouterr().out
