#!/usr/bin/env python3
"""Follow the rApp's and the gNB simulator's events and explain each one in plain words. Runs inside the compose network
(start.sh starts it in the background); both services are polled with the long-poll GET /events, so a line appears the
moment something happens. Services that are not up yet are waited for.

    python3 -u narrate.py          # NARRATE_COLOR=1 for colours

`config.read` is left out on purpose: RAN NF OAM reads the cell's configuration on every pass of the rApp's loop, which
would drown the real changes. Repeated no-change passes are not events at all (the rApp does not emit them).
"""

import json
import os
import threading

import httpx

SERVICES = {"rApp": "http://tx-muting-rapp:8000", "gNB ": "http://gnb-o1-adaptor-sim:8000"}
COLOR = os.environ.get("NARRATE_COLOR") == "1"
PAINT = {"rApp": "36", "gNB ": "35"}
SKIP = {"config.read"}
out = threading.Lock()

REASONS = {
    "INSTANTANEOUS_LOW_LOAD": "PRB and UEs are below the activation thresholds and the feature is enabled, so mute half of the TX paths",
    "LOAD_WITHIN_HYSTERESIS": "load is between the two thresholds, so leave the cell as it is (the hysteresis band)",
    "PRB_HIGH": "PRB is at or above the deactivation threshold, so restore full TX",
    "UE_COUNT_HIGH": "the UE count is at or above the deactivation threshold, so restore full TX",
    "PRB_NOT_LOW": "PRB is too high to mute", "UE_COUNT_NOT_LOW": "too many UEs to mute",
    "FEATURE_DISABLED": "the TX-muting feature is disabled on the cell", "MEASUREMENT_MISSING": "no PM sample yet, so no decision",
    "CURRENT_STATE_UNKNOWN": "the cell's TX state could not be read",
}


def kv(data: dict, *keys) -> str:
    return ", ".join(f"{k}={data[k]}" for k in keys if data.get(k) is not None)


def explain(who: str, kind: str, d: dict) -> str:
    if who == "rApp":
        if kind == "started":
            return f"the rApp is bound to {d.get('managedElementRef')} / cell {d.get('cellId')} and has opened DME data jobs for the PM counters"
        if kind == "auto-evaluation.started":
            return f"from now on the rApp decides by itself, one pass every {d.get('intervalSeconds')} s; nobody has to ask it"
        if kind == "auto-evaluation.stopped":
            return "the rApp's own decision loop stopped"
        if kind == "auto-evaluation.disabled":
            return d.get("reason", "")
        if kind == "tx-state.observed":
            return f"the rApp read the cell's TX state: {d.get('previous')} -> {d.get('current')} (first read, or someone else changed it)"
        if kind == "decision":
            text = f"{d['decisionId']}: {d['decision']} ({d['reason']}), cell was {d['stateBefore']}"
            text += f"\n      why: {REASONS.get(d['reason'], '')}"
            if d.get("changes"):
                text += f"\n      wrote: {json.dumps(d['changes'])} through DME -> RAN NF OAM -> the gNB"
                text += f"\n      read-back: {d.get('verification')} after {d.get('attempts')} attempt(s)"
                if d.get("verification") == "VERIFIED":
                    text += " (the gNB really applied it)"
                if d.get("rolledBack"):
                    text += "\n      the mute never took effect, so the rApp rolled the cell back to full TX"
            if d.get("unchangedPassesBefore"):
                text += f"\n      ({d['unchangedPassesBefore']} identical no-change passes before this one are not shown)"
            return text
        if kind == "tx-state.changed":
            return f"the cell's TX state changed and was verified: {d.get('previous')} -> {d.get('current')} (by {d.get('decisionId')})"
        if kind == "evaluation.error":
            return f"an automatic pass failed: {d.get('error')} (shown once; the loop keeps trying)"
        if kind == "evaluation.recovered":
            return "automatic passes work again"
        if kind == "reset":
            return "the rApp forgot its target and decision log"
    else:
        if kind == "endpoint.registered":
            return f"the gNB's O1 adaptor registered with RAN NF OAM ({d.get('managedElementRef')}), was marked ACTIVE by a heartbeat and subscribed to the PM counters"
        if kind == "endpoint.heartbeat":
            return "heartbeat sent to RAN NF OAM"
        if kind == "pm.reported":
            c = d.get("counters", {})
            return (f"the gNB reported PM for cell {d.get('cellId')}: PRB {c.get('DL_PRB_UTILIZATION')} %, {c.get('RRC_CONNECTED_UE')} UEs"
                    f"{' (generated)' if d.get('generated') else ''}; RAN NF OAM passes it to DME, the rApp reads it on its next pass")
        if kind == "config.received":
            return f"RAN NF OAM wrote {json.dumps(d.get('changes'))} to the cell (O1 edit-config); running config is now {json.dumps(d.get('running'))}"
        if kind == "config.local":
            return f"the gNB changed its own configuration: {json.dumps(d.get('changes'))}"
        if kind == "config.fault":
            return f"injected fault {d.get('mode')}: the gNB misbehaved on purpose for this edit-config"
        if kind == "fault.injected":
            return f"the next {d.get('count')} edit-config(s) will fail with {d.get('mode')}"
        if kind in ("alarm.raised", "alarm.cleared"):
            return f"alarm {d.get('sourceAlarmId')} {kind.split('.')[1]} (the rApp does not read alarms)"
        if kind == "northbound.error":
            return f"the gNB could not reach RAN NF OAM: {d.get('error')}"
    return json.dumps(d)


def paint(who: str, text: str) -> str:
    return f"\033[{PAINT[who]}m{text}\033[0m" if COLOR else text


def follow(who: str) -> None:
    base, since, first = SERVICES[who], 0, True
    while True:
        try:
            batch = httpx.get(base + "/events", params={"since": since, "wait": 20}, timeout=40).json()
        except (httpx.HTTPError, ValueError):
            first = False  # it was not there: whatever it says when it appears is new
            threading.Event().wait(2)
            continue
        if batch["lastSeq"] < since:  # a new container: numbering restarted
            since = 0
            continue
        if first:  # attached to something already running: skip its history
            since, first = batch["lastSeq"], False
            continue
        for e in batch["items"]:
            since = e["seq"]
            if e["kind"] in SKIP:
                continue
            with out:
                print(paint(who, f"  {e['time'][11:23]} [{who}] {e['kind']:<22} {explain(who, e['kind'], e['data'])}"), flush=True)


if __name__ == "__main__":
    for name in list(SERVICES)[1:]:
        threading.Thread(target=follow, args=(name,), daemon=True).start()
    follow(list(SERVICES)[0])
