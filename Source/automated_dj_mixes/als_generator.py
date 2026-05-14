"""Template-based ALS XML patching.

Decompresses a known-good Ableton Live 12 template, patches in audio clips
with warp markers, automation envelopes, and gain offsets, then recompresses.

CRITICAL: Must use raw line-level text ops for patching. Python's XmlWriter /
ElementTree reformats the document and Ableton rejects it as corrupt.
"""

from __future__ import annotations

import gzip
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


def _build_envelope_xml(target_id: str, points: list[tuple[float, float]], indent: str = "\t\t\t\t") -> list[str]:
    """Build automation envelope XML lines for a given AutomationTarget."""
    envelope_lines = [
        f"{indent}<AutomationEnvelope Id=\"{_alloc_id()}\">\n",
        f"{indent}\t<EnvelopeTarget>\n",
        f"{indent}\t\t<PointeeId Value=\"{target_id}\" />\n",
        f"{indent}\t</EnvelopeTarget>\n",
        f"{indent}\t<Automation>\n",
        f"{indent}\t\t<Events>\n",
    ]
    for i, (time_val, value) in enumerate(points):
        envelope_lines.append(
            f"{indent}\t\t\t<AutomationEvent Id=\"{i}\" Time=\"{time_val}\" Value=\"{value}\" />\n"
        )
    envelope_lines.extend([
        f"{indent}\t\t</Events>\n",
        f"{indent}\t</Automation>\n",
        f"{indent}</AutomationEnvelope>\n",
    ])
    return envelope_lines


def _set_track_name(lines: list[str], start: int, end: int, name: str) -> None:
    """Set the track's EffectiveName and UserName."""
    for i in range(start, min(start + 30, end)):
        if "<EffectiveName" in lines[i]:
            lines[i] = re.sub(r'Value="[^"]*"', f'Value="{name}"', lines[i])
        if "<UserName" in lines[i] and i > start + 5:
            lines[i] = re.sub(r'Value="[^"]*"', f'Value="{name}"', lines[i])
            break


def _set_utility_gain(lines: list[str], start: int, end: int, gain_db: float) -> None:
    """Set the Utility (StereoGain) Gain parameter."""
    ableton_val = _db_to_ableton_volume(gain_db)
    in_stereogain = False
    in_gain = False

    for i in range(start, end + 1):
        if "<StereoGain " in lines[i]:
            in_stereogain = True
        if in_stereogain and "</StereoGain>" in lines[i]:
            break
        if in_stereogain and "<Gain>" in lines[i]:
            in_gain = True
        if in_gain and 'Manual Value=' in lines[i]:
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


