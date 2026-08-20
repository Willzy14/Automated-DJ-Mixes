import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))


def _context(beat_levels, *, frames_per_beat=4):
    from align_engine import LoopQualityContext

    mix = np.repeat(np.asarray(beat_levels, dtype=float), frames_per_beat)
    envelopes = {
        name: mix * scale
        for name, scale in (
            ("drums", 1.0), ("bass", 0.8), ("other", 0.6),
            ("vocals", 0.4), ("mix", 1.0),
        )
    }
    return LoopQualityContext(
        Path("synthetic__stemenv.npz"),
        60.0,
        0.0,
        1.0 / frames_per_beat,
        envelopes,
    )


def _write_cache(project: Path, track: str, beat_levels):
    stem_dir = project / "_Stem Analysis"
    audio_dir = project / "Audio"
    stem_dir.mkdir(parents=True)
    audio_dir.mkdir(parents=True)
    wav = audio_dir / f"{track}.wav"
    wav.touch()
    mix = np.repeat(np.asarray(beat_levels, dtype=float), 4)
    arrays = {
        name: mix * scale
        for name, scale in (
            ("drums", 1.0), ("bass", 0.8), ("other", 0.6),
            ("vocals", 0.4), ("mix", 1.0),
        )
    }
    np.savez_compressed(
        stem_dir / f"{track}__stemenv.npz", hop_t=np.array(0.25), **arrays
    )
    metadata = {
        "track": track,
        "bpm": 60.0,
        "n_bars": len(beat_levels) // 4,
        "sections": [{
            "name": "outro_1",
            "label": "outro",
            "start_bar": 0,
            "end_bar": len(beat_levels) // 4,
            "start_sec": 0.0,
            "end_sec": float(len(beat_levels)),
            "stems_on": ["drums"],
        }],
        "signals": {
            "loop_windows": [[0.0, float(len(beat_levels)), 0,
                              len(beat_levels) // 4]],
        },
    }
    (stem_dir / f"SECTIONS_STEM_{track}.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    return wav, stem_dir


def test_window_with_approximately_24_percent_silence_is_rejected():
    from align_engine import evaluate_loop_quality

    levels = [0.1] * 48 + [0.1] * 12 + [1e-4] * 4
    result = evaluate_loop_quality(_context(levels), 48, 64, 48)

    assert result.silence_fraction == pytest.approx(0.25)
    assert "silence_fraction" in result.failed_checks


def test_twelve_beat_period_is_rejected_for_period_only():
    from align_engine import evaluate_loop_quality

    result = evaluate_loop_quality(_context([0.1] * 64), 16, 28, 16)

    assert result.failed_checks == ("period",)


def test_clean_sixteen_beat_window_passes():
    from align_engine import evaluate_loop_quality

    result = evaluate_loop_quality(_context([0.1] * 64), 16, 32, 16)

    assert result.passed


def test_single_deep_beat_dip_is_rejected_for_dip_only():
    """A beat 20 dB under the window median, with no silent frames at all.

    Isolates worst_beat_dip: silence_fraction stays 0.000, so this window is
    rejected by the dip term alone and nothing else.
    """
    from align_engine import evaluate_loop_quality

    levels = [0.1] * 64
    levels[20] = 0.01
    result = evaluate_loop_quality(_context(levels), 16, 32, 16)

    assert result.silence_fraction == pytest.approx(0.0)
    assert result.worst_beat_dip_db == pytest.approx(-20.0, abs=0.1)
    assert result.failed_checks == ("worst_beat_dip",)


def test_window_well_below_insert_level_is_rejected():
    from align_engine import LOOP_MAX_INSERT_LEVEL_DROP_DB, evaluate_loop_quality

    levels = [0.03] * 64
    levels[15] = 0.1
    result = evaluate_loop_quality(_context(levels), 16, 32, 16)

    assert result.insert_level_drop_db > LOOP_MAX_INSERT_LEVEL_DROP_DB
    assert "insert_level_match" in result.failed_checks


def test_match_track_raises_on_ambiguity_and_resolves_unambiguous():
    from apply_loops import _match_track

    ambiguous = [
        (1, 2, "Artist - Song (Instrumental Mix)"),
        (3, 4, "Artist - Song (Extended Mix)"),
    ]
    with pytest.raises(ValueError, match="Ambiguous track match") as exc:
        _match_track("Artist - Song", ambiguous)
    assert "Instrumental Mix" in str(exc.value)
    assert "Extended Mix" in str(exc.value)

    assert _match_track("Artist - Other", [(5, 6, "Artist - Other SW V2")]) \
        == (5, 6, "Artist - Other SW V2")


def test_override_allows_twelve_beat_planner_window_and_logs_waiver(
    tmp_path, caplog,
):
    from align_engine import load_track, pick_clean_drum_loop

    track_name = "Override Artist - Three Bar Loop"
    _wav, stem_dir = _write_cache(tmp_path, track_name, [0.1] * 32)
    override = {
        "overrides": [{
            "track": track_name,
            "source_beat_start": 0,
            "source_beat_end": 12,
            "insert_source_beat": 12,
            "waive_checks": ["period"],
        }]
    }
    (stem_dir / "loop_quality_overrides.json").write_text(
        json.dumps(override), encoding="utf-8"
    )
    track = load_track(stem_dir / f"SECTIONS_STEM_{track_name}.json")

    with caplog.at_level(logging.WARNING):
        picked = pick_clean_drum_loop(
            track, 0, 3, pref=3, insert_bar=3
        )

    assert picked == (0.0, 3.0)
    assert "LOOP QUALITY OVERRIDE APPLIED" in caplog.text
    assert "period" in caplog.text


def test_apply_batch_revalidates_before_mutation(tmp_path):
    from apply_loops import LoopSpec, apply_loops

    track_name = "Apply Gate Artist - Three Bar Loop"
    wav, _stem_dir = _write_cache(tmp_path, track_name, [0.1] * 32)
    wav_xml = str(wav).replace("\\", "/")
    lines = [
        '<AudioTrack Id="1">\n',
        f'  <EffectiveName Value="{track_name}" />\n',
        '  <Sample>\n',
        '    <ArrangerAutomation>\n',
        '      <Events>\n',
        '        <AudioClip Id="2" Time="0">\n',
        '          <CurrentStart Value="0" />\n',
        '          <CurrentEnd Value="12" />\n',
        '          <LoopStart Value="0" />\n',
        '          <LoopEnd Value="12" />\n',
        '          <Name Value="outro_1" />\n',
        f'          <FileRef><Path Value="{wav_xml}" /></FileRef>\n',
        '        </AudioClip>\n',
        '      </Events>\n',
        '    </ArrangerAutomation>\n',
        '  </Sample>\n',
        '</AudioTrack>\n',
    ]
    before = list(lines)
    spec = LoopSpec(track_name, 0, 12, 1, 12, "three_bar_loop")

    with pytest.raises(ValueError, match="Loop quality gate failed") as exc:
        apply_loops(lines, [spec])

    message = str(exc.value)
    assert "period_beats=12" in message
    assert "silence_fraction=" in message
    assert "worst_beat_dip_db=" in message
    assert "insert_level_drop_db=" in message
    assert "self_similarity=" in message
    assert lines == before


def test_vente_style_inconsistent_window_is_rejected():
    from align_engine import evaluate_loop_quality

    levels = [0.1] * 64
    levels[24:28] = [0.1] * 4
    levels[28:32] = [0.04] * 4
    context = _context(levels)
    # Give the stems a texture change as well as the 8 dB energy hole.
    context.envelopes["bass"][28 * 4:32 * 4] *= 0.1
    context.envelopes["other"][24 * 4:28 * 4] *= 0.1
    result = evaluate_loop_quality(context, 24, 32, 24)

    assert "self_similarity" in result.failed_checks


# --- UNMEASURED path -------------------------------------------------------
# Added 2026-08-20 after review found ZERO tests covered it. The gate treats an
# absent envelope cache as "cannot judge" (pass, reported) rather than "bad"
# (reject) -- failing closed there silently stripped loops from every un-analysed
# track and broke two of Sam's hand-corrected reference transitions. These pin
# BOTH halves: absence is tolerated, every other failure still bites.

def _unmeasured(msg="track has no cached stem-envelope path"):
    from align_engine import LoopQualityResult
    return LoopQualityResult(16.0, None, None, None, None, (), (), msg)


def test_unmeasured_window_passes_the_gate():
    result = _unmeasured()
    assert result.passed, "an unmeasurable window must not be treated as a bad one"
    assert result.unmeasured


def test_measured_failure_still_rejected_even_without_error():
    from align_engine import LoopQualityResult
    result = LoopQualityResult(
        12.0, 0.30, -40.0, 9.0, 0.5, ("silence_fraction", "period_beats"), (), None
    )
    assert not result.passed
    assert not result.unmeasured


def test_unmeasured_acceptance_is_reported_not_silent(capsys):
    """Accepting without a verdict must leave a trace -- silence was the defect."""
    from align_engine import _note_unmeasured_acceptance
    _note_unmeasured_acceptance(
        SimpleNamespace(name="Some Track"), _unmeasured(), 100.0, 116.0
    )
    out = capsys.readouterr().out
    assert "UNMEASURED" in out and "Some Track" in out and "100-116" in out


def test_a_measured_pass_prints_nothing(capsys):
    from align_engine import LoopQualityResult, _note_unmeasured_acceptance
    clean = LoopQualityResult(16.0, 0.0, -2.0, 0.5, 0.9, (), (), None)
    _note_unmeasured_acceptance(SimpleNamespace(name="Clean"), clean, 0.0, 16.0)
    assert capsys.readouterr().out == ""


def test_a_bad_waiver_name_raises_instead_of_passing_the_window():
    """A typo'd waiver used to be swallowed into UNMEASURED, silently passing the gate."""
    from align_engine import evaluate_loop_quality

    ctx = _context([1.0] * 64)
    with pytest.raises(ValueError, match="waiver"):
        evaluate_loop_quality(ctx, 0.0, 16.0, 0.0, ("not_a_real_check",))
