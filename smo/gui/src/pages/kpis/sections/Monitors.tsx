/** Section `kpis.monitors` (Overview and Assurance tabs): SA SMOS assurance monitors as a server-paged table (scope and metric floors); a click
 * opens the monitor panel (evaluate thresholds, execute a remedial action, escalate, with the count of actions it has taken). "Breaching first"
 * is a ⚠ gap: SA SMOS keeps no breach state on a monitor and its list has no such filter, so the table is in server order. */
import { useState } from "react";

import type { Monitor } from "../../../api/types";
import { Card, Id } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { MONITORS } from "../data/queries";
import { MonitorPanel } from "./MonitorPanel";

/** How a monitor's scope reads. */
export function monitorScope(m: Monitor) {
  return m.targetOrderId ? <>order <Id value={m.targetOrderId} /></> : m.targetCoordinationGroupId ? <>model group <Id value={m.targetCoordinationGroupId} /></>
    : m.targetRappInstanceId ? <>rApp instance <Id value={m.targetRappInstanceId} /></> : <span className="muted">unscoped</span>;
}

/** The table and the selected monitor's panel. */
export function Monitors({ compact }: { compact?: boolean }) {
  const [selected, setSelected] = useState<Monitor | null>(null);
  return (
    <>
      <Card section="kpis.monitors" title="Assurance monitors" sub="SA SMOS evaluates; SO SMOS remediates; escalates to you when it can't"
        actions={<span className="muted small">Click a monitor to evaluate, remediate or escalate</span>}>
        <ServerTable<Monitor> path={MONITORS} rowKey={(m) => m.monitorId} empty="No assurance monitors." onRowClick={setSelected} selectedKey={selected?.monitorId} columns={[
          { header: "Monitor", render: (m) => <Id value={m.monitorId} /> },
          { header: "Scope", render: monitorScope },
          { header: "Thresholds (floor)", render: (m) => <span className="mono">{Object.entries(m.thresholds).map(([k, v]) => `${k} ≥ ${v}`).join(", ") || "—"}</span> },
        ]} />
        {compact && <p className="gap-note">Not sorted breaching-first: SA SMOS keeps no breach state on a monitor.</p>}
      </Card>
      {selected && <MonitorPanel key={selected.monitorId} monitor={selected} onClose={() => setSelected(null)} />}
    </>
  );
}
