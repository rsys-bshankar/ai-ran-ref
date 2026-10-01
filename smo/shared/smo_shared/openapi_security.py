"""Declares, in each service's own generated OpenAPI spec, the R1 gateway
security this build already really enforces at runtime — without adding any
enforcing dependency to that service itself.

r1-termination/app/main.py's own `_authorized()` is the ONE real enforcement
point: RFC 7662 introspection against SME's token issuer, on every proxied
request, fail-closed. No individual backend service (aimgf, dme, mlmr, ...)
has ever had its own auth check — they all trust the gateway that's the only
way a real R1 consumer ever reaches them. That's still true after this
module exists: it only makes the OpenAPI contract honest about an interface
that was already real, not a new runtime behavior. See HISTORY.md §2 and docs/ARCHITECTURE.md's Wave 3
cross-cutting standardization item.

R1_CONTRACT_VERSION is this build's own semantic version for "the current,
Wave-3-complete R1 contract shape" — not a claim to match any one external
3GPP/O-RAN spec's own info.version, since most of these services compose
several specs (or none) rather than implement exactly one 1:1.
"""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

BEARER_SCHEME_NAME = "r1BearerAuth"
R1_CONTRACT_VERSION = "1.0.0"


def apply_r1_gateway_security(app: FastAPI, *, public_paths: frozenset[str] = frozenset()) -> None:
    """Every route on `app` requires the R1 gateway's bearer token, except
    `public_paths` (SME's own token-issuance/introspection endpoints, and
    r1-termination's own unauthenticated /health + /bootstrap — the two
    routes its own docstrings already say never call `_authorized`).
    """
    app.version = R1_CONTRACT_VERSION

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        schema.setdefault("components", {}).setdefault("securitySchemes", {})[BEARER_SCHEME_NAME] = {
            "type": "http", "scheme": "bearer", "bearerFormat": "JWT",
        }
        schema["security"] = [{BEARER_SCHEME_NAME: []}]
        for path, operations in schema.get("paths", {}).items():
            if path in public_paths:
                for operation in operations.values():
                    if isinstance(operation, dict):
                        operation["security"] = []
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi
