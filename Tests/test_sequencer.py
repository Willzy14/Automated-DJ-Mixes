"""Tests for Camelot wheel logic and harmonic sequencing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

from automated_dj_mixes.sequencer import key_to_camelot, CAMELOT_WHEEL


def test_key_to_camelot_major():
    assert key_to_camelot("C major") == "8B"
    assert key_to_camelot("G major") == "9B"
    assert key_to_camelot("F major") == "7B"


def test_key_to_camelot_minor():
    assert key_to_camelot("A minor") == "8A"
    assert key_to_camelot("E minor") == "9A"
    assert key_to_camelot("D minor") == "7A"


def test_key_to_camelot_unknown():
    assert key_to_camelot("X weird") is None


def test_all_keys_mapped():
    assert len(CAMELOT_WHEEL) == 24
