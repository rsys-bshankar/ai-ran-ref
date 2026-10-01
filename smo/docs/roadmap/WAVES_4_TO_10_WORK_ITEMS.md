# Waves 4–10 — Consolidated Work Items

**Status:** DRAFT. Not yet frozen: the open decisions in §0 are still to be discussed.
**Sources (three documents):**

| Tag | Document | What it is |
|-----|----------|------------|
| `[W49]` | SMO_Waves_4to9.docx | *Pre-roadmap checklist.* It lists the mandatory (M1–M7) and optional (O1–O3) artifacts, the dependency matrix, and the deliverables to produce for Waves 4–9. It contains **no work items of its own**. Its per-wave roadmaps are produced *after* the artifacts are reviewed, and that review is §2 below. |
| `[W10]` | SMO_Wave_10.docx | The raw Wave-10 proposal, in its successive versions: first proposal → D1–D8 → sequence diagram → package → safety refinement → reliability refinement. |
| `[W10C]` | SMO_Wave_10_Consolidated.docx | The consolidated Wave-10 v1.0. **It is canonical wherever it conflicts with `[W10]`.** Detail found only in `[W10]` (package contents, the Notification-retry rule, the demo script) is carried forward here. |

Work items are grounded against the repo as of `f444c71` (OPEN_ITEMS.md §6.1, 6.2 and 6.4–6.7 closed; §6.3 open).

---

## 0. Open decisions (to discuss before freezing)

| # | Topic | Conflict / ambiguity | Recommendation |
|---|-------|----------------------|----------------|
| D-1 | **Autonomy-mode semantics (Wave 8)** | OPEN_ITEMS.md §6.3, the user's own recorded design, says: **all three** modes route the outcome **as an Intent** via Intent Service / SO-/SA-SMOS. ASSIST means the operator **scopes the region** (nodes/cells/slices). A direct DME `/actions` write is the **manual, non-autonomous** path. `[W10C]` §16 says otherwise: ASSIST means the operator **approves the action**, and AUTONOMOUS means a **direct O1 action** with an *optional* Intent. | Keep §6.3 as the platform model. Treat ASSIST as "scope **and** approve", where approval means confirming the operator-scoped Intent. For Wave 10.1, AUTONOMOUS creates an Intent whose fulfilment dispatches through DME `/actions` → RAN NF OAM. That keeps one audit chain and lets Wave 10 exercise Wave 6. |
| D-2 | **O1 target IOC/attribute** | `[W10]`/`[W10C]` target `NRCellCU.administrativeState`. In TS 28.541 (`specs/5G_APIs/TS28541_NrNrm.yaml`), `administrativeState` lives on **NRCellDU**. NRCellCU has no such attribute. 3GPP also defines a dedicated ES control: `CESManagementFunction.energySavingControl` (TO_BE_ENERGY_SAVING / TO_BE_NOT_ENERGY_SAVING). | Use **NRCellDU.administrativeState** for 10.1, as the docs intend: one parameter, LOCKED/UNLOCKED. Log `energySavingControl` as a 10.1-follow-up option. |
| D-3 | **3-state model vs one O1 parameter** | `[W10C]` §10 recommends ACTIVE / PRE_SLEEP / SLEEP, but §3 says the output is exactly one parameter with LOCKED/UNLOCKED. The name `ACTIVE` also clashes with `RuntimeLifecycleState.ACTIVE`. | Make the 3-state model **rApp-internal** (`CellEnergyState`: `SERVING / PRE_SLEEP / SLEEP`). PRE_SLEEP = the 60-min sustained-low-PRB observation window. Nothing is written to O1 until PRE_SLEEP→SLEEP. |
| D-4 | **Illustrative APIs vs the real ones** | `[W10C]` §12 uses `GET /dme/datasets/PRB_UTILIZATION` (DME has no datasets resource; it is type/DataJob based), and `/dme/actions` with `requestedState`/`reason` (the real `/actions` takes `changes[]`/`source_context`). `[W10]` uses SDK names that don't exist: `sdk.data.get_dataset`, `sdk.lifecycle.start_training`, `sdk.models.store_model`, `sdk.analytics.get_prediction`, `sdk.platform.execute_action`. The real names are `discover_types`/`create_data_job`/`fetch_data_records`, `sdk.data.mediate_action`, etc. | Map onto the **existing** DME/SDK surface; add no `/datasets` facade. Optionally add thin SDK convenience wrappers (W10-03) that use the doc's names. |
| D-5 | **Hard-guard data source** | Emergency cell, coverage-critical cell, last remaining sector, active incident zone and neighbour PRB need per-cell classification plus a neighbour relation. No such inventory exists today. | Keep a per-cell **guard policy** in the rApp-instance config, with the neighbour list declared there too. Neighbour PRB is read from the same DME PRB DataJob. TEIV/topology-backed guards come later. |
| D-6 | **Model scope and format** | `[W10C]` lists Threshold → Regression → LSTM and `.onnx`. | 10.1 delivers **Threshold + Regression**. LSTM moves to a backlog item. Use ONNX only if the 6.2 runtimes already load it; otherwise use a plain serialized artifact ("or equivalent" in `[W10]` D2 allows this). |
| D-7 | **Emulation input ("Digital Twin Data")** | There is no digital-twin source in the build. | Use a synthetic PRB time series registered as a DME type (e.g. `PRB_UTILIZATION_SIM`) and fed by a sample producer. |
| D-8 | **Scope of 10.2–10.4** | Each one is a single input→output line, with no design. | Backlog placeholders only. Each gets its own design pass after 10.1 exits. |
| D-9 | **Waves 4–6 depth** | `[W49]` asks only for *mapping matrices* (TS 28.105/28.104/28.312). It doesn't say whether closing the gaps the matrices reveal falls inside the wave. | Each wave = matrix + close the **blocking** gaps (the ones Wave 10.1 needs). Every other gap goes into SPEC_AUDIT.md. |

