# rApp packaging

How an rApp is packaged for this SMO, what Onboarding validates, and which
part of each package file the platform actually uses. The reference packages
are the five directories under [`../samples/`](../samples/):

| Short name | Directory | What it demonstrates |
|---|---|---|
| hello-world | [`samples/hello-world-rapp/`](../samples/hello-world-rapp/README.md) | The minimal package: the full rApp lifecycle (onboard, deploy, activate, retire) with no AI. Used by [`DEMO_RUNBOOK.md`](../DEMO_RUNBOOK.md) §0–§23. |
| ES | [`samples/energy-saving-rapp/`](../samples/energy-saving-rapp/README.md) | Energy Saving: cell sleep/wake from PRB forecasts |
| MO | [`samples/mobility-optimization-rapp/`](../samples/mobility-optimization-rapp/README.md) | Mobility Robustness Optimization: per-relation CIO |
| CO | [`samples/coverage-optimization-rapp/`](../samples/coverage-optimization-rapp/README.md) | Coverage Optimization: joint tilt / power |
| TS | [`samples/traffic-steering-rapp/`](../samples/traffic-steering-rapp/README.md) | Traffic Steering: idle priority and connected CIO |

Onboarding is [`../onboarding/README.md`](../onboarding/README.md); the code is
`onboarding/app/main.py` (`_validate_package`, `_parse_ai_capabilities`,
`_validate_runtime_profiles`, `_parse_sme_declarations`).

## 1. Package layout

An rApp package is a CSAR (a zip). `python3 samples/build_csar.py <name>`
zips `samples/<name>/` into `samples/<name>.csar` with fixed timestamps, so an
unchanged source rebuilds byte-identically (the integration suite fails if a
committed `.csar` is stale). Tests, caches and `__pycache__` are left out.

```
<name>.csar
├── TOSCA-Metadata/TOSCA.meta        required   entry point: Entry-Definitions: Definitions/asd.yaml
├── Definitions/asd.yaml             required   the Application Service Descriptor (ASD)
├── manifest.yaml                    optional   AI platform manifest (this build's extension)
├── capabilities.yaml                optional   SDK namespaces consumed / provided (this build's extension)
├── Artifacts/…                      optional   deployment items, e.g. Artifacts/Deployment/HELM/*.tgz
├── Files/Sme/{providers,serviceapis,invokers}/*.json   optional   CAPIF registrations
├── Files/Dme/{infoproducers,infoconsumers}/*.json      optional   DME type / job declarations
├── Files/Acm/definition/compositions.json              optional   ONAP ACM composition (not used)
└── app/, demo.py, …                 free       the rApp's own source (the four AI rApps ship it here)
```

| Part | Read by | Effect |
|---|---|---|
| `TOSCA-Metadata/TOSCA.meta` | Onboarding | Missing file or no `Entry-Definitions:` line fails validation (`ONBOARDING` → `FAILED`). |
| `Definitions/asd.yaml` | Onboarding (line scan) | Must exist. Eight flat properties become the package identity (§2). Onboarding then creates an NFO `NFDeploymentDescriptor` that references it. |
| `manifest.yaml`, `capabilities.yaml` | Onboarding (YAML parse) | Stored as the package's `aiCapabilities` JSON and returned by `GET /onboarding/packages/{id}` and `…/onboarding-status` (§3, §4). |
| `Artifacts/…` | Onboarding | Every file under `Artifacts/` is recorded as a package artifact. |
| `Files/Sme/…` | Onboarding → rApp Management | Providers and service APIs are stored raw at onboarding and registered with SME per instance at `bootstrap-complete`. |
| `Files/Dme/…` | The rApp itself | Declarations the rApp posts to DME when it starts (hello-world only). Onboarding does not read them. |
| `Files/Acm/…` | Nothing | An ONAP ACM composition. A package without it onboards identically. |

Both extension files are **optional and additive**: a package with neither
onboards unchanged and its `aiCapabilities` is `null`.

## 2. `Definitions/asd.yaml`

Onboarding reads these `applicationServiceDescriptor` properties by scanning
for `key: value` lines, not by parsing the YAML. A missing property is simply
absent from the result; none is enforced as mandatory.

