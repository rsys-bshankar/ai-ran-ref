"""SB-10: a vendor profile for the O1 stub (`app/profile.py`, `app/profiles/<name>/`): the loader's checks, what `MOCK_O1_PROFILE` makes the stub do, the profile's
descriptor being the one its YANG gives, and the O1 conformance kit passing the stub in the profile (the report committed beside it is checked in tests_integration).
Run with: pytest smo/mock-o1-adaptor/tests/test_profile.py -q
"""

import importlib.util
import json
import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import profile  # noqa: E402
from app.main import app, _applied_changes, _faults, _object_state  # noqa: E402
from conformance.o1 import checks  # noqa: E402,F401
from conformance.o1.kit import FAIL, PASS, REGISTRY, SKIP, Context, run, summary  # noqa: E402

_spec = importlib.util.spec_from_file_location("ingest_yang_schema", ROOT / "scripts" / "ingest_yang_schema.py")
yang = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(yang)

NAME = "example-du"
DIRECTORY = profile.PROFILES / NAME


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    logging.disable(logging.CRITICAL)
    for variable in ("MOCK_O1_PROFILE", "MOCK_O1_VENDOR_NAME", "MOCK_O1_SUPPORTED_SERVICES", "MOCK_O1_VENDOR_MODES"):
        monkeypatch.delenv(variable, raising=False)
    _applied_changes.clear(), _object_state.clear(), _faults.clear()
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setenv("MOCK_O1_PROFILE", NAME)
    return TestClient(app, base_url="http://mock")


def write_profile(root, **changes):
    directory = root / "p1"
    directory.mkdir()
    body = {"profileVersion": 1, "name": "p1", "vendorName": "v", "supportedServices": ["PROV"], "supportedVendorModes": ["O1_NETCONF"], "conformanceMode": "SPEC", **changes}
    (directory / "profile.json").write_text(json.dumps(body))
    return directory


# ---------------------------------------------------------------- the loader

def test_the_shipped_profile_loads_and_is_listed():
    loaded = profile.load(NAME)
    assert loaded["vendorName"] == "example-vendor" and loaded["supportedVendorModes"] == ["O1_NETCONF"] and NAME in profile.available()


def test_a_name_is_a_directory_name_never_a_path():
    for name in ("../example-du", "example-du/..", "/etc", "Example", "a b", "", "x" * 80):
        with pytest.raises(profile.ProfileError, match="not a directory name"):
            profile.load(name)


def test_an_unknown_profile_is_refused_and_the_available_ones_named():
    with pytest.raises(profile.ProfileError, match="no profile 'nobody'.*example-du"):
        profile.load("nobody")


@pytest.mark.parametrize("changes,text", [
    ({"name": "other"}, "name must equal"), ({"vendorName": ""}, "vendorName is required"),
    ({"supportedServices": []}, "supportedServices"), ({"supportedServices": ["NOPE"]}, "supportedServices"), ({"supportedVendorModes": ["O1_SSH"]}, "supportedVendorModes"),
    ({"conformanceMode": "MINE"}, "conformanceMode must"), ({"iocDefaults": {"X": {"a": 1}}}, "iocDefaults"), ({"iocDefaults": []}, "iocDefaults"),
    ({"conformanceMode": "OWN"}, "needs schema"), ({"conformanceMode": "COMBINED", "schema": {"schemaName": "s"}}, "needs schema"),
])
def test_a_profile_that_is_not_well_formed_is_refused_with_the_reason(tmp_path, monkeypatch, changes, text):
    monkeypatch.setattr(profile, "PROFILES", tmp_path)
    profile.load.cache_clear()
    write_profile(tmp_path, **changes)
    with pytest.raises(profile.ProfileError, match=text):
        profile.load("p1")
    profile.load.cache_clear()


def test_a_profile_file_that_is_not_json_or_not_an_object_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "PROFILES", tmp_path)
    profile.load.cache_clear()
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "profile.json").write_text("{nope")
    with pytest.raises(profile.ProfileError, match="cannot be read as JSON"):
        profile.load("bad")
    (tmp_path / "list").mkdir()
    (tmp_path / "list" / "profile.json").write_text("[]")
    with pytest.raises(profile.ProfileError, match="not an object"):
        profile.load("list")
    profile.load.cache_clear()


def test_available_is_empty_without_the_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "PROFILES", tmp_path / "missing")
    assert profile.available() == []


def test_the_onboarding_body_is_what_ran_nf_oam_takes():
    body = profile.onboarding_body(NAME)
    assert body["vendorName"] == "example-vendor" and body["conformanceMode"] == "COMBINED" and body["supportedVendorModes"] == ["O1_NETCONF"]
    [schema] = body["schemas"]
    assert (schema["schemaName"], schema["revision"], schema["type"]) == ("example-du-ext", "2026-10-01", "YANG")
    assert set(schema["descriptor"]) == {"classes"} and "ExampleBeamProfile" in schema["descriptor"]["classes"]


def test_a_spec_only_profile_onboards_without_a_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "PROFILES", tmp_path)
    profile.load.cache_clear()
    write_profile(tmp_path)
    assert "schemas" not in profile.onboarding_body("p1")
    profile.load.cache_clear()


