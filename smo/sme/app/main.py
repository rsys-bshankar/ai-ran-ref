"""SME (Service Management and Exposure).

R1AP clause 6, profile of 3GPP TS 29.222 (CAPIF). Most mature R1 service
group — all four APIs stable, no alpha tags (Foundational Platform LLD
section 2.6). Real logic here for the two ambiguities the spec itself
leaves undefined: discover-vs-invoke authorization (one gate, section 2.2)
and registration conflict detection ((serviceName, producerId) uniqueness,
section 2.3).
"""

import datetime
import hashlib
import logging
import os
import secrets
import uuid
from typing import Literal

import httpx
import jwt
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from smo_shared.logconfig import install_logging
from smo_shared.metrics import install_metrics
from smo_shared.health import database_check, install_health
from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.timeutil import as_utc
from smo_shared.pagination import PageLimit, PageOffset, paginate
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.outbox import enqueue
from smo_shared import roles
from smo_shared.secretfile import read_secret

from .models import (API_INVOKER_EVENTS, EVENT_TYPES, InvokerRegistration, IssuedAccessToken, ProviderRegistration,
                     ServiceAuthzPolicy, ServiceEventSubscription, ServiceProfile, TrustedInvoker, UsedClientAssertion)

log = logging.getLogger(__name__)

ACCESS_TOKEN_TTL_SECONDS = 3600
# OI-2-oauth2-scope: the scopes SMO's own clients request — every module's
# R1Client (smo_shared/r1_client.py) and the GUI BFF. Granted as-is; they
# name no published API, so there is nothing to check them against.
INTERNAL_SCOPES = roles.INTERNAL_SCOPES | {roles.RAPP_SCOPE}      # PR-SEC-14: the internal two need an enrolled invoker (`_check_scope`); `smo-rapp` is for anyone
CAPIF_SCOPE_PREFIX = "3gpp#"
# SA-SME-1-public-key: RFC 7523 client authentication by a signed JWT.
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
# The assertion's `aud` must name this token endpoint, as R1 Termination
# advertises it (`{SME_URL}/oauth2/token`, r1-termination/app/main.py).
TOKEN_ENDPOINT_AUDIENCE = os.environ.get("SME_TOKEN_AUDIENCE") or f"{os.environ.get('SME_URL', 'http://sme:8000')}/oauth2/token"   # empty counts as unset (compose passes "")
ASSERTION_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "ES256", "ES384", "EdDSA"]
MAX_ASSERTION_LIFETIME_SECONDS = 300
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _SCRYPT_DKLEN = 2**14, 8, 1, 32

app = FastAPI(title="SME — Service Management and Exposure")
install_logging(app)  # structured JSON logs and one access-log line per request (PR-OBS-1)
install_metrics(app)  # /metrics and request count/latency series (PR-OBS-2)
# SME issues and introspects the R1 bearer token itself (r1-termination's
# own _authorized() calls /oauth2/introspect on every proxied request) —
# these two routes are the one exemption, the same way a token endpoint is
# never itself gated behind the token it hands out.
apply_r1_gateway_security(app, public_paths=frozenset({"/oauth2/token", "/oauth2/introspect"}))
apply_correlation_id(app)


install_health(app, checks=[database_check])  # /live, /ready and the /health alias (PR-ST-7)


def _hash_secret(secret: str) -> str:
    """Security review: an invoker's onboarding_secret is a real, checked
    credential — never stored in cleartext, or a DB leak (backup, SQL
    injection elsewhere, a dump) would hand out reusable client
    credentials directly. Salted scrypt (stdlib hashlib, no new
    dependency), stored as "salt_hex:digest_hex" — there's no separate
    salt column, the salt itself isn't secret, only storing the raw
    secret would be.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(secret.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)
    return f"{salt.hex()}:{digest.hex()}"


def _verify_secret(secret: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split(":")
    candidate = hashlib.scrypt(secret.encode(), salt=bytes.fromhex(salt_hex), n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)
    return secrets.compare_digest(candidate, bytes.fromhex(digest_hex))


def _hash_token(token: str) -> str:
    """Unlike onboarding_secret, the token is already 256 bits of real
    randomness from secrets.token_urlsafe, not a low-entropy human-chosen
    secret — a fast SHA-256 hash (not a slow KDF) is the correct,
    standard choice here. The raw token is returned to the caller once
    at issuance and never stored; only this hash is.
    """
    return hashlib.sha256(token.encode()).hexdigest()


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
    apiInvokerIds: list[str] | None = None  # OI-5-sme-filters
    aefIds: list[str] | None = None


class ProviderRegistrationRequest(BaseModel):
    apfId: str
    providerDomainInfo: str | None = None


@app.post("/provider-registrations", status_code=201)
def register_provider(body: ProviderRegistrationRequest, db: Session = Depends(get_session)):
    """HISTORY.md §5: Provider (APF) enrolment
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
    apiInvokerPublicKey: str


