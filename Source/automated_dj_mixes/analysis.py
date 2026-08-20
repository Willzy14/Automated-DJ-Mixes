"""SHIM — canonical code lives in Audio Analysis Toolkit (audio_analysis.track_analysis).
Re-exports for backwards compatibility."""
from audio_analysis.track_analysis import *  # noqa: F401,F403
from audio_analysis.track_analysis import (  # noqa: F401  (explicit + private re-exports)
    TrackAnalysis, analyse_track, analyse_folder, AUDIO_EXTENSIONS,
    _read_tags, _detect_downbeat, _refine_attack, _measure_lufs,
    _detect_sections, _detect_last_kick, _detect_bass_section,
    _detect_first_break_phrase_aware,
)
from pathlib import Path  # noqa: F401
