"""The dispatch table SO SMOS LLD section 1 closes — the real payoff of
having done every other module's LLD first. stepType x targetModule maps
to one concrete R1 call against a module that already exists as a real
service in this repo, not a placeholder.
"""

from smo_shared.r1_client import R1Client


class DownstreamError(Exception):
    """A downstream module answered (no transport-level exception), but
    with an error status. Caught while integration-testing: every
    dispatcher here was returning resp.json() unconditionally, so a real
    422 from A1 Related (an unknown policyTypeId) was recorded as
    COMPLETED with the error body as the "result" — execute_order's
    fail-fast logic only ever catches raised exceptions, never a
    successfully-received error response, so this silently defeated
    section 1.1's whole fail-fast guarantee until fixed.
    """


def _ensure_ok(resp) -> dict:
    if resp.status_code >= 400:
        raise DownstreamError(f"{resp.status_code}: {resp.json()}")
    return resp.json()


def dispatch_config(r1: R1Client, step: dict) -> dict:
    resp = r1.post("/ran-nf-oam/config-jobs", json={
        "requestedBy": step.get("requestedBy", "so-smos"),
        "scope": step["scope"],
        "changes": step["changes"],
        "msacRole": step.get("msacRole"),
    })
    return _ensure_ok(resp)


def dispatch_deploy(r1: R1Client, step: dict) -> dict:
    resp = r1.post("/nfo/deployments", json={
        "nfDeploymentDescriptorId": step["nfDeploymentDescriptorId"],
        "name": step.get("name") or f"deploy-{step['nfDeploymentDescriptorId']}",  # NFO's own duplication guard (OPEN_ITEMS.md section 5) needs a real name
        "requiredResourceTypeId": step.get("requiredResourceTypeId"),
    })
    return _ensure_ok(resp)


def dispatch_infra(r1: R1Client, step: dict) -> dict:
    resp = r1.post("/focom/resources/provision", json=step.get("spec", {}))
    return _ensure_ok(resp)


def dispatch_training(r1: R1Client, step: dict) -> dict:
    resp = r1.post("/aimgf/training-jobs", json={
        "modelId": step.get("modelId"),
        "modelCoordinationGroupId": step.get("modelCoordinationGroupId"),
        "producerId": step.get("producerId", "so-smos"),
        "requiredData": step.get("requiredData", {}),
        "validationCriteria": step.get("validationCriteria", {}),
    })
    return _ensure_ok(resp)


def dispatch_policy(r1: R1Client, step: dict) -> dict:
    resp = r1.post("/a1-related/policies", json={
        "policyTypeId": step["policyTypeId"],
        "policyObject": step["policyObject"],
        "nearRtRicId": step["nearRtRicId"],
        "creatorId": step.get("creatorId", "so-smos"),
    })
    return _ensure_ok(resp)


def dispatch_validation(r1: R1Client, step: dict) -> dict:
    """OPEN_ITEMS.md section 6.6, closed: `DISPATCH_TABLE` previously had
    only a TRAINING entry for the whole AI/ML pipeline — Validation could
    only ever be reached by calling AIMgF directly, never composed into a
    multi-step `ServiceOrder` the way Training could. Same request shape
    as AIMgF's own `RequestValidation` (call flow 02).
    """
    resp = r1.post("/aimgf/validation-jobs", json={
        "modelId": step.get("modelId"),
        "trainingJobId": step.get("trainingJobId"),
        "producerId": step.get("producerId", "so-smos"),
        "validationCriteria": step.get("validationCriteria", {}),
    })
    return _ensure_ok(resp)


def dispatch_emulation(r1: R1Client, step: dict) -> dict:
    """OPEN_ITEMS.md section 6.6, closed: same gap as dispatch_validation,
    for Emulation.
    """
    resp = r1.post("/aimgf/emulation-jobs", json={
        "modelId": step.get("modelId"),
        "producerId": step.get("producerId", "so-smos"),
        "emulationCriteria": step.get("emulationCriteria", {}),
    })
    return _ensure_ok(resp)


def dispatch_model_runtime_deploy(r1: R1Client, step: dict) -> dict:
    """OPEN_ITEMS.md section 6.6, closed: a model-runtime deploy
    (AIMgF's own `RequestModelRuntimeDeploy`, call flow 17) — deliberately
    a separate (stepType, targetModule) key from `("DEPLOY", "NFO")`
    above, which dispatches a workload's own NFO deployment, not a
    certified model's serving runtime. Requires `ModelLifecycleState` in
    {CERTIFIED, PROMOTED}; that guard fires inside AIMgF itself, so an
    ungated deploy attempt surfaces as an ordinary DownstreamError here,
    same fail-fast handling as every other dispatcher.
    """
    resp = r1.post(f"/aimgf/models/{step['modelId']}/runtime/deploy")
    return _ensure_ok(resp)


def dispatch_inference(r1: R1Client, step: dict) -> dict:
    """OPEN_ITEMS.md section 6.6, closed: same gap as dispatch_validation,
    for Inference (AIMgF's own `RequestInference`, gated on
    `RuntimeLifecycleState.ACTIVE`, not composable via this table before now).
    """
    params = {"notification_destination": step["notificationDestination"]} if step.get("notificationDestination") else None
    resp = r1.post(f"/aimgf/models/{step['modelId']}/inference-jobs", params=params)
    return _ensure_ok(resp)


# stepType -> (targetModule, dispatcher) — SO SMOS LLD section 1's table, made executable
DISPATCH_TABLE = {
    ("CONFIG", "RAN_NF_OAM"): dispatch_config,
    ("DEPLOY", "NFO"): dispatch_deploy,
    ("INFRA", "FOCOM"): dispatch_infra,
    ("TRAINING", "AI_ML_WORKFLOW"): dispatch_training,
    ("VALIDATION", "AI_ML_WORKFLOW"): dispatch_validation,
    ("EMULATION", "AI_ML_WORKFLOW"): dispatch_emulation,
    ("DEPLOY", "AIMGF"): dispatch_model_runtime_deploy,
    ("INFERENCE", "AI_ML_WORKFLOW"): dispatch_inference,
    ("POLICY", "A1_RELATED"): dispatch_policy,
}


def execute_order(r1: R1Client, steps: list[dict]) -> list[dict]:
    """SO SMOS LLD section 1.1's decision: sequential, fail-fast. The first
    FAILED step halts the order; subsequent steps stay PENDING (never
    attempted); completed steps are NOT auto-rolled-back — no compensating
    transaction mechanism exists in Phase 1, matching the Phase-1-thin
    pattern applied everywhere else in this framework.
    """
    results = []
    halted = False
    for step in steps:
        if halted:
            results.append({**step, "status": "PENDING"})
            continue
        key = (step["stepType"], step["targetModule"])
        dispatcher = DISPATCH_TABLE.get(key)
        if dispatcher is None:
            results.append({**step, "status": "FAILED", "error": f"no dispatcher for {key}"})
            halted = True
            continue
        try:
            outcome = dispatcher(r1, step)
            results.append({**step, "status": "COMPLETED", "result": outcome})
        except Exception as exc:  # noqa: BLE001 — Phase 1: any downstream failure halts the order
            results.append({**step, "status": "FAILED", "error": str(exc)})
            halted = True
    return results
