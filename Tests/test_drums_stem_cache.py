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


@pytest.fixture(autouse=True)
def _exercise_opus_path(monkeypatch):
    """The Opus path ships flag-OFF pending Sam's arbitration of the
    round-12..14 theoretical-decoder residual (see OPUS_PAYLOAD_ENABLED).
    The path and its 18 adversarial pins must stay green regardless, so the
    suite exercises it with the flag ON; the shipped default is pinned by
    its own test below."""
    monkeypatch.setattr(kick_model_adapter, "OPUS_PAYLOAD_ENABLED", True,
                        raising=False)



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

def test_mtime_alone_no_longer_invalidates_identical_content(tmp_path):
    """A changed timestamp over IDENTICAL bytes is a hit, deliberately.

    This test previously asserted the opposite. mtime was standing in for
    "has the audio changed?", and it answered wrongly in the case that costs
    real time: copying a track into a mix subset (`Audio Mix N/`) preserves
    every byte and resets mtime, so the whole corpus re-separated from
    scratch. Validation now falls back to a full content hash, which is
    STRICTLY STRONGER evidence than a timestamp - the audio genuinely has not
    changed, so the cached stem is genuinely valid.

    The property mtime was protecting is tested directly below.
    """
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    kick_model_adapter._save_drums_cache(wav, cache_dir, np.zeros(100, dtype=np.float32), 44100)
    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is not None

    # bump mtime only (keep size AND content the same)
    st = os.stat(wav)
    os.utime(wav, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))

    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is not None, \
        "identical bytes with a new timestamp must still serve the cache"


def test_changed_content_at_identical_size_still_invalidates(tmp_path):
    """The property mtime was really protecting: different audio, no stale stem.

    Same byte count, different bytes - the one case a size check cannot catch
    and where serving a cached stem would silently analyse the wrong audio.
    """
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    kick_model_adapter._save_drums_cache(wav, cache_dir, np.zeros(100, dtype=np.float32), 44100)
    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is not None

    original = wav.read_bytes()
    mutated = bytearray(original)
    mutated[len(mutated) // 2] ^= 0xFF          # flip one byte, mid-file
    wav.write_bytes(bytes(mutated))
    assert wav.stat().st_size == len(original), "fixture must keep size constant"

    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is None, \
        "different audio must never be served from cache"


def test_a_copy_of_a_track_hits_the_cache(tmp_path):
    """The 10 minutes lost on 2026-08-20, as a regression test.

    A mix subset is built by COPYING tracks into `Audio Mix N/`. The copy is
    byte-identical and carries a fresh mtime, and under the old rule every one
    re-separated. It must hit.
    """
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    drums = np.linspace(-1.0, 1.0, 4410, dtype=np.float32)
    kick_model_adapter._save_drums_cache(wav, cache_dir, drums, 44100)

    subset = tmp_path / "Audio Mix 12"
    subset.mkdir()
    copied = subset / wav.name
    copied.write_bytes(wav.read_bytes())
    os.utime(copied, ns=(os.stat(wav).st_atime_ns,
                         os.stat(wav).st_mtime_ns + 5_000_000_000))

    hit = kick_model_adapter._load_drums_cache(copied, cache_dir)
    assert hit is not None, "a byte-identical copy must reuse the cached stem"
    loaded, sr = hit
    assert sr == 44100
    assert np.array_equal(loaded, drums)


def test_legacy_cache_without_a_fingerprint_still_requires_mtime(tmp_path):
    """Backward compatibility: caches written before the fingerprint existed
    carry no hash, so they must keep the old, stricter rule rather than being
    trusted on size alone."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    kick_model_adapter._save_drums_cache(wav, cache_dir, np.zeros(100, dtype=np.float32), 44100)

    # Rewrite the sidecar without the fingerprint key, as an old build would.
    path = kick_model_adapter._drums_cache_path(wav, cache_dir)
    with np.load(path, allow_pickle=False) as d:
        payload = {k: d[k] for k in d.files if k != "src_fingerprint"}
    np.savez(path, **payload)

    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is not None
    st = os.stat(wav)
    os.utime(wav, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is None, \
        "a legacy cache has no content evidence, so mtime must still gate it"


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
    """Drop-in for `_run_demucs_separation` that records its call count.

    Returns the 5-tuple (envs, hop_t, drums, bass, sr). Bass defaults to a
    small synthetic signal so every orchestration test also exercises the
    opportunistic bass save; pass bass=None to model a bass-less Demucs
    variant (the save must simply be skipped, never crash).
    """

    def __init__(self, envs, hop_t, drums, sr, bass="default"):
        self.envs = envs
        self.hop_t = hop_t
        self.drums = drums
        self.sr = sr
        # A 60 Hz sine rather than a DC constant: the bass sidecar may encode
        # via Opus, which does not preserve DC - a sine survives either payload.
        t = np.arange(2205, dtype=np.float32) / 44100.0
        self.bass = ((0.05 * np.sin(2 * np.pi * 60.0 * t)).astype(np.float32)
                     if isinstance(bass, str) and bass == "default" else bass)
        self.calls = 0

    def __call__(self, wav_path, device, hop_sec):
        self.calls += 1
        return self.envs, self.hop_t, self.drums, self.bass, self.sr


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


# ---------------------------------------------------------------------------
# Bass-stem sidecar (int16, Sam's keep-the-bass decision 2026-08-27)
# ---------------------------------------------------------------------------

def test_bass_cache_int16_fallback_round_trip(tmp_path, monkeypatch):
    """The codec-free fallback must reconstruct within one quantization step.

    Forced explicitly (opus unavailable): the fallback is what runs on a
    machine without libsndfile-opus and whenever the opus verify-gate
    refuses, so it gets its own tight pin - a scale bug cannot hide behind
    'the consumers are coarse'.
    """
    monkeypatch.setattr(kick_model_adapter, "_opus_available", lambda: False)
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    rng = np.random.default_rng(7)
    bass = (rng.standard_normal(44100) * 0.3).astype(np.float32)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, 44100)

    hit = kick_model_adapter._load_bass_cache(wav, cache_dir)
    assert hit is not None
    loaded, sr = hit
    assert sr == 44100
    assert loaded.dtype == np.float32
    step = float(np.abs(bass).max()) / 32767.0
    assert np.max(np.abs(loaded - bass)) <= step * 1.01
    assert kick_model_adapter._bass_cache_path(wav, cache_dir).stat().st_size \
        < bass.nbytes


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_bass_cache_opus_round_trip(tmp_path):
    """Sam's chosen payload (2026-08-27: 'Opus encoded bytes inside the npz').

    Opus is LOSSY, and the saver measures any codec delay per file and the
    loader strips it (measured 0 on real audio with libsndfile 1.2.2, but
    never assumed - see _encode_bass_opus). This pins the contract: same
    length, same sr, high alignment-free correlation, sane energy. A wrong
    or unstripped delay collapses the correlation.

    The fixture carries a dash of deterministic noise on top of the tones:
    a PURELY periodic signal lets the saver's lag correlation alias to a
    whole number of periods (the exact artefact that produced a phantom
    "83 ms codec delay" during development), which this test must not
    reproduce by construction.
    """
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    t = np.arange(sr * 5, dtype=np.float32) / sr
    rng = np.random.default_rng(11)
    bass = (0.3 * np.sin(2 * np.pi * 60.0 * t)
            + 0.1 * np.sin(2 * np.pi * 120.0 * t)
            + 0.01 * rng.standard_normal(len(t))).astype(np.float32)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)

    with np.load(kick_model_adapter._bass_cache_path(wav, cache_dir),
                 allow_pickle=False) as d:
        assert str(d["payload_format"]) == "opus", \
            "a clean bass-band signal must take the opus path"
        assert float(d["verify_corr"]) >= 0.98

    hit = kick_model_adapter._load_bass_cache(wav, cache_dir)
    assert hit is not None
    loaded, lsr = hit
    assert lsr == sr
    assert len(loaded) == len(bass), "loader must restore the exact length"
    c = float(np.corrcoef(bass, loaded)[0, 1])
    assert c > 0.97, f"delay not stripped or codec mangled the signal (corr {c:.3f})"
    rms_ratio = float(np.sqrt((loaded ** 2).mean()) / np.sqrt((bass ** 2).mean()))
    assert 0.8 < rms_ratio < 1.2, rms_ratio
    # The point of the exercise: codec-small, not int16-small.
    assert kick_model_adapter._bass_cache_path(wav, cache_dir).stat().st_size \
        < bass.nbytes / 8


def test_bass_cache_legacy_pre_opus_sidecar_still_loads(tmp_path):
    """A sidecar written before the payload_format field existed (the one
    real file already in the 14.08.26 corpus) must keep loading."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    cache_dir.mkdir()
    bass = np.linspace(-0.4, 0.4, 4410, dtype=np.float32)
    scale = float(np.abs(bass).max())
    q = np.round(np.clip(bass / scale, -1, 1) * 32767.0).astype(np.int16)
    st = os.stat(wav)
    np.savez_compressed(
        kick_model_adapter._bass_cache_path(wav, cache_dir),
        bass_i16=q, scale=np.array(scale, dtype=np.float64),
        sr=np.array(44100),
        src_size=np.array(int(st.st_size)),
        src_mtime_ns=np.array(int(st.st_mtime_ns)),
        src_fingerprint=np.array(""),
        demucs_model=np.array(kick_model_adapter.DEMUCS_MODEL_NAME),
        cache_version=np.array(int(kick_model_adapter.BASS_CACHE_VERSION)),
    )
    hit = kick_model_adapter._load_bass_cache(wav, cache_dir)
    assert hit is not None
    loaded, sr = hit
    assert sr == 44100
    assert np.max(np.abs(loaded - bass)) <= scale / 32767.0 * 1.01


def test_bass_save_refuses_degenerate_stems_loudly(tmp_path):
    """Empty and non-finite stems raise ValueError INSIDE the saver - callers
    catch Exception, so the ride-along can never abort a drums run (Codex
    2026-08-27: np.abs([]).max() previously escaped the OSError-only catch)."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    with pytest.raises(ValueError):
        kick_model_adapter._save_bass_cache(
            wav, cache_dir, np.array([], dtype=np.float32), 44100)
    bad = np.ones(100, dtype=np.float32)
    bad[3] = np.nan
    with pytest.raises(ValueError):
        kick_model_adapter._save_bass_cache(wav, cache_dir, bad, 44100)
    assert not kick_model_adapter._bass_cache_path(wav, cache_dir).exists()


def test_bass_save_failure_never_aborts_the_drums_run(tmp_path, monkeypatch):
    """The containment itself, end to end: a bass saver that raises a
    NON-OSError must leave separate_envelopes_and_drums returning normally
    with the drums sidecar written."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    envs = {k: np.ones(10, dtype=float) for k in
            ("drums", "bass", "other", "vocals", "mix")}
    stub = _SeparationStub(envs, 0.1, np.full(500, 0.3, dtype=np.float32), 44100)
    monkeypatch.setattr(kick_model_adapter, "_run_demucs_separation", stub)

    def boom(*_a, **_k):
        raise RuntimeError("simulated codec failure")
    monkeypatch.setattr(kick_model_adapter, "_save_bass_cache", boom)

    out_envs, hop_t, drums, sr = kick_model_adapter.separate_envelopes_and_drums(
        wav, cache_dir)
    assert sr == 44100 and len(drums) == 500
    assert kick_model_adapter._load_drums_cache(wav, cache_dir) is not None
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is None


