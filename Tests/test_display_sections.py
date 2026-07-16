import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))


def test_intro_boundary_moves_to_stable_kick_return_across_one_beat_blip():
    from automated_dj_mixes.display_sections import refine_intro_drop_boundary

    sections = [
        {"label": "intro", "name": "intro_1", "start_bar": 0, "end_bar": 4,
         "start_sec": 0.0, "end_sec": 8.0},
        {"label": "drop", "name": "drop_1", "start_bar": 4, "end_bar": 24,
         "start_sec": 8.0, "end_sec": 48.0},
    ]
    landmarks = [
        {"type": "kick_dropout", "start_beat": 9, "end_beat": 24},
        {"type": "kick_dropout", "start_beat": 25, "end_beat": 32},
    ]

    refined, audit = refine_intro_drop_boundary(
        sections, landmarks, bpm=120.0, downbeat=0.0
    )

    assert refined[0]["end_bar"] == 8
    assert refined[1]["start_bar"] == 8
    assert audit["new_boundary_beat"] == 32


def test_display_sections_split_every_short_dropout():
    from automated_dj_mixes.display_sections import derive_display_sections

    sections = [
        {"label": "intro", "name": "intro_1", "start_bar": 0, "end_bar": 8},
        {"label": "drop", "name": "drop_1", "start_bar": 8, "end_bar": 24},
    ]
    landmarks = [
        {"landmark_id": "early", "type": "kick_dropout", "start_beat": 9,
         "end_beat": 32, "duration_beats": 23, "section_signal_bridged": False},
        {"landmark_id": "micro", "type": "kick_dropout", "start_beat": 60,
         "end_beat": 64, "duration_beats": 4, "section_signal_bridged": True},
        {"landmark_id": "mini_break", "type": "kick_dropout", "start_beat": 72,
         "end_beat": 80, "duration_beats": 8, "section_signal_bridged": False},
    ]

    display = derive_display_sections(sections, landmarks, source_end_beat=96)

    assert [section["label"] for section in display] == [
        "intro", "drop", "beat_dropout", "drop", "beat_dropout", "drop"
    ]
    dropout = display[2]
    assert (dropout["start_beat"], dropout["end_beat"]) == (60.0, 64.0)
    assert dropout["color"] == 55
    assert (display[4]["start_beat"], display[4]["end_beat"]) == (72.0, 80.0)
