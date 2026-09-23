"""Tests for DME (Foundational Platform LLD sections 3.1-3.5).
Run with: pytest smo/dme/tests -q
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smo_shared.db import Base, get_session

from app.main import app
from app.models import DataJob, DataOffer, DMEDeliverySchema, DMEType


class FakeHealthResponse:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[DMEType.__table__, DMEDeliverySchema.__table__, DataJob.__table__, DataOffer.__table__])
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


def register_type_body(name="PMCounters", version="1.0.0", **extra):
    return {"namespace": "RAN", "name": name, "version": version, "typeName": f"RAN.{name}",
            "producerId": "ran-nf-oam", "dataProductionSchema": {"type": "object"},
            "producerHealthCallbackUrl": "http://ran-nf-oam:8000/health", **extra}


def test_register_dme_type_returns_registration_id(client):
    resp = client.post("/production-capabilities", json=register_type_body())
    assert resp.status_code == 201
    assert "registrationId" in resp.json()


def test_duplicate_namespace_name_version_is_conflict(client):
    client.post("/production-capabilities", json=register_type_body())
    resp = client.post("/production-capabilities", json=register_type_body())
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "DME_TYPE_VERSION_CONFLICT"


def test_different_version_is_not_a_conflict(client):
    client.post("/production-capabilities", json=register_type_body(version="1.0.0"))
    resp = client.post("/production-capabilities", json=register_type_body(version="2.0.0"))
    assert resp.status_code == 201


def test_discover_returns_dme_type_id_struct(client, monkeypatch):
    """Foundational Platform LLD section 3.1: dmeTypeIdStruct is computed
    from our internal UUID, matching R1AP's actual wire identity.
    """
    monkeypatch.setattr("app.main.httpx.get", lambda url, timeout=None: FakeHealthResponse(200))
    client.post("/production-capabilities", json=register_type_body())
    resp = client.get("/dme-types")
    struct = resp.json()[0]["dmeTypeIdStruct"]
    assert struct == {"namespace": "RAN", "name": "PMCounters", "version": "1.0.0"}


def test_type_status_disabled_when_producer_health_callback_is_unreachable(client, monkeypatch):
    """The actual fix: producerHealthCallbackUrl was stored but never
    called at all, and typeStatus used to be derived from whether a
    DataJob row was ACTIVE — a dead producer with an active job still
    reported ENABLED. ICS's own typeStatus is driven entirely by real
    producer availability (ConsumerController.typeStatus /
    ProducerSupervision's health poll); this mirrors that.
    """
    import httpx as httpx_module

    def raise_error(url, timeout=None):
        raise httpx_module.ConnectError("unreachable")

    monkeypatch.setattr("app.main.httpx.get", raise_error)

    client.post("/production-capabilities", json=register_type_body())
    resp = client.get("/dme-types")
    assert resp.json()[0]["typeStatus"] == "DISABLED"


def test_type_status_disabled_on_a_non_2xx_health_response(client, monkeypatch):
    monkeypatch.setattr("app.main.httpx.get", lambda url, timeout=None: FakeHealthResponse(503))
    client.post("/production-capabilities", json=register_type_body())
    resp = client.get("/dme-types")
    assert resp.json()[0]["typeStatus"] == "DISABLED"


def test_type_status_enabled_when_producer_health_callback_responds(client, monkeypatch):
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        return FakeHealthResponse(200)

    monkeypatch.setattr("app.main.httpx.get", fake_get)

    client.post("/production-capabilities", json=register_type_body())
    resp = client.get("/dme-types")
    assert resp.json()[0]["typeStatus"] == "ENABLED"
    assert calls == ["http://ran-nf-oam:8000/health"]  # the registered producerHealthCallbackUrl, genuinely called


def test_type_status_stays_disabled_even_with_an_active_job_if_producer_is_unreachable(client, monkeypatch):
    """The headline regression this fix closes: an ACTIVE DataJob must no
    longer be enough on its own to report ENABLED.
    """
    import httpx as httpx_module

    def raise_error(url, timeout=None):
        raise httpx_module.ConnectError("unreachable")

    monkeypatch.setattr("app.main.httpx.get", raise_error)

    reg = client.post("/production-capabilities", json=register_type_body()).json()
    client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
    })
    resp = client.get("/dme-types")
    assert resp.json()[0]["typeStatus"] == "DISABLED"


def test_data_job_rejects_unknown_delivery_method(client):
    reg = client.post("/production-capabilities", json=register_type_body()).json()
    resp = client.post("/data-jobs", json={
        "dataDeliveryMode": "ONE_TIME", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "CARRIER_PIGEON", "consumerId": "rapp-1",
    })
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "DELIVERY_METHOD_NOT_OFFERED"


def test_data_job_accepts_wire_exact_delivery_values(client):
    """Section 3.2: enum renamed to match R1AP's exact wire values."""
    reg = client.post("/production-capabilities", json=register_type_body()).json()
    for method in ("PULL_HTTP", "PUSH_HTTP", "STREAMING_KAFKA"):
        resp = client.post("/data-jobs", json={
            "dataDeliveryMode": "ONE_TIME", "dmeTypeId": reg["registrationId"],
            "dataDeliveryMethod": method, "consumerId": "rapp-1",
        })
        assert resp.status_code == 202, method


def test_data_offer_commits_to_first_offered_method(client):
    """Section 3.5: producer offers multiple methods, framework commits to one."""
    reg = client.post("/production-capabilities", json=register_type_body()).json()
    resp = client.post("/offers", json={
        "dmeTypeId": reg["registrationId"], "dataDeliveryMode": "CONTINUOUS",
        "dataDeliveryMethods": ["PUSH_HTTP", "STREAMING_KAFKA"],
        "dataOfferTerminationNotificationUri": "http://producer/terminate",
    })
    assert resp.status_code == 201
    assert resp.json()["committedMethod"] == "PUSH_HTTP"


def test_data_job_rejects_a_method_the_offer_never_committed_to(client):
    """The actual fix: CreateDataJob previously only checked the requested
    method against the global wire-value set, never against what the
    specific DataOffer for this dmeTypeId actually committed to (section
    3.5's own "framework commits to one" decision) — a consumer could
    request STREAMING_KAFKA against a type whose offer only committed to
    PULL_HTTP, and DME accepted it without complaint.
    """
    reg = client.post("/production-capabilities", json=register_type_body()).json()
    client.post("/offers", json={
        "dmeTypeId": reg["registrationId"], "dataDeliveryMode": "CONTINUOUS",
        "dataDeliveryMethods": ["PULL_HTTP"],
        "dataOfferTerminationNotificationUri": "http://producer/terminate",
    })

    resp = client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "STREAMING_KAFKA", "consumerId": "rapp-1",
    })
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "DELIVERY_METHOD_NOT_OFFERED"


