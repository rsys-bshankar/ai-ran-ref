"""The offline checks: a rApp package (a CSAR) against what Onboarding will do with it. Nothing is running; nothing is fetched.

PK-V is the verdict: it calls `onboarding/app/package_validation.py`, the code `POST /packages` runs, so a package that passes it onboards (the NFO call that
follows is the one thing it does not exercise). The others take the same parsers apart so a developer is told which file is wrong, and add advice Onboarding does
not give (a warning is not a refusal).
"""

import importlib.util
import io
import re
import sys
import zipfile
from pathlib import Path

from smo_shared import csar_signing

from .kit import PACKAGE, Fail, PackageContext, Skip, Warn, check

ONBOARDING_VALIDATION = Path(__file__).resolve().parents[2] / "onboarding" / "app" / "package_validation.py"
SDK_NAMESPACES = {"data", "analytics", "models", "lifecycle", "intent", "platform"}
ASD_IDENTITY = ("application_name", "application_version", "provider")
NOT_SHIPPED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git", "tests", "node_modules"}
SECRET_NAMES = re.compile(r"(^|/)(\.env|id_rsa|id_ed25519|.*\.(pem|key|p12|pfx|jks))$", re.I)
PRIVATE_KEY = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|ed25519-seed:[A-Za-z0-9+/]{43}=")
MAX_SCANNED_BYTES = 1_000_000


def onboarding():
    """Onboarding's own validation module, loaded from its file (every module's package is called `app`, so it cannot be imported by name)."""
    module = sys.modules.get("rapp_onboarding_package_validation")
    if module is None:
        spec = importlib.util.spec_from_file_location("rapp_onboarding_package_validation", ONBOARDING_VALIDATION)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


def _open(ctx: PackageContext) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(ctx.data))
    except zipfile.BadZipFile:
        raise Skip("the package is not a readable zip file (PK-1)") from None


