"""PR-SEC-10.3: an instance is created with a scope claim, which is put on its invoker at SME; an upgrade keeps it and a rollback restores it. And the rApp's own calls to
bootstrap-complete and performance (which the gateway now lets an rApp make) are for its own instance only. PR-GUI-9.3: the `region` / `include_unscoped`
filters of `GET /instances`, matched against the stored claim in SQL.

Fixtures: `client`, `db_session_factory` and `FakeR1Response` from `test_main.py` (a fresh SQLite per test); `sme` below stands in for R1 (SME, NFO,
Onboarding). Run with: `cd smo/rapp-mgmt && PYTHONPATH=.:../shared python -m pytest tests/test_scope.py -q`."""

import uuid

import pytest

from test_main import FakeR1Response, client, db_session_factory  # noqa: F401  (pytest fixtures)

CLAIM = {"regions": ["eu-west"], "tenants": ["acme"]}


@pytest.fixture
def sme(monkeypatch):
    """R1 as rApp Management sees it: SME registers an invoker (and, like the real one, echoes the claim it recorded unless `forgets` is set), NFO and Onboarding answer."""
    seen = {"registrations": [], "deleted": [], "forgets": False}

    def post(self, path, json=None, **kw):
        if path == "/sme/invoker-registrations":
            seen["registrations"].append(json)
            answer = {"apiInvokerId": f"api-invoker-{len(seen['registrations'])}", "onboardingSecret": "s", "role": "rapp"}
            if json.get("authzScope") and not seen["forgets"]:
                answer["authzScope"] = json["authzScope"]
            return FakeR1Response(201, answer)
        if "/nfo/deployments" in path:
            return FakeR1Response(200, {"nfDeploymentId": str(uuid.uuid4())})
        if "/usage/start" in path:
            return FakeR1Response(200, {"registrationId": str(uuid.uuid4())})
        return FakeR1Response(200, {})

    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: FakeR1Response(200, {"state": "AVAILABLE", "nfDeploymentDescriptorId": str(uuid.uuid4())}))
    monkeypatch.setattr("app.main.R1Client.post", post)
    monkeypatch.setattr("app.main.R1Client.delete", lambda self, path, **kw: (seen["deleted"].append(path), FakeR1Response(204, {}))[1])
    monkeypatch.setattr("app.main.R1Client.put", lambda self, path, **kw: FakeR1Response(200, {}))
    return seen


def _create(client, **extra):
    return client.post("/instances", json={"packageId": str(uuid.uuid4()), "config": {}, **extra})


def test_an_instance_without_a_claim_registers_an_unscoped_invoker_as_before(client, sme):
    """An instance created without an authorisation scope registers an invoker with no `authzScope` key at all and shows no scope.
    """
    created = _create(client)
    assert created.status_code == 202
    assert sme["registrations"] == [{"apiInvokerPublicKey": f"rapp-instance:{created.json()['instanceId']}"}]          # no authzScope key at all
    assert client.get(f"/instances/{created.json()['instanceId']}").json()["authzScope"] is None


def test_the_claim_is_put_on_the_invoker_and_shown_on_the_instance(client, sme):
    """The scope given at create is normalised, put on the invoker at SME, and shown in the instance detail and the list."""
    created = _create(client, authzScope={"tenants": ["acme"], "regions": ["eu-west"]}).json()
    assert sme["registrations"][0]["authzScope"] == CLAIM
    assert client.get(f"/instances/{created['instanceId']}").json()["authzScope"] == CLAIM
    listed = client.get("/instances").json()["items"]
    assert [i["authzScope"] for i in listed] == [CLAIM]


# Each row is a scope that is not valid (empty list, wrong type, unknown key, a value with a space, a duplicate): create is 422 AUTHZ_SCOPE_INVALID, nothing is registered and no instance exists.
@pytest.mark.parametrize("claim", [{"regions": []}, {"regions": "eu"}, {"zones": ["z"]}, {"tenants": ["a b"]}, {"regions": ["eu", "eu"]}])
def test_a_claim_that_is_not_valid_creates_nothing(client, sme, claim):
    resp = _create(client, authzScope=claim)
    assert resp.status_code == 422 and resp.json()["detail"]["title"] == "AUTHZ_SCOPE_INVALID"
    assert sme["registrations"] == [] and client.get("/instances").json()["items"] == []


