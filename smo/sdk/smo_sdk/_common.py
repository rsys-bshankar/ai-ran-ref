"""Shared plumbing every sdk.* client uses.

Golden rule 6 (docs/ARCHITECTURE.md): the AI Runtime
SDK is a thin client over the same R1 Termination path every other
cross-module call in this build already uses — not a second one. Each
client class below wraps `smo_shared.r1_client.R1Client`, one typed
method per real platform-service route, so an rApp author calls
`sdk.models.register_model(...)` instead of hand-building an HTTP
request against `/mlmr/models`.
"""

import uuid

from smo_shared.r1_client import R1Client


class SdkError(Exception):
    """A platform service answered (no transport-level exception) with an
    error status. Same shape as so-smos/app/dispatch.py's own
    DownstreamError — an rApp author gets a clean exception with the
    real status code and body, not a silent success or a bare requests
    exception.
    """

    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"{status_code}: {body}")


def ensure_ok(resp) -> dict | list | None:
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        raise SdkError(resp.status_code, body)
    if resp.status_code == 204 or not resp.content:
        return None
    body = resp.json()
    # Wave 3: every list-returning route now answers {items, total, limit,
    # offset} (real limit/offset pagination) instead of a bare array. No
    # route in this build otherwise shapes a response as {"items": [...]},
    # so unwrapping it here — once, at this one shared response boundary
    # every sdk.* method already goes through — keeps every existing
    # list_*/query_*/discover_* method's own `-> list[dict]` return type
    # exactly as it was.
    if isinstance(body, dict) and isinstance(body.get("items"), list):
        return body["items"]
    return body


def _is_concurrent_modification(resp) -> bool:
    """409 whose ProblemDetails title is CONCURRENT_MODIFICATION (smo_shared/versioning.py), as
    opposed to the other 409s (an illegal transition, a name conflict), which are final answers."""
    if resp.status_code != 409:
        return False
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        return False
    return isinstance(detail, dict) and detail.get("title") == "CONCURRENT_MODIFICATION"


class _RetryOnConflict:
    """Wraps an R1 client so a mutating call that lost a write race (409 CONCURRENT_MODIFICATION,
    PR-ST-2) is sent once more. The platform rolled the first attempt back and reloads the row for
    the second, so the repeat either succeeds or is refused for what it now is (for example an
    illegal transition because the other writer already made the same move); a second conflict is
    returned as is. Reads are never retried, nor are calls that carry `files` (a consumed stream
    cannot be resent). Side effects a route performs before its commit, such as a call to another
    module, can run twice; the real remedy for that is an idempotency key (PR-ST-3), which every POST
    made through this wrapper carries: the first attempt's key is reused by the repeat, so the platform
    answers the repeat from the stored first answer if the first attempt did complete."""

    _MUTATING = ("post", "put", "patch", "delete")

    def __init__(self, r1):
        self._r1 = r1

    def __getattr__(self, name):
        attr = getattr(self._r1, name)
        if name not in self._MUTATING:
            return attr

        def call(path, *args, **kwargs):
            if name == "post" and kwargs.get("files") is None:
                # one key per SDK call, reused by the repeat below (PR-ST-3); a caller's own key wins
                headers = dict(kwargs.get("headers") or {})
                headers.setdefault("Idempotency-Key", uuid.uuid4().hex)
                kwargs["headers"] = headers
            resp = attr(path, *args, **kwargs)
            if kwargs.get("files") is None and _is_concurrent_modification(resp):
                resp = attr(path, *args, **kwargs)
            return resp

        return call


class BaseClient:
    def __init__(self, r1: R1Client | None = None):
        self._r1 = _RetryOnConflict(r1 or R1Client())
