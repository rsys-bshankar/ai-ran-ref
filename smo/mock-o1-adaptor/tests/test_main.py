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
    client.delete("/state")


def _edit_config_rpc(message_id: str, ref: str, attribute_changes: dict, operation: str = "merge") -> str:
    """Mirrors ran-nf-oam/app/netconf_client.py's own build_edit_config_rpc
    exactly — this module's whole job is to answer exactly what that real
    client sends, not a hand-picked simplification of it.
    """
    config_body = "".join(f"<{name}>{value}</{name}>" for name, value in attribute_changes.items())
    return (
        f'<rpc message-id="{message_id}" xmlns="{NETCONF_BASE_NS}">'
        f"<edit-config><target><running/></target>"
        f'<config><managed-object ref="{ref}" operation="{operation}">{config_body}</managed-object></config>'
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


def test_edit_config_delete_with_empty_payload_is_accepted():
    """SPEC_AUDIT.md item 3: RFC 6241 section 7.2's edit-config `operation`
    attribute — a delete legitimately carries no attribute_changes at
    all, unlike a merge/replace/create, so it must not be rejected for
    emptiness the way test_edit_config_with_empty_changes_is_rejected
    (an implicit merge) correctly is.
    """
    client.post("/edit-config", content=_edit_config_rpc("105", "ME-1", {"adminState": "UNLOCKED"}))
    rpc = _edit_config_rpc("106", "ME-1", {}, operation="delete")
    resp = client.post("/edit-config", content=rpc, headers={"Content-Type": "application/xml"})
    assert resp.status_code == 200
    root = ET.fromstring(resp.text)
    assert any(_local_tag(c) == "ok" for c in root)

    resp = client.get("/edit-config/ME-1")
    assert resp.json() == {"managedObjectRef": "ME-1", "attributeChanges": None}


def test_edit_config_remove_with_empty_payload_is_also_accepted():
    rpc = _edit_config_rpc("107", "ME-1", {}, operation="remove")
    resp = client.post("/edit-config", content=rpc, headers={"Content-Type": "application/xml"})
    root = ET.fromstring(resp.text)
    assert any(_local_tag(c) == "ok" for c in root)


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


def test_capability_declaration_is_configurable(monkeypatch):
    client = TestClient(app)
    assert client.get("/capabilities").json() == {
        "vendorName": "mock-vendor", "supportedVendorModes": ["O1_NETCONF"],
        "supportedServices": ["PROV", "FM", "PM", "FILE", "STREAM", "SWM", "SUBSCRIPTION", "HEARTBEAT"]}
    monkeypatch.setenv("MOCK_O1_VENDOR_NAME", "acme")
    monkeypatch.setenv("MOCK_O1_SUPPORTED_SERVICES", "PROV,FM")
    assert client.get("/capabilities").json()["supportedServices"] == ["PROV", "FM"]
    assert client.get("/capabilities").json()["vendorName"] == "acme"


# ---------------------------------------------------------------- Wave 10.1 (W10-17/W10-19/W10-20)

def _rpc(body: str, message_id: str = "200") -> str:
    return f'<rpc message-id="{message_id}" xmlns="{NETCONF_BASE_NS}">{body}</rpc>'


def _edit(ref, function_ref, changes):
    obj = f'<managed-object ref="{ref}" function-ref="{function_ref}" operation="merge">'
    body = "".join(f"<{k}>{v}</{k}>" for k, v in changes.items())
    return client.post("/edit-config", content=_rpc(f"<edit-config><target><running/></target><config>{obj}{body}"
                                                     "</managed-object></config></edit-config>"),
                       headers={"Content-Type": "application/xml"})


def _get(ref, function_ref):
    resp = client.post("/edit-config", content=_rpc(f'<get-config><source><running/></source><filter>'
                                                    f'<managed-object ref="{ref}" function-ref="{function_ref}"/>'
                                                    "</filter></get-config>"))
    obj = ET.fromstring(resp.text).find(f".//{{{NETCONF_BASE_NS}}}managed-object")
    return {_local_tag(c): c.text for c in obj}


def test_per_function_state_is_read_back_with_get_config():
    assert _get("gnb-1", "NRCellDU=101")["administrativeState"] == "UNLOCKED"  # IOC default
    _edit("gnb-1", "NRCellDU=101", {"administrativeState": "LOCKED"})
    assert _get("gnb-1", "NRCellDU=101")["administrativeState"] == "LOCKED"
    assert _get("gnb-1", "NRCellDU=102")["administrativeState"] == "UNLOCKED"  # a sibling cell is untouched
    _edit("gnb-1", "CESManagementFunction=101", {"energySavingControl": "TO_BE_ENERGY_SAVING"})
    assert _get("gnb-1", "CESManagementFunction=101") == {"energySavingControl": "TO_BE_ENERGY_SAVING",
                                                         "energySavingState": "IS_ENERGY_SAVING"}
    assert client.get("/objects/gnb-1", params={"function_ref": "NRCellDU=101"}).json()["attributes"]["administrativeState"] == "LOCKED"


def test_injected_faults_are_consumed_in_order():
    client.post("/faults", json={"mode": "TIMEOUT", "count": 2, "managedObjectRef": "gnb-1/NRCellDU=101"})
    client.post("/faults", json={"mode": "IGNORE_WRITE", "managedObjectRef": "gnb-1/NRCellDU=102"})
    assert _edit("gnb-1", "NRCellDU=101", {"administrativeState": "LOCKED"}).status_code == 504
    assert _edit("gnb-1", "NRCellDU=101", {"administrativeState": "LOCKED"}).status_code == 504
    assert "<ok/>" in _edit("gnb-1", "NRCellDU=101", {"administrativeState": "LOCKED"}).text
    assert "<ok/>" in _edit("gnb-1", "NRCellDU=102", {"administrativeState": "LOCKED"}).text
    assert _get("gnb-1", "NRCellDU=102")["administrativeState"] == "UNLOCKED"  # acknowledged, never applied
    client.post("/faults", json={"mode": "RPC_ERROR"})
    assert "rpc-error" in _edit("gnb-1", "NRCellDU=103", {"administrativeState": "LOCKED"}).text
    assert client.post("/faults", json={"mode": "MELT"}).status_code == 422


def test_neighbour_relation_and_dmro_defaults_and_writes():
    """Wave 10.2 (W10.2-04)."""
    assert _get("gnb-1", "NRCellRelation=201-202") == {"cellIndividualOffset": "[0, 0, 0, 0, 0, 0]", "isHOAllowed": "true",
                                                       "isMLBAllowed": "true"}
    _edit("gnb-1", "NRCellRelation=201-202", {"cellIndividualOffset": "[2, 2, 2, 2, 2, 2]"})
    assert _get("gnb-1", "NRCellRelation=201-202")["cellIndividualOffset"] == "[2, 2, 2, 2, 2, 2]"
    assert _get("gnb-1", "DMROFunction=gnb-1")["dmroControl"] == "true"


def test_coverage_knob_defaults_and_writes():
    """Wave 10.3 (W10.3-04): digital tilt and sector-carrier power."""
    assert _get("gnb-1", "CommonBeamformingFunction=301")["digitalTilt"] == "60"
    assert _get("gnb-1", "NRSectorCarrier=301")["configuredMaxTxPower"] == "43"
    _edit("gnb-1", "CommonBeamformingFunction=301", {"digitalTilt": "70"})
    _edit("gnb-1", "NRSectorCarrier=302", {"configuredMaxTxPower": "44"})
    assert _get("gnb-1", "CommonBeamformingFunction=301")["digitalTilt"] == "70"
    assert _get("gnb-1", "CommonBeamformingFunction=302")["digitalTilt"] == "60"
    assert _get("gnb-1", "NRSectorCarrier=302")["configuredMaxTxPower"] == "44"


def test_frequency_relation_defaults_and_writes():
    """Wave 10.4 (W10.4-04): idle-mode reselection priority per cell and layer."""
    assert _get("gnb-1", "NRFreqRelation=401-F2100") == {"cellReselectionPriority": "5", "qOffsetFreq": "0"}
    _edit("gnb-1", "NRFreqRelation=401-F2100", {"cellReselectionPriority": "6"})
    assert _get("gnb-1", "NRFreqRelation=401-F2100")["cellReselectionPriority"] == "6"
    assert _get("gnb-1", "NRFreqRelation=402-F2100")["cellReselectionPriority"] == "5"
