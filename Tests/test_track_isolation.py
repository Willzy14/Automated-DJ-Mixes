import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))


def _track(track_id, name, marker_count):
    markers = "".join(
        f'          <WarpMarker Id="{index}" SecTime="{index / 2}" BeatTime="{index}" />\n'
        for index in range(marker_count)
    )
    return [
        f'<AudioTrack Id="{track_id}">\n',
        f'  <EffectiveName Value="{name}" />\n',
        '  <Sample>\n',
        '    <ArrangerAutomation>\n',
        '      <Events>\n',
        '        <AudioClip Id="1" Time="0">\n',
        '          <WarpMarkers>\n',
        markers,
        '          </WarpMarkers>\n',
        '        </AudioClip>\n',
        '      </Events>\n',
        '    </ArrangerAutomation>\n',
        '  </Sample>\n',
        '</AudioTrack>\n',
    ]


def test_isolation_preserves_target_block_and_clears_other_events():
    from isolate_sections_tracks import isolate_track_events

    target = _track(1, "A &amp; B", 487)
    other = _track(2, "Other", 2)
    lines = target + other

    isolated, hashes = isolate_track_events(lines, ["A & B"])
    content = "".join(isolated)

    assert "".join(target) in content
    assert content.count("<AudioClip ") == 1
    assert content.count("<WarpMarker ") == 487
    assert list(hashes) == ["A & B"]


def test_section_selection_matches_xml_entities():
    from isolate_sections_tracks import select_sections

    selected = select_sections(
        {"A &amp; B": [{"label": "intro"}]},
        ["A & B"],
    )

    assert selected == {"A & B": [{"label": "intro"}]}
