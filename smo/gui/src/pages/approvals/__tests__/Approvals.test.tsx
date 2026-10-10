// @vitest-environment jsdom
/** Tests of the Approvals page (pages/approvals): the waiting queue (cards with the lapse countdown), the detail panel (impact tiles, the config
 * diff, why the rApp asks, the decision box and its result), RBAC, and the Decided tab with its drawer. Run: `npx vitest run src/pages/approvals`. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith, type Call } from "../../../testing/bff";
import { byText, cleanup, click, settle, type } from "../../../testing/dom";
import { Approvals } from "..";

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

/** The fake BFF of these tests: a signed-in user of `role`, the queue (one pending request), its detail and the summary count. */
function bff(role: "viewer" | "operator" | "admin", overrides: Record<string, unknown> = {}) {
  return fakeBff({
    "GET /me": { username: "ana", role, csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role, rules },
    "GET /smo/ran-nf-oam/rapp-approvals": (c: Call) => ({ items: c.query.get("status") === "PENDING" ? [approval()] : [approval(), approval({ approvalId: "done-1", status: "REJECTED", decidedBy: "smo-gui:bob", decisionReason: "not in this window" })], limit: 100, offset: 0, hasMore: false }),
    [`GET /smo/ran-nf-oam/rapp-approvals/${AID}`]: { body: detail() },
    "GET /smo/ran-nf-oam/decision-records": { items: [], limit: 1, offset: 0 },
    "GET /summary/approvals": { page: "approvals", computedAt: "", counts: { "approvals.PENDING": 1 }, partial: [] },
    ...overrides,
  });
}

const open = () => mountWith(<AuthProvider><Approvals /></AuthProvider>, { at: "/approvals" });
const detail$ = () => document.querySelector("[data-section='approvals.detail']") as HTMLElement;

