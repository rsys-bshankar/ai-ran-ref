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
from app.models import DataJob, DataOffer, DMEDeliverySchema, DMEType, DMETypeSubscription


class FakeHealthResponse:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[DMEType.__table__, DMEDeliverySchema.__table__, DataJob.__table__, DataOffer.__table__, DMETypeSubscription.__table__])
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


@pytest.fixture(autouse=True)
def _default_producer_callbacks_are_harmless(monkeypatch):
    """Every test registers producers at fake hostnames
    (http://ran-nf-oam:8000/...) that only resolve inside the real
    docker-compose network — health checks and job push/stop are all
    best-effort by design (OPEN_ITEMS.md section 5), so defaulting them
    to a harmless, deterministic response keeps every test that doesn't
    care about this behavior fast and stable. Tests that actually
    exercise health/push/stop behavior override this with their own
    monkeypatch.setattr call.
    """
    monkeypatch.setattr("app.main.httpx.get", lambda url, timeout=None: FakeHealthResponse(200))
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: FakeHealthResponse(200))
    monkeypatch.setattr("app.main.httpx.delete", lambda url, timeout=None: FakeHealthResponse(204))


def register_type_body(name="PMCounters", version="1.0.0", **extra):
    return {"namespace": "RAN", "name": name, "version": version, "typeName": f"RAN.{name}",
            "producerId": "ran-nf-oam", "dataProductionSchema": {"type": "object"},
            "producerHealthCallbackUrl": "http://ran-nf-oam:8000/health",
            "jobCallbackUrl": "http://ran-nf-oam:8000/dme-jobs", **extra}


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


