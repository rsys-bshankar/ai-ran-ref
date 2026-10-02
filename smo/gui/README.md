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

Captured by a Playwright walk-through of the full stack (every module, R1
Termination, the BFF and the GUI, on Postgres), starting from an empty
database. The state on screen is live: it was produced through the module
APIs, the four reference-rApp demo scripts (`samples/*/demo.py`) and the GUI
itself, signed in as `admin` at 1440 px wide, except the role views. The
walk-through finished with no 5xx responses from the GUI's own API.

Lifecycle (LCM) screens are in the order the lifecycle runs, so a module's
rows read as a story: the state before an action, then the state after it.

### Generic

| Screen | What it shows |
|---|---|
| [Sign in](docs/screenshots/generic/login.png) | The only page reachable signed out |
| [Failed sign-in](docs/screenshots/generic/login-failed.png) | Wrong password: a generic error that does not say which half was wrong; 5 failures lock the account for 5 minutes |
| [Account locked](docs/screenshots/generic/login-locked.png) | After 5 failed attempts the account is locked for 5 minutes; the message does not say whether the user exists |
| [Dashboard](docs/screenshots/pages/dashboard.png) | Module health for every service, open alarms by severity, SA SMOS escalations, model KPIs, rApp performance and fleet counts |
| [Lifecycle flows (list)](docs/screenshots/pages/flows.png) | The ten call-flow journeys; pick one, then the entity to follow |
| [Change password](docs/screenshots/generic/change-password-dialog.png) | Any signed-in user, from the sidebar footer |
| [Add user](docs/screenshots/generic/admin-add-user-dialog.png) | Admin: create a GUI user with a role |
| [Users and role matrix](docs/screenshots/pages/admin-users.png) | Admin: users, roles, deactivate / reset password, and the role matrix the BFF enforces |
| [Audit log](docs/screenshots/generic/admin-audit-log.png) | Admin: the append-only log of every mutating call (allowed or denied), sign-ins and user administration |

### Lifecycle flows (`/flows`)
| Flow | Result |
|---|---|
| [01 rApp onboarding → running instance](docs/screenshots/flows/f01.png) | Energy Saving package → instance → NFO deployment → bootstrapped; 6/6 |
| [02 AI/ML model: register → train → certify → deploy → infer → monitor](docs/screenshots/flows/f02.png) | 11 steps: runtime ACTIVE, an inference job, and an MLMF floor breach that triggers a retrain |
| [03 Configuration write, schema-checked, fleet-aware](docs/screenshots/flows/f03.png) | `PARTIAL_SUCCESS`: one ME applied, one never registered (`ENDPOINT_UNREACHABLE`) |
| [04 Closed-loop assurance: monitor → decide → remediate → escalate](docs/screenshots/flows/f04.png) | Threshold breach, `RECONNECT` resolved; 5/5 |
| [05 A1 EI registration → data consumption](docs/screenshots/flows/f05.png) | EI type → DME type → committed offer → active data job; 6/6 |
| [06 Package failure and the cascade-delete guard](docs/screenshots/flows/f06.png) | A package that failed validation; `CreateInstance` is refused (409) |
| [06 (guard blocking)](docs/screenshots/flows/f06-blocked.png) | A PRIMED package with active usage registrations: the guard is the failing step, so delete reads as blocked |
| [07 rApp fault and performance reporting](docs/screenshots/flows/f07.png) | Critical fault → FAULTED → recovered → RUNNING; the next action is Terminate |
| [08 RAN Analytics: producer → report → subscriber query](docs/screenshots/flows/f08.png) | Producer, SME service, push subscription, report, pull query; 5/5 |
| [09 Intent registration → fulfilment reporting → admin state](docs/screenshots/flows/f09.png) | Handler reports received, intent deactivated by its RMIO; 5/5 |
| [10 SO SMOS multi-step order: INFRA → TRAINING → DEPLOY](docs/screenshots/flows/f10.png) | Three steps dispatched to FOCOM, AI/ML Workflow and NFO, all completed |

### By module

Each table: the page's tabs first, then the lifecycle screens for that module.


#### rApps: packages and instances (Onboarding, rApp Management)

