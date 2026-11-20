"""Re-run the stem section detector from CACHED stems only (no Demucs), for scoring.

Usage:
    PYTHONPATH=Source python Tools/section_key_replay.py \
        --project "<project with _Stem Analysis caches>" --audio "<dir with the wavs>" \
        --out "<dir for SECTIONS_STEM_*.json>" [--match "PAX" ...]         [--section-mode legacy|groove] [--bar-one-adjust]

Reads `<project>/_Stem Analysis/*__stemenv.npz` and `*__drumsstem.npz`; the kick
model (Kick Detector V3) runs on the cached drums stem. The model's activation
depends only on the drums audio and the model weights, not on bpm or downbeat
(those are applied afterwards by presence_from_activation), so it is memoised
in `<out>/_kick_activation/` under a key made of the track name, the drums
cache's source fingerprint and the model file's size and mtime.
bpm and the starting downbeat come from the cached SECTIONS_STEM json (the grid
that analysis used), unless --downbeat forces one.

Read-only on the project: detect() runs with write_json=False and make_viz=False,
and width cues (which compute-and-cache Tier A arrays) are switched on only when
the npz already carries them. A missing drums cache is an error, never a
re-separation.

--bar-one-adjust (groove mode): when analysis.bar_one_suspect gives an offset,
re-run that track with the downbeat moved by it, as an upstream grid fix would,
and record the move in analysis.replay_downbeat_adjust_beats. The detector
itself never moves bar one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

import stem_detector as sd  # noqa: E402
import kick_model_adapter as kma  # noqa: E402


def load_drums_any_version(wav_path: Path, cache_dir: Path):
    """The cached drums stem for wav_path, whatever its cache_version.

    The live loader refuses caches older than DRUMS_CACHE_VERSION and would
    re-separate. A replay must score the SAME stems the brief names, so this
    accepts any version but still requires the same Demucs model and the same
    source file (size + fingerprint), and says which version it read."""
    cache = kma._drums_cache_path(wav_path, cache_dir)
    if not cache.exists():
        raise RuntimeError(f"no drums cache for {wav_path.name}; refusing to re-separate")
    with np.load(cache, allow_pickle=False) as d:
        if str(d["demucs_model"]) != kma.DEMUCS_MODEL_NAME:
            raise RuntimeError(f"{cache.name}: demucs model {d['demucs_model']} != {kma.DEMUCS_MODEL_NAME}")
        if not kma._source_matches(d, wav_path):
            raise RuntimeError(f"{cache.name}: cached stem is not from {wav_path}")
        version = int(d["cache_version"])
        drums, sr = np.asarray(d["drums"], dtype=np.float32), int(d["sr"])
    print(f"  drums cache v{version} {wav_path.stem[:50]}")
    return drums, sr


class CachedDrumsProvider(kma.KickPresenceProvider):
    """Kick provider that only ever reads the cached drums stem and memoises the
    model activation on disk. presence_per_beat never falls back to Demucs."""

    def __init__(self, cache_dir: Path, memo_dir: Path, **kw):
        super().__init__(**kw)
        self.cache_dir = cache_dir
        self.memo_dir = memo_dir
        self._current = None

    def presence_per_beat(self, wav_path, bpm, downbeat, n_beats=None, cache_dir=None):
        wav_path = Path(wav_path)
        drums, sr = load_drums_any_version(wav_path, self.cache_dir)
        self._current = self._memo_key(wav_path)
        return self._presence_readout(drums, sr, bpm, downbeat, n_beats)

    def _memo_key(self, wav_path):
        with np.load(kma._drums_cache_path(wav_path, self.cache_dir), allow_pickle=False) as d:
            fp = str(d["src_fingerprint"]) if "src_fingerprint" in d.files else str(int(d["src_size"]))
        st = self.model_path.stat()
        ident = f"{fp}|{self.model_path.name}|{st.st_size}|{st.st_mtime_ns}"
        return f"{wav_path.stem}__{hashlib.sha1(ident.encode()).hexdigest()[:12]}"

    def _activation(self, drums_mono, sr, chunk_fr=3000, overlap=200):
        memo = self.memo_dir / f"{self._current}.npy" if self._current else None
        if memo is not None and memo.exists():
            if self._model_mod is None:   # presence_from_activation lives in the model module
                self._model_mod = kma._load_model_module(self.root)
            return np.load(memo)
        act = super()._activation(drums_mono, sr, chunk_fr, overlap)
        if memo is not None:
            self.memo_dir.mkdir(parents=True, exist_ok=True)
            np.save(memo, act)
        return act


HUB_KICK_MODEL = (Path(__file__).resolve().parents[1] / ".." / "Kick Detector" / "Models"
                  / kma.MODEL_FILENAME)
DROPBOX_HUB_KICK_MODEL = (Path.home() / "Wired Masters Dropbox" / "Sam Wills" / "0.1---GIT HUB---"
                          / "Kick Detector" / "Models" / kma.MODEL_FILENAME)


def _default_kick_model():
    for p in (HUB_KICK_MODEL, DROPBOX_HUB_KICK_MODEL):
        if p.exists():
            return p.resolve()
    return HUB_KICK_MODEL


def _has_tier_a(npz: Path) -> bool:
    with np.load(npz, allow_pickle=False) as d:
        return sd._TIERA_WIDTH_KEY in d.files and sd._TIERA_BAND_LOW_KEY in d.files


def run(project: Path, audio_dirs, out: Path, matches=None, downbeat=None, kick_model_path=None,
        section_mode="legacy", bar_one_adjust=False, exact=False):
    cache_dir = project / "_Stem Analysis"
    provider = CachedDrumsProvider(cache_dir, out / "_kick_activation",
                                   model_path=kick_model_path or _default_kick_model())
    wavs = []
    for d in audio_dirs:
        wavs += sorted(Path(d).glob("*.wav"))
    seen, done = set(), []
    out.mkdir(parents=True, exist_ok=True)
    for wav in wavs:
        hit = (wav.stem in matches) if exact else any(m in wav.stem for m in matches or [])
        if wav.stem in seen or (matches and not hit):
            continue
        cached = cache_dir / f"SECTIONS_STEM_{wav.stem}.json"
        if not (cache_dir / f"{wav.stem}__stemenv.npz").exists() or not cached.exists():
            continue
        seen.add(wav.stem)
        prior = json.loads(cached.read_text(encoding="utf-8"))
        bpm = prior["bpm"]
        db = downbeat
        if db is None:   # the downbeat the cached analysis used (its bar 0 in seconds)
            first = prior["sections"][0]
            db = float(first["start_sec"]) - float(first["start_bar"]) * 240.0 / float(bpm)
        kw = dict(bpm=bpm, make_viz=False, write_json=False, kick_provider=provider,
                  section_mode=section_mode,
                  width_cues=_has_tier_a(cache_dir / f"{wav.stem}__stemenv.npz"))
        res = sd.detect(wav, project, downbeat=db, **kw)
        if res is None:
            continue
        sus = res.get("analysis", {}).get("bar_one_suspect") or {}
        if bar_one_adjust and sus.get("suspect") and sus.get("offset_beats"):
            k = int(sus["offset_beats"])
            res = sd.detect(wav, project, downbeat=db + k * 60.0 / float(bpm), **kw)
            res["analysis"]["replay_downbeat_adjust_beats"] = k
        (out / f"SECTIONS_STEM_{wav.stem}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        done.append(wav.stem)
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--audio", type=Path, action="append", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--match", action="append", default=None)
    ap.add_argument("--downbeat", type=float, default=None,
                    help="force one downbeat for every track (default: each cached analysis's own)")
    ap.add_argument("--kick-model-path", type=Path, default=None)
    ap.add_argument("--section-mode", choices=sd.SECTION_MODES, default=sd.SECTION_MODE_LEGACY)
    ap.add_argument("--bar-one-adjust", action="store_true")
    ap.add_argument("--exact", action="store_true", help="--match names are exact wav stems")
    args = ap.parse_args(argv)
    done = run(args.project, args.audio, args.out, args.match, args.downbeat, args.kick_model_path,
               args.section_mode, args.bar_one_adjust, args.exact)
    print(f"replayed {len(done)} track(s) -> {args.out}")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
