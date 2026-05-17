"""Read Rekordbox analysis data — phrase structure, beat grid, track metadata."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from pyrekordbox import Rekordbox6Database, AnlzFile


# Rekordbox "High mood" (mood=1) phrase kind → section label.
# This is the mapping used for dance/electronic music.
PHRASE_KIND_HIGH = {
    1: "intro",
    2: "up",       # build
    3: "down",     # breakdown
    5: "chorus",   # drop
    6: "outro",
}

PHRASE_KIND_MID = {
    1: "intro",
    2: "verse",
    3: "bridge",
    5: "chorus",
    6: "outro",
}

PHRASE_KIND_LOW = {
    1: "intro",
    2: "verse",
    3: "verse",
    5: "bridge",
    6: "chorus",
    7: "outro",
}

MOOD_MAPS = {1: PHRASE_KIND_HIGH, 2: PHRASE_KIND_MID, 3: PHRASE_KIND_LOW}


@dataclass
class PhraseEntry:
    """One structural phrase in a track."""
    index: int
    start_beat: int      # 1-based global beat number
    kind: int            # raw kind value from Rekordbox
    label: str           # human-readable: "intro", "up", "down", "chorus", "outro"
    fill: bool           # has a fill at the end
    fill_beat: int       # beat where fill starts (0 if no fill)


@dataclass
class RekordboxAnalysis:
    """Parsed Rekordbox analysis for a single track."""
    file_path: str
    title: str
    bpm: float
    key_name: str | None
    mood: int                           # 1=High (dance), 2=Mid, 3=Low
    end_beat: int                       # total beats in track
    phrases: list[PhraseEntry]
    beat_times_ms: list[int]            # millisecond timestamp per beat (index 0 = beat 1)
    first_downbeat_offset: int = 0      # index of first beat_of_bar=1 in beat_times_ms

    def beat_to_sec(self, beat: int) -> float:
        """Convert a 1-based beat number to seconds using the beat grid."""
        idx = beat - 1
        if idx < 0:
            return 0.0
        if idx < len(self.beat_times_ms):
            return self.beat_times_ms[idx] / 1000.0
        # Extrapolate past the grid using BPM
        last_time = self.beat_times_ms[-1] / 1000.0 if self.beat_times_ms else 0.0
        beats_past = idx - (len(self.beat_times_ms) - 1)
        return last_time + beats_past * (60.0 / self.bpm)

    def phrase_end_beat(self, phrase_idx: int) -> int:
        """End beat of a phrase (= start beat of next phrase, or end_beat for last)."""
        if phrase_idx < len(self.phrases) - 1:
            return self.phrases[phrase_idx + 1].start_beat
        return self.end_beat

    def first_phrase_of(self, label: str) -> PhraseEntry | None:
        """First phrase with the given label."""
        for p in self.phrases:
            if p.label == label:
                return p
        return None

    def first_phrase_of_after(self, label: str, after_beat: int) -> PhraseEntry | None:
        """First phrase with the given label that starts at or after after_beat."""
        for p in self.phrases:
            if p.label == label and p.start_beat >= after_beat:
                return p
        return None


def _parse_pssi_from_ext(ext_path: Path) -> tuple[int, int, list[dict]] | None:
    """Parse PSSI (phrase structure) tag directly from EXT binary data.

    pyrekordbox 0.4.x can't parse Rekordbox 7 EXT files, so we do minimal
    binary parsing: scan for the PSSI tag header, read the header fields,
    then read each 24-byte entry.
    """
    data = ext_path.read_bytes()
    pos = 0
    while pos < len(data) - 12:
        if data[pos:pos + 4] == b"PSSI":
            head_len = struct.unpack(">I", data[pos + 4:pos + 8])[0]
            hdr = data[pos + 12:pos + head_len]
            if len(hdr) < 20:
                return None

            entry_size = struct.unpack(">I", hdr[0:4])[0]
            len_entries = struct.unpack(">H", hdr[4:6])[0]
            mood = struct.unpack(">H", hdr[6:8])[0]
            # Bytes 8-13: unknown; bytes 14-15: end_beat; 16-17: unknown; 18: bank; 19: unknown
            end_beat = struct.unpack(">H", hdr[14:16])[0]

            entries = []
            entries_start = pos + head_len
            for i in range(len_entries):
                offset = entries_start + i * entry_size
                if offset + entry_size > len(data):
                    break
                idx = struct.unpack(">H", data[offset:offset + 2])[0]
                beat = struct.unpack(">H", data[offset + 2:offset + 4])[0]
                kind = struct.unpack(">H", data[offset + 4:offset + 6])[0]
                fill = data[offset + 21]
                fill_beat = struct.unpack(">H", data[offset + 22:offset + 24])[0]
                entries.append({
                    "index": idx,
                    "beat": beat,
                    "kind": kind,
                    "fill": fill,
                    "fill_beat": fill_beat,
                })
            return mood, end_beat, entries
        pos += 1
    return None


def _read_beat_grid(dat_path: Path) -> tuple[list[int], int]:
    """Read beat grid timestamps (ms) from DAT file via pyrekordbox.

    Returns (beat_times_ms, first_downbeat_offset) where
    first_downbeat_offset is the index of the first beat_of_bar=1 entry.
    Many tracks start on beat 2, 3, or 4 of a bar — the offset lets
    callers align to actual downbeats.
    """
    anlz = AnlzFile.parse_file(dat_path)
    if "PQTZ" not in anlz:
        return [], 0
    pqtz = anlz.get_tag("PQTZ")
    entries = pqtz.content.entries
    times = [int(e.time) for e in entries]
    offset = 0
    for i, e in enumerate(entries[:4]):
        bn = getattr(e, "beat_number", None) or getattr(e, "beat", None)
        if bn == 1:
            offset = i
            break
    return times, offset


def _find_anlz_root() -> Path:
    """Locate Rekordbox's ANLZ file storage directory."""
    from pyrekordbox import get_config
    cfg = get_config("rekordbox7")
    db_dir = Path(cfg["db_dir"])
    return db_dir / "share"