def _pem_public_key(value: str):
    """The invoker's key, if `apiInvokerPublicKey` is a PEM public key;
    None for an opaque label (SMO's own clients onboard with one)."""
    if not value.lstrip().startswith("-----BEGIN"):
        return None
    try:
        return load_pem_public_key(value.encode())
    except (ValueError, TypeError):
        return None


def _enrolled(presented: str | None) -> str:
    """PR-SEC-14: the kind of a new invoker. `internal` when the caller presents the enrollment secret every SMO module mounts, `rapp` when it
    presents none. A secret that does not match is refused (403), never quietly downgraded: a module with the wrong secret is a misconfiguration
    to see. With no secret configured SME will not tell the two apart: 503, unless `SME_ALLOW_OPEN_ENROLLMENT` is set (development and tests),
    which makes every invoker `internal`, as before this existed."""
    expected = read_secret("SMO_ENROLLMENT_SECRET")
    if not expected:
        if os.environ.get("SME_ALLOW_OPEN_ENROLLMENT", "").strip().lower() in ("1", "true", "yes", "on"):
            return roles.ROLE_INTERNAL
        raise framework_error(FrameworkError.ENROLLMENT_NOT_CONFIGURED,
                              detail="SME has no enrollment secret (SMO_ENROLLMENT_SECRET[_FILE]): it cannot tell an SMO module from an rApp")
    if presented is None:
        return roles.ROLE_RAPP
    if not roles.enrollment_secret_valid(presented, expected):
        raise framework_error(FrameworkError.ENROLLMENT_REFUSED, detail="the enrollment secret presented is not the one SME holds")
    return roles.ROLE_INTERNAL


def _check_public_key(value: str) -> None:
    if value.lstrip().startswith("-----BEGIN") and _pem_public_key(value) is None:
        raise framework_error(FrameworkError.SECURITY_CONTEXT_INVALID, detail="apiInvokerPublicKey is not a valid PEM public key")


@app.post("/invoker-registrations", status_code=201)
def register_invoker(body: InvokerRegistrationRequest, db: Session = Depends(get_session),
                     enrollment: str | None = Header(default=None, alias=roles.ENROLLMENT_HEADER)):
    """HISTORY.md §2: API Invoker onboarding
    (`invokermanagement.go`'s `InvokerManager`) — the real registry the
    Security/token API's own `IsInvokerRegistered`/`VerifyInvokerSecret`
    gate needs, entirely absent before this pass.

    HISTORY.md §7 SME item 1: the real CAPIF onboarding flow is
    public-key-based — the client supplies `apiInvokerPublicKey`; the
    server *generates* both `apiInvokerId` and `onboardingSecret` and
    returns them (`apiInvokerId` "shall not be present" in the real
    client request at all). Every call mints a new invoker (the reference
    always creates, never updates, on onboarding).

    SA-SME-1-public-key: a PEM public key is parsed here (422 if it is
    malformed) and later verifies the invoker's signed token requests;
    any other value is kept as an opaque label. OI-5-sme-filters: emits
    API_INVOKER_ONBOARDED.
    """
    _check_public_key(body.apiInvokerPublicKey)
    kind = _enrolled(enrollment)                                  # PR-SEC-14: `X-SMO-Enrollment` makes the invoker an SMO module's
    api_invoker_id = f"api-invoker-{uuid.uuid4()}"
    onboarding_secret = secrets.token_urlsafe(32)
    inv = InvokerRegistration(api_invoker_id=api_invoker_id, public_key=body.apiInvokerPublicKey,
                               onboarding_secret_hash=_hash_secret(onboarding_secret), kind=kind)
    db.add(inv)
    notify_invoker_change(db, api_invoker_id, "API_INVOKER_ONBOARDED")  # enqueued in this transaction (PR-MSG-1.6)
    db.commit()
    return {"apiInvokerId": inv.api_invoker_id, "onboardingSecret": onboarding_secret, "role": kind,
            "keyAuthentication": _pem_public_key(inv.public_key) is not None}


@app.put("/invoker-registrations/{api_invoker_id}")
def update_invoker(api_invoker_id: str, body: InvokerRegistrationRequest, db: Session = Depends(get_session)):
    """PutOnboardedInvokersApiInvokerId — replaces the invoker's public key
    (key rotation). Assertions signed with the old key stop verifying at
    once. 404 for an unknown invoker. Emits API_INVOKER_UPDATED."""
    inv = db.get(InvokerRegistration, api_invoker_id)
    if inv is None:
        raise framework_error(FrameworkError.INVOKER_NOT_REGISTERED, detail=f"invoker {api_invoker_id} not registered")
    _check_public_key(body.apiInvokerPublicKey)
    inv.public_key = body.apiInvokerPublicKey
    notify_invoker_change(db, api_invoker_id, "API_INVOKER_UPDATED")
    db.commit()
    return {"apiInvokerId": inv.api_invoker_id, "keyAuthentication": _pem_public_key(inv.public_key) is not None}


