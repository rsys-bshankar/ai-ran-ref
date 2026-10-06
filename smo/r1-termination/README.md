# R1 Termination (`r1-termination/`)

> The single entry point every rApp, GUI call and SMO-internal cross-module call goes through: it checks the bearer token against SME and forwards the request to the module named by the first path segment.

| | |
|---|---|
| Standards basis | O-RAN R1 gateway (token check, routing) + internal prefix-routing design |
| R1 route / port | Is the R1 gateway itself: container `:8000`, host `:8080` in `docker-compose.yml`. Own routes: `GET /health`, `GET /live`, `GET /ready`, `GET /version`, `GET /bootstrap`; everything else is the catch-all proxy |
| Depends on (over R1) | SME (`POST /oauth2/introspect`, direct to SME's address, not through itself); every module in `ROUTES` as a forwarding target |
| Called by | rApps, the GUI BFF, `smo_shared.R1Client` in every module, the reference rApps |
| Database tables | None (stateless) |
| Unit tests | 30 passed (`tests/`, no DB, standalone) |
| Status | Done. Token model is opaque-token introspection, not JWT/IdP signature checking; route-level test depth tracked by [OI-4](../OPEN_ITEMS.md) |

## 1. High-level design (HLD)

### 1.1 Purpose and scope

R1 Termination is the one place where R1 is exposed. It carries no domain schema and no business logic. It does three things:

1. **Bootstrap**: `GET /bootstrap` returns the SME token and service-discovery / publishing endpoints, unauthenticated, so an rApp can start from nothing.
2. **Authentication**: every proxied request must carry a bearer token that SME reports as active (RFC 7662 introspection).
3. **Routing**: the first path segment selects a backend (`/dme/data-jobs` goes to DME's `/data-jobs`); the prefix is stripped before forwarding.

It is deliberately a thin FastAPI reverse proxy rather than a gateway product (Kong etc.), so the whole SMO runs as one `docker-compose` stack with no extra infrastructure. TLS is assumed to terminate at the ingress in front of the container.

### 1.2 Standards basis

O-RAN R1 places a gateway between rApps and the SMO framework services; the R1 contract itself is the set of service APIs behind it (see the module READMEs). What this module realises of that gateway: a stable bootstrap URI, bearer-token enforcement on every service call, and prefix routing. Not realised: self-contained signed-JWT validation against an external IdP (Keycloak in the reference); SME issues opaque tokens and the gateway introspects them instead. The route table and the choice of prefixes are this build's own design (no standard fixes them).

Conventions every R1-facing service applies (authentication scheme `r1BearerAuth`, ProblemDetails, pagination, `notificationDestination`, correlation id, cross-module calls through `R1Client`) are cross-cutting and live in [ARCHITECTURE.md, R1 API conventions](../docs/ARCHITECTURE.md#r1-api-conventions). They are not repeated here.

### 1.3 Position in the platform

```
 rApp / GUI BFF / R1Client in any module
            |  Authorization: Bearer <token>
            v
   +--------------------+   POST /oauth2/introspect    +-------+
   |  R1 Termination    | ---------------------------> |  SME  |
   |  (this module)     | <--- {"active": true|false}  +-------+
   +--------------------+
      | strip "/<prefix>", forward verbatim
      v
   sme  dme  onboarding  rapp-mgmt  ran-nf-oam  nfo  focom  aimgf  mlmr  mllf
   ran-analytics  mdaf  intent-service  so-smos  sa-smos  <four reference rApps>
```

It calls only SME (introspection) and the chosen backend. It never reads a database and never interprets a body. The southbound mock (`mock-o1-adaptor`) is not behind it.

### 1.4 Ownership

| Owns | Does not own → owner |
|---|---|
| The prefix → backend route table (`ROUTES`) | Token issuance, invoker registry, introspection answer → SME |
| The bearer-token gate on proxied calls | Per-API authorization (which invoker may call which service) → SME service discovery gating; per-role rules for the GUI → GUI BFF (`gui-bff/app/rbac.py`) |
| `/bootstrap` and the probes | Every backend route and its errors → the backend module |
| Correlation-id assignment at the edge | Correlation-id propagation between modules → `smo_shared` (`R1Client`) |

### 1.5 Design decisions

| Decision | Reason |
|---|---|
| Token check by introspection against SME on every request | SME issues opaque, server-tracked tokens (no IdP in this build), so validity can only be asked, not verified from a signature. |
| Fails closed: an unreachable SME, a missing or non-`Bearer` header, an empty token, or `active != true` all give `401 UNAUTHORIZED` | Unlike best-effort notifications elsewhere, this is a security gate. |
| `/bootstrap`, the probes (`/health`, `/live`, `/ready`) and `/version` are unauthenticated | Bootstrap must work before a token exists, and an orchestrator probes without a token; all are assumed network-isolated. The probes are declared ahead of the catch-all, so it is answered locally and not treated as an unknown prefix. |
| Unknown prefix is `404 NO_ROUTE` before any token check | Nothing is forwarded, nothing is learned about backends. |
| Prefix is stripped before forwarding | No backend carries its own prefix in its routes. |
| `X-Correlation-ID` is overridden with the request's own id (the caller's, or the one the middleware just assigned); `X-R1-Invoker-Id` is set to the introspected token's `client_id` (any inbound value is dropped; omitted when the token carries none) and `X-R1-Role` to the `role` SME records for that invoker, `internal` (an SMO module or the GUI, which presented the enrollment secret) or `rapp` (`PR-SEC-14`; an SME that reports none is read by the token's scope); all other headers except `Host` are forwarded verbatim | One id threads the whole downstream fan-out of an inbound call (call flow 14). |
| `/dme-push` and `/dme-pull` both route to DME | Reserved aliases for the push and pull delivery transports; DME has no routes of its own under those names, so after prefix stripping they are the same as `/dme`. |
| Explicit `operation_id="proxy"` on the catch-all | FastAPI's auto id depended on set iteration order of the five methods and made the committed OpenAPI spec check flaky. |

Failure behaviour: a backend that does not answer within `R1_UPSTREAM_TIMEOUT_SECONDS` (60) is a `504 UPSTREAM_TIMEOUT`, one that cannot be reached is a `502 UPSTREAM_UNAVAILABLE`. Upstream status codes and bodies (including errors) are passed through unchanged. The backend call has the explicit 60 s timeout, longer than the 30 s a calling module allows itself (`smo_shared/timeouts.py`), so the outer call always outlasts the inner one; before this the gateway used httpx's implicit 5 s and failed any slower operation with an unhandled error.

## 2. Low-level design (LLD)

### 2.1 Code map

| File | Responsibility |
|---|---|
| `app/main.py` | The whole module: `ROUTES`, the probes, `/bootstrap`, the catch-all `proxy`, `_authorized` (introspection call). |
| `../shared/smo_shared/openapi_security.py` | `apply_r1_gateway_security(app, public_paths={"/health", "/live", "/ready", "/version", "/bootstrap"})`: adds the `r1BearerAuth` scheme to the OpenAPI document and marks those paths as unauthenticated. |
| `../shared/smo_shared/invoker.py` | `INVOKER_ID_HEADER`, `ON_BEHALF_OF_HEADER` and `invoker_id(request)`: the caller id a backend reads (MLMR's `storeDiscReqs`, and the per-rApp safeguards at RAN NF OAM, which apply to the rApp an SMO module is acting for). R1 Termination forwards `X-R1-On-Behalf-Of` only from an `internal` caller and drops an rApp's own value. |
| `../shared/smo_shared/correlation.py` | `apply_correlation_id(app)`: middleware assigning `X-Correlation-ID` when absent; `get_correlation_id()`. |

### 2.2 Data model

None: stateless. No table, no cache; every request is introspected afresh.

### 2.3 State machines

None: stateless.

### 2.4 API

| Method | Path | Purpose | Notable errors |
|---|---|---|---|
| GET | `/version` | The gateway's build, `{module, version, buildSha, builtAt}` (PR-OBS-8.1); no auth. A backend's is `/<module>/version`, token-gated like any call | 200 |
| GET | `/health` | Liveness of the gateway itself; no auth; an alias of `/live`. A backend's own probes are reached as `/<module>/health`, `/<module>/ready` and is token-gated like any call (the GUI BFF's `GET /modules/status` probes both). | none |
| GET | `/bootstrap` | `{apiEndpoints: [...]}` with exactly two entries, `service-apis` (discovery) and `published-apis` (registration), each with `tokenEndPoint.uri` and `apiEndPoint.uri`; no auth, URI-stable. The URIs name SME on the compose network, or, with `R1_PUBLIC_BASE_URL` set (an origin, never taken from request headers), `<base>/sme/...` with the token endpoint at `<base>/sme/oauth2/token` (PR-SEC-1.6). | none |
| GET, POST, PUT, PATCH, DELETE | `/{prefix}/{rest}` | Authenticate, strip `/{prefix}`, forward method, headers, query string and body to `ROUTES[prefix]/{rest}`; return the upstream status, headers and body. | `404 NO_ROUTE` unknown prefix; `401 UNAUTHORIZED` token check failed |

Notes:

- `/bootstrap` never lists an events-subscription endpoint: an rApp finds it through service discovery once it can reach `service-apis`.
- The URIs in `/bootstrap` are built from `ROUTES["/sme"]`, i.e. SME's own address (default `http://sme:8000`), not a gateway-prefixed URL. The token endpoint is therefore reached directly on SME. `R1Client` uses exactly this to obtain its token and to onboard its invoker.
- HEAD and OPTIONS are not routed (only the five methods above).
- A bare prefix (`/dme`) forwards to the backend root `/`.

Route table (`ROUTES`, prefix → env var → default):

| Prefix | Env var | Default backend |
|---|---|---|
| `/sme` | `SME_URL` | `http://sme:8000` |
| `/dme`, `/dme-push`, `/dme-pull` | `DME_URL` | `http://dme:8000` |
| `/onboarding` | `ONBOARDING_URL` | `http://onboarding:8000` |
| `/rapp-mgmt` | `RAPP_MGMT_URL` | `http://rapp-mgmt:8000` |
| `/ran-nf-oam` | `RAN_NF_OAM_URL` | `http://ran-nf-oam:8000` |
| `/nfo` | `NFO_URL` | `http://nfo:8000` |
| `/focom` | `FOCOM_URL` | `http://focom:8000` |
| `/aimgf` | `AIMGF_URL` | `http://aimgf:8000` |
| `/mlmr` | `MLMR_URL` | `http://mlmr:8000` |
| `/mllf` | `MLLF_URL` | `http://mllf:8000` |
| `/ran-analytics` | `RAN_ANALYTICS_URL` | `http://ran-analytics:8000` |
| `/mdaf` | `MDAF_URL` | `http://mdaf:8000` |
| `/intent-service` | `INTENT_SERVICE_URL` | `http://intent-service:8000` |
| `/so-smos` | `SO_SMOS_URL` | `http://so-smos:8000` |
| `/sa-smos` | `SA_SMOS_URL` | `http://sa-smos:8000` |
| `/energy-saving-rapp` | `ENERGY_SAVING_RAPP_URL` | `http://energy-saving-rapp:8000` |
| `/mobility-optimization-rapp` | `MOBILITY_OPTIMIZATION_RAPP_URL` | `http://mobility-optimization-rapp:8000` |
| `/coverage-optimization-rapp` | `COVERAGE_OPTIMIZATION_RAPP_URL` | `http://coverage-optimization-rapp:8000` |
| `/traffic-steering-rapp` | `TRAFFIC_STEERING_RAPP_URL` | `http://traffic-steering-rapp:8000` |

The reference rApps are routed so the GUI reaches their operator APIs through the same gateway as the SMO modules.

### 2.5 Interactions

| Call | When | Failure behaviour |
|---|---|---|
| `POST {SME_URL}/oauth2/introspect` with `{"token": ...}` | Every proxied request, before forwarding | Transport error, non-200, or `active != true`: request refused `401`. Fails closed. |
| `{method} {backend}/{rest}` | After a successful token check | No handling: transport errors are not caught (see 1.5). |

Request-time order: route lookup (404) → bearer header present and non-empty (401) → introspection (401) → forward. Authentication only establishes that the token is active; the gateway does not read `client_id` from the introspection answer and does not pass an identity downstream.

### 2.6 Configuration

| Variable | Default | Effect |
|---|---|---|
| `<NAME>_URL` per route | see the route table | Backend base URL for that prefix. `DME_URL` serves three prefixes. |
| `R1_UPSTREAM_TIMEOUT_SECONDS` | `60` | How long the gateway waits for the backend it proxies to |
| `R1_PUBLIC_BASE_URL` | unset | The origin consumers outside the compose network reach the gateway by (`https://localhost:8443` behind the TLS edge): `/bootstrap` advertises it instead of SME's compose address (PR-SEC-1.6). Validated at start |
| `R1_MAX_BODY_BYTES` | `1048576` | Largest request body any route accepts (413 over it) |
| `R1_MAX_BODY_OVERRIDES` | `/mlmr/models/*/artifact=52428800` | `<path-pattern>=<bytes>,...` caps that replace the default for matching paths (`*` matches anything); the default is the model artifact upload, 50 MiB like the GUI's nginx. Setting it replaces this default |
| `R1_RATE_PER_SECOND` | `100` | Requests a second each caller (invoker id) may sustain; `0` turns the limiter off |
| `R1_RATE_BURST` | `200` | Requests a caller may make at once before it is held to the rate |
| `SMO_ROLE_ENFORCEMENT` | `enforce` | `enforce`: an rApp is refused on the internal-only routes; `audit`: the same decision is counted (`smo_role_refusals_total`) and logged, then allowed (a rolling upgrade from a release with no enrollment). Anything else is `enforce` |
| `R1_KILL_SWITCH` | `on` | `off`: the gateway does not refuse changes by a stopped rApp (RAN NF OAM still refuses its config jobs). On, it reads the `rapp_kill` table; see "The kill switch" below |
| `R1_KILL_CACHE_SECONDS` | `3` | How long the gateway keeps what it read about one rApp: the delay between throwing the switch and the gateway acting on it |
| `R1_AUDIT` | `on` | `off` records nothing in the audit chain (PR-SEC-11). On, the gateway needs `SMO_DATABASE_URL` like a module does; a write that fails is logged and counted (`smo_audit_writes_total{outcome="failed"}`) and never fails the call |
| `R1_INTROSPECT_TIMEOUT_SECONDS` | `5` | How long it waits for SME's token introspection (a timeout fails closed: 401) |

`SME_URL` is also the target of introspection and of the URIs in `/bootstrap`.

### 2.7 Error codes

The gateway answers with `JSONResponse` bodies of the form `{"title": ..., "status": ...}`, not the full RFC 7807 shape the backends use.

| `title` | Status | When |
|---|---|---|
| `NO_ROUTE` | 404 | First path segment is not in `ROUTES` |
| `UNAUTHORIZED` | 401 | No `Authorization` header, not `Bearer`, empty token, SME unreachable, or token not active |
| `PAYLOAD_TOO_LARGE` | 413 | The request body is larger than the cap for that path (`Content-Length`, or counted while streaming); the backend is not called |
| `RATE_LIMITED` | 429 | The caller has used its request budget; `Retry-After` is the whole seconds to wait. Counted after authentication, so a refused unauthenticated request spends nobody's budget |
| `ROLE_NOT_PERMITTED` | 403 | The caller's role is `rapp` and the route is one only SMO modules and operators may call (`smo_shared/roles.py` `INTERNAL_ONLY`: setting or removing a per-rApp limit, defining or removing a KPI, purging CM history); the backend is not called |
| `UPSTREAM_TIMEOUT` | 504 | The backend did not answer within `R1_UPSTREAM_TIMEOUT_SECONDS` (`detail` names the route prefix) |
| `UPSTREAM_UNAVAILABLE` | 502 | The backend could not be reached (connection refused, DNS failure, reset) |

Every other status and body is the backend's, passed through.

### 2.8 Limits and open items

- Opaque-token introspection instead of signed JWTs. SME checks a token's scope when it issues it (HISTORY.md OI-2-oauth2-scope), but the gateway does not enforce it.
- Authentication only: no per-invoker or per-API authorization at the gateway. Routes map to modules, not to published APIs, so there is nothing here to match a scope against.
- Rate limit and body cap are in place (`PR-SEC-8.1`, `8.2`); no retry or circuit breaking. The buckets are per process, so with N gateway replicas a caller has N times the rate until the shared store of `SEC-8.5`; unauthenticated requests are not limited here yet (`SEC-8.3`), and one rate applies to every route (`SEC-8.4`).
- The upstream timeout is one value for every route (60 s), not per route or per call; a caller that sets its own longer timeout is still cut at 60 s.
- Upstream response headers are forwarded verbatim, including those describing the encoding of the original body.
- Test depth ([OI-4](../OPEN_ITEMS.md)).

## 3. Unit tests

### 3.1 Running them

```bash
cd smo/r1-termination && PYTHONPATH=.:../shared python -m pytest tests/ -q
```

### 3.2 What is covered

| Test file | Covers | Count |
|---|---|---|
| `tests/test_main.py` | Bootstrap content and its no-auth rule; route table covers every module; unknown prefix 404; proxy to the right backend; 401 for missing/non-bearer/inactive token; fail-closed when SME is unreachable; method, body and query forwarding; `Host` stripped, other headers kept; correlation id generated or kept; upstream error status passthrough; bare-prefix path; `/dme-push` and `/dme-pull` routing; local `/health` | 18 |

### 3.3 What is not covered here

- A real token round trip (SME issues, gateway introspects, backend answers) and the in-process service mesh: `tests_integration/` (`mesh.py` re-implements the prefix routing and bypasses gateway mechanics; `test_demo_runbook.py` exercises `/bootstrap`).
- The committed `docs/openapi/r1-termination.json` matching the live schema: `tests_integration/test_openapi_specs.py`.
- Real network behaviour: the timeout and error mapping is tested with a stubbed client, not over a real socket.

## 4. References

- Conventions shared by every R1-facing service: [ARCHITECTURE.md, R1 API conventions](../docs/ARCHITECTURE.md#r1-api-conventions)
- Call flows: [01 onboarding to deployment](../docs/call-flows/01-rapp-onboarding-to-deployment.md) (bootstrap), [14 correlation id](../docs/call-flows/14-correlation-id-propagation.md), [18 SME security lifecycle](../docs/call-flows/18-sme-trusted-invokers-lifecycle.md) (token and introspection)
- OpenAPI: [`../docs/openapi/r1-termination.json`](../docs/openapi/r1-termination.json)
- Related READMEs: [SME](../sme/README.md) (issues and introspects tokens), [DME](../dme/README.md)

## What an rApp may change (PR-SEC-14)

For a caller with the `rapp` role the gateway applies two lists from `shared/smo_shared/roles.py`: `INTERNAL_ONLY` (refused in any method) and `RAPP_MAY_CHANGE`, an allow-list for POST, PUT, PATCH and DELETE per module. A change that is not on it is refused with 403 `ROLE_NOT_PERMITTED` before a backend is called; `SMO_ROLE_ENFORCEMENT=audit` counts and logs it and lets it through. Reads are not decided by the allow-list. The list is what `smo_sdk` calls plus the consumer-facing request routes of the AI/ML services; `sdk/tests/conftest.py` fails any SDK test whose call is off it, so adding an SDK call means adding the route. An SMO module (the `internal` role) is never refused by either list.

## Audit (PR-SEC-11)

After it answers, the gateway adds one row to the audit hash chain (`smo_shared/audit.py`) for every authenticated POST, PUT, PATCH and DELETE, including the ones it refuses for the caller's role. Reads, calls with no good token and calls held by the rate limiter are not recorded (an attacker without a token must not be able to write to the database), and the body and query are never recorded. `python -m smo_shared.audit verify` and `export` run in any image of the stack: `docker compose exec r1-termination python -m smo_shared.audit verify`.

## The kill switch (AI-10.4)

An operator stops an rApp instance (`PUT /rapp-mgmt/instances/{id}/kill`, or the Stop button on the GUI's Safeguards page). RAN NF OAM refuses its config jobs at once; the gateway refuses every other *change* it makes, and every change an SMO module makes on its behalf, with 403 `RAPP_KILLED`. Left open so it can be wound down: reads, DELETE, the token endpoint and rolling back its own config job. See `smo_shared/killswitch.py` for the failure behaviour.
