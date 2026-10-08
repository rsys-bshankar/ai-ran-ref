import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { LoginCodeStep } from "../components/LoginCodeStep";
import { useToast } from "../components/Toast";
import { oidcErrorMessage, oidcLoginHref, type AuthConfig } from "../lib/oidc";
import { loginErrorMessage, recoveryLeftText, type LoginChallenge } from "../lib/mfa";

export function Login() {
  const { me, login, loginWithCode } = useAuth();
  const toast = useToast();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState<LoginChallenge | null>(null);
  const [breakGlassOpen, setBreakGlassOpen] = useState(false);
  const [params] = useSearchParams();
  // What the backend offers (PR-SEC-6, PR-SEC-7.6): OIDC when it is on, the password form unless it was switched off or the mode is "oidc". Until it
  // answers (or if it cannot), the password form is shown, as before.
  const authConfig = useQuery<AuthConfig>({ queryKey: ["bff", "auth-config"], queryFn: () => api<AuthConfig>("/auth/config"), staleTime: 60_000, retry: false });
  const ssoHref = oidcLoginHref(authConfig.data);
  const formIsNormal = authConfig.data?.localLogin !== false;
  // With GUI_LOGIN_MODE=oidc only the provider's button is shown; a break-glass account reaches the form through a small link.
  const breakGlassLink = !formIsNormal && authConfig.data?.breakGlass === true;
  const showPassword = formIsNormal || (breakGlassLink && breakGlassOpen);
  const ssoError = oidcErrorMessage(params.get("oidc_error"));

  if (me) return <Navigate to={(location.state as { from?: string } | null)?.from ?? "/"} replace />;

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
    return <div className="login-page"><LoginCodeStep onSubmit={submitCode} onBack={() => { setChallenge(null); setError(null); }} error={error} busy={busy} /></div>;
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="brand big"><span className="brand-mark">M</span><div><strong>SMO</strong><span>Operator Console</span></div></div>
        <p className="muted">AI-RAN Service Management &amp; Orchestration — sign in to manage rApps, AI/ML models, alarms and policies.</p>
        {ssoError && <div className="error-box" role="alert">{ssoError}</div>}
        {ssoHref && (
          // A full navigation, not a fetch: the backend answers with a redirect to the identity provider.
          <a className="btn primary block" href={ssoHref}>Sign in with {authConfig.data?.oidc.providerName ?? "SSO"}</a>
        )}
        {ssoHref && formIsNormal && <p className="muted small" style={{ textAlign: "center" }}>or with a local account</p>}
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
          <button className={`btn block${ssoHref ? "" : " primary"}`} disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        </>)}
      </form>
    </div>
  );
}
