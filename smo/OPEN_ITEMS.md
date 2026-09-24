# Open Items — Phase 1 SMO Reference Build

Tracked gaps against the original scope (`SMO Design Document v1.3`, the
`AI-RAN Framework Consolidated Reference`, and the `O-RAN-SC Repo
Inventory`), beyond what PR #1 delivers. Grouped so the next pass can be
picked up module by module. Nothing here blocks PR #1 — CI is green and it
merges as Phase 1's baseline; this is the Phase 2 backlog. §5 adds a
different kind of item: gaps found by cloning the actual O-RAN-SC repos
named in the Repo Blueprint and auditing each module's real completeness
against them, not just inferring from this build's own code.

## 1. Design-level decisions needed (not just code)

These need a call from whoever owns the relevant module's requirements
before they can be implemented — inventing an answer now would just move
the ambiguity into code.

- **`WEIGHTED_TRIGGERS`** (`ai-ml-workflow/`) — currently raises
  `NotImplementedError`. Needs real noise-floor data to design the
  weighting function; the value would be fabricated without it.
- **Alarm-storm correlation algorithm** (`ran-nf-oam/`) — flagged as
  needing a real algorithm; nothing implemented.
- **A1-ML operations** (`a1-related/`) — categorically out of scope per
  the A1 Related LLD section 0; schema-dormant. Revisit only if that scope
  decision changes.

## 2. Repo code / lifecycle gaps

- ~~**`NFDeploymentDescriptor` is never populated**~~ — **closed.** NFO
  now has a `CreateDescriptor` endpoint (`POST /descriptors`), called
  from Onboarding's `OnboardPackage` flow once validation succeeds;
  `rApp Management` consumes the real `nfDeploymentDescriptorId` via
  Onboarding's `onboarding-status` response instead of passing
  `packageId`. Verified against real Postgres (the FK now correctly
  rejects an invalid descriptor ID) and end to end via
  `tests_integration/test_cross_service.py`'s
  `test_onboarding_to_rapp_management_full_deploy_creates_real_nf_deployment_descriptor`.
- ~~`RAppInstance.RECOVER` had no HTTP route~~ — **closed.** The FSM
  transition (`FAULTED -> DEPLOYING`) existed and was unit-tested
  directly against the FSM, but no route in `rapp-mgmt/app/main.py`
  fired it — a critically faulted rApp instance had no API path back to
  `RUNNING`. Added `POST /instances/{id}/recover`, mirroring
  `bootstrap-complete`'s shape.
- ~~Onboarding's cascade-delete guard was unreachable from ordinary rApp
  deployment~~ — **closed.** `PackageUsageRegistration` rows were only
  ever created/stopped via Onboarding's `usage/start`/`usage/stop`,
  which nothing in `rApp Management`'s `CreateInstance`/`TerminateInstance`
  called. `CreateInstance` now calls `usage/start` and stores the real
  `registrationId` (new `RAppInstance.package_usage_registration_id`
  column); `TerminateInstance` now calls `usage/stop` before the row is
  deleted.
- ~~DME's `CreateDataJob` didn't validate against the actual
  `DataOffer`~~ — **closed.** It checked `dataDeliveryMethod` against the
  global `DELIVERY_METHODS` set only, never against what the
  `dmeTypeId`'s own `DataOffer` actually committed to. Now cross-checked
  (skipped when no offer exists for that type, so types without one are
  unaffected).
