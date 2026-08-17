"""musical_end_sec finds the last moment of real musical content, not the
file's end (which trails into silence / reverb decay).

The first attempt at this used a body-relative threshold and got it badly
wrong on BUTCH: a kick dropping out is roughly a 9 dB step, so the body-
relative method stopped at the last kick and reported 17.6 s of "tail" on
a track whose kick-less drum outro IS music. The peak-relative method
looks for the energy CLIFF (the -22 -> -62 dBFS step inside one second
that real endings produce) and survives a 9 dB kick dropout cleanly.

Tests skip the audio-dependent fixtures cleanly when 14.08.26/Audio is
absent. The synthetic / arithmetic tests always run.
"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))
from musical_end import musical_end_sec, musical_end_bars  # noqa: E402

AUDIO_DIR = ROOT / "Test Project" / "14.08.26" / "Audio"

FIXTURES = [
    ("BUTCH",                                  464.8),
    ("Revoloution",                            309.9),
    ("Switch Disco",                           268.1),
    ("Cevin Fisher & Harry Romero",            375.1),
]


def _resolve(substring: str) -> Path | None:
    if not AUDIO_DIR.is_dir():
        return None
    matches = [p for p in AUDIO_DIR.iterdir()
               if p.suffix.lower() == ".wav" and substring in p.name]
    if not matches:
        return None
    return sorted(matches)[0]


def _write_tone_then_silence(path: Path, tone_sec: float = 1.0,
                             silence_sec: float = 1.0, sr: int = 44100,
                             freq: float = 440.0) -> None:
    n_tone = int(round(tone_sec * sr))
    n_sil = int(round(silence_sec * sr))
    t = np.arange(n_tone) / sr
    tone = 0.5 * np.sin(2 * math.pi * freq * t)
    sil = np.zeros(n_sil, dtype=np.float32)
    stereo = np.stack([tone.astype(np.float32), tone.astype(np.float32)], axis=1)
    sf.write(str(path), stereo, sr)


def _write_silence(path: Path, dur_sec: float = 2.0, sr: int = 44100) -> None:
    n = int(round(dur_sec * sr))
    sf.write(str(path), np.zeros((n, 2), dtype=np.float32), sr)


def _write_mono_tone(path: Path, tone_sec: float = 1.0,
                     silence_sec: float = 1.0, sr: int = 44100) -> None:
    n_tone = int(round(tone_sec * sr))
    n_sil = int(round(silence_sec * sr))
    t = np.arange(n_tone) / sr
    tone = (0.5 * np.sin(2 * math.pi * 440.0 * t)).astype(np.float32)
    sf.write(str(path), tone, sr)


# --- synthetic tests, always run ---

def test_tone_then_silence_lands_on_boundary(tmp_path):
    wav = tmp_path / "tone.wav"
    _write_tone_then_silence(wav, tone_sec=1.0, silence_sec=1.0)
    end = musical_end_sec(str(wav))
    assert math.isclose(end, 1.0, abs_tol=0.1), f"expected ~1.0 s, got {end:.3f}"


def test_all_silent_returns_zero(tmp_path):
    wav = tmp_path / "silence.wav"
    _write_silence(wav)
    assert musical_end_sec(str(wav)) == 0.0


def test_mono_file_works(tmp_path):
    wav = tmp_path / "mono.wav"
    _write_mono_tone(wav, tone_sec=1.0, silence_sec=1.0)
    end = musical_end_sec(str(wav))
    assert math.isclose(end, 1.0, abs_tol=0.1), f"expected ~1.0 s, got {end:.3f}"


def test_bars_arithmetic_matches_sec():
    """musical_end_bars must equal (musical_end_sec - downbeat_sec) / sec_per_bar."""
    fake = "/nonexistent/path.wav"
    end_sec = 32.0
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr("musical_end.musical_end_sec",
                       lambda *a, **kw: end_sec)
        bars = musical_end_bars(fake, bpm=120.0, downbeat_sec=2.0)
    finally:
        monkey.undo()
    # (32 - 2) / (4 * 60 / 120) = 30 / 2 = 15
    assert math.isclose(bars, 15.0, abs_tol=1e-9)


# --- audio-dependent tests: skip cleanly if fixtures absent ---

@pytest.fixture(scope="module")
def fixture_paths() -> dict[str, Path]:
    paths = {}
    missing = []
    for sub, _expected in FIXTURES:
        p = _resolve(sub)
        if p is None:
            missing.append(sub)
        else:
            paths[sub] = p
    if missing:
        pytest.skip(f"14.08.26 audio fixtures not present: {missing}")
    return paths


def test_butch_kick_less_outro_inside_musical_region(fixture_paths):
    """Regression: the body-relative method stopped at the last kick and
    returned ~449.8 s. The peak-relative method keeps the ~15 s kick-less
    drum outro inside the musical region. Owner confirmed the true ending
    at 7:44 (464 s); expected end ~464.8 s."""
    end = musical_end_sec(str(fixture_paths["BUTCH"]))
    assert end > 460.0, (
        f"musical_end_sec({end:.2f}s) clipped the BUTCH kick-less outro; "
        f"the body-relative method returned ~449.8s here."
    )


@pytest.mark.parametrize("substring,expected", FIXTURES)
def test_measured_endpoints(fixture_paths, substring, expected):
    path = fixture_paths[substring]
    end = musical_end_sec(str(path))
    assert math.isclose(end, expected, abs_tol=0.5), (
        f"{substring}: expected {expected:.2f}s +/-0.5, got {end:.2f}s"
    )
