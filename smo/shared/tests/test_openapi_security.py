"""The generated spec declares the R1 bearer scheme and the error bodies every operation can answer with (found by the contract test)."""

from fastapi import FastAPI

from smo_shared.openapi_security import ERROR_ENVELOPE, STANDARD_ERROR_STATUSES, apply_r1_gateway_security


def _spec():
    app = FastAPI()

    @app.get("/things")
    def things():
        return []

    @app.post("/public", responses={404: {"description": "custom"}})
    def public(body: dict):
        return body

    apply_r1_gateway_security(app, public_paths=frozenset({"/public"}))
    return app.openapi()


def test_bearer_security_is_declared_except_on_public_paths():
    spec = _spec()
    assert spec["paths"]["/things"]["get"].get("security") is None and spec["security"]
    assert spec["paths"]["/public"]["post"]["security"] == []


def test_every_operation_declares_the_standard_error_statuses_with_the_envelope():
    spec = _spec()
    for operation in (spec["paths"]["/things"]["get"], spec["paths"]["/public"]["post"]):
        for status in STANDARD_ERROR_STATUSES:
            assert status in operation["responses"]
    assert spec["paths"]["/things"]["get"]["responses"]["503"]["content"]["application/json"]["schema"] == {"$ref": f"#/components/schemas/{ERROR_ENVELOPE}"}
    # a status the route documents itself is kept; 422 (ProblemDetails or validation list) is widened to the envelope
    assert spec["paths"]["/public"]["post"]["responses"]["404"]["description"] == "custom"
    assert spec["paths"]["/public"]["post"]["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith(ERROR_ENVELOPE)
    assert {"ProblemDetails", ERROR_ENVELOPE} <= set(spec["components"]["schemas"])


def test_a_number_too_large_for_the_database_is_a_422_not_a_500():
    from fastapi.testclient import TestClient
    from sqlalchemy.exc import DataError

    app = FastAPI()

    @app.get("/overflow")
    def overflow():
        raise OverflowError("Python int too large to convert to SQLite INTEGER")

    @app.get("/data-error")
    def data_error():
        raise DataError("INSERT ...", {}, Exception("integer out of range"))

    apply_r1_gateway_security(app)
    client = TestClient(app, raise_server_exceptions=False)
    for path in ("/overflow", "/data-error"):
        response = client.get(path)
        assert response.status_code == 422
        assert response.json()["detail"]["title"] == "VALUE_OUT_OF_RANGE"
