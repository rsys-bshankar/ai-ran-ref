# Wave 10.3 Exit Review — Coverage Optimization rApp

This review closes W10.3-10 in `WAVES_4_TO_10_WORK_ITEMS.md` §10a. The design decisions D10.3-1..4 were
frozen with the user before implementation. Every check below points at the test that proves it, and
those tests run in CI on every PR:
* `tests_integration/test_coverage_optimization_rapp.py` (CCO-01..CCO-20);
* `tests_integration/test_demo_runbook.py::test_coverage_optimization_demo_00_to_11_runs_end_to_end` (Demo 00–11, `DEMO_RUNBOOK.md` §26);
* `samples/coverage-optimization-rapp/tests` (model, joint optimiser, propagation model and engine).

In the integration suite and the demo, live PM is produced from each cell's tilt and power as read back
from the mock O1 adaptor. So every check below is on a closed loop: the rApp's own changes are measured
in the next hour's PM.

## Exit criteria

| # | Criterion | Evidence |
|---|-----------|----------|
| 1 | ✅ rApp onboarded | CCO-01: `coverage-optimization-rapp.csar` → `AVAILABLE`, aiCapabilities with 4 execution modes, 3 autonomy modes and runtime profiles; O1 targets `CommonBeamformingFunction` and `NRSectorCarrier`. Demo 01. |
| 2 | ✅ Dataset discovered | CCO-02: `COVERAGE_PERFORMANCE` (per-cell measurement-report statistics with the CM snapshot, O1 PM → RAN NF OAM → DME) and the Digital Twin's `COVERAGE_PERFORMANCE_SIM`, found via `sdk.data.get_dataset`. Demo 02. |
| 3 | ✅ Sensitivities learned | CCO-03: AIMgF TrainingJob `FINISHED`. The 12 learned sensitivities recover the propagation model's signs and magnitudes: uptilt raises overshoot, power cuts weak coverage, and neighbours reaching in raise pollution. `CoverageSensitivityModel` and `coverage_model.json` are in MLMR. Demo 03. |
| 4 | ✅ Validated and emulated | CCO-04/05: held-out RMSE ≤ 1.0 and direction accuracy ≥ 0.85. Emulation on the Digital Twin's injected faults: move accuracy 1.0, no false actions on the healthy cluster. Demo 04–05. |
| 5 | ✅ Certified and deployed | CCO-06/07: operator governance to PROMOTED, MLLF/AIMgF/NFO deploy, RuntimeLifecycle `ACTIVE`. Demo 06–07. |
| 6 | ✅ Joint plan treats the cause | CCO-08/09: 301 overshoots into 302/303, and the plan downtilts 301. 302 and 303 are `HELPED_BY` it and are not moved. CCO-15: pollution in 303 with no single overshooter is answered by downtilting its neighbours, not by pushing 303. CCO-14: weak coverage in 302 → `POWER_UP` on 302 alone. Demo 08. |
| 7 | ✅ O1 action generated and verified | CCO-08/09/14: AutonomyDispatch → Intent → SA SMOS O1-CM handler (new `CommonBeamformingFunction.digitalTilt` / `NRSectorCarrier.configuredMaxTxPower` targets) → DME → RAN NF OAM; read back over NETCONF get-config. Demo 09. |
| 8 | ✅ Bounds and pacing | CCO-10..13: nothing moves while a change set is observed, and steps are 1° or 1 dB. The tilt rises monotonically, each step confirmed, until the overshoot is gone, never beyond baseline + 4°. Engine unit tests cover each bound and the pacing guard. |
| 9 | ✅ KPI-verified revert | CCO-11: the measured objective dropped → `CONFIRMED`. CCO-16: the cluster got worse → every cell in the set `REVERTED` straight through DME, with the execution's correlation id, read back. Demo 10. |
| 10 | ✅ Coordination with EnergySaving | CCO-18: a cell the EnergySaving rApp sleeps is `CELL_ASLEEP`, and its neighbours are `NEIGHBOUR_ASLEEP`. After its wake, the cell and its neighbours are `RECENTLY_WOKEN`. |
| 11 | ✅ Coordination with Mobility | CCO-18: while the Mobility rApp observes a CIO change on 301→302, both cells are `MRO_OBSERVING` and are not moved. |
| 12 | ✅ Protected cells | CCO-17: EMERGENCY and incident-zone cells, and windows under 100 reports, are blocked. An active critical alarm holds every cell. Every blocking guard is named in the reason. |
| 13 | ✅ Autonomy modes | CCO-19: SHADOW only recommends; ASSIST holds the whole cluster until the operator resolves, then reconcile executes the change set; AUTONOMOUS acts (CCO-08). |
| 14 | ✅ Rollback | CCO-19: an `<rpc-error>` → `ACTION_FAILED_ROLLED_BACK`; a write the NF ignores → `VERIFY_FAILED_ROLLED_BACK`; both cells end STEADY at their previous settings and no change set is opened. |
| 15 | ✅ Audit trail, dashboard and demo | CCO-20: per cell per pass, Shares → Joint plan → Safety → Decision → Intent → Action → Verification → Final state, joined by execution id; dashboard with share and excess trends; GUI **Coverage** page. Demo 00–11 run end to end in CI. |

