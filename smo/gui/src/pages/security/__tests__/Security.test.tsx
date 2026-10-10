// @vitest-environment jsdom
/** Tests of the Account security page (pages/security): the three status tiles read `/api/me` and `/api/me/totp`, the recovery box shows the count
 * the server gives (never invented codes), the authenticator box is the shared enrolment component, and the sign-in history is a marked gap.
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

  // a user without a code is told to set it up and gets no recovery slots, and the history box says it is not available rather than inventing rows
  it("says the code is off, with no recovery codes, and marks sign-in history as a gap", async () => {
    bff({ available: true, enrolled: false, pending: false, recoveryCodesLeft: 0 });
    const { container } = await mountWith(<AuthProvider><Security /></AuthProvider>);
    await settle();
    expect(tile(container, "Two-step sign-in")).toContain("Off");
    expect(tile(container, "Recovery codes left")).toContain("—");
    expect(container.textContent).toMatch(/No recovery codes yet/);
    expect(container.querySelector("[data-section='security.signins'] .gap-note")).not.toBeNull();
    expect(container.querySelector("[data-section='security.signins'] table")).toBeNull();
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