Smaller inconsistencies that need no decision (resolved here):
- **Test-case IDs.** `[W10]` has three conflicting TC-14..TC-20 sets. **`[W10C]` TC01–TC33 is canonical.** The extra assertions from `[W10]` are folded in: NETCONF timeout ⇒ alarm generated + no lifecycle corruption (into TC16/TC17), and an explicit sleep trigger at PRB=3% (into TC08).
- **Retry.** "Max 3 retries" with "attempt 1 immediate, then +5s/+10s/+20s" means 4 attempts in total. Notifications (AIMgF/Intent/MDAF) are retried **0 times**, best-effort, per `[W10]`.
- **Lifecycle states.** `[W49]` M5 and `[W10]` D3 list subsets of the real FSM. The code is a superset (it adds PENDING_APPROVAL, DEPRECATED, RETIRED, FAILED and the 6.1 operator gates). Not a gap.

---

## 1. Dependency graph

```
Wave-4 (TS 28.105) ─┬───────────────► Wave-7 (Runtime) ──┐
Wave-5 (TS 28.104) ─┤                                    ├─► Wave-8 (Autonomy) ──► Wave-10.1 (Energy Saving)
Wave-6 (TS 28.312) ─┴────────────────────────────────────┘                          │
Wave-9 (Multi-vendor O1) ── independent; the 10.1 O1 path must not regress it       └─► 10.2 / 10.3 / 10.4
```
Wave-10 depends on Waves 4, 5, 7 and 8 `[W10]`. It needs Wave 6 only through D-1 (Intent-routed AUTONOMOUS).

---

## 2. Pre-roadmap gate: review of `[W49]` artifacts

All mandatory artifacts **exist**. The column on the right is what the review found.

