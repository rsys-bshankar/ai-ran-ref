# AI-RAN SMO — Phase 1 Reference Implementation

Repo code, generated from the LLDs of all fourteen SMO modules (companion to
`SMO Design Document v1.3`, the `AI-RAN Framework Consolidated Reference`,
and the `O-RAN-SC Repo Inventory` — see the session's Design blueprint for
the architecture pyramid and repo-adoption map this build follows).

## Stack

**Python 3.11 + FastAPI + SQLAlchemy + Pydantic**, one consistent stack
across all fourteen services, rather than literally forking each ADOPT
repo's original language (Go for `nonrtric-plt-sme`, Java for the ICS
reference, Python for `pti-o2`). The ADOPT repos stay the *pattern*
references — TOSCA packaging, CAPIF resource shapes, O2ims route layout —
this build consolidates on one stack for Phase 1 buildability. OpenAPI
specs come from FastAPI's own schema generation (`/docs` on any running
service) rather than hand-duplicated YAML.

## Layout

```
smo/
  shared/smo_shared/     db session, identity equivalence, RFC 7807 errors,
                          the generic FSM base every module's lifecycle
                          extends, and testing.py (a SQLite test-engine
                          helper — see "Running the tests")
  migrations/001_init.sql  the consolidated schema — every table from all
                          eight LLD passes, unified into one buildable DB
  <module>/app/           one directory per SMO module (see the table below)
    models.py              SQLAlchemy ORM
    statemachine.py         FSM(s), where the module has real lifecycle logic
    main.py                  FastAPI routes
  <module>/tests/          pytest suites — real unit tests against each
                          module in isolation, run, not just described
  tests_integration/       cross-service integration tests — loads multiple
                          modules into ONE process (loader.py) and routes
                          real R1Client/A1TerminationClient calls between
                          their TestClients (mesh.py) instead of mocking
                          them out, so an actual bug in how two modules
                          talk to each other gets caught
  mock-near-rt-ric/        the isolated A1-P test double (closes RT-7) —
                          not an SMO module, A1 Related's only southbound
                          dependency
  docs/call-flows/         Mermaid sequence diagrams stitching multiple
                          modules' LLDs into end-to-end journeys
  docker-compose.yml       Phase 1 deployment topology (SMO Design v1.3
                          section 4), including the isolated a1_mock_net
                          network segment
  Dockerfile               one Dockerfile, parameterized by MODULE build arg
```

## The fourteen modules

| Module | Directory | Lifecycle? |
|---|---|---|
| SME | `sme/` | — |
| DME | `dme/` | — |
| R1 Termination | `r1-termination/` | — (gateway, no domain schema) |
| Software Package Onboarding | `onboarding/` | `ApplicationPackage` FSM |
| rApp Management | `rapp-mgmt/` | `RAppInstance` FSM + upgrade auto-rollback |
| RAN NF OAM | `ran-nf-oam/` | `WriteConfigJob`, `SoftwareManagementJob`, `O1AdaptorEndpoint` health — 3 FSMs |
| A1 Related | `a1-related/` | — (A1-ML dormant, out of scope — see the module's LLD section 0). Its real southbound dependency is `mock-near-rt-ric/`, on an isolated network segment. |
| NFO | `nfo/` | `NFDeployment` |
| FOCOM | `focom/` | — |
| AI/ML Workflow | `ai-ml-workflow/` | `AIMLModel` FSM, `InferenceJob` FSM, retrain propagation |
| RAN Analytics | `ran-analytics/` | — |
| Policy Mgmt & Info | `policy-mgmt/` | — |
| SO SMOS | `so-smos/` | dispatch table, fail-fast execution |
| SA SMOS | `sa-smos/` | remedial-action dispatch (2 of 4 action types honestly unresolved) |

## Running it

```bash
cd smo
docker compose up --build
# R1 Termination on :8080 — every rApp-facing call goes through it
# Postgres on :5432, seeded from migrations/001_init.sql
# mock-near-rt-ric has no published port — reachable only from a1-related,
# on the isolated a1_mock_net network (closes RT-7)
```

## Running the tests

```bash
pip install -e shared

# per-module unit tests (each module in isolation, in-memory SQLite)
for m in onboarding rapp-mgmt ran-nf-oam ai-ml-workflow so-smos a1-related \
         sme dme r1-termination nfo focom ran-analytics policy-mgmt sa-smos \
         mock-near-rt-ric; do
  (cd $m && PYTHONPATH=.:../shared python -m pytest tests/ -v)
done

# cross-service integration tests (multiple modules loaded into one
# process, real calls between them — see tests_integration/)
PYTHONPATH=shared python -m pytest tests_integration/ -v
```

**127 tests total, all passing** as of this build: 117 unit tests across
all fourteen modules plus the mock, and 10 integration tests proving real
cross-service wiring. Notably including: the cascade-delete guard, upgrade
auto-rollback, the `PARTIAL_SUCCESS` decomposed-PATCH aggregation, the O1
Adaptor endpoint health lifecycle, the full AI/ML certification pipeline
plus retraining re-entry, SO SMOS's fail-fast dispatch semantics, A1
Related's real round trip to the mock Near-RT RIC (both `ENFORCED` and
`REJECTED` paths), a three-hop chain (SO SMOS → A1 Related → mock
Near-RT RIC) proving the dispatch table isn't calling into a stub, and a
full onboard-to-deploy chain (Onboarding → NFO → rApp Management) proving
NFO's real `NFDeploymentDescriptor` row — not `packageId` — makes it all
the way through. so-smos, ran-analytics, and focom — previously the
thinnest-covered modules — now have route-level coverage too, not just
dispatch-logic coverage, closing OPEN_ITEMS.md's test-coverage-parity item.

