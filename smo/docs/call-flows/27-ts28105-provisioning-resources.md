# Call Flow: TS 28.105 Provisioning Resources — Training Request/Process, Update Request, Model Loading Policy/Request, Execution Timeouts

AIMgF exposes every TS 28.105 AI/ML NRM IOC it owns as a flat REST resource with the spec's
own attribute names (`aimgf/app/nrm.py`, HISTORY.md W4, `docs/ROADMAP.md` TS 28.105 matrix).
The request resources are not a separate bookkeeping layer. An `MLTrainingRequest` *is* a
`TrainingJob`, and an `MLTestingRequest` *is* a `ValidationJob`. Both start through the same
`_start_training` / `_start_validation` core as `POST /training-jobs` and
`POST /validation-jobs`. As a result, the `ModelLifecycle` gate, the operator approvals (call
flow 26), the NFO execution runtime and the stage timeouts (HISTORY.md W7) apply the same way
whichever surface a caller uses. This flow covers the provisioning side: training
request/process/report, ML update, model loading, and the per-execution-mode timeout sweep.

**Status vocabularies (from the code):**

| Resource | Values produced | Notes |
|---|---|---|
| `MLTrainingRequest.requestStatus` (= `TrainingJob.status`) | `IN_PROGRESS`, `SUSPENDED`, `FINISHED`, `FAILED`, `CANCELLED` | `NOT_STARTED`/`CANCELLING` never produced: start and cancel are synchronous |
| `MLTrainingProcess.progressStatus.status` | `RUNNING`, `SUSPENDED`, `FINISHED`, `FAILED`, `CANCELLED` | mapped from the job by `_sync_training_process` |
| `MLTestingRequest.requestStatus` | `IN_PROGRESS`, `SUSPENDED`, `FINISHED`, `CANCELLED` | outcome is in `MLTestingReport.mLTestingResult` |
| `MLUpdateRequest.requestStatus` / `MLUpdateProcess` status | `IN_PROGRESS`/`SUSPENDED`/`FINISHED`/`CANCELLED` / `RUNNING`/`SUSPENDED`/`FINISHED`/`FAILED`/`CANCELLED` | request is `FINISHED` even when the process `FAILED` |
| `MLModelLoadingRequest.requestStatus` | `SUSPENDED`, `FINISHED`, `CANCELLED` (`IN_PROGRESS` only inside the call) | loading is synchronous |

## Training request → process → report, suspend/resume, cancel

