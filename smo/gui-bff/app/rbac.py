"""The GUI's permission table: METHOD + /<module>/... -> minimum role.

The BFF is the authority. The SPA hides actions a role can't take, but every
proxied call is matched here before it's forwarded to R1 Termination.

Allowlist semantics: the first matching rule wins, and a request matching no
rule is refused. Every read under a known module prefix is Viewer-level
(apart from the one sensitive read listed first); every mutation must be
listed explicitly. Machine-to-machine routes that only an rApp, NF or
another SMO module should call (SME token/registration APIs, DME producer
registration, NFO Instantiate, usage registrations, heartbeats, ...) are
deliberately absent, so the GUI can't reach them at all.

Some rules also pin request parameters to the caller's GUI identity rather
than trusting what the browser sent: who acknowledged or cleared an alarm,
SA SMOS's requester_is_admin flag, and the RMIO identity on Policy Mgmt
intents.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


RANK = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}

# The R1 Termination route prefixes the GUI may reach (R1's own ROUTES table,
# minus DME's push/pull aliases, which are rApp data-plane paths).
MODULES = [
    "sme", "dme", "onboarding", "rapp-mgmt", "ran-nf-oam", "a1-related", "nfo", "focom",
    "ai-ml-workflow", "ran-analytics", "policy-mgmt", "so-smos", "sa-smos",
]

# The RMIO identity every GUI-created intent carries. Policy Mgmt only lets
# an intent's own creator change its admin state, so pinning this on both
# create and update means the GUI can manage exactly the intents it created.
GUI_RMIO_ID = "smo-gui"


@dataclass(frozen=True)
class User:
    username: str
    role: Role


Overrides = Callable[[User], dict]


@dataclass(frozen=True)
class Rule:
    method: str
    pattern: re.Pattern
    role: Role
    query_match: dict = field(default_factory=dict)   # extra condition on query params
    query_overrides: Overrides | None = None          # params forced from the GUI identity
    json_overrides: Overrides | None = None           # top-level JSON body fields forced from it


@dataclass(frozen=True)
class Decision:
    allowed: bool
    required_role: Role | None   # None: not exposed through the GUI at all
    rule: Rule | None = None


_ID = r"[^/]+"


def _rule(method: str, path: str, role: Role, **kw) -> Rule:
    return Rule(method, re.compile("^" + path.replace("{id}", _ID) + "$"), role, **kw)


V, O, A = Role.VIEWER, Role.OPERATOR, Role.ADMIN

RULES: list[Rule] = [
    # --- the one sensitive read: feature groups carry datalake tokens
    _rule("GET", "/ai-ml-workflow/feature-groups", O),

    # --- Onboarding
    _rule("POST", "/onboarding/packages", O),
    _rule("POST", "/onboarding/packages/{id}/(prime|deprime|deprecate|cancel-delete)", O),
    _rule("DELETE", "/onboarding/packages/{id}", A),
    # usage registrations are what an rApp instance itself files (call flow 06):
    # simulating one, to exercise the cascade-delete guard, is admin-only
    _rule("POST", "/onboarding/packages/{id}/usage/start", A),
    _rule("POST", "/onboarding/packages/{id}/usage/{id}/stop", A),

    # --- rApp Management
    _rule("POST", "/rapp-mgmt/instances", O),
    _rule("PUT", "/rapp-mgmt/instances/{id}/config", O),
    _rule("POST", "/rapp-mgmt/instances/{id}/(upgrade|upgrade/resolve|recover|bootstrap-complete)", O),
    _rule("POST", "/rapp-mgmt/instances/{id}/terminate", A),
    _rule("DELETE", "/rapp-mgmt/instances/{id}", A),
    _rule("POST", "/rapp-mgmt/instances/{id}/(performance|fault)", A),   # test-data injection

    # --- AI/ML Workflow
    _rule("POST", "/ai-ml-workflow/models", O),
    _rule("PUT", "/ai-ml-workflow/models/{id}", O),
    _rule("DELETE", "/ai-ml-workflow/models/{id}", A),
    _rule("POST", "/ai-ml-workflow/models/{id}/advance", A, query_match={"event": "DEPRECATE"}),
    _rule("POST", "/ai-ml-workflow/models/{id}/(advance|artifact|deploy|inference-jobs)", O),
    _rule("POST", "/ai-ml-workflow/inference-jobs/{id}/resolve", O),
    _rule("POST", "/ai-ml-workflow/training-jobs", O),
    _rule("DELETE", "/ai-ml-workflow/training-jobs/{id}", O),        # cancel, not a hard delete
    _rule("POST", "/ai-ml-workflow/training-jobs/{id}/model-metrics", O),
    _rule("POST", "/ai-ml-workflow/(coordination-groups|feature-groups|mlmf/subscriptions)", O),
    _rule("POST", "/ai-ml-workflow/mlmf/subscriptions/{id}/reports", A),  # test-data injection

    # --- RAN NF OAM
    _rule("PATCH", "/ran-nf-oam/alarms/{id}/ack", O,
          query_overrides=lambda u: {"ack_user_id": u.username}),
    _rule("PATCH", "/ran-nf-oam/alarms/{id}/clear", O,
          query_overrides=lambda u: {"clear_user_id": u.username}),
    _rule("POST", "/ran-nf-oam/alarms/ingest", A),                   # test-data injection
    # CM writes: who asked, and the MSAC access tier that entire-RAN scope
    # requires, come from the GUI identity (an admin holds the tier; an
    # operator's entire-RAN write is refused by RAN NF OAM's own MSAC gate)
    _rule("POST", "/ran-nf-oam/config-jobs", O,
          json_overrides=lambda u: {"requestedBy": f"smo-gui:{u.username}", "msacRole": "admin" if u.role == Role.ADMIN else None}),
    _rule("POST", "/ran-nf-oam/(pm-subscriptions|software-management-jobs|o1-adaptor-endpoints|o1-adaptor-endpoints/discover)", O),
    _rule("POST", "/ran-nf-oam/software-management-jobs/{id}/advance", O),
    _rule("POST", "/ran-nf-oam/o1-adaptor-endpoints/{id}/heartbeat", A),   # what the ME's adaptor sends: simulation

    # --- A1 Related
    _rule("POST", "/a1-related/policies", O),
    _rule("PUT", "/a1-related/policies/{id}", O),
    _rule("DELETE", "/a1-related/policies/{id}", A),
    _rule("POST", "/a1-related/policies/subscriptions", O),
    _rule("DELETE", "/a1-related/policies/subscriptions/{id}", O),
    _rule("POST", "/a1-related/ei-types/register", A),               # producer side of call flow 05
    _rule("DELETE", "/a1-related/ei-types/{id}", A),
    _rule("PUT", "/a1-related/services", A),                         # A1-P service registry
    _rule("PUT", "/a1-related/services/{id}/keepalive", A),
    _rule("DELETE", "/a1-related/services/{id}", A),

    # --- DME (call flow 05): consumers are operator-level, producers admin
    _rule("POST", "/dme/data-jobs", O),
    _rule("PUT", "/dme/data-jobs/{id}", O),
    _rule("DELETE", "/dme/data-jobs/{id}", O),                       # terminate a consumer job
    _rule("POST", "/dme/type-subscriptions", O),
    _rule("DELETE", "/dme/type-subscriptions/{id}", O),
    _rule("POST", "/dme/production-capabilities", A),
    _rule("DELETE", "/dme/production-capabilities", A),
    _rule("POST", "/dme/offers", A),
    _rule("POST", "/dme/offers/{id}/notify", A),
    _rule("DELETE", "/dme/offers/{id}", A),

    # --- SME: registry administration is admin; event subscriptions operator.
    # Invoker onboarding returns a one-time secret, so it's admin-only and
    # audited; token issuance and introspection stay unexposed.
    _rule("POST", "/sme/provider-registrations", A),
    _rule("DELETE", "/sme/provider-registrations/{id}", A),
    _rule("POST", "/sme/published-apis/v1/{id}/service-apis", A),
    _rule("DELETE", "/sme/published-apis/v1/{id}/service-apis/{id}", A),
    _rule("POST", "/sme/invoker-registrations", A),
    _rule("PUT", "/sme/trusted-invokers/{id}", A),
    _rule("POST", "/sme/trusted-invokers/{id}/(update|delete)", A),
    _rule("DELETE", "/sme/trusted-invokers/{id}", A),
    _rule("POST", "/sme/capif-events/v1/{id}/subscriptions", O),
    _rule("DELETE", "/sme/capif-events/v1/{id}/subscriptions/{id}", O),

    # --- NFO
    _rule("POST", "/nfo/deployments/{id}/(heal|scale)", O),
    _rule("DELETE", "/nfo/deployments/{id}", A),

    # --- FOCOM
    _rule("POST", "/focom/resources/provision", A),
    _rule("DELETE", "/focom/resources/{id}", A),
    _rule("POST", "/focom/alarms/ingest", A),                        # test-data injection
    _rule("POST", "/focom/inventory/subscriptions", O),
    _rule("DELETE", "/focom/inventory/subscriptions/{id}", O),

    # --- Policy Mgmt
    _rule("POST", "/policy-mgmt/intents", O, json_overrides=lambda u: {"rmioId": GUI_RMIO_ID}),
    _rule("PATCH", "/policy-mgmt/intents/{id}/admin-state", O, json_overrides=lambda u: {"requesterId": GUI_RMIO_ID}),
    _rule("DELETE", "/policy-mgmt/intents/{id}", A),
    # RMIH registration is framework-internal only (D-SEC-POLICY-1: SO/SA SMOS
    # identities) and fulfilment reports come from an RMIH, so both are admin
    # acting on the framework's behalf (call flow 09)
    _rule("POST", "/policy-mgmt/intent-handling-functions", A),
    _rule("DELETE", "/policy-mgmt/intent-handling-functions/{id}", A),
    _rule("POST", "/policy-mgmt/intent-reports", A),

    # --- RAN Analytics
    _rule("POST", "/ran-analytics/subscriptions", O),
    _rule("DELETE", "/ran-analytics/subscriptions/{id}", O),
    _rule("POST", "/ran-analytics/producers", A),                    # producer side of call flow 08
    _rule("POST", "/ran-analytics/reports", A),

    # --- SO / SA SMOS
    _rule("POST", "/so-smos/orders", O),
    _rule("POST", "/so-smos/orders/{id}/cancel", O),
    _rule("POST", "/sa-smos/monitors", O),
    _rule("POST", "/sa-smos/monitors/{id}/(evaluate|escalate)", O),
    _rule("POST", "/sa-smos/monitors/{id}/remedial-actions", O,
          query_overrides=lambda u: {"requester_is_admin": "true" if u.role == Role.ADMIN else "false"}),

    # --- every other read under a known module prefix
    _rule("GET", "/(" + "|".join(re.escape(m) for m in MODULES) + ")(/.*)?", V),
]


def decide(method: str, path: str, query: dict[str, list[str]], role: Role) -> Decision:
    """`query` maps each param to ALL its values: a query_match rule
    matches if any value does, so `?event=CERTIFY&event=DEPRECATE` can't
    slip a deprecation past the admin-only rule whichever value the
    backend ends up reading.
    """
    method = method.upper()
    for rule in RULES:
        if rule.method != method or not rule.pattern.match(path):
            continue
        if any(v not in query.get(k, []) for k, v in rule.query_match.items()):
            continue
        return Decision(allowed=RANK[role] >= RANK[rule.role], required_role=rule.role, rule=rule)
    return Decision(allowed=False, required_role=None)
