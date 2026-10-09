"""Software Package Onboarding SMOS.

SMO Design v1.3 section 3.4, extended by Onboarding/rApp Mgmt LLD sections
1-4: concrete TOSCA-Metadata/Definitions/Artifacts package format, the
FAILED terminal state, and the cascade-delete guard (statemachine.py).
"""

import logging
import os
import uuid
from typing import Literal

import httpx
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared import csar_signing
from smo_shared.logconfig import install_logging
from smo_shared.metrics import count_by, install_metrics, register_query_gauge
from smo_shared.health import database_check, install_health, sme_token_check
from smo_shared.db import get_session
from smo_shared.errors import framework_error, FrameworkError, illegal_transition_error
from smo_shared.r1_client import R1Client
from smo_shared.statemachine import IllegalTransition
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.runtime_resources import container_resources
from smo_shared.pagination import PageLimit, PageOffset, paginate
from smo_shared.webhook import is_safe_webhook_destination
from smo_shared.versioning import install_concurrency_handler

from .package_validation import (EXECUTION_MODES, LIMIT_SPECS, PARSE_FAILURES, PackageValidationFailed, _asd_identity, _parse_ai_capabilities,  # noqa: F401  (re-exported: the tests and the fuzz target import them from here)
                                 _parse_sme_declarations, _validate_limits, _validate_runtime_profiles, validate_package_bytes)
from .models import ApplicationPackage, Artifact, PackageUsageRegistration
from .statemachine import ONBOARDING_FSM, PackageEvent, PackageState


class DescriptorCreationFailed(Exception):
    """NFO's CreateDescriptor call (NFO+FOCOM LLD section 2) didn't return
    201 — treated the same as any other onboarding validation failure.
    """


# What counts as "this package fails to validate" — broadened beyond
# malformed-zip/missing-entry to include the location being unreachable
# at all (caught while integration-testing: an unreachable location
# previously crashed OnboardPackage with an unhandled 500 instead of
# routing to FAILED, which is itself a real, expected outcome here), and
# now DescriptorCreationFailed/PackageValidationFailed alongside it, and
# yaml.YAMLError for the Wave 1 manifest.yaml/capabilities.yaml
# extension — a malformed one is a validation failure like any other
# malformed package file, not an unhandled 500.
ONBOARD_VALIDATION_FAILURES = (*PARSE_FAILURES, httpx.HTTPError, DescriptorCreationFailed)

log = logging.getLogger(__name__)

app = FastAPI(title="Software Package Onboarding SMOS")
install_logging(app)  # structured JSON logs and one access-log line per request (PR-OBS-1)
install_metrics(app)  # /metrics and request count/latency series (PR-OBS-2)
register_query_gauge("smo_rapp_packages", "rApp packages, by lifecycle state.", ["state"],
                     lambda s: count_by(s, ApplicationPackage.state, PackageState))  # PR-OBS-4
install_concurrency_handler(app)  # a stale write (PR-ST-2) is a 409, not a 500
apply_r1_gateway_security(app)
apply_correlation_id(app)


install_health(app, checks=[database_check, sme_token_check])  # /live, /ready and the /health alias (PR-ST-7)


