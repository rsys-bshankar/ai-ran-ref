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


def test_discover_returns_dme_type_id_struct(client):
    """Foundational Platform LLD section 3.1: dmeTypeIdStruct is computed
    from our internal UUID, matching R1AP's actual wire identity.
    """
    client.post("/production-capabilities", json=register_type_body())
    resp = client.get("/dme-types")
    struct = resp.json()[0]["dmeTypeIdStruct"]
    assert struct == {"namespace": "RAN", "name": "PMCounters", "version": "1.0.0"}


def test_type_status_disabled_with_no_active_jobs(client):
    """ADOPT from ICS (section 3.4) — computed ENABLED/DISABLED field."""
    client.post("/production-capabilities", json=register_type_body())
    resp = client.get("/dme-types")
    assert resp.json()[0]["typeStatus"] == "DISABLED"


def test_type_status_enabled_once_a_data_job_is_active(client):
    reg = client.post("/production-capabilities", json=register_type_body()).json()
    client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
    })
    resp = client.get("/dme-types")
    assert resp.json()[0]["typeStatus"] == "ENABLED"


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
