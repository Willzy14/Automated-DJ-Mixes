"""Canary: source audio must not vanish from under a corpus that has been analysed.

Sam's master WAVs under `Test Project/<corpus>/Audio/` were destroyed twice (2026-08-19
and 2026-08-20) by the same mechanism: a Windows junction is not a symlink, so a
recursive delete of a worktree/scratch scaffold TRAVERSES the junction and wipes the
real folder. Both times it went unnoticed until an unrelated test failed on an empty
glob. This makes that signal deliberate and loud.

The invariant, and why it can tell destruction from a machine that simply has no corpus:
`_Stem Analysis` is DERIVED from `Audio`, so analysis artifacts are a witness that the
audio existed. Hence per corpus:

  no analysis + no audio -> skip   (corpus not present on this machine / fresh clone)
  analysis + audio       -> pass
  analysis + NO audio    -> FAIL    (the destruction signature)
  analysis + short audio -> FAIL    (partial wipe)

`Audio/` is gitignored, so this can never be repaired by a checkout -- recovery means
re-copying from Sam's work folders or the G: backups (never moving originals). See
`Obsidian Brain/Ecosystem/Known Workarounds.md`, 2026-08-20, for the recovery routes
and the junction-safety rules that prevent a third occurrence.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Source"))

CORPUS_ROOT = ROOT / "Test Project"

# A corpus whose audio lives somewhere other than Audio/ (or which is analysis-only by
# design) goes here with a reason, so an exemption is always a deliberate, readable act.
EXEMPT = {
    "Stephanes Playlist": "validation-results corpus; analysed elsewhere, no local Audio/ by design",
    "_Bakeoff Corpus": "policy-comparison fixtures, not a source-audio corpus",
    # UNRESOLVED, awaiting Sam (found 2026-08-20 by this canary's first run): 8 of 9
    # analysed tracks have no wav anywhere in the corpus, and there is no Audio/ dir at
    # all -- the same shape as the destroyed 14.08.26. Either older junction-traversal
    # damage or the deliberate disk-space clear noted in AI_CONTEXT ("cleaned off disk
    # for space at some point"). Exempted so the canary stays green-means-green for the
    # ACTIVE corpus rather than training everyone to ignore a permanently red suite --
    # NOT because it was judged harmless. Sam decides: recover from the work folders /
    # G: backups, or accept the loss and delete this entry with the corpus.
    "12.08.26 Deep Soulful 10": "UNRESOLVED audio loss (8/9) - see comment; Sam to confirm recover-or-accept",
}


#: Folders that hold DERIVED audio, not source material. A rendered mix in
#: Output/ is not evidence that the source tracks survived, so the sweep must
#: not count it - otherwise a corpus whose Audio/ was wiped still "has wavs".
NON_SOURCE_DIRS = {"Output", "_Stem Analysis", "Visualisations", "Hints"}

#: Anything smaller than this is not a real master. A zero-byte or truncated
#: file used to satisfy the canary purely by existing (found 2026-08-27).
MIN_WAV_BYTES = 4096


def _analysis_witnesses(corpus: Path) -> list[str]:
    """Artifacts proving this corpus HAD source audio, whether or not
    `_Stem Analysis` still exists.

    The original canary keyed solely on `_Stem Analysis/SECTIONS_STEM_*.json`.
    That leaves the incident's worst shape undetected: a wipe that takes the
    analysis folder TOO drops the corpus out of the analysed set entirely, so
    the canary SKIPS and the suite goes green on a destroyed corpus. The
    observed incidents happened to spare `_Stem Analysis` (one courier
    junctioned `Audio` but COPIED the analysis), which is luck, not design.

    So any derived artifact counts as a witness. Only a corpus directory with
    nothing in it at all is treated as "not present on this machine".
    """
    found: list[str] = []
    stem_dir = corpus / "_Stem Analysis"
    if stem_dir.is_dir() and list(stem_dir.glob("SECTIONS_STEM_*.json")):
        found.append("_Stem Analysis/SECTIONS_STEM_*.json")
    for pattern, label in (("Output/*.als", "Output/*.als"),
                           ("Output/*.json", "Output/*.json"),
                           ("Hints/*.json", "Hints/*.json"),
                           ("_Stem Analysis/*.npz", "_Stem Analysis/*.npz")):
        if list(corpus.glob(pattern)):
            found.append(label)
    return found


def _analysed_corpora():
    if not CORPUS_ROOT.is_dir():
        return []
    out = []
    for d in sorted(p for p in CORPUS_ROOT.iterdir() if p.is_dir()):
        if d.name in EXEMPT:
            continue
        if _analysis_witnesses(d):
            out.append(d)
    return out


ANALYSED = _analysed_corpora()

pytestmark = pytest.mark.skipif(
    not ANALYSED,
    reason="no analysed corpus on this machine -- nothing to canary",
)


def _audio_wavs(corpus: Path):
    """Every SOURCE wav the corpus can currently see, at any depth.

    Discovered by sweep rather than assumed: corpora legitimately park tracks in
    `_Unused Audio/` and `_Excluded Audio/` (deliberately set aside), and a mix subset
    like `Audio Mix 12/` is a real home too -- it held the 12 survivors that made the
    2026-08-20 recovery possible. Any wav-bearing folder counts; only a track with NO
    wav anywhere is evidence of loss. (An earlier draft of this test omitted
    `_Unused Audio` and cried wolf on 6 healthy tracks -- hence the sweep.)

    Two holes closed 2026-08-27:
      * the sweep was ONE level deep (`corpus/*/*.wav`), so a track nested any
        further read as missing. Now recursive.
      * derived audio counted. A rendered mix in `Output/` is not evidence that
        the sources survived, so NON_SOURCE_DIRS are excluded - otherwise a
        corpus whose `Audio/` was wiped still looks populated.
    """
    wavs = {}
    for w in corpus.rglob("*.wav"):
        rel = w.relative_to(corpus)
        if rel.parts and rel.parts[0] in NON_SOURCE_DIRS:
            continue
        wavs.setdefault(w.stem, w)
    return wavs


def _unreadable(path: Path) -> str | None:
    """Reason this wav is not a usable master, or None if it looks fine.

    Existence was the whole test before. A zero-byte or header-only file - the
    shape a partial wipe or an interrupted copy leaves behind - sailed through
    purely by having the right name.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"unstatable ({exc.__class__.__name__})"
    if size < MIN_WAV_BYTES:
        return f"{size} bytes"
    try:
        import soundfile as sf
        info = sf.info(str(path))
    except Exception as exc:
        return f"undecodable ({exc.__class__.__name__})"
    if info.frames <= 0:
        return "zero frames"
    return None


