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

# The resolver breaks ties by date modified (newer first). make_folder gives every file this one time, so the old path-order tests do
# not depend on how quickly the files were written; the date tests below move single files with os.utime.
BASE_TIME = 1_700_000_000


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
        os.utime(mr / name, (BASE_TIME, BASE_TIME))
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


def test_latest_version_wins_and_the_exact_name_only_breaks_ties(tmp_path):
    # Sam's standing rule: the latest version wins. A bare "Better When.wav" does NOT beat the newer decorated file (A02),
    # and the "(Dub)" file is buried by the old rank. The exact name only decides between files of the SAME version rank.
    mod = one_folder(tmp_path, "Artist - Better When [Label] Project", ["Better When.wav", "Better When (Extended Mix) SW V2.wav"])
    assert names(mod.resolve("Artist", "Better When")) == ["Better When (Extended Mix) SW V2.wav", "Better When.wav"]
    mod = one_folder(tmp_path / "b", "Artist - Better When [Label] Project",
                     ["Better When (Dub).wav", "Better When (Extended Mix).wav", "Better When.wav"])
    assert names(mod.resolve("Artist", "Better When")) == ["Better When (Extended Mix).wav", "Better When.wav", "Better When (Dub).wav"]


def test_at_equal_version_rank_the_exact_title_wins(tmp_path):
    # Path descending alone would put "Better When Remaster.wav" before "01 Better When.wav"; the exact name decides the tie.
    files = ["01 Better When.wav", "Better When Remaster.wav", "Better When (Extended Mix) SW V2.wav"]
    mod = one_folder(tmp_path, "Artist - Better When [Label] Project", files)
    assert names(mod.resolve("Artist", "Better When")) == [
        "Better When (Extended Mix) SW V2.wav", "01 Better When.wav", "Better When Remaster.wav"]


def test_an_upper_case_wav_extension_is_still_the_exact_name(tmp_path):
    mod = one_folder(tmp_path, "Artist - Better When [Label] Project", ["01 Better When.WAV", "Better When Remaster.wav"])
    assert names(mod.resolve("Artist", "Better When")) == ["01 Better When.WAV", "Better When Remaster.wav"]
    mod2 = one_folder(tmp_path / "b", "Artist - Better When [Label] Project", ["02 Better When.wav", "Better When.WAV"])
    assert names(mod2.resolve("Artist", "Better When", None, "Better When")) == ["Better When.WAV"]


def test_artist_dash_title_is_an_exact_name_that_only_breaks_ties(tmp_path):
    files = ["Artist - Better When (Extended Mix).wav", "Artist - Better When.wav", "Better When Remaster.wav"]
    mod = one_folder(tmp_path, "Artist - Better When [Label] Project", files)
    assert names(mod.resolve("Artist", "Better When")) == [
        "Artist - Better When (Extended Mix).wav", "Artist - Better When.wav", "Better When Remaster.wav"]


@pytest.mark.parametrize("artist,folder,title,files,expected", [
    pytest.param("Artist", "Artist - Better When [Label] Project", "Better When",
                 ["Better When.wav", "Better When (Extended Mix) SW V2.wav"],
                 ["Better When (Extended Mix) SW V2.wav", "Better When.wav"], id="A02-bare-vs-SW-V2"),
    pytest.param("Artist", "Artist - Title [Label] Project", "Title",
                 ["Title.wav", "Title SW V2.wav", "Title SW V3.wav"],
                 ["Title SW V3.wav", "Title SW V2.wav", "Title.wav"], id="A16-bare-vs-V2-V3"),
    pytest.param("A03", "A03 - Title [Label] Project", "Title",
                 ["A03 - Title.wav", "A03 - Title 24 Bit MASTER.wav", "A03 - Title MASTER AMENDED.wav"],
                 ["A03 - Title MASTER AMENDED.wav", "A03 - Title 24 Bit MASTER.wav", "A03 - Title.wav"], id="A03-bare-vs-AMENDED"),
    pytest.param("Artist", "Artist - Title [Label] Project", "Title",
                 ["Title.wav", "Title (Extended Mix).wav"], ["Title (Extended Mix).wav", "Title.wav"], id="A14-bare-vs-Extended"),
    pytest.param("Artist", "Artist - Title [Label] Project", "Title",
                 ["Title.wav", "Title (Original Mix).wav"], ["Title (Original Mix).wav", "Title.wav"], id="A15-bare-vs-Original"),
    pytest.param("A30", "A30 - Title [Label] Project", "Title",
                 ["A30 - Title.wav", "A30 - Title (Extended Mix) SW V1.wav"],
                 ["A30 - Title (Extended Mix) SW V1.wav", "A30 - Title.wav"], id="A30-bare-vs-Extended-V1"),
])
def test_the_version_rule_beats_a_bare_exact_name(tmp_path, artist, folder, title, files, expected):
    # Regressions a reviewer measured against the first round: the old rank (Extended/Original, newest SW V, AMENDED) must decide.
    # For A03 and A30 the artist key matches the "A03 - Title" file name, which is what made the bare file an exact match.
    mod = one_folder(tmp_path, folder, files)
    assert names(mod.resolve(artist, title)) == expected


def test_an_empty_must_hint_is_no_hint(tmp_path):
    mod = one_folder(tmp_path, "Artist - Better When [Label] Project", ["Better When.wav", "Better When (Extended Mix) SW V2.wav"])
    assert names(mod.resolve("Artist", "Better When", ""))[0] == "Better When (Extended Mix) SW V2.wav"


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
    other = prefixed.replace(".wav", "").replace("02", "07")
    # a different number typed by the caller finds nothing (round 3); a name whose number did not change is the full stem
    assert names(mod.resolve("Artist", "Better When", None, other)) == ([prefixed] if other + ".wav" == prefixed else [])


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


# --- round 2: variants, the must hint, ties, track numbers, the exact selector -----------------------------------------

@pytest.mark.parametrize("variant", ["(Someone Remix)", "(Someone Rework)", "(Someone Refix)", "(Someone Bootleg)",
                                     "(Someone VIP)", "(VIP Mix)", "[Someone VIP]", "(Joe V.I.P.)", "(Joe V.I.P. Mix)",
                                     "(Joe Re-Work)", "(Joe Re-Fix)", "(Joe Remode)"])
def test_a_remix_like_variant_is_never_the_named_track(tmp_path, variant):
    # A remix and an original are different tracks: even with the newer version (A33) the variant does not win.
    files = ["Artist - Title (Extended Mix) SW V1.wav", f"Artist - Title {variant} (Extended Mix) SW V3.wav"]
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", files)
    assert names(mod.resolve("Artist", "Title")) == files


def test_original_folder_beats_a_remix_folder_with_a_higher_version(tmp_path):                         # A17
    lib = tmp_path / "Library"
    orig = make_folder(lib, "Artist - Title [Label] Project", ["Artist - Title (Extended Mix) SW V1.wav"])
    remix = make_folder(lib, "Artist - Title (Someone Remix) [Label] Project",
                        ["Artist - Title (Someone Remix) (Extended Mix) SW V2.wav"])
    mod, _ = load_resolver(tmp_path, [("Artist - Title [Label] Project", orig),
                                      ("Artist - Title (Someone Remix) [Label] Project", remix)])
    assert names(mod.resolve("Artist", "Title")) == [
        "Artist - Title (Extended Mix) SW V1.wav", "Artist - Title (Someone Remix) (Extended Mix) SW V2.wav"]


def test_a_remix_folder_alone_still_resolves(tmp_path):
    mod = one_folder(tmp_path, "Artist - Title (Someone Remix) [Label] Project",
                     ["Artist - Title (Someone Remix) SW V1.wav", "Artist - Title (Someone Remix) SW V2.wav"])
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title (Someone Remix) SW V2.wav"


def test_the_must_hint_beats_the_plain_title(tmp_path):                                                # A22
    mod = one_folder(tmp_path, "Artist - Title (Someone Remix) [Label] Project", ["Title.wav", "Title (Someone Remix) SW V2.wav"])
    assert names(mod.resolve("Artist", "Title", "Someone Remix")) == ["Title (Someone Remix) SW V2.wav", "Title.wav"]


def test_the_hint_picks_the_latest_version_of_the_remix(tmp_path):
    files = ["Artist - Title.wav", "Artist - Title (Someone Remix) SW V1.wav", "Artist - Title (Someone Remix) SW V2.wav"]
    mod = one_folder(tmp_path, "Artist - Title (Someone Remix) [Label] Project", files)
    assert names(mod.resolve("Artist", "Title", "Someone Remix"))[:2] == [
        "Artist - Title (Someone Remix) SW V2.wav", "Artist - Title (Someone Remix) SW V1.wav"]


def test_a_title_that_says_remix_names_the_remix(tmp_path):
    files = ["Artist - Title SW V3.wav", "Artist - Title (Someone Remix) SW V2.wav"]
    mod = one_folder(tmp_path, "Artist - Title Someone Remix [Label] Project", files)
    assert names(mod.resolve("Artist", "Title (Someone Remix)"))[0] == "Artist - Title (Someone Remix) SW V2.wav"


