"""Tests for A1 Related SMOS (A1 Related LLD sections 1-3).
Run with: pytest smo/a1-related/tests -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smo_shared.db import Base, get_session

from app.a1_termination_client import A1TerminationClient
from app.main import app, get_a1_termination_client
from app.models import A1EIType, A1Policy, PolicyStatusSubscription


class FakeA1Termination(A1TerminationClient):
    """Stands in for the mock Near-RT RIC over HTTP — same contract,
    no network call, so these stay unit tests. The real HTTP contract
    against the actual mock service is covered by
    mock-near-rt-ric/tests and the cross-service integration suite.
    """

    def __init__(self):
        self.policies = {}

    def create_policy(self, near_rt_ric_id, policy_type_id, policy_object):
        status = "REJECTED" if not policy_object else "ENFORCED"
        near_rt_ric_policy_id = "mock-nrt-policy-1"
        self.policies[near_rt_ric_policy_id] = status
        return {"policyId": near_rt_ric_policy_id, "enforcementStatus": status,
                "rejectionReason": None if policy_object else "empty policyObject"}

    def update_policy(self, policy_id, policy_object):
        return {"enforcementStatus": "REJECTED" if not policy_object else "ENFORCED"}

    def delete_policy(self, policy_id):
        pass

    def query_policy_status(self, policy_id):
        return {"enforcementStatus": "ENFORCED"}


@pytest.fixture
def client():
    # StaticPool: plain "sqlite://" opens a NEW blank in-memory DB per pooled
    # connection, and FastAPI's per-request get_session override would grab a
    # different connection than the one create_all ran on ("no such table").
    # One shared connection for the whole test, matching the real deployment's
    # one-shared-Postgres-instance topology closely enough for a unit test.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[A1Policy.__table__, PolicyStatusSubscription.__table__, A1EIType.__table__])
    TestSession = sessionmaker(bind=engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_a1_termination_client] = lambda: FakeA1Termination()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_query_policy_types_returns_known_catalog(client):
    resp = client.get("/policy-types")
    names = {t["policyTypeId"] for t in resp.json()}
    assert "ORAN_QoSandTSP_6.0.1" in names


def test_create_policy_unknown_type_rejected(client):
    resp = client.post("/policies", json={"policyTypeId": "NOT_A_REAL_TYPE", "policyObject": {"x": 1}, "nearRtRicId": "ric1", "creatorId": "rapp-1"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "POLICY_TYPE_NOT_SUPPORTED"


def test_create_policy_enforced_via_southbound_call(client):
    """The headline fix in this pass: enforcementStatus now reflects a
    real (mocked) Near-RT RIC round trip, not a value fabricated locally.
    """
    resp = client.post("/policies", json={"policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell1"}, "nearRtRicId": "ric1", "creatorId": "rapp-1"})
    assert resp.status_code == 201
    assert resp.json()["enforcementStatus"] == "ENFORCED"


def test_create_policy_with_empty_object_is_rejected_by_southbound(client):
    resp = client.post("/policies", json={"policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {}, "nearRtRicId": "ric1", "creatorId": "rapp-1"})
    assert resp.json()["enforcementStatus"] == "REJECTED"


def test_query_policy_status_refreshes_from_southbound(client):
    """R1GAP's cache/pass-through duality: query_policy_status is a LIVE
    call, not just a DB read — this test proves the mirror gets refreshed.
    """
    created = client.post("/policies", json={"policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {}, "nearRtRicId": "ric1", "creatorId": "rapp-1"}).json()
    assert created["enforcementStatus"] == "REJECTED"
    # FakeA1Termination.query_policy_status always answers ENFORCED, regardless
    # of prior state — proves the value is genuinely re-fetched, not cached.
    status = client.get(f"/policies/{created['policyId']}/status")
    assert status.json()["enforcementStatus"] == "ENFORCED"


def test_subscription_scope_and_policy_id_list_conflict(client):
    resp = client.post("/policies/subscriptions", json={
        "notificationDestination": "http://consumer/callback",
        "subscriptionScope": "OWN",
        "policyIdList": ["p1"],
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "SUBSCRIPTION_SCOPE_CONFLICT"


def test_subscription_scope_alone_is_valid(client):
    resp = client.post("/policies/subscriptions", json={"notificationDestination": "http://consumer/callback", "subscriptionScope": "ALL"})
    assert resp.status_code == 201


def test_query_policy_returns_created_policy(client):
    """GET /policies/{id} had zero test coverage at all — five whole
    routes (this, PUT, DELETE on policies, DELETE on subscriptions, and
    DELETE on ei-types) were never exercised before this pass.
    """
    created = client.post("/policies", json={
        "policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell1"}, "nearRtRicId": "ric1", "creatorId": "rapp-1",
    }).json()

    resp = client.get(f"/policies/{created['policyId']}")
    assert resp.status_code == 200
    assert resp.json()["policyId"] == created["policyId"]
    assert resp.json()["policyObject"] == {"scope": "cell1"}
    assert resp.json()["enforcementStatus"] == "ENFORCED"


def test_query_policy_for_unknown_id_is_a_genuine_error_not_404(client):
    """query_policy has no guard for a missing row — db.get returns None
    and _policy_view(None) crashes. Asserting that explicitly, the same
    pattern nfo/tests/test_main.py uses for its own unguarded-None path,
    rather than silently avoiding the case.
    """
    from fastapi.testclient import TestClient as _TestClient
    from app.main import app as _app
    raw = _TestClient(_app, raise_server_exceptions=False)
    resp = raw.get(f"/policies/{uuid.uuid4()}")
    assert resp.status_code == 500


def test_update_policy_reflects_southbound_enforcement_status(client):
    created = client.post("/policies", json={
        "policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {}, "nearRtRicId": "ric1", "creatorId": "rapp-1",
    }).json()
    assert created["enforcementStatus"] == "REJECTED"

    resp = client.put(f"/policies/{created['policyId']}", json={"scope": "cell2"})
    assert resp.status_code == 200
    assert resp.json()["enforcementStatus"] == "ENFORCED"
    assert resp.json()["policyObject"] == {"scope": "cell2"}


def test_delete_policy_removes_it(client):
    created = client.post("/policies", json={
        "policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell1"}, "nearRtRicId": "ric1", "creatorId": "rapp-1",
    }).json()

    resp = client.delete(f"/policies/{created['policyId']}")
    assert resp.status_code == 204

    from fastapi.testclient import TestClient as _TestClient
    from app.main import app as _app
    raw = _TestClient(_app, raise_server_exceptions=False)
    # same unguarded-None path as the query test above — proves the row is
    # actually gone (a query against a still-existing row would 200).
    assert raw.get(f"/policies/{created['policyId']}").status_code == 500


def test_delete_unknown_policy_is_idempotent(client):
    """delete_policy's `if p is not None` guard — never exercised for a
    missing id before this pass.
    """
    resp = client.delete(f"/policies/{uuid.uuid4()}")
    assert resp.status_code == 204


def test_delete_policy_calls_southbound_delete(client):
    """The mapping-store role's whole point: DELETE must reach the
    Near-RT RIC too, not just drop the local mirror row.
    """
    calls = []

    class RecordingA1Termination(FakeA1Termination):
        def delete_policy(self, policy_id):
            calls.append(policy_id)

    shared = RecordingA1Termination()
    app.dependency_overrides[get_a1_termination_client] = lambda: shared

    created = client.post("/policies", json={
        "policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell1"}, "nearRtRicId": "ric1", "creatorId": "rapp-1",
    }).json()
    client.delete(f"/policies/{created['policyId']}")

    assert calls == ["mock-nrt-policy-1"]


def test_unsubscribe_policy_status_removes_subscription(client):
    sub = client.post("/policies/subscriptions", json={"notificationDestination": "http://consumer/callback", "subscriptionScope": "ALL"}).json()
    resp = client.delete(f"/policies/subscriptions/{sub['subscriptionId']}")
    assert resp.status_code == 204


def test_unsubscribe_unknown_subscription_is_idempotent(client):
    resp = client.delete(f"/policies/subscriptions/{uuid.uuid4()}")
    assert resp.status_code == 204


def test_deregister_ei_type_removes_it(client, monkeypatch):
    from smo_shared import r1_client as r1_client_module

    class FakeResponse:
        status_code = 201
        def json(self):
            return {"registrationId": "11111111-1111-1111-1111-111111111111"}

    monkeypatch.setattr(r1_client_module.R1Client, "post", lambda self, path, json=None, **kw: FakeResponse())
    client.post("/ei-types/register", params={
        "ei_type_id": "ei-1", "registered_by": "rapp-1",
        "dme_namespace": "RAN", "dme_name": "CoverageIssue", "dme_version": "1.0.0",
    })

    resp = client.delete("/ei-types/ei-1")
    assert resp.status_code == 204


def test_deregister_unknown_ei_type_is_idempotent(client):
    """deregister_ei_type's `if ei is not None` guard — never exercised
    for a missing id before this pass.
    """
    resp = client.delete("/ei-types/never-registered")
    assert resp.status_code == 204


def test_register_ei_type_wraps_dme_registration(client, monkeypatch):
    """A1 Related LLD section 3: RegisterEIType is NOT a distinct R1AP
    call — it wraps DME's RegisterDMEType. Mocking R1Client.post here
    (unit test); the real cross-service call is covered by the
    integration suite.
    """
    from smo_shared import r1_client as r1_client_module

    class FakeResponse:
        status_code = 201
        def json(self):
            return {"registrationId": "11111111-1111-1111-1111-111111111111"}

    monkeypatch.setattr(r1_client_module.R1Client, "post", lambda self, path, json=None, **kw: FakeResponse())

    resp = client.post("/ei-types/register", params={
        "ei_type_id": "ei-1", "registered_by": "rapp-1",
        "dme_namespace": "RAN", "dme_name": "CoverageIssue", "dme_version": "1.0.0",
    })
    assert resp.status_code == 200
    assert resp.json()["eiSourceDmeTypeId"] == "11111111-1111-1111-1111-111111111111"


def test_health_endpoint_answers_the_callback_url_register_ei_type_registers(client):
    """OPEN_ITEMS.md section 5: register_ei_type registers
    http://a1-related:8000/health as this producer's health-supervision
    callback with DME, but no route ever answered it — a poller hitting
    that URL would 404. Confirms the route now exists and returns 200.
    """
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_update_policy_notifies_matching_subscriber_on_status_change(client, monkeypatch):
    """OPEN_ITEMS.md section 5: SubscribePolicyStatus/UnsubscribePolicyStatus
    were pure no-ops with zero delivery anywhere. This is the headline
    fix — a real status change now actually reaches a matching
    subscriber's notificationDestination.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    created = client.post("/policies", json={
        "policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell1"}, "nearRtRicId": "ric1", "creatorId": "rapp-1",
    }).json()
    assert created["enforcementStatus"] == "ENFORCED"

    client.post("/policies/subscriptions", json={
        "notificationDestination": "http://consumer/callback", "policyIdList": [created["policyId"]],
    })

    # FakeA1Termination.update_policy rejects an empty policyObject — a real status change.
    client.put(f"/policies/{created['policyId']}", json={})

    assert len(calls) == 1
    assert calls[0][0] == "http://consumer/callback"
    assert calls[0][1]["policyId"] == created["policyId"]
    assert calls[0][1]["enforcementStatus"] == "REJECTED"


