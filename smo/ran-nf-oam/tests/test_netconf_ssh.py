"""NETCONF over SSH (PR-SB-1.3): the session wrapper against an in-process SSH server (tests/netconf_ssh_server.py)."""

import pytest

from app import netconf_ssh
from app.netconf_ssh import NetconfSession, NetconfSshError, parse_ssh_uri, send_edit_config, send_edit_configs, send_get_config

from netconf_ssh_server import Behaviour, NetconfTestServer


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """Fixture factory: `lab(behaviour, trust=True, **kw)` starts an in-process NETCONF-over-SSH server, writes a known_hosts file that trusts its
    host key (or an empty one when `trust` is False) and sets the shared credential variables; all servers are closed afterwards.
    """
    servers = []

    def start(behaviour=None, trust=True, **kw):
        server = NetconfTestServer(behaviour, **kw)
        servers.append(server)
        known = tmp_path / "known_hosts"
        known.write_text(server.known_hosts_line() if trust else "")
        monkeypatch.setenv("NETCONF_SSH_KNOWN_HOSTS", str(known))
        monkeypatch.setenv("NETCONF_SSH_PASSWORD", "secret")
        return server

    yield start
    for server in servers:
        server.close()


def test_uri_parsing():
    """An `ssh://user@host[:port]` URI parses (default port 830); another scheme, a missing user or host, a bad port or a non-numeric port is
    refused.
    """
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
    """The session uses end-of-message framing when the server offers only base:1.0 and chunked framing when both offer 1.1, and an edit-config
    works in each.
    """
    server = lab(Behaviour(caps=caps))
    with NetconfSession(server.uri, timeout=5) as session:
        assert session.chunked is chunked
        assert "urn:ietf:params:netconf:base:1.0" in session.server_capabilities
    result = send_edit_config(server.uri, "ME-1", {"administrativeState": "LOCKED"}, message_id="m1", managed_function_ref="NRCellDU=1")
    assert result.applied and result.reason is None
    assert 'message-id="m1"' in server.behaviour.received[-1] and "<administrativeState>LOCKED</administrativeState>" in server.behaviour.received[-1]


def test_a_reply_arriving_in_pieces_is_reassembled(lab):
    """A reply the server sends in several small writes is reassembled before it is parsed."""
    server = lab(Behaviour(split=True))
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m2").applied


def test_get_config_returns_the_attributes(lab):
    """A get-config over SSH returns the managed object's attributes."""
    server = lab()
    assert send_get_config(server.uri, "ME-1", message_id="g1") == {"administrativeState": "UNLOCKED"}


def test_rpc_error_is_not_applied_and_not_retryable(lab):
    """An rpc-error reply is NETCONF_RPC_FAILED and not retryable."""
    server = lab(Behaviour(edit_reply="<rpc-error><error-tag>invalid-value</error-tag></rpc-error>"))
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m3")
    assert not result and result.reason == "NETCONF_RPC_FAILED" and not result.retryable


def test_a_silent_server_times_out_retryably(lab, monkeypatch):
    """A server that never answers gives NETCONF_TIMEOUT, which is retryable."""
    server = lab(Behaviour(silent=True))
    monkeypatch.setattr(netconf_ssh, "NETCONF_TIMEOUT_SECONDS", 0.5)
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m4")
    assert not result and result.reason == "NETCONF_TIMEOUT" and result.retryable


def test_a_closed_port_is_unreachable_retryably(lab):
    """A port with nothing listening gives NETCONF_UNREACHABLE, which is retryable."""
    lab()
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]                          # nothing listens here once the probe closes
    result = send_edit_config(f"ssh://netconf@127.0.0.1:{port}", "ME-1", {"a": "1"}, message_id="m5")
    assert result.reason == "NETCONF_UNREACHABLE" and result.retryable


def test_the_server_hanging_up_after_hello_is_unreachable(lab):
    """A server that closes the session after its hello gives NETCONF_UNREACHABLE."""
    server = lab(Behaviour(close_after_hello=True))
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m6").reason == "NETCONF_UNREACHABLE"


def test_an_unknown_host_key_is_refused(lab):
    """A server whose host key is not in the known hosts is refused (never trust on first use) and receives nothing."""
    server = lab(trust=False)
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m7")
    assert not result and result.reason == "NETCONF_RPC_FAILED" and not server.behaviour.received


