"""MLLF (ML Loading Function) — TS 28.105 AI/ML NRM realization.

Wave 1 of the AI Platform Service Decomposition split this module out of
the former flat `ai-ml-workflow/` (see the MLLF section of
docs/ARCHITECTURE.md). MLLF answers "may this model be loaded on these
node groups" — the deployment gate — distinct from AIMgF's lifecycle
state and MLMR's repository.

Deliberately thin: `ai-ml-workflow` never had a dedicated
load/unload/activate/deactivate surface of its own beyond
`request_model_deployment` (see docs/ARCHITECTURE.md's MLLF migration-source
note) — that one route moves here unchanged since Wave 1. Wave 2 only
repoints its gate/write-back at AIMgF's own `model_lifecycle` row instead
of MLMR's (Wave 1's `PATCH /mlmr/models/{id}/lifecycle` is gone —
lifecycle/node-group state was never MLMR's to carry).

Wave 3 correction (docs/ARCHITECTURE.md (MLLF)): the fuller
load/unload/activate/deactivate surface once described as future work
for MLLF turned out to already exist — AIMgF's own `RuntimeLifecycleState`
(built jointly with NFO in Wave 2) already answers that question.
This module stays exactly the gate-and-targeting surface it already
is; no code change, ownership docs corrected instead.
"""

import uuid

from fastapi import FastAPI, HTTPException
from smo_shared.logconfig import install_logging
from smo_shared.health import install_health, sme_token_check
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id

app = FastAPI(title="MLLF")
install_logging(app)  # structured JSON logs and one access-log line per request (PR-OBS-1)
apply_r1_gateway_security(app)
apply_correlation_id(app)

_aimgf = R1Client()


install_health(app, checks=[sme_token_check])  # /live, /ready and the /health alias (PR-ST-7)


@app.post("/models/{model_id}/deploy")
def request_model_deployment(model_id: uuid.UUID, node_groups: list[str]):
    """RequestModelDeployment. Requires CERTIFIED-or-PROMOTED
    ModelLifecycleState (the AIMgF gate, unchanged from v1.3, now read
    cross-service from AIMgF's own `model_lifecycle` row) and stamps
    clearedNodeGroups (LLD section 5, MultiNode Q2's targeting gap) back
    onto that same row — see `PATCH /aimgf/models/{id}/runtime/node-groups`.
    """
    resp = _aimgf.get(f"/aimgf/models/{model_id}/lifecycle")
    if resp.status_code == 404:
        raise framework_error(FrameworkError.MODEL_NOT_FOUND, detail="no such model")
    lifecycle = resp.json()
    if lifecycle["modelLifecycleState"] not in ("CERTIFIED", "PROMOTED"):
        raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED,
                               detail=f"cannot place a model in state {lifecycle['modelLifecycleState']}")
    updated = _aimgf.patch(f"/aimgf/models/{model_id}/runtime/node-groups", json={"clearedNodeGroups": node_groups}).json()
    return {"modelId": str(model_id), "clearedNodeGroups": updated["clearedNodeGroups"]}
