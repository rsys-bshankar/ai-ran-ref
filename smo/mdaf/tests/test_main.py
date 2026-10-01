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
    from app import models as mdaf_models
    Base.metadata.create_all(engine, tables=[
        cls.__table__ for cls in vars(mdaf_models).values()
        if isinstance(cls, type) and issubclass(cls, Base) and cls is not Base and cls.__module__ == mdaf_models.__name__
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
    yield TestClient(app)
    app.dependency_overrides.clear()


class FakeDmeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.fixture(autouse=True)
def _dme_data_jobs_are_known_by_default(monkeypatch):
    """Wave 3: publish_report's cross-service check
    (docs/ownership/DME_OWNERSHIP.md's "MDAF sources from DME only" rule)
    calls out to DME for every input_sources id. Every existing test uses
    either an empty list or an arbitrary placeholder UUID never meant to
    be a real negative-case test — defaulting the lookup to "found" keeps
    those tests focused on what they actually test.
    `test_publish_report_rejects_unknown_dme_input_source` overrides this.
    """
    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: FakeDmeResponse(200))


def test_publish_and_query_report_by_analytics_type(client):
    dme_type = str(uuid.uuid4())
    resp = client.post("/reports", params={"analytics_type": "resource-utilization"},
                        json={"output": {"utilization": 0.7}, "input_sources": [dme_type]})
    assert resp.status_code == 201

    listing = client.get("/reports", params={"analytics_type": "resource-utilization"})
    assert len(listing.json()["items"]) == 1
    assert listing.json()["items"][0]["output"]["utilization"] == 0.7

    empty = client.get("/reports", params={"analytics_type": "failure-prediction"})
    assert empty.json()["items"] == []


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
    assert len(all_reports.json()["items"]) == 2


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
    subs = resp.json()["items"]
    assert len(subs) == 1
    assert subs[0]["analyticsType"] == "coverage-issue-analysis"
    assert subs[0]["requestedBy"] == "sa-smos"


def test_list_subscriptions_filters_by_analytics_type(client):
    client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos"})
    client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "nfo"})

    resp = client.get("/subscriptions", params={"analytics_type": "resource-utilization"})
    requesters = [s["requestedBy"] for s in resp.json()["items"]]
    assert requesters == ["nfo"]


def test_list_subscriptions_filters_by_requested_by(client):
    client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos"})
    client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "sa-smos"})
    client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "nfo"})

    resp = client.get("/subscriptions", params={"requested_by": "sa-smos"})
    assert len(resp.json()["items"]) == 2
    assert {s["requestedBy"] for s in resp.json()["items"]} == {"sa-smos"}


def test_list_subscriptions_excludes_unsubscribed(client):
    sub = client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos"}).json()
    client.delete(f"/subscriptions/{sub['subscriptionId']}")

    resp = client.get("/subscriptions")
    assert resp.json()["items"] == []