def test_a_changed_host_key_is_refused(lab, tmp_path, monkeypatch):
    """A server presenting a different key from the one known for its address is refused and receives nothing."""
    server = lab()
    other = NetconfTestServer()
    try:
        (tmp_path / "known_hosts").write_text(other.known_hosts_line().replace(f":{other.port}]", f":{server.port}]").replace(f"[127.0.0.1]:{other.port}", f"[127.0.0.1]:{server.port}"))
        result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m8")
        assert not result and result.reason == "NETCONF_RPC_FAILED" and not server.behaviour.received
    finally:
        other.close()


def test_no_known_hosts_configured_refuses(lab, monkeypatch):
    """With no known-hosts file and no pinned keys the connection is refused before anything is sent."""
    server = lab()
    monkeypatch.delenv("NETCONF_SSH_KNOWN_HOSTS")
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m9")
    assert result.reason == "NETCONF_RPC_FAILED" and not server.behaviour.received


def test_wrong_password_is_refused(lab, monkeypatch):
    """A refused password is NETCONF_RPC_FAILED and not retryable, since repeating it cannot succeed."""
    server = lab()
    monkeypatch.setenv("NETCONF_SSH_PASSWORD", "wrong")
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m11")
    assert not result and result.reason == "NETCONF_RPC_FAILED" and not result.retryable


def test_no_netconf_subsystem_is_refused(lab):
    """A server that does not offer the netconf subsystem is NETCONF_RPC_FAILED."""
    server = lab(no_subsystem=True)
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m12").reason == "NETCONF_RPC_FAILED"


@pytest.mark.parametrize("hello_text", [
    "<not-a-hello/>",
    '<hello xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><capabilities><capability>urn:other</capability></capabilities></hello>',
    "this is not xml",
])
def test_a_bad_hello_is_refused(lab, hello_text):
    """A hello that is not a hello, offers no base capability or is not XML is NETCONF_RPC_FAILED."""
    server = lab(Behaviour(hello_text=hello_text))
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m13").reason == "NETCONF_RPC_FAILED"


def test_the_password_file_convention_is_honoured(lab, tmp_path, monkeypatch):
    """The shared password may come from a `_FILE` variable (its trailing newline is dropped) as well as from the variable itself."""
    server = lab()
    secret = tmp_path / "pw"
    secret.write_text("secret\n")
    monkeypatch.delenv("NETCONF_SSH_PASSWORD")
    monkeypatch.setenv("NETCONF_SSH_PASSWORD_FILE", str(secret))
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="m14").applied


LAB_DATA = ('<lab xmlns="urn:smo:lab"><cell><id>101</id><administrative-state>unlocked</administrative-state><tx-power>40</tx-power></cell>'
            '<cell><id>102</id><administrative-state>locked</administrative-state><tx-power>33</tx-power></cell></lab>')


def test_a_model_uri_reads_the_real_data_nodes_not_the_managed_object_shape(lab):
    """PR-SB-1.5: ?model=smo-lab sends a subtree filter on the lab model's list entry and maps the leaves back to SMO attribute names."""
    server = lab(Behaviour(data=LAB_DATA))
    uri = server.uri + "?model=smo-lab"
    assert send_get_config(uri, "SubNetwork=A,ManagedElement=ME-1", message_id="g2",
                           managed_function_ref="GNBDUFunction=1,NRCellDU=102") == {"administrativeState": "locked", "txPower": "33"}
    sent = server.behaviour.received[-1]
    assert 'type="subtree"' in sent and '<lab xmlns="urn:smo:lab"><cell><id>102</id></cell></lab>' in sent and "managed-object" not in sent
    assert send_get_config(uri, "ME-1", message_id="g3", managed_function_ref="101") == {"administrativeState": "unlocked", "txPower": "40"}
    assert send_get_config(uri, "ME-1", message_id="g4", managed_function_ref="NRCellDU=999") == {}


def test_a_model_uri_is_validated_when_it_is_parsed():
    """An unknown model, a repeated model option or an unknown option name is refused when the URI is parsed, so a typo never falls back to another
    payload shape.
    """
    assert parse_ssh_uri("ssh://u@h:830?model=smo-lab") == ("u", "h", 830)
    for bad in ("ssh://u@h?model=nope", "ssh://u@h?model=smo-lab&model=smo-lab", "ssh://u@h?mode=smo-lab"):
        with pytest.raises(NetconfSshError):
            parse_ssh_uri(bad)


