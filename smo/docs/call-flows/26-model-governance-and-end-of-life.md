# Call Flow: AI/ML Model Governance and End of Life — Approve → Certify → Promote → Rollback → Deprecate → Retire (and Retrain)

The governance half of AIMgF's 14-state `ModelLifecycle` FSM (`aimgf/app/statemachine.py`):
the operator decisions that take an emulated model to `PROMOTED`, the paths back out of
it (`ROLLBACK`, retrain, `REJECT`), and end of life (`DEPRECATE` → `RETIRE`). Call flow 02
shows the whole pipeline at a high level and call flow 17 the serving runtime; this flow
covers what each governance event does, what it writes, and what it leaves unchanged.

Every event in this flow goes through one route, `POST /aimgf/models/{id}/advance?event=…
&decided_by=…&rationale=…`. Producer rApps call it through
`sdk.lifecycle.LifecycleClient.advance_model_lifecycle(model_id, event, decided_by,
rationale)`, and the GUI calls it through the BFF. `_fire_model_event` (`aimgf/app/main.py`)
fires the FSM and writes a `LifecycleTransition` row (`fsm=MODEL`) for every event. For the
eight `GOVERNANCE_EVENTS` it also requires `decidedBy` (422 `GOVERNANCE_DECIDER_REQUIRED`
otherwise) and writes a `CertificationRecord`. `DEPRECATE` and `RETIRE` are not governance
events: they need no `decidedBy` and leave no `CertificationRecord`. No governance event
sends a notification (no webhook and no MLMF push), and none of them touches the
`RuntimeLifecycle` columns on the same row.

**ModelLifecycle transitions (from `build_model_lifecycle_fsm`):**

| From | Event | To | `decidedBy` + `CertificationRecord` | Fired by |
|---|---|---|---|---|
| `REGISTERED` | `CREATE_TRAINING` | `TRAINING` | — | `POST /training-jobs`, `/ml-training-requests`, `/ml-update-requests` |
| `TRAINING` | `TRAINING_COMPLETE` / `TRAINING_FAILED` | `TRAINED` / `FAILED` | — | `POST /training-jobs/{id}/complete`, timeout sweep (failure only) |
| `TRAINED` | `APPROVE_TRAINING` | `TRAINED` (self-loop, sets `trainingApproved`) | yes | `advance` |
| `TRAINED` | `CREATE_VALIDATION` | `VALIDATING` | — | `POST /validation-jobs`, `/ml-testing-requests` |
| `VALIDATING` | `VALIDATION_COMPLETE` / `VALIDATION_FAILED` | `VALIDATED` / `FAILED` | — | `.../complete`, testing-request cancel, timeout sweep |
| `VALIDATED` | `APPROVE_VALIDATION` | `VALIDATED` (self-loop, sets `validationApproved`) | yes | `advance` |
| `VALIDATED` | `CREATE_EMULATION` | `EMULATING` | — | `POST /emulation-jobs` |
| `EMULATING` | `EMULATION_COMPLETE` / `EMULATION_FAILED` | `EMULATED` / `FAILED` | — | `.../complete`, timeout sweep |
| `EMULATED` | `SUBMIT_FOR_APPROVAL` | `PENDING_APPROVAL` | yes | `advance` |
| `PENDING_APPROVAL` | `APPROVE` | `APPROVED` | yes | `advance` |
| `PENDING_APPROVAL` | `REJECT` | `FAILED` | yes | `advance` |
| `APPROVED` | `CERTIFY` | `CERTIFIED` | yes | `advance` |
| `CERTIFIED` | `PROMOTE` | `PROMOTED` | yes | `advance` |
| `CERTIFIED` | `DEPRECATE` | `DEPRECATED` | — | `advance` |
| `PROMOTED` | `ROLLBACK` | `CERTIFIED` | yes | `advance` |
| `PROMOTED` | `DEPRECATE` | `DEPRECATED` | — | `advance` |
| `PROMOTED` | `CREATE_TRAINING` | `TRAINING` (retrain) | — | training routes, MLMF group retrain |
| `FAILED` | `CREATE_TRAINING` | `TRAINING` (retry) | — | training routes |
| `FAILED` | `RETIRE` | `RETIRED` | — | `advance` |
| `DEPRECATED` | `RETIRE` | `RETIRED` | — | `advance` |
| `RETIRED` | (none) | terminal | — | — |

