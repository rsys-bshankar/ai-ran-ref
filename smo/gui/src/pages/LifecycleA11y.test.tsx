// @vitest-environment jsdom
/**
 * axe-core on the onboarding and campaign tabs, their forms and the campaign drawer, in jsdom. The browser check of every page (scripts/gui_e2e.py) runs axe in Chromium
// against the compose stack; this catches the structural rules (labels, names, roles, tables) where the pages are written. Colour contrast needs a real renderer
// and is left to that check.
 * The backend is `fakeBff` (testing/bff.ts) answering as an admin, so every form and button is shown. Run: `cd gui && npx vitest run src/pages/LifecycleA11y.test.tsx`.
 */
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import rules from "../auth/permissions.fixture.json";
import { fakeBff, mountWith } from "../testing/bff";
import { byText, cleanup, click, settle } from "../testing/dom";
import { CampaignsTab } from "./Campaigns";
import { OnboardingTab } from "./Onboarding";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const CID = "4b6f1c2e-3333-4d4e-9f5a-cccccccccccc";

/** Installs the fake backend for an admin: one template, one element ready to apply, one watcher, one endpoint and one halted campaign with its report. */
function bff() {
  return fakeBff({
    "GET /me": { username: "ana", role: "admin", csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role: "admin", rules },
    "GET /smo/ran-nf-oam/onboarding-templates": { items: [{ name: "du-basic", description: null, entityType: "O-DU", vendorName: null, softwareBaseline: "2.1", requireBaseline: false, autoApply: false, enabled: true, changes: [{ attributeChanges: { a: 1 }, operation: "merge" }], createdAt: null, updatedAt: null }], limit: 200, offset: 0 },
    "GET /smo/ran-nf-oam/element-onboarding": { items: [{ managedElementRef: "ME-1", status: "TEMPLATE_SELECTED", templateName: "du-basic", softwareVersion: "2.1", softwareBaseline: "2.1", softwareCheck: "MATCH", configJobId: null, detail: null, createdAt: null, updatedAt: null }], limit: 200, offset: 0 },
    "GET /smo/ran-nf-oam/lifecycle-subscriptions": { items: [{ subscriptionId: "s-1", callbackUri: "https://noc.example/hook", events: [], createdAt: null }], limit: 200, offset: 0 },
    "GET /smo/ran-nf-oam/o1-adaptor-endpoints": { items: [{ endpointId: "e-1", managedElementRef: "ME-1", adaptorUri: "http://a", protocolSupport: ["NETCONF"], registeredVia: "x", healthStatus: "ACTIVE", lastHeartbeatAt: null }], limit: 200, offset: 0 },
    "GET /smo/ran-nf-oam/software-campaigns": { items: [{ campaignId: CID, status: "HALTED", wave: 1, waveCount: 2, haltedReason: "GATE_FAILED", name: "r3", softwareVersion: "3.0", createdAt: null }], limit: 200, offset: 0 },
    [`GET /smo/ran-nf-oam/software-campaigns/${CID}/report`]: { body: {
      campaignId: CID, status: "HALTED", wave: 1, waveCount: 2, haltedReason: "GATE_FAILED", name: "r3", requestedBy: "smo-gui:ana", softwareVersion: "3.0", selector: null, elements: ["ME-1", "ME-2"], waveSize: 1,
      wavePauseSeconds: 0, gateMaxNewAlarms: 0, onGateFailure: "halt", jobTimeoutSeconds: null, rollbackOrder: "all", haltedDetail: "1 failed", nextWaveAt: null, createdAt: null, finishedAt: null,
      events: [{ at: "2026-10-09T10:00:00Z", event: "STARTED", wave: 0, detail: null, by: null }], summary: { elements: 2, started: 1, notReached: 1, completed: 0, failed: 1, inProgress: 0, reverted: 0 },
      waves: [{ wave: 1, elements: ["ME-1"], started: true, jobs: [{ managedElementRef: "ME-1", jobId: "j", phase: "INSTALL", status: "FAILED", revert: null }] }, { wave: 2, elements: ["ME-2"], started: false, jobs: [] }],
      attention: [{ managedElementRef: "ME-1", problem: "software job failed in phase INSTALL" }],
    } },
  });
}

/** Runs axe on `root` (colour contrast off: jsdom cannot render it) and returns the moderate, serious and critical findings as "rule: help (targets)" lines; empty means clean. */
async function violations(root: Element): Promise<string[]> {
  const result = await axe.run(root, { rules: { "color-contrast": { enabled: false } }, runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "best-practice"] } });
  return result.violations.filter((v) => v.impact === "serious" || v.impact === "critical" || v.impact === "moderate")
    .map((v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join("; ")})`);
}

describe("accessibility of the onboarding and campaign pages", () => {
  // The onboarding tab, the new-template form and the apply form have no labelling, role or table violation, so a keyboard or screen-reader user can use them.
  it("has no structural violation on the onboarding tab, its template form and its apply form", async () => {
    bff();
    const { container } = await mountWith(<AuthProvider><OnboardingTab /></AuthProvider>);
    await settle();
    expect(await violations(container)).toEqual([]);
    await click(byText(container, "button", "New template…")!);
    await settle();
    expect(await violations(document.querySelector("[role=dialog]")!)).toEqual([]);
    await click(byText(document.body, "button", "Cancel")!);
    await click(byText(container, "button", "Apply")!);
    await settle();
    expect(await violations(document.querySelector("[role=dialog]")!)).toEqual([]);
  });

  // The campaign list, the start form and the drawer of a halted campaign have no labelling, role or table violation.
  it("has none on the campaign list, the start form and the campaign drawer", async () => {
    bff();
    const { container } = await mountWith(<AuthProvider><CampaignsTab /></AuthProvider>);
    await settle();
    expect(await violations(container)).toEqual([]);
    await click(byText(container, "button", "Start a campaign…")!);
    await settle();
    expect(await violations(document.querySelector("[role=dialog]")!)).toEqual([]);
    await click(byText(document.body, "button", "Cancel")!);
    await click(container.querySelector("tbody tr") as HTMLElement);
    await settle();
    expect(await violations(document.querySelector("[role=dialog]")!)).toEqual([]);
  });
});
