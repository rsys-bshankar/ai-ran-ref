"""The caller's identity as R1 Termination vouches for it.

R1 Termination introspects every bearer token (RFC 7662) and, on success,
forwards the token's `client_id` (the API invoker's id) in `X-R1-Invoker-Id`.
Any value of that header a caller sent itself is dropped first, so a backend
behind R1 can trust it. A request that did not come through R1 (unit tests, an
in-process call) has none: `invoker_id` returns None.
"""

from fastapi import Request

INVOKER_ID_HEADER = "X-R1-Invoker-Id"


def invoker_id(request: Request) -> str | None:
    return request.headers.get(INVOKER_ID_HEADER) or None