def test_the_descriptor_is_what_the_yang_gives():
    """The descriptor in the profile is generated (scripts/ingest_yang_schema.py); this fails when the YANG was edited and the descriptor was not regenerated."""
    bundle = yang.ingest(yang.yang_files([DIRECTORY / "yang"]))
    stored = profile.descriptor(NAME)
    assert stored["classes"] == {k: dict(sorted(v.items())) for k, v in sorted(bundle.classes.items())}
    assert (stored["schemaName"], stored["revision"]) == (profile.load(NAME)["schema"]["schemaName"], bundle.revision()) and bundle.unresolved == set()
    for relative in profile.load(NAME)["schema"]["yang"]:
        assert (DIRECTORY / relative).is_file()


def test_every_class_with_defaults_is_a_class_of_the_vendors_model_or_the_standard():
    classes = set(profile.descriptor(NAME)["classes"]) | {"NRCellDU", "NRSectorCarrier"}
    assert set(profile.load(NAME)["iocDefaults"]) <= classes
    for ioc, defaults in profile.load(NAME)["iocDefaults"].items():
        assert set(defaults) <= set(profile.descriptor(NAME)["classes"].get(ioc, defaults)), f"{ioc} has a default for an attribute the vendor's model does not define"


# ---------------------------------------------------------------- the stub in the profile

def test_nothing_changes_without_the_variable():
    client = TestClient(app, base_url="http://mock")
    caps = client.get("/capabilities").json()
    assert caps["vendorName"] == "mock-vendor" and caps["supportedVendorModes"] == ["O1_NETCONF", "O1_RESTCONF"] and "SWM" in caps["supportedServices"]
    assert client.get("/restconf/data/managed-element=x").status_code == 200


def test_the_stub_declares_the_vendor_of_the_profile(stub):
    assert stub.get("/capabilities").json() == {"vendorName": "example-vendor", "supportedServices": ["PROV", "FM", "PM", "FILE", "HEARTBEAT"],
                                                "supportedVendorModes": ["O1_NETCONF"]}


def test_the_environment_still_wins_over_the_profile(stub, monkeypatch):
    monkeypatch.setenv("MOCK_O1_VENDOR_NAME", "renamed")
    monkeypatch.setenv("MOCK_O1_SUPPORTED_SERVICES", "PROV,SWM")
    monkeypatch.setenv("MOCK_O1_VENDOR_MODES", "O1_NETCONF,O1_RESTCONF")
    assert stub.get("/capabilities").json() == {"vendorName": "renamed", "supportedServices": ["PROV", "SWM"], "supportedVendorModes": ["O1_NETCONF", "O1_RESTCONF"]}
    assert stub.get("/.well-known/host-meta").status_code == 200


def test_a_netconf_only_vendor_has_no_restconf_root(stub):
    assert stub.get("/.well-known/host-meta").status_code == 404
    for method, path in (("GET", "/restconf/data/managed-element=x"), ("PATCH", "/restconf/data/managed-element=x"), ("PUT", "/restconf/data/managed-element=x"),
                         ("DELETE", "/restconf/data/managed-element=x"), ("POST", "/restconf/data"), ("POST", "/restconf/data/managed-element=x")):
        answer = stub.request(method, path, json={})
        assert answer.status_code == 404 and answer.json()["title"] == "no RESTCONF", (method, path)


def test_the_vendors_defaults_are_read_back_on_top_of_the_standards(stub):
    xml = ('<rpc message-id="1" xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><get-config><source><running/></source><filter>'
           '<managed-object ref="du-1" function-ref="{}"/></filter></get-config></rpc>')
    read = lambda function: stub.post("/edit-config", content=xml.format(function), headers={"Content-Type": "application/xml"}).text   # noqa: E731
    cell = read("NRCellDU=101")
    assert "exampleTxBackoffDb" in cell and "administrativeState" in cell        # the vendor's attribute beside the standard's default
    assert "<configuredMaxTxPower>40<" in read("NRSectorCarrier=1").replace(" ", "")
    assert "profileName" in read("ExampleBeamProfile=1")


def test_a_profile_that_does_not_exist_stops_the_stub_at_start_and_at_a_request(monkeypatch):
    monkeypatch.setenv("MOCK_O1_PROFILE", "nobody")
    with pytest.raises(profile.ProfileError):
        from app import main
        main._profile()


# ---------------------------------------------------------------- the conformance kit

def test_the_kit_passes_the_stub_in_the_profile_over_netconf_and_skips_restconf(stub):
    results = run(Context(stub, {"netconf"}))
    assert {r.id: r.detail for r in results if r.status == FAIL} == {}
    counts = summary(results)
    assert counts[FAIL] == 0 and {r.status for r in results if r.group == "RESTCONF"} == {SKIP}
    assert counts[PASS] == len([c for c in REGISTRY if c.group in ("DISC", "NETCONF")])


def test_the_kit_picks_netconf_alone_from_what_the_stub_declares(stub, tmp_path, capsys):
    from conformance.o1.__main__ import main
    assert main(["--adaptor", "http://mock", "--out", str(tmp_path / "r")], client=stub) == 0
    text = capsys.readouterr().out
    assert "Protocols run: netconf." in text and "**FAIL**" not in text
