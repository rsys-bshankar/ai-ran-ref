// @vitest-environment jsdom
/** Tests of the KPIs & Assurance page (pages/kpis): Overview is the default tab, its tiles show the server-computed KPIs (and "—" with the
 * reason for one that is not defined), the worst-10 list is ranked lowest first, the escalations callout carries the true count, the range
 * reaches the KPI calls, the MDA request form is read-only for a viewer and sends a TS 28.104 body for an operator (feature 9), a request can be cancelled,
 * MDA reports filter by kind and offer the file, and the pre-redesign tab ids (MLMF, Definitions) still work. `fetch` is stubbed by
 * `testing/bff.tsx` `fakeBff` with `auth/permissions.fixture.json`; no BFF runs. Run: `npx vitest run src/pages/kpis` from smo/gui. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import fixture from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith, type Call } from "../../../testing/bff";
import { byText, cleanup, click, settle, type } from "../../../testing/dom";
import { Kpis } from "../index";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const page = <T,>(items: T[], total = items.length) => ({ items, total, limit: 50, offset: 0 });
const kpi = (name: string, unit: string, items: unknown[]) => ({ kpi: name, unit, from: "f", to: "t", groupBy: "all", filesScanned: 3, truncated: false, items });

/** The fake BFF; `extraRules` go first in the permission table. */
function bff(role: "viewer" | "operator" | "admin", extraRules: unknown[] = []) {
  return fakeBff({
    "GET /me": { username: "ana", role, csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role, rules: [...extraRules, ...fixture] },
    "GET /smo/ran-nf-oam/kpis/dl_ue_throughput": (c: Call) => c.query.get("group_by") === "element"
      ? kpi("dl_ue_throughput", "Mbit/s", [{ group: { managedElementRef: "du-01" }, value: 180, samples: 4, reason: null }, { group: { managedElementRef: "du-03" }, value: 0.4, samples: 4, reason: null },
        { group: { managedElementRef: "du-02" }, value: 12, samples: 4, reason: null }, { group: { managedElementRef: "du-09" }, value: null, samples: 0, reason: "NO_DATA" }])
      : kpi("dl_ue_throughput", "Mbit/s", [{ group: {}, value: 182.34, samples: 1200, reason: null }]),
    "GET /smo/ran-nf-oam/kpis/dl_prb_utilization": kpi("dl_prb_utilization", "percent", [{ group: {}, value: 57, samples: 1200, reason: null }]),
    "GET /smo/ran-nf-oam/kpis/handover_success_rate": kpi("handover_success_rate", "percent", [{ group: {}, value: null, samples: 0, reason: "NO_DATA" }]),
    "GET /smo/ran-nf-oam/kpis/handover_failure_rate": kpi("handover_failure_rate", "percent", [{ group: {}, value: 0.9, samples: 30, reason: null }]),
    "GET /smo/ran-nf-oam/kpis/rrc_connected_ues_mean": { status: 404, body: { title: "KPI_NOT_FOUND" } },
    "GET /smo/sa-smos/monitors": page([{ monitorId: "m-1", targetOrderId: null, targetCoordinationGroupId: null, targetRappInstanceId: null, analyticsSubscriptionId: null, thresholds: { rrcSetupSuccess: 98 } }]),
    "GET /smo/sa-smos/remedial-actions": (c: Call) => c.query.get("outcome") === "ESCALATED"
      ? page([{ actionId: "a-1", monitorId: "m-1", actionType: "CONFIG_CHANGE", autoExecuted: true, outcome: "ESCALATED" }], 4) : page([]),
    "GET /smo/mdaf/mda-functions": page([{ id: "fn-1", attributes: { userLabel: "mdaf-coverage-1", supportedMDACapabilities: ["COVERAGE_ANALYTICS_COVERAGE_PROBLEM_ANALYSIS"], supportedMDADomain: "RAN", mLModelRefList: [], aIMLInferenceFunctionRefList: [] } }]),
    "GET /smo/mdaf/mda-requests": page([{ id: "req-9", attributes: { requestedMDAOutputs: [{ mDAType: "COVERAGE" }], analyticsScope: null, reportingMethod: "FILE", requestedBy: "smo-gui", active: true } }]),
    "DELETE /smo/mdaf/mda-requests/req-9": { status: 204 },
    "GET /smo/mdaf/mda-reports": (c: Call) => page([{ id: "rep-1", attributes: { mDAOutputs: [], mDARequestRef: null, mDAFunctionRef: "fn-1", reportKind: "DRIFT", scope: null, deliveredToRequestRefList: [], generatedAt: "2026-10-09T10:00:00Z" } }]
      .filter((r) => !c.query.get("report_kind") || r.attributes.reportKind === c.query.get("report_kind"))),
    "GET /smo/mdaf/reports": page([]), "GET /smo/mdaf/subscriptions": page([]), "GET /smo/ran-analytics/producers": page([]),
    "POST /smo/mdaf/mda-requests": { status: 201, body: { id: "req-1" } },
    "GET /smo/aimgf/mlmf/subscriptions": page([]), "GET /smo/mlmr/models": page([]),
    "GET /smo/ran-nf-oam/kpi-definitions": page([{ name: "dl_ue_throughput", formula: "thp", unit: "Mbit/s", description: null, counters: [{ counter: "DRB.UEThpDl", variable: "thp", aggregation: "avg" }] }]),
    "GET /smo/ran-nf-oam/kpi-schedules": page([]),
  });
}

