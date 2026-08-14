"""Reading and clearing MainTrack tempo envelopes.

Two defects, both found by Codex reviewing the tempo-arc wiring, both of which
would have made the arc unverifiable:

1. The reader collected BPM values but discarded their TIMES, so a caller could
   only ask "does an envelope exist" - never "is it the envelope we planned".
   For a tempo curve the positions are the whole point: the right BPMs in the
   wrong places is exactly the failure worth catching.

2. The remover stopped after the first matching envelope. A second one for the
   same target survived and Live obeyed the leftover - the failure that once made
   a mix play at 123 BPM while the static value read 120.49.
"""

import xml.etree.ElementTree as ET

from automated_dj_mixes.als_generator import _remove_existing_envelope_for_target
from validate_mix_plan_als import _main_tempo_state

TARGET = "8"


def _envelope_lines(target_id: str, points) -> list[str]:
    out = [f'<AutomationEnvelope Id="9">', f'<PointeeId Value="{target_id}" />']
    out += [f'<FloatEvent Time="{t}" Value="{v}" />' for t, v in points]
    out.append("</AutomationEnvelope>")
    return out


def test_remover_clears_every_matching_envelope():
    lines = (["<head>"] + _envelope_lines(TARGET, [(0, 120)])
             + ["<middle>"] + _envelope_lines(TARGET, [(0, 123)]) + ["<tail>"])
    removed = _remove_existing_envelope_for_target(lines, TARGET)
    assert removed > 0
    assert not any("AutomationEnvelope" in line for line in lines), (
        "a surviving second envelope is what Live would obey")
    assert [l for l in lines] == ["<head>", "<middle>", "<tail>"]


def test_remover_leaves_other_targets_alone():
    lines = ["<head>"] + _envelope_lines("99", [(0, 120)]) + ["<tail>"]
    before = list(lines)
    _remove_existing_envelope_for_target(lines, TARGET)
    assert lines == before


def test_reader_keeps_event_times():
    """Without times the envelope cannot be compared against a planned curve."""
    xml = f"""<Ableton><MainTrack>
      <Tempo><Manual Value="121.5" /><AutomationTarget Id="{TARGET}" /></Tempo>
      <AutomationEnvelope><PointeeId Value="{TARGET}" />
        <FloatEvent Time="640" Value="124.0" />
        <FloatEvent Time="0" Value="121.5" />
      </AutomationEnvelope>
    </MainTrack></Ableton>"""
    manual, events = _main_tempo_state(ET.fromstring(xml))
    assert manual == 121.5
    assert events == [(0.0, 121.5), (640.0, 124.0)], "times kept and sorted"


def test_reader_reports_no_events_when_envelope_absent():
    xml = f"""<Ableton><MainTrack>
      <Tempo><Manual Value="118.0" /><AutomationTarget Id="{TARGET}" /></Tempo>
    </MainTrack></Ableton>"""
    manual, events = _main_tempo_state(ET.fromstring(xml))
    assert manual == 118.0 and events == []