@app.delete("/invoker-registrations/{api_invoker_id}", status_code=204)
def offboard_invoker(api_invoker_id: str, db: Session = Depends(get_session)):
    """DeleteOnboardedInvokersApiInvokerId — offboarding. The invoker's
    live tokens and trusted-invoker security context go with it, so
    nothing it was granted outlives it. Idempotent (204 for an unknown id).
    Emits API_INVOKER_OFFBOARDED."""
    inv = db.get(InvokerRegistration, api_invoker_id)
    if inv is None:
        return
    _offboard(db, inv)


def _offboard(db: Session, inv: InvokerRegistration) -> None:
    """Remove an invoker with everything it was granted, and tell subscribers."""
    api_invoker_id = inv.api_invoker_id
    db.query(IssuedAccessToken).filter(IssuedAccessToken.api_invoker_id == api_invoker_id).delete()
    trusted = db.get(TrustedInvoker, api_invoker_id)
    if trusted is not None:
        db.delete(trusted)
    db.delete(inv)
    notify_invoker_change(db, api_invoker_id, "API_INVOKER_OFFBOARDED")
    db.commit()


@app.post("/invoker-registrations/purge-stale")
def purge_stale_invokers(unused_for_days: int = Query(gt=0, le=36500), dry_run: bool = True, db: Session = Depends(get_session)):
    """PR-ST-4 housekeeping (this build's own addition; CAPIF has no such operation). Offboards every invoker
    that has obtained no token for `unused_for_days` days (or, if it never did, was onboarded that long ago):
    the leftovers of processes that registered an identity of their own and were replaced. A module that
    comes back later and finds its identity gone is onboarded afresh by `R1Client`. `dry_run` defaults to
    true: it lists what would go and deletes nothing."""
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=unused_for_days)
    stale = list(db.scalars(select(InvokerRegistration).where(
        func.coalesce(InvokerRegistration.last_token_issued_at, InvokerRegistration.created_at) < cutoff)))
    ids = sorted(inv.api_invoker_id for inv in stale)
    if not dry_run:
        for inv in stale:
            _offboard(db, inv)
    return {"unusedForDays": unused_for_days, "dryRun": dry_run, "count": len(ids), "invokerIds": ids}


class AccessTokenRequest(BaseModel):
    grant_type: str
    client_id: str
    client_secret: str | None = None
    scope: str | None = None
    # SA-SME-1-public-key: RFC 7523 section 2.2 client authentication
    client_assertion_type: str | None = None
    client_assertion: str | None = None


