# Wave 10.2 Exit Review — Mobility Optimization rApp

This review closes W10.2-10 in `WAVES_4_TO_10_WORK_ITEMS.md` §10. The design decisions D10.2-1..4 were
frozen with the user before implementation. Every check below points at the test that proves it, and
those tests run in CI on every PR:
* `tests_integration/test_mobility_optimization_rapp.py` (MRO-01..MRO-20);
* `tests_integration/test_demo_runbook.py::test_mobility_optimization_demo_00_to_11_runs_end_to_end` (Demo 00–11, `DEMO_RUNBOOK.md` §25);
* `samples/mobility-optimization-rapp/tests` (model and MRO engine).

## Exit criteria

| # | Criterion | Evidence |
|---|-----------|----------|
| 1 | ✅ rApp onboarded | MRO-01: `mobility-optimization-rapp.csar` → `AVAILABLE`, aiCapabilities with 4 execution modes, 3 autonomy modes and runtime profiles; O1 targets `NRCellRelation` and `DMROFunction`. Demo 01. |
| 2 | ✅ Dataset discovered | MRO-02: `HO_PERFORMANCE` (per-relation multi-counter O1 PM → RAN NF OAM `/pm-reports` → DME) and the Digital Twin's `HO_PERFORMANCE_SIM`, found via `sdk.data.get_dataset`. Demo 02. |
| 3 | ✅ Trained, validated, emulated | MRO-03..05: AIMgF TrainingJob `FINISHED`, `MobilityRobustnessPredictor` and `mobility_model.json` in MLMR; ValidationJob `COMPLETED` (band score ≥ 0.8, RMSE ≤ 1.5); EmulationJob `COMPLETED` (direction accuracy ≥ 0.9 and no false actions on the Digital Twin's injected faults). Demo 03–05. |
| 4 | ✅ Certified and deployed | MRO-06/07: operator governance to PROMOTED, MLLF/AIMgF/NFO deploy, RuntimeLifecycle `ACTIVE`. Demo 06–07. |
| 5 | ✅ DMRO bounds applied | MRO-08: `DMROFunction` −6/+6 dB, 60 min between changes, `dmroControl` true — written through DME at deploy and read back `VERIFIED` (D10.2-1). Demo 07. |
| 6 | ✅ Classified failures steer the CIO | MRO-09: too late → `RAISE_CIO` +2 dB; too early → `LOWER_CIO` −2 dB. MRO-14: ping-pong −2 dB, wrong cell −1 dB. Healthy relations untouched. Demo 08. |
| 7 | ✅ O1 action generated and verified | MRO-09/10: AutonomyDispatch → Intent → SA SMOS O1-CM handler (`NRCellRelation.cellIndividualOffset` target) → DME → RAN NF OAM; all six QOffsetRange entries read back over NETCONF get-config. Demo 09. |
| 8 | ✅ Bounds and pacing | MRO-11..13: OBSERVING until post-change PM exists; one step per 60 min; 2 dB steps up to baseline + 6 dB, then `AT_BOUND`. Unit tests per rule. |
| 9 | ✅ KPI-verified revert | MRO-12: an improved rate → `CONFIRMED`. MRO-15: a worse rate → `REVERT_CIO` (`KPI_DEGRADED`) straight through DME with the execution's correlation id, read back. Demo 10. |
| 10 | ✅ EnergySaving coordination | MRO-17: relations towards a cell the EnergySaving rApp has in SLEEP or PRE_SLEEP are blocked `TARGET_ASLEEP`; after an EnergySaving wake, `TARGET_RECENTLY_WOKEN` for 30 min. |
| 11 | ✅ Network limits respected | MRO-16: `isHOAllowed=false`, EMERGENCY and incident-zone cells (source or target) and windows under 50 attempts are blocked; every blocking guard is named in the reason. |
| 12 | ✅ Autonomy modes | MRO-18: SHADOW only recommends; ASSIST waits for approval, and on reject nothing is written; AUTONOMOUS acts (MRO-09). |
| 13 | ✅ Rollback | MRO-19: an `<rpc-error>` → `ACTION_FAILED_ROLLED_BACK`; a write the NF ignores → `VERIFY_FAILED_ROLLED_BACK`; both end STEADY at the previous CIO. |
| 14 | ✅ Audit trail and dashboard | MRO-20: Prediction → Safety → Decision → Intent → Action → Verification → Final state per relation per pass, joined by execution id; dashboard with failure-rate trend; GUI **Mobility** page. Demo 11. |
| 15 | ✅ Demo completed | Demo 00–11 (`DEMO_RUNBOOK.md` §25) run by `samples/mobility-optimization-rapp/demo.py`, executed end to end in CI. |

## Platform changes this wave needed

- **RAN NF OAM `/pm-reports`** accepts multi-counter measurements: `values` (counter → value) and an
  optional `relation`, as well as the existing single `value`. A measurement needs one or the other.
- **Mock O1 adaptor** models `NRCellRelation` (`cellIndividualOffset`, `isHOAllowed`) and `DMROFunction`
  with TS 28.541-style defaults.
- **SA SMOS O1-CM handler** has the `NRCellRelation.cellIndividualOffset` CM target.
- **Wiring:** R1 route, BFF module and RBAC rules, compose service, migration tables
  (`mobility_instance`, `mobility_relation`, `mobility_decision`), OpenAPI spec, CI unit-test step.

## Deviations and their reasons

- **All six QOffsetRange entries carry the same value.** TS 28.541 defines CIO per measurement quantity
  (RSRP/RSRQ/SINR for SSB and CSI-RS). The rApp's PM has no per-quantity split, so one offset is written
  to all six and verified on all six.
- **The model is "classification + regression", not a learned policy** (D10.2-2). The failure class sets
  the direction and a persistence-anchored regression decides whether the problem will persist. A
  one-hour spike straight after a healthy hour is predicted to partly revert, so it is held rather than
  acted on; the tests feed two hours of sustained failures before expecting a step.
- **Reverts go straight to DME.** A revert restores the earlier state, like the EnergySaving rApp's wake;
  waiting for approval would leave a change that measurably hurt handover in place.
- **Coordination is read-only.** The Mobility rApp reads the EnergySaving rApp's published cell states
  over R1 and never writes them. A deployment without an EnergySaving instance still blocks targets that
  are asleep on O1.
- **Counters are TS 28.552-style names** (`MM.HoExeAtt`, `MM.HoFailTooLate`, `MM.HoFailTooEarly`,
  `MM.HoFailWrongCell`, `MM.HoPingPong`). The standard does not define all four failure classes as
  counters; the names are this reference build's.
- **The rApp's own tables are in the consolidated migration**, as in Wave 10.1.

## Success statement

Using only O1 handover PM data, a Non-RT RIC Mobility Optimization rApp:
* classifies handover failures per neighbour relation;
* executes a TS 28.105-governed AI lifecycle;
* bounds the gNB's own MRO with DMRO limits;
* adjusts per-relation CIO in small, paced, bounded steps;
* verifies every O1 write and reverts any change that made handover worse;
* stays clear of cells that are asleep, waking, protected, or barred from handover.

It needs no A1, Near-RT RIC, xApps or E2. **Met.** Every clause above is exercised by the tests listed
in this review.
