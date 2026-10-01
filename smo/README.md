# AI-RAN SMO — Reference Implementation

This directory holds a reference implementation of an O-RAN Service
Management and Orchestration (SMO) platform. It is built from the low-level
designs of the seventeen SMO modules that accompany *SMO Design Document v1.3*,
and checked against the 3GPP and O-RAN specifications in
[`../specs/`](../specs/README.md) and the O-RAN-SC reference repositories.
Each module is a Python 3.11 / FastAPI / SQLAlchemy service with its own
state machines and unit tests, and all modules share one Postgres schema.
They talk to each other only through the R1 Termination gateway. On top of
the platform sit an operator GUI with a backend-for-frontend, an AI Runtime
SDK, and four reference rApps (Energy Saving, Mobility Optimization,
Coverage Optimization, Traffic Steering). Each rApp runs a closed loop from
O1 PM data through the governed TS 28.105 model lifecycle to verified O1 CM
writes.

## Stack and modules

Every service in `docker-compose.yml` listens on container port `8000`
(nginx in `gui` listens on `8080`; Postgres on `5432`). Only three host ports are published:
R1 Termination `:8080`, the GUI `:3000`, and Postgres `:5432`. Every other
service is reachable only on the compose network, by hostname.

| Module | Directory | Role | Port / R1 route |
|---|---|---|---|
| R1 Termination | `r1-termination/` | rApp-facing gateway: `/bootstrap`, bearer-token check against SME, prefix-routed proxy to every module | host `8080` → `8000`; routes below |
| SME | `sme/` | CAPIF core: provider/invoker onboarding, OAuth2 tokens, service publish/discover, event subscriptions, trusted invokers | `/sme` |
| DME | `dme/` | Data management and exposure: producers, types, data jobs, offers, type subscriptions | `/dme`, `/dme-push`, `/dme-pull` |
| Onboarding | `onboarding/` | CSAR package validation, `ApplicationPackage` FSM, priming, usage registrations | `/onboarding` |
| rApp Management | `rapp-mgmt/` | `RAppInstance` FSM, deploy/bootstrap/upgrade/terminate, perf/fault reports | `/rapp-mgmt` |
| RAN NF OAM | `ran-nf-oam/` | O1: adaptor endpoints, NETCONF CM writes, alarms, PM subscriptions, software management, vendor capability registry | `/ran-nf-oam` |
| A1 Related | `a1-related/` | A1 policy mapping store, service supervision, EI types; southbound to the mock Near-RT RIC | `/a1-related` |
| NFO | `nfo/` | NF descriptors and deployments (`NFDeployment` FSM, heal/scale/terminate) | `/nfo` |
| FOCOM | `focom/` | O-Cloud inventory, provisioning, inventory subscriptions, FCAPS, TEIV topology export | `/focom` |
| AIMgF | `aimgf/` | AI/ML lifecycle orchestration: model and runtime lifecycle FSMs, training/validation/emulation/inference jobs, feature groups, MLMF | `/aimgf` |
| MLMR | `mlmr/` | Model repository: model identity, metadata, artifacts, coordination groups | `/mlmr` |
| MLLF | `mllf/` | Model loading and deployment targeting | `/mllf` |
| RAN Analytics | `ran-analytics/` | Analytics producer registration (an MDAF consumer) | `/ran-analytics` |
| MDAF | `mdaf/` | TS 28.104 analytics: reports, subscriptions, MDA requests | `/mdaf` |
| Intent Service | `intent-service/` | TS 28.312 intents, intent handlers (RMIH), reports, autonomy dispatches | `/intent-service` |
| SO SMOS | `so-smos/` | Multi-step service orders over a dispatch table, fail-fast | `/so-smos` |
| SA SMOS | `sa-smos/` | Assurance monitors, remedial actions, O1-CM intent handler | `/sa-smos` |
| Mock Near-RT RIC | `mock-near-rt-ric/` | A1-P test double, reachable only from `a1-related` on the internal `a1_mock_net` network | none |
| Mock O1 Adaptor | `mock-o1-adaptor/` | NETCONF-shaped O1 test double that answers RAN NF OAM's NETCONF RPCs | none (`mock-o1-adaptor:8000`) |
| Energy Saving rApp | `samples/energy-saving-rapp/` | Wave 10.1 reference rApp (cell sleep/wake) | `/energy-saving-rapp` |
| Mobility Optimization rApp | `samples/mobility-optimization-rapp/` | Wave 10.2 reference rApp (per-relation CIO) | `/mobility-optimization-rapp` |
| Coverage Optimization rApp | `samples/coverage-optimization-rapp/` | Wave 10.3 reference rApp (joint tilt/power) | `/coverage-optimization-rapp` |
| Traffic Steering rApp | `samples/traffic-steering-rapp/` | Wave 10.4 reference rApp (idle priority + connected CIO) | `/traffic-steering-rapp` |
| GUI BFF | `gui-bff/` | GUI users, roles, sessions, audit log; forwards allowed calls to R1 | none (reached via `gui` at `/api`) |
| GUI | `gui/` | React operator console behind nginx | host `3000` → `8080` |
| Postgres | — | `postgres:16-alpine`, seeded from `migrations/001_init.sql` | host `5432` |

