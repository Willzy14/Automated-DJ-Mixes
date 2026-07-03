"""SHIM — canonical code lives in Audio Analysis Toolkit (audio_analysis.track_analysis).
Re-exports for backwards compatibility; only the DJ-specific enrich_from_rekordbox lives here."""
from audio_analysis.track_analysis import *  # noqa: F401,F403
from audio_analysis.track_analysis import (  # noqa: F401  (explicit + private re-exports)
    TrackAnalysis, analyse_track, analyse_folder, AUDIO_EXTENSIONS,
    _read_tags, _detect_downbeat, _refine_attack, _measure_lufs,
    _detect_sections, _detect_last_kick, _detect_bass_section,
    _detect_first_break_phrase_aware,
)
from pathlib import Path  # noqa: F401

from automated_dj_mixes.rekordbox_reader import RekordboxAnalysis


def enrich_from_rekordbox(track: TrackAnalysis, rb) -> None:
    """Overwrite section markers with Rekordbox phrase analysis data.

    Maps Rekordbox structural phrases (intro/up/down/chorus/outro) to the
    pipeline's section anchors. Rekordbox's phrase analysis is far more
    reliable than our librosa-based detection, especially for bass/break
    boundaries.

    Mapping logic (for mood=1 / dance music):
      - bass_start = first "chorus" phrase start (the first drop)
      - bass_end   = where the last chorus run ends (start of final down/outro)
      - first_break_start = first "down" after the first chorus
      - first_break_end   = next "chorus" after that "down" (the second drop)
      - intro_end  = end of first "intro" phrase
      - last_kick  = start of "outro" phrase
      - first_downbeat = beat grid beat 1
    """
    from automated_dj_mixes.rekordbox_reader import RekordboxAnalysis

    if not isinstance(rb, RekordboxAnalysis) or not rb.phrases:
        return

    track.rekordbox_phrases = rb.phrases
    track.analysis_source = "rekordbox"

    # First TRUE downbeat from the beat grid → first_downbeat_sec.
    # NOT entry 0: grids often start on beat 2/3/4 of a bar —
    # first_downbeat_offset indexes the first beat_of_bar=1 entry, matching
    # warp beat 0 in calculate_warp_markers_from_beat_grid. (Anchoring at
    # entry 0 put the bar phase off by up to 3 beats — e.g. Todd Edwards.)
    if rb.beat_times_ms:
        off = getattr(rb, "first_downbeat_offset", 0) or 0
        off = min(max(off, 0), len(rb.beat_times_ms) - 1)
        track.first_downbeat_sec = rb.beat_times_ms[off] / 1000.0

    # Intro end = end of last consecutive "intro" phrase
    for i, p in enumerate(rb.phrases):
        if p.label == "intro":
            track.intro_end_sec = rb.beat_to_sec(rb.phrase_end_beat(i))
        else:
            break

    # First chorus (drop) → bass_start
    first_chorus = rb.first_phrase_of("chorus")
    if first_chorus is None:
        return

    first_chorus_idx = rb.phrases.index(first_chorus)
    track.bass_start_sec = rb.beat_to_sec(first_chorus.start_beat)

    # End of first chorus run = first non-chorus phrase after the chorus block
    chorus_end_idx = first_chorus_idx
    for j in range(first_chorus_idx + 1, len(rb.phrases)):
        if rb.phrases[j].label == "chorus":
            chorus_end_idx = j
        else:
            break

    # First break after first chorus run
    first_down_after_chorus = None
    for j in range(chorus_end_idx + 1, len(rb.phrases)):
        if rb.phrases[j].label == "down":
            first_down_after_chorus = rb.phrases[j]
            first_down_idx = j
            break

    if first_down_after_chorus:
        track.first_break_start_sec = rb.beat_to_sec(first_down_after_chorus.start_beat)

        # Find end of the down/break section → next chorus is the second drop
        next_chorus = rb.first_phrase_of_after("chorus", first_down_after_chorus.start_beat + 1)
        if next_chorus:
            track.first_break_end_sec = rb.beat_to_sec(next_chorus.start_beat)

    # bass_end = where bass permanently drops out near the end of the track.
    # Walk backwards from the end — find the last "chorus" phrase, then
    # bass_end is at the end of that phrase.
    last_chorus_idx = None
    for j in range(len(rb.phrases) - 1, -1, -1):
        if rb.phrases[j].label == "chorus":
            last_chorus_idx = j
            break

    if last_chorus_idx is not None:
        track.bass_end_sec = rb.beat_to_sec(rb.phrase_end_beat(last_chorus_idx))

    # last_kick = start of outro (or end of last non-outro phrase)
    outro = rb.first_phrase_of("outro")
    if outro:
        track.last_kick_sec = rb.beat_to_sec(outro.start_beat)
    elif last_chorus_idx is not None:
        track.last_kick_sec = rb.beat_to_sec(rb.phrase_end_beat(last_chorus_idx))
