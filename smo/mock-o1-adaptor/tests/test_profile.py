"""SB-10: a vendor profile for the O1 stub (`app/profile.py`, `app/profiles/<name>/`): the loader's checks, what `MOCK_O1_PROFILE` makes the stub do, the profile's descriptor being the one its YANG gives, and the O1 conformance kit passing the stub in the profile (the report committed beside it is checked in tests_integration).

Fixtures: `clean` (autouse; clears the profile variables, the stub state and logging), `stub` (a client for the app with `MOCK_O1_PROFILE=example-du`). The tests that
point the loader at a temporary directory clear `profile.load`'s cache around the change. Needs the `conformance/` package and `scripts/ingest_yang_schema.py` of
the module's parent (both loaded by path). Run: `cd smo/mock-o1-adaptor && PYTHONPATH=.:../shared python -m pytest tests/test_profile.py -q`.
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
    """Autouse fixture: no profile variable set, empty stub state, logging silenced for the test and restored after it."""
    logging.disable(logging.CRITICAL)
    for variable in ("MOCK_O1_PROFILE", "MOCK_O1_VENDOR_NAME", "MOCK_O1_SUPPORTED_SERVICES", "MOCK_O1_VENDOR_MODES"):
        monkeypatch.delenv(variable, raising=False)
    _applied_changes.clear(), _object_state.clear(), _faults.clear()
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture
def stub(monkeypatch):
    """Fixture: a test client for the app with `MOCK_O1_PROFILE` set to the shipped `example-du` profile."""
    monkeypatch.setenv("MOCK_O1_PROFILE", NAME)
    return TestClient(app, base_url="http://mock")


def write_profile(root, **changes):
    """Writes a profile named `p1` under `root` (a valid SPEC-mode profile unless `changes` overrides fields) and returns its directory."""
    directory = root / "p1"
    directory.mkdir()
    body = {"profileVersion": 1, "name": "p1", "vendorName": "v", "supportedServices": ["PROV"], "supportedVendorModes": ["O1_NETCONF"], "conformanceMode": "SPEC", **changes}
    (directory / "profile.json").write_text(json.dumps(body))
    return directory


# ---------------------------------------------------------------- the loader

def test_the_shipped_profile_loads_and_is_listed():
    """The `example-du` profile shipped in the image loads, is NETCONF-only, and is listed by `available()`."""
    loaded = profile.load(NAME)
    assert loaded["vendorName"] == "example-vendor" and loaded["supportedVendorModes"] == ["O1_NETCONF"] and NAME in profile.available()


def test_a_name_is_a_directory_name_never_a_path():
    """A profile name that is a path, has upper-case letters, spaces, is empty or is too long is refused before any file is touched, so a name cannot reach outside `profiles/`."""
    for name in ("../example-du", "example-du/..", "/etc", "Example", "a b", "", "x" * 80):
        with pytest.raises(profile.ProfileError, match="not a directory name"):
            profile.load(name)


def test_an_unknown_profile_is_refused_and_the_available_ones_named():
    """An unknown profile is a `ProfileError` that names the profiles that do exist."""
    with pytest.raises(profile.ProfileError, match="no profile 'nobody'.*example-du"):
        profile.load("nobody")


@pytest.mark.parametrize("changes,text", [
    ({"name": "other"}, "name must equal"), ({"vendorName": ""}, "vendorName is required"),
    ({"supportedServices": []}, "supportedServices"), ({"supportedServices": ["NOPE"]}, "supportedServices"), ({"supportedVendorModes": ["O1_SSH"]}, "supportedVendorModes"),
    ({"conformanceMode": "MINE"}, "conformanceMode must"), ({"iocDefaults": {"X": {"a": 1}}}, "iocDefaults"), ({"iocDefaults": []}, "iocDefaults"),
    ({"conformanceMode": "OWN"}, "needs schema"), ({"conformanceMode": "COMBINED", "schema": {"schemaName": "s"}}, "needs schema"),
])
def test_a_profile_that_is_not_well_formed_is_refused_with_the_reason(tmp_path, monkeypatch, changes, text):
    """Each way a `profile.json` can be wrong (table above) is refused with a message naming the problem."""
    monkeypatch.setattr(profile, "PROFILES", tmp_path)
    profile.load.cache_clear()
    write_profile(tmp_path, **changes)
    with pytest.raises(profile.ProfileError, match=text):
        profile.load("p1")
    profile.load.cache_clear()


def test_a_profile_file_that_is_not_json_or_not_an_object_is_refused(tmp_path, monkeypatch):
    """A `profile.json` that is not JSON, or is JSON but not an object, is a `ProfileError`, not a crash."""
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
    """With no `profiles/` directory `available()` is an empty list."""
    monkeypatch.setattr(profile, "PROFILES", tmp_path / "missing")
    assert profile.available() == []


def test_the_onboarding_body_is_what_ran_nf_oam_takes():
    """`onboarding_body` yields the vendor-onboarding body: the capability fields, the conformance mode and, for a COMBINED profile, the YANG schema with its descriptor classes."""
    body = profile.onboarding_body(NAME)
    assert body["vendorName"] == "example-vendor" and body["conformanceMode"] == "COMBINED" and body["supportedVendorModes"] == ["O1_NETCONF"]
    [schema] = body["schemas"]
    assert (schema["schemaName"], schema["revision"], schema["type"]) == ("example-du-ext", "2026-10-01", "YANG")
    assert set(schema["descriptor"]) == {"classes"} and "ExampleBeamProfile" in schema["descriptor"]["classes"]


def test_a_spec_only_profile_onboards_without_a_schema(tmp_path, monkeypatch):
    """A SPEC-mode profile has no schema, so its onboarding body carries none."""
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
    """Every class a profile gives defaults for, and every attribute of them, exists in the vendor's descriptor (or is one of the standard classes the profile adds to), so the defaults cannot name something the model lacks."""
    classes = set(profile.descriptor(NAME)["classes"]) | {"NRCellDU", "NRSectorCarrier"}
    assert set(profile.load(NAME)["iocDefaults"]) <= classes
    for ioc, defaults in profile.load(NAME)["iocDefaults"].items():
        assert set(defaults) <= set(profile.descriptor(NAME)["classes"].get(ioc, defaults)), f"{ioc} has a default for an attribute the vendor's model does not define"


