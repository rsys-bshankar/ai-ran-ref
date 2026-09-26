"""MDAF (Management Data Analytics Function) — TS 28.104 MDA NRM realization.

Wave 1 of the AI Platform Service Decomposition: split out of the former
`ran-analytics/` module, which kept its own use-case-specific role
(traffic/energy/coverage analytics) and becomes an MDAF consumer rather
than the service owning analytics reporting itself — see
docs/architecture/AI_PLATFORM_BASELINE.md and
docs/ownership/MDAF_OWNERSHIP.md. MDAF is analytics truth: it owns the
*output* of analysis (reports, subscriptions), not any domain-specific
production logic — `MDAFProducer` and its registration route stay in
`ran-analytics/`, which is why this module never calls out to it: a
producer registering itself and a report being published are
independent concerns here, exactly as they were before the split (the
original code never validated a report's producer registration either).
"""

import uuid

import httpx
from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session

from .models import MDAFReport, MDASubscription

app = FastAPI(title="MDAF")


@app.get("/health")
def health_check():
    """Liveness probe. The GUI BFF's GET /modules/status fans out to
    /<module>/health through R1 Termination for every module in parallel.
    """
    return {"status": "healthy"}


@app.post("/reports", status_code=201)
def publish_report(analytics_type: str, output: dict, input_sources: list[uuid.UUID], scope: dict | None = None, db: Session = Depends(get_session)):
    report = MDAFReport(analytics_type=analytics_type, output=output, input_sources=input_sources, scope=scope)
    db.add(report)
    db.commit()
    _notify_report_subscribers(db, report)
    return {"reportId": str(report.report_id)}


def _notify_report_subscribers(db: Session, report: MDAFReport) -> None:
    """OPEN_ITEMS.md section 5: PublishAnalyticsReport's subscriber loop
    was a deliberate no-op (`for sub in subs: pass`) — a matching
    MDASubscription was looked up but never actually notified, so every
    consumer had to poll QueryAnalyticsReport instead. Same shape fix as
    A1 Related's `_notify_policy_status_subscribers`/Intent Service's
    CreateIntent notification: best-effort, an unreachable subscriber
    never fails the publish that triggered it. Only subscriptions that
    registered a real `notificationDestination` are ever POSTed to — one
    that didn't (e.g. a purely poll-based consumer) is left alone rather
    than guessing a delivery target from `requestedBy`.
    """
    subs = db.scalars(select(MDASubscription).where(MDASubscription.analytics_type == report.analytics_type)).all()
    for sub in subs:
        if not sub.notification_destination:
            continue
        try:
            httpx.post(sub.notification_destination, json={
                "reportId": str(report.report_id), "analyticsType": report.analytics_type,
                "output": report.output, "inputSources": [str(s) for s in report.input_sources],
            }, timeout=2.0)
        except httpx.HTTPError:
            pass


@app.post("/subscriptions", status_code=201)
def subscribe_analytics(analytics_type: str, requested_by: str, notification_destination: str | None = None, scope: dict | None = None, db: Session = Depends(get_session)):
    sub = MDASubscription(analytics_type=analytics_type, requested_by=requested_by, notification_destination=notification_destination, scope=scope)
    db.add(sub)
    db.commit()
    return {"subscriptionId": str(sub.subscription_id)}


@app.delete("/subscriptions/{subscription_id}", status_code=204)
def unsubscribe_analytics(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    sub = db.get(MDASubscription, subscription_id)
    if sub is not None:
        db.delete(sub)
        db.commit()


@app.get("/subscriptions")
def list_analytics_subscriptions(analytics_type: str | None = None, requested_by: str | None = None, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: no list/query endpoint for active
    subscriptions existed at all — the reference defines this route
    (even though its own implementation of it is a no-op stub; ours
    actually reads real, persisted subscriptions).
    """
    stmt = select(MDASubscription)
    if analytics_type:
        stmt = stmt.where(MDASubscription.analytics_type == analytics_type)
    if requested_by:
        stmt = stmt.where(MDASubscription.requested_by == requested_by)
    return [_subscription_view(s) for s in db.scalars(stmt).all()]


@app.get("/reports")
def query_analytics_report(analytics_type: str | None = None, db: Session = Depends(get_session)):
    stmt = select(MDAFReport)
    if analytics_type:
        stmt = stmt.where(MDAFReport.analytics_type == analytics_type)
    return [{"reportId": str(r.report_id), "analyticsType": r.analytics_type, "output": r.output} for r in db.scalars(stmt).all()]


def _subscription_view(s: MDASubscription) -> dict:
    return {"subscriptionId": str(s.subscription_id), "analyticsType": s.analytics_type,
            "requestedBy": s.requested_by, "notificationDestination": s.notification_destination, "scope": s.scope}
