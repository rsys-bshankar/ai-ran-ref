"""CLI on top of the O1 adaptor simulator.

    python -m app.cli                      interactive shell; asynchronous events print as they happen
    python -m app.cli pm 18.4 4            one command, then exit
    python -m app.cli watch                follow the event log until Ctrl-C

Inside compose:  docker compose exec o1-adaptor-sim python -m app.cli

It only calls the simulator's HTTP routes (ADAPTOR_URL, default http://localhost:8000), so it works from any
container or host that can reach them. `help` lists the commands.
"""

import cmd
import json
import os
import shlex
import sys
import threading

import httpx

URL = os.environ.get("ADAPTOR_URL", "http://localhost:8000")

HELP = """\
  status                                   registration, running config, alarms, faults, generator
  register                                 self-register with RAN NF OAM, heartbeat, subscribe PM counters
  heartbeat                                one heartbeat
  pm <prb%> <ue> [sync|nosync] [cell]      report DL_PRB_UTILIZATION, RRC_CONNECTED_UE, RADIO_SYNC_STATE
  counter <TYPE> <value> [cell]            report any one PM counter
  alarm raise <id> [severity] [cause]      raise an alarm on the cell (severity: critical|major|minor|warning)
  alarm clear <id>                         clear it (RAN NF OAM alarmId or the source alarm id)
  config show [function-ref]               running configuration
  config set <k=v> [k=v ...]               change it locally, as the node itself would
  fault <TIMEOUT|RPC_ERROR|IGNORE_WRITE> [count]   make the next edit-config(s) misbehave
  gen start [seconds]                      report random PRB / UE counters periodically
  gen stop
  events [n]                               last n events (default 20)
  watch                                    follow events until Ctrl-C
  reset                                    forget config, alarms and faults
  quit"""


class Api:
    def __init__(self, url: str = URL, client: httpx.Client | None = None):
        self.http = client or httpx.Client(base_url=url, timeout=60.0)

    def call(self, verb: str, path: str, **kw):
        try:
            resp = self.http.request(verb, path, **kw)
        except httpx.HTTPError as exc:
            raise CliError(f"cannot reach the simulator: {exc}")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise CliError(f"{resp.status_code}: {detail}")
        return resp.json() if resp.content else None


class CliError(Exception):
    pass


def format_event(e: dict) -> str:
    data = " ".join(f"{k}={json.dumps(v) if not isinstance(v, str) else v}" for k, v in e["data"].items())
    return f"[{e['time'][11:23]}] #{e['seq']:<4} {e['kind']:<20} {data}"


