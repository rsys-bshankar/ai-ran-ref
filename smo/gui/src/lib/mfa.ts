// One-time codes (PR-SEC-7): what the sign-in's second step and the Account security page need that is not rendering.
// The reason codes are the backend's (gui-bff/app/main.py): the page shows its own wording for each and never the server's text for an unknown one.

import type { Me } from "../api/types";

/** What `POST /api/login` answers for an account with a one-time code: no session yet, a challenge to send back with the code. */
export interface LoginChallenge { mfaRequired: true; challenge: string; expiresIn: number }
/** The session `POST /api/login` or `POST /api/login/totp` opens; `recoveryCodesLeft` is there when a recovery code was spent. */
export type SignedIn = Me & { recoveryCodesLeft?: number };

export function isChallenge(result: unknown): result is LoginChallenge {
  return typeof result === "object" && result !== null && (result as { mfaRequired?: unknown }).mfaRequired === true
    && typeof (result as { challenge?: unknown }).challenge === "string";
}

/** The secret in groups of four, for typing into an authenticator app by hand. */
export function groupSecret(secret: string): string {
  return secret.replace(/\s+/g, "").replace(/(.{4})(?=.)/g, "$1 ");
}

/** What was typed, without the spaces and dashes people add when reading a code aloud. */
export function cleanCode(input: string): string {
  return input.trim().replace(/[\s-]+/g, "");
}

/** A six-digit one-time code (as against a recovery code). */
export function isTotpCode(input: string): boolean {
  return /^\d{6}$/.test(cleanCode(input));
}

/** A recovery code is sixteen letters and digits (shown in four groups of four). */
export function isRecoveryCode(input: string): boolean {
  return /^[a-z0-9]{16}$/i.test(cleanCode(input));
}

export function isPlausibleCode(input: string): boolean {
  return isTotpCode(input) || isRecoveryCode(input);
}

export function recoveryCodesText(codes: string[]): string {
  return codes.join("\n") + "\n";
}

export function recoveryLeftText(left: number): string {
  if (left <= 0) return "You have no recovery codes left: generate new ones on the Account security page.";
  return `${left} recovery code${left === 1 ? "" : "s"} left.`;
}

const LOGIN_MESSAGES: Record<string, string> = {
  INVALID_CODE: "That code is wrong, or has been used already. Wait for the next one on your device and try again.",
  CHALLENGE_INVALID: "The sign-in took too long. Enter your password again.",
  TOO_MANY_ATTEMPTS: "Too many failed attempts — try again in a few minutes.",
  INVALID_CREDENTIALS: "Invalid username or password.",
  LOGIN_MODE_OIDC_ONLY: "Local sign-in is closed on this console. Use the single sign-on button.",
  BREAK_GLASS_NEEDS_TOTP: "This break-glass account has no one-time code set up, so it cannot sign in. Ask another admin.",
  TOTP_KEY_UNAVAILABLE: "The one-time code cannot be checked on this server. Ask an administrator.",
  LOCAL_LOGIN_DISABLED: "Local sign-in is turned off. Use the single sign-on button.",
};

/** The sentence for a failed sign-in step, from the backend's reason code (its `title`); the status stands in for an old backend that sends none. */
export function loginErrorMessage(err: { status?: number; title?: string; message?: string }): string {
  if (err.title && Object.prototype.hasOwnProperty.call(LOGIN_MESSAGES, err.title)) return LOGIN_MESSAGES[err.title];
  if (err.status === 429) return LOGIN_MESSAGES.TOO_MANY_ATTEMPTS;
  if (err.status === 401) return LOGIN_MESSAGES.INVALID_CREDENTIALS;
  return err.message || "Sign-in failed.";
}

/** The routes an admin without a one-time code may still open when the backend requires one (GUI_ADMIN_MFA_REQUIRED). */
export function mustEnrol(me: Pick<Me, "mfaEnrolmentRequired"> | null | undefined, pathname: string): boolean {
  return Boolean(me?.mfaEnrolmentRequired) && pathname !== "/security";
}

/** What the Users table says about a user's one-time code. */
export function mfaLabel(u: { username: string; totpEnrolled?: boolean }): string {
  if (u.username.startsWith("oidc:")) return "identity provider";
  return u.totpEnrolled ? "enrolled" : "not set up";
}
