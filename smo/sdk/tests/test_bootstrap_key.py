"""PR-SEC-9.3: the SDK reaches /bootstrap only through R1Client, so `SMO_BOOTSTRAP_KEY` set in an rApp's environment makes the SDK's
first call present `X-Bootstrap-Key`; unset, it sends no such header (the gateway's default, an open /bootstrap)."""

import httpx
import pytest

from smo_sdk import AiRuntimeSdk
from smo_shared import r1_client

R1 = "http://r1-termination:8000"


class Gateway:
    """A gateway that, when `key` is set, refuses /bootstrap without it (as R1 Termination does with R1_BOOTSTRAP_KEY), then serves a token and one proxied call."""

    def __init__(self, key=None):
        self.key, self.bootstrap_headers, self.calls = key, [], []

    def _resp(self, status, payload, method, url):
        return httpx.Response(status, json=payload, request=httpx.Request(method, url))

    def get(self, url, headers=None, **kw):
        headers = headers or {}
        if url == f"{R1}/bootstrap":
            self.bootstrap_headers.append(headers)
            if self.key is not None and headers.get("X-Bootstrap-Key") != self.key:
                return self._resp(401, {"title": "UNAUTHORIZED", "status": 401}, "GET", url)
            return self._resp(200, {"apiEndpoints": [{"tokenEndPoint": {"uri": "http://sme:8000/oauth2/token"}}]}, "GET", url)
        self.calls.append(url)
        return self._resp(200, [], "GET", url)

    def post(self, url, json=None, headers=None, **kw):
        if url.endswith("/invoker-registrations"):
            return self._resp(201, {"apiInvokerId": "inv-1", "onboardingSecret": "s"}, "POST", url)
        return self._resp(200, {"access_token": "tok", "expires_in": 3600}, "POST", url)


@pytest.fixture
def gateway(monkeypatch):
    def install(key=None):
        fake = Gateway(key)
        monkeypatch.setattr(r1_client.httpx, "get", fake.get)
        monkeypatch.setattr(r1_client.httpx, "post", fake.post)
        monkeypatch.setattr(r1_client, "_identity", r1_client._ModuleIdentity())
        return fake
    monkeypatch.delenv("SMO_BOOTSTRAP_KEY", raising=False)
    monkeypatch.delenv("SMO_BOOTSTRAP_KEY_FILE", raising=False)
    return install


def sdk():
    return AiRuntimeSdk(r1_client.R1Client(R1))


def test_without_a_key_the_sdk_sends_no_bootstrap_header_to_an_open_gateway(gateway):
    fake = gateway(key=None)
    sdk().platform.list_published_services("apf-1")
    assert fake.bootstrap_headers == [{}] and fake.calls


def test_with_the_key_in_the_environment_the_sdk_passes_a_gateway_that_asks_for_it(gateway, monkeypatch):
    fake = gateway(key="shared-key")
    monkeypatch.setenv("SMO_BOOTSTRAP_KEY", "shared-key")
    sdk().platform.list_published_services("apf-1")
    assert fake.bootstrap_headers == [{"X-Bootstrap-Key": "shared-key"}] and fake.calls


def test_without_the_key_a_gateway_that_asks_for_it_refuses_discovery_and_the_sdk_gets_no_token(gateway, caplog):
    fake = gateway(key="shared-key")
    sdk().platform.list_published_services("apf-1")          # R1Client logs the failed discovery and sends the call without a token (the gateway then 401s it)
    assert fake.bootstrap_headers == [{}]
    assert any("401 Unauthorized" in r.getMessage() and "/bootstrap" in r.getMessage() for r in caplog.records)
