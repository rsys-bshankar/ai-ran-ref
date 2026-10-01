import { useState } from "react";

import { useSmo } from "../api/hooks";
import type { TsCell, TsDashboard, TsDecision, TsInstance, TsSteering } from "../api/types";
import { Sparkline } from "../components/charts";
import { ActionButton, Card, DataTable, Drawer, Id, Json, KeyValue, PageHeader, StateBadge } from "../components/ui";

// Wave 10.4 (W10.4-09): the Traffic Steering rApp's operator dashboard. For
// every cell and every execution: the congestion score and its forecast,
// the pairwise plan, the safety evaluation and excluded targets, the
// decision, intent, action, verification, KPI check and the steering in
// force (idle reselection priority and connected CIO).

const BASE = "/traffic-steering-rapp/instances";

export function TrafficSteering() {
  const instances = useSmo<TsInstance[]>(BASE);
  const [selected, setSelected] = useState("");
  const instanceId = selected || instances.data?.[0]?.instanceId || "";
  return (
    <>
      <PageHeader title="Traffic Steering" subtitle="Traffic Steering rApp — moves load off congested cells: idle UEs by reselection priority, connected UEs by CIO (Wave 10.4)"
        actions={<select value={instanceId} onChange={(e) => setSelected(e.target.value)} aria-label="rApp instance">
          {(instances.data ?? []).map((i) => <option key={i.instanceId} value={i.instanceId}>{i.managedElementRef} · {i.autonomyMode} · {i.instanceId.slice(0, 8)}</option>)}
        </select>} />
      {instances.data && instances.data.length === 0 && <Card><p className="muted">No Traffic Steering rApp instance is running. See DEMO_RUNBOOK.md §27, Demo 00–11.</p></Card>}
      {instanceId && <InstanceDashboard instanceId={instanceId} />}
    </>
  );
}

function steering(s: TsSteering | null | undefined) {
  const parts = [
    ...Object.entries(s?.prio ?? {}).map(([layer, v]) => `idle → ${layer} +${v}`),
    ...Object.entries(s?.cio ?? {}).map(([t, v]) => `CIO → ${t} +${v} dB`),
  ];
  return parts.length ? parts.join(" · ") : <span className="muted">none</span>;
}

function InstanceDashboard({ instanceId }: { instanceId: string }) {
  const dash = useSmo<TsDashboard>(`${BASE}/${instanceId}/dashboard`);
  const [cell, setCell] = useState<TsCell | null>(null);
  const inst = dash.data?.instance;
  return (
    <>
      {inst && <Card title="Instance" actions={<div className="row gap">
        <ActionButton label="Evaluate now" tone="primary" action={{ method: "POST", path: `${BASE}/${instanceId}/evaluate`, success: "Closed-loop pass complete" }} />
        <ActionButton label="Reconcile approvals" action={{ method: "POST", path: `${BASE}/${instanceId}/reconcile`, success: "ASSIST dispatches reconciled" }} />
      </div>}>
        <KeyValue items={[
          ["Managed element", inst.managedElementRef], ["Autonomy mode", <StateBadge key="m" state={inst.autonomyMode} />],
          ["Layers", Object.entries(inst.cells.reduce<Record<string, string[]>>((acc, c) => ({ ...acc, [c.layer]: [...(acc[c.layer] ?? []), c.cellId] }), {}))
            .map(([layer, cells]) => `${layer}: ${cells.join(", ")}`).join(" · ")],
          ["Actuators", `NRFreqRelation.cellReselectionPriority (baseline ${inst.baselinePriority} ± 2) · NRCellRelation.cellIndividualOffset (shared with Mobility, baseline ${inst.baselineCio} ± 6 dB)`],
          ["Model", inst.modelId ? <span key="mo"><Id value={inst.modelId} /> v{inst.modelVersion} (artifact {inst.artifactVersion})</span> : <span className="muted">not trained</span>],
          ["Coordination", [inst.energySavingInstanceId && "EnergySaving", inst.mobilityInstanceId && "Mobility", inst.coverageInstanceId && "Coverage"].filter(Boolean).join(" · ") || <span className="muted">O1 state only</span>],
          ["Recent steering (6 h)", inst.steeringLog.length ? inst.steeringLog.map((e) => `${e.source} → ${e.targets.join(",")}`).join(" · ") : <span className="muted">none</span>],
        ]} />
      </Card>}
      <Card title="Cells" actions={<span className="muted small">Steer at a forecast of 70 or more, hold 50–70, release below 50; no target above 55</span>}>
        <DataTable rows={dash.data?.cells} loading={dash.isLoading} error={dash.error} rowKey={(c) => c.cellId}
          onRowClick={setCell} empty="No managed cells." columns={[
            { header: "Cell", render: (c) => <span><strong>{c.cellId}</strong> <span className="small muted">{c.layer}</span></span> },
            { header: "State", render: (c) => <StateBadge state={c.state} /> },
            { header: "Congestion", render: (c) => <Sparkline points={c.scoreTrend} width={140} height={36} floor={0} /> },
            { header: "Forecast", render: (c) => <Forecast d={c.latestDecision} /> },
            { header: "Steering in force", render: (c) => <span className="small">{steering(c.steering)}</span> },
            { header: "Safety", render: (c) => <Safety d={c.latestDecision} /> },
            { header: "Decision", render: (c) => c.latestDecision ? <span><StateBadge state={c.latestDecision.decision} /> <span className="small muted">{c.latestDecision.reason}</span></span> : "—" },
            { header: "Intent → action → verification", render: (c) => <Execution d={c.latestDecision} /> },
          ]} />
      </Card>
      {cell && <CellDrawer instanceId={instanceId} cell={cell} onClose={() => setCell(null)} />}
    </>
  );
}