def test_vip_is_matched_as_a_whole_word_and_a_title_that_has_it_switches_it_off(tmp_path):
    # vip can be a word in a real title: asking for "VIP Lounge" makes the VIP Lounge file the named track ...
    mod = one_folder(tmp_path, "Artist - VIP Lounge [Label] Project", ["VIP Lounge SW V3.wav", "Lounge SW V1.wav"])
    assert names(mod.resolve("Artist", "VIP Lounge"))[0] == "VIP Lounge SW V3.wav"
    # ... asking for "Lounge" does not (a VIP is another track) ...
    assert names(mod.resolve("Artist", "Lounge"))[0] == "Lounge SW V1.wav"
    # ... and vip inside another word (Viper) is not a marker at all.
    mod2 = one_folder(tmp_path / "b", "Artist - Viper [Label] Project", ["Viper SW V3.wav", "Viper (Radio Edit) SW V1.wav"])
    assert names(mod2.resolve("Artist", "Viper"))[0] == "Viper SW V3.wav"


def test_must_hint_in_a_filename_beats_the_version_rank(tmp_path):
    # The folder name carries the hint, so both files are eligible; the file that contains it wins over the newer Extended Mix.
    files = ["Artist - Title (Club Mix) SW V1.wav", "Artist - Title (Extended Mix) SW V2.wav"]
    mod = one_folder(tmp_path, "Artist - Title Club Mix [Label] Project", files)
    assert names(mod.resolve("Artist", "Title", "Club Mix")) == files


def test_must_hint_that_is_a_full_filename_beats_the_version_rank(tmp_path):
    # title is "Better" so the exact-title tie-break is not involved: only the must hint's exact tier can put the bare file first.
    mod = one_folder(tmp_path, "Artist - Better When [Label] Project", ["Better When.wav", "Better When (Extended Mix) SW V2.wav"])
    assert names(mod.resolve("Artist", "Better", "Better When")) == ["Better When.wav", "Better When (Extended Mix) SW V2.wav"]


def test_files_that_tie_on_everything_sort_by_path_descending(tmp_path):
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", ["Title A.wav", "Title C.wav", "Title B.wav"])
    assert names(mod.resolve("Artist", "Title")) == ["Title C.wav", "Title B.wav", "Title A.wav"]


@pytest.mark.parametrize("raw,expected", [
    ("02 - Better When", "Better When"), ("02. Better When", "Better When"), ("02_Better When", "Better When"),
    ("02) Better When", "Better When"), ("2) Better When", "Better When"), ("02.Better When", "Better When"),
    ("02 Better When", "Better When"),                       # zero-padded: a track number
    ("7 Seconds", "7 Seconds"), ("100 Ways", "100 Ways"), ("007 Theme", "007 Theme"), ("0 To 100", "0 To 100"),
    ("1999", "1999"), ("24K Magic", "24K Magic"), ("2 Hearts", "2 Hearts"),
])
def test_a_leading_number_is_dropped_only_with_a_separator_or_zero_padded(tmp_path, raw, expected):
    mod, _ = load_resolver(tmp_path, [])
    assert mod.strip_num(raw) == expected


def test_a_bare_leading_number_is_part_of_another_song(tmp_path):                                      # A19
    mod = one_folder(tmp_path, "Artist - Seconds [Label] Project", ["7 Seconds.wav", "Seconds (Extended Mix) SW V2.wav"])
    assert names(mod.resolve("Artist", "Seconds"))[0] == "Seconds (Extended Mix) SW V2.wav"
    assert mod.resolve("Artist", "Seconds", None, "Seconds") == []                     # 7 Seconds is not "Seconds"
    assert names(mod.resolve("Artist", "Seconds", None, "7 Seconds")) == ["7 Seconds.wav"]


def test_asking_for_7_seconds_returns_7_seconds(tmp_path):
    mod = one_folder(tmp_path, "Artist - 7 Seconds [Label] Project", ["7 Seconds.wav", "Seconds (Extended Mix) SW V2.wav"])
    assert names(mod.resolve("Artist", "7 Seconds"))[0] == "7 Seconds.wav"


def test_100_ways_is_not_ways(tmp_path):
    mod = one_folder(tmp_path, "Artist - Ways [Label] Project", ["100 Ways.wav", "Ways SW V1.wav"])
    assert names(mod.resolve("Artist", "Ways"))[0] == "Ways SW V1.wav"
    assert mod.resolve("Artist", "Ways", None, "Ways") == []
    mod2 = one_folder(tmp_path / "b", "Artist - 100 Ways [Label] Project", ["100 Ways.wav", "Ways SW V1.wav"])
    assert names(mod2.resolve("Artist", "100 Ways"))[0] == "100 Ways.wav"


def test_exact_selector_full_stem_beats_the_number_stripped_match(tmp_path):
    mod = one_folder(tmp_path, EP_FOLDER, ["02 Better When.wav", "Better When.wav", "99 Zebra.wav"])
    assert names(mod.resolve("Artist", "Better When", None, "Better When")) == ["Better When.wav"]
    assert names(mod.resolve("Artist", "Better When", None, "02 Better When")) == ["02 Better When.wav"]


def test_exact_selector_falls_back_to_the_number_stripped_name_only_without_a_full_match(tmp_path):
    mod = one_folder(tmp_path, EP_FOLDER, ["02 Better When.wav", "99 Zebra.wav"])
    assert names(mod.resolve("Artist", "Better When", None, "Better When")) == ["02 Better When.wav"]


def test_exact_selector_does_not_return_another_track_number(tmp_path):
    mod = one_folder(tmp_path, "Art - Night EP [Lab] Project", ["01 Intro.wav", "05 Night.wav", "12 Intro.wav"])
    assert names(mod.resolve("Art", "Night", None, "01 Intro")) == ["01 Intro.wav"]
    assert names(mod.resolve("Art", "Night", None, "12 Intro")) == ["12 Intro.wav"]
    assert mod.resolve("Art", "Night", None, "07 Intro") == []            # a number of its own never falls back to another number


@pytest.mark.parametrize("given", [" 02 Better When.wav ", "02 Better When.wav ", "MASTER RENDERS\\02 Better When.wav",
                                   "MASTER RENDERS/02 Better When.wav", "C:\\x\\MASTER RENDERS\\02 Better When", "02  better   WHEN.WAV"])
def test_exact_selector_ignores_spaces_a_folder_part_and_case(tmp_path, given):
    mod = one_folder(tmp_path, EP_FOLDER, EP_FILES)
    assert names(mod.resolve("Artist", "Better When", None, given)) == ["02 Better When.wav"]


def test_exact_selector_does_not_fold_punctuation(tmp_path):
    mod = one_folder(tmp_path, "Artist - Better When [Label] Project", ["Better When Dub.wav"])
    assert mod.resolve("Artist", "Better When", None, "Better When (Dub)") == []
    assert mod.resolve("Artist", "Better When", None, "Better-When_Dub") == []
    assert names(mod.resolve("Artist", "Better When", None, "better when dub")) == ["Better When Dub.wav"]


@pytest.mark.parametrize("given", ["!!!", " ", "\\", "/"])
def test_exact_selector_with_nothing_to_match_returns_nothing(tmp_path, given):
    mod = one_folder(tmp_path, EP_FOLDER, EP_FILES)
    assert mod.resolve("Artist", "Better When", None, given) == []


# --- round 3: junk files, an exact name with its own number, remix spellings, a known limit ----------------------------

CAFE = "Caf" + chr(233)          # the accented e is built here so that this file stays ASCII


@pytest.mark.parametrize("artist,folder,title,files,first", [
    pytest.param("B03", "B03 - Cafe [Lab] Project", "Cafe", [CAFE + " (Extended Mix) SW V2.wav", "Cafe (Acapella).wav"],
                 CAFE + " (Extended Mix) SW V2.wav", id="B03-acapella-never-first"),
    pytest.param("B04", "B04 - Love & Hate [Lab] Project", "Love & Hate",
                 ["Love And Hate (Extended Mix) SW V3.wav", "Love & Hate (Dub).wav"],
                 "Love And Hate (Extended Mix) SW V3.wav", id="B04-dub-never-first"),
    pytest.param("B06", "B06 - Title [Lab] Project", "Title", ["Final Master SW V2.wav", "Title Stems.wav"],
                 "Final Master SW V2.wav", id="B06-stems-never-first"),
    pytest.param("B28", "B28 - Release Me [Lab] Project", "Release Me",
                 ["Release (Extended Mix) SW V2.wav", "Release Me (Instrumental).wav"],
                 "Release (Extended Mix) SW V2.wav", id="B28-instrumental-never-first"),
    pytest.param("A21", "A21 - Title EP [Lab] Project", "Title", ["A21 - Title (Dub).wav", "A21 - Other (Extended Mix).wav"],
                 "A21 - Other (Extended Mix).wav", id="A21-dub-of-the-title-is-not-the-pick"),
])
def test_a_junk_file_is_returned_only_when_nothing_else_is(tmp_path, artist, folder, title, files, first):
    # Never acapella / instrumental / dub / stems: the old rank buried them and the named-track tier must not lift them back up.
    mod = one_folder(tmp_path, folder, files)
    r = names(mod.resolve(artist, title))
    assert r[0] == first
    assert len(r) == 2                                   # the junk file is still listed, last