class OnboardRequest(BaseModel):
    location: str
    applicationType: Literal["rApp", "xApp", "CloudifiedNF", "PNF"] = "rApp"      # the column's CHECK (migrations/001_init.sql): any other value was a 500


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

    failure_reason: str | None = None
    try:
        entry_definitions, artifacts, integrity_hash, identity = _validate_package(body.location)
        # AsdDescriptorValidator's own duplicate-descriptor-id detection,
        # adapted to this build's own package identity (a content hash,
        # since real ASD descriptor data doesn't exist here) — a
        # byte-identical package already onboarded is rejected the same
        # way, not silently onboarded a second time. A package that is
        # DELETING (terminal: deleted, its row kept) or FAILED no longer
        # counts (OI-2-package-redeploy) — the same CSAR can be onboarded
        # again once its earlier package was deleted or failed.
        existing = db.scalar(select(ApplicationPackage).where(
            ApplicationPackage.integrity_hash == integrity_hash, ApplicationPackage.package_id != pkg.package_id,
            ApplicationPackage.state.not_in([PackageState.DELETING, PackageState.FAILED]),
        ).limit(1))
        if existing is not None:
            raise PackageValidationFailed(f"package with integrity hash {integrity_hash} already onboarded as {existing.package_id}")
        pkg.tosca_entry_definitions = entry_definitions
        pkg.integrity_hash = integrity_hash
        pkg.name = identity.get("name", pkg.name)
        pkg.version = identity.get("version", pkg.version)
        pkg.vendor = identity.get("vendor", pkg.vendor)
        pkg.descriptor_id = identity.get("descriptor_id")
        pkg.descriptor_invariant_id = identity.get("descriptor_invariant_id")
        pkg.descriptor_version = identity.get("descriptor_version")
        pkg.schema_version = identity.get("schema_version")
        pkg.ai_capabilities = identity.get("ai_capabilities")
        pkg.sme_declarations = identity.get("sme_declarations")
        # PR-RAPP-1: with no trust store configured this stays what it always was (the package validated; nothing was checked against a key); with one it is
        # true only for a package whose signature verified, and false for an unsigned one the policy lets through
        pkg.signature_verified = identity.get("signature_verified", True)
        if identity.get("signed_by"):
            log.info("package %s is signed by publisher %s", pkg.package_id, identity["signed_by"])
        for path, access_url in artifacts:
            db.add(Artifact(package_id=pkg.package_id, path=path, access_url=access_url))
        pkg.nf_deployment_descriptor_id = _create_nf_deployment_descriptor(pkg, entry_definitions)
        new_state = ONBOARDING_FSM.fire(PackageState.ONBOARDING, PackageEvent.VALIDATE_OK, db=db, package=pkg)
    except ONBOARD_VALIDATION_FAILURES as exc:
        new_state = ONBOARDING_FSM.fire(PackageState.ONBOARDING, PackageEvent.VALIDATE_FAILED, db=db, package=pkg)
        failure_reason = str(exc) if isinstance(exc, PackageValidationFailed) else type(exc).__name__
        log.warning("package %s failed validation: %s", pkg.package_id, failure_reason)

    pkg.state = new_state
    db.commit()
    accepted = {"packageId": str(pkg.package_id), "trackingId": str(pkg.package_id)}
    if failure_reason is not None:
        accepted["failureReason"] = failure_reason     # GUI-8.2: the precise message of a refused package (a declaration's place and rule); not stored
    return accepted


def _create_nf_deployment_descriptor(pkg: ApplicationPackage, entry_definitions: str) -> uuid.UUID:
    """NFO+FOCOM LLD section 2: NFDeploymentDescriptor is derived from the
    onboarded package's TOSCA Definitions/ at onboarding time — the actual
    fix for the gap where nfDeploymentDescriptorId referenced nothing
    concrete and rApp Management passed packageId directly where NFO
    expected a real descriptor. Phase 1: workloadTemplate is a thin
    reference to the entry definitions rather than a fully parsed TOSCA
    node template.
    """
    workload: dict = {"toscaEntryDefinitions": entry_definitions}
    # PR-RAPP-2.1: the manifest's CPU and memory per execution mode as the container resources a deployment manager applies (smo_shared/runtime_resources.py);
    # absent for a package with no runtimeProfiles, so its descriptor is what it was
    by_mode = {mode: resources for mode, profile in ((pkg.ai_capabilities or {}).get("runtimeProfiles") or {}).items() if (resources := container_resources(profile))}
    if by_mode:
        workload["containerResourcesByMode"] = by_mode
    resp = R1Client().post("/nfo/descriptors", json={
        "packageId": str(pkg.package_id), "name": entry_definitions,
        "workloadTemplate": workload,
    })
    if resp.status_code != 201:
        raise DescriptorCreationFailed(f"NFO CreateDescriptor returned {resp.status_code}")
    return uuid.UUID(resp.json()["nfDeploymentDescriptorId"])



