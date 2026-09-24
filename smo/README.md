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
         mock-near-rt-ric mock-o1-adaptor; do
  (cd $m && PYTHONPATH=.:../shared python -m pytest tests/ -v)
done

# cross-service integration tests (multiple modules loaded into one
# process, real calls between them — see tests_integration/)
PYTHONPATH=shared python -m pytest tests_integration/ -v

# regenerate docs/openapi/<module>.json after a real route/schema change
# (test_openapi_specs.py fails CI if a committed spec drifts from this)
PYTHONPATH=shared python scripts/generate_openapi_specs.py
```

**446 tests total, all passing** as of this build: 432 unit tests across
all fourteen modules plus the two mocks, and 14 integration tests proving
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
`focom` went from 37 tests to 38.

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
