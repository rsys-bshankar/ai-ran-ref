# Call Flow: NFO Workload Lifecycle — Instantiate → Scale → Heal → Terminate (synchronous or DMS-completed)

NFO's full workload surface — CreateDescriptor, Instantiate, Scale, Heal and Terminate —
over its 7-state `DeploymentState` FSM (`nfo/app/statemachine.py`, following
`o2dms/domain/states.py`'s reference shape under this build's state names; NFO+FOCOM LLD
section 4), including the two Instantiate guards. Its callers are rApp Management (call
flow 01), AIMgF's model and execution runtimes (call flows 02 and 17), SO SMOS's DEPLOY step
(call flow 10) and SA SMOS's RECONNECT remedial action (call flow 04).

```mermaid
sequenceDiagram
    actor Caller as rApp Mgmt / AIMgF
    participant NFO as NFO
    participant Focom as FOCOM
    participant DMS as Deployment manager (O2 DMS)

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
    Note over Caller,NFO: Terminate — synchronous by default, asynchronous on request
    alt default
        Caller->>NFO: DELETE /deployments/{id}
        NFO->>NFO: RUNNING -TERMINATE-> TERMINATING -UNINSTALL_COMPLETE-> DELETING,<br/>then NFOCloudResource, LCMOperation and NFDeployment rows deleted
        NFO-->>Caller: 204, the descriptor is free again
    else async_uninstall=true
        Caller->>NFO: DELETE /deployments/{id}?async_uninstall=true
        NFO->>NFO: RUNNING -TERMINATE-> TERMINATING, TERMINATE operation IN_PROGRESS
        NFO-->>Caller: 202, state=TERMINATING
        DMS->>NFO: POST /deployments/{id}/dms-notifications (UNINSTALL_COMPLETE)
        NFO->>NFO: TERMINATING -> DELETING
        alt resources released
            DMS->>NFO: dms-notifications (DELETE_COMPLETE)
            NFO->>NFO: rows deleted, state=DELETED
        else uninstall or deletion failed
            DMS->>NFO: dms-notifications (UNINSTALL_FAILED or DELETE_FAILED, detail)
            NFO->>NFO: -> ABNORMAL, abnormalReason set, TERMINATE operation FAILED
            Caller->>NFO: DELETE again (ABNORMAL -> DELETING) or Heal (ABNORMAL -> RUNNING)
        end
    end
    end
```

**Key decisions this flow depends on:**
- FOCOM's inventory is always queried before placement, even though Phase 1's answer is always the same degenerate cluster — the same "ask the real dependency, don't hardcode the Phase-1 answer" discipline call flow 01 establishes for FOCOM.
- `ALREADY_DEPLOYED` and `NAME_CONFLICT` are two independent guards, checked in that order: a descriptor can only ever back one deployment, and every deployment's name is globally unique regardless of which descriptor it came from.
- Scale and Heal both fire their FSM events within one request (`RUNNING -> UPDATING -> RUNNING`, `ABNORMAL -> RUNNING` or `RUNNING -> RUNNING`) rather than staying observably mid-transition — the same synchronous elision Instantiate and Terminate use, since no real Helm/K8s operation backs any of them.
- `DELETING` and `ABNORMAL` are reached through the API (HISTORY.md OI-3-nfo-abnormal). An asynchronous Terminate rests in `TERMINATING`, then `DELETING`, until the deployment manager reports; an uninstall or deletion failure, or a `RUNTIME_FAILURE` on a running workload, leaves it `ABNORMAL` with `abnormalReason`. `Heal` recovers an `ABNORMAL` deployment; `Terminate` retires it; a `DELETING` deployment re-`Terminate`d flips to `ABNORMAL` as a defensive catch-all.
- Terminate is synchronous by default because every SMO caller expects the deployment gone and its descriptor free when the call returns (an rApp rollback re-deploys a released descriptor). While a deployment is `TERMINATING`, `DELETING` or `ABNORMAL`, its descriptor stays deployed.
