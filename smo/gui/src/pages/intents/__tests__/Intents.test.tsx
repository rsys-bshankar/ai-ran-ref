// @vitest-environment jsdom
/** Tests of the Intents page (pages/intents, route /policy): tiles from the summary (with the unserved counts as "—"), the server table and the
 * selected intent's card (fulfilment ring from targets met, conflict callout), negotiation feedback (read-only under today's BFF table, sent with
 * the chosen outcome where a table allows it, feature 10), the cards view, the new-intent form's handler-support check, the Utility formulas tab,
 * and the old tab ids. `fetch` is stubbed by `testing/bff.tsx` `fakeBff` with `auth/permissions.fixture.json`; no BFF runs.
 * Run: `npx vitest run src/pages/intents` from smo/gui. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import fixture from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith } from "../../../testing/bff";
import { byText, cleanup, click, settle } from "../../../testing/dom";
import { Policy } from "../index";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const I1 = "11111111-aaaa-4aaa-8aaa-111111111111";
const I2 = "22222222-bbbb-4bbb-8bbb-222222222222";
const page = <T,>(items: T[]) => ({ items, total: items.length, limit: 50, offset: 0 });
const ok = { fulfilmentStatus: "FULFILLED" };
const intent = (id: string, label: string) => ({
  intentId: id, intentAdminState: "ACTIVATED", intentPriority: 1, rmioId: "smo-gui", intentMgmtPurpose: "FULFILMENT_WITH_NEGOTIATION", rmihId: "so-smos", userLabel: label,
  attributes: { intentExpectations: [{ expectationObject: { objectType: "RAN_SUBNETWORK" }, expectationTargets: [{ targetName: "RANEnergyConsumption", targetCondition: "IS_LESS_THAN", targetValueRange: 500 }] }] },
});
const FEEDBACK_RULE = { method: "POST", pattern: "^/intent-service/intents/[^/]+/negotiation-feedback$", role: "operator", queryMatch: {} };

/** The fake BFF; `extraRules` go first in the permission table (a table that opens a route today's BFF keeps closed). */
function bff(role: "viewer" | "operator" | "admin", extraRules: unknown[] = []) {
  return fakeBff({
    "GET /me": { username: "ana", role, csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role, rules: [...extraRules, ...fixture] },
    "GET /summary/intents": { page: "intents", computedAt: "now", counts: { "intents.ACTIVATED": 574, "intents.DEACTIVATED": 38, "intents.total": 612 }, partial: [] },
    "GET /smo/intent-service/intents": page([intent(I1, "Save energy at night"), intent(I2, "Keep handover success")]),
    "GET /smo/intent-service/intent-reports": page([
      { reportId: "r3", intentId: I1, attributes: { lastUpdatedTime: "2026-10-09T11:03:00Z", intentFulfilmentNegotiationReport: { possibleIntentOutcomeList: [{ possibleIntentOutcomeId: 17, intentFulfilmentInfo: ok }] } } },
      { reportId: "r2", intentId: I1, attributes: { lastUpdatedTime: "2026-10-09T11:02:00Z", intentConflictReports: [{ conflictId: "c1", conflictType: "INTENT_CONFLICT", conflictingIntent: I2 }] } },
      { reportId: "r1", intentId: I1, attributes: { lastUpdatedTime: "2026-10-09T11:01:00Z", intentFulfilmentReport: { intentFulfilmentInfo: { fulfilmentStatus: "NOT_FULFILLED", notFullfilledState: "DEGRADED" }, expectationFulfilmentResult: [{ expectaitonId: "e1", expectationFulfilmentInfo: ok, targetFulfilmentResults: [
        { targetName: "a", targetFulfilmentInfo: ok }, { targetName: "b", targetFulfilmentInfo: { fulfilmentStatus: "NOT_FULFILLED" } }, { targetName: "c", targetFulfilmentInfo: ok }, { targetName: "d", targetFulfilmentInfo: ok }] }] } } },
    ]),
    "GET /smo/intent-service/intent-handling-functions": page([{ rmihId: "so-smos", smeServiceId: "s", notificationDestination: "http://so-smos", intentHandlingScope: ["RAN"], attributes: { supportedNegotiationFunctionalities: null, intentHandlingCapabilityList: [
      { intentHandlingCapabilityId: "c", supportedExpectationObjectType: "RAN_SUBNETWORK", supportedExpectationTargetInfoList: [{ supportedTargetName: "RANEnergyConsumption" }] }] } }]),
    "GET /smo/intent-service/intent-utility-formulas": page([{ id: "f-1", attributes: { utilityFunctionId: "linear", utilityParameterList: [{ parameterName: "dlThroughputP10", parameterWeight: 0.8 }], utilityScale: 1, utilityOffset: 0 } }]),
    [`POST /smo/intent-service/intents/${I1}/negotiation-feedback`]: { reportId: "r3" },
  });
}