def test_publish_report_notifies_subscriber_with_a_notification_destination(client, monkeypatch):
    """OPEN_ITEMS.md section 5: PublishAnalyticsReport's subscriber loop
    was a deliberate no-op (`for sub in subs: pass`) — a matching
    MDASubscription was looked up but never actually notified. This is
    the headline fix — a published report now actually reaches a
    matching subscriber's notificationDestination.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos"}, json={"notificationDestination": "http://sa-smos:8000/analytics-reports"})
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

    client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "nfo"}, json={"notificationDestination": "http://nfo:8000/analytics-reports"})
    client.post("/reports", params={"analytics_type": "coverage-issue-analysis"}, json={"output": {}, "input_sources": []})

    assert calls == []


def test_publish_report_notification_delivery_survives_unreachable_subscriber(client, monkeypatch):
    import httpx as httpx_module

    def raise_error(url, json=None, timeout=None):
        raise httpx_module.ConnectError("unreachable")

    monkeypatch.setattr("app.main.httpx.post", raise_error)

    client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos"}, json={"notificationDestination": "http://sa-smos:8000/analytics-reports"})
    resp = client.post("/reports", params={"analytics_type": "coverage-issue-analysis"}, json={"output": {}, "input_sources": []})

    assert resp.status_code == 201  # must not raise despite the unreachable subscriber


def test_list_subscriptions_exposes_notification_destination(client):
    client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos"}, json={"notificationDestination": "http://sa-smos:8000/analytics-reports"})

    resp = client.get("/subscriptions")
    assert resp.json()["items"][0]["notificationDestination"] == "http://sa-smos:8000/analytics-reports"


def test_publish_report_rejects_unknown_dme_input_source(client, monkeypatch):
    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: FakeDmeResponse(404))
    resp = client.post("/reports", params={"analytics_type": "resource-utilization"},
                        json={"output": {"utilization": 0.7}, "input_sources": [str(uuid.uuid4())]})
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "DME_ARTIFACT_NOT_FOUND"


def test_publish_report_accepts_multiple_known_dme_input_sources(client):
    resp = client.post("/reports", params={"analytics_type": "resource-utilization"},
                        json={"output": {"utilization": 0.7}, "input_sources": [str(uuid.uuid4()), str(uuid.uuid4())]})
    assert resp.status_code == 201


# ---------------------------------------------------------------- Wave 3: ThresholdInfo conditional reporting

def test_subscribe_rejects_unknown_threshold_direction(client):
    resp = client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "nfo"},
                        json={"thresholdInfo": [{"monitoredMDAOutputIE": "utilization", "thresholdDirection": "SIDEWAYS", "thresholdValue": 0.8}]})
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "SCHEMA_VALIDATION_FAILED"


def test_subscribe_persists_threshold_info(client):
    sub = client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "nfo"},
                       json={"thresholdInfo": [{"monitoredMDAOutputIE": "utilization", "thresholdDirection": "UP",
                                                 "thresholdValue": 0.8, "hysteresis": 0.05}]}).json()
    listed = client.get("/subscriptions").json()["items"]
    assert listed[0]["thresholdInfo"] == [{"monitoredMDAOutputIE": "utilization", "thresholdDirection": "UP",
                                            "thresholdValue": 0.8, "hysteresis": 0.05}]
    assert listed[0]["subscriptionId"] == sub["subscriptionId"]


def test_threshold_subscription_fires_on_first_report_already_crossed(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))
    client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "nfo"},
                json={"notificationDestination": "http://nfo:8000/analytics-reports", "thresholdInfo": [{"monitoredMDAOutputIE": "utilization", "thresholdDirection": "UP", "thresholdValue": 0.8}]})
    client.post("/reports", params={"analytics_type": "resource-utilization"},
                json={"output": {"utilization": 0.9}, "input_sources": []})
    assert len(calls) == 1


def test_threshold_subscription_does_not_notify_below_threshold(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))
    client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "nfo"},
                json={"notificationDestination": "http://nfo:8000/analytics-reports", "thresholdInfo": [{"monitoredMDAOutputIE": "utilization", "thresholdDirection": "UP", "thresholdValue": 0.8}]})
    client.post("/reports", params={"analytics_type": "resource-utilization"},
                json={"output": {"utilization": 0.5}, "input_sources": []})
    assert calls == []


def test_threshold_subscription_does_not_refire_while_staying_above(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))
    client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "nfo"},
                json={"notificationDestination": "http://nfo:8000/analytics-reports", "thresholdInfo": [{"monitoredMDAOutputIE": "utilization", "thresholdDirection": "UP",
                                          "thresholdValue": 0.8, "hysteresis": 0.05}]})
    for value in (0.9, 0.85, 0.92):
        client.post("/reports", params={"analytics_type": "resource-utilization"},
                    json={"output": {"utilization": value}, "input_sources": []})
    assert len(calls) == 1  # only the first report (0.9) crossed; 0.85/0.92 stay on the ABOVE side


def test_threshold_subscription_refires_after_reset_and_recross(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))
    client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "nfo"},
                json={"notificationDestination": "http://nfo:8000/analytics-reports", "thresholdInfo": [{"monitoredMDAOutputIE": "utilization", "thresholdDirection": "UP",
                                          "thresholdValue": 0.8, "hysteresis": 0.05}]})
    for value in (0.9, 0.72, 0.95):  # cross up, drop below the reset line (0.8-0.05), cross up again
        client.post("/reports", params={"analytics_type": "resource-utilization"},
                    json={"output": {"utilization": value}, "input_sources": []})
    assert len(calls) == 2


def test_threshold_subscription_ignores_reports_missing_the_monitored_field(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))
    client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "nfo"},
                json={"notificationDestination": "http://nfo:8000/analytics-reports", "thresholdInfo": [{"monitoredMDAOutputIE": "utilization", "thresholdDirection": "UP", "thresholdValue": 0.8}]})
    client.post("/reports", params={"analytics_type": "resource-utilization"},
                json={"output": {"somethingElse": 1}, "input_sources": []})
    assert calls == []


def test_threshold_subscription_down_direction_fires_on_drop(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))
    client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "nfo"},
                json={"notificationDestination": "http://nfo:8000/analytics-reports", "thresholdInfo": [{"monitoredMDAOutputIE": "freeCapacity", "thresholdDirection": "DOWN", "thresholdValue": 0.1}]})
    client.post("/reports", params={"analytics_type": "resource-utilization"},
                json={"output": {"freeCapacity": 0.05}, "input_sources": []})
    assert len(calls) == 1


def test_subscription_without_threshold_info_is_still_notified_every_report(client, monkeypatch):
    """Regression: the pre-existing always-notify behavior for a plain
    subscription (no thresholdInfo) must be unaffected by this feature.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))
    client.post("/subscriptions", params={"analytics_type": "resource-utilization", "requested_by": "nfo"},
                json={"notificationDestination": "http://nfo:8000/analytics-reports"})
    for value in (0.1, 0.9, 0.1):
        client.post("/reports", params={"analytics_type": "resource-utilization"},
                    json={"output": {"utilization": value}, "input_sources": []})
    assert len(calls) == 3


def test_health_check_answers_the_gui_bff_liveness_probe(client):
    """GUI pass: the BFF's /modules/status probes /<module>/health on every module."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}
