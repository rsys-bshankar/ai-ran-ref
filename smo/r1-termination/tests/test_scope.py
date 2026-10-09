"""PR-SEC-10.3: the scope claim at the gateway. SME's introspection says what an invoker is scoped to; the gateway forwards it in `X-R1-Scope` and nothing a caller
sent can reach a module as the gateway's own claim (the same anti-spoofing property as the invoker id and the role)."""

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from smo_shared import roles, scope
from smo_shared.scope import ON_BEHALF_SCOPE_HEADER, SCOPE_HEADER

from app import main, operator_api
from app.main import ROUTES, app

client = TestClient(app)
AUTH = {"Authorization": "Bearer t"}
INTROSPECT_URL = f"{ROUTES['/sme']}/oauth2/introspect"
CLAIM = {"regions": ["eu-west"], "tenants": ["acme"]}
CLAIM_HEADER = '{"regions":["eu-west"],"tenants":["acme"]}'


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    main._limiter.clear()
    main._introspection_cache.clear()
    monkeypatch.delenv("R1_INTROSPECTION_CACHE_SECONDS", raising=False)
    yield
    main._limiter.clear()
    main._introspection_cache.clear()


@pytest.fixture
def gateway(monkeypatch):
    state = {"sme_says": {"active": True, "client_id": "inv-1", "role": "rapp", "authz_scope": CLAIM}, "forwarded": [], "introspections": 0}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kwargs):
            if url == INTROSPECT_URL:
                state["introspections"] += 1
                return httpx.Response(200, json=state["sme_says"])
            state["forwarded"].append((method, url, {k.lower(): v for k, v in kwargs.get("headers", {}).items()}))
            return httpx.Response(200, json={})

    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.delenv("SMO_ROLE_ENFORCEMENT", raising=False)
    return state


def sent(gateway, index=0):
    return gateway["forwarded"][index][2]


def test_the_claim_sme_introspected_is_forwarded_as_compact_json(gateway):
    assert client.get("/ran-nf-oam/alarms", headers=AUTH).status_code == 200
    assert sent(gateway)[SCOPE_HEADER.lower()] == CLAIM_HEADER


def test_an_unscoped_caller_gets_no_scope_header(gateway):
    gateway["sme_says"] = {"active": True, "client_id": "inv-1", "role": "rapp"}
    client.get("/ran-nf-oam/alarms", headers=AUTH)
    assert SCOPE_HEADER.lower() not in sent(gateway) and ON_BEHALF_SCOPE_HEADER.lower() not in sent(gateway)
    gateway["sme_says"]["authz_scope"] = {}                               # an empty claim is no claim
    client.get("/ran-nf-oam/alarms", headers=AUTH)
    assert SCOPE_HEADER.lower() not in sent(gateway, 1)


def test_a_scope_header_the_caller_sent_is_dropped_when_it_has_no_claim(gateway):
    """The anti-spoofing property: an unscoped caller cannot make a module believe it holds some claim; a forged header is not the module's input at all."""
    gateway["sme_says"] = {"active": True, "client_id": "inv-1", "role": "rapp"}
    client.get("/ran-nf-oam/alarms", headers={**AUTH, SCOPE_HEADER: '{"regions":["us-east"]}', ON_BEHALF_SCOPE_HEADER: '{"regions":["us-east"]}'})
    assert SCOPE_HEADER.lower() not in sent(gateway) and ON_BEHALF_SCOPE_HEADER.lower() not in sent(gateway)


def test_a_scope_header_the_caller_sent_is_overwritten_by_the_claim_of_the_token(gateway):
    client.get("/ran-nf-oam/alarms", headers={**AUTH, SCOPE_HEADER: '{"regions":["us-east"],"tenants":["globex"]}'})
    assert sent(gateway)[SCOPE_HEADER.lower()] == CLAIM_HEADER


def test_an_rapp_cannot_pass_a_claim_on_for_somebody_else(gateway):
    client.get("/ran-nf-oam/alarms", headers={**AUTH, "X-R1-On-Behalf-Of": "other", ON_BEHALF_SCOPE_HEADER: '{"regions":["us-east"]}'})
    assert ON_BEHALF_SCOPE_HEADER.lower() not in sent(gateway) and "x-r1-on-behalf-of" not in sent(gateway)


def test_an_internal_module_may_pass_on_the_claim_of_the_rapp_it_acts_for_and_its_own_header_is_not_a_claim(gateway):
    gateway["sme_says"] = {"active": True, "client_id": "dme-client", "role": "internal"}
    client.get("/ran-nf-oam/alarms", headers={**AUTH, "X-R1-On-Behalf-Of": "es-client", ON_BEHALF_SCOPE_HEADER: CLAIM_HEADER, SCOPE_HEADER: '{"regions":["x"]}'})
    headers = sent(gateway)
    assert headers[ON_BEHALF_SCOPE_HEADER.lower()] == CLAIM_HEADER and SCOPE_HEADER.lower() not in headers        # its own X-R1-Scope was spoofed: dropped


