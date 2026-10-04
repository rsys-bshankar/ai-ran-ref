"""Who is calling, as R1 Termination vouches for it (PR-SEC-14): an SMO module (or the operator's GUI) or an rApp.

SME records the `kind` of every invoker when it registers: `internal` for one that presented the enrollment secret (a compose secret every SMO
module mounts, `secrets/enrollment_secret`), `rapp` for every other. SME's token introspection answers with that as `role`; R1 Termination
forwards it in `X-R1-Role` (dropping any value a caller sent) and refuses a caller with the `rapp` role on the routes of `INTERNAL_ONLY` before
anything reaches a backend. The invoker id is `X-R1-Invoker-Id` (invoker.py).

Enforcement (`SMO_ROLE_ENFORCEMENT`, read by SME and R1 Termination, default `enforce`):
  enforce  an rApp is refused on an internal-only route (403 `ROLE_NOT_PERMITTED`) and is not granted an internal scope
  audit    the same decision is made, counted and logged, and then allowed: for a rolling upgrade from a release that has no enrollment
The policy is a deny-list: a route not listed is open to every valid token, as before. Add a route here, with the test that proves it exists.
"""

import hmac
import os
import re

ROLE_INTERNAL = "internal"
ROLE_RAPP = "rapp"
ROLE_HEADER = "X-R1-Role"
ENROLLMENT_HEADER = "X-SMO-Enrollment"

# the scopes only an internal invoker is granted (SME); `smo-rapp` is the scope an rApp asks for
INTERNAL_SCOPES = frozenset({"smo-internal", "smo-gui"})
RAPP_SCOPE = "smo-rapp"

# (module prefix at R1, methods, path regex) -> refused to an rApp. Paths are as the backend serves them, without the prefix.
INTERNAL_ONLY: tuple[tuple[str, frozenset[str], re.Pattern], ...] = tuple(
    (module, frozenset(methods), re.compile(pattern)) for module, methods, pattern in (
        # AI-10.2: what one rApp may do is set by the platform, never by an rApp
        ("/ran-nf-oam", ("PUT", "DELETE"), r"^/rapp-limits/[^/]+$"),
        # MGT-11: the KPIs the platform measures by
        ("/ran-nf-oam", ("PUT", "DELETE"), r"^/kpi-definitions/[^/]+$"),
        # MGT-1.8: removing CM history
        ("/ran-nf-oam", ("POST",), r"^/config-history/purge$"),
        # AI-10.4: stopping an rApp, lifting it, and who is stopped (an rApp may read whether it is stopped itself)
        ("/ran-nf-oam", ("PUT", "DELETE"), r"^/rapp-kill/[^/]+$"),
        ("/ran-nf-oam", ("GET",), r"^/rapp-kill$"),
        ("/rapp-mgmt", ("PUT", "DELETE"), r"^/instances/[^/]+/kill$"),
        # MGT-11.6/11.7: seeding the standard KPIs, and pushing results to DME (the reads stay open)
        ("/ran-nf-oam", ("POST",), r"^/kpi-definitions/standard$"),
        ("/ran-nf-oam", ("POST",), r"^/kpis/[^/]+/publish$"),
        # AI-10.6: who is told about refusals, and the record of them (it names other rApps)
        ("/ran-nf-oam", ("GET", "POST", "DELETE"), r"^/safeguard-subscriptions(/[^/]+)?$"),
        ("/ran-nf-oam", ("GET",), r"^/safeguard-refusals$"),
        ("/ran-nf-oam", ("POST",), r"^/safeguard-refusals/purge$"),
        ("/ran-nf-oam", ("PUT", "DELETE"), r"^/kpi-schedules/[^/]+$"),
    ))


def enforcement_mode() -> str:
    mode = os.environ.get("SMO_ROLE_ENFORCEMENT", "enforce").strip().lower()
    return mode if mode in ("enforce", "audit") else "enforce"      # a typo must not switch protection off


def internal_only(module: str, method: str, path: str) -> bool:
    """Whether an rApp is refused this call: `module` is the R1 prefix (`/ran-nf-oam`), `path` what follows it (`/rapp-limits/x`)."""
    path = "/" + path.lstrip("/")
    return any(m == module and method.upper() in methods and pattern.match(path) for m, methods, pattern in INTERNAL_ONLY)


def role_of(request) -> str | None:
    """The role R1 Termination vouched for on this request; None when it did not come through R1 (an in-process call, a test)."""
    return request.headers.get(ROLE_HEADER) or None


def enrollment_secret_valid(presented: str | None, expected: str) -> bool:
    return bool(presented) and bool(expected) and hmac.compare_digest(presented.encode(), expected.encode())
