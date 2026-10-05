"""PR-HA-4.2: a staged CM job survives the job runner being taken away, and goes on when it is back.

Runs inside the SME pod (it holds the enrollment secret, so it can take an internal token), in three calls by the CI job `helm`:

    kubectl -n smo exec -i deploy/sme -- python3 - start < scripts/k8s_staged_job_failover.py     # a three-wave job, paused 20 s between waves; prints its id
    ... scale the worker to 0, wait out the pause ...
    kubectl -n smo exec -i deploy/sme -- python3 - stalled JOB < scripts/k8s_staged_job_failover.py   # the job is where it was: nothing but the worker advances it
    ... scale the worker back ...
    kubectl -n smo exec -i deploy/sme -- python3 - finish JOB < scripts/k8s_staged_job_failover.py     # the job completes, every sub-change applied once

The state of the job is in the database (the wave it is at, when the next one is due), so any worker that runs afterwards picks it up (MSG-4.4).
Exits 1 on the first failure; removes what it made on `finish`.
"""
import sys
import time

import httpx

SME, R1 = "http://sme:8000", "http://r1-termination:8000"
SECRET = open("/run/secrets/enrollment_secret", encoding="utf-8").read().strip()
ELEMENTS = ["ha-job-du-1", "ha-job-du-2", "ha-job-du-3"]


def fail(message: str) -> None:
    print(f"FAIL {message}")
    sys.exit(1)


def token() -> str:
    reg = httpx.post(f"{SME}/invoker-registrations", json={"apiInvokerPublicKey": "ha-job"}, headers={"X-SMO-Enrollment": SECRET}, timeout=10)
    reg.raise_for_status()
    body = reg.json()
    resp = httpx.post(f"{SME}/oauth2/token", json={"grant_type": "client_credentials", "client_id": body["apiInvokerId"],
                                                    "client_secret": body["onboardingSecret"], "scope": "smo-internal"}, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


def call(method: str, path: str, **kw) -> httpx.Response:
    return httpx.request(method, f"{R1}/ran-nf-oam{path}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30, **kw)


def job(job_id: str) -> dict:
    r = call("GET", f"/config-jobs/{job_id}")
    if r.status_code != 200:
        fail(f"GET job -> {r.status_code} {r.text[:200]}")
    return r.json()


TOKEN = token()
step = sys.argv[1] if len(sys.argv) > 1 else ""

if step == "start":
    for ref in ELEMENTS:
        r = call("POST", "/o1-adaptor-endpoints", json={"managedElementRef": ref, "adaptorUri": "http://mock-o1-adaptor:8000/edit-config",
                                                         "protocolSupport": ["NETCONF"], "o1Protocol": "NETCONF", "entityType": "O-DU"})
        if r.status_code not in (200, 201, 409):
            fail(f"register {ref} -> {r.status_code} {r.text[:200]}")
        endpoint = r.json().get("endpointId") if r.status_code != 409 else None
        if endpoint:
            call("POST", f"/o1-adaptor-endpoints/{endpoint}/heartbeat")
    r = call("POST", "/config-jobs", json={"requestedBy": "ha-job", "scope": "cell", "waveSize": 1, "wavePauseSeconds": 20,
                                           "changes": [{"managedElementRef": ref, "attributeChanges": {"adminState": "UNLOCKED"}} for ref in ELEMENTS]})
    if r.status_code != 202:
        fail(f"POST config-job -> {r.status_code} {r.text[:200]}")
    view = job(r.json()["jobId"])
    if view["currentWave"] != 1 or view["waveCount"] != 3:
        fail(f"the job should be at wave 1 of 3 after the request, is at {view['currentWave']} of {view['waveCount']} ({view['status']})")
    print(r.json()["jobId"])

elif step == "stalled":
    view = job(sys.argv[2])
    if view["currentWave"] != 1 or view["status"] in ("COMPLETED", "FAILED"):
        fail(f"with no worker the job went on: wave {view['currentWave']}, {view['status']}")
    print(f"ok   the job is still at wave {view['currentWave']} of {view['waveCount']} ({view['status']}), the pause over, no worker running")

elif step == "finish":
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        view = job(sys.argv[2])
        if view["status"] in ("COMPLETED", "FAILED", "PARTIAL_SUCCESS"):
            break
        time.sleep(3)
    if view["status"] != "COMPLETED" or view["currentWave"] != 3:
        fail(f"the job did not complete after the worker came back: {view['status']}, wave {view['currentWave']} of {view['waveCount']}")
    states = sorted(s["status"] for s in view["subChanges"])
    if states != ["APPLIED"] * 3:
        fail(f"sub-changes: {states}")
    print("ok   the job completed all three waves after the worker was back, every sub-change applied once")

else:
    fail("usage: start | stalled JOB | finish JOB")
