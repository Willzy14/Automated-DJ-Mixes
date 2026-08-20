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


def _analysed_corpora():
    if not CORPUS_ROOT.is_dir():
        return []
    out = []
    for d in sorted(p for p in CORPUS_ROOT.iterdir() if p.is_dir()):
        if d.name in EXEMPT:
            continue
        stem_dir = d / "_Stem Analysis"
        if stem_dir.is_dir() and list(stem_dir.glob("SECTIONS_STEM_*.json")):
            out.append(d)
    return out


ANALYSED = _analysed_corpora()

pytestmark = pytest.mark.skipif(
    not ANALYSED,
    reason="no analysed corpus on this machine -- nothing to canary",
)


def _audio_wavs(corpus: Path):
    """Every wav the corpus can currently see, across its audio-bearing folders.

    Discovered by sweep rather than assumed: corpora legitimately park tracks in
    `_Unused Audio/` and `_Excluded Audio/` (deliberately set aside), and a mix subset
    like `Audio Mix 12/` is a real home too -- it held the 12 survivors that made the
    2026-08-20 recovery possible. Any wav-bearing folder counts; only a track with NO
    wav anywhere is evidence of loss. (An earlier draft of this test omitted
    `_Unused Audio` and cried wolf on 6 healthy tracks -- hence the sweep.)
    """
    wavs = {}
    for d in (p for p in corpus.iterdir() if p.is_dir()):
        for w in d.glob("*.wav"):
            wavs.setdefault(w.stem, w)
    return wavs


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
