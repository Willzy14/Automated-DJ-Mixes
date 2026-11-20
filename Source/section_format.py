"""Which section detector wrote a SECTIONS_STEM json, and a guard for readers.

A file written by the groove detector carries analysis.section_detector
(e.g. "groove-1") and analysis.sections_schema. Files without the field are
legacy. Readers that only understand legacy sections call require_legacy()
so a project folder never silently mixes the two formats.
"""
from __future__ import annotations

import html
import json
from math import isfinite
from pathlib import Path
from typing import NamedTuple

LEGACY = "legacy"
GROOVE = "groove"
SECTION_MODES = (LEGACY, GROOVE)
GROOVE_DETECTOR = "groove-1"
GROOVE_SCHEMA = 2


class SectionFormatError(ValueError):
    """A reader was handed a SECTIONS_STEM file in a format it cannot use."""


def section_detector_of(res) -> str:
    """'legacy' for a file without analysis.section_detector, else its value."""
    analysis = res.get("analysis") if isinstance(res, dict) else None
    if isinstance(analysis, dict) and analysis.get("section_detector"):
        return str(analysis["section_detector"])
    return LEGACY


def require_legacy(res, reader: str, where="") -> None:
    fmt = section_detector_of(res)
    if fmt != LEGACY:
        schema = res.get("analysis", {}).get("sections_schema")
        raise SectionFormatError(
            f"{reader} reads only legacy section files, but {where or 'this file'} was written "
            f"by section detector {fmt!r} (sections_schema {schema}). Re-run detection with "
            f"section_mode='legacy' for this project, or teach {reader} the new format.")


def section_mode_of(res: dict, reader: str, where="") -> str:
    detector = section_detector_of(res)
    if detector == LEGACY:
        return LEGACY
    schema = res["analysis"].get("sections_schema")
    if detector == GROOVE_DETECTOR and type(schema) is int and schema == GROOVE_SCHEMA:
        return GROOVE
    raise SectionFormatError(
        f"{reader} cannot read {where or 'this result'}: section detector {detector!r} "
        f"with sections_schema {schema!r} is unsupported.")


def require_section_mode(res: dict, mode: str, reader: str, where="") -> None:
    actual = section_mode_of(res, reader, where)
    if actual != mode:
        raise SectionFormatError(
            f"{reader}: {where or 'this result'} was written by section detector "
            f"{section_detector_of(res)!r}. Re-run Phase 1a with --section-mode {mode} "
            "or select the mode of the existing analysis.")


class GrooveFill(NamedTuple):
    start_bar: float
    end_bar: float
    start_sec: float
    end_sec: float


def groove_fills(res: dict, reader: str, where="") -> list[GrooveFill]:
    """Read groove's separate fill events on both detector clocks."""
    require_section_mode(res, GROOVE, reader, where)
    signals = res.get("signals")
    rows = signals.get("fill_sections") if isinstance(signals, dict) else None
    error = f"{reader}: {where or 'groove result'} has invalid signals.fill_sections"
    if not isinstance(rows, list):
        raise SectionFormatError(error)
    fills = []
    for row in rows:
        try:
            fill = GrooveFill(*(row[key] for key in GrooveFill._fields))
        except (KeyError, TypeError):
            raise SectionFormatError(error) from None
        if (not all(type(v) in (int, float) and isfinite(v) for v in fill)
                or not 0 <= fill.start_bar < fill.end_bar
                or not fill.start_sec <= fill.end_sec):
            raise SectionFormatError(error)
        fills.append(fill)
    return fills


def require_uniform_modes(modes, reader: str) -> None:
    if len(set(modes)) > 1:
        raise SectionFormatError(
            f"{reader} received mixed legacy and groove analyses. "
            "Re-run Phase 1a with one --section-mode for all tracks.")


def track_section_mode(stem_dir: Path, track_name: str, reader: str, *,
                       required: bool = False) -> str:
    """Check a layout track's sibling analysis for consumers of derived clips."""
    name = html.unescape(track_name)
    path = stem_dir / f"SECTIONS_STEM_{name.removesuffix('.wav')}.json"
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        if required:
            raise SectionFormatError(f"{reader}: cannot read section analysis {path}: {exc}") from exc
        return LEGACY
    if required and not isinstance(result, dict):
        raise SectionFormatError(f"{reader}: invalid section analysis in {path}")
    return section_mode_of(result, reader, path.name)
