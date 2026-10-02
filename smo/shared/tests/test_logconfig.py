
"""smo_shared.logconfig — structured logs, access log, redaction, LOG_LEVEL (PR-OBS-1)."""

import io
import json
import logging
import re

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from smo_shared.correlation import apply_correlation_id
from smo_shared.logconfig import (REDACTED, AccessLogMiddleware, JsonFormatter, RedactionFilter, configure_logging,
                                  install_logging, redact_text)


@pytest.fixture
def stream():
    """The log handler writing into a buffer; the root logger is put back afterwards."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    uvicorn_state = {n: (list(logging.getLogger(n).handlers), logging.getLogger(n).propagate)
                     for n in ("uvicorn", "uvicorn.error")}
    access_disabled = logging.getLogger("uvicorn.access").disabled
    buffer = io.StringIO()
    configure_logging("svc", stream=buffer, environ={"LOG_LEVEL": "DEBUG"})
    yield buffer
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    for name, (handlers, propagate) in uvicorn_state.items():
        logging.getLogger(name).handlers[:] = handlers
        logging.getLogger(name).propagate = propagate
    logging.getLogger("uvicorn.access").disabled = access_disabled


def lines(buffer) -> list[dict]:
    return [json.loads(line) for line in buffer.getvalue().splitlines()]


# ---------------------------------------------------------------- the format

def test_each_record_is_exactly_one_json_object_on_one_line(stream):
    log = logging.getLogger("t")
    log.info("first")
    log.warning("two\nlines and \"quotes\" and ünïcode")
    assert len(stream.getvalue().splitlines()) == 2
    first, second = lines(stream)
    assert first["message"] == "first" and first["level"] == "INFO" and first["logger"] == "t"
    assert second["message"] == "two\nlines and \"quotes\" and ünïcode"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", first["timestamp"])


def test_every_record_carries_the_service_name(stream):
    logging.getLogger("t").info("x")
    assert lines(stream)[0]["service"] == "svc"


def test_the_service_defaults_to_the_container_module(monkeypatch):
    monkeypatch.setenv("MODULE", "nfo")
    record = logging.LogRecord("n", logging.INFO, "f", 1, "m", (), None)
    assert json.loads(JsonFormatter().format(record))["service"] == "nfo"


def test_extra_fields_become_keys_and_an_unserialisable_one_does_not_break_the_line(stream):
    logging.getLogger("t").info("deployed", extra={"deploymentId": "d-1", "count": 3, "thing": object()})
    entry = lines(stream)[0]
    assert entry["deploymentId"] == "d-1" and entry["count"] == 3 and entry["thing"].startswith("<object")


def test_an_exception_is_one_field_not_extra_lines(stream):
    try:
        raise ValueError("bad input")
    except ValueError:
        logging.getLogger("t").exception("failed")
    assert len(stream.getvalue().splitlines()) == 1
    assert "ValueError: bad input" in lines(stream)[0]["exception"]


# ---------------------------------------------------------------- the request context

def make_app(stream) -> TestClient:
    app = FastAPI()
    install_logging(app)
    apply_correlation_id(app)
    log = logging.getLogger("app.handler")

    @app.get("/models/{model_id}")
    def model(model_id: str):
        log.info("looking up the model")
        return {"id": model_id}

    @app.get("/boom")
    def boom():
        raise HTTPException(status_code=503, detail="down")

    @app.get("/health")
    def health():
        return {"status": "healthy"}

    return TestClient(app)


def test_a_record_inside_a_request_carries_its_correlation_id(stream):
    make_app(stream).get("/models/7", headers={"X-Correlation-ID": "corr-123"})
    inside = [e for e in lines(stream) if e["logger"] == "app.handler"]
    assert inside and inside[0]["correlationId"] == "corr-123"


def test_a_request_without_one_gets_a_generated_id_and_the_access_line_has_it(stream):
    response = make_app(stream).get("/models/7")
    access = [e for e in lines(stream) if e["logger"] == "smo.access"][0]
    assert access["correlationId"] == response.headers["X-Correlation-ID"]


def test_the_access_line_has_method_route_template_status_and_duration_but_not_the_raw_path_or_query(stream):
    make_app(stream).get("/models/secret-looking-id-42?token=abc123&x=1")
    access = [e for e in lines(stream) if e["logger"] == "smo.access"]
    assert len(access) == 1                                    # one line per request
    entry = access[0]
    assert (entry["method"], entry["route"], entry["status"]) == ("GET", "/models/{model_id}", 200)
    assert isinstance(entry["durationMs"], float) and entry["durationMs"] >= 0
    own = json.dumps(entry)                                     # (the HTTP client library logs its own URL line, redacted)
    assert "secret-looking-id-42" not in own and "abc123" not in own and "token" not in own


def test_an_unknown_path_is_logged_as_unmatched_and_a_5xx_is_an_error(stream):
    client = make_app(stream)
    client.get("/no/such/route")
    client.get("/boom")
    entries = {e["status"]: e for e in lines(stream) if e["logger"] == "smo.access"}
    assert entries[404]["route"] == "unmatched" and entries[404]["level"] == "INFO"
    assert entries[503]["level"] == "ERROR" and entries[503]["route"] == "/boom"


def test_probes_are_debug_so_the_default_level_hides_them(stream):
    client = make_app(stream)
    configure_logging("svc", stream=stream, environ={})            # INFO
    client.get("/health")
    client.get("/models/1")
    routes = [e["route"] for e in lines(stream) if e["logger"] == "smo.access"]
    assert routes == ["/models/{model_id}"]


# ---------------------------------------------------------------- redaction

SEEDED = ["hunter2", "s3cr3t-value", "abcdefghijklmnop.qrs.tuv", "sk_live_0123456789", "p@ss-from-url", "cookie-value-9"]


@pytest.mark.parametrize("text", [
    "login failed password=hunter2 for admin",
    'body {"password": "hunter2", "user": "a"}',
    "client_secret: s3cr3t-value",
    "Authorization: Bearer abcdefghijklmnop.qrs.tuv",
    "authorization='Bearer abcdefghijklmnop.qrs.tuv'",
    "sent Bearer abcdefghijklmnop.qrs.tuv to sme",
    "api_key=sk_live_0123456789&x=1",
    "connecting to postgresql+psycopg://smo:p@ss-from-url@postgres:5432/smo",
    "access_token=abcdefghijklmnop.qrs.tuv expires_in=3600",
])
def test_a_seeded_secret_never_reaches_the_output(stream, text):
    logging.getLogger("t").error(text)
    out = stream.getvalue()
    assert not any(secret in out for secret in SEEDED), out
    assert REDACTED in out


def test_non_secret_text_is_left_alone():
    assert redact_text("created deployment d-1 for cell 101 with passenger count 3") == \
        "created deployment d-1 for cell 101 with passenger count 3"
    assert redact_text("token limit reached") == "token limit reached"


def test_printf_arguments_are_redacted_too(stream):
    logging.getLogger("t").warning("grant for %s failed: %s", "client-a", "password=hunter2")
    assert "hunter2" not in stream.getvalue()


def test_extra_fields_are_redacted_by_name_and_inside(stream):
    logging.getLogger("t").info("call", extra={
        "password": "hunter2", "Authorization": "Bearer abcdefghijklmnop.qrs.tuv", "headers": {"cookie": "cookie-value-9", "accept": "json"},
        "note": "sent api_key=sk_live_0123456789", "ok": "plain"})
    out = stream.getvalue()
    assert not any(secret in out for secret in SEEDED)
    entry = lines(stream)[0]
    assert entry["ok"] == "plain" and entry["headers"]["accept"] == "json" and entry["password"] == REDACTED


def test_a_secret_in_an_exception_message_or_traceback_is_redacted(stream):
    try:
        raise RuntimeError("could not connect with password=hunter2")
    except RuntimeError:
        logging.getLogger("t").exception("startup failed")
    assert "hunter2" not in stream.getvalue()


def test_uvicorn_and_third_party_loggers_go_through_the_same_redaction(stream):
    logging.getLogger("uvicorn.error").error("bad header Authorization: Bearer abcdefghijklmnop.qrs.tuv")
    logging.getLogger("some.library").warning("retry with token=sk_live_0123456789")
    out = stream.getvalue()
    assert "abcdefghijklmnop" not in out and "sk_live" not in out
    assert [e["logger"] for e in lines(stream)] == ["uvicorn.error", "some.library"]


# ---------------------------------------------------------------- LOG_LEVEL and setup

@pytest.mark.parametrize("value,hidden,shown", [("ERROR", "warning", "error"), ("warning", "info", "warning"),
                                                  ("DEBUG", None, "debug"), ("", "debug", "info")])
def test_log_level_from_the_environment(value, hidden, shown):
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    buffer = io.StringIO()
    try:
        configure_logging("svc", stream=buffer, environ={"LOG_LEVEL": value} if value else {})
        log = logging.getLogger("lvl")
        for name in ("debug", "info", "warning", "error"):
            getattr(log, name)(name)
        seen = [e["message"] for e in lines(buffer)]
        assert shown in seen and (hidden is None or hidden not in seen)
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_an_unknown_level_falls_back_to_info_and_says_so():
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    buffer = io.StringIO()
    try:
        configure_logging("svc", stream=buffer, environ={"LOG_LEVEL": "LOUD"})
        assert root.level == logging.INFO
        warning = lines(buffer)[0]
        assert warning["level"] == "WARNING" and "LOG_LEVEL='LOUD'" in warning["message"]
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_configuring_twice_leaves_one_handler_and_does_not_double_every_line(stream):
    configure_logging("svc", stream=stream, environ={"LOG_LEVEL": "DEBUG"})
    marked = [h for h in logging.getLogger().handlers if getattr(h, "_smo_json_handler", False)]
    assert len(marked) == 1
    logging.getLogger("t").info("once")
    assert len(stream.getvalue().splitlines()) == 1


def test_handlers_that_are_not_ours_are_left_alone(stream):
    other = logging.NullHandler()
    logging.getLogger().addHandler(other)
    try:
        configure_logging("svc", stream=stream, environ={})
        assert other in logging.getLogger().handlers
    finally:
        logging.getLogger().removeHandler(other)


def test_uvicorns_plain_text_access_log_is_turned_off_and_its_error_log_is_rerouted(stream):
    assert logging.getLogger("uvicorn.access").disabled is True
    assert logging.getLogger("uvicorn.error").handlers == [] and logging.getLogger("uvicorn.error").propagate is True


def test_the_middleware_and_filter_are_exported():
    assert AccessLogMiddleware and RedactionFilter
