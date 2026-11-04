"""Tests for Tools/resolve_masters.py (multi-track EP folders, the `must` hint, the exact-filename selector).

Hermetic: every test builds a synthetic library under tmp_path (100 ms silent WAVs written with the stdlib wave module,
no real audio, no Dropbox, no network). Tools/folder_index.json is gitignored and resolve_masters reads it at IMPORT time,
so the module is loaded from a COPY of Tools/resolve_masters.py placed beside a synthetic index. The copy is byte-for-byte
the worktree file (asserted), so these tests exercise exactly the code under Tools/.
"""
import importlib.util
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REAL_SOURCE = ROOT / "Tools" / "resolve_masters.py"

EP_FOLDER = "Artist - Better When [Label] Project"
EP_FILES = ["01 Change The Situation.wav", "02 Better When.wav", "03 Other Thing.wav"]


def _write_wav(path: Path, ms: int = 100, rate: int = 8000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * ms / 1000))


def make_folder(lib: Path, folder_name: str, files, subdir: str = "MASTER RENDERS") -> Path:
    """One project folder with a MASTER RENDERS subfolder holding silent WAVs (or other files)."""
    folder = lib / folder_name
    mr = folder / subdir
    mr.mkdir(parents=True)
    for name in files:
        if name.lower().endswith(".wav"):
            _write_wav(mr / name)
        else:
            (mr / name).write_bytes(b"not audio")
    return folder


def load_resolver(tmp_path: Path, folders):
    """Import a copy of resolve_masters.py that sits beside a synthetic folder_index.json listing `folders` [(name, path)]."""
    tools = tmp_path / "ToolsCopy"
    tools.mkdir(exist_ok=True)
    copy = tools / "resolve_masters.py"
    copy.write_bytes(REAL_SOURCE.read_bytes())
    assert copy.read_bytes() == REAL_SOURCE.read_bytes()
    (tools / "folder_index.json").write_text(json.dumps([[n, str(p)] for n, p in folders]), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"resolve_masters_under_test_{abs(hash(str(tmp_path)))}", copy)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, tools


def one_folder(tmp_path: Path, folder_name: str, files):
    lib = tmp_path / "Library"
    folder = make_folder(lib, folder_name, files)
    mod, _ = load_resolver(tmp_path, [(folder_name, folder)])
    return mod


def names(result):
    return [os.path.basename(p) for _, p in result]


# --- the bug and the fix ------------------------------------------------------------------------------------------

def test_tests_import_this_worktrees_resolver(tmp_path, capsys):
    mod, tools = load_resolver(tmp_path, [])
    with capsys.disabled():
        print(f"\n[evidence] worktree source: {REAL_SOURCE}")
        print(f"[evidence] imported module __file__ (byte-identical copy): {mod.__file__}")
    assert Path(mod.__file__).read_bytes() == REAL_SOURCE.read_bytes()
    assert REAL_SOURCE.is_file() and str(ROOT).lower() in str(REAL_SOURCE).lower()


def test_ep_folder_resolves_the_named_track_not_the_first(tmp_path):
    mod = one_folder(tmp_path, EP_FOLDER, EP_FILES)
    assert names(mod.resolve("Artist", "Better When"))[0] == "02 Better When.wav"


def test_ep_folder_with_decorated_filenames_resolves_the_named_track(tmp_path):
    # Real masters carry mix and version decoration, so an exact stem match never fires; the title inside the name must.
    files = ["Artist - Change The Situation (Extended Mix) SW V2.wav",
             "Artist - Better When (Extended Mix) SW V2.wav",
             "Artist - Zebra Dance (Extended Mix) SW V2.wav"]
    mod = one_folder(tmp_path, "Artist - Better When EP [Label] Project", files)
    assert names(mod.resolve("Artist", "Better When"))[0] == "Artist - Better When (Extended Mix) SW V2.wav"


