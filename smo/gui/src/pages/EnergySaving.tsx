import { useState } from "react";

import { useSmo } from "../api/hooks";
import type { EsCell, EsDashboard, EsDecision, EsInstance } from "../api/types";
import { Sparkline } from "../components/charts";
import { ActionButton, Can, Card, DataTable, Drawer, Id, Json, KeyValue, PageHeader, StateBadge } from "../components/ui";

// Wave 10.1 (W10-24): the EnergySaving rApp's operator dashboard. For every
// cell and every execution: PRB trend, prediction, safety evaluation,
// decision, intent, action, verification, rollback and the final cell state
// (Wave 10 operator acceptance criteria, §19).

const BASE = "/energy-saving-rapp/instances";

export function EnergySaving() {
  const instances = useSmo<EsInstance[]>(BASE);
  const [selected, setSelected] = useState("");
  const instanceId = selected || instances.data?.[0]?.instanceId || "";
  return (
    <>
      <PageHeader title="Energy Saving" subtitle="EnergySaving_rApp — sleeps lightly used cells through O1 and wakes them before load returns (Wave 10.1)"
        actions={<select value={instanceId} onChange={(e) => setSelected(e.target.value)} aria-label="rApp instance">
          {(instances.data ?? []).map((i) => <option key={i.instanceId} value={i.instanceId}>{i.managedElementRef} · {i.autonomyMode} · {i.instanceId.slice(0, 8)}</option>)}
        </select>} />
      {instances.data && instances.data.length === 0 && <Card><p className="muted">No EnergySaving rApp instance is running. See DEMO_RUNBOOK.md §24, Demo 00–11.</p></Card>}
      {instanceId && <InstanceDashboard instanceId={instanceId} />}
    </>
  );
}

function InstanceDashboard({ instanceId }: { instanceId: string }) {
  const dash = useSmo<EsDashboard>(`${BASE}/${instanceId}/dashboard`);
  const [cell, setCell] = useState<EsCell | null>(null);
  const inst = dash.data?.instance;
  return (
    <>
      {inst && <Card title="Instance" actions={<div className="row gap">
        <ActionButton label="Evaluate now" tone="primary" action={{ method: "POST", path: `${BASE}/${instanceId}/evaluate`, success: "Closed-loop pass complete" }} />
        <ActionButton label="Reconcile approvals" action={{ method: "POST", path: `${BASE}/${instanceId}/reconcile`, success: "ASSIST dispatches reconciled" }} />
      </div>}>
        <KeyValue items={[
          ["Managed element", inst.managedElementRef], ["Autonomy mode", <StateBadge key="m" state={inst.autonomyMode} />],
          ["Actuator", inst.actuator === "ENERGY_SAVING_CONTROL" ? "CESManagementFunction.energySavingControl" : "NRCellDU.administrativeState"],
          ["Model", inst.modelId ? <span key="mo"><Id value={inst.modelId} /> v{inst.modelVersion} (artifact {inst.artifactVersion})</span> : <span className="muted">not trained</span>],
          ["Datasets", Object.entries(inst.datasets).map(([stage, d]) => `${stage}: ${d.dataset}`).join(" · ")],
        ]} />
      </Card>}
      <Card title="Cells" actions={<span className="muted small">PRB trend: last 48 samples; sleep below 5 %, wake above 15 %</span>}>
        <DataTable rows={dash.data?.cells} loading={dash.isLoading} error={dash.error} rowKey={(c) => c.cellId}
          onRowClick={setCell} empty="No managed cells." columns={[
            { header: "Cell", render: (c) => <strong>{c.cellId}</strong> },
            { header: "State", render: (c) => <><StateBadge state={c.state} />{c.overrideBy && <span className="small muted"> override: {c.overrideBy}</span>}</> },
            { header: "O1", render: (c) => c.o1Value ?? <span className="muted">—</span> },
            { header: "PRB trend", render: (c) => <Sparkline points={c.prbTrend} width={160} height={36} floor={0} /> },
            { header: "Prediction", render: (c) => <Prediction d={c.latestDecision} /> },
            { header: "Safety", render: (c) => <Safety d={c.latestDecision} /> },
            { header: "Decision", render: (c) => c.latestDecision ? <span><StateBadge state={c.latestDecision.decision} /> <span className="small muted">{c.latestDecision.reason}</span></span> : "—" },
            { header: "Intent → action → verification", render: (c) => <Execution d={c.latestDecision} /> },
            { header: "", className: "actions", render: (c) => <Can method="POST" path={`${BASE}/${instanceId}/cells/${c.cellId}/override`}>
              {c.overrideBy
                ? <ActionButton label="Clear override" action={{ method: "DELETE", path: `${BASE}/${instanceId}/cells/${c.cellId}/override`, success: "Override cleared" }} />
                : <ActionButton label="Override: unlock" tone="danger" confirm={`Unlock cell ${c.cellId} now and suppress AI recommendations for it?`}
                    action={{ method: "POST", path: `${BASE}/${instanceId}/cells/${c.cellId}/override`, success: "Cell unlocked by operator",
                              json: { operator: "smo-gui", reason: "manual override" } }} />}
            </Can> },
          ]} />
      </Card>
      {cell && <CellDrawer instanceId={instanceId} cell={cell} onClose={() => setCell(null)} />}
    </>
  );
}

