// @vitest-environment jsdom
/** Tests of the Account security page (pages/security): the three status tiles read `/api/me` and `/api/me/totp`, the recovery box draws the
 * server's slots with the used ones struck through (never the codes), the authenticator box is the shared enrolment component, and the recent
 * sign-ins list the user's own audit rows from `/api/me/sign-ins`.
 * Uses the fake BFF of `src/testing/bff.tsx`. Run: `npx vitest run src/pages/security` from smo/gui. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith } from "../../../testing/bff";
import { cleanup, settle } from "../../../testing/dom";
import { Security } from "../index";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; });

/** A BFF for one local user whose one-time code status is `totp`. */
function bff(totp: unknown, local = true) {
  return fakeBff({
    "GET /me": { username: "ana", role: "operator", csrfToken: "c", local, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role: "operator", rules },
    "GET /me/totp": totp,
    "GET /me/sign-ins": [{ at: "2026-10-09T10:00:00Z", action: "LOGIN", detail: "local" }, { at: "2026-10-09T09:00:00Z", action: "LOGIN_FAILED", detail: "bad password" }],
  });
}

/** The text of the tile labelled `label`. */
const tile = (root: HTMLElement, label: string) =>
  Array.from(root.querySelectorAll(".kpi")).find((k) => k.querySelector(".kpi-l")?.textContent === label)?.textContent ?? "";

describe("Account security", () => {
  // an enrolled local user sees the code on, the server's count of recovery codes, and "Local account"
  it("shows the status tiles from the session and the one-time code status", async () => {
    bff({ available: true, enrolled: true, pending: false, recoveryCodesLeft: 7 });
    const { container } = await mountWith(<AuthProvider><Security /></AuthProvider>);
    await settle();
    expect(tile(container, "Two-step sign-in")).toContain("On");
    expect(tile(container, "Recovery codes left")).toContain("7");
    expect(tile(container, "Sign-in method")).toContain("Local account");
    expect(container.querySelectorAll("[data-section='security.recovery'] .recovery-codes li")).toHaveLength(7);
    expect(container.querySelector("[data-section='security.authenticator']")?.textContent).toMatch(/A one-time code is set up/);
  });

  // the slots the server lists are drawn in order, a used one struck through with its time
  it("strikes through the used recovery codes", async () => {
    bff({ available: true, enrolled: true, pending: false, recoveryCodesLeft: 2, recoveryCodes: [
      { slot: 2, used: true, usedAt: "2026-10-08T07:00:00Z" }, { slot: 1, used: false, usedAt: null }, { slot: 3, used: false, usedAt: null }] });
    const { container } = await mountWith(<AuthProvider><Security /></AuthProvider>);
    await settle();
    const slots = Array.from(container.querySelectorAll("[data-section='security.recovery'] .recovery-codes li"));
    expect(slots).toHaveLength(3);
    expect(slots.map((l) => l.classList.contains("used"))).toEqual([false, true, false]);
    expect(slots[1].textContent).toContain("#2");
  });

  // the user's own sign-ins are listed with a warning for the failed one
  it("lists the recent sign-ins", async () => {
    const calls = bff({ available: true, enrolled: true, pending: false, recoveryCodesLeft: 7 });
    const { container } = await mountWith(<AuthProvider><Security /></AuthProvider>);
    await settle();
    const box = container.querySelector("[data-section='security.signins']")!;
    expect(box.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(box.textContent).toContain("failed sign-in");
    expect(box.textContent).toContain("1 failed sign-in among these");
    expect(calls.find((c) => c.path === "/me/sign-ins")!.query.get("limit")).toBe("20");
  });

  // a user without a code is told to set it up and gets no recovery slots
  it("says the code is off, with no recovery codes", async () => {
    bff({ available: true, enrolled: false, pending: false, recoveryCodesLeft: 0 });
    const { container } = await mountWith(<AuthProvider><Security /></AuthProvider>);
    await settle();
    expect(tile(container, "Two-step sign-in")).toContain("Off");
    expect(tile(container, "Recovery codes left")).toContain("—");
    expect(container.textContent).toMatch(/No recovery codes yet/);
  });

  // an identity-provider user signs in by single sign-on and has no recovery box
  it("shows single sign-on for an identity-provider user", async () => {
    bff({ available: false, enrolled: false, pending: false, recoveryCodesLeft: 0, reason: "identity provider" }, false);
    const { container } = await mountWith(<AuthProvider><Security /></AuthProvider>);
    await settle();
    expect(tile(container, "Sign-in method")).toContain("Single sign-on");
    expect(container.querySelector("[data-section='security.recovery']")).toBeNull();
  });
});
