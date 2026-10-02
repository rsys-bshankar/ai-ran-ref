# AI-RAN SMO — Reference Implementation

This directory holds a reference implementation of an O-RAN Service
Management and Orchestration (SMO) platform. It is built from the design of
seventeen SMO modules, each documented in its own `README.md`, and checked
against the 3GPP and O-RAN specifications in
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

Each module has its own `README.md` with its high-level design, low-level
design and unit tests; the Module column links to it.

**Standards basis** says where a module's design comes from: **O-RAN** (an
O-RAN WG interface or O-RAN-SC component), **3GPP** (a 3GPP TS / TR), both, or
**Internal** (this build's own logic, with no standard behind it). A module
that realises a standard and adds its own behaviour on top says so.

### Platform modules

| Module | Standards basis | Directory | Role | Port / R1 route |
|---|---|---|---|---|
| [R1 Termination](r1-termination/README.md) | O-RAN (R1 gateway) + Internal (prefix routing) | `r1-termination/` | rApp-facing gateway: `/bootstrap`, bearer-token check against SME, prefix-routed proxy to every module | host `8080` → `8000`; routes below |
| [SME](sme/README.md) | O-RAN (R1 SME) + 3GPP (CAPIF, TS 23.222 / 29.222) | `sme/` | CAPIF core: provider/invoker onboarding, OAuth2 tokens, service publish/discover, event subscriptions, trusted invokers | `/sme` |
| [DME](dme/README.md) | O-RAN (R1 DME, ICS-derived) + Internal (O1 action mediation) | `dme/` | Data management and exposure: producers, types, data jobs, offers, type subscriptions | `/dme`, `/dme-push`, `/dme-pull` |
| [Onboarding](onboarding/README.md) | O-RAN (rApp package, ASD / TOSCA CSAR) + Internal (`manifest.yaml`, `capabilities.yaml`) | `onboarding/` | CSAR package validation, `ApplicationPackage` FSM, priming, usage registrations | `/onboarding` |
| [rApp Management](rapp-mgmt/README.md) | O-RAN (rApp Manager) + Internal (autonomy mode, region scope) | `rapp-mgmt/` | `RAppInstance` FSM, deploy/bootstrap/upgrade/terminate, perf/fault reports | `/rapp-mgmt` |
| [RAN NF OAM](ran-nf-oam/README.md) | O-RAN (O1) + 3GPP (MnS: TS 28.532 / 28.541 / 28.111, TS 28.319 MSAC) + Internal (vendor capability registry) | `ran-nf-oam/` | O1: adaptor endpoints, NETCONF and RESTCONF CM writes with MSAC access control, alarms, PM subscriptions and PM files, software management, vendor capability registry | `/ran-nf-oam` |
| [A1 Related](a1-related/README.md) | O-RAN (A1-P, A1-EI) | `a1-related/` | A1 policy mapping store, service supervision, EI types; southbound to the mock Near-RT RIC | `/a1-related` |
| [NFO](nfo/README.md) | O-RAN (O2-DMS-style deployment) + Internal (descriptor model) | `nfo/` | NF descriptors and deployments (`NFDeployment` FSM, heal/scale/terminate) | `/nfo` |
| [FOCOM](focom/README.md) | O-RAN (O2-IMS) | `focom/` | O-Cloud inventory, provisioning, inventory subscriptions, FCAPS, TEIV topology export | `/focom` |
| [AIMgF](aimgf/README.md) | 3GPP (TS 28.105) + Internal (lifecycle orchestration) | `aimgf/` | AI/ML lifecycle orchestration: model and runtime lifecycle FSMs, training/validation/emulation/inference jobs, feature groups, MLMF | `/aimgf` |
| [MLMR](mlmr/README.md) | 3GPP (TS 28.105, TS 29.482) | `mlmr/` | Model repository: model identity, metadata, artifacts, coordination groups | `/mlmr` |
| [MLLF](mllf/README.md) | Internal (informed by TS 28.105) | `mllf/` | Model loading and deployment targeting | `/mllf` |
| [RAN Analytics](ran-analytics/README.md) | Custom (no 3GPP IOC; O-RAN SMO-ARCH §4.1 NOTE 1 leaves a RAN Analytics SMOS out of scope) | `ran-analytics/` | Producer registry only: which analytics a producer rApp makes, from which DME types; not a TS 28.104 function and not an MDAF consumer in code | `/ran-analytics` |
| [MDAF](mdaf/README.md) | 3GPP (TS 28.104) | `mdaf/` | The TS 28.104 MDA function: stores and delivers analytics reports, subscriptions, MDA requests | `/mdaf` |
| [Intent Service](intent-service/README.md) | 3GPP (TS 28.312) | `intent-service/` | TS 28.312 intents, intent handlers (RMIH), reports, autonomy dispatches | `/intent-service` |
| [SO SMOS](so-smos/README.md) | O-RAN SMOS (WG1 SMO-ARCH §4.2.7); SMOS interfaces are unspecified, so the design is internal; does not register as an RMIH | `so-smos/` | Multi-step service orders over a dispatch table, fail-fast | `/so-smos` |
| [SA SMOS](sa-smos/README.md) | O-RAN SMOS (WG1 SMO-ARCH §4.2.8); SMOS interfaces are unspecified, so the design is internal; its O1-CM handler acts as a 3GPP TS 28.312 RMIH | `sa-smos/` | Assurance monitors, remedial actions, O1-CM intent handler | `/sa-smos` |
| Postgres | n/a (infrastructure) | — | `postgres:18-alpine`, schema by the `migrate` one-shot service (Alembic, `migrations/`) | host `5432` |

### Test doubles, SDK, GUI

| Module | Standards basis | Directory | Role | Port / R1 route |
|---|---|---|---|---|
| [Mock Near-RT RIC](mock-near-rt-ric/README.md) | Test double of an O-RAN A1-P endpoint | `mock-near-rt-ric/` | A1-P test double, reachable only from `a1-related` on the internal `a1_mock_net` network | none |
| [Mock O1 Adaptor](mock-o1-adaptor/README.md) | Test double of an O1 adaptor (NETCONF and RESTCONF) | `mock-o1-adaptor/` | O1 test double that answers RAN NF OAM's NETCONF RPCs (`/edit-config`) and RESTCONF requests (`/restconf`) | none (`mock-o1-adaptor:8000`) |
| [AI Runtime SDK](sdk/README.md) | Internal (thin client over R1) | `sdk/smo_sdk/` | Python clients for the six rApp-facing namespaces: data, analytics, models, lifecycle, intent, platform | library |
| [Shared library](shared/README.md) | Internal (implements the RFC 7807 / RFC 7662 conventions) | `shared/smo_shared/` | DB session, FSM base, errors, pagination, correlation ids, webhook helper, `R1Client` | library |
| [GUI BFF](gui-bff/README.md) | Internal | `gui-bff/` | GUI users, roles, sessions, audit log; forwards allowed calls to R1 | none (reached via `gui` at `/api`) |
| [GUI](gui/README.md) | Internal | `gui/` | React operator console behind nginx | host `3000` → `8080` |

### Reference rApps

rApps are not platform modules: they consume the platform through R1 and the AI
Runtime SDK, and each is packaged as a CSAR. How the packages are structured and
what each parameter means is in [`docs/RAPP_PACKAGING.md`](docs/RAPP_PACKAGING.md).

| rApp | Directory | Use case | R1 route | Call flow |
|---|---|---|---|---|
| [Hello World](samples/hello-world-rapp/README.md) | `samples/hello-world-rapp/` | Minimal package for the lifecycle demo (no AI) | none (not a service in compose) | [01](docs/call-flows/01-rapp-onboarding-to-deployment.md) |
| [Energy Saving](samples/energy-saving-rapp/README.md) | `samples/energy-saving-rapp/` | Cell sleep/wake | `/energy-saving-rapp` | [22](docs/call-flows/22-energy-saving-closed-loop.md) |
| [Mobility Optimization](samples/mobility-optimization-rapp/README.md) | `samples/mobility-optimization-rapp/` | Per-relation CIO | `/mobility-optimization-rapp` | [23](docs/call-flows/23-mobility-optimization-closed-loop.md) |
| [Coverage Optimization](samples/coverage-optimization-rapp/README.md) | `samples/coverage-optimization-rapp/` | Joint tilt / power | `/coverage-optimization-rapp` | [24](docs/call-flows/24-coverage-optimization-closed-loop.md) |
| [Traffic Steering](samples/traffic-steering-rapp/README.md) | `samples/traffic-steering-rapp/` | Idle priority + connected CIO | `/traffic-steering-rapp` | [25](docs/call-flows/25-traffic-steering-closed-loop.md) |

R1 Termination's routing table is `ROUTES` in `r1-termination/app/main.py`.
It strips the prefix before forwarding, so `/sme/capif-events/...` reaches SME's
`/capif-events/...`. Tokens are obtained from SME's own address, which
`/bootstrap` returns (`tokenEndPoint`); the gateway itself answers 401 to an
unauthenticated `/sme/oauth2/token`. Cross-module calls go through `smo_shared.r1_client.R1Client`,
which obtains its own SME token the same way an rApp does.

## Quickstart

```bash
cd smo
scripts/init_secrets.sh                  # once: writes the database password to secrets/db_password (no default)
export GUI_ADMIN_PASSWORD='choose-one'   # optional; otherwise one is generated
docker compose up -d --build
docker compose ps
```

The schema is created and upgraded by the `migrate` one-shot service (Alembic, `docs/adr/0001-schema-migrations.md`), which every service waits for: an empty volume gets the baseline (`migrations/001_init.sql`) and every revision after it, a volume made by an earlier stack that created the schema from the file is stamped and upgraded, an up-to-date one is left alone. After pulling a change, `docker compose up -d --build` migrates in place; `docker compose logs migrate` shows what it did. `docker compose down -v` still discards the demo data. A volume made when the database password was still `smo` (or any other earlier password) keeps it, and the new random one will not match: recreate the volume as well. The same goes for the `gui_bff_data` and `smo_packages` volumes of a stack started before the services ran as a non-root user: they are root-owned, so recreate them too.

- R1 Termination: `curl -s http://localhost:8080/bootstrap`
- Operator GUI: <http://localhost:3000>, user `admin`. If `GUI_ADMIN_PASSWORD`
  was unset, read the generated password with
  `docker compose exec gui-bff cat /data/initial-admin-password`.
  See [`gui/README.md`](gui/README.md) for the other GUI variables and roles.
- A guided walk-through of every module, with copy-pasteable commands:
  [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md).

TLS: `scripts/make_dev_certs.sh` makes a development CA and a server certificate (`smo/certs/`, git-ignored), and
`docker compose --profile tls up -d` adds an nginx edge (`edge/nginx.conf`) that terminates HTTPS in front of the GUI
(`https://localhost:3443`) and R1 Termination (`https://localhost:8443`), with HSTS. Trust `certs/ca.crt` in the browser, or
`curl --cacert certs/ca.crt https://localhost:8443/bootstrap`. The plain ports 3000 and 8080 stay open: remove their `ports:` in a
deployment that should be reachable over TLS only. The services behind the edge still speak HTTP on the compose network, and
`/bootstrap` still advertises their `http://` addresses (`PR-SEC-1.6`). The GUI's session cookie is `Secure` by default
(`GUI_COOKIE_SECURE`), so it is only sent over the HTTPS door; set it to `false` only for plain-HTTP development.

Slow statements: Postgres logs any statement slower than `POSTGRES_SLOW_QUERY_MS` (default 500 ms, set in `.env`; `-1` turns
it off) with its duration and text: `docker compose logs postgres | grep duration`.

Back up and restore the database (`scripts/db_backup.sh`, `scripts/db_restore.sh`): one `pg_dump` custom-format file,
checked before it is kept, mode 0600 (it holds every table, including each module's SME invoker secret).

```bash
cd smo
scripts/db_backup.sh --compose                        # the stack's postgres container; writes smo/backups/smo-<UTC>.dump
SMO_DATABASE_URL=... scripts/db_backup.sh out.dump     # or any database, with a pg_dump at least as new as the server
docker compose stop $(docker compose config --services | grep -vx postgres)   # nothing may write during a restore
scripts/db_restore.sh --compose --yes backups/smo-<UTC>.dump                    # replaces the data, in one transaction
```

A restore needs `--yes`, replaces every object in the dump (`--clean --if-exists`) and, being one transaction, changes
nothing if it fails. WAL archiving and point-in-time recovery are not set up yet (`PR-DB-6.3`).

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
under `smo/`. CI validates `docker-compose.yml` with `docker compose config` and
brings the full stack up (`compose-e2e`: the whole runbook, §2–§27, replayed live, plus the `a1_mock_net` isolation).

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
  HISTORY.md                audit trail: closed items, spec audit, wave exit reviews (cited by code comments)
  docker-compose.yml        deployment topology, incl. the isolated a1_mock_net network
  Dockerfile                one image, parameterised by the MODULE build arg
  migrations/001_init.sql   the consolidated Postgres schema (the Alembic baseline revision 0001)
  migrations/versions/      Alembic revisions on top of it; scripts/migrate.py applies them (docs/adr/0001-schema-migrations.md)
  shared/smo_shared/        DB session, FSM base, RFC 7807 errors, R1Client, webhook helpers,
                            correlation ids, SQLite test engine (testing.py), time helpers
  <module>/README.md        the module's HLD + LLD + unit-test document: design, data model, API, tests, status
  <module>/app/             one directory per SMO module (models.py, statemachine.py, main.py)
  <module>/tests/           that module's unit tests (standalone, SQLite)
  mock-near-rt-ric/         A1-P test double (A1 Related's southbound)
  mock-o1-adaptor/          NETCONF / RESTCONF O1 test double (RAN NF OAM's southbound)
  sdk/smo_sdk/              AI Runtime SDK: data, analytics, models, lifecycle, intent, platform clients
  gui/                      React + TypeScript operator console (nginx)
  gui-bff/                  GUI backend-for-frontend: auth, RBAC (app/rbac.py), audit, R1 proxy
  samples/
    build_csar.py           builds samples/<name>.csar from samples/<name>/
    <name>-rapp/README.md   each sample's README: what it does, design, package, API, tests
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
    ARCHITECTURE.md         layers, golden rules, R1 conventions, standards per service
    RAPP_PACKAGING.md       rApp CSAR layout, manifest.yaml / capabilities.yaml, per-sample parameter tables
    STANDARDS.md            frozen decisions, TS 28.105/28.104/28.312 compliance matrices, runtime realisation
    call-flows/             27 Mermaid sequence diagrams of end-to-end journeys
    openapi/                committed OpenAPI spec per service, checked against the live schema
```

## Documentation map

| Document | Contents |
|---|---|
| [`README.md`](README.md) | What this is, modules, quickstart, layout (this file) |
| `<module>/README.md` | Per-module HLD, LLD and unit tests; linked from the module tables above |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Layers, golden rules, R1 conventions, standards per service |
| [`docs/RAPP_PACKAGING.md`](docs/RAPP_PACKAGING.md) | How an rApp is packaged, each manifest / capabilities parameter, and what each sample declares |
| [`docs/call-flows/`](docs/call-flows/) | One Mermaid sequence diagram per end-to-end journey (01–27) |
| [`docs/openapi/`](docs/openapi/) | Generated OpenAPI spec per service |
| [`gui/README.md`](gui/README.md) | Operator GUI: running it, roles, security, pages, screenshots |
| [`OPEN_ITEMS.md`](OPEN_ITEMS.md) | Items still open, and deliberate scope cuts |
| [`CHANGELOG.md`](CHANGELOG.md), [`docs/RELEASES.md`](docs/RELEASES.md) | What changed for an operator; the tag scheme (`smo-vX.Y.Z`, semver) and how a release is cut |
| [`docs/NOTIFICATIONS.md`](docs/NOTIFICATIONS.md) | Every outbound call to a caller-registered destination, classified (outbox or inline), kept in step with the code by a test |
| [`docs/adr/`](docs/adr/) | Architecture decision records (`0001`: Alembic, one history) |
| [`docs/SECRETS.md`](docs/SECRETS.md) | Every secret: owner, how it is supplied, how it is stored (hash or plaintext), how it is rotated |
| [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md) | Command-by-command live demo against `docker compose up` |
| [`CLAUDE.md`](CLAUDE.md) | Working practice, conventions, full test battery |
| [`docs/STANDARDS.md`](docs/STANDARDS.md) | Reference: TS 28.105/28.104/28.312 compliance matrices, runtime realisation and the frozen design decisions |
| [`HISTORY.md`](HISTORY.md) | Audit trail only: decisions, closed items and the spec audit. Not needed to use or extend the platform; code comments cite its IDs |
