"""Template-based ALS XML patching.

Decompresses a known-good Ableton Live 12 template, patches in audio clips
with warp markers, automation envelopes, and gain offsets, then recompresses.

CRITICAL: Must use raw line-level text ops for patching. Python's XmlWriter /
ElementTree reformats the document and Ableton rejects it as corrupt.
"""

from __future__ import annotations

import gzip
import os
import re
from pathlib import Path
from dataclasses import dataclass

from automated_dj_mixes.analysis import TrackAnalysis
from automated_dj_mixes.warping import WarpMarker
from automated_dj_mixes.automation import AutomationPoint

_NEXT_ID = 50000


def _alloc_id() -> int:
    global _NEXT_ID
    _NEXT_ID += 1
    return _NEXT_ID


@dataclass
class TrackPatch:
    """Everything needed to patch one track into the ALS template."""
    analysis: TrackAnalysis
    track_index: int
    warp_markers: list[WarpMarker]
    gain_offset_db: float = 0.0
    arrangement_start_beats: float = 0.0
    warp_mode: int = 4  # 4 = Complex Pro, 6 = Repitch


def decompress_als(als_path: Path) -> list[str]:
    """Decompress an ALS file to a list of text lines."""
    with gzip.open(als_path, "rb") as f:
        content = f.read().decode("utf-8")
    return content.splitlines(keepends=True)


def compress_als(lines: list[str], output_path: Path) -> Path:
    """Compress lines back into an ALS file."""
    content = "".join(lines)
    raw_bytes = content.encode("utf-8")
    with gzip.open(output_path, "wb") as f:
        f.write(raw_bytes)
    return output_path


def _find_track_line_ranges(lines: list[str]) -> list[tuple[int, int, str]]:
    """Find the line ranges for each AudioTrack. Returns [(start, end, name), ...]."""
    tracks = []
    track_start = None
    depth = 0
    track_name = ""

    for i, line in enumerate(lines):
        if "<AudioTrack " in line:
            track_start = i
            depth = 1
            track_name = ""
        elif track_start is not None:
            if "<EffectiveName" in line and not track_name:
                m = re.search(r'Value="([^"]*)"', line)
                if m:
                    track_name = m.group(1)
            if "<AudioTrack " in line:
                depth += 1
            if "</AudioTrack>" in line:
                depth -= 1
                if depth == 0:
                    tracks.append((track_start, i, track_name))
                    track_start = None

    return tracks


def _db_to_ableton_volume(db: float) -> float:
    """Convert dB to Ableton's linear volume scale. 0dB = 1.0."""
    return 10 ** (db / 20.0)


def _find_automation_target_id(lines: list[str], start: int, end: int, device_name: str, param_name: str) -> str | None:
    """Find the AutomationTarget Id for a specific device parameter within a track's line range."""
    in_device = False
    in_param = False

    for i in range(start, end + 1):
        line = lines[i]
        if f"<{device_name} " in line or f"<{device_name}>" in line:
            in_device = True
        if in_device and f"</{device_name}>" in line:
            in_device = False
            in_param = False
        if in_device and f"<{param_name}>" in line:
            in_param = True
        if in_param and "AutomationTarget Id=" in line:
            m = re.search(r'Id="(\d+)"', line)
            if m:
                return m.group(1)
            in_param = False

    return None


def _find_filter_target_id(lines: list[str], start: int, end: int, filter_type: str) -> str | None:
    """Find the AutomationTarget Id for a specific AutoFilter2's Filter_Frequency.

    filter_type: 'lp' (value ~20000 = low-pass) or 'hp' (value ~20 = high-pass).
    Distinguishes the two AutoFilter2 instances by their current Manual frequency value.
    """
    lp_threshold = 1000.0
    in_autofilter = False
    filter_freq_value = None
    target_id = None

    for i in range(start, end + 1):
        line = lines[i]
        if "<AutoFilter2 " in line:
            in_autofilter = True
            filter_freq_value = None
            target_id = None

        if in_autofilter and "</AutoFilter2>" in line:
            if filter_freq_value is not None and target_id is not None:
                is_lp = filter_freq_value > lp_threshold
                if (filter_type == "lp" and is_lp) or (filter_type == "hp" and not is_lp):
                    return target_id
            in_autofilter = False

        if in_autofilter and "<Filter_Frequency>" in line:
            for j in range(i + 1, min(i + 10, end + 1)):
                if "Manual Value=" in lines[j]:
                    m = re.search(r'Value="([^"]*)"', lines[j])
                    if m:
                        filter_freq_value = float(m.group(1))
                if "AutomationTarget Id=" in lines[j]:
                    m = re.search(r'Id="(\d+)"', lines[j])
                    if m:
                        target_id = m.group(1)
                if "</Filter_Frequency>" in lines[j]:
                    break

    return None


