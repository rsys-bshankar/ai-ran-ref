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
                          the generic FSM base every module's lifecycle extends
  migrations/001_init.sql  the consolidated schema — every table from all
                          eight LLD passes, unified into one buildable DB
  <module>/app/           one directory per SMO module (see the table below)
    models.py              SQLAlchemy ORM
    statemachine.py         FSM(s), where the module has real lifecycle logic
    main.py                  FastAPI routes
  <module>/tests/          pytest suites for the state machines — run, not
                          just described (see "Running the tests" below)
  docs/call-flows/         Mermaid sequence diagrams stitching multiple
                          modules' LLDs into end-to-end journeys
  docker-compose.yml       Phase 1 deployment topology (SMO Design v1.3 section 4)
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
| A1 Related | `a1-related/` | — (A1-ML dormant, out of scope — see the module's LLD section 0) |
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
```

## Running the tests

Every state machine has a real pytest suite, run against an in-memory
SQLite DB (production runs against the full Postgres schema in
`migrations/001_init.sql` — see the note in `rapp-mgmt/tests/test_upgrade.py`
about the one place a test stands in a minimal cross-module FK stub).

```bash
pip install -e shared
for m in onboarding rapp-mgmt ran-nf-oam ai-ml-workflow so-smos; do
  (cd $m && PYTHONPATH=.:../shared python -m pytest tests/ -v)
done
```

36 tests total, all passing as of this build — covering the cascade-delete
guard, upgrade auto-rollback, the `PARTIAL_SUCCESS` decomposed-PATCH
aggregation, the O1 Adaptor endpoint health lifecycle, the full AI/ML
certification pipeline plus retraining re-entry, and SO SMOS's fail-fast
dispatch semantics.

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
- Every module's actual southbound integration (O1 Adaptor `PATCH` calls,
  `docker run` invocations, A1AP passthrough) is elided in favor of
  recording the correct state transition — this is a reference build of
  the SMO's own object model and orchestration logic, not a full O-RAN
  stack.
