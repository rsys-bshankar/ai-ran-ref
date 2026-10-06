import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { oidcErrorMessage, oidcLoginHref, type AuthConfig } from "../lib/oidc";

export function Login() {
  const { me, login } = useAuth();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [params] = useSearchParams();
  // What the backend offers (PR-SEC-6): OIDC when it is on, the password form unless it was switched off. Until it answers (or if it cannot),
  // the password form is shown, as before.
  const authConfig = useQuery<AuthConfig>({ queryKey: ["bff", "auth-config"], queryFn: () => api<AuthConfig>("/auth/config"), staleTime: 60_000, retry: false });
  const ssoHref = oidcLoginHref(authConfig.data);
  const showPassword = authConfig.data?.localLogin !== false;
  const ssoError = oidcErrorMessage(params.get("oidc_error"));

  if (me) return <Navigate to={(location.state as { from?: string } | null)?.from ?? "/"} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
    } catch (err) {
      const status = (err as { status?: number }).status;
      setError(status === 429 ? "Too many failed attempts — try again in a few minutes." : status === 401 ? "Invalid username or password." : (err as Error).message);
    } finally {
      setBusy(false);
    }
  };

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
        {ssoHref && showPassword && <p className="muted small" style={{ textAlign: "center" }}>or with a local account</p>}
        {showPassword && (<>
          <label className="field"><span className="field-label">Username</span>
            <input autoFocus={!ssoHref} autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
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
