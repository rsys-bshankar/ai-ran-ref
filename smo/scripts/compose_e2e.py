"""OI-2-compose-e2e: smoke-test the running docker-compose stack.

Runs INSIDE the r1-termination container (the only one that publishes a host
port, and the only vantage point from which module hostnames resolve), fed on
stdin by the CI job:

    docker compose exec -T r1-termination python3 - < scripts/compose_e2e.py

It replays the DEMO_RUNBOOK.md §1-§4 spine against the real containers:
every service answers, the sample CSAR onboards, an rApp instance deploys,
`/bootstrap` answers, and a token-gated call routes through R1 Termination.
The CSAR must already be served on r1-termination:8899 (the CI job does
this, as DEMO_RUNBOOK.md §1 does).

Exit status 0 only if every check passes; every failure is listed.
"""
import sys
import time

import httpx

# Services with their own GET /health (the three without one — gui-bff and
# the two mocks — are probed for "answers HTTP at all").
HEALTH = [
    "sme", "dme", "onboarding", "rapp-mgmt", "ran-nf-oam", "a1-related", "nfo",
    "focom", "mlmr", "aimgf", "mllf", "ran-analytics", "mdaf", "intent-service",
    "so-smos", "sa-smos", "energy-saving-rapp", "mobility-optimization-rapp",
    "coverage-optimization-rapp", "traffic-steering-rapp",
]
ANSWERS = ["mock-o1-adaptor", "gui-bff"]
CSAR_URL = "http://r1-termination:8899/hello-world-rapp.csar"

failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" -- {detail}"))
    if not ok:
        failures.append(name)


def wait_for(url: str, want_200: bool, timeout: float = 180.0) -> httpx.Response | None:
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=3.0)
            if r.status_code == 200 or not want_200:
                return r
            last = r.status_code
        except httpx.HTTPError as exc:
            last = repr(exc)
        time.sleep(2)
    print(f"  (gave up waiting on {url}: {last})")
    return None


# --- 1. every service is up -------------------------------------------------
for host in HEALTH:
    check(f"{host} /health", wait_for(f"http://{host}:8000/health", True) is not None)
for host in ANSWERS:
    check(f"{host} answers HTTP", wait_for(f"http://{host}:8000/", False) is not None)
check("r1-termination /health", wait_for("http://localhost:8000/health", True) is not None)

# --- 2. DEMO_RUNBOOK §2: onboard the sample CSAR ----------------------------
r = httpx.post("http://onboarding:8000/packages", json={"location": CSAR_URL}, timeout=30)
check("onboard POST /packages -> 202", r.status_code == 202, (r.status_code, r.text))
package_id = r.json().get("packageId") if r.status_code == 202 else None
state = None
for _ in range(30):
    if not package_id:
        break
    state = httpx.get(f"http://onboarding:8000/packages/{package_id}/onboarding-status", timeout=10).json().get("state")
    if state in ("AVAILABLE", "FAILED"):
        break
    time.sleep(1)
check("package reaches AVAILABLE", state == "AVAILABLE", state)

# --- 3. DEMO_RUNBOOK §3: deploy it -----------------------------------------
if package_id and state == "AVAILABLE":
    r = httpx.post("http://rapp-mgmt:8000/instances", json={"packageId": package_id, "config": {}}, timeout=30)
    check("rapp-mgmt POST /instances -> 2xx", r.is_success, (r.status_code, r.text))
    if r.is_success:
        check("instance has an oauthClientId", bool(r.json().get("oauthClientId")), r.json())

# --- 4. DEMO_RUNBOOK §4: bootstrap + an R1-routed, token-gated call ---------
r = httpx.get("http://r1-termination:8000/bootstrap", timeout=10)
check("R1 /bootstrap -> 200", r.status_code == 200, r.status_code)
if r.status_code == 200:
    check("bootstrap names service-apis", "service-apis" in r.text, r.text[:200])
r = httpx.get("http://r1-termination:8000/sme/health", timeout=10)
check("R1 rejects an unauthenticated routed call", r.status_code in (401, 403), r.status_code)

print()
if failures:
    print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("all compose e2e checks passed")