def test_edit_config_to_a_model_uri_writes_the_list_entry_with_the_operation_on_it(lab):
    """PR-SB-1.6: the write goes to <lab><cell> with the key and kebab-case leaves, `nc:operation` on the entry, never <managed-object>."""
    server = lab(Behaviour(data=LAB_DATA))
    uri = server.uri + "?model=smo-lab"
    result = send_edit_config(uri, "ME-1", {"txPower": 30, "administrativeState": "locked"}, message_id="w1", operation="replace",
                              managed_function_ref="GNBDUFunction=1,NRCellDU=102")
    assert result.applied
    sent = server.behaviour.received[-1]
    assert "managed-object" not in sent and "<target><running/></target>" in sent
    assert 'nc:operation="replace"' in sent and "<id>102</id><tx-power>30</tx-power><administrative-state>locked</administrative-state>" in sent
    send_edit_config(uri, "ME-1", {"txPower": 1}, message_id="w2", operation="delete", managed_function_ref="102")
    assert 'nc:operation="delete"' in server.behaviour.received[-1] and "tx-power" not in server.behaviour.received[-1]


def test_a_value_is_escaped_and_an_unknown_operation_sends_nothing(lab):
    """On the model payload a value is XML-escaped, and an operation the model path does not know fails with its name and sends nothing."""
    server = lab(Behaviour(data=LAB_DATA))
    uri = server.uri + "?model=smo-lab"
    send_edit_config(uri, "ME-1", {"description": "a<b&c"}, message_id="w3", managed_function_ref="101")
    assert "a&lt;b&amp;c" in server.behaviour.received[-1]
    before = len(server.behaviour.received)
    result = send_edit_config(uri, "ME-1", {"txPower": 1}, message_id="w4", operation="explode", managed_function_ref="101")
    assert not result and result.reason == "NETCONF_RPC_FAILED" and "explode" in result.detail and len(server.behaviour.received) == before


ERRORS = [
    ("<rpc-error><error-type>application</error-type><error-tag>invalid-value</error-tag><error-severity>error</error-severity>"
     "<error-path>/lab/cell/tx-power</error-path><error-message>out of range</error-message></rpc-error>",
     "invalid-value (a value is not acceptable) at /lab/cell/tx-power: out of range"),
    ("<rpc-error><error-tag>lock-denied</error-tag></rpc-error>", "lock-denied (the lock is held by another session)"),
    ("<rpc-error><error-tag>data-missing</error-tag><error-info><bad-element>cell</bad-element></error-info></rpc-error>",
     "data-missing (the data does not exist) at cell"),
    ("<rpc-error><error-tag>vendor-specific</error-tag><error-message>x</error-message></rpc-error>", "vendor-specific: x"),
    ("<rpc-error/>", "rpc-error"),
    ("<rpc-error><error-tag>operation-failed</error-tag><error-message>" + "y" * 500 + "</error-message></rpc-error>", None),
]


@pytest.mark.parametrize("reply,detail", ERRORS)
def test_rpc_error_becomes_a_detail_and_the_reason_stays_the_stable_code(lab, reply, detail):
    """PR-SB-1.7: one unit case per tag family; the code is still NETCONF_RPC_FAILED and still not retryable."""
    server = lab(Behaviour(edit_reply=reply))
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="e1")
    assert not result and result.reason == "NETCONF_RPC_FAILED" and not result.retryable
    if detail is None:
        assert len(result.detail) == 300 and result.detail.startswith("operation-failed (the operation failed): yyy")
    else:
        assert result.detail == detail


def test_attribute_names_round_trip():
    """camelCase attribute names and kebab-case YANG leaves convert both ways."""
    from app.yang_payload import to_attribute_name, to_yang_name
    for attribute, leaf in (("administrativeState", "administrative-state"), ("txPower", "tx-power"), ("id", "id")):
        assert to_yang_name(attribute) == leaf and to_attribute_name(leaf) == attribute


CAND_CAPS = ("urn:ietf:params:netconf:base:1.0", "urn:ietf:params:netconf:base:1.1", "urn:ietf:params:netconf:capability:candidate:1.0")
ERR = lambda tag, msg="": f"<rpc-error><error-tag>{tag}</error-tag><error-message>{msg}</error-message></rpc-error>"  # noqa: E731


def _steps(server):
    """The RPCs the server received, by name, in order."""
    import re
    names = []
    for text in server.behaviour.received:
        m = re.search(r'message-id="[^"]*-(lock|commit|discard|unlock)"', text)
        names.append(m.group(1) if m else ("edit-config" if "<edit-config>" in text else "other"))
    return names


