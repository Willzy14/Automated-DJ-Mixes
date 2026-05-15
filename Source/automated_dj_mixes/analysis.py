"""Read key/BPM from file tags, detect transients/downbeats, measure LUFS."""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field

import librosa
import numpy as np
import pyloudnorm as pyln
import mutagen
from mutagen.id3 import ID3
from mutagen.oggvorbis import OggVorbis
from mutagen.flac import FLAC

from automated_dj_mixes.sequencer import key_to_camelot

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".aiff", ".aif", ".ogg"}


@dataclass
class TrackAnalysis:
    path: Path
    key: str | None = None
    camelot: str | None = None
    bpm: float | None = None
    lufs: float | None = None
    first_downbeat_sec: float | None = None
    duration_sec: float | None = None
    sample_rate: int | None = None
    # Section markers (Phase 2 — for transition alignment)
    intro_end_sec: float | None = None        # where the track reaches full energy
    first_break_start_sec: float | None = None  # first significant energy drop after intro
    last_kick_sec: float | None = None        # last detected kick (end-of-drums anchor)
    cymbal_tail_end_sec: float | None = None  # end of meaningful audio after last kick
    # Phase 5 — base-to-base alignment anchors
    bass_start_sec: float | None = None        # where sustained bass synth enters (first drop)
    bass_end_sec: float | None = None          # where sustained bass synth exits (start of outro)
    # Phase 6 — phrase-aware break detection (for "tail-into-break" alignment)
    first_break_end_sec: float | None = None   # where energy returns after the first break (the drop point)
    warnings: list[str] = field(default_factory=list)


def _read_tags(track_path: Path) -> dict[str, str | None]:
    """Read key and BPM from file tags. Handles ID3, Vorbis, FLAC."""
    key = None
    bpm = None

    try:
        audio = mutagen.File(track_path, easy=True)
        if audio is None:
            return {"key": None, "bpm": None}

        # BPM: common tag names
        for tag in ("bpm", "TBPM", "TEMPO"):
            val = audio.get(tag)
            if val:
                bpm = str(val[0]) if isinstance(val, list) else str(val)
                break

        # Key: Mixed In Key writes to "initialkey" or "TKEY"
        for tag in ("initialkey", "TKEY", "key"):
            val = audio.get(tag)
            if val:
                key = str(val[0]) if isinstance(val, list) else str(val)
                break
    except Exception:
        pass

    # Fallback: try raw ID3 tags for MP3
    if (key is None or bpm is None) and track_path.suffix.lower() == ".mp3":
        try:
            tags = ID3(track_path)
            if bpm is None and "TBPM" in tags:
                bpm = str(tags["TBPM"].text[0])
            if key is None and "TKEY" in tags:
                key = str(tags["TKEY"].text[0])
        except Exception:
            pass

    return {"key": key, "bpm": bpm}


def _detect_downbeat(y: np.ndarray, sr: int, bpm: float | None = None) -> float:
    """Detect the first kick drum using per-frame bass power + rhythmic confirmation.

    Skips onsets in quiet/filtered intro sections by requiring minimum bass
    power (10% of track peak) at each candidate frame. Then confirms the
    candidate has recurring onsets at BPM intervals.
    """
    hop = 512

    S = librosa.feature.melspectrogram(y=y, sr=sr, hop_length=hop, n_mels=16, fmin=20, fmax=200)
    onset_env = librosa.onset.onset_strength(S=librosa.power_to_db(S), sr=sr, hop_length=hop)

    if onset_env.max() == 0:
        return 0.0

    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, hop_length=hop, backtrack=False,
    )
    onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop)

    if len(onset_times) == 0:
        return 0.0

    if bpm and bpm > 0:
        beat_dur = 60.0 / bpm
        tolerance = beat_dur * 0.12
        frame_power = np.mean(S, axis=0)
        peak_power = np.max(frame_power)
        drop_threshold = peak_power * 0.05
        # 8 beats of lookahead — confirms a real kick by checking that loud kicks
        # follow within ~2 bars (handles Huw Shipps: quiet first kick but loud
        # follow-ups within 1s) vs Love Me This Time (quiet rhythmic intro
        # stays quiet for 12+ seconds before the real drop)
        lookahead_frames = int(8 * beat_dur * sr / hop)

        for candidate_frame, candidate in zip(onset_frames, onset_times):
            cf = min(candidate_frame, len(frame_power) - 1)
            # Max power in candidate + next 8 beats — must show a real beat is starting
            window_end = min(len(frame_power), cf + lookahead_frames)
            window_max = np.max(frame_power[cf:window_end]) if window_end > cf else 0
            if window_max < drop_threshold:
                continue
            matches = 0
            for n in range(1, 17):
                expected = candidate + n * beat_dur
                if len(onset_times) > 0:
                    closest = np.min(np.abs(onset_times - expected))
                    if closest < tolerance:
                        matches += 1
            if matches >= 4:
                return _refine_attack(y, sr, candidate, hop)

    # Fallback: first onset above 30% of peak strength
    threshold = onset_env.max() * 0.3
    strong = [f for f in onset_frames if onset_env[min(f, len(onset_env) - 1)] >= threshold]
    if strong:
        t = float(librosa.frames_to_time(strong[0], sr=sr, hop_length=hop))
        return _refine_attack(y, sr, t, hop)

    return float(onset_times[0])