### SQLite portability notes (`shared/smo_shared/testing.py`)

Every model uses genuinely Postgres-shaped types (`ARRAY`, `JSONB`-style
`JSON`, native `Uuid`) for production fidelity, with a `.with_variant(...)`
SQLite fallback so the same models are unit-testable without a live
Postgres. Two real gaps surfaced while wiring this up, both now fixed
project-wide, not just worked around locally: `JSON`'s default
`none_as_null=False` stores a Python `None` as the literal JSON text
`"null"` rather than SQL `NULL` (broke a CHECK constraint until fixed),
and `ARRAY(Uuid)`'s SQLite JSON fallback needs a UUID-aware JSON encoder
(the stdlib `json` module can't serialize a raw `uuid.UUID`) — both
handled by `make_test_engine()`, so no individual test file needs to
rediscover either issue.

### Real bugs this pass found (not hypothetical — each had a failing test until fixed)

Writing the tests, not just the code, is what surfaced these:

- **R1 Termination never stripped the module prefix before forwarding** —
  would 404 against every real backend in production (`sme/app/main.py`'s
  own route is `/published-apis/...`, never `/sme/published-apis/...`).
  Caught building the integration harness, before a single test ran.
- **NFO queried FOCOM's inventory with the wrong parameter name**
  (`resourceType` vs. `resource_type`) — a *silent* bug, not a crash:
  FOCOM just always fell back to its default, ignoring NFO's actual
  request.
- **SO SMOS's dispatch table never checked downstream HTTP status codes**
  — a real 422 from A1 Related (an unknown policy type) was recorded as
  `COMPLETED`, silently defeating the whole fail-fast guarantee SO/SA
  SMOS LLD section 1.1 exists to provide.
- **A1 Related's own `policy_id` and the Near-RT RIC's internally-generated
  policy identifier were never linked** — every status query after the
  first `create_policy` call returned `SUSPENDED` ("unknown policyId"),
  because A1 Related was querying the mock using an ID the mock never
  issued. Fixed with a stored `near_rt_ric_policy_id` mapping.
- **SME's own SQL schema contradicted its own LLD's stated design
  decision** — `UNIQUE(service_name, producer_id)` was in the migration,
  but the LLD's actual rule is that `service_name` alone must be unique
  (a different producer registering the same name is the conflict; the
  same producer re-registering it should update in place, which the old
  constraint would have silently broken).
- **`Onboarding.OnboardPackage` had no handling for an unreachable
  `location`** — crashed with an unhandled 500 instead of routing to
  `FAILED`, which is itself a legitimate, expected outcome.
- Two SQLAlchemy portability bugs (`::int` cast syntax is Postgres-only;
  `sqlalchemy.dialects.postgresql.UUID`/`JSONB` don't compile on SQLite)
  and one ORM cascade config gap (deleting a `ServiceProfile` tried to
  null out its child's primary key instead of deleting the child row).
- **`NFDeploymentDescriptor` was never populated** — NFO+FOCOM LLD
  section 2's own stated design is that it's derived from an onboarded
  package's TOSCA `Definitions/` at onboarding time; `rApp Management`
  was passing `packageId` directly where NFO expected a real
  `nfDeploymentDescriptorId`, silently correct only because SQLite's test
  engine doesn't enforce the FK it relies on (a real Postgres run rejects
  it outright — verified). Fixed with a new NFO `CreateDescriptor`
  endpoint, called from Onboarding's `OnboardPackage` flow once
  validation succeeds; `rApp Management` now consumes the real ID via
  Onboarding's `onboarding-status` response. See
  `tests_integration/test_cross_service.py`'s
  `test_onboarding_to_rapp_management_full_deploy_creates_real_nf_deployment_descriptor`.
- **SO SMOS's `CancelOrder` never actually persisted the `CANCELLED`
  status** — it mutated the `steps` JSON column's list in place, which
  SQLAlchemy's change detection never tracks without
  `sqlalchemy.ext.mutable`; `commit()`'s default expire-on-commit then
  re-fetched the unchanged row, silently discarding the edit every time.
  Fixed by reassigning `order.steps` to a new list instead of mutating
  the old one's dicts. Caught while adding the route its own test suite
  never previously covered — no test had exercised `cancel_order` at all.
- **RAN Analytics' `RegisterAnalyticsProducer` crashed on the same
  producer re-registering the same `analyticsType`** — an unhandled
  `IntegrityError` on the `(producer_id, analytics_type)` composite
  primary key, the same shape of bug as SME's `RegisterService` had.
  Fixed with the same update-in-place upsert.

## What's deliberately incomplete

Matching the LLDs' own honesty about open items rather than papering over
them:

- **A1-ML operations** (`a1-related/`) — schema-dormant, no routes. Building
  them means implementing genuine A1AP behavior, out of this project's
  declared scope categorically (see the A1 Related LLD section 0).
- **`RECONNECT`/`ROLLBACK`** (`sa-smos/app/main.py`) — raise a clear error
  rather than silently picking one of several plausible meanings.
- **`upgradeTimeoutSeconds` default (300s)** (`rapp-mgmt/`) — not a
  researched value, flagged as a placeholder in both the LLD and the code.
- **`WEIGHTED_TRIGGERS`** (`ai-ml-workflow/`) — raises `NotImplementedError`;
  needs real noise-floor data before it can be designed, not invented now.
- Every module's actual southbound integration beyond A1 Related's mock
  Near-RT RIC (O1 Adaptor `PATCH` calls, `docker run` invocations) is
  elided in favor of recording the correct state transition — this is a
  reference build of the SMO's own object model and orchestration logic,
  not a full O-RAN stack.
