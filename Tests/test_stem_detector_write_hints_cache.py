"""--write-hints must derive hints from the cached SECTIONS_STEM json.

Bug (2026-11-20): `stem_detector.py <project> --write-hints` re-ran detect()
without the kick model, so hints disagreed with the pipeline's kick-model
sections (hints-vs-sections gate failed) AND detect() overwrote the cached
SECTIONS_STEM json the arrangement phase reads. No audio, no GPU: detect is
monkeypatched; the overwrite guard is tested as a helper because detect()
itself needs audio.
"""
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

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


def _project(tmp_path, cache=None, raw=None, stem="T"):
    (tmp_path / "Audio").mkdir(exist_ok=True)
    (tmp_path / "Audio" / f"{stem}.wav").write_bytes(b"")
    jp = tmp_path / "_Stem Analysis" / f"SECTIONS_STEM_{stem}.json"
    if cache is not None or raw is not None:
        jp.parent.mkdir(exist_ok=True)
        jp.write_text(raw if raw is not None else json.dumps(cache, indent=1),
                      encoding="utf-8")
    return jp


def _boom(*a, **k):
    raise AssertionError("detect() must never be called by --write-hints")


def _run(tmp_path, monkeypatch, extra=()):
    monkeypatch.setattr(stem_detector, "detect", _boom)
    monkeypatch.setattr(sys, "argv", ["stem_detector.py", str(tmp_path), "--write-hints", *extra])
    stem_detector.main()


def _run_ok(tmp_path, monkeypatch, extra=()):
    _run(tmp_path, monkeypatch, extra)
    return json.loads((tmp_path / "Hints" / "track_hints.json").read_text(encoding="utf-8"))


def _run_refused(tmp_path, monkeypatch, extra=()):
    with pytest.raises(SystemExit) as ei:
        _run(tmp_path, monkeypatch, extra)
    assert ei.value.code == 2


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_cached_kick_on_json_used_detect_never_called_json_untouched(tmp_path, monkeypatch, capsys):
    cached = _cached(kick=True)
    jp = _project(tmp_path, cache=cached)
    before = _sha(jp)
    hints = _run_ok(tmp_path, monkeypatch)
    assert _sha(jp) == before
    assert hints["T.wav"] == stem_detector.hints_from_stem_result(cached)
    assert hints["T.wav"]["first_drop_sec"] == 48 * 1.875


def test_missing_cache_for_one_of_two_tracks_refuses_and_writes_nothing(tmp_path, monkeypatch, capsys):
    _project(tmp_path, cache=_cached(kick=True))
    _project(tmp_path, stem="U")  # wav only, no cache
    hints_dir = tmp_path / "Hints"
    hints_dir.mkdir()
    existing = hints_dir / "track_hints.json"
    existing.write_text('{"old": 1}', encoding="utf-8")
    before = _sha(existing)
    _run_refused(tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert _sha(existing) == before
    assert "SECTIONS_STEM_U.json" in out and "Phase 1a" in out


def _bad(**over):
    d = _cached(kick=True)
    d.update(over)
    return d


BAD_SHAPES = {
    "track_mismatch": json.dumps(_bad(track="Other")),
    "empty_sections": json.dumps(_bad(sections=[])),
    "bpm_zero": json.dumps(_bad(bpm=0)),
    "bpm_negative": json.dumps(_bad(bpm=-128.0)),
    "bpm_string": json.dumps(_bad(bpm="128")),
    "signals_null": json.dumps(_bad(signals=None)),
    "top_level_array": json.dumps([1, 2, 3]),
    "invalid_json": "{not json",
    "kick_model_false": json.dumps(_cached(kick=False)),
    "legacy_no_provenance": json.dumps(_cached(kick=None)),
}


@pytest.mark.parametrize("name", sorted(BAD_SHAPES))
def test_unusable_shapes_are_refused(tmp_path, monkeypatch, capsys, name):
    _project(tmp_path, raw=BAD_SHAPES[name])
    _run_refused(tmp_path, monkeypatch)
    assert "[REFUSED]" in capsys.readouterr().out
    assert not (tmp_path / "Hints" / "track_hints.json").exists()


def test_legacy_file_with_kick_model_source_is_accepted(tmp_path, monkeypatch):
    d = _cached(kick=None)
    d["signals"]["kick_presence_source"] = "kick-detector-v3"
    _project(tmp_path, cache=d)
    assert "T.wav" in _run_ok(tmp_path, monkeypatch)


def test_legacy_file_with_energy_source_is_refused(tmp_path, monkeypatch):
    d = _cached(kick=None)
    d["signals"]["kick_presence_source"] = "stem-energy-threshold"
    _project(tmp_path, cache=d)
    _run_refused(tmp_path, monkeypatch)


def test_allow_unverified_accepts_kick_off_with_warn(tmp_path, monkeypatch, capsys):
    _project(tmp_path, cache=_cached(kick=False))
    hints = _run_ok(tmp_path, monkeypatch, extra=("--allow-unverified-cache",))
    assert "T.wav" in hints
    assert "[WARN]" in capsys.readouterr().out


def test_allow_unverified_still_refuses_track_mismatch(tmp_path, monkeypatch):
    _project(tmp_path, raw=BAD_SHAPES["track_mismatch"])
    _run_refused(tmp_path, monkeypatch, extra=("--allow-unverified-cache",))


class _EmptyProvider:
    def on_per_beat(self, wav, bpm, downbeat, n_beats):
        return []


class _FullProvider:
    def on_per_beat(self, wav, bpm, downbeat, n_beats):
        return [True] * n_beats


def test_provider_returning_nothing_is_not_marked_used():
    on, raw, used = stem_detector._model_kick_presence_per_beat(
        Path("x.wav"), 128.0, 0.0, 8, kick_provider=_EmptyProvider())
    assert used is False
    on, raw, used = stem_detector._model_kick_presence_per_beat(
        Path("x.wav"), 128.0, 0.0, 8, kick_provider=_FullProvider())
    assert used is True and bool(raw.all())


def test_non_ascii_name_in_refusal_does_not_raise_on_cp1252(tmp_path, monkeypatch):
    stem = "テスト track"
    _project(tmp_path, stem=stem)  # wav, no cache
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    _run_refused(tmp_path, monkeypatch)
    stream.flush()
    assert rb"\u30c6" in buf.getvalue()


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
