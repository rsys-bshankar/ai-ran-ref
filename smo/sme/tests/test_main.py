"""Tests for SME (Foundational Platform LLD sections 2.1-2.3).
Run with: pytest smo/sme/tests -q
"""

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smo_shared.db import Base, get_session

from app.main import app
from app.models import InvokerRegistration, IssuedAccessToken, ProviderRegistration, ServiceAuthzPolicy, ServiceEventSubscription, ServiceProfile, TrustedInvoker

# Every test in this file registers services under one of these three
# identities — pre-enrolling them here (via the real POST
# /provider-registrations route, not a data shortcut) keeps every existing
# register_service call site unchanged now that it requires a registered
# publishing function (OPEN_ITEMS.md section 5), the same way a test suite
# logs in a fixture user once rather than re-testing login in every test.
KNOWN_TEST_PUBLISHERS = ["rapp-1", "rapp-2", "rapp-3"]


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        ServiceProfile.__table__, ServiceAuthzPolicy.__table__, ServiceEventSubscription.__table__, ProviderRegistration.__table__,
        InvokerRegistration.__table__, IssuedAccessToken.__table__, TrustedInvoker.__table__,
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
    test_client = TestClient(app)
    for apf_id in KNOWN_TEST_PUBLISHERS:
        test_client.post("/provider-registrations", json={"apfId": apf_id})
    yield test_client
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


def test_register_service_without_enrollment_is_forbidden(client):
    """OPEN_ITEMS.md section 5: register_service used to accept any apf_id
    with no check that it's an actual registered publisher. The
    reference's own gate (PostApfIdServiceApis: 403, "api is only
    available for publishers") is now real.
    """
    resp = client.post("/published-apis/v1/rapp-unregistered/service-apis", json=register_body(producer="rapp-unregistered"))
    assert resp.status_code == 403
    assert resp.json()["detail"]["title"] == "APF_NOT_REGISTERED"


def test_register_provider_then_register_service_succeeds(client):
    resp = client.post("/provider-registrations", json={"apfId": "rapp-99", "providerDomainInfo": "test rApp"})
    assert resp.status_code == 201
    assert resp.json() == {"apfId": "rapp-99"}

    resp = client.post("/published-apis/v1/rapp-99/service-apis", json=register_body(producer="rapp-99", service_name="rapp-99-service"))
    assert resp.status_code == 201


def test_register_provider_is_idempotent_update_in_place(client):
    client.post("/provider-registrations", json={"apfId": "rapp-99", "providerDomainInfo": "first"})
    resp = client.post("/provider-registrations", json={"apfId": "rapp-99", "providerDomainInfo": "second"})
    assert resp.status_code == 201
    # still registered, still usable — a re-registration is an update, not a conflict
    resp = client.post("/published-apis/v1/rapp-99/service-apis", json=register_body(producer="rapp-99", service_name="rapp-99-service"))
    assert resp.status_code == 201


def test_deregister_provider_removes_it(client):
    client.post("/provider-registrations", json={"apfId": "rapp-99"})
    resp = client.delete("/provider-registrations/rapp-99")
    assert resp.status_code == 204

    resp = client.post("/published-apis/v1/rapp-99/service-apis", json=register_body(producer="rapp-99", service_name="rapp-99-service"))
    assert resp.status_code == 403


def test_deregister_unknown_provider_is_idempotent(client):
    resp = client.delete("/provider-registrations/does-not-exist")
    assert resp.status_code == 204


def test_query_own_services_without_enrollment_is_404(client):
    """GetApfIdServiceApis, publishservice.go: an apf_id with zero services
    and no enrolment is genuinely "not a publisher", not just "no
    services yet".
    """
    resp = client.get("/published-apis/v1/rapp-unregistered/service-apis")
    assert resp.status_code == 404


def test_query_own_services_with_enrollment_and_no_services_is_empty_list(client):
    client.post("/provider-registrations", json={"apfId": "rapp-99"})
    resp = client.get("/published-apis/v1/rapp-99/service-apis")
    assert resp.status_code == 200
    assert resp.json() == []


