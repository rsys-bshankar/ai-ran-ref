# Call Flow: Model Runtime Lifecycle — Deploy → Node-Groups → Activate → Scale → Terminate

AIMgF's `RuntimeLifecycle` FSM — the 8-state machine governing a model's *serving*
existence, independent of the 14-state `ModelLifecycle` governing its *certification*
path (call flow 02 shows both at a high level; see "AIMgF state machines" and "AIMgF NFO
invocation" in `docs/ARCHITECTURE.md`). This is the runtime side's dedicated walkthrough,
including its guard and its calls into NFO; call flow 15 shows what NFO does with them.

**Every stage is producer-driven, with no operator or GUI step.**
`RequestModelRuntimeDeploy/Activate/Scale/Terminate` take no operator identity or
approval, only the `MODEL_NOT_CERTIFIED` guard, which checks `ModelLifecycleState` — already
operator-gated upstream via CERTIFY/PROMOTE (call flow 02). Training and Validation have an
operator gate (`APPROVE_TRAINING`/`APPROVE_VALIDATION`, HISTORY.md OI-6.1); whether runtime
transitions should get one too is undecided (OPEN_ITEMS.md OI-6.1-runtime-gate).

```mermaid
sequenceDiagram
    actor Producer as Model Producer rApp
    participant AIMgF as AIMgF
    participant NFO as NFO
    participant MLLF as MLLF

    rect rgb(255, 240, 240)
    Note over Producer,AIMgF: Guard — governance must clear before a runtime can exist at all
    Producer->>AIMgF: RequestModelRuntimeDeploy(modelId)
    Note over AIMgF: ModelLifecycleState is still TRAINED, not CERTIFIED/PROMOTED
    AIMgF-->>Producer: 409 MODEL_NOT_CERTIFIED — RuntimeLifecycle guard fires<br/>before NFO is ever called, so a premature deploy attempt<br/>never creates an orphaned NFO descriptor
    end

    rect rgb(240, 255, 240)
    Note over Producer,NFO: Deploy — RuntimeLifecycle: NOT_DEPLOYED -> DEPLOYMENT_REQUESTED -> DEPLOYED
    Note over AIMgF: (model has since reached CERTIFIED, via call flow 02's own governance gate)
    Producer->>AIMgF: RequestModelRuntimeDeploy(modelId, package_id?, runtimeProfile?)
    AIMgF->>AIMgF: RuntimeLifecycle: NOT_DEPLOYED -> DEPLOYMENT_REQUESTED
    Note over AIMgF: Wave 7: INFERENCE profile = explicit body, else the rApp package's<br/>manifest runtimeProfiles.INFERENCE (Onboarding onboarding-status)
    AIMgF->>NFO: CreateDescriptor(packageId=null, workloadTemplate={modelId, jobKind: INFERENCE, resources})
    NFO-->>AIMgF: nfDeploymentDescriptorId
    AIMgF->>NFO: Instantiate(nfDeploymentDescriptorId, name)
    NFO-->>AIMgF: nfDeploymentId, state=RUNNING (call flow 15's own synchronous elision)
    AIMgF->>AIMgF: RuntimeLifecycle: DEPLOYMENT_REQUESTED -> DEPLOYED<br/>(store nfDeploymentDescriptorId/nfDeploymentId on the lifecycle row)
    AIMgF-->>Producer: RuntimeLifecycleState=DEPLOYED
    end

    rect rgb(240, 248, 255)
    Note over MLLF,AIMgF: Node-group placement — MLLF decides, AIMgF's row is the record
    MLLF->>AIMgF: GET /models/{id}/lifecycle
    AIMgF-->>MLLF: modelLifecycleState=CERTIFIED, runtimeLifecycleState=DEPLOYED
    MLLF->>AIMgF: PATCH /models/{id}/runtime/node-groups (clearedNodeGroups)
    Note over AIMgF: local-only — no NFO call — the same "MLLF owns the decision,<br/>AIMgF owns the row" split as MLMR's own former column, Wave 1
    AIMgF-->>MLLF: updated lifecycle
    end

    rect rgb(255, 250, 230)
    Note over Producer,AIMgF: Activate — AIMgF's own decision, not a further NFO call
    Producer->>AIMgF: RequestModelRuntimeActivate(modelId)
    AIMgF->>AIMgF: RuntimeLifecycle: DEPLOYED -> ACTIVATING -> ACTIVE<br/>(NFO's own deployment is already RUNNING since Deploy returned —<br/>Activate only ever decides whether traffic should be sent yet)
    AIMgF-->>Producer: RuntimeLifecycleState=ACTIVE
    end

    rect rgb(250, 240, 255)
    Note over Producer,NFO: Scale and Terminate — both real NFO calls, gated by RuntimeLifecycle first
    Producer->>AIMgF: RequestModelRuntimeScale(modelId)
    AIMgF->>AIMgF: RuntimeLifecycle: ACTIVE -> SCALING
    AIMgF->>NFO: POST /deployments/{nfDeploymentId}/scale
    NFO-->>AIMgF: state=RUNNING (call flow 15's own RUNNING->UPDATING->RUNNING elision)
    AIMgF->>AIMgF: RuntimeLifecycle: SCALING -> ACTIVE
    AIMgF-->>Producer: RuntimeLifecycleState=ACTIVE

    Producer->>AIMgF: RequestModelRuntimeTerminate(modelId)
    AIMgF->>AIMgF: RuntimeLifecycle: ACTIVE -> TERMINATING
    AIMgF->>NFO: DELETE /deployments/{nfDeploymentId}
    NFO-->>AIMgF: 204 (call flow 15's own real, synchronous deletion)
    AIMgF->>AIMgF: RuntimeLifecycle: TERMINATING -> TERMINATED
    AIMgF-->>Producer: RuntimeLifecycleState=TERMINATED
    end
```

**Key decisions this flow depends on:**
- Every execution runtime is sized from its execution mode's runtime profile — `workloadTemplate.resources` = {cpu, memory, gpu} — taken from an explicit `runtimeProfile` or from the rApp package's manifest `runtimeProfiles[<MODE>]` (W7-03). The same applies to the transient Training/Validation/Emulation runtimes (call flow 02), each with its own mode. Execution timeouts (W7-04: Training 30 min, Validation 15 min, Emulation 30 min, Inference 5 s) fail an overdue run cleanly; see "Runtime profiles and timeouts" in `docs/STANDARDS.md`.
- `RuntimeLifecycle` and `ModelLifecycle` are deliberately independent FSMs sharing one row — retraining a `PROMOTED` model doesn't force its runtime down, and a runtime can be scaled/terminated without touching the model's certification state.
- The `MODEL_NOT_CERTIFIED` guard fires *before* any NFO call — `deploy_model_runtime` checks `ModelLifecycleState` first, so a premature or duplicate deploy attempt never creates an orphaned `NFDeploymentDescriptor`/`NFDeployment` that would then need cleanup.
- Deploy, Scale and Terminate are the RuntimeLifecycle transitions that call NFO through `R1Client`: Deploy creates a descriptor and instantiates it, Scale calls `/deployments/{id}/scale`, Terminate calls `DELETE /deployments/{id}`. Each lands in call flow 15's dispatch, including its synchronous elision and its unreachable `ABNORMAL`/`DELETING` branches, since it's the same NFO code either caller reaches.
- `update_node_groups` never calls NFO — MLLF owns the *decision* of node-group placement and AIMgF's row is where that decision is written.
- Activate never re-contacts NFO — by the time Deploy returns, NFO's deployment is already `RUNNING` (Phase 1's synchronous elision, call flow 15); Activate is purely AIMgF's own gate on whether `RequestInference` may reach this model yet.
