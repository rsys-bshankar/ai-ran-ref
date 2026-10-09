// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import rules from "../auth/permissions.fixture.json";
import { fakeBff, mountWith } from "../testing/bff";
import { byText, cleanup, click, settle, type } from "../testing/dom";
import { Safeguards } from "./Safeguards";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const IID = "0b9f3f1e-4b0e-4a0c-9d6f-111111111111";
const INVOKER = "api-invoker-0a1b2c3d";

function bff(role: "viewer" | "operator" | "admin", approvalPolicy: unknown = null) {
  return fakeBff({
    "GET /me": { username: "ana", role, csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role, rules },
    "GET /smo/rapp-mgmt/instances": { items: [{ instanceId: IID, packageId: "p", state: "RUNNING", autonomyMode: "ASSIST" }], limit: 200, offset: 0 },
    [`GET /smo/rapp-mgmt/instances/${IID}/safeguards`]: { body: { instanceId: IID, invokerId: INVOKER, killed: false, kill: null, limits: null, approvalPolicy } },
    "GET /smo/ran-nf-oam/rapp-kill": { items: [], limit: 200, offset: 0 },
    [`PUT /smo/ran-nf-oam/rapp-approval-policy/${INVOKER}`]: { invokerId: INVOKER },
    [`DELETE /smo/ran-nf-oam/rapp-approval-policy/${INVOKER}`]: { status: 204 },
  });
}

const open = () => mountWith(<AuthProvider><Safeguards /></AuthProvider>);

describe("holding an rApp's changes for approval (AI-11.4)", () => {
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