def test_update_policy_does_not_notify_when_status_unchanged(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    created = client.post("/policies", json={
        "policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell1"}, "nearRtRicId": "ric1", "creatorId": "rapp-1",
    }).json()
    client.post("/policies/subscriptions", json={"notificationDestination": "http://consumer/callback"})

    # Non-empty policyObject stays ENFORCED per FakeA1Termination — no real change.
    client.put(f"/policies/{created['policyId']}", json={"scope": "cell2"})

    assert calls == []


def test_query_policy_status_notifies_on_refreshed_status_change(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    created = client.post("/policies", json={
        "policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {}, "nearRtRicId": "ric1", "creatorId": "rapp-1",
    }).json()
    assert created["enforcementStatus"] == "REJECTED"
    client.post("/policies/subscriptions", json={"notificationDestination": "http://consumer/callback"})

    # FakeA1Termination.query_policy_status always answers ENFORCED — a real change from REJECTED.
    client.get(f"/policies/{created['policyId']}/status")

    assert len(calls) == 1
    assert calls[0][1]["enforcementStatus"] == "ENFORCED"


def test_notification_is_not_sent_to_subscriber_filtered_out_by_policy_type(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    created = client.post("/policies", json={
        "policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell1"}, "nearRtRicId": "ric1", "creatorId": "rapp-1",
    }).json()
    client.post("/policies/subscriptions", json={
        "notificationDestination": "http://consumer/callback",
        "policyTypeIdList": ["ORAN_TrafficSteeringPreference_6.0.1"],
    })

    client.put(f"/policies/{created['policyId']}", json={})  # -> REJECTED, a real change

    assert calls == []


def test_notification_delivery_survives_unreachable_subscriber(client, monkeypatch):
    import httpx as httpx_module

    def raise_error(url, json=None, timeout=None):
        raise httpx_module.ConnectError("unreachable")

    monkeypatch.setattr("app.main.httpx.post", raise_error)

    created = client.post("/policies", json={
        "policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell1"}, "nearRtRicId": "ric1", "creatorId": "rapp-1",
    }).json()
    client.post("/policies/subscriptions", json={"notificationDestination": "http://consumer/callback"})

    resp = client.put(f"/policies/{created['policyId']}", json={})  # must not raise
    assert resp.status_code == 200


