"""The preview renderer must not hold on to the picture it drew.

Sam's Home PC (16 GB, also runs his trading desks) froze on 2026-11-21 when
the mix pipeline ran out of memory. One cause was the track previews: the old
waveform_preview.py built every picture with pyplot, and plt.close(fig) only
removed it from pyplot's global figure list. The figure stayed alive in a
reference cycle, and with it the Agg renderer that rasterised the 1.2 M point
fill_between polygon - about 1.2 GB per preview on the Home PC (measured
2026-11-22: private memory 482 MB -> 1654 MB after one render, -> 2834 MB
after the second).

These tests use synthetic audio only (no real tracks) and they check three
things:
  a. the figure is dead when render_preview returns, with the automatic
     cyclic collector switched off, so the memory goes back without any help
     from the caller;
  b. the PNG is written under the same file name as the old code gives;
  c. the picture is the same as the old code's picture, pixel for pixel.

The old implementation is read out of git (main) and imported under another
name, so the same tests run against the old code and the new one.
"""
import gc
import importlib.util
import subprocess
import sys
import types
import wave
import weakref
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Source"))

import matplotlib.figure  # noqa: E402
import matplotlib.image  # noqa: E402


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


# The git refs that hold the old (pyplot) implementation of the module.
OLD_REFS = ("main", "origin/main")


def _old_module_source() -> str:
    """The old implementation, straight out of git."""
    for ref in OLD_REFS:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "show",
             f"{ref}:Source/automated_dj_mixes/waveform_preview.py"],
            capture_output=True)
        if result.returncode == 0:
            return result.stdout.decode("utf-8")
    raise RuntimeError(
        "cannot read the old waveform_preview.py out of git (tried "
        + ", ".join(OLD_REFS) + "); the picture comparison needs it")


@pytest.fixture(scope="module")
def old_module(tmp_path_factory):
    """main's module, imported under another name."""
    source = _old_module_source()
    assert "matplotlib.pyplot" in source, (
        "the reference module is not the old pyplot implementation any more, "
        "so the picture comparison would compare the new code with itself")
    path = tmp_path_factory.mktemp("old_module") / "old_waveform_preview.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("old_waveform_preview", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["old_waveform_preview"] = module
    spec.loader.exec_module(module)
    yield module
    # The old code only closed the figure: it carried the Agg raster of the
    # waveform polygon until the cyclic collector ran. Collect it here so the
    # test process does not keep it around.
    sys.modules.pop("old_waveform_preview", None)
    gc.collect()


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


def test_the_png_is_written_with_the_same_name_as_the_old_code(tmp_path, old_module):
    """Same input, same output file name."""
    wav = _write_test_wav(tmp_path / "Test Track 01.wav")
    new_path = waveform_preview.render_preview(
        _preview_context(waveform_preview, wav), tmp_path / "new")
    old_path = old_module.render_preview(
        _preview_context(old_module, wav), tmp_path / "old")

    assert new_path.name == old_path.name
    assert new_path.name == "Preview_01_Test_Track_01.png"
    assert new_path.exists()
    assert new_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert old_path.exists()


def test_the_picture_is_the_same_as_the_old_code(tmp_path, old_module):
    """Same picture, pixel for pixel (Sam's rule: the pictures come out the same)."""
    wav = _write_test_wav(tmp_path / "Test Track 01.wav")
    new_png = waveform_preview.render_preview(
        _preview_context(waveform_preview, wav), tmp_path / "new")
    old_png = old_module.render_preview(
        _preview_context(old_module, wav), tmp_path / "old")

    new_image = matplotlib.image.imread(new_png)
    old_image = matplotlib.image.imread(old_png)
    assert new_image.shape == old_image.shape, (
        f"the picture changed shape: {new_image.shape} vs {old_image.shape}")
    difference = np.abs(new_image.astype(np.float64) - old_image.astype(np.float64))
    assert difference.max() == 0.0, (
        f"the picture changed: the maximum pixel difference is {difference.max()}")
