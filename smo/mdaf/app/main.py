"""MDAF (Management Data Analytics Function) — TS 28.104 MDA NRM realization.

Wave 1 of the AI Platform Service Decomposition: split out of the former
`ran-analytics/` module, which kept its own use-case-specific role
(traffic/energy/coverage analytics) and becomes an MDAF consumer rather
than the service owning analytics reporting itself — see
docs/ARCHITECTURE.md and
docs/ARCHITECTURE.md (MDAF). MDAF is analytics truth: it owns the
*output* of analysis (reports, subscriptions), not any domain-specific
production logic — `MDAFProducer` and its registration route stay in
`ran-analytics/`, which is why this module never calls out to it: a
producer registering itself and a report being published are
independent concerns here, exactly as they were before the split (the
original code never validated a report's producer registration either).

Wave 3 (docs/ARCHITECTURE.md (DME)): `publish_report` now does call
out cross-service, to DME — every `input_sources` id must be a real DME
`DataJob`, closing the "MDAF sources from DME only" rule with an
enforced check rather than a documented convention. MDAF never reaches
DME's O1 action-mediation path; this is the data path only.
"""

import uuid

import httpx
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.logconfig import install_logging
from smo_shared.metrics import install_metrics
from smo_shared.health import database_check, install_health, sme_token_check
from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.webhook import post_webhook
from smo_shared.pagination import PageLimit, PageOffset, paginate

from .models import MDAFReport, MDASubscription

THRESHOLD_DIRECTIONS = {"UP", "DOWN", "UP_AND_DOWN"}  # TS28.104 ThresholdInfo.thresholdDirection's exact wire values


class ThresholdInfo(BaseModel):
    monitoredMDAOutputIE: str
    thresholdDirection: str
    thresholdValue: float
    hysteresis: float = 0

app = FastAPI(title="MDAF")
install_logging(app)  # structured JSON logs and one access-log line per request (PR-OBS-1)
install_metrics(app)  # /metrics and request count/latency series (PR-OBS-2)
apply_r1_gateway_security(app)
apply_correlation_id(app)

_r1 = R1Client()


install_health(app, checks=[database_check, sme_token_check])  # /live, /ready and the /health alias (PR-ST-7)


def _validate_input_sources_are_real_dme_artifacts(input_sources: list[uuid.UUID]) -> None:
    """Wave 3 (AI Platform Service Decomposition) —
    docs/ARCHITECTURE.md's DME "two paths, not one": MDAF is a
    consumer of DME's data plane like any rApp, never the O1 action
    path, and a report can no longer cite data that never actually came
    from DME. Same bare-UUID cross-service-reference convention used
    for NFO/AIMgF elsewhere in this build — MDAF doesn't fetch the data
    itself here, only proves the reference is real.
    """
    for source_id in input_sources:
        resp = _r1.get(f"/dme/data-jobs/{source_id}")
        if resp.status_code != 200:
            raise framework_error(FrameworkError.DME_ARTIFACT_NOT_FOUND, detail=f"no such DME data job {source_id}")


@app.post("/reports", status_code=201)
def publish_report(analytics_type: str, output: dict, input_sources: list[uuid.UUID], scope: dict | None = None, db: Session = Depends(get_session)):
    _validate_input_sources_are_real_dme_artifacts(input_sources)
    report = MDAFReport(analytics_type=analytics_type, output=output, input_sources=input_sources, scope=scope)
    db.add(report)
    db.commit()
    _notify_report_subscribers(db, report)
    # Wave 5: a producer-push report also satisfies matching open MDARequests.
    _deliver_legacy_report(db, report)
    return {"reportId": str(report.report_id)}


def _threshold_crossed(sub: MDASubscription, output: dict) -> bool:
    """TS28.104 ThresholdInfo — edge-triggered crossing with a real
    hysteresis band, not a level check re-fired on every report (that
    would make `hysteresis` a declared-but-unused field, the exact
    anti-pattern this build's own audits keep catching elsewhere).
    Mutates `sub.threshold_state` in place (caller commits); returns
    True the moment ANY of this subscription's thresholds crosses.
    `threshold_state` maps monitoredMDAOutputIE -> "ABOVE"/"BELOW", the
    side last observed — None (never yet observed, or the metric hasn't
    appeared in a report yet) fires nothing on its own, it only seeds
    the state so the *next* report can detect a real transition.
    """
    if not sub.threshold_info:
        return False
    state = dict(sub.threshold_state or {})
    crossed = False
    for entry in sub.threshold_info:
        ie, direction = entry["monitoredMDAOutputIE"], entry["thresholdDirection"]
        if ie not in output:
            continue
        value, threshold, hysteresis = output[ie], entry["thresholdValue"], entry["hysteresis"]
        previous = state.get(ie)
        if direction in ("UP", "UP_AND_DOWN") and value >= threshold and previous != "ABOVE":
            crossed, state[ie] = True, "ABOVE"
        elif direction in ("DOWN", "UP_AND_DOWN") and value <= threshold and previous != "BELOW":
            crossed, state[ie] = True, "BELOW"
        elif value < threshold - hysteresis:
            state[ie] = "BELOW"
        elif value > threshold + hysteresis:
            state[ie] = "ABOVE"
        # inside the hysteresis band and no new crossing: leave state as-is
    sub.threshold_state = state
    return crossed