def _find_volume_target_id(lines: list[str], start: int, end: int) -> str | None:
    """Find the Mixer Volume AutomationTarget Id for a track."""
    in_mixer = False
    in_volume = False
    for i in range(start, end + 1):
        line = lines[i]
        if "<Mixer>" in line:
            in_mixer = True
        if in_mixer and "</Mixer>" in line:
            return None
        if in_mixer and "<Volume>" in line:
            in_volume = True
        if in_volume:
            if "AutomationTarget Id=" in line:
                m = re.search(r'Id="(\d+)"', line)
                if m:
                    return m.group(1)
            if "</Volume>" in line:
                in_volume = False
    return None


def _find_utility_gain_target_id(lines: list[str], start: int, end: int) -> str | None:
    """Find the Utility plugin's Gain AutomationTarget Id (StereoGain > Gain).

    This is the gain control on the Utility plugin (first in device chain),
    NOT the channel mixer fader on the right. Volume automation goes here
    so the mixer fader stays free for manual tweaking during playback.
    """
    in_stereogain = False
    in_gain = False
    for i in range(start, end + 1):
        line = lines[i]
        if "<StereoGain " in line or "<StereoGain>" in line:
            in_stereogain = True
        if in_stereogain and "</StereoGain>" in line:
            return None
        if in_stereogain and "<Gain>" in line:
            in_gain = True
        if in_gain:
            if "AutomationTarget Id=" in line:
                m = re.search(r'Id="(\d+)"', line)
                if m:
                    return m.group(1)
            if "</Gain>" in line:
                in_gain = False
    return None


def _find_main_track_envelopes_line(lines: list[str]) -> int | None:
    """Find the <Envelopes> line inside <MainTrack><AutomationEnvelopes>."""
    in_main = False
    for i, line in enumerate(lines):
        if "<MainTrack" in line:
            in_main = True
        if in_main and "<AutomationEnvelopes>" in line:
            for j in range(i, min(i + 5, len(lines))):
                if "<Envelopes" in lines[j]:
                    return j
            return None
    return None


def _remove_existing_envelope_for_target(lines: list[str], target_id: str) -> int:
    """Remove any AutomationEnvelope with the given PointeeId. Returns lines removed."""
    pointee_match = f'PointeeId Value="{target_id}"'
    for i, line in enumerate(lines):
        if pointee_match in line:
            # Walk backwards to find <AutomationEnvelope opening
            env_start = i
            while env_start > 0 and "<AutomationEnvelope " not in lines[env_start]:
                env_start -= 1
            # Walk forwards to find </AutomationEnvelope>
            env_end = i
            while env_end < len(lines) and "</AutomationEnvelope>" not in lines[env_end]:
                env_end += 1
            if env_end < len(lines):
                removed = env_end - env_start + 1
                del lines[env_start:env_end + 1]
                return removed
    return 0


def _insert_main_track_tempo_envelope(
    lines: list[str], tempo_points: list[tuple[float, float]], target_id: str = "8"
) -> int:
    """Insert a tempo automation envelope into the MainTrack. Removes any existing
    envelope for the same target first, so the template's default 120 BPM envelope
    doesn't conflict with our new one."""
    _remove_existing_envelope_for_target(lines, target_id)

    env_line = _find_main_track_envelopes_line(lines)
    if env_line is None:
        return 0

    env_indent = lines[env_line].split("<Envelopes")[0].replace("\r", "").replace("\n", "")
    content_indent = env_indent + "\t"
    envelope_lines = _build_envelope_xml(target_id, tempo_points, indent=content_indent)

    if "<Envelopes />" in lines[env_line]:
        lines[env_line:env_line + 1] = [
            f"{env_indent}<Envelopes>\r\n",
            *envelope_lines,
            f"{env_indent}</Envelopes>\r\n",
        ]
        return len(envelope_lines) + 1
    elif "<Envelopes>" in lines[env_line]:
        close_idx = env_line + 1
        depth = 1
        while close_idx < len(lines):
            if "<Envelopes>" in lines[close_idx]:
                depth += 1
            if "</Envelopes>" in lines[close_idx]:
                depth -= 1
                if depth == 0:
                    break
            close_idx += 1
        lines[close_idx:close_idx] = envelope_lines
        return len(envelope_lines)
    return 0


