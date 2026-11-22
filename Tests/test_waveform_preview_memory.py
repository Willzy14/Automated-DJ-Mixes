"""The preview renderer must not hold on to the picture it drew.

Sam's Home PC (16 GB, also runs his trading desks) froze on 2026-11-21 when
the mix pipeline ran out of memory. One cause was the track previews: the old
waveform_preview.py built every picture with pyplot, and plt.close(fig) only
removed it from pyplot's global figure list. The figure stayed alive in a
reference cycle, and with it the Agg renderer that rasterised the 1.2 M point
fill_between polygon - about 1.2 GB per preview on the Home PC (measured
2026-11-22: private memory 482 MB -> 1654 MB after one render, -> 2834 MB
after the second).

These tests use synthetic audio only (no real tracks) and check two things:
  a. the figure is dead when render_preview returns, with the automatic
     cyclic collector switched off, so the memory goes back without any help
     from the caller;
  b. the PNG is still written under the same file name.

The fix was also shown to give the same picture, pixel for pixel, as the old
pyplot code (synthetic and real tracks, 2026-11-22). That check is not kept as
a test: it would compare against a frozen copy of the old code and fail on any
deliberate change to the picture.
"""
import gc
import sys
import types
import wave
import weakref
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Source"))

import matplotlib.figure  # noqa: E402


def _ensure_analysis_importable():
    """automated_dj_mixes.analysis re-exports the sibling Audio Analysis Toolkit.

    The preview module imports it for the TrackAnalysis type only, so a stub
    stands in when the sibling repo is not on the path (these tests never
    build a real TrackAnalysis).
    """
    try:
        import audio_analysis.track_analysis  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    stub = types.ModuleType("audio_analysis")
    track = types.ModuleType("audio_analysis.track_analysis")
    for name in ("analyse_track", "analyse_folder", "_read_tags", "_detect_downbeat",
                 "_refine_attack", "_measure_lufs", "_detect_sections",
                 "_detect_last_kick", "_detect_bass_section",
                 "_detect_first_break_phrase_aware"):
        setattr(track, name, lambda *args, **kwargs: None)
    track.TrackAnalysis = type("TrackAnalysis", (), {})
    track.AUDIO_EXTENSIONS = (".wav",)
    stub.track_analysis = track
    sys.modules["audio_analysis"] = stub
    sys.modules["audio_analysis.track_analysis"] = track


_ensure_analysis_importable()

from automated_dj_mixes import waveform_preview  # noqa: E402


def _write_test_wav(path: Path, seconds: float = 20.0, sr: int = 44100) -> Path:
    """A synthetic stereo noise track (no real audio in the repo)."""
    rng = np.random.default_rng(20261122)
    frames = rng.standard_normal((int(seconds * sr), 2)).astype(np.float32) * 0.2
    pcm = (frames * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())
    return path


def _preview_context(module, wav_path: Path, track_index: int = 1):
    """A context the way the orchestrator builds it."""
    analysis = SimpleNamespace(path=Path(wav_path), bpm=128.0, key="Am",
                                camelot="8A", lufs=-8.0)
    energy = [
        SimpleNamespace(start_sec=0.0, end_sec=8.0, energy=2),
        SimpleNamespace(start_sec=8.0, end_sec=15.0, energy=7),
        SimpleNamespace(start_sec=15.0, end_sec=20.0, energy=9),
    ]
    return module.PreviewContext(
        track_index=track_index,
        analysis=analysis,
        mik_cues_sec=[3.0, 11.5],
        mik_energy_segments=energy,
    )


def test_the_figure_is_dead_when_render_preview_returns(tmp_path, monkeypatch):
    """The leak itself: no caller help, no waiting for the collector.

    On the old code the closed figure stays alive in a reference cycle, so
    this fails there and passes on the fixed code.
    """
    wav = _write_test_wav(tmp_path / "Test Track 01.wav")
    saved = []
    original_savefig = matplotlib.figure.Figure.savefig

    def recording_savefig(self, *args, **kwargs):
        # A weak reference only: the list must not keep the figure alive.
        saved.append(weakref.ref(self))
        return original_savefig(self, *args, **kwargs)

    monkeypatch.setattr(matplotlib.figure.Figure, "savefig", recording_savefig)

    ctx = _preview_context(waveform_preview, wav)
    gc.disable()
    try:
        out_path = waveform_preview.render_preview(ctx, tmp_path / "Previews")
    finally:
        gc.enable()

    assert saved, "render_preview never called Figure.savefig"
    assert out_path.exists()
    assert saved[0]() is None, (
        "the figure is still alive after render_preview returned: a closed "
        "figure stays in a reference cycle and holds the Agg raster of the "
        "waveform polygon with it (about 1.2 GB per preview on the Home PC)")


def test_the_png_keeps_its_file_name(tmp_path):
    """Same input, same output file name as before the fix."""
    wav = _write_test_wav(tmp_path / "Test Track 01.wav")
    out_path = waveform_preview.render_preview(
        _preview_context(waveform_preview, wav), tmp_path / "Previews")

    assert out_path.name == "Preview_01_Test_Track_01.png"
    assert out_path.exists()
    assert out_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
