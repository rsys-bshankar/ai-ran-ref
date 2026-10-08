// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { PinnedRapps, NAV } from "../components/Layout";
import { fakeBff, mountWith, type Call } from "../testing/bff";
import { byText, cleanup, click, settle, type } from "../testing/dom";
import { RappDetail } from "./RappDetail";
import { RappDirectory } from "./RappDirectory";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const IID = "0b9f3f1e-4b0e-4a0c-9d6f-111111111111";
const row = (n: number, extra: Record<string, unknown> = {}) => ({
  instanceId: n === 1 ? IID : `0b9f3f1e-4b0e-4a0c-9d6f-00000000000${n}`, packageId: "p", name: `rApp ${n}`, version: "1.0.0", vendor: n % 2 ? "Acme" : "Beta",
  state: "RUNNING", autonomyMode: "SHADOW", hasPage: n < 3, operatorApiRegistered: n === 1, pinned: false, ...extra,
});

function directory(overrides: Record<string, unknown> = {}) {
  return fakeBff({
    "GET /rapps": (c: Call) => ({ items: [row(1), row(2), row(3)].filter((r) => !c.query.get("search") || r.name.includes(c.query.get("search")!)), total: 3, limit: 25, offset: 0, owners: ["Acme", "Beta"], states: ["RUNNING"] }),
    "GET /me/pins": { max: 5, items: [] },
    [`PUT /me/pins/${IID}`]: { instanceId: IID, pinned: true },
    ...overrides,
  });
}

