"""Tests for the groove section detector (Source/groove_sections.py, selected by
detect(section_mode="groove")) and for the legacy default staying put.

Rules from the owner's answer key of 2026-11-20 and his holdout check."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import groove_sections as gs  # noqa: E402
import stem_detector as sd  # noqa: E402
import section_format  # noqa: E402
from test_section_soft_rules import _make_envs  # noqa: E402


def beats(bars):
    return np.repeat(np.asarray(bars, bool), 4)


def struct(full_bars, bass_bars):
    return gs.groove_structure(beats(full_bars), np.asarray(bass_bars, bool), len(full_bars))


def run_detect(monkeypatch, tmp_path, kick, bass, mode="groove", provider=None, **kw):
    envs, hop, _ = _make_envs(kick_on_per_bar=np.asarray(kick), bass_on_per_bar=np.asarray(bass),
                              mix_amp_per_bar=kw.pop("mix_amp", None))
    monkeypatch.setattr(sd, "_separate_envelopes", lambda *_a, **_k: (envs, hop))
    wav = tmp_path / "fake.wav"
    wav.write_bytes(b"x")
    return sd.detect(wav, tmp_path, bpm=120.0, downbeat=0.0, make_viz=False, write_json=False,
                     width_cues=False, section_mode=mode, kick_provider=provider, **kw)


def starts(res):
    return [s["start_bar"] for s in res["sections"]]


# --- the flag ----------------------------------------------------------------------

def test_default_mode_is_legacy_and_writes_no_new_fields(monkeypatch, tmp_path):
    # no section_mode argument at all: the default must be legacy
    envs, hop, _ = _make_envs(kick_on_per_bar=np.asarray([1] * 80),
                              bass_on_per_bar=np.asarray([0] * 16 + [1] * 64))
    monkeypatch.setattr(sd, "_separate_envelopes", lambda *_a, **_k: (envs, hop))
    wav = tmp_path / "fake.wav"
    wav.write_bytes(b"x")
    res = sd.detect(wav, tmp_path, bpm=120.0, downbeat=0.0, make_viz=False, write_json=False,
                    width_cues=False)
    assert res["analysis"] == {"kick_model": False}
    assert "fill_sections" not in res["signals"]
    assert section_format.section_detector_of(res) == "legacy"


def test_legacy_detect_never_imports_the_groove_module():
    import subprocess
    src = Path(__file__).resolve().parent.parent / "Source"
    tests = Path(__file__).resolve().parent
    code = (
        "import sys, numpy as np; sys.path.insert(0, r'%s'); sys.path.insert(0, r'%s');"
        "import stem_detector as sd; from test_section_soft_rules import _make_envs;"
        "import tempfile, pathlib; d = pathlib.Path(tempfile.mkdtemp()); w = d / 'f.wav'; w.write_bytes(b'x');"
        "e, h, _ = _make_envs(kick_on_per_bar=np.ones(40, int), bass_on_per_bar=np.ones(40, int));"
        "sd._separate_envelopes = lambda *a, **k: (e, h);"
        "sd.detect(w, d, bpm=120.0, downbeat=0.0, make_viz=False, write_json=False, width_cues=False);"
        "print('groove_sections' in sys.modules)" % (src, tests))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.strip().splitlines()[-1] == "False", out.stdout + out.stderr


def test_groove_mode_marks_its_format(monkeypatch, tmp_path):
    res = run_detect(monkeypatch, tmp_path, [1] * 80, [0] * 16 + [1] * 64)
    a = res["analysis"]
    assert a["section_detector"] == gs.SECTION_DETECTOR and a["sections_schema"] == gs.SECTIONS_SCHEMA
    assert a["downbeat_sec"] == 0.0 and "bar_one_suspect" in a
    assert section_format.section_detector_of(res) == "groove-1"


def test_unknown_mode_is_refused(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        run_detect(monkeypatch, tmp_path, [1] * 40, [1] * 40, mode="new")


def test_legacy_readers_and_default_hint_mode_refuse_groove(tmp_path):
    groove_res = {"track": "T", "bpm": 120.0, "n_bars": 8,
                  "sections": [{"start_bar": 0, "end_bar": 8, "start_sec": 0.0, "end_sec": 16.0, "label": "intro"}],
                  "signals": {"kick_presence_source": "kick-detector-v3"},
                  "analysis": {"kick_model": True, "section_detector": "groove-1", "sections_schema": 2}}
    with pytest.raises(section_format.SectionFormatError):
        section_format.require_legacy(groove_res, "reader")
    p = tmp_path / "SECTIONS_STEM_T.json"
    p.write_text(json.dumps(groove_res), encoding="utf-8")
    res, note = sd._check_cached_sections(p, "T")
    assert res is None and "groove-1" in note
    section_format.require_legacy({"analysis": {"kick_model": True}}, "reader")   # legacy passes


# --- bar one is reported, never moved -------------------------------------------------

def test_detector_never_moves_bar_one(monkeypatch, tmp_path):
    kick = [0] + [1] * 63                         # one silent bar, then the kick
    res = run_detect(monkeypatch, tmp_path, kick, [1] * 64)
    assert res["sections"][0]["start_sec"] == 0.0 and res["analysis"]["downbeat_sec"] == 0.0


def test_bar_one_report_finds_kick_returns_on_one_off_bar_beat():
    fb = np.ones(160, bool)
    for s in (10, 50, 90, 130):                   # every return lands on beat 3 (phase 2)
        fb[s:s + 8] = False
    mix = np.ones(800)
    rep = gs.bar_one_report(mix, 0.1, 120.0, 0.0, fb)
    assert rep["suspect"] and rep["offset_beats"] == 2 and rep["offset_bars"] == 0.5


def test_bar_one_report_is_quiet_on_a_good_grid():
    fb = np.ones(160, bool)
    for s in (12, 52, 92):
        fb[s:s + 4] = False
    rep = gs.bar_one_report(np.ones(800), 0.1, 120.0, 0.0, fb)
    assert not rep["suspect"] and rep["offset_beats"] == 0


def test_bar_one_report_counts_leading_silence_before_first_kick():
    fb = np.zeros(64, bool)
    fb[2:] = True
    mix = np.ones(int(64 * 5)) ; mix[:10] = 0.0   # 2 beats of silence at 0.1 s hops, 120 BPM
    rep = gs.bar_one_report(mix, 0.1, 120.0, 0.0, fb)
    assert rep["leading_silent_beats"] == 2 and rep["first_full_kick_beat"] == 2 and rep["suspect"]


# --- full kick, fills -------------------------------------------------------------------

def test_light_kick_is_heard_but_not_full():
    on = np.array([True, True, True, False])
    peaks = np.array([1.0, 0.5, 0.8, 1.0])
    assert gs.full_kick_beats(on, peaks, 1.0).tolist() == [True, False, True, False]


def test_three_of_four_beats_make_a_full_bar():
    fb = np.array([1, 1, 1, 0] + [1, 1, 0, 0], bool)
    assert gs.full_bars(fb, 2).tolist() == [True, False]


def test_one_and_two_bar_dropouts_are_fills_without_lines():
    full = [1] * 16 + [0] + [1] * 15 + [0, 0] + [1] * 14
    g = struct(full, [1] * 48)
    assert g["lines"] == [] and g["fills"] == [(16, 17), (32, 34)]


def test_bass_held_exception_keeps_a_five_bar_kick_out_a_fill():
    g = struct([1] * 16 + [0] * 5 + [1] * 19, [1] * 40)
    assert g["lines"] == [] and g["fills"] == [(16, 21)]


def test_bass_held_exception_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(gs, "FILL_MAX_BARS_BASS_HELD", gs.SHORT_FILL_BARS)
    g = struct([1] * 16 + [0] * 5 + [1] * 19, [1] * 40)
    assert g["lines"] == [16, 21]


def test_three_bar_kick_out_with_bass_gone_is_a_break():
    full = [1] * 16 + [0] * 3 + [1] * 21
    g = struct(full, [1] * 16 + [0] * 3 + [1] * 21)
    assert g["breaks"] == [(16, 19)]


def test_four_bar_break_with_bass_gone_is_a_section():
    full = [1] * 16 + [0] * 4 + [1] * 20
    g = struct(full, [1] * 16 + [0] * 4 + [1] * 20)
    assert g["lines"] == [16, 20]


def test_seven_bar_kick_out_is_a_break_even_with_bass():
    g = struct([1] * 16 + [0] * 7 + [1] * 17, [1] * 40)
    assert g["lines"] == [16, 23]


def test_drop_two_bars_before_phrase_point_is_the_fill_into_the_break():
    full = [1] * 30 + [0] * 18 + [1] * 16
    g = struct(full, [1] * 30 + [0] * 18 + [1] * 16)
    assert g["lines"] == [32, 48] and (30, 32) in g["lead_fills"]


def test_kick_return_is_not_snapped_to_the_grid():
    full = [1] * 56 + [0] * 18 + [1] * 26
    g = struct(full, [1] * 56 + [0] * 18 + [1] * 26)
    assert g["lines"] == [56, 74]


def test_kick_down_head_is_intro_and_line_is_where_full_kick_arrives():
    g = struct([0] * 16 + [1] * 32, [0] * 8 + [1] * 40)
    assert g["head_end"] == 16 and g["lines"] == [16]


def test_kick_stopping_four_bars_before_the_end_is_a_line_three_is_a_fill():
    g = struct([1] * 40 + [0] * 4, [1] * 44)
    assert g["tail_start"] == 40 and g["lines"] == [40]
    g = struct([1] * 40 + [0] * 3, [1] * 43)
    assert g["tail_start"] is None and g["lines"] == [] and g["fills"] == [(40, 43)]


# --- bass changes ---------------------------------------------------------------------

def test_lasting_bass_entry_under_full_kick_is_a_line():
    assert struct([1] * 48, [0] * 16 + [1] * 32)["lines"] == [16]


def test_bass_change_inside_a_fill_moves_to_the_fill_end():
    # the bass returns one bar INTO the fill (bar 23 of fill 22-24): the line is
    # the fill end 24, not the bar where the bass state flipped
    full = [1] * 22 + [0, 0] + [1] * 24
    bass = [0] * 23 + [1] + [1] * 24
    g = struct(full, bass)
    assert g["lines"] == [24]


def test_change_far_from_a_long_fill_is_not_parked_at_its_end():
    # a 5 bar bass-held fill from 20; the bass enters at bar 20, so the line is 20,
    # not the fill end 25 (fills longer than SHORT_FILL_BARS do not move lines)
    full = [1] * 20 + [0] * 5 + [1] * 23
    bass = [0] * 20 + [1] * 28
    g = struct(full, bass)
    assert 25 not in g["lines"]


def test_thinned_bass_that_comes_back_is_not_a_line():
    assert struct([1] * 48, [1] * 16 + [0] * 2 + [1] * 30)["lines"] == []


def test_a_bass_change_needs_four_bars_of_the_old_state():
    assert struct([1] * 48, [1] + [0] * 47)["lines"] == []


def test_two_bar_fill_with_kick_and_bass_gone_is_not_a_section():
    # Sam 2026-11-20: a fill end is not a section unless the kick or bass state
    # changes there (the old full-stop rule, fitted to Back In The Dance, is gone)
    full = [1] * 54 + [0, 0] + [1] * 24
    g = struct(full, [1] * 54 + [0, 0] + [1] * 24)
    assert g["lines"] == [] and g["fills"] == [(54, 56)]


# --- line spacing ---------------------------------------------------------------------

def test_close_lines_keep_the_stronger_evidence():
    # a weak cue at 20 and a kick return at 21: the kick return stays
    assert gs.keep_strongest({20: gs.RANK["cue"], 21: gs.RANK["kick_return"]}, 64) == [21]
    # equal evidence: the earlier stays
    assert gs.keep_strongest({20: 4, 21: 4}, 64) == [20]
    # two bars apart: both stay
    assert gs.keep_strongest({20: 1, 22: 5}, 64) == [20, 22]


def test_cues_never_cut_inside_kick_down_runs():
    g = struct([1] * 16 + [0] * 16 + [1] * 32, [1] * 64)
    lines = g["lines"]
    assert gs.gate_cue_bars([24], g, 64, lines) == []          # inside the break
    assert gs.gate_cue_bars([33], g, 64, lines) == []          # within 4 bars of a line
    assert gs.gate_cue_bars([48], g, 64, lines) == [48]        # full groove: allowed
    g2 = struct([1] * 20 + [0, 0] + [1] * 42, [1] * 64)
    assert gs.gate_cue_bars([21], g2, 64, g2["lines"]) == []   # inside a fill
    assert gs.gate_cue_bars([22], g2, 64, g2["lines"]) == [22]  # at the fill end


# --- bass state -----------------------------------------------------------------------

def test_bass_in_the_kick_falls_back_to_low_band():
    full = np.array([0] * 8 + [1] * 32 + [0] * 4 + [1] * 20, bool)
    bass_bar = np.where(full, 0.01, 0.0)
    bass_bar[40:44] = 1.0                 # a pad in the break is the only 'bass' Demucs finds
    low = np.where(full, 1.0, 0.2)
    on, src = gs.bass_state_per_bar(np.repeat(bass_bar, 4), low, full)
    assert src == "low_band" and on[10:38].all() and not on[41:43].any()


def test_late_bassline_under_a_big_kick_keeps_the_bass_stem():
    # techno: full kick 0-160, a separate bassline only 64-112. The low band is
    # loud everywhere (the kick). The bass entry and exit lines must survive.
    n = 160
    full = np.ones(n, bool)
    bass_bar = np.zeros(n)
    bass_bar[64:112] = 1.0
    on, src = gs.bass_state_per_bar(np.repeat(bass_bar, 4), np.ones(n), full)
    assert src == "bass_stem"
    g = gs.groove_structure(beats(full), on, n)
    assert g["lines"] == [64, 112]


def test_one_note_on_beat_one_is_not_a_bar_of_bass():
    bass_beats = np.array([1, 1, 1, 1] * 4 + [1, 0, 0, 0] + [0, 0, 0, 0] * 3, float)
    on, _ = gs.bass_state_per_bar(bass_beats, None, np.ones(8, bool))
    assert on.tolist() == [True] * 4 + [False] * 4


# --- outro ----------------------------------------------------------------------------

def test_outro_is_the_bar_after_the_last_bass_bar():
    bass = np.array([1] * 168 + [0] * 16, bool)               # Hannah Wants
    assert gs.outro_start(bass, [(166, 167)], 184, [False]) == 168


def test_outro_moves_to_the_end_of_a_fill_the_bass_leaves_in():
    bass = np.array([1] * 158 + [0] * 18, bool)               # Eli Brown
    assert gs.outro_start(bass, [(158, 160)], 176, [True]) == 160


def test_outro_ignores_one_note_hits_in_the_tail():
    bass = np.array([1] * 128 + [0, 1, 0, 0] * 4, bool)       # Passion VIP
    assert gs.bass_leave_bar(bass) == 128
    assert gs.outro_start(bass, [(126, 128)], 144, [False]) == 128


def test_outro_without_bass_leaving_uses_last_full_low_fill():
    bass = np.ones(193, bool)
    fills = [(31, 32), (95, 96), (175, 176), (192, 193)]
    assert gs.outro_start(bass, fills, 193, [True] * 4) == 176


def test_no_outro_when_the_last_fills_keep_the_low_end():
    assert gs.outro_start(np.ones(155, bool), [(122, 123), (130, 131)], 155, [False, False]) is None


def test_detect_wires_the_groove_outro(monkeypatch, tmp_path):
    # kick throughout; bass 8-60 then a two bar light dip 60-62; bass leaves at 61.
    kick = [1] * 80
    bass = [0] * 8 + [1] * 53 + [0] * 19
    res = run_detect(monkeypatch, tmp_path, kick, bass)
    labels = [(s["start_bar"], s["label"]) for s in res["sections"]]
    assert (61, "outro") in labels, labels


# --- fills as output ------------------------------------------------------------------

def test_one_beat_fill_ending_on_a_bar_line():
    hk = np.ones(40, bool)
    hk[23] = False                       # bar 5, beat 4
    fb = hk.copy()
    assert gs.one_beat_fills(hk, fb, 10) == [5]
    hk2 = np.ones(40, bool)
    hk2[21] = False                      # bar 5, beat 2: not on a bar line
    assert gs.one_beat_fills(hk2, hk2.copy(), 10) == []


def test_trailing_drop_fill_inside_a_soft_break():
    strength = np.full(64, 0.6)
    strength[56:64] = 0.05               # the last two bars before the return drop out
    hk = np.ones(64, bool)
    assert gs.trailing_drop_fill(strength, hk, 0, 64) == (56, 64)
    flat = np.full(64, 0.2)              # a kick-less break that never changes: no fill
    assert gs.trailing_drop_fill(flat, np.zeros(64, bool), 0, 64) is None


def test_refine_fill_beats_finds_half_bar_fill():
    fb = np.ones(40, bool)
    fb[10:12] = False
    assert gs.refine_fill_beats(fb, 2, 3) == (10, 12)


def test_a_line_never_sits_inside_a_fill(monkeypatch, tmp_path):
    kick = [1] * 30 + [0] * 18 + [1] * 32
    bass = [1] * 30 + [0] * 18 + [1] * 32
    res = run_detect(monkeypatch, tmp_path, kick, bass)
    lines = starts(res)[1:]
    for f in res["signals"]["fill_sections"]:
        assert not any(f["start_bar"] < L < f["end_bar"] for L in lines), (f, lines)
    assert 32 in lines and any(f["start_bar"] == 30 and f["end_bar"] == 32 for f in res["signals"]["fill_sections"])


class _GapProvider:
    def on_per_beat(self, wav_path, bpm, downbeat, n_beats):
        on = np.ones(n_beats, dtype=bool)
        on[16:24] = False
        return on


def test_groove_model_kick_out_is_a_fill(monkeypatch, tmp_path):
    # the same fake provider as the legacy pin test_flag_on_fake_provider_...:
    # in groove mode its 2 bar kick-out (beats 16-24 = 8-12 s) is a fill
    envs = {"drums": np.ones(320), "bass": np.zeros(320), "vocals": np.zeros(320),
            "other": np.ones(320) * 0.35, "mix": np.ones(320)}
    envs["bass"][80:280] = 1.0
    monkeypatch.setattr(sd, "_separate_envelopes", lambda *_a, **_k: (envs, 0.1))
    wav = tmp_path / "Track.wav"
    wav.write_bytes(b"x")
    res = sd.detect(wav, tmp_path, bpm=120.0, downbeat=0.0, make_viz=False, write_json=False,
                    kick_provider=_GapProvider(), width_cues=False, section_mode="groove")
    assert res["signals"]["fills"] == [[8.0, 12.0]]
    assert [(f["start_bar"], f["end_bar"]) for f in res["signals"]["fill_sections"]] == [(4.0, 6.0)]


# --- labels ---------------------------------------------------------------------------

def _label(full, bass, outro=None, soft=False, mix=None):
    g = struct(full, bass)
    b = np.asarray(bass, bool)
    lines = g["lines"] + ([outro] if outro and outro not in g["lines"] else [])
    lines = sorted(lines)
    secs = [{"start_bar": a, "end_bar": e} for a, e in zip([0] + lines, lines + [len(full)])]
    gs.label_sections(secs, g, b, outro, soft_intro_outro=soft,
                      mix_norm=None if mix is None else np.asarray(mix, float))
    return [s["label"] for s in secs], secs


def test_short_break_is_labelled_break():
    labels, _ = _label([1] * 16 + [0] * 4 + [1] * 20, [1] * 16 + [0] * 4 + [1] * 20)
    assert labels == ["intro", "break", "drop"]


def test_outro_then_kick_stop_reads_outro_outro_and_names_repeat():
    full = [1] * 40 + [0] * 8
    bass = [1] * 32 + [0] * 16
    labels, secs = _label(full, bass, outro=32)
    assert labels == ["intro", "outro", "outro"]
    assert [s["name"] for s in secs] == ["intro_1", "outro_1", "outro_2"]


def test_intro_intro_lines_are_kept_not_merged():
    labels, secs = _label([0] * 8 + [1] * 56, [0] * 16 + [1] * 48)
    assert labels[:2] == ["intro", "intro"] and [s["start_bar"] for s in secs][:3] == [0, 8, 16]


def test_r4_first_break_before_first_drop_is_intro_when_the_head_is_quiet():
    full = [1] * 16 + [0] * 8 + [1] * 56
    bass = [1] * 16 + [0] * 8 + [1] * 56
    mix = [0.6] * 24 + [1.0] * 56
    labels, _ = _label(full, bass, soft=True, mix=mix)
    assert labels == ["intro", "intro", "drop"]
    labels_loud, _ = _label(full, bass, soft=True, mix=[1.0] * 80)     # 25625f8: loud head
    assert labels_loud == ["intro", "break", "drop"]


def test_kickless_head_is_one_intro_in_groove_mode(monkeypatch, tmp_path):
    kick = [0] * 32 + [1] * 48
    bass = [0] * 16 + [1] * 8 + [0] * 8 + [1] * 48     # bass flips inside the head cut nothing
    res = run_detect(monkeypatch, tmp_path, kick, bass)
    assert starts(res)[:2] == [0, 32] and res["sections"][0]["label"] == "intro"


# --- legacy fold ----------------------------------------------------------------------

def test_legacy_folds_a_tiny_protected_drop_before_the_outro(monkeypatch, tmp_path):
    kick = [1] * 80
    bass = [0] * 8 + [1] * 68 + [0] * 4
    monkeypatch.setattr(sd, "ensure_tier_a_arrays",
                        lambda *_a, **_k: {sd._TIERA_WIDTH_KEY: np.ones(2000)})
    monkeypatch.setattr(sd, "_width_step_cues",
                        lambda *_a, **_k: [{"type": "width_step", "bar": 72.0, "start_sec": 144.0}])
    envs, hop, _ = _make_envs(kick_on_per_bar=np.asarray(kick), bass_on_per_bar=np.asarray(bass))
    monkeypatch.setattr(sd, "_separate_envelopes", lambda *_a, **_k: (envs, hop))
    wav = tmp_path / "fake.wav"
    wav.write_bytes(b"x")
    res = sd.detect(wav, tmp_path, bpm=120.0, downbeat=0.0, make_viz=False, write_json=False,
                    width_cues=True)
    labels = [(s["start_bar"], s["label"]) for s in res["sections"]]
    assert labels[-2:] == [(8, "drop"), (76, "outro")], labels


# --- added for mutants that survived the first mutation run ---------------------------

def test_fill_running_past_a_line_is_cut_at_the_line():
    # Ashibah: outro line at 138, fill 137.25-138.25 -> fill ends at 138.0
    assert gs.clip_fills_to_lines([[549, 553]], [138]) == [[549, 552]]
    assert gs.clip_fills_to_lines([[548, 552], [550, 551]], [140]) == [[548, 552]]


def test_detect_uses_the_groove_outro_not_the_legacy_one(monkeypatch, tmp_path):
    # the bass leaves inside the kick fill 58-60: the groove outro is the fill end 60
    # (legacy would say 58, the bar after the last kick-and-bass bar)
    kick = [1] * 58 + [0, 0] + [1] * 20
    bass = [0] * 8 + [1] * 50 + [0] * 22
    res = run_detect(monkeypatch, tmp_path, kick, bass)
    labels = [(s["start_bar"], s["label"]) for s in res["sections"]]
    assert (60, "outro") in labels and not any(b == 58 for b, _ in labels), labels


def test_groove_outro_is_capped_at_32_bars(monkeypatch, tmp_path):
    kick = [1] * 80
    bass = [0] * 8 + [1] * 32 + [0] * 40          # the bass leaves at 40: 40 bars of tail
    res = run_detect(monkeypatch, tmp_path, kick, bass)
    assert 48 in starts(res), starts(res)
    assert res["sections"][-1]["label"] == "outro" and res["sections"][-1]["start_bar"] == 48


def test_groove_bass_regions_follow_the_groove_bass_state(monkeypatch, tmp_path):
    n_bars, hop = 80, 0.1
    spb = 2.0
    n = int(n_bars * spb / hop) + 10
    envs = {k: np.zeros(n) for k in ("drums", "bass", "vocals", "other", "mix")}
    envs["drums"][:] = 1.0
    envs["mix"][:] = 1.0
    b = lambda bar: int(bar * spb / hop)
    envs["bass"][b(8):b(40)] = 1.0
    envs["bass"][b(40):b(72)] = 0.15              # thinned bass: legacy calls it gone, groove keeps it
    monkeypatch.setattr(sd, "_separate_envelopes", lambda *_a, **_k: (envs, hop))
    wav = tmp_path / "fake.wav"
    wav.write_bytes(b"x")
    kw = dict(bpm=120.0, downbeat=0.0, make_viz=False, write_json=False, width_cues=False)
    groove = sd.detect(wav, tmp_path, section_mode="groove", **kw)
    legacy = sd.detect(wav, tmp_path, **kw)
    assert [r[2:] for r in groove["signals"]["bass_regions"]] == [[8, 72]]
    assert [r[2:] for r in legacy["signals"]["bass_regions"]] == [[8, 40]]
    assert groove["signals"]["bass_state_bar"][39:41] == [1, 1]
    assert groove["signals"]["bass_out"] == 144.0 and legacy["signals"]["bass_out"] == 80.0



# --- round 3: owner's judgements of the 14.08.26 pictures ------------------------------

def test_residue_after_the_drop_is_not_bass_when_the_low_end_thins():
    # Christoph - Reachin: full bass to 175, a residue (0.13-0.31) from 176, low band down
    bass = np.array([0.0] * 32 + [1.0] * 144 + [0.2] * 9 + [0.02] * 9 + [0.25] * 6)
    low = np.array([0.5] * 32 + [1.0] * 144 + [0.67] * 24)
    assert gs.residue_leave_bar(bass, low) == 176


def test_thin_bass_over_a_held_low_end_is_still_bass():
    # Ninetoes 144-160: thinned bass, but the low band holds -> not a residue exit
    bass = np.array([1.0] * 144 + [0.2] * 16 + [0.01] * 32)
    low = np.array([0.9] * 160 + [0.88] * 32)
    assert gs.residue_leave_bar(bass, low) is None


def test_residue_that_comes_back_strongly_is_not_an_exit():
    bass = np.array([1.0] * 100 + [0.2] * 8 + [1.0] * 30)
    low = np.array([1.0] * 100 + [0.6] * 8 + [1.0] * 30)
    assert gs.residue_leave_bar(bass, low) is None


def test_outro_uses_the_earlier_residue_exit():
    bass_on = np.array([1] * 186 + [0] * 8 + [1] * 6, bool)   # residue still reads 'on' to 185
    assert gs.outro_start(bass_on, [], 200, [], residue_bar=176) == 176


def test_long_unchanging_intro_ends_at_the_fill_nearest_one_minute():
    spb = 1.875                                  # 128 BPM: bar 32 = 60 s
    assert gs.minute_fill_line([56, 64], [16, 32, 44, 48], 0.0, spb) == 32
    assert gs.minute_fill_line([24, 64], [16, 32], 0.0, spb) is None        # intro under a minute
    assert gs.minute_fill_line([56], [8, 44], 0.0, spb) is None             # no fill near 1:00
    assert gs.minute_fill_line([], [32], 0.0, spb) == 32


def test_a_cue_on_a_fill_end_cuts_nothing():
    g = struct([1] * 64, [1] * 64)
    assert gs.gate_cue_bars([40], g, 64, [], fill_end_bars=[40]) == []
    assert gs.gate_cue_bars([41], g, 64, [], fill_end_bars=[40]) == []
    assert gs.gate_cue_bars([44], g, 64, [], fill_end_bars=[40]) == [44]


def test_bass_residue_lines_after_the_outro_are_dropped():
    cands = {32: 5, 176: 5, 194: gs.RANK["bass_change"], 150: gs.RANK["bass_change"]}
    out = gs.drop_changes_after_outro(cands, [150, 194], 176)
    assert out == {32: 5, 176: 5, 150: gs.RANK["bass_change"]}
    assert gs.drop_changes_after_outro(cands, [150, 194], None) == cands


def test_a_kick_stop_on_the_same_bar_as_a_dropped_bass_change_keeps_its_line():
    # Bad Behaviours 143: the kick stops AND the residue bass flips there
    cands = {136: 5, 143: gs.RANK["bass_change"]}
    kinds = {136: {"outro"}, 143: {"tail", "bass_change"}}
    out = gs.drop_changes_after_outro(cands, [143], 136, kinds)
    assert out == {136: 5, 143: gs.RANK["tail"]}


def test_residue_exit_needs_the_tail_to_stay_a_residue():
    # after the last 4+ bar run the bass keeps coming back on every other bar
    bass = np.array([1.0] * 100 + [1.0, 0.1] * 12)
    low = np.array([1.0] * 100 + [0.6] * 24)
    assert gs.residue_leave_bar(bass, low) is None


def test_minute_rule_needs_an_opening_longer_than_75_seconds():
    assert gs.minute_fill_line([36], [30], 0.0, 2.0) is None     # first line at 72 s
    assert gs.minute_fill_line([40], [30], 0.0, 2.0) == 30       # first line at 80 s


def _detect_with_low_band(monkeypatch, tmp_path, kick, bass_vals, low_vals):
    n_bars, hop, spb = len(kick), 0.1, 2.0
    n = int(n_bars * spb / hop) + 10
    rep = lambda per_bar: np.concatenate([np.full(int(spb / hop), float(v)) for v in per_bar] + [np.zeros(10)])
    envs = {"drums": rep(kick), "bass": rep(bass_vals), "vocals": np.zeros(n), "other": np.zeros(n),
            "mix": rep([1.0] * n_bars)}
    monkeypatch.setattr(sd, "_separate_envelopes", lambda *_a, **_k: (envs, hop))
    cache = tmp_path / "_Stem Analysis"
    cache.mkdir(exist_ok=True)
    np.savez(cache / "fake__stemenv.npz", hop_t=np.array(hop), **envs, tiera_band_low=rep(low_vals))
    wav = tmp_path / "fake.wav"
    wav.write_bytes(b"x")
    return sd.detect(wav, tmp_path, bpm=120.0, downbeat=0.0, make_viz=False, write_json=False,
                     width_cues=False, section_mode="groove")


def test_detect_wires_the_residue_exit(monkeypatch, tmp_path):
    # the bass drops to a 20% residue at 60 and the low band thins: outro at 60,
    # and the bass strip agrees (no bass after 60)
    res = _detect_with_low_band(monkeypatch, tmp_path, [1] * 80,
                                [0.0] * 8 + [1.0] * 52 + [0.2] * 20, [1.0] * 60 + [0.6] * 20)
    assert (60, "outro") in [(s["start_bar"], s["label"]) for s in res["sections"]]
    assert res["signals"]["bass_state_bar"][60:] == [0] * 20


def test_detect_wires_the_one_minute_fill_in_a_long_unchanging_opening(monkeypatch, tmp_path):
    kick = [1] * 120
    kick[29] = 0                                   # a one bar fill ending at bar 30 = 60 s
    kick[14] = 0
    res = _detect_with_low_band(monkeypatch, tmp_path, kick, [1.0] * 120, [1.0] * 120)
    assert 30 in [s["start_bar"] for s in res["sections"]], [s["start_bar"] for s in res["sections"]]
