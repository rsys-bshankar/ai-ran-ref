#!/usr/bin/env python3
"""Package and instance lifecycle of the TX-muting rApp against Onboarding and rApp Management (see README.md section 10).

    python3 lcm.py onboard | prime | deploy | bootstrap | status | terminate | deprime | delete | retire | up | down

Runs inside the compose network (scripts/lcm.sh copies it and serves the CSAR). `up` = onboard, prime, deploy,
bootstrap; `down` = terminate, deprime, delete. Ids are kept in $LCM_STATE (default /tmp/tx-muting-lcm.json).
"""

import json
import os
import sys
import time

import httpx

CSAR_URL = os.environ.get("LCM_CSAR_URL", "http://r1-termination:8899/tx-muting-rapp.csar")
ONBOARDING, RAPP_MGMT = "http://onboarding:8000", "http://rapp-mgmt:8000"
STATE_FILE = os.environ.get("LCM_STATE", "/tmp/tx-muting-lcm.json")


def call(verb, url, expect=(200, 201, 202, 204), **kw):
    resp = getattr(httpx, verb)(url, timeout=60.0, **kw)
    if resp.status_code not in expect:
        raise SystemExit(f"{verb.upper()} {url} -> {resp.status_code}: {resp.text}")
    return resp.json() if resp.content else None


def show(label, value):
    print(f"  {label}: {value if isinstance(value, str) else json.dumps(value, default=str)}")


def need(state, key, step):
    if key not in state:
        raise SystemExit(f"no {key}: run `{step}` first")
    return state[key]


def onboard(state):
    """Onboard the CSAR: Onboarding fetches it, validates it and creates the NFO deployment descriptor."""
    state["packageId"] = call("post", f"{ONBOARDING}/packages", json={"location": CSAR_URL})["packageId"]
    for _ in range(20):
        status = call("get", f"{ONBOARDING}/packages/{state['packageId']}/onboarding-status")
        if status["state"] != "ONBOARDING":
            break
        time.sleep(1)
    show("onboarding", status)
    if status["state"] != "AVAILABLE":
        raise SystemExit("package did not become AVAILABLE: a byte-identical package is refused while the first is still onboarded (retire it, see README.md section 10)")


def prime(state):
    """Prime the package (AVAILABLE -> PRIMED)."""
    show("prime", call("post", f"{ONBOARDING}/packages/{need(state, 'packageId', 'onboard')}/prime"))


def deploy(state):
    """CreateInstance: rApp Management asks NFO to instantiate; the instance is DEPLOYING."""
    inst = call("post", f"{RAPP_MGMT}/instances", json={"packageId": need(state, "packageId", "onboard"), "config": {}, "autonomyMode": "AUTONOMOUS"})
    state["instanceId"] = inst["instanceId"]
    show("instance", inst)


def bootstrap(state):
    """Complete bootstrap, as the deployed container's callback does: DEPLOYING -> RUNNING."""
    show("instance", call("post", f"{RAPP_MGMT}/instances/{need(state, 'instanceId', 'deploy')}/bootstrap-complete"))


def status(state):
    """Package and instance state."""
    if "packageId" in state:
        show("package", call("get", f"{ONBOARDING}/packages/{state['packageId']}/onboarding-status"))
    if "instanceId" in state:
        show("instance", call("get", f"{RAPP_MGMT}/instances/{state['instanceId']}", expect=(200, 404)))


def terminate(state):
    """Terminate the instance (RUNNING -> UNDEPLOYED) and close its package usage."""
    show("instance", call("post", f"{RAPP_MGMT}/instances/{need(state, 'instanceId', 'deploy')}/terminate"))


def deprime(state):
    """Deprime the package (refused with 409 while an instance is deployed)."""
    show("deprime", call("post", f"{ONBOARDING}/packages/{need(state, 'packageId', 'onboard')}/deprime"))


def delete(state):
    """Delete the instance record."""
    call("delete", f"{RAPP_MGMT}/instances/{need(state, 'instanceId', 'deploy')}")
    state.pop("instanceId")
    show("instance", "deleted")


def retire(state):
    """Retire the package: AVAILABLE -> DEPRECATED -> DELETING (refused while an instance uses it). Also clears a FAILED one."""
    pid = need(state, "packageId", "onboard")
    pkg = call("get", f"{ONBOARDING}/packages/{pid}/onboarding-status")
    if pkg["state"] != "FAILED":
        call("post", f"{ONBOARDING}/packages/{pid}/deprecate")
    show("delete", call("delete", f"{ONBOARDING}/packages/{pid}"))
    state.pop("packageId")


STEPS = {f.__name__: f for f in (onboard, prime, deploy, bootstrap, status, terminate, deprime, delete, retire)}


def _chain(title, names):
    def run(state):
        for n in names:
            print(f"{n} - {STEPS[n].__doc__.splitlines()[0]}")
            STEPS[n](state)
            save(state)
    run.__doc__ = title
    return run


STEPS["up"] = _chain("onboard, prime, deploy, bootstrap", ("onboard", "prime", "deploy", "bootstrap"))
STEPS["down"] = _chain("terminate, deprime, delete", ("terminate", "deprime", "delete"))


def save(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1)


def main():
    cmds = sys.argv[1:]
    if not cmds or any(c not in STEPS for c in cmds):
        raise SystemExit(f"usage: lcm.py {' | '.join(STEPS)}")
    state = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
    for c in cmds:
        if c not in ("up", "down"):
            print(f"{c} - {STEPS[c].__doc__.splitlines()[0]}")
        try:
            STEPS[c](state)
        finally:
            save(state)


if __name__ == "__main__":
    main()