def _notify_report_subscribers(db: Session, report: MDAFReport) -> None:
    """HISTORY.md §5: PublishAnalyticsReport's subscriber loop
    was a deliberate no-op (`for sub in subs: pass`) — a matching
    MDASubscription was looked up but never actually notified, so every
    consumer had to poll QueryAnalyticsReport instead. Same shape fix as
    A1 Related's `_notify_policy_status_subscribers`/Intent Service's
    CreateIntent notification: best-effort, an unreachable subscriber
    never fails the publish that triggered it. Only subscriptions that
    registered a real `notificationDestination` are ever POSTed to — one
    that didn't (e.g. a purely poll-based consumer) is left alone rather
    than guessing a delivery target from `requestedBy`.

    Wave 3: a subscription with `threshold_info` set is now conditional —
    notified only on a real threshold crossing (see `_threshold_crossed`),
    not on every report. A subscription with no threshold_info keeps the
    original always-notify behavior, unchanged.
    """
    subs = db.scalars(select(MDASubscription).where(MDASubscription.analytics_type == report.analytics_type)).all()
    for sub in subs:
        crossed = _threshold_crossed(sub, report.output)
        if sub.threshold_info and not crossed:
            continue
        post_webhook(sub.notification_destination, json={
            "reportId": str(report.report_id), "analyticsType": report.analytics_type,
            "output": report.output, "inputSources": [str(s) for s in report.input_sources],
        }, timeout=2.0)
    db.commit()  # persists threshold_state even for subscriptions that didn't cross (or have no destination)


class SubscribeAnalyticsRequest(BaseModel):
    """`scope`'s wire shape changes here from a bare JSON body to this
    named model — FastAPI can't leave `scope` as the implicit unwrapped
    body once a second body-eligible field (`thresholdInfo`) exists
    alongside it. No real caller sets `scope` on a subscription today
    (grepped: ran-analytics never does, and no test did either before
    this pass), so this is a real but zero-blast-radius wire change.
    """

    scope: dict | None = None
    thresholdInfo: list[ThresholdInfo] | None = None
    # Wave 3 (cross-cutting standardization, Subscriptions): was a query
    # param — the one subscription-shaped resource in this build taking
    # its callback outside the body, unlike DME/A1-Related/Intent
    # Service's own notificationDestination body field it's now unified
    # with.
    notificationDestination: str | None = None


@app.post("/subscriptions", status_code=201)
def subscribe_analytics(analytics_type: str, requested_by: str,
                         body: SubscribeAnalyticsRequest = SubscribeAnalyticsRequest(), db: Session = Depends(get_session)):
    if body.thresholdInfo is not None:
        bad = [t.thresholdDirection for t in body.thresholdInfo if t.thresholdDirection not in THRESHOLD_DIRECTIONS]
        if bad:
            raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=f"unknown thresholdDirection {bad[0]!r}")
    sub = MDASubscription(analytics_type=analytics_type, requested_by=requested_by, notification_destination=body.notificationDestination,
                          scope=body.scope, threshold_info=[t.model_dump() for t in body.thresholdInfo] if body.thresholdInfo else None)
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
def list_analytics_subscriptions(analytics_type: str | None = None, requested_by: str | None = None,
                                  limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    """HISTORY.md §5: no list/query endpoint for active
    subscriptions existed at all — the reference defines this route
    (even though its own implementation of it is a no-op stub; ours
    actually reads real, persisted subscriptions).
    """
    stmt = select(MDASubscription)
    if analytics_type:
        stmt = stmt.where(MDASubscription.analytics_type == analytics_type)
    if requested_by:
        stmt = stmt.where(MDASubscription.requested_by == requested_by)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_subscription_view(s) for s in page["items"]]}


@app.get("/reports")
def query_analytics_report(analytics_type: str | None = None, limit: int = PageLimit, offset: int = PageOffset,
                            db: Session = Depends(get_session)):
    stmt = select(MDAFReport)
    if analytics_type:
        stmt = stmt.where(MDAFReport.analytics_type == analytics_type)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"reportId": str(r.report_id), "analyticsType": r.analytics_type, "output": r.output,
                               "reportKind": r.report_kind, "mdaType": r.mda_type} for r in page["items"]]}


def _subscription_view(s: MDASubscription) -> dict:
    return {"subscriptionId": str(s.subscription_id), "analyticsType": s.analytics_type,
            "requestedBy": s.requested_by, "notificationDestination": s.notification_destination, "scope": s.scope,
            "thresholdInfo": s.threshold_info}


# ---------------------------------------------------------------- Wave 5: TS 28.104 MDA NRM resources
# Imported last: app/mda.py reuses the helpers above. Bound at load time,
# never imported lazily inside a route (the integration mesh's loader
# evicts `app.*` from sys.modules after loading each service).
from .mda import deliver_legacy_report as _deliver_legacy_report, router as _mda_router  # noqa: E402

app.include_router(_mda_router)
