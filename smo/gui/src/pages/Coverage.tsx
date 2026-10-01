import { useState } from "react";

import { useSmo } from "../api/hooks";
import type { CcoCell, CcoDashboard, CcoDecision, CcoInstance, CcoSetting, CcoShares } from "../api/types";
import { Sparkline } from "../components/charts";
import { ActionButton, Card, DataTable, Drawer, Id, Json, KeyValue, PageHeader, StateBadge } from "../components/ui";

// Wave 10.3 (W10.3-09): the Coverage Optimization rApp's operator dashboard.
// For every cell and every execution: the weak-coverage / overshoot /
// pilot-pollution shares, the joint plan and its predicted gain, the safety
// evaluation, the decision, intent, action, verification, KPI check and the
// tilt and power now in force.

const BASE = "/coverage-optimization-rapp/instances";

export function Coverage() {
  const instances = useSmo<CcoInstance[]>(BASE);
  const [selected, setSelected] = useState("");
  const instanceId = selected || instances.data?.[0]?.instanceId || "";
  return (
    <>
      <PageHeader title="Coverage" subtitle="Coverage Optimization rApp — tunes a cluster's tilt and power jointly from weak coverage, overshoot and pilot pollution (Wave 10.3)"
        actions={<select value={instanceId} onChange={(e) => setSelected(e.target.value)} aria-label="rApp instance">
          {(instances.data ?? []).map((i) => <option key={i.instanceId} value={i.instanceId}>{i.managedElementRef} · {i.autonomyMode} · {i.instanceId.slice(0, 8)}</option>)}
        </select>} />
      {instances.data && instances.data.length === 0 && <Card><p className="muted">No Coverage Optimization rApp instance is running. See DEMO_RUNBOOK.md §26, Demo 00–11.</p></Card>}
      {instanceId && <InstanceDashboard instanceId={instanceId} />}
    </>
  );
}

function setting(s: CcoSetting | null | undefined) {
  if (!s) return "—";
  return `${s.digitalTilt != null ? (s.digitalTilt / 10).toFixed(1) : "?"}° · ${s.configuredMaxTxPower ?? "?"} dBm`;
}

