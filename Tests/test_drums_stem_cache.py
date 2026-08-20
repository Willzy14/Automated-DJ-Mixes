"""Tests for the drums-stem disk cache that backs kick-model mode.

The whole point: re-runs with intact caches must do ZERO Demucs work. This
file pins the cache helpers (`_save_drums_cache`/`_load_drums_cache`), the
three-case orchestration in `separate_envelopes_and_drums` (warm / mixed /
fresh), and the read-through in `KickPresenceProvider._drums_from_mix`.

All synthetic -- no torch, no demucs, no GPU, no real audio.
"""

import hashlib
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Source"))

import kick_model_adapter  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_wav(tmp_path: Path, name: str = "Track.wav", payload: bytes = b"placeholder") -> Path:
    p = tmp_path / name
    p.write_bytes(payload)
    return p


def _make_env_cache(tmp_path: Path, wav: Path, *, hop_t_val: float = 0.1,
                    n_frames: int = 320, with_tiera: bool = True) -> Path:
    """Write a minimal __stemenv.npz (matching stem_section_probe's layout)."""
    cache_dir = tmp_path / "_Stem Analysis"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{wav.stem}__stemenv.npz"
    np.savez_compressed(
        cache,
        hop_t=np.array(hop_t_val),
        drums=np.ones(n_frames, dtype=float),
        bass=np.zeros(n_frames, dtype=float),
        other=np.ones(n_frames, dtype=float) * 0.5,
        vocals=np.zeros(n_frames, dtype=float),
        mix=np.ones(n_frames, dtype=float),
        **({"tiera_foo": np.zeros(n_frames, dtype=float)} if with_tiera else {}),
    )
    return cache


# ---------------------------------------------------------------------------
# 1. round-trip
# ---------------------------------------------------------------------------

def test_save_load_round_trip(tmp_path):
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    drums = np.linspace(-1.0, 1.0, 8820, dtype=np.float32)
    kick_model_adapter._save_drums_cache(wav, cache_dir, drums, 44100)

    hit = kick_model_adapter._load_drums_cache(wav, cache_dir)
    assert hit is not None
    loaded, sr = hit
    assert sr == 44100
    assert loaded.dtype == np.float32
    assert np.array_equal(loaded, drums)


# ---------------------------------------------------------------------------
# 2. mtime invalidation
# ---------------------------------------------------------------------------

def test_mtime_invalidation(tmp_path):
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    kick_model_adapter._save_drums_cache(wav, cache_dir, np.zeros(100, dtype=np.float32), 44100)
    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is not None

    # bump mtime only (keep size the same)
    st = os.stat(wav)
    new_mtime = st.st_mtime_ns + 10_000_000
    os.utime(wav, ns=(st.st_atime_ns, new_mtime))

    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is None


# ---------------------------------------------------------------------------
# 3. size invalidation
# ---------------------------------------------------------------------------

def test_size_invalidation(tmp_path):
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    kick_model_adapter._save_drums_cache(wav, cache_dir, np.zeros(100, dtype=np.float32), 44100)
    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is not None

    with open(wav, "ab") as f:
        f.write(b"more")

    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is None


# ---------------------------------------------------------------------------
# 4. model-name mismatch
# ---------------------------------------------------------------------------

def test_model_name_mismatch(tmp_path, monkeypatch):
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    kick_model_adapter._save_drums_cache(wav, cache_dir, np.zeros(100, dtype=np.float32), 44100)
    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is not None

    monkeypatch.setattr(kick_model_adapter, "DEMUCS_MODEL_NAME", "htdemucs_ft")
    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is None


# ---------------------------------------------------------------------------
# 5. cache-version mismatch
# ---------------------------------------------------------------------------

def test_cache_version_mismatch(tmp_path, monkeypatch):
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    kick_model_adapter._save_drums_cache(wav, cache_dir, np.zeros(100, dtype=np.float32), 44100)
    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is not None

    monkeypatch.setattr(kick_model_adapter, "DRUMS_CACHE_VERSION", 99)
    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is None


# ---------------------------------------------------------------------------
# 6. corrupt cache
# ---------------------------------------------------------------------------

def test_corrupt_cache_returns_none_without_raising(tmp_path):
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    cache = kick_model_adapter._drums_cache_path(wav, cache_dir)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"this is not a valid npz file at all")
    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is None


# ---------------------------------------------------------------------------
# 7. missing source wav
# ---------------------------------------------------------------------------

