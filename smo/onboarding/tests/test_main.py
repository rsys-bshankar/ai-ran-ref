"""Tests for the OnboardPackage route (Onboarding/rApp Mgmt LLD sections
1-2), covering NFO's CreateDescriptor wiring — the actual fix for the
NFDeploymentDescriptor gap HISTORY.md flagged as the top item.
Run with: pytest smo/onboarding/tests -q
"""

import uuid
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Table, Uuid, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smo_shared.db import Base, get_session
from smo_shared.testing import concurrent_commit_on

from app.main import app
from app.models import ApplicationPackage, Artifact, PackageUsageRegistration


class FakeR1Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # nf_deployment_descriptor lives in the nfo module — stand in a minimal
    # table so ApplicationPackage's FK resolves, same pattern as nfo/tests'
    # application_package stub.
    if "nf_deployment_descriptor" not in Base.metadata.tables:
        Table("nf_deployment_descriptor", Base.metadata, Column("nf_deployment_descriptor_id", Uuid, primary_key=True))
    Base.metadata.create_all(engine, tables=[ApplicationPackage.__table__, Artifact.__table__, PackageUsageRegistration.__table__])
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


def test_onboard_success_creates_nf_deployment_descriptor_via_nfo(client, monkeypatch):
    """The actual fix: successful validation now calls NFO's
    CreateDescriptor and stores the real nfDeploymentDescriptorId on the
    package, instead of leaving rApp Management to pass packageId where
    NFO expects a genuine descriptor.
    """
    monkeypatch.setattr("app.main._validate_package", lambda location: ("Definitions/main.yaml", [], "deadbeef", {}))
    descriptor_id = uuid.uuid4()
    monkeypatch.setattr(
        "app.main.R1Client.post",
        lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(descriptor_id)}),
    )

    resp = client.post("/packages", json={"location": "http://example/pkg.csar"})
    package_id = resp.json()["packageId"]

    status = client.get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "AVAILABLE"
    assert status.json()["nfDeploymentDescriptorId"] == str(descriptor_id)


def test_onboard_routes_to_failed_when_nfo_descriptor_creation_fails(client, monkeypatch):
    """DescriptorCreationFailed folds into the same VALIDATE_FAILED path as
    a malformed zip or an unreachable location — NFO being unavailable at
    onboarding time is a real, expected failure mode, not a crash.
    """
    monkeypatch.setattr("app.main._validate_package", lambda location: ("Definitions/main.yaml", [], "deadbeef", {}))
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(503, {}))

    resp = client.post("/packages", json={"location": "http://example/pkg.csar"})
    package_id = resp.json()["packageId"]

    status = client.get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "FAILED"
    assert status.json()["nfDeploymentDescriptorId"] is None


def test_onboard_routes_to_failed_on_a_real_malformed_zip(client, monkeypatch):
    """_validate_package itself was never exercised before this pass —
    every prior test mocked it away entirely. This drives the real
    validation code against genuinely malformed zip bytes, only mocking
    the network fetch underneath it.
    """
    class FakeHttpResponse:
        content = b"not a real zip file"
        def raise_for_status(self):
            pass

    monkeypatch.setattr("app.main.httpx.get", lambda location, timeout=None: FakeHttpResponse())

    resp = client.post("/packages", json={"location": "http://example/pkg.csar"})
    package_id = resp.json()["packageId"]

    status = client.get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "FAILED"


