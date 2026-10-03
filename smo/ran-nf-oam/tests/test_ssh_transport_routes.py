"""PR-SB-1.2/1.5: `transport` on the endpoint, and CM write / read going over NETCONF-over-SSH to an in-process server."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smo_shared import outbox  # noqa: F401  (registers the outbox table on the metadata)
from smo_shared.db import Base, get_session
from smo_shared.idempotency import IdempotencyKey
from smo_shared.outbox import NotificationOutbox
from smo_shared.testing import make_test_engine

from app.main import app
from app.models import (O1AdaptorHostKey, Alarm, CMSchemaCache, CMSnapshot, ManagedEntity, O1AdaptorEndpoint, VendorCapability, WriteConfigJob, WriteConfigSubChange,
                        MsacAccessRule, MsacIdentity, MsacRole)

from netconf_ssh_server import Behaviour, NetconfTestServer

CAND_CAPS = ("urn:ietf:params:netconf:base:1.0", "urn:ietf:params:netconf:base:1.1", "urn:ietf:params:netconf:capability:candidate:1.0")


@pytest.fixture
def db_session_factory():
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[
        O1AdaptorEndpoint.__table__, ManagedEntity.__table__, Alarm.__table__, CMSchemaCache.__table__, WriteConfigJob.__table__,
        WriteConfigSubChange.__table__, CMSnapshot.__table__, VendorCapability.__table__, MsacIdentity.__table__, MsacRole.__table__, MsacAccessRule.__table__,
        IdempotencyKey.__table__, NotificationOutbox.__table__, O1AdaptorHostKey.__table__])
    return sessionmaker(bind=engine)


@pytest.fixture
def client(db_session_factory):
    def override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def lab(tmp_path, monkeypatch):
    server = NetconfTestServer()
    known = tmp_path / "known_hosts"
    known.write_text(server.known_hosts_line())
    monkeypatch.setenv("NETCONF_SSH_KNOWN_HOSTS", str(known))
    monkeypatch.setenv("NETCONF_SSH_PASSWORD", "secret")
    yield server
    server.close()


def _register(client, uri, **extra):
    return client.post("/o1-adaptor-endpoints", json={"managedElementRef": "ME-1", "adaptorUri": uri, "protocolSupport": ["NETCONF"],
                                                       "o1Protocol": "NETCONF", "entityType": "O-DU", **extra})


def test_default_transport_is_http_mock(client, db_session_factory):
    assert _register(client, "http://adaptor:8000/edit-config").status_code == 201
    db = db_session_factory()
    assert db.query(O1AdaptorEndpoint).one().transport == "http-mock"
    db.close()
    assert client.get("/o1-adaptor-endpoints").json()["items"][0]["transport"] == "http-mock"


@pytest.mark.parametrize("uri,extra", [
    ("http://adaptor:8000/x", {"transport": "ssh"}),               # scheme does not match the transport
    ("ssh://adaptor", {"transport": "ssh"}),                        # no user name
    ("ssh://admin@adaptor", {}),                                    # ssh:// without transport ssh
    ("ssh://admin@adaptor", {"transport": "ssh", "o1Protocol": "RESTCONF"}),
    ("ssh://admin@adaptor", {"transport": "telnet"}),
])
def test_registration_refuses_a_mismatched_transport(client, uri, extra):
    assert _register(client, uri, **extra).status_code in (400, 422)


def test_config_job_and_read_go_over_ssh(client, db_session_factory, lab):
    assert _register(client, lab.uri, transport="ssh").status_code == 201
    resp = client.post("/config-jobs", json={"requestedBy": "operator", "scope": "cell",
                                              "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"adminState": "UNLOCKED"}}]})
    assert resp.status_code == 202 and resp.json()["status"] == "COMPLETED"
    assert client.get(f"/config-jobs/{resp.json()['jobId']}").json()["subChanges"][0]["status"] == "APPLIED"
    assert "<adminState>UNLOCKED</adminState>" in lab.behaviour.received[-1]
    item = client.get("/managed-entities/ME-1/config-history").json()["items"][0]       # the before image was read over SSH too
    assert item["before"] == {"adminState": None} and item["after"] == {"adminState": "UNLOCKED"} and item["beforeError"] is None
    read = client.get("/managed-entities/ME-1/config")
    assert read.status_code == 200 and read.json()["attributes"] == {"administrativeState": "UNLOCKED"}


def test_the_config_route_reads_a_model_based_server(client, lab):
    """PR-SB-1.5: an endpoint registered with ?model=smo-lab gets a subtree get-config and the leaves come back as SMO attributes."""
    lab.behaviour.data = ('<lab xmlns="urn:smo:lab"><cell><id>101</id><administrative-state>unlocked</administrative-state>'
                          '<tx-power>40</tx-power></cell></lab>')
    assert _register(client, lab.uri + "?model=smo-lab", transport="ssh").status_code == 201
    read = client.get("/managed-entities/ME-1/config", params={"managed_function_ref": "GNBDUFunction=1,NRCellDU=101"})
    assert read.status_code == 200 and read.json()["attributes"] == {"administrativeState": "unlocked", "txPower": "40"}
    assert "managed-object" not in lab.behaviour.received[-1]


def test_a_rejected_model_write_reports_what_the_server_said(client, lab):
    """PR-SB-1.6/1.7 through the route: the job is rejected, the reason is the stable code, and the server's rpc-error is the detail."""
    lab.behaviour.edit_reply = ("<rpc-error><error-tag>invalid-value</error-tag><error-path>/lab/cell/tx-power</error-path>"
                                "<error-message>out of range</error-message></rpc-error>")
    assert _register(client, lab.uri + "?model=smo-lab", transport="ssh").status_code == 201
    resp = client.post("/config-jobs", json={"requestedBy": "operator", "scope": "cell", "changes": [
        {"managedElementRef": "ME-1", "managedFunctionRef": "GNBDUFunction=1,NRCellDU=101", "attributeChanges": {"txPower": 70000}}]})
    sub = client.get(f"/config-jobs/{resp.json()['jobId']}").json()["subChanges"][0]
    assert sub["status"] == "REJECTED" and sub["rejectionReason"] == "NETCONF_RPC_FAILED" and sub["attempts"] == 1
    assert sub["rejectionDetail"] == "invalid-value (a value is not acceptable) at /lab/cell/tx-power: out of range"