describe("the approval inbox", () => {
  // Pins down: lists what is waiting with the rApp, the changes, why, and when it lapses.
  it("lists what is waiting with the rApp, the changes, why, and when it lapses", async () => {
    const calls = bff("operator");
    const { container } = await open();
    await settle();
    const row = container.querySelector("[data-approval]")!;
    expect(row.textContent).toContain("energy-saving");
    expect(row.textContent).toContain("2 on ME-1, ME-2");
    expect(row.textContent).toContain("PRB use under 5 percent for an hour");
    expect(row.textContent).toMatch(/in 2\d min/);
    expect(row.textContent).toContain("expires");
    const asked = calls.find((c) => c.path === "/smo/ran-nf-oam/rapp-approvals")!;
    expect(asked.query.get("status")).toBe("PENDING");
    expect(asked.query.get("total")).toBe("false");                      // the inbox does not ask for a count it does not show
    expect(container.querySelector("[role=tab] .n")?.textContent?.trim()).toBe("1");          // the pending count comes from the summary
    expect(container.textContent).toContain("Not in this inbox yet");     // change-window and model gate approvals are said not to be here
  });

  // Pins down: says when nothing waits.
  it("says when nothing waits", async () => {
    bff("operator", { "GET /smo/ran-nf-oam/rapp-approvals": { items: [], limit: 100, offset: 0, hasMore: false } });
    const { container } = await open();
    await settle();
    expect(container.textContent).toContain("Nothing is waiting for a decision.");
  });

  // Pins down: shows what a request would write and why before anyone decides, and approves under the user's name.
  it("shows what a request would write and why before anyone decides, and approves under the user's name", async () => {
    const calls = bff("operator", { [`POST /smo/ran-nf-oam/rapp-approvals/${AID}/approve`]: { body: { ...detail({ status: "APPROVED", jobId: JOB, decidedBy: "smo-gui:ana" }), jobStatus: "COMPLETED" } } });
    await open();
    await settle();
    await click(byText(document.body, "button", "Review…")!);
    await settle();
    const drawer = detail$();
    expect(drawer.textContent).toContain("energy-saving 1.4.2");
    expect(drawer.textContent).toContain("dme://data-jobs/42");
    const lines = Array.from(drawer.querySelectorAll("[aria-label=Changes] > div")).map((d) => d.textContent);
    expect(lines).toEqual(["ME-1 / NRCellDU=101 · merge", "+ txPower: 20", "ME-2 · merge", "+ txPower: 20"]);
    await type(drawer.querySelector("input") as HTMLInputElement, "checked with the night plan");
    await click(byText(drawer, "button", "Approve & write")!);
    await settle();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe(`/smo/ran-nf-oam/rapp-approvals/${AID}/approve`);
    expect(post.body).toEqual({ reason: "checked with the night plan" });         // who decided is not sent: the BFF pins it to the signed-in user
    expect(detail$().querySelector("[data-section='approvals.decide']")?.textContent).toContain("APPROVED");    // the result is shown
    expect(detail$().querySelector("[data-section='approvals.decide']")?.textContent).toContain("COMPLETED");
  });

  // Pins down: rejects with a reason and writes nothing.
  it("rejects with a reason and writes nothing", async () => {
    const calls = bff("operator", { [`POST /smo/ran-nf-oam/rapp-approvals/${AID}/reject`]: { body: detail({ status: "REJECTED" }) } });
    await open();
    await settle();
    await click(byText(document.body, "button", "Review…")!);
    await settle();
    await click(byText(detail$(), "button", "Reject")!);
    await settle();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe(`/smo/ran-nf-oam/rapp-approvals/${AID}/reject`);
    expect(post.body).toEqual({ reason: null });
  });

  // Pins down: shows a viewer the request and no way to decide it.
  it("shows a viewer the request and no way to decide it", async () => {
    bff("viewer");
    await open();
    await settle();
    await click(byText(document.body, "button", "Review…")!);
    await settle();
    const drawer = detail$();
    expect(drawer.textContent).toContain("Why the rApp asks");
    expect(byText(drawer, "button", "Approve & write")).toBeNull();
    expect(byText(drawer as HTMLElement, "button", "Reject")).toBeNull();
  });

  // Pins down: shows a decided request with who decided, the job it became and a link to its decision record.
  it("shows a decided request with who decided, the job it became and a link to its decision record", async () => {
    bff("operator", {
      "GET /smo/ran-nf-oam/rapp-approvals": { items: [approval({ approvalId: "pending-1" }), approval({ status: "APPROVED", jobId: JOB, decidedBy: "smo-gui:bob", decisionReason: "not in this window" })], limit: 100, offset: 0, hasMore: false },
      [`GET /smo/ran-nf-oam/rapp-approvals/${AID}`]: { body: detail({ status: "APPROVED", jobId: JOB, decidedBy: "smo-gui:bob", decidedAt: new Date().toISOString(), decisionReason: "looks right" }) },
      "GET /smo/ran-nf-oam/decision-records": { items: [{ decisionId: "d-1", disposition: "APPROVED" }], limit: 1, offset: 0 },
      [`GET /smo/ran-nf-oam/config-jobs/${JOB}`]: { body: { jobId: JOB, status: "COMPLETED", subChanges: [] } },
    });
    const { container } = await open();
    await settle();
    await click(byText(container, "[role=tab]", "Decided")!);
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
    expect(byText(drawer as HTMLElement, "button", "Approve & write")).toBeNull();      // decided once
  });

  // Pins down: names the code a refused request was closed with.
  it("names the code a refused request was closed with", async () => {
    bff("operator", {
      "GET /smo/ran-nf-oam/rapp-approvals": { items: [approval({ status: "REFUSED", refusalCode: "RAPP_KILLED", decidedBy: "smo-gui:bob" })], limit: 100, offset: 0, hasMore: false },
    });
    const { container } = await open();
    await settle();
    await click(byText(container, "[role=tab]", "Decided")!);
    await settle();
    expect(container.querySelector("tbody tr")!.textContent).toContain("RAPP_KILLED");
  });

  // Pins down: draws a lapse countdown bar on each queued request and a running clock in the detail.
  it("draws a lapse countdown bar on each queued request and a running clock in the detail", async () => {
    bff("operator");
    const { container } = await open();
    await settle();
    const bar = container.querySelector("[data-approval] .meter[role=img]");
    expect(bar?.getAttribute("aria-label")).toMatch(/% of its waiting time left/);
    expect(detail$().querySelector("[aria-label='Time left']")?.textContent).toMatch(/^2\d:\d\d$/);   // selected by default: the first request
    expect(detail$().textContent).toContain("Expected KPI impact is not shown");                       // no invented prediction
  });

  // Pins down: shows a failure to read the queue instead of an empty inbox.
  it("shows a failure to read the queue instead of an empty inbox", async () => {
    bff("operator", { "GET /smo/ran-nf-oam/rapp-approvals": { status: 503, body: { title: "ENDPOINT_UNREACHABLE", detail: "RAN NF OAM is down" } } });
    const { container } = await open();
    await settle();
    expect(container.textContent).toContain("RAN NF OAM is down");
    expect(container.textContent).not.toContain("Nothing is waiting");
  });
});
