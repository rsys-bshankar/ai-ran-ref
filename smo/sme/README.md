# SME (`sme/`)

> Service Management and Exposure: the CAPIF-style core where services are published and discovered, API invokers are onboarded, and the bearer tokens that R1 Termination enforces are issued and introspected.

| | |
|---|---|
| Standards basis | O-RAN R1 SME on 3GPP CAPIF (TS 23.222 / TS 29.222) |
| R1 route / port | `/sme` via R1 Termination (container `:8000`) |
| Depends on (over R1) | Caller-registered event callback URLs only; no other module |
| Called by | R1 Termination (`/oauth2/introspect` on every proxied request); every module's `R1Client` (invoker onboarding and `/oauth2/token`); rApps and producers (publish, discover, subscribe); rApp Management (registers a package's declared providers and service APIs per instance); RAN Analytics (producer registration creates a service); GUI BFF |
| Database tables | `service_profile`, `service_authz_policy`, `provider_registration`, `invoker_registration`, `issued_access_token`, `trusted_invoker`, `service_event_subscription` |
| Unit tests | 71 passed (`tests/`, SQLite, standalone) |
| Status | Done for the subset in 1.2. Open: [SA-SME-1-public-key](../OPEN_ITEMS.md), [OI-2-oauth2-scope](../OPEN_ITEMS.md), [OI-5-sme-filters](../OPEN_ITEMS.md) |

## 1. High-level design (HLD)

### 1.1 Purpose and scope

SME gives the platform three things:

1. **Service exposure.** A registered publisher (APF) publishes a service API; a consumer discovers it. One authorization gate decides discoverability: a consumer who is not allowed never learns the service exists.
2. **Identity and tokens.** An API invoker (an rApp container, or an SMO module acting as one) is onboarded, receives a server-generated id and secret, and trades them for a bearer token. R1 Termination asks SME whether that token is active on every call.
3. **Change events.** Subscribers are notified when a service becomes available, changes or goes away.

It also keeps a per-invoker security context (trusted invokers) as CAPIF's security API defines it.

### 1.2 Standards basis

SME is a profile of 3GPP CAPIF (TS 23.222 architecture, TS 29.222 APIs), as R1AP clause 6 requires. Specs in `../../specs/5G_APIs/`:

| CAPIF API | Spec file | Realised as |
|---|---|---|
| Publish Service API | [`TS29222_CAPIF_Publish_Service_API.yaml`](../../specs/5G_APIs/TS29222_CAPIF_Publish_Service_API.yaml) | `POST/DELETE/GET /published-apis/v1/{apfId}/service-apis` |
| Discover Service API | [`TS29222_CAPIF_Discover_Service_API.yaml`](../../specs/5G_APIs/TS29222_CAPIF_Discover_Service_API.yaml) | `GET /service-apis/v1/allServiceAPIs` with `api_name`, `api_version`, `aef_id`, `protocol`, `data_format`, `comm_type` filters (plus the required `api_invoker_id`) |
| API Provider Management | [`TS29222_CAPIF_API_Provider_Management_API.yaml`](../../specs/5G_APIs/TS29222_CAPIF_API_Provider_Management_API.yaml) | `/provider-registrations` (APF enrolment, flattened) |
| API Invoker Management | [`TS29222_CAPIF_API_Invoker_Management_API.yaml`](../../specs/5G_APIs/TS29222_CAPIF_API_Invoker_Management_API.yaml) | `/invoker-registrations` (public-key onboarding, server-generated id and secret) |
| Security API | [`TS29222_CAPIF_Security_API.yaml`](../../specs/5G_APIs/TS29222_CAPIF_Security_API.yaml) | `/trusted-invokers/{id}` (`PUT`, `GET`, `DELETE`, `POST .../update`, `POST .../delete` revocation); token issuance at `/oauth2/token` |
| Events API | [`TS29222_CAPIF_Events_API.yaml`](../../specs/5G_APIs/TS29222_CAPIF_Events_API.yaml) | `/capif-events/v1/{subscriberId}/subscriptions`; events `SERVICE_API_AVAILABLE`, `SERVICE_API_UNAVAILABLE`, `SERVICE_API_UPDATE` |

Deliberate adaptations and omissions:

- Identity is flattened: `apfId == producerId == rAppId`, and an invoker id is the rAppId or an SMO module's own invoker id. There is no provider-domain / function-id hierarchy, so the domain-level collision detection of CAPIF core does not apply. Enrolment is an idempotent upsert on `apfId`.
- Tokens are opaque and server-tracked, not signed JWTs (no IdP is run). Validity is asked through RFC 7662 `POST /oauth2/introspect`, which R1 Termination calls. The token request/response shape follows CAPIF's security token operation (`client_id`, `client_secret`, `grant_type=client_credentials`, `scope`; `access_token`, `expires_in`, `token_type`, `scope`), with RFC 6749 error bodies.
- `ServiceProfile` keeps only the fields discovery filters need from `AefProfile` (`aefId`, `protocol`, `dataFormat`, `versions[].resources[].commType`), stored as JSON; the rest of `aefProfiles` passes through untouched.
- Not implemented: Access Control Policy, Auditing, Logging, Routing Info and Open Discover APIs; `apiCat` discovery filter; event filters on `apiInvokerId` and `aefId` (see 2.8).

### 1.3 Position in the platform

```
 rApp / module R1Client            R1 Termination
   | POST /invoker-registrations      | every proxied call:
   | POST /oauth2/token  (direct)     |   POST /oauth2/introspect {token}
   v                                  v
 +--------------------------------------+
 |                 SME                  |  publish / discover / events / trusted invokers
 +--------------------------------------+
        | event callbacks (best effort)
        v
   subscribers (callbackUri)
```

SME calls nothing but subscriber callbacks. It reads no other module's data, and no module reads SME's tables. The token and invoker endpoints are the one place a caller without a token must reach a service: `/bootstrap` on R1 Termination advertises SME's own address as the token endpoint, so clients call it directly (through the gateway those paths need a token like any other).

### 1.4 Ownership

| Owns | Does not own → owner |
|---|---|
| Service registry (`ServiceProfile`) and the discovery authorization gate | Whether a token is presented and enforced on a call → R1 Termination |
| Provider (APF) enrolment, invoker onboarding, secrets, tokens, introspection | Which instance an rApp is, and its `oauth_client_id` → rApp Management |
| Trusted-invoker security contexts | Declaring which services a package publishes → Onboarding (reads the CSAR); registering them per instance → rApp Management |
| Service change events and subscriptions | Per-role rules for GUI users → GUI BFF |

### 1.5 Design decisions

| Decision | Reason |
|---|---|
| One authorization gate for discovery and notification. A service with an authz policy and a non-empty `allowedConsumers` is returned to (and notified to) only those listed; an empty list means open to all. | CAPIF leaves discover-versus-invoke authorization open; one gate means an unauthorized consumer is never told the service exists. |
| `serviceName` is globally unique. The same producer re-registering updates in place (`SERVICE_API_UPDATE`); a different producer gets `SERVICE_NAME_CONFLICT`. | A conflict is two producers claiming one name, not one producer repeating itself. Unique on `service_name` alone. |
| Registering a service needs prior provider enrolment (`APF_NOT_REGISTERED`, 403). | CAPIF's `IsPublishingFunctionRegistered` gate. |
| The server mints `apiInvokerId` and the onboarding secret; the client supplies only a public key. Every onboarding creates a new invoker. | CAPIF trust direction: a client does not choose its own identity or secret. |
| Onboarding secrets are stored as salted scrypt hashes (`salt:digest`, n=2^14, r=8, p=1); tokens as SHA-256 hashes. Raw values are returned once and never stored. A DB leak yields no reusable credentials. Token hashing uses a fast hash because the token is 256 bits of randomness. | Security review. |
| Token endpoint rejects with 400 (`unsupported_grant_type`, `invalid_client`, `unauthorized_client`) and bodies `{"error", "error_description"}`, not ProblemDetails. | RFC 6749 / CAPIF shape. |
| `GET /trusted-invokers/{id}` redacts `authenticationInfo` and `authorizationInfo` to `""` unless asked per query flag. Invoker listings never expose a secret or its hash. | As CAPIF core. |
| `selSecurityMethod` of a trusted-invoker entry is the caller's first `prefSecurityMethods`; it is not matched against AEF-side capabilities. | No AEF-side security-method catalogue exists here to match against. |
| Revocation (`POST .../delete`) removes only entries matching the notified `aefId` or `apiIds`, and drops the record once none remain. | Implements CAPIF's stated filter semantics directly. |
| Event delivery is best effort via `smo_shared.webhook`; no retry queue. | An unreachable subscriber must never fail a publish. |
| Idempotent deletes: deregistering an unknown or someone else's service, provider, trusted invoker, or subscription is a silent 204 (an unmatched owner is a no-op, nothing is notified). | Same shape as CAPIF core. |