def test_data_job_accepts_the_offers_committed_method(client):
    reg = client.post("/production-capabilities", json=register_type_body()).json()
    client.post("/offers", json={
        "dmeTypeId": reg["registrationId"], "dataDeliveryMode": "CONTINUOUS",
        "dataDeliveryMethods": ["PULL_HTTP"],
        "dataOfferTerminationNotificationUri": "http://producer/terminate",
    })

    resp = client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
    })
    assert resp.status_code == 202


def test_data_job_unaffected_by_offer_check_when_no_offer_exists(client):
    """Not every DmeType in this build has a DataOffer — the check must
    not regress the existing PULL-only-no-offer case.
    """
    reg = client.post("/production-capabilities", json=register_type_body()).json()
    resp = client.post("/data-jobs", json={
        "dataDeliveryMode": "ONE_TIME", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
    })
    assert resp.status_code == 202


def test_deregister_producer_removes_all_its_types(client, monkeypatch):
    """The DME half of rApp Management's producer-reconsideration trigger
    (OPEN_ITEMS.md section 1) — deregistering a producer must remove
    every DMEType it registered, not just one.
    """
    monkeypatch.setattr("app.main.httpx.get", lambda url, timeout=None: FakeHealthResponse(200))
    client.post("/production-capabilities", json=register_type_body(name="TypeA", producerId="rapp-1"))
    client.post("/production-capabilities", json=register_type_body(name="TypeB", producerId="rapp-1"))
    client.post("/production-capabilities", json=register_type_body(name="TypeC", producerId="rapp-2"))

    resp = client.delete("/production-capabilities", params={"producer_id": "rapp-1"})
    assert resp.status_code == 204

    remaining = client.get("/dme-types").json()
    assert {t["producerId"] for t in remaining} == {"rapp-2"}


def test_deregister_unknown_producer_is_idempotent(client):
    resp = client.delete("/production-capabilities", params={"producer_id": "never-registered"})
    assert resp.status_code == 204