def test_missing_source_wav_returns_none(tmp_path):
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    kick_model_adapter._save_drums_cache(wav, cache_dir, np.zeros(100, dtype=np.float32), 44100)
    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is not None

    wav.unlink()
    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is None


# ---------------------------------------------------------------------------
# Common helper for the orchestration tests: a synthetic separation stub.
# ---------------------------------------------------------------------------

class _SeparationStub:
    """Drop-in for `_run_demucs_separation` that records its call count."""

    def __init__(self, envs, hop_t, drums, sr):
        self.envs = envs
        self.hop_t = hop_t
        self.drums = drums
        self.sr = sr
        self.calls = 0

    def __call__(self, wav_path, device, hop_sec):
        self.calls += 1
        return self.envs, self.hop_t, self.drums, self.sr


def _raiser(*_args, **_kwargs):
    raise AssertionError("Demucs must not run")


# ---------------------------------------------------------------------------
# 8. warm path (case A)
# ---------------------------------------------------------------------------

def test_warm_path_skips_demucs_and_preserves_env_cache(tmp_path, monkeypatch):
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    env_cache = _make_env_cache(tmp_path, wav)

    cached_drums = np.arange(1000, dtype=np.float32) * 0.001
    kick_model_adapter._save_drums_cache(wav, cache_dir, cached_drums, 44100)

    pre_hash = _sha256(env_cache)
    sys.modules.pop("torch", None)

    monkeypatch.setattr(kick_model_adapter, "_run_demucs_separation", _raiser)

    envs, hop_t, drums, sr = kick_model_adapter.separate_envelopes_and_drums(wav, cache_dir)

    # envelopes filtered: no hop_t, no tiera_foo
    assert "hop_t" not in envs
    assert "tiera_foo" not in envs
    for stem in ("drums", "bass", "other", "vocals", "mix"):
        assert stem in envs
    assert hop_t == 0.1
    assert drums.dtype == np.float32
    assert np.array_equal(drums, cached_drums)
    assert sr == 44100

    # no torch import
    assert "torch" not in sys.modules

    # env_cache bytes unchanged
    assert _sha256(env_cache) == pre_hash


# ---------------------------------------------------------------------------
# 9. mixed path (case B): env_cache exists, drums cache missing
# ---------------------------------------------------------------------------

def test_mixed_path_writes_drums_cache_and_keeps_env_cache(tmp_path, monkeypatch):
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    env_cache = _make_env_cache(tmp_path, wav)
    pre_hash = _sha256(env_cache)

    # No drums cache exists yet.
    assert kick_model_adapter._drums_cache_path(wav, cache_dir).exists() is False

    envs2 = {
        "drums": np.arange(10, dtype=float),
        "bass": np.arange(10, dtype=float) * 2,
        "other": np.arange(10, dtype=float) * 3,
        "vocals": np.arange(10, dtype=float) * 4,
        "mix": np.arange(10, dtype=float) * 5,
    }
    drums2 = np.full(500, 0.42, dtype=np.float32)
    stub = _SeparationStub(envs2, 0.1, drums2, 44100)
    monkeypatch.setattr(kick_model_adapter, "_run_demucs_separation", stub)

    envs, hop_t, drums, sr = kick_model_adapter.separate_envelopes_and_drums(wav, cache_dir)

    assert stub.calls == 1

    # drums cache was written and round-trips
    cache_path = kick_model_adapter._drums_cache_path(wav, cache_dir)
    assert cache_path.exists()
    loaded_drums, loaded_sr = kick_model_adapter._load_drums_cache(wav, cache_dir)
    assert loaded_sr == 44100
    assert np.array_equal(loaded_drums, drums2)

    # returned envelopes are the CACHED ones, not envs2
    for stem in ("drums", "bass", "other", "vocals", "mix"):
        assert stem in envs
        assert not np.array_equal(envs[stem], envs2[stem])
    assert "tiera_foo" not in envs

    # drums/sr are the fresh ones from separation
    assert np.array_equal(drums, drums2)
    assert sr == 44100
    assert hop_t == 0.1

    # env_cache bytes untouched
    assert _sha256(env_cache) == pre_hash


# ---------------------------------------------------------------------------
# 10. fresh path (case C): both caches missing, then warm re-run
# ---------------------------------------------------------------------------

