"""Tests for Source/render_check.py: render gate over a bounced mix WAV.

All fixtures are built in code from scratch: a minimal gzipped ALS the
parser accepts, an arrangement report dict, and short synthetic renders
(kick-like bursts over a tone bed, never accidentally silent). The corpus
regression pin (V10) skips cleanly when the audio corpus is absent.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

import numpy as np
import pytest
import soundfile as sf


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

import render_check  # noqa: E402


# --------------------------------------------------------------------------- #
# ALS fixture                                                                 #
# --------------------------------------------------------------------------- #

def _als_xml(clips, bpm=128.0, tempo_envelope=None):
    """Minimal gzipped XML the parser accepts: <Ableton>/<LiveSet>, one
    Tempo/Manual, one AudioTrack per track, each with N AudioClips.

    If tempo_envelope is not None, a MainTrack is added at the end with a
    Tempo/AutomationTarget (Id="8") and an AutomationEnvelope whose PointeeId
    is "8" and whose FloatEvents are supplied as values (64 beats apart) or
    explicit (time, value) pairs. The LiveSet
    Tempo/Manual remains the canonical Manual source (the envelope does not
    carry its own Manual)."""
    root = Element("Ableton")
    live = SubElement(root, "LiveSet")
    tempo = SubElement(live, "Tempo")
    manual = SubElement(tempo, "Manual")
    manual.set("Value", str(bpm))

    # Group clips by track so each AudioTrack has the right EffectiveName.
    by_track: dict[str, list[dict]] = {}
    for c in clips:
        by_track.setdefault(c["track"], []).append(c)

    for tname, tclips in by_track.items():
        track = SubElement(live, "AudioTrack")
        name = SubElement(track, "EffectiveName")
        name.set("Value", tname)
        for c in tclips:
            clip = SubElement(track, "AudioClip")
            cs = SubElement(clip, "CurrentStart")
            cs.set("Value", str(c["arr_start"]))
            ce = SubElement(clip, "CurrentEnd")
            ce.set("Value", str(c["arr_end"]))
            cname = SubElement(clip, "Name")
            cname.set("Value", c.get("name", "clip"))
            if c.get("loop_on"):
                loop = SubElement(clip, "Loop")
                ls = SubElement(loop, "LoopStart")
                ls.set("Value", str(c["loop_start"]))
                le = SubElement(loop, "LoopEnd")
                le.set("Value", str(c["loop_end"]))
                sr = SubElement(loop, "StartRelative")
                sr.set("Value", str(c.get("start_relative", 0.0)))
                lo = SubElement(loop, "LoopOn")
                lo.set("Value", "true")
            else:
                loop = SubElement(clip, "Loop")
                ls = SubElement(loop, "LoopStart")
                ls.set("Value", "0")
                le = SubElement(loop, "LoopEnd")
                le.set("Value", str(c["arr_end"] - c["arr_start"]))
                sr = SubElement(loop, "StartRelative")
                sr.set("Value", "0")
                lo = SubElement(loop, "LoopOn")
                lo.set("Value", "false")

    if tempo_envelope is not None:
        # The MainTrack shape mirrors als_generator._build_envelope_xml:
        # Tempo/AutomationTarget with Id="8" and an AutomationEnvelope with
        # EnvelopeTarget/PointeeId Value="8" and FloatEvent Values.
        mt = SubElement(live, "MainTrack")
        mt_tempo = SubElement(mt, "Tempo")
        at = SubElement(mt_tempo, "AutomationTarget")
        at.set("Id", "8")
        env = SubElement(mt, "AutomationEnvelope")
        target = SubElement(env, "EnvelopeTarget")
        pid = SubElement(target, "PointeeId")
        pid.set("Value", "8")
        auto = SubElement(env, "Automation")
        events = SubElement(auto, "Events")
        for i, item in enumerate(tempo_envelope):
            if isinstance(item, (tuple, list)):
                event_time, value = item
            else:
                event_time, value = i * 64.0, item
            fe = SubElement(events, "FloatEvent")
            fe.set("Time", str(event_time))
            fe.set("Value", str(value))
    return gzip.compress(tostring(root, encoding="utf-8"))


def _write_als(path, clips, bpm=128.0, tempo_envelope=None):
    path.write_bytes(_als_xml(clips, bpm, tempo_envelope=tempo_envelope))


def _write_report(path, loops=None, transitions=None):
    obj = {"loops": loops or [], "transitions": transitions or []}
    path.write_text(json.dumps(obj), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Synthetic render                                                            #
# --------------------------------------------------------------------------- #

def _synth_render(path, seconds, *, bpm=128.0, sr=44100,
                  clips=None, extra_process=None,
                  level_db=-20.0, with_kicks=True):
    """Stereo float -> PCM_24. Kick-like bursts (decaying sine) on a -20 dBFS
    220 Hz tone bed, so the file is never accidentally silent. Per-clip gain
    lets each test scale a clip's amplitude. extra_process(t, L, R) runs on
    every sample and can inject defects (silence, clicks, dips). With
    with_kicks=False, the per-beat kick layer is skipped -- useful for
    correlation tests where periodic peaks at frame boundaries produce
    spurious anti-correlation between otherwise-identical iterations."""
    spb = sr * 60.0 / bpm
    n = int(round(seconds * sr))
    t = np.arange(n) / sr
    amp = 10 ** (level_db / 20.0)
    L = amp * 0.5 * np.sin(2 * math.pi * 220.0 * t)
    R = amp * 0.5 * np.sin(2 * math.pi * 220.0 * t + 0.001)

    if with_kicks:
        beat_times = np.arange(0, seconds, 60.0 / bpm)
        for bt in beat_times:
            i0 = int(round(bt * sr))
            dur_samp = int(round(0.06 * sr))
            if i0 + dur_samp > n:
                break
            env = np.exp(-np.arange(dur_samp) / (sr * 0.012))
            ramp = int(round(0.005 * sr))
            env[:ramp] *= np.linspace(0, 1, ramp)
            L[i0:i0 + dur_samp] += env * np.sin(2 * math.pi * 60.0 * np.arange(dur_samp) / sr)
            R[i0:i0 + dur_samp] += env * np.sin(2 * math.pi * 60.0 * np.arange(dur_samp) / sr)

    if clips:
        sec_per_beat = 60.0 / bpm
        for c in clips:
            i0 = int(round(c["arr_start"] * sec_per_beat * sr))
            i1 = int(round(c["arr_end"] * sec_per_beat * sr))
            i0 = max(0, i0)
            i1 = min(n, i1)
            gain = c.get("gain", 1.0)
            if gain != 1.0:
                L[i0:i1] *= gain
                R[i0:i1] *= gain
            if c.get("freq"):
                f = c["freq"]
                seg_t = np.arange(i1 - i0) / sr
                L[i0:i1] = L[i0:i1] * 0.5 + amp * 0.4 * np.sin(2 * math.pi * f * seg_t)
                R[i0:i1] = R[i0:i1] * 0.5 + amp * 0.4 * np.sin(2 * math.pi * f * seg_t)

    if extra_process is not None:
        L, R = extra_process(t, L, R)

    stereo = np.stack([np.clip(L, -1.0, 1.0), np.clip(R, -1.0, 1.0)], axis=1)
    sf.write(str(path), stereo, sr, subtype="PCM_24")


def _single_track_clips(seconds, bpm=128.0, name="Tone"):
    """One full-track AudioClip covering the whole render. Useful for
    building up defect tests where exactly one clip is active everywhere."""
    return [{
        "track": name,
        "arr_start": 0,
        "arr_end": round(seconds * bpm / 60.0),
        "loop_on": False,
    }]


def _two_track_overlap(seconds, bpm=128.0):
    """Track A covers first half, Track B covers second half, then a 20%
    overlap region where both are active (so exposed_solo is NOT triggered
    on the quiet material in the second half)."""
    half = seconds / 2.0
    overlap_start = seconds * 0.5
    overlap_end = seconds * 0.7
    beats_per_sec = bpm / 60.0
    return [
        {"track": "A", "arr_start": 0,
         "arr_end": round(overlap_end * beats_per_sec),
         "loop_on": False},
        {"track": "B", "arr_start": round(overlap_start * beats_per_sec),
         "arr_end": round(seconds * beats_per_sec),
         "loop_on": False},
    ]


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #

def test_clean_render_passes(tmp_path):
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    seconds = 30.0
    clips = _single_track_clips(seconds)
    _write_als(als, clips)
    _synth_render(wav, seconds, clips=clips)
    _write_report(rpt)

    res = render_check.run_check(wav, rpt, als)
    assert res.exit_code == 0, [(f.check, f.level) for f in res.findings]
    assert res.verdict == "PASS"
    # INFO findings never affect the exit code (per spec); only FAIL/WARN count.
    assert not any(f.level in ("FAIL", "WARN") for f in res.findings)


def test_hard_silence_injected_fails(tmp_path):
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    seconds = 30.0
    clips = _single_track_clips(seconds)
    _write_als(als, clips)

    silence_start = 10.0
    silence_end = 11.0

    def silence(t, L, R):
        m = (t >= silence_start) & (t < silence_end)
        L = L.copy()
        R = R.copy()
        L[m] = 0.0
        R[m] = 0.0
        return L, R

    _synth_render(wav, seconds, clips=clips, extra_process=silence)
    _write_report(rpt)

    res = render_check.run_check(wav, rpt, als)
    assert res.exit_code == 2
    assert any(f.check == "hard_silence" and f.level == "FAIL" for f in res.findings)

    # Control A: 0.3 s silence (below the 0.5 s threshold) -> no finding.
    wav_b = tmp_path / "short.wav"
    rpt_b = tmp_path / "r_short.json"
    def short_silence(t, L, R):
        m = (t >= 5.0) & (t < 5.3)
        L = L.copy(); R = R.copy()
        L[m] = 0.0; R[m] = 0.0
        return L, R
    _synth_render(wav_b, seconds, clips=clips, extra_process=short_silence)
    _write_report(rpt_b)
    res_b = render_check.run_check(wav_b, rpt_b, als)
    assert not any(f.check == "hard_silence" for f in res_b.findings)

    # Control B: silence AFTER last clip end (the render tail) -> no finding.
    # Rendered 2 s longer than the arrangement so the tail actually exists.
    wav_c = tmp_path / "tail.wav"
    rpt_c = tmp_path / "r_tail.json"
    end_sec = clips[0]["arr_end"] * 60.0 / 128.0
    def tail_silence(t, L, R):
        m = t > end_sec
        L = L.copy(); R = R.copy()
        L[m] = 0.0; R[m] = 0.0
        return L, R
    _synth_render(wav_c, seconds + 2.0, clips=clips, extra_process=tail_silence)
    _write_report(rpt_c)
    res_c = render_check.run_check(wav_c, rpt_c, als)
    assert not any(f.check == "hard_silence" for f in res_c.findings)


def test_boundary_click_single_sample_step(tmp_path):
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    bpm = 128.0
    seconds = 30.0
    bps = bpm / 60.0
    # Two clips that meet exactly at beat 10 (4.6875 s).
    boundary_beat = 10
    clips = [
        {"track": "A", "arr_start": 0, "arr_end": boundary_beat, "loop_on": False},
        {"track": "B", "arr_start": boundary_beat,
         "arr_end": round(seconds * bps), "loop_on": False},
    ]
    _write_als(als, clips)

    def click_at_boundary(t, L, R):
        L = L.copy(); R = R.copy()
        t_b = boundary_beat / bps
        idx = int(round(t_b * 44100))
        L[idx] = L[idx] + 0.7
        R[idx] = R[idx] + 0.7
        return L, R

    _synth_render(wav, seconds, clips=clips, extra_process=click_at_boundary)
    _write_report(rpt)
    res = render_check.run_check(wav, rpt, als)
    assert any(f.check == "boundary_click" and f.level == "FAIL" for f in res.findings), \
        [(f.check, f.level, f.measured) for f in res.findings]

    # Control: a 5 ms ramped loud hit at the same boundary -> no click finding.
    wav_b = tmp_path / "ramp.wav"
    rpt_b = tmp_path / "r_ramp.json"
    def ramp_hit(t, L, R):
        L = L.copy(); R = R.copy()
        t_b = boundary_beat / bps
        i0 = int(round((t_b - 0.003) * 44100))
        i1 = int(round((t_b + 0.003) * 44100))
        ramp = np.linspace(0, 0.9, i1 - i0)
        L[i0:i1] += ramp
        R[i0:i1] += ramp
        return L, R
    _synth_render(wav_b, seconds, clips=clips, extra_process=ramp_hit)
    _write_report(rpt_b)
    res_b = render_check.run_check(wav_b, rpt_b, als)
    assert not any(f.check == "boundary_click" for f in res_b.findings), \
        [(f.check, f.level) for f in res_b.findings]


def test_level_cliff_at_loop_insert(tmp_path):
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    bpm = 128.0
    seconds = 30.0
    bps = bpm / 60.0
    insert_beat = 16
    # Whole-render base clip; loop insert at beat 16 drops 8 dB.
    clips = [
        {"track": "A", "arr_start": 0, "arr_end": round(seconds * bps),
         "loop_on": False},
    ]
    _write_als(als, clips)
    loops = [{
        "track": "A",
        "type": "tail",
        "source_beats": f"100-116",  # 16-beat loop
        "count": 1,
        "total_beats": 16.0,
        "insert_at_beat": insert_beat,
    }]

    def drop_at_insert(t, L, R):
        L = L.copy(); R = R.copy()
        t_b = insert_beat / bps
        m = t >= t_b
        L[m] *= 0.4  # ~ -8 dB
        R[m] *= 0.4
        return L, R

    _synth_render(wav, seconds, clips=clips, extra_process=drop_at_insert)
    _write_report(rpt, loops=loops)
    res = render_check.run_check(wav, rpt, als)
    cliffs = [f for f in res.findings if f.check == "level_cliff"]
    assert cliffs, [f.check for f in res.findings]
    assert all(f.level == "WARN" for f in cliffs)
    assert res.exit_code == 1

    # Control: 3 dB step -> no finding.
    wav_b = tmp_path / "small.wav"
    rpt_b = tmp_path / "r_small.json"
    def small_drop(t, L, R):
        L = L.copy(); R = R.copy()
        t_b = insert_beat / bps
        m = t >= t_b
        L[m] *= 10 ** (-3 / 20)  # ~ -3 dB
        R[m] *= 10 ** (-3 / 20)
        return L, R
    _synth_render(wav_b, seconds, clips=clips, extra_process=small_drop)
    _write_report(rpt_b, loops=loops)
    res_b = render_check.run_check(wav_b, rpt_b, als)
    assert not any(f.check == "level_cliff" for f in res_b.findings)


def test_loop_period_off_phrase(tmp_path):
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    bpm = 128.0
    seconds = 30.0
    bps = bpm / 60.0
    clips = [{
        "track": "A", "arr_start": 0, "arr_end": round(seconds * bps),
        "loop_on": False,
    }]
    _write_als(als, clips)
    # 12-beat loop, 4 iterations starting at beat 16.
    loops = [{
        "track": "A", "type": "tail",
        "source_beats": "100-112",  # 12-beat loop
        "count": 4,
        "total_beats": 48.0,
        "insert_at_beat": 16,
    }]

    def repeat_pattern(t, L, R):
        # 4-beat repeating loud/quiet pattern within the loop span.
        L = L.copy(); R = R.copy()
        t0 = (16 / bps)
        t1 = ((16 + 48) / bps)
        m = (t >= t0) & (t < t1)
        phase = ((t - t0) % (4 / bps)) / (1 / bps)  # beat position within 4-beat
        env = np.where((phase % 4) < 2, 1.0, 0.3)
        L[m] *= env[m]
        R[m] *= env[m]
        return L, R

    _synth_render(wav, seconds, clips=clips, extra_process=repeat_pattern)
    _write_report(rpt, loops=loops)
    res = render_check.run_check(wav, rpt, als)
    periods = [f for f in res.findings if f.check == "loop_period"]
    assert periods, [f.check for f in res.findings]
    assert any(f.level == "WARN" for f in periods)
    # Lag in measured should be 12.
    assert any(abs(f.measured.get("iter_len", 0) - 12) < 1e-6 for f in periods)

    # Control: 16-beat loop -> no finding.
    wav_b = tmp_path / "ok.wav"
    rpt_b = tmp_path / "r_ok.json"
    loops_ok = [{
        "track": "A", "type": "tail",
        "source_beats": "100-116",
        "count": 3,
        "total_beats": 48.0,
        "insert_at_beat": 16,
    }]
    _synth_render(wav_b, seconds, clips=clips, extra_process=repeat_pattern)
    _write_report(rpt_b, loops=loops_ok)
    res_b = render_check.run_check(wav_b, rpt_b, als)
    assert not any(f.check == "loop_period" for f in res_b.findings)


def test_exposed_solo_quiet_stretch(tmp_path):
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    bpm = 128.0
    seconds = 40.0
    bps = bpm / 60.0
    # Single clip covers everything (one active clip everywhere).
    clips = [{
        "track": "Solo", "arr_start": 0,
        "arr_end": round(seconds * bps),
        "loop_on": False,
    }]
    _write_als(als, clips)

    quiet_start = 10.0
    quiet_end = 16.0  # 6 s

    def quiet_stretch(t, L, R):
        L = L.copy(); R = R.copy()
        m = (t >= quiet_start) & (t < quiet_end)
        L[m] *= 10 ** (-15 / 20)  # -15 dB drop -> floor ~ -35 dBFS
        R[m] *= 10 ** (-15 / 20)
        return L, R

    _synth_render(wav, seconds, clips=clips, extra_process=quiet_stretch,
                  level_db=-20.0)
    _write_report(rpt)
    res = render_check.run_check(wav, rpt, als)
    solos = [f for f in res.findings if f.check == "exposed_solo"]
    assert solos, [f.check for f in res.findings]
    assert all(f.level == "WARN" for f in solos)

    # Control: same quiet audio but with TWO overlapping clips in the ALS at
    # that time -> no exposed_solo finding.
    als_b = tmp_path / "m2.als"
    wav_b = tmp_path / "two.wav"
    rpt_b = tmp_path / "r2.json"
    beats_total = round(seconds * bps)
    clips_two = [
        {"track": "A", "arr_start": 0, "arr_end": beats_total, "loop_on": False},
        {"track": "B", "arr_start": 0, "arr_end": beats_total, "loop_on": False},
    ]
    _write_als(als_b, clips_two)
    _synth_render(wav_b, seconds, clips=clips_two, extra_process=quiet_stretch,
                  level_db=-20.0)
    _write_report(rpt_b)
    res_b = render_check.run_check(wav_b, rpt_b, als_b)
    assert not any(f.check == "exposed_solo" for f in res_b.findings), \
        [(f.check, f.t0, f.t1) for f in res_b.findings if f.check == "exposed_solo"]


def test_loop_hole_quiet_run(tmp_path):
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    bpm = 128.0
    seconds = 30.0
    bps = bpm / 60.0
    clips = [{
        "track": "A", "arr_start": 0, "arr_end": round(seconds * bps),
        "loop_on": False,
    }]
    _write_als(als, clips)
    loops = [{
        "track": "A", "type": "tail",
        "source_beats": "100-108",  # 8-beat loop
        "count": 4,
        "total_beats": 32.0,
        "insert_at_beat": 16,
    }]

    def pattern(t, L, R):
        L = L.copy(); R = R.copy()
        t0 = (16 / bps)
        t1 = ((16 + 32) / bps)
        m = (t >= t0) & (t < t1)
        local = (t - t0) * bps  # beat position within loop
        within = local % 8
        env = np.where(within < 4, 1.0, 0.4)  # 4 loud + 4 quiet
        L[m] *= env[m]
        R[m] *= env[m]
        return L, R

    _synth_render(wav, seconds, clips=clips, extra_process=pattern)
    _write_report(rpt, loops=loops)
    res = render_check.run_check(wav, rpt, als)
    holes = [f for f in res.findings if f.check == "loop_hole"]
    assert holes, [f.check for f in res.findings]

    # Control: flat loop -> no finding.
    wav_b = tmp_path / "flat.wav"
    rpt_b = tmp_path / "r_flat.json"
    def noop(t, L, R):
        return L, R
    _synth_render(wav_b, seconds, clips=clips, extra_process=noop)
    _write_report(rpt_b, loops=loops)
    res_b = render_check.run_check(wav_b, rpt_b, als)
    assert not any(f.check == "loop_hole" for f in res_b.findings)


def test_loop_exit_jump(tmp_path):
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    bpm = 128.0
    seconds = 30.0
    bps = bpm / 60.0
    clips = [{
        "track": "A", "arr_start": 0, "arr_end": round(seconds * bps),
        "loop_on": False,
    }]
    _write_als(als, clips)
    insert = 16
    exit = insert + 16  # one iteration
    loops = [{
        "track": "A", "type": "tail",
        "source_beats": "100-116",  # 16-beat loop, 1 iter
        "count": 1,
        "total_beats": 16.0,
        "insert_at_beat": insert,
    }]

    def bump(t, L, R):
        L = L.copy(); R = R.copy()
        t_b = exit / bps
        m = t >= t_b
        L[m] *= 10 ** (5 / 20)  # +5 dB
        R[m] *= 10 ** (5 / 20)
        return L, R

    _synth_render(wav, seconds, clips=clips, extra_process=bump)
    _write_report(rpt, loops=loops)
    res = render_check.run_check(wav, rpt, als)
    jumps = [f for f in res.findings if f.check == "loop_exit_jump"]
    assert jumps, [f.check for f in res.findings]
    assert all(f.level == "WARN" for f in jumps)

    # Control: +2 dB -> no finding.
    wav_b = tmp_path / "small.wav"
    rpt_b = tmp_path / "r_small.json"
    def small_bump(t, L, R):
        L = L.copy(); R = R.copy()
        t_b = exit / bps
        m = t >= t_b
        L[m] *= 10 ** (2 / 20)
        R[m] *= 10 ** (2 / 20)
        return L, R
    _synth_render(wav_b, seconds, clips=clips, extra_process=small_bump)
    _write_report(rpt_b, loops=loops)
    res_b = render_check.run_check(wav_b, rpt_b, als)
    assert not any(f.check == "loop_exit_jump" for f in res_b.findings)


def test_loop_verbatim_envelope_correlation(tmp_path):
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    bpm = 128.0
    seconds = 30.0
    bps = bpm / 60.0
    # 4 iterations of 4 beats each. Kicks stay ON: the check reads each
    # iteration at exact sample offsets, so identical iterations correlate
    # ~1.0 regardless of where the 100 ms sweep frames fall -- the kick
    # attacks are the very structure the correlation should be measuring.
    clips = [{
        "track": "A", "arr_start": 0, "arr_end": round(seconds * bps),
        "loop_on": False,
    }]
    _write_als(als, clips)
    loops = [{
        "track": "A", "type": "tail",
        "source_beats": "100-104",  # 4-beat loop
        "count": 4,
        "total_beats": 16.0,
        "insert_at_beat": 16,
    }]

    def break_iteration(t, L, R):
        # Iteration 3 (beats 24..28) uses a different envelope shape:
        # half the time silence, half the time the bed. Different shape
        # across the iteration -> rms100 envelope diverges -> r drops < 0.9.
        L = L.copy(); R = R.copy()
        m3 = (t >= (24 / bps)) & (t < (28 / bps))
        local_idx = np.where(m3)[0]
        within = (t[local_idx] - (24 / bps)) * bps  # beat pos within iter
        # 4-beat iteration -> silence beats 25, 27; bed on 24, 26.
        silent = ((within.astype(int)) % 2) == 1
        L[local_idx[silent]] = 0.0
        R[local_idx[silent]] = 0.0
        return L, R

    _synth_render(wav, seconds, clips=clips, extra_process=break_iteration)
    _write_report(rpt, loops=loops)
    res = render_check.run_check(wav, rpt, als)
    vbs = [f for f in res.findings if f.check == "loop_verbatim"]
    assert vbs, [(f.check, f.level) for f in res.findings]
    assert all(f.level == "FAIL" for f in vbs)
    assert any(f.measured.get("min_r", 1.0) < 0.9 for f in vbs)

    # Control: flat loop (no modification) -> no finding.
    wav_b = tmp_path / "flat.wav"
    rpt_b = tmp_path / "r_flat.json"
    def noop(t, L, R):
        return L, R
    _synth_render(wav_b, seconds, clips=clips, extra_process=noop)
    _write_report(rpt_b, loops=loops)
    res_b = render_check.run_check(wav_b, rpt_b, als)
    assert not any(f.check == "loop_verbatim" for f in res_b.findings), \
        [(f.check, f.measured.get("min_r")) for f in res_b.findings if f.check == "loop_verbatim"]


def test_transition_dip(tmp_path):
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    bpm = 128.0
    seconds = 40.0
    bps = bpm / 60.0
    # Long base track; transition overlaps near the middle.
    clips = [{
        "track": "A", "arr_start": 0, "arr_end": round(seconds * bps),
        "loop_on": False,
    }]
    _write_als(als, clips)

    swap_beat = 32
    overlap = 8
    transitions = [{
        "pair_index": 1,
        "swap_beats": swap_beat,
        "overlap_beats": overlap,
        "swap_progress": 0.5,
    }]

    def dip(t, L, R):
        L = L.copy(); R = R.copy()
        t0 = ((swap_beat - overlap * 0.5) / bps)
        t1 = ((swap_beat - overlap * 0.5 + overlap) / bps)
        m = (t >= t0) & (t < t1)
        L[m] *= 10 ** (-5 / 20)  # ~5 dB dip
        R[m] *= 10 ** (-5 / 20)
        return L, R

    _synth_render(wav, seconds, clips=clips, extra_process=dip)
    _write_report(rpt, transitions=transitions)
    res = render_check.run_check(wav, rpt, als)
    dips = [f for f in res.findings if f.check == "transition_dip"]
    assert dips, [f.check for f in res.findings]
    assert any(f.measured.get("pair_index") == 1 for f in dips)
    # run_check must pass render_path through, or the band diagnosis silently
    # vanishes from every real run while the unit tests still pass.
    assert "band_db" in dips[0].measured, dips[0].measured
    assert set(dips[0].measured["band_db"]) == {
        n for n, _, _ in render_check.DIP_BANDS}, dips[0].measured["band_db"]
    assert dips[0].measured["deficit_band"], dips[0].measured
    assert "dip_at_sec" in dips[0].measured, dips[0].measured

    # Control: 1 dB dip -> no finding.
    wav_b = tmp_path / "sm.wav"
    rpt_b = tmp_path / "r_sm.json"
    def small_dip(t, L, R):
        L = L.copy(); R = R.copy()
        t0 = ((swap_beat - overlap * 0.5) / bps)
        t1 = ((swap_beat - overlap * 0.5 + overlap) / bps)
        m = (t >= t0) & (t < t1)
        L[m] *= 10 ** (-1 / 20)
        R[m] *= 10 ** (-1 / 20)
        return L, R
    _synth_render(wav_b, seconds, clips=clips, extra_process=small_dip)
    _write_report(rpt_b, transitions=transitions)
    res_b = render_check.run_check(wav_b, rpt_b, als)
    assert not any(f.check == "transition_dip" for f in res_b.findings)


def test_exit_code_semantics(tmp_path):
    """Pin: 0 = clean, 1 = warn only, 2 = fail."""
    # Clean -> 0 (reuse test_clean_render_passes setup).
    als = tmp_path / "a.als"
    wav = tmp_path / "a.wav"
    rpt = tmp_path / "a.json"
    seconds = 20.0
    clips = _single_track_clips(seconds)
    _write_als(als, clips)
    _synth_render(wav, seconds, clips=clips)
    _write_report(rpt)
    assert render_check.run_check(wav, rpt, als).exit_code == 0

    # Warn only -> 1 (loop_exit_jump, no FAIL).
    als = tmp_path / "b.als"
    wav = tmp_path / "b.wav"
    rpt = tmp_path / "b.json"
    bps = 128.0 / 60.0
    clips = [{"track": "A", "arr_start": 0, "arr_end": round(seconds * bps), "loop_on": False}]
    _write_als(als, clips)
    def bump(t, L, R):
        L = L.copy(); R = R.copy()
        m = t >= (16 + 16) / bps
        L[m] *= 10 ** (5 / 20); R[m] *= 10 ** (5 / 20)
        return L, R
    _synth_render(wav, seconds, clips=clips, extra_process=bump)
    _write_report(rpt, loops=[{
        "track": "A", "type": "tail", "source_beats": "100-116",
        "count": 1, "total_beats": 16.0, "insert_at_beat": 16,
    }])
    res = render_check.run_check(wav, rpt, als)
    assert res.exit_code == 1
    assert res.verdict == "WARN"

    # Fail -> 2 (silence in middle).
    als = tmp_path / "c.als"
    wav = tmp_path / "c.wav"
    rpt = tmp_path / "c.json"
    clips = _single_track_clips(seconds)
    _write_als(als, clips)
    def silence(t, L, R):
        L = L.copy(); R = R.copy()
        m = (t >= 5) & (t < 6)
        L[m] = 0.0; R[m] = 0.0
        return L, R
    _synth_render(wav, seconds, clips=clips, extra_process=silence, level_db=-60.0)
    _write_report(rpt)
    res = render_check.run_check(wav, rpt, als)
    assert res.exit_code == 2
    assert res.verdict == "FAIL"


def test_grid_fold_drift(tmp_path):
    """Kicks on the grid in region 1, shifted +55 ms in region 2 -> FAIL;
    control with a consistent grid -> no FAIL (constant bias is fine)."""
    bpm = 128.0
    seconds = 145.0
    bps = bpm / 60.0
    # Two solo regions >= 45 s separated by a 2-clip overlap, so
    # _pick_solo_regions has two distinct probe windows.
    clips = [
        {"track": "A", "arr_start": 0, "arr_end": round(70 * bps),
         "loop_on": False},
        {"track": "B", "arr_start": round(65 * bps),
         "arr_end": round(seconds * bps), "loop_on": False},
    ]

    def kicks(shift_late):
        def add(t, L, R):
            L = L.copy(); R = R.copy()
            sr = 44100
            n = len(L)
            for b in np.arange(0, seconds, 60.0 / bpm):
                bt = b + (shift_late if b >= 75.0 else 0.0)
                i0 = int(round(bt * sr))
                dur = int(round(0.05 * sr))
                if i0 + dur > n:
                    break
                env = np.exp(-np.arange(dur) / (sr * 0.01))
                kick = env * np.sin(2 * math.pi * 55.0 * np.arange(dur) / sr)
                L[i0:i0 + dur] += kick
                R[i0:i0 + dur] += kick
            return L, R
        return add

    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    _write_als(als, clips)
    _synth_render(wav, seconds, clips=clips, with_kicks=False,
                  extra_process=kicks(0.055))
    _write_report(rpt)
    res = render_check.run_check(wav, rpt, als)
    folds = [f for f in res.findings if f.check == "grid_fold"]
    assert any(f.level == "FAIL" for f in folds), \
        [(f.level, f.measured) for f in folds]

    # Control: consistent grid -> no FAIL (INFO only).
    wav_b = tmp_path / "ok.wav"
    _synth_render(wav_b, seconds, clips=clips, with_kicks=False,
                  extra_process=kicks(0.0))
    res_b = render_check.run_check(wav_b, rpt, als)
    folds_b = [f for f in res_b.findings if f.check == "grid_fold"]
    assert folds_b and all(f.level == "INFO" for f in folds_b), \
        [(f.level, f.measured) for f in folds_b]


# --------------------------------------------------------------------------- #
# Tempo-map mapping and fail-closed envelope handling                         #
# --------------------------------------------------------------------------- #

def test_tempo_automation_ramped_maps_and_runs(tmp_path):
    """A finite, in-range ramp is mapped and the audio checks run."""
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    seconds = 30.0
    clips = _single_track_clips(seconds)
    _write_als(als, clips, bpm=128.0,
               tempo_envelope=[(0.0, 128.0), (64.0, 130.0)])
    _synth_render(wav, seconds, clips=clips)
    _write_report(rpt)
    res = render_check.run_check(wav, rpt, als)
    assert res.exit_code == 0
    assert res.verdict == "PASS"
    assert res.meta["tempo_map"] == {
        "n_points": 2, "min": 128.0, "max": 130.0, "is_flat": False,
    }
    assert not any(f.check == "tempo_automation_unsupported"
                   for f in res.findings)


def test_tempo_automation_flat_envelope_passes(tmp_path):
    """Flat envelope (two FloatEvents both at Manual) must NOT trip the
    guard. V10 ALS has this exact shape; pin it as the negative control."""
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    seconds = 30.0
    clips = _single_track_clips(seconds)
    _write_als(als, clips, bpm=128.0, tempo_envelope=[128.0, 128.0])
    _synth_render(wav, seconds, clips=clips)
    _write_report(rpt)
    res = render_check.run_check(wav, rpt, als)
    assert res.exit_code == 0
    assert res.verdict == "PASS"
    assert not any(f.check == "tempo_automation_unsupported"
                   for f in res.findings)


def test_tempo_automation_mismatch_envelope_wins(tmp_path):
    """Flat envelope at a DIFFERENT value than Manual: the envelope overrides
    Manual at playback, so it is the map source and is no longer an error."""
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    seconds = 30.0
    clips = _single_track_clips(seconds)
    _write_als(als, clips, bpm=128.0, tempo_envelope=[130.0])
    _synth_render(wav, seconds, clips=clips)
    _write_report(rpt)
    res = render_check.run_check(wav, rpt, als)
    assert res.exit_code == 0
    assert res.meta["bpm"] == 128.0
    assert res.meta["tempo_map"] == {
        "n_points": 1, "min": 130.0, "max": 130.0, "is_flat": True,
    }
    assert not any(f.check == "tempo_automation_unsupported"
                   for f in res.findings)


def test_tempo_automation_raises_library():
    """An unmappable envelope still raises at the parser boundary."""
    root_xml = _als_xml(_single_track_clips(30), bpm=128.0,
                        tempo_envelope=[(float("nan"), 128.0)])
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".als", delete=False) as fh:
            fh.write(root_xml)
            tmp_path = Path(fh.name)
        with pytest.raises(render_check.TempoAutomationUnsupported):
            render_check.parse_als(tmp_path)
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass


def test_tempo_map_exact_log_inverse_holds_and_cache():
    root_xml = _als_xml(
        _single_track_clips(30), bpm=128.0,
        tempo_envelope=[(4.0, 120.0), (12.0, 132.0)],
    )
    import xml.etree.ElementTree as ET
    root = ET.fromstring(gzip.decompress(root_xml))
    tempo_map = render_check.TempoMap.from_als_root(root)

    ramp_sec = (60.0 / 1.5) * math.log(132.0 / 120.0)
    assert tempo_map.beat_to_sec(4.0) == pytest.approx(2.0)
    assert tempo_map.beat_to_sec(12.0) == pytest.approx(2.0 + ramp_sec)
    assert tempo_map.beat_to_sec(16.0) == pytest.approx(
        2.0 + ramp_sec + 4.0 * 60.0 / 132.0)
    beats = np.array([-2.0, 0.0, 4.0, 7.5, 12.0, 20.0])
    assert np.allclose(tempo_map.sec_to_beat(tempo_map.beat_to_sec(beats)),
                       beats, atol=1e-12)
    assert tempo_map.bpm_at(-1.0) == 120.0
    assert tempo_map.bpm_at(8.0) == 126.0
    assert tempo_map.bpm_at(20.0) == 132.0
    assert tempo_map.beat_edges_sec(24) is tempo_map.beat_edges_sec(24)


def test_tempo_map_folds_live_sentinel_to_zero():
    root_xml = _als_xml(
        _single_track_clips(30), bpm=128.0,
        tempo_envelope=[(-63072000.0, 120.0), (0.0, 120.0),
                        (8.0, 124.0)],
    )
    import xml.etree.ElementTree as ET
    root = ET.fromstring(gzip.decompress(root_xml))
    tempo_map = render_check.TempoMap.from_als_root(root)
    assert tempo_map.summary["n_points"] == 2
    assert tempo_map.beat_to_sec(0.0) == 0.0
    assert tempo_map.beat_to_sec(8.0) == pytest.approx(
        (60.0 / 0.5) * math.log(124.0 / 120.0))


@pytest.mark.parametrize("tempo_envelope", [
    [],
    [(0.0, 20.0)],
    [(0.0, 300.0)],
    [(0.0, float("nan"))],
    [(float("inf"), 128.0)],
])
def test_tempo_map_invalid_envelope_fails_closed(tmp_path, tempo_envelope):
    als = tmp_path / "bad.als"
    _write_als(als, _single_track_clips(30), bpm=128.0,
               tempo_envelope=tempo_envelope)
    with pytest.raises(render_check.TempoAutomationUnsupported):
        render_check.parse_als(als)


@pytest.mark.parametrize("manual", [20.0, 300.0, float("nan")])
def test_tempo_map_invalid_manual_fails_closed(tmp_path, manual):
    als = tmp_path / "bad-manual.als"
    _write_als(als, _single_track_clips(30), bpm=manual,
               tempo_envelope=[(0.0, 128.0)])
    with pytest.raises(render_check.TempoAutomationUnsupported):
        render_check.parse_als(als)


# --------------------------------------------------------------------------- #
# FIX 2 - crash = exit 2 (CLI subprocess)                                     #
# --------------------------------------------------------------------------- #

def test_cli_missing_als_exits_2(tmp_path):
    """A missing ALS file is an operational exception. The CLI must catch
    it and exit 2 (FAIL); without FIX 2, Python exits with 1, which mix.md
    defines as non-blocking WARN - a gate that could not run must not
    read as WARN."""
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    missing_als = tmp_path / "does_not_exist.als"
    _synth_render(wav, 30.0, clips=_single_track_clips(30.0))
    _write_report(rpt)
    script = ROOT / "Source" / "render_check.py"
    proc = subprocess.run(
        [sys.executable, str(script), str(wav), str(rpt), str(missing_als)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 2, (proc.stdout, proc.stderr, proc.returncode)
    assert "gate-error" in proc.stdout
    assert "verdict=FAIL" in proc.stdout


def test_cli_malformed_report_exits_2(tmp_path):
    """A malformed report JSON is an operational exception. CLI exits 2."""
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "m.json"
    als = tmp_path / "m.als"
    rpt.write_text("not json{", encoding="utf-8")
    _synth_render(wav, 30.0, clips=_single_track_clips(30.0))
    _write_als(als, _single_track_clips(30.0))
    script = ROOT / "Source" / "render_check.py"
    proc = subprocess.run(
        [sys.executable, str(script), str(wav), str(rpt), str(als)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 2, (proc.stdout, proc.stderr, proc.returncode)
    assert "gate-error" in proc.stdout


def test_cli_missing_render_exits_2(tmp_path):
    """A missing render WAV is an operational exception. CLI exits 2."""
    rpt = tmp_path / "r.json"
    als = tmp_path / "m.als"
    missing_wav = tmp_path / "does_not_exist.wav"
    _write_report(rpt)
    _write_als(als, _single_track_clips(30.0))
    script = ROOT / "Source" / "render_check.py"
    proc = subprocess.run(
        [sys.executable, str(script), str(missing_wav), str(rpt), str(als)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 2, (proc.stdout, proc.stderr, proc.returncode)
    assert "gate-error" in proc.stdout


# --------------------------------------------------------------------------- #
# FIX 3 - truncated render FAIL                                               #
# --------------------------------------------------------------------------- #

def test_render_truncated_fails(tmp_path):
    """ALS arr ~30 s (64 beats @ 128 bpm) but render only ~20 s -> FAIL."""
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    bpm = 128.0
    arr_seconds = 30.0
    render_seconds = 20.0
    beats_total = round(arr_seconds * bpm / 60.0)  # 64
    clips = [{
        "track": "Tone", "arr_start": 0,
        "arr_end": beats_total, "loop_on": False,
    }]
    _write_als(als, clips)
    _synth_render(wav, render_seconds, clips=clips)
    _write_report(rpt)
    res = render_check.run_check(wav, rpt, als)
    truncated = [f for f in res.findings if f.check == "render_truncated"]
    assert truncated, [(f.check, f.level) for f in res.findings]
    assert all(f.level == "FAIL" for f in truncated)
    assert res.exit_code == 2
    shortfall = truncated[0].measured["shortfall_sec"]
    # 30 - 20 = 10 s.
    assert abs(shortfall - 10.0) < 1.0, shortfall

    # Control: render matches arrangement (+ tail) -> no render_truncated.
    wav_full = tmp_path / "full.wav"
    rpt_full = tmp_path / "r_full.json"
    _synth_render(wav_full, arr_seconds + 0.5, clips=clips)
    _write_report(rpt_full)
    res_full = render_check.run_check(wav_full, rpt_full, als)
    assert not any(f.check == "render_truncated" for f in res_full.findings)


def test_render_truncated_produces_eof_reads_with_loop(tmp_path):
    """When a loop iteration extends past EOF, eof_truncated_reads FAILs too.
    The duration FAIL is the load-bearing assertion; this just confirms the
    EOF path fires."""
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    bpm = 128.0
    arr_seconds = 30.0
    render_seconds = 12.0  # well before arrangement end -> loop iter past EOF
    beats_total = round(arr_seconds * bpm / 60.0)
    clips = [{
        "track": "Tone", "arr_start": 0,
        "arr_end": beats_total, "loop_on": False,
    }]
    _write_als(als, clips)
    # Loop insert at beat 16 with 16-beat iterations -> iter 2 spans beats
    # 48..64, but the render is only 12 s (~25.6 beats), so iter 2 reads
    # start past EOF.
    loops = [{
        "track": "Tone", "type": "tail",
        "source_beats": "100-116",
        "count": 4, "total_beats": 64.0,
        "insert_at_beat": 16,
    }]
    _synth_render(wav, render_seconds, clips=clips)
    _write_report(rpt, loops=loops)
    res = render_check.run_check(wav, rpt, als)
    eofs = [f for f in res.findings if f.check == "eof_truncated_reads"]
    assert eofs, [(f.check, f.level) for f in res.findings]
    assert all(f.level == "FAIL" for f in eofs)
    # Confirm render_truncated is also reported.
    assert any(f.check == "render_truncated" for f in res.findings)
    assert res.exit_code == 2


# --------------------------------------------------------------------------- #
# FIX 4 - _pick_solo_regions dedup                                            #
# --------------------------------------------------------------------------- #

def test_pick_solo_regions_dedupes_with_one_run():
    """One eligible solo run must return exactly one region. The pre-fix
    bug returned the same run three times, masking drift in single-run
    arrangements (V10 had this exact shape)."""
    # Three target percentages, exactly one eligible solo run. Solo runs
    # of 23 s are below the 45 s GRID_FOLD_REGION_S threshold, so only the
    # long E tail is eligible.
    clips_one = [
        {"track": "A", "arr_start": 0, "arr_end": 50, "loop_on": False},
        {"track": "B", "arr_start": 50, "arr_end": 100, "loop_on": False},
        {"track": "C", "arr_start": 100, "arr_end": 150, "loop_on": False},
        {"track": "D", "arr_start": 150, "arr_end": 200, "loop_on": False},
        {"track": "E", "arr_start": 200, "arr_end": 300, "loop_on": False},
    ]
    tempo_map = render_check.TempoMap.flat(128.0)
    regions_one = render_check._pick_solo_regions(clips_one, tempo_map,
                                                  [0.15, 0.5, 0.85])
    assert len(regions_one) == 1, regions_one
    # The single region is E alone from 200..300 beats = 93.75..140.625 sec.
    expected = (200 * 60.0 / 128.0, 300 * 60.0 / 128.0)
    assert regions_one[0] == expected

    # Two back-to-back clips each >= 45 s solo -> two distinct regions,
    # third target skipped (no duplicate region possible).
    clips_two = [
        {"track": "A", "arr_start": 0, "arr_end": 100, "loop_on": False},
        {"track": "B", "arr_start": 100, "arr_end": 200, "loop_on": False},
    ]
    regions_two = render_check._pick_solo_regions(clips_two, tempo_map,
                                                  [0.15, 0.5, 0.85])
    assert len(regions_two) == 2, regions_two
    assert regions_two[0] != regions_two[1]


# --------------------------------------------------------------------------- #
# FIX 5 - missing report keys                                                 #
# --------------------------------------------------------------------------- #

def test_report_empty_dict_exits_2(tmp_path):
    """Report {} (no loops key, no transitions key): both report_missing_*
    SKIPs fire AND report_empty FAIL fires; gate exits 2."""
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    seconds = 30.0
    clips = _single_track_clips(seconds)
    _write_als(als, clips)
    _synth_render(wav, seconds, clips=clips)
    rpt.write_text("{}", encoding="utf-8")
    res = render_check.run_check(wav, rpt, als)
    checks = {f.check: f for f in res.findings}
    assert "report_missing_loops" in checks
    assert checks["report_missing_loops"].level == "SKIP"
    assert "report_missing_transitions" in checks
    assert checks["report_missing_transitions"].level == "SKIP"
    assert "report_empty" in checks
    assert checks["report_empty"].level == "FAIL"
    assert res.exit_code == 2
    assert res.verdict == "FAIL"


def test_report_loops_only_skips_transitions_clean_pass(tmp_path):
    """Report {"loops": []}: transitions is missing (SKIP), no report_empty,
    no FAIL -> clean render passes (SKIP does not affect verdict)."""
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    seconds = 30.0
    clips = _single_track_clips(seconds)
    _write_als(als, clips)
    _synth_render(wav, seconds, clips=clips)
    rpt.write_text(json.dumps({"loops": []}), encoding="utf-8")
    res = render_check.run_check(wav, rpt, als)
    checks = {f.check: f for f in res.findings}
    assert "report_missing_transitions" in checks
    assert checks["report_missing_transitions"].level == "SKIP"
    assert "report_missing_loops" not in checks
    assert "report_empty" not in checks
    assert not any(f.level == "FAIL" for f in res.findings)
    assert res.exit_code == 0
    assert res.verdict == "PASS"


def test_report_both_keys_no_skip_findings(tmp_path):
    """Existing fixtures (both keys present, possibly empty): no SKIP
    findings. Asserts the no-missing path stays clean."""
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    seconds = 30.0
    clips = _single_track_clips(seconds)
    _write_als(als, clips)
    _synth_render(wav, seconds, clips=clips)
    _write_report(rpt)  # both keys, both empty
    res = render_check.run_check(wav, rpt, als)
    assert not any(f.level == "SKIP" for f in res.findings)
    assert not any(f.check.startswith("report_missing_")
                   for f in res.findings)
    assert not any(f.check == "report_empty" for f in res.findings)


# --------------------------------------------------------------------------- #
# V10 corpus regression pin                                                   #
# --------------------------------------------------------------------------- #

CORPUS_ROOT = Path(os.environ.get("DJ_MIX_TEST_PROJECT",
                                  str(ROOT / "Test Project")))

V10_WAV = (CORPUS_ROOT / "14.08.26" / "Output"
           / "14.08.26 Mix V10.wav")
V10_ALS = (CORPUS_ROOT / "14.08.26" / "Output"
           / "14.08.26 Mix V10.als")
V10_REPORT = (CORPUS_ROOT / "14.08.26" / "Output"
              / "ARRANGEMENT_REPORT_V10.json")


@pytest.mark.skipif(
    not (V10_WAV.exists() and V10_ALS.exists() and V10_REPORT.exists()),
    reason="V10 corpus not present (gitignored)",
)
def test_v10_regression_pin():
    """The control sample the gate was calibrated on: no FAIL findings, exit
    code 1 (warnings only), and the exact defect set the V10 review located.
    See Documentation/Reviews/2026-08-20 First Render Check - Mix V10.md."""
    res = render_check.run_check(V10_WAV, V10_REPORT, V10_ALS)
    assert res.exit_code == 1, (
        f"V10 should exit 1 (warnings); got {res.exit_code}: "
        + "; ".join(f"{f.check}/{f.level}" for f in res.findings)
    )
    assert not any(f.level == "FAIL" for f in res.findings), (
        "V10 should have no FAIL findings: "
        + "; ".join(f"{f.check}/{f.level}" for f in res.findings)
    )

    def find(check, level="WARN"):
        return [f for f in res.findings if f.check == check and f.level == level]

    def near(check, t_center, tol=1.0):
        return [f for f in find(check)
                if abs((f.t0 + f.t1) / 2 - t_center) < tol]

    # Level cliffs at the three T-swap loop inserts (4:43.1, 20:15, 49:54.4).
    assert near("level_cliff", 283.1)
    assert near("level_cliff", 1215.0)
    assert near("level_cliff", 2994.4)

    # Loop period: Nappp 12-beat (24:24 area), and L1/L6 28-beat.
    nappp = [f for f in find("loop_period")
             if abs(f.measured.get("iter_len", 0) - 12) < 1e-6]
    assert any(abs(f.t0 - 1464.4) < 1.0 for f in nappp), \
        [(f.t0, f.measured) for f in nappp]
    bar28 = [f for f in find("loop_period")
             if abs(f.measured.get("iter_len", 0) - 28) < 1e-6]
    assert any(abs(f.t0 - 283.1) < 1.0 for f in bar28)
    assert any(abs(f.t0 - 2994.4) < 1.0 for f in bar28)

    # Exposed solo at 22:15.5-22:21.5 (+/-1 s each end), and nothing else.
    solos = find("exposed_solo")
    assert len(solos) == 1, [(f.t0, f.t1) for f in solos]
    assert abs(solos[0].t0 - 1335.5) < 1.0 and abs(solos[0].t1 - 1341.5) < 1.0

    # Loop hole overlapping 41:52.5-42:15.0.
    assert any(2512.5 <= f.t0 and f.t1 <= 2535.0 for f in find("loop_hole"))

    # Loop exit jumps at 20:22.5, 42:15.0, 50:20.6.
    assert near("loop_exit_jump", 1222.5)
    assert near("loop_exit_jump", 2535.0)
    assert near("loop_exit_jump", 3020.6)

    # Transition dips: exactly the prototype's firing set {T1, T4, T5, T11}
    # (T2/T8/T9 measured under 2 dB, T10 1.9 -- must stay silent).
    pairs = {f.measured.get("pair_index") for f in find("transition_dip")}
    assert pairs == {1, 4, 5, 11}, pairs
    assert 2 not in pairs and 9 not in pairs

    # ---- FIX 6 decimal pin (tolerances: t +/-1 s, dB +/-0.2, r +/-0.02,
    # durations +/-0.5 s). Documented in
    # Documentation/Reviews/2026-08-20 First Render Check - Mix V10.md and
    # the calibration comments in render_check.py. Re-running the V10
    # capture script (_capture_v10.py) refreshes these numbers.

    # Level cliffs: three steps, in order by t0.
    cliffs = sorted([f for f in find("level_cliff")],
                    key=lambda f: f.t0)
    cliff_steps = [(c.t0, c.measured["step_db"]) for c in cliffs]
    assert len(cliff_steps) >= 3, cliff_steps
    # Step 1 at 283.1 s -> -6.9 dB (from -17.7 to -24.5).
    assert abs(cliff_steps[0][0] - 283.1) < 1.0, cliff_steps[0]
    assert abs(cliff_steps[0][1] - (-6.9)) < 0.2, cliff_steps[0]
    assert abs(cliffs[0].measured["from_db"] - (-17.7)) < 0.2, cliffs[0].measured
    assert abs(cliffs[0].measured["to_db"] - (-24.5)) < 0.2, cliffs[0].measured
    # Step 2 at 1215.0 s -> -8.9 dB (from -17.2 to -26.1).
    assert abs(cliff_steps[1][0] - 1215.0) < 1.0, cliff_steps[1]
    assert abs(cliff_steps[1][1] - (-8.9)) < 0.2, cliff_steps[1]
    assert abs(cliffs[1].measured["from_db"] - (-17.2)) < 0.2, cliffs[1].measured
    assert abs(cliffs[1].measured["to_db"] - (-26.1)) < 0.2, cliffs[1].measured
    # Step 3 at 2994.4 s -> -7.6 dB (from -18.1 to -25.6).
    assert abs(cliff_steps[2][0] - 2994.4) < 1.0, cliff_steps[2]
    assert abs(cliff_steps[2][1] - (-7.6)) < 0.2, cliff_steps[2]
    assert abs(cliffs[2].measured["from_db"] - (-18.1)) < 0.2, cliffs[2].measured
    assert abs(cliffs[2].measured["to_db"] - (-25.6)) < 0.2, cliffs[2].measured

    # Loop period Nappp at ~1464.4: iter_len 12, r ~= 1.00.
    nappp = [f for f in find("loop_period")
             if abs(f.measured.get("iter_len", 0) - 12) < 1e-6]
    assert any(abs(f.t0 - 1464.4) < 1.0 for f in nappp), \
        [(f.t0, f.measured) for f in nappp]
    nappp_at = [f for f in nappp if abs(f.t0 - 1464.4) < 1.0][0]
    assert abs(nappp_at.measured["iter_len"] - 12) < 1e-6, nappp_at.measured
    assert abs(nappp_at.measured["r"] - 1.00) < 0.02, nappp_at.measured

    # Exposed solo at ~1335.5..1341.5, floor -46.9, duration ~6 s.
    # NOTE: 2026-08-25 capture measures -46.93 dBFS; the review doc had
    # -46.7, a 0.23 dB drift from re-render -- tolerance widened to 0.3 dB
    # to capture the actual measurement rather than pin a stale number.
    assert len(solos) == 1
    assert abs(solos[0].t0 - 1335.5) < 1.0
    assert abs(solos[0].t1 - 1341.5) < 1.0
    assert abs(solos[0].measured["floor_db"] - (-46.9)) < 0.3, solos[0].measured
    assert abs(solos[0].measured["duration_s"] - 6.0) < 0.5, solos[0].measured

    # Loop hole Vente tail (2512.5..2535.0): spread 7.3 dB.
    vente = [f for f in find("loop_hole")
             if 2512.5 <= f.t0 and f.t1 <= 2535.0]
    assert vente, [(f.t0, f.t1) for f in find("loop_hole")]
    assert any(abs(f.measured["spread_db"] - 7.3) < 0.2 for f in vente), \
        [(f.measured.get("spread_db")) for f in vente]

    # Loop exit jumps at 1222.5, 2535.0, 3020.6 -> documented as the range
    # +4.3 to +4.7 dB. Pin each exact decimal (within +/-0.05 dB) so a
    # regression that drifts the jump but keeps it "inside the range"
    # cannot pass. NOTE: 2026-08-25 capture measures 4.73 at 2535.0, 0.03 dB
    # above the documented +4.7 ceiling; the documented value is the
    # coarse rounded range from the review, the measured decimals drift
    # slightly outside it.
    jumps = sorted([f for f in find("loop_exit_jump")],
                   key=lambda f: f.t0)
    assert len(jumps) == 3, [(f.t0, f.measured) for f in jumps]
    # V10 measured decimals (captured 2026-08-25):
    expected_jumps = [
        (1222.5, 4.55),
        (2535.0, 4.73),
        (3020.6, 4.34),
    ]
    for (got, measured), (exp_t, exp_db) in zip(
            [(f.t0, f.measured["step_db"]) for f in jumps],
            expected_jumps):
        assert abs(got - exp_t) < 1.0, (got, exp_t)
        assert abs(measured - exp_db) < 0.05, (measured, exp_db)
        # All three are within ~0.05 dB of the documented +4.3..+4.7 range
        # (4.73 is 0.03 above the ceiling - rounding from the coarse doc).
        assert 4.3 - 0.05 <= measured <= 4.7 + 0.05, measured

    # Transition dip_db under THIS gate's windowing convention:
    # T1 4.6, T4 5.8, T5 2.8, T11 5.9. Keyed by pair_index.
    expected_dips = {1: 4.6, 4: 5.8, 5: 2.8, 11: 5.9}
    got_dips = {f.measured["pair_index"]: f.measured["dip_db"]
                for f in find("transition_dip")}
    assert set(got_dips.keys()) == set(expected_dips.keys()), got_dips
    for pair, exp_db in expected_dips.items():
        assert abs(got_dips[pair] - exp_db) < 0.2, (pair, got_dips[pair])

    # Integrated LUFS ~ -16.6.
    assert abs(res.meta["integrated_lufs"] - (-16.6)) < 0.2, \
        res.meta["integrated_lufs"]

    # Pin: no FAIL findings, no SKIP findings (V10 report has both keys).
    assert not any(f.level == "FAIL" for f in res.findings), \
        "V10 should have no FAIL findings: " + "; ".join(
            f"{f.check}/{f.level}" for f in res.findings
        )
    assert not any(f.level == "SKIP" for f in res.findings), \
        "V10 should have no SKIP findings: " + "; ".join(
            f"{f.check}/{f.level}" for f in res.findings
        )
    assert res.exit_code == 1, (
        f"V10 should exit 1 (warnings); got {res.exit_code}: "
        + "; ".join(f"{f.check}/{f.level}" for f in res.findings)
    )


# --------------------------------------------------------------------------- #
# V16 tempo-arc corpus pins                                                   #
# --------------------------------------------------------------------------- #

V16_ALS = (CORPUS_ROOT / "14.08.26" / "Output"
           / "14.08.26 Mix V16.als")


@pytest.mark.skipif(not V16_ALS.exists(),
                    reason="V16 ALS corpus not present (gitignored)")
def test_v16_tempo_map_measured_truth():
    import xml.etree.ElementTree as ET
    with gzip.open(V16_ALS, "rb") as fh:
        root = ET.fromstring(fh.read())
    tempo_map = render_check.TempoMap.from_als_root(root)
    mapped_end = tempo_map.beat_to_sec(10524.0)
    assert mapped_end == pytest.approx(5031.098, abs=0.0005)
    assert abs(mapped_end - 5031.3018) < 0.250
    assert tempo_map.summary == {
        "n_points": 31,
        "min": 121.41032298384611,
        "max": 129.52242915838903,
        "is_flat": False,
    }


# --------------------------------------------------------------------------- #
# Tempo-arc guards: two checks whose tolerances the map residual invalidates   #
# --------------------------------------------------------------------------- #

def test_boundary_click_skipped_on_tempo_arc(tmp_path):
    """A real click at a boundary FAILs under a flat map and is SKIPPED - by
    name - under an arc map.

    boundary_click reads a +/-2 ms window around each mapped boundary. The
    tempo map carries a measured ~19.5 ppm residual, so on a long arc render
    that window is nowhere near the true splice and the check reports clean:
    a false PASS on the one defect class it exists for. It must announce that
    it did not look, never quietly pass.
    """
    bpm = 128.0
    seconds = 30.0
    bps = bpm / 60.0
    boundary_beat = 10
    clips = [
        {"track": "A", "arr_start": 0, "arr_end": boundary_beat,
         "loop_on": False},
        {"track": "B", "arr_start": boundary_beat,
         "arr_end": round(seconds * bps), "loop_on": False},
    ]

    import xml.etree.ElementTree as ET
    import gzip as _gzip
    rpt = tmp_path / "arc.json"
    _write_report(rpt)

    def run_with(als_path, tag):
        """Place the click at the boundary time THIS map predicts.

        Codex caught the earlier version doing otherwise: it injected at the
        flat map's beat-10 time and then asked the arc map to find it, 5.7 ms
        away. That tested map disagreement, not the skip logic.
        """
        root = ET.fromstring(_gzip.open(als_path, "rb").read())
        tmap = render_check.TempoMap.from_als_root(root)
        t_b = tmap.beat_to_sec(float(boundary_beat))

        def click(t, L, R):
            L = L.copy(); R = R.copy()
            idx = int(round(t_b * 44100))
            L[idx] = L[idx] + 0.7
            R[idx] = R[idx] + 0.7
            return L, R

        wav = tmp_path / f"{tag}.wav"
        _synth_render(wav, seconds, clips=clips, extra_process=click)
        return render_check.run_check(wav, rpt, als_path), tmap

    # Flat map: the click is caught. This is the prove-the-test half - without
    # it, the SKIP below could be hiding a check that never worked.
    als_flat = tmp_path / "flat.als"
    _write_als(als_flat, clips)
    res_flat, tmap_flat = run_with(als_flat, "flat")
    assert tmap_flat.is_flat
    assert any(f.check == "boundary_click" and f.level == "FAIL"
               for f in res_flat.findings), \
        [(f.check, f.level) for f in res_flat.findings]
    assert not any(f.check == "boundary_click_skipped_tempo_arc"
                   for f in res_flat.findings)

    # Arc map: NO boundary is inspected, and the skip says so by name. An
    # earlier version inspected "early" boundaries on the grounds that the
    # 19.5 ppm mean bias stayed under half the window; Codex refuted it - the
    # scatter around that fit is 5.3 ms RMS, already wider than the whole
    # +/-2 ms window, so no boundary on an arc can be called trusted until the
    # map's uncertainty is characterised.
    als_arc = tmp_path / "arc.als"
    _write_als(als_arc, clips, bpm=bpm,
               tempo_envelope=[(0.0, 128.0), (64.0, 130.0)])
    res_arc, tmap_arc = run_with(als_arc, "arc")
    assert not tmap_arc.is_flat, "fixture must be an arc"
    skips = [f for f in res_arc.findings
             if f.check == "boundary_click_skipped_tempo_arc"]
    assert len(skips) == 1, [(f.check, f.level) for f in res_arc.findings]
    assert skips[0].measured["inspected_boundaries"] == 0
    assert not any(f.check == "boundary_click" for f in res_arc.findings), \
        "no boundary_click verdict may be reported against an arc map"


def test_boundary_click_skips_only_late_boundaries_on_an_arc(tmp_path):
    """Late boundaries ARE skipped, by name, and the report must not then
    call boundary_click clean.

    The residual grows with elapsed time, so past roughly 51 s the +/-2 ms
    window no longer contains the true splice. Those boundaries are announced
    as skipped; earlier ones in the same render are still inspected.
    """
    bpm = 120.0
    bps = bpm / 60.0
    seconds = 130.0
    early_beat = 8              # ~4 s   -> inspected
    late_beat = round(100 * bps)  # ~100 s -> skipped
    clips = [
        {"track": "A", "arr_start": 0, "arr_end": early_beat,
         "loop_on": False},
        {"track": "B", "arr_start": early_beat, "arr_end": late_beat,
         "loop_on": False},
        {"track": "C", "arr_start": late_beat,
         "arr_end": round(seconds * bps), "loop_on": False},
    ]
    wav = tmp_path / "long.wav"
    rpt = tmp_path / "long.json"
    _synth_render(wav, seconds, clips=clips)
    _write_report(rpt)
    als = tmp_path / "long.als"
    _write_als(als, clips, bpm=bpm,
               tempo_envelope=[(0.0, 120.0), (128.0, 124.0)])

    res = render_check.run_check(wav, rpt, als)
    skips = [f for f in res.findings
             if f.check == "boundary_click_skipped_tempo_arc"]
    assert len(skips) == 1, [(f.check, f.level) for f in res.findings]
    assert skips[0].level == "SKIP"
    assert skips[0].measured["skipped_boundaries"] >= 1
    assert skips[0].measured["skipped_check"] == "boundary_click"

    # The report must not list a skipped check as clean. Without this the
    # operator is told the very check that did not run came back clean, which
    # defeats the only safeguard the skip provides (Codex review 2026-08-27).
    md_path, _ = render_check.write_report(res, wav,
                                           tmp_path / "out.json")
    clean_section = md_path.read_text(
        encoding="utf-8").split("## Checks run clean")[-1]
    assert "boundary_click" not in clean_section, clean_section
    # ... and the skip itself must still be visible in the findings table.
    assert "boundary_click_skipped_tempo_arc" in md_path.read_text(
        encoding="utf-8")


def test_grid_fold_drift_warns_not_fails_on_tempo_arc(tmp_path):
    """Excess grid-fold drift FAILs under a flat map and WARNs under an arc.

    The 30 ms threshold was calibrated on a flat render. On an arc it is
    tripped by two measured confounds that are not render defects (the map
    residual, and probes landing on different tracks), so it must keep
    reporting the number without failing the render on it.
    """
    seconds = 145.0
    bpm = 120.0
    bps = bpm / 60.0
    # Two solo regions >= 45 s separated by a 2-clip overlap, so
    # _pick_solo_regions has two distinct probe windows. A single clip yields
    # ONE region and check_grid_fold returns its "fewer than 2 regions" INFO
    # without ever reaching the drift logic - the test would prove nothing.
    clips = [
        {"track": "A", "arr_start": 0, "arr_end": round(70 * bps),
         "loop_on": False},
        {"track": "B", "arr_start": round(65 * bps),
         "arr_end": round(seconds * bps), "loop_on": False},
    ]
    wav = tmp_path / "d.wav"
    rpt = tmp_path / "d.json"
    _synth_render(wav, seconds, clips=clips)
    _write_report(rpt)

    als_flat = tmp_path / "flat.als"
    _write_als(als_flat, clips, bpm=bpm)
    als_arc = tmp_path / "arc.als"
    _write_als(als_arc, clips, bpm=bpm,
               tempo_envelope=[(0.0, 120.0), (128.0, 124.0)])

    import xml.etree.ElementTree as ET
    import gzip as _gzip

    def grid_fold_level(als_path):
        root = ET.fromstring(_gzip.open(als_path, "rb").read())
        tmap = render_check.TempoMap.from_als_root(root)
        # Drift just OVER the flat threshold, identically for both maps, so
        # the only difference between the two calls is map flatness. It has to
        # be inside the residual allowance - past that an arc FAILs too, which
        # is the separate bounded-downgrade contract tested below.
        real = render_check._grid_fold_median
        seq = iter([0.0, 31.0])
        render_check._grid_fold_median = lambda *a, **k: next(seq)
        try:
            out = render_check.check_grid_fold(wav, clips, tmap)
        finally:
            render_check._grid_fold_median = real
        return [(f.check, f.level, f.measured.get("drift_ms")) for f in out]

    flat = grid_fold_level(als_flat)
    arc = grid_fold_level(als_arc)
    flat_levels = {lvl for c, lvl, _ in flat if c == "grid_fold"}
    arc_levels = {lvl for c, lvl, _ in arc if c == "grid_fold"}
    # Unconditional. An earlier version gated the decisive assertions behind
    # `if flat_levels == {"FAIL"}`, so a regression in the flat path let the
    # whole test pass without checking anything (Codex review 2026-08-27) -
    # exactly the vacuous-pass shape this suite exists to prevent.
    assert flat_levels == {"FAIL"}, flat
    assert arc_levels == {"WARN"}, (flat, arc)
    arc_drift = [d for c, _, d in arc if c == "grid_fold"][0]
    assert arc_drift == pytest.approx(31.0), arc


def test_map_vs_render_catches_a_wrong_map(tmp_path):
    """The constant-free defence against a grossly wrong map.

    grid_fold cannot gate on an arc, so this is what stops a mistimed render
    shipping. It compares where the map says the arrangement ends against
    where the audio actually stops - no fitted quantity involved.

    Prove-the-test: the SAME render passes under the correct arc map and FAILs
    under a flat one, so a pass cannot be an artefact of a slack tolerance.
    """
    bpm = 120.0
    arr_end_beats = 480.0
    # 120 -> 170 BPM across the arrangement. Flat calls this 240 s; the arc
    # integral makes it ~200.6 s, so a flat map is ~39 s out - past the
    # minutes-scale FAIL floor, which is deliberately set above anything a
    # fade or reverb tail could produce.
    clips = [{"track": "A", "arr_start": 0, "arr_end": int(arr_end_beats),
              "loop_on": False}]
    als_arc = tmp_path / "mvr.als"
    _write_als(als_arc, clips, bpm=bpm,
               tempo_envelope=[(0.0, 120.0), (arr_end_beats, 170.0)])

    import xml.etree.ElementTree as ET
    import gzip as _gzip
    root = ET.fromstring(_gzip.open(als_arc, "rb").read())
    arc_map = render_check.TempoMap.from_als_root(root)
    assert not arc_map.is_flat
    true_end = arc_map.beat_to_sec(arr_end_beats)
    flat_map = render_check.TempoMap.flat(bpm)
    assert flat_map.beat_to_sec(arr_end_beats) - true_end > \
        render_check.MAP_ENDPOINT_FAIL_ABS_SEC, \
        "fixture must separate the two maps by more than the FAIL floor"

    wav = tmp_path / "mvr.wav"
    _synth_render(wav, true_end, clips=clips)
    sweep = render_check.streaming_sweep(wav, arc_map)
    fps = int(round(1.0 / render_check.HOP_SEC))

    good = render_check.check_map_vs_render(
        sweep.rms100_db, fps, true_end, arc_map)
    assert good and good[0].level == "INFO", \
        [(f.level, f.measured) for f in good]
    assert abs(good[0].measured["delta_sec"]) <= good[0].measured["warn_above_sec"]

    bad = render_check.check_map_vs_render(
        sweep.rms100_db, fps, flat_map.beat_to_sec(arr_end_beats), flat_map)
    assert bad and bad[0].level == "FAIL", \
        [(f.level, f.measured) for f in bad]
    assert abs(bad[0].measured["delta_sec"]) > bad[0].measured["fail_above_sec"]


def test_map_vs_render_misses_a_compensated_interior_error(tmp_path):
    """PINS A KNOWN LIMITATION rather than a capability.

    check_map_vs_render only compares ENDPOINTS. A map that is wrong in the
    middle but right at the end sails through: 120->170 and 170->120 across
    the same span predict end times within a second of each other while
    differing hugely at the midpoint. Codex raised this in round 3; it is real,
    it is carded, and this test exists so nobody later mistakes the check for
    a general map-correctness gate.

    If a future change makes this test FAIL, that is good news - the interior
    is being validated. Update the card, do not delete the test.
    """
    arr_end_beats = 480.0
    up = render_check.TempoMap(np.array([0.0, arr_end_beats]),
                               np.array([120.0, 170.0]), 120.0)
    down = render_check.TempoMap(np.array([0.0, arr_end_beats]),
                                 np.array([170.0, 120.0]), 170.0)
    end_up = up.beat_to_sec(arr_end_beats)
    end_down = down.beat_to_sec(arr_end_beats)
    # Same total, by construction: the log integral is symmetric in v0 <-> v1.
    assert abs(end_up - end_down) < 1e-6, (end_up, end_down)
    # ... but grossly different in the middle: 17.4 s apart on this fixture,
    # comfortably past the endpoint check's 10 s warn cap, and it sees none
    # of it.
    mid_gap = abs(up.beat_to_sec(arr_end_beats / 2)
                  - down.beat_to_sec(arr_end_beats / 2))
    assert mid_gap > render_check.MAP_ENDPOINT_WARN_CAP_SEC, mid_gap

    clips = [{"track": "A", "arr_start": 0, "arr_end": int(arr_end_beats),
              "loop_on": False}]
    wav = tmp_path / "comp.wav"
    _synth_render(wav, end_up, clips=clips)
    sweep = render_check.streaming_sweep(wav, up)
    fps = int(round(1.0 / render_check.HOP_SEC))

    # The WRONG (reversed) map still passes the endpoint check.
    out = render_check.check_map_vs_render(sweep.rms100_db, fps, end_down, down)
    assert out and out[0].level == "INFO", \
        [(f.level, f.measured) for f in out]


def test_map_vs_render_tolerates_a_fading_ending(tmp_path):
    """A render whose audio dies before its clips do must not be newly FAILED.

    The endpoint is measured from the last audible frame, so a fade, a silent
    final clip or a reverb tail moves it by seconds. Codex flagged that an
    earlier tolerance made this a FAIL and so could reject valid FLAT renders
    the gate previously accepted. Seconds warn; only minutes fail.
    """
    fps = int(round(1.0 / render_check.HOP_SEC))
    tmap = render_check.TempoMap.flat(120.0)
    arr_end = 600.0
    # Audio stops 6 s early - a long fade. Frames are 100 ms.
    rms = np.full(int(arr_end * fps), -20.0)
    rms[int((arr_end - 6.0) * fps):] = render_check.FLOOR_DB
    out = render_check.check_map_vs_render(rms, fps, arr_end, tmap)
    assert out and out[0].level == "WARN", \
        [(f.level, f.measured) for f in out]

    # A minutes-scale gap on the same shape still FAILs.
    rms2 = np.full(int(arr_end * fps), -20.0)
    rms2[int((arr_end - 120.0) * fps):] = render_check.FLOOR_DB
    out2 = render_check.check_map_vs_render(rms2, fps, arr_end, tmap)
    assert out2 and out2[0].level == "FAIL", \
        [(f.level, f.measured) for f in out2]


def test_grid_fold_never_gates_on_an_arc(tmp_path):
    """On an arc grid_fold reports and does not gate, at ANY drift.

    Two earlier attempts at a gating rule were both wrong (an unconditional
    downgrade, then an allowance derived from the fitted 19.5 ppm mean, which
    grows without limit and swallows a known-wrong result on a longer mix).
    Until the map's uncertainty is characterised the honest position is that
    this check cannot gate an arc - and `check_map_vs_render` is what catches
    a grossly wrong map instead.
    """
    seconds = 145.0
    bpm = 120.0
    bps = bpm / 60.0
    clips = [
        {"track": "A", "arr_start": 0, "arr_end": round(70 * bps),
         "loop_on": False},
        {"track": "B", "arr_start": round(65 * bps),
         "arr_end": round(seconds * bps), "loop_on": False},
    ]
    wav = tmp_path / "b.wav"
    _synth_render(wav, seconds, clips=clips)
    als_arc = tmp_path / "arcb.als"
    _write_als(als_arc, clips, bpm=bpm,
               tempo_envelope=[(0.0, 120.0), (128.0, 124.0)])
    als_flat = tmp_path / "flatb.als"
    _write_als(als_flat, clips, bpm=bpm)

    import xml.etree.ElementTree as ET
    import gzip as _gzip

    def grid_fold_with(als_path, medians):
        root = ET.fromstring(_gzip.open(als_path, "rb").read())
        tmap = render_check.TempoMap.from_als_root(root)
        real = render_check._grid_fold_median
        seq = iter(medians)
        render_check._grid_fold_median = lambda *a, **k: next(seq)
        try:
            out = render_check.check_grid_fold(wav, clips, tmap)
        finally:
            render_check._grid_fold_median = real
        return [f for f in out if f.check == "grid_fold"]

    # Huge drift on an arc: still only a WARN, and it says it is not gating.
    big = grid_fold_with(als_arc, [0.0, 500.0])
    assert big and big[0].level == "WARN", [(f.level, f.measured) for f in big]
    assert big[0].measured["gates"] is False
    assert "cannot gate" in big[0].msg

    # The same drift on a FLAT map still FAILs - the flat path is untouched,
    # so this cannot pass by the check having been disabled outright.
    flat = grid_fold_with(als_flat, [0.0, 500.0])
    assert flat and flat[0].level == "FAIL", \
        [(f.level, f.measured) for f in flat]
    assert flat[0].measured["gates"] is True


# --------------------------------------------------------------------------- #
# Fail-closed set: ambiguities that must not be resolved silently              #
# (MiniMax review 2026-08-27, cases 2/4/5 - all three reproduced first)        #
# --------------------------------------------------------------------------- #

def _envelope_als_root(event_sets, manual=120.0, target="8",
                       omit_pointee=False):
    """MainTrack with one AutomationEnvelope per entry in event_sets."""
    from xml.etree.ElementTree import Element, SubElement, tostring, fromstring
    root = Element("Ableton")
    t = SubElement(root, "Tempo")
    SubElement(t, "Manual").set("Value", str(manual))
    mt = SubElement(root, "MainTrack")
    mtt = SubElement(mt, "Tempo")
    SubElement(mtt, "AutomationTarget").set("Id", "8")
    for events in event_sets:
        env = SubElement(mt, "AutomationEnvelope")
        et = SubElement(env, "EnvelopeTarget")
        if not omit_pointee:
            SubElement(et, "PointeeId").set("Value", target)
        ev = SubElement(env, "Events")
        for beat, value in events:
            fe = SubElement(ev, "FloatEvent")
            fe.set("Time", str(beat))
            fe.set("Value", str(value))
    return fromstring(tostring(root))


def test_two_envelopes_on_one_target_raise():
    """Which envelope plays is genuinely ambiguous, so refuse rather than
    silently take the first. Pre-fix this returned the FIRST envelope's map
    with no diagnostic at all."""
    root = _envelope_als_root([[(0.0, 120.0), (64.0, 121.0)],
                               [(0.0, 200.0), (64.0, 250.0)]])
    with pytest.raises(render_check.TempoAutomationUnsupported) as e:
        render_check.TempoMap.from_als_root(root)
    assert "2 tempo envelopes" in str(e.value)

    # Control: one envelope on the target still maps fine.
    ok = render_check.TempoMap.from_als_root(
        _envelope_als_root([[(0.0, 120.0), (64.0, 121.0)]]))
    assert not ok.is_flat


def test_unidentifiable_envelope_raises_rather_than_going_flat():
    """An envelope carrying events but no PointeeId might BE the tempo
    envelope. Falling through to flat() would check an ARC render against a
    FLAT map - the 170-second class of error this module exists to stop.

    Pre-fix this silently returned a flat map at the Manual tempo while the
    envelope said 120 -> 140.
    """
    root = _envelope_als_root([[(0.0, 120.0), (64.0, 140.0)]],
                              omit_pointee=True)
    with pytest.raises(render_check.TempoAutomationUnsupported) as e:
        render_check.TempoMap.from_als_root(root)
    assert "no usable PointeeId" in str(e.value)

    # Control 1: an envelope on a DIFFERENT, readable target is ignorable -
    # that is not ambiguous, so it must still fall through to flat.
    other = _envelope_als_root([[(0.0, 120.0), (64.0, 140.0)]], target="99")
    flat = render_check.TempoMap.from_als_root(other)
    assert flat.is_flat and flat.bpm_at(64.0) == pytest.approx(120.0)

    # Control 2: an envelope with no PointeeId AND no events is not evidence
    # of anything, so it must not raise.
    empty = _envelope_als_root([[]], omit_pointee=True)
    assert render_check.TempoMap.from_als_root(empty).is_flat


def test_first_breakpoint_after_beat_zero_holds_backwards():
    """Documented, deliberate semantics: a Live envelope extends its first
    breakpoint backwards, so with no sentinel the first value applies from
    beat 0 and Manual does NOT. Pinned so the choice cannot drift silently."""
    root = _envelope_als_root([[(5.0, 130.0)]], manual=120.0)
    tmap = render_check.TempoMap.from_als_root(root)
    assert tmap.bpm_at(0.0) == pytest.approx(130.0)
    assert tmap.beat_to_sec(2.0) == pytest.approx(2.0 * 60.0 / 130.0)
    assert tmap.beat_to_sec(2.0) != pytest.approx(2.0 * 60.0 / 120.0)


def test_map_endpoint_fail_tolerance_is_capped_at_long_duration():
    """The FAIL threshold IS the merge gate, so it must be bounded.

    An uncapped relative term looks reasonable at normal lengths and quietly
    becomes an unbounded acceptance envelope: Codex worked out that at 10
    hours a 1% term would let a SIX MINUTE discrepancy pass. The cap is what
    stops that, so it gets a test at a duration where it actually bites.
    """
    fps = int(round(1.0 / render_check.HOP_SEC))
    tmap = render_check.TempoMap.flat(120.0)
    ten_hours = 36000.0

    # Uncapped, 1% of 10 hours would be 360 s. The cap must hold it to 60.
    rms = np.full(10, -20.0)
    out = render_check.check_map_vs_render(rms, fps, ten_hours, tmap)
    assert out[0].measured["fail_above_sec"] == pytest.approx(
        render_check.MAP_ENDPOINT_FAIL_CAP_SEC)
    assert out[0].measured["warn_above_sec"] == pytest.approx(
        render_check.MAP_ENDPOINT_WARN_CAP_SEC)

    # A 6-minute discrepancy at that duration must FAIL, not pass.
    six_min_short = ten_hours - 360.0
    rms2 = np.full(int(six_min_short * fps), -20.0)
    out2 = render_check.check_map_vs_render(rms2, fps, ten_hours, tmap)
    assert out2 and out2[0].level == "FAIL", \
        [(f.level, f.measured) for f in out2]

    # The cap never rises above itself, and never drops below the floor.
    for dur in (10.0, 600.0, 5031.0, 36000.0, 360000.0):
        got = render_check.check_map_vs_render(
            np.full(5, -20.0), fps, dur, tmap)[0].measured["fail_above_sec"]
        assert (render_check.MAP_ENDPOINT_FAIL_ABS_SEC <= got
                <= render_check.MAP_ENDPOINT_FAIL_CAP_SEC), (dur, got)


def test_unmappable_tempo_bail_lists_no_check_as_clean(tmp_path):
    """The early-bail path never opens the audio, so NO check ran.

    Before this pin, run_check's TempoAutomationUnsupported return produced a
    report whose "Checks run clean" section named all eleven real checks -
    the exact silently-clean failure the SKIP handling exists to prevent, on
    the one path that never reads a sample. This is not hypothetical: the
    shipped RENDER_CHECK_V16.md of 2026-08-25 listed boundary_click,
    grid_fold, loop_verbatim and eight others as clean for a render whose
    reported duration was 0.00 s and whose clip count was 0.
    """
    als = tmp_path / "m.als"
    wav = tmp_path / "m.wav"
    rpt = tmp_path / "r.json"
    seconds = 10.0
    clips = _single_track_clips(seconds)
    # Manual tempo of 0 is outside the 20-300 BPM band, so the map refuses.
    _write_als(als, clips, bpm=0.0)
    _synth_render(wav, seconds, clips=clips)
    _write_report(rpt)

    res = render_check.run_check(wav, rpt, als)
    assert res.verdict == "FAIL" and res.exit_code == 2
    assert [f.check for f in res.findings] == ["tempo_automation_unsupported"]
    assert res.meta["checks_ran"] is False

    md_path, js_path = render_check.write_report(res, wav)
    md = md_path.read_text(encoding="utf-8")
    clean_section = md.split("## Checks run clean", 1)[1]
    assert "(none)" in clean_section, clean_section
    # No real check may be named as clean on a path that read no audio.
    for name in ("boundary_click", "grid_fold", "loop_verbatim",
                 "hard_silence", "transition_dip", "level_cliff",
                 "loop_hole", "loop_period", "loop_exit_jump",
                 "exposed_solo", "eof_truncated_reads", "map_vs_render"):
        assert name not in clean_section, (name, clean_section)

    # A normal run is unaffected: it still reports its clean checks.
    als2 = tmp_path / "ok.als"
    wav2 = tmp_path / "ok.wav"
    rpt2 = tmp_path / "ok.json"
    _write_als(als2, clips)
    _synth_render(wav2, seconds, clips=clips)
    _write_report(rpt2)
    res2 = render_check.run_check(wav2, rpt2, als2)
    assert res2.meta.get("checks_ran", True) is True
    md2 = render_check.write_report(res2, wav2)[0].read_text(encoding="utf-8")
    assert "hard_silence" in md2.split("## Checks run clean", 1)[1]


def _tone_render(path, seconds, notch=None, sr=44100):
    """Equal-amplitude sines, one per DIP_BANDS band. `notch` is
    (t0, t1, component_hz, gain_db): that component alone is attenuated over
    that span, so the expected per-band deficit is known exactly.
    """
    n = int(seconds * sr)
    t = np.arange(n, dtype=np.float64) / sr
    y = np.zeros(n, dtype=np.float64)
    for hz in (40.0, 100.0, 250.0, 1000.0, 4000.0):
        comp = np.sin(2 * math.pi * hz * t)
        if notch and abs(hz - notch[2]) < 1e-9:
            g = np.ones(n)
            i0, i1 = int(notch[0] * sr), int(notch[1] * sr)
            g[i0:i1] = 10.0 ** (notch[3] / 20.0)
            comp = comp * g
        y += comp
    y *= 0.12  # keep well clear of clipping
    sf.write(str(path), np.stack([y, y], axis=1), sr, subtype="PCM_24")


def test_dip_band_deficit_names_the_band_that_actually_dropped(tmp_path):
    """The diagnosis must identify WHICH band lost energy, not just that the
    level fell. Built with a known -12 dB notch on the 1 kHz component only.
    """
    wav = tmp_path / "tone.wav"
    _tone_render(wav, 40.0, notch=(30.0, 33.0, 1000.0, -12.0))

    res = render_check._dip_band_deficit(
        wav, dip_sec=31.5, base0_sec=5.0, base1_sec=25.0)
    assert res is not None
    bands = res["delta"]

    # The notched band takes the hit, close to the -12 dB applied.
    assert bands["mid"] == pytest.approx(-12.0, abs=1.0), bands
    # Every other band is essentially untouched.
    for name in ("sub", "bass", "lowmid", "high"):
        assert abs(bands[name]) < 1.0, (name, bands)
    # And the worst band is the one that actually dropped.
    assert min(bands, key=lambda k: bands[k]) == "mid", bands


def test_dip_band_deficit_survives_an_unreadable_render(tmp_path):
    """A diagnostic must never take the gate down with it."""
    missing = tmp_path / "nope.wav"
    assert render_check._dip_band_deficit(missing, 10.0, 1.0, 5.0) is None
    # A real file, but a window past the end: no data, no crash, no claim.
    wav = tmp_path / "short.wav"
    _tone_render(wav, 5.0)
    assert render_check._dip_band_deficit(wav, 900.0, 800.0, 850.0) is None


def test_transition_dip_full_path_locates_and_names_the_band(tmp_path):
    """Drive check_transition_dip END TO END, not just its band helper.

    The previous band test called _dip_band_deficit with hand-supplied
    coordinates, so the argmin -> seconds conversion, the naming threshold and
    the message were all untested. MiniMax's review named five mutants that
    survived it; this pins the four that matter:
      A  dip_sec off by one LUFS frame
      B  argmin dropped (dip_sec pinned to the window start)
      C  deficit_band hardcoded to "broadband"
      E  the naming threshold inverted
    """
    fps = int(round(1.0 / render_check.HOP_SEC))
    tmap = render_check.TempoMap.flat(120.0)          # 0.5 s per beat
    swap_beat = 140.0                                 # swap at 70.0 s
    swap_sec = 70.0
    notch_at = 78.0                                   # inside the 32-beat span
    wav = tmp_path / "dip.wav"
    _tone_render(wav, 92.0, notch=(notch_at - 1.5, notch_at + 1.5, 1000.0, -12.0))

    # Short-term LUFS is a TRAILING 3 s window, so a notch centred at
    # notch_at shows its minimum one half-window LATER. Build the array that
    # way, and require the code to correct back to the real defect time - a
    # flat spike at notch_at would let the uncorrected version pass.
    lag_sec = (render_check.ST_WINDOW_HOPS - 1) / 2.0 / fps
    st = np.full(int(92.0 * fps), -17.0)
    st[int(round((notch_at + lag_sec) * fps))] = -22.0   # > TRANSITION_DIP_DB
    out = render_check.check_transition_dip(
        [{"swap_beats": swap_beat, "pair_index": 1}], st, fps, tmap,
        render_path=wav)

    assert len(out) == 1, out
    m = out[0].measured
    # Kills A and B: the reported dip time must be the LUFS minimum, not the
    # window start (70.0) and not one frame off.
    # Exact, not a tolerance: the expected value is computable from the frame
    # the minimum was placed at, and a half-frame tolerance let a one-frame
    # mutant sit on the far edge and pass.
    min_frame = int(round((notch_at + lag_sec) * fps))
    expected = (min_frame - (render_check.ST_WINDOW_HOPS - 1) / 2.0) / fps
    assert m["dip_at_sec"] == pytest.approx(expected, abs=1e-6), (m, expected)
    assert abs(expected - notch_at) < 0.1, expected   # and it IS the notch
    assert abs(m["dip_at_sec"] - swap_sec) > 1.0, m
    # Kills C and E: the band that actually dropped must be named.
    assert m["deficit_band"] == "mid", m
    assert m["band_db"]["mid"] == pytest.approx(-12.0, abs=1.5), m
    for other in ("sub", "bass", "lowmid", "high"):
        assert abs(m["band_db"][other]) < 1.5, (other, m["band_db"])
    assert "deficit in mid" in out[0].msg, out[0].msg


def test_gate_error_path_lists_no_check_as_clean(tmp_path, monkeypatch):
    """main()'s exception path is the SECOND bail that reads no audio.

    Fixing only the tempo-envelope bail left this one reporting all twelve
    checks as clean beside a gate error (MiniMax review 2026-08-28). Driven
    through main() on purpose: an earlier version of this test built the
    CheckResult by hand, which proved write_report honours the flag but not
    that main() sets it - and a mutant that dropped it from main() survived.
    """
    wav = tmp_path / "m V1.wav"
    rpt = tmp_path / "r.json"
    als = tmp_path / "m.als"
    _write_report(rpt)
    als.write_bytes(b"this is not gzipped XML")   # parse_als raises
    _synth_render(wav, 5.0, clips=_single_track_clips(5.0))

    monkeypatch.setattr(sys, "argv",
                        ["render_check.py", str(wav), str(rpt), str(als)])
    assert render_check.main() == 2

    # main()'s error meta carries no v_suffix, so the report lands as
    # RENDER_CHECK.md rather than RENDER_CHECK_V1.md. Glob rather than pin
    # that detail - what matters here is what the report SAYS.
    reports = sorted(tmp_path.glob("RENDER_CHECK*.md"))
    assert len(reports) == 1, reports
    md = reports[0].read_text(encoding="utf-8")
    clean = md.split("## Checks run clean", 1)[1]
    assert "(none)" in clean, md
    for name in ("boundary_click", "grid_fold", "loop_verbatim",
                 "hard_silence", "transition_dip", "map_vs_render"):
        assert name not in clean, (name, md)


def test_band_rms_is_immune_to_stereo_cancellation(tmp_path):
    """Anti-phase channels must not read as a band deficit.

    Averaging L and R before filtering lets a width or polarity change cancel
    in the sum, inventing a huge deficit no one can hear (Codex review
    2026-08-28). Sam has flagged stereo width collapsing at a transition as an
    audible event in its own right, so this is a live case, not a contrivance.
    """
    sr = 44100
    t = np.arange(int(3.0 * sr), dtype=np.float64) / sr
    tone = 0.2 * np.sin(2 * math.pi * 1000.0 * t)
    in_phase = np.stack([tone, tone], axis=1)
    anti_phase = np.stack([tone, -tone], axis=1)

    a = render_check._band_rms_db(in_phase, sr, 400.0, 2000.0)
    b = render_check._band_rms_db(anti_phase, sr, 400.0, 2000.0)
    # Same energy per channel, so the same answer - within a hair.
    assert a == pytest.approx(b, abs=0.1), (a, b)
    assert a > -30.0, a          # and a real level, not a cancelled floor


def test_inaudible_band_cannot_win_the_diagnosis():
    """An empty band's noise floor drifting is not a repair target."""
    W = render_check._worst_audible_band
    # Sub is 80 dB down and falls 20 dB; mid is full level and falls 4.
    delta = {"sub": -20.0, "bass": -0.1, "lowmid": 0.0, "mid": -4.0, "high": 0.0}
    base = {"sub": -100.0, "bass": -22.0, "lowmid": -21.0, "mid": -21.0,
            "high": -21.0}
    assert W(delta, base) == "mid", W(delta, base)

    # Once that same band is audible, the biggest drop wins again.
    base_loud = dict(base, sub=-24.0)
    assert W(delta, base_loud) == "sub"

    # Exactly at the boundary counts as audible (>= loudest - threshold).
    edge = dict(base, sub=-21.0 - render_check.DIP_BAND_AUDIBLE_WITHIN_DB)
    assert W(delta, edge) == "sub"
    just_under = dict(base, sub=-21.01 - render_check.DIP_BAND_AUDIBLE_WITHIN_DB)
    assert W(delta, just_under) == "mid"

    # Every band inaudible (a near-silent passage): fall back rather than
    # raise, so the diagnostic still says something.
    allquiet = {k: -130.0 for k in delta}
    assert W(delta, allquiet) == "sub"


