/** Section `kpis.newMonitor` (Assurance tab, role-gated by the caller): register an SA SMOS assurance monitor with metric floors, scoped to an
 * SO SMOS order (remediates NF deployments), a model coordination group (retrains models) or an rApp instance (can roll the rApp back), or
 * unscoped. Reads the three scope lists (bounded at 200) only while the form is shown. */
import { useState } from "react";

import { ActionButton, Card, Field } from "../../../components/ui";
import { parseJsonObject } from "../../../lib/domain";
import { MONITORS, useCoordinationGroups, useInstances, useOrders } from "../data/queries";

/** The form. */
export function RegisterMonitor() {
  const orders = useOrders();
  const groups = useCoordinationGroups();
  const instances = useInstances();
  const [target, setTarget] = useState("");
  const [thresholds, setThresholds] = useState('{"throughputMbps": 100}');
  const parsed = parseJsonObject(thresholds);
  const [kind, id] = target.split(":");
  return (
    <Card section="kpis.newMonitor" title="Register assurance monitor">
      <div className="form inline">
        <Field label="Scope" hint="Order-scoped monitors remediate NF deployments; group-scoped ones retrain models; rApp-scoped ones can roll the rApp back">
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Unscoped</option>
            <optgroup label="SO SMOS orders">{orders.data?.map((o) => <option key={o.orderId} value={`order:${o.orderId}`}>{o.scope} ({o.orderId.slice(0, 8)})</option>)}</optgroup>
            <optgroup label="Model coordination groups">{groups.data?.map((g) => <option key={g.groupId} value={`group:${g.groupId}`}>group {g.groupId.slice(0, 8)}</option>)}</optgroup>
            <optgroup label="rApp instances">{instances.data?.filter((i) => i.state !== "UNDEPLOYED").map((i) => <option key={i.instanceId} value={`rapp:${i.instanceId}`}>instance {i.instanceId.slice(0, 8)} ({i.state})</option>)}</optgroup>
          </select>
        </Field>
        <Field label="Thresholds — metric floors (JSON)" hint={parsed.ok ? undefined : <span className="text-bad">{parsed.error}</span>}><input value={thresholds} onChange={(e) => setThresholds(e.target.value)} /></Field>
        <ActionButton label="Register" tone="primary" disabled={!parsed.ok} action={{
          method: "POST", path: MONITORS, json: parsed.ok ? parsed.value : {},
          query: { target_order_id: kind === "order" ? id : undefined, target_coordination_group_id: kind === "group" ? id : undefined,
                   target_rapp_instance_id: kind === "rapp" ? id : undefined },
          success: "Monitor registered",
        }} />
      </div>
    </Card>
  );
}
