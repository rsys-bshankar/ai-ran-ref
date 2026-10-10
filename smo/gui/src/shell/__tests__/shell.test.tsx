// @vitest-environment jsdom
/** Tests of the console shell: the breadcrumb table, the ⌘K jump list and the sidebar's grouped navigation with summary badges.
 * Uses the fake BFF of src/testing/bff.tsx; the sidebar needs an `AuthProvider`, so the test mounts one over the fake `/me`.
 * Run: `npx vitest run src/shell`. */
import { afterEach, describe, expect, it } from "vitest";

import { AuthProvider } from "../../auth/AuthContext";
import { fakeBff, mountWith } from "../../testing/bff";
import { cleanup, settle } from "../../testing/dom";
import { jumpsFor } from "../GlobalSearch";
import { crumbOf, NAV, NAV_GROUPS } from "../nav";
import { Sidebar } from "../Sidebar";

afterEach(cleanup);

describe("navigation table", () => {
  // The sidebar is grouped as the redesign draws it, and the new pages are reachable.
  it("groups pages into Overview, Automation, Network and Account", () => {
    expect(NAV_GROUPS.map((g) => g.title)).toEqual(["Overview", "Automation", "Network", "Account"]);
    for (const to of ["/topology", "/configuration", "/software", "/preferences"]) expect(NAV.map((n) => n.to)).toContain(to);
  });

  // The breadcrumb names the section and the page, including detail pages that are not in the sidebar.
  it("derives the breadcrumb from the path", () => {
    expect(crumbOf("/")).toEqual(["Overview", "Dashboard"]);
    expect(crumbOf("/alarms")).toEqual(["Network", "Alarms"]);
    expect(crumbOf("/rapps/123")).toEqual(["rApps", "rApp detail"]);
    expect(crumbOf("/elements/du-1")).toEqual(["RAN topology", "Element detail"]);
  });
});

describe("jump search", () => {
  const pages = NAV.map((n) => ({ label: n.label, hint: "Page", to: n.to }));
  // Typed text matches page names; an identifier offers direct jumps instead of a fake search.
  it("matches pages and offers id jumps", () => {
    expect(jumpsFor("alar", pages).map((j) => j.to)).toContain("/alarms");
    const uuid = "0b9f3f1e-1111-4222-8333-444455556666";
    expect(jumpsFor(uuid, pages).map((j) => j.to)).toEqual([`/decisions/${uuid}`, `/rapps/${uuid}`]);
    expect(jumpsFor("du-17", pages).map((j) => j.to)).toEqual(["/elements/du-17", "/alarms?me=du-17"]);
  });
});

describe("Sidebar", () => {
  // Badges are the BFF's true counts (critical + major alarms, waiting approvals), not the length of a first page.
  it("shows grouped entries and summary badges", async () => {
    fakeBff({
      "GET /me": { username: "ops", role: "operator", local: true, totpEnrolled: true },
      "GET /permissions": { role: "operator", rules: [] },
      "GET /modules/status": { checkedAt: "", modules: [{ module: "sme", healthy: true }, { module: "dme", healthy: true }] },
      "GET /me/pins": { max: 5, items: [] },
      "GET /summary/nav": { page: "nav", computedAt: "", partial: [], counts: { "alarms.critical": 1200, "alarms.major": 34, "approvals.PENDING": 18, "configJobs.HALTED": 0, "campaigns.HALTED": null } },
    });
    const { container } = await mountWith(<AuthProvider><Sidebar onChangePassword={() => {}} /></AuthProvider>);
    await settle(8);
    const text = container.textContent ?? "";
    expect(text).toContain("AI-RAN SMO");
    expect(text).toContain("SMO 2/2");
    const counts = Array.from(container.querySelectorAll(".nav-count")).map((n) => n.textContent);
    expect(counts).toEqual(["18", "999+"]);
    expect(container.querySelectorAll(".nav-group .eyebrow")).toHaveLength(4);
  });
});