def test_a_folder_that_holds_only_junk_still_returns_it(tmp_path):
    mod = one_folder(tmp_path, "Artist - Better When [Label] Project", ["Better When (Dub).wav"])
    assert names(mod.resolve("Artist", "Better When")) == ["Better When (Dub).wav"]
    mod2 = one_folder(tmp_path / "b", "Artist - Title [Label] Project", ["Title (Instrumental).wav", "Title (Acapella).wav"])
    assert sorted(names(mod2.resolve("Artist", "Title"))) == ["Title (Acapella).wav", "Title (Instrumental).wav"]


def test_a_title_that_contains_a_junk_word_is_not_junk_for_that_word(tmp_path):
    # "dub" is inside Dubai and "stems" inside Systems: asking for that title switches the word off, other junk words stay on.
    files = ["Dubai Nights.wav", "Other (Extended Mix) SW V2.wav", "Dubai Nights (Instrumental).wav"]
    mod = one_folder(tmp_path, "Artist - Dubai Nights EP [Label] Project", files)
    assert names(mod.resolve("Artist", "Dubai Nights")) == ["Dubai Nights.wav", "Other (Extended Mix) SW V2.wav",
                                                           "Dubai Nights (Instrumental).wav"]
    mod2 = one_folder(tmp_path / "b", "Artist - Systems EP [Label] Project", ["Systems.wav", "Other (Extended Mix) SW V2.wav"])
    assert names(mod2.resolve("Artist", "Systems"))[0] == "Systems.wav"


def test_a_must_hint_that_names_the_junk_word_is_respected(tmp_path):
    files = ["Artist - Title (Extended Mix) SW V2.wav", "Artist - Title (Dub Mix).wav"]
    mod = one_folder(tmp_path, "Artist - Title Dub Mix [Label] Project", files)
    assert names(mod.resolve("Artist", "Title", "Dub Mix"))[0] == "Artist - Title (Dub Mix).wav"
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title (Extended Mix) SW V2.wav"


def test_junk_is_judged_by_the_same_pattern_rank_uses(tmp_path):
    mod, _ = load_resolver(tmp_path, [])
    for junk in ("Title (Acapella).wav", "Title Instrumental.wav", "Title (Dub).wav", "Title Stems.wav", "Title.alp.wav",
                 "Title (Dubstramental).wav", "Title vocal remix only.wav"):
        assert mod.rank(junk) <= -100 + mod.rank("Title.wav") and mod.JUNK.search(junk.lower()), junk
    assert not mod.JUNK.search("title (extended mix) sw v2.wav") and mod.rank("Title (Extended Mix) SW V2.wav") > 0


def test_exact_selector_with_a_track_number_of_its_own_never_falls_back(tmp_path):
    mod = one_folder(tmp_path, "Artist - Dance [Label] Project", ["02 Dance (Extended Mix) SW V1.wav", "Dance (Extended Mix) SW V2.wav"])
    assert mod.resolve("Artist", "Dance", None, "05 Dance (Extended Mix) SW V1") == []
    assert names(mod.resolve("Artist", "Dance", None, "02 Dance (Extended Mix) SW V1")) == ["02 Dance (Extended Mix) SW V1.wav"]
    # no number typed: the number-stripped fallback still finds the numbered file
    assert names(mod.resolve("Artist", "Dance", None, "Dance (Extended Mix) SW V1")) == ["02 Dance (Extended Mix) SW V1.wav"]
    assert names(mod.resolve("Artist", "Dance", None, "Dance (Extended Mix) SW V2")) == ["Dance (Extended Mix) SW V2.wav"]


def test_exact_selector_name_with_its_own_number_does_not_match_a_double_numbered_file(tmp_path):
    # "02 Name" against "02 02 Name.wav": one number stripped from the file would equal what was typed, but the typed name
    # already has a number of its own, so there is no fallback.
    mod = one_folder(tmp_path, "Artist - Name [Label] Project", ["02 02 Name.wav"])
    assert mod.resolve("Artist", "Name", None, "02 Name") == []
    assert names(mod.resolve("Artist", "Name", None, "02 02 Name")) == ["02 02 Name.wav"]


@pytest.mark.parametrize("phrase", ["V.I.P.", "Re-Work", "Re-Fix", "Remode"])
def test_a_title_that_has_the_spelling_names_the_variant(tmp_path, phrase):
    files = [f"Artist - Title ({phrase}) SW V2.wav", "Artist - Title SW V3.wav"]
    mod = one_folder(tmp_path, f"Artist - Title {phrase} [Label] Project", files)
    assert names(mod.resolve("Artist", f"Title {phrase}"))[0] == files[0]


def test_a_must_hint_with_the_spelling_picks_the_variant(tmp_path):
    files = ["C02 - Title (Extended Mix) SW V1.wav", "C02 - Title (Joe Re-Work) (Extended Mix) SW V3.wav"]
    mod = one_folder(tmp_path, "C02 - Title [Lab] Project", files)
    assert names(mod.resolve("C02", "Title", "Re-Work")) == [files[1]]
    assert names(mod.resolve("C02", "Title"))[0] == files[0]


def test_extended_original_and_other_mix_names_are_not_remix_markers(tmp_path):
    mod, _ = load_resolver(tmp_path, [])
    for text in ("title extended mix", "title original mix", "title club mix", "title radio edit", "title joe mix",
                 "title 24 bit master", "title sw v2", "title master amended v3"):
        assert mod.variants(text) == set(), text
    assert mod.variants("title joe v i p mix") == {"v i p"} and mod.variants("title re work") == {"re work"}
    assert mod.variants("title re fix") == {"re fix"} and mod.variants("title joe remode") == {"remode"}
    assert mod.variants("title vip room vipers") == {"vip"}


def test_b02_the_newest_render_without_the_title_words_now_wins(tmp_path):
    # B02 was a known limit, left as a judgement call for Sam. On 2026-11-05 he decided: the newest version always wins. The
    # version-only MASTER AMENDED V3 is the newest version of the one track in this folder, so it beats the older titled file.
    mod = one_folder(tmp_path, "B02 - Title [Lab] Project", ["MASTER AMENDED V3.wav", "B02 - Title 24 Bit MASTER.wav"])
    assert names(mod.resolve("B02", "Title"))[0] == "MASTER AMENDED V3.wav"


def test_known_limit_a_typo_in_the_title_still_loses_to_the_older_correct_name(tmp_path):
    # B05: a misspelt title is not a version-only name (it has a word of its own), so no heuristic is added for it.
    mod2 = one_folder(tmp_path / "b", "B05 - Better When [Lab] Project",
                      ["Better Whn (Extended Mix) SW V3.wav", "Better When (Extended Mix) SW V1.wav"])
    assert names(mod2.resolve("B05", "Better When"))[0] == "Better When (Extended Mix) SW V1.wav"


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


def _run_cli_raw(tools: Path, picks):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-I", str(tools / "resolve_masters.py"), json.dumps(picks)],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(tools), env=env, timeout=60)


def test_cli_converts_non_string_elements_with_str(tmp_path):
    lib = tmp_path / "Library"
    folder = make_folder(lib, "Artist - 7 [Label] Project", ["7.wav", "Other.wav"])
    _, tools = load_resolver(tmp_path, [("Artist - 7 [Label] Project", folder)])
    done = _run_cli_raw(tools, [["Artist", 7, None, 7]])                  # integer title and integer exact filename
    assert done.returncode == 0, done.stderr
    assert "Artist - 7:" in done.stdout and "7.wav" in done.stdout and "Other.wav" not in done.stdout
    done = _run_cli_raw(tools, [["Artist", "7", 7]])                      # integer must
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("picks,needle", [
    pytest.param([["Artist", "Better When", None, ""]], "exact filename", id="empty-exact"),
    pytest.param([["Artist", "Better When", None, "  "]], "exact filename", id="blank-exact"),
    pytest.param([["Artist", "Better When"], ["Artist", "Better When", None, ""]], "exact filename", id="second-pick-empty-exact"),
    pytest.param([["Artist", "Better When", None, "02 Better When", "extra"]], "2 to 4 elements", id="fifth-element"),
    pytest.param([["Artist"]], "2 to 4 elements", id="one-element"),
    pytest.param([["Artist", None]], "must not be null", id="null-title"),
    pytest.param(["Artist"], "must be a list", id="pick-is-a-string"),
    pytest.param({"a": 1}, "list of picks", id="not-a-list"),
    pytest.param([["Artist", ""]], "title (2nd element)", id="empty-title"),
    pytest.param([["Artist", "  "]], "title (2nd element)", id="blank-title"),
    pytest.param([["Artist", "!!!"]], "title (2nd element)", id="punctuation-only-title"),
    pytest.param([["", "Title"]], "artist (1st element)", id="empty-artist"),
    pytest.param([["  ", "Title"]], "artist (1st element)", id="blank-artist"),
    pytest.param([["!!!", "Title"]], "artist (1st element)", id="punctuation-only-artist"),
])
def test_cli_rejects_bad_input_with_one_line_and_a_non_zero_exit(tmp_path, picks, needle):
    lib = tmp_path / "Library"
    folder = make_folder(lib, EP_FOLDER, EP_FILES)
    _, tools = load_resolver(tmp_path, [(EP_FOLDER, folder)])
    done = _run_cli_raw(tools, picks)
    assert done.returncode != 0
    assert done.stdout == ""                                              # checked before anything is printed
    err = done.stderr.strip()
    assert len(err.splitlines()) == 1 and needle in err, err


# --- the newest version always wins (Sam, 2026-11-05) ----------------------------------------------------------------

