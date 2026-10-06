"""The rApp service's closed loop against a fake DME and RAN NF OAM (no stack needed)."""

import datetime

import pytest
from fastapi.testclient import TestClient

from app import main

NOW = datetime.datetime.now(datetime.UTC).isoformat()


class Fake:
    """DME and RAN NF OAM in one: PM records, the O1 config a DME action writes, alarms."""

    def __init__(self):
        self.pm = {"DL_PRB_UTILIZATION": 18.4, "RRC_CONNECTED_UE": 4, "RADIO_SYNC_STATE": 1.0}
        self.config = {"txMutingFeatureEnable": "true", "txPathOffPattern": "HORIZONTAL_PLANE", "txMutingActivation": "MUTING_OFF"}
        self.alarms = []
        self.actions = []
        self.drop_writes = 0

    def __call__(self, base, verb, path, expect=(200, 201, 202, 204), **kw):
        if path == "/dme-types":
            return [{"typeName": f"RAN.PMCounters.{c}", "dmeTypeId": f"t-{c}"} for c in self.pm]
        if path == "/data-jobs":
            return {"dataJobId": "job-" + kw["json"]["dmeTypeId"][2:]}
        if path.startswith("/data-jobs/") and path.endswith("/records"):
            counter = path.split("/")[2][4:]
            return {"items": [{"payload": {"cellId": "101", "value": self.pm[counter], "timestamp": NOW}}]}
        if path.endswith("/config"):
            return {"attributes": dict(self.config)}
        if path == "/alarms":
            return {"items": self.alarms}
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
    main.reset()
    with TestClient(main.app) as client:
        client.fake = fake
        client.post("/start", json={"managedElementRef": "me-1", "cellId": "101"})
        yield client
    main.reset()


def test_requires_start():
    main.reset()
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


def test_blocking_alarm_and_radio_sync(svc):
    svc.fake.alarms = [{"severity": "critical", "sourceAlarmId": "13325", "managedFunctionRef": "NRCellDU=101"}]
    assert "BLOCKING_ALARM" in svc.post("/evaluate").json()["reason"]
    svc.fake.alarms = []
    svc.fake.pm["RADIO_SYNC_STATE"] = 0.0
    assert "RADIO_NOT_SYNCHRONIZED" in svc.post("/evaluate").json()["reason"]


def test_unverified_mute_is_retried_then_rolled_back(svc):
    svc.fake.drop_writes = 2  # the first try and the one retry are acknowledged but not applied
    d = svc.post("/evaluate").json()
    assert d["verification"]["result"] == "VERIFY_FAILED" and d["attempts"] == 2
    assert d["rollback"]["verification"]["result"] == "VERIFIED"
    assert len(svc.fake.actions) == 3
    assert svc.fake.actions[-1]["changes"][0]["attributeChanges"] == {"txMutingActivation": "MUTING_OFF"}
