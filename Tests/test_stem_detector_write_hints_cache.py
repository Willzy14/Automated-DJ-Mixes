"""--write-hints must derive hints from the cached SECTIONS_STEM json.

Bug (2026-11-20): `stem_detector.py <project> --write-hints` re-ran detect()
without the kick model, so hints disagreed with the pipeline's kick-model
sections (hints-vs-sections gate failed) AND detect() overwrote the cached
SECTIONS_STEM json the arrangement phase reads. No audio, no GPU: detect is
monkeypatched; the overwrite guard is tested as a helper because detect()
itself needs audio.
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

import stem_detector  # noqa: E402


def _sections():
    # 128 bpm -> 1.875 s/bar. drop at bar 48, break at bar 104, outro at bar 150.
    spb = 4 * 60.0 / 128.0
    def sec(label, b0, b1):
        return {"label": label, "start_bar": b0, "end_bar": b1,
                "start_sec": b0 * spb, "end_sec": b1 * spb}
    return [sec("intro", 0, 48), sec("drop", 48, 104), sec("break", 104, 120),
            sec("drop", 120, 150), sec("outro", 150, 182)]


def _cached(kick=True):
    d = {"track": "T", "bpm": 128.0, "n_bars": 182, "sections": _sections(),
         "signals": {"fills": []}}
    if kick is not None:
        d["analysis"] = {"kick_model": kick}
    return d


def _project(tmp_path, cache=None, raw=None):
    (tmp_path / "Audio").mkdir()
    (tmp_path / "Audio" / "T.wav").write_bytes(b"")
    jp = tmp_path / "_Stem Analysis" / "SECTIONS_STEM_T.json"
    if cache is not None or raw is not None:
        jp.parent.mkdir()
        jp.write_text(raw if raw is not None else json.dumps(cache, indent=1),
                      encoding="utf-8")
    return jp


def _run_main(tmp_path, monkeypatch, extra=()):
    monkeypatch.setattr(sys, "argv", ["stem_detector.py", str(tmp_path), "--write-hints", *extra])
    stem_detector.main()
    return json.loads((tmp_path / "Hints" / "track_hints.json").read_text(encoding="utf-8"))


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_cached_json_used_detect_not_called_json_untouched(tmp_path, monkeypatch, capsys):
    cached = _cached(kick=True)
    jp = _project(tmp_path, cache=cached)
    before = _sha(jp)
    calls = []
    monkeypatch.setattr(stem_detector, "detect", lambda *a, **k: calls.append(1))
    hints = _run_main(tmp_path, monkeypatch)
    assert calls == []
    assert _sha(jp) == before
    assert hints["T.wav"] == stem_detector.hints_from_stem_result(cached)
    assert hints["T.wav"]["first_drop_sec"] == 48 * 1.875
    assert "cached analysis" in capsys.readouterr().out


def test_no_cache_falls_back_to_detect_with_warn(tmp_path, monkeypatch, capsys):
    _project(tmp_path)
    calls = []

    def fake_detect(*a, **k):
        calls.append(k)
        return _cached(kick=False)

    monkeypatch.setattr(stem_detector, "detect", fake_detect)
    hints = _run_main(tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert len(calls) == 1
    assert "no cached analysis" in out
    assert "WITHOUT the kick detector" in out
    assert "T.wav" in hints


def test_no_cache_with_kick_model_flag_has_no_kick_warning(tmp_path, monkeypatch, capsys):
    _project(tmp_path)
    monkeypatch.setattr(stem_detector, "detect", lambda *a, **k: _cached(kick=True))
    _run_main(tmp_path, monkeypatch, extra=("--kick-model",))
    out = capsys.readouterr().out
    assert "no cached analysis" in out
    assert "WITHOUT the kick detector" not in out


def test_malformed_cache_warns_and_falls_back(tmp_path, monkeypatch, capsys):
    _project(tmp_path, raw="{not json")
    calls = []
    monkeypatch.setattr(stem_detector, "detect",
                        lambda *a, **k: calls.append(1) or _cached(kick=False))
    _run_main(tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert "malformed cache" in out
    assert len(calls) == 1


def test_guard_blocks_kick_off_write_over_kick_on_json(tmp_path):
    jp = _project(tmp_path, cache=_cached(kick=True))
    assert stem_detector._may_overwrite_sections_json(jp, False) is False


def test_guard_allows_kick_on_write_over_kick_on_json(tmp_path):
    jp = _project(tmp_path, cache=_cached(kick=True))
    assert stem_detector._may_overwrite_sections_json(jp, True) is True


def test_guard_legacy_json_without_key_is_overwritten(tmp_path):
    jp = _project(tmp_path, cache=_cached(kick=None))
    assert stem_detector._may_overwrite_sections_json(jp, False) is True


def test_guard_kick_off_json_and_missing_file_are_overwritten(tmp_path):
    jp = _project(tmp_path, cache=_cached(kick=False))
    assert stem_detector._may_overwrite_sections_json(jp, False) is True
    assert stem_detector._may_overwrite_sections_json(tmp_path / "nope.json", False) is True


def test_guard_prints_warn_naming_file(tmp_path, capsys):
    jp = _project(tmp_path, cache=_cached(kick=True))
    stem_detector._may_overwrite_sections_json(jp, False)
    assert "SECTIONS_STEM_T.json" in capsys.readouterr().out