def _find_eq_bass_target_id(lines: list[str], start: int, end: int) -> str | None:
    """Find the ChannelEq LowShelfGain AutomationTarget Id for a track."""
    in_eq = False
    in_low = False
    for i in range(start, end + 1):
        line = lines[i]
        if "<ChannelEq " in line or "<ChannelEq>" in line:
            in_eq = True
        if in_eq and "</ChannelEq>" in line:
            in_eq = False
            in_low = False
        if in_eq and "<LowShelfGain>" in line:
            in_low = True
        if in_low:
            if "AutomationTarget Id=" in line:
                m = re.search(r'Id="(\d+)"', line)
                if m:
                    return m.group(1)
            if "</LowShelfGain>" in line:
                in_low = False
    return None


def _build_envelope_xml(target_id: str, points: list[tuple[float, float]], indent: str = "\t\t\t\t\t\t") -> list[str]:
    """Build automation envelope XML lines for a given AutomationTarget."""
    t = indent
    default_value = points[0][1] if points else 0.0
    envelope_lines = [
        f"{t}<AutomationEnvelope Id=\"{_alloc_id()}\">\r\n",
        f"{t}\t<EnvelopeTarget>\r\n",
        f"{t}\t\t<PointeeId Value=\"{target_id}\" />\r\n",
        f"{t}\t</EnvelopeTarget>\r\n",
        f"{t}\t<Automation>\r\n",
        f"{t}\t\t<Events>\r\n",
        f"{t}\t\t\t<FloatEvent Id=\"{_alloc_id()}\" Time=\"-63072000\" Value=\"{default_value}\" />\r\n",
    ]
    for time_val, value in points:
        envelope_lines.append(
            f"{t}\t\t\t<FloatEvent Id=\"{_alloc_id()}\" Time=\"{time_val}\" Value=\"{value}\" />\r\n"
        )
    envelope_lines.extend([
        f"{t}\t\t</Events>\r\n",
        f"{t}\t\t<AutomationTransformViewState>\r\n",
        f"{t}\t\t\t<IsTransformPending Value=\"false\" />\r\n",
        f"{t}\t\t\t<TimeAndValueTransforms />\r\n",
        f"{t}\t\t</AutomationTransformViewState>\r\n",
        f"{t}\t</Automation>\r\n",
        f"{t}</AutomationEnvelope>\r\n",
    ])
    return envelope_lines


def _xml_escape(value: str) -> str:
    """Escape XML special characters for use inside attribute values."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _set_track_name(lines: list[str], start: int, end: int, name: str) -> None:
    """Set the track's EffectiveName and UserName."""
    safe = _xml_escape(name)
    for i in range(start, min(start + 30, end)):
        if "<EffectiveName" in lines[i]:
            lines[i] = re.sub(r'Value="[^"]*"', f'Value="{safe}"', lines[i])
        if "<UserName" in lines[i] and i > start + 5:
            lines[i] = re.sub(r'Value="[^"]*"', f'Value="{safe}"', lines[i])
            break


def _set_master_volume_level(lines: list[str], gain_db: float) -> None:
    """Set the MainTrack (master) Volume Manual value to a fixed level.

    Used to attenuate the whole project (e.g. -6 dB) to prevent clipping when
    summing many mastered tracks. The mixer fader on the master shows this
    level but isn't automated — Sam can still ride it manually.
    """
    ableton_val = _db_to_ableton_volume(gain_db)
    in_main = False
    in_volume = False
    for i, line in enumerate(lines):
        if "<MainTrack" in line:
            in_main = True
        if in_main and "</MainTrack>" in line:
            return
        if in_main and "<Volume>" in line:
            in_volume = True
        if in_volume and "Manual Value=" in line:
            lines[i] = re.sub(r'Manual Value="[^"]*"', f'Manual Value="{ableton_val}"', lines[i])
            return


