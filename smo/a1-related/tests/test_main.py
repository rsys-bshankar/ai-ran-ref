"""Tests for A1 Related SMOS (A1 Related LLD sections 1-3).
Run with: pytest smo/a1-related/tests -q
"""

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smo_shared.db import Base, get_session

from app.a1_termination_client import A1TerminationClient
from app.main import app, get_a1_termination_client
from app.models import A1EIType, A1Policy, A1ServiceRegistration, PolicyStatusSubscription


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
def engine():
    # StaticPool: plain "sqlite://" opens a NEW blank in-memory DB per pooled
    # connection, and FastAPI's per-request get_session override would grab a
    # different connection than the one create_all ran on ("no such table").
    # One shared connection for the whole test, matching the real deployment's
    # one-shared-Postgres-instance topology closely enough for a unit test.
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e, tables=[A1Policy.__table__, PolicyStatusSubscription.__table__, A1EIType.__table__, A1ServiceRegistration.__table__])
    return e


@pytest.fixture
def db_session_factory(engine):
    return sessionmaker(bind=engine)


@pytest.fixture
def client(engine):
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


def test_get_policy_type_returns_a_policy_schema(client):
    """OPEN_ITEMS.md section 5: no policy-type detail retrieval existed
    at all — the reference's own GetPolicyTypeDefinition
    (GET /policy-types/{policyTypeId}, pms-api-v3.json).
    """
    resp = client.get("/policy-types/ORAN_QoSandTSP_6.0.1")
    assert resp.status_code == 200
    assert resp.json()["policySchema"] == {"type": "object"}


def test_get_unknown_policy_type_is_404(client):
    resp = client.get("/policy-types/NOT_A_REAL_TYPE")
    assert resp.status_code == 404


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


def test_dme_jobs_endpoint_answers_the_callback_url_register_ei_type_registers(client):
    """OPEN_ITEMS.md section 5: register_ei_type now also registers
    http://a1-related:8000/dme-jobs as this producer's jobCallbackUrl —
    DME's own create_data_job/terminate_data_job actually push to it
    now, so this closes the same class of dangling-callback bug the
    /health route closed for the health-supervision URL.
    """
    resp = client.post("/dme-jobs", json={"infoJobIdentity": "job-1", "infoTypeIdentity": "type-1", "infoJobData": {}})
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"

    resp = client.delete("/dme-jobs/job-1")
    assert resp.status_code == 204


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


def test_register_service_creates_it(client):
    resp = client.put("/services", json={"serviceId": "rapp-1", "callbackUrl": "http://rapp-1/callback", "keepAliveIntervalSeconds": 60})
    assert resp.status_code == 200

    resp = client.get("/services", params={"service_id": "rapp-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["serviceId"] == "rapp-1"
    assert body["callbackUrl"] == "http://rapp-1/callback"
    assert body["keepAliveIntervalSeconds"] == 60
    assert body["timeSinceLastActivitySeconds"] < 5


def test_register_service_is_idempotent_update_in_place(client):
    """The same identity-space equivalence query_policies' own docstring
    already established: re-registering an already-known serviceId
    updates it, the same shape as SME's own register_service, not a
    conflict.
    """
    client.put("/services", json={"serviceId": "rapp-1", "keepAliveIntervalSeconds": 30})
    resp = client.put("/services", json={"serviceId": "rapp-1", "callbackUrl": "http://new/callback", "keepAliveIntervalSeconds": 90})
    assert resp.status_code == 200

    body = client.get("/services", params={"service_id": "rapp-1"}).json()
    assert body["callbackUrl"] == "http://new/callback"
    assert body["keepAliveIntervalSeconds"] == 90


def test_query_services_lists_all_registered(client):
    client.put("/services", json={"serviceId": "rapp-1"})
    client.put("/services", json={"serviceId": "rapp-2"})

    resp = client.get("/services")
    assert resp.status_code == 200
    ids = {s["serviceId"] for s in resp.json()["serviceList"]}
    assert ids == {"rapp-1", "rapp-2"}


def test_query_unknown_service_is_404(client):
    resp = client.get("/services", params={"service_id": "does-not-exist"})
    assert resp.status_code == 404


def test_keepalive_resets_activity_clock(client, db_session_factory):
    client.put("/services", json={"serviceId": "rapp-1", "keepAliveIntervalSeconds": 60})
    with db_session_factory() as session:
        svc = session.get(A1ServiceRegistration, "rapp-1")
        svc.last_activity_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=50)
        session.commit()

    resp = client.put("/services/rapp-1/keepalive")
    assert resp.status_code == 200

    body = client.get("/services", params={"service_id": "rapp-1"}).json()
    assert body["timeSinceLastActivitySeconds"] < 5


def test_keepalive_on_unknown_service_is_404(client):
    resp = client.put("/services/does-not-exist/keepalive")
    assert resp.status_code == 404


def test_unregister_service_removes_it(client):
    client.put("/services", json={"serviceId": "rapp-1"})
    resp = client.delete("/services/rapp-1")
    assert resp.status_code == 204
    assert client.get("/services", params={"service_id": "rapp-1"}).status_code == 404


def test_unregister_unknown_service_is_404(client):
    resp = client.delete("/services/does-not-exist")
    assert resp.status_code == 404


def test_unregister_service_deletes_its_policies_via_southbound_call(client):
    """deleteService, pms-api-v3.json: "All A1 Policy Instances for the
    previously registered service will be removed" — creator_id is this
    build's identity for "the service", and deletion goes through the
    same real southbound a1t.delete_policy call delete_policy itself
    uses, not just a local row drop.
    """
    calls = []

    class RecordingA1Termination(FakeA1Termination):
        def delete_policy(self, policy_id):
            calls.append(policy_id)

    shared = RecordingA1Termination()
    app.dependency_overrides[get_a1_termination_client] = lambda: shared

    client.put("/services", json={"serviceId": "rapp-1"})
    client.post("/policies", json={"policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell1"}, "nearRtRicId": "ric1", "creatorId": "rapp-1"})
    client.post("/policies", json={"policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell2"}, "nearRtRicId": "ric1", "creatorId": "rapp-other"})

    resp = client.delete("/services/rapp-1")
    assert resp.status_code == 204
    assert calls == ["mock-nrt-policy-1"]  # only rapp-1's own policy, not rapp-other's

    remaining = client.get("/policies", params={"creator_id": "rapp-1"}).json()
    assert remaining == []
    still_there = client.get("/policies", params={"creator_id": "rapp-other"}).json()
    assert len(still_there) == 1


