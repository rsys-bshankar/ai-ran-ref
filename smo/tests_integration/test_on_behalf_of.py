"""An rApp that writes through DME is still held by its own safeguards at RAN NF OAM.

DME mediates an rApp's action and posts the write to RAN NF OAM itself. Seen from RAN NF OAM that call is DME's, so before `X-R1-On-Behalf-Of` an rApp
that wrote through DME (the way the SDK and every sample rApp do) could not be stopped by its kill switch and was not held to its limits. The headers
below are what R1 Termination stamps on the inbound call (the in-process mesh has no gateway): `X-R1-Role` and the rApp's own `X-R1-Invoker-Id`.
"""

import pytest

RAPP = {"X-R1-Role": "rapp", "X-R1-Invoker-Id": "es-client"}
OTHER_RAPP = {"X-R1-Role": "rapp", "X-R1-Invoker-Id": "other-client"}


def _action(mesh, headers, elements=("ME-1",)):
    return mesh["dme"].post("/actions", headers=headers, json={"requestedBy": "es-rapp", "scope": "cell", "changes": [
        {"managedElementRef": ref, "attributeChanges": {"txPower": 12}} for ref in elements]})


def _title(response):
    return response.json()["detail"]["title"] if response.status_code >= 400 else None


@pytest.fixture
def stopped_rapp(mesh):
    resp = mesh["ran-nf-oam"].put("/rapp-kill/es-client", json={"requestedBy": "alice", "reason": "oscillating"})
    assert resp.status_code == 200
    return mesh


def test_a_stopped_rapp_cannot_write_through_dme(stopped_rapp):
    refused = _action(stopped_rapp, RAPP)
    assert refused.status_code == 403 and _title(refused) == "RAPP_KILLED", refused.text
    assert [a["status"] for a in stopped_rapp["dme"].get("/actions").json()["items"]] == ["REJECTED"]


def test_stopping_one_rapp_does_not_stop_another_or_dme_itself(stopped_rapp):
    assert _title(_action(stopped_rapp, OTHER_RAPP)) != "RAPP_KILLED"
    assert _title(_action(stopped_rapp, {"X-R1-Role": "internal", "X-R1-Invoker-Id": "dme-client"})) != "RAPP_KILLED"      # a module on its own account


def test_an_rapp_cannot_pose_as_another_to_escape_its_own_stop(stopped_rapp):
    """The stopped rApp names an unstopped one as the originator: R1 drops that header from an rApp, and a backend would not believe it from one."""
    sneaky = _action(stopped_rapp, {**RAPP, "X-R1-On-Behalf-Of": "other-client"})
    assert sneaky.status_code == 403 and _title(sneaky) == "RAPP_KILLED"


def test_the_limits_of_an_rapp_apply_to_what_it_writes_through_dme(mesh):
    assert mesh["ran-nf-oam"].put("/rapp-limits/es-client", json={"maxElementsPerJob": 1}).status_code == 200
    refused = _action(mesh, RAPP, elements=("ME-1", "ME-2"))
    assert refused.status_code == 403 and _title(refused) == "RAPP_BLAST_RADIUS_EXCEEDED", refused.text
    assert _title(_action(mesh, OTHER_RAPP, elements=("ME-1", "ME-2"))) != "RAPP_BLAST_RADIUS_EXCEEDED"        # another rApp has no such limit


def test_the_refusal_is_recorded_against_the_rapp_not_against_dme(stopped_rapp):
    _action(stopped_rapp, RAPP)
    [refusal] = stopped_rapp["ran-nf-oam"].get("/safeguard-refusals").json()["items"]
    assert refusal["invokerId"] == "es-client" and refusal["refusal"] == "RAPP_KILLED"
