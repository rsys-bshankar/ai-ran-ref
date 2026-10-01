"""SA-O1-4: the YANG front end (`scripts/ingest_yang_schema.py`) and the bundled
O-RAN WG10 O1 NRM and WG5 O-DU / O-CU data-model descriptors. Run with:
pytest smo/ran-nf-oam/tests -q
"""

import importlib.util
from pathlib import Path

import pytest

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)
from test_vendors import _endpoint, _vendor, _write, dispatched  # noqa: F401  (helpers and fixtures)

_script = Path(__file__).resolve().parents[2] / "scripts" / "ingest_yang_schema.py"
_spec = importlib.util.spec_from_file_location("ingest_yang_schema", _script)
yang = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(yang)

SAMPLE = '''
module acme-radio {
  namespace "urn:acme:radio"; prefix ar;
  import _3gpp-common-top { prefix top3gpp; }   // not in the input: left unresolved
  revision 2024-01-05 { description "older"; }
  revision 2025-03-09 { description "newer"; }

  typedef power-class { type enumeration { enum LOW; enum HIGH { value 5; } } }
  typedef percent { type uint8 { range "0..100"; } }
  typedef alias-of-percent { type percent; }

  grouping RadioGrp {
    leaf txPower { type power-class; }
    leaf load { type alias-of-percent; }
    leaf ratio { type decimal64 { fraction-digits 2; } }
    leaf on { type boolean; }
    leaf pinned { type empty; }
    leaf label { type string { pattern "a" + "b"; } }
    leaf-list neighbours { type string; }
    choice mode {
      case fdd { leaf dlArfcn { type uint32; } }
      case tdd { leaf arfcn { type int32; } }
    }
    list sectors { key id; leaf id { type string; } }
    container limits { leaf max { type uint16; } }
    leaf mystery { type other:thing; }
    leaf unionised { type union { type uint8; type string; } }
  }

  /* a top-level list in the 3GPP shape: attributes in `container attributes` */
  list Radio {
    key id;
    uses top3gpp:Top_Grp;
    container attributes { uses RadioGrp; leaf extra { type string; } }
  }
  container Plain { leaf plainLeaf { type string; } }
  augment "/me:ManagedElement" { list Aug { leaf augLeaf { type boolean; } } }
}
'''


def _bundle(tmp_path, text=SAMPLE):
    f = tmp_path / "m.yang"
    f.write_text(text)
    return yang.ingest([f])


def test_the_parser_reads_comments_quotes_concatenation_and_nesting():
    tree = yang.parse('module m { /* c */ namespace "urn:" + "x"; // trailing\n leaf a { type string; } }')
    assert tree[0][0] == "module" and tree[0][1] == "m"
    keywords = {c[0]: c for c in tree[0][2]}
    assert keywords["namespace"][1] == "urn:x" and keywords["leaf"][2][0][:2] == ("type", "string")


def test_classes_attributes_types_and_enums(tmp_path):
    b = _bundle(tmp_path)
    assert set(b.classes) == {"Radio", "Plain", "Aug"}  # nested list / container are attributes, not classes
    radio = b.classes["Radio"]
    assert radio["txPower"] == {"type": "string", "enum": ["LOW", "HIGH"]}  # a typedef'd enumeration
    assert radio["load"] == {"type": "integer"}  # a typedef of a typedef
    assert radio["ratio"] == {"type": "number"} and radio["on"] == {"type": "boolean"} and radio["pinned"] == {"type": "boolean"}
    assert radio["label"] == {"type": "string"} and radio["neighbours"] == {"type": "array"}
    assert radio["dlArfcn"] == {"type": "integer"} and radio["arfcn"] == {"type": "integer"}  # through choice / case
    assert radio["sectors"] == {"type": "array"} and radio["limits"] == {"type": "object"}
    assert radio["mystery"] == {"type": "any"} and radio["unionised"] == {"type": "string"}
    assert radio["extra"] == {"type": "string"} and "attributes" not in radio  # `container attributes` is flattened
    assert b.classes["Plain"] == {"plainLeaf": {"type": "string"}} and b.classes["Aug"] == {"augLeaf": {"type": "boolean"}}


def test_unresolved_groupings_are_reported_and_the_revision_is_the_newest(tmp_path):
    b = _bundle(tmp_path)
    assert b.unresolved == {"top3gpp:Top_Grp"} and b.revision() == "2025-03-09"


def test_a_grouping_in_another_file_resolves_and_a_cycle_terminates(tmp_path):
    (tmp_path / "a.yang").write_text("module a { grouping G { leaf fromG { type string; } uses H; } grouping H { uses G; leaf fromH { type int8; } } }")
    (tmp_path / "b.yang").write_text("module b { container C { uses G; } }")
    b = yang.ingest([tmp_path / "a.yang", tmp_path / "b.yang"])
    assert b.classes["C"] == {"fromG": {"type": "string"}, "fromH": {"type": "integer"}} and not b.unresolved


