"""gNB O1 adaptor simulator: consumed configuration, generated data towards a fake RAN NF OAM, the event log, the CLI."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import gnb_cli as cli, main, oam

NS = "urn:ietf:params:xml:ns:netconf:base:1.0"


class FakeOam:
    """Just enough of RAN NF OAM: records what the adaptor sends."""

    def __init__(self):
        self.requests = []
        self.alarm_seq = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path, dict(request.url.params),
                              json.loads(request.content) if request.content else None))
        path = request.url.path
        if request.method == "GET" and path.startswith("/managed-entities/"):
            return httpx.Response(404)
        if path == "/o1-adaptor-endpoints":
            return httpx.Response(201, json={"endpointId": "ep-1"})
        if path == "/alarms/ingest":
            self.alarm_seq += 1
            return httpx.Response(201, json={"alarmId": f"alarm-{self.alarm_seq}"})
        return httpx.Response(200, json={})

    def paths(self):
        return [(m, p) for m, p, _, _ in self.requests]


@pytest.fixture
def sim():
    fake = FakeOam()
    main.northbound = oam.OamClient("http://oam", transport=httpx.MockTransport(fake))
    main.store.reset()
    main._registration.clear()
    main._generator.stop()
    with TestClient(main.app) as client:
        client.fake = fake
        yield client
    main._generator.stop()


def rpc(kind, leaves=None, operation=None):
    body = "".join(f"<{k}>{v}</{k}>" for k, v in (leaves or {}).items())
    op = f' operation="{operation}"' if operation else ""
    inner = "<get-config><filter/></get-config>" if kind == "get" else "<edit-config/>"
    return (f'<rpc message-id="7" xmlns="{NS}">{inner}<managed-object ref="me-1" function-ref="NRCellDU=101"{op}>{body}'
            "</managed-object></rpc>")


def kinds(sim, since=0):
    return [e["kind"] for e in sim.get("/events", params={"since": since}).json()["items"]]


def test_edit_config_is_consumed_and_read_back(sim):
    r = sim.post("/edit-config", content=rpc("edit", {"txMutingActivation": "MUTING_ON"}))
    assert "<ok/>" in r.text
    got = sim.post("/edit-config", content=rpc("get")).text
    assert "<txMutingActivation>MUTING_ON</txMutingActivation>" in got
    assert "<txMutingFeatureEnable>true</txMutingFeatureEnable>" in got  # defaults still there
    events = sim.get("/events").json()["items"]
    received = next(e for e in events if e["kind"] == "config.received")
    assert received["data"]["changes"] == {"txMutingActivation": "MUTING_ON"}


@pytest.mark.parametrize("body,tag", [("<rpc", "malformed-message"), (rpc("edit"), "invalid-value")])
def test_bad_requests_are_rpc_errors(sim, body, tag):
    r = sim.post("/edit-config", content=body)
    assert r.status_code == 200 and f"<error-tag>{tag}</error-tag>" in r.text


def test_register_heartbeats_and_subscribes(sim):
    r = sim.post("/control/register")
    assert r.json()["endpointId"] == "ep-1"
    paths = sim.fake.paths()
    assert ("POST", "/o1-adaptor-endpoints") in paths and ("POST", "/o1-adaptor-endpoints/ep-1/heartbeat") in paths
    assert [p for _, p in paths].count("/pm-subscriptions") == 2
    assert "endpoint.registered" in kinds(sim)


def test_counters_become_pm_reports(sim):
    sim.post("/control/counters", json={"counters": {"DL_PRB_UTILIZATION": 18.4, "RRC_CONNECTED_UE": 4}, "cellId": "101"})
    reports = [b for m, p, _, b in sim.fake.requests if p == "/pm-reports"]
    assert {r["counterType"]: r["measurements"][0]["value"] for r in reports} == {"DL_PRB_UTILIZATION": 18.4, "RRC_CONNECTED_UE": 4}
    assert all(r["measurements"][0]["cellId"] == "101" for r in reports)
    assert "pm.reported" in kinds(sim)


def test_alarm_raise_and_clear_by_source_id(sim):
    raised = sim.post("/control/alarms", json={"sourceAlarmId": "13325"}).json()
    ingest = next(q for q in sim.fake.requests if q[1] == "/alarms/ingest")
    assert ingest[2]["source_alarm_id"] == "13325" and ingest[2]["managed_function_ref"] == "NRCellDU=101"
    assert sim.post("/control/alarms/13325/clear").json() == {"cleared": raised["alarmId"]}
    assert ("PATCH", f"/alarms/{raised['alarmId']}/clear") in sim.fake.paths()
    assert sim.post("/control/alarms/13325/clear").status_code == 404
    assert kinds(sim)[-2:] == ["alarm.raised", "alarm.cleared"]


def test_northbound_failure_is_an_event_and_502(sim):
    main.northbound = oam.OamClient("http://oam", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    assert sim.post("/control/counters", json={"counters": {"X": 1}}).status_code == 502
    assert "northbound.error" in kinds(sim)


@pytest.mark.parametrize("mode,status,tag", [("TIMEOUT", 504, None), ("RPC_ERROR", 200, "operation-failed")])
def test_faults(sim, mode, status, tag):
    sim.post("/control/faults", json={"mode": mode})
    r = sim.post("/edit-config", content=rpc("edit", {"txMutingActivation": "MUTING_ON"}))
    assert r.status_code == status and (tag is None or tag in r.text)
    assert "<ok/>" in sim.post("/edit-config", content=rpc("edit", {"txMutingActivation": "MUTING_ON"})).text  # consumed


def test_ignore_write_acknowledges_without_applying(sim):
    sim.post("/control/faults", json={"mode": "IGNORE_WRITE"})
    assert "<ok/>" in sim.post("/edit-config", content=rpc("edit", {"txMutingActivation": "MUTING_ON"})).text
    assert "MUTING_OFF" in sim.post("/edit-config", content=rpc("get")).text
    assert sim.post("/control/faults", json={"mode": "NOPE"}).status_code == 422


def test_local_config_change_is_an_event(sim):
    sim.post("/control/config", json={"attributes": {"txPathOffPattern": "VERTICAL_PLANE"}})
    assert sim.get(f"/objects/{main.ME}", params={"function_ref": "NRCellDU=101"}).json()["attributes"]["txPathOffPattern"] == "VERTICAL_PLANE"
    assert "config.local" in kinds(sim)


def test_generator_reports_periodically(sim):
    sim.post("/control/generator", json={"action": "start", "intervalSeconds": 0.05})
    for _ in range(100):
        if any(e["data"].get("generated") for e in sim.get("/events", params={"wait": 0.1}).json()["items"]):
            break
    sim.post("/control/generator", json={"action": "stop"})
    assert any(p == "/pm-reports" for _, p in sim.fake.paths())
    assert not sim.get("/control/status").json()["generator"]["running"]


def test_events_long_poll_returns_new_event(sim):
    import threading
    last = sim.get("/events").json()["lastSeq"]
    threading.Timer(0.2, lambda: main.events.emit("custom.event", x=1)).start()
    items = sim.get("/events", params={"since": last, "wait": 5}).json()["items"]
    assert [e["kind"] for e in items] == ["custom.event"]


# ---- the CLI drives the same routes

def test_cli_commands(sim, capsys):
    api = cli.Api(client=sim)
    cli.show(cli.run(api, ["pm", "18.4", "4"]))
    reports = {b["counterType"]: b["measurements"][0]["value"] for _, p, _, b in sim.fake.requests if p == "/pm-reports"}
    assert reports == {"DL_PRB_UTILIZATION": 18.4, "RRC_CONNECTED_UE": 4.0}
    cli.run(api, ["alarm", "raise", "13325", "major"])
    assert sim.fake.requests[-1][2]["severity"] == "major"
    cli.run(api, ["config", "set", "txMutingActivation=MUTING_ON"])
    assert "MUTING_ON" in json.dumps(cli.run(api, ["config", "show"]))
    cli.run(api, ["fault", "rpc_error", "2"])
    assert sim.get("/control/status").json()["faults"] == [{"mode": "RPC_ERROR", "count": 2}]
    out = cli.run(api, ["events", "3"])
    assert len(out.splitlines()) == 3 and "#" in out


@pytest.mark.parametrize("argv", [["pm", "1"], ["alarm"], ["config", "set"], ["gen", "x"], ["bogus"]])
def test_cli_usage_errors(sim, argv):
    with pytest.raises(cli.CliError):
        cli.run(cli.Api(client=sim), argv)


def test_cli_follow_prints_events(sim):
    import io, threading
    api, out, stop = cli.Api(client=sim), io.StringIO(), threading.Event()
    last = sim.get("/events").json()["lastSeq"]
    sim.post("/control/counters", json={"counters": {"DL_PRB_UTILIZATION": 5}})
    t = threading.Thread(target=cli.follow, args=(api, stop, last, out, 0.2))
    t.start()
    for _ in range(50):
        if "pm.reported" in out.getvalue():
            break
        threading.Event().wait(0.1)
    stop.set()
    t.join(30)
    assert "pm.reported" in out.getvalue() and "DL_PRB_UTILIZATION" in out.getvalue()