| Property | Description | Stored as | hello-world | ES / MO / CO / TS |
|---|---|---|---|---|
| `application_name` | rApp name | package `name` | `hello-world-rapp` | `EnergySaving_rApp`, `MobilityOptimization_rApp`, `CoverageOptimization_rApp`, `TrafficSteering_rApp` |
| `application_version` | rApp version | package `version` | `"1.0"` | `"1.0.0"` |
| `provider` | Vendor | package `vendor` | `ai-ran-ref` | `Radisys` |
| `descriptor_id` | ASD identity (UUID) | `descriptor_id` | set | set (distinct UUID each) |
| `descriptor_invariant_id` | Identity across versions | `descriptor_invariant_id` | set | set |
| `descriptor_version` | ASD version | `descriptor_version` | `"1.0"` | `"1.0.0"` |
| `schema_version` | ASD schema version | `schema_version` | `"2.0"` | `"2.0"` |
| `function_description` | Free text | not read | set | set |
| `artifacts:` (deployment items) | What to deploy: `type: tosca.artifacts.asd.deploymentItem`, `file`, `artifact_type`, `target_server`, `item_id` | not read as such; files under `Artifacts/` are recorded | one Helm chart (`helm_chart`, `chartmuseum`) | **none**; see the note below |

The AI rApps declare no deployment items because they are not deployed from a
Helm chart: their services run as `docker-compose.yml` services and are reached
through R1 Termination (`/energy-saving-rapp` and so on). The CSAR is what
Onboarding validates and what carries the manifest, not what starts the
process. Package uniqueness is the SHA-256 of the whole CSAR, not the ASD ids.

## 3. `manifest.yaml`

`rappManifest` is the root key. `executionModes`, `autonomyModes`,
`requiredServices` and `runtimeProfiles` are accepted either under
`rappManifest` or at the top level of the file (the layout the AI rApps use).

"Used by" says what the platform does with the value today. "Descriptive"
means Onboarding ignores it: it travels in the CSAR (and its hash) for people
and tools, and nothing branches on it.

| Parameter | Description | Used by | hello-world | ES | MO | CO | TS |
|---|---|---|---|---|---|---|---|
| `rappManifest.manifestVersion` | Version of the manifest format | Stored in `aiCapabilities.manifestVersion` | `"1.0"` | `"1.0"` | `"1.0"` | `"1.0"` | `"1.0"` |
| `rappManifest.aiRuntimeSdkVersion` | `sdk/` contract version the rApp was built against | Stored in `aiCapabilities.aiRuntimeSdkVersion` | `"1.0"` | `"1.0"` | `"1.0"` | `"1.0"` | `"1.0"` |
| `executionModes` | Which of `TRAINING`, `VALIDATION`, `EMULATION`, `INFERENCE` the package supports | Stored; every `runtimeProfiles` key must be one of them | **absent** (a) | all four | all four | all four | all four |
| `autonomyModes` | Which of `SHADOW`, `ASSIST`, `AUTONOMOUS` the rApp supports | Stored and exposed only. The mode in force is the rApp *instance's* `autonomyMode`; nothing checks it against this list. | **absent** (a) | all three | all three | all three | all three |
| `requiredServices` | Platform services the rApp needs | Stored and exposed only; no deploy-time check | **absent** (a) | DME, AIMgF, MLMR, MLLF, RAN-NF-OAM | same | same | same |
| `runtimeProfiles.<MODE>.cpu` | CPU cores for that execution runtime | **Consumed by AIMgF**: sizes the transient NFO runtime for a Training / Validation / Emulation request that names the package (`packageId`); an explicit `runtimeProfile` in the request wins | absent (a) | 8 / 4 / 4 / 2 | 4 / 2 / 2 / 1 | 4 / 2 / 2 / 1 | 4 / 2 / 2 / 1 |
| `runtimeProfiles.<MODE>.memory` | Memory (string, e.g. `16Gi`) | as `cpu` | absent (a) | 16Gi / 8Gi / 8Gi / 4Gi | 8Gi / 4Gi / 4Gi / 2Gi | same as MO | same as MO |
| `runtimeProfiles.<MODE>.gpu` | GPUs | as `cpu` | absent (a) | 0 in every mode | 0 | 0 | 0 |
| `name`, `version`, `vendor`, `ownerTeam`, `description`, `useCase`, `domain`, `deploymentModel` | Package description from the Wave 10 package proposal | Descriptive. Identity is taken from the ASD (§2), not from here. | absent (a) | set; `useCase: Energy Saving`, `deploymentModel: NonRT-RIC` | set | set | set |

The profile values in the `runtimeProfiles` row are listed in the order
TRAINING / VALIDATION / EMULATION / INFERENCE.

Validation of `runtimeProfiles` (`_validate_runtime_profiles`), each a
package failure (`FAILED`):

- it must be a mapping of mode → profile, and each profile a mapping;
- a mode must be one of the four execution modes;
- when `executionModes` is declared, every profile mode must appear in it;
- `cpu` and `gpu` must be non-negative numbers (booleans are refused);
- `memory` is kept as a string, unchecked.