def test_data_job_rejects_a_definition_violating_the_registered_schema(client):
    """OPEN_ITEMS.md section 5: ICS's own InfoJobs.validateJsonObjectAgainstSchema
    (validatePutInfoJob) — productionJobDefinition used to be accepted as an
    arbitrary dict, never checked against the DmeType's own
    dataProductionSchema.
    """
    reg = client.post("/production-capabilities", json=register_type_body(
        dataProductionSchema={"type": "object", "properties": {"cellId": {"type": "string"}}, "required": ["cellId"]},
    )).json()

    resp = client.post("/data-jobs", json={
        "dataDeliveryMode": "ONE_TIME", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
        "productionJobDefinition": {"cellId": 42},  # wrong type: schema requires a string
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "SCHEMA_VALIDATION_FAILED"


def test_data_job_rejects_a_definition_missing_a_required_field(client):
    reg = client.post("/production-capabilities", json=register_type_body(
        dataProductionSchema={"type": "object", "properties": {"cellId": {"type": "string"}}, "required": ["cellId"]},
    )).json()

    resp = client.post("/data-jobs", json={
        "dataDeliveryMode": "ONE_TIME", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
        "productionJobDefinition": {},
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "SCHEMA_VALIDATION_FAILED"


def test_data_job_accepts_a_definition_matching_the_registered_schema(client):
    reg = client.post("/production-capabilities", json=register_type_body(
        dataProductionSchema={"type": "object", "properties": {"cellId": {"type": "string"}}, "required": ["cellId"]},
    )).json()

    resp = client.post("/data-jobs", json={
        "dataDeliveryMode": "ONE_TIME", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
        "productionJobDefinition": {"cellId": "cell-42"},
    })
    assert resp.status_code == 202


def test_data_job_unaffected_by_schema_check_for_an_unknown_dme_type(client):
    """Not every dmeTypeId in a POST is guaranteed to resolve to a real
    DmeType (nothing else in create_data_job checks that either) — the
    schema check must not regress that existing permissive behavior.
    """
    resp = client.post("/data-jobs", json={
        "dataDeliveryMode": "ONE_TIME", "dmeTypeId": "11111111-1111-1111-1111-111111111111",
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
        "productionJobDefinition": {"anything": "goes"},
    })
    assert resp.status_code == 202


def test_update_data_job_rejects_a_definition_violating_the_registered_schema(client):
    reg = client.post("/production-capabilities", json=register_type_body(
        dataProductionSchema={"type": "object", "properties": {"cellId": {"type": "string"}}, "required": ["cellId"]},
    )).json()
    created = client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1", "productionJobDefinition": {"cellId": "cell-1"},
    }).json()

    resp = client.put(f"/data-jobs/{created['dataJobId']}", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1", "productionJobDefinition": {},
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "SCHEMA_VALIDATION_FAILED"


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


def test_deregister_producer_removes_all_its_types(client):
    """The DME half of rApp Management's producer-reconsideration trigger
    (OPEN_ITEMS.md section 1) — deregistering a producer must remove
    every DMEType it registered, not just one.
    """
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


def test_deregister_producer_removes_dependent_data_jobs_and_offers(client):
    """OPEN_ITEMS.md section 5: deregistering a producer used to delete
    its DMEType rows unconditionally, leaving any DataJob/DataOffer
    still referencing that type either orphaned (SQLite, no FK
    enforcement) or crashing with an unhandled IntegrityError (real
    Postgres — neither FK had an ON DELETE CASCADE, unlike
    dme_delivery_schema's own already-cascading one). A job/offer for a
    type nobody produces anymore is meaningless once the producer is
    gone, so both are cleaned up now.
    """
    reg = client.post("/production-capabilities", json=register_type_body(producerId="rapp-1")).json()
    job = client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
    }).json()
    offer = client.post("/offers", json={
        "dmeTypeId": reg["registrationId"], "dataDeliveryMode": "CONTINUOUS",
        "dataDeliveryMethods": ["PULL_HTTP"],
        "dataOfferTerminationNotificationUri": "http://producer/terminate",
    }).json()

    resp = client.delete("/production-capabilities", params={"producer_id": "rapp-1"})
    assert resp.status_code == 204

    assert client.get(f"/data-jobs/{job['dataJobId']}").status_code == 404
    assert client.get(f"/offers/{offer['offerId']}").status_code == 404


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


def test_update_data_job_changes_its_definition(client):
    """OPEN_ITEMS.md section 5: DME had no update-in-place semantics at
    all — only POST-create/DELETE. ICS's own PutIndividualInfoJob.
    """
    reg = client.post("/production-capabilities", json=register_type_body()).json()
    created = client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1", "productionJobDefinition": {"v": 1},
    }).json()

    resp = client.put(f"/data-jobs/{created['dataJobId']}", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1", "productionJobDefinition": {"v": 2},
    })
    assert resp.status_code == 200
    assert resp.json()["productionJobDefinition"] == {"v": 2}

    view = client.get(f"/data-jobs/{created['dataJobId']}").json()
    assert view["productionJobDefinition"] == {"v": 2}


def test_update_data_job_rejects_changing_its_target(client):
    """ICS itself rejects changing a job's type mid-update ("Cannot
    modify job type", 409 there) — the equivalent identity fields here
    are dmeTypeId/consumerId/dataDeliveryMode, all fixed at creation.
    """
    reg = client.post("/production-capabilities", json=register_type_body()).json()
    created = client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
    }).json()

    resp = client.put(f"/data-jobs/{created['dataJobId']}", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-2",
    })
    assert resp.status_code == 400
    assert resp.json()["detail"]["title"] == "DATA_JOB_TARGET_IMMUTABLE"


def test_update_data_job_revalidates_delivery_method_against_the_offer(client):
    reg = client.post("/production-capabilities", json=register_type_body()).json()
    client.post("/offers", json={
        "dmeTypeId": reg["registrationId"], "dataDeliveryMode": "CONTINUOUS",
        "dataDeliveryMethods": ["PULL_HTTP"],
        "dataOfferTerminationNotificationUri": "http://producer/terminate",
    })
    created = client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
    }).json()

    resp = client.put(f"/data-jobs/{created['dataJobId']}", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "STREAMING_KAFKA", "consumerId": "rapp-1",
    })
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "DELIVERY_METHOD_NOT_OFFERED"


def test_update_unknown_data_job_is_404(client):
    resp = client.put("/data-jobs/11111111-1111-1111-1111-111111111111", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": "22222222-2222-2222-2222-222222222222",
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
    })
    assert resp.status_code == 404


def test_update_data_job_re_pushes_to_the_producer(client, monkeypatch):
    """ICS re-runs startInfoSubscriptionJob on every PUT, new or
    updated — the producer is re-notified with the new job definition.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    reg = client.post("/production-capabilities", json=register_type_body()).json()
    created = client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
    }).json()
    calls.clear()  # drop the push from create_data_job itself

    client.put(f"/data-jobs/{created['dataJobId']}", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1", "productionJobDefinition": {"v": 2},
    })

    assert len(calls) == 1
    assert calls[0][1]["infoJobData"] == {"v": 2}


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


def test_discover_filters_by_data_category(client):
    """OPEN_ITEMS.md section 5: data_category was declared as a query
    param but silently never applied — every call returned every type
    regardless of the filter.
    """
    client.post("/production-capabilities", json=register_type_body(name="CoverageIssue"))
    client.post("/production-capabilities", json={
        "namespace": "AIML", "name": "ModelHealth", "version": "1.0.0", "typeName": "AIML.ModelHealth",
        "producerId": "ai-ml-workflow", "dataProductionSchema": {"type": "object"},
        "producerHealthCallbackUrl": "http://ai-ml-workflow:8000/health",
        "jobCallbackUrl": "http://ai-ml-workflow:8000/dme-jobs",
    })

    resp = client.get("/dme-types", params={"data_category": "AIML"})
    names = [t["typeName"] for t in resp.json()]
    assert names == ["AIML.ModelHealth"]


def test_discover_without_data_category_returns_every_type(client):
    client.post("/production-capabilities", json=register_type_body(name="CoverageIssue"))
    client.post("/production-capabilities", json={
        "namespace": "AIML", "name": "ModelHealth", "version": "1.0.0", "typeName": "AIML.ModelHealth",
        "producerId": "ai-ml-workflow", "dataProductionSchema": {"type": "object"},
        "producerHealthCallbackUrl": "http://ai-ml-workflow:8000/health",
        "jobCallbackUrl": "http://ai-ml-workflow:8000/dme-jobs",
    })

    resp = client.get("/dme-types")
    assert len(resp.json()) == 2


def test_create_data_job_pushes_the_job_to_the_producer(client, monkeypatch):
    """OPEN_ITEMS.md section 5: create_data_job/terminate_data_job only
    ever touched our own DB — ICS's own ProducerCallbacks.startInfoJob
    actually POSTs the job to the producer's jobCallbackUrl
    (ProducerJobInfo's wire shape). This is the actual fix.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    reg = client.post("/production-capabilities", json=register_type_body()).json()
    created = client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
        "productionJobDefinition": {"kpi": "throughput"},
    }).json()

    assert len(calls) == 1
    url, body = calls[0]
    assert url == "http://ran-nf-oam:8000/dme-jobs"
    assert body["infoJobIdentity"] == created["dataJobId"]
    assert body["infoTypeIdentity"] == reg["registrationId"]
    assert body["infoJobData"] == {"kpi": "throughput"}
    assert body["owner"] == "rapp-1"
    assert "lastUpdated" in body


def test_create_data_job_succeeds_even_if_the_producer_push_fails(client, monkeypatch):
    """Best-effort, same pattern as every other DME/FOCOM/A1-Related
    notification in this build — an unreachable producer must not fail
    the consumer-facing CreateDataJob call.
    """
    import httpx as httpx_module

    def raise_error(url, json=None, timeout=None):
        raise httpx_module.ConnectError("unreachable")

    monkeypatch.setattr("app.main.httpx.post", raise_error)

    reg = client.post("/production-capabilities", json=register_type_body()).json()
    resp = client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
    })
    assert resp.status_code == 202