| Screen | What it shows |
|---|---|
| [Packages (tab)](docs/screenshots/pages/rapps-packages.png) | Onboarded packages with state, signature and NF descriptor; one FAILED, one PRIMED, the rest AVAILABLE |
| [Instances (tab)](docs/screenshots/pages/rapps-instances.png) | rApp instances with state and autonomy mode (SHADOW, ASSIST, AUTONOMOUS) |
| [Package: failed validation](docs/screenshots/lcm/rapps-package-failed.png) | A package that never became AVAILABLE; nothing to deploy, only Delete |
| [Onboard a package](docs/screenshots/lcm/rapps-onboard-form.png) | The CSAR location form, filled but not submitted |
| [Package: onboarded](docs/screenshots/lcm/rapps-package-onboarded.png) | A second version of a package, AVAILABLE after Onboarding validated it |
| [Package: AVAILABLE](docs/screenshots/lcm/rapps-package-drawer.png) | TOSCA / ASD descriptor, SME declarations, artifacts, usage registrations, Prime |
| [Package: PRIMED, deprime blocked](docs/screenshots/lcm/rapps-package-primed-deprime-blocked.png) | Deprime is disabled while two usage registrations are active, with the reason |
| [Deploy dialog](docs/screenshots/lcm/rapps-deploy-dialog.png) | Instance configuration and autonomy mode, fixed for the instance's lifetime |
| [Instance: RUNNING](docs/screenshots/lcm/rapps-instance-drawer.png) | Provenance, configuration, version history, performance and faults |
| [Instance: FAULTED](docs/screenshots/lcm/rapps-instance-faulted.png) | After a critical fault report: Recover and Terminate |
| [Instance: recovering](docs/screenshots/lcm/rapps-instance-recovering.png) | Recover re-enters DEPLOYING until the rApp bootstraps again |
| [Instance: RUNNING again](docs/screenshots/lcm/rapps-instance-recovered.png) | Marked bootstrapped; the fault history remains |
| [Upgrade dialog](docs/screenshots/lcm/rapps-upgrade-dialog.png) | Pick an AVAILABLE package; the upgrade runs as two rows and is resolved success / failure |
| [Upgrade: UPGRADING](docs/screenshots/lcm/rapps-upgrade-in-progress.png) | The instance waits for the replacement; resolve with Upgrade succeeded or Upgrade failed |
| [Upgrade: replacement DEPLOYING](docs/screenshots/lcm/rapps-upgrade-replacement-deploying.png) | The replacement instance on the new package, awaiting its bootstrap |
| [Upgrade: committed](docs/screenshots/lcm/rapps-upgrade-committed.png) | The replacement is RUNNING, the version history shows the UPGRADE and Roll back is offered |
| [Rollback: UPGRADING](docs/screenshots/lcm/rapps-rollback-in-progress.png) | Roll back starts a replacement on the previous package and configuration |
| [Rollback: committed](docs/screenshots/lcm/rapps-rollback-committed.png) | Version history now shows ROLLBACK, and the upgrade it undid is marked rolled back |
| [Instance: UNDEPLOYED](docs/screenshots/lcm/rapps-instance-terminated.png) | After Terminate: NFO teardown and usage stop recorded under *Last teardown* |
| [Package: PRIMED, usage released](docs/screenshots/lcm/rapps-package-primed-usage-released.png) | Both usage registrations stopped, so Deprime is enabled |
| [Package: AVAILABLE again](docs/screenshots/lcm/rapps-package-deprimed.png) | Deprime succeeded (PRIMED → DEPRIMING → AVAILABLE) |
| [Package: DEPRECATED](docs/screenshots/lcm/rapps-package-deprecated.png) | After Deprecate: no new instances; Restore and Delete remain |
| [Package: DELETING](docs/screenshots/lcm/rapps-package-delete-requested.png) | Delete requested; the cascade-delete guard found no active usage |

#### AI/ML (MLMR, AIMgF, MLLF)

