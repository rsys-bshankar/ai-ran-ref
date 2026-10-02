"""smo_shared.bodylimit — request body cap (PR-SEC-8.1), driven as raw ASGI so chunked bodies can be sent."""

import asyncio
import json

import pytest

from smo_shared.bodylimit import MIB, BodySizeLimit, parse_overrides, settings_from_env


def run_request(limit, body_chunks, headers=None, path="/x", overrides=None, read_body=True, start_response_first=False):
    """Returns (status, body, bytes the app read). The app reads the whole body then answers 200."""
    consumed = []
    sent: list[dict] = []

    async def app(scope, receive, send):
        if start_response_first:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        if read_body:
            while True:
                message = await receive()
                consumed.append(len(message.get("body", b"")))
                if not message.get("more_body"):
                    break
        if not start_response_first:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    queue = [{"type": "http.request", "body": chunk, "more_body": i < len(body_chunks) - 1}
             for i, chunk in enumerate(body_chunks)] or [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive():
        return queue.pop(0)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "path": path, "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()]}
    asyncio.run(BodySizeLimit(app, lambda: (limit, overrides or {}))(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], body, sum(consumed)


def test_a_body_at_the_cap_passes_and_one_byte_over_is_413():
    assert run_request(10, [b"a" * 10])[0] == 200
    assert run_request(10, [b"a" * 11])[0] == 413


def test_a_declared_content_length_over_the_cap_is_refused_before_the_app_reads_anything():
    status, body, read = run_request(10, [b"a" * 11], headers={"content-length": "11"})
    assert (status, read) == (413, 0)
    assert json.loads(body)["title"] == "PAYLOAD_TOO_LARGE"


def test_a_chunked_body_with_no_content_length_is_stopped_when_it_passes_the_cap():
    status, body, read = run_request(10, [b"a" * 6, b"a" * 6, b"a" * 6])
    assert status == 413 and read <= 6                      # the second chunk never reaches the app
    assert json.loads(body)["status"] == 413


def test_an_empty_body_and_a_lying_content_length_header_are_handled():
    assert run_request(10, [], read_body=False)[0] == 200
    assert run_request(10, [b"a" * 11], headers={"content-length": "not-a-number"})[0] == 413   # the count still applies


def test_a_matching_override_replaces_the_cap_for_that_path_only():
    overrides = {"/models/*/artifact": 100}
    assert run_request(10, [b"a" * 50], path="/models/7/artifact", overrides=overrides)[0] == 200
    assert run_request(10, [b"a" * 50], path="/models/7", overrides=overrides)[0] == 413
    assert run_request(10, [b"a" * 101], path="/models/7/artifact", overrides=overrides)[0] == 413


def test_once_the_response_has_started_it_is_not_replaced():
    # the app answered before reading: nothing to refuse, and nothing may be sent twice
    assert run_request(10, [b"a" * 50], read_body=False, start_response_first=True)[0] == 200


def test_non_http_scopes_pass_through():
    called = []

    async def app(scope, receive, send):
        called.append(scope["type"])

    asyncio.run(BodySizeLimit(app, lambda: (1, {}))({"type": "lifespan"}, None, None))
    assert called == ["lifespan"]


def test_overrides_parse_and_reject_garbage():
    assert parse_overrides("/a/*/b=50, /c=7") == {"/a/*/b": 50, "/c": 7}
    assert parse_overrides("") == {}
    for bad in ("/a", "/a=x", "=5"):
        with pytest.raises(ValueError, match="bad body-limit override"):
            parse_overrides(bad)


def test_settings_come_from_the_environment_at_request_time(monkeypatch):
    read = settings_from_env("T", default_overrides="/up=5")
    assert read() == (MIB, {"/up": 5})
    monkeypatch.setenv("T_MAX_BODY_BYTES", "99")
    monkeypatch.setenv("T_MAX_BODY_OVERRIDES", "")
    assert read() == (99, {})
