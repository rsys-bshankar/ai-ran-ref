"""Tests for RAN Analytics SMOS (RAN Analytics LLD section 1).
Run with: pytest smo/ran-analytics/tests -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smo_shared.db import Base, get_session
from smo_shared.testing import make_test_engine

from app.main import app
from app.models import MDAFProducer


@pytest.fixture
def db_session_factory():
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[MDAFProducer.__table__])
    return sessionmaker(bind=engine)


@pytest.fixture
def client(db_session_factory, monkeypatch):
    def override_get_session():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: None)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_register_analytics_producer_closes_the_v1_3_gap(client):
    """RAN Analytics LLD section 1: v1.3 had Subscribe/Unsubscribe/Query
    but no way for a producer to register at all.
    """
    dme_type = str(uuid.uuid4())
    resp = client.post("/producers", params={"producer_id": "rapp-mdaf-1", "analytics_type": "coverage-issue-analysis"},
                        json={"dme_input_types": [dme_type], "output_schema": {"type": "object"}})
    assert resp.status_code == 201


def test_reregistering_same_producer_and_type_updates_in_place(client, db_session_factory):
    """The real fix this pass made: the same producer re-registering the
    same analytics_type (e.g. on restart) previously crashed with an
    unhandled IntegrityError on the (producer_id, analytics_type)
    composite primary key instead of updating in place — same shape of
    bug as SME's RegisterService had.
    """
    first_dme_type = uuid.uuid4()
    resp1 = client.post("/producers", params={"producer_id": "rapp-mdaf-1", "analytics_type": "coverage-issue-analysis"},
                         json={"dme_input_types": [str(first_dme_type)], "output_schema": {"type": "object"}})
    assert resp1.status_code == 201

    second_dme_type = uuid.uuid4()
    resp2 = client.post("/producers", params={"producer_id": "rapp-mdaf-1", "analytics_type": "coverage-issue-analysis"},
                         json={"dme_input_types": [str(second_dme_type)], "output_schema": {"type": "string"}})
    assert resp2.status_code == 201

    with db_session_factory() as session:
        rows = session.query(MDAFProducer).filter_by(producer_id="rapp-mdaf-1", analytics_type="coverage-issue-analysis").all()
        assert len(rows) == 1  # updated in place, not a second row or a crash
        assert rows[0].dme_input_types == [str(second_dme_type)]
        assert rows[0].output_schema == {"type": "string"}


def test_different_analytics_type_for_same_producer_is_a_separate_row(client):
    """producer_id + analytics_type together are the key — a different
    analytics_type for the same producer is a distinct registration, not
    a conflict with the one above.
    """
    resp1 = client.post("/producers", params={"producer_id": "rapp-mdaf-1", "analytics_type": "coverage-issue-analysis"},
                         json={"dme_input_types": [], "output_schema": {}})
    resp2 = client.post("/producers", params={"producer_id": "rapp-mdaf-1", "analytics_type": "resource-utilization"},
                         json={"dme_input_types": [], "output_schema": {}})
    assert resp1.status_code == 201
    assert resp2.status_code == 201


def test_register_analytics_producer_publishes_sme_service_registration(client, monkeypatch):
    """RegisterAnalyticsProducer's SME side-effect (POST to
    /sme/published-apis/.../service-apis) was never asserted on — only
    that the route itself returned 201. Confirms the mdaf.<analyticsType>
    naming and producerId actually reach SME.
    """
    calls = []
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: calls.append((path, json)))

    dme_type = str(uuid.uuid4())
    client.post("/producers", params={"producer_id": "rapp-mdaf-2", "analytics_type": "failure-prediction"},
                json={"dme_input_types": [dme_type], "output_schema": {"type": "object"}})

    assert len(calls) == 2
    enroll_path, enroll_payload = calls[0]
    assert enroll_path == "/sme/provider-registrations"
    assert enroll_payload == {"apfId": "rapp-mdaf-2"}
    path, payload = calls[1]
    assert path == "/sme/published-apis/v1/rapp-mdaf-2/service-apis"
    assert payload["serviceName"] == "mdaf.failure-prediction"
    assert payload["producerId"] == "rapp-mdaf-2"
    assert payload["moduleScope"] == "ran-analytics"


def test_list_producers_returns_registered_producer(client):
    """OPEN_ITEMS.md section 5: no list/query endpoint for registered
    producers existed at all — the reference defines this route (even
    if its own implementation is a no-op stub).
    """
    dme_type = str(uuid.uuid4())
    client.post("/producers", params={"producer_id": "rapp-mdaf-1", "analytics_type": "coverage-issue-analysis"},
                json={"dme_input_types": [dme_type], "output_schema": {"type": "object"}})

    resp = client.get("/producers")
    assert resp.status_code == 200
    producers = resp.json()
    assert len(producers) == 1
    assert producers[0]["producerId"] == "rapp-mdaf-1"
    assert producers[0]["analyticsType"] == "coverage-issue-analysis"
    assert producers[0]["dmeInputTypes"] == [dme_type]


def test_list_producers_filters_by_analytics_type(client):
    client.post("/producers", params={"producer_id": "rapp-mdaf-1", "analytics_type": "coverage-issue-analysis"},
                json={"dme_input_types": [], "output_schema": {}})
    client.post("/producers", params={"producer_id": "rapp-mdaf-2", "analytics_type": "resource-utilization"},
                json={"dme_input_types": [], "output_schema": {}})

    resp = client.get("/producers", params={"analytics_type": "resource-utilization"})
    ids = [p["producerId"] for p in resp.json()]
    assert ids == ["rapp-mdaf-2"]


def test_list_producers_filters_by_producer_id(client):
    client.post("/producers", params={"producer_id": "rapp-mdaf-1", "analytics_type": "coverage-issue-analysis"},
                json={"dme_input_types": [], "output_schema": {}})
    client.post("/producers", params={"producer_id": "rapp-mdaf-1", "analytics_type": "resource-utilization"},
                json={"dme_input_types": [], "output_schema": {}})
    client.post("/producers", params={"producer_id": "rapp-mdaf-2", "analytics_type": "resource-utilization"},
                json={"dme_input_types": [], "output_schema": {}})

    resp = client.get("/producers", params={"producer_id": "rapp-mdaf-1"})
    assert len(resp.json()) == 2
    assert {p["producerId"] for p in resp.json()} == {"rapp-mdaf-1"}


def test_list_producers_returns_empty_list_when_none_registered(client):
    resp = client.get("/producers")
    assert resp.json() == []


def test_health_check_answers_the_gui_bff_liveness_probe(client):
    """GUI pass: the BFF's /modules/status probes /<module>/health on every module."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}
