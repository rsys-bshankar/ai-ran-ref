# SMO Operator GUI

A React + TypeScript single-page console for operating the SMO modules and
the four reference rApps, plus its backend-for-frontend (`../gui-bff`).

```
Browser ──► gui (nginx :3000) ──/api──► gui-bff ──Bearer (SME-issued)──► R1 Termination ──► modules
```

- The browser talks **only** to its own origin. `/api` is reverse-proxied
  to the BFF; it never calls R1 Termination or a module port directly (no
  CORS anywhere, and no way around the role checks).
- The **BFF** holds GUI users and roles, issues the session JWT, checks
  every call against its permission table (`../gui-bff/app/rbac.py`), and
  forwards allowed calls to R1 Termination. It authenticates to R1 the way
  an rApp does: the token endpoint advertised by R1's `/bootstrap`, a CAPIF
  invoker identity onboarded once at SME, then `client_credentials`.
- Module routes stay the source of truth: the GUI drives the documented
  FastAPI endpoints (`../docs/openapi/<module>.json`) and doesn't
  reimplement any lifecycle logic.

Screenshots of the pages, tabs and lifecycle flows: [Screenshots](#screenshots).

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
| **Policy & Intents** | `/a1-related/policy-types`, `/a1-related/policies` (+ `{id}`, `{id}/status`), `/a1-related/policies/subscriptions`, `/a1-related/services` (+ `keepalive`) · `/intent-service/intents` (+ `admin-state`), `/intent-reports`, `/intent-handling-functions` · `/intent-service/autonomy-dispatches` (+ `resolve`, `reject`) |
| **Energy Saving**, **Mobility**, **Coverage**, **Traffic Steering** | `/energy-saving-rapp/instances`, `/mobility-optimization-rapp/instances`, `/coverage-optimization-rapp/instances`, `/traffic-steering-rapp/instances` (+ `{id}/dashboard`, `evaluate`, `reconcile`, per-cell or per-relation views); see `../DEMO_RUNBOOK.md` §24–§27 |
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
- A1-ML (dormant in A1 Related).
- Southbound Docker/NETCONF beyond what the modules already stub; results
  of inference jobs (pulled through DME, not shown here).
- R1 Termination's own OAuth is unchanged. The GUI's users and roles live in
  the BFF, not an external IdP.

## Screenshots

Captured by a Playwright walk-through against the local stack (modules, R1
Termination, the BFF and the nginx config, on Postgres 16), starting from an
empty database. Every screen is live state
produced by the walk-through itself, signed in as `admin`, at 1440 px wide.

The walk-through drives each call flow in `smo/docs/call-flows` from the GUI
and then visits every page and tab. It finished with no console errors and no
5xx responses. The four reference-rApp pages (Energy Saving, Mobility,
Coverage, Traffic Steering) are not captured yet.

### Lifecycle flows (`/flows`)

| # | Flow | Result |
|---|------|--------|
| 01 | [rApp onboarding → running instance](docs/screenshots/flows/f01.png) | 6/6, complete |
| 02 | [AI/ML model: register → train → certify → deploy → infer → monitor](docs/screenshots/flows/f02.png) | 11/11, complete |
| 03 | [Configuration write, schema-checked, fleet-aware](docs/screenshots/flows/f03.png) | `PARTIAL_SUCCESS`: one of the two MEs is deliberately unreachable |
| 04 | [Closed-loop assurance: monitor → decide → remediate → escalate](docs/screenshots/flows/f04.png) | 5/5, `CONFIG_CHANGE` `RESOLVED` |
| 05 | [A1 EI registration → data consumption](docs/screenshots/flows/f05.png) | 6/6, complete |
| 06 | [Package failure, deprecation and the cascade-delete guard](docs/screenshots/flows/f06.png): [guard blocking delete](docs/screenshots/flows/f06-blocked.png) | 5/5, complete |
| 07 | [rApp fault and performance reporting](docs/screenshots/flows/f07.png) | 5/5, FAULTED → recovered → RUNNING |
| 08 | [RAN Analytics: producer → report → subscriber query](docs/screenshots/flows/f08.png) | 5/5, complete |
| 09 | [Intent registration → fulfilment reporting → admin state](docs/screenshots/flows/f09.png) | 5/5, complete |
| 10 | [SO SMOS multi-step order: INFRA → TRAINING → DEPLOY](docs/screenshots/flows/f10.png) | 4/4, complete |

### Feature close-ups

| Screen | What it shows |
|--------|---------------|
| [Package priming, deprime blocked](docs/screenshots/features/rapps-priming-blocked.png) | PRIMED package with an active usage registration: Deprime is disabled, with the reason given |
| [Package priming, deprimed](docs/screenshots/features/rapps-priming-done.png) | Usage stopped → deprime succeeds → back to AVAILABLE |
| [Coordination groups](docs/screenshots/features/aiml-groups.png) | Create needs at least 2 members (AI/ML Workflow now returns 422 `COORDINATION_GROUP_TOO_SMALL`) |
| [Group-scoped remedial action](docs/screenshots/features/kpis-assurance-group.png) | `SCALE` on a model-group monitor → `RESOLVED` via a group retrain; the new Scope column |
| [SME event subscriptions](docs/screenshots/features/data-sme-events.png) | One unscoped subscription and one limited to a single service by `apiIds` |
| [A1 service supervision](docs/screenshots/features/policy-services.png) | Keep-alive countdown for a supervised service |

### Every page and tab

| Page | Tabs |
|------|------|
| Login | [sign-in](docs/screenshots/pages/login.png) |
| Dashboard | [overview](docs/screenshots/pages/dashboard.png) |
| Lifecycle flows | [flow list](docs/screenshots/pages/flows.png) |
| rApps | [packages](docs/screenshots/pages/rapps-packages.png) · [instances](docs/screenshots/pages/rapps-instances.png) |
| AI/ML | [models](docs/screenshots/pages/aiml-models.png) · [training](docs/screenshots/pages/aiml-training.png) · [inference](docs/screenshots/pages/aiml-inference.png) · [coordination groups](docs/screenshots/pages/aiml-groups.png) · [MLMF](docs/screenshots/pages/aiml-mlmf.png) · [feature groups](docs/screenshots/pages/aiml-features.png) |
| Alarms | [RAN](docs/screenshots/pages/alarms-ran.png) · [O-Cloud](docs/screenshots/pages/alarms-ocloud.png) |
| KPIs & Assurance | [rApp](docs/screenshots/pages/kpis-rapp.png) · [PM](docs/screenshots/pages/kpis-pm.png) · [MLMF](docs/screenshots/pages/kpis-mlmf.png) · [RAN Analytics](docs/screenshots/pages/kpis-analytics.png) · [assurance](docs/screenshots/pages/kpis-assurance.png) · [O-Cloud](docs/screenshots/pages/kpis-ocloud.png) |
| Policy & Intents | [A1 policies](docs/screenshots/pages/policy-a1.png) · [status subscriptions](docs/screenshots/pages/policy-status-subs.png) · [A1 services](docs/screenshots/pages/policy-services.png) · [intents](docs/screenshots/pages/policy-intents.png) · [handlers](docs/screenshots/pages/policy-handlers.png) |
| Infrastructure | [NFO](docs/screenshots/pages/infra-nfo.png) · [O-Cloud](docs/screenshots/pages/infra-ocloud.png) · [topology (TEIV)](docs/screenshots/pages/infra-topology.png) · [O1](docs/screenshots/pages/infra-o1.png) · [service orders](docs/screenshots/pages/infra-orders.png) |
| Data & Exposure | [DME](docs/screenshots/pages/data-dme.png) · [A1 EI](docs/screenshots/pages/data-a1-ei.png) · [SME](docs/screenshots/pages/data-sme.png) |
| Admin | [users](docs/screenshots/pages/admin-users.png) · [audit log](docs/screenshots/pages/admin-audit.png) |

### Drawers and dialogs

| Screen | What it shows |
|--------|---------------|
| [Dashboard with open alarms](docs/screenshots/details/dashboard-with-alarms.png) | Severity tiles and fleet counts once alarms are raised |
| [Alarm](docs/screenshots/details/alarm-drawer.png) | 3GPP TS 28.532 / 28.111 fault fields, lifecycle, Ack / Clear |
| [rApp instance](docs/screenshots/details/instance-drawer.png) | Instance detail and resource provenance, with lifecycle actions |
| [AI/ML model](docs/screenshots/details/model-drawer.png) | Pipeline stepper, metadata, artifact upload, training and inference jobs |
| [NF deployment](docs/screenshots/details/nfo-deployment-drawer.png) | NFO deployment, resources and LCM operations |
| [Resource pool](docs/screenshots/details/resource-pool-detail.png) | FOCOM pool with its resources |
| [CM write job](docs/screenshots/details/config-job-detail.png) | Per-managed-element sub-changes of a `PARTIAL_SUCCESS` job |
| [Config write](docs/screenshots/details/config-write-dialog.png) | Several MEs, scope selection, schema-checked changes |
| [Intent](docs/screenshots/details/intent-drawer.png) | Intent detail, fulfilment reports, admin state |
| [Service order](docs/screenshots/details/service-order-drawer.png) | SO SMOS order steps with their results |
| [One-time invoker secret](docs/screenshots/details/invoker-secret-dialog.png) | SME invoker onboarding: the secret is shown once and never again |

### Role views

Each screen is the same live state seen through a lower role. Actions the
role can't perform aren't rendered, and the BFF refuses them anyway.

| Role | Screens |
|------|---------|
| Viewer | [dashboard](docs/screenshots/roles/viewer-dashboard.png) · [flow 06](docs/screenshots/roles/viewer-flows-06.png) · [rApp packages](docs/screenshots/roles/viewer-rapps-packages.png) · [SME](docs/screenshots/roles/viewer-data-sme.png) |
| Operator | [dashboard](docs/screenshots/roles/operator-dashboard.png) · [rApp instances](docs/screenshots/roles/operator-rapps-instances.png) · [DME](docs/screenshots/roles/operator-data-dme.png) · [A1 policies](docs/screenshots/roles/operator-policy-a1.png) |
