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


def test_stem_section_probe_separation_passes_shifts_zero():
    calls = _apply_model_calls("stem_section_probe.py")
    assert len(calls) == 1
    val = _shifts_value(calls[0])
    assert isinstance(val, ast.Constant) and val.value == 0


def _is_zero(node) -> bool:
    return isinstance(node, ast.Constant) and type(node.value) is int and node.value == 0


def _has_shifts_zero(call: ast.Call) -> bool:
    return any(kw.arg == "shifts" and _is_zero(kw.value) for kw in call.keywords)


def check_apply_model_source(src: str, label: str = "<src>") -> list[str]:
    """Return one 'label:line reason' per apply_model use that is not a direct
    call with the literal keyword shifts=0 (names resolved through imports)."""
    tree = ast.parse(src)
    func_names: set[str] = set()
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "demucs.apply":
            for a in node.names:
                if a.name == "apply_model":
                    func_names.add(a.asname or a.name)
                elif a.name == "*":
                    problems.append(f"{label}:{node.lineno} star import from demucs.apply")

    def is_ref(n) -> bool:
        if isinstance(n, ast.Name):
            return isinstance(n.ctx, ast.Load) and n.id in func_names
        return isinstance(n, ast.Attribute) and n.attr == "apply_model"

    allowed: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if is_ref(fn):
            allowed.add(id(fn))
            if not _has_shifts_zero(node):
                problems.append(f"{label}:{node.lineno} call without literal shifts=0")
            continue
        fname = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        if fname == "partial" and node.args and is_ref(node.args[0]):
            allowed.add(id(node.args[0]))
            if not _has_shifts_zero(node):
                problems.append(f"{label}:{node.lineno} partial without literal shifts=0")
        elif (fname == "getattr" and len(node.args) >= 2
              and isinstance(node.args[1], ast.Constant)
              and node.args[1].value == "apply_model"):
            problems.append(f"{label}:{node.lineno} getattr of apply_model")
    for node in ast.walk(tree):
        if is_ref(node) and id(node) not in allowed:
            problems.append(f"{label}:{node.lineno} apply_model used other than a direct call")
    return problems


def test_every_apply_model_use_under_source_and_tools_passes_shifts_zero():
    problems = []
    scanned = 0
    for root in (SOURCE, SOURCE.parent / "Tools"):
        for path in sorted(root.rglob("*.py")):
            scanned += 1
            rel = path.relative_to(SOURCE.parent).as_posix()
            problems += check_apply_model_source(path.read_text(encoding="utf-8"), rel)
    assert scanned > 0
    assert not problems, "apply_model without explicit shifts=0: " + "; ".join(problems)




def _src(*lines: str) -> str:
    return "\n".join(lines) + "\n"


_FROM = "from demucs.apply import apply_model"
_FUNCTOOLS = "import functools"


def test_checker_flags_bad_uses():
    bad = {
        "aliased no shifts": _src(_FROM + " as am", "am(m, t)"),
        "shifts=1": _src(_FROM, "apply_model(m, t, shifts=1)"),
        "shifts variable": _src(_FROM, "n = 0", "apply_model(m, t, shifts=n)"),
        "positional shifts": _src(_FROM, "apply_model(m, t, 0)"),
        "partial no shifts": _src(_FROM, _FUNCTOOLS, "f = functools.partial(apply_model, m)"),
        "getattr": _src("import demucs.apply as M", "f = getattr(M, 'apply_model')"),
        "module alias call no shifts": _src("import demucs.apply as M", "M.apply_model(m, t)"),
        "full path call no shifts": _src("import demucs", "demucs.apply.apply_model(m, t)"),
        "assigned": _src(_FROM, "f = apply_model"),
        "passed as argument": _src(_FROM, "run(apply_model)"),
        "shifts=False": _src(_FROM, "apply_model(m, t, shifts=False)"),
    }
    for name, src in bad.items():
        assert check_apply_model_source(src, name), name


def test_checker_accepts_good_uses():
    good = {
        "plain": _src(_FROM, "apply_model(m, t, shifts=0)"),
        "aliased": _src(_FROM + " as am", "am(m, t, shifts=0)"),
        "module alias": _src("import demucs.apply as M", "M.apply_model(m, t, device=d, shifts=0)"),
        "partial": _src(_FROM, _FUNCTOOLS, "f = functools.partial(apply_model, shifts=0)"),
        "unrelated name": _src("def apply_model(x):", "    return x", "apply_model(1)"),
    }
    for name, src in good.items():
        assert check_apply_model_source(src, name) == [], name


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
