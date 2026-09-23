"""Tests for SME (Foundational Platform LLD sections 2.1-2.3).
Run with: pytest smo/sme/tests -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smo_shared.db import Base, get_session

from app.main import app
from app.models import ServiceAuthzPolicy, ServiceEventSubscription, ServiceProfile


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[ServiceProfile.__table__, ServiceAuthzPolicy.__table__, ServiceEventSubscription.__table__])
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


def register_body(service_name="training-status", producer="rapp-1", **extra):
    return {"serviceName": service_name, "producerId": producer, "endpoint": "http://rapp-1/svc",
            "version": "1.0", "moduleScope": "ai-ml-workflow", **extra}


def test_register_service_returns_service_id(client):
    resp = client.post("/published-apis/v1/rapp-1/service-apis", json=register_body())
    assert resp.status_code == 201
    assert "serviceId" in resp.json()


def test_duplicate_service_name_different_producer_is_conflict(client):
    """Foundational Platform LLD section 2.3's decision: (serviceName,
    producerId) uniqueness — same name, different producer, is rejected.
    """
    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(producer="rapp-1"))
    resp = client.post("/published-apis/v1/rapp-2/service-apis", json=register_body(producer="rapp-2"))
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "SERVICE_NAME_CONFLICT"


def test_same_producer_can_reregister_same_service_name(client):
    """Same producer re-registering the same name updates in place
    (idempotent RegisterService, section 2.3) — same serviceId returned,
    not a conflict and not a duplicate row.
    """
    first = client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(version="1.0")).json()
    second = client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(version="2.0")).json()
    assert first["serviceId"] == second["serviceId"]

    listing = client.get("/published-apis/v1/rapp-1/service-apis").json()
    assert len(listing) == 1
    assert listing[0]["version"] == "2.0"


def test_discovery_hides_service_from_unauthorized_consumer(client):
    """Section 2.2's decision: one gate. An rApp not in allowedConsumers
    never learns the service exists at all.
    """
    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(allowedConsumers=["rapp-2"]))
    resp = client.get("/service-apis/v1/allServiceAPIs", params={"api_invoker_id": "rapp-3"})
    assert resp.json() == []


def test_discovery_shows_service_to_authorized_consumer(client):
    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(allowedConsumers=["rapp-2"]))
    resp = client.get("/service-apis/v1/allServiceAPIs", params={"api_invoker_id": "rapp-2"})
    assert len(resp.json()) == 1


def test_discovery_shows_service_with_no_authz_restriction(client):
    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body())
    resp = client.get("/service-apis/v1/allServiceAPIs", params={"api_invoker_id": "anyone"})
    assert len(resp.json()) == 1


def test_subscribe_events_rejects_unknown_event_type(client):
    resp = client.post("/capif-events/v1/rapp-1/subscriptions", json={
        "subscriberId": "rapp-1", "eventTypes": ["NOT_A_REAL_EVENT"], "callbackUri": "http://rapp-1/cb",
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "SUBSCRIPTION_SCOPE_CONFLICT"


def test_subscribe_events_accepts_known_event_types(client):
    resp = client.post("/capif-events/v1/rapp-1/subscriptions", json={
        "subscriberId": "rapp-1", "eventTypes": ["SERVICE_API_AVAILABLE", "SERVICE_API_UPDATE"], "callbackUri": "http://rapp-1/cb",
    })
    assert resp.status_code == 201


def test_deregister_removes_service(client):
    reg = client.post("/published-apis/v1/rapp-1/service-apis", json=register_body()).json()
    client.delete(f"/published-apis/v1/rapp-1/service-apis/{reg['serviceId']}")
    resp = client.get("/published-apis/v1/rapp-1/service-apis")
    assert resp.json() == []


def test_deregister_unknown_service_is_idempotent(client):
    resp = client.delete(f"/published-apis/v1/rapp-1/service-apis/{uuid.uuid4()}")
    assert resp.status_code == 204


def test_deregister_by_wrong_producer_is_a_silent_noop(client):
    """deregister_service's `profile.producer_id == apf_id` guard — a
    producer can't delete another producer's service. Never exercised
    before this pass: only the matching-owner success path had coverage.
    """
    reg = client.post("/published-apis/v1/rapp-1/service-apis", json=register_body()).json()

    resp = client.delete(f"/published-apis/v1/rapp-2/service-apis/{reg['serviceId']}")
    assert resp.status_code == 204  # silent no-op, not an error — same shape as an unknown id

    still_there = client.get("/published-apis/v1/rapp-1/service-apis").json()
    assert len(still_there) == 1


def test_discover_services_filters_by_api_name(client):
    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(service_name="training-status"))
    client.post("/published-apis/v1/rapp-2/service-apis", json=register_body(service_name="coverage-analysis", producer="rapp-2"))

    resp = client.get("/service-apis/v1/allServiceAPIs", params={"api_invoker_id": "anyone", "api_name": "coverage-analysis"})
    names = [s["serviceName"] for s in resp.json()]
    assert names == ["coverage-analysis"]


def test_discover_services_filters_by_api_version(client):
    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(version="1.0"))
    client.post("/published-apis/v1/rapp-2/service-apis", json=register_body(service_name="other-service", producer="rapp-2", version="2.0"))

    resp = client.get("/service-apis/v1/allServiceAPIs", params={"api_invoker_id": "anyone", "api_version": "2.0"})
    versions = [s["version"] for s in resp.json()]
    assert versions == ["2.0"]


def test_unsubscribe_events_removes_subscription(client, db_session_factory):
    """unsubscribe_events (DELETE /capif-events/v1/{subscriber}/subscriptions/{id})
    had zero test coverage at all before this pass.
    """
    sub = client.post("/capif-events/v1/rapp-1/subscriptions", json={
        "subscriberId": "rapp-1", "eventTypes": ["SERVICE_API_AVAILABLE"], "callbackUri": "http://rapp-1/cb",
    }).json()

    resp = client.delete(f"/capif-events/v1/rapp-1/subscriptions/{sub['subscriptionId']}")
    assert resp.status_code == 204

    with db_session_factory() as session:
        assert session.get(ServiceEventSubscription, uuid.UUID(sub["subscriptionId"])) is None


def test_unsubscribe_unknown_subscription_is_idempotent(client):
    resp = client.delete(f"/capif-events/v1/rapp-1/subscriptions/{uuid.uuid4()}")
    assert resp.status_code == 204


def test_unsubscribe_by_wrong_subscriber_is_a_silent_noop(client, db_session_factory):
    sub = client.post("/capif-events/v1/rapp-1/subscriptions", json={
        "subscriberId": "rapp-1", "eventTypes": ["SERVICE_API_AVAILABLE"], "callbackUri": "http://rapp-1/cb",
    }).json()

    resp = client.delete(f"/capif-events/v1/rapp-2/subscriptions/{sub['subscriptionId']}")
    assert resp.status_code == 204  # silent no-op, not an error

    with db_session_factory() as session:
        assert session.get(ServiceEventSubscription, uuid.UUID(sub["subscriptionId"])) is not None


def test_register_service_notifies_only_matching_subscribers(client, monkeypatch):
    """OPEN_ITEMS.md section 5: notify_service_change was real logic
    (event-type filtering, the same authz gate discover_services uses,
    best-effort delivery) but was never actually called from anywhere —
    its own docstring said "wired in as a follow-up". This is the
    headline fix: registering a service now actually reaches a matching
    subscriber's callback.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/capif-events/v1/rapp-2/subscriptions", json={
        "subscriberId": "rapp-2", "eventTypes": ["SERVICE_API_AVAILABLE"], "callbackUri": "http://rapp-2/cb",
    })
    client.post("/capif-events/v1/rapp-3/subscriptions", json={
        "subscriberId": "rapp-3", "eventTypes": ["SERVICE_API_UPDATE"], "callbackUri": "http://rapp-3/cb",
    })
    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body())

    assert len(calls) == 1
    assert calls[0][0] == "http://rapp-2/cb"
    assert calls[0][1]["eventType"] == "SERVICE_API_AVAILABLE"


