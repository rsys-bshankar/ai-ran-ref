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
- **SA-RANOAM-8 (streaming)** — File reporting is built (`POST /pm-files`, `GET /files`,
  `notifyFileReady`). TS 28.532 streaming data reporting is not: there is no streaming transport,
  and `delivery_method=stream` stays a registration (MDAF's `STREAMING` is likewise recorded only,
  `SA-MDA-5`). Approach: a streaming transport shared by RAN NF OAM and MDAF, when a consumer needs one.
- **SA-RANOAM-4 / SA-O1-1 (containment)** — DN refs are parsed and validated and the IOC class is
  taken from the last RDN, but `managedElementRef` is still a flat registry key and there is no DN
  containment tree (the accepted D-9 deviation).
- **SA-RANOAM-1 (reach)** — MSAC guards CM writes only. Reads (`GET .../config`) and the other write
  routes are not evaluated. Approach: reuse `msac.authorize` per route.

Closed in this wave: SA-RANOAM-1 (TS 28.319 Identity / Role / AccessRule, per-sub-change evaluation),
SA-RANOAM-2 (`accessScope`, `scope` kept as an alias), SA-RANOAM-6-severity (`PerceivedSeverity`,
`INDETERMINATE`, upper-case `perceivedSeverity`).

### FOCOM (O2IMS)
- **SA-FOCOM-6 (performance depth)** — `FILE` / `STREAM` performance reporting, `PerformanceMeasurementStore`
  retention and the `reportInterval` / `heartbeatInterval` schedule are not built; records are ingested, not
  collected. Approach: a collector and a file writer when FOCOM talks to a real O-Cloud.
- **SA-FOCOM-7 (real clusters)** — `ProvisioningRequest` is fulfilled at the model level (a `NodeCluster` row);
  nothing is deployed on an O-Cloud, so `PENDING` / `PROGRESSING` / `FAILED` are never observed. Approach:
  drive a real DMS asynchronously and report phases.

Closed in this wave: SA-FOCOM-2 (Location, OCloudSite, pool links, inline resources), SA-FOCOM-6
(AlarmEventRecord, AlarmSubscription, performance records / jobs / NOTIFICATION subscriptions), SA-FOCOM-7
(Artifacts, Cluster, Infrastructure, ProvisioningRequest resources), SA-FOCOM-9 (closed resource types, seeded,
`POST /resource-types`, auto-registration behind `FOCOM_AUTO_REGISTER_RESOURCE_TYPES`).

### MLMR (TS 29.482)
- **SA-MLMR-6 (location)** — `accessReqs.location` is stored, not enforced: no requester location exists.
  Approach: take a location from the invoker's registration if the platform ever models one.
- **SA-MLMR-7 (phases)** — AIMgF writes `phaseInfo.phase` at training start (`IN_TRAINING` /
  `IN_RETRAINING`) and success (`TRAINED`) only; validation and deployment do not write `VALIDATED` /
  `DEPLOYED`. Approach: write the phase from the lifecycle FSM transitions in `_fire_model_event`.
- **SA-MLMR-1 (spec edges)** — the `MLModel` `anyOf` and the forward-compatible free-string enum
  values are not honoured; a model cannot be created from a profile (no type / version in
  `mlModelInfo`).

Closed in this wave: SA-MLMR-1 (`/storages`, profiles), SA-MLMR-6 (`storeDiscReqs` enforced for
discovery and download), SA-MLMR-7 (`phaseInfo.trainingInfo.baseModelId` lineage from AIMgF),
SA-MLMR-8 (`usageReqs`), SA-MLMR-9 (whole-object `filt-criteria` discovery).

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
