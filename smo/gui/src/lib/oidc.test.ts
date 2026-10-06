import { describe, expect, it } from "vitest";

import { oidcErrorMessage, oidcLoginHref } from "./oidc";

describe("oidcErrorMessage", () => {
  it("says nothing without a code", () => {
    expect(oidcErrorMessage(null)).toBeNull();
    expect(oidcErrorMessage(undefined)).toBeNull();
    expect(oidcErrorMessage("")).toBeNull();
  });
  it("explains each reason the backend can give", () => {
    expect(oidcErrorMessage("no_role")).toMatch(/none of your groups/);
    expect(oidcErrorMessage("account_disabled")).toMatch(/disabled/);
    expect(oidcErrorMessage("invalid_state")).toMatch(/Start it again/);
  });
  it("never echoes an unknown code, whatever it holds", () => {
    expect(oidcErrorMessage("<img src=x onerror=alert(1)>")).toBe("The single sign-on attempt failed.");
    expect(oidcErrorMessage("__proto__")).toBe("The single sign-on attempt failed.");
    expect(oidcErrorMessage("constructor")).toBe("The single sign-on attempt failed.");
  });
});

describe("oidcLoginHref", () => {
  it("is the backend's login path when OIDC is on", () => {
    expect(oidcLoginHref({ localLogin: true, oidc: { enabled: true, providerName: "Keycloak", loginUrl: "/api/oidc/login" } })).toBe("/api/oidc/login");
  });
  it("is null when OIDC is off or not loaded", () => {
    expect(oidcLoginHref(undefined)).toBeNull();
    expect(oidcLoginHref({ localLogin: true, oidc: { enabled: false } })).toBeNull();
  });
  it("refuses a login URL that is not a path of the backend", () => {
    expect(oidcLoginHref({ localLogin: true, oidc: { enabled: true, loginUrl: "https://evil.example.com/" } })).toBeNull();
    expect(oidcLoginHref({ localLogin: true, oidc: { enabled: true, loginUrl: "//evil.example.com/api/x" } })).toBeNull();
  });
});
