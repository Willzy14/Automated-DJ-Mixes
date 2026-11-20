"""Draw one section answer-key picture: two tracks at their mix position, proposed section
lines numbered so Sam can tick or correct each one.

Usage:
    python Tools/section_key_picture.py pair1.json out.png
    python Tools/section_key_picture.py --track SECTIONS_STEM_<track>.json --stem-dir <_Stem Analysis> out.png
The --track form draws ONE track straight from a detector result (its sections and
fill_sections): the whole track, plus zooms on the first and last 48 bars (the mix zones).
It also draws the detector's bass strip (signals.bass_state_bar, when present), the real
track end (analysis.duration_sec), the phrase points nearest one minute after the start
and one minute before the end (section lines and fill ends: "almost always where you're
going to be starting the mix"), and any bar-one suspicion. Optional: --title "..."."""
import glob, json, os, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

COL = {"INTRO": "#9ec5f8", "DROP": "#f7b59a", "BREAK": "#cdb4ec", "BUILD": "#fff3b0", "OUTRO": "#a9dfbf"}
LEGEND = list(COL.items()) + [("FILL", "#ff9800"), ("VOCAL (purple)", "#8e24aa"),
                               ("TRACK LEVEL (grey)", "#444444"), ("SECTION LINE (red)", "#c62828")]
LEGEND_SINGLE = [("BASS (blue strip)", "#0d47a1"), ("1 MIN MIX POINT", "#2e7d32")]


MINUTE_TOL_SEC = 15.0   # no marker when no phrase point lies within this of the minute


def mix_points(points_bar, downbeat, spb, end_sec, tol=MINUTE_TOL_SEC):
    """The phrase points (section lines, fill ends) nearest one minute after the
    start and one minute before the REAL end, with the measured time printed:
    [(bar, label)]. A side gets no marker when nothing lies within tol seconds
    of its minute (Reachin: the old marker sat 29 s from the end)."""
    out = []
    if not points_bar:
        return out
    to_sec = lambda b: downbeat + b * spb
    b_in = min(points_bar, key=lambda b: abs(to_sec(b) - 60.0))
    if abs(to_sec(b_in) - 60.0) <= tol:
        ts = to_sec(b_in)
        out.append((b_in, f"MIX POINT, {int(ts // 60)}:{int(ts % 60):02d} IN"))
    b_out = min(points_bar, key=lambda b: abs((end_sec - to_sec(b)) - 60.0))
    left = end_sec - to_sec(b_out)
    if abs(left - 60.0) <= tol:
        out.append((b_out, f"MIX POINT, {int(round(left))} s TO END"))
    return out


def track_from_result(path, stem_dir, key="A"):
    """A picture track dict built from a detector SECTIONS_STEM json."""
    res = json.load(open(path, encoding="utf-8"))
    bpm = float(res["bpm"])
    spb = 240.0 / bpm
    downbeat = float(res.get("analysis", {}).get("downbeat_sec", res["sections"][0]["start_sec"]))
    fills = res["signals"].get("fill_sections")
    if fills is None:
        fills = [{"start_bar": (a - downbeat) / spb, "end_bar": (b - downbeat) / spb}
                 for a, b in res["signals"].get("fills", [])]
    t = {"key": key, "match": res["track"], "name": f"{key}: {res['track']}", "bpm": bpm,
         "n_bars": int(res["n_bars"]), "lead_in_bars": downbeat / spb,
         "sections": [[s["start_bar"], s["label"].upper()] for s in res["sections"]],
         "fills": [[f["start_bar"], f["end_bar"]] for f in fills]}
    dur = res.get("analysis", {}).get("duration_sec")
    if dur:
        t["end_bar"] = (float(dur) - downbeat) / spb
    if res["signals"].get("bass_state_bar") is not None:
        t["bass"] = list(res["signals"]["bass_state_bar"])
    end_sec = float(dur) if dur else downbeat + t["n_bars"] * spb
    points = sorted(set([s["start_bar"] for s in res["sections"][1:]] + [f["end_bar"] for f in fills]))
    t["mix_points"] = mix_points(points, downbeat, spb, end_sec)
    sus = res.get("analysis", {}).get("bar_one_suspect") or {}
    if sus.get("suspect"):
        t["bar_one_note"] = (f"BAR ONE SUSPECT: the grid looks {sus.get('offset_beats')} beat(s) off "
                             f"({sus.get('evidence')}). Lines are drawn on the grid as given.")
    return t


