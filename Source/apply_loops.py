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
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


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
    # Clips in the same track to shift LATER (positive delta) BEFORE the
    # new loop clips are inserted. Used for tail loops, where the outro
    # must be pushed back to make room for the loop region in front of it.
    # Each entry: (clip_name_to_find, delta_beats).
    shifts_before_insert: list[tuple[str, float]] = field(default_factory=list)


def clone_clip(template_lines: list[str], spec: LoopSpec,
               arr_beat: float) -> list[str]:
    """Clone a template AudioClip with modified positions.

    Returns new lines for a single cloned clip.
    """
    src_start = spec.source_beat_start
    src_end = spec.source_beat_end
    src_len = src_end - src_start

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


def apply_loops(lines: list[str], specs: list[LoopSpec]) -> list[str]:
    """Insert loop clips into the ALS lines.  Returns modified lines."""
    global _NEXT_ID
    _NEXT_ID = find_max_id(lines) + 100  # safe gap above existing IDs

    als_tracks = find_track_line_ranges(lines)
    offset = 0  # cumulative line-count shift from insertions

    # Group specs by track to handle offset correctly
    for spec in specs:
        matched = _match_track(spec.track_name, als_tracks)
        if not matched:
            print(f"  WARNING: track '{spec.track_name}' not found, skipping loops")
            continue

        track_start, track_end, tname = matched
        # Apply accumulated offset
        track_start += offset
        track_end += offset

        # Apply any pre-insertion clip shifts (e.g. push the outro back so the
        # new tail loop clips fit in front of it).
        for clip_to_shift, delta in spec.shifts_before_insert:
            n = shift_named_clip(lines, track_start, track_end, clip_to_shift, delta)
            if n > 0:
                print(f"  Shifted '{clip_to_shift}' in '{tname}' by +{delta:.0f} beats "
                      f"({n} field(s)) to make room for tail loop")
            else:
                print(f"  WARNING: could not find '{clip_to_shift}' in '{tname}' to shift")

        events_start, events_end = find_clip_events(lines, track_start, track_end)
        if events_start < 0:
            print(f"  WARNING: no Events block found in '{tname}', skipping")
            continue

        # Handle self-closing <Events />
        is_empty = events_start == events_end and "<Events />" in lines[events_start]
        if is_empty:
            # Expand to <Events>\r\n</Events>\r\n
            indent = lines[events_start][:len(lines[events_start]) - len(lines[events_start].lstrip())]
            lines[events_start] = f"{indent}<Events>\r\n"
            lines.insert(events_start + 1, f"{indent}</Events>\r\n")
            events_end = events_start + 1
            offset += 1

        # Find template clip
        clip_range = extract_first_clip_lines(lines, events_start, events_end + offset)
        if not clip_range:
            print(f"  WARNING: no AudioClip found in '{tname}', skipping")
            continue

        clip_start, clip_end = clip_range
        template = lines[clip_start:clip_end + 1]

        # Generate all loop clips
        loop_len = spec.source_beat_end - spec.source_beat_start
        all_new_lines: list[str] = []

        for i in range(spec.count):
            arr_beat = spec.insert_at_beat + i * loop_len
            cloned = clone_clip(template, spec, arr_beat)
            all_new_lines.extend(cloned)

        # Find insertion point (account for current offset within this track)
        # Re-find events end since we may have expanded it
        _, events_end_now = find_clip_events(lines, track_start, track_end + offset)
        insert_at = find_insertion_point(
            lines, events_start, events_end_now, spec.insert_at_beat)

        # Insert
        lines[insert_at:insert_at] = all_new_lines
        added = len(all_new_lines)
        offset += added

        print(f"  {tname[:40]}: +{spec.count} loop clips "
              f"({loop_len:.0f}b x {spec.count} = {loop_len * spec.count:.0f}b) "
              f"at arr beat {spec.insert_at_beat:.0f}")

    return lines


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