| Screen | What it shows |
|---|---|
| [Models (tab)](docs/screenshots/pages/aiml-models.png) | Registered models with lifecycle state and cleared node groups |
| [Training jobs (tab)](docs/screenshots/pages/aiml-training.png) | Jobs with status, step and metrics |
| [Inference jobs (tab)](docs/screenshots/pages/aiml-inference.png) | Completed and failed inference jobs per model |
| [Coordination groups (tab)](docs/screenshots/pages/aiml-groups.png) | A group of two or more models retrained together |
| [Performance monitoring, MLMF (tab)](docs/screenshots/pages/aiml-mlmf.png) | Subscriptions with a KPI floor |
| [Feature groups (tab)](docs/screenshots/pages/aiml-features.png) | Registered datalake feature groups |
| [Register model](docs/screenshots/lcm/aiml-register-model-dialog.png) | Filled but not submitted |
| [Model: TRAINING](docs/screenshots/lcm/aiml-model-training.png) | Pipeline stepper, training job in progress |
| [Model: TRAINED, approval gate](docs/screenshots/lcm/aiml-model-trained-approval-gate.png) | After the training job finishes; an operator must approve before validation |
| [Model: retrain requested](docs/screenshots/lcm/aiml-model-retrain-requested.png) | Retry training after a failed run starts a new training job |
| [Model: VALIDATING](docs/screenshots/lcm/aiml-model-validating.png) | After the training approval and Request validation |
| [Model: VALIDATED, approval gate](docs/screenshots/lcm/aiml-model-validated-approval-gate.png) | A second operator gate before emulation |
| [Model: EMULATED](docs/screenshots/lcm/aiml-model-emulated-submit.png) | Submit for approval opens the governance steps |
| [Model: PENDING_APPROVAL](docs/screenshots/lcm/aiml-model-pending-approval.png) | Approve or Reject |
| [Model: CERTIFIED](docs/screenshots/lcm/aiml-model-certified.png) | Promote, Retrain or Deprecate |
| [Model: PROMOTED](docs/screenshots/lcm/aiml-model-promoted-deploy-node-groups.png) | The Deploy to node groups field, filled |
| [Deploy targets cleared](docs/screenshots/lcm/aiml-model-node-groups-cleared.png) | `clearedNodeGroups` stamped by MLLF |
| [Runtime: DEPLOYED](docs/screenshots/lcm/aiml-model-runtime-deployed.png) | AIMgF and NFO deployed the runtime; Activate or Terminate |
| [Runtime: ACTIVE](docs/screenshots/lcm/aiml-model-runtime-active-inference-requested.png) | An inference job RUNNING, with Completed / Failed to resolve it |
| [Model: PROMOTED, runtime ACTIVE](docs/screenshots/lcm/aiml-model-promoted-runtime-active.png) | Cleared node groups, artifact, runtime actions, training and inference jobs |
| [Write back model metrics](docs/screenshots/lcm/aiml-training-metrics-dialog.png) | What the trainer reports for a job; replaces the stored metrics |
| [Inference jobs](docs/screenshots/lcm/aiml-inference-job-running.png) | COMPLETED and FAILED outcomes across models |
| [New coordination group](docs/screenshots/lcm/aiml-coordination-group-form.png) | Two members picked, use cases entered |
| [MLMF: subscription and reports](docs/screenshots/lcm/aiml-mlmf-subscription-and-reports.png) | Subscribe form, subscriptions, and reports with a floor breach |
| [New feature group](docs/screenshots/lcm/aiml-feature-group-form.png) | Name, features and datalake settings; the token field is masked |

#### Alarms (RAN NF OAM O1 FM, FOCOM)

| Screen | What it shows |
|---|---|
| [RAN NF alarms (tab)](docs/screenshots/pages/alarms-ran.png) | Severity counters, alarm list, injection tool and FM subscriptions |
| [O-Cloud alarms (tab)](docs/screenshots/pages/alarms-ocloud.png) | Infrastructure alarms from FOCOM |
| [Filter: critical](docs/screenshots/lcm/alarms-filter-critical.png) | Clicking a severity counter filters the list |
| [Alarm: raised](docs/screenshots/lcm/alarm-raised-unacknowledged.png) | 3GPP TS 28.532 / 28.111 fault fields, UNACKNOWLEDGED |
| [Alarm: acknowledged](docs/screenshots/lcm/alarm-acknowledged.png) | Ack recorded against the GUI user |
| [Alarm: cleared](docs/screenshots/lcm/alarm-cleared.png) | Clear recorded with who and when; Unack remains |
| [List with cleared alarms](docs/screenshots/lcm/alarms-with-cleared.png) | *show cleared* includes the cleared alarm |

