#!/usr/bin/env python3
"""Load runner for the SMO stack (PR-V-8): a weighted mix of the main routes through the R1 gateway, at a fixed concurrency for a fixed time.

    scripts/load_run.py [--gateway http://r1-termination:8000] [--sme http://sme:8000] [--duration 30] [--concurrency 20] [--warmup 5] [--out load-results]

Runs on the compose network (the workflow runs it in a one-shot container, so the load does not share a container's CPU with the stack): it registers one invoker with the
enrollment secret (`/run/secrets/enrollment_secret`, an SMO module), takes a token, and each worker then picks a route by weight and calls it as fast as the answer comes
back (a closed loop: the concurrency is the number of calls in flight). Per route it reports requests, requests/s, p50 / p95 / p99 / max latency in ms and the error count
(status 5xx, or not the one expected); overall too. Writes `<out>/load-results.json` and `<out>/load-results.md` (a table for the job summary and docs/PERFORMANCE.md).

Exit status: 1 if any route answered a 5xx or the unexpected status more than `--max-error-rate` of the time (default 0), else 0. The numbers are the stack's on that runner:
GitHub's runners vary, so they are compared run against run on the same kind of runner, not against an absolute.
"""

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

# (name, method, path, weight, expected statuses). All through the gateway, as an SMO module (the internal scope), the way the modules and the GUI backend call it.
ROUTES = [
    ("service discovery", "GET", "/sme/service-apis/v1/allServiceAPIs", 10, (200,)),
    ("alarm list", "GET", "/ran-nf-oam/alarms?limit=50", 15, (200,)),
    ("kpi definitions", "GET", "/ran-nf-oam/kpi-definitions", 10, (200,)),
    ("config job list", "GET", "/ran-nf-oam/config-jobs?limit=20", 10, (200,)),
    ("rApp instances", "GET", "/rapp-mgmt/instances", 10, (200,)),
    ("package list", "GET", "/onboarding/packages", 10, (200,)),
    ("data types", "GET", "/dme/dme-types", 10, (200,)),
    ("config job, dry run", "POST", "/ran-nf-oam/config-jobs", 10, (200,)),
    ("token", "TOKEN", "/sme/oauth2/token", 5, (200,)),
]
DRY_RUN = {"requestedBy": "load", "scope": "cell", "dryRun": True, "changes": []}


@dataclass
class Series:
    latencies_ms: list[float] = field(default_factory=list)
    errors: int = 0
    statuses: dict[int, int] = field(default_factory=dict)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


async def register(client: httpx.AsyncClient, sme: str) -> dict:
    secret = Path("/run/secrets/enrollment_secret").read_text(encoding="utf-8").strip()
    resp = await client.post(f"{sme}/invoker-registrations", json={"apiInvokerPublicKey": "load"}, headers={"X-SMO-Enrollment": secret})
    resp.raise_for_status()
    return resp.json()


async def get_token(client: httpx.AsyncClient, sme: str, reg: dict) -> httpx.Response:
    return await client.post(f"{sme}/oauth2/token", json={"grant_type": "client_credentials", "client_id": reg["apiInvokerId"],
                                                          "client_secret": reg["onboardingSecret"], "scope": "smo-internal"})


async def one_call(client: httpx.AsyncClient, gateway: str, sme: str, reg: dict, access: str, route) -> int:
    name, method, path, _, _ = route
    if method == "TOKEN":
        return (await get_token(client, sme, reg)).status_code
    headers = {"Authorization": f"Bearer {access}"}
    if method == "POST":
        headers["Idempotency-Key"] = str(uuid.uuid4())
        return (await client.post(f"{gateway}{path}", json=DRY_RUN, headers=headers)).status_code
    return (await client.get(f"{gateway}{path}", headers=headers)).status_code


