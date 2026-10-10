/** Configuration · endpoint trust (`configuration.trust`): the O1 adaptor endpoints, paged by the server (`GET /o1-adaptor-endpoints`, filter
 * by health), and for the selected `ssh` endpoint the SSH host keys pinned for it (`GET /o1-adaptor-endpoints/{id}/host-keys`). An ssh endpoint
 * with no key pinned has its connections refused (unless the NETCONF_SSH_KNOWN_HOSTS file lists it), so that is a red alert; an unreachable one
 * says a changed host key is one possible cause. The backend does not record a key mismatch, and re-pinning (`PUT …/host-keys`) is not exposed
 * by the GUI BFF: both are listed in the README's Known limits. */
import { useState } from "react";
import { Link } from "react-router-dom";

import { Card, DataTable, Id, StateBadge, type Column } from "../../../components/ui";
import { Callout } from "../../../kit/Callout";
import { ServerTable } from "../../../kit/ServerTable";
import { Empty, QueryState } from "../../../kit/states";
import { formatTime } from "../../../lib/domain";
import { elementHref } from "../../element/data/types";
import { ENDPOINTS_PATH, useHostKeys } from "../data/queries";
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
            ]} />
          </QueryState>
          <p className="gap-note">Re-pin: the GUI BFF does not expose pinning a host key yet; an admin pins it through the RAN NF OAM API.</p>
        </>
      )}
    </Card>
  );
}
