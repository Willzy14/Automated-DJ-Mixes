import numpy as np


def test_extrapolate_restores_near_zero_head_beat():
    from audio_analysis.stem_grid import extrapolate_grid

    body = np.arange(0.494, 8.0, 0.5)
    full, added = extrapolate_grid(body, 10.0)

    assert added == 1
    assert full[0] == 0.0
    assert full[1] == 0.494


def test_extrapolate_rechecks_head_after_multiple_missing_beats():
    from audio_analysis.stem_grid import extrapolate_grid

    body = np.arange(0.994, 8.0, 0.5)
    full, added = extrapolate_grid(body, 10.0)

    assert added == 2
    assert full[0] == 0.0
    assert full[1] == 0.494
    assert full[2] == 0.994


def test_regular_file_head_overrides_confident_late_entry(monkeypatch):
    import audio_analysis.stem_grid as stem_grid

    monkeypatch.setattr(stem_grid, "_percussion_intro_phase", lambda *_args: 0)
    offset, method = stem_grid._resolve_first_downbeat_offset(
        db_grid_phase=0,
        added_before=1,
        dagree=1.0,
        dmethod="entry",
        kicks=np.array([0.5, 1.0]),
        drums=np.ones(100),
        sr=100,
        full_grid=np.array([0.0, 0.5, 1.0, 1.5]),
        period=0.5,
        mix=np.ones(100),
    )

    assert offset == 0
    assert method == "perc-intro"


def test_silent_head_keeps_confident_detected_phase(monkeypatch):
    import audio_analysis.stem_grid as stem_grid

    monkeypatch.setattr(stem_grid, "_percussion_intro_phase", lambda *_args: None)
    offset, method = stem_grid._resolve_first_downbeat_offset(
        db_grid_phase=0,
        added_before=1,
        dagree=1.0,
        dmethod="entry",
        kicks=np.array([0.5, 1.0]),
        drums=np.zeros(100),
        sr=100,
        full_grid=np.array([0.0, 0.5, 1.0, 1.5]),
        period=0.5,
        mix=np.zeros(100),
    )

    assert offset == 1
    assert method == "entry"


def test_asd_timing_path_still_loads_mix_for_head_resolution(monkeypatch):
    import audio_analysis.stem_grid as stem_grid

    sr = 100
    drums = np.ones(sr * 8)
    loaded_mix = np.arange(sr * 8, dtype=float)
    monkeypatch.setattr(stem_grid, "band_onsets", lambda *_args: np.arange(0.5, 7.5, 0.5))
    monkeypatch.setattr(stem_grid, "refine_to_click", lambda _d, _s, onsets: onsets)
    monkeypatch.setattr(stem_grid, "estimate_period", lambda _k: (0.5, 1.0))
    monkeypatch.setattr(
        stem_grid,
        "build_grid",
        lambda _k, _p: (np.arange(0.5, 7.5, 0.5), np.arange(14), np.arange(14)),
    )
    monkeypatch.setattr(stem_grid, "snap_grid_to_transients", lambda grid, _ticks: grid)
    monkeypatch.setattr(stem_grid, "grid_vs_kick", lambda *_args: 0.0)
    monkeypatch.setattr(stem_grid, "find_downbeat", lambda *_args: (0, 1.0, "entry"))

    captured = {}

    def resolve(*_args, **kwargs):
        captured["mix"] = kwargs.get("mix", _args[-1] if _args else None)
        return 0, "perc-intro"

    monkeypatch.setattr(stem_grid, "_resolve_first_downbeat_offset", resolve)

    import soundfile

    monkeypatch.setattr(soundfile, "read", lambda _path: (loaded_mix, sr))
    result = stem_grid.detect_beat_grid(
        "boundary.wav",
        drums=drums,
        sr=sr,
        asd_ticks=np.arange(0.5, 7.5, 0.5),
    )

    assert captured["mix"] is loaded_mix
    assert result.first_downbeat_offset == 0
    assert result.snapped_to_asd is True
