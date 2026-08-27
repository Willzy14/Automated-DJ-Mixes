"""Render gate: measure a bounced mix WAV against the ALS clip list and the
arrangement report, write a defect report beside the render, exit with status.

Permanent home of every check prototyped in the 2026-08-20 V10 review
(Documentation/Reviews/2026-08-20 First Render Check - Mix V10.md). One
streaming pass per render; targeted re-reads for clicks; librosa only inside
grid_fold over <= 3 x 60 s at 22050 Hz mono.

Usage:
    python Source/render_check.py <render.wav> <arrangement_report.json> <als_path> [--json-out PATH]
Library:
    from render_check import run_check, CheckResult, Finding
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
import soundfile as sf
from scipy.signal import lfilter, lfilter_zi, sosfiltfilt, butter

HOP_SEC = 0.1
FLOOR_DB = -120.0
SWEEP_BLOCK_SEC = 10.0
CLICK_HALF_WINDOW_SEC = 0.002  # +/-2 ms around a boundary, in samples via sr
NULL_BEATS_EACH_SIDE = 16
NULL_EXCLUSION_BEATS = 0.5
BOUNDARY_DEDUP_BEATS = 0.25
CLICK_SHAPE_MAX_WIDTH = 8
CLICK_NULL_RATIO = 1.5
CLICK_NULL_Z = 4.0

# Hard silence: rms100_db < -60 dBFS for > 0.5 s, inside the arrangement span.
HARD_SILENCE_DB = -60.0
HARD_SILENCE_MIN_S = 0.5

# Level cliff at loop insert: beat step worse than -6 dB.
LEVEL_CLIFF_DB = -6.0

# Loop exit jump: beat step >= +4 dB.
LOOP_EXIT_JUMP_DB = 4.0

# Off-phrase loop length set: anything else fires the warning.
LOOP_PERIOD_OK = {1, 2, 4, 8, 16, 32}

# Exposed solo: 100 ms RMS < -30 dBFS with exactly one active clip, > 3 s.
# 100 ms RMS, not short-term LUFS: on V10 the D5 breakdown sits below -30 for
# 5.8 s of RMS but only 3.0 s of 3 s-smoothed ST -- the smoothing eats the
# margin and turns the calibration case into a coin flip.
EXPOSED_SOLO_DB = -30.0
EXPOSED_SOLO_MIN_S = 3.0

# Loop hole: folded template spread >= 7 dB with >= 2 consecutive quiet beats.
# Calibrated on V10: the Vente tail loop (the review's D6) folds to a 7.3 dB
# spread; the Nappp loop folds to 6.6 dB and is already reported as D4/D5
# material, not a hole -- 7.0 separates them the way the prototype did.
LOOP_HOLE_SPREAD_DB = 7.0
LOOP_HOLE_QUIET_DB = 6.0
LOOP_HOLE_MIN_CONSEC = 2

# Transition dip, swap-centered: baseline = median ST-LUFS over beats
# [swap-64, swap-8); dip = baseline - min ST over [swap, swap+32]; > 2.5 dB
# fires. Calibrated on V10 against the prototype's firing set {T1,T4,T5,T11}:
# this convention measures 4.6/5.8/2.8/5.9 dB there and at most 1.9 dB on
# every transition the prototype cleared (the review quoted 3 dB under its
# own windowing; 2.5 reproduces its verdicts under this one).
TRANSITION_DIP_DB = 2.5
TRANSITION_BASELINE_BEATS = 64
TRANSITION_BASELINE_GAP_BEATS = 8
TRANSITION_DIP_SPAN_BEATS = 32

# Loop verbatim: min consecutive-iteration envelope r < 0.9 -> FAIL.
LOOP_VERBATIM_MIN_R = 0.9

# Grid fold: median-phase drift across solo probes > 30 ms -> FAIL.
GRID_FOLD_DRIFT_MS = 30.0
GRID_FOLD_REGION_S = 45.0
GRID_FOLD_PROBE_S = 60.0

# K-weighting filter design constants (ITU BS.1770-4, pyloudnorm DeMan variants).
KW_SHELF_F0 = 1681.9744509555319
KW_SHELF_GAIN_DB = 3.99984385397
KW_SHELF_Q = 0.7071752369554196
KW_HP_F0 = 38.13547087602444
KW_HP_Q = 0.5003270373238773
KW_LUFS_OFFSET_DB = -0.691

# Integrated LUFS gating (BS.1770-4).
LUFS_BLOCK_FRAMES = 4  # 400 ms at 100 ms hop.
LUFS_OVERLAP_FRAMES = 1  # 75% overlap -> step 1 frame.
LUFS_ABS_GATE = -70.0
LUFS_REL_GATE_OFFSET = 10.0


@dataclass
class Finding:
    check: str
    level: str
    t0: float
    t1: float
    beat0: float
    beat1: float
    measured: dict
    msg: str


@dataclass
class CheckResult:
    findings: list
    verdict: str
    exit_code: int
    meta: dict


# --------------------------------------------------------------------------- #
# Parsers                                                                     #
# --------------------------------------------------------------------------- #

def _float(el, tag):
    if el is None:
        return None
    n = el.find(tag)
    if n is None or n.get("Value") is None:
        return None
    return float(n.get("Value"))


class TempoAutomationUnsupported(Exception):
    """Raised when the MainTrack tempo envelope cannot be mapped safely.

    Attributes:
        distinct_values: sorted list of distinct FloatEvent Values (rounded
            to 6 dp) found on the envelope. None when the envelope is
            absent. Empty when the envelope has no parseable values.
    """

    def __init__(self, message: str, distinct_values: list[float] | None):
        super().__init__(message)
        self.distinct_values = distinct_values


def _find_main_track(root):
    for tag in ("MainTrack", "MasterTrack"):
        for el in root.iter(tag):
            return el
    return None


class TempoMap:
    """Piecewise beat<->time map from the MainTrack tempo envelope."""

    def __init__(self, beats: np.ndarray, bpms: np.ndarray,
                 nominal_bpm: float):
        self._beats = np.asarray(beats, dtype=np.float64)
        self._bpms = np.asarray(bpms, dtype=np.float64)
        self._nominal_bpm = float(nominal_bpm)
        if (self._beats.ndim != 1 or self._bpms.ndim != 1
                or len(self._beats) == 0
                or len(self._beats) != len(self._bpms)):
            raise ValueError("tempo map needs matching non-empty beat/BPM points")
        if (not np.all(np.isfinite(self._beats))
                or not np.all(np.isfinite(self._bpms))):
            raise ValueError("tempo map points must be finite")
        if np.any(np.diff(self._beats) <= 0):
            raise ValueError("tempo map beat points must be strictly increasing")
        if np.any((self._bpms <= 20.0) | (self._bpms >= 300.0)):
            raise ValueError("tempo map BPM must satisfy 20 < bpm < 300")
        if not math.isfinite(self._nominal_bpm):
            raise ValueError("nominal BPM must be finite")

        self._point_secs = np.zeros(len(self._beats), dtype=np.float64)
        self._point_secs[0] = self._beats[0] * 60.0 / self._bpms[0]
        for i in range(len(self._beats) - 1):
            width = self._beats[i + 1] - self._beats[i]
            v0 = self._bpms[i]
            v1 = self._bpms[i + 1]
            slope = (v1 - v0) / width
            if slope == 0.0:
                elapsed = width * 60.0 / v0
            else:
                elapsed = (60.0 / slope) * math.log(v1 / v0)
            self._point_secs[i + 1] = self._point_secs[i] + elapsed
        self._beat_edge_cache: dict[int, np.ndarray] = {}

    @classmethod
    def flat(cls, bpm: float) -> "TempoMap":
        bpm = float(bpm)
        if not math.isfinite(bpm) or not 20.0 < bpm < 300.0:
            raise TempoAutomationUnsupported(
                f"Manual tempo is outside 20 < bpm < 300: {bpm}",
                distinct_values=[bpm] if math.isfinite(bpm) else [],
            )
        return cls(np.array([0.0]), np.array([bpm]), bpm)

    @classmethod
    def from_als_root(cls, root) -> "TempoMap":
        manual = root.find(".//Tempo/Manual")
        if manual is None or manual.get("Value") is None:
            raise TempoAutomationUnsupported(
                "ALS has no parseable Manual tempo", distinct_values=[])
        try:
            nominal_bpm = float(manual.get("Value"))
        except (TypeError, ValueError) as e:
            raise TempoAutomationUnsupported(
                "ALS Manual tempo is not numeric", distinct_values=[]) from e
        if not math.isfinite(nominal_bpm) or not 20.0 < nominal_bpm < 300.0:
            distinct = ([nominal_bpm] if math.isfinite(nominal_bpm) else [])
            raise TempoAutomationUnsupported(
                f"Manual tempo is outside 20 < bpm < 300: {nominal_bpm}",
                distinct_values=distinct,
            )

        mt = _find_main_track(root)
        if mt is None:
            return cls.flat(nominal_bpm)
        tempo = mt.find(".//Tempo")
        target_id = "8"
        if tempo is not None:
            at = tempo.find("AutomationTarget")
            if at is not None and at.get("Id") is not None:
                target_id = at.get("Id")

        envelope = None
        for env in mt.iter("AutomationEnvelope"):
            pt = env.find("EnvelopeTarget/PointeeId")
            if pt is not None and pt.get("Value") == target_id:
                envelope = env
                break
        if envelope is None:
            return cls.flat(nominal_bpm)

        raw_points: list[tuple[float, float]] = []
        parsed_values: list[float] = []
        for fe in envelope.iter("FloatEvent"):
            time_text = fe.get("Time")
            value_text = fe.get("Value")
            if time_text is None or value_text is None:
                continue
            try:
                beat = float(time_text)
                value = float(value_text)
            except ValueError:
                continue
            parsed_values.append(value)
            if not math.isfinite(beat) or not math.isfinite(value):
                distinct = sorted({round(v, 6) for v in parsed_values
                                   if math.isfinite(v)})
                raise TempoAutomationUnsupported(
                    "tempo envelope has a non-finite time or value",
                    distinct_values=distinct,
                )
            if not 20.0 < value < 300.0:
                distinct = sorted({round(v, 6) for v in parsed_values})
                raise TempoAutomationUnsupported(
                    f"tempo envelope value outside 20 < bpm < 300: {value}",
                    distinct_values=distinct,
                )
            raw_points.append((beat, value))

        if not raw_points:
            raise TempoAutomationUnsupported(
                "tempo envelope has no parseable FloatEvent after folding",
                distinct_values=[],
            )

        # Live writes the value before the timeline at -63072000 beats. Fold
        # every pre-timeline event to beat zero; a real beat-zero event wins
        # when both are present, matching playback and avoiding a 63M-beat
        # integration interval.
        folded: list[tuple[float, float]] = []
        for beat, value in sorted(raw_points, key=lambda p: p[0]):
            beat = max(0.0, beat)
            if folded and beat == folded[-1][0]:
                folded[-1] = (beat, value)
            else:
                folded.append((beat, value))

        return cls(
            np.array([p[0] for p in folded], dtype=np.float64),
            np.array([p[1] for p in folded], dtype=np.float64),
            nominal_bpm,
        )

    def _return(self, values: np.ndarray, scalar: bool):
        return float(values[0]) if scalar else values

    def bpm_at(self, beat: float) -> float:
        b = float(beat)
        if not math.isfinite(b):
            raise ValueError("beat must be finite")
        if b <= self._beats[0]:
            return float(self._bpms[0])
        if b >= self._beats[-1]:
            return float(self._bpms[-1])
        i = int(np.searchsorted(self._beats, b, side="right") - 1)
        width = self._beats[i + 1] - self._beats[i]
        frac = (b - self._beats[i]) / width
        return float(self._bpms[i] + frac * (self._bpms[i + 1] - self._bpms[i]))

    def beat_to_sec(self, beat) -> float | np.ndarray:
        scalar = np.isscalar(beat)
        values = np.atleast_1d(np.asarray(beat, dtype=np.float64))
        if not np.all(np.isfinite(values)):
            raise ValueError("beats must be finite")
        out = np.empty(values.shape, dtype=np.float64)
        idx = np.searchsorted(self._beats, values, side="right") - 1

        before = idx < 0
        out[before] = (self._point_secs[0]
                       + (values[before] - self._beats[0])
                       * 60.0 / self._bpms[0])
        after = idx >= len(self._beats) - 1
        out[after] = (self._point_secs[-1]
                      + (values[after] - self._beats[-1])
                      * 60.0 / self._bpms[-1])
        middle = ~(before | after)
        if np.any(middle):
            mi = idx[middle]
            delta = values[middle] - self._beats[mi]
            width = self._beats[mi + 1] - self._beats[mi]
            v0 = self._bpms[mi]
            slope = (self._bpms[mi + 1] - v0) / width
            elapsed = delta * 60.0 / v0
            ramp = slope != 0.0
            elapsed[ramp] = ((60.0 / slope[ramp])
                             * np.log((v0[ramp] + slope[ramp] * delta[ramp])
                                      / v0[ramp]))
            out[middle] = self._point_secs[mi] + elapsed
        return self._return(out, scalar)

    def sec_to_beat(self, sec) -> float | np.ndarray:
        scalar = np.isscalar(sec)
        values = np.atleast_1d(np.asarray(sec, dtype=np.float64))
        if not np.all(np.isfinite(values)):
            raise ValueError("seconds must be finite")
        out = np.empty(values.shape, dtype=np.float64)
        idx = np.searchsorted(self._point_secs, values, side="right") - 1

        before = idx < 0
        out[before] = (self._beats[0]
                       + (values[before] - self._point_secs[0])
                       * self._bpms[0] / 60.0)
        after = idx >= len(self._point_secs) - 1
        out[after] = (self._beats[-1]
                      + (values[after] - self._point_secs[-1])
                      * self._bpms[-1] / 60.0)
        middle = ~(before | after)
        if np.any(middle):
            mi = idx[middle]
            elapsed = values[middle] - self._point_secs[mi]
            width = self._beats[mi + 1] - self._beats[mi]
            v0 = self._bpms[mi]
            slope = (self._bpms[mi + 1] - v0) / width
            delta = elapsed * v0 / 60.0
            ramp = slope != 0.0
            delta[ramp] = ((v0[ramp] / slope[ramp])
                           * np.expm1(slope[ramp] * elapsed[ramp] / 60.0))
            out[middle] = self._beats[mi] + delta
        return self._return(out, scalar)

    def beat_edges_sec(self, n_beats: int) -> np.ndarray:
        if isinstance(n_beats, bool) or int(n_beats) != n_beats or n_beats < 0:
            raise ValueError("n_beats must be a non-negative integer")
        n_beats = int(n_beats)
        if n_beats not in self._beat_edge_cache:
            beats = np.arange(n_beats + 1, dtype=np.float64)
            self._beat_edge_cache[n_beats] = self.beat_to_sec(beats)
        return self._beat_edge_cache[n_beats]

    @property
    def is_flat(self) -> bool:
        return bool(np.all(self._bpms == self._bpms[0]))

    @property
    def nominal_bpm(self) -> float:
        return self._nominal_bpm

    @property
    def summary(self) -> dict:
        return {
            "n_points": len(self._beats),
            "min": float(self._bpms.min()),
            "max": float(self._bpms.max()),
            "is_flat": self.is_flat,
        }


def parse_als(path: Path) -> tuple[TempoMap, list[dict]]:
    """Return (tempo_map, clips), with clip geometry in arrangement beats."""
    with gzip.open(path, "rb") as fh:
        root = ET.fromstring(fh.read())

    tempo_map = TempoMap.from_als_root(root)

    clips: list[dict] = []
    for track in root.iter("AudioTrack"):
        tname = ""
        for n in track.iter("EffectiveName"):
            tname = n.get("Value", "") or ""
            break
        for clip in track.iter("AudioClip"):
            cs = _float(clip, "CurrentStart")
            ce = _float(clip, "CurrentEnd")
            if cs is None or ce is None:
                continue
            loop_on = False
            loop_start = 0.0
            loop_end = 0.0
            start_relative = 0.0
            loop = clip.find("Loop")
            if loop is not None:
                ls = _float(loop, "LoopStart")
                le = _float(loop, "LoopEnd")
                sr = _float(loop, "StartRelative")
                lo = loop.find("LoopOn")
                loop_on = lo is not None and lo.get("Value") == "true"
                if ls is not None:
                    loop_start = ls
                if le is not None:
                    loop_end = le
                if sr is not None:
                    start_relative = sr
            clips.append({
                "track": tname,
                "arr_start": cs,
                "arr_end": ce,
                "loop_start": loop_start,
                "loop_end": loop_end,
                "start_relative": start_relative,
                "loop_on": loop_on,
            })
    return tempo_map, clips


def _parse_source_span(source_beats: str) -> tuple[float, float]:
    a, _, b = source_beats.partition("-")
    return float(a), float(b)


def parse_report(path: Path) -> tuple[list[dict], list[dict], list[str]]:
    """Return (loops, transitions, missing) where missing is the list of
    dependent keys absent from the JSON object. An empty list PRESENT in the
    JSON is an explicit statement ("we checked, nothing found") and is NOT
    missing - only a missing KEY is missing."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    missing: list[str] = []
    if "loops" not in data:
        missing.append("loops")
    if "transitions" not in data:
        missing.append("transitions")
    loops: list[dict] = []
    for lp in data.get("loops", []):
        a, b = _parse_source_span(lp["source_beats"])
        loops.append({
            "track": lp.get("track", ""),
            "type": lp.get("type", ""),
            "source_a": a,
            "source_b": b,
            "iter_len": b - a,
            "count": int(lp.get("count", 1) or 1),
            "total_beats": float(lp.get("total_beats", b - a)),
            "insert_at_beat": float(lp["insert_at_beat"]),
        })
    transitions: list[dict] = []
    for tr in data.get("transitions", []):
        transitions.append({
            "pair_index": int(tr["pair_index"]),
            "swap_beats": float(tr["swap_beats"]),
            "overlap_beats": float(tr["overlap_beats"]),
            "swap_progress": tr.get("swap_progress"),
        })
    return loops, transitions, missing


