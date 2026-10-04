"""R1 Termination — the gateway every rApp connects through.

Carries no domain schema of its own (SMO Design v1.3 section 3.3). Its whole
job is TLS/auth termination, routing, and the version-independent Bootstrap
endpoint. Route table and Bootstrap semantics per Foundational Platform LLD
section 4.

Phase 1: implemented as a thin FastAPI reverse-proxy rather than a real
gateway product (Kong etc. — flagged as a REFERENCE option, not adopted,
per the Repo Map blueprint) so the whole SMO can run as one docker-compose
stack without an extra infra dependency.
"""

import logging
import os
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

from smo_shared.logconfig import install_logging
from smo_shared.metrics import install_metrics, record_role_refusal
from smo_shared.bodylimit import MIB, BodySizeLimit, settings_from_env
from smo_shared.correlation import HEADER_NAME as CORRELATION_ID_HEADER
from smo_shared.correlation import apply_correlation_id, get_correlation_id
from smo_shared.audit import audit_enabled, write_audit
from smo_shared.health import install_health
from smo_shared import roles
from smo_shared.invoker import INVOKER_ID_HEADER, ON_BEHALF_OF_HEADER
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.ratelimit import TokenBuckets
from smo_shared.timeouts import introspect_timeout, upstream_timeout

log = logging.getLogger(__name__)

app = FastAPI(title="R1 Termination")
install_logging(app)  # structured JSON logs and one access-log line per request (PR-OBS-1)
install_metrics(app)  # /metrics and request count/latency series (PR-OBS-2)
# /health (with /live and /ready) and /bootstrap are this gateway's own exemptions (see
# below: the probes are answered ahead of _authorized entirely, /bootstrap is "No auth (network-isolated)") — every other
# path here is the catch-all proxy route, which really does call
# _authorized() on every request.
apply_r1_gateway_security(app, public_paths=frozenset({"/health", "/live", "/ready", "/bootstrap"}))
# This gateway is the true origin point for external traffic: a caller
# that never sent its own X-Correlation-ID gets one assigned here, which
# then propagates through the whole downstream fan-out (see the proxy
# route below, and smo_shared/r1_client.py for the intra-mesh half).
apply_correlation_id(app)

# Request body cap (PR-SEC-8.1): 1 MiB for every route, except the one that carries a file, an AI/ML model
# artifact upload (the GUI's nginx allows the same 50 MiB). Environment: R1_MAX_BODY_BYTES and
# R1_MAX_BODY_OVERRIDES (`<path-pattern>=<bytes>,...`; setting it replaces the default override).
app.add_middleware(BodySizeLimit, settings=settings_from_env(
    "R1", default_overrides=f"/mlmr/models/*/artifact={50 * MIB}"))

# Per-caller request budget (PR-SEC-8.2): a token bucket per invoker id. R1_RATE_PER_SECOND (default 100, 0 turns
# it off) refills it, R1_RATE_BURST (default 200) is its size. Held in this process: with several replicas each
# has its own bucket until the shared store of SEC-8.5.
_limiter = TokenBuckets(rate=lambda: float(os.environ.get("R1_RATE_PER_SECOND", "100")),
                        burst=lambda: float(os.environ.get("R1_RATE_BURST", "200")))


# R1 Termination's own probes, declared ahead of the catch-all proxy route so they are answered here,
# unauthenticated, rather than 404ing as an unknown prefix. Every backend module's own probes are
# reached through the proxy as /<module>/health, /<module>/ready (token-gated like any proxied call);
# the GUI BFF's GET /modules/status probes both. The gateway keeps no state of its own (the audit rows of PR-SEC-11 go to the shared database, and a database that
# is down fails no call), so it is ready whenever it is live; SME being down shows as 401s on proxied calls and as the modules'
# own /ready failing.
install_health(app)


