"""Route-level tests for RAN NF OAM SMOS's WriteConfigurationChanges
(RAN NF OAM LLD section 5.1) — the actual NETCONF dispatch, previously
elided behind a comment that recorded every sub_change as APPLIED without
dispatching anything. Run with: pytest smo/ran-nf-oam/tests -q
"""

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smo_shared.db import Base, get_session
from smo_shared.testing import make_test_engine

from app.main import app
from app.models import Alarm, CMSchemaCache, ManagedEntity, O1AdaptorEndpoint, PMSubscription, SoftwareManagementJob, WriteConfigJob, WriteConfigSubChange


@pytest.fixture
def db_session_factory():
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[
        O1AdaptorEndpoint.__table__, ManagedEntity.__table__, Alarm.__table__, CMSchemaCache.__table__,
        WriteConfigJob.__table__, WriteConfigSubChange.__table__, PMSubscription.__table__, SoftwareManagementJob.__table__,
    ])
    return sessionmaker(bind=engine)


@pytest.fixture
def client(db_session_factory):
    def override_get_session():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _make_me(db_session_factory, protocol="NETCONF", health="ACTIVE", last_heartbeat_at=None):
    db = db_session_factory()
    endpoint = O1AdaptorEndpoint(managed_element_ref="ME-1", adaptor_uri="http://adaptor:9000/netconf",
                                  protocol_support=[protocol], health_status=health, last_heartbeat_at=last_heartbeat_at)
    db.add(endpoint)
    db.flush()
    me = ManagedEntity(managed_element_ref="ME-1", entity_type="O-DU", o1_protocol=protocol, o1_adaptor_endpoint_id=endpoint.endpoint_id)
    db.add(me)
    db.commit()
    db.close()


def test_config_change_dispatches_netconf_and_applies(client, db_session_factory, monkeypatch):
    _make_me(db_session_factory, protocol="NETCONF")
    monkeypatch.setattr("app.main.send_edit_config", lambda adaptor_uri, target_ref, attribute_changes, message_id, operation="merge": True)

    resp = client.post("/config-jobs", json={
        "requestedBy": "operator", "scope": "cell",
        "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"adminState": "UNLOCKED"}}],
    })
    assert resp.status_code == 202
    assert resp.json()["status"] == "COMPLETED"

    job = client.get(f"/config-jobs/{resp.json()['jobId']}").json()
    assert job["subChanges"][0]["status"] == "APPLIED"
    assert job["subChanges"][0]["operation"] == "merge"


def test_config_change_threads_operation_and_allows_empty_payload_for_delete(client, db_session_factory, monkeypatch):
    """SPEC_AUDIT.md item 3: RFC 6241 section 7.2's real edit-config
    `operation` attribute, previously not modeled at all — every write
    was implicitly a merge. A delete legitimately carries no
    attributeChanges, which the pre-fix `change["attributeChanges"]`
    lookup would have raised a KeyError on.
    """
    _make_me(db_session_factory, protocol="NETCONF")
    seen = {}

    def fake_send_edit_config(adaptor_uri, target_ref, attribute_changes, message_id, operation="merge"):
        seen["attribute_changes"] = attribute_changes
        seen["operation"] = operation
        return True

    monkeypatch.setattr("app.main.send_edit_config", fake_send_edit_config)

    resp = client.post("/config-jobs", json={
        "requestedBy": "operator", "scope": "cell",
        "changes": [{"managedElementRef": "ME-1", "operation": "delete"}],
    })
    assert resp.status_code == 202
    assert seen["attribute_changes"] == {}
    assert seen["operation"] == "delete"

    job = client.get(f"/config-jobs/{resp.json()['jobId']}").json()
    assert job["subChanges"][0]["status"] == "APPLIED"
    assert job["subChanges"][0]["operation"] == "delete"


def test_config_change_rejects_when_netconf_rpc_fails(client, db_session_factory, monkeypatch):
    _make_me(db_session_factory, protocol="NETCONF")
    monkeypatch.setattr("app.main.send_edit_config", lambda adaptor_uri, target_ref, attribute_changes, message_id, operation="merge": False)

    resp = client.post("/config-jobs", json={
        "requestedBy": "operator", "scope": "cell",
        "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"adminState": "UNLOCKED"}}],
    })
    assert resp.json()["status"] == "FAILED"

    job = client.get(f"/config-jobs/{resp.json()['jobId']}").json()
    assert job["subChanges"][0]["status"] == "REJECTED"
    assert job["subChanges"][0]["rejectionReason"] == "NETCONF_RPC_FAILED"


