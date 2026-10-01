"""Unit tests for the NETCONF-shaped edit-config client (RAN NF OAM LLD
section 5.1's confirmed dispatch protocol). Run with:
pytest smo/ran-nf-oam/tests -q
"""

import httpx
import pytest

from app.netconf_client import build_edit_config_rpc, build_get_config_rpc, send_edit_config, send_get_config


def test_build_edit_config_rpc_embeds_target_and_attributes():
    rpc = build_edit_config_rpc("msg-1", "ME-1", {"adminState": "UNLOCKED"})
    assert 'message-id="msg-1"' in rpc
    assert 'ref="ME-1"' in rpc
    assert "<adminState>UNLOCKED</adminState>" in rpc
    assert 'operation="merge"' in rpc  # RFC 6241's own default, explicit here


def test_build_edit_config_rpc_emits_the_requested_operation():
    """SPEC_AUDIT.md item 3: RFC 6241 section 7.2's edit-config operation
    attribute (merge/replace/create/delete/remove), previously never
    emitted at all — every write was implicitly a merge.
    """
    rpc = build_edit_config_rpc("msg-1", "ME-1", {}, operation="delete")
    assert 'ref="ME-1" operation="delete"' in rpc


class FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


OK_REPLY = '<rpc-reply message-id="msg-1" xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><ok/></rpc-reply>'
ERROR_REPLY = ('<rpc-reply message-id="msg-1" xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">'
               '<rpc-error><error-type>application</error-type></rpc-error></rpc-reply>')


def _send(monkeypatch, reply=None, raises=None):
    def fake_post(url, content=None, headers=None, timeout=None):
        if raises:
            raise raises
        return reply
    monkeypatch.setattr("app.netconf_client.httpx.post", fake_post)
    return send_edit_config("http://adaptor:9000/netconf", "ME-1", {"adminState": "UNLOCKED"}, "msg-1")


def test_send_edit_config_true_on_ok_reply(monkeypatch):
    result = _send(monkeypatch, FakeResponse(200, OK_REPLY))
    assert bool(result) is True and result.reason is None


@pytest.mark.parametrize("reply,raises,reason,retryable", [
    (FakeResponse(200, ERROR_REPLY), None, "NETCONF_RPC_FAILED", False),
    (FakeResponse(200, "not xml"), None, "NETCONF_RPC_FAILED", False),
    (FakeResponse(400, ""), None, "NETCONF_RPC_FAILED", False),
    (FakeResponse(503, ""), None, "NETCONF_UNREACHABLE", True),
    (FakeResponse(504, ""), None, "NETCONF_TIMEOUT", True),
    (None, httpx.ConnectError("unreachable"), "NETCONF_UNREACHABLE", True),
    (None, httpx.ReadTimeout("slow"), "NETCONF_TIMEOUT", True),
])
def test_send_edit_config_failure_reasons(monkeypatch, reply, raises, reason, retryable):
    """Wave 10.1 (W10-19): a timeout or unreachable agent is retryable; an
    agent that answered <rpc-error> (or garbage) is not."""
    result = _send(monkeypatch, reply, raises)
    assert bool(result) is False and result.reason == reason and result.retryable is retryable


def test_function_ref_addresses_the_managed_function():
    rpc = build_edit_config_rpc("m", "gnb-1", {"administrativeState": "LOCKED"}, managed_function_ref="NRCellDU=101")
    assert '<managed-object ref="gnb-1" function-ref="NRCellDU=101" operation="merge">' in rpc
    assert '<managed-object ref="gnb-1" function-ref="NRCellDU=101"/>' in build_get_config_rpc("m", "gnb-1", "NRCellDU=101")


def test_send_get_config_reads_the_managed_object(monkeypatch):
    data = ('<rpc-reply message-id="m" xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><data>'
            '<managed-object ref="gnb-1" function-ref="NRCellDU=101"><administrativeState>LOCKED</administrativeState>'
            '</managed-object></data></rpc-reply>')
    monkeypatch.setattr("app.netconf_client.httpx.post", lambda url, content=None, headers=None, timeout=None: FakeResponse(200, data))
    assert send_get_config("http://a/edit-config", "gnb-1", "m", "NRCellDU=101") == {"administrativeState": "LOCKED"}
    monkeypatch.setattr("app.netconf_client.httpx.post", lambda url, content=None, headers=None, timeout=None: FakeResponse(200, ERROR_REPLY))
    assert send_get_config("http://a/edit-config", "gnb-1", "m") is None
