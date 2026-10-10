/** Infrastructure → O1 endpoints & jobs, the managed-elements box (`infrastructure.o1-endpoints`): the server-paged O1 adaptor endpoints with a
 * health filter (`?health_status=`), their region / tenant (PR-SEC-10.2), heartbeat and health discovery, and the role-gated "Register endpoint"
 * form. The element count is the table's own total (the infrastructure summary has no O1 endpoint count). */
import { useState } from "react";

import { useSmoAction } from "../../../api/hooks";
import type { O1Endpoint } from "../../../api/types";
import { ActionButton, Can, Card, Field, Modal, StateBadge } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { describePlace, formatTime, splitList } from "../../../lib/domain";
import { PATHS } from "../data/queries";

const HEALTH = ["ACTIVE", "DISCOVERED", "DEGRADED", "UNREACHABLE", "INACTIVE"] as const;

/** The box. */
export function O1Endpoints() {
  const [registering, setRegistering] = useState(false);
  const [health, setHealth] = useState("");
  return (
    <Card section="infrastructure.o1-endpoints" title="O1 adaptor endpoints (managed elements)" actions={<>
      <select value={health} onChange={(e) => setHealth(e.target.value)} aria-label="Health"><option value="">Any health</option>{HEALTH.map((h) => <option key={h}>{h}</option>)}</select>
      <ActionButton label="Run health discovery" action={{ method: "POST", path: `${PATHS.endpoints}/discover`, success: "Endpoint health re-aged" }} />
      <Can method="POST" path={PATHS.endpoints}><button type="button" className="btn primary" onClick={() => setRegistering(true)}>Register endpoint</button></Can>
    </>}>
      <ServerTable<O1Endpoint> path={PATHS.endpoints} query={{ health_status: health || undefined }} rowKey={(e) => e.endpointId} empty="No managed elements registered." columns={[
        { header: "Managed element", render: (e) => <strong>{e.managedElementRef}</strong> },
        { header: "Adaptor", render: (e) => <code className="small">{e.adaptorUri}</code> },
        { header: "Protocols", render: (e) => e.protocolSupport.join(", ") },
        { header: "Region / tenant", render: (e) => <span title="Set when the element is registered; a caller scoped to other regions or tenants cannot touch it">{describePlace(e)}</span> },
        { header: "Health", render: (e) => <StateBadge state={e.healthStatus} /> },
        { header: "Last heartbeat", render: (e) => formatTime(e.lastHeartbeatAt) },
        { header: "", className: "actions", render: (e) => <ActionButton label="Heartbeat" title="Simulates the ME's O1 adaptor heartbeat (DISCOVERED/DEGRADED → ACTIVE)"
          action={{ method: "POST", path: `${PATHS.endpoints}/${e.endpointId}/heartbeat`, success: `${e.managedElementRef} heartbeat` }} /> },
      ]} />
      {registering && <RegisterEndpoint onClose={() => setRegistering(false)} />}
    </Card>
  );
}

/** The register-endpoint form; a blank region or tenant is sent as null. */
function RegisterEndpoint({ onClose }: { onClose: () => void }) {
  const [f, setF] = useState({ managedElementRef: "", adaptorUri: "http://mock-o1-adaptor:8000/edit-config", protocolSupport: "NETCONF", o1Protocol: "NETCONF", entityType: "O-DU", vendorName: "", managedFunctionRef: "", region: "", tenant: "" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const action = useSmoAction();
  return (
    <Modal title="Register O1 adaptor endpoint" onClose={onClose}>
      <form className="form grid cols-2 tight" onSubmit={(e) => {
        e.preventDefault();
        action.mutate({ method: "POST", path: PATHS.endpoints, success: "Endpoint registered",
          json: { ...f, protocolSupport: splitList(f.protocolSupport), vendorName: f.vendorName || null, managedFunctionRef: f.managedFunctionRef || null, region: f.region.trim() || null, tenant: f.tenant.trim() || null } }, { onSuccess: onClose });
      }}>
        <Field label="Managed element ref"><input value={f.managedElementRef} onChange={set("managedElementRef")} required placeholder="ME-1" /></Field>
        <Field label="Entity type"><select value={f.entityType} onChange={set("entityType")}>{["O-DU", "O-CU-CP", "O-CU-UP", "O-RU"].map((t) => <option key={t}>{t}</option>)}</select></Field>
        <Field label="Adaptor URI"><input value={f.adaptorUri} onChange={set("adaptorUri")} required /></Field>
        <Field label="O1 protocol" hint="RESTCONF MEs are registered but CM writes to them are rejected (not implemented)"><select value={f.o1Protocol} onChange={set("o1Protocol")}><option>NETCONF</option><option>RESTCONF</option></select></Field>
        <Field label="Protocols supported"><input value={f.protocolSupport} onChange={set("protocolSupport")} /></Field>
        <Field label="Vendor"><input value={f.vendorName} onChange={set("vendorName")} /></Field>
        <Field label="Managed function ref"><input value={f.managedFunctionRef} onChange={set("managedFunctionRef")} /></Field>
        <Field label="Region" hint="Optional. A caller scoped to regions may touch only elements whose region it names"><input value={f.region} onChange={set("region")} placeholder="eu-west" maxLength={100} /></Field>
        <Field label="Tenant" hint="Optional. Likewise for tenants"><input value={f.tenant} onChange={set("tenant")} placeholder="acme" maxLength={100} /></Field>
        <div className="row gap end span-2"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn primary" disabled={action.isPending}>Register</button></div>
      </form>
    </Modal>
  );
}