def _set_mixer_volume_level(lines: list[str], start: int, end: int, gain_db: float) -> None:
    """Set the Mixer Volume fader to a static level (for LUFS matching).

    The fader stays at this position by default but is not automated, so it
    remains available for manual tweaking during playback. Volume automation
    goes on the Utility plugin's Gain parameter instead.
    """
    ableton_val = _db_to_ableton_volume(gain_db)
    in_mixer = False
    in_volume = False
    for i in range(start, end + 1):
        if "<Mixer>" in lines[i]:
            in_mixer = True
        if in_mixer and "</Mixer>" in lines[i]:
            return
        if in_mixer and "<Volume>" in lines[i]:
            in_volume = True
        if in_volume and "Manual Value=" in lines[i]:
            lines[i] = re.sub(r'Manual Value="[^"]*"', f'Manual Value="{ableton_val}"', lines[i])
            return


def _find_arranger_events_line(lines: list[str], start: int, end: int) -> int | None:
    """Find the <Events /> line inside <Sample><ArrangerAutomation> for a track."""
    in_sample = False
    in_arranger = False
    for i in range(start, end + 1):
        if "<Sample>" in lines[i]:
            in_sample = True
        if in_sample and "<ArrangerAutomation>" in lines[i]:
            in_arranger = True
        if in_arranger and "<Events" in lines[i]:
            return i
    return None


def _find_track_envelopes_line(lines: list[str], start: int, end: int) -> int | None:
    """Find the <Envelopes /> line inside the track-level <AutomationEnvelopes>."""
    for i in range(start, min(start + 25, end)):
        if "<AutomationEnvelopes>" in lines[i]:
            for j in range(i, min(i + 5, end)):
                if "<Envelopes" in lines[j]:
                    return j
    return None


def _build_file_ref_xml(track_path: Path, output_path: Path, indent: str) -> list[str]:
    """Build FileRef XML block matching Ableton Live 12.3 format."""
    abs_path = str(track_path.resolve()).replace("\\", "/")
    file_size = track_path.stat().st_size if track_path.exists() else 0

    try:
        rel_path = os.path.relpath(track_path.resolve(), output_path.resolve().parent).replace("\\", "/")
        rel_type = "1"
    except ValueError:
        rel_path = ""
        rel_type = "0"

    return [
        f"{indent}<FileRef>\n",
        f"{indent}\t<RelativePathType Value=\"{rel_type}\" />\n",
        f"{indent}\t<RelativePath Value=\"{rel_path}\" />\n",
        f"{indent}\t<Path Value=\"{abs_path}\" />\n",
        f"{indent}\t<Type Value=\"1\" />\n",
        f"{indent}\t<LivePackName Value=\"\" />\n",
        f"{indent}\t<LivePackId Value=\"\" />\n",
        f"{indent}\t<OriginalFileSize Value=\"{file_size}\" />\n",
        f"{indent}\t<OriginalCrc Value=\"0\" />\n",
        f"{indent}\t<SourceHint Value=\"\" />\n",
        f"{indent}</FileRef>\n",
    ]


