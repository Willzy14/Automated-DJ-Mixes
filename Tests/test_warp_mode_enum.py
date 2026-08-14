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
