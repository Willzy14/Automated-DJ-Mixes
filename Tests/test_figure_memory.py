"""The picture a renderer drew must be gone when the renderer returns.

Sam's Home PC (16 GB, also runs his trading desks) froze on 2026-11-21 when
the mix pipeline ran out of memory. One cause was the renderers that draw with
pyplot: plt.close(fig) only takes the figure off pyplot's global figure list,
so the figure stays alive in a reference cycle (figure <-> canvas, figure <->
axes) with the Agg raster of the picture on the canvas, until Python's cyclic
garbage collector happens to run.

Measured 2026-11-23 on the Home PC (synthetic 5-minute inputs, four calls per
renderer in one process with the automatic collector switched off): every
renderer of the fix kept its figure alive after it returned, private memory
grew by 3 MB to 127 MB per call, and one gc.collect() freed 10 MB to 407 MB.

The fix is automated_dj_mixes.figure_memory: frees_figures runs gc.collect()
when the wrapped function returns. These tests use synthetic data only (no
real tracks) and check:
  a. the decorator frees the picture - and the same check FAILS on the old,
     undecorated code, so the test can fail;
  b. every renderer of the fix carries the decorator;
  c. two real renderers, end to end;
  d. one loop renderer frees each picture before the next one is drawn.

The picture itself is not compared here: the fix changes no drawing code, and
a comparison against a frozen copy of the old code would fail on any
deliberate change to the picture.
"""
import gc
import importlib
import sys
import wave
import weakref
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Source"))

import matplotlib  # noqa: E402

# The picture checks need a backend that does not need a screen.
matplotlib.use("Agg")

import matplotlib.figure  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

import extract_musical_landmarks  # noqa: E402
import section_placement_viz  # noqa: E402
import sections_compare_viz  # noqa: E402
from automated_dj_mixes.figure_memory import frees_figures  # noqa: E402


# Every renderer the fix touches, and the module it lives in.
RENDERERS = [
    ("stem_detector", "_visualize"),
    ("align_engine", "visualize_transition"),
    ("transition_review_viz", "render_transition"),
    ("transition_review_viz", "render_transition_full_context"),
    ("loop_review_viz", "render_loop"),
    ("section_placement_viz", "render"),
    ("sections_blind_viz", "render_blind_quarters"),
    ("sections_compare_viz", "render_track_quarters"),
    ("stem_section_probe", "probe_track"),
    ("automated_dj_mixes.stem_grid", "main_viz"),
    ("extract_musical_landmarks", "_render"),
]


def _record_saved_figures(monkeypatch):
    """Record a weak reference to every figure that is saved.

    Only weak references: the list must not keep a picture alive itself.
    Returns (saved, alive_at_save), where alive_at_save[i] is the number of
    the already-saved figures that were still alive when figure i was saved.
    """
    saved = []
    alive_at_save = []
    original_savefig = matplotlib.figure.Figure.savefig

    def recording_savefig(self, *args, **kwargs):
        alive_at_save.append(sum(1 for ref in saved if ref() is not None))
        saved.append(weakref.ref(self))
        return original_savefig(self, *args, **kwargs)

    monkeypatch.setattr(matplotlib.figure.Figure, "savefig", recording_savefig)
    return saved, alive_at_save


def _write_test_wav(path: Path, seconds: float = 12.0, sr: int = 44100) -> Path:
    """A synthetic stereo noise track (no real audio in the repo)."""
    rng = np.random.default_rng(20261123)
    frames = rng.standard_normal((int(seconds * sr), 2)).astype(np.float32) * 0.2
    pcm = (frames * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())
    return path


def _landmark_result():
    """A hand-built landmark result, the shape _render reads."""
    return {
        "track": "Test Track",
        "n_bars": 64,
        "sections": [
            {"name": "Intro", "label": "intro", "start_bar": 0, "end_bar": 16},
            {"name": "Drop", "label": "drop", "start_bar": 16, "end_bar": 48},
            {"name": "Outro", "label": "outro", "start_bar": 48, "end_bar": 64},
        ],
        "signals": {
            "musical_landmarks": [
                {"type": "pre_drop_kick_gap", "start_bar": 14, "end_bar": 16,
                 "duration_beats": 8},
            ],
        },
    }


def _groove_data():
    """The stem sections the pipeline writes, built like the readers build them."""
    def section(label, start, end):
        return {"label": label, "start_bar": start, "end_bar": end,
                "start_sec": start * 2, "end_sec": end * 2}

    return {
        "track": "T", "bpm": 120, "n_bars": 80,
        "sections": [section("intro", 0, 8), section("intro", 8, 16),
                     section("drop", 16, 32), section("break", 32, 40),
                     section("drop", 40, 64), section("outro", 64, 72),
                     section("outro", 72, 80)],
        "signals": {
            "fills": [[112, 112.51]],
            "fill_sections": [section("fill", 56, 56.25), section("fill", 62.75, 63)],
            "bass_in": 32.0, "bass_out": 128.0, "loop_windows": [[0, 32, 0, 16]],
        },
        "analysis": {"kick_model": True, "section_detector": "groove-1",
                     "sections_schema": 2,
                     "bar_one_suspect": {"suspect": True, "offset_beats": 2}},
    }


