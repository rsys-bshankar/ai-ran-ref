#!/usr/bin/env bash
# Follow state changes as they happen: the rApp's (decisions, TX state observed / changed, start, reset) and the O1
# adaptor simulator's (config received, PM reported, alarms, faults). Ctrl-C to stop.
#
#   scripts/watch.sh            # both, merged on one console
#   scripts/watch.sh rapp       # the rApp only
#   scripts/watch.sh adaptor    # the simulator only (same as scripts/cli.sh watch)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

which="${1:-both}"
case "$which" in rapp|adaptor|both) ;; *) echo "usage: $0 [rapp|adaptor|both]" >&2; exit 1 ;; esac

compose exec -T -e "WATCH=$which" r1-termination python3 -u - <<'PY'
import json, os, threading, httpx

SERVICES = {"rapp": "http://tx-muting-rapp:8000", "adaptor": "http://o1-adaptor-sim:8000"}
want = [k for k in SERVICES if os.environ["WATCH"] in (k, "both")]
out = threading.Lock()


def follow(name):
    base = SERVICES[name]
    since = httpx.get(base + "/events", params={"limit": 1}, timeout=10).json()["lastSeq"]
    while True:
        try:
            batch = httpx.get(base + "/events", params={"since": since, "wait": 20}, timeout=40).json()
        except httpx.HTTPError:
            threading.Event().wait(2)
            continue
        for e in batch["items"]:
            data = " ".join(f"{k}={v if isinstance(v, str) else json.dumps(v)}" for k, v in e["data"].items())
            with out:
                print(f"[{e['time'][11:23]}] {name:<7} #{e['seq']:<4} {e['kind']:<20} {data}", flush=True)
            since = e["seq"]


for n in want[1:]:
    threading.Thread(target=follow, args=(n,), daemon=True).start()
follow(want[0])
PY
