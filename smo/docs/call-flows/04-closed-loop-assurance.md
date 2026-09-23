# Call Flow: Closed-Loop Assurance — Monitor → Decide → Remediate → Escalate

Stitches together SO/SA SMOS LLD sections 1-2 — the dispatch table (`so-smos/app/dispatch.py`)
and SA SMOS's honestly-incomplete `RemedialAction` dispatch.

```mermaid
sequenceDiagram
    actor Operator
    participant SO as SO SMOS
    participant SA as SA SMOS
    participant RanA as RAN Analytics SMOS
    participant NFOAM as RAN NF OAM SMOS
    participant NFO as NFO SMOS

    Operator->>SO: SubmitServiceOrder(scope, steps: [CONFIG->RAN_NF_OAM, DEPLOY->NFO])
    loop sequential, fail-fast (SO SMOS LLD section 1.1)
        SO->>NFOAM: dispatch_config(step)
        NFOAM-->>SO: COMPLETED
        SO->>NFO: dispatch_deploy(step)
        NFO-->>SO: COMPLETED
    end
    SO-->>Operator: orderId, steps=[COMPLETED, COMPLETED]

    SA->>SA: RegisterAssuranceMonitor(targetOrderId, thresholds)
    loop periodic, aligned with RAN Analytics' subscription cadence
        RanA-->>SA: MDAFReport (or MLMF PerformanceReport, if model-scoped)
        SA->>SA: EvaluateThresholds() — compare against requirementThresholds
    end

    alt breach detected
        SA->>SA: ExecuteRemedialAction(actionType=CONFIG_CHANGE)
        SA->>NFOAM: WriteConfigurationChanges (per SO/SA SMOS LLD section 2.1's dispatch)
        NFOAM-->>SA: COMPLETED
        SA->>SA: outcome = RESOLVED
    else actionType == SCALE
        SA->>SA: outcome = ESCALATED (inherits NFO's own Phase 1 stub status)
    else actionType == RECONNECT
        SA->>SO: GET /orders/{targetOrderId} — resolve the DEPLOY step's nfDeploymentId
        SO-->>SA: steps[] including the completed DEPLOY result
        SA->>NFO: POST /deployments/{nfDeploymentId}/heal
        NFO-->>SA: 200
        SA->>SA: outcome = RESOLVED (or ESCALATED if no order/DEPLOY step resolves)
    else actionType == ROLLBACK
        SA->>Operator: 501 ROLLBACK_HISTORY_UNAVAILABLE — rApp Management deletes the<br/>previous RAppInstance row on a successful upgrade, so no version<br/>history survives to roll back to — not a semantics question anymore
    end

    alt still unresolved
        SA->>Operator: EscalateToOperator(reason)
    end
```

**Key decisions this flow depends on:**
- SO SMOS never rolls back completed steps on a later failure — Phase 1 has no compensating-transaction mechanism, matching every other Phase-1-thin limitation in this framework (stated explicitly, not silently assumed).
- `RECONNECT` resolves its target the same way every other remedial action does — through the `AssuranceMonitor`'s `targetOrderId`, read back from SO SMOS's own order record — rather than needing a new resource-reference field on the monitor itself.
- `ROLLBACK` stays unsupported, but now for a concrete, checked reason (`ROLLBACK_HISTORY_UNAVAILABLE`) rather than a vague "ambiguous meaning" refusal: rApp Management's own upgrade machinery (`rapp-mgmt/app/upgrade.py`) deletes the prior `RAppInstance` row on a successful commit, so there is no version history anywhere in this build to roll back to — a rApp Management gap, not an SA SMOS design question.
- A coordination-group-scoped `AssuranceMonitor` (via `targetCoordinationGroupId`) would route through AI/ML Workflow's `should_trigger_group_retrain` instead of a single `ServiceOrder` — see call flow 02. A coordination-group-scoped `RECONNECT`/`ROLLBACK` isn't resolved by this pass either — only the `targetOrderId` path is.
