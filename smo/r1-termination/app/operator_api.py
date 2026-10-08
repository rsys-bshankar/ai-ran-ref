"""The dynamic prefix `/rapps/{instanceId}/operator/<route>` (PR-GUI-8, GUI-8.3; docs/adr/0004-operator-ui-declaration.md, 4).

A rApp instance registers where its operator API is reached (`operatorApiBase`, rApp Management). The gateway resolves the prefix to that base, so a
rApp onboarded at run time is reachable without a change to the gateway's route table or a restart. This module is the resolution and its cache;
`main.py` does the authentication, the role policy and the forwarding.

  - **Resolution** asks rApp Management (`GET /instances/{id}/operator-api`, as an internal caller) and keeps the answer, a base or "none", for
    `R1_OPERATOR_API_CACHE_SECONDS` (default 5; 0 asks every time). A registration, a change or a terminated instance therefore bites within that time.
    When rApp Management cannot be asked, an answer less than a minute old is used; with none the call is refused (503), never guessed.
  - **The base is a caller-supplied URL** (a workload registers it), so it is checked when it is stored (`smo_shared.webhook.normalise_base_url`) and again
    here before every call (`smo_shared.webhook.forward_to_destination`: a loopback, link-local or metadata address is not called).
  - **Nothing of the rApp's address or of an exception is ever put in a response**: a failure is a fixed title.
"""

import os
import re
import threading
import time
import uuid

import httpx

from smo_shared import mtls
from smo_shared.roles import ROLE_HEADER, ROLE_INTERNAL
from smo_shared.invoker import INVOKER_ID_HEADER
from smo_shared.timeouts import introspect_timeout
from smo_shared.webhook import normalise_base_url

PREFIX = "rapps"
STALE_SECONDS = 60.0
MAX_CACHED = 10_000
GATEWAY_ID = "r1-termination"

# what follows `/rapps/<uuid>/operator`: nothing, or `/` and segments of letters, digits and `._~-` (the segments a declared route can have)
_REST = re.compile(r"^(/[A-Za-z0-9._~-]+)*$")

_cache: dict[str, tuple[float, str | None]] = {}
_lock = threading.Lock()


class Unresolvable(RuntimeError):
    """rApp Management did not answer and there is no recent answer to use."""


def ttl() -> float:
    try:
        return max(0.0, float(os.environ.get("R1_OPERATOR_API_CACHE_SECONDS", "5")))
    except ValueError:
        return 5.0


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def split(rest_of_path: str) -> tuple[str, str] | None:
    """`<uuid>/operator[/<route>]` -> (instance id, the route to call at the rApp, starting with `/` or empty); None when it is not that shape.
    The route is limited to the segments a declared route can have, so nothing that a rApp's own router could read differently passes."""
    parts = rest_of_path.split("/", 2)
    if len(parts) < 2 or parts[1] != "operator":
        return None
    try:
        instance = str(uuid.UUID(parts[0]))
    except ValueError:
        return None
    route = "/" + parts[2] if len(parts) == 3 else ""
    if not _REST.fullmatch(route):
        return None
    return instance, route


async def resolve(instance_id: str, rapp_mgmt_url: str, now=time.monotonic) -> str | None:
    """The registered base of the instance (no trailing slash), or None when it has none, is unknown or is terminated. Raises Unresolvable."""
    moment = now()
    with _lock:
        cached = _cache.get(instance_id)
    if cached is not None and moment - cached[0] < ttl():
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=introspect_timeout(), **mtls.client_kwargs(rapp_mgmt_url)) as client:
            resp = await client.request("GET", f"{rapp_mgmt_url}/instances/{instance_id}/operator-api",
                                        headers={ROLE_HEADER: ROLE_INTERNAL, INVOKER_ID_HEADER: GATEWAY_ID})
        if resp.status_code == 404:
            base = None
        elif resp.status_code == 200:
            base = normalise_base_url(resp.json().get("operatorApiBase"))
        else:
            raise Unresolvable(f"rApp Management answered {resp.status_code}")
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        if cached is not None and moment - cached[0] < STALE_SECONDS:
            return cached[1]
        raise Unresolvable(exc.__class__.__name__) from exc
    except Unresolvable:
        if cached is not None and moment - cached[0] < STALE_SECONDS:
            return cached[1]
        raise
    with _lock:
        if len(_cache) >= MAX_CACHED:
            _cache.clear()
        _cache[instance_id] = (moment, base)
    return base
