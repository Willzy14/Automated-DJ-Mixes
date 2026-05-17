"""Parse Rekordbox waveform color data (PWV5 / PWV4) from .EXT analysis files.

This adds a fourth analysis signal — Rekordbox's purpose-built waveform display
data — alongside our existing librosa RMS, librosa bass band, and Rekordbox
phrase data. PWV bytes are the underlying numerical source of the colour
waveform you see in the Rekordbox UI.

ANALYSIS_MODEL_VERSION = "cue-candidates-v1"

PWV5 binary layout (high-resolution colour detail):
    Header (12 bytes after the 4-byte magic + 4-byte tag length + 4-byte content length):
      len_entry_bytes : Int32ub  (always 0x00000002 = 2 bytes per entry)
      len_entries     : Int32ub  (number of waveform points)
      unknown         : 4 bytes  (possibly scale/sample info)

    Each entry is a single 16-bit big-endian word with LSB-first packing:
      bits 0-2  : red   (3 bits, 0-7)
      bits 3-5  : green (3 bits, 0-7)
      bits 6-8  : blue  (3 bits, 0-7)
      bits 9-13 : height (5 bits, 0-31)
      bits 14-15: reserved / unused

The colour-to-frequency interpretation (blue ≈ bass, red ≈ highs, green ≈ mids)
is the convention Rekordbox uses for its UI display. Whether those map exactly
to spectral energy bands has NOT been formally validated yet — colour values
should be treated as visual display data first, frequency-correlated second.

PWV4 (512-segment colour waveform, Rekordbox 6 default) has 6-byte entries;
fallback parser provided but not the primary signal.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

ANALYSIS_MODEL_VERSION = "cue-candidates-v1"
WAVEFORM_PARSER_VERSION = "v1"


@dataclass
class WaveformEntry:
    """One entry from PWV5/PWV4. Neutral colour/height — no frequency claim."""
    color_r: int    # 0-7 (3-bit)
    color_g: int    # 0-7
    color_b: int    # 0-7
    height: int     # 0-31 (5-bit)


def _find_tag(data: bytes, magic: bytes) -> tuple[int, int, int] | None:
    """Return (header_pos, header_len, content_len) for the named tag, or None."""
    pos = data.find(magic)
    if pos == -1:
        return None
    head_len = struct.unpack(">I", data[pos + 4:pos + 8])[0]
    content_len = struct.unpack(">I", data[pos + 8:pos + 12])[0]
    return pos, head_len, content_len


def _decode_pwv5_entry(word: int) -> WaveformEntry:
    """Decode a 16-bit PWV5 word into colour + height (LSB-first layout)."""
    return WaveformEntry(
        color_r=word & 0b111,
        color_g=(word >> 3) & 0b111,
        color_b=(word >> 6) & 0b111,
        height=(word >> 9) & 0b11111,
    )


def parse_pwv5(ext_path: Path) -> list[WaveformEntry] | None:
    """Parse the PWV5 tag (high-resolution colour detail) from an .EXT file.

    Returns a list of WaveformEntry, one per source pixel.
    Returns None if PWV5 is not present.
    """
    data = ext_path.read_bytes()
    tag_info = _find_tag(data, b"PWV5")
    if tag_info is None:
        return None
    pos, head_len, _ = tag_info

    entry_size = struct.unpack(">I", data[pos + 12:pos + 16])[0]
    n_entries = struct.unpack(">I", data[pos + 16:pos + 20])[0]
    if entry_size != 2:
        return None  # unexpected layout; bail rather than misinterpret

    entries_start = pos + head_len
    entries: list[WaveformEntry] = []
    for i in range(n_entries):
        off = entries_start + i * 2
        if off + 2 > len(data):
            break
        word = struct.unpack(">H", data[off:off + 2])[0]
        entries.append(_decode_pwv5_entry(word))
    return entries


def parse_pwv4(ext_path: Path) -> list[WaveformEntry] | None:
    """Parse the PWV4 tag (512-segment colour preview, Rekordbox 6 default).

    PWV4 entries are 6 bytes each (one 1200-column preview of the full track).
    Lower resolution than PWV5 but available on older RB versions. Returns a
    list of WaveformEntry, or None if PWV4 not present.

    NOTE: PWV4 byte layout is not formally documented — this parser uses the
    convention reported by community sources: byte0=red, byte1=green,
    byte2=blue (8-bit each, scaled), byte3-5 = height + ?
    DO NOT TRUST without validation; PWV5 is the preferred signal.
    """
    data = ext_path.read_bytes()
    tag_info = _find_tag(data, b"PWV4")
    if tag_info is None:
        return None
    pos, head_len, _ = tag_info

    entry_size = struct.unpack(">I", data[pos + 12:pos + 16])[0]
    n_entries = struct.unpack(">I", data[pos + 16:pos + 20])[0]
    if entry_size != 6:
        return None

    entries_start = pos + head_len
    entries: list[WaveformEntry] = []
    for i in range(n_entries):
        off = entries_start + i * 6
        if off + 6 > len(data):
            break
        # Conservative neutral mapping: scale 8-bit RGB down to 0-7,
        # take byte 3 as height scaled to 0-31. NEEDS VALIDATION.
        r = data[off] >> 5
        g = data[off + 1] >> 5
        b = data[off + 2] >> 5
        h = data[off + 3] >> 3
        entries.append(WaveformEntry(color_r=r, color_g=g, color_b=b, height=h))
    return entries


def parse_waveform(ext_path: Path) -> tuple[list[WaveformEntry] | None, str]:
    """Try PWV5 first, fall back to PWV4. Returns (entries, source_tag_name).

    source_tag_name is one of: "PWV5", "PWV4", "none".
    """
    entries = parse_pwv5(ext_path)
    if entries:
        return entries, "PWV5"
    entries = parse_pwv4(ext_path)
    if entries:
        return entries, "PWV4"
    return None, "none"


def waveform_per_beat(
    entries: list[WaveformEntry],
    beat_times_ms: list[int],
    total_duration_sec: float,
) -> dict[str, list[float]]:
    """Aggregate per-pixel waveform data into per-beat arrays.

    The PWV entries are evenly spaced across the track duration. For each beat
    interval [beat_times_ms[i], beat_times_ms[i+1]), we average the entries
    whose timestamps fall inside that interval.

    Returns a dict with keys: "height", "r", "g", "b". Each value is a list
    of length len(beat_times_ms), one float per beat (normalized 0-1).
    """
    if not entries or not beat_times_ms or total_duration_sec <= 0:
        return {"height": [], "r": [], "g": [], "b": []}

    n_entries = len(entries)
    entry_dur_sec = total_duration_sec / n_entries

    height_per_beat: list[float] = []
    r_per_beat: list[float] = []
    g_per_beat: list[float] = []
    b_per_beat: list[float] = []

    for i, beat_ms in enumerate(beat_times_ms):
        beat_sec = beat_ms / 1000.0
        if i + 1 < len(beat_times_ms):
            next_sec = beat_times_ms[i + 1] / 1000.0
        else:
            next_sec = beat_sec + (60.0 / 128.0)  # fall back to one beat at 128bpm

        start_idx = int(beat_sec / entry_dur_sec)
        end_idx = max(start_idx + 1, int(next_sec / entry_dur_sec))
        end_idx = min(end_idx, n_entries)
        start_idx = min(start_idx, n_entries - 1)

        slice_ = entries[start_idx:end_idx]
        if not slice_:
            height_per_beat.append(0.0)
            r_per_beat.append(0.0)
            g_per_beat.append(0.0)
            b_per_beat.append(0.0)
            continue
        height_per_beat.append(sum(e.height for e in slice_) / len(slice_) / 31.0)
        r_per_beat.append(sum(e.color_r for e in slice_) / len(slice_) / 7.0)
        g_per_beat.append(sum(e.color_g for e in slice_) / len(slice_) / 7.0)
        b_per_beat.append(sum(e.color_b for e in slice_) / len(slice_) / 7.0)

    return {
        "height": height_per_beat,
        "r": r_per_beat,
        "g": g_per_beat,
        "b": b_per_beat,
    }
