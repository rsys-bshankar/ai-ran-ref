# Open items

Items still open in the Phase 1 SMO reference build, checked against the code. What was
decided and built is in [`HISTORY.md`](HISTORY.md). IDs keep their original numbering (`OI-…` from the former
`OPEN_ITEMS.md`, `SA-…` from the former `SPEC_AUDIT.md`, `W…` from the wave plan).

Each item: what is missing, why it matters, suggested approach.

## 1. Design decisions without an answer

- **OI-1-weighted-triggers** — `WEIGHTED_TRIGGERS` group-retrain propagation raises
  `NotImplementedError` (`aimgf/app/statemachine.py`). Needs real per-model noise-floor data; a
  weighting invented without it would be arbitrary. Approach: collect breach statistics from
  `MLMFSubscription` performance reports, then design the weighting (LLD §4.4 revisit trigger 3).
- **OI-1-alarm-storm** — No alarm-storm correlation algorithm in `ran-nf-oam/`;
  `correlation_group` is a coarse string. No audited O-RAN-SC repo implements one either.
  Approach: wait for real alarm traces; start with time-window + topology (`neighbourRefs`) grouping.
- **OI-1-a1-ml** — A1-ML operations are out of scope (A1 Related LLD §0); schema dormant. Revisit only
  if that scope decision changes.
- **OI-6.1-runtime-gate** — `RuntimeLifecycle` transitions (Deploy/Activate/Scale/Terminate, call
  flow 17) have no operator approval beyond the `MODEL_NOT_CERTIFIED` guard. Whether the OI-6.1
  gate should extend to them is undecided. Approach: if yes, reuse the self-loop governance-event
  pattern (`APPROVE_DEPLOY`-style event + flag) through `POST /models/{id}/advance`.

## 2. Platform gaps

- **OI-1-sa-rollback** — SA SMOS `ROLLBACK` always returns `ROLLBACK_HISTORY_UNAVAILABLE` (501):
  rApp Mgmt deletes the prior `RAppInstance` row on a successful upgrade commit, so no version
  history exists. Approach: keep the superseded instance (or a version record) in rApp Mgmt, then
  dispatch a rollback upgrade from SA SMOS.
- **OI-1-upgrade-identity** — `start_upgrade` (`rapp-mgmt/app/upgrade.py`) creates the replacement
  instance without an `oauth_client_id`, so it has no SME/DME identity and producer reconsideration
  skips `UPGRADE_COMMIT`. Approach: mint an id in `start_upgrade`, re-run bootstrap registration,
  deregister the old identity on commit.
- **OI-2-terminate-workload** — `TerminateInstance` (`rapp-mgmt/app/main.py`) revokes the
  credential and stops the usage registration but makes no NFO call, so the rApp's workload keeps
  running after the instance is `UNDEPLOYED`. Approach: store the `nfDeploymentId` from
  `CreateInstance` and call NFO's terminate (`DELETE /nfo/deployments/{id}`) on `TERMINATE`.
- **OI-2-upgrade-completeness** — `start_upgrade` creates a bare replacement row: no
  configuration, autonomy mode, region scope, NFO deployment or usage registration, and
  `newPackageId` is not checked for `AVAILABLE`/`PRIMED`. `resolve_upgrade` deletes the old row
  without usage/stop or DME/SME deregistration, so the old package's deprime and delete guards stay
  blocked; `upgradeTimeoutSeconds` is stored but never enforced. Call flow 07. Approach: run the
  replacement through `CreateInstance`'s path, and on commit run the old row's `TERMINATE` side
  effects before deleting it.
- **OI-2-lcm-error-mapping** — Lifecycle routes return 500 for an illegal transition (recover,
  terminate, upgrade, a critical fault on a non-`RUNNING` instance; onboarding deprecate, prime,
  cancel-delete) and for an unknown id. Onboarding's deprime/delete map every illegal transition to
  409 `SERVICE_NAME_CONFLICT`, so DELETE on a `PRIMED` package reports "blocked by a dependent"
  rather than "not allowed from PRIMED". `TERMINATE` is only legal from `RUNNING`, so a `FAULTED`
  instance must recover before it can be retired. Approach: map `IllegalTransition` to a 409 naming
  the state and event, 404 on unknown ids, and allow `TERMINATE` from `FAULTED`.
