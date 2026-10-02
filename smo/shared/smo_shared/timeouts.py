"""The platform's outbound HTTP timeouts, in one place (PR-ST-6).

An outbound call with no timeout either hangs a worker for good or, with httpx's own implicit 5 s default,
fails a perfectly healthy slow operation. Every HTTP call in service code therefore passes an explicit timeout,
and `tests_integration/test_http_timeouts.py` fails the build on one that does not.

The three defaults nest so that an outer caller always outlasts the call it waits on:

  SMO_HTTP_TIMEOUT_SECONDS          30   a module calling another module through R1 (`R1Client`)
  R1_UPSTREAM_TIMEOUT_SECONDS       60   R1 Termination waiting for the backend it proxies to
  R1_INTROSPECT_TIMEOUT_SECONDS      5   R1 Termination asking SME whether a token is active (fails closed)

Calls with their own documented bound keep it: webhooks (`webhook.py`, 5 s), A1 southbound (5 s), the
NETCONF/RESTCONF exchange (30 s), the CSAR download (30 s), SME token and registration calls (5 s).
Values are read when asked, not at import, so they can be changed by the environment of a running process
only through a restart, and tests can set them.
"""

import os


def _seconds(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def call_timeout() -> float:
    return _seconds("SMO_HTTP_TIMEOUT_SECONDS", 30.0)


def upstream_timeout() -> float:
    return _seconds("R1_UPSTREAM_TIMEOUT_SECONDS", 60.0)


def introspect_timeout() -> float:
    return _seconds("R1_INTROSPECT_TIMEOUT_SECONDS", 5.0)
