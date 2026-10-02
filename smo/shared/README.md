# Shared library (`shared/`)

> The `smo_shared` Python package every SMO backend module imports: the database session, FSM base, ProblemDetails errors, pagination, correlation ids, the forwarded invoker id, SSRF-guarded webhooks, the R1 client, OpenAPI security declaration and test helpers behind the R1 API conventions.

| | |
|---|---|
| Standards basis | Internal logic (common library; implements the RFC 7807 / RFC 7662 conventions used by every R1 service) |
| R1 route / port | None: a library, installed into every service image (`pip install -e /srv/shared` in the root `Dockerfile`) |
| Depends on (over R1) | `R1Client` calls R1 Termination (`/bootstrap`) and SME (`/invoker-registrations`, `/oauth2/token`) for its own token; no other module |
| Called by | Every backend module (imports); the SDK (`sdk/`) and the four sample rApps via `R1Client`. Not imported by `gui-bff` |
| Database tables | None. Provides `Base`, the engine and sessions that modules' `models.py` use |
| Unit tests | 180 passed (`tests/`; 40 more are skipped without `SMO_TEST_POSTGRES_URL`) |
| Status | Done. No OPEN_ITEMS ids |

## 1. High-level design (HLD)

### 1.1 Purpose and scope

One place for the plumbing that must behave identically in every service, so a convention ("R1 API conventions" in [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md#r1-api-conventions)) is implemented once and imported rather than re-derived. It contains no domain logic and owns no data. Modules import individual submodules (`from smo_shared.errors import ...`); `smo_shared/__init__.py` is empty apart from its docstring and re-exports nothing.

### 1.2 Standards basis

| Convention | Realised by | Reference |
|---|---|---|
| RFC 7807 ProblemDetails | `errors.py` | Error model the R1 service groups defer to (CAPIF TS 29.222 for SME, TS 29.500 for others, TS 28.532 for CM/FM); A1 policy management keeps its own table |
| RFC 7662 token introspection (R1 gateway) | Declared in OpenAPI by `openapi_security.py`; consumed by `r1_client.py` (obtains and sends the token). Enforcement lives in R1 Termination's `_authorized()`, not here | [`../../specs/5G_APIs/`](../../specs/5G_APIs/) CAPIF specs for the invoker path |
| RFC 6749 client-credentials grant | `r1_client.py` (`_ModuleIdentity`) | |
| TS 29.500 `3gpp-Sbi-Correlation-Info` | Deliberately not used: that header correlates subscriber identity, not requests. `X-Correlation-ID` is this build's own name | `correlation.py` module docstring |

Deliberately not here: any enforcing auth dependency on a backend service (R1 Termination is the single enforcement point), and a per-operation OpenAPI declaration of `X-Correlation-ID`.

### 1.3 Position in the platform

Imported by all services; at runtime it adds three outbound behaviours: `R1Client` (calls through R1 Termination, never a service URL), `webhook` (calls to caller-registered callback URLs), and nothing else. It never opens a connection by itself at import, except constructing the SQLAlchemy `engine` object in `db.py` (lazy: no connection until first use).

### 1.4 Ownership

Mapping of the "R1 API conventions" table in `docs/ARCHITECTURE.md` to code:

| Convention row | Implemented here | Not implemented here |
|---|---|---|
| Authentication | `openapi_security.apply_r1_gateway_security` (the `r1BearerAuth` scheme, global `security`, public-path exemptions); `R1Client` token acquisition | The introspection check (R1 Termination) and token issuance (SME) |
| Versioning | `openapi_security.R1_CONTRACT_VERSION` (`1.0.0`), set as `app.version` | |
| Errors | `errors.ProblemDetails`, `problem()`, `FrameworkError`, `framework_error()`, `illegal_transition_error()` | Each module's choice of code per route |
| Pagination | `pagination.paginate()`, `PageLimit`, `PageOffset` | Per-resource view functions; `gui-bff` keeps its own local copy |
| Subscriptions | Nothing (naming convention only: `notificationDestination`, with the CAPIF/O2ims exceptions) | Each module's request models |
| Callbacks | `webhook.post_webhook` / `get_webhook` / `delete_webhook` / `is_safe_webhook_destination` | |
| Correlation | `correlation.apply_correlation_id`, `get_correlation_id`; `R1Client` propagates | R1 Termination forwards its own current id |
| Cross-module calls | `r1_client.R1Client` | Choosing which module to call |

Also provided, outside that table: `db` (engine and session), `statemachine` (FSM base), `identity` (rAppId equivalence), `timeutil`, `testing`.

### 1.5 Design decisions

| Decision | Reason |
|---|---|
| One shared Postgres, partitioned by `moduleScope` columns, not per-module databases | Requirements v0.1 section 3. `db.py` offers one `Base`/engine; each module's models set and filter by their own scope. |
| `R1Client` is synchronous `httpx` with one process-wide identity and token cache | Cross-module calls inside request handlers are sync. One invoker per module, shared by its replicas through the `module_identity` table (or pinned by `SMO_INVOKER_ID`/`SMO_INVOKER_SECRET`); refreshed 30 s before expiry and once on 401. |
| When no token can be obtained, `R1Client` sends the call without `Authorization` and logs a warning rather than raising | R1 answers 401, which every caller already treats as an ordinary failed call. |
| Webhook guard blocks by scheme and literal address only (no DNS resolution, no hostname allowlist) | Legitimate callback hosts (rApp/producer containers) are assigned at deploy time and unknown in advance; unit tests use fictional hostnames. Residual risk: a hostname resolving to a blocked address (DNS rebinding) is accepted. |
| Webhook helpers never raise on an unreachable destination | Callbacks are best effort; each call site previously swallowed `httpx.HTTPError`. |
| FSM base holds only a transition table; state lives on the entity | One table per model class, reused across instances. |
| Error codes are tuples `(title, status)` in one `FrameworkError` class | One importable name per code; routes raise `framework_error(code, detail)`. |
| Correlation id is not declared in OpenAPI | A middleware-injected header is not a per-operation contract element. |

## 2. Low-level design (LLD)

### 2.1 Code map

| File | Responsibility |
|---|---|
| `smo_shared/db.py` | `MissingDatabaseUrl`, `resolve_database_url()`, `DATABASE_URL`, `engine_options()` / `build_engine()` (pool and session limits from `SMO_DB_*`), `engine`, `SessionLocal`, `Base`, `session_scope()`, `get_session()` |
| `smo_shared/single_runner.py` | `run_once_per_interval(name, interval_seconds, fn)`, `advisory_lock(name)` and the `PeriodicRun` model (table `periodic_run`): a periodic task runs on one replica per interval. No caller yet |
| `smo_shared/secretfile.py` | `read_secret(name)`: the value of `NAME`, or the contents of the file named by `NAME_FILE` (`SecretConflict` if both, `SecretFileError` if unreadable); used for the database URL and password |
| `smo_shared/bodylimit.py` | `BodySizeLimit` (ASGI middleware: 413 over a path's cap, from `Content-Length` or counted while streaming), `settings_from_env`, `parse_overrides` |
| `smo_shared/ratelimit.py` | `TokenBuckets`: a token bucket per caller, `take()` returns None or the seconds to wait; per process |
| `smo_shared/health.py` | `install_health(app, checks)`: `/live`, `/ready` and the `/health` alias; `database_check`, `sme_token_check`, `run_checks` |
| `smo_shared/timeouts.py` | `call_timeout()`, `upstream_timeout()`, `introspect_timeout()`: the platform's outbound HTTP timeouts, read from the environment when asked |
| `smo_shared/statemachine.py` | `StateMachine`, `Transition`, `IllegalTransition` |
| `smo_shared/errors.py` | `ProblemDetails`, `problem()`, `FrameworkError`, `framework_error()`, `illegal_transition_error()` |
| `smo_shared/pagination.py` | `paginate()`, `PageLimit`, `PageOffset`, `DEFAULT_LIMIT`, `MAX_LIMIT` |
| `smo_shared/correlation.py` | `apply_correlation_id()`, `get_correlation_id()`, `HEADER_NAME` |
| `smo_shared/invoker.py` | `INVOKER_ID_HEADER` (`X-R1-Invoker-Id`), `invoker_id(request)`: the caller id R1 Termination forwards |
| `smo_shared/webhook.py` | `post_webhook`, `get_webhook`, `delete_webhook`, `is_safe_webhook_destination` |
| `smo_shared/r1_client.py` | `R1Client` (a caller's own `headers=` are merged with the authorization and correlation headers; the client's win), `R1_GATEWAY_URL`, per-process `_ModuleIdentity` token cache |
| `smo_shared/openapi_security.py` | `apply_r1_gateway_security()`, `BEARER_SCHEME_NAME`, `R1_CONTRACT_VERSION` |
| `smo_shared/identity.py` | `rapp_id_from_instance()`, `is_framework_internal_identity()` |
| `smo_shared/timeutil.py` | `as_utc()` |
| `smo_shared/module_identity.py` | `DbIdentityStore` (`load`, `insert`, `replace`: a compare-and-swap) and the `ModuleIdentityRow` model (table `module_identity`): one SME invoker per module, shared by its replicas |
| `smo_shared/idempotency.py` | `idempotent(module, status_code)` route decorator, `run_idempotent()`, the `IdempotencyKey` model (table `idempotency_key`), `request_hash()` |
| `smo_shared/versioning.py` | `Versioned` (adds `row_version`, enforces it on every ORM UPDATE), `install_concurrency_handler()` (a stale write becomes 409 `CONCURRENT_MODIFICATION`) |
| `smo_shared/testing.py` | `make_test_engine()`, `concurrent_commit_on(table)` (simulates another replica committing first, for 409 tests) |
| `tests/` | See 3.2 |
| `pyproject.toml` | Package `smo-shared` 0.1.0, Python >= 3.11; deps sqlalchemy, psycopg, fastapi, pydantic, httpx, python-multipart, jsonschema |

### 2.2 Data model

None. `db.Base` is the declarative base all modules' `models.py` subclass; no table is defined in this package. The schema is `../migrations/001_init.sql`, checked against the ORM models by `../scripts/check_migration_matches_models.py`.

### 2.3 State machines

`statemachine.py` is the FSM base. Contract:

- `StateMachine[S, E]` holds `transitions: list[Transition]`; instantiate once per model class and reuse.
- `add(from_state, event, to_state, guard=None, action=None)` appends and returns the machine (chainable). `guard(**context) -> bool` is a precondition; `action(**context)` a side effect that runs only for the transition that wins.
- `fire(current_state, event, **context) -> new_state`: collects transitions matching `(current_state, event)`, evaluates their guards in registration order, runs the first passing one's `action` and returns its `to_state`. Raises `IllegalTransition(state, event)` (attributes `.state`, `.event`) if nothing matches or every guard rejects. It does not mutate the entity.
- `legal_events(current_state) -> list[E]`: events with at least one transition from that state, ignoring guards (can contain duplicates when several guarded transitions share an event).

Used by onboarding, rapp-mgmt, ran-nf-oam, nfo and aimgf for their own tables (each in its own `app/statemachine.py`; see those READMEs). `errors.illegal_transition_error(exc, subject)` turns an `IllegalTransition` into 409 `LIFECYCLE_ILLEGAL_TRANSITION` with detail `"<subject>: event <E> is not allowed in state <S>"`.

### 2.4 API: public helpers

**`db`**

| Name | Contract |
|---|---|
| `DATABASE_URL` | `SMO_DATABASE_URL`, required: no default (the process refuses to start without it) |
| `engine`, `SessionLocal` | `create_engine(..., pool_pre_ping=True, future=True)`; `sessionmaker(autoflush=False, autocommit=False)`. Created at import (the import fails with `MissingDatabaseUrl` when `SMO_DATABASE_URL` is unset, except under pytest, where it is an in-memory SQLite); no connection until used |
| `Base` | `DeclarativeBase` shared by all models |
| `session_scope()` | Context manager: commit on success, rollback and re-raise on exception, always close |
| `get_session()` | FastAPI dependency: yields a session, always closes; never commits (the route must) |

Unit tests override the dependency with a `make_test_engine()` session.

**`errors`**

| Name | Contract |
|---|---|
| `ProblemDetails` | Pydantic: `type` (default `about:blank`), `title`, `status`, `detail`, `instance` |
| `problem(status, title, detail=None)` | Returns (does not raise) an `HTTPException(status_code, detail=<ProblemDetails dict>)`; routes `raise problem(...)` |
| `FrameworkError` | Class of `(CODE, http_status)` tuples. Full list in 2.7 |
| `framework_error(code, detail=None)` | `problem(status, title=CODE, detail)` |
| `illegal_transition_error(exc, subject)` | See 2.3 |

The package installs no exception handler. FastAPI therefore serialises these as `{"detail": {"type": "about:blank", "title": CODE, "status": N, "detail": "...", "instance": null}}`: the ProblemDetails object is nested under `detail`, and `type` is always `about:blank` unless a service builds its own response. Callers (the SDK's `SdkError`, the sample rApps) read the nested shape. R1 Termination's own errors (`NO_ROUTE`, `UNAUTHORIZED`) are flat `{title, status}`.

**`pagination`**

| Name | Contract |
|---|---|
| `PageLimit` / `PageOffset` | `Query(100, ge=1, le=500)` / `Query(0, ge=0)`; use as route parameter defaults so every OpenAPI spec documents identical bounds |
| `paginate(db, stmt, limit, offset)` | `COUNT(*)` over `stmt.subquery()` plus `stmt.limit().offset()` executed in SQL. Returns `{"items": [ORM rows], "total", "limit", "offset"}`. Rows are raw ORM objects; the caller maps them: `{**page, "items": [view(r) for r in page["items"]]}`. The statement must carry its own `ORDER BY` for stable paging |

**`correlation`**

| Name | Contract |
|---|---|
| `HEADER_NAME` | `X-Correlation-ID` |
| `apply_correlation_id(app)` | Registers an HTTP middleware: reuse the inbound header, else a fresh UUID4; store it in a `ContextVar` for the request; echo it on the response |
| `get_correlation_id()` | Current id, or `None` outside a request or in a service that did not apply the middleware. `R1Client` uses it to set the header on every downstream call |

**`webhook`**

| Name | Contract |
|---|---|
| `is_safe_webhook_destination(dest)` | True only for `http`/`https` with a hostname that is not `localhost`, `metadata.google.internal` or `metadata`, and not a literal loopback, link-local (includes 169.254.169.254), multicast, unspecified or reserved IP. No DNS resolution |
| `post_webhook(dest, json, timeout=5.0)` | Best-effort POST; returns the `httpx.Response`, or `None` if the destination is missing/unsafe (a warning is logged for an unsafe non-empty one) or the call raises `httpx.HTTPError` |
| `get_webhook(dest, timeout=5.0)` / `delete_webhook(dest, timeout=5.0)` | Same semantics for GET / DELETE (no log line on rejection) |

Rule: any caller-supplied callback URL (`notificationDestination`, `callbackUri`, ...) is called only through these helpers, never a raw `httpx` call.

**`r1_client.R1Client(base_url=R1_GATEWAY_URL, bearer_token=None)`**

| Name | Contract |
|---|---|
| `R1_GATEWAY_URL` | `R1_GATEWAY_URL`, default `http://r1-termination:8000` |
| `get(path, **kw)`, `post(path, json=None, **kw)`, `put(...)`, `patch(...)`, `delete(path, **kw)` | `path` is `/<module>/...`; extra kwargs go to `httpx` (`params`, `files`, `timeout`, ...). Returns the raw `httpx.Response` (no status check, no raise) |
| Auth | Explicit `bearer_token` is used as is and never refreshed. Otherwise the process token: (1) `GET {base}/bootstrap` -> first `tokenEndPoint.uri`; (2) take the module's identity from `SMO_INVOKER_ID`/`SMO_INVOKER_SECRET` if set, else from the `module_identity` row for `MODULE`, else onboard at SME `/invoker-registrations` (label `smo-module:<MODULE>:<random>`) and store it; a replica that loses the race to store offboards its duplicate and adopts the winner's (no `MODULE`, `SMO_MODULE_IDENTITY_STORE=off` or an unreachable database: a per-process identity as before); (3) `client_credentials` grant, scope `smo-internal`. If SME answers 400 it onboards afresh once, replacing the stored identity with a compare-and-swap so only one replica does. Cached until `expires_in` minus 30 s; refreshed once on a 401 and the call retried once. Thread-safe (lock) |
| Failure | Token acquisition errors (`httpx.HTTPError`, no token endpoint, bad body) are logged and the call is sent without `Authorization` |
| Correlation | Adds `X-Correlation-ID` when `get_correlation_id()` is set; adds none otherwise |
| Timeouts | The bootstrap/onboard/grant calls use 5 s; the module call uses `call_timeout()` (30 s, `SMO_HTTP_TIMEOUT_SECONDS`) unless `timeout=` is passed, never httpx's implicit 5 s |

All instances in a process share one identity and token (`_identity`).

#### Single runner (`single_runner.py`)

| Item | Behaviour |
|---|---|
| `run_once_per_interval(name, interval_seconds, fn)` | Any number of replicas may call it on any schedule; across them `fn` runs at most once per interval and the call returns True where it ran. The claim is one atomic `UPDATE periodic_run SET last_run_at = now WHERE name = ... AND last_run_at <= now - interval`, so there is no leader and nothing to clean up after a crash. A raising `fn` gives the claim back (the next tick retries) and propagates |
| `advisory_lock(name)` | Postgres `pg_try_advisory_lock` on a dedicated autocommit connection (a pooled transaction would be ended by the idle-in-transaction limit); yields True if held, False if another session holds it; the server frees it when the holder dies. Always True on SQLite. `run_once_per_interval` holds it during `fn`, so a run longer than the interval is not started twice |
| Who ticks | Not this module: a Kubernetes CronJob, an external scheduler or an endpoint hit on a timer calls it. Nothing in the platform does yet (`HISTORY.md` §10, ST-1.4), and the statelessness guard forbids starting a scheduler inside a service |

#### Probes (`health.py`)

| Item | Behaviour |
|---|---|
| `install_health(app, checks=())` | Adds `GET /live` (always 200 `{"status":"live"}`), `GET /health` (alias of `/live`, `{"status":"healthy"}`) and `GET /ready` |
| `/ready` | Runs every check in parallel; 200 `{"status":"ready","checks":{name:"ok"}}`, or 503 `{"status":"not-ready",...}` where a failing check shows its exception class (`ConnectionError`) or `timeout`, never the message (it can hold a connection string) |
| Checks | A function that raises when its dependency is unusable. `database_check`: `SELECT 1` on the process's engine. `sme_token_check`: `R1Client`'s token (cached, so cheap) can be obtained. Bounded by `READY_CHECK_TIMEOUT_SECONDS` (3) |
| Use | Restart a container on `/live`; take it out of rotation on `/ready`. Adopted by every service except the GUI BFF; SME and focom skip the token check (SME is the issuer, focom calls nobody) |

**`openapi_security.apply_r1_gateway_security(app, *, public_paths=frozenset())`**

Sets `app.version = R1_CONTRACT_VERSION` (`1.0.0`) and replaces `app.openapi` so the generated schema carries `components.securitySchemes.r1BearerAuth` (HTTP bearer, JWT), a global `security` requirement, and `security: []` on every operation under a path in `public_paths` (SME token/introspection, R1 Termination `/health`, `/live`, `/ready` and `/bootstrap`). Declarative only: it adds no runtime check. Applied by 17 services (every R1-facing backend plus R1 Termination); the committed `../docs/openapi/*.json` are generated from it.

**`identity`**

| Name | Contract |
|---|---|
| `rapp_id_from_instance(instance_id)` | `str(UUID)`: `RAppInstance.instanceId` is the framework's rAppId; every producerId, consumerId, api-invoker-id and apfId must be this value |
| `is_framework_internal_identity(identity)` | True when the string is not a UUID (SO SMOS / SA SMOS register as RMIH producers with service-name identities). Used by Intent Service |

**`timeutil.as_utc(dt)`**: returns `dt` unchanged if tz-aware, else `dt` with `tzinfo=UTC`. Needed because SQLite returns `DateTime(timezone=True)` naive; Postgres returns aware values.

**`testing.make_test_engine()`**: in-memory SQLite engine for unit tests: `StaticPool` with `check_same_thread=False` (one shared connection, so `create_all()` and request sessions see the same database), a JSON serializer that encodes `uuid.UUID` (for the `ARRAY(Uuid)` SQLite JSON fallback), and pysqlite implicit transactions disabled (`isolation_level=None`, explicit `BEGIN` on each SQLAlchemy begin) so nested sessions in the cross-service integration suite do not commit each other's work. A production concern it does not have: it is for tests only. Models use Postgres types with `.with_variant(...)` SQLite fallbacks (`ARRAY`, `JSON`, `Uuid`).

### 2.5 Interactions

| Piece | Outbound call | Failure behaviour |
|---|---|---|
| `R1Client` | R1 `/bootstrap`; SME `/invoker-registrations`, `/oauth2/token`; the module call | No token: call sent unauthenticated (R1 401). Transport errors on the module call propagate as `httpx` exceptions to the caller (callers catch them) |
| `webhook` | HTTP to a caller-registered URL | Returns `None`; never raises for `httpx.HTTPError` |
| `correlation` | None (middleware only) | |

No background tasks.

### 2.6 Configuration

| Variable | Default | Where |
|---|---|---|
| `SMO_DATABASE_URL_FILE`, `SMO_DATABASE_PASSWORD`, `SMO_DATABASE_PASSWORD_FILE` | unset | `db.py` via `secretfile.py`: the URL from a file; a password put into the URL (compose gives each module a URL with no password and `SMO_DATABASE_PASSWORD_FILE=/run/secrets/db_password`) |
| `SMO_DATABASE_URL` | none; required: the process refuses to start without it (under pytest only, an in-memory SQLite) | `db.py` |
| `SMO_DB_POOL_SIZE`, `SMO_DB_MAX_OVERFLOW`, `SMO_DB_POOL_TIMEOUT_SECONDS`, `SMO_DB_POOL_RECYCLE_SECONDS` | 5, 10, 30, 1800 (recycle 0: never); Postgres only | `db.py`: the per-process connection pool. N replicas x W workers can hold N x W x (size + overflow) connections |
| `SMO_DB_STATEMENT_TIMEOUT_MS`, `SMO_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` | 30000, 300000 (0: off); Postgres only | `db.py`: server-side limits so a stuck query or a leaked transaction cannot hold a connection for ever |
| `READY_CHECK_TIMEOUT_SECONDS` | 3 | `health.py`: the longest a readiness check may take |
| `SMO_HTTP_TIMEOUT_SECONDS`, `R1_UPSTREAM_TIMEOUT_SECONDS`, `R1_INTROSPECT_TIMEOUT_SECONDS` | 30, 60, 5 | `timeouts.py` (the last two are R1 Termination's) |
| `R1_GATEWAY_URL` | `http://r1-termination:8000` | `r1_client.py` |
| `SMO_INVOKER_ID`, `SMO_INVOKER_SECRET` | unset: the module's shared identity from `module_identity`, registered on first use | `r1_client.py` |
| `SMO_MODULE_IDENTITY_STORE` | `db`; `off` gives each process its own invoker | `r1_client.py` |
| `MODULE` | `unknown` (set by the root `Dockerfile` build arg) | `r1_client.py`, label of the onboarded invoker |

### 2.7 Error codes

`FrameworkError` codes (all `(title, status)`; route files choose when to raise them, so see each module README for conditions).

| Status | Codes |
|---|---|
| 400 | `MODEL_IDENTITY_IMMUTABLE`, `FEATURE_GROUP_NAME_INVALID`, `DATA_JOB_TARGET_IMMUTABLE`, `INVOKER_NOT_REGISTERED` |
| 403 | `MSAC_ACCESS_DENIED`, `NODE_GROUP_NOT_CLEARED`, `APF_NOT_REGISTERED` |
| 404 | `DME_TYPE_NOT_FOUND`, `POLICY_TYPE_NOT_FOUND`, `A1_SERVICE_REGISTRATION_NOT_FOUND`, `MODEL_NOT_FOUND`, `TRAINING_JOB_NOT_FOUND`, `VALIDATION_JOB_NOT_FOUND`, `EMULATION_JOB_NOT_FOUND`, `INFERENCE_JOB_NOT_FOUND`, `MLMF_SUBSCRIPTION_NOT_FOUND`, `PRODUCER_NOT_FOUND`, `TYPE_SUBSCRIPTION_NOT_FOUND`, `DATA_JOB_NOT_FOUND`, `DATA_OFFER_NOT_FOUND`, `DME_ACTION_NOT_FOUND`, `RESOURCE_TYPE_NOT_FOUND`, `RESOURCE_POOL_NOT_FOUND`, `DEPLOYMENT_MANAGER_NOT_FOUND`, `INTENT_HANDLING_FUNCTION_NOT_FOUND`, `INTENT_NOT_FOUND`, `ARTIFACT_VERSION_NOT_FOUND`, `NFDEPLOYMENT_NOT_FOUND`, `RAPP_INSTANCE_NOT_FOUND`, `ASSURANCE_MONITOR_NOT_FOUND`, `PUBLISHING_FUNCTION_NOT_FOUND`, `TRUSTED_INVOKER_NOT_FOUND`, `AUTONOMY_DISPATCH_NOT_FOUND`, `NRM_OBJECT_NOT_FOUND`, `PACKAGE_NOT_FOUND`, `VENDOR_CAPABILITY_NOT_FOUND`, `CM_SCHEMA_NOT_FOUND`, `MANAGED_ENTITY_NOT_FOUND`, `PACKAGE_USAGE_REGISTRATION_NOT_FOUND`, `FEATURE_GROUP_NOT_FOUND` |
| 409 | `CONCURRENT_MODIFICATION`, `IDEMPOTENCY_KEY_IN_PROGRESS`, `PROTOCOL_NOT_SUPPORTED`, `MODEL_NOT_CERTIFIED`, `INFERENCE_MODEL_NOT_ACTIVE`, `MODEL_ALREADY_REGISTERED`, `FEATURE_GROUP_ALREADY_REGISTERED`, `LIFECYCLE_ILLEGAL_TRANSITION`, `TRAINING_JOB_ILLEGAL_TRANSITION`, `SERVICE_NAME_CONFLICT`, `DME_TYPE_VERSION_CONFLICT`, `DME_TYPE_HAS_ACTIVE_PRODUCERS`, `DELIVERY_METHOD_NOT_OFFERED`, `ROLLBACK_HISTORY_UNAVAILABLE`, `NFDEPLOYMENT_NAME_CONFLICT`, `NFDEPLOYMENT_DESCRIPTOR_ALREADY_DEPLOYED`, `NFDEPLOYMENT_ILLEGAL_OPERATION`, `RAPP_INSTANCE_NOT_UNDEPLOYED`, `TRAINING_NOT_APPROVED`, `VALIDATION_NOT_APPROVED`, `AUTONOMY_DISPATCH_NOT_AWAITING_SCOPE`, `INFERENCE_FUNCTION_NOT_ACTIVATED`, `MODEL_NOT_LOADED`, `O1_SERVICE_NOT_SUPPORTED`, `CM_SCHEMA_CONFLICT`, `RAPP_UPGRADE_TIMED_OUT` |
| 415 | `ARTIFACT_FORMAT_INVALID` |
| 422 | `IDEMPOTENCY_KEY_INVALID`, `IDEMPOTENCY_KEY_REUSED`, `SCHEMA_VALIDATION_FAILED`, `COORDINATION_GROUP_MISMATCH`, `COORDINATION_GROUP_TOO_SMALL`, `GOVERNANCE_DECIDER_REQUIRED`, `DIGITAL_TWIN_INFERENCE_NOT_ELIGIBLE`, `DME_ARTIFACT_NOT_FOUND`, `RMIH_CAPABILITY_MISMATCH`, `POLICY_TYPE_NOT_SUPPORTED`, `POLICY_OBJECT_SCHEMA_INVALID`, `SUBSCRIPTION_SCOPE_CONFLICT`, `NFDEPLOYMENT_DESCRIPTOR_NOT_FOUND`, `SECURITY_CONTEXT_INVALID`, `MDA_CAPABILITY_NOT_SUPPORTED`, `FEATURE_GROUP_DME_JOB_REFUSED` |
| 503 | `ENDPOINT_UNREACHABLE` |

Defined but never raised anywhere in the repo: `NODE_GROUP_NOT_CLEARED` (MLLF deploy now stamps node groups instead of checking them).

`problem()` itself accepts any status and title, so modules also raise ad-hoc titles (see each module README, and `gui-bff`, which uses its own flat `{title, status, detail}` helper).

### 2.8 Limits and open items

- `R1Client` and the webhook helpers are synchronous; an async caller would block the event loop.
- Webhook guard: DNS-rebinding residual risk accepted (1.5).
- `db.engine` is built at import from `SMO_DATABASE_URL`; modules that need a different database in tests override `get_session` or use `make_test_engine()`.
- Stale docstrings in the package: `__init__.py` and `db.py` say "fourteen modules"; the build has more services (documentation only, no behaviour).
- No OPEN_ITEMS ids refer to this package.

## 3. Unit tests

### 3.1 Running them

```bash
cd smo/shared && PYTHONPATH=. python -m pytest tests/ -q
```

### 3.2 What is covered

| Test file | Covers | Passed |
|---|---|---|
| `tests/test_correlation.py` | Id generated when the caller sends none; caller's id propagated and echoed; `get_correlation_id()` is `None` outside a request; two requests get distinct ids | 4 |
| `tests/test_r1_client.py` | A caller's own headers ride along and survive the 401 retry, the client's authorization wins; token obtained "the rApp way" (bootstrap, onboarding, client credentials) and attached; token cached across clients and calls; revoked token refreshed once with the same invoker; explicit bearer used as is; SME down means call sent unauthenticated, not raised; correlation header absent outside a request and propagated inside one | 7 |
| `tests/test_webhook.py` | Allowed destinations (http/https, ordinary and private-range hosts); rejected ones (bad scheme, loopback, link-local/metadata, multicast, unspecified, malformed; parametrized); `post_webhook`/`get_webhook`/`delete_webhook` call `httpx` for an allowed destination, no-op for a disallowed one, and `post_webhook` swallows an unreachable destination | 29 |
| `tests/test_versioning.py` | `Versioned` on SQLite and, with `SMO_TEST_POSTGRES_URL`, real Postgres: version starts at 1 and every update bumps it; two sessions firing one transition have exactly one winner; a write to another column also conflicts; the repeat after a conflict is refused as an illegal transition; eight threads racing one transition give one winner; a stale write is a 409 ProblemDetails | 11 (5 need Postgres) |
| `tests/test_idempotency.py` | `@idempotent` on SQLite and, with `SMO_TEST_POSTGRES_URL`, real Postgres: no header runs every time; a repeat replays the first answer and runs nothing; another payload or path is 422; keys are scoped to the caller; a failed attempt is not stored; a running key is 409; an abandoned reservation is taken over; expired records are purged; invalid keys are 422; six threads racing one key run the command once | 27 (13 need Postgres) |
| `tests/test_module_identity.py` | The store on SQLite and, with `SMO_TEST_POSTGRES_URL`, real Postgres (first insert wins; replace is a compare-and-swap; eight racing threads give one winner each) and `R1Client` with a fake SME: replicas and restarts of a module share one invoker; modules do not share; a replica that loses the race offboards its duplicate; an invoker SME forgot is replaced once and the others adopt the replacement; a broken store falls back to per-process; no `MODULE`, store off and an environment identity bypass the store | 20 (6 need Postgres) |
| `tests/test_db_url.py` | The configured URL is used as given; an unset or blank one outside tests is refused with a message naming the variable and `scripts/init_secrets.sh`; under pytest it is an in-memory SQLite, never a server; a real process without the variable exits non-zero on import, and starts with it | 7 |
| `tests/test_single_runner.py` | A repeat inside the interval does not run, one after it does; tasks are independent; a failed run gives the interval back; six racing replicas run the task once; on real Postgres: two sessions cannot hold one lock and it is free afterwards, a dead holder frees it, and a run longer than the interval is not started again elsewhere | 16 (10 need Postgres) |
| `tests/test_secretfile.py` | Value from the variable or the file, trailing newline removed and nothing else trimmed, both set is an error, a missing file names the variable and path; the password from a file is put into a password-less URL (percent-encoded), replaces one already there, the whole URL may come from a file | 11 |
| `tests/test_bodylimit.py` | The cap is exact (at it passes, one byte over is 413); a declared length over it is refused before the app reads; a chunked body is stopped when it passes the cap; per-path overrides; a response already started is not replaced; non-HTTP scopes pass; override parsing; settings from the environment | 9 |
| `tests/test_ratelimit.py` | Burst then rate; `Retry-After` is whole seconds to the next token; callers have separate buckets; a rate of 0 turns it off; settings read on every call; idle buckets are forgotten; eight threads never take more than the burst | 7 |
| `tests/test_health.py` | `/live` and `/health` stay 200 whatever the checks say; `/ready` 200 with all checks passing, 503 naming a failing one without its message; a hung check is `timeout` and does not hang the probe; checks run in parallel; the database check on SQLite and real Postgres, a down database (SQLite path, closed Postgres port) is not ready; the SME token check follows whether a token can be had | 11 (1 needs Postgres) |
| `tests/test_db_engine.py` | `engine_options`: Postgres defaults, every setting from the environment, 0 turns a limit off, SQLite gets none, the pool settings reach the engine; on real Postgres (`SMO_TEST_POSTGRES_URL`): a statement over the limit is cancelled by the server and the pool survives, a session idle inside a transaction is ended, and the control (no limit, same statement completes); the timeout defaults nest | 10 (3 need Postgres) |

### 3.3 What is not covered here

`db`, `errors`, `pagination`, `statemachine`, `identity`, `timeutil`, `openapi_security` and `testing` have no tests in `shared/tests/`; they are exercised through the module suites (e.g. `aimgf/tests/test_statemachine.py`, `onboarding/tests/test_statemachine.py`, every module's `tests/` using `make_test_engine()`, `paginate()` and `framework_error()`) and through `../tests_integration/` (including `test_openapi_specs.py`, which compares each committed `../docs/openapi/*.json` with the live schema). The real Postgres path of `db.py` is covered only by `../scripts/check_migration_matches_models.py`.

## 4. References

- [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md#r1-api-conventions): "R1 API conventions" (the table implemented here) and the golden rules
- [`../docs/call-flows/14-correlation-id-propagation.md`](../docs/call-flows/14-correlation-id-propagation.md): correlation id across a fan-out
- [`../r1-termination/README.md`](../r1-termination/README.md): the gateway `R1Client` calls and the enforcement point for the bearer scheme
- [`../sme/README.md`](../sme/README.md): invoker onboarding, token issuance, introspection
- [`../sdk/README.md`](../sdk/README.md): the rApp-facing client built on `R1Client`
- [`../gui-bff/README.md`](../gui-bff/README.md): the one service that does not use this package
- [`../CLAUDE.md`](../CLAUDE.md): cross-cutting conventions (R1Client, webhook, SQLite test engine)
- [`../OPEN_ITEMS.md`](../OPEN_ITEMS.md)
