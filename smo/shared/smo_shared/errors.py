"""RFC 7807 ProblemDetails — the error model every R1 service group defers
to (CAPIF/TS 29.222 for SME, generic TS 29.500/29.501 for DME/AI-ML-Workflow,
TS 28.532/28.111 for CM/FM), except A1 policy management, which owns its own
application error table (A1 Related LLD section 1.3).
"""

from fastapi import HTTPException
from pydantic import BaseModel


class ProblemDetails(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None


def problem(status: int, title: str, detail: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail=ProblemDetails(title=title, status=status, detail=detail).model_dump(),
    )


# Framework-level errors introduced across the LLD passes — never inherited
# from an underlying protocol, these are this SMO's own.
class FrameworkError:
    # RAN NF OAM LLD section 7
    ENDPOINT_UNREACHABLE = ("ENDPOINT_UNREACHABLE", 503)
    SCHEMA_VALIDATION_FAILED = ("SCHEMA_VALIDATION_FAILED", 422)
    MSAC_ACCESS_DENIED = ("MSAC_ACCESS_DENIED", 403)
    PROTOCOL_NOT_SUPPORTED = ("PROTOCOL_NOT_SUPPORTED", 409)
    # AI/ML Workflow LLD section 7
    MODEL_NOT_CERTIFIED = ("MODEL_NOT_CERTIFIED", 409)
    NODE_GROUP_NOT_CLEARED = ("NODE_GROUP_NOT_CLEARED", 403)
    COORDINATION_GROUP_MISMATCH = ("COORDINATION_GROUP_MISMATCH", 422)
    COORDINATION_GROUP_TOO_SMALL = ("COORDINATION_GROUP_TOO_SMALL", 422)
    INFERENCE_MODEL_NOT_ACTIVE = ("INFERENCE_MODEL_NOT_ACTIVE", 409)
    MODEL_ALREADY_REGISTERED = ("MODEL_ALREADY_REGISTERED", 409)
    MODEL_IDENTITY_IMMUTABLE = ("MODEL_IDENTITY_IMMUTABLE", 400)
    MODEL_ACCESS_DENIED = ("MODEL_ACCESS_DENIED", 403)
    MODEL_EXPIRED = ("MODEL_EXPIRED", 410)
    FEATURE_GROUP_NAME_INVALID = ("FEATURE_GROUP_NAME_INVALID", 400)
    FEATURE_GROUP_ALREADY_REGISTERED = ("FEATURE_GROUP_ALREADY_REGISTERED", 409)
    # OI-5-aiml-featuregroup-dme: an enable_dme feature group without a DME
    # type, or whose DME data job DME refused; an unknown group.
    FEATURE_GROUP_DME_JOB_REFUSED = ("FEATURE_GROUP_DME_JOB_REFUSED", 422)
    FEATURE_GROUP_NOT_FOUND = ("FEATURE_GROUP_NOT_FOUND", 404)
    # Wave 2 (AI Platform Service Decomposition) — AIMgF's own two real
    # state machines, docs/ARCHITECTURE.md (AIMgF)
    LIFECYCLE_ILLEGAL_TRANSITION = ("LIFECYCLE_ILLEGAL_TRANSITION", 409)
    GOVERNANCE_DECIDER_REQUIRED = ("GOVERNANCE_DECIDER_REQUIRED", 422)
    # Wave 3 (AI Platform Service Decomposition) — DME's revised dual
    # data-plane + O1-mediation role, docs/ARCHITECTURE.md (DME)
    DIGITAL_TWIN_INFERENCE_NOT_ELIGIBLE = ("DIGITAL_TWIN_INFERENCE_NOT_ELIGIBLE", 422)
    DME_ARTIFACT_NOT_FOUND = ("DME_ARTIFACT_NOT_FOUND", 422)
    # Wave 3 — AIMgF's TrainingJob suspend/resume, HISTORY.md §7's
    # AI/ML Workflow section item 6
    TRAINING_JOB_ILLEGAL_TRANSITION = ("TRAINING_JOB_ILLEGAL_TRANSITION", 409)
    # Wave 3 — Intent Service's consumer-side RMIH selection,
    # docs/ARCHITECTURE.md's Intent Service Wave 3 resolution
    RMIH_CAPABILITY_MISMATCH = ("RMIH_CAPABILITY_MISMATCH", 422)
    # A1 Related LLD section 1.3
    POLICY_TYPE_NOT_SUPPORTED = ("POLICY_TYPE_NOT_SUPPORTED", 422)
    POLICY_OBJECT_SCHEMA_INVALID = ("POLICY_OBJECT_SCHEMA_INVALID", 422)
    SUBSCRIPTION_SCOPE_CONFLICT = ("SUBSCRIPTION_SCOPE_CONFLICT", 422)
    # Foundational Platform LLD section 5
    SERVICE_NAME_CONFLICT = ("SERVICE_NAME_CONFLICT", 409)
    DME_TYPE_VERSION_CONFLICT = ("DME_TYPE_VERSION_CONFLICT", 409)
    # HISTORY.md §7 — DME vs. the real ICS API, Producer/Type conflation
    # finding, closed: a real ICS deleteInfoType 409 ("has one or several
    # active producers"), and its own 404 for an unknown type — the type
    # itself never had a dedicated not-found code before since it was
    # never independently addressable from its producer.
    DME_TYPE_NOT_FOUND = ("DME_TYPE_NOT_FOUND", 404)
    DME_TYPE_HAS_ACTIVE_PRODUCERS = ("DME_TYPE_HAS_ACTIVE_PRODUCERS", 409)
    DELIVERY_METHOD_NOT_OFFERED = ("DELIVERY_METHOD_NOT_OFFERED", 409)
    DATA_JOB_TARGET_IMMUTABLE = ("DATA_JOB_TARGET_IMMUTABLE", 400)
    APF_NOT_REGISTERED = ("APF_NOT_REGISTERED", 403)
    # SO/SA SMOS LLD section 2.1; OI-1-sa-rollback: nothing to roll back to —
    # a rApp instance with no un-rolled-back upgrade in its version history, or
    # an SA SMOS monitor whose target keeps no version history at all.
    ROLLBACK_HISTORY_UNAVAILABLE = ("ROLLBACK_HISTORY_UNAVAILABLE", 409)
    # NFO+FOCOM LLD section 4
    NFDEPLOYMENT_NAME_CONFLICT = ("NFDEPLOYMENT_NAME_CONFLICT", 409)
    NFDEPLOYMENT_DESCRIPTOR_ALREADY_DEPLOYED = ("NFDEPLOYMENT_DESCRIPTOR_ALREADY_DEPLOYED", 409)
    NFDEPLOYMENT_DESCRIPTOR_NOT_FOUND = ("NFDEPLOYMENT_DESCRIPTOR_NOT_FOUND", 422)
    NFDEPLOYMENT_ILLEGAL_OPERATION = ("NFDEPLOYMENT_ILLEGAL_OPERATION", 409)
    # Onboarding/rApp Mgmt LLD section 5-6
    RAPP_INSTANCE_NOT_UNDEPLOYED = ("RAPP_INSTANCE_NOT_UNDEPLOYED", 409)
    # SME Trusted Invokers (CAPIF core securityapi)
    INVOKER_NOT_REGISTERED = ("INVOKER_NOT_REGISTERED", 400)
    TRUSTED_INVOKER_NOT_FOUND = ("TRUSTED_INVOKER_NOT_FOUND", 404)
    SECURITY_CONTEXT_INVALID = ("SECURITY_CONTEXT_INVALID", 422)
    # Wave 3 (cross-cutting standardization) — Error Schema: every raw
    # HTTPException(status_code=404, detail="no such ...") across every
    # service now carries a named, importable code through this same
    # ProblemDetails convention, rather than a bare string only a human
    # ever reads.
    POLICY_TYPE_NOT_FOUND = ("POLICY_TYPE_NOT_FOUND", 404)
    ALARM_NOT_FOUND = ("ALARM_NOT_FOUND", 404)
    O1_ENDPOINT_NOT_FOUND = ("O1_ENDPOINT_NOT_FOUND", 404)
    O1_HOST_KEY_NOT_FOUND = ("O1_HOST_KEY_NOT_FOUND", 404)
    A1_SERVICE_REGISTRATION_NOT_FOUND = ("A1_SERVICE_REGISTRATION_NOT_FOUND", 404)
    MODEL_NOT_FOUND = ("MODEL_NOT_FOUND", 404)
    TRAINING_JOB_NOT_FOUND = ("TRAINING_JOB_NOT_FOUND", 404)
    VALIDATION_JOB_NOT_FOUND = ("VALIDATION_JOB_NOT_FOUND", 404)
    EMULATION_JOB_NOT_FOUND = ("EMULATION_JOB_NOT_FOUND", 404)
    MLMF_SUBSCRIPTION_NOT_FOUND = ("MLMF_SUBSCRIPTION_NOT_FOUND", 404)
    PRODUCER_NOT_FOUND = ("PRODUCER_NOT_FOUND", 404)
    TYPE_SUBSCRIPTION_NOT_FOUND = ("TYPE_SUBSCRIPTION_NOT_FOUND", 404)
    DATA_JOB_NOT_FOUND = ("DATA_JOB_NOT_FOUND", 404)
    DATA_OFFER_NOT_FOUND = ("DATA_OFFER_NOT_FOUND", 404)
    DME_ACTION_NOT_FOUND = ("DME_ACTION_NOT_FOUND", 404)
    RESOURCE_TYPE_NOT_FOUND = ("RESOURCE_TYPE_NOT_FOUND", 404)
    RESOURCE_POOL_NOT_FOUND = ("RESOURCE_POOL_NOT_FOUND", 404)
    DEPLOYMENT_MANAGER_NOT_FOUND = ("DEPLOYMENT_MANAGER_NOT_FOUND", 404)
    INTENT_HANDLING_FUNCTION_NOT_FOUND = ("INTENT_HANDLING_FUNCTION_NOT_FOUND", 404)
    INTENT_NOT_FOUND = ("INTENT_NOT_FOUND", 404)
    ARTIFACT_FORMAT_INVALID = ("ARTIFACT_FORMAT_INVALID", 415)
    ARTIFACT_VERSION_NOT_FOUND = ("ARTIFACT_VERSION_NOT_FOUND", 404)
    NFDEPLOYMENT_NOT_FOUND = ("NFDEPLOYMENT_NOT_FOUND", 404)
    RAPP_INSTANCE_NOT_FOUND = ("RAPP_INSTANCE_NOT_FOUND", 404)
    ASSURANCE_MONITOR_NOT_FOUND = ("ASSURANCE_MONITOR_NOT_FOUND", 404)
    PUBLISHING_FUNCTION_NOT_FOUND = ("PUBLISHING_FUNCTION_NOT_FOUND", 404)
    # HISTORY.md OI-6.1 — an explicit operator-approval gate on
    # Training->Validation->Emulation, mirroring the existing
    # CERTIFY/PROMOTE governance shape.
    TRAINING_NOT_APPROVED = ("TRAINING_NOT_APPROVED", 409)
    VALIDATION_NOT_APPROVED = ("VALIDATION_NOT_APPROVED", 409)
    # HISTORY.md OI-6.3 — rApp Autonomy Modes.
    AUTONOMY_DISPATCH_NOT_FOUND = ("AUTONOMY_DISPATCH_NOT_FOUND", 404)
    AUTONOMY_DISPATCH_NOT_AWAITING_SCOPE = ("AUTONOMY_DISPATCH_NOT_AWAITING_SCOPE", 409)
    # Wave 4 — TS 28.105 AI/ML NRM resources (aimgf/app/nrm.py, MLMR).
    NRM_OBJECT_NOT_FOUND = ("NRM_OBJECT_NOT_FOUND", 404)
    INFERENCE_FUNCTION_NOT_ACTIVATED = ("INFERENCE_FUNCTION_NOT_ACTIVATED", 409)
    MODEL_NOT_LOADED = ("MODEL_NOT_LOADED", 409)
    # Wave 5 — TS 28.104 MDARequest addressed to an MDAFunction.
    MDA_CAPABILITY_NOT_SUPPORTED = ("MDA_CAPABILITY_NOT_SUPPORTED", 422)
    # Wave 7 — runtime profiles (rApp package lookup) and inference reads.
    PACKAGE_NOT_FOUND = ("PACKAGE_NOT_FOUND", 404)
    # Wave 9 (HISTORY.md W9-01..06) — RAN NF OAM's
    # multi-vendor capability registry, docs/ARCHITECTURE.md
    O1_SERVICE_NOT_SUPPORTED = ("O1_SERVICE_NOT_SUPPORTED", 409)
    VENDOR_CAPABILITY_NOT_FOUND = ("VENDOR_CAPABILITY_NOT_FOUND", 404)
    CM_SCHEMA_NOT_FOUND = ("CM_SCHEMA_NOT_FOUND", 404)
    CM_SCHEMA_CONFLICT = ("CM_SCHEMA_CONFLICT", 409)
    MANAGED_ENTITY_NOT_FOUND = ("MANAGED_ENTITY_NOT_FOUND", 404)
    INFERENCE_JOB_NOT_FOUND = ("INFERENCE_JOB_NOT_FOUND", 404)
    # OI-2-lcm-error-mapping — Onboarding's usage/stop on an unknown (or
    # another package's) registration id, and an rApp upgrade resolved
    # after its upgradeTimeoutSeconds deadline (already rolled back).
    PACKAGE_USAGE_REGISTRATION_NOT_FOUND = ("PACKAGE_USAGE_REGISTRATION_NOT_FOUND", 404)
    RAPP_UPGRADE_TIMED_OUT = ("RAPP_UPGRADE_TIMED_OUT", 409)
    # PR-ST-2 — a row's version changed between load and write (another request
    # committed first); smo_shared/versioning.py. The caller repeats the request.
    CONCURRENT_MODIFICATION = ("CONCURRENT_MODIFICATION", 409)
    # PR-ST-3 — Idempotency-Key header on command routes; smo_shared/idempotency.py.
    IDEMPOTENCY_KEY_INVALID = ("IDEMPOTENCY_KEY_INVALID", 422)
    IDEMPOTENCY_KEY_REUSED = ("IDEMPOTENCY_KEY_REUSED", 422)
    IDEMPOTENCY_KEY_IN_PROGRESS = ("IDEMPOTENCY_KEY_IN_PROGRESS", 409)


def framework_error(code: tuple[str, int], detail: str | None = None) -> HTTPException:
    title, status = code
    return problem(status, title, detail)


def illegal_transition_error(exc, subject: str) -> HTTPException:
    """409 LIFECYCLE_ILLEGAL_TRANSITION for a `smo_shared.statemachine.
    IllegalTransition` raised by a lifecycle route — naming the entity,
    its current state and the refused event, instead of the unhandled
    500 the exception otherwise becomes.
    """
    return framework_error(FrameworkError.LIFECYCLE_ILLEGAL_TRANSITION,
                           detail=f"{subject}: event {exc.event} is not allowed in state {exc.state}")
