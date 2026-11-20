"""Which section detector wrote a SECTIONS_STEM json, and a guard for readers.

A file written by the groove detector carries analysis.section_detector
(e.g. "groove-1") and analysis.sections_schema. Files without the field are
legacy. Readers that only understand legacy sections call require_legacy()
so a project folder never silently mixes the two formats.
"""
from __future__ import annotations

LEGACY = "legacy"


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