def test_an_sme_that_does_not_record_the_claim_leaves_no_unscoped_identity_behind(client, sme):
    """Fail closed: an SME of the release before this one would ignore the field and hand out an invoker with no limit."""
    sme["forgets"] = True
    resp = _create(client, authzScope=CLAIM)
    assert resp.status_code == 503 and "scope claim" in resp.json()["detail"]["detail"]
    assert sme["deleted"] == ["/sme/invoker-registrations/api-invoker-1"]
    assert client.get("/instances").json()["items"] == []


def test_a_new_credential_carries_the_claim_too(client, sme):
    """Issuing new credentials registers the new invoker with the same scope claim."""
    created = _create(client, authzScope=CLAIM).json()
    assert client.post(f"/instances/{created['instanceId']}/credentials").status_code == 200
    assert [r.get("authzScope") for r in sme["registrations"]] == [CLAIM, CLAIM]


def test_an_upgrade_keeps_the_claim_and_a_rollback_restores_it(client, db_session_factory, sme):
    """The replacement instance and its invoker carry the scope, the version history keeps the old one, and a rollback restores it.
    """
    created = _create(client, authzScope=CLAIM).json()
    client.post(f"/instances/{created['instanceId']}/bootstrap-complete")
    upgraded = client.post(f"/instances/{created['instanceId']}/upgrade", json={"newPackageId": str(uuid.uuid4())})
    assert upgraded.status_code == 200, upgraded.text
    new_id = upgraded.json()["newInstanceId"]
    assert client.get(f"/instances/{new_id}").json()["authzScope"] == CLAIM
    assert sme["registrations"][-1]["authzScope"] == CLAIM                                         # the replacement's own invoker is scoped too
    assert client.post(f"/instances/{created['instanceId']}/upgrade/resolve", params={"succeeded": True}).status_code == 200
    from app.models import RAppInstanceVersion
    with db_session_factory() as db:
        [version] = db.query(RAppInstanceVersion).all()
        assert version.previous_authz_scope == CLAIM
    rolled = client.post(f"/instances/{new_id}/rollback")
    assert rolled.status_code == 200, rolled.text
    assert client.get(f"/instances/{rolled.json()['newInstanceId']}").json()["authzScope"] == CLAIM


# ---- the rApp's own calls

def _two_instances(client, sme):
    return _create(client).json(), _create(client).json()


def test_an_rapp_reports_bootstrap_and_performance_for_its_own_instance_only(client, sme):
    """A caller with the rApp role is refused (403 NOT_THIS_INSTANCE) on another instance's performance and bootstrap-complete, with no effect, while its own instance works.
    """
    mine, other = _two_instances(client, sme)
    own = {"X-R1-Role": "rapp", "X-R1-Invoker-Id": mine["oauthClientId"]}
    refused = client.post(f"/instances/{other['instanceId']}/performance", json={"cpu": 1}, headers=own)
    assert refused.status_code == 403 and refused.json()["detail"]["title"] == "NOT_THIS_INSTANCE"
    assert client.post(f"/instances/{other['instanceId']}/bootstrap-complete", headers=own).status_code == 403
    assert client.get(f"/instances/{other['instanceId']}").json()["state"] == "DEPLOYING"                  # nothing happened
    assert client.get(f"/instances/{other['instanceId']}/performance").json()["items"] == []
    assert client.post(f"/instances/{mine['instanceId']}/performance", json={"cpu": 1}, headers=own).json() == {"status": "recorded"}
    assert client.post(f"/instances/{mine['instanceId']}/bootstrap-complete", headers=own).json()["state"] == "RUNNING"


# Each row is rApp-role headers that do not identify the instance (no invoker id, an empty one, or another's): both the performance and the bootstrap-complete calls are 403 and the instance stays DEPLOYING.
@pytest.mark.parametrize("headers", [{"X-R1-Role": "rapp"}, {"X-R1-Role": "rapp", "X-R1-Invoker-Id": ""}, {"X-R1-Role": "rapp", "X-R1-Invoker-Id": "someone-else"}])
def test_an_rapp_that_is_not_the_instance_is_refused(client, sme, headers):
    mine, _ = _two_instances(client, sme)
    for path, body in (("performance", {"cpu": 1}), ("bootstrap-complete", None)):
        resp = client.post(f"/instances/{mine['instanceId']}/{path}", json=body, headers=headers) if body else client.post(f"/instances/{mine['instanceId']}/{path}", headers=headers)
        assert resp.status_code == 403, path
    assert client.get(f"/instances/{mine['instanceId']}").json()["state"] == "DEPLOYING"


