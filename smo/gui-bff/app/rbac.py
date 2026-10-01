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
SA SMOS's requester_is_admin flag, the RMIO identity on Intent Service
intents, and who rejected an ASSIST autonomy dispatch.
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
    "aimgf", "mlmr", "mllf", "ran-analytics", "mdaf", "intent-service", "so-smos", "sa-smos",
    "energy-saving-rapp",  # Wave 10.1: the reference rApp's operator API (its dashboard and loop controls)
    "mobility-optimization-rapp",  # Wave 10.2
    "coverage-optimization-rapp",  # Wave 10.3
    "traffic-steering-rapp",  # Wave 10.4
]

# The RMIO identity every GUI-created intent carries. Intent Service only lets
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
    _rule("GET", "/aimgf/feature-groups", O),

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

    # --- AI Platform (Wave 1 split of the former ai-ml-workflow: MLMR owns
    # the model/artifact/coordination-group rows, AIMgF owns lifecycle
    # state/training/validation/emulation/inference/MLMF/feature-groups,
    # MLLF owns deploy). AIMgF's own internal
    # PATCH /models/{id}/runtime/node-groups is deliberately absent — a
    # machine-to-machine route only MLLF calls, same as the SME/DME/NFO
    # internal routes above.
    _rule("POST", "/mlmr/models", O),
    _rule("PUT", "/mlmr/models/{id}", O),
    _rule("DELETE", "/mlmr/models/{id}", A),
    _rule("POST", "/mlmr/models/{id}/artifact", O),
    _rule("POST", "/mlmr/coordination-groups", O),
    # Wave 2: the six governance decisions (Approval/Certification/
    # Promotion/Rollback plus the submit/reject pair framing approval) are
    # admin-only, the same elevated stakes DEPRECATE/RETIRE already get —
    # everything else `advance` can fire (the automatic TRAINING_COMPLETE/
    # VALIDATION_COMPLETE/EMULATION_COMPLETE-style transitions) is operator.
    _rule("POST", "/aimgf/models/{id}/advance", A, query_match={"event": "DEPRECATE"}),
    _rule("POST", "/aimgf/models/{id}/advance", A, query_match={"event": "RETIRE"}),
    _rule("POST", "/aimgf/models/{id}/advance", A, query_match={"event": "SUBMIT_FOR_APPROVAL"}),
    _rule("POST", "/aimgf/models/{id}/advance", A, query_match={"event": "APPROVE"}),
    _rule("POST", "/aimgf/models/{id}/advance", A, query_match={"event": "REJECT"}),
    _rule("POST", "/aimgf/models/{id}/advance", A, query_match={"event": "CERTIFY"}),
    _rule("POST", "/aimgf/models/{id}/advance", A, query_match={"event": "PROMOTE"}),
    _rule("POST", "/aimgf/models/{id}/advance", A, query_match={"event": "ROLLBACK"}),
    _rule("POST", "/aimgf/models/{id}/(advance|inference-jobs)", O),
    _rule("POST", "/aimgf/models/{id}/runtime/terminate", A),  # tearing down a runtime is destructive, like the DELETEs above
    _rule("POST", "/aimgf/models/{id}/runtime/(deploy|activate|scale)", O),
    _rule("POST", "/mllf/models/{id}/deploy", O),
    _rule("POST", "/aimgf/inference-jobs/{id}/resolve", O),
    _rule("POST", "/aimgf/training-jobs", O),
    _rule("DELETE", "/aimgf/training-jobs/{id}", O),        # cancel, not a hard delete
    _rule("POST", "/aimgf/training-jobs/{id}/model-metrics", O),
    _rule("POST", "/aimgf/training-jobs/{id}/(suspend|resume)", O),  # Wave 3: same tier as cancel
    _rule("POST", "/aimgf/validation-jobs", O),
    _rule("POST", "/aimgf/validation-jobs/{id}/complete", O),
    _rule("POST", "/aimgf/emulation-jobs", O),
    _rule("POST", "/aimgf/emulation-jobs/{id}/complete", O),
    _rule("POST", "/aimgf/(feature-groups|mlmf/subscriptions)", O),
    _rule("POST", "/aimgf/mlmf/subscriptions/{id}/reports", A),  # test-data injection

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
    # Wave 9 (W9-01..06): the vendor capability registry, CM schema
    # descriptors and cell guards are inventory/onboarding data — admin.
    _rule("POST", "/ran-nf-oam/(cm-schemas|vendor-onboarding)", A),
    _rule("PUT", "/ran-nf-oam/vendor-capabilities/{id}", A),
    _rule("DELETE", "/ran-nf-oam/vendor-capabilities/{id}", A),
    _rule("PUT", "/ran-nf-oam/managed-entities/{id}/cells/{id}/guards", A),
    _rule("DELETE", "/ran-nf-oam/managed-entities/{id}/cells/{id}/guards", A),

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
    # Wave 3 (docs/ARCHITECTURE.md (DME)): ingesting a real data
    # payload is a producer-side operation, same tier as production-
    # capabilities/offers above. Mediating an O1 action is consumer-side
    # (an rApp's AI/ML decision) — operator, mirroring ran-nf-oam's own
    # POST /config-jobs identity-pinning above, since this route forwards
    # to exactly that one.
    _rule("POST", "/dme/data-jobs/{id}/records", A),
    _rule("POST", "/dme/actions", O,
          json_overrides=lambda u: {"requestedBy": f"smo-gui:{u.username}"}),

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

    # --- Intent Service (formerly Policy Mgmt — renamed in Wave 1 of the
    # AI Platform Service Decomposition; see docs/ARCHITECTURE.md (Intent Service))
    _rule("POST", "/intent-service/intents", O, json_overrides=lambda u: {"rmioId": GUI_RMIO_ID}),
    _rule("PATCH", "/intent-service/intents/{id}/admin-state", O, json_overrides=lambda u: {"requesterId": GUI_RMIO_ID}),
    _rule("DELETE", "/intent-service/intents/{id}", A),
    # RMIH registration is framework-internal only (D-SEC-POLICY-1: SO/SA SMOS
    # identities) and fulfilment reports come from an RMIH, so both are admin
    # acting on the framework's behalf (call flow 09)
    _rule("POST", "/intent-service/intent-handling-functions", A),
    _rule("DELETE", "/intent-service/intent-handling-functions/{id}", A),
    _rule("POST", "/intent-service/intent-reports", A),
    # rApp autonomy modes (HISTORY.md OI-6.3; Wave 8 W8-08): an operator
    # requests a dispatch, and scopes (resolve) or rejects an ASSIST one
    # left AWAITING_SCOPE — who rejected is pinned to the GUI identity.
    _rule("POST", "/intent-service/autonomy-dispatches", O),
    _rule("POST", "/intent-service/autonomy-dispatches/{id}/resolve", O),
    _rule("POST", "/intent-service/autonomy-dispatches/{id}/reject", O,
          json_overrides=lambda u: {"rejectedBy": f"smo-gui:{u.username}"}),

    # --- RAN Analytics / MDAF (Wave 1 split: reports/subscriptions moved
    # to mdaf/, producer registration stays in ran-analytics/)
    _rule("POST", "/mdaf/subscriptions", O),
    _rule("DELETE", "/mdaf/subscriptions/{id}", O),
    _rule("POST", "/ran-analytics/producers", A),                    # producer side of call flow 08
    _rule("POST", "/mdaf/reports", A),

    # --- SO / SA SMOS
    _rule("POST", "/so-smos/orders", O),
    _rule("POST", "/so-smos/orders/{id}/cancel", O),
    _rule("POST", "/sa-smos/monitors", O),
    _rule("POST", "/sa-smos/monitors/{id}/(evaluate|escalate)", O),
    _rule("POST", "/sa-smos/monitors/{id}/remedial-actions", O,
          query_overrides=lambda u: {"requester_is_admin": "true" if u.role == Role.ADMIN else "false"}),

    # --- Wave 10.1: the EnergySaving reference rApp (W10-15/W10-24). Driving
    # its loop and lifecycle is operator work (certification itself stays an
    # AIMgF governance decision); a manual override is attributed to the GUI
    # user; seeding Digital Twin data is test-data injection.
    _rule("POST", "/energy-saving-rapp/instances/{id}/(start|evaluate|reconcile)", O),
    _rule("POST", "/energy-saving-rapp/instances/{id}/lifecycle/(train|validate|emulate|deploy)", O),
    _rule("POST", "/energy-saving-rapp/instances/{id}/cells/{id}/override", O,
          json_overrides=lambda u: {"operator": f"smo-gui:{u.username}"}),
    _rule("DELETE", "/energy-saving-rapp/instances/{id}/cells/{id}/override", O),
    _rule("POST", "/energy-saving-rapp/sim-producer/(register|publish)", A),
    # --- Wave 10.2: the Mobility Optimization reference rApp — same split
    _rule("POST", "/mobility-optimization-rapp/instances/{id}/(start|evaluate|reconcile)", O),
    _rule("POST", "/mobility-optimization-rapp/instances/{id}/lifecycle/(train|validate|emulate|deploy)", O),
    _rule("POST", "/mobility-optimization-rapp/sim-producer/(register|publish)", A),
    # --- Wave 10.3: the Coverage Optimization reference rApp — same split
    _rule("POST", "/coverage-optimization-rapp/instances/{id}/(start|evaluate|reconcile)", O),
    _rule("POST", "/coverage-optimization-rapp/instances/{id}/lifecycle/(train|validate|emulate|deploy)", O),
    _rule("POST", "/coverage-optimization-rapp/sim-producer/(register|publish)", A),
    # --- Wave 10.4: the Traffic Steering reference rApp — same split
    _rule("POST", "/traffic-steering-rapp/instances/{id}/(start|evaluate|reconcile)", O),
    _rule("POST", "/traffic-steering-rapp/instances/{id}/lifecycle/(train|validate|emulate|deploy)", O),
    _rule("POST", "/traffic-steering-rapp/sim-producer/(register|publish)", A),

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
