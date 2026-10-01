"""What it takes to bring an RAppInstance's resources up, and to release them.

CreateInstance and UpgradeInstance's replacement row go through the same
`provision_instance` (OI-2-upgrade-completeness): package-state check, a
fresh `oauth_client_id` identity, NFO Instantiate and the package usage
registration. TerminateInstance, an upgrade commit (for the old row) and an
upgrade rollback or timeout (for the replacement) all release through the
same `release_instance_resources`: NFO Terminate and usage/stop, best-effort
but recorded on the row (`last_teardown`) — OI-2-terminate-workload.

The DME/SME deregistration and credential revocation half of a teardown is
the FSM's own transition action (statemachine.py's `_terminate_side_effects`),
not repeated here.
"""

import datetime
import uuid

import httpx
from sqlalchemy.orm import Session

from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client

from .models import RAppInstance
from .statemachine import InstanceState

# A package an instance may be created from: validated (AVAILABLE), or
# validated and primed (PRIMED). Priming is optional; depriming is refused
# while an instance of the package is running (onboarding's DEPRIME guard).
DEPLOYABLE_PACKAGE_STATES = ("AVAILABLE", "PRIMED")


def provision_instance(db: Session, package_id: uuid.UUID, configuration: dict | None,
                       autonomy_mode: str, region_scope: dict | None) -> RAppInstance:
    """Requires a validated package: AVAILABLE, or PRIMED (AVAILABLE plus
    pre-provisioned resources; D-SEC-RAPP-1) — 404 PACKAGE_NOT_FOUND for an
    unknown package, 409 MODEL_NOT_CERTIFIED for any other state. NFO handoff
    per Onboarding/rApp Mgmt LLD section 5: reads the package's TOSCA service
    template and issues NFO.Instantiate. The new row is flushed, not committed.
    """
    r1 = R1Client()
    pkg_resp = r1.get(f"/onboarding/packages/{package_id}/onboarding-status")
    if pkg_resp.status_code == 404:
        raise framework_error(FrameworkError.PACKAGE_NOT_FOUND, detail=f"no such package {package_id}")
    if pkg_resp.status_code != 200 or pkg_resp.json().get("state") not in DEPLOYABLE_PACKAGE_STATES:
        raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED, detail="package is not AVAILABLE or PRIMED")
    nf_deployment_descriptor_id = pkg_resp.json().get("nfDeploymentDescriptorId")
    if not nf_deployment_descriptor_id:
        # Every package that reaches AVAILABLE has one — OnboardPackage's own
        # validation pipeline creates it via NFO's CreateDescriptor (NFO+FOCOM
        # LLD section 2). Missing here means an inconsistent record, not a
        # normal refusal.
        raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED, detail="package has no nfDeploymentDescriptorId")

    inst = RAppInstance(package_id=package_id, configuration=configuration, state=InstanceState.DEPLOYING,
                        oauth_client_id=str(uuid.uuid4()), autonomy_mode=autonomy_mode, region_scope=region_scope)
    db.add(inst)
    db.flush()

    nfo_resp = r1.post("/nfo/deployments", json={
        "nfDeploymentDescriptorId": nf_deployment_descriptor_id,  # the real descriptor, per section 5
        "name": f"rapp-instance-{inst.instance_id}",  # NFO's own duplication guard (HISTORY.md §5) needs a real name
        "requiredResourceTypeId": (configuration or {}).get("requiredResourceTypeId"),
    })
    # NFO Instantiate answers 202 Accepted (nfo/app/main.py) — a 200-only check
    # dropped every real workloadRef. workload_ref IS the NFO nfDeploymentId,
    # which TERMINATE later hands back to NFO's DELETE /deployments/{id}.
    inst.workload_ref = nfo_resp.json().get("nfDeploymentId") if nfo_resp.status_code in (200, 202) else None

    usage_resp = r1.post(f"/onboarding/packages/{package_id}/usage/start", params={"consumer_id": str(inst.instance_id)})
    if usage_resp.status_code == 200:
        inst.package_usage_registration_id = uuid.UUID(usage_resp.json()["registrationId"])
    return inst


def _step(call) -> str:
    try:
        resp = call()
    except httpx.HTTPError as exc:
        return f"FAILED: {type(exc).__name__}"
    return "DONE" if resp.status_code in (200, 202, 204, 404) else f"FAILED: HTTP {resp.status_code}"


def release_instance_resources(inst: RAppInstance, reason: str) -> dict:
    """NFO Terminate for the instance's workload (`workload_ref`, the NFO
    nfDeploymentId) and usage/stop for its package usage registration —
    each best-effort (an unreachable NFO or Onboarding never blocks the
    teardown itself) but recorded: the returned outcome is what callers
    store in `last_teardown`. A 404 counts as done (already gone). On
    success the usage registration id is cleared, since the registration is
    closed; `workload_ref` is kept as the record of which deployment ran.
    """
    r1 = R1Client()
    if inst.workload_ref:
        nfo = _step(lambda: r1.delete(f"/nfo/deployments/{inst.workload_ref}"))
    else:
        nfo = "SKIPPED: no workloadRef"
    if inst.package_usage_registration_id is not None:
        usage = _step(lambda: r1.post(f"/onboarding/packages/{inst.package_id}/usage/{inst.package_usage_registration_id}/stop"))
        if usage == "DONE":
            inst.package_usage_registration_id = None
    else:
        usage = "SKIPPED: no usage registration"
    return {"instanceId": str(inst.instance_id), "reason": reason, "nfoTerminate": nfo, "usageStop": usage,
            "at": datetime.datetime.now(datetime.UTC).isoformat()}


