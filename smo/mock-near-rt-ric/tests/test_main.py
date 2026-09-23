"""Tests for the mock Near-RT RIC test double (closes RT-7, SMO Design
v1.3 section 3.9). Run with: pytest smo/mock-near-rt-ric/tests -q
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_create_policy_with_object_is_enforced():
    resp = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell1"})
    assert resp.status_code == 201
    assert resp.json()["enforcementStatus"] == "ENFORCED"


def test_create_policy_with_empty_object_is_rejected():
    resp = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={})
    assert resp.status_code == 201  # the mock always accepts the HTTP call; REJECTED is a domain outcome, not an error
    assert resp.json()["enforcementStatus"] == "REJECTED"
    assert resp.json()["rejectionReason"] == "empty policyObject"


def test_query_status_reflects_create_outcome():
    created = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell1"}).json()
    status = client.get(f"/a1-p/policies/{created['policyId']}/status")
    assert status.json()["enforcementStatus"] == "ENFORCED"


def test_query_status_for_unknown_policy():
    status = client.get("/a1-p/policies/does-not-exist/status")
    assert status.json()["enforcementStatus"] == "SUSPENDED"


def test_delete_then_query_returns_unknown():
    created = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell1"}).json()
    client.delete(f"/a1-p/policies/{created['policyId']}")
    status = client.get(f"/a1-p/policies/{created['policyId']}/status")
    assert status.json()["enforcementStatus"] == "SUSPENDED"
