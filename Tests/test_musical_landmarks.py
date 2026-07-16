import json
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))


def test_short_pre_drop_gap_survives_section_smoothing_as_landmark():
    from automated_dj_mixes.musical_landmarks import (
        extract_kick_dropout_landmarks,
    )

    raw = np.ones(128, dtype=bool)
    raw[92:96] = False
    section = np.ones(128, dtype=bool)
    sections = [
        {"name": "intro_1", "label": "intro", "start_bar": 0, "end_bar": 24},
        {"name": "drop_1", "label": "drop", "start_bar": 24, "end_bar": 32},
    ]

    landmarks = extract_kick_dropout_landmarks(
        raw, section, sections, bpm=120.0, downbeat=0.0,
        source="kick-detector-v3-raw",
    )

    assert len(landmarks) == 1
    landmark = landmarks[0]
    assert landmark["type"] == "pre_drop_kick_gap"
    assert landmark["start_beat"] == 92
    assert landmark["end_beat"] == 96
    assert landmark["duration_beats"] == 4
    assert landmark["section_name"] == "intro_1"
    assert landmark["section_signal_bridged"] is True
    assert "transition_end" in landmark["candidate_roles"]


def test_one_beat_syncopation_is_not_promoted_to_landmark():
    from automated_dj_mixes.musical_landmarks import (
        extract_kick_dropout_landmarks,
    )

    raw = np.ones(32, dtype=bool)
    raw[12] = False
    sections = [
        {"name": "drop_1", "label": "drop", "start_bar": 0, "end_bar": 8},
    ]

    assert extract_kick_dropout_landmarks(
        raw, raw, sections, bpm=120.0, downbeat=0.0
    ) == []


def test_refresh_adds_landmarks_without_changing_sections(tmp_path):
    from types import SimpleNamespace

    from extract_musical_landmarks import refresh_track_landmarks

    project = tmp_path
    (project / "Audio").mkdir()
    (project / "_Stem Analysis").mkdir()
    (project / "Audio" / "Track.wav").write_bytes(b"placeholder")
    sections = [
        {"name": "intro_1", "label": "intro", "start_bar": 0,
         "end_bar": 4, "start_sec": 0.0, "end_sec": 8.0},
        {"name": "drop_1", "label": "drop", "start_bar": 4,
         "end_bar": 8, "start_sec": 8.0, "end_sec": 16.0},
    ]
    payload = {
        "track": "Track", "bpm": 120.0, "n_bars": 8,
        "sections": sections, "signals": {},
    }
    stem = project / "_Stem Analysis" / "SECTIONS_STEM_Track.json"
    stem.write_text(json.dumps(payload), encoding="utf-8")

    class Provider:
        def presence_per_beat(self, *_args, **_kwargs):
            raw = np.ones(32, dtype=bool)
            raw[12:16] = False
            return SimpleNamespace(raw=raw, section=np.ones(32, dtype=bool))

    result = refresh_track_landmarks(
        project, "Track", provider=Provider(), render=False
    )

    assert result["sections"] == sections
    assert result["signals"]["musical_landmarks"][0]["start_beat"] == 12
    assert json.loads(stem.read_text(encoding="utf-8"))["sections"] == sections
