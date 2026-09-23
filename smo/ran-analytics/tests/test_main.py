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
from app.models import MDAFProducer, MDAFReport, MDASubscription


@pytest.fixture
def db_session_factory():
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[MDAFProducer.__table__, MDAFReport.__table__, MDASubscription.__table__])
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


def test_publish_and_query_report_by_analytics_type(client):
    dme_type = str(uuid.uuid4())
    resp = client.post("/reports", params={"analytics_type": "resource-utilization"},
                        json={"output": {"utilization": 0.7}, "input_sources": [dme_type]})
    assert resp.status_code == 201

    listing = client.get("/reports", params={"analytics_type": "resource-utilization"})
    assert len(listing.json()) == 1
    assert listing.json()[0]["output"]["utilization"] == 0.7

    empty = client.get("/reports", params={"analytics_type": "failure-prediction"})
    assert empty.json() == []


def test_subscribe_and_unsubscribe_analytics(client):
    sub = client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos"}).json()
    assert "subscriptionId" in sub
    resp = client.delete(f"/subscriptions/{sub['subscriptionId']}")
    assert resp.status_code == 204


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

    assert len(calls) == 1
    path, payload = calls[0]
    assert path == "/sme/published-apis/v1/rapp-mdaf-2/service-apis"
    assert payload["serviceName"] == "mdaf.failure-prediction"
    assert payload["producerId"] == "rapp-mdaf-2"
    assert payload["moduleScope"] == "ran-analytics"


def test_unsubscribe_unknown_subscription_is_idempotent(client):
    """unsubscribe_analytics's `if sub is not None` guard means deleting a
    subscription that was never created (or already deleted) must not
    raise — never exercised before this pass.
    """
    resp = client.delete(f"/subscriptions/{uuid.uuid4()}")
    assert resp.status_code == 204


def test_query_all_reports_without_filter_returns_everything(client):
    """query_analytics_report's unfiltered path (no analytics_type query
    param) was never exercised — only the filtered and empty-filtered
    cases had coverage.
    """
    client.post("/reports", params={"analytics_type": "coverage-issue-analysis"}, json={"output": {"a": 1}, "input_sources": []})
    client.post("/reports", params={"analytics_type": "resource-utilization"}, json={"output": {"b": 2}, "input_sources": []})

    all_reports = client.get("/reports")
    assert len(all_reports.json()) == 2


def test_publish_report_persists_scope(client, db_session_factory):
    """scope is an optional field on MDAFReport — never actually set to a
    non-None value and read back before this pass. query_analytics_report's
    own response view doesn't expose scope at all, so this reads the row
    back from the DB directly, the same pattern
    test_reregistering_same_producer_and_type_updates_in_place uses.
    """
    resp = client.post("/reports", params={"analytics_type": "coverage-issue-analysis"},
                        json={"output": {"a": 1}, "input_sources": [], "scope": {"cellId": "cell-42"}})
    assert resp.status_code == 201
    report_id = resp.json()["reportId"]

    with db_session_factory() as session:
        report = session.get(MDAFReport, uuid.UUID(report_id))
        assert report.scope == {"cellId": "cell-42"}


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


def test_list_subscriptions_returns_active_subscription(client):
    """OPEN_ITEMS.md section 5: no list/query endpoint for active
    subscriptions existed at all — same gap as list_analytics_producers.
    """
    client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos"})

    resp = client.get("/subscriptions")
    assert resp.status_code == 200
    subs = resp.json()
    assert len(subs) == 1
    assert subs[0]["analyticsType"] == "coverage-issue-analysis"
    assert subs[0]["requestedBy"] == "sa-smos"


def test_list_subscriptions_filters_by_analytics_type(client):
    client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos"})
    client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "nfo"})

    resp = client.get("/subscriptions", params={"analytics_type": "resource-utilization"})
    requesters = [s["requestedBy"] for s in resp.json()]
    assert requesters == ["nfo"]


def test_list_subscriptions_filters_by_requested_by(client):
    client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos"})
    client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "sa-smos"})
    client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "nfo"})

    resp = client.get("/subscriptions", params={"requested_by": "sa-smos"})
    assert len(resp.json()) == 2
    assert {s["requestedBy"] for s in resp.json()} == {"sa-smos"}


def test_list_subscriptions_excludes_unsubscribed(client):
    sub = client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos"}).json()
    client.delete(f"/subscriptions/{sub['subscriptionId']}")

    resp = client.get("/subscriptions")
    assert resp.json() == []


def test_publish_report_notifies_subscriber_with_a_notification_destination(client, monkeypatch):
    """OPEN_ITEMS.md section 5: PublishAnalyticsReport's subscriber loop
    was a deliberate no-op (`for sub in subs: pass`) — a matching
    MDASubscription was looked up but never actually notified. This is
    the headline fix — a published report now actually reaches a
    matching subscriber's notificationDestination.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos", "notification_destination": "http://sa-smos:8000/analytics-reports"})
    resp = client.post("/reports", params={"analytics_type": "coverage-issue-analysis"}, json={"output": {"issue": "cellA"}, "input_sources": []})
    report_id = resp.json()["reportId"]

    assert len(calls) == 1
    assert calls[0][0] == "http://sa-smos:8000/analytics-reports"
    assert calls[0][1]["reportId"] == report_id
    assert calls[0][1]["analyticsType"] == "coverage-issue-analysis"
    assert calls[0][1]["output"] == {"issue": "cellA"}


def test_publish_report_does_not_notify_subscriber_without_a_notification_destination(client, monkeypatch):
    """A subscriber that never registered a notificationDestination is a
    purely poll-based consumer (QueryAnalyticsReport) — left alone
    rather than having a delivery target guessed for it.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos"})
    client.post("/reports", params={"analytics_type": "coverage-issue-analysis"}, json={"output": {}, "input_sources": []})

    assert calls == []


def test_publish_report_does_not_notify_subscriber_of_a_different_analytics_type(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "nfo", "notification_destination": "http://nfo:8000/analytics-reports"})
    client.post("/reports", params={"analytics_type": "coverage-issue-analysis"}, json={"output": {}, "input_sources": []})

    assert calls == []


def test_publish_report_notification_delivery_survives_unreachable_subscriber(client, monkeypatch):
    import httpx as httpx_module

    def raise_error(url, json=None, timeout=None):
        raise httpx_module.ConnectError("unreachable")

    monkeypatch.setattr("app.main.httpx.post", raise_error)

    client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos", "notification_destination": "http://sa-smos:8000/analytics-reports"})
    resp = client.post("/reports", params={"analytics_type": "coverage-issue-analysis"}, json={"output": {}, "input_sources": []})

    assert resp.status_code == 201  # must not raise despite the unreachable subscriber


def test_list_subscriptions_exposes_notification_destination(client):
    client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos", "notification_destination": "http://sa-smos:8000/analytics-reports"})

    resp = client.get("/subscriptions")
    assert resp.json()[0]["notificationDestination"] == "http://sa-smos:8000/analytics-reports"
