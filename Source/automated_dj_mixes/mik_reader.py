"""Mixed In Key 11 reader — extracts cue points, beat grid, energy, and key
from GEOB tags written to WAV/MP3 file tags by MIK 11.

MIK 11 stores data in ID3 GEOB (General Encapsulated Object) frames:
  GEOB:CuePoints  — base64-encoded JSON: {"cues": [{"time": ms, "name": ""}]}
  GEOB:BeatGrid   — base64-encoded JSON: {"source": "mixedinkey", "tempo": float, "beats": [ms...]}
  GEOB:Energy     — base64-encoded JSON: {"energyLevel": int, "source": "mixedinkey"}
  GEOB:Key        — base64-encoded JSON: {"key": "1A", "source": "mixedinkey"}

Times are in milliseconds.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import mutagen

MIK_DB_PATHS = [
    Path.home() / "AppData/Local/Mixed In Key/Mixed In Key/11.0/MIKStore.db",
    Path.home() / "Library/Application Support/Mixed In Key/11.0/MIKStore.db",
]


@dataclass
class MikCue:
    time_ms: float
    time_sec: float
    name: str = ""
    index: int = 0


@dataclass
class MikBeatGrid:
    tempo: float
    beats_ms: list[float] = field(default_factory=list)
    algorithm: int = 0


@dataclass
class MikEnergySegment:
    start_sec: float
    end_sec: float
    energy: int


@dataclass
class MikTrackData:
    path: Path
    artist: str = ""
    title: str = ""
    key: str | None = None
    key_confidence: float = 0.0
    bpm: float | None = None
    energy: int | None = None
    lufs: float | None = None
    cues: list[MikCue] = field(default_factory=list)
    beat_grid: MikBeatGrid | None = None
    energy_segments: list[MikEnergySegment] = field(default_factory=list)
    source: str = "mixedinkey-11"


def _decode_geob(tag) -> dict | None:
    if tag is None or not hasattr(tag, "data"):
        return None
    try:
        return json.loads(base64.b64decode(tag.data))
    except Exception:
        try:
            return json.loads(tag.data)
        except Exception:
            return None


def _parse_timespan(ts: str) -> float:
    """Parse .NET TimeSpan string 'HH:MM:SS.ffffff' to seconds."""
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
        return h * 3600 + m * 60 + s
    return 0.0


def read_mik_from_tags(audio_path: Path) -> MikTrackData:
    """Read MIK 11 data from GEOB tags in a WAV/MP3 file."""
    result = MikTrackData(path=audio_path)
    m = mutagen.File(audio_path)
    if m is None or not hasattr(m, "tags") or m.tags is None:
        return result

    tags = m.tags
    result.artist = str(tags.get("TPE1", ""))
    result.title = str(tags.get("TIT2", ""))

    bpm_tag = tags.get("TBPM")
    if bpm_tag:
        try:
            result.bpm = float(str(bpm_tag))
        except ValueError:
            pass

    key_tag = tags.get("TKEY")
    if key_tag:
        result.key = str(key_tag)

    cue_data = _decode_geob(tags.get("GEOB:CuePoints"))
    if cue_data and "cues" in cue_data:
        for i, cue in enumerate(cue_data["cues"]):
            time_ms = cue.get("time", 0)
            result.cues.append(MikCue(
                time_ms=time_ms,
                time_sec=time_ms / 1000.0,
                name=cue.get("name", ""),
                index=i,
            ))

    bg_data = _decode_geob(tags.get("GEOB:BeatGrid"))
    if bg_data:
        result.beat_grid = MikBeatGrid(
            tempo=bg_data.get("tempo", 0),
            beats_ms=bg_data.get("beats", []),
            algorithm=bg_data.get("algorithm", 0),
        )

    energy_data = _decode_geob(tags.get("GEOB:Energy"))
    if energy_data:
        result.energy = energy_data.get("energyLevel")

    key_data = _decode_geob(tags.get("GEOB:Key"))
    if key_data:
        result.key = key_data.get("key", result.key)

    return result


def read_mik_energy_segments(audio_path: Path, db_path: Path | None = None) -> list[MikEnergySegment]:
    """Read per-segment energy data from MIK's SQLite database."""
    if db_path is None:
        for p in MIK_DB_PATHS:
            if p.exists():
                db_path = p
                break
    if db_path is None or not db_path.exists():
        return []

    db = sqlite3.connect(str(db_path))
    filename = audio_path.name

    row = db.execute(
        "SELECT ss.Data FROM SerializedSongStructure ss "
        "JOIN Song s ON s.Id = ss.SongId "
        "WHERE s.File LIKE ?",
        (f"%{filename}%",),
    ).fetchone()
    db.close()

    if not row or not row[0]:
        return []

    data = json.loads(row[0])
    segments = []
    for seg in data.get("EnergySegments", []):
        segments.append(MikEnergySegment(
            start_sec=_parse_timespan(seg["StartTime"]),
            end_sec=_parse_timespan(seg["EndTime"]),
            energy=seg["Energy"],
        ))
    return segments


def read_mik_db_track(audio_path: Path, db_path: Path | None = None) -> MikTrackData | None:
    """Read MIK data from SQLite database (complements tag data with LUFS, confidence)."""
    if db_path is None:
        for p in MIK_DB_PATHS:
            if p.exists():
                db_path = p
                break
    if db_path is None or not db_path.exists():
        return None

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    filename = audio_path.name

    row = db.execute(
        "SELECT * FROM Song WHERE File LIKE ?",
        (f"%{filename}%",),
    ).fetchone()
    db.close()

    if not row:
        return None

    result = MikTrackData(path=audio_path)
    result.artist = row["ArtistName"] or ""
    result.title = row["SongName"] or ""
    result.bpm = row["Tempo"]
    result.key = row["MainKey"]
    result.key_confidence = row["MainKeyConfidence"] or 0.0
    result.energy = row["OverallEnergy"]
    result.lufs = row["OverallVolumeLUFS"]
    return result


def enrich_from_mik(audio_path: Path, db_path: Path | None = None) -> MikTrackData:
    """Full MIK read: tags (cues + beat grid) + DB (LUFS, confidence, energy segments).

    Tag-derived data is the priority — cue points and beat grid live in
    the audio file itself. DB enrichment (LUFS, energy segments) is
    best-effort. If the DB is locked, missing, or its schema differs,
    we still return the tag data so callers can use the cue points.
    """
    tag_data = read_mik_from_tags(audio_path)

    try:
        db_data = read_mik_db_track(audio_path, db_path)
    except Exception:
        db_data = None
    if db_data:
        tag_data.lufs = db_data.lufs
        tag_data.key_confidence = db_data.key_confidence
        if not tag_data.energy and db_data.energy:
            tag_data.energy = db_data.energy

    try:
        tag_data.energy_segments = read_mik_energy_segments(audio_path, db_path)
    except Exception:
        tag_data.energy_segments = []

    return tag_data
