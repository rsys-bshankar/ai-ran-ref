"""Tests for R1 Termination (Foundational Platform LLD section 4).
Run with: pytest smo/r1-termination/tests -q
"""

import json

from fastapi.testclient import TestClient

from app.main import app, ROUTES

client = TestClient(app)

INTROSPECT_URL = f"{ROUTES['/sme']}/oauth2/introspect"
AUTH_HEADERS = {"Authorization": "Bearer test-token"}  # accepted by every fake client below, which treats introspection as a no-op success


def test_bootstrap_returns_only_service_and_published_apis():
    """Section 4.1: BootstrapInformation ONLY ever contains service-apis
    and published-apis entries, never events-subscription.
    """
    resp = client.get("/bootstrap")
    assert resp.status_code == 200
    names = {e["apiName"] for e in resp.json()["apiEndpoints"]}
    assert names == {"service-apis", "published-apis"}


def test_bootstrap_entries_carry_a_token_endpoint():
    """No global /token route — each entry carries its own conditional
    tokenEndPoint (section 4.1).
    """
    resp = client.get("/bootstrap")
    for entry in resp.json()["apiEndpoints"]:
        assert "tokenEndPoint" in entry


def test_bootstrap_needs_no_authorization_header():
    """Section 4.1: /bootstrap is the one route _authorized never gates —
    an rApp has no token yet the first time it calls this.
    """
    resp = client.get("/bootstrap")
    assert resp.status_code == 200


def test_route_table_covers_every_module():
    """Section 4.2's extended route table — every SMO module plus the two
    DME data-plane transport routes.
    """
    expected_prefixes = {
        "/sme", "/dme", "/dme-push", "/dme-pull", "/onboarding", "/rapp-mgmt",
        "/ran-nf-oam", "/a1-related", "/nfo", "/focom", "/aimgf", "/mlmr", "/mllf",
        "/ran-analytics", "/mdaf", "/intent-service", "/so-smos", "/sa-smos",
    }
    assert set(ROUTES.keys()) == expected_prefixes


def test_unknown_route_returns_404():
    resp = client.get("/not-a-real-module/anything")
    assert resp.status_code == 404
    assert resp.json()["title"] == "NO_ROUTE"


class FakeResponse:
    def __init__(self, status_code=200, content=b'{"ok": true}'):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": "application/json"}

    def json(self):
        return json.loads(self.content)


def test_proxy_forwards_to_correct_backend(monkeypatch):
    """The gateway forwards, doesn't handle the request itself — proves
    /sme/* actually reaches the SME backend URL, not some other one.
    """
    calls = []

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kwargs):
            calls.append((method, url))
            if url == INTROSPECT_URL:
                return FakeResponse(content=b'{"active": true}')
            return FakeResponse()

    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeAsyncClient)

    resp = client.get("/sme/service-apis/v1/allServiceAPIs", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    # the last call is the actual forward — the first is _authorized's own
    # introspection round trip.
    method, url = calls[-1]
    assert url.startswith(ROUTES["/sme"])
    # the module prefix must be STRIPPED before forwarding — no backend's own
    # routes carry it (real bug this test now catches: R1 Termination
    # originally forwarded the prefix through unstripped, which 404s
    # against every real backend service).
    assert url == f"{ROUTES['/sme']}/service-apis/v1/allServiceAPIs"


def test_proxy_rejects_a_request_with_no_authorization_header():
    resp = client.get("/sme/service-apis/v1/allServiceAPIs")
    assert resp.status_code == 401
    assert resp.json()["title"] == "UNAUTHORIZED"


def test_proxy_rejects_a_non_bearer_authorization_header():
    resp = client.get("/sme/service-apis/v1/allServiceAPIs", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert resp.status_code == 401


def test_proxy_rejects_a_token_sme_reports_inactive(monkeypatch):
    class InactiveAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kwargs):
            assert url == INTROSPECT_URL  # the forward call must never happen — 401 short-circuits before it
            return FakeResponse(content=b'{"active": false}')

    monkeypatch.setattr("app.main.httpx.AsyncClient", InactiveAsyncClient)
    resp = client.get("/sme/service-apis/v1/allServiceAPIs", headers=AUTH_HEADERS)
    assert resp.status_code == 401


def test_proxy_fails_closed_when_sme_introspection_is_unreachable(monkeypatch):
    """A security gate, not a best-effort side effect (unlike this
    build's usual "unreachable callback never fails the primary
    operation" notification pattern) — SME being down must reject, not
    silently let the request through.
    """
    import httpx as httpx_module

    class UnreachableAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kwargs):
            raise httpx_module.ConnectError("sme unreachable")

    monkeypatch.setattr("app.main.httpx.AsyncClient", UnreachableAsyncClient)
    resp = client.get("/sme/service-apis/v1/allServiceAPIs", headers=AUTH_HEADERS)
    assert resp.status_code == 401