Any other (state, event) pair returns 409 `LIFECYCLE_ILLEGAL_TRANSITION`. The GUI BFF
(`gui-bff/app/rbac.py`) restricts `SUBMIT_FOR_APPROVAL`, `APPROVE`, `REJECT`, `CERTIFY`,
`PROMOTE`, `ROLLBACK`, `DEPRECATE` and `RETIRE` to the admin role. Every other `advance` event,
including `APPROVE_TRAINING`/`APPROVE_VALIDATION`, is allowed for the operator role.

**What each state allows elsewhere:**

| Model state | Training start | Runtime deploy (AIMgF) / MLLF deploy / NRM loading | Inference (runtime `ACTIVE`) |
|---|---|---|---|
| `REGISTERED`, `FAILED`, `PROMOTED` | allowed | `PROMOTED` only | allowed |
| `TRAINING` | allowed (supersedes the open job) | 409 `MODEL_NOT_CERTIFIED` | allowed |
| `CERTIFIED` | 409 `MODEL_NOT_CERTIFIED` | allowed | allowed |
| `DEPRECATED`, `RETIRED`, any other | 409 `MODEL_NOT_CERTIFIED` | 409 `MODEL_NOT_CERTIFIED` | allowed |

The inference column holds because `request_inference` checks only
`runtimeLifecycleState == ACTIVE` and never reads the model state.

## (a) Governed path to PROMOTED

```mermaid
sequenceDiagram
    actor Producer as Model Producer rApp (smo_sdk.lifecycle)
    actor Operator as Operator (GUI, operator role)
    actor Admin as Governance admin (GUI, admin role)
    participant BFF as GUI BFF (rbac.py)
    participant AIMgF as AIMgF
    participant MLLF as MLLF
    participant NFO as NFO

    Note over Producer,AIMgF: Training completed — model is TRAINED, trainingApproved=false (call flow 02)
    Operator->>BFF: POST /api/smo/aimgf/models/{id}/advance?event=APPROVE_TRAINING&decided_by=op-1
    BFF->>AIMgF: forwarded (operator tier is enough for APPROVE_TRAINING)
    AIMgF->>AIMgF: TRAINED -> TRAINED, trainingApproved=true<br/>LifecycleTransition + CertificationRecord(APPROVE_TRAINING)
    AIMgF-->>Operator: 200 lifecycle view
    Note over Producer,AIMgF: validation runs, then APPROVE_VALIDATION the same way,<br/>then emulation runs — model is EMULATED (HISTORY.md OI-6.1)

    Admin->>BFF: advance(SUBMIT_FOR_APPROVAL) without decided_by
    BFF->>AIMgF: forwarded (admin tier)
    AIMgF-->>Admin: 422 GOVERNANCE_DECIDER_REQUIRED — nothing written

    Admin->>BFF: advance(SUBMIT_FOR_APPROVAL, decided_by=admin-1, rationale)
    BFF->>AIMgF: forwarded
    AIMgF->>AIMgF: EMULATED -> PENDING_APPROVAL + CertificationRecord
    AIMgF-->>Admin: 200 modelLifecycleState=PENDING_APPROVAL
    Admin->>AIMgF: advance(APPROVE, decided_by) via BFF
    AIMgF->>AIMgF: PENDING_APPROVAL -> APPROVED + CertificationRecord
    Admin->>AIMgF: advance(CERTIFY, decided_by) via BFF
    AIMgF->>AIMgF: APPROVED -> CERTIFIED + CertificationRecord
    Note over AIMgF,MLLF: from here on, deploy is allowed — CERTIFIED and PROMOTED both pass<br/>the AIMgF runtime guard, MLLF's gate and NRM loading
    Admin->>AIMgF: advance(PROMOTE, decided_by) via BFF
    AIMgF->>AIMgF: CERTIFIED -> PROMOTED + CertificationRecord

    Producer->>AIMgF: LifecycleClient.deploy_runtime(modelId) -> POST /aimgf/models/{id}/runtime/deploy
    AIMgF->>NFO: CreateDescriptor + Instantiate (jobKind INFERENCE)
    AIMgF-->>Producer: runtimeLifecycleState=DEPLOYED (call flow 17)
    Producer->>MLLF: LifecycleClient.deploy_model(modelId, nodeGroups) -> POST /mllf/models/{id}/deploy
    MLLF->>AIMgF: GET /aimgf/models/{id}/lifecycle
    AIMgF-->>MLLF: modelLifecycleState=PROMOTED
    MLLF->>AIMgF: PATCH /aimgf/models/{id}/runtime/node-groups
    MLLF-->>Producer: clearedNodeGroups
    Producer->>AIMgF: LifecycleClient.activate_runtime(modelId)
    AIMgF-->>Producer: runtimeLifecycleState=ACTIVE

    Admin->>AIMgF: GET /aimgf/models/{id}/governance-history
    AIMgF-->>Admin: CertificationRecords in decidedAt order —<br/>APPROVE_TRAINING, APPROVE_VALIDATION, SUBMIT_FOR_APPROVAL, APPROVE, CERTIFY, PROMOTE
    Admin->>AIMgF: GET /aimgf/models/{id}/lifecycle-history?fsm=MODEL
    AIMgF-->>Admin: every LifecycleTransition (fromState, toState, event, occurredAt)
```

