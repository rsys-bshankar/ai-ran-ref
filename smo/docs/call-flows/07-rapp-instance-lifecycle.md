# Call Flow: rApp Instance Lifecycle — Run → Report → Fault → Recover → Upgrade → Reconfigure → Terminate → Delete

A `RAppInstance` moves through rApp Management's instance FSM
(`rapp-mgmt/app/statemachine.py`, Onboarding/rApp Mgmt LLD sections 5-6) once
`CreateInstance` and `bootstrap-complete` have brought it to `RUNNING` (call flow 01). A
running instance reports performance and faults. A critical fault drives `CRASH` to
`FAULTED`, and `POST /instances/{id}/recover` sends it back through bootstrap. An upgrade
runs a replacement instance next to the old one and resolves to commit or rollback.
`PUT /instances/{id}/config` replaces the stored configuration. `terminate` tears the instance
down to `UNDEPLOYED`, and `DELETE` removes the row. Performance reporting is a pure record with
no FSM transition (contrast AI/ML Workflow's `ReportPerformance`, call flow 02, which drives
a retrain decision).

## RAppInstance FSM

| State | Events out (→ target) | Action on the transition |
|---|---|---|
| `DEPLOYING` | `BOOTSTRAP_OK` → `RUNNING`, `BOOTSTRAP_FAILED` → `FAULTED` | none |
| `RUNNING` | `START_UPGRADE` → `UPGRADING`, `TERMINATE` → `UNDEPLOYED`, `CRASH` → `FAULTED` | `TERMINATE`: deregister DME and SME, then revoke the credential. `CRASH`: deregister DME and SME |
| `UPGRADING` | `UPGRADE_COMMIT` → `UNDEPLOYED`, `UPGRADE_ROLLBACK` → `RUNNING` | `UPGRADE_COMMIT`: revoke the credential |
| `FAULTED` | `RECOVER` → `DEPLOYING` | none |
| `UNDEPLOYED` | none (terminal). `DELETE /instances/{id}` removes the row | — |

No transition has a guard. An event with no edge from the current state raises
`IllegalTransition`, which the rApp Management routes do not map to a framework error.
`TERMINATE` and `CRASH` leave only from `RUNNING`, so a `DEPLOYING`, `UPGRADING` or `FAULTED`
instance cannot be terminated, and a non-`RUNNING` instance cannot crash.

## Report, fault, recover

```mermaid
sequenceDiagram
    participant Container as rApp container
    participant R1 as R1 Termination
    participant Rapp as rApp Management SMOS
    participant DME as DME
    participant SME as SME
    participant Onb as Onboarding SMOS

    Note over Container,Rapp: instance RUNNING (call flow 01)

    loop periodic
        Container->>R1: POST /rapp-mgmt/instances/{id}/performance (metrics)
        R1->>Rapp: (proxied) ReportPerformance
        Rapp->>Rapp: record RAppPerformanceReport — no state transition
        Rapp-->>Container: status=recorded
    end

    alt non-critical fault
        Container->>R1: POST /rapp-mgmt/instances/{id}/fault (severity=minor, description)
        R1->>Rapp: (proxied) ReportFault
        Rapp->>Rapp: record RAppFaultReport — no state transition
        Rapp-->>Container: status=recorded, instanceState=RUNNING
    else critical fault
        Container->>R1: POST /rapp-mgmt/instances/{id}/fault (severity=critical, description)
        R1->>Rapp: (proxied) ReportFault
        Rapp->>Rapp: record RAppFaultReport
        Rapp->>Rapp: state: RUNNING -> FAULTED (CRASH)
        Rapp->>DME: DELETE /dme/production-capabilities (producer_id=oauthClientId)
        Rapp->>SME: DELETE each published service API, then the provider registration
        Note over Rapp,SME: best-effort — an unreachable DME or SME never blocks CRASH.<br/>The credential is kept, the instance can still recover.
        Rapp-->>Container: status=recorded, instanceState=FAULTED

        Container->>R1: POST /rapp-mgmt/instances/{id}/recover
        R1->>Rapp: (proxied) Recover
        Rapp->>Rapp: state: FAULTED -> DEPLOYING (RECOVER)
        Rapp-->>Container: state=DEPLOYING
        Container->>R1: GET /bootstrap, re-register DME types if a producer
        Container->>Rapp: POST /instances/{id}/bootstrap-complete
        Rapp->>Onb: GET /packages/{packageId}/onboarding-status
        Onb-->>Rapp: smeDeclarations
        Rapp->>SME: register providers and service APIs (apfId=oauthClientId)
        Rapp->>Rapp: state: DEPLOYING -> RUNNING (BOOTSTRAP_OK)
    end
```

## Upgrade: commit or rollback

