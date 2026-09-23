# Call Flow: SO SMOS Multi-Step Order — INFRA → TRAINING → DEPLOY

Stitches together SO/SA SMOS LLD section 1's full dispatch table (`so-smos/app/dispatch.py`)
across three of its five entries in one order, showing the fail-fast halt at whichever
step actually fails — not just the two-step CONFIG→NFO example call flow 04 already covers.

```mermaid
sequenceDiagram
    actor Operator
    participant SO as SO SMOS
    participant Focom as FOCOM SMOS
    participant AIML as AI/ML Workflow SMOS
    participant NFO as NFO SMOS

    Operator->>SO: SubmitServiceOrder(scope, steps: [INFRA->FOCOM, TRAINING->AI_ML_WORKFLOW, DEPLOY->NFO])
    SO->>SO: create ServiceOrder, steps=[...] (pre-execution, all implicitly PENDING)

    rect rgb(240, 255, 240)
    Note over SO,Focom: step 1 — INFRA
    SO->>Focom: dispatch_infra: POST /focom/resources/provision (spec)
    Focom-->>SO: 200 {resourceId, clusterId} -> step[0].status = COMPLETED
    end

    rect rgb(240, 255, 240)
    Note over SO,AIML: step 2 — TRAINING
    SO->>AIML: dispatch_training: POST /ai-ml-workflow/training-jobs (modelId, producerId, requiredData, validationCriteria)
    alt training job accepted
        AIML-->>SO: 201 {trainingJobId} -> step[1].status = COMPLETED
    else step supplied both modelId and modelCoordinationGroupId, or neither
        AIML-->>SO: 422 COORDINATION_GROUP_MISMATCH -> DownstreamError -> step[1].status = FAILED, halted = true
        Note over SO,NFO: step 3 never attempted — stays PENDING (fail-fast, no compensation).<br/>Step 1's provisioned resource is NOT automatically deprovisioned.
    end
    end

    opt all steps succeeded
        rect rgb(240, 255, 240)
        Note over SO,NFO: step 3 — DEPLOY
        SO->>NFO: dispatch_deploy: POST /nfo/deployments (nfDeploymentDescriptorId, requiredResourceTypeId)
        NFO-->>SO: 202 {nfDeploymentId, state=RUNNING} -> step[2].status = COMPLETED
        end
    end

    SO-->>Operator: orderId, steps=[...]

    Operator->>SO: GET /orders/{orderId}
    SO-->>Operator: persisted steps, current status per step

    opt operator wants to abandon a halted order
        Operator->>SO: POST /orders/{orderId}/cancel
        SO->>SO: every step still PENDING -> CANCELLED (COMPLETED/FAILED steps untouched)
        SO-->>Operator: updated steps
    end
```

**Key decisions this flow depends on:**
- Fail-fast with **no compensation**: if `TRAINING` fails after `INFRA` already succeeded, the provisioned FOCOM resource from step 1 is never automatically torn down — SO/SA SMOS LLD section 1.1 explicitly chose sequential fail-fast over a saga/compensating-transaction pattern for Phase 1. Cleanup after a partial failure is an operator (or SA SMOS remedial-action) responsibility, not something `execute_order` does itself.
- `CancelOrder` only ever touches steps still `PENDING` — a `COMPLETED` step's real-world effect (e.g. the FOCOM resource from step 1) is untouched by cancellation; cancelling the *order* is not the same as rolling back what already happened.
- Every dispatcher (`dispatch_infra`, `dispatch_training`, `dispatch_deploy`, plus `dispatch_config`/`dispatch_policy` shown in call flows 04/10) shares the same `_ensure_ok` status-code check — a downstream 4xx/5xx raises `DownstreamError`, which `execute_order`'s `except Exception` catches uniformly. This is what makes fail-fast real rather than cosmetic (see `smo/README.md`'s "Real bugs this pass found" — this exact check was originally missing).
