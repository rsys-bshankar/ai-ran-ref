"""PR-OBS-2: request count and latency series, and /metrics."""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from smo_shared.health import install_health
from smo_shared.metrics import install_metrics


def _app() -> FastAPI:
    app = FastAPI()
    install_metrics(app)
    install_health(app)

    @app.get("/models/{model_id}")
    def model(model_id: str):
        if model_id == "boom":
            raise HTTPException(status_code=409, detail="x")
        return {"id": model_id}

    @app.get("/crash")
    def crash():
        raise RuntimeError("no")

    return app


def _sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_a_request_is_counted_under_its_route_template_and_status():
    client = TestClient(_app())
    before = _sample("smo_http_requests_total", method="GET", route="/models/{model_id}", status="200")
    client.get("/models/a")
    client.get("/models/b")
    assert _sample("smo_http_requests_total", method="GET", route="/models/{model_id}", status="200") == before + 2


def test_raw_ids_never_become_label_values():
    TestClient(_app()).get("/models/some-unique-id-123")
    assert "some-unique-id-123" not in TestClient(_app()).get("/metrics").text


def test_error_statuses_have_their_own_series_and_unmatched_paths_share_one():
    client = TestClient(_app(), raise_server_exceptions=False)
    b409 = _sample("smo_http_requests_total", method="GET", route="/models/{model_id}", status="409")
    b404 = _sample("smo_http_requests_total", method="GET", route="unmatched", status="404")
    b500 = _sample("smo_http_requests_total", method="GET", route="/crash", status="500")
    client.get("/models/boom")
    client.get("/nope/1")
    client.get("/nope/2")
    client.get("/crash")
    assert _sample("smo_http_requests_total", method="GET", route="/models/{model_id}", status="409") == b409 + 1
    assert _sample("smo_http_requests_total", method="GET", route="unmatched", status="404") == b404 + 2
    assert _sample("smo_http_requests_total", method="GET", route="/crash", status="500") == b500 + 1


def test_latency_is_observed_in_a_histogram():
    client = TestClient(_app())
    before = _sample("smo_http_request_duration_seconds_count", method="GET", route="/models/{model_id}", status="200")
    client.get("/models/a")
    assert _sample("smo_http_request_duration_seconds_count", method="GET", route="/models/{model_id}", status="200") == before + 1
    assert _sample("smo_http_request_duration_seconds_sum", method="GET", route="/models/{model_id}", status="200") > 0


def test_probes_and_the_scrape_itself_are_not_counted():
    client = TestClient(_app())
    client.get("/live")
    client.get("/health")
    client.get("/metrics")
    client.get("/metrics")
    text = client.get("/metrics").text
    for route in ("/live", "/health", "/metrics"):
        assert f'route="{route}"' not in text


def test_metrics_is_prometheus_text_and_not_in_the_openapi_spec():
    client = TestClient(_app())
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE smo_http_requests_total counter" in response.text
    assert "/metrics" not in client.get("/openapi.json").json()["paths"]
