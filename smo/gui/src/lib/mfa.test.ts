import { describe, expect, it } from "vitest";

import { cleanCode, groupSecret, isChallenge, isPlausibleCode, isRecoveryCode, isTotpCode, loginErrorMessage, mfaLabel, mustEnrol, recoveryCodesText, recoveryLeftText } from "./mfa";

describe("isChallenge", () => {
  it("tells the second-step answer from a session", () => {
    expect(isChallenge({ mfaRequired: true, challenge: "a.b.c", expiresIn: 300 })).toBe(true);
    expect(isChallenge({ username: "ana", role: "admin", csrfToken: "x" })).toBe(false);
    expect(isChallenge({ mfaRequired: true })).toBe(false);
    expect(isChallenge(null)).toBe(false);
    expect(isChallenge(undefined)).toBe(false);
  });
});

describe("codes as typed", () => {
  it("ignores the spaces and dashes people add", () => {
    expect(cleanCode(" 123 456 ")).toBe("123456");
    expect(cleanCode("abcd-efgh-jkmn-pqrs")).toBe("abcdefghjkmnpqrs");
  });
  it("accepts six digits or a sixteen-character recovery code, nothing else", () => {
    expect(isTotpCode("123 456")).toBe(true);
    expect(isTotpCode("12345")).toBe(false);
    expect(isTotpCode("12345a")).toBe(false);
    expect(isRecoveryCode("abcd-efgh-jkmn-pqrs")).toBe(true);
    expect(isRecoveryCode("ABCD EFGH JKMN PQRS")).toBe(true);
    expect(isRecoveryCode("abcd-efgh-jkmn")).toBe(false);
    expect(isPlausibleCode("")).toBe(false);
    expect(isPlausibleCode("123456")).toBe(true);
    expect(isPlausibleCode("<script>")).toBe(false);
  });
});

describe("groupSecret", () => {
  it("shows the key in groups of four", () => {
    expect(groupSecret("ABCDEFGHIJKLMNOP")).toBe("ABCD EFGH IJKL MNOP");
    expect(groupSecret("ABCDEFGHIJ")).toBe("ABCD EFGH IJ");
    expect(groupSecret("")).toBe("");
  });
});

describe("recovery code text", () => {
  it("lists one code per line", () => {
    expect(recoveryCodesText(["a", "b"])).toBe("a\nb\n");
  });
  it("says how many are left, and warns at none", () => {
    expect(recoveryLeftText(9)).toBe("9 recovery codes left.");
    expect(recoveryLeftText(1)).toBe("1 recovery code left.");
    expect(recoveryLeftText(0)).toMatch(/no recovery codes left/);
  });
});

describe("loginErrorMessage", () => {
  it("has its own sentence for each reason the backend gives", () => {
    expect(loginErrorMessage({ status: 401, title: "INVALID_CODE" })).toMatch(/wrong, or has been used/);
    expect(loginErrorMessage({ status: 401, title: "CHALLENGE_INVALID" })).toMatch(/Enter your password again/);
    expect(loginErrorMessage({ status: 403, title: "LOGIN_MODE_OIDC_ONLY" })).toMatch(/single sign-on/);
    expect(loginErrorMessage({ status: 403, title: "BREAK_GLASS_NEEDS_TOTP" })).toMatch(/no one-time code/);
  });
  it("falls back on the status, then on the message, and never echoes an unknown title", () => {
    expect(loginErrorMessage({ status: 429 })).toMatch(/Too many failed attempts/);
    expect(loginErrorMessage({ status: 401 })).toBe("Invalid username or password.");
    expect(loginErrorMessage({ status: 500, message: "boom" })).toBe("boom");
    expect(loginErrorMessage({ status: 400, title: "<img src=x onerror=alert(1)>" })).toBe("Sign-in failed.");
    expect(loginErrorMessage({ title: "__proto__" })).toBe("Sign-in failed.");
  });
});

describe("mustEnrol", () => {
  it("sends an admin who must enrol to the security page and leaves them there", () => {
    expect(mustEnrol({ mfaEnrolmentRequired: true }, "/")).toBe(true);
    expect(mustEnrol({ mfaEnrolmentRequired: true }, "/admin")).toBe(true);
    expect(mustEnrol({ mfaEnrolmentRequired: true }, "/security")).toBe(false);
    expect(mustEnrol({ mfaEnrolmentRequired: false }, "/")).toBe(false);
    expect(mustEnrol({}, "/")).toBe(false);
    expect(mustEnrol(null, "/")).toBe(false);
  });
});

describe("mfaLabel", () => {
  it("names the state in the Users table", () => {
    expect(mfaLabel({ username: "ana", totpEnrolled: true })).toBe("enrolled");
    expect(mfaLabel({ username: "ana" })).toBe("not set up");
    expect(mfaLabel({ username: "oidc:123" })).toBe("identity provider");
  });
});