- **Two more real Postgres-schema bugs found fixing the above, same
  table** (`migrations/001_init.sql`'s `rapp_instance`), both fixed:
  `pending_upgrade_instance_id` was read/written throughout
  `rapp-mgmt/app/upgrade.py` and `main.py` but was **entirely missing**
  from the migration (only present in the SQLAlchemy model) — would
  crash on first use against real Postgres; and `oauth_client_id` was
  `NOT NULL` in the migration even though `_revoke_credential`
  explicitly sets it to `NULL` on `TERMINATE`/`UPGRADE_COMMIT` (closing
  v1.3's RT-3 finding) — would reject that commit outright. Neither was
  ever caught because SQLite's unit tests build their schema from the
  ORM models directly, never from this file, and the migration-Postgres
  CI job only checks table *count*, not columns. Verified fixed against
  a real local Postgres 16 instance. ~~That root cause itself~~ —
  **closed**: `scripts/check_migration_matches_models.py`, wired into
  the `migration-postgres` CI job, now applies the real migration,
  loads every module's ORM models onto the shared `Base`, and compares
  column presence + nullability per table against the live Postgres
  schema — the exact two checks that would have caught both bugs above
  automatically. Verified against a real local Postgres 16 instance:
  reproduced both original bugs directly (dropped the column, added the
  `NOT NULL` back) and confirmed the script fails with the precise
  error for each, then restored a clean migration and confirmed it
  passes (58 tables, 0 mismatches).
- ~~**No real southbound integrations beyond the A1 mock** — O1 Adaptor
  `PATCH` calls, actual `docker run` invocations, etc. are all elided in
  favor of recording the correct state transition.~~ — **closed,
  partially.** The O1 Adaptor `PATCH` half is real now: a new
  `mock-o1-adaptor` module (mirroring `mock-near-rt-ric`'s own scope —
  give the real caller something real to call, not a full protocol
  implementation) answers RAN NF OAM's real RFC 6241 `<edit-config>` RPC
  (`netconf_client.py`, already dispatching one over HTTP, previously to
  nothing that existed anywhere in this build's own topology) with a
  real `<rpc-reply>`, `<ok/>` or `<rpc-error>` depending on the request.
  Proven end to end by two new cross-service integration tests, not just
  a unit test — one of which surfaced a real, separate bug in the test
  harness itself: `tests_integration/mesh.py`'s `dispatch()` only ever
  forwarded a JSON body (`json=`), silently dropping any `content=`
  kwarg — every caller until `netconf_client.py` was JSON-only, so a raw
  XML POST body was forwarded as empty without erroring, masking itself
  as a plausible-looking `REJECTED` outcome rather than a harness bug.
  Fixed. Real `docker run` invocations (NFO) stay unmodeled — no
  equivalent test double exists for that southbound call, a
  structurally different, much bigger elision (real K8s/Helm/container
  orchestration) than answering one HTTP RPC.
- ~~**No real OAuth2/token enforcement at R1 Termination** — only a
  comment and a `tokenEndPoint` URI in the bootstrap response; no actual
  validation code path.~~ — **closed, partially.** The reference's own
  Security/token API (`securityservice.go`'s
  `PostSecuritiesSecurityIdToken`) needs a real API Invoker onboarding
  registry (`invokermanagement.go`) as its own prerequisite — also
  entirely unmodeled before this pass. Added both, minimally: a real
  `InvokerRegistration` (`POST /invoker-registrations`, this build's own
  flattened `apiInvokerId == consumerId == rAppId` identity, the same
  adaptation `ProviderRegistration` already made for producers) and a
  real `POST /oauth2/token` (client_credentials grant, genuinely checked
  `client_id`/`client_secret` against that registry — 400 on either
  failure, matching the reference's own two checks). The reference then
  delegates actual JWT signing to an external Keycloak instance; this
  build has no real IdP (the same no-real-southbound-integration elision
  as everywhere else), so it issues its own opaque, server-tracked
  bearer token instead, validated by a new `POST /oauth2/introspect`
  (RFC 7662 — the honest substitute for self-contained JWT signature
  verification). R1 Termination's gateway now genuinely enforces this on
  every proxied request (`/bootstrap` stays the one exception, per its
  own already-documented no-auth design) — a real, breaking change to an
  already-shipped route, fully handled: it fails CLOSED if SME is
  unreachable (a security gate, not a best-effort notification), and the
  cross-service integration suite is unaffected since it deliberately
  bypasses R1 Termination's own proxy mechanics (`tests_integration/mesh.py`'s
  own documented scope choice). Deliberately **not** adopted: per-scope
  AEF/API validation at token-issuance time (`IsFunctionRegistered`/
  `IsAPIPublished`) — this build elides fine-grained AuthZ throughout, so
  `scope` is accepted and echoed back, never checked against what's
  actually published. Neither the onboarding secret nor the issued
  token is ever stored in cleartext (a GitHub Advanced Security finding
  caught and fixed before merge) — `InvokerRegistration` keeps only a
  salted `scrypt` hash, `IssuedAccessToken` only a SHA-256 hash of the
  token itself.
- ~~**RAN NF OAM's MnS Registry discovery is a heartbeat-aging stub**, not
  real registry polling.~~ — **closed, partially.** Real MnS Registry NRM
  polling stays out of scope (no such external registry exists in this
  build to poll — the same declared elision as OAuth2's Keycloak and O1's
  full ntsim-ng simulator). What's closed: staleness is now computed
  live at `write_configuration_changes`'s own gate (`_age_endpoint_health`,
  shared with the bulk `/discover` sweep), the same "no scheduler exists
  anywhere in this build" pattern already used for DME's producer health
  and A1 Related's service supervision — a stale `ACTIVE` endpoint is
  caught and rejected (`ENDPOINT_UNREACHABLE`) the moment a config write
  is attempted against it, not only if something had separately polled
  `/discover` first. Writing real tests for this (previously zero — only
  the FSM transition itself was unit-tested, never the route) surfaced
  the third occurrence of the naive-vs-aware `DateTime(timezone=True)`
  SQLite portability gap (first hit by A1 Related, then SME): fixed with
  `smo_shared.timeutil.as_utc`, the same helper both of those already
  use.
- ~~**No persisted OpenAPI spec files anywhere** — relying entirely on
  FastAPI's live `/docs` generation rather than committed contracts.~~ —
  **closed.** Added `docs/openapi/<module>.json` for all fourteen
  modules plus the mock, generated by `scripts/generate_openapi_specs.py`
  from each app's own real `app.openapi()` output — not hand-written,
  not invented, just frozen to disk so a real contract change shows up
  as a diff in review. `tests_integration/test_openapi_specs.py` fails
  CI if a committed file drifts from the live schema. Writing that test
  surfaced a real bug: `r1-termination`'s catch-all proxy route (one
  `APIRoute` serving five HTTP methods) got a non-deterministic
  `operationId` from FastAPI's own `generate_unique_id()`, which picks
  `list(route.methods)[0]` — a plain `set`, so the pick depends on
  `PYTHONHASHSEED` and differs across process runs, making any committed
  spec for that route inherently flaky. Fixed with an explicit
  `operation_id="proxy"` (not referenced by any client in this build, so
  pinning it is a pure stability fix).
- ~~**FOCOM's hardcoded single-cluster stub.**~~ — **closed, partially.**
  The single-cluster *topology* itself stays Phase 1's declared scope
  (D-DEPLOY-FOCOM-1) — this build never claimed to model more than one
  cluster. What was still genuinely a stub: the §5 pass below gave FOCOM
  a real `ResourceType`/`ResourcePool`/`DeploymentManager` schema and
  wired every drill-down route to it, but left `GET /inventory` — the
  one thing NFO's real Instantiate call actually depends on — as a
  hardcoded literal that never touched that schema at all. Now sourced
  from the same seeded row every other route reads.
- ~~**NFO's Heal/Scale operations are stubs.**~~ — **closed**, and stale
  by the time this line was reached: closed already by the NFO+FOCOM §5
  pass below (real 7-state lifecycle, Heal/Scale now drive real state
  transitions) — this bullet just never got struck through when that
  landed.
- **None of the Repo Map's ADOPT recommendations are actually
  vendored/integrated** — this build consolidates on one Python/FastAPI
  stack rather than forking `nonrtric-plt-sme` (Go), the ICS reference
  (Java), `pti-o2` (Python), etc. The ADOPT repos stay pattern references
  only. §5 goes further than this: it audits, per module, whether the
  *functionality* those repos implement was still carried over even
  without vendoring the code — it mostly wasn't.
- **The full `docker-compose` stack (17 services, including the isolated
  `a1_mock_net` network segment) has never been run end-to-end** — no
  Docker daemon is available in the build sandbox (or in this build's own
  CI runners), so this stays genuinely out of scope: only
  `docker compose config` YAML parsing and direct Python/pytest execution
  against each service in isolation have been validated. The RT-7
  network-isolation claim is structurally correct in the compose file but
  functionally unverified. **Narrower piece closed**: that YAML-parsing
  validation used to be purely manual — an agent remembering to run
  `docker compose config` by hand after touching the compose file, easy
  to forget (and, per this build's own history, occasionally forgotten).
  A new CI job (`docker-compose-config`, no Docker daemon required —
  `docker compose config` only parses and renders the file) now runs it
  automatically on every push/PR touching `smo/**`, the same "catch real
  structural drift automatically instead of relying on human diligence"
  philosophy already used for the OpenAPI spec drift-check.

## 3. Call-flow gaps — closed

~~Only 4 of many plausible cross-module journeys were diagrammed~~ — all
six missing journeys listed here previously are now in
`smo/docs/call-flows/` (05 through 10): A1 EI registration end-to-end,
onboarding failure/deprecation/deletion paths, rApp fault/performance
reporting, RAN Analytics' own data-production flow, the Policy Mgmt
Intent-driven flow, and a multi-step SO SMOS order combining
INFRA + TRAINING + DEPLOY.

Writing them surfaced four real, previously-undocumented gaps. Three
are closed above (§2: `RAppInstance.RECOVER`'s missing route, the
cascade-delete guard's dead usage-registration wiring, DME's unchecked
`DataOffer`/`DataJob` method mismatch); the fourth (Policy Mgmt's
Intent-to-RMIH matching) turned out to need its own design decision and
moved to §1 instead:

- `RAppInstance`'s `RECOVER` transition (`FAULTED -> DEPLOYING`) had no
  HTTP route — a critically-faulted rApp instance had no API path back
  to `RUNNING` at all (call flow 07). Closed.
- Onboarding's cascade-delete guard depended on `PackageUsageRegistration`
  rows that rApp Management's `CreateInstance`/`TerminateInstance` never
  actually created or stopped — `usage/start`/`usage/stop` were reachable
  only out-of-band, not from ordinary rApp deployment (call flow 06). Closed.
- DME's `CreateDataJob` validated `dataDeliveryMethod` against the
  global known-methods set only, never against the specific `DataOffer`
  the `dmeTypeId` is actually associated with (call flow 05). Closed.
- Policy Mgmt has no matching/dispatch step between `CreateIntent` and
  `RegisterIntentHandlingFunction` — an RMIH is never notified of a new
  Intent it could fulfil; `IntentHandlingFunction.intent_handling_scope`
  is modeled but no code path ever sets or reads it (call flow 09). Needs
  a design decision — see §1.

## 4. Test coverage is uneven

Per-module unit test counts:

| Module | Tests |
|---|---|
| mock-o1-adaptor | 6 |
| policy-mgmt | 10 |
| sa-smos | 12 |
| so-smos | 13 |
| r1-termination | 15 |
| mock-near-rt-ric | 16 |
| rapp-mgmt | 19 |
| ran-analytics | 22 |
| nfo | 23 |
| onboarding | 30 |
| ran-nf-oam | 34 |
| focom | 38 |
| a1-related | 43 |
| sme | 47 |
| ai-ml-workflow | 49 |
| dme | 55 |

Plus 14 cross-service integration tests in `tests_integration/`.
`mock-near-rt-ric`/`r1-termination`/`policy-mgmt` (10 tests each) are
now the shallowest-covered tier — `nfo` moved out of it in a later pass
(gained real deployment-state-machine coverage). `rapp-mgmt` and
`dme` moved out of the shallow tier in an earlier pass (both gained
route-level tests); a later pass added route-level coverage for the five
§1 items closed below — `ai-ml-workflow` (+4), `policy-mgmt` (+3),
`sa-smos` (net +2, replacing one parametrized "ambiguous" test with four
RECONNECT/ROLLBACK-specific ones), and `ran-nf-oam` (+10: its first
route-level tests at all, plus unit tests for the new NETCONF client).
This pass closed the test-coverage priority item: `r1-termination` gained
coverage for non-GET methods, body/header/query-param forwarding, and
non-200 upstream passthrough (previously only GET and the URL-stripping
fix were exercised); `mock-near-rt-ric` gained coverage for
`UpdatePolicy` (`PUT /a1-p/policies/{id}`), which had zero tests at all
before this pass despite being a real route. A later pass added
`ai-ml-workflow`'s `PUT`/`DELETE /models/{id}` coverage (+6, see §5),
then `ran-analytics`'s real subscriber-notification delivery in
`publish_report` (+5, see §5), then `dme`'s `PUT /data-jobs/{id}`
(+5), its `/type-subscriptions` mechanism (+9), and real
job-definition JSON Schema validation (+5, see §5), then
`onboarding`'s package-validation hardening (+4), `sme`'s `apiIds`
event-subscription filter (+2), and finally `sme`'s real
`aefProfiles`/`apiSuppFeats`/`shareableInfo` fields plus
`discover_services`' matching filters (+5, see §5).

## 5. O-RAN-SC completeness gaps (repo-audited)

Every other section in this document was written from this build's own
code and the LLDs. This section is different: it's from directly cloning
the actual O-RAN-SC repos the Repo Blueprint named per module (18 repos,
under `/home/user/o-ran-sc/` when audited) and comparing each one's real
API surface against our implementation, route by route. The question
asked wasn't "does a pattern reference exist" (already answered by the
Blueprint) but "did we actually carry over everything in scope, or did
we quietly drop real functionality." The answer, per module: real,
concrete holes exist everywhere audited — this was not a clean bill of
health.

Two categories are kept separate. **Real gaps** are in-scope operations,
fields, or behaviors the reference repo has that ours doesn't — these
are actionable, pickable items, the same as everywhere else in this
document. **Structurally out of scope** is the already-known southbound-
elision pattern (real K8s/Helm/Kafka/S3/Kubeflow execution, real
transport security) — not repeated in full here since §2 already tracks
it in aggregate; only named again where a specific audit sharpened it.

Not every module could be audited this way: **R1 Termination** has no
O-RAN-SC repo of its own (only an external Kong pattern reference, not
this org's code) — nothing to diff against. **Policy Mgmt & Info**,
**SO SMOS**, and **SA SMOS** were independently confirmed at Blueprint
time to have no direct O-RAN-SC repo match (`BUILD` verdict) — there is
nothing upstream to audit completeness against for these three; their
own §1/§2 items stand as-is.

### SME (`sme/`) — vs `nonrtric-plt-sme`

- ~~**`notify_service_change` has no caller** — the one piece of event
  delivery this build actually wrote is never invoked from
  `register_service`/`deregister_service`, so `SubscribeEvents` is a
  dead pipeline end to end. The reference fires
  `SERVICE_API_AVAILABLE`/`UNAVAILABLE`/`UPDATE` from exactly those
  routes.~~ — **closed.** `register_service` now fires
  `SERVICE_API_AVAILABLE` on create and `SERVICE_API_UPDATE` on the
  idempotent re-registration path; `deregister_service` fires
  `SERVICE_API_UNAVAILABLE` (called before the row is deleted, since
  `notify_service_change` needs the still-live `ServiceProfile`/
  `authz_policy`). Also found and fixed while wiring this in:
  `notify_service_change`'s own docstring already claimed the same
  authz gate `discover_services` uses, but the code never enforced
  it — an unauthorized subscriber would have been notified about a
  service it isn't even allowed to discover. Now enforced.
- ~~Event subscription filtering is type-only — no per-`apiId`,
  `apiInvokerId`, or `aefId` filter, which the reference's
  `EventFilters` supports.~~ — **closed, partially.** Added `apiIds`
  to `SubscribeEvents`/`ServiceEventSubscription` (the reference's own
  `CAPIFEventFilter.apiIds`, `eventservice.go`'s
  `getMatchingSubs`/`matchesFilters`) — a subscription scoped to one or
  more `apiId`s (this build's `serviceId`) is no longer notified about
  a different service's events. `apiInvokerId` and `aefId` stay
  unimplemented for a concrete reason, not dropped silently: the
  reference's own `apiInvokerId` filter matches against an *event's*
  own `ApiInvokerIds`, which only `API_INVOKER_ONBOARDED`-class events
  carry — a real CAPIF Invoker-onboarding subsystem this build doesn't
  have (this module's own structurally-out-of-scope note below); this
  build's `SERVICE_API_*` notifications have no invoker id in their
  payload to filter on at all. `aefId` needs `aefProfiles`, which
  `ServiceProfile` doesn't model — the separate "flattened
  `ServiceProfile`" gap immediately below.
- ~~`discover_services` only filters on `api_name`/`api_version` — the
  reference also filters on category, `aefId`, protocol, data format,
  and comm type, against a nested `AefProfiles → Versions → Resources`
  structure ours has no equivalent of.~~ — **closed, partially.**
  `aefId`/`protocol`/`dataFormat`/`commType` are now real filters
  (`discoverservice.go`'s `matchesFilter`/`checkAefId`/`checkProtocol`/
  `checkDataFormat`/`checkVersionAndCommType`), walking the new
  `aef_profiles` field below. `category` stays unfiltered — this
  build's `ServiceProfile` has no category concept to filter on at all
  (not just an unwired filter param, an entirely absent one).
- ~~`ServiceProfile` is flattened — no `aefProfiles` (multiple exposing
  functions per API), `apiSuppFeats`, or `shareableInfo` (cross-provider
  sharing flag).~~ — **closed.** Added all three
  (`ServiceAPIDescription`'s own fields) to `ServiceRegistration`/
  `ServiceProfile`, registered and read back wholesale. `aefProfiles`
  is stored as JSON rather than normalized `AefProfile`/`Version`/
  `Resource` child tables — it's registered and queried as one unit,
  never independently CRUD'd, the same adaptation this build already
  uses for `DMEType.collection_spec`/`TrainingJob.required_data`. Only
  the fields `discover_services`' own new filters need are kept
  (`aefId`, `protocol`, `dataFormat`, per-version `resources[].commType`)
  — not the full CAPIF `AefProfile`/`Resource` schema (`aefLocation`,
  `domainName`, `interfaceDescriptions`, `custOperations`, etc.).
- ~~`register_service` accepts any `apf_id` with no check that it's an
  actual registered publisher — a direct consequence of provider
  enrolment being unmodeled.~~ — **closed.** An earlier pass investigated
  this and stopped short, reasoning that closing it honestly would mean
  un-declaring the very next bullet's "Provider enrolment: structurally
  out of scope" boundary — too big a change for a thinner-than-reference
  pass. Revisited and built for real: added a real, minimal Provider
  (APF) enrolment registry (`ProviderRegistration`,
  `POST`/`DELETE /provider-registrations`, the reference's own
  `providermanagement.go`'s `ProviderManager`/`PostRegistrations`/
  `DeleteRegistrationsRegistrationId`), scoped to exactly what this
  build's own flattened identity needs — `apf_id` alone (`apfId ==
  producerId == rAppId`, `register_service`'s own established
  equivalence), not the reference's full three-tier provider-domain ->
  APF/AEF/AMF-function hierarchy, which nothing else in this build
  models either. `register_service` and `query_own_services` now both
  enforce the reference's own real gate
  (`serviceRegister.IsPublishingFunctionRegistered(apfId)`,
  `publishservice.go`) — 403/404 for an unenrolled `apf_id`, mirroring
  `PostApfIdServiceApis`/`GetApfIdServiceApis` exactly, including the
  reference's own "existing services always win over current enrolment
  state" branch on the GET side. This is a real, breaking change to an
  already-shipped route's contract, not just an addition: RAN
  Analytics' `register_analytics_producer` (SME's only cross-module
  caller) now enrols its producer with SME before publishing its
  service, the same real two-step CAPIF dance the reference itself
  requires — verified against the real, in-process cross-service
  integration suite, not just a mock. Deliberately **not** adopted: the
  reference's own provider-domain/function-id split, its PUT
  (update-with-function-diffing) semantics, and its
  cross-provider-domain function-id collision detection — none of that
  has an equivalent concept in this build to attach to, the same
  "flatten to what this build's own identity model needs" adaptation
  already used for `aefProfiles`/`DMEType.collection_spec` elsewhere.
- *Now only partially out of scope, revised by direct inspection*: API
  Invoker onboarding and the Security/token API remain real CAPIF
  subsystems the reference implements that this build assumes
  pre-established — not gaps, a declared boundary.
  `accesscontrolpolicyapi`/`routinginfoapi`/`auditingapi`/`loggingapi`
  are unimplemented in the reference itself too — nothing to catch up
  to there. Provider enrolment itself is no longer fully out of scope
  (see above) — only its AEF/AMF function roles and provider-domain
  concept stay unmodeled, since this build has no separate exposing- or
  management-function identity anywhere else to attach them to.

### DME (`dme/`) — vs `nonrtric-plt-informationcoordinatorservice` (ICS)

- ~~**`producerHealthCallbackUrl` is stored but never called** —
  `typeStatus` is computed only from whether a `DataJob` row is
  `ACTIVE`, so a dead producer with an active job still reports
  `ENABLED`. ICS actually polls the callback and derives status from
  real producer availability.~~ — **closed.** `typeStatus` now calls
  the registered `producerHealthCallbackUrl` for real
  (`ConsumerController.typeStatus`/`ProducerSupervision`'s own health
  signal — ENABLED iff the producer answers, not whether any `DataJob`
  happens to be `ACTIVE`). Computed live, at read time
  (`GET /dme-types`), rather than via a periodic background poll — no
  scheduler exists anywhere in this build (elided, same as the real PM
  file-collection pipeline elsewhere), so a live check on read is the
  honest substitute. An unreachable/non-2xx producer now genuinely
  reports `DISABLED` even with an `ACTIVE` job.
- ~~No job push to producers at all — ICS POSTs the job definition to
  the producer's callback URL on create/delete; `create_data_job`/
  `terminate_data_job` only ever touch our own DB.~~ — **closed.**
  Added a real `jobCallbackUrl` field to `DMEType` registration
  (ICS's own `InfoProducer.jobCallbackUrl`, distinct from the
  health-supervision URL), and `create_data_job`/`terminate_data_job`
  now genuinely POST/DELETE to it (ICS's own
  `ProducerCallbacks.startInfoJob`/`stopInfoJob`, matching
  `ProducerJobInfo`'s wire shape). Best-effort, same pattern as every
  other DME/FOCOM/A1-Related notification in this build — an
  unreachable producer never fails the consumer-facing call. Also
  closed the two real producers' own half of this: `ran-nf-oam` and
  `a1-related` both now answer `/dme-jobs` (the URL they themselves
  register), the same dangling-callback bug class already fixed for
  `/health`.
- ~~No producer-status endpoint (`GET .../info-producers/{id}/status`).~~
  — **closed.** Added `GET /production-capabilities/{producer_id}/status`,
  reusing the same live health-check signal `typeStatus` now uses
  (ICS's own `ProducerController.getInfoProducerStatus`/
  `ProducerStatusInfo` — `ENABLED`/`DISABLED` from real producer
  availability). 404 if the producer has nothing registered, matching
  ICS's own not-found behavior.
- ~~No job-definition schema validation against `dataProductionSchema` —
  `productionJobDefinition` is accepted as an arbitrary dict.~~ —
  **closed.** ICS's own `InfoJobs.validateJsonObjectAgainstSchema`
  (`org.everit.json.schema`, called from `validatePutInfoJob`) does
  real JSON Schema validation of `jobDefinition` against the type's
  `jobDataSchema` before accepting a job. Adopted the Python
  equivalent, the `jsonschema` library (new dependency, added to
  `shared/pyproject.toml` and the CI workflow), in both
  `create_data_job` and `update_data_job` — a `productionJobDefinition`
  that doesn't validate against its `DmeType`'s registered
  `dataProductionSchema` is now rejected (`SCHEMA_VALIDATION_FAILED`,
  422) instead of accepted as an arbitrary dict. A `dmeTypeId` that
  doesn't resolve to a registered type skips the check, matching the
  same permissive shape `_validate_delivery_method`'s own offer check
  already has — nothing else in `create_data_job` enforces the type's
  existence either.
- ~~No GET-by-id for `DataJob`/`DataOffer`, no job-level status
  endpoint, and `discover_dme_types`' `data_category` query param is
  declared but silently never applied to the query.~~ — **closed.**
  Added `GET /data-jobs/{id}`, `GET /data-jobs/{id}/status`, and
  `GET /offers/{id}` (all 404 on an unknown id). `discover_dme_types`
  now applies `data_category` — filtered against `namespace`, since
  `DMEType` has no dedicated category column and namespace (the
  grouping half of R1AP's `namespace.name` typeName convention) is the
  closest concept it does have.
- ~~No update-in-place (PUT) semantics — only POST-create/DELETE.~~ —
  **closed.** Added `PUT /data-jobs/{id}` (ICS's own
  `PutIndividualInfoJob`, `ConsumerController.java`). ICS's own PUT is
  create-or-update against a caller-supplied `jobId` (201 new / 200
  updated); this build's `dataJobId` is always server-generated (see
  `create_data_job`), so this endpoint only ever updates an existing
  job — 404 on an unknown id, matching this module's other
  GET/DELETE-by-id routes. ICS itself also rejects changing a job's
  type mid-update ("Cannot modify job type", 409 there); the
  equivalent identity fields here (`dmeTypeId`/`consumerId`/
  `dataDeliveryMode`, all fixed at creation) are likewise immutable via
  this endpoint (`DATA_JOB_TARGET_IMMUTABLE`, 400 — the same adaptation
  AI/ML Workflow's `update_model` already made for its own identity
  fields). `dataDeliveryMethod` is re-validated against the same
  `DataOffer` cross-check `create_data_job` already applies (factored
  into a shared `_validate_delivery_method` helper), and a successful
  update re-pushes the job to the producer (ICS re-runs
  `startInfoSubscriptionJob` on every PUT, new or updated, not just on
  first creation).
- ~~No type-subscription mechanism (consumers notified when a type is
  registered/removed) — entirely absent.~~ — **closed.** Added
  `POST`/`GET`/`DELETE /type-subscriptions` and
  `GET /type-subscriptions/{id}` (ICS's own `/info-type-subscription`,
  `InfoTypeSubscriptions`/`ConsumerCallbacks`). ICS's own PUT is
  create-or-update against a caller-supplied `subscriptionId`; this
  build's id is server-generated, the same adaptation already made for
  every other subscription in this codebase (RAN Analytics, A1
  Related, Policy Mgmt, FOCOM). `register_dme_type` and
  `deregister_producer` now best-effort POST
  `{infoTypeId, jobDataSchema, status: REGISTERED|DEREGISTERED}` to
  every subscriber's `notificationDestination` (ICS's own
  `notifyTypeRegistered`/`notifyTypeRemoved`) — unfiltered, matching
  the reference's own lack of per-type scoping on this subscription.
- ~~`deregister_producer` deletes a producer's `DMEType` rows
  unconditionally — ... no cascade cleanup of orphaned `DataJob`/
  `DataOffer` rows.~~ — **closed.** This was worse than "orphaned":
  neither FK had an `ON DELETE CASCADE` (unlike `dme_delivery_schema`'s
  own already-cascading one), so deleting a `DMEType` with an existing
  `DataJob`/`DataOffer` would either silently orphan the rows (SQLite,
  no FK enforcement — never caught until this pass) or crash with an
  unhandled `IntegrityError` on real Postgres. Added the matching
  `ON DELETE CASCADE` to both FKs, plus explicit application-level
  cleanup in `deregister_producer` as a second, directly-testable line
  of defense — verified the cascade fires for real against a local
  Postgres 16 instance.

### Onboarding + rApp Management (`onboarding/`, `rapp-mgmt/`) — vs `nonrtric-plt-rappmanager`

- ~~**Missing package-level priming stage** — the reference has a
  distinct COMMISSIONED→PRIMING→PRIMED→DEPRIMING lifecycle that
  pre-provisions ACM composition/DME/SME resource declarations
  *before* any instance deploys, and blocks deprime/delete while
  instances reference the package. Our `onboarding` goes
  ONBOARDING→AVAILABLE directly and does all provisioning inline
  per-instance in `rapp-mgmt`, collapsing a real two-phase lifecycle
  into one.~~ — **closed, partially.** Added the real
  `PRIMING`/`PRIMED`/`DEPRIMING` states and `POST
  /packages/{id}/prime`/`POST /packages/{id}/deprime` (our `AVAILABLE`
  plays the reference's `COMMISSIONED` role). `deprime` is genuinely
  blocked while any active `PackageUsageRegistration` exists (the
  reference's own deprimeRapp guard), and `DELETE` has no edge from
  `PRIMED` at all — matching the reference's own `deleteRapp` guard
  ("the rApp is not in COMMISSIONED state") — so deleting a primed
  package now genuinely requires depriming first, not just as a
  documented rule. Real ACM/DME/SME resource pre-provisioning behind
  `PRIME` stays out of scope (same elision as this build's other
  southbound calls), so both transitions complete synchronously
  within one request rather than staying observably
  `PRIMING`/`DEPRIMING`. Deliberately **not** changed:
  `rapp-mgmt`'s `CreateInstance` still gates on `AVAILABLE`, not
  `PRIMED` — that's an already-confirmed, explicitly-cited design
  decision (D-SEC-RAPP-1) in `rapp-mgmt/app/main.py`, not something
  this pass should silently override. The real causal link the
  reference has ("primed resources exist before an instance can
  deploy against them") is therefore still not enforced — the
  lifecycle and its blocking semantics are real, but nothing yet
  requires a package to have been primed before an instance is
  created against it.
- ~~Package validation is much thinner — the reference runs an ordered
  validator chain (filename convention, required
  `Definitions/acm_composition.json`, ASD descriptor parsing with real
  duplicate-descriptor-id detection). `_validate_package` only reads
  `TOSCA.meta` and does a `KeyError` existence check — no filename
  check, no duplicate-package detection at all.~~ — **closed,
  partially.** Added the reference's `NamingValidator` (`.csar`
  filename convention, checked before ever fetching the location) and
  `FileExistenceValidator`'s required `Definitions/acm_composition.json`
  (alongside the existing `TOSCA-Metadata/TOSCA.meta` requirement, not
  replacing it), plus duplicate-package detection adapted to this
  build's own identity — a content hash (`integrity_hash`, already
  computed but never checked for uniqueness) — since the reference's
  own check is keyed on ASD descriptor data this build doesn't have.
  All three route a failing package to `FAILED`, matching this
  endpoint's existing async-contract shape (every call returns 202;
  success or failure is only observable via `onboarding-status`), not
  a synchronous HTTP rejection. The reference's real ASD descriptor
  parsing itself (`AsdDescriptorValidator`'s JSON-pointer walk into a
  descriptor/descriptor-variant id) stays out of scope — this build has
  no ASD descriptor concept to parse, the same elision already
  documented for `RappInstance`'s nested ACM/SME/DME resource records
  below.
- ~~No resource-provenance detail endpoints — the reference's
  `GET /rapps/{id}` and `GET /rapps/{id}/instance/{id}` return nested
  ACM/SME/DME resource records (composition IDs, provider-function IDs,
  producer/consumer type lists); ours returns only flat
  `{packageId, state, ...}`/`{instanceId, packageId, state}`.~~ —
  **closed, partially.** Added `GET /instances/{id}` — previously not
  even a single-instance detail read existed at all, only the list
  route and single-field sub-resources (`config`). The reference's own
  nested ACM/SME/DME resource records stay out of scope, unchanged:
  they're the caller-supplied deploy descriptor this build's
  `CreateInstance` never accepts in the first place (real ACM/Helm/K8s
  deployment is the declared elision) — echoing them back would mean
  inventing descriptor data, not exposing something this build already
  computes. What the new route does genuinely expose: `workloadRef`
  (the real NFO `nfDeploymentId` `CreateInstance` received back — the
  one real resource reference this build tracks) and the caller-supplied
  `configuration`, alongside the identity/state fields `list_instances`
  already returns.
- ~~No standalone delete-after-undeploy for an instance, distinct from
  `terminate`.~~ — **closed.** Adopted the reference's own split
  (`RappService.undeployRappInstance`/`deleteRappInstance`, DEPLOYED ->
  UNDEPLOYING -> UNDEPLOYED, delete only legal from UNDEPLOYED):
  `TERMINATE` now only tears the workload down (credential revocation,
  DME producer reconsideration, package-usage-stop — all unchanged) and
  lands in a terminal `UNDEPLOYED` state with the instance row still
  present, replacing the old `TERMINATING` state name. Deleting the row
  is now the separate `DELETE /instances/{id}` — 409
  (`RAPP_INSTANCE_NOT_UNDEPLOYED`) unless the instance is already
  `UNDEPLOYED`, matching the reference's own guard message ("not in
  UNDEPLOYED state"). Also found and fixed while wiring this in: neither
  `rapp_fault_report` nor `rapp_performance_report` had an `ON DELETE
  CASCADE` on their `instance_id` FK — the same FK-cascade bug class
  already found in DME's `deregister_producer`/AI-ML Workflow's
  `deregister_model` — so an instance with fault/performance history
  would have orphaned those rows (SQLite) or crashed with an unhandled
  `IntegrityError` (real Postgres) the first time this new DELETE was
  ever exercised. Fixed with both a DB-level `ON DELETE CASCADE` and
  explicit application-level cleanup, verified against a real local
  Postgres 16 instance. Not touched: `CreateInstance` still deploys the
  workload immediately and unconditionally (an already-cited, unrelated
  design decision, D-SEC-RAPP-1) — the reference's own POST only
  registers an instance `UNDEPLOYED`, with a separate `PUT .../instance/
  {id}` (`DeployOrder.DEPLOY`) actually triggering deployment; adopting
  that half too would mean reworking `CreateInstance`'s already-shipped
  contract, not just adding a standalone delete, so it's out of scope
  for this item specifically.
- *Confirmed structurally out of scope*: real ACM/Helm/K8s deployment
  (`rapp-manager-acm`'s composition create/prime/instantiate + real
  DeployState convergence polling) is the single largest elision in
  this whole build, and it's the intended one — already documented, not
  hidden. Real SME/CAPIF provider registration is the same pattern.
  (Also worth noting: this build's fault/performance reporting and
  CRASH/RECOVER/UPGRADE states are *additions* beyond the reference's
  own scope, not omissions.)

### RAN NF OAM (`ran-nf-oam/`) — vs `nonrtric-plt-ranpm`, `oam`, `smo-o1`, `sim-o1-interface`, `sim-o1-ofhmp-interfaces`

- ~~**`subscribe_pm` registers a dangling callback** — it POSTs
  `"producerHealthCallbackUrl": "http://ran-nf-oam:8000/health"` to DME,
  but no `/health` route (or any producer job-callback route) exists
  anywhere in `ran-nf-oam/app/main.py`.~~ — **closed.** Added
  `GET /health` returning `{"status": "healthy"}`, a plain liveness
  check answering the exact URL `subscribe_pm` registers with DME.
  Covered by
  `test_health_endpoint_answers_the_callback_url_subscribe_pm_registers`.
  Note: `a1-related/app/main.py`'s `register_ei_type` registers the
  identical `producerHealthCallbackUrl` pattern
  (`http://a1-related:8000/health`) with DME and had the same missing
  route — same bug class, fixed the same way, see below.
- ~~Alarm model is missing standard fault fields the wire format
  (VES/3GPP alarm IRP, per `oam`'s notification templates) carries:
  `probableCause`, `specificProblem`, `perceivedSeverity`,
  `rootCauseIndicator`, `correlatedNotifications` (a real list of
  related-alarm refs, not just a grouping string),
  `proposedRepairActions`.~~ — **closed.** Added `probableCause`,
  `specificProblem`, `rootCauseIndicator`, `correlatedNotifications`
  (a real `UUID[]` of related-alarm refs, alongside the existing
  `correlationGroup` grouping string, not replacing it), and
  `proposedRepairActions` to `Alarm`, all wired through
  `ingest_alarm` and exposed on `GET /alarms`. Verified against a
  real local Postgres 16 instance (field shapes match `oam`'s own
  `stndDefined-r16-notify-new-alarm.json` VES template).
  `perceivedSeverity` is not a separate new field — this build's
  existing `severity` column already carries that exact semantic
  content under its own wire name, so adding a second, duplicate
  field for it would be pure churn, not a real gap.
- ~~**No alarm-cleared lifecycle at all** — `/alarms/{id}/ack` only
  toggles `ack_state`; there's no CLEARED state or clear-alarm
  endpoint, so an alarm that stops recurring on the NF has no way to
  ever be marked resolved.~~ — **closed.** Added
  `PATCH /alarms/{id}/clear`, which sets `severity` to `'cleared'` —
  the reference's own `NotifyClearedAlarm` reuses
  `perceivedSeverity=CLEARED` rather than a separate state field, and
  this build's `severity` CHECK constraint already allowed `'cleared'`
  for exactly this reason, so this closes the gap without adding a
  redundant parallel field. Also added `clearedAt`/`clearUserId`
  metadata (the reference's `NotifyClearedAlarm` fields). A cleared
  alarm stays queryable via `GET /alarms`, not deleted.
- *Confirmed structurally out of scope*: the real PM file-collection/
  KPI-computation pipeline (`ranpm`'s FTPES/SFTP collector, XML→JSON
  converter, counter distributor) is a total, already-documented
  elision — this module is correctly scoped as a registration wrapper
  only. Real NETCONF/SSH transport and a real xNF simulator are the
  same pattern. No repo audited implements a real alarm-correlation
  *algorithm* either, so `correlation_group` being a coarse string (not
  an algorithm) tracks the reference's own immaturity, not a gap behind
  it.

### A1 Related (`a1-related/`, `mock-near-rt-ric/`) — vs `sim-a1-interface`, `nonrtric-plt-a1policymanagementservice`

- ~~**`register_ei_type` registers a dangling callback** — same bug
  class as ran-nf-oam's `subscribe_pm`: it POSTs
  `"producerHealthCallbackUrl": "http://a1-related:8000/health"` to
  DME, but no `/health` route exists in
  `a1-related/app/main.py`.~~ — **closed.** Added `GET /health`
  returning `{"status": "healthy"}`, same fix as ran-nf-oam's. Covered
  by
  `test_health_endpoint_answers_the_callback_url_register_ei_type_registers`.
- ~~No policy list/query-by-filter endpoint at all (`GET /policies`
  filterable by type/RIC/service) — only `GET /policies/{id}`
  exists.~~ — **closed.** Added `GET /policies`, filterable by
  `policy_type_id`/`near_rt_ric_id`/`creator_id` (the closest concept
  this model has to "service" is `creator_id` — the rApp that created
  the policy — since there's no separate service identifier).
- ~~No policy-type detail retrieval (`GET /policy-types/{id}`) —
  `QueryPolicyTypes` returns a hardcoded Python set (`KNOWN_POLICY_TYPES`),
  never sourced from or synced with an actual RIC; `nearRtRicId` is
  accepted but never used to filter or query anything real. No RIC
  repository (`/rics`) concept exists at all.~~ — **closed, partially.**
  Added `GET /policy-types/{id}` (the reference's own
  `GetPolicyTypeDefinition`, `pms-api-v3.json`), 404 on an unknown type,
  else a real `PolicyTypeObject` (`policySchema`/`statusSchema`). The
  `policySchema` returned is an honest empty placeholder
  (`{"type": "object"}`), not a fabricated A1TD schema this build was
  never given — the same "unknown real content, permissive placeholder"
  pattern already used for `ran-nf-oam`'s/`a1-related`'s own DME type
  registrations. `KNOWN_POLICY_TYPES` never being sourced from a real
  RIC, and the reference's separate RIC repository (`GET /rics`), stay
  out of scope — this build models no near-RT-RIC entity or inventory
  beyond the single A1 mock, so there's nothing real to attach a `/rics`
  route to without inventing one from nothing.
- ~~**`SubscribePolicyStatus`/`UnsubscribePolicyStatus` are pure no-ops
  with zero delivery anywhere in the stack** — confirmed against the
  real mechanism: the reference PMS passes a per-policy
  status-notification URI down to the RIC at creation time, and
  `sim-a1-interface`'s own mediator actually stores and pushes it. This
  is a genuine gap against a working reference, not just an
  ours-vs-theirs modeling choice — our endpoint's own declared purpose
  (notify on status change) is unmet.~~ — **closed.** `update_policy`
  and `query_policy_status` now call `_notify_policy_status_subscribers`
  whenever a policy's `enforcement_status` actually changes,
  best-effort (same pattern as Policy Mgmt's `CreateIntent`
  notification — an unreachable subscriber never fails the call).
  Filters by `policyIdList`/`policyTypeIdList`/`nearRtRicIdList`.
  Partial: `subscriptionScope`'s `OWN`/`OTHERS` distinction still can't
  be honored — it needs a subscriber identity this build doesn't track
  anywhere (the same elided-AuthZ pattern as `CreatePolicy`'s own
  docstring already calls out), so a scope-only subscription is treated
  as `ALL` rather than silently dropped. Covered by
  `test_update_policy_notifies_matching_subscriber_on_status_change`,
  `test_update_policy_does_not_notify_when_status_unchanged`,
  `test_query_policy_status_notifies_on_refreshed_status_change`,
  `test_notification_is_not_sent_to_subscriber_filtered_out_by_policy_type`,
  `test_notification_delivery_survives_unreachable_subscriber`.
- ~~No service registration/supervision (`/services`, keepalive, and
  auto-delete of a stale rApp's policies).~~ — **closed.** This module's
  own reference clone (`nonrtric-plt-a1policymanagementservice`) has no
  real Java source to ground against (only its OpenAPI spec), but that
  spec (`pms-api-v3.json`'s `ServiceRegistrationInfo`/`ServiceStatus`/
  the `/services*` routes) is itself real, authoritative wire-contract
  content, not invented. Added `PUT /services` (register-or-update,
  same idempotent shape as SME's own `register_service`), `GET
  /services`/`GET /services?serviceId=`, `DELETE /services/{id}`, and
  `PUT /services/{id}/keepalive`. `creator_id` is this build's existing
  identity for "the service that created a policy" (`query_policies`'s
  own docstring already established this equivalence), so unregistering
  a service — or the supervision sweep below — genuinely deletes its
  A1 policies via the same real southbound `a1t.delete_policy` call
  `delete_policy` itself uses, not just a local row drop. No scheduler
  exists anywhere in this build (the same elision already documented for
  DME's producer health), so the reference's own timeout-triggered
  auto-deregistration is enforced lazily: a stale service (past its own
  `keepAliveIntervalSeconds`) is swept the moment `GET /services` next
  reads it, computed live rather than via a periodic poll. Also found
  and fixed while wiring this in: `DateTime(timezone=True)` columns
  round-trip as naive datetimes under SQLite (Postgres returns them
  tz-aware) — the first elapsed-time computation in this build, and
  the first time this particular portability gap was hit; noted in
  `README.md`'s SQLite portability section. Deliberately out of scope:
  the reference's own `RICStatus` callback (notifying a registered
  service's `callbackUrl` of Near-RT RIC availability changes) — this
  build has no concept of RIC availability independent of the single
  A1 mock to trigger it from.
- ~~No duplicate-policy/fingerprint detection — the reference's
  mediator rejects duplicate policy content or a reused id across
  types; ours accepts anything per `policyId` with only an
  empty-object check.~~ — **closed, partially.** Added a real
  content-fingerprint check to `mock-near-rt-ric`'s `create_policy`/
  `update_policy` (ADOPT from the real near-rt-ric-simulator's own
  `calcFingerprint`/`policy_fingerprint` in
  `a1_mediator_controller.py`), scoped per policy type — a second,
  byte-identical `policyObject` under the same type is now genuinely
  `REJECTED`. The reference's other check ("reused id across types")
  doesn't apply to this build: our `policyId` is always freshly
  server-generated, never caller-supplied, so it can never collide
  with an existing one by construction.
- *Confirmed structurally out of scope*: A1TD/A1AP JSON-schema
  validation of `policyObject`, A1-ML (categorically dormant per LLD
  section 0), and real A1AP transport (TLS+mTLS+OAuth2.0+JWT) are all
  already-documented elisions, consistent with the reference's own
  simulator-only intent for some of these.

### NFO + FOCOM (`nfo/`, `focom/`) — vs `pti-o2`, `smo-teiv`

- ~~**No `ResourceType`/`ResourcePool`/`DeploymentManager` schema at
  all** — not just an empty collection behind the documented
  single-cluster limitation, but no model shape to extend later.
  `query_inventory` returns one hardcoded
  `resourcePools: [{resourcePoolId: "pool-0"}]` with nothing behind
  it, vs. the reference's real parent/child resource tree (pserver →
  CPU/RAM/interfaces/PCI/accelerators) typed via a 20-value
  `ResourceTypeEnum`.~~ — **closed.** Added real `ResourceType`,
  `ResourcePool`, `Resource` (with a `parentId` column supporting the
  reference's parent/child tree shape — real hardware telemetry
  populating it stays out of scope, same as elsewhere in this build),
  and `DeploymentManager` tables, lazily seeded with Phase 1's single
  degenerate topology on first read (matching the reference's own
  parent/child resource shape, not the 20-value enum's real hardware
  variety — deliberately out of scope for the same reason).
- ~~No per-resource-type/pool/resource drill-down endpoints — the
  reference exposes `/resourceTypes`, `/resourceTypes/{id}`,
  `/resourcePools/{id}/resources`, `/deploymentManagers/{id}` as
  distinct operations; FOCOM collapses everything into one `/inventory`
  route.~~ — **closed.** Added `GET /resource-types`,
  `GET /resource-types/{id}`, `GET /resource-pools`,
  `GET /resource-pools/{id}`, `GET /resource-pools/{id}/resources`,
  `GET /deployment-managers`, `GET /deployment-managers/{id}` (all 404
  on an unknown id, kebab-case to match this build's route-naming
  convention rather than the reference's camelCase). `provision_resource`/
  `deprovision_resource` now persist/remove real `Resource` rows
  instead of just returning a random UUID and storing nothing, so the
  new drill-down endpoints have real data behind them.
- ~~**`subscribe_inventory_changes` doesn't actually subscribe to
  anything** — it takes no callback parameter, stores nothing, and
  delivers nothing. The reference's `Subscription` model stores a real
  callback + filter and pushes typed create/modify/delete notifications
  on inventory change.~~ — **closed.** Added a real
  `InventorySubscription` model (`callbackUri` + optional
  `resourceTypeId` filter) with `POST`/`DELETE
  /inventory/subscriptions`, and wired best-effort CREATE/DELETE
  delivery into the module's only two mutating endpoints
  (`provision_resource`/`deprovision_resource`) — same pattern as
  A1 Related's `_notify_policy_status_subscribers`. The type-unknown-
  at-delete partial noted here originally is now also resolved: since
  `deprovision_resource` looks up the real `Resource` row before
  deleting it, it notifies with the resource's actual type.
- ~~NFO's deployment state machine is much thinner — reference has 7
  states (including ABNORMAL/UPDATING) plus real duplication/dependency
  guards and a resource-linkage object; ours only moves
  INSTANTIATING→RUNNING with no such guards, and Heal/Scale have no
  state transitions of any kind.~~ — **closed.** Added the reference's
  real 7-state lifecycle (`o2dms/domain/states.py`'s Initial/Installing/
  Installed/Updating/Uninstalling/Abnormal/Deleting, kept under this
  build's own INITIAL/INSTANTIATING/RUNNING/UPDATING/TERMINATING/
  ABNORMAL/DELETING naming), the reference's own duplication/dependency
  guards on Instantiate (`_check_duplication`/`_check_dependencies`,
  `dms_lcm_nfdeployment.py`), a real resource-linkage object
  (`NFOCloudResource`, the reference's `NfOCloudVResource`), and
  Heal/Scale now drive real state transitions instead of being pure
  stubs. Terminate mirrors the reference's own state dispatch exactly,
  including its defensive DELETING→ABNORMAL catch-all for a
  double-terminate race. Heal itself isn't modeled by the reference at
  all (no Heal command exists there) — its transitions are this build's
  own extrapolation to close the stated gap.
- ~~**No topology/entity-relationship export for TEIV at all**~~
  (**closed, partially**: real `GET /topology`, exporting FOCOM's own
  `ResourceType`/`ResourcePool`/`DeploymentManager`/`Resource` rows as
  typed entities/relationships in the reference's own wire shape
  (`o-ran-smo-teiv-cloud:<EntityType>` keys, `{id, attributes}` for
  entities, `{id, aSide, bSide, sourceIds}` for relationships — matching
  `EntityAndRelationshipModel.java`/`TeivIdBuilder.java`), with
  relationships built only from this schema's real foreign keys
  (resource→type, resource→pool, resource→parent) rather than invented
  ones. Deliberately not built: a CloudEvent/Kafka producer — this
  build has no message broker anywhere, and the reference's own export
  is push-based over Kafka, not a pull endpoint at all; `/topology` is
  this build's honest pull-based substitute).
- *Confirmed structurally out of scope*: the real `focom-to-teiv-adapter`
  mechanism (direct kubeconfig access to K8s clusters, CRD reads,
  deriving `OCloudNamespace`/`NodeCluster` entities from live
  `FocomProvisioningRequest`/O2ims Kubernetes CRDs), the Kafka/CloudEvent
  producer noted above, pti-o2's hardware-telemetry watchers, and real
  multi-cluster K8s lifecycle management are correctly excluded from a
  docker-run-based Phase 1.

### AI/ML Workflow (`ai-ml-workflow/`) — vs `aiml-fw-awmf-modelmgmtservice`, `aiml-fw-awmf-tm`, `aiml-fw-athp-sdk-feature-store`, `aiml-fw-athp-tps-kubeflow-adapter`

- ~~No model artifact upload/download or versioning~~ (**closed**: real
  `POST /models/{id}/artifact`/`GET /models/{id}/artifact/{version}`,
  with a real auto-incrementing `artifactVersion` separate from
  `modelVersion`, matching the reference's own `UploadModel`/
  `DownloadModel` shape. Real S3-backed storage is still a deliberate
  elision — the uploaded bytes are stored in a new `ModelArtifact`
  table instead, so upload+download genuinely round-trip;
  `artifact_location` is now actually written by `main.py`).
- ~~Model CRUD is incomplete — no `GET /models/{id}`, no update, no
  delete/deregister; only create and a type-filtered list existed.~~ —
  **closed.** `GET /models/{id}` (previous pass) now has siblings:
  `PUT /models/{id}` (`UpdateModel`, `mmes_apis.go`) 404s on an unknown
  id and 400s (`MODEL_IDENTITY_IMMUTABLE`) on a `modelType`/`version`
  mismatch against the existing record, matching the reference's own
  identity-is-immutable rejection; it updates only the metadata fields
  around that identity (`requiredResourceTypeId`,
  `trainingDataLineage`, `integrityHash`, `clearedNodeGroups`) —
  `state`/`trainingJobId`/`artifactLocation` stay owned by the
  dedicated advance/training/artifact-upload endpoints, not a generic
  PUT. `DELETE /models/{id}` (`DeleteModel`) surfaced the same
  unchecked-FK shape already found and fixed for DME's
  `deregister_producer`: none of `aiml_model`'s five dependent FKs
  (`model_artifact`, `training_job`, `model_change_subscription`,
  `mlmf_subscription`, `inference_job`, transitively
  `performance_report`) had any cascade behavior, so deleting a model
  with dependent rows would orphan them (SQLite, no FK enforcement) or
  crash with an unhandled `IntegrityError` (real Postgres). Fixed the
  same way as DME's fix: `ON DELETE CASCADE` added to every FK (the
  reference's own `DeleteModel`/`repo.Delete` explicitly cleans up its
  one dependent child table, `TargetEnvironment`, before deleting the
  parent — the same defense-in-depth shape, not an invented one) plus
  explicit application-level cleanup in `deregister_model` as a second,
  directly-testable line of defense. Verified the cascade fires for
  real against a local Postgres 16 instance, including the transitive
  `performance_report -> mlmf_subscription -> aiml_model` hop. Delete
  is idempotent on an unknown id, matching this module's other DELETE
  routes (`cancel_training`).
- ~~Registration metadata is thin — no I/O data type schema, no
  author/owner, no `TargetEnvironment` declarations (platform,
  environment type, dependencies) the reference requires.~~ —
  **closed.** Added `description`/`author`/`owner`/`inputDataType`/
  `outputDataType`/`targetEnvironments` to `RegisterModel` and
  `UpdateModel` — the reference's own `ModelRelatedInformation`/
  `ModelInformation`/`Metadata`/`TargetEnvironment` fields
  (`modelInfo.go`), required there but kept optional here since this
  build's own `RegisterModel` was already permissive before this pass.
  `targetEnvironments` is stored as JSON, not a normalized child table
  — registered and read back wholesale, the same adaptation this build
  already uses for SME's `aefProfiles`/`DMEType.collection_spec`.
- ~~`TrainingJob` is far thinner than the reference's real two-axis
  (step × status) tracking — no `run_id`, no distinct
  training/validation dataset fields, no metrics-writeback endpoint, no
  step state machine (DATA_EXTRACTION/TRAINING/TRAINED_MODEL), no
  separate consumer/producer rApp ids. Ours collapses all of this into
  two free-form JSON dicts and a flat status string.~~ — **closed,
  partially.** Added `runId`/`trainingDataset`/`validationDataset`/
  `consumerRappId`/`producerRappId` to `RequestTraining`, exposed on
  `GET .../status`, plus a real metrics-writeback pair
  (`POST`/`GET /training-jobs/{id}/model-metrics`, matching the
  reference's own `POST .../update-model-metrics/<id>`/
  `GET .../get-model-metrics/<id>`, whole-body-replace semantics, not a
  merge). Deliberately **not** adopted: the reference's real two-axis
  step×status tracking (`steps_state`/`TrainingJobStatus`,
  `trainingmgr/models/steps_state.py`) — replacing this build's
  existing flat `status` field with a step state machine would be a
  bigger, riskier rework of already-shipped behavior (every existing
  caller reads/writes a flat `status`), not a purely additive field;
  left for a future pass rather than attempted here.
- ~~No feature-group/feature-store concept exists at all — the reference
  has a first-class `FeatureGroup` entity with its own CRUD and a real
  SDK querying by trainingjob/feature name.~~ — **closed, partially.**
  Added a real `FeatureGroup` entity with `POST`/`GET /feature-groups`
  (the reference's own `CreateFeatureGroup`/`GetFeatureGroup`,
  `featuregroup_controller.py` — its only two routes; the reference
  itself has no delete route either). Faithfully matches the
  reference's own name validation (`\w+`, 3-63 characters) and
  duplicate-name 409. Deliberately **not** adopted: the reference's
  real Cassandra-backed feature store (`aiml-fw-athp-sdk-feature-store`,
  a separate ADOPT-only repo) and its `enableDme`-triggered real DME
  job creation (a raw PUT to
  `data-consumer/v1/info-jobs/{featureGroupName}` on the feature
  group's own host:port) — both are the same no-real-southbound-compute
  elision already documented throughout this build; `enableDme` is
  stored and returned faithfully, just not acted on. The real SDK
  querying by trainingjob/feature name has nothing in this build's own
  scope to attach to (no consumer of `FeatureGroup` data exists here
  either, same as the reference's own separation of concerns).
- ~~No uniqueness/conflict check on `(model_type, version)` — duplicate
  registrations silently succeed where the reference 409s.~~ —
  **closed.** Added a real `UniqueConstraint(model_type, version)` to
  `AIMLModel` (the reference's own `ModelID` composite primary key on
  `(modelName, modelVersion)`, `modelInfo.go`), and `register_model`
  now catches the resulting `IntegrityError` and 409s
  (`MODEL_ALREADY_REGISTERED`), matching the reference's own
  `RegisterModel` (`mmes_apis.go`).
- *Confirmed structurally out of scope*: real Kubeflow/K8s pipeline
  execution, the real Cassandra-backed feature store, and S3 artifact
  storage are total, deliberate elisions consistent with this build's
  no-real-southbound-compute design — `TrainingJob.status` is
  confirmed to be a pure DB flag with no executor behind it anywhere.
  (Also worth noting: MLMF's guard-floor-triggered group retrain, this
  session's own recent addition, is a real feature the AWMF repos don't
  even have.)

### RAN Analytics (`ran-analytics/`) — vs `aiml-fw-apm-influx-wrapper`, `aiml-fw-apm-monitoring-agent`, `aiml-fw-apm-monitoring-server`

- ~~No list/query endpoints for registered producers or active
  subscriptions (`GET /producers`, `GET /subscriptions`) — the
  reference defines these routes (even though its own implementation of
  them is a no-op stub).~~ — **closed.** Added both, filterable by
  `analytics_type` and (`producer_id`/`requested_by` respectively) —
  unlike the reference's stubs, ours actually reads real, persisted
  rows.
- ~~The dead subscriber-notification loop in `publish_report`
  (`for sub in subs: pass`) is real, but not a regression behind the
  reference: `aiml-fw-apm-monitoring-server`'s own `Subscribe` executor
  is equally an empty stub that doesn't even persist a subscription —
  ours was one step ahead (real DB persistence) with the same missing
  last-mile delivery.~~ — **closed.** Added an optional
  `notificationDestination` to `SubscribeAnalytics`/`MDASubscription`
  (same shape as A1 Related's `notification_destination` and Policy
  Mgmt's `notificationCallbackUri`), and `publish_report` now actually
  POSTs the new report to every matching subscriber that registered
  one — best-effort, same pattern as those two: an unreachable
  subscriber never fails the publish that triggered it. A subscription
  that never registered a destination (a purely poll-based consumer,
  the only kind this build had before this pass) is left alone rather
  than guessing a delivery target — the previous docstring's claim
  that `requestedBy` already served as one was never actually true (it
  is a plain identifier used for filtering, e.g. `"sa-smos"`/`"nfo"` in
  this module's own tests, not a callback URL or host).
- **Confirms the Blueprint's "BUILD" verdict directly**: of the three
  repos checked, `aiml-fw-apm-influx-wrapper` and
  `aiml-fw-apm-monitoring-agent` are both genuinely empty (only
  `.gitreview`/`INFO.yaml`, zero source), and
  `aiml-fw-apm-monitoring-server` has real route scaffolding but zero
  working business logic behind any of it. There is very little real
  O-RAN-SC prior art for this module to have been measured against —
  most of the items above are the one real, fixable exception.

## Closed

- **`NFDeploymentDescriptor` population** (§2, was priority 1) — see
  `smo/README.md`'s "Real bugs this pass found" section.
- **Bring the shallow-coverage modules to parity** (§4, was priority 2)
  — so-smos, ran-analytics, and focom now have route-level test
  coverage, not just dispatch/FSM-logic coverage. Writing it surfaced
  and fixed two real bugs: SO SMOS's `CancelOrder` never actually
  persisted (in-place JSON mutation SQLAlchemy never tracks), and RAN
  Analytics' `RegisterAnalyticsProducer` crashed on a legitimate
  re-registration (same shape as the SME bug from the original pass).
  See `smo/README.md`'s "Real bugs this pass found" section. `nfo` was
  separately brought to 5 by the `NFDeploymentDescriptor` fix.
- **Fill the call-flow gaps** (§3, was priority 3) — all six missing
  journeys are now in `smo/docs/call-flows/` (05 through 10); writing
  them is what surfaced the `RECOVER`/cascade-delete-guard/DME-validation
  items closed above, plus the Intent-to-RMIH matching item now in §1.
- **Five §1 design-level decisions, resolved by the stakeholder and
  implemented:**
  - **In-flight training-job upgrade behavior** (`ai-ml-workflow/`) — a
    second `RequestTraining` call on a model already `TRAINING` now
    cancels the orphaned job rather than silently overwriting
    `model.training_job_id`; the operator's new call wins. Also fixed a
    real crash bug found while implementing this: `RequestTraining`
    always fired the `TRAIN` event regardless of model state, which is
    only a legal transition from `REGISTERED` — every ordinary retrain
    (`ACTIVE -> TRAINING`) crashed with an unhandled `IllegalTransition`.
    Now fires `TRAIN` or `RETRAIN` based on the model's actual state.
  - **Intent-to-RMIH matching semantics** (`policy-mgmt/`) — resolved as
    capability-based push: `CreateIntent` takes an `intentType`, matches
    it against each `IntentHandlingFunction`'s
    `intent_handling_capability_list`, and POSTs to the new
    `notificationCallbackUri` (DME's `producerHealthCallbackUrl` pattern)
    of every match. Best-effort — an unreachable RMIH callback never
    fails `CreateIntent` itself.
  - **RAN NF OAM's CM cache sync method** (`ran-nf-oam/`) — confirmed
    NETCONF. `WriteConfigurationChanges`'s per-change dispatch (previously
    elided behind a comment that recorded every sub_change as `APPLIED`
    without dispatching anything) now sends a real NETCONF-shaped
    `<edit-config>` RPC (`netconf_client.py`) over plain HTTP to the ME's
    `O1AdaptorEndpoint.adaptor_uri` — not real SSH/ncclient transport,
    matching this build's all-HTTP-JSON pragmatism everywhere else. An ME
    provisioned for RESTCONF (`ManagedEntity.o1_protocol`) has no
    dispatch implementation yet and is rejected with the (previously
    dormant) `PROTOCOL_NOT_SUPPORTED` rather than silently applied.
    Scoped to the CM-write path only — the separate `cm_schema_cache`
    fetch path is untouched.
  - **SA SMOS `RECONNECT`/`ROLLBACK`** (`sa-smos/`) — split into two
    different problems. `RECONNECT` is now resolved: it reads the
    `AssuranceMonitor`'s `target_order_id` back from SO SMOS's own order
    record to find the completed `DEPLOY` step's `nfDeploymentId`, then
    dispatches to NFO's Heal — no new resource-reference field needed on
    the monitor itself. `ROLLBACK` stays unsupported, but now for a
    concrete, checked reason (new `ROLLBACK_HISTORY_UNAVAILABLE`, 501)
    instead of a generic "ambiguous meaning" refusal: rApp Management's
    own upgrade machinery deletes the prior `RAppInstance` row on a
    successful commit, so there is no version history anywhere in this
    build to roll back to — an rApp Management data-retention gap, not
    an SA SMOS design question. A coordination-group-scoped
    `RECONNECT`/`ROLLBACK` isn't resolved by this pass either — only the
    `targetOrderId` path is; bundled with the still-deferred
    `MLModelCoordinationGroup` × SA SMOS convergence item above.

  See `smo/docs/call-flows/04-closed-loop-assurance.md` for the updated
  RECONNECT/ROLLBACK sequence.
- **Deepen `r1-termination`'s and `mock-near-rt-ric`'s coverage** (§4, was
  priority 2) — both sat at 5 tests, the shallowest tier remaining after
  the previous pass. `r1-termination`'s proxy only ever had GET exercised
  through it, with no assertion on body/header/query-param forwarding or
  on a non-200 upstream response passing through unchanged (only the
  URL-stripping fix and the route table itself were covered); now also
  covers POST body+method forwarding, `Host` header stripped while others
  pass through, a 503 upstream response reaching the caller unchanged,
  the prefix-with-no-trailing-segment edge case, and that `/dme`,
  `/dme-push`, `/dme-pull` route independently rather than colliding.
  `mock-near-rt-ric`'s `UpdatePolicy` (`PUT /a1-p/policies/{id}`) had zero
  test coverage at all — a real, callable route nothing exercised; now
  covered for the enforced/rejected/unknown-id cases, plus idempotent
  delete-of-unknown-id and cross-policy isolation. Both now at 10 tests,
  no real bugs found (the gap was pure absence of tests, not a latent
  defect). 163 tests total, up from 153.
- **Deepen `nfo`'s and `ran-analytics`' coverage** (§4, was priority 2) —
  both sat at 5 tests, the shallowest tier remaining after the previous
  pass. `nfo`'s `query_operation_status` (`GET /operations/{id}`) had
  zero test coverage at all — Instantiate's and Heal/Scale's own
  `LCMOperation` rows were written but never read back through the route
  meant to query them; `query_cluster_placement`'s success path was also
  never asserted, only its deleted-deployment error path. Both now
  covered, plus idempotent `terminate` on an unknown deployment id.
  `ran-analytics`'s `RegisterAnalyticsProducer` never asserted on its own
  SME registration side-effect payload (the `mdaf.<analyticsType>`
  naming, `producerId`, `moduleScope`); `unsubscribe_analytics` on an
  unknown id and `query_analytics_report`'s unfiltered (no
  `analytics_type`) path were both also untested. All now covered,
  including `MDAFReport.scope`, previously never set to a non-`None`
  value in any test. No real bugs found this pass either — same pure
  absence-of-tests shape as the previous coverage pass. Both now at 9
  tests; `focom` (7) is now the shallowest-covered module. 171 tests
  total, up from 163.
- **Deepen `focom`'s coverage** (§4, was priority 2) — sat at 7 tests,
  the shallowest tier remaining after the previous two passes. The
  unfiltered path of `GET /performance` (no `resource_ref`) was never
  exercised, only the filtered case; `GET /inventory`'s default
  `resourceTypeId` fallback (`"generic"`) was asserted nowhere, only the
  explicit-`resource_type` case; `GET /alarms` and `GET /performance`'s
  empty-list responses were never checked; only one alarm was ever
  ingested in any test, never proving a second one doesn't overwrite or
  drop the first; and `deprovision_resource`'s Phase 1 stub behavior
  (succeeds for any `resource_id`, provisioned or not) was only ever
  exercised incidentally through the provision-then-deprovision happy
  path, never asserted directly. All six now covered. No real bugs found
  — same pure absence-of-tests shape as the previous two coverage
  passes. Now at 13 tests; `a1-related` (8) is now the shallowest-covered
  module. 177 tests total, up from 171.
- **Deepen `a1-related`'s coverage** (§4, was priority 2) — sat at 8
  tests, the shallowest tier remaining after the previous three coverage
  passes. Five whole routes had zero test coverage at all: `GET
  /policies/{id}`, `PUT /policies/{id}`, `DELETE /policies/{id}`,
  `DELETE /policies/subscriptions/{id}`, and `DELETE /ei-types/{id}` — by
  far the largest single-pass gap found across all four coverage passes.
  All five now covered, including each delete route's idempotent-on-
  unknown-id case, `DELETE /policies/{id}` actually reaching the (mocked)
  Near-RT RIC via `a1_termination_client` rather than only dropping the
  local mirror row, and `query_policy`'s unguarded-`None` crash on an
  unknown/deleted id explicitly asserted (the same "genuine error path,
  not a friendly 404" pattern `nfo`'s own placement route uses) rather
  than silently avoided. No real bugs found — same pure absence-of-tests
  shape as the previous three coverage passes. Now at 18 tests — the
  largest module by test count after `ran-nf-oam`.
  `onboarding`/`sme`/`rapp-mgmt`/`nfo`/`ran-analytics` (9 tests each) are
  now the shallowest-covered tier. 187 tests total, up from 177.
- **Four more §1 design-level decisions, resolved by the stakeholder and
  implemented:**
  - **rApp-as-producer reconsideration trigger** (`dme/` / `rapp-mgmt/`)
    — resolved as a push: `rapp-mgmt`'s `RAppInstance` FSM now calls a
    new `DELETE /production-capabilities` DME route (keyed by
    `producer_id`, which is the instance's own `oauth_client_id` —
    bootstrap registers with SME/DME under that same identity) whenever
    an instance crashes or terminates, deregistering every DME type it
    produced. Best-effort, same "unreachable callback never fails the
    primary operation" precedent as Policy Mgmt's `CreateIntent`
    dispatch — a DME outage never blocks CRASH/TERMINATE themselves.
    `UPGRADE_COMMIT` is deliberately untouched: the replacement
    instance's own `oauth_client_id` is never set by `start_upgrade` in
    the first place, a separate, pre-existing gap out of this decision's
    scope.
  - **`MLModelCoordinationGroup` × SA SMOS convergence**
    (`ai-ml-workflow/` / `sa-smos/`) — resolved as: retrain is the only
    meaningful remedial action for a coordination group, wired on both
    sides. `report_performance` used to compute `groupRetrainTriggered`
    and stop — nothing ever fired `RETRAIN` on a member model; it now
    fires the same `ACTIVE -> TRAINING` transition `RequestTraining`'s
    own `modelId`-targeted path uses for every currently-`ACTIVE`
    member, creating a per-model `TrainingJob` each. On the SA SMOS
    side, a coordination-group-scoped `AssuranceMonitor`
    (`target_coordination_group_id`) now bypasses `CONFIG_CHANGE`/
    `SCALE`/`RECONNECT`/`ROLLBACK` entirely — those are NF-deployment
    concepts that don't map onto a model group — and always dispatches
    a group retrain via AI/ML Workflow's `RequestTraining` instead,
    regardless of the requested `actionType`.
  - **Shape B / `JOINT_TRAINING`** (`ai-ml-workflow/`) — the original
    v1.3 scope decision (out of scope for this build) is confirmed
    still correct, not just left unrevisited. The schema still permits
    `group_type='JOINT_TRAINING'` for forward compatibility, but no
    code branches on it and none is expected to this phase.
  - **`upgradeTimeoutSeconds` default (300s)** (`rapp-mgmt/`) —
    confirmed as the actual intended default, not a placeholder standing
    in for missing data. The "ungrounded"/"placeholder" language is
    removed from the model column comment and the migration; the value
    itself (300) is unchanged.

  Implementing the second item's test coverage surfaced two more real,
  previously-latent bugs in `report_performance`'s group lookup, neither
  ever caught because no test had exercised that code path before:
  `MLModelCoordinationGroup.member_model_ids.any(model.model_id)` is real
  Postgres `ANY(array)` SQL with no SQLite equivalent under
  `member_model_ids`' JSON fallback (`no such function: ANY`) — replaced
  with an in-Python membership filter; and that filter itself needed a
  string comparison, not `model.model_id in group.member_model_ids`
  directly, since SQLite's JSON fallback has no UUID item type and reads
  `member_model_ids` back as plain strings where Postgres's native
  `ARRAY(Uuid)` round-trips real `uuid.UUID` objects. New tests: `dme`
  (+2), `rapp-mgmt` (+3), `ai-ml-workflow` (+3), `sa-smos` (+2). 197
  tests total, up from 187.
- **Deepen `onboarding`'s coverage** (§4, was priority 2) — the largest
  single-pass coverage gap found yet: 6 of its 8 routes (`GET /packages`,
  `POST .../deprecate`, `POST .../cancel-delete`, `DELETE /packages/{id}`,
  `POST .../usage/start`, `POST .../usage/{id}/stop`) had zero test
  coverage at all, including the cascade-delete guard the module's own
  docstring calls out as important — neither half of it (a blocking
  dependent child package, an active usage registration) had ever been
  exercised. All six now covered, along with `_validate_package`'s real
  validation logic against genuinely malformed zip bytes (every prior
  test mocked the function away entirely) and the `onboarding-status`
  404-shaped-but-actually-409 error path for an unknown package id. No
  real bugs found — same pure absence-of-tests shape as every coverage
  pass so far. Chosen over the three other tied modules
  (`sme`/`nfo`/`ran-analytics`) after a scan of all four route
  inventories found `onboarding` had by far the most uncovered routes.
  Now at 18 tests (was 9, tied for the largest module by test count).
  `nfo`/`ran-analytics`/`sme` (9 tests each) are the new shallowest tier.
  206 tests total, up from 197.
- **Deepen `sme`'s coverage** (§4, was priority 2) — the `nfo`/
  `ran-analytics`/`sme` survey flagged `sme` as the likely candidate,
  since `nfo` and `ran-analytics` were already close to thoroughly
  covered. `DELETE /capif-events/v1/{subscriber}/subscriptions/{id}`
  (`unsubscribe_events`) had zero test coverage at all; `deregister_service`'s
  wrong-producer no-op guard, and `discover_services`' `api_name`/
  `api_version` query filters, were also untested. `notify_service_change`
  — real event-type-filtering and best-effort-delivery logic, gated by
  the same authz check `discover_services` uses — isn't wired into any
  route yet (its own docstring calls this out: "wired in as a
  follow-up"), so it had never been tested at all; now covered directly
  rather than left untested until something calls it. No real bugs
  found — same pure absence-of-tests shape as every coverage pass so
  far. Now at 18 tests (was 9). `nfo`/`ran-analytics` (9 tests each) are
  the new shallowest tier, both already close to thoroughly covered per
  the earlier survey. 215 tests total, up from 206.
- `ran-nf-oam`'s and `a1-related`'s dangling `/health` callbacks (§5)
  closed in one pass, same bug class in both: `subscribe_pm` and
  `register_ei_type` each register a `producerHealthCallbackUrl`
  pointing at their own module's `/health` with DME, and neither
  module answered it. Both now have a `GET /health` route. 217 tests
  total, up from 215.
- `a1-related`'s `SubscribePolicyStatus`/`UnsubscribePolicyStatus`
  no-ops (§5) closed: `update_policy` and `query_policy_status` now
  deliver a best-effort notification to matching subscribers whenever
  a policy's enforcement status actually changes. 222 tests total, up
  from 217 (`a1-related` alone: 19 -> 24).
- `focom`'s `subscribe_inventory_changes` no-op (§5) closed: a real
  `InventorySubscription` model (callback + optional resource-type
  filter) now backs `POST`/`DELETE /inventory/subscriptions`, and
  `provision_resource`/`deprovision_resource` deliver best-effort
  CREATE/DELETE notifications to matching subscribers. Added the
  `inventory_subscription` table to `migrations/001_init.sql`,
  verified against a real local Postgres 16 instance. 228 tests total,
  up from 222 (`focom` alone: 13 -> 19).
- `sme`'s dead `notify_service_change` (§5) closed: `register_service`
  now fires `SERVICE_API_AVAILABLE`/`SERVICE_API_UPDATE` and
  `deregister_service` fires `SERVICE_API_UNAVAILABLE`. Also fixed
  while wiring it in: the function's own claimed authz gate (same one
  `discover_services` uses) was never actually enforced — an
  unauthorized subscriber would have been notified about a service it
  couldn't even discover. 232 tests total, up from 228 (`sme` alone:
  18 -> 22).
- `dme`'s missing GET-by-id/list/query endpoints (§5) closed: added
  `GET /data-jobs/{id}`, `GET /data-jobs/{id}/status`, and
  `GET /offers/{id}` (404 on an unknown id); `discover_dme_types`'
  `data_category` query param now actually filters, against
  `namespace` (the closest concept `DMEType` has to a category — see
  §5 for why). 240 tests total, up from 232 (`dme` alone: 15 -> 23).
- `a1-related`'s missing policy list/query-by-filter endpoint (§5)
  closed: added `GET /policies`, filterable by
  `policy_type_id`/`near_rt_ric_id`/`creator_id`. 245 tests total, up
  from 240 (`a1-related` alone: 24 -> 29).
- `focom`'s missing `ResourceType`/`ResourcePool`/`DeploymentManager`
  schema and drill-down endpoints (§5) closed: added the four tables
  (with `Resource.parentId` for the reference's parent/child shape,
  no real telemetry behind it), lazily seeded with Phase 1's single
  degenerate topology, plus `GET /resource-types`(`/{id}`),
  `GET /resource-pools`(`/{id}`, `/{id}/resources`), and
  `GET /deployment-managers`(`/{id}`). `provision_resource`/
  `deprovision_resource` now persist/remove real `Resource` rows
  instead of a stub UUID — also resolving the earlier partial in
  `subscribe_inventory_changes`'s notification delivery (deprovision
  can now notify with the resource's real type). Added the four
  tables to `migrations/001_init.sql`, verified against a real local
  Postgres 16 instance. 260 tests total, up from 245 (`focom` alone:
  19 -> 34).
- `ai-ml-workflow`'s missing `GET /models/{id}` (§5) closed: 404s on
  an unknown id. Update/delete/deregister remain open — a distinct,
  larger CRUD-completeness gap, not part of this GET-by-id theme.
  262 tests total, up from 260 (`ai-ml-workflow` alone: 18 -> 20).
- `ran-analytics`'s missing `GET /producers`/`GET /subscriptions` (§5)
  closed: both filterable, unlike the reference's own no-op stub
  implementations of the same two routes. This was §5's last
  GET-by-id/list/query item across every audited module. 270 tests
  total, up from 262 (`ran-analytics` alone: 9 -> 17).
- `ran-nf-oam`'s missing standard alarm fault fields (§5) closed:
  added `probableCause`/`specificProblem`/`rootCauseIndicator`/
  `correlatedNotifications`/`proposedRepairActions` to `Alarm`, wired
  through `ingest_alarm`, verified against a real local Postgres 16
  instance. 274 tests total, up from 270 (`ran-nf-oam` alone: 21 -> 25).
- `ran-nf-oam`'s missing alarm-cleared lifecycle (§5) closed: added
  `PATCH /alarms/{id}/clear`, setting `severity` to the already-valid
  `'cleared'` value (matching the reference's own
  `perceivedSeverity=CLEARED` shape) plus `clearedAt`/`clearUserId`
  metadata. Verified against a real local Postgres 16 instance. 277
  tests total, up from 274 (`ran-nf-oam` alone: 25 -> 28).
- `onboarding`'s missing package-level priming stage (§5) closed,
  partially: added real `PRIMING`/`PRIMED`/`DEPRIMING` states and
  `POST /packages/{id}/prime`/`POST /packages/{id}/deprime`, with
  `deprime` genuinely blocked by active usage registrations and
  `DELETE` having no edge from `PRIMED` at all (matches the
  reference's own guards). Deliberately not changed:
  `rapp-mgmt`'s `CreateInstance` still gates on `AVAILABLE`, not
  `PRIMED` — an already-confirmed design decision (D-SEC-RAPP-1), not
  overridden by this pass. Verified against a real local Postgres 16
  instance. 285 tests total, up from 277 (`onboarding` alone: 18 -> 26).
- `ai-ml-workflow`'s missing model artifact upload/download and
  versioning (§5) closed, as an honest Phase-1 stand-in: real S3-backed
  storage stays a total, deliberate elision (as before), so a new
  `ModelArtifact` table stores the actual uploaded bytes in-DB instead —
  `POST /models/{id}/artifact` and `GET /models/{id}/artifact/{version}`
  now genuinely round-trip, with `artifactVersion` a real
  auto-incrementing counter per model, distinct from `modelVersion`
  (matching the reference's own `UploadModel`/`DownloadModel` shape),
  and `artifact_location` is now actually written by `main.py` instead
  of sitting unused. Added the table to `migrations/001_init.sql`,
  verified against a real local Postgres 16 instance. 291 tests total,
  up from 285 (`ai-ml-workflow` alone: 20 -> 26).
- `focom`'s missing TEIV topology export (§5) closed, partially: added
  real `GET /topology`, a typed entity/relationship export of FOCOM's
  own `ResourceType`/`ResourcePool`/`DeploymentManager`/`Resource` rows
  in the reference's own wire shape, with relationships built only from
  this schema's real foreign keys. No new table needed — it's a pure
  read-only projection of already-existing rows. Deliberately not
  built: a CloudEvent/Kafka producer, since this build has no message
  broker anywhere and the reference's own export is push-based, not a
  pull endpoint — `/topology` is the honest substitute. 294 tests
  total, up from 291 (`focom` alone: 34 -> 37).
- `nfo`'s thin deployment state machine (§5) closed: added the
  reference's real 7-state lifecycle (statemachine.py), the reference's
  own duplication/dependency guards on Instantiate, a real
  resource-linkage object (`NFOCloudResource`), and real Heal/Scale
  state transitions in place of pure stubs. `NFDeployment` gained a
  real `name` column the duplication guard needs; the three real
  cross-module callers of NFO's Instantiate (`rapp-mgmt`, `so-smos`'s
  dispatch table, the cross-service integration test) were updated to
  pass one. Verified against a real local Postgres 16 instance. 308
  tests total, up from 294 (`nfo` alone: 9 -> 23).
- `dme`'s `producerHealthCallbackUrl` gap (§5) closed: `typeStatus` now
  genuinely calls the registered callback (ICS's own
  `ConsumerController.typeStatus`/`ProducerSupervision` health signal)
  instead of trusting whether a `DataJob` row happened to be `ACTIVE` —
  a dead producer with an active job no longer reports `ENABLED`.
  Computed live at read time (`GET /dme-types`) rather than via a
  periodic background poll, since no scheduler exists anywhere in this
  build. 310 tests total, up from 308 (`dme` alone: 23 -> 25).
- `dme`'s missing job push to producers (§5) closed: added a real
  `jobCallbackUrl` field to `DMEType` registration (ICS's own
  `InfoProducer.jobCallbackUrl`, distinct from the health-supervision
  URL), and `create_data_job`/`terminate_data_job` now genuinely
  POST/DELETE to it (ICS's own `ProducerCallbacks.startInfoJob`/
  `stopInfoJob`, matching `ProducerJobInfo`'s wire shape), best-effort.
  Also closed the producer-side half of this for both real producers
  in this build: `ran-nf-oam` and `a1-related` now answer `/dme-jobs`
  (the URL they themselves register with DME), the same
  dangling-callback bug class already fixed for `/health`. Verified
  against a real local Postgres 16 instance. 318 tests total, up from
  310 (`dme` alone: 25 -> 31, `ran-nf-oam` alone: 28 -> 29,
  `a1-related` alone: 29 -> 30).
- `ai-ml-workflow`'s missing `(model_type, version)` uniqueness check
  (§5) closed: added a real `UniqueConstraint` to `AIMLModel` (the
  reference's own `ModelID` composite primary key on
  `(modelName, modelVersion)`), and `register_model` now 409s
  (`MODEL_ALREADY_REGISTERED`) on a duplicate instead of silently
  creating a second, indistinguishable row — matching the reference's
  own `RegisterModel`. Verified against a real local Postgres 16
  instance. 321 tests total, up from 318 (`ai-ml-workflow` alone:
  26 -> 29).
- `dme`'s missing producer-status endpoint (§5) closed: added
  `GET /production-capabilities/{producer_id}/status`, reusing the
  same live health-check signal `typeStatus` already uses (ICS's own
  `ProducerController.getInfoProducerStatus`/`ProducerStatusInfo`).
  325 tests total, up from 321 (`dme` alone: 31 -> 35).
- `dme`'s `deregister_producer` cascade gap (§5) closed: added a real
  `ON DELETE CASCADE` to `data_job`/`data_offer`'s `dme_type_id` FKs
  (matching `dme_delivery_schema`'s own already-cascading one — neither
  had it before, so a `DMEType` with an existing `DataJob`/`DataOffer`
  either silently orphaned the rows under SQLite or crashed with an
  unhandled `IntegrityError` on real Postgres), plus explicit
  application-level cleanup in `deregister_producer` itself. Verified
  the cascade fires for real against a local Postgres 16 instance. 326
  tests total, up from 325 (`dme` alone: 35 -> 36).
- `a1-related`'s missing duplicate-policy detection (§5) closed,
  partially: added a real content-fingerprint check to
  `mock-near-rt-ric`'s `create_policy`/`update_policy` (ADOPT from the
  real near-rt-ric-simulator's own `calcFingerprint`/
  `policy_fingerprint`), scoped per policy type. The reference's other
  check ("reused id across types") doesn't apply here — this build's
  `policyId` is always freshly server-generated, never caller-supplied.
  332 tests total, up from 326 (`mock-near-rt-ric` alone: 10 -> 16).
- `ai-ml-workflow`'s model CRUD's last two gaps (§5) closed: added
  `PUT`/`DELETE /models/{id}`. Delete surfaced the same unchecked-FK
  shape already found and fixed for DME's `deregister_producer` — none
  of `aiml_model`'s five dependent FKs had cascade behavior — fixed the
  same way (`ON DELETE CASCADE` plus explicit application-level
  cleanup), verified against a real local Postgres 16 instance
  including the transitive `performance_report -> mlmf_subscription ->
  aiml_model` hop. 338 tests total, up from 332 (`ai-ml-workflow`
  alone: 29 -> 35).
- `ran-analytics`'s dead subscriber-notification loop in
  `publish_report` (§5) closed: added an optional
  `notificationDestination` to `SubscribeAnalytics`/`MDASubscription`
  (same shape as A1 Related's/Policy Mgmt's own subscription
  callbacks), and a published report is now best-effort POSTed to
  every matching subscriber that registered one. 343 tests total, up
  from 338 (`ran-analytics` alone: 17 -> 22).
- DME's missing update-in-place semantics (§5) closed: added
  `PUT /data-jobs/{id}` (ICS's own `PutIndividualInfoJob`). This
  build's `dataJobId` is server-generated (unlike ICS's caller-supplied
  `jobId`), so the endpoint only ever updates an existing job (404 on
  an unknown id); `dmeTypeId`/`consumerId`/`dataDeliveryMode` stay
  immutable (`DATA_JOB_TARGET_IMMUTABLE`, 400), matching ICS's own
  "cannot modify job type" rejection, and a successful update
  re-pushes the job to the producer, matching ICS's own PUT behavior.
  348 tests total, up from 343 (`dme` alone: 36 -> 41).
- DME's missing type-subscription mechanism (§5) closed: added
  `POST`/`GET`/`DELETE /type-subscriptions` and
  `GET /type-subscriptions/{id}` (ICS's own `/info-type-subscription`).
  `register_dme_type`/`deregister_producer` now best-effort notify
  every subscriber on any type registration/removal
  (`ConsumerCallbacks.notifyTypeRegistered`/`notifyTypeRemoved`),
  unfiltered, matching the reference's own lack of per-type scoping.
  357 tests total, up from 348 (`dme` alone: 41 -> 50).
- DME's missing job-definition schema validation (§5) closed: adopted
  the `jsonschema` library (ICS's own real JSON Schema validation,
  `org.everit.json.schema`) in `create_data_job`/`update_data_job` — a
  `productionJobDefinition` that doesn't validate against its
  `DmeType`'s registered `dataProductionSchema` is now rejected
  (`SCHEMA_VALIDATION_FAILED`, 422) instead of accepted as an arbitrary
  dict. 362 tests total, up from 357 (`dme` alone: 50 -> 55).
- Onboarding's much-thinner package validation (§5) closed, partially:
  added the reference's `.csar` filename convention check and its
  required `Definitions/acm_composition.json` file check, plus
  duplicate-package detection keyed on this build's own already-
  computed `integrity_hash` (adapted from the reference's ASD-
  descriptor-id uniqueness check, since this build has no ASD
  descriptor concept). All three route to `FAILED`, matching
  `OnboardPackage`'s existing async-contract shape. The reference's
  real ASD descriptor parsing stays out of scope — the same elision
  already documented for `RappInstance`'s nested ACM/SME/DME resource
  records. 366 tests total, up from 362 (`onboarding` alone: 26 -> 30).
- SME's type-only event subscription filtering (§5) closed, partially:
  added `apiIds` to `SubscribeEvents`/`ServiceEventSubscription` (the
  reference's own `CAPIFEventFilter.apiIds`) — a subscription scoped to
  specific `apiId`s is no longer notified about other services'
  events. `apiInvokerId`/`aefId` filters stay unimplemented for a
  concrete reason (no invoker-onboarding events, no `aefProfiles`
  concept), not dropped silently. 368 tests total, up from 366 (`sme`
  alone: 22 -> 24).
- SME's flattened `ServiceProfile` (§5) closed: added real
  `aefProfiles`/`apiSuppFeats`/`shareableInfo` fields (the reference's
  own `ServiceAPIDescription` fields), stored and read back wholesale
  as JSON rather than normalized child tables — the same adaptation
  already used elsewhere in this build for data that's registered and
  queried as one unit, never independently CRUD'd. `discover_services`'
  own filtering thinness (§5) closed alongside it, partially: `aefId`/
  `protocol`/`dataFormat`/`commType` are now real filters walking the
  new field; `category` stays unfiltered since this build has no
  category concept on `ServiceProfile` at all. 373 tests total, up
  from 368 (`sme` alone: 24 -> 29).
- AI/ML Workflow's thin registration metadata (§5) closed: added
  `description`/`author`/`owner`/`inputDataType`/`outputDataType`/
  `targetEnvironments` to `RegisterModel`/`UpdateModel` (the
  reference's own `ModelRelatedInformation`/`ModelInformation`/
  `Metadata`/`TargetEnvironment` fields, `modelInfo.go`) — required
  there, kept optional here since this build's own `RegisterModel` was
  already permissive. 376 tests total, up from 373 (`ai-ml-workflow`
  alone: 35 -> 38).
- AI/ML Workflow's thin `TrainingJob` (§5) closed, partially: added
  `runId`/`trainingDataset`/`validationDataset`/`consumerRappId`/
  `producerRappId` to `RequestTraining`, plus a real metrics-writeback
  pair (`POST`/`GET /training-jobs/{id}/model-metrics`, matching the
  reference's own `update-model-metrics`/`get-model-metrics` routes).
  The reference's real two-axis step×status tracking stays deliberately
  unadopted — replacing this build's existing flat `status` field with
  a step state machine is a bigger rework of already-shipped behavior,
  not a purely additive field. 380 tests total, up from 376
  (`ai-ml-workflow` alone: 38 -> 42).
- AI/ML Workflow's missing feature-group/feature-store concept (§5)
  closed, partially: added a real `FeatureGroup` entity with
  `POST`/`GET /feature-groups` (the reference's own `CreateFeatureGroup`/
  `GetFeatureGroup` — its only two routes). Real Cassandra-backed
  feature storage and the reference's `enableDme`-triggered real DME
  job creation stay deliberately unadopted — the same
  no-real-southbound-compute elision already documented throughout
  this build; `enableDme` is stored and returned faithfully, just not
  acted on. 387 tests total, up from 380 (`ai-ml-workflow` alone: 42 ->
  49).
- A1 Related's missing policy-type detail retrieval (§5) closed,
  partially: added `GET /policy-types/{id}` (the reference's own
  `GetPolicyTypeDefinition`), 404 on an unknown type, else a real
  `PolicyTypeObject`. `policySchema` is an honest empty placeholder
  (`{"type": "object"}`), not a fabricated A1TD schema this build was
  never given. `KNOWN_POLICY_TYPES` never being sourced from a real
  RIC, and the reference's separate RIC repository (`GET /rics`), stay
  out of scope — this build models no near-RT-RIC entity or inventory
  beyond the single A1 mock. 389 tests total, up from 387 (`a1-related`
  alone: 30 -> 32).
- rApp Management's missing standalone delete-after-undeploy (§5)
  closed: adopted the reference's own `undeployRappInstance`/
  `deleteRappInstance` split (DEPLOYED -> UNDEPLOYING -> UNDEPLOYED,
  delete only legal from UNDEPLOYED) — `TERMINATE` now lands in a
  terminal `UNDEPLOYED` state (replacing the old `TERMINATING` name)
  with the instance row still present, and a new
  `DELETE /instances/{id}` removes it, 409'ing
  (`RAPP_INSTANCE_NOT_UNDEPLOYED`) otherwise. Also found and fixed:
  `rapp_fault_report`/`rapp_performance_report` had no `ON DELETE
  CASCADE` on their `instance_id` FK — same bug class as DME's
  `deregister_producer`/AI-ML Workflow's `deregister_model` — fixed
  with both a DB-level cascade and explicit application cleanup,
  verified against real Postgres. `CreateInstance`'s already-shipped
  immediate-deploy behavior (D-SEC-RAPP-1) is unrelated and untouched.
  394 tests total, up from 389 (`rapp-mgmt` alone: 12 -> 17).
- A1 Related's missing service registration/supervision (§5) closed:
  added `PUT`/`GET /services`, `DELETE /services/{id}`, and
  `PUT /services/{id}/keepalive` (the reference's own `pms-api-v3.json`
  OpenAPI contract — no real Java source exists in this module's
  reference clone to ground against, but the spec itself is real,
  authoritative content). Unregistering a service, or the lazy
  keepalive-timeout sweep (no scheduler exists anywhere in this build,
  so staleness is computed live at `GET /services` read time, the same
  pattern as DME's producer health), genuinely deletes that service's
  A1 policies via the same real southbound call `delete_policy` itself
  uses. The reference's own `RICStatus` callback stays out of scope —
  this build has no RIC-availability concept independent of the single
  A1 mock. Also fixed: a real SQLite/Postgres `DateTime(timezone=True)`
  portability gap (naive vs. tz-aware datetimes), the first one this
  build's first elapsed-time computation ever hit. 405 tests total, up
  from 394 (`a1-related` alone: 32 -> 43).
- rApp Management's missing resource-provenance detail endpoints (§5)
  closed, partially: added `GET /instances/{id}` — previously not even
  a single-instance detail read existed at all, only the list route and
  single-field sub-resources (`config`). Genuinely exposes
  `workloadRef` (the real NFO `nfDeploymentId` `CreateInstance` received
  back) and the caller-supplied `configuration`. The reference's own
  nested ACM/SME/DME resource records stay out of scope, unchanged:
  they're the caller-supplied deploy descriptor `CreateInstance` never
  accepts in the first place — echoing them back would mean inventing
  descriptor data, not exposing something this build already computes.
  407 tests total, up from 405 (`rapp-mgmt` alone: 17 -> 19).
- SME's `register_service` accepting any `apf_id` (§5) closed for real,
  on a second look: added a minimal, real Provider (APF) enrolment
  registry (`ProviderRegistration`, `POST`/`DELETE
  /provider-registrations`, the reference's own `providermanagement.go`)
  scoped to this build's own flattened `apf_id` identity, not the
  reference's full provider-domain/APF-AEF-AMF hierarchy. `register_service`
  and `query_own_services` now both enforce the reference's own real
  gate (`IsPublishingFunctionRegistered`) — 403/404 for an unenrolled
  `apf_id`. This changes an already-shipped route's contract: RAN
  Analytics' `register_analytics_producer` (SME's only cross-module
  caller) now enrols before publishing, verified against the real
  cross-service integration suite, not just mocks. An earlier pass on
  this same item stopped short, reasoning the fix would "un-declare an
  explicit architectural boundary" — revisited and built once actually
  attempted, since the minimal `apf_id`-only registry needed turned out
  smaller than that earlier assessment feared. 415 tests total, up from
  407 (`sme` alone: 29 -> 37).
- With §5 fully closed, moved to §2: **no persisted OpenAPI spec files
  anywhere** closed. Added `docs/openapi/<module>.json` for all fourteen
  modules plus the mock, generated from each app's own real
  `app.openapi()` output by the new `scripts/generate_openapi_specs.py`,
  with a new CI-enforced drift check
  (`tests_integration/test_openapi_specs.py`). Writing that test
  surfaced a real bug: `r1-termination`'s catch-all proxy route got a
  non-deterministic `operationId` from FastAPI's own
  `generate_unique_id()` (`list(a_set)[0]`, `PYTHONHASHSEED`-dependent)
  — fixed with an explicit `operation_id`. Also struck a stale bullet
  found while in this section: "NFO's Heal/Scale operations are stubs"
  had already been closed by the NFO+FOCOM §5 pass and was never marked
  as such. 417 tests total, up from 415 (2 new integration tests; no
  module's unit count changed).
- Per the user's explicit direction to revisit previously-declared
  Phase-1 boundaries rather than stop, closed §2's "no real OAuth2/token
  enforcement at R1 Termination," partially: a real, minimal API Invoker
  registry (`InvokerRegistration`) plus a real `POST /oauth2/token`
  (client_credentials, genuinely checked secret) and `POST
  /oauth2/introspect` (RFC 7662 — the honest substitute for the
  reference's own externally-signed-JWT/Keycloak validation, an external
  IdP this build doesn't run). R1 Termination's gateway now genuinely
  enforces this on every proxied request, failing closed if SME is
  unreachable; the cross-service integration suite is unaffected since
  it deliberately bypasses R1 Termination's own proxy mechanics. Also
  extracted `smo_shared/timeutil.py`'s `as_utc()` from A1 Related's own
  local copy, now needed a second time for `IssuedAccessToken`'s expiry
  check. A GitHub Advanced Security review on the PR then caught a real
  finding before merge: `onboarding_secret` and `access_token` were both
  stored in cleartext — a DB leak (backup, SQL injection elsewhere, a
  dump) would have handed out reusable client credentials and live
  session tokens directly. Fixed: `InvokerRegistration` now stores only
  a salted `scrypt` hash (`onboarding_secret_hash`, stdlib `hashlib`, no
  new dependency), and `IssuedAccessToken` stores only a SHA-256 hash of
  the token (`access_token_hash`) — the raw token is returned to the
  caller once at issuance and never persisted. 432 tests total, up from
  417 (`sme` alone: 37 -> 47; `r1-termination` alone: 10 -> 15).
- Continuing to revisit previously-declared Phase-1 boundaries per
  explicit direction, closed §2's "no real southbound integrations
  beyond the A1 mock" for its O1 Adaptor half: a new `mock-o1-adaptor`
  module (mirroring `mock-near-rt-ric`'s own minimal scope) answers RAN
  NF OAM's real RFC 6241 `<edit-config>` RPC for real, proven by two new
  cross-service integration tests. Writing them surfaced a real,
  separate bug in the integration test harness itself:
  `tests_integration/mesh.py`'s `dispatch()` only ever forwarded a JSON
  body, silently dropping `content=` (needed for `netconf_client.py`'s
  raw XML POST, the first non-JSON caller this harness ever had) —
  fixed. 439 tests total, up from 432 (`mock-o1-adaptor`: new module, 5
  tests; integration suite 12 -> 14).
- A GitHub Advanced Security (CodeQL) review on that PR caught a real
  finding before merge: `mock-o1-adaptor`'s `/edit-config` parsed an
  attacker-reachable HTTP body with stdlib `xml.etree.ElementTree`,
  vulnerable to XML internal entity expansion (CWE-611). Fixed by
  switching to `defusedxml.ElementTree` there, plus the same fix in
  `netconf_client.py`'s reply parsing (the identical vulnerability
  class at the same protocol boundary, not itself CodeQL-flagged since
  it predates this PR's diff, but fixed for consistency — both already
  mirror each other's namespace-stripping technique). Added a
  regression test proving entity expansion is rejected, not parsed.
  440 tests total, up from 439 (`mock-o1-adaptor`: 5 -> 6).
- Continuing to revisit previously-declared Phase-1 boundaries per
  explicit direction, closed §2's "RAN NF OAM's MnS Registry discovery
  is a heartbeat-aging stub" — partially: real MnS Registry NRM polling
  stays out of scope (no such registry exists in this build), but
  staleness is now computed live at `write_configuration_changes`'s own
  gate, not only via the separate `/discover` sweep — the same "no
  scheduler exists anywhere in this build" pattern as DME's producer
  health and A1 Related's service supervision. Writing real tests for
  this route (previously zero) surfaced the third occurrence of the
  naive-vs-aware `DateTime(timezone=True)` SQLite portability gap
  (first hit by A1 Related, then SME) — fixed with the same
  `smo_shared.timeutil.as_utc` helper. 445 tests total, up from 440
  (`ran-nf-oam` alone: 29 -> 34).
- Continuing per explicit direction to revisit "other items like that",
  closed §2's "FOCOM's hardcoded single-cluster stub" — partially: the
  single-cluster topology itself stays Phase 1's declared scope, but
  `GET /inventory` (the one route NFO's real Instantiate call actually
  depends on) was still a hardcoded literal never touching the real
  `ResourceType`/`ResourcePool`/`DeploymentManager` schema the §5 pass
  gave every drill-down route — now sourced from the same seeded row.
  446 tests total, up from 445 (`focom` alone: 37 -> 38).
- Continuing "other items like that": §2's "the full `docker-compose`
  stack has never been run end-to-end" stays genuinely out of scope (no
  Docker daemon in this build's sandbox or its own CI runners), but the
  narrower piece — `docker compose config` YAML validation — used to be
  purely manual, an agent remembering to run it by hand after touching
  the compose file. A new `docker-compose-config` CI job now runs it
  automatically on every push/PR (no daemon required, confirmed: it
  parses and renders the file without needing one), the same drift-check
  philosophy already used for the OpenAPI specs. Also fixed the bullet's
  stale service count (15 -> 17, `mock-o1-adaptor` and others had been
  added since it was last written). No test-count change (a CI-only
  addition, not a pytest one) — still 446 tests.
- Continuing "other items like that": closed §2's own documented root
  cause for the two real `rapp_instance` Postgres-schema bugs found
  earlier — "the migration-Postgres CI job only checks table *count*,
  not columns." Added `scripts/check_migration_matches_models.py`
  (loads every module's ORM models onto the shared `Base`, compares
  column presence + nullability against the live Postgres schema after
  the real migration is applied) and wired it into the
  `migration-postgres` CI job. Verified against a real local Postgres
  16 instance by reproducing both original bugs directly (dropped
  `pending_upgrade_instance_id`, re-added `oauth_client_id`'s `NOT
  NULL`) and confirming the script fails with the exact error for each,
  then restoring a clean migration and confirming it passes (58 tables,
  0 mismatches). No pytest changes — still 446 tests.
- Per explicit direction, moved the repo root's seven spec directories
  (`5G_APIs/`, `O-RAN-WG4-MP-YANGs/`, `O-RAN-WG5-O-CU-MP-YANGs/`,
  `O-RAN-WG5-O-DU-MP-YANGs/`, `O-RAN-WG10-IMDM-YANGs/`,
  `O-RAN-WG10-O1NRM-YANGs/`, `o-cloud-im/`) into a new `specs/` folder
  parallel to `smo/`, with `specs/README.md` cataloging what's there and
  which files are relevant to which `smo/` module (notably: ~95
  `TS28xxx` 3GPP management-plane specs out of `5G_APIs/`'s ~540 total,
  and `o-cloud-im/resources/ORAN.O2ims.*.yaml` — the real O2IMS spec
  FOCOM's inventory routes have so far only been audited against
  `pti-o2`'s Python implementation of, never the formal spec itself).
  No code changes; groundwork for the next item, not a closure.
- Per explicit direction, ran the first of the two planned audits: `smo/`
  against the formal specs now in `specs/` — RAN NF OAM vs.
  MsacNrm/FaultNrm/ProvMnS/PerfMeasJobCtrlMnS + O1NRM YANGs, FOCOM vs.
  the real O2IMS data model, Policy Mgmt vs. IntentNrm, and SME vs.
  CAPIF core's real security-service source (the closest thing to a
  formal spec available for it, since no CAPIF OpenAPI file lives in
  `specs/`). Full findings in the new `smo/SPEC_AUDIT.md` — not
  reproduced here in full; highlights: RAN NF OAM's MSAC gate is
  confirmed a placeholder vs. the spec's real per-data-node RBAC engine
  (large, deliberate); FOCOM's `ResourceType`/`ResourcePool`/
  `DeploymentManager` are each missing several real, small-to-add
  fields; Policy Mgmt's Intent-to-RMIH matching field is wrong per the
  spec (matches on an invented `intentType` string instead of the
  spec's real `supportedExpectationObjectType` enum), and the spec
  itself suggests a different, consumer-selects-by-DN architecture
  than the push-notify mechanism already built; SME's invoker
  onboarding has the trust direction backwards vs. real CAPIF (client
  supplies its own `apiInvokerId`/secret rather than the CAPIF core
  generating and returning them), and the real CAPIF core's separate
  "Trusted Invokers" per-AEF authorization registry has no equivalent
  at all here — both newly documented, not previously known. Not yet
  audited against a formal spec (no directly relevant one lives in
  `specs/`): DME, A1 Related, Onboarding/rApp Mgmt, AI/ML Workflow, RAN
  Analytics — those stay grounded only against §5's source-code audits.
  No code changes in this pass — `SPEC_AUDIT.md` itself names which
  findings are small/closeable-now vs. moderate/breaking vs.
  large/structural-and-deliberate.
- Ran the second audit: a fresh pass against the O-RAN-SC Repo
  Blueprint's 18 shortlisted repos, focused on what changed since §5
  finished (OAuth2, the O1 mock, heartbeat-aging, FOCOM's inventory
  wiring) rather than re-deriving §5 from scratch. SME's own real
  findings (documented above) were the substantive result — grepped
  `oam`/`smo-o1`/`sim-o1-interface`'s source for a real MnS Registry
  discovery/registration protocol to further ground RAN NF OAM's
  heartbeat-aging elision against: none exists, confirming that elision
  stays correctly scoped, not a missed closeable gap. Same for FOCOM's
  `/inventory`: `pti-o2` has no combined inventory endpoint to ground
  it against beyond what the O2IMS spec audit already found.
- **Per explicit direction, prepared a real pilot-demo artifact and
  runbook** rather than more spec-gap closures (the user's own call:
  only close SPEC_AUDIT.md's gaps if one turns out to be demo-blocking
  — none were, one different real bug was found instead). Adapted the
  real O-RAN-SC reference's own sample package
  (`nonrtric-plt-rappmanager/sample-rapp-generator/rapp-all`) into
  `smo/samples/hello-world-rapp/` (+ `build_csar.py`, producing the
  committed `hello-world-rapp.csar`) — a real, valid CSAR for this
  build's own `Onboarding` validator, with real SME/DME registration
  bodies matching this build's actual request shapes. Doing this
  surfaced a real, previously undetected bug:
  `_validate_package`'s required-file check used
  `Definitions/acm_composition.json`, a path that doesn't exist
  anywhere in the real reference — the actual constant
  (`RappCsarPathProvider.ACM_COMPOSITION_JSON_LOCATION`,
  `FileExistenceValidator.java`) is
  `Files/Acm/definition/compositions.json`. This build's own test
  fixture had independently guessed the same wrong path, so the tests
  agreed with the implementation and neither ever caught it — only
  checking against the real reference's sample package did. Fixed both
  the validator and the fixture. `smo/DEMO_RUNBOOK.md` now documents
  the full onboard → deploy → bootstrap (SME/DME registration, OAuth2)
  → operate → retire sequence with real, copy-pasteable commands
  against a live `docker compose up` (this build's own sandbox still
  cannot run one — the runbook is explicit about that and about why
  every command routes through `docker compose exec r1-termination`,
  since only that service publishes a host port). Two new permanent
  integration tests prove the whole sequence for real:
  `test_real_demo_csar_onboards_and_deploys` (the CSAR alone) and
  `test_full_runbook_sequence_succeeds` (every single runbook command,
  in order). 448 tests total, up from 446 (`tests_integration`: 14 -> 16).
- **Started closing `SPEC_AUDIT.md`'s small/closeable gaps, per explicit
  direction** (the user's own call: no demo-blocking gap turned up, so
  work through the list directly). First: RAN NF OAM's `Alarm` model
  was missing `alarmType` (`TS28111_FaultNrm.yaml`'s closed 11-value
  enum, real Postgres `CHECK` constraint added and verified to actually
  reject an invalid value against a real local instance) and
  `ackUserId`/`alarmChangedTime` (who acknowledged an alarm, and the
  spec's own "last mutated" timestamp — neither previously recorded;
  `changed_at` now updates on both `PATCH /alarms/{id}/ack` and
  `PATCH /alarms/{id}/clear`, the two places this build actually
  mutates an existing alarm). Verified against a real local Postgres 16
  instance (migration applies cleanly, `check_migration_matches_models.py`
  passes, 58 tables). 449 tests total, up from 448 (`ran-nf-oam` alone:
  34 -> 35).
- **`SPEC_AUDIT.md` item 3: RAN NF OAM's `WriteConfigSubChange` had no
  operation-type field at all** — every write was implicitly a merge,
  with no create/delete/replace equivalent anywhere in `main.py` or
  `netconf_client.py`, even though `TS28532_ProvMnS.yaml` defines four
  distinct MOI lifecycle operations. Grounded in RFC 6241 section 7.2's
  real edit-config `operation` attribute (merge/replace/create/delete/
  remove) — this build's actually-implemented southbound protocol, per
  `netconf_client.py`'s own docstring — rather than ProvMnS's
  HTTP-verb-level framing, since that's the more precise match for what
  this build really dispatches. `operation` defaults to `"merge"`
  everywhere (real Postgres `CHECK` constraint for the 5 RFC values,
  verified to reject an invalid value against a real local instance),
  so every existing caller is unaffected. `netconf_client.py` now emits
  it as an attribute on the `<managed-object>` node itself (the RFC's
  real placement — the node the operation applies to, not
  `<edit-config>`). `mock-o1-adaptor`'s `edit_config` handler previously
  rejected any empty `attribute_changes` payload unconditionally; a
  real `delete`/`remove` legitimately carries none, so that would have
  wrongly rejected a legitimate delete — now only rejected for the
  other operations, and a delete/remove clears (rather than overwrites)
  the mock's own `_applied_changes` record for that ref. Verified
  against a real local Postgres 16 instance (migration applies cleanly,
  `check_migration_matches_models.py` passes, 58 tables; a manual
  `psql` insert confirmed the `operation` `CHECK` constraint genuinely
  rejects an invalid value). 453 tests total, up from 449 (`ran-nf-oam`:
  35 -> 37; `mock-o1-adaptor`: 6 -> 8).

## Suggested next pass (priority order)

1. **§5's repo-audited completeness gaps are now the priority backlog** —
   unlike §1's remaining items, every one of these is concrete, scoped,
   and buildable without a stakeholder call: a real reference
   implementation was read and a specific missing operation/field/behavior
   named. Within §5, the standout items — genuinely broken or misleading
   as shipped, not just "thinner than the reference" — are worth taking
   first:
   - ~~`ran-nf-oam`'s and `a1-related`'s dangling `/health` callbacks
     (`subscribe_pm` and `register_ei_type` each register a URL that
     404s) — a one-route fix, done for both in the same pass since it's
     the identical bug class.~~ — **closed.** Grepped every module for
     the same pattern (`producerHealthCallbackUrl`/`health_callback`
     self-registered against DME with no matching `/health` route) —
     confirmed no other module has it; this bug class is fully closed
     across the build, not just these two.
   - ~~`a1-related`'s `SubscribePolicyStatus`/`UnsubscribePolicyStatus`
     being complete no-ops with zero delivery anywhere in the stack.~~ —
     **closed.** Real best-effort delivery on status change now exists;
     `subscriptionScope`'s `OWN`/`OTHERS` filtering remains unhonored
     (needs subscriber identity this build doesn't track — noted in §5
     as a documented partial, not silently dropped).
   - ~~`focom`'s `subscribe_inventory_changes` not actually subscribing
     to anything (no callback param, no storage, no delivery).~~ —
     **closed.** Real best-effort delivery on provision/deprovision;
     the original type-unknown-at-delete partial is also resolved now
     that a real `ResourceType`/`ResourcePool` schema exists (see
     below).
   - ~~`sme`'s `notify_service_change` never being called from the
     routes that should trigger it (the delivery logic exists, it's
     just dead code).~~ — **closed.** Wired into
     `register_service`/`deregister_service`; also fixed the
     function's own claimed-but-unenforced authz gate while wiring it
     in.

   All four standout items are now closed. Every module's missing
   GET-by-id/list/query endpoints — `dme`, `a1-related`, `focom`,
   `ai-ml-workflow`, and finally `ran-analytics` — are now closed too;
   that recurring theme across §5 is done. Each module's remaining §5
   items are independently pickable, module by module.
2. The three remaining §1 design-level decisions — `WEIGHTED_TRIGGERS`,
   the alarm-storm correlation algorithm, and A1-ML operations — are not
   stakeholder-answerable the way the rest of that section was: the
   first two genuinely need real data (noise-floor data; a real
   correlation algorithm) that would otherwise be fabricated, and the
   third only needs revisiting if A1-ML's out-of-scope decision itself
   changes. Not blocked on a call, blocked on data or a scope change.
3. `mock-near-rt-ric`/`r1-termination`/`policy-mgmt` (10 tests each) are
   now the shallowest test-covered modules; `nfo`'s own real gap (the
   thin deployment state machine, closed this pass) is done, not just a
   coverage number. An earlier survey of the remaining shallow tier
   found only 1-2 minor edge-case gaps each — already close to
   thoroughly covered. Diminishing returns as a coverage pass; §5's
   remaining items are a better next target.
