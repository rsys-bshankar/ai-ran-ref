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

import uuid

from fastapi import FastAPI

app = FastAPI(title="Mock Near-RT RIC (A1-P test double)")

_policies: dict[str, dict] = {}


@app.post("/a1-p/policies", status_code=201)
def create_policy(near_rt_ric_id: str, policy_type_id: str, policy_object: dict):
    """A1UCR clause 6.3's create — the call A1 Related LLD section 1.1
    draws as a ref block, out of this project's scope, realized here only
    as a test double. REJECTED on an empty policyObject, purely so the
    reject path has something to actually exercise in tests.
    """
    policy_id = str(uuid.uuid4())
    status = "REJECTED" if not policy_object else "ENFORCED"
    _policies[policy_id] = {"nearRtRicId": near_rt_ric_id, "policyTypeId": policy_type_id, "status": status}
    return {"policyId": policy_id, "enforcementStatus": status,
            "rejectionReason": "empty policyObject" if status == "REJECTED" else None}


@app.put("/a1-p/policies/{policy_id}")
def update_policy(policy_id: str, policy_object: dict):
    record = _policies.get(policy_id)
    if record is None:
        return {"enforcementStatus": "REJECTED", "rejectionReason": "unknown policyId"}
    record["status"] = "REJECTED" if not policy_object else "ENFORCED"
    return {"policyId": policy_id, "enforcementStatus": record["status"]}


@app.delete("/a1-p/policies/{policy_id}", status_code=204)
def delete_policy(policy_id: str):
    _policies.pop(policy_id, None)


@app.get("/a1-p/policies/{policy_id}/status")
def query_policy_status(policy_id: str):
    record = _policies.get(policy_id)
    if record is None:
        return {"enforcementStatus": "SUSPENDED", "rejectionReason": "unknown policyId"}
    return {"policyId": policy_id, "enforcementStatus": record["status"]}
