"""Tests for the mock Near-RT RIC test double (closes RT-7, SMO Design
v1.3 section 3.9). Run with: pytest smo/mock-near-rt-ric/tests -q
"""

import pytest
from fastapi.testclient import TestClient

from app.main import _fingerprints, _policies, app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_mock_state():
    """_policies/_fingerprints are plain module-level dicts (this is a
    Phase 1 test double, not a real persistent service) — without this,
    OPEN_ITEMS.md section 5's new duplicate-content check would see
    every test's identical {"scope": "cell1"} payload under "t1" as a
    duplicate of whichever test happened to run first.
    """
    _policies.clear()
    _fingerprints.clear()


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


def test_delete_unknown_policy_is_idempotent():
    """dict.pop(id, None) — deleting an id that was never created (or
    already deleted) must not raise, matching a real A1-P DELETE's
    idempotent semantics.
    """
    resp = client.delete("/a1-p/policies/does-not-exist")
    assert resp.status_code == 204


def test_update_policy_with_object_is_enforced():
    """UpdatePolicy (PUT /a1-p/policies/{id}) had no test coverage at all
    before this pass — only create/query/delete were exercised.
    """
    created = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell1"}).json()
    resp = client.put(f"/a1-p/policies/{created['policyId']}", json={"scope": "cell2"})
    assert resp.status_code == 200
    assert resp.json()["enforcementStatus"] == "ENFORCED"

    status = client.get(f"/a1-p/policies/{created['policyId']}/status")
    assert status.json()["enforcementStatus"] == "ENFORCED"


def test_update_policy_with_empty_object_is_rejected():
    created = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell1"}).json()
    resp = client.put(f"/a1-p/policies/{created['policyId']}", json={})
    assert resp.json()["enforcementStatus"] == "REJECTED"

    status = client.get(f"/a1-p/policies/{created['policyId']}/status")
    assert status.json()["enforcementStatus"] == "REJECTED"


def test_update_unknown_policy_returns_rejected_without_crashing():
    resp = client.put("/a1-p/policies/does-not-exist", json={"scope": "cell1"})
    assert resp.status_code == 200
    assert resp.json()["enforcementStatus"] == "REJECTED"
    assert resp.json()["rejectionReason"] == "unknown policyId"


def test_policies_are_tracked_independently():
    """Two distinct policyIds in the shared _policies dict must never
    cross-contaminate each other's status.
    """
    enforced = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell1"}).json()
    rejected = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric2", "policy_type_id": "t2"}, json={}).json()

    assert client.get(f"/a1-p/policies/{enforced['policyId']}/status").json()["enforcementStatus"] == "ENFORCED"
    assert client.get(f"/a1-p/policies/{rejected['policyId']}/status").json()["enforcementStatus"] == "REJECTED"


def test_create_policy_rejects_duplicate_content_for_the_same_type():
    """OPEN_ITEMS.md section 5: the real near-rt-ric-simulator's own
    fingerprint-based duplicate check (a1_mediator_controller.py) was
    entirely unenforced here — a second, byte-identical policyObject
    under the same type used to be accepted without complaint.
    """
    first = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell1"}).json()
    assert first["enforcementStatus"] == "ENFORCED"

    second = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell1"}).json()
    assert second["enforcementStatus"] == "REJECTED"
    assert second["rejectionReason"] == "duplicate policy content for this type"
    assert second["policyId"] != first["policyId"]


def test_create_policy_allows_identical_content_under_a_different_type():
    """The fingerprint is scoped by policy type — the same content is
    legitimately reusable across distinct types.
    """
    client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell1"})
    resp = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t2"}, json={"scope": "cell1"})
    assert resp.json()["enforcementStatus"] == "ENFORCED"


def test_create_policy_allows_different_content_under_the_same_type():
    client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell1"})
    resp = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell2"})
    assert resp.json()["enforcementStatus"] == "ENFORCED"


def test_update_policy_rejects_content_duplicating_a_different_policy():
    other = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell1"}).json()
    target = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell2"}).json()

    resp = client.put(f"/a1-p/policies/{target['policyId']}", json={"scope": "cell1"})
    assert resp.json()["enforcementStatus"] == "REJECTED"
    assert resp.json()["rejectionReason"] == "duplicate policy content for this type"

    # target's own status genuinely flips to REJECTED, not left stale as ENFORCED
    status = client.get(f"/a1-p/policies/{target['policyId']}/status")
    assert status.json()["enforcementStatus"] == "REJECTED"
    # the other policy this collided with is untouched
    assert client.get(f"/a1-p/policies/{other['policyId']}/status").json()["enforcementStatus"] == "ENFORCED"


def test_update_policy_to_its_own_current_content_is_not_a_self_collision():
    created = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell1"}).json()
    resp = client.put(f"/a1-p/policies/{created['policyId']}", json={"scope": "cell1"})
    assert resp.json()["enforcementStatus"] == "ENFORCED"


def test_delete_frees_up_its_content_fingerprint_for_reuse():
    created = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell1"}).json()
    client.delete(f"/a1-p/policies/{created['policyId']}")

    resp = client.post("/a1-p/policies", params={"near_rt_ric_id": "ric1", "policy_type_id": "t1"}, json={"scope": "cell1"})
    assert resp.json()["enforcementStatus"] == "ENFORCED"
