"""Pure helpers behind `Tests/test_alignment_baseline.py --refresh`.

Master Board card 228: the regression net had become a drift detector, not a
control. `baseline_alignments.json` was refreshed 4+ times in three days and
`--refresh` overwrote it with no reason, reviewer, diff artifact or input hashes.
This module is what makes a refresh a decision with a record:

  * `--reason "<text>"` is REQUIRED (at least 15 letters or digits; punctuation,
    spaces and invisible characters do not count). Without it the command exits
    non-zero before anything is computed or written. Giving `--reason` or
    `--reviewer` twice is refused. `--reviewer "<name>"` is optional.
  * Every successful refresh writes a sidecar, `baseline_alignments.refresh-<UTC
    time>.json`, NEXT TO the baseline, BEFORE the baseline is replaced. It holds the
    reason, reviewer, time, git commit, schema marker, sha256 of every input the
    baseline generator reads, sha256 of the OLD and NEW baseline files, a diff
    summary and the policy shift. Two refreshes in one second get `-2`, `-3`, ...
  * The baseline itself is written to a temp file and swapped in with os.replace, so
    a failure leaves the old baseline intact. If the swap fails after the sidecar was
    written, the sidecar is renamed `*.not-applied`; a sidecar whose
    `new_baseline_sha256` differs from the baseline's current hash never landed.
  * A large change is refused unless `--accept-shift` is given (see the limits below):
    pairs moving between the two named policies, pairs flipping between align and
    raise, pairs changing overlap policy in any way, or too many pairs removed. The
    sidecar names each guard that fired in `guards_tripped_names`.
  * The temp files carry the process id. A refresh killed between its writes leaves a
    temp copy and a record with a live name; the next refresh deletes the stale temp
    file, renames the record `*.not-applied` and says so in one line.

Nothing here imports the alignment engine or needs the 14.08.26 corpus; the test file
only supplies a function that regenerates the payload. Tests: Tests/test_baseline_refresh.py.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Guard limits. These are a STARTING GUESS, to be tuned by Sam: they were picked
# so that a normal one-signal rewiring step (a handful of pairs moving) passes
# and a sweeping change has to be accepted on purpose.
# ---------------------------------------------------------------------------
#: Refuse when this many pairs or MORE move between named_landmark_64 and
#: standard_48 (both directions added together).
SHIFT_MAX_PAIRS = 10
#: ... or when MORE than this fraction of the compared pairs move between the two
#: policies (both directions added together).
SHIFT_MAX_FRACTION = 0.10
#: Refuse when MORE than this fraction of the old baseline's pairs are gone.
REMOVED_MAX_FRACTION = 0.05
#: Refuse when this many pairs or MORE change status (ok <-> raise), or when MORE than
#: this fraction of the compared pairs do. Set to the same numbers as the policy shift
#: by default; tune on their own if a rewiring step legitimately flips more.
STATUS_FLIP_MAX_PAIRS = 10
STATUS_FLIP_MAX_FRACTION = 0.10
#: Refuse when this many pairs or MORE change overlap_policy in ANY way (to or from
#: None, to or from an unnamed value, not only between the two named policies), or
#: when MORE than this fraction of the compared pairs do. Same default numbers again.
OVERLAP_CHANGE_MAX_PAIRS = 10
OVERLAP_CHANGE_MAX_FRACTION = 0.10

#: Letters or digits (str.isalnum) a --reason must have; spaces, punctuation and
#: invisible characters do not count.
MIN_REASON_CHARS = 15
#: A temp file or half-written record must be older than this before the next
#: refresh treats it as left behind by a killed run (so a refresh that is running
#: right now, which only holds such files for a moment, is never touched).
STALE_AFTER_SECONDS = 120
#: How many changed pair ids (with old and new values) the diff summary lists.
MAX_LISTED_PAIRS = 20

POLICY_NAMED = "named_landmark_64"
POLICY_STANDARD = "standard_48"

#: Fields grouped as "swap / handoff" in the diff summary.
SWAP_HANDOFF_FIELDS = ("swap_progress", "handoff_kind", "handoff_bar_out",
                       "arr_offset_bars", "overlap_bars")
_KNOWN_FIELDS = set(SWAP_HANDOFF_FIELDS) | {
    "alignment_policy", "overlap_policy", "plan", "status", "error"}

#: Code the baseline generator reads, relative to the project root. The generator
#: is compute_rows() in test_alignment_baseline.py: it imports align_engine, which
#: imports stem_section_probe and automated_dj_mixes.transition_policy. The test
#: file itself is listed because it holds the projection (_populate_ok,
#: _derive_overlap_policy, _spec_dict) that shapes every row.
CODE_INPUTS = (
    "Source/align_engine.py",
    "Source/stem_section_probe.py",
    "Source/automated_dj_mixes/__init__.py",
    "Source/automated_dj_mixes/transition_policy.py",
    "Tests/test_alignment_baseline.py",
)
#: Optional per-corpus file align_engine.load_track reads from the stem directory.
OVERRIDES_FILENAME = "loop_quality_overrides.json"


class RefreshRefused(Exception):
    """A refresh that must not go ahead. str(exc) is ONE plain line for the console."""


@dataclasses.dataclass(frozen=True)
class RefreshArgs:
    reason: str
    reviewer: str | None
    accept_shift: bool


def _ascii(text):
    return str(text).encode("ascii", "replace").decode("ascii")


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------
def validate_reason(reason):
    """Return the stripped reason, or raise RefreshRefused if it is missing or too short."""
    text = "" if reason is None else str(reason)
    if sum(1 for ch in text if ch.isalnum()) < MIN_REASON_CHARS:
        raise RefreshRefused(
            'refresh refused: --reason "<why this diff is accepted>" is required '
            f"(at least {MIN_REASON_CHARS} letters or digits); nothing was written")
    return text.strip()


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise RefreshRefused(f"refresh refused: {message}; nothing was written")


class _Once(argparse.Action):
    """Store the value, but refuse a second occurrence instead of silently replacing it."""

    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest) is not None:
            parser.error(f"{option_string} was given more than once")
        setattr(namespace, self.dest, values)


def parse_refresh_args(argv):
    """Parse the arguments after the script name. Raises RefreshRefused on any problem."""
    parser = _Parser(prog="test_alignment_baseline.py", add_help=False,
                     allow_abbrev=False)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--reason", default=None, action=_Once)
    parser.add_argument("--reviewer", default=None, action=_Once)
    parser.add_argument("--accept-shift", action="store_true", dest="accept_shift")
    ns = parser.parse_args(list(argv))
    reason = validate_reason(ns.reason)
    reviewer = (ns.reviewer or "").strip() or None
    return RefreshArgs(reason=reason, reviewer=reviewer,
                       accept_shift=bool(ns.accept_shift))


# ---------------------------------------------------------------------------
# Hashes
# ---------------------------------------------------------------------------
def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _label(path, root):
    if root is not None:
        try:
            return Path(path).resolve().relative_to(root).as_posix()
        except ValueError:
            pass
    return Path(path).as_posix()


def input_hashes(paths, root=None):
    """sha256 of every input file, keyed by path (relative to `root` when it is
    under it). A path that is not a file records the string "missing"."""
    root_path = Path(root).resolve() if root is not None else None
    hashes = {}
    for p in paths:
        p = Path(p)
        hashes[_label(p, root_path)] = sha256_file(p) if p.is_file() else "missing"
    return dict(sorted(hashes.items()))


def _track_name(stem_json):
    """The `track` field align_engine.load_track names the envelope cache from; the
    name in the file name when the JSON cannot be read (the generator will fail then)."""
    try:
        track = json.loads(Path(stem_json).read_text(encoding="utf-8"))["track"]
        if isinstance(track, str) and track:
            return track
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return Path(stem_json).name[len("SECTIONS_STEM_"):-len(".json")]


def generator_inputs(root, stem_dir):
    """Every file the baseline generator reads: the corpus stem JSONs, the optional
    loop-quality overrides beside them, the code files in CODE_INPUTS, and for every
    track the `<track>__stemenv.npz` envelope cache (align_engine.load_track, read by
    load_loop_quality_context from plan_fill_or_cut; a missing cache silently turns
    the loop quality check into "unmeasured", so it changes the plan) and the
    `SECTIONS_STEM_<track>.json` that loader derives from the cache's name."""
    root = Path(root)
    stem_dir = Path(stem_dir)
    stems = sorted(stem_dir.glob("SECTIONS_STEM_*.json"))
    paths = list(stems)
    paths.append(stem_dir / OVERRIDES_FILENAME)
    for stem in stems:
        track = _track_name(stem)
        paths.append(stem_dir / f"{track}__stemenv.npz")
        paths.append(stem_dir / f"SECTIONS_STEM_{track}.json")
    paths.extend(root / rel for rel in CODE_INPUTS)
    return paths


