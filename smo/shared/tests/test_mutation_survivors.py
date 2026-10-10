"""Tests added for the mutants the wider mutation scope (PR-V-2c) found that no test noticed: the webhook guard's logging, metrics and defaults,
the body-limit refusal's shape, the secret reader's file handling and messages, the identity helpers and the concurrent-modification body.
Each assertion names what a change to the code would break.

Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests/test_mutation_survivors.py -q
"""

import asyncio
import json
import logging
from uuid import UUID, uuid4

import httpx
import pytest

from smo_shared import metrics, webhook
from smo_shared.bodylimit import MIB, BodySizeLimit, settings_from_env
from smo_shared.identity import is_framework_internal_identity, rapp_id_from_instance
from smo_shared.secretfile import SecretFileError, read_secret
from smo_shared.versioning import concurrent_modification_response


# ---- webhook -------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def outbound(monkeypatch):
    """Replaces metrics.record_outbound with a recorder and yields the list of calls it received."""
    calls = []
    monkeypatch.setattr(metrics, "record_outbound", lambda *args: calls.append(args))
    return calls


def test_a_refused_post_is_logged_with_the_destination_and_a_refused_get_or_delete_is_not(caplog, outbound):
    """A refused POST is logged with its destination (none when there is no destination); refused GET and DELETE are not logged; all four are counted
    as blocked.
    """
    with caplog.at_level(logging.WARNING, logger="smo_shared.webhook"):
        assert webhook.post_webhook("http://127.0.0.1/cb", {}) is None
        assert webhook.get_webhook("http://127.0.0.1/cb") is None
        assert webhook.delete_webhook("http://127.0.0.1/cb") is None
        assert webhook.post_webhook(None, {}) is None            # no destination: nothing to name
    messages = [r.getMessage() for r in caplog.records]
    assert messages == ["webhook destination 'http://127.0.0.1/cb' rejected by SSRF guard; notification dropped"]
    assert [c[3] for c in outbound] == ["blocked"] * 4 and [c[2] for c in outbound] == ["post", "get", "delete", "post"]


# Table: (webhook function, httpx method, extra arguments) for POST, GET and DELETE; each must pass the 5 s default timeout (or the given one) and the
# mTLS arguments to httpx.
@pytest.mark.parametrize("function,method,args", [(webhook.post_webhook, "post", ({},)), (webhook.get_webhook, "get", ()), (webhook.delete_webhook, "delete", ())])
def test_each_verb_sends_with_a_five_second_timeout_unless_told_otherwise_and_with_the_mtls_arguments(monkeypatch, outbound, function, method, args):
    seen = {}

    def fake(destination, **kwargs):
        seen["destination"], seen["kwargs"] = destination, kwargs
        return httpx.Response(204)

    monkeypatch.setattr(httpx, method, fake)
    monkeypatch.setattr(webhook.mtls, "webhook_kwargs", lambda destination: {"verify": f"context-for-{destination}"})
    function("http://consumer/cb", *args)
    assert seen["kwargs"]["timeout"] == 5.0 and seen["kwargs"]["verify"] == "context-for-http://consumer/cb"
    function("http://consumer/cb", *args, timeout=1.5)
    assert seen["kwargs"]["timeout"] == 1.5


