// @vitest-environment jsdom
/** Tests of the Element detail page (pages/element, route /elements/:me) against a fake BFF: the header and overview from the element's
 * reads, the managed-object tree loading a node's children only when it is opened, two picked snapshots diffed, the cell guard editor shown
 * only to a role that may write guards (admin) and its PUT body, the admin's site-cluster editor in the header, "Refresh from element" for an
 * operator, and the DN helpers. Run: `npx vitest run src/pages/element`. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith, type Call } from "../../../testing/bff";
import { byText, cleanup, click, settle, type } from "../../../testing/dom";
import { functionRefOf, rootDn } from "../data/types";
import { ElementDetail } from "../index";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const ENTITY = {
  managedElementRef: "du-03", managedFunctionRef: null, entityType: "DU", vendorName: "vendor-a", o1Protocol: "NETCONF", o1AdaptorEndpointId: "ep-1",
  supportedServices: ["PROV", "FM"], conformanceMode: "SPEC", region: "eu-west", tenant: "acme", siteCluster: "metro-a",
  cellGuards: { "cell-8": { cellClass: "COVERAGE_CRITICAL", sectorGroup: "sg-12", incidentZone: "iz-1", neighbourRefs: ["cell-7", "cell-9"] } },
};
const ROOT = "ManagedElement=du-03";
const mo = (dn: string, parentDn: string | null) => ({ dn, parentDn, class: dn.split(",").pop()!.split("=")[0], id: dn.split("=").pop()!, managedElementRef: "du-03", source: "registry" });
const SNAPS = [
  { snapshotId: "s2", jobId: "j2", subChangeStatus: "APPLIED", managedElementRef: "du-03", managedFunctionRef: "GNBDUFunction=1", operation: "merge", before: { digitalTilt: 60 }, after: { digitalTilt: 70 }, beforeError: null, createdAt: "2026-10-09T02:37:00Z" },
  { snapshotId: "s1", jobId: "j1", subChangeStatus: "APPLIED", managedElementRef: "du-03", managedFunctionRef: "GNBDUFunction=1", operation: "merge", before: { digitalTilt: 50 }, after: { digitalTilt: 60 }, beforeError: null, createdAt: "2026-10-08T22:05:00Z" },
];

/** A fake BFF for the page (paths compared decoded). */
function bff(role: "viewer" | "operator" | "admin") {
  return fakeBff({
    "GET /me": { username: "ana", role, csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role, rules },
    "GET /smo/*": (c: Call) => {
      const p = decodeURIComponent(c.path).replace("/smo/ran-nf-oam", "");
      if (p === "/managed-entities/du-03") return ENTITY;
      if (p === "/alarms") return { items: [], total: c.query.get("severity") === "critical" ? 1 : 4, limit: 1, offset: 0 };
      if (p === "/managed-entities/du-03/config-history") return { items: c.query.get("limit") === "1" ? SNAPS.slice(0, 1) : SNAPS, total: 2, limit: 25, offset: 0 };
      if (p === "/managed-entities/du-03/config-history/diff") return { managedElementRef: "du-03", managedFunctionRef: "GNBDUFunction=1", fromSnapshot: "s1", toSnapshot: "s2", changed: [{ attribute: "digitalTilt", from: 60, to: 70 }], onlyInFrom: {}, onlyInTo: { cellIndividualOffset: 2 } };
      if (p === "/topology/links") return { items: [{ aElement: "du-03", aCell: "cell-8", bElement: "du-17", bCell: "cell-1", linkType: "INTER_ELEMENT", reciprocal: false, sameSectorGroup: false, sameIncidentZone: false }] };
      if (p === `/managed-objects/${ROOT}`) return mo(ROOT, null);
      if (p === `/managed-objects/${ROOT}/children`) return { items: [mo(`${ROOT},GNBDUFunction=1`, ROOT)], total: 1, limit: 50, offset: 0 };
      if (p === "/cell-guards") return { items: [{ managedElementRef: "du-03", cellId: "cell-8", ...ENTITY.cellGuards["cell-8"] }], total: 1, limit: 25, offset: 0 };
      return { status: 404, body: { title: "NOT_FOUND" } };
    },
    "PUT /smo/*": (c: Call) => c.body,
    "POST /smo/*": { status: 202, body: {} },
  });
}

/** Mounts the page for du-03 as `role` on tab `hash` and waits for its first reads; returns the mount and the recorded calls. */
const open = async (role: "viewer" | "operator" | "admin", hash = "") => {
  window.location.hash = hash;
  const calls = bff(role);
  const m = await mountWith(<AuthProvider><ElementDetail /></AuthProvider>, { at: "/elements/du-03", route: "/elements/:me" });
  await settle();
  return { ...m, calls };
};

