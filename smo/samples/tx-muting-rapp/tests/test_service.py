"""The rApp service's closed loop against a fake DME and RAN NF OAM (no stack needed)."""

import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main

NOW = datetime.datetime.now(datetime.UTC).isoformat()


class Fake:
    """DME and RAN NF OAM in one: PM records and the O1 config a DME action writes."""

    def __init__(self):
        self.pm = {"DL_PRB_UTILIZATION": 18.4, "RRC_CONNECTED_UE": 4}
        self.config = {"txMutingFeatureEnable": "true", "txPathOffPattern": "HORIZONTAL_PLANE", "txMutingActivation": "MUTING_OFF"}
        self.actions = []
        self.drop_writes = 0

    def __call__(self, base, verb, path, expect=(200, 201, 202, 204), **kw):
        if path == "/dme-types":
            return [{"typeName": f"RAN.PMCounters.{c}", "dmeTypeId": f"t-{c}"} for c in self.pm]
        if path == "/data-jobs":
            return {"dataJobId": "job-" + kw["json"]["dmeTypeId"][2:]}
        if path.startswith("/data-jobs/") and path.endswith("/records"):
            counter = path.split("/")[2][4:]
            if counter not in self.pm:  # no sample for this counter
                return {"items": []}
            return {"items": [{"payload": {"cellId": "101", "value": self.pm[counter], "timestamp": NOW}}]}
        if path.endswith("/config"):
            return {"attributes": dict(self.config)}
        if path == "/actions" and verb == "post":
            self.actions.append(kw["json"])
            if self.drop_writes:
                self.drop_writes -= 1
            else:
                self.config.update(kw["json"]["changes"][0]["attributeChanges"])
            return {"actionId": kw["json"]["actionId"], "status": "COMPLETED"}
        raise AssertionError(f"unexpected {verb} {path}")


@pytest.fixture
def svc(monkeypatch):
    fake = Fake()
    monkeypatch.setattr(main, "_call", fake)
    main._state.reset()
    with TestClient(main.app) as client:
        client.fake = fake
        client.post("/start", json={"managedElementRef": "me-1", "cellId": "101"})
        yield client
    main._state.reset()


def test_requires_start():
    main._state.reset()
    assert TestClient(main.app).post("/evaluate").status_code == 409


def test_low_load_mutes_and_verifies(svc):
    d = svc.post("/evaluate").json()
    assert (d["decision"], d["verification"]["result"]) == ("REDUCED_TX", "VERIFIED")
    assert svc.fake.config["txMutingActivation"] == "MUTING_ON"
    assert svc.get("/state").json()["config"]["txMutingActivation"] == "MUTING_ON"


def test_hysteresis_then_restore(svc):
    svc.post("/evaluate")
    svc.fake.pm["DL_PRB_UTILIZATION"] = 41.0
    assert svc.post("/evaluate").json()["decision"] == "NO_CHANGE"
    svc.fake.pm["DL_PRB_UTILIZATION"] = 45.0
    d = svc.post("/evaluate").json()
    assert (d["decision"], d["reason"]) == ("FULL_TX", "PRB_HIGH")
    assert [x["decisionId"] for x in svc.get("/decisions").json()["items"]] == ["TXM-0001", "TXM-0002", "TXM-0003"]


def test_missing_pm_sample_is_no_change_and_nothing_is_written(svc):
    del svc.fake.pm["RRC_CONNECTED_UE"]
    d = svc.post("/evaluate").json()
    assert (d["decision"], d["reason"], d["changes"]) == ("NO_CHANGE", "MEASUREMENT_MISSING", {})
    assert svc.fake.actions == []


def test_only_pm_and_configuration_are_read(svc):
    """No alarm or synchronisation input: the loop reads two counters and the three TX-muting leaves."""
    paths = []
    original = svc.fake.__call__

    def spy(service, verb, path, *a, **kw):
        paths.append(path)
        return original(service, verb, path, *a, **kw)

    main._call, saved = spy, main._call
    try:
        svc.post("/evaluate")
    finally:
        main._call = saved
    assert not any("alarm" in p for p in paths) and len([p for p in paths if p.endswith("/records")]) == 2


def test_unverified_mute_is_retried_then_rolled_back(svc):
    svc.fake.drop_writes = 2  # the first try and the one retry are acknowledged but not applied
    d = svc.post("/evaluate").json()
    assert d["verification"]["result"] == "VERIFY_FAILED" and d["attempts"] == 2
    assert d["rollback"]["verification"]["result"] == "VERIFIED"
    assert len(svc.fake.actions) == 3
    assert svc.fake.actions[-1]["changes"][0]["attributeChanges"] == {"txMutingActivation": "MUTING_OFF"}


