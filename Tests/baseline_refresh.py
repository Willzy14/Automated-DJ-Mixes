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
    The hashes are over the raw bytes as stored on this machine (a Windows checkout
    has CRLF line endings, git stores LF), so they match another machine only when
    its files have the same bytes; the sidecar says so in `hash_basis` and records
    each input's line ending in `input_line_endings`.
  * The baseline itself is written to a temp file `<baseline>.<pid>.<token>.tmp` (the
    token is 8 random hex digits, so a process id the system hands out again can
    never collide with an earlier run's temp file) and swapped in with os.replace, so
    a failure leaves the old baseline intact. If the swap fails after the sidecar was
    written, the sidecar is renamed `*.not-applied`. A refresh that lands leaves no
    temp file behind, so a live record whose own temp file still exists did not land.
  * A large change is refused (see the limits below): pairs moving between the two
    named policies, pairs flipping between align and raise, pairs changing overlap
    policy in any way, too many pairs changed in any field, too many unexplained pairs
    added, or too many pairs removed. The guard names are policy_shift, status_flips,
    overlap_policy_changes, pairs_changed, pairs_added and pairs_removed. The sidecar
    names each guard that fired in `guards_tripped_names`.
  * Pairs added because of a new track are expected. An added pair is explained when
    its out or its in is a new track, one that is in no row of the old baseline (as an
    out or as an in). A remix is a different track from its original, so a remix added
    next to its original is a new track. Only the unexplained pairs, between two tracks
    the old baseline already had, count toward pairs_added. The diff summary keeps
    `pairs_added` as the total and adds `pairs_added_new_track` and
    `pairs_added_unexplained`. Replacing a track is not growth: when any old pair is
    removed (a rename, a swap for a newer version, a half rename, a stale row of the old
    name), no added pair is explained. `old_tracks_gone` counts the old tracks that are
    in no new row.
  * `--accept-shift` names what it accepts. `--accept-shift pairs_added,pairs_changed`
    accepts those guards only. Any other guard that fired still refuses, and the
    refusal says "not accepted:" and names them. `--accept-shift all` accepts every
    guard that fired. The bare `--accept-shift` works only when exactly one guard fired.
    With two or more it is refused. An unknown guard name is a usage error. When no
    guard fired the flag changes nothing. The sidecar records `guards_accepted` (the
    names, or ["all"], empty when nothing fired).
  * A refusal about the guards or the flag is a few short lines: the first is
    `refresh refused: <what is wrong>` (at most 70 characters), then one fact per line
    (each guard on its own line with its count, for example "pairs_added: 38 unexplained",
    then "nothing was written"), then the exact flag to run on a line of its own. A
    refusal writes nothing, so these counts are the only place to read why a guard fired.
  * A payload with a duplicate (out, in) pair is refused outright; no flag overrides
    that.
  * A refresh killed between its writes (power cut, taskkill) leaves its temp file
    and a record with a live name. The record carries the process id and the temp
    file's name (`pid`, `staged_temp`). A later refresh, once the guards have passed
    and both the record and its temp file are older than STALE_AFTER_SECONDS, renames
    such a record `*.not-applied` ONLY when its own temp file still exists and hashes
    to the record's `new_baseline_sha256`, then deletes the stale temp file, and says
    so in one line. A temp file that a record still points at is kept while that
    record is not yet judged, and when the rename failed (the next cleanup tries
    again). A record whose temp file was deleted by hand cannot be proved unlanded
    and is left alone. A refused refresh touches nothing.

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
import uuid
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
#: Refuse when this many pairs or MORE changed in ANY field (alignment_policy, a plan
#: spec, swap_progress, cue provenance, ...), or MORE than this fraction of the
#: compared pairs did. This is the broadest measure, and a rewiring step is SUPPOSED
#: to move some decisions, so it is set looser than the narrow guards above.
CHANGED_MAX_PAIRS = 25
CHANGED_MAX_FRACTION = 0.25
#: Refuse when this many pairs or MORE are new, or MORE than this fraction of the old
#: baseline's pairs are (a corpus that grew). Not applied to the very first baseline.
ADDED_MAX_PAIRS = 25
ADDED_MAX_FRACTION = 0.25

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

#: The guard names: the words before the colon in each `guard_trips` line, in the order
#: they are reported. `--accept-shift` takes these (or `all`).
GUARD_NAMES = ("policy_shift", "status_flips", "overlap_policy_changes",
               "pairs_changed", "pairs_added", "pairs_removed")

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
#: What the recorded hashes are taken over; written into every sidecar.
HASH_BASIS = "raw bytes as stored on this machine"
#: Inputs with these extensions are text, so a line ending is detected for them.
_TEXT_SUFFIXES = {".py", ".json", ".txt", ".md"}


class RefreshRefused(Exception):
    """A refresh that must not go ahead. str(exc) is what the console shows. Most
    refusals are one plain line. The refusals about the large-change guards and about
    `--accept-shift` are a few short lines: the first is `refresh refused: <what is
    wrong>` (at most 70 characters), then one fact per line, then the exact flag to run
    on a line of its own."""


def _refused(first, *lines):
    """A RefreshRefused whose text is `refresh refused: <first>` and then `lines`."""
    return RefreshRefused("\n".join(["refresh refused: " + first, *lines]))


@dataclasses.dataclass(frozen=True)
class RefreshArgs:
    reason: str
    reviewer: str | None
    #: False (no flag), True (the bare flag), "all", or a tuple of guard names.
    accept_shift: object


def _ascii(text):
    return str(text).encode("ascii", "replace").decode("ascii")


def _shown(text, width=40):
    """`text` as ASCII, cut to `width` characters, so one name cannot make a long line."""
    text = _ascii(text)
    return text if len(text) <= width else text[:width - 3] + "..."


def _accept_plan(accept_shift):
    """Read what `--accept-shift` / `accept_shift=` asks for: ("none", ()), ("bare", ()),
    ("all", ()) or ("names", <the named guards, in guard order>).

    Takes False or None (no flag), True (the bare flag), the string "all", a comma
    separated string of guard names, or an iterable of guard names (an empty one is no
    flag). An unknown name, an empty name, or "all" mixed with names raises
    RefreshRefused, even when no guard fired, so a typo is never silently harmless."""
    if accept_shift is None or accept_shift is False:
        return "none", ()
    if accept_shift is True:
        return "bare", ()
    if isinstance(accept_shift, str):
        parts = accept_shift.split(",")
    else:
        try:
            parts = list(accept_shift)
        except TypeError:
            raise _refused("bad accept_shift value",
                           "it must be False, True, all or guard names",
                           "nothing was written") from None
        if not parts:
            return "none", ()
    parts = [p.strip() if isinstance(p, str) else str(p) for p in parts]
    unknown = [p for p in dict.fromkeys(parts) if p and p != "all" and p not in GUARD_NAMES]
    problems = [f"unknown name '{_shown(p)}'" for p in unknown]
    if "" in parts:
        problems.append("an empty name")
    if "all" in parts and len(set(parts)) > 1:
        problems.append("all cannot be mixed with names")
    if problems:
        raise _refused("bad --accept-shift value",
                       *[f"  {p}" for p in problems],
                       "valid names:", *[f"  {g}" for g in (*GUARD_NAMES, "all")],
                       "nothing was written")
    if set(parts) == {"all"}:
        return "all", ()
    return "names", tuple(g for g in GUARD_NAMES if g in parts)


def _plan_value(plan):
    """The RefreshArgs.accept_shift value for a plan from _accept_plan."""
    kind, names = plan
    return {"none": False, "bare": True, "all": "all"}.get(kind, names)


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


_ACCEPT_SHIFT_HELP = (
    "Accept guards that fired. NAMES is a comma-separated list of guard names ("
    + ", ".join(GUARD_NAMES) + ") or the word all. "
    "Only the named guards are accepted; any other guard that fired still refuses. "
    "The bare flag works only when exactly one guard fired.")


def _build_parser():
    parser = _Parser(prog="test_alignment_baseline.py", add_help=False,
                     allow_abbrev=False)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--reason", default=None, action=_Once)
    parser.add_argument("--reviewer", default=None, action=_Once)
    parser.add_argument("--accept-shift", nargs="?", const=True, default=None,
                        action=_Once, dest="accept_shift", metavar="NAMES",
                        help=_ACCEPT_SHIFT_HELP)
    return parser


def parse_refresh_args(argv):
    """Parse the arguments after the script name. Raises RefreshRefused on any problem."""
    ns = _build_parser().parse_args(list(argv))
    reason = validate_reason(ns.reason)
    reviewer = (ns.reviewer or "").strip() or None
    return RefreshArgs(reason=reason, reviewer=reviewer,
                       accept_shift=_plan_value(_accept_plan(ns.accept_shift)))


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


def line_ending(data):
    """"crlf", "lf", "mixed" or "none" for the bytes of a text file."""
    crlf = data.count(b"\r\n")
    bare_lf = data.count(b"\n") - crlf
    if crlf == 0 and bare_lf == 0:
        return "none"
    if crlf and bare_lf:
        return "mixed"
    return "crlf" if crlf else "lf"


def input_line_endings(paths, root=None):
    """The detected line ending of every input, keyed like input_hashes: "crlf", "lf",
    "mixed" or "none" for text files, "binary" for other extensions, "missing" for a
    path that is not a file."""
    root_path = Path(root).resolve() if root is not None else None
    endings = {}
    for p in paths:
        p = Path(p)
        if not p.is_file():
            kind = "missing"
        elif p.suffix.lower() not in _TEXT_SUFFIXES:
            kind = "binary"
        else:
            kind = line_ending(p.read_bytes())
        endings[_label(p, root_path)] = kind
    return dict(sorted(endings.items()))


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
    """Refuse anything that is not a baseline payload, and a payload that lists the same
    (out, in) pair twice: the diff keeps only the last copy of a pair, so the rows
    written would not be the rows that were checked. No flag overrides this."""
    ok = isinstance(payload, dict) and isinstance(payload.get("rows"), list) and all(
        isinstance(r, dict) and isinstance(r.get("out"), str) and isinstance(r.get("in"), str)
        and "status" in r
        for r in payload["rows"])
    if not ok:
        raise RefreshRefused(
            f"refresh refused: {label} is not a baseline payload "
            "(a JSON object with a 'rows' list of out/in/status rows); nothing was written")
    seen, repeated = set(), []
    for r in payload["rows"]:
        key = (r["out"], r["in"])
        if key in seen:
            repeated.append(key)
        seen.add(key)
    if repeated:
        raise RefreshRefused(
            f"refresh refused: {label} lists {len(set(repeated))} (out, in) pair(s) more "
            f"than once (duplicate pairs), e.g. {_pair_id(repeated[0])[:90]}; "
            "nothing was written")


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

    `pairs_added` counts every new pair. It is split in two: `pairs_added_new_track`
    (the pair's out or in is a track that is in no row of the old baseline, so the pair
    is expected) and `pairs_added_unexplained` (both tracks were already in the old
    baseline). A track counts as old if it is an out or an in in any old row. A remix is
    a different track from its original. With no old baseline every track is new.
    Replacing a track is not growth: when any old pair is removed (a track gone from the
    new rows, a half rename, a stale row of the old name), no added pair is explained.
    `old_tracks_gone` counts the old tracks that are in no new row.
    """
    o, n = _rows_by_key(old), _rows_by_key(new)
    added = sorted(set(n) - set(o))
    removed = sorted(set(o) - set(n))
    common = sorted(set(o) & set(n))
    old_tracks = {track for key in o for track in key}
    new_tracks = {track for key in n for track in key}
    tracks_gone = len(old_tracks - new_tracks)
    # an old pair that is gone means a rename or a swap, not growth: nothing is explained
    added_new_track = [] if removed else [
        k for k in added if k[0] not in old_tracks or k[1] not in old_tracks]
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
        "pairs_added_new_track": len(added_new_track),
        "pairs_added_unexplained": len(added) - len(added_new_track),
        "old_tracks_gone": tracks_gone,
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
    (pairs whose overlap_policy changed in ANY way, e.g. to or from None),
    pairs_changed (25 pairs or more, or over 25 percent, changed in ANY field),
    pairs_added (25 pairs or more, or over 25 percent of the old baseline, are
    unexplained new pairs; not applied to the very first baseline) and pairs_removed
    (over 5 percent gone).
    A new pair is explained when its out or its in is a new track, one that is in no row
    of the old baseline. Only unexplained pairs, between two tracks the old baseline
    already had, count toward pairs_added. A summary without the split counts every
    added pair as unexplained. Replacing a track is not growth: when any old pair is
    removed, no added pair is explained.
    Each guard is checked on its own. `--accept-shift` names the guards it accepts (or
    `all`); a guard it does not name still refuses, and a bare `--accept-shift` works only
    when exactly one guard fired (see refresh_baseline).
    A sweeping change that is not a move between the two named policies is caught by
    status_flips, overlap_policy_changes or pairs_changed. Not a guard, and never
    overridable: a payload that lists the same (out, in) pair twice is refused as a
    duplicate by check_payload before any guard runs."""
    return [text for _, text, _ in _guard_findings(shift, summary)]


def _guard_findings(shift, summary):
    """(name, text, fact) for every guard that fired, in report order. `text` is the full
    reason that guard_trips returns. `fact` is the short count a refusal prints on the
    guard's own line, taken from the same numbers (for example "50 of 380 pairs")."""
    found = []
    compared = max(shift["pairs_compared"], 1)
    changes = summary.get("changed_by_field") or {}
    moved = shift["total_moved"]
    if _over(moved, compared, SHIFT_MAX_PAIRS, SHIFT_MAX_FRACTION):
        found.append(("policy_shift",
                      f"policy_shift: {moved} of {compared} pairs moved between "
                      f"{POLICY_NAMED} and {POLICY_STANDARD} (limits: under "
                      f"{SHIFT_MAX_PAIRS} pairs and under {SHIFT_MAX_FRACTION:.0%})",
                      f"{moved} of {compared} pairs"))
    flips = changes.get("status_flip", 0)
    if _over(flips, compared, STATUS_FLIP_MAX_PAIRS, STATUS_FLIP_MAX_FRACTION):
        found.append(("status_flips",
                      f"status_flips: {flips} of {compared} pairs changed between align "
                      f"and raise (limits: under {STATUS_FLIP_MAX_PAIRS} pairs and under "
                      f"{STATUS_FLIP_MAX_FRACTION:.0%})",
                      f"{flips} of {compared} pairs"))
    overlap = changes.get("overlap_policy", 0)
    if _over(overlap, compared, OVERLAP_CHANGE_MAX_PAIRS, OVERLAP_CHANGE_MAX_FRACTION):
        found.append(("overlap_policy_changes",
                      f"overlap_policy_changes: {overlap} of {compared} pairs changed "
                      f"overlap policy in some way (limits: under {OVERLAP_CHANGE_MAX_PAIRS} "
                      f"pairs and under {OVERLAP_CHANGE_MAX_FRACTION:.0%})",
                      f"{overlap} of {compared} pairs"))
    changed = summary.get("pairs_changed", 0)
    if _over(changed, compared, CHANGED_MAX_PAIRS, CHANGED_MAX_FRACTION):
        found.append(("pairs_changed",
                      f"pairs_changed: {changed} of {compared} pairs changed in some field "
                      f"(limits: under {CHANGED_MAX_PAIRS} pairs and under "
                      f"{CHANGED_MAX_FRACTION:.0%})",
                      f"{changed} of {compared} pairs"))
    old_pairs = summary["pairs_old"]
    added = summary.get("pairs_added_unexplained", summary.get("pairs_added", 0))
    if old_pairs and _over(added, old_pairs, ADDED_MAX_PAIRS, ADDED_MAX_FRACTION):
        found.append(("pairs_added",
                      f"pairs_added: {added} unexplained new pairs on top of {old_pairs} "
                      f"(pairs of new tracks do not count; limits: under {ADDED_MAX_PAIRS} "
                      f"pairs and under {ADDED_MAX_FRACTION:.0%} of the old baseline)",
                      f"{added} unexplained"))
    removed = summary["pairs_removed"]
    if old_pairs and removed / old_pairs > REMOVED_MAX_FRACTION:
        found.append(("pairs_removed",
                      f"pairs_removed: {removed} of {old_pairs} pairs are gone from the new "
                      f"baseline (limit: {REMOVED_MAX_FRACTION:.0%})",
                      f"{removed} of {old_pairs} pairs"))
    return found


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


def _staged_name(final):
    """A fresh temp file name for `final`: `<name>.<pid>.<8 random hex>.tmp`. The process
    id keeps two refreshes at once apart, and the random token keeps apart two processes
    that were handed the same id (Windows reuses them), so no temp file is ever
    overwritten, consumed or mistaken for another run's."""
    return f"{final.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"


def _stage_bytes(final, data, name=None):
    """Write `data` to a temp file beside `final` and return the temp path (not live yet).
    `name` is the temp file's name when the caller must record it first."""
    staged = final.with_name(name or _staged_name(final))
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


def _sha_or_missing(path):
    return sha256_file(path) if path.is_file() else "missing"


def _read_record(path):
    """(raw bytes, record dict or None) of a refresh record file."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None, None
    try:
        rec = json.loads(raw.decode("ascii"))
    except ValueError:
        return raw, None
    return raw, rec if isinstance(rec, dict) else None


def _record_temp_name(rec):
    """The name of the temp file a record says it staged, or None if it names none."""
    name = rec.get("staged_temp") if rec else None
    return name if isinstance(name, str) and name and Path(name).name == name else None


def _unlanded_record(path, baseline):
    """None for a refresh record that is not proved unlanded. Otherwise the proof: the
    name of the record's OWN staged temp file (`staged_temp`, beside the baseline) when
    it still exists and hashes to the record's `new_baseline_sha256`, or "" for an empty
    record (killed after its name was claimed). A refresh that lands moves its temp file
    onto the baseline, so a landed refresh has no temp file left, whatever happens to
    the baseline afterwards (a revert, a later refresh)."""
    raw, rec = _read_record(path)
    if raw is None:
        return None
    if not raw:
        return ""
    name = _record_temp_name(rec)
    new = rec.get("new_baseline_sha256") if rec else None
    if name is None or not isinstance(new, str):
        return None
    try:
        return name if _sha_or_missing(baseline.with_name(name)) == new else None
    except OSError:
        return None


def cleanup_interrupted(baseline, stale_after=STALE_AFTER_SECONDS, now=None):
    """Tidy up after a refresh that was killed between its writes.

    A hard kill (power cut, taskkill) skips every except/finally, so it can leave its
    full-size temp copy of the baseline and a refresh record under a normal live name
    for a baseline that never moved. Only files older than `stale_after` seconds are
    touched, so a refresh running right now is safe. Records are judged FIRST, because
    the proof that a record never landed is its own temp file (see _unlanded_record);
    then the stale temp files matching this baseline's own patterns are deleted. A
    record is judged only when BOTH it and its own temp file are stale (the temp file
    is always the older of the two). A temp file that a record still points at is
    never deleted while that record is unjudged, or when renaming it failed (Dropbox
    or an editor holding it open): it is the record's only proof, and a later cleanup
    gets another try. A record judged unlanded is renamed `*.not-applied`.
    Returns {"removed": [names], "marked": [names]}.
    """
    baseline = Path(baseline)
    now = time.time() if now is None else now
    base, stem = re.escape(baseline.name), re.escape(baseline.stem)
    stamp = r"\d{8}T\d{6}Z(?:-\d+)?"
    # `<name>.tmp` (round 1), `<name>.<pid>.tmp` (rounds 2-3) and `<name>.<pid>.<token>.tmp`
    temp_re = re.compile(
        rf"^(?:{base}|{stem}\.refresh-{stamp}\.json)(?:\.\d+(?:\.[0-9a-f]{{8}})?)?\.tmp$")
    record_re = re.compile(rf"^{stem}\.refresh-{stamp}\.json$")
    ages = {}
    for path in sorted(baseline.parent.iterdir()):
        try:
            if path.is_file():
                ages[path] = now - path.stat().st_mtime
        except OSError:
            continue
    removed, marked, protected = [], [], set()
    for path in ages:                                       # 1. the records, while their temp files exist
        if not record_re.match(path.name):
            continue
        _, rec = _read_record(path)
        name = _record_temp_name(rec)
        temp_age = ages.get(baseline.with_name(name)) if name else None
        if ages[path] < stale_after or (temp_age is not None and temp_age < stale_after):
            if name:
                protected.add(name)                         # unjudged: keep its proof
            continue
        proof = _unlanded_record(path, baseline)
        if proof is None:
            continue
        try:
            os.rename(path, path.with_name(path.name + ".not-applied"))
            marked.append(path.name)
        except OSError:
            if proof:
                protected.add(proof)                        # not renamed: keep the proof
    for path, age in ages.items():                          # 2. then the stale temp files
        if temp_re.match(path.name) and age >= stale_after and path.name not in protected:
            try:
                path.unlink()
                removed.append(path.name)
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


def _n_guards(count):
    return f"{count} guard" + ("" if count == 1 else "s")


def _guard_refusal(first, shown, tripped, summary, note=None):
    """The short refusal for guards that fired: `first` after "refresh refused: ", the
    guards in `shown` ((name, fact) pairs) one per line as "<name>: <count>", an optional
    `note`, the diff counts, "nothing was written", then the exact flag to run (and `all`
    when more than one guard fired). The counts are here because a refusal writes
    nothing: this is the only place Sam can read why a guard fired."""
    lines = [f"  {name}: {fact}" for name, fact in shown]
    if note:
        lines.append(note)
    if summary["pairs_added"] and summary["pairs_removed"]:
        lines.append("old pairs are gone: replacing a track is not growth")
    lines.append(f"diff: {summary['pairs_added']} added "
                 f"({summary['pairs_added_unexplained']} unexplained), "
                 f"{summary['pairs_removed']} removed, {summary['pairs_changed']} changed")
    lines += ["nothing was written", "To accept, run:",
              "--accept-shift " + ",".join(tripped)]
    if len(tripped) > 1:
        lines += ["Or accept every guard:", "--accept-shift all"]
    return _refused(first, *lines)


def _accepted_guards(plan, found, summary):
    """The guard names a refresh accepts, given the plan from _accept_plan, the
    (name, text, fact) tuples from _guard_findings and the diff summary. Raises
    RefreshRefused (a few short lines, see _guard_refusal) when a guard that fired is not
    covered. [] when nothing fired; ["all"] for `all`; otherwise the guards that fired,
    in guard order."""
    kind, names = plan
    if not found:
        return []
    tripped = [name for name, _, _ in found]
    facts = [(name, fact) for name, _, fact in found]
    if kind == "all":
        return ["all"]
    if kind == "none":
        raise _guard_refusal(f"{_n_guards(len(tripped))} tripped", facts, tripped, summary)
    if kind == "bare":
        if len(tripped) == 1:
            return tripped
        raise _guard_refusal(f"bare --accept-shift, {_n_guards(len(tripped))} tripped",
                             facts, tripped, summary,
                             note="a bare --accept-shift is for one guard only")
    missing = [pair for pair in facts if pair[0] not in names]
    if missing:
        raise _guard_refusal(f"not accepted: {_n_guards(len(missing))}", missing, tripped,
                             summary)
    return tripped


def refresh_baseline(baseline, payload, *, reason, reviewer=None, accept_shift=False,
                     input_paths=(), root=None, clock=None, head=None, notify=None):
    """Replace `baseline` with `payload` if the reason and the guards allow it.

    `accept_shift` says which guards that fired are accepted: False (none), True (the
    bare flag: only when exactly one guard fired), "all", or an iterable of guard names
    (GUARD_NAMES; a comma-separated string works too). A guard that fired and is not
    covered refuses the refresh ("not accepted"). An unknown name refuses it even when
    no guard fired. Duplicate pairs are never overridable.

    Order: validate -> diff and guard -> clean up after a killed refresh (`notify`
    gets one line if it found anything) -> stage the new baseline as a temp file ->
    write the sidecar -> os.replace the temp file over the baseline. Nothing at all is
    written or deleted before the guards pass, so a refused refresh changes no file;
    the sidecar exists before the baseline moves.
    Returns a dict (sidecar path, diff summary, policy shift, accepted_shift,
    guards_accepted, hashes).
    """
    plan = _accept_plan(accept_shift)
    baseline = Path(baseline)
    reason = validate_reason(reason)
    check_payload(payload, "the new baseline")
    old_payload, old_sha = _read_old(baseline)
    summary = diff_summary(old_payload, payload)
    shift = policy_shift(old_payload, payload)
    found = _guard_findings(shift, summary)
    trips = [text for _, text, _ in found]
    accepted = _accepted_guards(plan, found, summary)

    cleaned = cleanup_interrupted(baseline)
    if notify is not None and (cleaned["removed"] or cleaned["marked"]):
        notify(f"cleaned up after an interrupted refresh: removed "
               f"{len(cleaned['removed'])} leftover temp file(s), marked "
               f"{len(cleaned['marked'])} unfinished record(s) .not-applied")

    now = (clock or _utc_now)()
    data = _baseline_bytes(payload)
    new_sha = sha256_bytes(data)
    staged_name = _staged_name(baseline)         # made once: the record names the file really used
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
        "hash_basis": HASH_BASIS,
        "input_line_endings": input_line_endings(input_paths, root),
        "baseline_line_ending": line_ending(data),
        "pid": os.getpid(),
        "staged_temp": staged_name,
        "old_baseline_sha256": old_sha,
        "new_baseline_sha256": new_sha,
        "old_counts": _counts(old_payload),
        "new_counts": _counts(payload),
        "diff_summary": summary,
        "policy_shift": shift,
        "guards_tripped": trips,
        "guards_tripped_names": [t.split(":", 1)[0] for t in trips],
        "accept_shift_flag": plan[0] != "none",
        "accepted_shift": bool(trips),
        "guards_accepted": accepted,
        "guard_limits": {"shift_max_pairs": SHIFT_MAX_PAIRS,
                         "shift_max_fraction": SHIFT_MAX_FRACTION,
                         "removed_max_fraction": REMOVED_MAX_FRACTION,
                         "status_flip_max_pairs": STATUS_FLIP_MAX_PAIRS,
                         "status_flip_max_fraction": STATUS_FLIP_MAX_FRACTION,
                         "overlap_change_max_pairs": OVERLAP_CHANGE_MAX_PAIRS,
                         "overlap_change_max_fraction": OVERLAP_CHANGE_MAX_FRACTION,
                         "changed_max_pairs": CHANGED_MAX_PAIRS,
                         "changed_max_fraction": CHANGED_MAX_FRACTION,
                         "added_max_pairs": ADDED_MAX_PAIRS,
                         "added_max_fraction": ADDED_MAX_FRACTION},
    }

    staged = _stage_bytes(baseline, data, staged_name)
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
            "guards_accepted": accepted, "old_sha256": old_sha, "new_sha256": new_sha}


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
    code: 0 refreshed, 2 refused (a few short lines on stderr), 1 failed with the old
    baseline left in place."""
    baseline = Path(baseline)
    try:
        args = parse_refresh_args(argv)
        _read_old(baseline)                       # cheap sanity check before the slow step
        payload = build_payload()
        result = refresh_baseline(
            baseline, payload, reason=args.reason, reviewer=args.reviewer,
            accept_shift=args.accept_shift, input_paths=input_paths, root=root,
            clock=clock, head=head, notify=_say)
    except RefreshRefused as exc:
        _say(str(exc), sys.stderr)
        return 2
    except OSError as exc:
        why = str(exc)
        if baseline.exists() and not os.access(baseline, os.W_OK):
            why = ("the baseline file is read-only (clear its read-only flag and run "
                   "again); system error: " + why)
        _say(f"refresh failed, the old baseline is unchanged: {why}", sys.stderr)
        return 1
    n = len(payload["rows"])
    ok = sum(1 for r in payload["rows"] if r["status"] == "ok")
    s, sh = result["summary"], result["shift"]
    _say(f"baseline refreshed: {n} pairs ({ok} align, {n - ok} raise)")
    _say(f"diff: {s['pairs_changed']} changed, {s['pairs_added']} added "
         f"({s['pairs_added_new_track']} for new tracks, "
         f"{s['pairs_added_unexplained']} unexplained), "
         f"{s['pairs_removed']} removed; policy moves: "
         f"{sh['named_landmark_64_to_standard_48']} to {POLICY_STANDARD}, "
         f"{sh['standard_48_to_named_landmark_64']} to {POLICY_NAMED}")
    if result["accepted_shift"]:
        if result["guards_accepted"] == ["all"]:
            _say("all guards accepted (--accept-shift all)")
        else:
            _say("guards accepted: " + ", ".join(result["guards_accepted"]))
        for trip in result["guards_tripped"]:
            _say("  " + trip)
    _say(f"refresh record: {result['sidecar'].name}")
    return 0
