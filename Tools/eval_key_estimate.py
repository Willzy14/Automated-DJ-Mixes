"""Measure key estimates against a CSV of reference keys."""

from __future__ import annotations

import argparse
import csv
import sys
import time
from bisect import bisect_right
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from itertools import islice
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Source"))

from automated_dj_mixes.key_estimate import (
    DEFAULT_PROFILE,
    PROFILE_NAMES,
    estimate_key,
    from_camelot,
    to_camelot,
)

_FIELDS = (
    "path", "expected", "estimated", "camelot_expected", "camelot_estimated",
    "relation", "confidence", "margin", "seconds",
)
_WEIGHTS = {"exact": 1.0, "fifth": 0.5, "relative": 0.3, "parallel": 0.2, "other": 0.0}
_COMPATIBLE = {"exact", "fifth", "relative"}
_SKIPS = {
    "skipped_key": "unparseable expected key",
    "skipped_audio": "audio read or analysis failed",
}
_BOUNDS = (0.2, 0.4, 0.6, 0.8)


def _relation(expected: str | None, estimated: str | None) -> str:
    if expected is None or estimated is None:
        return "other"
    if expected == estimated:
        return "exact"
    step = (int(estimated[:-1]) - int(expected[:-1])) % 12
    if expected[-1] == estimated[-1]:
        return "fifth" if step in (1, 11) else "other"
    if step == 0:
        return "relative"
    # The parallel minor is three wheel positions back from its major.
    if step == (9 if expected[-1] == "B" else 3):
        return "parallel"
    return "other"


def classify_relation(expected: str, estimated: str | None) -> str:
    """Classify two key names. Fifths count in either direction."""
    return _relation(to_camelot(expected), to_camelot(estimated))


def _evaluate(
    item: tuple[str, str], *, csv_dir: Path, profile: str
) -> dict[str, str]:
    path, expected = item
    result = dict.fromkeys(_FIELDS, "")
    result["path"] = path
    started = time.perf_counter()
    reference = to_camelot(expected)
    result["expected"] = from_camelot(reference) or expected
    if reference is None:
        result["relation"] = "skipped_key"
    else:
        result["camelot_expected"] = reference
        try:
            if not path:
                raise ValueError("Missing audio path")
            estimate = estimate_key(csv_dir / path, profile=profile)
        except Exception:
            result["relation"] = "skipped_audio"
        else:
            predicted = to_camelot(estimate.key)
            result["estimated"] = from_camelot(predicted) or ""
            result["camelot_estimated"] = predicted or ""
            result["relation"] = _relation(reference, predicted)
            result["confidence"] = f"{estimate.confidence:.8f}"
            result["margin"] = f"{estimate.margin:.8f}"
    result["seconds"] = f"{time.perf_counter() - started:.3f}"
    return result


def _read_input(path: Path, limit: int | None) -> dict[str, str]:
    rows = {}
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not {"path", "expected_key"}.issubset(reader.fieldnames or ()):
            raise ValueError("Input CSV must have path and expected_key columns")
        for row in islice(reader, limit):
            rows.setdefault((row["path"] or "").strip(), (row["expected_key"] or "").strip())
    return rows


def _read_results(path: Path) -> dict[str, dict[str, str]]:
    rows = {}
    if not path.exists() or path.stat().st_size == 0:
        return rows
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        if tuple(reader.fieldnames or ()) != _FIELDS:
            raise ValueError("Wrong results CSV header. Choose a new --out or use --force.")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Incomplete results row at line {reader.line_num}")
            relation = row["relation"]
            if relation in _WEIGHTS:
                if not 0 <= float(row["confidence"]) <= 1:
                    raise ValueError(f"Invalid confidence at line {reader.line_num}")
            elif relation not in _SKIPS:
                raise ValueError(f"Invalid relation at line {reader.line_num}")
            rows[row["path"]] = row
    return rows