def _validate_package(location: str) -> tuple[str, list[tuple[str, str]], str, dict]:
    """Open TOSCA-Metadata/Definitions/Artifacts, per Onboarding LLD section 1.

    HISTORY.md §5: NamingValidator's filename convention (a
    package location not ending in `.csar` is rejected up front, before
    ever fetching it), adopted from the reference's own validator chain.

    The reference's own FileExistenceValidator additionally requires
    `Files/Acm/definition/compositions.json` (an ONAP ACM composition
    file) alongside `TOSCA-Metadata/TOSCA.meta` — deliberately NOT
    adopted here. This build never calls ONAP ACM at all (real
    deployment orchestration is a declared elision — see
    rapp-mgmt/app/main.py's own CreateInstance), so requiring every CSAR
    to bundle an ONAP-specific file just to pass validation would be
    requiring a dependency this build doesn't have, not real spec
    fidelity. A package with or without that file onboards the same way.
    """
    if not location.endswith(".csar"):
        raise PackageValidationFailed(f"package location {location!r} does not end with .csar")
    # The location is caller-supplied and fetched from here (CodeQL py/full-ssrf):
    # the same scheme + loopback/link-local/metadata guard every other outbound
    # call in this build goes through (smo_shared.webhook), before anything is fetched.
    if not is_safe_webhook_destination(location):
        raise PackageValidationFailed(f"package location {location!r} is not an allowed http(s) destination")
    resp = httpx.get(location, timeout=30.0)
    resp.raise_for_status()
    data = resp.content
    trust, require_signed = _signing_policy()
    return validate_package_bytes(data, location, trust=trust, require_signed=require_signed)


def _signing_policy() -> tuple["csar_signing.TrustStore | None", bool]:
    """PR-RAPP-1.3/1.5, read for each package so a changed ConfigMap or a rotated key is seen without a restart. `ONBOARDING_TRUST_STORE`: a file or a directory
    of PEM public keys of the publishers whose packages are accepted (empty: no signature is checked, as before). `ONBOARDING_REQUIRE_SIGNED_PACKAGES`:
    `true` refuses a package that is not signed by one of them (default `false`). A trust store that cannot be used fails the package with the reason, and does
    not fall back to "trust nothing, check nothing"."""
    location = os.environ.get("ONBOARDING_TRUST_STORE", "").strip()
    require = os.environ.get("ONBOARDING_REQUIRE_SIGNED_PACKAGES", "false").strip().lower() in ("1", "true", "yes", "on")
    if not location:
        return None, require
    try:
        return csar_signing.load_trust_store(location), require
    except csar_signing.TrustStoreError as exc:
        raise PackageValidationFailed(f"the trust store cannot be used: {exc}") from exc


def _get_package_or_404(db: Session, package_id: uuid.UUID) -> ApplicationPackage:
    pkg = db.get(ApplicationPackage, package_id)
    if pkg is None:
        raise framework_error(FrameworkError.PACKAGE_NOT_FOUND, detail=f"no such package {package_id}")
    return pkg


def _fire(db: Session, pkg: ApplicationPackage, event: PackageEvent, guard_refusal: str | None = None) -> PackageState:
    """Fires `event` on `pkg`, mapping IllegalTransition to a 409. When the
    event has an edge from the current state, the refusal came from that
    edge's guard (active usage / blocking dependents) and is reported as
    409 SERVICE_NAME_CONFLICT with `guard_refusal`; otherwise the event is
    not allowed from this state at all: 409 LIFECYCLE_ILLEGAL_TRANSITION
    naming the state and the event (OI-2-lcm-error-mapping).
    """
    state = PackageState(pkg.state)
    try:
        return ONBOARDING_FSM.fire(state, event, db=db, package=pkg)
    except IllegalTransition as exc:
        if guard_refusal is not None and event in ONBOARDING_FSM.legal_events(state):
            raise framework_error(FrameworkError.SERVICE_NAME_CONFLICT, detail=guard_refusal) from exc
        raise illegal_transition_error(exc, f"package {pkg.package_id}") from exc