const open = async () => { const m = await mountWith(<AuthProvider><Kpis /></AuthProvider>); await settle(8); return m; };
const tile = (root: HTMLElement, label: string) => byText(root, ".kpi", new RegExp(label)) as HTMLElement;

describe("the Overview tab", () => {
  // Overview is the default; each tile is the server's KPI value, a KPI without data or definition shows "—" with the reason.
  it("shows the KPI tiles from the server's computation", async () => {
    bff("viewer");
    const { container } = await open();
    expect(byText(container, "[role=tab][aria-selected=true]", "Overview")).not.toBeNull();
    expect(tile(container, "DL UE throughput").querySelector(".kpi-v")!.textContent).toBe("182.3 Mbit/s");
    expect(tile(container, "DL PRB utilisation").querySelector(".kpi-v")!.textContent).toBe("57 %");
    expect(tile(container, "Handover success").textContent).toContain("no data in window");
    expect(tile(container, "RRC-connected UEs").querySelector(".kpi-v")!.textContent).toBe("—");
    expect(tile(container, "RRC-connected UEs").textContent).toContain("not defined");
  });

  // The worst list ranks the lowest values first and leaves out a group with no value.
  it("ranks the lowest-throughput elements", async () => {
    bff("viewer");
    const { container } = await open();
    const rows = [...container.querySelectorAll('[data-section="kpis.worst"] li')].map((li) => li.querySelector(".mono")!.textContent);
    expect(rows).toEqual(["du-03", "du-02", "du-01"]);
  });

  // The escalations callout shows the newest escalated actions and the true count from the envelope.
  it("shows what is escalated, with the true count", async () => {
    bff("viewer");
    const { container } = await open();
    const box = container.querySelector('[data-section="kpis.escalations"]') as HTMLElement;
    expect(box.textContent).toContain("CONFIG_CHANGE could not recover it");
    expect(byText(box, "button", "4 →")).not.toBeNull();
  });

  // Changing the range recomputes the KPIs over a longer window (from_time moves back).
  it("passes the range to the KPI calls", async () => {
    const calls = bff("viewer");
    const { container } = await open();
    const before = calls.filter((c) => c.path === "/smo/ran-nf-oam/kpis/dl_prb_utilization").length;
    await click(byText(container, "[role=radio]", "7 d")!);
    await settle(6);
    const all = calls.filter((c) => c.path === "/smo/ran-nf-oam/kpis/dl_prb_utilization");
    expect(all.length).toBeGreaterThan(before);
    const hours = (Date.now() - Date.parse(all[all.length - 1].query.get("from_time")!)) / 3_600_000;
    expect(Math.round(hours)).toBe(168);
  });
});

