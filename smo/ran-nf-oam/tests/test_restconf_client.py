"""Unit tests for the RFC 8040 RESTCONF CM client (OI-1-cm-sync-restconf).
Run with: pytest smo/ran-nf-oam/tests -q
"""

import httpx
import pytest

from app import restconf_client
from app.restconf_client import build_body, resource_url, send_edit, send_get

ROOT = "http://adaptor:9000/restconf"


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


def _error(tag):
    return {"ietf-restconf:errors": {"error": [{"error-type": "application", "error-tag": tag}]}}


@pytest.fixture
def wire(monkeypatch):
    """Records every request and answers from `wire.replies` (a response, or
    an exception to raise), defaulting to 204."""
    class Wire:
        calls = []
        replies = []

    def fake(verb):
        def send(url, json=None, **kw):
            Wire.calls.append((verb.upper(), url, json, kw.get("headers")))
            reply = Wire.replies.pop(0) if Wire.replies else FakeResponse(204)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return send

    for verb in ("get", "post", "put", "patch", "delete"):
        monkeypatch.setattr(restconf_client.httpx, verb, fake(verb))
    Wire.calls, Wire.replies = [], []
    return Wire


def test_resource_url_percent_encodes_each_key():
    assert resource_url(ROOT, "gnb-du-01") == f"{ROOT}/data/managed-element=gnb-du-01"
    assert resource_url(ROOT + "/", "gnb-du-01", "NRCellDU=101") == \
        f"{ROOT}/data/managed-element=gnb-du-01/managed-function=NRCellDU%3D101"
    assert resource_url(ROOT, "a/b,c") == f"{ROOT}/data/managed-element=a%2Fb%2Cc"


def test_body_is_an_rfc7951_list_entry_keyed_by_ref():
    assert build_body("ME-1", {"adminState": "LOCKED"}) == {"managed-element": [{"ref": "ME-1", "adminState": "LOCKED"}]}
    assert build_body("ME-1", {"tilt": 40}, "NRCellDU=1") == \
        {"managed-function": [{"function-ref": "NRCellDU=1", "tilt": "40"}]}


@pytest.mark.parametrize("operation, method, url", [
    ("merge", "PATCH", f"{ROOT}/data/managed-element=ME-1/managed-function=NRCellDU%3D1"),
    ("replace", "PUT", f"{ROOT}/data/managed-element=ME-1/managed-function=NRCellDU%3D1"),
    ("create", "POST", f"{ROOT}/data/managed-element=ME-1"),
    ("delete", "DELETE", f"{ROOT}/data/managed-element=ME-1/managed-function=NRCellDU%3D1"),
])
def test_each_operation_maps_to_its_rfc8040_method(wire, operation, method, url):
    result = send_edit(ROOT, "ME-1", {"a": "1"} if operation != "delete" else {}, "m-1", operation, "NRCellDU=1")
    assert result
    [(sent_method, sent_url, body, headers)] = wire.calls
    assert (sent_method, sent_url) == (method, url)
    assert headers["Content-Type"] == "application/yang-data+json"
    assert body == (None if method == "DELETE" else {"managed-function": [{"function-ref": "NRCellDU=1", "a": "1"}]})


def test_create_of_a_managed_element_posts_to_the_datastore(wire):
    assert send_edit(ROOT, "ME-1", {"a": "1"}, "m-1", "create")
    assert wire.calls[0][:3] == ("POST", f"{ROOT}/data", {"managed-element": [{"ref": "ME-1", "a": "1"}]})


def test_remove_of_a_missing_object_succeeds_but_delete_fails(wire):
    wire.replies = [FakeResponse(404, _error("data-missing")), FakeResponse(404, _error("data-missing"))]
    assert send_edit(ROOT, "ME-1", {}, "m-1", "remove")
    result = send_edit(ROOT, "ME-1", {}, "m-1", "delete")
    assert not result and result.reason == "RESTCONF_REQUEST_FAILED" and result.error_tag == "data-missing"
    assert not result.retryable


@pytest.mark.parametrize("reply, reason, retryable", [
    (httpx.ReadTimeout("slow"), "RESTCONF_TIMEOUT", True),
    (httpx.ConnectError("down"), "RESTCONF_UNREACHABLE", True),
    (FakeResponse(504), "RESTCONF_TIMEOUT", True),
    (FakeResponse(503), "RESTCONF_UNREACHABLE", True),
    (FakeResponse(500), "RESTCONF_UNREACHABLE", True),            # no errors body: transport trouble
    (FakeResponse(500, _error("operation-failed")), "RESTCONF_REQUEST_FAILED", False),  # a definite answer
    (FakeResponse(400, _error("invalid-value")), "RESTCONF_REQUEST_FAILED", False),
    (FakeResponse(409, _error("data-exists")), "RESTCONF_REQUEST_FAILED", False),
])
def test_failures_say_whether_they_are_worth_retrying(wire, reply, reason, retryable):
    wire.replies = [reply]
    result = send_edit(ROOT, "ME-1", {"a": "1"}, "m-1")
    assert not result and (result.reason, result.retryable) == (reason, retryable)


def test_unknown_operation_is_refused_without_a_request(wire):
    result = send_edit(ROOT, "ME-1", {"a": "1"}, "m-1", "frobnicate")
    assert not result and result.error_tag == "invalid-value" and wire.calls == []


def test_get_returns_the_attributes_without_the_key(wire):
    wire.replies = [FakeResponse(200, {"managed-function": [{"function-ref": "NRCellDU=1", "administrativeState": "LOCKED"}]})]
    assert send_get(ROOT, "ME-1", "m-1", "NRCellDU=1") == {"administrativeState": "LOCKED"}
    assert wire.calls[0][:2] == ("GET", f"{ROOT}/data/managed-element=ME-1/managed-function=NRCellDU%3D1")


@pytest.mark.parametrize("reply", [FakeResponse(404, _error("data-missing")), FakeResponse(200, {"other": []}),
                                   FakeResponse(200), httpx.ConnectError("down")])
def test_get_that_fails_or_answers_another_shape_is_none(wire, reply):
    wire.replies = [reply]
    assert send_get(ROOT, "ME-1", "m-1") is None
