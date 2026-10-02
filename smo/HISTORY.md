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
- **OI-C-demo-csar** — `samples/hello-world-rapp/` + `build_csar.py`; onboard → deploy → bootstrap →
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
- **Not taken, still open.** ST-8.3, adoption: there is still no periodic task (ST-1.4), so no caller. Who ticks (a Kubernetes CronJob, an
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
