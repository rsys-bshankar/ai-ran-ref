"""Unit tests for the NETCONF-shaped edit-config client (RAN NF OAM LLD
section 5.1's confirmed dispatch protocol). Run with:
pytest smo/ran-nf-oam/tests -q
"""

import httpx
import pytest

from app.netconf_client import build_edit_config_rpc, send_edit_config


def test_build_edit_config_rpc_embeds_target_and_attributes():
    rpc = build_edit_config_rpc("msg-1", "ME-1", {"adminState": "UNLOCKED"})
    assert 'message-id="msg-1"' in rpc
    assert 'ref="ME-1"' in rpc
    assert "<adminState>UNLOCKED</adminState>" in rpc


class FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


OK_REPLY = '<rpc-reply message-id="msg-1" xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><ok/></rpc-reply>'
ERROR_REPLY = ('<rpc-reply message-id="msg-1" xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">'
               '<rpc-error><error-type>application</error-type></rpc-error></rpc-reply>')


def test_send_edit_config_true_on_ok_reply(monkeypatch):
    monkeypatch.setattr("app.netconf_client.httpx.post", lambda url, content=None, headers=None, timeout=None: FakeResponse(200, OK_REPLY))
    assert send_edit_config("http://adaptor:9000/netconf", "ME-1", {"adminState": "UNLOCKED"}, "msg-1") is True


def test_send_edit_config_false_on_rpc_error(monkeypatch):
    monkeypatch.setattr("app.netconf_client.httpx.post", lambda url, content=None, headers=None, timeout=None: FakeResponse(200, ERROR_REPLY))
    assert send_edit_config("http://adaptor:9000/netconf", "ME-1", {"adminState": "UNLOCKED"}, "msg-1") is False


def test_send_edit_config_false_on_non_2xx(monkeypatch):
    monkeypatch.setattr("app.netconf_client.httpx.post", lambda url, content=None, headers=None, timeout=None: FakeResponse(503, ""))
    assert send_edit_config("http://adaptor:9000/netconf", "ME-1", {}, "msg-1") is False


def test_send_edit_config_false_on_transport_error(monkeypatch):
    def raise_error(url, content=None, headers=None, timeout=None):
        raise httpx.ConnectError("unreachable")
    monkeypatch.setattr("app.netconf_client.httpx.post", raise_error)
    assert send_edit_config("http://adaptor:9000/netconf", "ME-1", {}, "msg-1") is False


def test_send_edit_config_false_on_unparseable_reply(monkeypatch):
    monkeypatch.setattr("app.netconf_client.httpx.post", lambda url, content=None, headers=None, timeout=None: FakeResponse(200, "not xml"))
    assert send_edit_config("http://adaptor:9000/netconf", "ME-1", {}, "msg-1") is False
