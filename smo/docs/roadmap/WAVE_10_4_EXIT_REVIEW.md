# Wave 10.4 Exit Review — Traffic Steering rApp

This review closes W10.4-10 in `WAVES_4_TO_10_WORK_ITEMS.md` §10b. The design decisions D10.4-1..4 were
frozen with the user before implementation. Every check below points at the test that proves it, and
those tests run in CI on every PR:
* `tests_integration/test_traffic_steering_rapp.py` (TS-01..TS-20);
* `tests_integration/test_demo_runbook.py::test_traffic_steering_demo_00_to_11_runs_end_to_end` (Demo 00–11, `DEMO_RUNBOOK.md` §27);
* `samples/traffic-steering-rapp/tests` (model, planner, load model and engine);
* `samples/mobility-optimization-rapp/tests` (the reciprocal `MLB_OBSERVING` guard).

In the integration suite and the demo, live PM is produced from each cell's CIO and reselection priority
as read back from the mock O1 adaptor. So every check below is on a closed loop: the rApp's own steps
are measured in the next hour's PM.

## Exit criteria

| # | Criterion | Evidence |
|---|-----------|----------|
| 1 | ✅ rApp onboarded | TS-01: `traffic-steering-rapp.csar` → `AVAILABLE`, aiCapabilities with 4 execution modes, 3 autonomy modes and runtime profiles; O1 targets `NRFreqRelation` and `NRCellRelation`. Demo 01. |
| 2 | ✅ Dataset discovered | TS-02: `LOAD_PERFORMANCE` (TS 28.552 `RRU.PrbTotDl`, `RRC.ConnMean`, `DRB.UEThpDl`, per-relation handover counters and the CM snapshot, O1 PM → RAN NF OAM → DME) and the Digital Twin's `LOAD_PERFORMANCE_SIM`. Demo 02. |
| 3 | ✅ Trained, validated, emulated | TS-03..05: the learned transfer recovers the load model's (≈ 0.03 per CIO dB, ≈ 0.06 per priority step); held-out band score ≥ 0.85 and RMSE ≤ 3; every emulated hotspot steered, no false actions. Demo 03–05. |
| 4 | ✅ Certified and deployed | TS-06/07: operator governance to PROMOTED, MLLF/AIMgF/NFO deploy, RuntimeLifecycle `ACTIVE`. Demo 06–07. |
| 5 | ✅ Connected steering | TS-08: a hotspot on 401 → one +2 dB CIO step on 401→402, its least-loaded neighbour, on the same layer. It goes through AutonomyDispatch → Intent → SA SMOS O1-CM handler → DME → RAN NF OAM and is read back. Demo 08–09. |
| 6 | ✅ Idle steering | TS-11: the next step goes to the other layer by `NRFreqRelation=401-F2100` priority 5 → 6. TS-16: with the same-layer neighbour protected, the first step is idle. TS-18: with the same-layer neighbour asleep, the first step is idle. |
| 7 | ✅ Hysteresis, capacity and release | TS-12: once both neighbours are near their limit, `NO_ELIGIBLE_TARGET`, every candidate rejected `TARGET_CAPACITY`. TS-14: as load falls, the steering is released step by step (`RELEASE_CONNECTED`, then `RELEASE_IDLE`) back to the baseline. Unit tests cover the 50–70 hold zone. |
| 8 | ✅ KPI-verified revert | TS-10: confirmed when the source is below its no-steering forecast. TS-13: a 4 dB CIO step raises too-early handovers → `KPI_DEGRADED:HO_FAILURES`, reverted through DME with the execution's correlation id. TS-15: a step whose target then congests → `TARGET_CONGESTED`, reverted. Demo 10. |
| 9 | ✅ Shared CIO with Mobility, both ways | TS-18: while the Mobility rApp observes a CIO change on 401→402, Traffic Steering excludes that relation (`MRO_OBSERVING`) and leaves the Mobility rApp's +2 dB alone. When Traffic Steering's own step on it is under observation, a Mobility instance holds the relation (`MLB_OBSERVING`). Both stay inside baseline ± 6 dB. |
| 10 | ✅ Coordination with EnergySaving | TS-18: a sleeping cell is not a target (`TARGET_ASLEEP`) and does not steer (`CELL_ASLEEP`). Unit tests cover `TARGET_RECENTLY_WOKEN`. |
| 11 | ✅ Coordination with Coverage | Unit tests: a source in a Coverage change set is held (`COVERAGE_OBSERVING`), and such a cell is not a target. |
| 12 | ✅ Protected cells, network limits, anti-oscillation | TS-16: incident-zone and EMERGENCY cells, thin windows and critical alarms. TS-17: a relation with `isMLBAllowed=false` is never biased, and an idle knob at its bound is not stepped further. Unit tests: no steering back within 6 hours (`ANTI_OSCILLATION`). |
| 13 | ✅ Autonomy modes | TS-19: SHADOW only recommends; ASSIST holds the cluster until the operator resolves, then reconcile executes; AUTONOMOUS acts (TS-08). |
| 14 | ✅ Rollback | TS-19: an `<rpc-error>` → `ACTION_FAILED_ROLLED_BACK`; a write the NF ignores → `VERIFY_FAILED_ROLLED_BACK`; nothing is left in force. |
| 15 | ✅ Audit trail, dashboard and demo | TS-20: per cell per pass, Score / forecast → Safety and excluded targets → Plan → Intent → Action → Verification → Final state, joined by execution id; dashboard with score trends and steering in force; GUI **Traffic Steering** page. Demo 00–11 run end to end in CI. |