- **OI-2-package-redeploy** — A deleted package stays in terminal `DELETING`, and onboarding's
  duplicate-hash check ignores state, so the same CSAR can never be onboarded again. Approach:
  exclude `DELETING`/`FAILED` rows from the duplicate check.
- **OI-1-cm-sync-restconf** — RESTCONF has no dispatch: an ME provisioned for RESTCONF is rejected
  `PROTOCOL_NOT_SUPPORTED`, although W9 lets vendors declare `O1_RESTCONF`. Approach: add a RESTCONF
  client beside `netconf_client.py` (PATCH/PUT/DELETE on the data resource) and a mock endpoint.
- **OI-2-compose-e2e** — The full `docker-compose` stack has never been run end to end (no Docker
  daemon in the sandbox or CI); only `docker compose config` is checked. The `a1_mock_net` isolation
  (RT-7) is unverified. Approach: a CI job on a runner with Docker that brings the stack up and runs
  `DEMO_RUNBOOK.md` against it.
- **OI-2-oauth2-scope** — `/oauth2/token` accepts and echoes `scope` without checking it against
  published AEFs/APIs; tokens are opaque and introspected, no JWT/IdP. Approach: check `scope`
  against `ServiceProfile`/`TrustedInvoker` entries; optional signed JWT if an IdP is added.
- **SA-SME-1-public-key** — `InvokerRegistration.public_key` is stored but never used; no signature
  verification exists. Approach: verify a signed token request or client assertion with the key.
- **OI-5-sme-filters** — Event subscriptions cannot filter by `apiInvokerId` (no invoker-onboarding
  events) or `aefId`; `discover_services` has no `category`. Approach: emit `API_INVOKER_*` events
  from invoker registration, then add both filters; add `category` only with a source for it.
- **OI-5-a1-scope** — `subscriptionScope` OWN/OTHERS is treated as ALL; no subscriber identity is
  tracked. Approach: record the subscriber's rApp id (from the R1 token) and compare with
  `creator_id`.
- **OI-5-a1-ric-inventory** — Policy types are the hardcoded `KNOWN_POLICY_TYPES`; `policySchema` is
  a placeholder; no `GET /rics`. Approach: fetch types and schemas from the Near-RT RIC (A1-P
  `GET /policytypes`) and model a RIC inventory if more than one RIC is introduced.
- **OI-5-aiml-trainingjob-steps** — `TrainingJob` keeps a flat `status`; the reference's step×status
  tracking (DATA_EXTRACTION/TRAINING/TRAINED_MODEL) is not adopted. Approach: add a separate,
  additive `step` field driven by the NFO execution runtime (OI-6.2) rather than replacing `status`.
- **OI-5-aiml-featuregroup-dme** — `FeatureGroup.enableDme` is stored but no DME data job is
  created. Approach: on create with `enableDme`, create a DME DataJob for the group's type.
- **OI-3-nfo-abnormal** — NFO `DELETING`/`ABNORMAL` are unreachable through the API while Terminate
  is synchronous. Tracked only for the day Terminate becomes asynchronous (real Helm uninstall).
- **W10-alarm-cellref** — RAN NF OAM alarms carry no cell reference, so the EnergySaving, Coverage
  and Traffic Steering rApps hold a whole managed element on any critical alarm. Approach: add an
  optional `cellRef` (or `managedObjectInstance` DN) to `Alarm` and filter guards by it.

## 3. Spec conformance still open

### RAN NF OAM
- **SA-RANOAM-1** — MSAC is a presence check of `msacRole` when `scope == "entire-RAN"`, not
  TS 28.319 Identity/Role/AccessRule RBAC. Matters for multi-tenant CM writes. Approach: model
  Role/AccessRule with `dataNodeSelector` and evaluate per sub-change before dispatch.
- **SA-RANOAM-2** — `WriteConfigRequest.scope` collides with ProvMnS `ScopeType`. Approach: rename to
  `accessScope` with a deprecation alias.
- **SA-RANOAM-6-severity** — `severity` is CHECK-constrained to lowercase
  critical/major/minor/warning/cleared; TS 28.111 `PerceivedSeverity` has six upper-case values
  including INDETERMINATE. Approach: add INDETERMINATE and map case at the API boundary.
