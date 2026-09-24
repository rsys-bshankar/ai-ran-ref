"""SME (Service Management and Exposure).

R1AP clause 6, profile of 3GPP TS 29.222 (CAPIF). Most mature R1 service
group — all four APIs stable, no alpha tags (Foundational Platform LLD
section 2.6). Real logic here for the two ambiguities the spec itself
leaves undefined: discover-vs-invoke authorization (one gate, section 2.2)
and registration conflict detection ((serviceName, producerId) uniqueness,
section 2.3).
"""

import datetime
import secrets
import uuid

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.timeutil import as_utc

from .models import EVENT_TYPES, InvokerRegistration, IssuedAccessToken, ProviderRegistration, ServiceAuthzPolicy, ServiceEventSubscription, ServiceProfile

ACCESS_TOKEN_TTL_SECONDS = 3600

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
    aefProfiles: list[dict] = []
    apiSuppFeats: str | None = None
    shareableInfo: dict | None = None


class EventSubscriptionRequest(BaseModel):
    subscriberId: str
    eventTypes: list[str]
    callbackUri: str
    apiIds: list[str] | None = None


class ProviderRegistrationRequest(BaseModel):
    apfId: str
    providerDomainInfo: str | None = None


@app.post("/provider-registrations", status_code=201)
def register_provider(body: ProviderRegistrationRequest, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: Provider (APF) enrolment
    (`PostRegistrations`, `providermanagement.go`) — the real registry
    `register_service`'s own `apf_id` check needs, entirely absent
    before this pass. Idempotent update-in-place on a re-registration of
    the same `apfId`, the same shape `register_service`'s own
    same-producer re-registration already uses — the reference's real
    domain/function-id collision detection doesn't apply here, since
    this build has no separate provider-domain concept to collide on.
    """
    provider = db.get(ProviderRegistration, body.apfId)
    if provider is None:
        provider = ProviderRegistration(apf_id=body.apfId)
        db.add(provider)
    provider.provider_domain_info = body.providerDomainInfo
    db.commit()
    return {"apfId": provider.apf_id}


@app.delete("/provider-registrations/{apf_id}", status_code=204)
def deregister_provider(apf_id: str, db: Session = Depends(get_session)):
    """DeleteRegistrationsRegistrationId, providermanagement.go — idempotent,
    matching the reference's own delete-if-present-else-still-204 shape.
    """
    provider = db.get(ProviderRegistration, apf_id)
    if provider is not None:
        db.delete(provider)
        db.commit()


class InvokerRegistrationRequest(BaseModel):
    apiInvokerId: str
    onboardingSecret: str


@app.post("/invoker-registrations", status_code=201)
def register_invoker(body: InvokerRegistrationRequest, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 2: API Invoker onboarding
    (`invokermanagement.go`'s `InvokerManager`) — the real registry the
    Security/token API's own `IsInvokerRegistered`/`VerifyInvokerSecret`
    gate needs, entirely absent before this pass. Idempotent
    update-in-place on a re-registration of the same `apiInvokerId`, the
    same shape `register_provider`/`register_service` already use.
    """
    inv = db.get(InvokerRegistration, body.apiInvokerId)
    if inv is None:
        inv = InvokerRegistration(api_invoker_id=body.apiInvokerId)
        db.add(inv)
    inv.onboarding_secret = body.onboardingSecret
    db.commit()
    return {"apiInvokerId": inv.api_invoker_id}


class AccessTokenRequest(BaseModel):
    grant_type: str
    client_id: str
    client_secret: str | None = None
    scope: str | None = None


@app.post("/oauth2/token")
def issue_access_token(body: AccessTokenRequest, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 2: "no actual validation code path" for
    R1 Termination's advertised tokenEndPoint — this is that endpoint,
    finally real. Mirrors `PostSecuritiesSecurityIdToken`
    (`securityservice.go`)'s real request/response shape (`client_id`/
    `client_secret`/`grant_type`/`scope`, `access_token`/`expires_in`/
    `token_type`/`scope`) and its real two checks
    (`IsInvokerRegistered`, `VerifyInvokerSecret`) — 400 on either
    failure, same as the reference. The reference then delegates actual
    JWT signing to an external Keycloak instance; this build has no real
    IdP, so it issues its own opaque, server-tracked token instead (see
    `IssuedAccessToken`). Per-scope AEF/API validation
    (`IsFunctionRegistered`/`IsAPIPublished`) stays out of scope — this
    build elides fine-grained AuthZ throughout (e.g. `create_policy`'s
    own docstring), so `scope` is accepted and echoed back, never
    checked against what's actually published.
    """
    if body.grant_type != "client_credentials":
        return JSONResponse(status_code=400, content={"error": "unsupported_grant_type"})
    inv = db.get(InvokerRegistration, body.client_id)
    if inv is None:
        return JSONResponse(status_code=400, content={"error": "invalid_client", "error_description": "invoker not registered"})
    if not secrets.compare_digest(body.client_secret or "", inv.onboarding_secret):
        return JSONResponse(status_code=400, content={"error": "unauthorized_client", "error_description": "onboarding secret not valid"})
    token = secrets.token_urlsafe(32)
    expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS)
    db.add(IssuedAccessToken(access_token=token, api_invoker_id=inv.api_invoker_id, expires_at=expires_at))
    db.commit()
    return {"access_token": token, "expires_in": ACCESS_TOKEN_TTL_SECONDS, "token_type": "Bearer", "scope": body.scope}


class IntrospectRequest(BaseModel):
    token: str


@app.post("/oauth2/introspect")
def introspect_token(body: IntrospectRequest, db: Session = Depends(get_session)):
    """RFC 7662 — the honest substitute for the reference's own
    self-contained signed-JWT validation (see issue_access_token's own
    docstring for why this build issues opaque tokens instead). R1
    Termination calls this on every proxied request to decide whether to
    forward it; an unauthenticated internal call, matching the same
    network-isolation reasoning /bootstrap's own docstring already gives
    for staying unauthenticated itself (SME<->R1 Termination traffic
    never leaves the docker-compose network).
    """
    rec = db.get(IssuedAccessToken, body.token)
    if rec is None or as_utc(rec.expires_at) <= datetime.datetime.now(datetime.UTC):
        return {"active": False}
    return {"active": True, "client_id": rec.api_invoker_id, "exp": int(as_utc(rec.expires_at).timestamp())}


@app.post("/published-apis/v1/{apf_id}/service-apis", status_code=201)
def register_service(apf_id: str, body: ServiceRegistration, db: Session = Depends(get_session)):
    """RegisterService. apfId == producerId == rAppId (Foundational Platform
    LLD section 1) — identity.py's equivalence, enforced here.

    Section 2.3's conflict rule realized correctly: serviceName is globally
    unique. The SAME producer re-registering the same name updates in
    place (idempotent); a DIFFERENT producer registering that name is
    SERVICE_NAME_CONFLICT.

    OPEN_ITEMS.md section 5: previously accepted any apf_id with no check
    that it's an actual registered publisher. The reference's own gate
    (`PostApfIdServiceApis`, `publishservice.go`:
    `serviceRegister.IsPublishingFunctionRegistered(apfId)`, 403
    otherwise) is now real, backed by ProviderRegistration —
    register_provider above.
    """
    if db.get(ProviderRegistration, apf_id) is None:
        raise framework_error(FrameworkError.APF_NOT_REGISTERED, detail=f"{apf_id} is not a registered publishing function")

    existing = db.scalar(select(ServiceProfile).where(ServiceProfile.service_name == body.serviceName))
    if existing is not None and existing.producer_id != apf_id:
        raise framework_error(FrameworkError.SERVICE_NAME_CONFLICT, detail=f"{body.serviceName} already registered by a different producer")

    if existing is not None:
        profile = existing
        profile.endpoint, profile.version = body.endpoint, body.version
        profile.full_api_versions, profile.service_capabilities = body.fullApiVersions, body.serviceCapabilities
        profile.selection_criteria, profile.module_scope = body.selectionCriteria, body.moduleScope
        profile.aef_profiles, profile.api_supp_feats, profile.shareable_info = body.aefProfiles, body.apiSuppFeats, body.shareableInfo
    else:
        profile = ServiceProfile(
            service_name=body.serviceName, producer_id=apf_id, endpoint=body.endpoint, version=body.version,
            full_api_versions=body.fullApiVersions, service_capabilities=body.serviceCapabilities,
            selection_criteria=body.selectionCriteria, module_scope=body.moduleScope,
            aef_profiles=body.aefProfiles, api_supp_feats=body.apiSuppFeats, shareable_info=body.shareableInfo,
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
    """GetApfIdServiceApis, publishservice.go — mirrors the reference's own
    branch exactly: an apf_id with existing services always gets them back
    (a real producer's own row set wins regardless of its current
    enrolment state — the reference's own map-lookup-first shape), and
    only an apf_id with zero services falls back to the same
    IsPublishingFunctionRegistered gate register_service now uses, to
    distinguish "a real publisher with nothing registered yet" (empty
    list) from "not a publisher at all" (404).
    """
    rows = db.scalars(select(ServiceProfile).where(ServiceProfile.producer_id == apf_id)).all()
    if not rows and db.get(ProviderRegistration, apf_id) is None:
        raise HTTPException(status_code=404, detail=f"{apf_id} is not a registered publishing function")
    return [_service_view(r) for r in rows]


@app.get("/service-apis/v1/allServiceAPIs")
def discover_services(api_invoker_id: str, api_name: str | None = None, api_version: str | None = None,
                       aef_id: str | None = None, protocol: str | None = None, data_format: str | None = None,
                       comm_type: str | None = None, db: Session = Depends(get_session)):
    """DiscoverServices. Section 2.2's decision: one gate — an unauthorized
    consumer's query simply never returns the service, it is never told
    the service exists.

    OPEN_ITEMS.md section 5: discover_services only filtered on
    api_name/api_version — the reference (discoverservice.go's
    matchesFilter/checkAefId/checkProtocol/checkDataFormat/
    checkVersionAndCommType) also filters against each service's
    AefProfiles. aefId/protocol/dataFormat/commType are now real
    filters over ServiceProfile.aef_profiles (walked in Python, since
    it's a JSON blob here rather than the reference's relational
    AefProfile/Version/Resource join — same adaptation as the field's
    own storage). apiCat isn't — this build's ServiceProfile has no
    category concept at all, a gap ServiceProfile's own flattening note
    already covers.
    """
    stmt = select(ServiceProfile)
    if api_name:
        stmt = stmt.where(ServiceProfile.service_name == api_name)
    if api_version:
        stmt = stmt.where(ServiceProfile.version == api_version)
    rows = db.scalars(stmt).all()

    visible = []
    for r in rows:
        if not _matches_aef_filters(r, aef_id, protocol, data_format, comm_type):
            continue
        policy = r.authz_policy
        if policy is None or not policy.gates_discovery_visibility:
            visible.append(r)
        elif not policy.allowed_consumers or api_invoker_id in policy.allowed_consumers:
            visible.append(r)
    return [_service_view(r) for r in visible]


def _matches_aef_filters(r: ServiceProfile, aef_id: str | None, protocol: str | None, data_format: str | None, comm_type: str | None) -> bool:
    if not any((aef_id, protocol, data_format, comm_type)):
        return True
    for profile in r.aef_profiles or []:
        if aef_id and profile.get("aefId") != aef_id:
            continue
        if protocol and profile.get("protocol") != protocol:
            continue
        if data_format and profile.get("dataFormat") != data_format:
            continue
        if comm_type:
            resources = (res for v in profile.get("versions", []) for res in v.get("resources", []))
            if not any(res.get("commType") == comm_type for res in resources):
                continue
        return True
    return False


@app.post("/capif-events/v1/{subscriber_id}/subscriptions", status_code=201)
def subscribe_events(subscriber_id: str, body: EventSubscriptionRequest, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: event subscription filtering was type-only
    — the reference's own CAPIFEventFilter also filters by apiId/
    apiInvokerId/aefId (eventservice.go's getMatchingSubs). Of those,
    only apiId is meaningfully implementable here: apiInvokerId filters
    against an event's own ApiInvokerIds, which only API_INVOKER_ONBOARDED
    events carry — a real CAPIF Invoker-onboarding subsystem this build
    doesn't have (see this module's own structurally-out-of-scope note);
    our SERVICE_API_* notifications have no invoker id in their payload
    to filter on. aefId likewise needs aefProfiles, which ServiceProfile
    doesn't model (the separate "flattened ServiceProfile" gap). apiId
    maps directly onto this build's own service_id, so that one's real.
    """
    if not set(body.eventTypes) <= EVENT_TYPES:
        raise framework_error(FrameworkError.SUBSCRIPTION_SCOPE_CONFLICT, detail=f"eventTypes must be a subset of {EVENT_TYPES}")
    sub = ServiceEventSubscription(subscriber_id=subscriber_id, event_types=body.eventTypes, callback_uri=body.callbackUri, api_ids=body.apiIds)
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
        if sub.api_ids and str(service.service_id) not in sub.api_ids:
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
        "aefProfiles": r.aef_profiles or [],
        "apiSuppFeats": r.api_supp_feats,
        "shareableInfo": r.shareable_info,
    }