| ID | Artifact | Present | Finding (feeds the wave below) |
|----|----------|---------|-------------------------------|
| M1 | `aimgf/app/models.py` | ✅ | ModelLifecycle, TrainingJob, ValidationJob, EmulationJob, InferenceJob, CertificationRecord, LifecycleTransition are all present. **No `InferenceRuntime` entity**: runtime state is on `RuntimeLifecycle` via NFO → W4-03. |
| M2 | `mlmr/app/models.py` | ✅ | MLModel, ModelArtifact, MLModelCoordinationGroup, versioning `(model_type, version)` are present. |
| M3 | `mdaf/app/models.py` | ✅ | Only generic `MDAFReport` + `MDASubscription`, keyed by `analytics_type`. **No distinct AnalyticsReport / PredictionReport / DriftReport** → W5-02. |
| M4 | `intent-service/app/models.py` | ✅ | Intent, IntentReport, IntentHandlingFunction are present. **IntentExpectation is opaque JSON** (`intent_expectations`) → W6-02. |
| M5 | `aimgf/app/statemachine.py` | ✅ | Every model-lifecycle and runtime-lifecycle state listed in `[W49]` is present. |
| M6 | `nfo/app/models.py`, `statemachine.py` | ✅ | Descriptor, Deployment, LCMOperation, scale/heal/terminate are present. |
| M7 | `docs/openapi/{aimgf,mlmr,mdaf,intent-service}.json` | ✅ | Present. |
| O1 | `sdk/smo_sdk/` | ✅ | data / analytics / models / lifecycle / intent / platform. |
| O2 | GUI | ✅ | README + screenshots + flows. |
| O3 | Sample rApps | ⚠️ | Only `samples/hello-world-rapp`. The EnergySaving rApp (W10-01) becomes O3. |

| ID | Work item | Done when |
|----|-----------|-----------|
| W0-01 | Freeze §0 decisions D-1…D-9 | Every row has an agreed answer, recorded in this file |
| W0-02 | Add the dependency graph + spec-traceability index (`[W49]` "Expected Deliverables") | This file's §1 + per-wave matrices linked from README |

---

## 3. Wave-4 — TS 28.105 alignment (AIMgF / MLMR)
Depends on: M1, M2, M5.

| ID | Work item | Done when |
|----|-----------|-----------|
| W4-01 | **TS 28.105 compliance matrix**: each IOC/datatype/operation (MLModel, MLTrainingRequest/Report, MLTesting, MLEmulation, AIMLInferenceFunction, MLModelCoordinationGroup, notifications) mapped to entity/field/route, with status Compliant / Partial / Gap / Out-of-scope | `docs/roadmap/TS28105_COMPLIANCE_MATRIX.md` covers every TS 28.105 IOC in the corpus |
| W4-02 | MLMR repository-ownership review (`[W49]` M2 output): confirm MLMR owns identity/artifact/version and AIMgF owns lifecycle; no duplicated fields | Findings in the matrix; any duplicate closed or logged in SPEC_AUDIT.md |
| W4-03 | Decide and document how `InferenceRuntime` maps (`[W49]` M1 names it; code uses RuntimeLifecycle + NFO deployment) | Mapping row in the matrix; if an entity is added, it is in the OpenAPI |
| W4-04 | Close the **blocking** gaps found by W4-01 that Wave 10.1 uses (training/validation/emulation/inference request + report shapes) | Matrix rows used by 10.1 are all Compliant/Partial-accepted |
| W4-05 | Exit criteria for Wave-4 | Matrix reviewed; remaining gaps in SPEC_AUDIT.md; tests green |

## 4. Wave-5 — TS 28.104 alignment (MDAF)
Depends on: M3.

| ID | Work item | Done when |
|----|-----------|-----------|
| W5-01 | **TS 28.104 mapping matrix** (MDA request/report/subscription, ThresholdInfo, MDA types) | `TS28104_MAPPING_MATRIX.md` |
| W5-02 | Distinguish **AnalyticsReport / PredictionReport / DriftReport**, either as a typed `report_kind` or as separate schemas (additive; `analytics_type` stays) | OpenAPI exposes the three kinds; existing report tests still pass |
| W5-03 | `TRAFFIC_FORECAST` analytics type + `TrafficTrendReport` (`[W10]` D5, optional initial version) consumable via `sdk.analytics` | An rApp can subscribe/query a PredictionReport for a cell |
| W5-04 | Drift report hook: MDAF DriftReport → AIMgF retrain signal (wire only; reuse the MLMFSubscription guard pattern) | A DriftReport above threshold produces a notification AIMgF can consume |
| W5-05 | Exit criteria | Matrix reviewed; tests green |

## 5. Wave-6 — TS 28.312 alignment (Intent Service)
Depends on: M4.