def test_config_change_rejects_restconf_me_as_protocol_not_supported(client, db_session_factory, monkeypatch):
    """RESTCONF has no dispatch implementation yet — the confirmed
    protocol (OPEN_ITEMS.md) is NETCONF only, so a RESTCONF-provisioned ME
    is rejected honestly rather than silently treated as applied.
    """
    _make_me(db_session_factory, protocol="RESTCONF")
    monkeypatch.setattr("app.main.send_edit_config", lambda *a, **kw: pytest.fail("should not dispatch to a RESTCONF ME"))

    resp = client.post("/config-jobs", json={
        "requestedBy": "operator", "scope": "cell",
        "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"adminState": "UNLOCKED"}}],
    })
    job = client.get(f"/config-jobs/{resp.json()['jobId']}").json()
    assert job["subChanges"][0]["status"] == "REJECTED"
    assert job["subChanges"][0]["rejectionReason"] == "PROTOCOL_NOT_SUPPORTED"


def test_config_change_rejects_unreachable_endpoint_without_dispatch(client, db_session_factory, monkeypatch):
    _make_me(db_session_factory, protocol="NETCONF", health="UNREACHABLE")
    monkeypatch.setattr("app.main.send_edit_config", lambda *a, **kw: pytest.fail("should not dispatch to an unreachable endpoint"))

    resp = client.post("/config-jobs", json={
        "requestedBy": "operator", "scope": "cell",
        "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"adminState": "UNLOCKED"}}],
    })
    job = client.get(f"/config-jobs/{resp.json()['jobId']}").json()
    assert job["subChanges"][0]["rejectionReason"] == "ENDPOINT_UNREACHABLE"


def test_config_change_rejects_a_stale_active_endpoint_live_without_an_explicit_discover_call(client, db_session_factory, monkeypatch):
    """The heartbeat-aging check (OPEN_ITEMS.md section 2) is computed live
    at this gate now, the same "no scheduler exists anywhere in this
    build" pattern already used for DME's producer health and A1 Related's
    service supervision — so a stale endpoint is caught here even though
    nothing ever called POST /o1-adaptor-endpoints/discover first.
    """
    stale = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=10)
    _make_me(db_session_factory, protocol="NETCONF", health="ACTIVE", last_heartbeat_at=stale)
    monkeypatch.setattr("app.main.send_edit_config", lambda *a, **kw: pytest.fail("should not dispatch to a stale endpoint"))

    resp = client.post("/config-jobs", json={
        "requestedBy": "operator", "scope": "cell",
        "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"adminState": "UNLOCKED"}}],
    })
    job = client.get(f"/config-jobs/{resp.json()['jobId']}").json()
    assert job["subChanges"][0]["rejectionReason"] == "ENDPOINT_UNREACHABLE"


def test_config_change_proceeds_for_a_freshly_heartbeated_active_endpoint(client, db_session_factory, monkeypatch):
    fresh = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=5)
    _make_me(db_session_factory, protocol="NETCONF", health="ACTIVE", last_heartbeat_at=fresh)
    monkeypatch.setattr("app.main.send_edit_config", lambda adaptor_uri, target_ref, attribute_changes, message_id, operation="merge": True)

    resp = client.post("/config-jobs", json={
        "requestedBy": "operator", "scope": "cell",
        "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"adminState": "UNLOCKED"}}],
    })
    job = client.get(f"/config-jobs/{resp.json()['jobId']}").json()
    assert job["subChanges"][0]["status"] == "APPLIED"


def test_discover_endpoints_ages_a_stale_active_endpoint_to_degraded(client, db_session_factory):
    stale = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=10)
    _make_me(db_session_factory, protocol="NETCONF", health="ACTIVE", last_heartbeat_at=stale)

    resp = client.post("/o1-adaptor-endpoints/discover")
    assert resp.status_code == 200
    assert resp.json() == {"checked": 1}

    db = db_session_factory()
    ep = db.query(O1AdaptorEndpoint).filter_by(managed_element_ref="ME-1").one()
    assert ep.health_status == "DEGRADED"
    db.close()


def test_discover_endpoints_leaves_a_fresh_active_endpoint_alone(client, db_session_factory):
    fresh = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=5)
    _make_me(db_session_factory, protocol="NETCONF", health="ACTIVE", last_heartbeat_at=fresh)

    client.post("/o1-adaptor-endpoints/discover")

    db = db_session_factory()
    ep = db.query(O1AdaptorEndpoint).filter_by(managed_element_ref="ME-1").one()
    assert ep.health_status == "ACTIVE"
    db.close()