describe("RappDirectory", () => {
  it("lists every rApp with a link to its own page and says which declare a page", async () => {
    directory();
    const { container } = await mountWith(<RappDirectory />);
    await settle();
    const rows = Array.from(container.querySelectorAll("tbody tr"));
    expect(rows).toHaveLength(3);
    expect(rows[0].querySelector("a")?.getAttribute("href")).toBe(`/rapps/${IID}`);
    expect(rows[0].textContent).toContain("Acme");
    expect(rows[0].textContent).toContain("declared");
    expect(rows[1].textContent).toContain("declared, API not registered");
    expect(rows[2].textContent).toContain("overview only");
    expect(container.textContent).toContain("3 rApps");
  });

  it("searches after a short pause, sends the filters, and offers the owners and states the BFF reports", async () => {
    const calls = directory();
    const { container } = await mountWith(<RappDirectory />);
    await settle();
    await type(container.querySelector("input[type=search]") as HTMLInputElement, "rApp 2");
    await new Promise((r) => setTimeout(r, 400));
    await settle();
    expect(calls.filter((c) => c.path === "/rapps").map((c) => c.query.get("search"))).toEqual([null, "rApp 2"]);
    expect(container.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(Array.from(container.querySelectorAll("select[aria-label='Filter by owner'] option")).map((o) => o.textContent)).toEqual(["All owners", "Acme", "Beta"]);
    const state = container.querySelector("select[aria-label='Filter by state']") as HTMLSelectElement;
    state.value = "RUNNING";
    state.dispatchEvent(new Event("change", { bubbles: true }));
    await settle();
    expect(calls.filter((c) => c.path === "/rapps").at(-1)!.query.get("state")).toBe("RUNNING");
  });

  it("says so when nothing matches", async () => {
    directory({ "GET /rapps": { items: [], total: 0, limit: 25, offset: 0, owners: [], states: [] } });
    const { container } = await mountWith(<RappDirectory />);
    await settle();
    expect(container.textContent).toContain("No rApp instances yet");
  });

  it("pins and unpins with the star", async () => {
    const calls = directory({ "GET /rapps": { items: [row(1), row(2, { pinned: true })], total: 2, limit: 25, offset: 0, owners: [], states: [] }, [`DELETE /me/pins/${row(2).instanceId}`]: { status: 204 } });
    const { container } = await mountWith(<RappDirectory />);
    await settle();
    await click(container.querySelector("button[aria-label='Pin rApp 1']") as HTMLElement);
    await settle();
    expect(calls.some((c) => c.method === "PUT" && c.path === `/me/pins/${IID}`)).toBe(true);
    await click(container.querySelector("button[aria-label='Unpin rApp 2']") as HTMLElement);
    await settle();
    expect(calls.some((c) => c.method === "DELETE" && c.path === `/me/pins/${row(2).instanceId}`)).toBe(true);
  });

  it("at five pins the unpinned rows cannot be pinned (the BFF refuses a sixth too)", async () => {
    const five = [1, 2, 3, 4, 5].map((n) => row(n, { pinned: true }));
    directory({ "GET /me/pins": { max: 5, items: five }, "GET /rapps": { items: [...five.slice(0, 1), row(6)], total: 2, limit: 25, offset: 0, owners: [], states: [] } });
    const { container } = await mountWith(<RappDirectory />);
    await settle();
    const pin = container.querySelector("button[aria-label='Pin rApp 6']") as HTMLButtonElement;
    expect(pin.disabled).toBe(true);
    expect(pin.title).toContain("At most 5");
    expect((container.querySelector("button[aria-label='Unpin rApp 1']") as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows the BFF's error", async () => {
    directory({ "GET /rapps": { status: 502, body: { title: "R1_UNREACHABLE", detail: "R1 Termination did not answer" } } });
    const { container } = await mountWith(<RappDirectory />);
    await settle();
    expect(container.textContent).toContain("R1_UNREACHABLE");
  });
});

describe("the sidebar's pinned rApps", () => {
  it("lists the pins under the one rApps entry, each linking to its page", async () => {
    fakeBff({ "GET /me/pins": { max: 5, items: [row(1, { pinned: true }), row(2, { pinned: true, name: null })] } });
    const { container } = await mountWith(<PinnedRapps />);
    await settle();
    const links = Array.from(container.querySelectorAll("a"));
    expect(links.map((a) => [a.textContent, a.getAttribute("href")])).toEqual([["↳rApp 1", `/rapps/${IID}`], ["↳0b9f3f1e…", `/rapps/${row(2).instanceId}`]]);
  });

  it("draws nothing without pins or when the pins cannot be read", async () => {
    fakeBff({ "GET /me/pins": { items: [] } });
    expect((await mountWith(<PinnedRapps />)).container.querySelector("ul")).toBeNull();
    cleanup();
    fakeBff({ "GET /me/pins": { status: 500, body: {} } });
    expect((await mountWith(<PinnedRapps />)).container.querySelector("ul")).toBeNull();
  });

  it("the sidebar has one rApps entry and no entry of a single rApp", () => {
    const labels = NAV.map((n) => n.label);
    expect(labels.filter((l) => l === "rApps")).toHaveLength(1);
    for (const gone of ["Energy Saving", "Mobility", "Coverage", "Traffic Steering"]) expect(labels).not.toContain(gone);
    for (const to of NAV.map((n) => n.to)) expect(to).not.toMatch(/energy-saving|mobility|coverage|traffic-steering/);
  });
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

  it("draws the declared page first and the platform overview after it", async () => {
    detail(DECLARED);
    const { container } = await open();
    await settle(8);
    const titles = Array.from(container.querySelectorAll("h1, h2")).map((h) => h.textContent);
    expect(titles[0]).toBe("rApp 1 1.0.0");
    expect(titles.slice(1, 4)).toEqual(["Closed loop", "Platform overview", "Lifecycle"]);
    expect(buttons(container)).toContain("Evaluate now");
    expect(container.textContent).toContain("No performance reports.");
    expect(container.textContent).toContain("No faults reported.");
  });

  it("a viewer's page has no change button", async () => {
    detail({ ...DECLARED, canChange: false });
    const { container } = await open();
    await settle();
    expect(buttons(container)).not.toContain("Evaluate now");
    expect(container.textContent).toContain("Changing needs the operator role");
  });

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

  it("pins from the page header", async () => {
    const calls = detail(DECLARED);
    calls.length = 0;
    const { container } = await open();
    await settle();
    await click(byText(container, "button", "☆ Pin")!);
    await settle();
    expect(calls.some((c) => c.method === "PUT" && c.path === `/me/pins/${IID}`)).toBe(true);
  });

  it("an unknown rApp is an error with a way back", async () => {
    fakeBff({ [`GET /rapps/${IID}`]: { status: 404, body: { title: "NO_SUCH_RAPP", detail: "no such rApp instance" } }, "GET /me/pins": { items: [] } });
    const { container } = await open();
    await settle();
    expect(container.textContent).toContain("NO_SUCH_RAPP");
    expect(container.querySelector("a")?.getAttribute("href")).toBe("/rapps");
  });
});

const buttons = (c: HTMLElement) => Array.from(c.querySelectorAll("button")).map((b) => b.textContent);
