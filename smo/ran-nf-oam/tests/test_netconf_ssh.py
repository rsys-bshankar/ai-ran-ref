"""NETCONF over SSH (PR-SB-1.3): the session wrapper against an in-process SSH server (tests/netconf_ssh_server.py)."""

import pytest

from app import netconf_ssh
from app.netconf_ssh import NetconfSession, NetconfSshError, parse_ssh_uri, send_edit_config, send_get_config

from netconf_ssh_server import Behaviour, NetconfTestServer


@pytest.fixture
def lab(tmp_path, monkeypatch):
    servers = []

    def start(behaviour=None, trust=True, **kw):
        server = NetconfTestServer(behaviour, **kw)
        servers.append(server)
        known = tmp_path / "known_hosts"
        known.write_text(server.known_hosts_line() if trust else "")
        monkeypatch.setenv("NETCONF_SSH_KNOWN_HOSTS", str(known))
        monkeypatch.setenv("NETCONF_SSH_PASSWORD", "secret")
        monkeypatch.delenv("NETCONF_SSH_INSECURE_ANY_HOST_KEY", raising=False)
        return server

    yield start
    for server in servers:
        server.close()


def test_uri_parsing():
    assert parse_ssh_uri("ssh://admin@10.0.0.1") == ("admin", "10.0.0.1", 830)
    assert parse_ssh_uri("ssh://admin@gnb-1.lab:2222") == ("admin", "gnb-1.lab", 2222)
    for bad in ("http://admin@h", "ssh://h", "ssh://admin@", "ssh://admin@h:0", "ssh://admin@h:99999", "ssh://admin@h:x"):
        with pytest.raises(NetconfSshError):
            parse_ssh_uri(bad)


@pytest.mark.parametrize("caps,chunked", [
    (("urn:ietf:params:netconf:base:1.0",), False),
    (("urn:ietf:params:netconf:base:1.0", "urn:ietf:params:netconf:base:1.1"), True),
])
def test_hello_picks_the_framing_and_edit_config_is_applied(lab, caps, chunked):
    server = lab(Behaviour(caps=caps))
    with NetconfSession(server.uri, timeout=5) as session:
        assert session.chunked is chunked
        assert "urn:ietf:params:netconf:base:1.0" in session.server_capabilities
    result = send_edit_config(server.uri, "ME-1", {"administrativeState": "LOCKED"}, message_id="m1", managed_function_ref="NRCellDU=1")
    assert result.applied and result.reason is None
    assert 'message-id="m1"' in server.behaviour.received[-1] and "<administrativeState>LOCKED</administrativeState>" in server.behaviour.received[-1]


def test_a_reply_arriving_in_pieces_is_reassembled(lab):
    server = lab(Behaviour(split=True))
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m2").applied


def test_get_config_returns_the_attributes(lab):
    server = lab()
    assert send_get_config(server.uri, "ME-1", message_id="g1") == {"administrativeState": "UNLOCKED"}


def test_rpc_error_is_not_applied_and_not_retryable(lab):
    server = lab(Behaviour(edit_reply="<rpc-error><error-tag>invalid-value</error-tag></rpc-error>"))
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m3")
    assert not result and result.reason == "NETCONF_RPC_FAILED" and not result.retryable


def test_a_silent_server_times_out_retryably(lab, monkeypatch):
    server = lab(Behaviour(silent=True))
    monkeypatch.setattr(netconf_ssh, "NETCONF_TIMEOUT_SECONDS", 0.5)
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m4")
    assert not result and result.reason == "NETCONF_TIMEOUT" and result.retryable


def test_a_closed_port_is_unreachable_retryably(lab):
    lab()
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]                          # nothing listens here once the probe closes
    result = send_edit_config(f"ssh://netconf@127.0.0.1:{port}", "ME-1", {"a": "1"}, message_id="m5")
    assert result.reason == "NETCONF_UNREACHABLE" and result.retryable


def test_the_server_hanging_up_after_hello_is_unreachable(lab):
    server = lab(Behaviour(close_after_hello=True))
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m6").reason == "NETCONF_UNREACHABLE"


def test_an_unknown_host_key_is_refused(lab):
    server = lab(trust=False)
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m7")
    assert not result and result.reason == "NETCONF_RPC_FAILED" and not server.behaviour.received


def test_a_changed_host_key_is_refused(lab, tmp_path, monkeypatch):
    server = lab()
    other = NetconfTestServer()
    try:
        (tmp_path / "known_hosts").write_text(other.known_hosts_line().replace(f":{other.port}]", f":{server.port}]").replace(f"[127.0.0.1]:{other.port}", f"[127.0.0.1]:{server.port}"))
        result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m8")
        assert not result and result.reason == "NETCONF_RPC_FAILED" and not server.behaviour.received
    finally:
        other.close()


def test_no_known_hosts_configured_refuses_unless_insecure_is_set(lab, monkeypatch):
    server = lab()
    monkeypatch.delenv("NETCONF_SSH_KNOWN_HOSTS")
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m9").reason == "NETCONF_RPC_FAILED"
    monkeypatch.setenv("NETCONF_SSH_INSECURE_ANY_HOST_KEY", "true")
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m10").applied


def test_wrong_password_is_refused(lab, monkeypatch):
    server = lab()
    monkeypatch.setenv("NETCONF_SSH_PASSWORD", "wrong")
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m11")
    assert not result and result.reason == "NETCONF_RPC_FAILED" and not result.retryable


def test_no_netconf_subsystem_is_refused(lab):
    server = lab(no_subsystem=True)
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m12").reason == "NETCONF_RPC_FAILED"


@pytest.mark.parametrize("hello_text", [
    "<not-a-hello/>",
    '<hello xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><capabilities><capability>urn:other</capability></capabilities></hello>',
    "this is not xml",
])
def test_a_bad_hello_is_refused(lab, hello_text):
    server = lab(Behaviour(hello_text=hello_text))
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m13").reason == "NETCONF_RPC_FAILED"


def test_the_password_file_convention_is_honoured(lab, tmp_path, monkeypatch):
    server = lab()
    secret = tmp_path / "pw"
    secret.write_text("secret\n")
    monkeypatch.delenv("NETCONF_SSH_PASSWORD")
    monkeypatch.setenv("NETCONF_SSH_PASSWORD_FILE", str(secret))
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m14").applied
