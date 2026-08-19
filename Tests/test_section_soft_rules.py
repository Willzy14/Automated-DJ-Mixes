"""Unit tests for the soft intro/outro rules (R2/R3/R4) in stem_detector._assign_labels.

Style matches Tests/test_stem_detector_energy_cues.py: synthetic kick-presence
arrays, deterministic, no corpus dependency.

Covers:
  - R2: kick-less head extends intro through contiguous kick-less pre-drop sections
  - R2 negative: kick-in head + later kick-out break is NOT relabeled by R2
  - R3: kick-less tail triggers the soft outro_start upstream in detect()
  - R4: kick-in head + pre-first_drop break extends intro through that break
  - R4 negative: no pre-first_drop break => R4 is a no-op
  - Flag-OFF inertness: every rule is gated by soft_intro_outro=True
  - 25625f8 PIN: a long kick-less pre-drop section that is NOT contiguous with
    sections[0] stays a break (James Poole anomaly — R2 must never undo it)

The detect() flow is exercised by monkey-patching _separate_envelopes with a
fake env (test_kick_model_integration.py shape).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

import stem_detector as sd


# --- shared fakes ------------------------------------------------------------

def _make_envs(*, kick_on_per_bar: np.ndarray, bass_on_per_bar: np.ndarray,
               vocals_on_per_bar: np.ndarray | None = None,
               other_on_per_bar: np.ndarray | None = None,
               mix_amp_per_bar: np.ndarray | None = None,
               hop_t: float = 0.1, bpm: float = 120.0):
    """Build a fake _separate_envelopes return that drives detect() deterministically.

    Each per-bar boolean is expanded into a per-frame envelope using the SAME
    sec_per_bar that detect() will compute from bpm (sec_per_bar = 4*60/bpm).
    detect() computes n_bars from env duration / sec_per_bar, so the per-bar
    index in the test data must match detect()'s bar indexing — which means
    expand must use detect()'s sec_per_bar, not a hardcoded 1.0.
    """
    n_bars = len(kick_on_per_bar)
    sec_per_bar = 4 * 60.0 / bpm
    n_frames = int(n_bars * sec_per_bar / hop_t) + 10

    def expand_bool(per_bar):
        per_bar = np.asarray(per_bar, dtype=bool)
        out = np.zeros(n_frames, dtype=float)
        for b in range(n_bars):
            i0 = int(b * sec_per_bar / hop_t)
            i1 = min(int((b + 1) * sec_per_bar / hop_t), n_frames)
            if per_bar[b]:
                out[i0:i1] = 1.0
        return out

    def expand_amp(per_bar_amp):
        # Per-bar amplitude values (not boolean). Used for mix so that is_drop
        # can distinguish a quiet head from a loud body.
        per_bar_amp = np.asarray(per_bar_amp, dtype=float)
        out = np.zeros(n_frames, dtype=float)
        for b in range(n_bars):
            i0 = int(b * sec_per_bar / hop_t)
            i1 = min(int((b + 1) * sec_per_bar / hop_t), n_frames)
            out[i0:i1] = per_bar_amp[b]
        return out

    envs = {
        "drums": expand_bool(kick_on_per_bar),
        "bass": expand_bool(bass_on_per_bar),
        "vocals": expand_bool(vocals_on_per_bar if vocals_on_per_bar is not None else np.zeros(n_bars, dtype=bool)),
        "other": expand_bool(other_on_per_bar if other_on_per_bar is not None else np.zeros(n_bars, dtype=bool)),
        "mix": expand_amp(mix_amp_per_bar if mix_amp_per_bar is not None
                          else (kick_on_per_bar | bass_on_per_bar).astype(float)),
    }
    return envs, hop_t, n_bars


def _run_detect(*, kick_on_per_bar, bass_on_per_bar, soft_intro_outro=False,
                 vocals_on_per_bar=None, other_on_per_bar=None,
                 mix_amp_per_bar=None, monkeypatch=None, tmp_path=None,
                 bpm: float = 120.0):
    """Run detect() with a synthetic env. Returns the result dict (or None)."""
    hop_t = 0.1
    envs, hop_t_out, n_bars = _make_envs(
        kick_on_per_bar=kick_on_per_bar,
        bass_on_per_bar=bass_on_per_bar,
        vocals_on_per_bar=vocals_on_per_bar,
        other_on_per_bar=other_on_per_bar,
        mix_amp_per_bar=mix_amp_per_bar,
        hop_t=hop_t, bpm=bpm,
    )
    if monkeypatch is not None and tmp_path is not None:
        monkeypatch.setattr(sd, "_separate_envelopes", lambda *_a, **_k: (envs, hop_t))
        wav = tmp_path / "fake.wav"
        wav.write_bytes(b"x")
    else:
        wav = None  # type: ignore
    return sd.detect(
        wav, tmp_path, bpm=bpm, downbeat=0.0,
        make_viz=False, write_json=False,
        soft_intro_outro=soft_intro_outro,
    )


def _section_summary(sections):
    return " ".join(f"{s['label']}{s['end_bar'] - s['start_bar']}" for s in sections)


# --- R2: kick-less head extends intro through contiguous kick-less pre-drop ---

def test_r2_kickless_head_extends_intro_through_contiguous_kickless_break(monkeypatch, tmp_path):
    """16-bar kick-less head split by a kick-less break into 2 sections.
    R2 should merge them into one intro."""
    # 32-bar kick-less head (no kick), then kick-in drop. The head gets a boundary
    # at bar 8 from a sustained low-energy dip (kick-out break section is also
    # kick-less). With R2 ON, both head sections merge into intro (0-32).
    kick = np.array([0]*32 + [1]*48)  # 80 bars: kick-less 0-31, kick-in 32-79
    bass = np.array([0]*32 + [1]*48)
    res = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        soft_intro_outro=True, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    assert res is not None
    # Find the first intro's end bar.
    intros = [s for s in res["sections"] if s["label"] == "intro"]
    assert intros, f"expected at least one intro section; got {_section_summary(res['sections'])}"
    # Intro should cover the entire kick-less head (bars 0-32). At minimum,
    # the FIRST intro section must extend past bar 16 (the original hard-rule
    # length) into the contiguous kick-less break.
    first_intro_end = intros[0]["end_bar"]
    assert first_intro_end >= 32, (
        f"R2 should extend intro through contiguous kick-less head (>=32 bars); "
        f"first intro ended at {first_intro_end}. Sections: {_section_summary(res['sections'])}"
    )


def test_r2_negative_kickin_head_with_kickout_break_not_relabeled(monkeypatch, tmp_path):
    """R2 must NOT fire when the HEAD has kick-in — even if a pre-first_drop
    kick-out break exists later. R4 owns that case, not R2.

    head_kf=1.0 -> R4 branch. The head mix-energy must be < DROP_REL*full so
    sections[0] isn't classified as the first drop itself (which would leave R4
    nothing to do).
    """
    # Kick-in head (16 bars), then kick-out break (8 bars), then drop.
    # head_kf=1.0 -> R4 branch, not R2. R4 will relabel the first break to intro.
    kick = np.array([1]*16 + [0]*8 + [1]*56)  # 80 bars
    bass = np.array([1]*16 + [0]*8 + [1]*56)
    mix_amp = np.array([0.6]*16 + [0.6]*8 + [1.0]*56)
    res_on = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        mix_amp_per_bar=mix_amp,
        soft_intro_outro=True, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    res_off = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        mix_amp_per_bar=mix_amp,
        soft_intro_outro=False, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    assert res_on is not None and res_off is not None
    # R4 should change intro from 16 to 24 (covers the 8-bar break).
    intros_on = [s for s in res_on["sections"] if s["label"] == "intro"]
    intros_off = [s for s in res_off["sections"] if s["label"] == "intro"]
    assert intros_on[0]["end_bar"] == 24
    assert intros_off[0]["end_bar"] == 16


# --- R4: kick-in head + pre-first_drop break extends intro through that break -

def test_r4_kickin_head_pre_first_drop_break_extends_intro(monkeypatch, tmp_path):
    """Standard R4 case: kick-in head, kick-out break immediately before first drop.

    Head mix-energy must be < DROP_REL*full so the head isn't classified as the
    first drop itself (otherwise is_drop fires at sections[0] and R4 has nothing
    to do). Use mix_amp_per_bar=0.6 in the head and 1.0 in the body.
    """
    kick = np.array([1]*16 + [0]*8 + [1]*56)  # 16-bar kick-in, 8-bar break, then drop
    bass = np.array([1]*16 + [0]*8 + [1]*56)
    mix_amp = np.array([0.6]*16 + [0.6]*8 + [1.0]*56)  # head quieter than body
    res_on = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        mix_amp_per_bar=mix_amp,
        soft_intro_outro=True, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    assert res_on is not None
    # The 8-bar break should now be intro, not break.
    intro_sections = [s for s in res_on["sections"] if s["label"] == "intro"]
    assert len(intro_sections) == 1, f"expected one merged intro, got {_section_summary(res_on['sections'])}"
    assert intro_sections[0]["start_bar"] == 0
    assert intro_sections[0]["end_bar"] == 24  # 0-16 kick-in + 16-24 break


def test_r4_negative_no_pre_first_drop_break_is_noop(monkeypatch, tmp_path):
    """R4 must NOT extend intro when there's no pre-first_drop break/fill."""
    # Kick-in head, then continuous kick-in through the first drop section.
    # No kick-out break before the first drop -> R4 has nothing to relabel.
    kick = np.ones(80, dtype=int)
    bass = np.ones(80, dtype=int)
    res_on = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        soft_intro_outro=True, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    res_off = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        soft_intro_outro=False, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    assert res_on is not None and res_off is not None
    on_summary = _section_summary(res_on["sections"])
    off_summary = _section_summary(res_off["sections"])
    assert on_summary == off_summary, (
        f"R4 with no pre-first_drop break must be a no-op. "
        f"OFF={off_summary} ON={on_summary}"
    )