def test_partially_out_of_range_window_is_refused(tmp_path):
    """A window that runs off the end must return None, not a short fragment.

    Comparing 2 s of a requested 3 s against the full baseline reports the
    truncation as a band deficit.
    """
    wav = tmp_path / "short.wav"
    _tone_render(wav, 10.0)
    # Centred at 9.5 s: [8.0, 11.0) against a 10 s file -> only 2 of 3 s.
    assert render_check._dip_band_deficit(wav, 9.5, 1.0, 6.0) is None
    # Fully inside is still fine.
    assert render_check._dip_band_deficit(wav, 5.0, 1.0, 4.0) is not None


def test_failed_band_diagnosis_is_named_not_silent(tmp_path):
    """A diagnostic that could not run must say so on the finding.

    Silence is indistinguishable from "no deficit found", which is exactly the
    silently-clean class this file keeps closing (Codex + MiniMax, 2026-08-28).
    """
    fps = int(round(1.0 / render_check.HOP_SEC))
    wav = tmp_path / "short.wav"
    _tone_render(wav, 30.0)
    lag = (render_check.ST_WINDOW_HOPS - 1) / 2.0 / fps
    # The LUFS array runs past the audio (the arrangement is longer than the
    # render), so the dip minimum sits near EOF and its +/-1.5 s window
    # overruns the file.
    st = np.full(int(45.0 * fps), -17.0)
    st[int(round((29.55 + lag) * fps))] = -23.0
    out = render_check.check_transition_dip(
        [{"swap_beats": 56.0, "pair_index": 1}], st, fps,
        render_check.TempoMap.flat(120.0), render_path=wav)

    assert len(out) == 1, out
    m = out[0].measured
    assert "band_db" not in m, m            # it genuinely could not measure
    assert m.get("band_error"), m           # and it says so
