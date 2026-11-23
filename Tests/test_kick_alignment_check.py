"""Kick alignment check: synthetic kicks with a known delay, a tiny ALS, the CLI run for real."""
import gzip
import math
import struct
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Source"))
import kick_alignment_check as kac  # noqa: E402

SR = 44100
BEAT_S = 0.5                 # 120 BPM
WAV_BEATS = 100              # 50 s
A_CLIP = (0, 60)             # outgoing: arrangement beats 0..60, source beat = arrangement beat
B_CLIP = (40, 100)           # incoming: arrangement beats 40..100, source beat = arrangement beat - 40
CLI = ROOT / "Tools" / "check_kick_alignment.py"


# ---------------------------------------------------------------- fixtures: kicks, wavs, a tiny ALS

def _click(t):
    return np.sin(2 * np.pi * 4000 * t) * np.exp(-t / 0.0015) * (t < 0.008)


def _body(t, attack):
    phase = 2 * np.pi * 60 * t
    return np.sin(phase) * (1 - np.exp(-t / attack)) * np.exp(-t / 0.12)


def kick(kind):
    """'sharp': click and body together. 'soft': a clear early click, then a low thump that swells over 16 ms,
    so its steepest rise (the main hit) lands 10 ms after the beat."""
    t = np.arange(int(0.4 * SR)) / SR
    if kind == "sharp":
        return 0.8 * _click(t) + _body(t, 0.0015)
    s = np.clip(t - 0.002, 0, None)
    rise = np.where(s < 0.016, 0.5 - 0.5 * np.cos(np.pi * s / 0.016), 1.0)
    return 0.5 * _click(t) + 4.0 * np.sin(2 * np.pi * 60 * s) * rise * np.exp(-s / 0.12) * (t >= 0.002)


def write_wav(path, kind, delay_ms=0.0, kick_beats=range(WAV_BEATS)):
    """delay_ms: one figure, or a function of the source beat."""
    rng = np.random.default_rng(7)
    y = 0.001 * rng.standard_normal(WAV_BEATS * int(BEAT_S * SR))
    k = kick(kind)
    for b in kick_beats:
        d = delay_ms(b) if callable(delay_ms) else delay_ms
        i = int(round((b * BEAT_S + d / 1000.0) * SR))
        y[i:i + len(k)] += k[:len(y) - i]
    sf.write(str(path), y.astype("float32"), SR, subtype="FLOAT")


def _clip_xml(start, end, loop_start, loop_end, wav_name, path=None, relative="", beat_offset=0,
              markers_to=WAV_BEATS, loop_on=False, start_relative=0):
    """beat_offset: marker i has BeatTime i - beat_offset (negative BeatTime when it is above 0)."""
    markers = "".join(f'<WarpMarker SecTime="{i * BEAT_S}" BeatTime="{i - beat_offset}" />'
                      for i in range(markers_to + 1))
    return (f'<AudioClip><CurrentStart Value="{start}" /><CurrentEnd Value="{end}" />'
            f'<Loop><LoopStart Value="{loop_start}" /><LoopEnd Value="{loop_end}" />'
            f'<StartRelative Value="{start_relative}" /><LoopOn Value="{str(loop_on).lower()}" /></Loop>'
            f'<SampleRef><FileRef><RelativePath Value={quoteattr(relative)} />'
            f'<Path Value={quoteattr(path or "C:/Not Here/" + wav_name)} /></FileRef></SampleRef>'
            f'<WarpMarkers>{markers}</WarpMarkers></AudioClip>')


def write_als(path, tracks):
    """tracks: list of (track name, wav name, [(start, end, loop_start, loop_end[, extras dict]), ...])."""
    body = "".join(
        f'<AudioTrack><Name><EffectiveName Value={quoteattr(name)} /></Name><DeviceChain><MainSequencer><Sample>'
        f'<ArrangerAutomation><Events>'
        f'{"".join(_clip_xml(*c[:4], wav, **(c[4] if len(c) > 4 else {})) for c in clips)}'
        f'</Events></ArrangerAutomation></Sample></MainSequencer></DeviceChain></AudioTrack>'
        for name, wav, clips in tracks)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Ableton><LiveSet><Tracks>{body}</Tracks></LiveSet></Ableton>'
    with gzip.open(path, "wb") as fh:
        fh.write(xml.encode("utf-8"))


