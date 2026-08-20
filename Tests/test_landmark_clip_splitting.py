"""D5 clip geometry: landmark swaps become real two-sided ALS edges."""

import gzip
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import apply_automation  # noqa: E402
from automated_dj_mixes.als_generator import (  # noqa: E402
    split_audio_clip_at_beat,
)
from validate_mix_plan_als import _matches_clip_boundary  # noqa: E402


WINDOW_TAGS = {
    "CurrentStart", "CurrentEnd", "LoopStart", "LoopEnd", "OutMarker",
    "HiddenLoopStart", "HiddenLoopEnd", "SampleStart", "SampleEnd",
}


def _rich_clip(
    clip_id=10,
    take_id=14,
    remote_id=11,
    marker_ids=(12, 13),
    time=100.0,
    end=132.0,
    source_start=20.0,
    source_end=52.0,
    sample_start=2000.0,
    sample_end=5200.0,
    name="drop_1",
):
    return f'''<AudioClip Id="{clip_id}" Time="{time}">
  <LomId Value="0" />
  <CurrentStart Value="{time}" />
  <CurrentEnd Value="{end}" />
  <Loop>
    <LoopStart Value="{source_start}" />
    <LoopEnd Value="{source_end}" />
    <StartRelative Value="0" />
    <LoopOn Value="false" />
    <OutMarker Value="{source_end}" />
    <HiddenLoopStart Value="{source_start}" />
    <HiddenLoopEnd Value="{source_end}" />
    <SampleStart Value="{sample_start}" />
    <SampleEnd Value="{sample_end}" />
  </Loop>
  <Name Value="{name}" />
  <Annotation Value="keep every attribute" />
  <Color Value="55" />
  <Disabled Value="false" />
  <TimeSignature><TimeSignatures>
    <RemoteableTimeSignature Id="{remote_id}">
      <Numerator Value="4" /><Denominator Value="4" />
    </RemoteableTimeSignature>
  </TimeSignatures></TimeSignature>
  <TakeId Value="{take_id}" />
  <WarpMode Value="6" />
  <Fade Value="true" />
  <Fades>
    <FadeInLength Value="1.25" />
    <FadeOutLength Value="2.5" />
    <FadeInCurveSkew Value="0.2" />
    <FadeOutCurveSlope Value="-0.4" />
  </Fades>
  <SampleVolume Value="0.875" />
  <WarpMarkers>
    <WarpMarker Id="{marker_ids[0]}" SecTime="1.25" BeatTime="20.0" Custom="a" />
    <WarpMarker Id="{marker_ids[1]}" SecTime="17.25" BeatTime="52.0" Custom="b" />
  </WarpMarkers>
</AudioClip>
'''


def _document(*clips):
    xml = (
        '<Ableton Id="90000">\n<AudioTrack Id="90001">\n'
        '<ArrangerAutomation><Events>\n'
        + "".join(clips)
        + '</Events></ArrangerAutomation>\n</AudioTrack>\n</Ableton>\n'
    )
    return xml.splitlines(keepends=True)


def _root(lines):
    return ET.fromstring("".join(lines))


def _clip_elements(lines):
    return list(_root(lines).iter("AudioClip"))


def _value(element, tag):
    node = element.find(f".//{tag}")
    return float(node.get("Value"))


def _metadata_signature(element):
    clone = ET.fromstring(ET.tostring(element, encoding="unicode"))
    for node in clone.iter():
        node.attrib.pop("Id", None)
        if node.tag == "AudioClip":
            node.attrib["Time"] = "<arrangement-window>"
        if node.tag == "TakeId":
            node.attrib["Value"] = "<allocated-id>"
        if node.tag in WINDOW_TAGS:
            node.attrib["Value"] = "<playback-window>"
    return ET.tostring(clone, encoding="unicode")


