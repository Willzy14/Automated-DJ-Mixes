"""Ableton's WarpMode numbers, pinned against evidence from a live session.

These constants were wrong for the whole life of the project: Re-Pitch was set
to 6 and Complex Pro to 4. Live reads 4 as plain Complex and 6 as Complex Pro,
so every mix silently got the older stretch algorithm exactly where quality
mattered most, and Re-Pitch - a deliberate creative choice of Sam's, his +/-1
BPM rule - was never once applied.

Verified 2026-08-13 through Producer Pal against the open session
"Deep Soulful Mix V1 SW Tweaks": a clip the pipeline wrote as 4 reported
warpMode "complex", and one written as 6 reported "pro".

The bug was invisible in the file. Only Live could reveal it, which is the
argument for the post-build check that reads the generated set back through
Live rather than trusting the XML we just wrote.
"""

from pathlib import Path

from automated_dj_mixes.warping import (
    WARP_MODE_BEATS,
    WARP_MODE_COMPLEX,
    WARP_MODE_COMPLEX_PRO,
    WARP_MODE_REPITCH,
    WARP_MODE_TEXTURE,
    WARP_MODE_TONES,
    choose_dj_mix_warp_mode,
    choose_warp_mode,
)


def test_measured_enum_values():
    """4 and 6 were read back from Live itself - do not 'tidy' these."""
    assert WARP_MODE_COMPLEX == 4        # Live reports "complex"
    assert WARP_MODE_COMPLEX_PRO == 6    # Live reports "pro"


def test_repitch_is_not_complex_pro():
    """The exact confusion that caused the bug: these must never be equal, and
    Re-Pitch must not be 6."""
    assert WARP_MODE_REPITCH != WARP_MODE_COMPLEX_PRO
    assert WARP_MODE_REPITCH != 6
    assert WARP_MODE_COMPLEX_PRO != 4


def test_enum_is_contiguous_in_ui_order():
    """Beats, Tones, Texture, Re-Pitch, Complex ... Complex Pro. Re-Pitch at 3
    is inferred from this ordering with measured anchors either side."""
    assert [WARP_MODE_BEATS, WARP_MODE_TONES, WARP_MODE_TEXTURE,
            WARP_MODE_REPITCH, WARP_MODE_COMPLEX] == [0, 1, 2, 3, 4]


def test_stretching_selects_complex_pro_not_complex():
    """A 6 BPM move must time-stretch on the BETTER algorithm. Under the old
    constants this returned 4, i.e. plain Complex."""
    assert choose_dj_mix_warp_mode(124.0, 118.0) == WARP_MODE_COMPLEX_PRO
    assert choose_dj_mix_warp_mode(124.0, 118.0) != WARP_MODE_COMPLEX


def test_small_move_actually_repitches():
    """Sam's +/-1 BPM rule. Under the old constants this returned 6, which Live
    renders as Complex Pro - so the track stretched instead of re-pitching and
    the creative choice never reached the audio."""
    assert choose_dj_mix_warp_mode(121.0, 121.0) == WARP_MODE_REPITCH
    assert choose_warp_mode(126.0, 126.0) == WARP_MODE_REPITCH
    assert choose_warp_mode(126.0, 126.0) != WARP_MODE_COMPLEX_PRO


def test_writer_and_validator_agree_on_every_mode():
    """The half-migration that actually happened: warping.py was corrected but
    the MixPlan labeller and the reconciliation validator kept the OLD literals,
    so a Complex Pro track would have been frozen as "repitch" and then failed
    its own reconciliation. Symbol-only greps missed it because those sites used
    bare numbers.

    Pins the round trip: number -> label -> expected number.
    """
    import re
    from pathlib import Path

    src = Path("Source")

    # The labeller in propose_arrangement and the expectation in
    # validate_mix_plan_als must both key off the constants, not literals.
    for rel, needle in [
        ("propose_arrangement.py", 'if mode == WARP_MODE_REPITCH else "complex_pro"'),
        ("validate_mix_plan_als.py", '"repitch": WARP_MODE_REPITCH'),
        ("validate_mix_plan_als.py", '"complex_pro": WARP_MODE_COMPLEX_PRO'),
    ]:
        text = (src / rel).read_text(encoding="utf-8")
        assert needle in text, f"{rel} no longer keys off the constants: {needle}"

    # Broad sweep: no bare 4/6 anywhere in a warp context. The first version of
    # this test banned specific strings and so missed a console label reading
    # `== 6` -> "Re-Pitch", which printed the WRONG mode for every Complex Pro
    # track. A label that lies is exactly how the original bug survived.
    import itertools
    for rel in ("propose_arrangement.py", "validate_mix_plan_als.py",
                "automated_dj_mixes/orchestrator.py"):
        for lineno, line in enumerate(
                (src / rel).read_text(encoding="utf-8").splitlines(), 1):
            low = line.lower()
            if not any(k in low for k in ("warp", "repitch", "complex")):
                continue
            if "warp_mode_" in low or line.strip().startswith("#"):
                continue
            assert not re.search(r"==\s*[46]", line), (
                f"{rel}:{lineno} compares a bare warp literal: {line.strip()}")

    # And no consumer may re-introduce a bare warp-mode literal.
    banned = [
        ("propose_arrangement.py", r"warp_mode not in \(4, 6\)"),
        ("propose_arrangement.py", r'"repitch": 6'),
        ("validate_mix_plan_als.py", r'\{"repitch": 6, "complex_pro": 4\}'),
    ]
    for rel, pattern in banned:
        text = (src / rel).read_text(encoding="utf-8")
        assert not re.search(pattern, text), f"{rel} re-introduced a bare literal: {pattern}"


def test_playback_policy_accepts_repitch():
    """apply_playback_policy rejected anything outside (4, 6), so a genuinely
    re-pitched track (now 3) would have raised the moment a MixPlan was used.
    The 12.08.26 mix only escaped because it was built without one."""
    text = (Path(__file__).parent.parent / "Source" / "propose_arrangement.py").read_text(encoding="utf-8")
    assert "if warp_mode not in (WARP_MODE_REPITCH, WARP_MODE_COMPLEX_PRO):" in text
