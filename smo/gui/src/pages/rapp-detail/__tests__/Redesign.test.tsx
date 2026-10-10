// @vitest-environment jsdom
/** Tests of the redesigned parts of the rApp detail page (`pages/rapp-detail`): lifecycle flow steppers linking to their boards, Stop with a
 * reason and Resume (the safeguard kill calls), recent decisions asked for this rApp's invoker, the read-only autonomy control and the
 * cell-state gap note. Run: `npx vitest run src/pages/rapp-detail`. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import { fakeBff, mountWith } from "../../../testing/bff";
import { byText, cleanup, click, settle, type } from "../../../testing/dom";
import { RappDetail } from "..";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; });

const IID = "0b9f3f1e-4b0e-4a0c-9d6f-111111111111";
const ALL = ["GET", "POST", "PUT", "DELETE"].map((method) => ({ method, pattern: ".*", role: method === "GET" ? "viewer" : "operator", queryMatch: {} }));
const S = `/smo/rapp-mgmt/instances/${IID}`;

/** A fake BFF for one RUNNING rApp with safeguards (stopped when `killed`), its package, deployment and decisions. */
function bff(killed = false) {
  return fakeBff({
    [`GET /rapps/${IID}`]: { instanceId: IID, packageId: "p1", name: "Energy", version: "1.2.0", vendor: "Acme", state: "RUNNING", autonomyMode: "ASSIST", hasPage: false,
      operatorApiRegistered: false, pinned: false, declarationState: "none", declaration: null, readOnly: false, canChange: false },
    "GET /me/pins": { max: 5, items: [] },
    "GET /me": { username: "ana", role: "operator", csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false }, "GET /permissions": { role: "operator", rules: ALL },
    [`GET ${S}`]: { instanceId: IID, packageId: "p1", state: "RUNNING", autonomyMode: "ASSIST", workloadRef: "nf-1", regionScope: null, configuration: {}, pendingUpgradeInstanceId: null },
    [`GET ${S}/performance`]: { items: [{ reportId: "r", metrics: { x: 1 }, reportedAt: "2026-01-01T00:00:00Z" }] }, [`GET ${S}/faults`]: { items: [] },
    [`GET ${S}/safeguards`]: { instanceId: IID, invokerId: "inv-1", killed, kill: killed ? { invokerId: "inv-1", killedBy: "ana", reason: "test", killedAt: "2026-01-01T00:00:00Z" } : null,
      limits: { invokerId: "inv-1", maxConfigJobsPerHour: 10, maxElementsPerJob: 2, maxChangePercent: 25, updatedAt: "t", configJobsLastHour: 3 } },
    [`GET ${S}/versions`]: { versions: [], rollbackTarget: null },
    "GET /smo/onboarding/packages/p1/onboarding-status": { packageId: "p1", state: "AVAILABLE", nfDeploymentDescriptorId: "d1" },
    "GET /smo/onboarding/packages/p1/usage": { items: [{ registrationId: "u", consumerId: IID, stoppedAt: null, active: true }] },
    "GET /smo/nfo/deployments/nf-1": { nfDeploymentId: "nf-1", name: "es", state: "RUNNING", clusterId: "c", nfDeploymentDescriptorId: "d1", workloadRef: null, requiredResourceTypeId: null, abnormalReason: null },
    "GET /smo/ran-nf-oam/decision-records": { items: [{ decisionId: "d", occurredAt: "2026-01-01T00:00:00Z", invokerId: "inv-1", requestedBy: "x", disposition: "DIRECT", jobId: null, approvalId: null,
      actionId: null, inputsRef: null, modelVersion: null, rationale: "PRB 9% for 30 min", approvedBy: null, decidedBy: null, decidedAt: null, managedElements: ["du-1"], changeCount: 1, correlationId: null, contentHash: "h", auditSeq: 1 }],
      limit: 10, offset: 0, total: 42, hasMore: false },
    [`PUT ${S}/kill`]: { killed: true }, [`DELETE ${S}/kill`]: { status: 204 },
  });
}
const open = () => mountWith(<AuthProvider><RappDetail /></AuthProvider>, { at: `/rapps/${IID}`, route: "/rapps/:instanceId" });

describe("rApp detail · redesign", () => {
  // flows 01, 06 and 07 are evaluated from live state and each row opens its board for this rApp's subject
  it("draws the lifecycle flows as steppers linking to their boards", async () => {
    bff();
    const { container } = await open();
    await settle(10);
    const box = container.querySelector("[data-section='rapp.flows']")!;
    const hrefs = Array.from(box.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(hrefs).toContain(`/flows/07?subject=${IID}`);
    expect(hrefs).toContain("/flows/01?subject=p1");
    expect(hrefs).toContain("/flows/06?subject=p1");
    expect(box.querySelectorAll(".ministeps").length).toBe(3);
    expect(box.textContent).toContain("6/6 · complete");
    expect(box.textContent).toContain("links no AI/ML model");
  });

  // Stop asks for a reason and sends the safeguard kill; a stopped rApp offers Resume
  it("stops with a reason and resumes", async () => {
    const calls = bff();
    const { container } = await open();
    await settle(8);
    await click(byText(container, "button", "Stop rApp")!);
    await type(document.querySelector(".modal input") as HTMLInputElement, "storm");
    await click(byText(document.body, "button", "Stop writes")!);
    await settle();
    const put = calls.find((c) => c.method === "PUT" && c.path === `${S}/kill`);
    expect(put?.body).toEqual({ reason: "storm" });
    cleanup();
    const again = bff(true);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const killed = await open();
    await settle(8);
    await click(byText(killed.container, "button", "Resume")!);
    await settle();
    expect(again.some((c) => c.method === "DELETE" && c.path === `${S}/kill`)).toBe(true);
  });

  // the latest ten decisions of this rApp's invoker, without a total, with a link to Decisions filtered to it
  it("reads the recent decisions of this rApp", async () => {
    const calls = bff();
    const { container } = await open();
    await settle(10);
    const q = calls.find((c) => c.path === "/smo/ran-nf-oam/decision-records" && c.query.get("total") === "false")!.query;
    expect([q.get("invoker_id"), q.get("limit")]).toEqual(["inv-1", "10"]);
    const box = container.querySelector("[data-section='rapp.decisions']")!;
    expect(box.textContent).toContain("PRB 9% for 30 min");
    expect(box.querySelector("a")?.getAttribute("href")).toBe("/decisions?invoker=inv-1");
    expect(container.querySelector("[data-section='rapp.kpis']")!.textContent).toContain("42");
  });

  // no backend route changes the autonomy mode, so the control is read-only; the cell-state box is a gap note
  it("shows the autonomy mode read-only and the cell-state gap", async () => {
    bff();
    const { container } = await open();
    await settle(8);
    const radios = Array.from(container.querySelectorAll("[role=radiogroup] [role=radio]")) as HTMLButtonElement[];
    expect(radios.map((r) => r.disabled)).toEqual([true, true, true]);
    expect(radios.find((r) => r.getAttribute("aria-checked") === "true")?.textContent).toBe("Assist");
    expect(container.querySelector("[data-section='rapp.cells'] .gap-note")).not.toBeNull();
    expect(container.querySelector("[data-section='rapp.safeguards']")!.textContent).toContain("3 / 10");
  });
});