# ---- state changes are visible

def kinds(svc, since=0):
    return [(e["kind"], e["data"]) for e in svc.get("/events", params={"since": since}).json()["items"]]


def test_every_state_change_is_an_event(svc):
    assert kinds(svc)[-1][0] == "started" and kinds(svc)[-1][1]["cellId"] == "101"
    base = svc.get("/events").json()["lastSeq"]
    svc.post("/evaluate")                                   # mute: first read, then a verified change
    events = kinds(svc, base)
    assert [k for k, _ in events] == ["tx-state.observed", "decision", "tx-state.changed"]
    assert events[0][1] == {"decisionId": "TXM-0001", "previous": None, "current": "MUTING_OFF"}
    assert events[1][1]["decision"] == "REDUCED_TX" and events[1][1]["verification"] == "VERIFIED"
    assert events[2][1] == {"decisionId": "TXM-0001", "previous": "MUTING_OFF", "current": "MUTING_ON"}
    svc.fake.pm["DL_PRB_UTILIZATION"] = 41.0
    base = svc.get("/events").json()["lastSeq"]
    svc.post("/evaluate")                                   # no change: a decision event only
    assert [k for k, _ in kinds(svc, base)] == ["decision"]
    assert svc.get("/state").json()["lastKnownTxState"] == "MUTING_ON"


def test_a_change_nobody_here_made_is_observed(svc):
    svc.post("/evaluate")
    svc.fake.config["txMutingActivation"] = "MUTING_OFF"   # someone else switched TX back on
    svc.fake.pm["DL_PRB_UTILIZATION"] = 41.0
    svc.post("/evaluate")
    assert ("tx-state.observed", {"decisionId": "TXM-0002", "previous": "MUTING_ON", "current": "MUTING_OFF"}) in kinds(svc)


def test_rollback_is_one_event_with_the_final_state(svc):
    base = svc.get("/events").json()["lastSeq"]
    svc.fake.drop_writes = 2
    svc.post("/evaluate")
    events = kinds(svc, base)
    decision = next(d for k, d in events if k == "decision")
    assert decision["rolledBack"] is True and decision["attempts"] == 2
    assert not any(k == "tx-state.changed" and d["current"] == "MUTING_ON" for k, d in events)


def test_reset_is_an_event_and_keeps_the_log(svc):
    svc.delete("/state")
    assert kinds(svc)[-1][0] == "reset" and kinds(svc)[-1][1]["discarded"] == {"started": True, "decisions": 0}


def test_events_long_poll_wakes_on_a_change(svc):
    import threading
    last = svc.get("/events").json()["lastSeq"]
    threading.Timer(0.2, lambda: main._state.emit("custom", x=1)).start()
    assert [e["kind"] for e in svc.get("/events", params={"since": last, "wait": 5}).json()["items"]] == ["custom"]


# ---- calls go through R1 Termination with the rApp's token

def test_calls_use_r1_prefixes_and_the_bearer_token(monkeypatch):
    seen = []

    class Stub:
        def get(self, path, **kw):
            seen.append(("get", path))
            return httpx.Response(200, json={"items": []})

    monkeypatch.setattr(main, "_r1", Stub())
    main._call("ran-nf-oam", "get", "/managed-entities/x/config", params={})
    main._call("dme", "get", "/actions")
    assert seen == [("get", "/ran-nf-oam/managed-entities/x/config"), ("get", "/dme/actions")]


def test_a_refusal_at_the_gateway_is_a_502_that_names_the_call(monkeypatch):
    class Stub:
        def post(self, path, **kw):
            return httpx.Response(403, json={"title": "ROLE_NOT_PERMITTED"})

    monkeypatch.setattr(main, "_r1", Stub())
    with pytest.raises(main.HTTPException) as exc:
        main._call("dme", "post", "/actions", json={})
    assert exc.value.status_code == 502 and "/dme/actions via R1 -> 403" in exc.value.detail


def test_decision_id_is_the_correlation_id_and_the_token_rides_along():
    client = main._RappR1Client(bearer_token="rapp-token")
    assert client._headers()["Authorization"] == "Bearer rapp-token"
    token = main._correlation_override.set("TXM-0042")
    try:
        assert client._headers()[main.CORRELATION_HEADER] == "TXM-0042"
    finally:
        main._correlation_override.reset(token)