## (b) REJECT, then retry or retire

```mermaid
sequenceDiagram
    actor Admin as Governance admin
    actor Producer as Model Producer rApp
    participant AIMgF as AIMgF
    participant NFO as NFO

    Note over Admin,AIMgF: model is PENDING_APPROVAL
    Admin->>AIMgF: advance(REJECT, decided_by=admin-1, rationale=KPI regression in emulation)
    AIMgF->>AIMgF: PENDING_APPROVAL -> FAILED + CertificationRecord(REJECT)
    AIMgF-->>Admin: 200 modelLifecycleState=FAILED

    Producer->>AIMgF: LifecycleClient.deploy_runtime(modelId)
    AIMgF-->>Producer: 409 MODEL_NOT_CERTIFIED — guard fires before any NFO call

    alt retry — FAILED is a re-entry point
        Producer->>AIMgF: POST /aimgf/training-jobs (modelId)
        AIMgF->>NFO: CreateDescriptor + Instantiate (jobKind TRAINING)
        AIMgF->>AIMgF: FAILED -> TRAINING (CREATE_TRAINING, mLTrainingType=RE_TRAINING)<br/>trainingApproved and validationApproved reset to false
        AIMgF-->>Producer: 201 trainingJobId
    else give up
        Admin->>AIMgF: advance(RETIRE)
        AIMgF->>AIMgF: FAILED -> RETIRED (no decidedBy, no CertificationRecord)
        AIMgF-->>Admin: 200 modelLifecycleState=RETIRED
    end
```

## (c) ROLLBACK PROMOTED → CERTIFIED

```mermaid
sequenceDiagram
    actor Admin as Governance admin
    actor Producer as Model Producer rApp
    actor Consumer as Inference Consumer rApp
    participant AIMgF as AIMgF
    participant MLLF as MLLF
    participant NFO as NFO

    Note over Admin,AIMgF: model is PROMOTED, runtime is ACTIVE
    Admin->>AIMgF: advance(ROLLBACK, decided_by=admin-1, rationale=regression found)
    AIMgF->>AIMgF: PROMOTED -> CERTIFIED + CertificationRecord(ROLLBACK)
    AIMgF-->>Admin: modelLifecycleState=CERTIFIED, runtimeLifecycleState=ACTIVE
    Note over AIMgF,NFO: no NFO call, no runtime transition, no notification —<br/>ROLLBACK changes the certification state only

    Consumer->>AIMgF: POST /aimgf/models/{id}/inference-jobs
    AIMgF-->>Consumer: 201 — still served, the gate is runtime ACTIVE only
    Producer->>MLLF: POST /mllf/models/{id}/deploy
    MLLF-->>Producer: 200 — CERTIFIED still passes MLLF's gate

    Producer->>AIMgF: POST /aimgf/training-jobs (modelId)
    AIMgF-->>Producer: 409 MODEL_NOT_CERTIFIED — cannot (re)train a model in state CERTIFIED

    alt re-promote
        Admin->>AIMgF: advance(PROMOTE, decided_by)
        AIMgF->>AIMgF: CERTIFIED -> PROMOTED + CertificationRecord
    else take it out of service
        Admin->>AIMgF: advance(DEPRECATE) — see (e)
    end
```

