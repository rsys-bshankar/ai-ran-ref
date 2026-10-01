import { useState } from "react";

import { useSmo } from "../api/hooks";
import type { MroDashboard, MroDecision, MroInstance, MroRelation } from "../api/types";
import { Sparkline } from "../components/charts";
import { ActionButton, Card, DataTable, Drawer, Id, Json, KeyValue, PageHeader, StateBadge } from "../components/ui";

// Wave 10.2 (W10.2-09): the Mobility Optimization rApp's operator dashboard.
// For every neighbour relation and every execution: the handover failure-rate
// trend, the dominant failure class and prediction, the safety evaluation,
// the CIO decision, intent, action, verification, KPI check and the CIO now
// in force.

const BASE = "/mobility-optimization-rapp/instances";

export function Mobility() {
  const instances = useSmo<MroInstance[]>(BASE);
  const [selected, setSelected] = useState("");
  const instanceId = selected || instances.data?.[0]?.instanceId || "";
  return (
    <>
      <PageHeader title="Mobility" subtitle="Mobility Optimization rApp — tunes per-relation CIO from classified handover failures, within DMRO bounds (Wave 10.2)"
        actions={<select value={instanceId} onChange={(e) => setSelected(e.target.value)} aria-label="rApp instance">
          {(instances.data ?? []).map((i) => <option key={i.instanceId} value={i.instanceId}>{i.managedElementRef} · {i.autonomyMode} · {i.instanceId.slice(0, 8)}</option>)}
        </select>} />
      {instances.data && instances.data.length === 0 && <Card><p className="muted">No Mobility Optimization rApp instance is running. See DEMO_RUNBOOK.md §25, Demo 00–11.</p></Card>}
      {instanceId && <InstanceDashboard instanceId={instanceId} />}
    </>
  );
}

function InstanceDashboard({ instanceId }: { instanceId: string }) {
  const dash = useSmo<MroDashboard>(`${BASE}/${instanceId}/dashboard`);
  const [relation, setRelation] = useState<MroRelation | null>(null);
  const inst = dash.data?.instance;
  return (
    <>
      {inst && <Card title="Instance" actions={<div className="row gap">
        <ActionButton label="Evaluate now" tone="primary" action={{ method: "POST", path: `${BASE}/${instanceId}/evaluate`, success: "Closed-loop pass complete" }} />
        <ActionButton label="Reconcile approvals" action={{ method: "POST", path: `${BASE}/${instanceId}/reconcile`, success: "ASSIST dispatches reconciled" }} />
      </div>}>
        <KeyValue items={[
          ["Managed element", inst.managedElementRef], ["Autonomy mode", <StateBadge key="m" state={inst.autonomyMode} />],
          ["Actuator", `NRCellRelation.cellIndividualOffset (baseline ${inst.baselineCio} dB, ±6 dB)`],
          ["DMRO bounds", inst.dmroBounds ? Object.entries(inst.dmroBounds).map(([k, v]) => `${k}=${String(v)}`).join(" · ") : "—"],
          ["Model", inst.modelId ? <span key="mo"><Id value={inst.modelId} /> v{inst.modelVersion} (artifact {inst.artifactVersion})</span> : <span className="muted">not trained</span>],
          ["EnergySaving coordination", inst.energySavingInstanceId ? <Id value={inst.energySavingInstanceId} /> : <span className="muted">O1 state only</span>],
        ]} />
      </Card>}
      <Card title="Neighbour relations" actions={<span className="muted small">Failure rate: too-late + too-early + wrong-cell + ping-pong per attempt; act at ≥ 5 %, hold 2–5 %</span>}>
        <DataTable rows={dash.data?.relations} loading={dash.isLoading} error={dash.error} rowKey={(r) => r.relation}
          onRowClick={setRelation} empty="No managed relations." columns={[
            { header: "Relation", render: (r) => <strong>{r.source} → {r.target}</strong> },
            { header: "State", render: (r) => <StateBadge state={r.state} /> },
            { header: "CIO", render: (r) => r.cio != null ? `${r.cio} dB` : <span className="muted">—</span> },
            { header: "Failure rate", render: (r) => <Sparkline points={r.rateTrend} width={160} height={36} floor={0} /> },
            { header: "Prediction", render: (r) => <Prediction d={r.latestDecision} /> },
            { header: "Safety", render: (r) => <Safety d={r.latestDecision} /> },
            { header: "Decision", render: (r) => r.latestDecision ? <span><StateBadge state={r.latestDecision.decision} /> <span className="small muted">{r.latestDecision.reason}</span></span> : "—" },
            { header: "Intent → action → verification", render: (r) => <Execution d={r.latestDecision} /> },
          ]} />
      </Card>
      {relation && <RelationDrawer instanceId={instanceId} relation={relation} onClose={() => setRelation(null)} />}
    </>
  );
}