def test_discover_endpoints_ignores_an_endpoint_that_has_never_heartbeated(client, db_session_factory):
    """last_heartbeat_at stays NULL until the first POST .../heartbeat call
    — must not be treated as "infinitely stale" (None minus now would also
    raise, not just compare wrong).
    """
    _make_me(db_session_factory, protocol="NETCONF", health="ACTIVE", last_heartbeat_at=None)

    resp = client.post("/o1-adaptor-endpoints/discover")
    assert resp.status_code == 200

    db = db_session_factory()
    ep = db.query(O1AdaptorEndpoint).filter_by(managed_element_ref="ME-1").one()
    assert ep.health_status == "ACTIVE"
    db.close()


def test_register_o1_adaptor_endpoint_creates_endpoint_and_managed_entity(client, db_session_factory):
    """RAN NF OAM LLD section 1's own design intent ("per ME's O1 Adaptor
    registers itself into the MnS Registry NRM") previously had no real
    route anywhere in this build — the whole registry could only ever be
    populated by a test fixture reaching directly into the DB.
    """
    resp = client.post("/o1-adaptor-endpoints", json={
        "managedElementRef": "ME-2", "adaptorUri": "http://mock-o1-adaptor:8000/edit-config",
        "protocolSupport": ["NETCONF"], "o1Protocol": "NETCONF", "entityType": "O-DU",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["managedElementRef"] == "ME-2"
    assert body["healthStatus"] == "DISCOVERED"

    db = db_session_factory()
    ep = db.query(O1AdaptorEndpoint).filter_by(managed_element_ref="ME-2").one()
    assert ep.adaptor_uri == "http://mock-o1-adaptor:8000/edit-config"
    assert ep.protocol_support == ["NETCONF"]
    me = db.get(ManagedEntity, "ME-2")
    assert me.entity_type == "O-DU"
    assert me.o1_protocol == "NETCONF"
    assert me.o1_adaptor_endpoint_id == ep.endpoint_id
    db.close()


def test_register_o1_adaptor_endpoint_starts_discovered_not_active(client, db_session_factory):
    """A fresh registration hasn't heartbeated yet — DISCOVERED is the
    FSM's own real starting state, not the model column's own default
    (ACTIVE, kept for other callers' test convenience).
    """
    client.post("/o1-adaptor-endpoints", json={
        "managedElementRef": "ME-3", "adaptorUri": "http://mock-o1-adaptor:8000/edit-config",
        "protocolSupport": ["NETCONF"], "o1Protocol": "NETCONF", "entityType": "O-CU",
    })
    db = db_session_factory()
    ep = db.query(O1AdaptorEndpoint).filter_by(managed_element_ref="ME-3").one()
    assert ep.health_status == "DISCOVERED"
    db.close()


def test_registered_endpoint_can_then_heartbeat_to_active(client, db_session_factory):
    reg = client.post("/o1-adaptor-endpoints", json={
        "managedElementRef": "ME-4", "adaptorUri": "http://mock-o1-adaptor:8000/edit-config",
        "protocolSupport": ["NETCONF"], "o1Protocol": "NETCONF", "entityType": "O-DU",
    }).json()

    resp = client.post(f"/o1-adaptor-endpoints/{reg['endpointId']}/heartbeat")
    assert resp.status_code == 200
    assert resp.json()["healthStatus"] == "ACTIVE"


def test_subscribe_pm_persists_and_returns_granularity_period(client, db_session_factory, monkeypatch):
    """SPEC_AUDIT.md item 4: TS28550_PerfMeasJobCtrlMnS.yaml's
    granularityPeriod (the sampling interval), previously absent
    entirely from PMSubscription — subscribe_pm's own docstring already
    confirms the rest of that job-control shape (schedule/priority/
    reportingPeriod) is a deliberate scope cut, but this one field is
    needed by any real PM subscription regardless of wrapper shape.
    """
    calls = []
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: calls.append((path, json)))

    resp = client.post("/pm-subscriptions", params={
        "managed_element_ref": "ME-1", "counter_type": "PRB.Usage", "delivery_method": "pull", "granularity_period": 900,
    })
    assert resp.status_code == 200
    assert resp.json()["granularityPeriod"] == 900

    db = db_session_factory()
    sub = db.get(PMSubscription, uuid.UUID(resp.json()["subscriptionId"]))
    assert sub.granularity_period == 900


def test_subscribe_pm_without_granularity_period_defaults_to_null(client, db_session_factory, monkeypatch):
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: None)

    resp = client.post("/pm-subscriptions", params={
        "managed_element_ref": "ME-1", "counter_type": "PRB.Usage", "delivery_method": "pull",
    })
    assert resp.status_code == 200
    assert resp.json()["granularityPeriod"] is None


