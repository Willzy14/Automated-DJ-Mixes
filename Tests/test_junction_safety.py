"""Junction-traversal safety: the mechanism that destroyed Sam's masters twice.

On 2026-08-19 and 2026-08-20 the corpus audio under `Test Project/14.08.26/Audio`
was wiped. The cause was recorded as "a recursive delete traverses a Windows
junction and wipes the TARGET" - couriers junction the live corpus into a
scratch workspace, then a cleanup follows the link into the real folder.

The remedy carded at the time was to add junction guards to the project's four
`shutil.rmtree` call sites. Measured on 2026-08-27, that prescription is WRONG,
and these tests pin what is actually true instead:

    SAFE  (leave the junction target alone)
      shutil.rmtree                      - CPython handles junctions since 3.12
      powershell Remove-Item -Recurse    - safe
      cmd rmdir /s /q                    - safe
      git-bash rm -rf                    - safe (errors out, leaves the link)
      git clean -xfd                     - safe

    WIPES THE TARGET
      robocopy /MIR                      - mirrors an empty dir through the link
      hand-rolled Path.iterdir() recursion
      os.walk(topdown=False) + unlink/rmdir

So the danger is NOT the stdlib or the shells - it is HAND-ROLLED recursion
and robocopy. `Path.is_dir()` and `os.walk` both follow junctions, while
`os.path.islink()` returns False for them, which is the whole trap: the obvious
guard does not fire.

These tests convert that from a rule someone must remember into a control that
fails loudly - the repo's own "control, not rule" principle. They matter most
if Python is ever downgraded below 3.12 or someone hand-rolls a delete.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows junctions only")


def _make_junction_case(root: Path) -> tuple[Path, Path]:
    """A scratch tree containing a junction into a 'precious' folder.

    Returns (scratch_dir, canary_file). Everything lives under pytest's
    tmp_path - no real path is ever referenced.
    """
    precious = root / "PRECIOUS"
    precious.mkdir()
    canary = precious / "master.wav"
    canary.write_bytes(b"irreplaceable" * 100)

    scratch = root / "scratch"
    (scratch / "sub").mkdir(parents=True)
    (scratch / "sub" / "junk.txt").write_text("delete me")

    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(scratch / "Audio"), str(precious)],
        capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"could not create a junction: {result.stderr.strip()}")
    return scratch, canary


def test_junction_detection_primitives(tmp_path):
    """`islink` is False for a junction - the reason the obvious guard fails.

    Anyone writing a delete guard reaches for os.path.islink first. On a
    junction it returns False while is_dir() returns True, so the guard passes
    the link straight through to the recursion. os.path.isjunction (3.12+) is
    the primitive that actually works.
    """
    scratch, _ = _make_junction_case(tmp_path)
    link = scratch / "Audio"

    assert link.is_dir()
    assert os.path.islink(link) is False, \
        "if this ever becomes True, islink alone is finally a usable guard"
    assert os.path.isjunction(link) is True
    with os.scandir(scratch) as entries:
        entry = next(e for e in entries if e.name == "Audio")
        assert entry.is_dir(follow_symlinks=False) is True
        assert entry.is_symlink() is False
        assert entry.is_junction() is True


def test_shutil_rmtree_does_not_traverse_a_junction(tmp_path):
    """The delete mechanism this project actually uses must stay safe.

    All four `shutil.rmtree` call sites in the repo depend on this. CPython
    gained junction handling in 3.12; if the interpreter is ever downgraded, or
    the behaviour regresses, this fails and the guards genuinely become
    necessary.
    """
    scratch, canary = _make_junction_case(tmp_path)
    shutil.rmtree(scratch, ignore_errors=True)

    assert canary.exists(), (
        "shutil.rmtree followed a junction and destroyed the target - the "
        "2026-08 incident mechanism. The four rmtree sites now DO need "
        "explicit junction guards.")
    assert canary.read_bytes().startswith(b"irreplaceable")
    assert not scratch.exists()


def test_hand_rolled_recursion_is_the_actual_hazard(tmp_path):
    """PINS THE TRAP, not a capability.

    `os.walk` descends into junctions, so the most natural hand-rolled delete
    destroys the target. This test asserts the DANGEROUS behaviour so the trap
    is documented in executable form and nobody re-derives it from an incident.

    If this ever fails, CPython has changed os.walk's junction handling. That
    is good news - update the note above, do not delete the test.
    """
    scratch, canary = _make_junction_case(tmp_path)

    for dirpath, dirnames, filenames in os.walk(scratch, topdown=False):
        for name in filenames:
            try:
                os.unlink(os.path.join(dirpath, name))
            except OSError:
                pass
        for name in dirnames:
            try:
                os.rmdir(os.path.join(dirpath, name))
            except OSError:
                pass

    assert not canary.exists(), (
        "os.walk no longer traverses junctions - the hand-rolled-recursion "
        "hazard may be gone; re-measure and update the module docstring")


def test_no_source_file_uses_a_junction_unsafe_delete():
    """No robocopy, and no os.walk-driven deletion, anywhere in the tree.

    robocopy /MIR mirrors an empty directory straight through a junction and
    was measured destroying the canary. os.walk plus unlink/rmdir does the
    same. Neither has any use here, so their absence is enforced rather than
    trusted.
    """
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in list(root.glob("Source/**/*.py")) + list(root.glob("Tools/**/*.py")):
        if "Archive" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "robocopy" in text.lower():
            offenders.append(f"{path.relative_to(root)}: robocopy")
        if "os.walk" in text and any(
                token in text for token in ("os.unlink", "os.rmdir",
                                            ".unlink()", ".rmdir()")):
            offenders.append(f"{path.relative_to(root)}: os.walk + delete")
    assert not offenders, (
        "junction-unsafe delete idiom(s) introduced: " + "; ".join(offenders)
        + " - route deletions through shutil.rmtree, which handles junctions")
