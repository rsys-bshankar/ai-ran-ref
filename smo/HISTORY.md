# History — decisions and closed items

What the Phase 1 SMO reference build decided and built, condensed from the former
`HISTORY.md` (§1–§6, "Closed", "Suggested next pass"), `HISTORY.md §7` and the Wave
exit reviews. Items still open are in [`HISTORY.md`](HISTORY.md). Code comments cite
entries here by section (`HISTORY.md §5`) or ID (`HISTORY.md OI-6.3`).

**ID scheme**

| Prefix | Origin |
|---|---|
| `OI-1-…`, `OI-2-…`, `OI-3-…`, `OI-4` | Former `HISTORY.md` §1 (design decisions), §2 (code/lifecycle gaps), §3 (call-flow gaps), §4 (test coverage) |
| `OI-5-<module>-…` | Former §5, O-RAN-SC repo-audited completeness gaps |
| `OI-C-…` | Former "Closed" section entries not already covered by §1–§5 (pilot demo, audits) |
| `OI-6.1` … `OI-6.7` | Former §6, AI/ML pipeline review (numbers kept) |
| `SA-<area>-<n>` | Former `HISTORY.md §7`, numbered as in that file's per-module sections |
| `W0` … `W10.4` | Waves of the AI Platform Service Decomposition and `docs/ROADMAP.md` |

PR numbers are given where the squash-merge title or the original text names them.

Recurring conventions referred to below:
- **Best-effort notification**: an unreachable callback never fails the primary call; every
  caller-supplied callback goes through `smo_shared.webhook` (OI-6.3).
- **Lazy staleness**: no scheduler exists; liveness/expiry is computed at read or gate time.
- **Owned-child delete**: `ON DELETE CASCADE` on the FK plus explicit application cleanup.
- **Postgres verification**: schema changes are checked against a real Postgres 16 with
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
  `O1AdaptorEndpoint.adaptor_uri`. A RESTCONF-provisioned ME is rejected `PROTOCOL_NOT_SUPPORTED`.
  `cm_schema_cache` holds real descriptors since W9.
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
  is unreachable. Secrets stored as salted scrypt, tokens as SHA-256. Scopes are echoed, not checked.
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
  A full `docker compose up` run stays open (OPEN_ITEMS OI-2-compose-e2e).

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
- **OI-3-nfo-abnormal** — NFO `DELETING`/`ABNORMAL` dispatch is correct but unreachable while
  Terminate is synchronous; not a gap. Revisit if Terminate becomes asynchronous.
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

### SME (`sme/`) vs `nonrtric-plt-sme`
- **OI-5-sme-events** — `register_service` fires `SERVICE_API_AVAILABLE`/`SERVICE_API_UPDATE`,
  `deregister_service` fires `SERVICE_API_UNAVAILABLE`; `notify_service_change` enforces the same
  authz gate as `discover_services`.
- **OI-5-sme-apiids** — `apiIds` filter on `SubscribeEvents`. `apiInvokerId`/`aefId` filters not
  implemented (residual).
- **OI-5-sme-discover** — `discover_services` filters on `aefId`/`protocol`/`dataFormat`/`commType`;
  `category` has no model concept (residual).
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
  `producerRappId`; `POST`/`GET /training-jobs/{id}/model-metrics`. Step×status tracking not
  adopted (residual).
- **OI-5-aiml-featuregroup** — `POST`/`GET /feature-groups`, name rule `\w+` 3–63, duplicate 409;
  `enableDme` stored, not acted on.
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
- **SA-RANOAM-1** MSAC gate is a presence check on `msacRole` — open (structural).
- **SA-RANOAM-2** `scope` name collides with ProvMnS `ScopeType` — open (cosmetic).
- **SA-RANOAM-3** `WriteConfigSubChange.operation` (merge/replace/create/delete/remove, RFC 6241 §7.2,
  default merge, CHECK constraint); emitted on `<managed-object>`; mock adaptor accepts empty
  delete/remove payloads. (Closeable-list item 3.)
- **SA-RANOAM-4** Flat ref strings instead of DN addressing — accepted deviation.
- **SA-RANOAM-5** `alarmType` (11-value enum, CHECK). (Closeable item 1.)
- **SA-RANOAM-6** `ackUserId` and `alarmChangedTime` (`changed_at` updated on ack/clear). Severity
  enum residual in OPEN_ITEMS. (Closeable item 2.)
- **SA-RANOAM-7** `PMSubscription.granularityPeriod` (nullable). (Closeable item 4.)
- **SA-RANOAM-8** File/streaming transport — elided.
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
- **SA-FOCOM-2/6/7/9** open or accepted — see OPEN_ITEMS.

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

### SME vs CAPIF core source (`SA-SME-n`)
- **SA-SME-1** Invoker onboarding takes only `apiInvokerPublicKey`; server mints `apiInvokerId` and
  `onboardingSecret` (hashed). Public key stored, unused (OPEN_ITEMS). (Moderate item 3.)
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
- **SA-MLMR-1/6/7/8/9** open — see OPEN_ITEMS.

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
- **SA-O1-4** WG10/WG5 IOCs: per-vendor own/spec/combined conformance (#125); registry built in W9.
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
Consolidated work items and frozen decisions in `docs/ROADMAP.md`.
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
Exit: `docs/ROADMAP.md` — 20/20 IOCs, 125/126 attributes; one deviation,
`MLTrainingFunction.ThresholdMonitors` (TS 28.623 containment).

### W5 — TS 28.104 at REST level (MDAF) — #142
MDAFunction/MDARequest/MDAReport with typed AnalyticsReport/PredictionReport/DriftReport;
`TRAFFIC_FORECAST` + traffic-trend PREDICTIONS_PM_DATA report via `sdk.analytics`; DriftReport →
AIMgF retrain notification. Exit: `docs/ROADMAP.md` — 48/48; deviations: addressing,
STREAMING transport.

### W6 — strict TS 28.312 (Intent Service) — #143
Intent, IntentReport (seven kinds), IntentHandlingFunction, IntentUtilityFormula; structured,
family-checked expectations; energy-saving template `sdk.intent.energy_saving_expectation`;
IntentReport carries action refs. Exit: `docs/ROADMAP.md` — 83/91 compliant, 8 partial
value datatypes; all callers migrated.

### W7 — runtime realization (MLTF/MLVF/MLEF/MLIF) — #144
Gap analysis `docs/ROADMAP.md`; per-execution-mode runtime profiles from the
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
