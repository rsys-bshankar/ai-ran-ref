# Wave 10.1 Exit Review — EnergySaving rApp

This review closes W10-29 in `WAVES_4_TO_10_WORK_ITEMS.md`. The exit criteria are the 15 checks of the
consolidated Wave 10 document (`[W10C]` §20), and every check below points at the test that proves it.
Those tests run in CI on every PR:
* `tests_integration/test_energy_saving_rapp.py` (TC01–TC33);
* `tests_integration/test_demo_runbook.py::test_energy_saving_demo_01_to_11_runs_end_to_end` (Demo 00–11);
* `samples/energy-saving-rapp/tests` (model and decision engine).

## §20 exit criteria

| # | Criterion | Evidence |
|---|-----------|----------|
| 1 | ✅ EnergySaving_rApp onboarded | TC01: `energy-saving-rapp.csar` → `AVAILABLE`, aiCapabilities with 4 execution modes, 3 autonomy modes and runtime profiles (`test_tc01_to_tc10_…`). Demo 01. |
| 2 | ✅ Dataset discovered | TC02: `PRB_UTILIZATION` (O1 PM → RAN NF OAM `/pm-reports` → DME) and the Digital Twin's `PRB_UTILIZATION_SIM`, found via `sdk.data.get_dataset` with one data job per execution mode. Demo 02. |
| 3 | ✅ Training completed | TC03: AIMgF TrainingJob `FINISHED` on the package's MLTF profile; `EnergySavingPredictor` and its artifact (`energy_model.json`) are in MLMR. Demo 03. |
| 4 | ✅ Validation completed | TC04: ValidationJob `COMPLETED`. The held-out class score must be ≥ 0.8 and the RMSE ≤ 5. Demo 04. |
| 5 | ✅ Emulation completed | TC05: EmulationJob `COMPLETED` on the Digital Twin trend; midnight recommendation is `LOCKED`, coverage impact ≤ 5 %. Demo 05. |
| 6 | ✅ Certification achieved | TC06: governance SUBMIT_FOR_APPROVAL → APPROVE → CERTIFY → PROMOTE, each decided by the operator; the lifecycle history shows every state. Demo 06. |
| 7 | ✅ Runtime deployed | TC07: MLLF clears node groups (CERTIFIED check), AIMgF/NFO deploy MLIF, RuntimeLifecycle `ACTIVE`. Demo 07. |
| 8 | ✅ Inference executed | TC08: an AIMgF inference job per pass, resolved with the rApp's outputs into an AIMLInferenceReport; PRB 2 % → `LOCKED`. Demo 08. |
| 9 | ✅ PRB dataset consumed | TC02/TC08: training, validation and every live pass read DME records only, never another module's database. |
| 10 | ✅ O1 action generated | TC09/TC13: AutonomyDispatch → Intent → SA SMOS O1-CM handler → DME action → RAN NF OAM config job. Demo 09. |
| 11 | ✅ O1 action verified | TC10/TC18/TC33: read-after-write over NETCONF get-config (`GET /ran-nf-oam/managed-entities/{me}/config`). Demo 10. |
| 12 | ✅ administrativeState updated | TC10: `NRCellDU=101` LOCKED on the mock O1 adaptor; sibling cells untouched. With the D-2 actuator, `CESManagementFunction.energySavingControl` is set and `energySavingState` follows. Demo 10. |
| 13 | ✅ Rollback validated | TC27/TC28: a partial apply is rolled back to UNLOCKED, and the restore is itself verified. TC18: VERIFY_FAILED → rollback. TC17: ACTION_FAILED → rollback. |
| 14 | ✅ Audit trail complete | TC30: Prediction → Safety → Decision → Intent → Action → Verification → Rollback → Final state, one row per cell, joined by execution (correlation) id and dispatch / intent / action / job ids. TC21: direct actions carry the correlation id in DME. |
| 15 | ✅ Demo completed | Demo 00–11 (`DEMO_RUNBOOK.md` §24) run by `samples/energy-saving-rapp/demo.py`, executed end to end in CI. |

## Test matrix (`[W10C]` §18)

