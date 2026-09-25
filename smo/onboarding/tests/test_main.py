"""Tests for the OnboardPackage route (Onboarding/rApp Mgmt LLD sections
1-2), covering NFO's CreateDescriptor wiring — the actual fix for the
NFDeploymentDescriptor gap OPEN_ITEMS.md flagged as the top item.
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
    monkeypatch.setattr("app.main._validate_package", lambda location: ("Definitions/main.yaml", [], "deadbeef"))
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
    monkeypatch.setattr("app.main._validate_package", lambda location: ("Definitions/main.yaml", [], "deadbeef"))
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


def _real_package_bytes(include_acm_composition=True) -> bytes:
    """A minimal but genuinely well-formed CSAR — TOSCA-Metadata/TOSCA.meta
    pointing at a real Definitions/ entry, optionally with the reference's
    required composition file alongside it, at its real path
    (`RappCsarPathProvider.ACM_COMPOSITION_JSON_LOCATION`,
    `FileExistenceValidator.java`): Files/Acm/definition/compositions.json
    — not Definitions/acm_composition.json, which this fixture and
    _validate_package both got wrong before being checked against the
    reference's real sample package.
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("TOSCA-Metadata/TOSCA.meta", "Entry-Definitions: Definitions/main.yaml\n")
        z.writestr("Definitions/main.yaml", "tosca_definitions_version: tosca_simple_yaml_1_3\n")
        if include_acm_composition:
            z.writestr("Files/Acm/definition/compositions.json", "{}")
    return buf.getvalue()


def _mock_fetch(monkeypatch, content: bytes) -> None:
    class FakeHttpResponse:
        def raise_for_status(self):
            pass
    FakeHttpResponse.content = content
    monkeypatch.setattr("app.main.httpx.get", lambda location, timeout=None: FakeHttpResponse())


def test_onboard_routes_to_failed_when_location_does_not_end_with_csar(client, monkeypatch):
    """OPEN_ITEMS.md section 5: the reference's own NamingValidator — a
    package filename that doesn't follow the `.csar` convention was
    previously accepted without complaint. Checked before ever fetching
    the location, so no network mock is even needed here.
    """
    resp = client.post("/packages", json={"location": "http://example/pkg.zip"})
    package_id = resp.json()["packageId"]

    status = client.get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "FAILED"


def test_onboard_routes_to_failed_when_acm_composition_json_is_missing(client, monkeypatch):
    """OPEN_ITEMS.md section 5: the reference's own FileExistenceValidator
    requires Files/Acm/definition/compositions.json alongside
    TOSCA-Metadata/TOSCA.meta — previously never checked, a package
    missing it onboarded successfully anyway.
    """
    _mock_fetch(monkeypatch, _real_package_bytes(include_acm_composition=False))

    resp = client.post("/packages", json={"location": "http://example/pkg.csar"})
    package_id = resp.json()["packageId"]

    status = client.get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "FAILED"


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


def test_onboard_routes_to_failed_for_a_byte_identical_duplicate_package(client, monkeypatch):
    """OPEN_ITEMS.md section 5: the reference's own AsdDescriptorValidator
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


def test_onboarding_status_for_unknown_package_reuses_dme_type_version_conflict(client):
    """query_onboarding_status's own comment calls this a "404-shaped
    reuse", but DME_TYPE_VERSION_CONFLICT is actually a 409
    (smo_shared/errors.py) — asserting the real status code, not the
    comment's description of it.
    """
    resp = client.get(f"/packages/{uuid.uuid4()}/onboarding-status")
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "DME_TYPE_VERSION_CONFLICT"


def _make_available_package(client, monkeypatch, integrity_hash="deadbeef") -> str:
    # integrity_hash is a real, checked field now (OPEN_ITEMS.md section 5's
    # duplicate-package detection) — a caller onboarding more than one
    # package in the same test must vary it, or the second one routes to
    # FAILED as a genuine duplicate.
    monkeypatch.setattr("app.main._validate_package", lambda location: ("Definitions/main.yaml", [], integrity_hash))
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))
    return client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]


def test_query_packages_lists_and_filters_by_state(client, monkeypatch):
    available_id = _make_available_package(client, monkeypatch)
    monkeypatch.setattr("app.main._validate_package", lambda location: (_ for _ in ()).throw(KeyError("Definitions/missing.yaml")))
    client.post("/packages", json={"location": "http://example/other.csar"})  # routes to FAILED

    all_packages = client.get("/packages").json()
    assert len(all_packages) == 2

    available_only = client.get("/packages", params={"state": "AVAILABLE"}).json()
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
    """OPEN_ITEMS.md section 5: the reference's real
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

    with_package = client.get("/packages", params={"state": "PRIMED"}).json()
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
        session.add(ApplicationPackage(package_id=uuid.uuid4(), application_type="rApp", name="hello-world", version="1.0.0",
                                        vendor="acme", state="AVAILABLE", manifest_ref="m"))
        session.commit()
    [pkg] = client.get("/packages").json()
    assert (pkg["name"], pkg["version"], pkg["vendor"], pkg["applicationType"]) == ("hello-world", "1.0.0", "acme", "rApp")
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