def test_existing_clip_edge_is_byte_identical_and_never_zero_length():
    lines = _document(
        _rich_clip(end=116.0, source_end=36.0, sample_end=3600.0),
        _rich_clip(
            clip_id=20, take_id=24, remote_id=21, marker_ids=(22, 23),
            time=116.0, end=132.0, source_start=36.0, source_end=52.0,
            sample_start=3600.0, sample_end=5200.0, name="drop_2",
        ),
    )
    before = "".join(lines).encode("utf-8")

    assert not split_audio_clip_at_beat(lines, 0, len(lines) - 1, 116.0)
    assert not split_audio_clip_at_beat(lines, 0, len(lines) - 1, 100.0)
    assert not split_audio_clip_at_beat(lines, 0, len(lines) - 1, 132.0)
    assert not split_audio_clip_at_beat(lines, 0, len(lines) - 1, 999.0)
    assert "".join(lines).encode("utf-8") == before
    assert all(
        _value(clip, "CurrentEnd") > float(clip.get("Time"))
        for clip in _clip_elements(lines)
    )


def test_split_tiles_source_and_sample_windows_and_preserves_all_metadata():
    lines = _document(_rich_clip())
    original = _clip_elements(lines)[0]
    original_signature = _metadata_signature(original)
    original_markers = [dict(marker.attrib) for marker in original.iter("WarpMarker")]

    assert split_audio_clip_at_beat(lines, 0, len(lines) - 1, 112.0)
    outgoing, incoming = _clip_elements(lines)

    assert (float(outgoing.get("Time")), _value(outgoing, "CurrentEnd")) == (
        100.0, 112.0,
    )
    assert (float(incoming.get("Time")), _value(incoming, "CurrentEnd")) == (
        112.0, 132.0,
    )
    assert (_value(outgoing, "LoopStart"), _value(outgoing, "LoopEnd")) == (
        20.0, 32.0,
    )
    assert (_value(incoming, "LoopStart"), _value(incoming, "LoopEnd")) == (
        32.0, 52.0,
    )
    assert (_value(outgoing, "HiddenLoopStart"),
            _value(outgoing, "HiddenLoopEnd")) == (20.0, 32.0)
    assert (_value(incoming, "HiddenLoopStart"),
            _value(incoming, "HiddenLoopEnd")) == (32.0, 52.0)
    assert (_value(outgoing, "SampleStart"),
            _value(outgoing, "SampleEnd")) == (2000.0, 3200.0)
    assert (_value(incoming, "SampleStart"),
            _value(incoming, "SampleEnd")) == (3200.0, 5200.0)

    assert _metadata_signature(outgoing) == original_signature
    assert _metadata_signature(incoming) == original_signature
    for clip in (outgoing, incoming):
        markers = [dict(marker.attrib) for marker in clip.iter("WarpMarker")]
        assert [
            {key: value for key, value in marker.items() if key != "Id"}
            for marker in markers
        ] == [
            {key: value for key, value in marker.items() if key != "Id"}
            for marker in original_markers
        ]


def test_split_allocates_unique_ids_across_the_whole_document():
    lines = _document(_rich_clip())
    before_ids = {
        int(value) for value in re.findall(r'\bId="(\d+)"', "".join(lines))
    }

    assert split_audio_clip_at_beat(lines, 0, len(lines) - 1, 112.0)
    after = "".join(lines)
    all_ids = [int(value) for value in re.findall(r'\bId="(\d+)"', after)]
    take_ids = [
        int(value)
        for value in re.findall(r'<TakeId\b[^>]*\bValue="(\d+)"', after)
    ]
    assert len(all_ids) == len(set(all_ids))
    assert len(take_ids) == len(set(take_ids))
    incoming = _clip_elements(lines)[1]
    incoming_ids = {
        int(node.get("Id")) for node in incoming.iter() if node.get("Id")
    }
    assert incoming_ids.isdisjoint(before_ids)
    assert int(incoming.find("TakeId").get("Value")) not in {14}


