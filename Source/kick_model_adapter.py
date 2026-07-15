"""Optional Kick Detector V3 adapter for stem section detection.

This module is deliberately lazy: importing it must not import torch, demucs,
or the sibling Kick Detector project. Those heavy dependencies are loaded only
when the --kick-model path is explicitly enabled.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import soundfile as sf


MODEL_FILENAME = "kick_crnn_V3.pt"
DEFAULT_THRESHOLD = 0.30
DEFAULT_FILL_OFF_BEATS = 6
DEFAULT_DROP_ON_BEATS = 1

_MODEL_MODULE = None
_PRESENCE_MODULE = None
_DEMUCS_MODEL = None
_PROVIDER_CACHE = {}


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
        _DEMUCS_MODEL = get_model("htdemucs")
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


def separate_envelopes_and_drums(
    wav_path: Path,
    cache_dir: Path,
    device: str = "auto",
    hop_sec: float = 0.1,
) -> tuple[dict[str, np.ndarray], float, np.ndarray, int]:
    """Single Demucs pass for model mode.

    Returns the same envelope dict shape as stem_section_probe._separate_envelopes,
    plus the raw mono drums stem required by Kick Detector. Only envelopes are
    cached to disk, preserving the no-stem-audio-on-disk invariant.
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

    cache_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache_dir / f"{wav_path.stem}__stemenv.npz",
                        hop_t=np.array(hop / sr), **envs)
    return envs, hop / sr, drums, sr


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

    def _drums_from_mix(self, wav_path: Path) -> tuple[np.ndarray, int]:
        import torch
        from demucs.apply import apply_model

        data, sr = sf.read(str(wav_path), always_2d=True)
        wav = data.T.astype(np.float32)
        if wav.shape[0] == 1:
            wav = np.vstack([wav, wav])
        if sr != 44100:
            import librosa
            wav = librosa.resample(wav, orig_sr=sr, target_sr=44100)
            sr = 44100

        model = _demucs_model(self.device)
        source_names = list(model.sources)
        if "drums" not in source_names:
            raise RuntimeError(f"Demucs model has no drums source: {source_names}")
        drums_idx = source_names.index("drums")

        t = torch.from_numpy(wav)
        ref = t.mean(0)
        t = (t - ref.mean()) / (ref.std() + 1e-8)
        with torch.no_grad():
            out = apply_model(model, t[None], device=self.device, progress=True)[0]
        out = out * (ref.std() + 1e-8) + ref.mean()
        drums = out[drums_idx].mean(0).cpu().numpy().astype(np.float32)
        return drums, sr

    def on_per_beat(self, wav_path: Path, bpm: float, downbeat: float, n_beats: int | None = None) -> np.ndarray:
        drums, sr = self._drums_from_mix(Path(wav_path))
        act = self._activation(drums, sr)
        duration_s = len(drums) / sr
        assert self._model_mod is not None
        raw = self._model_mod.presence_from_activation(
            act,
            duration_s,
            bpm,
            downbeat=downbeat,
            thresh=self.threshold,
        )
        on = self._presence_mod.smooth_presence(raw, self.fill_off_beats, self.drop_on_beats)
        return _fit_length(on, n_beats)

    def on_per_beat_from_drums(
        self,
        drums_mono: np.ndarray,
        sr: int,
        bpm: float,
        downbeat: float,
        n_beats: int | None = None,
    ) -> np.ndarray:
        act = self._activation(drums_mono, sr)
        duration_s = len(drums_mono) / sr
        assert self._model_mod is not None
        raw = self._model_mod.presence_from_activation(
            act,
            duration_s,
            bpm,
            downbeat=downbeat,
            thresh=self.threshold,
        )
        on = self._presence_mod.smooth_presence(raw, self.fill_off_beats, self.drop_on_beats)
        return _fit_length(on, n_beats)


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