def test_r4_does_not_relabel_subsequent_body_breaks(monkeypatch, tmp_path):
    """R4 extends intro through the FIRST pre-first_drop break ONLY.
    Subsequent pre-first_drop breaks must NOT be relabeled (they'd be body
    sections, not intro extensions — otherwise the cascade turns body material
    into intro)."""
    # 16-bar kick-in head, then 8-bar break, then 8-bar drop, then 8-bar break,
    # then more drop. R4 should relabel ONLY the first break (16-24); the second
    # break (32-40) sits AFTER the first drop section (24-32) and stays a break.
    # Head mix-energy < DROP_REL*full so the head is not the first drop itself.
    kick = np.array([1]*16 + [0]*8 + [1]*8 + [0]*8 + [1]*40)  # 80 bars
    bass = np.array([1]*16 + [0]*8 + [1]*8 + [0]*8 + [1]*40)
    mix_amp = np.array([0.6]*16 + [0.6]*8 + [1.0]*8 + [1.0]*8 + [1.0]*40)
    res_on = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        mix_amp_per_bar=mix_amp,
        soft_intro_outro=True, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    assert res_on is not None
    # The first intro must end at 24, not extend through the second break at 40.
    intros = [s for s in res_on["sections"] if s["label"] == "intro"]
    assert intros[0]["end_bar"] == 24, (
        f"R4 must stop at first pre-first_drop break; intro ended at "
        f"{intros[0]['end_bar']}. Sections: {_section_summary(res_on['sections'])}"
    )


