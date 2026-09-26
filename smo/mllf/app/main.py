"""MLLF (ML Loading Function) — TS 28.105 AI/ML NRM realization.

Wave 1 of the AI Platform Service Decomposition: split out of the former
flat `ai-ml-workflow/` module (see docs/architecture/AI_PLATFORM_BASELINE.md
and docs/ownership/MLLF_OWNERSHIP.md). MLLF is deployment truth — it
answers "is this model loaded and active anywhere," distinct from AIMgF's
lifecycle-state question and MLMR's repository question.

Deliberately thin this wave: `ai-ml-workflow` never had a dedicated
load/unload/activate/deactivate surface of its own beyond
`request_model_deployment` (see MLLF_OWNERSHIP.md's own migration-source
note) — that one route moves here unchanged. Building the fuller
load/unload/activate/deactivate surface the ownership doc describes is
new business logic, out of scope for Wave 1's structural split; it's
left for a later wave.
"""

import uuid

from fastapi import FastAPI, HTTPException
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client

app = FastAPI(title="MLLF")

_mlmr = R1Client()


@app.get("/health")
def health_check():
    """Liveness probe. The GUI BFF's GET /modules/status fans out to
    /<module>/health through R1 Termination for every module in parallel.
    """
    return {"status": "healthy"}


@app.post("/models/{model_id}/deploy")
def request_model_deployment(model_id: uuid.UUID, node_groups: list[str]):
    """RequestModelDeployment. Requires CERTIFIED-or-later state (the
    AIMgF gate, unchanged from v1.3, now read cross-service from MLMR)
    and stamps clearedNodeGroups (LLD section 5, MultiNode Q2's
    targeting gap) back onto MLMR's own row — see
    `PATCH /models/{id}/lifecycle` in mlmr/app/main.py.
    """
    resp = _mlmr.get(f"/mlmr/models/{model_id}")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="no such model")
    model = resp.json()
    if model["state"] not in ("CERTIFIED", "LOADED", "ACTIVE"):
        raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED)
    updated = _mlmr.patch(f"/mlmr/models/{model_id}/lifecycle", json={"clearedNodeGroups": node_groups}).json()
    return {"modelId": str(model_id), "clearedNodeGroups": updated["clearedNodeGroups"]}