function InstanceDashboard({ instanceId }: { instanceId: string }) {
  const dash = useSmo<CcoDashboard>(`${BASE}/${instanceId}/dashboard`);
  const [cell, setCell] = useState<CcoCell | null>(null);
  const inst = dash.data?.instance;
  const plan = dash.data?.cells.map((c) => c.latestDecision?.prediction?.plan).find(Boolean);
  return (
    <>
      {inst && <Card title="Instance" actions={<div className="row gap">
        <ActionButton label="Evaluate now" tone="primary" action={{ method: "POST", path: `${BASE}/${instanceId}/evaluate`, success: "Closed-loop pass complete" }} />
        <ActionButton label="Reconcile approvals" action={{ method: "POST", path: `${BASE}/${instanceId}/reconcile`, success: "ASSIST dispatches reconciled" }} />
      </div>}>
        <KeyValue items={[
          ["Managed element", inst.managedElementRef], ["Autonomy mode", <StateBadge key="m" state={inst.autonomyMode} />],
          ["Actuators", `CommonBeamformingFunction.digitalTilt (baseline ${(inst.baselineTilt / 10).toFixed(1)}° ± 4°) · NRSectorCarrier.configuredMaxTxPower (baseline ${inst.baselinePower} dBm ± 3 dB)`],
          ["Model", inst.modelId ? <span key="mo"><Id value={inst.modelId} /> v{inst.modelVersion} (artifact {inst.artifactVersion})</span> : <span className="muted">not trained</span>],
          ["Change set under review", inst.observing ? `${Object.entries(inst.observing.cells).map(([c, x]) => `${c} ${x.move}`).join(", ")} — objective before ${inst.observing.preObjective}, predicted ${inst.observing.predictedObjective}` : <span className="muted">none</span>],
          ["Coordination", [inst.energySavingInstanceId && "EnergySaving", inst.mobilityInstanceId && "Mobility"].filter(Boolean).join(" · ") || <span className="muted">O1 state only</span>],
        ]} />
      </Card>}
      {plan && <Card title="Latest joint plan">
        <KeyValue items={[
          ["Moves", Object.keys(plan.moves).length ? Object.entries(plan.moves).map(([c, m]) => `${c} ${m} (${plan.drivers[c]})`).join(" · ") : <span className="muted">none — nothing worth its cost</span>],
          ["Cluster objective", `${plan.objectiveBefore} → ${plan.objectiveAfter} (gain ${plan.gain})`],
        ]} />
      </Card>}
      <Card title="Cells" actions={<span className="muted small">Objective: each share's excess over 5 %, summed; at most 2 cells move per pass</span>}>
        <DataTable rows={dash.data?.cells} loading={dash.isLoading} error={dash.error} rowKey={(c) => c.cellId}
          onRowClick={setCell} empty="No managed cells." columns={[
            { header: "Cell", render: (c) => <strong>{c.cellId}</strong> },
            { header: "State", render: (c) => <StateBadge state={c.state} /> },
            { header: "Tilt · power", render: (c) => setting(c) },
            { header: "Excess", render: (c) => <Sparkline points={c.excessTrend} width={140} height={36} floor={0} /> },
            { header: "Shares", render: (c) => <Shares s={c.latestDecision?.shares} /> },
            { header: "Safety", render: (c) => <Safety d={c.latestDecision} /> },
            { header: "Decision", render: (c) => c.latestDecision ? <span><StateBadge state={c.latestDecision.decision} /> <span className="small muted">{c.latestDecision.reason}</span></span> : "—" },
            { header: "Intent → action → verification", render: (c) => <Execution d={c.latestDecision} /> },
          ]} />
      </Card>
      {cell && <CellDrawer instanceId={instanceId} cell={cell} onClose={() => setCell(null)} />}
    </>
  );
}

function Shares({ s }: { s: CcoShares | null | undefined }) {
  if (!s) return <span className="muted">—</span>;
  return <span className="small">weak {s.WEAK_COVERAGE} % · over {s.OVERSHOOT} % · poll {s.PILOT_POLLUTION} %</span>;
}

function Safety({ d }: { d: CcoDecision | null }) {
  if (!d?.safety) return <span className="muted">—</span>;
  if (d.safety.passed) return <span className="text-ok small">all guards pass</span>;
  return <span className="text-bad small">{d.safety.blocks.map((b) => `${b.guard} (${b.level})`).join(", ")}</span>;
}

function Execution({ d }: { d: CcoDecision | null }) {
  if (!d) return <span className="muted">—</span>;
  const parts = [
    d.toSetting && `${setting(d.fromSetting)} → ${setting(d.toSetting)}`,
    d.intent && `intent ${d.intent.status}`, d.action && `action ${d.action.status}`,
    d.verification && d.verification.result, d.kpi && `KPI ${d.kpi.verdict}`, d.rollback && `rollback ${d.rollback.result}`,
  ].filter(Boolean);
  return <span className="small"><StateBadge state={d.outcome} />{parts.length > 0 && <span className="muted"> {parts.join(" → ")}</span>}</span>;
}

function CellDrawer({ instanceId, cell, onClose }: { instanceId: string; cell: CcoCell; onClose: () => void }) {
  const history = useSmo<CcoDecision[]>(`${BASE}/${instanceId}/decisions`, { cell_id: cell.cellId, limit: 20 });
  const series = (k: keyof CcoShares) => cell.shareTrend.map((p) => ({ t: p.t, v: p[k] }));
  return (
    <Drawer title={`Cell ${cell.cellId}`} onClose={onClose}>
      <Sparkline points={series("WEAK_COVERAGE")} width={420} height={60} floor={0} label="Weak coverage (%)" />
      <Sparkline points={series("OVERSHOOT")} width={420} height={60} floor={0} label="Overshoot (%)" />
      <Sparkline points={series("PILOT_POLLUTION")} width={420} height={60} floor={0} label="Pilot pollution (%)" />
      <h3>Latest execution (audit trail)</h3>
      {cell.latestDecision ? <Json value={cell.latestDecision} /> : <p className="muted">No decision yet.</p>}
      <h3>History</h3>
      <DataTable rows={history.data} loading={history.isLoading} error={history.error} rowKey={(d) => d.decisionId} empty="No decisions." columns={[
        { header: "Observed", render: (d) => d.observedAt?.replace("T", " ").slice(0, 16) ?? "—" },
        { header: "Decision", render: (d) => <StateBadge state={d.decision} /> },
        { header: "Setting", render: (d) => d.toSetting ? `${setting(d.fromSetting)} → ${setting(d.toSetting)}` : "—" },
        { header: "Reason", render: (d) => <span className="small">{d.reason}</span> },
        { header: "Outcome", render: (d) => <StateBadge state={d.outcome} /> },
        { header: "Execution", render: (d) => <Id value={d.executionId} /> },
      ]} />
    </Drawer>
  );
}
