// @vitest-environment jsdom
/** axe-core on the Software page's campaign boxes (MGT-15.5 to 15.7): the list, the new-campaign form, the detail of a halted campaign with its
 * wave elements, and the failure-notice watchers, in jsdom. The browser check of every page (scripts/gui_e2e.py) runs axe in Chromium against
 * the compose stack; this catches the structural rules (labels, names, roles, tables) where the boxes are written. Colour contrast needs a real
 * renderer and is left to that check. The backend is `fakeBff` answering as an admin. Run: `cd gui && npx vitest run src/pages/software`. */
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith } from "../../../testing/bff";
import { cleanup, settle } from "../../../testing/dom";
import { Software } from "../index";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const CID = "4b6f1c2e-3333-4d4e-9f5a-cccccccccccc";
/** A campaign halted by a failed gate after its first of two waves. */
const CAMPAIGN = {
  campaignId: CID, status: "HALTED", wave: 1, waveCount: 2, haltedReason: "GATE_FAILED", name: "r3", requestedBy: "smo-gui:ana", softwareVersion: "3.0", selector: null, elements: ["ME-1", "ME-2"], waveSize: 1,
  wavePauseSeconds: 0, gateMaxNewAlarms: 0, onGateFailure: "halt", jobTimeoutSeconds: null, rollbackOrder: "all", haltedDetail: "1 failed", nextWaveAt: null, createdAt: null, finishedAt: null,
  events: [{ at: "2026-10-09T10:00:00Z", event: "STARTED", wave: 0, detail: null, by: null }],
};

/** Installs the fake backend for an admin: the halted campaign with its report, one watcher. */
function bff() {
  return fakeBff({
    "GET /me": { username: "ana", role: "admin", csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role: "admin", rules },
    "GET /summary/software": { page: "software", computedAt: "", partial: [], counts: { "campaigns.HALTED": 1, "campaigns.total": 1 } },
    "GET /smo/ran-nf-oam/software-campaigns": { items: [CAMPAIGN], total: 1, limit: 25, offset: 0 },
    [`GET /smo/ran-nf-oam/software-campaigns/${CID}`]: { body: CAMPAIGN },
    [`GET /smo/ran-nf-oam/software-campaigns/${CID}/report`]: { body: {
      ...CAMPAIGN, summary: { elements: 2, started: 1, notReached: 1, completed: 0, failed: 1, inProgress: 0, reverted: 0 },
      waves: [{ wave: 1, elements: ["ME-1"], started: true, jobs: [{ managedElementRef: "ME-1", jobId: "j", phase: "INSTALL", status: "FAILED", revert: null, timedOut: true }] }, { wave: 2, elements: ["ME-2"], started: false, jobs: [] }],
      attention: [{ managedElementRef: "ME-1", problem: "software job failed in phase INSTALL" }],
    } },
    "GET /smo/ran-nf-oam/lifecycle-subscriptions": { items: [{ subscriptionId: "s-1", callbackUri: "https://noc.example/hook", events: [], createdAt: null }], limit: 200, offset: 0 },
    "GET /smo/ran-nf-oam/vendor-capabilities": { items: [], total: 0, limit: 100, offset: 0 },
  });
}

/** Runs axe on `root` (colour contrast off: jsdom cannot render it) and returns the moderate, serious and critical findings as "rule: help (targets)" lines; empty means clean. */
async function violations(root: Element): Promise<string[]> {
  const result = await axe.run(root, { rules: { "color-contrast": { enabled: false } }, runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "best-practice"] } });
  return result.violations.filter((v) => v.impact === "serious" || v.impact === "critical" || v.impact === "moderate")
    .map((v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join("; ")})`);
}

describe("accessibility of the software campaigns", () => {
  // The campaign list, the detail of a halted campaign, its wave elements and the watchers have no labelling, role or table violation.
  it("has none on the list, the campaign detail, its elements and the watchers", async () => {
    bff();
    const { container } = await mountWith(<AuthProvider><Software /></AuthProvider>, { at: `/software?campaign=${CID}` });
    await settle(8);
    for (const id of ["software.list", "software.detail", "software.elements", "software.watchers"]) {
      expect(await violations(container.querySelector(`[data-section='${id}']`)!)).toEqual([]);
    }
  });

  // The new-campaign form, with the job timeout and the rollback order, has no labelling violation.
  it("has none on the new-campaign form", async () => {
    window.location.hash = "#new";
    bff();
    const { container } = await mountWith(<AuthProvider><Software /></AuthProvider>, { at: "/software" });
    await settle();
    expect(await violations(container.querySelector("[data-section='software.new']")!)).toEqual([]);
  });
});
