// @vitest-environment jsdom
/** Tests of the rApp directory (`pages/rapps/sections/Directory.tsx`), the sidebar's pinned rApps: listing, search and filters,
 * pins and the pin limit, the BFF error. Moved from the pre-redesign
 * `pages/RappPages.test.tsx` with every assertion kept. Run: `npx vitest run src/pages/rapps`. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PinnedRapps, NAV } from "../../../components/Layout";
import { fakeBff, mountWith, type Call } from "../../../testing/bff";
import { cleanup, click, settle, type } from "../../../testing/dom";
import { RappDirectory } from "../sections/Directory";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const IID = "0b9f3f1e-4b0e-4a0c-9d6f-111111111111";
const row = (n: number, extra: Record<string, unknown> = {}) => ({
  instanceId: n === 1 ? IID : `0b9f3f1e-4b0e-4a0c-9d6f-00000000000${n}`, packageId: "p", name: `rApp ${n}`, version: "1.0.0", vendor: n % 2 ? "Acme" : "Beta",
  state: "RUNNING", autonomyMode: "SHADOW", hasPage: n < 3, operatorApiRegistered: n === 1, pinned: false, ...extra,
});

/**
 * Starts the fake BFF with three rApps (search is applied on the name), an empty pin list, the pin route and the directory's owners and states; `overrides` adds or replaces routes.
 */
function directory(overrides: Record<string, unknown> = {}) {
  return fakeBff({
    "GET /rapps": (c: Call) => ({ items: [row(1), row(2), row(3)].filter((r) => !c.query.get("search") || r.name.includes(c.query.get("search")!)), total: 3, limit: 25, offset: 0, owners: ["Acme", "Beta"], states: ["RUNNING"] }),
    "GET /me/pins": { max: 5, items: [] },
    [`PUT /me/pins/${IID}`]: { instanceId: IID, pinned: true },
    ...overrides,
  });
}

describe("RappDirectory", () => {
  // Every rApp is listed with a link to its own page, and the list says which of them declare a page.
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

  // Typing searches after a short pause rather than on each key, the filters are sent as chosen, and the owners and states offered are the ones the BFF reports.
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

  // A search with no match says so.
  it("says so when nothing matches", async () => {
    directory({ "GET /rapps": { items: [], total: 0, limit: 25, offset: 0, owners: [], states: [] } });
    const { container } = await mountWith(<RappDirectory />);
    await settle();
    expect(container.textContent).toContain("No rApp instances yet");
  });

  // The star pins and unpins a rApp through the BFF.
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

  // At the pin limit the unpinned rows cannot be pinned (the BFF refuses a sixth too), while the pinned ones can still be unpinned.
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

  // A failed directory read shows the BFF's error.
  it("shows the BFF's error", async () => {
    directory({ "GET /rapps": { status: 502, body: { title: "R1_UNREACHABLE", detail: "R1 Termination did not answer" } } });
    const { container } = await mountWith(<RappDirectory />);
    await settle();
    expect(container.textContent).toContain("R1_UNREACHABLE");
  });
});

describe("the sidebar's pinned rApps", () => {
  // The pinned rApps are listed under the one rApps menu entry, each linking to its page.
  it("lists the pins under the one rApps entry, each linking to its page", async () => {
    fakeBff({ "GET /me/pins": { max: 5, items: [row(1, { pinned: true }), row(2, { pinned: true, name: null })] } });
    const { container } = await mountWith(<PinnedRapps />);
    await settle();
    const links = Array.from(container.querySelectorAll("a"));
    expect(links.map((a) => [a.textContent, a.getAttribute("href")])).toEqual([["↳rApp 1", `/rapps/${IID}`], ["↳0b9f3f1e…", `/rapps/${row(2).instanceId}`]]);
  });

  // Without pins, or when the pins cannot be read, the sidebar shows no pin list.
  it("draws nothing without pins or when the pins cannot be read", async () => {
    fakeBff({ "GET /me/pins": { items: [] } });
    expect((await mountWith(<PinnedRapps />)).container.querySelector("ul")).toBeNull();
    cleanup();
    fakeBff({ "GET /me/pins": { status: 500, body: {} } });
    expect((await mountWith(<PinnedRapps />)).container.querySelector("ul")).toBeNull();
  });

  // The menu has one rApps entry and no entry for a single rApp, so a new rApp needs no GUI build.
  it("the sidebar has one rApps entry and no entry of a single rApp", () => {
    const labels = NAV.map((n) => n.label);
    expect(labels.filter((l) => l === "rApps")).toHaveLength(1);
    for (const gone of ["Energy Saving", "Mobility", "Coverage", "Traffic Steering"]) expect(labels).not.toContain(gone);
    for (const to of NAV.map((n) => n.to)) expect(to).not.toMatch(/energy-saving|mobility|coverage|traffic-steering/);
  });
});
