"""MLLF (ML Loading Function): the deploy-request gate for a model, as a single route.

Where it sits: one FastAPI app behind R1 Termination at `/mllf`. It has no tables. It reads a model's lifecycle from AIMgF and
writes the cleared node groups back to AIMgF's `model_lifecycle` row, both over R1 (`R1Client`); it never calls MLMR, NFO
or DME and deploys nothing. The design record is `docs/ARCHITECTURE.md` (MLLF); the load / unload / activate / deactivate
state machine is AIMgF's `RuntimeLifecycleState`, not this module's.

What it owns: the rule that only a `CERTIFIED` or `PROMOTED` model may be placed, and the choice of node groups. What it does
not own: lifecycle state and the `clearedNodeGroups` column (AIMgF), the model record (MLMR).

Before editing: the one route's docstring is published in `docs/openapi/mllf.json`. The read of the lifecycle and the write of the
node groups are two separate calls, so the state can change between them.
"""

import uuid

from fastapi import FastAPI, HTTPException
from smo_shared.logconfig import install_logging
from smo_shared.metrics import install_metrics
from smo_shared.health import install_health, sme_token_check
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id

app = FastAPI(title="MLLF")
install_logging(app)  # structured JSON logs and one access-log line per request (PR-OBS-1)
install_metrics(app)  # /metrics and request count/latency series (PR-OBS-2)
apply_r1_gateway_security(app)
apply_correlation_id(app)

# Client for the calls to AIMgF; a module-level instance, which the unit tests replace by patching `R1Client.get` and `R1Client.patch`.
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
    # Gate and record, in this order: read AIMgF's lifecycle of the model (404 MODEL_NOT_FOUND if AIMgF has none), refuse any state
    # other than CERTIFIED and PROMOTED (409 MODEL_NOT_CERTIFIED, which includes DEPRECATED and RETIRED), then replace the model's
    # `clearedNodeGroups` in AIMgF with `node_groups` (a whole-list replace, no merge) and echo what AIMgF stored. Nothing is
    # deployed. The state is read from AIMgF's `model_lifecycle` row, not from MLMR, because lifecycle is not the repository's. An answer
    # from AIMgF other than 200 or 404 on the read is not inspected, and a failed PATCH is not checked.
    resp = _aimgf.get(f"/aimgf/models/{model_id}/lifecycle")
    if resp.status_code == 404:
        raise framework_error(FrameworkError.MODEL_NOT_FOUND, detail="no such model")
    lifecycle = resp.json()
    if lifecycle["modelLifecycleState"] not in ("CERTIFIED", "PROMOTED"):
        raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED,
                               detail=f"cannot place a model in state {lifecycle['modelLifecycleState']}")
    updated = _aimgf.patch(f"/aimgf/models/{model_id}/runtime/node-groups", json={"clearedNodeGroups": node_groups}).json()
    return {"modelId": str(model_id), "clearedNodeGroups": updated["clearedNodeGroups"]}