def _token_error(error: str, description: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": error, "error_description": description})


REQUIRED_ASSERTION_CLAIMS = ("iss", "sub", "aud", "exp", "jti")


def _assertion_failure(exc: jwt.PyJWTError) -> str:
    if isinstance(exc, jwt.InvalidSignatureError):
        return "Signature verification failed"
    if isinstance(exc, jwt.ExpiredSignatureError):
        return "Signature has expired"
    if isinstance(exc, jwt.InvalidAudienceError):
        return "Audience does not name this token endpoint"
    if isinstance(exc, jwt.MissingRequiredClaimError):
        missing = next((claim for claim in REQUIRED_ASSERTION_CLAIMS if claim == exc.claim), "a required")
        return f"Token is missing the {missing} claim"
    if isinstance(exc, jwt.InvalidAlgorithmError):
        return "The specified alg value is not allowed"
    return "malformed or unverifiable JWT"


def _verify_client_assertion(db: Session, inv: InvokerRegistration, body: AccessTokenRequest) -> str | None:
    """RFC 7523 `private_key_jwt`: None when the assertion authenticates the
    invoker, else why not. The JWT must be signed with the invoker's
    onboarded public key, name the invoker as both `iss` and `sub`, name
    this token endpoint as `aud`, expire within MAX_ASSERTION_LIFETIME_SECONDS,
    and carry a `jti` never used before — it is recorded here so the same
    assertion can't buy a second token."""
    if body.client_assertion_type != CLIENT_ASSERTION_TYPE:
        return f"client_assertion_type must be {CLIENT_ASSERTION_TYPE}"
    key = _pem_public_key(inv.public_key)
    if key is None:
        return "invoker has no PEM public key to verify an assertion with"
    try:
        claims = jwt.decode(body.client_assertion, key=key, algorithms=ASSERTION_ALGORITHMS,
                            audience=TOKEN_ENDPOINT_AUDIENCE, options={"require": list(REQUIRED_ASSERTION_CLAIMS)})
    except jwt.PyJWTError as exc:
        # A fixed message per failure kind, never the library's own text
        # (CodeQL py/stack-trace-exposure)
        return f"client assertion not valid: {_assertion_failure(exc)}"
    if claims["iss"] != inv.api_invoker_id or claims["sub"] != inv.api_invoker_id:
        return "client assertion iss and sub must both be the client_id"
    now = datetime.datetime.now(datetime.UTC)
    expires_at = datetime.datetime.fromtimestamp(claims["exp"], datetime.UTC)
    if (expires_at - now).total_seconds() > MAX_ASSERTION_LIFETIME_SECONDS:
        return f"client assertion must expire within {MAX_ASSERTION_LIFETIME_SECONDS} s"
    db.query(UsedClientAssertion).filter(UsedClientAssertion.expires_at <= now).delete()
    if db.get(UsedClientAssertion, str(claims["jti"])) is not None:
        return "client assertion jti already used"
    db.add(UsedClientAssertion(jti=str(claims["jti"]), api_invoker_id=inv.api_invoker_id, expires_at=expires_at))
    return None


def _visible_to(service: ServiceProfile, api_invoker_id: str) -> bool:
    """discover_services' gate (section 2.2): may this invoker see it?"""
    policy = service.authz_policy
    return (policy is None or not policy.gates_discovery_visibility or not policy.allowed_consumers
            or api_invoker_id in policy.allowed_consumers)


def _check_scope(db: Session, scope: str | None, api_invoker_id: str, kind: str = roles.ROLE_INTERNAL) -> str | None:
    """OI-2-oauth2-scope: None when the requested scope may be granted,
    else why not. Absent, or one of INTERNAL_SCOPES: granted. Otherwise it
    must be TS 29.222's `3gpp#aefId:apiName[,apiName...][;aefId:...]`, and
    every apiName must be a published service that is exposed by that
    aefId (one of its aefProfiles) and visible to this invoker — the same
    checks as the reference's `IsFunctionRegistered`/`IsAPIPublished`,
    plus discovery's visibility gate, so a token never names an API the
    invoker could not discover."""
    if scope in roles.INTERNAL_SCOPES and kind != roles.ROLE_INTERNAL:
        # PR-SEC-14: an rApp does not get the scopes the SMO's own clients use. In audit mode it is granted and counted.
        if roles.enforcement_mode() == "enforce":
            return f"scope {scope!r} is for SMO modules: this invoker did not enroll"
        log.warning("role audit: rApp invoker %s was granted internal scope %r", api_invoker_id, scope)
        return None
    if not scope or scope in INTERNAL_SCOPES:
        return None
    if not scope.startswith(CAPIF_SCOPE_PREFIX):
        return f"scope must be {sorted(INTERNAL_SCOPES)} or {CAPIF_SCOPE_PREFIX}aefId:apiName[,apiName][;aefId:apiName]"
    for part in scope[len(CAPIF_SCOPE_PREFIX):].split(";"):
        aef_id, sep, api_names = part.partition(":")
        names = [n for n in api_names.split(",") if n]
        if not sep or not aef_id or not names:
            return f"malformed scope entry {part!r}, expected aefId:apiName[,apiName]"
        for name in names:
            service = db.scalar(select(ServiceProfile).where(ServiceProfile.service_name == name))
            if service is None or not _visible_to(service, api_invoker_id):
                return f"API {name!r} is not published"
            if aef_id not in _aef_ids(service):
                return f"API {name!r} is not exposed by AEF {aef_id!r}"
    return None


class OAuthError(BaseModel):
    """RFC 6749 section 5.2: what the token and introspection endpoints answer when they refuse, not the ProblemDetails of the other routes."""
    error: str
    error_description: str | None = None


class UnparsableBody(BaseModel):
    """What the framework answers (400) before the route runs, when the body is not JSON."""
    detail: str


@app.post("/oauth2/token", responses={400: {"model": OAuthError | UnparsableBody, "description": "RFC 6749 section 5.2 error"}, 401: {"model": OAuthError | UnparsableBody, "description": "RFC 6749 section 5.2 error"}})
def issue_access_token(body: AccessTokenRequest, db: Session = Depends(get_session)):
    """HISTORY.md §2: "no actual validation code path" for
    R1 Termination's advertised tokenEndPoint — this is that endpoint,
    finally real. Mirrors `PostSecuritiesSecurityIdToken`
    (`securityservice.go`)'s real request/response shape (`client_id`/
    `client_secret`/`grant_type`/`scope`, `access_token`/`expires_in`/
    `token_type`/`scope`) and its real checks — 400 on any failure, same
    as the reference. The reference then delegates actual JWT signing to an
    external Keycloak instance; this build has no real IdP, so it issues its
    own opaque, server-tracked token instead (see `IssuedAccessToken`).

    The client authenticates with exactly one of its onboarding secret
    (`client_secret`) or an RFC 7523 client assertion signed with its
    onboarded public key (`client_assertion`, SA-SME-1-public-key).
    `scope` is checked before anything is issued (`_check_scope`,
    OI-2-oauth2-scope): 400 `invalid_scope` names the first API that is
    not published, not exposed by the named AEF, or not visible to the
    invoker.
    """
    if body.grant_type != "client_credentials":
        return JSONResponse(status_code=400, content={"error": "unsupported_grant_type"})
    if body.client_secret is not None and body.client_assertion is not None:
        return _token_error("invalid_request", "use client_secret or client_assertion, not both")
    inv = db.get(InvokerRegistration, body.client_id)
    if inv is None:
        return _token_error("invalid_client", "invoker not registered")
    if body.client_assertion is not None:
        problem = _verify_client_assertion(db, inv, body)
        if problem is not None:
            db.rollback()
            return _token_error("invalid_client", problem)
    elif not _verify_secret(body.client_secret or "", inv.onboarding_secret_hash):
        return _token_error("unauthorized_client", "onboarding secret not valid")
    problem = _check_scope(db, body.scope, inv.api_invoker_id, inv.kind)
    if problem is not None:
        db.rollback()
        return _token_error("invalid_scope", problem)
    token = secrets.token_urlsafe(32)
    expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS)
    db.add(IssuedAccessToken(access_token_hash=_hash_token(token), api_invoker_id=inv.api_invoker_id,
                             expires_at=expires_at, scope=body.scope or None))
    inv.last_token_issued_at = datetime.datetime.now(datetime.UTC)
    db.commit()
    return {"access_token": token, "expires_in": ACCESS_TOKEN_TTL_SECONDS, "token_type": "Bearer", "scope": body.scope}


