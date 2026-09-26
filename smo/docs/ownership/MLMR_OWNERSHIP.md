# MLMR Ownership

Status: **frozen** — Wave 0. See `docs/architecture/SERVICE_OWNERSHIP_MATRIX.md`
and `docs/architecture/AI_PLATFORM_BASELINE.md`.

## Mission

MLMR (ML Model Repository) is model truth. It records what a model *is*
— its identity, its versions, its artifacts, its coordination groups —
and nothing about what state it's currently in or whether it's loaded
anywhere.

## Owns

- `MLModel` — the AI asset itself (identity, type, version, description,
  author/owner, artifact location, target environments)
- `MLModelVersion`
- `MLModelRepository` (the containing concept, per TS 28.105)
- `MLModelCoordinationGroup` (member models, retrain propagation —
  already real and unit-tested in this build's `ai-ml-workflow`, moves
  here unchanged)
- Model metadata
- Versioning
- Artifact metadata and storage (this build's own in-DB `ModelArtifact`
  elision — real S3-backed storage stays out of scope, same as today)

## Does NOT own

| Concern | Owner |
|---|---|
| Lifecycle state, training/validation/emulation/inference requests | AIMgF |
| Model loading, activation, deployment tracking | MLLF |

MLMR has **no lifecycle logic** — it never fires a state transition and
never decides whether a model is allowed to move from one state to
another. It answers "what models exist and what do we know about them,"
not "what state is this model in right now."

## Migration source (Wave 1)

Split out of `ai-ml-workflow/app/models.py`: `AIMLModel` (renamed to
`MLModel` to match TS 28.105's own vocabulary — a real rename, not a
copy, so every existing reference needs updating in the same PR),
`MLModelCoordinationGroup`, `ModelArtifact`. The routes
`register_model`/`discover_models`/`get_model`/`update_model`/
`deregister_model`/`upload_model_artifact`/`download_model_artifact`/
`create_coordination_group`/`list_coordination_groups` move here from
`ai-ml-workflow/app/main.py`.

Not migrated here: `training_job_id` as a *foreign key AIMgF reads*
stays conceptually AIMgF's own reference into MLMR, not the other way
around — MLMR does not reach into AIMgF's own job tables.