Failure behaviour: SME has no outbound dependency whose failure changes a response. If SME is down, R1 Termination fails closed and every call returns 401.

## 2. Low-level design (LLD)

### 2.1 Code map

| File | Responsibility |
|---|---|
| `app/main.py` | All routes; secret and token hashing; discovery filter and gate; event notification; trusted-invoker validation. |
| `app/models.py` | The tables and `EVENT_TYPES`. |
| `../shared/smo_shared/` | `webhook`, `pagination`, `errors`, `timeutil`, `openapi_security` (exempts `/oauth2/token` and `/oauth2/introspect` from the declared bearer scheme), `r1_client` (the client side of onboarding and token fetch). |

### 2.2 Data model

**`service_profile`**: one row per published service.

| Column | Notes |
|---|---|
| `service_id` (PK, UUID) | Returned as `serviceId`; also the `apiId` used by event filters |
| `service_name` | UNIQUE |
| `producer_id` | The publishing `apfId` |
| `endpoint`, `version`, `full_api_versions`, `service_capabilities`, `selection_criteria` | |
| `module_scope` | Required on registration; stored, not used in any query |
| `aef_profiles` (JSON list), `api_supp_feats`, `shareable_info` | CAPIF `ServiceAPIDescription` parts |

**`service_authz_policy`**: PK and FK `service_id` (cascade); `allowed_consumers` (array; JSON on SQLite); `gates_discovery_visibility` (default true). Created with every registration; the flag has no API to change it.

**`provider_registration`**: `apf_id` (PK), `provider_domain_info`.

**`invoker_registration`**: `api_invoker_id` (PK, `api-invoker-<uuid>`), `public_key` (stored, never used), `onboarding_secret_hash`.

**`issued_access_token`**: `access_token_hash` (PK), `api_invoker_id`, `expires_at`. Rows are never purged.

**`trusted_invoker`**: `api_invoker_id` (PK), `notification_destination`, `request_test_notification`, `security_info` (JSON list of `{aefId, apiId, authenticationInfo, authorizationInfo, prefSecurityMethods, selSecurityMethod}`).

**`service_event_subscription`**: `subscription_id` (PK), `subscriber_id`, `event_types` (array), `callback_uri`, `api_ids` (array, null).

No cross-module references.

### 2.3 State machines

None: stateless as to lifecycle. The only time-dependent state is token validity: a token is active until `expires_at` (issuance plus `ACCESS_TOKEN_TTL_SECONDS` = 3600); there is no revocation route.

### 2.4 API

**Provider enrolment**

| Method | Path | Purpose | Notable errors |
|---|---|---|---|
| POST | `/provider-registrations` (201) | `{apfId, providerDomainInfo?}`; idempotent upsert; returns `{apfId}` | |
| GET | `/provider-registrations` | Paged; each item has `serviceCount` | |
| DELETE | `/provider-registrations/{apf_id}` (204) | Idempotent | |

**Invoker onboarding and tokens**

| Method | Path | Purpose | Notable errors |
|---|---|---|---|
| POST | `/invoker-registrations` (201) | `{apiInvokerPublicKey}` → `{apiInvokerId, onboardingSecret}`; always creates a new invoker | |
| GET | `/invoker-registrations` | Paged: `apiInvokerId`, `apiInvokerPublicKey`, `trusted` | |
| POST | `/oauth2/token` | `{grant_type: client_credentials, client_id, client_secret, scope?}` → `{access_token, expires_in, token_type: Bearer, scope}`; `scope` is echoed, not checked | 400 `unsupported_grant_type`, `invalid_client`, `unauthorized_client` |
| POST | `/oauth2/introspect` | `{token}` → `{active: false}` or `{active: true, client_id, exp}`; unauthenticated | |

**Trusted invokers (CAPIF security contexts)**