def test_a_candidate_write_is_lock_edit_commit_unlock(lab):
    """PR-SB-1.8: the edit goes to <candidate/>, then commit, and the lock is always released."""
    server = lab(Behaviour(caps=CAND_CAPS))
    result = send_edit_config(server.uri + "?datastore=candidate", "ME-1", {"a": "1"}, message_id="c1")
    assert result.applied and _steps(server) == ["lock", "edit-config", "commit", "unlock"]
    assert "<target><candidate/></target>" in server.behaviour.received[1]


def test_a_refused_edit_is_discarded_and_unlocked_and_nothing_is_committed(lab):
    """On the candidate datastore a refused edit is followed by discard and unlock, with no commit, and the detail carries the server's reason."""
    server = lab(Behaviour(caps=CAND_CAPS, edit_reply=ERR("invalid-value", "out of range")))
    result = send_edit_config(server.uri + "?datastore=candidate", "ME-1", {"a": "1"}, message_id="c2")
    assert not result and result.reason == "NETCONF_RPC_FAILED" and not result.retryable
    assert result.detail == "edit-config: invalid-value (a value is not acceptable): out of range"
    assert _steps(server) == ["lock", "edit-config", "discard", "unlock"]


def test_a_refused_commit_is_discarded_and_unlocked(lab):
    """A refused commit is followed by discard and unlock, and the detail names the commit step."""
    server = lab(Behaviour(caps=CAND_CAPS, step_replies={"commit": ERR("operation-failed", "validation failed")}))
    result = send_edit_config(server.uri + "?datastore=candidate", "ME-1", {"a": "1"}, message_id="c3")
    assert not result and result.detail == "commit: operation-failed (the operation failed): validation failed"
    assert _steps(server) == ["lock", "edit-config", "commit", "discard", "unlock"]


def test_a_denied_lock_touches_nothing(lab):
    """A refused lock ends the transaction at once: no edit, discard or unlock is sent."""
    server = lab(Behaviour(caps=CAND_CAPS, step_replies={"lock": ERR("lock-denied")}))
    result = send_edit_config(server.uri + "?datastore=candidate", "ME-1", {"a": "1"}, message_id="c4")
    assert not result and result.detail == "lock: lock-denied (the lock is held by another session)"
    assert _steps(server) == ["lock"]


def test_a_server_without_a_candidate_datastore_gets_no_rpc(lab):
    """A server that does not offer the candidate capability is not sent any RPC after the hello, and the detail says so."""
    server = lab(Behaviour())
    result = send_edit_config(server.uri + "?datastore=candidate", "ME-1", {"a": "1"}, message_id="c5")
    assert not result and "candidate" in result.detail and server.behaviour.received == []


def test_a_failed_unlock_after_a_good_commit_does_not_undo_the_write(lab):
    """A failed unlock after a successful commit does not turn the applied write into a failure."""
    server = lab(Behaviour(caps=CAND_CAPS, step_replies={"unlock": ERR("operation-failed")}))
    assert send_edit_config(server.uri + "?datastore=candidate", "ME-1", {"a": "1"}, message_id="c6").applied
    assert _steps(server) == ["lock", "edit-config", "commit", "unlock"]


def test_a_model_write_to_the_candidate_names_the_candidate(lab):
    """A model-payload write to the candidate datastore names the candidate as its edit target."""
    server = lab(Behaviour(caps=CAND_CAPS))
    send_edit_config(server.uri + "?model=smo-lab&datastore=candidate", "ME-1", {"txPower": 30}, message_id="c7", managed_function_ref="101")
    assert "<target><candidate/></target>" in server.behaviour.received[1] and "<tx-power>30</tx-power>" in server.behaviour.received[1]


def test_the_datastore_option_is_validated():
    """A datastore other than `running` or `candidate`, or a repeated option, is refused when the URI is parsed."""
    assert parse_ssh_uri("ssh://u@h?datastore=candidate") == ("u", "h", 830)
    for bad in ("ssh://u@h?datastore=startup", "ssh://u@h?datastore=candidate&datastore=running"):
        with pytest.raises(NetconfSshError):
            parse_ssh_uri(bad)


# --- PR-SB-2.1/2.2: per-endpoint credentials -----------------------------------------------------------------------------------------------


def test_a_credential_ref_is_resolved_at_connect_time_and_wins_over_the_shared_credential(lab, monkeypatch):
    """The server accepts "secret"; the shared password is wrong, so only the endpoint's own credential can succeed."""
    server = lab(Behaviour())
    monkeypatch.setenv("NETCONF_SSH_PASSWORD", "the-shared-one-is-wrong")
    assert not send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="k1").applied                      # no ref: the shared credential
    monkeypatch.setenv("NETCONF_CRED_GNB_1_PASSWORD", "secret")
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="k2", credential_ref="gnb-1").applied
    assert send_get_config(server.uri, "ME-1", message_id="k3", credential_ref="gnb-1") is not None


