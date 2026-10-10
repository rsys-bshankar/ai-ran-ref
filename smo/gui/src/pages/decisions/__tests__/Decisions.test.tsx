// @vitest-environment jsdom
/** Tests of the Decisions page (pages/decisions): the record table (filters as query parameters, the default 24 h range, paging), the summary
 * tiles, the export job (current range, rApp, outcome and scope; "All" from the first record; operators only), keyset paging, the chain panel that follows the selected row, the one-record route with its integrity check (and both approvers of a request that needed two), and the config job's link back to its
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
    expect(asked.query.get("after")).toBe("");                                      // keyset-paged from the first page (GUI-9.5b)
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
    expect(last.query.get("after")).toBe("");
  });

  // Pins down: "Export…" starts an export job with the range, rApp and outcome; "All" exports from the first record; the job is announced with a
  // link to the Exports page (GUI-9.5b)
  it("starts an export job with the current filters, also for All", async () => {
    const OPERATOR = { "GET /me": { username: "ops", role: "operator", csrfToken: "c", local: true, totpEnrolled: true }, "GET /permissions": { role: "operator", rules } };
    const calls = bff({ ...OPERATOR, "POST /exports": { status: 202, body: { id: "e1", kind: "decisions", state: "QUEUED", params: {} } } });
    const { container } = await mountWith(<AuthProvider><Decisions /></AuthProvider>, { at: "/decisions" });
    await settle();
    const select = container.querySelector("select[aria-label='Filter by outcome']") as HTMLSelectElement;
    select.value = "APPROVED";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await settle();
    expect(byText(container, "a", "Export CSV")).toBeNull();                       // the streamed link is gone
    await click(byText(document.body, "button", "Export…")!);
    await click(byText(document.body, "button", "Start export")!);
    await settle();
    const post = calls.filter((c) => c.method === "POST" && c.path === "/exports").at(-1)!;
    expect(post.body).toMatchObject({ kind: "decisions", disposition: "APPROVED" });
    expect(Math.abs(Date.now() - 86_400_000 - Date.parse((post.body as { since: string }).since))).toBeLessThan(60_000);
    expect(document.body.textContent).toContain("The export is queued");
    expect(document.body.querySelector("a[href='/exports']")).not.toBeNull();
    await click(byText(document.body, "button", "Close")!);
    await click(byText(container, "button", "All")!);
    await settle();
    await click(byText(document.body, "button", "Export…")!);
    await click(byText(document.body, "button", "Start export")!);
    await settle();
    expect((calls.filter((c) => c.path === "/exports").at(-1)!.body as { since: string }).since).toBe("1970-01-01T00:00:00.000Z");
  });

  // Pins down: a viewer is offered no export (decisions exports need the operator role), and a refused export says why in the dialog.
  it("offers no export to a viewer and says why an export was refused", async () => {
    bff();
    const viewer = await mountWith(<AuthProvider><Decisions /></AuthProvider>, { at: "/decisions" });
    await settle();
    expect(byText(viewer.container, "button", "Export…")).toBeNull();
    viewer.unmount();
    bff({ "GET /me": { username: "ops", role: "operator", csrfToken: "c", local: true }, "GET /permissions": { role: "operator", rules },
      "POST /exports": { status: 429, body: { title: "TOO_MANY_EXPORTS" } } });
    const { container } = await mountWith(<AuthProvider><Decisions /></AuthProvider>, { at: "/decisions" });
    await settle();
    await click(byText(container, "button", "Export…")!);
    await click(byText(document.body, "button", "Start export")!);
    await settle();
    expect(document.body.textContent).toContain("three exports running");
  });

  // Pins down: the table pages by keyset: Next asks with the answer's nextCursor, Previous goes back without a cursor, and Next is off when
  // the server says there is no more (GUI-9.5b).
  it("pages by cursor: Next sends nextCursor, and stops when there is no more", async () => {
    const calls = bff({ "GET /smo/ran-nf-oam/decision-records": (c: Call) => (c.query.get("after") === ""
      ? { items: [record({ decisionId: "d-first" })], limit: 50, nextCursor: "cur-1", hasMore: true }
      : { items: [record({ decisionId: "d-second" })], limit: 50, nextCursor: null, hasMore: false }) });
    const { container } = await mountWith(<AuthProvider><Decisions /></AuthProvider>, { at: "/decisions" });
    await settle();
    const list = () => calls.filter((c) => c.path === "/smo/ran-nf-oam/decision-records");
    expect((byText(container, "button", "← Previous") as HTMLButtonElement).disabled).toBe(true);
    await click(byText(container, "button", "Next →")!);
    await settle();
    expect(list().at(-1)!.query.get("after")).toBe("cur-1");
    expect(list().every((c) => !c.query.has("offset"))).toBe(true);
    expect((byText(container, "button", "Next →") as HTMLButtonElement).disabled).toBe(true);
    await click(byText(container, "button", "← Previous")!);
    await settle();
    expect(container.querySelector("tbody")!.textContent).toContain("APPROVED");
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

  // A decision record with two approvers shows both in the list row and under "Approvers (two were needed)" on the record, while one without the field shows its single approver as before.
  it("names both approvers of a decision that needed two, in the list and on the record", async () => {
    const two = record({ approvedBy: "smo-gui:bob", approvers: ["smo-gui:alice", "smo-gui:bob"] });
    bff({
      "GET /smo/ran-nf-oam/decision-records": { items: [two, record({ decisionId: "d2", approvedBy: "smo-gui:carol" })], limit: 25, offset: 0, hasMore: false },
      [`GET /smo/ran-nf-oam/decision-records/${DID}`]: { body: { ...two, integrity: { status: "VERIFIED" } } },
    });
    const list = await mountWith(<AuthProvider><Decisions /></AuthProvider>, { at: "/decisions" });
    await settle();
    const rows = Array.from(list.container.querySelectorAll("tbody tr"));
    expect(rows[0].textContent).toContain("smo-gui:alice, smo-gui:bob");
    expect(rows[1].textContent).toContain("smo-gui:carol");                              // a record without the field reads as before
    cleanup();
    const one = await open();
    await settle();
    expect(one.container.textContent).toContain("Approvers (two were needed)");
    expect(one.container.textContent).toContain("smo-gui:alice, smo-gui:bob");
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