```mermaid
sequenceDiagram
    actor Requester as Training requester (rApp or operator)
    participant AIMgF as AIMgF (nrm.py + main.py)
    participant MLMR as MLMR
    participant NFO as NFO
    participant MLTF as MLTF execution runtime
    participant DME as DME

    Requester->>AIMgF: POST /aimgf/ml-training-requests<br/>(mLModelRef or mLModelCoordinationGroupRef, trainingRequestSource,<br/>mLTrainingFunctionRef?, mLTrainingType?, dmeDataJobIds?, notificationUri?)
    AIMgF->>AIMgF: exactly one of mLModelRef / group ref — COORDINATION_GROUP_MISMATCH otherwise<br/>mLTrainingFunctionRef must exist — NRM_OBJECT_NOT_FOUND otherwise
    opt dmeDataJobIds given
        AIMgF->>DME: GET /dme/data-jobs/{id} per id
        DME-->>AIMgF: 200 or DME_ARTIFACT_NOT_FOUND (HISTORY.md OI-6.4)
    end
    AIMgF->>MLMR: GET /mlmr/models/{id} (model-targeted only)
    AIMgF->>AIMgF: _start_training gate — model must be REGISTERED, PROMOTED, FAILED or TRAINING<br/>else 409 MODEL_NOT_CERTIFIED. INITIAL_TRAINING only for a REGISTERED model
    AIMgF->>AIMgF: TrainingJob(status=IN_PROGRESS, timeoutSeconds=TRAINING default)<br/>+ MLTrainingProcess(status=RUNNING, priority, terminationConditions)
    AIMgF->>NFO: CreateDescriptor(workloadTemplate={jobKind TRAINING, jobId}) + Instantiate
    NFO-->>AIMgF: nfDeploymentId (stored on the job)
    AIMgF->>AIMgF: ModelLifecycle CREATE_TRAINING -> TRAINING (model-targeted)<br/>or supersede the model's open job (CANCELLED, its NFO runtime deleted)
    AIMgF-->>Requester: 201 {id, attributes: requestStatus=IN_PROGRESS, mLTrainingType, ...}

    loop while training
        MLTF->>AIMgF: POST /aimgf/ml-training-processes/{id}/progress<br/>(progressPercentage 0-100, progressStateInfo?, resultStateInfo?)
        AIMgF-->>MLTF: process view — 409 TRAINING_JOB_ILLEGAL_TRANSITION unless process is RUNNING
    end

    rect rgb(250, 240, 255)
    Note over Requester,AIMgF: suspend / resume — three equivalent surfaces, no NFO call, ModelLifecycle untouched
    Requester->>AIMgF: PATCH /ml-training-requests/{id} {suspendRequest: true}<br/>(or PATCH /ml-training-processes/{id} {suspendProcess: true}, or POST /training-jobs/{id}/suspend)
    AIMgF->>AIMgF: IN_PROGRESS -> SUSPENDED, process SUSPENDED — 409 from any other status
    Note over AIMgF: a SUSPENDED run is skipped by the timeout sweep
    Requester->>AIMgF: POST /training-jobs/{id}/resume
    AIMgF->>AIMgF: SUSPENDED -> IN_PROGRESS, startedAt reset to now (timeout clock restarts)
    Note over Requester,AIMgF: PATCH {suspendRequest: false} also resumes, but does not reset startedAt
    end

    alt run completes
        MLTF->>AIMgF: POST /aimgf/training-jobs/{id}/complete (succeeded, metrics, TS 28.105 report fields)
        AIMgF->>AIMgF: job FINISHED or FAILED — 409 if not IN_PROGRESS/SUSPENDED (late result)
        AIMgF->>NFO: DELETE /nfo/deployments/{nfDeploymentId}
        AIMgF->>AIMgF: TRAINING_COMPLETE -> TRAINED or TRAINING_FAILED -> FAILED<br/>process FINISHED/100/SUCCEEDED (or FAILED)<br/>MLTrainingReport (lastTrainingRef chains to the previous report, mLModelGeneratedRef on success)
        AIMgF->>Requester: best-effort POST notificationUri (jobKind TRAINING, succeeded, metrics)
    else requester cancels
        Requester->>AIMgF: PATCH /ml-training-requests/{id} {cancelRequest: true}<br/>(or DELETE /ml-training-requests/{id}, or DELETE /training-jobs/{id})
        AIMgF->>NFO: DELETE /nfo/deployments/{nfDeploymentId}
        AIMgF->>AIMgF: job CANCELLED, cancelRequest=true, process CANCELLED
        Note over AIMgF: no ModelLifecycle event — the model stays TRAINING until a new<br/>training request supersedes the job or a generic advance moves it
        AIMgF-->>Requester: 200 / 204 — the job row stays as history
    end

    Requester->>AIMgF: GET /ml-training-processes/{id}, GET /ml-training-reports?model_id=
    AIMgF-->>Requester: progressStatus, trainingRequestRef, trainingReportRef, report attributes
```

## ML update request → FINE_TUNING runs → update report