def test_health_endpoint_answers_the_callback_url_subscribe_pm_registers(client):
    """OPEN_ITEMS.md section 5: subscribe_pm registers
    http://ran-nf-oam:8000/health as this producer's health-supervision
    callback with DME, but no route ever answered it — a poller hitting
    that URL would 404. Confirms the route now exists and returns 200.
    """
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_dme_jobs_endpoint_answers_the_callback_url_subscribe_pm_registers(client):
    """OPEN_ITEMS.md section 5: subscribe_pm now also registers
    http://ran-nf-oam:8000/dme-jobs as this producer's jobCallbackUrl —
    DME's own create_data_job/terminate_data_job actually push to it
    now, so this closes the same class of dangling-callback bug the
    /health route closed for the health-supervision URL.
    """
    resp = client.post("/dme-jobs", json={"infoJobIdentity": "job-1", "infoTypeIdentity": "type-1", "infoJobData": {}})
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"

    resp = client.delete("/dme-jobs/job-1")
    assert resp.status_code == 204


def test_ingest_alarm_persists_standard_fault_fields(client, db_session_factory):
    """OPEN_ITEMS.md section 5: the alarm model was missing the standard
    fault fields the wire format (VES/3GPP alarm IRP, per oam's own
    NotifyNewAlarm template) carries — probableCause, specificProblem,
    rootCauseIndicator, correlatedNotifications, proposedRepairActions.
    alarmType (SPEC_AUDIT.md, TS28111_FaultNrm.yaml's AlarmRecord) was
    the one of these still missing after that pass.
    """
    _make_me(db_session_factory)
    other_alarm_id = str(uuid.uuid4())

    resp = client.post("/alarms/ingest", params={
        "source_alarm_id": "src-1", "managed_element_ref": "ME-1", "severity": "critical",
        "probable_cause": "linkFailure", "specific_problem": "Optical link down",
        "root_cause_indicator": True, "correlated_notifications": [other_alarm_id],
        "proposed_repair_actions": "Replace the SFP module.", "alarm_type": "EQUIPMENT_ALARM",
    })
    assert resp.status_code == 200
    alarm_id = resp.json()["alarmId"]

    listing = client.get("/alarms").json()
    assert len(listing) == 1
    alarm = listing[0]
    assert alarm["alarmId"] == alarm_id
    assert alarm["probableCause"] == "linkFailure"
    assert alarm["specificProblem"] == "Optical link down"
    assert alarm["rootCauseIndicator"] is True
    assert alarm["correlatedNotifications"] == [other_alarm_id]
    assert alarm["proposedRepairActions"] == "Replace the SFP module."
    assert alarm["alarmType"] == "EQUIPMENT_ALARM"


def test_ingest_alarm_defaults_fault_fields_when_not_provided(client, db_session_factory):
    """The reference's NotifyNewAlarm fields are all optional on ingest —
    an alarm raised without them must not crash and must default sanely
    (rootCauseIndicator false, correlatedNotifications empty).
    """
    _make_me(db_session_factory)

    resp = client.post("/alarms/ingest", params={
        "source_alarm_id": "src-2", "managed_element_ref": "ME-1", "severity": "minor",
    })
    assert resp.status_code == 200

    alarm = client.get("/alarms").json()[0]
    assert alarm["probableCause"] is None
    assert alarm["specificProblem"] is None
    assert alarm["rootCauseIndicator"] is False
    assert alarm["correlatedNotifications"] == []
    assert alarm["proposedRepairActions"] is None
    assert alarm["alarmType"] is None