Why a package leaves a parameter out:

- **(a) hello-world** has no AI part: no training, no model, no inference, no
  autonomy. Its manifest carries only the two version fields, so it
  onboards as a plain rApp. Without `executionModes` and `runtimeProfiles`
  AIMgF would create any runtime requested for it unsized.
- The four AI rApps declare all four execution modes and all three autonomy
  modes: each runs its decisions through `AutonomyDispatch`, which implements
  SHADOW (record only), ASSIST (operator confirms) and AUTONOMOUS (applies at
  once).
- ES declares larger TRAINING and VALIDATION profiles than the other three.
  The manifests do not record why.
- `gpu: 0` everywhere: the reference models are small (threshold / regression),
  see each rApp's `app/model/`.

## 4. `capabilities.yaml`

`capabilities.consumes` and `capabilities.provides` are lists of
`{namespace, description}`, where `namespace` names one of the six
[AI Runtime SDK](../sdk/README.md) clients. Onboarding stores both lists as
declared and does not check that `namespace` is one of the six or that the
rApp really calls it. Everything else in the file is descriptive.

| Namespace | Meaning | hello-world | ES | MO | CO | TS |
|---|---|---|---|---|---|---|
| `data` (consumes) | `sdk.data`: DME datasets, cell guards, O1 config read-back | its own `hello-world-metrics` type | PRB_UTILIZATION, PRB_UTILIZATION_SIM | HO_PERFORMANCE, HO_PERFORMANCE_SIM | COVERAGE_PERFORMANCE, COVERAGE_PERFORMANCE_SIM | LOAD_PERFORMANCE, LOAD_PERFORMANCE_SIM |
| `analytics` (consumes) | `sdk.analytics`: MDAF predictions and reports | not used (c) | MDAF PRB predictions (`get_prediction`) | not used (d) | not used (d) | not used (d) |
| `models` (consumes) | `sdk.models`: register the model, store its artifact in MLMR | not used (c) | EnergySavingPredictor | MobilityRobustnessPredictor | CoverageSensitivityModel | CongestionSteeringModel |
| `lifecycle` (consumes) | `sdk.lifecycle`: AIMgF jobs, MLLF deploy, inference | not used (c) | yes | yes | yes | yes |
| `intent` (consumes) | `sdk.intent`: `AutonomyDispatch` of the decision | not used (c) | LOCK recommendations | CIO changes | tilt / power change sets | steering and release steps |
| `platform` (consumes) | `sdk.platform`: SME registration, direct DME O1 actions | SME provider and service API registration | wake, rollback, operator override | KPI reverts, rollbacks, DMRO bounds | KPI reverts, rollbacks | KPI reverts, rollbacks |
| `data` (provides) | A dataset the rApp publishes for others | `hello-world-metrics` | `PRB_UTILIZATION_SIM` (Digital Twin sample producer) | `HO_PERFORMANCE_SIM` | `COVERAGE_PERFORMANCE_SIM` | `LOAD_PERFORMANCE_SIM` |
| other namespaces (provides) | | none | none (e) | none (e) | none (e) | none (e) |

Why a namespace is not declared:

- **(c) hello-world** is a lifecycle demo, not an AI rApp, so it declares
  only `data` and `platform`.
- **(d) `analytics` in MO, CO and TS.** These rApps derive their features
  from the DME datasets themselves and read no MDAF prediction or report.
  Only ES asks MDAF for a PRB forecast. The declaration matches each rApp's
  code (`sdk.analytics` is called only in `energy-saving-rapp/app/main.py`).
- **(e) `provides`.** The platform defines no SDK surface an rApp can serve
  to others, so the only thing an rApp provides is data, through its own
  Digital Twin producer.

Descriptive-only keys, present in the four AI rApps and absent from
hello-world. Onboarding ignores all of them:

