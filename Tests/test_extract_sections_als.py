"""Pins for extract_sections_als.main()'s output file set (2026-09-02):
"Sections V1" extracts must dual-write BOTH V1_baseline.json AND
Sections_V1.json with identical content - validate_hints_vs_sections'
hard gate looks for Sections_V<N>.json, but the extractor originally
wrote only V1_baseline.json, so every fresh project needed a manual
copy step before the pipeline could run. Every other stem (V2, V3, ...)
writes only its own canonical Sections_V<N>.json - no dual-write.

parse_sections_als itself is monkeypatched out: this file is about the
main()/file-naming contract, not the ALS XML parse (covered elsewhere).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Source"))

import extract_sections_als  # noqa: E402


FAKE_SECTIONS = {
    "Drums": [{
        "name": "drop_1", "label": "drop", "label_n": 1,
        "arr_time": 0.0, "arr_end": 64.0,
        "arr_bars": 0.0, "arr_end_bars": 16.0,
        "source_start_beats": 0.0, "source_end_beats": 64.0,
        "color": 0,
    }],
}


def _run_main(monkeypatch, tmp_path, stem):
    project = tmp_path / "Some Project"
    output_dir = project / "Output"
    output_dir.mkdir(parents=True)
    als_path = output_dir / f"{stem}.als"
    als_path.write_bytes(b"")  # main() only checks .exists() before parsing

    monkeypatch.setattr(extract_sections_als, "parse_sections_als", lambda p: FAKE_SECTIONS)
    monkeypatch.setattr(sys, "argv", ["extract_sections_als.py", str(als_path)])
    extract_sections_als.main()
    return project / "Sections Review"


def test_sections_v1_dual_writes_baseline_and_canonical_name(monkeypatch, tmp_path):
    review_dir = _run_main(monkeypatch, tmp_path, "Sections V1")

    baseline = review_dir / "V1_baseline.json"
    canonical = review_dir / "Sections_V1.json"
    assert baseline.exists()
    assert canonical.exists()
    assert json.loads(baseline.read_text()) == FAKE_SECTIONS
    assert json.loads(canonical.read_text()) == FAKE_SECTIONS


def test_sections_v2_writes_only_its_own_canonical_name(monkeypatch, tmp_path):
    review_dir = _run_main(monkeypatch, tmp_path, "Sections V2")

    assert (review_dir / "Sections_V2.json").exists()
    # No V1-style dual-write artifact, and no stray V2_baseline.json either.
    assert not (review_dir / "V1_baseline.json").exists()
    assert not (review_dir / "V2_baseline.json").exists()


def test_v10_does_not_false_match_the_v1_exact_stem_check(monkeypatch, tmp_path):
    # "V10".startswith("V1") - the fix guards against this with an exact
    # stem comparison ("Sections V1"), not a prefix check.
    review_dir = _run_main(monkeypatch, tmp_path, "Sections V10")

    assert (review_dir / "Sections_V10.json").exists()
    assert not (review_dir / "V1_baseline.json").exists()
    assert not (review_dir / "Sections_V1.json").exists()