| Method | Path | Purpose | Notable errors |
|---|---|---|---|
| PUT | `/trusted-invokers/{id}` (201) | Create or replace `{notificationDestination, requestTestNotification, securityInfo[]}` | 400 `INVOKER_NOT_REGISTERED`; 422 `SECURITY_CONTEXT_INVALID` (blank destination, empty `securityInfo`, an entry without `prefSecurityMethods`) |
| GET | `/trusted-invokers/{id}?authentication_info=&authorization_info=` | Secrets redacted unless the flag is set | 404 `TRUSTED_INVOKER_NOT_FOUND` |
| GET | `/trusted-invokers` | Paged | |
| POST | `/trusted-invokers/{id}/update` | Update in place; does not re-check invoker onboarding | 404; 422 |
| POST | `/trusted-invokers/{id}/delete` (204) | Revocation by `{aefId?, apiIds[], apiInvokerId, cause}` (`cause`: `OVERLIMIT_USAGE` or `UNEXPECTED_REASON`) | 404; 422 (empty `apiIds`) |
| DELETE | `/trusted-invokers/{id}` (204) | Idempotent | |

**Publish and discover**

| Method | Path | Purpose | Notable errors |
|---|---|---|---|
| POST | `/published-apis/v1/{apf_id}/service-apis` (201) | Register or update; body: `serviceName, producerId, endpoint, version, moduleScope, fullApiVersions, serviceCapabilities, selectionCriteria, allowedConsumers, aefProfiles, apiSuppFeats, shareableInfo`. The path `apf_id` is the producer; the body `producerId` is not used for ownership. Returns `{serviceId}`. | 403 `APF_NOT_REGISTERED`; 409 `SERVICE_NAME_CONFLICT` |
| GET | `/published-apis/v1/{apf_id}/service-apis` | The publisher's own services (bare list). A publisher with services always gets them; one with none gets `[]` if enrolled | 404 `PUBLISHING_FUNCTION_NOT_FOUND` (not enrolled and no services) |
| DELETE | `/published-apis/v1/{apf_id}/service-apis/{service_id}` (204) | Owner only; a different `apf_id` is a silent no-op. Notifies `SERVICE_API_UNAVAILABLE`. | |
| GET | `/service-apis/v1/allServiceAPIs?api_invoker_id=&api_name=&api_version=&aef_id=&protocol=&data_format=&comm_type=` | Discovery (bare list). `api_invoker_id` is required and taken as given. A service is returned if it has no policy or `allowedConsumers` is empty or contains the invoker, and (when any AEF filter is given) at least one `aefProfile` matches all given AEF filters. | |

**Events**

| Method | Path | Purpose | Notable errors |
|---|---|---|---|
| POST | `/capif-events/v1/{subscriber_id}/subscriptions` (201) | `{subscriberId, eventTypes[], callbackUri, apiIds?}` → `{subscriptionId}` | 422 `SUBSCRIPTION_SCOPE_CONFLICT` (an event type outside `EVENT_TYPES`) |
| GET | `/capif-events/v1/{subscriber_id}/subscriptions` | Paged; a build addition (CAPIF defines only `POST`) | |
| DELETE | `/capif-events/v1/{subscriber_id}/subscriptions/{subscription_id}` (204) | Only if the subscriber matches; otherwise a silent no-op | |

**Other**: `GET /health`.

### 2.5 Interactions

| Direction | Call | When | Failure behaviour |
|---|---|---|---|
| in | `POST /oauth2/introspect` from R1 Termination | every proxied request | n/a |
| out, webhook | `POST {callbackUri}` `{serviceId, eventType}` (5 s) | register (`SERVICE_API_AVAILABLE`), re-register (`SERVICE_API_UPDATE`), deregister (`SERVICE_API_UNAVAILABLE`, sent before the row is deleted) | Best effort; no retry or backoff queue. A subscriber is skipped if its `eventTypes` lacks the event, its `apiIds` filter lacks the service, or the service's policy lists consumers and the subscriber is not one of them. |

Matching rule for the last case: the subscriber id is compared with `allowedConsumers` exactly as the discovery gate compares `api_invoker_id`.

### 2.6 Configuration

SME reads no environment variable of its own. Through `smo_shared`: `SMO_DATABASE_URL` (default `postgresql+psycopg://smo:smo@postgres:5432/smo`). Constants in code: `ACCESS_TOKEN_TTL_SECONDS = 3600`; scrypt parameters `n=2**14, r=8, p=1, dklen=32`.

### 2.7 Error codes

