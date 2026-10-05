"""PR-DB-7.3 / PR-HA-3: write to the database through SME every 250 ms while the Postgres primary is taken away, and say how long writes failed.

Runs inside the SME pod (it holds the enrollment secret), started in the background by the CI job `postgres-ha`:

    kubectl -n smo exec deploy/sme -- python3 -c "$(cat scripts/k8s_failover_probe.py)" > probe.log &
    ... switch over / delete the primary ...
    kubectl -n smo exec deploy/sme -- touch /tmp/stop

Each attempt registers an invoker (a row written to Postgres) and deletes it again. It stops when /tmp/stop appears and prints one line:

    writes=N failed=F longest_gap_seconds=S recovered=yes|no

The gap is the time from the first failed attempt of a run of failures to the next success, so it is what a caller saw as the outage.
Exits 1 when writes did not recover, or when the longest gap is over FAILOVER_MAX_SECONDS (default 90).
"""
import os
import sys
import time

import httpx

SME = "http://sme:8000"
SECRET = open("/run/secrets/enrollment_secret", encoding="utf-8").read().strip()
LIMIT = float(os.environ.get("FAILOVER_MAX_SECONDS", "90"))
DEADLINE = time.monotonic() + 1800  # a probe that is never stopped does not run for ever


def write() -> bool:
    try:
        r = httpx.post(f"{SME}/invoker-registrations", json={"apiInvokerPublicKey": "ha-failover"},
                       headers={"X-SMO-Enrollment": SECRET}, timeout=5)
        if r.status_code != 201 and r.status_code != 200:
            return False
        httpx.delete(f"{SME}/invoker-registrations/{r.json()['apiInvokerId']}", timeout=5)
        return True
    except (httpx.HTTPError, ValueError, KeyError):
        return False


writes = failed = 0
gap_start: float | None = None
longest = 0.0
while not os.path.exists("/tmp/stop") and time.monotonic() < DEADLINE:
    writes += 1
    now = time.monotonic()
    if write():
        if gap_start is not None:
            longest = max(longest, time.monotonic() - gap_start)
            gap_start = None
    else:
        failed += 1
        if gap_start is None:
            gap_start = now
    time.sleep(0.25)

recovered = gap_start is None and writes > failed
print(f"writes={writes} failed={failed} longest_gap_seconds={longest:.1f} recovered={'yes' if recovered else 'no'}")
sys.exit(0 if recovered and longest <= LIMIT else 1)