| ID | Work item | Done when |
|----|-----------|-----------|
| W6-01 | **TS 28.312 mapping matrix** (Intent, IntentExpectation, ExpectationObject/Target/Context, IntentReport, IntentHandlingFunction, fulfilment info) | `TS28312_MAPPING_MATRIX.md` |
| W6-02 | Give **IntentExpectation** a structure (expectationId, verb, object, targets, contexts) in place of opaque JSON. Additive, with the JSON kept as a fallback | Schema-validated expectations; existing intent tests still pass |
| W6-03 | Energy-saving expectation template, e.g. "Reduce energy in scope X during 00:00–05:00" (`[W10]` "Where Intent fits") | Template creatable via `sdk.intent.create_intent` |
| W6-04 | IntentReport fulfilment status linked to downstream actions (DME action IDs, correlation-id) | IntentReport shows FULFILLED / NOT_FULFILLED with action refs |
| W6-05 | Exit criteria | Matrix reviewed; tests green |

## 6. Wave-7 — Runtime realization (MLTF / MLVF / MLEF / MLIF)
Depends on: M1, M5, M6. OPEN_ITEMS §6.2 (real NFO-backed runtimes) is **already closed**, so this wave is mostly gap analysis plus hardening.

| ID | Work item | Done when |
|----|-----------|-----------|
| W7-01 | **Runtime realization gap analysis** (`[W49]` M5 output) against the §6.2 build | `RUNTIME_REALIZATION_GAP_ANALYSIS.md` |
| W7-02 | **MLTF/MLVF/MLEF/MLIF deployment model** doc (`[W49]` M6 output): descriptor → deployment → lifecycle → scale/heal/terminate per role | Doc + call flow 17 updated |
| W7-03 | Per-execution-mode **runtime profiles** (cpu/memory/gpu) from the rApp manifest, carried to the NFO descriptor (`[W10]` manifest `runtimeProfiles`) | NFO deployment reflects the profile of the mode requested |
| W7-04 | **Stage timeouts**: Training 30 min, Validation 15 min, Emulation 30 min, Inference 5 s (`[W10C]` §13). Expiry → job FAILED + lifecycle FAILED event, no corruption | Configurable timeouts; tests for each expiry |
| W7-05 | Exit criteria | Gap analysis reviewed; tests green |

## 7. Wave-8 — Autonomy modes (AUTONOMOUS / ASSIST / SHADOW) = OPEN_ITEMS §6.3
Depends on: Wave-6, Wave-7. **Blocked on D-1.**

| ID | Work item | Done when |
|----|-----------|-----------|
| W8-01 | `autonomy_mode` (+ AUTONOMOUS region scope) as an onboarding-time property of the rApp/rApp-instance; manifest field `autonomyModes` | Onboarding persists and validates it; GUI shows it |
| W8-02 | Inference-outcome → Intent hand-off (SO/SA-SMOS `DISPATCH_TABLE` entry, per §6.3 and §6.6) | An AUTONOMOUS outcome creates and dispatches an Intent at the pre-configured scope |
| W8-03 | ASSIST: an intermediate Intent state awaiting operator scope/approval, plus an API and a GUI action | Nothing is dispatched until the operator acts |
| W8-04 | SHADOW: computed Intent surfaced and notified, never dispatched | No RAN change; notification observable |
| W8-05 | Operator notification in **all** modes (best-effort, 0 retries) | Notification emitted for every outcome |
| W8-06 | Update call flows 02/03/09 to show the real hand-off; close §6.3 in OPEN_ITEMS.md | Docs updated |

## 8. Wave-9 — Multi-vendor O1 framework
Depends on: `O1_VENDOR_ONBOARDING_GUIDE.md` (exists).

| ID | Work item | Done when |
|----|-----------|-----------|
| W9-01 | **Capability Registry**: per-vendor `supported_services`, `conformance_mode` (own/spec/combined), `schema_ref` | Registry resource + API in RAN NF OAM |
| W9-02 | Bind `CMSchemaCache` to the registry's `schema_ref`; config writes validated against the vendor schema | Write to an unsupported attribute rejected with the standard error schema |
| W9-03 | Vendor onboarding flow (endpoint discover → capability declare → schema load) | Call flow + integration test |
| W9-04 | Declare `O1_NETCONF` / `O1_RESTCONF` vendor modes (`[W10]` capabilities `supportedVendorModes`) | Registry reflects the transport per vendor |
| W9-05 | Exit criteria | Guide's sketch implemented; tests green |

