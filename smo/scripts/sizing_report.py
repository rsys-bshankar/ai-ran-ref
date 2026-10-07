#!/usr/bin/env python3
"""Turns `docker stats` samples taken during a load run into a sizing table (PR-OPS-9.2).

    scripts/sizing_report.py <stats.jsonl> [--out load-out] [--mem-headroom 2.0] [--request-headroom 1.25]

`stats.jsonl` is one JSON object per line, as `docker stats --no-stream --format '{{json .}}'` prints it, with a `t` (seconds since the start) added by the sampler
(`scripts/sample_stats.sh`). Per container the report has the peak memory, the median and p95 CPU while loaded (a core is 100 %), and a suggestion: memory request = peak
x `--request-headroom`, memory limit = peak x `--mem-headroom`, CPU request = p95 rounded up to 25 m. Those are the numbers the chart's `resources` should be set from, on a
runner of this size and at this load; `docs/SIZING.md` says how to scale them. Writes `<out>/sizing.json` and `<out>/sizing.md`.
"""

import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path

UNITS = {"b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3, "kb": 1000, "mb": 1000**2, "gb": 1000**3}


def to_bytes(text: str) -> int:
    match = re.fullmatch(r"\s*([0-9.]+)\s*([A-Za-z]+)\s*", text)
    if not match or match.group(2).lower() not in UNITS:
        raise ValueError(f"not a size: {text!r}")
    return int(float(match.group(1)) * UNITS[match.group(2).lower()])


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


def service_name(container: str) -> str:
    """`smo-r1-termination-1` -> `r1-termination` (the compose project prefix and the replica suffix off)."""
    return re.sub(r"^smo[-_]", "", re.sub(r"[-_]\d+$", "", container))


def round_up(value: float, step: float) -> float:
    return math.ceil(value / step) * step


def summarize(lines: list[str], mem_headroom: float, request_headroom: float) -> list[dict]:
    per: dict[str, dict[str, list]] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        sample = json.loads(line)
        entry = per.setdefault(service_name(sample["Name"]), {"mem": [], "cpu": []})
        entry["mem"].append(to_bytes(sample["MemUsage"].split("/")[0]))
        entry["cpu"].append(float(sample["CPUPerc"].rstrip("%")))
    rows = []
    for name, entry in sorted(per.items()):
        peak = max(entry["mem"])
        p95 = percentile(entry["cpu"], 0.95)
        mib = 1024**2
        rows.append({
            "service": name, "samples": len(entry["mem"]),
            "memPeakMiB": round(peak / mib, 1),
            "cpuMedianPercent": round(statistics.median(entry["cpu"]), 1), "cpuP95Percent": round(p95, 1),
            "suggest": {
                "memoryRequestMiB": int(round_up(peak * request_headroom / mib, 16)),
                "memoryLimitMiB": int(round_up(peak * mem_headroom / mib, 64)),
                "cpuRequestMilli": int(round_up(max(p95 * 10, 1), 25)),
            },
        })
    return rows


def markdown(rows: list[dict]) -> str:
    out = ["| Service | Samples | Memory peak (MiB) | CPU median (%) | CPU p95 (%) | Suggested memory request | Suggested memory limit | Suggested CPU request |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        s = r["suggest"]
        out.append(f"| {r['service']} | {r['samples']} | {r['memPeakMiB']} | {r['cpuMedianPercent']} | {r['cpuP95Percent']} | {s['memoryRequestMiB']}Mi | {s['memoryLimitMiB']}Mi | {s['cpuRequestMilli']}m |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stats")
    parser.add_argument("--out", default="load-out")
    parser.add_argument("--mem-headroom", type=float, default=2.0)
    parser.add_argument("--request-headroom", type=float, default=1.25)
    args = parser.parse_args(argv)
    rows = summarize(Path(args.stats).read_text().splitlines(), args.mem_headroom, args.request_headroom)
    if not rows:
        print("no samples", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "sizing.json").write_text(json.dumps(rows, indent=2) + "\n")
    (out / "sizing.md").write_text(markdown(rows))
    print(markdown(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