def _refine_attack(y: np.ndarray, sr: int, coarse_sec: float, hop: int) -> float:
    """Refine an onset time to the exact attack start using the raw waveform."""
    coarse_sample = int(coarse_sec * sr)
    search_start = max(0, coarse_sample - int(sr * 0.1))
    region = np.abs(y[search_start:coarse_sample + hop])
    if len(region) == 0:
        return coarse_sec

    peak_amp = region.max()
    if peak_amp == 0:
        return coarse_sec

    above = np.where(region >= peak_amp * 0.05)[0]
    if len(above) > 0:
        return float((search_start + int(above[0])) / sr)
    return coarse_sec


def _measure_lufs(y: np.ndarray, sr: int) -> float:
    """Measure integrated LUFS of the audio signal."""
    meter = pyln.Meter(sr)
    if y.ndim == 1:
        y_stereo = np.stack([y, y])
    else:
        y_stereo = y
    loudness = meter.integrated_loudness(y_stereo.T)
    return float(loudness)


def _detect_sections(
    y: np.ndarray, sr: int, bpm: float, first_kick_sec: float
) -> tuple[float | None, float | None, float | None, float | None]:
    """Detect intro_end, first_break_start, last_kick, cymbal_tail_end.

    Uses a smoothed RMS energy curve (~1 bar window). Sections are defined by
    sustained crossings of energy thresholds:
      - intro_end: first time RMS sustains above 60% of peak for 4+ bars
      - first_break_start: first drop below 50% of peak for 2+ bars after intro_end
      - last_kick: last bass-band onset with rhythmic confirmation
      - cymbal_tail_end: last point where energy is above 3% of peak (silence threshold)
    """
    hop = 512
    beat_dur = 60.0 / bpm
    bar_frames = max(1, int(4 * beat_dur * sr / hop))

    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    smoothed = np.convolve(rms, np.ones(bar_frames) / bar_frames, mode="same")

    peak_rms = smoothed.max()
    if peak_rms == 0:
        return None, None, None, None

    full_energy_threshold = peak_rms * 0.60
    break_threshold = peak_rms * 0.50
    silence_threshold = peak_rms * 0.03

    def frame_to_sec(f: int) -> float:
        return float(f * hop / sr)

    # intro_end: first time smoothed RMS sustains above full_energy_threshold for 4+ bars
    above = smoothed >= full_energy_threshold
    sustain_4bars = bar_frames * 4
    intro_end_frame = None
    first_kick_frame = int(first_kick_sec * sr / hop)
    for i in range(first_kick_frame, len(above) - sustain_4bars):
        if above[i : i + sustain_4bars].all():
            intro_end_frame = i
            break
    intro_end = frame_to_sec(intro_end_frame) if intro_end_frame is not None else None

    # first_break_start: first sustained drop below break_threshold after intro_end
    first_break_start = None
    if intro_end_frame is not None:
        below = smoothed < break_threshold
        sustain_2bars = bar_frames * 2
        search_start = intro_end_frame + sustain_4bars
        for i in range(search_start, len(below) - sustain_2bars):
            if below[i : i + sustain_2bars].all():
                first_break_start = frame_to_sec(i)
                break

    # last_kick: scan onsets from the end backwards
    last_kick = _detect_last_kick(y, sr, bpm)

    # cymbal_tail_end: last frame where smoothed RMS is above silence threshold
    above_silence = np.where(smoothed >= silence_threshold)[0]
    cymbal_tail_end = frame_to_sec(int(above_silence[-1])) if len(above_silence) > 0 else None

    return intro_end, first_break_start, last_kick, cymbal_tail_end


