"""Predict a bounce's per-band level from the SOURCE audio plus the .als,
without rendering anything.

WHY THIS EXISTS. Every other check in the pipeline is a prediction made by the
arithmetic that built the file, so the render gate deliberately measures the
BOUNCE instead. That is the right call for a gate, but it makes the bounce the
only way to find out what a change would do - and bouncing an 84-minute mix
costs Sam a manual export and 1.3 GB. This module is the other half: a
FEED-FORWARD model accurate enough to answer "what would happen if" before
anything is rendered.

It is not a replacement for the gate. The gate stays the authority on what a
render actually contains; this predicts what one WOULD contain.

THE MODEL. Ableton's mix is a linear sum here, so per-band power at an
arrangement instant is

    sum over tracks of  (master * mixer_trim)^2 * StereoGain(t)^2
                        * band_power(source audio, low-shelf applied)

where the source instant comes from the clip's own warp markers. The low shelf
is applied as a FILTER, not a per-band scalar - a shelf changes the low bands
and leaves the top alone, and treating it as a broadband gain gets both wrong.

WHAT MAKES IT LEGITIMATE, AND WHAT WOULD BREAK IT. The sum is only linear
because nothing in the path is dynamic: the master chain is empty and every
track's AutoFilters sit transparent and unautomated. Those are not assumptions
here - they are checked, and the model REFUSES rather than returning a number
it cannot stand behind. The pipeline can automate those filters (als_generator
carries lp_filter/hp_filter param keys), so the day it does, this must fail
loudly instead of silently drifting.

Measured against the real 14.08.26 Mix V16 bounce the underlying model tracks
the render to about 0.5 dB mean absolute error across bands, with a worst cell
near 1.8 dB - and at least one cell of that size was SIGN-INVERTED. So it is
good enough to rank and to reason with, and NOT good enough to size a
correction in a band where its own residual is that large. Callers get the
uncertainty alongside the number for exactly that reason.
"""
from __future__ import annotations

import gzip
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import sosfilt, sosfiltfilt

# Bands are the gate's, deliberately: a prediction that cannot be compared
# against a measurement is not much use.
from render_check import DIP_BANDS, TempoMap, _band_rms_db  # noqa: F401

# ChannelEq's low shelf. Ableton does not expose the corner in the ALS, so it
# is fixed here at the documented Channel EQ low-shelf corner. It matters:
# moving it changes which of the gate's bands the shelf touches.
SHELF_FC_HZ = 100.0
# ChannelEq LowShelfGain is a LINEAR AMPLITUDE RATIO, range [0.18, 5.6],
# i.e. -14.89 dB to +14.96 dB, with 1.0 = exactly 0 dB. Verified against the
# template XML and Ableton's documented +/-15 dB.
SHELF_MIN, SHELF_MAX = 0.18, 5.6
# A filter is treated as transparent only within these bounds.
FILTER_NEUTRAL_LP_HZ = 19000.0
FILTER_NEUTRAL_HP_HZ = 30.0
FILTER_NEUTRAL_RES = 0.05

# WHAT THIS MODEL IS ACTUALLY WORTH, PER BAND.
#
# The raw model over-predicts the low end by a FIXED amount. Measured against
# the real 14.08.26 Mix V16 bounce over ~280 probes spread across the whole 84
# minutes, the render sits below the prediction by a constant offset that grows
# as frequency falls and vanishes above about 150 Hz.
#
# The mechanism is NOT known, and the following have each been eliminated by
# measurement rather than argument:
#   - it is not the AutoFilters. Every track carries a 20 kHz lowpass and a
#     20 Hz highpass, both modelled here; modelling them moved the numbers by
#     0.01 dB, because a highpass at 20 Hz does nothing at 50 Hz.
#   - it is not ChannelEq. Every parameter on every instance sits at unity and
#     its own highpass is off on all 30 tracks.
#   - it is not gain staging. Above 150 Hz the model matches the render to
#     0.1 dB, and a gain error would move every band together.
#   - it is not the warp engine. Both warp modes show it (repitch +3.1,
#     complex_pro +2.0), and the repitch tracks run at a playback ratio of
#     1.0002, i.e. essentially untouched audio.
# A solo passage shows the shape directly: the render is 3-4 dB down across
# 26-62 Hz, 1 dB at 60-90, and within 0.2 dB everywhere above 90 Hz. That is a
# fixed filter somewhere between the source file and the render which is not
# in the ALS device chain.
#
# So it is CALIBRATED OUT rather than explained away. The offsets below are
# medians (so a handful of outliers cannot set them) measured only where the
# band is actually present above -45 dB - the model's error on a silent band
# is real but nobody makes decisions from the sub level of silence, and
# including those probes inflated the apparent spread nearly fourfold.
#
# After calibration, every band lands under 0.5 dB mean error:
#
#     band     offset    MAE     p95    worst
#     sub      +2.76    0.46    1.09    12.05
#     bass     +0.85    0.31    1.03     2.22
#     lowmid   +0.15    0.31    0.92     4.23
#     mid      +0.01    0.19    0.68     2.19
#     high     +0.28    0.25    0.79     3.57
#
# THE CAVEAT THAT MATTERS: these come from ONE mix. Applying them elsewhere
# assumes the same fixed filter, which is plausible (it is present on every
# track and both warp modes) but unproven. Re-measure on a second bounce
# before trusting the low bands on unfamiliar material.
BAND_CALIBRATION_DB = {"sub": 2.76, "bass": 0.85, "lowmid": 0.15, "mid": 0.01,
                       "high": 0.28}