def _write_envelope_cache(project: Path, stem: str = "T") -> Path:
    """The cached stem envelopes the placement viz reads (no Demucs here)."""
    directory = project / "_Stem Analysis"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}__stemenv.npz"
    np.savez(path, hop_t=0.1, mix=np.ones(1600), drums=np.ones(1600))
    return path


# ---------------------------------------------------------------- a. the decorator


def test_the_decorator_frees_the_picture(tmp_path, monkeypatch):
    """The picture is gone when the decorated call returns.

    The undecorated call is the old code: the closed picture stays alive in a
    reference cycle, so this check is able to fail.
    """
    saved, _ = _record_saved_figures(monkeypatch)

    def draw(path):
        fig, ax = plt.subplots(figsize=(2, 2))
        ax.plot([0, 1], [0, 1])
        fig.savefig(path)
        plt.close(fig)
        return "drawn"

    # The old code: nothing frees the picture. The check stays inside the
    # disabled window: with the automatic collector on, a collection could
    # run between the call and the check and hide the leak.
    gc.disable()
    try:
        assert draw(tmp_path / "old.png") == "drawn"
        assert saved, "no picture was saved"
        assert saved[-1]() is not None, (
            "the picture died even without the decorator: this test cannot "
            "fail on the old code, so it proves nothing")
    finally:
        gc.enable()

    # The fixed code: the decorator collects the cycle before it returns.
    decorated = frees_figures(draw)
    gc.disable()
    try:
        assert decorated(tmp_path / "new.png") == "drawn"
        assert saved[-1]() is None, (
            "the picture is still alive after the decorated call returned")
    finally:
        gc.enable()


def test_the_decorator_passes_the_return_value_and_the_exception_through():
    """The decorator wraps the call; it does not swallow anything."""
    @frees_figures
    def returns_a_value():
        return {"drawn": True}

    @frees_figures
    def raises():
        raise ValueError("boom")

    assert returns_a_value() == {"drawn": True}
    with pytest.raises(ValueError, match="boom"):
        raises()


# ------------------------------------------------------------ b. every renderer


@pytest.mark.parametrize("module_name, function_name", RENDERERS)
def test_every_renderer_carries_the_decorator(module_name, function_name):
    """Every renderer of the fix is decorated.

    A module that cannot be imported fails the test with its import error:
    there is no silent skip.
    """
    module = importlib.import_module(module_name)
    function = getattr(module, function_name)
    assert getattr(function, "frees_figures", False) is True, (
        f"{module_name}.{function_name} does not free its picture")


# --------------------------------------------------------- c. two real renderers


def test_the_landmark_picture_is_dead_when_render_returns(tmp_path, monkeypatch):
    """extract_musical_landmarks._render frees the picture it drew."""
    saved, _ = _record_saved_figures(monkeypatch)

    # The check stays inside the disabled window: with the automatic
    # collector on, a collection could run between the call and the check
    # and hide the leak.
    gc.disable()
    try:
        extract_musical_landmarks._render(tmp_path / "LANDMARKS_T.png",
                                          _landmark_result())
        assert saved, "no picture was saved"
        assert saved[0]() is None, (
            "the picture is still alive after _render returned: a closed "
            "pyplot figure stays alive in a reference cycle and holds the Agg "
            "raster of the picture with it")
    finally:
        gc.enable()

    assert (tmp_path / "LANDMARKS_T.png").exists()


def test_the_placement_picture_is_dead_when_render_returns(tmp_path, monkeypatch):
    """section_placement_viz.render frees the picture it drew."""
    _write_envelope_cache(tmp_path)
    saved, _ = _record_saved_figures(monkeypatch)

    # The check stays inside the disabled window: with the automatic
    # collector on, a collection could run between the call and the check
    # and hide the leak.
    gc.disable()
    try:
        out = section_placement_viz.render(tmp_path, tmp_path / "T.wav",
                                           _groove_data(), tmp_path)
        assert saved, "no picture was saved"
        assert saved[0]() is None, (
            "the picture is still alive after render returned: a closed "
            "pyplot figure stays alive in a reference cycle and holds the Agg "
            "raster of the picture with it")
    finally:
        gc.enable()

    assert out.exists()


# ------------------------------------------------------------- d. a loop renderer


def test_a_loop_renderer_frees_every_picture_before_the_next_one_is_drawn(
        tmp_path, monkeypatch):
    """One call draws several pictures: the earlier ones must be gone.

    The check is made at the moment each picture is saved, so it sees the
    state of the earlier pictures at that time, not at the end of the call.
    """
    wav = _write_test_wav(tmp_path / "Test Track.wav")
    saved, alive_at_save = _record_saved_figures(monkeypatch)
    clips = [
        {"name": "Intro", "source_start_beats": 0, "source_end_beats": 32},
        {"name": "Drop", "source_start_beats": 32, "source_end_beats": 64},
    ]

    gc.disable()
    try:
        sections_compare_viz.render_track_quarters(
            "Test Track", wav, 128.0, 0.5, clips, clips, tmp_path, quarters=2)
        assert len(saved) == 2, f"expected two pictures, got {len(saved)}"
        assert alive_at_save == [0, 0], (
            "a picture of the same call was still alive when the next one was "
            f"saved: {alive_at_save}")
        assert all(ref() is None for ref in saved), (
            "a picture of the call was still alive after the call returned")
    finally:
        gc.enable()