#### KPIs & Assurance (rApp Management, MLMF, MDAF, SA SMOS, FOCOM)

| Screen | What it shows |
|---|---|
| [rApp performance (tab)](docs/screenshots/pages/kpis-rapp.png) | Sparklines per reported metric |
| [PM subscriptions (tab)](docs/screenshots/pages/kpis-pm.png) | Counter subscriptions per managed element |
| [Model KPIs, MLMF (tab)](docs/screenshots/pages/kpis-mlmf.png) | Model KPI reports and floor breaches |
| [RAN Analytics (tab)](docs/screenshots/pages/kpis-analytics.png) | Reports, producers and subscriptions |
| [Assurance, SA SMOS (tab)](docs/screenshots/pages/kpis-assurance.png) | Monitors and remedial actions with outcomes |
| [O-Cloud performance (tab)](docs/screenshots/pages/kpis-ocloud.png) | FOCOM metrics (empty: this build has no ingest route) |
| [New PM subscription](docs/screenshots/lcm/kpis-pm-subscription-form.png) | Managed element, counter, delivery and granularity |
| [Analytics report](docs/screenshots/lcm/kpis-analytics-report-dialog.png) | One report's output |
| [Monitor: threshold breach](docs/screenshots/lcm/kpis-monitor-evaluate-breach.png) | Evaluate with a metric below its floor |
| [Remedial action: RESOLVED](docs/screenshots/lcm/kpis-remedial-action-resolved.png) | `RECONNECT` executed against the order's NF deployment |
| [Escalated to operator](docs/screenshots/lcm/kpis-escalated-to-operator.png) | Escalate with a reason; outcome ESCALATED |
| [Group-scoped remediation](docs/screenshots/lcm/kpis-group-retrain-remediation.png) | Any action on a model-group monitor retrains the group |
| [Register assurance monitor](docs/screenshots/lcm/kpis-register-monitor-form.png) | Scope picked from the SO SMOS orders, metric floors entered |
| [Monitor registered](docs/screenshots/lcm/kpis-monitor-registered.png) | The new monitor listed with no actions yet |

#### Policy & Intents (A1 Related, Intent Service)

| Screen | What it shows |
|---|---|
| [A1 policies (tab)](docs/screenshots/pages/policy-a1.png) | Policies with enforcement status (ENFORCED, REJECTED, SUSPENDED) |
| [Policy status subscriptions (tab)](docs/screenshots/pages/policy-status-subs.png) | Subscriptions notified on enforcement-status changes |
| [A1 services (tab)](docs/screenshots/pages/policy-services.png) | A1-P service registry with keep-alive supervision |
| [Intents (tab)](docs/screenshots/pages/policy-intents.png) | TS 28.312 intents with admin state |
| [Intent handlers, RMIH (tab)](docs/screenshots/pages/policy-handlers.png) | Framework-internal handlers and their capabilities |
| [Autonomy dispatches (tab)](docs/screenshots/pages/policy-autonomy.png) | DISPATCHED, SHADOWED and AWAITING_SCOPE dispatches by autonomy mode |
| [Create A1 policy](docs/screenshots/lcm/policy-a1-create-form.png) | Type, Near-RT RIC and policy object |
| [Policy: ENFORCED](docs/screenshots/lcm/policy-a1-created-enforced.png) | The new policy listed after the Near-RT RIC accepted it |
| [Policy detail](docs/screenshots/lcm/policy-a1-drawer.png) | Policy object and enforcement status |
| [Service keep-alive](docs/screenshots/lcm/policy-service-keepalive.png) | A supervised service after Keep alive, with its countdown |
| [Service unregistered](docs/screenshots/lcm/policy-service-unregistered.png) | Unregister removes the service and deletes its policies |
| [Create intent](docs/screenshots/lcm/policy-intent-create-form.png) | Handler, expectation object type, targets, priority and purpose |
| [Intent: ACTIVATED](docs/screenshots/lcm/policy-intent-drawer.png) | Expectations and the handler's reports |
| [Intent: DEACTIVATED](docs/screenshots/lcm/policy-intent-deactivated.png) | Admin state changed by the intent's own RMIO (`smo-gui`) |
| [Publish an intent report](docs/screenshots/lcm/policy-intent-report-form.png) | The admin tool for acting as the handling RMIH |
| [Intent report published](docs/screenshots/lcm/policy-intent-report-published.png) | The new fulfilment report appears first in the intent's reports |
| [Autonomy: AWAITING_SCOPE](docs/screenshots/lcm/policy-autonomy-awaiting-scope.png) | An ASSIST instance waits for an operator to scope it, or reject it |
| [Autonomy: resolved](docs/screenshots/lcm/policy-autonomy-resolved.png) | Scope supplied; an intent was created and the dispatch is DISPATCHED |

