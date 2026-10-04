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
    assert radio["load"] == {"type": "integer", "range": [[0, 100]]}  # a typedef of a typedef: the restriction comes through
    assert radio["ratio"] == {"type": "number", "fractionDigits": 2} and radio["on"] == {"type": "boolean"} and radio["pinned"] == {"type": "boolean"}
    assert radio["label"] == {"type": "string", "pattern": ["ab"]} and radio["neighbours"] == {"type": "array"}
    assert radio["dlArfcn"] == {"type": "integer", "range": [[0, 2**32 - 1]]} and radio["arfcn"] == {"type": "integer", "range": [[-2**31, 2**31 - 1]]}  # through choice / case
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
    assert b.classes["C"] == {"fromG": {"type": "string"}, "fromH": {"type": "integer", "range": [[-128, 127]]}} and not b.unresolved


def test_a_library_supplies_definitions_but_no_classes_and_the_input_wins(tmp_path):
    """SB-3: the 3GPP common modules are a library: their groupings and typedefs resolve, their own IOCs are not classes."""
    (tmp_path / "lib.yang").write_text(
        "module lib { typedef pct { type uint8; } grouping Top_Grp { leaf id { type string; } leaf userLabel { type string; } }"
        " grouping Shared { leaf fromLib { type string; } } container LibOnly { leaf x { type string; } } }")
    (tmp_path / "own.yang").write_text(
        "module own { grouping Shared { leaf fromOwn { type string; } }"
        " list Radio { key id; uses lib:Top_Grp; container attributes { uses Shared; leaf load { type pct; } } } }")
    b = yang.ingest([tmp_path / "own.yang"], [tmp_path / "lib.yang"])
    assert b.classes == {"Radio": {"id": {"type": "string"}, "userLabel": {"type": "string"}, "fromOwn": {"type": "string"},
                                   "load": {"type": "integer", "range": [[0, 255]]}}}   # no LibOnly; the input's `Shared` won
    assert not b.unresolved and b.library_used == {"lib.yang"}


def test_without_the_library_the_same_input_leaves_the_grouping_unresolved(tmp_path):
    (tmp_path / "own.yang").write_text("module own { list Radio { key id; uses top:Top_Grp; leaf n { type string; } } }")
    b = yang.ingest([tmp_path / "own.yang"])
    assert b.unresolved == {"top:Top_Grp"} and b.classes["Radio"] == {"n": {"type": "string"}} and not b.library_used


# ---------------------------------------------------------------- the bundled descriptors

def test_no_bundled_yang_descriptor_has_an_unresolved_grouping_any_more():
    """SB-3.4: with the 3GPP common modules as a library, `id`, `userLabel` and the EP and managed-function groupings resolve."""
    import json
    schemas = sorted((Path(__file__).resolve().parents[1] / "app" / "cm_schemas").glob("*.json"))
    yang_descriptors = [json.loads(p.read_text()) for p in schemas]
    yang_descriptors = [d for d in yang_descriptors if d["type"] == "YANG"]
    assert len(yang_descriptors) == 4
    for descriptor in yang_descriptors:
        assert descriptor["unresolved"] == [], (descriptor["schemaName"], descriptor["unresolved"])
        assert descriptor["library"], descriptor["schemaName"]


def test_the_3gpp_common_attributes_are_in_the_o1_nrm_classes(client):
    classes = _full(client, "o-ran-wg10-o1nrm")
    assert {"id", "userLabel"} <= set(classes["EP_E2"]) and {"id", "userLabel"} <= set(classes["NearRTRICFunction"])
    assert classes["EP_D2C"]["localPortNumber"] == {"type": "integer", "range": [[0, 65535]]}   # an `inet:port-number`, `any` before the library

def _full(client, name):
    listed = {(s["schemaName"]): s for s in client.get("/cm-schemas").json()["items"]}
    assert listed[name]["builtin"] and listed[name]["type"] == "YANG"
    return client.get(f"/cm-schemas/{name}", params={"revision": listed[name]["revision"]}).json()["descriptor"]["classes"]


def test_the_wg10_o1nrm_classes_are_bundled(client):
    classes = _full(client, "o-ran-wg10-o1nrm")
    assert {"ORU", "NearRTRICFunction", "EP_E2", "EP_D2C", "EP_D2U", "NESPolicy", "NESPolicyRelation", "RRMPolicyRBAlloc", "D2Params"} <= set(classes)
    assert classes["ORU"] == {"id": {"type": "string"}, "oRUControllerList": {"type": "array"}, "ruInstanceId": {"type": "string"}}
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


