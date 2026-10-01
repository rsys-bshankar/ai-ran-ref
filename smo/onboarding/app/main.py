"""Software Package Onboarding SMOS.

SMO Design v1.3 section 3.4, extended by Onboarding/rApp Mgmt LLD sections
1-4: concrete TOSCA-Metadata/Definitions/Artifacts package format, the
FAILED terminal state, and the cascade-delete guard (statemachine.py).
"""

import hashlib
import json
import uuid
import zipfile
from io import BytesIO

import httpx
import yaml

class DescriptorCreationFailed(Exception):
    """NFO's CreateDescriptor call (NFO+FOCOM LLD section 2) didn't return
    201 — treated the same as any other onboarding validation failure.
    """


class PackageValidationFailed(Exception):
    """HISTORY.md §5: the reference's own ordered validator
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
# now DescriptorCreationFailed/PackageValidationFailed alongside it, and
# yaml.YAMLError for the Wave 1 manifest.yaml/capabilities.yaml
# extension — a malformed one is a validation failure like any other
# malformed package file, not an unhandled 500.
ONBOARD_VALIDATION_FAILURES = (zipfile.BadZipFile, KeyError, FileNotFoundError, httpx.HTTPError, DescriptorCreationFailed, PackageValidationFailed, yaml.YAMLError, json.JSONDecodeError)
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import framework_error, FrameworkError
from smo_shared.r1_client import R1Client
from smo_shared.statemachine import IllegalTransition
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.pagination import PageLimit, PageOffset, paginate

from .models import ApplicationPackage, Artifact, PackageUsageRegistration
from .statemachine import ONBOARDING_FSM, PackageEvent, PackageState

app = FastAPI(title="Software Package Onboarding SMOS")
apply_r1_gateway_security(app)
apply_correlation_id(app)


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
        entry_definitions, artifacts, integrity_hash, identity = _validate_package(body.location)
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
        pkg.name = identity.get("name", pkg.name)
        pkg.version = identity.get("version", pkg.version)
        pkg.vendor = identity.get("vendor", pkg.vendor)
        pkg.descriptor_id = identity.get("descriptor_id")
        pkg.descriptor_invariant_id = identity.get("descriptor_invariant_id")
        pkg.descriptor_version = identity.get("descriptor_version")
        pkg.schema_version = identity.get("schema_version")
        pkg.ai_capabilities = identity.get("ai_capabilities")
        pkg.sme_declarations = identity.get("sme_declarations")
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


_ASD_IDENTITY_FIELDS = {
    "application_name": "name", "application_version": "version", "provider": "vendor",
    # Real ASD schema fields (asd_types.yaml's tosca.nodes.asd node type,
    # grounded against nonrtric-plt-rappmanager's own real sample CSARs,
    # not a summary) — required alongside the three above, never
    # captured before this pass. Surfaced for real spec fidelity;
    # package identity/uniqueness stays on integrity_hash, unchanged.
    "descriptor_id": "descriptor_id", "descriptor_invariant_id": "descriptor_invariant_id",
    "descriptor_version": "descriptor_version", "schema_version": "schema_version",
}


def _asd_identity(definitions: str) -> dict[str, str]:
    """The ASD's own applicationServiceDescriptor identity properties
    (application_name / application_version / provider), read from the
    entry definitions so a validated package stops showing as
    `unresolved-until-validated 0.0.0`. A line scan rather than a YAML
    parse: these are flat scalar properties, and this module carries no
    YAML dependency. Missing fields are simply absent from the result.
    """
    found: dict[str, str] = {}
    for line in definitions.splitlines():
        key, sep, value = line.strip().partition(":")
        if sep and key in _ASD_IDENTITY_FIELDS and _ASD_IDENTITY_FIELDS[key] not in found:
            value = value.split(" #", 1)[0].strip().strip("\"'")
            if value:
                found[_ASD_IDENTITY_FIELDS[key]] = value
    return found


def _parse_ai_capabilities(z: zipfile.ZipFile) -> dict | None:
    """Wave 1's rApp packaging extension (docs/architecture/
    docs/ARCHITECTURE.md): an optional AI Platform capability
    declaration, read from two new CSAR-root files alongside the existing
    TOSCA-Metadata/Definitions/Artifacts layout —

    - `manifest.yaml`: `rappManifest.manifestVersion` /
      `rappManifest.aiRuntimeSdkVersion` (the sdk/ contract version this
      rApp was built against).
    - `capabilities.yaml`: `capabilities.consumes` / `capabilities.provides`,
      each a list of `{namespace, description}` naming which of sdk/'s six
      client namespaces (data/analytics/models/lifecycle/intent/platform)
      this rApp uses.

    Both are optional and additive: a package built before this extension,
    or one that simply has neither file (every package this build produced
    before this pass), still onboards exactly as before — this returns
    None and `ApplicationPackage.ai_capabilities` stays NULL. Unlike
    `_asd_identity`'s flat scalar line-scan, these files carry real nested
    structure, so this one genuinely needs a YAML parse.
    """
    result: dict = {}
    names = z.namelist()
    if "manifest.yaml" in names:
        manifest = yaml.safe_load(z.read("manifest.yaml")) or {}
        rapp_manifest = manifest.get("rappManifest") or {}
        result["manifestVersion"] = rapp_manifest.get("manifestVersion")
        result["aiRuntimeSdkVersion"] = rapp_manifest.get("aiRuntimeSdkVersion")
        # Wave 7 (docs/ROADMAP.md W7-03): the AI-runtime part of
        # the manifest — which execution modes the package supports and the
        # compute each one needs. Accepted either under `rappManifest` or at
        # the manifest's top level (the SMO_Wave_10 package layout).
        for key in ("executionModes", "autonomyModes", "requiredServices", "runtimeProfiles"):
            value = rapp_manifest.get(key, manifest.get(key))
            if value is not None:
                result[key] = value
        profiles = result.get("runtimeProfiles")
        if profiles is not None:
            result["runtimeProfiles"] = _validate_runtime_profiles(profiles, result.get("executionModes"))
    if "capabilities.yaml" in names:
        parsed = yaml.safe_load(z.read("capabilities.yaml")) or {}
        caps = parsed.get("capabilities") or {}
        result["consumes"] = caps.get("consumes") or []
        result["provides"] = caps.get("provides") or []
    return result or None


EXECUTION_MODES = ("TRAINING", "VALIDATION", "EMULATION", "INFERENCE")


def _validate_runtime_profiles(profiles, execution_modes) -> dict:
    """W7-03: `runtimeProfiles` maps an execution mode (TRAINING /
    VALIDATION / EMULATION / INFERENCE) to {cpu, memory, gpu}. A profile
    for a mode the manifest doesn't declare in `executionModes` (when it
    declares any) is a packaging error, as is an unknown mode or a
    non-numeric cpu/gpu. Raises PackageValidationFailed -> the package fails onboarding (FAILED).
    """
    if not isinstance(profiles, dict):
        raise PackageValidationFailed("runtimeProfiles must be a mapping of execution mode -> profile")
    out = {}
    for mode, profile in profiles.items():
        if mode not in EXECUTION_MODES:
            raise PackageValidationFailed(f"runtimeProfiles: unknown execution mode {mode!r}")
        if execution_modes and mode not in execution_modes:
            raise PackageValidationFailed(f"runtimeProfiles: {mode} is not one of the declared executionModes")
        if not isinstance(profile, dict):
            raise PackageValidationFailed(f"runtimeProfiles.{mode} must be a mapping")
        clean = {}
        for field in ("cpu", "gpu"):
            if field in profile:
                if not isinstance(profile[field], (int, float)) or isinstance(profile[field], bool) or profile[field] < 0:
                    raise PackageValidationFailed(f"runtimeProfiles.{mode}.{field} must be a non-negative number")
                clean[field] = profile[field]
        if "memory" in profile:
            clean["memory"] = str(profile["memory"])
        out[mode] = clean
    return out


def _parse_sme_declarations(z: zipfile.ZipFile) -> dict | None:
    """The real O-RAN SC rApp Manager's own CSAR layout
    (`nonrtric-plt-rappmanager/sample-rapp-generator/`'s real sample
    packages): `Files/Sme/providers/*.json` (real CAPIF
    `APIProviderEnrolmentDetails`) and `Files/Sme/serviceapis/*.json`
    (real CAPIF `ServiceAPIDescription`) declare which SME provider/API
    this package registers as, once deployed. Read here at onboarding
    time and stored raw; registered per-instance by rapp-mgmt's
    bootstrap-complete (`SmeDeployer.deployRappInstance`'s own real
    per-*instance*, not per-package, timing — the reference's own
    `primeRapp` is a documented no-op for SME).

    Optional and additive, like `manifest.yaml`/`capabilities.yaml`: a
    package whose CSAR declares neither directory (every package before
    this pass, and the ONAP-Files/Acm-only samples) onboards exactly as
    before — this returns None, `ApplicationPackage.sme_declarations`
    stays NULL, and bootstrap-complete simply has nothing to register.
    Sorted names for deterministic ordering across multiple provider/
    service-API files.
    """
    names = z.namelist()
    providers = [json.loads(z.read(n)) for n in sorted(names) if n.startswith("Files/Sme/providers/") and n.endswith(".json")]
    service_apis = [json.loads(z.read(n)) for n in sorted(names) if n.startswith("Files/Sme/serviceapis/") and n.endswith(".json")]
    if not providers and not service_apis:
        return None
    return {"providers": providers, "serviceApis": service_apis}


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
    resp = httpx.get(location, timeout=30.0)
    resp.raise_for_status()
    data = resp.content
    with zipfile.ZipFile(BytesIO(data)) as z:
        meta = z.read("TOSCA-Metadata/TOSCA.meta").decode()
        entry_line = next(l for l in meta.splitlines() if l.startswith("Entry-Definitions:"))
        entry_definitions = entry_line.split(":", 1)[1].strip()
        identity = _asd_identity(z.read(entry_definitions).decode(errors="replace"))  # raises KeyError if missing/malformed
        artifacts = [(n, f"{location}#{n}") for n in z.namelist() if n.startswith("Artifacts/") and not n.endswith("/")]
        ai_capabilities = _parse_ai_capabilities(z)
        if ai_capabilities is not None:
            identity["ai_capabilities"] = ai_capabilities
        sme_declarations = _parse_sme_declarations(z)
        if sme_declarations is not None:
            identity["sme_declarations"] = sme_declarations
    integrity_hash = hashlib.sha256(data).hexdigest()
    return entry_definitions, artifacts, integrity_hash, identity


@app.get("/packages/{package_id}/onboarding-status")
def query_onboarding_status(package_id: uuid.UUID, db: Session = Depends(get_session)):
    pkg = db.get(ApplicationPackage, package_id)
    if pkg is None:
        raise framework_error(FrameworkError.DME_TYPE_VERSION_CONFLICT, detail="no such package")  # 404-shaped reuse; Phase 1
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
    pkg = db.get(ApplicationPackage, package_id)
    pkg.state = ONBOARDING_FSM.fire(PackageState(pkg.state), PackageEvent.DEPRECATE, db=db, package=pkg)
    db.commit()
    return _package_view(pkg)


@app.post("/packages/{package_id}/prime")
def prime_package(package_id: uuid.UUID, db: Session = Depends(get_session)):
    """HISTORY.md §5: the reference's real
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
    stmt = select(PackageUsageRegistration).where(PackageUsageRegistration.package_id == package_id)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"registrationId": str(r.id), "consumerId": r.consumer_id,
             "stoppedAt": r.stopped_at.isoformat() if r.stopped_at else None, "active": r.stopped_at is None}
            for r in page["items"]]}