# Mean absolute error AFTER calibration.
BAND_MAE_DB = {"sub": 0.46, "bass": 0.31, "lowmid": 0.31, "mid": 0.19,
               "high": 0.25}
# 95th percentile error after calibration - the number to size against, since
# a mean hides the tail and the tail is what produces a wrong-sized fix.
BAND_P95_DB = {"sub": 1.09, "bass": 1.03, "lowmid": 0.92, "mid": 0.68,
               "high": 0.79}
# A correction must be at least this many times the band's 95th-percentile
# error before it is worth making. At 2x, a move is comfortably larger than
# the uncertainty behind it; below that the fix could be the wrong size, and
# near 1x it could be the wrong direction.
SIZING_MARGIN = 2.0


class ModelRefused(Exception):
    """The set contains something the model does not represent.

    Raised rather than returning a number, because a silently-wrong prediction
    is exactly the failure this project keeps having to unpick.
    """


@dataclass
class Clip:
    arr_start: float
    arr_end: float
    loop_start: float
    start_relative: float
    warp_beats: np.ndarray
    warp_secs: np.ndarray
    source: Path

    def source_sec(self, arr_beat: float) -> float | None:
        """Arrangement beat -> seconds into the source file, via warp markers."""
        if not (self.arr_start <= arr_beat < self.arr_end):
            return None
        clip_beat = self.loop_start + self.start_relative + (arr_beat - self.arr_start)
        return float(np.interp(clip_beat, self.warp_beats, self.warp_secs))


@dataclass
class Track:
    name: str
    mixer_trim: float
    clips: list[Clip] = field(default_factory=list)
    gain_env: list[tuple[float, float]] = field(default_factory=list)
    shelf_env: list[tuple[float, float]] = field(default_factory=list)
    # (kind, hz) per static AutoFilter, e.g. ("high", 20.0). These are NOT
    # decorative: a 24 dB/oct highpass at 20 Hz takes real energy out of a band
    # measured from 20 Hz up, and ignoring it over-predicted sub by ~3 dB at
    # every probe on V16.
    filters: list[tuple[str, float]] = field(default_factory=list)

    @staticmethod
    def _env_at(env: list[tuple[float, float]], beat: float, default: float) -> float:
        """Ableton holds the last value; envelopes are step-and-ramp, and the
        pipeline writes explicit points either side of every move, so linear
        interpolation between written points reproduces what Live plays."""
        if not env:
            return default
        beats = [b for b, _ in env]
        vals = [v for _, v in env]
        if beat <= beats[0]:
            return vals[0]
        if beat >= beats[-1]:
            return vals[-1]
        return float(np.interp(beat, beats, vals))

    def gain_at(self, beat: float) -> float:
        return self._env_at(self.gain_env, beat, 1.0)

    def shelf_at(self, beat: float) -> float:
        return self._env_at(self.shelf_env, beat, 1.0)

    def clip_at(self, beat: float) -> Clip | None:
        for c in self.clips:
            if c.arr_start <= beat < c.arr_end:
                return c
        return None