def test_bass_cache_copy_hits_via_fingerprint(tmp_path):
    """A byte-identical copy with fresh mtime must hit, same as drums."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    bass = np.linspace(-0.5, 0.5, 4410, dtype=np.float32)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, 44100)

    subset = tmp_path / "Audio Mix 12"
    subset.mkdir()
    copied = subset / wav.name
    copied.write_bytes(wav.read_bytes())
    os.utime(copied, ns=(os.stat(wav).st_atime_ns,
                         os.stat(wav).st_mtime_ns + 5_000_000_000))
    assert kick_model_adapter._load_bass_cache(copied, cache_dir) is not None


def test_bass_cache_rejects_changed_content(tmp_path):
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    kick_model_adapter._save_bass_cache(
        wav, cache_dir, np.zeros(100, dtype=np.float32), 44100)
    original = wav.read_bytes()
    mutated = bytearray(original)
    mutated[len(mutated) // 2] ^= 0xFF
    wav.write_bytes(bytes(mutated))
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is None


def test_bass_save_never_touches_the_drums_sidecar(tmp_path):
    """Adding bass must not invalidate the 20 warm drums caches that exist.

    The rollout is opportunistic-backfill by design: warm corpora keep their
    drums sidecars byte-identical and simply gain a bass file whenever a
    separation next runs. A drums invalidation here would silently cost
    ~21 s x 20 tracks of re-separation on the next build.
    """
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    drums = np.linspace(-1.0, 1.0, 8820, dtype=np.float32)
    kick_model_adapter._save_drums_cache(wav, cache_dir, drums, 44100)
    drums_path = kick_model_adapter._drums_cache_path(wav, cache_dir)
    before = drums_path.read_bytes()

    kick_model_adapter._save_bass_cache(
        wav, cache_dir, np.ones(4410, dtype=np.float32) * 0.2, 44100)

    assert drums_path.read_bytes() == before, \
        "saving the bass sidecar must leave the drums sidecar byte-identical"
    dhit = kick_model_adapter._load_drums_cache(wav, cache_dir)
    assert dhit is not None and np.array_equal(dhit[0], drums)


def test_load_bass_stem_public_reader(tmp_path):
    """The public reader auto-derives the sidecar dir for both Audio homes and
    returns None (never raises) on every miss shape."""
    corpus = tmp_path / "corpus"
    audio = corpus / "Audio"
    audio.mkdir(parents=True)
    (corpus / "_Stem Analysis").mkdir()
    wav = audio / "Artist - Track.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 5000)

    assert kick_model_adapter.load_bass_stem(wav) is None  # no sidecar yet

    bass = np.full(2205, 0.1, dtype=np.float32)
    kick_model_adapter._save_bass_cache(
        wav, corpus / "_Stem Analysis", bass, 44100)
    hit = kick_model_adapter.load_bass_stem(wav)
    assert hit is not None and hit[1] == 44100

    # A copy in a mix subset resolves the same sidecar dir and hits.
    subset = corpus / "Audio Mix 12"
    subset.mkdir()
    copied = subset / wav.name
    copied.write_bytes(wav.read_bytes())
    assert kick_model_adapter.load_bass_stem(copied) is not None

    # A wav with no corpus-shaped parent -> None, not an exception.
    stray = tmp_path / "stray.wav"
    stray.write_bytes(b"RIFF" + b"\x00" * 100)
    assert kick_model_adapter.load_bass_stem(stray) is None


def test_fresh_separation_saves_bass_alongside_drums(tmp_path, monkeypatch):
    """Case C now leaves BOTH sidecars behind; a bass-less model skips
    the bass save without failing the drums flow."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    envs = {
        "drums": np.ones(10, dtype=float),
        "bass": np.zeros(10, dtype=float),
        "other": np.ones(10, dtype=float),
        "vocals": np.zeros(10, dtype=float),
        "mix": np.ones(10, dtype=float),
    }
    stub = _SeparationStub(envs, 0.1, np.full(500, 0.3, dtype=np.float32), 44100)
    monkeypatch.setattr(kick_model_adapter, "_run_demucs_separation", stub)
    kick_model_adapter.separate_envelopes_and_drums(wav, cache_dir)
    hit = kick_model_adapter._load_bass_cache(wav, cache_dir)
    assert hit is not None
    # Existence alone would not catch wrong content or shape (Codex
    # 2026-08-27); pin length, sr and energy against what the stub emitted.
    loaded, bsr = hit
    assert bsr == 44100 and len(loaded) == len(stub.bass)
    rms_ratio = (float(np.sqrt((loaded ** 2).mean()))
                 / float(np.sqrt((stub.bass ** 2).mean())))
    assert 0.8 < rms_ratio < 1.2, rms_ratio

    # bass-less separation: no bass sidecar, no crash, drums still cached
    wav2 = _make_wav(tmp_path, name="Other.wav", payload=b"different")
    stub2 = _SeparationStub(envs, 0.1, np.full(500, 0.3, dtype=np.float32),
                            44100, bass=None)
    monkeypatch.setattr(kick_model_adapter, "_run_demucs_separation", stub2)
    kick_model_adapter.separate_envelopes_and_drums(wav2, cache_dir)
    assert kick_model_adapter._load_bass_cache(wav2, cache_dir) is None
    assert kick_model_adapter._load_drums_cache(wav2, cache_dir) is not None


