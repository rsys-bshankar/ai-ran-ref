"""The permission table on its own (rbac.py), independent of HTTP.
Run with: cd smo/gui-bff && PYTHONPATH=. python -m pytest tests -q
"""

import json
import sys
from pathlib import Path

import pytest

from app.rbac import MODULES, RULES, Role, decide

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from export_permissions import FIXTURE, export  # noqa: E402


def allowed(method, path, role, **query):
    return decide(method, path, {k: [v] for k, v in query.items()}, Role(role)).allowed


@pytest.mark.parametrize("module", MODULES)
def test_every_module_is_readable_by_a_viewer(module):
    assert allowed("GET", f"/{module}/health", "viewer")


@pytest.mark.parametrize("method,path,minimum", [
    ("POST", "/onboarding/packages", "operator"),
    ("POST", "/onboarding/packages/p/prime", "operator"),
    ("DELETE", "/onboarding/packages/p", "admin"),
    ("POST", "/rapp-mgmt/instances", "operator"),
    ("POST", "/rapp-mgmt/instances/i/upgrade", "operator"),
    ("POST", "/rapp-mgmt/instances/i/recover", "operator"),
    ("POST", "/rapp-mgmt/instances/i/terminate", "admin"),
    ("DELETE", "/rapp-mgmt/instances/i", "admin"),
    ("POST", "/aimgf/training-jobs", "operator"),
    ("POST", "/aimgf/models/m/advance", "operator"),
    ("POST", "/aimgf/training-jobs/j/complete", "operator"),
    ("DELETE", "/mlmr/models/m", "admin"),
    ("PATCH", "/ran-nf-oam/alarms/a/ack", "operator"),
    ("PATCH", "/ran-nf-oam/alarms/a/clear", "operator"),
    ("POST", "/ran-nf-oam/alarms/ingest", "admin"),
    ("POST", "/sa-smos/monitors/m/evaluate", "operator"),
    ("DELETE", "/a1-related/policies/p", "admin"),
    ("DELETE", "/nfo/deployments/d", "admin"),
    ("GET", "/aimgf/feature-groups", "operator"),
    # GUI pass 2
    ("POST", "/dme/data-jobs", "operator"),
    ("DELETE", "/dme/data-jobs/j", "operator"),
    ("POST", "/dme/offers", "admin"),
    ("POST", "/dme/offers/o/notify", "admin"),
    ("POST", "/a1-related/policies/subscriptions", "operator"),
    ("DELETE", "/a1-related/policies/subscriptions/s", "operator"),
    ("POST", "/a1-related/ei-types/register", "admin"),
    ("PUT", "/a1-related/services", "admin"),
    ("POST", "/sme/provider-registrations", "admin"),
    ("POST", "/sme/published-apis/v1/apf/service-apis", "admin"),
    ("DELETE", "/sme/published-apis/v1/apf/service-apis/s", "admin"),
    ("POST", "/sme/invoker-registrations", "admin"),
    ("PUT", "/sme/trusted-invokers/t", "admin"),
    ("POST", "/sme/trusted-invokers/t/delete", "admin"),
    ("POST", "/sme/capif-events/v1/sub/subscriptions", "operator"),
    ("POST", "/onboarding/packages/p/usage/start", "admin"),
    ("POST", "/onboarding/packages/p/usage/r/stop", "admin"),
    ("POST", "/ran-nf-oam/o1-adaptor-endpoints/e/heartbeat", "admin"),
    ("POST", "/intent-service/intent-handling-functions", "admin"),
    ("POST", "/intent-service/intent-reports", "admin"),
    ("POST", "/ran-analytics/producers", "admin"),
    ("POST", "/mdaf/reports", "admin"),
    ("POST", "/focom/inventory/subscriptions", "operator"),
    ("POST", "/dme/production-capabilities", "admin"),
    # Wave 3 (docs/ARCHITECTURE.md (DME))
    ("POST", "/dme/data-jobs/j/records", "admin"),
    ("POST", "/dme/actions", "operator"),
    ("POST", "/aimgf/training-jobs/j/suspend", "operator"),
    ("POST", "/aimgf/training-jobs/j/resume", "operator"),
    # Wave 8 (W8-08): ASSIST dispatches are scoped or rejected by an operator
    ("POST", "/intent-service/autonomy-dispatches", "operator"),
    ("POST", "/intent-service/autonomy-dispatches/d/resolve", "operator"),
    ("POST", "/intent-service/autonomy-dispatches/d/reject", "operator"),
    # Wave 9 (W9-01..06): vendor registry, CM schemas and cell guards are admin
    ("POST", "/ran-nf-oam/cm-schemas", "admin"),
    ("POST", "/ran-nf-oam/vendor-onboarding", "admin"),
    ("PUT", "/ran-nf-oam/vendor-capabilities/acme", "admin"),
    ("DELETE", "/ran-nf-oam/vendor-capabilities/acme", "admin"),
    ("PUT", "/ran-nf-oam/managed-entities/me-1/cells/1/guards", "admin"),
    ("GET", "/ran-nf-oam/cell-guards", "viewer"),
    # Wave 10.1: the EnergySaving rApp's operator API
    ("GET", "/energy-saving-rapp/instances/i/dashboard", "viewer"),
    ("POST", "/energy-saving-rapp/instances/i/evaluate", "operator"),
    ("POST", "/energy-saving-rapp/instances/i/lifecycle/train", "operator"),
    ("POST", "/energy-saving-rapp/instances/i/cells/101/override", "operator"),
    ("DELETE", "/energy-saving-rapp/instances/i/cells/101/override", "operator"),
    ("POST", "/energy-saving-rapp/sim-producer/publish", "admin"),
    # Wave 10.2: the Mobility Optimization rApp
    ("GET", "/mobility-optimization-rapp/instances/i/dashboard", "viewer"),
    ("POST", "/mobility-optimization-rapp/instances/i/evaluate", "operator"),
    ("POST", "/mobility-optimization-rapp/instances/i/lifecycle/deploy", "operator"),
    ("POST", "/mobility-optimization-rapp/sim-producer/publish", "admin"),
    # AI-10.x: the safeguards of an rApp
    ("GET", "/rapp-mgmt/instances/i/safeguards", "viewer"),
    ("GET", "/ran-nf-oam/safeguard-refusals", "viewer"),
    ("GET", "/ran-nf-oam/rapp-kill", "viewer"),
    ("PUT", "/rapp-mgmt/instances/i/kill", "operator"),
    ("DELETE", "/rapp-mgmt/instances/i/kill", "admin"),
    ("PUT", "/ran-nf-oam/rapp-limits/i", "admin"),
    ("DELETE", "/ran-nf-oam/rapp-limits/i", "admin"),
    ("POST", "/ran-nf-oam/safeguard-subscriptions", "admin"),
    ("DELETE", "/ran-nf-oam/safeguard-subscriptions/s", "admin"),
    ("POST", "/ran-nf-oam/safeguard-refusals/purge", "admin"),
    # change management: rollback, staged jobs, KPIs
    ("POST", "/ran-nf-oam/config-jobs/j/rollback", "operator"),
    ("POST", "/ran-nf-oam/config-jobs/j/continue", "operator"),
    ("POST", "/ran-nf-oam/config-jobs/j/halt", "operator"),
    ("POST", "/ran-nf-oam/config-jobs/j/abort", "operator"),
    ("GET", "/ran-nf-oam/config-jobs/j", "viewer"),
    ("GET", "/ran-nf-oam/kpi-definitions", "viewer"),
    ("GET", "/ran-nf-oam/kpi-schedules", "viewer"),
    ("PUT", "/ran-nf-oam/kpi-definitions/k", "admin"),
    ("DELETE", "/ran-nf-oam/kpi-definitions/k", "admin"),
    ("POST", "/ran-nf-oam/kpi-definitions/standard", "admin"),
    ("PUT", "/ran-nf-oam/kpi-schedules/s", "admin"),
    ("DELETE", "/ran-nf-oam/kpi-schedules/s", "admin"),
    # Wave 10.3: the Coverage Optimization rApp
    ("GET", "/coverage-optimization-rapp/instances/i/dashboard", "viewer"),
    ("POST", "/coverage-optimization-rapp/instances/i/evaluate", "operator"),
    ("POST", "/coverage-optimization-rapp/instances/i/lifecycle/train", "operator"),
    ("POST", "/coverage-optimization-rapp/sim-producer/register", "admin"),
    # Wave 10.4: the Traffic Steering rApp
    ("GET", "/traffic-steering-rapp/instances/i/relations", "viewer"),
    ("POST", "/traffic-steering-rapp/instances/i/evaluate", "operator"),
    ("POST", "/traffic-steering-rapp/instances/i/lifecycle/emulate", "operator"),
    ("POST", "/traffic-steering-rapp/sim-producer/publish", "admin"),
])
def test_minimum_role_per_route(method, path, minimum):
    order = ["viewer", "operator", "admin"]
    for role in order:
        assert allowed(method, path, role) == (order.index(role) >= order.index(minimum)), (method, path, role)


