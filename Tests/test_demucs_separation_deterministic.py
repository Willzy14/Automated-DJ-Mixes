"""Demucs separation must be deterministic, and stems cached from the old
random separation must be discarded.

demucs.apply.apply_model defaults to shifts=1, which applies a random,
unseeded time offset on every call, so two separations of one file differ
slightly and the beat-grid detector can flip between a good and a bad grid.
shifts=0 removes the randomness.

No GPU, no audio, no real Demucs run. The two call sites sit inside functions
that need torch, soundfile and a loaded model, so the shifts=0 pin reads the
source syntax tree instead of executing them.
"""

import ast
import os
import sys
from pathlib import Path

import numpy as np

SOURCE = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE))

import kick_model_adapter  # noqa: E402


def _apply_model_calls(filename: str) -> list[ast.Call]:
    tree = ast.parse((SOURCE / filename).read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if name == "apply_model":
                calls.append(node)
    return calls


def _shifts_value(call: ast.Call):
    for kw in call.keywords:
        if kw.arg == "shifts":
            return kw.value
    return None


def test_probe_stem_kick_grid_separation_passes_shifts_zero():
    calls = _apply_model_calls("probe_stem_kick_grid.py")
    assert len(calls) == 1
    val = _shifts_value(calls[0])
    assert isinstance(val, ast.Constant) and val.value == 0


def test_kick_model_adapter_separation_passes_shifts_zero():
    calls = _apply_model_calls("kick_model_adapter.py")
    assert len(calls) == 1
    val = _shifts_value(calls[0])
    assert isinstance(val, ast.Constant) and val.value == 0


def test_drums_cache_version_is_two():
    assert kick_model_adapter.DRUMS_CACHE_VERSION == 2


def test_drums_cache_recorded_with_version_one_is_a_miss(tmp_path):
    wav = tmp_path / "Track.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 64)
    cache_dir = tmp_path / "_Stem Analysis"
    cache_dir.mkdir()
    st = os.stat(wav)

    def write(version: int):
        np.savez(
            kick_model_adapter._drums_cache_path(wav, cache_dir),
            drums=np.zeros(100, dtype=np.float32),
            sr=np.array(44100),
            src_size=np.array(int(st.st_size)),
            src_mtime_ns=np.array(int(st.st_mtime_ns)),
            src_fingerprint=np.array(""),
            demucs_model=np.array(kick_model_adapter.DEMUCS_MODEL_NAME),
            cache_version=np.array(version),
        )

    write(1)
    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is None
    write(2)
    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is not None