function Prediction({ d }: { d: EsDecision | null }) {
  const m = d?.prediction?.model;
  if (!m) return <span className="muted">—</span>;
  return <span className="small">{d?.prb ?? "?"} % → <strong>{m.futurePrb} %</strong> <StateBadge state={m.recommendedState} /> <span className="muted">conf {m.confidence}</span>
    {d?.prediction?.mdafFuturePrb != null && <span className="muted"> · MDAF {d.prediction.mdafFuturePrb} %</span>}</span>;
}

function Safety({ d }: { d: EsDecision | null }) {
  if (!d?.safety) return <span className="muted">—</span>;
  if (d.safety.passed) return <span className="text-ok small">all guards pass</span>;
  return <span className="text-bad small">{d.safety.blocks.map((b) => `${b.guard} (${b.level})`).join(", ")}</span>;
}

function Execution({ d }: { d: EsDecision | null }) {
  if (!d) return <span className="muted">—</span>;
  const parts = [
    d.intent && `intent ${d.intent.status}`, d.action && `action ${d.action.status}`,
    d.verification && d.verification.result, d.rollback && `rollback ${d.rollback.result}`,
  ].filter(Boolean);
  return <span className="small"><StateBadge state={d.outcome} />{parts.length > 0 && <span className="muted"> {parts.join(" → ")}</span>}</span>;
}

function CellDrawer({ instanceId, cell, onClose }: { instanceId: string; cell: EsCell; onClose: () => void }) {
  const history = useSmo<EsDecision[]>(`${BASE}/${instanceId}/decisions`, { cell_id: cell.cellId, limit: 20 });
  return (
    <Drawer title={`Cell ${cell.cellId}`} onClose={onClose}>
      <Sparkline points={cell.prbTrend} width={420} height={90} floor={0} label="PRB utilisation (%)" />
      <h3>Latest execution (audit trail)</h3>
      {cell.latestDecision ? <Json value={cell.latestDecision} /> : <p className="muted">No decision yet.</p>}
      <h3>History</h3>
      <DataTable rows={history.data} loading={history.isLoading} error={history.error} rowKey={(d) => d.decisionId} empty="No decisions." columns={[
        { header: "Observed", render: (d) => d.observedAt?.replace("T", " ").slice(0, 16) ?? "—" },
        { header: "PRB", render: (d) => d.prb ?? "—" },
        { header: "Decision", render: (d) => <StateBadge state={d.decision} /> },
        { header: "Reason", render: (d) => <span className="small">{d.reason}</span> },
        { header: "Outcome", render: (d) => <StateBadge state={d.outcome} /> },
        { header: "Execution", render: (d) => <Id value={d.executionId} /> },
      ]} />
    </Drawer>
  );
}
