"""Tests for R1 Termination (Foundational Platform LLD section 4).
Run with: pytest smo/r1-termination/tests -q
"""

from fastapi.testclient import TestClient

from app.main import app, ROUTES

client = TestClient(app)


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


def test_route_table_covers_every_module():
    """Section 4.2's extended route table — every SMO module plus the two
    DME data-plane transport routes.
    """
    expected_prefixes = {
        "/sme", "/dme", "/dme-push", "/dme-pull", "/onboarding", "/rapp-mgmt",
        "/ran-nf-oam", "/a1-related", "/nfo", "/focom", "/ai-ml-workflow",
        "/ran-analytics", "/policy-mgmt", "/so-smos", "/sa-smos",
    }
    assert set(ROUTES.keys()) == expected_prefixes


def test_unknown_route_returns_404():
    resp = client.get("/not-a-real-module/anything")
    assert resp.status_code == 404
    assert resp.json()["title"] == "NO_ROUTE"


def test_proxy_forwards_to_correct_backend(monkeypatch):
    """The gateway forwards, doesn't handle the request itself — proves
    /sme/* actually reaches the SME backend URL, not some other one.
    """
    calls = []

    class FakeResponse:
        content = b'{"ok": true}'
        status_code = 200
        headers = {"content-type": "application/json"}

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kwargs):
            calls.append((method, url))
            return FakeResponse()

    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeAsyncClient)

    resp = client.get("/sme/service-apis/v1/allServiceAPIs")
    assert resp.status_code == 200
    method, url = calls[0]
    assert url.startswith(ROUTES["/sme"])
    # the module prefix must be STRIPPED before forwarding — no backend's own
    # routes carry it (real bug this test now catches: R1 Termination
    # originally forwarded the prefix through unstripped, which 404s
    # against every real backend service).
    assert url == f"{ROUTES['/sme']}/service-apis/v1/allServiceAPIs"


class RecordingAsyncClient:
    """Records every call made through it, for asserting exactly what the
    proxy forwarded (method, url, headers, params, body) — none of that
    was covered before this pass, only the URL-stripping behavior was.
    """
    calls = []

    class FakeResponse:
        def __init__(self, status_code=200, content=b'{"ok": true}'):
            self.status_code = status_code
            self.content = content
            self.headers = {"content-type": "application/json"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, headers=None, params=None, content=None, **kwargs):
        self.calls.append({"method": method, "url": url, "headers": headers, "params": params, "content": content})
        return self.FakeResponse(status_code=self._next_status)

    _next_status = 200


def _install_recording_client(monkeypatch, next_status=200):
    RecordingAsyncClient.calls = []
    RecordingAsyncClient._next_status = next_status
    monkeypatch.setattr("app.main.httpx.AsyncClient", RecordingAsyncClient)
    return RecordingAsyncClient


def test_proxy_forwards_method_body_and_query_params(monkeypatch):
    recorder = _install_recording_client(monkeypatch)
    resp = client.post("/sme/published-apis/v1", params={"foo": "bar"}, json={"serviceName": "x"})
    assert resp.status_code == 200
    call = recorder.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{ROUTES['/sme']}/published-apis/v1"
    assert call["params"]["foo"] == "bar"
    assert b"serviceName" in call["content"]


def test_proxy_strips_host_header_but_forwards_others(monkeypatch):
    recorder = _install_recording_client(monkeypatch)
    client.get("/sme/service-apis/v1/allServiceAPIs", headers={"Authorization": "Bearer tok", "Host": "should-not-forward"})
    call = recorder.calls[0]
    assert "authorization" in {k.lower() for k in call["headers"]}
    assert "host" not in {k.lower() for k in call["headers"]}


def test_proxy_passes_through_upstream_error_status_unchanged(monkeypatch):
    """A real backend failure (e.g. 503) must reach the rApp unchanged,
    not be swallowed or remapped by the gateway.
    """
    _install_recording_client(monkeypatch, next_status=503)
    resp = client.get("/sme/service-apis/v1/allServiceAPIs")
    assert resp.status_code == 503


def test_proxy_handles_prefix_only_path_with_no_trailing_segment(monkeypatch):
    """/sme with nothing after it — full_path.split("/", 1) has no second
    element, so rest_of_path must fall back to "" rather than crash.
    """
    recorder = _install_recording_client(monkeypatch)
    resp = client.get("/sme")
    assert resp.status_code == 200
    assert recorder.calls[0]["url"] == f"{ROUTES['/sme']}/"


def test_dme_push_and_pull_prefixes_route_to_dme_without_colliding(monkeypatch):
    """/dme, /dme-push, and /dme-pull all resolve to the DME backend but
    are distinct dict keys — proves none of the three shadows another via
    prefix matching (the proxy's prefix lookup is dict equality, not
    startswith, but that invariant was never actually asserted).
    """
    recorder = _install_recording_client(monkeypatch)
    client.get("/dme-push/some/path")
    assert recorder.calls[0]["url"] == f"{ROUTES['/dme-push']}/some/path"

    recorder2 = _install_recording_client(monkeypatch)
    client.get("/dme/some/path")
    assert recorder2.calls[0]["url"] == f"{ROUTES['/dme']}/some/path"
