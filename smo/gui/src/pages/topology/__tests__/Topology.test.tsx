// @vitest-environment jsdom
/** Tests of the RAN topology page (pages/topology) against a fake BFF: the relation tiles come from `/topology/links/counts`, the problem table
 * is server-paged and filtered (`reciprocal=false&link_type=…`), the focus picker asks the element search, the neighbour graph draws the focused element with a dashed edge for a one-way relation, the relation check calls
 * `/topology/relation` with both DNs, the graph layout rules (`data/graph.ts`), and the containment tree (GUI-3): its alarm overlay (a folded
 * node shows the worst alarm below it), folding, the drill-down to the element page and the rules of `data/containment.ts`. Run: `npx vitest run src/pages/topology`. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith, withProviders, type Call } from "../../../testing/bff";
import { byText, cleanup, click, mount, settle, type } from "../../../testing/dom";
import type { CellLink } from "../../element/data/types";
import { buildTree, describeAlarms, visibleRows, worse, type ContainmentGraph, type GraphMo } from "../data/containment";
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

/** A managed object of the containment graph. */
const mo = (dn: string, parentDn: string | null, alarms: Record<string, number> = {}, worst: string | null = null): GraphMo => {
  const [cls, id] = dn.split(",").at(-1)!.split("=");
  return { dn, parentDn, class: cls, id, managedElementRef: dn.split(",")[0].split("=")[1], source: "registry", alarms, worst };
};

/** Two elements: ME-1 with a cell carrying a critical alarm under its DU function, ME-2 with a minor alarm on its root. */
const GRAPH: ContainmentGraph = {
  nodes: [mo("ManagedElement=ME-1", null), mo("ManagedElement=ME-1,GNBDUFunction=1", "ManagedElement=ME-1"),
    mo("ManagedElement=ME-1,GNBDUFunction=1,NRCellDU=101", "ManagedElement=ME-1,GNBDUFunction=1", { critical: 2, minor: 1 }, "critical"),
    mo("ManagedElement=ME-2", null, { minor: 1 }, "minor")],
  edges: [{ child: "ManagedElement=ME-1,GNBDUFunction=1", parent: "ManagedElement=ME-1" },
    { child: "ManagedElement=ME-1,GNBDUFunction=1,NRCellDU=101", parent: "ManagedElement=ME-1,GNBDUFunction=1" }],
  total: 4, truncated: false,
};

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
      if (path === "/smo/ran-nf-oam/topology/graph") return GRAPH;
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

describe("the containment tree (GUI-3)", () => {
  const box = (root: HTMLElement) => root.querySelector('[data-section="topology.containment"]') as HTMLElement;
  const row = (root: HTMLElement, dn: string) => box(root).querySelector(`[data-dn="${dn}"]`) as SVGGElement | null;

  // The network view starts with the roots folded, each coloured by the worst open alarm anywhere below it.
  it("draws the roots folded with the worst alarm below them", async () => {
    const calls = bff();
    const { container } = await open();
    await settle(6);
    expect(row(container, "ManagedElement=ME-1")!.getAttribute("data-colour")).toBe("critical");
    expect(row(container, "ManagedElement=ME-2")!.getAttribute("data-colour")).toBe("minor");
    expect(row(container, "ManagedElement=ME-1,GNBDUFunction=1")).toBeNull();
    expect(box(container).textContent).toContain("4 managed objects · 2 with open alarms");
    expect(calls.find((c) => c.path === "/smo/ran-nf-oam/topology/graph")!.query.get("max_nodes")).toBe("500");
  });

  // Unfolding a node shows its children; an unfolded node takes its own colour, and the alarmed cell says what it carries.
  it("unfolds a node to its children", async () => {
    bff();
    const { container } = await open();
    await settle(6);
    await click(box(container).querySelector('[aria-label="Unfold ManagedElement=ME-1"]') as unknown as HTMLElement);
    expect(row(container, "ManagedElement=ME-1")!.getAttribute("data-colour")).toBe("none");
    expect(row(container, "ManagedElement=ME-1,GNBDUFunction=1")!.getAttribute("data-colour")).toBe("critical");
    await click(box(container).querySelector('[aria-label="Unfold GNBDUFunction=1"]') as unknown as HTMLElement);
    expect(row(container, "ManagedElement=ME-1,GNBDUFunction=1,NRCellDU=101")!.textContent).toContain("2 critical, 1 minor");
  });

  // A focused element asks for its own tree, open to the leaves.
  it("opens the focused element's tree", async () => {
    const calls = bff();
    const { container } = await open("/topology?me=ME-1");
    await settle(6);
    expect(calls.find((c) => c.path === "/smo/ran-nf-oam/topology/graph")!.query.get("managed_element_ref")).toBe("ME-1");
    expect(row(container, "ManagedElement=ME-1,GNBDUFunction=1,NRCellDU=101")).not.toBeNull();
  });

  // A node's name opens its element's page on the Managed objects tab.
  it("drills down to the element page", async () => {
    bff();
    const { container } = await mount(withProviders(
      <Routes><Route path="/topology" element={<AuthProvider><Topology /></AuthProvider>} /><Route path="/elements/:me" element={<p id="element-page">element</p>} /></Routes>,
      { at: "/topology" }));
    await settle(6);
    await click(row(container, "ManagedElement=ME-2")!.querySelector(".g-label") as unknown as HTMLElement);
    expect(container.querySelector("#element-page")).not.toBeNull();
  });

  // The pure rules: the worse of two severities, the subtree's worst, the row cap, and the alarm text.
  it("folds, colours and caps by the rules", () => {
    expect(worse("minor", "critical")).toBe("critical");
    expect(worse(null, "warning")).toBe("warning");
    const tree = buildTree(GRAPH);
    expect(tree.roots).toEqual(["ManagedElement=ME-1", "ManagedElement=ME-2"]);
    expect(tree.subtreeWorst.get("ManagedElement=ME-1")).toBe("critical");
    const all = visibleRows(tree, () => true);
    expect(all.rows.map((r) => r.depth)).toEqual([0, 1, 2, 0]);
    expect(visibleRows(tree, () => true, 2).hidden).toBe(2);
    expect(describeAlarms({ minor: 1, critical: 2 })).toBe("2 critical, 1 minor");
  });
});