def test_folder_name_hint_that_names_a_different_track_does_not_win(tmp_path):
    # must matches the FOLDER name (it names the other EP track); the requested title still decides the file.
    mod = one_folder(tmp_path, "Artist - Change The Situation & Better When [Label] Project", EP_FILES)
    r = mod.resolve("Artist", "Better When", "Change The Situation")
    assert names(r)[0] == "02 Better When.wav"


def test_must_matching_a_filename_when_the_folder_name_does_not(tmp_path):
    files = ["Artist - Title (Extended Mix) SW V2.wav", "Artist - Title (Club Mix) SW V1.wav"]
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", files)
    r = mod.resolve("Artist", "Title", "Club Mix")
    assert names(r) == ["Artist - Title (Club Mix) SW V1.wav"]


def test_must_given_as_a_full_filename_with_a_track_number(tmp_path):
    mod = one_folder(tmp_path, EP_FOLDER, EP_FILES)
    assert names(mod.resolve("Artist", "Better When", "02 Better When"))[0] == "02 Better When.wav"
    assert names(mod.resolve("Artist", "Better When", "Better When.wav"))[0] == "02 Better When.wav"


def test_must_in_the_folder_name_still_filters_folders(tmp_path):
    lib = tmp_path / "Library"
    fa = make_folder(lib, "Artist - Title [Label A] Project", ["Title A.wav"])
    fb = make_folder(lib, "Artist - Title [Label B] Project", ["Title B.wav"])
    mod, _ = load_resolver(tmp_path, [("Artist - Title [Label A] Project", fa), ("Artist - Title [Label B] Project", fb)])
    assert names(mod.resolve("Artist", "Title", "Label B")) == ["Title B.wav"]
    assert names(mod.resolve("Artist", "Title", "Label A")) == ["Title A.wav"]
    assert sorted(names(mod.resolve("Artist", "Title"))) == ["Title A.wav", "Title B.wav"]


def test_exact_filename_beats_partial_matches(tmp_path):
    # PINNED: a file named exactly like the requested title beats every partial match, including the
    # "(Extended Mix)" file that the old rank preferred, and the "(Dub)" file.
    files = ["Better When (Dub).wav", "Better When (Extended Mix).wav", "Better When.wav"]
    mod = one_folder(tmp_path, "Artist - Better When [Label] Project", files)
    r = names(mod.resolve("Artist", "Better When"))
    assert r[0] == "Better When.wav"
    assert r == ["Better When.wav", "Better When (Extended Mix).wav", "Better When (Dub).wav"]


def test_exact_filename_matches_artist_dash_title_too(tmp_path):
    files = ["Artist - Better When (Extended Mix).wav", "Artist - Better When.wav"]
    mod = one_folder(tmp_path, "Artist - Better When [Label] Project", files)
    assert names(mod.resolve("Artist", "Better When"))[0] == "Artist - Better When.wav"


def test_exact_selector_picks_only_that_file(tmp_path):
    files = ["Better When (Dub).wav", "Better When (Extended Mix).wav", "Better When.wav"]
    mod = one_folder(tmp_path, "Artist - Better When [Label] Project", files)
    # PINNED: the selector is strict, and it can pick the dub that the rank penalty buries.
    assert names(mod.resolve("Artist", "Better When", None, "Better When (Dub)")) == ["Better When (Dub).wav"]
    assert names(mod.resolve("Artist", "Better When", None, "better when (dub).WAV")) == ["Better When (Dub).wav"]
    assert names(mod.resolve("Artist", "Better When", None, "Better When")) == ["Better When.wav"]


def test_exact_selector_with_no_match_returns_nothing(tmp_path):
    # PINNED: an explicit selector never falls back to some other file.
    mod = one_folder(tmp_path, EP_FOLDER, EP_FILES)
    assert mod.resolve("Artist", "Better When", None, "Better When (Extended Mix)") == []
    assert mod.resolve("Artist", "Better When", None, "Better") == []


def test_exact_selector_is_not_a_substring_match(tmp_path):
    mod = one_folder(tmp_path, EP_FOLDER, ["02 Better When Now.wav", "03 Better.wav"])
    assert mod.resolve("Artist", "Better", None, "Better When") == []
    assert names(mod.resolve("Artist", "Better", None, "Better")) == ["03 Better.wav"]


