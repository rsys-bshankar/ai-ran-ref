/** Section `aiml.mlmf` (MLMF tab, also the KPIs page's "Model KPIs (MLMF)" tab): model-performance subscriptions as a server-paged table with
 * Unsubscribe, the role-gated "Subscribe to model performance" form, and, for the selected subscription, its reports as one sparkline per metric
 * (floor drawn) plus the report table and the admin-only report injection. Model performance, distinct from RAN Analytics' MDAF. */
import { useState } from "react";

import type { MlmfSubscription } from "../../../api/types";
import { useSmoAction } from "../../../api/hooks";
import { Sparkline } from "../../../components/charts";
import { ActionButton, Can, Card, DataTable, Field, Id } from "../../../components/ui";
import { formatTime, metricSeries, numericMetricKeys, parseJsonObject, splitList } from "../../../lib/domain";
import { ServerTable } from "../../../kit/ServerTable";
import { MLMF_SUBSCRIPTIONS, useDmeTypes, useModelIndex, useModelNames, useSubscriptionReports } from "../data/queries";

/** The tab. */
export function Mlmf() {
  const modelName = useModelNames();
  const [selected, setSelected] = useState<MlmfSubscription | null>(null);
  return (
    <>
      <Can method="POST" path={MLMF_SUBSCRIPTIONS}><SubscribeMlmf /></Can>
      <Card section="aiml.mlmf" title="MLMF subscriptions" actions={<span className="muted small">Model performance, distinct from RAN Analytics' MDAF</span>}>
        <ServerTable<MlmfSubscription> path={MLMF_SUBSCRIPTIONS} rowKey={(s) => s.subscriptionId} empty="No MLMF subscriptions."
          onRowClick={setSelected} selectedKey={selected?.subscriptionId} columns={[
            { header: "Subscription", render: (s) => <Id value={s.subscriptionId} /> },
            { header: "Model", render: (s) => modelName(s.modelId) ?? <Id value={s.modelId} /> },
            { header: "Metrics", render: (s) => s.metricTypes.join(", ") },
            { header: "Guard KPI floor", render: (s) => s.guardKpiFloor ? Object.entries(s.guardKpiFloor).map(([k, v]) => `${k} ≥ ${v}`).join(", ") : <span className="muted">none</span> },
            { header: "Delivery", render: (s) => s.notificationDestination ?? <span className="muted">poll</span> },
            { header: "", className: "actions", render: (s) => <ActionButton label="Unsubscribe" action={{ method: "DELETE", path: `${MLMF_SUBSCRIPTIONS}/${s.subscriptionId}`, success: "Unsubscribed" }} onDone={() => setSelected(null)} /> },
          ]} />
      </Card>
      {selected && <MlmfReports sub={selected} />}
    </>
  );
}

/** The reports of one subscription: sparklines, table, and the admin injection. */
function MlmfReports({ sub }: { sub: MlmfSubscription }) {
  const reports = useSubscriptionReports(sub.subscriptionId, 100);
  const [metrics, setMetrics] = useState("{}");
  const parsed = parseJsonObject(metrics);
  const keys = numericMetricKeys(reports.data ?? []);
  return (
    <Card section="aiml.mlmfReports" title={<>Reports for <Id value={sub.subscriptionId} /></>}>
      {keys.length === 0 ? <p className="muted">No reports yet.</p> : (
        <div className="spark-list">{keys.map((k) => <Sparkline key={k} points={metricSeries(reports.data!, k)} floor={sub.guardKpiFloor?.[k]} label={k} width={320} height={60} />)}</div>
      )}
      <DataTable rows={reports.data} loading={reports.isLoading} error={reports.error} rowKey={(r) => r.reportId} empty="—" columns={[
        { header: "Reported", render: (r) => formatTime(r.reportedAt) },
        { header: "Metrics", render: (r) => <code className="small">{JSON.stringify(r.metrics)}</code> },
        { header: "Floor", render: (r) => r.breachedFloor ? <span className="badge b-bad tone-bad">BREACHED</span> : <span className="badge b-ok tone-ok">ok</span> },
      ]} />
      <Can method="POST" path={`${MLMF_SUBSCRIPTIONS}/${sub.subscriptionId}/reports`}>
        <details className="admin-tools">
          <summary>Admin: inject a performance report</summary>
          <p className="muted small">A report under a guard floor marks the model for retrain — and, for a coordination-group member, retrains every PROMOTED member.</p>
          <Field label="Metrics (JSON)"><textarea rows={2} value={metrics} onChange={(e) => setMetrics(e.target.value)} placeholder='{"accuracy": 0.82}' spellCheck={false} /></Field>
          <ActionButton label="Report" disabled={!parsed.ok} action={{ method: "POST", path: `${MLMF_SUBSCRIPTIONS}/${sub.subscriptionId}/reports`, json: parsed.ok ? parsed.value : {}, success: "Report recorded" }} />
        </details>
      </Can>
    </Card>
  );
}

/** Subscribe a model to performance monitoring: DME type, metric types, guard floor, optional push destination. */
function SubscribeMlmf() {
  const models = useModelIndex();
  const dmeTypes = useDmeTypes();
  const [modelId, setModelId] = useState("");
  const [dmeTypeId, setDmeTypeId] = useState("");
  const [metricTypes, setMetricTypes] = useState("accuracy");
  const [floor, setFloor] = useState('{"accuracy": 0.9}');
  const [notificationDestination, setNotificationDestination] = useState("");
  const parsed = parseJsonObject(floor);
  const action = useSmoAction();
  return (
    <Card section="aiml.mlmfSubscribe" title="Subscribe to model performance">
      <form className="form inline" onSubmit={(e) => {
        e.preventDefault();
        if (!parsed.ok) return;
        action.mutate({ method: "POST", path: MLMF_SUBSCRIPTIONS,
          query: { model_id: modelId, dme_type_id: dmeTypeId, notification_destination: notificationDestination || undefined },
          json: { metric_types: splitList(metricTypes), guard_kpi_floor: Object.keys(parsed.value).length ? parsed.value : null }, success: "MLMF subscription created" });
      }}>
        <Field label="Model"><select value={modelId} onChange={(e) => setModelId(e.target.value)} required><option value="">Choose…</option>{models.data?.items.map((m) => <option key={m.modelId} value={m.modelId}>{m.modelType} {m.version}</option>)}</select></Field>
        <Field label="DME type" hint="Data type the metrics arrive on">
          <input list="dme-types" value={dmeTypeId} onChange={(e) => setDmeTypeId(e.target.value)} required placeholder="dmeTypeId (UUID)" pattern="[0-9a-fA-F\-]{36}" />
          <datalist id="dme-types">{dmeTypes.data?.map((t) => <option key={t.dmeTypeId} value={t.dmeTypeId}>{t.typeName}</option>)}</datalist>
        </Field>
        <Field label="Metric types"><input value={metricTypes} onChange={(e) => setMetricTypes(e.target.value)} /></Field>
        <Field label="Guard KPI floor (JSON)" hint={parsed.ok ? undefined : <span className="text-bad">{parsed.error}</span>}><input value={floor} onChange={(e) => setFloor(e.target.value)} /></Field>
        <Field label="Notification URL (optional)" hint="Leave blank to poll instead"><input value={notificationDestination} onChange={(e) => setNotificationDestination(e.target.value)} placeholder="http://consumer/mlmf-events" /></Field>
        <button type="submit" className="btn primary" disabled={!parsed.ok || action.isPending}>Subscribe</button>
      </form>
    </Card>
  );
}
