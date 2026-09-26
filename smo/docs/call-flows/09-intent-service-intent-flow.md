# Call Flow: Intent Registration → Fulfilment Reporting → Admin-State Control

Stitches together Intent Service (formerly Policy Mgmt) LLD sections 1-3: `QueryIntent` and
`UpdateIntentAdminState` close operations v1.3 never had (`intentAdminState` existed with
nothing that could change it), and `DeregisterIntentHandlingFunction` restores
register/deregister symmetry `RegisterIntentHandlingFunction` alone left broken.

```mermaid
sequenceDiagram
    actor SO as SO SMOS (framework-internal RMIH)
    participant R1 as R1 Termination
    participant Policy as Intent Service
    participant SME as SME
    actor RMIO as Intent-owning rApp (RMIO)

    SO->>R1: POST /intent-service/intent-handling-functions (rmihId, smeServiceId, capabilities)
    R1->>Policy: (proxied) RegisterIntentHandlingFunction
    Policy->>Policy: is_framework_internal_identity(rmihId)?
    alt caller is an ordinary rApp
        Policy-->>SO: 409 SERVICE_NAME_CONFLICT — external callers may never hold an rmihId (D-SEC-POLICY-1)
    else caller is a framework-internal SMO module
        Policy->>Policy: create IntentHandlingFunction
        Policy-->>SO: rmihId
    end

    RMIO->>R1: POST /intent-service/intents (expectations, priority, rmioId)
    R1->>Policy: (proxied) CreateIntent
    Policy->>Policy: create Intent, intentAdminState=ACTIVATED (default)
    Policy-->>RMIO: intentId

    Note over Policy,SO: RMIH discovers relevant Intents (Phase 1: elided — no<br/>subscription/notification wired from CreateIntent to an RMIH)

    loop RMIH's own fulfilment cycle
        SO->>R1: POST /intent-service/intent-reports (intentId, fulfilmentReport, conflictReports?)
        R1->>Policy: (proxied) PublishIntentReport
        Policy->>Policy: persist IntentReport, last_updated_time=now()
        Policy-->>SO: reportId
    end

    RMIO->>R1: GET /intent-service/intents/{id}
    R1->>Policy: (proxied) QueryIntent
    Policy-->>RMIO: intentAdminState, priority, ...

    RMIO->>R1: PATCH /intent-service/intents/{id}/admin-state (newState=DEACTIVATED, requesterId=rmioId)
    R1->>Policy: (proxied) UpdateIntentAdminState
    Policy->>Policy: check requesterId == intent.rmio_id
    alt requester is not the intent's own creator
        Policy-->>RMIO: 409 SERVICE_NAME_CONFLICT — only the creating RMIO may change admin state
    else authorized
        Policy->>Policy: intentAdminState = DEACTIVATED
        Policy-->>RMIO: updated Intent
    end

    SO->>R1: DELETE /intent-service/intent-handling-functions/{rmihId}
    R1->>Policy: (proxied) DeregisterIntentHandlingFunction
    Policy->>Policy: delete IntentHandlingFunction
```

**Key decisions this flow depends on:**
- `RegisterIntentHandlingFunction` is framework-internal only (D-SEC-POLICY-1) — `is_framework_internal_identity()` rejects any ordinary rApp attempting to register as an RMIH; only SO SMOS / SA SMOS are legitimate callers in this build.
- `UpdateIntentAdminState` is RMIO-only, checked against the `Intent`'s own `rmioId` at creation time — an RMIH that fulfils an Intent can report on it (`PublishIntentReport`) but cannot deactivate it; only the Intent's creator can.
- **Gap worth noting, not previously called out**: nothing in this build notifies an RMIH when a new `Intent` matching its `intentHandlingCapabilityList` is created — `CreateIntent` and `RegisterIntentHandlingFunction` both exist, but the matching/dispatch step between them (an RMIH discovering which Intents it should act on) is unbuilt. `intent_handling_scope` is modeled on `IntentHandlingFunction` but `RegisterIntentHandlingFunction`'s own request body never sets it, and nothing reads it — a column with no code path touching it at all.
- `intentExpectations` stays an opaque `JSON` blob throughout — TS 28.312's own expectation grammar isn't in this build's source corpus, so it's carried, never interpreted (matches the model's own inline comment).
