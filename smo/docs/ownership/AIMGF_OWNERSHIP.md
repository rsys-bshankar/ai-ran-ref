# AIMgF Ownership

Status: **frozen** — Wave 0. See `docs/architecture/SERVICE_OWNERSHIP_MATRIX.md`
for how this fits alongside MLMR/MLLF/MDAF/DME/Intent Service, and
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

**Lifecycle state machine** (two of them — see Wave 2 for the full
design):
- Model Lifecycle (`ModelLifecycleState`)
- Runtime Lifecycle (`RuntimeLifecycleState`), jointly with NFO

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

Wave 2 deepens this into the full eight-aggregate domain model
(`MLModel` reference, `TrainingJob`, `ValidationJob`, `EmulationJob`,
`InferenceRuntime`, `CertificationRecord`, `LifecycleTransition`) and
the two real state machines — this document only freezes the ownership
boundary Wave 1 needs to do the split correctly.
