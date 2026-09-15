"""Tests for extract_sections_als.parse_sections_als itself (burn list E3).

Found by Codex during a margin-fix review (2026-09-xx), logged not fixed at
the time: the parser split each track's body on the LITERAL, fixed-order
string `<AudioClip Id="\\d+" Time="` - valid XML that reorders AudioClip's
own attributes (or that a different Ableton version writes differently)
made every clip in that track silently vanish from the parsed result, with
no error, falling `apply_automation` back to whatever sections JSON already
existed on disk instead of the .als just re-extracted.
"""
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Source"))

from extract_sections_als import parse_sections_als  # noqa: E402


def _als(xml_body: str, tmp_path: Path) -> Path:
    """A minimal gzip'd XML fixture in the same shape parse_sections_als
    expects: one or more <AudioTrack>...</AudioTrack> blocks."""
    p = tmp_path / "Sections V1.als"
    p.write_bytes(gzip.compress(xml_body.encode("utf-8")))
    return p


def _one_clip_track(name: str, clip_tag: str) -> str:
    return (
        f'<AudioTrack Id="0"><EffectiveName Value="{name}"/>'
        f'{clip_tag}'
        f'<CurrentEnd Value="64.0"/>'
        f'<LoopStart Value="0.0"/><LoopEnd Value="64.0"/>'
        f'<Name Value="drop_1"/><Color Value="3"/>'
        f'</AudioClip></AudioTrack>'
    )


def test_parses_a_clip_in_the_normal_attribute_order(tmp_path):
    xml = _one_clip_track("Drums", '<AudioClip Id="7" Time="16.0">')
    result = parse_sections_als(_als(xml, tmp_path))

    assert "Drums" in result
    assert len(result["Drums"]) == 1
    clip = result["Drums"][0]
    assert clip["arr_time"] == 16.0
    assert clip["arr_end"] == 64.0
    assert clip["name"] == "drop_1"
    assert clip["label"] == "drop"
    assert clip["label_n"] == 1


def test_parses_a_clip_with_time_before_id_real_bug_this_fix_closes(tmp_path):
    """The exact class of input the old fixed-order split silently lost
    every clip on: valid XML, attributes just in a different order."""
    xml = _one_clip_track("Drums", '<AudioClip Time="16.0" Id="7">')
    result = parse_sections_als(_als(xml, tmp_path))

    assert "Drums" in result, "the track must not silently vanish"
    assert len(result["Drums"]) == 1
    assert result["Drums"][0]["arr_time"] == 16.0


def test_parses_a_clip_with_extra_attributes_between_id_and_time(tmp_path):
    """Not just reordering - any real AudioClip carries several more
    attributes (e.g. LomId, LomIdView, TrackId...) between Id and Time in
    a real Ableton-written file; the fixed-order split's exact adjacency
    requirement was fragile to this too, not just a swap."""
    xml = _one_clip_track(
        "Drums", '<AudioClip Id="7" LomId="0" LomIdView="0" Time="16.0">')
    result = parse_sections_als(_als(xml, tmp_path))

    assert "Drums" in result
    assert result["Drums"][0]["arr_time"] == 16.0


def test_multiple_clips_in_one_track_all_survive_reordered_attributes(tmp_path):
    xml = (
        '<AudioTrack Id="0"><EffectiveName Value="Drums"/>'
        '<AudioClip Time="0.0" Id="1">'
        '<CurrentEnd Value="32.0"/><LoopStart Value="0.0"/><LoopEnd Value="32.0"/>'
        '<Name Value="intro_1"/><Color Value="1"/></AudioClip>'
        '<AudioClip Id="2" Time="32.0">'
        '<CurrentEnd Value="96.0"/><LoopStart Value="32.0"/><LoopEnd Value="96.0"/>'
        '<Name Value="drop_1"/><Color Value="3"/></AudioClip>'
        '</AudioTrack>'
    )
    result = parse_sections_als(_als(xml, tmp_path))

    assert len(result["Drums"]) == 2
    names = {c["name"] for c in result["Drums"]}
    assert names == {"intro_1", "drop_1"}


def test_time_attribute_is_scoped_to_the_clips_own_opening_tag(tmp_path):
    """The fix must not accidentally match a `Time=` that happens to appear
    later in the clip body (e.g. inside a nested element) instead of the
    AudioClip tag's own attribute - confirmed by giving the clip body an
    unrelated Time-shaped decoy after its real opening tag."""
    xml = (
        '<AudioTrack Id="0"><EffectiveName Value="Drums"/>'
        '<AudioClip Id="1" Time="8.0">'
        '<SomeOtherThing Time="999.0"/>'
        '<CurrentEnd Value="64.0"/><LoopStart Value="0.0"/><LoopEnd Value="64.0"/>'
        '<Name Value="drop_1"/><Color Value="3"/></AudioClip>'
        '</AudioTrack>'
    )
    result = parse_sections_als(_als(xml, tmp_path))

    assert result["Drums"][0]["arr_time"] == 8.0
