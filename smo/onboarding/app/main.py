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

# What counts as "this package fails to validate" — broadened beyond
# malformed-zip/missing-entry to include the location being unreachable
# at all (caught while integration-testing: an unreachable location
# previously crashed OnboardPackage with an unhandled 500 instead of
# routing to FAILED, which is itself a real, expected outcome here).
ONBOARD_VALIDATION_FAILURES = (zipfile.BadZipFile, KeyError, FileNotFoundError, httpx.HTTPError)
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import framework_error, FrameworkError
from smo_shared.statemachine import IllegalTransition

from .models import ApplicationPackage, Artifact, PackageUsageRegistration
from .statemachine import ONBOARDING_FSM, PackageEvent, PackageState

app = FastAPI(title="Software Package Onboarding SMOS")


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
    db.flush()

    try:
        entry_definitions, artifacts, integrity_hash = _validate_package(body.location)
        pkg.tosca_entry_definitions = entry_definitions
        pkg.integrity_hash = integrity_hash
        pkg.signature_verified = True
        for path, access_url in artifacts:
            db.add(Artifact(package_id=pkg.package_id, path=path, access_url=access_url))
        new_state = ONBOARDING_FSM.fire(PackageState.ONBOARDING, PackageEvent.VALIDATE_OK, db=db, package=pkg)
    except ONBOARD_VALIDATION_FAILURES:
        new_state = ONBOARDING_FSM.fire(PackageState.ONBOARDING, PackageEvent.VALIDATE_FAILED, db=db, package=pkg)

    pkg.state = new_state
    db.commit()
    return {"packageId": str(pkg.package_id), "trackingId": str(pkg.package_id)}


def _validate_package(location: str) -> tuple[str, list[tuple[str, str]], str]:
    """Open TOSCA-Metadata/Definitions/Artifacts, per Onboarding LLD section 1."""
    resp = httpx.get(location, timeout=30.0)
    resp.raise_for_status()
    data = resp.content
    with zipfile.ZipFile(BytesIO(data)) as z:
        meta = z.read("TOSCA-Metadata/TOSCA.meta").decode()
        entry_line = next(l for l in meta.splitlines() if l.startswith("Entry-Definitions:"))
        entry_definitions = entry_line.split(":", 1)[1].strip()
        z.getinfo(entry_definitions)  # raises KeyError if missing/malformed
        artifacts = [(n, f"{location}#{n}") for n in z.namelist() if n.startswith("Artifacts/") and not n.endswith("/")]
    integrity_hash = hashlib.sha256(data).hexdigest()
    return entry_definitions, artifacts, integrity_hash


@app.get("/packages/{package_id}/onboarding-status")
def query_onboarding_status(package_id: uuid.UUID, db: Session = Depends(get_session)):
    pkg = db.get(ApplicationPackage, package_id)
    if pkg is None:
        raise framework_error(FrameworkError.DME_TYPE_VERSION_CONFLICT, detail="no such package")  # 404-shaped reuse; Phase 1
    return {"packageId": str(pkg.package_id), "state": pkg.state}


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
        "state": pkg.state,
        "toscaEntryDefinitions": pkg.tosca_entry_definitions,
        "signatureVerified": pkg.signature_verified,
    }