def read_rekordbox_library() -> dict[str, RekordboxAnalysis]:
    """Read all analyzed tracks from Rekordbox, keyed by filename (lowercased).

    Returns a dict mapping lowercase filename → RekordboxAnalysis so the
    pipeline can match input tracks by name.
    """
    db = Rekordbox6Database()
    anlz_root = _find_anlz_root()

    tracks = db.get_content().all()
    results: dict[str, RekordboxAnalysis] = {}

    for track in tracks:
        anlz_rel = track.AnalysisDataPath
        if not anlz_rel:
            continue

        dat_path = anlz_root / anlz_rel.lstrip("/")
        ext_path = dat_path.with_suffix(".EXT")

        if not dat_path.exists():
            continue

        # Beat grid from DAT (pyrekordbox handles this fine)
        beat_times, db_offset = _read_beat_grid(dat_path)

        # Phrase structure from EXT (manual binary parse for RB7 compat)
        phrases: list[PhraseEntry] = []
        mood = 1
        end_beat = len(beat_times)

        if ext_path.exists():
            pssi = _parse_pssi_from_ext(ext_path)
            if pssi is not None:
                mood, end_beat, raw_entries = pssi
                kind_map = MOOD_MAPS.get(mood, PHRASE_KIND_HIGH)
                for e in raw_entries:
                    label = kind_map.get(e["kind"], f"unknown_{e['kind']}")
                    phrases.append(PhraseEntry(
                        index=e["index"],
                        start_beat=e["beat"],
                        kind=e["kind"],
                        label=label,
                        fill=bool(e["fill"]),
                        fill_beat=e["fill_beat"],
                    ))

        bpm = (track.BPM or 0) / 100.0
        key_name = track.KeyName if hasattr(track, "KeyName") else None
        filename = (track.FileNameL or "").lower()
        file_path = track.FolderPath or ""

        ra = RekordboxAnalysis(
            file_path=file_path,
            title=track.Title or "",
            bpm=bpm,
            key_name=key_name,
            mood=mood,
            end_beat=end_beat,
            phrases=phrases,
            beat_times_ms=beat_times,
            first_downbeat_offset=db_offset,
        )
        results[filename] = ra

    return results


def find_rekordbox_match(
    track_filename: str,
    rb_library: dict[str, RekordboxAnalysis],
) -> RekordboxAnalysis | None:
    """Find a Rekordbox analysis matching a track filename.

    Tries exact match first, then substring match (handles cases where
    Rekordbox and the input folder have slightly different filenames).
    """
    key = track_filename.lower()

    # Exact match
    if key in rb_library:
        return rb_library[key]

    # Stem match (ignore extension differences: .wav vs .aiff)
    stem = Path(key).stem
    for rb_key, ra in rb_library.items():
        if Path(rb_key).stem == stem:
            return ra

    # Substring match (track name contained in RB filename or vice versa)
    for rb_key, ra in rb_library.items():
        if stem in rb_key or Path(rb_key).stem in key:
            return ra

    return None