def test_every_outcome_is_counted_with_the_client_target_method_outcome_and_a_duration(monkeypatch, outbound):
    """Each callback outcome (5xx, timeout, transport error, a response-less stub) is counted with client, target, method and a duration measured in
    seconds.
    """
    monkeypatch.setattr(httpx, "post", lambda destination, **kw: httpx.Response(503))
    webhook.post_webhook("http://consumer/cb", {})

    def timeout(destination, **kw):
        raise httpx.ConnectTimeout("slow")

    def broken(destination, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", timeout)
    assert webhook.post_webhook("http://consumer/cb", {}) is None
    monkeypatch.setattr(httpx, "post", broken)
    assert webhook.post_webhook("http://consumer/cb", {}) is None

    class NotAResponse:        # a stub in a test may answer with something that has no status code
        pass

    monkeypatch.setattr(httpx, "post", lambda destination, **kw: NotAResponse())
    assert isinstance(webhook.post_webhook("http://consumer/cb", {}), NotAResponse)
    assert [c[:4] for c in outbound] == [("webhook", "callback", "post", "5xx"), ("webhook", "callback", "post", "timeout"),
                                          ("webhook", "callback", "post", "error"), ("webhook", "callback", "post", "error")]
    for call in outbound:
        assert len(call) == 5 and isinstance(call[4], float) and 0 <= call[4] < 5, call      # the time the call took, not a clock reading


# ---- body limit ----------------------------------------------------------------------------------------------------------------------

def test_the_413_names_its_limit_and_carries_the_headers_that_make_it_a_complete_json_answer():
    """The 413 answer names the limit in its detail and has exactly the content-type, content-length and connection headers of a complete JSON answer.
    """
    sent = []

    async def app(scope, receive, send):
        raise AssertionError("the app must not run")

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "path": "/x", "headers": [(b"content-length", b"2000")]}
    asyncio.run(BodySizeLimit(app, lambda: (1000, {}))(scope, None, send))
    start, body = sent
    headers = dict(start["headers"])
    payload = json.loads(body["body"])
    assert start["status"] == 413 and set(headers) == {b"content-type", b"content-length", b"connection"}
    assert headers[b"content-type"] == b"application/json" and headers[b"connection"] == b"close"
    assert headers[b"content-length"] == str(len(body["body"])).encode()
    assert payload["detail"] == "the request body is larger than the 1000 bytes this route accepts" and payload["status"] == 413


def test_with_nothing_in_the_environment_the_default_cap_is_a_mebibyte_and_there_are_no_overrides(monkeypatch):
    """With no settings the body cap is 1 MiB and there are no path overrides."""
    monkeypatch.delenv("ZZ_MAX_BODY_BYTES", raising=False)
    monkeypatch.delenv("ZZ_MAX_BODY_OVERRIDES", raising=False)
    assert settings_from_env("ZZ")() == (MIB, {})


# ---- secret files ---------------------------------------------------------------------------------------------------------------------

def test_a_secret_file_is_read_as_utf_8_whatever_the_locale(monkeypatch, tmp_path):
    """A secret file is opened as UTF-8 explicitly, so a non-ASCII secret reads the same under any locale."""
    path = tmp_path / "s"
    path.write_bytes("pässwörd\n".encode("utf-8"))
    opened = {}
    real_open = open

    def spy(file, *args, **kwargs):
        opened.update(kwargs)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("smo_shared.secretfile.open", spy, raising=False)
    assert read_secret("S", {"S_FILE": str(path)}) == "pässwörd"
    assert opened["encoding"] == "utf-8"


def test_an_unreadable_secret_file_says_why_or_at_least_what_kind_of_error(monkeypatch, tmp_path):
    """An unreadable secret file's error says the OS reason, or the exception class when the OS gave none, and never leaks the contents."""
    with pytest.raises(SecretFileError) as missing:
        read_secret("S", {"S_FILE": str(tmp_path / "absent")})
    assert "No such file or directory" in str(missing.value) and "FileNotFoundError" not in str(missing.value)

    def refuse(file, *args, **kwargs):
        raise OSError()          # no strerror

    monkeypatch.setattr("smo_shared.secretfile.open", refuse, raising=False)
    with pytest.raises(SecretFileError) as bare:
        read_secret("S", {"S_FILE": "/x"})
    assert str(bare.value).endswith("cannot be read: OSError")


# ---- identity, versioning ------------------------------------------------------------------------------------------------------------

def test_the_rapp_id_is_the_instance_id_as_text_and_only_a_non_uuid_is_a_framework_identity():
    """The rApp id is the instance id as text; only a non-UUID id (so-smos, sa-smos) counts as a framework identity."""
    instance = uuid4()
    assert rapp_id_from_instance(instance) == str(instance) and isinstance(rapp_id_from_instance(instance), str)
    assert is_framework_internal_identity("so-smos") is True and is_framework_internal_identity("sa-smos") is True
    assert is_framework_internal_identity(str(instance)) is False and is_framework_internal_identity(str(UUID(int=0))) is False


def test_the_concurrent_modification_answer_tells_the_caller_to_repeat_the_request():
    """The 409 body tells the caller the resource changed under it and to repeat the request."""
    response = concurrent_modification_response()
    assert response.status_code == 409
    assert json.loads(response.body)["detail"]["detail"] == "the resource was modified by another request while this one was running; repeat the request"
