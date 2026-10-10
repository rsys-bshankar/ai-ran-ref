// @vitest-environment jsdom
/** Tests of the Decisions page (pages/decisions): the record table (filters as query parameters, the default 24 h range, paging), the summary
 * tiles, the CSV export link (current range, rApp and outcome; none for "All"), the chain panel that follows the selected row, the one-record route with its integrity check, and the config job's link back to its
 * record. Run: `npx vitest run src/pages/decisions`. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { ConfigJobDrawer } from "../../../components/ConfigJobDrawer";
import { fakeBff, mountWith, type Call } from "../../../testing/bff";
import { byText, cleanup, click, settle, type } from "../../../testing/dom";
import { DecisionDetail, Decisions } from "..";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const DID = "d1d1d1d1-0000-4000-8000-000000000001";
const JOB = "7a2c9f1b-2222-4b3c-8d4e-bbbbbbbbbbbb";
const APPROVAL = "6f1b8e0a-1111-4a2b-9c3d-aaaaaaaaaaaa";

/** A decision record as the API serves it; `extra` overrides fields. */
const record = (extra: Record<string, unknown> = {}) => ({
  decisionId: DID, occurredAt: "2026-10-09T10:00:00.000000Z", invokerId: "api-invoker-0a1b2c3d-4e5f", requestedBy: "energy-saving", disposition: "APPROVED", jobId: JOB,
  approvalId: APPROVAL, actionId: "act-1", inputsRef: "dme://data-jobs/42", modelVersion: "energy-saving 1.4.2", rationale: "PRB use under 5 percent for an hour",
  approvedBy: "smo-gui:alice", decidedBy: "smo-gui:alice", decidedAt: "2026-10-09T10:05:00.000000Z", managedElements: ["ME-1", "ME-2"], changeCount: 2, correlationId: "c-1",
  contentHash: "a".repeat(64), auditSeq: 7, ...extra,
});

/** The fake BFF of these tests: a viewer, two records, one record with its integrity check, the job, and the 24 h summary. */
function bff(overrides: Record<string, unknown> = {}) {
  return fakeBff({
    "GET /me": { username: "ana", role: "viewer", csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role: "viewer", rules },
    "GET /smo/ran-nf-oam/decision-records": { items: [record(), record({ decisionId: "d2", disposition: "DIRECT", approvedBy: null, approvalId: null, rationale: null, modelVersion: null })], limit: 25, offset: 0, hasMore: false },
    [`GET /smo/ran-nf-oam/decision-records/${DID}`]: { body: { ...record(), integrity: { status: "VERIFIED", auditSeq: 7, auditHash: "b".repeat(64) } } },
    [`GET /smo/ran-nf-oam/config-jobs/${JOB}`]: { body: { jobId: JOB, status: "COMPLETED", subChanges: [] } },
    "GET /summary/decisions": { page: "decisions", computedAt: "", partial: [],
      counts: { "decisions24h.total": 2416, "decisions24h.DIRECT": 1782, "decisions24h.APPROVED": 512, "decisions24h.REJECTED": 3, "decisions24h.EXPIRED": 2, "decisions24h.REFUSED": 1 } },
    ...overrides,
  });
}

