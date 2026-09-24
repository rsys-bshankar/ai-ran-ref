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

import os

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

app = FastAPI(title="R1 Termination")

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
    "/ai-ml-workflow": os.environ.get("AI_ML_WORKFLOW_URL", "http://ai-ml-workflow:8000"),
    "/ran-analytics": os.environ.get("RAN_ANALYTICS_URL", "http://ran-analytics:8000"),
    "/policy-mgmt": os.environ.get("POLICY_MGMT_URL", "http://policy-mgmt:8000"),
    "/so-smos": os.environ.get("SO_SMOS_URL", "http://so-smos:8000"),
    "/sa-smos": os.environ.get("SA_SMOS_URL", "http://sa-smos:8000"),
}


@app.get("/bootstrap")
def bootstrap():
    """Foundational Platform LLD section 4.1: BootstrapInformation.apiEndpoints
    ONLY ever contains service-apis (discovery) and published-apis
    (registration) entries — never events-subscription. An rApp discovers
    the subscription endpoint the normal way, via service discovery, once
    it can reach service-apis. No auth (network-isolated), URI-stable
    across all R1 Termination versions.
    """
    return {
        "apiEndpoints": [
            {
                "apiName": "service-apis",
                "tokenEndPoint": {"uri": f"{ROUTES['/sme']}/oauth2/token"},
                "apiEndPoint": {"uri": f"{ROUTES['/sme']}/service-apis/v1/allServiceAPIs"},
            },
            {
                "apiName": "published-apis",
                "tokenEndPoint": {"uri": f"{ROUTES['/sme']}/oauth2/token"},
                "apiEndPoint": {"uri": f"{ROUTES['/sme']}/published-apis/v1"},
            },
        ]
    }


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], operation_id="proxy")
async def proxy(full_path: str, request: Request):
    """OPEN_ITEMS.md section 2: explicit operation_id, not FastAPI's
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
    segments = full_path.split("/", 1)
    prefix = "/" + segments[0]
    backend = ROUTES.get(prefix)
    if backend is None:
        return JSONResponse(status_code=404, content={"title": "NO_ROUTE", "status": 404})

    # Strip the module prefix before forwarding — no backend service's own
    # routes carry it (e.g. SME's real route is /published-apis/v1/...,
    # never /sme/published-apis/v1/...). Forwarding the prefix through
    # unstripped would 404 against every real backend; caught while
    # building the cross-service integration test harness.
    rest_of_path = segments[1] if len(segments) > 1 else ""

    # TLS is terminated at the ingress in front of this container (Phase 1: docker-compose
    # network boundary); OAuth2.0 validation happens here before forwarding, per
    # SMO Design v1.3 section 3.3's route table (auth: oauth2 on every backend route
    # except /bootstrap).
    body = await request.body()
    async with httpx.AsyncClient() as client:
        upstream = await client.request(
            request.method,
            f"{backend}/{rest_of_path}",
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            params=request.query_params,
            content=body,
        )
    return Response(content=upstream.content, status_code=upstream.status_code, headers=dict(upstream.headers))