def _age(path: Path, seconds: int) -> None:
    """Set one file's modification time to BASE_TIME + seconds (a bigger number is newer)."""
    os.utime(path, (BASE_TIME + seconds, BASE_TIME + seconds))


def _folder_and_tools(tmp_path: Path, folder_name: str, files):
    lib = tmp_path / "Library"
    folder = make_folder(lib, folder_name, files)
    mod, tools = load_resolver(tmp_path, [(folder_name, folder)])
    return mod, tools, folder / "MASTER RENDERS"


@pytest.mark.parametrize("name,expected", [
    ("MASTER AMENDED V3.wav", True), ("SW V3.wav", True), ("24 Bit MASTER.wav", True), ("Final Master SW V2.wav", True),
    ("master.wav", True), ("02 - SW V2.wav", True), ("01 MASTER AMENDED.WAV", True), ("2) SW V1.wav", True),
    ("Artist - Title SW V3.wav", False), ("Master A SW V1.wav", False), ("Artist - SW V3.wav", False), ("SW V3 Dub.wav", False),
    ("MASTER AMENDED VERSION.wav", False), ("Mastering.wav", False), (".wav", False), ("02.wav", False), ("V3.wav", False),
    ("Extended SW V3.wav", True), ("Original Mix SW V2.wav", True), ("Radio Edit SW V2.wav", True),
    ("Club Mix SW V2.wav", False), ("MASTER AMENDED V.wav", False),
])
def test_what_counts_as_a_version_only_name(tmp_path, name, expected):
    # Nothing may be left of the name after the version words, a leading track number and .wav, and at least one version word is there.
    mod, _ = load_resolver(tmp_path, [])
    assert bool(mod.vonly(name)) is expected


def test_a_version_only_file_beats_an_older_titled_version_in_a_one_track_folder(tmp_path):              # (a)
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", ["Artist - Title SW V1.wav", "MASTER AMENDED V3.wav"])
    assert names(mod.resolve("Artist", "Title")) == ["MASTER AMENDED V3.wav", "Artist - Title SW V1.wav"]


def test_sw_v3_alone_in_the_name_beats_sw_v1_with_the_title(tmp_path):                                    # (b)
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", ["Artist - Title SW V1.wav", "SW V3.wav"])
    assert names(mod.resolve("Artist", "Title")) == ["SW V3.wav", "Artist - Title SW V1.wav"]
    # the other way round the titled file is the newer version, so it keeps winning
    mod2 = one_folder(tmp_path / "b", "Artist - Title [Label] Project", ["Artist - Title SW V3.wav", "SW V1.wav"])
    assert names(mod2.resolve("Artist", "Title")) == ["Artist - Title SW V3.wav", "SW V1.wav"]


def test_a_version_only_file_takes_the_best_mix_preference_of_its_folder(tmp_path):                       # (c)
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", ["Artist - Title (Extended Mix) SW V1.wav", "MASTER AMENDED V3.wav"])
    r = mod.resolve("Artist", "Title")
    assert names(r) == ["MASTER AMENDED V3.wav", "Artist - Title (Extended Mix) SW V1.wav"]
    assert [s for s, _ in r] == [51, 41]            # the score shown: 40 (Extended) + 3 + 8 for AMENDED V3, and 40 + 1 for SW V1
    # with an Extended and a Radio Edit in the folder it takes the Extended preference, so it also beats the Radio Edit at SW V5
    files = ["Title (Radio Edit) SW V5.wav", "Title (Extended Mix) SW V1.wav", "MASTER AMENDED V3.wav"]
    mod2 = one_folder(tmp_path / "b", "Artist - Title [Label] Project", files)
    assert names(mod2.resolve("Artist", "Title")) == ["MASTER AMENDED V3.wav", "Title (Extended Mix) SW V1.wav", "Title (Radio Edit) SW V5.wav"]
    # with only a Radio Edit it has that preference, and is still the newest version of the track
    mod3 = one_folder(tmp_path / "c", "Artist - Title [Label] Project", ["Title (Radio Edit) SW V1.wav", "MASTER AMENDED V3.wav"])
    assert names(mod3.resolve("Artist", "Title")) == ["MASTER AMENDED V3.wav", "Title (Radio Edit) SW V1.wav"]


def test_version_only_names_keep_sams_order(tmp_path):
    # 24 Bit MASTER < MASTER AMENDED < AMENDED V2 < AMENDED V3, and SW V1 < SW V2 < SW V3
    mod = one_folder(tmp_path, "Artist - Title [Label] Project",
                     ["Artist - Title.wav", "24 Bit MASTER.wav", "MASTER AMENDED.wav", "MASTER AMENDED V2.wav"])
    assert names(mod.resolve("Artist", "Title")) == ["MASTER AMENDED V2.wav", "MASTER AMENDED.wav", "24 Bit MASTER.wav"]
    mod2 = one_folder(tmp_path / "b", "Artist - Title [Label] Project", ["Artist - Title SW V1.wav", "SW V2.wav", "SW V3.wav"])
    assert names(mod2.resolve("Artist", "Title")) == ["SW V3.wav", "SW V2.wav", "Artist - Title SW V1.wav"]
    mod3 = one_folder(tmp_path / "c", "Artist - Title [Label] Project", ["Artist - Title.wav", "MASTER AMENDED V3.wav", "MASTER AMENDED V2.wav"])
    assert names(mod3.resolve("Artist", "Title")) == ["MASTER AMENDED V3.wav", "MASTER AMENDED V2.wav", "Artist - Title.wav"]


def test_in_an_ep_folder_a_version_only_file_does_not_win_but_the_command_line_says_so(tmp_path):         # (d)
    files = ["Artist - Change The Situation (Extended Mix) SW V2.wav", "Artist - Better When (Extended Mix) SW V2.wav",
             "Artist - Zebra Dance (Extended Mix) SW V2.wav", "MASTER AMENDED V3.wav"]
    mod, tools, _ = _folder_and_tools(tmp_path, "Artist - Better When EP [Label] Project", files)
    assert names(mod.resolve("Artist", "Better When"))[0] == "Artist - Better When (Extended Mix) SW V2.wav"
    out = _run_cli(tools, [["Artist", "Better When"]])
    lines = out.splitlines()
    assert lines[0] == "Artist - Better When:" and lines[1].endswith("Artist - Better When (Extended Mix) SW V2.wav")
    notes = [ln for ln in lines if ln.strip().startswith("NOTE")]
    assert len(notes) == 1 and "not chosen" in notes[0] and "the folder name is not exactly artist and title" in notes[0], out
    assert "several tracks" not in out                            # the old wording said so even when the folder held one track
    at = lines.index(notes[0])
    assert at > 1 and lines[at + 1].strip().endswith("MASTER AMENDED V3.wav")        # the file on its own line, after the result lines


def test_the_note_names_every_newer_version_only_file_once(tmp_path):
    files = ["Artist - Change The Situation SW V1.wav", "Artist - Better When SW V1.wav", "MASTER AMENDED V3.wav", "SW V4.wav"]
    _, tools, _ = _folder_and_tools(tmp_path, "Artist - Better When EP [Label] Project", files)
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Better When"]]))
    assert len(heads) == 1 and len(listed) == 2, (heads, listed)                 # one NOTE line, then one line per file
    assert listed[0].endswith("MASTER AMENDED V3.wav") and listed[1].endswith("SW V4.wav")      # the newest version (AMENDED V3 = 11) first


def test_cli_note_only_when_a_newer_version_only_file_was_passed_over(tmp_path):
    name = "Artist - Better When EP [Label] Project"
    files = ["Artist - Change The Situation SW V2.wav", "Artist - Better When SW V2.wav", "SW V1.wav"]
    _, tools, mr = _folder_and_tools(tmp_path, name, files)
    pick = [["Artist", "Better When"]]
    assert "NOTE" not in _run_cli(tools, pick)                    # SW V1 is older than the chosen SW V2
    _write_wav(mr / "SW V2.wav")
    _age(mr / "SW V2.wav", -100)
    assert "NOTE" not in _run_cli(tools, pick)                    # the same version and an older date
    _age(mr / "SW V2.wav", 100)
    heads, listed = _note_block(_run_cli(tools, pick))                                  # the same version and a newer date
    assert len(heads) == 1 and len(listed) == 1 and listed[0].endswith("SW V2.wav"), (heads, listed)
    assert "NOTE" not in _run_cli(tools, [["Artist", "Better When", None, "Artist - Better When SW V2"]])   # an exact name was asked for
    assert "NOTE" not in _run_cli(tools, [["Artist", "Better When", None, "No Such File"]])


def test_cli_prints_no_note_when_the_version_only_file_was_chosen(tmp_path):
    _, tools, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", ["Artist - Title SW V1.wav", "MASTER AMENDED V3.wav"])
    out = _run_cli(tools, [["Artist", "Title"]])
    assert out.splitlines()[1].endswith("MASTER AMENDED V3.wav") and "NOTE" not in out


