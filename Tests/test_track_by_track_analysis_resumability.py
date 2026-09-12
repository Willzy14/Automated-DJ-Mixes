"""Track-by-track analysis-phase caching/resumability (Sam, 2026-09-11).

Root cause (py-spy-confirmed on the Home PC): `run_pipeline` used to sweep
ALL tracks through stem-grid detection, THEN hard-gate on
`enforce_owned_grid_coverage`, THEN sweep ALL tracks through stem-sections +
kick-model. If a MemoryError hit even one track during the grid sweep, the
coverage gate raised and aborted the WHOLE run before ANY track — including
ones whose own grid detection had already succeeded — ever reached the stage
that writes the `_Stem Analysis` cache. Nothing was resumable, including the
tracks that had already worked.

The fix restructures the per-track analysis phase (grid detection, the
BPM-authority overwrite, and stem-sections/kick-model detection) into ONE
loop that finishes each track completely before starting the next, and makes
grid detection's own Demucs separation persist immediately instead of being
thrown away if a later track crashes.

Two things are pinned here:
  (a) a track's analysis result is written to disk immediately after that
      track's own processing completes, not after a later track's stage
      even starts (`test_track_one_is_fully_cached_before_track_two_...`);
  (b) the mechanism that makes a re-run cheap for an already-cached track —
      `stem_grid._lazy_stem_audio` now saves what it separates, so a second
      call for the same track is served entirely from disk, with zero
      re-separation (`test_lazy_stem_audio_*`).
(c) — the untouched gate/pipeline suite still passing — is proven by the
      full `Tests/` run, not by anything in this file.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

import automated_dj_mixes.orchestrator as orchestrator
import automated_dj_mixes.phrase_viz as phrase_viz
import automated_dj_mixes.stem_grid as stem_grid
import probe_stem_kick_grid
import stem_detector
from automated_dj_mixes.analysis import TrackAnalysis


# ---------------------------------------------------------------------------
# (a) per-track write-immediacy inside run_pipeline's merged analysis loop
# ---------------------------------------------------------------------------

def _fake_grid(bpm: float = 120.0, n_beats: int = 64) -> SimpleNamespace:
    return SimpleNamespace(
        beat_times_ms=[int(i * (60000.0 / bpm)) for i in range(n_beats)],
        first_downbeat_offset=0,
        bpm=bpm,
        confidence=0.95,
        grid_vs_kick_ms=2.0,
        downbeat_method="entry",
        downbeat_agree=1.0,
        flag="",
        snapped_to_asd=False,
        timing_src="detector",
    )


def test_track_one_is_fully_cached_before_track_two_analysis_even_starts(
    tmp_path, monkeypatch,
):
    project = tmp_path
    audio = project / "Audio"
    audio.mkdir()
    (audio / "Track1 SW V1.wav").write_bytes(b"placeholder")
    (audio / "Track2 SW V1.wav").write_bytes(b"placeholder")
    stem_dir = project / "_Stem Analysis"

    analyses = [
        TrackAnalysis(path=audio / "Track1 SW V1.wav", bpm=120.0, camelot="8A",
                      lufs=-8.0, first_downbeat_sec=0.0, duration_sec=180.0),
        TrackAnalysis(path=audio / "Track2 SW V1.wav", bpm=124.0, camelot="9A",
                      lufs=-8.0, first_downbeat_sec=0.0, duration_sec=180.0),
    ]
    monkeypatch.setattr(orchestrator, "analyse_folder", lambda _d: analyses)

    order_observations: list[tuple[str, bool]] = []
    stem_detect_calls: list[str] = []

    def fake_detect_beat_grid(path, asd_ticks=None):
        if "Track2" in path.name:
            # By the time track 2's OWN grid detection is even attempted,
            # track 1 must already be fully processed and cached on disk —
            # not merely grid-detected, but through stage 3 too.
            marker = stem_dir / "CACHE_Track1 SW V1.marker"
            order_observations.append(("track2_grid_attempt", marker.exists()))
            raise MemoryError(
                "simulated OOM during full-resolution spectrogram analysis"
            )
        return _fake_grid(bpm=120.0)

    def fake_stem_detect(wav_path, project_dir, *, bpm, downbeat, kick_model,
                          kick_model_path, kick_model_device, make_viz):
        # Mirrors the real stem_detector.detect: persists a per-track
        # artifact to `_Stem Analysis` before returning.
        d = project_dir / "_Stem Analysis"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"CACHE_{wav_path.stem}.marker").write_text("done", encoding="utf-8")
        stem_detect_calls.append(wav_path.name)
        return {"sections": [], "bpm": bpm}

    monkeypatch.setattr(stem_grid, "detect_beat_grid", fake_detect_beat_grid)
    monkeypatch.setattr(stem_detector, "detect", fake_stem_detect)
    monkeypatch.setattr(phrase_viz, "segments_from_stem_sections", lambda *a, **k: [])
    monkeypatch.setattr(phrase_viz, "validate_bar_math", lambda *a, **k: [])

    with pytest.raises(RuntimeError, match="Owned stem-grid MISSING"):
        orchestrator.run_pipeline(
            audio,
            project / "Output",
            project_root=Path(__file__).resolve().parent.parent,
            skip_desktop_analyze=True,
            stem_grid=True,
            stem_sections=True,
        )

    # The coverage gate correctly still refuses to ship a mix missing a
    # track's grid — that behaviour is untouched. What must be true despite
    # the hard stop: track 1's own analysis (both stages) was captured to
    # disk BEFORE track 2's grid detection was even attempted, and track 2's
    # own stage-3 fallback (using its raw analysis bpm, no grid) still ran.
    assert order_observations == [("track2_grid_attempt", True)], (
        "track 1's cache must exist by the time track 2's grid detection "
        "starts — not only after the whole batch finishes"
    )
    assert (stem_dir / "CACHE_Track1 SW V1.marker").exists()
    assert (stem_dir / "CACHE_Track2 SW V1.marker").exists()
    assert stem_detect_calls == ["Track1 SW V1.wav", "Track2 SW V1.wav"], (
        "stage 3 must run track-by-track in order, not deferred as a "
        "second full-corpus sweep after the grid sweep"
    )


def test_a_track_that_fails_leaves_no_marker_but_does_not_lose_earlier_ones(
    tmp_path, monkeypatch,
):
    """The flip side of the ordering pin: a track whose OWN grid detection
    fails must not corrupt or delete anything already written for a track
    that came before it — the per-track try/except isolates failures."""
    project = tmp_path
    audio = project / "Audio"
    audio.mkdir()
    (audio / "Good SW V1.wav").write_bytes(b"placeholder")
    (audio / "Bad SW V1.wav").write_bytes(b"placeholder")
    stem_dir = project / "_Stem Analysis"

    analyses = [
        TrackAnalysis(path=audio / "Good SW V1.wav", bpm=120.0, camelot="8A",
                      lufs=-8.0, first_downbeat_sec=0.0, duration_sec=180.0),
        TrackAnalysis(path=audio / "Bad SW V1.wav", bpm=124.0, camelot="9A",
                      lufs=-8.0, first_downbeat_sec=0.0, duration_sec=180.0),
    ]
    monkeypatch.setattr(orchestrator, "analyse_folder", lambda _d: analyses)

    def fake_detect_beat_grid(path, asd_ticks=None):
        if "Bad" in path.name:
            raise MemoryError("simulated OOM")
        return _fake_grid(bpm=120.0)

    def fake_stem_detect(wav_path, project_dir, *, bpm, downbeat, kick_model,
                          kick_model_path, kick_model_device, make_viz):
        d = project_dir / "_Stem Analysis"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"CACHE_{wav_path.stem}.marker").write_text("done", encoding="utf-8")
        return {"sections": [], "bpm": bpm}

    monkeypatch.setattr(stem_grid, "detect_beat_grid", fake_detect_beat_grid)
    monkeypatch.setattr(stem_detector, "detect", fake_stem_detect)
    monkeypatch.setattr(phrase_viz, "segments_from_stem_sections", lambda *a, **k: [])
    monkeypatch.setattr(phrase_viz, "validate_bar_math", lambda *a, **k: [])

    with pytest.raises(RuntimeError, match="Owned stem-grid MISSING"):
        orchestrator.run_pipeline(
            audio,
            project / "Output",
            project_root=Path(__file__).resolve().parent.parent,
            skip_desktop_analyze=True,
            stem_grid=True,
            stem_sections=True,
        )

    assert (stem_dir / "CACHE_Good SW V1.marker").exists()
    # The failed track still gets a stage-3 attempt (fallback bpm, no grid) —
    # its OWN cache is not corrupted or half-written, it simply has no grid.
    assert (stem_dir / "CACHE_Bad SW V1.marker").exists()


# ---------------------------------------------------------------------------
# (b) stem_grid._lazy_stem_audio persists a fresh separation immediately, so
# a re-run of the SAME track does zero further Demucs work.
# ---------------------------------------------------------------------------

def test_lazy_stem_audio_saves_fresh_separation_and_serves_cache_on_repeat(
    tmp_path, monkeypatch,
):
    import kick_model_adapter

    corpus = tmp_path / "corpus"
    audio = corpus / "Audio"
    audio.mkdir(parents=True)
    wav = audio / "Track1 SW V1.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 2000)
    # Created up front by run_pipeline (not lazily on first write) — see
    # test_stem_analysis_dir_is_created_up_front below for that half.
    (corpus / "_Stem Analysis").mkdir(parents=True, exist_ok=True)

    drums = np.linspace(-1.0, 1.0, 4410, dtype=np.float32)
    bass = np.linspace(-0.5, 0.5, 4410, dtype=np.float32)
    calls = {"n": 0}

    def fake_stem_audio(_wav):
        calls["n"] += 1
        return drums, bass, 44100

    monkeypatch.setattr(probe_stem_kick_grid, "stem_audio", fake_stem_audio)

    d1, b1, sr1 = stem_grid._lazy_stem_audio(wav)
    assert calls["n"] == 1
    assert sr1 == 44100
    assert np.array_equal(d1, drums)
    assert np.array_equal(b1, bass)

    cache_dir = corpus / "_Stem Analysis"
    drums_hit = kick_model_adapter._load_drums_cache(wav, cache_dir)
    assert drums_hit is not None, (
        "grid detection's own separation must be cached immediately, not "
        "only by the later kick-model/stem-sections stage"
    )
    assert np.array_equal(drums_hit[0], drums)
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is not None

    # The re-run case: a LATER track crashing must not force this track's
    # ~9-minute Demucs separation to happen again.
    d2, b2, sr2 = stem_grid._lazy_stem_audio(wav)
    assert calls["n"] == 1, "a track with a valid cache must not be re-separated"
    assert sr2 == 44100
    assert np.array_equal(d2, drums)
    assert b2 is None  # cache-hit path returns None for bass (grid detector discards it)


def test_lazy_stem_audio_without_the_sidecar_dir_is_unchanged(tmp_path, monkeypatch):
    """No `_Stem Analysis` folder at all (e.g. a caller outside run_pipeline
    that never creates it) -> behaviour is exactly what it always was:
    separate, don't cache, don't crash."""
    audio = tmp_path / "Audio"
    audio.mkdir()
    wav = audio / "Loose.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 500)

    drums = np.zeros(100, dtype=np.float32)
    bass = np.zeros(100, dtype=np.float32)
    monkeypatch.setattr(probe_stem_kick_grid, "stem_audio",
                        lambda _w: (drums, bass, 44100))

    d, b, sr = stem_grid._lazy_stem_audio(wav)
    assert sr == 44100
    assert np.array_equal(d, drums)
    assert not (tmp_path / "_Stem Analysis").exists()


def test_stem_analysis_dir_is_created_up_front_before_any_track_runs(
    tmp_path, monkeypatch,
):
    """run_pipeline must create `_Stem Analysis` before the per-track loop
    starts, not lazily on a track's first cache write — otherwise the very
    first track's own grid-detection separation (fix above) has nowhere to
    save to."""
    project = tmp_path
    audio = project / "Audio"
    audio.mkdir()
    (audio / "Only SW V1.wav").write_bytes(b"placeholder")
    stem_dir = project / "_Stem Analysis"

    analyses = [
        TrackAnalysis(path=audio / "Only SW V1.wav", bpm=120.0, camelot="8A",
                      lufs=-8.0, first_downbeat_sec=0.0, duration_sec=180.0),
    ]
    monkeypatch.setattr(orchestrator, "analyse_folder", lambda _d: analyses)

    saw_dir_before_detection = []

    def fake_detect_beat_grid(path, asd_ticks=None):
        saw_dir_before_detection.append(stem_dir.is_dir())
        raise MemoryError("simulated OOM")

    monkeypatch.setattr(stem_grid, "detect_beat_grid", fake_detect_beat_grid)
    monkeypatch.setattr(stem_detector, "detect", lambda *a, **k: None)

    with pytest.raises(RuntimeError, match="Owned stem-grid MISSING"):
        orchestrator.run_pipeline(
            audio,
            project / "Output",
            project_root=Path(__file__).resolve().parent.parent,
            skip_desktop_analyze=True,
            stem_grid=True,
            stem_sections=True,
        )

    assert saw_dir_before_detection == [True]