@pytest.mark.parametrize("corpus", ANALYSED, ids=lambda p: p.name)
def test_analysed_corpus_still_has_its_audio(corpus):
    analysed = {
        p.name[len("SECTIONS_STEM_"):-len(".json")]
        for p in (corpus / "_Stem Analysis").glob("SECTIONS_STEM_*.json")
    }
    wavs = _audio_wavs(corpus)

    assert wavs, (
        f"CORPUS AUDIO DESTROYED: '{corpus.name}' has {len(analysed)} analysed tracks but "
        f"NO wav files in Audio/, 'Audio Mix 12/' or '_Excluded Audio/'. Analysis is derived "
        f"from audio, so the audio existed and is now gone -- this is the junction-traversal "
        f"wipe (see Tests/test_corpus_audio_canary.py's docstring). Recover before doing "
        f"anything else; do NOT re-run analysis over an empty folder."
    )

    missing = sorted(analysed - set(wavs))
    assert not missing, (
        f"CORPUS AUDIO PARTIALLY DESTROYED: '{corpus.name}' is missing the source wav for "
        f"{len(missing)} of {len(analysed)} analysed tracks. Missing: {missing[:5]}"
        f"{' ...' if len(missing) > 5 else ''}. Analysis exists for each, so each wav was "
        f"present when it was analysed."
    )

    # Present is not the same as intact. A truncated or zero-byte file has the
    # right name and none of the audio.
    damaged = []
    for stem in sorted(analysed):
        reason = _unreadable(wavs[stem])
        if reason:
            damaged.append(f"{stem} ({reason})")
    assert not damaged, (
        f"CORPUS AUDIO DAMAGED: '{corpus.name}' has {len(damaged)} analysed track(s) whose "
        f"wav exists but is not usable audio: {damaged[:5]}"
        f"{' ...' if len(damaged) > 5 else ''}. A zero-byte or truncated file is the "
        f"signature of an interrupted copy or a partial wipe -- recover it, do not "
        f"re-analyse over it."
    )


def test_canary_survives_a_wipe_that_also_takes_the_analysis_folder(tmp_path):
    """The hole the original canary left open, now closed.

    Keying the witness solely on `_Stem Analysis` meant a wipe taking BOTH the
    audio and the analysis dropped the corpus out of the analysed set, so the
    canary skipped and the suite went green on a destroyed corpus. The observed
    incidents spared `_Stem Analysis` by luck, not design.
    """
    corpus = tmp_path / "14.09.99 Fake Mix"
    (corpus / "Output").mkdir(parents=True)
    (corpus / "Output" / "Fake Mix V1.als").write_bytes(b"\x1f\x8b" + b"0" * 64)

    # No _Stem Analysis at all, and no source audio - the worst-case shape.
    assert _analysis_witnesses(corpus), (
        "a corpus carrying Output artifacts must still count as analysed, or a "
        "wipe that takes _Stem Analysis too goes undetected")

    # And a rendered mix must NOT masquerade as surviving source audio.
    (corpus / "Output" / "Fake Mix V1.wav").write_bytes(b"RIFF" + b"0" * 8192)
    assert _audio_wavs(corpus) == {}, \
        "Output/ holds derived audio; counting it hides the loss of the sources"


def test_zero_byte_wav_is_not_accepted_as_audio(tmp_path):
    """Existence was the whole check; a named empty file used to pass."""
    empty = tmp_path / "Artist - Track.wav"
    empty.write_bytes(b"")
    assert _unreadable(empty) == "0 bytes"

    stub = tmp_path / "Artist - Stub.wav"
    stub.write_bytes(b"RIFF" + b"\x00" * 40)
    assert _unreadable(stub) is not None, \
        "a header-sized fragment is not a master"