def make_set(root, name, out_kind="sharp", in_kind="sharp", in_delay_ms=0.0, out_delay_ms=0.0, in_kick_beats=None):
    folder = Path(root) / name
    audio = folder / "Audio"
    audio.mkdir(parents=True)
    write_wav(audio / "Out - First Song SW V2.wav", out_kind, out_delay_ms)
    write_wav(audio / "In - Second Song (Extended Mix) SW V3.wav", in_kind, in_delay_ms,
              range(WAV_BEATS) if in_kick_beats is None else in_kick_beats)
    als = folder / "Set.als"
    write_als(als, [
        ("Out - First Song SW V2", "Out - First Song SW V2.wav", [(*A_CLIP, *A_CLIP)]),
        ("In - Second Song (Extended Mix) SW V3", "In - Second Song (Extended Mix) SW V3.wav", [(B_CLIP[0], B_CLIP[1], 0, 60)]),
    ])
    return als, audio


@pytest.fixture(scope="module")
def sets(tmp_path_factory):
    root = tmp_path_factory.mktemp("kick_alignment")
    return {
        "later": make_set(root, "later", in_delay_ms=8.0),
        "earlier": make_set(root, "earlier", out_delay_ms=8.0),
        "soft": make_set(root, "soft", in_kind="soft"),
        "aligned": make_set(root, "aligned"),
        "few": make_set(root, "few", in_kick_beats=range(0, 100, 5)),
        "late20": make_set(root, "late20", in_delay_ms=20.0),
        "late32": make_set(root, "late32", in_delay_ms=32.0),
        "ramp": make_set(root, "ramp", in_delay_ms=lambda b: 2.0 + 0.6 * b),
    }


@pytest.fixture(scope="module")
def measured(sets):
    return {name: kac.check_als(als, audio) for name, (als, audio) in sets.items()}


# ---------------------------------------------------------------- the lag, its sign, the soft attack

def test_known_delay_reads_back_with_the_right_sign(measured):
    later = measured["later"][0]
    assert later.shared_kicks >= kac.MIN_SHARED_KICKS
    assert abs(later.click["median"] - 8.0) <= 1.0, later.click
    assert abs(later.main["median"] - 8.0) <= 1.0, later.main
    assert later.verdict in ("WARN", "FAIL")
    earlier = measured["earlier"][0]
    assert abs(earlier.click["median"] + 8.0) <= 1.0, earlier.click
    assert abs(earlier.main["median"] + 8.0) <= 1.0, earlier.main


def test_aligned_kicks_pass(measured):
    aligned = measured["aligned"][0]
    assert aligned.verdict == "PASS", (aligned.click, aligned.main, aligned.flags)
    assert abs(aligned.click["median"]) < 1.0 and abs(aligned.main["median"]) < 1.0


def test_soft_attack_shows_click_aligned_and_main_hit_late(measured):
    soft = measured["soft"][0]
    assert abs(soft.click["median"]) <= 1.5, soft.click
    assert abs(soft.main["median"] - 10.0) <= 2.0, soft.main
    assert "SLOW_ATTACK" in soft.flags
    assert soft.verdict in ("WARN", "FAIL")


def test_too_few_shared_kicks_cannot_judge_and_never_pass(measured):
    few = measured["few"][0]
    assert few.shared_kicks < kac.MIN_SHARED_KICKS
    assert few.verdict == "CANNOT_JUDGE"


@pytest.mark.parametrize("shared, expect", [(0, "CANNOT_JUDGE"), (15, "CANNOT_JUDGE"), (16, "PASS")])
def test_judge_thresholds(shared, expect):
    stat = dict(n=shared, median=0.5, iqr=1.0)
    assert kac.judge(shared, stat, dict(stat))[0] == expect


@pytest.mark.parametrize("click, main, expect", [
    (3.9, 0.0, "PASS"), (4.0, 0.0, "WARN"), (0.0, -7.9, "WARN"), (-8.0, 0.0, "FAIL"), (1.0, 6.0, "WARN"),
])
def test_judge_uses_the_worse_of_the_two_in_absolute_value(click, main, expect):
    verdict, flags = kac.judge(40, dict(n=40, median=click, iqr=1.0), dict(n=40, median=main, iqr=1.0))
    assert verdict == expect


def test_judge_flags_a_slow_attack_and_never_passes_it():
    verdict, flags = kac.judge(40, dict(n=40, median=0.0, iqr=1.0), dict(n=40, median=3.9, iqr=1.0))
    assert flags == [] and verdict == "PASS"
    verdict, flags = kac.judge(40, dict(n=40, median=-2.0, iqr=1.0), dict(n=40, median=2.5, iqr=1.0))
    assert flags == ["SLOW_ATTACK"] and verdict == "WARN"


def test_a_scattered_estimator_cannot_fail_or_pass_alone():
    scattered = dict(n=40, median=-9.0, iqr=22.0)
    verdict, flags = kac.judge(40, dict(n=40, median=0.5, iqr=1.0), scattered)
    assert verdict == "WARN" and "SCATTER_MAIN" in flags


