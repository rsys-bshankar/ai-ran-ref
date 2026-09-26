"""Tests for MDAF's routes (RAN Analytics LLD section 1; TS 28.104 MDA NRM).
Run with: pytest smo/mdaf/tests -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smo_shared.db import Base, get_session
from smo_shared.testing import make_test_engine

from app.main import app
from app.models import MDAFReport, MDASubscription


@pytest.fixture
def db_session_factory():
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[MDAFReport.__table__, MDASubscription.__table__])
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
    test_reregistering_same_producer_and_type_updates_in_place (now in
    ran-analytics/tests) uses.
    """
    resp = client.post("/reports", params={"analytics_type": "coverage-issue-analysis"},
                        json={"output": {"a": 1}, "input_sources": [], "scope": {"cellId": "cell-42"}})
    assert resp.status_code == 201
    report_id = resp.json()["reportId"]

    with db_session_factory() as session:
        report = session.get(MDAFReport, uuid.UUID(report_id))
        assert report.scope == {"cellId": "cell-42"}


def test_list_subscriptions_returns_active_subscription(client):
    """OPEN_ITEMS.md section 5: no list/query endpoint for active
    subscriptions existed at all — same gap as ran-analytics's own
    producer list.
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


def test_health_check_answers_the_gui_bff_liveness_probe(client):
    """GUI pass: the BFF's /modules/status probes /<module>/health on every module."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}