def test_fresh_path_writes_both_caches_and_warms_on_repeat(tmp_path, monkeypatch):
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"

    envs1 = {
        "drums": np.linspace(0, 1, 50, dtype=float),
        "bass": np.linspace(0, 1, 50, dtype=float) * 0.5,
        "other": np.linspace(0, 1, 50, dtype=float) * 0.25,
        "vocals": np.linspace(0, 1, 50, dtype=float) * 0.1,
        "mix": np.linspace(0, 1, 50, dtype=float),
    }
    drums1 = np.full(2000, 0.123, dtype=np.float32)
    stub = _SeparationStub(envs1, 0.1, drums1, 44100)
    monkeypatch.setattr(kick_model_adapter, "_run_demucs_separation", stub)

    # first call: writes both caches
    envs_a, hop_t_a, drums_a, sr_a = kick_model_adapter.separate_envelopes_and_drums(wav, cache_dir)
    assert stub.calls == 1

    env_cache = cache_dir / f"{wav.stem}__stemenv.npz"
    drums_cache = cache_dir / f"{wav.stem}__drumsstem.npz"
    assert env_cache.exists()
    assert drums_cache.exists()

    for stem, expected in envs1.items():
        assert np.array_equal(envs_a[stem], expected)
    assert hop_t_a == 0.1
    assert np.array_equal(drums_a, drums1)
    assert sr_a == 44100

    # second call: monkeypatch separation to RAISE; cache hit must save it.
    monkeypatch.setattr(kick_model_adapter, "_run_demucs_separation", _raiser)

    envs_b, hop_t_b, drums_b, sr_b = kick_model_adapter.separate_envelopes_and_drums(wav, cache_dir)

    for stem, expected in envs1.items():
        assert np.array_equal(envs_b[stem], expected)
    assert hop_t_b == 0.1
    assert np.array_equal(drums_b, drums1)
    assert sr_b == 44100


# ---------------------------------------------------------------------------
# 11. stale-cache fallback (the acceptance case)
# ---------------------------------------------------------------------------

def test_stale_cache_triggers_re_separation(tmp_path, monkeypatch):
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"

    # Establish a valid baseline cache.
    old_drums = np.full(500, 0.111, dtype=np.float32)
    kick_model_adapter._save_drums_cache(wav, cache_dir, old_drums, 44100)

    # Bump the wav's mtime -- cache should now be considered stale.
    st = os.stat(wav)
    os.utime(wav, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))

    fresh_envs = {
        "drums": np.ones(10, dtype=float),
        "bass": np.ones(10, dtype=float) * 0.5,
        "other": np.ones(10, dtype=float) * 0.25,
        "vocals": np.ones(10, dtype=float) * 0.1,
        "mix": np.ones(10, dtype=float),
    }
    fresh_drums = np.full(750, 0.777, dtype=np.float32)
    stub = _SeparationStub(fresh_envs, 0.1, fresh_drums, 44100)
    monkeypatch.setattr(kick_model_adapter, "_run_demucs_separation", stub)

    envs, hop_t, drums, sr = kick_model_adapter.separate_envelopes_and_drums(wav, cache_dir)

    assert stub.calls == 1
    assert np.array_equal(drums, fresh_drums)
    assert sr == 44100

    # The sidecar was rewritten with the new fingerprint; a following load hits.
    hit = kick_model_adapter._load_drums_cache(wav, cache_dir)
    assert hit is not None
    loaded_drums, loaded_sr = hit
    assert loaded_sr == 44100
    assert np.array_equal(loaded_drums, fresh_drums)


# ---------------------------------------------------------------------------
# 12. KickPresenceProvider._drums_from_mix read-through / write-through
# ---------------------------------------------------------------------------

def test_drums_from_mix_read_through_returns_cached(monkeypatch, tmp_path):
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    cached_drums = np.full(1234, 0.555, dtype=np.float32)
    kick_model_adapter._save_drums_cache(wav, cache_dir, cached_drums, 44100)

    monkeypatch.setattr(kick_model_adapter, "_run_demucs_separation", _raiser)

    provider = object.__new__(kick_model_adapter.KickPresenceProvider)
    provider.device = "cpu"
    drums, sr = provider._drums_from_mix(wav, cache_dir=cache_dir)

    assert np.array_equal(drums, cached_drums)
    assert sr == 44100


