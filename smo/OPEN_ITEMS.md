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

- **OI-5-a1-scope** — `subscriptionScope` OWN/OTHERS is treated as ALL; no subscriber identity is
  tracked. Approach: record the subscriber's rApp id (from the R1 token) and compare with
  `creator_id`.
- **OI-5-a1-ric-inventory** — Policy types are the hardcoded `KNOWN_POLICY_TYPES`; `policySchema` is
  a placeholder; no `GET /rics`. Approach: fetch types and schemas from the Near-RT RIC (A1-P
  `GET /policytypes`) and model a RIC inventory if more than one RIC is introduced.

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

## 4. Test coverage

- **OI-4** — Coverage is uneven. By `def test_` count today the shallowest suites are `mllf` (5),
  `ran-analytics` (13), `mock-o1-adaptor` (14), `so-smos` (15), `mock-near-rt-ric` (16) and
  `r1-termination` (18). Approach: add route-level tests to `mllf` first (its routes are the
  CERTIFIED gate in every rApp deployment); the others were last surveyed as near-complete.