const open = async () => { const m = await mountWith(<AuthProvider><Policy /></AuthProvider>); await settle(8); return m; };
const selectFirst = async (root: HTMLElement) => { await click(root.querySelector('[data-section="intents.table"] tbody tr') as HTMLElement); await settle(8); };

describe("the Intents tab", () => {
  // Tiles read the summary's true counts; the two counts no route serves say "—" instead of a number counted from one page.
  it("shows the summary tiles", async () => {
    bff("viewer");
    const { container } = await open();
    const tiles = container.querySelector('[data-section="intents.tiles"]') as HTMLElement;
    expect(tiles.textContent).toContain("574");
    expect(tiles.textContent).toContain("/ 612");
    expect(byText(tiles, ".kpi", /Not fulfilled/)!.querySelector(".kpi-v")!.textContent).toBe("—");
  });

  // The selected intent's card derives fulfilment from targets met (3 of 4), names its handler, and shows the reported conflict.
  it("shows the selected intent's card", async () => {
    bff("viewer");
    const { container } = await open();
    await selectFirst(container);
    const card = container.querySelector('[data-section="intents.card"]') as HTMLElement;
    expect(card.querySelector(".ring")!.getAttribute("aria-label")).toBe("Targets fulfilled: 75 %");
    expect(card.textContent).toContain("3/4 targets met");
    expect(card.textContent).toContain("Handler so-smos");
    expect(card.querySelector(".callout")!.textContent).toContain("intent conflict");
  });

  // Today's BFF table does not open negotiation feedback: the card lists the outcomes read-only and offers no button.
  it("keeps negotiation feedback read-only where the BFF does not allow it", async () => {
    bff("operator");
    const { container } = await open();
    await selectFirst(container);
    const box = container.querySelector('[data-part="negotiation"]') as HTMLElement;
    expect(box.textContent).toContain("17 (FULFILLED)");
    expect(byText(box, "button", "Acceptable")).toBeNull();
  });

  // Where a permission table allows it, a satisfaction answer is sent for the chosen outcome (feature 10).
  it("sends negotiation feedback where allowed", async () => {
    const calls = bff("operator", [FEEDBACK_RULE]);
    const { container } = await open();
    await selectFirst(container);
    await click(byText(container.querySelector('[data-part="negotiation"]') as HTMLElement, "button", "Acceptable")!);
    await settle();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe(`/smo/intent-service/intents/${I1}/negotiation-feedback`);
    expect(post.body).toEqual({ referredIntentOutcomeId: 17, consumerSatisfactionIndex: 70 });
  });

  // The cards view draws one card per intent of the page.
  it("switches to cards", async () => {
    bff("viewer");
    const { container } = await open();
    await click(byText(container, "[role=radio]", "Cards")!);
    await settle(8);
    expect(container.querySelectorAll('[data-section="intents.card"]').length).toBe(2);
  });

  // The new-intent form says whether the chosen handler supports the expectation before anything is sent.
  it("checks handler support in the new-intent form", async () => {
    bff("operator");
    const { container } = await open();
    const form = container.querySelector('[data-section="intents.new"]') as HTMLElement;
    const select = form.querySelector("select") as HTMLSelectElement;
    select.value = "so-smos";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await settle();
    expect(form.textContent).toContain("Handler supports target");
  });
});

describe("other tabs", () => {
  // Feature 10: the formulas tab lists each formula's function, weighted parameters, scale and offset.
  it("lists the utility formulas", async () => {
    window.location.hash = "#formulas";
    bff("viewer");
    const { container } = await open();
    const box = container.querySelector('[data-section="intents.formulas"]') as HTMLElement;
    expect(box.textContent).toContain("linear");
    expect(box.textContent).toContain("dlThroughputP10 × 0.8");
  });

  // The pre-redesign id #autonomy still opens the dispatches tab.
  it("keeps the old tab ids", async () => {
    window.location.hash = "#autonomy";
    fakeBff({ "GET /me": { username: "ana", role: "viewer", csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false }, "GET /permissions": { role: "viewer", rules: fixture },
      "GET /smo/intent-service/autonomy-dispatches": page([]) });
    const { container } = await open();
    expect(container.querySelector('[data-section="intents.dispatches"]')!.textContent).toContain("No autonomy dispatches.");
  });
});