## Platform changes this wave needed

- **Mock O1 adaptor** models `CommonBeamformingFunction` (`digitalTilt`, `digitalAzimuth`,
  `coverageShape`) and `NRSectorCarrier` (`configuredMaxTxPower`, `txDirection`).
- **SA SMOS O1-CM handler** has the `CommonBeamformingFunction.digitalTilt` and
  `NRSectorCarrier.configuredMaxTxPower` CM targets, with scalar integer values.
- **Wiring:** R1 route, BFF module and RBAC rules, compose service, migration tables
  (`coverage_instance`, `coverage_cell`, `coverage_decision`), OpenAPI spec, CI unit-test step.
- RAN NF OAM needed no change: the Wave 10.2 multi-counter `/pm-reports` carries the coverage counters.
  The bundled TS 28.541 descriptor already defines both IOCs.

## Deviations and their reasons

- **The radio network is a linear propagation model.** `app/producer.py` turns tilt and power into
  problem shares with fixed coefficients and injected faults. It is deterministic, so the closed loop is
  testable. It is not a ray tracer. The model learns the coefficients from history and is never told
  them.
- **`configuredMaxTxPower` is carried in dBm.** The mock adaptor and the rApp use whole dBm, so a 1 dB
  step is one unit.
- **A critical alarm holds the whole managed element.** RAN NF OAM's alarm view has no cell reference,
  so the rApp cannot tell which cell an alarm is on. This matches the EnergySaving rApp's TC24.
- **The objective is a hinge on fixed 5 % thresholds**, one per problem class, plus a cost per moved
  cell. A per-cell, per-class threshold from the TS 28.541 CCO parameter sets is a backlog refinement.
- **The counters are this reference build's names** (`MR.*`, plus the `CM.*` snapshot joined into each
  window). TS 28.552 does not define measurement-report problem shares as counters.
- **A joint plan may also move a cell that is only a contributor.** On the sample cluster, an overshoot
  in 301 also downtilts 304 one step, because 304's overlap into 302/303 adds to their pollution. Each
  such move must pay its cell cost and pass the no-worsening check.
- **The rApp's own tables are in the consolidated migration**, as in Waves 10.1 and 10.2.

## Success statement

Using only O1 PM data, a Non-RT RIC Coverage Optimization rApp:
* learns how coverage problems respond to tilt and power, its own and its neighbours';
* executes a TS 28.105-governed AI lifecycle;
* adjusts a cluster's tilt and power jointly, in small, paced, bounded steps, at the cause of each
  problem;
* verifies every O1 write and reverts any change set that left the cluster worse;
* stays clear of cells that are asleep, waking, protected, alarmed, or mid-way through a Mobility
  change.

It needs no A1, Near-RT RIC, xApps or E2. **Met.** Every clause above is exercised by the tests listed
in this review.