def _build_track(track_id, name, clip_id, start, end, source_end):
    return f'''<AudioTrack Id="{track_id}">
<Name><EffectiveName Value="{name}" /></Name>
<DeviceChain><MainSequencer><Sample><ArrangerAutomation><Events>
<AudioClip Id="{clip_id}" Time="{start}">
<CurrentStart Value="{start}" /><CurrentEnd Value="{end}" />
<Loop><LoopStart Value="0.0" /><LoopEnd Value="{source_end}" />
<StartRelative Value="0" /><LoopOn Value="false" />
<OutMarker Value="{source_end}" />
<HiddenLoopStart Value="0.0" /><HiddenLoopEnd Value="{source_end}" /></Loop>
<Name Value="drop_1" /><Color Value="10" /><TakeId Value="{clip_id + 1}" />
<WarpMode Value="6" /><WarpMarkers>
<WarpMarker Id="{clip_id + 2}" SecTime="0" BeatTime="0" />
<WarpMarker Id="{clip_id + 3}" SecTime="60" BeatTime="128" />
</WarpMarkers></AudioClip>
</Events></ArrangerAutomation></Sample></MainSequencer>
<Devices>
<StereoGain Id="{clip_id + 10}">
<Gain>
<AutomationTarget Id="{clip_id + 11}" />
</Gain>
</StereoGain>
<ChannelEq Id="{clip_id + 12}">
<LowShelfGain>
<AutomationTarget Id="{clip_id + 13}" />
</LowShelfGain>
</ChannelEq>
</Devices></DeviceChain>
<AutomationEnvelopes><Envelopes /></AutomationEnvelopes>
</AudioTrack>
'''


def _write_build_inputs(tmp_path, policy):
    als_path = tmp_path / "arranged.als"
    output_path = tmp_path / "built.als"
    sections_path = tmp_path / "sections.json"
    report_path = tmp_path / "report.json"
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Ableton MajorVersion="5" MinorVersion="0">\n'
        '<MainTrack><Tempo><Manual Value="128.0" /></Tempo></MainTrack>\n'
        + _build_track(1, "Track Alpha", 100, 0.0, 800.0, 800.0)
        + _build_track(2, "Track Beta", 200, 500.0, 1400.0, 900.0)
        + '</Ableton>\n'
    )
    with gzip.open(als_path, "wb") as handle:
        handle.write(xml.encode("utf-8"))
    sections_path.write_text("{}", encoding="utf-8")
    report_path.write_text(json.dumps({"transitions": [{
        "out_track": "Track Alpha",
        "in_track": "Track Beta",
        "swap_beats": 600.0,
        "handoff_kind": "test",
        "alignment_policy": policy,
    }]}), encoding="utf-8")
    return als_path, sections_path, output_path, report_path


@pytest.mark.parametrize("policy", [
    "paired_landmarks_v2",
    "tail_anchor_rescue_v1",
])
def test_build_creates_both_side_boundaries_for_every_landmark_policy(
    tmp_path, monkeypatch, policy,
):
    als_path, sections_path, output_path, report_path = _write_build_inputs(
        tmp_path, policy
    )
    monkeypatch.setattr(sys, "argv", [
        "apply_automation.py", str(als_path), str(sections_path),
        str(output_path), str(report_path),
    ])

    apply_automation.main()

    with gzip.open(output_path, "rb") as handle:
        root = ET.fromstring(handle.read())
    tracks = list(root.iter("AudioTrack"))
    assert len(tracks) == 2
    for track in tracks:
        clips = list(track.iter("AudioClip"))
        assert len(clips) == 2
        assert _matches_clip_boundary(clips, 600.0)
        assert all(
            _value(clip, "CurrentEnd") > float(clip.get("Time"))
            for clip in clips
        )


def test_non_landmark_build_leaves_clip_geometry_unsplit(tmp_path, monkeypatch):
    als_path, sections_path, output_path, report_path = _write_build_inputs(
        tmp_path, "legacy_v1"
    )
    monkeypatch.setattr(sys, "argv", [
        "apply_automation.py", str(als_path), str(sections_path),
        str(output_path), str(report_path),
    ])

    apply_automation.main()

    with gzip.open(output_path, "rb") as handle:
        root = ET.fromstring(handle.read())
    assert [len(list(track.iter("AudioClip"))) for track in root.iter("AudioTrack")] == [1, 1]
