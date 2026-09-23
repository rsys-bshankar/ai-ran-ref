"""A1 Related SMOS.

SMO Design v1.3 section 3.9, corrected by A1 Related LLD: the mapping-store
role (section 1.1) is what's actually in scope — enforcementStatus is a
local mirror, never computed here. The five A1-ML operations are dormant
(section 0) — not implemented in this reference build.
"""

import uuid

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client

from .models import A1EIType, A1Policy, PolicyStatusSubscription

app = FastAPI(title="A1 Related SMOS")

KNOWN_POLICY_TYPES = {"ORAN_QoSandTSP_6.0.1", "ORAN_TrafficSteeringPreference_6.0.1"}  # A1TD clause 7.2 catalog sample


class CreatePolicyRequest(BaseModel):
    policyTypeId: str
    policyObject: dict
    nearRtRicId: str
    creatorId: str


class SubscriptionRequest(BaseModel):
    notificationDestination: str
    subscriptionScope: str | None = None  # OWN | OTHERS | ALL
    policyIdList: list[str] | None = None
    policyTypeIdList: list[str] | None = None
    nearRtRicIdList: list[str] | None = None


@app.get("/policy-types")
def query_policy_types(near_rt_ric_id: str | None = None):
    return [{"policyTypeId": t, "nearRtRicId": near_rt_ric_id or "mock-near-rt-ric-001"} for t in KNOWN_POLICY_TYPES]


@app.post("/policies", status_code=201)
def create_policy(body: CreatePolicyRequest, db: Session = Depends(get_session)):
    """Create A1 policy — section 1.1's confirmed thin-envelope sequence:
    AuthZ (via SME, elided in this reference), generate policyId, [ref: A1
    interface call, out of scope], store the policyId<->nearRtRicId
    mapping. That mapping IS the actual in-scope substance.
    """
    if body.policyTypeId not in KNOWN_POLICY_TYPES:
        raise framework_error(FrameworkError.POLICY_TYPE_NOT_SUPPORTED, detail=body.policyTypeId)
    policy = A1Policy(policy_type_id=body.policyTypeId, creator_id=body.creatorId,
                       near_rt_ric_id=body.nearRtRicId, policy_object=body.policyObject,
                       enforcement_status="PENDING")
    db.add(policy)
    db.commit()
    # Phase 1: the actual A1UCR clause 6.3 call to the Near-RT RIC is out of
    # scope — mock endpoint per SMO Design v1.3 section 3.9 (closes RT-7).
    # enforcement_status stays PENDING until a status-sync mechanism (also
    # out of this reference build's scope) updates it.
    return {"policyId": str(policy.policy_id)}


@app.get("/policies/{policy_id}")
def query_policy(policy_id: uuid.UUID, db: Session = Depends(get_session)):
    p = db.get(A1Policy, policy_id)
    return _policy_view(p)


@app.put("/policies/{policy_id}")
def update_policy(policy_id: uuid.UUID, policy_object: dict, db: Session = Depends(get_session)):
    p = db.get(A1Policy, policy_id)
    p.policy_object = policy_object
    db.commit()
    return _policy_view(p)


@app.delete("/policies/{policy_id}", status_code=204)
def delete_policy(policy_id: uuid.UUID, db: Session = Depends(get_session)):
    p = db.get(A1Policy, policy_id)
    if p is not None:
        db.delete(p)
        db.commit()


@app.get("/policies/{policy_id}/status")
def query_policy_status(policy_id: uuid.UUID, db: Session = Depends(get_session)):
    p = db.get(A1Policy, policy_id)
    return {"policyId": str(p.policy_id), "enforcementStatus": p.enforcement_status}  # cached, per section 1.1


@app.post("/policies/subscriptions", status_code=201)
def subscribe_policy_status(body: SubscriptionRequest, db: Session = Depends(get_session)):
    """A1 Related LLD section 1.2: the schema v1.3 never had, closing the
    same 'operation with no backing object' gap found elsewhere.
    """
    if body.subscriptionScope is not None and body.policyIdList is not None:
        raise framework_error(FrameworkError.SUBSCRIPTION_SCOPE_CONFLICT)
    sub = PolicyStatusSubscription(
        notification_destination=body.notificationDestination, subscription_scope=body.subscriptionScope,
        policy_id_list=body.policyIdList, policy_type_id_list=body.policyTypeIdList, near_rt_ric_id_list=body.nearRtRicIdList,
    )
    db.add(sub)
    db.commit()
    return {"subscriptionId": str(sub.subscription_id)}


@app.delete("/policies/subscriptions/{subscription_id}", status_code=204)
def unsubscribe_policy_status(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    sub = db.get(PolicyStatusSubscription, subscription_id)
    if sub is not None:
        db.delete(sub)
        db.commit()


@app.post("/ei-types/register")
def register_ei_type(ei_type_id: str, registered_by: str, dme_namespace: str, dme_name: str, dme_version: str, db: Session = Depends(get_session)):
    """RegisterEIType — A1 Related LLD section 3: NOT a distinct R1AP call
    (clause 9 has no 9.2). This wraps DME's real RegisterDMEType, then
    records the EI bookkeeping entry.
    """
    r1 = R1Client()
    dme_resp = r1.post("/dme/production-capabilities", json={
        "namespace": dme_namespace, "name": dme_name, "version": dme_version,
        "typeName": f"{dme_namespace}.{dme_name}", "producerId": registered_by,
        "dataProductionSchema": {}, "producerHealthCallbackUrl": "http://a1-related:8000/health",
    })
    dme_type_id = dme_resp.json()["registrationId"]
    ei = A1EIType(ei_type_id=ei_type_id, registered_by=registered_by, ei_source_dme_type_id=dme_type_id)
    db.add(ei)
    db.commit()
    return {"eiTypeId": ei.ei_type_id, "eiSourceDmeTypeId": dme_type_id}


@app.delete("/ei-types/{ei_type_id}", status_code=204)
def deregister_ei_type(ei_type_id: str, db: Session = Depends(get_session)):
    ei = db.get(A1EIType, ei_type_id)
    if ei is not None:
        db.delete(ei)
        db.commit()


def _policy_view(p: A1Policy) -> dict:
    return {"policyId": str(p.policy_id), "policyTypeId": p.policy_type_id, "nearRtRicId": p.near_rt_ric_id,
            "policyObject": p.policy_object, "enforcementStatus": p.enforcement_status}