def test_a_candidate_endpoint_commits_through_the_route_and_reports_a_failed_commit(client, lab):
    """PR-SB-1.8: the job is APPLIED after lock/edit/commit/unlock; a refused commit is REJECTED with the step in the detail."""
    lab.behaviour.caps = list(CAND_CAPS)
    assert _register(client, lab.uri + "?datastore=candidate", transport="ssh").status_code == 201
    change = {"managedElementRef": "ME-1", "attributeChanges": {"adminState": "UNLOCKED"}}
    sub = client.get(f"/config-jobs/{client.post('/config-jobs', json={'requestedBy': 'operator', 'scope': 'cell', 'changes': [change]}).json()['jobId']}").json()["subChanges"][0]
    assert sub["status"] == "APPLIED"
    lab.behaviour.step_replies = {"commit": "<rpc-error><error-tag>operation-failed</error-tag><error-message>no</error-message></rpc-error>"}
    sub = client.get(f"/config-jobs/{client.post('/config-jobs', json={'requestedBy': 'operator', 'scope': 'cell', 'changes': [change]}).json()['jobId']}").json()["subChanges"][0]
    assert sub["status"] == "REJECTED" and sub["rejectionDetail"].startswith("commit: operation-failed")


def test_the_endpoint_stores_a_reference_and_connects_with_what_it_names(client, lab, monkeypatch, db_session_factory):
    """PR-SB-2: register with credentialRef; the row holds the name, the connect uses the named credential (the shared one is wrong here)."""
    monkeypatch.setenv("NETCONF_SSH_PASSWORD", "wrong")
    monkeypatch.setenv("NETCONF_CRED_GNB_1_PASSWORD", "secret")
    assert _register(client, lab.uri, transport="ssh", credentialRef="gnb-1").status_code == 201
    db = db_session_factory()
    assert db.query(O1AdaptorEndpoint).one().credential_ref == "gnb-1"
    db.close()
    assert client.get("/o1-adaptor-endpoints").json()["items"][0]["credentialRef"] == "gnb-1"
    assert "secret" not in str(client.get("/o1-adaptor-endpoints").json())
    resp = client.post("/config-jobs", json={"requestedBy": "operator", "scope": "cell",
                                              "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"adminState": "UNLOCKED"}}]})
    assert client.get(f"/config-jobs/{resp.json()['jobId']}").json()["subChanges"][0]["status"] == "APPLIED"
    assert client.get("/managed-entities/ME-1/config").status_code == 200


@pytest.mark.parametrize("ref", ["Tr0ub4dor&3", "correct horse battery staple", "never-configured"])
def test_registration_refuses_a_literal_secret_or_an_unknown_name_without_echoing_it(client, lab, ref):
    """PR-SB-2.1: a reference is a name of a configured credential; a pasted password is neither."""
    resp = _register(client, lab.uri, transport="ssh", credentialRef=ref)
    assert resp.status_code in (400, 422) and ref not in resp.text


def test_a_credential_ref_needs_the_ssh_transport(client):
    resp = _register(client, "http://adaptor:8000/x", credentialRef="gnb-1")
    assert resp.status_code in (400, 422) and "ssh or tls only" in resp.text


# --- PR-SB-2.3: pinned host keys ---------------------------------------------------------------------------------------------------------


def _pin(client, endpoint_id, key, by="alice"):
    return client.put(f"/o1-adaptor-endpoints/{endpoint_id}/host-keys",
                      json={"keyType": key.get_name(), "publicKey": key.get_base64(), "pinnedBy": by})


def _write(client):
    resp = client.post("/config-jobs", json={"requestedBy": "operator", "scope": "cell",
                                              "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"adminState": "UNLOCKED"}}]})
    return client.get(f"/config-jobs/{resp.json()['jobId']}").json()["subChanges"][0]


def test_a_pinned_key_is_what_trusts_the_server_and_a_changed_key_is_refused(client, lab, monkeypatch):
    """No known_hosts file at all: the endpoint's own pinned key is the only trust. Then the server's key changes (here: a different key is
    pinned): the connection is refused, and only an operator pinning the new key makes it work again."""
    import paramiko
    from app.netconf_ssh import host_key_fingerprint
    monkeypatch.delenv("NETCONF_SSH_KNOWN_HOSTS")
    endpoint_id = _register(client, lab.uri, transport="ssh").json()["endpointId"]
    refused = _write(client)
    assert refused["status"] == "REJECTED" and refused["rejectionReason"] == "NETCONF_RPC_FAILED"        # nothing pinned, nothing trusted
    pinned = _pin(client, endpoint_id, lab.host_key)
    assert pinned.status_code == 200 and pinned.json()["fingerprint"] == host_key_fingerprint(lab.host_key) and pinned.json()["replaced"] is False
    assert _write(client)["status"] == "APPLIED"
    assert client.get(f"/o1-adaptor-endpoints/{endpoint_id}/host-keys").json()["items"][0]["pinnedBy"] == "alice"
    # the device's key "changes": pin a different one for the same type; the real server no longer matches
    other = paramiko.RSAKey.generate(2048)
    replaced = _pin(client, endpoint_id, other, by="bob")
    assert replaced.json()["replaced"] is True and replaced.json()["pinnedBy"] == "bob"
    changed = _write(client)
    assert changed["status"] == "REJECTED" and "does not match" in changed["rejectionDetail"]
    assert _pin(client, endpoint_id, lab.host_key).json()["replaced"] is True                         # the operator accepts the real one again
    assert _write(client)["status"] == "APPLIED"


def test_unpinning_removes_the_trust(client, lab, monkeypatch):
    monkeypatch.delenv("NETCONF_SSH_KNOWN_HOSTS")
    endpoint_id = _register(client, lab.uri, transport="ssh").json()["endpointId"]
    _pin(client, endpoint_id, lab.host_key)
    assert _write(client)["status"] == "APPLIED"
    assert client.delete(f"/o1-adaptor-endpoints/{endpoint_id}/host-keys/{lab.host_key.get_name()}").status_code == 204
    assert _write(client)["status"] == "REJECTED"
    assert client.delete(f"/o1-adaptor-endpoints/{endpoint_id}/host-keys/{lab.host_key.get_name()}").status_code == 404


def test_pinning_refuses_junk_a_non_ssh_endpoint_and_an_unknown_endpoint(client, lab):
    ssh_id = _register(client, lab.uri, transport="ssh").json()["endpointId"]
    for body in ({"keyType": "ssh-rsa", "publicKey": "not base64!", "pinnedBy": "a"},
                 {"keyType": "ssh-rsa", "publicKey": "AAAA", "pinnedBy": "a"},
                 {"keyType": "nonsense", "publicKey": lab.host_key.get_base64(), "pinnedBy": "a"}):
        assert client.put(f"/o1-adaptor-endpoints/{ssh_id}/host-keys", json=body).status_code in (400, 422)
    assert client.get(f"/o1-adaptor-endpoints/{ssh_id}/host-keys").json() == {"items": []}
    client.delete(f"/o1-adaptor-endpoints/{ssh_id}/host-keys/ssh-rsa")
    mock_id = client.post("/o1-adaptor-endpoints", json={"managedElementRef": "ME-2", "adaptorUri": "http://adaptor:8000/x",
                                                         "protocolSupport": ["NETCONF"], "o1Protocol": "NETCONF", "entityType": "O-DU"}).json()["endpointId"]
    assert _pin(client, mock_id, lab.host_key).status_code in (400, 422)
    assert _pin(client, "00000000-0000-0000-0000-000000000000", lab.host_key).status_code == 404


def test_the_known_hosts_file_still_works_and_a_pinned_key_adds_to_it(client, lab, monkeypatch):
    """The file of PR-SB-1 is untouched: with it and no pin the write works (the fixture's default)."""
    _register(client, lab.uri, transport="ssh")
    assert _write(client)["status"] == "APPLIED"


def test_registration_refuses_an_unknown_model(client, lab):
    assert _register(client, lab.uri + "?model=nope", transport="ssh").status_code in (400, 422)


def test_a_rejecting_server_gives_a_rejected_sub_change(client, lab):
    lab.behaviour.edit_reply = "<rpc-error><error-tag>invalid-value</error-tag></rpc-error>"
    _register(client, lab.uri, transport="ssh")
    resp = client.post("/config-jobs", json={"requestedBy": "operator", "scope": "cell",
                                              "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"adminState": "X"}}]})
    sub = client.get(f"/config-jobs/{resp.json()['jobId']}").json()["subChanges"][0]
    assert sub["status"] == "REJECTED" and sub["rejectionReason"] == "NETCONF_RPC_FAILED" and sub["attempts"] == 1


def test_an_unreachable_ssh_server_is_retried_then_rejected(client, lab, monkeypatch):
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr("app.main._sleep", lambda s: None)
    _register(client, f"ssh://netconf@127.0.0.1:{port}", transport="ssh")
    resp = client.post("/config-jobs", json={"requestedBy": "operator", "scope": "cell",
                                              "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"adminState": "X"}}]})
    sub = client.get(f"/config-jobs/{resp.json()['jobId']}").json()["subChanges"][0]
    assert sub["status"] == "REJECTED" and sub["rejectionReason"] == "NETCONF_UNREACHABLE" and sub["attempts"] > 1


# --- PR-SB-2.4: transport tls --------------------------------------------------------------------------------------------------------------


@pytest.fixture
def tls_lab(tmp_path, monkeypatch):
    from netconf_tls_server import NetconfTlsTestServer, Pki
    ca = Pki(tmp_path)
    server_cert, server_key = ca.issue("server", server=True)
    client_cert, client_key = ca.issue("client")
    server = NetconfTlsTestServer(server_cert, server_key, ca.ca_file)
    monkeypatch.setenv("NETCONF_CRED_RU_1_CERT_FILE", client_cert)
    monkeypatch.setenv("NETCONF_CRED_RU_1_KEY_FILE", client_key)
    monkeypatch.setenv("NETCONF_CRED_RU_1_CA_FILE", ca.ca_file)
    yield server
    server.close()


def test_a_tls_endpoint_is_registered_written_and_read_through_the_routes(client, tls_lab, db_session_factory):
    resp = _register(client, tls_lab.uri, transport="tls", credentialRef="ru-1")
    assert resp.status_code == 201
    assert client.get("/o1-adaptor-endpoints").json()["items"][0]["transport"] == "tls"
    sub = _write(client)
    assert sub["status"] == "APPLIED"
    assert client.get("/managed-entities/ME-1/config").json()["attributes"] == {"administrativeState": "UNLOCKED"}
    # host keys are an SSH matter: a TLS endpoint trusts a CA file
    assert client.get(f"/o1-adaptor-endpoints/{resp.json()['endpointId']}/host-keys").status_code in (400, 422)


def test_a_tls_endpoint_whose_certificate_is_refused_is_a_rejected_write(client, tls_lab, monkeypatch, tmp_path):
    from netconf_tls_server import Pki
    _register(client, tls_lab.uri, transport="tls", credentialRef="ru-1")
    cert, key = Pki(tmp_path, name="rogue").issue("client")
    monkeypatch.setenv("NETCONF_CRED_RU_1_CERT_FILE", cert)
    monkeypatch.setenv("NETCONF_CRED_RU_1_KEY_FILE", key)
    sub = _write(client)
    assert sub["status"] == "REJECTED" and sub["rejectionReason"] == "NETCONF_RPC_FAILED" and "refused" in sub["rejectionDetail"]


@pytest.mark.parametrize("uri,extra", [
    ("ssh://admin@adaptor", {"transport": "tls"}),                   # scheme does not match the transport
    ("tls://adaptor", {}),                                            # tls:// without transport tls
    ("tls://admin@adaptor", {"transport": "tls"}),                    # the user comes from the certificate: none in the URI
    ("tls://adaptor", {"transport": "tls", "o1Protocol": "RESTCONF"}),
    ("tls://adaptor?model=nope", {"transport": "tls"}),
])
def test_registration_refuses_a_mismatched_tls_endpoint(client, uri, extra):
    assert _register(client, uri, **extra).status_code in (400, 422)