def test_at_the_same_version_the_newer_date_modified_wins(tmp_path):                                      # (e)
    mod, _, mr = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", ["Title A.wav", "Title B.wav"])
    _age(mr / "Title A.wav", 100)
    _age(mr / "Title B.wav", 0)
    assert names(mod.resolve("Artist", "Title")) == ["Title A.wav", "Title B.wav"]
    _age(mr / "Title A.wav", 0)
    _age(mr / "Title B.wav", 100)                                 # the dates swapped: the other file wins (path order alone gave B always)
    assert names(mod.resolve("Artist", "Title")) == ["Title B.wav", "Title A.wav"]
    # a version-only file and a titled file at the same version are told apart by the date too
    mod2, _, mr2 = _folder_and_tools(tmp_path / "b", "Artist - Title [Label] Project", ["Artist - Title SW V3.wav", "SW V3.wav"])
    _age(mr2 / "SW V3.wav", 100)
    assert names(mod2.resolve("Artist", "Title")) == ["SW V3.wav", "Artist - Title SW V3.wav"]
    _age(mr2 / "SW V3.wav", -100)
    assert names(mod2.resolve("Artist", "Title")) == ["Artist - Title SW V3.wav", "SW V3.wav"]


def test_the_date_never_beats_a_higher_version_or_the_exact_name(tmp_path):
    mod, _, mr = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", ["Artist - Title SW V2.wav", "Artist - Title SW V1.wav"])
    _age(mr / "Artist - Title SW V1.wav", 1000)                   # the older version was saved last: the newer version still wins
    assert names(mod.resolve("Artist", "Title")) == ["Artist - Title SW V2.wav", "Artist - Title SW V1.wav"]
    mod2, _, mr2 = _folder_and_tools(tmp_path / "b", "Artist - Title [Label] Project", ["Title.wav", "Title Remaster.wav"])
    _age(mr2 / "Title Remaster.wav", 1000)                        # at one version the exact name is still ahead of the date
    assert names(mod2.resolve("Artist", "Title")) == ["Title.wav", "Title Remaster.wav"]


def test_radio_edit_at_a_newer_version_still_loses_to_the_extended_mix(tmp_path):                         # (f)
    files = ["Title (Radio Edit) SW V5.wav", "Title (Extended Mix) SW V1.wav"]
    mod, _, mr = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", files)
    assert names(mod.resolve("Artist", "Title")) == ["Title (Extended Mix) SW V1.wav", "Title (Radio Edit) SW V5.wav"]
    _age(mr / files[0], 1000)
    assert names(mod.resolve("Artist", "Title"))[0] == "Title (Extended Mix) SW V1.wav"        # and a newer date does not change it


def test_a_big_version_number_cannot_outweigh_a_mix_preference(tmp_path):
    # Extended (+40) and Original Mix (+30) are about which mix, not which version: the two are compared, never added.
    files = ["Title (Original Mix) SW V15.wav", "Title (Extended Mix) SW V1.wav"]
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", files)
    assert names(mod.resolve("Artist", "Title")) == ["Title (Extended Mix) SW V1.wav", "Title (Original Mix) SW V15.wav"]
    # inside one mix the higher version always wins
    mod2 = one_folder(tmp_path / "b", "Artist - Title [Label] Project", ["Title (Extended Mix) SW V1.wav", "Title (Extended Mix) SW V2.wav"])
    assert names(mod2.resolve("Artist", "Title")) == ["Title (Extended Mix) SW V2.wav", "Title (Extended Mix) SW V1.wav"]


def test_a_newer_plain_version_does_not_beat_an_older_extended_mix(tmp_path):
    # Sam, 2026-11-05: "The newest version should always win." For a DJ mix the Extended kind comes first, then the newest version
    # WITHIN it (his files exist as "Extended ... SW V3"): a newer plain version does NOT beat an older Extended.
    # Only a version-only file inherits a mix preference; a titled file without a mix marker has none.
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", ["Title SW V3.wav", "Title (Extended Mix) SW V1.wav"])
    assert names(mod.resolve("Artist", "Title")) == ["Title (Extended Mix) SW V1.wav", "Title SW V3.wav"]


def test_a_version_only_file_never_makes_the_remix_or_the_original_the_answer(tmp_path):                   # (g)
    orig, remix, vonly_name = "Artist - Title SW V1.wav", "Artist - Title (Someone Remix) SW V2.wav", "MASTER AMENDED V3.wav"
    # a folder named for the original that also holds a remix: the original is the answer, the version-only file is not placed
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", [orig, remix, vonly_name])
    assert names(mod.resolve("Artist", "Title"))[0] == orig
    # a folder named for the remix, the remix asked for in the hint: the remix is the answer, and the version-only file is not
    # placed ahead of the original (the folder is not named for the title that was asked)
    mod2 = one_folder(tmp_path / "b", "Artist - Title Someone Remix [Label] Project", [orig, remix, vonly_name])
    assert names(mod2.resolve("Artist", "Title"))[0] == orig            # a folder NAMED for the remix never stands in for the original
    assert names(mod2.resolve("Artist", "Title", "Someone Remix")) == [remix, orig, vonly_name]


def test_junk_stays_last_even_when_it_is_the_newest_and_the_highest_version(tmp_path):                    # (h)
    junk = "Artist - Title (Instrumental) SW V9.wav"
    mod, _, mr = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", ["Artist - Title SW V1.wav", junk, "MASTER AMENDED V3.wav"])
    _age(mr / junk, 1000)
    r = names(mod.resolve("Artist", "Title"))
    assert len(r) == 3 and r[-1] == junk
    # Junk with the best marks (Extended + Original Mix, -100 for junk = -30) is still above a Radio Edit (-40) on the mix alone:
    # only the junk tier itself keeps it last.
    junk2 = "Title (Extended Original Mix) (Instrumental) SW V9.wav"
    mod2, _, mr2 = _folder_and_tools(tmp_path / "b", "Artist - Title [Label] Project", ["Title (Radio Edit) SW V1.wav", junk2])
    _age(mr2 / junk2, 1000)
    assert names(mod2.resolve("Artist", "Title")) == ["Title (Radio Edit) SW V1.wav", junk2]


def test_the_must_hint_and_the_exact_name_still_beat_a_version_only_file(tmp_path):
    files = ["Artist - Title (Club Mix) SW V1.wav", "Artist - Title (Extended Mix) SW V2.wav", "MASTER AMENDED V3.wav"]
    mod = one_folder(tmp_path, "Artist - Title Club Mix [Label] Project", files)
    assert names(mod.resolve("Artist", "Title", "Club Mix"))[0] == "Artist - Title (Club Mix) SW V1.wav"
    assert names(mod.resolve("Artist", "Title", None, "Artist - Title (Extended Mix) SW V2")) == ["Artist - Title (Extended Mix) SW V2.wav"]
    assert names(mod.resolve("Artist", "Title", None, "MASTER AMENDED V3")) == ["MASTER AMENDED V3.wav"]


# --- round 2: the one-track decision comes from the folder, strays, amended without digits, a NOTE for every loser -------

def _library(tmp_path: Path, spec):
    """Several project folders at once: spec is {folder name: [files]}; returns (module, tools folder)."""
    lib = tmp_path / "Library"
    folders = [(name, make_folder(lib, name, files)) for name, files in spec.items()]
    return load_resolver(tmp_path, folders)


def _note_block(out: str):
    """From the command line output: the NOTE header lines, and the file lines (6 spaces in) listed under them."""
    lines = out.splitlines()
    return [ln.strip() for ln in lines if ln.strip().startswith("NOTE")], [ln.strip() for ln in lines if ln.startswith("      ")]


def test_r2_a_longer_title_folder_never_stands_in_for_the_shorter_title(tmp_path):
    mod, tools = _library(tmp_path, {
        "Getto - Love [Label] Project": ["Getto - Love SW V2.wav"],
        "Getto - Love Me Right [Label] Project": ["Getto - Love Me Right SW V1.wav", "MASTER AMENDED V3.wav"]})
    assert names(mod.resolve("Getto", "Love"))[0] == "Getto - Love SW V2.wav"
    heads, listed = _note_block(_run_cli(tools, [["Getto", "Love"]]))              # nothing is promoted from the Love Me Right folder, and its newer file is named
    assert len(heads) == 1 and "the folder name is not exactly artist and title" in heads[0]
    assert len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav") and "Love Me Right" in listed[0]


def test_r1b_a_remix_folder_with_only_version_only_files_never_stands_in_for_the_original(tmp_path):
    mod, tools = _library(tmp_path, {
        "Artist - Title [Label] Project": ["Artist - Title SW V1.wav"],
        "Artist - Title (Someone Remix) [Label] Project": ["MASTER AMENDED V3.wav", "24 Bit MASTER.wav"]})
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))            # nothing is promoted from the remix folder, and its newer file is named
    assert len(heads) == 1 and "the folder name is not exactly artist and title" in heads[0]
    assert len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav") and "(Someone Remix)" in listed[0]


def test_r3b_a_folder_for_a_longer_word_never_stands_in_for_the_title(tmp_path):
    mod, tools = _library(tmp_path, {
        "Artist - Love [Label] Project": ["Artist - Love SW V1.wav"],
        "Artist - Lovely Day [Label] Project": ["MASTER AMENDED V3.wav", "SW V2.wav"]})
    assert names(mod.resolve("Artist", "Love"))[0] == "Artist - Love SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Love"]]))              # matched by substring only: not promoted, and said so
    assert len(heads) == 1 and "the folder name does not name this track" in heads[0]
    assert len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav") and "Lovely Day" in listed[0]


