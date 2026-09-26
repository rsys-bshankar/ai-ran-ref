# SMO Operator GUI

A React + TypeScript single-page console for operating the fourteen SMO
modules, plus its backend-for-frontend (`../gui-bff`).

```
Browser ──► gui (nginx :3000) ──/api──► gui-bff ──Bearer (SME-issued)──► R1 Termination ──► modules
```

- The browser talks **only** to its own origin. `/api` is reverse-proxied
  to the BFF; it never calls R1 Termination or a module port directly (no
  CORS anywhere, and no way around the role checks).
- The **BFF** holds GUI users and roles, issues the session JWT, checks
  every call against its permission table (`gui-bff/app/rbac.py`), and
  forwards allowed calls to R1 Termination. It authenticates to R1 the way
  an rApp does: the token endpoint advertised by R1's `/bootstrap`, a CAPIF
  invoker identity onboarded once at SME, then `client_credentials`.
- Module routes stay the source of truth: the GUI drives the documented
  FastAPI endpoints (`../docs/openapi/<module>.json`) and doesn't
  reimplement any lifecycle logic.

Screenshots of every page, tab and lifecycle flow, taken from a live
walk-through: [docs/SCREENSHOTS.md](docs/SCREENSHOTS.md).

## Run it

With the whole stack:

```bash
cd smo
export GUI_ADMIN_PASSWORD='choose-one' GUI_OPERATOR_PASSWORD='…' GUI_VIEWER_PASSWORD='…' GUI_JWT_SECRET="$(openssl rand -base64 48)"
docker compose up --build
# open http://localhost:3000
```

| Variable (gui-bff) | Default | Meaning |
|---|---|---|
| `R1_URL` | `http://r1-termination:8000` | The only southbound the BFF talks to |
| `SME_URL` | discovered via R1 `/bootstrap` | Override the SME token endpoint base |
| `GUI_ADMIN_PASSWORD` | random, written to `GUI_INITIAL_PASSWORD_FILE` | Seeds user `admin` on first boot only |
| `GUI_INITIAL_PASSWORD_FILE` | `/data/initial-admin-password` in compose | Where a generated admin password goes (mode 0600, never logged): `docker compose exec gui-bff cat /data/initial-admin-password`; delete it after changing the password |
| `GUI_OPERATOR_PASSWORD` / `GUI_VIEWER_PASSWORD` | unset → user not created | Seed users `operator` / `viewer` |
| `GUI_JWT_SECRET` | random per boot (sessions end on restart) | HS256 session signing key |
| `GUI_COOKIE_SECURE` | `true` | Set `false` only for plain-http access by a non-localhost name |
| `GUI_DATABASE_URL` | `sqlite:////data/gui-bff.db` (compose volume) | Users, audit log, the BFF's SME credential |
| `GUI_SESSION_TTL_SECONDS` | `28800` | Session lifetime |

No password ever lives in git: seed users are hashed (salted scrypt) from
the environment on the first boot, and never touched again after that.
Manage them afterwards under **Admin → Users & roles**.

Development, against a BFF on `:8090` (itself pointed at a running R1):

```bash
cd smo/gui-bff && R1_URL=http://localhost:8080 GUI_ADMIN_PASSWORD=admin-pass-1 GUI_COOKIE_SECURE=true \
  PYTHONPATH=. uvicorn app.main:app --port 8090
cd smo/gui && npm install && npm run dev     # http://localhost:5173, /api proxied to :8090 (GUI_BFF_URL overrides)
```

Tests:

```bash
cd smo/gui-bff && PYTHONPATH=. python -m pytest tests -q     # auth, CSRF, RBAC, proxy, health, admin/audit
cd smo/gui && npm test && npm run typecheck && npm run build   # Vitest: role gating, API helpers, domain logic
```

`src/auth/permissions.fixture.json` is a snapshot of the BFF's permission
table: the SPA's Vitest suite evaluates it (so the Python regexes provably
behave the same in JS), and the BFF's `test_rbac.py` fails if it drifts.
Regenerate with `cd smo/gui-bff && PYTHONPATH=. python scripts/export_permissions.py`.

## Roles

