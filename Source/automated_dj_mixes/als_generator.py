"""Template-based ALS XML patching.

Decompresses a known-good Ableton Live 12 template, patches in audio clips
with warp markers, automation envelopes, and gain offsets, then recompresses.

CRITICAL: Must use raw line-level text ops for patching. Python's XmlWriter /
ElementTree reformats the document and Ableton rejects it as corrupt.
"""

from __future__ import annotations

import gzip
import math
import os
import re
from pathlib import Path
from dataclasses import dataclass

from automated_dj_mixes.analysis import TrackAnalysis
from automated_dj_mixes.warping import WarpMarker
from automated_dj_mixes.automation import AutomationPoint
from automated_dj_mixes.phrase_viz import PhraseSegment


@dataclass
class LoopSpec:
    """Loop spec for the chop-and-duplicate clip emitter. Inlined when the old
    transition.py was retired; this path only fired in the removed full-mix
    mode, but the als_generator unit test still exercises it."""
    chop_at_beats: float
    num_extra_copies: int
    loop_source_start: float
    loop_source_end: float


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
    loop_spec: LoopSpec | None = None
    phrase_segments: list[PhraseSegment] | None = None  # visualization mode


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
    from validate_als import report_als
    errors = report_als(output_path)
    if errors:
        raise ValueError(
            f"ALS validation failed for {output_path.name}: {errors[0]}"
        )
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
    total = 0
    while True:
        removed_this_pass = _remove_first_envelope_for_target(lines, pointee_match)
        if not removed_this_pass:
            return total
        total += removed_this_pass


def _remove_first_envelope_for_target(lines: list[str], pointee_match: str) -> int:
    """Remove ONE envelope. `_remove_existing_envelope_for_target` loops on this.

    It used to return after the first match, so a second envelope for the same
    target survived and Live obeyed the leftover - the failure that once made a
    mix play at 123 BPM while the static value read 120.49.
    """
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


_CLIP_BOUNDARY_ABS_TOL = 1e-6


def _audio_clip_line_ranges(
    lines: list[str], start: int, end: int,
) -> list[tuple[int, int]]:
    """Return complete AudioClip line ranges inside one track."""
    ranges: list[tuple[int, int]] = []
    clip_start: int | None = None
    for index in range(start, min(end + 1, len(lines))):
        if clip_start is None and "<AudioClip " in lines[index]:
            clip_start = index
        if clip_start is not None and "</AudioClip>" in lines[index]:
            ranges.append((clip_start, index))
            clip_start = None
    return ranges


def _xml_number(line: str, tag: str, attribute: str = "Value") -> float | None:
    match = re.search(
        rf'<{re.escape(tag)}\b[^>]*\b{re.escape(attribute)}="([^"]+)"',
        line,
    )
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _clip_number(
    lines: list[str], start: int, end: int, tag: str, attribute: str = "Value",
) -> float | None:
    for index in range(start, end + 1):
        value = _xml_number(lines[index], tag, attribute)
        if value is not None:
            return value
    return None


def _replace_clip_value(
    clip_lines: list[str], tag: str, value: float,
) -> None:
    pattern = rf'(<{re.escape(tag)}\b[^>]*\bValue=")[^"]+("[^>]*>)'
    replacement = rf'\g<1>{float(value)}\g<2>'
    for index, line in enumerate(clip_lines):
        if re.search(pattern, line):
            clip_lines[index] = re.sub(pattern, replacement, line, count=1)
            return


def _seed_id_allocator_from_document(lines: list[str]) -> None:
    """Advance the shared allocator beyond every ID already in the ALS."""
    global _NEXT_ID
    highest = _NEXT_ID
    for line in lines:
        values = [
            *(int(value) for value in re.findall(r'\bId="(\d+)"', line)),
            *(int(value) for value in re.findall(
                r'<TakeId\b[^>]*\bValue="(\d+)"', line
            )),
        ]
        if values:
            highest = max(highest, *values)
    _NEXT_ID = highest