def test_few_kicks_with_a_huge_lag_say_so():
    verdict, flags = kac.judge(3, dict(n=3, median=1.0, iqr=0.5), dict(n=3, median=-31.0, iqr=0.5))
    assert verdict == "CANNOT_JUDGE" and flags == ["LARGE_LAG_FEW_KICKS"]


# ---------------------------------------------------------------- the picture and determinism

def test_picture_is_written_and_a_rerun_gives_the_same_numbers(sets, tmp_path):
    als, audio = sets["later"]
    first = kac.check_als(als, audio, tmp_path / "one")
    second = kac.check_als(als, audio, tmp_path / "two")
    png = Path(first[0].picture)
    assert png.name == "T01 First Song to Second Song.png"
    assert png.stat().st_size > 5000
    data = png.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (1800, 600)
    clean = lambda results: [{k: v for k, v in r.items() if k != "picture"} for r in kac.results_json(results)]
    assert clean(first) == clean(second)
    assert Path(second[0].picture).read_bytes() == data


def test_picture_is_drawn_without_pyplot(sets, tmp_path):
    als, audio = sets["later"]
    before = set(sys.modules)
    kac.check_als(als, audio, tmp_path)
    assert "matplotlib.pyplot" not in set(sys.modules) - before


def test_pick_beats_spreads_early_middle_late_and_takes_fewer_when_fewer_exist():
    rows = [kac.BeatMeasure(b, "a", 0.0, "b", 0.0, True) for b in range(100, 140)]
    picked = [r.beat for r in kac.pick_beats(rows)]
    assert len(picked) == 4 and picked == sorted(picked) and picked[0] < 105 and picked[-1] > 134
    assert len(kac.pick_beats(rows[:3])) == 3
    assert kac.pick_beats([]) == []


def test_short_names():
    assert kac.short_name("Jones - Get Right (Extended Mix) SW V6") == "Get Right"
    assert kac.short_name("Rafael Cerato - TCFS (Extended Mix) 24 Bit MASTER") == "TCFS"
    assert kac.short_name("We Met Offline - 007 (Extended Mix) 24 Bit MASTER AMENDED") == "007"
    assert kac.short_name("Hiisak & Reeva - Killa (Extended Mix) SW V2") == "Killa"
    assert kac.short_name("VLAD - POP (Extended Mix) SW V2.2") == "POP"


# ---------------------------------------------------------------- the ALS

def test_als_parse_gives_the_transition_and_the_source_beat_mapping(tmp_path):
    als = tmp_path / "tiny.als"
    write_als(als, [
        ("Second", "second.wav", [(40, 100, 8, 68)]),
        ("First", "first.wav", [(0, 24, 0, 24), (24, 48, 24, 48)]),
    ])
    tracks = kac.read_als(als)
    assert [t.name for t in tracks] == ["First", "Second"]
    transitions = kac.find_transitions(tracks)
    assert len(transitions) == 1
    t = transitions[0]
    assert (t.index, t.outgoing.name, t.incoming.name) == (1, "First", "Second")
    assert (t.start, t.end) == (40.0, 48.0)         # 8 beats of overlap
    pos = kac.source_position(t.incoming, 44.0)
    assert pos.source_beat == 8 + (44 - 40) and pos.sec == pytest.approx(12 * BEAT_S)
    assert pos.clip.wav == "second.wav"
    pos = kac.source_position(t.outgoing, 30.0)
    assert pos.source_beat == 30.0 and pos.clip.start == 24.0
    assert kac.source_position(t.outgoing, 48.0) is None and kac.source_position(t.incoming, 39.0) is None


def test_als_is_only_read(tmp_path):
    als = tmp_path / "tiny.als"
    write_als(als, [("First", "a.wav", [(0, 8, 0, 8)]), ("Second", "b.wav", [(4, 12, 0, 8)])])
    before = als.read_bytes()
    kac.read_als(als)
    assert als.read_bytes() == before


def test_missing_audio_cannot_judge_and_says_which(tmp_path):
    als = tmp_path / "tiny.als"
    write_als(als, [("First", "a.wav", [(0, 8, 0, 8)]), ("Second", "b.wav", [(4, 12, 0, 8)])])
    results = kac.check_als(als, tmp_path / "nowhere", tmp_path / "pics")
    assert results[0].verdict == "CANNOT_JUDGE" and "MISSING_AUDIO" in results[0].flags
    assert "a.wav" in results[0].note and results[0].picture == ""


# ---------------------------------------------------------------- memory: windows only