SINGLE = len(sys.argv) > 1 and sys.argv[1] == "--track"
cfg, out = {}, None


def _setup():
    global cfg, out
    if SINGLE:
        global _t
        _a = sys.argv[1:]
        _res, _dir, out = _a[_a.index("--track") + 1], _a[_a.index("--stem-dir") + 1], _a[-1]
        _t = track_from_result(_res, _dir)
        _title = _a[_a.index("--title") + 1] if "--title" in _a else "HOLDOUT (not tuned on): " + _t["match"]
        cfg = {"stem_dir": _dir, "tracks": [_t], "b_starts_at_a_bar": 0, "title": _title}
    else:
        cfg = json.load(open(sys.argv[1], encoding="utf-8"))
        out = sys.argv[2]


def load(t):
    f = [x for x in glob.glob(os.path.join(cfg["stem_dir"], "*__stemenv.npz")) if t["match"] in x][0]
    z = np.load(f, allow_pickle=True)
    hop, bar = float(z["hop_t"]), 240.0 / t["bpm"]
    x = np.arange(len(z["mix"])) * hop / bar - t.get("lead_in_bars", 0)
    norm = lambda a: np.clip(a / np.percentile(a, 99.5), 0, 1)
    ok = x >= 0
    return x[ok], norm(z["mix"])[ok], norm(z["vocals"])[ok], bar


def lane(ax, t, off, y0, big):
    x, mix, voc, bar = load(t)
    end_bar = t.get("end_bar", t["n_bars"])
    keep = x <= end_bar
    x, mix, voc = x[keep] + off, mix[keep], voc[keep]
    secs = t["sections"] + [[end_bar, "END"]]
    for (s, lab), (e, _) in zip(secs[:-1], secs[1:]):
        ax.fill_between([s + off, e + off], y0, y0 + 0.44, color=COL[lab], alpha=0.75, lw=0)
        mid = (s + e) / 2 + off
        if ax.get_xlim()[0] <= mid <= ax.get_xlim()[1] and (e - s) * big >= 7:
            ax.text(mid, y0 + 0.405, lab, ha="center", va="center", fontsize=13 if big > 1 else 10, weight="bold", color="#222")
    base = y0 + 0.03
    for j, (fs, fe) in enumerate(t.get("fills", []), start=1):
        ax.fill_between([fs + off, fe + off], y0, y0 + 0.36, color="#ff9800", alpha=0.9, lw=0, zorder=1)
        fx = (fs + fe) / 2 + off
        if ax.get_xlim()[0] <= fx <= ax.get_xlim()[1]:
            sec = (fs + t.get("lead_in_bars", 0)) * bar
            ax.text(fx, y0 + 0.37 if big == 1 else y0 - 0.075, (f"F{j}" + chr(10) + f"{int(sec // 60)}:{int(sec % 60):02d}" if SINGLE else f"F{j}") if big == 1 else f"FILL {t['key']}-F{j}\nbar {fs + 1:g}, {int(sec // 60)}:{int(sec % 60):02d}", ha="center", va="bottom" if big == 1 else "top",
                    fontsize=8 if big == 1 else 10.5, weight="bold", color="#b34700", linespacing=0.95)
    hb = t.get("bass")
    if hb:
        run = None
        for bi in range(len(hb) + 1):
            on = bi < len(hb) and hb[bi]
            if on and run is None:
                run = bi
            if not on and run is not None:
                ax.fill_between([run + off, bi + off], y0 - 0.025, y0 - 0.002, color="#0d47a1", lw=0, zorder=4)
                run = None
    for mp, txt in t.get("mix_points", []):
        xs = mp + off
        if ax.get_xlim()[0] <= xs <= ax.get_xlim()[1]:
            ms = (mp + t.get("lead_in_bars", 0)) * bar
            ax.plot([xs, xs], [y0 - 0.03, y0 + 0.52], color="#2e7d32", lw=2.6 if big > 1 else 1.8, ls="--", zorder=6)
            ax.text(xs, y0 + 0.56, f"{txt}" + chr(10) + f"bar {mp + 1:g}, {int(ms // 60)}:{int(ms % 60):02d}", ha="center", va="bottom",
                    fontsize=10 if big > 1 else 7.5, weight="bold", color="#2e7d32", zorder=7,
                    bbox=dict(fc="white", ec="none", alpha=0.85, pad=0.5))
    end = end_bar + off
    if ax.get_xlim()[0] <= end <= ax.get_xlim()[1]:
        es = (end_bar + t.get("lead_in_bars", 0)) * bar if SINGLE else t["n_bars"] * bar
        ax.text(end + 0.3, y0 + 0.22, f"END\n{int(es // 60)}:{int(es % 60):02d}", ha="left", va="center", fontsize=9 if big == 1 else 12, weight="bold")
    if t["key"] == "A" and cfg.get("a_cut_at") is not None:
        ax.fill_between([cfg["a_cut_at"], t["n_bars"]], y0, y0 + 0.44, facecolor="white", alpha=0.6, edgecolor="#555", hatch="xx", lw=1, zorder=3)
        if ax.get_xlim()[0] <= cfg["a_cut_at"] <= ax.get_xlim()[1]:
            ax.text((cfg["a_cut_at"] + t["n_bars"]) / 2, y0 + 0.22, "NOT USED" if big == 1 else "NOT USED"+chr(10)+"(A stops here)", ha="center", va="center",
                    fontsize=9 if big == 1 else 13, weight="bold", color="#333", zorder=8, bbox=dict(fc="white", ec="none", alpha=0.8))
    if t["key"] == "A" and cfg.get("a_loop_to") and big > 1:
        ax.fill_between([t["n_bars"], cfg["a_loop_to"]], y0, y0 + 0.44, facecolor="none", edgecolor="#2e7d32", hatch="//", lw=1.5)
        ax.text((t["n_bars"] + cfg["a_loop_to"]) / 2, y0 + 0.46, "outro beats\nlooped to here", ha="center", va="bottom", fontsize=10.5, color="#2e7d32", weight="bold")
    ax.fill_between(x, base, base + mix * 0.30, color="#444", lw=0)
    ax.fill_between(x, base, base + voc * 0.12, color="#8e24aa", alpha=0.85, lw=0)
    for i, (s, lab) in enumerate(t["sections"][1:], start=1):
        xs = s + off
        if not (ax.get_xlim()[0] <= xs <= ax.get_xlim()[1]):
            continue
        ax.plot([xs, xs], [y0, y0 + 0.44], color="#c62828", lw=2.2)
        sec = (s + t.get("lead_in_bars", 0)) * bar
        ax.scatter([xs], [y0 + 0.485], s=1500 if big > 1 else 620, color="#c62828", zorder=5, clip_on=False)
        ax.text(xs, y0 + 0.485, f"{t['key']}{i}", ha="center", va="center", fontsize=15 if big > 1 else 10, weight="bold",
                color="white", zorder=6)
        ax.text(xs, y0 - (0.032 if SINGLE else 0.012), f"bar {s + 1}\n{int(sec // 60)}:{int(sec % 60):02d}", ha="center", va="top",
                fontsize=10 if big > 1 else 7.5, color="#111", linespacing=0.95)
    ax.text(ax.get_xlim()[0] + 0.5, y0 + 0.22, t["key"], ha="right", va="center", fontsize=26, weight="bold", color="#c62828")