R1 Termination's routing table is `ROUTES` in `r1-termination/app/main.py`.
It strips the prefix before forwarding, so `/sme/oauth2/token` reaches SME's
`/oauth2/token`. Cross-module calls go through `smo_shared.r1_client.R1Client`,
which obtains its own SME token the same way an rApp does.

## Quickstart

```bash
cd smo
export GUI_ADMIN_PASSWORD='choose-one'   # optional; otherwise one is generated
docker compose up -d --build
docker compose ps
```

- R1 Termination: `curl -s http://localhost:8080/bootstrap`
- Operator GUI: <http://localhost:3000>, user `admin`. If `GUI_ADMIN_PASSWORD`
  was unset, read the generated password with
  `docker compose exec gui-bff cat /data/initial-admin-password`.
  See [`gui/README.md`](gui/README.md) for the other GUI variables and roles.
- A guided walk-through of every module, with copy-pasteable commands:
  [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md).

Run the tests:

```bash
cd smo
pip install -e shared
(cd onboarding && PYTHONPATH=.:../shared python -m pytest tests/ -q)   # one module's unit suite
PYTHONPATH=shared python -m pytest tests_integration/ -q              # cross-service integration suite
docker compose config --quiet                                         # compose file is valid
```

The full verification battery is in [`CLAUDE.md`](CLAUDE.md): every module's
unit suite, the four sample rApps, `gui-bff`, the integration suite (which
also checks `docs/openapi/*.json` against each live schema), the
migration-vs-models check against a real Postgres, and the GUI typecheck,
tests, build and call-flow diagram validation. CI
(`../.github/workflows/smo-tests.yml`) runs the same checks on every change
under `smo/`. CI validates `docker-compose.yml` with `docker compose config`
but does not start the stack.

After editing a sample rApp, rebuild its package with
`python3 samples/build_csar.py <name>`. The integration suite fails if a
committed `.csar` no longer matches its sources. After changing a route's
request or response shape, regenerate the specs with
`PYTHONPATH=shared python scripts/generate_openapi_specs.py`.

## Repository layout