describe("the decision list", () => {
  // Pins down: lists why rApps acted and what became of it, with the approver.
  it("lists why rApps acted and what became of it, with the approver", async () => {
    const calls = bff();
    const { container } = await mountWith(<AuthProvider><Decisions /></AuthProvider>, { at: "/decisions" });
    await settle();
    const rows = Array.from(container.querySelectorAll("tbody tr"));
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("APPROVED");
    expect(rows[0].textContent).toContain("energy-saving 1.4.2");
    expect(rows[0].textContent).toContain("PRB use under 5 percent for an hour");
    expect(rows[0].textContent).toContain("smo-gui:alice");
    expect(rows[0].querySelector("a")?.getAttribute("href")).toBe(`/decisions/${DID}`);
    expect(rows[1].textContent).toContain("none given");
    const asked = calls.find((c) => c.path === "/smo/ran-nf-oam/decision-records")!;
    expect(asked.query.get("total")).toBe("false");
    expect(asked.query.get("limit")).toBe("50");                                    // the user's rows-per-page preference (default 50)
    const since = Date.parse(asked.query.get("since") ?? "");
    expect(Math.abs(Date.now() - 86_400_000 - since)).toBeLessThan(60_000);        // the last 24 h by default
  });

  // Pins down: sends the filters it was given and starts again from the first page.
  it("sends the filters it was given and starts again from the first page", async () => {
    const calls = bff();
    const { container } = await mountWith(<AuthProvider><Decisions /></AuthProvider>, { at: "/decisions" });
    await settle();
    await type(container.querySelector("input[aria-label='Filter by rApp']") as HTMLInputElement, "api-invoker-0a1b");
    const select = container.querySelector("select[aria-label='Filter by outcome']") as HTMLSelectElement;
    select.value = "REJECTED";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await settle();
    const last = calls.filter((c) => c.path === "/smo/ran-nf-oam/decision-records").at(-1)!;
    expect(last.query.get("invoker_id")).toBe("api-invoker-0a1b");
    expect(last.query.get("disposition")).toBe("REJECTED");
    expect(last.query.get("offset")).toBe("0");
  });

  // Pins down: the export link carries the range and the rApp / outcome filters; "All" (no start) offers no export and says why.
  it("links the CSV export with the current filters, and not for All", async () => {
    bff();
    const { container } = await mountWith(<AuthProvider><Decisions /></AuthProvider>, { at: "/decisions" });
    await settle();
    const select = container.querySelector("select[aria-label='Filter by outcome']") as HTMLSelectElement;
    select.value = "APPROVED";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await settle();
    const link = byText<HTMLAnchorElement>(container, "a", "Export CSV")!;
    const url = new URL(link.getAttribute("href")!, "http://x");
    expect(url.pathname).toBe("/api/smo/ran-nf-oam/decision-records/export.csv");
    expect(url.searchParams.get("disposition")).toBe("APPROVED");
    expect(url.searchParams.get("since")).not.toBeNull();
    await click(byText(container, "button", "All")!);
    await settle();
    expect(byText(container, "a", "Export CSV")).toBeNull();
    expect(container.textContent).toContain("Pick a time range");
  });

  // Pins down: pages: Older asks for the next page only when the server says there is one.
  it("pages: Older asks for the next page only when the server says there is one", async () => {
    const calls = bff({ "GET /smo/ran-nf-oam/decision-records": (c: Call) => ({ items: [record({ decisionId: `d-${c.query.get("offset")}` })], limit: 50, offset: Number(c.query.get("offset")), hasMore: c.query.get("offset") === "0" }) });
    const { container } = await mountWith(<AuthProvider><Decisions /></AuthProvider>, { at: "/decisions" });
    await settle();
    const list = () => calls.filter((c) => c.path === "/smo/ran-nf-oam/decision-records" && c.query.get("limit") !== "1");
    expect((byText(container, "button", "← Previous") as HTMLButtonElement).disabled).toBe(true);
    await click(byText(container, "button", "Next →")!);
    await settle();
    expect(list().at(-1)!.query.get("offset")).toBe("50");
    expect((byText(container, "button", "Next →") as HTMLButtonElement).disabled).toBe(true);
    expect((byText(container, "button", "← Previous") as HTMLButtonElement).disabled).toBe(false);
  });

  // Pins down: narrows to one job or one approval request when the address says so.
  it("narrows to one job or one approval request when the address says so", async () => {
    const calls = bff();
    const { container } = await mountWith(<AuthProvider><Decisions /></AuthProvider>, { at: `/decisions?job=${JOB}` });
    await settle();
    const asked = calls.find((c) => c.path === "/smo/ran-nf-oam/decision-records")!;
    expect(asked.query.get("job_id")).toBe(JOB);
    expect(asked.query.get("since")).toBeNull();                                   // narrowed to one job: no time range, so an old record still shows
    expect(container.textContent).toContain("Narrowed to job");
  });

  // Pins down: says when there is nothing and when the read failed.
  it("says when there is nothing and when the read failed", async () => {
    bff({ "GET /smo/ran-nf-oam/decision-records": { items: [], limit: 25, offset: 0, hasMore: false } });
    const first = await mountWith(<AuthProvider><Decisions /></AuthProvider>);
    await settle();
    expect(first.container.textContent).toContain("No decision has been recorded");
    cleanup();
    bff({ "GET /smo/ran-nf-oam/decision-records": { status: 503, body: { title: "ENDPOINT_UNREACHABLE", detail: "RAN NF OAM is down" } } });
    const second = await mountWith(<AuthProvider><Decisions /></AuthProvider>);
    await settle();
    expect(second.container.textContent).toContain("RAN NF OAM is down");
  });
});

describe("the decision panel", () => {
  // Pins down: shows the 24 h counts from the server summary.
  it("shows the 24 h counts from the server summary", async () => {
    bff();
    const { container } = await mountWith(<AuthProvider><Decisions /></AuthProvider>, { at: "/decisions" });
    await settle();
    const tiles = container.querySelector("[data-section='decisions.tiles']")!.textContent ?? "";
    expect(tiles).toContain("2,416");
    expect(tiles).toContain("1,782");
    expect(tiles).toContain("512");
    expect(tiles).toContain("6");                                                  // rejected + lapsed + refused
  });

  // Pins down: selects the first row, and selecting another row updates the chain and its integrity.
  it("selects the first row, and selecting another row updates the chain and its integrity", async () => {
    const calls = bff({ "GET /smo/ran-nf-oam/decision-records/d2": { body: { ...record({ decisionId: "d2", disposition: "DIRECT", approvedBy: null, approvalId: null }), integrity: { status: "UNCHAINED" } } } });
    const { container } = await mountWith(<AuthProvider><Decisions /></AuthProvider>, { at: "/decisions" });
    await settle();
    const chain = () => container.querySelector("[data-section='decisions.chain']")!;
    expect(chain().textContent).toContain("approved by smo-gui:alice");
    expect(chain().querySelectorAll("ol[aria-label='Decision chain'] > li")).toHaveLength(6);
    expect(container.querySelector("[data-section='decisions.integrity'] [role=status]")?.textContent).toContain("VERIFIED");
    await click(container.querySelectorAll("tbody tr")[1] as HTMLElement);
    await settle();
    expect(chain().textContent).toContain("Autonomous: not held for approval");
    expect(chain().textContent).toContain("none given");
    expect(container.querySelector("tbody tr.selected")?.textContent).toContain("DIRECT");
    expect(calls.some((c) => c.path === "/smo/ran-nf-oam/decision-records/d2")).toBe(true);
    expect(container.querySelector("[data-section='decisions.integrity'] [role=status]")?.textContent).toContain("UNCHAINED");
  });
});

