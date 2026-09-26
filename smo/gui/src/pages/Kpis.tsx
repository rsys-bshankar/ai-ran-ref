import { useState } from "react";

import { useSmo, useSmoAction } from "../api/hooks";
import type {
  AnalyticsProducer, DmeType, AnalyticsReport, AnalyticsSubscription, CoordinationGroup, InstanceSummary, Monitor, OCloudMetric,
  O1Endpoint, PerfReport, PmSubscription, RemedialAction, ServiceOrder,
} from "../api/types";
import { Sparkline } from "../components/charts";
import { ActionButton, Can, Card, DataTable, Field, Id, Json, Modal, PageHeader, StateBadge, Tabs, useHashTab } from "../components/ui";
import { formatTime, metricSeries, numericMetricKeys, parseJsonObject } from "../lib/domain";
import { Mlmf } from "./Aiml";

const TABS = ["rapp", "pm", "mlmf", "analytics", "assurance", "ocloud"] as const;

export function Kpis() {
  const [tab, setTab] = useHashTab(TABS, "rapp");
  return (
    <>
      <PageHeader title="KPIs & Assurance" subtitle="rApp, RAN, model and O-Cloud performance, plus SA SMOS closed-loop assurance" />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "rapp", label: "rApp performance" }, { id: "pm", label: "PM subscriptions" }, { id: "mlmf", label: "Model KPIs (MLMF)" },
        { id: "analytics", label: "RAN Analytics" }, { id: "assurance", label: "Assurance (SA SMOS)" }, { id: "ocloud", label: "O-Cloud performance" },
      ]} />
      {tab === "rapp" && <RappPerformance />}
      {tab === "pm" && <PmSubscriptions />}
      {tab === "mlmf" && <Mlmf />}
      {tab === "analytics" && <Analytics />}
      {tab === "assurance" && <Assurance />}
      {tab === "ocloud" && <OCloudPerformance />}
    </>
  );
}

// ---------------------------------------------------------------- rApp performance

function RappPerformance() {
  const instances = useSmo<InstanceSummary[]>("/rapp-mgmt/instances");
  const [id, setId] = useState("");
  const chosen = id || instances.data?.find((i) => i.state === "RUNNING")?.instanceId || "";
  const perf = useSmo<PerfReport[]>(chosen ? `/rapp-mgmt/instances/${chosen}/performance` : null, { limit: 100 });
  const keys = numericMetricKeys(perf.data ?? []);
  return (
    <Card title="rApp performance reports" actions={
      <select value={chosen} onChange={(e) => setId(e.target.value)} aria-label="Instance">
        <option value="">Choose an instance…</option>
        {instances.data?.map((i) => <option key={i.instanceId} value={i.instanceId}>{i.instanceId.slice(0, 8)} ({i.state})</option>)}
      </select>}>
      {!chosen ? <p className="muted">No instance selected.</p> : <>
        {keys.length === 0 ? <p className="muted">This instance hasn't reported performance yet (POST /instances/&#123;id&#125;/performance).</p>
          : <div className="spark-list">{keys.map((k) => <Sparkline key={k} points={metricSeries(perf.data!, k)} label={k} width={320} height={60} />)}</div>}
        <DataTable rows={perf.data} rowKey={(r) => r.reportId} error={perf.error} empty="—" columns={[
          { header: "Reported", render: (r) => formatTime(r.reportedAt) },
          { header: "Metrics", render: (r) => <code className="small">{JSON.stringify(r.metrics)}</code> },
        ]} />
      </>}
    </Card>
  );
}

// ---------------------------------------------------------------- PM

