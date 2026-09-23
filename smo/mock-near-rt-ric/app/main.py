"""Mock Near-RT RIC — the isolated A1-P test double.

SMO Design v1.3 section 3.9: "Mock Near-RT RIC endpoint isolation
requirement made explicit in topology (closes RT-7)". This is that
endpoint. It is NOT an SMO module and NOT reachable through R1
Termination — A1 Related SMOS is the only caller (per A1 Related LLD
section 1.1's confirmed sequence, the ref-block "A1UCR clause 6.3 — an
actual A1AP call to the Near-RT RIC — OUT OF SCOPE" for this project).
Kept on its own isolated docker-compose network segment, no published
ports (see docker-compose.yml's a1_mock_net).

Deliberately not a real A1AP implementation — no A1TD schema validation,
no A1AP transport (TLS+mTLS+OAuth2.0+JWT per A1TP is elided). Its only
job is to give A1 Related SMOS something real to call so
enforcementStatus can transition away from PENDING, closing the loop
this reference build would otherwise leave permanently open.
"""

import json
import uuid

from fastapi import FastAPI

app = FastAPI(title="Mock Near-RT RIC (A1-P test double)")

_policies: dict[str, dict] = {}
_fingerprints: dict[str, str] = {}  # policy_id -> content fingerprint. ADOPT from the real near-rt-ric-simulator's own policy_fingerprint dict (a1_mediator_controller.py)


def _fingerprint(policy_object: dict, policy_type_id: str) -> str:
    """ADOPT from the real near-rt-ric-simulator's own calcFingerprint
    (utils.py): a stable content fingerprint scoped by policy type, so
    identical policyObject content under a different type never
    collides. Not the reference's own ad hoc sorted-key string
    concatenation — json.dumps(sort_keys=True) is the standard-library
    equivalent for the same practical effect (a stable, order-
    independent content signature). The reference's other check
    ("reused id across types") doesn't apply here: our policyId is
    always freshly server-generated, never caller-supplied, so it can
    never collide with an existing one.
    """
    return json.dumps(policy_object, sort_keys=True) + ":" + policy_type_id


@app.post("/a1-p/policies", status_code=201)
def create_policy(near_rt_ric_id: str, policy_type_id: str, policy_object: dict):
    """A1UCR clause 6.3's create — the call A1 Related LLD section 1.1
    draws as a ref block, out of this project's scope, realized here only
    as a test double. REJECTED on an empty policyObject, purely so the
    reject path has something to actually exercise in tests.

    OPEN_ITEMS.md section 5: also REJECTED on duplicate policy content
    for the same policy type — the real near-rt-ric-simulator's own
    fingerprint check (a1_mediator_controller.py's is_duplicate_check()
    path), previously entirely unenforced here (only the empty-object
    check existed).
    """
    policy_id = str(uuid.uuid4())
    if not policy_object:
        status, reason = "REJECTED", "empty policyObject"
    else:
        fp = _fingerprint(policy_object, policy_type_id)
        if fp in _fingerprints.values():
            status, reason = "REJECTED", "duplicate policy content for this type"
        else:
            status, reason = "ENFORCED", None
            _fingerprints[policy_id] = fp
    _policies[policy_id] = {"nearRtRicId": near_rt_ric_id, "policyTypeId": policy_type_id, "status": status}
    return {"policyId": policy_id, "enforcementStatus": status, "rejectionReason": reason}


@app.put("/a1-p/policies/{policy_id}")
def update_policy(policy_id: str, policy_object: dict):
    """OPEN_ITEMS.md section 5: same duplicate-content check as create,
    matching the reference's own PUT-is-create-or-update semantics — a
    new fingerprint colliding with a DIFFERENT policy's is rejected
    (updating a policy back to its own current content is not a
    collision with itself).
    """
    record = _policies.get(policy_id)
    if record is None:
        return {"enforcementStatus": "REJECTED", "rejectionReason": "unknown policyId"}
    if not policy_object:
        record["status"] = "REJECTED"
        _fingerprints.pop(policy_id, None)
        return {"policyId": policy_id, "enforcementStatus": "REJECTED", "rejectionReason": "empty policyObject"}
    fp = _fingerprint(policy_object, record["policyTypeId"])
    duplicate_of_other = any(pid != policy_id and other_fp == fp for pid, other_fp in _fingerprints.items())
    if duplicate_of_other:
        record["status"] = "REJECTED"
        return {"policyId": policy_id, "enforcementStatus": "REJECTED", "rejectionReason": "duplicate policy content for this type"}
    record["status"] = "ENFORCED"
    _fingerprints[policy_id] = fp
    return {"policyId": policy_id, "enforcementStatus": "ENFORCED", "rejectionReason": None}


@app.delete("/a1-p/policies/{policy_id}", status_code=204)
def delete_policy(policy_id: str):
    _policies.pop(policy_id, None)
    _fingerprints.pop(policy_id, None)


@app.get("/a1-p/policies/{policy_id}/status")
def query_policy_status(policy_id: str):
    record = _policies.get(policy_id)
    if record is None:
        return {"enforcementStatus": "SUSPENDED", "rejectionReason": "unknown policyId"}
    return {"policyId": policy_id, "enforcementStatus": record["status"]}
