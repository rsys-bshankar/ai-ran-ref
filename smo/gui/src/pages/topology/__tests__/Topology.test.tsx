// @vitest-environment jsdom
/** Tests of the RAN topology page (pages/topology) against a fake BFF: the relation tiles come from `/topology/links/counts`, the problem table
 * is server-paged and filtered (`reciprocal=false&link_type=…`), the focus picker asks the element search, the neighbour graph draws the focused element with a dashed edge for a one-way relation, the relation check calls
 * `/topology/relation` with both DNs, and the graph layout rules (`data/graph.ts`). Run: `npx vitest run src/pages/topology`. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith, type Call } from "../../../testing/bff";
import { byText, cleanup, click, settle, type } from "../../../testing/dom";
import type { CellLink } from "../../element/data/types";
import { buildGraph, countLinks, fixHint, MAX_NODES, problemLinks } from "../data/graph";
import { Topology } from "../index";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const link = (p: Partial<CellLink>): CellLink => ({ aElement: "ME-1", aCell: "c1", bElement: "ME-2", bCell: "c5", linkType: "INTER_ELEMENT", reciprocal: true, sameSectorGroup: false, sameIncidentZone: false, ...p });

const LINKS: CellLink[] = [
  link({}),
  link({ aCell: "c2", bCell: "c6", reciprocal: false }),
  link({ aCell: "c1", bElement: "ME-1", bCell: "c2", linkType: "INTRA_ELEMENT" }),
  link({ aCell: "c2", bElement: null, bCell: "ext-9", linkType: "EXTERNAL", reciprocal: false }),
  link({ aElement: "ME-3", aCell: "c7", bElement: null, bCell: "c9", linkType: "AMBIGUOUS", reciprocal: false }),
];

/** A fake BFF for the page; `calls` records every request. */
function bff(role: "viewer" | "operator" | "admin" = "viewer") {
  return fakeBff({
    "GET /me": { username: "ana", role, csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role, rules },
    "GET /smo/*": (c: Call) => {
      const path = decodeURIComponent(c.path);
      if (path === "/smo/ran-nf-oam/topology/links/counts") return { total: 1234, notReciprocal: 30, external: 7, ambiguous: 3, intraElement: 1, interElement: 2 };
      if (path === "/smo/ran-nf-oam/topology/links") {
        const me = c.query.get("managed_element_ref");
        const type = c.query.get("link_type");
        const rec = c.query.get("reciprocal");
        const items = LINKS.filter((l) => (!me || l.aElement === me || l.bElement === me) && (!type || l.linkType === type) && (rec === null || String(l.reciprocal) === rec));
        return c.query.get("limit") ? { items, total: items.length, limit: Number(c.query.get("limit")), offset: 0 } : { items };
      }
      if (path === "/smo/ran-nf-oam/managed-entities") return { items: [{ managedElementRef: c.query.get("search") ? `${c.query.get("search")}-7` : "ME-1" }], total: c.query.get("limit") === "1" ? 812 : 1, limit: 100, offset: 0 };
      if (path === "/smo/ran-nf-oam/cell-guards") return { items: [], total: 2436, limit: 1, offset: 0 };
      if (path === "/smo/ran-nf-oam/managed-entities/ME-1") return { managedElementRef: "ME-1", vendorName: "vendor-a", o1Protocol: "NETCONF", region: "eu-west", tenant: "acme", cellGuards: { c1: { cellClass: "COVERAGE_CRITICAL", sectorGroup: "sg-1", incidentZone: null, neighbourRefs: ["c5", "c2"] }, c2: { cellClass: "NORMAL", sectorGroup: "sg-1", incidentZone: null, neighbourRefs: ["c6"] } } };
      if (path === "/smo/ran-nf-oam/topology/relation") return { a: c.query.get("a"), b: c.query.get("b"), relation: "SIBLING" };
      return { status: 404, body: { title: "NOT_FOUND" } };
    },
  });
}

/** Mounts the page at `at`. */
const open = (at = "/topology") => mountWith(<AuthProvider><Topology /></AuthProvider>, { at });

