"""PR-SB-2.4: NETCONF over TLS (RFC 7589) against an in-process server with throwaway certificates."""

import pytest

from app.netconf_ssh import NetconfSshError, send_edit_config, send_get_config
from app.netconf_tls import parse_tls_uri

from netconf_ssh_server import Behaviour
from netconf_tls_server import NetconfTlsTestServer, Pki

CAND = ("urn:ietf:params:netconf:base:1.0", "urn:ietf:params:netconf:base:1.1", "urn:ietf:params:netconf:capability:candidate:1.0")


@pytest.fixture
def pki(tmp_path):
    ca = Pki(tmp_path)
    server_cert, server_key = ca.issue("server", server=True)
    client_cert, client_key = ca.issue("client")
    return ca, (server_cert, server_key), (client_cert, client_key)


@pytest.fixture
def lab(pki, monkeypatch):
    """start(behaviour) -> server; the client's own credential is the named one `ru-1` (the shared one is left unset)."""
    ca, (server_cert, server_key), (client_cert, client_key) = pki
    servers = []

    def start(behaviour=None):
        server = NetconfTlsTestServer(server_cert, server_key, ca.ca_file, behaviour)
        servers.append(server)
        monkeypatch.setenv("NETCONF_CRED_RU_1_CERT_FILE", client_cert)
        monkeypatch.setenv("NETCONF_CRED_RU_1_KEY_FILE", client_key)
        monkeypatch.setenv("NETCONF_CRED_RU_1_CA_FILE", ca.ca_file)
        return server

    yield start
    for server in servers:
        server.close()


def test_the_uri(monkeypatch):
    assert parse_tls_uri("tls://gnb-1.lab") == ("gnb-1.lab", 6513)
    assert parse_tls_uri("tls://10.0.0.1:6514?model=smo-lab&datastore=candidate") == ("10.0.0.1", 6514)
    for bad in ("ssh://h", "tls://", "tls://user@h", "tls://h:0", "tls://h?model=nope", "http://h"):
        with pytest.raises(NetconfSshError):
            parse_tls_uri(bad)


def test_a_write_and_a_read_over_mutual_tls(lab):
    server = lab(Behaviour())
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="t1", credential_ref="ru-1")
    assert result.applied and 'message-id="t1"' in server.behaviour.received[-1]
    assert send_get_config(server.uri, "ME-1", message_id="t2", credential_ref="ru-1") == {"administrativeState": "UNLOCKED"}


def test_the_model_payload_and_the_candidate_datastore_work_over_tls_too(lab):
    server = lab(Behaviour(caps=CAND))
    result = send_edit_config(server.uri + "?model=smo-lab&datastore=candidate", "ME-1", {"txPower": 30}, message_id="t3",
                              managed_function_ref="101", credential_ref="ru-1")
    assert result.applied
    steps = [t for t in server.behaviour.received]
    assert "<lock>" in steps[0] and "<tx-power>30</tx-power>" in steps[1] and "<commit/>" in steps[2] and "<unlock>" in steps[3]


def test_a_client_certificate_from_another_ca_is_refused_by_the_server(lab, pki, tmp_path, monkeypatch):
    server = lab(Behaviour())
    rogue = Pki(tmp_path, name="rogue")
    cert, key = rogue.issue("client")
    monkeypatch.setenv("NETCONF_CRED_RU_1_CERT_FILE", cert)
    monkeypatch.setenv("NETCONF_CRED_RU_1_KEY_FILE", key)
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="t4", credential_ref="ru-1")
    assert not result and result.reason == "NETCONF_RPC_FAILED" and server.behaviour.received == []


def test_a_server_certificate_the_client_does_not_trust_is_refused(lab, tmp_path, monkeypatch):
    server = lab(Behaviour())
    other = Pki(tmp_path, name="other")
    monkeypatch.setenv("NETCONF_CRED_RU_1_CA_FILE", other.ca_file)                  # the client now trusts a different CA
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="t5", credential_ref="ru-1")
    assert not result and result.reason == "NETCONF_RPC_FAILED" and "not trusted" in result.detail