---

## 9. Wave-10.1 — EnergySaving_rApp (direct O1 closed loop)
Depends on: Waves 4, 5, 7, 8. Planes: R1 (control), O1 (management). Excludes A1, Near-RT RIC, xApps, E2.

### 9.1 Package & model
| ID | Work item | Source | Done when |
|----|-----------|--------|-----------|
| W10-01 | `samples/energy-saving-rapp/` → `energy-saving-rapp.csar`: `manifest.yaml`, `capabilities.yaml`, `EnergyModel.py`, `TrainingLogic.py`, `ValidationLogic.py`, `EmulationLogic.py`, `InferenceLogic.py` | D1 | Onboards → AVAILABLE; declares 4 execution modes + 3 autonomy modes + runtime profiles (TC01) |
| W10-02 | Energy model: Threshold (min) + Regression predicting next-hour PRB; outputs `{futurePrb, recommendedState, confidence}` | D2, §6 | Model artifact stored in MLMR |
| W10-03 | (Optional, D-4) SDK convenience wrappers matching the doc names, built on existing calls | `[W10]` API mapping | Wrappers + unit tests |

### 9.2 Data & analytics
| ID | Work item | Source | Done when |
|----|-----------|--------|-----------|
| W10-04 | PRB utilization PM → RAN NF OAM → DME type `PRB_UTILIZATION` (per-cell time series), plus a sample producer | D4, §3 | rApp discovers the type and reads history via `sdk.data` only, with no direct DB access (TC02) |
| W10-05 | Synthetic emulation dataset `PRB_UTILIZATION_SIM` (D-7) | §5 | MLEF consumes it |
| W10-06 | Consume `TrafficTrendReport`/PredictionReport via `sdk.analytics` (depends on W5-03) | D5 | rApp reads the MDAF prediction |

### 9.3 Lifecycle & runtime
| ID | Work item | Source | Done when |
|----|-----------|--------|-----------|
| W10-07 | End-to-end AIMgF lifecycle run for the energy model: REGISTERED → TRAINING → TRAINED → VALIDATING → VALIDATED → EMULATING → EMULATED → (approval) → CERTIFIED → PROMOTED, on MLTF/MLVF/MLEF runtimes | D3 | TC03–TC06 |
| W10-08 | MLIF deployment AIMgF → NFO → RuntimeLifecycle ACTIVE (MLLF checks CERTIFIED) | D6 | TC07 |
| W10-09 | Inference via `POST /models/{id}/inference-jobs` | §12 | PRB=2–3% → LOCK recommendation (TC08) |

### 9.4 Decision engine (inside the rApp)
| ID | Work item | Source | Done when |
|----|-----------|--------|-----------|
| W10-10 | Pipeline: Input → Prediction → Safety Evaluation → Decision {LOCK, UNLOCK, NO_CHANGE} → O1 Execution → Verification → Audit | §7 | Each stage recorded |
| W10-11 | Sleep policy: PRB < 5% sustained 60 min **and** all guards pass; internal states SERVING/PRE_SLEEP/SLEEP (D-3) | §8, §10 | TC08 |
| W10-12 | Wake policy: predicted PRB > 15% OR neighbour PRB > 80% OR critical coverage alarm OR operator override | §9 | TC19–TC22 |
| W10-13 | Hysteresis: 5–15% → NO_CHANGE | §10 | TC23 |
| W10-14 | Safety guards. Hard: emergency, coverage-critical, last sector, incident zone. Medium: neighbour congestion, active critical alarm. Soft: recently unlocked < 30 min. Guards apply **independent of AI confidence**; data source per D-5 | §11 | TC24–TC26 |
| W10-15 | Operator override (manual UNLOCK via GUI) suppresses AI recommendations | §9 | TC21 |

