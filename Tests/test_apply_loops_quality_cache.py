"""Regression pins for apply_loops._quality_cache_for_track's cross-project
disambiguation (found 2026-09-11: a track reused across two projects made the
worktree-wide cache glob raise on a genuine, resolvable match)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from apply_loops import _quality_cache_for_track


def _track_text(wav_path: Path) -> str:
    return f'<Path Value="{wav_path.as_posix()}" />'


@pytest.fixture(autouse=True)
def _chdir_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield


def _make_cache(project: Path, track_name: str) -> Path:
    stem_dir = project / "_Stem Analysis"
    stem_dir.mkdir(parents=True, exist_ok=True)
    cache = stem_dir / f"{track_name}__stemenv.npz"
    cache.write_bytes(b"")
    return cache


def test_same_track_cached_in_another_project_is_not_ambiguous(tmp_path):
    """The exact 2026-09-11 shape: the track's OWN project has a cache, and
    an unrelated older project happens to have cached a same-named track too.
    The lookup must resolve to the current project's own cache, not raise."""
    track_name = "Sam Leagas - Bad Behaviours (Extended Mix) SW V2"

    current_project = tmp_path / "10.09.26 Tech House Heldout"
    other_project = tmp_path / "14.08.26"
    wanted = _make_cache(current_project, track_name)
    _make_cache(other_project, track_name)

    wav_path = current_project / "Audio" / f"{track_name}.wav"
    result = _quality_cache_for_track(_track_text(wav_path), track_name)

    assert result == wanted.resolve()


def test_genuinely_ambiguous_within_the_same_project_still_raises(tmp_path):
    """If two caches both live under the track's own project root, there is
    no ground truth to pick one - the fail-closed raise must survive."""
    track_name = "Duplicate Track"
    project = tmp_path / "Some Project"

    # Two different _Stem Analysis directories that both resolve to being
    # "under" the track's own project root.
    (project / "A" / "_Stem Analysis").mkdir(parents=True)
    (project / "A" / "_Stem Analysis" / f"{track_name}__stemenv.npz").write_bytes(b"")
    (project / "B" / "_Stem Analysis").mkdir(parents=True)
    (project / "B" / "_Stem Analysis" / f"{track_name}__stemenv.npz").write_bytes(b"")

    wav_path = project / "Audio" / f"{track_name}.wav"

    with pytest.raises(ValueError, match="multiple cached stem envelopes"):
        _quality_cache_for_track(_track_text(wav_path), track_name)


def test_single_match_still_resolves_directly(tmp_path):
    """Unrelated to the bug: the ordinary single-cache case must keep working."""
    track_name = "Only One"
    project = tmp_path / "Only Project"
    wanted = _make_cache(project, track_name)

    wav_path = project / "Audio" / f"{track_name}.wav"
    result = _quality_cache_for_track(_track_text(wav_path), track_name)

    assert result == wanted.resolve()
