# SMO AI Platform Architecture

This is the architecture of the SMO AI Platform in `smo/`: its layers, the
rules that hold it together, which service owns what, how a RAN vendor is
onboarded over O1, and how the reference rApps use the platform.

Changing a rule or an ownership line here is an architecture decision: edit
this document first, then the code. For the work plan and standards
compliance see [ROADMAP.md](ROADMAP.md). For how the platform reached this
shape (the Wave 0–3 decomposition, corrections to the source plan, audit
history) see [HISTORY.md](../HISTORY.md).

## Contents

- [Layered architecture](#layered-architecture)
- [Golden rules](#golden-rules)
- [Service map](#service-map)
- [Repository layout](#repository-layout)
- [R1 API conventions](#r1-api-conventions)
- [Service ownership](#service-ownership)
  - [Ownership at a glance](#ownership-at-a-glance)
  - [AIMgF](#aimgf) · [MLMR](#mlmr) · [MLLF](#mllf) · [MDAF](#mdaf) · [DME](#dme) · [Intent Service](#intent-service)
- [O1 vendor onboarding](#o1-vendor-onboarding)
- [Reference rApps](#reference-rapps)
- [Related documents](#related-documents)

## Layered architecture

```
+------------------------------------------------+
|                Operator / OSS                  |
+------------------------------------------------+
                    |  Intent Service
                    v
+================================================+
|                    SMO                         |
+================================================+
|           Platform Services Layer              |
|  SME | DME | MDAF | AIMgF | MLMR | MLLF        |
|  Intent Service | RAN NF OAM                   |
|  Onboarding | rApp Management                  |
+------------------------------------------------+
                    ^
                    |  R1 (R1 Termination)
                    v
+------------------------------------------------+
|             AI Runtime SDK Layer               |
|  sdk.data | sdk.analytics | sdk.models         |
|  sdk.lifecycle | sdk.intent | sdk.platform     |
+------------------------------------------------+
                    ^
                    v
+------------------------------------------------+
|                rApp Layer                      |
|  EnergySaving | Mobility Optimization          |
|  Coverage Optimization | Traffic Steering      |
|  hello-world (sample)                          |
+------------------------------------------------+
                    ^
                    v
+------------------------------------------------+
|           Runtime Execution Layer              |
|  TRAINING   -> MLTF    VALIDATION -> MLVF      |
|  EMULATION  -> MLEF    INFERENCE  -> MLIF      |
+------------------------------------------------+
                    ^
                    v
+------------------------------------------------+
|           Infrastructure Layer                 |
|  NFO | Kubernetes/Docker (unmodeled southbound)|
|  O2IMS/O2DMS (FOCOM) | GPU/NPU/CPU             |
+------------------------------------------------+
```

## Golden rules

1. **Platform Services are permanent services.** SME, DME, MDAF, AIMgF,
   MLMR, MLLF, Intent Service, RAN NF OAM, Onboarding and rApp Management
   are long-lived, independently deployed services, not modules of
   convenience.
2. **rApps are business logic.** The reference rApps under `samples/` are
   consumers of the platform, never platform internals.
3. **MLTF/MLVF/MLEF/MLIF are runtime roles, not services.** They are
   execution modes of one runtime (TRAINING → MLTF, VALIDATION → MLVF,
   EMULATION → MLEF, INFERENCE → MLIF), scheduled by NFO. There is no
   `mltf/` module. See [ROADMAP.md#runtime-realization](ROADMAP.md#runtime-realization).
4. **NFO owns execution placement.** Where and how a runtime executes is
   NFO's decision, never AIMgF's.
5. **AIMgF owns lifecycle.** AIMgF decides what state a model or runtime is
   in and whether a transition is allowed. It does not train, validate,
   emulate, infer or store anything itself.
6. **R1 owns service exposure.** Every platform service is reached through
   R1 Termination's gateway (`r1-termination/`). Nothing bypasses it; the AI
   Runtime SDK (`sdk/smo_sdk/`) is a thin client over that same path, not a
   second one.

## Service map

| Service | Module | Standard it realizes |
|---|---|---|
| SME | `sme/` | O-RAN (CAPIF-derived) |
| DME | `dme/` | O-RAN ICS-derived data plane + O1 Adaptor MnS mapping (O1 action mediation) |
| MDAF | `mdaf/` | 3GPP TS 28.104 (MDA NRM) |
| AIMgF | `aimgf/` | 3GPP TS 28.105 (AI/ML NRM): lifecycle, requests, functions |
| MLMR | `mlmr/` | 3GPP TS 28.105 (MLModel, repository) + TS 29.482 AIMLE MLR |
| MLLF | `mllf/` | TS 28.105 deploy-request gate and node-group targeting |
| Intent Service | `intent-service/` | 3GPP TS 28.312 (Intent NRM) |
| NFO | `nfo/` | O-Cloud / O2 (deployment) |
| FOCOM | `focom/` | O2IMS |
| RAN NF OAM | `ran-nf-oam/` | O1 (CM/FM/PM/SWM, per-vendor capability registry) |
| RAN Analytics | `ran-analytics/` | Use-case analytics producers; an MDAF consumer |

A1 policy (`a1-related/`) is a separate concept from intents and is not part
of the Intent Service.

## Repository layout

| Group | Modules |
|---|---|
| AI platform services | `aimgf/`, `mlmr/`, `mllf/`, `mdaf/`, `intent-service/`, `dme/` |
| Other platform services | `sme/`, `nfo/`, `focom/`, `ran-nf-oam/`, `onboarding/`, `rapp-mgmt/`, `a1-related/`, `sa-smos/`, `so-smos/`, `ran-analytics/` |
| Exposure | `r1-termination/` (R1 gateway), `sdk/` (AI Runtime SDK), `gui/` + `gui-bff/` |
| Southbound simulators | `mock-o1-adaptor/`, `mock-near-rt-ric/` |
| Shared library | `shared/smo_shared/` (DB, errors, pagination, correlation, webhook, R1 client, OpenAPI security) |
| rApps | `samples/` (four reference rApps + `hello-world-rapp`) |
| Tooling and tests | `scripts/`, `migrations/`, `tests_integration/` |

**rApp packaging.** A CSAR may carry two optional root files,
`manifest.yaml` and `capabilities.yaml`, validated by Onboarding
(`onboarding/app/main.py`). `capabilities.yaml` declares which of the SDK's
six namespaces (data / analytics / models / lifecycle / intent / platform)
the rApp consumes or provides. `manifest.yaml` declares `executionModes`,
`autonomyModes`, `requiredServices` and per-mode `runtimeProfiles`. A package
without either file onboards unchanged.

## R1 API conventions

Every R1-facing service applies the same conventions, implemented once in
`shared/smo_shared/`:

| Concern | Convention |
|---|---|
| Authentication | R1 Termination introspects every proxied bearer token against SME's issuer (RFC 7662). Each service's OpenAPI declares the `r1BearerAuth` HTTP-bearer scheme (`openapi_security.py`). Exempt: SME `/oauth2/token`, `/oauth2/introspect`; R1 Termination `/health`, `/bootstrap`. The southbound mocks (`mock-o1-adaptor`, `mock-near-rt-ric`) are not R1-facing. |
| Versioning | `info.version` is the R1 contract version (`R1_CONTRACT_VERSION`, `1.0.0`). |
| Errors | RFC 7807 ProblemDetails (`type`, `title`, `status`, `detail`, `instance`) via `framework_error()` / `FrameworkError` (`errors.py`). A1 policy management keeps its own A1 error table. |
| Pagination | Every DB-backed list returns `{items, total, limit, offset}` from a SQL `LIMIT`/`OFFSET` plus `COUNT(*)` (`pagination.py`). Exceptions: fixed enums (A1 `/policy-types`) and spec-fixed shapes (A1-PMS `/services`; CAPIF `GetApfIdServiceApis` / `DiscoverServices` in SME). The GUI's `useSmo()` and the SDK's `ensure_ok()` unwrap `items`. |
| Subscriptions | Subscription resources name their callback `notificationDestination`, unless a real external spec fixes another name (FOCOM `callback` per O2ims, SME `callbackUri` per CAPIF). One-off job callbacks (`InferenceJob.notificationDestination`, `TrainingJob.notificationUri`) are not subscriptions. |
| Callbacks | Any caller-supplied callback URL is called through `smo_shared.webhook`. |
| Correlation | `X-Correlation-ID` (`correlation.py`): middleware assigns one when absent; `R1Client` propagates it on every downstream call; R1 Termination forwards its own current id. It is not declared per operation in OpenAPI. See call flow 14. |
| Cross-module calls | Always `R1Client` through R1 Termination, never a direct service URL. |

## Service ownership

### Ownership at a glance

One line per service:

- **AIMgF** = state + decisions.
- **MLMR** = model truth.
- **MLLF** = the deploy-request gate + node-group targeting (runtime truth is AIMgF + NFO's `RuntimeLifecycleState`).
- **NFO** = runtime truth (where and how it runs).
- **MDAF** = analytics truth.
- **DME** = data truth, plus O1 action mediation (O1 protocol dispatch is RAN NF OAM's).
- **Intent Service** = intent truth.

AI/ML responsibilities across AIMgF, MLMR and MLLF:

| Function | AIMgF | MLMR | MLLF |
|---|---|---|---|
| Lifecycle state | ✅ | ❌ | ❌ |
| Model metadata | ❌ | ✅ | ❌ |
| Model artifact registry | ❌ | ✅ | ❌ |
| Version control | ❌ | ✅ | ❌ |
| Training / validation / emulation request | ✅ | ❌ | ❌ |
| Inference runtime request | ✅ | ❌ | ❌ |
| Load/activate model (`RuntimeLifecycleState`, jointly with NFO) | ✅ | ❌ | ❌ |
| Deploy-request gate / node-group targeting | ❌ | ❌ | ✅ |
| NFO invocation | ✅ | ❌ | ❌ |

Cross-module references are bare UUIDs (for example `TrainingJob.model_id`
into MLMR), resolved over R1 rather than by reading another module's tables.

### AIMgF

AI Management Function (`aimgf/`). AIMgF is the AI lifecycle orchestrator:
it makes lifecycle decisions and orchestrates MLMR, MLLF, DME, MDAF, NFO and
the rApp execution roles. It does not train, infer or store models.

| Owns | Does not own → owner |
|---|---|
| Model and runtime lifecycle state machines | Model identity, artifacts, versions, coordination groups → MLMR |
| Training / validation / emulation / inference requests and job state | Deploy-request gate, node-group targeting → MLLF |
| Governance: approval, certification, promotion, rollback | Data, datasets, feature sets → DME |
| NFO invocation (runtime create / scale / terminate) | Analytics reports, predictions, drift → MDAF |
| TS 28.105 functions, requests, processes and reports (see [ROADMAP.md#ts-28105](ROADMAP.md#ts-28105)) | Business logic → rApps |
| MLMF performance subscriptions (`/mlmf/subscriptions`), feature groups (`/feature-groups`) | |

#### AIMgF state machines

`aimgf/app/statemachine.py` holds two independent FSMs on one
`ModelLifecycle` row per model:

- **Model lifecycle** (`ModelLifecycleState`, 14 states): `REGISTERED` →
  `TRAINING` → `TRAINED` → `VALIDATING` → `VALIDATED` → `EMULATING` →
  `EMULATED` → `PENDING_APPROVAL` → `APPROVED` → `CERTIFIED` → `PROMOTED`,
  plus `DEPRECATED`, `RETIRED` and `FAILED`. `ROLLBACK` returns `PROMOTED` →
  `CERTIFIED`. `REJECT` sends `PENDING_APPROVAL` → `FAILED`; a `FAILED` model
  can be retrained or retired. There is no lightweight update path:
  retraining a `PROMOTED` model re-enters at `TRAINING`.
  Operator gates `APPROVE_TRAINING` and `APPROVE_VALIDATION` (self-loops on
  `TRAINED` / `VALIDATED`) must be fired before `CREATE_VALIDATION` and
  `CREATE_EMULATION` respectively.
- **Runtime lifecycle** (`RuntimeLifecycleState`, 8 states, jointly owned
  with NFO): `NOT_DEPLOYED` → `DEPLOYMENT_REQUESTED` → `DEPLOYED` →
  `ACTIVATING` → `ACTIVE`, with `SCALING` (back to `ACTIVE`) and
  `TERMINATING` → `TERMINATED`. Retraining a promoted model does not take its
  runtime down, and a runtime can scale or terminate without touching
  certification.

#### AIMgF domain model

| Aggregate | Route(s) |
|---|---|
| `ModelLifecycle` (`model_lifecycle_state`, `runtime_lifecycle_state`, `training_job_id`, `cleared_node_groups`, NFO descriptor / deployment ids) | `GET /models/{id}/lifecycle`, `GET /model-lifecycles`, `POST /models/{id}/advance` |
| `TrainingJob` (= TS 28.105 MLTrainingRequest) | `POST /training-jobs`, `.../complete`, `.../suspend`, `.../resume`, `DELETE /training-jobs/{id}` |
| `ValidationJob` (= MLTestingRequest; requires `TRAINED`) | `POST /validation-jobs`, `.../complete` |
| `EmulationJob` (requires `VALIDATED`) | `POST /emulation-jobs`, `.../complete` |
| `InferenceJob` (requires runtime `ACTIVE`) | `POST /models/{id}/inference-jobs`, `POST /inference-jobs/{id}/resolve` |
| `CertificationRecord` | written by `advance` for every governance event; `GET /models/{id}/governance-history` |
| `LifecycleTransition` (`fsm=MODEL`/`RUNTIME`) | `GET /models/{id}/lifecycle-history?fsm=` |

`TrainingJob.status` uses TS 28.105 `requestStatus` values (`NOT_STARTED`,
`IN_PROGRESS`, `FINISHED`, `SUSPENDED`, `CANCELLED`) plus this build's
`FAILED`; `CANCELLING` is never produced because cancellation is synchronous.
Governance events (`SUBMIT_FOR_APPROVAL`, `APPROVE`, `REJECT`, `CERTIFY`,
`PROMOTE`, `ROLLBACK`, `APPROVE_TRAINING`, `APPROVE_VALIDATION`) require
`decidedBy`; `rationale` is optional.

#### AIMgF NFO invocation

- `POST /models/{id}/runtime/deploy` calls NFO `CreateDescriptor` +
  `Instantiate`. A model runtime has no onboarded package, so `packageId` is
  omitted (NFO's descriptor `package_id` is nullable).
- `.../runtime/scale` and `.../runtime/terminate` call NFO scale and
  terminate on the resulting `nfDeploymentId`.
- `.../runtime/activate` is local: NFO's deployment is already `RUNNING`
  when `Instantiate` returns; `ACTIVATE` is AIMgF's decision to accept
  inference traffic.
- Training, validation and emulation each get a transient NFO runtime,
  created on request and torn down on completion, cancel or timeout (see
  [ROADMAP.md#runtime-realization](ROADMAP.md#runtime-realization)).

### MLMR

ML Model Repository (`mlmr/`). MLMR records what a model *is*: identity,
versions, artifacts, coordination groups. It records nothing about lifecycle
state or deployment and never fires a state transition.

| Owns | Does not own → owner |
|---|---|
| `MLModel` (identity, type, version, description, owner, target environments; TS 29.482 `domain`, `customDomain`, `vendors`; TS 28.105 writable attributes) | Lifecycle state; training / validation / emulation / inference requests → AIMgF |
| `ModelArtifact` (in-DB bytes; `size_bytes` computed from the uploaded bytes) | Deploy gate and targeting → MLLF; load/activate state → AIMgF + NFO |
| Versioning by `(model_type, version)` | |
| `MLModelRepository` (TS 28.105 container; deleting it un-contains its models and groups) | |
| `MLModelCoordinationGroup` (members, retrain propagation; ≥ 2 members) | |

Routes: `/models` (register, discover, get, update, deregister),
`/models/{id}/artifact` (upload, download by version), `/coordination-groups`,
and the TS 28.105 views `/ml-models/{id}`, `/ml-model-repositories`,
`/ml-model-coordination-groups/{id}`. The read-only TS 28.105 attributes
`mLTrainingType`, `aIMLInferenceReportRefList` and `usedByFunctionRefList`
are joined from AIMgF (`GET /aimgf/ml-models/{id}/nrm-refs`) over R1, not
stored in MLMR. Real object storage (S3) is out of scope.

### MLLF

ML Loading Function (`mllf/`). MLLF is the gate and targeting surface for a
model's deployment request. It has no lifecycle logic and no table of its
own.

| Owns | Does not own → owner |
|---|---|
| `POST /models/{id}/deploy`: refuses unless AIMgF's `ModelLifecycleState` is `CERTIFIED` or `PROMOTED` (`MODEL_NOT_CERTIFIED`, 409) | Training, validation, emulation, certification → AIMgF |
| `clearedNodeGroups` targeting, written onto AIMgF's row via `PATCH /aimgf/models/{id}/runtime/node-groups` | Repository, versioning, coordination groups → MLMR |
| | The load/unload/activate/deactivate state machine (`RuntimeLifecycleState`) → AIMgF + NFO |

### MDAF

Management Data Analytics Function (`mdaf/`). MDAF owns the *output* of
analysis, realizing TS 28.104 (`specs/5G_APIs/TS28104_MdaNrm.yaml`,
`TS28104_MdaReport.yaml`).

| Owns | Does not own → owner |
|---|---|
| Analytics, prediction and drift reports (`reportKind` `ANALYTICS` / `PREDICTION` / `DRIFT`) | Training, model repository → AIMgF / MLMR |
| Analytics subscriptions with `ThresholdInfo` edge-triggered `UP` / `DOWN` / `UP_AND_DOWN` crossings and hysteresis | Data storage → DME |
| TS 28.104 `MDAFunction`, `MDARequest`, `MDAReport`, request matching and delivery (`mdaf/app/mda.py`) | Use-case analytics production (traffic / energy / coverage) → RAN Analytics |
| Drift → retrain signal: a `DRIFT` report naming `mLModelRef` is forwarded to that model's AIMgF MLMF subscriptions | |

Routes: `POST/GET /reports`, `/subscriptions` (callback
`notificationDestination` in the body), `/mda-functions`, `/mda-requests`,
`/mda-reports` (+ `/mda-reports/{id}/file`). `publish_report` validates every
`input_sources` id against a real DME `DataJob` (`DME_ARTIFACT_NOT_FOUND`,
422), so MDAF sources data from DME only. `analytics_type` stays a free
string; an optional `mdaType` is validated against the TS 28.104 enum.

**RAN Analytics** (`ran-analytics/`) keeps only analytics-producer
registration (`POST/GET /producers`) and is an MDAF consumer. The
RAN-Analytics/MDAF line is a product-organization choice, not a TS 28.104
requirement (TS 28.104's `MDAType` already spans these use cases).

### DME

Data Management and Exposure (`dme/`). DME is the vendor-neutral data and
control broker between rApps and heterogeneous RAN / Digital Twin sources,
with two responsibilities:

1. **Data plane**: acquire, tag with provenance, store and expose the data
   the AI/ML lifecycle needs (training, testing, emulation, inference,
   closed-loop feedback), for rApps and MDAF alike.
2. **O1 action mediation**: turn an rApp's decision into an O1 action,
   forwarded to RAN NF OAM, which speaks the wire protocol.

| Owns | Does not own → owner |
|---|---|
| Type / producer registry: `DMEType`, `DMETypeSubscription`, `DataOffer`, production capabilities | NETCONF/RESTCONF dispatch → RAN NF OAM |
| Data jobs, data discovery, data lineage | O1 endpoint registry, ME/MF addressing, alarms, PM/CM/SWM jobs → RAN NF OAM |
| Source provenance and the Digital-Twin eligibility rule | Analytics output → MDAF |
| `DataRecord`: producer ingest `POST /data-jobs/{id}/records`, consumer fetch `GET /data-jobs/{id}/records` | Model lifecycle and orchestration → AIMgF |
| `DmeActionRecord` + `POST /actions` (O1 action mediation) | Models, artifacts → MLMR |

#### DME source provenance and eligibility

Every `DMEType` carries `source_domain` (`LIVE_RAN` | `DIGITAL_TWIN`) and a
`source_context` JSON dict (vendor / product / release / instance / node /
cell, whichever a producer populates). Every `DataJob` carries
`lifecycle_stage` (`TRAINING` | `TESTING` | `EMULATION` | `INFERENCE` |
`CLOSED_LOOP_FEEDBACK`). Creating a job with `DIGITAL_TWIN` data for
`INFERENCE` is refused (`DIGITAL_TWIN_INFERENCE_NOT_ELIGIBLE`, 422): a Digital
Twin feeds training and emulation, never inference. A type that declares no
domain skips the check.

#### DME data path and action path

- **Action path — rApp → DME → RAN NF OAM → O1.** `POST /dme/actions`
  records the decision (target `managedElementRef`, `className` /
  `managedFunctionRef`, attribute changes, source context) and forwards it
  to `POST /ran-nf-oam/config-jobs`. DME's record is the audit of what the
  AI/ML decision asked for; RAN NF OAM's `WriteConfigJob` is the record of
  what NETCONF did. An `actionId` already recorded is `IGNORED`
  (idempotency). A 4xx from RAN NF OAM (capability or schema refusal) is
  passed back unchanged and the action is recorded `REJECTED`. MDAF is never
  on this path.
- **Data path — MDAF and rApps → DME.** MDAF consumes DME's data plane like
  any rApp.

#### Multi-vendor principle

Every dataset, record and control operation DME brokers carries enough
source identity that a multi-vendor, multi-Digital-Twin deployment never
mixes data across producers. Data-model conformance is chosen per vendor
(own model, the O-RAN WG5 / 3GPP model, or combined), never hard-coded; the
registry that realizes this is in RAN NF OAM, see
[O1 vendor onboarding](#o1-vendor-onboarding). O-RAN WG4 (O-RU M-plane YANG)
is out of scope apart from the Software Management RPC engine RAN NF OAM
implements. A1, Near-RT RIC and xApps are not on the DME loop: inference runs
inside the rApp.

### Intent Service

Intent Service (`intent-service/`) is intent truth. It realizes TS 28.312
(`specs/5G_APIs/TS28312_IntentNrm.yaml` and the five `*Expectation.yaml`
family files).

| Owns | Does not own → owner |
|---|---|
| `Intent` with structured `IntentExpectation`s, `IntentReport`, `IntentHandlingFunction` (RMIH), `IntentUtilityFormula` | A1 policy create / enforce / retract → `a1-related/` |
| Intent resolution and assurance: feasibility, conflict, fulfilment and negotiation reports, report delivery | O1 enactment of CM intents → SA SMOS O1-CM handler (an RMIH) |
| `AutonomyDispatch` (rApp autonomy modes) | |

#### RMIH selection

Selection is consumer-side, following TS 28.312 NRM containment
(`IntentHandlingFunction` contains `Intent`):

- `POST /intents` requires `rmihId`: the caller addresses one RMIH it has
  discovered via `GET /intent-handling-functions`.
- The RMIH's declared capabilities and scope must cover the intent
  (`RMIH_CAPABILITY_MISMATCH`, 422).
- `Intent.rmih_id` is a foreign key with `ON DELETE CASCADE`: deregistering
  an RMIH ends every intent addressed to it.
- Dispatch is one best-effort notification to the named RMIH. RMIHs register
  with a `notificationDestination`.

#### Autonomy dispatch

An rApp instance carries `autonomyMode` (`AUTONOMOUS` / `ASSIST` /
`SHADOW`, default `SHADOW`) and `regionScope` (rApp Management). An
inference outcome is handed to `POST /intent-service/autonomy-dispatches`:

| Mode | Behaviour |
|---|---|
| `AUTONOMOUS` | An Intent is created at once. |
| `ASSIST` | `AWAITING_SCOPE` until the operator calls `/resolve` (with scope → Intent) or `/reject` (→ `REJECTED`; 409 unless `AWAITING_SCOPE`). |
| `SHADOW` | `SHADOWED`; never enforced. |

Every mode notifies the operator (best effort, via `smo_shared.webhook`).

**Generic O1-CM handler.** SA SMOS registers as RMIH `sa-smos` for
RAN_SUBNETWORK expectations whose targets are `<IOC>.<attribute>` CM
attributes (`sa-smos/app/o1cm.py`; `/sa-smos/o1-cm-handler/registration`,
`/intents`, `/enactments`). For each `IS_EQUAL_TO` target and each cell in
the `Cell` context, it issues a change through `POST /dme/actions` (one
action per expectation) and posts the IntentReport `FULFILLED` /
`NOT_FULFILLED` with DME action and config-job references in
`additionalFulfilmentInfo`. Supported CM targets:
`NRCellDU.administrativeState`, `CESManagementFunction.energySavingControl`,
`NRCellRelation.cellIndividualOffset`, `CommonBeamformingFunction.digitalTilt`,
`NRSectorCarrier.configuredMaxTxPower`, `NRFreqRelation.cellReselectionPriority`.
See call flow 09.

## O1 vendor onboarding

Onboarding a RAN vendor or a Digital Twin is data fed to RAN NF OAM, not new
code (`ran-nf-oam/app/vendors.py`, call flow 21). The registry is per vendor;
two generic checks read it on every O1 operation.

### The three axes

A vendor's O1 termination differs on three independent axes:

| Axis | Question | Realized by |
|---|---|---|
| 1. MnS transport | Which wire protocol? | `ManagedEntity.o1_protocol`; vendor modes `O1_NETCONF` / `O1_RESTCONF`. NETCONF is RFC 6241-shaped `edit-config` over HTTP (`netconf_client.py`); RESTCONF is RFC 8040 on the data resource (`restconf_client.py`). |
| 2. MnS services | Does the vendor implement this operation category at all? (presence) | `supportedServices` ⊆ `PROV`, `FM`, `PM`, `FILE`, `STREAM`, `SWM`, `SUBSCRIPTION`, `HEARTBEAT` |
| 3. IOC data model | Whose class / attribute names and value ranges? (shape) | `conformanceMode` `SPEC` / `OWN` / `COMBINED` + CM schema descriptors |

### Registry resources

All routes are under `/ran-nf-oam` through R1.

| Resource | Routes |
|---|---|
| Vendor capability | `PUT /vendor-capabilities/{vendor}` (body: `supportedServices` ≥ 1, `conformanceMode` default `SPEC`, `supportedVendorModes` default `["O1_NETCONF"]`, `schemaRef`, `specSchemaRef`), `GET /vendor-capabilities`, `GET`/`DELETE /vendor-capabilities/{vendor}` |
| CM schema descriptors | `POST /cm-schemas` (`schemaName`, `revision`, `type` `YANG`/`OPENAPI_NRM`/`DESCRIPTOR`, `location`, `descriptor`), `GET /cm-schemas`, `GET /cm-schemas/{name}?revision=` |
| Onboarding flow | `POST /vendor-onboarding` |
| Aggregate capabilities | `GET /capabilities` (union of vendor modes, the MnS service list, per-vendor summary) |
| Managed entities | `GET /managed-entities?vendor_name=`, `GET /managed-entities/{me}` (effective services, conformance mode, cell guards) |
| Cell guards | `PUT`/`DELETE /managed-entities/{me}/cells/{cell}/guards` (`cellClass` `EMERGENCY` / `COVERAGE_CRITICAL` / `NORMAL`, `sectorGroup`, `incidentZone`, `neighbourRefs`), `GET /cell-guards?cell_class=&sector_group=&incident_zone=&managed_element_ref=&cell_id=` |

A descriptor has the shape
`{"classes": {"<IOC>": {"<attribute>": {"type": ..., "enum"?: [...]}}}}`.
The 3GPP TS 28.541 NR NRM descriptor `3gpp-ts28541-nrnrm@19.6.0` (54 IOC
classes, generated from `specs/5G_APIs/TS28541_NrNrm.yaml`) is bundled in
`ran-nf-oam/app/cm_schemas/` and is the default `specSchemaRef`.

### Operator steps

1. **Generate the data-model descriptor (offline, once per data model).**
   Descriptors are derived mechanically from NRM OpenAPI definitions, never
   hand-transcribed:

   ```
   cd smo && python scripts/ingest_cm_schema.py <NRM OpenAPI file>... \
       --name <schemaName> --revision <revision> --out <descriptor>.json
   ```

   Every `<IOC>-Single` schema becomes a class; its `attributes` (following
   `allOf` and `$ref` across sibling files) become the attributes a CM write
   may set. A `SPEC`-only vendor needs no descriptor of its own.

2. **The vendor's O1 adaptor registers its first managed element.**

   ```
   POST /ran-nf-oam/o1-adaptor-endpoints
   {"managedElementRef", "adaptorUri", "protocolSupport", "o1Protocol",
    "entityType", "managedFunctionRef"?, "vendorName", "supportedServices"?}
   → 201 {"endpointId", "managedElementRef", "healthStatus": "DISCOVERED"}
   ```

   Before a capability exists for the vendor, nothing is gated.

3. **Onboard the vendor: discover → load schemas → declare capability, in
   one call (admin).**

   ```
   POST /ran-nf-oam/vendor-onboarding
   {"vendorName",
    "discoverFrom": "<a managedElementRef registered for this vendor>",
    "conformanceMode": "SPEC" | "OWN" | "COMBINED",
    "schemas": [{"schemaName", "revision", "type", "location", "descriptor"}],
    "supportedServices"?, "supportedVendorModes"?, "schemaRef"?, "specSchemaRef"?}
   → 201 {"vendorName", "discovered", "schemasLoaded", "capability"}
   ```

   - Discovery reads `GET /capabilities` at the registered `adaptorUri`'s
     origin, through `smo_shared.webhook`. It never fetches a URL from the
     request. The adaptor answers `{vendorName, supportedServices,
     supportedVendorModes}`.
   - Values in the body win over discovered ones. `supportedServices` must
     come from one or the other; vendor modes default to `["O1_NETCONF"]`.
   - `OWN` / `COMBINED` need `schemaRef`; with exactly one entry in
     `schemas` it defaults to that schema.
   - Errors: discovery ME of another vendor or adaptor declaring another
     vendor → 422 `SCHEMA_VALIDATION_FAILED`; adaptor unreachable or ME
     without an adaptor → 503 `ENDPOINT_UNREACHABLE`; a different descriptor
     at an existing name and revision → 409 `CM_SCHEMA_CONFLICT`; an unknown
     `schemaRef` → 404 `CM_SCHEMA_NOT_FOUND`; an already-registered endpoint
     of the vendor using an undeclared mode → 409 `PROTOCOL_NOT_SUPPORTED`.

   The same result is reachable step by step with `POST /cm-schemas` and
   `PUT /vendor-capabilities/{vendor}`.

4. **Register the vendor's further managed elements** with the same
   `POST /ran-nf-oam/o1-adaptor-endpoints`. `o1Protocol` must map to a
   declared vendor mode (`NETCONF` → `O1_NETCONF`, `RESTCONF` →
   `O1_RESTCONF`; else 409 `PROTOCOL_NOT_SUPPORTED`). An endpoint's
   `supportedServices` may narrow the vendor's (for example an O-RU exposing
   only `FM` and `HEARTBEAT`), never widen them (422
   `SCHEMA_VALIDATION_FAILED`).

5. **Set cell guards** for cells that rApps must protect:
   `PUT /ran-nf-oam/managed-entities/{me}/cells/{cell}/guards`.

For a test vendor, `mock-o1-adaptor` serves a configurable `GET /capabilities`
(`MOCK_O1_VENDOR_NAME`, `MOCK_O1_SUPPORTED_SERVICES`, `MOCK_O1_VENDOR_MODES`).

### Checks at request time

| Check | Where | Refusal |
|---|---|---|
| Axis-2 presence guard (`require_service`) | `PROV`: `POST /config-jobs`, `GET /managed-entities/{me}/config`; `FM`: `POST /alarms/ingest`, `POST /fm-subscriptions`; `PM`: `POST /pm-subscriptions`, `POST /pm-reports`; `SWM`: `POST /software-management-jobs` | 409 `O1_SERVICE_NOT_SUPPORTED` |
| Axis-3 schema check (`schema_problems`) | every change of `POST /config-jobs`, before a `WriteConfigJob` is created | 422 `SCHEMA_VALIDATION_FAILED`, naming every offending attribute |

- Effective services are the endpoint's own declaration if set, else the
  vendor's.
- The class of a change comes from `className`, else from the
  `managedFunctionRef` prefix (`NRCellDU=1` → `NRCellDU`). A change naming no
  class must use attributes some class defines. Values are checked against
  the descriptor's `enum` where present (for example
  `NRCellDU.administrativeState` ∈ {`LOCKED`, `UNLOCKED`}).
- `conformanceMode` selects the descriptor(s): `SPEC` = spec descriptor only;
  `OWN` = vendor descriptor only; `COMBINED` = spec descriptor plus the
  vendor descriptor's classes and attributes as named augments.
- A managed element whose vendor has no registered capability skips both
  checks (permissive default for single-vendor deployments).
- A refused write never reaches the adaptor. DME passes the 4xx back to the
  rApp and records the action `REJECTED`.

### Limits

- **Transport.** NETCONF (RFC 6241-shaped `edit-config`) and RESTCONF
  (RFC 8040) are dispatched, both over plain HTTP without TLS or
  authentication. Any other `o1_protocol` is rejected at dispatch with
  `PROTOCOL_NOT_SUPPORTED`. A new transport needs one client module, added
  to `_o1_client` in `ran-nf-oam/app/main.py`.
- **YANG.** The ingestion script reads NRM OpenAPI only; a YANG bundle needs
  a YANG front end (`pyang`) emitting the same descriptor shape.
- **Semantics.** A descriptor documents shape, not runtime behaviour; a
  vendor that silently ignores an accepted attribute is found only by
  integration testing against that vendor.

## Reference rApps

Four reference rApps under `samples/` exercise the platform end to end. Each
is a standalone CSAR (`manifest.yaml`, `capabilities.yaml`, ASD; four
execution modes, three autonomy modes, runtime profiles) and follows the same
pattern:

- **R1 only.** The rApp reaches the platform only through R1 Termination:
  the AI Runtime SDK (`smo_sdk.AiRuntimeSdk` over `R1Client`) plus plain R1
  reads of its rApp Management instance, RAN NF OAM alarms and peer rApps'
  published states. No A1, Near-RT RIC, xApps or E2.
- **O1 PM in.** PM reaches RAN NF OAM (`/pm-reports`), is registered as a DME
  type and read with `sdk.data.get_dataset`; a Digital Twin `*_SIM` producer
  feeds emulation. Guards come from `sdk.data.query_cell_guards` and alarms.
- **AI lifecycle.** Train, validate, emulate, certify and deploy through
  AIMgF / MLMR / MLLF / NFO; inference via
  `POST /aimgf/models/{id}/inference-jobs`.
- **O1 CM out.** A decision goes through `AutonomyDispatch` → Intent → SA SMOS
  O1-CM handler → DME `/actions` → RAN NF OAM `/config-jobs` → O1 adaptor,
  and is verified by read-back (`sdk.data.read_config` → `GET /ran-nf-oam/managed-entities/{me}/config`).
  KPI-driven reverts and rollbacks go straight to DME `/actions` with the
  execution's correlation id.
- **Coordination.** The rApps read each other's published states over R1 so
  they never act on the same cell or relation at once.

| rApp | Sample | O1 actuator(s) | Call flow |
|---|---|---|---|
| EnergySaving | `samples/energy-saving-rapp/` | `NRCellDU.administrativeState` or `CESManagementFunction.energySavingControl` (per instance) | [22](call-flows/22-energy-saving-closed-loop.md) |
| Mobility Optimization | `samples/mobility-optimization-rapp/` | `NRCellRelation.cellIndividualOffset` within `DMROFunction` bounds | [23](call-flows/23-mobility-optimization-closed-loop.md) |
| Coverage Optimization | `samples/coverage-optimization-rapp/` | `CommonBeamformingFunction.digitalTilt`, `NRSectorCarrier.configuredMaxTxPower` | [24](call-flows/24-coverage-optimization-closed-loop.md) |
| Traffic Steering | `samples/traffic-steering-rapp/` | `NRFreqRelation.cellReselectionPriority` (idle), `NRCellRelation.cellIndividualOffset` (connected) | [25](call-flows/25-traffic-steering-closed-loop.md) |

Design decisions, work items and evidence for each are in
[ROADMAP.md](ROADMAP.md) (waves 10.1–10.4).

## Related documents

| Document | Content |
|---|---|
| [ROADMAP.md](ROADMAP.md) | Waves 4–10.4, frozen decisions, standards compliance matrices, runtime realization, backlog |
| [HISTORY.md](../HISTORY.md) | How the platform got here: Wave 0–3 decomposition, audits, exit reviews |
| [call-flows/](call-flows/) | Sequence diagrams 01–27 (02 and 17: AI/ML lifecycle; 09: intents; 12: DME eligibility; 14: correlation id; 21: vendor onboarding; 22–25: reference rApps; 26: model governance and end of life; 27: TS 28.105 provisioning resources) |
| [openapi/](openapi/) | Generated OpenAPI specs per service |
| `../HISTORY.md §7` | Formal-spec audit per module |
| `../DEMO_RUNBOOK.md` | Runnable demo, including §24–§27 for the reference rApps |