describe("RAN topology page", () => {
  // The tiles show the server's relation counts (one-way = not reciprocal − external − ambiguous) and the guarded-cell / element totals.
  it("counts the relations and their problems from /topology/links/counts", async () => {
    const calls = bff();
    const { container } = await open();
    await settle();
    const tiles = container.querySelector("[data-section='topology.tiles']")!;
    expect(tiles.textContent).toContain("2,436");
    expect(tiles.textContent).toContain("812 managed elements");
    expect(byText(tiles, ".kpi", /Neighbour relations1,234/)).not.toBeNull();
    expect(byText(tiles, ".kpi", /Not reciprocal20/)).not.toBeNull();
    expect(byText(tiles, ".kpi", /External7/)).not.toBeNull();
    expect(byText(tiles, ".kpi", /Ambiguous3/)).not.toBeNull();
    expect(calls.some((c) => c.path.endsWith("/topology/links") && !c.query.get("limit"))).toBe(false);   // the whole list is never read
  });

  // The problem table lists the one-way relations first, and a tile switches it to another kind.
  it("lists the relations that need attention, each linking to the cell guards where it is fixed", async () => {
    const calls = bff();
    const { container } = await open();
    await settle();
    const asked = calls.filter((c) => c.path.endsWith("/topology/links")).at(-1)!;
    expect(asked.query.get("reciprocal")).toBe("false");
    expect(asked.query.get("link_type")).toBe("INTER_ELEMENT");
    expect(asked.query.get("limit")).not.toBeNull();
    const table = container.querySelector("[data-section='topology.problems']")!;
    expect(table.textContent).toContain("ME-1 / c2");
    expect(table.textContent).toContain("add c2 to the neighbours of ME-2 / c6");
    expect(table.querySelector("a[href='/elements/ME-2#guards']")).not.toBeNull();
    await click(byText(container, ".kpi", /External/)!);
    await settle();
    expect(calls.filter((c) => c.path.endsWith("/topology/links")).at(-1)!.query.get("link_type")).toBe("EXTERNAL");
    expect(table.textContent).toContain("ext-9");
    expect(table.textContent).toContain("onboard the neighbour");
  });

  // Focusing an element draws its cells and neighbours; the one-way relation is a dashed (`oneway`) edge.
  it("draws the focused element's neighbour graph with dashed one-way edges", async () => {
    const calls = bff();
    const { container } = await open("/topology?me=ME-1");
    await settle();
    const graph = container.querySelector("[data-section='topology.graph'] .graph svg")!;
    expect(graph.querySelectorAll(".g-node").length).toBeGreaterThanOrEqual(5);
    expect(graph.querySelectorAll(".g-edge.oneway").length).toBe(1);
    expect(graph.querySelectorAll(".g-node.warn").length).toBe(1);                 // the external neighbour
    expect(container.textContent).toContain("Neighbours of ME-1");
    expect(calls.some((c) => c.path.endsWith("/topology/links") && c.query.get("managed_element_ref") === "ME-1")).toBe(true);
  });

  // The focus picker asks RAN NF OAM's element search once the typing pauses, and offers what it found.
  it("suggests elements from the server search", async () => {
    const calls = bff();
    const { container } = await open();
    await settle();
    await type(container.querySelector("input[aria-label='Focus element']") as HTMLInputElement, "du");
    await new Promise((r) => setTimeout(r, 250));
    await settle();
    const search = calls.filter((c) => c.path === "/smo/ran-nf-oam/managed-entities" && c.query.get("search"));
    expect(search.at(-1)!.query.get("search")).toBe("du");
    expect(container.querySelector("#topology-elements option[value='du-7']")).not.toBeNull();
  });

  // The relation check sends both DNs and shows the answer.
  it("checks how two managed objects stand in the containment tree", async () => {
    const calls = bff();
    const { container } = await open();
    await settle();
    const box = container.querySelector("[data-section='topology.check']")!;
    const [a, b] = Array.from(box.querySelectorAll("input"));
    await type(a as HTMLInputElement, "ManagedElement=ME-1,GNBDUFunction=1");
    await type(b as HTMLInputElement, "ManagedElement=ME-1,GNBCUCPFunction=1");
    await click(byText(box, "button", "Check")!);
    await settle();
    const call = calls.find((c) => c.path.endsWith("/topology/relation"))!;
    expect(call.query.get("a")).toBe("ManagedElement=ME-1,GNBDUFunction=1");
    expect(box.textContent).toContain("SIBLING");
  });
});

describe("relation rules (data/graph.ts)", () => {
  // One-way counts only relations between managed cells; external and ambiguous are counted apart.
  it("counts and filters problem relations", () => {
    const c = countLinks(LINKS);
    expect(c).toEqual({ total: 5, inter: 2, intra: 1, notReciprocal: 1, external: 1, ambiguous: 1 });
    expect(problemLinks(LINKS, "oneway").map((l) => l.bCell)).toEqual(["c6"]);
    expect(fixHint(LINKS[3]).text).toContain("onboard");
  });

  // The graph never draws more than MAX_NODES nodes and says how many it left out.
  it("caps the neighbour graph at 200 nodes", () => {
    const many = Array.from({ length: 300 }, (_, i) => link({ bElement: `ME-${i + 10}`, bCell: `n${i}` }));
    const g = buildGraph("ME-1", ["c1"], many);
    expect(g.nodes.length).toBeLessThanOrEqual(MAX_NODES);
    expect(g.hidden).toBe(300 - (MAX_NODES - 2));
  });
});