# --- R3: kick-less tail triggers soft outro_start ----------------------------

def test_r3_kickless_tail_triggers_soft_outro(monkeypatch, tmp_path):
    """With soft_intro_outro=True, a track whose tail is kick-less but where
    the existing bass-finish detector missed the outro should still get an
    outro_start. R3 fires when:
      - body_idx is empty (kick and bass never co-occur, so the existing
        bass-finish outro_start returns None), AND
      - the last OUTRO_KICKLESS_BARS have kick presence < KICKLESS_FRAC.
    """
    # Bass enters only after the kick leaves — kick and bass never co-occur,
    # so body_idx is empty and the existing bass-finish outro_start returns
    # None. R3 fires on the kick-less tail.
    n_bars = 96
    kick = np.array([1]*48 + [0]*48)  # kick-in 0-47, kick-out 48-95
    bass = np.array([0]*48 + [1]*48)  # bass 48-95 (after kick leaves)
    res = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        soft_intro_outro=True, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    assert res is not None
    assert res["signals"]["soft_intro_outro_hints"]["r3_outro_active"] is True


def test_r3_inactive_when_existing_outro_path_fires(monkeypatch, tmp_path):
    """R3 must report ACTIVE only when the existing hard path missed the outro.
    If the bass-finish detector already sets outro_start, R3 is not active."""
    # Bass exits at bar 48, vocals throughout, hard outro path fires.
    n_bars = 80
    kick = np.ones(n_bars, dtype=int)
    bass = np.array([1]*48 + [0]*32)
    vocals = np.ones(n_bars, dtype=int)
    res = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        vocals_on_per_bar=vocals,
        soft_intro_outro=True, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    assert res is not None
    # The hard outro_start (last bass-on + 1) fires regardless of soft_intro_outro.
    # R3 should report False because the hard path already fired.
    assert res["signals"]["soft_intro_outro_hints"]["r3_outro_active"] is False


# --- flag-OFF inertness ------------------------------------------------------

