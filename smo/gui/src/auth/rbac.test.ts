import { describe, expect, it } from "vitest";

import fixture from "./permissions.fixture.json";
import { can, requiredRole, roleAtLeast, type PermissionRule, type Role } from "./rbac";

// The BFF's real table (gui-bff/scripts/export_permissions.py; the BFF's own
// test_rbac.py fails if this snapshot drifts). Evaluating it here proves the
// Python regexes — re.escape output included — behave the same in JS.
const RULES = fixture as PermissionRule[];

describe("requiredRole against the BFF's table", () => {
  it.each<[string, string, Role]>([
    ["GET", "/rapp-mgmt/instances", "viewer"],
    ["GET", "/ran-nf-oam/alarms", "viewer"],
    ["GET", "/mlmr/models/abc/artifact/2", "viewer"],
    ["POST", "/onboarding/packages", "operator"],
    ["POST", "/aimgf/training-jobs", "operator"],
    ["PATCH", "/ran-nf-oam/alarms/a1/ack", "operator"],
    ["PATCH", "/ran-nf-oam/alarms/a1/clear", "operator"],
    ["POST", "/sa-smos/monitors/m1/evaluate", "operator"],
    ["POST", "/rapp-mgmt/instances/i1/terminate", "admin"],
    ["DELETE", "/onboarding/packages/p1", "admin"],
    ["DELETE", "/nfo/deployments/d1", "admin"],
    ["GET", "/aimgf/feature-groups", "operator"],
  ])("%s %s needs %s", (method, path, role) => {
    expect(requiredRole(RULES, method, path)).toBe(role);
  });

  it("returns null for routes the GUI does not expose", () => {
    expect(requiredRole(RULES, "POST", "/sme/oauth2/token")).toBeNull();
    expect(requiredRole(RULES, "POST", "/nfo/deployments")).toBeNull();
    expect(requiredRole(RULES, "GET", "/dme-push/x")).toBeNull();
    expect(requiredRole(RULES, "GET", "/not-a-module")).toBeNull();
  });

  it("does not let an id span path segments", () => {
    expect(requiredRole(RULES, "POST", "/rapp-mgmt/instances/a/b/terminate")).toBeNull();
  });

  it("applies query matches: DEPRECATE is admin-only, other advances are operator", () => {
    expect(requiredRole(RULES, "POST", "/aimgf/models/m/advance", { event: "CERTIFY" })).toBe("operator");
    expect(requiredRole(RULES, "POST", "/aimgf/models/m/advance", { event: "DEPRECATE" })).toBe("admin");
    expect(requiredRole(RULES, "POST", "/aimgf/models/m/advance", { event: ["CERTIFY", "DEPRECATE"] })).toBe("admin");
  });
});

describe("can", () => {
  it("gates by role rank", () => {
    const terminate = ["POST", "/rapp-mgmt/instances/i/terminate"] as const;
    expect(can(RULES, "operator", ...terminate)).toBe(false);
    expect(can(RULES, "admin", ...terminate)).toBe(true);
    expect(can(RULES, "viewer", "POST", "/aimgf/training-jobs")).toBe(false);
    expect(can(RULES, "operator", "POST", "/aimgf/training-jobs")).toBe(true);
    expect(can(RULES, "viewer", "GET", "/nfo/deployments")).toBe(true);
  });

  it("denies everything without a role or for unexposed routes", () => {
    expect(can(RULES, undefined, "GET", "/nfo/deployments")).toBe(false);
    expect(can(RULES, "admin", "POST", "/sme/oauth2/token")).toBe(false);
  });

  it("is first-match-wins, like the BFF", () => {
    const rules: PermissionRule[] = [
      { method: "GET", pattern: "^/x/secret$", role: "admin", queryMatch: {} },
      { method: "GET", pattern: "^/x/.*$", role: "viewer", queryMatch: {} },
    ];
    expect(can(rules, "viewer", "GET", "/x/secret")).toBe(false);
    expect(can(rules, "viewer", "GET", "/x/other")).toBe(true);
  });

  it("orders roles viewer < operator < admin", () => {
    expect(roleAtLeast("admin", "operator")).toBe(true);
    expect(roleAtLeast("operator", "admin")).toBe(false);
    expect(roleAtLeast("viewer", "viewer")).toBe(true);
  });
});
