/** Data & Exposure → SME, the API invokers box (`data.invokers`): onboarded invokers (server-paged), whether each is trusted, "Trust…" (the
 * trusted-invoker security-context form), remove trust, offboard, and the role-gated onboarding form whose one-time secret is shown once. The
 * "with a security context" count is the trusted-invoker list's own total. Moved from the pre-redesign page with its actions unchanged. */
import { useState } from "react";

import { useSmoAction } from "../../../api/hooks";
import type { SmeInvoker } from "../../../api/types";
import { ActionButton, Can, Card, Field, Modal, StateBadge } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { PATHS, useTrustedCount } from "../data/queries";

/** The box. */
export function Invokers() {
  const trusted = useTrustedCount();
  const onboard = useSmoAction();
  const [publicKey, setPublicKey] = useState("");
  const [secret, setSecret] = useState<{ apiInvokerId: string; onboardingSecret: string } | null>(null);
  const [trustFor, setTrustFor] = useState<string | null>(null);
  return (
    <Card section="data.invokers" title="API invokers">
      <ServerTable<SmeInvoker> path={PATHS.invokers} rowKey={(i) => i.apiInvokerId} empty="No invokers onboarded." columns={[
        { header: "Invoker", render: (i) => <code className="small">{i.apiInvokerId}</code> },
        { header: "Authenticates with", render: (i) => i.keyAuthentication ? "secret or signed assertion" : "secret" },
        { header: "Trusted", render: (i) => i.trusted ? <StateBadge state="ENABLED" /> : <span className="muted">no</span> },
        { header: "", className: "actions", render: (i) => <div className="row gap end">
          {i.trusted
            ? <ActionButton label="Remove trust" tone="danger" confirm="Remove this invoker's security context?" action={{ method: "DELETE", path: `${PATHS.trustedInvokers}/${i.apiInvokerId}`, success: "Security context removed" }} />
            : <Can method="PUT" path={`${PATHS.trustedInvokers}/${i.apiInvokerId}`}><button type="button" className="btn small" onClick={() => setTrustFor(i.apiInvokerId)}>Trust…</button></Can>}
          <ActionButton label="Offboard" tone="danger" confirm="Offboard this invoker? Its tokens and security context are revoked." action={{ method: "DELETE", path: `${PATHS.invokers}/${i.apiInvokerId}`, success: "Invoker offboarded" }} />
        </div> },
      ]} />
      <p className="muted small">{trusted.data?.total ?? "—"} with a security context. Onboarding secrets are hashed at SME and never shown again. An invoker onboarded with a PEM public key can also authenticate with an RFC 7523 signed client assertion.</p>
      <Can method="POST" path={PATHS.invokers}>
        <div className="form inline">
          <Field label="Invoker public key"><input value={publicKey} onChange={(e) => setPublicKey(e.target.value)} placeholder="-----BEGIN PUBLIC KEY-----…" /></Field>
          <button type="button" className="btn" disabled={!publicKey || onboard.isPending} onClick={() => onboard.mutate(
            { method: "POST", path: PATHS.invokers, json: { apiInvokerPublicKey: publicKey }, success: "Invoker onboarded" },
            { onSuccess: (d) => { setSecret(d as { apiInvokerId: string; onboardingSecret: string }); setPublicKey(""); } })}>Onboard invoker</button>
        </div>
      </Can>
      {secret && (
        <Modal title="Invoker onboarded — copy the secret now" onClose={() => setSecret(null)}>
          <p>SME stores only a hash of this onboarding secret. It is shown once; the invoker uses it for the OAuth2 client_credentials grant.</p>
          <dl className="kv"><div><dt>apiInvokerId</dt><dd><code>{secret.apiInvokerId}</code></dd></div><div><dt>onboardingSecret</dt><dd><code>{secret.onboardingSecret}</code></dd></div></dl>
        </Modal>
      )}
      {trustFor && <TrustInvoker invokerId={trustFor} onClose={() => setTrustFor(null)} />}
    </Card>
  );
}

/** The trusted-invoker security-context form. */
function TrustInvoker({ invokerId, onClose }: { invokerId: string; onClose: () => void }) {
  const [dest, setDest] = useState("http://invoker:8000/security-notify");
  const [aefId, setAefId] = useState("");
  const [apiId, setApiId] = useState("");
  const [method, setMethod] = useState("OAUTH");
  const action = useSmoAction();
  return (
    <Modal title="Register trusted-invoker security context" onClose={onClose}>
      <form className="form" onSubmit={(e) => {
        e.preventDefault();
        action.mutate({ method: "PUT", path: `${PATHS.trustedInvokers}/${invokerId}`, success: "Security context registered",
          json: { notificationDestination: dest, securityInfo: [{ aefId: aefId || null, apiId: apiId || null, prefSecurityMethods: [method] }] } }, { onSuccess: onClose });
      }}>
        <p className="muted small">Invoker <code>{invokerId}</code></p>
        <Field label="Notification destination"><input value={dest} onChange={(e) => setDest(e.target.value)} required /></Field>
        <div className="grid cols-3 tight">
          <Field label="AEF ID"><input value={aefId} onChange={(e) => setAefId(e.target.value)} /></Field>
          <Field label="API ID"><input value={apiId} onChange={(e) => setApiId(e.target.value)} /></Field>
          <Field label="Preferred method"><select value={method} onChange={(e) => setMethod(e.target.value)}><option>OAUTH</option><option>PSK</option><option>PKI</option></select></Field>
        </div>
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn primary" disabled={action.isPending}>Register</button></div>
      </form>
    </Modal>
  );
}
