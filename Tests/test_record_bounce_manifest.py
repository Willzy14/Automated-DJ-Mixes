"""Tests for Source/record_bounce_manifest.py: the strong bounce-to-side
binding (Codex review, 2026-09-14, burn list A4 excerpt-extraction half,
round 2 MAJOR-1) that extract_transition_excerpts.py verifies before trusting
a manually bounced --side WAV.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import record_bounce_manifest as rbm  # noqa: E402


def _make_side(ab_root: Path, label: str, als_bytes: bytes, report_bytes: bytes) -> Path:
    side_dir = ab_root / label
    side_dir.mkdir(parents=True, exist_ok=True)
    (side_dir / f"Mix {label}.als").write_bytes(als_bytes)
    (side_dir / f"Arranged {label}_ARRANGEMENT_REPORT.json").write_bytes(report_bytes)
    return side_dir


def test_sha256_file_matches_hashlib_directly(tmp_path):
    import hashlib

    p = tmp_path / "f.bin"
    p.write_bytes(b"some content, more than zero bytes")
    assert rbm.sha256_file(p) == hashlib.sha256(p.read_bytes()).hexdigest()


def test_sha256_file_streams_correctly_across_chunk_boundaries(tmp_path):
    import hashlib

    p = tmp_path / "big.bin"
    # 3 full chunks + a partial one, so the streaming loop's boundary logic
    # is actually exercised, not just a single small read.
    data = (b"x" * rbm._HASH_CHUNK_BYTES * 3) + b"tail-bytes"
    p.write_bytes(data)
    assert rbm.sha256_file(p) == hashlib.sha256(data).hexdigest()


def test_record_writes_a_manifest_with_correct_hashes(tmp_path):
    ab_root = tmp_path / "AB"
    side_dir = _make_side(ab_root, "A", b"als-bytes-here", b'{"tracks": []}')
    wav = tmp_path / "Mix A.wav"
    wav.write_bytes(b"fake-wav-bytes")

    manifest_path = rbm.record(ab_root, "A", wav)
    assert manifest_path == side_dir / "Mix A.bounce_manifest.json"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["side"] == "A"
    assert manifest["als_sha256"] == rbm.sha256_file(side_dir / "Mix A.als")
    assert manifest["report_sha256"] == rbm.sha256_file(
        side_dir / "Arranged A_ARRANGEMENT_REPORT.json")
    assert manifest["wav_sha256"] == rbm.sha256_file(wav)
    assert manifest["wav_filename"] == "Mix A.wav"
    assert "recorded_at" in manifest


def test_record_refuses_when_als_is_missing(tmp_path):
    ab_root = tmp_path / "AB"
    (ab_root / "A").mkdir(parents=True)
    (ab_root / "A" / f"Arranged A_ARRANGEMENT_REPORT.json").write_text("{}", encoding="utf-8")
    wav = tmp_path / "Mix A.wav"
    wav.write_bytes(b"x")
    try:
        rbm.record(ab_root, "A", wav)
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "missing final ALS" in str(e)


def test_record_refuses_when_wav_is_missing(tmp_path):
    ab_root = tmp_path / "AB"
    _make_side(ab_root, "A", b"als", b"{}")
    try:
        rbm.record(ab_root, "A", tmp_path / "does-not-exist.wav")
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "missing render" in str(e)
