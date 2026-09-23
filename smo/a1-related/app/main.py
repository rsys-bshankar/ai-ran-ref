"""A1 Related SMOS.

SMO Design v1.3 section 3.9, corrected by A1 Related LLD: the mapping-store
role (section 1.1) is what's actually in scope — enforcementStatus is a
local mirror, refreshed from the (mocked) Near-RT RIC via
a1_termination_client rather than fabricated locally. The five A1-ML
operations are dormant (section 0) — not implemented in this reference
build.
"""

import uuid

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client

from .a1_termination_client import A1TerminationClient
from .models import A1EIType, A1Policy, PolicyStatusSubscription

app = FastAPI(title="A1 Related SMOS")

KNOWN_POLICY_TYPES = {"ORAN_QoSandTSP_6.0.1", "ORAN_TrafficSteeringPreference_6.0.1"}  # A1TD clause 7.2 catalog sample


def get_a1_termination_client() -> A1TerminationClient:
    return A1TerminationClient()


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
def create_policy(body: CreatePolicyRequest, db: Session = Depends(get_session), a1t: A1TerminationClient = Depends(get_a1_termination_client)):
    """Create A1 policy — section 1.1's confirmed thin-envelope sequence:
    AuthZ (via SME, elided in this reference), generate policyId, the
    A1UCR clause 6.3 call to the Near-RT RIC (a1_termination_client, the
    mock endpoint per SMO Design v1.3 section 3.9, closes RT-7), then
    store the policyId<->nearRtRicId mapping AND the resulting
    enforcement status. The mapping-store role is still the actual
    in-scope substance — the southbound call is a real dependency now,
    not silently elided.
    """
    if body.policyTypeId not in KNOWN_POLICY_TYPES:
        raise framework_error(FrameworkError.POLICY_TYPE_NOT_SUPPORTED, detail=body.policyTypeId)
    result = a1t.create_policy(body.nearRtRicId, body.policyTypeId, body.policyObject)
    policy = A1Policy(policy_type_id=body.policyTypeId, creator_id=body.creatorId,
                       near_rt_ric_id=body.nearRtRicId, policy_object=body.policyObject,
                       near_rt_ric_policy_id=result.get("policyId"),
                       enforcement_status=result["enforcementStatus"], rejection_reason=result.get("rejectionReason"))
    db.add(policy)
    db.commit()
    return {"policyId": str(policy.policy_id), "enforcementStatus": policy.enforcement_status}


@app.get("/policies/{policy_id}")
def query_policy(policy_id: uuid.UUID, db: Session = Depends(get_session)):
    p = db.get(A1Policy, policy_id)
    return _policy_view(p)


@app.put("/policies/{policy_id}")
def update_policy(policy_id: uuid.UUID, policy_object: dict, db: Session = Depends(get_session), a1t: A1TerminationClient = Depends(get_a1_termination_client)):
    p = db.get(A1Policy, policy_id)
    result = a1t.update_policy(p.near_rt_ric_policy_id, policy_object)
    p.policy_object = policy_object
    p.enforcement_status = result["enforcementStatus"]
    db.commit()
    return _policy_view(p)


@app.delete("/policies/{policy_id}", status_code=204)
def delete_policy(policy_id: uuid.UUID, db: Session = Depends(get_session), a1t: A1TerminationClient = Depends(get_a1_termination_client)):
    p = db.get(A1Policy, policy_id)
    if p is not None:
        a1t.delete_policy(p.near_rt_ric_policy_id)
        db.delete(p)
        db.commit()


@app.get("/policies/{policy_id}/status")
def query_policy_status(policy_id: uuid.UUID, db: Session = Depends(get_session), a1t: A1TerminationClient = Depends(get_a1_termination_client)):
    """R1GAP's cache/pass-through duality (section 1.1): refresh the local
    mirror from a live Near-RT RIC call rather than trusting a
    potentially-stale cached value indefinitely. Uses near_rt_ric_policy_id
    — the Near-RT RIC's OWN identifier for this policy, not our R1-facing
    policy_id (see the model's docstring; caught by the integration suite).
    """
    p = db.get(A1Policy, policy_id)
    result = a1t.query_policy_status(p.near_rt_ric_policy_id)
    p.enforcement_status = result["enforcementStatus"]
    db.commit()
    return {"policyId": str(p.policy_id), "enforcementStatus": p.enforcement_status}


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
    ei = A1EIType(ei_type_id=ei_type_id, registered_by=registered_by, ei_source_dme_type_id=uuid.UUID(dme_type_id))
    db.add(ei)
    db.commit()
    return {"eiTypeId": ei.ei_type_id, "eiSourceDmeTypeId": dme_type_id}


@app.delete("/ei-types/{ei_type_id}", status_code=204)
def deregister_ei_type(ei_type_id: str, db: Session = Depends(get_session)):
    ei = db.get(A1EIType, ei_type_id)
    if ei is not None:
        db.delete(ei)
        db.commit()


@app.get("/health")
def health_check():
    """Producer health-supervision callback (OPEN_ITEMS.md section 5):
    register_ei_type registers this exact URL with DME as its
    producerHealthCallbackUrl, but no route ever answered it — a health
    poller hitting the registered callback would 404 against a producer
    this module itself just told DME was healthy. A plain liveness
    check: reachable and 200 means this A1 Related instance is up.
    """
    return {"status": "healthy"}


def _policy_view(p: A1Policy) -> dict:
    return {"policyId": str(p.policy_id), "policyTypeId": p.policy_type_id, "nearRtRicId": p.near_rt_ric_id,
            "policyObject": p.policy_object, "enforcementStatus": p.enforcement_status}
