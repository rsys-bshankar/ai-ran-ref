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
| rApp Management | `rapp-mgmt/` | `RAppInstance` FSM + upgrade auto-rollback; CRASH/TERMINATE now push a DME deregistration for the instance's own producer registrations |
| RAN NF OAM | `ran-nf-oam/` | `WriteConfigJob`, `SoftwareManagementJob`, `O1AdaptorEndpoint` health — 3 FSMs; CM writes dispatch as real NETCONF `<edit-config>` RPCs (RESTCONF-provisioned MEs rejected, not implemented) |
| A1 Related | `a1-related/` | — (A1-ML dormant, out of scope — see the module's LLD section 0). Its real southbound dependency is `mock-near-rt-ric/`, on an isolated network segment. |
| NFO | `nfo/` | `NFDeployment` |
| FOCOM | `focom/` | — |
| AI/ML Workflow | `ai-ml-workflow/` | `AIMLModel` FSM, `InferenceJob` FSM, individual + coordination-group retrain propagation (a guard-KPI breach now actually fires `RETRAIN` on every `ACTIVE` group member, not just computes a bool) |
| RAN Analytics | `ran-analytics/` | — |
| Policy Mgmt & Info | `policy-mgmt/` | — |
| SO SMOS | `so-smos/` | dispatch table, fail-fast execution |
| SA SMOS | `sa-smos/` | remedial-action dispatch (`RECONNECT` resolved via SO SMOS order lookup + NFO Heal; a coordination-group-scoped monitor always dispatches a group retrain via AI/ML Workflow instead; `ROLLBACK` honestly unresolved — see below) |

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

**217 tests total, all passing** as of this build: 207 unit tests across
all fourteen modules plus the mock, and 10 integration tests proving real
cross-service wiring. Notably including: the cascade-delete guard (now
actually reachable via `usage/start`/`usage/stop` — see "Real bugs"
below), upgrade auto-rollback, the `PARTIAL_SUCCESS` decomposed-PATCH
aggregation, the O1 Adaptor endpoint health lifecycle, the full AI/ML
certification pipeline plus retraining re-entry, SO SMOS's fail-fast
dispatch semantics, A1 Related's real round trip to the mock Near-RT RIC
(both `ENFORCED` and `REJECTED` paths), a three-hop chain (SO SMOS → A1
Related → mock Near-RT RIC) proving the dispatch table isn't calling
into a stub, a full onboard-to-deploy chain (Onboarding → NFO →
rApp Management) proving NFO's real `NFDeploymentDescriptor` row — not
`packageId` — makes it all the way through, RAN NF OAM's real NETCONF
`<edit-config>` dispatch (mocked transport, real RPC-reply parsing), SA
SMOS's `RECONNECT` resolving a concrete `nfDeploymentId` via a live SO
SMOS order lookup, R1 Termination's proxy forwarding a POST body, method,
headers (`Host` stripped, others kept) and query params while passing a
non-200 upstream status straight through, the mock Near-RT RIC's
`UpdatePolicy` route (previously entirely untested), NFO's
`query_operation_status` route reading back the exact `LCMOperation` rows
Instantiate/Heal/Scale wrote (also previously entirely untested), RAN
Analytics' `RegisterAnalyticsProducer` asserted against the actual SME
service-registration payload it publishes, FOCOM's `deprovision_resource`
stub asserted to succeed for an arbitrary, never-provisioned `resource_id`,
and A1 Related's `GET`/`PUT`/`DELETE /policies/{id}`, `DELETE
/policies/subscriptions/{id}`, and `DELETE /ei-types/{id}` — five whole
routes with zero test coverage at all until that pass. Onboarding then
topped it: 6 of its 8 routes (`GET /packages`, deprecate, cancel-delete,
delete, and both usage-registration routes) had zero coverage, including
neither half of the cascade-delete guard its own docstring calls out —
a blocking dependent child package, or an active usage registration —
ever having been exercised. SME's `unsubscribe_events` route and its
`notify_service_change` function — real event-filtering and
best-effort-delivery logic not yet wired into any route — were also
both entirely untested until this pass.
so-smos, ran-analytics, focom, rapp-mgmt, and dme — previously among the
thinnest-covered modules — now have route-level
coverage too, not just dispatch-logic coverage, closing OPEN_ITEMS.md's
test-coverage-parity item; `ran-nf-oam` went from FSM-only coverage to 20
tests (its first route-level and NETCONF-client tests) resolving the CM
sync method design decision, then to 21 when a `GET /health` route was
added to answer the callback URL `subscribe_pm` registers with DME (see
"Real bugs" below and OPEN_ITEMS.md section 5). `a1-related` picked up
the identical fix in the same pass — `register_ei_type` registers the
same-shaped callback with DME — going from 18 tests to 19.

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
- **`RAppInstance.RECOVER` had no HTTP route at all** — the FSM
  transition (`FAULTED -> DEPLOYING`) existed and was unit-tested
  directly against the FSM, but nothing in `rapp-mgmt/app/main.py` ever
  fired it; a critically faulted rApp instance had no API path back to
  `RUNNING`. Fixed with `POST /instances/{id}/recover`.
- **Onboarding's cascade-delete guard was unreachable from ordinary rApp
  deployment** — `PackageUsageRegistration` rows are only ever
  created/stopped via Onboarding's `usage/start`/`usage/stop`, and
  nothing in `rApp Management`'s `CreateInstance`/`TerminateInstance`
  ever called them, so the guard's active-usage condition could never
  fire from a real deployment. Fixed by wiring both calls in.
- **DME's `CreateDataJob` never validated against the actual
  `DataOffer`** — only against the global set of known wire-value
  methods, so a consumer could request a delivery method the specific
  `dmeTypeId`'s producer never actually offered. Fixed with a real
  cross-check.
- **Two more real Postgres-schema bugs, both in `rapp_instance`,
  found while fixing the above**: `pending_upgrade_instance_id` — read
  and written throughout `rapp-mgmt/app/upgrade.py` and `main.py` — was
  **entirely missing** from `migrations/001_init.sql` (only present in
  the SQLAlchemy model), and `oauth_client_id` was `NOT NULL` in the
  migration even though `_revoke_credential` explicitly sets it to
  `NULL` on termination (closing v1.3's RT-3 finding). Both would have
  crashed against real Postgres on first use; neither was ever caught
  because SQLite's unit tests build their schema straight from the ORM
  models, never from this file, and the migration-Postgres CI job only
  checks table *count*, not columns. Verified fixed against a real local
  Postgres 16 instance, not just SQLite.
- **`AI/ML Workflow`'s `RequestTraining` crashed on every ordinary
  retrain** — it always fired the model FSM's `TRAIN` event regardless of
  the model's actual state, but `TRAIN` is only a legal transition from
  `REGISTERED`; calling `RequestTraining` on an `ACTIVE` model (the normal
  retrain case) hit an unhandled `IllegalTransition` (500). Fixed by
  firing `TRAIN` or `RETRAIN` based on the model's actual state, and by
  explicitly cancelling an orphaned in-flight `TrainingJob` rather than
  silently overwriting `model.training_job_id` when a second
  `RequestTraining` call arrives mid-flight.
- **`AI/ML Workflow`'s coordination-group lookup never actually worked
  under any test until this pass gave it route-level coverage** —
  `report_performance` located a model's coordination group with
  `MLModelCoordinationGroup.member_model_ids.any(model.model_id)`, real
  Postgres `ANY(array)` SQL with no SQLite equivalent under
  `member_model_ids`' JSON fallback (`sqlite3.OperationalError: no such
  function: ANY`). Replaced with an in-Python membership filter — and
  that filter itself needed a string comparison, not
  `model.model_id in group.member_model_ids` directly, since SQLite's
  JSON fallback has no UUID item type and reads `member_model_ids` back
  as plain strings where Postgres's native `ARRAY(Uuid)` round-trips
  real `uuid.UUID` objects. Both were caught by writing the first tests
  ever to exercise this code path, not found by inspection.

## What's deliberately incomplete

Matching the LLDs' own honesty about open items rather than papering over
them:

- **A1-ML operations** (`a1-related/`) — schema-dormant, no routes. Building
  them means implementing genuine A1AP behavior, out of this project's
  declared scope categorically (see the A1 Related LLD section 0).
- **`ROLLBACK`** (`sa-smos/app/main.py`) — raises a clear, specific error
  (`ROLLBACK_HISTORY_UNAVAILABLE`) rather than picking one of several
  plausible meanings: rApp Management's own upgrade machinery deletes the
  prior `RAppInstance` row on a successful commit, so no version history
  survives anywhere in this build to roll back to. `RECONNECT` is now
  resolved (see the table above).
- **RESTCONF-provisioned MEs in RAN NF OAM's `WriteConfigurationChanges`**
  (`ran-nf-oam/app/main.py`) — the confirmed dispatch protocol is NETCONF
  only; an ME with `o1_protocol=RESTCONF` is rejected with
  `PROTOCOL_NOT_SUPPORTED` rather than silently applied. The NETCONF RPC
  itself is still sent as XML over plain HTTP, not real SSH/ncclient
  transport, matching this build's all-HTTP-JSON pragmatism everywhere
  else.
- **`WEIGHTED_TRIGGERS`** (`ai-ml-workflow/`) — raises `NotImplementedError`;
  needs real noise-floor data before it can be designed, not invented now.
- Every module's actual southbound integration beyond A1 Related's mock
  Near-RT RIC and RAN NF OAM's NETCONF client (`docker run` invocations,
  etc.) is elided in favor of recording the correct state transition —
  this is a reference build of the SMO's own object model and
  orchestration logic, not a full O-RAN stack.