def git_head(cwd):
    """`git rev-parse HEAD` run in `cwd`, or "unknown" when git or the repo is not there."""
    try:
        done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(cwd),
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = done.stdout.strip()
    if done.returncode == 0 and len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
        return sha
    return "unknown"


# ---------------------------------------------------------------------------
# Payload shape, diff, policy shift, guards
# ---------------------------------------------------------------------------
def check_payload(payload, label):
    ok = isinstance(payload, dict) and isinstance(payload.get("rows"), list) and all(
        isinstance(r, dict) and "out" in r and "in" in r and "status" in r
        for r in payload["rows"])
    if not ok:
        raise RefreshRefused(
            f"refresh refused: {label} is not a baseline payload "
            "(a JSON object with a 'rows' list of out/in/status rows); nothing was written")


def _rows_by_key(payload):
    if payload is None:
        return {}
    rows = payload["rows"] if isinstance(payload, dict) else payload
    return {(r["out"], r["in"]): r for r in rows}


def _pair_id(key):
    return f"{key[0]} -> {key[1]}"


def diff_summary(old, new):
    """Counts of what a refresh changes, plus the first MAX_LISTED_PAIRS changed pair ids
    with old and new values for the changed fields.

    `old` and `new` are baseline payloads (or bare row lists); `old` may be None (no
    baseline yet). `changed_by_field` counts PAIRS in which that kind of field differs:
    alignment_policy, overlap_policy, swap_handoff (swap_progress / handoff_kind /
    handoff_bar_out / arr_offset_bars / overlap_bars), spec (the plan_fill_or_cut
    output), raise_type (a pair that raised both times with a different exception
    type), status_flip (ok <-> raise), other (any remaining field, e.g. cue
    provenance, score, notes, a raise message).
    """
    o, n = _rows_by_key(old), _rows_by_key(new)
    added = sorted(set(n) - set(o))
    removed = sorted(set(o) - set(n))
    common = sorted(set(o) & set(n))
    counters = {"alignment_policy": 0, "overlap_policy": 0, "swap_handoff": 0,
                "spec": 0, "raise_type": 0, "status_flip": 0, "other": 0}
    changed = []
    for key in common:
        ro, rn = o[key], n[key]
        diffs = {f: {"old": ro.get(f), "new": rn.get(f)}
                 for f in sorted(set(ro) | set(rn)) if ro.get(f) != rn.get(f)}
        if not diffs:
            continue
        changed.append((key, diffs))
        if "alignment_policy" in diffs:
            counters["alignment_policy"] += 1
        if "overlap_policy" in diffs:
            counters["overlap_policy"] += 1
        if any(f in diffs for f in SWAP_HANDOFF_FIELDS):
            counters["swap_handoff"] += 1
        if "plan" in diffs:
            counters["spec"] += 1
        if "status" in diffs:
            counters["status_flip"] += 1
        elif ro.get("status") == "raise" and "error" in diffs:
            counters["raise_type"] += 1
        if any(f not in _KNOWN_FIELDS for f in diffs):
            counters["other"] += 1
    return {
        "pairs_old": len(o),
        "pairs_new": len(n),
        "pairs_added": len(added),
        "pairs_removed": len(removed),
        "pairs_changed": len(changed),
        "pairs_unchanged": len(common) - len(changed),
        "changed_by_field": counters,
        "first_added": [_pair_id(k) for k in added[:MAX_LISTED_PAIRS]],
        "first_removed": [_pair_id(k) for k in removed[:MAX_LISTED_PAIRS]],
        "first_changed": [
            {"id": _pair_id(k), "out": k[0], "in": k[1], "changes": diffs}
            for k, diffs in changed[:MAX_LISTED_PAIRS]],
    }


