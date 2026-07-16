"""Tests for the Rekordbox agent-health hardening.

Covers the pure logic that (a) detects a stale / version-mismatched
rekordboxAgent state — the root cause of the 2026-06-08 "Communication with
rekordboxAgent failed" pipeline stall — and (b) the hard gate that refuses to
build a mix on partial Rekordbox phrase data.
"""

import json
from pathlib import Path

import pytest

import automated_dj_mixes.desktop_analyzer as d
import automated_dj_mixes.orchestrator as o


# ---------- agent-state staleness ----------

def _write_options(path, app_ver=None, lang_path=None):
    opts = [["db-path", "X"], ["port", "30001"]]
    if lang_path is not None:
        opts.append(["lang-path", str(lang_path)])
    if app_ver is not None:
        opts.append(["app_ver", app_ver])
    path.write_text(json.dumps({"options": opts, "defaults": {}}), encoding="utf-8")


def test_rekordbox_agent_error_is_runtimeerror():
    assert issubclass(d.RekordboxAgentError, RuntimeError)


def test_read_agent_pin_parses_options_array(tmp_path, monkeypatch):
    opt = tmp_path / "options.json"
    _write_options(opt, app_ver="7.2.14", lang_path=opt)  # lang_path -> existing file
    monkeypatch.setattr(d, "RB_AGENT_OPTIONS", opt)
    app_ver, lang_path = d._read_agent_pin()
    assert app_ver == "7.2.14"
    assert lang_path == str(opt)


def test_agent_absent_is_not_stale(tmp_path, monkeypatch):
    # No options.json yet -> nothing to reset (don't churn a fresh install).
    monkeypatch.setattr(d, "RB_AGENT_OPTIONS", tmp_path / "missing.json")
    assert d._agent_state_is_stale("7.2.14") is False


def test_agent_version_mismatch_is_stale(tmp_path, monkeypatch):
    opt = tmp_path / "options.json"
    _write_options(opt, app_ver="7.0.1", lang_path=opt)
    monkeypatch.setattr(d, "RB_AGENT_OPTIONS", opt)
    assert d._agent_state_is_stale("7.2.14") is True


def test_agent_matching_version_not_stale(tmp_path, monkeypatch):
    opt = tmp_path / "options.json"
    _write_options(opt, app_ver="7.2.14", lang_path=opt)  # lang_path exists
    monkeypatch.setattr(d, "RB_AGENT_OPTIONS", opt)
    assert d._agent_state_is_stale("7.2.14") is False


def test_agent_dead_langpath_is_stale(tmp_path, monkeypatch):
    opt = tmp_path / "options.json"
    dead = tmp_path / "rekordbox 7.0.1" / "english.lang"  # never created
    _write_options(opt, app_ver="7.2.14", lang_path=dead)
    monkeypatch.setattr(d, "RB_AGENT_OPTIONS", opt)
    assert d._agent_state_is_stale("7.2.14") is True


def test_reset_agent_state_backs_up_reversibly(tmp_path, monkeypatch):
    opt = tmp_path / "options.json"
    _write_options(opt, app_ver="7.0.1")
    monkeypatch.setattr(d, "RB_AGENT_OPTIONS", opt)
    assert d._reset_agent_state() is True
    assert not opt.exists()                                   # live file moved
    assert (tmp_path / "options.json.stale-bak").exists()     # backup kept


# ---------- partial-Rekordbox hard gate ----------

class _FakeAnalysis:
    def __init__(self, path):
        self.path = Path(path)


def test_gate_raises_on_missing_rb():
    a1, a2 = _FakeAnalysis("C:/x/T1.wav"), _FakeAnalysis("C:/x/T2.wav")
    rb_matches = {str(a1.path): object()}  # only T1 has RB data
    with pytest.raises(RuntimeError, match="MISSING"):
        o.enforce_rekordbox_coverage([a1, a2], rb_matches, allow_partial_rekordbox=False)


def test_gate_allows_with_flag(capsys):
    a1, a2 = _FakeAnalysis("C:/x/T1.wav"), _FakeAnalysis("C:/x/T2.wav")
    rb_matches = {str(a1.path): object()}
    missing = o.enforce_rekordbox_coverage([a1, a2], rb_matches, allow_partial_rekordbox=True)
    assert missing == ["T2.wav"]
    assert "librosa fallback" in capsys.readouterr().out


def test_gate_passes_on_full_coverage():
    a1 = _FakeAnalysis("C:/x/T1.wav")
    rb_matches = {str(a1.path): object()}
    assert o.enforce_rekordbox_coverage([a1], rb_matches, allow_partial_rekordbox=False) == []


def test_run_pipeline_exposes_allow_partial_param():
    import inspect
    assert "allow_partial_rekordbox" in inspect.signature(o.run_pipeline).parameters


class _FakeGrid:
    def __init__(self, count):
        self.beat_times_ms = list(range(count))


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


def test_owned_mode_runs_mik_but_never_launches_rekordbox(tmp_path, monkeypatch):
    audio = tmp_path / "Audio"
    audio.mkdir()
    (audio / "Track SW V1.wav").write_bytes(b"placeholder")
    calls = []
    monkeypatch.setattr(
        d, "analyze_folder_with_mik", lambda *_args, **_kwargs: calls.append("mik")
    )
    monkeypatch.setattr(
        d, "analyze_folder_with_rekordbox",
        lambda *_args, **_kwargs: calls.append("rekordbox"),
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


# ---------- launch is context-proof (de-elevation) ----------
# rekordbox's agent fails when RB runs elevated. The launcher must keep RB at
# Medium integrity no matter where the pipeline is booted from.

def test_launch_uses_explorer_when_available(monkeypatch):
    calls = []
    monkeypatch.setattr(d, "_is_elevated", lambda: True)      # admin VS Code
    monkeypatch.setattr(d, "_explorer_available", lambda: True)
    monkeypatch.setattr(d.subprocess, "Popen", lambda args, **kw: calls.append(args))
    d._start_rb_like_manual(None, d.RB_EXE)
    assert calls and calls[0][0] == "explorer.exe"            # de-elevated launch


def test_launch_uses_shell_start_when_medium_and_no_explorer(monkeypatch):
    calls = []
    monkeypatch.setattr(d, "_is_elevated", lambda: False)     # already Medium
    monkeypatch.setattr(d, "_explorer_available", lambda: False)
    monkeypatch.setattr(d.subprocess, "Popen", lambda args, **kw: calls.append(args))
    d._start_rb_like_manual(None, d.RB_EXE)
    assert calls and calls[0][:3] == ["cmd", "/c", "start"]


def test_launch_refuses_when_elevated_and_no_explorer(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("must not launch RB elevated with no way to de-elevate")
    monkeypatch.setattr(d, "_is_elevated", lambda: True)
    monkeypatch.setattr(d, "_explorer_available", lambda: False)
    monkeypatch.setattr(d.subprocess, "Popen", _boom)
    with pytest.raises(d.RekordboxAgentError):
        d._start_rb_like_manual(None, d.RB_EXE)