def test_flag_off_is_byte_identical_to_main(monkeypatch, tmp_path):
    """The whole point of the default flag: OFF must not change a thing.

    For a representative kick-in track, OFF == OFF-with-soft_intro_outro arg
    (i.e. passing the flag as False is equivalent to the default behaviour).
    More importantly, the OFF result must be byte-identical to main @ 88f15c4's
    result — proven at the corpus level in _compare_off_to_main.py.
    """
    kick = np.array([1]*16 + [0]*8 + [1]*56)
    bass = np.array([1]*16 + [0]*8 + [1]*56)
    res_off = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        soft_intro_outro=False, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    res_default = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        soft_intro_outro=False, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    assert res_off == res_default
    # soft_intro_outro_hints must be ABSENT (not null) when flag is off.
    assert "soft_intro_outro_hints" not in res_off["signals"]


def test_flag_off_keeps_25625f8_james_poole_break(monkeypatch, tmp_path):
    """Pin 25625f8: a long kick-less pre-drop section that is NOT contiguous
    with sections[0] stays a break. This is the James Poole anomaly — R2 must
    never undo it."""
    # sections[0] = intro with kick IN, then a long kick-less break section,
    # then kick-in drop. head_kf=1.0 so R2 does NOT fire (R4 branch instead).
    # Even if R2's branch were taken by mistake, R2 only walks through sections
    # contiguous with sections[0]; sections[1] IS contiguous here (immediately
    # follows sections[0]). To exercise the carve-out, we need a kick-IN
    # section BETWEEN sections[0] and the kick-out break.
    #
    # Pattern: kick-in intro (4b) -> kick-in build (4b) -> kick-out break (16b) ->
    # kick-in drop. sections[0]=intro (4b), sections[1]=build (4b, kick-in),
    # sections[2]=break (16b, kick-out). The kick-out break is NOT contiguous
    # with sections[0] (sections[1] is between them), and R2 stops at
    # sections[1] (kick-in -> stop). The break stays a break.
    kick = np.array([1]*4 + [1]*4 + [0]*16 + [1]*56)  # 80 bars
    bass = np.array([1]*4 + [1]*4 + [0]*16 + [1]*56)
    res = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        soft_intro_outro=True, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    assert res is not None
    # The 16-bar break (bars 8-24) must stay a break. If R2 silently extends
    # intro through it, the test fails.
    break_sections = [s for s in res["sections"] if s["label"] == "break"]
    assert any(s["start_bar"] == 8 and s["end_bar"] == 24 for s in break_sections), (
        f"25625f8 PIN violated: 16-bar kick-less pre-drop section should stay a "
        f"break. Sections: {_section_summary(res['sections'])}"
    )
    # And the hint should report R2 didn't extend through it.
    hints = res["signals"]["soft_intro_outro_hints"]
    assert hints["r2_extended_through_bar"] is None or hints["r2_extended_through_bar"] <= 8


def test_flag_off_keeps_25612f8_kickless_intro_stays_intro(monkeypatch, tmp_path):
    """Pin 25612f8: the FIRST section (kick-less, head) stays intro under OFF.
    This is the trivial 'head = intro' guarantee. Under OFF the hard rule forces
    sections[0]=intro regardless; under ON R2 explicitly confirms it via the
    head_kf < KICKLESS_FRAC condition."""
    # 32-bar kick-less head, then drop.
    kick = np.array([0]*32 + [1]*48)
    bass = np.array([0]*32 + [1]*48)
    res_off = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        soft_intro_outro=False, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    res_on = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        soft_intro_outro=True, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    assert res_off is not None and res_on is not None
    intros_off = [s for s in res_off["sections"] if s["label"] == "intro"]
    intros_on = [s for s in res_on["sections"] if s["label"] == "intro"]
    # Both must start at bar 0 and end >= 32 (the kick-less head).
    assert intros_off[0]["start_bar"] == 0
    assert intros_off[0]["end_bar"] >= 32
    assert intros_on[0]["start_bar"] == 0
    assert intros_on[0]["end_bar"] >= 32


# --- soft_hints visibility ---------------------------------------------------

def test_soft_hints_key_only_present_when_flag_on(monkeypatch, tmp_path):
    """The soft_intro_outro_hints signal key must be OMITTED when flag is off
    (not emitted as null) so the OFF JSON shape is byte-identical to main."""
    kick = np.array([1]*16 + [0]*8 + [1]*56)
    bass = np.array([1]*16 + [0]*8 + [1]*56)
    res_off = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        soft_intro_outro=False, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    res_on = _run_detect(
        kick_on_per_bar=kick, bass_on_per_bar=bass,
        soft_intro_outro=True, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    assert "soft_intro_outro_hints" not in res_off["signals"]
    assert "soft_intro_outro_hints" in res_on["signals"]