def test_deprecate_is_admin_only_via_query_match():
    assert allowed("POST", "/aimgf/models/m/advance", "operator", event="APPROVE_TRAINING")
    assert not allowed("POST", "/aimgf/models/m/advance", "operator", event="DEPRECATE")
    assert allowed("POST", "/aimgf/models/m/advance", "admin", event="DEPRECATE")


def test_any_duplicated_value_triggers_the_query_match():
    decision = decide("POST", "/aimgf/models/m/advance", {"event": ["CERTIFY", "DEPRECATE"]}, Role.OPERATOR)
    assert not decision.allowed and decision.required_role == Role.ADMIN


@pytest.mark.parametrize("method,path", [
    ("POST", "/sme/oauth2/token"),
    ("POST", "/sme/oauth2/introspect"),
    ("POST", "/a1-related/dme-jobs"),
    ("POST", "/ran-nf-oam/dme-jobs"),
    ("POST", "/nfo/deployments"),
    ("PATCH", "/dme/data-jobs/j"),
    ("POST", "/sme/trusted-invokers/t/revoke"),
    ("GET", "/dme-push/anything"),
    ("GET", "/unknown/thing"),
    ("PATCH", "/rapp-mgmt/instances/i"),
])
def test_unlisted_routes_are_not_exposed_to_anyone(method, path):
    decision = decide(method, path, {}, Role.ADMIN)
    assert not decision.allowed and decision.required_role is None