# ---------------------------------------------------------------- constraints (SB-5.1)

CONSTRAINED = '''
module acme-limits {
  namespace "urn:acme:limits"; prefix al;
  typedef pct { type uint8 { range "0..100"; } }
  typedef pct-half { type pct { range "10..50"; } }
  typedef hex { type string { pattern '[A-F]+'; length "1..8"; } }
  typedef hex-pair { type hex { pattern '.{2}'; } }
  typedef latitude { type decimal64 { fraction-digits 4; range "-90.0000..+90.0000"; } }
  container Limits {
    leaf pct { type pct; }
    leaf half { type pct-half; }
    leaf holes { type uint16 { range "0 | 5..10 | 100..max"; } }
    leaf native { type int8; }
    leaf hexPair { type hex-pair; }
    leaf lat { type latitude; }
    leaf money { type decimal64 { fraction-digits 2; } }
    leaf short { type string { length "min..5"; } }
    leaf plain { type string; }
    leaf negated { type string { pattern 'x' { modifier invert-match; } } }
  }
}
'''


def test_ranges_lengths_patterns_and_fraction_digits_are_captured(tmp_path):
    (tmp_path / "limits.yang").write_text(CONSTRAINED)
    limits = yang.ingest([tmp_path / "limits.yang"]).classes["Limits"]
    assert limits["pct"] == {"type": "integer", "range": [[0, 100]]}
    assert limits["half"]["range"] == [[10, 50]]                                   # the most derived range replaces the inherited
    assert limits["holes"]["range"] == [[0, 0], [5, 10], [100, 65535]]             # `|` alternatives, `max` = the type's bound
    assert limits["native"] == {"type": "integer", "range": [[-128, 127]]}         # no range: the native bounds
    assert limits["hexPair"] == {"type": "string", "length": [[1, 8]], "pattern": ["[A-F]+", ".{2}"]}   # patterns accumulate
    assert limits["lat"] == {"type": "number", "range": [[-90.0, 90.0]], "fractionDigits": 4}
    assert limits["money"] == {"type": "number", "fractionDigits": 2}
    assert limits["short"] == {"type": "string", "length": [[0, 5]]}
    assert limits["plain"] == {"type": "string"} and limits["negated"] == {"type": "string"}   # invert-match is not captured


def test_the_openapi_front_end_captures_minimum_maximum_length_and_pattern(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("ingest_cm_schema", Path(__file__).resolve().parents[2] / "scripts" / "ingest_cm_schema.py")
    cm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cm)
    assert cm._type_of({"type": "integer", "minimum": 0, "maximum": 7}, tmp_path) == {"type": "integer", "range": [[0, 7]]}
    assert cm._type_of({"type": "integer", "minimum": 5}, tmp_path) == {"type": "integer", "range": [[5, None]]}
    assert cm._type_of({"type": "string", "maxLength": 9, "pattern": "^a+$"}, tmp_path) == {"type": "string", "length": [[0, 9]], "pattern": ["^a+$"]}
    assert cm._type_of({"type": "integer"}, tmp_path) == {"type": "integer"}


def test_a_value_outside_the_leafs_type_or_range_is_refused_before_anything_is_sent(client, dispatched):
    """SB-5.2: every sub-change is checked against its leaf's YANG type and range before dispatch; one bad change stops the job."""
    rev = next(s["revision"] for s in client.get("/cm-schemas").json()["items"] if s["schemaName"] == "o-ran-wg10-o1nrm")
    _vendor(client, "oran", conformanceMode="COMBINED", schemaRef={"schemaName": "o-ran-wg10-o1nrm", "revision": rev})
    _endpoint(client, vendor="oran")

    assert _write(client, className="EP_D2C", attributeChanges={"localPortNumber": 8080}).status_code == 202       # inet:port-number
    assert _write(client, className="GNBDUFunction", attributeChanges={"gnbIdLength": 28}).status_code == 202      # TS 28.541: 22..32
    sent_so_far = len(dispatched)

    too_big = _write(client, className="EP_D2C", attributeChanges={"localPortNumber": 70000})
    assert too_big.status_code == 422
    assert "localPortNumber=70000 is out of range 0..65535" in too_big.json()["detail"]["detail"]
    below = _write(client, className="GNBDUFunction", attributeChanges={"gnbIdLength": 21})
    assert below.status_code == 422 and "gnbIdLength=21 is out of range 22..32" in below.json()["detail"]["detail"]
    wrong_type = _write(client, className="EP_D2C", attributeChanges={"localPortNumber": "http"})
    assert wrong_type.status_code == 422 and "is not an integer" in wrong_type.json()["detail"]["detail"]

    # one bad change in a job of two: neither is sent
    both = client.post("/config-jobs", json={"requestedBy": "rapp", "scope": "cell", "changes": [
        {"managedElementRef": "ME-A", "className": "EP_D2C", "attributeChanges": {"localPortNumber": 9000}},
        {"managedElementRef": "ME-A", "className": "EP_D2C", "attributeChanges": {"remotePortNumber": 99999}}]})
    assert both.status_code == 422 and "remotePortNumber=99999" in both.json()["detail"]["detail"]
    assert len(dispatched) == sent_so_far


