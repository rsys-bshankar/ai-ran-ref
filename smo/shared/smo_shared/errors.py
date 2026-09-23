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
    INFERENCE_MODEL_NOT_ACTIVE = ("INFERENCE_MODEL_NOT_ACTIVE", 409)
    MODEL_ALREADY_REGISTERED = ("MODEL_ALREADY_REGISTERED", 409)
    MODEL_IDENTITY_IMMUTABLE = ("MODEL_IDENTITY_IMMUTABLE", 400)
    # A1 Related LLD section 1.3
    POLICY_TYPE_NOT_SUPPORTED = ("POLICY_TYPE_NOT_SUPPORTED", 422)
    POLICY_OBJECT_SCHEMA_INVALID = ("POLICY_OBJECT_SCHEMA_INVALID", 422)
    SUBSCRIPTION_SCOPE_CONFLICT = ("SUBSCRIPTION_SCOPE_CONFLICT", 422)
    # Foundational Platform LLD section 5
    SERVICE_NAME_CONFLICT = ("SERVICE_NAME_CONFLICT", 409)
    DME_TYPE_VERSION_CONFLICT = ("DME_TYPE_VERSION_CONFLICT", 409)
    DELIVERY_METHOD_NOT_OFFERED = ("DELIVERY_METHOD_NOT_OFFERED", 409)
    # SO/SA SMOS LLD section 2.1
    ROLLBACK_HISTORY_UNAVAILABLE = ("ROLLBACK_HISTORY_UNAVAILABLE", 501)
    # NFO+FOCOM LLD section 4
    NFDEPLOYMENT_NAME_CONFLICT = ("NFDEPLOYMENT_NAME_CONFLICT", 409)
    NFDEPLOYMENT_DESCRIPTOR_ALREADY_DEPLOYED = ("NFDEPLOYMENT_DESCRIPTOR_ALREADY_DEPLOYED", 409)
    NFDEPLOYMENT_DESCRIPTOR_NOT_FOUND = ("NFDEPLOYMENT_DESCRIPTOR_NOT_FOUND", 422)
    NFDEPLOYMENT_ILLEGAL_OPERATION = ("NFDEPLOYMENT_ILLEGAL_OPERATION", 409)


def framework_error(code: tuple[str, int], detail: str | None = None) -> HTTPException:
    title, status = code
    return problem(status, title, detail)
