# Call Flow: AI/ML Model — Register → Train → Certify → Deploy → Infer → Monitor

Stitches together the full lifecycle AI/ML Workflow LLD sections 1-8 close: the certification
pipeline (v1.3, unchanged shape), the inference API (LLD section 3, new), and MLMF
monitoring (LLD section 2, new) feeding back into a retrain trigger.

```mermaid
sequenceDiagram
    actor Producer as Model Producer rApp
    participant AIML as AI/ML Workflow SMOS
    participant DME as DME
    participant Consumer as Inference Consumer rApp

    Producer->>AIML: RegisterModel(modelType, version)
    AIML-->>Producer: modelId, state=REGISTERED

    Producer->>AIML: RequestTraining(modelId, requiredData, validationCriteria)
    AIML->>AIML: state: REGISTERED -> TRAINING
    Note over AIML: MLTF trains (Phase 1: elided)
    Producer->>AIML: advance(TRAINING_COMPLETE)
    AIML->>AIML: state: TRAINING -> TESTED
    Producer->>AIML: advance(VALIDATION_COMPLETE)
    AIML->>AIML: state: TESTED -> EMULATED
    Note over AIML: AIMgF governance decision — the certification gate,<br/>still the framework's own contribution, no O-RAN equivalent
    AIML->>AIML: advance(CERTIFY)
    AIML->>AIML: state: EMULATED -> CERTIFIED

    Producer->>AIML: RequestModelDeployment(modelId, nodeGroups)
    AIML->>AIML: check state in {CERTIFIED, LOADED, ACTIVE} — MODEL_NOT_CERTIFIED otherwise
    AIML->>AIML: stamp clearedNodeGroups (MultiNode Q2 gap closure, LLD section 5)
    AIML->>AIML: advance(LOAD), advance(ACTIVATE)
    AIML->>AIML: state: CERTIFIED -> LOADED -> ACTIVE

    Consumer->>AIML: RequestInference(modelId)
    AIML->>AIML: check state == ACTIVE — INFERENCE_MODEL_NOT_ACTIVE otherwise
    AIML-->>Consumer: inferenceJobId, status=RUNNING
    Note over AIML,DME: input features pulled via DME against the model's<br/>registered inputDataType — never inline in the request (LLD section 3)
    AIML->>AIML: resolve(succeeded=true) -> status=COMPLETED
    Consumer->>DME: pull result via the model's outputDataType DmeTypeId
    DME-->>Consumer: prediction payload

    Producer->>AIML: SubscribePerformanceMonitoring(modelId, metricTypes, guardKpiFloor)
    Note over AIML: MLMF — new sub-function, distinct from RAN Analytics' MDAF (LLD section 2)
    Producer->>AIML: ReportPerformance(subscriptionId, metrics)
    AIML->>AIML: breachedFloor = metrics violate guardKpiFloor
    alt breached and model belongs to a MLModelCoordinationGroup
        AIML->>AIML: should_trigger_group_retrain(retrainPropagation, ...)
        Note over AIML: ANY_MEMBER_TRIGGERS (checked-in default) fires on ONE breach —<br/>all group members retrain together (LLD section 4.3-4.4)
    end
```

**Key decisions this flow depends on:**
- No lightweight update path — retraining always re-enters at `TRAINING`, never skips to `ACTIVE` (unchanged project design principle, confirmed by every LLD pass touching this module).
- The inference result is pulled via DME, never returned inline from the R1-facing inference API — symmetric with training never carrying the trained artifact inline either.
- `ANY_MEMBER_TRIGGERS` means a coordination group's retrain trigger is genuinely group-scoped — one member's breach retrains the shared model all members depend on, not just that member's own pipeline.
