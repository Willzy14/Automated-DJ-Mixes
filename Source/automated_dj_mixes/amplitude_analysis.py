"""SHIM — canonical code lives in Audio Analysis Toolkit (audio_analysis.amplitude).
Re-exports for backwards compatibility; no logic lives here."""
from audio_analysis.amplitude import *  # noqa: F401,F403
from audio_analysis.amplitude import (  # noqa: F401
    compute_envelope, find_first_drop, find_first_break, find_outro_start,
    find_clean_loop_window, snap_to_mik_or_beat, _smooth,
    ENVELOPE_SR, ENVELOPE_HOP_SEC, SMOOTH_WINDOW_SEC, LOW_TIER, HIGH_TIER,
    DROP_SEARCH_START_SEC, DROP_SEARCH_END_SEC, DROP_MIN_RISE, DROP_MIN_LEVEL_AFTER,
    BREAK_MIN_DROP, BREAK_SEARCH_WINDOW_SEC, OUTRO_SEARCH_BACK_SEC, OUTRO_MIN_DROP,
    OUTRO_TAIL_EXCLUDE_SEC, MIK_SNAP_TOLERANCE_SEC, AMP_BASE_CONFIDENCE,
    AMP_MIK_CORROBORATED_BONUS,
)
