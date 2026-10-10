// @vitest-environment jsdom
/** Tests of the Dashboard (pages/dashboard): tiles and "+N more" read the summary's true totals, not the length of a list page; module health keeps
 * the "Modules healthy n/m" text the smoke script reads; the health map drills region → elements and back; the first load stays within its call
 * budget, with the trends loaded only on demand. Uses the fake BFF of `src/testing/bff.tsx`. Run: `npx vitest run src/pages/dashboard` from smo/gui. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith, type Call } from "../../../testing/bff";
import { byText, cleanup, click, settle } from "../../../testing/dom";
import { Dashboard } from "../index";
import { regionsOf } from "../sections/HealthMap";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; });

const page = <T,>(items: T[]) => ({ items, limit: 3, offset: 0, hasMore: true });
const COUNTS = {
  "alarms.critical": 37, "alarms.major": 120, "alarms.minor": 300, "alarms.warning": 827, "alarms.cleared": 16, "alarms.total": 1300,
  "ocloudAlarms.total": 4, "approvals.PENDING": 18, "decisions24h.total": 2416, "decisions24h.DIRECT": 1782, "decisions24h.APPROVED": 512,
  "decisions24h.REJECTED": 0, "decisions24h.EXPIRED": 0, "decisions24h.REFUSED": 122, "escalations.total": 4, "mlmfBreaches.total": 3, "models.total": 214,
  "trainingJobs.total": 9, "elements.total": 4112, "instances.RUNNING": 471, "instances.FAULTED": 6, "instances.total": 487,
};
const alarm = (i: number) => ({ alarmId: `a${i}`, managedElementRef: `du-0${i}`, severity: "critical", ackState: "UNACKNOWLEDGED", specificProblem: "LOS", raisedAt: "2026-10-09T10:00:00Z" });
const element = (ref: string, region: string | null) => ({ managedElementRef: ref, managedFunctionRef: null, entityType: "DU", vendorName: "acme", region, tenant: null, supportedServices: [] });

/** A BFF whose lists hold three rows each while the summary says there are many more. */
function bff() {
  return fakeBff({
    "GET /me": { username: "ana", role: "operator", csrfToken: "c", local: true },
    "GET /permissions": { role: "operator", rules },
    "GET /summary/dashboard": { page: "dashboard", computedAt: "2026-10-09T10:00:00Z", counts: COUNTS, partial: [] },
    "GET /modules/status": { checkedAt: "2026-10-09T10:00:00Z", modules: [
      { module: "ran-nf-oam", healthy: true, latencyMs: 4, statusCode: 200, error: null, ready: true, version: "1", buildSha: "abc", builtAt: "x" },
      { module: "aimgf", healthy: false, latencyMs: 0, statusCode: 503, error: null, ready: null, version: null, buildSha: null, builtAt: null },
    ] },
    "GET /smo/ran-nf-oam/alarms": page([alarm(1), alarm(2), alarm(3)]),
    "GET /smo/ran-nf-oam/rapp-approvals": page([]),
    "GET /smo/aimgf/mlmf/reports": page([]),
    "GET /smo/sa-smos/remedial-actions": page([]),
    "GET /smo/ran-nf-oam/decision-records": page([]),
    "GET /smo/ran-nf-oam/managed-entities": (c: Call) => c.query.get("region") === "eu-west"
      ? { items: [element("du-01", "eu-west")], limit: 50, offset: 0, total: 1 }
      : { items: [element("du-01", "eu-west"), element("du-02", "eu-west"), element("du-03", "us-east"), element("du-04", null)], limit: 100, offset: 0, hasMore: false },
    "GET /smo/rapp-mgmt/instances": page([]),
  });
}

/** The text of the tile labelled `label`. */
const tile = (root: HTMLElement, label: string) =>
  Array.from(root.querySelectorAll(".kpi")).find((k) => k.querySelector(".kpi-l")?.textContent === label)?.textContent ?? "";

describe("Dashboard", () => {
  // every tile shows the summary's total (1,284 open = total − cleared), never the three rows a list page returned
  it("reads the tiles from the summary counts, not from list length", async () => {
    bff();
    const { container } = await mountWith(<AuthProvider><Dashboard /></AuthProvider>);
    await settle(6);
    expect(tile(container, "Open alarms")).toContain("1,284");
    expect(tile(container, "Open alarms")).toContain("37 critical");
    expect(tile(container, "Autonomous actions · 24 h")).toContain("2,416");
    expect(tile(container, "Awaiting approval")).toContain("18");
    expect(tile(container, "Model guard breaches")).toContain("3");
    expect(tile(container, "Network health")).toContain("—");
    const attention = container.querySelector("[data-section='dashboard.attention']")!;
    expect(attention.querySelectorAll("li")).toHaveLength(3);
    expect(attention.textContent).toContain("+34 more");
  });

  // scripts/gui_smoke.py matches /Modules healthy\s*(\d+)\/(\d+)/ on the page text, and the #health anchor stays
  it("shows Modules healthy n/m and the readiness table under #health", async () => {
    bff();
    const { container } = await mountWith(<AuthProvider><Dashboard /></AuthProvider>);
    await settle(6);
    expect(container.textContent).toMatch(/Modules healthy\s*1\/2/);
    expect(container.querySelector("#health")).not.toBeNull();
    expect(container.querySelector("[data-section='dashboard.platform'] table")?.textContent).toContain("READY");
  });

  // a region tile drills into that region's elements (asked with ?region=) linking to the element page, and Back returns to the regions
  it("drills from a region to its elements and back", async () => {
    const calls = bff();
    const { container } = await mountWith(<AuthProvider><Dashboard /></AuthProvider>);
    await settle(6);
    const map = container.querySelector("[data-section='dashboard.map']") as HTMLElement;
    expect(map.textContent).toContain("2 elements");                       // complete sample: true counts per region
    await click(byText(map, "button", /eu-west/)!);
    await settle();
    expect(calls.some((c) => c.path === "/smo/ran-nf-oam/managed-entities" && c.query.get("region") === "eu-west")).toBe(true);
    expect(map.querySelector("a[href='/elements/du-01']")).not.toBeNull();
    await click(byText(map, "button", /Back to all regions/)!);
    expect(byText(map, "button", /us-east/)).not.toBeNull();
  });

  // the first load makes at most 8 data calls (summary, module health, four top-3 lists, decisions, one element page); trends wait for a click
  it("keeps the first load within its call budget and loads trends on demand", async () => {
    const calls = bff();
    const { container } = await mountWith(<AuthProvider><Dashboard /></AuthProvider>);
    await settle(6);
    const data = calls.filter((c) => c.path !== "/me" && c.path !== "/permissions");
    expect(new Set(data.map((c) => `${c.path}?${c.query}`)).size).toBeLessThanOrEqual(8);
    expect(calls.some((c) => c.path === "/smo/rapp-mgmt/instances")).toBe(false);
    for (const c of data.filter((d) => d.path.startsWith("/smo/") && d.path !== "/smo/ran-nf-oam/managed-entities")) expect(Number(c.query.get("limit"))).toBeLessThanOrEqual(6);
    await click(byText(container, "button", "Show trends")!);
    await settle();
    expect(calls.some((c) => c.path === "/smo/rapp-mgmt/instances" && c.query.get("state") === "RUNNING")).toBe(true);
  });

  // region names come from distinct elements; elements without a region are counted apart
  it("groups a page of elements by region", () => {
    expect(regionsOf([element("a", "x"), element("a", "x"), element("b", "x"), element("c", null)] as never)).toEqual({ regions: [{ name: "x", elements: 2 }], unset: 1 });
  });
});
