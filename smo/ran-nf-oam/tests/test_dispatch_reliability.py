"""Wave 10.1 (docs/ROADMAP.md W10-17/W10-19/W10-20): per-function
addressing, NETCONF retries with backoff, the alarm raised when they are
exhausted, and read-after-write. Run with: pytest smo/ran-nf-oam/tests -q
"""

import pytest

from test_main import _make_me, client, db_session_factory  # noqa: F401  (pytest fixtures)

from app.netconf_client import EditResult

CHANGE = {"managedElementRef": "ME-1", "managedFunctionRef": "NRCellDU=101",
          "attributeChanges": {"administrativeState": "LOCKED"}}


@pytest.fixture
def adaptor(monkeypatch):
    """Scripted edit-config outcomes, consumed one per attempt."""
    state = {"outcomes": [], "calls": [], "slept": []}

    def fake_send(adaptor_uri, target_ref, attribute_changes, message_id, operation="merge", managed_function_ref=None):
        state["calls"].append((target_ref, managed_function_ref, attribute_changes))
        return state["outcomes"].pop(0) if state["outcomes"] else EditResult(True)

    monkeypatch.setattr("app.main.send_edit_config", fake_send)
    monkeypatch.setattr("app.main._sleep", state["slept"].append)
    return state


def _write(client):
    job = client.post("/config-jobs", json={"requestedBy": "rapp", "scope": "cell", "changes": [CHANGE]}).json()
    return job, client.get(f"/config-jobs/{job['jobId']}").json()["subChanges"][0]


def test_the_managed_function_ref_is_dispatched(client, db_session_factory, adaptor):
    _make_me(db_session_factory)
    job, sub = _write(client)
    assert job["status"] == "COMPLETED" and sub["attempts"] == 1 and sub["managedFunctionRef"] == "NRCellDU=101"
    assert adaptor["calls"] == [("ME-1", "NRCellDU=101", {"administrativeState": "LOCKED"})]


def test_a_timeout_is_retried_with_backoff(client, db_session_factory, adaptor):
    _make_me(db_session_factory)
    adaptor["outcomes"] = [EditResult(False, "NETCONF_TIMEOUT"), EditResult(False, "NETCONF_UNREACHABLE")]
    job, sub = _write(client)
    assert job["status"] == "COMPLETED" and sub["attempts"] == 3
    assert adaptor["slept"] == [5.0, 10.0]
    assert client.get("/alarms", params={"managed_element_ref": "ME-1"}).json()["items"] == []


def test_exhausted_retries_fail_the_change_and_raise_an_alarm(client, db_session_factory, adaptor):
    _make_me(db_session_factory)
    adaptor["outcomes"] = [EditResult(False, "NETCONF_TIMEOUT")] * 4
    job, sub = _write(client)
    assert job["status"] == "FAILED"
    assert (sub["status"], sub["rejectionReason"], sub["attempts"]) == ("REJECTED", "NETCONF_TIMEOUT", 4)
    assert adaptor["slept"] == [5.0, 10.0, 20.0]
    alarm = client.get("/alarms", params={"managed_element_ref": "ME-1"}).json()["items"][0]
    assert (alarm["probableCause"], alarm["alarmType"], alarm["severity"]) == ("NETCONF_TIMEOUT", "COMMUNICATIONS_ALARM", "major")
    assert "NRCellDU=101" in alarm["specificProblem"]


def test_an_rpc_error_is_not_retried(client, db_session_factory, adaptor):
    _make_me(db_session_factory)
    adaptor["outcomes"] = [EditResult(False, "NETCONF_RPC_FAILED")]
    job, sub = _write(client)
    assert (job["status"], sub["attempts"], sub["rejectionReason"]) == ("FAILED", 1, "NETCONF_RPC_FAILED")
    assert client.get("/alarms").json()["items"] == []


