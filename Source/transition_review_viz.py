"""Render per-transition PNGs from a sections JSON + audio.

For each transition, shows the overlap zone with:
  - Outgoing waveform with section colour bands
  - Incoming waveform with section colour bands
  - Vertical lines at outgoing outro start AND incoming first rise (build/drop)
  - Bar grid

Output: <project>/Output/Visualisations/Transitions_V<N>/T<NN>_<out>_to_<in>.png
"""

from __future__ import annotations
import json
import sys
from pathlib import Path

import librosa
import matplotlib.pyplot as plt
import numpy as np


LABEL_COLOURS = {
    "intro":  "#7ec850",
    "build":  "#5bc0de",
    "drop":   "#f0c020",
    "break":  "#5099d8",
    "fill":   "#e8a04a",
    "outro":  "#e25f5f",
}


def bpm_from_track(audio_dir: Path, name: str) -> float:
    """Estimate BPM from filename or use librosa."""
    # Simple: use librosa beat_track once. Sam's WAVs are tagged-aligned to
    # whole BPM in MIK so the estimate is reliable.
    wav = audio_dir / (name + ".wav")
    if not wav.exists():
        wav = audio_dir / name
    y, sr = librosa.load(str(wav), sr=22050, mono=True)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    return float(tempo)


def _compute_bands(audio, sr):
    """Return (full_env, low_env, mid_env, hi_env) on a 50ms grid, each
    normalised to its own 0..0.9 peak so they overlay readably."""
    try:
        import scipy.signal as sps
        sos_low = sps.butter(4, 250, "lowpass", fs=sr, output="sos")
        sos_mid = sps.butter(4, [250, 2500], "bandpass", fs=sr, output="sos")
        sos_hi  = sps.butter(4, 2500, "highpass", fs=sr, output="sos")
        a_low = sps.sosfilt(sos_low, audio)
        a_mid = sps.sosfilt(sos_mid, audio)
        a_hi  = sps.sosfilt(sos_hi, audio)
    except Exception:
        a_low = audio * 0.0; a_mid = audio * 0.0; a_hi = audio * 0.0

    bin_sec = 0.05
    samples_per_bin = max(1, int(bin_sec * sr))
    n_bins = len(audio) // samples_per_bin
    def _e(sig):
        m = np.abs(sig[: n_bins * samples_per_bin]).reshape(n_bins, samples_per_bin)
        return m.max(axis=1)
    def _n(a):
        p = a.max()
        return a / p * 0.9 if p > 0 else a
    full = _e(audio)
    low = _n(_e(a_low))
    mid = _n(_e(a_mid))
    hi = _n(_e(a_hi))
    return full, low, mid, hi, bin_sec