def test_terminate_data_job_stops_the_job_at_the_producer(client, monkeypatch):
    """ICS's own ProducerCallbacks.stopInfoJob — DELETE to
    jobCallbackUrl/{jobId}.
    """
    reg = client.post("/production-capabilities", json=register_type_body()).json()
    created = client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
    }).json()

    calls = []
    monkeypatch.setattr("app.main.httpx.delete", lambda url, timeout=None: calls.append(url))

    client.delete(f"/data-jobs/{created['dataJobId']}")
    assert calls == [f"http://ran-nf-oam:8000/dme-jobs/{created['dataJobId']}"]


def test_terminate_unknown_data_job_pushes_nothing(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.delete", lambda url, timeout=None: calls.append(url))

    resp = client.delete("/data-jobs/11111111-1111-1111-1111-111111111111")
    assert resp.status_code == 204
    assert calls == []


def test_terminate_data_job_succeeds_even_if_the_producer_stop_fails(client, monkeypatch):
    import httpx as httpx_module

    def raise_error(url, timeout=None):
        raise httpx_module.ConnectError("unreachable")

    reg = client.post("/production-capabilities", json=register_type_body()).json()
    created = client.post("/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
        "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1",
    }).json()

    monkeypatch.setattr("app.main.httpx.delete", raise_error)
    resp = client.delete(f"/data-jobs/{created['dataJobId']}")
    assert resp.status_code == 204


def test_register_dme_type_exposes_job_callback_url(client):
    client.post("/production-capabilities", json=register_type_body())
    resp = client.get("/dme-types")
    assert resp.json()[0]["jobCallbackUrl"] == "http://ran-nf-oam:8000/dme-jobs"


def test_query_producer_status_enabled_when_healthy(client, monkeypatch):
    """OPEN_ITEMS.md section 5: no producer-status endpoint existed at
    all — ICS's own GET .../info-producers/{id}/status.
    """
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        return FakeHealthResponse(200)

    monkeypatch.setattr("app.main.httpx.get", fake_get)
    client.post("/production-capabilities", json=register_type_body(producerId="ran-nf-oam"))

    resp = client.get("/production-capabilities/ran-nf-oam/status")
    assert resp.status_code == 200
    assert resp.json() == {"producerId": "ran-nf-oam", "operationalState": "ENABLED"}
    assert calls == ["http://ran-nf-oam:8000/health"]


def test_query_producer_status_disabled_when_unreachable(client, monkeypatch):
    import httpx as httpx_module

    def raise_error(url, timeout=None):
        raise httpx_module.ConnectError("unreachable")

    monkeypatch.setattr("app.main.httpx.get", raise_error)
    client.post("/production-capabilities", json=register_type_body(producerId="ran-nf-oam"))

    resp = client.get("/production-capabilities/ran-nf-oam/status")
    assert resp.status_code == 200
    assert resp.json()["operationalState"] == "DISABLED"


def test_query_producer_status_for_unknown_producer_is_404(client):
    resp = client.get("/production-capabilities/never-registered/status")
    assert resp.status_code == 404


def test_query_producer_status_after_deregistration_is_404(client):
    client.post("/production-capabilities", json=register_type_body(producerId="ran-nf-oam"))
    client.delete("/production-capabilities", params={"producer_id": "ran-nf-oam"})

    resp = client.get("/production-capabilities/ran-nf-oam/status")
    assert resp.status_code == 404