function Forecast({ d }: { d: TsDecision | null }) {
  const m = d?.prediction?.model;
  if (!m) return <span className="muted">—</span>;
  return <span className="small">{m.score} → <strong>{m.forecast}</strong> <StateBadge state={m.band} /></span>;
}

function Safety({ d }: { d: TsDecision | null }) {
  if (!d?.safety) return <span className="muted">—</span>;
  if (!d.safety.passed) return <span className="text-bad small">{d.safety.blocks.map((b) => `${b.guard} (${b.level})`).join(", ")}</span>;
  const excluded = d.safety.excluded ?? [];
  return excluded.length
    ? <span className="small muted">excluded: {excluded.map((e) => `${e.target ?? e.layer} ${e.reason}`).join(", ")}</span>
    : <span className="text-ok small">all guards pass</span>;
}

function Execution({ d }: { d: TsDecision | null }) {
  if (!d) return <span className="muted">—</span>;
  const parts = [
    d.managedRef && d.toValue != null && `${d.managedRef} ${d.fromValue} → ${d.toValue}`,
    d.intent && `intent ${d.intent.status}`, d.action && `action ${d.action.status}`,
    d.verification && d.verification.result, d.kpi && `KPI ${d.kpi.verdict}`, d.rollback && `rollback ${d.rollback.result}`,
  ].filter(Boolean);
  return <span className="small"><StateBadge state={d.outcome} />{parts.length > 0 && <span className="muted"> {parts.join(" → ")}</span>}</span>;
}

function CellDrawer({ instanceId, cell, onClose }: { instanceId: string; cell: TsCell; onClose: () => void }) {
  const history = useSmo<TsDecision[]>(`${BASE}/${instanceId}/decisions`, { cell_id: cell.cellId, limit: 20 });
  return (
    <Drawer title={`Cell ${cell.cellId} (${cell.layer})`} onClose={onClose}>
      <Sparkline points={cell.scoreTrend} width={420} height={90} floor={0} label="Congestion score" />
      <h3>Latest execution (audit trail)</h3>
      {cell.latestDecision ? <Json value={cell.latestDecision} /> : <p className="muted">No decision yet.</p>}
      <h3>History</h3>
      <DataTable rows={history.data} loading={history.isLoading} error={history.error} rowKey={(d) => d.decisionId} empty="No decisions." columns={[
        { header: "Observed", render: (d) => d.observedAt?.replace("T", " ").slice(0, 16) ?? "—" },
        { header: "Score", render: (d) => d.score ?? "—" },
        { header: "Decision", render: (d) => <StateBadge state={d.decision} /> },
        { header: "Change", render: (d) => d.managedRef && d.toValue != null ? `${d.managedRef} ${d.fromValue} → ${d.toValue}` : "—" },
        { header: "Reason", render: (d) => <span className="small">{d.reason}</span> },
        { header: "Outcome", render: (d) => <StateBadge state={d.outcome} /> },
        { header: "Execution", render: (d) => <Id value={d.executionId} /> },
      ]} />
    </Drawer>
  );
}