def run(api: Api, argv: list[str]) -> object:
    """Execute one command; returns what to print (a dict/list/str) or None."""
    if not argv:
        return None
    c, a = argv[0], argv[1:]
    if c == "status":
        return api.call("get", "/control/status")
    if c == "register":
        return api.call("post", "/control/register")
    if c == "heartbeat":
        return api.call("post", "/control/heartbeat")
    if c == "pm":
        if len(a) < 2:
            raise CliError("usage: pm <prb%> <ue> [sync|nosync] [cell]")
        sync = a[2] if len(a) > 2 else "sync"
        if sync not in ("sync", "nosync"):
            raise CliError("third argument must be sync or nosync")
        body = {"counters": {"DL_PRB_UTILIZATION": float(a[0]), "RRC_CONNECTED_UE": float(a[1]),
                             "RADIO_SYNC_STATE": 1.0 if sync == "sync" else 0.0}}
        if len(a) > 3:
            body["cellId"] = a[3]
        return api.call("post", "/control/counters", json=body)
    if c == "counter":
        if len(a) < 2:
            raise CliError("usage: counter <TYPE> <value> [cell]")
        body = {"counters": {a[0]: float(a[1])}}
        if len(a) > 2:
            body["cellId"] = a[2]
        return api.call("post", "/control/counters", json=body)
    if c == "alarm" and a[:1] == ["raise"] and len(a) >= 2:
        body = {"sourceAlarmId": a[1]}
        if len(a) > 2:
            body["severity"] = a[2]
        if len(a) > 3:
            body["probableCause"] = a[3]
        return api.call("post", "/control/alarms", json=body)
    if c == "alarm" and a[:1] == ["clear"] and len(a) == 2:
        return api.call("post", f"/control/alarms/{a[1]}/clear")
    if c == "alarm":
        raise CliError("usage: alarm raise <id> [severity] [cause] | alarm clear <id>")
    if c == "config" and a[:1] == ["show"]:
        st = api.call("get", "/control/status")
        ref = st["managedElementRef"]
        return api.call("get", f"/objects/{ref}", params={"function_ref": a[1] if len(a) > 1 else f"NRCellDU={st['cellId']}"})
    if c == "config" and a[:1] == ["set"] and len(a) > 1:
        try:
            attrs = dict(kv.split("=", 1) for kv in a[1:])
        except ValueError:
            raise CliError("usage: config set <k=v> [k=v ...]")
        return api.call("post", "/control/config", json={"attributes": attrs})
    if c == "config":
        raise CliError("usage: config show [function-ref] | config set <k=v> ...")
    if c == "fault":
        if not a:
            raise CliError("usage: fault <TIMEOUT|RPC_ERROR|IGNORE_WRITE> [count]")
        return api.call("post", "/control/faults", json={"mode": a[0].upper(), "count": int(a[1]) if len(a) > 1 else 1})
    if c == "gen":
        if a[:1] == ["start"]:
            return api.call("post", "/control/generator", json={"action": "start", "intervalSeconds": float(a[1]) if len(a) > 1 else 10.0})
        if a[:1] == ["stop"]:
            return api.call("post", "/control/generator", json={"action": "stop"})
        raise CliError("usage: gen start [seconds] | gen stop")
    if c == "events":
        items = api.call("get", "/events", params={"limit": 1000})["items"]
        return "\n".join(format_event(e) for e in items[-(int(a[0]) if a else 20):]) or "(no events)"
    if c == "reset":
        api.call("delete", "/control/state")
        return "state reset"
    raise CliError(f"unknown command {c!r}; try help")


def follow(api: Api, stop: threading.Event, since: int, out=sys.stdout, wait: float = 20) -> None:
    """Print events as they arrive (long poll) until `stop` is set."""
    while not stop.is_set():
        try:
            batch = api.call("get", "/events", params={"since": since, "wait": wait})
        except CliError:
            stop.wait(2)
            continue
        for e in batch["items"]:
            print(format_event(e), file=out, flush=True)
            since = e["seq"]


def show(result) -> None:
    if result is not None:
        print(result if isinstance(result, str) else json.dumps(result, indent=2, default=str))


class Shell(cmd.Cmd):
    intro = "O1 adaptor simulator CLI. `help` lists commands; asynchronous events print as they arrive."
    prompt = "o1> "

    def __init__(self, api: Api):
        super().__init__()
        self.api = api
        self._stop = threading.Event()

    def preloop(self):
        try:
            since = self.api.call("get", "/events", params={"limit": 1})["lastSeq"]
        except CliError as exc:
            print(exc)
            since = 0
        threading.Thread(target=follow, args=(self.api, self._stop, since), daemon=True).start()

    def postloop(self):
        self._stop.set()

    def default(self, line):
        if line.strip() in ("quit", "exit", "EOF"):
            return True
        try:
            show(run(self.api, shlex.split(line)))
        except (CliError, ValueError) as exc:
            print(f"error: {exc}")

    def do_help(self, _):
        print(HELP)

    def do_watch(self, _):
        print("following events, Ctrl-C to stop")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            print()

    def do_quit(self, _):
        return True

    do_exit = do_EOF = do_quit

    def emptyline(self):
        pass


def main(argv: list[str]) -> int:
    api = Api()
    if not argv:
        Shell(api).cmdloop()
        return 0
    if argv == ["help"]:
        print(HELP)
        return 0
    if argv == ["watch"]:
        stop = threading.Event()
        try:
            follow(api, stop, 0)
        except KeyboardInterrupt:
            pass
        return 0
    try:
        show(run(api, argv))
    except (CliError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
