#!/usr/bin/env python3
"""Stress and failure-injection checks for the SMO gateway (PR-V-9). One scenario per run; each states an invariant and exits 1 if it is broken.

    scripts/stress_run.py limiter                    a burst past the per-caller budget: 429 with Retry-After, never a 5xx, and the caller is served again after the wait
    scripts/stress_run.py limiter-shared --budget shared|per-replica
                                                     one burst through several gateway replicas (docker-compose.stress-shared.yml, a budget of --rate-burst and --rate-per-second): with `shared`
                                                     (R1_RATE_STORE=postgres) about one budget is let through; with `per-replica` (the default store, the control that shows the check can tell) more than one budget is let through
    scripts/stress_run.py oversize                   a body over the 1 MiB cap: 413 before the service reads it, and the gateway keeps answering
    scripts/stress_run.py saturate [--levels 20,50,100,200] [--seconds 15]
                                                     ramp the callers in flight (run with the rate limiter off): every answer is 200, or 503/429 with Retry-After; no hang, no other 5xx
    scripts/stress_run.py probe --expect down|up     a few calls to a database-backed route: `down` (the database is stopped) must be answered within --deadline s
                                                     with a 5xx (not a hang; with --down-status 503, exactly 503 and a Retry-After: SME stopped), `up` must be 200 within --wait s (recovery after the dependency comes back)

Runs in a one-shot container on the compose network, like load_run.py (same registration and token). The workflow stops and starts containers between probes.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

import httpx
from load_run import get_token, register

LIST = "/ran-nf-oam/alarms?limit=5"


async def session(client: httpx.AsyncClient, args) -> dict[str, str]:
    """A token, kept in `--token-file` so a later run (with the database stopped, when no one can register) reuses it."""
    cache = Path(args.token_file) if args.token_file else None
    if cache is not None and cache.exists():
        return {"Authorization": f"Bearer {cache.read_text(encoding='utf-8').strip()}"}
    reg = await register(client, args.sme)
    token = await get_token(client, args.sme, reg)
    token.raise_for_status()
    access = token.json()["access_token"]
    if cache is not None:
        cache.write_text(access, encoding="utf-8")
    return {"Authorization": f"Bearer {access}"}


def shared_verdict(allowed: int, elapsed: float, rate_burst: int, rate_per_second: float, slack: int, shared: bool) -> str | None:
    """None when the number of calls let through says what the store should: at most one budget (the burst plus what refilled while the calls ran, plus `slack`) when the replicas
    share it; more than that when each keeps its own (the control: if it does not show, the calls did not reach more than one replica and the check cannot tell the two apart)."""
    one_budget = rate_burst + rate_per_second * elapsed + slack
    if shared:
        if allowed > one_budget:
            return f"{allowed} calls were let through in {elapsed:.1f} s, more than one shared budget ({one_budget:.0f}): the replicas keep their own buckets"
        if allowed < rate_burst / 2:
            return f"only {allowed} calls were let through, far below the budget of {rate_burst}: the shared store refuses what it should allow"
        return None
    if allowed <= one_budget:
        return f"the control let {allowed} calls through, no more than one budget ({one_budget:.0f}) although each replica keeps its own: the check cannot tell the two apart (did the calls reach one replica?)"
    return None


def fail(msg: str) -> int:
    """Prints `FAIL: <msg>` and returns 1, so a scenario can `return fail(...)`."""
    print(f"FAIL: {msg}")
    return 1


async def limiter(client, args, headers) -> int:
    """Scenario `limiter`: sends `--burst` GETs at once; passes when none is a 5xx or unanswered, at least one is a 429, every 429 has a `Retry-After` of 1 s or more,
        and a call after waiting the longest `Retry-After` plus a second is 200 again.

        Needs the gateway started with the small rate budget of `docker-compose.stress-limiter.yml`; against the default budget no call is limited and the scenario fails.
    """
    results = await asyncio.gather(*[client.get(f"{args.gateway}{LIST}", headers=headers) for _ in range(args.burst)], return_exceptions=True)
    codes: dict[int, int] = {}
    for r in results:
        code = r.status_code if isinstance(r, httpx.Response) else 0
        codes[code] = codes.get(code, 0) + 1
    print(f"limiter: {args.burst} calls at once -> {dict(sorted(codes.items()))}")
    limited = [r for r in results if isinstance(r, httpx.Response) and r.status_code == 429]
    if any(c >= 500 or c == 0 for c in codes):
        return fail("a call got a 5xx or no answer under the burst")
    if not limited:
        return fail("no call was rate limited: is the gateway started with docker-compose.stress-limiter.yml (a budget of 20 and 5 a second), and the burst larger than that?")
    if any(int(r.headers.get("Retry-After", "0")) < 1 for r in limited):
        return fail("a 429 came without a usable Retry-After")
    wait = max(int(r.headers["Retry-After"]) for r in limited)
    await asyncio.sleep(wait + 1)
    after = await client.get(f"{args.gateway}{LIST}", headers=headers)
    if after.status_code != 200:
        return fail(f"after waiting Retry-After ({wait} s) the caller got {after.status_code}, not 200")
    print(f"limiter: OK, {len(limited)} limited, served again after {wait + 1} s")
    return 0


async def limiter_shared(client, args, headers) -> int:
    """Scenario `limiter-shared`: one burst through the gateway replicas, judged by `shared_verdict` on how many calls were let through (status 200).

        A 5xx or an unanswered call fails it first. `--budget shared` expects about one budget; `--budget per-replica` is the control that must let more through.
    """
    started = time.monotonic()
    results = await asyncio.gather(*[client.get(f"{args.gateway}{LIST}", headers=headers) for _ in range(args.burst)], return_exceptions=True)
    elapsed = time.monotonic() - started
    codes: dict[int, int] = {}
    for r in results:
        code = r.status_code if isinstance(r, httpx.Response) else 0
        codes[code] = codes.get(code, 0) + 1
    print(f"limiter-shared ({args.budget}): {args.burst} calls at once in {elapsed:.1f} s -> {dict(sorted(codes.items()))}")
    if any(c >= 500 or c == 0 for c in codes):
        return fail("a call got a 5xx or no answer under the burst")
    problem = shared_verdict(codes.get(200, 0), elapsed, args.rate_burst, args.rate_per_second, args.slack, args.budget == "shared")
    if problem:
        return fail(problem)
    print(f"limiter-shared ({args.budget}): OK, {codes.get(200, 0)} let through")
    return 0


async def oversize(client, args, headers) -> int:
    """Scenario `oversize`: posts a 3 MiB body (the cap is 1 MiB); passes when the answer is 413, or the connection is ended during the upload (an early 413 can reach a client
        that is still sending that way), and the next ordinary call is served with 200.
    """
    body = b"x" * (3 * 1024 * 1024)
    try:
        resp = await client.post(f"{args.gateway}/ran-nf-oam/config-jobs", content=body, headers={**headers, "Content-Type": "application/json"})
    except httpx.TransportError as exc:
        # the cap is enforced before the service reads the body: the server may end the exchange while the client is still sending
        print(f"oversize: the connection was ended during the upload ({type(exc).__name__}), the way an early 413 reaches a client that is still sending")
    else:
        if resp.status_code != 413:
            return fail(f"a 3 MiB body got {resp.status_code}, not 413")
    after = await client.get(f"{args.gateway}{LIST}", headers=headers)
    if after.status_code != 200:
        return fail(f"after the oversized body the gateway answered {after.status_code}")
    print("oversize: OK, refused, and the next call is served")
    return 0


async def hammer(client, url: str, headers: dict, deadline: float, codes: dict[int, int], problems: list[str]) -> None:
    """One caller of the `saturate` scenario: GETs `url` back to back until `deadline`, counting each status in `codes` (0 for a transport error).

        A 429 or 503 without `Retry-After`, and every transport error, add a line to `problems`.
    """
    while time.monotonic() < deadline:
        try:
            r = await client.get(url, headers=headers)
            code = r.status_code
            if code in (429, 503) and "Retry-After" not in r.headers:
                problems.append(f"{code} without Retry-After")
        except httpx.HTTPError as exc:
            code = 0
            problems.append(type(exc).__name__)
        codes[code] = codes.get(code, 0) + 1


async def saturate(client, args, headers) -> int:
    """Scenario `saturate`: for each level in `--levels`, holds that many callers in flight for `--seconds` and checks the answers.

        Only 200, 429 and 503 are acceptable statuses, a 429 or 503 must carry `Retry-After`, and nothing may hang. Connection resets (status 0) are counted apart: a few are
        tolerated, up to `--max-reset-rate` of the calls, because a kept-alive connection closed by the server as it is reused shows as a reset; the two reset error classes are
        taken out of `problems` and judged by that rate instead. Returns 1 if any level had a violation.
    """
    bad = 0
    for level in [int(x) for x in args.levels.split(",")]:
        codes: dict[int, int] = {}
        problems: list[str] = []
        deadline = time.monotonic() + args.seconds

        await asyncio.gather(*[hammer(client, f"{args.gateway}{LIST}", headers, deadline, codes, problems) for _ in range(level)])
        total = sum(codes.values()) or 1
        resets = codes.pop(0, 0)
        problems = [p for p in problems if p != "ReadError" and p != "RemoteProtocolError"] + (["connection resets above --max-reset-rate"] if resets / total > args.max_reset_rate else [])
        if resets:
            print(f"saturate: {resets} of {total} calls ended in a connection reset ({100 * resets / total:.2f} %, the limit is {100 * args.max_reset_rate:.2f} %)")
        wrong = {c: n for c, n in codes.items() if c not in (200, 429, 503)}
        print(f"saturate: {level} in flight for {args.seconds:.0f} s -> {dict(sorted(codes.items()))}")
        if wrong:
            bad += 1
            print(f"FAIL: at {level} in flight, unexpected answers {wrong}")
        if problems:
            bad += 1
            print(f"FAIL: at {level} in flight: {sorted(set(problems))}")
    return 1 if bad else 0


async def probe(client, args, headers) -> int:
    """Scenario `probe`: `--expect down` makes three calls to a database-backed route while the dependency is stopped, each of which must be answered within `--deadline` s
        with a 5xx (exactly `--down-status` when given; a 503 needs `Retry-After`), not a hang or a success.

        `--expect up` polls every 2 s for up to `--wait` s until the route answers 200 again. The workflow stops and starts the containers between the probes.
    """
    started = time.monotonic()
    if args.expect == "down":
        for _ in range(3):
            t = time.monotonic()
            try:
                r = await client.get(f"{args.gateway}{LIST}", headers=headers, timeout=args.deadline)
            except httpx.HTTPError as exc:
                return fail(f"with the database down a call got no answer within {args.deadline} s ({type(exc).__name__}): a hang, not an error")
            print(f"probe down: {r.status_code} in {time.monotonic() - t:.1f} s")
            if r.status_code < 500:
                return fail(f"with the database down a database-backed route answered {r.status_code}")
            if args.down_status and r.status_code != args.down_status:
                return fail(f"with the dependency down the gateway answered {r.status_code}, not {args.down_status}")
            if r.status_code == 503 and int(r.headers.get("Retry-After", "0")) < 1:
                return fail("a 503 came without a usable Retry-After")
        return 0
    while time.monotonic() - started < args.wait:
        try:
            r = await client.get(f"{args.gateway}{LIST}", headers=headers, timeout=10)
            if r.status_code == 200:
                print(f"probe up: 200 after {time.monotonic() - started:.0f} s")
                return 0
        except httpx.HTTPError:
            pass
        await asyncio.sleep(2)
    return fail(f"the gateway did not recover to 200 within {args.wait} s of the database coming back")


async def main_async(args) -> int:
    async with httpx.AsyncClient(timeout=args.timeout, limits=httpx.Limits(max_connections=400, max_keepalive_connections=400)) as client:
        headers = await session(client, args)
        return await {"limiter": limiter, "limiter-shared": limiter_shared, "oversize": oversize, "saturate": saturate, "probe": probe}[args.scenario](client, args, headers)


def main() -> None:
    """Command-line entry: parses the options, runs the chosen scenario and exits with its status (0 passed, 1 broke the invariant)."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scenario", choices=["limiter", "limiter-shared", "oversize", "saturate", "probe"])
    ap.add_argument("--gateway", default="http://r1-termination:8000")
    ap.add_argument("--sme", default="http://sme:8000")
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--token-file", default="")
    ap.add_argument("--max-reset-rate", type=float, default=0.005, help="saturate: share of calls that may end in a connection reset (a kept-alive connection closed as it is reused)")
    ap.add_argument("--burst", type=int, default=100)
    ap.add_argument("--budget", choices=["shared", "per-replica"], default="shared", help="limiter-shared: what the replicas are started with (R1_RATE_STORE=postgres, or the default store)")
    ap.add_argument("--rate-burst", type=int, default=20, help="limiter-shared: the gateway's R1_RATE_BURST")
    ap.add_argument("--rate-per-second", type=float, default=5, help="limiter-shared: the gateway's R1_RATE_PER_SECOND")
    ap.add_argument("--slack", type=int, default=8, help="limiter-shared: calls more than one budget that are still taken to be one (timing of the refill)")
    ap.add_argument("--levels", default="20,50,100,200")
    ap.add_argument("--seconds", type=float, default=15)
    ap.add_argument("--expect", choices=["down", "up"], default="up")
    ap.add_argument("--down-status", type=int, default=0, help="probe down: the one status the gateway must answer (0: any 5xx); SME down is 503")
    ap.add_argument("--deadline", type=float, default=20)
    ap.add_argument("--wait", type=float, default=90)
    sys.exit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