```mermaid
sequenceDiagram
    actor Requester as Update requester
    participant AIMgF as AIMgF
    participant NFO as NFO
    participant MLTF as MLTF execution runtimes

    Requester->>AIMgF: POST /aimgf/ml-update-requests<br/>(mLModelRefList, mLUpdateFunctionRef?, newCapabilityVersionId?, performanceGainThreshold?)
    AIMgF->>AIMgF: mLModelRefList non-empty — SCHEMA_VALIDATION_FAILED otherwise<br/>every model REGISTERED, PROMOTED or FAILED — 409 MODEL_NOT_CERTIFIED before any run starts
    AIMgF->>AIMgF: MLUpdateRequest(IN_PROGRESS) + MLUpdateProcess(RUNNING)
    loop per model in mLModelRefList
        AIMgF->>NFO: CreateDescriptor + Instantiate (jobKind TRAINING)
        AIMgF->>AIMgF: _start_training(mLTrainingType=FINE_TUNING, mlUpdateProcessId)<br/>CREATE_TRAINING -> TRAINING
    end
    AIMgF-->>Requester: 201 {requestStatus IN_PROGRESS, mLUpdateProcessRef}

    loop each run ends (complete, timeout or cancel)
        MLTF->>AIMgF: POST /training-jobs/{id}/complete
        AIMgF->>AIMgF: advance_ml_update_process — progressPercentage = terminal runs / all runs
    end
    AIMgF->>AIMgF: all runs terminal — process FINISHED (all succeeded) or FAILED,<br/>resultStateInfo = n/m models updated, request FINISHED
    AIMgF->>AIMgF: MLUpdateReport — updatedMLCapability {availMLCapabilityReportID,<br/>mLCapabilityVersionId, expectedPerformanceGains from numeric modelMetrics}<br/>copied onto MLUpdateFunction.availMLCapabilityReport and mLModelRef

    alt suspend or resume the whole update
        Requester->>AIMgF: PATCH /ml-update-requests/{id} {suspendRequest: true or false}
        AIMgF->>AIMgF: applied to every run — suspend is 409 if any run is not IN_PROGRESS<br/>request and process SUSPENDED / IN_PROGRESS + RUNNING
    else cancel
        Requester->>AIMgF: PATCH /ml-update-requests/{id} {cancelRequest: true}
        AIMgF->>NFO: DELETE /nfo/deployments/{id} per still-running run
        AIMgF->>AIMgF: request and process CANCELLED, open runs CANCELLED, no report written
    end
    Note over Requester,AIMgF: PATCH on a FINISHED or CANCELLED update — 409 TRAINING_JOB_ILLEGAL_TRANSITION
```

## Model loading — policy trigger and loading request

