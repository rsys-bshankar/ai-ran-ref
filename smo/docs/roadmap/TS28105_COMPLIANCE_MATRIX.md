# TS 28.105 compliance matrix — AIMgF / MLMR

**Wave 4** (`docs/roadmap/WAVES_4_TO_10_WORK_ITEMS.md`, W4-01..W4-04, decision D-9). Generated from
`specs/5G_APIs/TS28105_AiMlNrm.yaml`, one row for each attribute or contained child of each of the
spec's 20 IOCs (126 rows). The generator also checks that every attribute marked Compliant
appears by name in the implementing code.

## Summary

| | Count |
|---|---|
| IOCs in TS 28.105 | 20, all implemented as REST resources |
| Attributes / children | 126 |
| Compliant | 125 |
| Deviation | 1: `MLTrainingFunction.ThresholdMonitors` (a TS 28.623 IOC; see below) |

**Addressing.** This is the one deviation that applies to every row. DN-typed attributes (`Dn`,
`DnRo`, `DnListRo`) carry this build's resource ids (UUIDs), and resources are flat REST
collections, not a DN containment tree. Each resource is returned as `{"id", "attributes"}`,
mirroring the spec's own `-Single` shape. Spec containment (for example, MLTrainingRequest under
MLTrainingFunction) is expressed as a `…Ref` attribute on the child.

**Not a parallel model.** An MLTrainingRequest *is* a `TrainingJob`, and an MLTestingRequest *is*
a `ValidationJob`. Both start through the same core (`_start_training` / `_start_validation` in
`aimgf/app/main.py`) as the original job routes, so the following still apply to NRM-created
runs:
- the model-lifecycle gate
- the operator approvals of OPEN_ITEMS.md §6.1
- the NFO execution runtimes of §6.2

Every training run, however started, gets an MLTrainingProcess. Every completion writes the
matching report: MLTrainingReport, MLTestingReport, or AIMLInferenceReport (for emulation and
inference).

**Behaviour, not just storage.**
- `cancelRequest` / `suspendRequest` / `cancelProcess` / `suspendProcess` act on the run.
- `activationStatus` gates inference.
- An MLModelLoadingRequest brings the model's serving runtime up through the real RuntimeLifecycle (NFO).
- An MLUpdateRequest runs FINE_TUNING training per model and writes the MLUpdateReport once every run is terminal.
- FL/RL datatypes (`FLRequirement`, `RLRequirement`, `FLParticipationInfo`, `SupportedLearningTechnology`, `FLReportPerClient`) are validated against the spec enums and stored. This build has no distributed-training engine that would consume them, the same "no real southbound compute" boundary as the rest of the build.

**The deviation.** `MLTrainingFunction.ThresholdMonitors` is a containment of TS 28.623's
generic `ThresholdMonitor` IOC, not a TS 28.105 one. Model-performance threshold monitoring is
AIMgF MLMF (`/aimgf/mlmf/subscriptions`, `guardKpiFloor`). SPEC_AUDIT.md's TS 28.105 item 7
records that as the functional equivalent. A generic TS 28.623 ThresholdMonitor belongs to a
TS 28.623 pass, not this one.

## W4-02 — MLMR repository-ownership review

| Concern | Owner | Finding |
|---|---|---|
| Model identity, version, artifacts | MLMR | `MLModel` (`mLModelId` = `model_id`, `mLModelVersion` = `version`), `ModelArtifact` |
| TS 28.105 writable MLModel attributes | MLMR | New columns on `aiml_model`, accepted on register and update |
| `mLTrainingType`, `aIMLInferenceReportRefList`, `usedByFunctionRefList` (read-only) | AIMgF | Not duplicated in MLMR. `GET /mlmr/ml-models/{id}` joins them from `GET /aimgf/ml-models/{id}/nrm-refs` through R1, degrading to empty values if AIMgF is unreachable |
| Repository container | MLMR | New `MLModelRepository`. Deleting it un-contains its models and groups (`ON DELETE SET NULL`); MLMR model truth outlives a container |
| Coordination groups | MLMR | `memberMLModelRefList` = `member_model_ids` (minItems 2, already enforced) |
| Lifecycle state | AIMgF | Unchanged. MLMR stores no lifecycle (`SERVICE_OWNERSHIP_MATRIX.md`) |

No duplicated fields between MLMR and AIMgF.

## W4-03 — "InferenceRuntime" mapping

