// @vitest-environment jsdom
/** Tests of the Safeguards page (pages/safeguards): the per-rApp limits table and its approval-policy actions (AI-11.4), RBAC on them, the
 * "Stop all rApp writes" loop over the per-rApp stop with its confirm, and the tabs. Run: `npx vitest run src/pages/safeguards`. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith } from "../../../testing/bff";
import { byText, cleanup, click, settle, type } from "../../../testing/dom";
import { Safeguards } from "..";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const IID = "0b9f3f1e-4b0e-4a0c-9d6f-111111111111";
const INVOKER = "api-invoker-0a1b2c3d";

/** The fake BFF of these tests: a user of `role`, one instance with `approvalPolicy`, empty stop and refusal lists; `extra` adds or replaces routes. */
function bff(role: "viewer" | "operator" | "admin", approvalPolicy: unknown = null, extra: Record<string, unknown> = {}) {
  return fakeBff({
    "GET /me": { username: "ana", role, csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role, rules },
    "GET /smo/rapp-mgmt/instances": { items: [{ instanceId: IID, packageId: "p", state: "RUNNING", autonomyMode: "ASSIST" }], limit: 200, offset: 0 },
    [`GET /smo/rapp-mgmt/instances/${IID}/safeguards`]: { body: { instanceId: IID, invokerId: INVOKER, killed: false, kill: null, limits: null, approvalPolicy } },
    "GET /smo/ran-nf-oam/rapp-kill": { items: [], limit: 200, offset: 0 },
    [`PUT /smo/ran-nf-oam/rapp-approval-policy/${INVOKER}`]: { invokerId: INVOKER },
    [`DELETE /smo/ran-nf-oam/rapp-approval-policy/${INVOKER}`]: { status: 204 },
    "GET /smo/ran-nf-oam/safeguard-refusals": { items: [], limit: 1, offset: 0, total: 0 },
    "GET /summary/rapps": { page: "rapps", computedAt: "", counts: { "instances.total": 1 }, partial: [] },
    ...extra,
  });
}

const open = () => mountWith(<AuthProvider><Safeguards /></AuthProvider>);

