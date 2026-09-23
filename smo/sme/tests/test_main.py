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


def test_register_service_stores_and_exposes_aef_profiles(client):
    """OPEN_ITEMS.md section 5: ServiceProfile was flattened — no
    aefProfiles (multiple exposing functions per API), apiSuppFeats, or
    shareableInfo (cross-provider sharing flag).
    """
    aef_profiles = [{
        "aefId": "aef-1", "protocol": "HTTP_2", "dataFormat": "JSON",
        "versions": [{"apiVersion": "v1", "resources": [{"resourceName": "counters", "commType": "REQUEST_RESPONSE"}]}],
    }]
    resp = client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(
        aefProfiles=aef_profiles, apiSuppFeats="1f", shareableInfo={"isShareable": True, "capifProvDoms": ["domain-a"]},
    ))
    service_id = resp.json()["serviceId"]

    view = client.get("/service-apis/v1/allServiceAPIs", params={"api_invoker_id": "anyone"}).json()[0]
    assert view["serviceId"] == service_id
    assert view["aefProfiles"] == aef_profiles
    assert view["apiSuppFeats"] == "1f"
    assert view["shareableInfo"] == {"isShareable": True, "capifProvDoms": ["domain-a"]}


def test_discover_services_filters_by_aef_id(client):
    """OPEN_ITEMS.md section 5: discover_services only filtered on
    api_name/api_version — the reference (discoverservice.go's
    matchesFilter) also filters against each service's AefProfiles.
    """
    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(
        service_name="watched", aefProfiles=[{"aefId": "aef-1", "versions": []}],
    ))
    client.post("/published-apis/v1/rapp-2/service-apis", json=register_body(
        service_name="other", producer="rapp-2", aefProfiles=[{"aefId": "aef-2", "versions": []}],
    ))

    resp = client.get("/service-apis/v1/allServiceAPIs", params={"api_invoker_id": "anyone", "aef_id": "aef-1"})
    names = [s["serviceName"] for s in resp.json()]
    assert names == ["watched"]


def test_discover_services_filters_by_protocol_and_data_format(client):
    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(
        service_name="http-service", aefProfiles=[{"aefId": "aef-1", "protocol": "HTTP_2", "dataFormat": "JSON", "versions": []}],
    ))
    client.post("/published-apis/v1/rapp-2/service-apis", json=register_body(
        service_name="other-service", producer="rapp-2", aefProfiles=[{"aefId": "aef-2", "protocol": "HTTP_1_1", "dataFormat": "XML", "versions": []}],
    ))

    by_protocol = client.get("/service-apis/v1/allServiceAPIs", params={"api_invoker_id": "anyone", "protocol": "HTTP_2"})
    assert [s["serviceName"] for s in by_protocol.json()] == ["http-service"]

    by_data_format = client.get("/service-apis/v1/allServiceAPIs", params={"api_invoker_id": "anyone", "data_format": "XML"})
    assert [s["serviceName"] for s in by_data_format.json()] == ["other-service"]


def test_discover_services_filters_by_comm_type_across_nested_resources(client):
    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(
        service_name="streaming-service", aefProfiles=[{
            "aefId": "aef-1", "versions": [{"apiVersion": "v1", "resources": [{"resourceName": "r1", "commType": "SUBSCRIBE_NOTIFY"}]}],
        }],
    ))
    client.post("/published-apis/v1/rapp-2/service-apis", json=register_body(
        service_name="request-service", producer="rapp-2", aefProfiles=[{
            "aefId": "aef-2", "versions": [{"apiVersion": "v1", "resources": [{"resourceName": "r1", "commType": "REQUEST_RESPONSE"}]}],
        }],
    ))

    resp = client.get("/service-apis/v1/allServiceAPIs", params={"api_invoker_id": "anyone", "comm_type": "SUBSCRIBE_NOTIFY"})
    assert [s["serviceName"] for s in resp.json()] == ["streaming-service"]


def test_discover_services_with_no_aef_filters_returns_services_without_aef_profiles(client):
    """A service registered without any aefProfiles at all (every service
    in this build before this pass) must still be discoverable when no
    aef-level filter is given.
    """
    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body())

    resp = client.get("/service-apis/v1/allServiceAPIs", params={"api_invoker_id": "anyone"})
    assert len(resp.json()) == 1


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


def test_subscription_with_api_ids_filter_only_notifies_for_a_matching_service(client, monkeypatch):
    """OPEN_ITEMS.md section 5: event subscription filtering was
    type-only — the reference's own CAPIFEventFilter also filters by
    apiId (eventservice.go's getMatchingSubs/matchesFilters). A
    subscription scoped to one service's apiId must not be notified
    about a different service's events.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    watched = client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(service_name="watched-service")).json()
    client.post("/capif-events/v1/rapp-2/subscriptions", json={
        "subscriberId": "rapp-2", "eventTypes": ["SERVICE_API_UPDATE"], "callbackUri": "http://rapp-2/cb",
        "apiIds": [watched["serviceId"]],
    })

    client.post("/published-apis/v1/rapp-3/service-apis", json=register_body(service_name="other-service"))  # AVAILABLE, wrong event type anyway
    client.post("/published-apis/v1/rapp-3/service-apis", json=register_body(service_name="other-service", version="2.0"))  # UPDATE, wrong apiId
    assert calls == []

    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(service_name="watched-service", version="2.0"))  # UPDATE, matching apiId
    assert len(calls) == 1
    assert calls[0][1]["serviceId"] == watched["serviceId"]


def test_subscription_without_api_ids_filter_notifies_for_every_matching_service(client, monkeypatch):
    """A subscription with no apiIds filter (the only kind this build had
    before this pass) keeps its existing unscoped behavior.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/capif-events/v1/rapp-2/subscriptions", json={
        "subscriberId": "rapp-2", "eventTypes": ["SERVICE_API_AVAILABLE"], "callbackUri": "http://rapp-2/cb",
    })

    client.post("/published-apis/v1/rapp-1/service-apis", json=register_body(service_name="service-a"))
    client.post("/published-apis/v1/rapp-3/service-apis", json=register_body(service_name="service-b"))

    assert len(calls) == 2


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