def _only(z: zipfile.ZipFile, *names: str) -> zipfile.ZipFile:
    """A zip holding just these entries of `z` that exist: the Onboarding parsers read a whole package, a check wants one file's verdict."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for name in names:
            if name in z.namelist():
                target.writestr(name, z.read(name))
    return zipfile.ZipFile(io.BytesIO(out.getvalue()))


def _asd_definitions(z: zipfile.ZipFile) -> tuple[str, str]:
    try:
        meta = z.read("TOSCA-Metadata/TOSCA.meta").decode()
    except KeyError:
        raise Fail("TOSCA-Metadata/TOSCA.meta is missing") from None
    entry = next((line.split(":", 1)[1].strip() for line in meta.splitlines() if line.startswith("Entry-Definitions:")), None)
    if not entry:
        raise Fail("TOSCA-Metadata/TOSCA.meta has no Entry-Definitions: line")
    try:
        return entry, z.read(entry).decode(errors="replace")
    except KeyError:
        raise Fail(f"the entry definitions file {entry} named by TOSCA.meta is not in the package") from None


@check("PK-1", "STRUCTURE", "the package is a .csar zip file with no entry listed twice", PACKAGE)
def pk_zip(ctx: PackageContext) -> None:
    if not ctx.name.endswith(".csar"):
        raise Fail(f"the file name {ctx.name!r} does not end with .csar (Onboarding refuses a location that does not)")
    try:
        z = zipfile.ZipFile(io.BytesIO(ctx.data))
    except zipfile.BadZipFile:
        raise Fail("not a zip file") from None
    names = [i.filename for i in z.infolist() if not i.is_dir()]
    if len(set(names)) != len(names):
        raise Fail("an entry is listed twice, so which one a reader sees depends on the reader")
    bad = z.testzip()
    if bad is not None:
        raise Fail(f"entry {bad} is corrupt (its CRC does not match)")


@check("PK-2", "STRUCTURE", "TOSCA-Metadata/TOSCA.meta names an entry definitions file that is in the package", PACKAGE)
def pk_tosca(ctx: PackageContext) -> None:
    _asd_definitions(_open(ctx))


@check("PK-3", "STRUCTURE", "the ASD names the rApp: application_name, application_version and provider are set", PACKAGE)
def pk_identity(ctx: PackageContext) -> None:
    z = _open(ctx)
    try:
        _, definitions = _asd_definitions(z)
    except Fail:
        raise Skip("the entry definitions cannot be read (PK-2)") from None
    found = onboarding()._asd_identity(definitions)
    missing = [k for k, field in (("application_name", "name"), ("application_version", "version"), ("provider", "vendor")) if field not in found]
    if missing:
        raise Warn(f"the ASD does not set {', '.join(missing)}: Onboarding accepts that, and lists the package as 'unresolved-until-validated 0.0.0' or without a vendor")


@check("PK-4", "MANIFEST", "manifest.yaml, when present, is valid: runtimeProfiles, limits and operatorUi pass Onboarding's rules", PACKAGE)
def pk_manifest(ctx: PackageContext) -> None:
    z = _open(ctx)
    if "manifest.yaml" not in z.namelist():
        raise Skip("the package has no manifest.yaml (optional)")
    try:
        result = onboarding()._parse_ai_capabilities(_only(z, "manifest.yaml"))
    except onboarding().PARSE_FAILURES as exc:
        raise Fail(f"manifest.yaml: {exc if isinstance(exc, onboarding().PackageValidationFailed) else type(exc).__name__ + ': ' + str(exc)[:200]}") from None
    ctx.cache["manifest"] = result or {}


@check("PK-5", "MANIFEST", "the manifest declares its execution modes and a runtime profile for each, so the platform can size its runtimes", PACKAGE)
def pk_profiles(ctx: PackageContext) -> None:
    manifest = ctx.cache.get("manifest")
    if manifest is None:
        raise Skip("no valid manifest.yaml (PK-4)")
    modes, profiles = manifest.get("executionModes"), manifest.get("runtimeProfiles")
    problems = []
    if not modes:
        problems.append("executionModes is not declared")
    if not profiles:
        problems.append("runtimeProfiles is not declared: AIMgF cannot size the runtime, and no CPU or memory request or limit reaches the NFO descriptor")
    elif modes:
        problems += [f"{m} is an execution mode with no runtime profile" for m in modes if m not in profiles]
    if problems:
        raise Warn("; ".join(problems))


@check("PK-6", "MANIFEST", "capabilities.yaml, when present, parses and names SDK namespaces", PACKAGE)
def pk_capabilities(ctx: PackageContext) -> None:
    z = _open(ctx)
    if "capabilities.yaml" not in z.namelist():
        raise Skip("the package has no capabilities.yaml (optional)")
    try:
        result = onboarding()._parse_ai_capabilities(_only(z, "capabilities.yaml")) or {}
    except onboarding().PARSE_FAILURES as exc:
        raise Fail(f"capabilities.yaml: {exc if isinstance(exc, onboarding().PackageValidationFailed) else type(exc).__name__ + ': ' + str(exc)[:200]}") from None
    unknown = sorted({str(c.get("namespace")) for key in ("consumes", "provides") for c in result.get(key, []) if isinstance(c, dict)} - SDK_NAMESPACES)
    malformed = [c for key in ("consumes", "provides") for c in result.get(key, []) if not isinstance(c, dict) or "namespace" not in c]
    if malformed:
        raise Warn(f"{len(malformed)} consumes/provides entries are not {{namespace, description}} mappings")
    if unknown:
        raise Warn(f"namespaces {unknown} are not one of the six SDK namespaces ({sorted(SDK_NAMESPACES)}); Onboarding stores them without checking")


@check("PK-7", "MANIFEST", "Files/Sme declarations, when present, are valid JSON", PACKAGE)
def pk_sme(ctx: PackageContext) -> None:
    z = _open(ctx)
    if not any(n.startswith("Files/Sme/") for n in z.namelist()):
        raise Skip("the package declares no SME providers or service APIs")
    try:
        onboarding()._parse_sme_declarations(z)
    except onboarding().PARSE_FAILURES as exc:
        raise Fail(f"Files/Sme: {type(exc).__name__}: {str(exc)[:200]}") from None


@check("PK-8", "HYGIENE", "the package ships no test suite, cache, environment file or key file", PACKAGE)
def pk_hygiene(ctx: PackageContext) -> None:
    z = _open(ctx)
    names = [i.filename for i in z.infolist() if not i.is_dir()]
    stray = [n for n in names if NOT_SHIPPED_PARTS & set(n.split("/")[:-1]) or SECRET_NAMES.search(n)]
    if stray:
        raise Warn(f"{len(stray)} files that should not be shipped, e.g. {', '.join(stray[:3])}")


@check("PK-9", "HYGIENE", "no file in the package holds a private key", PACKAGE)
def pk_no_private_key(ctx: PackageContext) -> None:
    z = _open(ctx)
    holders = [i.filename for i in z.infolist() if not i.is_dir() and i.file_size <= MAX_SCANNED_BYTES and PRIVATE_KEY.search(z.read(i.filename))]
    if holders:
        raise Fail(f"{', '.join(holders[:3])} holds a private key: anyone who gets the package gets the key")


@check("PK-S", "SIGNATURE", "the package is signed by a publisher in the trust store, and nothing was changed after signing", PACKAGE)
def pk_signature(ctx: PackageContext) -> None:
    if ctx.trust is None:
        if ctx.require_signed:
            raise Fail("signed packages are required but no trust store was given (--trust): this is what Onboarding does with ONBOARDING_REQUIRE_SIGNED_PACKAGES and no ONBOARDING_TRUST_STORE")
        raise Skip("no --trust given: the signature is not checked")
    try:
        verified = csar_signing.verify_csar(ctx.data, ctx.trust)
    except csar_signing.SignatureError as exc:
        if exc.code == "unsigned" and not ctx.require_signed and not csar_signing.is_signed(_open(ctx).namelist()):
            raise Warn("the package is not signed (accepted by Onboarding unless ONBOARDING_REQUIRE_SIGNED_PACKAGES is on)") from None
        raise Fail(str(exc)) from None
    ctx.cache["publisher"] = verified.publisher


@check("PK-V", "ONBOARDING", "Onboarding's validation accepts the package (the same code POST /packages runs)", PACKAGE)
def pk_onboarding(ctx: PackageContext) -> None:
    module = onboarding()
    try:
        entry, artifacts, integrity_hash, identity = module.validate_package_bytes(ctx.data, ctx.name, trust=ctx.trust, require_signed=ctx.require_signed)
    except module.PARSE_FAILURES as exc:
        reason = str(exc) if isinstance(exc, module.PackageValidationFailed) else f"{type(exc).__name__}: {str(exc)[:200]}"
        raise Fail(f"Onboarding would mark this package FAILED: {reason}") from None
    ctx.cache["identity"] = identity
