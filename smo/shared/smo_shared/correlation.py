"""Real request-correlation-ID propagation across the R1 mesh — Wave 3
cross-cutting standardization's last item (docs/architecture/
AI_PLATFORM_BASELINE.md). Audited at the start of the Wave 3
standardization pass ("Correlation-ID: totally absent") but never picked
up by any of the four shipped slices (OAuth2/JWT+Versioning, Error
Schema, Pagination, Subscriptions) — this closes it.

No formal 3GPP/O-RAN spec defines a header for this exact purpose:
specs/5G_APIs/TS29500_CustomHeaders.abnf's own real
3gpp-Sbi-Correlation-Info header is a different concept entirely
(subscriber-identity correlation — imsi/msisdn/impu/etc — not request
tracing), confirmed by reading its own ABNF grammar directly, not
assumed from the name alone. The real O-RAN SC smo-teiv component
(already this build's own ground truth for FOCOM's TEIV export) does
track a genuine per-event `correlationid` (its own CloudEvent extension
attribute, echoed via MDC into structured JSON logs) — the same concept,
a different transport (CloudEvents, not this build's own plain-REST R1
mesh). `X-Correlation-ID` is this module's own HTTP-native name for the
same idea, matching the near-universal industry convention for exactly
this purpose.

Deliberately not declared in any service's OpenAPI schema (unlike
openapi_security.py's security scheme): a middleware-injected header on
every route isn't a per-operation contract element, and adding it as a
formal parameter to ~200 operations across 18 services would be a much
larger, largely cosmetic diff for no real behavior gain.
"""

import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request

HEADER_NAME = "X-Correlation-ID"
_current_correlation_id: ContextVar[str | None] = ContextVar("_current_correlation_id", default=None)


def get_correlation_id() -> str | None:
    """The current request's correlation ID, if this call is happening
    inside a request handled by a service with `apply_correlation_id`
    applied — None outside any request context (e.g. a standalone
    script, or a service that hasn't applied it).
    """
    return _current_correlation_id.get()


def apply_correlation_id(app: FastAPI) -> None:
    """Every request gets a real correlation ID: the caller's own
    X-Correlation-ID if it sent one, else a freshly generated one.
    Stored for the duration of the request — R1Client
    (smo_shared/r1_client.py) reads it via get_correlation_id() and
    attaches it to every downstream call this handler makes, so one
    inbound request's whole cross-service fan-out shares one ID — and
    echoed back on the response so a caller that didn't send one can
    still see what got assigned.
    """

    @app.middleware("http")
    async def _correlation_id_middleware(request: Request, call_next):
        correlation_id = request.headers.get(HEADER_NAME) or str(uuid.uuid4())
        token = _current_correlation_id.set(correlation_id)
        try:
            response = await call_next(request)
        finally:
            _current_correlation_id.reset(token)
        response.headers[HEADER_NAME] = correlation_id
        return response
