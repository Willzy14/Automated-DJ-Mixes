"""Extract per-transition excerpts from N compared mixes and seal them blind.

Burn list A4 (2026-09-14, Astra's second finding): `seal_listening_test.py`
randomises whatever audio it's given, but does NOT extract the transition
excerpts itself. `Heldout Replay Plan V2.md`'s listening-test spec calls for
"Whole-mix pair plus randomized per-transition excerpts" - the whole-mix half
already works (call `seal_listening_test.py` directly on the full renders);
this script is the missing per-transition half.

For each side (A/B/C, ...) built by `build_ab_comparison.py`, this reads that
side's OWN `Arranged {side}_ARRANGEMENT_REPORT.json` (transition geometry)
and OWN `Mix {side}.als` (ground-truth tempo, read the same way burn list A4
reads it - straight off the ALS, never re-derived) - each side's overlap
zone for the same transition can genuinely differ, since that is the entire
point of the comparison. For each transition, it computes every side's
excerpt window, EQUALISES their durations (Codex review, 2026-09-14 - see
`_equalize_windows_sec`; an unequal clip length is itself a blind-breaking
tell, real numbers proved it), slices the matching window out of every
side's rendered WAV, then hands those raw clips to `seal_listening_test.py`
as a subprocess to randomise/blind/twin them - no sealing logic is
duplicated here. Raw clips live only in a throwaway temp directory under
opaque (non-side-disclosing) filenames, deleted immediately after sealing;
nothing under `--out-dir` ever discloses which side is which. That is the
same "blind means blind" principle `seal_listening_test.py` documents for
itself.

The excerpt window is the transition's overlap zone (incoming track's
arrangement start -> outgoing track's arrangement end) plus `--context-bars`
bars of pad on each side - the exact convention already established and
reviewed in `transition_review_viz.render_transition` (`ctx_bars = 8`).

Usage:
    python Source/extract_transition_excerpts.py \\
        --ab-root "<project>/Output/AB" \\
        --side A="<...>/Mix A (date).wav" \\
        --side B="<...>/Mix B (date).wav" \\
        --side C="<...>/Mix C (date).wav" \\
        --twin-of A --seed 12345 \\
        --out-dir "<project>/Output/AB/_listening_test/Transitions" \\
        [--context-bars 8] [--build-results "<...>/_audit/build_results.json"]
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import soundfile as sf

SOURCE_DIR = Path(__file__).parent
sys.path.insert(0, str(SOURCE_DIR))

from render_check import TempoAutomationUnsupported, parse_als  # noqa: E402

BEATS_PER_BAR = 4.0  # this project is 4/4 throughout - same assumption
                     # transition_review_viz.py and align_engine.py already make.
DEFAULT_CONTEXT_BARS = 8.0  # matches transition_review_viz.render_transition's ctx_bars
DEFAULT_DURATION_TOLERANCE = 0.10  # see _sanity_check_wav_duration
DEFAULT_SEAL_TIMEOUT_SEC = 600.0

# Prefix for this script's OWN throwaway per-run temp roots (opaque UUID
# filenames live inside them, never a side-bearing name - Codex round-1
# MAJOR-4, 2026-09-14). NO automatic cross-run cleanup runs against this
# prefix: an earlier version swept every matching directory at every
# invocation's startup, first unconditionally (a live regression - it could
# delete a CONCURRENT invocation's still-in-use clips, Codex round-2 MAJOR-2)
# and then only past a 1-hour age threshold (Codex round-3 MAJOR-2: age does
# not prove inactivity - a genuinely slow run past that threshold would still
# get its live directory deleted by another invocation's cleanup; the
# "safest" fix Codex offered was to drop automatic cross-run cleanup
# entirely rather than build a real ownership/lock mechanism for a
# hygiene-only feature). Adopted: this constant now exists only so the
# opaque-filename prefix is defined once; nothing sweeps directories by it.
# A leftover directory from a killed run is left for the OS's own temp
# cleanup - its opaque filenames disclose nothing regardless of how long
# they sit there, which is the property that actually matters.
TEMP_DIR_PREFIX = "extract_transition_excerpts_run_"


def _parse_side(spec: str) -> tuple[str, Path]:
    if "=" not in spec:
        raise argparse.ArgumentTypeError(f"--side must be LABEL=path, got: {spec!r}")
    label, _, path = spec.partition("=")
    label = label.strip()
    if not label:
        raise argparse.ArgumentTypeError(f"--side has an empty label: {spec!r}")
    return label, Path(path)


def _select_transitions(report: dict) -> dict[int, dict]:
    """{pair_index: {"out_track": ..., "in_track": ...}} from one side's
    ARRANGEMENT_REPORT. `tracks[]` is written in running order, so pair_index
    i connects tracks[i-1] to tracks[i] by position - verified against the
    transition's own out_track/in_track names as a safety net. A mismatch
    means the report's own tracks-order invariant broke; this refuses to
    guess rather than pairing the wrong two tracks. A duplicate pair_index
    is refused rather than silently overwriting the first occurrence
    (Codex MINOR-6, 2026-09-14)."""
    tracks = report["tracks"]
    result: dict[int, dict] = {}
    for t in report["transitions"]:
        idx = t["pair_index"]
        if idx in result:
            raise ValueError(
                f"duplicate pair_index {idx} in transitions[] - refusing to "
                f"silently overwrite the first occurrence")
        if idx < 1 or idx > len(tracks) - 1:
            raise ValueError(
                f"pair_index {idx} is out of range for {len(tracks)} tracks")
        out_track = tracks[idx - 1]
        in_track = tracks[idx]
        if out_track["name"] != t["out_track"] or in_track["name"] != t["in_track"]:
            raise ValueError(
                f"pair_index {idx}: tracks[] order does not match "
                f"transitions[] out_track/in_track - report invariant "
                f"broken, refusing to guess which tracks this transition is "
                f"between")
        result[idx] = {"out_track": out_track, "in_track": in_track}
    return result


def _verify_cross_side_transition_identity(side_data: dict, labels: list[str],
                                           pair_indices) -> None:
    """Cross-side identity check (Codex MAJOR-2, 2026-09-14): sides sharing
    the same SET of pair_index values only proves they agree on how many
    transitions there are and how they're numbered - not that pair_index N
    names the same out_track -> in_track pair on every side. Each side's own
    `_select_transitions` already checks internal self-consistency (its
    transitions[] agree with its own tracks[] order); this checks that the
    same pair_index names the SAME two tracks across every side, since
    `build_ab_comparison.py`'s own contract is "same sections ALS, section
    map, tracks, and running order" for every side it builds. A violation
    here means that contract broke somewhere upstream, and sealing
    mismatched transitions together under one shared listening round would
    silently produce a meaningless comparison."""
    reference_label = labels[0]
    for idx in pair_indices:
        reference = side_data[reference_label]["transitions"][idx]
        ref_pair = (reference["out_track"]["name"], reference["in_track"]["name"])
        for label in labels[1:]:
            other = side_data[label]["transitions"][idx]
            other_pair = (other["out_track"]["name"], other["in_track"]["name"])
            if other_pair != ref_pair:
                raise ValueError(
                    f"pair_index {idx} names different tracks across sides - "
                    f"{reference_label!r} has {ref_pair}, {label!r} has "
                    f"{other_pair} - sides do not share the same running "
                    f"order, refusing to seal these together")


def _check_wav_duration_for_side(d: dict, label: str, tolerance: float) -> None:
    """The WEAK binding check: compares one side's actual rendered duration
    against what that side's OWN arrangement report predicts (its last
    track's arr_end, converted via that side's own tempo map), refusing if
    they disagree by more than `tolerance`. Only catches a GROSSLY wrong
    file - a same-length A/B swap (the realistic mistake for comparison
    variants built from the same tracks) passes this cleanly (Codex MAJOR-1,
    2026-09-14). Kept as the fallback for a side with no bounce manifest -
    see `_verify_side_binding`, which is what actually decides whether this
    runs for a given side."""
    last_arr_end = max(t["arr_end"] for t in d["report"]["tracks"])
    expected_sec = d["tempo_map"].beat_to_sec(last_arr_end)
    actual_sec = d["wav_duration_sec"]
    if expected_sec <= 0:
        return
    rel_diff = abs(actual_sec - expected_sec) / expected_sec
    if rel_diff > tolerance:
        raise ValueError(
            f"side {label!r}'s rendered WAV is {actual_sec:.1f}s but its "
            f"own arrangement report predicts {expected_sec:.1f}s "
            f"({rel_diff:.0%} off, tolerance {tolerance:.0%}) - possible "
            f"wrong/swapped --side file, refusing to extract from it")


def _sanity_check_wav_duration(side_data: dict, labels: list[str],
                               tolerance: float) -> None:
    """Run the weak duration check (see `_check_wav_duration_for_side`) for
    every side unconditionally - used directly by callers that want it on
    its own, without the manifest tier. `main()` itself calls
    `_verify_side_binding` instead, which uses the strong bounce-manifest
    check per side where one is available."""
    for label in labels:
        _check_wav_duration_for_side(side_data[label], label, tolerance)


def _verify_bounce_manifest(manifest_path: Path, als_path: Path,
                            report_path: Path, wav_path: Path) -> None:
    """The STRONG binding check (Codex MAJOR-1, 2026-09-14): verifies a
    `record_bounce_manifest.py` manifest against what is actually on disk
    right now. Proves byte-for-byte that the ALS, report, and WAV this run
    is about to use are exactly the three files that were hashed together
    at the moment the manifest was recorded - not merely "plausibly the
    right length," which is all the duration check can ever offer. It
    cannot prove a human recorded the RIGHT file in the first place; only
    that whatever was recorded has not silently drifted since. Raises on
    any mismatch.

    TOCTOU WINDOW (Codex round 3 MINOR, sharpened round 4, 2026-09-14): this
    hashes the WAV once, here, near the top of `main()` - the same file is
    then reopened and re-read from disk separately for EACH transition's
    actual slice, later in the run (`extract_excerpt`). A file replaced on
    disk in between those two moments would be verified against one set of
    bytes and sliced from another. Full re-hashing before every transition
    was rejected as real, avoidable cost for a long mix (hundreds of MB,
    hashed up to N times); round 3 initially argued a corrupted run from
    this would be audibly/visibly broken and so self-evident - round 4
    correctly called that overstated: a plausible but provenance-invalid
    substitute WAV would NOT necessarily sound broken. The cheap mitigation
    Codex round 4 suggested closes the actual accidental-replacement case
    without repeated full-file hashing: `_wav_identity` below captures
    (mtime, size) alongside this hash check, and the caller
    (`_verify_side_binding` / `main()`'s extraction loop) rechecks that
    identity immediately before each transition's slice, aborting that
    transition if it changed. This is NOT cryptographic - a same-mtime,
    same-size replacement would still slip through - only a stat check, but
    it catches the normal, accidental case (a re-bounce, a sync overwrite)
    at negligible cost."""
    from record_bounce_manifest import sha256_file

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for field, path in (("als", als_path), ("report", report_path), ("wav", wav_path)):
        expected = manifest.get(f"{field}_sha256")
        if not expected:
            raise ValueError(f"{manifest_path} is missing {field}_sha256")
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(
                f"{manifest_path} recorded {field}_sha256={expected} for "
                f"side {manifest.get('side')!r}, but {path} currently "
                f"hashes to {actual} - the file has changed since the "
                f"manifest was recorded, or this is not the file it was "
                f"recorded for. Refusing to extract from this side.")


def _wav_identity(path: Path) -> tuple[float, int]:
    """(mtime, size) - a cheap, non-cryptographic fingerprint used only to
    detect an ACCIDENTAL mid-run replacement of an already strongly-bound
    WAV (Codex round 4, 2026-09-14). Not a substitute for the sha256 binding
    check itself - see `_verify_bounce_manifest`'s TOCTOU note."""
    st = path.stat()
    return (st.st_mtime, st.st_size)


def _check_wav_identity_unchanged(d: dict, label: str) -> None:
    """If this side was strongly bound (`d["bound_wav_identity"]` set by
    `_verify_side_binding`), reconfirm the WAV's (mtime, size) hasn't
    changed since verification, right before slicing it for one transition.
    A no-op for a side with no recorded identity (never bound, or weakly
    bound - nothing to compare against)."""
    recorded = d.get("bound_wav_identity")
    if recorded is None:
        return
    current = _wav_identity(d["wav"])
    if current != recorded:
        raise ValueError(
            f"side {label!r}'s WAV changed since it was verified against "
            f"its bounce manifest (mtime/size went from {recorded} to "
            f"{current}) - refusing to slice from it now")


def _verify_side_binding(side_data: dict, labels: list[str], ab_root: Path,
                         tolerance: float, require_manifests: bool = False
                         ) -> dict[str, str]:
    """Per side: verify the STRONG bounce-manifest binding if
    `record_bounce_manifest.py` was run for it (a
    `Mix {label}.bounce_manifest.json` sits next to its ALS), else fall back
    to the WEAK duration-sanity heuristic - printing a visible WARNING so a
    weakly-bound side is never silently treated as equally trustworthy as a
    manifest-verified one.

    Returns `{label: "strong" | "weak"}` so the caller can record and
    SURFACE which binding tier each side actually got - not just at this
    per-side warning, but in the run's own results/summary, so a run that
    fell back for some sides never looks identical to a fully manifest-
    verified one (Codex round 3, 2026-09-14: round 2's fix made the fallback
    silent-but-for-a-warning, which meant a completed run's own exit code
    and results file carried no trace of which sides were actually strongly
    bound - "not auditable against the exact failure the manifest was
    introduced to prevent," in Codex's words).

    `require_manifests=True` (CLI `--require-bounce-manifests`) makes a
    missing manifest a hard SystemExit instead of a fallback - the stricter
    opt-in Codex also offered as an acceptable disposition. Off by default
    because a manifest's value depends entirely on being recorded AT BOUNCE
    TIME (the one moment a human still knows for certain which file they
    just exported); making it mandatory does not by itself buy back that
    property if the workflow around it isn't followed, so this stays an
    explicit choice rather than a silently-stricter default that could
    surprise an existing caller."""
    binding: dict[str, str] = {}
    for label in labels:
        d = side_data[label]
        side_dir = ab_root / label
        manifest_path = side_dir / f"Mix {label}.bounce_manifest.json"
        if manifest_path.is_file():
            _verify_bounce_manifest(
                manifest_path, side_dir / f"Mix {label}.als",
                side_dir / f"Arranged {label}_ARRANGEMENT_REPORT.json",
                d["wav"])
            binding[label] = "strong"
            d["bound_wav_identity"] = _wav_identity(d["wav"])
        elif require_manifests:
            raise SystemExit(
                f"--require-bounce-manifests is set but side {label!r} has "
                f"no bounce manifest at {manifest_path} - run "
                f"record_bounce_manifest.py for every side first, or drop "
                f"--require-bounce-manifests to accept the weaker fallback")
        else:
            print(f"  WARNING: side {label!r} has no bounce manifest "
                 f"(run record_bounce_manifest.py right after bouncing next "
                 f"time) - falling back to the weaker duration-sanity check, "
                 f"which cannot catch a same-length swap")
            _check_wav_duration_for_side(d, label, tolerance)
            binding[label] = "weak"
    return binding


def _transition_window_beats(out_track: dict, in_track: dict,
                             context_bars: float) -> tuple[float, float]:
    """(view_start_beats, view_end_beats) for one transition's excerpt
    window: the overlap zone (incoming track's arrangement start -> outgoing
    track's arrangement end) plus `context_bars` bars of pad each side - the
    same convention `transition_review_viz.render_transition` already uses
    for the identical purpose. The start is clamped so it never goes
    negative; the end is left uncapped here and clamped against the actual
    render's duration later. `context_bars` must be finite and non-negative
    (Codex MINOR-5, 2026-09-14) - a negative value would silently shrink the
    intended window rather than fail."""
    ov_start = float(in_track["arr_start"])
    ov_end = float(out_track["arr_end"])
    if ov_end <= ov_start:
        raise ValueError(
            f"overlap zone is empty or inverted: in_track arr_start "
            f"{ov_start} >= out_track arr_end {ov_end}")
    context_bars = float(context_bars)
    if not math.isfinite(context_bars) or context_bars < 0:
        raise ValueError(
            f"context_bars must be finite and >= 0, got {context_bars}")
    pad_beats = context_bars * BEATS_PER_BAR
    view_start = max(0.0, ov_start - pad_beats)
    view_end = ov_end + pad_beats
    return view_start, view_end


def resolve_tempo_map(als_path: Path):
    """Ground-truth beat -> seconds map for one side, read straight off the
    Mix ALS's own tempo - the same "read what was actually written, never
    re-derive it" principle burn list A4's
    `_resolve_inherited_tempo_and_warp_modes` established for warp mode.
    `render_check.parse_als` already handles a flat Manual tempo AND a
    genuine MainTrack tempo automation envelope (a real ramp converts
    correctly via its piecewise beat<->time map - `build_ab_comparison.py`
    never produces one today, but a production mix with a tempo arc would
    still extract correctly here). It raises `TempoAutomationUnsupported`
    only when the tempo genuinely cannot be read unambiguously - multiple
    envelopes claiming the tempo target, an unidentifiable envelope that
    might be the tempo one, or a non-finite/out-of-range value - and that
    exception is left to propagate here: one side's tempo being unreadable
    means NONE of that side's excerpts can be trusted, for any transition,
    so the whole run aborts rather than silently proceeding on 2 of 3
    sides."""
    tempo_map, _clips = parse_als(als_path)
    return tempo_map


def _wav_duration_sec(wav_path: Path) -> float:
    info = sf.info(str(wav_path))
    return info.frames / info.samplerate


def _equalize_windows_sec(windows: dict[str, tuple[float, float]],
                          bounds: dict[str, tuple[float, float]]
                          ) -> dict[str, tuple[float, float]]:
    """Force every side's excerpt window to the SAME total duration for one
    transition - Codex FATAL-1, 2026-09-14: differing per-side overlap-zone
    lengths (the actual policy difference this listening test exists to
    surface) meant differing CLIP DURATIONS, and a clip's length is
    trivially audible/visible even with a randomised filename and stripped
    metadata. Real project data proved it live: transition 1's side C clip
    came out 147.69s against 103.38s for A/B - a 44-second gap that would
    have identified side C before a single note played.

    The overlap zone itself is never cropped - that IS the content being
    judged, and cropping it would silently hide exactly the difference the
    test exists to surface. Instead every side SHORTER than the longest is
    extended symmetrically with more real, already-rendered audio from
    before/after its own natural window, clamped to that side's own file
    bounds. No side is ever padded with silence - silence has its own
    audible onset and would be exactly as disclosing as an unequal
    duration. If a side cannot be extended far enough to match without
    running past its own file's bounds, this raises rather than serve
    mismatched-duration clips for that transition - fail closed, matching
    every other gate in this pipeline."""
    if not windows:
        return {}
    target = max(end - start for start, end in windows.values())
    result: dict[str, tuple[float, float]] = {}
    for label, (start, end) in windows.items():
        deficit = target - (end - start)
        if deficit <= 1e-9:
            result[label] = (start, end)
            continue
        lo, hi = bounds[label]
        half = deficit / 2.0
        new_start = start - half
        new_end = end + half
        if new_start < lo:
            new_end += (lo - new_start)
            new_start = lo
        if new_end > hi:
            new_start -= (new_end - hi)
            new_end = hi
        new_start = max(lo, new_start)
        new_end = min(hi, new_end)
        if (new_end - new_start) + 1e-6 < target:
            raise ValueError(
                f"side {label!r} cannot be extended to match the other "
                f"sides' duration ({target:.2f}s) without exceeding its own "
                f"render's bounds [{lo:.2f}s, {hi:.2f}s] - refusing to serve "
                f"mismatched-duration clips for this transition")
        result[label] = (new_start, new_end)
    return result


def extract_excerpt(wav_path: Path, start_sec: float, end_sec: float,
                    out_path: Path) -> None:
    """Slice [start_sec, end_sec) out of a rendered WAV and write it as a
    fresh file - decoded and re-written, so no chunk of the source WAV's
    metadata survives (same principle as
    `seal_listening_test._write_clean_copy`, applied here too since these
    raw clips are handed straight to that script next). Takes SECONDS, not
    beats - callers convert via a tempo map and equalise durations first
    (`_equalize_windows_sec`) so every side's clip for one transition comes
    out the same length; this function only knows how to cut one file."""
    with sf.SoundFile(str(wav_path)) as f:
        sr = f.samplerate
        subtype = f.subtype
        total_frames = len(f)
        start_frame = max(0, int(round(start_sec * sr)))
        end_frame = min(total_frames, int(round(end_sec * sr)))
        if end_frame <= start_frame:
            raise ValueError(
                f"excerpt window [{start_sec:.2f}s, {end_sec:.2f}s) is empty "
                f"after clamping to {wav_path.name}'s {total_frames / sr:.2f}s "
                f"duration")
        f.seek(start_frame)
        data = f.read(frames=end_frame - start_frame, always_2d=True)
    sf.write(str(out_path), data, sr, subtype=subtype, format="WAV")


def _check_build_results(build_results_path: Path, labels: list[str]) -> None:
    """Optional gate matching Plan V2's own requirement: "All automated
    checks (ALS gate, MixPlan reconciliation, report binding) pass before
    anything is heard." If `--build-results` is given (the
    `_audit/build_results.json` `build_ab_comparison.py` already writes),
    refuse to extract from any side that isn't `stage: complete, ok: true`.
    Optional and skipped entirely when omitted, so this script still works
    standalone against an arbitrary ALS/WAV/report triple that never went
    through `build_ab_comparison.py`."""
    results = json.loads(build_results_path.read_text(encoding="utf-8"))
    bad = []
    for label in labels:
        entry = results.get(label)
        if not entry or not entry.get("ok") or entry.get("stage") != "complete":
            bad.append((label, entry.get("stage") if entry else "missing"))
    if bad:
        detail = ", ".join(f"{label} ({stage})" for label, stage in bad)
        raise SystemExit(
            f"refusing to extract: {detail} did not pass build_ab_comparison.py's "
            f"own gates (arrange/automation/reconcile) per {build_results_path} - "
            f"Plan V2 requires all automated checks to pass before anything is heard")


def run(cmd: list[str], log: Path, timeout_sec: float) -> tuple[int, str]:
    """Run one subprocess, capturing output to `log`. `timeout_sec` bounds a
    hung child (Codex MAJOR-4, 2026-09-14: `subprocess.run` had no timeout,
    so a hung `seal_listening_test.py` blocked this process indefinitely,
    and the only way out was an external hard kill - which skips Python's
    own `finally`/context-manager cleanup and can leave raw clips on disk).
    A timeout is reported the same way as any other seal failure (see the
    caller) rather than crashing the whole run."""
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout_sec)
    except subprocess.TimeoutExpired as e:
        partial = (e.stdout or "") + "\n" + (e.stderr or "")
        log.write_text(f"TIMED OUT after {timeout_sec}s\n{partial}", encoding="utf-8")
        return -1, f"timed out after {timeout_sec}s"
    log.write_text((proc.stdout or "") + "\n" + (proc.stderr or ""),
                   encoding="utf-8")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ab-root", type=Path, required=True, dest="ab_root",
                        help="Output/AB directory build_ab_comparison.py wrote "
                             "(supplies each side's Mix {side}.als and "
                             "Arranged {side}_ARRANGEMENT_REPORT.json)")
    parser.add_argument("--side", action="append", type=_parse_side,
                        dest="sides", required=True,
                        help="LABEL=path to that side's rendered whole-mix WAV, "
                             "repeatable, at least 2 required")
    parser.add_argument("--twin-of", required=True, dest="twin_of",
                        help="label of the side to duplicate as the noise-twin "
                             "control, forwarded to seal_listening_test.py")
    parser.add_argument("--seed", type=int, required=True,
                        help="base seed; transition N is sealed with seed + N, "
                             "so the whole run is reproducible for audit")
    parser.add_argument("--out-dir", type=Path, required=True, dest="out_dir")
    parser.add_argument("--context-bars", type=float, default=DEFAULT_CONTEXT_BARS,
                        dest="context_bars")
    parser.add_argument("--duration-tolerance", type=float,
                        default=DEFAULT_DURATION_TOLERANCE, dest="duration_tolerance",
                        help="fractional tolerance for the WAV-vs-report duration "
                             "sanity check (default 0.10 = 10%%)")
    parser.add_argument("--seal-timeout-sec", type=float,
                        default=DEFAULT_SEAL_TIMEOUT_SEC, dest="seal_timeout_sec")
    parser.add_argument("--build-results", type=Path, dest="build_results",
                        help="optional _audit/build_results.json from "
                             "build_ab_comparison.py - if given, refuses to "
                             "extract from any side that didn't pass its gates")
    parser.add_argument("--require-bounce-manifests", action="store_true",
                        dest="require_bounce_manifests",
                        help="refuse any side that has no record_bounce_manifest.py "
                             "manifest, instead of falling back to the weaker "
                             "duration-sanity check")
    args = parser.parse_args()

    if len(args.sides) < 2:
        raise SystemExit(f"need at least 2 --side entries, got {len(args.sides)}")
    labels = [label for label, _ in args.sides]
    if len(set(labels)) != len(labels):
        raise SystemExit(f"--side labels must be unique, got: {labels}")
    by_label_wav = dict(args.sides)
    if args.twin_of not in by_label_wav:
        raise SystemExit(
            f"--twin-of {args.twin_of!r} is not one of the --side labels: {labels}")
    for label, wav_path in args.sides:
        if not wav_path.is_file():
            raise SystemExit(f"missing render for side {label!r}: {wav_path}")

    if args.build_results is not None:
        _check_build_results(args.build_results, labels)

    # Load each side's report + tempo map + wav duration once. A tempo map
    # that cannot be resolved unambiguously (TempoAutomationUnsupported) is
    # allowed to propagate and abort the whole run - see resolve_tempo_map's
    # docstring.
    side_data: dict[str, dict] = {}
    for label, wav_path in args.sides:
        side_dir = args.ab_root / label
        report_path = side_dir / f"Arranged {label}_ARRANGEMENT_REPORT.json"
        als_path = side_dir / f"Mix {label}.als"
        if not report_path.is_file():
            raise SystemExit(f"missing arrangement report for side {label!r}: {report_path}")
        if not als_path.is_file():
            raise SystemExit(f"missing final ALS for side {label!r}: {als_path}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        tempo_map = resolve_tempo_map(als_path)
        transitions = _select_transitions(report)
        side_data[label] = {
            "wav": wav_path, "tempo_map": tempo_map, "transitions": transitions,
            "report": report, "wav_duration_sec": _wav_duration_sec(wav_path),
        }

    # Every side must offer the exact same set of transitions - without that,
    # there is no sensible way to pair "transition 3 on A" with "transition 3
    # on B/C" for a single listening round.
    pair_index_sets = {label: set(d["transitions"]) for label, d in side_data.items()}
    reference_label = labels[0]
    reference_set = pair_index_sets[reference_label]
    mismatched = {label: idx_set for label, idx_set in pair_index_sets.items()
                 if idx_set != reference_set}
    if mismatched:
        raise SystemExit(
            f"sides do not share the same transitions - cannot pair them for "
            f"a listening round: {reference_label} has {sorted(reference_set)}, "
            f"mismatched sides: "
            f"{ {label: sorted(s) for label, s in mismatched.items()} }")

    if not reference_set:
        print("No transitions to extract (single-track mix) - nothing to do.")
        return 0

    # Matching pair_index SETS (above) is not enough - verify every side
    # actually agrees on which two tracks pair_index N names (Codex MAJOR-2).
    _verify_cross_side_transition_identity(side_data, labels, reference_set)
    binding = _verify_side_binding(side_data, labels, args.ab_root,
                                   args.duration_tolerance,
                                   require_manifests=args.require_bounce_manifests)
    weak_sides = sorted(label for label, tier in binding.items() if tier == "weak")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    audit_dir = args.out_dir / "_audit"
    results: dict[str, dict] = {}

    for pair_index in sorted(reference_set):
        t_label = f"T{pair_index:02d}"
        print(f"\n=== transition {t_label} ===")
        with tempfile.TemporaryDirectory(prefix=TEMP_DIR_PREFIX) as raw_dir_str:
            raw_dir = Path(raw_dir_str)
            seal_cmd = [
                sys.executable, str(SOURCE_DIR / "seal_listening_test.py"),
                "--twin-of", args.twin_of,
                "--out-dir", str(args.out_dir / t_label),
                "--seed", str(args.seed + pair_index),
            ]
            try:
                windows_sec: dict[str, tuple[float, float]] = {}
                bounds_sec: dict[str, tuple[float, float]] = {}
                for label in labels:
                    d = side_data[label]
                    out_track = d["transitions"][pair_index]["out_track"]
                    in_track = d["transitions"][pair_index]["in_track"]
                    start_beat, end_beat = _transition_window_beats(
                        out_track, in_track, args.context_bars)
                    tempo_map = d["tempo_map"]
                    windows_sec[label] = (
                        tempo_map.beat_to_sec(start_beat),
                        tempo_map.beat_to_sec(end_beat),
                    )
                    bounds_sec[label] = (0.0, d["wav_duration_sec"])
                # Equalise BEFORE cutting - an unequal clip length is itself
                # a blind-breaking tell (Codex FATAL-1). Opaque filenames
                # (not "{t_label}_{label}.wav") so a raw clip surviving an
                # abnormal stop can't disclose its side either (Codex
                # MAJOR-4).
                equalized = _equalize_windows_sec(windows_sec, bounds_sec)
                for label in labels:
                    # Re-check a strongly-bound side's WAV identity right
                    # before slicing it (Codex round 4, 2026-09-14 - narrows
                    # the TOCTOU window between verification and use, per
                    # transition, at negligible cost).
                    _check_wav_identity_unchanged(side_data[label], label)
                    start_sec, end_sec = equalized[label]
                    raw_path = raw_dir / f"{uuid.uuid4().hex}.wav"
                    extract_excerpt(side_data[label]["wav"], start_sec, end_sec, raw_path)
                    seal_cmd += ["--side", f"{label}={raw_path}"]
            except Exception as e:
                print(f"  EXTRACT FAILED: {type(e).__name__}: {e}")
                results[t_label] = {"pair_index": pair_index, "stage": "extract",
                                    "ok": False, "error": f"{type(e).__name__}: {e}"}
                continue

            code, out = run(seal_cmd, audit_dir / f"{t_label}_seal.log",
                            args.seal_timeout_sec)
            if code != 0:
                tail = "\n".join(out.strip().splitlines()[-6:])
                print(f"  SEAL FAILED ({code}):\n{tail}")
                results[t_label] = {"pair_index": pair_index, "stage": "seal", "ok": False}
                continue
            print(f"  sealed -> {args.out_dir / t_label / 'Listen'}")
            results[t_label] = {"pair_index": pair_index, "stage": "complete", "ok": True}

    audit_dir.mkdir(parents=True, exist_ok=True)
    # `binding` is recorded alongside the per-transition results, not just
    # printed as a warning during the run, so a completed run's own output
    # is never silently ambiguous about which sides were strongly bound
    # (Codex round 3, 2026-09-14).
    (audit_dir / "extract_results.json").write_text(
        json.dumps({"binding": binding, "transitions": results}, indent=2),
        encoding="utf-8")

    done = [t for t, r in results.items() if r.get("ok")]
    print(f"\nSealed {len(done)}/{len(reference_set)} transitions: {', '.join(done) or 'none'}")
    if weak_sides:
        print(f"NOT FULLY AUDITABLE: side(s) {', '.join(weak_sides)} had no bounce "
             f"manifest - their binding to this side was only duration-checked, "
             f"not cryptographically verified. Run record_bounce_manifest.py for "
             f"them next time, or pass --require-bounce-manifests to refuse this.")
    print(f"Audit logs: {audit_dir}")
    return 0 if len(done) == len(reference_set) else 1


if __name__ == "__main__":
    sys.exit(main())