describe("Element detail page", () => {
  // The header and overview read the element, its alarm counts, its history total and its relations.
  it("shows the element's header and overview", async () => {
    const { container } = await open("viewer");
    expect(container.querySelector("h1")!.textContent).toBe("du-03");
    expect(container.textContent).toContain("vendor-a · O1 NETCONF");
    const overview = container.querySelector("[data-section='element.overview']")!;
    expect(byText(overview, ".kpi", /Critical alarms1/)).not.toBeNull();
    expect(overview.textContent).toContain("1 guarded above NORMAL");
    expect(overview.textContent).toContain("1 not reciprocal");
  });

  // Children of a tree node are fetched only when it is opened, with a page size.
  it("loads a managed object's children on expand", async () => {
    const { container, calls } = await open("viewer", "#mo");
    const tree = container.querySelector("[data-section='element.mo']")!;
    expect(tree.textContent).toContain("ManagedElement=du-03");
    expect(calls.some((c) => c.path.endsWith("/children"))).toBe(false);
    await click(tree.querySelector("button[aria-label='Open ManagedElement=du-03']") as HTMLElement);
    await settle();
    const call = calls.find((c) => c.path.endsWith("/children"))!;
    expect(call.query.get("limit")).toBe("50");
    expect(tree.textContent).toContain("GNBDUFunction=1");
  });

  // Picking a from and a to snapshot asks for their diff and shows it as -/+ lines.
  it("diffs two picked snapshots", async () => {
    const { container, calls } = await open("viewer", "#history");
    const history = container.querySelector("[data-section='element.history']")!;
    const froms = Array.from(history.querySelectorAll("button")).filter((b) => b.textContent === "from");
    const tos = Array.from(history.querySelectorAll("button")).filter((b) => b.textContent === "to");
    await click(froms[1]);
    await click(tos[0]);
    await settle();
    const diff = calls.find((c) => c.path.endsWith("/config-history/diff"))!;
    expect([diff.query.get("from_snapshot"), diff.query.get("to_snapshot")]).toEqual(["s1", "s2"]);
    const box = container.querySelector("[data-section='element.diff']")!;
    expect(box.querySelector(".diff .m")!.textContent).toContain("digitalTilt: 60");
    expect(box.querySelector(".diff .p")!.textContent).toContain("digitalTilt: 70");
  });

  // Editing a guard is an admin's call: an operator sees no Edit; an admin edits and the PUT carries the guard.
  it("lets only an admin edit a cell guard", async () => {
    const op = await open("operator", "#guards");
    expect(op.container.textContent).toContain("COVERAGE_CRITICAL");
    expect(byText(op.container, "button", "Edit")).toBeNull();
    cleanup();
    const admin = await open("admin", "#guards");
    await click(byText(admin.container, "button", "Edit")!);
    await settle();
    const editor = admin.container.querySelector("[data-section='element.guard-editor']")!;
    await click(byText(editor, "button", "EMERGENCY")!);
    await click(byText(editor, "button", "Save guard")!);
    await settle();
    const put = admin.calls.find((c) => c.method === "PUT")!;
    expect(decodeURIComponent(put.path)).toBe("/smo/ran-nf-oam/managed-entities/du-03/cells/cell-8/guards");
    expect(put.body).toEqual({ cellClass: "EMERGENCY", sectorGroup: "sg-12", incidentZone: "iz-1", neighbourRefs: ["cell-7", "cell-9"] });
  });
});

describe("Element writes opened by GUI-9.7 / 9.8", () => {
  // The header shows the site cluster; only an admin edits it, and the PUT carries the new value (empty clears it).
  it("lets an admin set the site cluster", async () => {
    const op = await open("operator");
    const header = () => document.querySelector("[data-section='element.header']") as HTMLElement;
    expect(header().textContent).toContain("site cluster metro-a");
    expect(byText(header(), "button", "Edit cluster")).toBeNull();
    cleanup();
    const admin = await open("admin");
    await click(byText(header(), "button", "Edit cluster")!);
    const input = header().querySelector("input[aria-label='Site cluster']") as HTMLInputElement;
    await type(input, "metro-b");
    await click(byText(header(), "button", "Save")!);
    await settle();
    const put = admin.calls.find((c) => c.method === "PUT")!;
    expect(decodeURIComponent(put.path)).toBe("/smo/ran-nf-oam/managed-entities/du-03/site-cluster");
    expect(put.body).toEqual({ siteCluster: "metro-b" });
    expect(op).toBeDefined();
  });

  // An operator may walk the element's server again ("Refresh from element"); a viewer may not.
  it("offers Refresh from element to an operator", async () => {
    const viewer = await open("viewer", "#mo");
    expect(byText(viewer.container, "button", "Refresh from element")).toBeNull();
    cleanup();
    const op = await open("operator", "#mo");
    await click(byText(op.container, "button", "Refresh from element")!);
    await settle();
    expect(op.calls.some((c) => c.method === "POST" && decodeURIComponent(c.path) === "/smo/ran-nf-oam/managed-entities/du-03/managed-objects/refresh")).toBe(true);
  });
});

describe("DN helpers", () => {
  // A flat ref has a ManagedElement root; a function ref is the DN below it.
  it("derives the root DN and the function ref", () => {
    expect(rootDn("du-03")).toBe("ManagedElement=du-03");
    expect(rootDn("ManagedElement=x")).toBe("ManagedElement=x");
    expect(functionRefOf("du-03", "ManagedElement=du-03,GNBDUFunction=1")).toBe("GNBDUFunction=1");
    expect(functionRefOf("du-03", "ManagedElement=du-03")).toBeNull();
  });
});