| Code | Status | When |
|---|---|---|
| `APF_NOT_REGISTERED` | 403 | Publishing without provider enrolment |
| `SERVICE_NAME_CONFLICT` | 409 | `serviceName` held by a different producer |
| `PUBLISHING_FUNCTION_NOT_FOUND` | 404 | Own-services query for an unenrolled `apf_id` with no services |
| `SUBSCRIPTION_SCOPE_CONFLICT` | 422 | Unknown event type (the name is shared with A1; here it means an invalid `eventTypes`) |
| `INVOKER_NOT_REGISTERED` | 400 | `PUT /trusted-invokers` for an invoker never onboarded |
| `TRUSTED_INVOKER_NOT_FOUND` | 404 | Read, update or revoke without a context |
| `SECURITY_CONTEXT_INVALID` | 422 | Malformed `ServiceSecurity` or `SecurityNotification` |
| `unsupported_grant_type`, `invalid_client`, `unauthorized_client` | 400 | `/oauth2/token` (body `{"error": ...}`, not ProblemDetails) |
| FastAPI request validation | 422 | Missing or mistyped fields |

### 2.8 Limits and open items

- Invoker `public_key` is stored but no signature is verified; the secret is the only credential ([SA-SME-1-public-key](../OPEN_ITEMS.md)).
- `scope` on the token request is echoed, not checked against published AEFs or APIs; tokens are opaque, with no IdP ([OI-2-oauth2-scope](../OPEN_ITEMS.md)).
- Event filters on `apiInvokerId` and `aefId`, and a `category` discovery filter, are missing ([OI-5-sme-filters](../OPEN_ITEMS.md)).
- Discovery does not verify that `api_invoker_id` is an onboarded invoker or the caller's own identity.
- Expired tokens are not purged; invokers and tokens cannot be revoked or deleted.
- `gates_discovery_visibility` is always true; `module_scope` is stored only.
- No retry for event delivery.
- Out of scope: CAPIF Access Control Policy, Auditing, Logging, Routing Info, Open Discover.

## 3. Unit tests

### 3.1 Running them

```bash
cd smo/sme && PYTHONPATH=.:../shared python -m pytest tests/ -q
```

### 3.2 What is covered

| Test file | Covers | Count |
|---|---|---|
| `tests/test_main.py` | Service registry and discovery: register, name conflict, same-producer update, authorization gate (hidden / shown / unrestricted), deregistration ownership, `api_name` / `api_version` / AEF / protocol / data-format / comm-type filters | 16 |
| | Event subscriptions and notifications: event-type validation, unsubscribe ownership, matching subscribers only, update / unavailable events, `apiIds` filter, visibility respected, unreachable subscriber | 13 |
| | Provider enrolment and the own-services query | 8 |
| | Invoker onboarding, token issue and introspection (server-generated id and secret, no cleartext secret or token, bad grant / client / secret, expired and unknown tokens) | 12 |
| | Trusted invokers: validation, replace, redaction and reveal, update, revocation (by `aefId`, whole record), 404s | 17 |
| | Registry reads (providers with service count, invokers without secrets, trusted invokers, event subscriptions) and health | 5 |
| | Total | 71 |

### 3.3 What is not covered here

- R1 Termination actually introspecting a token issued by SME, and every module's `R1Client` onboarding and refreshing its token: `tests_integration/` (in-process mesh; the gateway mechanics themselves are tested in `r1-termination/tests`).
- rApp Management registering a package's declared providers and service APIs, and RAN Analytics creating a service: `tests_integration/test_cross_service.py` (e.g. `test_ran_analytics_producer_registration_creates_a_real_sme_service`, `test_real_demo_csar_onboards_and_deploys`).
- PostgreSQL array columns and cascades (unit tests use SQLite).

## 4. References

- Call flows: [18 SME CAPIF security lifecycle](../docs/call-flows/18-sme-trusted-invokers-lifecycle.md), [01 onboarding to deployment](../docs/call-flows/01-rapp-onboarding-to-deployment.md)
- OpenAPI: [`../docs/openapi/sme.json`](../docs/openapi/sme.json)
- Specs: [`../../specs/5G_APIs/`](../../specs/5G_APIs/) (`TS29222_CAPIF_*.yaml`)
- Open items: [`../OPEN_ITEMS.md`](../OPEN_ITEMS.md); audit history: [`../HISTORY.md`](../HISTORY.md)
- Cross-cutting rules: [ARCHITECTURE.md](../docs/ARCHITECTURE.md)
- Related READMEs: [R1 Termination](../r1-termination/README.md) (the token gate), [rApp Management](../rapp-mgmt/README.md) (per-instance registration), [Onboarding](../onboarding/README.md) (CSAR declarations)
