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
  a real local Postgres 16 instance.
- **No real southbound integrations beyond the A1 mock** — O1 Adaptor
  `PATCH` calls, actual `docker run` invocations, etc. are all elided in
  favor of recording the correct state transition.
- **No real OAuth2/token enforcement at R1 Termination** — only a comment
  and a `tokenEndPoint` URI in the bootstrap response; no actual
  validation code path.
- **RAN NF OAM's MnS Registry discovery is a heartbeat-aging stub**, not
  real registry polling.
- **No persisted OpenAPI spec files anywhere** — relying entirely on
  FastAPI's live `/docs` generation rather than committed contracts.
- **FOCOM's hardcoded single-cluster stub.**
- **NFO's Heal/Scale operations are stubs.**
- **None of the Repo Map's ADOPT recommendations are actually
  vendored/integrated** — this build consolidates on one Python/FastAPI
  stack rather than forking `nonrtric-plt-sme` (Go), the ICS reference
  (Java), `pti-o2` (Python), etc. The ADOPT repos stay pattern references
  only. §5 goes further than this: it audits, per module, whether the
  *functionality* those repos implement was still carried over even
  without vendoring the code — it mostly wasn't.
- **The full `docker-compose` stack (15 services, including the isolated
  `a1_mock_net` network segment) has never been run end-to-end** — no
  Docker daemon is available in the build sandbox; only
  `docker-compose config` YAML parsing and direct Python/pytest execution
  against each service in isolation have been validated. The RT-7
  network-isolation claim is structurally correct in the compose file but
  functionally unverified.

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
| nfo | 9 |
| mock-near-rt-ric | 10 |
| r1-termination | 10 |
| policy-mgmt | 10 |
| rapp-mgmt | 12 |
| sa-smos | 12 |
| so-smos | 13 |
| ran-analytics | 17 |
| ai-ml-workflow | 20 |
| sme | 22 |
| dme | 23 |
| onboarding | 26 |
| ran-nf-oam | 28 |
| a1-related | 29 |
| focom | 34 |

Plus 10 cross-service integration tests in `tests_integration/`.
`nfo` (9 tests) is now the shallowest-covered module. `rapp-mgmt` and
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
before this pass despite being a real route.

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
- Event subscription filtering is type-only — no per-`apiId`,
  `apiInvokerId`, or `aefId` filter, which the reference's
  `EventFilters` supports.
- `discover_services` only filters on `api_name`/`api_version` — the
  reference also filters on category, `aefId`, protocol, data format,
  and comm type, against a nested `AefProfiles → Versions → Resources`
  structure ours has no equivalent of.
- `ServiceProfile` is flattened — no `aefProfiles` (multiple exposing
  functions per API), `apiSuppFeats`, or `shareableInfo` (cross-provider
  sharing flag).
- `register_service` accepts any `apf_id` with no check that it's an
  actual registered publisher — a direct consequence of provider
  enrolment being unmodeled (see below).
- *Structurally out of scope, confirmed by direct inspection*: API
  Invoker onboarding, Provider (APF/AEF/AMF) enrolment, and the
  Security/token API are real CAPIF subsystems the reference implements
  that this build assumes pre-established: not gaps, a declared
  boundary. `accesscontrolpolicyapi`/`routinginfoapi`/`auditingapi`/
  `loggingapi` are unimplemented in the reference itself too — nothing
  to catch up to there.

### DME (`dme/`) — vs `nonrtric-plt-informationcoordinatorservice` (ICS)

- **`producerHealthCallbackUrl` is stored but never called** — `typeStatus`
  is computed only from whether a `DataJob` row is `ACTIVE`, so a dead
  producer with an active job still reports `ENABLED`. ICS actually
  polls the callback and derives status from real producer availability.
- No job push to producers at all — ICS POSTs the job definition to the
  producer's callback URL on create/delete; `create_data_job`/
  `terminate_data_job` only ever touch our own DB.
- No producer-status endpoint (`GET .../info-producers/{id}/status`).
- No job-definition schema validation against `dataProductionSchema` —
  `productionJobDefinition` is accepted as an arbitrary dict.