def test_only_short_windows_are_read(sets, monkeypatch, tmp_path):
    als, audio = sets["later"]
    limit = 3 * SR
    frames_read = []
    real = sf.SoundFile.read

    def guarded(self, frames=-1, *args, **kwargs):
        assert 0 < frames <= limit, f"asked for {frames} frames"
        frames_read.append(frames)
        return real(self, frames, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("soundfile.read loads a whole file")

    monkeypatch.setattr(sf.SoundFile, "read", guarded)
    monkeypatch.setattr(sf, "read", forbidden)
    kac.check_als(als, audio, tmp_path)
    assert frames_read and max(frames_read) <= limit < WAV_BEATS * int(BEAT_S * SR)


# ---------------------------------------------------------------- the CLI, executed

def run_cli(*args, env=None):
    import os
    environ = {k: v for k, v in os.environ.items() if k != "KICK_ALIGNMENT_MODE"}
    environ.update(env or {})
    return subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True,
                          timeout=300, env=environ)


def test_cli_warn_mode_prints_one_line_per_transition_and_exits_zero(sets, tmp_path):
    als, audio = sets["late20"]
    result = run_cli(als, "--audio", audio, "--out", tmp_path / "pics")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("T01 First Song to Second Song: FAIL click +20")
    assert " main +20" in lines[0] and "shared kicks " in lines[0]
    assert lines[-1] == f"LOOK AT THESE PICTURES: {tmp_path / 'pics'}"
    assert (tmp_path / "pics" / "T01 First Song to Second Song.png").stat().st_size > 5000
    assert (tmp_path / "pics" / "kick_alignment.json").is_file()
    assert "\u2014" not in result.stdout


def test_cli_fail_mode_exits_one_on_a_fail_and_zero_on_a_pass(sets, tmp_path):
    bad = run_cli(*sets["late20"][:1], "--audio", sets["late20"][1], "--out", tmp_path / "a", "--mode", "fail")
    assert bad.returncode == 1 and ": FAIL " in bad.stdout
    good = run_cli(*sets["aligned"][:1], "--audio", sets["aligned"][1], "--out", tmp_path / "b", "--mode", "fail")
    assert good.returncode == 0 and ": PASS " in good.stdout


def test_cli_mode_comes_from_the_env_var_when_not_given(sets, tmp_path):
    als, audio = sets["late20"]
    assert run_cli(als, "--audio", audio, "--out", tmp_path / "a", env={"KICK_ALIGNMENT_MODE": "fail"}).returncode == 1
    assert run_cli(als, "--audio", audio, "--out", tmp_path / "b", env={"KICK_ALIGNMENT_MODE": "warn"}).returncode == 0
    assert run_cli(als, "--audio", audio, "--out", tmp_path / "c").returncode == 0


def test_cli_cannot_judge_is_printed_and_is_not_a_pass(sets, tmp_path):
    als, audio = sets["few"]
    result = run_cli(als, "--audio", audio, "--out", tmp_path / "p")
    assert ": CANNOT_JUDGE " in result.stdout and ": PASS " not in result.stdout
    assert result.returncode == 0


def test_cli_unreadable_als_exits_two_even_in_warn_mode(tmp_path):
    result = run_cli(tmp_path / "missing.als", "--audio", tmp_path)
    assert result.returncode == 2 and "could not run" in result.stderr
    bad = tmp_path / "bad.als"
    bad.write_bytes(b"not an als")
    assert run_cli(bad, "--audio", tmp_path).returncode == 2


def test_cli_with_out_given_writes_nothing_beside_the_als(sets, tmp_path):
    als, audio = sets["aligned"]
    before = sorted(p.name for p in als.parent.iterdir())
    run_cli(als, "--audio", audio, "--out", tmp_path / "pics")
    assert sorted(p.name for p in als.parent.iterdir()) == before


# ================================================================ round 2: fixes from the MiniMax review

def stat(n=40, median=0.0, iqr=1.0, big=0, first=None, second=None):
    return dict(n=n, median=median, iqr=iqr, big=big, first_half=first, second_half=second)


NO_STAT = dict(n=0, median=None, iqr=None, big=0, first_half=None, second_half=None)


def fake_result(shared, click, main):
    r = kac.TransitionResult(1, "A - One", "B - Two", "One", "Two", 0.0, 32.0)
    r.shared_kicks, r.click, r.main = shared, click, main
    r.verdict, r.flags = kac.judge(shared, click, main)
    return r


# ---- finding 3: a flam on a minority of the beats

def test_a_flam_on_a_minority_of_beats_is_not_hidden_by_the_median():
    values = [0.0] * 23 + [12.0] * 7
    st = kac._stats(values, list(range(30)), 15)
    assert st["median"] == 0.0 and st["iqr"] == 0.0 and st["big"] == 7
    verdict, flags = kac.judge(30, st, dict(st))
    assert verdict == "WARN" and "MINORITY_FLAM" in flags
    line = kac.summary_line(fake_result(30, st, dict(st)))
    assert "7 of 30 shared beats are 8 ms or more apart on click" in line


