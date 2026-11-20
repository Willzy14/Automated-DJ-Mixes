"""Unit tests for the groove-structure section rules in Source/stem_detector.py
(answer key 2026-11-20): bar one, fills as first-class output, lines where the
full kick or the bass changes, short breaks, pickups, outro start."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

import stem_detector as sd  # noqa: E402


def beats(bars_full):
    """Per-beat full-kick array from a per-bar 0/1 list (4 beats per bar)."""
    return np.repeat(np.asarray(bars_full, bool), 4)


def run(full_bars, bass_bars, heard=None, bass_raw=None):
    fb = beats(full_bars)
    return sd._groove_structure(fb, np.asarray(bass_bars, bool), len(full_bars),
                                heard_kick=None if heard is None else beats(heard),
                                bass_raw_on=None if bass_raw is None else np.asarray(bass_raw, bool))


# --- bar one -------------------------------------------------------------------

def _env(beat_vals, hop_t=0.05, bpm=120.0):
    beat_sec = 60.0 / bpm
    per = int(round(beat_sec / hop_t))
    return np.repeat(np.asarray(beat_vals, float), per), hop_t


def test_two_silent_beats_then_kick_moves_bar_one():
    mix, hop = _env([0.0, 0.02] + [1.0] * 62)
    drums, _ = _env([0.0, 0.0] + [1.0] * 62)
    assert sd._skip_leading_silence(mix, hop, 120.0, 0.0, drums) == 1.0   # 2 beats at 120 BPM


def test_no_silence_keeps_bar_one():
    mix, hop = _env([1.0] * 64)
    assert sd._skip_leading_silence(mix, hop, 120.0, 0.0, mix) == 0.0


def test_quiet_intro_is_not_silence():
    # a quiet hats intro at 0.3 of the body level is music, not silence
    mix, hop = _env([0.3] * 16 + [1.0] * 48)
    assert sd._skip_leading_silence(mix, hop, 120.0, 0.0, mix) == 0.0


def test_silence_followed_by_a_pad_not_a_kick_keeps_the_grid():
    mix, hop = _env([0.0] * 2 + [1.0] * 62)
    drums, _ = _env([0.0] * 8 + [1.0] * 56)
    assert sd._skip_leading_silence(mix, hop, 120.0, 0.0, drums) == 0.0


# --- full kick -----------------------------------------------------------------

def test_light_kick_is_heard_but_not_full():
    on = np.array([True, True, True, False])
    peaks = np.array([1.0, 0.5, 0.8, 1.0])
    assert sd._full_kick_beats(on, peaks, 1.0).tolist() == [True, False, True, False]


# --- fills never cut, breaks do --------------------------------------------------

def test_one_and_two_bar_dropouts_are_fills_without_lines():
    full = [1] * 16 + [0] + [1] * 15 + [0, 0] + [1] * 14
    bass = [1] * 48
    g = run(full, bass)
    assert g["lines"] == []
    assert g["fills"] == [(16, 17), (32, 34)]


def test_kick_out_with_bass_holding_up_to_six_bars_is_a_fill():
    full = [1] * 16 + [0] * 5 + [1] * 19
    g = run(full, [1] * 40)
    assert g["lines"] == [] and g["fills"] == [(16, 21)]


def test_four_bar_break_with_bass_gone_is_a_section():
    full = [1] * 16 + [0] * 4 + [1] * 20
    bass = [1] * 16 + [0] * 4 + [1] * 20
    g = run(full, bass)
    assert g["lines"] == [16, 20] and g["breaks"] == [(16, 20)]


def test_long_kick_out_is_a_break_even_with_bass():
    full = [1] * 16 + [0] * 8 + [1] * 16
    g = run(full, [1] * 40)
    assert g["lines"] == [16, 24]


def test_drop_two_bars_before_phrase_point_is_the_fill_into_the_break():
    # kick goes at 30, break proper starts on the phrase point 32 (Eli Brown)
    full = [1] * 30 + [0] * 18 + [1] * 16
    g = run(full, [1] * 30 + [0] * 18 + [1] * 16)
    assert g["lines"] == [32, 48]
    assert (30, 32) in g["fills"] and (30, 32) in g["lead_fills"]


def test_kick_return_is_not_snapped_to_the_grid():
    # break 56-74: the drop returns two bars late (Ashibah pickup)
    full = [1] * 56 + [0] * 18 + [1] * 26
    g = run(full, [1] * 56 + [0] * 18 + [1] * 26)
    assert g["lines"] == [56, 74]


def test_kick_down_head_is_intro_and_line_is_where_full_kick_arrives():
    full = [0] * 16 + [1] * 32
    g = run(full, [0] * 8 + [1] * 40)
    assert g["head_end"] == 16 and g["lines"] == [16]


def test_kick_stopping_four_bars_before_the_end_is_a_line():
    full = [1] * 40 + [0] * 8
    g = run(full, [1] * 48)
    assert g["tail_start"] == 40 and g["lines"] == [40]


# --- bass changes under a full kick ------------------------------------------------

def test_lasting_bass_entry_under_full_kick_is_a_line():
    g = run([1] * 48, [0] * 16 + [1] * 32)
    assert g["lines"] == [16]


def test_bass_change_on_a_fill_goes_to_the_fill_end():
    full = [1] * 23 + [0] + [1] * 24
    bass = [0] * 23 + [0] + [1] * 24
    g = run(full, bass)
    assert g["lines"] == [24]


def test_thinned_bass_that_comes_back_is_not_a_line():
    g = run([1] * 48, [1] * 16 + [0] * 2 + [1] * 30)
    assert g["lines"] == []


def test_two_bar_full_stop_fill_resets_the_phrase():
    full = [1] * 54 + [0, 0] + [1] * 24
    bass_raw = [1] * 54 + [0, 0] + [1] * 24
    heard = [1] * 54 + [0, 0] + [1] * 24
    g = run(full, [1] * 80, heard=heard, bass_raw=bass_raw)
    assert g["lines"] == [56]


def test_light_kick_two_bar_fill_is_ornament():
    full = [1] * 54 + [0, 0] + [1] * 24
    heard = [1] * 80                      # the kick is still heard, only lighter
    g = run(full, [1] * 80, heard=heard, bass_raw=[1] * 54 + [0, 0] + [1] * 24)
    assert g["lines"] == []


# --- bass state, outro ------------------------------------------------------------

def test_bass_in_the_kick_falls_back_to_low_band():
    full = np.array([0] * 8 + [1] * 32 + [0] * 4 + [1] * 20, bool)
    bass_stem = np.where(full, 0.01, 0.0)
    bass_stem[40:44] = 1.0                # a pad in the break is the only 'bass' Demucs finds
    low = np.where(full, 1.0, 0.2)
    on, src = sd._bass_state_per_bar(bass_stem, low, full)
    assert src == "low_band"
    assert on[10:38].all() and not on[41:43].any()


def test_bass_stem_is_used_when_it_carries_the_groove():
    full = np.ones(32, bool)
    bass = np.array([0.0] * 8 + [1.0] * 24)
    on, src = sd._bass_state_per_bar(bass, np.ones(32), full)
    assert src == "bass_stem" and not on[:8].any() and on[9:].all()


def test_outro_where_bass_leaves_moves_to_fill_end():
    bass = np.array([1] * 158 + [0] * 18, bool)
    assert sd._groove_outro_start(bass, [(158, 160)], 176, [True]) == 160


def test_outro_without_bass_leaving_uses_last_full_low_fill():
    bass = np.ones(193, bool)
    bass[192] = False
    fills = [(31, 32), (95, 96), (175, 176), (192, 193)]
    assert sd._groove_outro_start(bass, fills, 193, [True, True, True, True]) == 176


def test_no_outro_when_the_last_fills_keep_the_low_end():
    bass = np.ones(155, bool)
    assert sd._groove_outro_start(bass, [(122, 123), (130, 131)], 155, [False, False]) is None


def test_refine_fill_beats_finds_half_bar_fill():
    fb = np.ones(40, bool)
    fb[10:12] = False                    # bar 2, beats 3-4
    assert sd._refine_fill_beats(fb, 2, 3) == (10, 12)


def test_short_break_is_labelled_break():
    full = [1] * 16 + [0] * 4 + [1] * 20
    bass = np.array([1] * 16 + [0] * 4 + [1] * 20, bool)
    g = run(full, bass)
    secs = [{"start_bar": a, "end_bar": b} for a, b in zip([0] + g["lines"], g["lines"] + [40])]
    sd._label_groove_sections(secs, g, bass, None, soft_intro_outro=False)
    assert [s["label"] for s in secs] == ["intro", "break", "drop"]
