// @vitest-environment jsdom
/**
 * Component tests of the approval inbox (pages/Approvals.tsx): the waiting and decided lists, the review drawer, approving and rejecting with the user's name, the viewer's read-only view, and the opt-in two-person approval.
 * Runs against `fakeBff` with the real permission table (auth/permissions.fixture.json); jsdom, no server. Run: `cd gui && npx vitest run src/pages/Approvals.test.tsx`.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import rules from "../auth/permissions.fixture.json";
import { fakeBff, mountWith, type Call } from "../testing/bff";
import { byText, cleanup, click, settle, type } from "../testing/dom";
import { Approvals } from "./Approvals";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const AID = "6f1b8e0a-1111-4a2b-9c3d-aaaaaaaaaaaa";
const JOB = "7a2c9f1b-2222-4b3c-8d4e-bbbbbbbbbbbb";
const soon = () => new Date(Date.now() + 25 * 60_000).toISOString();

const approval = (extra: Record<string, unknown> = {}) => ({
  approvalId: AID, invokerId: "api-invoker-0a1b2c3d-4e5f", requestedBy: "energy-saving", status: "PENDING", managedElements: ["ME-1", "ME-2"], changeCount: 2,
  createdAt: new Date().toISOString(), expiresAt: soon(), onTimeout: "EXPIRE", decidedBy: null, decidedAt: null, decisionReason: null, jobId: null, refusalCode: null,
  correlationId: null, decision: { rationale: "PRB use under 5 percent for an hour", modelVersion: "energy-saving 1.4.2", inputsRef: "dme://data-jobs/42", actionId: "act-1" }, ...extra,
});
const detail = (extra: Record<string, unknown> = {}) => ({
  ...approval(extra), accessScope: "cell",
  changes: [{ managedElementRef: "ME-1", managedFunctionRef: "NRCellDU=101", operation: "merge", attributeChanges: { txPower: 20 } }, { managedElementRef: "ME-2", attributeChanges: { txPower: 20 } }],
});

/**
 * Starts the fake BFF for a user of `role` (the permission table is the real one) with one waiting request, one rejected request and the request's detail; `overrides` adds or replaces routes.
 */
function bff(role: "viewer" | "operator" | "admin", overrides: Record<string, unknown> = {}) {
  return fakeBff({
    "GET /me": { username: "ana", role, csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role, rules },
    "GET /smo/ran-nf-oam/rapp-approvals": (c: Call) => ({ items: c.query.get("status") === "PENDING" ? [approval()] : [approval(), approval({ approvalId: "done-1", status: "REJECTED", decidedBy: "smo-gui:bob", decisionReason: "not in this window" })], limit: 100, offset: 0, hasMore: false }),
    [`GET /smo/ran-nf-oam/rapp-approvals/${AID}`]: { body: detail() },
    "GET /smo/ran-nf-oam/decision-records": { items: [], limit: 1, offset: 0 },
    ...overrides,
  });
}

const open = () => mountWith(<AuthProvider><Approvals /></AuthProvider>, { at: "/approvals" });