def _detect_last_kick(y: np.ndarray, sr: int, bpm: float) -> float | None:
    """Find the last kick drum in the track (the end-of-drums anchor for mix-out).

    Searches onsets backwards from the end, applying the same rhythmic + drop
    confirmation logic as first-kick detection but in reverse (looking at the
    PRECEDING 8 beats for a loud kick).
    """
    hop = 512
    S = librosa.feature.melspectrogram(y=y, sr=sr, hop_length=hop, n_mels=16, fmin=20, fmax=200)
    onset_env = librosa.onset.onset_strength(S=librosa.power_to_db(S), sr=sr, hop_length=hop)

    if onset_env.max() == 0:
        return None

    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, hop_length=hop, backtrack=False,
    )
    onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop)
    if len(onset_times) == 0:
        return None

    beat_dur = 60.0 / bpm
    tolerance = beat_dur * 0.12
    frame_power = np.mean(S, axis=0)
    peak_power = np.max(frame_power)
    drop_threshold = peak_power * 0.05
    lookbehind_frames = int(8 * beat_dur * sr / hop)

    # Walk onsets from the LAST one backwards
    for candidate_frame, candidate in zip(onset_frames[::-1], onset_times[::-1]):
        cf = min(candidate_frame, len(frame_power) - 1)
        window_start = max(0, cf - lookbehind_frames)
        window_max = np.max(frame_power[window_start : cf + 1]) if cf > window_start else 0
        if window_max < drop_threshold:
            continue
        # Rhythmic confirmation looking backwards
        matches = 0
        for n in range(1, 17):
            expected = candidate - n * beat_dur
            if len(onset_times) > 0:
                closest = np.min(np.abs(onset_times - expected))
                if closest < tolerance:
                    matches += 1
        if matches >= 4:
            return _refine_attack(y, sr, candidate, hop)
    return None


def _detect_bass_section(
    y: np.ndarray,
    sr: int,
    bpm: float,
    first_kick_sec: float = 0.025,
    threshold_pct: float = 0.40,
    sustain_beats: int = 16,
) -> tuple[float | None, float | None]:
    """Detect sustained bass synth section using off-beat energy.

    Kicks produce loud transients ON the beat. Between kicks (off-beats),
    bass synth holds notes — keeping energy high. Pure kick-only sections
    (intros/outros) have low energy on off-beats. By sampling power at the
    midpoint between expected kicks, we isolate when the bass synth is active.
    """
    hop = 512
    S = librosa.feature.melspectrogram(
        y=y, sr=sr, hop_length=hop, n_mels=8, fmin=40, fmax=180
    )
    bass_power = np.mean(S, axis=0)

    beat_dur = 60.0 / bpm
    duration = len(y) / sr
    n_beats = int((duration - first_kick_sec) / beat_dur)
    if n_beats <= 0:
        return None, None

    # Sample bass-band power in a ±40ms window around each off-beat
    window_sec = 0.04
    window_frames = max(1, int(window_sec * sr / hop))

    off_beat_powers = np.zeros(n_beats)
    off_beat_times = np.zeros(n_beats)
    for n in range(n_beats):
        off_beat_time = first_kick_sec + (n + 0.5) * beat_dur
        off_beat_frame = int(off_beat_time * sr / hop)
        lo = max(0, off_beat_frame - window_frames)
        hi = min(len(bass_power), off_beat_frame + window_frames + 1)
        if hi > lo:
            off_beat_powers[n] = np.mean(bass_power[lo:hi])
            off_beat_times[n] = off_beat_time

    if off_beat_powers.max() == 0:
        return None, None

    # Smooth over 1 bar (4 beats) to suppress noise
    smoothed = np.convolve(off_beat_powers, np.ones(4) / 4, mode="same")
    peak = smoothed.max()
    threshold = peak * threshold_pct
    above = smoothed >= threshold

    bass_start = None
    bass_end = None

    # First sustained region above threshold
    for i in range(len(above) - sustain_beats):
        if above[i : i + sustain_beats].all():
            bass_start = float(off_beat_times[i])
            break

    # Last sustained region (search from end)
    for i in range(len(above) - sustain_beats, -1, -1):
        if above[i : i + sustain_beats].all():
            end_idx = min(i + sustain_beats, len(off_beat_times) - 1)
            bass_end = float(off_beat_times[end_idx])
            break

    return bass_start, bass_end