### 9.5 O1 actuation & reliability
| ID | Work item | Source | Done when |
|----|-----------|--------|-----------|
| W10-16 | Action path rApp → DME `/actions` → RAN NF OAM `/config-jobs` → NETCONF/RESTCONF → O1 adaptor → `NRCellDU.administrativeState` (D-2) | D7, §12 | DmeActionRecord created, cell LOCKED (TC09) |
| W10-17 | Mock O1 adaptor: model `NRCellDU.administrativeState` (read + write) for the demo cells | D7 | Read-back reflects writes |
| W10-18 | Idempotency: skip if already in the desired state; `actionId` dedup in DME → duplicates IGNORED | §13 | TC14, TC15, TC29 |
| W10-19 | Timeouts DME→RAN NF OAM 10 s, NETCONF/RESTCONF 30 s; retries immediate/+5/+10/+20 s; then `ACTION_FAILED` + alarm, no lifecycle corruption | §13 | TC16, TC17 |
| W10-20 | Read-after-write verification (GET administrativeState) → `VERIFY_FAILED` on mismatch | §14 | TC10, TC18, TC33 |
| W10-21 | Rollback to UNLOCKED on VERIFY_FAILED / NETCONF_FAILED / PARTIAL_SUCCESS / neighbour congestion / coverage alarm, through the same DME → RAN NF OAM path | §15 | TC27, TC28 |

### 9.6 Autonomy, audit & UX
| ID | Work item | Source | Done when |
|----|-----------|--------|-----------|
| W10-22 | Apply Wave-8 modes to the rApp. SHADOW: recommend + notify, no change. ASSIST: approval before action. AUTONOMOUS: no human (per D-1) | §16 | TC11–TC13 |
| W10-23 | Audit trace chain Prediction → Safety → Decision → (Intent) → Action → Verification → Rollback → Final state, joined by correlation-id; queryable via GUI/Audit/Lifecycle views | D8, §19 | TC30 |
| W10-24 | GUI Energy Saving dashboard: PRB trend, prediction, safety evaluation, decision, intent, action, verification, rollback, final cell state | §19, Demo 11 | Operator acceptance §19 met |
| W10-25 | Carrier-grade tests: false wake-up prediction, neighbour overload recovery, read-after-write mismatch | §18 | TC31–TC33 |

### 9.7 Verification & demo
| ID | Work item | Source | Done when |
|----|-----------|--------|-----------|
| W10-26 | Integration test suite TC01–TC33 (`tests_integration/test_energy_saving_rapp.py`) | §18 | All green |
| W10-27 | DEMO_RUNBOOK section: Demo 01–11 (`[W10]` demo script) + matching demo-runbook test steps | `[W10]` | Runbook executes end to end |
| W10-28 | Call-flow doc `21-energy-saving-closed-loop.md` (mermaid) | §4, §12 | Renders on GitHub |
| W10-29 | Wave-10.1 exit review against `[W10C]` §20 (15 checks) + success statement | §20 | All checked |

## 10. Wave-10.2 / 10.3 / 10.4 — backlog placeholders (D-8)

| ID | rApp | Input → Output | Next step |
|----|------|----------------|-----------|
| W10.2-00 | Mobility Optimization | Handover failure rate → Cell Individual Offset (CIO) | Design pass after 10.1 exit |
| W10.3-00 | Coverage Optimization | RSRP → antenna tilt | Design pass after 10.1 exit |
| W10.4-00 | Traffic Steering | Congestion score → cell reselection bias | Design pass after 10.1 exit |
| W10-B1 | Energy model LSTM variant (D-6) | PRB → PRB for the next N windows | After 10.1 |
| W10-B2 | `CESManagementFunction.energySavingControl` as an alternative actuator (D-2) | — | After 10.1 |

---

## 11. Suggested execution order
1. W0-01 (freeze decisions) → 2. W4 / W5 / W6 matrices in parallel → 3. W7 → 4. W8 (closes §6.3) → 5. W9 (in parallel with 4) → 6. W10.1 in sub-order 9.1 → 9.2 → 9.3 → 9.4 → 9.5 → 9.6 → 9.7.

**Totals:** 2 gate items · W4: 5 · W5: 5 · W6: 5 · W7: 5 · W8: 6 · W9: 5 · W10.1: 29 · backlog: 5 → **72 work items**.
