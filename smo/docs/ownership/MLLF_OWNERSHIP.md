# MLLF Ownership

Status: **frozen** — Wave 0. See `docs/architecture/SERVICE_OWNERSHIP_MATRIX.md`
and `docs/architecture/AI_PLATFORM_BASELINE.md`.

## Mission

MLLF (ML Loading Function) is deployment truth. It answers "is this
model actually loaded and active anywhere right now," distinct from
AIMgF's "is this model allowed to be active" (a lifecycle-state
question) and MLMR's "does this model exist" (a repository question).

## Owns

- Model loading / unloading
- Model activation / deactivation
- Deployment record and tracking

## Does NOT own

| Concern | Owner |
|---|---|
| Training, validation, emulation, certification | AIMgF |
| Repository, versioning, coordination groups | MLMR |

MLLF has **no lifecycle logic** — same discipline as MLMR. It executes
a load/activate/deactivate operation when asked and records the result;
it does not decide whether a model is *allowed* to load (AIMgF's own
`LOAD_READY` gate, per Wave 2's state machine, is what grants that
permission before MLLF is ever called).

## Migration source (Wave 1)

Today's `ai-ml-workflow` has no dedicated loading/activation surface of
its own beyond `request_model_deployment` (`POST /models/{id}/deploy`,
targeting `cleared_node_groups`) — that route, and a new explicit
load/unload/activate/deactivate surface built out from it, move here.
`request_model_deployment`'s own `cleared_node_groups` concept (the
MultiNode Q2 gap closure already in this build) becomes MLLF's own
deployment-targeting model, not AIMgF's.