def _sme_provider_registration_body(provider: dict, apf_id: str) -> dict:
    """A CSAR's own `Files/Sme/providers/*.json` may already be this
    build's own real `ProviderRegistrationRequest` body
    (`samples/hello-world-rapp/`'s own established convention —
    `apfId`/`providerDomainInfo`) or the real external CAPIF
    `APIProviderEnrolmentDetails` shape (`apiProvDomInfo`/`apiProvFuncs`,
    grounded against `nonrtric-plt-rappmanager`'s own real sample
    packages) — `providerDomainInfo` reads whichever key is actually
    present. `apfId` is always this instance's own `oauth_client_id`
    (the "one instance, one identity" convention already used for its
    DME producer_id), never whatever a CSAR's own JSON declares — two
    instances of the same package must never collide on one shared,
    hardcoded apfId.
    """
    return {"apfId": apf_id, "providerDomainInfo": provider.get("providerDomainInfo") or provider.get("apiProvDomInfo")}


def _sme_service_registration_body(service_api: dict, apf_id: str) -> dict:
    """A CSAR's own `Files/Sme/serviceapis/*.json` may already be this
    build's own real `ServiceRegistration` body
    (`samples/hello-world-rapp/`'s own established convention —
    `serviceName`/`endpoint`/`version`/`moduleScope`/... directly) or
    the real external CAPIF `ServiceAPIDescription` shape
    (`apiName`/`aefProfiles` with nested `versions`/
    `interfaceDescriptions`, grounded against
    `nonrtric-plt-rappmanager`'s own real sample packages) needing a
    real field-by-field mapping — `serviceName`'s presence distinguishes
    the two. `producerId` is always this instance's own `oauth_client_id`
    either way, never whatever the CSAR's own JSON declares, same
    reasoning as `_sme_provider_registration_body` above. For the real
    CAPIF shape, serviceName/endpoint/version/moduleScope are this
    build's own required fields with no CAPIF equivalent, so a fixed,
    documented default fills each one where the real sample has nothing
    to say (its own `apiVersion` is even an empty string).
    `aefProfiles` passes through byte-for-byte in both cases: this
    build's own field is an untyped `list[dict]`, so neither shape needs
    lossy reshaping there.

    `serviceName` is always suffixed with this instance's own apfId —
    caught by running this against this repo's own real, already-shipped
    demo CSAR (`samples/hello-world-rapp/`), not assumed: SME's own
    `register_service` treats `serviceName` as globally unique across
    every producer (`sme/app/main.py`'s own documented Section 2.3
    rule), so a CSAR's fixed, package-level `serviceName` would
    otherwise collide the moment a second instance of the same package
    tries to register the identical name under its own, different
    apfId — a real `SERVICE_NAME_CONFLICT`, not a hypothetical one.
    """
    if "serviceName" in service_api:
        return {**service_api, "producerId": apf_id, "serviceName": f"{service_api['serviceName']}-{apf_id}"}
    first_profile = (service_api.get("aefProfiles") or [{}])[0]
    first_version = (first_profile.get("versions") or [{}])[0]
    first_iface = (first_profile.get("interfaceDescriptions") or [{}])[0]
    endpoint = f"http://{first_iface['ipv4Addr']}:{first_iface['port']}" if first_iface.get("ipv4Addr") else "http://unknown"
    return {
        "serviceName": f"{service_api.get('apiName', 'unnamed-service')}-{apf_id}", "producerId": apf_id,
        "endpoint": endpoint, "version": first_version.get("apiVersion") or "1.0",
        "moduleScope": "rapp", "aefProfiles": service_api.get("aefProfiles", []),
    }


def register_sme_declarations(inst: RAppInstance) -> None:
    """HISTORY.md §7's Onboarding/rApp Mgmt finding 3 (SME auto-
    registration): real O-RAN SC rApp Manager behavior
    (SmeDeployer.deployRappInstance) registers a package's CSAR-bundled
    Files/Sme/providers/ + Files/Sme/serviceapis/ declarations with SME
    per *instance*, at deploy time — not at onboarding or priming (the
    reference's own primeRapp is a documented no-op for SME). Runs at
    bootstrap-complete, and for an upgrade replacement that is committed
    without having called bootstrap-complete itself (upgrade.py).

    Uses this instance's own oauth_client_id as its SME apfId, the same
    "one instance, one identity" convention already used for its DME
    producer_id (statemachine.py's _reconsider_dme_registration).
    Best-effort: an unreachable SME must never block bootstrap from
    completing, the same "unreachable callback never fails the primary
    operation" precedent used throughout this build.
    """
    r1 = R1Client()
    pkg_resp = r1.get(f"/onboarding/packages/{inst.package_id}/onboarding-status")
    declarations = pkg_resp.json().get("smeDeclarations") if pkg_resp.status_code == 200 else None
    if not declarations:
        return
    apf_id = inst.oauth_client_id
    try:
        for provider in declarations.get("providers", []):
            r1.post("/sme/provider-registrations", json=_sme_provider_registration_body(provider, apf_id))
        service_ids = []
        for service_api in declarations.get("serviceApis", []):
            resp = r1.post(f"/sme/published-apis/v1/{apf_id}/service-apis", json=_sme_service_registration_body(service_api, apf_id))
            if resp.status_code == 201:
                service_ids.append(resp.json()["serviceId"])
        inst.sme_service_ids = service_ids or None
    except httpx.HTTPError:
        pass