- **SA-RANOAM-4 / SA-O1-1** — Flat ref strings instead of DN/typed addressing (accepted D-9-style
  deviation). Approach if needed: LDN parsing on `managedFunctionRef`, keyed by IOC class.
- **SA-RANOAM-8 / SA-MDA-5** — RAN NF OAM has no TS 28.532 file or streaming data reporting;
  MDAF serves FILE reports (`GET /mda-reports/{id}/file` + file-ready notification) but records
  `STREAMING` only. Approach: reuse MDAF's file-ready pattern for PM in RAN NF OAM; streaming later.

### FOCOM (O2IMS)
- **SA-FOCOM-2** — `ResourcePool` lacks `oCloudSiteId` and the inline `resources` array; no
  `OCloudSite`/`Location`, so `GET /inventory` returns empty `locations`/`oCloudSites`. Approach:
  add a seeded `OCloudSite`, link pools to it, inline resources in the pool view.
- **SA-FOCOM-6** — Alarms/performance are flat (three-field `OCloudAlarm`, no subscribe/notify, no
  performance ingest). Approach: `AlarmEventRecord` with X.733 `eventType` and an `AlarmSubscription`
  notify path reusing `smo_shared.webhook`.
- **SA-FOCOM-7** — No ProvisioningRequest, Artifacts, NodeCluster or Infrastructure resources
  (single-cluster Phase 1 scope). Build only with multi-cluster support.
- **SA-FOCOM-9** — `provision_resource` auto-registers unknown `resourceTypeId`s although O2IMS
  `ResourceType` is read-only. Approach: reject unknown types (404) behind a flag, after seeding the
  types callers use.

### MLMR (TS 29.482)
- **SA-MLMR-1** — No `MLModelsStorage`/`MLModelProfile` layer (accepted flat shape).
- **SA-MLMR-6** — No `storeDiscReqs` (retention, access requirements). Storing without enforcing
  would mislead. Approach: add with enforcement in `download_model_artifact` once a backend authz
  model exists (RBAC is only in `gui-bff/` today).
- **SA-MLMR-7** — `trainingInfo` mostly absent, notably `baseModelId` lineage. Approach: set
  `baseModelId` from AIMgF retrain/coordination-group paths.
- **SA-MLMR-8** — No `MLModelUsage` (TRAINING/INFERENCE). Small: a validated list column on
  `MLModel`.
- **SA-MLMR-9** — `ModelInformationDiscovery` whole-object `filt-criteria` not supported;
  `discover_models` filters by `model_type` only.

### Intent Service (TS 28.312)
- **SA-INTENT-partial** — 8 value datatypes are accepted without enforcing their inner structure
  (UEGroup, QoSId, CivicArea, CivicAddress, Frequency, ReportingCondition, TimeCondition,
  TargetFulfilmentCondition; `docs/ROADMAP.md`). Approach: add schemas
  one datatype at a time where a family uses it.

### SME / O1 vendor models
- **SA-O1-4** — WG10-O1NRM and WG5 O-DU/O-CU IOCs (ORU, NearRTRICFunction, EP_*, NESPolicy,
  RRMPolicyRBAlloc, CTI*) are not modelled; W9's registry supports per-vendor schemas but only the
  bundled TS 28.541 descriptor ships. Approach: ingest the YANG-derived descriptors with
  `scripts/ingest_cm_schema.py` when a vendor needs them.

## 4. Wave backlog

- **W10.4-merge** — Wave 10.4 (Traffic Steering) exists as a WIP commit (`784c5f6`); no merged PR yet.
- **W10-B1** — EnergySaving LSTM model variant (D-6 backlog); the shipped model is threshold +
  regression. Approach: add as a second model type in the same package, compared in validation.
- **W10.3-thresholds** — Coverage objective uses fixed 5 % thresholds per problem class; per-cell,
  per-class thresholds from the TS 28.541 CCO parameter sets are a refinement.

## 5. Test coverage

- **OI-4** — Coverage is uneven. By `def test_` count today the shallowest suites are `mllf` (5),
  `ran-analytics` (13), `mock-o1-adaptor` (14), `so-smos` (15), `mock-near-rt-ric` (16) and
  `r1-termination` (18). Approach: add route-level tests to `mllf` first (its routes are the
  CERTIFIED gate in every rApp deployment); the others were last surveyed as near-complete.
