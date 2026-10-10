// @vitest-environment jsdom
/** Data & Exposure: the flow tab draws producers → types → consumers from DME types and one bounded page of jobs and offers (≤ 8 rows a column,
 * "+N other", a note when the page is cut), the jobs table is server-paged with its filters as query params and shows last delivery as a gap,
 * the old `#dme` / `#sme` tab ids still land, and the pure flow aggregation. Run: `npx vitest run src/pages/data` from smo/gui. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DataJob, DmeType } from "../../../api/types";
import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith } from "../../../testing/bff";
import { byText, cleanup, settle } from "../../../testing/dom";
import { Data } from "..";
import { buildFlow } from "../data/flow";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const type = (id: string, name: string, producerIds: string[]): DmeType => ({ dmeTypeId: id, dmeTypeIdStruct: {}, typeName: name, producerIds, typeStatus: "ENABLED" });
const job = (id: string, typeId: string, consumer: string): DataJob => ({ dataJobId: id, dmeTypeId: typeId, consumerId: consumer, dataDeliveryMode: "CONTINUOUS", dataDeliveryMethod: "PUSH_HTTP", productionJobDefinition: {}, deliveryDetails: {}, status: "ACTIVE" });
const TYPES = [type("t-1", "RAN.PmCell", ["ran-nf-oam"]), type("t-2", "RAN.Coverage", ["ran-analytics", "ran-nf-oam"])];
const JOBS = [job("j-1", "t-1", "energy-saving"), job("j-2", "t-1", "mobility"), job("j-3", "t-2", "energy-saving")];

/** The fake BFF: two types, three jobs on a page that says there are 900, one offer, and empty SME lists. */
function bff() {
  return fakeBff({
    "GET /me": { username: "ana", role: "admin", csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role: "admin", rules },
    "GET /smo/dme/dme-types": TYPES,
    "GET /smo/dme/data-jobs": { items: JOBS, total: 900, limit: 500, offset: 0 },
    "GET /smo/dme/offers": { items: [{ offerId: "o-1", dmeTypeId: "t-1", dataDeliveryMethodsOffered: ["PUSH_HTTP"], committedMethod: "PUSH_HTTP", dataAvailabilityNotificationUri: null, dataOfferTerminationNotificationUri: "x" }], total: 1, limit: 500, offset: 0 },
    "GET /smo/dme/production-capabilities": [],
    "GET /smo/dme/type-subscriptions": { items: [], total: 0, limit: 50, offset: 0 },
    "GET /smo/sme/*": { items: [], total: 0, limit: 50, offset: 0 },
  });
}

const open = () => mountWith(<AuthProvider><Data /></AuthProvider>);

describe("the flow & jobs tab", () => {
  // The default tab draws the three columns from the DME lists and says the jobs page was cut.
  it("draws producers, types and consumers and names a cut page", async () => {
    const calls = bff();
    const { container } = await open();
    await settle(8);
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("DME: flow & jobs");
    const labels = Array.from(container.querySelectorAll(".flow-label")).map((t) => t.textContent);
    expect(labels).toEqual(expect.arrayContaining(["ran-nf-oam", "ran-analytics", "RAN.PmCell", "RAN.Coverage", "energy-saving", "mobility"]));
    expect(container.querySelectorAll(".flow-band").length).toBeGreaterThan(0);
    expect(container.querySelector('[data-section="data.flow"]')?.textContent).toContain("first 500 of 900");
    const flowJobs = calls.find((c) => c.path === "/smo/dme/data-jobs" && c.query.get("limit") === "500");
    expect(flowJobs).toBeTruthy();
  });

  // The jobs table pages on the server, and its last-delivery column is a gap, not a time.
  it("pages the jobs on the server and shows last delivery as a gap", async () => {
    const calls = bff();
    const { container } = await open();
    await settle(8);
    const table = container.querySelector('[data-section="data.jobs"]')!;
    expect(Array.from(table.querySelectorAll("th")).map((h) => h.textContent)).toContain("Last delivery");
    expect(table.querySelectorAll("tbody tr").length).toBe(3);
    expect(table.querySelector(".pager")?.textContent).toContain("900");
    expect(calls.some((c) => c.path === "/smo/dme/data-jobs" && c.query.get("offset") === "0" && c.query.get("limit") !== "500")).toBe(true);
    expect(table.querySelector(".gap-note")).toBeTruthy();
  });
});

describe("the tabs", () => {
  // Links to /data#sme still open the SME tab, and only its own lists are read.
  it("opens the SME tab from its old hash and reads only SME lists", async () => {
    window.location.hash = "#sme";
    const calls = bff();
    const { container } = await open();
    await settle(6);
    expect(byText(container, '[role="tab"]', "SME: services & invokers")?.getAttribute("aria-selected")).toBe("true");
    expect(calls.some((c) => c.path.startsWith("/smo/dme/"))).toBe(false);
    expect(calls.some((c) => c.path === "/smo/sme/provider-registrations")).toBe(true);
  });
});

describe("buildFlow", () => {
  // A type served by two producers counts in both producers' bands; consumers are the jobs' consumer ids.
  it("aggregates jobs per producer, type and consumer", () => {
    const flow = buildFlow(TYPES, JOBS, []);
    expect(flow.producers.find((p) => p.key === "ran-nf-oam")?.value).toBe(3);
    expect(flow.producers.find((p) => p.key === "ran-analytics")?.value).toBe(1);
    expect(flow.types.map((t) => [t.label, t.value])).toEqual([["RAN.PmCell", 2], ["RAN.Coverage", 1]]);
    expect(flow.consumers.find((c) => c.key === "energy-saving")?.value).toBe(2);
    expect(flow.right.reduce((s, l) => s + l.value, 0)).toBe(3);
  });

  // Past `max` rows a column keeps the biggest max − 1 and folds the rest into one "+N other" row.
  it("folds a long column into +N other", () => {
    const many = Array.from({ length: 12 }, (_, i) => type(`t-${i}`, `T${i}`, ["p"]));
    const jobs = many.flatMap((t, i) => Array.from({ length: 12 - i }, (_, k) => job(`${t.dmeTypeId}-${k}`, t.dmeTypeId, `c-${i}`)));
    const flow = buildFlow(many, jobs, [], 8);
    expect(flow.types).toHaveLength(8);
    expect(flow.types[7].label).toBe("+5 other");
    expect(flow.types[7].folded).toBe(5);
    expect(flow.consumers).toHaveLength(8);
  });
});