@app.get("/packages/{package_id}/onboarding-status")
def query_onboarding_status(package_id: uuid.UUID, db: Session = Depends(get_session)):
    """404 PACKAGE_NOT_FOUND for an unknown package."""
    pkg = _get_package_or_404(db, package_id)
    return {
        "packageId": str(pkg.package_id), "state": pkg.state,
        "nfDeploymentDescriptorId": str(pkg.nf_deployment_descriptor_id) if pkg.nf_deployment_descriptor_id else None,
        # rapp-mgmt's CreateInstance already calls this exact route to read
        # state/nfDeploymentDescriptorId — smeDeclarations rides along here
        # rather than a new dedicated route, for bootstrap-complete's own
        # per-instance SME registration (HISTORY.md §7's Onboarding/rApp
        # Mgmt finding 3).
        "smeDeclarations": pkg.sme_declarations,
        # Wave 7 (W7-03): AIMgF reads the package's runtimeProfiles from here
        # when it sizes a Training/Validation/Emulation/Inference runtime.
        "aiCapabilities": pkg.ai_capabilities,
    }


@app.get("/packages")
def query_packages(state: str | None = None, limit: int = PageLimit, offset: int = PageOffset,
                    db: Session = Depends(get_session)):
    stmt = select(ApplicationPackage)
    if state:
        stmt = stmt.where(ApplicationPackage.state == state)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_package_view(p) for p in page["items"]]}


@app.post("/packages/{package_id}/deprecate")
def deprecate_package(package_id: uuid.UUID, db: Session = Depends(get_session)):
    """AVAILABLE -> DEPRECATED; 409 LIFECYCLE_ILLEGAL_TRANSITION from any other state."""
    pkg = _get_package_or_404(db, package_id)
    pkg.state = _fire(db, pkg, PackageEvent.DEPRECATE)
    db.commit()
    return _package_view(pkg)


@app.post("/packages/{package_id}/prime")
def prime_package(package_id: uuid.UUID, db: Session = Depends(get_session)):
    """HISTORY.md §5: the reference's real
    COMMISSIONED->PRIMING->PRIMED lifecycle, missing entirely — this
    build went ONBOARDING->AVAILABLE directly. Real ACM/DME/SME
    resource pre-provisioning behind priming is out of scope (same
    elision as this build's other southbound calls), so both
    transitions fire within this one request. 409
    LIFECYCLE_ILLEGAL_TRANSITION from any state but AVAILABLE.
    """
    pkg = _get_package_or_404(db, package_id)
    pkg.state = _fire(db, pkg, PackageEvent.PRIME)
    pkg.state = _fire(db, pkg, PackageEvent.PRIME_COMPLETE)
    db.commit()
    return _package_view(pkg)


@app.post("/packages/{package_id}/deprime")
def deprime_package(package_id: uuid.UUID, db: Session = Depends(get_session)):
    """The reference's own deprimeRapp guard: blocked while any rApp
    instance still references this package (mirrors the cascade-delete
    guard's active-usage-registration check): 409 SERVICE_NAME_CONFLICT.
    Any state but PRIMED: 409 LIFECYCLE_ILLEGAL_TRANSITION.
    """
    pkg = _get_package_or_404(db, package_id)
    pkg.state = _fire(db, pkg, PackageEvent.DEPRIME, guard_refusal="blocked by an active usage registration")
    pkg.state = _fire(db, pkg, PackageEvent.DEPRIME_COMPLETE)
    db.commit()
    return _package_view(pkg)


@app.post("/packages/{package_id}/cancel-delete")
def cancel_delete(package_id: uuid.UUID, db: Session = Depends(get_session)):
    """DEPRECATED -> AVAILABLE; 409 LIFECYCLE_ILLEGAL_TRANSITION from any other state."""
    pkg = _get_package_or_404(db, package_id)
    pkg.state = _fire(db, pkg, PackageEvent.CANCEL_DELETE)
    db.commit()
    return _package_view(pkg)


