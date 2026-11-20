"""Demucs separation must be deterministic, and stems cached from the old
random separation must be discarded.

demucs.apply.apply_model defaults to shifts=1, which applies a random,
unseeded time offset on every call, so two separations of one file differ
slightly and the beat-grid detector can flip between a good and a bad grid.
shifts=0 removes the randomness.

No GPU, no audio, no real Demucs run. The three call sites (probe_stem_kick_grid,
kick_model_adapter, stem_section_probe) sit inside functions that need torch,
soundfile and a loaded model, so the shifts=0 pins read the source syntax tree
instead of executing them.

Limits of the repo-wide guard: it is a tripwire for the ordinary ways a call
gets added, not a proof. It only acts on names a module has itself bound to
Demucs through an import. It does not follow cross-module re-exports, class
attribute indirection, or importlib / getattr with a variable name.
"""

import ast
import os
import sys
from pathlib import Path

import numpy as np

SOURCE = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE))

import kick_model_adapter  # noqa: E402


def _is_zero(node) -> bool:
    return isinstance(node, ast.Constant) and type(node.value) is int and node.value == 0


def _has_shifts_zero(call: ast.Call) -> bool:
    return any(kw.arg == "shifts" and _is_zero(kw.value) for kw in call.keywords)


def _bound_demucs_names(tree):
    """(names bound to apply_model, {local module name: dotted module path})."""
    func_names: set[str] = set()
    mods: dict[str, str] = {}
    star: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "demucs" or a.name.startswith("demucs."):
                    if a.asname:
                        mods[a.asname] = a.name
                    else:
                        mods["demucs"] = "demucs"
        elif isinstance(node, ast.ImportFrom):
            if node.module == "demucs.apply":
                for a in node.names:
                    if a.name == "apply_model":
                        func_names.add(a.asname or a.name)
                    elif a.name == "*":
                        star.append(node.lineno)
            elif node.module == "demucs":
                for a in node.names:
                    if a.name == "apply":
                        mods[a.asname or a.name] = "demucs.apply"
    return func_names, mods, star


def _local_names(fn) -> set[str]:
    args = fn.args
    names = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    body = [fn.body] if isinstance(fn, ast.Lambda) else fn.body
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                names.add(n.id)
    return names


def check_apply_model_source(src: str, label: str = "<src>") -> list[str]:
    """One 'label:line reason' per use of a Demucs apply_model, bound through
    this module's own imports, that is not a direct call with the literal
    keyword shifts=0. A module with no demucs import yields nothing.

    A name shadowed by a parameter or assignment inside a function does not
    count inside that function (names stored anywhere in the function body
    count as shadowing, which can only hide a finding, never invent one)."""
    tree = ast.parse(src)
    func_names, mods, star = _bound_demucs_names(tree)
    problems = [f"{label}:{ln} star import from demucs.apply" for ln in star]
    if not func_names and not mods:
        return problems
    allowed: set[int] = set()

    def resolve(node, shadow):
        if isinstance(node, ast.Name):
            return None if node.id in shadow else mods.get(node.id)
        if isinstance(node, ast.Attribute):
            base = resolve(node.value, shadow)
            return f"{base}.{node.attr}" if base else None
        return None

    def is_ref(node, shadow) -> bool:
        if isinstance(node, ast.Name):
            return (isinstance(node.ctx, ast.Load) and node.id in func_names
                    and node.id not in shadow)
        return (isinstance(node, ast.Attribute) and node.attr == "apply_model"
                and resolve(node.value, shadow) == "demucs.apply")

    def visit(node, shadow):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            shadow = shadow | _local_names(node)
        if isinstance(node, ast.Call):
            fn = node.func
            fname = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if is_ref(fn, shadow):
                allowed.add(id(fn))
                if not _has_shifts_zero(node):
                    problems.append(f"{label}:{node.lineno} call without literal shifts=0")
            elif fname == "partial" and node.args and is_ref(node.args[0], shadow):
                allowed.add(id(node.args[0]))
                problems.append(
                    f"{label}:{node.lineno} partial of apply_model: call apply_model "
                    "directly with shifts=0")
            elif (fname == "getattr" and len(node.args) >= 2
                  and resolve(node.args[0], shadow) == "demucs.apply"
                  and isinstance(node.args[1], ast.Constant)
                  and node.args[1].value == "apply_model"):
                problems.append(f"{label}:{node.lineno} getattr of apply_model")
        elif is_ref(node, shadow) and id(node) not in allowed:
            problems.append(f"{label}:{node.lineno} apply_model used other than a direct call")
        for child in ast.iter_child_nodes(node):
            visit(child, shadow)

    visit(tree, frozenset())
    return problems


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
    assert _is_zero(_shifts_value(calls[0]))