def _reallocate_cloned_clip_ids(clip_lines: list[str]) -> None:
    """Give every ID-bearing entity in a cloned clip a fresh document ID."""
    for index, line in enumerate(clip_lines):
        line = re.sub(
            r'\bId="\d+"',
            lambda _match: f'Id="{_alloc_id()}"',
            line,
        )
        if "<TakeId " in line:
            line = re.sub(
                r'(\bValue=")\d+("[^>]*>)',
                lambda match: f'{match.group(1)}{_alloc_id()}{match.group(2)}',
                line,
                count=1,
            )
        clip_lines[index] = line


def split_audio_clip_at_beat(
    lines: list[str], track_start: int, track_end: int, beat: float,
) -> bool:
    """Split the clip spanning *beat* without changing its playback.

    The ALS is patched as lines, never serialized through an XML writer. The
    outgoing half keeps the original clip identity; the incoming half is a
    byte-for-byte clone apart from fresh IDs and the arrangement/source window
    fields that must begin at the split. Both halves retain the complete warp
    grid, fades, gain, colour, name, mute state, and all other clip metadata.

    A beat already represented by any clip edge uses the same tolerance as
    validate_mix_plan_als._matches_clip_boundary and is a strict no-op.
    """
    beat = float(beat)
    ranges = _audio_clip_line_ranges(lines, track_start, track_end)
    geometry: list[tuple[int, int, float, float]] = []
    for clip_start, clip_end in ranges:
        clip_time = _xml_number(lines[clip_start], "AudioClip", "Time")
        current_end = _clip_number(
            lines, clip_start, clip_end, "CurrentEnd"
        )
        if clip_time is None or current_end is None:
            continue
        geometry.append((clip_start, clip_end, clip_time, current_end))

    boundaries = [
        value
        for _start, _end, clip_time, current_end in geometry
        for value in (clip_time, current_end)
    ]
    if any(math.isclose(beat, boundary, abs_tol=_CLIP_BOUNDARY_ABS_TOL)
           for boundary in boundaries):
        return False

    spanning = next(
        (item for item in geometry if item[2] < beat < item[3]),
        None,
    )
    if spanning is None:
        return False

    clip_start, clip_end, clip_time, current_end = spanning
    current_start = _clip_number(
        lines, clip_start, clip_end, "CurrentStart"
    )
    loop_start = _clip_number(lines, clip_start, clip_end, "LoopStart")
    loop_end = _clip_number(lines, clip_start, clip_end, "LoopEnd")
    if current_start is None or loop_start is None or loop_end is None:
        raise ValueError(
            f"AudioClip spanning beat {beat:g} has incomplete playback windows"
        )
    arrangement_span = current_end - current_start
    source_span = loop_end - loop_start
    if arrangement_span <= 0 or source_span <= 0:
        raise ValueError(
            f"AudioClip spanning beat {beat:g} has a non-positive playback span"
        )
    fraction = (beat - current_start) / arrangement_span
    if not 0.0 < fraction < 1.0:
        return False
    source_split = loop_start + fraction * source_span

    original = list(lines[clip_start:clip_end + 1])
    outgoing = list(original)
    incoming = list(original)

    _replace_clip_value(outgoing, "CurrentEnd", beat)
    for tag in ("LoopEnd", "OutMarker", "HiddenLoopEnd"):
        _replace_clip_value(outgoing, tag, source_split)

    incoming[0] = re.sub(
        r'(\bTime=")[^"]+("[^>]*>)',
        rf'\g<1>{beat}\g<2>',
        incoming[0],
        count=1,
    )
    _replace_clip_value(incoming, "CurrentStart", beat)
    for tag in ("LoopStart", "HiddenLoopStart"):
        _replace_clip_value(incoming, tag, source_split)

    # Some Live schema variants carry an explicit sample window in addition
    # to the beat-domain Loop window. Tile those fields by the same fraction;
    # WarpMarker SecTime/BeatTime pairs remain complete and unchanged on both
    # halves so the certified sample-to-beat mapping itself is preserved.
    for sample_start_tag, sample_end_tag in (
        ("SampleStart", "SampleEnd"),
        ("SampleStartTime", "SampleEndTime"),
        ("SampleStartFrame", "SampleEndFrame"),
    ):
        sample_start = _clip_number(
            lines, clip_start, clip_end, sample_start_tag
        )
        sample_end = _clip_number(lines, clip_start, clip_end, sample_end_tag)
        if sample_start is None or sample_end is None:
            continue
        sample_split = sample_start + fraction * (sample_end - sample_start)
        _replace_clip_value(outgoing, sample_end_tag, sample_split)
        _replace_clip_value(incoming, sample_start_tag, sample_split)

    _seed_id_allocator_from_document(lines)
    _reallocate_cloned_clip_ids(incoming)
    lines[clip_start:clip_end + 1] = [*outgoing, *incoming]
    return True


