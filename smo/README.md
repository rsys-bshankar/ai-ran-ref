# AI-RAN SMO — Phase 1 Reference Implementation

Repo code, generated from the LLDs of all fourteen SMO modules (companion to
`SMO Design Document v1.3`, the `AI-RAN Framework Consolidated Reference`,
and the `O-RAN-SC Repo Inventory` — see the session's Design blueprint for
the architecture pyramid and repo-adoption map this build follows). The
formal specs this build is grounded against — 3GPP 5G Core OpenAPI, O-RAN
O1/O2 YANG and information models — live as sibling reference material in
[`../specs/`](../specs/README.md). `SPEC_AUDIT.md` compares this build
against those formal specs directly (distinct from `OPEN_ITEMS.md`
section 5's audits against the O-RAN-SC source-code repos).

## Project status

Three separate audits, three separate ground truths, kept deliberately
apart rather than merged into one score:

| Audit | Ground truth | Status |
|---|---|---|
| `OPEN_ITEMS.md` section 5 | 18 cloned O-RAN-SC repos (ADOPT/REFERENCE source code) | **Fully closed** |
| `SPEC_AUDIT.md` | Formal 3GPP/O-RAN specs in `../specs/` | **Closed for the 6 modules with a matching spec file** |
| `OPEN_ITEMS.md` sections 1-4 | This build's own LLDs and internal completeness | **Closed except 3 stakeholder-blocked design decisions** |

**O-RAN-SC source-code audit (section 5) — done.** Every module with an
actual O-RAN-SC repo to diff against (SME, DME, Onboarding+rApp Mgmt,
RAN NF OAM, A1 Related, NFO+FOCOM, AI/ML Workflow, RAN Analytics) had its
real API surface compared route by route against that repo. Every real
gap found is closed — confirmed by re-reading the section end to end, not
assumed. R1 Termination, Policy Mgmt, SO SMOS, and SA SMOS have no
O-RAN-SC repo match at all (a confirmed `BUILD` verdict at Blueprint
time), so there is nothing upstream to audit completeness against for
those four.

**Formal-spec audit (`SPEC_AUDIT.md`) — done where a spec exists, but
coverage is partial.** Six modules had a directly relevant formal spec
file already sitting in `../specs/`: **RAN NF OAM** (TS28319/28111/
28532/28550 + the O1NRM YANGs), **FOCOM** (the real O2IMS
`o-cloud-im/` information model), **Policy Mgmt** (TS28312 IntentNrm),
**SME** (the real CAPIF core source, read closely for security/
trust-model detail beyond section 5's own pass), and **AI/ML
Workflow**/**RAN Analytics** (TS28105 AI/ML NRM and TS28104 MDA NRM —
this file previously said no relevant spec existed for these two; that
was stale, the files were already present, just never cataloged). For
all six, every small and moderate closeable finding is closed (missing
enum/column fields, field-name mismatches, FOCOM's `/inventory` reshape
toward `OCloud`, Policy Mgmt's matching-field rename to
`supportedExpectationObjectType`, SME's invoker-onboarding trust-model
flip, SME's real Trusted Invokers registry, AI/ML Workflow's
`mLTrainingType`), and every large/structural finding (MSAC RBAC,
DN/typed O1 addressing, file/streaming transport, FOCOM's
Provisioning/Artifacts/Cluster/Infrastructure categories, and — new
this pass — AI/ML Workflow's/RAN Analytics's whole TS28105/TS28104 NRM
containment trees and FL/RL modeling) was confirmed as a **deliberate
Phase-1 scope cut**, not a bug — both modules target the O-RAN-SC
`aiml-fw`/`aiml-fw-apm` reference architecture instead, already
confirmed in section 5. See "What's deliberately incomplete" below.
Two open items are architectural, not code: Policy Mgmt's (now Intent
Service's — see "Phase 2" below) Intent-to-RMIH matching (producer-side
push vs. the spec's implied consumer-side LDN selection), and — new
this pass — RAN Analytics's
`analytics_type` enum constraint and missing threshold-based
conditional reporting, both real but moderate/breaking, left for a
deliberate follow-up rather than guessed at.

Not yet audited against a formal spec at all, because no relevant spec
file exists in `../specs/` yet: **DME** (ICS's own spec set isn't
there), **A1 Related** (3GPP/O-RAN A1 specs aren't there — section 5's
source-code audit remains the only ground truth), and
**Onboarding/rApp Mgmt** (TOSCA/rApp packaging specs aren't there).
Also cataloged in `../specs/` but never compared against: the O-RAN
WG4/WG5 O-RU/O-CU/O-DU management-plane YANGs — likely out of scope
given this build's single-node topology, but genuinely unconfirmed,
not assumed.

**Internal completeness (`OPEN_ITEMS.md` sections 1-4) — closed except 3
items blocked on data, not effort.** Section 2's repo/lifecycle gaps and
section 3's call-flow gaps are closed; section 4's test-coverage
priority item is closed (current counts: ~525 unit tests across the 16
services, plus 5 in `shared/tests/`, 111 in `gui-bff/tests/`, and 29
cross-service integration tests). Section 1's three design decisions
remain open, but genuinely need a stakeholder's real data or a scope
call rather than more engineering effort — see "What's deliberately
incomplete" below.

**Demo depth (`DEMO_RUNBOOK.md`) — the most recently closed backlog.**
Seven previously-undemonstrated, already-implemented pieces of real
functionality (Onboarding's package priming lifecycle, DME's
type-subscription mechanism, FOCOM's TEIV topology export, AI/ML
Workflow's feature groups, SA SMOS's coordination-group remedial action,
SME's `apiId` event-subscription filtering, A1 Related's service
supervision sweep) each got a runbook section and a matching
`tests_integration/test_demo_runbook.py` step, each verified against a
real local Postgres 16 instance rather than just SQLite — which is how 4
genuine production bugs got caught that no unit test had ever touched
(see "Real bugs this pass found" below). `OPEN_ITEMS.md`'s own
"Suggested next pass" section confirms this list is now exhausted.

**Bottom line — what's actually remaining:** extending the formal-spec
audit to the three still-unaudited modules (DME, A1 Related,
Onboarding/rApp Mgmt — no spec file exists yet for any of them) and the
WG4/WG5 YANGs (real, unstarted work); a scoped follow-up pass on RAN
Analytics's own moderate/breaking findings (the `analytics_type` enum
constraint, threshold-based conditional reporting) and AI/ML Workflow's
(`requestStatus` vocabulary, cancel/suspend-flag support); Policy
Mgmt's and RAN Analytics's own architecture questions (need a
stakeholder decision, not code); and the three section 1 design
decisions (blocked on real data/algorithm/scope input). Everything else
large/structural is a **confirmed** Phase-1 scope cut, not a gap.

**Phase 2 — AI Platform Service Decomposition (in progress, new
direction).** Per an external architecture review (the "SMO Actions"
documents) and explicit agreement to proceed: `ai-ml-workflow/` is being
split into three real platform services matching TS 28.105's own NRM
boundaries (**AIMgF** for lifecycle orchestration, **MLMR** for the
model repository, **MLLF** for loading/activation), `ran-analytics/` is
gaining a sibling **MDAF** service for TS 28.104-shaped analytics
reporting, `policy-mgmt/` is being renamed to **Intent Service**
(TS 28.312) — a correction found while starting this work: it already
had no policy/rule/constraint code to leave behind, contrary to the
review's own assumption — and a new **AI Runtime SDK** (`sdk/`) is being
added so rApps stop calling module REST endpoints directly. This is a
genuine, deliberate reversal of the Phase-1 scope choice `SPEC_AUDIT.md`
documented for AI/ML Workflow and RAN Analytics, not a bug fix. Full
architecture in `docs/architecture/AI_PLATFORM_BASELINE.md` and
`docs/architecture/SERVICE_OWNERSHIP_MATRIX.md`; per-service detail in
`docs/ownership/`. Sequenced in four waves — **Wave 0** (this
architecture freeze, done), Wave 1 (service decomposition), Wave 2
(AIMgF's own state machines and domain model), Wave 3 (R1 contracts and
OpenAPI standardization) — each a separate, reviewable step; do not
reorder them.

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
  mock-o1-adaptor/         the NETCONF-shaped O1 Adaptor test double —
                          not an SMO module, RAN NF OAM's real southbound
                          dependency for CM writes (real RFC 6241
                          <edit-config> RPCs, previously answered by
                          nothing in this build's own topology)
  docs/call-flows/         Mermaid sequence diagrams stitching multiple
                          modules' LLDs into end-to-end journeys
  docs/openapi/            committed OpenAPI spec per module, generated
                          from each app's own real app.openapi() output
                          (scripts/generate_openapi_specs.py) — a real
                          contract change now shows up as a diff, caught
                          if it drifts by tests_integration/test_openapi_specs.py
  scripts/                 one-off tooling, e.g. generate_openapi_specs.py
  samples/hello-world-rapp.csar  a real, valid sample rApp package — see
                          DEMO_RUNBOOK.md
  DEMO_RUNBOOK.md          real, copy-pasteable commands walking a live
                          docker compose up through a sample rApp's full
                          onboard -> deploy -> bootstrap -> operate ->
                          retire lifecycle
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
| RAN NF OAM | `ran-nf-oam/` | `WriteConfigJob`, `SoftwareManagementJob`, `O1AdaptorEndpoint` health — 3 FSMs; CM writes dispatch as real NETCONF `<edit-config>` RPCs (RESTCONF-provisioned MEs rejected, not implemented). Its real southbound dependency is `mock-o1-adaptor/`, which actually answers them. |
| A1 Related | `a1-related/` | — (A1-ML dormant, out of scope — see the module's LLD section 0). Its real southbound dependency is `mock-near-rt-ric/`, on an isolated network segment. |
| NFO | `nfo/` | `NFDeployment` |
| FOCOM | `focom/` | — |
| AI/ML Workflow | `ai-ml-workflow/` | `AIMLModel` FSM, `InferenceJob` FSM, individual + coordination-group retrain propagation (a guard-KPI breach now actually fires `RETRAIN` on every `ACTIVE` group member, not just computes a bool) |
| RAN Analytics | `ran-analytics/` | — |
| Intent Service (formerly Policy Mgmt & Info — renamed in Wave 1 of the AI Platform Service Decomposition, see "Project status" above) | `intent-service/` | — |
| SO SMOS | `so-smos/` | dispatch table, fail-fast execution |
| SA SMOS | `sa-smos/` | remedial-action dispatch (`RECONNECT` resolved via SO SMOS order lookup + NFO Heal; a coordination-group-scoped monitor always dispatches a group retrain via AI/ML Workflow instead; `ROLLBACK` honestly unresolved — see below) |

## Running it

```bash
cd smo
docker compose up --build
# R1 Termination on :8080 — every rApp-facing call goes through it
# Operator GUI on :3000 — see gui/README.md (set GUI_ADMIN_PASSWORD first)
# Postgres on :5432, seeded from migrations/001_init.sql
# mock-near-rt-ric has no published port — reachable only from a1-related,
# on the isolated a1_mock_net network (closes RT-7)
```

## Running the tests

```bash
pip install -e shared

# per-module unit tests (each module in isolation, in-memory SQLite)
for m in onboarding rapp-mgmt ran-nf-oam ai-ml-workflow so-smos a1-related \
         sme dme r1-termination nfo focom ran-analytics intent-service sa-smos \
         mock-near-rt-ric mock-o1-adaptor; do
  (cd $m && PYTHONPATH=.:../shared python -m pytest tests/ -v)
done

# cross-service integration tests (multiple modules loaded into one
# process, real calls between them — see tests_integration/)
PYTHONPATH=shared python -m pytest tests_integration/ -v

# regenerate docs/openapi/<module>.json after a real route/schema change
# (test_openapi_specs.py fails CI if a committed spec drifts from this)
PYTHONPATH=shared python scripts/generate_openapi_specs.py

# validate docker-compose.yml itself (no Docker daemon needed — this
# only parses and renders the file; CI runs it automatically too, the
# docker-compose-config job)
docker compose config --quiet
```

**496 tests total, all passing** as of this build: 480 unit tests across
all fourteen modules plus the two mocks, and 16 integration tests proving
real cross-service wiring. Notably including: the cascade-delete guard (now
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
same-shaped callback with DME — going from 18 tests to 19, then to 24
in the next pass when `SubscribePolicyStatus`/`UnsubscribePolicyStatus`
went from pure no-ops to actually delivering a best-effort notification
to matching subscribers whenever a policy's enforcement status changes
(`update_policy` and `query_policy_status` — see OPEN_ITEMS.md
section 5). `focom` went from 13 tests to 19 in the same pass:
`subscribe_inventory_changes` went from a pure no-op to a real
`InventorySubscription` model backing `POST`/`DELETE
/inventory/subscriptions`, with `provision_resource`/
`deprovision_resource` delivering best-effort CREATE/DELETE
notifications to matching subscribers (a new `inventory_subscription`
table in `migrations/001_init.sql`, verified against a real local
Postgres 16 instance). `sme` went from 18 tests to 22 in the next
pass: `notify_service_change` — real event-type-filtering and
best-effort-delivery logic that had sat uncalled since it was
written — is now actually fired from `register_service`
(`SERVICE_API_AVAILABLE`/`SERVICE_API_UPDATE`) and
`deregister_service` (`SERVICE_API_UNAVAILABLE`); wiring it in also
surfaced that its own claimed authz gate (the same one
`discover_services` uses) was never enforced, now fixed. `dme` went
from 15 tests to 23 in the next pass: added `GET /data-jobs/{id}`,
`GET /data-jobs/{id}/status`, and `GET /offers/{id}` (none existed
before), and fixed `discover_dme_types`' `data_category` query param,
which was declared but silently never applied — it now filters
against `namespace`, the closest concept `DMEType` has to a category.
`a1-related` went from 24 tests to 29 in the next pass: added
`GET /policies` (filterable by
`policy_type_id`/`near_rt_ric_id`/`creator_id`), the only real gap
left against the module's mapping-store role now that
`GET /policies/{id}` existed but the list/filter view never did.
`focom` went from 19 tests to 34 in the next pass — the biggest single
jump yet: real `ResourceType`/`ResourcePool`/`Resource`/
`DeploymentManager` tables (`Resource` carries a `parentId` for the
reference's parent/child shape; no real hardware telemetry populates
it, same elision as elsewhere), lazily seeded with Phase 1's single
degenerate topology, plus the drill-down endpoints the reference
exposes as distinct operations (`/resourceTypes`, `/resourcePools`,
`/resourcePools/{id}/resources`, `/deploymentManagers`, all with
`/{id}` variants) that FOCOM previously collapsed into one hardcoded
`/inventory` route. `provision_resource`/`deprovision_resource` now
persist/remove real `Resource` rows instead of a stub UUID.
`ai-ml-workflow` went from 18 tests to 20 in the next pass: added
`GET /models/{id}` (404 on an unknown id) — model CRUD's other gaps
(update, delete/deregister) are a distinct, larger completeness item,
not part of this fix. `ran-analytics` went from 9 tests to 17 in the
final pass of this theme: added `GET /producers` and
`GET /subscriptions` (both filterable), where the reference itself
only ever defines the routes as no-op stubs — this build's versions
actually read real, persisted rows. That closes every module's
missing GET-by-id/list/query endpoint gap found by the O-RAN-SC audit.
`ran-nf-oam` went from 21 tests to 25 in the next pass: `Alarm` gained
the standard 3GPP TS 28.532 FaultMnS fault fields (`probableCause`,
`specificProblem`, `rootCauseIndicator`, `correlatedNotifications` —
a real `UUID[]` of related-alarm refs alongside the existing
`correlationGroup` grouping string, `proposedRepairActions`), wired
through `ingest_alarm` and `GET /alarms`, verified against a real
local Postgres 16 instance. `ran-nf-oam` went from 25 tests to 28 in
the very next pass: added `PATCH /alarms/{id}/clear`, closing the
missing alarm-cleared lifecycle by setting `severity` to the
already-valid `'cleared'` value (matching the reference's own
`perceivedSeverity=CLEARED` shape) rather than adding a redundant
parallel state field, plus `clearedAt`/`clearUserId` metadata.
`onboarding` went from 18 tests to 26 in the next pass: added real
`PRIMING`/`PRIMED`/`DEPRIMING` package states and `POST
/packages/{id}/prime`/`POST /packages/{id}/deprime`, closing the
missing package-level priming stage — `deprime` is genuinely blocked
by an active usage registration and `DELETE` has no edge from
`PRIMED` at all, matching the reference's own guards. `rapp-mgmt`'s
`CreateInstance` still gates on `AVAILABLE`, not `PRIMED` — an
already-confirmed design decision (D-SEC-RAPP-1), deliberately not
overridden here (see OPEN_ITEMS.md section 5 for the full scoping
note). `ai-ml-workflow` went from 20 tests to 26 in the next pass: added
real `POST /models/{id}/artifact` (upload) and
`GET /models/{id}/artifact/{version}` (download), closing the missing
model artifact upload/download and versioning gap — `artifactVersion`
is a real auto-incrementing counter per model, distinct from
`modelVersion`, matching the reference's own `UploadModel`/
`DownloadModel` shape. Real S3-backed storage stays a deliberate
elision (as documented elsewhere in this build); the actual uploaded
bytes are stored in a new `ModelArtifact` table instead, so upload and
download genuinely round-trip, and `artifact_location` — previously a
field nothing in `main.py` ever read or wrote — is now stamped on
every upload. `focom` went from 34 tests to 37 in the next pass: added
real `GET /topology`, closing the missing TEIV topology export —
FOCOM's own `ResourceType`/`ResourcePool`/`DeploymentManager`/
`Resource` rows now export as typed entities/relationships in the
reference's own wire shape, relationships built only from this
schema's real foreign keys (resource→type, resource→pool,
resource→parent), no invented ones. A CloudEvent/Kafka producer is
deliberately not built — this build has no message broker anywhere,
and the reference's own export is push-based, not a pull endpoint at
all; `/topology` is the honest pull-based substitute (see
OPEN_ITEMS.md section 5 for the full scoping note). `nfo` went from 9
tests to 23 in the next pass: added the reference's real 7-state
deployment lifecycle
(INITIAL/INSTANTIATING/RUNNING/UPDATING/TERMINATING/ABNORMAL/DELETING,
matching `o2dms/domain/states.py`'s own Initial/Installing/Installed/
Updating/Uninstalling/Abnormal/Deleting one for one), the reference's
own duplication/dependency guards on Instantiate (a deployment can no
longer silently double-book a name or a descriptor, and a nonexistent
descriptorId is rejected up front), a real resource-linkage object
(`NFOCloudResource`, the reference's `NfOCloudVResource`), and real
Heal/Scale state transitions in place of pure stubs. Terminate mirrors
the reference's own state dispatch exactly, including its defensive
catch-all for a double-terminate race. `dme` went from 23 tests to 25
in the next pass: `typeStatus` now genuinely calls the registered
`producerHealthCallbackUrl` (ICS's own
`ConsumerController.typeStatus`/`ProducerSupervision` health signal)
instead of trusting whether a `DataJob` row happened to be `ACTIVE` —
a dead producer with an active job no longer reports `ENABLED`.
Computed live at read time rather than via a periodic background poll,
since no scheduler exists anywhere in this build (elided, same as the
real PM file-collection pipeline elsewhere). `dme` went from 25 tests
to 31 in the next pass: added a real `jobCallbackUrl` field to
`DMEType` registration (ICS's own `InfoProducer.jobCallbackUrl`,
distinct from the health-supervision URL), and `create_data_job`/
`terminate_data_job` now genuinely POST/DELETE to it (ICS's own
`ProducerCallbacks.startInfoJob`/`stopInfoJob`), best-effort. This
also closed the producer-side half of the same gap: `ran-nf-oam`
(28 -> 29 tests) and `a1-related` (29 -> 30 tests) now both answer
`/dme-jobs` — the URL they themselves register with DME — the same
dangling-callback bug class already fixed for `/health`.
`ai-ml-workflow` went from 26 tests to 29 in the next pass: added a
real `UniqueConstraint` on `AIMLModel`'s `(model_type, version)` (the
reference's own `ModelID` composite primary key on
`(modelName, modelVersion)`), so `register_model` now 409s
(`MODEL_ALREADY_REGISTERED`) on a duplicate instead of silently
creating a second, indistinguishable row. `dme` went from 31 tests to
35 in the next pass: added `GET /production-capabilities/{producer_id}/status`,
reusing the same live health-check signal `typeStatus` already uses
(ICS's own `ProducerController.getInfoProducerStatus`). `dme` went
from 35 tests to 36 in the next pass: added a real `ON DELETE CASCADE`
to `data_job`/`data_offer`'s `dme_type_id` FKs (matching
`dme_delivery_schema`'s own already-cascading one — deregistering a
producer with an existing job/offer against one of its types
previously either silently orphaned the rows under SQLite or crashed
with an unhandled `IntegrityError` on real Postgres), plus explicit
application-level cleanup in `deregister_producer` itself.
`mock-near-rt-ric` went from 10 tests to 16 in the next pass: added a
real content-fingerprint check to `create_policy`/`update_policy`
(ADOPT from the real near-rt-ric-simulator's own `calcFingerprint`/
`policy_fingerprint`), scoped per policy type — a second, byte-
identical `policyObject` under the same type is now genuinely
`REJECTED`. `ai-ml-workflow` went from 29 tests to 35 in the next
pass, closing model CRUD's last two gaps: `PUT /models/{id}`
(`UpdateModel`) 404s on an unknown id and 400s
(`MODEL_IDENTITY_IMMUTABLE`) on a `modelType`/`version` mismatch
against the existing record — identity stays immutable, matching
`register_model`'s own uniqueness constraint — updating only the
metadata around it. `DELETE /models/{id}` (`DeleteModel`) surfaced the
exact same unchecked-FK shape already found and fixed for DME's
`deregister_producer`: none of `aiml_model`'s five dependent FKs had
any cascade behavior, so deleting a model with dependent rows would
orphan them under SQLite or crash with an unhandled `IntegrityError`
on real Postgres. Fixed with `ON DELETE CASCADE` on every FK (the
reference's own `DeleteModel` explicitly cleans up its one dependent
child table first, in a transaction — the same defense-in-depth shape,
not invented) plus explicit application-level cleanup, verified
against a real local Postgres 16 instance including the transitive
`performance_report -> mlmf_subscription -> aiml_model` hop.
`ran-analytics` went from 17 tests to 22 in the next pass:
`publish_report`'s subscriber-notification loop (`for sub in subs:
pass`) — a real gap, though not a regression behind
`aiml-fw-apm-monitoring-server`'s own equally-empty `Subscribe` stub —
now actually delivers. Added an optional `notificationDestination` to
`SubscribeAnalytics`/`MDASubscription` (same shape as A1 Related's
`notification_destination`/Policy Mgmt's `notificationCallbackUri`),
and a published report is now best-effort POSTed to every matching
subscriber that registered one; a purely poll-based subscriber (no
destination registered) is left alone rather than having a delivery
target guessed for it. `dme` went from 36 tests to 41 in the next
pass: added `PUT /data-jobs/{id}` (ICS's own `PutIndividualInfoJob`),
closing the missing update-in-place gap. This build's `dataJobId` is
server-generated (unlike ICS's caller-supplied `jobId`), so the
endpoint only ever updates an existing job; `dmeTypeId`/`consumerId`/
`dataDeliveryMode` stay immutable, matching ICS's own "cannot modify
job type" rejection, `dataDeliveryMethod` is re-validated against the
same `DataOffer` cross-check `create_data_job` already applies, and a
successful update re-pushes the job to the producer, matching ICS's
own PUT behavior of re-running `startInfoSubscriptionJob` on every
call, new or updated. `dme` went from 41 tests to 50 in the next pass:
added `POST`/`GET`/`DELETE /type-subscriptions` and
`GET /type-subscriptions/{id}`, closing the missing type-subscription
mechanism (ICS's own `/info-type-subscription`). `register_dme_type`
and `deregister_producer` now best-effort notify every subscriber
whenever any type is registered or removed (ICS's own
`ConsumerCallbacks.notifyTypeRegistered`/`notifyTypeRemoved`),
unfiltered — matching the reference's own lack of per-type scoping on
this particular subscription (unlike, say, A1 Related's policy-status
subscriptions, which do filter). `dme` went from 50 tests to 55 in the
next pass: closed the missing job-definition schema validation gap by
adopting the `jsonschema` library (a new dependency, matching ICS's
own real JSON Schema validation via `org.everit.json.schema`) in
`create_data_job`/`update_data_job` — a `productionJobDefinition` that
doesn't validate against its `DmeType`'s registered
`dataProductionSchema` is now rejected instead of accepted as an
arbitrary dict. `onboarding` went from 26 tests to 30 in the next
pass: closed its much-thinner package validation gap, partially —
added the reference's `.csar` filename convention check and its
required `Definitions/acm_composition.json` file check, plus
duplicate-package detection keyed on this build's own already-computed
`integrity_hash` (adapted from the reference's ASD-descriptor-id
uniqueness check, since this build has no ASD descriptor concept to
check against). All three route the package to `FAILED`, matching
`OnboardPackage`'s existing async-contract shape rather than a
synchronous HTTP rejection. `sme` went from 22 tests to 24 in the next
pass: closed its type-only event subscription filtering gap,
partially — added `apiIds` to `SubscribeEvents`/
`ServiceEventSubscription` (the reference's own
`CAPIFEventFilter.apiIds`), so a subscription scoped to specific
`apiId`s is no longer notified about other services' events.
`apiInvokerId`/`aefId` filters stay unimplemented for a concrete
reason — no invoker-onboarding events exist in this build to filter
on, and no `aefProfiles` concept exists on `ServiceProfile` — not
dropped silently. `sme` went from 24 tests to 29 in the next pass:
closed its flattened `ServiceProfile` gap by adding real
`aefProfiles`/`apiSuppFeats`/`shareableInfo` fields (the reference's
own `ServiceAPIDescription` fields), stored as JSON and read back
wholesale rather than as normalized child tables, and closed
`discover_services`' own filtering thinness alongside it, partially —
`aefId`/`protocol`/`dataFormat`/`commType` are now real filters
walking the new field; `category` stays unfiltered since this build
has no category concept on `ServiceProfile` at all. `ai-ml-workflow`
went from 35 tests to 38 in the next pass: closed its thin
registration metadata gap by adding `description`/`author`/`owner`/
`inputDataType`/`outputDataType`/`targetEnvironments` to
`RegisterModel`/`UpdateModel` (the reference's own
`ModelRelatedInformation`/`ModelInformation`/`Metadata`/
`TargetEnvironment` fields) — required there, kept optional here since
this build's own `RegisterModel` was already permissive before this
pass. `ai-ml-workflow` went from 38 tests to 42 in the next pass:
closed its thin `TrainingJob` gap, partially — added `runId`/
`trainingDataset`/`validationDataset`/`consumerRappId`/
`producerRappId` to `RequestTraining`, plus a real metrics-writeback
route pair matching the reference's own `update-model-metrics`/
`get-model-metrics` routes. The reference's real two-axis step×status
tracking stays deliberately unadopted — replacing this build's
existing flat `status` field with a step state machine would be a
bigger rework of already-shipped behavior, not a purely additive
field. `ai-ml-workflow` went from 42 tests to 49 in the next pass:
closed its missing feature-group/feature-store concept, partially — added
a real `FeatureGroup` entity with `POST`/`GET /feature-groups` (the
reference's own `CreateFeatureGroup`/`GetFeatureGroup`, its only two
routes). Real Cassandra-backed feature storage and the reference's
`enableDme`-triggered real DME job creation stay deliberately
unadopted, the same no-real-southbound-compute elision as elsewhere in
this build — `enableDme` is stored and returned faithfully, just not
acted on. `a1-related` went from 30 tests to 32 in the next pass:
closed its missing policy-type detail retrieval gap, partially — added
`GET /policy-types/{id}` (the reference's own `GetPolicyTypeDefinition`),
404 on an unknown type, else a real `PolicyTypeObject`. `policySchema`
is an honest empty placeholder rather than a fabricated A1TD schema
this build was never given; the reference's separate RIC repository
(`GET /rics`) stays out of scope, since this build models no
near-RT-RIC entity or inventory beyond the single A1 mock. `rapp-mgmt`
went from 12 tests to 17 in the next pass: closed its missing
standalone delete-after-undeploy gap — adopted the reference's own
`undeployRappInstance`/`deleteRappInstance` split (DEPLOYED ->
UNDEPLOYING -> UNDEPLOYED, delete only legal once UNDEPLOYED).
`TERMINATE` now only tears the workload down and lands in a terminal
`UNDEPLOYED` state (replacing the old `TERMINATING` name) with the
instance row still present; a new `DELETE /instances/{id}` removes it,
409'ing otherwise. Also found and fixed while wiring this in: neither
`rapp_fault_report` nor `rapp_performance_report` had an `ON DELETE
CASCADE` on their `instance_id` FK — the same bug class already found
in DME's `deregister_producer`/AI-ML Workflow's `deregister_model` —
fixed with both a DB-level cascade and explicit application cleanup,
verified against a real local Postgres 16 instance.
`CreateInstance`'s already-shipped immediate-deploy behavior is a
separate, already-cited design decision and stays untouched.
`a1-related` went from 32 tests to 43 in the next pass: closed its
missing service registration/supervision gap — this module's own
reference clone has no real Java source to ground against, only its
OpenAPI spec (`pms-api-v3.json`), but that spec's `ServiceRegistrationInfo`/
`ServiceStatus`/`/services*` routes are themselves real, authoritative
wire-contract content. Added `PUT`/`GET /services`, `DELETE
/services/{id}`, and `PUT /services/{id}/keepalive`; unregistering a
service — or the lazy keepalive-timeout sweep, since no scheduler
exists anywhere in this build — genuinely deletes its A1 policies via
the same real southbound call `delete_policy` itself uses. The
reference's own `RICStatus` callback stays out of scope, since this
build has no RIC-availability concept independent of the single A1
mock. `rapp-mgmt` went from 17 tests to 19 in the next pass: closed its
missing resource-provenance detail gap, partially — added
`GET /instances/{id}`, which previously didn't exist at all (only the
list route and single-field sub-resources did). Genuinely exposes the
real NFO `workloadRef` and caller-supplied `configuration`; the
reference's own nested ACM/SME/DME resource records stay out of scope,
since they're the caller-supplied deploy descriptor `CreateInstance`
never accepts in the first place. `sme` went from 29 tests to 37 in the
next pass: closed `register_service`'s missing `apf_id` check for
real, on a second look — added a minimal Provider (APF) enrolment
registry scoped to this build's own flattened `apf_id` identity, not
the reference's full provider-domain/APF-AEF-AMF hierarchy.
`register_service`/`query_own_services` now both enforce the
reference's own real `IsPublishingFunctionRegistered` gate. This
changes an already-shipped route's contract: RAN Analytics' only
cross-module call into SME now enrols before publishing, verified
against the real cross-service integration suite. With §5 fully
closed, the next pass moved to §2: closed "no persisted OpenAPI spec
files anywhere" — `docs/openapi/<module>.json` for all fourteen modules
plus the mock, generated from each app's own real `app.openapi()`
output, with a new CI-enforced drift check
(`tests_integration/test_openapi_specs.py`) that fails if a committed
spec stops matching the live schema. Writing that test caught a real
bug: `r1-termination`'s catch-all proxy route (one route serving five
HTTP methods) got a non-deterministic `operationId` from FastAPI's own
`generate_unique_id()`, which picks the first element of a plain `set`
— hash-seed-dependent, so the "committed" schema would never have
stayed stable. Fixed with an explicit `operation_id`. Integration
suite: 10 tests to 12. Per the user's explicit direction to revisit
previously-declared Phase-1 boundaries, the next pass closed "no real
OAuth2/token enforcement at R1 Termination," partially: a real, minimal
API Invoker registry, a real `POST /oauth2/token` (client_credentials,
genuinely checked secret), and `POST /oauth2/introspect` (RFC 7662 —
the honest substitute for the reference's own externally-signed-JWT
validation, which needs a Keycloak instance this build doesn't run). R1
Termination now genuinely enforces this on every proxied request,
failing closed if SME is unreachable. A GitHub Advanced Security review
on that PR caught a real finding before merge: the onboarding secret
and issued tokens were both stored in cleartext — fixed with a salted
`scrypt` hash for the former and a SHA-256 hash of the token for the
latter, neither value ever stored raw. `sme` went from 37 tests to 47;
`r1-termination` from 10 to 15. Continuing to revisit previously-declared
Phase-1 boundaries, the next pass closed the O1 Adaptor half of "no real
southbound integrations beyond the A1 mock": a new `mock-o1-adaptor`
module (mirroring `mock-near-rt-ric`'s own minimal scope) answers RAN NF
OAM's real RFC 6241 `<edit-config>` RPC for real. Writing the two new
cross-service integration tests that prove this surfaced a real,
separate bug in the harness itself: `tests_integration/mesh.py`'s
`dispatch()` only ever forwarded a JSON body, silently dropping any raw
`content=` kwarg (`netconf_client.py`'s XML POST was the first non-JSON
caller this harness ever had) — fixed. `mock-o1-adaptor` is a new
module: 5 tests; the integration suite went from 12 to 14. A GitHub
Advanced Security (CodeQL) review on that PR then caught a real finding:
`mock-o1-adaptor`'s `/edit-config` parsed an attacker-reachable HTTP body
with stdlib `xml.etree.ElementTree`, vulnerable to XML internal entity
expansion (CWE-611) — fixed by switching to `defusedxml.ElementTree`
there and, for the same vulnerability class at the same protocol
boundary, in `netconf_client.py`'s reply parsing too. `mock-o1-adaptor`
went from 5 tests to 6. Continuing to revisit previously-declared
Phase-1 boundaries, the next pass closed "RAN NF OAM's MnS Registry
discovery is a heartbeat-aging stub" — partially: real MnS Registry NRM
polling stays out of scope (no such registry exists in this build), but
`write_configuration_changes`'s own gate now ages a stale `ACTIVE`
endpoint live, the moment a write is attempted against it, rather than
depending on something having already called the separate
`POST /o1-adaptor-endpoints/discover` sweep first — the same "no
scheduler exists anywhere in this build" pattern as DME's producer
health and A1 Related's service supervision. Writing real route-level
tests for this (previously zero) surfaced the third occurrence of the
naive-vs-aware `DateTime(timezone=True)` SQLite portability gap — fixed
with the same `as_utc` helper A1 Related and SME already use.
`ran-nf-oam` went from 29 tests to 34. Continuing to revisit "other
items like that", the next pass closed "FOCOM's hardcoded single-cluster
stub" — partially: the single-cluster topology itself stays Phase 1's
declared scope, but `GET /inventory` (the one route NFO's real
Instantiate call actually depends on) was still a hardcoded literal
that never touched the real `ResourceType`/`ResourcePool`/
`DeploymentManager` schema a §5 pass had already given every drill-down
route — now sourced from the same seeded row every other route reads.
`focom` went from 37 tests to 38. Continuing "other items like that",
the full `docker-compose` stack running end-to-end stays genuinely out
of scope (no Docker daemon in this build's sandbox or its own CI
runners), but the narrower piece — validating the compose file's YAML
structure — used to be purely manual. A new `docker-compose-config` CI
job now runs `docker compose config --quiet` automatically on every
push/PR (no daemon needed for that, just parsing and rendering), the
same drift-check philosophy already used for the OpenAPI specs.
Continuing "other items like that", the next pass closed the
migration-Postgres CI job's own documented root cause for the two
`rapp_instance` schema bugs found earlier (table *count* only, never
columns): `scripts/check_migration_matches_models.py` now loads every
module's ORM models and compares column presence + nullability against
the live Postgres schema once the real migration is applied, wired into
that CI job. Verified by reproducing both original bugs directly against
a real local Postgres 16 instance and confirming the script catches
each with a precise error, then restoring a clean migration and
confirming it passes. Two further passes then produced `SPEC_AUDIT.md`
(a deep comparison against the formal specs in `specs/`, distinct from
this file's own source-code audits) and a delta pass against the
O-RAN-SC repos, both summarized in `OPEN_ITEMS.md`. Per explicit
direction, the pass after that built a real pilot-demo artifact instead
of chasing more spec gaps: `smo/samples/hello-world-rapp.csar`, adapted
from the real O-RAN-SC reference's own sample package
(`nonrtric-plt-rappmanager/sample-rapp-generator/rapp-all`), and
`smo/DEMO_RUNBOOK.md`, a real command-by-command walkthrough of the
full onboard → deploy → bootstrap → operate → retire lifecycle against
a live `docker compose up`. Building the sample package this way (not a
synthetic fixture) surfaced a real bug neither this build's own
validator nor its own test fixture had ever caught, because both had
independently guessed the same wrong path: `_validate_package` required
`Definitions/acm_composition.json`, which doesn't exist anywhere in the
real reference — the actual required path
(`RappCsarPathProvider.ACM_COMPOSITION_JSON_LOCATION`) is
`Files/Acm/definition/compositions.json`. Fixed, and now proven by two
new permanent integration tests that run the real CSAR and the full
runbook sequence end to end. `tests_integration` went from 14 tests to 16.
Started closing `SPEC_AUDIT.md`'s small/closeable gaps directly, per
explicit direction. First: RAN NF OAM's `Alarm` model was missing
`alarmType` (a real Postgres `CHECK` constraint, verified to actually
reject an invalid value) and `ackUserId`/`alarmChangedTime` (neither
previously recorded — `changed_at` now updates on both the ack and
clear routes, the two places this build mutates an existing alarm).
Verified against a real local Postgres 16 instance. `ran-nf-oam` went
from 34 tests to 35. Next: item 3, `WriteConfigSubChange` had no
operation-type field at all — every write was implicitly a merge, with
no create/delete/replace equivalent. Grounded in RFC 6241 section 7.2's
real edit-config `operation` attribute (the actually-implemented
southbound protocol) rather than `TS28532_ProvMnS.yaml`'s HTTP-verb
framing; defaults to `"merge"` everywhere (real `CHECK` constraint for
the 5 RFC values, verified to reject an invalid one) so every existing
caller is unaffected. Fixing this surfaced a real bug in
`mock-o1-adaptor`: its `edit_config` handler rejected any empty
`attribute_changes` payload unconditionally, which would have wrongly
rejected a legitimate delete (a delete carries none by design) — now
only rejected for the other operations. `ran-nf-oam` went from 35 tests
to 37, `mock-o1-adaptor` from 6 to 8. Next: item 4, `PMSubscription` was
missing `granularityPeriod` (`TS28550_PerfMeasJobCtrlMnS.yaml`'s
sampling-interval field) — `subscribe_pm`'s own docstring already
confirms the rest of that job-control shape (schedule/priority/
reportingPeriod) is a deliberate scope cut, but this one field is
needed regardless of wrapper shape and was fully absent. Nullable,
optional request param, no existing caller's shape changes.
`ran-nf-oam` went from 37 tests to 39. Next: items 5-6, Policy Mgmt's
`intentHandlingScope` was untyped JSON never set or read, and `DELETE
/intents/{id}` didn't exist at all. `TS28312_IntentNrm.yaml`'s
`IntentHandlingScope` is a closed 2-value enum (RAN/CN) — now a
Pydantic `Literal` on both `RegisterIntentHandlingFunction` (a real 422
on an invalid value) and, as a new optional field, `CreateIntent`,
where it pre-filters the existing `intentType` capability match (an
RMIH whose declared scope doesn't cover the request is skipped; one
with no declared scope still matches anything, so no existing caller's
behavior changes). `DELETE /intents/{id}` closes the gap where an RMIO
could only deactivate an Intent, never retract it — cascades to
`IntentReport` the same way this build's other owned-child deletes
already do, verified for real against a local Postgres 16 instance.
`policy-mgmt` went from 10 tests to 15. Next: item 7,
`ORAN.O2ims.Inventory.yaml` requires several fields none of FOCOM's
`ResourceType`/`DeploymentManager`/`Resource` models had at all —
dictionary refs and closed enums on the first, capacity/capability
arrays on the second, `globalAssetId`/`tags`/`groups` on the third.
All added as nullable columns (no route registers a `ResourceType` or
`DeploymentManager` in enough detail to set most of them), with real
`CHECK` constraints for `resourceKind`/`resourceClass`'s closed enums.
`Resource`'s three fields are the one exception actually wired to a
caller: `provision_resource`'s already-untyped spec dict now reads
`globalAssetId`/`tags`/`groups` from it too. `focom` went from 38
tests to 42.

Last: item 8, `InventorySubscription` used this build's own invented
`callbackUri` instead of the real spec's `callback`, and had no
`consumerSubscriptionId` at all. Renamed outright (no cross-module
caller ever used the old name); `consumerSubscriptionId` is now
accepted, persisted, returned on the subscribe response, and — per the
spec's own description of what it's for — passed through on every
inventory-change notification, not just stored inertly. `focom` went
from 42 tests to 45.

This closes `SPEC_AUDIT.md`'s entire "What's genuinely closeable now"
list — every small/scoped, non-breaking spec gap that pass identified
is now closed. What remains there is moderate/breaking-shape or
large/structural, each already flagged as needing a deliberate
follow-up pass or a confirmed Phase-1 scope cut.

Next, per explicit direction: triaged `SPEC_AUDIT.md`'s large/structural
items (confirmed Phase-1 scope cuts) against what `DEMO_RUNBOOK.md`'s
pilot demo actually calls, rather than a general risk assessment. Five
of six are out of scope — RAN NF OAM and Policy Mgmt are never called
in the runbook, and FOCOM appears only as one read-only `GET /inventory`
call. The sixth, SME's missing "Trusted Invokers" security subsystem,
is the one gap the demo walks through live (the invoker-registration
step has the client self-assert its own `apiInvokerId`/
`onboardingSecret`) — not worth building before the demo, but a
one-line disclosure was added right at that step. Doc-only, no test
count change.

Then the first moderate/breaking-shape item: Policy Mgmt's
Intent-to-RMIH matching used an invented top-level `intentType` field
with no shared vocabulary with `TS28312_IntentNrm.yaml`, and conflated
it with `intentMgmtPurpose` (a semantically unrelated real spec field
— a workflow-procedure enum). Matching now reads the spec's real
field, each expectation's own `expectationObject.objectType`, straight
out of the already-accepted `expectations` list, matched against each
RMIH's declared `supportedExpectationObjectType` capability;
`intentMgmtPurpose` is now a real, independently-settable field with
the spec's own default and a real Postgres `CHECK` constraint for its
5 real values. A repo-wide grep confirmed no other module ever called
these routes with the old field names, so the blast radius stayed
entirely inside `policy-mgmt/`. `policy-mgmt` went from 15 tests to 18.

Then the second moderate item: FOCOM's `GET /inventory` returned an ad
hoc `{clusterId, resourcePools:[...]}` shape matching no real O2IMS
schema. Reshaped toward `OCloud`, the spec's own real aggregate root —
`oCloudId`/`name`/`description`/`resourceTypes`/`deploymentManagers`
now come from FOCOM's own real, already-seeded topology;
`locations`/`oCloudSites` (required in the real spec) are honestly
empty rather than fabricated, since FOCOM has no `OCloudSite`/
`Location` concept at all. `resource_type` now genuinely filters
`resourceTypes` against a known `ResourceType` instead of being an
unvalidated echo. First checked NFO's real `Instantiate` caller
precisely to scope the blast radius: it reads exactly one key
(`clusterId` → now `oCloudId`), with the same graceful fallback kept;
NFO's own, unrelated `clusterId` response field for its own callers
was left untouched. No migration change. `focom` went from 45 tests
to 48.

Last, per explicit user direction to implement rather than just
document: SME's invoker onboarding had the trust direction backwards.
The real CAPIF core's onboarding is public-key-based — the client
submits `apiInvokerPublicKey`; the server generates and returns both
`apiInvokerId` and `onboardingSecret`. `InvokerRegistrationRequest`
now takes only the public key; `register_invoker` mints a real
server-side id and a real random secret (still hashed at rest) and
returns both, matching the real endpoint's own "always mint a new
invoker" behavior rather than updating one in place.
`InvokerRegistration` gained a `public_key` column. A repo-wide grep
confirmed only SME's own routes/tests and `DEMO_RUNBOOK.md`'s own
script ever referenced the old client-supplied fields — both updated;
the runbook's own earlier "worth calling out live" disclosure is now
updated to reflect the fix, while the real CAPIF core's separate
"Trusted Invokers" registry (a genuine additional subsystem) stays
named as still open. This closes every item on `SPEC_AUDIT.md`'s
"Moderate/breaking-shape items" list. `sme` went from 47 tests to 49.

**Pilot demo, Phase B: RAN NF OAM closed-loop.** Per the large/structural
triage's own conclusion, the next pass exercises real, already-tested
capabilities the demo never actually called rather than building
confirmed-out-of-scope items. Building `DEMO_RUNBOOK.md`'s new RAN NF OAM
section surfaced a genuine gap: the `EndpointHealth` FSM and the
CM-write/alarm routes were real and fully unit-tested, but no route
anywhere ever created an `O1AdaptorEndpoint`/`ManagedEntity` row in the
first place — `docker-compose.yml`'s own comment on `mock-o1-adaptor`
names this exact gap, and the real LLD design intent (each ME's O1
Adaptor self-registers into the MnS Registry NRM) was never implemented
as a route. Closed with a new `POST /o1-adaptor-endpoints` route that
creates both rows starting at the FSM's real `DISCOVERED` state (a first
heartbeat is still required to reach `ACTIVE`). `DEMO_RUNBOOK.md` gained
a full closed-loop walkthrough — register, heartbeat, dispatch a real CM
write, confirm it against the mock O1 Adaptor's own applied-config
endpoint, then ingest/ack/clear an alarm — and
`tests_integration/test_demo_runbook.py` proves the same sequence end to
end. No schema change. `ran-nf-oam` went from 39 tests to 42. Next: Phase
C (FOCOM resource management) and Phase D (Policy Mgmt intent
automation), per explicit user direction to build the large/structural
triage into the pilot demo's use case one phase at a time.

**Pilot demo, Phase C: FOCOM resource management.** Unlike Phase B, no
code gap here — FOCOM's provision/deprovision/subscribe routes and their
notification delivery were already real and already fully unit-tested;
`DEMO_RUNBOOK.md` just never called them. New section: subscribe to
inventory changes filtered by resource type, provision a matching
resource (confirmed via the real pool drill-down), deprovision it — each
mutation fires a real outbound notification. The live walkthrough's
callback points at a placeholder host with no listener in this compose
stack, matching the same honesty pattern already used for the DME
producer callbacks earlier in the runbook, and says so explicitly.
`tests_integration/test_demo_runbook.py` gained a step that actually
proves the notification fires by intercepting the exact `httpx.post`
call FOCOM's own code makes (same technique already used for the sample
CSAR fetch), asserting the real CREATE/DELETE payloads round-trip. No
code, schema, or OpenAPI-spec change. Next: Phase D (Policy Mgmt intent
automation), the last of the three.

**Pilot demo, Phase D: Policy Mgmt intent automation.** Last of the
three passes; like Phase C, no code gap — the capability-based
Intent-to-RMIH match-and-dispatch was already real and unit-tested,
`DEMO_RUNBOOK.md` just never called it. New section: register an RMIH
under the `so-smos` framework-internal identity (D-SEC-POLICY-1 rejects
an rApp's UUID `rmihId` outright) declaring `supportedExpectationObjectType:
RAN_SUBNETWORK`, create an Intent with a matching
`expectationObject.objectType`, confirm the real dispatch notification
fires to the RMIH's callback, then retract symmetrically (delete the
Intent, deregister the RMIH) — the same provision/deprovision-pair
shape as Phase C. The live callback has no real route behind it in this
build, stated explicitly, same honesty pattern as Phase C.
`tests_integration/test_demo_runbook.py` gained a step proving the real
dispatch fires by intercepting the exact `httpx.post` call
`create_intent` makes. No code, schema, or OpenAPI-spec change. This
closes out all three demo-expansion phases confirmed with the user for
this pilot demo pass.

**Demo depth: RAN NF OAM's real `PARTIAL_SUCCESS` failure path.** With
`OPEN_ITEMS.md` §5 fully closed, the next pass deepens an existing demo
section rather than adding a new one. `WriteConfigurationChanges`
genuinely decomposes a multi-ME batch into independent per-ME
sub-changes and aggregates them — a batch mixing a healthy, registered
ME with one that was never registered settles as `PARTIAL_SUCCESS`, not
all-or-nothing (`ENDPOINT_UNREACHABLE` on the missing one, the same
real per-ME dispatch gate the closed-loop walkthrough already exercises).
Extended `DEMO_RUNBOOK.md`'s "RAN NF OAM closed-loop" section with this
walkthrough and added a matching integration-test step. No code,
schema, or OpenAPI-spec change.

**Demo depth: a whole new module, A1 Policy Management.** A1 Related
and `mock-near-rt-ric` were entirely absent from the demo despite
having real, already-tested logic. New `DEMO_RUNBOOK.md` section:
register a service, list real policy types, create an A1 Policy — a
genuine round trip through `A1TerminationClient` to the mock Near-RT
RIC, settling `ENFORCED` — subscribe to its status, then create a
byte-identical second policy and observe the mock's own real
content-fingerprint check reject it (`REJECTED`). Update the first
policy to an empty object to trigger a genuine `ENFORCED -> REJECTED`
transition, firing a real status-change notification (proven the same
way as the FOCOM/Policy Mgmt demo notifications, by intercepting the
exact `httpx.post` call). Retract both policies and deregister the
service. `tests_integration/test_demo_runbook.py` gained a matching
step. No code, schema, or OpenAPI-spec change.

**Demo depth: Policy Mgmt's real `intentHandlingScope` negative case.**
The existing Policy Mgmt demo section only showed the positive match;
`_matching_rmihs`' own real scope pre-filter was already implemented
and unit-tested but never demonstrated. Extended the section: register
a second RMIH with the same capability but a different (`CN`-only)
scope, create a second `RAN`-scoped Intent, and show only the
`RAN`-scoped RMIH is notified — the scope-mismatched one is correctly
skipped despite its matching capability. `tests_integration/
test_demo_runbook.py` gained a matching step asserting both outcomes.
No code, schema, or OpenAPI-spec change.

**Demo depth: Onboarding's real duplicate-content validation failure.**
Last of four confidently-in-scope demo-depth passes. Every prior demo
onboards exactly one package and only shows the success path;
`_validate_package`'s duplicate-content check (matching
`integrity_hash`) was already implemented and unit-tested but never
demonstrated. Extended the onboarding section: onboard the exact same
CSAR a second time — `OnboardPackage` still returns `202` synchronously
(this endpoint never rejects synchronously), and `onboarding-status`
shows the second package genuinely `FAILED` while the first stays
`AVAILABLE`, untouched. `tests_integration/test_demo_runbook.py` gained
a matching step. No code, schema, or OpenAPI-spec change.

This closes the confidently-in-scope portion of the post-§5 demo-gap
list. FOCOM FCAPS depth and SME's Trusted Invokers registry are each
already-confirmed out of scope; bringing AI/ML Workflow, RAN Analytics,
SO SMOS, or SA SMOS into the demo is a genuine scope question, not a
mechanical pickup — left for explicit direction.

**Demo depth: FOCOM FCAPS, reversing the earlier "out of scope"
triage per explicit user direction.** FOCOM's alarm/performance routes
(a distinct domain from RAN NF OAM's RAN-function alarms) were already
real, Postgres-backed, and unit-tested but never in the demo. Extended
the FOCOM section: real infrastructure alarm ingest and query, then a
performance query — genuinely filterable but empty in a fresh stack,
disclosed honestly (no `POST /performance` route exists in this build
at all; real metrics would arrive via O2ims's own collection
mechanism, not a stub). `tests_integration/test_demo_runbook.py`
gained a matching step. No code, schema, or OpenAPI-spec change.

**SME's real "Trusted Invokers" security-context registry**
(SPEC_AUDIT.md SME item 2), built for real per explicit user direction
reversing the earlier "disproportionate for a scoped fix" assessment.
Added the real `PUT`/`GET`/`DELETE /trusted-invokers/{apiInvokerId}`
plus revocation, matching `capifcore/internal/securityservice/
security.go`'s own routes and validation — invoker-registration gate
on `PUT`, real body validation, `GET`'s real authenticationInfo/
authorizationInfo redaction by default, and revocation's real
per-entry removal with whole-record cleanup once empty. Confirmed by
inspection that CAPIF core's own token-issuance path never reads
`trustedInvokers` at all, so no existing SME route needed rewiring —
this is a standalone registry a real AEF would consult directly. New
`TrustedInvoker` model (JSON `security_info`, the same flattening
adaptation already used for `aefProfiles`), new migration table, a new
`DEMO_RUNBOOK.md` section reusing step 4's real invoker registration,
and a matching integration-test step. Verified against a real local
Postgres 16 instance (59 tables, up from 58). `sme` went from 49 tests
to 66.

**Demo: AI/ML Workflow.** The first of four modules never touched by
any demo phase before this pass. New section: register a model with
real metadata → request training (fires the real `TRAIN` FSM
transition) → upload a real artifact (genuinely round-trips through a
Postgres-backed row) → write real training metrics → advance the model
through its real lifecycle FSM (`TRAINING_COMPLETE` →
`VALIDATION_COMPLETE` → `CERTIFY` → `LOAD` → `ACTIVATE`, five genuine
transitions) → download the artifact back and confirm the bytes match
→ deregister. `tests_integration/test_demo_runbook.py` gained a
matching step. No code, schema, or OpenAPI-spec change.

**Demo: RAN Analytics.** The last of four modules never touched by any
demo phase. New section: register an analytics producer (the same
real two-step CAPIF dance as step 4 — SME provider enrolment, then a
second, distinct service publish) → subscribe with a real
`notificationDestination` → publish a report, firing a real
notification (proven the same way as the FOCOM/Policy Mgmt/A1 Related
demo notifications, by intercepting the exact `httpx.post` call) →
confirm the report is queryable → unsubscribe.
`tests_integration/test_demo_runbook.py` gained a matching step. No
code, schema, or OpenAPI-spec change. This completes bringing every
module the six-item sequence named into the demo.

**Demo: SO SMOS.** SO SMOS's own real dispatch table and fail-fast
execution semantics were already implemented and unit-tested, but no
demo phase had ever submitted a real multi-step order. New section:
submit a 3-step order (`INFRA` → FOCOM, `POLICY` → A1 Related with an
unrecognized policy type, `TRAINING` → AI/ML Workflow) — step 1
`COMPLETED`, step 2 `FAILED` (a real downstream rejection), step 3
`PENDING` (the real fail-fast halt, never dispatched). Confirm via
`GET /orders/{id}`, then cancel — turning the `PENDING` step
`CANCELLED` while leaving the others untouched.
`tests_integration/test_demo_runbook.py` gained a matching step. No
code, schema, or OpenAPI-spec change.

**Demo: SA SMOS.** The sixth and last of the six-item follow-up
sequence. SA SMOS's own real remedial-action dispatch (SO/SA SMOS LLD
section 2.1) was already implemented and unit-tested, but no demo phase
had ever exercised it. `RECONNECT` needs a genuine, `RUNNING`
`NFDeployment` distinct from the sample rApp's own deployment (NFO's
real duplication guard means a descriptor can only be deployed once),
so the new section creates a second `NFDeploymentDescriptor` against
the already-onboarded package, deploys it via a real SO SMOS `DEPLOY`
order, registers an `AssuranceMonitor` scoped to that order, evaluates
a real threshold breach, then exercises both real outcomes:
`RECONNECT` resolves the concrete `nfDeploymentId` via a live SO SMOS
order lookup (`_resolve_deployed_nf`) and dispatches NFO's real `Heal`
(`RESOLVED`), and `ROLLBACK` genuinely returns
`ROLLBACK_HISTORY_UNAVAILABLE` (501) — rApp Management deletes the
prior `RAppInstance` row on a successful upgrade, so no version history
survives to roll back to, a concrete, checked reason rather than a
generic refusal. Placed as the new §15, ahead of §16 (SO SMOS) rather
than after it — the reordering, and the two real bugs below it
surfaced, are the actual story here, not just new demo copy.
`tests_integration/test_demo_runbook.py` gained a matching step. No
schema or OpenAPI-spec change; two real, pre-existing app bugs (in
`onboarding/app/main.py` and `nfo/app/main.py`) and one real
test-harness bug (`shared/smo_shared/testing.py`,
`tests_integration/conftest.py`) fixed along the way — see "Real bugs"
below for all three. This closes the entire six-item follow-up
sequence: FOCOM FCAPS depth, SME Trusted Invokers, AI/ML Workflow, RAN
Analytics, SO SMOS, and SA SMOS are now all demoed.

**Demo depth: Onboarding's package priming lifecycle.** With the
six-item sequence done, and `OPEN_ITEMS.md` §5's full repo-audited
completeness backlog now confirmed fully closed (every module — SME,
DME, Onboarding+rApp Mgmt, RAN NF OAM, A1 Related, NFO+FOCOM, AI/ML
Workflow, RAN Analytics — checked directly, not one open item left),
this picks up the "more demo depth" thread instead. Onboarding's real
`AVAILABLE -> PRIMING -> PRIMED -> DEPRIMING` lifecycle (our
`AVAILABLE` playing the reference's `COMMISSIONED` role) was built in
an earlier pass but had never appeared in the demo at all. Extended
§17 (Retire): prime the sample rApp's own package → attempt to
deprime it while its instance is still deployed, a genuine refusal
(the reference's own `deprimeRapp` guard, a real query against
`PackageUsageRegistration`, not a scripted failure) → terminate the
instance (which itself calls Onboarding's real `usage/stop`) → deprime
again, now genuinely succeeding, the same guard passing for real →
delete the instance. `tests_integration/test_demo_runbook.py` gained a
matching step. No code, schema, or OpenAPI-spec change to the app
itself.

Grounding this against real Postgres surfaced a second, subtler
test-harness bug beyond the one PR #86 already fixed — see "Real bugs"
below (`tests_integration/conftest.py`'s `expire_on_commit=False` fix):
a nested cross-service commit could be silently discarded by an outer
Session's own implicit rollback on close, with no error of any kind,
purely a SQLite `StaticPool`-sharing artifact. Confirmed and fixed the
same way as before — the identical sequence already passed cleanly
against a real local Postgres instance before this test-harness fix
even landed.

**Demo depth: DME's type-subscription mechanism.** Continuing the
same "more demo depth" thread. ICS's own `/info-type-subscription`
(`InfoTypeSubscriptions`/`ConsumerCallbacks`) — a consumer notified
whenever any `DmeType` is registered or removed — was closed in an
earlier §5 pass but had never appeared in the demo. New section:
subscribe with a real `notificationDestination` → register a new DME
type, firing a real `REGISTERED` notification
(`_notify_type_subscribers`) → deregister the producer, tearing down
both this new type and step 4's own `hello-world-metrics` and firing a
matching `DEREGISTERED` notification for each → unsubscribe.
`tests_integration/test_demo_runbook.py` gained a matching step,
proving the real dispatch fires with the correct `infoTypeId`/
`jobDataSchema`/`status` payload by intercepting the exact `httpx.post`
call `_notify_type_subscribers` makes (same technique as every other
notification demo in this runbook). No code, schema, or OpenAPI-spec
change — confirmed via a full local Postgres 16 pass (59 tables, 0
mismatches) and the live-schema-match check, both green.

**Demo depth: FOCOM's TEIV topology export.** Continuing the same
"more demo depth" thread. `GET /topology` (closed in an earlier §5
pass — the Blueprint names "FOCOM's placement as a TEIV data source"
as a confirmed integration point) had never been called anywhere in
the runbook. New section: export FOCOM's real `ResourceType`/
`ResourcePool`/`DeploymentManager`/`Resource` rows in the reference's
own wire shape — genuinely non-trivial by this point, since SO SMOS's
own `INFRA` step earlier in the runbook already auto-registered a real
`gpu-l40` `ResourceType` and provisioned a `Resource` against it, never
deprovisioned. `tests_integration/test_demo_runbook.py` gained a
matching step. No schema or OpenAPI-spec change, but grounding this
against real Postgres caught a real, previously-invisible bug (see
"Real bugs" below): `provision_resource`'s own auto-registration of an
unrecognized `resourceTypeId` added the new `ResourceType` and the new
`Resource` row in the same flush, with no explicit intermediate flush
between them — a real `ForeignKeyViolation` under Postgres, every time
a genuinely new resource type is provisioned, never caught by SQLite's
own non-FK-enforcing test harness. Fixed with an explicit `db.flush()`
between the two, matching the pattern NFO's own `Instantiate` already
uses for its own dependent inserts.

**Demo depth: AI/ML Workflow's feature groups.** Continuing the same
"more demo depth" thread. The reference's own `FeatureGroup` entity
(`CreateFeatureGroup`/`GetFeatureGroup`, `featuregroup_controller.py`
— added in an earlier §5 pass) had never been touched by any demo
phase. New section: register a feature group with real InfluxDB-shaped
connection details → confirm it's listed → a real duplicate-name
rejection (`FEATURE_GROUP_ALREADY_REGISTERED`, a genuine
`UniqueConstraint`, not a scripted check) → a real invalid-name
rejection (`FEATURE_GROUP_NAME_INVALID`, the reference's own `\w+`,
3-63 character rule, shared with `TrainingJob` names). Real
Cassandra-backed feature-store queries and `enableDme`'s real DME job
creation stay the same deliberate elisions already documented for this
module. `tests_integration/test_demo_runbook.py` gained a matching
step. No code, schema, or OpenAPI-spec change — confirmed via a full
local Postgres 16 pass (59 tables, 0 mismatches), a live register/list/
duplicate/invalid round trip against that same instance, and the
live-schema-match check, all green.

**Demo depth: SA SMOS's coordination-group remedial action.** Continuing
the same "more demo depth" thread. SA SMOS's own `MLModelCoordinationGroup`
convergence (OPEN_ITEMS.md section 1) had been real and unit-tested since
an earlier pass, but never demonstrated: step 15's own `AssuranceMonitor`
was `targetOrderId`-scoped throughout. New section: register two models →
create a real coordination group → register a `targetCoordinationGroupId`-
scoped `AssuranceMonitor` → execute a remedial action with any
`actionType` (`SCALE`, which would always `ESCALATED` for an order-scoped
monitor) → `outcome` is `RESOLVED` because `execute_remedial_action`
checks the coordination-group target *first*, bypassing the NF-deployment
`actionType` branching entirely, and dispatches a real
`POST /ai-ml-workflow/training-jobs` instead → confirmed by querying the
real resulting `TrainingJob` row. `tests_integration/test_demo_runbook.py`
gained a matching step. Grounding this against real Postgres surfaced a
real, previously-invisible bug — see "Real bugs" below:
`create_coordination_group` had no pre-validation for the migration's own
`>= 2 members` `CHECK` constraint, so a single-member group 500'd instead
of 422ing. Fixed, with a new regression test; re-confirmed via a full
local Postgres 16 pass (59 tables, 0 mismatches), a live two-model
register/reject/create/monitor/remedial/confirm round trip against that
same instance, and the live-schema-match check, all green. One OpenAPI
spec change (`ai-ml-workflow.json`, the new error), regenerated.

**Demo depth: SME's event-subscription `apiId` filtering.** Continuing
the same "more demo depth" thread. `SubscribeEvents`' own `apiIds` filter
(the reference's `CAPIFEventFilter`, `eventservice.go`'s
`getMatchingSubs`) had been real and unit-tested since an earlier §5
pass, but SME's whole event-subscription-and-notification mechanism —
not just the `apiId` filter, the type-only filtering path too — had
never appeared anywhere in this runbook at all. New section: subscribe
one consumer unscoped (`SERVICE_API_UPDATE`, every service) and one
scoped to `helloworld-api`'s own `serviceId` → register-then-update an
unrelated service, which reaches only the unscoped consumer (the scoped
one's own `apiIds` filter excludes it) → update `helloworld-api` itself,
which reaches both. `tests_integration/test_demo_runbook.py` gained a
matching step, intercepting the exact `httpx.post` calls the same way as
the DME type-subscription step above. No code, schema, or OpenAPI-spec
change this time — a live subscribe/register/update/confirm/unsubscribe
round trip against a real local Postgres 16 instance found no issue,
same as the AI/ML Workflow feature-groups pass.

**Demo depth: A1 Related's service supervision sweep.** The last item on
the "more demo depth" list this pass. The reference's own Service
Registry and Supervision contract ("When a service fails to invoke
keepalive within the configured time... automatically deregistered and
its policies will be deleted") had been real and unit-tested since an
earlier §5 pass, but step 11's own `putService` call used
`keepAliveIntervalSeconds: 0` (supervision disabled) throughout, so the
real lazy-sweep-on-read auto-deregistration path had never fired in this
runbook. New section: register a new supervised service with a real,
short `keepAliveIntervalSeconds` → create a policy under it → let the
interval elapse without a keepalive call (a real `sleep`, since no
scheduler exists anywhere in this build — the sweep happens lazily, on
the next `GET /services` read, not on a timer) → the service is
genuinely deregistered (`404`, not just reported stale), and its policy
is torn down alongside it, the same cascade an explicit retract already
demonstrated in step 11. `tests_integration/test_demo_runbook.py` gained
a matching step. No code, schema, or OpenAPI-spec change — a live
register/create/sleep/sweep/confirm round trip against a real local
Postgres 16 instance found no issue.

This closes out every item on the "more demo depth" list from this
whole pass (PRs #88-through-this one) — see OPEN_ITEMS.md's own
"Suggested next pass" section for where this leaves the backlog.

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
rediscover either issue. A third gap surfaced by `a1-related`'s service
registration/supervision (§5) — the first place this build ever computed
an elapsed time: SQLite round-trips a `DateTime(timezone=True)` column as
a naive `datetime` (no `tzinfo`), while Postgres returns one already
tz-aware; subtracting `datetime.now(datetime.UTC)` from a naive value
raises `TypeError`. Originally handled locally in
`a1-related/app/main.py`'s own `_as_utc()` (treat a naive value as UTC,
since that's what's always written); needed a second time by `sme`'s
issued-access-token expiry check, so now lives in
`shared/smo_shared/timeutil.py`'s `as_utc()` instead — not in
`make_test_engine()`, since this isn't SQLite-serializer machinery, it's
a per-column read-time normalization any future elapsed-time computation
on a `DateTime(timezone=True)` column needs to call.

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
- **`Onboarding.OnboardPackage` called NFO's real `CreateDescriptor`
  before its own `ApplicationPackage` row was ever committed** —
  `flush()`, not `commit()`, before the nested cross-service call; NFO's
  `NFDeploymentDescriptor.package_id` has a real FK on it, and under
  real Postgres's own READ COMMITTED isolation (a genuinely separate
  connection for NFO's own session) that row is invisible until
  committed — a real `ForeignKeyViolation` on *every* onboard, never
  once caught by SQLite's own single-shared-connection test harness,
  which gives every session an accidental dirty-read view of every
  other session's uncommitted work. Found grounding the SA SMOS demo
  below, which needed a second real deployment of an already-onboarded
  package. Fixed by committing the package row before calling NFO, not
  after — verified against a real local Postgres 16 instance, not just
  SQLite.
- **NFO's `Terminate` never cleared a deployment's own `LCMOperation`
  history before deleting it** — `LCMOperation.nf_deployment_id` has a
  real FK, same as `NFOCloudResource`'s (already explicitly cleaned up
  first); every deployment has at least one `LCMOperation` row (from
  `Instantiate`), so this was a real `ForeignKeyViolation` on *every*
  real terminate, not just an edge case — SQLite's own test harness
  never enforces FKs by default, so nothing had ever caught it. Found
  the same way as the bug above, verifying the SA SMOS demo's second
  deployment's own teardown against real Postgres. Fixed by clearing
  `LCMOperation` rows alongside `NFOCloudResource`, matching the
  pattern already established there.
- **`tests_integration/`'s own shared SQLite test engine let a cross-
  service call chain three deep silently corrupt itself** — a second
  real `so-smos -> nfo -> focom` order dispatch in the same test hit a
  genuine `StaleDataError` (SQLite's `StaticPool` funnels every nested
  FastAPI request's own `Session` onto the same one physical
  connection; an inner Session's commit was silently committing an
  outer Session's not-yet-committed work too). Confirmed purely a
  test-harness artifact, not an app bug, by running the identical
  sequence against a real local Postgres instance (passes cleanly —
  every Session there gets its own real connection). Fixed with
  SQLAlchemy's own documented pysqlite workaround
  (`shared/smo_shared/testing.py`) plus binding every Session in
  `tests_integration/conftest.py`'s `mesh`/`db_connection` fixtures to
  one shared Connection via `join_transaction_mode="create_savepoint"`,
  so nested Sessions share the one real transaction through SAVEPOINT
  nesting instead of colliding.
- **That same savepoint fix had a second, subtler hole in it** — a
  nested cross-service commit could still be silently discarded, with
  no error at all. Root cause: SQLAlchemy's default
  `expire_on_commit=True` means any attribute read on an outer
  Session's object *after* its own `commit()` (e.g. rapp-mgmt's
  `terminate_instance` building a URL from `inst.package_id` right
  after committing `inst.state`) silently opens a *second* implicit
  transaction (a new, unreleased savepoint) on that same Session. If a
  nested cross-service call then commits — releasing its own savepoint
  into that still-open second one, since savepoints stack — before the
  outer Session is closed, `Session.close()`'s own implicit rollback of
  that never-explicitly-committed second savepoint takes the nested
  commit down with it. Caught concretely: Onboarding's real deprime
  guard kept refusing even after `TerminateInstance`'s own real
  `usage/stop` call had genuinely returned `200`. Confirmed purely a
  test-harness artifact the same way as the bug above (the identical
  prime/deprime/terminate sequence already passes against a real local
  Postgres instance). Fixed with `expire_on_commit=False` on
  `tests_integration/conftest.py`'s `TestSession`.
- **FOCOM's `provision_resource` could hit a real `ForeignKeyViolation`
  on every genuinely new `resourceTypeId`** — its own auto-registration
  of an unrecognized type (`db.add(ResourceType(...))`) and the new
  `Resource` row referencing it were both added to the same flush with
  no explicit flush between them; SQLAlchemy's insert ordering across
  the two didn't reliably insert the parent row first, so the
  dependent `Resource` insert could reference a `resource_type_id` that
  didn't exist yet in the same transaction. A minimal, reproducible
  case against a real local Postgres 16 instance, unrelated to any
  nested-session harness quirk — SQLite's own test harness never
  enforces FKs, so nothing had ever caught it, and no test before this
  pass had provisioned a genuinely new `resourceTypeId` against real
  Postgres at all. Found grounding the new FOCOM topology export demo
  below. Fixed with an explicit `db.flush()` between the two inserts,
  matching the pattern NFO's own `Instantiate` already uses.
- **AI/ML Workflow's `create_coordination_group` had no pre-validation
  for the migration's own `member_model_ids` `CHECK` constraint**
  (`array_length(member_model_ids, 1) >= 2` — a coordination group of
  fewer than two members isn't a coordination of anything). A
  single-member (or empty) `memberModelIds` raised an unhandled
  `IntegrityError` (a bare 500) instead of a clean error, the same class
  of bug `RequestTraining`'s own `exactly_one_target` pre-check already
  guards against elsewhere in this same module. The constraint was never
  mirrored onto the ORM model (only the raw migration DDL has it), so
  SQLite's schema — built straight from the ORM models — never enforced
  it, and no unit test had ever caught it either; one existing unit test
  (`test_list_coordination_groups_returns_members`) even created a
  single-member group and passed, silently exercising behavior real
  Postgres would reject. Found grounding the new SA SMOS
  coordination-group remedial-action demo above. Fixed with a
  `COORDINATION_GROUP_TOO_SMALL` (422) pre-check, matching
  `RequestTraining`'s own pattern; the pre-existing test fixed to use two
  distinct-`model_type` members, plus a new regression test for the
  rejection itself.

## What's deliberately incomplete

Matching the LLDs' own honesty about open items rather than papering over
them. See "Project status" above for how this fits the three-audit
picture; this section is the itemized detail behind it.

**Section 1 design decisions (`OPEN_ITEMS.md`) — blocked on data or a
scope call, not on engineering effort:**

- **`WEIGHTED_TRIGGERS`** (`ai-ml-workflow/`) — raises `NotImplementedError`;
  needs real noise-floor data before it can be designed, not invented now.
- **Alarm-storm correlation algorithm** (`ran-nf-oam/`) — flagged as
  needing a real correlation algorithm; nothing implemented. Same
  reasoning as `WEIGHTED_TRIGGERS`: a fabricated algorithm would be worse
  than an honest gap.
- **A1-ML operations** (`a1-related/`) — schema-dormant, no routes. Building
  them means implementing genuine A1AP behavior, out of this project's
  declared scope categorically (see the A1 Related LLD section 0). Only
  needs revisiting if that scope decision itself changes.

**Formal-spec audit gaps (`SPEC_AUDIT.md`) — real, unstarted work, not
yet attempted for lack of a spec file:**

- **DME** — ICS's own formal spec set isn't in `../specs/` yet.
- **A1 Related** — the 3GPP/O-RAN A1 specs aren't in `../specs/` either;
  the `sim-a1-interface`/`a1pms` source-code audit in `OPEN_ITEMS.md`
  section 5 remains the only ground truth for this module.
- **Onboarding/rApp Mgmt** — TOSCA/rApp packaging specs aren't in
  `../specs/`.
- **O-RAN WG4/WG5 O-RU/O-CU/O-DU management-plane YANGs** — present in
  `../specs/` but never compared against. Likely out of scope given this
  build's single-node topology, but genuinely unconfirmed.

**Two open architectural questions, not code gaps:**

- Policy Mgmt's Intent-to-RMIH matching is producer-side push
  (`create_intent`'s own `_matching_rmihs`); TS28312 IntentNrm's NRM
  containment model (`IntentHandlingFunction-Single` *contains*
  `Intent`) implies the spec's real answer is consumer-side LDN
  selection instead — an MnS consumer picks and addresses an
  already-chosen RMIH when creating an Intent. A real, architecturally
  different, spec-grounded alternative worth a design note before
  treating the current mechanism as final. Moot for `DEMO_RUNBOOK.md`
  — neither `CreateIntent` nor `RegisterIntentHandlingFunction` is ever
  called there.
- RAN Analytics implements TS28104 MDA's own `MDAType` domain (coverage/
  mobility/energy-saving analytics) via `aiml-fw-apm`'s proactive
  producer-push shape rather than TS28104's consumer-request
  (`MDARequest`/`MDAReport`) NRM model — the same confirmed,
  deliberate architecture choice as AI/ML Workflow's below. Two smaller,
  real deltas within that choice are moderate/breaking, not closed:
  `analytics_type` is a free string where the spec defines a real,
  closed 24-value enum (constraining it would reject whatever strings
  any existing caller already uses, not yet audited), and there's no
  `ThresholdInfo`-based conditional reporting at all (every report
  always fires) — AI/ML Workflow's own `MLMFSubscription.guard_kpi_floor`
  is a directly analogous mechanism already in this codebase this
  module could crib from. See `SPEC_AUDIT.md` for the full findings.

**Confirmed, deliberate Phase-1 scope cuts — not gaps, not pickable as
scoped PRs:**

- **RAN NF OAM's MSAC gate** — a single optional `msac_role` string
  checked for presence, not TS28319 MsacNrm's real per-data-node
  Identity/Role/AccessRule ABAC/RBAC engine. A real subsystem, correctly
  left out of Phase 1.
- **RAN NF OAM's flat-string O1 addressing** — opaque strings
  (`managed_element_ref`, `entity_type`) rather than real hierarchical
  LDN with typed identityrefs; the 3GPP base ManagedElement/
  ManagedFunction NRM module itself isn't even in `../specs/` yet to
  fully compare against.
- **RAN NF OAM's file/streaming transport machinery**
  (`FileDataReportingMnS`/`StreamingDataMnS`) — `southbound_engine` is
  just a chosen label; `subscribe_pm` is a DME-producer registration
  wrapper by its own docstring, never a real clause-8 PM job-control API.
- **RESTCONF-provisioned MEs in RAN NF OAM's `WriteConfigurationChanges`**
  (`ran-nf-oam/app/main.py`) — the confirmed dispatch protocol is NETCONF
  only; an ME with `o1_protocol=RESTCONF` is rejected with
  `PROTOCOL_NOT_SUPPORTED` rather than silently applied. The NETCONF RPC
  itself is still sent as XML over plain HTTP, not real SSH/ncclient
  transport, matching this build's all-HTTP-JSON pragmatism everywhere
  else.
- **FOCOM's whole missing O2IMS resource categories**
  (`ProvisioningRequest`'s real template-driven workflow,
  `ArtifactResourceType`/`ArtifactResource`, `NodeCluster`/
  `ClusterResource`, `Gateway`/`SiteNetwork`) and its **thin FCAPS
  model relative to the real O2IMS spec** (a full `AlarmSubscription`/
  notify path, a job/dictionary/state-machine Performance model) —
  consistent with FOCOM's own documented single-degenerate-cluster
  Phase-1 scope; no real hardware telemetry source exists in this build
  to feed a deeper model honestly.
- **AI/ML Workflow's and RAN Analytics's whole TS28105/TS28104 NRM
  containment trees, plus AI/ML Workflow's FL/RL modeling** — both
  modules target the O-RAN-SC `aiml-fw`/`aiml-fw-apm` reference
  architecture instead (a flat REST job-manager / producer-push shape,
  already confirmed via `OPEN_ITEMS.md` section 5), not 3GPP's own
  containment-tree NRM with typed DN addressing, distinct
  Request/Process/Report resources per function, and real
  `FLRequirement`/`RLRequirement` federated/reinforcement-learning
  fields. A confirmed, deliberate architecture choice, the same
  category as FOCOM's O2IMS mismatch above — neither module's own
  runbook demo touches any of this real NRM surface, since neither
  ever claimed to implement it.
- **`ROLLBACK`** (`sa-smos/app/main.py`) — raises a clear, specific error
  (`ROLLBACK_HISTORY_UNAVAILABLE`) rather than picking one of several
  plausible meanings: rApp Management's own upgrade machinery deletes the
  prior `RAppInstance` row on a successful commit, so no version history
  survives anywhere in this build to roll back to. `RECONNECT` is now
  resolved (see the table above).
- **SME's real CAPIF "Trusted Invokers" security-context subsystem** —
  closed; see `SPEC_AUDIT.md`. Its own remaining gap
  (`PrepareNewSecurityContext`'s real cross-check against a published
  AEF's declared security methods) has no equivalent data source in this
  build (no per-AEF security-method catalog was ever modeled) — adapted
  honestly rather than fabricated.
- Every module's actual southbound integration beyond A1 Related's mock
  Near-RT RIC and RAN NF OAM's NETCONF client (`docker run` invocations,
  etc.) is elided in favor of recording the correct state transition —
  this is a reference build of the SMO's own object model and
  orchestration logic, not a full O-RAN stack.
- The full `docker-compose` stack (17 services) has never been run
  end-to-end — no Docker daemon is available in this build's own CI
  runners or any sandbox this project has run in. Only
  `docker compose config` YAML parsing (validated automatically by CI's
  `docker-compose-config` job) and direct pytest execution against each
  service in isolation are verified; the network-isolation claim for
  `a1_mock_net` is structurally correct in the compose file but
  functionally unverified.