#### Energy Saving, Mobility, Coverage, Traffic Steering (reference rApps)

| Screen | What it shows |
|---|---|
| [Energy Saving](docs/screenshots/pages/energy-saving.png) | Per-cell PRB trend, prediction, safety guards, decision and verification |
| [Mobility](docs/screenshots/pages/mobility.png) | Neighbour relations with handover KPIs and CIO decisions |
| [Coverage](docs/screenshots/pages/coverage.png) | Per-cell tilt and power with excess shares, and the latest joint plan |
| [Traffic Steering](docs/screenshots/pages/traffic-steering.png) | Per-cell congestion, forecast, steering in force and safety |
| [Energy Saving: cell detail](docs/screenshots/lcm/energy-saving-drilldown-drawer.png) | The decision chain for one cell |
| [Energy Saving: after Evaluate now](docs/screenshots/lcm/energy-saving-after-evaluate.png) | One closed-loop pass completed |
| [Mobility: relation detail](docs/screenshots/lcm/mobility-drilldown-drawer.png) | The decision chain for one neighbour relation |
| [Mobility: after Evaluate now](docs/screenshots/lcm/mobility-after-evaluate.png) | One closed-loop pass completed |
| [Coverage: cell detail](docs/screenshots/lcm/coverage-drilldown-drawer.png) | The decision chain for one cell |
| [Coverage: after Evaluate now](docs/screenshots/lcm/coverage-after-evaluate.png) | One closed-loop pass completed |
| [Traffic Steering: cell detail](docs/screenshots/lcm/traffic-steering-drilldown-drawer.png) | The decision chain for one cell |
| [Traffic Steering: after Evaluate now](docs/screenshots/lcm/traffic-steering-after-evaluate.png) | One closed-loop pass completed |

#### Infrastructure (NFO, FOCOM, RAN NF OAM, SO SMOS)

| Screen | What it shows |
|---|---|
| [NF deployments (tab)](docs/screenshots/pages/infra-nfo.png) | Deployments with state, heal / scale / terminate, and descriptors |
| [O-Cloud inventory (tab)](docs/screenshots/pages/infra-ocloud.png) | Pool resources, deployment managers, inventory subscriptions, resource types |
| [Topology (tab)](docs/screenshots/pages/infra-topology.png) | TEIV entities and relationships exported from FOCOM |
| [O1 endpoints & jobs (tab)](docs/screenshots/pages/infra-o1.png) | Managed elements, CM write jobs and software management jobs |
| [Service orders (tab)](docs/screenshots/pages/infra-orders.png) | Orders: completed, and a failed one whose pending step was cancelled |
| [NF deployment: RUNNING](docs/screenshots/lcm/nfo-deployment-running.png) | LCM operations and linked O-Cloud resources |
| [NF deployment: heal and scale](docs/screenshots/lcm/nfo-heal-and-scale-operations.png) | HEAL and SCALE operations recorded |
| [NF deployment: ABNORMAL](docs/screenshots/lcm/nfo-runtime-failure-abnormal.png) | A deployment-manager `RUNTIME_FAILURE` with its reason |
| [NF deployment: healed](docs/screenshots/lcm/nfo-healed-running.png) | Heal returns it to RUNNING |
| [O-Cloud resource provisioned](docs/screenshots/lcm/ocloud-resource-provisioned.png) | A new resource in the pool, with Deprovision |
| [Inventory-change subscription](docs/screenshots/lcm/ocloud-inventory-subscription-form.png) | Callback and resource type filter, filled |
| [Subscription created](docs/screenshots/lcm/ocloud-inventory-subscription-created.png) | Notified on provision / deprovision of matching resources |
| [Register O1 endpoint](docs/screenshots/lcm/o1-register-endpoint-dialog.png) | Managed element, adaptor URI, protocols, vendor |
| [O1 health discovery](docs/screenshots/lcm/o1-health-discovery.png) | Run health discovery re-ages every endpoint's health |
| [CM write to a RESTCONF ME](docs/screenshots/lcm/o1-cm-write-restconf-applied.png) | A RESTCONF managed element accepts the write; one sub-change APPLIED |
| [New CM write](docs/screenshots/lcm/o1-cm-write-dialog.png) | Several managed elements, scope, operation, schema-checked changes |
| [CM write submitted](docs/screenshots/lcm/o1-cm-write-submitted.png) | The new job in the list |
| [CM job: PARTIAL_SUCCESS](docs/screenshots/lcm/o1-cm-job-partial-success.png) | Per-managed-element sub-changes: APPLIED and REJECTED |
| [Software management job](docs/screenshots/lcm/o1-software-job-advanced.png) | A job advanced to its next phase |
| [Compose service order](docs/screenshots/lcm/orders-compose.png) | Step templates added to the order |
| [Service order: executed](docs/screenshots/lcm/order-drawer-steps.png) | Per-step results |

