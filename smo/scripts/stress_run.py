#!/usr/bin/env python3
"""Stress and failure-injection checks for the SMO gateway (PR-V-9). One scenario per run; each states an invariant and exits 1 if it is broken.

    scripts/stress_run.py limiter                    a burst past the per-caller budget: 429 with Retry-After, never a 5xx, and the caller is served again after the wait
    scripts/stress_run.py oversize                   a body over the 1 MiB cap: 413 before the service reads it, and the gateway keeps answering
    scripts/stress_run.py saturate [--levels 20,50,100,200] [--seconds 15]
                                                     ramp the callers in flight (run with the rate limiter off): every answer is 200, or 503/429 with Retry-After; no hang, no other 5xx
    scripts/stress_run.py probe --expect down|up     a few calls to a database-backed route: `down` (the database is stopped) must be answered within --deadline s
                                                     with a 5xx (not a hang), `up` must be 200 within --wait s (recovery after the database comes back)

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


def fail(msg: str) -> int:
    print(f"FAIL: {msg}")
    return 1


async def limiter(client, args, headers) -> int:
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


async def oversize(client, args, headers) -> int:
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
        return await {"limiter": limiter, "oversize": oversize, "saturate": saturate, "probe": probe}[args.scenario](client, args, headers)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scenario", choices=["limiter", "oversize", "saturate", "probe"])
    ap.add_argument("--gateway", default="http://r1-termination:8000")
    ap.add_argument("--sme", default="http://sme:8000")
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--token-file", default="")
    ap.add_argument("--max-reset-rate", type=float, default=0.005, help="saturate: share of calls that may end in a connection reset (a kept-alive connection closed as it is reused)")
    ap.add_argument("--burst", type=int, default=100)
    ap.add_argument("--levels", default="20,50,100,200")
    ap.add_argument("--seconds", type=float, default=15)
    ap.add_argument("--expect", choices=["down", "up"], default="up")
    ap.add_argument("--deadline", type=float, default=20)
    ap.add_argument("--wait", type=float, default=90)
    sys.exit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