The BFF is authoritative. The SPA reads the same table (`GET /api/permissions`)
to hide what a role can't do, and the BFF re-checks every proxied call anyway.
A route missing from the table is refused for everyone, admin included (SME
token/registration APIs, DME producer registration, NFO Instantiate, usage
registrations, heartbeats: machine-to-machine only).

| | Viewer | Operator | Admin |
|---|:-:|:-:|:-:|
| Every read: status, lists, details, alarms, KPIs (feature groups excepted: they carry datalake tokens) | ✓ | ✓ | ✓ |
| Lifecycle: onboard/prime/deprime/deprecate packages; create, configure, upgrade, recover, bootstrap instances; register, edit, train, advance, deploy models, inference, training-metrics writeback, feature groups; ack/clear alarms; CM writes, PM subscriptions, SW jobs; A1 policies and policy-status subscriptions, intents, analytics subscriptions; DME consumer data jobs and type subscriptions; SME event subscriptions; FOCOM inventory subscriptions; SO orders; SA monitor evaluate / remediate / escalate; NFO heal/scale | | ✓ | ✓ |
| Hard deletes and teardown: delete packages, terminate/delete instances, deprecate/delete models, delete A1 policies and intents, terminate NF deployments, provision/deprovision O-Cloud resources | | | ✓ |
| Registry administration: SME providers, published service APIs, invoker onboarding (the one-time secret is shown once) and trusted-invoker security contexts; the A1-P service registry; RMIH registration (framework identities only) | | | ✓ |
| Acting as another party, to exercise a flow: DME producer types and offers, A1 EI types, RAN Analytics producers and reports, intent fulfilment reports, package usage registrations, O1 heartbeats, and test alarms, rApp perf/faults and MLMF reports; GUI users and the audit log | | | ✓ |

The BFF also pins identity-bearing parameters rather than trusting the
browser: `ack_user_id` / `clear_user_id` are the GUI user; SA SMOS's
`requester_is_admin` follows the GUI role; a CM write's `requestedBy` is the
GUI user and its MSAC tier (which `entire-RAN` scope requires) is granted to
admins only; Intent Service intents carry RMIO `smo-gui` (so the GUI can change
the admin state of exactly the intents it created).

## Security

- Session JWT in an `HttpOnly; Secure; SameSite=Strict` cookie scoped to
  `/api`. Unsafe methods also need `X-CSRF-Token` matching the claim inside
  the JWT (double submit). Scripts can use `POST /api/token` (OAuth2 password
  grant) and send `Authorization: Bearer`.
- Roles are read from the user table on every request: a demotion applies
  immediately, and a password reset or deactivation revokes existing sessions.
- 5 failed logins lock an account for 5 minutes. Unknown users cost the same
  hash work as known ones.