def _low_shelf_sos(gain_lin: float, sr: float, fc: float = SHELF_FC_HZ) -> np.ndarray:
    """RBJ low-shelf biquad as sos. gain_lin is the linear amplitude ratio the
    ALS stores, so 1.0 is a no-op and 0.18 is the -14.89 dB floor."""
    A = math.sqrt(max(gain_lin, 1e-6))
    w0 = 2.0 * math.pi * fc / sr
    alpha = math.sin(w0) / 2.0 * math.sqrt(2.0)
    cw = math.cos(w0)
    two_sqrt_a_alpha = 2.0 * math.sqrt(A) * alpha
    b0 = A * ((A + 1) - (A - 1) * cw + two_sqrt_a_alpha)
    b1 = 2 * A * ((A - 1) - (A + 1) * cw)
    b2 = A * ((A + 1) - (A - 1) * cw - two_sqrt_a_alpha)
    a0 = (A + 1) + (A - 1) * cw + two_sqrt_a_alpha
    a1 = -2 * ((A - 1) + (A + 1) * cw)
    a2 = (A + 1) + (A - 1) * cw - two_sqrt_a_alpha
    return np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]])


# --------------------------------------------------------------------------- #
# Signal-path guards                                                          #
# --------------------------------------------------------------------------- #

def _manual(el, tag: str) -> str | None:
    for c in el.iter(tag):
        m = c.find("Manual")
        if m is not None and m.get("Value") is not None:
            return m.get("Value")
    return None


def check_signal_path(root) -> None:
    """Refuse unless the path is the linear one the model assumes.

    Every clause here is a term the model does NOT carry. If any of them ever
    becomes live, a prediction would drift quietly rather than fail - which is
    the failure mode this project has spent the most time paying for.
    """
    main = next(root.iter("MainTrack"), None)
    if main is not None:
        for chain in main.iter("DeviceChain"):
            devs = [d.tag for dc in chain.iter("Devices") for d in dc]
            if devs:
                raise ModelRefused(
                    f"master chain is not empty ({', '.join(sorted(set(devs)))}); "
                    "a bus processor makes the sum non-linear and this model "
                    "cannot represent it")
            break

    # Which automation targets actually carry events?
    live_targets = set()
    for env in root.iter("AutomationEnvelope"):
        pid = env.find(".//PointeeId")
        if pid is None or not pid.get("Value"):
            continue
        if next(env.iter("FloatEvent"), None) is not None:
            live_targets.add(pid.get("Value"))

    for af in root.iter("AutoFilter2"):
        on = _manual(af, "On")
        freq = _manual(af, "Filter_Frequency")
        res = _manual(af, "Filter_Resonance")
        ids = {t.get("Id") for t in af.iter("AutomationTarget") if t.get("Id")}
        if ids & live_targets:
            raise ModelRefused(
                "an AutoFilter is automated; the model carries no filter term, "
                "so a swept filter would be silently absent from the prediction")
        # A STATIC filter is modelled rather than refused - see Track.filters.
        # Only the terms the model genuinely cannot carry are refused.
        if res is not None and abs(float(res)) > FILTER_NEUTRAL_RES:
            raise ModelRefused(
                f"an AutoFilter has resonance {res}; the model assumes none")


# --------------------------------------------------------------------------- #
# Loading                                                                     #
# --------------------------------------------------------------------------- #

@dataclass
class MixModel:
    master: float
    tracks: list[Track]
    tempo_map: TempoMap


