# Call Flow: EnergySaving rApp — PM → Prediction → Safety → O1 Action → Verification → Rollback

The EnergySaving rApp (`samples/energy-saving-rapp/`; Wave 10.1 in `docs/STANDARDS.md`) is
the first reference rApp built on the platform. It uses
O1 PM data only and the R1 control plane only; there is no A1, Near-RT RIC,
xApp or E2.

The diagram follows one pass of the closed loop for an AUTONOMOUS instance.
Training, validation, emulation, certification and deployment come before
it; they are the call flow 02 / 17 lifecycle, driven by the rApp through
the SDK. In the diagram:
* the rApp is a regular R1 consumer and reaches every module through R1
  Termination via the AI Runtime SDK;
* `RAN NF OAM` is both the PM source and the O1 executor;
* `SA SMOS` is the generic O1-CM intent handler (call flow 09).

```mermaid
sequenceDiagram
    participant NF as gNB-DU (O1 adaptor)
    participant OAM as RAN NF OAM
    participant DME as DME
    participant ES as EnergySaving rApp
    participant AIMGF as AIMgF (MLIF)
    participant MDAF as MDAF
    participant IS as Intent Service
    participant SA as SA SMOS (O1-CM handler)

    NF->>OAM: PM report (PRB_UTILIZATION per cell, 5-min samples)
    OAM->>DME: POST /dme/data-jobs/{job}/records — every job on RAN.PMCounters.PRB_UTILIZATION
    Note over ES: POST /instances/{id}/evaluate — one pass, its X-Correlation-ID is the execution id
    ES->>DME: sdk.data.get_dataset(PRB_UTILIZATION, INFERENCE) — per-cell series
    ES->>OAM: sdk.data.query_cell_guards() and GET /alarms — guards, critical / coverage alarms
    ES->>AIMGF: POST /models/{id}/inference-jobs — runtime must be ACTIVE
    ES->>ES: EnergyModel.infer per cell — futurePrb, recommendedState, confidence
    ES->>AIMGF: POST /inference-jobs/{id}/resolve (inferenceOutputs) — AIMLInferenceReport
    ES->>MDAF: sdk.analytics.get_prediction(cell, PRB_UTILIZATION) — an extra wake signal
    ES->>ES: engine.decide — sleep 5 % for 60 min, wake 15 %, hysteresis, HARD/MEDIUM/SOFT guards
    ES->>OAM: GET /managed-entities/{me}/config — idempotency, already in the wanted state means no action

    rect rgb(240, 248, 255)
    Note over ES,SA: LOCK (reduces service) — governed by the instance's autonomy mode
    ES->>IS: POST /autonomy-dispatches (RAN_SUBNETWORK, cells, NRCellDU.administrativeState IS_EQUAL_TO LOCKED)
    alt SHADOW
        IS-->>ES: SHADOWED — recommendation only, operator notified
    else ASSIST
        IS-->>ES: AWAITING_SCOPE — the operator resolves or rejects, the rApp reconciles later
    else AUTONOMOUS
        IS->>IS: create Intent — cells bounded by the instance regionScope
        IS->>SA: POST /o1-cm-handler/intents (intentId)
        SA->>DME: POST /dme/actions (actionId = uuid5(intent, expectation), changes per cell)
        DME->>OAM: POST /config-jobs (className, managedFunctionRef NRCellDU=cell)
        OAM->>OAM: pre-check, PROV service and vendor schema
        OAM->>NF: edit-config, retried at +5, +10 and +20 s on timeout, alarm when exhausted
        NF-->>OAM: ok / rpc-error / timeout
        OAM-->>DME: job COMPLETED / PARTIAL_SUCCESS / FAILED
        SA->>IS: POST /intent-reports — FULFILLED or NOT_FULFILLED, actions in additionalFulfilmentInfo
        IS-->>ES: dispatchId, DISPATCHED, intentId
        ES->>IS: GET /intent-reports?intent_id — which DME action ran, with what status
    end
    end

    ES->>OAM: GET /managed-entities/{me}/config?managed_function_ref=NRCellDU=cell — read-after-write
    OAM->>NF: get-config
    alt verified LOCKED
        ES->>ES: cell SLEEP, audit EXECUTED
    else VERIFY_FAILED / PARTIAL_SUCCESS / ACTION_FAILED
        ES->>DME: POST /dme/actions (UNLOCKED, own actionId, correlationId) — rollback
        DME->>OAM: POST /config-jobs
        OAM->>NF: edit-config
        ES->>OAM: read back UNLOCKED, re-sent once if still wrong
        ES->>ES: cell SERVING, audit with the rollback trigger
    end

    rect rgb(240, 255, 240)
    Note over ES,NF: UNLOCK (wake) and operator override restore service, so they go straight to DME in every enforcing mode
    ES->>DME: POST /dme/actions (UNLOCKED, own actionId) — then verify, then re-send once on mismatch
    end
```

**Key decisions this flow depends on:**

- **Only PRE_SLEEP → SLEEP and SLEEP → SERVING write to O1** (decision D-3).
  SERVING / PRE_SLEEP / SLEEP are rApp-internal states. The 60-minute
  sustain is measured on the samples' own timestamps, so a decision never
  depends on how often the loop runs.
- **The actuator is configurable per instance** (D-2):
  `NRCellDU.administrativeState` LOCKED/UNLOCKED, verified on the same
  attribute; or `CESManagementFunction.energySavingControl`, verified on
  `energySavingState`.
- **Only a LOCK goes through the autonomy dispatch.** A LOCK reduces
  service, so it is the action the autonomy modes govern. A wake, a
  rollback or an operator override restores service and must not wait for
  an approval, so each goes straight to DME `/actions` in AUTONOMOUS and
  ASSIST. Each carries its own `actionId` (replays are IGNORED) and the
  execution's correlation id.
- **The region scope bounds a dispatch's cells; it never adds to them**
  (Intent Service `_scoped`). An AUTONOMOUS instance acts only on the cells
  it decided about, and only inside its pre-configured region.
- **Guards hold whatever the model's confidence.**
  - HARD: emergency cell, coverage-critical cell, last awake cell of its
    sector group, incident zone.
  - MEDIUM: a neighbour above 80 % PRB; an active critical alarm on the
    cell, on a neighbour it hands its traffic to, or on the managed element
    as a whole (an alarm that names no cell).
  - SOFT: unlocked less than 30 minutes ago.
  Guard data are RAN NF OAM cell attributes (D-5, W9-06). A sector group
  is evaluated in sequence within one pass, so two low cells can't both
  sleep.
- **Reliability.**
  - Timeouts: DME → RAN NF OAM 10 s; NETCONF 30 s.
  - Retries: a timed-out or unreachable NETCONF write is retried
    (attempt 1, then +5/+10/+20 s); an `<rpc-error>` is final.
  - Failure: exhausted retries raise an alarm on the ME; the rApp records
    `ACTION_FAILED`, rolls back, and leaves the AIMgF model and runtime
    lifecycle untouched.
- **The audit trail** (`GET /instances/{id}/decisions`) has one row per
  cell per pass:
  Prediction → Safety → Decision → Intent → Action → Verification →
  Rollback → Final state.
  It joins the platform's own records by id (dispatch, intent, DME action,
  RAN NF OAM job). The GUI's **Energy Saving** page shows the latest row
  per cell with its PRB trend.
