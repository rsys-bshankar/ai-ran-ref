/** The instance drawer of the rApps tables: one instance's lifecycle facts, its configuration (editable), version history, performance
 * sparklines, faults and the admin "inject test reports" tools. Opened by a row click in `InstanceTable` or `Rollouts`. */
import { useState } from "react";
import { Link } from "react-router-dom";

import { useSmoAction } from "../../../api/hooks";
import { Sparkline } from "../../../components/charts";
import { ActionButton, Can, DataTable, Drawer, ErrorBox, Field, Id, Json, KeyValue, SeverityChip, StateBadge } from "../../../components/ui";
import { formatTime, metricSeries, numericMetricKeys, parseJsonObject } from "../../../lib/domain";
import { instanceBase, useFaults, useInstance, usePerformance } from "../data/queries";
import { InstanceActions } from "./InstanceActions";
import { UpgradeModal } from "./UpgradeModal";
import { VersionHistory } from "./VersionHistory";

/** The drawer of instance `id`. */
export function InstanceDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const inst = useInstance(id);
  const perf = usePerformance(id);
  const faults = useFaults(id);
  const [upgrading, setUpgrading] = useState(false);
  const keys = numericMetricKeys(perf.data ?? []);
  return (
    <Drawer title={<>rApp instance <Id value={id} /></>} onClose={onClose}>
      <ErrorBox error={inst.error} />
      {inst.data && <>
        <div className="row between"><StateBadge state={inst.data.state} /><InstanceActions inst={inst.data} withUpgrade={() => setUpgrading(true)} /></div>
        <p><Link className="btn small" to={`/rapps/${id}`}>Open this rApp's page →</Link></p>
        <KeyValue items={[
          ["Instance ID", <code>{inst.data.instanceId}</code>], ["Package", <code>{inst.data.packageId}</code>],
          ["NFO deployment (workloadRef)", inst.data.workloadRef && <code>{inst.data.workloadRef}</code>],
          ["Pending upgrade to", inst.data.pendingUpgradeInstanceId && <code>{inst.data.pendingUpgradeInstanceId}</code>],
          ["SME service API(s)", inst.data.smeServiceIds?.length
            ? <code className="small">{inst.data.smeServiceIds.join(", ")}</code>
            : <span className="muted">none registered</span>],
          ["Autonomy mode", <StateBadge state={inst.data.autonomyMode} />],
          ["Region scope", inst.data.regionScope ? <Json value={inst.data.regionScope} /> : <span className="muted">—</span>],
          ["Last teardown", inst.data.lastTeardown
            ? <span className="small">{inst.data.lastTeardown.reason} of <code>{inst.data.lastTeardown.instanceId.slice(0, 8)}</code>: NFO terminate {inst.data.lastTeardown.nfoTerminate}, usage/stop {inst.data.lastTeardown.usageStop}</span>
            : <span className="muted">—</span>],
        ]} />
        <ConfigEditor id={id} config={inst.data.configuration ?? {}} />
        <VersionHistory id={id} state={inst.data.state} />
      </>}
      <h3>Performance</h3>
      {keys.length === 0 ? <p className="muted">No performance reports.</p> : <div className="spark-list">{keys.map((k) => <Sparkline key={k} points={metricSeries(perf.data!, k)} label={k} width={300} />)}</div>}
      <h3>Faults</h3>
      <DataTable rows={faults.data} error={faults.error} rowKey={(f) => f.faultId} empty="No faults reported." columns={[
        { header: "Severity", render: (f) => <SeverityChip severity={f.severity} /> },
        { header: "Description", render: (f) => f.description ?? "—" },
        { header: "Reported", render: (f) => formatTime(f.reportedAt) },
      ]} />
      <Can method="POST" path={`${instanceBase(id)}/performance`}><InjectReports id={id} /></Can>
      {upgrading && inst.data && <UpgradeModal inst={inst.data} onClose={() => setUpgrading(false)} />}
    </Drawer>
  );
}

/** The instance configuration, shown as JSON with an Edit / Save cycle (`PUT …/config`). */
function ConfigEditor({ id, config }: { id: string; config: Record<string, unknown> }) {
  const [text, setText] = useState<string | null>(null);
  const action = useSmoAction();
  const path = `${instanceBase(id)}/config`;
  if (text === null) {
    return (
      <>
        <div className="row between"><h3>Configuration</h3><Can method="PUT" path={path}><button type="button" className="btn small" onClick={() => setText(JSON.stringify(config, null, 2))}>Edit</button></Can></div>
        <Json value={config} />
      </>
    );
  }
  const parsed = parseJsonObject(text);
  return (
    <>
      <h3>Configuration</h3>
      <textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} aria-label="Configuration (JSON)" />
      {!parsed.ok && <p className="text-bad small">{parsed.error}</p>}
      <div className="row gap end">
        <button type="button" className="btn" onClick={() => setText(null)}>Cancel</button>
        <button type="button" className="btn primary" disabled={!parsed.ok || action.isPending}
          onClick={() => parsed.ok && action.mutate({ method: "PUT", path, json: parsed.value, success: "Configuration updated" }, { onSuccess: () => setText(null) })}>Save</button>
      </div>
    </>
  );
}

/** Admin tools: report performance or a fault as the rApp itself would (a critical fault crashes the instance to FAULTED). */
function InjectReports({ id }: { id: string }) {
  const [metrics, setMetrics] = useState('{"throughputMbps": 120, "latencyMs": 8}');
  const [severity, setSeverity] = useState("minor");
  const [description, setDescription] = useState("");
  const parsed = parseJsonObject(metrics);
  const base = instanceBase(id);
  return (
    <details className="admin-tools">
      <summary>Admin: inject test reports</summary>
      <p className="muted small">What the rApp itself would report. A <strong>critical</strong> fault crashes the instance to FAULTED.</p>
      <Field label="Performance metrics (JSON)"><textarea rows={3} value={metrics} onChange={(e) => setMetrics(e.target.value)} spellCheck={false} /></Field>
      <ActionButton label="Report performance" disabled={!parsed.ok} action={{ method: "POST", path: `${base}/performance`, json: parsed.ok ? parsed.value : {}, success: "Performance recorded" }} />
      <div className="form inline">
        <Field label="Fault severity"><select value={severity} onChange={(e) => setSeverity(e.target.value)}>{["warning", "minor", "major", "critical"].map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Description"><input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <ActionButton label="Report fault" tone={severity === "critical" ? "danger" : "default"} action={{ method: "POST", path: `${base}/fault`, query: { severity, description }, success: "Fault recorded" }} />
      </div>
    </details>
  );
}