def test_a_few_stray_beats_below_the_share_do_not_flag():
    st = kac._stats([0.0] * 25 + [12.0] * 5, list(range(30)), 15)       # 5 of 30 is under 20 percent
    assert kac.judge(30, st, dict(st)) == ("PASS", [])


# ---- finding 4: a scattered estimator with a large median is named, drift is shown

def test_a_scattered_estimator_with_a_large_median_is_flagged_large_scattered():
    verdict, flags = kac.judge(40, stat(median=0.5), stat(median=-9.0, iqr=22.0))
    assert verdict == "WARN" and flags == ["SCATTER_MAIN", "LARGE_SCATTERED"]
    line = kac.summary_line(fake_result(40, stat(median=0.5), stat(median=-9.0, iqr=22.0)))
    assert "main hit median -9.0 ms but the beats spread 22.0 ms" in line


def test_both_scattered_is_a_warn_and_never_a_pass():
    verdict, flags = kac.judge(40, stat(median=1.0, iqr=20.0), stat(median=-1.0, iqr=15.0))
    assert verdict == "WARN" and flags == ["SCATTER_CLICK", "SCATTER_MAIN"]


def test_drift_between_the_halves_is_in_the_summary_and_the_json():
    res = fake_result(40, stat(median=3.0, first=0.0, second=6.0), stat(median=3.0, first=2.0, second=3.0))
    assert "click drifts +0.0 ms to +6.0 ms" in kac.summary_line(res)
    assert "main hit drifts" not in kac.summary_line(res)
    row = kac.results_json([res])[0]
    assert row["click"]["first_half"] == 0.0 and row["click"]["second_half"] == 6.0


# ---- finding 2: a missing estimator does not make the transition unjudgeable

def test_a_missing_click_is_judged_on_the_main_hit_alone():
    verdict, flags = kac.judge(40, NO_STAT, stat(median=15.0))
    assert verdict == "FAIL" and flags == ["NO_CLICK"]
    assert kac.judge(40, NO_STAT, stat(median=0.5)) == ("PASS", ["NO_CLICK"])
    assert "judged on the main hit alone" in kac.summary_line(fake_result(40, NO_STAT, stat(median=15.0)))


def test_a_missing_main_hit_is_judged_on_the_click_alone():
    verdict, flags = kac.judge(40, stat(median=-15.0), NO_STAT)
    assert verdict == "FAIL" and flags == ["NO_MAIN"]


def test_neither_estimator_is_cannot_judge_and_says_so():
    verdict, flags = kac.judge(40, NO_STAT, NO_STAT)
    assert verdict == "CANNOT_JUDGE" and flags == ["NO_CLICK", "NO_MAIN"]


def test_a_transition_with_no_click_lag_at_all_still_fails_on_the_main_hit(sets, monkeypatch):
    monkeypatch.setattr(kac, "click_lag_ms", lambda a, b: (math.nan, math.nan, False))
    als, audio = sets["late20"]
    res = kac.check_als(als, audio)[0]
    assert res.verdict == "FAIL" and "NO_CLICK" in res.flags
    assert abs(res.main["median"] - 20.0) <= 1.5


# ---- finding 14 and 5: few kicks, said plainly

def test_a_lag_of_4_to_8_ms_on_too_few_kicks_is_flagged():
    verdict, flags = kac.judge(12, stat(n=12, median=7.9), stat(n=12, median=7.9))
    assert (verdict, flags) == ("CANNOT_JUDGE", ["LARGE_LAG_FEW_KICKS"])
    assert kac.judge(12, stat(n=12, median=3.9), stat(n=12, median=3.9)) == ("CANNOT_JUDGE", [])
    assert "a lag of 7.9 ms was seen but on too few kicks" in kac.summary_line(
        fake_result(12, stat(n=12, median=7.9), stat(n=12, median=7.9)))


def test_cannot_judge_says_why_when_there_are_few_or_no_shared_kicks(measured):
    few = kac.summary_line(measured["few"][0])
    assert "only " in few and "(need 16)" in few
    assert "search window of plus or minus 35 ms" in few and "cannot be measured" in few
    none = kac.summary_line(fake_result(0, NO_STAT, NO_STAT))
    assert ("CANNOT_JUDGE click n/a main n/a shared kicks 0 | no beat where both tracks have a kick in the "
            "search window of plus or minus 35 ms; a lag larger than that cannot be measured") in none


# ---- finding 1 (rejected, wording): click lined up, main hit late

def test_slow_attack_keeps_its_verdict_and_says_what_it_means(measured):
    line = kac.summary_line(measured["soft"][0])
    assert "SLOW_ATTACK | click lined up, main hit late: look at the picture" in line
    res = fake_result(40, stat(median=-6.0), stat(median=12.0))      # main hit over the fail figure stays FAIL
    assert res.verdict == "FAIL" and "SLOW_ATTACK" in res.flags
    assert "click and main hit disagree by 18.0 ms: look at the picture" in kac.summary_line(res)