describe("the approval inbox", () => {
  // The waiting list shows the rApp, the changes, the rationale and when each request lapses.
  it("lists what is waiting with the rApp, the changes, why, and when it lapses", async () => {
    const calls = bff("operator");
    const { container } = await open();
    await settle();
    const row = container.querySelector("tbody tr")!;
    expect(row.textContent).toContain("energy-saving");
    expect(row.textContent).toContain("2 on ME-1, ME-2");
    expect(row.textContent).toContain("PRB use under 5 percent for an hour");
    expect(row.textContent).toMatch(/in 2\d min/);
    expect(row.textContent).toContain("expires");
    const asked = calls.find((c) => c.path === "/smo/ran-nf-oam/rapp-approvals")!;
    expect(asked.query.get("status")).toBe("PENDING");
    expect(asked.query.get("total")).toBe("false");                      // the inbox does not ask for a count it does not show
    expect(container.querySelector("[role=tab] .badge")?.textContent?.trim()).toBe("1");
    expect(container.textContent).toContain("Not in this inbox yet");     // change-window and model gate approvals are said not to be here
  });

  // An empty queue says nothing is waiting.
  it("says when nothing waits", async () => {
    bff("operator", { "GET /smo/ran-nf-oam/rapp-approvals": { items: [], limit: 100, offset: 0, hasMore: false } });
    const { container } = await open();
    await settle();
    expect(container.textContent).toContain("Nothing is waiting for a decision.");
  });

  // Before anyone decides, the drawer shows what the request would write and why, and approving is recorded under the user's name.
  it("shows what a request would write and why before anyone decides, and approves under the user's name", async () => {
    const calls = bff("operator", { [`POST /smo/ran-nf-oam/rapp-approvals/${AID}/approve`]: { body: { ...detail({ status: "APPROVED", jobId: JOB, decidedBy: "smo-gui:ana" }), jobStatus: "COMPLETED" } } });
    await open();
    await settle();
    await click(byText(document.body, "button", "Review…")!);
    await settle();
    const drawer = document.querySelector("[role=dialog]")!;
    expect(drawer.textContent).toContain("energy-saving 1.4.2");
    expect(drawer.textContent).toContain("dme://data-jobs/42");
    const lines = Array.from(drawer.querySelectorAll("ul[aria-label=Changes] li")).map((li) => li.textContent);
    expect(lines).toEqual(["ME-1 / NRCellDU=101 merge txPower=20", "ME-2 merge txPower=20"]);
    await type(drawer.querySelector("input") as HTMLInputElement, "checked with the night plan");
    await click(byText(drawer as HTMLElement, "button", "Approve")!);
    await settle();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe(`/smo/ran-nf-oam/rapp-approvals/${AID}/approve`);
    expect(post.body).toEqual({ reason: "checked with the night plan" });         // who decided is not sent: the BFF pins it to the signed-in user
  });

  // A rejection sends the reason and nothing is written.
  it("rejects with a reason and writes nothing", async () => {
    const calls = bff("operator", { [`POST /smo/ran-nf-oam/rapp-approvals/${AID}/reject`]: { body: detail({ status: "REJECTED" }) } });
    await open();
    await settle();
    await click(byText(document.body, "button", "Review…")!);
    await settle();
    await click(byText(document.querySelector("[role=dialog]") as HTMLElement, "button", "Reject")!);
    await settle();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe(`/smo/ran-nf-oam/rapp-approvals/${AID}/reject`);
    expect(post.body).toEqual({ reason: null });
  });

  // A viewer can read a request but is offered no way to decide it.
  it("shows a viewer the request and no way to decide it", async () => {
    bff("viewer");
    await open();
    await settle();
    await click(byText(document.body, "button", "Review…")!);
    await settle();
    const drawer = document.querySelector("[role=dialog]")!;
    expect(drawer.textContent).toContain("Why the rApp asks");
    expect(byText(drawer as HTMLElement, "button", "Approve")).toBeNull();
    expect(byText(drawer as HTMLElement, "button", "Reject")).toBeNull();
  });

  // A decided request shows who decided it, the job it became and a link to its decision record.
  it("shows a decided request with who decided, the job it became and a link to its decision record", async () => {
    bff("operator", {
      "GET /smo/ran-nf-oam/rapp-approvals": { items: [approval({ approvalId: "pending-1" }), approval({ status: "APPROVED", jobId: JOB, decidedBy: "smo-gui:bob", decisionReason: "not in this window" })], limit: 100, offset: 0, hasMore: false },
      [`GET /smo/ran-nf-oam/rapp-approvals/${AID}`]: { body: detail({ status: "APPROVED", jobId: JOB, decidedBy: "smo-gui:bob", decidedAt: new Date().toISOString(), decisionReason: "looks right" }) },
      "GET /smo/ran-nf-oam/decision-records": { items: [{ decisionId: "d-1", disposition: "APPROVED" }], limit: 1, offset: 0 },
      [`GET /smo/ran-nf-oam/config-jobs/${JOB}`]: { body: { jobId: JOB, status: "COMPLETED", subChanges: [] } },
    });
    const { container } = await open();
    await settle();
    await click(byText(container, "button", "Decided")!);
    await settle();
    const rows = Array.from(container.querySelectorAll("tbody tr"));
    expect(rows).toHaveLength(1);                                        // the pending one is not in this list
    expect(rows[0].textContent).toContain("APPROVED");
    expect(rows[0].textContent).toContain("smo-gui:bob");
    expect(rows[0].textContent).toContain("not in this window");
    await click(rows[0] as HTMLElement);
    await settle();
    const drawer = document.querySelector("[role=dialog]")!;
    expect(drawer.querySelector("a")?.getAttribute("href")).toBe("/decisions/d-1");
    expect(drawer.textContent).toContain("Approved: the config job was made from the request");
    expect(byText(drawer as HTMLElement, "button", "Approve")).toBeNull();      // decided once
  });

  // A request that was approved but then refused shows the refusal code it was closed with.
  it("names the code a refused request was closed with", async () => {
    bff("operator", {
      "GET /smo/ran-nf-oam/rapp-approvals": { items: [approval({ status: "REFUSED", refusalCode: "RAPP_KILLED", decidedBy: "smo-gui:bob" })], limit: 100, offset: 0, hasMore: false },
    });
    const { container } = await open();
    await settle();
    await click(byText(container, "button", "Decided")!);
    await settle();
    expect(container.querySelector("tbody tr")!.textContent).toContain("RAPP_KILLED");
  });

  // A failed read of the queue is shown as an error, not as an empty inbox.
  it("shows a failure to read the queue instead of an empty inbox", async () => {
    bff("operator", { "GET /smo/ran-nf-oam/rapp-approvals": { status: 503, body: { title: "ENDPOINT_UNREACHABLE", detail: "RAN NF OAM is down" } } });
    const { container } = await open();
    await settle();
    expect(container.textContent).toContain("RAN NF OAM is down");
    expect(container.textContent).not.toContain("Nothing is waiting");
  });
});