```mermaid
sequenceDiagram
    actor Requester as Loading requester
    actor Monitor as Policy evaluator (e.g. MLMF breach handler)
    participant AIMgF as AIMgF
    participant NFO as NFO
    actor Consumer as Inference Consumer rApp

    Requester->>AIMgF: POST /aimgf/aiml-inference-functions (aIMLInferenceName, activationStatus=DEACTIVATED)
    AIMgF-->>Requester: 201 AIMLInferenceFunction F

    rect rgb(240, 248, 255)
    Note over Monitor,AIMgF: policy-driven loading — no MLModelLoadingRequest behind it
    Requester->>AIMgF: POST /aimgf/ml-model-loading-policies (aIMLInferenceFunctionRef=F, mLModelRef, policyForLoading)
    AIMgF-->>Requester: 201 policy P (stored only — AIMgF never evaluates thresholdList itself)
    Monitor->>AIMgF: POST /aimgf/ml-model-loading-policies/P/trigger
    AIMgF->>AIMgF: _check_loadable — every model CERTIFIED or PROMOTED (MODEL_NOT_CERTIFIED)<br/>and runtime not TERMINATING/TERMINATED (LIFECYCLE_ILLEGAL_TRANSITION) — all or nothing
    AIMgF->>AIMgF: MLModelLoadingProcess(RUNNING, mLModelLoadingPolicyRef=[P])
    end

    rect rgb(240, 255, 240)
    Note over Requester,AIMgF: request-driven loading
    Requester->>AIMgF: POST /aimgf/ml-model-loading-requests (F, mLModelToLoadRef, suspendRequest?)
    AIMgF->>AIMgF: same _check_loadable
    alt suspendRequest=true
        AIMgF-->>Requester: 201 requestStatus=SUSPENDED, nothing loaded
        Requester->>AIMgF: PATCH /ml-model-loading-requests/{id} {suspendRequest: false}
        AIMgF->>AIMgF: _check_loadable again, then load
    else cancelRequest=true (at creation or by PATCH before loading)
        AIMgF-->>Requester: requestStatus=CANCELLED, nothing loaded
    end
    end

    loop _run_loading, per model
        alt runtimeLifecycleState NOT_DEPLOYED
            AIMgF->>NFO: CreateDescriptor(jobKind INFERENCE) + Instantiate
            AIMgF->>AIMgF: RuntimeLifecycle NOT_DEPLOYED -> DEPLOYMENT_REQUESTED -> DEPLOYED
        end
        alt runtimeLifecycleState DEPLOYED
            AIMgF->>AIMgF: RuntimeLifecycle DEPLOYED -> ACTIVATING -> ACTIVE (no NFO call)
        end
        AIMgF->>AIMgF: add model to F.mLModelRefList, process loadedMLModelRef + progressPercentage
    end
    AIMgF->>AIMgF: process FINISHED/100/SUCCEEDED, request FINISHED
    AIMgF-->>Requester: 201 request (or process) view
    Note over AIMgF: loading uses AIMgF's own RuntimeLifecycle helpers, not MLLF —<br/>clearedNodeGroups is not set and no runtime profile is sent

    Consumer->>AIMgF: POST /aimgf/models/{id}/inference-jobs?aiml_inference_function_id=F
    AIMgF-->>Consumer: 409 INFERENCE_FUNCTION_NOT_ACTIVATED while F is DEACTIVATED
    Requester->>AIMgF: PATCH /aimgf/aiml-inference-functions/F {activationStatus: ACTIVATED}
    Consumer->>AIMgF: POST /aimgf/models/{id}/inference-jobs?aiml_inference_function_id=F
    AIMgF-->>Consumer: 201 — runtime ACTIVE, F ACTIVATED and the model in F.mLModelRefList<br/>(MODEL_NOT_LOADED otherwise)
```

## Execution timeouts — per-mode sweep (Wave 7)

```mermaid
sequenceDiagram
    actor Scheduler as Scheduler (or any job read)
    participant AIMgF as AIMgF
    participant NFO as NFO
    actor Requester as Job requester (notificationUri)

    Note over AIMgF: timeoutSeconds is fixed at job start — request timeoutSeconds, else<br/>AIMGF_TIMEOUT_KIND_SECONDS, else Training 1800, Validation 900, Emulation 1800, Inference 5
    Scheduler->>AIMgF: POST /aimgf/execution-timeouts/sweep
    Note over Scheduler,AIMgF: the same _expire_overdue_jobs also runs lazily on every training, validation,<br/>emulation and inference status, list, complete and resolve call (not on the NRM GET routes)

    loop TrainingJob IN_PROGRESS past startedAt + timeoutSeconds
        AIMgF->>NFO: DELETE /nfo/deployments/{nfDeploymentId}
        AIMgF->>AIMgF: job FAILED, TRAINING_FAILED if still legal -> model FAILED<br/>MLTrainingProcess FAILED, resultStateInfo=TIMEOUT<br/>advance_ml_update_process when the run belongs to an ML update
    end
    loop ValidationJob / EmulationJob RUNNING past deadline
        AIMgF->>NFO: DELETE /nfo/deployments/{nfDeploymentId}
        AIMgF->>AIMgF: job FAILED, metrics.failureReason=TIMEOUT<br/>VALIDATION_FAILED / EMULATION_FAILED if still legal -> model FAILED<br/>validation also writes MLTestingReport(mLTestingResult=FAILED)
    end
    loop InferenceJob RUNNING past deadline
        AIMgF->>AIMgF: InferenceJob RUNNING -> FAILED — no NFO call, no lifecycle change
    end
    AIMgF->>AIMgF: commit
    AIMgF->>Requester: best-effort POST per expired job (jobKind, jobId, succeeded=false, failureReason TIMEOUT)
    AIMgF-->>Scheduler: {expired: [{jobKind, jobId}], defaultTimeoutSeconds}

    Requester->>AIMgF: POST /aimgf/training-jobs/{id}/complete (late result)
    AIMgF-->>Requester: 409 TRAINING_JOB_ILLEGAL_TRANSITION — a timed-out run is never resurrected
    Note over Requester,AIMgF: model is FAILED — retrain via a new training request (call flow 26 d) or RETIRE
```