def test_a_credential_can_come_from_a_mounted_file(lab, monkeypatch, tmp_path):
    """A named credential's password may come from its `_FILE` variable."""
    server = lab(Behaviour())
    monkeypatch.delenv("NETCONF_SSH_PASSWORD")
    secret = tmp_path / "gnb2"
    secret.write_text("secret\n")
    monkeypatch.setenv("NETCONF_CRED_GNB2_PASSWORD_FILE", str(secret))
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="k4", credential_ref="gnb2").applied


def test_a_reference_that_resolves_to_nothing_is_refused_not_replaced_by_the_shared_credential(lab, monkeypatch):
    """A credential reference with no configured secret is refused and never replaced by the shared credential, even when that one would work."""
    server = lab(Behaviour())
    monkeypatch.setenv("NETCONF_SSH_PASSWORD", "secret")                                  # would work, and must not be used
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="k5", credential_ref="unconfigured")
    assert not result and result.reason == "NETCONF_RPC_FAILED" and "not configured" in result.detail and server.behaviour.received == []


def test_an_unreadable_secret_file_names_the_variable_never_a_value(lab, monkeypatch):
    """A secret file that cannot be read gives an error that names the credential and the kind of error, never a value."""
    server = lab(Behaviour())
    monkeypatch.setenv("NETCONF_CRED_BROKEN_PASSWORD_FILE", "/nonexistent/secret")
    result = send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="k6", credential_ref="broken")
    assert not result and result.detail == "credential 'broken': SecretFileError"


def test_credentials_for_reads_the_key_file_and_the_shared_fallback(monkeypatch, tmp_path):
    """A named credential gives its key file with no password, and no name gives the shared password and key file."""
    from app.netconf_ssh import credentials_for
    monkeypatch.setenv("NETCONF_CRED_RU_1_KEY_FILE", "/keys/ru-1")
    monkeypatch.setenv("NETCONF_SSH_PASSWORD", "shared")
    monkeypatch.setenv("NETCONF_SSH_KEY_FILE", "/keys/shared")
    assert credentials_for("ru-1") == (None, "/keys/ru-1")
    assert credentials_for(None) == ("shared", "/keys/shared")


@pytest.mark.parametrize("ref", ["Hunter2!", "p@ssw0rd", "UPPER", "1abc", "has space", "a" * 64, "", "x/y"])
def test_a_value_that_is_not_shaped_like_a_name_is_refused_without_echoing_it(ref, monkeypatch):
    """A credential reference that is not a lower-case name (which might be a pasted secret) is refused and the error does not repeat it."""
    from app.netconf_ssh import check_credential_ref
    with pytest.raises(ValueError) as exc:
        check_credential_ref(ref)
    assert ref not in str(exc.value) or ref == ""


def test_a_name_that_names_no_configured_credential_is_refused(monkeypatch):
    """A well-shaped credential name with no secret configured on the service is refused, and accepted once one is configured."""
    from app.netconf_ssh import check_credential_ref
    with pytest.raises(ValueError, match="no credential configured"):
        check_credential_ref("never-configured")
    monkeypatch.setenv("NETCONF_CRED_NOW_CONFIGURED_PASSWORD", "x")
    check_credential_ref("now-configured")


# --- PR-SEC-4.8: rotating an element's credential --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("variable,ref", [("NETCONF_SSH_PASSWORD_FILE", None), ("NETCONF_CRED_GNB_7_PASSWORD_FILE", "gnb-7")])
def test_a_rotated_password_file_is_used_by_the_next_connect_without_a_restart(lab, monkeypatch, tmp_path, variable, ref):
    """The rotation runbook (docs/SECRETS.md): change the password on the element, then replace the mounted file. Between the two the old value is
    refused; once the file holds the new one the next connect succeeds, in the same process."""
    server = lab(Behaviour())
    monkeypatch.delenv("NETCONF_SSH_PASSWORD")
    secret = tmp_path / "pw"
    secret.write_text("secret\n")
    monkeypatch.setenv(variable, str(secret))
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="r1", credential_ref=ref).applied
    server.password = "rotated"                                                                 # step 1: the element's password changes
    assert not send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="r2", credential_ref=ref).applied   # the file still holds the old one
    secret.write_text("rotated\n")                                                              # step 2: the mounted file is replaced
    assert send_edit_config(server.uri, "ME-1", {"a": "1"}, message_id="r3", credential_ref=ref).applied


