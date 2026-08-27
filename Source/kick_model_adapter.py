"""Optional Kick Detector V3 adapter for stem section detection.

This module is deliberately lazy: importing it must not import torch, demucs,
or the sibling Kick Detector project. Those heavy dependencies are loaded only
when the --kick-model path is explicitly enabled.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf


MODEL_FILENAME = "kick_crnn_V3.pt"
DEFAULT_THRESHOLD = 0.30
DEFAULT_FILL_OFF_BEATS = 6
DEFAULT_DROP_ON_BEATS = 1
DEMUCS_MODEL_NAME = "htdemucs"
DRUMS_CACHE_VERSION = 1

_MODEL_MODULE = None
_PRESENCE_MODULE = None
_DEMUCS_MODEL = None
_PROVIDER_CACHE = {}


@dataclass(frozen=True)
class KickPresenceReadout:
    raw: np.ndarray
    section: np.ndarray


def default_kick_detector_root() -> Path:
    """Sibling project location under the shared project hub."""
    return Path(__file__).resolve().parents[1].parent / "Kick Detector"


def default_model_path() -> Path:
    return default_kick_detector_root() / "Models" / MODEL_FILENAME


def _load_model_module(root: Path):
    global _MODEL_MODULE
    if _MODEL_MODULE is not None:
        return _MODEL_MODULE
    model_py = root / "Source" / "model.py"
    if not model_py.exists():
        raise FileNotFoundError(f"Kick Detector model.py not found: {model_py}")
    spec = importlib.util.spec_from_file_location("kickdet_model", model_py)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load Kick Detector model module from {model_py}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MODEL_MODULE = module
    return module


def _load_presence_module(root: Path):
    global _PRESENCE_MODULE
    if _PRESENCE_MODULE is not None:
        return _PRESENCE_MODULE
    pp_py = root / "Source" / "presence_postprocess.py"
    if not pp_py.exists():
        raise FileNotFoundError(f"Kick Detector presence_postprocess.py not found: {pp_py}")
    spec = importlib.util.spec_from_file_location("kickdet_presence_postprocess", pp_py)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load Kick Detector presence module from {pp_py}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _PRESENCE_MODULE = module
    return module


def _auto_device() -> str:
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def _demucs_model(device: str):
    global _DEMUCS_MODEL
    if _DEMUCS_MODEL is None:
        from demucs.pretrained import get_model
        _DEMUCS_MODEL = get_model(DEMUCS_MODEL_NAME)
        _DEMUCS_MODEL.to(device).eval()
    return _DEMUCS_MODEL


def _fit_length(on: np.ndarray, n_beats: int | None) -> np.ndarray:
    if n_beats is None:
        return np.asarray(on, dtype=bool)
    out = np.asarray(on, dtype=bool)
    if len(out) < n_beats:
        out = np.pad(out, (0, n_beats - len(out)), constant_values=False)
    elif len(out) > n_beats:
        out = out[:n_beats]
    return out


def _env(mono: np.ndarray, hop: int) -> np.ndarray:
    nfr = len(mono) // hop
    fr = mono[: nfr * hop].reshape(nfr, hop)
    return np.sqrt((fr.astype(np.float64) ** 2).mean(axis=1) + 1e-12)


def _drums_cache_path(wav_path: Path, cache_dir: Path) -> Path:
    return cache_dir / f"{wav_path.stem}__drumsstem.npz"


def _content_fingerprint(wav_path: Path) -> str | None:
    """Full-content sha1 - the file's actual identity, not a proxy for it.

    Exists because mtime alone made the cache useless the moment a track was
    COPIED. Building a mix subset copies tracks into `Audio Mix N/`, which
    preserves every byte and resets mtime, so each copied track re-separated
    from scratch (a courier lost ~10 minutes to exactly this on 2026-08-20).

    Measured before choosing whole-file over a cheaper edge sample: sha1 runs
    at ~1330 MB/s here, so fingerprinting all 20 masters of a corpus costs
    ~1.3 s against ~420 s of cold Demucs. An edge-sampled hash would have been
    marginally faster and carried a real blind spot - a same-size change in the
    middle of a file would fingerprint identical - so it was a false economy.
    """
    try:
        h = hashlib.sha1()
        with open(wav_path, "rb") as fh:
            while chunk := fh.read(1 << 20):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _save_drums_cache(wav_path: Path, cache_dir: Path, drums: np.ndarray, sr: int) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    st = os.stat(wav_path)
    final = _drums_cache_path(wav_path, cache_dir)
    tmp = cache_dir / f"{wav_path.stem}.tmp.npz"
    np.savez(
        tmp,
        drums=np.asarray(drums, dtype=np.float32),
        sr=np.array(int(sr)),
        src_size=np.array(int(st.st_size)),
        src_mtime_ns=np.array(int(st.st_mtime_ns)),
        src_fingerprint=np.array(_content_fingerprint(wav_path) or ""),
        demucs_model=np.array(DEMUCS_MODEL_NAME),
        cache_version=np.array(int(DRUMS_CACHE_VERSION)),
    )
    os.replace(tmp, final)


def _source_matches(d, wav_path: Path) -> bool:
    """Shared source-identity validation for the stem sidecars.

    Size must match outright (cheapest reject). A differing mtime is the shape
    a COPY leaves - building a mix subset copies tracks into `Audio Mix N/`,
    and under mtime-only validation every one of them re-separated from
    scratch - so it falls back to the full-content fingerprint, which is
    strictly STRONGER evidence than a timestamp: this only widens the hit
    rate, never loosens correctness. Caches written before the fingerprint
    existed carry "" and still require the exact mtime.
    """
    if not wav_path.exists():
        return False
    st = os.stat(wav_path)
    if int(st.st_size) != int(d["src_size"]):
        return False
    if int(st.st_mtime_ns) != int(d["src_mtime_ns"]):
        cached_fp = (str(d["src_fingerprint"])
                     if "src_fingerprint" in d.files else "")
        if not cached_fp:
            return False
        if _content_fingerprint(wav_path) != cached_fp:
            return False
    return True


def _load_drums_cache(wav_path: Path, cache_dir: Path) -> tuple[np.ndarray, int] | None:
    cache = _drums_cache_path(wav_path, cache_dir)
    if not cache.exists():
        return None
    try:
        with np.load(cache, allow_pickle=False) as d:
            for key in (
                "drums",
                "sr",
                "src_size",
                "src_mtime_ns",
                "demucs_model",
                "cache_version",
            ):
                if key not in d.files:
                    return None
            if int(d["cache_version"]) != DRUMS_CACHE_VERSION:
                return None
            if str(d["demucs_model"]) != DEMUCS_MODEL_NAME:
                return None
            if not _source_matches(d, wav_path):
                return None
            drums = np.asarray(d["drums"], dtype=np.float32)
            sr = int(d["sr"])
    except Exception:
        return None
    return drums, sr


# --------------------------------------------------------------------------- #
# Bass-stem sidecar (Sam, 2026-08-27: keep the bass on disk; payload choice   #
# "Opus encoded bytes inside the npz sounds like the win"). Same lifecycle    #
# as the drums sidecar; the PAYLOAD is Opus bytes stored inside the npz,      #
# with an int16-quantized fallback for machines whose libsndfile lacks OPUS,  #
# for effectively-silent stems, and for any encode/verify failure. Measured   #
# on a real master: 2.96 MB Opus vs 29.7 MB int16 vs 68.3 MB float32.         #
# Bytes-inside-npz rather than a .opus file so the "no mixable stem audio on  #
# disk" line stays honest: the sidecar is an analysis artifact, not a         #
# playable file anyone could mistake for a master. Every consumer of bass     #
# audio today is coarse (the grid repairer lowpasses to 200 Hz and votes      #
# with +/-90 ms tolerance), so codec loss is immaterial - but codec TIMING    #
# is not, which is why the saver proves each file's round trip below.         #
# --------------------------------------------------------------------------- #

BASS_CACHE_VERSION = 1

#: Opus operates at 48 kHz; libsndfile accepts no other rate for OGG/OPUS.
_OPUS_SR = 48000
#: The saver DECODES what it just encoded and requires this correlation
#: against the pre-encode signal, else it falls back to int16.
_OPUS_VERIFY_MIN_CORR = 0.98
#: Probe window RMS below this means the stem has no informative content to
#: verify against - fail CLOSED to int16 (Codex blocker: the old zero-energy
#: branch scored achieved=1.0 and passed silence straight through the gate).
_OPUS_PROBE_MIN_RMS = 1e-5

#: PAYLOAD CHOICE - ARBITRATED BY SAM, 2026-08-27: "flip the switch. 3 mb."
#: Opus payload ON (2.95 vs 29.7 MB/track). Context the next reader needs:
#: this flag exists because a 14-round adversarial review ended in a
#: standoff. The Opus path carries a 19-pin suite - pilot-chirp alignment
#: (matched-filter over a buffer that physically ends at the data boundary,
#: signed-response threshold, edge refusal, re-located on EVERY load),
#: length reconciliation, and up to five timeline-spread source-excerpt
#: anchors - refusing every REACHABLE decoder fault found in rounds 4-11
#: (asymmetric delay, trim, inversion, NaN, gain, forged/duplicated pilots,
#: net-shift splices). What no finite check can refuse - proven by
#: construction in rounds 12-14 - is surgical zero-net-length fabrication at
#: ever-finer scale by a decoder acting as a sample-level adversary, which
#: no real codec resembles. Codex held DON'T SHIP on that theoretical
#: residual; Claude held it unreachable; per the charter's 5-round cap the
#: call went to Sam, who accepted the residual for 10x smaller sidecars.
#: Flip to False to fall back to int16 (no decoder, no objection class) -
#: existing opus sidecars keep loading either way; the flag gates SAVES.
OPUS_PAYLOAD_ENABLED = True

# Alignment is NOT inferred from the bass - it is anchored to a known PILOT.
#
# Three attempts at inferring the codec delay from the signal itself each
# died in review (Codex, 2026-08-27): an unconstrained correlation argmax
# picks whole-period aliases on periodic basslines; a capped search with a
# null-lag preference was then killed by construction with a gated
# 153.846 Hz bass whose period EQUALS a genuine 312-sample delay - the null
# wins, verification still scores 0.99, and every note edge lands 6.5 ms
# late. The lesson is structural: alignment inference on periodic content is
# underdetermined, so no threshold fixes it.
#
# Instead the saver PREPENDS a known aperiodic chirp (plus guard silence)
# before encoding and locates it after decoding by matched filter. A chirp's
# autocorrelation is a single sharp peak, so the located position is exact
# regardless of what the bass does, and the loader's trim is derived from
# it - measured per file, inferred from nothing. A tail pad absorbs any
# decoder truncation so it can never eat audio.
_OPUS_PILOT_GUARD = 960                  # 20 ms silence each side of the chirp
_OPUS_PILOT_CHIRP = 2880                 # 60 ms linear chirp 200 -> 6000 Hz
_OPUS_PILOT_LEN = _OPUS_PILOT_GUARD * 2 + _OPUS_PILOT_CHIRP
_OPUS_TAIL_PAD = 4800                    # 100 ms decoder-truncation absorber
#: Largest decoder delay the locate will entertain (20 ms @ 48 kHz; Opus
#: pre-skip is ~6.5 ms, libsndfile measures 0, so this is still 3x reality).
#: LOAD-BEARING, with the exact inequality it must satisfy:
#:     GUARD + MAX_DELAY + CHIRP <= PILOT_LEN
#:     960   + 960       + 2880  =  4800  = PILOT_LEN
#: i.e. the LAST candidate's ENTIRE correlation span ends exactly where the
#: data begins. Codex round 7 killed the previous 40 ms value with this
#: inequality violated (5760 > 4800): bounding where candidates START is not
#: enough, because the 2880-sample span of the last candidate still READ 960
#: bass samples - and a bass head shaped like the chirp's tail outscored the
#: real pilot (1.0217E vs 0.9858E) for an accepted 40 ms misalignment under
#: perfectly ordinary decoding. With this bound the slice handed to the
#: correlator simply ends before the data: the proof is the array bound.
_OPUS_MAX_DECODER_DELAY = 960
#: The decoded pilot's matched-filter response must retain at least this
#: fraction of the template's own autocorrelation energy. An ABSOLUTE
#: threshold in known units - the chirp's energy is a constant of the
#: design - so it needs nothing from the surrounding signal. Measured
#: response on real material is 0.986, so the margin is ~2x; a response
#: below it means the decode is untrustworthy and the answer is a MISS.
_OPUS_PILOT_MIN_RESPONSE = 0.5


def _opus_pilot_chirp() -> np.ndarray:
    t = np.arange(_OPUS_PILOT_CHIRP, dtype=np.float64) / _OPUS_SR
    f0, f1, dur = 200.0, 6000.0, _OPUS_PILOT_CHIRP / _OPUS_SR
    phase = 2.0 * np.pi * (f0 * t + (f1 - f0) / (2.0 * dur) * t * t)
    return (0.5 * np.hanning(_OPUS_PILOT_CHIRP) * np.sin(phase)).astype(np.float32)


def _bass_cache_path(wav_path: Path, cache_dir: Path) -> Path:
    return cache_dir / f"{wav_path.stem}__bassstem.npz"


def _opus_available() -> bool:
    try:
        return "OPUS" in sf.available_subtypes("OGG")
    except Exception:
        return False


def _resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to:
        return x
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(sr_from, sr_to)
    return resample_poly(x, sr_to // g, sr_from // g).astype(np.float32)


def _encode_bass_opus(bass: np.ndarray, sr: int) -> dict | None:
    """Encode to Opus bytes and PROVE the round trip before trusting it.

    Encodes at 48 kHz (the only rate Opus accepts), then immediately decodes
    and cross-correlates against the pre-encode signal to MEASURE any codec
    delay; the measured lag is stored and stripped at load. Why measured and
    never assumed, in both directions: a synthetic pure-sine probe "measured"
    83 ms of delay - which turned out to be the correlation peak aliasing to
    five periods of the sine, not a codec property - while the first real
    track measured 0 samples (libsndfile strips Opus pre-skip itself). An
    assumed constant would have been wrong either way; a wrong lag here would
    bias every bass onset by up to the grid-repairer's whole +/-90 ms vote
    tolerance. Returns the npz payload fields, or None when the round trip
    fails its correlation gate (caller falls back to int16 - a worse size is
    better than a wrong signal).
    """
    import io
    x48 = _resample(bass, sr, _OPUS_SR)
    chirp = _opus_pilot_chirp()
    guard = np.zeros(_OPUS_PILOT_GUARD, dtype=np.float32)
    stream = np.concatenate(
        [guard, chirp, guard, x48,
         np.zeros(_OPUS_TAIL_PAD, dtype=np.float32)])
    buf = io.BytesIO()
    sf.write(buf, stream, _OPUS_SR, format="OGG", subtype="OPUS")
    raw = buf.getvalue()
    buf.seek(0)
    y48, sr_dec = sf.read(buf, dtype="float32", always_2d=False)
    if sr_dec != _OPUS_SR or len(y48) < _OPUS_PILOT_LEN + len(x48) // 2:
        return None

    data_start = _locate_pilot(y48)
    if data_start is None:
        return None                      # pilot not found: fail CLOSED

    # Verify content at the pilot-derived (exact) alignment, on the most
    # ENERGETIC 10 s - a track whose bass sits out the intro must not verify
    # against near-silence.
    win = min(len(x48), _OPUS_SR * 10)
    start = 0
    if len(x48) > win:
        sq = np.cumsum(x48.astype(np.float64) ** 2)
        start = int(np.argmax(sq[win:] - sq[:-win]))
    xp = x48[start:start + win]
    if float(np.sqrt((xp ** 2).mean())) < _OPUS_PROBE_MIN_RMS:
        return None                      # nothing informative to verify: int16
    b = y48[data_start + start:data_start + start + win]
    m = min(len(xp), len(b))
    if m < min(win, _OPUS_SR):
        return None
    a, b = xp[:m], b[:m]
    denom = float(np.sqrt((a * a).sum()) * np.sqrt((b * b).sum()))
    if denom <= 0:
        return None                      # uninformative round trip: fail CLOSED
    achieved = float((a * b).sum() / denom)
    if not np.isfinite(achieved) or achieved < _OPUS_VERIFY_MIN_CORR:
        return None
    # SOURCE-EXCERPT ANCHOR (Codex round 11). Every structural gate infers
    # alignment from the decoded stream, and a sufficiently perverse decoder
    # can always fabricate a stream that satisfies structure at a wrong
    # global offset (a later-forged pilot parked inside the length band; an
    # earlier forgery composed with tail truncation). The npz fields are the
    # one thing the decoder does not control - the same trust the int16
    # payload already rests on - so store 100 ms of the SOURCE itself, from
    # its most energetic window, and let the loader accept only a stream
    # that MATCHES it at the claimed alignment. Ground truth, not inference.
    # TIMELINE-COVERING ANCHORS (Codex round 13). One anchor proves only
    # local correspondence: a decoder that decodes faithfully through the
    # verified excerpt, splices in 100 ms of zeros after it and trims the
    # tail to balance passes every structural gate and one local anchor.
    # Anchors spread across the timeline - max-energy, mid-points, and one
    # pinned to the TAIL - mean any net-shift splice desyncs every anchor
    # after the splice point. What provably remains is fabrication confined
    # strictly BETWEEN adjacent anchors with zero net length change: not a
    # shift of served audio but invented interior content, which no codec
    # fault resembles - that narrowed fault model is stated here on purpose
    # (a full-stream check is the int16 payload, which trusts these same
    # npz bytes).
    ref_win = min(len(x48), _OPUS_TAIL_PAD)
    sq2 = np.cumsum(x48.astype(np.float64) ** 2)

    def _win_rms(s: int) -> float:
        e = sq2[min(len(x48) - 1, s + ref_win - 1)] - (sq2[s - 1] if s else 0.0)
        return float(np.sqrt(max(0.0, e) / ref_win))

    starts: list[int] = []
    if len(x48) > ref_win:
        starts.append(int(np.argmax(sq2[ref_win:] - sq2[:-ref_win])))
        for frac in (0.25, 0.5, 0.75):
            starts.append(int(frac * (len(x48) - ref_win)))
        starts.append(len(x48) - ref_win)          # the tail anchor
    else:
        starts.append(0)
    # Dedupe, keep only windows with verifiable energy. The floor is
    # RELATIVE to the stem's own loudest window, not absolute: on the first
    # real track the tail anchor landed on the outro fade (rms 5.4e-5 -
    # above any absolute floor, but pure codec-noise territory) and scored
    # 0.126 against a 0.9 gate, turning every load into a false MISS. An
    # anchor quieter than 2% of the loudest window cannot out-vote codec
    # noise, so it is dropped at SAVE rather than left to refuse at load.
    # Windows that fail here reduce coverage (splices confined beyond the
    # last surviving anchor join the stated between-anchors fabrication
    # residual); they never reduce correctness.
    rms_floor = max(_OPUS_PROBE_MIN_RMS, 0.02 * _win_rms(starts[0]))
    seen: list[int] = []
    for s in starts:
        s = max(0, min(s, len(x48) - ref_win))
        if all(abs(s - t) > ref_win // 2 for t in seen) and _win_rms(s) >= rms_floor:
            seen.append(s)
    if not seen:
        return None
    refs = np.stack([x48[s:s + ref_win] for s in seen]).astype(np.float16)
    if not np.all(np.isfinite(refs)):
        # float16 tops out at 65504: a source sample beyond it casts to inf,
        # and an infinite anchor poisons the load-side correlation into NaN,
        # which compares False against every threshold - fail-OPEN (Codex
        # round 12; unreachable from real Demucs output, whose samples live
        # near +/-1, but the guard costs nothing). An anchor that cannot
        # verify must not exist: int16.
        return None
    return {
        "payload": np.frombuffer(raw, dtype=np.uint8),
        "payload_format": np.array("opus"),
        "sr_encoded": np.array(int(_OPUS_SR)),
        "data_start_48": np.array(int(data_start)),
        "n_samples": np.array(int(len(bass))),
        "n48": np.array(int(len(x48))),   # encoded data length: the loader's
                                          # length-reconciliation anchor
        "anchor_starts": np.array(seen, dtype=np.int64),
        "anchor_refs": refs,
        "verify_corr": np.array(float(achieved)),
        "sr": np.array(int(sr)),
    }


def _locate_pilot(y48: np.ndarray) -> int | None:
    """Return the sample where the DATA begins, or None (fail closed).

    The search window is the whole construction. The real chirp starts at
    codec_delay + GUARD, and codec_delay is bounded by
    _OPUS_MAX_DECODER_DELAY, so every legitimate apex lies in
    [0, GUARD + MAX_DELAY]. The earliest any impostor chirp can START is the
    data region, PILOT_LEN + codec_delay. With MAX_DELAY < PILOT_LEN - GUARD
    (1920 < 3840), those ranges are DISJOINT with 1920 samples to spare -
    the search simply never reads far enough to see anything the bass
    contains. This replaced three content-relative gates, each of which
    review killed with a measured counterexample (loudest-peak: captured by
    any louder impostor; runner-up ratio: polarity backwards, a STRONGER
    impostor scores as less ambiguous; quiet-preamble belt: the 20 ms before
    a data-start impostor is the pilot's own trailing guard silence, so the
    belt passed exactly when it was needed). Position is the one thing the
    bass cannot forge, because the search never enters the bass.

    Within the window: argmax, gated by the absolute response threshold. A
    degraded pilot (below threshold) or an outsized decoder delay (apex
    beyond the window) is a MISS - the caller re-separates; nothing guesses.
    """
    chirp = _opus_pilot_chirp()
    window = _OPUS_PILOT_GUARD + _OPUS_MAX_DECODER_DELAY
    # The slice IS the proof: window + CHIRP == PILOT_LEN (see the constant),
    # so `head` ends exactly where the data begins and no candidate's
    # correlation span can read a single bass sample. Round 7 showed why the
    # span, not just the candidate positions, must be bounded.
    head = y48[:window + _OPUS_PILOT_CHIRP]
    if len(head) < len(chirp) + 1:
        return None
    c = np.correlate(head, chirp, "valid")
    if len(c) == 0:
        return None
    # A single NaN poisons every correlation it touches, and NaN passes
    # `< threshold` as False - NumPy's argmax then anoints an arbitrary
    # index (Codex round 10: one NaN at sample 2880 produced an accepted
    # 959-sample error). Non-finite anywhere in the head is a broken
    # decode: MISS.
    if not np.all(np.isfinite(c)):
        return None
    # Locate on |c|, gate on the SIGNED value (Codex round 9): the chirp's
    # autocorrelation is NOT monotone - it has a -0.716E sidelobe at lag 7 -
    # so under a polarity-inverted decode the true apex reads -1.0E, plain
    # argmax ignores it, and an interior sidelobe at +0.716E was accepted as
    # a 7-sample misalignment with inverted audio. On |c| the true apex wins
    # under either polarity; requiring c[apex] >= +threshold then refuses the
    # inversion outright (its signed value is ~ -1.0E) along with anything
    # merely weak.
    apex = int(np.argmax(np.abs(c)))
    threshold = _OPUS_PILOT_MIN_RESPONSE * float(np.dot(chirp, chirp))
    if float(c[apex]) < threshold:
        return None
    # An apex ON either edge is refused. Codex round 8: a delay of 961 puts
    # the true apex ONE sample outside the window, yet the edge candidate
    # still scores 0.91E off the chirp's own autocorrelation - an accepted
    # one-sample misalignment. The autocorrelation peaks at zero lag and
    # decays monotonically, so for ANY delay at/beyond the bound the
    # in-window maximum sits exactly on the far edge, and for any leading
    # trim that swallows the whole guard it sits on the near edge - while
    # every legitimate in-bound delay peaks strictly interior. Edge apex is
    # therefore the precise signature of an out-of-band decode: MISS.
    if apex <= 0 or apex >= len(c) - 1:
        return None
    return apex + _OPUS_PILOT_CHIRP + _OPUS_PILOT_GUARD


def _decode_bass_opus(d) -> tuple[np.ndarray, int] | None:
    import io
    raw = bytes(d["payload"].tobytes())
    y48, _ = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
    if "data_start_48" in d.files:
        # RE-LOCATE the pilot on every load. The stored position is a
        # SAVE-TIME measurement, and Codex round 4 showed reusing it blindly
        # breaks on decoder asymmetry: a different machine or libsndfile can
        # expose a different delay, silently shifting every onset with no
        # gate left to notice. The chirp travels inside the payload, so the
        # loader can always measure rather than trust; a failed or ambiguous
        # locate is a cache miss (caller re-separates), never wrong audio.
        # The stored value stays as a diagnostic.
        data_start = _locate_pilot(y48)
        if data_start is None:
            return None
        # LENGTH RECONCILIATION (Codex round 10). The apex tells us where a
        # pilot is; it cannot tell us it is THE pilot when the pre-data zone
        # carries a duplicate (accepted 660-sample error) or the real one
        # was erased and an earlier one forged. But the stream's length is
        # known: after the true data start, exactly n48 + TAIL_PAD samples
        # follow (minus any tail the decoder truncated - the pad absorbs
        # that, never audio). A pilot forged EARLIER always leaves a surplus
        # beyond the pad; a legitimate leading trim shortens the stream in
        # step and stays consistent. Arithmetic against a stored constant -
        # nothing left to out-shout.
        n48 = (int(d["n48"]) if "n48" in d.files
               else int(round(int(d["n_samples"]) * _OPUS_SR / int(d["sr"]))))
        remaining = len(y48) - data_start
        if not (n48 - 96 <= remaining <= n48 + _OPUS_TAIL_PAD + 96):
            return None
        # SOURCE-EXCERPT VERIFICATION - the accepting check; everything above
        # is a fast pre-filter. Codex round 11 proved structural gates alone
        # cannot pin a global offset (a later-forged pilot fits inside the
        # length band; earlier forgery composes with tail truncation). The
        # stored 100 ms source excerpt CAN: correlate it against the decoded
        # stream at the claimed alignment over a +/-2 ms scan; the peak must
        # sit within 4 samples of zero offset at >= 0.9 correlation. Any
        # accepted global shift beyond 4 samples would move this peak - the
        # scan is far narrower than any bass period, so no alias can enter.
        # Sidecars written before the excerpt existed fail closed (one
        # re-separation, once).
        if "anchor_refs" not in d.files or "anchor_starts" not in d.files:
            return None
        refs = np.asarray(d["anchor_refs"], dtype=np.float32)
        starts = np.asarray(d["anchor_starts"], dtype=np.int64)
        if refs.ndim != 2 or len(starts) != len(refs) or len(refs) == 0:
            return None
        if refs.shape[1] < _OPUS_SR // 100:  # under 10 ms verifies nothing
            return None
        if not np.all(np.isfinite(refs)):
            # An infinite/NaN anchor turns a score into NaN, and NaN
            # compares False against the threshold - fail-open (Codex round
            # 12). A sidecar whose anchors cannot verify is a MISS.
            return None
        # EVERY anchor must match at the claimed alignment. Fidelity at
        # offset zero, no peak search - an earlier +/-4-sample argmax gate
        # was ill-conditioned on narrowband content (a 60 Hz excerpt reads
        # 0.9988 at 6 samples off: the surface is a plateau). The admitted
        # bound, stated: a global shift passes only where every anchor's
        # own autocorrelation stays >= 0.9 at that shift - content on which
        # the shift is consumer-invisible by construction. A mid-stream
        # splice desyncs every anchor after it (Codex round 13's zero-insert
        # + tail-trim construction dies on the mid and tail anchors).
        for rstart, ref in zip(starts, refs):
            cand = y48[data_start + int(rstart):
                       data_start + int(rstart) + refs.shape[1]]
            if len(cand) < refs.shape[1] or not np.all(np.isfinite(cand)):
                return None
            denom = float(np.sqrt((ref.astype(np.float64) ** 2).sum())
                          * np.sqrt((cand.astype(np.float64) ** 2).sum()))
            if not np.isfinite(denom) or denom <= 0:
                return None
            score = float(np.dot(ref.astype(np.float64),
                                 cand.astype(np.float64)) / denom)
            if not np.isfinite(score) or score < 0.9:
                return None
        y48 = y48[data_start:]
    else:
        # Pre-pilot opus sidecar (one existed in the 14.08.26 corpus, written
        # with a measured lag of 0): no pilot in its payload to locate, so
        # its stored lag is all there is.
        y48 = y48[int(d["lead_pad"]):]
    sr = int(d["sr"])
    y = _resample(y48, int(d["sr_encoded"]), sr)
    n = int(d["n_samples"])
    if len(y) < n:
        # Only the appended tail pad can be missing - audio never is.
        y = np.pad(y, (0, n - len(y)))
    return y[:n].astype(np.float32), sr


def _save_bass_cache(wav_path: Path, cache_dir: Path, bass: np.ndarray, sr: int) -> None:
    bass = np.asarray(bass, dtype=np.float32)
    # Fail loudly HERE, inside the callers' catch-all: an empty or non-finite
    # stem must neither be cached nor abort the drums run it rode along with
    # (Codex review 2026-08-27 - np.abs([]).max() raised straight through the
    # old `except OSError`).
    if bass.size == 0:
        raise ValueError("refusing to cache an empty bass stem")
    if not np.all(np.isfinite(bass)):
        raise ValueError("refusing to cache a non-finite bass stem")

    cache_dir.mkdir(parents=True, exist_ok=True)
    st = os.stat(wav_path)
    fields = None
    if OPUS_PAYLOAD_ENABLED and _opus_available():
        try:
            fields = _encode_bass_opus(bass, int(sr))
        except Exception:
            # A codec crash is just another reason to take the fallback -
            # previously it escaped and the sidecar was not written at all
            # (Codex round-2 warning).
            fields = None
    if fields is None:
        # int16 fallback: sample-exact-ish, codec-free, always available.
        scale = float(max(1e-9, np.abs(bass).max()))
        q = np.round(np.clip(bass / scale, -1.0, 1.0) * 32767.0).astype(np.int16)
        fields = {
            "payload_format": np.array("int16"),
            "bass_i16": q,
            "scale": np.array(scale, dtype=np.float64),
            "sr": np.array(int(sr)),
        }
    fields.update(
        src_size=np.array(int(st.st_size)),
        src_mtime_ns=np.array(int(st.st_mtime_ns)),
        src_fingerprint=np.array(_content_fingerprint(wav_path) or ""),
        demucs_model=np.array(DEMUCS_MODEL_NAME),
        cache_version=np.array(int(BASS_CACHE_VERSION)),
    )
    final = _bass_cache_path(wav_path, cache_dir)
    tmp = cache_dir / f"{wav_path.stem}.bass.tmp.npz"
    np.savez_compressed(tmp, **fields)
    os.replace(tmp, final)


def _load_bass_cache(wav_path: Path, cache_dir: Path) -> tuple[np.ndarray, int] | None:
    cache = _bass_cache_path(wav_path, cache_dir)
    if not cache.exists():
        return None
    try:
        with np.load(cache, allow_pickle=False) as d:
            for key in ("sr", "src_size", "src_mtime_ns", "demucs_model",
                        "cache_version"):
                if key not in d.files:
                    return None
            if int(d["cache_version"]) != BASS_CACHE_VERSION:
                return None
            if str(d["demucs_model"]) != DEMUCS_MODEL_NAME:
                return None
            if not _source_matches(d, wav_path):
                return None
            fmt = (str(d["payload_format"]) if "payload_format" in d.files
                   else "int16")           # pre-opus sidecars carry int16 keys
            if fmt == "opus":
                return _decode_bass_opus(d)
            if "bass_i16" not in d.files or "scale" not in d.files:
                return None
            bass = d["bass_i16"].astype(np.float32) * (float(d["scale"]) / 32767.0)
            return bass, int(d["sr"])
    except Exception:
        return None


def default_sidecar_dir(wav_path: Path) -> Path | None:
    """The corpus `_Stem Analysis` folder for a wav, or None.

    Two levels up covers both legitimate homes - `<corpus>/Audio/x.wav` and a
    mix subset like `<corpus>/Audio Mix 12/x.wav` (same rule as
    stem_grid._sidecar_dir).
    """
    candidate = wav_path.parent.parent / "_Stem Analysis"
    return candidate if candidate.is_dir() else None


def load_bass_stem(wav_path: Path, cache_dir: Path | None = None) -> tuple[np.ndarray, int] | None:
    """Public read-through for the bass sidecar. None on any miss - callers
    fall back to a fresh separation, so a miss costs time, never correctness."""
    wav_path = Path(wav_path)
    if cache_dir is None:
        cache_dir = default_sidecar_dir(wav_path)
    if cache_dir is None:
        return None
    return _load_bass_cache(wav_path, cache_dir)


def _run_demucs_separation(
    wav_path: Path, device: str, hop_sec: float
) -> tuple[dict[str, np.ndarray], float, np.ndarray, np.ndarray | None, int]:
    """Heavy Demucs separation body. The import torch / from demucs.apply
    lines stay INSIDE this function so the module's lazy-import guarantee
    holds and the warm cache path (case A) never pulls torch/demucs.

    Returns (envs, hop_t, drums, bass, sr). Bass is None if the model has no
    bass source - it is an opportunistic extra (Sam 2026-08-27: keep it),
    never a requirement, so its absence must not break any drums flow.
    """
    import torch
    from demucs.apply import apply_model

    dev = _auto_device() if device == "auto" else device
    data, sr = sf.read(str(wav_path), always_2d=True)
    wav = data.T.astype(np.float32)
    if wav.shape[0] == 1:
        wav = np.vstack([wav, wav])
    if sr != 44100:
        import librosa
        wav = librosa.resample(wav, orig_sr=sr, target_sr=44100)
        sr = 44100

    model = _demucs_model(dev)
    source_names = list(model.sources)
    if "drums" not in source_names:
        raise RuntimeError(f"Demucs model has no drums source: {source_names}")

    t = torch.from_numpy(wav)
    ref = t.mean(0)
    t = (t - ref.mean()) / (ref.std() + 1e-8)
    print(f"  separating {wav_path.name} ({dev.upper()}, kick model)...")
    with torch.no_grad():
        out = apply_model(model, t[None], device=dev, progress=True)[0]
    out = out * (ref.std() + 1e-8) + ref.mean()

    hop = max(1, int(sr * hop_sec))
    envs = {name: _env(out[i].mean(0).cpu().numpy(), hop) for i, name in enumerate(source_names)}
    envs["mix"] = _env(wav.mean(0), hop)
    drums = out[source_names.index("drums")].mean(0).cpu().numpy().astype(np.float32)
    bass = None
    if "bass" in source_names:
        bass = out[source_names.index("bass")].mean(0).cpu().numpy().astype(np.float32)

    return envs, hop / sr, drums, bass, sr


def separate_envelopes_and_drums(
    wav_path: Path,
    cache_dir: Path,
    device: str = "auto",
    hop_sec: float = 0.1,
) -> tuple[dict[str, np.ndarray], float, np.ndarray, int]:
    """Single Demucs pass for model mode, with disk caches for both envelopes
    and the raw mono drums stem.

    Returns the same envelope dict shape as stem_section_probe._separate_envelopes,
    plus the raw mono drums stem required by Kick Detector. The envelope cache
    (<stem>__stemenv.npz) and a new drums-stem sidecar (<stem>__drumsstem.npz)
    live side by side in cache_dir. Re-runs with intact caches do zero Demucs
    work (Sam 2026-08-19: persist Demucs output so re-runs skip Demucs entirely).
    The sidecar stores analysis output, not mixable audio, so the
    no-stem-audio-on-disk invariant is preserved.
    """
    env_cache = cache_dir / f"{wav_path.stem}__stemenv.npz"

    # CASE A - warm: envelopes + drums stem both cached; pure read-through.
    drums_hit = _load_drums_cache(wav_path, cache_dir)
    if env_cache.exists() and drums_hit is not None:
        with np.load(env_cache, allow_pickle=False) as d:
            hop_t = float(d["hop_t"])
            envs = {
                k: d[k] for k in d.files if k != "hop_t" and not k.startswith("tiera_")
            }
        drums, sr = drums_hit
        print(f"  cache hit {wav_path.name} (envelopes + drums stem, Demucs skipped)")
        return envs, hop_t, drums, sr

    # Cases B and C: must run Demucs at least once for fresh drums.
    envs, hop_t, drums, bass, sr = _run_demucs_separation(wav_path, device, hop_sec)

    # Always persist the drums stem when we just computed it.
    _save_drums_cache(wav_path, cache_dir, drums, sr)
    # Bass rides along OPPORTUNISTICALLY whenever a separation ran anyway
    # (Sam 2026-08-27). Never forces a re-separation: a warm drums cache
    # stays warm with no bass sidecar, and bass backfills the next time
    # this track is separated fresh. Failure to save must not fail the run.
    if bass is not None:
        try:
            _save_bass_cache(wav_path, cache_dir, bass, sr)
        except Exception as e:
            # Bass is opportunistic; nothing it does may fail the drums run
            # that already succeeded (Codex 2026-08-27: OSError alone let a
            # ValueError from a degenerate stem abort the whole call).
            print(f"  warning: bass stem cache write failed for "
                  f"{wav_path.name}: {type(e).__name__}: {e}")

    if env_cache.exists():
        # CASE B - mixed: env_cache is authoritative for envelopes (it may
        # carry tiera_ augmentation keys and must NOT be rewritten); drums
        # come from the fresh separation we just ran. Return envelopes loaded
        # from disk so callers see exactly what is persisted.
        with np.load(env_cache, allow_pickle=False) as d:
            cached_hop_t = float(d["hop_t"])
            cached_envs = {
                k: d[k] for k in d.files if k != "hop_t" and not k.startswith("tiera_")
            }
        return cached_envs, cached_hop_t, drums, sr

    # CASE C - fresh: write the envelope cache byte-identically to today's
    # layout, then return the values we just computed.
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(env_cache, hop_t=np.array(hop_t), **envs)
    return envs, hop_t, drums, sr


class KickPresenceProvider:
    """Run Kick Detector V3 on a mastered track and return beat-level presence."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        device: str = "auto",
        threshold: float = DEFAULT_THRESHOLD,
        fill_off_beats: int = DEFAULT_FILL_OFF_BEATS,
        drop_on_beats: int = DEFAULT_DROP_ON_BEATS,
    ):
        self.model_path = Path(model_path) if model_path else default_model_path()
        self.root = self.model_path.parents[1]
        self.device = _auto_device() if device == "auto" else device
        self.threshold = threshold
        self.fill_off_beats = fill_off_beats
        self.drop_on_beats = drop_on_beats
        self._model = None
        self._torch = None
        self._model_mod = None
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Kick Detector weights not found: {self.model_path}. "
                "Run without --kick-model or pass --kick-model-path."
            )
        self._presence_mod = _load_presence_module(self.root)

    def _load(self):
        if self._model is not None:
            return
        import torch
        self._torch = torch
        self._model_mod = _load_model_module(self.root)
        blob = torch.load(str(self.model_path), map_location=self.device)
        n_mels = blob.get("cfg", {}).get("n_mels", self._model_mod.N_MELS)
        self._model = self._model_mod.build_model(n_mels=n_mels)
        self._model.load_state_dict(blob["state_dict"])
        self._model.to(self.device).eval()

    def _activation(self, drums_mono: np.ndarray, sr: int, chunk_fr: int = 3000, overlap: int = 200) -> np.ndarray:
        self._load()
        assert self._model is not None
        assert self._torch is not None
        assert self._model_mod is not None

        if sr != self._model_mod.SR:
            import librosa
            drums_mono = librosa.resample(drums_mono, orig_sr=sr, target_sr=self._model_mod.SR)
            sr = self._model_mod.SR
        mel = self._model_mod.log_mel(drums_mono, sr=sr)
        frames = mel.shape[1]
        acc = np.zeros(frames, dtype=np.float32)
        cnt = np.zeros(frames, dtype=np.float32)
        step = chunk_fr - overlap
        with self._torch.no_grad():
            for start in range(0, frames, step):
                end = min(frames, start + chunk_fr)
                x = self._torch.from_numpy(mel[:, start:end]).unsqueeze(0).unsqueeze(0).to(self.device)
                logits = self._model(x)[0].cpu().numpy()
                acc[start:end] += 1.0 / (1.0 + np.exp(-logits))
                cnt[start:end] += 1.0
                if end == frames:
                    break
        return acc / np.maximum(cnt, 1e-6)

    def _drums_from_mix(
        self, wav_path: Path, cache_dir: Path | None = None
    ) -> tuple[np.ndarray, int]:
        if cache_dir is None and wav_path.parent.name == "Audio":
            cache_dir = wav_path.parent.parent / "_Stem Analysis"

        if cache_dir is not None:
            hit = _load_drums_cache(wav_path, cache_dir)
            if hit is not None:
                return hit

        envs, hop_t, drums, bass, sr = _run_demucs_separation(wav_path, self.device, 0.1)

        if cache_dir is not None:
            try:
                _save_drums_cache(wav_path, cache_dir, drums, sr)
            except OSError as e:
                print(
                    f"  warning: drums stem cache write failed for "
                    f"{wav_path.name}: {e}"
                )
            if bass is not None:
                try:
                    _save_bass_cache(wav_path, cache_dir, bass, sr)
                except Exception as e:
                    # Same containment as the module-level saver: bass must
                    # never abort a drums run (Codex 2026-08-27).
                    print(
                        f"  warning: bass stem cache write failed for "
                        f"{wav_path.name}: {type(e).__name__}: {e}"
                    )
            env_cache = cache_dir / f"{wav_path.stem}__stemenv.npz"
            if not env_cache.exists():
                cache_dir.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(
                    env_cache, hop_t=np.array(hop_t), **envs
                )
        return drums, sr

    def _presence_readout(
        self,
        drums: np.ndarray,
        sr: int,
        bpm: float,
        downbeat: float,
        n_beats: int | None,
    ) -> KickPresenceReadout:
        act = self._activation(drums, sr)
        duration_s = len(drums) / sr
        assert self._model_mod is not None
        raw = self._model_mod.presence_from_activation(
            act, duration_s, bpm, downbeat=downbeat, thresh=self.threshold,
        )
        section = self._presence_mod.smooth_presence(
            raw, self.fill_off_beats, self.drop_on_beats
        )
        return KickPresenceReadout(
            raw=_fit_length(raw, n_beats),
            section=_fit_length(section, n_beats),
        )

    def presence_per_beat(
        self, wav_path: Path, bpm: float, downbeat: float,
        n_beats: int | None = None,
        cache_dir: Path | None = None,
    ) -> KickPresenceReadout:
        drums, sr = self._drums_from_mix(Path(wav_path), cache_dir=cache_dir)
        return self._presence_readout(drums, sr, bpm, downbeat, n_beats)

    def on_per_beat(self, wav_path: Path, bpm: float, downbeat: float,
                    n_beats: int | None = None,
                    cache_dir: Path | None = None) -> np.ndarray:
        return self.presence_per_beat(
            wav_path, bpm, downbeat, n_beats, cache_dir=cache_dir
        ).section

    def presence_per_beat_from_drums(
        self,
        drums_mono: np.ndarray,
        sr: int,
        bpm: float,
        downbeat: float,
        n_beats: int | None = None,
    ) -> KickPresenceReadout:
        return self._presence_readout(drums_mono, sr, bpm, downbeat, n_beats)

    def on_per_beat_from_drums(
        self,
        drums_mono: np.ndarray,
        sr: int,
        bpm: float,
        downbeat: float,
        n_beats: int | None = None,
    ) -> np.ndarray:
        return self.presence_per_beat_from_drums(
            drums_mono, sr, bpm, downbeat, n_beats
        ).section


def get_provider(
    model_path: str | Path | None = None,
    device: str = "auto",
    threshold: float = DEFAULT_THRESHOLD,
    fill_off_beats: int = DEFAULT_FILL_OFF_BEATS,
    drop_on_beats: int = DEFAULT_DROP_ON_BEATS,
) -> KickPresenceProvider:
    path = Path(model_path) if model_path else default_model_path()
    key = (str(path.resolve()), device, threshold, fill_off_beats, drop_on_beats)
    if key not in _PROVIDER_CACHE:
        _PROVIDER_CACHE[key] = KickPresenceProvider(
            model_path=path,
            device=device,
            threshold=threshold,
            fill_off_beats=fill_off_beats,
            drop_on_beats=drop_on_beats,
        )
    return _PROVIDER_CACHE[key]
