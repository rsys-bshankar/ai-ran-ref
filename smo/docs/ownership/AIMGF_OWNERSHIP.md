# AIMgF Ownership

Status: **implemented** — Wave 2 (`aimgf/app/statemachine.py`,
`aimgf/app/models.py`, `aimgf/app/main.py`). See
`docs/architecture/SERVICE_OWNERSHIP_MATRIX.md` for how this fits
alongside MLMR/MLLF/MDAF/DME/Intent Service, and
`docs/architecture/AI_PLATFORM_BASELINE.md` for the layered picture.

## Mission

AIMgF (AI Management Function) is the AI lifecycle orchestrator.

- It makes lifecycle decisions.
- It does **not** perform training.
- It does **not** perform inference.
- It does **not** store models.
- It orchestrates all of them — MLMR, MLLF, DME, MDAF, NFO, and rApp
  execution roles.

## Owns

**Lifecycle state machine** (two of them, both real as of Wave 2 —
`aimgf/app/statemachine.py`):
- Model Lifecycle (`ModelLifecycleState`, 14 states): `REGISTERED` →
  `TRAINING` → `TRAINED` → `VALIDATING` → `VALIDATED` → `EMULATING` →
  `EMULATED` → `PENDING_APPROVAL` → `APPROVED` → `CERTIFIED` →
  `PROMOTED`, with `DEPRECATED`/`RETIRED`/`FAILED` as terminal states off
  that spine and `ROLLBACK` (`PROMOTED` → `CERTIFIED`) as governance's
  own escape hatch. No lightweight update path: retraining a `PROMOTED`
  model re-enters at `TRAINING`, mirroring the pre-Wave-2 FSM's own
  `ACTIVE` → `RETRAIN` → `TRAINING` edge.
- Runtime Lifecycle (`RuntimeLifecycleState`, 8 states), jointly with
  NFO: `NOT_DEPLOYED` → `DEPLOYMENT_REQUESTED` → `DEPLOYED` →
  `ACTIVATING` → `ACTIVE`, with `SCALING` (returns to `ACTIVE`) and
  `TERMINATING` → `TERMINATED`. Deliberately independent of Model
  Lifecycle: retraining a `PROMOTED` model doesn't force its runtime
  down, and a runtime can be scaled/terminated without touching the
  model's own certification state.

**AI workflow requests**
- Create Training
- Create Validation
- Create Emulation
- Create Inference
- Stop Runtime
- Retire Model

**Runtime tracking**
- Training job state
- Validation job state
- Emulation job state
- Inference job state

**Governance**
- Approval
- Certification
- Promotion
- Rollback

**NFO invocation**
- Request runtime creation
- Request runtime termination
- Request runtime scaling

## Does NOT own

| Concern | Owner |
|---|---|
| Repository (model artifacts, versioning, coordination groups) | MLMR |
| Model loading / activation | MLLF |
| Data | DME |
| Analytics | MDAF |
| Business logic | rApps |

## API ownership (Wave 3 contract design starts from this list)

AIMgF's own future R1 contract should expose:

- `registerModel()`
- `startTraining()`
- `startValidation()`
- `startEmulation()`
- `startInference()`
- `getLifecycleStatus()`
- `certifyModel()`
- `retireModel()`

It should **not** expose `storeModel()` (MLMR's) or `loadModel()`
(MLLF's) — a route request for either of those inside `aimgf/` is a
sign the Wave 1 migration misclassified something and should be moved,
not implemented in place.

## Migration source (Wave 1)

`ai-ml-workflow/app/statemachine.py` moves to `aimgf/app/statemachine.py`
— lifecycle ownership is AIMgF's, so the FSM that encodes it belongs
here. `ai-ml-workflow/app/models.py` is split three ways (see
`MLMR_OWNERSHIP.md` / `MLLF_OWNERSHIP.md` for their shares); AIMgF's
share is the lifecycle-facing objects: `TrainingJob`, `ValidationJob`,
`EmulationJob`, `InferenceJob`/`InferenceRuntime`, and lifecycle state
itself — not `AIMLModel`'s own repository row (MLMR's) and not a
loading/activation record (MLLF's).

## Domain model (Wave 2, `aimgf/app/models.py`)

The full eight-aggregate domain model, implemented:

1. **`MLModel` reference** — not a table AIMgF owns (MLMR's own row); a
   bare UUID everywhere else in this list references it, the same
   cross-module-reference shape as `TrainingJob.model_id` already used.
2. **`ModelLifecycle`** — one row per model, AIMgF's own lifecycle-state
   truth: `model_lifecycle_state`, `runtime_lifecycle_state`,
   `training_job_id`, `cleared_node_groups`,
   `nf_deployment_descriptor_id`, `nf_deployment_id`. Replaces Wave 1's
   `PATCH /mlmr/models/{id}/lifecycle` outright — MLMR's own row no
   longer carries any of this.
3. **`TrainingJob`** — unchanged from Wave 1.
4. **`ValidationJob`** — new: `POST /validation-jobs` (requires
   `TRAINED`, fires `CREATE_VALIDATION`), `POST /validation-jobs/{id}/complete`.
5. **`EmulationJob`** — new: `POST /emulation-jobs` (requires
   `VALIDATED`, fires `CREATE_EMULATION`), `POST /emulation-jobs/{id}/complete`.
6. **`InferenceJob`** (realizes the list's own `InferenceRuntime`) —
   unchanged shape, now gated on `RuntimeLifecycleState.ACTIVE` instead
   of the old flat `ModelState.ACTIVE`.
7. **`CertificationRecord`** — new: one row per governance decision
   (`SUBMIT_FOR_APPROVAL`/`APPROVE`/`REJECT`/`CERTIFY`/`PROMOTE`/`ROLLBACK`),
   written by `POST /models/{id}/advance` whenever the fired event is
   one of `statemachine.GOVERNANCE_EVENTS` — `decidedBy` is required for
   these six, `rationale` optional. `GET /models/{id}/governance-history`
   is the real audit trail.
8. **`LifecycleTransition`** — new: every ModelLifecycle *and*
   RuntimeLifecycle transition, tagged `fsm="MODEL"`/`"RUNTIME"`.
   `GET /models/{id}/lifecycle-history` (optionally filtered by `fsm`).

## NFO invocation (Wave 2, real)

`POST /models/{id}/runtime/deploy` calls NFO's own `CreateDescriptor` +
`Instantiate` for real — a model runtime has no onboarded
`ApplicationPackage` behind it, unlike an rApp's own
`NfDeploymentDescriptor`, so `packageId` is omitted (NFO's own
`nf_deployment_descriptor.package_id` column is nullable since this
wave, for exactly this caller). `.../runtime/scale` and
`.../runtime/terminate` call NFO's own `scale`/`terminate` on the
resulting `nfDeploymentId`. `.../runtime/activate` is local-only:
NFO's own deployment is already `RUNNING` once `deploy` returns (Phase
1: `Instantiate` completes synchronously, the same elision used
elsewhere in this build) — `ACTIVATE` is AIMgF's own decision about
whether to accept inference traffic yet, not a further NFO call.

A real bug this exact deploy → scale → terminate sequence found running
against live Postgres (not caught by any prior unit test, SQLite-backed
and never enforcing the FK): NFO's own `Terminate` route deleted its
`LCMOperation` history by a filter query issued *before* flushing the
`TERMINATE` operation row it had just added in the same call, on a
session with `autoflush=False` — so that one row was never caught by
the filter, and `Terminate`'s own final `NFDeployment` delete then
violated the FK the still-pending row held. Fixed with an explicit
`db.flush()` between the two (`nfo/app/main.py`).