describe("holding an rApp's changes for approval (AI-11.4)", () => {
  // Pins down: says that an rApp without a policy writes at once, and what the policy of one that has it is.
  it("says that an rApp without a policy writes at once, and what the policy of one that has it is", async () => {
    bff("admin");
    const first = await open();
    await settle();
    expect(first.container.textContent).toContain("Writes at once");
    cleanup();
    bff("admin", { invokerId: INVOKER, timeoutSeconds: 7200, onTimeout: "REJECT", setBy: "rapp-mgmt", updatedAt: "2026-10-09T00:00:00Z" });
    const second = await open();
    await settle();
    expect(second.container.textContent).toContain("Held for approval · lapses after 2 h (rejected)");
  });

  // Pins down: lets an admin hold it, with the timeout in minutes and what a lapsed request becomes.
  it("lets an admin hold it, with the timeout in minutes and what a lapsed request becomes", async () => {
    const calls = bff("admin");
    await open();
    await settle();
    await click(byText(document.body, "button", "Approval…")!);
    const dialog = document.querySelector("[role=dialog]") as HTMLElement;
    await type(dialog.querySelector("input") as HTMLInputElement, "30");
    const select = dialog.querySelector("select") as HTMLSelectElement;
    select.value = "REJECT";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await click(byText(dialog, "button", "Hold for approval")!);
    await settle();
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.path).toBe(`/smo/ran-nf-oam/rapp-approval-policy/${INVOKER}`);
    expect(put.body).toEqual({ timeoutSeconds: 1800, onTimeout: "REJECT" });          // who set it is pinned by the BFF
  });

  // Pins down: refuses a timeout that is not a whole number of minutes in range, without calling.
  it("refuses a timeout that is not a whole number of minutes in range, without calling", async () => {
    const calls = bff("admin");
    await open();
    await settle();
    await click(byText(document.body, "button", "Approval…")!);
    const dialog = document.querySelector("[role=dialog]") as HTMLElement;
    for (const bad of ["", "0", "1.5", "10081", "soon"]) {
      await type(dialog.querySelector("input") as HTMLInputElement, bad);
      await click(byText(dialog, "button", "Hold for approval")!);
      expect(dialog.querySelector("[role=alert]")?.textContent).toContain("whole number of minutes");
    }
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  // Pins down: offers to stop holding only where there is a policy, and an operator is offered neither (it is an admin's decision).
  it("offers to stop holding only where there is a policy, and an operator is offered neither (it is an admin's decision)", async () => {
    bff("admin", { invokerId: INVOKER, timeoutSeconds: 3600, onTimeout: "EXPIRE", setBy: "admin", updatedAt: "2026-10-09T00:00:00Z" });
    const admin = await open();
    await settle();
    expect(byText(admin.container, "button", "Stop holding")).not.toBeNull();
    cleanup();
    bff("operator", { invokerId: INVOKER, timeoutSeconds: 3600, onTimeout: "EXPIRE", setBy: "admin", updatedAt: "2026-10-09T00:00:00Z" });
    const operator = await open();
    await settle();
    expect(byText(operator.container, "button", "Stop holding")).toBeNull();
    expect(byText(operator.container, "button", "Approval…")).toBeNull();
    expect(operator.container.textContent).toContain("Held for approval");                  // it can see it
  });
});

describe("stopping every rApp at once", () => {
  const IID2 = "0b9f3f1e-4b0e-4a0c-9d6f-222222222222";
  const three = { items: [
    { instanceId: IID, packageId: "p", state: "RUNNING", autonomyMode: "ASSIST" },
    { instanceId: IID2, packageId: "p", state: "RUNNING", autonomyMode: "AUTONOMOUS" },
    { instanceId: "gone", packageId: "p", state: "UNDEPLOYED", autonomyMode: "SHADOW" },
  ], limit: 200, offset: 0, total: 3 };

  // Pins down: names how many rApps it will stop, then stops each one with the reason and says how it went.
  it("names how many rApps it will stop, then stops each one with the reason and says how it went", async () => {
    const calls = bff("operator", null, {
      "GET /smo/rapp-mgmt/instances": three,
      [`PUT /smo/rapp-mgmt/instances/${IID}/kill`]: { invokerId: INVOKER },
      [`PUT /smo/rapp-mgmt/instances/${IID2}/kill`]: { status: 409, body: { title: "NO_CREDENTIAL", detail: "no credential" } },
    });
    await open();
    await settle();
    await click(byText(document.body, "button", "Stop all rApp writes")!);
    await settle();
    const dialog = document.querySelector("[role=dialog]") as HTMLElement;
    expect(dialog.textContent).toContain("Stop all 2 rApps?");                         // the undeployed one cannot write: not counted
    expect(calls.some((c) => c.method === "PUT")).toBe(false);                        // nothing stopped before the confirm
    await type(dialog.querySelector("input") as HTMLInputElement, "storm");
    await click(byText(dialog, "button", "Stop 2 rApps")!);
    await settle(8);
    const puts = calls.filter((c) => c.method === "PUT");
    expect(puts.map((c) => c.path)).toEqual([`/smo/rapp-mgmt/instances/${IID}/kill`, `/smo/rapp-mgmt/instances/${IID2}/kill`]);
    expect(puts[0].body).toEqual({ reason: "storm" });
    const result = document.querySelector("[data-section='safeguards.stopall']")!.textContent ?? "";
    expect(result).toContain("Stopped 1 rApp; 1 failed");
    expect(result).toContain("no credential");
  });

  // Pins down: cancelling the confirm stops nothing.
  it("cancelling the confirm stops nothing", async () => {
    const calls = bff("operator", null, { "GET /smo/rapp-mgmt/instances": three });
    await open();
    await settle();
    await click(byText(document.body, "button", "Stop all rApp writes")!);
    await settle();
    await click(byText(document.querySelector("[role=dialog]") as HTMLElement, "button", "Cancel")!);
    await settle();
    expect(document.querySelector("[role=dialog]")).toBeNull();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  // Pins down: is not offered to a viewer.
  it("is not offered to a viewer", async () => {
    bff("viewer");
    await open();
    await settle();
    expect(byText(document.body, "button", "Stop all rApp writes")).toBeNull();
  });
});

describe("the tabs", () => {
  // Pins down: shows refusals with a reason badge, paged and bounded to the last 7 days by default.
  it("shows refusals with a reason badge, paged and bounded to the last 7 days by default", async () => {
    const calls = bff("operator", null, {
      "GET /smo/ran-nf-oam/safeguard-refusals": { items: [{ refusalId: "r1", occurredAt: "2026-10-09T10:00:00Z", invokerId: INVOKER, requestedBy: "energy-saving", refusal: "RAPP_RATE_LIMITED", detail: "11th job", announced: true }], limit: 50, offset: 0, total: 1 },
    });
    const { container } = await open();
    await settle();
    await click(byText(container, "[role=tab]", "Refusals")!);
    await settle();
    expect(window.location.hash).toBe("#refusals");
    expect(container.querySelector("tbody tr .badge")?.textContent).toBe("RAPP_RATE_LIMITED");
    const asked = calls.filter((c) => c.path === "/smo/ran-nf-oam/safeguard-refusals" && c.query.get("limit") !== "1").at(-1)!;
    expect(Math.abs(Date.now() - 7 * 86_400_000 - Date.parse(asked.query.get("since") ?? ""))).toBeLessThan(60_000);
  });
});