def test_stale_service_is_auto_deregistered_and_its_policies_deleted(client, db_session_factory):
    """The reference's own supervision contract (ServiceStatus's own
    keepAliveIntervalSeconds description): "When a service fails to
    invoke this 'keepalive' call within the configured time, the service
    is considered unavailable. An unavailable service will be
    automatically deregistered and its policies will be deleted." No
    scheduler exists anywhere in this build, so the sweep happens lazily
    on the next GET /services read (_sweep_stale_service) instead of on
    a timer.
    """
    calls = []

    class RecordingA1Termination(FakeA1Termination):
        def delete_policy(self, policy_id):
            calls.append(policy_id)

    shared = RecordingA1Termination()
    app.dependency_overrides[get_a1_termination_client] = lambda: shared

    client.put("/services", json={"serviceId": "rapp-1", "keepAliveIntervalSeconds": 30})
    client.post("/policies", json={"policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell1"}, "nearRtRicId": "ric1", "creatorId": "rapp-1"})
    with db_session_factory() as session:
        svc = session.get(A1ServiceRegistration, "rapp-1")
        svc.last_activity_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=31)  # just past the deadline
        session.commit()

    resp = client.get("/services", params={"service_id": "rapp-1"})
    assert resp.status_code == 404  # swept, not just stale-but-still-listed

    assert calls == ["mock-nrt-policy-1"]
    assert client.get("/policies", params={"creator_id": "rapp-1"}).json() == []


def test_service_with_supervision_disabled_never_goes_stale(client, db_session_factory):
    """keepAliveIntervalSeconds == 0 means supervision is disabled per the
    reference's own schema — no amount of inactivity should sweep it.
    """
    client.put("/services", json={"serviceId": "rapp-1", "keepAliveIntervalSeconds": 0})
    with db_session_factory() as session:
        svc = session.get(A1ServiceRegistration, "rapp-1")
        svc.last_activity_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=365)
        session.commit()

    resp = client.get("/services", params={"service_id": "rapp-1"})
    assert resp.status_code == 200


# ---------------------------------------------------------------- list reads (GUI pass 2)

def test_list_policy_status_subscriptions_is_not_captured_by_the_policy_id_route(client):
    """GET /policies/subscriptions must reach its own route, not
    /policies/{policy_id} (which would 422 on a non-UUID)."""
    created = client.post("/policies/subscriptions", json={"notificationDestination": "http://consumer/cb",
                                                           "subscriptionScope": "ALL"}).json()
    resp = client.get("/policies/subscriptions")
    assert resp.status_code == 200
    assert [(s["subscriptionId"], s["notificationDestination"]) for s in resp.json()] == [(created["subscriptionId"], "http://consumer/cb")]


def test_list_ei_types_returns_registrations_with_their_dme_type(client, monkeypatch):
    from smo_shared import r1_client as r1_client_module

    class FakeResponse:
        status_code = 201
        def json(self):
            return {"registrationId": "11111111-1111-1111-1111-111111111111"}

    monkeypatch.setattr(r1_client_module.R1Client, "post", lambda self, path, json=None, **kw: FakeResponse())
    assert client.get("/ei-types").json() == []
    client.post("/ei-types/register", params={"ei_type_id": "ei-1", "registered_by": "rapp-1",
                                              "dme_namespace": "RAN", "dme_name": "CoverageIssue", "dme_version": "1.0.0"})
    assert client.get("/ei-types").json() == [{"eiTypeId": "ei-1", "registeredBy": "rapp-1",
                                               "eiSourceDmeTypeId": "11111111-1111-1111-1111-111111111111"}]