describe("two-person approval (opt-in; a request that needs one approval looks as before)", () => {
  const vote = (by: string, reason: string | null = null) => ({ by, at: new Date().toISOString(), reason });

  // The inbox row of a two-approval request shows its progress, and a request needing one, or from a RAN NF OAM that sends neither field, shows "one needed".
  // A request that needs two approvals shows how many it has and needs; a single-approval request shows nothing extra.
  it("shows how many approvals a waiting request has and needs, and nothing extra for a single approval", async () => {
    bff("operator", {
      "GET /smo/ran-nf-oam/rapp-approvals": { items: [approval({ approvalId: "two-1", requiredApprovals: 2, approvals: [vote("smo-gui:bob")] }), approval({ approvalId: "one-1", requiredApprovals: 1, approvals: [] }), approval({ approvalId: "old-1" })], limit: 100, offset: 0, hasMore: false },
    });
    const { container } = await open();
    await settle();
    const rows = Array.from(container.querySelectorAll("tbody tr")).map((r) => r.textContent ?? "");
    expect(rows[0]).toContain("1 of 2 approvals");
    expect(rows[1]).toContain("one needed");
    expect(rows[2]).toContain("one needed");                              // a RAN NF OAM that predates the field sends neither key
  });

  // The drawer lists the approvals so far; the first of two approvals is sent with a null reason and the page says another person must approve, not that the change is written.
  // The first of two approvals is listed and sent without claiming that the change is written.
  it("lists the approvals so far in the drawer and sends the first of two approvals without claiming the change is written", async () => {
    const calls = bff("operator", {
      [`GET /smo/ran-nf-oam/rapp-approvals/${AID}`]: { body: detail({ requiredApprovals: 2, approvals: [] }) },
      [`POST /smo/ran-nf-oam/rapp-approvals/${AID}/approve`]: { body: { ...detail({ requiredApprovals: 2, approvals: [vote("smo-gui:ana")] }), jobStatus: null } },
    });
    await open();
    await settle();
    await click(byText(document.body, "button", "Review…")!);
    await settle();
    const drawer = document.querySelector("[role=dialog]") as HTMLElement;
    expect(drawer.textContent).toContain("Approvals so far (0 of 2)");
    expect(drawer.textContent).toContain("first of two approvals");
    await click(byText(drawer, "button", "Approve")!);
    await settle();
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ reason: null });
    expect(document.body.textContent).toContain("Your approval is recorded: another person must approve before anything is written");
    expect(document.body.textContent).not.toContain("Approved: the change is being written");
  });

  // Someone who already approved sees the approval and its reason, has the Approve button disabled and a notice, and can still press Reject.
  // The person who already approved is not offered a second approval (they may still reject).
  it("shows who has approved and does not offer the same person a second approval (they may still reject)", async () => {
    bff("operator", { [`GET /smo/ran-nf-oam/rapp-approvals/${AID}`]: { body: detail({ requiredApprovals: 2, approvals: [vote("smo-gui:Ana", "fine by me")] }) } });   // the signed-in user is ana
    await open();
    await settle();
    await click(byText(document.body, "button", "Review…")!);
    await settle();
    const drawer = document.querySelector("[role=dialog]") as HTMLElement;
    const lines = Array.from(drawer.querySelectorAll("ul[aria-label='Approvals so far'] li")).map((li) => li.textContent ?? "");
    expect(lines).toHaveLength(1);
    expect(lines[0]).toContain("smo-gui:Ana");
    expect(lines[0]).toContain("fine by me");
    expect((byText(drawer, "button", "Approve") as HTMLButtonElement).disabled).toBe(true);
    expect(drawer.textContent).toContain("You have approved this request");
    expect((byText(drawer, "button", "Reject") as HTMLButtonElement).disabled).toBe(false);
  });

  // The drawer tells the second approver theirs is the last one needed, and after they approve the page says the change is being written.
  // The second person is told theirs is the last approval needed, and the change is said to be written when they send it.
  it("tells a second person that theirs is the last approval and says the change is written when they send it", async () => {
    const calls = bff("operator", {
      [`GET /smo/ran-nf-oam/rapp-approvals/${AID}`]: { body: detail({ requiredApprovals: 2, approvals: [vote("smo-gui:bob")] }) },
      [`POST /smo/ran-nf-oam/rapp-approvals/${AID}/approve`]: { body: { ...detail({ status: "APPROVED", jobId: JOB, requiredApprovals: 2, approvals: [vote("smo-gui:bob"), vote("smo-gui:ana")] }), jobStatus: "COMPLETED" } },
    });
    await open();
    await settle();
    await click(byText(document.body, "button", "Review…")!);
    await settle();
    const drawer = document.querySelector("[role=dialog]") as HTMLElement;
    expect(drawer.textContent).toContain("Your approval is the last one needed");
    await click(byText(drawer, "button", "Approve")!);
    await settle();
    expect(calls.find((c) => c.method === "POST")!.path).toBe(`/smo/ran-nf-oam/rapp-approvals/${AID}/approve`);
    expect(document.body.textContent).toContain("Approved: the change is being written");
  });

  // The Decided tab shows both approvers of a two-approval request in its row.
  // The decided list names both approvers of a two-person request.
  it("names both approvers in the decided list", async () => {
    bff("operator", {
      "GET /smo/ran-nf-oam/rapp-approvals": { items: [approval({ status: "APPROVED", decidedBy: "smo-gui:ana", requiredApprovals: 2, approvals: [vote("smo-gui:bob"), vote("smo-gui:ana")] })], limit: 100, offset: 0, hasMore: false },
    });
    const { container } = await open();
    await settle();
    await click(byText(container, "button", "Decided")!);
    await settle();
    expect(container.querySelector("tbody tr")!.textContent).toContain("smo-gui:bob, smo-gui:ana");
  });
});
