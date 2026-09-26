"""Software Package Onboarding SMOS.

SMO Design v1.3 section 3.4, extended by Onboarding/rApp Mgmt LLD sections
1-4: concrete TOSCA-Metadata/Definitions/Artifacts package format, the
FAILED terminal state, and the cascade-delete guard (statemachine.py).
"""

import hashlib
import uuid
import zipfile
from io import BytesIO

import httpx

class DescriptorCreationFailed(Exception):
    """NFO's CreateDescriptor call (NFO+FOCOM LLD section 2) didn't return
    201 — treated the same as any other onboarding validation failure.
    """


class PackageValidationFailed(Exception):
    """OPEN_ITEMS.md section 5: the reference's own ordered validator
    chain (rapp-manager-models' csar/validator/*) catches a package
    whose filename doesn't follow convention (NamingValidator) or that
    duplicates one already onboarded (AsdDescriptorValidator's own
    descriptorId-uniqueness check, adapted here to this build's own
    identity — a content hash — since real ASD descriptor data is a
    deliberate elision elsewhere in this build). _validate_package
    previously only checked the zip was well-formed and had its entry
    definitions.
    """


# What counts as "this package fails to validate" — broadened beyond
# malformed-zip/missing-entry to include the location being unreachable
# at all (caught while integration-testing: an unreachable location
# previously crashed OnboardPackage with an unhandled 500 instead of
# routing to FAILED, which is itself a real, expected outcome here), and
# now DescriptorCreationFailed/PackageValidationFailed alongside it.
ONBOARD_VALIDATION_FAILURES = (zipfile.BadZipFile, KeyError, FileNotFoundError, httpx.HTTPError, DescriptorCreationFailed, PackageValidationFailed)
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import framework_error, FrameworkError
from smo_shared.r1_client import R1Client
from smo_shared.statemachine import IllegalTransition

from .models import ApplicationPackage, Artifact, PackageUsageRegistration
from .statemachine import ONBOARDING_FSM, PackageEvent, PackageState

app = FastAPI(title="Software Package Onboarding SMOS")


@app.get("/health")
def health_check():
    """Liveness probe. The GUI BFF's GET /modules/status fans out to
    /<module>/health through R1 Termination for every module in parallel,
    so every module answers one — previously only ran-nf-oam/a1-related
    did (as their own DME producer-health callback URL).
    """
    return {"status": "healthy"}


class OnboardRequest(BaseModel):
    location: str
    applicationType: str = "rApp"


@app.post("/packages", status_code=202)
def onboard_package(body: OnboardRequest, db: Session = Depends(get_session)):
    """OnboardPackage(location) — async. Onboarding LLD section 3's
    concretized validation pipeline: fetch, open TOSCA.meta, verify
    signature (internal-consistency only, D-SEC-ONBD-1), parse
    Definitions/<name>.yaml, register Artifacts, THEN reach AVAILABLE.
    """
    pkg = ApplicationPackage(
        application_type=body.applicationType,
        name="unresolved-until-validated",
        version="0.0.0",
        manifest_ref=body.location,
        state=PackageState.ONBOARDING,
    )
    db.add(pkg)
    # A real commit, not just flush() — _create_nf_deployment_descriptor
    # below calls NFO over a real network hop (a separate service, its own
    # DB connection in production), and NFO's own NFDeploymentDescriptor
    # row has a real FK on application_package.package_id. flush() only
    # makes this row visible within THIS session's own uncommitted
    # transaction; under Postgres's real READ COMMITTED isolation, NFO's
    # separate connection can't see it yet, so its own INSERT would hit a
    # genuine ForeignKeyViolation — a real bug (masked in this build's own
    # SQLite test harness, whose single shared StaticPool connection gives
    # every nested session an accidental dirty-read view of this session's
    # uncommitted work).
    db.commit()

    try:
        entry_definitions, artifacts, integrity_hash = _validate_package(body.location)
        # AsdDescriptorValidator's own duplicate-descriptor-id detection,
        # adapted to this build's own package identity (a content hash,
        # since real ASD descriptor data doesn't exist here) — a
        # byte-identical package already onboarded is rejected the same
        # way, not silently onboarded a second time.
        existing = db.scalar(select(ApplicationPackage).where(
            ApplicationPackage.integrity_hash == integrity_hash, ApplicationPackage.package_id != pkg.package_id,
        ))
        if existing is not None:
            raise PackageValidationFailed(f"package with integrity hash {integrity_hash} already onboarded as {existing.package_id}")
        pkg.tosca_entry_definitions = entry_definitions
        pkg.integrity_hash = integrity_hash
        pkg.signature_verified = True
        for path, access_url in artifacts:
            db.add(Artifact(package_id=pkg.package_id, path=path, access_url=access_url))
        pkg.nf_deployment_descriptor_id = _create_nf_deployment_descriptor(pkg, entry_definitions)
        new_state = ONBOARDING_FSM.fire(PackageState.ONBOARDING, PackageEvent.VALIDATE_OK, db=db, package=pkg)
    except ONBOARD_VALIDATION_FAILURES:
        new_state = ONBOARDING_FSM.fire(PackageState.ONBOARDING, PackageEvent.VALIDATE_FAILED, db=db, package=pkg)

    pkg.state = new_state
    db.commit()
    return {"packageId": str(pkg.package_id), "trackingId": str(pkg.package_id)}