`SMO_Waves_4to9.docx` M1 names an `InferenceRuntime` entity. It is realised by two existing
pieces, so no new entity is needed:

- **AIMLInferenceFunction** (TS 28.105): the logical inference host. It holds `activationStatus`, `managedActivationScope`, and the loaded models.
- **RuntimeLifecycle** (AIMgF `model_lifecycle.runtime_lifecycle_state` + `nf_deployment_id`): the model's real NFO-backed serving deployment (MLIF). MLModelLoadingProcess drives it NOT_DEPLOYED → DEPLOYED → ACTIVE.

An inference names both: the model, which must be runtime ACTIVE, and optionally the function,
which must be ACTIVATED with the model loaded.

## Matrix

### MLTrainingFunction

Owner: **AIMgF** · Resource: `/aimgf/ml-training-functions`

| Attribute / child | Status | Note |
|---|---|---|
| `supportedLearningTechnology` | Compliant |  |
| `fLParticipationInfo` | Compliant |  |
| `mLKnowledge` | Compliant |  |
| `mLTrainingType` | Compliant |  |
| `mLModelRepositoryRef` | Compliant (id-addressed) |  |
| `MLTrainingRequest` | Compliant (containment as reference) | Containment: `mLTrainingFunctionRef` on the request; list with `?ml_training_function_id=` |
| `MLTrainingProcess` | Compliant (containment as reference) | Containment: reached through the request (`trainingRequestRef`) |
| `MLTrainingReport` | Compliant (containment as reference) | Containment: `mLTrainingFunctionRef` on the report |
| `ThresholdMonitors` | Deviation | Deviation (not modelled): TS 28.623 ThresholdMonitor containment. Threshold monitoring of a model is AIMgF MLMF (`/aimgf/mlmf/subscriptions`, guardKpiFloor), recorded as equivalent in SPEC_AUDIT.md item 7 |
| `MLTestingRequest` | Compliant (containment as reference) | Containment: testing requests are contained by MLTestingFunction here (`mLTestingFunctionRef`). The spec allows either container |
| `MLTestingReport` | Compliant (containment as reference) | Containment: as MLTestingRequest |

### MLTrainingRequest

Owner: **AIMgF** · Resource: `/aimgf/ml-training-requests (= TrainingJob)`

