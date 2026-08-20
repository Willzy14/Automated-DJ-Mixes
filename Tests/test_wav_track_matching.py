import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))


# Shared collision fixture: two stems whose first 24 normalised chars match
# ("cevin fisher - the freak") - the old 24-char prefix fallback would have
# picked the WRONG wav (the Riva Starr Remix listed first). The canonical
# matcher routes through apply_loops._match_track (exact -> name substring ->
# candidate substring) and correctly returns the Original Mix.
COLLISION_TRACK = "Cevin Fisher - The Freaks Come Out (Original Mix)"
COLLISION_WAVS = [
    Path("Cevin Fisher - The Freaks Come Out (Riva Starr Remix) SW V2.wav"),  # WRONG one FIRST
    Path("Cevin Fisher - The Freaks Come Out (Original Mix) SW V2.wav"),      # the RIGHT one
]


def test_exact_match_normal_case():
    from apply_automation import _wav_for_track

    wavs = [
        Path("Nic Fanciulli - Vente (Extended Mix) 24 Bit MASTER.wav"),
        Path("Nic Fanciulli - Revoloution (Extended Mix) 24 Bit MASTER.wav"),
    ]
    assert _wav_for_track("Nic Fanciulli - Revoloution (Extended Mix) 24 Bit MASTER", wavs) \
        == Path("Nic Fanciulli - Revoloution (Extended Mix) 24 Bit MASTER.wav")


def test_entity_escaped_names_match():
    from apply_automation import _wav_for_track

    # (a) Named XML entity '&amp;' - both html.unescape and apply_loops._normalise
    # decode this one, but the test guards the end-to-end path.
    assert _wav_for_track(
        "BUTCH &amp; Santos -  Come Get Up 24 Bit MASTER",
        [Path("BUTCH & Santos -  Come Get Up 24 Bit MASTER.wav")],
    ) == Path("BUTCH & Santos -  Come Get Up 24 Bit MASTER.wav")

    # (b) Numeric entity '&#39;' - ONLY html.unescape handles this; if anyone
    # ever drops the html.unescape() call in _wav_for_track, this assertion
    # breaks.
    assert _wav_for_track(
        "Artist - Don&#39;t Stop SW V2",
        [Path("Artist - Don't Stop SW V2.wav")],
    ) == Path("Artist - Don't Stop SW V2.wav")


def test_prefix_collision_right_wav_matched():
    from apply_automation import _wav_for_track

    # The canonical matcher must pick the Original Mix, NOT the Riva Starr
    # Remix that shares the 24-char prefix.
    assert _wav_for_track(COLLISION_TRACK, COLLISION_WAVS) == COLLISION_WAVS[1]
    assert _wav_for_track(COLLISION_TRACK, COLLISION_WAVS) != COLLISION_WAVS[0]


def test_prove_the_test_old_prefix_logic_fails_collision():
    # This is the prove-the-test negative control - if the old prefix
    # logic ever comes back, test 3 fails.
    import html

    from apply_automation import _normalise

    def _old_wav_for_track(track_name, wavs):
        n = _normalise(html.unescape(track_name))
        for w in wavs:
            if _normalise(w.stem) == n:
                return w
        for w in wavs:                       # 24-char prefix fallback (the bug)
            wn = _normalise(w.stem)
            if wn[:24] == n[:24]:
                return w
        return None

    old = _old_wav_for_track(COLLISION_TRACK, COLLISION_WAVS)
    assert old == COLLISION_WAVS[0]      # old logic picks the WRONG wav
    assert old != COLLISION_WAVS[1]


def test_no_match_returns_none():
    from apply_automation import _wav_for_track

    assert _wav_for_track(
        "Zombie Nation - Kernkraft 400",
        [Path("Harry Romero - Renegades SW V1.wav")],
    ) is None