```mermaid
sequenceDiagram
    actor Operator
    participant Rapp as rApp Management SMOS

    Note over Operator,Rapp: old instance RUNNING on package P1, P2 is the target package
    Operator->>Rapp: POST /instances/{oldId}/upgrade (newPackageId=P2)
    Rapp->>Rapp: old: RUNNING -> UPGRADING (START_UPGRADE)
    Rapp->>Rapp: insert replacement RAppInstance, packageId=P2, state=DEPLOYING
    Rapp->>Rapp: old.pending_upgrade_instance_id = replacement id
    Note over Rapp: the replacement gets no oauth_client_id, no NFO deployment,<br/>no usage/start and no copy of configuration, autonomyMode or<br/>regionScope (OPEN_ITEMS OI-1-upgrade-identity).<br/>upgradeTimeoutSeconds (default 300) is stored on the old row,<br/>the caller reports the outcome.
    Rapp-->>Operator: newInstanceId, oldInstanceState=UPGRADING

    alt replacement bootstrapped (succeeded=true)
        Operator->>Rapp: POST /instances/{oldId}/upgrade/resolve?succeeded=true
        Rapp->>Rapp: replacement: DEPLOYING -> RUNNING (BOOTSTRAP_OK)
        Rapp->>Rapp: old: UPGRADING -> UNDEPLOYED (UPGRADE_COMMIT), credential revoked
        Rapp->>Rapp: old row deleted
        Note over Rapp: no DME or SME deregistration and no usage/stop<br/>for the old instance on commit
        Rapp-->>Operator: instanceId=replacement, state=RUNNING, packageId=P2
    else replacement failed or timed out (succeeded=false)
        Operator->>Rapp: POST /instances/{oldId}/upgrade/resolve?succeeded=false
        Rapp->>Rapp: replacement: DEPLOYING -> FAULTED (BOOTSTRAP_FAILED)
        Rapp->>Rapp: replacement row deleted
        Rapp->>Rapp: old: UPGRADING -> RUNNING (UPGRADE_ROLLBACK)
        Rapp->>Rapp: old.pending_upgrade_instance_id = NULL
        Rapp-->>Operator: instanceId=old, state=RUNNING, packageId=P1
    end
```

## Reconfigure, terminate, delete

```mermaid
sequenceDiagram
    actor Operator
    participant Rapp as rApp Management SMOS
    participant DME as DME
    participant SME as SME
    participant Onb as Onboarding SMOS

    Operator->>Rapp: PUT /instances/{id}/config (JSON body)
    Rapp->>Rapp: configuration = body, whole object replaced, any state
    Rapp-->>Operator: status=updated
    Note over Rapp: no state transition and nothing pushed to the container.<br/>The rApp reads it with GET /instances/{id}/config.

    Operator->>Rapp: DELETE /instances/{id}
    Rapp-->>Operator: 409 RAPP_INSTANCE_NOT_UNDEPLOYED — state=RUNNING

    Operator->>Rapp: POST /instances/{id}/terminate
    Rapp->>Rapp: state: RUNNING -> UNDEPLOYED (TERMINATE)
    Rapp->>DME: DELETE /dme/production-capabilities (producer_id=oauthClientId)
    Rapp->>SME: DELETE each published service API, then the provider registration
    Rapp->>Rapp: revoke credential — oauth_client_id = NULL
    Rapp->>Rapp: commit
    Rapp->>Onb: POST /packages/{packageId}/usage/{registrationId}/stop
    Onb->>Onb: stopped_at = now()
    Rapp-->>Operator: state=UNDEPLOYED
    Note over Rapp: no NFO call — the workload behind workloadRef stays deployed

    Operator->>Rapp: DELETE /instances/{id}
    Rapp->>Rapp: state is UNDEPLOYED — delete fault reports,<br/>performance reports, then the instance row
    Rapp-->>Operator: 204
```

**Key decisions this flow depends on:**
- Only `severity == "critical"` drives a state transition. `report_fault` records every report regardless of severity, and the history is readable at `GET /instances/{id}/faults` (and `/performance` for metrics, newest first).
- `CRASH` and `TERMINATE` deregister the instance's DME production capabilities and SME service APIs keyed by its `oauth_client_id` (HISTORY.md OI-1-producer-reconsideration). Both are best-effort. `TERMINATE` runs them before revoking the credential, because revocation clears the id they key on. `CRASH` keeps the credential so `RECOVER` can reuse the same identity.
- `RECOVER` (`FAULTED -> DEPLOYING`) is fired by `POST /instances/{id}/recover` (HISTORY.md OI-2-recover). It re-enters where `CreateInstance` does: the container re-bootstraps and calls `bootstrap-complete`, which re-registers the package's SME declarations. There is no lightweight path straight back to `RUNNING`, matching AI/ML Workflow's retraining re-entry (call flow 02).
- An upgrade is two rows in choreography (`rapp-mgmt/app/upgrade.py`, LLD section 6), not one row changing package in place. Auto-rollback needs no manual intervention, and the old instance keeps its package, credential and identity throughout a failed attempt.
- `upgradeTimeoutSeconds` (default 300 s, HISTORY.md OI-1-upgrade-timeout) is stored on the instance. No timer enforces it: the outcome arrives through `POST /instances/{id}/upgrade/resolve?succeeded=…`.
- On commit, the old row passes through `UNDEPLOYED` with its credential revoked and is then deleted, so no version history remains (OPEN_ITEMS OI-1-sa-rollback). The replacement carries no `oauth_client_id`, so it has no SME/DME identity, and producer reconsideration does not run on `UPGRADE_COMMIT` (OPEN_ITEMS OI-1-upgrade-identity).
- `PUT /instances/{id}/config` replaces `configuration` wholesale in any state, with no schema check and no FSM event. `autonomyMode` and `regionScope` are not part of it: they are fixed at `CreateInstance` (HISTORY.md OI-6.3).
- `TerminateInstance` stops the package usage registration after its own commit, which is what lets Onboarding's deprime and cascade-delete guards pass (call flow 06, HISTORY.md OI-2-usage-registration). An instance created without a registration skips the call.
- Undeploy and delete are separate operations: `terminate` keeps the row in `UNDEPLOYED`, and `DELETE /instances/{id}` is legal only from `UNDEPLOYED` (409 `RAPP_INSTANCE_NOT_UNDEPLOYED` otherwise, 404 `RAPP_INSTANCE_NOT_FOUND` for an unknown id). Delete removes the instance's fault and performance reports explicitly, in addition to their `ON DELETE CASCADE` foreign keys.