def test_r3c_a_folder_for_another_artist_never_stands_in_for_the_artist(tmp_path):
    mod, tools = _library(tmp_path, {
        "Ian - Title [Label] Project": ["Ian - Title SW V1.wav"],
        "Brian - Title [Label] Project": ["MASTER AMENDED V3.wav"]})
    assert names(mod.resolve("Ian", "Title"))[0] == "Ian - Title SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Ian", "Title"]]))
    assert len(heads) == 1 and "the folder name does not name this track" in heads[0]
    assert len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav") and "Brian" in listed[0]


def test_two_different_titled_tracks_in_one_folder_are_not_one_track(tmp_path):
    # Love and Love Me Right both carry the title Love: the folder is named for Love, but it is not one track.
    files = ["Artist - Love SW V1.wav", "Artist - Love Me Right SW V1.wav", "MASTER AMENDED V3.wav"]
    mod, tools, _ = _folder_and_tools(tmp_path, "Artist - Love [Label] Project", files)
    assert names(mod.resolve("Artist", "Love"))[0] == "Artist - Love SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Love"]]))
    assert len(heads) == 1 and len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")


@pytest.mark.parametrize("stray", ["Acapella.wav", "Instrumental SW V2.wav"])
def test_a_junk_file_is_ignored_and_does_not_break_an_exact_single_track_folder(tmp_path, stray):
    files = ["Artist - Title SW V1.wav", "MASTER AMENDED V3.wav", stray]
    mod, tools, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", files)
    assert names(mod.resolve("Artist", "Title"))[0] == "MASTER AMENDED V3.wav"       # the newest version of the one track
    assert _note_block(_run_cli(tools, [["Artist", "Title"]])) == ([], [])             # nothing was passed over


def test_a_scratch_file_makes_the_folder_not_exact_so_nothing_is_promoted(tmp_path):
    files = ["Artist - Title SW V1.wav", "MASTER AMENDED V3.wav", "test bounce.wav"]
    mod, tools, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", files)
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))
    assert len(heads) == 1 and "the folder may hold other tracks" in heads[0] and len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")


def test_r16_a_remix_folder_with_a_remix_and_a_junk_file_never_stands_in_for_the_original(tmp_path):
    mod, tools = _library(tmp_path, {
        "Artist - Title [Label] Project": ["Artist - Title SW V1.wav"],
        "Artist - Title (Someone Remix) [Label] Project": ["Artist - Title (Someone Remix) SW V2.wav", "Acapella.wav", "MASTER AMENDED V3.wav"]})
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))           # nothing is promoted from the remix folder, and its newer file is named
    assert len(heads) == 1 and "the folder name is not exactly artist and title" in heads[0]
    assert len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav") and "(Someone Remix)" in listed[0]


def test_in_a_folder_named_for_the_remix_nothing_is_promoted_and_the_newer_file_is_named(tmp_path):
    # The folder name has parentheses, so it is not exactly artist - title, even when the title asked for says the remix.
    files = ["Artist - Title (Someone Remix) SW V1.wav", "MASTER AMENDED V3.wav"]
    mod, tools, _ = _folder_and_tools(tmp_path, "Artist - Title (Someone Remix) [Label] Project", files)
    assert names(mod.resolve("Artist", "Title (Someone Remix)"))[0] == "Artist - Title (Someone Remix) SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title (Someone Remix)"]]))
    assert len(heads) == 1 and "the folder name is not exactly artist and title" in heads[0]
    assert len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")


def test_a_newer_file_with_a_typo_in_the_title_never_wins_and_is_named(tmp_path):
    # The typo file has a word of its own, so it is not version-only and the folder is not exact: the right track is chosen. Round 4 said names
    # that hold other words are not listed; now a file with a version token in its name is listed too, in a folder named exactly artist - title.
    files = ["Artist - Title SW V1.wav", "Artist - Ttile SW V3.wav", "MASTER AMENDED V3.wav"]
    mod, tools, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", files)
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))
    assert len(heads) == 2 and len(listed) == 2, (heads, listed)                  # the version-only file (may hold other tracks) and the typo (cannot tell)
    assert sorted(os.path.basename(x) for x in listed) == ["Artist - Ttile SW V3.wav", "MASTER AMENDED V3.wav"]
    assert any("cannot tell which track it belongs to" in h for h in heads) and any("may hold other tracks" in h for h in heads)


@pytest.mark.parametrize("name,score", [("MASTER AMENDED VERSION.wav", 6), ("Title (Amended Vocal) SW V2.wav", 8), ("MASTER AMENDED V.wav", 6)])
def test_amended_without_digits_is_plain_amended_and_never_kills_the_lookup(tmp_path, name, score):
    mod, tools, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", ["Artist - Title SW V1.wav", name])
    assert mod.ver(name) == score                                  # no digits after "amended v": plain amended (+6), not a crash
    assert sorted(names(mod.resolve("Artist", "Title"))) == sorted(["Artist - Title SW V1.wav", name])
    assert _run_cli(tools, [["Artist", "Title"]]).startswith("Artist - Title:")


def test_a_version_only_file_that_lost_in_a_one_track_folder_still_gets_a_note(tmp_path):
    # Folder A is one track (a Radio Edit and its newer version-only file); folder B has the Extended Mix, which comes first.
    mod, tools = _library(tmp_path, {
        "Artist - Title [Label A] Project": ["Artist - Title (Radio Edit) SW V1.wav", "MASTER AMENDED V3.wav"],
        "Artist - Title [Label B] Project": ["Artist - Title (Extended Mix) SW V1.wav"]})
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title (Extended Mix) SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))
    assert len(heads) == 1 and "ranked lower" in heads[0]
    assert len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav") and "Label A" in listed[0]


def test_a_must_hint_does_not_silence_the_note(tmp_path):
    files = ["Artist - Title (Club Mix) SW V1.wav", "Artist - Title (Extended Mix) SW V2.wav", "MASTER AMENDED V3.wav"]
    _, tools, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", files)
    out = _run_cli(tools, [["Artist", "Title", "Club Mix"]])
    assert out.splitlines()[1].endswith("Artist - Title (Club Mix) SW V1.wav")          # the hint decides, as before
    heads, listed = _note_block(out)
    assert len(heads) == 1 and len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")


def test_an_ep_folder_still_gives_the_named_track_and_a_note_for_the_version_only_file(tmp_path):
    files = ["01 Change The Situation.wav", "02 Better When.wav", "03 Other Thing.wav", "MASTER AMENDED V3.wav"]
    mod, tools, _ = _folder_and_tools(tmp_path, EP_FOLDER, files)
    assert names(mod.resolve("Artist", "Better When"))[0] == "02 Better When.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Better When"]]))
    assert len(heads) == 1 and len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")


@pytest.mark.parametrize("files,first", [
    pytest.param(["Artist - Title SW V1.wav", "Artist - Demo Days SW V1.wav", "MASTER AMENDED V3.wav"], "Artist - Title SW V1.wav", id="demo-days"),
    pytest.param(["01 Artist - Title.wav", "02 Artist - Rough Diamond.wav", "MASTER AMENDED V3.wav"], "01 Artist - Title.wav", id="rough-diamond"),
])
def test_a_real_track_with_a_scratch_word_in_its_name_is_another_track(tmp_path, files, first):
    mod, tools, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", files)
    assert names(mod.resolve("Artist", "Title"))[0] == first                       # the folder is an EP: the version-only file is not placed
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))
    assert len(heads) == 1 and len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")


def test_d2c_love_me_right_in_the_love_folder_never_stands_in_for_love(tmp_path):
    mod, tools = _library(tmp_path, {
        "Artist - Love [Label] Project": ["Artist - Love Me Right (Extended Mix) SW V1.wav", "MASTER AMENDED V3.wav"],
        "Artist - Love [Other] Project": ["Artist - Love (Extended Mix) SW V2.wav"]})
    assert names(mod.resolve("Artist", "Love"))[0] == "Artist - Love (Extended Mix) SW V2.wav"


def test_d2d_a_file_of_another_artist_in_the_folder_never_makes_it_one_track(tmp_path):
    mod, _ = _library(tmp_path, {
        "Ian - Title [Label] Project": ["Brian - Title SW V1.wav", "MASTER AMENDED V3.wav"],
        "Ian - Title [Other] Project": ["Ian - Title SW V2.wav"]})
    assert names(mod.resolve("Ian", "Title"))[0] == "Ian - Title SW V2.wav"


def test_a_junk_file_that_carries_the_title_does_not_break_a_one_track_folder(tmp_path):
    files = ["Artist - Title SW V1.wav", "Artist - Title (Instrumental) SW V2.wav", "MASTER AMENDED V3.wav"]
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", files)
    assert names(mod.resolve("Artist", "Title"))[0] == "MASTER AMENDED V3.wav"            # junk is left out of the decision, it does not split the track


def test_a_folder_named_for_the_original_that_holds_only_a_remix_is_not_one_track(tmp_path):
    mod, _ = _library(tmp_path, {
        "Artist - Title [Label A] Project": ["Artist - Title (Remix) SW V1.wav", "MASTER AMENDED V3.wav"],
        "Artist - Title [Label B] Project": ["Artist - Title SW V1.wav"]})
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title SW V1.wav"        # the remix folder's version-only file is never promoted


