"""Tests for the mock O1 Adaptor test double (RAN NF OAM LLD section 5.1's
CM write path — netconf_client.py). Run with:
pytest smo/mock-o1-adaptor/tests -q
"""

import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app.main import NETCONF_BASE_NS, _applied_changes, app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_mock_state():
    """_applied_changes is a plain module-level dict (Phase 1 test double,
    not a real persistent service) — same reset shape mock-near-rt-ric's
    own fixture already uses for the identical reason.
    """
    _applied_changes.clear()


def _edit_config_rpc(message_id: str, ref: str, attribute_changes: dict) -> str:
    """Mirrors ran-nf-oam/app/netconf_client.py's own build_edit_config_rpc
    exactly — this module's whole job is to answer exactly what that real
    client sends, not a hand-picked simplification of it.
    """
    config_body = "".join(f"<{name}>{value}</{name}>" for name, value in attribute_changes.items())
    return (
        f'<rpc message-id="{message_id}" xmlns="{NETCONF_BASE_NS}">'
        f"<edit-config><target><running/></target>"
        f'<config><managed-object ref="{ref}">{config_body}</managed-object></config>'
        f"</edit-config></rpc>"
    )


def _local_tag(elem: ET.Element) -> str:
    return elem.tag.rsplit("}", 1)[-1]


def test_edit_config_with_changes_replies_ok():
    rpc = _edit_config_rpc("101", "ME-1", {"adminState": "UNLOCKED"})
    resp = client.post("/edit-config", content=rpc, headers={"Content-Type": "application/xml"})
    assert resp.status_code == 200
    root = ET.fromstring(resp.text)
    assert _local_tag(root) == "rpc-reply"
    assert root.attrib["message-id"] == "101"
    assert any(_local_tag(c) == "ok" for c in root)


def test_edit_config_records_the_applied_attribute_changes():
    rpc = _edit_config_rpc("102", "ME-1", {"adminState": "UNLOCKED", "txPower": "10"})
    client.post("/edit-config", content=rpc, headers={"Content-Type": "application/xml"})

    resp = client.get("/edit-config/ME-1")
    assert resp.json() == {"managedObjectRef": "ME-1", "attributeChanges": {"adminState": "UNLOCKED", "txPower": "10"}}


def test_edit_config_with_empty_changes_is_rejected():
    """Same "empty payload is a real, testable rejection trigger" pattern
    mock-near-rt-ric's own create_policy already established.
    """
    rpc = _edit_config_rpc("103", "ME-1", {})
    resp = client.post("/edit-config", content=rpc, headers={"Content-Type": "application/xml"})
    assert resp.status_code == 200  # the mock always accepts the HTTP call; rpc-error is a domain outcome, not an HTTP error
    root = ET.fromstring(resp.text)
    assert _local_tag(root) == "rpc-reply"
    assert not any(_local_tag(c) == "ok" for c in root)
    assert any(_local_tag(c) == "rpc-error" for c in root)


def test_edit_config_with_malformed_xml_is_rejected():
    resp = client.post("/edit-config", content=b"not xml at all", headers={"Content-Type": "application/xml"})
    assert resp.status_code == 200
    root = ET.fromstring(resp.text)
    assert any(_local_tag(c) == "rpc-error" for c in root)


def test_query_last_applied_for_unknown_ref_returns_none():
    resp = client.get("/edit-config/does-not-exist")
    assert resp.status_code == 200
    assert resp.json() == {"managedObjectRef": "does-not-exist", "attributeChanges": None}


def test_edit_config_rejects_entity_expansion_instead_of_parsing_it():
    """CodeQL finding (CWE-611): this endpoint parses an attacker-reachable
    HTTP body, so a billion-laughs-style internal entity must be rejected
    the same way malformed XML already is, not expanded.
    """
    malicious = (
        '<?xml version="1.0"?>'
        "<!DOCTYPE rpc [<!ENTITY boom \"" + ("x" * 1000) + "\">]>"
        f'<rpc message-id="104" xmlns="{NETCONF_BASE_NS}">'
        "<edit-config><target><running/></target>"
        '<config><managed-object ref="ME-1"><adminState>&boom;</adminState></managed-object></config>'
        "</edit-config></rpc>"
    )
    resp = client.post("/edit-config", content=malicious, headers={"Content-Type": "application/xml"})
    assert resp.status_code == 200
    root = ET.fromstring(resp.text)
    assert any(_local_tag(c) == "rpc-error" for c in root)
    assert _applied_changes == {}