def test_query_policies_returns_every_policy_unfiltered(client):
    """OPEN_ITEMS.md section 5: no policy list/query-by-filter endpoint
    existed at all — only GET /policies/{id}.
    """
    client.post("/policies", json={"policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell1"}, "nearRtRicId": "ric1", "creatorId": "rapp-1"})
    client.post("/policies", json={"policyTypeId": "ORAN_TrafficSteeringPreference_6.0.1", "policyObject": {"scope": "cell2"}, "nearRtRicId": "ric2", "creatorId": "rapp-2"})

    resp = client.get("/policies")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_query_policies_filters_by_policy_type_id(client):
    client.post("/policies", json={"policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell1"}, "nearRtRicId": "ric1", "creatorId": "rapp-1"})
    client.post("/policies", json={"policyTypeId": "ORAN_TrafficSteeringPreference_6.0.1", "policyObject": {"scope": "cell2"}, "nearRtRicId": "ric1", "creatorId": "rapp-1"})

    resp = client.get("/policies", params={"policy_type_id": "ORAN_TrafficSteeringPreference_6.0.1"})
    types = [p["policyTypeId"] for p in resp.json()]
    assert types == ["ORAN_TrafficSteeringPreference_6.0.1"]


def test_query_policies_filters_by_near_rt_ric_id(client):
    client.post("/policies", json={"policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell1"}, "nearRtRicId": "ric1", "creatorId": "rapp-1"})
    client.post("/policies", json={"policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell2"}, "nearRtRicId": "ric2", "creatorId": "rapp-1"})

    resp = client.get("/policies", params={"near_rt_ric_id": "ric2"})
    rics = [p["nearRtRicId"] for p in resp.json()]
    assert rics == ["ric2"]


def test_query_policies_filters_by_creator_id(client):
    client.post("/policies", json={"policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell1"}, "nearRtRicId": "ric1", "creatorId": "rapp-1"})
    client.post("/policies", json={"policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell2"}, "nearRtRicId": "ric1", "creatorId": "rapp-2"})

    resp = client.get("/policies", params={"creator_id": "rapp-2"})
    assert len(resp.json()) == 1
    assert resp.json()[0]["policyObject"] == {"scope": "cell2"}


def test_query_policies_with_no_matching_filter_returns_empty_list(client):
    client.post("/policies", json={"policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell1"}, "nearRtRicId": "ric1", "creatorId": "rapp-1"})

    resp = client.get("/policies", params={"near_rt_ric_id": "ric-does-not-exist"})
    assert resp.json() == []