# path prefix -> backend service, per Foundational Platform LLD section 4.2
ROUTES = {
    "/sme": os.environ.get("SME_URL", "http://sme:8000"),
    "/dme": os.environ.get("DME_URL", "http://dme:8000"),
    "/dme-push": os.environ.get("DME_URL", "http://dme:8000"),
    "/dme-pull": os.environ.get("DME_URL", "http://dme:8000"),
    "/onboarding": os.environ.get("ONBOARDING_URL", "http://onboarding:8000"),
    "/rapp-mgmt": os.environ.get("RAPP_MGMT_URL", "http://rapp-mgmt:8000"),
    "/ran-nf-oam": os.environ.get("RAN_NF_OAM_URL", "http://ran-nf-oam:8000"),
    "/a1-related": os.environ.get("A1_RELATED_URL", "http://a1-related:8000"),  # reserved, inert until Near-RT RIC
    "/nfo": os.environ.get("NFO_URL", "http://nfo:8000"),
    "/focom": os.environ.get("FOCOM_URL", "http://focom:8000"),
    "/aimgf": os.environ.get("AIMGF_URL", "http://aimgf:8000"),
    "/mlmr": os.environ.get("MLMR_URL", "http://mlmr:8000"),
    "/mllf": os.environ.get("MLLF_URL", "http://mllf:8000"),
    "/ran-analytics": os.environ.get("RAN_ANALYTICS_URL", "http://ran-analytics:8000"),
    "/mdaf": os.environ.get("MDAF_URL", "http://mdaf:8000"),
    "/intent-service": os.environ.get("INTENT_SERVICE_URL", "http://intent-service:8000"),
    "/so-smos": os.environ.get("SO_SMOS_URL", "http://so-smos:8000"),
    "/sa-smos": os.environ.get("SA_SMOS_URL", "http://sa-smos:8000"),
    # Wave 10.1: the EnergySaving reference rApp's own northbound API (its
    # operator dashboard, override and loop controls) — reached by the GUI
    # through the same gateway as the SMO modules.
    "/energy-saving-rapp": os.environ.get("ENERGY_SAVING_RAPP_URL", "http://energy-saving-rapp:8000"),
    # Wave 10.2: the Mobility Optimization reference rApp
    "/mobility-optimization-rapp": os.environ.get("MOBILITY_OPTIMIZATION_RAPP_URL", "http://mobility-optimization-rapp:8000"),
    # Wave 10.3: the Coverage Optimization reference rApp
    "/coverage-optimization-rapp": os.environ.get("COVERAGE_OPTIMIZATION_RAPP_URL", "http://coverage-optimization-rapp:8000"),
    # Wave 10.4: the Traffic Steering reference rApp
    "/traffic-steering-rapp": os.environ.get("TRAFFIC_STEERING_RAPP_URL", "http://traffic-steering-rapp:8000"),
}


def _public_base_url() -> str | None:
    """PR-SEC-1.6: the address consumers outside the compose network reach this gateway by (`R1_PUBLIC_BASE_URL`, for example
    `https://r1.example:8443` behind the TLS edge), or None. Set by the operator, never taken from request headers: the token endpoint
    this advertises is where an rApp sends its client credentials, so a Host header an attacker chose must not decide it."""
    value = os.environ.get("R1_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise RuntimeError(f"R1_PUBLIC_BASE_URL must be an origin such as https://host:8443, not {value!r}")
    return value


PUBLIC_BASE_URL = _public_base_url()                 # read once at start: a bad value stops the service, it does not surface per request


@app.get("/bootstrap")
def bootstrap():
    """Foundational Platform LLD section 4.1: BootstrapInformation.apiEndpoints
    ONLY ever contains service-apis (discovery) and published-apis
    (registration) entries — never events-subscription. An rApp discovers
    the subscription endpoint the normal way, via service discovery, once
    it can reach service-apis. No auth (network-isolated), URI-stable
    across all R1 Termination versions.

    Inside the compose network the entries name SME directly (`http://sme:8000/...`). With `R1_PUBLIC_BASE_URL` set (PR-SEC-1.6) they
    name this gateway's public address instead: the API entries go through the gateway (`<base>/sme/...`, token required) and the
    token endpoint is the one path the TLS edge forwards to SME without a token (`<base>/sme/oauth2/token`), so a consumer that only
    reaches the HTTPS door can complete the whole flow.
    """
    if PUBLIC_BASE_URL:
        token, apis = f"{PUBLIC_BASE_URL}/sme/oauth2/token", f"{PUBLIC_BASE_URL}/sme"
    else:
        token, apis = f"{ROUTES['/sme']}/oauth2/token", ROUTES["/sme"]
    return {
        "apiEndpoints": [
            {
                "apiName": "service-apis",
                "tokenEndPoint": {"uri": token},
                "apiEndPoint": {"uri": f"{apis}/service-apis/v1/allServiceAPIs"},
            },
            {
                "apiName": "published-apis",
                "tokenEndPoint": {"uri": token},
                "apiEndPoint": {"uri": f"{apis}/published-apis/v1"},
            },
        ]
    }


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], operation_id="proxy")
async def proxy(full_path: str, request: Request):
    """HISTORY.md §2: explicit operation_id, not FastAPI's
    auto-derived one — generate_unique_id() picks
    list(route.methods)[0].lower() for its default, and route.methods is
    a plain set, so the auto id (and the "Duplicate Operation ID"
    warning it triggers) was non-deterministic across process runs
    (PYTHONHASHSEED-dependent set iteration order) purely because this
    one route serves five methods. Surfaced by adding a persisted
    OpenAPI spec (docs/openapi/) with a CI check that the committed file
    matches the live schema — a non-deterministic operationId made that
    check itself flaky. operation_id isn't referenced by any client in
    this build, so pinning it to a fixed string is a pure stability fix,
    not a behavior change.
    """
    response = await _proxy(full_path, request)
    audited = getattr(request.state, "audit", None)          # set once the caller is known: an unauthenticated or rate-limited call is not recorded
    if audited is not None and request.method in roles.CHANGES and audit_enabled():
        invoker, role, refused, on_behalf_of = audited
        result = f"REFUSED:{refused}" if refused else str(response.status_code)
        task = BackgroundTask(write_audit, actor=invoker or "unknown", role=role, action=request.method, target="/" + full_path, result=result,
                              correlation_id=get_correlation_id(), detail={"onBehalfOf": on_behalf_of} if on_behalf_of else None)
        response.background = task
    return response