def test_drums_from_mix_writes_sidecar_and_env_cache_when_wav_is_under_audio(
    monkeypatch, tmp_path,
):
    # Project layout: tmp_path/Audio/Track.wav -> derive tmp_path/_Stem Analysis.
    project = tmp_path
    audio = project / "Audio"
    audio.mkdir()
    wav = audio / "Track.wav"
    wav.write_bytes(b"placeholder")
    cache_dir = project / "_Stem Analysis"

    fresh_drums = np.full(4321, 0.333, dtype=np.float32)
    envs = {
        "drums": np.arange(20, dtype=float),
        "bass": np.arange(20, dtype=float) * 2,
        "other": np.arange(20, dtype=float) * 3,
        "vocals": np.arange(20, dtype=float) * 4,
        "mix": np.arange(20, dtype=float) * 5,
    }
    stub = _SeparationStub(envs, 0.1, fresh_drums, 44100)
    monkeypatch.setattr(kick_model_adapter, "_run_demucs_separation", stub)

    provider = object.__new__(kick_model_adapter.KickPresenceProvider)
    provider.device = "cpu"

    drums, sr = provider._drums_from_mix(wav)  # cache_dir derived

    assert stub.calls == 1
    assert np.array_equal(drums, fresh_drums)
    assert sr == 44100

    # Sidecar exists and round-trips.
    drums_cache = cache_dir / f"{wav.stem}__drumsstem.npz"
    assert drums_cache.exists()
    loaded_drums, loaded_sr = kick_model_adapter._load_drums_cache(wav, cache_dir)
    assert loaded_sr == 44100
    assert np.array_equal(loaded_drums, fresh_drums)

    # Free envelope cache also written (no env_cache existed before).
    env_cache = cache_dir / f"{wav.stem}__stemenv.npz"
    assert env_cache.exists()


def test_drums_from_mix_does_not_overwrite_existing_env_cache(monkeypatch, tmp_path):
    project = tmp_path
    audio = project / "Audio"
    audio.mkdir()
    wav = audio / "Track.wav"
    wav.write_bytes(b"placeholder")
    cache_dir = project / "_Stem Analysis"

    # Pre-existing env_cache with a known tiera_ key and a known hop_t.
    env_cache = _make_env_cache(tmp_path, wav, hop_t_val=0.1, with_tiera=True)
    pre_hash = _sha256(env_cache)

    fresh_drums = np.full(500, 0.999, dtype=np.float32)
    envs = {
        "drums": np.zeros(5, dtype=float),
        "bass": np.zeros(5, dtype=float),
        "other": np.zeros(5, dtype=float),
        "vocals": np.zeros(5, dtype=float),
        "mix": np.zeros(5, dtype=float),
    }
    stub = _SeparationStub(envs, 0.1, fresh_drums, 44100)
    monkeypatch.setattr(kick_model_adapter, "_run_demucs_separation", stub)

    provider = object.__new__(kick_model_adapter.KickPresenceProvider)
    provider.device = "cpu"

    drums, sr = provider._drums_from_mix(wav)

    assert stub.calls == 1
    assert np.array_equal(drums, fresh_drums)
    assert sr == 44100

    # env_cache must remain byte-identical -- the free env write is skipped.
    assert _sha256(env_cache) == pre_hash

    # Drums sidecar is written.
    drums_cache = cache_dir / f"{wav.stem}__drumsstem.npz"
    assert drums_cache.exists()


def test_drums_from_mix_no_caching_when_cache_dir_unknown(monkeypatch, tmp_path):
    # wav_path whose parent is not "Audio" -> cache_dir stays None,
    # so no cache writes happen anywhere.
    wav = tmp_path / "Stray.wav"
    wav.write_bytes(b"placeholder")

    fresh_drums = np.full(100, 0.5, dtype=np.float32)
    envs = {
        "drums": np.arange(4, dtype=float),
        "bass": np.arange(4, dtype=float) * 2,
        "other": np.arange(4, dtype=float) * 3,
        "vocals": np.arange(4, dtype=float) * 4,
        "mix": np.arange(4, dtype=float) * 5,
    }
    stub = _SeparationStub(envs, 0.1, fresh_drums, 44100)
    monkeypatch.setattr(kick_model_adapter, "_run_demucs_separation", stub)

    provider = object.__new__(kick_model_adapter.KickPresenceProvider)
    provider.device = "cpu"

    drums, sr = provider._drums_from_mix(wav)

    assert stub.calls == 1
    assert np.array_equal(drums, fresh_drums)
    assert sr == 44100
    # No caches anywhere under tmp_path.
    assert list(tmp_path.rglob("*__drumsstem.npz")) == []
    assert list(tmp_path.rglob("*__stemenv.npz")) == []