## Platform and earlier-rApp changes this wave needed

- **Mock O1 adaptor** models `NRFreqRelation` (`cellReselectionPriority`, `qOffsetFreq`), and
  `NRCellRelation` now also carries `isMLBAllowed`.
- **SA SMOS O1-CM handler** has the `NRFreqRelation.cellReselectionPriority` CM target. The CIO target
  is the one from 10.2.
- **The Mobility rApp** (10.2) has the reciprocal arbitration. An optional `trafficSteeringInstanceId`
  in its instance config makes it hold relations the Traffic Steering rApp is observing (`MLB_OBSERVING`).
  It is stored in a new `mobility_instance.traffic_steering_instance_id` column. Without it, 10.2
  behaves as before.
- **Wiring:** R1 route, BFF module and RBAC rules, compose service, migration tables
  (`traffic_instance`, `traffic_cell`, `traffic_decision`), OpenAPI spec, CI unit-test step.

## Deviations and their reasons

- **The network is a linear load model.** `app/producer.py` moves a fixed fraction of a cell's offered
  load per CIO dB and per priority step, with a fixed handover-failure penalty above 2 dB. It is
  deterministic, so the closed loop is testable. The model learns the fractions from history and is
  never told them.
- **The region scope names relations, not cells.** Intent Service bounds a dispatch's Cell context by
  the instance's region scope. For this rApp the scope lists every `<source>-<target>` relation and
  `<cell>-<layer>` frequency relation it may write, as the Mobility rApp's lists relations.
- **Cell layers come from the instance config.** The PM path carries no frequency-layer field, so the
  cells' layers are configured (`cells: [{cellId, layer}]`). The Digital Twin's records carry them.
- **One knob per step.** The rApp never changes priority and CIO for one source in the same pass; the
  KPI check then attributes the effect to one change.
- **A critical alarm holds the whole managed element.** RAN NF OAM's alarms carry no cell reference, so
  this matches Waves 10.1 and 10.3.
- **The counters for handover attempts and failures per relation (`HO.Att.<cell>`, `HO.Fail.<cell>`)
  and the CM snapshot (`CM.*`) are this reference build's names.** The load counters are TS 28.552
  names.
- **The rApp's own tables are in the consolidated migration**, as in Waves 10.1–10.3.

## Success statement

Using only O1 PM data, a Non-RT RIC Traffic Steering rApp:
* forecasts congestion per cell;
* executes a TS 28.105-governed AI lifecycle;
* moves load to the least-loaded neighbour, in idle mode across layers and in connected mode by CIO, in
  small, paced, bounded steps;
* never overloads a target;
* shares the CIO safely with the Mobility rApp;
* verifies every O1 write, reverts any step that hurt, and releases its steering when load falls;
* stays clear of cells that are asleep, waking, protected, alarmed, or mid-way through another rApp's
  change.

It needs no A1, Near-RT RIC, xApps or E2. **Met.** Every clause above is exercised by the tests listed
in this review.