async def _proxy(full_path: str, request: Request):
    """The proxy itself (`proxy` above adds the audit record): authenticates, applies the role policy, forwards."""
    segments = full_path.split("/", 1)
    prefix = "/" + segments[0]
    if segments[1:] == ["metrics"]:
        # Every module's /metrics is for the scraper on the container network (PR-OBS-2.3), not for token holders.
        return JSONResponse(status_code=404, content={"title": "NO_ROUTE", "status": 404})
    backend = ROUTES.get(prefix)
    if backend is None:
        return JSONResponse(status_code=404, content={"title": "NO_ROUTE", "status": 404})

    caller = await _introspect_token(request)
    if caller is None:
        return JSONResponse(status_code=401, content={"title": "UNAUTHORIZED", "status": 401})
    invoker_id, role = caller
    request.state.audit = (invoker_id, role, None, request.headers.get(ON_BEHALF_OF_HEADER) if role == roles.ROLE_INTERNAL else None)
    wait = _limiter.take(invoker_id or "anonymous")
    if wait is not None:
        return JSONResponse(status_code=429, headers={"Retry-After": str(wait)}, content={
            "title": "RATE_LIMITED", "status": 429,
            "detail": f"this caller has used its request budget; retry in {wait} s"})

    rest_of_path = segments[1] if len(segments) > 1 else ""
    if role == roles.ROLE_RAPP and (roles.internal_only(prefix, request.method, rest_of_path)
                                    or not roles.rapp_may_change(prefix, request.method, rest_of_path)):
        # PR-SEC-14: a route that changes what the platform allows rApps to do is not one an rApp may call, and an rApp changes only what
        # roles.RAPP_MAY_CHANGE lists
        action = "refused" if roles.enforcement_mode() == "enforce" else "audited"
        record_role_refusal(prefix, action)
        if action == "refused":
            request.state.audit = (invoker_id, role, "ROLE_NOT_PERMITTED", None)
            return JSONResponse(status_code=403, content={
                "title": "ROLE_NOT_PERMITTED", "status": 403,
                "detail": f"{request.method} {prefix}/{rest_of_path} is for SMO modules and operators, not for an rApp"})
        log.warning("role audit: rApp %s called %s %s/%s", invoker_id, request.method, prefix, rest_of_path)

    # Strip the module prefix before forwarding — no backend service's own
    # routes carry it (e.g. SME's real route is /published-apis/v1/...,
    # never /sme/published-apis/v1/...). Forwarding the prefix through
    # unstripped would 404 against every real backend; caught while
    # building the cross-service integration test harness.

    # (rest_of_path was worked out above, for the role policy)
    # TLS is terminated at the ingress in front of this container (Phase 1:
    # docker-compose network boundary) — everything past _authorized above
    # is just forwarding the already-authenticated request.
    body = await request.body()
    # Every other header forwards verbatim; X-Correlation-ID is
    # explicitly overridden with this request's own real one (the
    # caller's, or one apply_correlation_id's middleware just generated
    # if it sent none) rather than whatever raw casing/value it arrived
    # with, so a caller that omitted the header still gets a consistent
    # ID threaded through its own request's whole downstream fan-out.
    forwarded_headers = {k: v for k, v in request.headers.items()
                          if k.lower() not in ("host", CORRELATION_ID_HEADER.lower(), INVOKER_ID_HEADER.lower(), roles.ROLE_HEADER.lower(),
                                               ON_BEHALF_OF_HEADER.lower())}
    forwarded_headers[roles.ROLE_HEADER] = role              # PR-SEC-14: never a value the caller sent (dropped above)
    forwarded_headers[CORRELATION_ID_HEADER] = get_correlation_id()
    # The caller's own id, from the introspected token: any inbound value of
    # this header is dropped above, so a backend can trust it. Empty when the
    # token carries no client id.
    if invoker_id:
        forwarded_headers[INVOKER_ID_HEADER] = invoker_id
    # Who an SMO module is acting for (smo_shared/invoker.py). Only a module may say it: an rApp's own value was dropped above, so an rApp cannot
    # pose as another rApp (to escape its own limits, or to spend another's).
    on_behalf_of = request.headers.get(ON_BEHALF_OF_HEADER)
    if on_behalf_of and role == roles.ROLE_INTERNAL:
        forwarded_headers[ON_BEHALF_OF_HEADER] = on_behalf_of
    try:
        async with httpx.AsyncClient(timeout=upstream_timeout()) as client:
            upstream = await client.request(
                request.method,
                f"{backend}/{rest_of_path}",
                headers=forwarded_headers,
                params=request.query_params,
                content=body,
            )
    except httpx.TimeoutException:
        return JSONResponse(status_code=504, content={
            "title": "UPSTREAM_TIMEOUT", "status": 504,
            "detail": f"{prefix} did not answer within {upstream_timeout():g} s"})
    except httpx.HTTPError:
        return JSONResponse(status_code=502, content={
            "title": "UPSTREAM_UNAVAILABLE", "status": 502, "detail": f"{prefix} could not be reached"})
    return Response(content=upstream.content, status_code=upstream.status_code, headers=dict(upstream.headers))


