/**
 * Unit tests of the single-sign-on helpers in `oidc.ts`: the wording for the backend's reason codes and the rule for the sign-in link. No DOM.
 * Run: `cd gui && npx vitest run src/lib/oidc.test.ts`.
 */

import { describe, expect, it } from "vitest";

import { oidcErrorMessage, oidcLoginHref } from "./oidc";

describe("oidcErrorMessage", () => {
  // No reason code means no message.
  it("says nothing without a code", () => {
    expect(oidcErrorMessage(null)).toBeNull();
    expect(oidcErrorMessage(undefined)).toBeNull();
    expect(oidcErrorMessage("")).toBeNull();
  });
  // Each reason code the backend can give has its own explanation.
  it("explains each reason the backend can give", () => {
    expect(oidcErrorMessage("no_role")).toMatch(/none of your groups/);
    expect(oidcErrorMessage("account_disabled")).toMatch(/disabled/);
    expect(oidcErrorMessage("invalid_state")).toMatch(/Start it again/);
  });
  // An unknown reason code (markup, `__proto__`, `constructor`) must not reach the page: a generic line is shown instead.
  it("never echoes an unknown code, whatever it holds", () => {
    expect(oidcErrorMessage("<img src=x onerror=alert(1)>")).toBe("The single sign-on attempt failed.");
    expect(oidcErrorMessage("__proto__")).toBe("The single sign-on attempt failed.");
    expect(oidcErrorMessage("constructor")).toBe("The single sign-on attempt failed.");
  });
});

describe("oidcLoginHref", () => {
  // With OIDC on, the button points at the login path the backend named.
  it("is the backend's login path when OIDC is on", () => {
    expect(oidcLoginHref({ localLogin: true, oidc: { enabled: true, providerName: "Keycloak", loginUrl: "/api/oidc/login" } })).toBe("/api/oidc/login");
  });
  // With OIDC off or the configuration not yet loaded there is no link.
  it("is null when OIDC is off or not loaded", () => {
    expect(oidcLoginHref(undefined)).toBeNull();
    expect(oidcLoginHref({ localLogin: true, oidc: { enabled: false } })).toBeNull();
  });
  // A login URL that is not a path under /api/ (another host, or a protocol-relative URL) is refused, so the button can never send the browser to another site.
  it("refuses a login URL that is not a path of the backend", () => {
    expect(oidcLoginHref({ localLogin: true, oidc: { enabled: true, loginUrl: "https://evil.example.com/" } })).toBeNull();
    expect(oidcLoginHref({ localLogin: true, oidc: { enabled: true, loginUrl: "//evil.example.com/api/x" } })).toBeNull();
  });
});
