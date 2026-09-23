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
