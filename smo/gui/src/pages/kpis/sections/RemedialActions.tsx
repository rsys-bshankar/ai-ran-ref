/** Section `kpis.actions` (Assurance tab): SA SMOS remedial actions as a server-paged table filtered by outcome on the server (`outcome`;
 * ESCALATED = handed to an operator), with the action's monitor, what that monitor's scope makes the action do, its type, whether it ran on its
 * own, and its outcome. The scope is looked up in one bounded read of the monitors (≤ 500). */
import { useState } from "react";

import type { RemedialAction } from "../../../api/types";
import { Card, Id, StateBadge } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { REMEDIAL_ACTIONS, useMonitorIndex } from "../data/queries";

/** The outcomes the filter offers. */
const OUTCOMES = ["RESOLVED", "ESCALATED"];

/** The box. */
export function RemedialActions() {
  const [outcome, setOutcome] = useState("");
  const monitors = useMonitorIndex();
  const scopeOf = (a: RemedialAction) => {
    const m = monitors.data?.items.find((x) => x.monitorId === a.monitorId);
    return m?.targetCoordinationGroupId ? "model group (retrain)" : m?.targetOrderId ? "service order" : m?.targetRappInstanceId ? "rApp instance" : "—";
  };
  return (
    <Card section="kpis.actions" title="Remedial actions" actions={<>
      <span className="muted small">ESCALATED = handed to an operator</span>
      <select value={outcome} onChange={(e) => setOutcome(e.target.value)} aria-label="Outcome"><option value="">All outcomes</option>{OUTCOMES.map((o) => <option key={o}>{o}</option>)}</select>
    </>}>
      <ServerTable<RemedialAction> path={REMEDIAL_ACTIONS} query={{ outcome: outcome || undefined }} rowKey={(a) => a.actionId} empty="No remedial actions." columns={[
        { header: "Action", render: (a) => <Id value={a.actionId} /> },
        { header: "Monitor", render: (a) => <Id value={a.monitorId} /> },
        { header: "Scope", render: scopeOf },
        { header: "Type", render: (a) => a.actionType },
        { header: "Auto-executed", render: (a) => (a.autoExecuted ? "yes" : "no") },
        { header: "Outcome", render: (a) => <StateBadge state={a.outcome} /> },
      ]} />
    </Card>
  );
}
