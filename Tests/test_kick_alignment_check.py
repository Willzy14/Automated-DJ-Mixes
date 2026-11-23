"""Kick alignment check: synthetic kicks with a known delay, a tiny ALS, the CLI run for real."""
import gzip
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
    rng = np.random.default_rng(7)
    y = 0.001 * rng.standard_normal(WAV_BEATS * int(BEAT_S * SR))
    k = kick(kind)
    for b in kick_beats:
        i = int(round((b * BEAT_S + delay_ms / 1000.0) * SR))
        y[i:i + len(k)] += k[:len(y) - i]
    sf.write(str(path), y.astype("float32"), SR, subtype="FLOAT")


def _clip_xml(start, end, loop_start, loop_end, wav_name):
    markers = "".join(f'<WarpMarker SecTime="{i * BEAT_S}" BeatTime="{i}" />' for i in range(WAV_BEATS + 1))
    return (f'<AudioClip><CurrentStart Value="{start}" /><CurrentEnd Value="{end}" />'
            f'<Loop><LoopStart Value="{loop_start}" /><LoopEnd Value="{loop_end}" />'
            f'<StartRelative Value="0" /><LoopOn Value="false" /></Loop>'
            f'<SampleRef><FileRef><Path Value={quoteattr("C:/Not Here/" + wav_name)} /></FileRef></SampleRef>'
            f'<WarpMarkers>{markers}</WarpMarkers></AudioClip>')


def write_als(path, tracks):
    """tracks: list of (track name, wav name, [(start, end, loop_start, loop_end), ...])."""
    body = "".join(
        f'<AudioTrack><Name><EffectiveName Value={quoteattr(name)} /></Name><DeviceChain><MainSequencer><Sample>'
        f'<ArrangerAutomation><Events>{"".join(_clip_xml(*c, wav) for c in clips)}</Events></ArrangerAutomation>'
        f'</Sample></MainSequencer></DeviceChain></AudioTrack>'
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
    result = run_cli(als, "--audio", audio, "--out", tmp_path / "p", "--mode", "fail")
    assert ": CANNOT_JUDGE " in result.stdout and ": PASS " not in result.stdout
    assert result.returncode == 0


def test_cli_unreadable_als_exits_two_even_in_warn_mode(tmp_path):
    result = run_cli(tmp_path / "missing.als", "--audio", tmp_path)
    assert result.returncode == 2 and "cannot read" in result.stderr
    bad = tmp_path / "bad.als"
    bad.write_bytes(b"not an als")
    assert run_cli(bad, "--audio", tmp_path).returncode == 2


def test_cli_does_not_write_next_to_the_als_besides_the_picture_folder(sets, tmp_path):
    als, audio = sets["aligned"]
    before = sorted(p.name for p in als.parent.iterdir())
    run_cli(als, "--audio", audio, "--out", tmp_path / "pics")
    assert sorted(p.name for p in als.parent.iterdir()) == before