describe("RAN Analytics (feature 9)", () => {
  // An operator cancels an analysis request with DELETE after a confirm.
  it("cancels an analysis request", async () => {
    window.location.hash = "#analytics";
    vi.stubGlobal("confirm", () => true);
    const calls = bff("operator");
    const { container } = await open();
    const box = container.querySelector('[data-section="kpis.mdaRequests"]') as HTMLElement;
    await click(byText(box, "button", "Cancel")!);
    await settle();
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/smo/mdaf/mda-requests/req-9")).toBe(true);
  });

  // The BFF opens POST /mdaf/mda-requests to operators: a viewer gets a read-only note and nothing can be sent.
  it("keeps the request form read-only for a viewer", async () => {
    window.location.hash = "#analytics";
    bff("viewer");
    const { container } = await open();
    const box = container.querySelector('[data-section="kpis.mdaRequest"]') as HTMLElement;
    expect(box.textContent).toContain("Read-only here");
    expect(byText(box, "button", "Request analysis")).toBeNull();
  });

  // An operator's request carries the function, the MDA type it supports, the entity scope and the delivery method.
  it("requests an analysis as an operator", async () => {
    window.location.hash = "#analytics";
    const calls = bff("operator");
    const { container } = await open();
    const box = container.querySelector('[data-section="kpis.mdaRequest"]') as HTMLElement;
    const [fn] = box.querySelectorAll("select") as NodeListOf<HTMLSelectElement>;
    fn.value = "fn-1";
    fn.dispatchEvent(new Event("change", { bubbles: true }));
    await settle();
    const [, output, method] = box.querySelectorAll("select") as NodeListOf<HTMLSelectElement>;
    output.value = "COVERAGE_ANALYTICS_COVERAGE_PROBLEM_ANALYSIS";
    output.dispatchEvent(new Event("change", { bubbles: true }));
    method.value = "STREAMING";
    method.dispatchEvent(new Event("change", { bubbles: true }));
    await type(box.querySelector("input.mono") as HTMLInputElement, "du-03, du-04");
    await click(byText(box, "button", "Request analysis")!);
    await settle();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toEqual({ mDAFunctionRef: "fn-1", requestedMDAOutputs: [{ mDAType: "COVERAGE_ANALYTICS_COVERAGE_PROBLEM_ANALYSIS" }], reportingMethod: "STREAMING",
      reportingTarget: null, analyticsScope: { managedEntitiesScope: ["du-03", "du-04"] }, requestedBy: "smo-gui" });
  });

  // Reports filter by kind on the server and each offers its file through the BFF.
  it("lists MDA reports with a kind filter and the file download", async () => {
    window.location.hash = "#analytics";
    const calls = bff("viewer");
    const { container } = await open();
    const box = container.querySelector('[data-section="kpis.mdaReports"]') as HTMLElement;
    expect(box.querySelector("a")!.getAttribute("href")).toBe("/api/smo/mdaf/mda-reports/rep-1/file");
    const kind = box.querySelector("select") as HTMLSelectElement;
    kind.value = "PREDICTION";
    kind.dispatchEvent(new Event("change", { bubbles: true }));
    await settle(6);
    expect(calls.some((c) => c.path === "/smo/mdaf/mda-reports" && c.query.get("report_kind") === "PREDICTION")).toBe(true);
    expect(box.textContent).toContain("No MDA report published.");
  });
});

describe("kept tabs", () => {
  // The pre-redesign ids still open their tab: #mlmf the model KPIs (from the AI/ML folder), #definitions the KPI catalogue.
  it("opens #mlmf and #definitions", async () => {
    window.location.hash = "#mlmf";
    bff("viewer");
    const first = await open();
    expect(first.container.querySelector('[data-section="aiml.mlmf"]')).not.toBeNull();
    cleanup();
    window.location.hash = "#definitions";
    bff("viewer");
    const second = await open();
    expect(second.container.querySelector('[data-section="kpis.definitions"]')!.textContent).toContain("DRB.UEThpDl");
  });
});
