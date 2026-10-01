# Runtime realization — MLTF / MLVF / MLEF / MLIF

**Wave 7** (`docs/roadmap/WAVES_4_TO_10_WORK_ITEMS.md`, W7-01..W7-05). This file holds:
- W7-01, the gap analysis: what the four AI/ML execution roles were like after OPEN_ITEMS.md §6.2 and what this wave closed.
- W7-02, the deployment model: descriptor → deployment → lifecycle → scale / heal / terminate for each role.

## W7-02 — Deployment model per role

| Role | Spec realisation | Started by | NFO descriptor (`workloadTemplate`) | Lifetime | Ended by |
|---|---|---|---|---|---|
| **MLTF** (training) | TrainingJob = TS 28.105 MLTrainingRequest + MLTrainingProcess | `POST /training-jobs`, `POST /ml-training-requests`, group retrain, MLUpdateRequest | `{jobKind: TRAINING, jobId, resources}` | Transient: one descriptor + deployment per run | Completion, cancel, supersede, or **timeout** (`DELETE /nfo/deployments/{id}`) |
| **MLVF** (validation / testing) | ValidationJob = MLTestingRequest | `POST /validation-jobs`, `POST /ml-testing-requests` | `{jobKind: VALIDATION, jobId, resources}` | Transient | Completion, cancel, or **timeout** |
| **MLEF** (emulation) | EmulationJob on an AIMLInferenceEmulationFunction | `POST /emulation-jobs` | `{jobKind: EMULATION, jobId, resources}` | Transient | Completion or **timeout** |
| **MLIF** (inference) | RuntimeLifecycle + AIMLInferenceFunction | `POST /models/{id}/runtime/deploy`, or MLModelLoadingRequest / Policy | `{modelId, jobKind: INFERENCE, resources}` | Long-lived serving deployment | `POST /models/{id}/runtime/terminate` |

For the MLIF runtime, lifecycle state and the NFO call behind each step:

| RuntimeLifecycle step | NFO call |
|---|---|
| NOT_DEPLOYED → DEPLOYMENT_REQUESTED → DEPLOYED | CreateDescriptor + Instantiate |
| ACTIVATING → ACTIVE | AIMgF's own gate; no NFO call |
| SCALING → ACTIVE | `POST /deployments/{id}/scale` |
| TERMINATING → TERMINATED | `DELETE /deployments/{id}` |

Each inference job references the serving deployment and never creates its own. NFO's `HEAL`
(call flow 15) applies to all four roles alike, because they are ordinary NFDeployments.

**Runtime profiles (W7-03).** An rApp package declares profiles in its `manifest.yaml`:
- `executionModes`
- `runtimeProfiles: {TRAINING|VALIDATION|EMULATION|INFERENCE: {cpu, memory, gpu}}`, either at
  the top level or under `rappManifest`

Onboarding validates the profiles: only known modes, only modes listed in `executionModes`, and
non-negative cpu/gpu. An invalid profile sends the package to FAILED. Onboarding exposes the
profiles on `onboarding-status`.

Each AIMgF request may name `packageId` (that package's profile for the mode is used) or give an
explicit `runtimeProfile` (which wins). The chosen profile is stored on the job or lifecycle row
and sent to NFO as `workloadTemplate.resources`.

**Execution timeouts (W7-04).** Defaults (SMO_Wave_10_Consolidated §13):

| Stage | Default timeout |
|---|---|
| Training | 30 min |
| Validation | 15 min |
| Emulation | 30 min |
| Inference | 5 s |

They can be overridden per deployment with `AIMGF_TIMEOUT_<KIND>_SECONDS`, and per request with
`timeoutSeconds` (or `timeout_seconds` on inference). A run past `started_at + timeout_seconds`
is failed cleanly:
- status set to FAILED, and its NFO runtime torn down
- the model's lifecycle stage fails, but only if still legal (no lifecycle corruption)
- the MLTrainingProcess gets `resultStateInfo=TIMEOUT`; validation writes a FAILED MLTestingReport
- an ML update is advanced
- the requester is notified (`failureReason: TIMEOUT`)

A late completion or resolve gets a 409. A SUSPENDED training run is paused, and its clock
restarts on resume. Expiry is enforced lazily on every job read and completion, and on demand via
`POST /aimgf/execution-timeouts/sweep` for a scheduler.

## W7-01 — Gap analysis (against the §6.2 build)

| Gap found | Status |
|---|---|
| Execution runtimes were created unsized; the descriptor carried only `{jobKind, jobId}` | **Closed**: per-mode `resources` from the package manifest or an explicit profile |
| The rApp manifest had no notion of execution modes or compute | **Closed**: `executionModes` / `autonomyModes` / `requiredServices` / `runtimeProfiles` parsed and validated at onboarding |
| A run could stay IN_PROGRESS/RUNNING forever, and its NFO runtime with it | **Closed**: stage timeouts with clean failure and runtime teardown |
| A late completion could overwrite a finished, cancelled or timed-out run | **Closed**: completion and resolve are only legal from a running state |
| Unknown inference job id → 500 (AttributeError) | **Closed**: 404 `INFERENCE_JOB_NOT_FOUND` |
| Runtime scaling takes no target size (NFO scale is a single "scale" step) | Open: NFO's scale has no replica or resource argument. Left for an NFO pass; out of 10.1's path |
| No asynchronous completion from NFO (instantiate is synchronous, Phase 1 elision) | Open by design: the same elision as call flow 15 |
| Runtime transitions (deploy/activate/scale/terminate) have no operator gate | Unchanged: an operator gate exists only on the certification path (OPEN_ITEMS.md §6.1). Autonomy gating of *actions* is Wave 8's concern |

## W7-05 — Exit

Profiles and timeouts are implemented and tested (`aimgf/tests/test_runtime.py`, onboarding
runtime-profile tests). Call flow 17 is updated. The remaining open rows above are recorded,
not blocking for Wave 10.1.