def test_a_remix_and_an_original_in_a_folder_named_for_the_original_are_two_tracks(tmp_path):
    # The label is called Remix Records, so the hint "Remix" matches the folder name and nothing is filtered out.
    files = ["Artist - Title SW V1.wav", "Artist - Title (Remix) SW V2.wav", "MASTER AMENDED V3.wav"]
    mod = one_folder(tmp_path, "Artist - Title [Remix Records] Project", files)
    assert names(mod.resolve("Artist", "Title", "Remix")) == ["Artist - Title (Remix) SW V2.wav", "Artist - Title SW V1.wav", "MASTER AMENDED V3.wav"]


def test_a_folder_with_only_version_only_files_is_never_one_track(tmp_path):
    mod, tools = _library(tmp_path, {
        "Artist - Title [Label A] Project": ["MASTER AMENDED V3.wav", "SW V2.wav"],
        "Artist - Title [Label B] Project": ["Artist - Title SW V1.wav"]})
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))
    assert len(heads) == 1 and len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")      # SW V2 is older than its own folder's best


def test_d3a_a_remix_hint_names_the_newer_file_of_every_folder_it_passed_over(tmp_path):
    mod, tools = _library(tmp_path, {
        "Artist - Title [Label] Project": ["Artist - Title SW V1.wav", "MASTER AMENDED V2.wav"],
        "Artist - Title (X Remix) [Label] Project": ["Artist - Title (X Remix) SW V1.wav", "MASTER AMENDED V3.wav"]})
    assert names(mod.resolve("Artist", "Title", "X Remix"))[0] == "Artist - Title (X Remix) SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title", "X Remix"]]))
    assert len(heads) == 2 and len(listed) == 2, (heads, listed)                  # two folders, two reasons, one file each
    assert sorted(os.path.basename(x) for x in listed) == ["MASTER AMENDED V2.wav", "MASTER AMENDED V3.wav"]


def test_d4c_scratch_backup_and_typo_names_never_win_and_are_named(tmp_path):
    files = ["Artist - Title SW V1.wav", "Copy of MASTER AMENDED V4.wav", "SW V5 backup.wav", "Artist - Ttile Rough SW V3.wav"]
    mod, tools, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", files)
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title SW V1.wav"          # names with other words in them can never win
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))                    # and none is passed over in silence
    assert len(heads) == 1 and "cannot tell which track it belongs to" in heads[0], heads
    assert [os.path.basename(x) for x in listed] == ["Copy of MASTER AMENDED V4.wav", "SW V5 backup.wav", "Artist - Ttile Rough SW V3.wav"]


@pytest.mark.parametrize("folder", ["Artist - Title Project", "01 Artist - Title [Label] Project", "02. Artist - Title [Label] Project"])
def test_a_trailing_project_or_a_leading_track_number_in_the_folder_name_is_cut_off(tmp_path, folder):
    mod = one_folder(tmp_path, folder, ["Artist - Title SW V1.wav", "MASTER AMENDED V3.wav"])
    assert names(mod.resolve("Artist", "Title"))[0] == "MASTER AMENDED V3.wav"


@pytest.mark.parametrize("folder,titled", [
    ("Artist & Other - Title [Label] Project", "Artist - Title SW V1.wav"),
    ("Artist feat. Other - Title [Label] Project", "Artist - Title SW V1.wav"),
    ("Artist ft. Other - Title [Label] Project", "Artist - Title SW V1.wav"),
    ("Artist feat. Other - Title [Label] Project", "Artist feat. Other - Title SW V1.wav"),
    ("Artist ft. Other - Title [Label] Project", "Artist ft. Other - Title SW V1.wav"),
    ("Artist (feat. Other) - Title [Label] Project", "Artist - Title SW V1.wav"),
    ("Title - Artist [Label] Project", "Title - Artist SW V1.wav"),
    ("Artist - (Title) [Label] Project", "Artist - Title SW V1.wav"),
    ("Artist - Title (Extended Mix) [Label] Project", "Artist - Title SW V1.wav"),
    ("Artist - Title (Short Mix) [Label] Project", "Artist - Title (Short Mix) SW V1.wav"),
    ("Artist - Title [X Remix] [Label] Project", "Artist - Title SW V1.wav"),
])
def test_e1_a_folder_that_is_not_exactly_artist_and_title_promotes_nothing_and_says_so(tmp_path, folder, titled):
    mod, tools, _ = _folder_and_tools(tmp_path, folder, [titled, "MASTER AMENDED V3.wav"])
    assert names(mod.resolve("Artist", "Title"))[0] == titled
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))
    assert len(heads) == 1 and "the folder name is not exactly artist and title" in heads[0]
    assert len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")


def test_a_version_only_file_with_a_mix_word_keeps_its_own_mix(tmp_path):
    # Extended SW V3 is version-only and Extended: it beats the Radio Edit. An unmarked version-only file still inherits the folder's best mix.
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", ["Artist - Title (Radio Edit) SW V1.wav", "Extended SW V3.wav"])
    r = mod.resolve("Artist", "Title")
    assert names(r) == ["Extended SW V3.wav", "Artist - Title (Radio Edit) SW V1.wav"] and [sc for sc, _ in r] == [43, -39]


def test_master_amended_v_alone_is_not_version_only_and_never_wins(tmp_path):
    mod, _, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", ["Artist - Title SW V1.wav", "MASTER AMENDED V.wav"])
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title SW V1.wav"


def test_a_remix_word_alone_as_the_hint_still_names_the_originals_newer_file(tmp_path):
    mod, tools = _library(tmp_path, {
        "Artist - Title [Label] Project": ["Artist - Title SW V1.wav", "MASTER AMENDED V3.wav"],
        "Artist - Title (X Remix) [Label] Project": ["Artist - Title (X Remix) SW V1.wav"]})
    assert names(mod.resolve("Artist", "Title", "Remix"))[0] == "Artist - Title (X Remix) SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title", "Remix"]]))      # the hint removed the original's files: its newer one is still named
    assert len(heads) == 1 and "ranked lower" in heads[0] and len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")


def test_the_note_prints_the_whole_path_on_its_own_line(tmp_path):
    folder = "Artist - Better When EP [Label] Project"
    files = ["01 Change The Situation.wav", "02 Better When.wav", "MASTER AMENDED V3.wav"]
    _, tools, mr = _folder_and_tools(tmp_path, folder, files)
    lines = _run_cli(tools, [["Artist", "Better When"]]).splitlines()
    at = [i for i, ln in enumerate(lines) if "NOTE" in ln]
    assert len(at) == 1 and len(lines[at[0]]) <= 100 and not lines[at[0]].rstrip().endswith(".wav")      # one short plain line first
    assert lines[at[0] + 1] == "      " + str(mr / "MASTER AMENDED V3.wav")                            # then the whole path, nothing cut
    assert folder in lines[at[0] + 1] and len(lines[at[0] + 1].strip()) > 105


def test_two_tracks_whose_names_are_mix_words_are_not_one_track(tmp_path):
    # Artist Main, tracks Original and Original Version: both used to reduce to the same empty residual and the folder counted as one track.
    files = ["Main - Original.wav", "Main - Original Version.wav", "MASTER AMENDED V3.wav"]
    mod, tools, _ = _folder_and_tools(tmp_path, "Main - Original [Label] Project", files)
    assert names(mod.resolve("Main", "Original"))[0] == "Main - Original.wav"
    heads, listed = _note_block(_run_cli(tools, [["Main", "Original"]]))
    assert len(heads) == 1 and len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")
    # tracks Original and Version in a folder named for Version: the Original file is another track
    mod2 = one_folder(tmp_path / "b", "Main - Version [Label] Project", ["Main - Original.wav", "Main - Version.wav", "MASTER AMENDED V3.wav"])
    assert names(mod2.resolve("Main", "Version"))[0] == "Main - Version.wav"


def test_a_folder_that_matches_only_by_substring_still_notes_a_newer_version_only_file(tmp_path):
    mod, tools = _library(tmp_path, {
        "Mark - Title [Label] Project": ["Mark - Title SW V2.wav"],
        "Marky - Title [Label] Project": ["Marky - Title SW V1.wav", "MASTER AMENDED V3.wav"]})
    assert names(mod.resolve("Mark", "Title"))[0] == "Mark - Title SW V2.wav"
    heads, listed = _note_block(_run_cli(tools, [["Mark", "Title"]]))
    assert len(heads) == 1 and "the folder name does not name this track" in heads[0]
    assert len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav") and "Marky" in listed[0]


def test_a_version_only_file_older_than_its_own_folders_best_is_not_listed(tmp_path):
    # In folder X the Radio Edit SW V5 is the best file, so SW V3 is not the newest there; it is newer than the chosen Extended SW V1 only globally.
    mod, tools = _library(tmp_path, {
        "Artist - Title [Label X] Project": ["Artist - Title (Radio Edit) SW V5.wav", "SW V3.wav"],
        "Artist - Title [Label Z] Project": ["Artist - Title (Extended Mix) SW V1.wav"]})
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title (Extended Mix) SW V1.wav"
    assert _note_block(_run_cli(tools, [["Artist", "Title"]])) == ([], [])


def test_the_best_file_of_its_folder_is_not_listed_when_it_is_not_newer_than_the_chosen_one(tmp_path):
    # Folder A's version-only file wins its own folder but the Extended SW V12 in folder B is a newer version: nothing newer was passed over.
    mod, tools = _library(tmp_path, {
        "Artist - Title [Label A] Project": ["Artist - Title (Radio Edit) SW V1.wav", "MASTER AMENDED V3.wav"],
        "Artist - Title [Label B] Project": ["Artist - Title (Extended Mix) SW V12.wav"]})
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title (Extended Mix) SW V12.wav"
    assert _note_block(_run_cli(tools, [["Artist", "Title"]])) == ([], [])


