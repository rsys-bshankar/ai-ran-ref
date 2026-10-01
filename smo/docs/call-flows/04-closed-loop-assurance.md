# Call Flow: Closed-Loop Assurance — Monitor → Decide → Remediate → Escalate

An SO SMOS service order is monitored by SA SMOS (SO/SA SMOS LLD sections 1-2): a threshold
breach in a pushed report triggers a `RemedialAction`, and anything left unresolved is
escalated to the operator. `CONFIG_CHANGE` and `RECONNECT` are dispatched, `SCALE` escalates,
and `ROLLBACK` returns a rApp instance to its previous version (a rApp-instance-scoped monitor
only). The order itself runs through SO SMOS's dispatch table
(`so-smos/app/dispatch.py`).

**How this relates to call flow 03**: this is a sibling entry point into the same CM-write
mechanism, not an extension of or alternative to it. Flow 03 is the generic "how does a CM
change reach a real ME" walkthrough (Path A/B, dispatch, decomposition). This flow's
`SA->>NFOAM: WriteConfigurationChanges` line is one more caller of that route, triggered
automatically by a threshold breach instead of by an rApp's or operator's own decision.
The dispatch, decomposition and per-ME aggregation downstream of that call are identical —
see call flow 03.

```mermaid
sequenceDiagram
    actor Operator
    participant SO as SO SMOS
    participant SA as SA SMOS
    participant MDAF as MDAF
    participant AIMgF as AIMgF
    participant NFOAM as RAN NF OAM SMOS
    participant NFO as NFO SMOS
    participant Rapp as rApp Management SMOS

    Operator->>SO: SubmitServiceOrder(scope, steps: [CONFIG->RAN_NF_OAM, DEPLOY->NFO])
    loop sequential, fail-fast (SO SMOS LLD section 1.1)
        SO->>NFOAM: dispatch_config(step)
        NFOAM-->>SO: COMPLETED
        SO->>NFO: dispatch_deploy(step)
        NFO-->>SO: COMPLETED
    end
    SO-->>Operator: orderId, steps=[COMPLETED, COMPLETED]

    SA->>SA: RegisterAssuranceMonitor(targetOrderId, targetCoordinationGroupId<br/>or targetRappInstanceId — at most one, thresholds)
    loop periodic, aligned with each subscription's own report cadence
        alt RAN-behavior scoped
            MDAF-->>SA: best-effort push: MDAFReport (call flow 08 — RAN Analytics<br/>only registers the producer — MDAF owns report storage/push since Wave 1)
        else model-scoped
            AIMgF-->>SA: best-effort push: MLMF PerformanceReport (call flow 02/13 —<br/>AIMgF pushes directly, distinct domain from MDAF's RAN-behavior analytics)
        end
        SA->>SA: EvaluateThresholds() — compare against requirementThresholds
    end

    alt breach detected
        SA->>SA: ExecuteRemedialAction(actionType=CONFIG_CHANGE)
        SA->>NFOAM: WriteConfigurationChanges (per SO/SA SMOS LLD section 2.1's dispatch)
        NFOAM-->>SA: COMPLETED
        SA->>SA: outcome = RESOLVED
    else actionType == SCALE
        SA->>SA: outcome = ESCALATED (inherits NFO's own Phase 1 stub status)
    else actionType == RECONNECT, rApp-instance-scoped
        SA->>Rapp: GET /instances/{targetRappInstanceId}/versions — the current instance's workloadRef
        SA->>NFO: POST /deployments/{workloadRef}/heal
        SA->>SA: outcome = RESOLVED (or ESCALATED if nothing resolves)
    else actionType == RECONNECT, order-scoped
        SA->>SO: GET /orders/{targetOrderId} — resolve the DEPLOY step's nfDeploymentId
        SO-->>SA: steps[] including the completed DEPLOY result
        SA->>NFO: POST /deployments/{nfDeploymentId}/heal
        NFO-->>SA: 200
        SA->>SA: outcome = RESOLVED (or ESCALATED if no order/DEPLOY step resolves)
    else actionType == ROLLBACK, rApp-instance-scoped
        SA->>Rapp: POST /instances/{targetRappInstanceId}/rollback (call flow 07)
        alt rollback started
            Rapp-->>SA: newInstanceId, fromPackageId, toPackageId
            SA->>SA: outcome = RESOLVED, result = the rollback
        else refused (nothing to roll back, not RUNNING, package gone)
            Rapp-->>SA: 404 or 409 problem
            SA->>SA: outcome = ESCALATED, detail = rApp Management's reason
        end
    else actionType == ROLLBACK, any other monitor
        SA->>Operator: 409 ROLLBACK_HISTORY_UNAVAILABLE — only rApp Management keeps<br/>a version history, NFO keeps none for a bare NF deployment
    end

    alt still unresolved
        SA->>Operator: EscalateToOperator(reason)
    end
```

**Key decisions this flow depends on:**
- SO SMOS never rolls back completed steps on a later failure — Phase 1 has no compensating-transaction mechanism (stated explicitly, not silently assumed).
- `RECONNECT` resolves its target through the `AssuranceMonitor`'s `targetOrderId`, read back from SO SMOS's own order record (the DEPLOY step's `nfDeploymentId`), and heals that deployment through NFO — rather than needing a new resource-reference field on the monitor itself.
- `ROLLBACK` needs a target with a version history, and only rApp Management keeps one (HISTORY.md OI-1-sa-rollback). A monitor scoped to a rApp instance (`targetRappInstanceId`) dispatches rApp Management's `POST /instances/{id}/rollback`, an upgrade back to the previous package and configuration. An order-scoped or unscoped monitor is refused with 409 `ROLLBACK_HISTORY_UNAVAILABLE`.
- `RESOLVED` for a rollback means the rollback upgrade has started, not that it is committed. The replacement is resolved like any upgrade (call flow 07), and a failed or timed-out rollback leaves the current version running. A refusal from rApp Management is recorded as `ESCALATED`, with its reason in `detail`, rather than failing the request.
- A rApp-instance-scoped monitor keeps the instance id it was registered with. An upgrade replaces the instance row, so rApp Management resolves a superseded id through its version history, and both `ROLLBACK` and `RECONNECT` reach the current instance.
- A coordination-group-scoped `AssuranceMonitor` (`targetCoordinationGroupId`) bypasses the actionType branches entirely: it watches model performance, not an NF deployment, so its remedial action is always a group retrain through AIMgF's `POST /training-jobs` (`modelCoordinationGroupId`) — see call flow 02.
- The actor pushing reports into SA SMOS is whichever service owns them — MDAF for RAN-behavior analytics, AIMgF for model performance (MLMF). RAN Analytics only registers the producer capability (call flow 08); it never holds or pushes reports.
