# Service Ownership Matrix

Status: **frozen** — Wave 0 deliverable, companion to
`AI_PLATFORM_BASELINE.md`. The purpose is to remove ambiguity before any
Wave 1 code moves: for each of AIMgF, MLMR, MLLF, MDAF, DME, and Intent
Service, "owns" is a route/model this service is the single source of
truth for; "does not own" is an explicit pointer to whoever actually is,
so a Wave 1 PR never has to guess which of `aimgf`/`mlmr`/`mllf` a given
piece of `ai-ml-workflow` code belongs to.

## AIMgF (AI Management Function)

**Mission**: AIMgF is the AI lifecycle orchestrator. It makes lifecycle
*decisions*. It does not train, does not infer, does not store models —
it orchestrates all of those.

**Owns**
- ML lifecycle state machine (Model Lifecycle + Runtime Lifecycle — see
  Wave 2 and `docs/ownership/AIMGF_OWNERSHIP.md`)
- Model registration (the lifecycle-facing side; MLMR owns the actual
  repository row)
- Training / validation / emulation / inference *requests*
- Certification requests and records
- Runtime status tracking
- NFO invocation (request runtime creation/termination/scaling)

**Does NOT own**
- Model artifacts, versioning, coordination groups → **MLMR**
- Model loading/activation/deployment tracking → **MLLF**
- Data, datasets, feature sets → **DME**
- Analytics reports, predictions, drift → **MDAF**
- Business logic → **rApps**

## MLMR (ML Model Repository)

**Owns**
- `MLModel`, `MLModelVersion`
- `MLModelCoordinationGroup`
- Model metadata, versioning
- Artifact metadata (artifact storage mechanics are unchanged from
  today's `ai-ml-workflow`'s own in-DB `ModelArtifact` elision — see
  `SPEC_AUDIT.md`)

**Does NOT own**
- Lifecycle state, training/validation/emulation/inference requests → **AIMgF**
- Loading, activation, deployment tracking → **MLLF**

## MLLF (ML Loading Function)

**Owns**
- Model loading / unloading
- Model activation / deactivation
- Deployment record and tracking

**Does NOT own**
- Training, validation, repository → **AIMgF** / **MLMR**

## MDAF (Management Data Analytics Function)

**Owns**
- Analytics reports, prediction reports, drift reports
- Retraining recommendations
- Analytics subscriptions
- Knowledge objects

Maps to `specs/5G_APIs/TS28104_MdaNrm.yaml` /
`TS28104_MdaReport.yaml` (already present and audited, see
`SPEC_AUDIT.md`'s RAN Analytics section).

**Does NOT own**
- Training, model repository → **AIMgF** / **MLMR**
- Data storage → **DME**
- Domain-specific analytics use cases (traffic/energy/coverage) stay
  with **RAN Analytics**, which becomes an MDAF consumer rather than
  the analytics-owning service — see the open item on this boundary in
  the Wave 0 discussion; TS 28.104's own `MDAType` enum already spans
  those use cases, so this split is a deliberate product-organization
  choice, not one directly forced by the spec.

## DME (unchanged this wave — restated for completeness)

**Owns**
- Datasets, feature sets
- Data discovery
- Data subscriptions
- Data lineage

**Does NOT own**
- Analytics, predictions → **MDAF**
- Models → **MLMR**

## Intent Service

**Owns**
- `Intent`, `IntentExpectation`, `IntentReport`, `IntentState`
- Intent resolution, intent assurance

Maps to `specs/5G_APIs/TS28312_IntentNrm.yaml` and the
`TS28312_*Expectation.yaml` files (already present).

**Does NOT own**
- A1 policy create/enforce/retract (a genuinely different "policy"
  concept) → stays with **`a1-related/`**, unchanged and untouched by
  this decomposition.

**Correction to the source plan**: the SMO Actions 1/2/3 documents
describe `policy-mgmt` as splitting into a leftover Policy/Rule/
Constraint engine plus a new Intent Service. Checked against the actual
code before writing this matrix: `policy-mgmt/` has no policy/rule/
constraint model or route at all today — its entire surface (`Intent`,
`IntentReport`, `IntentHandlingFunction`) is already Intent-shaped.
Wave 1's real action is renaming `policy-mgmt/` → `intent-service/`,
not extracting one service from a two-concern module — see
`docs/ownership/INTENT_SERVICE_OWNERSHIP.md` for the full note. A real
Policy/Rule/Constraint engine, if ever wanted, is new work for a later
wave.

This split is the natural moment to also resolve the open architecture
question `SPEC_AUDIT.md` already flagged for Policy Mgmt (as it was
still named there): TS 28.312's own NRM containment model
(`IntentHandlingFunction` *contains* `Intent`) implies consumer-side LDN
selection rather than this build's current producer-side push matching.
Intent Service's own design (Wave 3) should settle that question
explicitly rather than carrying the ambiguity
forward into a new service.

## Responsibility matrix (AIMgF / MLMR / MLLF)

The three-way split of `ai-ml-workflow` in one table — this is the
reference a Wave 1 PR checks against when classifying an existing route
or model:

| Function | AIMgF | MLMR | MLLF |
|---|---|---|---|
| Lifecycle state | ✅ | ❌ | ❌ |
| Model metadata | ❌ | ✅ | ❌ |
| Model artifact registry | ❌ | ✅ | ❌ |
| Training request | ✅ | ❌ | ❌ |
| Validation request | ✅ | ❌ | ❌ |
| Emulation request | ✅ | ❌ | ❌ |
| Inference runtime request | ✅ | ❌ | ❌ |
| Load model | ❌ | ❌ | ✅ |
| Activate model | ❌ | ❌ | ✅ |
| Version control | ❌ | ✅ | ❌ |
| NFO invocation | ✅ | ❌ | ❌ |

## Architect's freeze principle

One sentence each, to prevent almost every future ownership dispute:

- **AIMgF** = state + decisions.
- **MLMR** = model truth.
- **MLLF** = deployment truth.
- **NFO** = runtime truth.
- **MDAF** = analytics truth.
- **DME** = data truth.
- **Intent Service** = intent truth.