# --- round 4: promote only in an exact single-track folder, and never pass a newer file over in silence ------------------

@pytest.mark.parametrize("name", ["Original SW V3.wav", "Main SW V3.wav", "Edit SW V3.wav"])
def test_e3_a_version_only_file_with_a_mix_word_keeps_its_own_score_and_loses_to_an_older_extended(tmp_path, name):
    # mix() scores these three names 0: they must not borrow the Extended preference of the folder.
    mod, tools, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", ["Artist - Title (Extended Mix) SW V1.wav", name])
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title (Extended Mix) SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))                # the newer plain version is named, not passed over in silence
    assert len(heads) == 1 and "ranked lower" in heads[0] and len(listed) == 1 and listed[0].endswith(name)


def test_an_exact_folder_promotes_an_extended_version_only_file_with_its_own_mix(tmp_path):
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", ["Artist - Title (Extended Mix) SW V1.wav", "Extended SW V3.wav"])
    r = mod.resolve("Artist", "Title")
    assert names(r) == ["Extended SW V3.wav", "Artist - Title (Extended Mix) SW V1.wav"] and [sc for sc, _ in r] == [43, 41]


def test_e9_a_title_that_is_a_version_word_never_makes_a_version_only_file_titled(tmp_path):
    mod, tools = _library(tmp_path, {
        "Artist - Mastermind [Label] Project": ["MASTER AMENDED V3.wav"],
        "Artist - Master [Label] Project": ["Artist - Master SW V1.wav"]})
    assert names(mod.resolve("Artist", "Master"))[0] == "Artist - Master SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Master"]]))
    assert len(heads) == 1 and "the folder name does not name this track" in heads[0]
    assert len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav") and "Mastermind" in listed[0]


def test_e4_the_artist_word_inside_another_title_is_not_taken_out(tmp_path):
    # Artist Main, title Version: "Main - Main Version" is another title, not Version.
    mod, tools, _ = _folder_and_tools(tmp_path, "Main - Version [Label] Project", ["Main - Main Version SW V1.wav", "MASTER AMENDED V3.wav"])
    assert names(mod.resolve("Main", "Version"))[0] == "Main - Main Version SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Main", "Version"]]))
    assert len(heads) == 1 and "the folder may hold other tracks" in heads[0] and len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")


@pytest.mark.parametrize("other", ["Demo.wav", "Rough Copy.wav"])
def test_e5_a_scratch_or_copy_name_makes_the_folder_not_exact(tmp_path, other):
    mod, tools, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", ["Artist - Title SW V1.wav", other, "MASTER AMENDED V3.wav"])
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))
    assert len(heads) == 1 and "the folder may hold other tracks" in heads[0] and len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")


@pytest.mark.parametrize("name", ["Artist - SW V3.wav", "Artist - (Extended Mix) SW V3.wav", "01 Artist - MASTER AMENDED V3.wav"])
def test_a_file_with_only_versions_and_decorations_is_named_in_the_note_and_never_promoted(tmp_path, name):
    mod, tools, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", ["Artist - Title SW V1.wav", name])
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))
    assert len(heads) == 1 and "the folder may hold other tracks" in heads[0] and len(listed) == 1 and listed[0].endswith(name)


def test_a_must_hint_that_matches_nothing_says_so_and_still_prints_the_note(tmp_path):
    _, tools, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", ["Artist - Title SW V1.wav", "MASTER AMENDED V3.wav"])
    out = _run_cli(tools, [["Artist", "Title", "No Such Hint"]])
    assert 'NONE: no file matched the hint "No Such Hint"' in out
    heads, listed = _note_block(out)
    assert len(heads) == 1 and len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")


# --- round 5: a newer file with one extra word is named, a group is a decoration only when it holds mix words, honest reasons ----

@pytest.mark.parametrize("name", ["SW V5 backup.wav", "MASTER AMENDED V.wav", "MASTER AMENDED V3 - Copy.wav", "Club SW V3.wav",
                                  "Artist - Ttile SW V3.wav", "Copy of MASTER AMENDED V4.wav", "Artist - Ttile Rough SW V3.wav", "SW V3 (1).wav"])
def test_a_newer_file_with_one_extra_word_is_named_in_the_note_and_never_promoted(tmp_path, name):
    mod, tools, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", ["Artist - Title SW V1.wav", name])
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))
    assert len(heads) == 1 and "cannot tell which track it belongs to" in heads[0]
    assert len(listed) == 1 and listed[0].endswith(name)


def test_a_newer_dated_file_without_a_version_token_is_not_listed(tmp_path):
    # Only files that carry a version token are listed: Demo.wav is newer by date (both are at version 0, so the date decides) but says nothing about a version.
    mod, tools, mr = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", ["Artist - Title.wav", "Demo.wav"])
    _age(mr / "Demo.wav", 100)
    assert names(mod.resolve("Artist", "Title"))[0] == "Artist - Title.wav"
    assert _note_block(_run_cli(tools, [["Artist", "Title"]])) == ([], [])


def test_a_file_with_an_extra_word_is_not_listed_in_an_ep_folder(tmp_path):
    # Only in a folder named exactly artist - title: an EP folder is not flooded with every other track that has a version token.
    _, tools, _ = _folder_and_tools(tmp_path, "Artist - Better When EP [Label] Project", ["Artist - Better When SW V1.wav", "SW V5 backup.wav"])
    assert _note_block(_run_cli(tools, [["Artist", "Better When"]])) == ([], [])


@pytest.mark.parametrize("group", ["(Part 2)", "(Reprise)", "(X Re-Edit)", "(feat. X)", "[Part 2]"])
def test_a_group_that_is_not_all_mix_words_makes_a_different_track_so_nothing_is_promoted(tmp_path, group):
    files = ["Artist - Title SW V1.wav", f"Artist - Title {group} SW V1.wav", "MASTER AMENDED V3.wav"]
    mod, tools, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", files)
    assert names(mod.resolve("Artist", "Title"))[0] != "MASTER AMENDED V3.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))
    assert len(heads) == 1 and "the folder may hold other tracks" in heads[0]
    assert len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")


def test_part_1_and_part_2_in_one_folder_do_not_promote_an_untitled_master(tmp_path):
    files = ["Artist - Title (Part 1) SW V1.wav", "Artist - Title (Part 2) SW V1.wav", "MASTER AMENDED V3.wav"]
    mod, tools, _ = _folder_and_tools(tmp_path, "Artist - Title [Label] Project", files)
    assert names(mod.resolve("Artist", "Title"))[0] in ("Artist - Title (Part 1) SW V1.wav", "Artist - Title (Part 2) SW V1.wav")
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))
    assert len(heads) == 1 and len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")


@pytest.mark.parametrize("group", ["(Extended Mix)", "(Radio Edit)", "(Club Mix)", "[Extended Mix]", "(Vocal Mix)"])
def test_a_group_of_only_mix_words_is_still_a_decoration(tmp_path, group):
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", [f"Artist - Title {group} SW V1.wav", "MASTER AMENDED V3.wav"])
    assert names(mod.resolve("Artist", "Title"))[0] == "MASTER AMENDED V3.wav"


def test_the_reason_for_an_exact_folder_name_does_not_claim_other_tracks(tmp_path):
    # Artist Bob, title Bob Song: the file has no artist prefix, so the artist word is taken off the title and the folder is not called exact.
    # The folder holds only this track, so the NOTE must not say that it holds other tracks.
    mod, tools, _ = _folder_and_tools(tmp_path, "Bob - Bob Song [Label] Project", ["Bob Song SW V1.wav", "MASTER AMENDED V3.wav"])
    assert names(mod.resolve("Bob", "Bob Song"))[0] == "Bob Song SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Bob", "Bob Song"]]))
    assert len(heads) == 1 and "may hold other tracks" in heads[0] and "holds other" not in heads[0]
    assert len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")


@pytest.mark.parametrize("artist,title", [("Artist", ""), ("Artist", "!!!"), ("Artist", chr(228)), ("", "Title"), (" ", "Title"), (chr(228), "Title")])
def test_the_library_refuses_an_empty_artist_or_title(tmp_path, artist, title):
    mod = one_folder(tmp_path, "Artist - Title [Label] Project", ["Artist - Title SW V1.wav"])
    with pytest.raises(ValueError, match="letter or a digit"):
        mod.resolve(artist, title)
    with pytest.raises(ValueError):
        mod.scan(artist, title, "Hint")


def test_a_folder_named_only_for_the_title_is_not_exact(tmp_path):
    # The artist is in the label name so the folder is scanned, but the name is only the title: nothing is promoted and the file is named.
    mod, tools, _ = _folder_and_tools(tmp_path, "Title [Artist Records] Project", ["Title SW V1.wav", "MASTER AMENDED V3.wav"])
    assert names(mod.resolve("Artist", "Title"))[0] == "Title SW V1.wav"
    heads, listed = _note_block(_run_cli(tools, [["Artist", "Title"]]))
    assert len(heads) == 1 and "the folder name is not exactly artist and title" in heads[0]
    assert len(listed) == 1 and listed[0].endswith("MASTER AMENDED V3.wav")