def _build_audio_clip_xml(
    patch: TrackPatch,
    output_path: Path,
    indent: str = "\t\t\t\t\t\t\t\t\t",
) -> list[str]:
    """Build AudioClip XML using Ableton Live 12.3's proven reference format."""
    clip_id = _alloc_id()
    take_id = _alloc_id()
    a = patch.analysis
    name = _xml_escape(a.path.stem)
    warp_markers = patch.warp_markers
    arr_start = patch.arrangement_start_beats

    duration_sec = a.duration_sec or 0.0
    duration_beats = warp_markers[-1].beat_time if warp_markers else duration_sec
    sample_count = int(duration_sec * (a.sample_rate or 44100))
    sr = a.sample_rate or 44100

    abs_path = _xml_escape(str(a.path.resolve()).replace("\\", "/"))
    try:
        rel_path = _xml_escape(os.path.relpath(a.path.resolve(), output_path.resolve().parent).replace("\\", "/"))
    except ValueError:
        rel_path = abs_path
    file_size = a.path.stat().st_size if a.path.exists() else 0

    t = indent
    xml = f"""{t}<AudioClip Id="{clip_id}" Time="{arr_start}">
{t}\t<LomId Value="0" />
{t}\t<LomIdView Value="0" />
{t}\t<CurrentStart Value="{arr_start}" />
{t}\t<CurrentEnd Value="{arr_start + duration_beats}" />
{t}\t<Loop>
{t}\t\t<LoopStart Value="0" />
{t}\t\t<LoopEnd Value="{duration_beats}" />
{t}\t\t<StartRelative Value="0" />
{t}\t\t<LoopOn Value="false" />
{t}\t\t<OutMarker Value="{duration_beats}" />
{t}\t\t<HiddenLoopStart Value="0" />
{t}\t\t<HiddenLoopEnd Value="{duration_beats}" />
{t}\t</Loop>
{t}\t<Name Value="{name}" />
{t}\t<Annotation Value="" />
{t}\t<Color Value="37" />
{t}\t<LaunchMode Value="0" />
{t}\t<LaunchQuantisation Value="0" />
{t}\t<TimeSignature>
{t}\t\t<TimeSignatures>
{t}\t\t\t<RemoteableTimeSignature Id="0">
{t}\t\t\t\t<Numerator Value="4" />
{t}\t\t\t\t<Denominator Value="4" />
{t}\t\t\t\t<Time Value="0" />
{t}\t\t\t</RemoteableTimeSignature>
{t}\t\t</TimeSignatures>
{t}\t</TimeSignature>
{t}\t<Envelopes>
{t}\t\t<Envelopes />
{t}\t</Envelopes>
{t}\t<ScrollerTimePreserver>
{t}\t\t<LeftTime Value="0" />
{t}\t\t<RightTime Value="{duration_beats}" />
{t}\t</ScrollerTimePreserver>
{t}\t<TimeSelection>
{t}\t\t<AnchorTime Value="0" />
{t}\t\t<OtherTime Value="0" />
{t}\t</TimeSelection>
{t}\t<Legato Value="false" />
{t}\t<Ram Value="false" />
{t}\t<GrooveSettings>
{t}\t\t<GrooveId Value="-1" />
{t}\t</GrooveSettings>
{t}\t<Disabled Value="false" />
{t}\t<VelocityAmount Value="0" />
{t}\t<FollowAction>
{t}\t\t<FollowTime Value="4" />
{t}\t\t<IsLinked Value="true" />
{t}\t\t<LoopIterations Value="1" />
{t}\t\t<FollowActionA Value="4" />
{t}\t\t<FollowActionB Value="0" />
{t}\t\t<FollowChanceA Value="100" />
{t}\t\t<FollowChanceB Value="0" />
{t}\t\t<JumpIndexA Value="1" />
{t}\t\t<JumpIndexB Value="1" />
{t}\t\t<FollowActionEnabled Value="false" />
{t}\t</FollowAction>
{t}\t<Grid>
{t}\t\t<FixedNumerator Value="1" />
{t}\t\t<FixedDenominator Value="16" />
{t}\t\t<GridIntervalPixel Value="20" />
{t}\t\t<Ntoles Value="2" />
{t}\t\t<SnapToGrid Value="true" />
{t}\t\t<Fixed Value="false" />
{t}\t</Grid>
{t}\t<FreezeStart Value="0" />
{t}\t<FreezeEnd Value="0" />
{t}\t<IsWarped Value="true" />
{t}\t<TakeId Value="{take_id}" />
{t}\t<IsInKey Value="true" />
{t}\t<ScaleInformation>
{t}\t\t<Root Value="0" />
{t}\t\t<Name Value="0" />
{t}\t</ScaleInformation>
{t}\t<SampleRef>
{t}\t\t<FileRef>
{t}\t\t\t<RelativePathType Value="1" />
{t}\t\t\t<RelativePath Value="{rel_path}" />
{t}\t\t\t<Path Value="{abs_path}" />
{t}\t\t\t<Type Value="1" />
{t}\t\t\t<LivePackName Value="" />
{t}\t\t\t<LivePackId Value="" />
{t}\t\t\t<OriginalFileSize Value="{file_size}" />
{t}\t\t\t<OriginalCrc Value="0" />
{t}\t\t\t<SourceHint Value="" />
{t}\t\t</FileRef>
{t}\t\t<LastModDate Value="0" />
{t}\t\t<SourceContext />
{t}\t\t<SampleUsageHint Value="0" />
{t}\t\t<DefaultDuration Value="{sample_count}" />
{t}\t\t<DefaultSampleRate Value="{sr}" />
{t}\t\t<SamplesToAutoWarp Value="0" />
{t}\t</SampleRef>
{t}\t<Onsets>
{t}\t\t<UserOnsets />
{t}\t\t<HasUserOnsets Value="false" />
{t}\t</Onsets>
{t}\t<WarpMode Value="{patch.warp_mode}" />
{t}\t<GranularityTones Value="30" />
{t}\t<GranularityTexture Value="65" />
{t}\t<FluctuationTexture Value="25" />
{t}\t<TransientResolution Value="6" />
{t}\t<TransientLoopMode Value="2" />
{t}\t<TransientEnvelope Value="100" />
{t}\t<ComplexProFormants Value="100" />
{t}\t<ComplexProEnvelope Value="128" />
{t}\t<Sync Value="true" />
{t}\t<HiQ Value="true" />
{t}\t<Fade Value="false" />
{t}\t<Fades>
{t}\t\t<FadeInLength Value="0" />
{t}\t\t<FadeOutLength Value="0" />
{t}\t\t<ClipFadesAreInitialized Value="true" />
{t}\t\t<CrossfadeInState Value="0" />
{t}\t\t<FadeInCurveSkew Value="0" />
{t}\t\t<FadeInCurveSlope Value="0" />
{t}\t\t<FadeOutCurveSkew Value="0" />
{t}\t\t<FadeOutCurveSlope Value="0" />
{t}\t\t<IsDefaultFadeIn Value="false" />
{t}\t\t<IsDefaultFadeOut Value="false" />
{t}\t</Fades>
{t}\t<PitchCoarse Value="0" />
{t}\t<PitchFine Value="0" />
{t}\t<SampleVolume Value="1" />
{t}\t<WarpMarkers>
"""
    for wm in warp_markers:
        xml += f"""{t}\t\t<WarpMarker Id="{_alloc_id()}" SecTime="{wm.sample_time}" BeatTime="{wm.beat_time}" />\n"""
    xml += f"""{t}\t</WarpMarkers>
{t}\t<SavedWarpMarkersForStretched />
{t}\t<MarkersGenerated Value="true" />
{t}\t<IsSongTempoLeader Value="false" />
{t}</AudioClip>
"""
    return [line + "\r\n" for line in xml.split("\n") if line.strip()]