async def worker(client, gateway, sme, reg, access, deadline: float, record_from: float, series: dict[str, Series], weights, rng: random.Random):
    while time.monotonic() < deadline:
        route = rng.choices(ROUTES, weights=weights)[0]
        started = time.monotonic()
        try:
            status = await one_call(client, gateway, sme, reg, access, route)
        except httpx.HTTPError:
            status = 0
        elapsed = (time.monotonic() - started) * 1000
        if started < record_from:
            continue                                          # warm-up: connections, caches, the pool
        s = series[route[0]]
        s.latencies_ms.append(elapsed)
        s.statuses[status] = s.statuses.get(status, 0) + 1
        if status not in route[4]:
            s.errors += 1


def summarise(series: dict[str, Series], seconds: float) -> dict:
    rows, all_latencies, all_errors = [], [], 0
    for route in ROUTES:
        s = series[route[0]]
        n = len(s.latencies_ms)
        rows.append({"route": route[0], "requests": n, "rps": round(n / seconds, 1), "p50": round(percentile(s.latencies_ms, 0.50), 1),
                     "p95": round(percentile(s.latencies_ms, 0.95), 1), "p99": round(percentile(s.latencies_ms, 0.99), 1),
                     "max": round(max(s.latencies_ms), 1) if s.latencies_ms else 0.0, "errors": s.errors, "statuses": s.statuses})
        all_latencies += s.latencies_ms
        all_errors += s.errors
    total = {"route": "all", "requests": len(all_latencies), "rps": round(len(all_latencies) / seconds, 1), "p50": round(percentile(all_latencies, 0.50), 1),
             "p95": round(percentile(all_latencies, 0.95), 1), "p99": round(percentile(all_latencies, 0.99), 1),
             "max": round(max(all_latencies), 1) if all_latencies else 0.0, "errors": all_errors, "statuses": {}}
    return {"routes": rows, "total": total, "mean_ms": round(statistics.fmean(all_latencies), 1) if all_latencies else 0.0}


def markdown(result: dict, args) -> str:
    lines = [f"Load: {args.concurrency} callers in flight for {args.duration} s after a {args.warmup} s warm-up, through the gateway as an SMO module.", "",
             "| route | requests | req/s | p50 ms | p95 ms | p99 ms | max ms | errors |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in result["routes"] + [result["total"]]:
        lines.append(f"| {row['route']} | {row['requests']} | {row['rps']} | {row['p50']} | {row['p95']} | {row['p99']} | {row['max']} | {row['errors']} |")
    return "\n".join(lines) + "\n"


async def main_async(args) -> int:
    limits = httpx.Limits(max_connections=args.concurrency + 5, max_keepalive_connections=args.concurrency + 5)
    async with httpx.AsyncClient(limits=limits, timeout=args.timeout) as client:
        reg = await register(client, args.sme)
        token = await get_token(client, args.sme, reg)
        token.raise_for_status()
        access = token.json()["access_token"]
        series = {route[0]: Series() for route in ROUTES}
        weights = [route[3] for route in ROUTES]
        start = time.monotonic()
        deadline = start + args.warmup + args.duration
        rng = random.Random(args.seed)  # noqa: S311 — picks a route for the load, not a secret
        await asyncio.gather(*[worker(client, args.gateway, args.sme, reg, access, deadline, start + args.warmup, series, weights, random.Random(rng.random()))  # noqa: S311
                               for _ in range(args.concurrency)])
    result = summarise(series, args.duration)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "load-results.json").write_text(json.dumps({"concurrency": args.concurrency, "duration": args.duration, **result}, indent=1))
    table = markdown(result, args)
    (out / "load-results.md").write_text(table)
    print(table)
    rate = result["total"]["errors"] / max(1, result["total"]["requests"])
    if rate > args.max_error_rate:
        print(f"FAIL: {result['total']['errors']} of {result['total']['requests']} calls were errors ({rate:.2%} > {args.max_error_rate:.2%}); statuses per route are in load-results.json", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gateway", default="http://r1-termination:8000")
    ap.add_argument("--sme", default="http://sme:8000")
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--warmup", type=float, default=5)
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--max-error-rate", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=os.environ.get("LOAD_OUT", "load-results"))
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
