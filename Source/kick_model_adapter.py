"""Optional Kick Detector V3 adapter for stem section detection.

This module is deliberately lazy: importing it must not import torch, demucs,
or the sibling Kick Detector project. Those heavy dependencies are loaded only
when the --kick-model path is explicitly enabled.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf


MODEL_FILENAME = "kick_crnn_V3.pt"
DEFAULT_THRESHOLD = 0.30
DEFAULT_FILL_OFF_BEATS = 6
DEFAULT_DROP_ON_BEATS = 1
DEMUCS_MODEL_NAME = "htdemucs"
DRUMS_CACHE_VERSION = 1

_MODEL_MODULE = None
_PRESENCE_MODULE = None
_DEMUCS_MODEL = None
_PROVIDER_CACHE = {}


@dataclass(frozen=True)
class KickPresenceReadout:
    raw: np.ndarray
    section: np.ndarray


def default_kick_detector_root() -> Path:
    """Sibling project location under the shared project hub."""
    return Path(__file__).resolve().parents[1].parent / "Kick Detector"


def default_model_path() -> Path:
    return default_kick_detector_root() / "Models" / MODEL_FILENAME


def _load_model_module(root: Path):
    global _MODEL_MODULE
    if _MODEL_MODULE is not None:
        return _MODEL_MODULE
    model_py = root / "Source" / "model.py"
    if not model_py.exists():
        raise FileNotFoundError(f"Kick Detector model.py not found: {model_py}")
    spec = importlib.util.spec_from_file_location("kickdet_model", model_py)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load Kick Detector model module from {model_py}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MODEL_MODULE = module
    return module


def _load_presence_module(root: Path):
    global _PRESENCE_MODULE
    if _PRESENCE_MODULE is not None:
        return _PRESENCE_MODULE
    pp_py = root / "Source" / "presence_postprocess.py"
    if not pp_py.exists():
        raise FileNotFoundError(f"Kick Detector presence_postprocess.py not found: {pp_py}")
    spec = importlib.util.spec_from_file_location("kickdet_presence_postprocess", pp_py)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load Kick Detector presence module from {pp_py}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _PRESENCE_MODULE = module
    return module


def _auto_device() -> str:
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def _demucs_model(device: str):
    global _DEMUCS_MODEL
    if _DEMUCS_MODEL is None:
        from demucs.pretrained import get_model
        _DEMUCS_MODEL = get_model(DEMUCS_MODEL_NAME)
        _DEMUCS_MODEL.to(device).eval()
    return _DEMUCS_MODEL


def _fit_length(on: np.ndarray, n_beats: int | None) -> np.ndarray:
    if n_beats is None:
        return np.asarray(on, dtype=bool)
    out = np.asarray(on, dtype=bool)
    if len(out) < n_beats:
        out = np.pad(out, (0, n_beats - len(out)), constant_values=False)
    elif len(out) > n_beats:
        out = out[:n_beats]
    return out


def _env(mono: np.ndarray, hop: int) -> np.ndarray:
    nfr = len(mono) // hop
    fr = mono[: nfr * hop].reshape(nfr, hop)
    return np.sqrt((fr.astype(np.float64) ** 2).mean(axis=1) + 1e-12)


def _drums_cache_path(wav_path: Path, cache_dir: Path) -> Path:
    return cache_dir / f"{wav_path.stem}__drumsstem.npz"


def _content_fingerprint(wav_path: Path) -> str | None:
    """Full-content sha1 - the file's actual identity, not a proxy for it.

    Exists because mtime alone made the cache useless the moment a track was
    COPIED. Building a mix subset copies tracks into `Audio Mix N/`, which
    preserves every byte and resets mtime, so each copied track re-separated
    from scratch (a courier lost ~10 minutes to exactly this on 2026-08-20).

    Measured before choosing whole-file over a cheaper edge sample: sha1 runs
    at ~1330 MB/s here, so fingerprinting all 20 masters of a corpus costs
    ~1.3 s against ~420 s of cold Demucs. An edge-sampled hash would have been
    marginally faster and carried a real blind spot - a same-size change in the
    middle of a file would fingerprint identical - so it was a false economy.
    """
    try:
        h = hashlib.sha1()
        with open(wav_path, "rb") as fh:
            while chunk := fh.read(1 << 20):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _save_drums_cache(wav_path: Path, cache_dir: Path, drums: np.ndarray, sr: int) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    st = os.stat(wav_path)
    final = _drums_cache_path(wav_path, cache_dir)
    tmp = cache_dir / f"{wav_path.stem}.tmp.npz"
    np.savez(
        tmp,
        drums=np.asarray(drums, dtype=np.float32),
        sr=np.array(int(sr)),
        src_size=np.array(int(st.st_size)),
        src_mtime_ns=np.array(int(st.st_mtime_ns)),
        src_fingerprint=np.array(_content_fingerprint(wav_path) or ""),
        demucs_model=np.array(DEMUCS_MODEL_NAME),
        cache_version=np.array(int(DRUMS_CACHE_VERSION)),
    )
    os.replace(tmp, final)


def _load_drums_cache(wav_path: Path, cache_dir: Path) -> tuple[np.ndarray, int] | None:
    cache = _drums_cache_path(wav_path, cache_dir)
    if not cache.exists():
        return None
    try:
        with np.load(cache, allow_pickle=False) as d:
            for key in (
                "drums",
                "sr",
                "src_size",
                "src_mtime_ns",
                "demucs_model",
                "cache_version",
            ):
                if key not in d.files:
                    return None
            if int(d["cache_version"]) != DRUMS_CACHE_VERSION:
                return None
            if str(d["demucs_model"]) != DEMUCS_MODEL_NAME:
                return None
            if not wav_path.exists():
                return None
            st = os.stat(wav_path)
            # Size must always match; it is the cheapest possible reject.
            if int(st.st_size) != int(d["src_size"]):
                return None
            if int(st.st_mtime_ns) != int(d["src_mtime_ns"]):
                # Same bytes, different timestamp - the shape a COPY leaves.
                # Building a mix subset copies tracks into `Audio Mix N/`, and
                # under mtime-only validation every one of them re-separated
                # from scratch. Fall back to a content fingerprint: strictly
                # STRONGER evidence than a timestamp, so this only widens the
                # hit rate, never loosens correctness. Caches written before
                # the fingerprint existed carry "" and still require mtime.
                cached_fp = (str(d["src_fingerprint"])
                             if "src_fingerprint" in d.files else "")
                if not cached_fp:
                    return None
                if _content_fingerprint(wav_path) != cached_fp:
                    return None
            drums = np.asarray(d["drums"], dtype=np.float32)
            sr = int(d["sr"])
    except Exception:
        return None
    return drums, sr


def _run_demucs_separation(
    wav_path: Path, device: str, hop_sec: float
) -> tuple[dict[str, np.ndarray], float, np.ndarray, int]:
    """Heavy Demucs separation body. The import torch / from demucs.apply
    lines stay INSIDE this function so the module's lazy-import guarantee
    holds and the warm cache path (case A) never pulls torch/demucs.
    """
    import torch
    from demucs.apply import apply_model

    dev = _auto_device() if device == "auto" else device
    data, sr = sf.read(str(wav_path), always_2d=True)
    wav = data.T.astype(np.float32)
    if wav.shape[0] == 1:
        wav = np.vstack([wav, wav])
    if sr != 44100:
        import librosa
        wav = librosa.resample(wav, orig_sr=sr, target_sr=44100)
        sr = 44100

    model = _demucs_model(dev)
    source_names = list(model.sources)
    if "drums" not in source_names:
        raise RuntimeError(f"Demucs model has no drums source: {source_names}")

    t = torch.from_numpy(wav)
    ref = t.mean(0)
    t = (t - ref.mean()) / (ref.std() + 1e-8)
    print(f"  separating {wav_path.name} ({dev.upper()}, kick model)...")
    with torch.no_grad():
        out = apply_model(model, t[None], device=dev, progress=True)[0]
    out = out * (ref.std() + 1e-8) + ref.mean()

    hop = max(1, int(sr * hop_sec))
    envs = {name: _env(out[i].mean(0).cpu().numpy(), hop) for i, name in enumerate(source_names)}
    envs["mix"] = _env(wav.mean(0), hop)
    drums = out[source_names.index("drums")].mean(0).cpu().numpy().astype(np.float32)

    return envs, hop / sr, drums, sr


def separate_envelopes_and_drums(
    wav_path: Path,
    cache_dir: Path,
    device: str = "auto",
    hop_sec: float = 0.1,
) -> tuple[dict[str, np.ndarray], float, np.ndarray, int]:
    """Single Demucs pass for model mode, with disk caches for both envelopes
    and the raw mono drums stem.

    Returns the same envelope dict shape as stem_section_probe._separate_envelopes,
    plus the raw mono drums stem required by Kick Detector. The envelope cache
    (<stem>__stemenv.npz) and a new drums-stem sidecar (<stem>__drumsstem.npz)
    live side by side in cache_dir. Re-runs with intact caches do zero Demucs
    work (Sam 2026-08-19: persist Demucs output so re-runs skip Demucs entirely).
    The sidecar stores analysis output, not mixable audio, so the
    no-stem-audio-on-disk invariant is preserved.
    """
    env_cache = cache_dir / f"{wav_path.stem}__stemenv.npz"

    # CASE A - warm: envelopes + drums stem both cached; pure read-through.
    drums_hit = _load_drums_cache(wav_path, cache_dir)
    if env_cache.exists() and drums_hit is not None:
        with np.load(env_cache, allow_pickle=False) as d:
            hop_t = float(d["hop_t"])
            envs = {
                k: d[k] for k in d.files if k != "hop_t" and not k.startswith("tiera_")
            }
        drums, sr = drums_hit
        print(f"  cache hit {wav_path.name} (envelopes + drums stem, Demucs skipped)")
        return envs, hop_t, drums, sr

    # Cases B and C: must run Demucs at least once for fresh drums.
    envs, hop_t, drums, sr = _run_demucs_separation(wav_path, device, hop_sec)

    # Always persist the drums stem when we just computed it.
    _save_drums_cache(wav_path, cache_dir, drums, sr)

    if env_cache.exists():
        # CASE B - mixed: env_cache is authoritative for envelopes (it may
        # carry tiera_ augmentation keys and must NOT be rewritten); drums
        # come from the fresh separation we just ran. Return envelopes loaded
        # from disk so callers see exactly what is persisted.
        with np.load(env_cache, allow_pickle=False) as d:
            cached_hop_t = float(d["hop_t"])
            cached_envs = {
                k: d[k] for k in d.files if k != "hop_t" and not k.startswith("tiera_")
            }
        return cached_envs, cached_hop_t, drums, sr

    # CASE C - fresh: write the envelope cache byte-identically to today's
    # layout, then return the values we just computed.
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(env_cache, hop_t=np.array(hop_t), **envs)
    return envs, hop_t, drums, sr


class KickPresenceProvider:
    """Run Kick Detector V3 on a mastered track and return beat-level presence."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        device: str = "auto",
        threshold: float = DEFAULT_THRESHOLD,
        fill_off_beats: int = DEFAULT_FILL_OFF_BEATS,
        drop_on_beats: int = DEFAULT_DROP_ON_BEATS,
    ):
        self.model_path = Path(model_path) if model_path else default_model_path()
        self.root = self.model_path.parents[1]
        self.device = _auto_device() if device == "auto" else device
        self.threshold = threshold
        self.fill_off_beats = fill_off_beats
        self.drop_on_beats = drop_on_beats
        self._model = None
        self._torch = None
        self._model_mod = None
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Kick Detector weights not found: {self.model_path}. "
                "Run without --kick-model or pass --kick-model-path."
            )
        self._presence_mod = _load_presence_module(self.root)

    def _load(self):
        if self._model is not None:
            return
        import torch
        self._torch = torch
        self._model_mod = _load_model_module(self.root)
        blob = torch.load(str(self.model_path), map_location=self.device)
        n_mels = blob.get("cfg", {}).get("n_mels", self._model_mod.N_MELS)
        self._model = self._model_mod.build_model(n_mels=n_mels)
        self._model.load_state_dict(blob["state_dict"])
        self._model.to(self.device).eval()

    def _activation(self, drums_mono: np.ndarray, sr: int, chunk_fr: int = 3000, overlap: int = 200) -> np.ndarray:
        self._load()
        assert self._model is not None
        assert self._torch is not None
        assert self._model_mod is not None

        if sr != self._model_mod.SR:
            import librosa
            drums_mono = librosa.resample(drums_mono, orig_sr=sr, target_sr=self._model_mod.SR)
            sr = self._model_mod.SR
        mel = self._model_mod.log_mel(drums_mono, sr=sr)
        frames = mel.shape[1]
        acc = np.zeros(frames, dtype=np.float32)
        cnt = np.zeros(frames, dtype=np.float32)
        step = chunk_fr - overlap
        with self._torch.no_grad():
            for start in range(0, frames, step):
                end = min(frames, start + chunk_fr)
                x = self._torch.from_numpy(mel[:, start:end]).unsqueeze(0).unsqueeze(0).to(self.device)
                logits = self._model(x)[0].cpu().numpy()
                acc[start:end] += 1.0 / (1.0 + np.exp(-logits))
                cnt[start:end] += 1.0
                if end == frames:
                    break
        return acc / np.maximum(cnt, 1e-6)

    def _drums_from_mix(
        self, wav_path: Path, cache_dir: Path | None = None
    ) -> tuple[np.ndarray, int]:
        if cache_dir is None and wav_path.parent.name == "Audio":
            cache_dir = wav_path.parent.parent / "_Stem Analysis"

        if cache_dir is not None:
            hit = _load_drums_cache(wav_path, cache_dir)
            if hit is not None:
                return hit

        envs, hop_t, drums, sr = _run_demucs_separation(wav_path, self.device, 0.1)

        if cache_dir is not None:
            try:
                _save_drums_cache(wav_path, cache_dir, drums, sr)
            except OSError as e:
                print(
                    f"  warning: drums stem cache write failed for "
                    f"{wav_path.name}: {e}"
                )
            env_cache = cache_dir / f"{wav_path.stem}__stemenv.npz"
            if not env_cache.exists():
                cache_dir.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(
                    env_cache, hop_t=np.array(hop_t), **envs
                )
        return drums, sr

    def _presence_readout(
        self,
        drums: np.ndarray,
        sr: int,
        bpm: float,
        downbeat: float,
        n_beats: int | None,
    ) -> KickPresenceReadout:
        act = self._activation(drums, sr)
        duration_s = len(drums) / sr
        assert self._model_mod is not None
        raw = self._model_mod.presence_from_activation(
            act, duration_s, bpm, downbeat=downbeat, thresh=self.threshold,
        )
        section = self._presence_mod.smooth_presence(
            raw, self.fill_off_beats, self.drop_on_beats
        )
        return KickPresenceReadout(
            raw=_fit_length(raw, n_beats),
            section=_fit_length(section, n_beats),
        )

    def presence_per_beat(
        self, wav_path: Path, bpm: float, downbeat: float,
        n_beats: int | None = None,
        cache_dir: Path | None = None,
    ) -> KickPresenceReadout:
        drums, sr = self._drums_from_mix(Path(wav_path), cache_dir=cache_dir)
        return self._presence_readout(drums, sr, bpm, downbeat, n_beats)

    def on_per_beat(self, wav_path: Path, bpm: float, downbeat: float,
                    n_beats: int | None = None,
                    cache_dir: Path | None = None) -> np.ndarray:
        return self.presence_per_beat(
            wav_path, bpm, downbeat, n_beats, cache_dir=cache_dir
        ).section

    def presence_per_beat_from_drums(
        self,
        drums_mono: np.ndarray,
        sr: int,
        bpm: float,
        downbeat: float,
        n_beats: int | None = None,
    ) -> KickPresenceReadout:
        return self._presence_readout(drums_mono, sr, bpm, downbeat, n_beats)

    def on_per_beat_from_drums(
        self,
        drums_mono: np.ndarray,
        sr: int,
        bpm: float,
        downbeat: float,
        n_beats: int | None = None,
    ) -> np.ndarray:
        return self.presence_per_beat_from_drums(
            drums_mono, sr, bpm, downbeat, n_beats
        ).section


def get_provider(
    model_path: str | Path | None = None,
    device: str = "auto",
    threshold: float = DEFAULT_THRESHOLD,
    fill_off_beats: int = DEFAULT_FILL_OFF_BEATS,
    drop_on_beats: int = DEFAULT_DROP_ON_BEATS,
) -> KickPresenceProvider:
    path = Path(model_path) if model_path else default_model_path()
    key = (str(path.resolve()), device, threshold, fill_off_beats, drop_on_beats)
    if key not in _PROVIDER_CACHE:
        _PROVIDER_CACHE[key] = KickPresenceProvider(
            model_path=path,
            device=device,
            threshold=threshold,
            fill_off_beats=fill_off_beats,
            drop_on_beats=drop_on_beats,
        )
    return _PROVIDER_CACHE[key]
