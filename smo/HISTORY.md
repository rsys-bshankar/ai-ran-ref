# History — decisions and closed items

What the Phase 1 SMO reference build decided and built, condensed from the former
`HISTORY.md` (§1–§6, "Closed", "Suggested next pass"), `HISTORY.md §7` and the Wave
exit reviews. Items still open are in [`OPEN_ITEMS.md`](OPEN_ITEMS.md). Code comments cite
entries here by section (`HISTORY.md §5`) or ID (`HISTORY.md OI-6.3`).

**ID scheme**

| Prefix | Origin |
|---|---|
| `OI-1-…`, `OI-2-…`, `OI-3-…`, `OI-4` | Former `HISTORY.md` §1 (design decisions), §2 (code/lifecycle gaps), §3 (call-flow gaps), §4 (test coverage) |
| `OI-5-<module>-…` | Former §5, O-RAN-SC repo-audited completeness gaps |
| `OI-C-…` | Former "Closed" section entries not already covered by §1–§5 (pilot demo, audits) |
| `OI-6.1` … `OI-6.7` | Former §6, AI/ML pipeline review (numbers kept) |
| `PR-<area>-<n>` | Production-readiness features from `OPEN_ITEMS.md` §5, closed (§10) |
| `SA-<area>-<n>` | Former `HISTORY.md §7`, numbered as in that file's per-module sections |
| `W0` … `W10.4` | Waves of the AI Platform Service Decomposition; work-item IDs (`W9-02`) are indexed at the end of §9, decisions `D-1`…`D-9` are in `docs/STANDARDS.md` |

PR numbers are given where the squash-merge title or the original text names them.

Recurring conventions referred to below:
- **Best-effort notification**: an unreachable callback never fails the primary call; every
  caller-supplied callback goes through `smo_shared.webhook` (OI-6.3).
- **Lazy staleness**: no scheduler exists; liveness/expiry is computed at read or gate time.
- **Owned-child delete**: `ON DELETE CASCADE` on the FK plus explicit application cleanup.
- **Postgres verification**: schema changes are checked against a real Postgres 18 with
  `scripts/check_migration_matches_models.py` (OI-2-migration-check).

---

## 1. Design decisions (former §1)

- **OI-1-training-upgrade** — A second `RequestTraining` on a model already `TRAINING` cancels the
  orphaned job; the newer call wins. `RequestTraining` fires `TRAIN` or `RETRAIN` by model state
  (fixes a crash on every `ACTIVE -> TRAINING` retrain).
- **OI-1-intent-rmih** — Intent-to-RMIH matching. First built as capability-based push; replaced in
  Wave 3 by consumer-side selection: `CreateIntent` names one registered `rmihId`, validated against
  `intentHandlingCapabilityList` and `intentHandlingScope` (`RMIH_CAPABILITY_MISMATCH`, 422).
  See SA-INTENT-arch and W3. (#110)
- **OI-1-cm-sync** — RAN NF OAM CM sync method is NETCONF. `WriteConfigurationChanges` sends an
  RFC 6241 `<edit-config>` RPC over HTTP (`ran-nf-oam/app/netconf_client.py`) to the ME's
  `O1AdaptorEndpoint.adaptor_uri`. A RESTCONF-provisioned ME is dispatched over RFC 8040 since
  OI-1-cm-sync-restconf. `cm_schema_cache` holds real descriptors since W9.
- **OI-1-cm-sync-restconf** — RESTCONF dispatch for an ME provisioned with `o1Protocol=RESTCONF`.
  - **Client:** `ran-nf-oam/app/restconf_client.py`. The ME's `adaptor_uri` is the RESTCONF root, and
    a managed object is the data resource `{root}/data/managed-element={ref}[/managed-function={functionRef}]`
    with percent-encoded keys. Bodies are `application/yang-data+json` RFC 7951 list entries.
  - **Operations:** `merge` is PATCH, `replace` is PUT, `create` is POST on the parent (409
    `data-exists`), `delete` is DELETE (`data-missing` is an error) and `remove` is DELETE with a
    missing target accepted. Read-after-write (`GET /managed-entities/{ref}/config`) is a GET.
  - **Retries:** the same policy and alarm as NETCONF. A timeout, 502/503, or a 5xx without an error
    body is transient (`RESTCONF_TIMEOUT`, `RESTCONF_UNREACHABLE`). An `ietf-restconf:errors` reply is
    a definite answer and is never retried (`RESTCONF_REQUEST_FAILED`).
  - **Dispatch:** RAN NF OAM picks the client by `ManagedEntity.o1_protocol`. Any protocol other than
    NETCONF or RESTCONF is still rejected `PROTOCOL_NOT_SUPPORTED` (the read route answers 409).
  - **Mock:** `mock-o1-adaptor` answers RFC 8040 at `/restconf` (plus `/.well-known/host-meta`) over
    the same running configuration and fault injection as its NETCONF route. It now declares both
    vendor modes by default.
  - **Not taken:** TLS, HTTP authentication, and the YANG library (`ietf-yang-library`). They match
    this build's plain-HTTP transport everywhere else, and there are no YANG modules to list.
  - **Not taken:** YANG Patch (RFC 8072). One plain PATCH per managed object is enough, because the
    write path already sends one atomic request per sub-change.
- **OI-1-sa-reconnect** — SA SMOS `RECONNECT` reads the monitor's `target_order_id` back from SO SMOS,
  finds the completed `DEPLOY` step's `nfDeploymentId` and dispatches NFO Heal. For a
  rApp-instance-scoped monitor it heals the current instance's `workloadRef`. Call flow 04.
- **OI-1-sa-rollback** — SA SMOS `ROLLBACK` returns a rApp to its previous version.
  - **Version history:** rApp Management records every committed upgrade in `rapp_instance_version`.
    Each row holds what the retired instance ran (package, configuration, `autonomyMode`,
    `regionScope`) and the lineage link from the retired instance id to its successor.
  - **Rollback:** `POST /rapp-mgmt/instances/{id}/rollback` is an upgrade back to the newest
    `UPGRADE` version not already rolled back, restoring that snapshot. It is the same two-row
    choreography, with the same timeout and auto-rollback, so a failed rollback leaves the current
    version running. A commit records a `ROLLBACK` version and marks the upgrade it undid, so repeated
    rollbacks walk back (v3 → v2 → v1) instead of flip-flopping.
  - **Superseded ids:** an upgrade replaces the instance row, so a superseded id resolves through the
    lineage to the current instance (`rollback`, `GET /instances/{id}/versions`).
  - **SA SMOS:** `AssuranceMonitor` gains a third, exclusive target, `target_rapp_instance_id`.
    `ROLLBACK` on it dispatches the rollback: `RESOLVED` with the started rollback in `result`, or
    `ESCALATED` with rApp Management's reason in `detail` (nothing left to roll back, not `RUNNING`,
    earlier package no longer deployable). An order-scoped or unscoped monitor answers 409
    `ROLLBACK_HISTORY_UNAVAILABLE` (formerly 501): NFO keeps no version history for a bare NF deployment.
  - **Not taken:** keeping the retired `RAppInstance` row instead of a version record. It would
    leave an `UNDEPLOYED` row per upgrade in every instance list, and its revoked credential and
    released NFO deployment would have to be re-provisioned anyway.
  - **Not taken:** rolling back an order-scoped NF deployment through NFO, which has no
    descriptor or version history to return to.
  - **GUI:** the rApp instance drawer shows the version history and a Roll back button. The KPIs
    page registers rApp-scoped monitors.
  - Call flows 04 and 07.
- **OI-1-producer-reconsideration** — On CRASH/TERMINATE, the `RAppInstance` FSM calls DME
  `DELETE /production-capabilities` keyed by the instance's `oauth_client_id`; best-effort.
  `UPGRADE_COMMIT` runs the same teardown on the superseded instance (OI-2-upgrade-completeness).
- **OI-1-coordination-group** — Retrain is the only remedial action for an
  `MLModelCoordinationGroup`. `report_performance` fires `RETRAIN` (one `TrainingJob` each) on every
  `ACTIVE` member; an SA SMOS monitor with `target_coordination_group_id` always dispatches a group
  retrain regardless of `actionType`. Group membership is filtered in Python (SQLite has no `ANY`).
- **OI-1-joint-training** — Shape B / `JOINT_TRAINING` stays out of scope; the schema value is kept
  for forward compatibility, no code branches on it.
- **OI-1-upgrade-timeout** — `upgradeTimeoutSeconds` default 300 s is the intended value (LLD §6).

## 2. Repo code / lifecycle gaps (former §2)

- **OI-2-nfdd** — NFO `POST /descriptors` (CreateDescriptor), called by Onboarding after validation;
  rApp Mgmt uses the real `nfDeploymentDescriptorId` from `onboarding-status`.
- **OI-2-recover** — `POST /instances/{id}/recover` fires `RECOVER` (`FAULTED -> DEPLOYING`).
- **OI-2-usage-registration** — `CreateInstance` calls Onboarding `usage/start` and stores
  `package_usage_registration_id`; `TerminateInstance` calls `usage/stop`. Makes the cascade-delete
  guard reachable.
- **OI-2-dataoffer-check** — `CreateDataJob` checks `dataDeliveryMethod` against the type's own
  `DataOffer` when one exists (`_validate_delivery_method`).
- **OI-2-schema-bugs** — `rapp_instance.pending_upgrade_instance_id` was missing from the migration
  and `oauth_client_id` was wrongly `NOT NULL`; both fixed.
- **OI-2-migration-check** — `scripts/check_migration_matches_models.py` (CI job
  `migration-postgres`) applies the migration and compares column presence and nullability with
  every ORM model.
- **OI-2-o1-adaptor** — `mock-o1-adaptor` answers RAN NF OAM's `<edit-config>` with `<ok/>` or
  `<rpc-error>`; XML parsed with `defusedxml` (CWE-611). Fixed `tests_integration/mesh.py`
  dropping non-JSON (`content=`) bodies. Real `docker run`/Helm in NFO stays elided.
- **OI-2-oauth2** — SME `InvokerRegistration` (`POST /invoker-registrations`), `POST /oauth2/token`
  (client_credentials) and `POST /oauth2/introspect` (RFC 7662, opaque server-tracked tokens).
  R1 Termination enforces a token on every proxied call except `/bootstrap` and fails closed if SME
  is unreachable. Secrets stored as salted scrypt, tokens as SHA-256. Scopes are checked since
  OI-2-oauth2-scope.
- **OI-2-oauth2-scope** — `POST /oauth2/token` checks `scope` before issuing (`_check_scope`).
  - **Granted as-is:** absent, or one of SMO's own scopes (`smo-internal`, `smo-gui`).
  - **Checked:** TS 29.222's `3gpp#aefId:apiName[,apiName][;aefId:...]`. Each API must be a
    published service, exposed by that AEF (one of its `aefProfiles`), and discoverable by the
    invoker under the same gate as `discover_services`.
  - **Refused:** anything else, with 400 `invalid_scope` naming the first problem.
  - The granted scope is stored on `issued_access_token.scope` and returned by
    `/oauth2/introspect` (RFC 7662 `scope`).
  - **Not taken:** per-call enforcement at R1 Termination. The gateway maps paths to modules, not
    to published APIs, so it has nothing to match a scope against; the AEF that serves the API is
    the place that would. Signed JWT tokens are not taken either: no IdP is run.
- **OI-2-mns-registry** — Endpoint staleness is computed live at the `write_configuration_changes`
  gate (`_age_endpoint_health`, shared with `/discover`). Real MnS Registry polling stays elided.
  `smo_shared.timeutil.as_utc` handles SQLite naive datetimes.