```
smo/
  README.md                 this file
  CLAUDE.md                 working practice and the full verification battery
  DEMO_RUNBOOK.md           live walk-through: hello-world lifecycle (§0–§23), reference rApps (§24–§27)
  OPEN_ITEMS.md             open items only
  HISTORY.md                closed history, spec audit, wave exit reviews
  docker-compose.yml        deployment topology, incl. the isolated a1_mock_net network
  Dockerfile                one image, parameterised by the MODULE build arg
  migrations/001_init.sql   the consolidated Postgres schema
  shared/smo_shared/        DB session, FSM base, RFC 7807 errors, R1Client, webhook helpers,
                            correlation ids, SQLite test engine (testing.py), time helpers
  <module>/app/             one directory per SMO module (models.py, statemachine.py, main.py)
  <module>/tests/           that module's unit tests (standalone, SQLite)
  mock-near-rt-ric/         A1-P test double (A1 Related's southbound)
  mock-o1-adaptor/          NETCONF-shaped O1 test double (RAN NF OAM's southbound)
  sdk/smo_sdk/              AI Runtime SDK: data, analytics, models, lifecycle, intent, platform clients
  gui/                      React + TypeScript operator console (nginx)
  gui-bff/                  GUI backend-for-frontend: auth, RBAC (app/rbac.py), audit, R1 proxy
  samples/
    build_csar.py           builds samples/<name>.csar from samples/<name>/
    hello-world-rapp/       minimal sample package used by DEMO_RUNBOOK.md §0–§23
    energy-saving-rapp/     Wave 10.1 rApp: service, model, decision engine, demo.py (§24)
    mobility-optimization-rapp/  Wave 10.2 rApp: service, model, MRO engine, demo.py (§25)
    coverage-optimization-rapp/  Wave 10.3 rApp: service, model, joint optimiser, demo.py (§26)
    traffic-steering-rapp/  Wave 10.4 rApp: service, model, pairwise planner, demo.py (§27)
    *.csar                  committed packages built from the directories above
  tests_integration/        in-process service mesh (loader.py, mesh.py) and cross-service tests,
                            incl. test_demo_runbook.py, which replays DEMO_RUNBOOK.md
  scripts/                  OpenAPI generation, migration-vs-models check, CM schema ingest,
                            TS 28.312 family generation
  docs/
    ARCHITECTURE.md         platform baseline, service ownership, O1 vendor onboarding
    ROADMAP.md              waves, compliance matrices, runtime realisation
    call-flows/             27 Mermaid sequence diagrams of end-to-end journeys
    openapi/                committed OpenAPI spec per service, checked against the live schema
```

## Status

| Area | Status | Detail |
|---|---|---|
| Phase 1 platform (17 modules, R1 gateway, schema, mocks, GUI) | Done | [`HISTORY.md`](HISTORY.md) |
| O-RAN-SC gap closures (route-by-route audit against the reference repos) | Done | [`HISTORY.md`](HISTORY.md) |
| Formal-spec audit (3GPP / O-RAN specs in `../specs/`) | Done where a spec file exists | [`HISTORY.md`](HISTORY.md) |
| AI/ML pipeline review (§6 backlog, items 6.1–6.7) | Done | [`HISTORY.md`](HISTORY.md) |
| Waves 0–3: AI Platform service decomposition, R1 contracts | Done | [`HISTORY.md`](HISTORY.md), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Waves 4–10.4: TS 28.105/28.104/28.312 compliance, runtime, autonomy, multi-vendor O1, four reference rApps | Done | [`docs/ROADMAP.md`](docs/ROADMAP.md), [`HISTORY.md`](HISTORY.md) |
| Open items and deliberate scope cuts | Open | [`OPEN_ITEMS.md`](OPEN_ITEMS.md) |

## Documentation map

| Document | Contents |
|---|---|
| [`README.md`](README.md) | What this is, modules, quickstart, layout (this file) |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Platform baseline, service ownership, O1 vendor onboarding |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Waves, TS 28.105/28.104/28.312 compliance matrices, runtime realisation |
| [`OPEN_ITEMS.md`](OPEN_ITEMS.md) | Items still open, and deliberate scope cuts |
| [`HISTORY.md`](HISTORY.md) | Condensed closed history, spec audit, wave exit reviews |
| [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md) | Command-by-command live demo against `docker compose up` |
| [`CLAUDE.md`](CLAUDE.md) | Working practice, conventions, full test battery |
| [`docs/call-flows/`](docs/call-flows/) | One Mermaid sequence diagram per end-to-end journey (01–27) |
| [`docs/openapi/`](docs/openapi/) | Generated OpenAPI spec per service |
| [`gui/README.md`](gui/README.md) | Operator GUI: running it, roles, security, pages, screenshots |