def test_a_server_certificate_for_another_host_is_refused(pki, tmp_path, monkeypatch):
    ca, _, (client_cert, client_key) = pki
    wrong_cert, wrong_key = Pki.issue(ca, "other-host", server=True)                # names 127.0.0.1 only: connect by name instead
    server = NetconfTlsTestServer(wrong_cert, wrong_key, ca.ca_file)
    for var, value in (("CERT_FILE", client_cert), ("KEY_FILE", client_key), ("CA_FILE", ca.ca_file)):
        monkeypatch.setenv(f"NETCONF_CRED_RU_1_{var}", value)
    try:
        result = send_edit_config(f"tls://localhost:{server.port}", "ME-1", {"a": "1"}, message_id="t6", credential_ref="ru-1")
        assert not result and result.reason == "NETCONF_RPC_FAILED" and "does not name the host" in result.detail
    finally:
        server.close()


def test_an_expired_client_certificate_is_refused(lab, pki, monkeypatch):
    ca = pki[0]
    server = lab(Behaviour())
    cert, key = ca.issue("old-client", expired=True)
    monkeypatch.setenv("NETCONF_CRED_RU_1_CERT_FILE", cert)
    monkeypatch.setenv("NETCONF_CRED_RU_1_KEY_FILE", key)
    assert not send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="t7", credential_ref="ru-1").applied


def test_an_incomplete_or_unreadable_credential_is_refused_before_connecting(lab, monkeypatch):
    server = lab(Behaviour())
    monkeypatch.delenv("NETCONF_CRED_RU_1_KEY_FILE")
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="t8", credential_ref="ru-1")
    assert not result and "NETCONF_CRED_RU_1_KEY_FILE" in result.detail and server.handshakes_failed == 0
    monkeypatch.setenv("NETCONF_CRED_RU_1_KEY_FILE", "/nonexistent/key")
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="t9", credential_ref="ru-1")
    assert not result and result.detail == "TLS credential files cannot be loaded: FileNotFoundError"


def test_a_named_credential_has_no_fallback_to_the_shared_one(lab, monkeypatch, pki):
    server = lab(Behaviour())
    monkeypatch.setenv("NETCONF_TLS_CERT_FILE", pki[2][0])
    monkeypatch.setenv("NETCONF_TLS_KEY_FILE", pki[2][1])
    monkeypatch.setenv("NETCONF_TLS_CA_FILE", pki[0].ca_file)
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="t10").applied                         # no ref: the shared files
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="t11", credential_ref="unconfigured")
    assert not result and "NETCONF_CRED_UNCONFIGURED_CERT_FILE" in result.detail


def test_nothing_listening_is_unreachable_and_a_silent_server_times_out(lab, monkeypatch):
    import socket
    from app import netconf_ssh
    lab(Behaviour())
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    assert send_edit_config(f"tls://127.0.0.1:{port}", "ME-1", {"a": "1"}, message_id="t12", credential_ref="ru-1").reason == "NETCONF_UNREACHABLE"
    server = lab(Behaviour(silent=True))
    monkeypatch.setattr(netconf_ssh, "NETCONF_TIMEOUT_SECONDS", 0.5)
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="t13", credential_ref="ru-1").reason == "NETCONF_TIMEOUT"


def test_a_rotated_client_certificate_is_used_by_the_next_connect_without_a_restart(lab, pki, tmp_path):
    """PR-SEC-4.8: the runbook replaces the certificate and key files; the next connect uses them. The rogue pair in between is what a half-done rotation
    looks like (the new files from a CA the element does not trust): refused until the right pair is in place."""
    ca, _, (client_cert, client_key) = pki
    server = lab(Behaviour())
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="t20", credential_ref="ru-1").applied
    old_cert, old_key = open(client_cert).read(), open(client_key).read()
    rogue_cert, rogue_key = Pki(tmp_path, name="rogue").issue("client")
    open(client_cert, "w").write(open(rogue_cert).read())
    open(client_key, "w").write(open(rogue_key).read())
    assert not send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="t21", credential_ref="ru-1").applied
    new_cert, new_key = ca.issue("client-2")                                              # the rotated pair, from the CA the element trusts
    open(client_cert, "w").write(open(new_cert).read())
    open(client_key, "w").write(open(new_key).read())
    assert (open(client_cert).read(), open(client_key).read()) != (old_cert, old_key)
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="t22", credential_ref="ru-1").applied
