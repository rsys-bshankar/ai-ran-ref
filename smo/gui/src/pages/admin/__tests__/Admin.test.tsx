// @vitest-environment jsdom
/** Tests of the Admin page (pages/admin): the Users tab (sign-in method, Add user dialog), the Audit log (server paging, filters as query params,
 * HTTP outcome badge) and the RAN access control (MSAC) tab listing RAN NF OAM's roles, identities and access rules. Uses the fake BFF of
 * `src/testing/bff.tsx` and the permission fixture. Run: `npx vitest run src/pages/admin` from smo/gui. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith } from "../../../testing/bff";
import { byText, cleanup, click, settle } from "../../../testing/dom";
import { Admin } from "../index";
import { auditOutcome } from "../sections/AuditLog";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const page = <T,>(items: T[], total = items.length) => ({ items, total, limit: 50, offset: 0 });

/** An admin's BFF with two users, a page of audit entries and one of each MSAC object. */
function bff() {
  return fakeBff({
    "GET /me": { username: "root", role: "admin", csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role: "admin", rules },
    "GET /admin/users": [
      { username: "root", role: "admin", active: true, createdAt: "2026-10-01T00:00:00Z", totpEnrolled: true },
      { username: "oidc:jane.doe", role: "operator", active: true, createdAt: "2026-10-02T00:00:00Z" },
    ],
    "GET /admin/audit": page([
      { id: 2, at: "2026-10-09T10:00:00Z", username: "viewer1", role: "viewer", action: "DENIED", method: "DELETE", path: "/smo/rapp-mgmt/instances/x", statusCode: 403, detail: "refused by role" },
      { id: 1, at: "2026-10-09T09:00:00Z", username: "root", role: "admin", action: "LOGIN", method: null, path: null, statusCode: null, detail: null },
    ], 812),
    "GET /smo/ran-nf-oam/msac/roles": page([{ id: "r1", attributes: { roleName: "cm-writer", accessRulesList: ["a1", "a2"] } }]),
    "GET /smo/ran-nf-oam/msac/identities": page([{ id: "i1", attributes: { identityType: "MACHINEUSER", identityName: "es-rapp", roleList: ["r1"] } }]),
    "GET /smo/ran-nf-oam/msac/access-rules": page([{ id: "a1", attributes: { ruleName: "deny-cells", dataNodeSelector: "/ManagedElement=*/NRCellDU=*", operations: ["UPDATE"], actions: "DENY" } }]),
  });
}

describe("Admin", () => {
  // the Users tab tells a local account from an identity-provider one and opens the Add user dialog
  it("lists users with their sign-in method and opens Add user", async () => {
    bff();
    const { container } = await mountWith(<AuthProvider><Admin /></AuthProvider>);
    await settle();
    const rows = Array.from(container.querySelectorAll("[data-section='admin.users'] tbody tr"));
    expect(rows.map((r) => r.textContent)).toEqual([expect.stringContaining("Local"), expect.stringContaining("SSO")]);
    expect(container.querySelector("[data-section='admin.roles']")).not.toBeNull();
    await click(byText(container, "button", "Add user")!);
    expect(document.querySelector("[role=dialog]")?.textContent).toMatch(/Initial password/);
  });

  // the audit log is paged by the server (limit/offset sent, the total shown) and each row has an outcome badge
  it("pages the audit log on the server and shows the HTTP outcome", async () => {
    const calls = bff();
    window.location.hash = "#audit";
    const { container } = await mountWith(<AuthProvider><Admin /></AuthProvider>);
    await settle();
    const audit = calls.find((c) => c.path === "/admin/audit")!;
    expect(audit.query.get("limit")).not.toBeNull();
    expect(audit.query.get("offset")).toBe("0");
    expect(container.textContent).toMatch(/of 812/);
    expect(container.querySelector("[data-section='admin.audit'] .badge.b-bad")?.textContent).toBe("403");
    expect(calls.some((c) => c.path.startsWith("/smo/ran-nf-oam/msac"))).toBe(false);      // other tabs' queries do not run
  });

  // the MSAC tab lists RAN NF OAM's roles, identities and access rules, and offers no delete the BFF would refuse
  it("lists MSAC roles, identities and access rules", async () => {
    const calls = bff();
    window.location.hash = "#msac";
    const { container } = await mountWith(<AuthProvider><Admin /></AuthProvider>);
    await settle();
    const text = container.querySelector("[data-section='admin.msac']")?.textContent ?? "";
    expect(text).toContain("cm-writer");
    expect(text).toContain("es-rapp");
    expect(text).toContain("MACHINEUSER");
    expect(text).toContain("deny-cells");
    expect(container.querySelector("[data-section='admin.msac.rules'] .badge.b-bad")?.textContent).toBe("DENY");
    expect(byText(container, "button", "Delete")).toBeNull();
    expect(calls.some((c) => c.path === "/admin/users")).toBe(false);
  });

  // the outcome rule: a status code wins, a refused sign-in without one reads "refused", anything else "ok"
  it("derives the audit outcome from the status code or the event", () => {
    expect(auditOutcome({ action: "PROXY", statusCode: 201 })).toEqual({ tone: "ok", text: "201" });
    expect(auditOutcome({ action: "LOGIN_FAILED", statusCode: null })).toEqual({ tone: "bad", text: "refused" });
    expect(auditOutcome({ action: "LOGOUT", statusCode: null })).toEqual({ tone: "ok", text: "ok" });
  });
});