def test_subscribe_and_unsubscribe_type_changes(client):
    """OPEN_ITEMS.md section 5: ICS's own `/info-type-subscription` — a
    consumer notified whenever any DmeType is registered or removed.
    Entirely absent from this build until this pass.
    """
    sub = client.post("/type-subscriptions", json={"notificationDestination": "http://consumer/type-changes", "owner": "sa-smos"}).json()
    assert "subscriptionId" in sub

    resp = client.delete(f"/type-subscriptions/{sub['subscriptionId']}")
    assert resp.status_code == 204


def test_unsubscribe_unknown_type_subscription_is_idempotent(client):
    resp = client.delete("/type-subscriptions/11111111-1111-1111-1111-111111111111")
    assert resp.status_code == 204


def test_get_type_subscription_by_id_returns_its_fields(client):
    created = client.post("/type-subscriptions", json={"notificationDestination": "http://consumer/type-changes", "owner": "sa-smos"}).json()

    resp = client.get(f"/type-subscriptions/{created['subscriptionId']}")
    assert resp.status_code == 200
    assert resp.json() == {"subscriptionId": created["subscriptionId"], "notificationDestination": "http://consumer/type-changes", "owner": "sa-smos"}


def test_get_unknown_type_subscription_is_404(client):
    resp = client.get("/type-subscriptions/11111111-1111-1111-1111-111111111111")
    assert resp.status_code == 404


def test_list_type_subscriptions_filters_by_owner(client):
    client.post("/type-subscriptions", json={"notificationDestination": "http://sa-smos/type-changes", "owner": "sa-smos"})
    client.post("/type-subscriptions", json={"notificationDestination": "http://nfo/type-changes", "owner": "nfo"})

    resp = client.get("/type-subscriptions", params={"owner": "nfo"})
    assert [s["owner"] for s in resp.json()] == ["nfo"]


def test_register_dme_type_notifies_subscribers(client, monkeypatch):
    """The headline fix — ICS's own ConsumerCallbacks.notifyTypeRegistered.
    Unfiltered: every subscriber hears about every type registration,
    matching the reference's own lack of per-type scoping.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))
    client.post("/type-subscriptions", json={"notificationDestination": "http://consumer/type-changes", "owner": "sa-smos"})

    reg = client.post("/production-capabilities", json=register_type_body()).json()

    assert len(calls) == 1
    assert calls[0][0] == "http://consumer/type-changes"
    assert calls[0][1]["infoTypeId"] == reg["registrationId"]
    assert calls[0][1]["jobDataSchema"] == {"type": "object"}
    assert calls[0][1]["status"] == "REGISTERED"


def test_deregister_producer_notifies_subscribers_per_removed_type(client, monkeypatch):
    """ICS's own ConsumerCallbacks.notifyTypeRemoved — fired once per
    DmeType a producer's deregistration actually removes.
    """
    client.post("/production-capabilities", json=register_type_body(name="TypeA", producerId="rapp-1"))
    client.post("/production-capabilities", json=register_type_body(name="TypeB", producerId="rapp-1"))

    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))
    client.post("/type-subscriptions", json={"notificationDestination": "http://consumer/type-changes", "owner": "sa-smos"})

    client.delete("/production-capabilities", params={"producer_id": "rapp-1"})

    assert len(calls) == 2
    assert {c[1]["status"] for c in calls} == {"DEREGISTERED"}


def test_register_dme_type_does_not_notify_when_no_subscribers(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/production-capabilities", json=register_type_body())

    assert calls == []


def test_register_dme_type_succeeds_even_if_a_subscriber_is_unreachable(client, monkeypatch):
    import httpx as httpx_module

    def raise_error(url, json=None, timeout=None):
        raise httpx_module.ConnectError("unreachable")

    monkeypatch.setattr("app.main.httpx.post", raise_error)
    client.post("/type-subscriptions", json={"notificationDestination": "http://consumer/type-changes", "owner": "sa-smos"})

    resp = client.post("/production-capabilities", json=register_type_body())
    assert resp.status_code == 201  # must not raise despite the unreachable subscriber
