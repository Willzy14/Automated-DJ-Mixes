import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))


def _clip(last_sec):
    return ET.fromstring(
        '<AudioClip>'
        '<WarpMarker SecTime="0.5" BeatTime="0" />'
        f'<WarpMarker SecTime="{last_sec}" BeatTime="400" />'
        '</AudioClip>'
    )


def test_warp_summary_records_marker_grid_and_encoded_bpm():
    from automated_dj_mixes.warp_contract import summarize_warp_grid

    summary = summarize_warp_grid(_clip(200.5))

    assert summary.marker_count == 2
    assert summary.source_grid_bpm == pytest.approx(120.0)
    assert len(summary.grid_sha256) == 64


def test_track_warp_summary_rejects_different_clip_grids():
    from automated_dj_mixes.warp_contract import summarize_track_warp_grids

    track = ET.Element("AudioTrack")
    track.append(_clip(200.5))
    track.append(_clip(202.0))

    with pytest.raises(ValueError, match="do not share one warp grid"):
        summarize_track_warp_grids(track)