# ---- finding 7: the ALS's own paths

REL = dict(path="../Audio/o.wav", relative="../Audio/o.wav")


def _small_set(root, out_delay=0.0, in_delay=8.0, out_clip=(0, 60, 0, 60), in_clip=(40, 100, 0, 60),
               out_extras=None, in_extras=None):
    (root / "Audio").mkdir(parents=True, exist_ok=True)
    (root / "Output").mkdir(parents=True, exist_ok=True)
    write_wav(root / "Audio" / "o.wav", "sharp", out_delay)
    write_wav(root / "Audio" / "i.wav", "sharp", in_delay)
    als = root / "Output" / "Set.als"
    write_als(als, [
        ("Out - A", "o.wav", [(*out_clip, out_extras or dict(path="../Audio/o.wav", relative="../Audio/o.wav"))]),
        ("In - B", "i.wav", [(*in_clip, in_extras or dict(path="../Audio/i.wav", relative="../Audio/i.wav"))]),
    ])
    return als


def test_audio_is_found_through_the_alss_own_relative_path_without_audio_dir(tmp_path):
    als = _small_set(tmp_path)
    res = kac.check_als(als, None)[0]
    assert res.verdict != "CANNOT_JUDGE" and "MISSING_AUDIO" not in res.flags
    assert abs(res.click["median"] - 8.0) <= 1.0
    result = run_cli(als, "--out", tmp_path / "pics")           # no --audio at all
    assert result.returncode == 0 and result.stdout.startswith("T01 A to B: "), result.stdout + result.stderr


def test_the_audio_folder_still_wins_when_it_holds_the_file_name(tmp_path):
    als = _small_set(tmp_path, in_extras=dict(path="../Nowhere/i.wav", relative="../Nowhere/i.wav"))
    res = kac.check_als(als, tmp_path / "Audio")[0]
    assert res.verdict != "CANNOT_JUDGE" and abs(res.click["median"] - 8.0) <= 1.0


# ---- finding 10: two files with the same base name

def test_two_wavs_with_the_same_file_name_in_different_folders_are_not_mixed(tmp_path):
    (tmp_path / "d1").mkdir()
    (tmp_path / "d2").mkdir()
    write_wav(tmp_path / "d1" / "Track.wav", "sharp", 0.0)
    write_wav(tmp_path / "d2" / "Track.wav", "sharp", 8.0)
    als = tmp_path / "Set.als"
    write_als(als, [
        ("Out - A", "Track.wav", [(0, 60, 0, 60, dict(path=(tmp_path / "d1" / "Track.wav").as_posix()))]),
        ("In - B", "Track.wav", [(40, 100, 0, 60, dict(path=(tmp_path / "d2" / "Track.wav").as_posix()))]),
    ])
    res = kac.check_als(als, None)[0]
    assert abs(res.click["median"] - 8.0) <= 1.0, res.click


# ---- finding 11: a clip that starts at the top of its wav keeps its first beats

def test_a_blend_of_exactly_16_beats_at_the_top_of_the_wav_keeps_its_first_beats(tmp_path):
    als = _small_set(tmp_path, in_delay=0.0, out_clip=(0, 56, 0, 56))      # overlap [40, 56) = 16 beats
    res = kac.check_als(als, None)[0]
    assert res.shared_kicks == 16 and res.verdict == "PASS", (res.shared_kicks, res.verdict, res.dropped)
    assert res.padded_windows >= 2
    assert res.dropped == {"no_position": 0, "no_window": 0}
    row = kac.results_json([res])[0]
    assert row["padded_windows"] == res.padded_windows and row["dropped"] == res.dropped


# ---- finding 12: clip shapes that are not mapped are refused

@pytest.mark.parametrize("extras", [dict(loop_on=True), dict(start_relative=1)])
def test_looped_or_start_relative_clips_are_refused_not_guessed(tmp_path, extras):
    als = _small_set(tmp_path, in_extras=dict(path="../Audio/i.wav", **extras))
    res = kac.check_als(als, None, tmp_path / "pics")[0]
    assert res.verdict == "CANNOT_JUDGE" and res.flags == ["UNSUPPORTED_CLIP"]
    assert "LoopOn or StartRelative" in kac.summary_line(res)
    assert res.picture == ""


# ---- finding 13: the click range

def test_the_click_range_is_symmetric(tmp_path):
    for out_delay, in_delay, want in ((0.0, 25.0, 25.0), (25.0, 0.0, -25.0)):
        res = kac.check_als(_small_set(tmp_path / f"s{want:g}", out_delay, in_delay), None)[0]
        assert abs(res.click["median"] - want) <= 1.5, (want, res.click)
        assert res.click_range_edge == 0