def derive_v_suffix(render_path: Path, report_path: Path | None) -> str:
    pat = re.compile(r"[Vv](\d+)")
    for p in (render_path, report_path):
        if p is None:
            continue
        matches = pat.findall(p.name)
        if matches:
            return f"V{matches[-1]}"
    return ""


# --------------------------------------------------------------------------- #
# K-weighting biquads                                                          #
# --------------------------------------------------------------------------- #

def _deman_high_shelf(gain_db: float, q: float, fc: float, sr: float) -> tuple[np.ndarray, np.ndarray]:
    A = 10.0 ** (gain_db / 20.0)
    K = math.tan(math.pi * fc / sr)
    Vb = A ** 0.499666774155
    a0 = 1.0 + K / q + K * K
    b0 = (A + Vb * K / q + K * K) / a0
    b1 = 2.0 * (K * K - A) / a0
    b2 = (A - Vb * K / q + K * K) / a0
    a1 = 2.0 * (K * K - 1.0) / a0
    a2 = (1.0 - K / q + K * K) / a0
    return np.array([b0, b1, b2]), np.array([1.0, a1, a2])


def _deman_high_pass(q: float, fc: float, sr: float) -> tuple[np.ndarray, np.ndarray]:
    K = math.tan(math.pi * fc / sr)
    den = 1.0 + K / q + K * K
    b0 = 1.0
    b1 = -2.0
    b2 = 1.0
    a1 = 2.0 * (K * K - 1.0) / den
    a2 = (1.0 - K / q + K * K) / den
    return np.array([b0, b1, b2]), np.array([1.0, a1, a2])