def _build_file_ref_xml(track_path: Path, indent: str) -> list[str]:
    """Build FileRef XML block for an audio file."""
    abs_path = str(track_path.resolve())
    file_size = track_path.stat().st_size if track_path.exists() else 0

    return [
        f"{indent}<FileRef>\n",
        f"{indent}\t<RelativePathType Value=\"3\" />\n",
        f"{indent}\t<RelativePath Value=\"\" />\n",
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
    indent: str = "\t\t\t\t\t\t\t",
) -> list[str]:
    """Build AudioClip XML for insertion into ArrangerAutomation Events."""
    clip_id = _alloc_id()
    a = patch.analysis
    name = a.path.stem
    warp_markers = patch.warp_markers
    arr_start = patch.arrangement_start_beats

    duration_beats = warp_markers[-1].beat_time if len(warp_markers) >= 2 else 0.0
    sample_count = int(a.duration_sec * a.sample_rate) if a.duration_sec and a.sample_rate else 0
    sr = a.sample_rate or 44100

    t = indent
    lines = []

    lines.append(f'{t}<AudioClip Id="{clip_id}" Time="{arr_start}">\n')
    lines.append(f'{t}\t<LomId Value="0" />\n')
    lines.append(f'{t}\t<LomIdView Value="0" />\n')
    lines.append(f'{t}\t<CurrentStart Value="{arr_start}" />\n')
    lines.append(f'{t}\t<CurrentEnd Value="{arr_start + duration_beats}" />\n')

    lines.append(f'{t}\t<Loop>\n')
    lines.append(f'{t}\t\t<LoopStart Value="0" />\n')
    lines.append(f'{t}\t\t<LoopEnd Value="{duration_beats}" />\n')
    lines.append(f'{t}\t\t<StartRelative Value="0" />\n')
    lines.append(f'{t}\t\t<LoopOn Value="false" />\n')
    lines.append(f'{t}\t\t<OutMarker Value="{duration_beats}" />\n')
    lines.append(f'{t}\t\t<HiddenLoopStart Value="0" />\n')
    lines.append(f'{t}\t\t<HiddenLoopEnd Value="{duration_beats}" />\n')
    lines.append(f'{t}\t</Loop>\n')

    lines.append(f'{t}\t<Name Value="{name}" />\n')
    lines.append(f'{t}\t<Annotation Value="" />\n')
    lines.append(f'{t}\t<Color Value="-1" />\n')
    lines.append(f'{t}\t<LaunchMode Value="0" />\n')
    lines.append(f'{t}\t<LaunchQuantisation Value="0" />\n')

    lines.append(f'{t}\t<TimeSignature>\n')
    lines.append(f'{t}\t\t<TimeSignatures>\n')
    lines.append(f'{t}\t\t\t<RemoteableTimeSignature Id="0">\n')
    lines.append(f'{t}\t\t\t\t<Numerator Value="4" />\n')
    lines.append(f'{t}\t\t\t\t<Denominator Value="4" />\n')
    lines.append(f'{t}\t\t\t\t<Time Value="0" />\n')
    lines.append(f'{t}\t\t\t</RemoteableTimeSignature>\n')
    lines.append(f'{t}\t\t</TimeSignatures>\n')
    lines.append(f'{t}\t</TimeSignature>\n')

    lines.append(f'{t}\t<Envelopes>\n')
    lines.append(f'{t}\t\t<Envelopes />\n')
    lines.append(f'{t}\t</Envelopes>\n')

    lines.append(f'{t}\t<ScrollerTimePreserver>\n')
    lines.append(f'{t}\t\t<LeftTime Value="0" />\n')
    lines.append(f'{t}\t\t<RightTime Value="{duration_beats}" />\n')
    lines.append(f'{t}\t</ScrollerTimePreserver>\n')

    lines.append(f'{t}\t<TimeSelection>\n')
    lines.append(f'{t}\t\t<AnchorTime Value="0" />\n')
    lines.append(f'{t}\t\t<OtherTime Value="0" />\n')
    lines.append(f'{t}\t</TimeSelection>\n')

    lines.append(f'{t}\t<Legato Value="false" />\n')
    lines.append(f'{t}\t<Ram Value="false" />\n')
    lines.append(f'{t}\t<GrooveSettings>\n')
    lines.append(f'{t}\t\t<GrooveId Value="-1" />\n')
    lines.append(f'{t}\t</GrooveSettings>\n')
    lines.append(f'{t}\t<Disabled Value="false" />\n')
    lines.append(f'{t}\t<VelocityAmount Value="0" />\n')

    lines.append(f'{t}\t<FollowAction>\n')
    lines.append(f'{t}\t\t<FollowTime Value="4" />\n')
    lines.append(f'{t}\t\t<IsLinked Value="true" />\n')
    lines.append(f'{t}\t\t<LoopIterations Value="1" />\n')
    lines.append(f'{t}\t\t<FollowActionA Value="4" />\n')
    lines.append(f'{t}\t\t<FollowActionB Value="0" />\n')
    lines.append(f'{t}\t\t<FollowChanceA Value="100" />\n')
    lines.append(f'{t}\t\t<FollowChanceB Value="0" />\n')
    lines.append(f'{t}\t\t<JumpIndexA Value="1" />\n')
    lines.append(f'{t}\t\t<JumpIndexB Value="1" />\n')
    lines.append(f'{t}\t\t<FollowActionEnabled Value="false" />\n')
    lines.append(f'{t}\t</FollowAction>\n')

    lines.append(f'{t}\t<Grid>\n')
    lines.append(f'{t}\t\t<FixedNumerator Value="1" />\n')
    lines.append(f'{t}\t\t<FixedDenominator Value="16" />\n')
    lines.append(f'{t}\t\t<GridIntervalPixel Value="20" />\n')
    lines.append(f'{t}\t\t<Ntoles Value="2" />\n')
    lines.append(f'{t}\t\t<SnapToGrid Value="true" />\n')
    lines.append(f'{t}\t\t<Fixed Value="false" />\n')
    lines.append(f'{t}\t</Grid>\n')

    lines.append(f'{t}\t<SampleRef>\n')
    lines.extend(_build_file_ref_xml(a.path, f'{t}\t\t'))
    lines.append(f'{t}\t\t<LastModDate Value="0" />\n')
    lines.append(f'{t}\t\t<SourceContext>\n')
    lines.append(f'{t}\t\t\t<SourceContext Id="0">\n')
    lines.append(f'{t}\t\t\t\t<OriginalFileRef>\n')
    lines.extend(_build_file_ref_xml(a.path, f'{t}\t\t\t\t\t'))
    lines.append(f'{t}\t\t\t\t</OriginalFileRef>\n')
    lines.append(f'{t}\t\t\t\t<BrowserContentPath Value="" />\n')
    lines.append(f'{t}\t\t\t</SourceContext>\n')
    lines.append(f'{t}\t\t</SourceContext>\n')
    lines.append(f'{t}\t\t<SampleUsageHint Value="0" />\n')
    lines.append(f'{t}\t\t<DefaultDuration Value="{sample_count}" />\n')
    lines.append(f'{t}\t\t<DefaultSampleRate Value="{sr}" />\n')
    lines.append(f'{t}\t</SampleRef>\n')

    lines.append(f'{t}\t<Onsets>\n')
    lines.append(f'{t}\t\t<UserOnsets />\n')
    lines.append(f'{t}\t\t<HasUserOnsets Value="false" />\n')
    lines.append(f'{t}\t</Onsets>\n')

    lines.append(f'{t}\t<WarpMode Value="4" />\n')
    lines.append(f'{t}\t<GranularityTones Value="30" />\n')
    lines.append(f'{t}\t<GranularityTexture Value="65" />\n')
    lines.append(f'{t}\t<FluctuationTexture Value="25" />\n')
    lines.append(f'{t}\t<ComplexProFormants Value="100" />\n')
    lines.append(f'{t}\t<ComplexProEnvelope Value="128" />\n')
    lines.append(f'{t}\t<TransientResolution Value="6" />\n')
    lines.append(f'{t}\t<TransientLoopMode Value="2" />\n')
    lines.append(f'{t}\t<TransientEnvelope Value="100" />\n')
    lines.append(f'{t}\t<IsWarped Value="true" />\n')
    lines.append(f'{t}\t<TimeShift Value="0" />\n')
    lines.append(f'{t}\t<PitchCoarse Value="0" />\n')
    lines.append(f'{t}\t<PitchFine Value="0" />\n')
    lines.append(f'{t}\t<SampleVolume Value="1" />\n')
    lines.append(f'{t}\t<MarkerDensity Value="2" />\n')
    lines.append(f'{t}\t<AutoWarpTolerance Value="4" />\n')

    lines.append(f'{t}\t<WarpMarkers>\n')
    for wm in warp_markers:
        lines.append(f'{t}\t\t<WarpMarker SecTime="{wm.sample_time}" BeatTime="{wm.beat_time}" />\n')
    lines.append(f'{t}\t</WarpMarkers>\n')

    lines.append(f'{t}\t<SavedWarpMarkersForStretched>\n')
    for wm in warp_markers:
        lines.append(f'{t}\t\t<WarpMarker SecTime="{wm.sample_time}" BeatTime="{wm.beat_time}" />\n')
    lines.append(f'{t}\t</SavedWarpMarkersForStretched>\n')

    lines.append(f'{t}\t<MarkersGenerated Value="false" />\n')
    lines.append(f'{t}\t<IsSongTempoMaster Value="false" />\n')
    lines.append(f'{t}</AudioClip>\n')

    return lines