def _create_nf_deployment_descriptor(pkg: ApplicationPackage, entry_definitions: str) -> uuid.UUID:
    """NFO+FOCOM LLD section 2: NFDeploymentDescriptor is derived from the
    onboarded package's TOSCA Definitions/ at onboarding time — the actual
    fix for the gap where nfDeploymentDescriptorId referenced nothing
    concrete and rApp Management passed packageId directly where NFO
    expected a real descriptor. Phase 1: workloadTemplate is a thin
    reference to the entry definitions rather than a fully parsed TOSCA
    node template.
    """
    resp = R1Client().post("/nfo/descriptors", json={
        "packageId": str(pkg.package_id), "name": entry_definitions,
        "workloadTemplate": {"toscaEntryDefinitions": entry_definitions},
    })
    if resp.status_code != 201:
        raise DescriptorCreationFailed(f"NFO CreateDescriptor returned {resp.status_code}")
    return uuid.UUID(resp.json()["nfDeploymentDescriptorId"])


def _validate_package(location: str) -> tuple[str, list[tuple[str, str]], str]:
    """Open TOSCA-Metadata/Definitions/Artifacts, per Onboarding LLD section 1.

    OPEN_ITEMS.md section 5: two more checks from the reference's own
    validator chain, previously entirely absent — NamingValidator's
    filename convention (a package location not ending in `.csar` is
    rejected up front, before ever fetching it) and
    FileExistenceValidator's required composition file (checked alongside
    the existing `TOSCA-Metadata/TOSCA.meta` requirement, not replacing
    it — the reference requires both). The exact path was wrong before —
    `Definitions/acm_composition.json` — a guess that didn't match the
    reference; the real one is `RappCsarPathProvider.
    ACM_COMPOSITION_JSON_LOCATION` (`FileExistenceValidator.java`):
    `Files/Acm/definition/compositions.json`. Caught while adapting the
    reference's own real sample package (`sample-rapp-generator/rapp-all`,
    which puts its composition file at exactly this path) for a demo —
    the old path would have rejected every real CSAR the reference itself
    produces, even though this build's own synthetic test fixture
    (constructed to match the same wrong assumption) never caught it.
    """
    if not location.endswith(".csar"):
        raise PackageValidationFailed(f"package location {location!r} does not end with .csar")
    resp = httpx.get(location, timeout=30.0)
    resp.raise_for_status()
    data = resp.content
    with zipfile.ZipFile(BytesIO(data)) as z:
        meta = z.read("TOSCA-Metadata/TOSCA.meta").decode()
        entry_line = next(l for l in meta.splitlines() if l.startswith("Entry-Definitions:"))
        entry_definitions = entry_line.split(":", 1)[1].strip()
        z.getinfo(entry_definitions)  # raises KeyError if missing/malformed
        z.getinfo("Files/Acm/definition/compositions.json")  # required per the reference's FileExistenceValidator
        artifacts = [(n, f"{location}#{n}") for n in z.namelist() if n.startswith("Artifacts/") and not n.endswith("/")]
    integrity_hash = hashlib.sha256(data).hexdigest()
    return entry_definitions, artifacts, integrity_hash


@app.get("/packages/{package_id}/onboarding-status")
def query_onboarding_status(package_id: uuid.UUID, db: Session = Depends(get_session)):
    pkg = db.get(ApplicationPackage, package_id)
    if pkg is None:
        raise framework_error(FrameworkError.DME_TYPE_VERSION_CONFLICT, detail="no such package")  # 404-shaped reuse; Phase 1
    return {
        "packageId": str(pkg.package_id), "state": pkg.state,
        "nfDeploymentDescriptorId": str(pkg.nf_deployment_descriptor_id) if pkg.nf_deployment_descriptor_id else None,
    }


@app.get("/packages")
def query_packages(state: str | None = None, db: Session = Depends(get_session)):
    stmt = select(ApplicationPackage)
    if state:
        stmt = stmt.where(ApplicationPackage.state == state)
    return [_package_view(p) for p in db.scalars(stmt).all()]


@app.post("/packages/{package_id}/deprecate")
def deprecate_package(package_id: uuid.UUID, db: Session = Depends(get_session)):
    pkg = db.get(ApplicationPackage, package_id)
    pkg.state = ONBOARDING_FSM.fire(PackageState(pkg.state), PackageEvent.DEPRECATE, db=db, package=pkg)
    db.commit()
    return _package_view(pkg)


