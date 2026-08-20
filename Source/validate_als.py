"""Validate that a Sections .als file is parseable by Ableton.

Catches the class of bug that produced corrupt V4 in 22.05.26 Mix —
where `apply_loops.py` overwrote integer fields with clip-name strings,
making Ableton refuse to load the file with:

    "Unexpected value for int node: drop_4_tail_loop"

The gate has two layers:
  1. STRUCTURAL — gzip-decompress and parse the XML. Catches any malformed
     element, unclosed tag, etc.
  2. TYPE — walk known elements that Ableton expects to hold integers
     and confirm their Value attribute parses as int. Add more known-int
     element paths to KNOWN_INT_PATHS as new corruption modes surface.

Exit codes:
  0 — clean, Ableton will load it
  1 — usage error / file missing
  2 — structural or type error (with specific element pointed out)

CLI:
  python validate_als.py <path.als>
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import sys
from pathlib import Path
import xml.etree.ElementTree as ET


# Element paths (parent/child under <AudioClip>) whose Value attribute
# must parse as int. Each entry: (parent_tag, child_tag).
# Add more here as new corruption patterns are found.
KNOWN_INT_CHILDREN = [
    ("ScaleInformation", "Root"),   # 0-11, scale root
    ("ScaleInformation", "Name"),   # 0-11, scale name index — V4 22.05.26 bug
    ("ScaleInformation", "Cipher"),
]

# Top-level int-valued elements that appear inside an AudioClip (not nested).
KNOWN_INT_TOPLEVEL = [
    "Type",
    "Disabled",          # actually bool but Ableton stores 'true'/'false'
]

# Mixer devices the automation pipeline writes envelopes to, as
# (device_tag, param_tag) pairs. Discovered from the live template
# ("DJ Mix Template 2026.als": every audio track except Session Time
# carries Utility + Channel EQ) and mirrored by the target finders in
# als_generator.py / apply_automation.py (StereoGain>Gain,
# ChannelEq>LowShelfGain). Kept here as DATA, not imports - validate_als
# is the corruption gate and must never import the pipeline; callers
# that know their expectations pass them in as arguments.
MIXER_AUTOMATION_DEVICES = (
    ("StereoGain", "Gain"),          # Utility gain - volume fades
    ("ChannelEq", "LowShelfGain"),   # Channel EQ low shelf - bass swaps
)

# FloatEvents earlier than this are Ableton "before-all-time" sentinel
# points (Time="-63072000"), not real automation - excluded from spans.
_SENTINEL_TIME_CEILING = 0.0


def _is_int(s: str) -> bool:
    try:
        int(s)
        return True
    except (ValueError, TypeError):
        return False


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def _track_effective_name(track_elem) -> str:
    for e in track_elem.iter("EffectiveName"):
        return e.get("Value", "?")
    return "?"


def _normalise_name(s: str) -> str:
    return " ".join(html.unescape(s or "").split()).casefold()


def _mixer_target_ids(track_elem, devices) -> set[str]:
    """AutomationTarget Ids of the given (device_tag, param_tag) params on one track."""
    ids: set[str] = set()
    for device_tag, param_tag in devices:
        for dev in track_elem.iter(device_tag):
            for param in dev.iter(param_tag):
                tgt = param.find("AutomationTarget")
                if tgt is not None and tgt.get("Id"):
                    ids.add(tgt.get("Id"))
    return ids


def report_als(
    path,
    expected_track_count: int | None = None,
    expected_track_devices: tuple[tuple[str, str], ...] | None = None,
    expected_transitions: list[dict] | None = None,
) -> list[str]:
    """Validate `path`, print a one-line OK/FAIL banner, return the errors.

    Hook wired into every compress_als() so the corruption gate runs
    automatically on every emitted .als — not only when a human runs the
    CLI. Writers treat a non-empty result as fatal and do not report success.

    The three optional expectation layers (track count, mixer device
    presence, transition envelope presence) are forwarded as-is when
    supplied; omitted = skipped (existing compress_als hooks stay
    byte-identical).
    """
    p = Path(path)
    errs = validate_als(
        p,
        expected_track_count=expected_track_count,
        expected_track_devices=expected_track_devices,
        expected_transitions=expected_transitions,
    )
    if errs:
        print(f"  [FAIL] ALS validation: {len(errs)} issue(s) in {p.name} "
              f"- do NOT load in Ableton:")
        for e in errs[:10]:
            print(f"      - {e}")
        if len(errs) > 10:
            print(f"      ... +{len(errs) - 10} more")
    else:
        print(f"  [OK] ALS validation passed: {p.name}")
    return errs


def validate_als(
    path: Path,
    expected_track_count: int | None = None,
    expected_track_devices: tuple[tuple[str, str], ...] | None = None,
    expected_transitions: list[dict] | None = None,
) -> list[str]:
    """Return a list of error messages. Empty list = file is clean.

    The first four layers (gzip/XML structural parse, known-int field
    types, clip sanity, track ordering) always run. The three OPTIONAL
    expectation layers are skipped unless their caller passes them:

      expected_track_count   - asserts the ALS carries exactly this many
                               clip-bearing AudioTracks (layer 5).
      expected_track_devices - asserts every ACTIVE track has each of these
                               (device_tag, param_tag) AutomationTarget Ids
                               (layer 6, transition automation contract).
      expected_transitions   - asserts every transition dict with a non-null
                               swap_beats has, on BOTH its out_track and
                               in_track, an automation envelope covering the
                               swap time (layer 7).

    All three are None by default so existing call sites (the report_als
    hook fired on every compress_als) stay byte-identical.
    """
    errors: list[str] = []

    # --- Layer 1: gzip + XML structural parse ---
    try:
        with gzip.open(path, "rb") as g:
            content_bytes = g.read()
    except Exception as e:
        return [f"Cannot gzip-decompress {path.name}: {e}"]

    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        return [f"UTF-8 decode failed in {path.name}: {e}"]

    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        # Try to surface the line/column from the error
        return [f"XML parse error in {path.name}: {e}"]

    # --- Layer 2: type checks on known-int fields ---
    for clip in root.iter("AudioClip"):
        clip_name = None
        # Find this clip's own <Name> for context in error messages.
        # The AudioClip-level Name is a direct child, not nested.
        for child in clip:
            if child.tag == "Name":
                clip_name = child.get("Value", "?")
                break

        for parent_tag, child_tag in KNOWN_INT_CHILDREN:
            for parent in clip.iter(parent_tag):
                child = parent.find(child_tag)
                if child is None:
                    continue
                val = child.get("Value")
                if val is None:
                    errors.append(
                        f"Clip '{clip_name}': <{parent_tag}><{child_tag}/> "
                        f"has no Value attribute"
                    )
                elif not _is_int(val):
                    errors.append(
                        f"Clip '{clip_name}': <{parent_tag}><{child_tag} "
                        f"Value=\"{val}\"/> is not an integer "
                        f"(Ableton rejects this with 'Unexpected value for "
                        f"int node: {val}')"
                    )

    # --- Layer 3: clip sanity — no zero/negative-length clips ---
    # Catches the failure mode where apply_section_corrections pushes a
    # boundary past the to_clip's end, leaving the clip with start > end.
    # Ableton may "load" this but the clip is invisible / unplayable.
    for clip in root.iter("AudioClip"):
        clip_name = "?"
        for child in clip:
            if child.tag == "Name":
                clip_name = child.get("Value", "?")
                break

        def _f(elem_tag):
            el = clip.find(elem_tag)
            if el is None:
                return None
            v = el.get("Value")
            try:
                return float(v)
            except (ValueError, TypeError):
                return None

        cs = _f("CurrentStart")
        ce = _f("CurrentEnd")
        ls = _f("LoopStart")
        le = _f("LoopEnd")

        if cs is not None and ce is not None and ce <= cs:
            errors.append(
                f"Clip '{clip_name}': CurrentEnd ({ce}) <= CurrentStart ({cs}) "
                f"— zero/negative arrangement length. Clip will be invisible "
                f"in Ableton or behave strangely."
            )
        if ls is not None and le is not None and le <= ls:
            errors.append(
                f"Clip '{clip_name}': LoopEnd ({le}) <= LoopStart ({ls}) "
                f"— zero/negative source length. The audio region "
                f"references nothing playable."
            )

    # --- Layer 4: track ordering — AudioTrack order in the .als file
    # MUST match the time order of the tracks' first clips. Catches the
    # symptom of misapplied shifts, regardless of root cause. Bug we
    # actually shipped: Mike Richters - Your Love and Your Love
    # (Instrumental Mix) collided on _match_track's loose-prefix path,
    # and both shifts hit the Instrumental track. Result: track #20
    # played at the start of the mix and track #19 played at the end.
    #
    # The orchestrator's --sections-layout places AudioTracks in
    # Camelot+BPM-sequenced order. So .als-file order = expected play
    # order. If after shifts a later-in-file track ends up earlier in
    # time than an earlier-in-file track, something is wrong.
    audio_tracks_in_file_order = []
    for at in root.iter("AudioTrack"):
        # Pull the EffectiveName for the error message
        name = "?"
        for ename in at.iter("EffectiveName"):
            name = ename.get("Value", "?")
            break
        # Pull the earliest clip's Time
        times = []
        for clip in at.iter("AudioClip"):
            t = clip.get("Time")
            try:
                times.append(float(t))
            except (ValueError, TypeError):
                pass
        if not times:
            continue  # ignore tracks with no clips (Return tracks, Session Time, etc.)
        audio_tracks_in_file_order.append((min(times), name))

    # Filter to AudioTracks that actually carry audio — the Ableton template
    # has 35 audio slots but only ~20 are used per project; empty slots get
    # filtered out above (no clips → no entry).
    if len(audio_tracks_in_file_order) >= 2:
        prev_t, prev_name = audio_tracks_in_file_order[0]
        for t, name in audio_tracks_in_file_order[1:]:
            if t < prev_t:
                errors.append(
                    f"Track ordering: '{name}' plays at arr_time {t} but the "
                    f"previous track in file order ('{prev_name}') plays at "
                    f"{prev_t} (later). Tracks are out of sequence in the "
                    f"arrangement — likely a shift was applied to the wrong "
                    f"AudioTrack (see _match_track collision class, 22.05.26)."
                )
            prev_t, prev_name = t, name

    # Compute once for layers 5-7 (only used when the caller asked for them).
    audio_tracks = list(root.iter("AudioTrack"))
    active_tracks = [at for at in audio_tracks
                     if next(at.iter("AudioClip"), None) is not None]

    # --- Layer 5: expected track count (Codex B6) ---
    # The orchestrator knows exactly how many clip-bearing AudioTracks the
    # job must produce (one per track patch). A mismatch means a clip
    # insertion silently failed or the template was stripped/wrong upstream.
    # Runs only when the caller passes expected_track_count; absent = skip.
    if expected_track_count is not None and len(active_tracks) != expected_track_count:
        errors.append(
            f"Track count: ALS has {len(active_tracks)} clip-bearing AudioTrack(s) "
            f"but the job expects {expected_track_count} - a track was dropped or "
            f"duplicated upstream (truncation class, Codex B6)."
        )

    # --- Layer 6: mixer device presence on every active track (Codex B6) ---
    # The automation stage writes envelopes to Utility gain + Channel EQ
    # low shelf; both must be present with a resolved AutomationTarget Id
    # on every clip-bearing track, otherwise the planned transition
    # automation cannot land. Runs only when the caller passes
    # expected_track_devices; absent = skip.
    if expected_track_devices is not None:
        for at in active_tracks:
            name = _track_effective_name(at)
            for device_tag, param_tag in expected_track_devices:
                has_target = False
                for dev in at.iter(device_tag):
                    for param in dev.iter(param_tag):
                        tgt = param.find("AutomationTarget")
                        if tgt is not None and tgt.get("Id"):
                            has_target = True
                            break
                    if has_target:
                        break
                if not has_target:
                    errors.append(
                        f"Track '{name}': mixer device <{device_tag}> "
                        f"(param {param_tag}) is missing or has no "
                        f"AutomationTarget Id - transition automation "
                        f"cannot target it (Codex B6)."
                    )

    # --- Layer 7: transition envelope presence (Codex B6) ---
    # For each transition dict with a non-null swap_beats, both the
    # out_track and in_track must carry, for EVERY device in the
    # expectation set (Utility gain AND Channel EQ low shelf), an
    # automation envelope whose real FloatEvent times cover the swap.
    # "Real" means Time >= _SENTINEL_TIME_CEILING so Ableton's
    # before-all-time sentinel (Time="-63072000") doesn't fake-coverage.
    # Runs only when the caller passes expected_transitions; absent =
    # skip. When the caller also passes expected_track_devices, that
    # overrides the default mixer device set; otherwise the standard
    # (Utility + Channel EQ) set is used.
    if expected_transitions is not None:
        devices = expected_track_devices or MIXER_AUTOMATION_DEVICES
        by_name = {
            _normalise_name(_track_effective_name(at)): at
            for at in active_tracks
        }
        for tr in expected_transitions:
            swap = tr.get("swap_beats")
            if swap is None:
                continue  # transition has no automation contract
            out_raw = tr.get("out_track", "")
            in_raw = tr.get("in_track", "")
            for side_raw in (out_raw, in_raw):
                norm = _normalise_name(side_raw)
                if norm in by_name:
                    track = by_name[norm]
                else:
                    # Substring match either direction, unique only -
                    # mirrors the pipeline's exact/substring matcher.
                    candidates = [
                        at for at in active_tracks
                        if norm and (
                            norm in _normalise_name(_track_effective_name(at))
                            or _normalise_name(_track_effective_name(at)) in norm
                        )
                    ]
                    if len(candidates) == 1:
                        track = candidates[0]
                    else:
                        errors.append(
                            f"Transition '{tr.get('out_track', '?')}' -> "
                            f"'{tr.get('in_track', '?')}': track '{side_raw}' "
                            f"not found unambiguously in ALS - cannot verify "
                            f"its transition envelopes."
                        )
                        continue
                track_name = _track_effective_name(track)
                # Coverage is PER DEVICE: every (device, param) in the
                # expectation set must have its own envelope whose real
                # events span the swap. Pooling the target ids let ANY one
                # qualifying envelope (e.g. the volume fade) conceal a
                # completely absent bass-EQ envelope - a false pass
                # (Codex round-2 BLOCKER 2). Each miss is reported naming
                # the device so the failure is actionable.
                for device_tag, param_tag in devices:
                    target_ids = _mixer_target_ids(
                        track, ((device_tag, param_tag),)
                    )
                    covered = False
                    for env in track.iter("AutomationEnvelope"):
                        env_target = env.find("EnvelopeTarget/PointeeId")
                        if env_target is None:
                            continue
                        pointee = env_target.get("Value")
                        if pointee is None or pointee not in target_ids:
                            continue
                        times: list[float] = []
                        for ev in env.iter("FloatEvent"):
                            try:
                                t = float(ev.get("Time", "nan"))
                            except (ValueError, TypeError):
                                continue
                            if t >= _SENTINEL_TIME_CEILING:
                                times.append(t)
                        if not times:
                            continue
                        lo, hi = min(times), max(times)
                        if lo <= swap <= hi:
                            covered = True
                            break
                    if not covered:
                        errors.append(
                            f"Transition '{tr.get('out_track', '?')}' -> "
                            f"'{tr.get('in_track', '?')}' at beat {swap}: no "
                            f"<{device_tag}> (param {param_tag}) automation "
                            f"envelope on track '{track_name}' covers the "
                            f"swap - the planned transition automation is "
                            f"missing from the ALS (Codex B6)."
                        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("als_path", type=Path)
    parser.add_argument(
        "--expected-tracks", type=int, default=None,
        help="Assert the ALS carries exactly this many clip-bearing AudioTracks",
    )
    parser.add_argument(
        "--require-devices", action="store_true",
        help="Assert every active track carries Utility (StereoGain) + Channel EQ "
             "with resolved AutomationTarget Ids",
    )
    parser.add_argument(
        "--arrangement-report", type=Path, default=None,
        help="JSON arrangement report; its transitions are checked for envelope "
             "coverage of the planned swap_beats",
    )
    args = parser.parse_args()

    if not args.als_path.exists():
        print(f"ERROR: {args.als_path} does not exist", file=sys.stderr)
        return 1

    devices = MIXER_AUTOMATION_DEVICES if args.require_devices else None
    transitions = None
    if args.arrangement_report is not None:
        if not args.arrangement_report.exists():
            print(f"ERROR: {args.arrangement_report} does not exist",
                  file=sys.stderr)
            return 1
        rep = json.loads(args.arrangement_report.read_text(encoding="utf-8"))
        transitions = rep.get("transitions", [])

    errs = validate_als(
        args.als_path,
        expected_track_count=args.expected_tracks,
        expected_track_devices=devices,
        expected_transitions=transitions,
    )
    if errs:
        print(f"FAIL  {args.als_path.name}: {len(errs)} issue(s)",
              file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        print(f"\nAbleton would reject this file. Fix the upstream script "
              f"that wrote it (apply_loops.py, apply_automation.py, etc.) "
              f"before proceeding.", file=sys.stderr)
        return 2

    print(f"PASS  {args.als_path.name}: structurally valid + known int "
          f"fields type-correct. Ableton can load this.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
