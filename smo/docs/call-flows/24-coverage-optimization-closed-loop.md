# Call Flow: Coverage Optimization rApp — Coverage PM → Joint plan → Safety → Tilt / power → Verification → KPI check

The Coverage Optimization rApp (`samples/coverage-optimization-rapp/`; Wave 10.3 in
`docs/ROADMAP.md`) is the third reference rApp. It tunes a cluster
of cells' digital tilt and transmit power, jointly, from O1 coverage PM.
Like call flows 22 and 23, it uses the R1 control plane only; there is no
A1, Near-RT RIC, xApp or E2.

The diagram follows one pass of the closed loop for an AUTONOMOUS instance.
Training (on history in which tilt and power varied), validation,
emulation, certification and deployment come before it; they are the call
flow 02 / 17 lifecycle.

```mermaid
sequenceDiagram
    participant NF as gNB (O1 adaptor)
    participant OAM as RAN NF OAM
    participant DME as DME
    participant CCO as Coverage rApp
    participant ES as EnergySaving rApp
    participant MRO as Mobility rApp
    participant AIMGF as AIMgF (MLIF)
    participant IS as Intent Service
    participant SA as SA SMOS (O1-CM handler)

    NF->>OAM: PM report COVERAGE_PERFORMANCE, hourly, per cell (MR totals, weak RSRP, overshoot, pilot pollution, overlap per neighbour, CM snapshot)
    OAM->>DME: POST /dme/data-jobs/{job}/records
    Note over CCO: POST /instances/{id}/evaluate, its X-Correlation-ID is the execution id
    CCO->>DME: sdk.data.get_dataset(COVERAGE_PERFORMANCE, INFERENCE), the cluster's latest windows
    alt a change set is under observation
        CCO->>CCO: engine.kpi_check, cluster objective now vs before the change
        CCO->>DME: if worse, POST /dme/actions (previous tilt / power per cell, REVERT KPI_DEGRADED, correlationId)
        CCO->>OAM: read back, cells STEADY, audit REVERTED or CONFIRMED
    else no change set open
        CCO->>OAM: GET /managed-entities/{me}/config, live digitalTilt and configuredMaxTxPower per cell, NRCellDU / CES state
        CCO->>OAM: sdk.data.query_cell_guards() and GET /alarms, protected cells and critical alarms
        CCO->>ES: GET /energy-saving-rapp/instances/{id}/cells, SLEEP / PRE_SLEEP and last wake
        CCO->>MRO: GET /mobility-optimization-rapp/instances/{id}/relations, relations OBSERVING
        CCO->>CCO: engine guards and bounds, the moves each cell may make
        CCO->>AIMGF: POST /models/{id}/inference-jobs, runtime must be ACTIVE
        CCO->>CCO: CoverageModel.optimise, joint search over the cluster, at most 2 cells, one step each
        CCO->>AIMGF: POST /inference-jobs/{id}/resolve (inferenceOutputs), AIMLInferenceReport

        rect rgb(240, 248, 255)
        Note over CCO,SA: the change set, one dispatch per pass, one expectation per cell
        CCO->>IS: POST /autonomy-dispatches (CommonBeamformingFunction.digitalTilt or NRSectorCarrier.configuredMaxTxPower)
        alt SHADOW
            IS-->>CCO: SHADOWED, recommendation only
        else ASSIST
            IS-->>CCO: AWAITING_SCOPE, the operator resolves or rejects, the rApp reconciles later
        else AUTONOMOUS
            IS->>SA: POST /o1-cm-handler/intents (intentId)
            SA->>DME: POST /dme/actions (actionId = uuid5(intent, expectation))
            DME->>OAM: POST /config-jobs (CommonBeamformingFunction=cell or NRSectorCarrier=cell)
            OAM->>NF: edit-config, retried on timeout, alarm when exhausted
            SA->>IS: POST /intent-reports
            IS-->>CCO: dispatchId, DISPATCHED, intentId
        end
        end

        CCO->>OAM: GET /managed-entities/{me}/config?managed_function_ref=..., read-after-write per cell
        alt verified
            CCO->>CCO: cells OBSERVING, the change set and its objective kept for the KPI check
        else VERIFY_FAILED / ACTION_FAILED
            CCO->>DME: POST /dme/actions (previous setting, own actionId), rollback, read back, re-sent once
        end
    end
```

**Key decisions this flow depends on:**

- **Tilt plus transmit power** (D10.3-1).
  `CommonBeamformingFunction.digitalTilt` is in tenths of a degree, with
  positive meaning downtilt. `NRSectorCarrier.configuredMaxTxPower` is in
  dBm in this build. One knob moves per cell per change, and each write is
  read back.
- **Joint neighbour optimisation** (D10.3-2). The model has 12 learned
  sensitivities: three problem shares × own tilt, own power, neighbours'
  tilt and neighbours' power. Neighbour effects are weighted by the
  measured overlap. The optimiser scores every move set of up to two cells
  by the cluster objective, plus a cost per moved cell:
  - the objective is the sum of every share's excess over 5 %;
  - a move set must beat doing nothing by at least 0.75;
  - no cell's own excess may be predicted to grow by more than 0.5.

  Because neighbour terms are in the model, pollution is treated at its
  source.
- **Bounds and pacing** (D10.3-4a).
  - Tilt stays within baseline ± 4°, in 1° steps.
  - Power stays within baseline ± 3 dB, in 1 dB steps.
  - A cell changes at most once per 60 minutes.
  - A window needs at least 100 measurement reports.
- **KPI-verified revert of the whole change set** (D10.3-4b). While a set
  is OBSERVING nothing else moves. After 60 minutes of post-change PM the
  measured cluster objective is compared with its value before the change.
  If it is worse by more than 0.5, every cell in the set is reverted
  straight through DME, otherwise the set is CONFIRMED.
- **Coordination** (D10.3-4c). A cell is held in each of these cases:
  - it is asleep (O1 or EnergySaving SLEEP / PRE_SLEEP);
  - a neighbour is asleep;
  - it or a neighbour woke less than 30 minutes ago;
  - the Mobility rApp has one of its relations OBSERVING.

  Both rApps are read over R1, never written.
- **Protected cells** (D10.3-4d). EMERGENCY and incident-zone cells never
  move. An active critical alarm on the managed element holds every cell,
  because alarms carry no cell reference in this build. Every blocking
  guard is named in the decision's reason.
- **The audit trail** (`GET /instances/{id}/decisions`) has one row per
  cell per pass:
  Shares → Joint plan → Safety → Decision → Intent → Action → Verification
  → KPI → Rollback → Final state.
  The GUI's **Coverage** page shows the latest row per cell, the latest
  joint plan, and each cell's excess trend.