def _insert_audio_clip(lines: list[str], start: int, end: int, clip_lines: list[str]) -> int:
    """Insert audio clip XML into a track's ArrangerAutomation Events. Returns line count delta."""
    events_line = _find_arranger_events_line(lines, start, end)
    if events_line is None:
        return 0

    if "<Events />" in lines[events_line]:
        lines[events_line:events_line + 1] = [
            lines[events_line].replace("<Events />", "<Events>\n"),
            *clip_lines,
            "\t\t\t\t\t\t</Events>\n",
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

    if "<Envelopes />" in lines[env_line]:
        lines[env_line:env_line + 1] = [
            lines[env_line].replace("<Envelopes />", "<Envelopes>\n"),
            *all_env_lines,
            "\t\t\t\t</Envelopes>\n",
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
        raise ValueError(
            f"Template has {len(available_tracks)} audio tracks but "
            f"{len(patches)} patches provided"
        )

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
            _set_utility_gain(lines, start, end, patch.gain_offset_db)

        clip_xml = _build_audio_clip_xml(patch)
        delta = _insert_audio_clip(lines, start, end, clip_xml)
        offset += delta
        end += delta

        if transition_automation and patch.track_index in transition_automation:
            envelope_blocks = []
            for param_key, points in transition_automation[patch.track_index]:
                target_id = None
                if param_key == "lp_filter":
                    target_id = _find_filter_target_id(lines, start, end, "lp")
                elif param_key == "hp_filter":
                    target_id = _find_filter_target_id(lines, start, end, "hp")
                else:
                    parts = param_key.split(".", 1)
                    if len(parts) == 2:
                        target_id = _find_automation_target_id(lines, start, end, parts[0], parts[1])

                if target_id:
                    env_points = [(p.time_beats, p.value) for p in points]
                    envelope_blocks.append(_build_envelope_xml(target_id, env_points))

            if envelope_blocks:
                delta2 = _insert_automation_envelopes(lines, start, end, envelope_blocks)
                offset += delta2

    _set_project_bpm(lines, project_bpm)

    return compress_als(lines, output_path)


def _set_project_bpm(lines: list[str], bpm: float) -> None:
    """Set the project tempo in the MasterTrack."""
    for i, line in enumerate(lines):
        if "<Tempo>" in line:
            for j in range(i, min(i + 5, len(lines))):
                if "Manual Value=" in lines[j]:
                    lines[j] = re.sub(r'Manual Value="[^"]*"', f'Manual Value="{bpm}"', lines[j])
                    return
