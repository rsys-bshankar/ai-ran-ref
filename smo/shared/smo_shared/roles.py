"""Who is calling, as R1 Termination vouches for it (PR-SEC-14): an SMO module (or the operator's GUI) or an rApp.

SME records the `kind` of every invoker when it registers: `internal` for one that presented the enrollment secret (a compose secret every SMO
module mounts, `secrets/enrollment_secret`), `rapp` for every other. SME's token introspection answers with that as `role`; R1 Termination
forwards it in `X-R1-Role` (dropping any value a caller sent) and refuses a caller with the `rapp` role on the routes of `INTERNAL_ONLY` before
anything reaches a backend. The invoker id is `X-R1-Invoker-Id` (invoker.py).

Enforcement (`SMO_ROLE_ENFORCEMENT`, read by SME and R1 Termination, default `enforce`):
  enforce  an rApp is refused on an internal-only route (403 `ROLE_NOT_PERMITTED`) and is not granted an internal scope
  audit    the same decision is made, counted and logged, and then allowed: for a rolling upgrade from a release that has no enrollment
Two lists decide what an rApp may call. `INTERNAL_ONLY` is a deny-list (any method, so it also covers reads): a route listed is refused. `RAPP_MAY_CHANGE` is
an allow-list for *changes* (POST, PUT, PATCH, DELETE): an rApp may change only what the SDK and the 3GPP consumer-facing routes need, and a change anywhere
else (onboarding, instance management, orchestration, the other modules' administration) is refused with the same 403 `ROLE_NOT_PERMITTED`. Reads are
open to every valid token, as before; scoping them per tenant is PR-SEC-10. Add a route to either list with the test that proves it exists.
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


CHANGES = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# module prefix at R1 -> the changes an rApp may make there, as (methods, path regex); `None` is every change (a module that is an rApp's own surface).
# Derived from what `smo_sdk` calls (tests/test_roles.py in r1-termination proves every SDK call is on it) plus the consumer-facing request routes
# of the 3GPP services that an rApp that does not use the SDK would call. What is left out is what administers a service rather than uses it:
# registering functions, repositories and storages, runtime scaling and termination, sweeps, trusted invokers, purging, and every route of the
# modules an operator drives (onboarding, instance management, orchestration, the sample rApps' own APIs).
RAPP_MAY_CHANGE: dict[str, tuple[tuple[frozenset[str], re.Pattern], ...] | None] = {
    prefix: None if rules is None else tuple((frozenset(m), re.compile(rx)) for m, rx in rules) for prefix, rules in {
        "/sme": (
            (("POST",), r"^/(provider-registrations|invoker-registrations|oauth2/(token|introspect))$"),
            (("DELETE",), r"^/provider-registrations/[^/]+$"),
            (("POST",), r"^/published-apis/v1/[^/]+/service-apis$"),
            (("DELETE",), r"^/published-apis/v1/[^/]+/service-apis/[^/]+$"),
            (("POST",), r"^/capif-events/v1/[^/]+/subscriptions$"),
            (("DELETE",), r"^/capif-events/v1/[^/]+/subscriptions/[^/]+$"),
        ),
        "/dme": (
            (("POST",), r"^/(actions|production-capabilities|data-jobs|offers|type-subscriptions)$"),
            (("DELETE",), r"^/(production-capabilities|data-jobs)$"),
            (("PUT", "DELETE"), r"^/data-jobs/[^/]+$"),
            (("POST",), r"^/data-jobs/[^/]+/records$"),
            (("DELETE",), r"^/(dme-types|offers|type-subscriptions)/[^/]+$"),
            (("POST",), r"^/offers/[^/]+/notify$"),
        ),
        "/aimgf": (
            (("POST",), r"^/(training-jobs|validation-jobs|emulation-jobs|feature-groups|mlmf/subscriptions|ml-(training|testing|update|model-loading)-requests|aiml-inference-reports)$"),
            (("DELETE",), r"^/(training-jobs|feature-groups|mlmf/subscriptions|ml-training-requests)/[^/]+$"),
            (("PATCH",), r"^/ml-(training|testing|update|model-loading)-requests/[^/]+$"),
            (("POST",), r"^/training-jobs/[^/]+/(suspend|resume|progress|model-metrics|complete)$"),
            (("POST",), r"^/(validation|emulation)-jobs/[^/]+/complete$"),
            (("POST",), r"^/mlmf/subscriptions/[^/]+/reports$"),
            (("POST",), r"^/models/[^/]+/(advance|inference-jobs|runtime/deploy|runtime/activate)$"),
            (("POST",), r"^/inference-jobs/[^/]+/resolve$"),
        ),
        "/mlmr": (
            (("POST",), r"^/(models|coordination-groups)$"),
            (("PUT", "DELETE"), r"^/models/[^/]+$"),
            (("POST",), r"^/models/[^/]+/artifact$"),
            (("PATCH",), r"^/models/[^/]+/phase-info$"),
        ),
        "/mllf": ((("POST",), r"^/models/[^/]+/deploy$"),),
        "/mdaf": (
            (("POST",), r"^/(subscriptions|mda-requests|mda-reports|reports)$"),
            (("DELETE",), r"^/(subscriptions|mda-requests)/[^/]+$"),
        ),
        "/intent-service": (
            (("POST",), r"^/(intents|intent-reports|intent-handling-functions|autonomy-dispatches)$"),
            (("DELETE",), r"^/(intents|intent-handling-functions)/[^/]+$"),
            (("PATCH",), r"^/intents/[^/]+/admin-state$"),
            (("POST",), r"^/intents/[^/]+/negotiation-feedback$"),
            (("POST",), r"^/autonomy-dispatches/[^/]+/(resolve|reject)$"),
        ),
        # CM writes by an rApp are checked by its own safeguards (AI-10); undoing its job is the same path
        "/ran-nf-oam": (
            (("POST",), r"^/config-jobs$"),
            (("POST",), r"^/config-jobs/[^/]+/rollback$"),
        ),
        # an rApp's own data-producer and A1 surfaces, which the SDK does not wrap
        "/ran-analytics": None,
        "/a1-related": None,
        "/dme-push": None,
        "/dme-pull": None,
    }.items()
}


def rapp_may_change(module: str, method: str, path: str) -> bool:
    """Whether an rApp may make this *change*; a read is always `True` here. A module that is not in `RAPP_MAY_CHANGE` is not for rApps to change."""
    if method.upper() not in CHANGES:
        return True
    if module not in RAPP_MAY_CHANGE:
        return False
    rules = RAPP_MAY_CHANGE[module]
    if rules is None:
        return True
    path = "/" + path.lstrip("/")
    return any(method.upper() in methods and pattern.match(path) for methods, pattern in rules)


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