class IntrospectRequest(BaseModel):
    token: str


@app.post("/oauth2/introspect", responses={400: {"model": OAuthError | UnparsableBody, "description": "RFC 6749 section 5.2 error"}, 401: {"model": OAuthError | UnparsableBody, "description": "RFC 6749 section 5.2 error"}})
def introspect_token(body: IntrospectRequest, db: Session = Depends(get_session)):
    """RFC 7662 — the honest substitute for the reference's own
    self-contained signed-JWT validation (see issue_access_token's own
    docstring for why this build issues opaque tokens instead). R1
    Termination calls this on every proxied request to decide whether to
    forward it; an unauthenticated internal call, matching the same
    network-isolation reasoning /bootstrap's own docstring already gives
    for staying unauthenticated itself (SME<->R1 Termination traffic
    never leaves the docker-compose network). `scope` is the granted scope
    (RFC 7662 section 2.2), for the resource server that enforces it.
    """
    rec = db.get(IssuedAccessToken, _hash_token(body.token))
    if rec is None or as_utc(rec.expires_at) <= datetime.datetime.now(datetime.UTC):
        return {"active": False}
    registration = db.get(InvokerRegistration, rec.api_invoker_id)
    view = {"active": True, "client_id": rec.api_invoker_id, "exp": int(as_utc(rec.expires_at).timestamp()),
            "role": registration.kind if registration is not None else roles.ROLE_RAPP}     # PR-SEC-14: what R1 Termination applies its role policy on
    if rec.scope:
        view["scope"] = rec.scope
    return view


class SecurityInformationRequest(BaseModel):
    aefId: str | None = None
    apiId: str | None = None
    authenticationInfo: str | None = None
    authorizationInfo: str | None = None
    prefSecurityMethods: list[str]


class ServiceSecurityRequest(BaseModel):
    notificationDestination: str
    requestTestNotification: bool = False
    securityInfo: list[SecurityInformationRequest]


def _validate_service_security(body: ServiceSecurityRequest) -> None:
    if not body.notificationDestination.strip():
        raise framework_error(FrameworkError.SECURITY_CONTEXT_INVALID, detail="ServiceSecurity missing required notificationDestination")
    if not body.securityInfo:
        raise framework_error(FrameworkError.SECURITY_CONTEXT_INVALID, detail="ServiceSecurity missing required securityInfo")
    for info in body.securityInfo:
        if not info.prefSecurityMethods:
            raise framework_error(FrameworkError.SECURITY_CONTEXT_INVALID, detail="SecurityInformation missing required prefSecurityMethods")


