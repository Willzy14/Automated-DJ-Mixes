"""SPIKE — does Demucs stem separation give a cleaner section-boundary signal
than the current 3-band amplitude detector?

ANALYSIS ONLY. Stems are derived purely to read structure; the original WAV is
never altered and is the only audio that would ever go in a mix. No stem audio
is even written to disk — we separate in-memory and keep only tiny per-stem
energy envelopes (cached as .npz), so this also avoids the torchaudio/torchcodec
FFmpeg dependency entirely (uses soundfile for I/O).

For each track this:
  1. Separates into drums/bass/vocals/other (Demucs htdemucs, in-memory).
  2. Computes a per-stem RMS energy envelope.
  3. For every CURRENT section boundary (from the blind-viz stats JSON), measures
     how much each stem steps across it (Δ over N bars before vs after):
       - real break  -> bass/drums drop out (strong negative Δ)
       - real outro  -> vocals/mids drop, bass holds
       - spurious cut -> ~0 Δ in every stem
  4. Renders a comparison PNG and prints a per-boundary verdict.

Usage:
    python Source/stem_section_probe.py "<project-path>" [--track "<wav name>"]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import soundfile as sf

STEM_COLORS = {"drums": "#e4572e", "bass": "#2e86ab", "vocals": "#8e44ad", "other": "#3a3a3a"}
# Coloured section zones (overlaid on both the track and the stems).
SECTION_COLORS = {
    "intro": "#5b8def", "build": "#f4b400", "drop": "#e4572e",
    "break": "#8e44ad", "fill": "#9e9e9e", "outro": "#2e9e5b",
}


def _seccol(label):
    return SECTION_COLORS.get(label, "#cccccc")


_MODEL = None


def _device() -> str:
    """GPU if a CUDA build + GPU are present, else CPU. Demucs separation runs
    ~10-30x faster on the GPU — one-time PyTorch CUDA install (Sam 2026-06-10)."""
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def _model():
    global _MODEL
    if _MODEL is None:
        from demucs.pretrained import get_model
        _MODEL = get_model("htdemucs")
        _MODEL.to(_device()).eval()
    return _MODEL


def _separate_envelopes(wav_path: Path, cache_dir: Path, hop_sec: float = 0.1):
    """Return ({stem: rms_envelope}, hop_seconds). Cached as .npz so the slow
    separation only runs once per track."""
    cache = cache_dir / f"{wav_path.stem}__stemenv.npz"
    if cache.exists():
        d = np.load(cache, allow_pickle=False)
        hop_t = float(d["hop_t"])
        return {k: d[k] for k in d.files if k != "hop_t"}, hop_t

    import torch
    from demucs.apply import apply_model

    data, sr = sf.read(str(wav_path), always_2d=True)      # [n, ch]
    wav = data.T.astype(np.float32)                         # [ch, n]
    if wav.shape[0] == 1:
        wav = np.vstack([wav, wav])
    if sr != 44100:
        import librosa
        wav = librosa.resample(wav, orig_sr=sr, target_sr=44100)
        sr = 44100

    model = _model()
    src_names = list(model.sources)                         # ['drums','bass','other','vocals']
    t = torch.from_numpy(wav)
    ref = t.mean(0)
    t = (t - ref.mean()) / (ref.std() + 1e-8)
    dev = _device()
    print(f"  separating {wav_path.name} ({dev.upper()})...")
    with torch.no_grad():
        out = apply_model(model, t[None], device=dev, progress=True)[0]   # [src, ch, n]
    out = out * (ref.std() + 1e-8) + ref.mean()

    hop = max(1, int(sr * hop_sec))

    def _env(mono):
        nfr = len(mono) // hop
        fr = mono[: nfr * hop].reshape(nfr, hop)
        return np.sqrt((fr.astype(np.float64) ** 2).mean(axis=1) + 1e-12)

    envs = {name: _env(out[i].mean(0).cpu().numpy()) for i, name in enumerate(src_names)}
    envs["mix"] = _env(wav.mean(0))   # original-track envelope, for the top panel
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, hop_t=np.array(hop / sr), **envs)
    return envs, hop / sr


def _load_stats(blind_dir: Path, track_stem: str):
    for p in blind_dir.glob("*_stats.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("track") == track_stem:
            return d
    return None


def probe_track(wav: Path, project: Path):
    blind_dir = next((project / "Sections Review").glob("Blind_V*"), None)
    stats = _load_stats(blind_dir, wav.stem) if blind_dir else None
    if not stats:
        print(f"  [skip] no stats JSON matching {wav.stem}")
        return

    envs, hop_t = _separate_envelopes(wav, project / "_Stem Analysis")
    L = min(len(e) for e in envs.values())
    envs = {k: v[:L] for k, v in envs.items()}
    t = np.arange(L) * hop_t

    bpm = stats["bpm"]
    win = 4 * (4 * 60.0 / bpm)   # 4 bars either side
    sections = stats["sections"]

    def smean(env, a, b):
        ia, ib = max(0, int(a / hop_t)), min(L, int(b / hop_t))
        return float(env[ia:ib].mean()) if ib > ia else 0.0

    print(f"\n=== {wav.stem}  ({bpm:.1f} BPM) ===")
    print(f"{'bar':>6} {'transition':<20} {'Δdrums':>7} {'Δbass':>7} {'Δvocal':>7} {'Δother':>7}  verdict")
    for i in range(1, len(sections)):
        prev, cur = sections[i - 1], sections[i]
        tb, bar, label = cur["start_sec"], cur.get("src_bar_start", 0.0), cur["label"]
        d = {}
        for s, env in envs.items():
            before, after = smean(env, tb - win, tb), smean(env, tb, tb + win)
            d[s] = (after - before) / max(before, after, 1e-6)
        strong = {s: v for s, v in d.items() if abs(v) >= 0.25}
        if not strong:
            verdict = "FLAT — no stem step (boundary likely spurious)"
        elif label == "break" and d["bass"] <= -0.25:
            verdict = "break CONFIRMED (bass drops)"
        elif label == "break" and d["bass"] > -0.10:
            verdict = "break DOUBTFUL (bass holds)"
        elif label == "outro" and d["vocals"] <= -0.20 and d["bass"] > -0.30:
            verdict = "outro CONFIRMED (vox out, bass holds)"
        else:
            verdict = "step: " + ",".join(sorted(strong, key=lambda s: -abs(strong[s])))
        print(f"{bar:>6.0f} {prev['label']+'->'+cur['label']:<20} "
              f"{d['drums']:>+7.2f} {d['bass']:>+7.2f} {d['vocals']:>+7.2f} {d['other']:>+7.2f}  {verdict}")

    # Comparison PNG — original track (top, prominent) + 4 stems, sharing one
    # timeline, with the current section zones coloured + labelled on every
    # panel so you can see whether each cut lines up with a real stem event.
    order = [s for s in ("drums", "bass", "vocals", "other") if s in envs]
    mix = envs["mix"]
    fig, axes = plt.subplots(len(order) + 1, 1, figsize=(18, 10), sharex=True,
                             gridspec_kw={"height_ratios": [2.2] + [1] * len(order)})

    def _zones(ax, label_it=False):
        for sec in sections:
            ax.axvspan(sec["start_sec"], sec["end_sec"], color=_seccol(sec["label"]), alpha=0.16, lw=0)
            ax.axvline(sec["start_sec"], color="k", lw=0.6, alpha=0.45)
            if label_it:
                mid = 0.5 * (sec["start_sec"] + sec["end_sec"])
                ax.text(mid, 0.94, sec["name"], rotation=90, fontsize=6,
                        va="top", ha="center", color="#111")

    axes[0].plot(t, mix / (mix.max() + 1e-9), color="#222", lw=0.7)
    axes[0].fill_between(t, mix / (mix.max() + 1e-9), color="#222", alpha=0.22)
    axes[0].set_ylabel("TRACK\n(original)", fontsize=9, fontweight="bold")
    axes[0].set_ylim(0, 1.05)
    axes[0].set_title(f"{wav.stem}   —   current section cuts on the track vs the Demucs stems "
                      f"({bpm:.1f} BPM)", fontsize=11)
    _zones(axes[0], label_it=True)

    for i, s in enumerate(order):
        ax = axes[i + 1]
        e = envs[s]
        ax.fill_between(t, e / (e.max() + 1e-9), color=STEM_COLORS[s], alpha=0.85)
        ax.set_ylabel(s, fontsize=9, color=STEM_COLORS[s], fontweight="bold")
        ax.set_ylim(0, 1.05)
        _zones(ax)

    axes[-1].set_xlabel("seconds")
    fig.tight_layout()
    out = project / "_Stem Analysis" / f"PROBE_{wav.stem}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=95)
    plt.close(fig)
    print(f"  -> {out.name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--track", type=str, default=None)
    args = ap.parse_args()
    audio = args.project / "Audio"
    wavs = [audio / args.track] if args.track else sorted(audio.glob("*.wav"))
    for w in wavs:
        probe_track(w, args.project)


if __name__ == "__main__":
    main()