def k_weight_biquads(sr: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    b_shelf, a_shelf = _deman_high_shelf(KW_SHELF_GAIN_DB, KW_SHELF_Q, KW_SHELF_F0, sr)
    b_hp, a_hp = _deman_high_pass(KW_HP_Q, KW_HP_F0, sr)
    return b_shelf, a_shelf, b_hp, a_hp


# --------------------------------------------------------------------------- #
# Streaming sweep                                                             #
# --------------------------------------------------------------------------- #

@dataclass
class SweepArrays:
    rms100_db: np.ndarray
    kms100: np.ndarray
    beat_rms_db: np.ndarray
    beat_count: np.ndarray
    meta: dict


@dataclass
class _SweepState:
    """Filter state and partial-frame accumulators carried across blocks."""
    zi_shelf_L: np.ndarray
    zi_shelf_R: np.ndarray
    zi_hp_L: np.ndarray
    zi_hp_R: np.ndarray
    # Partial-frame K-weighted sum-of-squares.
    kw_sum_sq: float = 0.0
    # Partial-frame raw sum-of-squares (for 100 ms dB RMS).
    raw_sum_sq: float = 0.0
    # Sample count accumulated in the partial frame.
    partial_count: int = 0


def _flush_frame(state: _SweepState, hop_idx: int,
                 rms100_db: np.ndarray, kms100: np.ndarray,
                 hop_samples: int) -> int:
    """If the partial frame is full, flush it and advance hop_idx."""
    if state.partial_count < hop_samples:
        return hop_idx
    count = state.partial_count
    # BS.1770 stereo: sum of per-channel weighted mean squares, weight 1.0.
    kms100[hop_idx] = state.kw_sum_sq / count
    if state.raw_sum_sq > 0:
        rms100_db[hop_idx] = 10.0 * np.log10(state.raw_sum_sq / count)
    else:
        rms100_db[hop_idx] = FLOOR_DB
    state.kw_sum_sq = 0.0
    state.raw_sum_sq = 0.0
    state.partial_count = 0
    return hop_idx + 1


def streaming_sweep(render_path: Path, tempo_map: TempoMap) -> SweepArrays:
    """One pass: 100 ms RMS, 100 ms K-weighted MS, per-beat RMS. O(duration)
    summary arrays; constant audio memory: carries filter state and a beat
    accumulator across reads (the per-hop and per-beat arrays grow with the
    render duration, but no audio block is retained)."""
    sr = float(sf.info(render_path).samplerate)
    n_frames = sf.info(render_path).frames
    channels = sf.info(render_path).channels
    duration = n_frames / sr

    b_shelf, a_shelf, b_hp, a_hp = k_weight_biquads(sr)
    state = _SweepState(
        zi_shelf_L=lfilter_zi(b_shelf, a_shelf),
        zi_shelf_R=lfilter_zi(b_shelf, a_shelf),
        zi_hp_L=lfilter_zi(b_hp, a_hp),
        zi_hp_R=lfilter_zi(b_hp, a_hp),
    )

    n_hops = int(duration / HOP_SEC) + 2
    rms100_db = np.full(n_hops, FLOOR_DB, dtype=np.float64)
    kms100 = np.zeros(n_hops, dtype=np.float64)

    n_beats = int(math.ceil(tempo_map.sec_to_beat(duration))) + 2
    beat_edges_sec = tempo_map.beat_edges_sec(n_beats)
    beat_sum_sq = np.zeros(n_beats, dtype=np.float64)
    beat_count = np.zeros(n_beats, dtype=np.int64)

    hop_samples = int(round(HOP_SEC * sr))
    block_samples = int(round(SWEEP_BLOCK_SEC * sr))
    sample_pos = 0
    hop_idx = 0

    with sf.SoundFile(str(render_path), "r") as fh:
        while True:
            block = fh.read(block_samples, dtype="float64", always_2d=True)
            if block.size == 0:
                break
            L = block[:, 0]
            R = block[:, 1] if block.shape[1] > 1 else block[:, 0]

            Lw, state.zi_shelf_L = lfilter(b_shelf, a_shelf, L, zi=state.zi_shelf_L)
            Lw, state.zi_hp_L = lfilter(b_hp, a_hp, Lw, zi=state.zi_hp_L)
            Rw, state.zi_shelf_R = lfilter(b_shelf, a_shelf, R, zi=state.zi_shelf_R)
            Rw, state.zi_hp_R = lfilter(b_hp, a_hp, Rw, zi=state.zi_hp_R)

            blk_start = sample_pos
            blk_end = sample_pos + len(L)

            t_samples = (blk_start + np.arange(len(L))) / sr
            beat_idx = (np.searchsorted(beat_edges_sec, t_samples,
                                        side="right") - 1)
            sum_sq = (L.astype(np.float64) ** 2 + R.astype(np.float64) ** 2) / 2.0
            np.add.at(beat_sum_sq, beat_idx, sum_sq)
            np.add.at(beat_count, beat_idx, 1)

            i = 0
            while i < len(L):
                want = hop_samples - state.partial_count
                take = min(want, len(L) - i)
                state.kw_sum_sq += float(np.sum(Lw[i:i + take] ** 2))
                state.kw_sum_sq += float(np.sum(Rw[i:i + take] ** 2))
                state.raw_sum_sq += float(np.sum(
                    (L[i:i + take] ** 2 + R[i:i + take] ** 2) / 2.0
                ))
                state.partial_count += take
                i += take
                if state.partial_count >= hop_samples:
                    hop_idx = _flush_frame(state, hop_idx, rms100_db, kms100,
                                            hop_samples)
            sample_pos = blk_end

    rms100_db = rms100_db[:hop_idx]
    kms100 = kms100[:hop_idx]

    valid = beat_count > 0
    beat_rms_db = np.full(beat_sum_sq.shape, FLOOR_DB)
    safe_mean = np.maximum(beat_sum_sq[valid] / beat_count[valid], 1e-30)
    beat_rms_db[valid] = 10.0 * np.log10(safe_mean)
    beat_rms_db[beat_rms_db < FLOOR_DB] = FLOOR_DB

    meta = {
        "render": str(render_path),
        "sr": sr,
        "channels": channels,
        "frames": n_frames,
        "duration_sec": duration,
        "bpm": tempo_map.nominal_bpm,
        "tempo_map": tempo_map.summary,
        "hop_sec": HOP_SEC,
        "hop_samples": hop_samples,
        "block_samples": block_samples,
    }
    return SweepArrays(rms100_db, kms100, beat_rms_db, beat_count, meta)


# --------------------------------------------------------------------------- #
# Loudness helpers                                                            #
# --------------------------------------------------------------------------- #

def short_term_lufs(kms100: np.ndarray) -> np.ndarray:
    """3 s trailing short-term LUFS at 100 ms hop (growing window < 3 s in)."""
    n = len(kms100)
    out = np.full(n, FLOOR_DB)
    if n < 1:
        return out
    # Cumulative sum makes the window mean O(1).
    cs = np.concatenate([[0.0], np.cumsum(kms100)])
    start = 0
    for i in range(n):
        if i - 29 > start:
            start = i - 29
        j = i + 1
        mean_kw = (cs[j] - cs[start]) / (j - start)
        if mean_kw <= 0:
            out[i] = FLOOR_DB
        else:
            out[i] = KW_LUFS_OFFSET_DB + 10.0 * np.log10(mean_kw)
    return out


def integrated_lufs(kms100: np.ndarray) -> float:
    """BS.1770-4 gating: 400 ms blocks at 75% overlap, abs -70, rel -10 LU."""
    n = len(kms100)
    if n < LUFS_BLOCK_FRAMES:
        return FLOOR_DB
    block_ms = np.array([
        kms100[i:i + LUFS_BLOCK_FRAMES].mean() for i in range(n - LUFS_BLOCK_FRAMES + 1)
    ])
    block_lufs = KW_LUFS_OFFSET_DB + 10.0 * np.log10(np.maximum(block_ms, 1e-30))
    keep = block_lufs >= LUFS_ABS_GATE
    if not np.any(keep):
        return FLOOR_DB
    abs_mean_lin = block_ms[keep].mean()
    abs_mean_lufs = KW_LUFS_OFFSET_DB + 10.0 * np.log10(abs_mean_lin)
    rel_thresh = abs_mean_lufs - LUFS_REL_GATE_OFFSET
    keep = keep & (block_lufs >= rel_thresh)
    if not np.any(keep):
        return abs_mean_lufs
    final_lin = block_ms[keep].mean()
    return KW_LUFS_OFFSET_DB + 10.0 * np.log10(final_lin)


# --------------------------------------------------------------------------- #
# Geometry helpers                                                            #
# --------------------------------------------------------------------------- #

def arr_to_sec(beats: float, tempo_map: TempoMap) -> float:
    return tempo_map.beat_to_sec(beats)


def sec_to_arr(sec: float, tempo_map: TempoMap) -> float:
    return tempo_map.sec_to_beat(sec)


def clip_arr_span(clips: list[dict]) -> tuple[float, float]:
    if not clips:
        return 0.0, 0.0
    a = min(c["arr_start"] for c in clips)
    b = max(c["arr_end"] for c in clips)
    return a, b


def active_clips_at(clips: list[dict], t_sec: float,
                    tempo_map: TempoMap) -> int:
    """Number of AudioClips whose [arr_start_sec, arr_end_sec) covers t_sec."""
    n = 0
    for c in clips:
        s = arr_to_sec(c["arr_start"], tempo_map)
        e = arr_to_sec(c["arr_end"], tempo_map)
        if s <= t_sec < e:
            n += 1
    return n


def solo_runs(clips: list[dict], tempo_map: TempoMap,
              min_s: float) -> list[tuple[float, float]]:
    """Contiguous intervals where exactly one clip is active, length >= min_s.

    Sweeps the sorted clip start/end events. Between events the active
    count is constant; an interval counts as a solo run when active count
    is 1 across its full length."""
    events: list[tuple[float, int]] = []
    for c in clips:
        s = arr_to_sec(c["arr_start"], tempo_map)
        e = arr_to_sec(c["arr_end"], tempo_map)
        events.append((s, +1))
        events.append((e, -1))
    if not events:
        return []
    events.sort()
    merged: list[tuple[float, int]] = []
    for t, d in events:
        if merged and merged[-1][0] == t:
            merged[-1] = (t, merged[-1][1] + d)
        else:
            merged.append((t, d))

    runs: list[tuple[float, float]] = []
    count = 0
    run_start: float | None = None
    for t, d in merged:
        if count == 1 and run_start is not None:
            end = t
            if end - run_start >= min_s:
                runs.append((run_start, end))
        count += d
        if count == 1:
            run_start = t
        else:
            run_start = None
    return runs


# --------------------------------------------------------------------------- #
# Boundary computation                                                        #
# --------------------------------------------------------------------------- #

def collect_boundaries(clips: list[dict], loops: list[dict],
                       arr_end_beat: float,
                       tempo_map: TempoMap) -> list[float]:
    """All boundary times in seconds, deduplicated within BOUNDARY_DEDUP_BEATS,
    excluding t=0 and the arrangement end."""
    raw_beats: list[float] = []
    for c in clips:
        raw_beats.append(c["arr_start"])
        raw_beats.append(c["arr_end"])
    for lp in loops:
        a, b = lp["source_a"], lp["source_b"]
        iter_len = b - a
        total = lp["total_beats"]
        count = max(1, round(total / iter_len))
        ins = lp["insert_at_beat"]
        for k in range(1, count):
            raw_beats.append(ins + k * iter_len)

    raw_beats = [b for b in raw_beats
                 if b > 1e-6 and b < arr_end_beat - 1e-6]
    raw_beats.sort()
    out_beats: list[float] = []
    last = -1e9
    for beat in raw_beats:
        if beat - last >= BOUNDARY_DEDUP_BEATS:
            out_beats.append(beat)
            last = beat
    return [arr_to_sec(beat, tempo_map) for beat in out_beats]


# --------------------------------------------------------------------------- #
# Checks                                                                      #
# --------------------------------------------------------------------------- #

def check_hard_silence(rms100_db: np.ndarray, fps: int,
                       arr_start_sec: float, arr_end_sec: float) -> list[Finding]:
    """Inside arrangement span only; tail beyond arr_end is exempt."""
    a = max(0, int(math.floor(arr_start_sec * fps)))
    b = min(len(rms100_db), int(math.ceil(arr_end_sec * fps)))
    findings: list[Finding] = []
    i = a
    while i < b:
        if rms100_db[i] < HARD_SILENCE_DB:
            j = i
            while j < b and rms100_db[j] < HARD_SILENCE_DB:
                j += 1
            t0 = i / fps
            t1 = j / fps
            if t1 - t0 > HARD_SILENCE_MIN_S:
                findings.append(Finding(
                    check="hard_silence", level="FAIL",
                    t0=t0, t1=t1,
                    beat0=0.0, beat1=0.0,
                    measured={"min_db": float(rms100_db[i:j].min()),
                              "frames": int(j - i)},
                    msg=f"hard silence ({t1 - t0:.1f}s) inside arrangement span",
                ))
            i = j
        else:
            i += 1
    return findings


def _read_window(fh: sf.SoundFile, center_sample: int, half: int) -> np.ndarray | None:
    a = max(0, center_sample - half)
    b = min(fh.frames, center_sample + half)
    if b - a < 2:
        return None
    fh.seek(a)
    return fh.read(b - a, dtype="float64", always_2d=True)


def _boundary_metric(y: np.ndarray) -> float:
    """Max over channels of max |x[n]-x[n-1]| in the window."""
    if y is None or len(y) < 2:
        return 0.0
    per_ch = [float(np.max(np.abs(np.diff(y[:, c])))) for c in range(y.shape[1])]
    return max(per_ch)


def _click_shape(y: np.ndarray) -> bool:
    """d[n]=|x[n]-x[n-1]| mono max-of-channels; click-shaped iff <= CLICK_SHAPE_MAX_WIDTH
    samples sit above 0.25 * dmax."""
    if y is None or len(y) < 2:
        return False
    d = np.max(np.abs(np.diff(y, axis=0)), axis=1) if y.ndim > 1 else np.abs(np.diff(y))
    dmax = float(d.max())
    if dmax <= 0:
        return False
    width = int(np.sum(d > 0.25 * dmax))
    return width <= CLICK_SHAPE_MAX_WIDTH


def check_boundary_click(render_path: Path, boundaries_sec: list[float],
                         tempo_map: TempoMap) -> list[Finding]:
    """For each boundary: read +/-2 ms, metric vs null on 32 nearest beats.

    Null set: integer beats 16 each side of the boundary, skipping any beat
    within NULL_EXCLUSION_BEATS of ANY boundary in the input list. One open
    handle + a per-beat metric cache: boundaries cluster around transitions,
    so neighbouring boundaries share most of their null beats.

    Any boundary or null window whose requested span extends past EOF is
    counted (past-EOF only; a request that starts before 0 is just a normal
    leading-edge clip). The count is surfaced as a FAIL finding so a render
    cut off mid-mix can never pass by silently skipping its tail windows."""
    findings: list[Finding] = []
    null_cache: dict[int, float] = {}
    eof_truncated_count = 0

    with sf.SoundFile(str(render_path), "r") as fh:
        sr = float(fh.samplerate)
        half = max(2, int(round(CLICK_HALF_WINDOW_SEC * sr)))
        boundary_beats = [sec_to_arr(t, tempo_map) for t in boundaries_sec]

        for t_sec, bb in zip(boundaries_sec, boundary_beats):
            center = int(round(t_sec * sr))
            if center + half > fh.frames:
                eof_truncated_count += 1
                continue
            y = _read_window(fh, center, half)
            if y is None:
                continue
            metric = _boundary_metric(y)

            cands = []
            for delta in range(-NULL_BEATS_EACH_SIDE, NULL_BEATS_EACH_SIDE + 1):
                beat_pos = int(round(bb)) + delta
                if any(abs(beat_pos - bp) < NULL_EXCLUSION_BEATS
                       for bp in boundary_beats):
                    continue
                cands.append(beat_pos)

            null_metrics: list[float] = []
            for bp in cands:
                if bp in null_cache:
                    null_metrics.append(null_cache[bp])
                    continue
                bp_center = int(round(arr_to_sec(bp, tempo_map) * sr))
                if bp_center + half > fh.frames:
                    # No null_cache entry: a fake metric must never stand in
                    # for a real one on a later boundary's null set.
                    eof_truncated_count += 1
                    continue
                y0 = _read_window(fh, bp_center, half)
                if y0 is None:
                    continue
                m0 = _boundary_metric(y0)
                null_cache[bp] = m0
                null_metrics.append(m0)

            if len(null_metrics) < 8:
                continue
            null = np.asarray(null_metrics)
            null_max = float(null.max())
            null_mean = float(null.mean())
            null_std = float(null.std())
            z = (metric - null_mean) / (null_std + 1e-12)

            if (metric > CLICK_NULL_RATIO * null_max and z > CLICK_NULL_Z
                    and _click_shape(y)):
                t0 = max(0.0, t_sec - 0.005)
                findings.append(Finding(
                    check="boundary_click", level="FAIL",
                    t0=t0,
                    t1=max(t0 + 0.01, t_sec + 0.005),
                    beat0=bb - 0.01, beat1=bb + 0.01,
                    measured={"metric": float(metric),
                              "null_max": null_max,
                              "null_mean": null_mean,
                              "null_std": null_std,
                              "z": float(z)},
                    msg=("click-shaped discontinuity at boundary "
                         f"(z={z:.1f}, {metric:.3f} vs null max {null_max:.3f})"),
                ))

    if eof_truncated_count:
        findings.append(Finding(
            check="eof_truncated_reads", level="FAIL",
            t0=0.0, t1=0.0, beat0=0.0, beat1=0.0,
            measured={"count": eof_truncated_count},
            msg=(f"{eof_truncated_count} boundary window reads truncated by "
                 "EOF"),
        ))
    return findings


def _beat_step_db(beat_rms_db: np.ndarray, beat_idx: int) -> float:
    if beat_idx <= 0 or beat_idx >= len(beat_rms_db):
        return 0.0
    return float(beat_rms_db[beat_idx] - beat_rms_db[beat_idx - 1])


def check_level_cliff(loops: list[dict], beat_rms_db: np.ndarray,
                      tempo_map: TempoMap) -> list[Finding]:
    findings: list[Finding] = []
    for lp in loops:
        b = int(round(lp["insert_at_beat"]))
        if b <= 0 or b >= len(beat_rms_db):
            continue
        step = _beat_step_db(beat_rms_db, b)
        if step <= LEVEL_CLIFF_DB:
            findings.append(Finding(
                check="level_cliff", level="WARN",
                t0=arr_to_sec(b - 1, tempo_map),
                t1=arr_to_sec(b, tempo_map),
                beat0=float(b - 1), beat1=float(b),
                measured={"step_db": step,
                          "from_db": float(beat_rms_db[b - 1]),
                          "to_db": float(beat_rms_db[b])},
                msg=f"level-cliff splice into loop insert ({step:+.1f} dB)",
            ))
    return findings


def _source_rewind_beats(clips: list[dict],
                         exit_beat: float) -> float | None:
    """Best-effort: source position jump at the loop exit. Computed from the
    clip starting at exit_beat (the post-loop clip) and the source position
    the looping clip would have reached at the previous beat. Returns None
    when either side is missing or non-looping."""
    # Find clip starting at exit (the post-loop clip).
    post = None
    for c in clips:
        if abs(c["arr_start"] - exit_beat) < 1e-3:
            post = c
            break
    if post is None:
        return None
    # Find the looping clip that contains exit_beat-1 beat.
    pre = None
    for c in clips:
        if c["loop_on"] and c["arr_start"] < exit_beat - 1e-3 < c["arr_end"]:
            pre = c
            break
    if pre is None:
        return None
    iter_len = pre["loop_end"] - pre["loop_start"]
    if iter_len <= 0:
        return None
    # Position in pre-clip's loop at the end of its last full iteration.
    elapsed = (exit_beat - pre["arr_start"])
    within = ((elapsed - 1e-9) % iter_len)
    pre_source = pre["loop_start"] + within
    post_source = post["loop_start"] + post["start_relative"]
    return float(pre_source - post_source)


def check_loop_exit_jump(loops: list[dict], beat_rms_db: np.ndarray,
                         clips: list[dict],
                         tempo_map: TempoMap) -> list[Finding]:
    findings: list[Finding] = []
    for lp in loops:
        e = int(round(lp["insert_at_beat"] + lp["total_beats"]))
        if e <= 0 or e >= len(beat_rms_db):
            continue
        step = _beat_step_db(beat_rms_db, e)
        if step >= LOOP_EXIT_JUMP_DB:
            measured = {"step_db": step,
                        "from_db": float(beat_rms_db[e - 1]),
                        "to_db": float(beat_rms_db[e])}
            rewind = _source_rewind_beats(clips, e)
            if rewind is not None:
                measured["source_rewind_beats"] = rewind
            findings.append(Finding(
                check="loop_exit_jump", level="WARN",
                t0=arr_to_sec(e - 1, tempo_map),
                t1=arr_to_sec(e, tempo_map),
                beat0=float(e - 1), beat1=float(e),
                measured=measured,
                msg=(f"jump-back splice at loop exit ({step:+.1f} dB) "
                     "source rewinds"),
            ))
    return findings


def _autocorr_at_lag(values: np.ndarray, lag: int) -> float:
    """Pearson r between the segment and its lag-k shift (mean-removed)."""
    if lag <= 0 or lag * 2 >= len(values):
        return 0.0
    a = values[:-lag]
    b = values[lag:]
    a = a - a.mean()
    b = b - b.mean()
    sa = a.std()
    sb = b.std()
    if sa < 1e-12 or sb < 1e-12:
        return 0.0
    return float(np.dot(a, b) / (sa * sb * len(a)))


def check_loop_period(loops: list[dict], beat_rms_db: np.ndarray,
                      beat_count: np.ndarray,
                      tempo_map: TempoMap) -> list[Finding]:
    findings: list[Finding] = []
    for lp in loops:
        iter_len = lp["iter_len"]
        a_beat = lp["insert_at_beat"]
        b_beat = lp["insert_at_beat"] + lp["total_beats"]
        if iter_len not in LOOP_PERIOD_OK:
            measured = {"iter_len": float(iter_len)}
            r_val = None
            a_idx = int(round(a_beat))
            b_idx = int(round(b_beat))
            count = max(1, round(lp["total_beats"] / iter_len))
            if count >= 2 and b_idx - a_idx > iter_len:
                # dB-domain per-beat values: reproduces the prototype exactly
                # (V10 Nappp lag 12 r=1.00, lag 16 r=0.72); linear energy
                # compresses the quiet beats and reads 0.49 at lag 16.
                r_val = _autocorr_at_lag(beat_rms_db[a_idx:b_idx].astype(float),
                                         int(round(iter_len)))
                measured["r"] = r_val
                measured["count"] = int(count)
            findings.append(Finding(
                check="loop_period", level="WARN",
                t0=arr_to_sec(a_beat, tempo_map),
                t1=arr_to_sec(b_beat, tempo_map),
                beat0=a_beat, beat1=b_beat,
                measured=measured,
                msg=(f"off-phrase loop length ({iter_len:g} beats, "
                     f"{'r=' + f'{r_val:.2f}' if r_val is not None else 'no autocorr'})"),
            ))
    return findings


def check_exposed_solo(rms100_db: np.ndarray, fps: int,
                       clips: list[dict],
                       tempo_map: TempoMap) -> list[Finding]:
    """THE D5 CALL, judged and recorded: V10's 22:15.5-22:21.5 floor is
    -46.7 dBFS -- in a car that reads as silence, and it IS Sam's second
    ear-note. But it is not digital silence: it sits 13+ dB above the -60
    hard-silence FAIL floor, and the render reproduces the source breakdown
    faithfully -- the defect is that the PLAN leaves Nappp's own break fully
    exposed with nothing layered under it. A plan-level musical decision the
    gate must surface for Sam's ear, never hard-FAIL the render for. WARN."""
    findings: list[Finding] = []
    # Solo intervals from the clip list; the >3 s gate applies to the quiet
    # sub-runs inside them.
    runs = solo_runs(clips, tempo_map, 0.0)
    for t0_sec, t1_sec in runs:
        i0 = max(0, int(round(t0_sec * fps)))
        i1 = min(len(rms100_db), int(round(t1_sec * fps)))
        if i1 - i0 <= 1:
            continue
        seg = rms100_db[i0:i1]
        quiet = seg < EXPOSED_SOLO_DB
        i = 0
        while i < len(quiet):
            if not quiet[i]:
                i += 1
                continue
            j = i
            while j < len(quiet) and quiet[j]:
                j += 1
            q_t0 = (i0 + i) / fps
            q_t1 = (i0 + j) / fps
            if q_t1 - q_t0 > EXPOSED_SOLO_MIN_S:
                floor = float(seg[i:j].min())
                findings.append(Finding(
                    check="exposed_solo", level="WARN",
                    t0=q_t0, t1=q_t1,
                    beat0=sec_to_arr(q_t0, tempo_map),
                    beat1=sec_to_arr(q_t1, tempo_map),
                    measured={"floor_db": floor,
                              "duration_s": float(q_t1 - q_t0)},
                    msg=(f"near-silent source material exposed solo "
                         f"({q_t1 - q_t0:.1f}s, floor {floor:.1f} dBFS)"),
                ))
            i = j
    return findings


def check_loop_hole(loops: list[dict], beat_rms_db: np.ndarray,
                    tempo_map: TempoMap) -> list[Finding]:
    findings: list[Finding] = []
    for lp in loops:
        iter_len = lp["iter_len"]
        a_beat = lp["insert_at_beat"]
        b_beat = lp["insert_at_beat"] + lp["total_beats"]
        count = max(1, round(lp["total_beats"] / iter_len))
        if count < 2:
            continue
        a_idx = int(round(a_beat))
        b_idx = int(round(b_beat))
        span = beat_rms_db[a_idx:b_idx]
        n_iters = int((b_idx - a_idx) // iter_len)
        if n_iters < 2:
            continue
        # Fold: template[i] = median across iterations of span[i + k*iter_len].
        span_len = len(span)
        template = []
        for i in range(int(iter_len)):
            vals = []
            for k in range(n_iters):
                local_pos = i + k * int(iter_len)
                if local_pos < span_len:
                    vals.append(span[local_pos])
            if vals:
                template.append(float(np.median(vals)))
            else:
                template.append(FLOOR_DB)
        if not template:
            continue
        tmax = max(template)
        tmin = min(template)
        if tmax - tmin < LOOP_HOLE_SPREAD_DB:
            continue
        quiet_thresh = tmax - LOOP_HOLE_QUIET_DB
        # Run-length scan for >= 2 consecutive quiet beats.
        run = 0
        max_run = 0
        for v in template:
            if v < quiet_thresh:
                run += 1
                max_run = max(max_run, run)
            else:
                run = 0
        if max_run >= LOOP_HOLE_MIN_CONSEC:
            findings.append(Finding(
                check="loop_hole", level="WARN",
                t0=arr_to_sec(a_beat, tempo_map),
                t1=arr_to_sec(b_beat, tempo_map),
                beat0=a_beat, beat1=b_beat,
                measured={"spread_db": tmax - tmin,
                          "quiet_run_beats": max_run,
                          "template": template},
                msg=("loop repeats an internal level hole every iteration "
                     f"(spread {tmax - tmin:.1f} dB, "
                     f"{max_run}-beat hole)"),
            ))
    return findings


def check_transition_dip(transitions: list[dict], st_lufs: np.ndarray,
                         fps: int, tempo_map: TempoMap) -> list[Finding]:
    findings: list[Finding] = []
    for tr in transitions:
        swap = tr["swap_beats"]
        start_beat = swap
        end_beat = swap + TRANSITION_DIP_SPAN_BEATS
        start_sec = arr_to_sec(start_beat, tempo_map)
        end_sec = arr_to_sec(end_beat, tempo_map)
        b0_sec = arr_to_sec(swap - TRANSITION_BASELINE_BEATS, tempo_map)
        b1_sec = arr_to_sec(swap - TRANSITION_BASELINE_GAP_BEATS, tempo_map)
        b0 = max(0, int(round(b0_sec * fps)))
        b1 = max(b0 + 1, int(round(b1_sec * fps)))
        i0 = max(0, int(round(start_sec * fps)))
        i1 = min(len(st_lufs), int(round(end_sec * fps)))
        if b1 - b0 < 4 or i1 - i0 < 1:
            continue
        baseline = float(np.median(st_lufs[b0:b1]))
        seg = st_lufs[i0:i1]
        if len(seg) == 0:
            continue
        dip_min = float(seg.min())
        dip = baseline - dip_min
        if dip > TRANSITION_DIP_DB:
            findings.append(Finding(
                check="transition_dip", level="WARN",
                t0=start_sec, t1=end_sec,
                beat0=start_beat, beat1=end_beat,
                measured={"baseline_lufs": baseline,
                          "dip_lufs": dip_min,
                          "dip_db": dip,
                          "pair_index": tr["pair_index"]},
                msg=(f"transition pair {tr['pair_index']} dips "
                     f"{dip:.1f} dB vs baseline ({baseline:.1f} LUFS)"),
            ))
    return findings


def _iteration_envelope(fh: sf.SoundFile, sr: float, tempo_map: TempoMap,
                        insert_beat: float, iter_len: float, k: int,
                        hop_ms: float = 10.0) -> np.ndarray | None:
    """10 ms RMS envelope of iteration k, read at EXACT sample offsets.
    Sample-aligned on purpose: slicing the 100 ms sweep frames instead puts
    each iteration at a different phase within a frame (a beat is not a
    whole number of frames), which wrecks the correlation at kick attacks --
    measured on V10 it reads r 0.29-0.91 where the aligned envelope reads
    0.95-0.99 (the prototype's numbers)."""
    start_beat = insert_beat + k * iter_len
    end_beat = start_beat + iter_len
    s0 = int(round(arr_to_sec(start_beat, tempo_map) * sr))
    s1 = int(round(arr_to_sec(end_beat, tempo_map) * sr))
    n = s1 - s0
    if s0 < 0 or s0 + n > fh.frames or n <= 0:
        return None
    fh.seek(s0)
    y = fh.read(n, dtype="float64", always_2d=True)
    e = (y ** 2).sum(axis=1) / y.shape[1]
    hop = max(1, int(round(sr * hop_ms / 1000.0)))
    m = len(e) // hop
    if m < 8:
        return None
    return np.sqrt(e[:m * hop].reshape(m, hop).mean(axis=1))


def check_loop_verbatim(render_path: Path, loops: list[dict],
                        tempo_map: TempoMap) -> list[Finding]:
    findings: list[Finding] = []
    eof_truncated_count = 0
    with sf.SoundFile(str(render_path), "r") as fh:
        sr = float(fh.samplerate)
        for lp in loops:
            iter_len = lp["iter_len"]
            count = max(1, round(lp["total_beats"] / iter_len))
            if count < 2:
                continue
            envs = []
            for k in range(count):
                start_beat = lp["insert_at_beat"] + k * iter_len
                end_beat = start_beat + iter_len
                s0 = int(round(arr_to_sec(start_beat, tempo_map) * sr))
                s1 = int(round(arr_to_sec(end_beat, tempo_map) * sr))
                n = s1 - s0
                # past-EOF reads are counted as truncations; the
                # _iteration_envelope helper also returns None for very short
                # (m < 8) envelopes, which is NOT an EOF issue - we only
                # count when s0 + n > fh.frames.
                if s0 + n > fh.frames:
                    eof_truncated_count += 1
                    continue
                e = _iteration_envelope(fh, sr, tempo_map,
                                        lp["insert_at_beat"], iter_len, k)
                if e is None:
                    break
                envs.append(e)
            if len(envs) < 2:
                continue
            min_len = min(len(e) for e in envs)
            envs = [e[:min_len] for e in envs]
            rs: list[float] = []
            for k in range(len(envs) - 1):
                a, b = envs[k], envs[k + 1]
                if a.std() < 1e-12 or b.std() < 1e-12:
                    continue
                r = float(np.corrcoef(a, b)[0, 1])
                rs.append(r)
            if not rs:
                continue
            min_r = min(rs)
            if min_r < LOOP_VERBATIM_MIN_R:
                findings.append(Finding(
                    check="loop_verbatim", level="FAIL",
                    t0=arr_to_sec(lp["insert_at_beat"], tempo_map),
                    t1=arr_to_sec(lp["insert_at_beat"] + lp["total_beats"],
                                  tempo_map),
                    beat0=lp["insert_at_beat"],
                    beat1=lp["insert_at_beat"] + lp["total_beats"],
                    measured={"min_r": min_r, "iters": int(count),
                              "rs": [round(r, 3) for r in rs]},
                    msg=(f"loop does not repeat verbatim (min r={min_r:.2f} "
                         f"across {count} iterations)"),
                ))
    if eof_truncated_count:
        findings.append(Finding(
            check="eof_truncated_reads", level="FAIL",
            t0=0.0, t1=0.0, beat0=0.0, beat1=0.0,
            measured={"count": eof_truncated_count},
            msg=(f"{eof_truncated_count} loop iteration reads truncated by "
                 "EOF"),
        ))
    return findings


def _pick_solo_regions(clips: list[dict], tempo_map: TempoMap,
                       targets_pct: list[float]) -> list[tuple[float, float]]:
    """Solo runs (one active clip) >= GRID_FOLD_REGION_S, pick the one nearest
    each target percentage of the arrangement span. Distinct picks only: if
    every run is already chosen for an earlier target, skip the later target.
    Without the dedup, a single eligible solo run masquerades as multiple
    probes and reports zero drift (one region, three identical phase medians)."""
    arr_start, arr_end = clip_arr_span(clips)
    arr_start_sec = arr_to_sec(arr_start, tempo_map)
    arr_dur = arr_to_sec(arr_end, tempo_map) - arr_start_sec
    runs = solo_runs(clips, tempo_map, GRID_FOLD_REGION_S)
    if not runs:
        return []
    out: list[tuple[float, float]] = []
    chosen: set[tuple[float, float]] = set()
    for pct in targets_pct:
        target = arr_start_sec + pct * arr_dur
        remaining = [r for r in runs if r not in chosen]
        if not remaining:
            continue
        best = min(remaining, key=lambda r: abs(((r[0] + r[1]) / 2) - target))
        out.append(best)
        chosen.add(best)
    return out


def _grid_fold_median(path: Path, t0_sec: float, dur_sec: float,
                      tempo_map: TempoMap) -> float:
    """Lazy librosa: load mono at 22050, lowpass 150 Hz, fold onsets, return
    median phase in ms."""
    import librosa
    dur = min(dur_sec, GRID_FOLD_PROBE_S)
    y, sr = librosa.load(str(path), sr=22050, mono=True,
                         offset=t0_sec, duration=dur)
    sos = butter(4, 150.0, btype="low", fs=sr, output="sos")
    y_low = sosfiltfilt(sos, y)
    env = librosa.onset.onset_strength(y=np.ascontiguousarray(y_low), sr=sr)
    on = librosa.onset.onset_detect(onset_envelope=env, sr=sr, units="time",
                                    backtrack=False)
    if len(on) < 5:
        return 0.0
    times = on + t0_sec
    onset_beats = sec_to_arr(times, tempo_map)
    beat_dur = 60.0 / tempo_map.bpm_at(float(onset_beats.mean()))
    phase_ms = ((onset_beats + 0.5) % 1.0 - 0.5) * beat_dur * 1000.0
    # Dominant-cluster median, not the raw median: solo house program has
    # offbeat percussion whose onsets fold to +/-half-beat and drag the raw
    # median tens of ms off (V10 region at 7:13 reads -31.9 raw vs +49.6
    # clustered while its neighbours cluster at +47.9/+50.9 -- the cluster
    # is the kick lattice, the raw median is the hat pattern). Same
    # histogram logic as probe_render_flam.py.
    half_beat_ms = beat_dur * 500.0
    hist, edges = np.histogram(phase_ms, bins=47,
                               range=(-half_beat_ms, half_beat_ms))
    centers = (edges[:-1] + edges[1:]) / 2.0
    mode_c = centers[int(hist.argmax())]
    near = phase_ms[np.abs(phase_ms - mode_c) <= 60.0]
    if len(near) < 5:
        return float(np.median(phase_ms))
    return float(np.median(near))


def check_grid_fold(render_path: Path, clips: list[dict],
                    tempo_map: TempoMap) -> list[Finding]:
    """Up to 3 solo regions at 15/50/85% of the arrangement, >= 45 s each.
    Drift = max - min of median onset phase across regions; > 30 ms -> FAIL.
    The constant librosa bias is fine; only drift fails."""
    findings: list[Finding] = []
    regions = _pick_solo_regions(clips, tempo_map, [0.15, 0.5, 0.85])
    if len(regions) < 2:
        findings.append(Finding(
            check="grid_fold", level="INFO",
            t0=0.0, t1=0.0,
            beat0=0.0, beat1=0.0,
            measured={"regions": len(regions)},
            msg="grid_fold skipped (fewer than 2 solo regions >= 45 s)",
        ))
        return findings
    medians: list[float] = []
    used: list[tuple[float, float]] = []
    for t0, t1 in regions:
        try:
            m = _grid_fold_median(render_path, t0, t1 - t0, tempo_map)
        except Exception:
            continue
        medians.append(m)
        used.append((t0, t1))
    if len(medians) < 2:
        findings.append(Finding(
            check="grid_fold", level="INFO",
            t0=0.0, t1=0.0,
            beat0=0.0, beat1=0.0,
            measured={"regions": len(medians)},
            msg="grid_fold skipped (probe failure)",
        ))
        return findings
    drift = max(medians) - min(medians)
    measured = {"medians_ms": medians,
                "regions": [(round(t0, 1), round(t1, 1)) for t0, t1 in used],
                "drift_ms": drift,
                "tempo_map_flat": tempo_map.is_flat}
    if drift > GRID_FOLD_DRIFT_MS:
        # GRID_FOLD_DRIFT_MS was calibrated on a flat-tempo render, where the
        # beat<->time map is exact. On a tempo ARC two confounds are measured
        # and neither is a render defect: the map carries a ~19.5 ppm residual
        # of unknown origin (96 ms over an 84-minute mix, fitted across all 16
        # solo runs at R^2 0.969), and the probes land on DIFFERENT tracks
        # whose kick attacks read at different phases. V16 measures 53.2 ms
        # with a correct map against a 30 ms threshold. So on an arc the
        # number is still worth surfacing - a badly wrong map reads 101.9 ms,
        # which is plainly distinguishable - but it must not FAIL a render on
        # a threshold that does not yet mean anything here. Downgrade, say so,
        # and keep the measurement visible. Restore FAIL once the residual is
        # explained against a render made from a known-identical ALS.
        arc = not tempo_map.is_flat
        findings.append(Finding(
            check="grid_fold", level="WARN" if arc else "FAIL",
            t0=used[0][0], t1=used[-1][1],
            beat0=sec_to_arr(used[0][0], tempo_map),
            beat1=sec_to_arr(used[-1][1], tempo_map),
            measured=measured,
            msg=(f"beat grid drifts across the render ({drift:.1f} ms)"
                 + ("; threshold not yet calibrated for a tempo arc, so this "
                    "warns rather than fails" if arc else "")),
        ))
    else:
        findings.append(Finding(
            check="grid_fold", level="INFO",
            t0=used[0][0], t1=used[-1][1],
            beat0=sec_to_arr(used[0][0], tempo_map),
            beat1=sec_to_arr(used[-1][1], tempo_map),
            measured=measured,
            msg=f"beat grid consistent (drift {drift:.1f} ms)",
        ))
    return findings


def check_kick_flam(*args, **kwargs) -> list[Finding]:
    """SHIPS DISABLED. Ported fold/two-cluster logic from
    Source/probe_render_flam.py but not invoked from run_check: the probe
    has a proven false-positive mode on shuffled/percussive program - the
    2026-08-20 control run on a no-transition window (Renegades solo)
    produced the identical two-cluster signature from single-track
    percussion - so it must not accuse a transition until it has a
    per-track control baseline (flag only if the cluster is present in the
    overlap AND absent in both tracks' solo control windows). See
    Documentation/Reviews/2026-08-20 First Render Check - Mix V10.md."""
    return []


# --------------------------------------------------------------------------- #
# Orchestrator                                                                #
# --------------------------------------------------------------------------- #

def run_check(render_path: Path, report_path: Path,
              als_path: Path) -> CheckResult:
    render_path = Path(render_path)
    report_path = Path(report_path)
    als_path = Path(als_path)

    # Fail closed when the tempo envelope cannot produce a trustworthy map.
    # Running any audio check with a partial or invalid map would silently
    # mis-check the render.
    try:
        tempo_map, clips = parse_als(als_path)
    except TempoAutomationUnsupported as e:
        distinct = (list(e.distinct_values)
                    if e.distinct_values is not None else [])
        finding = Finding(
            check="tempo_automation_unsupported", level="FAIL",
            t0=0.0, t1=0.0, beat0=0.0, beat1=0.0,
            measured={"distinct_tempo_values": distinct},
            msg=f"tempo envelope cannot be mapped safely: {e}",
        )
        return CheckResult(
            findings=[finding],
            verdict="FAIL",
            exit_code=2,
            meta={
                "render": str(render_path),
                "verdict": "FAIL",
                "integrated_lufs": FLOOR_DB,
                "duration_sec": 0.0,
                "v_suffix": derive_v_suffix(render_path, report_path),
            },
        )
    loops, transitions, missing = parse_report(report_path)

    findings: list[Finding] = []

    # Surface missing report keys. A missing key means a whole class of
    # checks (loops or transitions) was disabled; an empty list is an
    # explicit "checked, nothing found" and is NOT missing. SKIP does not
    # affect verdict. When both keys are missing, add a FAIL so the gate
    # exits 2 - "report had nothing, nothing was checked" is not a pass.
    for key in missing:
        findings.append(Finding(
            check="report_missing_" + key, level="SKIP",
            t0=0.0, t1=0.0, beat0=0.0, beat1=0.0,
            measured={},
            msg=(f"report has no '{key}' key - "
                 f"{key}-dependent checks skipped"),
        ))
    if len(missing) == 2:
        findings.append(Finding(
            check="report_empty", level="FAIL",
            t0=0.0, t1=0.0, beat0=0.0, beat1=0.0,
            measured={},
            msg=("report has neither 'loops' nor 'transitions' - "
                 "nothing was actually checked"),
        ))

    sweep = streaming_sweep(render_path, tempo_map)
    st_lufs = short_term_lufs(sweep.kms100)
    int_lufs = integrated_lufs(sweep.kms100)

    arr_start_b, arr_end_b = clip_arr_span(clips)
    arr_start_s = arr_to_sec(arr_start_b, tempo_map)
    arr_end_s = arr_to_sec(arr_end_b, tempo_map)
    one_beat = 60.0 / tempo_map.bpm_at(arr_end_b)
    fps = int(round(1.0 / HOP_SEC))

    # Truncation check: a render that ends before the arrangement end is
    # broken. Compare stream duration to arrangement end with a one-beat
    # tolerance (the last beat's worth of audio may legitimately be tail
    # silence the gate does not need). Keep running the other checks; more
    # findings are fine and the gate still wants to surface what it could
    # see.
    duration = float(sweep.meta.get("duration_sec", 0.0))
    if duration + one_beat < arr_end_s:
        shortfall = arr_end_s - duration
        findings.append(Finding(
            check="render_truncated", level="FAIL",
            t0=duration, t1=arr_end_s,
            beat0=sec_to_arr(duration, tempo_map), beat1=arr_end_b,
            measured={"duration_sec": duration,
                      "arr_end_sec": arr_end_s,
                      "shortfall_sec": shortfall},
            msg=(f"render ends {shortfall:.1f}s before the arrangement "
                 "end"),
        ))

    findings += check_hard_silence(sweep.rms100_db, fps, arr_start_s, arr_end_s)

    boundaries_sec = collect_boundaries(clips, loops, arr_end_b, tempo_map)
    if boundaries_sec and not tempo_map.is_flat:
        # A NAMED skip, never a silent pass. check_boundary_click inspects a
        # +/-CLICK_HALF_WINDOW_SEC (2 ms) window around each mapped boundary.
        # The map carries a measured ~19.5 ppm residual, so past roughly 100 s
        # into an arc render that window sits nowhere near the real splice:
        # the check would find nothing and report clean. That is a FALSE PASS
        # on the exact defect class it exists to catch, which is worse than
        # not running it. Unlike grid_fold it yields no usable number on an
        # arc, so it is skipped outright rather than downgraded.
        findings.append(Finding(
            check="boundary_click_skipped_tempo_arc", level="SKIP",
            t0=0.0, t1=0.0, beat0=0.0, beat1=0.0,
            measured={"boundaries": len(boundaries_sec),
                      "click_half_window_sec": CLICK_HALF_WINDOW_SEC},
            msg=("boundary_click skipped: the +/-2 ms window cannot be "
                 "trusted against a tempo-arc map with a ~19.5 ppm residual"),
        ))
    elif boundaries_sec:
        findings += check_boundary_click(render_path, boundaries_sec, tempo_map)

    findings += check_level_cliff(loops, sweep.beat_rms_db, tempo_map)
    findings += check_loop_exit_jump(loops, sweep.beat_rms_db, clips,
                                     tempo_map)
    findings += check_loop_period(loops, sweep.beat_rms_db, sweep.beat_count,
                                  tempo_map)
    findings += check_exposed_solo(sweep.rms100_db, fps, clips, tempo_map)
    findings += check_loop_hole(loops, sweep.beat_rms_db, tempo_map)
    findings += check_transition_dip(transitions, st_lufs, fps, tempo_map)
    findings += check_loop_verbatim(render_path, loops, tempo_map)
    findings += check_grid_fold(render_path, clips, tempo_map)
    # check_kick_flam is disabled; not invoked.

    levels_present = {f.level for f in findings}
    if "FAIL" in levels_present:
        verdict = "FAIL"
        exit_code = 2
    elif "WARN" in levels_present:
        verdict = "WARN"
        exit_code = 1
    else:
        verdict = "PASS"
        exit_code = 0

    meta = dict(sweep.meta)
    meta.update({
        "bpm": tempo_map.nominal_bpm,
        "tempo_map": tempo_map.summary,
        "clip_count": len(clips),
        "integrated_lufs": int_lufs,
        "verdict": verdict,
        "v_suffix": derive_v_suffix(render_path, report_path),
        "arr_start_sec": arr_start_s,
        "arr_end_sec": arr_end_s,
        "n_boundaries": len(boundaries_sec),
    })
    return CheckResult(findings=findings, verdict=verdict, exit_code=exit_code,
                       meta=meta)


# --------------------------------------------------------------------------- #
# Report writers                                                              #
# --------------------------------------------------------------------------- #

def _fmt_time(t: float) -> str:
    if t < 0:
        return "-"
    m = int(t // 60)
    s = t - 60 * m
    return f"{m}:{s:04.1f}"


def _round_finding(f: Finding) -> dict:
    return {
        "check": f.check,
        "level": f.level,
        "t0": round(f.t0, 1),
        "t1": round(f.t1, 1),
        "beat0": round(f.beat0, 2),
        "beat1": round(f.beat1, 2),
        "measured": f.measured,
        "msg": f.msg,
    }


def write_report(result: CheckResult, render_path: Path,
                 json_out: Path | None = None) -> tuple[Path, Path]:
    v_suffix = result.meta.get("v_suffix") or ""
    base = f"RENDER_CHECK_{v_suffix}" if v_suffix else "RENDER_CHECK"
    md_path = render_path.parent / f"{base}.md"
    js_path = json_out if json_out else (render_path.parent / f"{base}.json")

    md: list[str] = []
    md.append(f"# Render Check {v_suffix}\n")
    md.append(f"- render: `{result.meta.get('render', '')}`")
    md.append(f"- duration: {result.meta.get('duration_sec', 0):.2f} s")
    md.append(f"- sr / channels: {result.meta.get('sr', 0):.0f} Hz / "
              f"{result.meta.get('channels', 0)} ch")
    md.append(f"- BPM: {result.meta.get('bpm', 0):.3f}")
    md.append(f"- clip count: {result.meta.get('clip_count', 0)}")
    md.append(f"- integrated LUFS: {result.meta.get('integrated_lufs', -120):.2f}")
    md.append(f"- verdict: **{result.verdict}** (exit {result.exit_code})\n")

    md.append("## Findings\n")
    md.append("| # | time | arr beat | check | level | measured | note |")
    md.append("|---|------|----------|-------|-------|----------|------|")
    sorted_f = sorted(result.findings, key=lambda f: (f.t0, f.level))
    for i, f in enumerate(sorted_f, 1):
        time = (f"{_fmt_time(f.t0)}" if f.t1 <= f.t0 + 0.05
                else f"{_fmt_time(f.t0)} - {_fmt_time(f.t1)}")
        bts = (f"{f.beat0:.2f}" if f.beat1 <= f.beat0 + 0.02
               else f"{f.beat0:.2f} - {f.beat1:.2f}")
        def _fmt_val(v):
            if isinstance(v, float):
                return f"{v:.2f}"
            if isinstance(v, list):
                return "[" + ", ".join(_fmt_val(x) for x in v) + "]"
            return str(v)
        measured = ", ".join(f"{k}={_fmt_val(v)}" for k, v in f.measured.items())
        md.append(f"| {i} | {time} | {bts} | {f.check} | {f.level} | "
                  f"{measured} | {f.msg} |")

    all_checks = {"hard_silence", "boundary_click", "level_cliff",
                  "loop_exit_jump", "loop_period", "exposed_solo",
                  "loop_hole", "transition_dip", "loop_verbatim",
                  "grid_fold", "kick_flam", "eof_truncated_reads"}
    fired = {f.check for f in result.findings}
    clean = sorted(all_checks - fired - {"kick_flam"})
    md.append("\n## Checks run clean\n")
    md.append(", ".join(clean) if clean else "(none)")

    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    payload = {
        "meta": result.meta,
        "verdict": result.verdict,
        "exit_code": result.exit_code,
        "findings": [_round_finding(f) for f in result.findings],
    }
    js_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return md_path, js_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("render", type=Path)
    p.add_argument("report", type=Path)
    p.add_argument("als", type=Path)
    p.add_argument("--json-out", type=Path, default=None)
    args = p.parse_args(argv)

    # Wrap the run-and-report block: any operational exception (missing files,
    # bad ALS, IO errors, etc.) must exit 2 (FAIL). Without this guard, an
    # unhandled exception exits with code 1, which mix.md defines as a
    # non-blocking WARN -- a gate that could not run must read as FAIL.
    # KeyboardInterrupt/SystemExit are deliberately not caught (Exception alone
    # excludes them).
    try:
        result = run_check(args.render, args.report, args.als)
        write_report(result, args.render, args.json_out)
        print(f"verdict={result.verdict} exit={result.exit_code} "
              f"findings={len(result.findings)}")
        return result.exit_code
    except Exception as e:
        err_result = CheckResult(
            findings=[Finding(
                check="gate_error", level="FAIL",
                t0=0.0, t1=0.0, beat0=0.0, beat1=0.0,
                measured={"error": type(e).__name__},
                msg=f"gate could not run: {e}",
            )],
            verdict="FAIL",
            exit_code=2,
            meta={
                "render": str(args.render),
                "verdict": "FAIL",
                "error": str(e),
            },
        )
        try:
            write_report(err_result, args.render, args.json_out)
        except Exception:
            # The render dir may not exist; the FAIL on stderr/stdout is the
            # contract, the report is best-effort.
            pass
        print(f"verdict=FAIL exit=2 gate-error: {type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