def test_provider_path_saves_bass_sidecar(tmp_path, monkeypatch):
    """The KickPresenceProvider separation path saves bass too (it is a
    different code block from the module-level function - MiniMax noted it
    had no direct coverage)."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    envs = {k: np.ones(10, dtype=float) for k in
            ("drums", "bass", "other", "vocals", "mix")}
    stub = _SeparationStub(envs, 0.1, np.full(500, 0.3, dtype=np.float32), 44100)
    monkeypatch.setattr(kick_model_adapter, "_run_demucs_separation", stub)

    provider = object.__new__(kick_model_adapter.KickPresenceProvider)
    provider.device = "cpu"
    provider._drums_from_mix(wav, cache_dir=cache_dir)
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is not None


# ---------------------------------------------------------------------------
# refit_grid_from_stem.load_or_separate_stems - the cache-first consumer
# ---------------------------------------------------------------------------

def _sine(n, hz, sr=44100, amp=0.3):
    t = np.arange(n, dtype=np.float32) / sr
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def test_refit_backfills_then_reuses_the_caches(tmp_path, monkeypatch):
    """Codex's blocker as a regression test: the first repair of a legacy
    track separates ONCE and persists BOTH sidecars; the second repair runs
    no separation at all. The first version separated and threw the bass
    away, so every repair re-ran Demucs forever."""
    import refit_grid_from_stem as refit

    corpus = tmp_path / "corpus"
    audio = corpus / "Audio"
    audio.mkdir(parents=True)
    (corpus / "_Stem Analysis").mkdir()
    wav = audio / "Artist - Track.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 9000)

    calls = {"n": 0}

    def fake_sep(_wav):
        calls["n"] += 1
        return _sine(44100, 55.0), _sine(44100, 60.0), 44100

    monkeypatch.setattr(refit, "stem_audio", fake_sep)

    drums1, bass1, sr1 = refit.load_or_separate_stems(wav, corpus)
    assert calls["n"] == 1
    assert kick_model_adapter._load_drums_cache(
        wav, corpus / "_Stem Analysis") is not None, "drums must be backfilled"
    assert kick_model_adapter._load_bass_cache(
        wav, corpus / "_Stem Analysis") is not None, "bass must be backfilled"

    drums2, bass2, sr2 = refit.load_or_separate_stems(wav, corpus)
    assert calls["n"] == 1, "second repair must be served from the sidecars"
    assert sr2 == sr1 and len(drums2) == len(drums1) and len(bass2) == len(bass1)
    assert np.array_equal(drums2, drums1), "drums sidecar is lossless"

    # A warm, already-valid drums sidecar is never rewritten by the backfill.
    dpath = kick_model_adapter._drums_cache_path(wav, corpus / "_Stem Analysis")
    before = dpath.read_bytes()
    kick_model_adapter._bass_cache_path(wav, corpus / "_Stem Analysis").unlink()
    refit.load_or_separate_stems(wav, corpus)          # re-separates for bass
    assert calls["n"] == 2
    assert dpath.read_bytes() == before, \
        "backfill must not rewrite a valid drums sidecar"


def test_refit_wrong_project_falls_back_and_creates_nothing(tmp_path, monkeypatch):
    """A non-corpus `project` must neither break the repair nor sprout a
    `_Stem Analysis` folder somewhere arbitrary."""
    import refit_grid_from_stem as refit

    wav = tmp_path / "loose.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 500)
    not_a_corpus = tmp_path / "random dir"
    not_a_corpus.mkdir()

    monkeypatch.setattr(refit, "stem_audio",
                        lambda _w: (_sine(4410, 55.0), _sine(4410, 60.0), 44100))
    drums, bass, sr = refit.load_or_separate_stems(wav, not_a_corpus)
    assert sr == 44100 and len(drums) == 4410
    assert not (not_a_corpus / "_Stem Analysis").exists(), \
        "backfill must not create cache dirs outside a real corpus"


def _gate_onsets(x: np.ndarray, frame: int = 256, step: int = 16,
                 quiet_frames: int = 25) -> list[int]:
    """Sample indices where a gated signal switches on after a silent run.

    Works on a moving-RMS envelope rather than raw samples, for two reasons
    learned the hard way: raw thresholds are PHASE-sensitive at a hard gate
    edge (whether the first audible sample clears the threshold depends on
    where the sine happens to be), and Opus leaves pre-echo in the silent
    gaps that defeats any tight per-sample hysteresis. Averaging over a few
    ms removes both.
    """
    ms = np.convolve(x.astype(np.float64) ** 2,
                     np.ones(frame) / frame, mode="same")
    hot = ms > 0.02          # rms ~0.14 against the fixtures' 0.21 rms
    cold = ms < 0.002        # rms ~0.045, far above codec pre-echo energy
    onsets = []
    # Schmitt trigger: a quiet run ARMS the detector, and the arm persists
    # through the envelope's ramp between the two thresholds. (Requiring the
    # quiet run to sit IMMEDIATELY before the hot sample fails structurally:
    # the moving average always ramps through the dead band on its way up,
    # resetting a naive adjacency counter every step.)
    run, armed = quiet_frames, True
    for i in range(0, len(x), step):
        if hot[i]:
            if armed:
                onsets.append(i)
            armed = False
            run = 0
        elif cold[i]:
            run += 1
            if run >= quiet_frames:
                armed = True
    return onsets


def _codex_gated_bass(sr: int = 44100, seconds: int = 10) -> np.ndarray:
    """Codex's round-3 counterexample: a 153.846 Hz bass - period exactly the
    312-sample genuine Opus delay at 48 kHz - gated 1 s on / 1 s off."""
    t = np.arange(sr * seconds, dtype=np.float32) / sr
    x = 0.3 * np.sin(2 * np.pi * (48000.0 / 312.0) * t)
    gate = (np.floor(t) % 2 == 0).astype(np.float32)
    return (x * gate).astype(np.float32)


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_gated_periodic_bass_keeps_exact_onset_timing(tmp_path):
    """Codex's own kill input, as the pin.

    Every lag-INFERENCE design died on this signal (round 2: period alias;
    round 3: a genuine delay equal to the period loses to the null and every
    note edge lands 6.5 ms late while verification still passes). The pilot
    design does not infer at all, so the gate ONSETS - the thing the grid
    repairer actually votes with - must come back exactly where they were.
    """
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)

    with np.load(kick_model_adapter._bass_cache_path(wav, cache_dir),
                 allow_pickle=False) as d:
        assert str(d["payload_format"]) == "opus"
        assert int(d["data_start_48"]) >= kick_model_adapter._OPUS_PILOT_LEN

    loaded, _ = kick_model_adapter._load_bass_cache(wav, cache_dir)
    assert len(loaded) == len(bass)
    on_in = _gate_onsets(bass)
    on_out = _gate_onsets(loaded)
    # ON-blocks at seconds 0/2/4/6/8 - five onsets, t=0 included (the
    # detector starts armed). An earlier revision asserted four, which was
    # this test rationalising its own then-broken detector.
    assert len(on_in) == 5
    assert len(on_out) == len(on_in), (on_in, on_out)
    worst = max(abs(a - b) for a, b in zip(on_in, on_out))
    assert worst <= sr // 500, \
        f"note edges shifted by {worst} samples ({worst/sr*1000:.2f} ms)"
    # Two-sided tail check (round-3 minor: the old assert only bounded below).
    tail = sr // 10
    r_in = float(np.sqrt((bass[-tail:] ** 2).mean()))
    r_out = float(np.sqrt((loaded[-tail:] ** 2).mean()))
    assert 0.5 * r_in <= r_out <= 2.0 * r_in, (r_in, r_out)


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_genuine_decoder_delay_is_absorbed_by_the_pilot(tmp_path, monkeypatch):
    """The case round 3 said was 'neither constructed nor meaningfully
    pinned': a REAL decoder delay. Injected here by wrapping sf.read to
    prepend 312 samples of silence to every decode - save-side verification
    and load both see the delayed stream, the pilot locator absorbs it, and
    the onsets still land exactly.
    """
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr)

    real_read = kick_model_adapter.sf.read

    def delayed_read(*args, **kwargs):
        y, rsr = real_read(*args, **kwargs)
        return np.concatenate([np.zeros(312, dtype=y.dtype), y]), rsr

    monkeypatch.setattr(kick_model_adapter.sf, "read", delayed_read)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)
    with np.load(kick_model_adapter._bass_cache_path(wav, cache_dir),
                 allow_pickle=False) as d:
        assert str(d["payload_format"]) == "opus", \
            "a plain delay must not force the fallback"
        assert int(d["data_start_48"]) >= \
            kick_model_adapter._OPUS_PILOT_LEN + 312 - 4, \
            "the located data start must include the injected delay"
    loaded, _ = kick_model_adapter._load_bass_cache(wav, cache_dir)

    assert len(loaded) == len(bass)
    on_in, on_out = _gate_onsets(bass), _gate_onsets(loaded)
    assert len(on_out) == len(on_in)
    worst = max(abs(a - b) for a, b in zip(on_in, on_out))
    assert worst <= sr // 500, \
        f"genuine delay leaked into the audio: {worst/sr*1000:.2f} ms"


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_legacy_lead_pad_sidecar_still_loads(tmp_path):
    """The pre-pilot opus sidecar shape (one real file exists in 14.08.26,
    written with measured lag 0 and NO pilot in its payload) must keep
    loading through the lead_pad branch."""
    import io
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    cache_dir.mkdir()
    sr = 44100
    t = np.arange(sr * 2, dtype=np.float32) / sr
    rng = np.random.default_rng(5)
    bass = (0.3 * np.sin(2 * np.pi * 60.0 * t)
            + 0.01 * rng.standard_normal(len(t))).astype(np.float32)
    x48 = kick_model_adapter._resample(bass, sr, 48000)
    buf = io.BytesIO()
    kick_model_adapter.sf.write(buf, x48, 48000, format="OGG", subtype="OPUS")
    st = os.stat(wav)
    np.savez_compressed(
        kick_model_adapter._bass_cache_path(wav, cache_dir),
        payload=np.frombuffer(buf.getvalue(), dtype=np.uint8),
        payload_format=np.array("opus"),
        sr_encoded=np.array(48000), lead_pad=np.array(0),
        n_samples=np.array(len(bass)), verify_corr=np.array(0.999),
        sr=np.array(sr),
        src_size=np.array(int(st.st_size)),
        src_mtime_ns=np.array(int(st.st_mtime_ns)),
        src_fingerprint=np.array(""),
        demucs_model=np.array(kick_model_adapter.DEMUCS_MODEL_NAME),
        cache_version=np.array(int(kick_model_adapter.BASS_CACHE_VERSION)),
    )
    hit = kick_model_adapter._load_bass_cache(wav, cache_dir)
    assert hit is not None
    loaded, lsr = hit
    assert lsr == sr and len(loaded) == len(bass)
    assert float(np.corrcoef(bass, loaded)[0, 1]) > 0.97


def test_opus_silent_and_quiet_head_stems_fail_closed(tmp_path):
    """Codex round-2 blocker: zero probe energy used to score achieved=1.0
    and pass silence straight through the opus gate. Silence now takes the
    int16 fallback (fail closed), and a stem whose HEAD is silent verifies
    against its most energetic window instead of the quiet start."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100

    # Fully silent stem -> int16 payload, round-trips to silence.
    kick_model_adapter._save_bass_cache(
        wav, cache_dir, np.zeros(sr, dtype=np.float32), sr)
    with np.load(kick_model_adapter._bass_cache_path(wav, cache_dir),
                 allow_pickle=False) as d:
        assert str(d["payload_format"]) == "int16"
    loaded, _ = kick_model_adapter._load_bass_cache(wav, cache_dir)
    assert float(np.abs(loaded).max()) < 1e-6

    if not kick_model_adapter._opus_available():
        return
    # 12 s silent head then a real bassline: the probe must find the energy.
    wav2 = _make_wav(tmp_path, name="LateBass.wav", payload=b"late")
    t = np.arange(sr * 8, dtype=np.float32) / sr
    rng = np.random.default_rng(3)
    body = (0.3 * np.sin(2 * np.pi * 60.0 * t)
            + 0.01 * rng.standard_normal(len(t))).astype(np.float32)
    stem = np.concatenate([np.zeros(sr * 12, dtype=np.float32), body])
    kick_model_adapter._save_bass_cache(wav2, cache_dir, stem, sr)
    with np.load(kick_model_adapter._bass_cache_path(wav2, cache_dir),
                 allow_pickle=False) as d:
        assert str(d["payload_format"]) == "opus"
        assert float(d["verify_corr"]) >= 0.98, \
            "verification must have measured the audible window, not the silence"
    loaded2, _ = kick_model_adapter._load_bass_cache(wav2, cache_dir)
    assert len(loaded2) == len(stem)
    c = float(np.corrcoef(stem[sr * 12:], loaded2[sr * 12:])[0, 1])
    assert c > 0.97, c


