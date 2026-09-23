"""SME (Service Management and Exposure).

R1AP clause 6, profile of 3GPP TS 29.222 (CAPIF). Most mature R1 service
group — all four APIs stable, no alpha tags (Foundational Platform LLD
section 2.6). Real logic here for the two ambiguities the spec itself
leaves undefined: discover-vs-invoke authorization (one gate, section 2.2)
and registration conflict detection ((serviceName, producerId) uniqueness,
section 2.3).
"""

import uuid

import httpx
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error

from .models import EVENT_TYPES, ServiceAuthzPolicy, ServiceEventSubscription, ServiceProfile

app = FastAPI(title="SME — Service Management and Exposure")


class ServiceRegistration(BaseModel):
    serviceName: str
    producerId: str
    endpoint: str
    version: str
    fullApiVersions: list[str] = []
    serviceCapabilities: dict = {}
    selectionCriteria: dict = {}
    moduleScope: str
    allowedConsumers: list[str] = []


class EventSubscriptionRequest(BaseModel):
    subscriberId: str
    eventTypes: list[str]
    callbackUri: str


@app.post("/published-apis/v1/{apf_id}/service-apis", status_code=201)
def register_service(apf_id: str, body: ServiceRegistration, db: Session = Depends(get_session)):
    """RegisterService. apfId == producerId == rAppId (Foundational Platform
    LLD section 1) — identity.py's equivalence, enforced here.

    Section 2.3's conflict rule realized correctly: serviceName is globally
    unique. The SAME producer re-registering the same name updates in
    place (idempotent); a DIFFERENT producer registering that name is
    SERVICE_NAME_CONFLICT.
    """
    existing = db.scalar(select(ServiceProfile).where(ServiceProfile.service_name == body.serviceName))
    if existing is not None and existing.producer_id != apf_id:
        raise framework_error(FrameworkError.SERVICE_NAME_CONFLICT, detail=f"{body.serviceName} already registered by a different producer")

    if existing is not None:
        profile = existing
        profile.endpoint, profile.version = body.endpoint, body.version
        profile.full_api_versions, profile.service_capabilities = body.fullApiVersions, body.serviceCapabilities
        profile.selection_criteria, profile.module_scope = body.selectionCriteria, body.moduleScope
    else:
        profile = ServiceProfile(
            service_name=body.serviceName, producer_id=apf_id, endpoint=body.endpoint, version=body.version,
            full_api_versions=body.fullApiVersions, service_capabilities=body.serviceCapabilities,
            selection_criteria=body.selectionCriteria, module_scope=body.moduleScope,
        )
        db.add(profile)
    db.flush()

    if profile.authz_policy is None:
        db.add(ServiceAuthzPolicy(service_id=profile.service_id, allowed_consumers=body.allowedConsumers))
    else:
        profile.authz_policy.allowed_consumers = body.allowedConsumers
    db.commit()
    notify_service_change(db, profile, "SERVICE_API_UPDATE" if existing is not None else "SERVICE_API_AVAILABLE")
    return {"serviceId": str(profile.service_id)}


@app.delete("/published-apis/v1/{apf_id}/service-apis/{service_id}", status_code=204)
def deregister_service(apf_id: str, service_id: uuid.UUID, db: Session = Depends(get_session)):
    profile = db.get(ServiceProfile, service_id)
    if profile is not None and profile.producer_id == apf_id:
        notify_service_change(db, profile, "SERVICE_API_UNAVAILABLE")  # before delete: notify_service_change needs the still-live row
        db.delete(profile)
        db.commit()


@app.get("/published-apis/v1/{apf_id}/service-apis")
def query_own_services(apf_id: str, db: Session = Depends(get_session)):
    rows = db.scalars(select(ServiceProfile).where(ServiceProfile.producer_id == apf_id)).all()
    return [_service_view(r) for r in rows]


@app.get("/service-apis/v1/allServiceAPIs")
def discover_services(api_invoker_id: str, api_name: str | None = None, api_version: str | None = None, db: Session = Depends(get_session)):
    """DiscoverServices. Section 2.2's decision: one gate — an unauthorized
    consumer's query simply never returns the service, it is never told
    the service exists.
    """
    stmt = select(ServiceProfile)
    if api_name:
        stmt = stmt.where(ServiceProfile.service_name == api_name)
    if api_version:
        stmt = stmt.where(ServiceProfile.version == api_version)
    rows = db.scalars(stmt).all()

    visible = []
    for r in rows:
        policy = r.authz_policy
        if policy is None or not policy.gates_discovery_visibility:
            visible.append(r)
        elif not policy.allowed_consumers or api_invoker_id in policy.allowed_consumers:
            visible.append(r)
    return [_service_view(r) for r in visible]


@app.post("/capif-events/v1/{subscriber_id}/subscriptions", status_code=201)
def subscribe_events(subscriber_id: str, body: EventSubscriptionRequest, db: Session = Depends(get_session)):
    if not set(body.eventTypes) <= EVENT_TYPES:
        raise framework_error(FrameworkError.SUBSCRIPTION_SCOPE_CONFLICT, detail=f"eventTypes must be a subset of {EVENT_TYPES}")
    sub = ServiceEventSubscription(subscriber_id=subscriber_id, event_types=body.eventTypes, callback_uri=body.callbackUri)
    db.add(sub)
    db.commit()
    return {"subscriptionId": str(sub.subscription_id)}


@app.delete("/capif-events/v1/{subscriber_id}/subscriptions/{subscription_id}", status_code=204)
def unsubscribe_events(subscriber_id: str, subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    sub = db.get(ServiceEventSubscription, subscription_id)
    if sub is not None and sub.subscriber_id == subscriber_id:
        db.delete(sub)
        db.commit()


def notify_service_change(db: Session, service: ServiceProfile, event_type: str) -> None:
    """Producer-initiated push (OPEN_ITEMS.md section 5): now wired in from
    register_service (SERVICE_API_AVAILABLE on create, SERVICE_API_UPDATE
    on the idempotent re-registration path) and deregister_service
    (SERVICE_API_UNAVAILABLE). Delivered to every subscriber whose
    eventTypes includes event_type AND who is authorized to see the
    service, per discover_services' own gate (section 2.2) — the
    docstring already claimed this authz check but the code never
    enforced it until now.
    """
    subs = db.scalars(select(ServiceEventSubscription)).all()
    policy = service.authz_policy
    for sub in subs:
        if event_type not in sub.event_types:
            continue
        if policy is not None and policy.gates_discovery_visibility and policy.allowed_consumers and sub.subscriber_id not in policy.allowed_consumers:
            continue
        try:
            httpx.post(sub.callback_uri, json={"serviceId": str(service.service_id), "eventType": event_type}, timeout=5.0)
        except httpx.HTTPError:
            pass  # Phase 1: best-effort; no retry/backoff queue yet


def _service_view(r: ServiceProfile) -> dict:
    return {
        "serviceId": str(r.service_id),
        "serviceName": r.service_name,
        "producerId": r.producer_id,
        "endpoint": r.endpoint,
        "version": r.version,
        "fullApiVersions": r.full_api_versions or [],
        "serviceCapabilities": r.service_capabilities or {},
    }