**Key decisions this flow depends on:**
- One start path. `POST /ml-training-requests`, `POST /training-jobs`, MLMF group retrain and `POST /ml-update-requests` all call `_start_training`, so every run gets the same lifecycle gate, NFO execution runtime, `MLTrainingProcess` and default timeout. The same holds for `POST /ml-testing-requests` and `POST /validation-jobs` through `_start_validation`, including the `APPROVE_TRAINING` gate (HISTORY.md OI-6.1, OI-6.2).
- `mLTrainingType` is derived as `INITIAL_TRAINING` for a `REGISTERED` model and `RE_TRAINING` otherwise. A requester may name `PRE_SPECIALISED_TRAINING` or `FINE_TUNING`, and an ML update always uses `FINE_TUNING`. Naming `INITIAL_TRAINING` for a model that has already been trained returns 409 `LIFECYCLE_ILLEGAL_TRANSITION`.
- Suspend and resume are plain job status flips. They make no NFO call, and the execution runtime stays up while the run is suspended. They do not touch `ModelLifecycle`. A suspended training run never times out, and `POST /training-jobs/{id}/resume` restarts its clock.
- Cancelling a training run tears down its NFO runtime and marks the job and process `CANCELLED`, but fires no `ModelLifecycle` event. Cancelling a testing request (`PATCH /ml-testing-requests/{id} {cancelRequest}`) fires `VALIDATION_FAILED`, which moves the model to `FAILED`.
- An ML update is all-or-nothing at start and per-run afterwards. It finishes when every run is terminal. Its process is `FINISHED` only if every run succeeded, and the `MLUpdateReport` lists only the models that succeeded.
- Model loading checks every model up front (`CERTIFIED`/`PROMOTED`, runtime not terminating), then brings each runtime to `ACTIVE` through AIMgF's own `RuntimeLifecycle` and NFO (call flow 17). MLLF is not involved. A loading policy is stored data plus an explicit `/trigger`, and nothing in AIMgF evaluates its `thresholdList`. There is no unload operation: cancelling a loading request does not remove anything already loaded.
- Inference through an `AIMLInferenceFunction` needs three things: a runtime in `ACTIVE`, a function in `ACTIVATED`, and the model in the function's `mLModelRefList`.
- Execution timeouts (HISTORY.md W7, `docs/ROADMAP.md` "Runtime profiles and timeouts") fail an overdue run cleanly. The run's status becomes `FAILED`, its NFO runtime is deleted, and the model's stage is failed only when that transition is still legal (`_fire_if_legal`), so there is no lifecycle corruption. The requester is notified with `failureReason: TIMEOUT`, and a late completion or resolve returns 409. An inference timeout fails only the `InferenceJob`.
- Runs started through the NRM surface (`MLTrainingRequest`, `MLTestingRequest`, `MLUpdateRequest`) take no `packageId`/`runtimeProfile`/`timeoutSeconds`. Their NFO runtime is unsized and they use the deployment-wide default timeout. Per-run sizing and timeout overrides are available only on the `/training-jobs`-style routes (W7-03, W7-04).