def test_query_own_services_returns_existing_services_even_after_deregistration(client):
    """publishservice.go's own GetApfIdServiceApis checks the published-
    services map FIRST, falling back to the enrolment gate only when it's
    empty — an apf_id with existing services always gets them back,
    regardless of its current enrolment state.
    """
    client.post("/provider-registrations", json={"apfId": "rapp-99"})
    client.post("/published-apis/v1/rapp-99/service-apis", json=register_body(producer="rapp-99", service_name="rapp-99-service"))
    client.delete("/provider-registrations/rapp-99")

    resp = client.get("/published-apis/v1/rapp-99/service-apis")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def _register_invoker(client, public_key="pk-1"):
    """SPEC_AUDIT.md SME item 1: the real CAPIF onboarding flow is
    public-key-based — the client supplies apiInvokerPublicKey; the
    server generates and returns both apiInvokerId and
    onboardingSecret. Every test that needs a registered invoker now
    goes through this real round trip rather than asserting a
    client-chosen id/secret straight into existence.
    """
    resp = client.post("/invoker-registrations", json={"apiInvokerPublicKey": public_key})
    assert resp.status_code == 201
    return resp.json()


def test_register_invoker_generates_id_and_secret_server_side(client):
    """SPEC_AUDIT.md SME item 1: apiInvokerId/onboardingSecret were
    previously client-supplied (a self-asserted identity, a
    client-chosen secret) — the weaker trust direction the real CAPIF
    core's own schema explicitly forbids ("apiInvokerId shall not be
    present" in the client's request). Both are now server-generated.
    """
    body = _register_invoker(client, public_key="pk-1")
    assert body["apiInvokerId"].startswith("api-invoker-")
    assert body["onboardingSecret"]


def test_register_invoker_generates_a_distinct_id_and_secret_each_call(client):
    first = _register_invoker(client, public_key="pk-1")
    second = _register_invoker(client, public_key="pk-1")  # same public key, still a genuinely new onboarding
    assert first["apiInvokerId"] != second["apiInvokerId"]
    assert first["onboardingSecret"] != second["onboardingSecret"]


def test_register_invoker_persists_the_submitted_public_key(client, db_session_factory):
    body = _register_invoker(client, public_key="the-real-public-key")
    with db_session_factory() as session:
        stored = session.get(InvokerRegistration, body["apiInvokerId"])
    assert stored.public_key == "the-real-public-key"


def test_onboarding_secret_is_never_stored_in_cleartext(client, db_session_factory):
    """Security review finding on PR #54: a DB leak (backup, SQL
    injection elsewhere, a dump) must never hand out a reusable client
    credential directly.
    """
    body = _register_invoker(client)
    with db_session_factory() as session:
        stored = session.get(InvokerRegistration, body["apiInvokerId"]).onboarding_secret_hash
    assert body["onboardingSecret"] not in stored
    assert ":" in stored  # salt_hex:digest_hex


def test_access_token_is_never_stored_in_cleartext(client, db_session_factory):
    """Same finding: the raw bearer token is returned to the caller once
    and must never be recoverable from a DB leak either.
    """
    inv = _register_invoker(client)
    token = client.post("/oauth2/token", json={
        "grant_type": "client_credentials", "client_id": inv["apiInvokerId"], "client_secret": inv["onboardingSecret"],
    }).json()["access_token"]
    with db_session_factory() as session:
        stored_hashes = [row.access_token_hash for row in session.query(IssuedAccessToken).all()]
    assert token not in stored_hashes
    assert len(stored_hashes) == 1
    assert len(stored_hashes[0]) == 64  # hex-encoded SHA-256