def test_path_ids_cannot_span_segments():
    assert not allowed("POST", "/rapp-mgmt/instances/a/b/terminate", "admin")


def test_every_mutating_rule_requires_at_least_operator():
    assert all(r.role != Role.VIEWER for r in RULES if r.method != "GET")


def test_spa_permissions_fixture_matches_the_live_table():
    """smo/gui's Vitest suite checks the SPA's evaluator against this
    snapshot of the table; it must be the real one."""
    assert json.loads(FIXTURE.read_text()) == export(), "stale fixture: run scripts/export_permissions.py"


def test_the_gui_cannot_stop_an_rapp_as_somebody_else():
    decision = decide("PUT", "/rapp-mgmt/instances/i/kill", {}, Role.OPERATOR)
    assert decision.allowed and decision.rule.json_overrides(type("U", (), {"username": "alice", "role": Role.OPERATOR})()) == {"requestedBy": "smo-gui:alice"}


@pytest.mark.parametrize("path", ["/ran-nf-oam/config-jobs/j/rollback", "/ran-nf-oam/config-jobs/j/continue", "/ran-nf-oam/config-jobs/j/halt", "/ran-nf-oam/config-jobs/j/abort"])
def test_a_job_action_is_always_attributed_to_the_gui_user_and_an_admin_holds_the_msac_tier(path):
    rule = decide("POST", path, {}, Role.OPERATOR).rule
    user = lambda role: type("U", (), {"username": "alice", "role": role})()  # noqa: E731
    assert rule.json_overrides(user(Role.OPERATOR))["requestedBy"] == "smo-gui:alice"
    if path.endswith("rollback"):
        assert rule.json_overrides(user(Role.OPERATOR))["msacRole"] is None and rule.json_overrides(user(Role.ADMIN))["msacRole"] == "admin"


def test_kpi_check_and_publish_are_not_exposed_to_the_gui():
    for path in ("/ran-nf-oam/config-jobs/j/kpi-check", "/ran-nf-oam/kpis/k/publish", "/ran-nf-oam/config-jobs/advance-due"):
        assert not decide("POST", path, {}, Role.ADMIN).allowed