## (d) Retrain from PROMOTED (direct or MLMF-triggered) and from FAILED

```mermaid
sequenceDiagram
    actor Producer as Model Producer rApp
    actor SA as SA SMOS (MLMF subscriber)
    participant AIMgF as AIMgF
    participant MLMR as MLMR
    participant NFO as NFO

    Note over Producer,AIMgF: model is PROMOTED with an ACTIVE runtime
    Producer->>AIMgF: LifecycleClient.start_training(modelId, producerId)
    AIMgF->>AIMgF: _start_training gate — REGISTERED, PROMOTED, FAILED or TRAINING
    AIMgF->>NFO: CreateDescriptor + Instantiate (jobKind TRAINING, TRAINING runtime profile)
    AIMgF->>AIMgF: PROMOTED -> TRAINING (CREATE_TRAINING), mLTrainingType=RE_TRAINING<br/>trainingApproved/validationApproved reset to false
    AIMgF-->>Producer: 201 trainingJobId
    Note over AIMgF: runtimeLifecycleState stays ACTIVE — the previous version keeps serving<br/>while the model re-runs Training, Validation, Emulation and governance

    Producer->>AIMgF: LifecycleClient.start_training(modelId) again, while still TRAINING
    AIMgF->>AIMgF: open TrainingJob -> CANCELLED, its NFO runtime deleted<br/>(no FSM event — model is already TRAINING)
    AIMgF-->>Producer: 201 new trainingJobId

    rect rgb(240, 248, 255)
    Note over SA,MLMR: MLMF-triggered retrain of a coordination group (call flow 02)
    Producer->>AIMgF: ReportPerformance(subscriptionId, metrics below guardKpiFloor)
    AIMgF->>SA: best-effort POST notificationDestination (breachedFloor=true)
    AIMgF->>MLMR: GET /mlmr/coordination-groups
    MLMR-->>AIMgF: group containing this model
    AIMgF->>AIMgF: should_trigger_group_retrain(ANY_MEMBER_TRIGGERS) = true
    AIMgF->>AIMgF: _start_training(member, RE_TRAINING) for every PROMOTED member —<br/>members in any other state (CERTIFIED after ROLLBACK, DEPRECATED, ...) are skipped
    AIMgF-->>Producer: reportId, groupRetrainTriggered, retrainedModelIds
    end

    Note over Producer,AIMgF: FAILED model (training, validation or emulation failure, REJECT or timeout)
    Producer->>AIMgF: POST /aimgf/training-jobs (modelId)
    AIMgF->>AIMgF: FAILED -> TRAINING (CREATE_TRAINING), RE_TRAINING
    AIMgF-->>Producer: 201 trainingJobId
```

## (e) DEPRECATE → RETIRE, and what is refused afterwards