function Prediction({ d }: { d: MroDecision | null }) {
  const m = d?.prediction?.model;
  if (!m) return <span className="muted">—</span>;
  return <span className="small">{m.rate} % → <strong>{m.futureRate} %</strong> <StateBadge state={m.recommendation} />
    {m.cause && <span className="muted"> {m.cause}</span>} <span className="muted">conf {m.confidence}</span></span>;
}

function Safety({ d }: { d: MroDecision | null }) {
  if (!d?.safety) return <span className="muted">—</span>;
  if (d.safety.passed) return <span className="text-ok small">all guards pass</span>;
  return <span className="text-bad small">{d.safety.blocks.map((b) => `${b.guard} (${b.level})`).join(", ")}</span>;
}

function Execution({ d }: { d: MroDecision | null }) {
  if (!d) return <span className="muted">—</span>;
  const parts = [
    d.fromCio != null && d.toCio != null && `${d.fromCio} → ${d.toCio} dB`,
    d.intent && `intent ${d.intent.status}`, d.action && `action ${d.action.status}`,
    d.verification && d.verification.result, d.kpi && `KPI ${d.kpi.verdict}`, d.rollback && `rollback ${d.rollback.result}`,
  ].filter(Boolean);
  return <span className="small"><StateBadge state={d.outcome} />{parts.length > 0 && <span className="muted"> {parts.join(" → ")}</span>}</span>;
}

function RelationDrawer({ instanceId, relation, onClose }: { instanceId: string; relation: MroRelation; onClose: () => void }) {
  const history = useSmo<MroDecision[]>(`${BASE}/${instanceId}/decisions`, { relation: relation.relation, limit: 20 });
  return (
    <Drawer title={`Relation ${relation.source} → ${relation.target}`} onClose={onClose}>
      <Sparkline points={relation.rateTrend} width={420} height={90} floor={0} label="Mobility failure rate (%)" />
      <h3>Latest execution (audit trail)</h3>
      {relation.latestDecision ? <Json value={relation.latestDecision} /> : <p className="muted">No decision yet.</p>}
      <h3>History</h3>
      <DataTable rows={history.data} loading={history.isLoading} error={history.error} rowKey={(d) => d.decisionId} empty="No decisions." columns={[
        { header: "Observed", render: (d) => d.observedAt?.replace("T", " ").slice(0, 16) ?? "—" },
        { header: "Rate", render: (d) => d.rate ?? "—" },
        { header: "Decision", render: (d) => <StateBadge state={d.decision} /> },
        { header: "CIO", render: (d) => d.toCio != null ? `${d.fromCio} → ${d.toCio}` : "—" },
        { header: "Reason", render: (d) => <span className="small">{d.reason}</span> },
        { header: "Outcome", render: (d) => <StateBadge state={d.outcome} /> },
        { header: "Execution", render: (d) => <Id value={d.executionId} /> },
      ]} />
    </Drawer>
  );
}