def _insert_audio_clip(lines: list[str], start: int, end: int, clip_lines: list[str]) -> int:
    """Insert audio clip XML into a track's ArrangerAutomation Events. Returns line count delta."""
    events_line = _find_arranger_events_line(lines, start, end)
    if events_line is None:
        return 0

    indent = lines[events_line].split("<Events")[0].rstrip("\r\n")

    if "<Events />" in lines[events_line]:
        lines[events_line:events_line + 1] = [
            f"{indent}<Events>\r\n",
            *clip_lines,
            f"{indent}</Events>\r\n",
        ]
        return len(clip_lines) + 1
    elif "<Events>" in lines[events_line]:
        close_idx = events_line + 1
        while close_idx <= end and "</Events>" not in lines[close_idx]:
            close_idx += 1
        lines[close_idx:close_idx] = clip_lines
        return len(clip_lines)

    return 0


def _insert_automation_envelopes(
    lines: list[str],
    start: int,
    end: int,
    envelope_blocks: list[list[str]],
) -> int:
    """Insert automation envelopes into a track's AutomationEnvelopes section. Returns line count delta."""
    env_line = _find_track_envelopes_line(lines, start, end)
    if env_line is None:
        return 0

    all_env_lines = []
    for block in envelope_blocks:
        all_env_lines.extend(block)

    env_indent = lines[env_line].split("<Envelopes")[0]
    env_indent = env_indent.replace("\r", "").replace("\n", "")

    if "<Envelopes />" in lines[env_line]:
        lines[env_line:env_line + 1] = [
            f"{env_indent}<Envelopes>\r\n",
            *all_env_lines,
            f"{env_indent}</Envelopes>\r\n",
        ]
        return len(all_env_lines) + 1
    elif "<Envelopes>" in lines[env_line]:
        close_idx = env_line + 1
        depth = 1
        while close_idx <= end:
            if "<Envelopes>" in lines[close_idx]:
                depth += 1
            if "</Envelopes>" in lines[close_idx]:
                depth -= 1
                if depth == 0:
                    break
            close_idx += 1
        lines[close_idx:close_idx] = all_env_lines
        return len(all_env_lines)

    return 0


