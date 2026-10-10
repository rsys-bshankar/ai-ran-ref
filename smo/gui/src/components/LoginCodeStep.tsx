/**
 * The second step of the sign-in form (PR-SEC-7.2): shown after the password was accepted for an account that has a one-time code. Used by `pages/Login.tsx`;
 * the code rules (six digits or a recovery code) are in `lib/mfa.ts`.
 */

import { useState, type FormEvent } from "react";

import { cleanCode, isPlausibleCode } from "../lib/mfa";

/** The second step of a sign-in (PR-SEC-7.2): the six-digit code from the authenticator app, or one recovery code. The password was right already. */
export function LoginCodeStep({ onSubmit, onBack, error, busy }: {
  onSubmit: (code: string) => void;
  onBack: () => void;
  error: string | null;
  busy: boolean;
}) {
  const [code, setCode] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (isPlausibleCode(code)) onSubmit(cleanCode(code));
  };
  return (
    <form className="login-card" onSubmit={submit} aria-label="One-time code">
      <div className="brand big"><span className="brand-mark">M</span><div><strong>SMO</strong><span>Operator Console</span></div></div>
      <p className="muted">Enter the 6-digit code from your authenticator app. If you lost the device, enter one of your recovery codes instead.</p>
      <label className="field"><span className="field-label">One-time code</span>
        <input autoFocus autoComplete="one-time-code" inputMode="text" spellCheck={false} value={code} onChange={(e) => setCode(e.target.value)}
               placeholder="123456" aria-describedby="code-hint" required />
        <span className="field-hint" id="code-hint">A recovery code has 16 characters, like abcd-efgh-jkmn-pqrs. Each works once.</span>
      </label>
      {error && <div className="error-box" role="alert">{error}</div>}
      <button className="btn primary block" disabled={busy || !isPlausibleCode(code)}>{busy ? "Checking…" : "Verify"}</button>
      <button type="button" className="btn ghost block" onClick={onBack}>Back</button>
    </form>
  );
}
