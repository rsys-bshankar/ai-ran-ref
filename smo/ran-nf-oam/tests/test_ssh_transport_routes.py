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
from app.models import (Alarm, CMSchemaCache, CMSnapshot, ManagedEntity, O1AdaptorEndpoint, VendorCapability, WriteConfigJob, WriteConfigSubChange,
                        MsacAccessRule, MsacIdentity, MsacRole)

from netconf_ssh_server import Behaviour, NetconfTestServer


@pytest.fixture
def db_session_factory():
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[
        O1AdaptorEndpoint.__table__, ManagedEntity.__table__, Alarm.__table__, CMSchemaCache.__table__, WriteConfigJob.__table__,
        WriteConfigSubChange.__table__, CMSnapshot.__table__, VendorCapability.__table__, MsacIdentity.__table__, MsacRole.__table__, MsacAccessRule.__table__,
        IdempotencyKey.__table__, NotificationOutbox.__table__])
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
