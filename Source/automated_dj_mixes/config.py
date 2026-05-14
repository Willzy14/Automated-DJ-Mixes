"""Settings loader — reads Config/settings.json and provides defaults."""

import json
from pathlib import Path

DEFAULTS = {
    "crossfade_bars": 32,
    "filter_automation_depth_db": -16,
    "gain_strategy": "match_quietest",
    "max_gain_reduction_db": 12,
    "true_peak_ceiling_db": -1,
    "ableton_version": "12",
    "default_project_tempo": 128,
    "versioning_prefix": "V",
}


def load_config(config_path: Path | None = None) -> dict:
    """Load settings from JSON file, falling back to defaults for missing keys."""
    settings = dict(DEFAULTS)
    if config_path and config_path.exists():
        with open(config_path) as f:
            settings.update(json.load(f))
    return settings
