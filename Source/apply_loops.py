"""Apply loop extensions to a Sections .als by cloning AudioClip blocks.

Takes an ALS file + loop specifications, inserts new AudioClip blocks that
repeat existing source regions.  Each loop clip is a discrete copy (LoopOn=false)
placed back-to-back — matching Sam's mixing technique.

Usage:
    python Source/apply_loops.py <input.als> <loops.json> <output.als>

The loops JSON is a list of objects:
  [
    {
      "track_name": "Harry Romero - Renegades SW V1",
      "source_beat_start": 0,
      "source_beat_end": 16,
      "count": 3,
      "insert_at_beat": 1696,
      "clip_name": "intro_1"
    }
  ]

Can also be imported and called programmatically:
    from apply_loops import decompress_als, compress_als, apply_loops
"""

from __future__ import annotations

import gzip
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


MAX_LOOP_REPEATS = 8
MAX_LOOP_EXTENSION_BEATS = 128.0  # 32 bars at 4/4


# ── ALS read / write ─────────────────────────────────────────────────────────

def decompress_als(als_path: Path) -> list[str]:
    with gzip.open(als_path, "rb") as f:
        content = f.read().decode("utf-8")
    return content.splitlines(keepends=True)


def compress_als(lines: list[str], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(lines)
    with gzip.open(output_path, "wb") as f:
        f.write(content.encode("utf-8"))
    from validate_als import report_als
    errors = report_als(output_path)
    if errors:
        raise ValueError(
            f"ALS validation failed for {output_path.name}: {errors[0]}"
        )
    return output_path


# ── Track / clip finding ────────────────────────────────────────────────────

def find_track_line_ranges(lines: list[str]) -> list[tuple[int, int, str]]:
    """Return [(start_line, end_line, effective_name), ...] for AudioTracks."""
    tracks: list[tuple[int, int, str]] = []
    track_start: int | None = None
    depth = 0
    track_name = ""
    for i, line in enumerate(lines):
        if "<AudioTrack " in line:
            if track_start is None:
                track_start = i
                depth = 1
                track_name = ""
            else:
                depth += 1
        elif track_start is not None:
            if "<EffectiveName" in line and not track_name:
                m = re.search(r'Value="([^"]*)"', line)
                if m:
                    track_name = m.group(1)
            if "</AudioTrack>" in line:
                depth -= 1
                if depth == 0:
                    tracks.append((track_start, i, track_name))
                    track_start = None
    return tracks


def _normalise(s: str) -> str:
    # Normalise unicode dashes + lowercase + entity-decode so a name
    # written with 'foo &apos; bar' matches an .als-stored 'foo \' bar'.
    s = (s.replace("&apos;", "'")
         .replace("&amp;", "&")
         .replace("&quot;", '"')
         .replace("&lt;", "<")
         .replace("&gt;", ">")
         .replace("–", "-").replace("—", "-"))
    return s.lower().strip()


def _match_track(name: str, als_tracks: list[tuple[int, int, str]]) -> tuple[int, int, str] | None:
    """Find the ALS track whose name matches `name`.

    Match order (only ONE — first hit wins):
      1. Exact match on normalised names.
      2. `name` is a substring of `tname` (the requesting name is the
         shorter, more specific one — fine).
      3. `tname` is a substring of `name` (the .als name is the shorter).

    Loose prefix matching (first 20 chars) was removed because it
    collides when an artist has two tracks sharing a long prefix
    (e.g. 'Mike Richters - Your Love' and 'Mike Richters - Your Love
    (Instrumental Mix)' both matched on prefix 'mike richters - your'
    in the 22.05.26 mix and got their shifts applied to the wrong track).
    """
    nn = _normalise(name)

    # 1. Exact
    for start, end, tname in als_tracks:
        if nn == _normalise(tname):
            return start, end, tname

    # 2. name ⊂ tname
    for start, end, tname in als_tracks:
        tn = _normalise(tname)
        if nn in tn:
            return start, end, tname

    # 3. tname ⊂ name
    for start, end, tname in als_tracks:
        tn = _normalise(tname)
        if tn in nn:
            return start, end, tname

    return None


# ── ID allocation ────────────────────────────────────────────────────────────

def find_max_id(lines: list[str]) -> int:
    """Scan all lines for Id="N" patterns, return the highest N found."""
    max_id = 0
    for line in lines:
        for m in re.finditer(r'Id="(\d+)"', line):
            val = int(m.group(1))
            if val > max_id:
                max_id = val
    return max_id


_NEXT_ID = 0  # set by apply_loops() after scanning


def _alloc_id() -> int:
    global _NEXT_ID
    _NEXT_ID += 1
    return _NEXT_ID


# ── Events block finding ────────────────────────────────────────────────────

def find_clip_events(lines: list[str], track_start: int, track_end: int
                     ) -> tuple[int, int]:
    """Find the <Events> block that contains AudioClips within a track.

    Navigates: <Sample> → <ArrangerAutomation> → <Events>
    Returns (events_line, events_end_line) — the lines containing <Events>
    and </Events> (or the self-closing <Events /> line).
    """
    in_sample = False
    in_arranger = False
    events_start = -1

    for i in range(track_start, track_end + 1):
        stripped = lines[i].strip()

        if "<Sample>" in stripped:
            in_sample = True
        if in_sample and "<ArrangerAutomation>" in stripped:
            in_arranger = True

        if in_arranger:
            if "<Events>" in stripped or "<Events />" in stripped:
                events_start = i
            if events_start >= 0 and ("</Events>" in stripped or "<Events />" in stripped):
                return events_start, i

    return -1, -1


def extract_first_clip_lines(lines: list[str], events_start: int,
                             events_end: int) -> tuple[int, int] | None:
    """Find line range of the first <AudioClip>...</AudioClip> in Events."""
    clip_start = -1
    for i in range(events_start, events_end + 1):
        if "<AudioClip " in lines[i]:
            clip_start = i
            break

    if clip_start < 0:
        return None

    for i in range(clip_start, events_end + 1):
        if "</AudioClip>" in lines[i]:
            return clip_start, i

    return None


# ── Clip cloning ─────────────────────────────────────────────────────────────

@dataclass
class LoopSpec:
    track_name: str
    source_beat_start: float
    source_beat_end: float
    count: int
    insert_at_beat: float
    clip_name: str = ""
    # Optional shorter FINAL clip (a partial chunk) placed after the `count` full
    # clips, so a loop lands EXACTLY on a marker when the gap isn't a whole
    # multiple of the chunk (e.g. outro loops, where the track length is off-grid).
    tail_partial_beats: float = 0.0
    # Clips in the same track to shift LATER (positive delta) BEFORE the
    # new loop clips are inserted. Used for tail loops, where the outro
    # must be pushed back to make room for the loop region in front of it.
    # Each entry: (clip_name_to_find, delta_beats).
    shifts_before_insert: list[tuple[str, float]] = field(default_factory=list)


def validate_loop_spec(spec: LoopSpec) -> None:
    """Reject unsafe loop geometry before any ALS text is mutated."""
    numeric = {
        "source_beat_start": spec.source_beat_start,
        "source_beat_end": spec.source_beat_end,
        "insert_at_beat": spec.insert_at_beat,
        "tail_partial_beats": spec.tail_partial_beats,
    }
    for field_name, value in numeric.items():
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(
                f"LoopSpec for '{spec.track_name}' has invalid {field_name}: {value!r}"
            )

    loop_len = spec.source_beat_end - spec.source_beat_start
    if loop_len <= 0:
        raise ValueError(
            f"LoopSpec for '{spec.track_name}' has non-positive loop length {loop_len}"
        )
    if not isinstance(spec.count, int) or isinstance(spec.count, bool) or spec.count < 0:
        raise ValueError(
            f"LoopSpec for '{spec.track_name}' has invalid repeat count {spec.count!r}"
        )
    if spec.count > MAX_LOOP_REPEATS:
        raise ValueError(
            f"LoopSpec for '{spec.track_name}' exceeds repeat cap "
            f"({spec.count} > {MAX_LOOP_REPEATS})"
        )
    if spec.tail_partial_beats < 0 or spec.tail_partial_beats > loop_len:
        raise ValueError(
            f"LoopSpec for '{spec.track_name}' has invalid partial length "
            f"{spec.tail_partial_beats} for a {loop_len}-beat loop"
        )
    if spec.insert_at_beat < 0:
        raise ValueError(
            f"LoopSpec for '{spec.track_name}' would create negative arrangement Time "
            f"at beat {spec.insert_at_beat}"
        )

    extension = spec.count * loop_len + spec.tail_partial_beats
    if extension > MAX_LOOP_EXTENSION_BEATS:
        raise ValueError(
            f"LoopSpec for '{spec.track_name}' exceeds extension cap "
            f"({extension:g} > {MAX_LOOP_EXTENSION_BEATS:g} beats)"
        )
    for clip_name, delta in spec.shifts_before_insert:
        if not clip_name or not isinstance(delta, (int, float)) or not math.isfinite(delta):
            raise ValueError(
                f"LoopSpec for '{spec.track_name}' has an invalid pre-insert shift"
            )
        if delta < 0:
            raise ValueError(
                f"LoopSpec for '{spec.track_name}' has a negative pre-insert shift"
            )


def clone_clip(template_lines: list[str], spec: LoopSpec,
               arr_beat: float) -> list[str]:
    """Clone a template AudioClip with modified positions.

    Returns new lines for a single cloned clip.
    """
    src_start = spec.source_beat_start
    src_end = spec.source_beat_end
    src_len = src_end - src_start
    if src_len <= 0:
        raise ValueError(
            f"clone_clip: non-positive loop length ({src_len}) for "
            f"'{spec.track_name}' (source {src_start}->{src_end}). "
            f"A zero/negative-length clip corrupts the .als."
        )

    new_lines = list(template_lines)  # shallow copy of strings

    # Track nested elements so we only rewrite the clip-level <Name>, not
    # the integer <Name> inside <ScaleInformation> (musical-scale index).
    # Without this guard, the second <Name Value="N"/> (an int) gets
    # overwritten with the clip name string and Ableton refuses to load
    # the .als ("Unexpected value for int node").
    in_scale_info = False

    for idx, line in enumerate(new_lines):
        stripped = line.strip()

        # Open/close tracking for ScaleInformation
        if "<ScaleInformation>" in stripped or "<ScaleInformation " in stripped:
            in_scale_info = True
        if "</ScaleInformation>" in stripped:
            in_scale_info = False
            continue  # nothing to rewrite on the closing tag itself

        # AudioClip Id and Time on opening tag
        if "<AudioClip " in stripped and 'Id="' in stripped:
            new_id = _alloc_id()
            line = re.sub(r'Id="\d+"', f'Id="{new_id}"', line, count=1)
            line = re.sub(r'Time="[^"]+"', f'Time="{arr_beat}"', line, count=1)
            new_lines[idx] = line

        # CurrentStart / CurrentEnd
        elif "<CurrentStart " in stripped:
            new_lines[idx] = re.sub(
                r'Value="[^"]+"', f'Value="{arr_beat}"', line, count=1)
        elif "<CurrentEnd " in stripped:
            new_lines[idx] = re.sub(
                r'Value="[^"]+"', f'Value="{arr_beat + src_len}"', line, count=1)

        # Loop region (source range)
        elif "<LoopStart " in stripped:
            new_lines[idx] = re.sub(
                r'Value="[^"]+"', f'Value="{src_start}"', line, count=1)
        elif "<LoopEnd " in stripped:
            new_lines[idx] = re.sub(
                r'Value="[^"]+"', f'Value="{src_end}"', line, count=1)

        # Hidden loop (keep full source range for zoom-out view)
        elif "<HiddenLoopStart " in stripped:
            new_lines[idx] = re.sub(
                r'Value="[^"]+"', f'Value="{src_start}"', line, count=1)
        elif "<HiddenLoopEnd " in stripped:
            new_lines[idx] = re.sub(
                r'Value="[^"]+"', f'Value="{src_end}"', line, count=1)

        # OutMarker (must be >= LoopEnd for Ableton to be happy)
        elif "<OutMarker " in stripped:
            new_lines[idx] = re.sub(
                r'Value="[^"]+"', f'Value="{src_end}"', line, count=1)

        # StartRelative (reset to 0 — we always start at LoopStart)
        elif "<StartRelative " in stripped:
            new_lines[idx] = re.sub(
                r'Value="[^"]+"', 'Value="0"', line, count=1)

        # LoopOn (always false — discrete clips, not Ableton loops)
        elif "<LoopOn " in stripped:
            new_lines[idx] = re.sub(
                r'Value="[^"]+"', 'Value="false"', line, count=1)

        # Clip name — but ONLY the AudioClip-level <Name>, not the integer
        # <Name> inside <ScaleInformation> (a musical-scale-name index).
        elif "<Name " in stripped and spec.clip_name and not in_scale_info:
            new_lines[idx] = re.sub(
                r'Value="[^"]+"', f'Value="{spec.clip_name}"', line, count=1)

        # TakeId (unique per clip)
        elif "<TakeId " in stripped:
            new_lines[idx] = re.sub(
                r'Value="\d+"', f'Value="{_alloc_id()}"', line, count=1)

        # ScrollerTimePreserver (view range)
        elif "<LeftTime " in stripped:
            new_lines[idx] = re.sub(
                r'Value="[^"]+"', f'Value="{src_start}"', line, count=1)
        elif "<RightTime " in stripped:
            new_lines[idx] = re.sub(
                r'Value="[^"]+"', f'Value="{src_end}"', line, count=1)

        # WarpMarker Ids (replace each with a new unique Id)
        elif "WarpMarker " in stripped and 'Id="' in stripped:
            new_lines[idx] = re.sub(
                r'Id="\d+"', lambda _: f'Id="{_alloc_id()}"', line)

    return new_lines


# ── Insertion ────────────────────────────────────────────────────────────────

def find_insertion_point(lines: list[str], events_start: int,
                         events_end: int, target_beat: float) -> int:
    """Find the line where a clip with Time=target_beat should be inserted.

    Clips must be sorted by Time within <Events>.  Returns the line index
    where the new clip block should be inserted (before existing clips with
    higher Time values).
    """
    # If Events is self-closing, insert right after it (we'll expand it)
    if events_start == events_end:
        return events_start + 1

    last_clip_end = events_start + 1  # default: right after <Events>

    i = events_start
    while i <= events_end:
        line = lines[i]
        if "<AudioClip " in line:
            m = re.search(r'Time="([^"]+)"', line)
            if m:
                clip_time = float(m.group(1))
                if clip_time > target_beat:
                    return last_clip_end
            # Find end of this clip to update last_clip_end
            for j in range(i, events_end + 1):
                if "</AudioClip>" in lines[j]:
                    last_clip_end = j + 1
                    i = j
                    break
        i += 1

    return last_clip_end  # insert after all existing clips


def shift_named_clip(lines: list[str], track_start: int, track_end: int,
                     clip_name: str, delta_beats: float) -> int:
    """Shift the Time/CurrentStart/CurrentEnd of a single named clip later
    by `delta_beats`. Returns the number of fields modified (0 if clip not
    found). Used to push the outro back when we insert tail loops in front
    of it."""
    # Find the clip by <Name Value="<clip_name>" /> at the AudioClip level
    # (not nested in ScaleInformation).
    in_scale_info = False
    clip_open_idx = None
    found_idx = None
    for i in range(track_start, track_end + 1):
        line = lines[i]
        stripped = line.strip()
        if "<ScaleInformation>" in stripped or "<ScaleInformation " in stripped:
            in_scale_info = True
        if "</ScaleInformation>" in stripped:
            in_scale_info = False
            continue
        if "<AudioClip " in stripped and 'Id="' in stripped:
            clip_open_idx = i
        if (not in_scale_info and "<Name " in stripped
                and f'Value="{clip_name}"' in stripped):
            found_idx = i
            break
    if found_idx is None or clip_open_idx is None:
        return 0

    # Find the closing </AudioClip>
    clip_close_idx = None
    for j in range(found_idx, min(track_end + 1, len(lines))):
        if "</AudioClip>" in lines[j]:
            clip_close_idx = j
            break
    if clip_close_idx is None:
        return 0

    # Patch attributes within this clip
    changed = 0
    # 1) Time on the opening tag
    m = re.search(r'Time="([\d.\-]+)"', lines[clip_open_idx])
    if m:
        new_t = float(m.group(1)) + delta_beats
        lines[clip_open_idx] = re.sub(
            r'Time="[\d.\-]+"', f'Time="{new_t}"', lines[clip_open_idx], count=1)
        changed += 1
    # 2) CurrentStart, CurrentEnd inside the clip (arrangement-beats fields)
    for k in range(clip_open_idx, clip_close_idx + 1):
        line = lines[k]
        for tag in ("CurrentStart", "CurrentEnd"):
            m = re.search(rf'<{tag} Value="([\d.\-]+)"', line)
            if m:
                new_v = float(m.group(1)) + delta_beats
                lines[k] = re.sub(
                    rf'(<{tag} Value=")[\d.\-]+(")',
                    rf'\g<1>{new_v}\g<2>', line, count=1)
                line = lines[k]
                changed += 1
    return changed


def trim_named_clip_front(lines: list[str], track_start: int, track_end: int,
                          clip_name: str, trim_beats: float) -> int:
    """Trim the FRONT of a named clip by `trim_beats`: move its start later in
    BOTH the arrangement (Time/CurrentStart) and the source (LoopStart), KEEPING
    its end (CurrentEnd/LoopEnd/OutMarker). The clip then plays `trim_beats`
    further into its source and starts that much later, leaving a gap where its
    front was — the outgoing track covers it. Used for partial intro trims (e.g.
    My Own Thang: trim the front of its 32-bar intro to land on a marker) that
    can't be done by whole-clip removal. Returns fields modified (0 if not found).
    """
    if trim_beats <= 0:
        return 0
    in_scale_info = False
    clip_open_idx = None
    found_idx = None
    for i in range(track_start, track_end + 1):
        stripped = lines[i].strip()
        if "<ScaleInformation>" in stripped or "<ScaleInformation " in stripped:
            in_scale_info = True
        if "</ScaleInformation>" in stripped:
            in_scale_info = False
            continue
        if "<AudioClip " in stripped and 'Id="' in stripped:
            clip_open_idx = i
        if (not in_scale_info and "<Name " in stripped
                and f'Value="{clip_name}"' in stripped):
            found_idx = i
            break
    if found_idx is None or clip_open_idx is None:
        return 0
    clip_close_idx = None
    for j in range(found_idx, min(track_end + 1, len(lines))):
        if "</AudioClip>" in lines[j]:
            clip_close_idx = j
            break
    if clip_close_idx is None:
        return 0
    changed = 0
    m = re.search(r'Time="([\d.\-]+)"', lines[clip_open_idx])
    if m:
        lines[clip_open_idx] = re.sub(
            r'Time="[\d.\-]+"', f'Time="{float(m.group(1)) + trim_beats}"',
            lines[clip_open_idx], count=1)
        changed += 1
    # ONLY the start fields move (keep the end). CurrentStart + LoopStart shift
    # together to preserve the 1:1 source->arrangement mapping.
    for k in range(clip_open_idx, clip_close_idx + 1):
        line = lines[k]
        for tag in ("CurrentStart", "LoopStart"):
            m = re.search(rf'<{tag} Value="([\d.\-]+)"', line)
            if m:
                lines[k] = re.sub(
                    rf'(<{tag} Value=")[\d.\-]+(")',
                    rf'\g<1>{float(m.group(1)) + trim_beats}\g<2>', line, count=1)
                line = lines[k]
                changed += 1
    return changed


def _preflight_loop_specs(lines: list[str], specs: list[LoopSpec]) -> None:
    """Validate an entire loop batch before the first in-memory edit."""
    als_tracks = find_track_line_ranges(lines)
    for spec in specs:
        validate_loop_spec(spec)
        matched = _match_track(spec.track_name, als_tracks)
        if not matched:
            raise ValueError(f"Loop target track '{spec.track_name}' was not found")

        track_start, track_end, track_name = matched
        events_start, events_end = find_clip_events(lines, track_start, track_end)
        if events_start < 0:
            raise ValueError(f"Loop target track '{track_name}' has no Events block")
        if not extract_first_clip_lines(lines, events_start, events_end):
            raise ValueError(f"Loop target track '{track_name}' has no AudioClip template")

        track_text = "".join(lines[track_start:track_end + 1])
        for clip_name, _delta in spec.shifts_before_insert:
            if f'Value="{clip_name}"' not in track_text:
                raise ValueError(
                    f"Pre-insert shift target '{clip_name}' was not found in '{track_name}'"
                )


def apply_loops(lines: list[str], specs: list[LoopSpec]) -> list[str]:
    """Insert a preflighted batch of loop clips into the ALS lines."""
    global _NEXT_ID
    _preflight_loop_specs(lines, specs)
    _NEXT_ID = find_max_id(lines) + 100  # safe gap above existing IDs

    # Re-find each track's line range on the CURRENT lines for every spec.
    # Two specs can target the SAME track (a middle track gets an intro loop
    # as the incoming of one transition AND a tail loop as the outgoing of the
    # next). A single cumulative `offset` over-counted the first insertion and
    # pushed the second spec's search window past the track. Re-finding per
    # spec is simpler and always correct.
    for spec in specs:
        als_tracks = find_track_line_ranges(lines)
        matched = _match_track(spec.track_name, als_tracks)
        if not matched:
            raise ValueError(f"Loop target track '{spec.track_name}' disappeared during apply")

        track_start, track_end, tname = matched

        # Apply any pre-insertion clip shifts (e.g. push the outro back so the
        # new tail loop clips fit in front of it).
        for clip_to_shift, delta in spec.shifts_before_insert:
            n = shift_named_clip(lines, track_start, track_end, clip_to_shift, delta)
            if n > 0:
                print(f"  Shifted '{clip_to_shift}' in '{tname}' by +{delta:.0f} beats "
                      f"({n} field(s)) to make room for tail loop")
            else:
                raise ValueError(
                    f"Pre-insert shift target '{clip_to_shift}' disappeared in '{tname}'"
                )

        events_start, events_end = find_clip_events(lines, track_start, track_end)
        if events_start < 0:
            raise ValueError(f"Loop target track '{tname}' lost its Events block")

        # Handle self-closing <Events />
        is_empty = events_start == events_end and "<Events />" in lines[events_start]
        if is_empty:
            # Expand to <Events>\r\n</Events>\r\n
            indent = lines[events_start][:len(lines[events_start]) - len(lines[events_start].lstrip())]
            lines[events_start] = f"{indent}<Events>\r\n"
            lines.insert(events_start + 1, f"{indent}</Events>\r\n")
            events_end = events_start + 1
            track_end += 1

        # Find template clip
        clip_range = extract_first_clip_lines(lines, events_start, events_end)
        if not clip_range:
            raise ValueError(f"Loop target track '{tname}' lost its AudioClip template")

        clip_start, clip_end = clip_range
        template = lines[clip_start:clip_end + 1]

        # Generate all loop clips
        loop_len = spec.source_beat_end - spec.source_beat_start
        all_new_lines: list[str] = []

        for i in range(spec.count):
            arr_beat = spec.insert_at_beat + i * loop_len
            cloned = clone_clip(template, spec, arr_beat)
            all_new_lines.extend(cloned)

        # Optional partial final clip — lands the loop EXACTLY on the marker.
        if spec.tail_partial_beats and spec.tail_partial_beats > 0:
            import copy
            pspec = copy.copy(spec)
            pspec.source_beat_end = spec.source_beat_start + spec.tail_partial_beats
            arr_beat = spec.insert_at_beat + spec.count * loop_len
            all_new_lines.extend(clone_clip(template, pspec, arr_beat))

        # Insertion point — events on the current lines (post-expansion).
        _, events_end_now = find_clip_events(lines, track_start, track_end)
        insert_at = find_insertion_point(
            lines, events_start, events_end_now, spec.insert_at_beat)

        # Insert
        lines[insert_at:insert_at] = all_new_lines

        print(f"  {tname[:40]}: +{spec.count} loop clips "
              f"({loop_len:.0f}b x {spec.count} = {loop_len * spec.count:.0f}b) "
              f"at arr beat {spec.insert_at_beat:.0f}")

    return lines


# ── Clip removal (used for intro_skip_bars) ──────────────────────────────────

def _find_named_clip_span(lines: list[str], events_start: int, events_end: int,
                          targets: set[str]) -> tuple[int, int] | None:
    """Return (open_line, close_line) of the first <AudioClip> block in
    [events_start, events_end] whose clip <Name Value="..."/> is in `targets`.

    The clip name is a string (e.g. 'intro_1'); the integer <Name> inside
    <ScaleInformation> can't collide because targets hold clip-name strings,
    not 0-11 scale indices.
    """
    clip_open = None
    i = events_start
    while i <= events_end and i < len(lines):
        s = lines[i]
        if "<AudioClip " in s:
            clip_open = i
        elif clip_open is not None and 'Name Value="' in s:
            mm = re.search(r'Name Value="([^"]*)"', s)
            if mm and mm.group(1) in targets:
                j = clip_open
                while j < len(lines) and "</AudioClip>" not in lines[j]:
                    j += 1
                return clip_open, j
        if "</AudioClip>" in s:
            clip_open = None
        i += 1
    return None


def remove_named_clips(lines: list[str], track_name: str, names) -> int:
    """Delete whole <AudioClip>...</AudioClip> blocks whose clip name is in
    `names`, within the track called `track_name`. Re-finds ranges after each
    removal (line indices shift). Returns the number of clips removed.
    """
    targets = {n for n in (names or []) if n}
    if not targets:
        return 0
    removed = 0
    while True:
        m = _match_track(track_name, find_track_line_ranges(lines))
        if not m:
            break
        ts, te, _ = m
        es, ee = find_clip_events(lines, ts, te)
        if es < 0:
            break
        span = _find_named_clip_span(lines, es, ee, targets)
        if span is None:
            break
        a, b = span
        del lines[a:b + 1]
        removed += 1
    return removed


# ── Shift helpers (used by propose_arrangement.py) ───────────────────────────

def shift_track_clips(lines: list[str], track_start: int, track_end: int,
                      delta_beats: float) -> None:
    """Shift all AudioClip positions in a track by delta_beats (in-place).

    Modifies Time, CurrentStart, CurrentEnd on every AudioClip within the
    given track line range.  LoopStart/LoopEnd are NOT changed (source
    positions stay the same).
    """
    if abs(delta_beats) < 0.001:
        return

    for i in range(track_start, track_end + 1):
        line = lines[i]
        stripped = line.strip()

        # AudioClip opening tag: Time="..."
        if "<AudioClip " in stripped and 'Time="' in stripped:
            lines[i] = re.sub(
                r'(Time=")([^"]+)(")',
                lambda m: f'{m.group(1)}{float(m.group(2)) + delta_beats}{m.group(3)}',
                line, count=1)

        # CurrentStart
        elif "<CurrentStart " in stripped and 'Value="' in stripped:
            lines[i] = re.sub(
                r'(Value=")([^"]+)(")',
                lambda m: f'{m.group(1)}{float(m.group(2)) + delta_beats}{m.group(3)}',
                line, count=1)

        # CurrentEnd
        elif "<CurrentEnd " in stripped and 'Value="' in stripped:
            lines[i] = re.sub(
                r'(Value=")([^"]+)(")',
                lambda m: f'{m.group(1)}{float(m.group(2)) + delta_beats}{m.group(3)}',
                line, count=1)


def shift_clips_from_beat(lines: list[str], track_start: int, track_end: int,
                          threshold_beat: float, delta_beats: float) -> int:
    """Shift only the AudioClips whose Time >= threshold_beat by delta_beats (in-place),
    within the track range. Used by the break-skip: once the incoming's pre-drop break is
    removed, pull its drop + everything after it left, WITHOUT moving the intro in front
    of the break. Returns the number of clips shifted. LoopStart/LoopEnd (source) untouched."""
    if abs(delta_beats) < 0.001:
        return 0
    shifted, active = 0, False
    for i in range(track_start, track_end + 1):
        stripped = lines[i].strip()
        if "<AudioClip " in stripped and 'Time="' in stripped:
            m = re.search(r'Time="([^"]+)"', stripped)
            active = m is not None and float(m.group(1)) >= threshold_beat - 0.001
            if active:
                lines[i] = re.sub(r'(Time=")([^"]+)(")',
                    lambda mm: f'{mm.group(1)}{float(mm.group(2)) + delta_beats}{mm.group(3)}',
                    lines[i], count=1)
                shifted += 1
        elif active and "<CurrentStart " in stripped and 'Value="' in stripped:
            lines[i] = re.sub(r'(Value=")([^"]+)(")',
                lambda mm: f'{mm.group(1)}{float(mm.group(2)) + delta_beats}{mm.group(3)}',
                lines[i], count=1)
        elif active and "<CurrentEnd " in stripped and 'Value="' in stripped:
            lines[i] = re.sub(r'(Value=")([^"]+)(")',
                lambda mm: f'{mm.group(1)}{float(mm.group(2)) + delta_beats}{mm.group(3)}',
                lines[i], count=1)
        elif "</AudioClip>" in stripped:
            active = False
    return shifted


def split_clip_skip_before_end(lines: list[str], track_start: int, track_end: int,
                               clip_name: str, skip_beats: float,
                               keep_end_beats: float) -> bool:
    """Shorten the named clip by skip_beats while KEEPING its final keep_end_beats:
    the clip plays its body, skips skip_beats of source just before the tail, then
    plays the final keep_end_beats. Splits it into two clips. Returns True if applied.
    Used by the break-skip to trim the outgoing outro to the (pulled) incoming marker
    WITHOUT losing its ending — mimics Sam's Crusy middle-skip (2026-06-09)."""
    from types import SimpleNamespace
    es, ee = find_clip_events(lines, track_start, track_end)
    if es < 0:
        return False
    span = _find_named_clip_span(lines, es, ee, {clip_name})
    if span is None:
        return False
    a, b = span
    T = ls = le = None
    for k in range(a, b + 1):
        s = lines[k].strip()
        if "<AudioClip " in s and 'Time="' in s:
            mm = re.search(r'Time="([^"]+)"', s)
            if mm:
                T = float(mm.group(1))
        elif "<LoopStart " in s and 'Value="' in s and ls is None:
            mm = re.search(r'Value="([^"]+)"', s)
            if mm:
                ls = float(mm.group(1))
        elif "<LoopEnd " in s and 'Value="' in s and le is None:
            mm = re.search(r'Value="([^"]+)"', s)
            if mm:
                le = float(mm.group(1))
    if T is None or ls is None or le is None:
        return False
    if skip_beats + keep_end_beats >= (le - ls):      # too short to split — leave it
        return False
    a_src_end = le - keep_end_beats - skip_beats      # body plays [ls, a_src_end]
    a_arr_end = T + (a_src_end - ls)
    b_src_start = le - keep_end_beats                 # tail plays [b_src_start, le]
    template = lines[a:b + 1]                          # full original (pre-trim) as clone template
    # 1) trim the ORIGINAL clip's end to a_src_end
    for k in range(a, b + 1):
        s = lines[k].strip()
        if "<CurrentEnd " in s and 'Value="' in s:
            lines[k] = re.sub(r'Value="[^"]+"', f'Value="{a_arr_end}"', lines[k], count=1)
        elif ("<LoopEnd " in s or "<HiddenLoopEnd " in s or "<OutMarker " in s) and 'Value="' in s:
            lines[k] = re.sub(r'Value="[^"]+"', f'Value="{a_src_end}"', lines[k], count=1)
    # 2) clone the TAIL [b_src_start, le] right after the trimmed body
    spec = SimpleNamespace(source_beat_start=b_src_start, source_beat_end=le,
                           clip_name=clip_name, track_name=clip_name)
    lines[b + 1:b + 1] = clone_clip(template, spec, a_arr_end)
    return True


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 4:
        print("Usage: python apply_loops.py <input.als> <loops.json> <output.als>")
        sys.exit(1)

    in_path = Path(sys.argv[1])
    loops_path = Path(sys.argv[2])
    out_path = Path(sys.argv[3])

    print(f"Reading {in_path}")
    lines = decompress_als(in_path)
    print(f"  {len(lines)} lines")

    with open(loops_path, encoding="utf-8") as f:
        loop_dicts = json.load(f)

    specs = [LoopSpec(**d) for d in loop_dicts]
    print(f"\n{len(specs)} loop specs loaded")

    lines = apply_loops(lines, specs)

    print(f"\nWriting {out_path}")
    compress_als(lines, out_path)
    print(f"  Done — {out_path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