def test_kick_model_adapter_separation_passes_shifts_zero():
    calls = _apply_model_calls("kick_model_adapter.py")
    assert len(calls) == 1
    assert _is_zero(_shifts_value(calls[0]))


def test_stem_section_probe_separation_passes_shifts_zero():
    calls = _apply_model_calls("stem_section_probe.py")
    assert len(calls) == 1
    assert _is_zero(_shifts_value(calls[0]))


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
        "shifts=False": _src(_FROM, "apply_model(m, t, shifts=False)"),
        "partial no shifts": _src(_FROM, _FUNCTOOLS, "f = functools.partial(apply_model, m)"),
        "partial with shifts=0": _src(
            _FROM, "from functools import partial", "f = partial(apply_model, shifts=0)"),
        "getattr on demucs module": _src("import demucs.apply as M", "f = getattr(M, 'apply_model')"),
        "module alias call": _src("import demucs.apply as M", "M.apply_model(m, t)"),
        "full path call": _src("import demucs", "demucs.apply.apply_model(m, t)"),
        "import demucs.apply call": _src("import demucs.apply", "demucs.apply.apply_model(m, t)"),
        "from demucs import apply": _src("from demucs import apply as A", "A.apply_model(m, t)"),
        "assigned": _src(_FROM, "f = apply_model"),
        "passed as argument": _src(_FROM, "run(apply_model)"),
        "call in a function": _src(_FROM, "def f(m, t):", "    return apply_model(m, t)"),
    }
    for name, src in bad.items():
        assert check_apply_model_source(src, name), name


def test_checker_accepts_good_uses():
    good = {
        "plain": _src(_FROM, "apply_model(m, t, shifts=0)"),
        "aliased": _src(_FROM + " as am", "am(m, t, shifts=0)"),
        "module alias": _src("import demucs.apply as M", "M.apply_model(m, t, device=d, shifts=0)"),
        "full path": _src("import demucs", "demucs.apply.apply_model(m, t, shifts=0)"),
        "editor.apply_model, no demucs import": _src("editor.apply_model(preset)"),
        "getattr on non-demucs object, no demucs import": _src("f = getattr(editor, 'apply_model')"),
        "editor.apply_model beside a demucs import": _src(
            "import demucs.apply as M", "editor.apply_model(preset)"),
        "parameter named like the alias": _src(
            _FROM + " as am", "def f(am):", "    return am(1)"),
        "assignment shadows the name in a function": _src(
            _FROM, "def f():", "    apply_model = other", "    return apply_model(1)"),
        "own function of the same name, no demucs import": _src(
            "def apply_model(x):", "    return x", "apply_model(1)"),
    }
    for name, src in good.items():
        assert check_apply_model_source(src, name) == [], name


def test_checker_known_limits_are_not_flagged():
    """Documented blind spots (see the module docstring). Pinned so a change in
    what the tripwire covers is a deliberate edit."""
    not_flagged = {
        "cross-module re-export": _src("from helpers import apply_model", "apply_model(m, t)"),
        "importlib with a name": _src(
            "import importlib",
            "importlib.import_module('demucs.apply').apply_model(m, t)"),
        "getattr with a variable name": _src(
            "import demucs.apply as M", "n = 'apply_model'", "f = getattr(M, n)"),
        "attribute of an unrelated object": _src(
            _FROM, "self.sep = helpers.separator", "self.sep.apply_model(m, t)"),
    }
    for name, src in not_flagged.items():
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