def test_a_claim_on_an_internal_invoker_is_forwarded_as_its_own(gateway):
    gateway["sme_says"] = {"active": True, "client_id": "gui", "role": "internal", "authz_scope": {"tenants": ["acme"]}}
    client.get("/ran-nf-oam/alarms", headers=AUTH)
    assert sent(gateway)[SCOPE_HEADER.lower()] == '{"tenants":["acme"]}'


@pytest.mark.parametrize("damaged", ["eu", ["eu"], {"regions": []}, {"regions": "eu"}, {"cells": ["c"]}, {"tenants": ["a b"]}, 7])
def test_a_claim_that_cannot_be_read_permits_nothing_and_is_never_dropped(gateway, damaged):
    gateway["sme_says"]["authz_scope"] = damaged
    assert client.get("/ran-nf-oam/alarms", headers=AUTH).status_code == 200
    assert scope.decode(sent(gateway)[SCOPE_HEADER.lower()]) == scope.DENY_ALL


def test_the_claim_is_part_of_the_cached_answer_and_a_change_through_the_gateway_applies_at_once(gateway, monkeypatch):
    monkeypatch.setenv("R1_INTROSPECTION_CACHE_SECONDS", "60")
    gateway["sme_says"]["role"] = "internal"                                                                     # the operator's own caller; its claim is the one that changes
    client.get("/ran-nf-oam/alarms", headers=AUTH)
    client.get("/ran-nf-oam/alarms", headers=AUTH)
    assert gateway["introspections"] == 1 and sent(gateway, 1)[SCOPE_HEADER.lower()] == CLAIM_HEADER           # the second one from the cache, claim included
    gateway["sme_says"]["authz_scope"] = {"regions": ["us-east"]}
    client.get("/ran-nf-oam/alarms", headers=AUTH)
    assert sent(gateway, 2)[SCOPE_HEADER.lower()] == CLAIM_HEADER                                               # still the cached claim: inside the TTL
    assert client.put("/sme/invoker-registrations/inv-1/authz-scope", headers=AUTH, json={"authzScope": {"regions": ["us-east"]}}).status_code == 200
    client.get("/ran-nf-oam/alarms", headers=AUTH)
    assert sent(gateway, 4)[SCOPE_HEADER.lower()] == '{"regions":["us-east"]}'


def test_a_change_of_another_invokers_claim_does_not_evict_this_one(gateway, monkeypatch):
    monkeypatch.setenv("R1_INTROSPECTION_CACHE_SECONDS", "60")
    gateway["sme_says"]["role"] = "internal"
    client.get("/ran-nf-oam/alarms", headers=AUTH)
    client.put("/sme/invoker-registrations/someone-else/authz-scope", headers=AUTH, json={})
    client.get("/ran-nf-oam/alarms", headers=AUTH)
    client.get("/sme/invoker-registrations/inv-1/authz-scope", headers=AUTH)             # a read is no change
    client.put("/sme/invoker-registrations/inv-1/key/authz-scope", headers=AUTH, json={})     # not that route
    assert gateway["introspections"] == 1


def test_a_rapp_is_refused_the_routes_that_set_a_scope_and_the_backend_is_never_called(gateway):
    for method, path in (("PUT", "/sme/invoker-registrations/inv-1/authz-scope"), ("PUT", "/ran-nf-oam/managed-entities/e/scope")):
        resp = client.request(method, path, headers=AUTH, json={})
        assert resp.status_code == 403 and resp.json()["title"] == "ROLE_NOT_PERMITTED"
    assert gateway["forwarded"] == []
    assert roles.internal_only("/sme", "PUT", "/invoker-registrations/x/authz-scope")


def test_the_operator_api_forward_carries_the_claim_and_not_a_spoofed_one(monkeypatch):
    """/rapps/{instanceId}/operator/...: the rApp's own page is a module too; what reaches it as the claim is the gateway's."""
    instance = str(uuid.uuid4())
    seen = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kwargs):
            if url == INTROSPECT_URL:
                return httpx.Response(200, json={"active": True, "client_id": "gui", "role": "internal", "authz_scope": {"tenants": ["acme"]}})
            return httpx.Response(200, json={"instanceId": instance, "state": "RUNNING", "operatorApiBase": "http://my-rapp:8000"})

    async def forward(method, url, **kwargs):
        seen["headers"] = {k.lower(): v for k, v in kwargs["headers"].items()}
        return httpx.Response(200, json={})

    operator_api.clear_cache()
    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.main.forward_to_destination", forward)
    resp = client.get(f"/rapps/{instance}/operator/x", headers={**AUTH, SCOPE_HEADER: '{"regions":["spoofed"]}'})
    assert resp.status_code == 200
    assert seen["headers"][SCOPE_HEADER.lower()] == '{"tenants":["acme"]}'
    operator_api.clear_cache()
