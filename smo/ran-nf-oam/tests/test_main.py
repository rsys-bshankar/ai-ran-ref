"""Route-level tests for RAN NF OAM SMOS's WriteConfigurationChanges
(RAN NF OAM LLD section 5.1) — the actual NETCONF dispatch, previously
elided behind a comment that recorded every sub_change as APPLIED without
dispatching anything. Run with: pytest smo/ran-nf-oam/tests -q
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smo_shared.db import Base, get_session
from smo_shared.testing import make_test_engine

from app.main import app
from app.models import Alarm, CMSchemaCache, ManagedEntity, O1AdaptorEndpoint, PMSubscription, SoftwareManagementJob, WriteConfigJob, WriteConfigSubChange


@pytest.fixture
def db_session_factory():
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[
        O1AdaptorEndpoint.__table__, ManagedEntity.__table__, Alarm.__table__, CMSchemaCache.__table__,
        WriteConfigJob.__table__, WriteConfigSubChange.__table__, PMSubscription.__table__, SoftwareManagementJob.__table__,
    ])
    return sessionmaker(bind=engine)


@pytest.fixture
def client(db_session_factory):
    def override_get_session():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _make_me(db_session_factory, protocol="NETCONF", health="ACTIVE"):
    db = db_session_factory()
    endpoint = O1AdaptorEndpoint(managed_element_ref="ME-1", adaptor_uri="http://adaptor:9000/netconf",
                                  protocol_support=[protocol], health_status=health)
    db.add(endpoint)
    db.flush()
    me = ManagedEntity(managed_element_ref="ME-1", entity_type="O-DU", o1_protocol=protocol, o1_adaptor_endpoint_id=endpoint.endpoint_id)
    db.add(me)
    db.commit()
    db.close()


def test_config_change_dispatches_netconf_and_applies(client, db_session_factory, monkeypatch):
    _make_me(db_session_factory, protocol="NETCONF")
    monkeypatch.setattr("app.main.send_edit_config", lambda adaptor_uri, target_ref, attribute_changes, message_id: True)

    resp = client.post("/config-jobs", json={
        "requestedBy": "operator", "scope": "cell",
        "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"adminState": "UNLOCKED"}}],
    })
    assert resp.status_code == 202
    assert resp.json()["status"] == "COMPLETED"

    job = client.get(f"/config-jobs/{resp.json()['jobId']}").json()
    assert job["subChanges"][0]["status"] == "APPLIED"


def test_config_change_rejects_when_netconf_rpc_fails(client, db_session_factory, monkeypatch):
    _make_me(db_session_factory, protocol="NETCONF")
    monkeypatch.setattr("app.main.send_edit_config", lambda adaptor_uri, target_ref, attribute_changes, message_id: False)

    resp = client.post("/config-jobs", json={
        "requestedBy": "operator", "scope": "cell",
        "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"adminState": "UNLOCKED"}}],
    })
    assert resp.json()["status"] == "FAILED"

    job = client.get(f"/config-jobs/{resp.json()['jobId']}").json()
    assert job["subChanges"][0]["status"] == "REJECTED"
    assert job["subChanges"][0]["rejectionReason"] == "NETCONF_RPC_FAILED"


def test_config_change_rejects_restconf_me_as_protocol_not_supported(client, db_session_factory, monkeypatch):
    """RESTCONF has no dispatch implementation yet — the confirmed
    protocol (OPEN_ITEMS.md) is NETCONF only, so a RESTCONF-provisioned ME
    is rejected honestly rather than silently treated as applied.
    """
    _make_me(db_session_factory, protocol="RESTCONF")
    monkeypatch.setattr("app.main.send_edit_config", lambda *a, **kw: pytest.fail("should not dispatch to a RESTCONF ME"))

    resp = client.post("/config-jobs", json={
        "requestedBy": "operator", "scope": "cell",
        "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"adminState": "UNLOCKED"}}],
    })
    job = client.get(f"/config-jobs/{resp.json()['jobId']}").json()
    assert job["subChanges"][0]["status"] == "REJECTED"
    assert job["subChanges"][0]["rejectionReason"] == "PROTOCOL_NOT_SUPPORTED"


def test_config_change_rejects_unreachable_endpoint_without_dispatch(client, db_session_factory, monkeypatch):
    _make_me(db_session_factory, protocol="NETCONF", health="UNREACHABLE")
    monkeypatch.setattr("app.main.send_edit_config", lambda *a, **kw: pytest.fail("should not dispatch to an unreachable endpoint"))

    resp = client.post("/config-jobs", json={
        "requestedBy": "operator", "scope": "cell",
        "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"adminState": "UNLOCKED"}}],
    })
    job = client.get(f"/config-jobs/{resp.json()['jobId']}").json()
    assert job["subChanges"][0]["rejectionReason"] == "ENDPOINT_UNREACHABLE"
