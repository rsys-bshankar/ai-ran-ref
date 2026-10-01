# Call Flow: Mobility Optimization rApp — HO PM → Classification → Prediction → Safety → CIO → Verification → KPI check

The Mobility Optimization rApp (`samples/mobility-optimization-rapp/`; Wave 10.2 in
`docs/ROADMAP.md`) is the second reference rApp. It tunes handover
per neighbour relation from O1 handover PM. Like the EnergySaving rApp (call
flow 22), it uses the R1 control plane only; there is no A1, Near-RT RIC, xApp
or E2.

The diagram follows one pass of the closed loop for an AUTONOMOUS instance.
Before it come training, validation, emulation, certification and
deployment (the call flow 02 / 17 lifecycle). Deployment also writes the
gNB's `DMROFunction` bounds through DME and reads them back.

```mermaid
sequenceDiagram
    participant NF as gNB (O1 adaptor)
    participant OAM as RAN NF OAM
    participant DME as DME
    participant MRO as Mobility rApp
    participant ES as EnergySaving rApp
    participant AIMGF as AIMgF (MLIF)
    participant IS as Intent Service
    participant SA as SA SMOS (O1-CM handler)

    NF->>OAM: PM report HO_PERFORMANCE, hourly, per relation (attempts, too late, too early, wrong cell, ping-pong)
    OAM->>DME: POST /dme/data-jobs/{job}/records with values and relation
    Note over MRO: POST /instances/{id}/evaluate, its X-Correlation-ID is the execution id
    MRO->>DME: sdk.data.get_dataset(HO_PERFORMANCE, INFERENCE), per-relation series
    MRO->>OAM: GET /managed-entities/{me}/config, current CIO and isHOAllowed per relation, target cell state
    MRO->>OAM: sdk.data.query_cell_guards(), EMERGENCY and incident-zone cells
    MRO->>ES: GET /energy-saving-rapp/instances/{id}/cells, SLEEP / PRE_SLEEP and last wake per cell
    MRO->>AIMGF: POST /models/{id}/inference-jobs, runtime must be ACTIVE
    MRO->>MRO: MobilityModel.infer per relation, dominant cause, futureRate, recommendation
    MRO->>AIMGF: POST /inference-jobs/{id}/resolve (inferenceOutputs), AIMLInferenceReport
    MRO->>MRO: engine.decide, KPI check of the last change, then guards, then a bounded 2 dB step

    rect rgb(240, 248, 255)
    Note over MRO,SA: RAISE_CIO / LOWER_CIO, one dispatch per pass, one expectation per relation
    MRO->>IS: POST /autonomy-dispatches (NRCellRelation.cellIndividualOffset per relation)
    alt SHADOW
        IS-->>MRO: SHADOWED, recommendation only
    else ASSIST
        IS-->>MRO: AWAITING_SCOPE, the operator resolves or rejects, the rApp reconciles later
    else AUTONOMOUS
        IS->>SA: POST /o1-cm-handler/intents (intentId)
        SA->>DME: POST /dme/actions (actionId = uuid5(intent, expectation))
        DME->>OAM: POST /config-jobs (NRCellRelation=source-target)
        OAM->>NF: edit-config, retried on timeout, alarm when exhausted
        SA->>IS: POST /intent-reports
        IS-->>MRO: dispatchId, DISPATCHED, intentId
    end
    end

    MRO->>OAM: GET /managed-entities/{me}/config?managed_function_ref=NRCellRelation=x, read-after-write
    alt verified, all six QOffsetRange entries equal the new CIO
        MRO->>MRO: relation OBSERVING, audit EXECUTED
    else VERIFY_FAILED / ACTION_FAILED
        MRO->>DME: POST /dme/actions (previous CIO, own actionId), rollback, read back, re-sent once
        MRO->>MRO: relation STEADY, audit with the rollback trigger
    end

    rect rgb(255, 245, 238)
    Note over MRO,NF: one hour later, the KPI check. A worse failure rate reverts the change straight through DME
    MRO->>DME: POST /dme/actions (REVERT KPI_DEGRADED, previous CIO, correlationId)
    MRO->>OAM: read back, relation STEADY, audit REVERTED
    end
```

**Key decisions this flow depends on:**

- **The actuator is the per-relation CIO, bounded by DMRO** (D10.2-1).
  `NRCellRelation.cellIndividualOffset` is a TS 28.541 QOffsetRange list.
  The rApp writes all six entries with the same value and reads all six
  back. At deploy it writes `DMROFunction` bounds of −6/+6 dB with a
  60-minute minimum time between changes, so the gNB's own MRO stays
  inside the same envelope.
- **Classified MRO plus regression** (D10.2-2). The dominant failure class
  sets the direction:
  - too late: +2 dB;
  - too early or ping-pong: −2 dB;
  - wrong cell: −1 dB.

  A persistence-anchored regression predicts the next hour's failure rate.
  The controller acts at 5 % or more, holds between 2 and 5 %, and
  otherwise reports the relation healthy. A one-hour spike straight after a
  healthy hour is predicted to partly revert, so it stays in the hold zone.
- **Bounds and pacing** (D10.2-4a).
  - CIO stays within the baseline ± 6 dB (`AT_BOUND` beyond it).
  - Steps are at most 2 dB.
  - A relation changes at most once per 60 minutes.
  - A window needs at least 50 handover attempts.
- **KPI-verified revert** (D10.2-4b). A changed relation is OBSERVING until
  60 minutes of post-change PM exist. If its failure rate rose by more than
  0.5 points the change is reverted, otherwise it is CONFIRMED. A revert
  restores the earlier state, so it goes straight to DME in every enforcing
  mode, like the EnergySaving rApp's wake.
- **Coordination with EnergySaving** (D10.2-4c). No change aims at a target
  cell that is:
  - asleep on O1 (NRCellDU LOCKED or CES energy saving);
  - in the EnergySaving rApp's SLEEP or PRE_SLEEP state;
  - less than 30 minutes past a wake.

  The rApp reads the EnergySaving rApp's published cell states over R1. It
  never writes them.
- **Respect the network's own limits** (D10.2-4d). These relations are left
  alone:
  - a relation with `isHOAllowed=false`;
  - one whose source or target is an EMERGENCY cell;
  - one whose source or target is in an incident zone.

  Every guard that blocks is recorded in the decision's reason.
- **The CIO is shared with the Traffic Steering rApp** (call flow 25,
  D10.4-1). When the instance is given `trafficSteeringInstanceId`, a
  relation that rApp is observing a CIO step on is held (`MLB_OBSERVING`).
  The two rApps keep the CIO inside the same baseline ± 6 dB envelope.
- **The audit trail** (`GET /instances/{id}/decisions`) has one row per
  relation per pass:
  Prediction → Safety → Decision → Intent → Action → Verification → KPI →
  Rollback → Final state.
  The GUI's **Mobility** page shows the latest row per relation with its
  failure-rate trend.
