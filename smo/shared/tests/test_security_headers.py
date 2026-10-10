"""The response headers every module sends (smo_shared/security_headers.py): present on every answer, errors included, and overridable by a route.

Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests/test_security_headers.py -q
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from smo_shared.correlation import apply_correlation_id
from smo_shared.security_headers import HEADERS


def _app() -> FastAPI:
    """Helper: an app with the correlation middleware (which installs the security headers), an ordinary route and a route that sets one of the headers
    itself.
    """
    app = FastAPI()
    apply_correlation_id(app)

    @app.get("/plain")
    def plain():
        # Test route; not part of any published API.
        return {"ok": True}

    @app.get("/override")
    def override():
        # Test route that sets Cross-Origin-Resource-Policy itself; not part of any published API.
        return JSONResponse({"ok": True}, headers={"Cross-Origin-Resource-Policy": "cross-origin"})

    return app


def test_every_answer_carries_the_headers_including_errors():
    """Every answer, including the 404 of an unknown path, carries X-Content-Type-Options and Cross-Origin-Resource-Policy."""
    client = TestClient(_app())
    for path in ("/plain", "/does-not-exist"):
        response = client.get(path)
        for name, value in HEADERS.items():
            assert response.headers[name] == value, (path, name)


def test_a_route_can_set_the_header_itself():
    """A header the route sets itself is kept (setdefault), while the other standard header is still added."""
    response = TestClient(_app()).get("/override")
    assert response.headers["Cross-Origin-Resource-Policy"] == "cross-origin"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