def policy_shift(old, new):
    """How many pairs present in both moved between overlap policies."""
    o, n = _rows_by_key(old), _rows_by_key(new)
    common = set(o) & set(n)
    to_standard = to_named = 0
    for key in common:
        before, after = o[key].get("overlap_policy"), n[key].get("overlap_policy")
        if before == POLICY_NAMED and after == POLICY_STANDARD:
            to_standard += 1
        elif before == POLICY_STANDARD and after == POLICY_NAMED:
            to_named += 1
    return {
        "named_landmark_64_to_standard_48": to_standard,
        "standard_48_to_named_landmark_64": to_named,
        "total_moved": to_standard + to_named,
        "pairs_compared": len(common),
    }


def _over(count, compared, max_pairs, max_fraction):
    return count >= max_pairs or count / compared > max_fraction


def guard_trips(shift, summary):
    """Reasons the large-change guard fires, each "<guard name>: <plain text>" (an empty
    list means fine to refresh). The guards, in the order they are reported:
    policy_shift (pairs moving between named_landmark_64 and standard_48),
    status_flips (pairs whose ok/raise status changed), overlap_policy_changes
    (pairs whose overlap_policy changed in ANY way, e.g. to or from None) and
    pairs_removed. A sweeping change that is not a move between the two named
    policies is caught by the middle two."""
    trips = []
    compared = max(shift["pairs_compared"], 1)
    changes = summary.get("changed_by_field") or {}
    moved = shift["total_moved"]
    if _over(moved, compared, SHIFT_MAX_PAIRS, SHIFT_MAX_FRACTION):
        trips.append(f"policy_shift: {moved} of {compared} pairs moved between "
                     f"{POLICY_NAMED} and {POLICY_STANDARD} (limits: under "
                     f"{SHIFT_MAX_PAIRS} pairs and under {SHIFT_MAX_FRACTION:.0%})")
    flips = changes.get("status_flip", 0)
    if _over(flips, compared, STATUS_FLIP_MAX_PAIRS, STATUS_FLIP_MAX_FRACTION):
        trips.append(f"status_flips: {flips} of {compared} pairs changed between align "
                     f"and raise (limits: under {STATUS_FLIP_MAX_PAIRS} pairs and under "
                     f"{STATUS_FLIP_MAX_FRACTION:.0%})")
    overlap = changes.get("overlap_policy", 0)
    if _over(overlap, compared, OVERLAP_CHANGE_MAX_PAIRS, OVERLAP_CHANGE_MAX_FRACTION):
        trips.append(f"overlap_policy_changes: {overlap} of {compared} pairs changed "
                     f"overlap policy in some way (limits: under {OVERLAP_CHANGE_MAX_PAIRS} "
                     f"pairs and under {OVERLAP_CHANGE_MAX_FRACTION:.0%})")
    old_pairs = summary["pairs_old"]
    removed = summary["pairs_removed"]
    if old_pairs and removed / old_pairs > REMOVED_MAX_FRACTION:
        trips.append(f"pairs_removed: {removed} of {old_pairs} pairs are gone from the new "
                     f"baseline (limit: {REMOVED_MAX_FRACTION:.0%})")
    return trips