def test_terminate_data_offer_fires_termination_notification(client, monkeypatch):
    """Section 3.5: normal-direction notification on termination —
    framework -> Producer, distinct from the reversed availability
    notification the Producer sends the other way.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    reg = client.post("/production-capabilities", json=register_type_body()).json()
    offer = client.post("/offers", json={
        "dmeTypeId": reg["registrationId"], "dataDeliveryMode": "CONTINUOUS",
        "dataDeliveryMethods": ["PUSH_HTTP"],
        "dataOfferTerminationNotificationUri": "http://producer/terminate",
    }).json()

    client.delete(f"/offers/{offer['offerId']}")
    assert len(calls) == 1
    assert calls[0][0] == "http://producer/terminate"


def test_get_data_job_by_id_returns_its_fields(client):
    """OPEN_ITEMS.md section 5: no GET-by-id for DataJob existed at all."""
    reg = client.post("/production-capabilities", json=register_type_body()).json()
    created = client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
    }).json()

    resp = client.get(f"/data-jobs/{created['dataJobId']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dataJobId"] == created["dataJobId"]
    assert body["dmeTypeId"] == reg["registrationId"]
    assert body["dataDeliveryMethod"] == "PULL_HTTP"
    assert body["consumerId"] == "rapp-1"
    assert body["status"] == "ACTIVE"


def test_get_unknown_data_job_is_404(client):
    resp = client.get("/data-jobs/11111111-1111-1111-1111-111111111111")
    assert resp.status_code == 404


def test_query_data_job_status_returns_status(client):
    """No job-level status endpoint existed — only the list/GET-by-id
    view, which this build didn't even have until this pass.
    """
    reg = client.post("/production-capabilities", json=register_type_body()).json()
    created = client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
    }).json()

    resp = client.get(f"/data-jobs/{created['dataJobId']}/status")
    assert resp.status_code == 200
    assert resp.json() == {"dataJobId": created["dataJobId"], "status": "ACTIVE"}


def test_query_unknown_data_job_status_is_404(client):
    resp = client.get("/data-jobs/11111111-1111-1111-1111-111111111111/status")
    assert resp.status_code == 404


def test_get_data_offer_by_id_returns_its_fields(client):
    """OPEN_ITEMS.md section 5: no GET-by-id for DataOffer existed at all."""
    reg = client.post("/production-capabilities", json=register_type_body()).json()
    created = client.post("/offers", json={
        "dmeTypeId": reg["registrationId"], "dataDeliveryMode": "CONTINUOUS",
        "dataDeliveryMethods": ["PUSH_HTTP", "STREAMING_KAFKA"],
        "dataOfferTerminationNotificationUri": "http://producer/terminate",
    }).json()

    resp = client.get(f"/offers/{created['offerId']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["offerId"] == created["offerId"]
    assert body["dmeTypeId"] == reg["registrationId"]
    assert body["committedMethod"] == "PUSH_HTTP"
    assert body["dataDeliveryMethodsOffered"] == ["PUSH_HTTP", "STREAMING_KAFKA"]


def test_get_unknown_data_offer_is_404(client):
    resp = client.get("/offers/11111111-1111-1111-1111-111111111111")
    assert resp.status_code == 404


def test_discover_filters_by_data_category(client, monkeypatch):
    """OPEN_ITEMS.md section 5: data_category was declared as a query
    param but silently never applied — every call returned every type
    regardless of the filter.
    """
    monkeypatch.setattr("app.main.httpx.get", lambda url, timeout=None: FakeHealthResponse(200))
    client.post("/production-capabilities", json=register_type_body(name="CoverageIssue"))
    client.post("/production-capabilities", json={
        "namespace": "AIML", "name": "ModelHealth", "version": "1.0.0", "typeName": "AIML.ModelHealth",
        "producerId": "ai-ml-workflow", "dataProductionSchema": {"type": "object"},
        "producerHealthCallbackUrl": "http://ai-ml-workflow:8000/health",
    })

    resp = client.get("/dme-types", params={"data_category": "AIML"})
    names = [t["typeName"] for t in resp.json()]
    assert names == ["AIML.ModelHealth"]


def test_discover_without_data_category_returns_every_type(client, monkeypatch):
    monkeypatch.setattr("app.main.httpx.get", lambda url, timeout=None: FakeHealthResponse(200))
    client.post("/production-capabilities", json=register_type_body(name="CoverageIssue"))
    client.post("/production-capabilities", json={
        "namespace": "AIML", "name": "ModelHealth", "version": "1.0.0", "typeName": "AIML.ModelHealth",
        "producerId": "ai-ml-workflow", "dataProductionSchema": {"type": "object"},
        "producerHealthCallbackUrl": "http://ai-ml-workflow:8000/health",
    })

    resp = client.get("/dme-types")
    assert len(resp.json()) == 2