def generate_session(
    template_path: Path,
    patches: list[TrackPatch],
    output_path: Path,
    project_bpm: float = 128.0,
    transition_automation: dict[int, list[tuple[str, list[AutomationPoint]]]] | None = None,
    tempo_automation: list[AutomationPoint] | None = None,
) -> Path:
    """Generate a complete ALS session from template + track patches.

    Patches are applied to tracks 2-12 (index 0 = track 2, skipping track 1 / Session Time).
    transition_automation maps track_index -> [(device_param_key, points), ...] for envelopes.
    """
    global _NEXT_ID
    _NEXT_ID = 50000

    lines = decompress_als(template_path)
    track_ranges = _find_track_line_ranges(lines)

    available_tracks = track_ranges[1:]

    if len(patches) > len(available_tracks):
        print(
            f"WARNING: Template has {len(available_tracks)} audio tracks but "
            f"{len(patches)} patches provided — using first {len(available_tracks)} tracks. "
            f"Add more tracks to the template to fit the full mix."
        )
        patches = patches[: len(available_tracks)]

    offset = 0
    for patch in patches:
        if patch.track_index >= len(available_tracks):
            continue

        start, end, _ = available_tracks[patch.track_index]
        start += offset
        end += offset

        track_name = patch.analysis.path.stem
        _set_track_name(lines, start, end, track_name)

        if patch.gain_offset_db != 0.0:
            _set_mixer_volume_level(lines, start, end, patch.gain_offset_db)

        events_line = _find_arranger_events_line(lines, start, end)
        events_indent = lines[events_line].split("<Events")[0].rstrip("\r\n") if events_line else "\t\t\t\t\t\t\t\t"
        clip_indent = events_indent + "\t"
        clip_xml = _build_audio_clip_xml(patch, output_path, indent=clip_indent)
        delta = _insert_audio_clip(lines, start, end, clip_xml)
        offset += delta
        end += delta

        if transition_automation and patch.track_index in transition_automation:
            env_line = _find_track_envelopes_line(lines, start, end)
            env_indent = "\t\t\t\t\t"
            if env_line is not None:
                env_indent = lines[env_line].split("<Envelopes")[0].replace("\r", "").replace("\n", "")
            content_indent = env_indent + "\t"

            envelope_blocks = []
            for param_key, points in transition_automation[patch.track_index]:
                target_id = None
                if param_key == "lp_filter":
                    target_id = _find_filter_target_id(lines, start, end, "lp")
                elif param_key == "hp_filter":
                    target_id = _find_filter_target_id(lines, start, end, "hp")
                elif param_key == "volume":
                    # Utility plugin Gain — leaves the mixer fader free for manual tweaking
                    target_id = _find_utility_gain_target_id(lines, start, end)
                elif param_key == "eq_bass":
                    target_id = _find_eq_bass_target_id(lines, start, end)
                else:
                    parts = param_key.split(".", 1)
                    if len(parts) == 2:
                        target_id = _find_automation_target_id(lines, start, end, parts[0], parts[1])

                if target_id:
                    env_points = [(p.time_beats, p.value) for p in points]
                    envelope_blocks.append(_build_envelope_xml(target_id, env_points, indent=content_indent))

            if envelope_blocks:
                delta2 = _insert_automation_envelopes(lines, start, end, envelope_blocks)
                offset += delta2

    _set_project_bpm(lines, project_bpm)
    # Master at -6dB by default to prevent clipping when summing mastered tracks.
    # Sam can still ride the master fader manually since it's not automated.
    _set_master_volume_level(lines, -6.0)

    if tempo_automation:
        tempo_points = [(p.time_beats, p.value) for p in tempo_automation]
        _insert_main_track_tempo_envelope(lines, tempo_points)

    return compress_als(lines, output_path)


def _set_project_bpm(lines: list[str], bpm: float) -> None:
    """Set the project tempo in the MasterTrack."""
    for i, line in enumerate(lines):
        if "<Tempo>" in line:
            for j in range(i, min(i + 5, len(lines))):
                if "Manual Value=" in lines[j]:
                    lines[j] = re.sub(r'Manual Value="[^"]*"', f'Manual Value="{bpm}"', lines[j])
                    return