def schema_marker(payload):
    """The baseline carries no schema marker of its own, so derive one from its shape:
    the top-level keys and the union of row keys. A declared `schema` (or
    `schema_version`) key, if a future baseline adds one, wins."""
    declared = payload.get("schema", payload.get("schema_version"))
    top = sorted(payload)
    row_keys = sorted({k for r in payload["rows"] for k in r})
    digest = sha256_bytes(json.dumps([top, row_keys]).encode("ascii"))[:16]
    return {
        "marker": f"declared:{declared}" if declared is not None else f"derived:{digest}",
        "top_level_keys": top,
        "row_keys": row_keys,
    }


# ---------------------------------------------------------------------------
# Writing: temp file, then os.replace. Sidecar first, baseline second.
# ---------------------------------------------------------------------------
def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def _stage_bytes(final, data):
    """Write `data` to a temp file beside `final` and return the temp path (not live yet).
    The name carries the process id, so two refreshes at once never share a temp file."""
    staged = final.with_name(f"{final.name}.{os.getpid()}.tmp")
    try:
        with open(staged, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        _quiet_remove(staged)
        raise
    return staged


def _quiet_remove(path):
    try:
        path.unlink()
    except OSError:
        pass


def _baseline_bytes(payload):
    """The exact bytes the pre-hardening --refresh wrote: Path.write_text of
    json.dumps(sort_keys=True, indent=1), utf-8, with the platform's newline."""
    text = json.dumps(payload, sort_keys=True, indent=1)
    if os.linesep != "\n":
        text = text.replace("\n", os.linesep)
    return text.encode("utf-8")


def _publish_sidecar(baseline, record, now):
    """Write the sidecar next to the baseline under a name nothing else holds, atomically."""
    stamp = now.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    data = (json.dumps(record, sort_keys=True, indent=1) + "\n").encode("ascii")
    for n in range(1, 1000):
        suffix = "" if n == 1 else f"-{n}"
        final = baseline.with_name(f"{baseline.stem}.refresh-{stamp}{suffix}.json")
        try:
            open(final, "xb").close()           # claim the name; never overwrite another record
        except FileExistsError:
            continue
        staged = None
        try:
            staged = _stage_bytes(final, data)
            os.replace(staged, final)
        except BaseException:
            if staged is not None:
                _quiet_remove(staged)
            _quiet_remove(final)
            raise
        return final
    raise RefreshRefused("refresh refused: 999 refresh records already share one second; "
                         "nothing was written")


def _unlanded_record(path, current_sha):
    """True for a refresh record that cannot belong to a refresh that landed: empty
    (killed after the name was claimed), or one whose OLD hash is the baseline now on
    disk while its NEW hash is not (killed before the swap)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    if not raw:
        return True
    try:
        rec = json.loads(raw.decode("ascii"))
    except ValueError:
        return False
    if not isinstance(rec, dict):
        return False
    new, old = rec.get("new_baseline_sha256"), rec.get("old_baseline_sha256")
    return isinstance(new, str) and isinstance(old, str) and new != current_sha and old == current_sha


def cleanup_interrupted(baseline, stale_after=STALE_AFTER_SECONDS, now=None):
    """Tidy up after a refresh that was killed between its writes.

    A hard kill (power cut, taskkill) skips every except/finally, so it can leave a
    full-size temp copy of the baseline and a refresh record under a normal live name
    for a baseline that never moved. Only files older than `stale_after` seconds are
    touched, so a refresh running right now is safe. Temp files matching this
    baseline's own patterns are deleted; a record whose OLD hash is the current
    baseline's and whose NEW hash is not (or an empty one) is renamed `*.not-applied`.
    Returns {"removed": [names], "marked": [names]}.
    """
    baseline = Path(baseline)
    now = time.time() if now is None else now
    base, stem = re.escape(baseline.name), re.escape(baseline.stem)
    stamp = r"\d{8}T\d{6}Z(?:-\d+)?"
    temp_re = re.compile(rf"^(?:{base}|{stem}\.refresh-{stamp}\.json)(?:\.\d+)?\.tmp$")
    record_re = re.compile(rf"^{stem}\.refresh-{stamp}\.json$")
    current = sha256_file(baseline) if baseline.is_file() else "missing"
    removed, marked = [], []
    for path in sorted(baseline.parent.iterdir()):
        if not path.is_file():
            continue
        try:
            if now - path.stat().st_mtime < stale_after:
                continue
            if temp_re.match(path.name):
                path.unlink()
                removed.append(path.name)
            elif record_re.match(path.name) and _unlanded_record(path, current):
                os.rename(path, path.with_name(path.name + ".not-applied"))
                marked.append(path.name)
        except OSError:
            continue
    return {"removed": removed, "marked": marked}


def _read_old(baseline):
    """(payload or None, sha256 or "missing") of the baseline now on disk."""
    if not baseline.exists():
        return None, "missing"
    raw = baseline.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise RefreshRefused(
            "refresh refused: the existing baseline is not valid JSON; fix or move it "
            "first; nothing was written") from None
    check_payload(payload, "the existing baseline")
    return payload, sha256_bytes(raw)


def refresh_baseline(baseline, payload, *, reason, reviewer=None, accept_shift=False,
                     input_paths=(), root=None, clock=None, head=None):
    """Replace `baseline` with `payload` if the reason and the guards allow it.

    Order: validate -> diff and guard -> stage the new baseline as a temp file ->
    write the sidecar -> os.replace the temp file over the baseline. Nothing live is
    touched before the guards pass; the sidecar exists before the baseline moves.
    Returns a dict (sidecar path, diff summary, policy shift, accepted_shift, hashes).
    """
    baseline = Path(baseline)
    reason = validate_reason(reason)
    check_payload(payload, "the new baseline")
    old_payload, old_sha = _read_old(baseline)
    summary = diff_summary(old_payload, payload)
    shift = policy_shift(old_payload, payload)
    trips = guard_trips(shift, summary)
    if trips and not accept_shift:
        raise RefreshRefused("refresh refused: " + "; ".join(trips)
                             + ". Read the diff, then add --accept-shift if it is "
                             "intended; nothing was written")

    now = (clock or _utc_now)()
    data = _baseline_bytes(payload)
    new_sha = sha256_bytes(data)
    schema = schema_marker(payload)
    record = {
        "kind": "baseline_refresh_record",
        "format": 1,
        "baseline_file": baseline.name,
        "reason": reason,
        "reviewer": reviewer,
        "time_utc": now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": (head or git_head)(baseline.parent),
        "schema_marker": schema["marker"],
        "schema_detail": schema,
        "input_hashes": input_hashes(input_paths, root),
        "old_baseline_sha256": old_sha,
        "new_baseline_sha256": new_sha,
        "old_counts": _counts(old_payload),
        "new_counts": _counts(payload),
        "diff_summary": summary,
        "policy_shift": shift,
        "guards_tripped": trips,
        "guards_tripped_names": [t.split(":", 1)[0] for t in trips],
        "accept_shift_flag": bool(accept_shift),
        "accepted_shift": bool(trips) and bool(accept_shift),
        "guard_limits": {"shift_max_pairs": SHIFT_MAX_PAIRS,
                         "shift_max_fraction": SHIFT_MAX_FRACTION,
                         "removed_max_fraction": REMOVED_MAX_FRACTION,
                         "status_flip_max_pairs": STATUS_FLIP_MAX_PAIRS,
                         "status_flip_max_fraction": STATUS_FLIP_MAX_FRACTION,
                         "overlap_change_max_pairs": OVERLAP_CHANGE_MAX_PAIRS,
                         "overlap_change_max_fraction": OVERLAP_CHANGE_MAX_FRACTION},
    }

    staged = _stage_bytes(baseline, data)
    sidecar = None
    try:
        sidecar = _publish_sidecar(baseline, record, now)
        os.replace(staged, baseline)
    except BaseException:
        _quiet_remove(staged)
        if sidecar is not None:
            try:
                os.rename(sidecar, sidecar.with_name(sidecar.name + ".not-applied"))
            except OSError:
                pass
        raise
    return {"sidecar": sidecar, "summary": summary, "shift": shift,
            "accepted_shift": record["accepted_shift"], "guards_tripped": trips,
            "old_sha256": old_sha, "new_sha256": new_sha}


def _counts(payload):
    if payload is None:
        return None
    return {k: payload.get(k) for k in ("n_tracks", "n_pairs", "ok", "raised")}


def _say(text, stream=None):
    print(_ascii(text), file=stream or sys.stdout)


def run_refresh_cli(argv, baseline, build_payload, input_paths, root=None,
                    clock=None, head=None):
    """The whole `--refresh` command. `argv` is sys.argv[1:]; `build_payload()` returns
    the freshly generated payload (the slow, corpus-dependent step). Returns the exit
    code: 0 refreshed, 2 refused (one plain line on stderr), 1 failed with the old
    baseline left in place."""
    baseline = Path(baseline)
    try:
        args = parse_refresh_args(argv)
        _read_old(baseline)                       # cheap sanity check before the slow step
        cleaned = cleanup_interrupted(baseline)
        if cleaned["removed"] or cleaned["marked"]:
            _say(f"cleaned up after an interrupted refresh: removed "
                 f"{len(cleaned['removed'])} leftover temp file(s), marked "
                 f"{len(cleaned['marked'])} unfinished record(s) .not-applied")
        payload = build_payload()
        result = refresh_baseline(
            baseline, payload, reason=args.reason, reviewer=args.reviewer,
            accept_shift=args.accept_shift, input_paths=input_paths, root=root,
            clock=clock, head=head)
    except RefreshRefused as exc:
        _say(str(exc), sys.stderr)
        return 2
    except OSError as exc:
        _say(f"refresh failed, the old baseline is unchanged: {exc}", sys.stderr)
        return 1
    n = len(payload["rows"])
    ok = sum(1 for r in payload["rows"] if r["status"] == "ok")
    s, sh = result["summary"], result["shift"]
    _say(f"baseline refreshed: {n} pairs ({ok} align, {n - ok} raise)")
    _say(f"diff: {s['pairs_changed']} changed, {s['pairs_added']} added, "
         f"{s['pairs_removed']} removed; policy moves: "
         f"{sh['named_landmark_64_to_standard_48']} to {POLICY_STANDARD}, "
         f"{sh['standard_48_to_named_landmark_64']} to {POLICY_NAMED}")
    if result["accepted_shift"]:
        _say("large shift accepted with --accept-shift: " + "; ".join(result["guards_tripped"]))
    _say(f"refresh record: {result['sidecar'].name}")
    return 0
