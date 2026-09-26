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
    ("POST", "/ai-ml-workflow/training-jobs", "operator"),
    ("POST", "/ai-ml-workflow/models/m/advance", "operator"),
    ("DELETE", "/ai-ml-workflow/models/m", "admin"),
    ("PATCH", "/ran-nf-oam/alarms/a/ack", "operator"),
    ("PATCH", "/ran-nf-oam/alarms/a/clear", "operator"),
    ("POST", "/ran-nf-oam/alarms/ingest", "admin"),
    ("POST", "/sa-smos/monitors/m/evaluate", "operator"),
    ("DELETE", "/a1-related/policies/p", "admin"),
    ("DELETE", "/nfo/deployments/d", "admin"),
    ("GET", "/ai-ml-workflow/feature-groups", "operator"),
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
    ("POST", "/ran-analytics/reports", "admin"),
    ("POST", "/focom/inventory/subscriptions", "operator"),
    ("POST", "/dme/production-capabilities", "admin"),
])
def test_minimum_role_per_route(method, path, minimum):
    order = ["viewer", "operator", "admin"]
    for role in order:
        assert allowed(method, path, role) == (order.index(role) >= order.index(minimum)), (method, path, role)


def test_deprecate_is_admin_only_via_query_match():
    assert allowed("POST", "/ai-ml-workflow/models/m/advance", "operator", event="ACTIVATE")
    assert not allowed("POST", "/ai-ml-workflow/models/m/advance", "operator", event="DEPRECATE")
    assert allowed("POST", "/ai-ml-workflow/models/m/advance", "admin", event="DEPRECATE")


def test_any_duplicated_value_triggers_the_query_match():
    decision = decide("POST", "/ai-ml-workflow/models/m/advance", {"event": ["CERTIFY", "DEPRECATE"]}, Role.OPERATOR)
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
