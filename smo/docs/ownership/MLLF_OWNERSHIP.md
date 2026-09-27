# MLLF Ownership

Status: **implemented, scope corrected Wave 3**. See
`docs/architecture/SERVICE_OWNERSHIP_MATRIX.md` and
`docs/architecture/AI_PLATFORM_BASELINE.md`.

## Mission

MLLF (ML Loading Function) is the gate and targeting surface for a
model's deployment request — distinct from AIMgF's "is this model
allowed to be active" (a lifecycle-state question) and MLMR's "does
this model exist" (a repository question).

## Wave 3 correction (was a real open question, not a bug)

Wave 1 deferred a fuller "load/unload/activate/deactivate + deployment
record" surface for MLLF, described below in the original **Owns**
list. Wave 2, built independently for a different reason (AIMgF's own
runtime-lifecycle work with NFO), already answers that exact question:
`RuntimeLifecycleState` (`NOT_DEPLOYED` → `DEPLOYMENT_REQUESTED` →
`DEPLOYED` → `ACTIVATING` → `ACTIVE`, with `SCALING` and
`TERMINATING` → `TERMINATED`) is jointly owned by AIMgF and NFO
(`docs/ownership/AIMGF_OWNERSHIP.md`) and already tracks load/unload/
activate/deactivate for a model's runtime, end to end, with NFO
actually provisioning it.

Building a second, MLLF-owned deployment record for the same concept
would not be closing a gap — it would create two competing sources of
truth for the same question. Confirmed this wave (asked rather than
guessed, given the real overlap risk): MLLF's role is the thin
gate-and-targeting surface it already is, not a second state machine.
The original **Owns** list below described Wave 1's aspiration before
Wave 2's `RuntimeLifecycleState` existed to make it moot; it's
corrected here rather than carried forward as a stale TODO.

## Owns

- The `POST /models/{id}/deploy` gate: requires AIMgF's own
  `ModelLifecycleState` to be `CERTIFIED` or `PROMOTED` (read
  cross-service from AIMgF's `model_lifecycle` row) before a deployment
  request is accepted at all.
- `clearedNodeGroups` targeting — which node groups a CERTIFIED/
  PROMOTED model is cleared to deploy onto. Stamped back onto AIMgF's
  own `model_lifecycle` row (`PATCH /aimgf/models/{id}/runtime/
  node-groups`), since that's where `RuntimeLifecycleState` itself
  lives — MLLF has no row of its own to carry it on.

## Does NOT own

| Concern | Owner |
|---|---|
| Training, validation, emulation, certification | AIMgF |
| Repository, versioning, coordination groups | MLMR |
| Runtime lifecycle (deploy/activate/scale/terminate), the actual load/unload/activate/deactivate state machine | AIMgF (jointly with NFO) |

MLLF has **no lifecycle logic of its own** — same discipline as MLMR.
It gates a deployment request and records targeting; it does not decide
whether a model is *allowed* to load (AIMgF's own gate) and does not
track runtime state itself (AIMgF+NFO's `RuntimeLifecycleState` does).

## Migration source (Wave 1)

Today's `ai-ml-workflow` has no dedicated loading/activation surface of
its own beyond `request_model_deployment` (`POST /models/{id}/deploy`,
targeting `cleared_node_groups`) — that route moved here unchanged.
`request_model_deployment`'s own `cleared_node_groups` concept (the
MultiNode Q2 gap closure already in this build) is MLLF's own
deployment-*targeting* model, not AIMgF's — but the deployment
*lifecycle* itself (Wave 2's `RuntimeLifecycleState`) is AIMgF's, per
the Wave 3 correction above.