function PmSubscriptions() {
  const subs = useSmo<PmSubscription[]>("/ran-nf-oam/pm-subscriptions");
  const endpoints = useSmo<O1Endpoint[]>("/ran-nf-oam/o1-adaptor-endpoints");
  const [f, setF] = useState({ managed_element_ref: "", counter_type: "DRB.UEThpDl", delivery_method: "push", granularity_period: "900" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <>
      <Can method="POST" path="/ran-nf-oam/pm-subscriptions">
        <Card title="New PM subscription">
          <p className="muted small">SubscribePM registers a DME producer type for the counter (RAN NF OAM LLD 3.5) — the counters themselves are consumed through DME. Live file collection is out of scope.</p>
          <div className="form inline">
            <Field label="Managed element"><select value={f.managed_element_ref} onChange={set("managed_element_ref")}><option value="">Choose…</option>{endpoints.data?.map((e) => <option key={e.endpointId}>{e.managedElementRef}</option>)}</select></Field>
            <Field label="Counter type"><input value={f.counter_type} onChange={set("counter_type")} /></Field>
            <Field label="Delivery"><select value={f.delivery_method} onChange={set("delivery_method")}><option value="pull">pull (ProvMnS)</option><option value="push">push (PMJobControl)</option><option value="stream">stream (StreamingDataReporting)</option><option value="file">file (FileDataReporting)</option></select></Field>
            <Field label="Granularity (s)"><input type="number" min={1} value={f.granularity_period} onChange={set("granularity_period")} /></Field>
            <ActionButton label="Subscribe" tone="primary" disabled={!f.managed_element_ref} action={{ method: "POST", path: "/ran-nf-oam/pm-subscriptions", query: f, success: "PM subscription created" }} />
          </div>
        </Card>
      </Can>
      <Card title="PM subscriptions">
        <DataTable rows={subs.data} loading={subs.isLoading} error={subs.error} rowKey={(s) => s.subscriptionId} empty="No PM subscriptions." columns={[
          { header: "Subscription", render: (s) => <Id value={s.subscriptionId} /> },
          { header: "Managed element", render: (s) => s.managedElementRef },
          { header: "Counter", render: (s) => <code>{s.counterType}</code> },
          { header: "Delivery", render: (s) => s.deliveryMethod },
          { header: "Southbound engine", render: (s) => s.southboundEngine },
          { header: "Granularity", render: (s) => (s.granularityPeriod ? `${s.granularityPeriod} s` : "—") },
        ]} />
      </Card>
    </>
  );
}

// ---------------------------------------------------------------- RAN Analytics

function Analytics() {
  const [type, setType] = useState("");
  const reports = useSmo<AnalyticsReport[]>("/ran-analytics/reports", { analytics_type: type });
  const producers = useSmo<AnalyticsProducer[]>("/ran-analytics/producers");
  const subs = useSmo<AnalyticsSubscription[]>("/ran-analytics/subscriptions");
  const [newType, setNewType] = useState("");
  const [shown, setShown] = useState<AnalyticsReport | null>(null);
  const types = [...new Set((producers.data ?? []).map((p) => p.analyticsType))];
  return (
    <>
      <Card title="Analytics reports (MDAF)" actions={<select value={type} onChange={(e) => setType(e.target.value)} aria-label="Analytics type"><option value="">All types</option>{types.map((t) => <option key={t}>{t}</option>)}</select>}>
        <DataTable rows={reports.data} loading={reports.isLoading} error={reports.error} rowKey={(r) => r.reportId} empty="No analytics reports published." onRowClick={setShown} columns={[
          { header: "Report", render: (r) => <Id value={r.reportId} /> },
          { header: "Type", render: (r) => r.analyticsType },
          { header: "Output", render: (r) => <code className="small clip">{JSON.stringify(r.output)}</code> },
        ]} />
      </Card>
      <div className="grid cols-2">
        <Card title="Producers">
          <DataTable rows={producers.data} rowKey={(p) => `${p.producerId}/${p.analyticsType}`} empty="No producers registered." columns={[
            { header: "Producer", render: (p) => p.producerId }, { header: "Type", render: (p) => p.analyticsType },
            { header: "DME inputs", render: (p) => p.dmeInputTypes.length },
          ]} />
        </Card>
        <Card title="Subscriptions">
          <Can method="POST" path="/ran-analytics/subscriptions">
            <div className="form inline">
              <Field label="Analytics type"><input list="an-types" value={newType} onChange={(e) => setNewType(e.target.value)} /><datalist id="an-types">{types.map((t) => <option key={t} value={t} />)}</datalist></Field>
              <ActionButton label="Subscribe" disabled={!newType} action={{ method: "POST", path: "/ran-analytics/subscriptions", query: { analytics_type: newType, requested_by: "smo-gui" }, success: "Subscribed (poll-based)" }} />
            </div>
          </Can>
          <DataTable rows={subs.data} rowKey={(s) => s.subscriptionId} empty="No subscriptions." columns={[
            { header: "Type", render: (s) => s.analyticsType }, { header: "Requested by", render: (s) => s.requestedBy },
            { header: "Delivery", render: (s) => s.notificationDestination ?? <span className="muted">poll</span> },
            { header: "", className: "actions", render: (s) => <ActionButton label="Unsubscribe" action={{ method: "DELETE", path: `/ran-analytics/subscriptions/${s.subscriptionId}`, success: "Unsubscribed" }} /> },
          ]} />
        </Card>
      </div>
      <Can method="POST" path="/ran-analytics/producers"><AnalyticsProducerTools types={types} /></Can>
      {shown && <Modal title={`${shown.analyticsType} report`} onClose={() => setShown(null)}><Json value={shown.output} /></Modal>}
    </>
  );
}

function AnalyticsProducerTools({ types }: { types: string[] }) {
  const dmeTypes = useSmo<DmeType[]>("/dme/dme-types");
  const [producer, setProducer] = useState("rapp-mdaf-1");
  const [type, setType] = useState("coverage-issue-analysis");
  const [inputs, setInputs] = useState<string[]>([]);
  const [reportType, setReportType] = useState("");
  const [output, setOutput] = useState('{"coverageHoles": 2, "worstCell": "cell-7"}');
  const parsed = parseJsonObject(output);
  return (
    <Card title="Producer side (call flow 08)" actions={<span className="muted small">Admin: act as an MDAF analytics producer</span>}>
      <div className="grid cols-2">
        <div>
          <h3>Register a producer</h3>
          <p className="muted small">Also registers <code>mdaf.&lt;type&gt;</code> with SME so consumers can discover it — the producer must be a registered SME provider (Data &amp; Exposure → SME).</p>
          <div className="form">
            <Field label="Producer ID"><input value={producer} onChange={(e) => setProducer(e.target.value)} /></Field>
            <Field label="Analytics type"><input value={type} onChange={(e) => setType(e.target.value)} /></Field>
            <Field label="DME input types" hint="Ctrl/Cmd-click for several">
              <select multiple size={Math.min(4, Math.max(2, dmeTypes.data?.length ?? 2))} value={inputs} onChange={(e) => setInputs([...e.target.selectedOptions].map((o) => o.value))}>
                {dmeTypes.data?.map((t) => <option key={t.dmeTypeId} value={t.dmeTypeId}>{t.typeName}</option>)}
              </select>
            </Field>
          </div>
          <ActionButton label="Register producer" tone="primary" disabled={!producer || !type} action={{
            method: "POST", path: "/ran-analytics/producers", query: { producer_id: producer, analytics_type: type },
            json: { dme_input_types: inputs, output_schema: { type: "object" } }, success: "Producer registered",
          }} />
        </div>
        <div>
          <h3>Publish a report</h3>
          <p className="muted small">Pushed best-effort to subscribers with a notification destination; poll-only subscribers read it back via the reports list.</p>
          <div className="form">
            <Field label="Analytics type"><input list="an-types-pub" value={reportType} onChange={(e) => setReportType(e.target.value)} /><datalist id="an-types-pub">{types.map((t) => <option key={t} value={t} />)}</datalist></Field>
            <Field label="Output (JSON)" hint={parsed.ok ? undefined : <span className="text-bad">{parsed.error}</span>}><textarea rows={3} value={output} onChange={(e) => setOutput(e.target.value)} spellCheck={false} /></Field>
          </div>
          <ActionButton label="Publish report" tone="primary" disabled={!reportType || !parsed.ok} action={{
            method: "POST", path: "/ran-analytics/reports", query: { analytics_type: reportType },
            json: { output: parsed.ok ? parsed.value : {}, input_sources: inputs }, success: "Report published",
          }} />
        </div>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- SA SMOS

function Assurance() {
  const monitors = useSmo<Monitor[]>("/sa-smos/monitors");
  const actions = useSmo<RemedialAction[]>("/sa-smos/remedial-actions");
  const [selected, setSelected] = useState<Monitor | null>(null);
  return (
    <>
      <Can method="POST" path="/sa-smos/monitors"><RegisterMonitor /></Can>
      <Card title="Assurance monitors" actions={<span className="muted small">Click a monitor to evaluate, remediate or escalate</span>}>
        <DataTable rows={monitors.data} loading={monitors.isLoading} error={monitors.error} rowKey={(m) => m.monitorId} empty="No assurance monitors."
          onRowClick={setSelected} selectedKey={selected?.monitorId} columns={[
            { header: "Monitor", render: (m) => <Id value={m.monitorId} /> },
            { header: "Scope", render: (m) => m.targetOrderId ? <>order <Id value={m.targetOrderId} /></> : m.targetCoordinationGroupId ? <>model group <Id value={m.targetCoordinationGroupId} /></> : <span className="muted">unscoped</span> },
            { header: "Thresholds (floor)", render: (m) => Object.entries(m.thresholds).map(([k, v]) => `${k} ≥ ${v}`).join(", ") || "—" },
            { header: "Actions taken", render: (m) => (actions.data ?? []).filter((a) => a.monitorId === m.monitorId).length },
          ]} />
      </Card>
      {selected && <MonitorPanel monitor={selected} onClose={() => setSelected(null)} />}
      <Card title="Remedial actions" actions={<span className="muted small">ESCALATED = handed to an operator</span>}>
        <DataTable rows={actions.data ? [...actions.data].reverse() : undefined} rowKey={(a) => a.actionId} empty="No remedial actions." columns={[
          { header: "Action", render: (a) => <Id value={a.actionId} /> },
          { header: "Monitor", render: (a) => <Id value={a.monitorId} /> },
          { header: "Scope", render: (a) => {
            const m = monitors.data?.find((x) => x.monitorId === a.monitorId);
            return m?.targetCoordinationGroupId ? "model group (retrain)" : m?.targetOrderId ? "service order" : "—";
          } },
          { header: "Type", render: (a) => a.actionType },
          { header: "Auto-executed", render: (a) => (a.autoExecuted ? "yes" : "no") },
          { header: "Outcome", render: (a) => <StateBadge state={a.outcome} /> },
        ]} />
      </Card>
    </>
  );
}

function RegisterMonitor() {
  const orders = useSmo<ServiceOrder[]>("/so-smos/orders");
  const groups = useSmo<CoordinationGroup[]>("/mlmr/coordination-groups");
  const [target, setTarget] = useState("");
  const [thresholds, setThresholds] = useState('{"throughputMbps": 100}');
  const parsed = parseJsonObject(thresholds);
  const [kind, id] = target.split(":");
  return (
    <Card title="Register assurance monitor">
      <div className="form inline">
        <Field label="Scope" hint="Order-scoped monitors remediate NF deployments; group-scoped ones retrain models">
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Unscoped</option>
            <optgroup label="SO SMOS orders">{orders.data?.map((o) => <option key={o.orderId} value={`order:${o.orderId}`}>{o.scope} ({o.orderId.slice(0, 8)})</option>)}</optgroup>
            <optgroup label="Model coordination groups">{groups.data?.map((g) => <option key={g.groupId} value={`group:${g.groupId}`}>group {g.groupId.slice(0, 8)}</option>)}</optgroup>
          </select>
        </Field>
        <Field label="Thresholds — metric floors (JSON)" hint={parsed.ok ? undefined : <span className="text-bad">{parsed.error}</span>}><input value={thresholds} onChange={(e) => setThresholds(e.target.value)} /></Field>
        <ActionButton label="Register" tone="primary" disabled={!parsed.ok} action={{
          method: "POST", path: "/sa-smos/monitors", json: parsed.ok ? parsed.value : {},
          query: { target_order_id: kind === "order" ? id : undefined, target_coordination_group_id: kind === "group" ? id : undefined },
          success: "Monitor registered",
        }} />
      </div>
    </Card>
  );
}

function MonitorPanel({ monitor, onClose }: { monitor: Monitor; onClose: () => void }) {
  const [metrics, setMetrics] = useState(JSON.stringify(Object.fromEntries(Object.keys(monitor.thresholds).map((k) => [k, 0])), null, 0));
  const [breaches, setBreaches] = useState<Record<string, number> | null>(null);
  const [actionType, setActionType] = useState("CONFIG_CHANGE");
  const [reason, setReason] = useState("");
  const evaluate = useSmoAction();
  const parsed = parseJsonObject(metrics);
  const base = `/sa-smos/monitors/${monitor.monitorId}`;
  return (
    <Card title={<>Monitor <Id value={monitor.monitorId} /></>} actions={<button className="btn ghost" onClick={onClose}>Close</button>}>
      <div className="grid cols-3">
        <div>
          <h3>Evaluate thresholds</h3>
          <Field label="Current metrics (JSON)" hint={parsed.ok ? undefined : <span className="text-bad">{parsed.error}</span>}><textarea rows={3} value={metrics} onChange={(e) => setMetrics(e.target.value)} spellCheck={false} /></Field>
          <Can method="POST" path={`${base}/evaluate`}>
            <button className="btn primary" disabled={!parsed.ok || evaluate.isPending} onClick={() => parsed.ok && evaluate.mutate({ method: "POST", path: `${base}/evaluate`, json: parsed.value },
              { onSuccess: (d) => setBreaches((d as { breaches: Record<string, number> }).breaches) })}>Evaluate</button>
          </Can>
          {breaches && (Object.keys(breaches).length === 0 ? <p className="text-ok">No breaches.</p> : <p className="text-bad">Breached: {Object.entries(breaches).map(([k, v]) => `${k} < ${v}`).join(", ")}</p>)}
        </div>
        <div>
          <h3>Remedial action</h3>
          {monitor.targetCoordinationGroupId && <p className="muted small">Group-scoped: any action type dispatches a group retrain via AI/ML Workflow.</p>}
          <Field label="Action type" hint={monitor.targetCoordinationGroupId ? undefined : actionType === "ROLLBACK" ? "Not supported: rApp Management keeps no prior-version history (SA SMOS returns ROLLBACK_HISTORY_UNAVAILABLE)." : actionType === "SCALE" ? "Always escalates in Phase 1 (NFO scale is a stub)." : undefined}>
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

// ---------------------------------------------------------------- FOCOM performance

function OCloudPerformance() {
  const metrics = useSmo<OCloudMetric[]>("/focom/performance");
  return (
    <Card title="O-Cloud performance (FOCOM)">
      <DataTable rows={metrics.data} loading={metrics.isLoading} error={metrics.error} rowKey={(m) => `${m.resourceRef}/${m.metricName}/${m.value}`} empty="No O-Cloud metrics recorded." columns={[
        { header: "Resource", render: (m) => <code>{m.resourceRef}</code> }, { header: "Metric", render: (m) => m.metricName }, { header: "Value", render: (m) => m.value },
      ]} />
    </Card>
  );
}
