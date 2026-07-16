from __future__ import annotations

import gzip
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

from analyze_correction_diff import analyse


def _als(path: Path, *, second_start: float, marker_sec: float = 0.5) -> Path:
    tracks = []
    for index, (name, start) in enumerate((("Out", 0.0), ("In", second_start))):
        end = 64.0 if index == 0 else start + 64.0
        volume_target = 100 + index * 10
        bass_target = volume_target + 1
        volume = [(start, 0.2), (start + 32, 1.0)] if index else [(0, 1), (64, 0)]
        bass = [(start, 0.18), (start + 32, 1.0)] if index else [(0, 1), (start + 32, 0.18)]
        def envelope(target: int, points: list[tuple[float, float]]) -> str:
            events = "".join(
                f'<FloatEvent Time="{time}" Value="{value}" />'
                for time, value in points
            )
            return (
                f'<AutomationEnvelope><EnvelopeTarget><PointeeId Value="{target}" />'
                f'</EnvelopeTarget><Automation><Events>{events}</Events></Automation>'
                f'</AutomationEnvelope>'
            )
        tracks.append(
            f'''<AudioTrack Id="{index + 1}">
<Name><EffectiveName Value="{name}" /></Name>
<DeviceChain><Devices><StereoGain><Gain><AutomationTarget Id="{volume_target}" /></Gain></StereoGain>
<ChannelEq><LowShelfGain><AutomationTarget Id="{bass_target}" /></LowShelfGain></ChannelEq></Devices></DeviceChain>
<AutomationEnvelopes><Envelopes>{envelope(volume_target, volume)}{envelope(bass_target, bass)}</Envelopes></AutomationEnvelopes>
<Sample><ArrangerAutomation><Events><AudioClip Id="{index + 20}" Time="{start}">
<CurrentEnd Value="{end}" /><Loop><LoopStart Value="0" /><LoopEnd Value="64" /></Loop>
<Name Value="{'drop_1' if index == 0 else 'intro_1'}" /><Color Value="12" /><WarpMode Value="4" />
<WarpMarkers><WarpMarker Id="1" SecTime="0" BeatTime="0" /><WarpMarker Id="2" SecTime="{marker_sec}" BeatTime="1" /></WarpMarkers>
</AudioClip></Events></ArrangerAutomation></Sample></AudioTrack>'''
        )
    xml = f"<Ableton><LiveSet><Tracks>{''.join(tracks)}</Tracks></LiveSet></Ableton>"
    with gzip.open(path, "wb") as handle:
        handle.write(xml.encode("utf-8"))
    return path


def test_analyse_reports_arrangement_delta_and_preserved_warp(tmp_path: Path) -> None:
    baseline = _als(tmp_path / "baseline.als", second_start=32.0)
    corrected = _als(tmp_path / "corrected.als", second_start=16.0)

    result = analyse(baseline, corrected)

    assert result["all_warp_grids_preserved"] is True
    assert result["transitions"][0]["overlap_delta_beats"] == 16.0
    assert result["tracks"][1]["start_delta_beats"] == -16.0


def test_analyse_detects_warp_grid_change(tmp_path: Path) -> None:
    baseline = _als(tmp_path / "baseline.als", second_start=32.0)
    corrected = _als(
        tmp_path / "corrected.als", second_start=32.0, marker_sec=0.51
    )

    result = analyse(baseline, corrected)

    assert result["all_warp_grids_preserved"] is False
    assert result["tracks"][0]["warp_grid_preserved"] is False
