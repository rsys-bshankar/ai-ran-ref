"""Every service exposes /metrics; R1 Termination does not proxy it (PR-OBS-2.2, 2.3)."""

from fastapi.testclient import TestClient

from smo_shared.metrics import MetricsMiddleware


def test_every_loaded_service_has_the_metrics_middleware_and_route(loaded_apps):
    missing = [name for name, main in loaded_apps.items()
               if not any(m.cls is MetricsMiddleware for m in main.app.user_middleware)
               or "/metrics" not in {getattr(r, "path", None) for r in main.app.routes}]
    assert missing == [], f"services without /metrics: {missing}"


def test_r1_termination_does_not_proxy_a_modules_metrics(loaded_apps, monkeypatch):
    r1 = loaded_apps["r1-termination"]

    async def must_not_be_asked(request):
        raise AssertionError("the gateway tried to authenticate/forward a /metrics request")

    monkeypatch.setattr(r1, "_introspect", must_not_be_asked)
    client = TestClient(r1.app)
    for module in ("sme", "nfo", "onboarding", "r1-termination"):
        response = client.get(f"/{module}/metrics")
        assert response.status_code == 404, module
        assert response.json()["title"] == "NO_ROUTE"
    assert client.get("/metrics").status_code == 200       # R1's own series, answered by the gateway itself
