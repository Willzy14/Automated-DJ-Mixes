"""Section-placement review viz — ALL FOUR STEMS.

Renders, per track, the real audio as five panels (full mix + drums/kick, bass,
vocals, other) with the pipeline's placed section boundaries drawn on top, plus
the measured cues (kick out/in, bass in/out, fills). The test it answers: does
each intro/drop/break/outro boundary land on the actual musical event — judged
against the KICK and bass, not just the broadband envelope.

Reuses the cached Demucs stem envelopes (_Stem Analysis/*__stemenv.npz) so it is
instant — no re-separation. Boundaries + cues come from the SECTIONS_STEM_*.json
the pipeline already wrote (these ARE the placed sections).

Usage:
    set PYTHONPATH=Source
    python -m section_placement_viz "Test Project/24.06.26"
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from stem_section_probe import _separate_envelopes

STAMP = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

SECCOL = {
    "intro": "#2e9e5b", "build": "#17a2b8", "break": "#1f4e9e",
    "drop": "#d4a017", "fill": "#ff8c00", "outro": "#c0392b",
}
STEMS = ["drums", "bass", "vocals", "other"]
STEM_COLORS = {"drums": "#c0392b", "bass": "#7b3fa0", "vocals": "#8e44ad", "other": "#2e7d32"}
PANEL_LABEL = {"drums": "DRUMS / KICK", "bass": "BASS", "vocals": "VOCALS", "other": "OTHER"}


def _grid(ax, downbeat, sec_per_bar, n_bars, bold=False):
    for b in range(0, n_bars + 1, 4):
        is16 = (b % 16 == 0)
        ax.axvline(downbeat + b * sec_per_bar, color="#cfcfcf",
                   lw=(1.0 if is16 else 0.4), alpha=0.7, zorder=0)


def render(project: Path, wav: Path, data: dict, out_dir: Path):
    sections = data["sections"]
    bpm = data["bpm"]
    signals = data.get("signals", {})
    downbeat = float(sections[0]["start_sec"]) if sections else 0.0
    sec_per_bar = 4 * 60.0 / bpm

    cache_dir = project / "_Stem Analysis"
    envs, hop_t = _separate_envelopes(wav, cache_dir)
    order = [s for s in STEMS if s in envs]
    L = min(len(envs[s]) for s in order + ["mix"])
    t = np.arange(L) * hop_t
    dur = L * hop_t
    n_bars = int((dur - downbeat) / sec_per_bar) + 1

    kick_out = [c["start_sec"] for c in signals.get("kick_cues", []) if c["type"] == "kick_dropout"]
    kick_in = [c["start_sec"] for c in signals.get("kick_cues", []) if c["type"] == "kick_return"]

    n = len(order) + 1
    fig, axes = plt.subplots(n, 1, figsize=(22, 12), sharex=True,
                             gridspec_kw={"height_ratios": [2.6, 1.5, 1.5, 1.0, 1.0][:n]})
    fig.subplots_adjust(left=0.035, right=0.995, top=0.93, bottom=0.05, hspace=0.13)

    # --- mix panel: sections + labels + cues ---
    ax0 = axes[0]
    _grid(ax0, downbeat, sec_per_bar, n_bars)
    mix = envs["mix"][:L]
    ax0.fill_between(t, mix / (mix.max() + 1e-9), color="#666", alpha=0.5, lw=0, zorder=1)
    for s in sections:
        a, b = float(s["start_sec"]), float(s["end_sec"])
        col = SECCOL.get(s["label"], "#777")
        ax0.axvspan(a, b, color=col, alpha=0.17, lw=0, zorder=2)
        ax0.axvline(a, color="k", lw=1.2, alpha=0.65, zorder=4)
        nb = s["end_bar"] - s["start_bar"]
        ax0.text(0.5 * (a + b), 0.86, f"{s['label']}\n{nb}b\n@bar {s['start_bar']}",
                 ha="center", va="top", fontsize=9, zorder=6,
                 bbox=dict(boxstyle="round,pad=0.28", fc="white", ec=col, alpha=0.9))
    ax0.axvline(sections[-1]["end_sec"], color="k", lw=1.2, alpha=0.65, zorder=4)
    for a, b in signals.get("fills", []):
        ax0.axvspan(a, b, color="#ff8c00", alpha=0.75, lw=0, zorder=5)
    for bar in range(0, n_bars + 1, 16):
        ax0.text(downbeat + bar * sec_per_bar, 0.02, str(bar), fontsize=8.5, color="#000",
                 ha="center", va="bottom", zorder=8, fontweight="bold",
                 bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.7))
    ax0.set_ylim(0, 1.06)
    ax0.set_yticks([])
    ax0.set_ylabel("FULL MIX", fontsize=9, fontweight="bold")
    seq = "  ".join(f"{s['label']}{s['end_bar'] - s['start_bar']}" for s in sections)
    ax0.set_title(
        f"{wav.stem}    [{len(sections)} sections · {int(dur//60)}:{int(dur%60):02d} · "
        f"{bpm:.0f} BPM · downbeat {downbeat:.2f}s · {n_bars} bars]   "
        f"black=section cut  gold dash=kick OUT  teal dot=kick IN  blue=bass in/out  orange=fill\n"
        f"sections: {seq}        ·  generated {STAMP}",
        fontsize=11, loc="left")

    # --- stem panels ---
    for i, s in enumerate(order):
        ax = axes[i + 1]
        _grid(ax, downbeat, sec_per_bar, n_bars)
        e = envs[s][:L]
        ax.fill_between(t, e / (e.max() + 1e-9), color=STEM_COLORS[s], alpha=0.8, lw=0, zorder=1)
        for sec in sections:
            ax.axvline(float(sec["start_sec"]), color="k", lw=0.9, alpha=0.45, zorder=4)
        if s == "drums":
            for x in kick_out:
                ax.axvline(x, color="#d4a017", lw=1.3, alpha=0.95, ls=(0, (3, 2)), zorder=5)
            for x in kick_in:
                ax.axvline(x, color="#17a2b8", lw=1.3, alpha=0.9, ls=(0, (1, 1)), zorder=5)
        if s == "bass":
            for key, ls in (("bass_in", "-"), ("bass_out", "--")):
                x = signals.get(key)
                if x is not None:
                    ax.axvline(x, color="#1f4e9e", lw=2.2, alpha=0.95, ls=ls, zorder=6)
        ax.set_ylim(0, 1.06)
        ax.set_yticks([])
        ax.set_ylabel(PANEL_LABEL[s], fontsize=9, color=STEM_COLORS[s], fontweight="bold")

    axes[-1].set_xlim(0, dur)
    axes[-1].set_xlabel("time (s)")

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"PLACE_{wav.stem}.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def main():
    project = Path(sys.argv[1])
    stem_dir = project / "_Stem Analysis"
    audio_dir = project / "Audio"
    out_dir = project / "Sections Review" / "PLACEMENT"
    jsons = sorted(stem_dir.glob("SECTIONS_STEM_*.json"))
    print(f"{len(jsons)} section JSONs")
    for jp in jsons:
        track = jp.stem[len("SECTIONS_STEM_"):]
        wav = audio_dir / f"{track}.wav"
        if not wav.exists():
            print(f"  [skip] no audio for {track}")
            continue
        data = json.loads(jp.read_text(encoding="utf-8"))
        try:
            out = render(project, wav, data, out_dir)
            print(f"  wrote {out.name}")
        except Exception as e:
            print(f"  [FAIL] {track}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
