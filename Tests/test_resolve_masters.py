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


def test_known_limit_the_newest_render_without_the_title_words_loses(tmp_path):
    # B02 and B05, left as a judgement call for Sam (see the module docstring): no heuristic is added for them.
    mod = one_folder(tmp_path, "B02 - Title [Lab] Project", ["MASTER AMENDED V3.wav", "B02 - Title 24 Bit MASTER.wav"])
    assert names(mod.resolve("B02", "Title"))[0] == "B02 - Title 24 Bit MASTER.wav"
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