# --- PR-SB-1.10: several edits of one element as one candidate transaction -----------------------------------------------------------------


EDITS = [{"target_ref": "ME-1", "attribute_changes": {"a": "1"}}, {"target_ref": "ME-1", "attribute_changes": {"b": "2"}},
         {"target_ref": "ME-1", "attribute_changes": {"c": "3"}}]


def test_several_edits_are_locked_once_and_committed_once(lab):
    """Several edits of one element are one candidate transaction: one lock, each edit, one commit, one unlock."""
    server = lab(Behaviour(caps=CAND_CAPS))
    results = send_edit_configs(server.uri + "?datastore=candidate", EDITS, message_id="t1")
    assert [r.applied for r in results] == [True, True, True]
    assert _steps(server) == ["lock", "edit-config", "edit-config", "edit-config", "commit", "unlock"]


def test_a_refused_edit_rolls_back_the_ones_before_it_and_skips_the_ones_after(lab):
    """When one edit of a transaction is refused, the earlier ones are rolled back (NETCONF_TRANSACTION_ABORTED), the later ones are never sent,
    nothing is committed, and none is retryable.
    """
    server = lab(Behaviour(caps=CAND_CAPS, edit_replies=["<ok/>", ERR("invalid-value", "out of range")]))
    results = send_edit_configs(server.uri + "?datastore=candidate", EDITS, message_id="t2")
    assert [r.applied for r in results] == [False, False, False]
    assert [r.reason for r in results] == ["NETCONF_TRANSACTION_ABORTED", "NETCONF_RPC_FAILED", "NETCONF_TRANSACTION_ABORTED"]
    assert results[1].detail == "edit-config: invalid-value (a value is not acceptable): out of range"
    assert "sub-change 2 of 3 was refused" in results[0].detail and "sub-change 2 of 3 was refused" in results[2].detail
    assert not any(r.retryable for r in results)
    assert _steps(server) == ["lock", "edit-config", "edit-config", "discard", "unlock"]       # the third was never sent, and nothing was committed


def test_a_refused_commit_is_every_edits_result(lab):
    """A refused commit is the result of every edit in the transaction."""
    server = lab(Behaviour(caps=CAND_CAPS, step_replies={"commit": ERR("operation-failed", "validation failed")}))
    results = send_edit_configs(server.uri + "?datastore=candidate", EDITS[:2], message_id="t3")
    assert [r.applied for r in results] == [False, False]
    assert {r.detail for r in results} == {"commit: operation-failed (the operation failed): validation failed"}
    assert _steps(server) == ["lock", "edit-config", "edit-config", "commit", "discard", "unlock"]


def test_a_refused_lock_changes_nothing_for_any_edit(lab):
    """A refused lock fails every edit of the transaction and sends nothing else."""
    server = lab(Behaviour(caps=CAND_CAPS, step_replies={"lock": ERR("lock-denied", "held by another session")}))
    results = send_edit_configs(server.uri + "?datastore=candidate", EDITS[:2], message_id="t4")
    assert [r.applied for r in results] == [False, False] and _steps(server) == ["lock"]


def test_a_server_that_cannot_be_reached_fails_every_edit_the_same_retryable_way(tmp_path, monkeypatch):
    """When the server cannot be reached every edit of the transaction fails with the same retryable NETCONF_UNREACHABLE."""
    monkeypatch.setenv("NETCONF_SSH_PASSWORD", "secret")
    (tmp_path / "known_hosts").write_text("")
    monkeypatch.setenv("NETCONF_SSH_KNOWN_HOSTS", str(tmp_path / "known_hosts"))
    results = send_edit_configs("ssh://netconf@127.0.0.1:1?datastore=candidate", EDITS[:2], message_id="t5")
    assert [r.applied for r in results] == [False, False] and {r.reason for r in results} == {"NETCONF_UNREACHABLE"}
    assert all(r.retryable for r in results)


def test_a_transaction_needs_the_candidate_datastore_in_the_uri(lab):
    """A multi-edit transaction without `?datastore=candidate` in the URI is a ValueError and nothing is sent."""
    server = lab(Behaviour(caps=CAND_CAPS))
    with pytest.raises(ValueError):
        send_edit_configs(server.uri, EDITS[:2], message_id="t6")
    assert server.behaviour.received == []
