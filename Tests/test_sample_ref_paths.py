"""Tests for _fix_sample_ref_paths (burn list A6).

clone_clip (apply_loops.py) duplicates AudioClip blocks - including their
embedded SampleRef/FileRef - as opaque template TEXT, so whatever
depth/machine a clip's paths were originally correct for propagates
unchanged through every downstream copy. build_ab_comparison.py's deeper
layout (<project>/Output/AB/<side>/Mix <side>.als) sits at a different
depth than the source was computed for, so every track shows OFFLINE in
Ableton when opened on a different machine than it was built on.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Source"))

from apply_automation import _fix_sample_ref_paths  # noqa: E402


def _file_ref_block(rel_path: str, abs_path: str, extra: str = "") -> str:
    return (
        "<FileRef>\n"
        f'  <RelativePathType Value="1" />\n'
        f'  <RelativePath Value="{rel_path}" />\n'
        f'  <Path Value="{abs_path}" />\n'
        f'  <Type Value="1" />\n'
        f'  <LivePackName Value="" />\n'
        f'  <LivePackId Value="" />\n'
        f'  <OriginalFileSize Value="123456" />\n'
        f'  <OriginalCrc Value="0" />\n'
        f'  <SourceHint Value="" />\n'
        f"{extra}"
        "</FileRef>\n"
    )


def _sample_ref(file_ref_block: str) -> str:
    return (
        "<SampleRef>\n"
        f"{file_ref_block}"
        '  <LastModDate Value="0" />\n'
        "  <SourceContext />\n"
        '  <SampleUsageHint Value="0" />\n'
        '  <DefaultDuration Value="13978020" />\n'
        '  <DefaultSampleRate Value="44100" />\n'
        '  <SamplesToAutoWarp Value="0" />\n'
        "</SampleRef>\n"
    )


def _wrap_clip(name: str, sample_ref: str) -> str:
    """A minimal AudioClip-shaped wrapper so tests can verify unrelated
    clip content (timing, name) is left byte-identical."""
    return (
        f'<AudioClip Id="1" Time="16.0">\n'
        f'  <Name Value="{name}" />\n'
        f'  <CurrentStart Value="0" />\n'
        f'  <CurrentEnd Value="64" />\n'
        f"{sample_ref}"
        "</AudioClip>\n"
    )


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _make_audio_dir(tmp_path: Path, filenames: list[str]) -> Path:
    audio_dir = tmp_path / "Audio"
    audio_dir.mkdir()
    for name in filenames:
        (audio_dir / name).write_bytes(b"RIFF....WAVEfmt ")
    return audio_dir


def test_ab_comparison_depth_rewrites_both_relative_and_absolute_path(tmp_path):
    """The exact bug shape: RelativePath hardcoded to one ../ level, Path
    baked in from a different machine's drive letter. Output saved 3
    levels below the project root (Output/AB/A/Mix A.als)."""
    audio_dir = _make_audio_dir(tmp_path, ["Track One.wav"])
    output_path = tmp_path / "Output" / "AB" / "A" / "Mix A.als"
    output_path.parent.mkdir(parents=True)

    block = _file_ref_block(
        rel_path="../Audio/Track One.wav",
        abs_path="G:/Wired Masters Dropbox/Sam Wills/.../Audio/Track One.wav",
    )
    text = _wrap_clip("intro_1", _sample_ref(block))

    fixed = "".join(_fix_sample_ref_paths(_lines(text), output_path, audio_dir))

    assert '<RelativePath Value="../../../Audio/Track One.wav" />' in fixed
    assert f'<Path Value="{audio_dir.as_posix()}/Track One.wav" />' in fixed


def test_single_mix_depth_recomputes_the_same_correct_answer(tmp_path):
    """Proves the fix is depth-AWARE, not a no-op: at the standard
    single-mix depth (Output/Mix.als, one level below the project root),
    recomputing still lands on ../Audio/ - the same value that was already
    there, but genuinely recomputed, not left alone by accident."""
    audio_dir = _make_audio_dir(tmp_path, ["Track One.wav"])
    output_path = tmp_path / "Output" / "Mix.als"
    output_path.parent.mkdir(parents=True)

    block = _file_ref_block(
        rel_path="../Audio/Track One.wav",
        abs_path="G:/some/other/machine/Audio/Track One.wav",
    )
    text = _wrap_clip("intro_1", _sample_ref(block))

    fixed = "".join(_fix_sample_ref_paths(_lines(text), output_path, audio_dir))

    assert '<RelativePath Value="../Audio/Track One.wav" />' in fixed
    assert f'<Path Value="{audio_dir.as_posix()}/Track One.wav" />' in fixed


def test_filename_not_found_under_audio_dir_left_untouched(tmp_path):
    audio_dir = _make_audio_dir(tmp_path, ["Other Track.wav"])
    output_path = tmp_path / "Output" / "AB" / "A" / "Mix A.als"
    output_path.parent.mkdir(parents=True)

    block = _file_ref_block(
        rel_path="../Audio/Missing Track.wav",
        abs_path="G:/somewhere/Audio/Missing Track.wav",
    )
    text = _wrap_clip("intro_1", _sample_ref(block))

    fixed = "".join(_fix_sample_ref_paths(_lines(text), output_path, audio_dir))

    assert fixed == text


def test_no_audio_dir_findable_leaves_whole_file_unchanged(tmp_path):
    output_path = tmp_path / "Output" / "AB" / "A" / "Mix A.als"
    output_path.parent.mkdir(parents=True)

    block = _file_ref_block(
        rel_path="../Audio/Track One.wav",
        abs_path="G:/somewhere/Audio/Track One.wav",
    )
    text = _wrap_clip("intro_1", _sample_ref(block))

    fixed = "".join(_fix_sample_ref_paths(_lines(text), output_path, None))

    assert fixed == text


def test_multiple_clips_on_one_track_each_get_their_own_fileref_fixed(tmp_path):
    """A real multi-clip track (e.g. Freejak's 8 clips in the original bug
    report) - every clip's own separate FileRef must be fixed, not just
    the first one the regex finds."""
    audio_dir = _make_audio_dir(tmp_path, ["Track One.wav", "Track Two.wav"])
    output_path = tmp_path / "Output" / "AB" / "A" / "Mix A.als"
    output_path.parent.mkdir(parents=True)

    clip1 = _wrap_clip("intro_1", _sample_ref(_file_ref_block(
        rel_path="../Audio/Track One.wav",
        abs_path="G:/machine1/Audio/Track One.wav",
    )))
    clip2 = _wrap_clip("drop_1", _sample_ref(_file_ref_block(
        rel_path="../Audio/Track Two.wav",
        abs_path="G:/machine1/Audio/Track Two.wav",
    )))
    text = clip1 + clip2

    fixed = "".join(_fix_sample_ref_paths(_lines(text), output_path, audio_dir))

    assert '<RelativePath Value="../../../Audio/Track One.wav" />' in fixed
    assert '<RelativePath Value="../../../Audio/Track Two.wav" />' in fixed
    assert fixed.count(f'<Path Value="{audio_dir.as_posix()}/Track One.wav" />') == 1
    assert fixed.count(f'<Path Value="{audio_dir.as_posix()}/Track Two.wav" />') == 1


def test_only_the_two_path_attributes_change_everything_else_byte_identical(tmp_path):
    audio_dir = _make_audio_dir(tmp_path, ["Track One.wav"])
    output_path = tmp_path / "Output" / "AB" / "A" / "Mix A.als"
    output_path.parent.mkdir(parents=True)

    block = _file_ref_block(
        rel_path="../Audio/Track One.wav",
        abs_path="G:/machine1/Audio/Track One.wav",
    )
    text = _wrap_clip("intro_1", _sample_ref(block))

    fixed_lines = _fix_sample_ref_paths(_lines(text), output_path, audio_dir)
    fixed = "".join(fixed_lines)

    # Strip the two rewritten lines from both texts; everything else must
    # be identical (clip name, timing, every other FileRef field).
    def _without_path_lines(s: str) -> str:
        return "\n".join(
            line for line in s.splitlines()
            if "<RelativePath Value=" not in line and "<Path Value=" not in line
        )

    assert _without_path_lines(fixed) == _without_path_lines(text)


def test_factory_content_fileref_left_alone(tmp_path):
    """A Max for Live device (.amxd) or an empty unused slot (both values
    "") never lived under this project's own Audio/ folder - must not be
    guessed at just because SOME FileRef in the file needed fixing."""
    audio_dir = _make_audio_dir(tmp_path, ["Track One.wav"])
    output_path = tmp_path / "Output" / "AB" / "A" / "Mix A.als"
    output_path.parent.mkdir(parents=True)

    real_clip = _wrap_clip("intro_1", _sample_ref(_file_ref_block(
        rel_path="../Audio/Track One.wav",
        abs_path="G:/machine1/Audio/Track One.wav",
    )))
    amxd_ref = _file_ref_block(rel_path="", abs_path="C:/ProgramData/Ableton/Some Device.amxd")
    empty_ref = _file_ref_block(rel_path="", abs_path="")
    text = real_clip + amxd_ref + empty_ref

    fixed = "".join(_fix_sample_ref_paths(_lines(text), output_path, audio_dir))

    assert 'Value="C:/ProgramData/Ableton/Some Device.amxd"' in fixed
    assert amxd_ref in fixed
    assert empty_ref in fixed


def test_empty_relative_path_falls_through_to_absolute_path_basename(tmp_path):
    """RelativePathType=0 (absolute-only) shape: RelativePath is empty but
    Path carries the real file - the basename anchor must fall through."""
    audio_dir = _make_audio_dir(tmp_path, ["Track One.wav"])
    output_path = tmp_path / "Output" / "AB" / "A" / "Mix A.als"
    output_path.parent.mkdir(parents=True)

    block = _file_ref_block(
        rel_path="",
        abs_path="G:/machine1/Audio/Track One.wav",
    )
    text = _wrap_clip("intro_1", _sample_ref(block))

    fixed = "".join(_fix_sample_ref_paths(_lines(text), output_path, audio_dir))

    assert '<RelativePath Value="../../../Audio/Track One.wav" />' in fixed
    assert f'<Path Value="{audio_dir.as_posix()}/Track One.wav" />' in fixed


def test_idempotent_running_twice_matches_running_once(tmp_path):
    audio_dir = _make_audio_dir(tmp_path, ["Track One.wav", "Track Two.wav"])
    output_path = tmp_path / "Output" / "AB" / "A" / "Mix A.als"
    output_path.parent.mkdir(parents=True)

    clip1 = _wrap_clip("intro_1", _sample_ref(_file_ref_block(
        rel_path="../Audio/Track One.wav",
        abs_path="G:/machine1/Audio/Track One.wav",
    )))
    clip2 = _wrap_clip("drop_1", _sample_ref(_file_ref_block(
        rel_path="../Audio/Track Two.wav",
        abs_path="H:/machine2/somewhere/Audio/Track Two.wav",
    )))
    text = clip1 + clip2

    once = _fix_sample_ref_paths(_lines(text), output_path, audio_dir)
    twice = _fix_sample_ref_paths(once, output_path, audio_dir)

    assert "".join(once) == "".join(twice)


def test_xml_escaped_apostrophe_in_filename_still_resolves(tmp_path):
    """Real bug caught by a real-corpus dry-run (2026-09-15): ALS attribute
    values are XML-escaped (&apos; etc., same convention this file already
    uses for track names), so a filename like "There's A Party" is stored
    as "There&apos;s A Party" in RelativePath/Path - comparing that raw
    escaped text against the real on-disk filename ("There's A Party")
    never matches, and the fail-safe silently left it untouched. Must
    unescape before the filesystem lookup, and re-escape the real filename
    on the way back out so the written value stays consistent with every
    other untouched FileRef in the file."""
    audio_dir = _make_audio_dir(tmp_path, ["HARTY - There's A Party.wav"])
    output_path = tmp_path / "Output" / "AB" / "A" / "Mix A.als"
    output_path.parent.mkdir(parents=True)

    block = _file_ref_block(
        rel_path="../Audio/HARTY - There&apos;s A Party.wav",
        abs_path="G:/machine1/Audio/HARTY - There&apos;s A Party.wav",
    )
    text = _wrap_clip("intro_1", _sample_ref(block))

    fixed = "".join(_fix_sample_ref_paths(_lines(text), output_path, audio_dir))

    assert (
        '<RelativePath Value="../../../Audio/HARTY - There&apos;s A Party.wav" />'
        in fixed
    )
    assert (
        f'<Path Value="{audio_dir.as_posix()}/HARTY - There&apos;s A Party.wav" />'
        in fixed
    )
    # The raw, unescaped apostrophe must never appear in the written XML -
    # only the &apos; form, consistent with every other value in the file.
    assert "There's A Party" not in fixed


def test_case_insensitive_match_resolves_to_the_real_on_disk_name(tmp_path):
    """A differently-cased basename must still resolve - AND must be
    rewritten to the file's REAL on-disk name, not the wrong-case name
    that happened to be in the source ALS. Path.exists() alone is not
    enough to prove this: NTFS/APFS default to case-insensitive lookup, so
    (audio_dir / "KICK.wav").exists() can return True even when the real
    file is kick.wav - naively trusting that would silently keep writing
    the wrong case."""
    audio_dir = _make_audio_dir(tmp_path, ["kick.wav"])
    output_path = tmp_path / "Output" / "AB" / "A" / "Mix A.als"
    output_path.parent.mkdir(parents=True)

    block = _file_ref_block(
        rel_path="../Audio/KICK.wav",
        abs_path="G:/machine1/Audio/KICK.wav",
    )
    text = _wrap_clip("intro_1", _sample_ref(block))

    fixed = "".join(_fix_sample_ref_paths(_lines(text), output_path, audio_dir))

    assert '<RelativePath Value="../../../Audio/kick.wav" />' in fixed
    assert f'<Path Value="{audio_dir.as_posix()}/kick.wav" />' in fixed
    assert "KICK.wav" not in fixed


def test_ampersand_in_filename_is_re_escaped_and_output_parses(tmp_path):
    """A bare & written back (RUZE & Chesster, 2026-09-15) made the whole ALS unloadable."""
    import xml.etree.ElementTree as ET

    audio_dir = _make_audio_dir(tmp_path, ["RUZE & Chesster - Another Night.wav"])
    output_path = tmp_path / "Output" / "Mix.als"
    output_path.parent.mkdir(parents=True)

    block = _file_ref_block(
        rel_path="../Audio/RUZE &amp; Chesster - Another Night.wav",
        abs_path="G:/machine1/Audio/RUZE &amp; Chesster - Another Night.wav",
    )
    text = _wrap_clip("intro_1", _sample_ref(block))

    fixed = "".join(_fix_sample_ref_paths(_lines(text), output_path, audio_dir))

    assert '<RelativePath Value="../Audio/RUZE &amp; Chesster - Another Night.wav" />' in fixed
    assert "RUZE & Chesster" not in fixed
    ET.fromstring(fixed)


def test_relative_audio_dir_still_writes_an_absolute_path_value(tmp_path, monkeypatch):
    """The pipeline runs from the repo root with relative project paths."""
    import re

    _make_audio_dir(tmp_path, ["Track One.wav"])
    (tmp_path / "Output").mkdir()
    monkeypatch.chdir(tmp_path)

    block = _file_ref_block(
        rel_path="../Audio/Track One.wav",
        abs_path="G:/machine1/Audio/Track One.wav",
    )
    text = _wrap_clip("intro_1", _sample_ref(block))

    fixed = "".join(_fix_sample_ref_paths(
        _lines(text), Path("Output") / "Mix.als", Path("Audio")))

    written = re.search(r'<Path Value="([^"]*)"', fixed).group(1)
    assert Path(written).is_absolute()
    assert Path(written).samefile(tmp_path / "Audio" / "Track One.wav")
    assert '<RelativePath Value="../Audio/Track One.wav" />' in fixed