@pytest.mark.parametrize("prefixed", ["02 - Better When.wav", "02. Better When.wav", "02 Better When.wav",
                                      "2) Better When.wav", "02_Better When.wav", "02.Better When.wav"])
def test_track_number_prefixes_are_stripped_for_exact_matching(tmp_path, prefixed):
    mod = one_folder(tmp_path, EP_FOLDER, [prefixed, "99 Zebra.wav"])
    assert names(mod.resolve("Artist", "Better When"))[0] == prefixed
    assert names(mod.resolve("Artist", "Better When", None, "Better When")) == [prefixed]
    # the same prefix typed in the hint is stripped too
    assert names(mod.resolve("Artist", "Better When", None, prefixed.replace(".wav", "").replace("02", "07")))[:1] == [prefixed]


def test_a_title_that_starts_with_a_number_is_not_mangled(tmp_path):
    mod = one_folder(tmp_path, "Artist - 2 Hearts [Label] Project", ["2 Hearts.wav", "9 Zebra.wav"])
    assert names(mod.resolve("Artist", "2 Hearts", None, "2 Hearts")) == ["2 Hearts.wav"]
    assert names(mod.resolve("Artist", "2 Hearts"))[0] == "2 Hearts.wav"


def test_matching_is_case_insensitive(tmp_path):
    mod = one_folder(tmp_path, EP_FOLDER, ["01 Change The Situation.wav", "02 BETTER when.WAV", "03 Other Thing.wav"])
    assert names(mod.resolve("artist", "better WHEN"))[0] == "02 BETTER when.WAV"
    assert names(mod.resolve("ARTIST", "Better When", None, "02 better WHEN.wav")) == ["02 BETTER when.WAV"]


def test_no_filename_match_behaves_as_before(tmp_path):
    # None of the files mentions the title, so the order is the old one: version rank, then path descending.
    files = ["Master A SW V1.wav", "Master B SW V2.wav", "Master C 24 Bit MASTER.wav", "Master D.wav"]
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", files)
    r = mod.resolve("Artist", "Title")
    assert names(r) == ["Master C 24 Bit MASTER.wav", "Master B SW V2.wav", "Master A SW V1.wav"]
    assert [s for s, _ in r] == [5, 2, 1]


def test_latest_version_still_wins(tmp_path):
    files = ["Artist - Title (Extended Mix) SW V1.wav", "Artist - Title (Extended Mix) SW V2.wav",
             "Artist - Title (Extended Mix) SW V3.wav"]
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", files)
    assert names(mod.resolve("Artist", "Title")) == [
        "Artist - Title (Extended Mix) SW V3.wav", "Artist - Title (Extended Mix) SW V2.wav",
        "Artist - Title (Extended Mix) SW V1.wav"]


def test_amended_versions_still_order_after_plain_masters(tmp_path):
    files = ["Artist - Title 24 Bit MASTER.wav", "Artist - Title MASTER AMENDED.wav", "Artist - Title MASTER AMENDED V2.wav"]
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", files)
    assert names(mod.resolve("Artist", "Title")) == [
        "Artist - Title MASTER AMENDED V2.wav", "Artist - Title MASTER AMENDED.wav", "Artist - Title 24 Bit MASTER.wav"]


def test_versions_inside_an_ep_pick_the_latest_of_the_named_track(tmp_path):
    files = ["Artist - Better When (Extended Mix) SW V1.wav", "Artist - Better When (Extended Mix) SW V2.wav",
             "Artist - Change The Situation (Extended Mix) SW V1.wav", "Artist - Change The Situation (Extended Mix) SW V2.wav"]
    mod = one_folder(tmp_path, "Artist - Better When EP [Label] Project", files)
    r = names(mod.resolve("Artist", "Better When"))
    assert r[:2] == ["Artist - Better When (Extended Mix) SW V2.wav", "Artist - Better When (Extended Mix) SW V1.wav"]