def _percent(numerator: int, denominator: int) -> str:
    return f"{100 * numerator / denominator:.1f}%" if denominator else "n/a"


def _summary(rows: list[dict[str, str]], resumed: int) -> None:
    scored = [row for row in rows if row["relation"] in _WEIGHTS]
    skipped = [row for row in rows if row["relation"] in _SKIPS]
    total = len(scored)
    counts = Counter(row["relation"] for row in scored)
    print(f"Scored {total} | skipped {len(skipped)} | resumed {resumed}")
    for row in skipped:
        reason = _SKIPS[row["relation"]]
        display_path = ascii(row["path"]) if row["path"] else "(blank)"
        print(f"  {reason}: {display_path}")
    for relation in _WEIGHTS:
        print(f"  {relation:8s} {counts[relation]:5d}  {_percent(counts[relation], total)}")
    weighted = sum(counts[relation] * weight for relation, weight in _WEIGHTS.items())
    if total:
        print(f"MIREX weighted score (symmetric fifths): {weighted / total:.4f}")
    else:
        print("MIREX weighted score (symmetric fifths): n/a")
    compatible = sum(counts[relation] for relation in _COMPATIBLE)
    print(f"Camelot-compatible: {_percent(compatible, total)}")

    bands = [Counter() for _ in range(5)]
    for row in scored:
        band = bisect_right(_BOUNDS, float(row["confidence"]))
        bands[band][row["relation"]] += 1
    print("\nConfidence band      N       Exact   Compatible")
    for index, band_counts in enumerate(bands):
        n = sum(band_counts.values())
        hits = sum(band_counts[relation] for relation in _COMPATIBLE)
        close = "]" if index == 4 else ")"
        interval = f"[{index / 5:.1f}, {(index + 1) / 5:.1f}{close}"
        print(f"{interval:<18}{n:>5}{_percent(band_counts['exact'], n):>12}{_percent(hits, n):>13}")


class _AsciiParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        super().error(message.encode("ascii", errors="backslashreplace").decode("ascii"))


def main(argv: list[str] | None = None) -> int:
    parser = _AsciiParser(prog="eval_key_estimate.py", description=__doc__)
    parser.add_argument("--csv", type=Path, required=True, help="Input UTF-8 CSV")
    parser.add_argument("--out", type=Path, required=True, help="Results CSV")
    parser.add_argument("--profile", choices=PROFILE_NAMES, default=DEFAULT_PROFILE)
    parser.add_argument("--limit", type=int, help="Use the first N input rows")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--force", action="store_true", help="Replace the results CSV")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be nonnegative")
    if args.workers < 1:
        parser.error("--workers must be positive")

    try:
        if args.csv.resolve() == args.out.resolve() or (
            args.out.exists() and args.csv.samefile(args.out)
        ):
            raise ValueError("Input and output must be different files")
        inputs = _read_input(args.csv, args.limit)
        completed = {} if args.force else _read_results(args.out)
        pending = [(path, key) for path, key in inputs.items() if path not in completed]
        resumed = len(inputs) - len(pending)
        if pending or not completed:
            mode = "a" if completed else "w"
            work = partial(_evaluate, csv_dir=args.csv.resolve().parent, profile=args.profile)
            needs_newline = False
            if mode == "a":
                with args.out.open("rb") as previous:
                    previous.seek(-1, 2)
                    needs_newline = previous.read(1) not in (b"\n", b"\r")
            with args.out.open(mode, encoding="utf-8", newline="") as output:
                if needs_newline:
                    output.write("\n")
                writer = csv.DictWriter(output, fieldnames=_FIELDS)
                if mode == "w":
                    writer.writeheader()
                    output.flush()
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    for row in pool.map(work, pending, buffersize=2 * args.workers):
                        writer.writerow(row)
                        output.flush()
                        completed[row["path"]] = row
        _summary([completed[path] for path in inputs], resumed)
    except (OSError, ValueError, csv.Error) as exc:
        print(f"Error: {ascii(str(exc))}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
