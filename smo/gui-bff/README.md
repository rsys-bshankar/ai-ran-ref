# SMO Operator GUI BFF (`gui-bff/`)

> The only API the operator console's browser talks to: it owns GUI users, sessions, role-based access and the audit log, and forwards allowed calls to R1 Termination with its own SME-issued OAuth2 token.

| | |
|---|---|
| Standards basis | Internal logic (operator-console backend; no standard) |
| R1 route / port | None. Not an R1 service: reached only as `/api/*` through the `gui` nginx (:3000); container :8000, publishes no host port |
| Depends on (over R1) | R1 Termination (`/bootstrap`, `/health`, every proxied `/<module>/...`) and, for its own token, SME's `/invoker-registrations` and `/oauth2/token` (URL discovered via R1 `/bootstrap`, or `SME_URL`) |
| Called by | The GUI SPA (`../gui/`), and scripts via `POST /api/token` |
| Database tables | `gui_user`, `gui_audit_log`, `gui_smo_credential`, `gui_setting`, `gui_login_failure`, `gui_revoked_session`, `gui_oidc_login` (own SQLite/SQLAlchemy store, not the SMO Postgres schema) |
| Unit tests | 876 passed (`tests/`, SQLite, standalone; R1 and SME faked with `httpx.MockTransport`; 3 more run only when `SMO_TEST_POSTGRES_URL` is set) |
| Status | Done. OIDC login (SEC-6, opt-in) is built; open: SEC-6.8 (LDAP bind, optional) and SEC-7.5 (admin revokes a user's sessions). Sessions are signed JWTs with a logout revocation list. Several instances work against one shared `GUI_DATABASE_URL`: signing key, lockout counters and SME credential live in it; see 2.8 |

The console as a whole (pages, screenshots, role matrix, run instructions, `GUI_*` quick reference) is described in [`../gui/README.md`](../gui/README.md). This file documents only the BFF's own design and does not repeat the role tables there; the authoritative permission table is `app/rbac.py`.

## 1. High-level design (HLD)

### 1.1 Purpose and scope

The BFF exists so that the browser never calls R1 Termination or a module port directly (that would need CORS on every module and bypass role checks). It:

- authenticates GUI users (password login, httpOnly session cookie, or an OAuth2 password-grant Bearer token for scripts), and, when switched on, through an OpenID Connect provider (section 2.9);
- holds the user and role table and checks every proxied call against it (`app/rbac.py`);
- pins identity-bearing request fields to the signed-in GUI user instead of trusting the browser;
- authenticates to the SMO as an ordinary R1 consumer (CAPIF invoker at SME, `client_credentials`);
- writes an append-only audit log;
- aggregates module health.

It implements no SMO domain logic: lifecycle rules stay in the modules, and the BFF is a policy-enforcing pass-through.

### 1.2 Standards basis

None. Reused conventions only: RFC 6749 section 4.3 (resource-owner password grant, `POST /api/token`), OpenID Connect Core 1.0 and Discovery 1.0 with RFC 7636 PKCE and RP-Initiated Logout 1.0 for the optional OIDC login (section 2.9), RFC 7519 (HS256 session JWT, hand-rolled verifier in `app/security.py`), and the CAPIF invoker onboarding plus client-credentials path the SMO already exposes ([`../../specs/5G_APIs/`](../../specs/5G_APIs/) `TS29222_CAPIF_API_Invoker_Management_API.yaml`) for its southbound identity. Its error bodies are `{title, status, detail}` shaped like the R1 ProblemDetails convention but deliberately do not use `smo_shared` (see 1.5).

### 1.3 Position in the platform

```
Browser --/api--> gui (nginx :3000) --> gui-bff --Bearer (SME-issued)--> R1 Termination --> modules
                                           |                                   ^
                                           +-- own SQLite: users, audit, SME credential, sign-ins in flight
                                           +-- (optional) OIDC provider: discovery, JWKS, token endpoint
                                           +-- /bootstrap, SME onboarding + token ---+
```

- Calls: only R1 Termination (and SME's token/registration endpoints at the address R1 advertises), and, with OIDC on, the one configured identity provider (`GUI_OIDC_ISSUER`). Never a module container directly.
- Never touches: the SMO Postgres database, `smo_shared`, any module port.
- Publishes no host port (`docker-compose.yml`).
- It does not set `X-Correlation-ID`; R1 Termination mints one per proxied request (call flow 14).

### 1.4 Ownership

| Owns | Does not own (owner) |
|---|---|
| GUI users, roles, password hashes, session revocation counter | rApp / SMO identities (SME, rApp Management) |
| The RBAC table mapping `METHOD + /<module>/...` to a minimum role | The semantic validity of any proxied request (the target module) |
| Append-only GUI audit log | Platform-side audit or fault data (RAN NF OAM, FOCOM) |
| The BFF's own CAPIF invoker credential at SME | SME's invoker registry and token issuance (SME) |
| Health, readiness and build-version aggregation across modules (`GET /api/modules/status`) | Module health endpoints themselves (each module) |

### 1.5 Design decisions

| Decision | Reason |
|---|---|
| Allowlist RBAC, first match wins, unmatched = refused for everyone | A new module route is unreachable from the GUI until someone adds a rule. Machine-to-machine routes (SME token/registration, DME producer registration, NFO Instantiate, heartbeats, `usage/*`) are simply absent. |
| Role read from the `gui_user` row on every request, never from the JWT | A demotion applies on the next request. |
| JWT carries `ver` = `gui_user.token_version`; bumped on password change/reset and on (de)activation | Existing sessions die at once without a session store. Self password change re-issues the caller's own token. |
| Cookie session plus double-submit CSRF: `smo_csrf` cookie (readable) echoed as `X-CSRF-Token`, compared with the `csrf` claim inside the JWT | Applies only to cookie-authenticated `POST/PUT/PATCH/DELETE`. A Bearer call needs no CSRF (a browser never attaches one by itself). |
| Cookies: `smo_session` HttpOnly, `Path=/api`; `SameSite=Strict`; `Secure` by default (`GUI_COOKIE_SECURE`) | Session unreachable from script and not sent to non-API paths. |
| OIDC (PR-SEC-6): authorization code with PKCE, opt in, one provider; the session after it is the same signed cookie, CSRF token and `jti` as a password login; the identity provider does multi-factor, the BFF has none of its own | Section 2.9 has the flow and what was not taken. |
| HS256 verified with stdlib `hmac`, fixed header, constant-time compare, `exp` required | No `alg` negotiation to get wrong; no JWT library for the BFF's own session token. (The OIDC provider's RS/ES-signed ID tokens are the one place a library is used: PyJWT, with an allowed list of asymmetric algorithms, section 2.9.) |
| scrypt (N=2^14, r=8, p=1, 16-byte salt) password hashes; unknown user verifies against a dummy hash | Response time does not reveal which usernames exist. |
| Local `_problem()` / `_paginate()` instead of `smo_shared` | The BFF's CI job and image deliberately do not depend on `smo_shared`; the BFF must also keep working (login, audit) while the SMO stack is down. |
| Own small store, not the SMO Postgres schema | None of it is SMO domain data. |
| Identity pinning in RBAC rules (`query_overrides`, `json_overrides`) | `ack_user_id`/`clear_user_id`, SA SMOS `requester_is_admin`, CM-write `requestedBy` + `msacRole`, DME `/dme/actions` `requestedBy`, Intent `rmioId`/`requesterId` (`smo-gui`), ASSIST reject `rejectedBy`, energy-saving override `operator` are overwritten from the session. |
| `query_match` checks every value of a repeated parameter | `?event=CERTIFY&event=DEPRECATE` cannot slip past the admin rule whichever value the backend reads. |
| Proxy forwards only `Content-Type` and `Accept`; strips hop-by-hop, `content-length`, `content-encoding`, `set-cookie`, `server`, `date` from the response | Browser credentials never reach R1; an upstream `Set-Cookie` never reaches the browser. |
| BFF JSON responses carry `CSP default-src 'none'; frame-ancestors 'none'`, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` | Nothing the BFF serves may be framed, sniffed or run. |
| Secrets from the environment only; a generated admin password goes to a mode 0600 file, never the log | See `config.py`, `seed_users`. |
| Failure behaviour | SME/token failure: 502 `SMO_AUTH_FAILED`. R1 unreachable: 502 `R1_UNREACHABLE`. Upstream errors pass through verbatim. Exception text is never put in the response `detail`. |

Idempotency: none needed beyond `seed_users` (first boot only, never touches a non-empty user table) and user DELETE (204 whether or not the user existed).

## 2. Low-level design (LLD)

### 2.1 Code map

| File | Responsibility |
|---|---|
| `app/main.py` | `create_app()`: lifespan (DB, seeding, gateway), session/CSRF dependencies, login/token/logout/me/password, `/api/auth/config`, `/api/oidc/login` and `/callback` (user provisioning), `GET /api/permissions`, health aggregation, the RBAC proxy, admin user CRUD, audit listing; `seed_users`, `_write_initial_password` |
| `app/rbac.py` | `Role`, `RANK`, `MODULES`, `Rule`, `RULES` (125 rules), `decide()`, `GUI_RMIO_ID` |
| `app/oidc.py` | `OidcConfig` (validates the settings), `OidcClient` (discovery and JWKS with caching and rotation, authorization URL, code exchange, ID-token validation, group-to-role, end-session URL), `OidcError` and its reason codes |
| `app/security.py` | `hash_password`/`verify_password` (scrypt), `issue_jwt`/`decode_jwt` (HS256) |
| `app/smo_client.py` | `R1Gateway`: token discovery, one-time invoker onboarding, cached `client_credentials` token, `request()` with one refresh on 401, `r1_health()` |
| `app/db.py` | `GuiUser`, `AuditEntry` (with append-only ORM guard), `SmoCredential`, `GuiSetting`, `LoginFailure`, `RevokedSession`, `OidcLogin`, `Database` (`create_all` on start) |
| `app/config.py` | `Settings` from environment; generates `jwt_secret` if unset |
| `scripts/export_permissions.py` | Writes `../gui/src/auth/permissions.fixture.json` from `RULES` |
| `tests/test_main.py`, `tests/test_shared_state.py`, `tests/test_oidc.py`, `tests/test_rbac.py` | See 3.2 |
| `tests/keycloak/smo-realm.json` | The Keycloak realm the browser check signs in against (CI only): client `smo-gui`, groups `smo-admins`, `smo-ops`, `smo-viewers`, four throwaway users |

### 2.2 Data model

Own database (`GUI_DATABASE_URL`, default `sqlite:///./gui-bff.db`; compose uses `sqlite:////data/gui-bff.db` on the `gui_bff_data` volume). Tables are created by `Base.metadata.create_all`; there is no migration and none of them is in `migrations/001_init.sql`.

**`gui_user`**

| Column | Notes |
|---|---|
| `username` | PK; creation validated against `^[a-z][a-z0-9_.-]{1,31}$`; an OIDC user is `oidc:<subject>` (the colon keeps it apart from every local name) |
| `password_hash` | `salt_hex:digest_hex` (scrypt); `!` for a user of the identity provider, which can never verify (no password stored) |
| `role` | `viewer` / `operator` / `admin` |
| `active` | default true |
| `token_version` | seeded users start at 0, a user created through the admin route at a random value (so a user deleted and created again under the same name does not accept the earlier holder's token); bumped on password change/reset and on any `active` change |
| `created_at` | UTC |

**`gui_audit_log`** (append-only: a `before_flush` listener raises `PermissionError` for any dirty or deleted `AuditEntry`)

| Column | Notes |
|---|---|
| `id` | autoincrement PK |
| `at`, `username`, `role` | `username`/`role` nullable (e.g. failed login for an unknown name has no role) |
| `action` | `LOGIN`, `LOGIN_FAILED`, `LOGIN_LOCKED`, `OIDC_LOGIN`, `OIDC_LOGIN_FAILED`, `TOKEN`, `LOGOUT`, `PASSWORD_CHANGED`, `USER_CREATED`, `USER_UPDATED`, `USER_DELETED`, `PROXY`, `DENIED` |
| `method`, `path`, `status_code`, `detail` | proxy calls; `path` includes the query string for mutating calls |

**`gui_oidc_login`** (PR-SEC-6): one OIDC sign-in in flight, written by `GET /api/oidc/login` and deleted by the callback: `state` (PK), `nonce`, `verifier` (the PKCE code verifier), `binding_hash` (SHA-256 of the value in the browser's `smo_oidc` cookie), `expires_at` (indexed; ten minutes). In the database, not in a process, because the callback may land on another instance. Contains no personal data. Expired rows are removed whenever a sign-in starts; at most 5000 may wait (the route is unauthenticated).

**`gui_smo_credential`** (one row, `id=1`): `api_invoker_id`, `onboarding_secret`. Stored as issued because it must be presented to SME's token endpoint, so it cannot be hashed here.

### 2.3 State machines

None: stateless. The only state is per-user `token_version` and `active` (no transitions beyond what 2.4 lists), plus the SME token cache (2.5). The signing key, the login-failure counters and the SME credential are database rows, shared by every instance of the database.

### 2.4 API

Every list route also takes the optional `total` (boolean, default `true`, the shared `smo_shared.pagination` parameter): `total=false` skips the `COUNT(*)` of the whole result, leaves `total` out of the envelope and adds `hasMore`.

All routes are under `/api`. OpenAPI is served at `/api/openapi.json` (docs/redoc disabled); there is no committed `../docs/openapi/gui-bff.json`.

**Auth and session**

| Method | Path | Purpose | Notable errors |
|---|---|---|---|
| GET | `/api/auth/config` | No session. `{localLogin, oidc: {enabled, providerName, loginUrl}}`: what the sign-in page offers | none |
| POST | `/api/login` | `{username, password}` -> sets `smo_session` + `smo_csrf` cookies; returns `{username, role, csrfToken}` | 401 `INVALID_CREDENTIALS`; 429 `TOO_MANY_ATTEMPTS`; 403 `LOCAL_LOGIN_DISABLED` |
| GET | `/api/oidc/login` | Starts an OIDC sign-in: 302 to the provider's authorization endpoint (state, nonce, PKCE S256 challenge) and sets the `smo_oidc` binding cookie | 404 `OIDC_DISABLED`; otherwise a redirect to `/login?oidc_error=<code>` |
| GET | `/api/oidc/callback` | `code`, `state` from the provider: validates, signs the user in, 303 to `/` with `smo_session` + `smo_csrf`; on any failure 303 to `/login?oidc_error=<code>` and an `OIDC_LOGIN_FAILED` audit row | 404 `OIDC_DISABLED`; codes: `idp_unavailable`, `idp_error`, `access_denied`, `invalid_state`, `token_exchange_failed`, `token_invalid`, `no_role`, `account_disabled`, `too_many_logins` |
| POST | `/api/token` | Form `grant_type=password`, `username`, `password` -> `{access_token, token_type: Bearer, expires_in}` | 400 `{"error": "unsupported_grant_type"}` or `{"error": "invalid_grant"}` (a lockout keeps status 429); 403 `unauthorized_client` when local login is off |
| POST | `/api/logout` | Clears both cookies, revokes the session, audits `LOGOUT` if the cookie was valid; for an OIDC user whose provider advertises an `end_session_endpoint` the answer also holds `endSessionUrl`, where the SPA sends the browser | none (needs no session or CSRF) |
| GET | `/api/me` | `{username, role, csrfToken}` | 401 `UNAUTHENTICATED` / `SESSION_REVOKED` |
| POST | `/api/me/password` | `{currentPassword, newPassword (min 8)}`; bumps `token_version`, re-issues the caller's session | 400 `INVALID_CREDENTIALS`; 422 on short password |
| GET | `/api/permissions` | `{role, rules[{method, pattern, role, queryMatch}]}`: the RBAC table for the SPA (display only) | 401 |
| GET | `/api/modules/status` | Parallel health probe of `r1-termination` (direct `/health`) and every module in `MODULES` (via R1 `GET /<module>/health`); then, for a module that is live, `/<module>/ready` and `/<module>/version` (R1's own `/ready` and `/version` for the gateway), in parallel; returns `{checkedAt, modules[{module, healthy, latencyMs, statusCode, error, ready, version, buildSha, builtAt}]}`: `ready` is `true`/`false` from 200/503 and `null` when the module did not answer it; `version`, `buildSha` and `builtAt` are `null` for a module that is down or has no `/version` (an older release during a rolling upgrade) | 401 |

**Proxy**

| Method | Path | Purpose | Notable errors |
|---|---|---|---|
| GET POST PUT PATCH DELETE | `/api/smo/{module}/...` | `decide()` then forward to R1 `/{module}/...` with the BFF token; response status and body pass through | 403 `FORBIDDEN` (role too low, or "not exposed through the GUI"); 400 `INVALID_BODY` (an override rule on a non-object body); 502 `SMO_AUTH_FAILED`; 502 `R1_UNREACHABLE` |

Rule evaluation (`decide`): rules are scanned in order; a rule matches on method, a fully anchored path regex (`{id}` = `[^/]+`, so ids cannot span segments) and every `query_match` pair. The first match decides: allowed iff the caller's rank is at least the rule's role. No match: refused with no required role. The single `viewer` rule is the final catch-all `GET /(<modules>)(/.*)?`; the one sensitive read before it (`GET /aimgf/feature-groups` and `/aimgf/feature-groups/{name}`, which carry datalake tokens) requires operator. Of the 132 rules, 70 require operator, 61 admin, 1 viewer. For the per-role summary see [`../gui/README.md`](../gui/README.md#roles); the rule list in `rbac.py` is the source of truth.

Override rules (`Rule.query_overrides` / `json_overrides`), applied before forwarding:

| Route | Forced from the session |
|---|---|
| `PATCH /ran-nf-oam/alarms/{id}/ack` / `.../clear` | query `ack_user_id` / `clear_user_id` = username |
| `POST /ran-nf-oam/config-jobs` | body `requestedBy` = `smo-gui:<user>`, `msacRole` = `admin` for admins else null |
| `POST /dme/actions` | body `requestedBy` = `smo-gui:<user>` |
| `POST /intent-service/intents`, `PATCH .../intents/{id}/admin-state` | body `rmioId` / `requesterId` = `smo-gui` |
| `POST /intent-service/autonomy-dispatches/{id}/reject` | body `rejectedBy` = `smo-gui:<user>` |
| `POST /sa-smos/monitors/{id}/remedial-actions` | query `requester_is_admin` = `true` for admins else `false` |
| `POST /energy-saving-rapp/instances/{id}/cells/{id}/override` | body `operator` = `smo-gui:<user>` |

Forced values replace whatever the browser sent. The role split for `POST /aimgf/models/{id}/advance` is by `event` query value: DEPRECATE, RETIRE, SUBMIT_FOR_APPROVAL, APPROVE, REJECT, CERTIFY, PROMOTE, ROLLBACK are admin; any other event is operator.

**Admin** (all require role `admin`)

| Method | Path | Purpose | Notable errors |
|---|---|---|---|
| GET | `/api/admin/users` | List users | 403 `FORBIDDEN` |
| POST | `/api/admin/users` | `{username, password (min 8), role}` | 400 `INVALID_USERNAME`; 409 `USER_EXISTS` |
| PATCH | `/api/admin/users/{username}` | Any of `role`, `active`, `password`; (de)activation or reset bumps `token_version` | 404 `NO_SUCH_USER`; 409 `LAST_ADMIN` |
| DELETE | `/api/admin/users/{username}` | 204 (also when the user does not exist). One transaction removes the account and the failed-login counter kept under that name; every token naming the user is refused from then on (`SESSION_REVOKED`). The audit rows that name the user stay (`docs/PRIVACY.md` section 4, the erasure procedure) | 409 `CANNOT_DELETE_SELF`; 409 `LAST_ADMIN` |
| GET | `/api/admin/audit` | Newest first; filters `username`, `action`; `limit` (1-500, default 100), `offset`; returns `{items, total, limit, offset}` | 403 |

### 2.5 Interactions

**Southbound token (`R1Gateway`)**: (1) `GET {R1}/bootstrap` for the `tokenEndPoint` URI (skipped when `SME_URL` is set: `{SME_URL}/oauth2/token`); (2) if `gui_smo_credential` is empty, `POST {sme}/invoker-registrations` with an opaque `apiInvokerPublicKey` and persist the returned `apiInvokerId` + `onboardingSecret`; (3) `POST` the token endpoint with `grant_type=client_credentials`, scope `smo-gui`. If SME answers 400 (invoker unknown, e.g. its DB was reset) the BFF onboards afresh once. The token is cached until `expires_in` minus 30 s (minimum 1 s) under an `asyncio.Lock`. Any `httpx.HTTPError` or non-200 becomes `SmoAuthError`.

**Proxy call**: `R1Gateway.request()` attaches `Authorization: Bearer`; if R1 answers 401 it fetches a fresh token and retries exactly once. Timeout `GUI_UPSTREAM_TIMEOUT_SECONDS`.

**Health probe**: `GUI_HEALTH_TIMEOUT_SECONDS` per module; a `SmoAuthError` reports `error: "auth: no SMO access token"`, a transport error `error: "unreachable"`, and one failing module never fails the endpoint.

**Audit rules**: every sign-in event, user admin action, every `DENIED` proxy call (any method), and every mutating proxied call (`POST/PUT/PATCH/DELETE`, with the final upstream status and the query string; a 502 is recorded with the cause) is written. Allowed reads are not audited.

**Background tasks**: none. In-process state: the login-failure map (`username -> (count, first_failure_time)`) and the cached SME token.

**Login lockout**: 5 failures (`MAX_LOGIN_FAILURES`) inside 300 s (`LOCKOUT_SECONDS`) lock the account for the rest of that window (429 `TOO_MANY_ATTEMPTS`, audited `LOGIN_LOCKED`); a success clears the counter. The counters are rows in `gui_login_failure`, keyed by the username as typed (an unknown name is counted and locked exactly like a real one, so the lockout does not reveal which usernames exist), updated atomically in SQL, so every instance sharing the database counts the same failures.

### 2.6 Configuration

All read in `app/config.py` at import time.

| Variable | Default | Meaning |
|---|---|---|
| `R1_URL` | `http://r1-termination:8000` | R1 Termination base URL |
| `SME_URL` | unset (discovered via R1 `/bootstrap`) | Override SME base for onboarding and token |
| `GUI_DATABASE_URL` | `sqlite:///./gui-bff.db` | Users, audit, SME credential (compose: `sqlite:////data/gui-bff.db`) |
| `GUI_JWT_SECRET` | unset: the first instance generates a key and stores it in `gui_setting`; every instance of the database (and every restart) then uses that one (warning logged) | HS256 key |
| `GUI_SESSION_TTL_SECONDS` | `28800` | JWT and cookie lifetime |
| `GUI_COOKIE_SECURE` | `true` | `Secure` flag on both cookies |
| `GUI_ADMIN_PASSWORD` | random, written to `GUI_INITIAL_PASSWORD_FILE` | Seeds `admin` on first boot (user table empty) |
| `GUI_INITIAL_PASSWORD_FILE` | `./initial-admin-password` (compose: `/data/initial-admin-password`) | Mode 0600 file for a generated password |
| `GUI_OPERATOR_PASSWORD`, `GUI_VIEWER_PASSWORD` | unset: user not created | Seeds `operator` / `viewer` |
| `GUI_HEALTH_TIMEOUT_SECONDS` | `3` | Per-module health probe |
| `GUI_UPSTREAM_TIMEOUT_SECONDS` | `30` | Proxied call |

Constants in code: `MIN_PASSWORD_LENGTH` 8, `MAX_LOGIN_FAILURES` 5, `LOCKOUT_SECONDS` 300, cookie names `smo_session` / `smo_csrf`, header `X-CSRF-Token`.

### 2.7 Error codes

The BFF answers `{"title", "status", "detail"?}` (no `type` / `instance`). Errors raised from the session dependencies (`UNAUTHENTICATED`, `SESSION_REVOKED`, `CSRF_TOKEN_INVALID`, admin `FORBIDDEN`) are `HTTPException`s, so FastAPI nests that object under a top-level `detail` key; errors returned directly (`_problem`) are flat. Upstream R1/module error bodies pass through unchanged.

| `title` | Status | When |
|---|---|---|
| `INVALID_CREDENTIALS` | 401 / 400 | Bad login (401); wrong `currentPassword` on password change (400) |
| `TOO_MANY_ATTEMPTS` | 429 | Account locked after repeated failures |
| `UNAUTHENTICATED` | 401 | No or invalid/expired token |
| `SESSION_REVOKED` | 401 | User deleted or deactivated, or `ver` no longer matches |
| `CSRF_TOKEN_INVALID` | 403 | Cookie-authenticated unsafe method without a matching `X-CSRF-Token` |
| `FORBIDDEN` | 403 | Role too low, route not in the table, or admin-only admin route |
| `INVALID_BODY` | 400 | Override rule applies and body is not a JSON object |
| `SMO_AUTH_FAILED` | 502 | Could not obtain an SME token |
| `R1_UNREACHABLE` | 502 | Transport failure to R1 |
| `INVALID_USERNAME`, `USER_EXISTS`, `NO_SUCH_USER`, `LAST_ADMIN`, `CANNOT_DELETE_SELF` | 400 / 409 / 404 / 409 / 409 | User administration |
| `OIDC_USER` | 409 | An admin tried to set a password on a user of the identity provider |
| `LOCAL_LOGIN_DISABLED` | 403 | `POST /api/login` with `GUI_LOCAL_LOGIN_ENABLED=false` |

### 2.8 Limits and open items

- Sessions are signed tokens with no session list: `POST /api/logout` clears the cookies and records the token's `jti` in `gui_revoked_session` until the token would expire, so a copied cookie or Bearer token stops working on every instance. There is still no way for an admin to list or end one user's sessions short of deactivating or deleting the user or resetting the password (`SEC-7.4`, `SEC-7.5`).
- Running more than one instance needs one shared database: set `GUI_DATABASE_URL` to the same Postgres (or similar) for all of them. The default SQLite file belongs to one instance, and two instances on separate files would have separate users. When seeding on first boot, set `GUI_ADMIN_PASSWORD` explicitly: with a generated password each instance writes its own password file, and an instance that loses the seeding race deletes the one it wrote.
- The SME token cache is per process (a token is per process by nature); an expired or revoked token is refreshed once on a 401.
- One OIDC provider at most, no LDAP bind (`SEC-6.8`, optional, open). Users and roles still live in `gui_user`; R1 Termination's own OAuth is unchanged.
- No per-module data validation: an operator can submit anything the module accepts; the BFF checks role only.
- The RBAC regexes are mirrored in the SPA (`../gui/src/auth/permissions.fixture.json`); regenerate with `cd gui-bff && PYTHONPATH=. python scripts/export_permissions.py` after editing `rbac.py` (`test_rbac.py` fails on drift).
- No OPEN_ITEMS id refers to this module.

### 2.9 OIDC login (PR-SEC-6)

Off by default (`GUI_OIDC_ENABLED=false`): nothing below exists then, `/api/oidc/*` answer 404 and `GET /api/auth/config` says `oidc.enabled: false`. Local username/password login is unchanged and stays as the break-glass admin path. Every variable is in `../docs/CONFIGURATION.md`; the ones that matter: `GUI_OIDC_ISSUER`, `GUI_OIDC_CLIENT_ID`, `GUI_OIDC_CLIENT_SECRET[_FILE]`, `GUI_OIDC_REDIRECT_URI` (the public URL of `/api/oidc/callback`, registered at the provider), `GUI_OIDC_SCOPES`, `GUI_OIDC_GROUPS_CLAIM`, `GUI_OIDC_GROUP_ROLE_MAP`, `GUI_OIDC_DEFAULT_ROLE`, `GUI_OIDC_PROVIDER_NAME`, `GUI_OIDC_POST_LOGOUT_REDIRECT_URI`, `GUI_LOCAL_LOGIN_ENABLED`. `OidcConfig.from_settings` checks the combination when the app is built, so a missing issuer, a non-https URL (http only for localhost or with `GUI_OIDC_ALLOW_HTTP`), `openid` missing from the scopes, a bad role in the map, or "nobody could sign in" (no map and no default role) stops the start and names the variable.

**The flow.**

1. The sign-in page (`gui/src/pages/Login.tsx`) asks `GET /api/auth/config` and, when OIDC is on, shows a link "Sign in with <provider name>" to `/api/oidc/login` (a full navigation, not a fetch).
2. `GET /api/oidc/login` creates `state`, `nonce`, a PKCE verifier (S256 challenge) and a browser-binding value; stores `state`, `nonce`, the verifier and the SHA-256 of the binding value in `gui_oidc_login`; sets the binding value as the cookie `smo_oidc` (HttpOnly, `SameSite=Lax` because the provider's redirect back is a cross-site navigation that `Strict` would not carry, `Path=/api/oidc`, ten minutes); and answers 302 to the provider's authorization endpoint, found by discovery.
3. The provider authenticates the person (and enforces multi-factor: the BFF has none of its own, `SEC-7.1` to `7.3` stay unbuilt) and redirects to `GET /api/oidc/callback?code=...&state=...`.
4. The callback consumes the `gui_oidc_login` row by `state` (a `DELETE` whose row count decides, so a replay, an expired state or a second instance racing for it finds nothing), checks the `smo_oidc` cookie against `binding_hash` (so a callback URL planted in another browser, login CSRF, does not work), exchanges the code at the token endpoint with the verifier (client authentication `client_secret_basic`, or `client_secret_post` when that is all the provider offers, or none for a public client), and validates the ID token.
5. **ID-token validation** (`OidcClient.validate_id_token`): the algorithm must be one of RS256/384/512, PS256/384/512, ES256/384/512 (so `none` and an HMAC keyed with the public key are refused before any key is looked at); the signature is checked against the JWKS key with the token's `kid`; `iss` equals the configured issuer; `aud` contains the client id, and `azp` equals it when present or when there are several audiences; `exp`, `iat`, `sub`, `iss`, `aud` are required, with 30 s of clock leeway; `nonce` equals the one this sign-in started with; `at_hash`, when the token has one, matches the access token.
6. **Role.** The claim named by `GUI_OIDC_GROUPS_CLAIM` (a list, or a string split on spaces and commas; a dotted name reaches into an object, such as `realm_access.roles`) is looked up in `GUI_OIDC_GROUP_ROLE_MAP` (`group=role,...`); the highest role matched wins; none matched gives `GUI_OIDC_DEFAULT_ROLE`, which is empty by default, and empty means refused (`no_role`). The ID token alone is used: the userinfo endpoint is not called.
7. **User.** Provisioned just in time as `gui_user` `oidc:<subject>` with `password_hash = "!"` (no password is stored and none can verify, so the row cannot be used at `/api/login`). The role is set from the token at every sign-in, and a changed role is noted in the audit row; a role an admin sets by hand on the Users page is therefore overwritten at the next sign-in. A user with `active = false` is refused (`account_disabled`). A user who signs in with no mapped group has the `token_version` of an existing row bumped, which ends that person's earlier sessions. An admin cannot set a password on such a user (409 `OIDC_USER`); deleting the user works as for any other, and the next sign-in creates it again.
8. **Session.** The same as a password login: `issue_session` (HS256 JWT with `ver`, `csrf`, `jti`), `smo_session` and `smo_csrf` cookies set on the 303 to `/`, the `smo_oidc` cookie cleared. Everything after that (CSRF double submit, revocation by `jti`, `token_version`, role read from the row on every request) is the existing machinery.
9. **Audit.** `OIDC_LOGIN` (username `oidc:<subject>`, role, detail `iss=<issuer>` and whether the user was created or the role changed) and `OIDC_LOGIN_FAILED` (the subject when the token was valid enough to have one; detail is a reason code, a validation class name or a claim name, never a token, a code or the provider's text). The browser is only given the reason code, as `/login?oidc_error=<code>`, and the page maps it to its own wording.

**Provider documents.** Discovery (`<issuer>/.well-known/openid-configuration`; its `issuer` must equal the configured one, and the endpoints it names must be https) and the JWKS are fetched with `GUI_OIDC_TIMEOUT_SECONDS` and cached in the process for an hour; a cache of public provider metadata is safe per replica. A `kid` the cache does not know makes one refetch (key rotation), at most once per 30 s so a forged `kid` cannot make the BFF hammer the provider; a provider that cannot be reached leaves the last good copy in use. Only RSA and EC signing keys (`use` absent or `sig`) are kept.

**Logout.** `POST /api/logout` revokes the session as before. For an `oidc:` user, when the provider's discovery document has an `end_session_endpoint`, the answer also carries `endSessionUrl` (`?client_id=...` and, if `GUI_OIDC_POST_LOGOUT_REDIRECT_URI` is set, `post_logout_redirect_uri`), and the SPA sends the browser there (RP-initiated logout) so the provider's session ends too. No `id_token_hint` is sent: the BFF keeps no ID token, so the provider may ask the person to confirm.

**Break-glass.** `GUI_LOCAL_LOGIN_ENABLED` (default `true`) keeps `/api/login` and the `/api/token` password grant; `false` answers both 403 and hides the form, and is refused at start unless OIDC is on (otherwise nobody could sign in).

**Not taken.** Native MFA (the provider does it); storing the ID token (for `id_token_hint`, and to avoid a PII copy); calling the userinfo endpoint; several providers; mapping the e-mail or `preferred_username` to a readable name (a `gui_user` row is keyed by name and the schema is not changed in a minor release, so the subject is the name and the audit row names the issuer); an OIDC back-channel logout; LDAP (`SEC-6.8`).

## 3. Unit tests

### 3.1 Running them

```bash
cd smo/gui-bff && PYTHONPATH=.:../shared python -m pytest tests/ -q
```

(`smo_shared` is not imported; the path is only for parity with the other modules.)

### 3.2 What is covered

| Test file | Covers | Passed |
|---|---|---|
| `tests/test_main.py` | Login cookies (HttpOnly session, readable CSRF, Secure default), wrong password audited, lockout, tampered token, logout, seeding (no password in git, never touches a populated table), viewer/operator/admin gating through the proxy (nothing reaches R1 when denied), identity pinning (ack user, remedial `requester_is_admin`, intent RMIO, CM-write requester/MSAC tier, ASSIST reject, energy-saving override), role change on next request, CSRF on cookie sessions, Bearer grant without CSRF, BFF token (not browser credentials) forwarded, hop-by-hop and `Set-Cookie` stripping, SMO auth failure without leaking exception text, upstream error pass-through, one-time token refresh on 401, audit of mutations and not reads, append-only audit guard, security headers, health aggregation (including SMO auth failure), user admin (create/update/delete, session revocation, last-admin guard, own password change), the erasure of a user end to end and what it leaves in the audit log (STD-4.3), audit listing/filters, `/api/permissions` | 43 |
| `tests/test_shared_state.py` | Instances on one database: a generated signing key is stored once and shared (a session from one instance is accepted by another and survives a restart; separate explicit secrets are not shared, as the control); failed logins count across instances, a success clears them, an unknown name locks like a real one, the window restarts, concurrent failures are all counted; two instances seeding one empty database do not crash and leave one password file; two instances onboarding at once keep one SME invoker and offboard the duplicate; a forgotten invoker is replaced once; instances starting together all create the schema; an old database gains the new tables; the store operations under races on SQLite and Postgres | 21 (3 of them are Postgres variants, skipped without `SMO_TEST_POSTGRES_URL`) |
| `tests/test_oidc.py` | OIDC login against a fake provider (`httpx.MockTransport`, an RSA key generated in the test; the provider checks the redirect URI, the client credentials and the PKCE verifier): the redirect (state, nonce, S256 challenge, binding cookie), the happy path (user created with no password, normal cookies, audit with the subject), role by highest group, role re-evaluated at each sign-in, unknown group refused with no user created, default role, losing every group ends earlier sessions, nested and string group claims, disabled user refused, local login of an OIDC user refused, admin password reset refused; negative: unknown, replayed, expired state, a callback from another browser, wrong nonce, no nonce, wrong audience, wrong `azp`, wrong issuer, expired token, missing `exp`/`sub`, tampered payload, another key's signature, `alg=none`, HS256 keyed with the public key, malformed token, wrong `at_hash`, provider error codes (never echoed), a provider that refuses the code or the PKCE verifier; keys and discovery (cached, rotation picked up, a forged `kid` does not make the BFF refetch each time, an unreachable provider, stale discovery kept, a discovery document for another issuer); logout with and without an end-session endpoint; local login kept, switched off, and refused at start without OIDC; configuration validation (twelve bad settings); the credential from a file; PKCE against the RFC 7636 example; a sign-in started on one instance and finished on another; the CI realm agrees with the workflow and the script | 68 |
| `tests/test_rbac.py` | Every module readable by a viewer; minimum role per route (parametrized, 109 cases total in the file); `event=DEPRECATE` admin-only via `query_match` including duplicated values; unlisted routes refused for everyone; ids cannot span path segments; every mutating rule requires at least operator; SPA permissions fixture equals the live table | 109 |

### 3.3 What is not covered here

- Against a real OIDC provider: the unit tests use a fake one. The browser check `scripts/gui_oidc_e2e.py` signs in through a real Keycloak (CI job `oidc` of `.github/workflows/smo-gui-e2e.yml`, realm `tests/keycloak/smo-realm.json`).
- Against a real R1 Termination / SME / modules: not in `tests_integration/` either; the BFF is exercised only against the fake in `tests/test_main.py` (`FakeSmo`).
- The SPA side (role gating in JavaScript, API helpers): `cd smo/gui && npx vitest run`, including `src/auth/rbac.test.ts` against the shared fixture.
- The whole BFF against a non-SQLite database: only the shared-state operations (stored setting, failed-login counting, SME credential store and replace) run on Postgres, in `tests/test_shared_state.py`.

## 4. References

- [`../gui/README.md`](../gui/README.md): console pages, role matrix, run instructions, screenshots
- [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md): platform baseline; the "Pagination" and "Errors" conventions the BFF intentionally reimplements locally
- [`../docs/call-flows/14-correlation-id-propagation.md`](../docs/call-flows/14-correlation-id-propagation.md): correlation ids minted at R1 Termination
- [`../docs/call-flows/26-model-governance-and-end-of-life.md`](../docs/call-flows/26-model-governance-and-end-of-life.md): the admin-only governance events behind the `advance` rules
- [`../r1-termination/README.md`](../r1-termination/README.md): the gateway the BFF proxies to (route table, token introspection)
- [`../sme/README.md`](../sme/README.md): invoker onboarding and token issuance used by `R1Gateway`
- [`../OPEN_ITEMS.md`](../OPEN_ITEMS.md)