def render_transition_full_context(out_name, out_secs, out_bpm, out_audio,
                                   in_name, in_secs, in_bpm, in_audio,
                                   out_path: Path, t_index: int):
    """Ableton-arrangement-style view: both FULL tracks stacked vertically,
    each positioned at its arrangement start so the overlap zone visually
    aligns. Lets you see the chop in the context of each track's full shape.
    """
    sr = 22050
    out_full, out_low, out_mid, out_hi, bin_sec = _compute_bands(out_audio, sr)
    in_full,  in_low,  in_mid,  in_hi,  _       = _compute_bands(in_audio, sr)

    # Time axes for each track, in arrangement beats.
    # Each track's local time (seconds since track start) maps to:
    #   arr_beat = arr_start + (local_sec - first_source_sec) * bpm/60
    # But our sections JSON already encodes arr_time per section in arrangement
    # beats — we use the section's source_start_beats as the local-zero ref.
    out_arr_start = out_secs[0]["arr_time"]
    out_src_start = out_secs[0]["source_start_beats"]
    in_arr_start  = in_secs[0]["arr_time"]
    in_src_start  = in_secs[0]["source_start_beats"]
    out_beats_per_sec = out_bpm / 60
    in_beats_per_sec  = in_bpm / 60

    out_bin_times = np.arange(len(out_full)) * bin_sec  # seconds
    in_bin_times  = np.arange(len(in_full))  * bin_sec
    out_arr_times = out_arr_start + (out_bin_times * out_beats_per_sec - out_src_start)
    in_arr_times  = in_arr_start  + (in_bin_times  * in_beats_per_sec  - in_src_start)

    # Normalise full envelope to 0..1 per track (visual scale only)
    if out_full.max() > 0: out_full = out_full / out_full.max() * 0.9
    if in_full.max()  > 0: in_full  = in_full  / in_full.max()  * 0.9

    # X range covers both tracks end-to-end
    x_lo = min(out_arr_times[0], in_arr_times[0]) - 8
    x_hi = max(out_arr_times[-1], in_arr_times[-1]) + 8
    span_bars = (x_hi - x_lo) / 4

    # Width scales with span — keep ~30 bars per inch so it stays readable
    fig_w = max(20, min(60, span_bars / 8))
    fig, axes = plt.subplots(2, 1, figsize=(fig_w, 8), dpi=100, sharex=True)
    ax_out, ax_in = axes

    for ax, name, secs, arr_times, full, low, mid, hi in [
        (ax_out, out_name, out_secs, out_arr_times, out_full, out_low, out_mid, out_hi),
        (ax_in,  in_name,  in_secs,  in_arr_times,  in_full,  in_low,  in_mid,  in_hi),
    ]:
        ax.fill_between(arr_times, -full, full, color="#444", alpha=0.7, linewidth=0, zorder=1)
        ax.plot(arr_times,  low, color="#ff5050", linewidth=0.5, alpha=0.85, zorder=4)
        ax.plot(arr_times, -low, color="#ff5050", linewidth=0.5, alpha=0.85, zorder=4)
        ax.plot(arr_times,  mid, color="#50d050", linewidth=0.4, alpha=0.75, zorder=4)
        ax.plot(arr_times, -mid, color="#50d050", linewidth=0.4, alpha=0.75, zorder=4)
        ax.plot(arr_times,  hi,  color="#6080ff", linewidth=0.4, alpha=0.7,  zorder=4)
        ax.plot(arr_times, -hi,  color="#6080ff", linewidth=0.4, alpha=0.7,  zorder=4)
        # Section bands
        for s in secs:
            colour = LABEL_COLOURS.get(s["label"].lower(), "#888")
            ax.axvspan(s["arr_time"], s["arr_end"], color=colour, alpha=0.22, zorder=0)
            mid_x = (s["arr_time"] + s["arr_end"]) / 2
            ax.text(mid_x, -0.95, s["name"], ha="center", va="top", fontsize=6,
                    color="black",
                    bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.7),
                    zorder=5)
        ax.set_ylim(-1.05, 1.05)
        ax.set_yticks([])
        ax.set_title(name[:80], fontsize=10, loc="left")

    # Overlap zone shading on both axes
    ov_start = in_secs[0]["arr_time"]
    ov_end   = out_secs[-1]["arr_end"]
    out_outro = next((s for s in out_secs if s["label"].lower() == "outro"), None)
    in_rise = next((s for s in in_secs if s["label"].lower() in ("build", "drop")), None)

    for ax in axes:
        ax.axvspan(ov_start, ov_end, color="#ffff20", alpha=0.08, zorder=0)
        ax.axvline(ov_start, color="lime", lw=1.5, alpha=0.8,
                   label=f"overlap start {ov_start:.0f}")
        ax.axvline(ov_end, color="red", lw=1.5, alpha=0.8,
                   label=f"overlap end {ov_end:.0f}")
        if out_outro:
            ax.axvline(out_outro["arr_time"], color="orange", lw=2.5, alpha=0.9, ls="--",
                       label=f"out outro start {out_outro['arr_time']:.0f}")
        if in_rise:
            ax.axvline(in_rise["arr_time"], color="cyan", lw=2.5, alpha=0.9, ls=":",
                       label=f"in {in_rise['name']} start {in_rise['arr_time']:.0f}")

    # Bar gridlines (every 16 bars on this wide view)
    first_bar = int(x_lo / 4) * 4
    for b in range(first_bar, int(x_hi) + 16, 16):
        for ax in axes:
            ax.axvline(b, color="#000", alpha=0.10, lw=0.4, zorder=0)

    ax_out.set_xlim(x_lo, x_hi)
    ax_out.legend(loc="upper right", fontsize=8, framealpha=0.85)
    ax_in.set_xlabel("Arrangement beat")
    overlap_bars = (ov_end - ov_start) / 4
    fig.suptitle(
        f"T{t_index}: FULL CONTEXT — both tracks at arrangement positions.  "
        f"Overlap {ov_end - ov_start:.0f} beats ({overlap_bars:.0f} bars).  "
        f"Grey=full envelope, red=low(<250), green=mid(250-2500), blue=high(>2500).",
        fontsize=10, y=0.98)
    fig.tight_layout()
    fig.savefig(out_path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def render_transition(out_name, out_secs, out_bpm, out_audio,
                      in_name, in_secs, in_bpm, in_audio,
                      out_path: Path, t_index: int):
    # Overlap zone in arrangement beats
    ov_start = in_secs[0]["arr_time"]
    ov_end = out_secs[-1]["arr_end"]
    ctx_bars = 8  # context before & after
    view_start = ov_start - ctx_bars * 4
    view_end = ov_end + ctx_bars * 4
    view_beats = view_end - view_start

    fig, axes = plt.subplots(2, 1, figsize=(18, 8), dpi=110, sharex=True)
    ax_out, ax_in = axes

    # Outgoing
    out_arr_start = out_secs[0]["arr_time"]
    out_src_start = out_secs[0]["source_start_beats"]
    out_arr_end = out_secs[-1]["arr_end"]
    # Sample rate for audio
    out_sr_per_beat = 22050 * 60 / out_bpm  # samples per beat

    # Build waveform RMS envelope
    def rms_envelope(y, sr, window_sec=0.05):
        hop = int(sr * window_sec)
        rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
        times = np.arange(len(rms)) * window_sec
        return times, rms

    for ax, name, secs, bpm, audio, arr_start, src_start in [
        (ax_out, out_name, out_secs, out_bpm, out_audio, out_arr_start, out_src_start),
        (ax_in, in_name, in_secs, in_bpm, in_audio, in_secs[0]["arr_time"], in_secs[0]["source_start_beats"]),
    ]:
        sr = 22050
        beats_per_sec = bpm / 60
        # For each visible arrangement beat, find the source sample
        # arr_beat -> src_beat = src_start + (arr_beat - arr_start)
        # src_beat -> sample = src_beat / beats_per_sec * sr
        times_arr_beats = np.linspace(view_start, view_end, 4000)
        src_beats = src_start + (times_arr_beats - arr_start)
        sample_idx = (src_beats / beats_per_sec * sr).astype(int)
        valid = (sample_idx >= 0) & (sample_idx < len(audio))
        wave = np.zeros_like(times_arr_beats)
        wave[valid] = np.abs(audio[sample_idx[valid]])
        # Smooth with a rolling max for visual
        win = 40
        smooth = np.maximum.accumulate(np.concatenate([wave[:win], np.maximum(wave[win:], 0)]))
        # Simple moving average
        kernel = np.ones(20) / 20
        smooth = np.convolve(wave, kernel, mode='same')

        ax.fill_between(times_arr_beats, -smooth, smooth, color="#444", alpha=0.7)

        # Colour bands per section
        for s in secs:
            label = s["label"].lower()
            colour = LABEL_COLOURS.get(label, "#888")
            s_arr = s["arr_time"]
            e_arr = s["arr_end"]
            ax.axvspan(s_arr, e_arr, color=colour, alpha=0.25, zorder=0)
            # Label
            mid = (s_arr + e_arr) / 2
            if view_start <= mid <= view_end:
                ax.text(mid, 0.85, s["name"], ha="center", va="top",
                        fontsize=8, color="black",
                        bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.7))

        ax.set_ylim(-1, 1)
        ax.set_xlim(view_start, view_end)
        ax.set_title(name[:60], fontsize=10, loc="left")

        # Bar gridlines (16-beat = 4 bars)
        first_bar = int(view_start / 4) * 4
        for b in range(first_bar, int(view_end) + 4, 4):
            ax.axvline(b, color="#000", alpha=0.05, lw=0.5)
        for b in range(first_bar, int(view_end) + 16, 16):
            ax.axvline(b, color="#000", alpha=0.15, lw=0.8)

    # Vertical lines for key transition points
    # Outgoing outro start
    out_outro = next((s for s in out_secs if s["label"].lower() == "outro"), None)
    in_rise = next((s for s in in_secs if s["label"].lower() in ("build", "drop")), None)

    for ax in axes:
        ax.axvline(ov_start, color="lime", lw=2, alpha=0.8, label=f"overlap start {ov_start:.0f}")
        ax.axvline(ov_end, color="red", lw=2, alpha=0.8, label=f"overlap end {ov_end:.0f}")
        if out_outro:
            ax.axvline(out_outro["arr_time"], color="orange", lw=3, alpha=0.9, ls="--",
                       label=f"out outro start {out_outro['arr_time']:.0f}")
        if in_rise:
            ax.axvline(in_rise["arr_time"], color="cyan", lw=3, alpha=0.9, ls=":",
                       label=f"in {in_rise['name']} start {in_rise['arr_time']:.0f}")

    ax_out.legend(loc="upper right", fontsize=8)
    ax_in.set_xlabel("Arrangement beat")
    overlap_bars = (ov_end - ov_start) / 4
    fig.suptitle(f"T{t_index}: overlap {ov_end - ov_start:.0f} beats ({overlap_bars:.0f} bars)",
                 fontsize=12, y=0.98)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def main():
    if len(sys.argv) < 3:
        print("Usage: python transition_review_viz.py <sections.json> <audio_dir> [version]")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    audio_dir = Path(sys.argv[2])
    version = sys.argv[3] if len(sys.argv) >= 4 else "V13"

    with open(json_path, encoding="utf-8") as f:
        sections = json.load(f)
    tracks = list(sections.keys())

    project_dir = json_path.parent.parent
    out_dir = project_dir / "Output" / "Visualisations" / f"Transitions_{version}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Read BPMs from the arrangement report (MIK-enriched, reliable).
    # Prefer the report matching this version, else the most-recent one.
    bpm_lookup = {}
    report_path = project_dir / "Output" / f"ARRANGEMENT_REPORT_{version}.json"
    if not report_path.exists():
        candidates = sorted((project_dir / "Output").glob("ARRANGEMENT_REPORT*.json"),
                            key=lambda p: p.stat().st_mtime)
        report_path = candidates[-1] if candidates else None
    if report_path and report_path.exists():
        with open(report_path) as f:
            rep = json.load(f)
        for t in rep.get("tracks", []):
            bpm_lookup[t["name"]] = t["bpm"]
        print(f"BPMs from: {report_path.name}")

    # Pre-load audio for each track
    print("Loading audio...")
    audio_cache = {}
    bpm_cache = {}
    for name in tracks:
        wav = audio_dir / (name + ".wav")
        if not wav.exists():
            print(f"  MISSING: {name}.wav")
            continue
        print(f"  {name[:50]}")
        y, sr = librosa.load(str(wav), sr=22050, mono=True)
        audio_cache[name] = y
        # Prefer BPM from arrangement report
        if name in bpm_lookup:
            bpm_cache[name] = bpm_lookup[name]
        else:
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            bpm_cache[name] = float(np.atleast_1d(tempo)[0])
        print(f"    BPM: {bpm_cache[name]:.2f}")

    for i in range(len(tracks) - 1):
        out_name = tracks[i]
        in_name = tracks[i + 1]
        if out_name not in audio_cache or in_name not in audio_cache:
            continue
        stem = f"T{i+1:02d}_{out_name[:25].replace('/','_')}_to_{in_name[:25].replace('/','_')}"
        zoom_path = out_dir / f"{stem}_ZOOM.png"
        ctx_path  = out_dir / f"{stem}_FULL.png"
        print(f"\nT{i+1}: {out_name[:40]} -> {in_name[:40]}")
        render_transition(
            out_name, sections[out_name], bpm_cache[out_name], audio_cache[out_name],
            in_name, sections[in_name], bpm_cache[in_name], audio_cache[in_name],
            zoom_path, i + 1,
        )
        print(f"  -> {zoom_path.name}")
        render_transition_full_context(
            out_name, sections[out_name], bpm_cache[out_name], audio_cache[out_name],
            in_name, sections[in_name], bpm_cache[in_name], audio_cache[in_name],
            ctx_path, i + 1,
        )
        print(f"  -> {ctx_path.name}")


if __name__ == "__main__":
    main()