def test_a_lag_past_the_click_range_is_counted_and_judged_on_the_main_hit(measured):
    res = measured["late32"][0]
    assert res.click_range_edge >= kac.MIN_SHARED_KICKS
    assert res.verdict == "FAIL" and "NO_CLICK" in res.flags and abs(res.main["median"] - 32.0) <= 2.0
    assert kac.results_json([res])[0]["click_range_edge"] == res.click_range_edge
    assert "click lags sat on the +-30 ms edge" in kac.summary_line(res)


# ---- finding 15: arrangement clips only

def test_session_view_clips_are_not_read(tmp_path):
    session = _clip_xml(0, 8, 0, 8, "s.wav")
    arrangement = _clip_xml(0, 8, 0, 8, "a.wav")
    xml = ('<Ableton><LiveSet><Tracks>'
           f'<AudioTrack><Name><EffectiveName Value="Session only" /></Name><DeviceChain><ClipSlotList><ClipSlot>'
           f'<ClipSlot><Value>{session}</Value></ClipSlot></ClipSlot></ClipSlotList></DeviceChain></AudioTrack>'
           f'<AudioTrack><Name><EffectiveName Value="Arranged" /></Name><DeviceChain><MainSequencer><Sample>'
           f'<ArrangerAutomation><Events>{arrangement}</Events></ArrangerAutomation></Sample></MainSequencer>'
           f'</DeviceChain></AudioTrack></Tracks></LiveSet></Ableton>')
    als = tmp_path / "s.als"
    with gzip.open(als, "wb") as fh:
        fh.write(xml.encode())
    assert [t.name for t in kac.read_als(als)] == ["Arranged"]


# ---- finding 20: the mapping, which the first round barely tested

def test_negative_beat_time_markers_map_to_the_right_seconds(tmp_path):
    als = tmp_path / "neg.als"
    write_als(als, [("A", "a.wav", [(40, 60, 0, 20, dict(beat_offset=2))])])
    pos = kac.source_position(kac.read_als(als)[0], 44.0)
    assert pos.source_beat == 4.0 and pos.sec == pytest.approx(3.0)       # beat -2 is at 0 s, so beat 4 is at 3 s


def test_markers_that_stop_short_of_the_clip_end_drop_the_beats_and_say_so(tmp_path):
    als = _small_set(tmp_path, in_extras=dict(path="../Audio/i.wav", markers_to=10))
    tracks = kac.read_als(als)
    assert kac.source_position(tracks[1], 50.0) is not None and kac.source_position(tracks[1], 51.0) is None
    res = kac.check_als(als, None)[0]
    assert res.dropped["no_position"] == 9 and res.verdict == "CANNOT_JUDGE"


def test_an_outgoing_track_with_several_clips_and_a_gap(tmp_path):
    als = _small_set(tmp_path, in_delay=0.0)
    write_als(als, [
        ("Out - A", "o.wav", [(0, 44, 0, 44, dict(path="../Audio/o.wav")), (48, 70, 48, 70, dict(path="../Audio/o.wav"))]),
        ("In - B", "i.wav", [(30, 100, 0, 70, dict(path="../Audio/i.wav"))]),
    ])
    transitions = kac.find_transitions(kac.read_als(als))
    assert (transitions[0].start, transitions[0].end) == (30.0, 70.0)
    assert kac.source_position(transitions[0].outgoing, 45.0) is None
    assert kac.source_position(transitions[0].outgoing, 48.0).source_beat == 48.0
    res = kac.check_als(als, None)[0]
    assert res.dropped["no_position"] == 4 and res.shared_kicks >= 30 and res.verdict == "PASS"


def test_per_beat_lags_follow_a_changing_delay_and_are_not_one_constant(measured):
    res = measured["ramp"][0]
    beats = [b for b in res.beats if b.shared and not math.isnan(b.click_lag)]
    assert len(beats) >= 12
    for b in beats:
        assert abs(b.click_lag - (2.0 + 0.6 * (b.beat - B_CLIP[0]))) <= 1.5, (b.beat, b.click_lag)
    lags = [b.click_lag for b in beats]
    assert max(lags) - min(lags) > 5.0


# ---- finding 20: the picture rows come from different tracks

def _plotted(fig, columns):
    return [[ax.get_lines()[0].get_ydata() for ax in fig.axes[row * columns:(row + 1) * columns]] for row in (0, 1)]


def _expected_row(readers, wav, sec):
    x, t0, _ = readers[wav].window(sec)
    ms = (t0 + np.arange(len(x)) / readers[wav].sr) * 1000.0
    sel = (ms >= kac.PIC_X_MS[0] - 5) & (ms <= kac.PIC_X_MS[1] + 5)
    return x[sel] / max(float(np.max(np.abs(x[sel]))), 1e-9)