def test_opus_encode_crash_falls_back_to_int16(tmp_path, monkeypatch):
    """Codex round-2 warning: a codec exception used to escape, so no sidecar
    was written at all. It must land in the int16 fallback instead."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"

    def boom(*_a, **_k):
        raise RuntimeError("simulated libsndfile crash")
    monkeypatch.setattr(kick_model_adapter, "_encode_bass_opus", boom)

    t = np.arange(4410, dtype=np.float32) / 44100.0
    bass = (0.2 * np.sin(2 * np.pi * 60.0 * t)).astype(np.float32)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, 44100)
    with np.load(kick_model_adapter._bass_cache_path(wav, cache_dir),
                 allow_pickle=False) as d:
        assert str(d["payload_format"]) == "int16"
    loaded, _ = kick_model_adapter._load_bass_cache(wav, cache_dir)
    step = float(np.abs(bass).max()) / 32767.0
    assert np.max(np.abs(loaded - bass)) <= step * 1.01


def test_refit_bass_backfill_failure_does_not_block_drums_backfill(tmp_path, monkeypatch):
    """Codex round-2 warning: one shared try meant a bass-save exception also
    prevented the missing DRUMS sidecar from being written."""
    import refit_grid_from_stem as refit

    corpus = tmp_path / "corpus"
    (corpus / "Audio").mkdir(parents=True)
    (corpus / "_Stem Analysis").mkdir()
    wav = corpus / "Audio" / "Track.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 2000)

    monkeypatch.setattr(refit, "stem_audio",
                        lambda _w: (_sine(4410, 55.0), _sine(4410, 60.0), 44100))

    def boom(*_a, **_k):
        raise RuntimeError("bass saver down")
    monkeypatch.setattr(kick_model_adapter, "_save_bass_cache", boom)

    drums, bass, sr = refit.load_or_separate_stems(wav, corpus)
    assert sr == 44100
    assert kick_model_adapter._load_drums_cache(
        wav, corpus / "_Stem Analysis") is not None, \
        "drums backfill must survive a bass-save failure"
    assert kick_model_adapter._load_bass_cache(
        wav, corpus / "_Stem Analysis") is None


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_load_time_decoder_delay_is_absorbed(tmp_path, monkeypatch):
    """Codex round-4 blocker, direction 1: the LOAD decoder differs from the
    SAVE decoder (other machine, other libsndfile). The earlier pin patched
    sf.read around both save and load, so it only ever proved
    save_delay == load_delay. Here the save runs clean and only the load is
    delayed - the loader must re-locate the pilot instead of trusting the
    stored save-time position."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)   # clean save

    real_read = kick_model_adapter.sf.read

    def delayed_read(*args, **kwargs):
        y, rsr = real_read(*args, **kwargs)
        return np.concatenate([np.zeros(312, dtype=y.dtype), y]), rsr

    monkeypatch.setattr(kick_model_adapter.sf, "read", delayed_read)
    loaded, _ = kick_model_adapter._load_bass_cache(wav, cache_dir)
    assert len(loaded) == len(bass)
    on_in, on_out = _gate_onsets(bass), _gate_onsets(loaded)
    assert len(on_out) == len(on_in)
    worst = max(abs(a - b) for a, b in zip(on_in, on_out))
    assert worst <= sr // 500, \
        f"load-side decoder delay leaked into the audio: {worst/sr*1000:.2f} ms"


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_save_time_delay_with_clean_load(tmp_path, monkeypatch):
    """Codex round-4 blocker, direction 2: the SAVE decoder was delayed (its
    stored diagnostic position includes the delay) but the LOAD decoder is
    clean. Blind reuse of the stored position would now trim 312 samples of
    real audio; re-locating must not."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr)

    real_read = kick_model_adapter.sf.read

    def delayed_read(*args, **kwargs):
        y, rsr = real_read(*args, **kwargs)
        return np.concatenate([np.zeros(312, dtype=y.dtype), y]), rsr

    monkeypatch.setattr(kick_model_adapter.sf, "read", delayed_read)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)
    monkeypatch.setattr(kick_model_adapter.sf, "read", real_read)   # clean load

    with np.load(kick_model_adapter._bass_cache_path(wav, cache_dir),
                 allow_pickle=False) as d:
        assert str(d["payload_format"]) == "opus"
        stored = int(d["data_start_48"])
    assert stored >= kick_model_adapter._OPUS_PILOT_LEN + 300, \
        "fixture must actually store a delayed save-time position"

    loaded, _ = kick_model_adapter._load_bass_cache(wav, cache_dir)
    assert len(loaded) == len(bass)
    on_in, on_out = _gate_onsets(bass), _gate_onsets(loaded)
    assert len(on_out) == len(on_in)
    worst = max(abs(a - b) for a, b in zip(on_in, on_out))
    assert worst <= sr // 500, \
        f"stale stored data_start trimmed real audio: {worst/sr*1000:.2f} ms"


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_louder_fake_pilot_cannot_capture_alignment(tmp_path):
    """Codex rounds 4-5, its own construction at its own strengthened scale:
    the bass BEGINS with a 1.5x-louder copy of the pilot chirp, followed by
    a long periodic body. Selection history on this input: argmax aligned to
    the fake (round 4); a runner-up ambiguity ratio had its polarity
    backwards - the STRONGER impostor measured 0.6697, sliding under the
    0.7 gate, mis-aligning by 80 ms while content verification still passed
    0.9876 (round 5, measured by Codex). Earliest-above-absolute-threshold
    cannot be captured: the impostor can be louder but never earlier.

    The pin is timing, not payload: whatever payload is chosen, every gate
    onset must land within 2 ms. An 80 ms capture moves all of them.
    """
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    chirp48 = kick_model_adapter._opus_pilot_chirp()
    fake = kick_model_adapter._resample(chirp48 * 1.5, 48000, sr).astype(np.float32)
    body = _codex_gated_bass(sr, seconds=10)
    bass = np.concatenate(
        [fake, np.zeros(sr // 50, dtype=np.float32), body])

    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)
    with np.load(kick_model_adapter._bass_cache_path(wav, cache_dir),
                 allow_pickle=False) as d:
        fmt = str(d["payload_format"])
        if fmt == "opus":
            nominal = kick_model_adapter._OPUS_PILOT_LEN
            assert abs(int(d["data_start_48"]) - nominal) < 200, \
                "alignment captured by the impostor chirp"

    loaded, _ = kick_model_adapter._load_bass_cache(wav, cache_dir)
    assert loaded is not None and len(loaded) == len(bass)
    on_in, on_out = _gate_onsets(bass), _gate_onsets(loaded)
    assert len(on_out) == len(on_in), (fmt, on_in, on_out)
    worst = max(abs(a - b) for a, b in zip(on_in, on_out))
    assert worst <= sr // 500, \
        f"[payload {fmt}] impostor shifted onsets by {worst/sr*1000:.2f} ms"


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_load_refuses_when_the_pilot_is_gone(tmp_path, monkeypatch):
    """The absolute threshold fails CLOSED at load: if the decoded stream no
    longer contains a locatable pilot, the loader returns a cache miss (the
    caller re-separates) rather than serving guessed alignment."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)

    real_read = kick_model_adapter.sf.read

    def pilotless_read(*args, **kwargs):
        y, rsr = real_read(*args, **kwargs)
        y = y.copy()
        y[:kick_model_adapter._OPUS_PILOT_LEN] = 0.0
        return y, rsr

    monkeypatch.setattr(kick_model_adapter.sf, "read", pilotless_read)
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is None, \
        "a stream with no locatable pilot must be a MISS, never a guess"


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_degraded_pilot_with_impostor_is_a_miss_not_a_capture(tmp_path, monkeypatch):
    """Codex's round-6 reproduction, verbatim: the combined case the earlier
    tests only covered separately. Bass carries a 1.5x impostor at data
    start; the LOAD decode comes back at uniform 0.49 gain, dropping the
    real pilot's response to ~0.48E (below the 0.5E threshold) while the
    impostor would still clear it. The quiet-preamble belt failed open here
    - the 20 ms before the impostor is the pilot's own trailing guard
    silence - producing an accepted 80 ms misalignment. With the bounded
    search window the impostor is never even read: the correct outcome is a
    MISS (re-separation), and anything else fails this test."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    chirp48 = kick_model_adapter._opus_pilot_chirp()
    fake = kick_model_adapter._resample(chirp48 * 1.5, 48000, sr).astype(np.float32)
    bass = np.concatenate(
        [fake, np.zeros(sr // 50, dtype=np.float32), _codex_gated_bass(sr)])
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)   # healthy save
    with np.load(kick_model_adapter._bass_cache_path(wav, cache_dir),
                 allow_pickle=False) as d:
        assert str(d["payload_format"]) == "opus", "fixture needs an opus payload"

    real_read = kick_model_adapter.sf.read

    def attenuated_read(*args, **kwargs):
        y, rsr = real_read(*args, **kwargs)
        return (y * 0.49).astype(y.dtype), rsr

    monkeypatch.setattr(kick_model_adapter.sf, "read", attenuated_read)
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is None, \
        "degraded pilot + surviving impostor must MISS, never mis-align"


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_delay_beyond_the_bound_is_a_miss(tmp_path, monkeypatch):
    """The 40 ms delay bound is load-bearing (it is what keeps the data
    region unreachable), so exceeding it must refuse, not stretch. A
    2500-sample injected delay pushes the real apex past the window: MISS."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)

    real_read = kick_model_adapter.sf.read

    def very_delayed_read(*args, **kwargs):
        y, rsr = real_read(*args, **kwargs)
        return np.concatenate([np.zeros(2500, dtype=y.dtype), y]), rsr

    monkeypatch.setattr(kick_model_adapter.sf, "read", very_delayed_read)
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is None, \
        "a delay beyond the bound is outside the construction - refuse it"