- nginx: strict CSP (`script-src 'self'`, no inline script), `frame-ancestors
  'none'`, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`,
  `Permissions-Policy`. The BFF: `default-src 'none'` CSP, `no-store`.
- The proxy forwards only `Content-Type`/`Accept`. Browser cookies and
  credentials never reach R1, hop-by-hop headers are stripped both ways, and
  an upstream `Set-Cookie` never reaches the browser.
- Append-only audit log (**Admin → Audit log**): every mutating proxied call
  (allowed or denied, with its status), plus sign-ins and user administration.
- Only `gui` (:3000) and R1 Termination (:8080, for rApps, unchanged) publish
  ports. `gui-bff` publishes nothing, and neither joins `a1_mock_net`.

## Pages and the R1 paths they use

| Page | Module paths (all via `/api/smo/…` → R1) |
|---|---|
| **Lifecycle flows** | All ten `docs/call-flows` journeys, each a live step timeline for a chosen package / model / config job / monitor / EI type / instance / analytics type / intent / order, with the next action on the current step (`lib/flows.ts` holds the step logic). Reads everything the flows touch, including `/onboarding/packages/{id}/usage`, `/dme/offers`, `/dme/data-jobs`, `/a1-related/ei-types`, `/sme/published-apis/v1/{apf}/service-apis` |
| **Dashboard** | BFF `GET /api/modules/status` (every `/<module>/health`, in parallel) · `/ran-nf-oam/alarms` · `/focom/alarms` · `/aimgf/mlmf/reports` · `/rapp-mgmt/instances/{id}/performance` · `/sa-smos/remedial-actions?outcome=ESCALATED` · fleet counts from onboarding, rapp-mgmt, mlmr, nfo, ran-nf-oam, a1-related, intent-service, ran-analytics lists |
| **rApps** (call-flow 01) | `/onboarding/packages` (+ `prime`, `deprime`, `deprecate`, `cancel-delete`, `DELETE`, `artifacts`, `usage` + `start`/`stop`) · `/rapp-mgmt/instances` (+ `GET {id}`, `config`, `bootstrap-complete`, `upgrade`, `upgrade/resolve`, `recover`, `terminate`, `DELETE`, `performance`, `faults`) |
| **AI/ML** (call-flow 02) | `/mlmr/models` (+ `{id}` `PUT`/`DELETE`, `artifact`, `artifact/{v}`) · `/mlmr/coordination-groups` · `/aimgf/models/{id}/(advance?event=\|inference-jobs)` · `/aimgf/training-jobs` (+ `model-metrics`) · `/aimgf/inference-jobs` (+ `resolve`) · `/aimgf/mlmf/subscriptions` (+ `reports`) · `/aimgf/feature-groups` · `/mllf/models/{id}/deploy` · `/dme/dme-types` |
| **Alarms** | `/ran-nf-oam/alarms` (filters `managed_element_ref`, `severity`; `PATCH …/ack`, `…/clear`; admin `alarms/ingest`) · `/focom/alarms` |
| **KPIs & Assurance** | `/rapp-mgmt/instances/{id}/performance` · `/ran-nf-oam/pm-subscriptions` · MLMF as above · `/mdaf/reports`, `subscriptions` · `/ran-analytics/producers` (and, as admin, registering producers and publishing reports via `/mdaf/reports`) · `/sa-smos/monitors` (+ `evaluate`, `remedial-actions`, `escalate`), `/sa-smos/remedial-actions` · `/focom/performance` |
| **Policy & Intents** | `/a1-related/policy-types`, `/a1-related/policies` (+ `{id}`, `{id}/status`), `/a1-related/policies/subscriptions`, `/a1-related/services` (+ `keepalive`) · `/intent-service/intents` (+ `admin-state`), `/intent-reports`, `/intent-handling-functions` |
| **Infrastructure** | `/nfo/deployments` (+ `heal`, `scale`, `resources`, `operations`, `DELETE`), `/nfo/descriptors` · `/focom/resource-pools` (+ `resources`), `resource-types`, `deployment-managers`, `topology`, `resources/provision`, `inventory/subscriptions` · `/ran-nf-oam/o1-adaptor-endpoints` (+ `discover`, `heartbeat`), `config-jobs` (several MEs per job), `software-management-jobs` (+ `advance`) · `/so-smos/orders` (+ `cancel`) |
| **Data & Exposure** (call flows 01, 05, 08) | `/dme/dme-types`, `production-capabilities`, `data-jobs`, `offers` (+ `notify`), `type-subscriptions` · `/a1-related/ei-types` (+ `register`) · `/sme/provider-registrations`, `published-apis/v1/{apf}/service-apis`, `invoker-registrations`, `trusted-invokers`, `service-apis/v1/allServiceAPIs`, `capif-events/v1/{subscriber}/subscriptions` |
| **Admin** | BFF `/api/admin/users`, `/api/admin/audit` |

Polling: alarms every 5 s, module health every 10 s, lists every 15 s
(TanStack Query). Any lifecycle action refetches every SMO read, since one
call often changes another module's state.

## Out of scope (Phase 1)

- Alarm-storm correlation (alarms carry `correlationGroup` /
  `correlatedNotifications`, but the GUI doesn't compute storms).
- Live KPI file collection: PM subscriptions register DME producer types,
  and the counters themselves aren't collected (as in RAN NF OAM).
- A1-ML (dormant in A1 Related), and SA SMOS `ROLLBACK` (the module returns
  `ROLLBACK_HISTORY_UNAVAILABLE`, which the GUI shows as-is).
- Southbound Docker/NETCONF beyond what the modules already stub; results
  of inference jobs (pulled through DME, not shown here).
- R1 Termination's own OAuth is unchanged. The GUI's users and roles live in
  the BFF, not an external IdP.