- **OI-2-openapi** — `docs/openapi/<module>.json` generated by `scripts/generate_openapi_specs.py`;
  `tests_integration/test_openapi_specs.py` fails on drift. R1 Termination's proxy route pins
  `operation_id="proxy"` (FastAPI's derived id depended on `PYTHONHASHSEED`).
- **OI-2-focom-inventory** — `GET /inventory` reads the seeded `DeploymentManager`/`ResourcePool`
  rows; single-cluster topology stays Phase 1 scope (D-DEPLOY-FOCOM-1). Reshaped in SA-FOCOM-8.
- **OI-2-nfo-heal-scale** — Heal/Scale drive real state transitions (see OI-5-nfo-fsm).
- **OI-2-adopt-pattern-only** — Decision: ADOPT repos (`nonrtric-plt-sme`, ICS, `pti-o2`, …) are
  pattern references only; one Python/FastAPI stack, nothing vendored.
- **OI-2-compose-config** — CI job `docker-compose-config` runs `docker compose config --quiet`.
- **OI-2-compose-e2e** — CI job `compose-e2e` (`smo-tests.yml`, GitHub's Docker-enabled
  `ubuntu-latest`) runs `docker compose up -d --build` on the full stack, then:
  - `scripts/compose_e2e.py` inside `r1-termination` (the fast gate): every service answers
    (`/health`; gui-bff and the mock O1 adaptor, which have none, answer HTTP), `/bootstrap` names
    `service-apis`, and an unauthenticated routed call is rejected;
  - replays DEMO_RUNBOOK §2–§27 live: `tests_integration/test_demo_runbook.py` with
    `SMO_E2E_LIVE=1` (`tests_integration/live.py`) in a `python:3.11-slim` container attached to the
    compose network, driving every module by hostname. The same file runs in-process on every CI
    run, so the two cannot drift. The container is also `demo-consumer` (a real receiver for the
    runbook's callbacks, so the notification assertions check real deliveries) and the package
    server (the built `samples/*.csar`, from a fixed table). The four reference rApp demos run their
    own `demo.py` scripts, so the live R1 token flow is exercised too;
  - checks the `a1_mock_net` isolation (RT-7): `a1-related` reaches `mock-near-rt-ric`, the default
    network cannot resolve it, and it has no route out (`internal: true`); only `gui`, `postgres`
    and `r1-termination` publish host ports;
  - checks the GUI serves the SPA and proxies `/api`; dumps logs on failure; `docker compose down -v`.
  Also runnable by hand (`workflow_dispatch`). The first runs found no stack defect.
  - **Not taken:** the intent-dispatch delivery assertions (§10) run in-process only: live, those
    destinations are the real so-smos / sa-smos services, whose receipt the test cannot observe.

**Lifecycle (LCM) defects found by call flows 06, 07, 26 and 27, all fixed:**
- **OI-2-terminate-workload** — `TERMINATE` releases the instance's resources
  (`rapp-mgmt/app/provisioning.py:release_instance_resources`): NFO `DELETE /nfo/deployments/{workloadRef}`
  and usage/stop, best-effort, the outcome recorded in `rapp_instance.last_teardown` (`lastTeardown`).
- **OI-1-upgrade-identity / OI-2-upgrade-completeness** — The upgrade replacement is provisioned like
  `CreateInstance` (`provision_instance`: deployable package state, fresh `oauth_client_id`, NFO
  instantiate, usage/start; configuration, autonomy mode, region scope and timeout copied). Commit tears
  the old instance down like `TERMINATE` and deletes it; rollback tears the replacement down.
  `upgradeTimeoutSeconds` is enforced lazily: an overdue upgrade rolls back (`RAPP_UPGRADE_TIMED_OUT`).
- **OI-2-lcm-error-mapping** — Illegal lifecycle transitions return 409 `LIFECYCLE_ILLEGAL_TRANSITION`
  naming the state and event (rApp Management, Onboarding); `SERVICE_NAME_CONFLICT` only when a guard
  refused. Unknown ids return 404. `TERMINATE` is legal from `FAULTED` and `DEPLOYING`.
- **OI-2-package-redeploy** — The duplicate-hash check ignores `DELETING` and `FAILED` packages, so a
  deleted or failed CSAR can be onboarded again.
- **OI-5-onboarding-priming (residual)** — `CreateInstance` accepts `AVAILABLE` or `PRIMED`.
- **OI-2-model-eol-serving** — `DEPRECATED`/`RETIRED` models refuse runtime activate and scale;
  `RETIRED` also refuses inference, and `RETIRE` terminates the runtime (NFO teardown and runtime
  events). A `DEPRECATED` model's active runtime keeps serving until retirement (a grace period for
  consumers to move). TS 28.105 has no deprecated/retired state (its only serving control is
  `AIMLInferenceFunction.activationStatus` plus loading), so this split is a design choice; the
  alternative, deactivating the runtime on `DEPRECATE`, was not taken because it would make
  deprecation and retirement the same thing for consumers. Call flow 26.
- **OI-2-governance-bypass** — `POST /models/{id}/advance` fires only governance events plus
  `DEPRECATE`/`RETIRE`; job-driven events and unknown events return 422 naming the job route. The
  GUI completes stages through the job routes.
- **OI-2-training-lifecycle-edges** — Cancelling an active training run fires `TRAINING_FAILED` and
  tears down its runtime (finished jobs: 409); a rolled-back `CERTIFIED` model can retrain; wrong-state
  requests return 409 naming the state; every resume path restarts the timeout clock; the NRM read
  routes run the lazy timeout sweep; NRM training/testing requests accept optional `packageId`,
  `runtimeProfile` and `timeoutSeconds`. Call flow 27.

## 3. Call-flow gaps (former §3)

- **OI-3-flows-05-10** — Call flows 05–10 added (A1 EI registration, onboarding failure/deletion,
  rApp fault/performance, RAN Analytics production, Intent flow, multi-step SO SMOS order). They
  surfaced OI-2-recover, OI-2-usage-registration, OI-2-dataoffer-check and OI-1-intent-rmih.
- **OI-3-flows-11-20** — Flows 11–20 added and six refreshed (#127): DME producer/type LCM, DataRecord
  movement, MLMF subscription LCM, correlation-id, NFO workload LCM, FOCOM inventory LCM, AIMgF runtime
  LCM, SME CAPIF security, SWM job FSM, alarm+PM subscription LCM.
- **OI-3-report-performance-404** — `report_performance` on an unknown `subscriptionId` returns
  `MLMF_SUBSCRIPTION_NOT_FOUND` (404) instead of a 500. (#128)
- **OI-3-pm-unsubscribe** — `DELETE /pm-subscriptions/{id}` (idempotent) plus a GUI Unsubscribe
  action. (#128)
- **OI-3-nfo-abnormal** — NFO's `DELETING` and `ABNORMAL` states are reachable through the API,
  by an asynchronous Terminate that the deployment manager (O2 DMS) completes.
  - **Default:** `DELETE /deployments/{id}` stays synchronous (`TERMINATING` -> `DELETING` ->
    removed, 204). rApp Management, AIMgF and SO SMOS expect the deployment gone and its
    descriptor free when the call returns: rApp rollback re-deploys a released descriptor.
  - **Asynchronous:** `?async_uninstall=true` answers 202 and leaves the deployment `TERMINATING`,
    with its `TERMINATE` operation `IN_PROGRESS`.
  - **DMS reports:** the DMS reports through `POST /deployments/{id}/dms-notifications`:
    - `UNINSTALL_COMPLETE` -> `DELETING`, then `DELETE_COMPLETE` removes the deployment;
    - `UNINSTALL_FAILED` / `DELETE_FAILED` -> `ABNORMAL`, with the operation `FAILED`;
    - `RUNTIME_FAILURE` takes an `INSTANTIATING` / `RUNNING` / `UPDATING` workload to `ABNORMAL`.
  - **ABNORMAL:** the deployment keeps the reason (`nf_deployment.abnormal_reason`). Heal recovers
    it and clears the reason; Terminate retires it. Its descriptor stays deployed until the
    deployment is really gone.
  - New `GET /deployments/{id}`. The GUI BFF lets admins post DMS notifications (no real DMS runs).
  - **Not taken:** making Terminate asynchronous by default, which would break every caller that
    re-deploys a descriptor. An asynchronous Instantiate or Scale is not taken either: neither has
    a state that only an asynchronous completion reaches.
- **OI-3-mermaid** — Bare `;` broke GitHub's sequence-diagram parser (#129);
  `gui/scripts/validate-call-flow-diagrams.mjs` runs in CI (#139).

## 4. Test coverage (former §4)

- **OI-4** — Coverage passes added route-level tests to so-smos, ran-analytics, focom, nfo,
  r1-termination (method/body/header/query forwarding, non-200 passthrough), mock-near-rt-ric
  (`UpdatePolicy`), a1-related, onboarding and sme. Bugs found on the way: SO SMOS `CancelOrder`
  never persisted (in-place JSON mutation), RAN Analytics producer re-registration crashed.
  Residual note in OPEN_ITEMS OI-4.

## 5. O-RAN-SC completeness gaps (former §5)

Audited route by route against the 18 Repo Blueprint repos. R1 Termination, Policy Mgmt/Intent
Service, SO SMOS and SA SMOS have no upstream repo to audit against. Every §5 gap is closed or
closed partially; residuals are in OPEN_ITEMS.

- **W10-alarm-cellref** — A RAN NF OAM alarm can name the cell it is about, so the reference rApps
  hold that cell instead of the whole managed element.
  - **RAN NF OAM:** `POST /alarms/ingest` takes `managed_function_ref` (stored in the existing
    `alarm.managed_function_ref` column, until now set only by RAN NF OAM's own dispatch alarms).
    `GET /alarms` returns it as `managedFunctionRef` and filters by it.
  - **SDK:** `sdk.data.query_critical_alarms(me)` returns an `AlarmScope`. `alarm_cell` reads the
    cell from the reference: `NRCellDU`, `NRCellCU`, `NRSectorCarrier`, `CommonBeamformingFunction`
    and `CESManagementFunction` name it directly, `NRCellRelation` and `NRFreqRelation` before the `-`.
    Any other reference, or none, is about the element as a whole and holds every cell, as before.
  - **EnergySaving:** a cell's LOCK is blocked (and a coverage alarm wakes it) when the alarm is
    on the cell or on a neighbour it hands its traffic to (`neighbourRefs`).
  - **Coverage:** a cell is held when the alarm is on it or on a neighbour, since a tilt or power
    move changes the neighbours' coverage too.
  - **Traffic Steering:** an alarmed cell is held as a source and excluded as a target
    (`TARGET_CRITICAL_ALARM`).
  - Each decision's `criticalAlarmIds` lists only the alarms that hold that cell.
  - **Not taken:** DN-based object addressing (SA-RANOAM-4 stays open); the flat
    `<IOC>=<id>` reference the rApps already write with is enough to name a cell.

### SME (`sme/`) vs `nonrtric-plt-sme`
- **OI-5-sme-events** — `register_service` fires `SERVICE_API_AVAILABLE`/`SERVICE_API_UPDATE`,
  `deregister_service` fires `SERVICE_API_UNAVAILABLE`; `notify_service_change` enforces the same
  authz gate as `discover_services`.
- **OI-5-sme-apiids** — `apiIds` filter on `SubscribeEvents`. The `apiInvokerId`/`aefId` filters
  followed in OI-5-sme-filters.
- **OI-5-sme-discover** — `discover_services` filters on `aefId`/`protocol`/`dataFormat`/`commType`.
- **OI-5-sme-filters** — The rest of TS 29.222's `CAPIFEventFilter`, plus invoker events.
  - **Events:** invoker onboarding, key update (`PUT /invoker-registrations/{id}`) and offboarding
    (`DELETE /invoker-registrations/{id}`) emit `API_INVOKER_ONBOARDED`, `API_INVOKER_UPDATED` and
    `API_INVOKER_OFFBOARDED`.
  - **Filters:** subscriptions take `apiInvokerIds` and `aefIds` beside `apiIds`. Each filter
    that is set must share a value with the event, whose `eventDetail` carries `apiIds` and `aefIds`
    (from the service's `aefProfiles`) or `apiInvokerIds`. An event with no value of a filtered kind
    does not match, as in CAPIF core's `getMatchingSubs`.
  - **Offboarding** deletes the invoker's tokens and trusted-invoker context.
  - **Not taken:** the `category` discovery filter. No published service carries a category, and
    there is nothing that would supply one.
- **OI-5-sme-aefprofiles** — `aefProfiles` (JSON), `apiSuppFeats`, `shareableInfo` on
  `ServiceProfile`; only the fields the filters need are kept.
- **OI-5-sme-provider-enrolment** — `ProviderRegistration` (`POST`/`DELETE /provider-registrations`);
  `register_service`/`query_own_services` return 403/404 for an unenrolled `apf_id`. RAN Analytics
  enrols before publishing. Provider-domain/AEF/AMF roles not modelled.

### DME (`dme/`) vs ICS
- **OI-5-dme-health** — `typeStatus` calls `producerHealthCallbackUrl` live at `GET /dme-types`.
- **OI-5-dme-jobpush** — `jobCallbackUrl` on registration; create/terminate POST/DELETE the job to
  the producer (best-effort). `ran-nf-oam` and `a1-related` answer `/dme-jobs`.
- **OI-5-dme-producer-status** — `GET /production-capabilities/{producer_id}/status`.
- **OI-5-dme-schema** — `productionJobDefinition` validated with `jsonschema` against
  `dataProductionSchema` (`SCHEMA_VALIDATION_FAILED`, 422).
- **OI-5-dme-get-by-id** — `GET /data-jobs/{id}`, `/data-jobs/{id}/status`, `GET /offers/{id}`;
  `data_category` filters on `namespace`.
- **OI-5-dme-put** — `PUT /data-jobs/{id}` updates in place; identity fields immutable
  (`DATA_JOB_TARGET_IMMUTABLE`, 400); re-pushes to the producer.
- **OI-5-dme-type-subscriptions** — `/type-subscriptions` CRUD; REGISTERED/DEREGISTERED notifications.
- **OI-5-dme-cascade** — `data_job`/`data_offer` FKs cascade; explicit cleanup on deregistration.

### Onboarding + rApp Management vs `nonrtric-plt-rappmanager`
- **OI-5-onboarding-priming** — `PRIMING`/`PRIMED`/`DEPRIMING`, `POST /packages/{id}/prime|deprime`;
  deprime blocked by active usage registrations; no `DELETE` edge from `PRIMED`. Synchronous.
  `CreateInstance` accepts `AVAILABLE` or `PRIMED` packages; priming stays optional. Call flow 06.
- **OI-5-onboarding-validation** — `.csar` filename check, required-file check, duplicate detection
  on `integrity_hash`; failures land `FAILED` via the async 202 contract. Required-file path later
  removed (SA-ASD-2).
- **OI-5-rapp-instance-detail** — `GET /instances/{id}` with `workloadRef` and `configuration`;
  nested ACM/SME/DME records not modelled.
- **OI-5-rapp-undeploy-delete** — `TERMINATE` lands in `UNDEPLOYED` (row kept); `DELETE
  /instances/{id}` 409s (`RAPP_INSTANCE_NOT_UNDEPLOYED`) otherwise. Fault/performance report FKs
  cascade.

### RAN NF OAM vs `ranpm`, `oam`, `smo-o1`, `sim-o1-*`
- **OI-5-ranoam-health** — `GET /health` answers the URL `subscribe_pm` registers.
- **OI-5-ranoam-alarm-fields** — `probableCause`, `specificProblem`, `rootCauseIndicator`,
  `correlatedNotifications` (UUID[]), `proposedRepairActions`; `severity` carries `perceivedSeverity`.
- **OI-5-ranoam-alarm-clear** — `PATCH /alarms/{id}/clear` sets `severity='cleared'` plus
  `clearedAt`/`clearUserId`.

### A1 Related vs `sim-a1-interface`, `a1policymanagementservice`
- **OI-5-a1-health** — `GET /health` answers the URL `register_ei_type` registers.
- **OI-5-a1-policy-list** — `GET /policies` filterable by `policy_type_id`/`near_rt_ric_id`/`creator_id`.
- **OI-5-a1-policy-type** — `GET /policy-types/{id}` with placeholder `policySchema`
  `{"type":"object"}`; no `/rics` (residual).
- **OI-5-a1-status-notify** — Status changes in `update_policy`/`query_policy_status` notify matching
  subscribers; `subscriptionScope` OWN/OTHERS treated as ALL (residual).
- **OI-5-a1-services** — `PUT`/`GET /services`, `DELETE /services/{id}`, `PUT
  /services/{id}/keepalive` (pms-api-v3); unregistering or a lazy keepalive sweep deletes the
  service's policies southbound. `RICStatus` callback out of scope.
- **OI-5-a1-fingerprint** — `mock-near-rt-ric` rejects byte-identical `policyObject` per type.

### NFO + FOCOM vs `pti-o2`, `smo-teiv`
- **OI-5-focom-schema** — `ResourceType`, `ResourcePool`, `Resource` (`parentId`), `DeploymentManager`
  tables, lazily seeded with the single-cluster topology.
- **OI-5-focom-drilldown** — `GET /resource-types[/{id}]`, `/resource-pools[/{id}[/resources]]`,
  `/deployment-managers[/{id}]`; provision/deprovision persist real `Resource` rows.
- **OI-5-focom-subscription** — `InventorySubscription` with `POST`/`DELETE
  /inventory/subscriptions`; CREATE/DELETE notifications with the real resource type.
- **OI-5-nfo-fsm** — Seven-state deployment lifecycle (INITIAL…DELETING), `_check_duplication`/
  `_check_dependencies` on Instantiate (`NFDeployment.name` added), `NFOCloudResource` linkage,
  Heal/Scale transitions.
- **OI-5-focom-teiv** — `GET /topology` exports entities/relationships in TEIV wire shape from real
  FKs; no Kafka/CloudEvent producer.

### AI/ML Workflow (now `aimgf/`, `mlmr/`, `mllf/`) vs `aiml-fw-*`
- **OI-5-aiml-artifact** — `POST /models/{id}/artifact`, `GET /models/{id}/artifact/{version}`;
  bytes in `ModelArtifact`; `artifactVersion` separate from `modelVersion`. No S3.
- **OI-5-aiml-crud** — `GET`/`PUT`/`DELETE /models/{id}`; identity immutable
  (`MODEL_IDENTITY_IMMUTABLE`); dependent FKs cascade.
- **OI-5-aiml-metadata** — `description`, `author`, `owner`, `inputDataType`, `outputDataType`,
  `targetEnvironments` (JSON), optional.
- **OI-5-aiml-trainingjob** — `runId`, `trainingDataset`, `validationDataset`, `consumerRappId`,
  `producerRappId`; `POST`/`GET /training-jobs/{id}/model-metrics`. Step tracking followed in
  OI-5-aiml-trainingjob-steps.
- **OI-5-aiml-trainingjob-steps** — A training run's steps: `DATA_EXTRACTION`, `TRAINING` and
  `TRAINED_MODEL` (the reference Training Manager's three main steps).
  - **Reporting:** the execution runtime reports the step it has reached with
    `POST /training-jobs/{id}/progress`. The report goes forward only, and only for an
    `IN_PROGRESS` run (409 `TRAINING_JOB_ILLEGAL_TRANSITION` otherwise).
  - **Storage:** `training_job.current_step` keeps the furthest step reached.
  - **Each step's status is derived**, not stored: steps before the current one are `FINISHED`.
    The current step carries the job's state (`IN_PROGRESS`, `SUSPENDED`, `FAILED`, `CANCELLED`),
    and later steps are `NOT_STARTED`. A `FINISHED` run finished every step.
  - Job views (`/status`, list, complete) carry `currentStep` and `steps`.
  - **Not taken:** a per-step status state machine stored beside `status`, as the reference has.
    Every place that ends a run (complete, cancel, timeout, the NRM flags, MLUpdate) would have to
    keep it in step. Deriving it leaves `status` the single record of how the run ended.
  - **Not taken:** the reference's composite steps (`DATA_EXTRACTION_AND_TRAINING`,
    `TRAINING_AND_TRAINED_MODEL`), which only mark the hand-over between two steps.
- **OI-5-aiml-featuregroup** — `POST`/`GET /feature-groups`, name rule `\w+` 3–63, duplicate 409.
- **OI-5-aiml-featuregroup-dme** — An `enableDme` feature group gets a real DME data job, like the
  reference's `create_dme_filtered_data_job`.
  - **Request:** `dmeTypeId` is required with `enableDme` (422 `FEATURE_GROUP_DME_JOB_REFUSED`).
  - **The job:** `CONTINUOUS`, `lifecycleStage` `TRAINING`, consumer `aimgf:feature-group:<name>`,
    delivery `dataDeliveryMethod` (default `PULL_HTTP`). Its definition carries the group's
    features, `measuredObjClass`, `sourceName` and `measurement`.
  - **Order:** the job is created before the group is stored. If DME refuses it (an unknown type,
    a definition its schema rejects, a delivery method no offer commits to), there is no group,
    and the error is 422 with DME's reason. A duplicate name is refused before any job is created.
  - **Storage:** `feature_group.dme_type_id` / `dme_data_job_id`.
  - **New routes:** `GET` / `DELETE /feature-groups/{name}`. The delete terminates the job, best
    effort, with the outcome in `dmeDataJobTeardown`.
  - **GUI BFF:** gains the missing operator rules for creating, reading and deleting a group (the
    single-group read carries the datalake token, like the list).
  - **Not taken:** the feature store itself, as before.
- **OI-5-aiml-uniqueness** — `UniqueConstraint(model_type, version)`; `MODEL_ALREADY_REGISTERED` 409.

### RAN Analytics (now `ran-analytics/` + `mdaf/`) vs `aiml-fw-apm-*`
- **OI-5-ranalytics-list** — `GET /producers`, `GET /subscriptions`, filterable.
- **OI-5-ranalytics-notify** — Optional `notificationDestination`; `publish_report` notifies
  matching subscribers. The reference repos are empty or stubs (confirms the BUILD verdict).

## 6. Pilot demo and audit passes (former "Closed" log)

`DEMO_RUNBOOK.md` and `tests_integration/test_demo_runbook.py` cover every section below.
- **OI-C-specs-folder** — Spec directories moved into `specs/` with `specs/README.md` catalogue.
- **OI-C-spec-audit** — First formal-spec audit, written up as `HISTORY.md §7` (section 7 here).
  Second Repo Blueprint pass found no further MnS Registry protocol to ground against.
- **OI-C-demo-csar** — `samples/hello-world-rapp/` (since removed; the lifecycle runbook now uses the Energy Saving package) + `build_csar.py`; onboard → deploy → bootstrap →
  operate → retire runbook. Found the required ACM file path was wrong in both validator and fixture.
- **OI-C-demo-ranoam** — `POST /o1-adaptor-endpoints` creates `ManagedEntity` + `O1AdaptorEndpoint`
  in `DISCOVERED`; runbook: heartbeat → CM write → alarm ingest/ack/clear, plus `PARTIAL_SUCCESS` on
  a mixed batch.
- **OI-C-demo-focom** — Provision/deprovision with inventory notifications; alarm ingest; `GET
  /performance` (no ingest route exists).
- **OI-C-demo-intent** — RMIH register, Intent create/dispatch, `intentHandlingScope` negative case.
- **OI-C-demo-a1** — Service registration, policy create (`ENFORCED`), duplicate `REJECTED`, status
  notification; supervision sweep with a short keepalive (#95).
- **OI-C-demo-onboarding** — Duplicate-content `FAILED`; priming lifecycle with deprime refusal (#88).
- **OI-C-demo-sme** — Trusted Invokers walkthrough (SA-SME-2); `apiIds` event filtering (#94).
- **OI-C-demo-aiml** — Model register → train → artifact → metrics → lifecycle → deregister;
  feature groups (#91).
- **OI-C-demo-ranalytics** — Producer, subscription, report notification.
- **OI-C-demo-so-smos** — Three-step order with fail-fast halt and cancel.
- **OI-C-demo-sa-smos** — `RECONNECT` (Heal, `RESOLVED`) and `ROLLBACK` refused for an order-scoped
  monitor (409 since OI-1-sa-rollback). Fixed: Onboarding
  committed the package before calling NFO; NFO Terminate clears `LCMOperation` rows; integration
  harness uses SAVEPOINT-joined sessions (`smo_shared/testing.py`) and `expire_on_commit=False`.
  Coordination-group remedial action (#92): `COORDINATION_GROUP_TOO_SMALL` (422) pre-check.
- **OI-C-demo-dme** — Type-subscription notifications (#89).
- **OI-C-demo-teiv** — `GET /topology` (#90). Fixed missing flush between auto-registered
  `ResourceType` and `Resource` inserts (FK violation on Postgres).
- **OI-C-gui** — SMO Operator GUI + BFF (`gui/`, `gui-bff/`) (#87); README consolidation (#96).

## 7. Spec audit outcomes (former `SPEC_AUDIT.md`)

Findings were sized small / moderate / large-structural. Every small and moderate finding is closed.
Large-structural items are confirmed Phase 1 scope cuts unless noted; open ones are in OPEN_ITEMS.

### RAN NF OAM vs TS 28.319/28.111/28.532/28.550 + O1NRM YANGs (`SA-RANOAM-n`)
- **SA-RANOAM-1** TS 28.319 Identity / Role / AccessRule as REST resources; `POST /config-jobs` evaluates
  the requester's roles per sub-change before dispatch (DENY beats ALLOW; Jex subset: absolute
  `/Class=id/...` with `*`). Requesters with no Identity or defined Role keep the old gate. Closed (reads and
  other write routes unguarded).
- **SA-RANOAM-2** `accessScope` replaces `scope`; `scope` stays as a deprecated alias. Closed.
- **SA-RANOAM-3** `WriteConfigSubChange.operation` (merge/replace/create/delete/remove, RFC 6241 §7.2,
  default merge, CHECK constraint); emitted on `<managed-object>`; mock adaptor accepts empty
  delete/remove payloads. (Closeable-list item 3.)
- **SA-RANOAM-4** DN refs accepted and validated on `managedFunctionRef` (class = last RDN); ME ids stay flat
  keys, no containment tree — accepted deviation.
- **SA-RANOAM-5** `alarmType` (11-value enum, CHECK). (Closeable item 1.)
- **SA-RANOAM-6** `ackUserId` and `alarmChangedTime` (`changed_at` updated on ack/clear). Severity
  now `PerceivedSeverity` (either case in, `INDETERMINATE` added, `perceivedSeverity` upper-case out): closed.
  (Closeable item 2.)
- **SA-RANOAM-7** `PMSubscription.granularityPeriod` (nullable). (Closeable item 4.)
- **SA-RANOAM-8** File Data Reporting built (`/pm-files`, `/files`, `notifyFileReady`); streaming still elided.
- **SA-RANOAM-9/10** `HeartbeatNtf` direction and `WriteConfigJob`/`PARTIAL_SUCCESS` — not gaps.

### FOCOM vs O2IMS (`SA-FOCOM-n`)
- **SA-FOCOM-1/3/4** `ResourceType.alarmDictionaryId/performanceDictionaryId/resourceKind/
  resourceClass/extensions`, `Resource.globalAssetId/tags/groups`, `DeploymentManager.
  supportedLocations/capabilities/capacity`; enums CHECK-constrained; `Resource` fields settable via
  `provision_resource` spec. (Closeable item 7.)
- **SA-FOCOM-5** `InventorySubscription.callbackUri` renamed `callback`; `consumerSubscriptionId`
  stored and passed on notifications. (Closeable item 8.)
- **SA-FOCOM-8** `GET /inventory` returns an `OCloud` shape (`oCloudId`, `name`, `resourceTypes`,
  `deploymentManagers`; `locations`/`oCloudSites` empty); `resource_type` filters. NFO reads `oCloudId`
  with the same fallback. (Moderate item 2.)
- **SA-FOCOM-2** `Location`, `OCloudSite`, `ResourcePool.oCloudSiteId` / `resources`; `/inventory` returns them. Closed.
- **SA-FOCOM-6** `AlarmEventRecord` (X.733 `eventType`, `PerceivedSeverity`, times), `AlarmSubscription` + `AlarmEvent`;
  performance records, jobs and NOTIFICATION subscriptions. Closed (FILE / STREAM reporting open).
- **SA-FOCOM-7** Artifacts, Cluster, Infrastructure and ProvisioningRequest as REST resources; a request creates
  a model-level `NodeCluster`. Closed (no real cluster).
- **SA-FOCOM-9** Unknown `resourceTypeId` is 404; `generic`, `gpu-l40`, `pserver` seeded; `POST /resource-types`;
  `FOCOM_AUTO_REGISTER_RESOURCE_TYPES` restores the old behaviour. Closed.

### Intent Service vs TS 28.312 (`SA-INTENT-n`)
- **SA-INTENT-1** `intentHandlingScope` is `Literal["RAN","CN"]`; checked against the named RMIH
  since W3 (first built as a pre-filter). (Closeable item 5.)
- **SA-INTENT-2** Capability check uses `expectations[].expectationObject.objectType` vs
  `supportedExpectationObjectType`. (#71; moderate item 1.)
- **SA-INTENT-3** `intentMgmtPurpose` independent field, spec default, CHECK constraint.
- **SA-INTENT-4** Required `Intent` fields (`userLabel`, `intentReportControl`, …) — closed in W6.
- **SA-INTENT-5** `DELETE /intents/{id}`, cascades `IntentReport` (#66). (Closeable item 6.)
- **SA-INTENT-6** All seven IntentReport kinds — closed in W6.
- **SA-INTENT-arch** Consumer-side RMIH selection by `rmihId` (W3, #110); see OI-1-intent-rmih.
- **SA-INTENT-partial** Closed: `Frequency`, `UEGroup`, `QoSId`, `CivicArea`, `CivicAddress`, `ReportingCondition`,
  `TimeCondition`, `TargetFulfilmentCondition` structure-checked (`ts28312_datatypes.py`); `ValueRangeType` enforced for
  generic values. The SDK's `energy_saving_expectation` sent a non-spec `schedulingTime` value and now sends a
  `SchedulingTime` (`timeIntervals`).

### SME vs CAPIF core source (`SA-SME-n`)
- **SA-SME-1** Invoker onboarding takes only `apiInvokerPublicKey`; server mints `apiInvokerId` and
  `onboardingSecret` (hashed). The key is used since SA-SME-1-public-key. (Moderate item 3.)
- **SA-SME-1-public-key** — An onboarded PEM public key is a credential: a token request can
  authenticate with an RFC 7523 client assertion instead of the onboarding secret.
  - **Checks:** the JWT must be signed with the invoker's key (RS, PS, ES or EdDSA), carry
    `iss` = `sub` = the invoker and `aud` = the token endpoint (`SME_TOKEN_AUDIENCE`, default
    `{SME_URL}/oauth2/token`), and expire within 300 s.
  - **Replay:** its `jti` is recorded in `used_client_assertion` until it expires, so one assertion
    buys one token.
  - **Errors:** secret and assertion together is `invalid_request`; a failed check is
    `invalid_client`, naming the reason.
  - **Onboarding:** a malformed PEM key is refused (422 `SECURITY_CONTEXT_INVALID`). Any other
    value stays an opaque label, which SMO's own clients use: they keep authenticating with
    their secret. `PUT /invoker-registrations/{id}` rotates the key, effective at once.
  - **Dependency:** `pyjwt[crypto]` is added to the service image and to CI.
  - **Not taken:** signature checks on other requests (only the token endpoint authenticates
    invokers), and mTLS.
- **SA-SME-2** Trusted Invokers: `PUT`/`GET`/`DELETE /trusted-invokers/{apiInvokerId}` and `POST
  …/delete` revocation; invoker-registration gate, body validation, auth-info redaction, per-entry
  revocation. `selSecurityMethod` = first declared preference. Token issuance does not read it.
- **SA-SME-3** VES heartbeat — not a gap.

### AI/ML (AIMgF) vs TS 28.105 (`SA-AIML-n`)
- **SA-AIML-1/2** NRM containment tree and FL/RL data model — closed at REST level in W4.
- **SA-AIML-3** Coordination group `minItems: 2` — confirms `COORDINATION_GROUP_TOO_SMALL`.
- **SA-AIML-4** `ml_training_type` (INITIAL_TRAINING/RE_TRAINING produced; CHECK allows all four).
- **SA-AIML-5** `TrainingJob.status` uses `requestStatus` names (NOT_STARTED/IN_PROGRESS/SUSPENDED/
  FINISHED/CANCELLED) plus `FAILED`. (#123)
- **SA-AIML-6** `POST /training-jobs/{id}/suspend|resume` (`TRAINING_JOB_ILLEGAL_TRANSITION`). (#108)
- **SA-AIML-7** `guard_kpi_floor` is equivalent to ThresholdMonitor — not a gap.
- **SA-AIML-8** `MLMFSubscription.notification_destination`; `DELETE /mlmf/subscriptions/{id}`. (#124)

### MLMR vs TS 29.482 MLR (`SA-MLMR-n`) — Wave 3 slice (#107)
- **SA-MLMR-2/3/4** `domain`/`custom_domain` (validated), `vendors`, `ModelArtifact.size_bytes`.
- **SA-MLMR-5** `MLModelPhase` belongs to AIMgF lifecycle — not a gap.
- **SA-MLMR-1** `MLModelsStorage` / `MLModelProfile` as `/storages`; a profile names a registered model. Closed.
- **SA-MLMR-6** `storeDiscReqs` (`duration`, `accessReqs`) enforced for discovery and download on the caller id R1
  Termination now forwards (`X-R1-Invoker-Id`). Closed (`location` not enforced).
- **SA-MLMR-7** `phaseInfo` with `trainingInfo.baseModelId`; AIMgF writes it at training start and success. Closed.
- **SA-MLMR-8** `usageReqs` (TRAINING / INFERENCE). Closed.
- **SA-MLMR-9** `GET /models?filt-criteria=` whole-object discovery with a `DiscoveryResp`. Closed.

### MDAF / RAN Analytics vs TS 28.104 (`SA-MDA-n`)
- **SA-MDA-1** MDAFunction/MDARequest/MDAReport — closed at REST level in W5.
- **SA-MDA-2** Optional `mda_type` validated against the 24-value enum; inferred for two shorthand
  values, `null` otherwise. (#122)
- **SA-MDA-3** `ThresholdInfo` conditional reporting with hysteresis and persisted
  `threshold_state`. (#106)
- **SA-MDA-4** Opaque `scope` — `MDARequest.analyticsScope` is a typed `AnalyticsScopeType` since W5.
- **SA-MDA-5** FILE reporting served since W5 (`GET /mda-reports/{id}/file` + file-ready
  notification); STREAMING recorded only (OPEN_ITEMS).

### DME/O1 Adaptor vs the MnS hierarchy workbook (`SA-O1-n`)
- **SA-O1-1** DME `/actions` addresses by IOC class names; still resolves through flat refs.
- **SA-O1-2** PM job control / file / streaming APIs — elided (SA-RANOAM-7/8).
- **SA-O1-3** Software Management already carries `ru_instance_id` and a DOWNLOAD/INSTALL/ACTIVATE
  FSM. WG4 (O-RU) out of scope. (#125)
- **SA-O1-4** WG10/WG5 IOCs: per-vendor own/spec/combined conformance (#125); registry built in W9. The WG10 O1 NRM and
  WG5 O-DU / O-CU descriptors now ship, generated from the YANG by `scripts/ingest_yang_schema.py` (3GPP common-module
  attributes unresolved: OPEN_ITEMS). Closed.
- **SA-O1-5** O-RU aggregation mount points — out of scope.

### Onboarding vs ASD/TOSCA CSAR (`SA-ASD-n`) (#116)
- **SA-ASD-1** `descriptor_id`, `descriptor_invariant_id`, `descriptor_version`, `schema_version`
  parsed and surfaced; uniqueness stays on the content hash.
- **SA-ASD-2** ONAP ACM composition file no longer required.
- **SA-ASD-3** `Files/Sme/` declarations parsed at onboarding; `bootstrap-complete` registers them
  with SME under the instance's `oauth_client_id`; TERMINATE/CRASH deregister. Both CAPIF and the
  build's own body shapes accepted. (#117)
- **SA-ASD-4** Helm deployment-item properties — not a gap.

### DME vs ICS API (`SA-ICS-n`)
- **SA-ICS-1** `DMEProducer` + `DMEProducerType` (many-to-many). Re-registration and multiple
  producers per type succeed; `typeStatus` ENABLED if any producer is healthy; job start/stop fan
  out; `deregister_producer` removes only the producer; `DELETE /dme-types/{id}` 409s while producers
  remain; `GET /production-capabilities[/{id}]`. (#121)
- **SA-ICS-2** `DELETE /data-jobs?consumer_id=X`; SDK `terminate_data_jobs_for_consumer`. (#119)
- **SA-ICS-3** `dataDeliveryMode` is an SMO v1.3 convention — not a gap.

## 8. AI/ML pipeline review (former §6)

From the architectural review of call flows 02/03/04/06/08/09/10/11/17/20 (#130).
- **OI-6.1** Operator gate: `APPROVE_TRAINING`/`APPROVE_VALIDATION` are governance self-loop events
  through `POST /models/{id}/advance` (require `decidedBy`, write a `CertificationRecord`). They set
  `ModelLifecycle.training_approved`/`validation_approved`, reset by `CREATE_TRAINING`.
  `request_validation`/`request_emulation` return 409 `TRAINING_NOT_APPROVED`/
  `VALIDATION_NOT_APPROVED`. GUI `modelActions(state, gate)`. (#135; CHECK-constraint fix #136)
  Runtime-transition gate is open (OPEN_ITEMS OI-6.1-runtime-gate).
- **OI-6.2** Execution runtimes: training/validation/emulation requests call NFO CreateDescriptor +
  Instantiate (`{"jobKind", "jobId"}` template), store `nf_deployment_descriptor_id`/
  `nf_deployment_id`, and terminate on completion or supersession. `InferenceJob.nf_deployment_id`
  is stamped from the model's serving deployment; no per-call deployment. (#137)
- **OI-6.3** Autonomy modes: `RAppInstance.autonomyMode` (AUTONOMOUS/ASSIST/SHADOW, default SHADOW)
  and `regionScope`, fixed at `CreateInstance`. Intent Service `AutonomyDispatch`:
  `POST /autonomy-dispatches` validates the RMIH, then AUTONOMOUS → Intent (`DISPATCHED`), ASSIST →
  `AWAITING_SCOPE` until `/resolve`, SHADOW → `SHADOWED`, no Intent. All modes notify the operator.
  Bypasses SO SMOS by design. `shared/smo_shared/webhook.py` SSRF guard (http/https only; loopback,
  link-local, multicast, reserved rejected) applied to every callback site (CodeQL `py/full-ssrf`).
  Later: Onboarding's package fetch (`_validate_package`) goes through the same guard; the guard returns
  False rather than raising for a malformed URL such as `http://[` (found by the Hypothesis property
  tests in `shared/tests/test_webhook_properties.py`). ClusterFuzzLite then fuzzes the guard and
  Onboarding's CSAR parsers (`fuzz/`, `.clusterfuzzlite/`); its first run found that a non-UTF-8 JSON file
  or a YAML manifest that is not a mapping escaped Onboarding's `FAILED` path. CodeQL does not recognise the custom guard as a
  sanitizer, so its `py/full-ssrf` alerts on the guarded calls are dismissed as by-design (a hostname
  allowlist is not viable here, see the module docstring).
  (#138) Wave 8 follow-ons: ASSIST `/reject` → `REJECTED`; dispatched Intent carries
  `objectInstance` and a `Cell` context from `regionScope`; SA SMOS O1-CM handler (W8). (#145)
- **OI-6.4** `RequestTraining.dmeDataJobIds` checked with `GET /dme/data-jobs/{id}`
  (`DME_ARTIFACT_NOT_FOUND`); stored on `TrainingJob`. Optional. (#133)
- **OI-6.5** `outcome_artifact_dme_type_id` set on completion; `_notify_job_completion` to each job's
  `notificationUri`; new `POST /training-jobs/{id}/complete`. (#134)
- **OI-6.6** SO SMOS dispatch entries for VALIDATION, EMULATION, `("DEPLOY","AIMGF")` and INFERENCE;
  GUI step templates. (#132)
- **OI-6.7** `POST`/`GET`/`DELETE /fm-subscriptions` register one shared `RAN.FaultRecords` DME type
  per subscribing ME; GUI FM subscriptions section. Visibility only; clearing stays RAN NF OAM. (#131)

## 9. Waves

### Waves 0–3 (AI Platform Service Decomposition)
- **W0** Architecture freeze (#99).
- **W1** `policy-mgmt` → `intent-service` (#100); `ai-ml-workflow` → `aimgf`/`mlmr`/`mllf` (#101);
  `mdaf` split from `ran-analytics` (#102); AI Runtime SDK `sdk/` + rApp packaging (#103).
- **W2** AIMgF `ModelLifecycle`/`RuntimeLifecycle` state machines; MLMR is model truth, AIMgF is
  lifecycle truth (#104).
- **W3** DME dual data-plane + O1 action mediation `/actions` (#105); MDAF ThresholdInfo (#106);
  MLMR vs TS 29.482 (#107); TrainingJob suspend/resume (#108); MLLF ownership docs (#109);
  consumer-side RMIH selection (#110); OAuth2/JWT + versioning (#111); error schema (#112);
  pagination (#113); unified callback field names (#114); SME auto-registration (#117);
  correlation-ID propagation across R1 (#118).

### W0 (Waves 4–10 plan) — #140
Frozen decisions D-1…D-9 are in `docs/STANDARDS.md`. Order of the work: Waves 4, 5 and 6 (the three standards) feed Wave 7
(runtime) and Wave 8 (autonomy), which feed Wave 10.1; Wave 9 (multi-vendor O1) is independent but the 10.1 O1 path must not regress
it; 10.2, 10.3 and 10.4 each started after the previous exit.
- D-1 Generic O1-CM intent handler (RMIH) in SA SMOS.
- D-1b ASSIST: approve (resolve with scope) or reject; stays `AWAITING_SCOPE` until one.
- D-2 Actuator per instance: `NRCellDU.administrativeState` or
  `CESManagementFunction.energySavingControl`.
- D-3 rApp-internal SERVING/PRE_SLEEP/SLEEP; only PRE_SLEEP→SLEEP writes O1.
- D-4 Existing DME types/DataJobs + `/actions`, thin SDK wrappers.
- D-5 Cell guard data as RAN NF OAM ManagedEntity attributes.
- D-6 Threshold + linear regression model; LSTM to backlog.
- D-7 Synthetic `PRB_UTILIZATION_SIM` emulation input.
- D-9 Full TS 28.105/28.104/28.312 compliance at REST level; addressing (flat REST, no DN tree) is
  the one deviation.
- P-1/P-2 One PR per wave, squash merge.

### W4 — TS 28.105 at REST level (AIMgF + MLMR) — #141
Every TS 28.105 IOC as a REST resource with spec names/enums (`aimgf/app/nrm.py`, MLMR
`/ml-models`, `/ml-model-repositories`, `/ml-model-coordination-groups`), FL/RL as data model.
Exit: `docs/STANDARDS.md` — 20/20 IOCs, 125/126 attributes; one deviation,
`MLTrainingFunction.ThresholdMonitors` (TS 28.623 containment).

### W5 — TS 28.104 at REST level (MDAF) — #142
MDAFunction/MDARequest/MDAReport with typed AnalyticsReport/PredictionReport/DriftReport;
`TRAFFIC_FORECAST` + traffic-trend PREDICTIONS_PM_DATA report via `sdk.analytics`; DriftReport →
AIMgF retrain notification. Exit: `docs/STANDARDS.md` — 48/48; deviations: addressing,
STREAMING transport.

### W6 — strict TS 28.312 (Intent Service) — #143
Intent, IntentReport (seven kinds), IntentHandlingFunction, IntentUtilityFormula; structured,
family-checked expectations; energy-saving template `sdk.intent.energy_saving_expectation`;
IntentReport carries action refs. Exit: `docs/STANDARDS.md` — 83/91 compliant, 8 partial
value datatypes; all callers migrated.

### W7 — runtime realization (MLTF/MLVF/MLEF/MLIF) — #144
Gaps found and closed: execution runtimes unsized (descriptor carried only `{jobKind, jobId}`), manifest without execution modes or
compute, runs that could stay IN_PROGRESS forever, late completions overwriting a finished run, unknown inference job → 500 (now 404
`INFERENCE_JOB_NOT_FOUND`). Open by design: Instantiate is synchronous (no async completion from NFO). Still open: runtime scaling
takes no target size (`OPEN_ITEMS.md` OI-7-nfo-scale-size). Per-execution-mode runtime profiles from the
rApp manifest carried to the NFO descriptor; stage timeouts (training 30 min, validation 15 min,
emulation 30 min, inference 5 s) → job FAILED + lifecycle FAILED event. Exit: tests green.

### W8 — autonomy modes — #145 (platform model #138)
- W8-07 SA SMOS O1-CM handler (`sa-smos/app/o1cm.py`): registers as RMIH `sa-smos` for
  `RAN_SUBNETWORK` expectations with `<IOC>.<attribute>` `IS_EQUAL_TO` targets; writes per-cell
  changes through `POST /dme/actions`; publishes an IntentReport; records `o1_cm_enactment`.
- W8-08 `POST /autonomy-dispatches/{id}/reject` → `REJECTED`; GUI Reject; BFF pins `rejectedBy`.
Exit: an AUTONOMOUS or resolved-ASSIST dispatch ends in an O1 change and a fulfilment report.

### W9 — multi-vendor O1 — #146
`ran-nf-oam/app/vendors.py`: `PUT/GET/DELETE /vendor-capabilities/{vendor}` (supported services gate
FM/PM/SWM/PROV, 409 `O1_SERVICE_NOT_SUPPORTED`; `supportedVendorModes`); `POST/GET /cm-schemas` with
the bundled TS 28.541 descriptor and a pre-write check (422 `SCHEMA_VALIDATION_FAILED`);
`POST /vendor-onboarding`; cell guards (`PUT/DELETE /managed-entities/{me}/cells/{cell}/guards`,
`GET /cell-guards`). Call flow 21. Exit: guide updated, battery green.

### W10.1 — EnergySaving rApp — #147
`samples/energy-saving-rapp/`. PRB PM → DME `PRB_UTILIZATION` (+ `_SIM`); threshold + regression
model with hour-of-day profile; sleep below 5 % sustained 60 min, wake above 15 % / neighbour > 80 % /
coverage alarm / override; hard, medium and soft guards; retries 0/5/10/20 s; read-after-write
verification; rollback. LOCK follows the autonomy mode; wake/rollback/override go straight to DME.
Exit review: 15/15 criteria, TC01–TC33 and Demo 00–11 green in CI. Deviations:
NRCellDU target (D-2), no LSTM, no `GET /dme/datasets/{name}`, simulated NETCONF timeouts.
**Not taken (W10-B1):** EnergySaving LSTM model variant (D-6 backlog); the shipped model is threshold +
regression. Approach if wanted: a second model type in the same package, compared in validation.

### W10.2 — Mobility Optimization rApp — #148
- D10.2-1 Per-relation `NRCellRelation.cellIndividualOffset` plus `DMROFunction` bounds.
- D10.2-2 Classified MRO (too-late/too-early/wrong-cell/ping-pong) + persistence regression.
- D10.2-3 Standalone sample `samples/mobility-optimization-rapp/`.
- D10.2-4 ±6 dB, 2 dB steps, 60 min pacing, ≥ 50 attempts; KPI-verified revert; EnergySaving
  coordination; `isHOAllowed` and protected cells.
RAN NF OAM `/pm-reports` accepts multi-counter `values` + `relation`. Exit review:
15/15, MRO-01..20 and Demo 00–11 green. Deviations: one CIO value written to all six QOffsetRange
entries; build-specific counter names.

### W10.3 — Coverage Optimization rApp — #149
- D10.3-1 `CommonBeamformingFunction.digitalTilt` and `NRSectorCarrier.configuredMaxTxPower`.
- D10.3-2 Joint neighbour optimisation over learned linear sensitivities (≤ 2 cells per pass).
- D10.3-3 Standalone sample `samples/coverage-optimization-rapp/`.
- D10.3-4 Tilt ±4°/1°, power ±3 dB/1 dB, 60 min, ≥ 100 reports; whole-set revert; EnergySaving and
  Mobility coordination; protected/alarmed cells.
Exit review: 15/15, CCO-01..20 and Demo 00–11 green on a closed loop over a
linear propagation model. Deviations: dBm power units, element-wide alarm hold, fixed 5 % thresholds.
**Not taken (W10.3-thresholds):** the objective uses a fixed 5 % threshold per problem class; per-cell,
per-class thresholds from the TS 28.541 CCO parameter sets are a refinement.

### W10.4 — Traffic Steering rApp — #150
- D10.4-1 Idle `NRFreqRelation.cellReselectionPriority` and connected `NRCellRelation` CIO; shared
  ±6 dB CIO envelope with two-way Mobility arbitration; `isMLBAllowed`/`isHOAllowed` respected.
- D10.4-2 Congestion score (0.5 PRB + 0.3 UE + 0.2 throughput deficit) + regression; offload ≥ 70,
  hold 50–70, release < 50; learned transfer per step.
- D10.4-3 Standalone sample `samples/traffic-steering-rapp/`; Mobility rApp gains optional
  `trafficSteeringInstanceId` (`MLB_OBSERVING`).
- D10.4-4 2 dB CIO / ±2 priority steps, 60 min, ≥ 10 samples; revert on target congestion, worse
  source or HO failure rise > 2 points; coordination; protected cells; target ≤ 55; 6 h anti-oscillation.
Exit review: 15/15, TS-01..20 and Demo 00–11 green. Deviations: linear load
model, region scope lists relations, layers from instance config, one knob per step.

### Work-item index

Code comments and docs cite wave work items by ID (`W9-02`, `W10.3-10`). One line each; the design
decisions behind them are in `docs/STANDARDS.md` (D-1…D-9) and the wave entries above.

- **W4-04** — Every TS 28.105 IOC at REST level (D-9), spec names/enums + notifications
- **W5-01** — TS 28.104 mapping matrix
- **W5-02** — Every TS 28.104 IOC/datatype at REST level (D-9); AnalyticsReport / PredictionReport / DriftReport as typed report kinds
- **W5-03** — `TRAFFIC_FORECAST` / TrafficTrendReport via `sdk.analytics`
- **W5-04** — DriftReport → AIMgF retrain signal
- **W6-03** — Energy-saving expectation template
- **W6-04** — IntentReport fulfilment linked to downstream actions
- **W7-03** — Per-mode runtime profiles (cpu/memory/gpu) from the manifest → NFO descriptor
- **W7-04** — Stage timeouts: training 30 min, validation 15 min, emulation 30 min, inference 5 s
- **W8-07** — Generic O1-CM Intent handler (D-1)
- **W8-08** — ASSIST reject (D-1b)
- **W9-01** — Capability Registry: per-vendor services, conformance mode, schema ref
- **W9-02** — `CMSchemaCache` bound to the registry; CM writes validated against the vendor schema
- **W9-03** — Vendor onboarding flow (discover → load → declare)
- **W9-04** — `O1_NETCONF` / `O1_RESTCONF` vendor modes
- **W9-05** — Exit
- **W9-06** — Cell guard attributes (D-5)
- **W10-01** — Package → `energy-saving-rapp.csar` (manifest, capabilities, model + four logic files)
- **W10-02** — Energy model: threshold + linear regression for next-hour PRB → `{futurePrb, recommendedState, confidence}` (D-6)
- **W10-03** — SDK wrappers (D-4)
- **W10-04** — PRB utilization PM → RAN NF OAM → DME `PRB_UTILIZATION`, sample producer
- **W10-05** — Synthetic `PRB_UTILIZATION_SIM` (D-7)
- **W10-06** — Consume MDAF prediction via `sdk.analytics`
- **W10-07** — Full AIMgF lifecycle REGISTERED → … → PROMOTED on MLTF/MLVF/MLEF
- **W10-08** — MLIF deploy AIMgF → NFO → runtime ACTIVE (MLLF checks CERTIFIED)
- **W10-09** — Inference via `POST /models/{id}/inference-jobs`
- **W10-10** — Pipeline: input → prediction → safety → decision → O1 execution → verification → audit
- **W10-11** — Sleep: PRB < 5 % for 60 min and all guards pass (D-3)
- **W10-12** — Wake: predicted PRB > 15 %, neighbour PRB > 80 %, critical coverage alarm, or operator override
- **W10-13** — Hysteresis: 5–15 % → NO_CHANGE
- **W10-14** — Hard / medium / soft safety guards, independent of AI confidence (W9-06 data + `/alarms`)
- **W10-15** — Operator override suppresses AI recommendations
- **W10-16** — Action path AutonomyDispatch → Intent → O1-CM RMIH → DME → RAN NF OAM → adaptor; actuator per instance (D-2)
- **W10-17** — Mock O1 adaptor models `NRCellDU.administrativeState`, `CESManagementFunction.energySavingControl/energySavingState`
- **W10-18** — Idempotency: skip if already in state; `actionId` dedup → IGNORED
- **W10-19** — Timeouts DME→OAM 10 s, NETCONF 30 s; retries immediate/+5/+10/+20 s; then `ACTION_FAILED` + alarm
- **W10-20** — Read-after-write verification → `VERIFY_FAILED` on mismatch
- **W10-21** — Rollback to UNLOCKED on VERIFY_FAILED / NETCONF_FAILED / PARTIAL_SUCCESS / neighbour congestion / coverage alarm
- **W10-23** — Audit trace chain joined by correlation id
- **W10-24** — GUI Energy Saving dashboard
- **W10-25** — Carrier-grade tests: false wake-up, neighbour overload recovery, read-after-write mismatch
- **W10-26** — Integration suite TC01–TC33
- **W10-27** — DEMO_RUNBOOK §24, Demo 01–11
- **W10-B1** — Energy model LSTM variant (D-6): PRB → PRB for the next N windows
- **W10.2-01** — Package → `mobility-optimization-rapp.csar`
- **W10.2-02** — Model `MobilityRobustnessPredictor` + logic files; JSON artifact in MLMR
- **W10.2-03** — Multi-counter PM (`values`, `relation`) in RAN NF OAM `/pm-reports`; `HO_PERFORMANCE` + `HO_PERFORMANCE_SIM`
- **W10.2-04** — Mock adaptor: `NRCellRelation` (`cellIndividualOffset`, `isHOAllowed`), `DMROFunction`
- **W10.2-05** — MRO engine: classification, thresholds (act ≥ 5 %, hold 2–5 %), step controller, pacing, guards
- **W10.2-06** — Actuation via AutonomyDispatch → Intent → O1-CM handler → DME → RAN NF OAM; reverts direct to DME; DMRO bounds at deploy
- **W10.2-07** — KPI-verified revert, else CONFIRMED
- **W10.2-08** — EnergySaving coordination over R1
- **W10.2-09** — Audit, dashboard, GUI **Mobility** page, BFF rules, R1 route, compose service
- **W10.2-10** — Integration tests, runbook §25, call flow 23, exit review
- **W10.3-01** — Package → `coverage-optimization-rapp.csar`
- **W10.3-02** — Model `CoverageSensitivityModel` (12 learned sensitivities) + joint optimiser + logic files
- **W10.3-03** — `COVERAGE_PERFORMANCE` PM (`MR.*`, CM snapshot) in DME; `COVERAGE_PERFORMANCE_SIM`; sample propagation model
- **W10.3-04** — Mock adaptor: `CommonBeamformingFunction`, `NRSectorCarrier`
- **W10.3-05** — Engine: guards, bounds, pacing, sample minimum → allowed moves
- **W10.3-06** — Actuation: one AutonomyDispatch per pass, one expectation per changed cell; reverts direct to DME
- **W10.3-07** — KPI-verified revert of the whole change set, else CONFIRMED
- **W10.3-08** — Coordination with EnergySaving and Mobility over R1
- **W10.3-09** — Audit, dashboard, GUI **Coverage** page, BFF rules, R1 route, compose service
- **W10.3-10** — Integration tests, runbook §26, call flow 24, exit review
- **W10.4-01** — Package → `traffic-steering-rapp.csar`
- **W10.4-02** — Model `CongestionSteeringModel` + logic files
- **W10.4-03** — `LOAD_PERFORMANCE` PM in DME; `LOAD_PERFORMANCE_SIM` with hotspots; sample load model
- **W10.4-04** — Mock adaptor: `NRFreqRelation` (`cellReselectionPriority`, `qOffsetFreq`), `NRCellRelation.isMLBAllowed`
- **W10.4-05** — Engine: thresholds, hysteresis, target and knob choice, guards, bounds, pacing, anti-oscillation
- **W10.4-06** — Actuation: one AutonomyDispatch per pass, one expectation per change; reverts direct to DME
- **W10.4-07** — KPI-verified revert, else CONFIRMED
- **W10.4-08** — Coordination with EnergySaving, Mobility (two-way CIO) and Coverage
- **W10.4-09** — Audit, dashboard, GUI **Traffic Steering** page, BFF rules, R1 route, compose service
- **W10.4-10** — Integration tests, runbook §27, call flow 25, exit review

## 10. Production readiness (closed features from `OPEN_ITEMS.md` §5)

### PR-ST-1 — Statelessness audit and guard

- **Audit (ST-1.1).** The table of what a process holds is in
  `docs/ARCHITECTURE.md`, "Process state and scale-out". Result: no service starts a thread, timer or
  task; the holders are `R1Client`'s per-process identity (fixed by `PR-ST-4`), the GUI BFF's lockout
  counters and its per-boot JWT secret (`PR-ST-5`), and a read-only `lru_cache` of the bundled CM schemas.
  The BFF's SME credential is already persisted in the database, so replicas share one identity there.
- **Guard (ST-1.2, ST-1.3).** `scripts/check_statelessness.py` parses `<module>/app`, `shared/smo_shared`,
  `sdk/smo_sdk` and `samples/*/app` and fails on module-level mutable containers, locks and thread-locals,
  a module-level instance of a stateful class defined in the same file, `app.state.x = <mutable>`, `global`,
  `lru_cache`/`cache`, and thread, task, executor, `BackgroundTasks`, `sched` or `apscheduler` use. Findings
  that are accepted are in `scripts/statelessness_allowlist.txt`, each with a reason; a stale entry fails the
  check. `tests_integration/test_statelessness_guard.py` runs it on the real tree and proves each rule fires
  on a seeded violation. CI runs it in the `lint` job (standard library only).
- **Time-driven behaviour (ST-1.4).** Nothing needs a periodic tick: endpoint health ageing (RAN NF OAM), A1
  service keep-alive, the rApp upgrade timeout, SA SMOS monitors, MDAF delivery and FOCOM intervals are all
  lazy, caller-driven or stored-only. Table in `docs/ARCHITECTURE.md`; one row in each module README.
  Consequence: `PR-ST-8` has no consumer yet and is deferred until a feature adds a periodic task.
- **Not taken.** ALL_CAPS names are trusted as constants and the instance-state rule only sees classes
  defined in the same file; a stricter rule (every module-level mutable regardless of case, or every
  imported class) was rejected as noisier than useful. `R1Gateway` is in the audit table by hand.

### PR-ST-2 — Optimistic concurrency on lifecycle rows

- **Mechanism (ST-2.1, ST-2.2).** `smo_shared/versioning.py`: the `Versioned` mixin adds
  `row_version INTEGER NOT NULL DEFAULT 1` and sets SQLAlchemy's `version_id_col`, so every ORM UPDATE or
  DELETE of the row is `... WHERE row_version = <loaded>`. A write that matches no row raises
  `StaleDataError`, which `install_concurrency_handler(app)` turns into `409 CONCURRENT_MODIFICATION`
  (ProblemDetails, same envelope as `problem()`; new `FrameworkError.CONCURRENT_MODIFICATION`).
- **Rollout (ST-2.1, 2.4 to 2.7).** `rapp_instance`, `application_package`, `nf_deployment`,
  `model_lifecycle` (model and runtime state in one row), `write_config_job` and `software_management_job`;
  columns added to `migrations/001_init.sql`; the handler installed in the five owning modules. Each module
  has a route test in which `smo_shared.testing.concurrent_commit_on(table)` lands another writer's commit
  between load and write: the route answers 409, and the repeat succeeds.
- **Proof (ST-2.3).** `shared/tests/test_versioning.py` runs on file SQLite and, with
  `SMO_TEST_POSTGRES_URL` (CI job `migration-postgres`), on real Postgres: two sessions firing one
  transition have one winner; eight threads racing one transition have one winner; the repeat after a
  conflict is refused as an illegal transition. Removing the mixin makes five of these fail.
- **Lazy sweeps (found on the way).** `rapp-mgmt`'s upgrade-timeout sweep writes from reads and from
  `upgrade/resolve`. `_sweep_overdue_upgrade` treats a lost race as "another replica already did it":
  reads carry on, `resolve` still answers `RAPP_UPGRADE_TIMED_OUT`. The stale write can surface inside the
  sweep's own flush, not only at the commit, so the helper wraps both.
- **SDK (ST-2.8).** `smo_sdk._common._RetryOnConflict` sends a mutating call once more on `409
  CONCURRENT_MODIFICATION` only: other 409s (illegal transition, name conflict) are final; reads and
  calls with `files` are not retried.
- **Not taken.** A check inside `StateMachine.fire()`: the FSM is a pure transition table whose callers
  assign the returned state, so the check belongs at the write, where it also covers non-FSM columns.
  `SELECT ... FOR UPDATE`: it would hold a connection and a row lock across the R1 calls many routes make
  before committing. Versioning the remaining job tables (`training_job`, `validation_job`, `emulation_job`,
  `inference_job`, the NRM processes): their transitions are driven by one external completion call each;
  add the mixin if a second writer appears. NFO `scale` needs no check: it returns the row to `RUNNING`
  within the request, so it writes no net change.
- **Known limit.** Side effects a route performs before its commit (a call to NFO, SME or DME) can run
  twice when the first attempt loses the race and is repeated, and the lazy sweep's teardown calls can run
  on two replicas. The remedy is the idempotency key of `PR-ST-3`.

### PR-ST-3 — Idempotency keys on commands

- **Mechanism (ST-3.1, ST-3.2).** `smo_shared/idempotency.py`: the `@idempotent(module, status_code)` route decorator
  (the route takes `request: Request` and `db: Session`) and the `idempotency_key` table (primary key module, caller, key;
  request hash, state, stored status and body, created_at; migration in `001_init.sql`). The first use commits an
  `IN_PROGRESS` reservation, runs the route and stores the 2xx answer as `COMPLETED`; a repeat gets that answer with
  `Idempotent-Replayed: true`. The key is scoped to the caller (the invoker id R1 Termination vouches for, `anonymous`
  without one), and the request hash covers method, path and payload, so another request under the same key is
  `422 IDEMPOTENCY_KEY_REUSED`. A repeat while the first runs is `409 IDEMPOTENCY_KEY_IN_PROGRESS`; an attempt that raises
  releases its reservation, so only 2xx answers are ever stored and a repeat after a failure (including a lost write race,
  `PR-ST-2`) runs again. A reservation older than `IDEMPOTENCY_IN_PROGRESS_SECONDS` (300) is taken over by a
  compare-and-swap, so exactly one replica takes an abandoned key.
- **Routes (ST-3.3 to ST-3.6).** `POST /rapp-mgmt/instances`; `POST /nfo/deployments` and `.../scale`; AIMgF `POST
  /training-jobs`, `/validation-jobs`, `/emulation-jobs`, `/models/{id}/inference-jobs`; `POST /ran-nf-oam/config-jobs`. Each has
  a route test that a repeat creates no second row (and, for config jobs, sends nothing southbound twice); an integration
  test through the shared database and NFO's real FOCOM call also shows the per-caller scoping.
- **Expiry (ST-3.7).** Records older than `IDEMPOTENCY_KEY_TTL_SECONDS` (86400) are deleted when a new key is reserved, so there
  is no scheduler and no extra write on a replay.
- **SDK (ST-3.8).** `smo_sdk._common._RetryOnConflict` adds a generated `Idempotency-Key` to every POST (not uploads, and a
  caller's own key wins) and the repeat after `409 CONCURRENT_MODIFICATION` reuses it. This needed `R1Client` to merge a
  caller's `headers=` with its own; the client's authorization and correlation headers win on a clash, and the extra headers
  survive the 401 refresh retry.
- **Proof.** `shared/tests/test_idempotency.py` on SQLite and real Postgres (CI `migration-postgres` job), including six
  threads racing one key (one execution; the rest replay or get 409). With the reservation logic removed, 16 of its tests fail.
- **Not taken.** Writing the stored answer inside the route's own transaction: routes commit internally and build their
  response afterwards, so the answer is stored right after the commit instead. A replica that dies in that window leaves a
  reservation that is taken over after the in-progress timeout, and the command can then run a second time; the window is
  documented in `ARCHITECTURE.md`. Storing 4xx answers: a deterministic refusal is cheap to recompute and storing it would
  pin a stale refusal. Versioning the header into OpenAPI: it is a platform-wide convention, documented once.

### PR-ST-4 — One module identity across replicas

- **The number (ST-4.1).** Eighteen processes call R1: fourteen platform modules (`a1-related`, `aimgf`, `dme`,
  `intent-service`, `mdaf`, `mllf`, `mlmr`, `nfo`, `onboarding`, `ran-analytics`, `ran-nf-oam`, `rapp-mgmt`, `sa-smos`,
  `so-smos`) and the four sample rApps. Each registered a fresh SME invoker on its first outgoing call after every start,
  so one restart of the stack added up to 18 registrations, and N replicas of a module added N. (Counted from the code; the
  stack was not run.)
- **Shared identity (ST-4.2).** `smo_shared/module_identity.py`: the `module_identity` table (migration included) and
  `DbIdentityStore` (`load`, `insert`, `replace`). `R1Client`'s onboarding takes the identity from `SMO_INVOKER_ID`/`SECRET`
  if set, else from the stored row for `MODULE`, else registers at SME and stores it. A replica that loses the primary-key race
  offboards its own duplicate and adopts the winner's. When SME refuses the stored invoker, one replica replaces it with a
  compare-and-swap on the old invoker id and the others adopt the replacement. No `MODULE`, `SMO_MODULE_IDENTITY_STORE=off`
  or an unreachable database falls back to the old per-process identity, so `R1Client` still never raises. The secret is stored
  as issued, like the BFF's `gui_smo_credential`: the module has to present it to SME.
- **Housekeeping (ST-4.4).** SME invokers carry `created_at` and `last_token_issued_at` (set at every token grant), and
  `POST /invoker-registrations/purge-stale?unused_for_days=N&dry_run=` offboards the ones unused for N days; `dry_run`
  defaults to true. A purged module is onboarded afresh by the replace path above. Tested against stale, recent and
  long-onboarded-but-active invokers.
- **Proof.** `shared/tests/test_module_identity.py` (store on SQLite and real Postgres, including eight threads racing an insert
  and a replace; `R1Client` against a fake SME: replicas and restarts share one invoker, a lost race offboards the duplicate, a
  forgotten invoker is replaced once). With the load-and-adopt step removed, 6 of its tests fail.
- **Not taken, and why.** The plan's ST-4.3, an SME registration that is idempotent on a stable label: SME keeps only a hash of
  the secret, so a repeat could not return it; rotating it on each repeat would make replicas invalidate each other; and anyone
  who knew `smo-module:<MODULE>` could take the module's identity. The registration stays "always creates", as in the CAPIF
  reference. The plan's init step that registers one invoker per module before the service starts: ordering across the compose
  and Helm start-up, and nothing for a replica added later; registering lazily in the first replica that needs it covers both.
  Storing the secret hashed or in a secret manager: `SEC-4` is where a managed secret would replace the column.
- **Known limits.** A losing replica's duplicate is removed best-effort (a failed delete leaves an orphan the purge removes).
  Anything that can read the shared database can read module secrets, which the shared database already allowed
  (`DB-2` narrows it). A database volume created before this change lacks the new table and columns; the README now says to
  recreate it until `PR-OPS-1` lands.

### PR-ST-5 — GUI BFF without per-process state

- **Session signing key (ST-5.1).** With `GUI_JWT_SECRET` unset the BFF no longer signs with a random per-process
  value: the first instance stores the one it generated in `gui_setting`, and every instance of that database (and every
  restart) reads it back, so a session from one instance is accepted by another and survives a restart. An explicit
  `GUI_JWT_SECRET` is used as is and stores nothing. Chosen over the plan's "require an explicit shared value" because that
  would break the documented throwaway quickstart (`GUI_JWT_SECRET` has always been optional).
- **Login lockout (ST-5.2, ST-5.3).** The `app.state.login_failures` dict is gone; failures are rows in `gui_login_failure`
  (username, count, window start), counted with atomic SQL (`count = count + 1`, a restart of an expired window, an insert
  that falls back to counting when two instances insert at once), so concurrent instances lose no failure. Chosen over the
  plan's two columns on `gui_user`: the counter is keyed by the name as typed, so an unknown username locks exactly like a
  real one and the lockout does not reveal which usernames exist (the old map had the same property). The new tables are
  created by the BFF's existing `create_all`, so no existing database needs altering.
- **Found on the way, same class of problem.** (1) Two instances onboarding at SME at once each registered an invoker and
  the last writer won the `gui_smo_credential` row: now an insert-or-adopt with a compare-and-swap replace, and the loser
  offboards its duplicate (as `PR-ST-4` does for the modules). (2) Two instances seeding one empty database crashed the
  second on a duplicate key and left a generated-password file that did not match the stored admin: now the loser keeps the
  winner's users and removes its own file. (3) Instances starting together all run `create_all` on a shared database, whose
  check-then-create is not atomic: `Database()` now looks again after losing that race.
- **Proof.** `gui-bff/tests/test_shared_state.py`: instances on one database file share the key, the lockout and the SME
  credential; two instances on different explicit secrets do not accept each other's sessions (the control); an old database
  gains the new tables; the stored-setting, failure-counting and credential operations race on SQLite and real Postgres
  (CI `migration-postgres` job). With the shared key and the credential race handling broken, 5 of its tests fail.
- **A latent flake, fixed.** `test_a_tampered_session_token_is_rejected` overwrote the last two characters of the signature
  with `AA`; the last character of a 43-character base64url signature carries only 4 data bits, so about one token in a
  thousand came out unchanged and still valid (measured: 21 of 20,000). It failed once in this PR's CI. It now changes one whole
  character in the middle of the signature (0 of 20,000 verify).
- **Not taken, and still open.** Server-side logout revocation (a JWT stays valid until `exp` or a `token_version` bump;
  `SEC-7.4`). Running several instances on the default SQLite file: it belongs to one instance, so instances need one shared
  `GUI_DATABASE_URL`, which the README now says. A generated admin password with several instances: each writes its own
  password file, so set `GUI_ADMIN_PASSWORD` when seeding a shared database.

### PR-ST-6 — Pool, timeouts and shutdown

- **Pool and server-side limits (ST-6.1, ST-6.2).** `smo_shared/db.py` builds the engine from `engine_options()`: `pool_size`
  (`SMO_DB_POOL_SIZE`, 5), `max_overflow` (10), `pool_timeout` (30 s), `pool_recycle` (1800 s), and on Postgres a
  `statement_timeout` (30 s) and `idle_in_transaction_session_timeout` (300 s) sent as connection options. `0` turns a limit
  off; SQLite keeps only `pool_pre_ping`. The idle-in-transaction default is longer than any request so it only catches a
  leaked transaction. Proof on real Postgres: a `pg_sleep` is cancelled, an idle transaction is ended, and the control
  without limits is not.
- **One timeout for every outbound call (ST-6.3).** `smo_shared/timeouts.py` holds the three values (call 30 s, R1 upstream 60 s,
  introspection 5 s; each overridable by env) and they nest: a module's R1 call outlasts R1 Termination's upstream call, which
  outlasts the introspection. `R1Client` now sets a default timeout on every call. A grep-style AST test
  (`tests_integration/test_http_timeouts.py`) fails on any `httpx` call or client in service code without `timeout=`.
- **Found on the way.** R1 Termination's proxy used httpx's implicit 5 s timeout and let any upstream error escape as a 500.
  It now answers 504 `UPSTREAM_TIMEOUT` and 502 `UPSTREAM_UNAVAILABLE` (flat bodies, as the rest of R1).
- **Workers and drain (ST-6.4, ST-6.5).** The Dockerfile runs `exec uvicorn ... --workers $UVICORN_WORKERS
  --timeout-graceful-shutdown $UVICORN_GRACEFUL_SHUTDOWN_SECONDS` (1 and 20 s), so SIGTERM reaches uvicorn, and every service in
  compose gets `stop_grace_period: 30s`. `tests_integration/test_graceful_shutdown.py` runs that CMD, signals it mid-request and
  asserts the request completes, new connections are refused or reset, the exit is bounded, and the compose grace exceeds the
  drain; without `exec` all four fail. uvicorn re-raises SIGTERM after draining, so the exit status is -15 / 143, not 0; with
  several workers new connections may be accepted and then reset during the drain.
- **Not taken.** Per-module pool sizes (the env is per container already); more than one worker for the mocks (module-level
  state, out of scope).

### PR-ST-7 — Readiness vs liveness

- **One router for every service (ST-7.1, ST-7.5).** `smo_shared/health.py` `install_health(app, checks)` adds `/live` (always 200),
  `/ready` (runs the checks, 200 or 503 naming the failing one) and keeps `/health` as an alias of `/live`, because DME's producer
  supervision, the GUI module grid and the runbook already call it. Adopted by all 16 backends, R1 Termination, the four sample rApps and
  the two mocks (which had no probe at all); the GUI BFF is not a module behind R1 and keeps its own surface. The per-module `/health`
  handlers and their copies of one docstring are gone.
- **Checks (ST-7.2, ST-7.3).** `database_check` (`SELECT 1`) on every service with a database, `sme_token_check` on every caller of R1
  (the cached token, so a probe costs nothing; SME down and no cached token means not ready). SME itself skips the token check (it is
  the issuer, and R1 depends on it), and so does focom, which calls nobody. Checks run in parallel and are bounded by
  `READY_CHECK_TIMEOUT_SECONDS` (3) so a hung dependency is a `timeout` in the answer, not a hung probe. A failing check shows its
  exception class, never its message, since a database error message can carry the connection string.
- **R1 Termination.** `/live`, `/ready` and `/health` are public in its OpenAPI like `/health` was; it is ready whenever it is live: it
  has no database and no state, and an SME outage already shows as 401 at the gateway and as every module's own `/ready`.
- **Compose (ST-7.6).** Every service built from the shared Dockerfile (all but the GUI BFF) has a healthcheck that calls `/ready`
  with Python (the image has no curl), so `docker compose ps` shows what can take traffic. Nothing waits on them yet:
  `depends_on` still gates only on Postgres.
- **Proof.** `shared/tests/test_health.py` (checks, timeout, parallelism, real SQLite and Postgres, a closed Postgres port) and
  `tests_integration/test_probes.py` (every loaded service: 200 when dependencies answer, 503 for every database service with the
  database down while `/live` and `/health` stay 200, 503 without an SME token for R1 callers, the gateway's probes need no token, and
  compose probes `/ready` on every shared-Dockerfile service).
- **Not taken, still open.** ST-7.4, the schema-at-head check, needs `OPS-1.2` (there is no schema version to compare to yet); it
  stays in `OPEN_ITEMS.md` as one function to add to `install_health`'s list. Gating `depends_on` on `service_healthy` for the
  modules: the start-up order is not a problem today, and a not-ready SME would stall the whole stack.

### PR-MSG-4 (scheduler part) — the worker

- **What.** `smo_shared/worker.py`: `Task(name, interval_seconds, fn)`, `tick(tasks, ...)` (offers every task once to `run_once_per_interval`, one
  failing task does not stop the others) and `python -m smo_shared.worker`, the loop (`SMO_WORKER_TICK_SECONDS`, a per-worker back-off for a task that
  failed, a heartbeat file, stop on SIGTERM after the task in hand). A module declares `TASKS` in `app/tasks.py`. Compose runs it as
  `ran-nf-oam-worker` (same image, `command` overridden, same secrets and so the same module identity as the API, ST-4); the healthcheck is the
  heartbeat file's age. This is the first caller of `PR-ST-8` (ST-8.3, adoption, for RAN NF OAM).
- **First users.** The wave advance (`advance_due`, which `POST /config-jobs/advance-due` now also calls), `kpi_schedule` (a table and
  `PUT/GET/DELETE /kpi-schedules`; revision 0019) and the refusal purge (`SAFEGUARD_REFUSAL_RETENTION_DAYS`, default keep). Each task finds what is
  due from the database and commits in steps, so a crash and a rerun do no harm. A failing KPI schedule is marked `ERROR` and waits for its next
  interval.
- **Chosen over the plan's generic `job` table (MSG-4.1) for these users:** all of them are periodic or due-by-timestamp, not queued work with a
  payload, progress and a cancel flag, and the claim of ST-8 already gives "at most once per interval, a dead holder frees it". The `job` table,
  cancel and lease-resume (MSG-4.1 to 4.4) are still open for the queue-shaped users (MSG-4.5 southbound sub-changes, MSG-4.6 software
  management).
- **KPI guard (the second PR of this feature).** `kpi-check` on a timer needs a job id, a KPI and a revert decision per job, so the job carries
  them: `kpiGuard` on `POST /config-jobs` (`write_config_job.kpi_guard`, `kpi_guard_result`, `kpi_guard_checked_at`; revision 0020, nullable
  columns). The worker task `run-kpi-guards` (every minute) takes finished jobs (COMPLETED, PARTIAL_SUCCESS) whose observation window has
  passed and runs the shared `_kpi_check` (the route's own body, extracted) with the job's settings, `requestedBy` `kpi-guard:<requester>`, and
  `revert` only if the job asked. **The revert is never forced**: a value changed since the job leaves it unreverted and the result says why
  (final, REGRESSED, `reverted` false), because an automatic override of somebody's later change is not a decision a timer should take.
  OK and REGRESSED are final; INSUFFICIENT_DATA or a transient error is retried each run until `RAN_NF_OAM_KPI_GUARD_GRACE_MINUTES` (60) after
  the window, so late PM files are still seen and a KPI with no data does not make the worker look for ever. `revert` defaults to false: an
  unasked-for revert is the riskier surprise. Proven by `ran-nf-oam/tests/test_kpi_guard.py` (12), and by mutation: ignoring the window, forcing
  the revert, and making every verdict final each fail a test.
- **Not taken.** The worker exports no metrics (it has no port). A worker is not started by anything
  but compose: the Helm chart (OPS-2) gets its own Deployment.
- **Proof.** `shared/tests/test_worker.py` (once per interval, two workers, one failing task, per-module names, the loop's back-off, heartbeat and
  stop), `ran-nf-oam/tests/test_tasks.py` (schedules end to end against a fake DME, the purge, the wave advance as the worker runs it, the task
  list) and a real run of the worker against Postgres: three tasks ran, a schedule with no KPI recorded `ERROR`, SIGTERM stopped it.

### PR-ST-8 — Single-runner guard

- **The helper (ST-8.1, ST-8.2).** `smo_shared/single_runner.py` `run_once_per_interval(name, interval_seconds, fn)`: across any number of
  replicas `fn` runs at most once per interval. The claim is one atomic `UPDATE periodic_run SET last_run_at = now WHERE name = ... AND
  last_run_at <= now - interval` on a row per task (created by the first caller, a lost insert race is harmless), so there is no leader to
  elect, no thread inside a service (the statelessness guard forbids one) and nothing to clean up after a crash. A raising `fn` gives the
  claim back, so a failed run does not use up the interval and the next tick retries.
- **Overlap.** `advisory_lock(name)` is a Postgres session advisory lock on a dedicated autocommit connection, held while `fn` runs: a
  run longer than the interval is not started a second time elsewhere (that caller gives its claim back and returns False), and the server
  frees the lock when the holder dies, which is the lease. An ordinary pooled connection would hold an open transaction that
  `idle_in_transaction_session_timeout` (PR-ST-6) ends, taking the lock with it, so the lock connection is autocommit. On SQLite (unit
  tests) the lock is always held.
- **Chosen over the plan's lease-only lock** for the interval itself: an advisory lock alone says who runs now, not whether the interval
  already ran, and would need the last-run time kept somewhere anyway; the row answers both and works on every database.
- **Proof.** `shared/tests/test_single_runner.py` on SQLite and real Postgres (CI `migration-postgres`): once per interval, again after it,
  independent tasks, a failure gives the interval back, six racing replicas run once; Postgres-only: two sessions and one lock, a dead
  holder frees it, and a long run is not started twice. Dropping the lock check or the interval condition fails the Postgres tests. The
  `periodic_run` table is in `migrations/001_init.sql` and in the migration-vs-models check.
- **Not taken, still open.** ST-8.3, adoption: RAN NF OAM adopted it through the worker (PR-MSG-4); any later periodic task does the same. Who ticks (a Kubernetes CronJob, an
  external scheduler) stays a deployment choice for the feature that needs it. Clock skew between replicas shifts a firing by the skew,
  which is fine for intervals of seconds and up.

### PR-ST-9 — Inline retry in the request thread

- **What was found.** RAN NF OAM dispatches each southbound sub-change inside the request that submitted the job, retrying after
  `0, 5, 10, 20` s (W10-19) with `time.sleep`. Nothing bounded the total: against an adaptor that waits out the 30 s exchange timeout
  one sub-change could take 4 x 30 + 35 = 155 s, and a job of N changes N times that, while R1 Termination answers 504 after 60 s
  (PR-ST-6) and the work carries on unseen by the caller.
- **The bound (ST-9.1, ST-9.2).** A retry budget per sub-change, `RAN_NF_OAM_DISPATCH_RETRY_BUDGET_SECONDS` (35): a retry is not started if
  the time already spent plus its delay would pass the budget; the first attempt is always made. The worst case per sub-change is the
  budget (at most the sum of the delays) plus one attempt in flight: `worst_case_dispatch_seconds()`, 65 s by default, stated in the
  README with the per-job multiplier and the advice for callers that must answer inside R1's 60 s (one change per job, budget 25 or
  less). The clock and the sleep are injectable (`_monotonic`, `_sleep`), so the tests assert the bound exactly.
- **Chosen: keep the documented schedule as the default.** 35 s equals the sum of the default delays, so a fast-failing adaptor
  (connection refused) still gets all four attempts exactly as W10-19 specifies, and only a slow one is cut (two attempts instead of
  four). The plan's "low default for synchronous callers" would have changed that specified behaviour for everyone; the env setting
  gives a synchronous caller the low value without it.
- **Proof.** `ran-nf-oam/tests/test_dispatch_reliability.py` on a fake clock: fast failures keep the whole schedule (35 s slept), 30 s
  attempts get two attempts and finish inside 65 s, no attempt duration from 0 to 30 s exceeds the worst case, a smaller budget stops
  earlier, the first attempt is made even with budget 0. Removing the budget check fails 5 of them.
- **Not taken, still open.** ST-9.3, moving the retries to the job runner so no `sleep` remains in a request path, needs `MSG-4.5` (there
  is no job runner yet). A job of several sub-changes still runs them one after the other in the request.

### PR-DB-1 — No default credentials

- **No default URL (DB-1.1).** `smo_shared/db.py` no longer falls back to `postgresql+psycopg://smo:smo@postgres:5432/smo`. With
  `SMO_DATABASE_URL` unset or blank, importing it raises `MissingDatabaseUrl`, whose message names the variable, shows the URL shape and
  points at `.env.example`, so a container exits at start instead of quietly talking to whatever answers on `postgres:5432` with a known
  password. `resolve_database_url()` is the single place that decides.
- **Tests (DB-1.2).** Under pytest an unset URL is an in-memory SQLite (`TEST_DATABASE_URL`), never a server. Chosen over setting the
  variable in every module's tests (about 25 places, and a new module would silently break): the unit suites already build the engine
  at import without connecting, so the detector (`"pytest" in sys.modules`) changes nothing for them, and a production image does not
  contain pytest. The two scripts that import the apps outside pytest: `generate_openapi_specs.py` sets `sqlite://` (it never connects);
  `check_migration_matches_models.py` now refuses to run without `SMO_DATABASE_URL` and no longer defaults to `smo:smo@localhost`.
  The CSAR parser fuzz target (ClusterFuzzLite) imports the onboarding app outside pytest and broke on the first CI run of this change; it
  now sets `sqlite://` before importing (it never connects), and a test requires that of every fuzz target that imports an app.
- **Compose (DB-1.3).** `POSTGRES_PASSWORD` and every service's `SMO_DATABASE_URL` read `${POSTGRES_PASSWORD:?...}` from `smo/.env`
  (copied from the committed `.env.example`, which holds a placeholder, not a usable password; `.env` is git-ignored). `docker compose
  config` fails without it; CI asserts that, then validates with `--env-file .env.example` and copies it to `.env` for the e2e stack.
  The password sits inside a URL, so it must be URL-safe (`openssl rand -hex 24`); the example says so.
- **Docs (DB-1.4).** README and GUI quickstarts, the demo runbook, the verification battery in `CLAUDE.md` (its local Postgres now uses
  a throwaway password, and applies the migration before the check, which the old text left out) and every module's config table.
- **Proof.** `shared/tests/test_db_url.py` (including a real subprocess that exits non-zero without the variable and starts with it) and
  `tests_integration/test_database_credentials.py` (no `smo:smo@` literal in service, shared or script source; compose requires the
  password on the database and on every service URL; `.env.example` names it and `.env` is ignored).
- **Not taken, still open.** Compose still publishes Postgres on host port 5432 and runs it as superuser `smo` for every module
  (`PR-DB-2` per-module roles; `PR-SEC-4` secrets manager replaces the `.env` file; `SEC-4.3`). The GUI BFF's own SQLite needs no password.
  A volume created with the old `smo` password keeps it: recreate the volume (`docker compose down -v`) or change the role's password.

### PR-DB-6 — Backup and restore (DB-6.1; 6.2–6.4 open)

- **The scripts (DB-6.1).** `scripts/db_backup.sh` writes one `pg_dump --format=custom` file (`--no-owner --no-privileges`); `scripts/db_restore.sh`
  puts it back with `pg_restore --clean --if-exists --exit-on-error --single-transaction`, so a restore that fails (a truncated or foreign file)
  changes nothing. Two modes: the host's `pg_dump`/`pg_restore` against `SMO_DATABASE_URL` (override the binaries with `PG_DUMP` / `PG_RESTORE`;
  they must be at least as new as the server), or `--compose`, which runs them inside the compose `postgres` container, always the right
  version for the server and the mode the stack's operator wants. `scripts/pg_env.sh` turns the SQLAlchemy URL into `PG*` variables, so the
  password is never on a command line (`ps`).
- **Safe by default.** The dump goes to `OUTPUT.partial`, is checked with `pg_restore --list` and only then renamed, so a failed dump leaves nothing
  that looks like a backup. The file is mode 0600: it holds every table, including each module's SME invoker secret. A restore refuses without
  `--yes`. The README says to stop the writing services first.
- **Proof.** `tests_integration/test_db_backup_restore.py` on a real Postgres (`SMO_TEST_POSTGRES_URL`): migration applied to a scratch database
  and seeded (including a value with quotes and non-ASCII), backed up, restored into an empty database and compared table by table (122
  tables); a second restore over a drifted live database brings the original data back; no `--yes` changes nothing; a truncated file
  changes nothing; a failed backup leaves no file; the password is never seen in a process listing. Dropping `--clean`, the `--yes` check or
  putting the password in `--dbname` each fails a test. The test skips when the client tools are older than the server (pg_dump refuses),
  which is the case on the CI runner (Postgres 16 client, 18 server), so CI covers the scripts in the compose e2e job instead: backup in
  compose mode, stop everything but the database, delete rows, restore, and compare the row count.
- **Not taken, still open.** `compose` mode could not be run in the sandbox this was written in (no Docker daemon): its first run is the CI e2e
  job. WAL archiving and point-in-time recovery (DB-6.3), a restore drill with timings (DB-6.4), and the CI job with a runbook smoke after the
  restore and a host-mode run against Postgres 18 (DB-6.2) are not done; a dump restores to the moment it was taken only.

### PR-DB-4 — Indexes and pagination (DB-4.1; 4.2–4.5 open)

- **Slow-statement log (DB-4.1).** The compose `postgres` service starts with `-c log_min_duration_statement=${POSTGRES_SLOW_QUERY_MS:-500}`: any
  statement slower than 500 ms is logged with its duration and text (`docker compose logs postgres | grep duration`). `POSTGRES_SLOW_QUERY_MS`
  in `.env` changes it (`-1` off, `0` every statement); `.env.example` and the README say so. The `command:` keeps the image's `postgres`
  entrypoint, so first-start initialisation (the migration in `docker-entrypoint-initdb.d`) is unchanged. A plain startup flag, not a
  `postgresql.conf` mount: nothing to keep in step with the image's version.
- **Proof.** `tests_integration/test_slow_query_log.py`: the compose command carries the setting with the 500 ms default and the documented
  override; a real Postgres accepts the exact option with each documented value (`SHOW` returns `500ms`, `0`, `-1`); a misspelt setting
  fails all four. The compose e2e job runs `SELECT pg_sleep(1)` in the container and greps the container log for the logged duration, so
  the whole path (flag, server, log) is checked on a real server.
- **Not taken, still open.** `auto_explain` and `pg_stat_statements` (the next step up from a log line; needs a preloaded library and an
  extension per database); `EXPLAIN` of the ten busiest list routes against a large table (DB-4.2, needs `QA-1.2` seed data), the
  indexes they show are missing (DB-4.3), and keyset pagination (DB-4.4, DB-4.5).

### PR-QA-6 — Authorisation matrix (QA-6.1; 6.2 open)

- **The walk (QA-6.1).** No backend checks a token; R1 Termination is the one enforcement point, so "is any route open?" is a question about the
  gateway. `tests_integration/test_authz_walk.py` answers it from the apps' real route tables: (1) the gateway's explicit routes are exactly
  `/health`, `/live`, `/ready` and `/bootstrap` (any other explicit route on it would be answered before the token check); (2) for every
  method-and-path of every backend (455 today, docs routes included), a request through the gateway with no `Authorization`, an empty one, a
  bare `Bearer`, a non-bearer scheme, a bare token without a scheme, or an inactive token is 401 and never reaches the backend, and with an
  active token it is forwarded exactly once (so the walk cannot pass by refusing everything); (3) every committed OpenAPI spec declares the
  `r1BearerAuth` requirement on every operation, with `security: []` only on the gateway's four public paths and SME's `/oauth2/token` and
  `/oauth2/introspect`.
- **Teeth.** The walker is a function (`unauthenticated_routes`) proved on a toy gateway with one seeded open path. On the real gateway, adding an
  explicit `GET /debug-routes` fails the first test and skipping the check for one prefix (`dme/data-jobs`) fails the walk; both reverted.
- **Found on the way.** Nothing open. One thing the first draft got wrong, worth keeping: SME's own `/oauth2/introspect` route and the gateway's
  introspection call have the same URL once the prefix is stripped, so the fake backend told them apart by the call shape (`json=` vs a forwarded body).
- **Not taken, still open.** QA-6.2 (the GUI BFF's role matrix). An unknown prefix is answered 404 `NO_ROUTE` before the token check, which tells an
  unauthenticated caller which prefixes exist (they are public in `/bootstrap`-adjacent docs); the walk does not treat that as open since no backend is reached.

### PR-SEC-13 — Container hardening (13.1, 13.2, 13.3; 13.4 open)

- **Non-root (SEC-13.1).** Checked first: every service built from the shared `Dockerfile` ran as root. The image now creates user `smo`
  (uid/gid 10001) and ends with `USER 10001:10001`, a numeric id so a runtime policy can verify it. `/srv` (code, the editable shared
  install) stays root-owned and read-only to it. The only places a service writes are `/data` (the GUI BFF's SQLite database and first-run
  admin password file) and `/srv/packages` (Onboarding's package volume); both are created and chowned in the image, and a new named volume
  takes its ownership from the image directory, so compose needed no `user:` or init step. A volume made by an older, root-run stack stays
  root-owned and needs `docker compose down -v` (README).
- **Capabilities and escalation (SEC-13.3).** Every service we build (the 24 on the shared image and the nginx GUI, which was already
  unprivileged) gets `cap_drop: [ALL]` and `security_opt: [no-new-privileges:true]` through one `x-hardening` anchor. Postgres keeps
  its defaults: its entrypoint drops privileges itself and needs a few.
- **Found on the way: NFO was `privileged: true` with `/var/run/docker.sock` mounted.** Either is root on the host. Nothing used them: NFO
  has never started a container (its README says no `docker run`; the only mention is a comment). Both are removed; the Docker bridge of
  SMO Design v1.3 section 3.7 is to be built as a narrow separate component (a socket proxy or the Kubernetes API), not by giving the
  service the socket. `cap_drop` next to `privileged` would have been a lie, since privileged grants every capability.
- **Proof.** `tests_integration/test_container_hardening.py`: the last `USER` is numeric, non-zero and before `CMD`; `/data` and
  `/srv/packages` are created and chowned and are the volume mount points; every built service drops all capabilities and sets
  no-new-privileges; no service is privileged, on the host network, PID or IPC namespace, adds a capability or mounts the Docker socket
  (this is the check that found NFO). Removing `USER` or adding `cap_add` to one service fails it. The compose e2e job is the runtime check
  (every service starts and passes the runbook replay as uid 10001); the sandbox had no Docker daemon, so the image build and run were
  first exercised there.
- **Not taken, still open.** `read_only: true` with `tmpfs` (SEC-13.2): the runbook `docker compose cp`s CSARs into `r1-termination:/tmp`, which
  does not work into a tmpfs, so the replay and the runbook need another way to serve packages first. The Helm chart (SEC-13.4, needs the
  chart). `seccomp`/`AppArmor` profiles, and image scanning (`PR-SEC-12`).

### PR-SEC-8 — Rate and size limits (8.1, 8.2; 8.3–8.6 open)

- **Body cap (SEC-8.1).** `smo_shared/bodylimit.py` `BodySizeLimit`, an ASGI middleware, answers `413 PAYLOAD_TOO_LARGE` before the service reads a
  body over the cap: from `Content-Length` when there is one, and by counting bytes as they arrive when there is not (a chunked upload), so a caller
  cannot dodge it by leaving the header out. It sits on R1 Termination (every API request passes it; the backends stay behind it): 1 MiB
  (`R1_MAX_BODY_BYTES`) for every path except `/mlmr/models/*/artifact`, the one route that carries a file, which gets 50 MiB like the GUI's nginx
  `client_max_body_size` (`R1_MAX_BODY_OVERRIDES`, `<path-pattern>=<bytes>`, `fnmatch` patterns, set to replace the default). The plan said "CSAR upload
  route higher"; there is none (Onboarding takes a package location), the model artifact upload is the real one. Settings are read per request.
- **Rate limit (SEC-8.2).** `smo_shared/ratelimit.py` `TokenBuckets`: one bucket per invoker id (the id R1 vouches for), refilled at `R1_RATE_PER_SECOND`
  (100) up to `R1_RATE_BURST` (200); an empty bucket is `429 RATE_LIMITED` with `Retry-After` in whole seconds to the next token. It runs after the
  token check, so an unauthenticated request spends nobody's budget (and is not limited here yet, SEC-8.3), and before the backend is called. `0` turns it
  off. Idle buckets are forgotten once they would be full, bounding the table by the callers active recently. Defaults are generous on purpose: the
  platform's own modules call through R1 constantly, each as its own invoker.
- **A known limit, stated where it lives.** The buckets are in the process, so N gateway replicas give a caller N times the rate until `SEC-8.5` (a shared
  store). `docs/ARCHITECTURE.md`'s process-state table has a row for it. The statelessness guard does not see it: it tracks classes defined in the
  same module, not an instance of an imported one, so the row is by hand (a gap in the guard, noted rather than fixed here).
- **Proof.** `shared/tests/test_bodylimit.py` (raw ASGI: exact at the cap, declared length refused before the app reads, chunked body stopped, overrides,
  a started response is not replaced) and `test_ratelimit.py` (fake clock: burst then rate, whole-second `Retry-After`, per-caller buckets, off at 0,
  idle eviction, eight threads never exceed the burst); `r1-termination/tests`: 429 after the burst with `Retry-After` and no backend call, one noisy
  caller does not starve another, unauthenticated requests spend no budget, 413 at 1 MiB and the artifact route's 50 MiB (and only that route), settings from
  the environment. Disabling the byte count, the refill clamp or the `>= 1` test each fails a test. The authorisation walk (`QA-6.1`) turns the limiter
  off: it makes thousands of requests as one caller.
- **Not taken, still open.** SEC-8.3 a stricter limit on unauthenticated paths (`/bootstrap`, the 401s), SEC-8.4 limits per route class, SEC-8.5 the
  shared store, SEC-8.6 the BFF login route; a per-route cap on the backends themselves (they are only reachable through R1).

### PR-SEC-4 — Secret management (4.1–4.3; 4.4–4.8 open)

- **Inventory (SEC-4.1).** `docs/SECRETS.md`: every secret with owner, how it is supplied, how it is stored (hash or plaintext), how it is rotated today
  and what is planned, plus where a secret must never appear and the database password's rotation steps. Writing it found one thing the plan did not
  list: AIMgF's `feature_group.token` (an InfluxDB token supplied by the caller) is stored in plaintext and returned by reads of the feature group. It
  is in the table, with the `SEC-4.6` pattern (a reference, not a value) as the fix; not changed here. The plaintext credentials the platform keeps
  by design (`module_identity.invoker_secret`, `gui_smo_credential`) are marked as such: a database dump or backup holds them.
- **The `*_FILE` helper (SEC-4.2).** `smo_shared/secretfile.py` `read_secret(NAME)`: the variable's value, or the contents of the file named by `NAME_FILE`; neither is
  None; both set is `SecretConflict` (which wins is not something to guess about a credential); an unreadable file is `SecretFileError` naming the variable and
  path, never contents; one trailing newline is removed and nothing else trimmed. `smo_shared/db.py` uses it for the URL (`SMO_DATABASE_URL` or
  `_FILE`) and the password (`SMO_DATABASE_PASSWORD` or `_FILE`), put into the URL percent-encoded with SQLAlchemy's own URL type, so any character in a
  generated password works.
- **Compose secret for the database password (SEC-4.3).** A top-level secret `db_password` from `secrets/db_password`, created once by
  `scripts/init_secrets.sh` (random 48 hex characters, never printed, an existing file is kept so a re-run cannot lock the stack out of its own
  database). Postgres reads it through the official image's `POSTGRES_PASSWORD_FILE`; every module gets `SMO_DATABASE_URL` with no password and
  `SMO_DATABASE_PASSWORD_FILE=/run/secrets/db_password`. This replaces the `${POSTGRES_PASSWORD:?}` of `PR-DB-1`, which kept the password out of the compose
  file but still put it in every container's environment (`docker inspect`, `/proc/<pid>/environ`); `.env.example` no longer has it. The directory is 0700
  and git-ignored; the file is 0644 because Compose bind-mounts it as it is and the services run as uid 10001 (the directory keeps other accounts out).
  CI checks the rendered compose config for a password and creates the secret before bringing the stack up.
- **Proof.** `shared/tests/test_secretfile.py` (including the percent-encoding of `p@ss/word:1`) and `tests_integration/test_database_credentials.py`
  (compose has no password literal and no `POSTGRES_PASSWORD:`; Postgres and every service that has a database URL has the secret and a password-less URL;
  `secrets/` and `.env` are ignored; `init_secrets.sh` creates mode-correct files once and never overwrites). The runtime path (Postgres reading the file, uid 10001
  reading a root-owned 0644 bind mount) could not be run in the sandbox (no Docker daemon): the CI compose e2e job is its first run.
- **Upgrade.** A Postgres volume made with the earlier password keeps it, and the generated one will not match: `docker compose down -v` (demo data) or set the
  role's password to the new value (`docs/SECRETS.md`).
- **Not taken, still open.** SEC-4.4 and 4.5 (the GUI passwords and session key, and the module invoker secret, through the same helper), 4.6 (adaptor
  credentials as references), 4.7 (External Secrets / Vault manifest, needs the chart), 4.8 (a rotation tried for each secret).

### PR-SEC-1 — TLS at the edge (1.1–1.5; 1.6 open)

- **Development certificates (SEC-1.1).** `scripts/make_dev_certs.sh [--force] [DIR]` makes a development CA (10 years) and a server certificate (365 days) with
  ECDSA P-256 keys, for `localhost`, `127.0.0.1`, `::1`, `r1-termination`, `gui` and any `SMO_TLS_NAMES` (DNS names or IPs). The server certificate is a
  leaf (`CA:FALSE`, `serverAuth`, SAN), signed by the CA; the CA key stays 0600 and is never mounted anywhere. An existing set is kept unless `--force`. The
  output directory `smo/certs/` is 0700 and git-ignored; `server.crt` and `server.key` are 0644 because Compose mounts them as secret files read by an unprivileged
  nginx (uid 101), the same trade as the database password file, and the directory keeps other accounts out.
- **One edge, not TLS inside each service (SEC-1.2, 1.3, 1.5).** The compose profile `tls` adds `edge-tls`, an unprivileged nginx (same pinned image as the GUI) with
  `cap_drop: [ALL]`: `https://localhost:3443` to `gui:8080` and `https://localhost:8443` to `r1-termination:8000`, certificate and key as Compose secrets. Chosen over
  uvicorn's `--ssl-*` flags on R1 Termination and a TLS server block in the GUI's nginx: turning TLS on in R1 itself would break every internal caller, the
  healthcheck and the SME bootstrap URLs (all `http://r1-termination:8000`), and a TLS block in the GUI's nginx config cannot be optional (nginx will not start
  with a missing certificate). So the plan's "`https://localhost:3000`" and "`https://localhost:8080`" became 3443 and 8443: the plain ports stay as they are and the
  profile adds a door; a deployment that should be TLS-only removes the `ports:` of `gui` and `r1-termination`. The default stack needs no certificate (the secrets
  are only used by `edge-tls`).
- **HSTS and the cookie (SEC-1.4).** Both doors send `Strict-Transport-Security: max-age=86400; includeSubDomains` (a day, not a year, so a development certificate does
  not pin a browser to HTTPS for localhost for months; the config says to raise it on a real edge). The GUI session cookie was already `Secure` by default
  (`GUI_COOKIE_SECURE`, true in compose, false only in CI, which has no TLS); a test now pins that default.
- **Proof.** `tests_integration/test_tls_edge.py`: the certificate chains to the CA and is not a CA, carries every name, is valid between 300 and 400 days, its key
  matches; the CA key is 0600 and the directory 0700; a re-run keeps and `--force` replaces; a real TLS handshake from Python succeeds for each name against the
  generated certificate and fails for a client without the CA; the nginx config offers only TLS 1.2 and 1.3 (allowing 1.1 or dropping HSTS fails a test), has
  exactly the two TLS doors and no plain listener, and proxies each to the right service; compose has the edge only under the profile, two published ports, hardening,
  both secrets, and no other service has a profile. The compose e2e job brings the edge up and checks over real TLS: `/bootstrap` through 8443, the SPA and HSTS
  through 3443, a client without the CA refused, TLS 1.1 refused. The edge itself could not be started in the sandbox (no Docker daemon, no nginx): that job is its
  first run.
- **Not taken, still open.** SEC-1.6: `/bootstrap` still advertises `http://sme:8000/oauth2/token` and friends, so an external rApp that bootstraps over HTTPS is
  then told to use HTTP. It needs the advertised base URL to be configurable (and the SME token endpoint reachable at the edge). mTLS between services is
  `PR-SEC-2`. Certificate rotation and a real CA (`SEC-1`'s production path) are the deployment's; OCSP stapling, HTTP/3 and a redirect from the plain ports are not done.

### PR-OBS-1 — Structured logs

- **One format (OBS-1.1, 1.2, 1.5).** `smo_shared/logconfig.py` `configure_logging()` puts one handler on the root logger: one JSON object per line on stdout with `timestamp`
  (UTC, milliseconds), `level`, `logger`, `service` (the container's `MODULE`), `message`, `correlationId` (the id `X-Correlation-ID` carries through the
  fan-out, read from the request context, so it is on every record logged while handling a request, including from libraries), `exception` (one field, not a
  traceback spread over lines) and every `extra=` key. `LOG_LEVEL` sets the level (default INFO; an unknown name falls back to INFO and says so). Uvicorn's own
  loggers are sent through the same handler; its plain-text access line is turned off (`--no-access-log` in the Dockerfile CMD, and the logger disabled) because
  the middleware below replaces it. The handler writes to whatever `sys.stdout` is at the moment of each record, so a test runner that swaps stdout never leaves it
  on a closed stream. Only this module's own handler is replaced on a second call; handlers that are not its own are left alone.
- **Access log (OBS-1.3).** `AccessLogMiddleware` (pure ASGI, so it sees the real status and the whole duration): one `smo.access` line per request with `method`, the **route
  template** (`/models/{model_id}`, `unmatched` if no route matched), `status`, `durationMs` and `correlationId`. The raw path and the query string are never logged:
  an id in a path, or a token in a query, would be. Probes (`/live`, `/ready`, `/health`) are DEBUG so a probe every few seconds from every container does not drown
  the log; a 5xx is ERROR.
- **Redaction (OBS-1.4).** A filter on the handler (so it covers uvicorn and third-party loggers too) scrubs, before a line is formatted: `Authorization`/`Bearer` values
  (keeping the scheme), `password=`, `secret=`, `token=`, `api_key=` style pairs and their JSON form, the password in a `scheme://user:password@host` URL, and any extra
  field whose name says it is a secret (also inside dicts and lists), in the message, its printf arguments, the exception text and the extras. It is a safety net
  for the mistake nobody meant to make, not permission to log a credential; the test seeds nine shapes of secret (the generic HTTP client logs request URLs, which is
  where a `token=` query would otherwise have leaked).
- **Adopted everywhere (OBS-1.6).** `install_logging(app)` after the `FastAPI(...)` line in the 16 backends, R1 Termination, the four sample rApps and the two mocks (23 apps),
  mechanically; it configures logging only if nothing has, so the order of calls does not matter. `tests_integration/test_logging_adoption.py` fails on a service without
  the middleware and checks one JSON access line per request, with the template and without the query.
- **Not taken, still open.** The GUI BFF keeps its own plain logging: it deliberately does not depend on `smo_shared` (its `main.py` says why), so it needs its own copy of the
  formatter or a decision to depend on the shared package; its `log.warning` lines name no credentials. Request and response bodies are never logged. Log shipping
  (`OBS-6`), traces (`OBS-3`), metrics (`OBS-2`) and a log-volume budget are not done; `correlationId` on records from background threads started by a handler would be
  empty (nothing starts one: the statelessness guard forbids it).

### PR-OBS-2 — Metrics (OBS-2.1–2.3)

- **Pinned (OBS-2.1).** `prometheus-client` is a direct dependency in `requirements/runtime.in`, compiled into `runtime.txt` and `dev.txt` with hashes (0.26.0), and in `shared/pyproject.toml`.
- **Two series (OBS-2.2).** `smo_shared/metrics.py` `MetricsMiddleware` (pure ASGI): `smo_http_requests_total` and the histogram `smo_http_request_duration_seconds`, labelled
  `method`, **route template** and `status`. Raw paths never become label values (bounded cardinality); unmatched requests are one `route="unmatched"` series. Probes and
  `/metrics` itself are not counted, so a scrape every few seconds is not most of the traffic. Held per process: with `UVICORN_WORKERS` above 1 a scrape sees one worker, so the
  default of one worker per container and scaling by replicas is the supported shape until multiprocess mode is added.
- **`/metrics` (OBS-2.3).** `install_metrics(app)` after `install_logging(app)` in all 23 apps: the Prometheus text format, not in the OpenAPI specs. It is for the scraper on the
  container network. R1 Termination answers `/<module>/metrics` with 404 `NO_ROUTE` before introspection or forwarding, so a token holder cannot read another module's series
  through the gateway (`tests_integration/test_metrics_adoption.py`, which fails with the guard removed); the TLS edge returns 404 for `/metrics` on 8443. R1's own `/metrics`
  is on its container port, which the development compose file publishes; production publishes only the edge (`PR-SEC-9`).
- **Adopted everywhere (OBS-2.7)** by the same change. **Not done:** DB pool gauges (OBS-2.4), FSM transition counter (OBS-2.5), business metrics and alerting (`OBS-4`, `OBS-5`).

### PR-OPS-1 — Alembic baseline (OPS-1.1–1.3)

- **Decision (OPS-1.1).** `docs/adr/0001-schema-migrations.md`: Alembic; **one history for the whole schema** (the tables are one schema with foreign keys across module boundaries, applied once by one job);
  hand-written revisions, no autogenerate (the revisions, not the models, define the schema; the models check is the safety net); migrations run by a job, never by N replicas at start-up. `001_init.sql` is
  frozen once released.
- **Baseline and stamping (OPS-1.2).** `migrations/versions/0001_baseline.py` runs `migrations/001_init.sql` unchanged, through the raw DBAPI cursor with no parameters (`op.execute(text())` reads the `:` of a CHECK as a bind parameter
  and `exec_driver_sql` makes psycopg read the `%` of `NOT LIKE '%:%'` as a placeholder; both failed). `scripts/migrate.py` upgrades to head (or `--revision`, `--current`): a database with the schema but no `alembic_version`
  (what compose's initdb makes) is **stamped** at `0001` first and then upgraded, an empty one runs the baseline, a migrated one is a no-op. Alembic's directory is `migrations/` itself (`env.py`, `versions/`), not `alembic/`,
  so it cannot shadow the package; `smo/alembic.ini`; `alembic` is a direct dependency in the hashed lock. `tests_integration/test_migrations.py` (on Postgres): a fresh database and a stamped one have the same columns,
  defaults and constraints; a second run neither restamps nor changes anything; history is one linear chain whose baseline is the SQL file.
- **Check at head (OPS-1.3).** `scripts/check_migration_matches_models.py` now requires the database to be at the migration head (it names `scripts/migrate.py` otherwise) and then compares columns and nullability as before;
  a model change with no revision fails it (tested by dropping a column after migrating). CI's `migration-postgres` job migrates the empty database with `migrate.py` instead of piping the SQL file to `psql`, then runs the check
  and the migration tests; `CLAUDE.md` step 4 does the same.
- **Not done:** the first real revision (OPS-1.4), a compose `migrate` service the modules wait for (OPS-1.5: compose still creates the schema from the file alone), upgrade-from-previous-commit in CI (OPS-1.6), the contributor
  rule that a schema change is a revision (OPS-1.7), and the schema-at-head readiness check (ST-7.4).

### PR-OPS-4 — Release scheme and changelog (OPS-4.1)

- **Scheme.** `docs/RELEASES.md`: Semantic Versioning with a `smo-v` tag prefix (the repository also holds the specification material outside `smo/`), one version for the whole platform (every image, the shared library, the SDK and the
  Alembic history move together), `-rc.N` for candidates, `0.MINOR` is the breaking number until 1.0. What each bump means is stated against what an operator or rApp depends on: R1 routes and fields, the CSAR manifest, configuration
  variables, and the schema (a revision is additive when the previous release's code runs on it; anything else is a MAJOR or an expand/contract split across two MINORs, `PR-OPS-5`). The release procedure is written down: rename
  `Unreleased`, merge as its own PR, annotated tag on the merge commit, tags never moved.
- **`smo/CHANGELOG.md`.** Keep a Changelog format, operator-facing (behaviour, configuration, schema), with the production-readiness work to date under `[Unreleased]`, including the changes an upgrading operator must act on (no default
  database password, non-root volumes to recreate, `migrate.py`). `tests_integration/test_changelog.py` keeps it well-formed: an `Unreleased` section first, semver dated headings newest first, a link per section.
- **Decision not taken:** the first tag. A person cuts it: a tag is an outward, effectively permanent act. It is `OPS-4.1b` in `OPEN_ITEMS.md`, with `smo-v0.1.0` proposed. Image publishing by tag (4.2), release notes (4.3) and the `SECURITY.md` table (4.4) are open.

### PR-OPS-1 (continued) and PR-MSG-1.2 — First revision, the migrate service, the contributor rule (OPS-1.4, 1.5, 1.7; MSG-1.2)

- **Revision `0002` (OPS-1.4, MSG-1.2).** `migrations/versions/0002_notification_outbox.py` creates `notification_outbox` (id, module, destination, payload JSONB, status `PENDING|SENT|DEAD` with a CHECK, attempts,
  next_attempt_at, last_error, created_at) and a partial index on `next_attempt_at WHERE status = 'PENDING'`, which is what a drain asks. It is the first revision and is additive (a new table nothing else reads), so the
  previous release runs on the new schema. `smo_shared/outbox.py` is the ORM model, imported by `scripts/check_migration_matches_models.py`. `enqueue` and `drain` (MSG-1.3, 1.4) and the modules' adoption (1.5+) are open. A real
  table was chosen over a throw-away column so the first revision is something the next feature needs anyway. `scripts/migrate.py` gained `--downgrade REVISION` (`-1`: one step); `test_migrations.py` takes a database from `0001`
  to head and back and finds the schema equal to one that never left `0001`, then forward again; its `HEAD` constant must be raised with every revision.
- **The `migrate` service (OPS-1.5).** A compose one-shot service (`restart: "no"`, hardened like the others, no published port) runs `python /srv/scripts/migrate.py`; every database-using service `depends_on` it with
  `service_completed_successfully` (one YAML anchor, `x-wait-for-schema`, instead of 19 copies). The Dockerfile copies `alembic.ini`, `migrations/` and `scripts/migrate.py` into every image, so the image of any release can bring
  the database to that release's schema; the service uses the R1 Termination build. Postgres no longer has `001_init.sql` mounted into initdb: an empty volume is migrated from the baseline, a volume made by an earlier stack
  is stamped at `0001` and upgraded (the case `migrate.py` already handled), an up-to-date one is untouched. If it fails nothing else starts and `docker compose logs migrate` says why. CI's compose-e2e job asserts the service
  exited 0 and the database is at the newest revision in the repository. Not verified outside CI: there is no Docker daemon in the development sandbox, so the compose path is first exercised by that job.
- **The rule (OPS-1.7).** `CLAUDE.md`, "Schema changes are revisions": a revision plus the model in one PR, never an edit to `001_init.sql` or an earlier revision, compatible with the previous release where possible, `HEAD` raised in
  the test, a `CHANGELOG.md` line. Upgrade-from-the-previous-commit in CI (OPS-1.6) is still open.

### PR-MSG-1 — Transactional outbox: inventory, enqueue, drain (MSG-1.1, 1.3, 1.4)

- **Inventory (MSG-1.1).** `docs/NOTIFICATIONS.md` lists every call to a caller-registered destination (20: 17 POSTs, 1 DELETE, 2 GETs) with a class: **A** a notification whose answer nothing reads (17, move to the outbox), **B** a command
  (the DME stop-job DELETE; stays inline until the outbox row can carry a method), **C** a read whose answer the caller uses (DME producer health, RAN NF OAM capability discovery; inline always). The worry that an RMIH
  callback might need its answer did not hold: no A-class site keeps the response. `tests_integration/test_notification_inventory.py` walks the AST of every `app/` and fails on a call site (or a use of `enqueue`) without a row, and on a row
  without a call site. Found on the way: MDAF sets `delivery.notified = True` whatever the answer was (after the move it means "enqueued"); SA SMOS has no registered-destination call at all.
- **`enqueue` (MSG-1.3).** `enqueue(db, destination, payload, module=None)` adds a PENDING `notification_outbox` row to the caller's session and flushes nothing: rolling back removes it (tested), and a rollback also forgets the
  session's pending ids. A destination the SSRF guard refuses is dropped with a warning at this point, as `post_webhook` did, and `drain` checks again at send time. `module` defaults to the container's `MODULE`.
- **`drain` (MSG-1.4).** An `after_commit` listener on SQLAlchemy's `Session` drains, in the committing thread, exactly the rows that session enqueued, which is what the inline `post_webhook` did minus its two failure modes (lost on a crash
  after the commit; sent although the request rolled back). It never raises into the caller. `drain(engine)` with no ids is the sweep a worker (MSG-2) or a recovery path runs: every due PENDING row, oldest first, then the retention
  purge of SENT rows (`SMO_OUTBOX_SENT_RETENTION_SECONDS`, default a day). A row is **claimed** by one atomic `UPDATE ... WHERE status = 'PENDING' AND next_attempt_at <= now` that also pushes `next_attempt_at` a 60 s lease ahead, so concurrent
  replicas never send a row twice (four threads, Postgres and SQLite, tested) and a process killed mid-send leaves a row that becomes due when the lease ends: **at-least-once**, so a consumer may see a notification twice after a crash.
  Outcomes: any answer below 500 is SENT (a 4xx is the destination refusing and a retry changes nothing); no answer or a 5xx counts an attempt and backs off (5 s, 30 s, 2 min, 10 min); the fifth failed attempt, or a destination the SSRF
  guard refuses at send time, is DEAD. `SMO_OUTBOX_INLINE_DRAIN=false` turns the inline drain off for a deployment that has a worker (MSG-2.5).
- **Decision not taken:** the inline drain sends only this commit's own rows and never retries failed ones, so one dead subscriber does not add its 2 to 5 s timeout to every later request; its rows wait for the sweep. Until MSG-2's
  worker exists, nothing runs that sweep, so a notification whose inline attempt failed stays PENDING (visible in the table) rather than being retried: no worse than the old single best-effort attempt, and recoverable later.
  The `UPDATE` uses `synchronize_session=False`: with the default, SQLAlchemy evaluates the WHERE in Python against identity-map rows and SQLite hands datetimes back naive, which raised comparing naive with aware.
- **Not done:** no module uses the outbox yet (MSG-1.5 onwards, one PR each), the worker (MSG-2), signing and the delivery log (MSG-5).

### PR-MSG-1.5 — DME callbacks through the outbox

- **Three sites moved** (`docs/NOTIFICATIONS.md`, class A): the type-registered / type-removed notification to every type subscriber, the offer-termination notice, and the job push to every supporting producer (on create and on update).
  Each is now `enqueue(db, destination, payload)`; the route's own `db.commit()` is the commit that also makes the rows durable and triggers the send.
- **Order changed, deliberately.** All three used to commit and then call the destination, so a crash in between lost the notification and nothing recorded that it was owed. The enqueue now comes before the commit in
  `register_dme_type`, `delete_dme_type`, `terminate_data_offer`, `create_data_job` and `update_data_job` (a `flush()` in `create_data_job` gives the job its id for the payload first). Observable behaviour is the same for a caller:
  the notification still goes out in the request thread right after the commit, and the 86 existing DME tests (which replace `httpx.post`) pass unchanged.
- **Left inline:** the stop-job `DELETE` (class B) and the health GET (class C).
- **Tests (3 new):** the MSG-1.5 crash test (with the inline drain off, as if the process died after the commit, the subscriber's notification is one PENDING row, nothing was sent, and a later `drain` delivers it and marks it SENT);
  one row per producer for a job push, none sent before the drain; a commit that fails leaves no row, sends nothing and un-registers the type too. The DME fixture creates the outbox table.
- **Differences to know:** the send timeout is now the outbox's 2 s (job push and offer termination used 5 s); a failed first attempt leaves the row PENDING with its attempt counted instead of being forgotten, retried by the sweep once MSG-2's worker exists.

### PR-MSG-1.6 — SME event subscriptions through the outbox

- **One site moved:** `_deliver` in `sme/app/main.py`, which every CAPIF event goes through (`SERVICE_API_AVAILABLE`/`UPDATE`/`UNAVAILABLE` and `API_INVOKER_ONBOARDED`/`UPDATED`/`OFFBOARDED`). It enqueues one row per matching subscriber;
  the filters (event types, `apiIds`/`aefIds`/invoker ids, the discovery-visibility gate) are unchanged and still applied at enqueue time.
- **Order changed:** the enqueue now comes before the commit in `register_service`, `deregister_service` (it already ran before the delete), `register_invoker`, `update_invoker` and `_offboard` (which also serves `purge-stale`).
  One thing needed care: `register_service` used to commit first, which expired the profile, so the visibility check in `notify_service_change` saw the authorization policy written a moment earlier. Before a commit that policy was
  cached as `None` (the request had just read it, found none and added one separately), so a gated service would have been announced to everybody. The route now flushes and expires `authz_policy` before notifying; with that line removed,
  an existing test and the new one fail (checked).
- **Tests (3 new):** the MSG-1.6 crash test (inline drain off: one PENDING row with the event and service id, nothing sent, a later drain delivers it); the visibility gate still hides a gated service from a subscriber outside
  `allowedConsumers` when the notification is enqueued in the registering request; a registration whose commit fails leaves no row, sends nothing and registers no service. The SME fixture creates the outbox table.
- **Differences to know:** send timeout is the outbox's 2 s (was 5 s); a failed first attempt stays PENDING for the sweep instead of being forgotten; at-least-once, so a subscriber may see an event twice after a crash.

### PR-MSG-1.7 — AIMgF notifications through the outbox

- **Two sites moved:** `_notify_job_completion` (training, validation and emulation completion, and the execution-timeout sweep for those and for inference jobs: one function, four call sites) and `report_performance`'s push to the
  MLMF subscription's destination. `_notify_job_completion` now takes the session and enqueues; the docstring says the caller must commit after it.
- **Order changed:** every caller enqueues before its `db.commit()`: `complete_training_job`, `complete_validation_job`, `complete_emulation_job`, and the timeout sweep, which used to commit and then loop over the expired jobs and now
  enqueues all of them and commits once. `report_performance` flushes the new report (for its id), enqueues, then commits. A completion and its notification are one transaction.
- **Tests (3 new):** the MSG-1.7 crash test (training completion with the inline drain off: one PENDING row with the job kind and id, nothing sent, a later drain delivers it); a performance report is committed with its subscriber push;
  a completion whose commit fails leaves no row, sends nothing and leaves the job unfinished. The AIMgF fixture creates the outbox table; all 195 existing tests (which replace `httpx.post`) pass unchanged.
- **Differences to know:** at-least-once; a failed first attempt stays PENDING for the sweep. The send timeout was already 2 s here.

### PR-MSG-1.8 — A1 Related, FOCOM and MDAF notifications through the outbox

- **Six sites moved** (`docs/NOTIFICATIONS.md`): A1 Related's policy-status notification; FOCOM's inventory notification, alarm notification (`fcaps._notify`) and performance report (`fcaps._report`); MDAF's analytics-report
  subscriber notification and its MDA request delivery (`_deliver`, two POSTs). Subscriber selection and filters are unchanged.
- **Order changed:** the enqueue now precedes the commit everywhere: A1's `update_policy` and `query_policy_status` (the status change and its notification commit together); FOCOM's provision and deprovision (a `flush()` gives
  the resource its id), alarm ingest (flush), acknowledge, clear, severity change, and performance ingest.
- **MDAF is one transaction now.** `publish_report` and `publish_mda_report` used to commit the report, then `_notify_report_subscribers` committed again (to persist `threshold_state`), then `_deliver` committed a third time. The report
  is now flushed, the subscriber rows enqueued (no commit there any more), and `_deliver`'s commit makes the report, the notifications, the threshold state and the request deliveries atomic. The cost: a failure in delivery matching
  now rolls back the report instead of leaving it stored without its deliveries. `delivery.notified` now means "enqueued".
- **Tests (6 new):** the MSG-1.8 crash test in each of the three modules (inline drain off: one PENDING row, nothing sent, the report/resource/policy change is there, a later drain delivers it); FOCOM alarm NEW and CLEAR notifications
  commit with their alarm; a FOCOM provision whose commit fails leaves no row, no resource and sends nothing; an MDAF publish whose commit fails stores no report and sends nothing. The three fixtures create the outbox table. One existing
  FOCOM test patched `app.fcaps.post_webhook`; it now patches `smo_shared.webhook.post_webhook`, the outbox's network seam. All other existing tests (which replace `httpx.post`) pass unchanged.
- **Differences to know:** at-least-once; a failed first attempt stays PENDING for the sweep; the send timeout is the outbox's 2 s (the same as these modules used).

### PR-MSG-1.9 — Intent Service and RAN NF OAM notifications through the outbox (closes MSG-1's adoption)

- **Four sites moved:** the Intent Service's RMIH notification (`_create_intent_row`), report delivery to `intentReportControl` recipients (`_deliver_report`, from three places) and the autonomy-operator notification
  (`_notify_autonomy_operator`, from dispatch, resolve and reject); RAN NF OAM's `notifyFileReady` to file subscribers (`report_pm_file`). SA SMOS, named in the plan, has no registered-destination call (see `docs/NOTIFICATIONS.md`), so there was nothing to move.
- **`_create_intent_row` no longer commits.** It used to commit the intent and its first report, then post, and the autonomy paths then committed the dispatch separately. It now enqueues and returns, and every caller commits:
  `create_intent` commits after it; `request_autonomy_dispatch` and `resolve_autonomy_dispatch` already committed after it, so the dispatch, the Intent it creates, their reports and all notifications are now **one transaction**
  (a dispatch can no longer survive with its Intent missing, or the reverse). `_deliver_report` and `_notify_autonomy_operator` take the session and enqueue; `update_intent_admin_state`, `publish_intent_report`, resolve, reject and
  request enqueue before their commit. RAN NF OAM enqueues one row per matching subscription before the commit that stores the file and bumps the subscriptions' sequence numbers, so the number a consumer is told is the number that was saved.
- **Tests (4 new):** the MSG-1.9 crash test in each module (Intent: the intent, its first report and the RMIH and recipient notifications; RAN NF OAM: the file, the sequence number and the notification; inline drain off: PENDING rows, nothing sent,
  a later drain delivers them); an AUTONOMOUS dispatch's operator notice, RMIH notice and Intent report in one transaction; a create whose commit fails stores no intent, no row, sends nothing. Both fixtures create the outbox table; one
  RAN NF OAM test patched `app.main.post_webhook` and now patches `smo_shared.webhook.post_webhook`.
- **Left:** `MSG-1.10`, a `method` column so the DME stop-job DELETE can use the outbox. With 1.9 every class-A notification in the platform is durable. Still open for delivery: the sweep (a worker, `MSG-2`), signing and a delivery log (`MSG-5`).

### PR-SB-3 — 3GPP common YANG as a library (SB-3.1–3.5; closes SA-O1-4)

- **Sources (SB-3.1, 3.2).** The 3GPP SA5 YANG set is in `specs/MnS/yang-models/` (134 modules, 3GPP's own README, with `external-yams/ietf-*`), added by the repository owner: the `_3gpp-common-*` modules
  (`-top`, `-managed-function`, `-managed-element`, `-ep-rp`, `-measurements`, `-yang-types`, `-subnetwork`, ...) and the `_3gpp-nr-nrm-*` and `_3gpp-5gc-nrm-*` modules.
- **A library mode, not a bigger input (SB-3.3).** `scripts/ingest_yang_schema.py` gained `--library <files or dirs>`: those modules supply groupings and typedefs, but their own lists and containers are **not** classes. Feeding the
  whole 3GPP set as input would have put every 3GPP IOC (the NR and 5GC NRMs, 134 modules) into every O-RAN descriptor. A definition in the input wins over a library one of the same name (first definition by name is kept, inputs first), and the
  library files that actually supplied something are recorded under `library` in the descriptor (6 for WG10, 1 each for the O-DU and O-CU). A name-keyed lookup remains the reader's limit: two modules defining one grouping
  name would be resolved by whichever is read first (none collide across the files used today).
- **Result (SB-3.4).** The four bundled descriptors (`o-ran-wg10-o1nrm`, `-wg5-du-mp`, `-wg5-cu-mp`, `-wg10-wg5`) are regenerated: `unresolved` goes from 4/4/1/1 entries to none; classes and revisions are unchanged (9, 41, 3 and 53 IOCs).
  They gain `id` and `userLabel` (`Top_Grp`), the EP and managed-function attributes (28 attributes for WG10, 2 each for the DU and CU), and four port numbers move from `any` to `integer` (`inet:port-number`, found in the
  library's `external-yams`). Nothing is removed. A vendor writing to those classes may now set `id` and `userLabel`.
- **Tests (SB-3.5).** The descriptor-vs-source integration test regenerates each YANG descriptor with the library and compares classes, `unresolved`, revision and `library`; two unit tests for the library mode (definitions only
  and input wins; unresolved without it); no bundled YANG descriptor has an unresolved grouping; `EP_E2` and `NearRTRICFunction` carry `id` and `userLabel`; the `ORU` class assertion now includes `id`.
- **Not done:** WG4 O-RU YANG (`PR-SB-4`), `when` / `must` evaluation, and the 5GC and NR modules as descriptors of their own.

### PR-SB-5 — YANG-validated writes (SB-5.1, 5.2)

- **Constraints in the descriptors (SB-5.1, part 1).** A descriptor attribute used to carry `type` and `enum` only, so an out-of-range number or a malformed string passed the schema check. `scripts/ingest_yang_schema.py` now also captures, per leaf:
  `range` (a list of `[lo, hi]` intervals; the leaf's own, else its typedef's, else the native bounds of `int8` ... `uint64`), `fractionDigits` for `decimal64` (and its `range`), and for strings `length` (intervals, `null` unbounded) and `pattern`
  (a list: a typedef's patterns and the leaf's own all apply). Typedef chains work: the most derived `range` / `length` replaces the inherited one, patterns accumulate; `min` / `max`, `|` alternatives, spaces around `..`, `+90.0` and hex numbers are read.
  `ingest_cm_schema.py` (OpenAPI) captures `minimum` / `maximum` / `minLength` / `maxLength` / `pattern` the same way. All five bundled descriptors were regenerated (61 attributes of the TS 28.541 NR NRM gained a range or length;
  nothing else changed).
- **The checker (SB-5.1, part 2).** `ran-nf-oam/app/leafcheck.py` `check_value(entry, value)` returns `None` or the reason. Integer: a JSON integer, an integer-valued string (RFC 7951 sends 64-bit integers as strings) or float, never a boolean,
  then `range`. Decimal: a number or numeric string, at most `fractionDigits` decimals, `range`. Boolean: `true`/`false` or those strings. String: a string (a number is read as its text, because the descriptor folds `union`, `leafref`,
  `identityref` and `bits` into "string"), `length` in characters, every `pattern` (anchored, as in YANG). Enum (any type): the listed members. Array, object, any: their JSON shapes. A pattern Python's `re` cannot read is skipped, not guessed at.
- **Run on every sub-change (SB-5.2).** `vendors.schema_problems`, which `POST /config-jobs` already ran on every change before creating a job or dispatching anything, now calls it (the old enum-only branch became one case of it). A job with
  one bad change in two sends neither; the 422 `SCHEMA_VALIDATION_FAILED` detail names the attribute, the value and the reason (`localPortNumber=70000 is out of range 0..65535`).
- **Tests.** 53 checker cases (`tests/test_leafcheck.py`); constraint capture from YANG (typedef chains, alternatives, patterns accumulating, invert-match skipped) and from OpenAPI; the end-to-end refusal for the WG10 descriptor (an `inet:port-number`), for the
  3GPP descriptor (`gnbIdLength` 22..32, `gnbDuId` 0..68719476735), a wrong type, and a two-change job where nothing is sent; the descriptor-vs-source integration test regenerates all of them. No existing write test needed a change.
- **Deliberately lenient:** a vendor's `union` leaf is never rejected for its type; an integer accepts `"7"`; unknown YANG `must` / `when` conditions are not evaluated.
- **Not done:** failure to `rejection_reason` codes (SB-5.3), the unknown-attribute policy flag (SB-5.4), `must` constraints (SB-5.5).

### PR-SB-1 (part 1) — NETCONF over SSH (SB-1.1, 1.2, 1.3, 1.5 wiring)

- **Library (SB-1.1).** `docs/adr/0002-netconf-over-ssh-client.md`: paramiko for the SSH session and our own framing and hello (the RPC builders and reply parsers are `netconf_client.py`'s, reused). Not ncclient (a large layer over paramiko that hides
  framing and timeouts), not scrapli-netconf (a plugin system for the same result), not asyncssh (asyncio only; every route here dispatches with blocking calls). `paramiko` joins `requirements/runtime.in` (with `bcrypt`, `pynacl`, `invoke`).
- **`transport` column (SB-1.2).** Revision `0003`: `o1_adaptor_endpoint.transport TEXT NOT NULL DEFAULT 'http-mock' CHECK (... IN ('http-mock','ssh'))`; existing rows are `http-mock`, the previous release ignores the column.
  Registration takes `transport`; `ssh` needs `o1Protocol` NETCONF and an `ssh://user@host[:port]` URI, and an `ssh://` URI needs `transport: ssh` (422 otherwise). `_o1_client(protocol, transport)` picks the client. Capability discovery
  (an HTTP GET) refuses an `ssh` endpoint with `PROTOCOL_NOT_SUPPORTED`.
- **Session wrapper (SB-1.3).** `app/netconf_ssh.py` `NetconfSession`: connect with a timeout, open the `netconf` subsystem, exchange `<hello>` (always end-of-message framed), use chunked framing when both sides offer base:1.1, else `]]>]]>`.
  A reply may arrive in any number of pieces; a reply over 16 MiB is refused. Host keys: an `NETCONF_SSH_KNOWN_HOSTS` file with `RejectPolicy`; refusal when none is configured (no way to skip the check).
  Reasons: timeout -> `NETCONF_TIMEOUT`, refused or broken connection -> `NETCONF_UNREACHABLE` (both retried by the existing policy); host key, authentication, missing subsystem, bad hello, `<rpc-error>` -> `NETCONF_RPC_FAILED` (not retried).
- **Read and write (SB-1.5 wiring; edit-config is also available).** `send_get_config` and `send_edit_config` have the shapes of the HTTP ones, so `POST /config-jobs` and `GET /managed-entities/{ref}/config` work over SSH unchanged.
- **Tests.** An in-process paramiko SSH server (`tests/netconf_ssh_server.py`) lets the wrapper and the routes run in the unit suite: 18 wrapper cases and 7 route cases.
- **Not done:** the `netconf-lab` compose profile and a run against netopeer2 (SB-1.4, and SB-1.5's "route returns data from the lab server"), `<rpc-error>` tag mapping (SB-1.7), candidate datastore (SB-1.8), credentials per endpoint and a pinning route (`PR-SB-2`).

### PR-MGT-3 and MGT-8.1 — dry run, and a 404 for an unknown alarm

- **Dry run (MGT-3.1–3.3).** `POST /config-jobs` takes `dryRun: true`. It runs the same MSAC, service-presence and data-model checks as a real write (the YANG leaf checks of `SB-5` included), so the same refusals come back as 403 / 422. When they pass it
  creates no job, sends nothing southbound and writes no outbox row; it answers 200 `{"dryRun": true, "status": "VALIDATED" | "WOULD_REJECT_SOME", "changes": [{managedElementRef, managedFunctionRef, operation, verdict: "PASS" | "WOULD_REJECT", reason}]}`.
  The verdict reuses the dispatch loop's own gate (`_dispatch_blocker`: no registered endpoint, endpoint `UNREACHABLE` / `DEGRADED` after aging, no client for the protocol), so a change a real write would reject at dispatch is reported as `WOULD_REJECT` with its reason, not `PASS`.
  The response is stored and replayed under an `Idempotency-Key` like any other. **Not checked:** whether the adaptor would accept the value (only a real write learns that).
- **Alarm 404 and ack state (MGT-8.1).** `PATCH /alarms/{id}/ack` and `/clear` on an unknown id raised `AttributeError` (a 500); they now return 404 `ALARM_NOT_FOUND` (new in `FrameworkError`). `new_state` is `ACKNOWLEDGED` or `UNACKNOWLEDGED` (what the table's CHECK allows), anything else 422 with nothing stored.
- **Tests.** `test_yang_schemas.py` (dry run: pass, YANG refusal, unknown attribute, unregistered element, nothing written or sent, the real write afterwards; MSAC denial), `test_main.py` (404 for both routes, invalid and valid ack states).

### PR-MGT-1 (MGT-1.1–1.4) — CM history: before and after images

- **Table (MGT-1.1).** Revision `0004` creates `cm_snapshot`: `sub_change_id` (unique, cascades with the sub-change), `job_id`, element, function, `operation`, `before` and `after` (JSONB), `before_error`, `created_at`, indexed by `(managed_element_ref, created_at DESC)`.
  Additive; the previous release ignores it. Model: `CMSnapshot` in `ran-nf-oam/app/models.py`.
- **Before image (MGT-1.2).** Just before dispatching a sub-change that passed every gate, `_capture_before` reads the object from the NF with the endpoint's own client (NETCONF `get-config`, over HTTP or SSH, or RESTCONF `GET`) and keeps the current values of the
  attributes the change names (`null` for one that is not there); a delete/remove, which names none, keeps the whole object. **A failed read does not stop the write**: `before` is `null` and `before_error` says `before-image read failed` (or `... raised <Type>`), so a missing image is never mistaken for an empty one.
  Chosen against refusing the write: an adaptor whose reads fail still has to be writable today; a flag to require the image belongs with the rollback guard (MGT-1.7).
- **After image (MGT-1.3).** What the NF acknowledged: the written values when applied (`null` for delete/remove), `null` when it refused. It is the requested values, not a second read, so a write costs one extra exchange, not two.
- **Time.** The read is one more exchange per dispatched sub-change: worst case per sub-change is now `worst_case_sub_change_seconds()` = `worst_case_dispatch_seconds()` + 30 s = 95 s by default (the dispatch bound itself is unchanged: 65 s). `RAN_NF_OAM_CM_SNAPSHOTS=false` switches the read and the table off.
- **Route (MGT-1.4).** `GET /managed-entities/{ref}/config-history?managed_function_ref=&limit=&offset=`: newest first, each item with `jobId`, `subChangeStatus`, `operation`, `before`, `after`, `beforeError`, `createdAt`. A change rejected before dispatch (endpoint down, no client) and a dry run have no row.
- **Tests.** `tests/test_cm_history.py` (11): both images, a refused change, a failed and a raising reader, a delete, ordering / filter / paging with each write's before equal to the previous after, nothing for a blocked change or a dry run, the off switch, the 95 s bound; the SSH route test checks the image read over SSH. `tests/conftest.py` stubs the HTTP read so unit tests never touch the network.
- **Not done:** snapshot diff (MGT-1.5), rollback (MGT-1.6), the changed-since guard (MGT-1.7), retention (MGT-1.8, needs DB-3.2).

### PR-OPS-10 (OPS-10.1, 10.2, 10.4) — the deploy gate on main

- **Workflow (OPS-10.1).** `.github/workflows/deploy-on-main.yml`, triggered by `push` to `main` (the repository's default branch is `main`, not `master`) and by `workflow_dispatch`: create the secrets, `docker compose up -d --build`, check the one-shot migrate service exited 0
  and the database is at the newest revision, the R1 gate fast check, the demo runbook replay (`tests_integration/test_demo_runbook.py` with `SMO_E2E_LIVE=1`), the GUI proxy check, logs and `docker compose ps` uploaded as the `deploy-gate-logs` artifact on failure, and
  `docker compose down -v` always. It is the same set of steps as the `compose-e2e` job of `smo-tests.yml` (which also runs on pushes to `main`, and is the required check on pull requests); they are repeated rather than shared because moving that job into a reusable workflow
  would rename a required check. **Honest note:** this means every merge runs the stack twice; the new workflow earns its place by its own name, concurrency rule, badge and failure notification, and it is the lane that grows into the upgrade and Helm lanes (OPS-10.5 onward).
- **Concurrency and time (OPS-10.2).** One concurrency group with `cancel-in-progress`, so two quick merges leave one run; `timeout-minutes: 25` (the PR job has 30).
- **Notification (OPS-10.4).** A `notify` job (`issues: write`, nothing else beyond reading) opens one issue labelled `deploy-gate` when the run fails, comments on it when the next run fails too, and closes it when a run is green; a cancelled run notifies nobody.
  The maintainers get it through normal issue notifications. `workflow_dispatch` with `seed_break: true` fails on purpose, which is how the notification is proven. The badge is at the top of `smo/README.md`.
- **Pinned action:** `actions/upload-artifact` v7.0.1 by commit, like the others (the repository pins every action by SHA).
- **Not done:** the headless GUI smoke check with screenshots (OPS-10.3), the upgrade lane (OPS-10.5), Helm on kind (OPS-10.6), the rolling-upgrade lane (OPS-10.7).

### PR-OPS-10.3 — headless GUI smoke check in the deploy gate

- `scripts/gui_smoke.py` (Playwright, Chromium, headless): opens `/login`, signs in as `admin`, waits for the dashboard's "Modules healthy" tile to read `n/n` (up to 60 s: the dashboard polls every 10 s and the stack may just have come up), then opens `/alarms`. Screenshots `01-login.png`,
  `02-module-status.png` (full page: the module-health grid), `03-alarms.png`, plus `99-failure.png` when something throws, are written even when a check fails. Exit 0 only if every module is healthy. `--allow-unhealthy` skips the `n/n` requirement (used to run the GUI without the stack).
- In `deploy-on-main.yml` the step installs `requirements/smoke.txt` (Playwright, hash-locked like the other requirement sets), `playwright install --with-deps chromium`, reads the generated admin password from the BFF container's mode-0600 file (it never reaches the log), runs the script against port 3000, and uploads the screenshots as the
  `gui-smoke-screenshots` artifact (always, 7 days).
- **How it was checked here:** the real GUI build and the real GUI BFF behind a small static-and-proxy stand-in for the nginx container, without the SMO stack: the flow ran end to end (login, dashboard, alarms), the strict mode failed with 0/21 healthy and wrote the failure screenshot, a wrong password failed on the login screen. The compose run itself (all modules healthy) is
  exercised only by the deploy gate.
- **Not done:** OPS-10.5 onward (upgrade lane, Helm on kind, rolling upgrade).

### PR-SB-1.4 — the netconf-lab profile

- **What.** `docker compose --profile netconf-lab up -d netconf-lab`: the `sysrepo/sysrepo-netopeer2` image (pinned by digest; Netopeer2 on port 830, user `netconf`, password `netconf`, a host key baked into the development image) with one small model, `smo-lab`
  (`netconf-lab/smo-lab.yang`: a list of cells with `administrative-state` and a ranged `tx-power`) and two seeded cells. `netconf-lab/start.sh` installs the model and its data (startup and running) before it starts the image's own supervisor, so nothing restarts. Published on `127.0.0.1:8830` only.
  A third-party development image that runs as root: it does not take the hardening anchor, which the hardening test only requires of the images this repository builds. `test_tls_edge.py` now lists the two profiled services.
- **The check.** `scripts/netconf_lab_check.py` connects with `NetconfSession` (the SSH wrapper), checks the `<hello>` (a base capability), reads the model with a subtree `<get-config>` and compares both cells' values. CI job "NETCONF lab (netopeer2)" in `smo-tests.yml`: start the profile, wait for SSH with `ssh-keyscan`
  (which writes the known_hosts file the wrapper insists on), run the script, dump the lab logs on failure, tear down. The script was run here against the in-process test server; the real server is only reachable in CI.
  **First CI run, against the real Netopeer2 image:** SSH connect, password login, host-key check, the `netconf` subsystem and the `<hello>` (27 capabilities, base:1.1 chunked framing) all worked. The script then failed on its own over-strict check that the hello advertises `smo-lab`: a YANG 1.1 module is listed in ietf-yang-library, not in the hello (RFC 7950 section 5.6.4), so that check is now only printed and the returned data is the proof.
- **What this proves, and what it does not.** It proves the wrapper's hello, framing and host-key check work against a real Netopeer2. It does **not** make `GET /managed-entities/{ref}/config` work against it: that route still sends the build's own `<managed-object ref=...>` shape, which a real server rejects. Mapping the route to a real model is the open part of SB-1.5.
- **Not done:** SB-1.5 (above), `edit-config` against the lab (NACM may refuse the lab user; SB-1.6), `<rpc-error>` tags (SB-1.7), candidate datastore (SB-1.8), the runbook step (SB-1.9).

### PR-OPS-1.6 — the previous schema upgrades to head, in CI

- `scripts/check_upgrade_from_previous.sh`: checks the previous commit (default `HEAD^1`: the base branch tip on a pull request's merge commit, the previous main commit on a push to main) out into a scratch worktree, migrates an **empty** database to *its* head with *its* `scripts/migrate.py`, then runs *this* commit's `migrate.py` and
  `check_migration_matches_models.py` against the same database. It catches a revision that only works on a fresh database (a column the previous release already has, a constraint older rows would break). A previous commit without `scripts/migrate.py` (before `OPS-1`), or no previous commit (a shallow clone of one), is reported and skipped.
- In CI: a step of the `migration-postgres` job (checkout with `fetch-depth: 2`), against a second database `upgrade` created for it. **Not a full upgrade test:** the database is empty (no rows to carry through a data migration), and only schema revisions are exercised, not the previous release's code against the new schema (`OPS-5.2`) or the runbook replay after an upgrade (`QA-4.1`, `OPS-10.5`).
- Checked here against `HEAD^1` (0004 to 0004) and against the commit where 0002 was head (0002 to 0004).

### PR-OBS-2.4, 2.5 — pool gauges and FSM transition counters

- **FSM counter (OBS-2.5).** One hook in `StateMachine.fire` (`smo_shared/statemachine.py`), so every machine of every module is covered without touching them: `smo_fsm_transitions_total{machine,from_state,event,to_state}` on a taken transition and
  `smo_fsm_illegal_transitions_total{machine,from_state,event}` on a refusal (no transition, or every guard said no). `machine` is the state enum's class name (`JobState`, `ModelLifecycleState`, ...), `from_state`/`event`/`to_state` are the enum values, so the label set is the declared states
  and events only. A guard that rejects counts as a refusal, a guard that passes with an action counts after the action ran.
- **Pool gauges (OBS-2.4).** A collector reads the module's own engine pool at scrape time (no cost on the request path): `smo_db_pool_connections{state="in_use"|"idle"|"overflow"}` and `smo_db_pool_capacity` (pool_size + max_overflow). `install_metrics` registers it once per process, and the collector takes the engine from `smo_shared.db` **only if the process has already imported it** (it never imports it itself: building that engine needs database credentials, and R1 Termination and the mock services have none; the first version imported it and the compose stack failed to start, which only the compose job could show).
  **No "waiting" gauge:** SQLAlchemy does not expose the number of callers blocked on the pool; an exhausted pool is `in_use` equal to `smo_db_pool_capacity` (and the callers then fail with the pool timeout, `PR-ST-6`). A pool that is not a `QueuePool` (the SQLite test engines) reports nothing.
- **Tests.** `shared/tests/test_metrics.py`: transitions counted by machine / state / event / target, a refusal and a guard rejection in the refusal series, the pool gauges following checkouts and returns of a real `QueuePool` (3 out with one overflow, then 2 idle), no series for a pool without those counts, and a service without database credentials installing metrics in a clean subprocess.
- **Not done:** outbound call metrics (OBS-2.6), the Grafana dashboard (OBS-2.8).

### PR-MSG-1.10 — DELETE rows: the DME stop-job DELETE moves to the outbox

- **Column.** Revision `0005`: `notification_outbox.method TEXT NOT NULL DEFAULT 'POST' CHECK (method IN ('POST','DELETE'))`. Additive; every existing row is a POST, and the previous release ignores the column. `enqueue(db, destination, payload, module=None, method="POST")` refuses any other method; a `DELETE` row carries `{}` as its payload.
  `_send` calls `delete_webhook` (2 s) for a DELETE row and `post_webhook` for a POST; retries, backoff, lease and DEAD handling are the same for both.
- **DME.** `_stop_job_at_producers` enqueues one DELETE row per supporting producer (`<jobCallbackUrl>/<jobId>`) **before** the commit that deletes the job; `terminate_data_job` and `terminate_data_jobs_for_consumer` used to commit first and call the producers after, so a crash in between left a producer running a job DME no longer had.
  The commit now carries the deletion and the stop requests together. The timeout on the producer call is the outbox's 2 s (it was 5 s inline).
- **Inventory.** The one class-B site is now `enqueue`, class A (`docs/NOTIFICATIONS.md`, guarded by `test_notification_inventory.py`); the only inline calls left are the two class-C reads, which stay inline because the caller needs the answer.
- **Tests.** Shared: a DELETE row goes out after the commit as a DELETE and never as a POST, a row without a method is a POST, an unknown method is refused, a rolled-back DELETE is never sent, an unreachable one is retried and then DEAD (SQLite and Postgres). DME: deleting a job leaves one PENDING DELETE row per producer and sends nothing until the drain, which then sends them (the crash test pattern).

### PR-SEC-1.6 — /bootstrap behind the TLS edge

- **The problem.** `/bootstrap` told every consumer to use `http://sme:8000/...`: an address that exists only on the compose network, so a consumer that reaches the platform through the HTTPS door (`https://host:8443`) could not follow it, and the token endpoint it names is the one that must be reachable before a token exists.
- **The setting.** `R1_PUBLIC_BASE_URL` (r1-termination; compose passes `${R1_PUBLIC_BASE_URL:-}`, `.env.example` documents it): an origin such as `https://localhost:8443`. Unset, nothing changes (SME's address on the compose network). Set, `/bootstrap` advertises the gateway's public address: the API entries as `<base>/sme/service-apis/v1/allServiceAPIs` and
  `<base>/sme/published-apis/v1` (through R1, token required) and the token endpoint as `<base>/sme/oauth2/token`. **It is configuration, never derived from request headers:** the token endpoint is where an rApp sends its client credentials, so a Host or X-Forwarded-Host an attacker chose must not decide it (a test sends hostile headers). A value that is not an origin
  (no scheme, a non-http scheme, a path, a query) stops the service at start.
- **The edge.** `edge/nginx.conf` forwards exactly one path around R1's token check, `= /sme/oauth2/token` to `http://sme:8000/oauth2/token` (a test fails if the edge forwards any other path straight to SME); everything else under `/sme/` goes through R1. `edge-tls` now depends on `sme`.
- **The audience.** A client assertion's `aud` must be the token endpoint the client was told (RFC 7523), and SME checks it against `SME_TOKEN_AUDIENCE`. Compose sets it to `<R1_PUBLIC_BASE_URL>/sme/oauth2/token` when the base URL is set, else leaves it empty, which SME now treats as unset (it used `os.environ.get(..., default)`, where an empty string would have won). The shared `environment:` block of the
  database-using services moved to a top-level `x-db-env` anchor so SME can add its own variable.
- **Tests.** Unit: the two bootstrap modes, hostile headers ignored, origin validation (7 cases); integration: the edge forwards only the token path to SME and compose carries the setting. CI (the TLS step): with the setting on, `/bootstrap` over HTTPS names `https://localhost:8443/sme/oauth2/token`, a malformed POST to it is answered by SME (422/400), `/sme/service-apis/...` without a token is 401 from R1, SME's audience is that URL; then the default is restored.
- **Not done:** the full rApp runbook over HTTPS (the replay still runs against the plain ports); the CI step is the first run against the real edge.

### PR-SEC-13.2 — read-only root filesystem

- **Setting.** The `x-hardening` anchor now also carries `read_only: true` and `tmpfs: ["/tmp"]`, so it covers every service we build and the two nginx services (the unprivileged nginx image keeps its pid file and temp paths under `/tmp`). Postgres keeps its defaults (it writes to its data volume and its own runtime directories).
- **What still writes.** `/tmp` (memory, empty after a restart), `/data` (GUI BFF), `/srv/packages` (Onboarding) and the new `smo_scratch` volume at `/srv/scratch` on `r1-termination`. The Dockerfile creates `/srv/scratch` owned by the service user, like the other two.
- **The runbook.** `docker compose cp` cannot write into a tmpfs, so every CSAR and sample directory the runbook serves now goes to `/srv/scratch` (§1, §25-§27) and `http.server` serves that directory; the demo scripts' state files stay in `/tmp`, which is writable. The runbook *replay* (`test_demo_runbook.py` with `SMO_E2E_LIVE=1`) serves packages from the test process, so it exercises the services, not these `cp` lines.
- **Tests.** `test_container_hardening.py`: every built service and the edge is `read_only` with the `/tmp` tmpfs, the scratch volume is mounted, the runbook has no `/tmp` copy target. The real proof is the compose job in CI (every service starts, passes `/ready`, and the replay runs on the read-only stack).
- **Not done:** SEC-13.4 (the same settings in the Helm chart, after OPS-2.2).

### PR-SB-1.5 — the config read against a real YANG model

- **The problem.** The route sent the build's `<managed-object ref=...>` shape; a real server (Netopeer2) only knows its models' data nodes, so the read could not return anything from it.
- **The option.** An ssh adaptor URI may carry `?model=<name>` (`ran-nf-oam/app/yang_payload.py`). Without it nothing changes. With it, `send_get_config` sends an RFC 6241 subtree filter on the model's list entry (`<lab xmlns="urn:smo:lab"><cell><id>102</id></cell></lab>`) and maps the reply's leaves back to SMO names (`administrative-state` becomes `administrativeState`). The key is the id of the last RDN of the managed function reference (or of the element reference if there is none), so `GNBDUFunction=1,NRCellDU=102` and the flat `102` both address cell 102. An entry that is not there reads as `{}`.
- **Known models only.** `PROFILES` holds `smo-lab` (container, list, key leaf, namespace). An unknown model, a repeated one or another option is refused at registration (the existing URI validation), so a typo never falls back to the shape the server does not understand. Adding a model is one `Profile`.
- **Writes.** `edit-config` to a model-based endpoint returns `NETCONF_RPC_FAILED` without sending anything: SB-1.6 builds that payload. Choosing refusal over sending the old shape keeps a config job from claiming success on a server that cannot have understood it.
- **Tests.** Unit: builder/parser, name round trip, URI validation, the in-process server receives a subtree filter and no `<managed-object>`, cell 101/102/999; route: `GET /managed-entities/ME-1/config` with a model endpoint, unknown model refused. CI: `scripts/netconf_lab_check.py` now also calls `send_get_config(...?model=smo-lab)` against Netopeer2 and expects cell 102's seeded values.
- **Not done:** SB-1.6 (write payload), a second model (a real O-RAN or 3GPP one), choosing the model per managed element rather than per endpoint.

### PR-OBS-2.6 — outbound call metrics

- **Series.** `smo_outbound_calls_total{client,target,method,outcome}` and `smo_outbound_call_duration_seconds{client,target}` (`smo_shared/metrics.py`, `record_outbound`), recorded at the two places a call leaves a service: `R1Client._call` (one per attempt, so the retry after a 401 is two calls) and `webhook._send` (post, get and delete share it now instead of three copies of the same guard and `try`).
- **Labels.** `client` is `r1` or `webhook`. For `r1` the `target` is the module in the path (`r1_target`: the first segment if it is module-shaped, else `other`). For a webhook it is the constant `callback`: the destination is caller-registered, and a host label would let any caller create series without bound, so the "per-destination" of the original wording became per-module for R1 and per-outcome for callbacks. `outcome` is the status class, `timeout`, `error` (any other transport failure) or `blocked` (the SSRF guard refused; nothing was sent).
- **Behaviour unchanged.** `R1Client` still raises the transport error after counting it; the webhook helpers still return `None` for every failure; the SSRF warning is still logged for a refused POST.
- **Tests.** `shared/tests/test_metrics.py`: target extraction (bounded), outcome classes through the client, timeout and error counted and re-raised, the 401 retry counted twice, webhook outcomes under the constant target, and no host in the exposition.
- **Not done:** the Grafana dashboard that charts it (OBS-2.8); outbound calls the outbox sends are webhook calls and so are counted with them.

### PR-SB-1.6, 1.7 — writes to a model-based server, and what the server said

- **Write payload (SB-1.6).** `yang_payload.build_edit_config_rpc`: `<lab xmlns="urn:smo:lab"><cell xmlns:nc=... nc:operation="merge"><id>102</id><tx-power>30</tx-power></cell></lab>` in `<config>` of `<edit-config>` on `running`. The `operation` is RFC 6241 section 7.2's attribute on the list entry (merge, replace, create, delete, remove); a delete or remove carries the key only; values are XML-escaped; an unknown operation or datastore is refused before anything is sent. `netconf_ssh.send_edit_config` picks it for an endpoint with `?model=` and keeps the `<managed-object>` shape otherwise. Until now a model endpoint refused every write.
- **The server's reason (SB-1.7).** `rpc_error_detail` turns the first `<rpc-error>` of a reply into one bounded line (`invalid-value (a value is not acceptable) at /lab/cell/tx-power: out of range`): the error-tag with a few words for each RFC 6241 appendix A tag, the error-path or bad-element, and the message; an unlisted tag is shown as it came. It rides on `EditResult.detail`, is stored as `write_config_sub_change.rejection_detail` (revision `0006`, additive and nullable) and is returned as `rejectionDetail`. The stable code `rejectionReason` is still `NETCONF_RPC_FAILED`, and the failure is still not retried: a `lock-denied` or `resource-denied` could be, but retrying on a tag the server chose is a decision for SB-1.8 (locking) rather than a side effect of a message parser.
- **Both transports.** The detail comes from `edit_outcome`, which the HTTP client shares, so the mock path gets it too.
- **Tests.** Unit: the builder per operation, escaping, unknown operation sends nothing, six `<rpc-error>` shapes (a standard tag with path and message, one with only a tag, a bad-element, an unlisted tag, an empty error, the 300-character bound); route: a rejected model write reports the detail. CI: `scripts/netconf_lab_check.py` writes tx-power 41 to cell 101 on Netopeer2, reads it back, restores 40, and expects 99 (outside the model's range) to be refused with a reason.
- **Not done:** the candidate datastore (SB-1.8) and the runbook CM write against the lab (SB-1.9); the `before` image of a CM snapshot for a model endpoint is read with the same `?model=` read as SB-1.5, so it needed no change.

### PR-SB-1.8, 1.9 — the candidate datastore, and the runbook CM write against the lab

- **Option.** `?datastore=candidate` on an ssh adaptor URI (`yang_payload.datastore_of`; `running` is the default; anything else, or a repeat, is refused at registration with the other URI options). It combines with `?model=`.
- **Transaction (SB-1.8).** `netconf_ssh.candidate_transaction`, in the one SSH session the write already opens: `<lock>` the candidate, `<edit-config>` with `<target><candidate/>`, `<commit/>`, `<unlock>` (RFC 6241 sections 8.3, 7.2, 8.3.4.1 and 7.5-7.6 as the building blocks). A refused lock changes nothing and sends nothing else. A refused edit or commit is followed by `<discard-changes/>`, so the candidate is left as it was found. The unlock always follows and is best effort: a failed unlock after a good commit is logged, not reported, since the commit stands. A server whose hello lacks `:candidate:1.0` is refused before any RPC. The failing step is named in `rejectionDetail` (`commit: operation-failed (...): validation failed`); the code is still `NETCONF_RPC_FAILED`.
- **Per write, not per job.** Each sub-change is dispatched on its own (HISTORY.md §7 item 3 and the dispatch loop), so the transaction covers one sub-change. A job whose second sub-change fails keeps the first one committed. The job-level version needs the loop to group sub-changes by element first: SB-1.10.
- **The runbook against the lab (SB-1.9).** `scripts/netconf_lab_runbook.py` replays section 7 through RAN NF OAM's own routes, in process (an in-memory database, no containers but the lab): register the adaptor (ssh, model, candidate), heartbeat, write tx-power 41, read it back from Netopeer2, the history (before 40, after 41), a batch with a never-registered ME (`PARTIAL_SUCCESS`), and a value outside the range refused with the server's reason, leaving 40 in place. CI runs it in the "NETCONF lab" job. **What it is not:** the compose replay (`test_demo_runbook.py`) still drives the mock adaptor; running that over SSH needs the stack's `ran-nf-oam` to hold the lab's host key and password, which is per-endpoint credentials (SB-2) first: SB-1.11.
- **Tests.** Unit, against the in-process server with candidate behaviours: the happy sequence and its order, a refused edit (discard, unlock, no commit), a refused commit, a denied lock (nothing else sent), no candidate capability (nothing sent), a failed unlock after a good commit, a model write naming `<candidate/>`, option validation; route: a candidate endpoint commits, and a refused commit is REJECTED with the step in the detail.

### PR-SB-2.1, 2.2 (and SEC-4.6) — a credential per endpoint, by reference

- **The reference.** `o1_adaptor_endpoint.credential_ref TEXT NULL` (revision `0007`, additive; NULL is the shared credential of PR-SB-1, which is what every existing row used). It holds a **name**, never a secret. Registration takes `credentialRef`; it is for `transport: ssh` only and must (a) look like a name (`^[a-z][a-z0-9_-]{0,62}$`: a password such as `Tr0ub4dor&3` or a passphrase fails the shape) and (b) name a credential this service has actually been given. The second test is what really keeps a pasted secret out: a literal password is never a configured name. The check is in the route, not the request model, and its message never repeats the value: a request-model validation error echoes the whole input in FastAPI's 422 body, so a pasted secret would have been returned to the caller (and appear in whatever logs the body).
- **Resolution (SB-2.2).** At each connect, `netconf_ssh.credentials_for(ref)`: `NETCONF_CRED_<NAME>_PASSWORD` or `_PASSWORD_FILE` (the `*_FILE` convention of `smo_shared/secretfile.py`, so a compose or Kubernetes secret mount works) and `NETCONF_CRED_<NAME>_KEY_FILE`, `<NAME>` being the reference upper-cased with `-` as `_`. A reference that resolves to nothing is an error (`NETCONF_RPC_FAILED`, `credential 'x' is not configured`), and an unreadable secret file reports the variable's class (`SecretFileError`), never a value. **There is no fallback to the shared credential for an endpoint that names one**: it must get that one, so a deleted secret fails closed instead of silently authenticating as someone else. Because the value is read at every connect, rotating a mounted file needs no restart.
- **Plumbing.** `main._ssh_kwargs` passes `credential_ref` to the ssh clients only (the HTTP and RESTCONF clients have no such parameter): the write dispatch, the before-image read of the CM history, and `GET .../config`.
- **Tests.** The named credential wins over a wrong shared one (against the in-process SSH server), a mounted file, an unresolvable name refused with nothing sent, an unreadable file names the variable, the key-file and shared fallback, eight name-shaped refusals that do not echo the value, an unconfigured name refused; routes: register, store the name only, connect and write through it, a literal secret refused without echo, `credentialRef` needs ssh. CI: the "Runbook CM write" step now runs with only `NETCONF_CRED_LAB_PASSWORD` set and no shared password, so the lab is reached with the endpoint's own credential.
- **Not done:** pinning a host key per endpoint through an operator route (SB-2.3), TLS client certificates (SB-2.4), a rotation runbook (SEC-4.8).

### PR-SB-2.3 — host keys pinned per endpoint

- **Store.** `o1_adaptor_host_key` (revision `0008`, a new table, so the previous release is unaffected): `endpoint_id` (cascade on delete), `key_type`, `public_key` (base64 body, public only), `fingerprint` (OpenSSH `SHA256:` form), `pinned_by`, `pinned_at`; unique per (endpoint, key type).
- **Routes.** `PUT /o1-adaptor-endpoints/{id}/host-keys` pins a key from `keyType` and `publicKey` (the two fields of a known_hosts line), parsed by paramiko, so base64 junk or a truncated blob is a 422. `GET` lists with fingerprints (what an operator compares against `ssh-keygen -lf` or the device label), `DELETE .../host-keys/{keyType}` removes one. ssh endpoints only (422 otherwise); unknown endpoint or key are `O1_ENDPOINT_NOT_FOUND` / `O1_HOST_KEY_NOT_FOUND` (404).
- **Never learned.** There is no scan or accept-on-first-connect route: the operator supplies the key from a source they trust, which is the point of pinning, and why PR-SB-1's rule (no trust on first use, no escape hatch) is unchanged.
- **Checking.** `_host_key_policy` loads `NETCONF_SSH_KNOWN_HOSTS` if set and adds the endpoint's pinned keys under paramiko's `[host]:port` name, with `RejectPolicy`; with neither it refuses (`pin one for the endpoint or set NETCONF_SSH_KNOWN_HOSTS`). A server presenting a different key than the pinned one of its type is refused (`host key ... does not match the known one`).
- **Changing a key.** Pinning a different key for a type that already has one replaces it, says `replaced: true`, and logs a warning naming the endpoint and the operator: the one way to accept a changed key, and it is an authenticated, named action rather than something a connection can cause.
- **Plumbing.** `_ssh_options(db, endpoint)` replaces PR-SB-2's `_ssh_kwargs`: the credential name and the pinned keys, for ssh endpoints only, to the write dispatch, the before-image read and `GET .../config`.
- **Tests.** Against the in-process SSH server with **no known_hosts file at all**: unpinned is refused; pinned works; a different key pinned makes the real server refused with "does not match"; re-pinning the real key restores it; unpin removes the trust (and a second unpin is 404); junk keys, a non-ssh endpoint and an unknown endpoint refused; the file of PR-SB-1 still works. CI: the lab runbook script takes the key from the file the job recorded (standing in for the operator's own source), pins it through the route, drops the file from its environment and then runs every step.
- **Not done:** TLS client certificates (SB-2.4); an audit trail beyond the log line (the `pinned_by` of the current key is all that is kept); pinning from the GUI.

### PR-SB-2.4 — NETCONF over TLS with a client certificate

- **Transport.** `transport: tls` (revision `0009` widens the CHECK of `0003` to `http-mock`, `ssh`, `tls`; existing rows untouched; the downgrade fails on purpose while a row still says `tls`), `adaptorUri` `tls://host[:port]` (default 6513; a user name in it is refused: under RFC 7589 the NETCONF user comes from the client certificate), with the `?model=` and `?datastore=` options of the ssh URI. `NetconfTlsSession` (`netconf_tls.py`) subclasses `NetconfSession`, so the `<hello>`, both framings, the candidate transaction, the model payload and every `EditResult` reason are shared; `netconf_ssh.open_session` picks the class from the URI scheme and the one pair of send functions serves both.
- **Verification both ways.** The server's certificate must chain to the CA file and name the host (`check_hostname`, `CERT_REQUIRED`, TLS 1.2 minimum; there is no switch to skip either, as with SSH host keys). The client certificate comes from the endpoint's `credentialRef` (`NETCONF_CRED_<NAME>_CERT_FILE`, `_KEY_FILE`, `_CA_FILE`; the shared `NETCONF_TLS_*` without one); all three are required, and a named credential never falls back to the shared one. Files are read at every connect. A missing or malformed file names its kind (`FileNotFoundError`), never contents.
- **Error mapping.** An untrusted or wrong-host server certificate is `NETCONF_RPC_FAILED` (`not trusted or does not name the host: <verify message>`); a refused client certificate is `NETCONF_RPC_FAILED` too: with TLS 1.3 the alert arrives on the first read, so the base class now maps an `ssl.SSLError` while sending or receiving to `the TLS session was refused: <reason>` instead of calling it unreachable; a closed port is `NETCONF_UNREACHABLE` and a silent server `NETCONF_TIMEOUT`, as for SSH.
- **Tests.** A TLS test server with throwaway certificates (`tests/netconf_tls_server.py`: a CA, a server certificate for 127.0.0.1, client certificates, expired and rogue-CA ones) behind the same NETCONF conversation as the SSH one: write and read over mutual TLS, the model payload and candidate transaction over TLS, a client certificate from another CA refused by the server, a server certificate from an untrusted CA or for another host refused by the client, an expired client certificate refused, incomplete or unreadable credential files refused before connecting, no fallback for a named credential, closed port, silent server; routes: register, write, read, a rejected write when the certificate is refused, five mismatched registrations.
- **Not done (SB-2.5):** a TLS listener in the netconf-lab server, so the real server is not yet exercised over TLS. Netopeer2 configures TLS through `ietf-netconf-server` with keystore and truststore entries whose exact shape depends on the pinned image's version, so it needs the image inspected rather than guessed (the CI job prints that model for now).

### PR-SB-4 — looked at, blocked on the source files (no code)

- **What was checked.** SB-4.1 needs the O-RAN WG4 M-plane YANG modules (`o-ran-hardware`, `o-ran-uplane-conf`, `o-ran-module-cap`, ...). They are published by the O-RAN Alliance with its specifications under its own licence. The public YangModels collection (`standard/`, `experimental/`, `vendor/`) has none of them: only the IETF `ietf-hardware` they build on.
- **What was not done, and why.** Writing the modules from memory would be inventing standards text, and registering O-RU classes in the vendor registry (SB-4.3) or testing an O-RU write (SB-4.4) without the real models would be guesswork about names and constraints. So SB-4.1 stays open on the files, which someone with the Alliance's material has to supply.
- **SB-4.2 needs no new code.** `scripts/ingest_yang_schema.py` already takes any YANG directory and the 3GPP library (`--library ../specs/MnS/yang-models`), which is how the WG10 and WG5 descriptors were made; with the WG4 files in `specs/` it is one command.

### PR-SB-6.1, 6.3, 6.4, 6.6 — the managed-object containment tree

- **Table.** `managed_object` (revision `0010`, a new table so the previous release is unaffected): `dn` (primary key, TS 32.300 DN text), `parent_dn` (self-reference, `ON DELETE CASCADE`), `object_class` and `object_id` (the last RDN), `managed_element_ref` (foreign key to `managed_entity`, cascading: the tree goes with its element), `source` (`registry` or `walk`, checked) and `updated_at`; indexes on the parent and the element.
- **How DNs are formed** (`app/mo_tree.py`). An element registered under a flat key `ME-1` has the root `ManagedElement=ME-1`; a key that is itself a DN is its own root. A managed function ref is the DN *below* the root, as the write routes already take it (`GNBDUFunction=1,NRCellDU=101`), so its full DN is `ManagedElement=ME-1,GNBDUFunction=1,NRCellDU=101`; a ref that starts `ManagedElement=` is taken as a full DN, and a flat function id (`101`) has no class to hang under so it addresses the root. DNs are compared as exact text.
- **What registration does (SB-6.1).** `POST /o1-adaptor-endpoints` puts the root, and the function the element was registered with, in the tree with every ancestor (`ensure`: existing rows are left alone, creation order is root first so the foreign key holds).
- **Backfill (SB-6.6).** The revision fills the tree from the elements already registered, in the same way (a flat key, a DN key, a function chain, a flat function id), and keeps what came before a malformed DN rather than failing the upgrade. Checked on a Postgres database with the three shapes. The `managedElementRef` rows themselves are not rewritten: it stays the flat registry key (SA-RANOAM-4's accepted deviation is narrowed, not closed: a write route still takes the flat key).
- **Routes.** `GET /managed-objects/{dn}`, `/children` (paged, ordered by class then id) and `/subtree?depth=` (nested `children`, default and maximum depth 16, at most 1000 nodes with `truncated` saying when the cap cut it). An unknown DN is 404 `MANAGED_OBJECT_NOT_FOUND` on all three. They sit behind R1's token like every route (the route-table walk covers them).
- **Tests.** DN formation; registration puts the root and the function in; children direct only, ordered, paged; subtree nested, depth-limited, capped, depth 99 refused; 404s; a DN-keyed element is its own root and its tree cascades away with it (SQLite foreign keys on, as in Postgres); idempotent `ensure`. Migration: a fresh Postgres, the models check, and the backfill.
- **Not done:** filling the tree from a server (SB-6.2), refusing a sub-change whose target is not in it (SB-6.5), the TEIV export (SB-6.7).

### PR-SB-2.5 — a TLS listener in the netconf-lab server

- **Why it needed a look first.** Netopeer2 configures TLS through `ietf-netconf-server` (2019-07-02) with `ietf-keystore` and `ietf-truststore` entries, a shape that depends on the pinned image's version (2.1.49). Two CI diagnostics printed the model tree and Netopeer2's own `example_configuration/tls_listen.xml`, `tls_keystore.xml` and `tls_truststore.xml`, and the listener is written from those.
- **What the lab does at start** (`netconf-lab/tls.sh`, sourced by `start.sh`): a throwaway PKI in `/pki` (compose mounts the git-ignored `./netconf-lab/pki`): a CA, a server certificate with the SAN `IP:127.0.0.1` (so the client's hostname check passes), a client certificate signed by that CA, two days' validity; then the server key and certificate into the keystore (PKCS#8 and SubjectPublicKeyInfo DER, base64, `rsa2048`), the CA and the client certificate as two truststore bags (`cacerts`, `clientcerts`), and a `smo-lab-tls` endpoint on port 6513 that requires a client certificate and maps it by SHA-256 fingerprint (`04:` + the digest, the x509c2n format) to the user `netconf`. The files are merged with `sysrepocfg --edit`, never imported over the modules: an import would replace the keystore and drop the SSH host key (`genkey`) and the SSH endpoint. The CA key, the server key and the signing requests are deleted after use; the caller needs only the client certificate, its key and the CA certificate.
- **The check.** `scripts/netconf_lab_check.py`, with `NETCONF_LAB_TLS_URI` set, reads cell 101 over TLS with the endpoint credential `labtls` (`NETCONF_CRED_LABTLS_*` pointing at those files), writes tx-power 42, reads it back, restores 40, and expects 99 refused with the server's reason: the same three behaviours as over SSH. The CI job waits for the port, lists the PKI directory and runs it; a failure also dumps the lab's logs.
- **Not done:** the compose runbook replay over TLS or SSH (SB-1.11); certificate rotation for the lab (it is regenerated at each start).

### PR-SB-6.2, 6.5, 6.7 — filling the tree from a server, enforcing it, exporting it

- **The walk (SB-6.2).** `POST /managed-entities/{ref}/managed-objects/refresh`: a whole-container subtree `get-config` on the endpoint's model (`yang_payload.build_walk_rpc`), the list entries read as DNs below the element root (`walk_paths`), then `mo_tree.apply_walk` makes the element's tree match: objects the server reports are added with `source=walk` (an existing row is left as it is), `walk` objects it no longer reports are removed leaves first, and `registry` rows are never removed. `ensure` with `source=registry` promotes any `walk` row on the way down, so a function an operator registered a cell under is not swept away when the server stops reporting it. The response is `{added, removed, unchanged, total}`. An endpoint without an ssh or tls transport and a `?model=` is 409 `PROTOCOL_NOT_SUPPORTED` (a server without a model reports no objects to walk); a failed read is 503; an unknown element 404.
- **Mapping a model onto classes.** A `Profile` now says where its entries sit in the containment: `rdn_class` and the RDNs above them. `smo-lab` is not a 3GPP model; its `cell` entries are mapped onto the names the runbook already uses, `GNBDUFunction=1,NRCellDU=<id>`, so a walk of the lab gives DNs the write routes take. A real model needs its own mapping (one `Profile`).
- **Enforcement (SB-6.5).** `RAN_NF_OAM_ENFORCE_MO_TREE=true` (off by default; read at each call) adds a check to `_dispatch_blocker`: a sub-change whose target DN (`mo_tree.target_dn`) is not in the tree is `REJECTED` with `MANAGED_OBJECT_NOT_FOUND`, zero attempts, nothing sent, and a dry run says `WOULD_REJECT` with that reason. The element root is always in the tree, so a write without a function ref is never refused by it. With the flag on and nothing walked, only registered functions pass: walk first.
- **The export (SB-6.7).** `GET /topology` (optional `managed_element_ref`) in the wire shape FOCOM's `/topology` uses for the TEIV adapter: `o-ran-smo-teiv-ran:ManagedObject` entities (`id` `urn:oran:smo:teiv:ManagedObject:<dn>`, attributes `dn`, `class`, `objectId`, `managedElementRef`, `source`) and `o-ran-smo-teiv-ran:MANAGEDOBJECT_CHILD_OF_MANAGEDOBJECT` relationships (child on the a-side, `sourceIds` the two DNs). It is this build's own export of what it holds: **not** the TEIV RAN domain model with its typed entities, which MGT-10.2 (link-type awareness) will need to map onto.
- **Tests.** Unit: a registry row survives a walk and promotes the walk rows above it. Routes, against the in-process SSH server with lab data: a walk fills the tree and follows the server (a cell lost, a cell gained, everything gone, the root stays), a model-less endpoint is 409, an unknown element 404, a failed read 503, the flag off and on (not in the tree, real but not walked yet, walked, dry run, no function ref), the export's nodes, links, filter and empty case. CI: `scripts/netconf_lab_check.py` walks Netopeer2 and expects the two seeded cells as `GNBDUFunction=1,NRCellDU=101` and `102`; the lab runbook script walks, lists the children, refuses a cell that is not in the tree under the flag, writes a walked one, and checks the export (five nodes, four links, since its element key is itself a DN under a `SubNetwork`).
- **Closed:** `PR-SB-6`. **Not done:** typed TEIV entities, deleting objects through the API, a periodic walk (no scheduler exists here: refresh is a call).

- **`smo-v0.1.0` cut (OPS-4.1b), 2026-10-03.** Authorised by the user ("Tag first, ship rollback in v0.2"). A source release: images by tag (OPS-4.2) follow, with a manual trigger so the workflow can be run against this tag. The known limitations are listed under `CHANGELOG.md` `[0.1.0]`. Order after the tag: OPS-4.2–4.4, SEC-12.3/12.4, OPS-5.2, OPS-10.5, then SB-1.10/1.11, SEC-4.8, and v0.2 (MGT-1.5–1.8, MGT-5.x, AI-10.5, MGT-10.2).

- **Image release workflow (OPS-4.2, 4.3, 4.4, SEC-12.3, 12.4).** `release-images.yml` has no third-party actions: nothing to pin or to trust beyond the runner's docker, git and gh, and cosign built from its pinned module version (`v2.5.3`). Provenance is BuildKit's own (`--provenance=mode=max`, SBOM too), stored in the registry beside the image, instead of a separate attestation action. The image list is read from `docker-compose.yml` so a new module is published without editing the workflow. `workflow_dispatch` takes an existing tag so `smo-v0.1.0`, cut before this existed, can be published. Not yet exercised: the workflow runs only when a tag is pushed or dispatched, so its first real run is the first one after merge.

- **Compose replay over SSH (SB-1.11).** `tests_integration/test_runbook_over_ssh.py` runs in the compose job's replay container (`SMO_E2E_LIVE=1`, `SMO_LAB_HOST=netconf-lab`) against the stack's own RAN NF OAM, recreated with `netconf-lab/compose.replay.yml` (the lab's password under the credential name `lab`, nothing else). It registers the endpoint, checks an unpinned server is refused, pins the key it read with paramiko, writes 41 and reads it back, gets the server's own reason for 99, and restores 40. It is a separate pytest file rather than a second mode of `test_demo_runbook.py` because the runbook's mock adaptor and the lab are different servers; it is skipped outside live mode. First verified by CI on the PR that added it.

- **Rotation runbooks tried (SEC-4.8).** Each runbook in `docs/SECRETS.md` names the check that tries it. The database password: `scripts/rotate_db_password.sh`, run by the compose CI job, which then checks that the old value is refused (from a second container on the compose network: inside the postgres container `127.0.0.1` is trusted) and that the stack answers on the new one. The rest are in-process: a changed SSH password file and a replaced TLS certificate pair are used by the next connect without a restart; deleting a module's `module_identity` row makes the next start register one new invoker that its replicas share; changing `GUI_JWT_SECRET` ends old sessions. What is not tried: the element's side of an adaptor rotation, and a database rotation with no window (needs two roles or a pooler, DB-5).

- **One candidate transaction per job and element (SB-1.10).** The grouping is made before dispatch from the registry: a change is in a group when it is not blocked and its endpoint is ssh/tls NETCONF with `?datastore=candidate`, and it joins the changes of the same element. `candidate_edits` (netconf_ssh.py) is the one implementation: the single-edit `candidate_transaction` of SB-1.8 is now a group of one, so the two cannot drift. The sub-changes that did not fail get their own reason, `NETCONF_TRANSACTION_ABORTED`, rather than the server's, because they were not refused by it; the detail names the one that was. A refused commit is not attributable to one edit, so every sub-change carries it. Before images are read before the lock (reads against the running datastore), one per sub-change, so the history has a row per sub-change as before. Not done: grouping across elements behind one endpoint (a server that exposes several elements through one session would need a transaction per session, not per element), and a job-level all-or-nothing across elements (that is MGT-5 waves and rollback, MGT-1.6).

- **CM diff, rollback, guard and retention (MGT-1.5–1.8, v0.2).** A rollback is a new write job, not a special path: `_execute_write` is the body of `POST /config-jobs` and the rollback route builds the same `WriteConfigRequest` from the snapshots, so MSAC, the schema check, dispatch (including SB-1.10 transactions), snapshots and the alarm on failed dispatch all apply, and the undo is itself undoable. The plan is made from `cm_snapshot` rows of applied sub-changes only, in reverse; the changed-since guard compares what the job left behind (the forward fold of its `after` values per target, so two writes to one attribute are not reported as a conflict) with a fresh read. The guard fails closed: an unreadable target counts as changed unless `force`. What cannot be restored is refused whole, not partially: an attribute with no value before (the before image holds `None`; there is no 'unset' in the write path), a missing before image, a delete with no recorded values. `rollback_forced` is the audit of an override. The diff works on each snapshot's image (before with after laid over it), so it only knows attributes a write named. Retention: `RAN_NF_OAM_CM_SNAPSHOT_RETENTION_DAYS` defaults to 0 (keep); the purge is a route an operator or scheduler calls, nothing deletes on its own. DB-3.1/3.2 for the other tables are still open: this is the setting for `cm_snapshot` only.

- **Release workflow: tooling from its own commit, and a builder that can attest.** Found before the first run: the workflow checked the tag out for every job, so for `smo-v0.1.0` (cut before `release_images.py` and `release_notes.py` existed) the plan step could not run, and the default docker driver refuses `--provenance`/`--sbom`. The image jobs still build the tag's own source; the plan and notes jobs now run at the commit the workflow runs at (with all tags fetched), and the build uses a `docker-container` builder. Still unexercised on GitHub until the first dispatch or tag push.

- **The previous release's code on the new schema (OPS-5.1, 5.2).** `scripts/check_previous_release_code.sh` builds the newest final `smo-v*` tag from its own source, replaces its `migrate` service by a no-op (it would fail on revisions it does not have), migrates the database with the current commit's `scripts/migrate.py`, starts the old stack and replays the old release's own runbook (`test_demo_runbook.py` from the tag, so the test is the one that release shipped). A control runs first on a throwaway stack: with `write_config_job.msac_role` renamed the first runbook test must fail, otherwise the check cannot fail and the script says so. It has to run first because a replay leaves data behind that makes a second replay on the same stack fail for unrelated reasons. CI runs it when a PR changes `smo/migrations` and on every push to `main`; on other PRs the job passes without running (a required check that never reports would block the merge). The expand/contract rule is in `CLAUDE.md` (OPS-5.1). OPS-10.5 (the upgrade lane on `main`, which also replays the *new* code on the old data) is the other direction and still open; this is the compatibility half. First verified by CI on the PR that added it.

- **Waves, the health gate and the automatic revert (MGT-5, v0.2).** The job is created with every sub-change as `PENDING`, its `position` and its `wave`; `_dispatch_wave` is the old dispatch loop operating on one wave's rows (so the SB-1.10 grouping applies per wave and a job without `waveSize` is the same code path with one wave). Waves are made from the *elements*, not the sub-changes, so one element's changes are never split. `HALTED` is the only state a job is in between waves, with `halted_reason` saying why; the request that creates or continues a job runs waves until it ends or halts, so there is no background runner, and a wave pause is a `next_wave_at` that `continue` honours and `advance-due` (called by a scheduler) acts on. A gate stops at the first failure, a rejected sub-change before alarms, because rolling on after a wave that did not apply is how a canary becomes an outage. The automatic revert is `_rollback_plan` + `_changed_since` + a normal write job with `rollbackOf` (MGT-1.6), so MSAC, the schema check, SB-1.10 transactions and snapshots apply to it, and it refuses to overwrite a value somebody changed since: the job halts `REVERT_REFUSED` instead of reverting in silence. Undone sub-changes become `REVERTED` (they no longer count as applied), unrun ones `REJECTED` `WAVE_NOT_RUN`, so a reverted job ends `FAILED` from its own rows. Not done: a gate on KPIs (MGT-11 first), waves across elements chosen by region or topology (MGT-15), and a scheduler inside the service (`advance-due` is the hook).

- **KPI engine (MGT-11.1-11.5, v0.2).** The formula language is a whitelist over `ast` (`kpi_formula.py`): numbers, counter variables, `+ - * / **`, comparisons, six functions and `ifelse`; every other node is refused at definition time, so a hostile formula never reaches a counter and the evaluator never calls `eval`. `if` is `ifelse` because `if` is a Python keyword and the formula is real Python syntax. Division by zero and a missing counter make the KPI undefined (null) rather than an error: a cell with no handovers has no success rate. `ifelse` evaluates only the branch taken. Counters are aggregated per counter, per group, *then* the formula is evaluated, which is what makes regional ratios right (the mean of cell ratios is not the region's ratio). Regions are the cell guards the registry already has (`sectorGroup`, `incidentZone`). The data is the stored PM files only (a bounded newest-first scan, `truncated` when cut): `/pm-reports` goes to DME and leaves nothing here, and a proper store with retention is MGT-12/SB-18. Not done: seeding a standard KPI set (11.6) and exposing results as a DME type (11.7).

- **Revert on KPI regression (AI-10.5, v0.2).** The check is anchored on the job's `schema_validated_at` (when it was created, which is when its first wave went out) with a baseline window before it and an observation window from it, per element the job *applied* changes to. Both windows need `minSamples` observations (a measurement with several counters counts once) and a defined, non-zero baseline, otherwise the verdict is INSUFFICIENT_DATA and nothing is reverted: a decision to undo a change is never taken on thin data. `direction` says which way is worse, because a drop rate regresses upwards. The revert is the rollback of MGT-1.6 limited to the regressed elements (`_rollback_plan(..., elements)`), so the access check, the schema check, snapshots and the changed-since guard apply and an override is recorded. What is not built: something that calls the check on a schedule or when PM data arrives (an rApp, SA SMOS or a cron calls it, as it does `advance-due`), and an autonomy policy that decides the thresholds. AI-10.1/10.2 (limits in the manifest, a rate limit per rApp) are the other half of the AI-10 safety net and are not part of this change.
- **Per-rApp limits (AI-10.1/10.2, v0.2).** The manifest's `limits` is validated at onboarding (only `configJobsPerHour`; an unknown name fails the package rather than being ignored, because a limit the platform cannot enforce must not look as if it were). Enforcement is where the writes happen: RAN NF OAM keeps `rapp_limit` per invoker id and counts the caller's jobs in the last hour from `write_config_job.invoker_id`/`created_at` (R1 Termination's `X-R1-Invoker-Id`, the token's client id, which is the instance's `oauth_client_id`, so the rApp's own identity is the key and nothing the rApp sends can change it). The count is by a rolling hour in the database, so it is right across replicas and restarts, unlike the process-local bucket of SEC-8.2. rApp Management pushes the limit at `bootstrap-complete`, *before* the instance goes RUNNING, and fails closed (503, instance stays DEPLOYING): the alternative, running without a limit the package asked for, would make the declaration decorative. `rapp_limits_set` on the instance is what makes teardown delete it. Rollbacks and automatic reverts do not go through the check (undoing a change must not be refused because the budget is spent), and neither does a dry-run. A caller cannot set or delete its own limit; beyond that **any valid token still reaches `PUT /rapp-limits/...` through R1**, as it reaches every route, so an rApp could set another rApp's limit: caller roles at the gateway are not built and this does not pretend otherwise. Not done: magnitude and blast radius (AI-10.3), the kill switch (AI-10.4), events for refusals (AI-10.6).
- **Link types in the topology (MGT-10.2, v0.2).** The data this service holds about relations is the containment tree and the per-cell `neighbourRefs` an operator declares, so link types are read from those and no further: a neighbour is `INTRA_ELEMENT` or `INTER_ELEMENT` by which element owns the neighbour's cell id, `AMBIGUOUS` when two elements claim the id (guessing which one a fault would spread to is worse than saying so) and `EXTERNAL` when none does. Relations are returned as declared with a `reciprocal` flag, because a relation only one side lists is information too. The vocabulary is this build's own: nothing says which 3GPP interface (Xn, F1) a relation rides on, so none is named, and the TEIV export (`/topology`) is unchanged. Containment is computed from DNs and the stored parent. Not built: a consumer. Alarm-storm correlation (MGT-9, MGT-10.1/10.3) does not exist yet, so these two routes are what it will read; nothing in this change suppresses or groups an alarm.
- **The upgrade lane (OPS-10.5, v0.2).** OPS-1.6 (`check_upgrade_from_previous.sh`) migrates an *empty* database from the previous commit; OPS-5.2 proves the old code runs on the new schema; this is the operator's upgrade: install the previous release (built from its tag, its own `migrate`), fill it by running its own `test_full_runbook_sequence_succeeds` (the runbook is the only thing that writes realistic rows into the whole stack), stop it keeping the volume, start this commit's stack on the same volume so *this commit's migrate service* upgrades rows that exist. The control is a seeded migration (`ADD COLUMN ... NOT NULL` on `application_package`): it passes on an empty database, which is why the fresh-database check cannot catch this class, and fails on a populated one, where it must be refused and leave the revision untouched (Postgres DDL is transactional). Checked afterwards: row counts per table not lower (the outbox and idempotency keys excepted: they drain and expire), packages and instances identical by id, `check_migration_matches_models.py`, and the compose fast gate against the upgraded stack. Both stacks share a project name (`upgrade`) so they share the database volume, and the database password file is copied to the old tree. **Not done:** the runbook is not replayed on the upgraded database (it onboards a CSAR that must not already exist, so a second run fails on the duplicate-content check by design); the data check is counts and ids, not every column; and the lane starts from the newest *final* tag only, one version back. OPS-10.6/10.7 (Helm on kind as the master gate, mixed-version rolling lane) are not part of this.
- **The audit chain and what it costs the gateway (PR-SEC-11.1 to 11.5, v0.3).** Until now the gateway "has no database" and a refused or accepted change left only a log line. The audit has to be written by the one place every change passes, so R1 Termination now uses the shared database, for this and nothing else. The decisions: (1) a write is a background task after the response is sent and its failure is logged and counted, never raised, so the database being down fails no call (the alternative, refusing changes while the audit cannot be written, turns an audit outage into a platform outage, and R1 already fails closed on SME for the same reason of authentication; an operator who wants fail-closed can say so and it is one mode away, not built); (2) only authenticated calls are recorded, because the rate limiter sits after authentication and an unauthenticated flood must not become a database write flood; (3) the chain is numbered by a single locked `audit_head` row, so replicas of the gateway queue on it rather than fork the chain (a database that does not lock rows, SQLite in the tests, collides on the primary key and `write_audit` numbers again); (4) the body and the query are never recorded, because they carry credentials and rApp data; (5) for an SMO module acting for an rApp (`X-R1-On-Behalf-Of`) the actor is the module and the rApp is in `detail`, which is the truth about who made the call and still answers who it was for. **Limits:** whoever can write the database and recompute every hash is not caught by the chain, which is why `export` ends with the head to keep elsewhere; rows are never purged; the `/modules/status` and GUI view (SEC-11.6) are not built; token requests are recorded like any other POST (a busy rApp's refreshes are most of the rows).
- **The image scan (PR-SEC-12.2).** Asked for before the published images are made public: once they are, anyone can scan them. Choices: (1) *scan what the release would publish*, i.e. build every image from the checkout (the same loop as the Helm job) rather than pull the published ones, which are private and would need a token and a package link; (2) *Trivy, built from its tagged source with `go install`* because this repository pins every action by commit and the sandbox that wrote this cannot look up a third-party action's or image's digest; the module path and tag are pinned and the Go checksum database verifies the source; (3) *only fixed HIGH and CRITICAL findings gate* (`--ignore-unfixed`): an unfixed one cannot be removed by an upgrade, and gating on it would only produce a red check nobody can act on; (4) *not a required check* and run weekly, because a base image gets new advisories without any change in this repository and an unrelated pull request must not be blocked by it; (5) *accepted findings are written down* in `.trivyignore` with a reason. **Limits:** it scans packages (OS and language), not configuration, secrets or the Helm chart; the report names the finding, a person decides whether it is reachable. The first run is `report_only`, to read what is there before it gates.
- **Delivering an instance's credentials (PR-SEC-14, v0.3).** The invoker secret is shown by SME once and kept only as a hash, so "delivery" can only mean: whoever registers the invoker puts the secret where the workload reads it, in the same moment, and keeps nothing. Options weighed: (a) hand it to NFO inside the instantiate request (NFO would store it in its descriptor/deployment rows, and its southbound is not modelled here, so it would not be a delivery at all); (b) a pluggable sink in rApp Management. Chosen: (b), with one sink, Kubernetes Secrets, because it is the deployment the chart targets and can be proved on kind; a Vault/External Secrets sink is another implementation of the same two functions (`deliver`, `withdraw`). Decisions: the Secret is written when the invoker is made (create, and each rotation), not on demand, so the workload NFO starts right after finds it; a failure to write it fails the create (an instance whose workload cannot authenticate is no use) and withdraws the invoker; the credentials endpoint stops returning the secret in this mode so it exists in exactly one place; teardown deletes it best-effort and records the outcome in `last_teardown.credentialSecret`. **Security cost, stated:** rApp Management now holds a service-account token that can create, replace and delete Secrets in the whole namespace (create cannot be limited by name); the Role has no get, list or watch, so a compromise of rApp Management cannot read `smo-secrets` or another instance's credentials, but it could overwrite or delete them (a denial, not a disclosure). The Secret is plaintext in etcd unless the cluster encrypts it at rest. **Not done:** NFO does not yet reference the Secret when it deploys the workload (the descriptor naming is the operator's), and compose has no equivalent.
- **The kill switch at the gateway (AI-10.4 extended, v0.3).** A stop only reached RAN NF OAM's config jobs, so a stopped rApp could still do everything else the gateway lets an rApp do. With the audit work R1 has the database, so it reads `rapp_kill` itself rather than asking RAN NF OAM per request (a call per change would double the gateway's latency and make RAN NF OAM a dependency of every other module's traffic). Decisions: (1) *changes only*; a stopped rApp that cannot read would loop on errors, and what an rApp may read is PR-SEC-10's question; (2) *DELETE, the token endpoint and rollback stay open* so a stopped rApp can be wound down and an operator is not left with orphaned data jobs and subscriptions that only the rApp could remove; (3) *an SMO module acting for a stopped rApp is refused too* (the on-behalf-of header), so DME's own writes for it stop, and an rApp cannot dodge by naming another because R1 drops its own header; (4) *a cache of 3 s per invoker*, which is the honest cost of not asking the database on every call, shown in the GUI text and the docs; (5) *unreadable means refuse*, unlike the audit write: the switch is a safety control, and the modules behind the gateway need the same database, so refusing adds little harm. The gateway's table definition is a second declaration of RAN NF OAM's `RAppKill` columns (a test pins the names). **Limits:** the refusal is not announced to the safeguard subscribers of AI-10.6 (those events are RAN NF OAM's, through its outbox; the audit chain and a metric record it); reads stay open.
- **The Helm chart: choices (PR-OPS-2/3, v0.3).** (1) *Compose stays*; the chart is a second description of the same stack, so the risk is drift, and `test_helm_chart.py` pins the two together (every service is a module; same image as the release publishes, same secrets, same worker command, same environment; no rApp holds the enrollment secret). (2) *Service names are compose's* (`sme`, `postgres`, ...) because the code and the GUI's nginx address each other by them; the price is one release per namespace, which the README says. (3) *One template over a map* rather than a sub-chart per module: the modules differ by a handful of fields and a fix to the pod spec is then made once. (4) *The migration* is a plain Job on install and a `pre-upgrade` hook on upgrade, not a hook both times, because a `pre-install` hook runs before the bundled database exists; the pods' `wait-for-schema` init container (`migrate.py --wait`) is what makes the order safe and also what stops a new pod serving on an older schema (OPS-3.2). It accepts a database *past* the image's head, so the previous release's pods keep running on a newer schema during a rolling upgrade (expand/contract). (5) *Secrets* are generated once and kept by `lookup`; `existingSecret` is the production path. (6) *Bundled Postgres* is a lab convenience: one pod, no backup, no HA (DB-7 is the HA work). **Limits:** replicas above 1, PDB and HPA are templates, off, untested under load; NetworkPolicy is only rendered, not tested for a denied path (kind's default CNI does not enforce it); the chart is proved on kind from locally built images, not from the published ones until a release exists (`image.tag` defaults to the appVersion, which the release PR must raise); `helm` could not be installed from a release in the authoring sandbox (built from source), the CI job is the real gate.
- **An allow-list for what an rApp may change (PR-SEC-14, v0.3).** The deny-list of v0.2 stays for the routes that set what the platform allows rApps to do, but a deny-list cannot keep an rApp out of a route nobody thought of (onboarding another package, creating or terminating instances, deleting another invoker's registration). For *changes* the policy is now an allow-list, so the default is no. It was safe to do now because the only code that calls R1 as an rApp in this repository is `smo_sdk` (the samples call nothing else), so the list is derived from the SDK and *proved* by it: the SDK's fake client asserts every change it makes is allowed, so a new SDK call without its route fails a test. What the SDK does not call but a conformant rApp would is added by judgement, not evidence: the `ml-*-requests` families and `aiml-inference-reports` (a request is by definition made by a consumer), `config-jobs` and its rollback (the safeguards apply there), and the rApp-facing `a1-related`, `ran-analytics`, `dme-push` and `dme-pull`; left out is administration of a service (functions, repositories, storages, sweeps, runtime scale and terminate, trusted invokers, purging). **Limits:** reads are still open to every valid token (per-tenant read scoping is PR-SEC-10); a third-party rApp that calls a route outside the list breaks on upgrade, which is why `audit` mode exists; the list is by path shape, not by body, so an allowed route is allowed for any content. **A blind spot found on the way:** with the FastAPI in use, a router added by `include_router` is one opaque entry in `app.routes` (no `path`, no `methods`), so the route walk of `test_authz_walk.py` and the role-policy walk silently skipped every route of such a router (all of AI/ML Management's TS 28.105 routes, among others); the helper now also reads the OpenAPI document, which lists them all, and the two walks cover several hundred routes more than before.
- **An rApp's writes through DME carry its identity (PR-SEC-14 follow-up, v0.3).** Found while building the GUI for the safeguards: the SDK and sample rApps write through `/dme/actions`, and DME forwards to RAN NF OAM with its own module token, so R1 vouched `internal` with DME's invoker id and none of the four safeguards (kill switch, rate limit, blast radius, magnitude) applied to the path rApps actually use; a stopped rApp could still write. The tests missed it because they called RAN NF OAM directly with the rApp's identity. Fix: R1 forwards `X-R1-On-Behalf-Of` only on calls from an internal caller; the shared `R1Client` adds it from a contextvar set by middleware from the inbound request, so every hop down the chain carries the original rApp; `invoker_id(request)` prefers it when the role is `internal`. An rApp or an unauthenticated caller cannot set it (R1 drops it unless the role is internal). **Limits:** a mixed-version stack keeps the old behaviour across modules that do not yet forward the header; this affected 0.2.0.
- **Caller roles and the rApp identity (PR-SEC-14, v0.2).** Found while starting AI-10.3/10.4: the per-rApp limit of AI-10.2 (above) never applied to a real rApp. It keyed on `X-R1-Invoker-Id`, which is the id of the invoker SME generates (`api-invoker-<uuid>`), and assumed that equals the instance's `oauth_client_id`, but rApp Management minted that as a random UUID that no token ever carried, and the sample rApps authenticated as `smo-module:<MODULE>` through the same `R1Client` as the SMO's own services, asking for the same `smo-internal` scope. The tests passed because they set the header by hand. So no caller could be told from another, and any valid token reached every route. The fix is one identity model: SME records every invoker as `internal` or `rapp` (`invoker_registration.kind`), by whether it presented a secret (`secrets/enrollment_secret`) that only the SMO's own services mount; introspection reports it; R1 forwards `X-R1-Role` and applies a deny-list. rApp Management now registers a real invoker per instance and *that* id is `oauth_client_id`, so SME apfId, DME producer id, the limit and the token's client id are one value. The secret of that invoker is returned once by `POST /instances/{id}/credentials` and not by create, because create is an idempotent command and a replay would serve a stored secret. Decisions: the policy is a deny-list (today three route groups), not an allow-list, because the repository's tests drive services in-process and cannot show that every route an rApp legitimately calls is on an allow-list, and a wrong allow-list would break the platform; widening it is one line in `roles.py` plus the test that the route exists. Existing invokers migrate to `internal` (they are SMO services); this is also what lets a rolling upgrade keep working. The user chose enforcement from the first deploy; `audit` exists as a switch. **Limits:** in the compose demo the sample rApps are static services with their own module identities and are not deployed through `POST /instances`, so a limit set from a manifest at bootstrap-complete keys on the instance's invoker, which those containers do not use; a deployment that passes the instance credentials to the workload (`SMO_INVOKER_ID`/`SMO_INVOKER_SECRET`, `SMO_IDENTITY_KIND=rapp`) gets the full effect, and `compose_e2e_roles.py` proves the path with an invoker of its own. Delivering the credentials to a workload from NFO is not built. The deny-list covers the platform-configuration routes named in the review (limits, KPI definitions, history purge), not every administrative route.
- **The kill switch (AI-10.4, v0.2).** Keyed on the invoker id R1 vouches for, like the limits, in RAN NF OAM where the writes are. It refuses *new forward changes* (a job, a dry run, going on to the next wave) and nothing else: stopping an rApp that has just broken something must still allow the operator, or the rApp, to roll it back, so rollback, revert, halt and abort are open to it. A dry run is refused too: a stopped rApp has nothing to ask. The operator-facing form is on the instance in rApp Management, which knows the instance's invoker id, and it fails with 503 rather than answering as if the switch were thrown when RAN NF OAM could not be told. The kill rows are not removed when an instance is terminated (the invoker id is never reused, and the row records who stopped it and why). Not done: the switch only reaches RAN NF OAM's writes; an rApp's other actions (DME, A1 policies, intents) are not stopped by it, and terminating the instance remains the way to stop everything.
- **Blast radius and magnitude (AI-10.3, v0.2).** Both are checked in RAN NF OAM, before the job row exists, so a refused job leaves nothing behind, and for dry runs too (a dry run that says "would pass" for a write that would then be refused would be a lie). Blast radius counts *distinct managed elements*, not changes, because the unit that fails together is the element. Magnitude is relative to the value read from the NF *now* (the before-image read the snapshots already use), not to a value the rApp claims; it needs one southbound read per numeric attribute set, only for rApps that declare it. Fail closed: a current value that cannot be read, or is not a number, is refused with that reason, because a magnitude limit that is skipped whenever the NF is slow is not a limit; the cost is that an rApp with the limit cannot *create* a value that does not exist yet, which is stated in the limit's documentation. A value of 0 may only stay 0 (percent of zero is not defined, and any move from it is unbounded). Per-attribute absolute bounds (`min`/`max` of a value) are not built: they need the vendor's data model. Rollbacks and reverts are exempt, as for the rate limit. `PUT /rapp-limits` replaces the whole set so that the manifest is the source of truth: removing a limit from a manifest and redeploying removes it.
- **Events for refusals (AI-10.6, v0.2).** All four refusals go through one function (`_refuse`) that writes the record and the outbox rows and then *commits before raising*: raising the 403/429 rolls the request's transaction back, and a refusal that vanishes with its own rollback is the one case where the evidence matters most. Two things are kept apart on purpose: the record (`safeguard_refusal`, every refusal, queryable, with `announced`) and the event (webhook through the outbox, at most once per interval per rApp and code). The first answers "what did this rApp try" after the fact; the second answers "tell me now" without letting a runaway rApp turn its own refusals into a flood of calls to the watcher. The interval deduplicates in the database, so it holds across replicas, unlike the process-local R1 bucket. The destination is checked by the SSRF guard when subscribing (422) instead of being dropped silently at send time. Not done: purging old refusals (they are small rows, but nothing removes them), an event when a limit or the kill switch is *set* (the log line is the only trace of who stopped an rApp, besides the `rapp_kill` row), and the GUI.
- **The standard KPI set and KPIs in DME (MGT-11.6/11.7, v0.2).** The seeded KPIs are defined over the counters the build's own data and sample rApps use (`RRU.PrbTotDl`, `RRC.ConnMean`, `DRB.UEThpDl`, the `MM.Ho*` family), and the description says so: the names are TS 28.552 style, but the KPI definitions of TS 28.554 are not reproduced here, because without the document the formulas would be written from memory. An operator who needs them defines them with the same route. Seeding is an explicit idempotent call, not a startup action (every replica would race to write, and the service holds no state at start), and it never overwrites: an operator's edit of a seeded KPI is theirs. DME is how consumers get data in this platform (an rApp discovers a type and opens a data job), so results are published to a DME type per KPI (`RAN.KPI.<name>`, like `RAN.PMCounters.<counter>`) instead of giving rApps a second way in; the call registers the type each time (DME's registration is an upsert) so a restart of DME does not lose it. Publishing is a call, not a schedule: no scheduler exists in the service, as for `advance-due` and the KPI check. Not done: publishing on a schedule or when PM data arrives, retention of what was published, and SLA evaluation on these KPIs (MGT-18).
- **First scan result and its fixes (PR-SEC-12.2).** The first run found six distinct fixable HIGH findings across all 25 images: a pcre2 advisory (Debian and Alpine) and three Python packages (msgpack, urllib3, setuptools). The Python ones are not in the hashed lock: they are the copies vendored inside pip and setuptools in the base image, which nothing uses at run time. Fix: `pip uninstall pip setuptools wheel` at the end of the Python image build, `apt-get install --only-upgrade libpcre2-8-0` there, and `apk upgrade pcre2` in the GUI image. Trade-off: a container can no longer `pip install` at run time (it never needed to); the upgrade step goes when a newer base digest carries the fix.
- **Replicas on kind, and what the first probe measures (PR-HA-1/2).** Choices: (1) *prove it in the existing `helm` job*, after the single-replica checks, rather than a new job that would build the 25 images again; (2) *a probe pod inside the cluster*, not the runner, because a port-forward pins one pod and dies with it, while a pod calling the Services sees what another module sees; (3) *`/health` of each module, not the runbook*: the runbook replay is built for the compose network (callback receiver, package server) and needs a port of its own (HA-1.2), so this first lane proves the platform part (readiness, surge, drain) and not request correctness under restart; (4) *one retry allowed*, as the done-when says; a pod can reset a connection that was already open when it was told to stop; (5) *`preStopSeconds`*: the endpoint update and SIGTERM race, so the pod sleeps first (5 s, inside the 30 s grace beside the server's 20 s drain). Onboarding and the GUI backend stay at one replica and are not restarted in the probe: they hold a volume and roll with Recreate. Making Onboarding's package store shareable (an object store or a ReadWriteMany volume) is the next step for them and is not scheduled.
- **The outbox sweep was missing (PR-MSG-2 as part of PR-HA-4.1).** HA-4.1 asks that killing the delivery worker loses no notification, and the check found there was no delivery worker: the outbox's full `drain` was documented as "MSG-2's worker" and nobody called it, so a row whose first send failed stayed PENDING for ever. Choices: (1) *the sweep is a task of the existing worker framework, not a new service or image*, so there is no 26th image and a deployment that already runs `ran-nf-oam-worker` gets it; (2) *one claim for the whole database* (`Task.shared`), because the table is shared and a per-module claim would sweep it once per module for nothing; (3) *the claim is the existing atomic UPDATE with a 60 s lease*, not `FOR UPDATE SKIP LOCKED` (MSG-2.2): it meets the same done-when (two senders never send the same row) on SQLite as well as Postgres, and the advisory lock around the sweep already keeps two workers from sweeping at once; (4) *the back-off stays 5 s / 30 s / 2 min / 10 min*, not the 0/5/10/20 s of the plan's MSG-2.3, which would give up on a subscriber that is down for under a minute; (5) *inline drain stays on* (MSG-2.5 is not done): it delivers in the request's own thread, and the sweep is the safety net. **Test:** in CI a sink pod takes 0.3 s per notification, 100 are queued, both worker pods are force-deleted after the first ten arrive, and the replacements must deliver all 100 (duplicates are counted). **Not covered:** killing the job runner in the middle of a staged job (HA-4.2): its tasks are idempotent and a dead worker's claim ends with its connection, but no test kills one yet.
- **Placement (PR-HA-5.1).** Topology spread constraints, not pod anti-affinity: they say the same thing ("not all on one node") with one `maxSkew`, and they handle a third replica on two nodes where hard anti-affinity would leave it Pending. Default `soft`, because a default that leaves pods Pending on a one-node install (kind, a laptop) would break the quick start; `hard` is for a production cluster that has the nodes. Not done: a multi-node kind run showing pods on different nodes (it would load 25 images onto every node in a job that already takes 20 minutes); the CI renders each mode and checks the strings.


- **Two replicas found a gap in the kill switch (PR-HA-5 CI).** The roles script threw an rApp's kill switch and then expected the refusal to be in `safeguard_refusals`. With one gateway replica that held; with two it did not. Cause: the gateway (0.3.0) refuses a stopped rApp's change before any backend, and only RAN NF OAM writes the record and sends the AI-10.6 event, so whenever the gateway's 3 s cache already knew the stop, a config job was refused and not recorded. One replica hid it because its cache for that invoker was usually stale at that moment. Fix: the gateway leaves `POST /ran-nf-oam/config-jobs` to RAN NF OAM (same 403, plus the record); all other changes are still refused at the gateway. A refusal at the gateway of any *other* change (a DME data job, a training job) is audited (`REFUSED:RAPP_KILLED`) and counted but not in the safeguard record or its events: that record is RAN NF OAM's and covers config jobs; extending it to the gateway would need R1 to write to RAN NF OAM's tables or call it, which is not decided. The earlier check on a DME data job was fixed in the same PR for the same reason (it asserted something that depended on cache timing).
- **Per-module schemas, step one: owners and boundaries (PR-DB-2.1 to 2.4).** The map is generated from what each module's models add to the shared metadata, loaded one after another in a clean process (the same `load_app_module` the integration tests use), so it records what the code declares, not what anyone remembered: 134 tables, no table declared twice, six declared by the shared library for every module (`shared`: `notification_outbox`, `idempotency_key`, `module_identity`, `periodic_run`, `audit_log`, `audit_head`). R1 Termination, MLLF and the two mocks own none. **The ORM had no cross-module foreign keys; the migrations had 24** (the unit tests run on SQLite built from the ORM, so nothing ever ran against them except real Postgres). Decision for every one: *plain id, no constraint*, because a role that may not read another module's schema cannot be bound to it by a constraint, and because the modules already check existence through each other's APIs and handle their own cascades (Onboarding's delete guard, MLMR's repository delete). One revision for all 24 instead of one pair per PR: dropping a constraint changes no data and the reasoning is the same for each. **Behaviour that changes:** MLMR deleting a model no longer cascades into AIMGF's tables; AIMGF's rows are left naming a model that is gone (the SQLite tests never cascaded, so this makes Postgres match what was tested). **Next decisions, for the schemas themselves:** the six `shared` tables go in a schema of their own (`platform`) that every module's role may use, because every module writes the outbox, idempotency keys and module identity and a copy per module would break the single sweep and the single audit chain; `audit_log` and `audit_head` are written only by R1, so their grants are narrower. The reference rApps' tables count as their own modules (they are separate services with their own databases' worth of tables here).
- **Per-module schemas, the pilot (PR-DB-2.5/2.6).** What the pilot decided for the other fourteen: (1) *a module's schema is the module's name, its role is `smo_<module>`*, listed with its shared tables in `migrations/db_roles.json`, which `scripts/db_roles.py` and the schema check both read. (2) *The search path is the role's default* (`ALTER ROLE ... SET search_path`), not an option the service sets: the code is unchanged, and it survives PgBouncer in transaction mode (which does not carry session options). (3) *Roles are made by a script run after the migration, not by the migration*: roles are cluster-wide, a migration has no password to give, and a managed Postgres may not let the migration user create roles; no password file means no role, so a deployment that has not adopted them is untouched. (4) *Expand, then contract*: moving a table breaks the previous release (it names the table without a schema, and connections it opened keep their search path), so each moved table leaves an updatable view in `public` for one release (ADR 0001); a test runs the old statements through it. (5) *The shared tables stay in `public` for now*, granted per module by name; moving them to a schema of their own comes when most modules have roles, because it touches every module. (6) *Shared tables are granted by name, so the audit chain, outbox and idempotency keys are denied to a module that does not need them*: Onboarding gets `module_identity` only. (7) **Helm is the next PR, not this one**: a pre-upgrade hook Job runs before the chart's Secret is updated, so an upgrade from the previous chart would find the new password key missing; the fix (make the Secret a hook resource, or split role provisioning from the migrate Job) deserves its own change and its own kind test. Until then the chart connects as the owner and Onboarding works through the compatibility view, which the previous-release lane already exercises. **Known limit:** a process reads its role password once, at start, so a rotated password needs a restart; the compose rotation steps say so.
- **Helm adopts the roles (PR-DB-2.6, second half).** The ordering problem the pilot left open: the migrate Job (a `pre-upgrade` hook) runs before Helm applies the release's own resources, so an upgrade from a chart with no roles would run it against a Secret that does not exist yet. Options weighed: (a) make `smo-secrets` itself a hook resource: rejected, because Helm then sees it removed from the release and deletes it after the hook created it; (b) run `db_roles.py` in a post hook: rejected, because with `--wait` the pods cannot become ready without their roles and the post hook waits for them; (c) *a new Secret, `smo-role-secrets`, as a hook resource of its own* (pre-install and pre-upgrade, weight -10, `before-hook-creation`): chosen. It never existed in an earlier release so there is no ownership to untangle, it is created before the Job, its values are read back from the live Secret (`lookup`) so they never change by themselves, and Helm does not delete it when the release changes. The cost: it is not removed by `helm uninstall` (it stays with the Postgres volume, and the README says so). The pod volume became a `projected` volume (the owner's Secret for what the module needs from it, the role Secret for its password), so a module with a role holds `db_password_<role>` and not `db_password`. The kind job now turns the roles off and on again on the running release (the adoption path an upgrade takes) and checks the role is refused other modules' tables. **Local checking:** this change is the first chart change checked with `helm` here (built from source with `go install`), so `test_helm_chart.py` ran in full, 25 tests, instead of being skipped as before.
- **Per-module schemas, first group (PR-DB-2.7).** Ten modules in one revision (`0024`) and one PR instead of ten: the pilot fixed the pattern, dropping nothing and moving no data, and each CI cycle costs about twenty minutes, so ten PRs would have bought ten sequential cycles for the same assurance (the compose replay and the Postgres role test cover every module in one run, and a failure names the module). The group is the modules that touch only their own tables and the shared ones, found by scanning each module's code for the shared libraries it uses (outbox, idempotency, module identity, periodic runs, audit) and for raw SQL (there is none outside the shared library). The reference rApps count as modules: they are separate services. **MLLF and the mocks have no database** (MLLF's chart entry says `database: false`), so there is no role to make. The rest (SME, DME, NFO, RApp Management, A1 Related, FOCOM, AIMgF, RAN NF OAM, R1 Termination) are the second group because they carry what needs a decision: R1 Termination reads RAN NF OAM's `rapp_kill` for the gateway's kill switch (an explicit, read-only grant to document, `read` in the manifest, and the gateway should name the schema instead of relying on the compatibility view), the outbox sweep and the worker's periodic runs sit in RAN NF OAM, and SME's tables are read on every request. **A check that was not checking:** the workflow's `! command` lines (this branch's and two older ones) asserted that something is absent, but a failed `!` does not stop a bash script run with `-e`, so they could not fail; each is now an `if`, and every one of them holds when run.
- **Per-module schemas, second group (PR-DB-2.7).** The nine modules that carried decisions. (1) *R1 Termination gets a role and no schema.* It owns no table; it writes the shared audit chain (`audit_log`, `audit_head`) and reads one table of RAN NF OAM's for the kill switch. That read is a `read` entry in the manifest (SELECT only, `ran_nf_oam.rapp_kill`), so a reviewer sees it and a test proves the role cannot change it; the gateway names the schema through `R1_KILL_SWITCH_SCHEMA` rather than relying on the compatibility view, which goes away one release later. Not done: moving the kill switch behind RAN NF OAM's API (the gateway would then call a module per change; the shared read is the cheaper design and is now explicit). (2) *RAN NF OAM and its worker share one role*: it is one SMO module (one identity, ST-4) that holds the outbox sweep and the periodic runs, so it is granted `periodic_run` and the outbox, and nobody else is. (3) *Shared tables per module are the ones its code uses* (outbox, idempotency keys, module identity), and a module that uses none of one is refused it; the compose replay would show a missing grant as a failed call, and does not. (4) *The CI checks ask the question per module, not for a fixed table*: `scripts/db_roles_denied.py` lists what a role must be refused (one table of each other module and of the shared tables, minus what it owns, is given or may read), because a fixed table is a table some module owns.
- **Postgres HA lab (PR-DB-7.2 to 7.4, PR-HA-3.2).** The ADR chose an operator; this is the proof. Decisions: (1) *CloudNativePG, operator and operand pinned* (manifest sha256, operand by digest) so the lane is reproducible; the operand is PostgreSQL 17.4 because 1.25 does not support 18, which the bundled lab Postgres uses, so the lane also shows the migrations are not tied to 18. (2) *The owner gets CREATEROLE through `postInitApplicationSQL`*, since the migrate Job makes the per-module roles; a managed service must grant the same or the roles be made by hand (already in the chart's notes). (3) *The chart installs with the replicas first in the host list and `target_session_attrs=read-write`*, the form that needs the driver to skip a standby, rather than the `-rw` service that would hide a mistake there; SQLAlchemy cannot parse a comma in the host part, so a list goes in the query (`?host=a,b&port=5432`), which it passes to the driver unchanged. (4) *The failure is an unplanned kill by force-deleting the pod*, the harder case; the measure is a write probe through SME (a row written and deleted every 250 ms) rather than `/health`, because a service can answer health while its database is gone. The longest gap is the recovery time the lane records. (5) *Not done, said so in OPEN_ITEMS*: a planned switchover (needs the cnpg plugin or a status patch) and the full runbook replay in the cluster, which needs a callback receiver and package server reachable from the pods; the e2e checks and the roles script run before and after instead. The job was written without a local cluster (no Docker in the sandbox), so its first CI run is also its first run.


- **PgBouncer in a compose profile (PR-DB-5).** Decisions: (1) *a profile and two variables, not a second compose file*: every URL's host and port are `${SMO_DB_HOST:-postgres}` and `${SMO_DB_PORT:-5432}`, so the default stack is untouched and the pooled one is one command; `migrate` stays direct because DDL and advisory locks want a session. (2) *Credentials*: each module has its own role, so PgBouncer needs every password; its entrypoint builds `userlist.txt` from the same compose secrets (SCRAM against plaintext on both sides) rather than an `auth_query`, which would need a SECURITY DEFINER function in the database; a rotation means restarting it. (3) *`options` is refused by a pooler*, and ignoring it would drop the statement and idle-in-transaction limits silently, so the services stop sending it behind a pooler and PgBouncer's `connect_query` sets the same two limits on every server connection. (4) *Prepared statements*: psycopg 3 prepares a statement after five repeats on one connection; behind transaction pooling the next transaction may land elsewhere. The safe default behind a pooler is off; `SMO_DB_PREPARE_THRESHOLD` turns it back on for a PgBouncer with `max_prepared_statements` (1.21+), which `scripts/pooler_check.py` shows working with the bundled one. (5) *The Helm chart gets no pooler*: the operator route of ADR 0003 brings its own (CloudNativePG `Pooler`), and the flag is the only thing the chart's modules need. The Alpine package was not available to read from the sandbox, so the image build is also first exercised in CI.
- **Staged CM jobs and the status constraints (revision 0026).** Found by the first test to put a staged job through Postgres (PR-HA-4.2): `POST /config-jobs` with `waveSize` returned 500 because `write_config_job_status_check` (from the baseline SQL) lists five states and `JobState` has six (`HALTED`, MGT-5, 0.2.0), and `write_config_sub_change_status_check` lacks `REVERTED` (MGT-5.5). Why it lived for two releases: SQLite (all unit tests) has no such constraint, the Postgres lanes ran single-wave jobs, and `check_migration_matches_models.py` compares columns, not checks. What changed: the revision; a Postgres test comparing the constraints with the code's states; the migration round-trip test's `_schema` now covers every schema and constraint *definitions* (it saw only `public` and constraint names since DB-2, so a revision touching a module's table was invisible to it). What this suggests for v0.5.0's validation work: every `CHECK (x IN (...))` in the schema should be compared with the enum or literal set that writes the column, not just these two; listed there (OPEN_ITEMS, v0.5.0). The reproduction took ten minutes against a local Postgres with the module's own role; the CI lane could not show the log of the failing pod, so the failing job step also keeps the pod logs now.

- **A1, the Near-RT RIC and E2 removed (release 0.5.0).** Decision: A1 policy management, xApps, the Near-RT RIC and E2 are out of scope at every stage (October 2026), so the `a1-related` module, `mock-near-rt-ric` and their wiring were deleted rather than frozen. What the entries above (§5 O-RAN-SC completeness, §7 spec audit, §9 waves, the PR-MSG-1.8 outbox conversion, PR-DB-2.7) say about A1 Related describes the code as it was in `smo-v0.4.0`. Choices: (1) *the four `a1_*` tables stay one release*: a rolling upgrade runs the previous release's code against the new schema, and that code uses them, so the revision that drops them comes after this release (`migrations/table_owners.json` lists them under `_retired` so the orphan-table checks stay honest); (2) *the role `smo_a1_related` is not dropped either*, for the same reason, and its entry left `db_roles.json` and the compose secrets, so nothing connects as it any more; (3) *the gateway answers `404 NO_ROUTE` for `/a1-related`* instead of a tombstone route, since a consumer of an A1 route would have to change anyway; (4) *the breaking-change check learned "removed-module"* (`scripts/check_breaking_changes.py`), so deleting a module's spec is a break that needs a reasoned waiver; (5) the demo runbook keeps its numbering, steps 11 and 22 become one-line notes. Not done: removing the "No A1, Near-RT RIC, xApp or E2" sentences of the sample rApps and call flows 22 to 25, which are true and say what the samples do not use.

### PR-OBS-8 — /version per module, and readiness and version in the GUI

- **Route (OBS-8.1).** `smo_shared/health.py` `install_health` adds `GET /version` beside `/live`, `/ready` and `/health`, so every app that already installs the probes (the nineteen modules, the gateway and the four sample rApps included) gets it with no edit of its own `main.py`. The body is `{module, version, buildSha, builtAt}` (`VersionReport`), read from the environment on every call, not at import, so a test can set it: `MODULE` (the existing build argument; a sample rApp's `samples/` prefix is dropped; with none, the lower-cased app title), `SMO_VERSION`, `SMO_BUILD_SHA`, `SMO_BUILT_AT`, each `unknown` when absent or empty.
- **Image.** The shared `Dockerfile` declares the three as `ARG` with default `unknown` and turns them into `ENV`, as the last layers before `CMD`: a new commit invalidates only that layer, not the dependency and code layers. `docker-compose.yml` has an `x-build-info` anchor of `${SMO_VERSION:-unknown}`, `${SMO_BUILD_SHA:-unknown}` and `${SMO_BUILT_AT:-unknown}`, merged into the `args` of every build (`<<: *build_info`); `scripts/release_images.py` still reads `MODULE` from the same `args`. `release-images.yml` passes `SMO_VERSION` (the plan job's version), `SMO_BUILD_SHA` (the HEAD of the tag's checkout, not the workflow's `github.sha`, which on a manual dispatch is the dispatched branch) and `SMO_BUILT_AT` (UTC, ISO 8601) to `docker buildx build`; the main CI's `docker compose up --build` exports `SMO_BUILD_SHA` as the run's commit. A date is a build argument, so two builds of one commit differ in `builtAt` and are not byte-identical; that is accepted, since the image's digest is what a release pins.
- **Unauthenticated, like the probes.** The gateway's `public_paths` gains `/version` (its OpenAPI operation declares `security: []`), and so do `test_authz_walk.py`'s `PUBLIC_AT_THE_GATEWAY` and its "exactly the public ones" assertion; every backend's `/<module>/version` goes through the catch-all proxy and stays token-gated, which the same walk proves for all routes. `/version` joins `PROBE_PATHS` (access line at DEBUG, no request metric). `docs/openapi/*.json` regenerated: each of the twenty-one specs gains one `GET /version`; the breaking-change check reports no new break (an added path).
- **BFF (OBS-8.2).** `GET /api/modules/status` keeps its `/health` probe (and its `latencyMs`, which stays the health call's) and, for a module that is live, asks `/<module>/ready` and `/<module>/version` in parallel (the gateway's own through `R1Gateway.r1_get`, a public route needing no token). New per-module fields: `ready` (true for 200, false for 503, null when not asked or not answered with either), `version`, `buildSha`, `builtAt` (null unless `/version` answered 200). The probe never raises on the new routes: a timeout, a refused token or a 404 from an older release during a rolling upgrade are nulls, so the table keeps working while versions are mixed. Not taken: a `/version` of the BFF itself (it is not a row of the status table, and its CI deliberately has no `smo_shared`).
- **GUI (OBS-8.3).** `ModuleStatus` in `api/types.ts` carries the four fields; `moduleRows` in `lib/domain.ts` turns them into rows (DOWN, NOT READY, READY or UNKNOWN; version; the commit shortened to seven characters; build time; a dash for null and `unknown`) and flags a module whose commit differs from the most common one, which is how a rolling upgrade shows. The Dashboard's Module health card renders it with `DataTable` under the existing tiles. The Vitest suite includes only `*.test.ts` (no DOM renderer is configured), so the "component test" is on `moduleRows`, the logic of the component, in `lib/domain.test.ts`.
- **Tests.** `shared/tests/test_health.py` (the body, `unknown` when absent or empty, the `samples/` prefix, no readiness check consulted); `tests_integration/test_probes.py` (every app answers it; the gateway's operation is open); `gui-bff/tests/test_main.py` (ready and not-ready, a module without `/version`, a module that is down, the gateway's own routes, SME unreachable); four Vitest cases.
- **Not done:** a Prometheus `smo_build_info` gauge (the version is in `/version` and the GUI only); the Helm chart does not set the three variables (the images already carry them).

### PR-OPS-7 — the configuration reference

- **Generator (OPS-7.1).** `scripts/config_reference.py` parses (never imports) `<module>/app`, `samples/*/app`, `shared/smo_shared`, `sdk/smo_sdk`, `gui-bff/app` and the two scripts that run in the images (`migrate.py`, `db_roles.py`), tests excluded, and finds: `os.environ.get("X", d)`, `os.environ["X"]` (default `required`), `"X" in os.environ`, `os.getenv`, the same on a parameter named `environ` (the `def f(environ=os.environ)` testing seam used all over `smo_shared`), `environ.get("X") or d` (the `or` operand is the default), and `read_secret("X")` of `smo_shared/secretfile.py`, which yields two rows, `X` and `X_FILE`, both secret. Defaults are folded to a constant where the code allows it: literals, module-level constants (including a dict looked up by a constant key, so `DEFAULT_TIMEOUT_SECONDS[kind]` gives each AIMgF timeout its own default), arithmetic, `str()` / `int()` / `float()`, f-strings of those. What cannot be folded is `computed in code`, and the description says how.
- **Wrappers are followed, not reported.** A function that reads the variable named by one of its own parameters (`_seconds(name, default)` in four files, `number(name, default)` inside `engine_options`, `_bool(name, default)` of the GUI settings, `settings_from_env(prefix)` with its closure and `f"{prefix}_MAX_BODY_BYTES"`) is recorded as a wrapper; each call site, in any walked file, is one variable with that call's own name and default. A wrapper whose default is not in the read itself takes its parameter called `default`. A name built from a function's return (`_cred_var(ref, part)`) is evaluated with the run-time parts as `<REF>`, so `NETCONF_CRED_<REF>_PASSWORD` is a pattern row. A name the walk cannot work out at all (the comprehension in `netconf_tls.tls_material_for`, the `for part` loop in `credential_configured`, the dict comprehension in AIMgF's timeout sweep) must be named by a `# config-ref: A, B` comment on its line or the line above, which replaces what the walk found on that statement; without one the read is `unresolved` and CI fails. Three such comments exist (`ran-nf-oam/app/netconf_ssh.py`, `netconf_tls.py`, `aimgf/app/main.py`), no behaviour change. The `${NAME:-default}` substitutions of `docker-compose.yml` are reads of the module `compose` (`SMO_DB_HOST`, `SMO_DB_PORT`, `PGBOUNCER_*`, `POSTGRES_SLOW_QUERY_MS`, the build arguments of `x-build-info`, and the GUI and R1 values it passes through).
- **Table (OPS-7.2).** 124 variables. `docs/CONFIGURATION.md` is prose (how to read it, the `*_FILE` convention, how compose and the Helm chart set a variable, what it does not cover) and, between two markers, the generated tables grouped by owner: `smo_shared` (read in the library, so by every module that uses that part), each module, "several modules" (the gateway's `*_URL` routing table, `SME_URL`, `SMO_ENROLLMENT_SECRET`) and "compose only". Columns: variable, default (several defaults when it is read in several places with different ones; a bare `.get("X")` next to one with a default in the same file is the same variable's guard and not listed), secret, files that read it (no line numbers: they move with every edit, and the document would be stale on each; `python scripts/config_reference.py` prints `file:line`), description. Secret is the name heuristic (PASSWORD, SECRET, KEY, TOKEN) or a `read_secret` read or its `_FILE` form; a description entry may override it with `{"description": ..., "secret": false}` (`IDEMPOTENCY_KEY_TTL_SECONDS`, `SME_TOKEN_AUDIENCE`, `GUI_INITIAL_PASSWORD_FILE`, `SMO_DB_ROLE_PASSWORD_DIR` are not secrets). The descriptions are `docs/config_descriptions.json`, keyed by variable name so regeneration keeps them; the generator renders a missing one as `TODO` and refuses to pass. All 124 are written, from reading the code.
- **CI (OPS-7.3).** `tests_integration/test_config_reference.py`: every variable read in code is in the table, the table lists none that no code reads, no unresolved read, every variable has a description and no description is orphaned, the documented default is the code default, the committed document is exactly what the generator writes, and secrets are marked. The extraction is exercised on a synthetic snippet (each way of reading, a wrapper with its own default, a prefix f-string, a run-time pattern, an annotation, a compose substitution), and the seeded miss runs a snippet with a new read through the same function: the real tree plus that one read is reported as undocumented, and a table with a row nothing reads is reported as stale.
- **Not taken:** reading `values.yaml` and the compose `environment:` blocks to add a "set in" column (the prose says where each deployment sets variables; a column would make the document change whenever compose does); a variable read through `kwargs` or `getattr(os.environ, ...)` is not seen (none exists); variables of third-party images (`POSTGRES_*`, PgBouncer's, nginx) are not the SMO's and are not listed.
### PR-STD — standards and compliance documents (STD-2.1, STD-4.1, STD-4.3, STD-5.1, STD-6.1)

- **The release table (STD-2.1).** `specs/README.md`, "Specification release table": one row per specification in `specs/` that `smo/` cites, and per spec the code cites that has no file (R1AP, SMO-ARCH, O2-DMS, TS 23.222, 32.300, 32.161, 28.552/28.554), with the release as the file states it. Decisions: (1) *the release is what the file says, nothing inferred*: the OpenAPI `info.version` of a 28-series file is the TS version it was generated from, so "Rel-19" is read from `19.x.y`, and the table says so; TS 29.222 and TS 29.482 name their TS version in `externalDocs` (V20.0.0 for five CAPIF files and V19.5.0 for one; V19.1.0), which is quoted; O-RAN YANG gets the range of its `revision` dates. (2) *no version is given for an O-RAN document that is not in `specs/`*: the repository records none, and the cell says "not recorded". (3) *the ~540 other 5G-core files are not listed*: nothing cites them. The table showed the releases are mixed (TS 28.105 at 19.5.0 while 28.104 and 28.312 are at 20.0.0, the Heartbeat file at 18.1.0, the 5GC NRM at 20.3.0), which `docs/STANDARDS.md` now says beside its matrices. **STD-2.2 not taken beyond a minimal list:** only RFC 7807 (obsoleted by RFC 9457) and RFC 7230 (by 9110/9112) are stated as newer, because they are certain from RFC numbering; every newer 3GPP or O-RAN release is "not assessed", and `STD-2.2` stays open. Found while reading `specs/README.md`: it still names `smo/SPEC_AUDIT.md` (merged into `OPEN_ITEMS.md`), lists A1 Related among the compared modules, and says no code references the spec paths (`scripts/ingest_yang_schema.py --library MnS/yang-models` does); left as they were.
- **The personal-data inventory (STD-4.1).** `docs/PRIVACY.md`, section 2: 18 rows from the models, migrations and code (every module's models were searched for person, subscriber and device identifiers). Findings that the inventory made and the rest of the repository had not said: (1) *no subscriber or UE identifier exists in any model*; the two columns with "subscriber" in the name are the SME invoker of an event subscription and a column nothing writes (`mdaf_report.subscriber_attribution`); what a real RAN puts in `pm_file.content` or an alarm text is stored as sent and is not assessed. (2) *The GUI audit log (`gui_audit_log`) is not hash-chained*: it is append-only because a `before_flush` listener refuses an ORM update or delete; the chain is `audit_log` (`smo_shared/audit.py`), which records gateway changes under the invoker id, and a GUI action reaches it as the GUI's own invoker, never the person. (3) *The GUI usernames are copied into module tables* as `smo-gui:<username>` (`write_config_job.requested_by`, `rapp_kill.killed_by`, `dme_action_record.requested_by`, `autonomy_dispatch.rejected_by`, the sample rApp's `override_by`) and as the plain name in `alarm.ack_user_id` / `clear_user_id`, by the `json_overrides` / `query_overrides` of `rbac.py`. (4) *`decided_by` (AI/ML model governance) and `pinnedBy` (host keys) are not pinned to the signed-in user*, unlike `requestedBy`. (5) *`scripts/db_backup.sh` does not back up the GUI's SQLite database* (users, GUI audit, counters), although the README said "every table". (6) *Retention is "none" for nearly every row*: the only deletions are SENT outbox rows (24 h), idempotency records (24 h), revocation rows, and the opt-in purges of CM snapshots and safeguard refusals; `PENDING` / `DEAD` outbox rows and `gui_login_failure` rows for names that never sign in are never removed. (7) *The GUI web server's default access log carries client IP addresses and the request path* (`DELETE /api/admin/users/<name>`); the application stores no IP. New items for what is not built: `STD-4.5` (erasure beyond the account), `STD-4.6` (pin the two fields), `STD-4.7` (tamper evidence for the GUI log), `DB-6.5` (back up the GUI database).
- **Erasure of a GUI user (STD-4.3).** `delete_user` now removes the user's `gui_login_failure` row in the same transaction as the account (it stayed before). There is no session row to remove: a session is a signed token naming the user, and `current_session` refuses a token whose user row is gone, so deleting the user ends every session on every instance; `gui_revoked_session` holds a token id and an expiry only, so it is neither personal data nor touched. *A defect found while writing the test*: a user created under a deleted name started at `token_version` 0 and so accepted the previous holder's unexpired token; `create_user` now starts a new user at `secrets.randbits(30)` (a token carries the version it was issued under, and the comparison is for equality). Seeded first-boot users keep 0. The test (`gui-bff/tests/test_main.py::test_erasing_a_gui_user_end_to_end_and_what_it_leaves_behind`) signs in by cookie and by script token, acknowledges an alarm through the proxy, ends a third session, fails two logins, deletes the user, and asserts what is gone (user row, counter row, both sessions, sign-in), that a re-created user does not revive either token, and what stays (every audit row naming the user, which the ORM still refuses to edit). **What remains and the options, written in `docs/PRIVACY.md` section 4:** the audit rows (append-only by design; on this log the constraint is policy and the ORM guard, since it has no chain) can be kept with a stated period (needs `DB-3.6`), pseudonymised at write time with an opaque per-user id, or rewritten by SQL under change control (possible only because the log is not chained, and it breaks the append-only claim; not recommended). The platform chain is a different matter and the document says so: a row cannot be edited or removed without `verify` failing, so a person's identifier must never be written there; today it is not (the actor is the GUI's invoker). *Not taken:* pseudonymising at write time (it changes what the audit log shows an admin and needs a schema change in the GUI database, whose tables are created by `create_all`, with no migration history), a route that rewrites module columns (a new write path into history), and deleting the audit rows (the log's purpose). The SQL for module columns is given as an example and marked untried. The audit chain format is unchanged.
- **The data-residency statement (STD-6.1).** `docs/DATA_RESIDENCY.md`: where data lives (Postgres; the SQLite file on `gui_bff_data`; `smo_packages`; `smo_scratch`; backups; logs; images) and every outbound path the code has: notifications to caller-registered URLs through `smo_shared.webhook` (literal loopback, link-local and reserved addresses refused; names are not resolved, and private ranges are allowed), the package download by location, NETCONF over SSH or TLS and RESTCONF to registered endpoints, the Kubernetes API with credential delivery on, and image pulls. *Checked rather than assumed:* there is no telemetry, analytics or update check in the code (no client for one; the GUI loads nothing external and its CSP is `self`); there is no outbound O2 call at all (FOCOM imports `httpx` and never calls it; NFO instantiates nothing on a cluster). The signature and provenance of an image are verified against the registry by the operator; nothing leaves a site for it.
- **The control matrix (STD-5.1).** `docs/CONTROL_MATRIX.md`: Annex A of ISO/IEC 27001:2022 by theme (organizational, with the technical controls mapped by their 8.x numbers; people and physical are "not applicable (software product)" with the reason) and the NESAS/SCAS test categories by name. Choices: (1) *conservative statuses*: "Implemented" only where a test or CI job is named; a control with a missing part is "Partial" with the item ID; what lives in GitHub settings (branch protection, required reviews, CodeQL's configuration, secret scanning) is "not verifiable from the repository" and not claimed. (2) *SCAS by category, not by requirement ID*: no SCAS exists for an SMO, and a mapping to TS 33.117 clause numbers would claim a conformance nobody tested; the matrix says it is not a NESAS audit. (3) *Written without the threat model*: `STD-5.1` was to need `SEC-14.2` (the STRIDE table), which is open; the matrix therefore maps what exists to the control themes and names "no threat model" as a gap (5.8, 8.26), and it should be revisited when `SEC-14.1`–`14.3` land. (4) *0.5.0 scope*: `OPEN_ITEMS.md` has no scope block for 0.5.0, so mTLS (`SEC-2`), OIDC (`SEC-6`), MFA and revocation (`SEC-7`), `/bootstrap` (`SEC-9`) and DR (`HA-6`) are cited by ID as Planned, as `docs/VALIDATION.md` V-7c already orders them before the external test. Stale statements found and corrected in `SECURITY.md` in the same change: the Python dependencies are a hashed lock, not unpinned; Dependabot is configured; the TLS profile exists; the Postgres password is generated, not `smo` / `smo`; the session key is stored once in the GUI database, not random per boot. Also noted, not changed: `OPEN_ITEMS.md` `SEC-7.4` still says only a `token_version` bump revokes a session, which has not been true since logout revocation (`gui_revoked_session`).

### PR-SEC-7 — logout revocation (SEC-7.4)

- **Logout ends the session itself (SEC-7.4, done in V-13c).** Every GUI session token carries an id (`jti`); `POST /api/logout` records it in `gui_revoked_session` (primary key `jti`, `expires_at` indexed; created with the other BFF tables by `Base.metadata.create_all`, entries removed once the token would have expired anyway) and `current_session` answers `401 SESSION_REVOKED` for a recorded id, for the cookie and for a Bearer token, and across replicas because the table is shared. A design choice not taken: a row per live session (the step's wording), because a revocation list stays small (only logged-out sessions, purged at expiry) while a session table grows with every login. A token issued before the change has no id and lives until it expires. Tests: `gui-bff/tests/test_main.py` (three) and the browser check `scripts/gui_session_e2e.py`. SEC-7.5 (an admin action to revoke all of a user's sessions) is open; a password change or deactivation already bumps `token_version`.
