// @vitest-environment jsdom
/** Tests of the rApp detail page (`pages/rapp-detail`, route /rapps/<instance>): header and pin, the platform overview, the declared operator
 * page below it, access scope, viewer rights, missing declarations, an unknown rApp, plus the redesign's lifecycle flows, Stop and decisions.
 * Moved from the pre-redesign `pages/RappPages.test.tsx` with every assertion kept. Run: `npx vitest run src/pages/rapp-detail`. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import { fakeBff, mountWith } from "../../../testing/bff";
import { byText, cleanup, click, settle } from "../../../testing/dom";
import { RappDetail } from "..";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const IID = "0b9f3f1e-4b0e-4a0c-9d6f-111111111111";
const row = (n: number, extra: Record<string, unknown> = {}) => ({
  instanceId: n === 1 ? IID : `0b9f3f1e-4b0e-4a0c-9d6f-00000000000${n}`, packageId: "p", name: `rApp ${n}`, version: "1.0.0", vendor: n % 2 ? "Acme" : "Beta",
  state: "RUNNING", autonomyMode: "SHADOW", hasPage: n < 3, operatorApiRegistered: n === 1, pinned: false, ...extra,
});

describe("RappDetail", () => {
  const DECLARED = {
    ...row(1), declarationState: "declared", readOnly: false, canChange: true,
    declaration: { version: 1, panels: [{ id: "controls", title: "Closed loop", kind: "actions", actions: [{ id: "go", label: "Evaluate now", method: "POST", path: "/instances/{instanceId}/evaluate", success: "ok" }] }] },
  };
  const detail = (page: Record<string, unknown>) => fakeBff({
    [`GET /rapps/${IID}`]: page, "GET /me/pins": { max: 5, items: [] },
    "GET /me": { username: "ana", role: "operator", csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false }, "GET /permissions": { role: "operator", rules: [] },
    [`GET /smo/rapp-mgmt/instances/${IID}`]: { instanceId: IID, packageId: "p", state: "RUNNING", autonomyMode: "SHADOW", workloadRef: "w-1", regionScope: null, configuration: {} },
    [`GET /smo/rapp-mgmt/instances/${IID}/performance`]: { items: [] }, [`GET /smo/rapp-mgmt/instances/${IID}/faults`]: { items: [] },
    [`GET /smo/rapp-mgmt/instances/${IID}/safeguards`]: { instanceId: IID, invokerId: "inv", killed: false, kill: null, limits: null },
    [`GET /smo/rapp-mgmt/instances/${IID}/versions`]: { versions: [], rollbackTarget: null },
  });
  const open = () => mountWith(<AuthProvider><RappDetail /></AuthProvider>, { at: `/rapps/${IID}`, route: "/rapps/:instanceId" });

  // redesign (BRIEF §4, handoff RappDetail.dc.html): the header, then the platform overview, then the declared operator page below it
  it("draws the header, the platform overview, and the declared page below it", async () => {
    detail(DECLARED);
    const { container } = await open();
    await settle(8);
    const titles = Array.from(container.querySelectorAll("h1, h2")).map((h) => h.textContent);
    expect(titles[0]).toBe("rApp 1 1.0.0");
    expect(titles[1]).toBe("Platform overview");
    const at = (t: string) => titles.indexOf(t);
    for (const t of ["Lifecycle flows for this rApp", "Lifecycle", "Safeguards", "Faults", "Operator page", "Closed loop"]) expect(at(t)).toBeGreaterThan(at("Platform overview"));
    expect(at("Closed loop")).toBeGreaterThan(at("Operator page"));
    expect(at("Operator page")).toBeGreaterThan(at("Lifecycle"));
    expect(buttons(container)).toContain("Evaluate now");
    expect(container.textContent).toContain("No performance reports.");
    expect(container.textContent).toContain("No faults reported.");
  });

  // pins down: shows the access scope of the instance: the regions and tenants it may touch, or that it is unscoped
  it("shows the access scope of the instance: the regions and tenants it may touch, or that it is unscoped", async () => {
    detail(DECLARED);
    const unscoped = await open();
    await settle(8);
    expect(unscoped.container.textContent).toContain("Access scope");
    expect(unscoped.container.textContent).toContain("Unscoped (every managed element)");
    cleanup();
    fakeBff({
      [`GET /rapps/${IID}`]: DECLARED, "GET /me/pins": { max: 5, items: [] },
      "GET /me": { username: "ana", role: "operator", csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false }, "GET /permissions": { role: "operator", rules: [] },
      [`GET /smo/rapp-mgmt/instances/${IID}`]: { instanceId: IID, packageId: "p", state: "RUNNING", autonomyMode: "SHADOW", workloadRef: "w-1", regionScope: null, configuration: {},
        authzScope: { regions: ["eu-west"], tenants: ["acme"] } },
      [`GET /smo/rapp-mgmt/instances/${IID}/performance`]: { items: [] }, [`GET /smo/rapp-mgmt/instances/${IID}/faults`]: { items: [] },
      [`GET /smo/rapp-mgmt/instances/${IID}/safeguards`]: { instanceId: IID, invokerId: "inv", killed: false, kill: null, limits: null },
      [`GET /smo/rapp-mgmt/instances/${IID}/versions`]: { versions: [], rollbackTarget: null },
    });
    const scoped = await open();
    await settle(8);
    expect(scoped.container.textContent).toContain("regions eu-west · tenants acme");
    expect(scoped.container.textContent).not.toContain("Unscoped (every managed element)");
  });

  // pins down: a viewer's page has no change button
  it("a viewer's page has no change button", async () => {
    detail({ ...DECLARED, canChange: false });
    const { container } = await open();
    await settle();
    expect(buttons(container)).not.toContain("Evaluate now");
    expect(container.textContent).toContain("Changing needs the operator role");
  });

  // pins down: a rApp with no declaration, or one this console cannot read, still has its overview
  it("a rApp with no declaration, or one this console cannot read, still has its overview", async () => {
    detail({ ...DECLARED, declarationState: "none", declaration: null, canChange: false });
    const none = await open();
    await settle(8);
    expect(none.container.textContent).toContain("declares no operator page");
    cleanup();
    detail({ ...DECLARED, declarationState: "unreadable", declaration: null, canChange: false });
    const { container } = await open();
    await settle();
    expect(container.textContent).toContain("could not be read by this console");
    expect(container.textContent).toContain("Platform overview");
  });

  // pins down: pins from the page header
  it("pins from the page header", async () => {
    const calls = detail(DECLARED);
    calls.length = 0;
    const { container } = await open();
    await settle();
    await click(byText(container, "button", "☆ Pin")!);
    await settle();
    expect(calls.some((c) => c.method === "PUT" && c.path === `/me/pins/${IID}`)).toBe(true);
  });

  // pins down: an unknown rApp is an error with a way back
  it("an unknown rApp is an error with a way back", async () => {
    fakeBff({ [`GET /rapps/${IID}`]: { status: 404, body: { title: "NO_SUCH_RAPP", detail: "no such rApp instance" } }, "GET /me/pins": { items: [] } });
    const { container } = await open();
    await settle();
    expect(container.textContent).toContain("NO_SUCH_RAPP");
    expect(container.querySelector("a")?.getAttribute("href")).toBe("/rapps");
  });
});

const buttons = (c: HTMLElement) => Array.from(c.querySelectorAll("button")).map((b) => b.textContent);