def test_query_alarms_filters_by_managed_element_ref(client, db_session_factory):
    db = db_session_factory()
    endpoint1 = O1AdaptorEndpoint(managed_element_ref="ME-1", adaptor_uri="http://adaptor-1:9000/netconf", protocol_support=["NETCONF"])
    endpoint2 = O1AdaptorEndpoint(managed_element_ref="ME-2", adaptor_uri="http://adaptor-2:9000/netconf", protocol_support=["NETCONF"])
    db.add(endpoint1)
    db.add(endpoint2)
    db.flush()
    db.add(ManagedEntity(managed_element_ref="ME-1", entity_type="O-DU", o1_protocol="NETCONF", o1_adaptor_endpoint_id=endpoint1.endpoint_id))
    db.add(ManagedEntity(managed_element_ref="ME-2", entity_type="O-DU", o1_protocol="NETCONF", o1_adaptor_endpoint_id=endpoint2.endpoint_id))
    db.commit()
    db.close()

    client.post("/alarms/ingest", params={"source_alarm_id": "src-1", "managed_element_ref": "ME-1", "severity": "critical"})
    client.post("/alarms/ingest", params={"source_alarm_id": "src-2", "managed_element_ref": "ME-2", "severity": "minor"})

    resp = client.get("/alarms", params={"managed_element_ref": "ME-2"})
    alarms = resp.json()
    assert len(alarms) == 1
    assert alarms[0]["managedElementRef"] == "ME-2"


def test_change_alarm_ack_state(client, db_session_factory):
    _make_me(db_session_factory)
    alarm_id = client.post("/alarms/ingest", params={
        "source_alarm_id": "src-1", "managed_element_ref": "ME-1", "severity": "major",
    }).json()["alarmId"]

    resp = client.patch(f"/alarms/{alarm_id}/ack", params={"new_state": "ACKNOWLEDGED"})
    assert resp.status_code == 200
    assert resp.json()["ackState"] == "ACKNOWLEDGED"


def test_change_alarm_ack_state_records_ack_user_id_and_changed_at(client, db_session_factory):
    """SPEC_AUDIT.md: TS28111_FaultNrm.yaml's AlarmRecord carries
    ackUserId (who acknowledged it) and alarmChangedTime (its own "last
    mutated" timestamp) — PATCH /alarms/{id}/ack never recorded either.
    """
    _make_me(db_session_factory)
    alarm_id = client.post("/alarms/ingest", params={
        "source_alarm_id": "src-1", "managed_element_ref": "ME-1", "severity": "major",
    }).json()["alarmId"]
    assert client.get("/alarms").json()[0]["changedAt"] is None

    resp = client.patch(f"/alarms/{alarm_id}/ack", params={"new_state": "ACKNOWLEDGED", "ack_user_id": "operator-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ackUserId"] == "operator-1"
    assert body["changedAt"] is not None


def test_clear_alarm_sets_cleared_severity_and_metadata(client, db_session_factory):
    """OPEN_ITEMS.md section 5: no alarm-cleared lifecycle existed at
    all — an alarm that stopped recurring on the NF had no way to ever
    be marked resolved. Matches the reference's own NotifyClearedAlarm
    shape: perceivedSeverity=CLEARED, not a separate state field.
    """
    _make_me(db_session_factory)
    alarm_id = client.post("/alarms/ingest", params={
        "source_alarm_id": "src-1", "managed_element_ref": "ME-1", "severity": "critical",
    }).json()["alarmId"]

    resp = client.patch(f"/alarms/{alarm_id}/clear", params={"clear_user_id": "operator-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["severity"] == "cleared"
    assert body["clearUserId"] == "operator-1"
    assert body["clearedAt"] is not None
    assert body["changedAt"] == body["clearedAt"]


def test_cleared_alarm_still_appears_in_query_alarms(client, db_session_factory):
    """Clearing doesn't delete the alarm — it stays queryable, same as
    the reference's own retained-but-cleared alarm records.
    """
    _make_me(db_session_factory)
    alarm_id = client.post("/alarms/ingest", params={
        "source_alarm_id": "src-1", "managed_element_ref": "ME-1", "severity": "critical",
    }).json()["alarmId"]
    client.patch(f"/alarms/{alarm_id}/clear")

    alarms = client.get("/alarms").json()
    assert len(alarms) == 1
    assert alarms[0]["severity"] == "cleared"


def test_clear_alarm_without_clear_user_id_leaves_it_null(client, db_session_factory):
    _make_me(db_session_factory)
    alarm_id = client.post("/alarms/ingest", params={
        "source_alarm_id": "src-1", "managed_element_ref": "ME-1", "severity": "warning",
    }).json()["alarmId"]

    resp = client.patch(f"/alarms/{alarm_id}/clear")
    assert resp.status_code == 200
    assert resp.json()["clearUserId"] is None
    assert resp.json()["clearedAt"] is not None
