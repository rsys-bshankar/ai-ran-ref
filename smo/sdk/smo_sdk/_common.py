"""Shared plumbing every sdk.* client uses.

Golden rule 6 (docs/architecture/AI_PLATFORM_BASELINE.md): the AI Runtime
SDK is a thin client over the same R1 Termination path every other
cross-module call in this build already uses — not a second one. Each
client class below wraps `smo_shared.r1_client.R1Client`, one typed
method per real platform-service route, so an rApp author calls
`sdk.models.register_model(...)` instead of hand-building an HTTP
request against `/mlmr/models`.
"""

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
    return resp.json()


class BaseClient:
    def __init__(self, r1: R1Client | None = None):
        self._r1 = r1 or R1Client()