# ---------------------------------------------------------------- the stub in the profile

def test_nothing_changes_without_the_variable():
    """Without `MOCK_O1_PROFILE` the stub is the generic mock: default vendor, both transports, all services, and a RESTCONF root."""
    client = TestClient(app, base_url="http://mock")
    caps = client.get("/capabilities").json()
    assert caps["vendorName"] == "mock-vendor" and caps["supportedVendorModes"] == ["O1_NETCONF", "O1_RESTCONF"] and "SWM" in caps["supportedServices"]
    assert client.get("/restconf/data/managed-element=x").status_code == 200


def test_the_stub_declares_the_vendor_of_the_profile(stub):
    """With a profile active, `/capabilities` declares that vendor's name, services and transports."""
    assert stub.get("/capabilities").json() == {"vendorName": "example-vendor", "supportedServices": ["PROV", "FM", "PM", "FILE", "HEARTBEAT"],
                                                "supportedVendorModes": ["O1_NETCONF"]}


def test_the_environment_still_wins_over_the_profile(stub, monkeypatch):
    """`MOCK_O1_VENDOR_NAME`, `MOCK_O1_SUPPORTED_SERVICES` and `MOCK_O1_VENDOR_MODES` override the profile, and adding the RESTCONF mode brings its root back."""
    monkeypatch.setenv("MOCK_O1_VENDOR_NAME", "renamed")
    monkeypatch.setenv("MOCK_O1_SUPPORTED_SERVICES", "PROV,SWM")
    monkeypatch.setenv("MOCK_O1_VENDOR_MODES", "O1_NETCONF,O1_RESTCONF")
    assert stub.get("/capabilities").json() == {"vendorName": "renamed", "supportedServices": ["PROV", "SWM"], "supportedVendorModes": ["O1_NETCONF", "O1_RESTCONF"]}
    assert stub.get("/.well-known/host-meta").status_code == 200


def test_a_netconf_only_vendor_has_no_restconf_root(stub):
    """A vendor that declares only NETCONF answers every RESTCONF route with a 404 problem, as a real NETCONF-only adaptor would."""
    assert stub.get("/.well-known/host-meta").status_code == 404
    for method, path in (("GET", "/restconf/data/managed-element=x"), ("PATCH", "/restconf/data/managed-element=x"), ("PUT", "/restconf/data/managed-element=x"),
                         ("DELETE", "/restconf/data/managed-element=x"), ("POST", "/restconf/data"), ("POST", "/restconf/data/managed-element=x")):
        answer = stub.request(method, path, json={})
        assert answer.status_code == 404 and answer.json()["title"] == "no RESTCONF", (method, path)


def test_the_vendors_defaults_are_read_back_on_top_of_the_standards(stub):
    """Get-config of a class returns the vendor's attributes together with the standard class defaults, and of a vendor-only class returns the vendor's."""
    xml = ('<rpc message-id="1" xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><get-config><source><running/></source><filter>'
           '<managed-object ref="du-1" function-ref="{}"/></filter></get-config></rpc>')
    read = lambda function: stub.post("/edit-config", content=xml.format(function), headers={"Content-Type": "application/xml"}).text   # noqa: E731
    cell = read("NRCellDU=101")
    assert "exampleTxBackoffDb" in cell and "administrativeState" in cell        # the vendor's attribute beside the standard's default
    assert "<configuredMaxTxPower>40<" in read("NRSectorCarrier=1").replace(" ", "")
    assert "profileName" in read("ExampleBeamProfile=1")


def test_a_profile_that_does_not_exist_stops_the_stub_at_start_and_at_a_request(monkeypatch):
    """A `MOCK_O1_PROFILE` naming no profile raises `ProfileError` from the stub's `_profile()`, which also runs at import, so a typo stops the container instead of falling back silently."""
    monkeypatch.setenv("MOCK_O1_PROFILE", "nobody")
    with pytest.raises(profile.ProfileError):
        from app import main
        main._profile()


# ---------------------------------------------------------------- the conformance kit

def test_the_kit_passes_the_stub_in_the_profile_over_netconf_and_skips_restconf(stub):
    """The O1 conformance kit run over NETCONF fails nothing against the stub in the profile, every RESTCONF check is skipped, and every DISC and NETCONF check passes."""
    results = run(Context(stub, {"netconf"}))
    assert {r.id: r.detail for r in results if r.status == FAIL} == {}
    counts = summary(results)
    assert counts[FAIL] == 0 and {r.status for r in results if r.group == "RESTCONF"} == {SKIP}
    assert counts[PASS] == len([c for c in REGISTRY if c.group in ("DISC", "NETCONF")])


def test_the_kit_picks_netconf_alone_from_what_the_stub_declares(stub, tmp_path, capsys):
    """Run with its default protocol choice, the kit's runner takes the transports from the stub's declaration (NETCONF only), exits 0 and reports no failure."""
    from conformance.o1.__main__ import main
    assert main(["--adaptor", "http://mock", "--out", str(tmp_path / "r")], client=stub) == 0
    text = capsys.readouterr().out
    assert "Protocols run: netconf." in text and "**FAIL**" not in text