def test_opus_pilot_span_invariant_holds_as_constants():
    """The round-7 disjointness inequality, as an executable check so no
    future constant tweak can silently reopen it: the LAST candidate's
    entire correlation span must end at or before the data start."""
    assert (kick_model_adapter._OPUS_PILOT_GUARD
            + kick_model_adapter._OPUS_MAX_DECODER_DELAY
            + kick_model_adapter._OPUS_PILOT_CHIRP) \
        <= kick_model_adapter._OPUS_PILOT_LEN


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_chirp_tail_shaped_bass_head_cannot_capture(tmp_path):
    """Codex round 7, verbatim: bass whose FIRST 960 samples are
    1.5 x sign(chirp[1920:2880]) - shaped to resonate with the template's
    tail - followed by a 25 Hz periodic body. Under the 40 ms window this
    outscored the real pilot (1.0217E vs 0.9858E at candidate 2880, span
    reading into the bass) for an accepted 40 ms misalignment at ordinary
    decode settings. With the span-bounded window those samples are simply
    never read. Built directly at 48 kHz so the adversarial head survives
    exactly as constructed (no resample smearing)."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 48000
    chirp = kick_model_adapter._opus_pilot_chirp()
    evil_head = (1.5 * np.sign(chirp[1920:2880])).astype(np.float32)
    t = np.arange(sr * 5, dtype=np.float32) / sr
    body = (0.3 * np.sin(2 * np.pi * 25.0 * t)).astype(np.float32)
    bass = np.concatenate([evil_head, body])

    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)
    with np.load(kick_model_adapter._bass_cache_path(wav, cache_dir),
                 allow_pickle=False) as d:
        fmt = str(d["payload_format"])
        if fmt == "opus":
            nominal = kick_model_adapter._OPUS_PILOT_LEN
            ds = int(d["data_start_48"])
            assert abs(ds - nominal) < 200, \
                f"alignment captured by the chirp-tail-shaped bass head (ds={ds})"
    hit = kick_model_adapter._load_bass_cache(wav, cache_dir)
    assert hit is not None
    loaded, lsr = hit
    assert lsr == sr and len(loaded) == len(bass)
    # The body must not be shifted: correlate the periodic body region
    # unaligned - a 1920-sample capture drops this well below 0.9.
    c = float(np.corrcoef(bass[2000:], loaded[2000:])[0, 1])
    assert c > 0.95, f"[{fmt}] body mis-aligned (corr {c:.3f})"


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
@pytest.mark.parametrize("delay", [961, 962])
def test_opus_delay_just_beyond_the_bound_is_a_miss(tmp_path, monkeypatch, delay):
    """Codex round 8, deterministic samples 961 and 962: the true apex sits
    just outside the window, but the EDGE candidate still clears the energy
    threshold off the chirp's own autocorrelation (0.914E / 0.673E) - the
    old code accepted a one- or two-sample misalignment. Edge-apex refusal
    must turn both into a MISS."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)

    real_read = kick_model_adapter.sf.read

    def delayed_read(*args, **kwargs):
        y, rsr = real_read(*args, **kwargs)
        return np.concatenate([np.zeros(delay, dtype=y.dtype), y]), rsr

    monkeypatch.setattr(kick_model_adapter.sf, "read", delayed_read)
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is None, \
        f"delay {delay} (just over the bound) must MISS, never shift by a sample"


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_near_bound_in_band_delay_still_lands_exactly(tmp_path, monkeypatch):
    """The acceptance side of the same boundary: a 900-sample delay is
    inside the bound, peaks strictly interior, and must round-trip with
    exact onset timing - edge refusal must not eat legitimate decodes."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)

    real_read = kick_model_adapter.sf.read

    def delayed_read(*args, **kwargs):
        y, rsr = real_read(*args, **kwargs)
        return np.concatenate([np.zeros(900, dtype=y.dtype), y]), rsr

    monkeypatch.setattr(kick_model_adapter.sf, "read", delayed_read)
    hit = kick_model_adapter._load_bass_cache(wav, cache_dir)
    assert hit is not None, "an in-bound delay must still be served"
    loaded, _ = hit
    assert len(loaded) == len(bass)
    on_in, on_out = _gate_onsets(bass), _gate_onsets(loaded)
    assert len(on_out) == len(on_in)
    worst = max(abs(a - b) for a, b in zip(on_in, on_out))
    assert worst <= sr // 500, f"in-bound delay mis-served: {worst/sr*1000:.2f} ms"


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_polarity_inverted_decode_is_a_miss(tmp_path, monkeypatch):
    """Codex round 9: a decode returning -y hid the true apex from plain
    argmax (it reads -1.0E) while the chirp's -0.716E sidelobe at lag 7,
    flipped positive, passed threshold and edge refusal - an accepted
    7-sample misalignment with INVERTED audio, uncaught by any load gate.
    Locating on |c| and sign-gating the apex must turn this into a MISS."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)   # healthy save

    real_read = kick_model_adapter.sf.read

    def inverted_read(*args, **kwargs):
        y, rsr = real_read(*args, **kwargs)
        return -y, rsr

    monkeypatch.setattr(kick_model_adapter.sf, "read", inverted_read)
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is None, \
        "an inverted decode must MISS, never serve shifted inverted audio"


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_duplicate_pilot_in_the_predata_zone_is_a_miss(tmp_path, monkeypatch):
    """Codex round 10, construction 1: a second pilot injected at sample 300
    while the genuine one sits at 960 - the fake scored 1.0004E, won argmax,
    sat interior and positive, and was accepted as a 660-sample (13.75 ms)
    error. Length reconciliation catches it: a pilot found EARLIER than the
    true one leaves 660 surplus samples beyond n48 + TAIL_PAD."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)

    chirp = kick_model_adapter._opus_pilot_chirp()
    real_read = kick_model_adapter.sf.read

    def doubled_read(*args, **kwargs):
        y, rsr = real_read(*args, **kwargs)
        y = y.copy()
        end = 300 + len(chirp)
        if len(y) >= end:
            y[300:end] = y[300:end] + chirp
        return y, rsr

    monkeypatch.setattr(kick_model_adapter.sf, "read", doubled_read)
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is None, \
        "a duplicated pilot must MISS, never accept a 13.75 ms shift"


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_nan_in_the_decode_is_a_miss(tmp_path, monkeypatch):
    """Codex round 10, construction 2: one NaN at decoded sample 2880 makes
    c[1:] NaN; argmax anoints index 1 and `NaN < threshold` is False - an
    accepted 959-sample error. Non-finite correlation must MISS."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)

    real_read = kick_model_adapter.sf.read

    def nan_read(*args, **kwargs):
        y, rsr = real_read(*args, **kwargs)
        y = y.copy()
        if len(y) > 2880:
            y[2880] = np.nan
        return y, rsr

    monkeypatch.setattr(kick_model_adapter.sf, "read", nan_read)
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is None, \
        "a NaN-poisoned decode must MISS, never mis-locate"


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_later_forged_pilot_inside_the_length_band_is_a_miss(tmp_path, monkeypatch):
    """Codex round 11, construction 1: erase the genuine pilot, place the
    chirp at sample 1919 - interior, positive, above threshold, and the
    959-sample surplus hides INSIDE the tail-pad allowance of the length
    band. Only the source-excerpt anchor can see it: at the claimed
    alignment the decoded content is 959 samples early, so the excerpt's
    correlation peak leaves the +/-2 ms scan. MISS."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)

    chirp = kick_model_adapter._opus_pilot_chirp()
    real_read = kick_model_adapter.sf.read

    def forged_read(*args, **kwargs):
        y, rsr = real_read(*args, **kwargs)
        y = y.copy()
        y[:kick_model_adapter._OPUS_PILOT_LEN] = 0.0          # erase genuine
        y[1919:1919 + len(chirp)] = chirp                     # forge later
        return y, rsr

    monkeypatch.setattr(kick_model_adapter.sf, "read", forged_read)
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is None, \
        "a later-forged pilot must MISS, never serve 20 ms-early bass"


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_earlier_forgery_composed_with_tail_truncation_is_a_miss(tmp_path, monkeypatch):
    """Codex round 11, construction 2: the round-10 earlier-pilot forgery
    (delta = 660) hidden by truncating 660 tail samples, so the length
    reconciliation balances. The excerpt anchor still sees the 660-sample
    shift at the claimed alignment. MISS."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)

    chirp = kick_model_adapter._opus_pilot_chirp()
    real_read = kick_model_adapter.sf.read

    def forged_read(*args, **kwargs):
        y, rsr = real_read(*args, **kwargs)
        y = y.copy()
        y[:kick_model_adapter._OPUS_PILOT_LEN] = 0.0
        y[300:300 + len(chirp)] = chirp                       # forge earlier
        return y[:len(y) - 660], rsr                          # hide with trim

    monkeypatch.setattr(kick_model_adapter.sf, "read", forged_read)
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is None, \
        "earlier forgery + tail trim must MISS, never serve shifted bass"


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_sidecar_without_the_excerpt_anchor_fails_closed(tmp_path):
    """A pilot-payload sidecar written before the excerpt anchor existed has
    no ground truth to verify against - it must MISS (one re-separation,
    once), never be served on structural gates alone."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)

    path = kick_model_adapter._bass_cache_path(wav, cache_dir)
    with np.load(path, allow_pickle=False) as d:
        assert str(d["payload_format"]) == "opus"
        stripped = {k: d[k] for k in d.files
                    if k not in ("anchor_refs", "anchor_starts")}
    np.savez_compressed(path, **stripped)
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is None


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_float16_overflowing_anchor_refuses_at_save(tmp_path):
    """Codex round 12: a source sample beyond float16's 65504 ceiling casts
    the anchor to inf, whose NaN score compares False against the threshold
    - a fail-OPEN anchor that let the later-pilot forgery back in. The saver
    must refuse the anchor (int16 fallback), and the audio still round-trips
    sample-exactly through the fallback."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr) * 0.001          # quiet body...
    bass[len(bass) // 2] = 65520.0                # ...so the spike window wins
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)
    with np.load(kick_model_adapter._bass_cache_path(wav, cache_dir),
                 allow_pickle=False) as d:
        assert str(d["payload_format"]) == "int16", \
            "an anchor that cannot verify must not exist"
    loaded, _ = kick_model_adapter._load_bass_cache(wav, cache_dir)
    step = float(np.abs(bass).max()) / 32767.0
    assert np.max(np.abs(loaded - bass)) <= step * 1.01


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_nonfinite_anchor_on_disk_is_a_miss(tmp_path):
    """Belt for the load side of the same hole: a sidecar whose stored
    anchor carries inf (however it got there) must MISS - previously the
    NaN score sailed past `< 0.9` and served a forged alignment."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)

    path = kick_model_adapter._bass_cache_path(wav, cache_dir)
    with np.load(path, allow_pickle=False) as d:
        fields = {k: d[k].copy() for k in d.files}
    refs = fields["anchor_refs"].astype(np.float16)
    refs[0, 10] = np.float16(np.inf)
    fields["anchor_refs"] = refs
    np.savez_compressed(path, **fields)
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is None


@pytest.mark.skipif(not kick_model_adapter._opus_available(),
                    reason="libsndfile lacks OGG/OPUS")
def test_opus_midstream_splice_is_a_miss(tmp_path, monkeypatch):
    """Codex round 13, verbatim: decode faithfully through the pilot and the
    early audio, insert 4800 zero samples after the first anchor's region,
    trim 4800 from the tail pad so length reconciliation balances. One local
    anchor scored 1.0 and everything after the splice was served 100 ms
    late. The mid and tail anchors must desync and MISS."""
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    sr = 44100
    bass = _codex_gated_bass(sr, seconds=10)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, sr)

    with np.load(kick_model_adapter._bass_cache_path(wav, cache_dir),
                 allow_pickle=False) as d:
        assert str(d["payload_format"]) == "opus"
        assert len(np.asarray(d["anchor_starts"])) >= 3, \
            "fixture long enough to carry spread anchors"
        first_end = (int(d["data_start_48"]) + int(np.min(d["anchor_starts"]))
                     + int(np.asarray(d["anchor_refs"]).shape[1]))

    real_read = kick_model_adapter.sf.read

    def spliced_read(*args, **kwargs):
        y, rsr = real_read(*args, **kwargs)
        cut = min(first_end + 100, len(y) - 4800)
        y = np.concatenate([y[:cut],
                            np.zeros(4800, dtype=y.dtype),
                            y[cut:len(y) - 4800]])
        return y, rsr

    monkeypatch.setattr(kick_model_adapter.sf, "read", spliced_read)
    assert kick_model_adapter._load_bass_cache(wav, cache_dir) is None, \
        "a mid-stream splice must MISS, never serve 100 ms-late audio"


def test_default_payload_is_int16_pending_arbitration(tmp_path, monkeypatch):
    """The SHIPPED default: OPUS_PAYLOAD_ENABLED is False, so saves take the
    int16 payload - no decoder, no member of the round-12..14 objection
    class - until Sam arbitrates the opus residual and flips the flag."""
    monkeypatch.setattr(kick_model_adapter, "OPUS_PAYLOAD_ENABLED", False)
    wav = _make_wav(tmp_path)
    cache_dir = tmp_path / "_Stem Analysis"
    bass = _codex_gated_bass(44100)
    kick_model_adapter._save_bass_cache(wav, cache_dir, bass, 44100)
    with np.load(kick_model_adapter._bass_cache_path(wav, cache_dir),
                 allow_pickle=False) as d:
        assert str(d["payload_format"]) == "int16"
    loaded, _ = kick_model_adapter._load_bass_cache(wav, cache_dir)
    step = float(np.abs(bass).max()) / 32767.0
    assert np.max(np.abs(loaded - bass)) <= step * 1.01

    # And the module-level default itself, so a flag flip is always a
    # deliberate, visible act:
    import importlib, sys as _sys
    src = Path(kick_model_adapter.__file__).read_text(encoding="utf-8")
    assert "OPUS_PAYLOAD_ENABLED = False" in src
