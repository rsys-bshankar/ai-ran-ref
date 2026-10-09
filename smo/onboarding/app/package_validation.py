"""Package validation, the part of Onboarding that needs neither the database nor the web framework.

`validate_package_bytes` is what `POST /packages` runs on a fetched CSAR (`app/main.py` fetches it, then calls this) and what the offline validator of
the rApp conformance pack runs on a file (`conformance/rapp`, PR-RAPP-3.1), so a developer sees the verdict Onboarding will give. It raises
`PackageValidationFailed` (with the reason) or one of the parse errors in `PARSE_FAILURES`; the caller treats all of them as "this package fails".

PR-RAPP-1: when a trust store is given the package signature is checked (smo_shared/csar_signing.py), and `require_signed` refuses a package that has none.
"""

import hashlib
import json
import re
import zipfile
from io import BytesIO
from typing import Any

import yaml

from smo_shared import csar_signing
from smo_shared.operator_ui import OperatorUiInvalid, validate_operator_ui


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



# What counts as "this package fails to validate" besides PackageValidationFailed itself: a zip that is not one, a missing entry, malformed YAML or JSON in a
# package file. (Onboarding adds the location being unreachable and NFO refusing the descriptor.)
PARSE_FAILURES = (zipfile.BadZipFile, KeyError, FileNotFoundError, PackageValidationFailed, yaml.YAMLError, json.JSONDecodeError, UnicodeDecodeError)


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
        if not isinstance(manifest, dict):
            raise PackageValidationFailed("manifest.yaml must be a mapping")
        rapp_manifest = manifest.get("rappManifest") or {}
        if not isinstance(rapp_manifest, dict):
            raise PackageValidationFailed("manifest.yaml: rappManifest must be a mapping")
        result["manifestVersion"] = rapp_manifest.get("manifestVersion")
        result["aiRuntimeSdkVersion"] = rapp_manifest.get("aiRuntimeSdkVersion")
        # Wave 7 (HISTORY.md W7-03): the AI-runtime part of
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
        # AI-10.1: what this rApp may do on the network, declared by the package and enforced by the platform (`limits.configJobsPerHour`).
        limits = rapp_manifest.get("limits", manifest.get("limits"))
        if limits is not None:
            result["limits"] = _validate_limits(limits)
        # GUI-8.2: the operator page the rApp declares (docs/adr/0004-operator-ui-declaration.md). Optional; absent -> the key is absent.
        operator_ui = rapp_manifest.get("operatorUi", manifest.get("operatorUi"))
        if operator_ui is not None:
            try:
                result["operatorUi"] = validate_operator_ui(operator_ui)
            except OperatorUiInvalid as exc:
                raise PackageValidationFailed(str(exc)) from exc
    if "capabilities.yaml" in names:
        parsed = yaml.safe_load(z.read("capabilities.yaml")) or {}
        if not isinstance(parsed, dict):
            raise PackageValidationFailed("capabilities.yaml must be a mapping")
        caps = parsed.get("capabilities") or {}
        if not isinstance(caps, dict):
            raise PackageValidationFailed("capabilities.yaml: capabilities must be a mapping")
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


# name -> (whole numbers only?, largest value). Every limit is positive. Enforced by RAN NF OAM (AI-10.2, AI-10.3).
LIMIT_SPECS = {"configJobsPerHour": (True, 100_000), "maxElementsPerJob": (True, 10_000), "maxChangePercent": (False, 10_000)}