def test_read_after_write(client, db_session_factory, monkeypatch):
    _make_me(db_session_factory)
    seen = []
    monkeypatch.setattr("app.main.send_get_config", lambda uri, ref, message_id, managed_function_ref=None:
                        seen.append((ref, managed_function_ref)) or {"administrativeState": "LOCKED"})
    resp = client.get("/managed-entities/ME-1/config", params={"managed_function_ref": "NRCellDU=101"})
    assert resp.json() == {"managedElementRef": "ME-1", "managedFunctionRef": "NRCellDU=101",
                           "attributes": {"administrativeState": "LOCKED"}}
    assert seen == [("ME-1", "NRCellDU=101")]
    monkeypatch.setattr("app.main.send_get_config", lambda *a, **kw: None)
    assert client.get("/managed-entities/ME-1/config").status_code == 503
    assert client.get("/managed-entities/ghost/config").status_code == 503


def test_pm_reports_are_delivered_to_every_data_job_of_the_counter_type(client, db_session_factory, monkeypatch):
    """Wave 10.1 (W10-04): O1 PM → RAN NF OAM → DME."""
    _make_me(db_session_factory)
    posted = []

    class Resp:
        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    def fake_get(self, path, **kw):
        if path == "/dme/dme-types":
            return Resp([{"dmeTypeId": "t-1", "typeName": "RAN.PMCounters.PRB_UTILIZATION"}])
        return Resp({"items": [{"dataJobId": "j-1"}, {"dataJobId": "j-2"}]})

    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: posted.append((path, json)) or Resp({}))
    report = {"managedElementRef": "ME-1", "counterType": "PRB_UTILIZATION",
              "measurements": [{"cellId": "101", "value": 3.2, "timestamp": "2026-01-01T00:00:00Z"}]}
    assert client.post("/pm-reports", json=report).status_code == 422  # no PM subscription yet
    client.post("/pm-subscriptions", params={"managed_element_ref": "ME-1", "counter_type": "PRB_UTILIZATION", "delivery_method": "pull"})
    posted.clear()
    out = client.post("/pm-reports", json=report).json()
    assert (out["dataJobs"], out["recordsDelivered"]) == (2, 2)
    assert posted[0] == ("/dme/data-jobs/j-1/records", {"payload": {
        "managedElementRef": "ME-1", "cellId": "101", "counter": "PRB_UTILIZATION", "value": 3.2,
        "timestamp": "2026-01-01T00:00:00+00:00"}})


def test_pm_reports_carry_multi_counter_per_relation_measurements(client, db_session_factory, monkeypatch):
    """Wave 10.2 (W10.2-03): handover counters per neighbour relation."""
    _make_me(db_session_factory)
    posted = []

    class Resp:
        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: Resp(
        [{"dmeTypeId": "t-1", "typeName": "RAN.PMCounters.HO_PERFORMANCE"}] if path == "/dme/dme-types"
        else {"items": [{"dataJobId": "j-1"}]}))
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: posted.append(json) or Resp({}))
    client.post("/pm-subscriptions", params={"managed_element_ref": "ME-1", "counter_type": "HO_PERFORMANCE", "delivery_method": "pull"})
    posted.clear()
    counters = {"MM.HoExeAtt": 120, "MM.HoFailTooLate": 9}
    assert client.post("/pm-reports", json={"managedElementRef": "ME-1", "counterType": "HO_PERFORMANCE", "measurements": [
        {"cellId": "201", "relation": "201-202", "values": counters, "timestamp": "2026-01-01T00:00:00Z"}]}).status_code == 201
    assert posted[0]["payload"]["values"] == counters and posted[0]["payload"]["relation"] == "201-202"
    assert client.post("/pm-reports", json={"managedElementRef": "ME-1", "counterType": "HO_PERFORMANCE", "measurements": [
        {"cellId": "201", "timestamp": "2026-01-01T00:00:00Z"}]}).status_code == 422
