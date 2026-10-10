// @vitest-environment jsdom
/** axe-core on Configuration → Element onboarding (MGT-14.6/14.7): the templates, element and watcher boxes, the new-template form and the apply
 * form, in jsdom. The browser check of every page (scripts/gui_e2e.py) runs axe in Chromium against the compose stack; this catches the structural
 * rules (labels, names, roles, tables) where the boxes are written. Colour contrast needs a real renderer and is left to that check. The backend
 * is `fakeBff` answering as an admin, so every form and button is shown. Run: `cd gui && npx vitest run src/pages/configuration`. */
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith } from "../../../testing/bff";
import { byText, cleanup, click, settle } from "../../../testing/dom";
import { Configuration } from "../index";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = "#onboarding"; });

/** Installs the fake backend for an admin: one template, one element ready to apply, one watcher and one endpoint. */
function bff() {
  return fakeBff({
    "GET /me": { username: "ana", role: "admin", csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role: "admin", rules },
    "GET /summary/configuration": { page: "configuration", computedAt: "", partial: [], counts: {} },
    "GET /smo/ran-nf-oam/onboarding-templates": { items: [{ name: "du-basic", description: null, entityType: "O-DU", vendorName: null, softwareBaseline: "2.1", requireBaseline: false, autoApply: false, enabled: true, changes: [{ attributeChanges: { a: 1 }, operation: "merge" }], createdAt: null, updatedAt: null }], limit: 200, offset: 0 },
    "GET /smo/ran-nf-oam/element-onboarding": { items: [{ managedElementRef: "ME-1", status: "TEMPLATE_SELECTED", templateName: "du-basic", softwareVersion: "2.1", softwareBaseline: "2.1", softwareCheck: "MATCH", configJobId: null, detail: null, createdAt: null, updatedAt: null }], total: 1, limit: 25, offset: 0 },
    "GET /smo/ran-nf-oam/lifecycle-subscriptions": { items: [{ subscriptionId: "s-1", callbackUri: "https://noc.example/hook", events: [], createdAt: null }], limit: 200, offset: 0 },
    "GET /smo/ran-nf-oam/o1-adaptor-endpoints": { items: [{ endpointId: "e-1", managedElementRef: "ME-1", adaptorUri: "http://a", protocolSupport: ["NETCONF"], registeredVia: "x", healthStatus: "ACTIVE", lastHeartbeatAt: null }], limit: 200, offset: 0 },
  });
}

/** Runs axe on `root` (colour contrast off: jsdom cannot render it) and returns the moderate, serious and critical findings as "rule: help (targets)" lines; empty means clean. */
async function violations(root: Element): Promise<string[]> {
  const result = await axe.run(root, { rules: { "color-contrast": { enabled: false } }, runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "best-practice"] } });
  return result.violations.filter((v) => v.impact === "serious" || v.impact === "critical" || v.impact === "moderate")
    .map((v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join("; ")})`);
}

describe("accessibility of element onboarding", () => {
  // The onboarding boxes, the new-template form and the apply form have no labelling, role or table violation, so a keyboard or screen-reader user can use them.
  it("has no structural violation on the onboarding boxes, the template form and the apply form", async () => {
    bff();
    const { container } = await mountWith(<AuthProvider><Configuration /></AuthProvider>, { at: "/configuration" });
    await settle();
    for (const id of ["configuration.templates", "configuration.onboarding", "configuration.watchers"]) {
      expect(await violations(container.querySelector(`[data-section='${id}']`)!)).toEqual([]);
    }
    await click(byText(container, "button", "New template…")!);
    await settle();
    expect(await violations(document.querySelector("[role=dialog]")!)).toEqual([]);
    await click(byText(document.body, "button", "Cancel")!);
    await click(byText(container, "button", "Apply")!);
    await settle();
    expect(await violations(document.querySelector("[role=dialog]")!)).toEqual([]);
  });
});