```mermaid
sequenceDiagram
    actor Admin as Governance admin
    actor Producer as Model Producer rApp
    actor Consumer as Inference Consumer rApp
    participant AIMgF as AIMgF
    participant MLLF as MLLF
    participant NFO as NFO

    Note over Admin,AIMgF: model is PROMOTED (or CERTIFIED), runtime is ACTIVE
    Admin->>AIMgF: advance(DEPRECATE) — admin role in the BFF, no decidedBy needed
    AIMgF->>AIMgF: PROMOTED -> DEPRECATED, LifecycleTransition only, no CertificationRecord
    AIMgF-->>Admin: modelLifecycleState=DEPRECATED, runtimeLifecycleState=ACTIVE
    Admin->>AIMgF: advance(RETIRE)
    AIMgF->>AIMgF: DEPRECATED -> RETIRED (terminal)
    AIMgF-->>Admin: modelLifecycleState=RETIRED, runtimeLifecycleState=ACTIVE

    rect rgb(255, 240, 240)
    Note over Producer,MLLF: refused once DEPRECATED or RETIRED
    Producer->>AIMgF: POST /aimgf/models/{id}/runtime/deploy
    AIMgF-->>Producer: 409 MODEL_NOT_CERTIFIED
    Producer->>MLLF: POST /mllf/models/{id}/deploy
    MLLF-->>Producer: 409 MODEL_NOT_CERTIFIED
    Producer->>AIMgF: POST /aimgf/ml-model-loading-requests (this model)
    AIMgF-->>Producer: 409 MODEL_NOT_CERTIFIED
    Producer->>AIMgF: POST /aimgf/training-jobs, /ml-training-requests or /ml-update-requests
    AIMgF-->>Producer: 409 MODEL_NOT_CERTIFIED — cannot (re)train
    Producer->>AIMgF: advance(PROMOTE or ROLLBACK or CREATE_TRAINING)
    AIMgF-->>Producer: 409 LIFECYCLE_ILLEGAL_TRANSITION — RETIRED has no outgoing edges
    end

    rect rgb(255, 250, 230)
    Note over Consumer,NFO: NOT refused — no automatic runtime teardown
    Consumer->>AIMgF: POST /aimgf/models/{id}/inference-jobs
    AIMgF-->>Consumer: 201 inferenceJobId — gate is runtimeLifecycleState=ACTIVE only
    Note over AIMgF: an AIMLInferenceFunction that lists the model keeps it in mLModelRefList,<br/>and MLMF subscriptions on the model keep accepting reports
    end

    Admin->>AIMgF: POST /aimgf/models/{id}/runtime/terminate (admin role)
    AIMgF->>AIMgF: ACTIVE -> TERMINATING
    AIMgF->>NFO: DELETE /nfo/deployments/{nfDeploymentId}
    AIMgF->>AIMgF: TERMINATING -> TERMINATED
    AIMgF-->>Admin: runtimeLifecycleState=TERMINATED
    Consumer->>AIMgF: POST /aimgf/models/{id}/inference-jobs
    AIMgF-->>Consumer: 409 INFERENCE_MODEL_NOT_ACTIVE
```

**Key decisions this flow depends on:**
- One route, `POST /models/{id}/advance`, carries every governance and end-of-life event. The SDK's `advance_model_lifecycle` is a thin pass-through, and the GUI BFF's RBAC is the only place that separates admin-only events from operator-tier ones.
- The eight `GOVERNANCE_EVENTS` (`SUBMIT_FOR_APPROVAL`, `APPROVE`, `REJECT`, `CERTIFY`, `PROMOTE`, `ROLLBACK`, `APPROVE_TRAINING`, `APPROVE_VALIDATION`) require `decidedBy` and each writes a `CertificationRecord` (`GET /models/{id}/governance-history`). Every transition, governance or not, writes a `LifecycleTransition` (`GET /models/{id}/lifecycle-history?fsm=MODEL`). `DEPRECATE` and `RETIRE` appear only in the latter (HISTORY.md OI-6.1).
- `ModelLifecycle` and `RuntimeLifecycle` are independent FSMs on one row. `ROLLBACK`, `DEPRECATE`, `RETIRE` and retraining never change `runtimeLifecycleState`, never call NFO and never notify anyone. Taking a deprecated or retired model out of service is a separate, explicit `runtime/terminate` call (admin role). Until that call, inference keeps working. OPEN_ITEMS.md OI-6.1-runtime-gate covers the related open question of operator gates on runtime transitions.
- The deploy gate is `CERTIFIED` or `PROMOTED`, applied in three places: AIMgF `runtime/deploy` (before any NFO call), MLLF `POST /models/{id}/deploy`, and NRM loading (`_check_loadable`). A rolled-back (`CERTIFIED`) model therefore stays deployable.
- Retraining re-enters at `TRAINING` from `REGISTERED`, `PROMOTED` or `FAILED`, never from `CERTIFIED`, `DEPRECATED` or `RETIRED`. Retraining has no lightweight update path. A fresh `CREATE_TRAINING` resets `trainingApproved`/`validationApproved`, so a new cycle needs new operator approvals. MLMF group retrain only picks up members that are `PROMOTED` at the time of the breach.
- `REJECT` sends the model to `FAILED`, which is the shared failure and retry state, the same one a failed or timed-out Training/Validation/Emulation run lands in (call flow 27). From `FAILED`, the model can be retrained or retired.
- `RETIRED` is terminal: it has no outgoing FSM edge, and AIMgF refuses every route that would start new work on the model with 409.