def load_model(als_path: Path, audio_dir: Path | None = None) -> MixModel:
    """Parse the set into the terms the model needs, after checking the path."""
    als_path = Path(als_path)
    root = ET.fromstring(gzip.open(als_path, "rb").read())
    check_signal_path(root)

    tempo_map = TempoMap.from_als_root(root)

    master = 1.0
    main = next(root.iter("MainTrack"), None)
    if main is not None:
        for mixer in main.iter("Mixer"):
            v = _manual(mixer, "Volume")
            if v is not None:
                master = float(v)
            break

    # AutomationTarget Id -> (track name, which lane)
    target_lane: dict[str, tuple[str, str]] = {}
    tracks: list[Track] = []
    for tr in root.iter("AudioTrack"):
        name = next((n.get("Value", "") for n in tr.iter("EffectiveName")), "")
        trim = 1.0
        for mixer in tr.iter("Mixer"):
            v = _manual(mixer, "Volume")
            if v is not None:
                trim = float(v)
            break
        t = Track(name=name, mixer_trim=trim)
        for dev in tr.iter("StereoGain"):
            for p in dev.iter("Gain"):
                at = p.find("AutomationTarget")
                if at is not None and at.get("Id"):
                    target_lane[at.get("Id")] = (name, "gain")
            break
        for dev in tr.iter("ChannelEq"):
            for p in dev.iter("LowShelfGain"):
                at = p.find("AutomationTarget")
                if at is not None and at.get("Id"):
                    target_lane[at.get("Id")] = (name, "shelf")
            break
        for af in tr.iter("AutoFilter2"):
            if _manual(af, "On") != "true":
                continue
            fz = _manual(af, "Filter_Frequency")
            ft = _manual(af, "Filter_Type")
            if fz is None or ft is None:
                continue
            t.filters.append(("low" if ft == "0" else "high", float(fz)))
        for clip in tr.iter("AudioClip"):
            cs, ce = _manual(clip, "CurrentStart"), _manual(clip, "CurrentEnd")
            if cs is None or ce is None:
                cs = clip.findtext("CurrentStart") or None
                ce = clip.findtext("CurrentEnd") or None
            def _f(el, tag):
                n = el.find(tag)
                return float(n.get("Value")) if n is not None and n.get("Value") else None
            cs = _f(clip, "CurrentStart")
            ce = _f(clip, "CurrentEnd")
            if cs is None or ce is None:
                continue
            loop = clip.find("Loop")
            ls = _f(loop, "LoopStart") if loop is not None else 0.0
            sr_ = _f(loop, "StartRelative") if loop is not None else 0.0
            wms = sorted((float(w.get("SecTime")), float(w.get("BeatTime")))
                         for w in clip.iter("WarpMarker"))
            rel = next((e.get("Value") for e in clip.iter("RelativePath")), None)
            if len(wms) < 2 or rel is None:
                continue
            src = (audio_dir / Path(rel).name if audio_dir
                   else (als_path.parent / rel).resolve())
            t.clips.append(Clip(
                arr_start=cs, arr_end=ce,
                loop_start=ls or 0.0, start_relative=sr_ or 0.0,
                warp_beats=np.array([b for _, b in wms]),
                warp_secs=np.array([s for s, _ in wms]),
                source=src))
        tracks.append(t)

    by_name = {t.name: t for t in tracks}
    for env in root.iter("AutomationEnvelope"):
        pid = env.find(".//PointeeId")
        if pid is None or not pid.get("Value"):
            continue
        who = target_lane.get(pid.get("Value"))
        if not who:
            continue
        name, lane = who
        pts = []
        for fe in env.iter("FloatEvent"):
            try:
                pts.append((float(fe.get("Time")), float(fe.get("Value"))))
            except (TypeError, ValueError):
                continue
        # Ableton's "before all time" sentinel is a default, not a real point.
        pts = sorted(p for p in pts if p[0] > -1e6)
        if not pts:
            continue
        t = by_name.get(name)
        if t is None:
            continue
        (t.gain_env if lane == "gain" else t.shelf_env).extend(pts)
    for t in tracks:
        t.gain_env.sort()
        t.shelf_env.sort()
    return MixModel(master=master, tracks=tracks, tempo_map=tempo_map)


# --------------------------------------------------------------------------- #
# Prediction                                                                  #
# --------------------------------------------------------------------------- #