| TC | Case | Test |
|----|------|------|
| TC01–TC10 | Lifecycle, inference, O1 action and verification | `test_tc01_to_tc10_lifecycle_inference_and_verified_o1_action` |
| TC11 | SHADOW: recommendation only | `test_tc11_tc12_shadow_assist_and_the_energy_saving_control_actuator` |
| TC12 | ASSIST: approval required; reject means no action | same |
| TC13 | AUTONOMOUS: immediate | `test_tc01_to_tc10_…` |
| TC14 | Duplicate LOCK → no action | `test_tc01_to_tc10_…` |
| TC15 | Duplicate UNLOCK → no action | `test_tc21_operator_override_and_tc15_duplicate_unlock` |
| TC16 | NETCONF timeout → retried | `test_tc16_netconf_timeout_retried_and_tc17_retries_exhausted` |
| TC17 | Retries exhausted → ACTION_FAILED + alarm, lifecycle intact | same |
| TC18 | Verification mismatch → rollback | `test_tc18_tc27_tc28_tc33_verification_and_rollback` |
| TC19 | Neighbour > 80 % → UNLOCK | `test_tc19_neighbour_congestion_wakes_and_tc32_recovery` |
| TC20 | Coverage alarm → UNLOCK | `test_tc20_coverage_alarm_wakes_and_tc24_critical_alarm_blocks` |
| TC21 | Operator override → UNLOCK, AI suppressed | `test_tc21_operator_override_and_tc15_duplicate_unlock` |
| TC22 | Wake threshold exceeded → UNLOCK | `test_tc22_wake_threshold_tc23_hysteresis_tc31_false_wake_up_and_mdaf` |
| TC23 | 5–15 % → no state change | same |
| TC24 | Active critical alarm → LOCK blocked | `test_tc20_…_tc24_…` |
| TC25 | Last serving sector → LOCK blocked | `test_tc25_last_sector_and_tc26_emergency_cell_block` |
| TC26 | Emergency cell → LOCK blocked | same |
| TC27 | Rollback restores the previous state | `test_tc18_tc27_tc28_tc33_…` |
| TC28 | Partial apply → rollback | same |
| TC29 | Action replay (same actionId) → ignored | `test_tc01_to_tc10_…` |
| TC30 | Audit trail completeness | `test_tc01_to_tc10_…` |
| TC31 | False wake-up: no flapping, the 30-min soft guard holds | `test_tc22_…_tc31_…` |
| TC32 | Neighbour overload recovery | `test_tc19_…_tc32_…` |
| TC33 | Read-after-write mismatch on a wake → re-sent, verified | `test_tc18_…_tc33_…` |

## Deviations and their reasons

- **The O1 target is NRCellDU, not the documents' NRCellCU.** TS 28.541 places `administrativeState` on
  NRCellDU (decision D-2). CESManagementFunction.energySavingControl is the configurable alternative.
- **The model is "Threshold + regression", not ONNX/LSTM** (D-6). It regresses the next-hour change
  (drift, last-hour trend, and the change the learned hour-of-day profile expects), applies the 5 % / 15 %
  thresholds, and is stored as a plain JSON artifact. A regression on PRB alone could not tell midnight
  from the morning ramp; the hour-of-day profile is what lets it decline to sleep a 2 % cell at 05:00.
  LSTM stays in the backlog.
- **There is no `GET /dme/datasets/{name}`.** A dataset is a DME type; `sdk.data.get_dataset` discovers it by
  name and reads it through a data job (D-4).
- **A LOCK follows the autonomy mode; a wake doesn't.** A LOCK goes through AutonomyDispatch / Intent. A
  wake, a rollback or an override restores service, so it goes straight to DME in AUTONOMOUS and ASSIST.
  A safety wake waiting for operator approval would contradict §9's "must return to service".
- **Simulated NETCONF timeouts.** The mock O1 adaptor injects a timeout as HTTP 504, and RAN NF OAM
  treats it as a NETCONF timeout. The retry delays come from `RAN_NF_OAM_NETCONF_RETRY_DELAYS` (default
  0,5,10,20 s), which the tests run without waiting.
- **The rApp's own tables are in the consolidated migration.** In a real deployment it would have its
  own store; this reference build runs one shared Postgres.

## Success statement

Using only O1 PM data, a Non-RT RIC EnergySaving_rApp:
* predicts sustained low utilization;
* executes a TS 28.105-governed AI lifecycle;
* deploys through AIMgF/NFO;
* safely transitions cells into energy-saving mode;
* automatically restores service before congestion occurs;
* performs verified O1 configuration updates.

It needs no A1, Near-RT RIC, xApps or E2. **Met.** Every clause above is exercised by the tests listed
in this review.
