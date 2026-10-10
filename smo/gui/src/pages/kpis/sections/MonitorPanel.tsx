/** Section `kpis.monitor`: one assurance monitor's panel. Evaluate its thresholds against metrics the operator enters (`POST
 * /sa-smos/monitors/{id}/evaluate`, the breaches come back inline), execute a remedial action (CONFIG_CHANGE, SCALE, RECONNECT, ROLLBACK; what
 * each does depends on the monitor's scope), or escalate to an operator with a reason. All role-gated. Shows how many remedial actions the monitor
 * has taken (the true count, `total` of its actions). */
import { useState } from "react";

import { useSmoAction } from "../../../api/hooks";
import type { Monitor } from "../../../api/types";
import { ActionButton, Can, Card, Field, Id } from "../../../components/ui";
import { parseJsonObject } from "../../../lib/domain";
import { monitorPath, useMonitorActionCount } from "../data/queries";

/** The panel. */
export function MonitorPanel({ monitor, onClose }: { monitor: Monitor; onClose: () => void }) {
  const [metrics, setMetrics] = useState(JSON.stringify(Object.fromEntries(Object.keys(monitor.thresholds).map((k) => [k, 0])), null, 0));
  const [breaches, setBreaches] = useState<Record<string, number> | null>(null);
  const [actionType, setActionType] = useState("CONFIG_CHANGE");
  const [reason, setReason] = useState("");
  const evaluate = useSmoAction();
  const taken = useMonitorActionCount(monitor.monitorId);
  const parsed = parseJsonObject(metrics);
  const base = monitorPath(monitor.monitorId);
  return (
    <Card section="kpis.monitor" title={<>Monitor <Id value={monitor.monitorId} /></>}
      sub={taken.data ? `${taken.data.total ?? taken.data.items.length} remedial action${(taken.data.total ?? 0) === 1 ? "" : "s"} taken` : undefined}
      actions={<button type="button" className="btn ghost" onClick={onClose}>Close</button>}>
      <div className="grid cols-3">
        <div>
          <h3>Evaluate thresholds</h3>
          <Field label="Current metrics (JSON)" hint={parsed.ok ? undefined : <span className="text-bad">{parsed.error}</span>}><textarea rows={3} value={metrics} onChange={(e) => setMetrics(e.target.value)} spellCheck={false} /></Field>
          <Can method="POST" path={`${base}/evaluate`}>
            <button type="button" className="btn primary" disabled={!parsed.ok || evaluate.isPending} onClick={() => parsed.ok && evaluate.mutate({ method: "POST", path: `${base}/evaluate`, json: parsed.value },
              { onSuccess: (d) => setBreaches((d as { breaches: Record<string, number> }).breaches) })}>Evaluate</button>
          </Can>
          {breaches && (Object.keys(breaches).length === 0 ? <p className="text-ok">No breaches.</p> : <p className="text-bad">Breached: {Object.entries(breaches).map(([k, v]) => `${k} < ${v}`).join(", ")}</p>)}
        </div>
        <div>
          <h3>Remedial action</h3>
          {monitor.targetCoordinationGroupId && <p className="muted small">Group-scoped: any action type dispatches a group retrain via AI/ML Workflow.</p>}
          <Field label="Action type" hint={monitor.targetCoordinationGroupId ? undefined : actionType === "ROLLBACK" ? (monitor.targetRappInstanceId ? "Upgrades the rApp back to its previous version (rApp Management's version history); escalated if there is none." : "Needs a rApp-instance-scoped monitor: only rApp Management keeps a version history (409 ROLLBACK_HISTORY_UNAVAILABLE).") : actionType === "SCALE" ? "Always escalates in Phase 1 (NFO scale is a stub)." : undefined}>
            <select value={actionType} onChange={(e) => setActionType(e.target.value)}>{["CONFIG_CHANGE", "SCALE", "RECONNECT", "ROLLBACK"].map((t) => <option key={t}>{t}</option>)}</select>
          </Field>
          <ActionButton label="Execute" tone="primary" action={{ method: "POST", path: `${base}/remedial-actions`, query: { action_type: actionType }, success: `${actionType} dispatched` }} />
        </div>
        <div>
          <h3>Escalate to operator</h3>
          <Field label="Reason"><input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="needs manual intervention" /></Field>
          <ActionButton label="Escalate" tone="danger" disabled={!reason} action={{ method: "POST", path: `${base}/escalate`, query: { reason }, success: "Escalated" }} />
        </div>
      </div>
    </Card>
  );
}