def test_reregister_service_notifies_with_update_event_type(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/capif-events/v1/rapp-2/subscriptions", json={
        "subscriberId": "rapp-2", "eventTypes": ["SERVICE_API_AVAILABLE", "SERVICE_API_UPDATE"], "callbackUri": "http://rapp-2/cb",
    })
    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(version="1.0"))
    calls.clear()  # discard the create-time SERVICE_API_AVAILABLE notification

    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(version="2.0"))

    assert len(calls) == 1
    assert calls[0][1]["eventType"] == "SERVICE_API_UPDATE"


def test_deregister_service_notifies_with_unavailable_event_type(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/capif-events/v1/rapp-2/subscriptions", json={
        "subscriberId": "rapp-2", "eventTypes": ["SERVICE_API_UNAVAILABLE"], "callbackUri": "http://rapp-2/cb",
    })
    reg = client.post("/published-apis/v1/rapp-1/service-apis", json=register_body()).json()
    calls.clear()  # discard the create-time notification (different event type anyway)

    client.delete(f"/published-apis/v1/rapp-1/service-apis/{reg['serviceId']}")

    assert len(calls) == 1
    assert calls[0][1]["eventType"] == "SERVICE_API_UNAVAILABLE"


def test_deregister_by_wrong_producer_does_not_notify(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/capif-events/v1/rapp-2/subscriptions", json={
        "subscriberId": "rapp-2", "eventTypes": ["SERVICE_API_UNAVAILABLE"], "callbackUri": "http://rapp-2/cb",
    })
    reg = client.post("/published-apis/v1/rapp-1/service-apis", json=register_body()).json()
    calls.clear()

    client.delete(f"/published-apis/v1/rapp-2/service-apis/{reg['serviceId']}")  # wrong producer — silent no-op

    assert calls == []


def test_notify_service_change_does_not_notify_a_subscriber_the_service_is_not_visible_to(client, monkeypatch):
    """notify_service_change's own docstring claims the same authz gate
    discover_services uses, but nothing ever enforced it — an
    unauthorized subscriber would have been notified about a service
    it isn't even allowed to discover. Now enforced.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/capif-events/v1/rapp-3/subscriptions", json={
        "subscriberId": "rapp-3", "eventTypes": ["SERVICE_API_AVAILABLE"], "callbackUri": "http://rapp-3/cb",
    })
    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(allowedConsumers=["rapp-2"]))

    assert calls == []


def test_notify_service_change_survives_unreachable_subscriber(client, monkeypatch):
    import httpx as httpx_module

    def raise_error(url, json=None, timeout=None):
        raise httpx_module.ConnectError("unreachable")

    monkeypatch.setattr("app.main.httpx.post", raise_error)

    client.post("/capif-events/v1/rapp-2/subscriptions", json={
        "subscriberId": "rapp-2", "eventTypes": ["SERVICE_API_AVAILABLE"], "callbackUri": "http://rapp-2/cb",
    })
    resp = client.post("/published-apis/v1/rapp-1/service-apis", json=register_body())  # must not raise
    assert resp.status_code == 201
