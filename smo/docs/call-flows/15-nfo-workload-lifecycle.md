# Call Flow: NFO Workload Lifecycle — Instantiate → Scale → Heal → Terminate

Stitches together NFO+FOCOM LLD section 4 and `app/statemachine.py`'s real 7-state
`DeploymentState` FSM (`o2dms/domain/states.py`'s own reference shape, kept under this
build's existing state names). Only `instantiate`/`terminate` get touched briefly inside
call flows 01/02/10 today — this is NFO's own dedicated walkthrough of its full 5-operation
surface, including two guard rejections and a real, honestly-documented FSM dead end.

```mermaid
sequenceDiagram
    actor Caller as rApp Mgmt / AIMgF
    participant NFO as NFO
    participant Focom as FOCOM

    rect rgb(240, 255, 240)
    Note over Caller,NFO: Descriptor → instantiate
    Caller->>NFO: CreateDescriptor(packageId?, name, requiredResourceTypeId?, workloadTemplate)
    NFO-->>Caller: nfDeploymentDescriptorId
    Caller->>NFO: Instantiate(nfDeploymentDescriptorId, name)
    NFO->>Focom: GET /inventory?resource_type=requiredResourceTypeId
    Focom-->>NFO: oCloudId (Phase 1: always the same degenerate cluster)
    NFO->>NFO: state: INITIAL -> INSTANTIATING -> RUNNING<br/>(Phase 1 elision: real docker run/Helm install completes<br/>synchronously within this one call, same pattern<br/>call flow 02's own training/validation/emulation elisions use)
    NFO-->>Caller: nfDeploymentId, state=RUNNING, clusterId
    end

    rect rgb(255, 240, 240)
    Note over Caller,NFO: Guards — a descriptor deploys at most once, a name is unique
    Caller->>NFO: Instantiate(nfDeploymentDescriptorId, name="other-name")
    NFO-->>Caller: 409 NFDEPLOYMENT_DESCRIPTOR_ALREADY_DEPLOYED — this descriptor already has a deployment
    Caller->>NFO: Instantiate(otherDescriptorId, name (already in use))
    NFO-->>Caller: 409 NFDEPLOYMENT_NAME_CONFLICT
    end

    rect rgb(240, 248, 255)
    Note over Caller,NFO: Scale — RUNNING only, synchronous like Instantiate
    Caller->>NFO: Scale(nfDeploymentId)
    NFO->>NFO: state: RUNNING -> UPDATING -> RUNNING<br/>(real Helm-upgrade replica-count change elided, same pattern)
    NFO-->>Caller: nfDeploymentId, state=RUNNING
    end

    rect rgb(255, 250, 230)
    Note over Caller,NFO: Heal — idempotent from RUNNING, real recovery from ABNORMAL
    Caller->>NFO: Heal(nfDeploymentId)
    NFO->>NFO: state: RUNNING -HEAL-> RUNNING (already healthy — a no-op transition, not an error)
    NFO-->>Caller: nfDeploymentId, state=RUNNING
    end

    rect rgb(250, 240, 255)
    Note over Caller,NFO: Terminate — deletion is real and synchronous, not staged
    Caller->>NFO: Terminate(nfDeploymentId)
    NFO->>NFO: state: RUNNING -TERMINATE-> TERMINATING (transient FSM value only)
    NFO->>NFO: real Helm-uninstall elided synchronously — NFOCloudResource and<br/>LCMOperation rows deleted, then the NFDeployment row itself
    NFO-->>Caller: 204
    end
```

**Key decisions this flow depends on:**
- FOCOM's inventory is always queried before placement, even though Phase 1's answer is always the same degenerate cluster — the same "ask the real dependency, don't hardcode the Phase-1 answer" discipline call flow 01 already establishes for FOCOM.
- `ALREADY_DEPLOYED` and `NAME_CONFLICT` are two independent guards, checked in that order: a descriptor can only ever back one deployment, and every deployment's name is globally unique regardless of which descriptor it came from.
- Scale and Heal both fire two FSM events within one request (`RUNNING -> UPDATING -> RUNNING`, `ABNORMAL -> RUNNING` or `RUNNING -> RUNNING`) rather than staying observably mid-transition — the same synchronous-elision pattern Instantiate and Terminate both already use, since no real Helm/K8s operation backs any of them yet.
- **A genuinely dead-end FSM branch, surfaced by writing this flow, not previously documented this way**: `DeploymentState.DELETING` and `DeploymentState.ABNORMAL` are both real states with real dispatch logic mirroring the reference's own `dms_lcm_nfdeployment.py` exactly (`ABNORMAL` recoverable via `Heal`, `DELETING` re-`Terminate`d flips to `ABNORMAL` as a defensive catch-all) — but neither is reachable by any sequence of real API calls in this build. Every `Terminate` call that computes `DELETING` as an intermediate FSM value falls straight through to synchronous row deletion in that same request (matching Instantiate/Scale's own synchronous elision), so a second `Terminate` call can never actually find a row still sitting in `DELETING` to trigger the `ABNORMAL` catch-all, and nothing else in this build ever sets `ABNORMAL` either. This build's own unit tests (`test_main.py`) exercise both branches only by writing `state = "DELETING"`/`"ABNORMAL"` directly via a raw DB session, never by driving two real HTTP calls in sequence — confirming this isn't an oversight in the tests, it's the only way those branches are reachable at all today. Worth adding to `OPEN_ITEMS.md` if `Terminate` ever becomes genuinely asynchronous (a real Helm uninstall that doesn't complete within one request) — that's exactly the condition under which a second `Terminate` racing the first would become possible.
