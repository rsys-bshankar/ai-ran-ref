"""A database integrity error from the caller's input answers 4xx with a problem document; any other unhandled error a problem document too (smo_shared/errors.py).

Found by the authenticated DAST scan (V-7d): a reference to an element that is not there, and a value the column's CHECK refuses, were plain-text 500s."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from smo_shared.errors import install_integrity_handlers


class _Orig(Exception):
    def __init__(self, message, sqlstate=None):
        super().__init__(message)
        self.sqlstate = sqlstate


def _app():
    app = FastAPI()
    install_integrity_handlers(app)

    @app.get("/fk")
    def fk():
        raise IntegrityError("INSERT", {}, _Orig('insert or update on table "x" violates foreign key constraint "y"', "23503"))

    @app.get("/unique")
    def unique():
        raise IntegrityError("INSERT", {}, _Orig('duplicate key value violates unique constraint "x_pkey"', "23505"))

    @app.get("/check")
    def check():
        raise IntegrityError("INSERT", {}, _Orig('new row for relation "x" violates check constraint "x_type_check"', "23514"))

    @app.get("/sqlite-fk")
    def sqlite_fk():
        raise IntegrityError("INSERT", {}, _Orig("FOREIGN KEY constraint failed"))

    @app.get("/other-integrity")
    def other():
        raise IntegrityError("INSERT", {}, _Orig("not null violation", "23502"))

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internals: password=hunter2")

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path,status,title", [
    ("/fk", 422, "REFERENCED_RESOURCE_NOT_FOUND"), ("/sqlite-fk", 422, "REFERENCED_RESOURCE_NOT_FOUND"),
    ("/unique", 409, "RESOURCE_ALREADY_EXISTS"), ("/check", 422, "CONSTRAINT_VIOLATED"),
    ("/other-integrity", 500, "INTERNAL_ERROR"),
])
def test_an_integrity_error_is_the_problem_it_names(path, status, title):
    response = _app().get(path)
    assert response.status_code == status and response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"]["title"] == title and response.json()["detail"]["status"] == status


def test_an_unhandled_error_answers_a_problem_document_that_says_nothing_of_the_error():
    response = _app().get("/boom")
    assert response.status_code == 500 and response.json()["detail"]["title"] == "INTERNAL_ERROR"
    assert "hunter2" not in response.text and "RuntimeError" not in response.text