async def _introspect(request: Request) -> str | None:
    """The caller's invoker id from the token, None when the token is not good (see `_introspect_token`)."""
    caller = await _introspect_token(request)
    return None if caller is None else caller[0]


async def _introspect_token(request: Request) -> tuple[str, str] | None:
    """HISTORY.md §2: "No real OAuth2/token enforcement at R1
    Termination — only a comment and a tokenEndPoint URI in the bootstrap
    response; no actual validation code path." This is that path, per
    SMO Design v1.3 section 3.3's route table (auth: oauth2 on every
    backend route except /bootstrap, which never calls this).

    The reference's own token validation is self-contained signature
    verification against a real, externally-issued signed JWT (Keycloak
    — an external IdP this build doesn't run, the same
    no-real-southbound-integration elision as everywhere else); SME's
    own /oauth2/token issues an opaque token instead (see its own
    docstring), so the honest substitute here is RFC 7662 token
    INTROSPECTION — asking SME whether the token is still active — on
    every proxied request. This is a security gate, not a best-effort
    side effect: unlike this build's usual "unreachable callback never
    fails the primary operation" pattern (DME/A1 Related notifications),
    SME being unreachable here fails CLOSED (unauthorized), not open.
    """
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[len("bearer "):].strip()
    if not token:
        return None
    async with httpx.AsyncClient(timeout=introspect_timeout()) as client:
        try:
            resp = await client.request("POST", f"{ROUTES['/sme']}/oauth2/introspect", json={"token": token})
        except httpx.HTTPError:
            return None
    if resp.status_code != 200 or resp.json().get("active") is not True:
        return None
    body = resp.json()
    # PR-SEC-14: the role SME records for the invoker. An SME that does not say (the release before this one) is read by the scope, which is
    # what its own clients ask for: smo-internal / smo-gui is an SMO module, anything else an rApp.
    role = body.get("role") or (roles.ROLE_INTERNAL if body.get("scope") in roles.INTERNAL_SCOPES else roles.ROLE_RAPP)
    return str(body.get("client_id") or ""), role


async def _authorized(request: Request) -> bool:
    return await _introspect(request) is not None