| Key | Describes | ES | MO | CO | TS |
|---|---|---|---|---|---|
| `datasets` | DME types used | PRB_UTILIZATION, _SIM | HO_PERFORMANCE, _SIM | COVERAGE_PERFORMANCE, _SIM | LOAD_PERFORMANCE, _SIM |
| `supportedStages` | Lifecycle stages | four | four | four | four |
| `producedModel` | `{name, type}` of the model | EnergySavingPredictor, REGRESSION | MobilityRobustnessPredictor, CLASSIFICATION_REGRESSION | CoverageSensitivityModel, REGRESSION_OPTIMISATION | CongestionSteeringModel, REGRESSION |
| `inputs` | Counters / attributes read | absent (f) | `MM.Ho*` counters | `MR.*`, `CM.*` | `RRU.PrbTotDl`, `RRC.ConnMean`, `DRB.UEThpDl`, `HO.*`, `CM.*` |
| `outputs` | O1 attributes written | `administrativeState` | `cellIndividualOffset` | `digitalTilt`, `configuredMaxTxPower` | `cellReselectionPriority`, `cellIndividualOffset` |
| `supportedActions` | Actions the rApp can take | `LOCK_CELL`, `UNLOCK_CELL` | `RAISE_CIO`, `LOWER_CIO`, `REVERT_CIO` | `DOWNTILT`, `UPTILT`, `POWER_UP`, `POWER_DOWN`, `REVERT` | `STEER_*`, `RELEASE_*`, `REVERT` |
| `o1Targets` / `o1Attributes` | TS 28.541 classes and attributes it changes | `NRCellDU`, `CESManagementFunction` | `NRCellRelation`, `DMROFunction` | `CommonBeamformingFunction`, `NRSectorCarrier` | `NRFreqRelation`, `NRCellRelation` |
| `supportedVendorModes` | O1 transports | `O1_NETCONF`, `O1_RESTCONF` | same | same | same |
| `supportedAnalytics` | Analytics kinds used | `TRAFFIC_FORECAST` | absent (d) | absent (d) | absent (d) |

- **(f)** ES does not list `inputs`; its manifest names the dataset only
  (`datasets: PRB_UTILIZATION`). The other three enumerate the counters.
- `supportedVendorModes` names the O1 transports the rApp's writes may travel
  over: RAN NF OAM dispatches RFC 6241 `edit-config` (NETCONF) and RFC 8040
  RESTCONF, both over plain HTTP (see
  [`../ran-nf-oam/README.md`](../ran-nf-oam/README.md)).

## 5. Files under `Files/`

| Path | Shape | Used by | hello-world | AI rApps |
|---|---|---|---|---|
| `Files/Sme/providers/*.json` | CAPIF `APIProviderEnrolmentDetails` | rApp Management registers it with SME at `bootstrap-complete` | yes | none (g) |
| `Files/Sme/serviceapis/*.json` | CAPIF `ServiceAPIDescription` | as above | yes | none (g) |
| `Files/Sme/invokers/*.json` | CAPIF invoker onboarding body | not read by Onboarding; the demo runbook posts it as the rApp's invoker registration | yes | none (g) |
| `Files/Dme/infoproducers/*.json` | DME type registration (`POST /dme/production-capabilities`) | the rApp at start | yes | none (h) |
| `Files/Dme/infoconsumers/*.json` | DME data job | the rApp at start | yes | none (h) |
| `Files/Acm/definition/compositions.json` | ONAP ACM composition | nothing: the build never calls ONAP ACM | yes | none |

- **(g)** The four AI rApps ship no SME declarations, so rApp Management has
  nothing to register for them. hello-world exists to show the SME path.
- **(h)** The AI rApps register their DME types from code (for example
  `energy-saving-rapp/app/producer.py` calls `sdk.data.register_type`) instead
  of shipping JSON declarations.

## 6. What fails onboarding

All of these end in `ApplicationPackage.state = FAILED` rather than an HTTP
error from the create call:

| Cause | Source |
|---|---|
| Location does not end in `.csar` | `NamingValidator` check |
| Not a zip, or `TOSCA-Metadata/TOSCA.meta` or the entry definitions file is missing, or no `Entry-Definitions:` line | `_validate_package` |
| Invalid YAML in `manifest.yaml` or `capabilities.yaml` | `_parse_ai_capabilities` |
| Invalid `runtimeProfiles` (§3) | `_validate_runtime_profiles` |
| Malformed JSON under `Files/Sme/` | `_parse_sme_declarations` (`JSONDecodeError`) |
| Location unreachable (HTTP error fetching the CSAR) | `_validate_package` |
| NFO refuses the descriptor | `_create_nf_deployment_descriptor` |

The package signature check is an internal-consistency check, not
verification against a trust anchor (see [`../../SECURITY.md`](../../SECURITY.md)).

## 7. Authoring checklist

1. Copy the closest sample. For an AI rApp start from `energy-saving-rapp/`; for
   a non-AI service from `hello-world-rapp/`.
2. Give the ASD a new `descriptor_id` / `descriptor_invariant_id`, and set
   `application_name`, `application_version`, `provider`.
3. In `manifest.yaml` list only the modes you implement, and keep every
   `runtimeProfiles` key inside `executionModes`.
4. In `capabilities.yaml` declare exactly the SDK namespaces your code calls.
5. Rebuild with `python3 samples/build_csar.py <name>` and run
   `PYTHONPATH=shared python -m pytest tests_integration/ -q`.
