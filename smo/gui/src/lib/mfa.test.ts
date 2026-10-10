/**
 * Unit tests of the pure one-time-code helpers in `mfa.ts`: the challenge check, code cleaning and format checks, the secret grouping, the recovery-code texts, the sign-in error wording,
 * the enrolment redirect rule and the Users-table label. No DOM. Run: `cd gui && npx vitest run src/lib/mfa.test.ts`.
 */

import { describe, expect, it } from "vitest";

import { cleanCode, groupSecret, isChallenge, isPlausibleCode, isRecoveryCode, isTotpCode, loginErrorMessage, mfaLabel, mustEnrol, recoveryCodesText, recoveryLeftText } from "./mfa";

describe("isChallenge", () => {
  // The answer of /login is told apart: a challenge needs `mfaRequired` and a challenge string, a session or a half-formed answer is not one.
  it("tells the second-step answer from a session", () => {
    expect(isChallenge({ mfaRequired: true, challenge: "a.b.c", expiresIn: 300 })).toBe(true);
    expect(isChallenge({ username: "ana", role: "admin", csrfToken: "x" })).toBe(false);
    expect(isChallenge({ mfaRequired: true })).toBe(false);
    expect(isChallenge(null)).toBe(false);
    expect(isChallenge(undefined)).toBe(false);
  });
});

describe("codes as typed", () => {
  // Spaces and dashes that people add when reading a code aloud are removed before the code is checked or sent.
  it("ignores the spaces and dashes people add", () => {
    expect(cleanCode(" 123 456 ")).toBe("123456");
    expect(cleanCode("abcd-efgh-jkmn-pqrs")).toBe("abcdefghjkmnpqrs");
  });
  // Only six digits or sixteen letters and digits are plausible codes; anything else (including markup) is refused before it is sent.
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
  // The setup key is shown in groups of four so it can be typed into an authenticator app by hand.
  it("shows the key in groups of four", () => {
    expect(groupSecret("ABCDEFGHIJKLMNOP")).toBe("ABCD EFGH IJKL MNOP");
    expect(groupSecret("ABCDEFGHIJ")).toBe("ABCD EFGH IJ");
    expect(groupSecret("")).toBe("");
  });
});

describe("recovery code text", () => {
  // The recovery codes are copied one per line with a closing newline.
  it("lists one code per line", () => {
    expect(recoveryCodesText(["a", "b"])).toBe("a\nb\n");
  });
  // The recovery-code count is worded in the singular and plural, and zero warns the user to generate new codes.
  it("says how many are left, and warns at none", () => {
    expect(recoveryLeftText(9)).toBe("9 recovery codes left.");
    expect(recoveryLeftText(1)).toBe("1 recovery code left.");
    expect(recoveryLeftText(0)).toMatch(/no recovery codes left/);
  });
});

describe("loginErrorMessage", () => {
  // Each reason code the backend gives has its own sentence for the sign-in form.
  it("has its own sentence for each reason the backend gives", () => {
    expect(loginErrorMessage({ status: 401, title: "INVALID_CODE" })).toMatch(/wrong, or has been used/);
    expect(loginErrorMessage({ status: 401, title: "CHALLENGE_INVALID" })).toMatch(/Enter your password again/);
    expect(loginErrorMessage({ status: 403, title: "LOGIN_MODE_OIDC_ONLY" })).toMatch(/single sign-on/);
    expect(loginErrorMessage({ status: 403, title: "BREAK_GLASS_NEEDS_TOTP" })).toMatch(/no one-time code/);
  });
  // An unknown title is never echoed (it could hold markup or a prototype name); the status, then the message, then a generic line stand in.
  it("falls back on the status, then on the message, and never echoes an unknown title", () => {
    expect(loginErrorMessage({ status: 429 })).toMatch(/Too many failed attempts/);
    expect(loginErrorMessage({ status: 401 })).toBe("Invalid username or password.");
    expect(loginErrorMessage({ status: 500, message: "boom" })).toBe("boom");
    expect(loginErrorMessage({ status: 400, title: "<img src=x onerror=alert(1)>" })).toBe("Sign-in failed.");
    expect(loginErrorMessage({ title: "__proto__" })).toBe("Sign-in failed.");
  });
});

describe("mustEnrol", () => {
  // An admin who must enrol is redirected everywhere except the Account security page, and a user who need not enrol never is.
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
  // The Users table says "enrolled", "not set up" or "identity provider" (a user named oidc:...).
  it("names the state in the Users table", () => {
    expect(mfaLabel({ username: "ana", totpEnrolled: true })).toBe("enrolled");
    expect(mfaLabel({ username: "ana" })).toBe("not set up");
    expect(mfaLabel({ username: "oidc:123" })).toBe("identity provider");
  });
});