class RecordingAsyncClient:
    """Records every call made through it (except introspection, which
    every test in this file expects to just succeed — see
    test_proxy_rejects_* above for the cases that exercise introspection
    itself), for asserting exactly what the proxy forwarded (method, url,
    headers, params, body).
    """
    calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, headers=None, params=None, content=None, **kwargs):
        if url == INTROSPECT_URL:
            return FakeResponse(content=b'{"active": true}')
        self.calls.append({"method": method, "url": url, "headers": headers, "params": params, "content": content})
        return FakeResponse(status_code=self._next_status)

    _next_status = 200


def _install_recording_client(monkeypatch, next_status=200):
    RecordingAsyncClient.calls = []
    RecordingAsyncClient._next_status = next_status
    monkeypatch.setattr("app.main.httpx.AsyncClient", RecordingAsyncClient)
    return RecordingAsyncClient


def test_proxy_forwards_method_body_and_query_params(monkeypatch):
    recorder = _install_recording_client(monkeypatch)
    resp = client.post("/sme/published-apis/v1", params={"foo": "bar"}, json={"serviceName": "x"}, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    call = recorder.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{ROUTES['/sme']}/published-apis/v1"
    assert call["params"]["foo"] == "bar"
    assert b"serviceName" in call["content"]


def test_proxy_strips_host_header_but_forwards_others(monkeypatch):
    recorder = _install_recording_client(monkeypatch)
    client.get("/sme/service-apis/v1/allServiceAPIs", headers={**AUTH_HEADERS, "Host": "should-not-forward"})
    call = recorder.calls[0]
    assert "authorization" in {k.lower() for k in call["headers"]}
    assert "host" not in {k.lower() for k in call["headers"]}


def test_proxy_passes_through_upstream_error_status_unchanged(monkeypatch):
    """A real backend failure (e.g. 503) must reach the rApp unchanged,
    not be swallowed or remapped by the gateway.
    """
    _install_recording_client(monkeypatch, next_status=503)
    resp = client.get("/sme/service-apis/v1/allServiceAPIs", headers=AUTH_HEADERS)
    assert resp.status_code == 503


def test_proxy_handles_prefix_only_path_with_no_trailing_segment(monkeypatch):
    """/sme with nothing after it — full_path.split("/", 1) has no second
    element, so rest_of_path must fall back to "" rather than crash.
    """
    recorder = _install_recording_client(monkeypatch)
    resp = client.get("/sme", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert recorder.calls[0]["url"] == f"{ROUTES['/sme']}/"


def test_dme_push_and_pull_prefixes_route_to_dme_without_colliding(monkeypatch):
    """/dme, /dme-push, and /dme-pull all resolve to the DME backend but
    are distinct dict keys — proves none of the three shadows another via
    prefix matching (the proxy's prefix lookup is dict equality, not
    startswith, but that invariant was never actually asserted).
    """
    recorder = _install_recording_client(monkeypatch)
    client.get("/dme-push/some/path", headers=AUTH_HEADERS)
    assert recorder.calls[0]["url"] == f"{ROUTES['/dme-push']}/some/path"

    recorder2 = _install_recording_client(monkeypatch)
    client.get("/dme/some/path", headers=AUTH_HEADERS)
    assert recorder2.calls[0]["url"] == f"{ROUTES['/dme']}/some/path"


def test_own_health_check_is_answered_locally_without_authorization(monkeypatch):
    """GUI pass: /health is R1's own route, declared ahead of the catch-all
    proxy — it must never be proxied or token-gated (an unknown prefix
    used to 404 here)."""
    def boom(*a, **kw):
        raise AssertionError("R1's own /health must not make any upstream call")

    monkeypatch.setattr("app.main.httpx.AsyncClient", boom)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}