def _security_info_with_sel_method(security_info: list[SecurityInformationRequest]) -> list[dict]:
    """`typeupdate.go`'s `PrepareNewSecurityContext`: the real CAPIF core
    cross-checks each `securityInfo` entry's `apiId`/`aefId` against a
    real published `ServiceAPIDescription`'s own `AefProfile.
    SecurityMethods` to pick a compatible `selSecurityMethod`, 400ing if
    none exists. This build's own `ServiceProfile.aef_profiles` (SPEC_
    AUDIT.md SME item, closed earlier) never stored a per-AEF security-
    method catalog — there's no real AEF-side capability data here to
    cross-check against, only what the invoker itself declares. Adapted
    honestly: `selSecurityMethod` is the caller's own first declared
    `prefSecurityMethods` entry, not a fabricated AEF-side match.
    """
    return [
        {"aefId": info.aefId, "apiId": info.apiId, "authenticationInfo": info.authenticationInfo,
         "authorizationInfo": info.authorizationInfo, "prefSecurityMethods": info.prefSecurityMethods,
         "selSecurityMethod": info.prefSecurityMethods[0]}
        for info in security_info
    ]


@app.put("/trusted-invokers/{api_invoker_id}", status_code=201)
def register_trusted_invoker(api_invoker_id: str, body: ServiceSecurityRequest, db: Session = Depends(get_session)):
    """PutTrustedInvokersApiInvokerId (`security.go`) — registers (or
    replaces, on a re-PUT) the security context a real AEF would consult
    for this invoker. Gated on the invoker already being onboarded
    (`invokerRegister.IsInvokerRegistered`), same real 400 the reference
    returns otherwise.
    """
    if db.get(InvokerRegistration, api_invoker_id) is None:
        raise framework_error(FrameworkError.INVOKER_NOT_REGISTERED, detail=f"invoker {api_invoker_id} not registered")
    _validate_service_security(body)
    ti = db.get(TrustedInvoker, api_invoker_id)
    if ti is None:
        ti = TrustedInvoker(api_invoker_id=api_invoker_id)
        db.add(ti)
    ti.notification_destination = body.notificationDestination
    ti.request_test_notification = body.requestTestNotification
    ti.security_info = _security_info_with_sel_method(body.securityInfo)
    db.commit()
    return _trusted_invoker_view(ti)


@app.get("/trusted-invokers/{api_invoker_id}")
def get_trusted_invoker(api_invoker_id: str, authentication_info: bool = False, authorization_info: bool = False, db: Session = Depends(get_session)):
    """GetTrustedInvokersApiInvokerId — the real reference redacts
    `authenticationInfo`/`authorizationInfo` to an empty string unless
    the caller explicitly asks for each via its own query params
    (`checkParams`), rather than always returning the raw secrets to
    whoever asks.
    """
    ti = db.get(TrustedInvoker, api_invoker_id)
    if ti is None:
        raise framework_error(FrameworkError.TRUSTED_INVOKER_NOT_FOUND, detail=f"invoker {api_invoker_id} not registered as trusted invoker")
    view = _trusted_invoker_view(ti)
    for info in view["securityInfo"]:
        if not authentication_info:
            info["authenticationInfo"] = ""
        if not authorization_info:
            info["authorizationInfo"] = ""
    return view


@app.delete("/trusted-invokers/{api_invoker_id}", status_code=204)
def deregister_trusted_invoker(api_invoker_id: str, db: Session = Depends(get_session)):
    ti = db.get(TrustedInvoker, api_invoker_id)
    if ti is not None:
        db.delete(ti)
        db.commit()


@app.post("/trusted-invokers/{api_invoker_id}/update")
def update_trusted_invoker(api_invoker_id: str, body: ServiceSecurityRequest, db: Session = Depends(get_session)):
    """PostTrustedInvokersApiInvokerIdUpdate — update-in-place. Unlike
    the PUT above, the reference never re-checks invoker registration
    here, only that a trusted-invoker context already exists.
    """
    ti = db.get(TrustedInvoker, api_invoker_id)
    if ti is None:
        raise framework_error(FrameworkError.TRUSTED_INVOKER_NOT_FOUND, detail=f"invoker {api_invoker_id} not registered as trusted invoker")
    _validate_service_security(body)
    ti.notification_destination = body.notificationDestination
    ti.request_test_notification = body.requestTestNotification
    ti.security_info = _security_info_with_sel_method(body.securityInfo)
    db.commit()
    return _trusted_invoker_view(ti)


class SecurityNotificationRequest(BaseModel):
    aefId: str | None = None
    apiIds: list[str]
    apiInvokerId: str
    cause: Literal["OVERLIMIT_USAGE", "UNEXPECTED_REASON"]


