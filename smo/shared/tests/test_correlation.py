"""apply_correlation_id (smo_shared/correlation.py) — Wave 3 cross-cutting
standardization's Correlation-ID slice. Run with:
cd smo/shared && PYTHONPATH=. python -m pytest tests -q
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from smo_shared.correlation import HEADER_NAME, apply_correlation_id, get_correlation_id


def _app() -> TestClient:
    app = FastAPI()
    apply_correlation_id(app)

    @app.get("/echo")
    def echo():
        return {"correlationId": get_correlation_id()}

    return TestClient(app)


def test_generates_a_correlation_id_when_the_caller_sends_none():
    client = _app()
    resp = client.get("/echo")
    assert resp.status_code == 200
    generated = resp.json()["correlationId"]
    assert generated  # a real, non-empty id was generated
    assert resp.headers[HEADER_NAME] == generated


def test_propagates_and_echoes_the_callers_own_correlation_id():
    client = _app()
    resp = client.get("/echo", headers={HEADER_NAME: "caller-supplied-id"})
    assert resp.status_code == 200
    assert resp.json()["correlationId"] == "caller-supplied-id"
    assert resp.headers[HEADER_NAME] == "caller-supplied-id"


def test_get_correlation_id_is_none_outside_any_request():
    assert get_correlation_id() is None


def test_two_requests_get_two_different_generated_ids():
    client = _app()
    first = client.get("/echo").json()["correlationId"]
    second = client.get("/echo").json()["correlationId"]
    assert first != second