def split_audio_clips_at_beats(
    lines: list[str], track_start: int, track_end: int, beats: list[float],
) -> int:
    """Split one track at each requested beat, returning the split count."""
    split_count = 0
    for beat in sorted({float(value) for value in beats}, reverse=True):
        before = len(lines)
        if split_audio_clip_at_beat(lines, track_start, track_end, beat):
            split_count += 1
            track_end += len(lines) - before
    return split_count


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
    """Build AudioClip XML lines for a track.

    Emits the original clip in full (no clip-level looping). If a loop_spec
    is present, additionally emits `num_extra_copies` duplicate clips on the
    timeline after the natural end — each playing the same source range. This
    is the "chop and duplicate" pattern, NOT Ableton's clip loop feature.
    """
    warp_markers = patch.warp_markers
    arr_start = patch.arrangement_start_beats
    duration_beats = (
        warp_markers[-1].beat_time if warp_markers else (patch.analysis.duration_sec or 0.0)
    )

    # Phrase visualization mode: emit one colored clip per phrase segment.
    # Normalize so the FIRST segment lands at arr_start (handles tracks where
    # the intro starts at a negative source beat due to first_downbeat_offset).
    if patch.phrase_segments:
        lines = []
        first_source = patch.phrase_segments[0].source_start_beats
        for seg in patch.phrase_segments:
            clip_time = arr_start + (seg.source_start_beats - first_source)
            lines.extend(_build_single_clip_xml(
                patch, output_path, indent,
                clip_time=clip_time,
                source_start=seg.source_start_beats,
                source_end=seg.source_end_beats,
                color=seg.color,
                name_override=seg.name,
            ))
        return lines

    # If a loop_spec is present, chop the original clip at chop_at_beats
    # (skipping any tail). Then emit num_extra_copies duplicate clips of the
    # loop region after the chop.
    ls = patch.loop_spec
    original_end = ls.chop_at_beats if ls else duration_beats

    lines = _build_single_clip_xml(
        patch, output_path, indent,
        clip_time=arr_start,
        source_start=0.0,
        source_end=original_end,
    )

    if ls and ls.num_extra_copies > 0:
        loop_len = ls.loop_source_end - ls.loop_source_start
        for i in range(ls.num_extra_copies):
            extra_time = arr_start + original_end + i * loop_len
            lines.extend(_build_single_clip_xml(
                patch, output_path, indent,
                clip_time=extra_time,
                source_start=ls.loop_source_start,
                source_end=ls.loop_source_end,
            ))

    return lines