@app.delete("/packages/{package_id}")
def delete_package(package_id: uuid.UUID, db: Session = Depends(get_session)):
    """AVAILABLE/DEPRECATED -> DELETING behind the cascade-delete guard (409
    SERVICE_NAME_CONFLICT when a dependent child package or an active usage
    registration blocks it); a FAILED package's row is deleted directly.
    Any other state (PRIMED, DELETING, ...): 409 LIFECYCLE_ILLEGAL_TRANSITION.
    """
    pkg = _get_package_or_404(db, package_id)
    if pkg.state == PackageState.FAILED:
        # FAILED never reached AVAILABLE, so nothing can depend on it — cascade
        # check skipped entirely, per Onboarding LLD section 3.
        db.delete(pkg)
        db.commit()
        return {"status": "deleted"}
    pkg.state = _fire(db, pkg, PackageEvent.DELETE,
                      guard_refusal="blocked by a dependent child package or active usage registration")
    db.commit()
    return _package_view(pkg)


@app.post("/packages/{package_id}/usage/start")
def register_usage_start(package_id: uuid.UUID, consumer_id: str, db: Session = Depends(get_session)):
    """404 PACKAGE_NOT_FOUND for an unknown package."""
    _get_package_or_404(db, package_id)
    reg = PackageUsageRegistration(package_id=package_id, consumer_id=consumer_id)
    db.add(reg)
    db.commit()
    return {"registrationId": str(reg.id)}


@app.post("/packages/{package_id}/usage/{registration_id}/stop")
def register_usage_stop(package_id: uuid.UUID, registration_id: uuid.UUID, db: Session = Depends(get_session)):
    """404 PACKAGE_NOT_FOUND for an unknown package, 404
    PACKAGE_USAGE_REGISTRATION_NOT_FOUND for a registration id that is
    unknown or belongs to another package. Idempotent: stopping an already
    stopped registration keeps its original stoppedAt.
    """
    import datetime

    _get_package_or_404(db, package_id)
    reg = db.get(PackageUsageRegistration, registration_id)
    if reg is None or reg.package_id != package_id:
        raise framework_error(FrameworkError.PACKAGE_USAGE_REGISTRATION_NOT_FOUND,
                              detail=f"no usage registration {registration_id} for package {package_id}")
    if reg.stopped_at is None:
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
        "descriptorId": pkg.descriptor_id,
        "descriptorInvariantId": pkg.descriptor_invariant_id,
        "descriptorVersion": pkg.descriptor_version,
        "schemaVersion": pkg.schema_version,
        "signatureVerified": pkg.signature_verified,
        "nfDeploymentDescriptorId": str(pkg.nf_deployment_descriptor_id) if pkg.nf_deployment_descriptor_id else None,
        "aiCapabilities": pkg.ai_capabilities,
        "smeDeclarations": pkg.sme_declarations,
    }


@app.get("/packages/{package_id}/artifacts")
def list_package_artifacts(package_id: uuid.UUID, limit: int = PageLimit, offset: int = PageOffset,
                            db: Session = Depends(get_session)):
    """(GUI pass 2) The artifacts registered during validation (Onboarding LLD section 1)."""
    _get_package_or_404(db, package_id)
    stmt = select(Artifact).where(Artifact.package_id == package_id)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"artifactId": str(a.artifact_id), "path": a.path, "accessUrl": a.access_url}
            for a in page["items"]]}


@app.get("/packages/{package_id}/usage")
def list_package_usage(package_id: uuid.UUID, limit: int = PageLimit, offset: int = PageOffset,
                        db: Session = Depends(get_session)):
    """(GUI pass 2) Usage registrations behind the cascade-delete guard (call flow 06):
    any row still missing stoppedAt blocks deprime and delete, so the operator
    can now see why a delete was refused.
    """
    _get_package_or_404(db, package_id)
    stmt = select(PackageUsageRegistration).where(PackageUsageRegistration.package_id == package_id)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"registrationId": str(r.id), "consumerId": r.consumer_id,
             "stoppedAt": r.stopped_at.isoformat() if r.stopped_at else None, "active": r.stopped_at is None}
            for r in page["items"]]}