def test_issue_token_succeeds_for_a_registered_invoker(client):
    inv = _register_invoker(client)
    resp = client.post("/oauth2/token", json={
        "grant_type": "client_credentials", "client_id": inv["apiInvokerId"], "client_secret": inv["onboardingSecret"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["access_token"]


def test_issue_token_rejects_unregistered_invoker(client):
    resp = client.post("/oauth2/token", json={"grant_type": "client_credentials", "client_id": "does-not-exist", "client_secret": "whatever"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client"


def test_issue_token_rejects_wrong_secret(client):
    inv = _register_invoker(client)
    resp = client.post("/oauth2/token", json={"grant_type": "client_credentials", "client_id": inv["apiInvokerId"], "client_secret": "wrong"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "unauthorized_client"


def test_issue_token_rejects_unsupported_grant_type(client):
    inv = _register_invoker(client)
    resp = client.post("/oauth2/token", json={
        "grant_type": "authorization_code", "client_id": inv["apiInvokerId"], "client_secret": inv["onboardingSecret"],
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_grant_type"


def test_introspect_active_token_reports_active_with_client_id(client):
    inv = _register_invoker(client)
    token = client.post("/oauth2/token", json={
        "grant_type": "client_credentials", "client_id": inv["apiInvokerId"], "client_secret": inv["onboardingSecret"],
    }).json()["access_token"]

    resp = client.post("/oauth2/introspect", json={"token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    assert body["client_id"] == inv["apiInvokerId"]


def test_introspect_unknown_token_is_inactive(client):
    resp = client.post("/oauth2/introspect", json={"token": "not-a-real-token"})
    assert resp.status_code == 200
    assert resp.json() == {"active": False}


def test_introspect_expired_token_is_inactive(client, db_session_factory):
    inv = _register_invoker(client)
    token = client.post("/oauth2/token", json={
        "grant_type": "client_credentials", "client_id": inv["apiInvokerId"], "client_secret": inv["onboardingSecret"],
    }).json()["access_token"]

    from app.main import _hash_token

    with db_session_factory() as session:
        rec = session.get(IssuedAccessToken, _hash_token(token))
        rec.expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
        session.commit()

    resp = client.post("/oauth2/introspect", json={"token": token})
    assert resp.json() == {"active": False}


def _register_trusted_invoker(client, api_invoker_id, aef_id="aef-1", api_id="api-1", pref_methods=None):
    return client.put(f"/trusted-invokers/{api_invoker_id}", json={
        "notificationDestination": "http://consumer/security-notify",
        "securityInfo": [{"aefId": aef_id, "apiId": api_id, "authenticationInfo": "auth-info", "authorizationInfo": "authz-info",
                           "prefSecurityMethods": pref_methods or ["OAUTH"]}],
    })


def test_register_trusted_invoker_rejects_unregistered_invoker(client):
    resp = _register_trusted_invoker(client, "does-not-exist")
    assert resp.status_code == 400
    assert resp.json()["detail"]["title"] == "INVOKER_NOT_REGISTERED"


def test_register_trusted_invoker_succeeds_for_registered_invoker(client):
    inv = _register_invoker(client)
    resp = _register_trusted_invoker(client, inv["apiInvokerId"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["apiInvokerId"] == inv["apiInvokerId"]
    assert body["notificationDestination"] == "http://consumer/security-notify"
    assert body["securityInfo"][0]["selSecurityMethod"] == "OAUTH"


def test_register_trusted_invoker_rejects_missing_notification_destination(client):
    inv = _register_invoker(client)
    resp = client.put(f"/trusted-invokers/{inv['apiInvokerId']}", json={
        "notificationDestination": "", "securityInfo": [{"prefSecurityMethods": ["OAUTH"]}],
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "SECURITY_CONTEXT_INVALID"


def test_register_trusted_invoker_rejects_empty_security_info(client):
    inv = _register_invoker(client)
    resp = client.put(f"/trusted-invokers/{inv['apiInvokerId']}", json={"notificationDestination": "http://consumer/notify", "securityInfo": []})
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "SECURITY_CONTEXT_INVALID"


def test_register_trusted_invoker_rejects_missing_pref_security_methods(client):
    inv = _register_invoker(client)
    resp = client.put(f"/trusted-invokers/{inv['apiInvokerId']}", json={
        "notificationDestination": "http://consumer/notify", "securityInfo": [{"aefId": "aef-1", "prefSecurityMethods": []}],
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "SECURITY_CONTEXT_INVALID"


def test_register_trusted_invoker_is_idempotent_replace_on_re_put(client):
    inv = _register_invoker(client)
    _register_trusted_invoker(client, inv["apiInvokerId"], pref_methods=["OAUTH"])
    resp = _register_trusted_invoker(client, inv["apiInvokerId"], pref_methods=["PSK"])
    assert resp.status_code == 201
    assert resp.json()["securityInfo"][0]["selSecurityMethod"] == "PSK"
    assert len(resp.json()["securityInfo"]) == 1


def test_get_trusted_invoker_returns_404_for_unregistered(client):
    resp = client.get("/trusted-invokers/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["detail"]["title"] == "TRUSTED_INVOKER_NOT_FOUND"


def test_get_trusted_invoker_redacts_authentication_and_authorization_info_by_default(client):
    inv = _register_invoker(client)
    _register_trusted_invoker(client, inv["apiInvokerId"])
    resp = client.get(f"/trusted-invokers/{inv['apiInvokerId']}")
    assert resp.status_code == 200
    info = resp.json()["securityInfo"][0]
    assert info["authenticationInfo"] == ""
    assert info["authorizationInfo"] == ""


def test_get_trusted_invoker_reveals_authentication_and_authorization_info_when_requested(client):
    inv = _register_invoker(client)
    _register_trusted_invoker(client, inv["apiInvokerId"])
    resp = client.get(f"/trusted-invokers/{inv['apiInvokerId']}", params={"authentication_info": True, "authorization_info": True})
    info = resp.json()["securityInfo"][0]
    assert info["authenticationInfo"] == "auth-info"
    assert info["authorizationInfo"] == "authz-info"


def test_deregister_trusted_invoker_removes_it(client, db_session_factory):
    inv = _register_invoker(client)
    _register_trusted_invoker(client, inv["apiInvokerId"])
    resp = client.delete(f"/trusted-invokers/{inv['apiInvokerId']}")
    assert resp.status_code == 204
    with db_session_factory() as session:
        assert session.get(TrustedInvoker, inv["apiInvokerId"]) is None


def test_deregister_unknown_trusted_invoker_is_idempotent(client):
    resp = client.delete("/trusted-invokers/does-not-exist")
    assert resp.status_code == 204


def test_update_trusted_invoker_requires_existing_context(client):
    inv = _register_invoker(client)
    resp = client.post(f"/trusted-invokers/{inv['apiInvokerId']}/update", json={
        "notificationDestination": "http://consumer/notify", "securityInfo": [{"prefSecurityMethods": ["OAUTH"]}],
    })
    assert resp.status_code == 404
    assert resp.json()["detail"]["title"] == "TRUSTED_INVOKER_NOT_FOUND"


def test_update_trusted_invoker_does_not_re_check_invoker_registration(client, db_session_factory):
    """The reference's own PostTrustedInvokersApiInvokerIdUpdate never
    re-checks invoker registration, only that a trusted-invoker context
    already exists — mirrored here.
    """
    inv = _register_invoker(client)
    _register_trusted_invoker(client, inv["apiInvokerId"])
    with db_session_factory() as session:
        session.query(InvokerRegistration).filter_by(api_invoker_id=inv["apiInvokerId"]).delete()
        session.commit()

    resp = client.post(f"/trusted-invokers/{inv['apiInvokerId']}/update", json={
        "notificationDestination": "http://consumer/notify-v2", "securityInfo": [{"prefSecurityMethods": ["PSK"]}],
    })
    assert resp.status_code == 200
    assert resp.json()["notificationDestination"] == "http://consumer/notify-v2"


def test_revoke_trusted_invoker_removes_matching_entry_by_aef_id(client):
    inv = _register_invoker(client)
    client.put(f"/trusted-invokers/{inv['apiInvokerId']}", json={
        "notificationDestination": "http://consumer/notify",
        "securityInfo": [
            {"aefId": "aef-1", "apiId": "api-1", "prefSecurityMethods": ["OAUTH"]},
            {"aefId": "aef-2", "apiId": "api-2", "prefSecurityMethods": ["OAUTH"]},
        ],
    })
    resp = client.post(f"/trusted-invokers/{inv['apiInvokerId']}/delete", json={
        "aefId": "aef-1", "apiIds": ["nonexistent"], "apiInvokerId": inv["apiInvokerId"], "cause": "UNEXPECTED_REASON",
    })
    assert resp.status_code == 204

    remaining = client.get(f"/trusted-invokers/{inv['apiInvokerId']}")
    assert len(remaining.json()["securityInfo"]) == 1
    assert remaining.json()["securityInfo"][0]["aefId"] == "aef-2"


def test_revoke_trusted_invoker_deletes_whole_record_when_no_entries_remain(client, db_session_factory):
    inv = _register_invoker(client)
    _register_trusted_invoker(client, inv["apiInvokerId"], aef_id="aef-1", api_id="api-1")
    resp = client.post(f"/trusted-invokers/{inv['apiInvokerId']}/delete", json={
        "aefId": "aef-1", "apiIds": ["api-1"], "apiInvokerId": inv["apiInvokerId"], "cause": "OVERLIMIT_USAGE",
    })
    assert resp.status_code == 204
    with db_session_factory() as session:
        assert session.get(TrustedInvoker, inv["apiInvokerId"]) is None


def test_revoke_trusted_invoker_returns_404_for_unregistered(client):
    resp = client.post("/trusted-invokers/does-not-exist/delete", json={
        "apiIds": ["api-1"], "apiInvokerId": "does-not-exist", "cause": "OVERLIMIT_USAGE",
    })
    assert resp.status_code == 404
    assert resp.json()["detail"]["title"] == "TRUSTED_INVOKER_NOT_FOUND"


def test_revoke_trusted_invoker_rejects_empty_api_ids(client):
    inv = _register_invoker(client)
    _register_trusted_invoker(client, inv["apiInvokerId"])
    resp = client.post(f"/trusted-invokers/{inv['apiInvokerId']}/delete", json={
        "apiIds": [], "apiInvokerId": inv["apiInvokerId"], "cause": "OVERLIMIT_USAGE",
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "SECURITY_CONTEXT_INVALID"


def test_health_check_answers_the_gui_bff_liveness_probe(client):
    """GUI pass: the BFF's /modules/status probes /<module>/health on every module."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}