@app.post("/trusted-invokers/{api_invoker_id}/delete", status_code=204)
def revoke_trusted_invoker(api_invoker_id: str, body: SecurityNotificationRequest, db: Session = Depends(get_session)):
    """PostTrustedInvokersApiInvokerIdDelete — revocation, not a full
    delete: only the `securityInfo` entries matching the notified
    `aefId` or one of `apiIds` are removed (`revokeTrustedInvoker`); the
    whole trusted-invoker record is only dropped once no entries remain.
    Implements the reference's own stated filter semantics directly
    (keep entries that don't match) rather than its own Go loop, which
    mutates a slice by a stale index mid-iteration — a real bug in
    `capifcore` itself, not behavior worth reproducing.
    """
    if not body.apiIds:
        raise framework_error(FrameworkError.SECURITY_CONTEXT_INVALID, detail="SecurityNotification missing required apiIds")
    ti = db.get(TrustedInvoker, api_invoker_id)
    if ti is None:
        raise framework_error(FrameworkError.TRUSTED_INVOKER_NOT_FOUND, detail=f"invoker {api_invoker_id} not registered as trusted invoker")
    remaining = [info for info in ti.security_info
                 if info.get("aefId") != body.aefId and info.get("apiId") not in body.apiIds]
    if not remaining:
        db.delete(ti)
    else:
        ti.security_info = remaining
    db.commit()


def _trusted_invoker_view(ti: TrustedInvoker) -> dict:
    return {"apiInvokerId": ti.api_invoker_id, "notificationDestination": ti.notification_destination,
            "requestTestNotification": ti.request_test_notification, "securityInfo": ti.security_info}


