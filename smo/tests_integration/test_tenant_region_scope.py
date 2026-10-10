"""PR-SEC-10 across modules: the scope claim R1 Termination stamps travels with an rApp through the SMO module that acts for it (DME), so writing through DME is no way
round the scope, and the module that owns the target (RAN NF OAM) decides. The in-process mesh has no gateway: the headers below are what R1 Termination would stamp on
the inbound call (its own tests, r1-termination/tests/test_scope.py, prove it stamps exactly these and drops what a caller sent).
"""

import json

import pytest
from sqlalchemy import text

RAPP = {"X-R1-Role": "rapp", "X-R1-Invoker-Id": "es-client"}


def scoped(**claim):
    return {**RAPP, "X-R1-Scope": json.dumps(claim, sort_keys=True, separators=(",", ":"))}


@pytest.fixture
def places(mesh, db_connection):
    for ref, region, tenant in (("ME-EU", "eu", "acme"), ("ME-US", "us", "acme"), ("ME-NONE", None, None)):
        db_connection.execute(text("INSERT INTO managed_entity (managed_element_ref, entity_type, o1_protocol, cell_guards, region, tenant) "
                                   "VALUES (:ref, 'O-DU', 'NETCONF', '{}', :region, :tenant)"), {"ref": ref, "region": region, "tenant": tenant})
    return mesh


def _action(mesh, headers, elements):
    return mesh["dme"].post("/actions", headers=headers, json={"requestedBy": "es-rapp", "scope": "cell", "changes": [
        {"managedElementRef": ref, "attributeChanges": {"txPower": 12}} for ref in elements]})


def _title(response):
    return response.json()["detail"]["title"] if response.status_code >= 400 else None


def test_an_rapp_scoped_to_a_region_cannot_write_to_another_through_dme(places):
    refused = _action(places, scoped(regions=["eu"]), ["ME-US"])
    assert refused.status_code == 403 and _title(refused) == "SCOPE_DENIED", refused.text
    assert [a["status"] for a in places["dme"].get("/actions").json()["items"]] == ["REJECTED"]


def test_one_element_outside_the_scope_refuses_the_whole_action(places):
    refused = _action(places, scoped(regions=["eu"]), ["ME-EU", "ME-US"])
    assert refused.status_code == 403 and _title(refused) == "SCOPE_DENIED"
    assert places["ran-nf-oam"].get("/config-jobs").json()["items"] == []


def test_an_element_without_a_region_is_not_for_a_scoped_rapp(places):
    assert _title(_action(places, scoped(regions=["eu"]), ["ME-NONE"])) == "SCOPE_DENIED"
    assert _title(_action(places, scoped(tenants=["acme"]), ["ME-NONE"])) == "SCOPE_DENIED"


@pytest.mark.parametrize("headers", [RAPP, {"X-R1-Role": "internal", "X-R1-Invoker-Id": "dme-client"}])
def test_an_unscoped_rapp_and_a_module_on_its_own_account_are_unchanged(places, headers):
    for element in ("ME-EU", "ME-US", "ME-NONE"):
        assert _title(_action(places, headers, [element])) != "SCOPE_DENIED", element


def test_an_element_inside_the_scope_is_not_refused_by_it(places):
    assert _title(_action(places, scoped(regions=["eu"], tenants=["acme"]), ["ME-EU"])) != "SCOPE_DENIED"


def test_an_rapp_cannot_pose_as_an_unscoped_one_by_naming_another_originator(places):
    """The scoped rApp names an unscoped originator: a backend does not believe that header from an rApp, and the claim it carries is its own."""
    sneaky = _action(places, {**scoped(regions=["eu"]), "X-R1-On-Behalf-Of": "other-client"}, ["ME-US"])
    assert sneaky.status_code == 403 and _title(sneaky) == "SCOPE_DENIED"


def test_the_refusal_is_recorded_against_the_rapp_not_against_dme(places):
    _action(places, scoped(regions=["eu"]), ["ME-US"])
    [refusal] = places["ran-nf-oam"].get("/safeguard-refusals").json()["items"]
    assert refusal["invokerId"] == "es-client" and refusal["refusal"] == "SCOPE_DENIED"


def test_the_reads_through_the_modules_are_scoped_too(places):
    eu = scoped(regions=["eu"])
    assert [e["managedElementRef"] for e in places["ran-nf-oam"].get("/managed-entities", headers=eu).json()["items"]] == ["ME-EU"]
    assert places["ran-nf-oam"].get("/managed-entities/ME-US/config", headers=eu).status_code == 403
    assert len(places["ran-nf-oam"].get("/managed-entities", headers=RAPP).json()["items"]) == 3


def test_the_actions_dme_lists_are_those_inside_the_callers_scope(places):
    """PR-SEC-10.7: DME has no region or tenant, so it asks RAN NF OAM which elements the rApp's claim covers (the claim travels with the call, as for a write)."""
    eu = scoped(regions=["eu"])
    inside = _action(places, RAPP, ["ME-EU"]).json()["actionId"]                         # an unscoped rApp wrote to an eu element and a us one
    outside = _action(places, RAPP, ["ME-US"]).json()["actionId"]
    mixed = _action(places, RAPP, ["ME-EU", "ME-US"]).json()["actionId"]
    listed = lambda headers: {a["actionId"] for a in places["dme"].get("/actions", headers=headers).json()["items"]}      # noqa: E731
    assert listed(RAPP) == listed({}) == {inside, outside, mixed}
    assert listed(eu) == {inside} and places["dme"].get("/actions", headers=eu).json()["total"] == 1
    assert listed(scoped(regions=["nowhere"])) == set()
    assert places["dme"].get(f"/actions/{inside}", headers=eu).status_code == 200
    for hidden in (outside, mixed):
        assert places["dme"].get(f"/actions/{hidden}", headers=eu).status_code == 404
    assert places["dme"].get(f"/actions/{outside}", headers=RAPP).status_code == 200
    assert listed({"X-R1-Role": "internal", "X-R1-Invoker-Id": "gui-invoker"}) == {inside, outside, mixed}


def test_a_scoped_rapp_reads_its_own_jobs_through_dme_and_not_an_operators(places):
    """A scoped rApp can read the job DME forwarded for it, but not an operator's job on the same element, through the real DME and RAN NF OAM modules."""
    eu = scoped(regions=["eu"])
    mine = _action(places, eu, ["ME-EU"]).json()["forwardedJobId"]
    operator = {"X-R1-Role": "internal", "X-R1-Invoker-Id": "gui-invoker"}
    operators = places["ran-nf-oam"].post("/config-jobs", headers=operator, json={
        "requestedBy": "alice", "scope": "cell", "changes": [{"managedElementRef": "ME-EU", "attributeChanges": {"txPower": 3}}]}).json()["jobId"]
    assert places["ran-nf-oam"].get(f"/config-jobs/{mine}", headers=eu).status_code == 200
    assert places["ran-nf-oam"].get(f"/config-jobs/{operators}", headers=eu).status_code == 404
    assert places["ran-nf-oam"].get(f"/config-jobs/{operators}", headers=RAPP).status_code == 200            # an rApp nobody scoped: as before
    assert {j["jobId"] for j in places["ran-nf-oam"].get("/config-jobs", headers=eu).json()["items"]} == {mine}
