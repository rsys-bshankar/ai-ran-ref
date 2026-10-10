// @vitest-environment jsdom
/**
 * Component tests of the decision record pages (pages/Decisions.tsx) and of the link from a config job to why it was made (components/ConfigJobDrawer.tsx): the list, filters, paging, the narrowing by
 * address, one record and its integrity result, and the missing-record case. Runs as a viewer against `fakeBff` with the real permission table; jsdom. Run: `cd gui && npx vitest run src/pages/Decisions.test.tsx`.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import rules from "../auth/permissions.fixture.json";
import { ConfigJobDrawer } from "../components/ConfigJobDrawer";
import { fakeBff, mountWith, type Call } from "../testing/bff";
import { byText, cleanup, click, settle, type } from "../testing/dom";
import { DecisionDetail, Decisions } from "./Decisions";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const DID = "d1d1d1d1-0000-4000-8000-000000000001";
const JOB = "7a2c9f1b-2222-4b3c-8d4e-bbbbbbbbbbbb";
const APPROVAL = "6f1b8e0a-1111-4a2b-9c3d-aaaaaaaaaaaa";

/**
 * A decision record as RAN NF OAM returns it (an APPROVED one with a job and an approval request); `extra` replaces fields.
 */
const record = (extra: Record<string, unknown> = {}) => ({
  decisionId: DID, occurredAt: "2026-10-09T10:00:00.000000Z", invokerId: "api-invoker-0a1b2c3d-4e5f", requestedBy: "energy-saving", disposition: "APPROVED", jobId: JOB,
  approvalId: APPROVAL, actionId: "act-1", inputsRef: "dme://data-jobs/42", modelVersion: "energy-saving 1.4.2", rationale: "PRB use under 5 percent for an hour",
  approvedBy: "smo-gui:alice", decidedBy: "smo-gui:alice", decidedAt: "2026-10-09T10:05:00.000000Z", managedElements: ["ME-1", "ME-2"], changeCount: 2, correlationId: "c-1",
  contentHash: "a".repeat(64), auditSeq: 7, ...extra,
});

/**
 * Starts the fake BFF as a viewer with a two-record list, one record with a VERIFIED integrity block and a completed config job; `overrides` adds or replaces routes.
 */
function bff(overrides: Record<string, unknown> = {}) {
  return fakeBff({
    "GET /me": { username: "ana", role: "viewer", csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role: "viewer", rules },
    "GET /smo/ran-nf-oam/decision-records": { items: [record(), record({ decisionId: "d2", disposition: "DIRECT", approvedBy: null, approvalId: null, rationale: null, modelVersion: null })], limit: 25, offset: 0, hasMore: false },
    [`GET /smo/ran-nf-oam/decision-records/${DID}`]: { body: { ...record(), integrity: { status: "VERIFIED", auditSeq: 7, auditHash: "b".repeat(64) } } },
    [`GET /smo/ran-nf-oam/config-jobs/${JOB}`]: { body: { jobId: JOB, status: "COMPLETED", subChanges: [] } },
    ...overrides,
  });
}

describe("the decision list", () => {
  // The list shows when, the rApp, the outcome, the model, the rationale and the approver of each record.
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
    expect(asked.query.get("limit")).toBe("25");
  });

  // Filters go to the query as typed (blank ones left out) and a changed filter starts again from the first page.
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

  // "Older" is enabled only when the server says there is another page.
  it("pages: Older asks for the next page only when the server says there is one", async () => {
    const calls = bff({ "GET /smo/ran-nf-oam/decision-records": (c: Call) => ({ items: [record({ decisionId: `d-${c.query.get("offset")}` })], limit: 25, offset: Number(c.query.get("offset")), hasMore: c.query.get("offset") === "0" }) });
    const { container } = await mountWith(<AuthProvider><Decisions /></AuthProvider>, { at: "/decisions" });
    await settle();
    expect((byText(container, "button", "Newer") as HTMLButtonElement).disabled).toBe(true);
    await click(byText(container, "button", "Older")!);
    await settle();
    expect(calls.filter((c) => c.path === "/smo/ran-nf-oam/decision-records").at(-1)!.query.get("offset")).toBe("25");
    expect((byText(container, "button", "Older") as HTMLButtonElement).disabled).toBe(true);
    expect((byText(container, "button", "Newer") as HTMLButtonElement).disabled).toBe(false);
  });

  // An address with `?job=` or `?approval=` narrows the list and offers a way back to all.
  it("narrows to one job or one approval request when the address says so", async () => {
    const calls = bff();
    const { container } = await mountWith(<AuthProvider><Decisions /></AuthProvider>, { at: `/decisions?job=${JOB}` });
    await settle();
    expect(calls.find((c) => c.path === "/smo/ran-nf-oam/decision-records")!.query.get("job_id")).toBe(JOB);
    expect(container.textContent).toContain("Narrowed to job");
  });

  // An empty answer says no decision was recorded, and a failed read shows the error instead of an empty table.
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

describe("one decision", () => {
  const open = () => mountWith(<AuthProvider><DecisionDetail /></AuthProvider>, { at: `/decisions/${DID}`, route: "/decisions/:decisionId" });

  // One record shows its inputs, model version, rationale and approver, and that it matches the audit chain.
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

  // A record that does not match the audit chain is said so plainly, with the reason.
  it("says plainly when the record does not match the chain", async () => {
    bff({ [`GET /smo/ran-nf-oam/decision-records/${DID}`]: { body: { ...record(), integrity: { status: "MISMATCH", reason: "audit row 7 does not carry this record's hash" } } } });
    const { container } = await open();
    await settle();
    expect(container.querySelector("[role=status] .badge")?.textContent).toBe("MISMATCH");
    expect(container.textContent).toContain("audit row 7 does not carry this record's hash");
    expect(container.textContent).toContain("was changed after it was written");
  });

  // The job of a record opens in its drawer, and a record with no job says none was made.
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
  // A decision that needed two approvals names both approvers, in the list and on the record.
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

  // A record that does not exist shows the 404 message.
  it("is a 404 message for a record that does not exist", async () => {
    bff({ [`GET /smo/ran-nf-oam/decision-records/${DID}`]: { status: 404, body: { title: "DECISION_RECORD_NOT_FOUND", detail: "no decision record" } } });
    const { container } = await open();
    await settle();
    expect(container.textContent).toContain("no decision record");
  });
});

describe("the config job links to why it was made", () => {
  const job = () => mountWith(<AuthProvider><ConfigJobDrawer id={JOB} onClose={() => undefined} /></AuthProvider>);

  // A config job made for an rApp shows the rationale, the approver and a link to the decision record.
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

  // A job a person made has no "made for an rApp" block.
  it("shows nothing about an rApp for a job a person made", async () => {
    bff({ "GET /smo/ran-nf-oam/decision-records": { items: [], limit: 1, offset: 0 } });
    await job();
    await settle();
    expect(document.querySelector("[role=dialog]")?.textContent).not.toContain("Made for an rApp");
  });
});