# ---------------------------------------------------------------- the bundled descriptors

def _full(client, name):
    listed = {(s["schemaName"]): s for s in client.get("/cm-schemas").json()["items"]}
    assert listed[name]["builtin"] and listed[name]["type"] == "YANG"
    return client.get(f"/cm-schemas/{name}", params={"revision": listed[name]["revision"]}).json()["descriptor"]["classes"]


def test_the_wg10_o1nrm_classes_are_bundled(client):
    classes = _full(client, "o-ran-wg10-o1nrm")
    assert {"ORU", "NearRTRICFunction", "EP_E2", "EP_D2C", "EP_D2U", "NESPolicy", "NESPolicyRelation", "RRMPolicyRBAlloc", "D2Params"} <= set(classes)
    assert classes["ORU"] == {"oRUControllerList": {"type": "array"}, "ruInstanceId": {"type": "string"}}
    assert classes["NESPolicy"]["policyType"] == {"type": "string", "enum": ["TRX_CONTROL", "ASM"]}
    assert classes["RRMPolicyRBAlloc"]["direction"]["enum"] == ["BIDIRECTION", "UL", "DL"]


def test_the_wg5_odu_and_ocu_classes_are_bundled(client):
    du, cu = _full(client, "o-ran-wg5-du-mp"), _full(client, "o-ran-wg5-cu-mp")
    assert "CTIFunction" in du and len(du) >= 30 and {"PDCPConfig", "SecurityHandling"} <= set(cu)


def test_the_combined_descriptor_is_the_union(client):
    combined = _full(client, "o-ran-wg10-wg5")
    parts = {**_full(client, "o-ran-wg10-o1nrm"), **_full(client, "o-ran-wg5-du-mp"), **_full(client, "o-ran-wg5-cu-mp")}
    assert combined == parts


def test_a_vendor_conforms_to_the_wg10_model_and_writes_are_checked(client, dispatched):
    rev = next(s["revision"] for s in client.get("/cm-schemas").json()["items"] if s["schemaName"] == "o-ran-wg10-o1nrm")
    _vendor(client, "oran", conformanceMode="COMBINED", schemaRef={"schemaName": "o-ran-wg10-o1nrm", "revision": rev})
    _endpoint(client, vendor="oran")
    assert _write(client, className="ORU", attributeChanges={"ruInstanceId": "ru-1"}).status_code == 202
    assert _write(client, className="NESPolicy", attributeChanges={"policyType": "ASM"}).status_code == 202
    assert _write(client, managedFunctionRef="NRCellDU=1", attributeChanges={"administrativeState": "LOCKED"}).status_code == 202  # still the 3GPP model
    bad_enum = _write(client, className="NESPolicy", attributeChanges={"policyType": "SNOOZE"})
    assert bad_enum.status_code == 422 and "is not one of" in bad_enum.json()["detail"]["detail"]
    assert _write(client, className="ORU", attributeChanges={"noSuchAttr": 1}).status_code == 422
    assert len(dispatched) == 3


def test_a_dn_function_ref_picks_the_class_from_its_last_rdn(client, dispatched):
    rev = next(s["revision"] for s in client.get("/cm-schemas").json()["items"] if s["schemaName"] == "o-ran-wg10-o1nrm")
    _vendor(client, "oran", conformanceMode="COMBINED", schemaRef={"schemaName": "o-ran-wg10-o1nrm", "revision": rev})
    _endpoint(client, vendor="oran")
    ok = _write(client, managedFunctionRef="SubNetwork=A,ManagedElement=ME-A,ORU=1", attributeChanges={"ruInstanceId": "ru-1"})
    assert ok.status_code == 202
    assert _write(client, managedFunctionRef="SubNetwork=A,ManagedElement=ME-A,ORU=1", attributeChanges={"policyType": "ASM"}).status_code == 422


def test_a_vendor_conforms_to_the_combined_wg10_wg5_model(client, dispatched):
    rev = next(s["revision"] for s in client.get("/cm-schemas").json()["items"] if s["schemaName"] == "o-ran-wg10-wg5")
    _vendor(client, "oran", conformanceMode="OWN", schemaRef={"schemaName": "o-ran-wg10-wg5", "revision": rev})
    _endpoint(client, vendor="oran")
    assert _write(client, className="ORU", attributeChanges={"ruInstanceId": "ru-1"}).status_code == 202
    assert _write(client, className="PDCPConfig", attributeChanges={}).status_code == 202  # a WG5 O-CU class
    assert _write(client, className="NRCellDU", attributeChanges={"administrativeState": "LOCKED"}).status_code == 422  # OWN: no 3GPP model
