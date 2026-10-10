/** The right half of the sign-in page (`login.form`, handoff `Login.dc.html`): the single sign-on button first when the backend offers OIDC, then the
 * local account form, then the note about the one-time code. After a right password for an account with a one-time code the form gives way to
 * `components/LoginCodeStep` (PR-SEC-7.2). With `GUI_LOGIN_MODE=oidc` only the provider's button shows, and a break-glass account reaches the
 * form through a small link (PR-SEC-7.6). The labels "Username", "Password" and the button "Sign in" are what `scripts/gui_e2e.py` looks for. */
import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { useAuth } from "../../../auth/AuthContext";
import { LoginCodeStep } from "../../../components/LoginCodeStep";
import { useToast } from "../../../components/Toast";
import { Icon } from "../../../kit/icons";
import { loginErrorMessage, recoveryLeftText, type LoginChallenge } from "../../../lib/mfa";
import { oidcErrorMessage, oidcLoginHref } from "../../../lib/oidc";
import { useAuthConfig } from "../data/queries";

/**
 * The sign-in form and its code step. The first step posts the username and password through `AuthProvider.login`; an answer with a challenge opens the code step, a session
 * opens the app. A spent challenge (CHALLENGE_INVALID) or a lock-out (429) sends the user back to the password step. With `GUI_LOGIN_MODE=oidc` the password form is hidden
 * behind a small "Break-glass sign-in" link that appears only when the backend says a break-glass account exists. After a recovery code is spent, a toast says how many are left.
 */
export function SignInForm() {
  const { login, loginWithCode } = useAuth();
  const toast = useToast();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState<LoginChallenge | null>(null);
  const [breakGlassOpen, setBreakGlassOpen] = useState(false);
  const [params] = useSearchParams();
  const authConfig = useAuthConfig();
  const ssoHref = oidcLoginHref(authConfig.data);
  const formIsNormal = authConfig.data?.localLogin !== false;
  // With GUI_LOGIN_MODE=oidc only the provider's button is shown; a break-glass account reaches the form through a small link.
  const breakGlassLink = !formIsNormal && authConfig.data?.breakGlass === true;
  const showPassword = formIsNormal || (breakGlassLink && breakGlassOpen);
  const ssoError = oidcErrorMessage(params.get("oidc_error"));

  const fail = (err: unknown) => {
    const e = err as { status?: number; title?: string; message?: string };
    setError(loginErrorMessage(e));
    // a challenge that no longer works (expired, spent) or an account that is locked: back to the password
    if (e.title === "CHALLENGE_INVALID" || e.status === 429) setChallenge(null);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const next = await login(username, password);
      if (next) setChallenge(next);
      setPassword("");
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async (code: string) => {
    if (!challenge) return;
    setBusy(true);
    setError(null);
    try {
      const done = await loginWithCode(challenge.challenge, code);
      if (done.recoveryCodesLeft !== undefined) toast.push({ tone: done.recoveryCodesLeft > 2 ? "info" : "error", text: recoveryLeftText(done.recoveryCodesLeft) });
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  if (challenge) {
    return (
      <section className="login-form-wrap" data-section="login.form">
        <LoginCodeStep onSubmit={submitCode} onBack={() => { setChallenge(null); setError(null); }} error={error} busy={busy} />
      </section>
    );
  }

  return (
    <section className="login-form-wrap" data-section="login.form">
      <form className="login-card" onSubmit={submit} aria-labelledby="sign-in-h">
        <div className="col" style={{ gap: 6 }}>
          <h2 id="sign-in-h">Sign in</h2>
          <p className="muted">Use your operator account to manage rApps, AI/ML models, alarms and policies. Every action is recorded against it.</p>
        </div>
        {ssoError && <div className="error-box" role="alert">{ssoError}</div>}
        {ssoHref && (
          // A full navigation, not a fetch: the backend answers with a redirect to the identity provider.
          <a className="btn primary block" href={ssoHref}><Icon name="safeguards" />Sign in with {authConfig.data?.oidc.providerName ?? "SSO"}</a>
        )}
        {ssoHref && formIsNormal && <div className="or-line">or with a local account</div>}
        {breakGlassLink && !breakGlassOpen && (
          <button type="button" className="btn ghost small" onClick={() => setBreakGlassOpen(true)}>Break-glass sign-in</button>
        )}
        {showPassword && (<>
          {breakGlassLink && <p className="muted small">Break-glass accounts only: a password and a one-time code.</p>}
          <label className="field"><span className="field-label">Username</span>
            <input autoFocus={!ssoHref || breakGlassLink} autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
          </label>
          <label className="field"><span className="field-label">Password</span>
            <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </label>
          {error && <div className="error-box" role="alert">{error}</div>}
          <button type="submit" className={`btn block${ssoHref ? "" : " primary"}`} disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
          <div className="callout info">
            <Icon name="security" />
            <span className="small muted">If your account has a one-time code, the code from your authenticator app follows the password. Lost the device? Use a recovery code.</span>
          </div>
        </>)}
        {breakGlassLink && <p className="small faint">Break-glass sign-in stays available when single sign-on is down. Repeated failed attempts lock the account.</p>}
      </form>
    </section>
  );
}