@app.post("/published-apis/v1/{apf_id}/service-apis", status_code=201)
def register_service(apf_id: str, body: ServiceRegistration, db: Session = Depends(get_session)):
    """RegisterService. apfId == producerId == rAppId (Foundational Platform
    LLD section 1) — identity.py's equivalence, enforced here.

    Section 2.3's conflict rule realized correctly: serviceName is globally
    unique. The SAME producer re-registering the same name updates in
    place (idempotent); a DIFFERENT producer registering that name is
    SERVICE_NAME_CONFLICT.

    HISTORY.md §5: previously accepted any apf_id with no check
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
    db.flush()
    db.expire(profile, ["authz_policy"])  # the visibility check below reads the policy this request just wrote
    notify_service_change(db, profile, "SERVICE_API_UPDATE" if existing is not None else "SERVICE_API_AVAILABLE")
    db.commit()
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
        raise framework_error(FrameworkError.PUBLISHING_FUNCTION_NOT_FOUND, detail=f"{apf_id} is not a registered publishing function")
    return [_service_view(r) for r in rows]


@app.get("/service-apis/v1/allServiceAPIs")
def discover_services(api_invoker_id: str, api_name: str | None = None, api_version: str | None = None,
                       aef_id: str | None = None, protocol: str | None = None, data_format: str | None = None,
                       comm_type: str | None = None, db: Session = Depends(get_session)):
    """DiscoverServices. Section 2.2's decision: one gate — an unauthorized
    consumer's query simply never returns the service, it is never told
    the service exists.

    HISTORY.md §5: discover_services only filtered on
    api_name/api_version — the reference (discoverservice.go's
    matchesFilter/checkAefId/checkProtocol/checkDataFormat/
    checkVersionAndCommType) also filters against each service's
    AefProfiles. aefId/protocol/dataFormat/commType are now real
    filters over ServiceProfile.aef_profiles (walked in Python, since
    it's a JSON blob here rather than the reference's relational
    AefProfile/Version/Resource join — same adaptation as the field's
    own storage). apiCat isn't — this build's ServiceProfile has no
    category, and nothing that publishes a service supplies one
    (OI-5-sme-filters).
    """
    stmt = select(ServiceProfile)
    if api_name:
        stmt = stmt.where(ServiceProfile.service_name == api_name)
    if api_version:
        stmt = stmt.where(ServiceProfile.version == api_version)
    rows = db.scalars(stmt).all()

    visible = [r for r in rows if _matches_aef_filters(r, aef_id, protocol, data_format, comm_type) and _visible_to(r, api_invoker_id)]
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
    """SubscribeEvent, with TS 29.222's CAPIFEventFilter (OI-5-sme-filters):
    `apiIds` (this build's serviceIds), `apiInvokerIds` and `aefIds`. Each
    filter that is set must share a value with the event — see
    `_filters_match` — and an event that carries no value of that kind
    does not match it."""
    if not set(body.eventTypes) <= EVENT_TYPES:
        raise framework_error(FrameworkError.SUBSCRIPTION_SCOPE_CONFLICT, detail=f"eventTypes must be a subset of {sorted(EVENT_TYPES)}")
    sub = ServiceEventSubscription(subscriber_id=subscriber_id, event_types=body.eventTypes, callback_uri=body.callbackUri,
                                   api_ids=body.apiIds, api_invoker_ids=body.apiInvokerIds, aef_ids=body.aefIds)
    db.add(sub)
    db.commit()
    return {"subscriptionId": str(sub.subscription_id)}


@app.delete("/capif-events/v1/{subscriber_id}/subscriptions/{subscription_id}", status_code=204)
def unsubscribe_events(subscriber_id: str, subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    sub = db.get(ServiceEventSubscription, subscription_id)
    if sub is not None and sub.subscriber_id == subscriber_id:
        db.delete(sub)
        db.commit()


def _aef_ids(service: ServiceProfile) -> set[str]:
    return {p["aefId"] for p in service.aef_profiles or [] if p.get("aefId")}


def _filters_match(sub: ServiceEventSubscription, detail: dict) -> bool:
    """CAPIFEventFilter matching (eventservice.go's getMatchingSubs): every
    filter the subscription sets must intersect the event's own values of
    that kind; an unset filter matches anything."""
    for wanted, carried in ((sub.api_ids, detail.get("apiIds")), (sub.api_invoker_ids, detail.get("apiInvokerIds")),
                            (sub.aef_ids, detail.get("aefIds"))):
        if wanted and not set(wanted) & set(carried or ()):
            return False
    return True


def _deliver(db: Session, event_type: str, detail: dict, payload: dict, visible=lambda sub: True) -> None:
    for sub in db.scalars(select(ServiceEventSubscription)).all():
        if event_type in sub.event_types and _filters_match(sub, detail) and visible(sub):
            enqueue(db, sub.callback_uri, {**payload, "subscriptionId": str(sub.subscription_id),
                                           "eventType": event_type, "eventDetail": detail})
            # A row in the caller's transaction, sent after it commits (PR-MSG-1.6); the caller commits.


def notify_service_change(db: Session, service: ServiceProfile, event_type: str) -> None:
    """Producer-initiated push (HISTORY.md §5): wired in from
    register_service (SERVICE_API_AVAILABLE on create, SERVICE_API_UPDATE
    on the idempotent re-registration path) and deregister_service
    (SERVICE_API_UNAVAILABLE). Delivered to every subscriber whose
    eventTypes includes event_type, whose filters match the service (its
    serviceId and its AEFs), and who is authorized to see the service, per
    discover_services' own gate (section 2.2).
    """
    detail = {"apiIds": [str(service.service_id)], "aefIds": sorted(_aef_ids(service))}
    _deliver(db, event_type, detail, {"serviceId": str(service.service_id)},
             visible=lambda sub: _visible_to(service, sub.subscriber_id))


def notify_invoker_change(db: Session, api_invoker_id: str, event_type: str) -> None:
    """OI-5-sme-filters: API_INVOKER_ONBOARDED / _UPDATED / _OFFBOARDED,
    delivered to subscribers of the event type whose filters match the
    invoker (an apiIds or aefIds filter never matches an invoker event)."""
    _deliver(db, event_type, {"apiInvokerIds": [api_invoker_id]}, {"apiInvokerId": api_invoker_id})


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


# ---------------------------------------------------------------- registry reads (GUI pass 2)
# The provider, invoker, trusted-invoker and event-subscription registries were
# write-only (or read one known id at a time). No secret ever leaves here:
# invokers expose their id and public key, never the onboarding-secret hash.

@app.get("/provider-registrations")
def list_providers(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    page = paginate(db, select(ProviderRegistration), limit, offset)
    return {**page, "items": [{"apfId": p.apf_id, "providerDomainInfo": p.provider_domain_info,
             "serviceCount": len(db.scalars(select(ServiceProfile).where(ServiceProfile.producer_id == p.apf_id)).all())}
            for p in page["items"]]}


@app.get("/invoker-registrations")
def list_invokers(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    page = paginate(db, select(InvokerRegistration), limit, offset)
    return {**page, "items": [{"apiInvokerId": i.api_invoker_id, "apiInvokerPublicKey": i.public_key,
             "keyAuthentication": _pem_public_key(i.public_key) is not None,
             "trusted": db.get(TrustedInvoker, i.api_invoker_id) is not None}
            for i in page["items"]]}


@app.get("/trusted-invokers")
def list_trusted_invokers(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    page = paginate(db, select(TrustedInvoker), limit, offset)
    return {**page, "items": [_trusted_invoker_view(ti) for ti in page["items"]]}


@app.get("/capif-events/v1/{subscriber_id}/subscriptions")
def list_event_subscriptions(subscriber_id: str, limit: int = PageLimit, offset: int = PageOffset,
                              db: Session = Depends(get_session)):
    """The real TS29222_CAPIF_Events_API.yaml only ever defines POST on
    this path — no GET/list operation exists in the real spec at all, so
    this is this build's own GUI-pass addition, not a spec-mandated
    shape: free to follow this build's own pagination convention.
    """
    stmt = select(ServiceEventSubscription).where(ServiceEventSubscription.subscriber_id == subscriber_id)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"subscriptionId": str(s.subscription_id), "subscriberId": s.subscriber_id, "eventTypes": s.event_types,
             "callbackUri": s.callback_uri, "apiIds": s.api_ids, "apiInvokerIds": s.api_invoker_ids, "aefIds": s.aef_ids}
            for s in page["items"]]}