def _detect_first_break_phrase_aware(
    y: np.ndarray, sr: int, bpm: float, bass_start_sec: float | None, first_kick_sec: float
) -> tuple[float | None, float | None]:
    """Detect the first break (drop in energy then return) using a 16-bar phrase grid.

    Returns (break_start_sec, break_end_sec). The break is defined as a 16-bar
    segment where the average RMS energy drops below 60% of the surrounding
    baseline. break_end is when energy recovers above 80% of baseline.

    Both points are aligned to 16-bar grid positions starting from bass_start
    (or first_kick if bass detection failed) — Sam's rule: changes happen on
    phrase marks.
    """
    hop = 512
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]

    beat_dur = 60.0 / bpm
    bars_16_frames = max(1, int(16 * 4 * beat_dur * sr / hop))

    # Start scanning from bass_start (typical case) or first_kick (fallback)
    scan_start_sec = bass_start_sec if bass_start_sec else first_kick_sec
    scan_start_frame = int(scan_start_sec * sr / hop)

    # Walk forward in 16-bar increments, measure avg RMS per segment
    segments = []  # (start_frame, avg_rms)
    f = scan_start_frame
    while f + bars_16_frames < len(rms):
        avg = float(np.mean(rms[f : f + bars_16_frames]))
        segments.append((f, avg))
        f += bars_16_frames

    if len(segments) < 3:
        return None, None

    # Baseline = average of first two 16-bar segments (the established "drop" energy)
    baseline = float(np.mean([s[1] for s in segments[:2]]))
    if baseline == 0:
        return None, None

    break_start_frame = None
    break_end_frame = None
    for i in range(2, len(segments)):
        if break_start_frame is None:
            # Looking for the drop
            if segments[i][1] < baseline * 0.6:
                break_start_frame = segments[i][0]
        else:
            # Looking for the recovery
            if segments[i][1] >= baseline * 0.8:
                break_end_frame = segments[i][0]
                break

    if break_start_frame is None:
        return None, None

    break_start_sec_v = float(break_start_frame * hop / sr)
    # If we never saw recovery, set break_end at break_start + 16 bars
    if break_end_frame is None:
        break_end_sec_v = break_start_sec_v + 16 * 4 * beat_dur
    else:
        break_end_sec_v = float(break_end_frame * hop / sr)

    return break_start_sec_v, break_end_sec_v


def analyse_track(track_path: Path) -> TrackAnalysis:
    """Analyse a single track: read tags, detect downbeat, measure loudness."""
    result = TrackAnalysis(path=track_path)

    tags = _read_tags(track_path)
    result.key = tags["key"]
    if result.key:
        result.camelot = key_to_camelot(result.key)
        if result.camelot is None:
            result.warnings.append(f"Key '{result.key}' could not be mapped to Camelot")

    if tags["bpm"]:
        try:
            result.bpm = float(tags["bpm"])
        except ValueError:
            result.warnings.append(f"Invalid BPM tag: {tags['bpm']}")

    y, sr = librosa.load(track_path, sr=None, mono=True)
    result.sample_rate = sr
    result.duration_sec = float(librosa.get_duration(y=y, sr=sr))

    if result.bpm is None:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        result.bpm = float(np.squeeze(tempo))
        result.warnings.append(f"BPM detected by librosa: {result.bpm:.1f}")

    result.first_downbeat_sec = _detect_downbeat(y, sr, bpm=result.bpm)

    # Phase 2: section detection (for transition alignment)
    intro_end, first_break, last_kick, cymbal_end = _detect_sections(
        y, sr, result.bpm, result.first_downbeat_sec
    )
    result.intro_end_sec = intro_end
    result.first_break_start_sec = first_break
    result.last_kick_sec = last_kick
    result.cymbal_tail_end_sec = cymbal_end

    # Phase 5: bass section detection (for base-to-base alignment)
    bass_start, bass_end = _detect_bass_section(
        y, sr, result.bpm, result.first_downbeat_sec
    )
    # Sanity filter: bass section must be at least 60s. Anything shorter is
    # likely a false positive (e.g. a one-off riser or fade-in artefact) —
    # treat as missing and let the orchestrator fall back to end-to-end alignment.
    if bass_start is not None and bass_end is not None and (bass_end - bass_start) >= 60.0:
        result.bass_start_sec = bass_start
        result.bass_end_sec = bass_end
    else:
        result.bass_start_sec = None
        result.bass_end_sec = None

    # Phase 6: phrase-aware break detection (16-bar grid scan)
    # Finds the first sustained energy drop AND when it returns.
    # Used for "tail-into-break" alignment: outgoing finishes before incoming's break_start.
    break_start, break_end = _detect_first_break_phrase_aware(
        y, sr, result.bpm, result.bass_start_sec, result.first_downbeat_sec
    )
    # Only override the existing first_break_start if the phrase-aware version found one
    if break_start is not None:
        result.first_break_start_sec = break_start
    result.first_break_end_sec = break_end

    y_full, sr_full = librosa.load(track_path, sr=None, mono=False)
    result.lufs = _measure_lufs(y_full, sr_full)

    return result


def analyse_folder(folder: Path) -> list[TrackAnalysis]:
    """Analyse all audio files in a folder."""
    tracks = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() in AUDIO_EXTENSIONS and not f.name.startswith("."):
            tracks.append(analyse_track(f))
    return tracks
