# Call Flow: AI/ML Model — Register → Train → Certify → Deploy → Infer → Monitor

Stitches together the full lifecycle AI/ML Workflow LLD sections 1-8 close: the certification
pipeline (v1.3, unchanged shape), the inference API (LLD section 3, new), and MLMF
monitoring (LLD section 2, new) feeding back into a retrain trigger.

Wave 1 of the AI Platform Service Decomposition split this flow's single
former `ai-ml-workflow` participant into three real services — MLMR
(model repository), AIMgF (lifecycle orchestration), MLLF (loading/
deployment) — talking to each other through R1 Termination like every
other cross-module call in this build, not a shared in-process ORM. See
`docs/architecture/SERVICE_OWNERSHIP_MATRIX.md` for the full ownership
split and `docs/ownership/{AIMGF,MLMR,MLLF}_OWNERSHIP.md` for each
service's own detail.

```mermaid
sequenceDiagram
    actor Producer as Model Producer rApp
    participant MLMR as MLMR
    participant AIMgF as AIMgF
    participant MLLF as MLLF
    participant DME as DME
    actor Consumer as Inference Consumer rApp

    Producer->>MLMR: RegisterModel(modelType, version)
    MLMR-->>Producer: modelId, state=REGISTERED

    Producer->>AIMgF: RequestTraining(modelId, requiredData, validationCriteria)
    AIMgF->>MLMR: GET /models/{id} (read current state)
    MLMR-->>AIMgF: state=REGISTERED
    AIMgF->>MLMR: PATCH /models/{id}/lifecycle (state=TRAINING, trainingJobId)
    Note over AIMgF: MLTF trains (Phase 1: elided)
    Producer->>AIMgF: advance(TRAINING_COMPLETE)
    AIMgF->>MLMR: PATCH /models/{id}/lifecycle (state=TESTED)
    Producer->>AIMgF: advance(VALIDATION_COMPLETE)
    AIMgF->>MLMR: PATCH /models/{id}/lifecycle (state=EMULATED)
    Note over AIMgF: AIMgF governance decision — the certification gate,<br/>still the framework's own contribution, no O-RAN equivalent
    Producer->>AIMgF: advance(CERTIFY)
    AIMgF->>MLMR: PATCH /models/{id}/lifecycle (state=CERTIFIED)

    Producer->>MLLF: RequestModelDeployment(modelId, nodeGroups)
    MLLF->>MLMR: GET /models/{id} (read current state)
    MLMR-->>MLLF: state=CERTIFIED
    Note over MLLF: check state in {CERTIFIED, LOADED, ACTIVE} — MODEL_NOT_CERTIFIED otherwise
    MLLF->>MLMR: PATCH /models/{id}/lifecycle (clearedNodeGroups) — MultiNode Q2 gap closure, LLD section 5
    Producer->>AIMgF: advance(LOAD), advance(ACTIVATE)
    AIMgF->>MLMR: PATCH /models/{id}/lifecycle (state=LOADED, then ACTIVE)

    Consumer->>AIMgF: RequestInference(modelId)
    AIMgF->>MLMR: GET /models/{id} (check state == ACTIVE) — INFERENCE_MODEL_NOT_ACTIVE otherwise
    AIMgF-->>Consumer: inferenceJobId, status=RUNNING
    Note over AIMgF,DME: input features pulled via DME against the model's<br/>registered inputDataType — never inline in the request (LLD section 3)
    AIMgF->>AIMgF: resolve(succeeded=true) -> status=COMPLETED
    Consumer->>DME: pull result via the model's outputDataType DmeTypeId
    DME-->>Consumer: prediction payload

    Producer->>AIMgF: SubscribePerformanceMonitoring(modelId, metricTypes, guardKpiFloor)
    Note over AIMgF: MLMF — new sub-function, distinct from RAN Analytics' MDAF (LLD section 2)
    Producer->>AIMgF: ReportPerformance(subscriptionId, metrics)
    AIMgF->>AIMgF: breachedFloor = metrics violate guardKpiFloor
    alt breached and model belongs to a MLModelCoordinationGroup
        AIMgF->>MLMR: GET /coordination-groups (find this model's group)
        MLMR-->>AIMgF: group + memberModelIds
        AIMgF->>AIMgF: should_trigger_group_retrain(retrainPropagation, ...)
        Note over AIMgF: ANY_MEMBER_TRIGGERS (checked-in default) fires on ONE breach —<br/>all group members retrain together (LLD section 4.3-4.4)
        AIMgF->>MLMR: PATCH /models/{memberId}/lifecycle (state=TRAINING) per ACTIVE member
    end
```

**Key decisions this flow depends on:**
- No lightweight update path — retraining always re-enters at `TRAINING`, never skips to `ACTIVE` (unchanged project design principle, confirmed by every LLD pass touching this module).
- The inference result is pulled via DME, never returned inline from the R1-facing inference API — symmetric with training never carrying the trained artifact inline either.
- `ANY_MEMBER_TRIGGERS` means a coordination group's retrain trigger is genuinely group-scoped — one member's breach retrains the shared model all members depend on, not just that member's own pipeline.
- MLMR's own row is the single source of truth for `state`/`trainingJobId`/`clearedNodeGroups` this wave — AIMgF and MLLF never keep their own copy, they read and write it through MLMR's `GET`/`PATCH /models/{id}/lifecycle` on every decision. Wave 2 deepens this into AIMgF's own full domain model.