def test_non_wav_files_are_ignored(tmp_path):
    files = ["02 Better When.mp3", "02 Better When.wav.asd", "02 Better When.alp", "notes.txt", "02 Better When.wav"]
    mod = one_folder(tmp_path, EP_FOLDER, files)
    assert names(mod.resolve("Artist", "Better When")) == ["02 Better When.wav"]
    assert mod.resolve("Artist", "Better When", None, "02 Better When.mp3") == []


def test_a_directory_named_like_a_wav_is_never_returned(tmp_path):
    lib = tmp_path / "Library"
    folder = make_folder(lib, EP_FOLDER, ["02 Better When.wav"])
    (folder / "MASTER RENDERS" / "Better When.wav").mkdir()          # exists, but is not a file
    mod, _ = load_resolver(tmp_path, [(EP_FOLDER, folder)])
    r = mod.resolve("Artist", "Better When")
    assert names(r) == ["02 Better When.wav"]
    assert all(os.path.isfile(p) for _, p in r)


def test_every_returned_path_exists(tmp_path):
    mod = one_folder(tmp_path, EP_FOLDER, EP_FILES)
    for args in (("Artist", "Better When"), ("Artist", "Better When", "Other"), ("Artist", "Better When", None, "02 Better When")):
        assert all(os.path.isfile(p) for _, p in mod.resolve(*args))


def test_folders_without_master_renders_and_lnk_entries_are_skipped(tmp_path):
    lib = tmp_path / "Library"
    nomr = make_folder(lib, "Artist - Title [NoMR] Project", ["Title.wav"], subdir="Audio")
    lnk = make_folder(lib, "Artist - Title [Link] Project.lnk", ["Title.wav"])
    good = make_folder(lib, "Artist - Title [Good] Project", ["Title Good.wav"])
    mod, _ = load_resolver(tmp_path, [("Artist - Title [NoMR] Project", nomr), ("Artist - Title [Link] Project.lnk", lnk),
                                      ("Artist - Title [Good] Project", good)])
    assert names(mod.resolve("Artist", "Title")) == ["Title Good.wav"]


def test_folder_must_contain_both_artist_and_title(tmp_path):
    lib = tmp_path / "Library"
    other = make_folder(lib, "Someone Else - Better When [Label] Project", ["Better When.wav"])
    mod, _ = load_resolver(tmp_path, [("Someone Else - Better When [Label] Project", other)])
    assert mod.resolve("Artist", "Better When") == []


def test_at_most_three_results(tmp_path):
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", [f"Title V{i}.wav" for i in range(6)])
    assert len(mod.resolve("Artist", "Title")) == 3


# --- the command line ---------------------------------------------------------------------------------------------

def _run_cli(tools: Path, picks) -> str:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    done = subprocess.run([sys.executable, "-I", str(tools / "resolve_masters.py"), json.dumps(picks)],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(tools), env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_cli_fourth_element_is_the_exact_filename(tmp_path):
    lib = tmp_path / "Library"
    folder = make_folder(lib, EP_FOLDER, EP_FILES)
    _, tools = load_resolver(tmp_path, [(EP_FOLDER, folder)])
    out = _run_cli(tools, [["Artist", "Better When", None, "03 Other Thing"]])
    assert "Artist - Better When:" in out
    assert "03 Other Thing.wav" in out
    assert "02 Better When.wav" not in out and "01 Change The Situation.wav" not in out
    none = _run_cli(tools, [["Artist", "Better When", None, "No Such File"]])
    assert "NONE" in none


def test_cli_three_element_form_still_works(tmp_path):
    lib = tmp_path / "Library"
    folder = make_folder(lib, EP_FOLDER, EP_FILES)
    _, tools = load_resolver(tmp_path, [(EP_FOLDER, folder)])
    out = _run_cli(tools, [["Artist", "Better When"], ["Artist", "Better When", "Label"]])
    first = out.index("Artist - Better When:")
    assert out.count("Artist - Better When:") == 2
    assert out[first:].splitlines()[1].endswith("02 Better When.wav")
