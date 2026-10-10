// @vitest-environment jsdom
/** Tests of the Safeguards page (pages/safeguards): the per-rApp limits table and its approval-policy actions (AI-11.4, with one or two approvers), RBAC on them, the
 * one-call "Stop all rApp writes" (`PUT /rapp-mgmt/kill-all`) with its confirm and result, the admin's "Resume all", and the tabs. Run: `npx vitest run src/pages/safeguards`. */
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

  // The policy dialog defaults to one approval and sends `requiredApprovals: 2` only when two is chosen, so a policy left at one sends no such key.
  it("lets an admin ask for two different people to approve, and sends nothing extra when it is left at one", async () => {
    const calls = bff("admin");
    await open();
    await settle();
    await click(byText(document.body, "button", "Approval…")!);
    let dialog = document.querySelector("[role=dialog]") as HTMLElement;
    const needed = dialog.querySelectorAll("select")[1] as HTMLSelectElement;
    expect(needed.value).toBe("1");                                                                 // the default is the single approval
    needed.value = "2";
    needed.dispatchEvent(new Event("change", { bubbles: true }));
    await click(byText(dialog, "button", "Hold for approval")!);
    await settle();
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ timeoutSeconds: 3600, onTimeout: "EXPIRE", requiredApprovals: 2 });
    cleanup();
    const second = bff("admin");
    await open();
    await settle();
    await click(byText(document.body, "button", "Approval…")!);
    dialog = document.querySelector("[role=dialog]") as HTMLElement;
    await click(byText(dialog, "button", "Hold for approval")!);
    await settle();
    expect(second.find((c) => c.method === "PUT")!.body).toEqual({ timeoutSeconds: 3600, onTimeout: "EXPIRE" });       // no requiredApprovals key
  });

  // A policy with `requiredApprovals: 2` is described as needing two different people, and its dialog opens with two selected.
  it("shows a policy that asks for two approvals, and opens its dialog with two selected", async () => {
    bff("admin", { invokerId: INVOKER, timeoutSeconds: 3600, onTimeout: "EXPIRE", requiredApprovals: 2, setBy: "admin", updatedAt: "2026-10-09T00:00:00Z" });
    const view = await open();
    await settle();
    expect(view.container.textContent).toContain("Held for approval · two different people must approve · lapses after 1 h (expires)");
    await click(byText(document.body, "button", "Approval…")!);
    expect(((document.querySelector("[role=dialog]") as HTMLElement).querySelectorAll("select")[1] as HTMLSelectElement).value).toBe("2");
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

  // Pins down: the confirm names the live and already-stopped counts; one PUT with the reason; the result lists stopped, already stopped and failed.
  it("stops every rApp in one call and says how it went", async () => {
    const calls = bff("operator", null, {
      "GET /smo/rapp-mgmt/kill-all": { stopped: 1, instances: 3 },
      "PUT /smo/rapp-mgmt/kill-all": { stopped: 1, alreadyStopped: 1, failed: [{ instanceId: IID2, error: "no credential" }] },
    });
    await open();
    await settle();
    expect(byText(document.body, "button", "Resume all")).toBeNull();                 // resume is an admin's
    await click(byText(document.body, "button", "Stop all rApp writes")!);
    await settle();
    const dialog = document.querySelector("[role=dialog]") as HTMLElement;
    expect(dialog.textContent).toContain("stops the writes of 2 running rApp instances");
    expect(dialog.textContent).toContain("1 of 3 are already stopped");
    expect(calls.some((c) => c.method === "PUT")).toBe(false);                        // nothing stopped before the confirm
    await type(dialog.querySelector("input") as HTMLInputElement, "storm");
    await click(byText(dialog, "button", "Stop all rApps")!);
    await settle(8);
    const puts = calls.filter((c) => c.method === "PUT");
    expect(puts.map((c) => c.path)).toEqual(["/smo/rapp-mgmt/kill-all"]);
    expect(puts[0].body).toEqual({ reason: "storm" });
    const result = document.querySelector("[data-section='safeguards.stopall']")!.textContent ?? "";
    expect(result).toContain("Stopped 1 rApp · 1 already stopped · 1 failed");
    expect(result).toContain("no credential");
  });

  // Pins down: an admin resumes every stopped rApp with one DELETE after a confirm naming the count.
  it("resumes all for an admin", async () => {
    const calls = bff("admin", null, {
      "GET /smo/rapp-mgmt/kill-all": { stopped: 4, instances: 5 },
      "DELETE /smo/rapp-mgmt/kill-all": { resumed: 4, failed: [] },
    });
    await open();
    await settle();
    await click(byText(document.body, "button", "Resume all")!);
    await settle();
    const dialog = document.querySelector("[role=dialog]") as HTMLElement;
    expect(dialog.textContent).toContain("resumes 4 stopped rApp instances");
    await click(byText(dialog, "button", "Resume all")!);
    await settle(8);
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/smo/rapp-mgmt/kill-all"]);
    expect(document.querySelector("[data-section='safeguards.stopall']")!.textContent).toContain("Resumed 4 rApps");
  });

  // Pins down: cancelling the confirm stops nothing.
  it("cancelling the confirm stops nothing", async () => {
    const calls = bff("operator", null, { "GET /smo/rapp-mgmt/kill-all": { stopped: 0, instances: 2 } });
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