def _real_package_bytes(include_acm_composition=True, definitions="tosca_definitions_version: tosca_simple_yaml_1_3\n",
                         manifest_yaml=None, capabilities_yaml=None, sme_provider_json=None, sme_service_api_json=None) -> bytes:
    """A minimal but genuinely well-formed CSAR — TOSCA-Metadata/TOSCA.meta
    pointing at a real Definitions/ entry, optionally with the reference's
    required composition file alongside it, at its real path
    (`RappCsarPathProvider.ACM_COMPOSITION_JSON_LOCATION`,
    `FileExistenceValidator.java`): Files/Acm/definition/compositions.json
    — not Definitions/acm_composition.json, which this fixture and
    _validate_package both got wrong before being checked against the
    reference's real sample package.

    `manifest_yaml`/`capabilities_yaml` (raw YAML text, root-level files)
    are the Wave 1 rApp packaging extension — both optional, and omitted
    by default, so every existing call site of this fixture keeps
    covering the pre-extension, no-AI-capabilities case unchanged.
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("TOSCA-Metadata/TOSCA.meta", "Entry-Definitions: Definitions/main.yaml\n")
        z.writestr("Definitions/main.yaml", definitions)
        if include_acm_composition:
            z.writestr("Files/Acm/definition/compositions.json", "{}")
        if manifest_yaml is not None:
            z.writestr("manifest.yaml", manifest_yaml)
        if capabilities_yaml is not None:
            z.writestr("capabilities.yaml", capabilities_yaml)
        if sme_provider_json is not None:
            z.writestr("Files/Sme/providers/provider-function-1.json", sme_provider_json)
        if sme_service_api_json is not None:
            z.writestr("Files/Sme/serviceapis/api-set-1.json", sme_service_api_json)
    return buf.getvalue()


def _mock_fetch(monkeypatch, content: bytes) -> None:
    class FakeHttpResponse:
        def raise_for_status(self):
            pass
    FakeHttpResponse.content = content
    monkeypatch.setattr("app.main.httpx.get", lambda location, timeout=None: FakeHttpResponse())


def test_onboard_routes_to_failed_when_location_does_not_end_with_csar(client, monkeypatch):
    """HISTORY.md §5: the reference's own NamingValidator — a
    package filename that doesn't follow the `.csar` convention was
    previously accepted without complaint. Checked before ever fetching
    the location, so no network mock is even needed here.
    """
    resp = client.post("/packages", json={"location": "http://example/pkg.zip"})
    package_id = resp.json()["packageId"]

    status = client.get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "FAILED"


def test_onboard_succeeds_without_the_onap_acm_composition_file(client, monkeypatch):
    """The reference's own FileExistenceValidator requires
    Files/Acm/definition/compositions.json (an ONAP ACM composition
    file) alongside TOSCA-Metadata/TOSCA.meta. Deliberately NOT adopted
    here (formal-spec audit, Onboarding/rApp Mgmt vs. the real ASD/TOSCA
    CSAR format — see HISTORY.md §7): this build never calls ONAP ACM at
    all, so requiring every CSAR to bundle an ONAP-specific file just to
    pass validation isn't real spec fidelity, it's an unwanted
    dependency. A package that omits the file onboards the same as one
    that includes it.
    """
    _mock_fetch(monkeypatch, _real_package_bytes(include_acm_composition=False))
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))

    resp = client.post("/packages", json={"location": "http://example/pkg.csar"})
    package_id = resp.json()["packageId"]

    status = client.get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "AVAILABLE"


def test_onboard_succeeds_with_a_real_well_formed_package(client, monkeypatch):
    """The positive case for the two checks above — a genuinely
    well-formed package (real zip bytes, not the usual fully-mocked
    _validate_package) still reaches AVAILABLE.
    """
    _mock_fetch(monkeypatch, _real_package_bytes())
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))

    resp = client.post("/packages", json={"location": "http://example/pkg.csar"})
    package_id = resp.json()["packageId"]

    status = client.get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "AVAILABLE"


def test_onboard_resolves_name_version_vendor_from_the_asd(client, monkeypatch):
    """A validated package takes its identity from the ASD's own
    application_name / application_version / provider properties instead
    of keeping the `unresolved-until-validated 0.0.0` placeholder.
    """
    asd = (
        "tosca_definitions_version: tosca_simple_yaml_1_3\n"
        "topology_template:\n  node_templates:\n    applicationServiceDescriptor:\n      properties:\n"
        '        provider: "ai-ran-ref"\n        application_name: energy-saving-rapp\n        application_version: "1.1"  # bumped\n'
    )
    _mock_fetch(monkeypatch, _real_package_bytes(definitions=asd))
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))

    package_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]

    pkg = next(p for p in client.get("/packages").json()["items"] if p["packageId"] == package_id)
    assert (pkg["state"], pkg["name"], pkg["version"], pkg["vendor"]) == ("AVAILABLE", "energy-saving-rapp", "1.1", "ai-ran-ref")


def test_onboard_captures_the_real_asd_descriptor_identity_fields(client, monkeypatch):
    """The real ASD schema (asd_types.yaml's tosca.nodes.asd node type,
    grounded against nonrtric-plt-rappmanager's own sample CSARs) also
    requires descriptor_id/descriptor_invariant_id/descriptor_version/
    schema_version alongside application_name/application_version/
    provider — none captured before this pass. Package identity/
    uniqueness stays on integrity_hash (unchanged); these are surfaced
    for real spec fidelity only.
    """
    asd = (
        "tosca_definitions_version: tosca_simple_yaml_1_2\n"
        "topology_template:\n  node_templates:\n    applicationServiceDescriptor:\n      properties:\n"
        "        descriptor_id: 2cd6a567-2e33-4960-8ef7-1cc519c998c4\n"
        "        descriptor_invariant_id: 3f8a5e1b-68f1-42e5-89d0-47090dd0ef5a\n"
        '        descriptor_version: "1.0"\n        schema_version: "2.0"\n'
        '        provider: "ai-ran-ref"\n        application_name: energy-saving-rapp\n        application_version: "1.0"\n'
    )
    _mock_fetch(monkeypatch, _real_package_bytes(definitions=asd))
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))

    package_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]

    pkg = next(p for p in client.get("/packages").json()["items"] if p["packageId"] == package_id)
    assert pkg["descriptorId"] == "2cd6a567-2e33-4960-8ef7-1cc519c998c4"
    assert pkg["descriptorInvariantId"] == "3f8a5e1b-68f1-42e5-89d0-47090dd0ef5a"
    assert pkg["descriptorVersion"] == "1.0"
    assert pkg["schemaVersion"] == "2.0"


def test_onboard_keeps_placeholder_identity_when_the_asd_has_none(client, monkeypatch):
    _mock_fetch(monkeypatch, _real_package_bytes())
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))

    package_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]

    pkg = next(p for p in client.get("/packages").json()["items"] if p["packageId"] == package_id)
    assert (pkg["state"], pkg["name"], pkg["version"], pkg["vendor"]) == ("AVAILABLE", "unresolved-until-validated", "0.0.0", None)


def test_onboard_leaves_ai_capabilities_null_when_neither_file_is_present(client, monkeypatch):
    """Every package this build produced before the Wave 1 rApp packaging
    extension (and any package that simply doesn't declare AI Platform
    capabilities) onboards exactly as before — aiCapabilities stays null,
    not an empty dict or a validation failure.
    """
    _mock_fetch(monkeypatch, _real_package_bytes())
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))

    package_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]

    pkg = next(p for p in client.get("/packages").json()["items"] if p["packageId"] == package_id)
    assert pkg["state"] == "AVAILABLE"
    assert pkg["aiCapabilities"] is None


def test_onboard_parses_manifest_and_capabilities_yaml_when_present(client, monkeypatch):
    """The positive case: a package declaring both root-level files (like
    samples/energy-saving-rapp/) has its AI Platform
    capability declaration parsed and stored.
    """
    manifest_yaml = "rappManifest:\n  manifestVersion: \"1.0\"\n  aiRuntimeSdkVersion: \"1.0\"\n"
    capabilities_yaml = (
        "capabilities:\n"
        "  provides:\n"
        "    - namespace: data\n"
        "      description: produces energy-saving-metrics\n"
        "  consumes:\n"
        "    - namespace: platform\n"
        "      description: registers as an SME provider\n"
    )
    _mock_fetch(monkeypatch, _real_package_bytes(manifest_yaml=manifest_yaml, capabilities_yaml=capabilities_yaml))
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))

    package_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]

    pkg = next(p for p in client.get("/packages").json()["items"] if p["packageId"] == package_id)
    assert pkg["state"] == "AVAILABLE"
    assert pkg["aiCapabilities"] == {
        "manifestVersion": "1.0", "aiRuntimeSdkVersion": "1.0",
        "provides": [{"namespace": "data", "description": "produces energy-saving-metrics"}],
        "consumes": [{"namespace": "platform", "description": "registers as an SME provider"}],
    }


def test_onboard_parses_capabilities_yaml_alone_without_a_manifest(client, monkeypatch):
    """The two files are independently optional — capabilities.yaml
    without manifest.yaml still produces a partial aiCapabilities dict,
    not a validation failure.
    """
    capabilities_yaml = "capabilities:\n  provides:\n    - namespace: models\n      description: registers a model\n"
    _mock_fetch(monkeypatch, _real_package_bytes(capabilities_yaml=capabilities_yaml))
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))

    package_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]

    pkg = next(p for p in client.get("/packages").json()["items"] if p["packageId"] == package_id)
    assert pkg["aiCapabilities"] == {
        "consumes": [], "provides": [{"namespace": "models", "description": "registers a model"}],
    }


def test_onboard_routes_to_failed_on_malformed_capabilities_yaml(client, monkeypatch):
    """A malformed manifest.yaml/capabilities.yaml is a package validation
    failure like any other malformed package file (ONBOARD_VALIDATION_
    FAILURES), not an unhandled 500 — the same discipline as the
    malformed-zip and missing-composition-file cases above.
    """
    _mock_fetch(monkeypatch, _real_package_bytes(capabilities_yaml="capabilities: [unterminated"))

    resp = client.post("/packages", json={"location": "http://example/pkg.csar"})
    package_id = resp.json()["packageId"]

    status = client.get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "FAILED"


def test_onboard_leaves_sme_declarations_null_when_neither_directory_is_present(client, monkeypatch):
    """Every package this build produced before this pass (and any package
    that simply doesn't bundle Files/Sme/) onboards exactly as before —
    smeDeclarations stays null, not an empty dict or a validation failure.
    """
    _mock_fetch(monkeypatch, _real_package_bytes())
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))

    package_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]

    pkg = next(p for p in client.get("/packages").json()["items"] if p["packageId"] == package_id)
    assert pkg["state"] == "AVAILABLE"
    assert pkg["smeDeclarations"] is None


def test_onboard_parses_sme_provider_and_service_api_declarations_when_present(client, monkeypatch):
    """Real O-RAN SC rApp Manager CSAR layout (nonrtric-plt-rappmanager's
    own sample-rapp-generator packages): Files/Sme/providers/*.json + Files/
    Sme/serviceapis/*.json, read raw and stored for rapp-mgmt's own
    bootstrap-complete to register per-instance (HISTORY.md §7's
    Onboarding/rApp Mgmt finding 3).
    """
    provider_json = '{"apiProvDomInfo": "Provider domain", "apiProvFuncs": [{"apiProvFuncRole": "APF"}]}'
    service_api_json = '{"apiName": "Energy Saving API Set 1", "aefProfiles": [{"aefId": "aef-1"}]}'
    _mock_fetch(monkeypatch, _real_package_bytes(sme_provider_json=provider_json, sme_service_api_json=service_api_json))
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))

    package_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]

    pkg = next(p for p in client.get("/packages").json()["items"] if p["packageId"] == package_id)
    assert pkg["state"] == "AVAILABLE"
    assert pkg["smeDeclarations"] == {
        "providers": [{"apiProvDomInfo": "Provider domain", "apiProvFuncs": [{"apiProvFuncRole": "APF"}]}],
        "serviceApis": [{"apiName": "Energy Saving API Set 1", "aefProfiles": [{"aefId": "aef-1"}]}],
    }


def test_onboard_routes_to_failed_on_malformed_sme_provider_json(client, monkeypatch):
    """A malformed Files/Sme/providers/*.json is a package validation
    failure like any other malformed package file, not an unhandled 500 —
    the same discipline as malformed manifest.yaml/capabilities.yaml.
    """
    _mock_fetch(monkeypatch, _real_package_bytes(sme_provider_json="{not valid json"))

    resp = client.post("/packages", json={"location": "http://example/pkg.csar"})
    package_id = resp.json()["packageId"]

    status = client.get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "FAILED"


def test_onboard_routes_to_failed_for_a_byte_identical_duplicate_package(client, monkeypatch):
    """HISTORY.md §5: the reference's own AsdDescriptorValidator
    rejects re-onboarding a package whose ASD descriptor already exists;
    adapted here to this build's own identity (a content hash, since
    real ASD descriptor data doesn't exist in this build) — a
    byte-identical package already onboarded is rejected the same way
    on a second attempt, not silently onboarded twice.
    """
    package_bytes = _real_package_bytes()
    _mock_fetch(monkeypatch, package_bytes)
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))

    first = client.post("/packages", json={"location": "http://example/pkg.csar"})
    first_status = client.get(f"/packages/{first.json()['packageId']}/onboarding-status")
    assert first_status.json()["state"] == "AVAILABLE"

    second = client.post("/packages", json={"location": "http://example/pkg-again.csar"})
    second_status = client.get(f"/packages/{second.json()['packageId']}/onboarding-status")
    assert second_status.json()["state"] == "FAILED"


def test_onboarding_status_for_unknown_package_is_404(client):
    """OI-2-lcm-error-mapping: was a 409 DME_TYPE_VERSION_CONFLICT reuse."""
    resp = client.get(f"/packages/{uuid.uuid4()}/onboarding-status")
    assert resp.status_code == 404
    assert resp.json()["detail"]["title"] == "PACKAGE_NOT_FOUND"


def _make_available_package(client, monkeypatch, integrity_hash="deadbeef") -> str:
    # integrity_hash is a real, checked field now (HISTORY.md §5's
    # duplicate-package detection) — a caller onboarding more than one
    # package in the same test must vary it, or the second one routes to
    # FAILED as a genuine duplicate.
    monkeypatch.setattr("app.main._validate_package", lambda location: ("Definitions/main.yaml", [], integrity_hash, {}))
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))
    return client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]


def test_query_packages_lists_and_filters_by_state(client, monkeypatch):
    available_id = _make_available_package(client, monkeypatch)
    monkeypatch.setattr("app.main._validate_package", lambda location: (_ for _ in ()).throw(KeyError("Definitions/missing.yaml")))
    client.post("/packages", json={"location": "http://example/other.csar"})  # routes to FAILED

    all_packages = client.get("/packages").json()["items"]
    assert len(all_packages) == 2

    available_only = client.get("/packages", params={"state": "AVAILABLE"}).json()["items"]
    assert [p["packageId"] for p in available_only] == [available_id]


def test_deprecate_then_cancel_delete_round_trip(client, monkeypatch):
    """DEPRECATE (AVAILABLE -> DEPRECATED) and CANCEL_DELETE (DEPRECATED ->
    AVAILABLE) had no test coverage at all before this pass.
    """
    package_id = _make_available_package(client, monkeypatch)

    deprecated = client.post(f"/packages/{package_id}/deprecate")
    assert deprecated.status_code == 200
    assert deprecated.json()["state"] == "DEPRECATED"

    restored = client.post(f"/packages/{package_id}/cancel-delete")
    assert restored.status_code == 200
    assert restored.json()["state"] == "AVAILABLE"


def test_delete_failed_package_skips_cascade_check(client, monkeypatch):
    """The module's own docstring calls this out as a deliberate shortcut:
    a FAILED package never reached AVAILABLE, so nothing could depend on
    it — DELETE must not even run the cascade query. Never tested before
    this pass despite being explicitly documented behavior.
    """
    monkeypatch.setattr("app.main._validate_package", lambda location: (_ for _ in ()).throw(KeyError("boom")))
    package_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]

    resp = client.delete(f"/packages/{package_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"


def test_delete_available_package_with_no_dependents_succeeds(client, monkeypatch):
    package_id = _make_available_package(client, monkeypatch)
    resp = client.delete(f"/packages/{package_id}")
    assert resp.status_code == 200
    assert resp.json()["state"] == "DELETING"


def test_delete_blocked_by_dependent_child_package(client, db_session_factory, monkeypatch):
    """Onboarding/rApp Mgmt LLD section 4's cascade-delete guard, one half:
    an AVAILABLE/DEPRECATED child package blocks its parent's deletion.
    """
    parent_id = uuid.UUID(_make_available_package(client, monkeypatch, integrity_hash="parent-hash"))
    child_id = uuid.UUID(_make_available_package(client, monkeypatch, integrity_hash="child-hash"))

    with db_session_factory() as session:
        child = session.get(ApplicationPackage, child_id)
        child.parent_package_id = parent_id
        session.commit()

    resp = client.delete(f"/packages/{parent_id}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "SERVICE_NAME_CONFLICT"


def test_delete_blocked_by_active_usage_registration(client, monkeypatch):
    """The cascade-delete guard's other half: a usage registration with no
    stopped_at blocks deletion — the actual condition
    TerminateInstance's usage/stop call (rapp-mgmt) exists to clear.
    """
    package_id = _make_available_package(client, monkeypatch)
    client.post(f"/packages/{package_id}/usage/start", params={"consumer_id": "instance-1"})

    resp = client.delete(f"/packages/{package_id}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "SERVICE_NAME_CONFLICT"


def test_delete_succeeds_once_usage_registration_is_stopped(client, monkeypatch):
    package_id = _make_available_package(client, monkeypatch)
    reg = client.post(f"/packages/{package_id}/usage/start", params={"consumer_id": "instance-1"}).json()

    stop_resp = client.post(f"/packages/{package_id}/usage/{reg['registrationId']}/stop")
    assert stop_resp.status_code == 200

    resp = client.delete(f"/packages/{package_id}")
    assert resp.status_code == 200
    assert resp.json()["state"] == "DELETING"


def test_prime_moves_available_package_to_primed(client, monkeypatch):
    """HISTORY.md §5: the reference's real
    COMMISSIONED->PRIMING->PRIMED lifecycle was missing entirely —
    this build went ONBOARDING->AVAILABLE directly.
    """
    package_id = _make_available_package(client, monkeypatch)

    resp = client.post(f"/packages/{package_id}/prime")
    assert resp.status_code == 200
    assert resp.json()["state"] == "PRIMED"


def test_deprime_moves_primed_package_back_to_available(client, monkeypatch):
    package_id = _make_available_package(client, monkeypatch)
    client.post(f"/packages/{package_id}/prime")

    resp = client.post(f"/packages/{package_id}/deprime")
    assert resp.status_code == 200
    assert resp.json()["state"] == "AVAILABLE"


def test_deprime_blocked_by_active_usage_registration(client, monkeypatch):
    """The reference's own deprimeRapp guard: 'Unable to deprime as there
    are active rapp instances.'
    """
    package_id = _make_available_package(client, monkeypatch)
    client.post(f"/packages/{package_id}/prime")
    client.post(f"/packages/{package_id}/usage/start", params={"consumer_id": "instance-1"})

    resp = client.post(f"/packages/{package_id}/deprime")
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "SERVICE_NAME_CONFLICT"

    with_package = client.get("/packages", params={"state": "PRIMED"}).json()["items"]
    assert [p["packageId"] for p in with_package] == [package_id]


def test_deprime_succeeds_once_usage_registration_is_stopped(client, monkeypatch):
    package_id = _make_available_package(client, monkeypatch)
    client.post(f"/packages/{package_id}/prime")
    reg = client.post(f"/packages/{package_id}/usage/start", params={"consumer_id": "instance-1"}).json()
    client.post(f"/packages/{package_id}/usage/{reg['registrationId']}/stop")

    resp = client.post(f"/packages/{package_id}/deprime")
    assert resp.status_code == 200
    assert resp.json()["state"] == "AVAILABLE"


def test_health_check_answers_the_gui_bff_liveness_probe(client):
    """GUI pass: the BFF's /modules/status probes /<module>/health on every module."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


def test_query_packages_exposes_identity_fields_for_the_gui(client, db_session_factory):
    """GUI pass: the package list only carried id/state, so an operator
    couldn't tell packages apart by name/version."""
    with db_session_factory() as session:
        session.add(ApplicationPackage(package_id=uuid.uuid4(), application_type="rApp", name="energy-saving", version="1.0.0",
                                        vendor="acme", state="AVAILABLE", manifest_ref="m"))
        session.commit()
    [pkg] = client.get("/packages").json()["items"]
    assert (pkg["name"], pkg["version"], pkg["vendor"], pkg["applicationType"]) == ("energy-saving", "1.0.0", "acme", "rApp")
    assert pkg["nfDeploymentDescriptorId"] is None


def test_package_row_is_committed_before_nfo_create_descriptor_is_called(tmp_path, monkeypatch):
    """NFO is a separate process with its own DB connection, and its
    nf_deployment_descriptor.package_id carries a real FK to
    application_package (migrations/001_init.sql). The package row used to
    be only flushed — uncommitted, invisible to any other connection — when
    CreateDescriptor ran, so on real Postgres NFO's insert hit a
    ForeignKeyViolation and every onboarding ended FAILED. A file-backed
    SQLite with one connection per session reproduces that visibility
    (the StaticPool fixture above shares one connection, so it can't).
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'onboarding.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[ApplicationPackage.__table__, Artifact.__table__, PackageUsageRegistration.__table__])
    factory = sessionmaker(bind=engine)

    def override_get_session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    seen_by_nfo = []

    def fake_nfo_create_descriptor(self, path, json=None, **kw):
        with factory() as other_connection:
            seen_by_nfo.append(other_connection.get(ApplicationPackage, uuid.UUID(json["packageId"])) is not None)
        return FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())})

    _mock_fetch(monkeypatch, _real_package_bytes())
    monkeypatch.setattr("app.main.R1Client.post", fake_nfo_create_descriptor)
    app.dependency_overrides[get_session] = override_get_session
    try:
        package_id = TestClient(app).post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]
        state = TestClient(app).get(f"/packages/{package_id}/onboarding-status").json()["state"]
    finally:
        app.dependency_overrides.clear()
    assert seen_by_nfo == [True]
    assert state == "AVAILABLE"


def test_list_usage_registrations_shows_what_blocks_delete(client, db_session_factory):
    """GUI pass 2: the cascade-delete guard's usage registrations (call flow 06)."""
    package_id = uuid.uuid4()
    with db_session_factory() as session:
        session.add(ApplicationPackage(package_id=package_id, application_type="rApp", name="p", version="1",
                                        state="AVAILABLE", manifest_ref="m"))
        session.add(Artifact(package_id=package_id, path="Files/Helm/app.tgz", access_url="http://x/app.tgz"))
        session.commit()
    reg = client.post(f"/packages/{package_id}/usage/start", params={"consumer_id": "instance-1"}).json()

    [usage] = client.get(f"/packages/{package_id}/usage").json()["items"]
    assert (usage["registrationId"], usage["consumerId"], usage["active"]) == (reg["registrationId"], "instance-1", True)
    client.post(f"/packages/{package_id}/usage/{reg['registrationId']}/stop")
    [usage] = client.get(f"/packages/{package_id}/usage").json()["items"]
    assert usage["active"] is False and usage["stoppedAt"]

    assert [a["path"] for a in client.get(f"/packages/{package_id}/artifacts").json()["items"]] == ["Files/Helm/app.tgz"]


# ---------------------------------------------------------------- Wave 7: runtime profiles (W7-03)

def _onboard_with_manifest(client, monkeypatch, manifest_yaml):
    _mock_fetch(monkeypatch, _real_package_bytes(manifest_yaml=manifest_yaml))
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))
    package_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]
    return next(p for p in client.get("/packages").json()["items"] if p["packageId"] == package_id)


def test_onboard_parses_execution_modes_and_runtime_profiles(client, monkeypatch):
    """Top-level keys (the SMO_Wave_10 package layout) are accepted next to rappManifest."""
    manifest_yaml = (
        "rappManifest:\n  manifestVersion: \"1.0\"\n"
        "executionModes: [TRAINING, INFERENCE]\n"
        "autonomyModes: [SHADOW, ASSIST, AUTONOMOUS]\n"
        "runtimeProfiles:\n"
        "  TRAINING: {cpu: 8, memory: 16Gi, gpu: 0}\n"
        "  INFERENCE: {cpu: 2, memory: 4Gi, gpu: 0}\n"
    )
    pkg = _onboard_with_manifest(client, monkeypatch, manifest_yaml)
    assert pkg["state"] == "AVAILABLE"
    caps = pkg["aiCapabilities"]
    assert caps["executionModes"] == ["TRAINING", "INFERENCE"]
    assert caps["autonomyModes"] == ["SHADOW", "ASSIST", "AUTONOMOUS"]
    assert caps["runtimeProfiles"] == {"TRAINING": {"cpu": 8, "gpu": 0, "memory": "16Gi"},
                                       "INFERENCE": {"cpu": 2, "gpu": 0, "memory": "4Gi"}}



@pytest.mark.parametrize("profiles", [
    "runtimeProfiles:\n  TRAINING: {cpu: -1}\n",                                   # negative cpu
    "runtimeProfiles:\n  COMPILING: {cpu: 1}\n",                                   # unknown mode
    "executionModes: [INFERENCE]\nruntimeProfiles:\n  TRAINING: {cpu: 1}\n",       # undeclared mode
    "runtimeProfiles: [1, 2]\n",                                                   # not a mapping
])
def test_onboard_fails_on_an_invalid_runtime_profile(client, monkeypatch, profiles):
    pkg = _onboard_with_manifest(client, monkeypatch, profiles)
    assert pkg["state"] == "FAILED"


# ---------------------------------------------------------------- AI-10.1: limits in the manifest

def test_onboard_reads_the_limits_of_the_manifest(client, monkeypatch):
    pkg = _onboard_with_manifest(client, monkeypatch, "rappManifest:\n  manifestVersion: \"1.0\"\n  limits:\n    configJobsPerHour: 12\n")
    assert pkg["state"] == "AVAILABLE"
    assert pkg["aiCapabilities"]["limits"] == {"configJobsPerHour": 12}


def test_limits_are_accepted_at_the_top_level_and_are_optional(client, monkeypatch):
    assert _onboard_with_manifest(client, monkeypatch, "limits: {configJobsPerHour: 3}\n")["aiCapabilities"]["limits"] == {"configJobsPerHour": 3}


def test_onboard_reads_blast_radius_and_magnitude_limits(client, monkeypatch):
    pkg = _onboard_with_manifest(client, monkeypatch, "limits: {configJobsPerHour: 4, maxElementsPerJob: 3, maxChangePercent: 12.5}\n")
    assert pkg["state"] == "AVAILABLE"
    assert pkg["aiCapabilities"]["limits"] == {"configJobsPerHour": 4, "maxElementsPerJob": 3, "maxChangePercent": 12.5}


@pytest.mark.parametrize("limits", [
    "limits: {maxElementsPerJob: 2.5}\n",             # elements are whole
    "limits: {maxElementsPerJob: 10001}\n",
    "limits: {maxChangePercent: 0}\n",
    "limits: {maxChangePercent: -1}\n",
    "limits: {maxChangePercent: 10001}\n",
    "limits: {maxChangePercent: true}\n",
    "limits: {maxChangePercent: '20'}\n",
    "limits: {maxChangePercent: .nan}\n",
])
def test_onboard_fails_on_an_invalid_blast_radius_or_magnitude(client, monkeypatch, limits):
    assert _onboard_with_manifest(client, monkeypatch, limits)["state"] == "FAILED"


@pytest.mark.parametrize("limits", [
    "limits: {configJobsPerHour: 0}\n",             # not positive
    "limits: {configJobsPerHour: 1.5}\n",           # not whole
    "limits: {configJobsPerHour: true}\n",          # a bool is not a number
    "limits: {configJobsPerHour: 100001}\n",        # above the largest accepted
    "limits: {configJobsPerHour: '5'}\n",           # a string
    "limits: {blastRadius: 5}\n",                   # a limit the platform does not know
    "limits: [1, 2]\n",                             # not a mapping
])
def test_onboard_fails_on_an_invalid_limit(client, monkeypatch, limits):
    assert _onboard_with_manifest(client, monkeypatch, limits)["state"] == "FAILED"


# ---------------------------------------------------------------- OI-2-package-redeploy

@pytest.mark.parametrize("earlier_state", ["DELETING", "FAILED"])
def test_a_csar_can_be_onboarded_again_once_its_package_is_deleted_or_failed(client, db_session_factory, monkeypatch, earlier_state):
    """The duplicate-hash check ignores DELETING (terminal) and FAILED
    packages, so the same CSAR is not locked out forever."""
    package_bytes = _real_package_bytes()
    _mock_fetch(monkeypatch, package_bytes)
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))
    first_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]
    with db_session_factory() as session:
        session.get(ApplicationPackage, uuid.UUID(first_id)).state = earlier_state
        session.commit()

    second_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]

    assert client.get(f"/packages/{second_id}/onboarding-status").json()["state"] == "AVAILABLE"


def test_a_deleted_package_can_be_onboarded_again_end_to_end(client, monkeypatch):
    _mock_fetch(monkeypatch, _real_package_bytes())
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))
    first_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]
    assert client.delete(f"/packages/{first_id}").json()["state"] == "DELETING"

    second_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]
    assert client.get(f"/packages/{second_id}/onboarding-status").json()["state"] == "AVAILABLE"


@pytest.mark.parametrize("earlier_state", ["AVAILABLE", "PRIMED", "DEPRECATED"])
def test_a_live_package_still_blocks_a_duplicate(client, db_session_factory, monkeypatch, earlier_state):
    _mock_fetch(monkeypatch, _real_package_bytes())
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))
    first_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]
    with db_session_factory() as session:
        session.get(ApplicationPackage, uuid.UUID(first_id)).state = earlier_state
        session.commit()

    second_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]
    assert client.get(f"/packages/{second_id}/onboarding-status").json()["state"] == "FAILED"


# ---------------------------------------------------------------- OI-2-lcm-error-mapping

def _set_state(db_session_factory, package_id, state):
    with db_session_factory() as session:
        session.get(ApplicationPackage, uuid.UUID(package_id)).state = state
        session.commit()


@pytest.mark.parametrize("route,state,event", [
    ("deprecate", "PRIMED", "DEPRECATE"),
    ("deprecate", "DEPRECATED", "DEPRECATE"),
    ("prime", "DEPRECATED", "PRIME"),
    ("prime", "PRIMED", "PRIME"),
    ("deprime", "AVAILABLE", "DEPRIME"),
    ("cancel-delete", "AVAILABLE", "CANCEL_DELETE"),
])
def test_illegal_lifecycle_events_are_409_naming_state_and_event(client, db_session_factory, monkeypatch, route, state, event):
    package_id = _make_available_package(client, monkeypatch)
    _set_state(db_session_factory, package_id, state)

    resp = client.post(f"/packages/{package_id}/{route}")

    assert resp.status_code == 409
    problem = resp.json()["detail"]
    assert problem["title"] == "LIFECYCLE_ILLEGAL_TRANSITION"
    assert f"event {event}" in problem["detail"] and f"state {state}" in problem["detail"]


@pytest.mark.parametrize("state", ["PRIMED", "DELETING", "ONBOARDING"])
def test_delete_from_a_state_with_no_delete_edge_is_an_illegal_transition_not_a_dependent(client, db_session_factory, monkeypatch, state):
    """DELETE on a PRIMED package used to report "blocked by a dependent";
    it is simply not allowed from PRIMED."""
    package_id = _make_available_package(client, monkeypatch)
    _set_state(db_session_factory, package_id, state)

    resp = client.delete(f"/packages/{package_id}")

    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "LIFECYCLE_ILLEGAL_TRANSITION"
    assert f"state {state}" in resp.json()["detail"]["detail"]


@pytest.mark.parametrize("method,path", [
    ("post", "/packages/{id}/deprecate"), ("post", "/packages/{id}/prime"), ("post", "/packages/{id}/deprime"),
    ("post", "/packages/{id}/cancel-delete"), ("delete", "/packages/{id}"),
    ("get", "/packages/{id}/artifacts"), ("get", "/packages/{id}/usage"),
])
def test_unknown_package_is_404_on_every_lifecycle_route(client, method, path):
    resp = getattr(client, method)(path.format(id=uuid.uuid4()))
    assert resp.status_code == 404
    assert resp.json()["detail"]["title"] == "PACKAGE_NOT_FOUND"


def test_usage_start_on_an_unknown_package_is_404(client):
    resp = client.post(f"/packages/{uuid.uuid4()}/usage/start", params={"consumer_id": "i-1"})
    assert resp.status_code == 404


def test_usage_stop_on_an_unknown_or_foreign_registration_is_404(client, monkeypatch):
    package_id = _make_available_package(client, monkeypatch, integrity_hash="a")
    other_id = _make_available_package(client, monkeypatch, integrity_hash="b")
    reg = client.post(f"/packages/{other_id}/usage/start", params={"consumer_id": "i-1"}).json()["registrationId"]

    unknown = client.post(f"/packages/{package_id}/usage/{uuid.uuid4()}/stop")
    foreign = client.post(f"/packages/{package_id}/usage/{reg}/stop")

    assert unknown.status_code == 404 and foreign.status_code == 404
    assert unknown.json()["detail"]["title"] == "PACKAGE_USAGE_REGISTRATION_NOT_FOUND"
    assert client.get(f"/packages/{other_id}/usage").json()["items"][0]["active"] is True  # untouched


def test_usage_stop_is_idempotent(client, monkeypatch):
    package_id = _make_available_package(client, monkeypatch)
    reg = client.post(f"/packages/{package_id}/usage/start", params={"consumer_id": "i-1"}).json()["registrationId"]
    client.post(f"/packages/{package_id}/usage/{reg}/stop")
    first = client.get(f"/packages/{package_id}/usage").json()["items"][0]["stoppedAt"]

    assert client.post(f"/packages/{package_id}/usage/{reg}/stop").status_code == 200
    assert client.get(f"/packages/{package_id}/usage").json()["items"][0]["stoppedAt"] == first


@pytest.mark.parametrize("location", [
    "http://169.254.169.254/latest/meta-data/pkg.csar",   # cloud metadata endpoint
    "http://127.0.0.1:8000/pkg.csar",
    "http://localhost/pkg.csar",
    "file:///etc/pkg.csar",
])
def test_onboard_never_fetches_a_disallowed_location(client, monkeypatch, location):
    """CodeQL py/full-ssrf: the package location is caller-supplied, so it goes
    through the shared SSRF guard before any fetch; a refused location is a
    FAILED onboarding and the network is never touched."""
    def must_not_fetch(*args, **kwargs):
        raise AssertionError("a disallowed package location was fetched")
    monkeypatch.setattr("app.main.httpx.get", must_not_fetch)

    resp = client.post("/packages", json={"location": location})
    assert resp.status_code == 202
    status = client.get(f"/packages/{resp.json()['packageId']}/onboarding-status")
    assert status.json()["state"] == "FAILED"


# ---------------------------------------------------------------- fuzz findings (fuzz/fuzz_csar_parsers.py)

def _zip_with(**entries):
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in entries.items():
            z.writestr(name.replace("__", "/").replace("_", "."), content)
    return zipfile.ZipFile(BytesIO(buf.getvalue()))


def test_a_non_utf8_sme_declaration_is_a_validation_failure_not_a_crash():
    """The fuzzer's first finding: json.loads on non-UTF-8 bytes raised
    UnicodeDecodeError, which was not an onboarding validation failure."""
    from app.main import ONBOARD_VALIDATION_FAILURES, _parse_sme_declarations

    z = _zip_with(Files__Sme__serviceapis__a_json=b"\xf7\xff\xfe")
    with pytest.raises(ONBOARD_VALIDATION_FAILURES):
        _parse_sme_declarations(z)


@pytest.mark.parametrize("name, content", [
    ("manifest_yaml", "- a\n- b\n"),                  # a list, not a mapping
    ("manifest_yaml", "rappManifest: [1, 2]\n"),      # rappManifest not a mapping
    ("capabilities_yaml", "- consumes\n"),
    ("capabilities_yaml", "capabilities: [x]\n"),
])
def test_a_manifest_or_capabilities_file_that_is_not_a_mapping_fails_validation(name, content):
    from app.main import PackageValidationFailed, _parse_ai_capabilities

    with pytest.raises(PackageValidationFailed):
        _parse_ai_capabilities(_zip_with(**{name: content}))


def test_onboarding_a_package_with_a_non_utf8_sme_file_ends_in_failed(client, monkeypatch):
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("TOSCA-Metadata/TOSCA.meta", "Entry-Definitions: Definitions/asd.yaml\n")
        z.writestr("Definitions/asd.yaml", "application_name: demo\n")
        z.writestr("Files/Sme/serviceapis/a.json", b"\xf7\xff\xfe")

    class FakeHttpResponse:
        content = buf.getvalue()
        def raise_for_status(self):
            pass

    monkeypatch.setattr("app.main.httpx.get", lambda location, timeout=None: FakeHttpResponse())
    resp = client.post("/packages", json={"location": "http://example/pkg.csar"})
    assert resp.status_code == 202
    status = client.get(f"/packages/{resp.json()['packageId']}/onboarding-status")
    assert status.json()["state"] == "FAILED"


def test_a_concurrent_writer_turns_a_transition_into_a_409_and_the_repeat_succeeds(client, monkeypatch):
    """PR-ST-2: ApplicationPackage is versioned; a stale write is a 409, not a lost update."""
    package_id = _make_available_package(client, monkeypatch)

    with concurrent_commit_on("application_package") as fired:
        stale = client.post(f"/packages/{package_id}/deprecate")
    assert fired and stale.status_code == 409
    assert stale.json()["detail"]["title"] == "CONCURRENT_MODIFICATION"

    repeat = client.post(f"/packages/{package_id}/deprecate")
    assert repeat.status_code == 200 and repeat.json()["state"] == "DEPRECATED"


def test_an_application_type_the_column_does_not_accept_is_a_422_not_a_500(client):
    """Found by the authenticated DAST scan (V-7d): `application_type` has a CHECK, and the request took any string."""
    response = client.post("/packages", json={"location": "http://example.invalid/x.csar", "applicationType": "string"})
    assert response.status_code == 422
