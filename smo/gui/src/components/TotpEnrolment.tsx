import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "../api/client";
import type { TotpBegin, TotpConfirmed, TotpStatus } from "../api/types";
import { cleanCode, groupSecret, isTotpCode, recoveryCodesText, recoveryLeftText } from "../lib/mfa";
import { Card } from "./ui";

/** Shown once, after the confirmation: the recovery codes cannot be read again, so leaving takes an explicit "I have saved them". */
export function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(recoveryCodesText(codes)); setCopied(true); } catch { setCopied(false); }
  };
  return (
    <div className="form" role="region" aria-label="Recovery codes">
      <div className="error-box" role="alert">Save these recovery codes now. They are shown only this once. Each one signs you in once if you lose your device.</div>
      <ul className="recovery-codes">{codes.map((c) => <li key={c}><code>{c}</code></li>)}</ul>
      <div className="row gap">
        <button type="button" className="btn" onClick={copy}>{copied ? "Copied" : "Copy all"}</button>
        <button type="button" className="btn primary" onClick={onDone}>I have saved them</button>
      </div>
    </div>
  );
}

function CodeForm({ label, button, busy, error, onSubmit }: { label: string; button: string; busy: boolean; error: string | null; onSubmit: (code: string) => void }) {
  const [code, setCode] = useState("");
  const submit = (e: FormEvent) => { e.preventDefault(); if (isTotpCode(code)) onSubmit(cleanCode(code)); };
  return (
    <form className="form inline" onSubmit={submit}>
      <label className="field"><span className="field-label">{label}</span>
        <input autoComplete="one-time-code" inputMode="numeric" maxLength={7} placeholder="123456" value={code} onChange={(e) => setCode(e.target.value)} required />
      </label>
      <button className="btn primary" disabled={busy || !isTotpCode(code)}>{busy ? "Checking…" : button}</button>
      {error && <div className="error-box" role="alert" style={{ flexBasis: "100%" }}>{error}</div>}
    </form>
  );
}

const reason = (err: unknown): string => (err instanceof ApiError ? (err.detail ?? err.title) : (err as Error).message);

/** Account security (PR-SEC-7.1, 7.3): set up the one-time code of the signed-in local account, see the recovery codes left, make new ones. */
export function TotpEnrolment({ required = false }: { required?: boolean }) {
  const qc = useQueryClient();
  const status = useQuery<TotpStatus, ApiError>({ queryKey: ["bff", "totp"], queryFn: () => api<TotpStatus>("/me/totp"), retry: false });
  const [begun, setBegun] = useState<TotpBegin | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["bff", "totp"] }); qc.invalidateQueries({ queryKey: ["bff", "me"] }); };

  const begin = useMutation<TotpBegin, ApiError>({
    mutationFn: () => api<TotpBegin>("/me/totp/begin", { method: "POST" }),
    onSuccess: (b) => { setBegun(b); setError(null); refresh(); },
    onError: (e) => setError(reason(e)),
  });
  const confirm = useMutation<TotpConfirmed, ApiError, string>({
    mutationFn: (code) => api<TotpConfirmed>("/me/totp/confirm", { method: "POST", json: { code } }),
    // the secret is not needed any more, and the recovery codes are held in this component only until the user says they are saved
    onSuccess: (r) => { setBegun(null); setCodes(r.recoveryCodes); setError(null); },
    onError: (e) => setError(reason(e)),
  });
  const regenerate = useMutation<TotpConfirmed, ApiError, string>({
    mutationFn: (code) => api<TotpConfirmed>("/me/totp/recovery-codes", { method: "POST", json: { code } }),
    onSuccess: (r) => { setCodes(r.recoveryCodes); setError(null); },
    onError: (e) => setError(reason(e)),
  });

  if (status.isLoading) return <Card title="One-time code"><p className="muted">Loading…</p></Card>;
  if (status.error || !status.data) return <Card title="One-time code"><div className="error-box" role="alert">{status.error ? reason(status.error) : "Could not load."}</div></Card>;
  const s = status.data;

  if (codes) {
    return <Card title="Recovery codes"><RecoveryCodes codes={codes} onDone={() => { setCodes(null); refresh(); }} /></Card>;
  }
  if (s.reason === "identity provider") {
    return <Card title="One-time code"><p>You sign in through the identity provider, which asks for the second factor. There is nothing to set up here.</p></Card>;
  }
  return (
    <Card title="One-time code (authenticator app)">
      {required && <div className="error-box" role="alert">An administrator account must have a one-time code before it can use the console. Set it up below.</div>}
      {s.enrolled ? (
        <>
          <p>A one-time code is set up for your account. You enter it after your password. {recoveryLeftText(s.recoveryCodesLeft)}</p>
          <h3>New recovery codes</h3>
          <p className="muted small">Replaces all the old ones. Asks for a current code from your app.</p>
          <CodeForm label="Current code" button="Generate new recovery codes" busy={regenerate.isPending} error={error} onSubmit={(c) => regenerate.mutate(c)} />
          <p className="muted small">To use a new device, ask an administrator to reset your one-time code (Admin &gt; Users), then set it up again.</p>
        </>
      ) : !s.available ? (
        <div className="error-box" role="alert">One-time codes are not set up on this server. An administrator must set GUI_TOTP_KEY (or GUI_TOTP_KEY_FILE).</div>
      ) : !begun ? (
        <>
          <p>Protect your account with a code from an authenticator app (Google Authenticator, Microsoft Authenticator, FreeOTP, a password manager). It asks for the code every time you sign in with your password.</p>
          {error && <div className="error-box" role="alert">{error}</div>}
          <button className="btn primary" onClick={() => begin.mutate()} disabled={begin.isPending}>{s.pending ? "Start again with a new secret" : "Set up a one-time code"}</button>
        </>
      ) : (
        <div className="form">
          <p>In your authenticator app choose &quot;enter a setup key&quot; (there is no QR code here), name it, and type this key. Account type: time based.</p>
          <label className="field"><span className="field-label">Setup key</span>
            <input readOnly value={groupSecret(begun.secret)} aria-label="Setup key" onFocus={(e) => e.currentTarget.select()} style={{ fontFamily: "monospace" }} />
          </label>
          <details>
            <summary className="muted small">otpauth link, for apps that open one</summary>
            <input readOnly value={begun.otpauthUri} aria-label="otpauth link" onFocus={(e) => e.currentTarget.select()} style={{ width: "100%", fontFamily: "monospace" }} />
          </details>
          <p className="muted small">The code is not active until you type a valid one from the app.</p>
          <CodeForm label="Code from the app" button="Confirm" busy={confirm.isPending} error={error} onSubmit={(c) => confirm.mutate(c)} />
        </div>
      )}
    </Card>
  );
}