def test_each_picture_row_is_the_window_of_its_own_track(sets):
    als, audio = sets["soft"]                      # outgoing sharp kicks, incoming soft kicks: different audio
    res = kac.check_als(als, audio)[0]
    rows = kac.pick_beats(res.beats)
    readers = {w: kac.WavReader(w) for r in rows for w in (r.wav_out, r.wav_in)}
    try:
        top, bottom = _plotted(kac.build_figure(res, readers), len(rows))
        for col, r in enumerate(rows):
            assert np.allclose(top[col], _expected_row(readers, r.wav_out, r.sec_out))
            assert np.allclose(bottom[col], _expected_row(readers, r.wav_in, r.sec_in))
            assert not np.allclose(top[col], bottom[col])
        # the check has teeth: a figure whose incoming row is the outgoing window drawn twice no longer matches
        wrong_bottom = bottom[0]
        for r in rows:
            r.wav_in, r.sec_in = r.wav_out, r.sec_out
        top2, bottom2 = _plotted(kac.build_figure(res, readers), len(rows))
        assert np.allclose(top2[0], bottom2[0]) and not np.allclose(bottom2[0], wrong_bottom)
    finally:
        for reader in readers.values():
            reader.close()


# ---- finding 8: the count line and the silent no-op

def test_cli_prints_a_count_line(sets, tmp_path):
    result = run_cli(*sets["late20"][:1], "--audio", sets["late20"][1], "--out", tmp_path / "p")
    assert "1 transitions: 0 PASS, 0 WARN, 1 FAIL, 0 CANNOT_JUDGE" in result.stdout.splitlines()


def test_cli_says_loudly_when_nothing_could_be_judged_and_fail_mode_exits_one(sets, tmp_path):
    als, audio = sets["few"]
    warn = run_cli(als, "--audio", audio, "--out", tmp_path / "w")
    assert warn.returncode == 0
    assert "1 transitions: 0 PASS, 0 WARN, 0 FAIL, 1 CANNOT_JUDGE" in warn.stdout
    assert "NOTHING COULD BE JUDGED" in warn.stdout
    fail = run_cli(als, "--audio", audio, "--out", tmp_path / "f", "--mode", "fail")
    assert fail.returncode == 1 and "NOTHING COULD BE JUDGED" in fail.stdout


def test_cli_does_not_point_at_an_empty_picture_folder(sets, tmp_path):
    als, _ = sets["aligned"]
    empty = tmp_path / "no audio here"
    empty.mkdir()
    result = run_cli(als, "--audio", empty, "--out", tmp_path / "p")
    assert result.returncode == 0
    assert "audio not found" in result.stdout and "NOTHING COULD BE JUDGED" in result.stdout
    assert "LOOK AT THESE PICTURES" not in result.stdout and "no pictures were made" in result.stdout


def test_cli_a_judged_set_does_not_print_the_loud_line(sets, tmp_path):
    result = run_cli(*sets["aligned"][:1], "--audio", sets["aligned"][1], "--out", tmp_path / "p", "--mode", "fail")
    assert result.returncode == 0 and "NOTHING COULD BE JUDGED" not in result.stdout


# ---- finding 9: any failure to run is exit 2, with the class and message

def test_cli_truncated_xml_in_a_valid_gzip_is_exit_two_with_the_class_named(tmp_path):
    als = tmp_path / "truncated.als"
    als.write_bytes(gzip.compress(b"<Ableton><LiveSet>"))
    for mode in ("warn", "fail"):
        result = run_cli(als, "--audio", tmp_path, "--mode", mode)
        assert result.returncode == 2, result.stdout + result.stderr
        assert "ParseError" in result.stderr and "could not run" in result.stderr


def test_cli_an_unreadable_wav_is_exit_two_with_the_class_named(tmp_path):
    als, audio = make_set(tmp_path, "bad")
    (audio / "In - Second Song (Extended Mix) SW V3.wav").write_bytes(b"this is not audio")
    result = run_cli(als, "--audio", audio, "--out", tmp_path / "p", "--mode", "fail")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Error" in result.stderr and "could not run" in result.stderr


# ---- finding 16: the default output folder

def test_cli_without_out_writes_a_kick_alignment_folder_beside_the_als(tmp_path):
    als, audio = make_set(tmp_path, "default")
    result = run_cli(als, "--audio", audio)
    assert result.returncode == 0, result.stdout + result.stderr
    folder = als.parent / "Kick Alignment"
    assert (folder / "T01 First Song to Second Song.png").stat().st_size > 5000
    assert result.stdout.splitlines()[-1] == f"LOOK AT THESE PICTURES: {folder}"