def test_a_range_in_the_3gpp_spec_descriptor_is_checked_too(client, dispatched):
    _vendor(client, "plain3gpp", conformanceMode="SPEC")
    _endpoint(client, vendor="plain3gpp")
    ok = _write(client, className="GNBDUFunction", attributeChanges={"gnbDuId": 1234, "gnbIdLength": 24})
    assert ok.status_code == 202
    bad = _write(client, className="GNBDUFunction", attributeChanges={"gnbDuId": -1})
    assert bad.status_code == 422 and "gnbDuId=-1 is out of range 0..68719476735" in bad.json()["detail"]["detail"]


def _dry(client, me="ME-A", **change):
    return client.post("/config-jobs", json={"requestedBy": "rapp", "scope": "cell", "dryRun": True,
                                             "changes": [{"managedElementRef": me, **change}]})


def test_a_dry_run_runs_every_check_and_sends_nothing(client, dispatched, db_session_factory):
    """MGT-3.1 to 3.3: the same refusals as a real write (data model, YANG range), a per-change verdict, no job, no southbound call."""
    from app.models import WriteConfigJob, WriteConfigSubChange
    rev = next(s["revision"] for s in client.get("/cm-schemas").json()["items"] if s["schemaName"] == "o-ran-wg10-o1nrm")
    _vendor(client, "oran", conformanceMode="COMBINED", schemaRef={"schemaName": "o-ran-wg10-o1nrm", "revision": rev})
    _endpoint(client, vendor="oran")

    ok = _dry(client, className="EP_D2C", attributeChanges={"localPortNumber": 8080})
    assert ok.status_code == 200
    assert ok.json() == {"dryRun": True, "status": "VALIDATED", "waves": [["ME-A"]], "changes": [
        {"managedElementRef": "ME-A", "managedFunctionRef": None, "operation": "merge", "verdict": "PASS", "reason": None}]}

    too_big = _dry(client, className="EP_D2C", attributeChanges={"localPortNumber": 70000})          # MGT-3.3: the YANG check applies
    assert too_big.status_code == 422 and "is out of range 0..65535" in too_big.json()["detail"]["detail"]
    assert _dry(client, className="ORU", attributeChanges={"noSuchAttr": 1}).status_code == 422
    nope = _dry(client, me="ME-NOPE", attributeChanges={"a": 1}).json()                              # not registered: a real write is rejected
    assert nope["status"] == "WOULD_REJECT_SOME" and nope["changes"][0] == {
        "managedElementRef": "ME-NOPE", "managedFunctionRef": None, "operation": "merge", "verdict": "WOULD_REJECT", "reason": "ENDPOINT_UNREACHABLE"}

    assert dispatched == []                                                                         # nothing went southbound
    db = db_session_factory()
    assert db.query(WriteConfigJob).count() == 0 and db.query(WriteConfigSubChange).count() == 0
    db.close()
    real = _write(client, className="EP_D2C", attributeChanges={"localPortNumber": 8080})           # and a real write still dispatches
    assert real.status_code == 202 and len(dispatched) == 1


def test_a_dry_run_is_denied_where_the_real_write_would_be(client, dispatched):
    resp = client.post("/config-jobs", json={"requestedBy": "rapp", "scope": "entire-RAN", "dryRun": True,
                                             "changes": [{"managedElementRef": "ME-A", "attributeChanges": {"a": 1}}]})
    assert resp.status_code == 403 and "MSAC_ACCESS_DENIED" in str(resp.json())
    assert dispatched == []