| Attribute / child | Status | Note |
|---|---|---|
| `aIMLInferenceName` | Compliant |  |
| `fLRequirement` | Compliant |  |
| `candidateTrainingDataSource` | Compliant |  |
| `trainingDataQualityScore` | Compliant |  |
| `trainingRequestSource` | Compliant | Required on create (= producer) |
| `requestStatus` | Compliant | = TrainingJob.status (spec enum; FAILED is this build's addition) |
| `performanceRequirements` | Compliant |  |
| `rLRequirement` | Compliant |  |
| `cancelRequest` | Compliant |  |
| `suspendRequest` | Compliant |  |
| `trainingDataStatisticalProperties` | Compliant |  |
| `distributedTrainingExpectation` | Compliant |  |
| `mLKnowledgeName` | Compliant |  |
| `mLTrainingType` | Compliant |  |
| `expectedInferenceScope` | Compliant |  |
| `clusteringInfo` | Compliant |  |
| `mLModelRef` | Compliant (id-addressed) |  |
| `mLModelCoordinationGroupRef` | Compliant (id-addressed) |  |

### MLTrainingProcess

Owner: **AIMgF** · Resource: `/aimgf/ml-training-processes`

| Attribute / child | Status | Note |
|---|---|---|
| `priority` | Compliant |  |
| `terminationConditions` | Compliant |  |
| `progressStatus` | Compliant | ProcessMonitor. Runtime write-back via POST …/progress |
| `cancelProcess` | Compliant |  |
| `suspendProcess` | Compliant |  |
| `trainingRequestRef` | Compliant (id-addressed) |  |
| `participatingFLClientRefList` | Compliant (id-addressed) |  |
| `trainingReportRef` | Compliant (id-addressed) |  |
| `mLModelRef` | Compliant (id-addressed) |  |
| `mLModelCoordinationGroupRef` | Compliant (id-addressed) |  |

### MLTrainingReport

Owner: **AIMgF** · Resource: `/aimgf/ml-training-reports`

| Attribute / child | Status | Note |
|---|---|---|
| `usedConsumerTrainingData` | Compliant |  |
| `modelConfidenceIndication` | Compliant |  |
| `modelPerformanceTraining` | Compliant |  |
| `modelPerformanceValidation` | Compliant |  |
| `dataRatioTrainingAndValidation` | Compliant |  |
| `areNewTrainingDataUsed` | Compliant |  |
| `fLReportPerClient` | Compliant |  |
| `trainingProcessRef` | Compliant (id-addressed) |  |
| `lastTrainingRef` | Compliant (id-addressed) |  |
| `mLModelGeneratedRef` | Compliant (id-addressed) |  |
| `mLModelCoordinationGroupGeneratedRef` | Compliant (id-addressed) |  |

### MLTestingFunction

Owner: **AIMgF** · Resource: `/aimgf/ml-testing-functions`

| Attribute / child | Status | Note |
|---|---|---|
| `mLModelRef` | Compliant (id-addressed) |  |
| `MLTestingRequest` | Compliant (containment as reference) | Containment: `mLTestingFunctionRef` |
| `MLTestingReport` | Compliant (containment as reference) | Containment: `mLTestingFunctionRef` |

### MLTestingRequest

Owner: **AIMgF** · Resource: `/aimgf/ml-testing-requests (= ValidationJob)`

| Attribute / child | Status | Note |
|---|---|---|
| `requestStatus` | Compliant | Mapped from ValidationJob.status (COMPLETED/FAILED → FINISHED; outcome in MLTestingReport.mLTestingResult) |
| `cancelRequest` | Compliant |  |
| `suspendRequest` | Compliant |  |
| `mLModelRef` | Compliant (id-addressed) |  |
| `mLModelCoordinationGroupRef` | Compliant (id-addressed) |  |

### MLTestingReport

Owner: **AIMgF** · Resource: `/aimgf/ml-testing-reports`

| Attribute / child | Status | Note |
|---|---|---|
| `modelPerformanceTesting` | Compliant |  |
| `mLTestingResult` | Compliant |  |
| `testingRequestRef` | Compliant (id-addressed) |  |

### MLModelLoadingRequest

Owner: **AIMgF** · Resource: `/aimgf/ml-model-loading-requests`

| Attribute / child | Status | Note |
|---|---|---|
| `requestStatus` | Compliant |  |
| `cancelRequest` | Compliant |  |
| `suspendRequest` | Compliant |  |
| `mLModelToLoadRef` | Compliant (id-addressed) |  |

### MLModelLoadingPolicy

Owner: **AIMgF** · Resource: `/aimgf/ml-model-loading-policies`

| Attribute / child | Status | Note |
|---|---|---|
| `aIMLInferenceName` | Compliant |  |
| `policyForLoading` | Compliant |  |
| `mLModelRef` | Compliant (id-addressed) |  |

### MLModelLoadingProcess

Owner: **AIMgF** · Resource: `/aimgf/ml-model-loading-processes`

| Attribute / child | Status | Note |
|---|---|---|
| `progressStatus` | Compliant |  |
| `cancelProcess` | Compliant |  |
| `suspendProcess` | Compliant |  |
| `mLModelLoadingRequestRef` | Compliant (id-addressed) |  |
| `mLModelLoadingPolicyRef` | Compliant (id-addressed) |  |
| `loadedMLModelRef` | Compliant (id-addressed) |  |

### MLModel

Owner: **MLMR** · Resource: `/mlmr/ml-models/{id} (+ /mlmr/models)`

| Attribute / child | Status | Note |
|---|---|---|
| `mLModelId` | Compliant | = MLMR model_id |
| `aIMLInferenceName` | Compliant |  |
| `mLModelVersion` | Compliant | = MLMR version |
| `expectedRunTimeContext` | Compliant |  |
| `trainingContext` | Compliant |  |
| `runTimeContext` | Compliant |  |
| `supportedPerformanceIndicators` | Compliant |  |
| `mLCapabilitiesInfoList` | Compliant |  |
| `mLTrainingType` | Compliant | Read-only. Joined from AIMgF (type of the latest successful training) |
| `inferenceScope` | Compliant |  |
| `retrainingEventsMonitorRef` | Compliant (id-addressed) |  |
| `sourceTrainedMLModelRef` | Compliant (id-addressed) |  |
| `aIMLInferenceReportRefList` | Compliant (id-addressed) | Read-only. Joined from AIMgF |
| `usedByFunctionRefList` | Compliant (id-addressed) | Read-only. Joined from AIMgF (inference functions it is loaded on) |

### MLModelRepository

Owner: **MLMR** · Resource: `/mlmr/ml-model-repositories`

| Attribute / child | Status | Note |
|---|---|---|
| `MLModel` | Compliant (containment as reference) | Containment: `mLModelRepositoryRef` on MLModel; listed in the repository view |
| `MLModelCoordinationGroup` | Compliant (containment as reference) | Containment: `mLModelRepositoryRef` on the group; listed in the repository view |

### MLModelCoordinationGroup

Owner: **MLMR** · Resource: `/mlmr/ml-model-coordination-groups/{id} (+ /mlmr/coordination-groups)`

| Attribute / child | Status | Note |
|---|---|---|
| `memberMLModelRefList` | Compliant | = member_model_ids, minItems 2 enforced |

### MLUpdateFunction

Owner: **AIMgF** · Resource: `/aimgf/ml-update-functions`

| Attribute / child | Status | Note |
|---|---|---|
| `availMLCapabilityReport` | Compliant |  |
| `mLModelRef` | Compliant (id-addressed) |  |
| `MLUpdateRequest` | Compliant (containment as reference) | Containment: `mLUpdateFunctionRef` |
| `MLUpdateProcess` | Compliant (containment as reference) | Containment: via the request |
| `MLUpdateReport` | Compliant (containment as reference) | Containment: via the process |

### MLUpdateRequest

Owner: **AIMgF** · Resource: `/aimgf/ml-update-requests`

| Attribute / child | Status | Note |
|---|---|---|
| `performanceGainThreshold` | Compliant |  |
| `newCapabilityVersionId` | Compliant |  |
| `updateTimeDeadline` | Compliant |  |
| `requestStatus` | Compliant |  |
| `mLUpdateReportingPeriod` | Compliant |  |
| `cancelRequest` | Compliant |  |
| `suspendRequest` | Compliant |  |
| `mLUpdateProcessRef` | Compliant (id-addressed) |  |
| `mLModelRefList` | Compliant (id-addressed) |  |

### MLUpdateProcess

Owner: **AIMgF** · Resource: `/aimgf/ml-update-processes`

| Attribute / child | Status | Note |
|---|---|---|
| `progressStatus` | Compliant |  |
| `cancelProcess` | Compliant |  |
| `suspendProcess` | Compliant |  |
| `mLModelRefList` | Compliant (id-addressed) |  |
| `mLUpdateRequestRefList` | Compliant (id-addressed) |  |
| `mLUpdateReportRef` | Compliant (id-addressed) |  |

### MLUpdateReport

Owner: **AIMgF** · Resource: `/aimgf/ml-update-reports`

| Attribute / child | Status | Note |
|---|---|---|
| `updatedMLCapability` | Compliant |  |
| `mLModelRefList` | Compliant (id-addressed) |  |
| `mLUpdateProcessRef` | Compliant (id-addressed) |  |

### AIMLInferenceFunction

Owner: **AIMgF** · Resource: `/aimgf/aiml-inference-functions`

| Attribute / child | Status | Note |
|---|---|---|
| `activationStatus` | Compliant | Enforced: DEACTIVATED refuses inference on the function |
| `managedActivationScope` | Compliant |  |
| `usedByFunctionRefList` | Compliant (id-addressed) | Read-only. Derived from the `consumer_ref` of inference jobs |
| `mLModelRefList` | Compliant (id-addressed) | Read-only. Set by MLModelLoadingProcess |
| `AIMLInferenceReport` | Compliant (containment as reference) | Containment: `aIMLInferenceFunctionRef` |
| `MLModelLoadingRequest` | Compliant (containment as reference) | Containment: `aIMLInferenceFunctionRef` |
| `MLModelLoadingProcess` | Compliant (containment as reference) | Containment: `aIMLInferenceFunctionRef` |
| `MLModelLoadingPolicy` | Compliant (containment as reference) | Containment: `aIMLInferenceFunctionRef` |

### AIMLInferenceReport

Owner: **AIMgF** · Resource: `/aimgf/aiml-inference-reports`

| Attribute / child | Status | Note |
|---|---|---|
| `inferenceOutputs` | Compliant |  |
| `potentialImpactInfo` | Compliant |  |
| `mLModelRefList` | Compliant (id-addressed) |  |

### AIMLInferenceEmulationFunction

Owner: **AIMgF** · Resource: `/aimgf/aiml-inference-emulation-functions`

| Attribute / child | Status | Note |
|---|---|---|
| `AIMLInferenceReport` | Compliant (containment as reference) | Containment: `aIMLInferenceEmulationFunctionRef` |
