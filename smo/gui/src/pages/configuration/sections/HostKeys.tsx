/** Configuration · endpoint trust (`configuration.trust`): the O1 adaptor endpoints, paged by the server (`GET /o1-adaptor-endpoints`, filter
 * by health), and for the selected `ssh` endpoint the SSH host keys pinned for it (`GET /o1-adaptor-endpoints/{id}/host-keys`). An ssh endpoint
 * with no key pinned has its connections refused (unless the NETCONF_SSH_KNOWN_HOSTS file lists it), so that is a red alert; an unreachable one
 * says a changed host key is one possible cause. An admin pins (or re-pins) a key (`PUT …/host-keys`, the BFF records the admin as `pinnedBy`) and
 * removes one (`DELETE …/host-keys/{keyType}`): the trust anchor of the SSH connection, so admin only. The backend does not record a key mismatch
 * (README, Known limits). */
import { useState } from "react";
import { Link } from "react-router-dom";

import { ActionButton, Can, Card, DataTable, Field, Id, StateBadge, type Column } from "../../../components/ui";
import { Callout } from "../../../kit/Callout";
import { ServerTable } from "../../../kit/ServerTable";
import { Empty, QueryState } from "../../../kit/states";
import { formatTime } from "../../../lib/domain";
import { elementHref } from "../../element/data/types";
import { ENDPOINTS_PATH, hostKeysPath, useHostKeys } from "../data/queries";
import type { Endpoint } from "../data/types";

/** The endpoint table's columns. */
const COLUMNS: Column<Endpoint>[] = [
  { header: "Endpoint", render: (e) => <Id value={e.endpointId} /> },
  { header: "Element", render: (e) => <Link to={elementHref(e.managedElementRef)} onClick={(ev) => ev.stopPropagation()}>{e.managedElementRef}</Link> },
  { header: "Transport", render: (e) => e.transport },
  { header: "Health", render: (e) => <StateBadge state={e.healthStatus} /> },
  { header: "Last heartbeat", render: (e) => <span className="small muted">{formatTime(e.lastHeartbeatAt)}</span> },
];

/** The two cards: endpoints, and the keys of the selected one. */
export function HostKeys() {
  const [health, setHealth] = useState("");
  const [selected, setSelected] = useState<Endpoint | null>(null);
  return (
    <div className="grid g-main-side" data-section="configuration.trust">
      <Card title="O1 adaptor endpoints" sub="pick an ssh endpoint to see its pinned host keys" actions={<label className="row small">Health
        <select aria-label="Health" value={health} onChange={(e) => setHealth(e.target.value)}>
          <option value="">All</option>{["ACTIVE", "DISCOVERED", "DEGRADED", "UNREACHABLE"].map((h) => <option key={h}>{h}</option>)}
        </select></label>}>
        <ServerTable<Endpoint> path={ENDPOINTS_PATH} query={health ? { health_status: health } : undefined} columns={COLUMNS} rowKey={(e) => e.endpointId}
          onRowClick={setSelected} selectedKey={selected?.endpointId} empty="No O1 adaptor endpoint registered." />
      </Card>
      {selected ? <Keys endpoint={selected} /> : <Card title="Pinned SSH host keys"><Empty title="Pick an endpoint." /></Card>}
    </div>
  );
}

/** The keys of one endpoint, with the alerts. */
function Keys({ endpoint }: { endpoint: Endpoint }) {
  const ssh = endpoint.transport === "ssh";
  const keys = useHostKeys(ssh ? endpoint.endpointId : null);
  return (
    <Card title="Pinned SSH host keys" sub={`${endpoint.managedElementRef} · ${endpoint.adaptorUri}`}>
      {!ssh ? <Empty title={`This endpoint uses ${endpoint.transport}.`}>Host keys apply to NETCONF over SSH endpoints only.</Empty> : (
        <>
          {endpoint.healthStatus === "UNREACHABLE" && (
            <Callout tone="bad" title="Endpoint unreachable">A host key that differs from the pinned one is refused, and is one possible cause; the backend does not say which.</Callout>
          )}
          <QueryState q={keys} empty={<Callout tone="bad" title="No host key pinned">Connections to this endpoint are refused unless NETCONF_SSH_KNOWN_HOSTS lists it. An admin pins the key the device presents.</Callout>}>
            <DataTable rows={keys.data} rowKey={(k) => k.keyType} columns={[
              { header: "Key type", render: (k) => <span className="mono small">{k.keyType}</span> },
              { header: "Fingerprint", render: (k) => <span className="mono small">{k.fingerprint}</span> },
              { header: "Pinned by", render: (k) => k.pinnedBy },
              { header: "Pinned", render: (k) => <span className="small muted">{formatTime(k.pinnedAt)}</span> },
              { header: "", className: "actions", render: (k) => <ActionButton label="Remove" tone="danger"
                confirm={`Remove the pinned ${k.keyType} key? Connections are refused until a key is pinned again.`}
                action={{ method: "DELETE", path: `${hostKeysPath(endpoint.endpointId)}/${encodeURIComponent(k.keyType)}`, success: "Host key removed" }} /> },
            ]} />
          </QueryState>
          <Can method="PUT" path={hostKeysPath(endpoint.endpointId)}><PinKey endpointId={endpoint.endpointId} /></Can>
        </>
      )}
    </Card>
  );
}

/** The SSH key types a NETCONF device presents. */
const KEY_TYPES = ["ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "rsa-sha2-512", "rsa-sha2-256", "ssh-rsa"] as const;

/** The key type and base64 blob of what was pasted: a bare blob keeps `fallbackType`; a `.pub` or known_hosts line gives its own type. */
export function parseKeyInput(text: string, fallbackType: string): { type: string; blob: string } {
  const parts = text.trim().split(/\s+/).filter(Boolean);
  const i = parts.findIndex((t) => /^(ssh-|ecdsa-)/.test(t));
  if (i >= 0 && parts[i + 1]) return { type: parts[i], blob: parts[i + 1] };
  return { type: fallbackType, blob: parts.length === 1 ? parts[0] : "" };
}

/** An admin's form to pin (or replace) the host key of one type; the public key is the base64 blob the device presents. */
function PinKey({ endpointId }: { endpointId: string }) {
  const [keyType, setKeyType] = useState<string>(KEY_TYPES[0]);
  const [publicKey, setPublicKey] = useState("");
  // a pasted "ssh-ed25519 AAAA… comment" (.pub) or "host ssh-ed25519 AAAA…" (known_hosts) line is reduced to its key blob and its type
  const { type, blob } = parseKeyInput(publicKey, keyType);
  return (
    <details className="inset" data-part="pin-key">
      <summary className="small">Pin a host key (admin)</summary>
      <div className="form">
        <Field label="Key type"><select value={type} onChange={(e) => setKeyType(e.target.value)} aria-label="Key type">{KEY_TYPES.map((t) => <option key={t}>{t}</option>)}</select></Field>
        <Field label="Public key" hint="The base64 key the device presents, or a whole known_hosts / .pub line"><textarea rows={3} className="mono" value={publicKey}
          onChange={(e) => setPublicKey(e.target.value)} aria-label="Public key" spellCheck={false} /></Field>
        <div className="row end">
          <ActionButton label="Pin key" tone="primary" disabled={!blob}
            confirm="Pin this key? The element's connections are trusted only if it presents exactly this key."
            action={{ method: "PUT", path: hostKeysPath(endpointId), json: { keyType: type, publicKey: blob, pinnedBy: "smo-gui" }, success: `Host key ${type} pinned` }}
            onDone={() => setPublicKey("")} />
        </div>
      </div>
    </details>
  );
}
