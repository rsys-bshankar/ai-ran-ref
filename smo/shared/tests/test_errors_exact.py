"""smo_shared.errors, exactly: what identifies each integrity problem (SQLSTATE on the error or on its diagnostics, or the driver's words), the wording of every answer,
and the log line. Written for the mutants the wider mutation scope (PR-V-2c) found that no test noticed."""

import json
import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import DataError, IntegrityError

from smo_shared.errors import (FrameworkError, ProblemDetails, _integrity_problem, _orig, framework_error, illegal_transition_error, install_integrity_handlers,
                               install_out_of_range_handler, problem)

FK = ("REFERENCED_RESOURCE_NOT_FOUND", 422, "the request refers to something that does not exist")
UNIQUE = ("RESOURCE_ALREADY_EXISTS", 409, "the request repeats something that already exists")
CHECK = ("CONSTRAINT_VIOLATED", 422, "a value in the request is not one the service accepts")
OTHER = ("INTERNAL_ERROR", 500, "the service could not complete the request")


def integrity(orig):
    return IntegrityError("INSERT", {}, orig)


def with_sqlstate(state, text="x"):
    error = Exception(text)
    error.sqlstate = state
    return error


def with_diag(state, text="x"):
    error = Exception(text)
    error.diag = SimpleNamespace(sqlstate=state)
    return error


@pytest.mark.parametrize("orig,expected", [
    (with_sqlstate("23503"), FK), (with_sqlstate("23505"), UNIQUE), (with_sqlstate("23514"), CHECK),                       # the error's own SQLSTATE
    (with_diag("23503"), FK), (with_diag("23505"), UNIQUE), (with_diag("23514"), CHECK),                                    # or its diagnostics'
    (Exception("FOREIGN KEY constraint failed"), FK), (Exception("duplicate key value violates unique constraint"), UNIQUE),
    (Exception("UNIQUE violation"), UNIQUE), (Exception('new row violates CHECK constraint "x"'), CHECK),                  # or the driver's words, in any case
    (with_sqlstate("23502", "null value"), OTHER), (Exception("something else"), OTHER),
])
def test_each_integrity_error_is_the_problem_its_state_or_words_name(orig, expected):
    assert _integrity_problem(integrity(orig)) == expected


def test_an_error_without_an_orig_is_judged_by_its_own_text():
    class Bare(Exception):
        pass

    assert _integrity_problem(Bare("violates foreign key constraint")) == FK
    assert _integrity_problem(Bare("nothing known")) == OTHER


def test_the_state_wins_over_misleading_words_and_each_class_is_not_another():
    assert _integrity_problem(integrity(with_sqlstate("23505", "check constraint foreign key"))) == UNIQUE
    assert _integrity_problem(integrity(with_sqlstate("23514", "unique constraint"))) == CHECK
    assert _integrity_problem(integrity(with_sqlstate("23502", "foreign key unique constraint check constraint"))) == OTHER     # a state that is none of the three
    assert _integrity_problem(integrity(with_diag("23502", "unique violation"))) == OTHER
    for words in ("unique constraint", "unique violation"):
        assert _integrity_problem(integrity(Exception(words))) == UNIQUE
    assert _integrity_problem(integrity(Exception("check constraint"))) == CHECK


def app_with_handlers():
    app = FastAPI()
    install_integrity_handlers(app)
    install_out_of_range_handler(app)

    @app.get("/integrity")
    def integrity_route():
        raise integrity(with_sqlstate("23503", "insert on table \"x\" violates foreign key\nDETAIL: Key (a)=(b) is not present"))

    @app.get("/other")
    def other():
        raise RuntimeError("password=hunter2")

    @app.get("/overflow")
    def overflow():
        raise OverflowError("too big")

    @app.get("/data")
    def data():
        raise DataError("INSERT", {}, Exception("integer out of range"))

    return TestClient(app, raise_server_exceptions=False)


def test_the_answers_are_complete_problem_documents():
    client = app_with_handlers()
    answer = client.get("/integrity")
    assert answer.status_code == 422 and answer.json() == {"detail": {"type": "about:blank", "title": "REFERENCED_RESOURCE_NOT_FOUND", "status": 422,
                                                                      "detail": "the request refers to something that does not exist", "instance": None}}
    unhandled = client.get("/other")
    assert unhandled.status_code == 500 and unhandled.json() == {"detail": {"type": "about:blank", "title": "INTERNAL_ERROR", "status": 500,
                                                                            "detail": "the service could not complete the request", "instance": None}}
    for path in ("/overflow", "/data"):
        answer = client.get(path)
        assert answer.status_code == 422 and answer.json()["detail"] == {
            "type": "about:blank", "title": "VALUE_OUT_OF_RANGE", "status": 422,
            "detail": "a value in the request is outside the range the service can store", "instance": None}


def test_the_integrity_log_line_names_the_request_the_answer_and_the_first_line_of_the_database_s_words(caplog):
    with caplog.at_level(logging.WARNING, logger="smo.errors"):
        app_with_handlers().get("/integrity")
    assert [r.getMessage() for r in caplog.records] == ['GET /integrity answered 422 REFERENCED_RESOURCE_NOT_FOUND: insert on table "x" violates foreign key']


def test_the_database_s_words_in_the_log_are_cut_at_200_characters(caplog):
    app = FastAPI()
    install_integrity_handlers(app)

    @app.get("/long")
    def long():
        raise integrity(with_sqlstate("23505", "u" * 500))

    with caplog.at_level(logging.WARNING, logger="smo.errors"):
        TestClient(app, raise_server_exceptions=False).get("/long")
    assert caplog.records[0].getMessage().endswith(": " + "u" * 200)


def test_problem_and_framework_error_carry_status_title_and_detail():
    caught = problem(418, "TEAPOT", "short and stout")
    assert (caught.status_code, caught.detail) == (418, ProblemDetails(title="TEAPOT", status=418, detail="short and stout").model_dump())
    assert problem(404, "GONE").detail["detail"] is None
    caught = framework_error(FrameworkError.CONSTRAINT_VIOLATED, "why")
    assert (caught.status_code, caught.detail["title"], caught.detail["detail"]) == (422, "CONSTRAINT_VIOLATED", "why")
    assert framework_error(FrameworkError.CONSTRAINT_VIOLATED).detail["detail"] is None


def test_an_illegal_transition_names_the_subject_the_event_and_the_state():
    caught = illegal_transition_error(SimpleNamespace(event="STOP", state="IDLE"), "job 7")
    assert (caught.status_code, caught.detail["title"]) == (409, "LIFECYCLE_ILLEGAL_TRANSITION")
    assert caught.detail["detail"] == "job 7: event STOP is not allowed in state IDLE"


def test_the_integrity_warning_is_logged_by_the_errors_logger(caplog):
    with caplog.at_level(logging.WARNING, logger="smo.errors"):
        app_with_handlers().get("/integrity")
    assert [r.name for r in caplog.records] == ["smo.errors"]


def test_the_driver_s_words_are_the_wrapped_error_or_the_error_itself():
    inner = Exception("inner")
    assert _orig(integrity(inner)) is inner
    plain = Exception("plain")
    assert _orig(plain) is plain