def test_the_limits_are_put_in_force_on_the_platforms_own_account_when_the_rapp_reports_it_is_up(client, monkeypatch):
    """RAN NF OAM refuses a caller that changes its own limit, and a module acting for an rApp is that rApp: so when the rApp itself calls bootstrap-complete the platform
    must not set the limits 'for' it. The originator seen by the calls rApp Management then makes is nobody."""
    from smo_shared.invoker import get_originator
    from test_rapp_limits import _wire
    _wire(monkeypatch, {"configJobsPerHour": 7})
    seen = []

    def put(self, path, json=None, **kw):
        seen.append((path, get_originator()))
        return FakeR1Response(200, {})

    monkeypatch.setattr("app.main.R1Client.put", put)
    created = _create(client).json()
    own = {"X-R1-Role": "rapp", "X-R1-Invoker-Id": created["oauthClientId"]}
    assert client.post(f"/instances/{created['instanceId']}/bootstrap-complete", headers=own).json()["state"] == "RUNNING"
    assert seen == [(f"/ran-nf-oam/rapp-limits/{created['oauthClientId']}", None)]


def test_an_instance_without_a_credential_cannot_be_acted_for_by_an_rapp(client, sme):
    """A terminated instance has no credential, so no rApp can act for it (403)."""
    created = _create(client).json()
    client.post(f"/instances/{created['instanceId']}/terminate")
    resp = client.post(f"/instances/{created['instanceId']}/performance", json={"cpu": 1}, headers={"X-R1-Role": "rapp", "X-R1-Invoker-Id": created["oauthClientId"]})
    assert resp.status_code == 403


def test_an_operator_and_a_call_that_did_not_come_through_the_gateway_are_as_before(client, sme):
    """An internal caller, and a call without gateway headers, may still act for any instance; an unknown instance is 404."""
    mine, other = _two_instances(client, sme)
    internal = {"X-R1-Role": "internal", "X-R1-Invoker-Id": "smo-gui"}
    assert client.post(f"/instances/{mine['instanceId']}/performance", json={"cpu": 1}, headers=internal).status_code == 200
    assert client.post(f"/instances/{other['instanceId']}/bootstrap-complete", headers=internal).status_code == 200
    assert client.post(f"/instances/{mine['instanceId']}/performance", json={"cpu": 2}).status_code == 200
    assert client.post(f"/instances/{uuid.uuid4()}/performance", json={"cpu": 1}, headers=internal).status_code == 404


@pytest.fixture
def scoped_instances(client, sme):
    """Four instances by name: `eu` (regions eu), `eu-us` (regions eu and us), `tenant-only` (tenants acme: no region restriction) and `unscoped`."""
    claims = {"eu": {"regions": ["eu"]}, "eu-us": {"regions": ["eu", "us"]}, "tenant-only": {"tenants": ["acme"]}, "unscoped": None}
    ids = {}
    for name, claim in claims.items():
        ids[name] = _create(client, **({"authzScope": claim} if claim else {})).json()["instanceId"]
    return ids


def _listed(client, **params):
    """The instance ids `GET /instances` answers for `params`, as a set, and the page total."""
    body = client.get("/instances", params=params).json()
    return {i["instanceId"] for i in body["items"]}, body["total"]


def test_region_filter_keeps_the_instances_scoped_to_it_and_the_unscoped(client, scoped_instances):
    """PR-GUI-9.3: by default `region` keeps the instances whose claim lists it AND those that restrict no region (they may act there too)."""
    ids, total = _listed(client, region="us")
    assert ids == {scoped_instances["eu-us"], scoped_instances["tenant-only"], scoped_instances["unscoped"]} and total == 3


def test_region_filter_without_the_unscoped(client, scoped_instances):
    """`include_unscoped=false` keeps only the instances whose `authzScope.regions` names the region; the page total agrees."""
    ids, total = _listed(client, region="eu", include_unscoped="false")
    assert ids == {scoped_instances["eu"], scoped_instances["eu-us"]} and total == 2
    assert _listed(client, region="nowhere", include_unscoped="false") == (set(), 0)


def test_without_region_include_unscoped_changes_nothing(client, scoped_instances):
    """`include_unscoped` alone filters nothing: every instance is listed, as before the filter existed."""
    assert _listed(client, include_unscoped="false")[1] == 4


def test_region_filter_value_is_bounded(client, scoped_instances):
    """An empty region is a 422, not a filter that matches only the unscoped instances."""
    assert client.get("/instances", params={"region": ""}).status_code == 422
