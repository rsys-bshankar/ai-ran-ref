#!/usr/bin/env python3
"""TX-muting rApp demo: steps 00-06 against a running stack (see README, Run and test).

    python3 demo.py 00        # one step
    python3 demo.py all       # every step in order

Run it inside the compose network (from the r1-termination container; scripts/run_demo.sh does that). It calls each
service by hostname, the way DEMO_RUNBOOK.md does. All network data comes from the O1 adaptor simulator, exactly as
you can trigger it by hand with its CLI (scripts/cli.sh): the demo only calls the same control routes. Ids are
kept between steps in $DEMO_STATE (default /tmp/tx-muting-demo.json).
"""

import json
import os
import sys

import httpx

ME = os.environ.get("DEMO_ME", "tx-muting-me-001")  # must equal ADAPTOR_ME of the simulator
CELL = os.environ.get("DEMO_CELL", "101")
MFR = f"NRCellDU={CELL}"
RAPP = os.environ.get("DEMO_RAPP_URL", "http://tx-muting-rapp:8000")
ADAPTOR = os.environ.get("DEMO_ADAPTOR_URL", "http://o1-adaptor-sim:8000")
STATE_FILE = os.environ.get("DEMO_STATE", "/tmp/tx-muting-demo.json")
TX_LEAVES = ("txMutingFeatureEnable", "txPathOffPattern", "txMutingActivation")


def call(verb: str, base: str, path: str, expect=(200, 201, 202, 204), **kw):
    if not base.startswith("http"):
        base = f"http://{base}:8000"
    resp = getattr(httpx, verb)(f"{base}{path}", timeout=120.0, **kw)
    if resp.status_code not in expect:
        raise SystemExit(f"{verb.upper()} {base}{path} -> {resp.status_code}: {resp.text}")
    return resp.json() if resp.content else None


def show(label: str, value) -> None:
    print(f"  {label}: {value if isinstance(value, str) else json.dumps(value, default=str)}")


def pm(prb: float, ue: int) -> None:
    """The adaptor reports one sample per counter to RAN NF OAM, time-stamped now."""
    call("post", ADAPTOR, "/control/counters", json={"cellId": CELL, "counters": {
        "DL_PRB_UTILIZATION": prb, "RRC_CONNECTED_UE": ue}})
    show("PM reported", {"dlPrbUtilization": prb, "rrcConnectedUeCount": ue})


def evaluate() -> dict:
    d = call("post", RAPP, "/evaluate")
    show("decision", f"{d['decisionId']} {d['decision']} ({d['reason']})")
    show("state before", d["currentState"])
    if "action" in d:
        show("DME action", {k: d["action"].get(k) for k in ("actionId", "status", "forwardedJobId")})
        show("read-back", d["verification"])
    return d


def step_00(state: dict) -> None:
    """Prepare the RAN: the O1 adaptor registers (ACTIVE), 2 PM counters subscribed, existing TX-muting config."""
    reg = call("post", ADAPTOR, "/control/register")
    seeded = call("post", "ran-nf-oam", "/config-jobs", json={
        "requestedBy": "initial-reconciliation", "accessScope": "single-ME",
        "changes": [{"managedElementRef": ME, "className": "NRCellDU", "managedFunctionRef": MFR,
                     "attributeChanges": {"txMutingFeatureEnable": "true", "txPathOffPattern": "HORIZONTAL_PLANE",
                                          "txMutingActivation": "MUTING_OFF"}}]})
    show("O1 adaptor", reg)
    show("initial config job", seeded["status"])
    show(MFR, call("get", ADAPTOR, f"/objects/{ME}", params={"function_ref": MFR})["attributes"])


def step_01(state: dict) -> None:
    """rApp start: load and check thresholds, open one ONE_TIME pull data job per counter in DME."""
    started = call("post", RAPP, "/start", json={"managedElementRef": ME, "cellId": CELL})
    show("thresholds", started["thresholds"])
    show("data jobs", started["dataJobs"])


def step_02(state: dict) -> None:
    """Low load: PRB 18.4 %, 4 UEs -> REDUCED_TX, MUTING_ON read back."""
    pm(18.4, 4)
    state["lastAction"] = evaluate().get("action")


def step_03(state: dict) -> None:
    """Show the DME action, the RAN NF OAM job and what the O1 adaptor received and applied."""
    action = state.get("lastAction")
    if not action:
        raise SystemExit("no action recorded: run step 02 first")
    record = call("get", "dme", f"/actions/{action['actionId']}")
    job = call("get", "ran-nf-oam", f"/config-jobs/{record['forwardedJobId']}")
    applied = call("get", ADAPTOR, f"/objects/{ME}", params={"function_ref": MFR})["attributes"]
    show("DME action", {k: record[k] for k in ("actionId", "requestedBy", "status", "correlationId", "sourceContext")})
    show("config job", {k: job.get(k) for k in ("jobId", "status")})
    show("O1 adaptor running config", {k: applied.get(k) for k in TX_LEAVES})
    received = [e for e in call("get", ADAPTOR, "/events", params={"limit": 1000})["items"] if e["kind"] == "config.received"]
    if received:
        show("last config received by the adaptor", received[-1]["data"]["changes"])


def step_04(state: dict) -> None:
    """Hysteresis (no change): PRB 41 %, 8 UEs while MUTING_ON -> NO_CHANGE, no O1 write."""
    pm(41.0, 8)
    evaluate()


def step_05(state: dict) -> None:
    """Restore full TX: PRB 45 % -> FULL_TX, MUTING_OFF read back."""
    pm(45.0, 8)
    evaluate()


def step_06(state: dict) -> None:
    """Audit: every decision of this run and every DME action the rApp requested."""
    for d in call("get", RAPP, "/decisions")["items"]:
        verified = (d.get("verification") or {}).get("result", "-")
        print(f"  {d['decisionId']}  {d['currentState'] or '-':10} -> {d['decision']:10} {verified:13} {d['reason']}")
    show("DME actions by the rApp", len(call("get", RAPP, "/actions")["items"]))


STEPS = {f"{i:02d}": globals()[f"step_{i:02d}"] for i in range(7)}


def main() -> None:
    steps = list(STEPS) if sys.argv[1:] in ([], ["all"]) else sys.argv[1:]
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        raise SystemExit(f"unknown step(s) {unknown}; choose from {list(STEPS)} or 'all'")
    state = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
    for step in steps:
        fn = STEPS[step]
        print(f"Step {step} - {fn.__doc__.splitlines()[0]}")
        fn(state)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, default=str, indent=1)


if __name__ == "__main__":
    main()
