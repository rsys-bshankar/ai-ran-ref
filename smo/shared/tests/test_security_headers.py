from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from smo_shared.correlation import apply_correlation_id
from smo_shared.security_headers import HEADERS


def _app() -> FastAPI:
    app = FastAPI()
    apply_correlation_id(app)

    @app.get("/plain")
    def plain():
        return {"ok": True}

    @app.get("/override")
    def override():
        return JSONResponse({"ok": True}, headers={"Cross-Origin-Resource-Policy": "cross-origin"})

    return app


def test_every_answer_carries_the_headers_including_errors():
    client = TestClient(_app())
    for path in ("/plain", "/does-not-exist"):
        response = client.get(path)
        for name, value in HEADERS.items():
            assert response.headers[name] == value, (path, name)


def test_a_route_can_set_the_header_itself():
    response = TestClient(_app()).get("/override")
    assert response.headers["Cross-Origin-Resource-Policy"] == "cross-origin"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
