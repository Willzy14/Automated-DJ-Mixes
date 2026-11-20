"""Tests for Tools/measure_section_content.py on synthetic envelopes (hop 0.1 s, 120 bpm so
one bar = 2.0 s = 20 samples, one beat = 5 samples).  Run: python -I -m pytest Tests/"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Tools"))
import measure_section_content as m  # noqa: E402

HOP, BPM, BAR = 0.1, 120.0, 20


def env(kinds, scale=1.0, shift=0):
    """One bar per entry of kinds.  Each entry is a set of words: kick, stem, pumped, sub,
    voc, other.  `shift` delays everything by that many samples."""
    n = len(kinds) * BAR
    e = {k: np.zeros(n) for k in ("drums", "bass", "vocals", "other", "tiera_band_low")}
    for b, ks in enumerate(kinds):
        for i in range(b * BAR, (b + 1) * BAR):
            on_beat = (i % 5) == 0
            if "kick" in ks:
                e["drums"][i] = 1.0 if on_beat else 0.05
                e["tiera_band_low"][i] = 1.0 if on_beat else 0.05
            if "stem" in ks:
                e["bass"][i] = 0.8
            if "pumped" in ks:                       # sidechained: ducked 60 percent of every beat
                e["bass"][i] = 0.1 if (i % 5) < 3 else 0.8
            if "sub" in ks:
                e["tiera_band_low"][i] = max(e["tiera_band_low"][i], 0.9)
            if "voc" in ks:
                e["vocals"][i] = 0.7
            if "other" in ks:
                e["other"][i] = 0.7
    if shift:
        for k in e:
            e[k] = np.concatenate([np.zeros(shift), e[k]])
    return {k: v * scale for k, v in e.items()}


def run(kinds, scale=1.0, shift=0, lead=0.0):
    return m.bar_content(env(kinds, scale=scale, shift=shift), HOP, BPM, len(kinds), lead)


LOUD = {"kick", "stem", "voc", "other"}  # one loud bar so the track's own peak is defined


def test_kick_only_is_kick_not_bass_not_music():
    k, b, mu = run([LOUD, {"kick"}, {"kick"}])
    assert (k[1], b[1], mu[1]) == (1, 0, 0)


def test_bass_stem_means_bass():
    k, b, mu = run([LOUD, {"kick", "stem"}])
    assert b[1] == 1


def test_sidechain_pumped_stem_still_counts_as_bass():
    # the stem is ducked to 0.1 for 60 percent of the bar; the 90th percentile is still 0.8,
    # whereas a median would call the bar empty
    k, b, mu = run([LOUD, {"kick", "pumped"}])
    assert b[1] == 1


def test_sustained_sub_without_a_stem_means_bass():
    # PAX case: no bass stem at all, but the low band holds between kicks
    k, b, mu = run([LOUD, {"kick", "sub"}])
    assert b[1] == 1 and mu[1] == 0


def test_kick_without_sustain_is_not_sub_bass():
    k, b, mu = run([LOUD, {"kick"}])
    assert b[1] == 0


def test_music_is_vocals_or_other():
    k, b, mu = run([LOUD, {"voc"}, {"other"}, set()])
    assert mu[1] == 1 and mu[2] == 1 and mu[3] == 0


def test_silence_is_nothing():
    k, b, mu = run([LOUD, set()])
    assert (k[1], b[1], mu[1]) == (0, 0, 0)


def test_thresholds_are_relative_to_the_tracks_own_level():
    kinds = [LOUD, {"kick", "stem"}, {"kick"}]
    assert run(kinds, scale=1.0) == run(kinds, scale=100.0) == run(kinds, scale=0.001)


def test_lead_in_bars_shifts_the_bar_grid():
    # everything starts 10 samples (half a bar) into the file.  With lead_in_bars 0.5 bar 0 is
    # the loud bar, bar 1 has the bass stem and bar 2 is kick only.  Read from the file start
    # instead, each bar window straddles two real bars and bar 2 wrongly shows bass.
    kinds = [LOUD, {"kick", "stem"}, {"kick"}]
    e = env(kinds, shift=10)
    assert m.bar_content(e, HOP, BPM, 3, 0.5)[1] == [1, 1, 0]
    assert m.bar_content(e, HOP, BPM, 3, 0.0)[1] == [1, 1, 1]


def test_bars_past_the_end_of_the_envelope_are_empty():
    k, b, mu = m.bar_content(env([LOUD, {"kick", "stem"}]), HOP, BPM, 5)
    assert len(b) == 5 and b[2:] == [0, 0, 0] and k[2:] == [0, 0, 0] and mu[2:] == [0, 0, 0]


def test_ranges_are_half_open_runs():
    assert m.ranges([0, 1, 1, 0, 1]) == [[1, 3], [4, 5]]
    assert m.ranges([0, 0]) == [] and m.ranges([1, 1]) == [[0, 2]]


def test_written_file_has_one_flag_per_bar_for_every_key_track():
    p = ROOT / "Documentation" / "Golden Sections" / "Content 2026-11-20 DFTD.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    key = json.loads(m.KEY.read_text(encoding="utf-8"))
    assert set(d["tracks"]) == set(m.MATCH)
    for name, t in d["tracks"].items():
        n = int(np.ceil(key["tracks"][name]["n_bars"]))
        assert len(t["has_kick"]) == len(t["has_bass"]) == len(t["has_music"]) == n
        assert set(t["has_bass"]) <= {0, 1}
    assert d["tracks"]["Adam Ten & Rafael - Call 911"]["lead_in_bars"] == 0.5