def _validate_limits(limits) -> dict:
    """AI-10.1/10.3: `limits` maps a limit name to a positive number. An unknown name, a value that is not a number (a bool is not one), one out of
    range, or a fraction where a whole number is needed is a packaging error (the package fails onboarding): a limit the platform cannot enforce
    must not be silently ignored. `configJobsPerHour`: how many CM write jobs the rApp may start in any hour; `maxElementsPerJob`: how many managed
    elements one job may touch (blast radius); `maxChangePercent`: how far, in percent of its current value, a numeric value may move in one write
    (magnitude)."""
    if not isinstance(limits, dict):
        raise PackageValidationFailed("limits must be a mapping of limit name -> number")
    out = {}
    for name, value in limits.items():
        if name not in LIMIT_SPECS:
            raise PackageValidationFailed(f"limits: unknown limit {name!r} (known: {', '.join(sorted(LIMIT_SPECS))})")
        whole, largest = LIMIT_SPECS[name]
        number = isinstance(value, int) if whole else isinstance(value, (int, float))
        if not number or isinstance(value, bool) or not 0 < value <= largest or value != value:
            raise PackageValidationFailed(f"limits.{name} must be a {'whole number' if whole else 'number'} above 0 and at most {largest}")
        out[name] = value
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



def verify_signature(data: bytes, trust: "csar_signing.TrustStore | None", require_signed: bool) -> dict[str, Any]:
    """PR-RAPP-1.4/1.5. Returns what is known about the signature: {} when nothing was asked for (no trust store, signing not required: the package is taken
    as before), else `{"signature_verified": True, "signed_by": publisher}`. Raises PackageValidationFailed with a clear reason when:
      - signing is required and there is no trust store (a misconfiguration, so every package is refused rather than every package accepted);
      - the package carries a signature or digest list and it does not verify (tampered, added or removed file, wrong key, unknown publisher);
      - signing is required and the package has none.
    With a trust store and `require_signed` off, an unsigned package is accepted but not marked verified."""
    if trust is None:
        if require_signed:
            raise PackageValidationFailed("signed packages are required (ONBOARDING_REQUIRE_SIGNED_PACKAGES) but no trusted publisher keys are configured (ONBOARDING_TRUST_STORE)")
        return {}
    if not require_signed and not _carries_signing_entries(data):
        return {"signature_verified": False}
    try:
        verification = csar_signing.verify_csar(data, trust)
    except csar_signing.SignatureError as exc:
        raise PackageValidationFailed(f"package signature: {exc}") from exc
    return {"signature_verified": True, "signed_by": verification.publisher}


def _carries_signing_entries(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            return csar_signing.is_signed(z.namelist())
    except zipfile.BadZipFile:
        return False


def validate_package_bytes(data: bytes, location: str, *, trust: "csar_signing.TrustStore | None" = None,
                           require_signed: bool = False) -> tuple[str, list[tuple[str, str]], str, dict]:
    """Open TOSCA-Metadata/Definitions/Artifacts of a fetched CSAR, per Onboarding LLD section 1: (entry definitions, artifacts, integrity hash, identity).
    `location` is only used to give each artifact its access URL. The signature is checked first (a tampered package is not parsed); see verify_signature."""
    signature = verify_signature(data, trust, require_signed)
    with zipfile.ZipFile(BytesIO(data)) as z:
        meta = z.read("TOSCA-Metadata/TOSCA.meta").decode()
        entry_line = next(l for l in meta.splitlines() if l.startswith("Entry-Definitions:"))
        entry_definitions = entry_line.split(":", 1)[1].strip()
        identity: dict[str, Any] = _asd_identity(z.read(entry_definitions).decode(errors="replace"))  # raises KeyError if missing/malformed
        artifacts = [(n, f"{location}#{n}") for n in z.namelist() if n.startswith("Artifacts/") and not n.endswith("/")]
        ai_capabilities = _parse_ai_capabilities(z)
        if ai_capabilities is not None:
            identity["ai_capabilities"] = ai_capabilities
        sme_declarations = _parse_sme_declarations(z)
        if sme_declarations is not None:
            identity["sme_declarations"] = sme_declarations
    identity.update(signature)
    integrity_hash = hashlib.sha256(data).hexdigest()
    return entry_definitions, artifacts, integrity_hash, identity