#### Data & Exposure (DME, A1 EI, SME)

| Screen | What it shows |
|---|---|
| [DME: types, jobs, offers (tab)](docs/screenshots/pages/data-dme.png) | Producers, data types, data jobs, offers and type subscriptions |
| [A1 EI types (tab)](docs/screenshots/pages/data-a1-ei.png) | Enrichment-information types wrapping DME types |
| [SME: services & invokers (tab)](docs/screenshots/pages/data-sme.png) | Providers, published APIs, invokers, discovery and CAPIF event subscriptions (one unscoped, one limited by `apiIds`) |
| [DME offer: notify data ready](docs/screenshots/lcm/dme-offer-notify-data-ready.png) | The producer's availability signal for a committed offer |
| [Register a producer data type](docs/screenshots/lcm/dme-register-type-form.png) | The admin tool for acting as a producer |
| [Create a data job](docs/screenshots/lcm/dme-data-job-form.png) | Type, mode, delivery (limited to what an offer committed) and consumer |
| [Data job](docs/screenshots/lcm/dme-data-job-dialog.png) | The created job's record |
| [Register EI type](docs/screenshots/lcm/a1-ei-register-form.png) | Filled but not submitted |
| [EI type registered](docs/screenshots/lcm/a1-ei-registered.png) | The new type listed with its DME type |
| [SME invoker secret](docs/screenshots/lcm/sme-invoker-secret-dialog.png) | Shown once and never again; SME keeps only a hash |
| [Trusted-invoker security context](docs/screenshots/lcm/sme-trusted-invoker-dialog.png) | AEF, API and preferred security method |
| [CAPIF event subscription form](docs/screenshots/lcm/sme-event-subscription-form.png) | Subscriber, events and callback |
| [CAPIF event subscription created](docs/screenshots/lcm/sme-event-subscription-created.png) | Listed under its subscriber |

### Role views

Each screen is the same live state seen through a lower role. Actions the
role can't perform aren't rendered, and the BFF refuses them anyway.

| Role | Screens |
|---|---|
| Viewer | [dashboard](docs/screenshots/roles/viewer-dashboard.png) · [alarms](docs/screenshots/roles/viewer-alarm-list.png) · [flow 06](docs/screenshots/roles/viewer-flows-06.png) · [rApp packages](docs/screenshots/roles/viewer-rapp-packages.png) · [SME](docs/screenshots/roles/viewer-sme.png) |
| Operator | [dashboard](docs/screenshots/roles/operator-dashboard.png) · [rApp instances](docs/screenshots/roles/operator-rapp-instances.png) · [DME](docs/screenshots/roles/operator-dme.png) · [A1 policies](docs/screenshots/roles/operator-a1-policies.png) · [infrastructure](docs/screenshots/roles/operator-infrastructure.png) |
