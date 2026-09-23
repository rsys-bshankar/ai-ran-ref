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
        "requiredResourceTypeId": step.get("requiredResourceTypeId"),
    })
    return _ensure_ok(resp)


def dispatch_infra(r1: R1Client, step: dict) -> dict:
    resp = r1.post("/focom/resources/provision", json=step.get("spec", {}))
    return _ensure_ok(resp)


def dispatch_training(r1: R1Client, step: dict) -> dict:
    resp = r1.post("/ai-ml-workflow/training-jobs", json={
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


# stepType -> (targetModule, dispatcher) — SO SMOS LLD section 1's table, made executable
DISPATCH_TABLE = {
    ("CONFIG", "RAN_NF_OAM"): dispatch_config,
    ("DEPLOY", "NFO"): dispatch_deploy,
    ("INFRA", "FOCOM"): dispatch_infra,
    ("TRAINING", "AI_ML_WORKFLOW"): dispatch_training,
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
