"""R1Client's OAuth2 client flow (smo_shared/r1_client.py): every
SMO-internal call through R1 Termination carries a Bearer token obtained
the rApp way — bootstrap-advertised token endpoint, invoker onboarding at
SME, client_credentials — cached per process and refreshed once on a 401.
Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests -q
"""

import httpx
import pytest

from smo_shared import r1_client
from smo_shared.r1_client import R1Client, _ModuleIdentity

R1 = "http://r1-termination:8000"
SME = "http://sme:8000"


class FakeNetwork:
    """R1 /bootstrap + SME invoker onboarding/token endpoint + R1's token-gated proxy."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.issued: list[str] = []
        self.revoked: set[str] = set()
        self.invokers = 0
        self.sme_up = True

    def _resp(self, status, payload, method, url):
        return httpx.Response(status, json=payload, request=httpx.Request(method, url))

    def get(self, url, headers=None, **kw):
        self.calls.append(("GET", url, headers or {}))
        if url == f"{R1}/bootstrap":
            return self._resp(200, {"apiEndpoints": [{"apiName": "service-apis", "tokenEndPoint": {"uri": f"{SME}/oauth2/token"}}]}, "GET", url)
        return self._proxied("GET", url, headers)

    def post(self, url, json=None, headers=None, **kw):
        self.calls.append(("POST", url, headers or {}))
        if url.startswith(SME):
            if not self.sme_up:
                raise httpx.ConnectError("refused")
            if url == f"{SME}/invoker-registrations":
                self.invokers += 1
                return self._resp(201, {"apiInvokerId": f"api-invoker-{self.invokers}", "onboardingSecret": "s"}, "POST", url)
            assert json["grant_type"] == "client_credentials" and json["client_secret"] == "s"
            token = f"tok-{len(self.issued) + 1}"
            self.issued.append(token)
            return self._resp(200, {"access_token": token, "expires_in": 3600}, "POST", url)
        return self._proxied("POST", url, headers)

    def _proxied(self, method, url, headers):
        token = (headers or {}).get("Authorization", "").removeprefix("Bearer ")
        ok = token in self.issued and token not in self.revoked
        return self._resp(200 if ok else 401, {"ok": ok}, method, url)


@pytest.fixture
def net(monkeypatch):
    fake = FakeNetwork()
    monkeypatch.setattr(r1_client.httpx, "get", fake.get)
    monkeypatch.setattr(r1_client.httpx, "post", fake.post)
    monkeypatch.setattr(r1_client, "_identity", _ModuleIdentity())
    return fake


def test_internal_calls_carry_a_token_obtained_the_rapp_way(net):
    resp = R1Client(R1).get("/onboarding/packages/p/onboarding-status")
    assert resp.status_code == 200
    urls = [u for _, u, _ in net.calls]
    assert urls == [f"{R1}/bootstrap", f"{SME}/invoker-registrations", f"{SME}/oauth2/token",
                    f"{R1}/onboarding/packages/p/onboarding-status"]
    assert net.calls[-1][2]["Authorization"] == "Bearer tok-1"


def test_the_token_is_cached_across_clients_and_calls(net):
    R1Client(R1).post("/nfo/descriptors", json={})
    R1Client(R1).get("/focom/inventory")
    assert net.issued == ["tok-1"] and net.invokers == 1


def test_a_revoked_token_is_refreshed_once_with_the_same_invoker(net):
    R1Client(R1).get("/focom/inventory")
    net.revoked.add("tok-1")
    assert R1Client(R1).get("/focom/inventory").status_code == 200
    assert net.issued == ["tok-1", "tok-2"] and net.invokers == 1


def test_an_explicit_bearer_token_is_used_as_is(net):
    net.issued.append("callers-own")
    assert R1Client(R1, bearer_token="callers-own").get("/dme/dme-types").status_code == 200
    assert [u for _, u, _ in net.calls] == [f"{R1}/dme/dme-types"]


def test_sme_down_sends_the_call_unauthenticated_rather_than_raising(net):
    net.sme_up = False
    assert R1Client(R1).get("/focom/inventory").status_code == 401