@app.post("/packages/{package_id}/prime")
def prime_package(package_id: uuid.UUID, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: the reference's real
    COMMISSIONED->PRIMING->PRIMED lifecycle, missing entirely — this
    build went ONBOARDING->AVAILABLE directly. Real ACM/DME/SME
    resource pre-provisioning behind priming is out of scope (same
    elision as this build's other southbound calls), so both
    transitions fire within this one request.
    """
    pkg = db.get(ApplicationPackage, package_id)
    pkg.state = ONBOARDING_FSM.fire(PackageState(pkg.state), PackageEvent.PRIME, db=db, package=pkg)
    pkg.state = ONBOARDING_FSM.fire(PackageState(pkg.state), PackageEvent.PRIME_COMPLETE, db=db, package=pkg)
    db.commit()
    return _package_view(pkg)


@app.post("/packages/{package_id}/deprime")
def deprime_package(package_id: uuid.UUID, db: Session = Depends(get_session)):
    """The reference's own deprimeRapp guard: blocked while any rApp
    instance still references this package (mirrors the cascade-delete
    guard's active-usage-registration check).
    """
    pkg = db.get(ApplicationPackage, package_id)
    try:
        pkg.state = ONBOARDING_FSM.fire(PackageState(pkg.state), PackageEvent.DEPRIME, db=db, package=pkg)
    except IllegalTransition:
        raise framework_error(FrameworkError.SERVICE_NAME_CONFLICT, detail="blocked by an active usage registration")
    pkg.state = ONBOARDING_FSM.fire(PackageState(pkg.state), PackageEvent.DEPRIME_COMPLETE, db=db, package=pkg)
    db.commit()
    return _package_view(pkg)


@app.post("/packages/{package_id}/cancel-delete")
def cancel_delete(package_id: uuid.UUID, db: Session = Depends(get_session)):
    pkg = db.get(ApplicationPackage, package_id)
    pkg.state = ONBOARDING_FSM.fire(PackageState(pkg.state), PackageEvent.CANCEL_DELETE, db=db, package=pkg)
    db.commit()
    return _package_view(pkg)


@app.delete("/packages/{package_id}")
def delete_package(package_id: uuid.UUID, db: Session = Depends(get_session)):
    pkg = db.get(ApplicationPackage, package_id)
    if pkg.state == PackageState.FAILED:
        # FAILED never reached AVAILABLE, so nothing can depend on it — cascade
        # check skipped entirely, per Onboarding LLD section 3.
        db.delete(pkg)
        db.commit()
        return {"status": "deleted"}
    try:
        new_state = ONBOARDING_FSM.fire(PackageState(pkg.state), PackageEvent.DELETE, db=db, package=pkg)
    except IllegalTransition:
        raise framework_error(FrameworkError.SERVICE_NAME_CONFLICT, detail="blocked by a dependent child package or active usage registration")
    pkg.state = new_state
    db.commit()
    return _package_view(pkg)


@app.post("/packages/{package_id}/usage/start")
def register_usage_start(package_id: uuid.UUID, consumer_id: str, db: Session = Depends(get_session)):
    reg = PackageUsageRegistration(package_id=package_id, consumer_id=consumer_id)
    db.add(reg)
    db.commit()
    return {"registrationId": str(reg.id)}


@app.post("/packages/{package_id}/usage/{registration_id}/stop")
def register_usage_stop(package_id: uuid.UUID, registration_id: uuid.UUID, db: Session = Depends(get_session)):
    import datetime

    reg = db.get(PackageUsageRegistration, registration_id)
    reg.stopped_at = datetime.datetime.now(datetime.UTC)
    db.commit()
    return {"status": "stopped"}


def _package_view(pkg: ApplicationPackage) -> dict:
    return {
        "packageId": str(pkg.package_id),
        "name": pkg.name,
        "version": pkg.version,
        "vendor": pkg.vendor,
        "applicationType": pkg.application_type,
        "state": pkg.state,
        "toscaEntryDefinitions": pkg.tosca_entry_definitions,
        "signatureVerified": pkg.signature_verified,
        "nfDeploymentDescriptorId": str(pkg.nf_deployment_descriptor_id) if pkg.nf_deployment_descriptor_id else None,
    }


@app.get("/packages/{package_id}/artifacts")
def list_package_artifacts(package_id: uuid.UUID, db: Session = Depends(get_session)):
    """(GUI pass 2) The artifacts registered during validation (Onboarding LLD section 1)."""
    return [{"artifactId": str(a.artifact_id), "path": a.path, "accessUrl": a.access_url}
            for a in db.scalars(select(Artifact).where(Artifact.package_id == package_id)).all()]


@app.get("/packages/{package_id}/usage")
def list_package_usage(package_id: uuid.UUID, db: Session = Depends(get_session)):
    """(GUI pass 2) Usage registrations behind the cascade-delete guard (call flow 06):
    any row still missing stoppedAt blocks deprime and delete, so the operator
    can now see why a delete was refused.
    """
    return [{"registrationId": str(r.id), "consumerId": r.consumer_id,
             "stoppedAt": r.stopped_at.isoformat() if r.stopped_at else None, "active": r.stopped_at is None}
            for r in db.scalars(select(PackageUsageRegistration).where(PackageUsageRegistration.package_id == package_id)).all()]
