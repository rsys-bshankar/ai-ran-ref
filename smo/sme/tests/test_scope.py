"""PR-SEC-10.3: the scope claim of an invoker (docs/adr/0005-tenant-region-authorization.md). Set at registration or by an operator, returned by token introspection
as `authz_scope`, never widened by a scoped caller registering another invoker."""

import pytest

from smo_shared.scope import ON_BEHALF_SCOPE_HEADER, SCOPE_HEADER

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)

CLAIM = {"regions": ["eu-west"], "tenants": ["acme", "globex"]}


def _register(client, scope=None, headers=None):
    body = {"apiInvokerPublicKey": "label"}
    if scope is not None:
        body["authzScope"] = scope
    return client.post("/invoker-registrations", json=body, headers=headers or {})


def _introspect(client, registration):
    token = client.post("/oauth2/token", json={"grant_type": "client_credentials", "client_id": registration["apiInvokerId"],
                                               "client_secret": registration["onboardingSecret"]}).json()["access_token"]
    return client.post("/oauth2/introspect", json={"token": token}).json()


def test_an_invoker_registered_without_a_claim_is_unscoped_and_introspection_says_nothing_of_one(client):
    registration = _register(client).json()
    assert registration["authzScope"] is None
    view = _introspect(client, registration)
    assert view["active"] is True and "authz_scope" not in view


def test_the_claim_given_at_registration_is_returned_by_introspection_sorted(client):
    registration = _register(client, {"tenants": ["globex", "acme"], "regions": ["eu-west"]})
    assert registration.status_code == 201 and registration.json()["authzScope"] == CLAIM
    assert _introspect(client, registration.json())["authz_scope"] == CLAIM


@pytest.mark.parametrize("claim", [{"regions": []}, {"regions": "eu"}, {"cells": ["c"]}, {"tenants": ["a b"]}, {"regions": ["eu", "eu"]}, {"tenants": [1]}, {"regions": ["x" * 101]}])
def test_a_claim_that_is_not_valid_is_refused_with_a_fixed_message(client, claim):
    resp = _register(client, claim)
    assert resp.status_code == 422 and resp.json()["detail"]["title"] == "AUTHZ_SCOPE_INVALID"
    assert client.get("/invoker-registrations").json()["items"] == []
    assert "x" * 101 not in resp.text


def test_an_empty_claim_is_no_claim(client):
    assert _register(client, {}).json()["authzScope"] is None


def test_an_operator_sets_changes_and_removes_the_claim_and_it_applies_to_the_tokens_already_issued(client):
    registration = _register(client).json()
    token = client.post("/oauth2/token", json={"grant_type": "client_credentials", "client_id": registration["apiInvokerId"],
                                               "client_secret": registration["onboardingSecret"]}).json()["access_token"]
    introspect = lambda: client.post("/oauth2/introspect", json={"token": token}).json()      # noqa: E731
    assert "authz_scope" not in introspect()
    url = f"/invoker-registrations/{registration['apiInvokerId']}/authz-scope"
    assert client.put(url, json={"authzScope": {"regions": ["eu-west"]}}).json() == {"apiInvokerId": registration["apiInvokerId"], "authzScope": {"regions": ["eu-west"]}}
    assert introspect()["authz_scope"] == {"regions": ["eu-west"]}                      # read live: the token issued before is limited from now on
    client.put(url, json={"authzScope": CLAIM})
    assert introspect()["authz_scope"] == CLAIM                                         # replaced, not merged
    assert client.put(url, json={"authzScope": None}).json()["authzScope"] is None
    assert "authz_scope" not in introspect()
    client.put(url, json={"authzScope": CLAIM})
    assert client.put(url, json={}).json()["authzScope"] is None


def test_setting_a_claim_is_validated_and_needs_a_known_invoker(client):
    registration = _register(client).json()
    url = f"/invoker-registrations/{registration['apiInvokerId']}/authz-scope"
    resp = client.put(url, json={"authzScope": {"regions": []}})
    assert resp.status_code == 422 and resp.json()["detail"]["title"] == "AUTHZ_SCOPE_INVALID"
    assert client.get("/invoker-registrations").json()["items"][0]["authzScope"] is None             # the failed edit changed nothing
    missing = client.put("/invoker-registrations/nobody/authz-scope", json={"authzScope": CLAIM})
    assert missing.status_code == 400 and missing.json()["detail"]["title"] == "INVOKER_NOT_REGISTERED"


def test_the_registry_list_shows_the_claim(client):
    _register(client, CLAIM)
    _register(client)
    items = client.get("/invoker-registrations").json()["items"]
    assert sorted((i["authzScope"] is not None) for i in items) == [False, True]
    assert all("onboardingSecret" not in i for i in items)


def test_a_scoped_caller_cannot_mint_an_invoker_with_more_reach_than_its_own(client):
    """An rApp that is limited could otherwise leave its limit by registering a fresh identity."""
    scoped = {SCOPE_HEADER: '{"regions":["eu-west"]}'}
    inherited = _register(client, headers=scoped)
    assert inherited.status_code == 201 and inherited.json()["authzScope"] == {"regions": ["eu-west"]}      # no claim asked for: it gets the caller's
    narrower = _register(client, {"regions": ["eu-west"], "tenants": ["acme"]}, headers=scoped)
    assert narrower.status_code == 201 and narrower.json()["authzScope"] == {"regions": ["eu-west"], "tenants": ["acme"]}
    for wider in ({"regions": ["eu-west", "us-east"]}, {"regions": ["us-east"]}, {"tenants": ["acme"]}):
        resp = _register(client, wider, headers=scoped)
        assert resp.status_code == 403 and resp.json()["detail"]["title"] == "SCOPE_DENIED", wider
    count = len(client.get("/invoker-registrations").json()["items"])
    assert count == 2                                                                                         # the refused ones registered nothing


def test_an_unscoped_caller_registers_any_claim_and_a_module_acting_for_a_scoped_rapp_is_held_to_it(client):
    assert _register(client, {"regions": ["anywhere"]}).status_code == 201
    module_for_rapp = {"X-R1-Role": "internal", "X-R1-On-Behalf-Of": "es-client", ON_BEHALF_SCOPE_HEADER: '{"tenants":["acme"]}'}
    assert _register(client, {"tenants": ["globex"]}, headers=module_for_rapp).status_code == 403
    assert _register(client, headers=module_for_rapp).json()["authzScope"] == {"tenants": ["acme"]}
    assert _register(client, headers={"X-R1-Role": "internal"}).json()["authzScope"] is None


def test_a_claim_that_cannot_be_read_at_the_caller_leaves_nothing_to_hand_out(client):
    for claim in ({"regions": ["eu"]}, None):
        resp = _register(client, claim, headers={SCOPE_HEADER: "garbage"})
        assert resp.status_code == 403 and resp.json()["detail"]["title"] == "SCOPE_DENIED"
    assert client.get("/invoker-registrations").json()["items"] == []
