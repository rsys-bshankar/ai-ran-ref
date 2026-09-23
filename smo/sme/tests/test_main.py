"""Tests for SME (Foundational Platform LLD sections 2.1-2.3).
Run with: pytest smo/sme/tests -q
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smo_shared.db import Base, get_session

from app.main import app
from app.models import ServiceAuthzPolicy, ServiceEventSubscription, ServiceProfile


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[ServiceProfile.__table__, ServiceAuthzPolicy.__table__, ServiceEventSubscription.__table__])
    TestSession = sessionmaker(bind=engine)

    def override_get_session():
        session = TestSession()
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
