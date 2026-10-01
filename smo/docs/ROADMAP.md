# SMO Roadmap — Waves 4–10.4

**Status: all waves 4–10.4 are done.** Each wave landed as one squash-merged
PR. This file holds the frozen decisions, the wave dependency graph, the
work items with their evidence, the standards compliance matrices and the
runtime realization. The architecture itself is in
[ARCHITECTURE.md](ARCHITECTURE.md); how each wave was reviewed and closed is
in [HISTORY.md](../HISTORY.md).

Source tags: `[W49]` = SMO_Waves_4to9.docx (pre-roadmap artifact checklist);
`[W10]` = SMO_Wave_10.docx (raw Wave 10 proposal); `[W10C]` =
SMO_Wave_10_Consolidated.docx (Wave 10 v1.0, canonical over `[W10]`).

## Contents

- [Frozen decisions](#frozen-decisions)
- [Dependency graph](#dependency-graph)
- [Waves](#waves): [0](#wave-0) · [4](#wave-4) · [5](#wave-5) · [6](#wave-6) · [7](#wave-7) · [8](#wave-8) · [9](#wave-9) · [10.1](#wave-101) · [10.2](#wave-102) · [10.3](#wave-103) · [10.4](#wave-104)
- [Backlog](#backlog)
- [Standards compliance](#standards-compliance): [TS 28.105](#ts-28105) · [TS 28.104](#ts-28104) · [TS 28.312](#ts-28312)
- [Runtime realization](#runtime-realization)

## Frozen decisions

| # | Topic | Decision |
|---|-------|----------|
| D-1 | Intent → O1 enactment | **Generic platform O1-CM Intent handler (RMIH)** in SA SMOS. It translates CM-shaped intent expectations into DME `/actions` → RAN NF OAM for *any* rApp, and posts IntentReports (W8-07). |
| D-1b | ASSIST semantics | The operator can **approve** (resolve, with scope) **or reject**. The dispatch stays `AWAITING_SCOPE` until one of the two; `/reject` → `REJECTED` (W8-08). |
| D-2 | O1 actuator | **Both, configurable per rApp instance**: `NRCellDU.administrativeState` (LOCKED/UNLOCKED) **or** `CESManagementFunction.energySavingControl` (TO_BE_ENERGY_SAVING/TO_BE_NOT_ENERGY_SAVING). `administrativeState` is on NRCellDU, where TS 28.541 places it. |
| D-3 | 3-state model | **rApp-internal** `SERVING / PRE_SLEEP / SLEEP`. PRE_SLEEP is the 60-min sustained-low-PRB window. Only PRE_SLEEP→SLEEP produces an O1 write. |
| D-4 | APIs | **Existing DME types/DataJobs + `/actions`**, plus thin SDK wrappers named as in the docs (`get_dataset`, `start_training`, `store_model`, `get_prediction`, `execute_action`). |
| D-5 | Guard data | **RAN NF OAM ManagedEntity attributes**: cell classification (emergency, coverage-critical, sector group / last sector, incident zone) and neighbour list, readable by any rApp (W9-06). |
| D-6 | Model | **Threshold + linear regression** (next-hour PRB), stored as a plain serialized artifact. LSTM is backlog. |
| D-7 | Emulation input | Synthetic `PRB_UTILIZATION_SIM` DME type fed by a sample producer. |
| D-8 | 10.2–10.4 | Designed only after 10.1 exits; each gets its own frozen design decisions (below). |
| D-9 | Waves 4–6 depth | **Full spec compliance at REST level.** Every IOC, attribute, enum, operation and notification of TS 28.105 / 28.104 / 28.312 as REST resources/fields in the existing services, with spec names. The one recorded deviation is **addressing**: flat REST, no DN containment tree. FL/RL and MLUpdateFunction included. |
| P-1 | PR granularity | **One PR per wave.** Each PR: push → CI → fix until green → squash-merge → next wave. |
| P-2 | Merge method | **Squash merge.** |

Resolved without a decision:
- **Test-case IDs.** `[W10C]` TC01–TC33 is canonical. The extra `[W10]` assertions (NETCONF timeout ⇒ alarm + no lifecycle corruption; PRB=3% sleep trigger) are folded into TC16/TC17/TC08.
- **Retry.** "Max 3 retries" = immediate, then +5/+10/+20 s (4 attempts). Notifications are retried 0 times, best-effort.
- **Lifecycle states** in `[W49]`/`[W10]` are subsets of the real FSM, not a gap.

## Dependency graph

```
Wave-4 (TS 28.105) ─┬───────────────► Wave-7 (Runtime) ──┐
Wave-5 (TS 28.104) ─┤                                    ├─► Wave-8 (Autonomy) ──► Wave-10.1 (Energy Saving)
Wave-6 (TS 28.312) ─┴────────────────────────────────────┘                          │
Wave-9 (Multi-vendor O1) ── independent; the 10.1 O1 path must not regress it       └─► 10.2 (Mobility) → 10.3 (Coverage) → 10.4 (Traffic Steering)
```

Wave 10 depends on Waves 4, 5, 7 and 8, on Wave 6 through the Intent-routed
AUTONOMOUS/ASSIST path (D-1), and on Wave 9 for the D-5 guard data and the
vendor-mode registry. Each of 10.2–10.4 started after the previous exit.

## Waves

### Wave 0

| ID | Work item | Done when / evidence |
|---|---|---|
| W0-01 | Freeze decisions D-1…D-9 | ✅ [Frozen decisions](#frozen-decisions) |
| W0-02 | Dependency graph + spec-traceability index (`[W49]` "Expected Deliverables") | ✅ [Dependency graph](#dependency-graph); per-standard matrices in [Standards compliance](#standards-compliance) |

### Wave 4

TS 28.105 alignment (AIMgF / MLMR). Result: 20/20 IOCs as REST resources,
125/126 attributes compliant; see [TS 28.105](#ts-28105).

| ID | Work item | Done when / evidence |
|---|---|---|
| W4-01 | TS 28.105 compliance matrix (every IOC/datatype/operation → entity/field/route) | ✅ [TS 28.105](#ts-28105) covers every IOC in the corpus |
| W4-02 | MLMR repository-ownership review: MLMR owns identity/artifact/version, AIMgF owns lifecycle | ✅ [MLMR ownership review](#mlmr-ownership-review-w4-02): no duplicated fields |
| W4-03 | Map `[W49]`'s `InferenceRuntime` | ✅ [InferenceRuntime mapping](#inferenceruntime-mapping-w4-03): no new entity |
| W4-04 | Every TS 28.105 IOC at REST level (D-9), spec names/enums + notifications | ✅ `aimgf/app/nrm.py`, MLMR `/ml-models`, `/ml-model-repositories`; every row Compliant or addressing-only deviation |
| W4-05 | Exit | ✅ Matrix reviewed; remaining gap in HISTORY.md §7; tests green |

### Wave 5

TS 28.104 alignment (MDAF). Result: 48/48 rows compliant; see
[TS 28.104](#ts-28104).

| ID | Work item | Done when / evidence |
|---|---|---|
| W5-01 | TS 28.104 mapping matrix | ✅ [TS 28.104](#ts-28104) |
| W5-02 | Every TS 28.104 IOC/datatype at REST level (D-9); AnalyticsReport / PredictionReport / DriftReport as typed report kinds | ✅ `mdaf/app/mda.py`; `reportKind`, `GET /mda-reports?report_kind=` |
| W5-03 | `TRAFFIC_FORECAST` / TrafficTrendReport via `sdk.analytics` | ✅ A PREDICTIONS_PM_DATA report; `sdk.analytics.get_prediction(cell, pm_name)` |
| W5-04 | DriftReport → AIMgF retrain signal | ✅ DRIFT report naming `mLModelRef` → that model's MLMF subscriptions |
| W5-05 | Exit | ✅ Matrix reviewed; tests green |

### Wave 6

TS 28.312 alignment (Intent Service). Result: 83/91 rows compliant, 8 partial
value datatypes; strict validation, every caller migrated; see
[TS 28.312](#ts-28312).

| ID | Work item | Done when / evidence |
|---|---|---|
| W6-01 | TS 28.312 mapping matrix | ✅ [TS 28.312](#ts-28312) |
| W6-02 | Every TS 28.312 IOC/datatype at REST level (D-9): structured IntentExpectation + the five expectation families, fulfilment/conflict/feasibility reports | ✅ Schema-validated expectations (`intent-service/app/ts28312.py`, `ts28312_families.py`) |
| W6-03 | Energy-saving expectation template | ✅ `sdk.intent.energy_saving_expectation(...)` |
| W6-04 | IntentReport fulfilment linked to downstream actions | ✅ `IntentFulfilmentReport.additionalFulfilmentInfo` with DME action refs; written by the W8-07 handler |
| W6-05 | Exit | ✅ Matrix reviewed; tests green |

### Wave 7

Runtime realization (MLTF / MLVF / MLEF / MLIF); see
[Runtime realization](#runtime-realization).

| ID | Work item | Done when / evidence |
|---|---|---|
| W7-01 | Runtime realization gap analysis | ✅ [Gap analysis](#gap-analysis-w7-01) |
| W7-02 | Deployment model per role: descriptor → deployment → lifecycle → scale/heal/terminate | ✅ [Deployment model](#deployment-model-per-role-w7-02); call flow 17 |
| W7-03 | Per-mode runtime profiles (cpu/memory/gpu) from the manifest → NFO descriptor | ✅ `workloadTemplate.resources`; `aimgf/tests/test_runtime.py` |
| W7-04 | Stage timeouts: training 30 min, validation 15 min, emulation 30 min, inference 5 s | ✅ Configurable; clean FAILED, no lifecycle corruption; tests per expiry |
| W7-05 | Exit | ✅ Gap analysis reviewed; tests green |

### Wave 8

Autonomy modes (AUTONOMOUS / ASSIST / SHADOW). W8-01..06 were closed by
PR #138 before the roadmap: `RAppInstance.autonomyMode` / `regionScope`,
`POST /intent-service/autonomy-dispatches`, `AWAITING_SCOPE` → `/resolve`,
`SHADOWED`, best-effort notification in all modes, call flows 01/02/03/09.

| ID | Work item | Done when / evidence |
|---|---|---|
| W8-01..06 | Platform autonomy model | ✅ PR #138 (above) |
| W8-07 | Generic O1-CM Intent handler (D-1) | ✅ `sa-smos/app/o1cm.py` (`/sa-smos/o1-cm-handler/registration`, `/intents`, `/enactments`); `test_cross_service.py::test_o1_cm_intent_handler_enacts_an_intent_through_dme_to_the_o1_adaptor` |
| W8-08 | ASSIST reject (D-1b) | ✅ `POST /intent-service/autonomy-dispatches/{id}/reject` (409 unless `AWAITING_SCOPE`); GUI Reject button; BFF pins `rejectedBy` |
| W8-09 | Call flow 09 + OPEN_ITEMS updated | ✅ Call flow 09; `OPEN_ITEMS.md` §6.3 Wave 8 follow-ons |

### Wave 9

Multi-vendor O1 framework; see
[ARCHITECTURE.md#o1-vendor-onboarding](ARCHITECTURE.md#o1-vendor-onboarding).

| ID | Work item | Done when / evidence |
|---|---|---|
| W9-01 | Capability Registry: per-vendor services, conformance mode, schema ref | ✅ `ran-nf-oam/app/vendors.py` `PUT/GET/DELETE /vendor-capabilities/{vendor}`; FM/PM/SWM/PROV gate (409 `O1_SERVICE_NOT_SUPPORTED`) |
| W9-02 | `CMSchemaCache` bound to the registry; CM writes validated against the vendor schema | ✅ `POST/GET /cm-schemas`, bundled TS 28.541 descriptor (`scripts/ingest_cm_schema.py`); 422 `SCHEMA_VALIDATION_FAILED` surfaced through DME |
| W9-03 | Vendor onboarding flow (discover → load → declare) | ✅ `POST /vendor-onboarding`; call flow 21; `test_cross_service.py::test_vendor_onboarding_gates_o1_writes_by_capability_and_schema` |
| W9-04 | `O1_NETCONF` / `O1_RESTCONF` vendor modes | ✅ `supportedVendorModes` gates endpoint registration; `GET /ran-nf-oam/capabilities` |
| W9-06 | Cell guard attributes (D-5) | ✅ `ManagedEntity.cell_guards`; `PUT/DELETE /managed-entities/{me}/cells/{cell}/guards`, `GET /cell-guards`; SDK `query_cell_guards` |
| W9-05 | Exit | ✅ Battery green; `ran-nf-oam/tests/test_vendors.py` |

### Wave 10.1

EnergySaving rApp, direct O1 closed loop (`samples/energy-saving-rapp/`,
call flow 22). Planes: R1 (control), O1 (management). Excludes A1, Near-RT
RIC, xApps, E2. Design decisions: D-2, D-3, D-4, D-5, D-6, D-7 above.

| ID | Work item | Done when / evidence |
|---|---|---|
| W10-01 | Package → `energy-saving-rapp.csar` (manifest, capabilities, model + four logic files) | ✅ Onboards → AVAILABLE with 4 execution modes, 3 autonomy modes, runtime profiles (TC01) |
| W10-02 | Energy model: threshold + linear regression for next-hour PRB → `{futurePrb, recommendedState, confidence}` (D-6) | ✅ Artifact stored in MLMR |
| W10-03 | SDK wrappers (D-4) | ✅ `sdk/tests/test_wave10_wrappers.py` |
| W10-04 | PRB utilization PM → RAN NF OAM → DME `PRB_UTILIZATION`, sample producer | ✅ Read via `sdk.data` only (TC02) |
| W10-05 | Synthetic `PRB_UTILIZATION_SIM` (D-7) | ✅ Consumed by MLEF |
| W10-06 | Consume MDAF prediction via `sdk.analytics` | ✅ rApp reads the MDAF prediction |
| W10-07 | Full AIMgF lifecycle REGISTERED → … → PROMOTED on MLTF/MLVF/MLEF | ✅ TC03–TC06 |
| W10-08 | MLIF deploy AIMgF → NFO → runtime ACTIVE (MLLF checks CERTIFIED) | ✅ TC07 |
| W10-09 | Inference via `POST /models/{id}/inference-jobs` | ✅ PRB 2–3 % → LOCK (TC08) |
| W10-10 | Pipeline: input → prediction → safety → decision → O1 execution → verification → audit | ✅ Each stage recorded |
| W10-11 | Sleep: PRB < 5 % for 60 min and all guards pass (D-3) | ✅ TC08 |
| W10-12 | Wake: predicted PRB > 15 %, neighbour PRB > 80 %, critical coverage alarm, or operator override | ✅ TC19–TC22 |
| W10-13 | Hysteresis: 5–15 % → NO_CHANGE | ✅ TC23 |
| W10-14 | Hard / medium / soft safety guards, independent of AI confidence (W9-06 data + `/alarms`) | ✅ TC24–TC26 |
| W10-15 | Operator override suppresses AI recommendations | ✅ TC21 |
| W10-16 | Action path AutonomyDispatch → Intent → O1-CM RMIH → DME → RAN NF OAM → adaptor; actuator per instance (D-2) | ✅ DmeActionRecord, cell LOCKED / ES active (TC09) |
| W10-17 | Mock O1 adaptor models `NRCellDU.administrativeState`, `CESManagementFunction.energySavingControl/energySavingState` | ✅ Read-back reflects writes |
| W10-18 | Idempotency: skip if already in state; `actionId` dedup → IGNORED | ✅ TC14, TC15, TC29 |
| W10-19 | Timeouts DME→OAM 10 s, NETCONF 30 s; retries immediate/+5/+10/+20 s; then `ACTION_FAILED` + alarm | ✅ TC16, TC17; `ran-nf-oam/tests/test_dispatch_reliability.py` |
| W10-20 | Read-after-write verification → `VERIFY_FAILED` on mismatch | ✅ TC10, TC18, TC33 |
| W10-21 | Rollback to UNLOCKED on VERIFY_FAILED / NETCONF_FAILED / PARTIAL_SUCCESS / neighbour congestion / coverage alarm | ✅ TC27, TC28 |
| W10-22 | Wave 8 autonomy modes applied via AutonomyDispatch | ✅ TC11–TC13 |
| W10-23 | Audit trace chain joined by correlation id | ✅ TC30 |
| W10-24 | GUI Energy Saving dashboard | ✅ `[W10C]` §19 operator acceptance |
| W10-25 | Carrier-grade tests: false wake-up, neighbour overload recovery, read-after-write mismatch | ✅ TC31–TC33 |
| W10-26 | Integration suite TC01–TC33 | ✅ `tests_integration/test_energy_saving_rapp.py` |
| W10-27 | DEMO_RUNBOOK §24, Demo 01–11 | ✅ `test_demo_runbook.py::test_energy_saving_demo_01_to_11_runs_end_to_end` |
| W10-28 | Call flow 22 | ✅ Renders on GitHub |
| W10-29 | Exit review against `[W10C]` §20 (15 checks) | ✅ See below |

**Exit:** all criteria met — see [HISTORY.md](../HISTORY.md) W10.1. Proven by
`tests_integration/test_energy_saving_rapp.py`,
`tests_integration/test_demo_runbook.py::test_energy_saving_demo_01_to_11_runs_end_to_end`,
`samples/energy-saving-rapp/tests`.

### Wave 10.2

Mobility Optimization rApp: handover failure → CIO
(`samples/mobility-optimization-rapp/`, call flow 23).

| # | Topic | Decision |
|---|-------|----------|
| D10.2-1 | O1 actuator | **CIO plus DMRO bounds.** The rApp writes per-relation `NRCellRelation.cellIndividualOffset` (TS 28.541: six `QOffsetRange` entries in dB, all written with the same value) and bounds the gNB's distributed MRO through `DMROFunction` (`maximumDeviationHoTriggerLow/High`, `minimumTimeBetweenHoTriggerChange`, `dmroControl`), so the two optimisers can't fight. |
| D10.2-2 | Algorithm | **Classified MRO plus regression.** Per relation and hourly window, PM counters split handover failures into too-late / too-early / wrong-cell plus ping-pongs (TS 38.300 §15.5.2, TS 28.552-style `MM.*` counters). A persistence-anchored regression predicts the next-hour failure rate. A bounded step controller acts: too-late raises CIO; too-early or ping-pong lowers it; wrong-cell lowers it by 1 dB. |
| D10.2-3 | Code structure | **A standalone sample** beside the EnergySaving rApp; 10.1 untouched. |
| D10.2-4 | Safety | (a) Bounds and pacing: baseline ± 6 dB, steps ≤ 2 dB, ≥ 60 min between changes on a relation, ≥ 50 handover attempts per window. (b) KPI-verified revert after a 60-min observation window. (c) EnergySaving coordination: never tune towards a SLEEP or PRE_SLEEP cell; hold for 30 min after a wake. (d) Skip `isHOAllowed=false` relations and emergency or incident-zone cells (W9-06). |

| ID | Work item | Done when / evidence |
|---|---|---|
| W10.2-01 | Package → `mobility-optimization-rapp.csar` | ✅ Onboards → AVAILABLE (MRO-01) |
| W10.2-02 | Model `MobilityRobustnessPredictor` + logic files; JSON artifact in MLMR | ✅ Trained/validated/emulated (MRO-03..05); RMSE ≈ 0.5, emulation direction accuracy 1.0 |
| W10.2-03 | Multi-counter PM (`values`, `relation`) in RAN NF OAM `/pm-reports`; `HO_PERFORMANCE` + `HO_PERFORMANCE_SIM` | ✅ Read via `sdk.data.get_dataset` (MRO-02) |
| W10.2-04 | Mock adaptor: `NRCellRelation` (`cellIndividualOffset`, `isHOAllowed`), `DMROFunction` | ✅ Read-back (MRO-08/10) |
| W10.2-05 | MRO engine: classification, thresholds (act ≥ 5 %, hold 2–5 %), step controller, pacing, guards | ✅ `samples/mobility-optimization-rapp/tests/test_engine.py` |
| W10.2-06 | Actuation via AutonomyDispatch → Intent → O1-CM handler → DME → RAN NF OAM; reverts direct to DME; DMRO bounds at deploy | ✅ MRO-08/09/10 |
| W10.2-07 | KPI-verified revert, else CONFIRMED | ✅ MRO-15 (revert), MRO-12 (confirm) |
| W10.2-08 | EnergySaving coordination over R1 | ✅ MRO-17 (`TARGET_ASLEEP`, `TARGET_RECENTLY_WOKEN`) |
| W10.2-09 | Audit, dashboard, GUI **Mobility** page, BFF rules, R1 route, compose service | ✅ MRO-20; `gui/src/pages/Mobility.tsx` |
| W10.2-10 | Integration tests, runbook §25, call flow 23, exit review | ✅ See below |

**Exit:** all criteria met — see [HISTORY.md](../HISTORY.md) W10.2. Proven by
`tests_integration/test_mobility_optimization_rapp.py` (MRO-01..MRO-20),
`tests_integration/test_demo_runbook.py::test_mobility_optimization_demo_00_to_11_runs_end_to_end`,
`samples/mobility-optimization-rapp/tests`.

### Wave 10.3

Coverage Optimization rApp: weak coverage / overshoot / pilot pollution →
tilt + power (`samples/coverage-optimization-rapp/`, call flow 24).

| # | Topic | Decision |
|---|-------|----------|
| D10.3-1 | O1 actuator | **Tilt plus transmit power.** Per cell, `CommonBeamformingFunction.digitalTilt` (TS 28.541, tenths of a degree, positive = downtilt) and `NRSectorCarrier.configuredMaxTxPower`. One knob per cell per change. |
| D10.3-2 | Algorithm | **Joint neighbour optimisation.** Per cell and hourly window, measurement-report PM gives three problem shares (weak coverage, overshoot, pilot pollution; the TS 28.541 CCO classes) plus overlap with each neighbour. A learned linear sensitivity model predicts how each share responds to the cell's own tilt/power step and, weighted by overlap, to its neighbours'. A joint search picks the move set (≤ 2 cells per pass) that most reduces the cluster objective: the sum of every share's excess over 5 %, plus a cost per changed cell. |
| D10.3-3 | Code structure | **A standalone sample**; 10.1 and 10.2 untouched. |
| D10.3-4 | Safety | (a) Tilt within baseline ± 4° in 1° steps; power within baseline ± 3 dB in 1 dB steps; ≥ 60 min between changes on a cell; ≥ 100 measurement reports per window. (b) A change set is observed 60 min; if the cluster objective worsened, every cell in it is reverted. (c) Never change a cell while it or a neighbour is asleep, pre-sleep or < 30 min past a wake, or while the Mobility rApp has one of its relations OBSERVING. (d) Skip EMERGENCY, incident-zone and critical-alarm cells. |

| ID | Work item | Done when / evidence |
|---|---|---|
| W10.3-01 | Package → `coverage-optimization-rapp.csar` | ✅ Onboards → AVAILABLE (CCO-01) |
| W10.3-02 | Model `CoverageSensitivityModel` (12 learned sensitivities) + joint optimiser + logic files | ✅ CCO-03..05; sensitivities recover the propagation model (RMSE ≈ 0.1); emulation move accuracy 1.0 |
| W10.3-03 | `COVERAGE_PERFORMANCE` PM (`MR.*`, CM snapshot) in DME; `COVERAGE_PERFORMANCE_SIM`; sample propagation model | ✅ Read via `sdk.data.get_dataset` (CCO-02); live PM follows live O1 settings |
| W10.3-04 | Mock adaptor: `CommonBeamformingFunction`, `NRSectorCarrier` | ✅ Read-back (CCO-09) |
| W10.3-05 | Engine: guards, bounds, pacing, sample minimum → allowed moves | ✅ `samples/coverage-optimization-rapp/tests/test_engine.py` |
| W10.3-06 | Actuation: one AutonomyDispatch per pass, one expectation per changed cell; reverts direct to DME | ✅ CCO-08/09/14 |
| W10.3-07 | KPI-verified revert of the whole change set, else CONFIRMED | ✅ CCO-16 (revert), CCO-11 (confirm) |
| W10.3-08 | Coordination with EnergySaving and Mobility over R1 | ✅ CCO-18 (`CELL_ASLEEP`, `NEIGHBOUR_ASLEEP`, `RECENTLY_WOKEN`, `MRO_OBSERVING`) |
| W10.3-09 | Audit, dashboard, GUI **Coverage** page, BFF rules, R1 route, compose service | ✅ CCO-20; `gui/src/pages/Coverage.tsx` |
| W10.3-10 | Integration tests, runbook §26, call flow 24, exit review | ✅ See below |

**Exit:** all criteria met — see [HISTORY.md](../HISTORY.md) W10.3. Proven by
`tests_integration/test_coverage_optimization_rapp.py` (CCO-01..CCO-20),
`tests_integration/test_demo_runbook.py::test_coverage_optimization_demo_00_to_11_runs_end_to_end`,
`samples/coverage-optimization-rapp/tests`.

### Wave 10.4

Traffic Steering rApp: congestion → idle reselection priority + connected
CIO bias (`samples/traffic-steering-rapp/`, call flow 25).

| # | Topic | Decision |
|---|-------|----------|
| D10.4-1 | O1 actuator | **Idle and connected.** Idle: `NRFreqRelation.cellReselectionPriority` towards the target's layer (TS 28.541, 0–7). Connected: `NRCellRelation.cellIndividualOffset` on the congested cell → target relation, the knob the Mobility rApp tunes. **CIO arbitration:** both rApps keep CIO within baseline ± 6 dB (the DMRO bounds); neither changes a relation the other has under observation, and the Mobility rApp has the reciprocal guard. Inter-frequency targets are steered in idle mode first; connected CIO is used intra-frequency or once the idle knob is at its bound. Relations with `isMLBAllowed=false` or `isHOAllowed=false` are never biased. |
| D10.4-2 | Algorithm | **Congestion score + regression, pairwise.** Score = 0.5·PRB utilisation + 0.3·connected-UE load + 0.2·throughput deficit (TS 28.552 `RRU.PrbTotDl`, `RRC.ConnMean`, `DRB.UEThpDl`). The next-hour forecast is persistence + last-hour trend + a learned hour-of-day profile. Forecast ≥ 70 → offload one step to the least-loaded eligible neighbour; 50–70 → hold; < 50 → release one step. The transfer per CIO dB and per priority step is learned from history. |
| D10.4-3 | Code structure | **A standalone sample.** The only change to an earlier rApp is the Mobility rApp's optional reciprocal CIO guard. |
| D10.4-4 | Safety | (a) CIO steps of 2 dB inside the shared envelope; priority within baseline ± 2 (and 0–7) in steps of 1; ≥ 60 min between changes on a cell; ≥ 10 PM samples per window. (b) After 60 min, revert if the target became congested, the source got worse, or (connected) the relation's HO failure rate rose by > 2 points. (c) Never steer towards a cell asleep, pre-sleep or < 30 min past a wake, nor from a sleeping cell; hold relations the Mobility rApp has OBSERVING and cells in a Coverage change set under observation. (d) Skip EMERGENCY / incident-zone cells, hold under a critical alarm, never push a target's post-transfer forecast above 55, never steer T → S within 6 h of S → T. |

| ID | Work item | Done when / evidence |
|---|---|---|
| W10.4-01 | Package → `traffic-steering-rapp.csar` | ✅ Onboards → AVAILABLE (TS-01) |
| W10.4-02 | Model `CongestionSteeringModel` + logic files | ✅ TS-03..05; learned transfer ≈ 0.03 / dB, ≈ 0.06 / step; emulation steering accuracy 1.0 |
| W10.4-03 | `LOAD_PERFORMANCE` PM in DME; `LOAD_PERFORMANCE_SIM` with hotspots; sample load model | ✅ Read via `sdk.data.get_dataset` (TS-02); live PM follows live steering |
| W10.4-04 | Mock adaptor: `NRFreqRelation` (`cellReselectionPriority`, `qOffsetFreq`), `NRCellRelation.isMLBAllowed` | ✅ Read-back (TS-08/11) |
| W10.4-05 | Engine: thresholds, hysteresis, target and knob choice, guards, bounds, pacing, anti-oscillation | ✅ `samples/traffic-steering-rapp/tests/test_engine.py` |
| W10.4-06 | Actuation: one AutonomyDispatch per pass, one expectation per change; reverts direct to DME | ✅ TS-08, TS-11 |
| W10.4-07 | KPI-verified revert, else CONFIRMED | ✅ TS-13 (`HO_FAILURES`), TS-15 (`TARGET_CONGESTED`), TS-10 (confirm) |
| W10.4-08 | Coordination with EnergySaving, Mobility (two-way CIO) and Coverage | ✅ TS-18 (`TARGET_ASLEEP`, `CELL_ASLEEP`, `MRO_OBSERVING`, `MLB_OBSERVING`); Coverage in unit tests |
| W10.4-09 | Audit, dashboard, GUI **Traffic Steering** page, BFF rules, R1 route, compose service | ✅ TS-20; `gui/src/pages/TrafficSteering.tsx` |
| W10.4-10 | Integration tests, runbook §27, call flow 25, exit review | ✅ See below |

**Exit:** all criteria met — see [HISTORY.md](../HISTORY.md) W10.4. Proven by
`tests_integration/test_traffic_steering_rapp.py` (TS-01..TS-20),
`tests_integration/test_demo_runbook.py::test_traffic_steering_demo_00_to_11_runs_end_to_end`,
`samples/traffic-steering-rapp/tests`,
`samples/mobility-optimization-rapp/tests` (reciprocal `MLB_OBSERVING` guard).

## Backlog

| ID | Item | Status |
|---|---|---|
| W10-B1 | Energy model LSTM variant (D-6): PRB → PRB for the next N windows | Not taken; recorded in HISTORY.md (W10.1) |
| W10-B2 | `CESManagementFunction.energySavingControl` as an alternative actuator (D-2) | Built in Wave 10.1: actuator selectable per instance (`actuator: ENERGY_SAVING_CONTROL`); covered in `test_energy_saving_rapp.py` |
| W10.3-00 | Coverage Optimization (RSRP → tilt) | Built as [Wave 10.3](#wave-103) |
| W10.4-00 | Traffic Steering (congestion → reselection bias) | Built as [Wave 10.4](#wave-104) |
| — | Coverage: per-cell, per-class thresholds from the TS 28.541 CCO parameter sets (instead of the fixed 5 % excess) | Not taken; recorded in HISTORY.md (W10.3) |
| — | NFO scaling with a target size (replicas / resources) | Open; see [Gap analysis](#gap-analysis-w7-01) |

## Standards compliance

All three matrices are generated from the spec YAML in `specs/5G_APIs/`, and
the generator checks that every attribute marked Compliant appears by name in
the implementing code. Attributes of one IOC that share a status and need no
note are listed together in one table row.

**Addressing is the one deviation that applies to every row.** DN-typed
attributes (`Dn`, `DnRo`, `DnListRo`) carry resource ids (UUIDs); resources
are flat REST collections, not a DN containment tree. Each resource is
returned as `{"id", "attributes"}`, mirroring the spec's `-Single` shape.
Containment is a `…Ref` attribute on the child ("containment as reference").

### Compliance limits (what stops "100%")

"Compliant" above means the spec YAML is realised as REST resources with spec
names and enums. It does not mean full conformance to the whole TS. Each
limit below is tracked in [`../OPEN_ITEMS.md`](../OPEN_ITEMS.md) §3.

| Standard | Module(s) | REST-level status | Limits to 100% |
|---|---|---|---|
| TS 28.104 | MDAF | 48/48 rows | `STREAMING` is recorded, not streamed (SA-MDA-5, no TS 28.532 streaming transport); `recommendationFilter`, `performanceThresholdInfo`, `analysisRequirements`, `thresholdMonitorRefList` stored not enforced; `areaScope` stored not matched; backing-model refs set by the caller; flat addressing |
| TS 28.312 | Intent Service | 83/91 rows, 8 partial | SA-INTENT-partial: 8 value datatypes without inner structure; flat addressing |
| TS 28.105 | AIMgF, MLMR, MLLF, runtime | 125/126 attributes | `ThresholdMonitors` (TS 28.623 containment, MLMF equivalent); FL/RL stored, no training engine; runtime scale has no target size; flat addressing |
| TS 29.482 | MLMR | `MLModel`, storages / profiles, `storeDiscReqs`, discovery at REST level | `accessReqs.location` not enforced; phase written at training start / success only; the `MLModel` `anyOf` and forward-compatible enums not honoured |
| TS 28.532 / 28.111 / 28.319 | RAN NF OAM | MSAC, `accessScope`, `PerceivedSeverity`, DN refs and file reporting closed | Streaming reporting (SA-RANOAM-8); DN containment tree (SA-RANOAM-4); MSAC guards CM writes only; Jex subset for `dataNodeSelector` |
| O2IMS | FOCOM | Inventory sites, fault, performance, artifacts / cluster / infrastructure / provisioning at REST level | `FILE` / `STREAM` performance reporting; no real cluster behind a ProvisioningRequest; flat addressing |
| TS 28.541 + WG10/WG5 | RAN NF OAM | Partial | SA-O1-4: WG10 O1NRM and WG5 classes not modelled |

RAN Analytics is not in this table: it realises no standard (it is a producer
registry, separate from MDAF).

Spec conformance is closed module by module (16 items): RAN NF OAM (5),
FOCOM (4), MLMR (5), Intent Service (1), O1 vendor models (1). Each closes in
its own PR with the matching `OPEN_ITEMS.md` and matrix rows updated.

### TS 28.105

AI/ML NRM, AIMgF and MLMR (`specs/5G_APIs/TS28105_AiMlNrm.yaml`).

| | Count |
|---|---|
| IOCs | 20, all REST resources |
| Attributes / children | 126 |
| Compliant | 125 |
| Deviation | 1: `MLTrainingFunction.ThresholdMonitors` |

- **Not a parallel model.** An MLTrainingRequest *is* a `TrainingJob`, an
  MLTestingRequest *is* a `ValidationJob`; both start through the same core
  (`_start_training` / `_start_validation` in `aimgf/app/main.py`), so the
  lifecycle gate, the operator approvals and the NFO execution runtimes
  apply to NRM-created runs. Every training run gets an MLTrainingProcess;
  every completion writes MLTrainingReport, MLTestingReport or
  AIMLInferenceReport (emulation and inference).
- **Behaviour.** `cancelRequest` / `suspendRequest` / `cancelProcess` /
  `suspendProcess` act on the run; `activationStatus` gates inference; an
  MLModelLoadingRequest brings the serving runtime up through
  RuntimeLifecycle (NFO); an MLUpdateRequest runs FINE_TUNING training per
  model and writes the MLUpdateReport once every run is terminal.
- **FL/RL.** `FLRequirement`, `RLRequirement`, `FLParticipationInfo`,
  `SupportedLearningTechnology`, `FLReportPerClient` are validated against
  the spec enums and stored; there is no distributed-training engine to
  consume them.
- **The deviation.** `ThresholdMonitors` is a containment of TS 28.623's
  `ThresholdMonitor`, not a TS 28.105 IOC. Model-performance threshold
  monitoring is AIMgF MLMF (`/aimgf/mlmf/subscriptions`, `guardKpiFloor`),
  recorded as the equivalent in HISTORY.md §7 (TS 28.105 item 7).

#### MLMR ownership review (W4-02)

| Concern | Owner | Finding |
|---|---|---|
| Model identity, version, artifacts | MLMR | `MLModel` (`mLModelId` = `model_id`, `mLModelVersion` = `version`), `ModelArtifact` |
| TS 28.105 writable MLModel attributes | MLMR | Columns on `aiml_model`, accepted on register and update |
| `mLTrainingType`, `aIMLInferenceReportRefList`, `usedByFunctionRefList` (read-only) | AIMgF | Joined into `GET /mlmr/ml-models/{id}` from `GET /aimgf/ml-models/{id}/nrm-refs` over R1; empty if AIMgF is unreachable |
| Repository container | MLMR | `MLModelRepository`; deleting it un-contains its models and groups (`ON DELETE SET NULL`) |
| Coordination groups | MLMR | `memberMLModelRefList` = `member_model_ids` (minItems 2) |
| Lifecycle state | AIMgF | MLMR stores no lifecycle |

#### InferenceRuntime mapping (W4-03)

`[W49]`'s `InferenceRuntime` is realised by two existing pieces:
**AIMLInferenceFunction** (the logical inference host: `activationStatus`,
`managedActivationScope`, loaded models) and **RuntimeLifecycle** (AIMgF
`runtime_lifecycle_state` + `nf_deployment_id`, the NFO-backed MLIF serving
deployment that MLModelLoadingProcess drives NOT_DEPLOYED → DEPLOYED →
ACTIVE). An inference names the model (runtime must be ACTIVE) and
optionally the function (must be ACTIVATED with the model loaded).

#### TS 28.105 matrix

| IOC | Owner | Resource |
|---|---|---|
| MLTrainingFunction | AIMgF | `/aimgf/ml-training-functions` |
| MLTrainingRequest | AIMgF | `/aimgf/ml-training-requests` (= TrainingJob) |
| MLTrainingProcess | AIMgF | `/aimgf/ml-training-processes` |
| MLTrainingReport | AIMgF | `/aimgf/ml-training-reports` |
| MLTestingFunction | AIMgF | `/aimgf/ml-testing-functions` |
| MLTestingRequest | AIMgF | `/aimgf/ml-testing-requests` (= ValidationJob) |
| MLTestingReport | AIMgF | `/aimgf/ml-testing-reports` |
| MLModelLoadingRequest | AIMgF | `/aimgf/ml-model-loading-requests` |
| MLModelLoadingPolicy | AIMgF | `/aimgf/ml-model-loading-policies` |
| MLModelLoadingProcess | AIMgF | `/aimgf/ml-model-loading-processes` |
| MLModel | MLMR | `/mlmr/ml-models/{id}` (+ `/mlmr/models`) |
| MLModelRepository | MLMR | `/mlmr/ml-model-repositories` |
| MLModelCoordinationGroup | MLMR | `/mlmr/ml-model-coordination-groups/{id}` (+ `/mlmr/coordination-groups`) |
| MLUpdateFunction | AIMgF | `/aimgf/ml-update-functions` |
| MLUpdateRequest | AIMgF | `/aimgf/ml-update-requests` |
| MLUpdateProcess | AIMgF | `/aimgf/ml-update-processes` |
| MLUpdateReport | AIMgF | `/aimgf/ml-update-reports` |
| AIMLInferenceFunction | AIMgF | `/aimgf/aiml-inference-functions` |
| AIMLInferenceReport | AIMgF | `/aimgf/aiml-inference-reports` |
| AIMLInferenceEmulationFunction | AIMgF | `/aimgf/aiml-inference-emulation-functions` |

| IOC | Attribute / child | Status | Note |
|---|---|---|---|
| MLTrainingFunction | `supportedLearningTechnology`, `fLParticipationInfo`, `mLKnowledge`, `mLTrainingType` | Compliant | |
| | `mLModelRepositoryRef` | Compliant (id-addressed) | |
| | `MLTrainingRequest` | Compliant (containment as reference) | Containment: `mLTrainingFunctionRef` on the request; list with `?ml_training_function_id=` |
| | `MLTrainingProcess` | Compliant (containment as reference) | Containment: reached through the request (`trainingRequestRef`) |
| | `MLTrainingReport` | Compliant (containment as reference) | Containment: `mLTrainingFunctionRef` on the report |
| | `ThresholdMonitors` | Deviation | Deviation (not modelled): TS 28.623 ThresholdMonitor containment. Threshold monitoring of a model is AIMgF MLMF (`/aimgf/mlmf/subscriptions`, guardKpiFloor), recorded as equivalent in HISTORY.md §7 item 7 |
| | `MLTestingRequest` | Compliant (containment as reference) | Containment: testing requests are contained by MLTestingFunction here (`mLTestingFunctionRef`). The spec allows either container |
| | `MLTestingReport` | Compliant (containment as reference) | Containment: as MLTestingRequest |
| MLTrainingRequest | `aIMLInferenceName`, `fLRequirement`, `candidateTrainingDataSource`, `trainingDataQualityScore`, `performanceRequirements`, `rLRequirement`, `cancelRequest`, `suspendRequest`, `trainingDataStatisticalProperties`, `distributedTrainingExpectation`, `mLKnowledgeName`, `mLTrainingType`, `expectedInferenceScope`, `clusteringInfo` | Compliant | |
| | `trainingRequestSource` | Compliant | Required on create (= producer) |
| | `requestStatus` | Compliant | = TrainingJob.status (spec enum; FAILED is this build's addition) |
| | `mLModelRef`, `mLModelCoordinationGroupRef` | Compliant (id-addressed) | |
| MLTrainingProcess | `priority`, `terminationConditions`, `cancelProcess`, `suspendProcess` | Compliant | |
| | `progressStatus` | Compliant | ProcessMonitor. Runtime write-back via POST …/progress |
| | `trainingRequestRef`, `participatingFLClientRefList`, `trainingReportRef`, `mLModelRef`, `mLModelCoordinationGroupRef` | Compliant (id-addressed) | |
| MLTrainingReport | `usedConsumerTrainingData`, `modelConfidenceIndication`, `modelPerformanceTraining`, `modelPerformanceValidation`, `dataRatioTrainingAndValidation`, `areNewTrainingDataUsed`, `fLReportPerClient` | Compliant | |
| | `trainingProcessRef`, `lastTrainingRef`, `mLModelGeneratedRef`, `mLModelCoordinationGroupGeneratedRef` | Compliant (id-addressed) | |
| MLTestingFunction | `mLModelRef` | Compliant (id-addressed) | |
| | `MLTestingRequest` | Compliant (containment as reference) | Containment: `mLTestingFunctionRef` |
| | `MLTestingReport` | Compliant (containment as reference) | Containment: `mLTestingFunctionRef` |
| MLTestingRequest | `requestStatus` | Compliant | Mapped from ValidationJob.status (COMPLETED/FAILED → FINISHED; outcome in MLTestingReport.mLTestingResult) |
| | `cancelRequest`, `suspendRequest` | Compliant | |
| | `mLModelRef`, `mLModelCoordinationGroupRef` | Compliant (id-addressed) | |
| MLTestingReport | `modelPerformanceTesting`, `mLTestingResult` | Compliant | |
| | `testingRequestRef` | Compliant (id-addressed) | |
| MLModelLoadingRequest | `requestStatus`, `cancelRequest`, `suspendRequest` | Compliant | |
| | `mLModelToLoadRef` | Compliant (id-addressed) | |
| MLModelLoadingPolicy | `aIMLInferenceName`, `policyForLoading` | Compliant | |
| | `mLModelRef` | Compliant (id-addressed) | |
| MLModelLoadingProcess | `progressStatus`, `cancelProcess`, `suspendProcess` | Compliant | |
| | `mLModelLoadingRequestRef`, `mLModelLoadingPolicyRef`, `loadedMLModelRef` | Compliant (id-addressed) | |
| MLModel | `mLModelId` | Compliant | = MLMR model_id |
| | `aIMLInferenceName`, `expectedRunTimeContext`, `trainingContext`, `runTimeContext`, `supportedPerformanceIndicators`, `mLCapabilitiesInfoList`, `inferenceScope` | Compliant | |
| | `mLModelVersion` | Compliant | = MLMR version |
| | `mLTrainingType` | Compliant | Read-only. Joined from AIMgF (type of the latest successful training) |
| | `retrainingEventsMonitorRef`, `sourceTrainedMLModelRef` | Compliant (id-addressed) | |
| | `aIMLInferenceReportRefList` | Compliant (id-addressed) | Read-only. Joined from AIMgF |
| | `usedByFunctionRefList` | Compliant (id-addressed) | Read-only. Joined from AIMgF (inference functions it is loaded on) |
| MLModelRepository | `MLModel` | Compliant (containment as reference) | Containment: `mLModelRepositoryRef` on MLModel; listed in the repository view |
| | `MLModelCoordinationGroup` | Compliant (containment as reference) | Containment: `mLModelRepositoryRef` on the group; listed in the repository view |
| MLModelCoordinationGroup | `memberMLModelRefList` | Compliant | = member_model_ids, minItems 2 enforced |
| MLUpdateFunction | `availMLCapabilityReport` | Compliant | |
| | `mLModelRef` | Compliant (id-addressed) | |
| | `MLUpdateRequest` | Compliant (containment as reference) | Containment: `mLUpdateFunctionRef` |
| | `MLUpdateProcess` | Compliant (containment as reference) | Containment: via the request |
| | `MLUpdateReport` | Compliant (containment as reference) | Containment: via the process |
| MLUpdateRequest | `performanceGainThreshold`, `newCapabilityVersionId`, `updateTimeDeadline`, `requestStatus`, `mLUpdateReportingPeriod`, `cancelRequest`, `suspendRequest` | Compliant | |
| | `mLUpdateProcessRef`, `mLModelRefList` | Compliant (id-addressed) | |
| MLUpdateProcess | `progressStatus`, `cancelProcess`, `suspendProcess` | Compliant | |
| | `mLModelRefList`, `mLUpdateRequestRefList`, `mLUpdateReportRef` | Compliant (id-addressed) | |
| MLUpdateReport | `updatedMLCapability` | Compliant | |
| | `mLModelRefList`, `mLUpdateProcessRef` | Compliant (id-addressed) | |
| AIMLInferenceFunction | `activationStatus` | Compliant | Enforced: DEACTIVATED refuses inference on the function |
| | `managedActivationScope` | Compliant | |
| | `usedByFunctionRefList` | Compliant (id-addressed) | Read-only. Derived from the `consumer_ref` of inference jobs |
| | `mLModelRefList` | Compliant (id-addressed) | Read-only. Set by MLModelLoadingProcess |
| | `AIMLInferenceReport` | Compliant (containment as reference) | Containment: `aIMLInferenceFunctionRef` |
| | `MLModelLoadingRequest` | Compliant (containment as reference) | Containment: `aIMLInferenceFunctionRef` |
| | `MLModelLoadingProcess` | Compliant (containment as reference) | Containment: `aIMLInferenceFunctionRef` |
| | `MLModelLoadingPolicy` | Compliant (containment as reference) | Containment: `aIMLInferenceFunctionRef` |
| AIMLInferenceReport | `inferenceOutputs`, `potentialImpactInfo` | Compliant | |
| | `mLModelRefList` | Compliant (id-addressed) | |
| AIMLInferenceEmulationFunction | `AIMLInferenceReport` | Compliant (containment as reference) | Containment: `aIMLInferenceEmulationFunctionRef` |

### TS 28.104

MDA NRM, MDAF (`specs/5G_APIs/TS28104_MdaNrm.yaml`, `TS28104_MdaReport.yaml`;
`mdaf/app/mda.py`). 48 rows, 48 compliant. Recorded deviations: addressing,
and the `STREAMING` reporting method's transport.

- **Request-driven on top of producer-push.** The producer-push surface
  (`POST /reports`, `/subscriptions`, ThresholdInfo notification) is
  unchanged. An MDARequest declares the outputs it wants, with per-IE
  `filterValue`, edge-triggered `threshold` (hysteresis) and `timeOut`
  filters, for a scope and a `startTime`/`stopTime` window. Every report,
  spec-shaped (`POST /mda-reports`) or legacy (`POST /reports`), is matched
  against all open requests and delivered per `reportingMethod`:
  `NOTIFICATION` (best-effort POST to `reportingTarget`), `FILE`
  (`GET /mda-reports/{id}/file` + `notifyFileReady`), `STREAMING` (recorded
  and retrievable, not streamed: there is no TS 28.532 `StreamingDataMnS`
  transport).
- **Typed outputs.** `mDAOutputList` is validated against the output type its
  `mDAType` selects (9 MDATypes have typed outputs); other types use
  MDAOutputEntry pairs, which the spec allows for all types.
- **Report kinds (W5-02)** `ANALYTICS` / `PREDICTION` / `DRIFT`, inferred
  from the MDAType or set with `reportKind`.

| IOC | Owner | Resource |
|---|---|---|
| MDAFunction | MDAF | `/mdaf/mda-functions` |
| MDARequest | MDAF | `/mdaf/mda-requests` |
| MDAReport | MDAF | `/mdaf/mda-reports` (+ `GET …/{id}/file`) |

| IOC | Attribute / child | Status | Note |
|---|---|---|---|
| MDAFunction | `supportedMDACapabilities`, `supportedMDADomain` | Compliant | |
| | `mLModelRefList` | Compliant | Set at create/replace (readOnly in the spec: this build has no auto-discovery of the backing models) |
| | `aIMLInferenceFunctionRefList` | Compliant | As mLModelRefList |
| | `MDARequest` | Compliant (containment as reference) | Containment: `mDAFunctionRef` on the request (capabilities enforced) |
| | `MDAReport` | Compliant (containment as reference) | Containment: `mDAFunctionRef` on the report |
| MDARequest | `requestedMDAOutputs` | Compliant | Matched per mDAType; mDAOutputIEFilters filterValue / threshold (edge-triggered, hysteresis) / timeOut enforced |
| | `reportingMethod` | Compliant | NOTIFICATION → POST to reportingTarget; FILE → `GET /mda-reports/{id}/file` + notifyFileReady; STREAMING → recorded + retrievable (no TS 28.532 streaming transport — see Deviations) |
| | `reportingTarget` | Compliant | |
| | `analyticsScope` | Compliant | managedEntitiesScope matched against the report scope; areaScope stored |
| | `startTime` | Compliant | Enforced: reports before startTime are not delivered |
| | `stopTime` | Compliant | Enforced |
| | `recommendationFilter` | Compliant | Stored and returned |
| | `performanceThresholdInfo` | Compliant | Stored and returned (TS 28.623 ThresholdInfo) |
| | `analysisRequirements` | Compliant | Stored and returned |
| | `thresholdMonitorRefList` | Compliant | Stored and returned (TS 28.623 ThresholdMonitor ids) |
| MDAReport | `mDAReportID` | Compliant | = report id |
| | `mDAOutputs` | Compliant | Typed per mDAType (9 typed outputs) or MDAOutputEntry pairs; validated |
| | `mDARequestRef` | Compliant | Set when the producer answers one request; `deliveredToRequestRefList` lists every request it satisfied |

**MDA report output datatypes**

| Datatypes | Status | Note |
|---|---|---|
| `ProjectionDuration`, `MDAOutputs`, `MDAOutputEntry`, `Recommended3GPPAction`, `RecommendedAction`, `RadioEnvironmentMap`, `CoverageCharacterization`, `MeasurementDataCorrelationRecommendation`, `PmPrediction`, `ThresholdAssessment`, `ManagementDataCollectionInfo` | Compliant | validated |
| `PagingOptimizationAnalysisOutput`, `MobilityPerformanceAnalysisOutput`, `CoverageProblemAnalysisOutput`, `TrainingDataAnalysisOutput`, `NFScalingDimensioningDataAnalysisOutput`, `PMDataOutput`, `FailurePredictionOutput`, `TrafficCongestionProblemAnalysisOutput`, `RETTPAnalyticsAnalysisOutput` | Compliant | validated (selected by mDAType) |
| `MDAType`, `ReportingMethod`, `MDADomain`, `ThresholdInfo`, `AnalyticsScopeType`, `MDAOutputPerMDAType`, `MDAOutputIEFilter`, `AnalyticsSchedule`, `AnalysisRequirement` | Compliant | closed enum / structure enforced |

### TS 28.312

Intent NRM, Intent Service (`specs/5G_APIs/TS28312_IntentNrm.yaml` and the
five `TS28312_*Expectation.yaml` family files). 91 rows: 83 compliant,
8 partial. Recorded deviation: addressing.

- **Strict.** `POST /intents` and `POST /autonomy-dispatches` accept only
  spec-valid intents: required `userLabel`, `intentExpectations` (≥ 1) and
  `intentReportControl` (with `observationPeriod`); each expectation needs an
  `expectationId`, an `expectationObject` and ≥ 1 target; closed enums
  throughout.
- **Expectation families.** `scripts/generate_ts28312_families.py` generates
  `intent-service/app/ts28312_families.py`: 34 specialised targets and 41
  specialised contexts. A specialised name must use its own condition and
  value range (for example `AveDLPrbLoad` is `IS_LESS_THAN` with an integer
  0..100); any other name is the generic ExpectationTarget/Context.
- **At creation:** every expectation object type needs a capability on the
  addressed RMIH; a purpose needing negotiation (FEASIBILITY_CHECK /
  EXPLORATION / FULFILMENT_WITH_NEGOTIATION) must be in the RMIH's
  `supportedNegotiationFunctionalities` when it declares any; target
  feasibility is checked against `supportedExpectationTargetInfoList` (a
  FEASIBILITYCHECK* intent is accepted with an INFEASIBLE report, a
  fulfilment intent with an infeasible target is rejected); a TARGET_CONFLICT
  is reported against other ACTIVATED intents on the same object instance;
  the initial IntentReport (NOT_FULFILLED / RECEIVED) becomes
  `intentReportReference`.
- **Reports** go to each `intentReportControl.reportRecipientAddress` whose
  `expectedReportTypes` they match. Deactivation reports SUSPENDED. A
  consumer answers a negotiation report with
  `POST /intents/{id}/negotiation-feedback`.

| IOC | Owner | Resource |
|---|---|---|
| Intent | Intent Service | `/intent-service/intents` |
| IntentReport | Intent Service | `/intent-service/intent-reports` |
| IntentHandlingFunction | Intent Service | `/intent-service/intent-handling-functions` |
| IntentUtilityFormula | Intent Service | `/intent-service/intent-utility-formulas` |

| IOC | Attribute / child | Status | Note |
|---|---|---|---|
| Intent | `userLabel`, `contextSelectivity`, `consumerSatisfactionIndexThreshold`, `expectationSelectivity`, `intentContexts`, `intentPriority`, `intentPreemptionCapability`, `implicitIntentIndex`, `guaranteePeriods`, `intentHandlingInfo`, `intentInterpretationAssistanceInfo` | Compliant | |
| | `intentExpectations` | Compliant | Strict: IntentExpectation or a family specialisation (Radio Network / Radio Service / 5GC / Edge / Network Maintenance); specialised targets/contexts checked against generated family constraints |
| | `intentMgmtPurpose` | Compliant | FEASIBILITYCHECK* → feasibility report; purposes needing negotiation require the RMIH's supportedNegotiationFunctionalities |
| | `intentAdminState` | Compliant | DEACTIVATED → report NOT_FULFILLED/SUSPENDED |
| | `intentReportControl` | Compliant | Required; drives report delivery (reportRecipientAddress × expectedReportTypes) |
| | `intentReportReference` | Compliant | Read-only: the latest IntentReport (initial RECEIVED report written at creation) |
| | `intentUtilityFormulaRef` | Compliant | Validated against IntentUtilityFormula |
| IntentReport | `intentFulfilmentReport`, `intentExplorationReport`, `intentUtilityReports`, `intentDecompositionReport` | Compliant | |
| | `intentConflictReports` | Compliant | Also computed at creation (TARGET_CONFLICT vs other ACTIVATED intents on the same objectInstance) |
| | `intentFeasibilityCheckReport` | Compliant | Also computed by Intent Service from the RMIH's IntentHandlingCapability |
| | `intentFulfilmentNegotiationReport` | Compliant | Consumer feedback via POST /intents/{id}/negotiation-feedback |
| | `lastUpdatedTime` | Compliant | Set on write / negotiation feedback |
| | `intentReference` | Compliant | = intent id |
| IntentHandlingFunction | `intentHandlingScope`, `supportedNegotiationFunctionalities`, `supportedUtilityList` | Compliant | |
| | `intentHandlingCapabilityList` | Compliant | Strict IntentHandlingCapability; drives object-type, target feasibility checks |
| | `Intent` | Compliant (containment as reference) | Containment: Intent.rmihId (ON DELETE CASCADE) |
| | `IntentReport` | Compliant (containment as reference) | Containment: via the Intent |
| | `IntentUtilityFormula` | Compliant (containment as reference) | Containment: referenced from Intent.intentUtilityFormulaRef |
| IntentUtilityFormula | `utilityFunctionId`, `utilityParameterList`, `utilityScale`, `utilityOffset` | Compliant | |

**Datatypes**

| Datatypes | Status |
|---|---|
| `IntentExpectation`, `ExpectationObject`, `Condition`, `Selectivity`, `IntentMgmtPurpose`, `FulfilmentStatus`, `NotFulfilledState`, `FulfilmentInfo`, `FulfilmentStatisticsInfo`, `Distribution`, `ExpectationVerb`, `ValueRangeType`, `IntentHandlingScope`, `NegotiationFunctionality`, `IntentHandlingInfo`, `ExpectationTarget`, `Context`, `IntentReportControl`, `ExpectedReportType`, `IntentFulfilmentReport`, `ExpectationFulfilmentResult`, `TargetFulfilmentResult`, `IntentConflictReport`, `IntentUtilityReport`, `IntentFeasibilityCheckReport`, `InFeasibleExpectationInfo`, `InFeasibleTargetInfo`, `IntentExplorationReport`, `ExpectationExplorationResult`, `TargetExplorationResult`, `IntentFulfilmentNegotiationReport`, `PossibleIntentOutcome`, `PossibleImpact`, `IntentFulfilmentNegotiationFeedback`, `ImplicitIntent`, `IntentHandlingCapability`, `SupportedExpectationTargetInfo`, `SupportedContextInfo`, `UtilityParameter`, `UtilityResult`, `UtilityDefinition`, `IntentDecompositionReport`, `IntentTraceabilityInfo`, `IntentInterpretationAssistanceInfo`, `DecompositionAssistingContext`, `SchedulingTimeContext` | Compliant |
| `Frequency`, `UEGroup`, `QoSId`, `CivicArea`, `CivicAddress`, `ReportingCondition`, `TimeCondition`, `TargetFulfilmentCondition` | Partial ¹ |

¹ Partial: accepted as a value; inner structure not enforced (family
constraints still apply where a family specialises it).

## Runtime realization

The four AI/ML execution roles (MLTF / MLVF / MLEF / MLIF) are ordinary NFO
deployments driven by AIMgF (Wave 7).

### Deployment model per role (W7-02)

| Role | Spec realisation | Started by | NFO descriptor (`workloadTemplate`) | Lifetime | Ended by |
|---|---|---|---|---|---|
| **MLTF** (training) | TrainingJob = MLTrainingRequest + MLTrainingProcess | `POST /training-jobs`, `POST /ml-training-requests`, group retrain, MLUpdateRequest | `{jobKind: TRAINING, jobId, resources}` | Transient: one descriptor + deployment per run | Completion, cancel, supersede, or timeout (`DELETE /nfo/deployments/{id}`) |
| **MLVF** (validation / testing) | ValidationJob = MLTestingRequest | `POST /validation-jobs`, `POST /ml-testing-requests` | `{jobKind: VALIDATION, jobId, resources}` | Transient | Completion, cancel, or timeout |
| **MLEF** (emulation) | EmulationJob on an AIMLInferenceEmulationFunction | `POST /emulation-jobs` | `{jobKind: EMULATION, jobId, resources}` | Transient | Completion or timeout |
| **MLIF** (inference) | RuntimeLifecycle + AIMLInferenceFunction | `POST /models/{id}/runtime/deploy`, or MLModelLoadingRequest / Policy | `{modelId, jobKind: INFERENCE, resources}` | Long-lived serving deployment | `POST /models/{id}/runtime/terminate` |

| MLIF RuntimeLifecycle step | NFO call |
|---|---|
| NOT_DEPLOYED → DEPLOYMENT_REQUESTED → DEPLOYED | CreateDescriptor + Instantiate |
| ACTIVATING → ACTIVE | none (AIMgF's own gate) |
| SCALING → ACTIVE | `POST /deployments/{id}/scale` |
| TERMINATING → TERMINATED | `DELETE /deployments/{id}` |

Each inference job references the serving deployment and never creates its
own. NFO `HEAL` (call flow 15) applies to all four roles.

### Runtime profiles and timeouts

**Profiles (W7-03).** A package's `manifest.yaml` declares `executionModes`
and `runtimeProfiles: {TRAINING|VALIDATION|EMULATION|INFERENCE: {cpu, memory,
gpu}}`, at the top level or under `rappManifest`. Onboarding accepts only
known modes listed in `executionModes` with non-negative cpu/gpu (else the
package goes to FAILED) and exposes the profiles on `onboarding-status`. An
AIMgF request may name `packageId` (that package's profile for the mode) or
give an explicit `runtimeProfile` (which wins); the chosen profile is stored
on the job or lifecycle row and sent to NFO as `workloadTemplate.resources`.

**Timeouts (W7-04).** Defaults (`[W10C]` §13):

| Stage | Default |
|---|---|
| Training | 30 min |
| Validation | 15 min |
| Emulation | 30 min |
| Inference | 5 s |

Override per deployment with `AIMGF_TIMEOUT_<KIND>_SECONDS` and per request
with `timeoutSeconds` (`timeout_seconds` on inference). A run past
`started_at + timeout_seconds` fails cleanly: status FAILED and its NFO
runtime torn down; the model's lifecycle stage fails only if still legal;
MLTrainingProcess gets `resultStateInfo=TIMEOUT`, validation writes a FAILED
MLTestingReport, an ML update is advanced; the requester is notified
(`failureReason: TIMEOUT`). A late completion or resolve gets 409. A
SUSPENDED training run is paused and its clock restarts on resume. Expiry is
enforced lazily on every job read and completion, and on demand via
`POST /aimgf/execution-timeouts/sweep`.

### Gap analysis (W7-01)

| Gap | Status |
|---|---|
| Execution runtimes unsized; descriptor carried only `{jobKind, jobId}` | Closed: per-mode `resources` from the manifest or an explicit profile |
| Manifest had no execution modes or compute | Closed: `executionModes` / `autonomyModes` / `requiredServices` / `runtimeProfiles` validated at onboarding |
| A run could stay IN_PROGRESS/RUNNING forever, with its NFO runtime | Closed: stage timeouts with clean failure and teardown |
| A late completion could overwrite a finished, cancelled or timed-out run | Closed: completion and resolve only from a running state |
| Unknown inference job id → 500 | Closed: 404 `INFERENCE_JOB_NOT_FOUND` |
| Runtime scaling takes no target size | Open: NFO scale has no replica or resource argument (see [Backlog](#backlog)) |
| No asynchronous completion from NFO | Open by design: Instantiate is synchronous (call flow 15) |
| Runtime transitions have no operator gate | By design: operator gates exist on the certification path; autonomy gating of actions is Wave 8's |

Evidence: `aimgf/tests/test_runtime.py`, onboarding runtime-profile tests, call flow 17.