- ~~No GET-by-id for `DataJob`/`DataOffer`, no job-level status
  endpoint, and `discover_dme_types`' `data_category` query param is
  declared but silently never applied to the query.~~ — **closed.**
  Added `GET /data-jobs/{id}`, `GET /data-jobs/{id}/status`, and
  `GET /offers/{id}` (all 404 on an unknown id). `discover_dme_types`
  now applies `data_category` — filtered against `namespace`, since
  `DMEType` has no dedicated category column and namespace (the
  grouping half of R1AP's `namespace.name` typeName convention) is the
  closest concept it does have.
- No update-in-place (PUT) semantics — only POST-create/DELETE.
- No type-subscription mechanism (consumers notified when a type is
  registered/removed) — entirely absent.
- `deregister_producer` deletes a producer's `DMEType` rows
  unconditionally — no check for active producers still depending on a
  type, and no cascade cleanup of orphaned `DataJob`/`DataOffer` rows.

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
- Package validation is much thinner — the reference runs an ordered
  validator chain (filename convention, required
  `Definitions/acm_composition.json`, ASD descriptor parsing with real
  duplicate-descriptor-id detection). `_validate_package` only reads
  `TOSCA.meta` and does a `KeyError` existence check — no filename
  check, no duplicate-package detection at all.
- No resource-provenance detail endpoints — the reference's
  `GET /rapps/{id}` and `GET /rapps/{id}/instance/{id}` return nested
  ACM/SME/DME resource records (composition IDs, provider-function IDs,
  producer/consumer type lists); ours returns only flat
  `{packageId, state, ...}`/`{instanceId, packageId, state}`.
- No standalone delete-after-undeploy for an instance, distinct from
  `terminate`.
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
- No policy-type detail retrieval (`GET /policy-types/{id}`) —
  `QueryPolicyTypes` returns a hardcoded Python set (`KNOWN_POLICY_TYPES`),
  never sourced from or synced with an actual RIC; `nearRtRicId` is
  accepted but never used to filter or query anything real. No RIC
  repository (`/rics`) concept exists at all.
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
- No service registration/supervision (`/services`, keepalive, and
  auto-delete of a stale rApp's policies).
- No duplicate-policy/fingerprint detection — the reference's mediator
  rejects duplicate policy content or a reused id across types; ours
  accepts anything per `policyId` with only an empty-object check.
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
- NFO's deployment state machine is much thinner — reference has 7
  states (including ABNORMAL/UPDATING) plus real duplication/dependency
  guards and a resource-linkage object; ours only moves
  INSTANTIATING→RUNNING with no such guards, and Heal/Scale have no
  state transitions of any kind.
- **No topology/entity-relationship export for TEIV at all** — the
  Blueprint explicitly names "FOCOM's placement as a TEIV data source"
  as a confirmed integration point, but FOCOM has no typed
  entity/relationship model, no CloudEvent/Kafka producer, and no
  `/topology`-shaped endpoint — not even a stub exists for an
  integration this build's own Blueprint claims.
- *Confirmed structurally out of scope*: the real `focom-to-teiv-adapter`
  mechanism (direct kubeconfig access to K8s clusters, CRD reads),
  pti-o2's hardware-telemetry watchers, and real multi-cluster K8s
  lifecycle management are correctly excluded from a docker-run-based
  Phase 1.

### AI/ML Workflow (`ai-ml-workflow/`) — vs `aiml-fw-awmf-modelmgmtservice`, `aiml-fw-awmf-tm`, `aiml-fw-athp-sdk-feature-store`, `aiml-fw-athp-tps-kubeflow-adapter`

- No model artifact upload/download or versioning — the reference has
  real `UploadModel`/`DownloadModel` (S3-backed) with an
  auto-incrementing `artifactVersion` separate from `modelVersion`; ours
  has an `artifact_location` string field that nothing in `main.py` ever
  reads or writes.
- Model CRUD is incomplete — ~~no `GET /models/{id}`~~ (**closed**: now
  404s on an unknown id), no update, no delete/deregister; only
  create and a type-filtered list existed before this pass.
- Registration metadata is thin — no I/O data type schema, no
  author/owner, no `TargetEnvironment` declarations (platform,
  environment type, dependencies) the reference requires.
- `TrainingJob` is far thinner than the reference's real two-axis
  (step × status) tracking — no `run_id`, no distinct
  training/validation dataset fields, no metrics-writeback endpoint, no
  step state machine (DATA_EXTRACTION/TRAINING/TRAINED_MODEL), no
  separate consumer/producer rApp ids. Ours collapses all of this into
  two free-form JSON dicts and a flat status string.
- No feature-group/feature-store concept exists at all — the reference
  has a first-class `FeatureGroup` entity with its own CRUD and a real
  SDK querying by trainingjob/feature name.
- No uniqueness/conflict check on `(model_type, version)` — duplicate
  registrations silently succeed where the reference 409s.
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
- The dead subscriber-notification loop in `publish_report`
  (`for sub in subs: pass`) is real, but not a regression behind the
  reference: `aiml-fw-apm-monitoring-server`'s own `Subscribe`
  executor is equally an empty stub that doesn't even persist a
  subscription — ours is one step ahead (real DB persistence) with the
  same missing last-mile delivery.
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
3. `nfo` (9 tests) is now the shallowest test-covered module, but an
   earlier survey found only 1-2 minor edge-case gaps — already close
   to thoroughly covered. Diminishing returns as a coverage pass;
   §5's remaining items are a better next target.