def panel(ax, lo, hi, big, title):
    ax.set_xlim(lo, hi); ax.set_ylim(-0.17, 1.30); ax.axis("off")
    a, b = cfg["tracks"]
    step = 8
    for g in range(int(lo // step) * step, int(hi) + 1, step):
        if g >= lo:
            ax.plot([g, g], [-0.04, 1.15], color="#999", lw=0.9 if g % 16 else 1.4, alpha=0.55, zorder=0)
    lane(ax, a, 0, 0.70, big)
    lane(ax, b, cfg["b_starts_at_a_bar"], 0.0, big)
    sw = cfg.get("switch_at")
    if sw is not None and lo <= sw <= hi:
        ax.plot([sw, sw], [-0.04, 1.15], color="#1565c0", lw=3 if big > 1 else 2, ls="--", zorder=4)
        ax.text(sw, 0.555, "BASS SWITCH", ha="center", va="center", fontsize=13 if big > 1 else 8, weight="bold", color="white",
                bbox=dict(boxstyle="round,pad=0.3", fc="#1565c0", ec="none"), zorder=7)
    ax.text(lo, 1.30, title, fontsize=14, weight="bold", va="top")


def single_panel(ax, lo, hi, big, title):
    ax.set_xlim(lo, hi); ax.set_ylim(-0.17, 0.80); ax.axis("off")
    step = 8
    for g in range(int(lo // step) * step, int(hi) + 1, step):
        if g >= lo:
            ax.plot([g, g], [-0.04, 0.6], color="#999", lw=0.9 if g % 16 else 1.4, alpha=0.55, zorder=0)
    lane(ax, cfg["tracks"][0], 0, 0.05, big)
    ax.text(lo, 0.80, title, fontsize=14, weight="bold", va="top")


def _main():
    _setup()
    fig = plt.figure(figsize=(24, 13), dpi=100)
    if SINGLE:
        t0 = cfg["tracks"][0]
        nb = int(np.ceil(t0.get("end_bar", t0["n_bars"])))
        ax1 = fig.add_axes([0.03, 0.50, 0.96, 0.43])
        single_panel(ax1, -8, nb + 4, 1, "WHOLE TRACK   (grey lines every 8 bars)")
        ax2 = fig.add_axes([0.03, 0.05, 0.47, 0.42])
        single_panel(ax2, -2, min(nb, 48), 2, "ZOOM: FIRST 48 BARS (mix-in zone)")
        ax3 = fig.add_axes([0.52, 0.05, 0.47, 0.42])
        single_panel(ax3, max(0, nb - 48), nb + 3, 2, "ZOOM: LAST 48 BARS (mix-out zone)")
    else:
        total = cfg["b_starts_at_a_bar"] + cfg["tracks"][1]["n_bars"]
        ax1 = fig.add_axes([0.03, 0.50, 0.96, 0.43])
        panel(ax1, -8, total + 2, 1, "WHOLE OF BOTH TRACKS, placed as in the mix.  A = " + cfg["tracks"][0]["name"][3:] + "   B = " + cfg["tracks"][1]["name"][3:] + "   (grey lines every 8 bars, purple = vocal)")
        ax2 = fig.add_axes([0.03, 0.04, 0.96, 0.43])
        panel(ax2, cfg["zoom"][0], cfg["zoom"][1], 3, "ZOOM ON THE BLEND")
    fig.text(0.012, 0.965, cfg["title"], fontsize=20, weight="bold")
    fig.text(0.988, 0.938 if SINGLE else 0.968,
             "Each red numbered line is where I say a section starts. Tick it, or tell me the right bar.   "
             "Bar numbers count from 1 at the track's first beat, as Ableton does.", fontsize=11.5, ha="right")
    if SINGLE and cfg["tracks"][0].get("bar_one_note"):
        fig.text(0.012, 0.938, cfg["tracks"][0]["bar_one_note"], fontsize=11.5, color="#b00020", weight="bold")
    if SINGLE:
        legend = LEGEND + LEGEND_SINGLE
        step = 0.98 / len(legend)
        for i, (k, c) in enumerate(legend):
            fig.patches.append(plt.Rectangle((0.012 + i * step, 0.008), 0.010, 0.016, color=c, transform=fig.transFigure))
            fig.text(0.025 + i * step, 0.016, k, fontsize=9.5, va="center")
    else:   # the original pair legend, unchanged
        for i, (k, c) in enumerate(list(COL.items()) + [("FILL", "#ff9800")]):
            fig.patches.append(plt.Rectangle((0.012 + i * 0.07, 0.008), 0.014, 0.018, color=c, transform=fig.transFigure))
            fig.text(0.030 + i * 0.07, 0.017, k, fontsize=11, va="center")
    fig.savefig(out, facecolor="white")
    print("wrote", out)


if __name__ == "__main__":
    _main()
