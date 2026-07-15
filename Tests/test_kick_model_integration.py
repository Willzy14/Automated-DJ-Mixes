import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

import stem_detector


def _fake_envs(n_frames=320):
    drums = np.ones(n_frames, dtype=float)
    bass = np.zeros(n_frames, dtype=float)
    vocals = np.zeros(n_frames, dtype=float)
    other = np.ones(n_frames, dtype=float) * 0.35
    mix = np.ones(n_frames, dtype=float)
    bass[80:280] = 1.0
    return {
        "drums": drums,
        "bass": bass,
        "vocals": vocals,
        "other": other,
        "mix": mix,
    }, 0.1


def test_flag_off_fixed_input_parity_and_lazy_import(monkeypatch, tmp_path):
    monkeypatch.setattr(stem_detector, "_separate_envelopes", lambda *_args, **_kwargs: _fake_envs())
    sys.modules.pop("kick_model_adapter", None)
    sys.modules.pop("kickdet_model", None)

    wav = tmp_path / "Track.wav"
    wav.write_bytes(b"placeholder")

    first = stem_detector.detect(
        wav, tmp_path, bpm=120.0, downbeat=0.0, make_viz=False, write_json=False
    )
    second = stem_detector.detect(
        wav, tmp_path, bpm=120.0, downbeat=0.0, make_viz=False, write_json=False
    )

    assert first == second
    assert first["signals"]["kick_presence_source"] == "stem-energy-threshold"
    assert "kick_model_adapter" not in sys.modules
    assert "kickdet_model" not in sys.modules


class FakeKickProvider:
    def on_per_beat(self, wav_path, bpm, downbeat, n_beats):
        on = np.ones(n_beats, dtype=bool)
        on[16:24] = False
        return on


def test_flag_on_fake_provider_overrides_only_kick_presence(monkeypatch, tmp_path):
    monkeypatch.setattr(stem_detector, "_separate_envelopes", lambda *_args, **_kwargs: _fake_envs())
    sys.modules.pop("kick_model_adapter", None)

    wav = tmp_path / "Track.wav"
    wav.write_bytes(b"placeholder")

    res = stem_detector.detect(
        wav,
        tmp_path,
        bpm=120.0,
        downbeat=0.0,
        make_viz=False,
        write_json=False,
        kick_provider=FakeKickProvider(),
    )

    cues = [(c["type"], c["beat"]) for c in res["signals"]["kick_cues"]]
    assert ("kick_dropout", 16) in cues
    assert ("kick_return", 24) in cues
    assert res["signals"]["kick_presence_source"] == "kick-detector-v3"
    assert res["signals"]["fills"] == []
    assert "kick_model_adapter" not in sys.modules


def test_adapter_defaults_and_smoothing_do_not_import_torch():
    sys.modules.pop("torch", None)
    from kick_model_adapter import (
        MODEL_FILENAME,
        _load_presence_module,
        default_kick_detector_root,
        default_model_path,
    )

    assert default_model_path().name == MODEL_FILENAME
    presence = _load_presence_module(default_kick_detector_root())
    raw = np.array([True, False, False, True, True, False, True, True, True])
    smoothed = presence.smooth_presence(raw, fill_off_beats=2, drop_on_beats=1)
    assert smoothed.tolist() == [True, True, True, True, True, True, True, True, True]
    assert "torch" not in sys.modules


def test_missing_weights_error_is_clear(tmp_path):
    from kick_model_adapter import KickPresenceProvider

    missing = tmp_path / "missing.pt"
    with pytest.raises(FileNotFoundError) as excinfo:
        KickPresenceProvider(model_path=missing, device="cpu")

    assert str(missing) in str(excinfo.value)
    assert "--kick-model" in str(excinfo.value)


def test_orchestrator_requires_stem_sections_for_kick_model(tmp_path):
    from automated_dj_mixes.orchestrator import run_pipeline

    with pytest.raises(RuntimeError) as excinfo:
        run_pipeline(
            tmp_path / "Audio",
            tmp_path / "Output",
            project_root=Path(__file__).resolve().parent.parent,
            skip_desktop_analyze=True,
            kick_model=True,
        )

    assert "--sections-layout --stem-sections" in str(excinfo.value)