describe("one decision", () => {
  const open = () => mountWith(<AuthProvider><DecisionDetail /></AuthProvider>, { at: `/decisions/${DID}`, route: "/decisions/:decisionId" });

  // Pins down: shows the inputs, the model version, the rationale, who approved, and that it matches the audit chain.
  it("shows the inputs, the model version, the rationale, who approved, and that it matches the audit chain", async () => {
    bff();
    const { container } = await open();
    await settle();
    const text = container.textContent ?? "";
    for (const expected of ["PRB use under 5 percent for an hour", "energy-saving 1.4.2", "dme://data-jobs/42", "smo-gui:alice", "act-1", "ME-1, ME-2 (2 changes)",
      "The record still matches the hash written to the audit chain"]) expect(text).toContain(expected);
    expect(container.querySelector("[role=status] .badge")?.textContent).toBe("VERIFIED");
    expect(text).toContain("python -m smo_shared.audit verify");
  });

  // Pins down: says plainly when the record does not match the chain.
  it("says plainly when the record does not match the chain", async () => {
    bff({ [`GET /smo/ran-nf-oam/decision-records/${DID}`]: { body: { ...record(), integrity: { status: "MISMATCH", reason: "audit row 7 does not carry this record's hash" } } } });
    const { container } = await open();
    await settle();
    expect(container.querySelector("[role=status] .badge")?.textContent).toBe("MISMATCH");
    expect(container.textContent).toContain("audit row 7 does not carry this record's hash");
    expect(container.textContent).toContain("was changed after it was written");
  });

  // Pins down: opens the config job it became, and says when no job was made.
  it("opens the config job it became, and says when no job was made", async () => {
    bff();
    const first = await open();
    await settle();
    await click(byText(first.container, "button", /7a2c9f1b/)!);
    await settle();
    expect(document.querySelector("[role=dialog]")?.textContent).toContain("Config job");
    cleanup();
    bff({ [`GET /smo/ran-nf-oam/decision-records/${DID}`]: { body: { ...record({ disposition: "REJECTED", jobId: null, approvedBy: null }), integrity: { status: "VERIFIED" } } } });
    const second = await open();
    await settle();
    expect(second.container.textContent).toContain("none was made");
    expect(second.container.textContent).toContain("A person (or the timeout policy) rejected it");
  });

  // Pins down: is a 404 message for a record that does not exist.
  it("is a 404 message for a record that does not exist", async () => {
    bff({ [`GET /smo/ran-nf-oam/decision-records/${DID}`]: { status: 404, body: { title: "DECISION_RECORD_NOT_FOUND", detail: "no decision record" } } });
    const { container } = await open();
    await settle();
    expect(container.textContent).toContain("no decision record");
  });
});

describe("the config job links to why it was made", () => {
  const job = () => mountWith(<AuthProvider><ConfigJobDrawer id={JOB} onClose={() => undefined} /></AuthProvider>);

  // Pins down: shows the rationale, the approver and a link when an rApp made the job.
  it("shows the rationale, the approver and a link when an rApp made the job", async () => {
    const calls = bff();
    await job();
    await settle();
    const drawer = document.querySelector("[role=dialog]")!;
    expect(drawer.textContent).toContain("Made for an rApp");
    expect(drawer.textContent).toContain("approved by smo-gui:alice");
    expect(drawer.textContent).toContain("PRB use under 5 percent for an hour");
    expect(drawer.querySelector("a")?.getAttribute("href")).toBe(`/decisions/${DID}`);
    expect(calls.find((c) => c.path === "/smo/ran-nf-oam/decision-records")!.query.get("job_id")).toBe(JOB);
  });

  // Pins down: shows nothing about an rApp for a job a person made.
  it("shows nothing about an rApp for a job a person made", async () => {
    bff({ "GET /smo/ran-nf-oam/decision-records": { items: [], limit: 1, offset: 0 } });
    await job();
    await settle();
    expect(document.querySelector("[role=dialog]")?.textContent).not.toContain("Made for an rApp");
  });
});
