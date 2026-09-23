"""RAN Analytics SMOS (MDAF).

SMO Design v1.3 section 3.11, extended by RAN Analytics LLD section 1:
RegisterAnalyticsProducer closes the producer-side gap v1.3 left entirely
unmodeled (Subscribe/Unsubscribe/Query existed, nothing for a producer to
register against). Distinct domain from MLMF — RAN behavior, not model
performance (LLD section 2).
"""

import uuid

import httpx
from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.r1_client import R1Client

from .models import MDAFProducer, MDAFReport, MDASubscription

app = FastAPI(title="RAN Analytics SMOS (MDAF)")


@app.post("/producers", status_code=201)
def register_analytics_producer(producer_id: str, analytics_type: str, dme_input_types: list[uuid.UUID], output_schema: dict, db: Session = Depends(get_session)):
    """RegisterAnalyticsProducer — an update-in-place upsert on
    (producer_id, analytics_type), not just an insert: the same producer
    re-registering the same analytics_type (e.g. on restart) is a normal
    occurrence, not a conflict, and previously crashed with an unhandled
    IntegrityError on the composite primary key instead. Same shape of
    fix as SME's RegisterService (Foundational Platform LLD section 5).
    """
    prod = db.get(MDAFProducer, (producer_id, analytics_type))
    if prod is None:
        prod = MDAFProducer(producer_id=producer_id, analytics_type=analytics_type)
        db.add(prod)
    prod.dme_input_types = dme_input_types
    prod.output_schema = output_schema
    db.commit()
    R1Client().post("/sme/published-apis/v1/{}/service-apis".format(producer_id), json={
        "serviceName": f"mdaf.{analytics_type}", "producerId": producer_id, "endpoint": "internal",
        "version": "1.0", "serviceCapabilities": {"analyticsType": analytics_type}, "moduleScope": "ran-analytics",
    })
    return {"status": "registered"}


@app.post("/reports", status_code=201)
def publish_report(analytics_type: str, output: dict, input_sources: list[uuid.UUID], scope: dict | None = None, db: Session = Depends(get_session)):
    report = MDAFReport(analytics_type=analytics_type, output=output, input_sources=input_sources, scope=scope)
    db.add(report)
    db.commit()

    subs = db.scalars(select(MDASubscription).where(MDASubscription.analytics_type == analytics_type)).all()
    for sub in subs:
        pass  # Phase 1: notification callback elided; SubscribeAnalytics's requested_by is the delivery target
    return {"reportId": str(report.report_id)}


@app.post("/subscriptions", status_code=201)
def subscribe_analytics(analytics_type: str, requested_by: str, scope: dict | None = None, db: Session = Depends(get_session)):
    sub = MDASubscription(analytics_type=analytics_type, requested_by=requested_by, scope=scope)
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


@app.get("/producers")
def list_analytics_producers(analytics_type: str | None = None, producer_id: str | None = None, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: no list/query endpoint for registered
    producers existed at all — same gap as list_analytics_subscriptions.
    """
    stmt = select(MDAFProducer)
    if analytics_type:
        stmt = stmt.where(MDAFProducer.analytics_type == analytics_type)
    if producer_id:
        stmt = stmt.where(MDAFProducer.producer_id == producer_id)
    return [_producer_view(p) for p in db.scalars(stmt).all()]


@app.get("/reports")
def query_analytics_report(analytics_type: str | None = None, db: Session = Depends(get_session)):
    stmt = select(MDAFReport)
    if analytics_type:
        stmt = stmt.where(MDAFReport.analytics_type == analytics_type)
    return [{"reportId": str(r.report_id), "analyticsType": r.analytics_type, "output": r.output} for r in db.scalars(stmt).all()]


def _producer_view(p: MDAFProducer) -> dict:
    return {"producerId": p.producer_id, "analyticsType": p.analytics_type,
            "dmeInputTypes": [str(t) for t in p.dme_input_types], "outputSchema": p.output_schema}


def _subscription_view(s: MDASubscription) -> dict:
    return {"subscriptionId": str(s.subscription_id), "analyticsType": s.analytics_type,
            "requestedBy": s.requested_by, "scope": s.scope}
