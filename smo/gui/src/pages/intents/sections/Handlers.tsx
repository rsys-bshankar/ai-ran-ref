/** Section `intents.handlers` (Handlers tab): the registered intent handlers (RMIH) as a server-paged table with their supported object types,
 * target names, scope and callback, a role-gated Deregister, and the admin-only "register a handler on the framework's behalf" tool. Handlers are
 * framework-internal (SO SMOS / SA SMOS, D-SEC-POLICY-1): Intent Service refuses an rApp id (a UUID). */
import { useState } from "react";

import type { Rmih } from "../../../api/types";
import { ActionButton, Can, Card, Field } from "../../../components/ui";
import { splitList } from "../../../lib/domain";
import { ServerTable } from "../../../kit/ServerTable";
import { HANDLERS } from "../data/queries";

/** The tab. */
export function Handlers() {
  return (
    <Card section="intents.handlers" title="Registered intent handlers" actions={<span className="muted small">Framework-internal only (SO SMOS / SA SMOS) — D-SEC-POLICY-1</span>}>
      <ServerTable<Rmih> path={HANDLERS} rowKey={(h) => h.rmihId} empty="No intent handlers registered." columns={[
        { header: "RMIH", render: (h) => <strong>{h.rmihId}</strong> },
        { header: "Supported object types", render: (h) => (h.attributes?.intentHandlingCapabilityList ?? []).map((c) => c.supportedExpectationObjectType).join(", ") },
        { header: "Supported targets", render: (h) => [...new Set((h.attributes?.intentHandlingCapabilityList ?? []).flatMap((c) => c.supportedExpectationTargetInfoList.map((t) => t.supportedTargetName)))].join(", ") || "—" },
        { header: "Scope", render: (h) => h.intentHandlingScope?.join(", ") ?? "any" },
        { header: "Callback", render: (h) => <code className="small">{h.notificationDestination}</code> },
        { header: "", className: "actions", render: (h) => <ActionButton label="Deregister" tone="danger" confirm={`Deregister ${h.rmihId}?`}
          action={{ method: "DELETE", path: `${HANDLERS}/${h.rmihId}`, success: "Handler deregistered" }} /> },
      ]} />
      <Can method="POST" path={HANDLERS}><RegisterHandler /></Can>
    </Card>
  );
}

/** The admin tool: one capability per object type, each supporting the listed target names. */
function RegisterHandler() {
  const [f, setF] = useState({ rmihId: "so-smos", smeServiceId: "so-smos-intent-handler", callback: "http://so-smos:8000/intents", types: "RAN_SUBNETWORK", targetNames: "RANEnergyConsumption", scope: "RAN" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const uuidLike = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(f.rmihId);
  return (
    <details className="admin-tools">
      <summary>Admin: register a handler on the framework's behalf</summary>
      <p className="muted small">An rApp identity (a UUID) is always refused: only SMO modules such as <code>so-smos</code> / <code>sa-smos</code> may hold an rmihId.</p>
      <div className="form grid cols-3 tight">
        <Field label="RMIH ID" hint={uuidLike ? <span className="text-bad">an rApp id — Intent Service will refuse it</span> : undefined}><input value={f.rmihId} onChange={set("rmihId")} /></Field>
        <Field label="SME service ID"><input value={f.smeServiceId} onChange={set("smeServiceId")} /></Field>
        <Field label="Notification callback"><input value={f.callback} onChange={set("callback")} /></Field>
        <Field label="Supported expectation object types" hint="Comma-separated: RAN_SUBNETWORK, EDGE_SERVICE_SUPPORT, 5GC_SUBNETWORK, RADIO_SERVICE"><input value={f.types} onChange={set("types")} /></Field>
        <Field label="Supported target names" hint="Comma-separated, e.g. RANEnergyConsumption, AveDLPrbLoad — an Intent's targets must be among them"><input value={f.targetNames} onChange={set("targetNames")} /></Field>
        <Field label="Handling scope"><select value={f.scope} onChange={set("scope")}><option value="">any</option><option>RAN</option><option>CN</option></select></Field>
      </div>
      <ActionButton label="Register handler" disabled={!f.rmihId || !f.types} action={{
        method: "POST", path: HANDLERS, success: "Handler registered",
        json: { rmihId: f.rmihId, smeServiceId: f.smeServiceId, notificationDestination: f.callback,
          intentHandlingCapabilityList: splitList(f.types).map((t) => ({
            intentHandlingCapabilityId: `cap-${t}`, supportedExpectationObjectType: t,
            supportedExpectationTargetInfoList: splitList(f.targetNames).map((n) => ({ supportedTargetName: n })),
          })),
          intentHandlingScope: f.scope ? [f.scope] : null },
      }} />
    </details>
  );
}
