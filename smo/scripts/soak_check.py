#!/usr/bin/env python3
"""Is anything growing during a soak? (PR-V-9b)

    scripts/soak_check.py soak-samples.csv [--growth 0.25]

Reads `<unix seconds>,<metric>,<value>` lines written by the soak workflow's sampler (container memory in MiB as `mem:<container>`, open file descriptors as
`fds:<container>`, database connections `pg_connections`, notifications waiting `outbox_pending`) and compares, for each metric, the mean of the first third of the
samples after a warm-up (the first tenth) with the mean of the last third. A metric fails when it grew by more than `--growth` (default 25 %) AND by more than a floor
for its kind (50 MiB, 50 descriptors, 10 connections, 100 notifications), so a small number that doubled is not a leak. Prints a table (markdown) and exits 1 on any failure.
"""

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

FLOORS = {"mem": 50.0, "fds": 50.0, "pg_connections": 10.0, "outbox_pending": 100.0}


def load(path: Path) -> dict[str, list[tuple[int, float]]]:
    """Reads the sampler's CSV into `{metric: [(unix seconds, value), ...]}`. Lines that do not have three fields or whose numbers do not parse are skipped silently."""
    series: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split(",")
        if len(parts) != 3:
            continue
        try:
            series[parts[1]].append((int(parts[0]), float(parts[2])))
        except ValueError:
            continue
    return series


def judge(series: dict[str, list[tuple[int, float]]], growth: float) -> tuple[list[dict], bool]:
    """Decides, per metric, whether it grew: returns `(rows, ok)`.

        The first tenth of the samples is dropped as warm-up, then the mean of the first third of the rest is compared with the mean of the last third. A metric is GROWING when
        the rise exceeds both the floor for its kind (`FLOORS`, by the part of the name before the colon) and `growth` as a fraction of the first mean (any rise over the
        floor when the first mean is 0). A metric with fewer than nine samples is reported as "too few samples" and makes `ok` false: a soak that could not measure
        something fails rather than passes.
    """
    rows, ok = [], True
    for metric in sorted(series):
        values = [v for _, v in sorted(series[metric])]
        n = len(values)
        # fewer than nine samples cannot leave three in each of the first and the last third once the warm-up tenth is dropped, so no verdict is possible
        if n < 9:
            rows.append({"metric": metric, "samples": n, "first": 0.0, "last": 0.0, "change": 0.0, "verdict": "too few samples"})
            ok = False
            continue
        values = values[n // 10:]                                   # the warm-up
        third = max(1, len(values) // 3)
        first, last = statistics.fmean(values[:third]), statistics.fmean(values[-third:])
        floor = FLOORS.get(metric.split(":")[0], 0.0)
        grew = last - first
        bad = grew > floor and (first == 0 or grew / first > growth)
        ok = ok and not bad
        rows.append({"metric": metric, "samples": n, "first": round(first, 1), "last": round(last, 1),
                     "change": round(100 * grew / first, 1) if first else 0.0, "verdict": "GROWING" if bad else "flat"})
    return rows, ok


def main() -> None:
    """Prints the verdict table and exits 0 when every metric is flat, 1 otherwise."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("samples")
    ap.add_argument("--growth", type=float, default=0.25)
    args = ap.parse_args()
    rows, ok = judge(load(Path(args.samples)), args.growth)
    print("| metric | samples | first third | last third | change % | verdict |\n|---|---:|---:|---:|---:|---|")
    for r in rows:
        print(f"| {r['metric']} | {r['samples']} | {r['first']} | {r['last']} | {r['change']} | {r['verdict']} |")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
