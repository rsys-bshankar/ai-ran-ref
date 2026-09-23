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
    else actionType in {RECONNECT, ROLLBACK}
        SA->>Operator: 422 — ambiguous in this reference build (SO/SA SMOS LLD section 2.1),<br/>needs module-qualified variants before implementing
    end

    alt still unresolved
        SA->>Operator: EscalateToOperator(reason)
    end
```

**Key decisions this flow depends on:**
- SO SMOS never rolls back completed steps on a later failure — Phase 1 has no compensating-transaction mechanism, matching every other Phase-1-thin limitation in this framework (stated explicitly, not silently assumed).
- `RECONNECT` and `ROLLBACK` are deliberately **not** force-resolved to a single meaning — the reference build raises a clear error rather than silently picking one of several plausible targets (rApp version rollback vs. model version rollback).
- A coordination-group-scoped `AssuranceMonitor` (via `targetCoordinationGroupId`) would route through AI/ML Workflow's `should_trigger_group_retrain` instead of a single `ServiceOrder` — see call flow 02.