def _source_band_power(clip: Clip, src_sec: float, window_sec: float,
                       shelf: float, cache: dict,
                       filters: tuple = ()) -> dict | None:
    """Per-band power of one clip's source audio at an instant, shelf applied.

    The shelf is a FILTER applied to the audio before the bands are measured,
    which is the whole reason a low-shelf cut shows up in sub and bass and not
    in high.
    """
    key = (str(clip.source), round(src_sec, 3), round(window_sec, 3),
           round(shelf, 4), filters)
    if key in cache:
        return cache[key]
    try:
        info = sf.info(str(clip.source))
    except (OSError, RuntimeError):
        return None
    sr = float(info.samplerate)
    a = int((src_sec - window_sec / 2.0) * sr)
    b = int((src_sec + window_sec / 2.0) * sr)
    if a < 0 or b > info.frames or b - a < sr * 0.25:
        return None
    with sf.SoundFile(str(clip.source)) as fh:
        fh.seek(a)
        y = fh.read(b - a, dtype="float64", always_2d=True)
    from scipy.signal import butter
    if abs(shelf - 1.0) > 1e-6:
        # ONE pass, deliberately. The shelf models a ChannelEq parameter, and a
        # real device filters once and causally. sosfiltfilt runs the filter
        # forward AND backward, squaring its magnitude response and DOUBLING the
        # shelf's effect in dB - measured, band-integrated over sub: a
        # LowShelfGain of 0.52 designs to -5.5 dB and was being applied as
        # -10.1 dB, and 0.18 as -19.7 instead of -14.3.
        #
        # This was invisible for as long as the pipeline only ever WROTE 0.18 or
        # 1.0: at unity the shelf is not in the chain at all, and at the kill the
        # track's low bands sit 20+ dB below the other track's and contribute
        # essentially nothing to a summed prediction. It goes live the moment an
        # INTERMEDIATE value is written - which `two_stage_bass` already does
        # (EQ_BASS_PARTIAL = 0.52) and which the bass-residual sizing does by
        # design.
        sos = _low_shelf_sos(shelf, sr)
        y = np.stack([sosfilt(sos, y[:, c]) for c in range(y.shape[1])], 1)
    # NO warm-up lead is read before the window, and that is deliberate. A
    # causal biquad at 100 Hz settles in a few milliseconds, so over a 3 s
    # window the cold start is worth 0.03 dB on noise and 0.00 dB on kick-shaped
    # material - measured on four signal shapes. An earlier version of this fix
    # DID read a 1 s lead, on the strength of a 1.22 dB reading that turned out
    # to be a bug in the probe itself (it called sosfilt on a 2-D array without
    # an axis, so it filtered across the two CHANNELS instead of across time).
    # With shelf == 1.0 nothing above runs at all, so every unity-shelf
    # prediction is bit-identical to the pre-fix model - which is what keeps the
    # published band calibration valid.
    for kind, hz in filters:
        # Ableton slope 1 is 24 dB/oct, i.e. 4th order. Left zero-phase: these
        # sit at 20 Hz and 20 kHz where the double-application was measured to
        # move the result 0.01 dB, and changing them WOULD move every calibration
        # probe. Recorded as a known imprecision rather than fixed blind.
        sos = butter(4, hz, btype=("high" if kind == "high" else "low"),
                     fs=sr, output="sos")
        y = np.stack([sosfiltfilt(sos, y[:, c]) for c in range(y.shape[1])], 1)
    out = {}
    for name, lo, hi in DIP_BANDS:
        bs = (butter(4, lo, btype="high", fs=sr, output="sos") if hi is None
              else butter(4, [lo, hi], btype="band", fs=sr, output="sos"))
        ms = sum(float(np.mean(sosfiltfilt(bs, y[:, c]) ** 2))
                 for c in range(y.shape[1])) / y.shape[1]
        out[name] = ms
    cache[key] = out
    return out


def predict_bands(model: MixModel, arr_sec: float, window_sec: float = 3.0,
                  cache: dict | None = None) -> dict:
    """Predicted per-band level (dB) of the bounce at an arrangement instant.

    Sums power across tracks. Returns per-band dB plus the per-track power
    shares, because "which track owns this band right now" is usually the
    question worth asking.
    """
    cache = {} if cache is None else cache
    beat = float(model.tempo_map.sec_to_beat(arr_sec))
    totals = {n: 0.0 for n, _, _ in DIP_BANDS}
    shares: dict[str, dict] = {}
    for t in model.tracks:
        clip = t.clip_at(beat)
        if clip is None:
            continue
        g = t.gain_at(beat)
        if g <= 1e-6:
            continue
        src = clip.source_sec(beat)
        if src is None:
            continue
        bp = _source_band_power(clip, src, window_sec, t.shelf_at(beat), cache,
                                tuple(t.filters))
        if bp is None:
            continue
        scale = (model.master * t.mixer_trim * g) ** 2
        shares[t.name] = {n: bp[n] * scale for n in totals}
        for n in totals:
            totals[n] += bp[n] * scale
    return {
        # Calibrated: the raw model over-predicts the low end by a fixed
        # amount whose mechanism is not known but whose size is measured.
        "band_db": {n: 10.0 * math.log10(max(v, 1e-24))
                    - BAND_CALIBRATION_DB.get(n, 0.0)
                    for n, v in totals.items()},
        "band_db_uncalibrated": {n: 10.0 * math.log10(max(v, 1e-24))
                                 for n, v in totals.items()},
        # Every prediction carries what it is worth. A number without its
        # uncertainty is how a 3 dB sub bias becomes a 3 dB correction.
        "uncertainty_db": dict(BAND_P95_DB),
        "shares": shares,
        "beat": beat,
    }


def can_size_correction(band: str, correction_db: float) -> bool:
    """Is this correction big enough to be worth making, given the model's own
    error in that band?

    The useful question is not "is this band accurate" in the abstract - it is
    whether the move being contemplated is larger than the uncertainty behind
    it. A 4 dB correction in a band good to 1 dB is sound; a 0.8 dB correction
    in the same band is inside the noise and could be the wrong direction.
    """
    p95 = BAND_P95_DB.get(band)
    if p95 is None:
        return False
    return abs(correction_db) >= SIZING_MARGIN * p95


def band_uncertainty_db(band: str) -> float:
    """The number to put an error bar on a prediction with."""
    return BAND_P95_DB.get(band, 99.0)