def _build_single_clip_xml(
    patch: TrackPatch,
    output_path: Path,
    indent: str,
    clip_time: float,
    source_start: float,
    source_end: float,
    color: int = 37,
    name_override: str | None = None,
) -> list[str]:
    """Build XML for one AudioClip element.

    clip_time: arrangement beat where the clip starts
    source_start, source_end: source-beat range to play (post-warp)
    color: Ableton color index (37 = default)
    name_override: clip name to show in arrangement (default = file stem)
    """
    clip_id = _alloc_id()
    take_id = _alloc_id()
    a = patch.analysis
    name = _xml_escape(name_override) if name_override else _xml_escape(a.path.stem)
    warp_markers = patch.warp_markers

    duration_sec = a.duration_sec or 0.0
    full_duration_beats = warp_markers[-1].beat_time if warp_markers else duration_sec
    sample_count = int(duration_sec * (a.sample_rate or 44100))
    sr = a.sample_rate or 44100

    clip_duration = source_end - source_start
    clip_end = clip_time + clip_duration

    abs_path = _xml_escape(str(a.path.resolve()).replace("\\", "/"))
    try:
        rel_path = _xml_escape(os.path.relpath(a.path.resolve(), output_path.resolve().parent).replace("\\", "/"))
    except ValueError:
        rel_path = abs_path
    file_size = a.path.stat().st_size if a.path.exists() else 0

    t = indent
    xml = f"""{t}<AudioClip Id="{clip_id}" Time="{clip_time}">
{t}\t<LomId Value="0" />
{t}\t<LomIdView Value="0" />
{t}\t<CurrentStart Value="{clip_time}" />
{t}\t<CurrentEnd Value="{clip_end}" />
{t}\t<Loop>
{t}\t\t<LoopStart Value="{source_start}" />
{t}\t\t<LoopEnd Value="{source_end}" />
{t}\t\t<StartRelative Value="0" />
{t}\t\t<LoopOn Value="false" />
{t}\t\t<OutMarker Value="{source_end}" />
{t}\t\t<HiddenLoopStart Value="{source_start}" />
{t}\t\t<HiddenLoopEnd Value="{source_end}" />
{t}\t</Loop>
{t}\t<Name Value="{name}" />
{t}\t<Annotation Value="" />
{t}\t<Color Value="{color}" />
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
{t}\t\t<RightTime Value="{full_duration_beats}" />
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

    # Codex B6: NEVER truncate. A job that needs more tracks than the template
    # offers used to silently drop the tail of the mix with only a warning —
    # and the shipped ALS validated clean. Smaller-than-template jobs are fine
    # (unused template tracks simply stay empty); only genuine overflow raises.
    if len(patches) > len(available_tracks):
        raise ValueError(
            f"Template '{template_path.name}' has only {len(available_tracks)} "
            f"usable audio tracks (excluding Session Time) but the job needs "
            f"{len(patches)} — refusing to truncate: track(s) "
            f"{len(available_tracks) + 1}-{len(patches)} of the mix would be "
            f"silently dropped. Use a template with at least {len(patches)} "
            f"audio tracks."
        )
    out_of_range = sorted({p.track_index for p in patches
                           if p.track_index >= len(available_tracks)})
    if out_of_range:
        raise ValueError(
            f"Template '{template_path.name}' has only {len(available_tracks)} "
            f"usable audio tracks but patch track_index(es) {out_of_range} "
            f"point past the last template track — those tracks would be "
            f"silently dropped from the mix. Use a bigger template or fix "
            f"the patch indices."
        )

    offset = 0
    for patch in patches:
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

    result = compress_als(lines, output_path)

    # Codex B6: job-aware expectation gate. compress_als already ran the
    # generic corruption gate (layers 1-4); this generator KNOWS the job, so
    # it also asserts the written ALS carries exactly len(patches) clip-bearing
    # tracks, each with the mixer devices the automation stage later targets
    # (Utility gain + Channel EQ low shelf). Catches a clip insertion that
    # silently failed and a stripped/wrong template — before the file ships.
    from validate_als import MIXER_AUTOMATION_DEVICES, validate_als
    expectation_errors = validate_als(
        output_path,
        expected_track_count=len(patches),
        expected_track_devices=MIXER_AUTOMATION_DEVICES,
    )
    if expectation_errors:
        raise ValueError(
            f"ALS expectation gate failed for {output_path.name}: "
            f"{expectation_errors[0]}"
        )
    return result


def _set_project_bpm(lines: list[str], bpm: float) -> None:
    """Set a fixed MainTrack tempo and remove any inherited tempo envelope."""
    _remove_existing_envelope_for_target(lines, "8")
    for i, line in enumerate(lines):
        if "<Tempo>" in line:
            for j in range(i, min(i + 5, len(lines))):
                if "Manual Value=" in lines[j]:
                    lines[j] = re.sub(r'Manual Value="[^"]*"', f'Manual Value="{bpm}"', lines[j])
                    return
