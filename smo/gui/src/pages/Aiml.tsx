import { useState, type FormEvent } from "react";

import { useSmo, useSmoAction } from "../api/hooks";
import type { CoordinationGroup, DmeType, InferenceJob, MlmfReport, MlmfSubscription, Model, TrainingJob } from "../api/types";
import { FsmStepper, Sparkline } from "../components/charts";
import {
  ActionButton, Can, Card, DataTable, Drawer, ErrorBox, Field, Id, Json, KeyValue, Modal, PageHeader, StateBadge, Tabs,
  useHashTab,
} from "../components/ui";
import { DEPLOYABLE_MODEL_STATES, formatTime, metricSeries, modelActions, numericMetricKeys, parseJsonObject, splitList } from "../lib/domain";

const TABS = ["models", "training", "inference", "groups", "mlmf"] as const;

export function Aiml() {
  const [tab, setTab] = useHashTab(TABS, "models");
  return (
    <>
      <PageHeader title="AI/ML" subtitle={<>Model registration → training → validation → certification → deployment → inference, per <code>docs/call-flows/02-aiml-model-train-to-inference.md</code></>} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "models", label: "Models" }, { id: "training", label: "Training jobs" }, { id: "inference", label: "Inference jobs" },
        { id: "groups", label: "Coordination groups" }, { id: "mlmf", label: "Performance monitoring (MLMF)" },
      ]} />
      {tab === "models" && <Models />}
      {tab === "training" && <TrainingJobs />}
      {tab === "inference" && <InferenceJobs />}
      {tab === "groups" && <Groups />}
      {tab === "mlmf" && <Mlmf />}
    </>
  );
}

export function useModelNames() {
  const models = useSmo<Model[]>("/ai-ml-workflow/models");
  return (id: string | null) => {
    const m = models.data?.find((x) => x.modelId === id);
    return m ? `${m.modelType} ${m.version}` : null;
  };
}

// ---------------------------------------------------------------- models

function Models() {
  const [modelType, setModelType] = useState("");
  const models = useSmo<Model[]>("/ai-ml-workflow/models", { model_type: modelType });
  const [selected, setSelected] = useState<string | null>(null);
  const [registering, setRegistering] = useState(false);
  return (
    <>
      <Card title="Registered models" actions={<>
        <input placeholder="Filter by model type" value={modelType} onChange={(e) => setModelType(e.target.value)} aria-label="Filter by model type" />
        <Can method="POST" path="/ai-ml-workflow/models"><button className="btn primary" onClick={() => setRegistering(true)}>Register model</button></Can>
      </>}>
        <DataTable rows={models.data} loading={models.isLoading} error={models.error} rowKey={(m) => m.modelId}
          empty="No models registered." onRowClick={(m) => setSelected(m.modelId)} selectedKey={selected}
          columns={[
            { header: "Model", render: (m) => <><strong>{m.modelType}</strong> <span className="muted">v{m.version}</span><div className="muted small">{m.description ?? ""}</div></> },
            { header: "ID", render: (m) => <Id value={m.modelId} /> },
            { header: "State", render: (m) => <StateBadge state={m.state} /> },
            { header: "Node groups", render: (m) => m.clearedNodeGroups.join(", ") || <span className="muted">—</span> },
            { header: "Owner", render: (m) => m.owner ?? <span className="muted">—</span> },
            { header: "", className: "actions", render: (m) => <ModelActions model={m} /> },
          ]} />
      </Card>
      {registering && <RegisterModel onClose={() => setRegistering(false)} />}
      {selected && <ModelDrawer id={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

function ModelActions({ model }: { model: Model }) {
  const base = `/ai-ml-workflow/models/${model.modelId}`;
  return (
    <div className="row gap end">
      {modelActions(model.state).map((a) => a.kind === "train"
        ? <ActionButton key="train" label={a.label} tone="primary" action={{ method: "POST", path: "/ai-ml-workflow/training-jobs", json: { modelId: model.modelId, producerId: "smo-gui" }, success: `${a.label}: training job started` }} />
        : <ActionButton key={a.event} label={a.label} tone={a.event === "DEPRECATE" ? "danger" : "primary"} confirm={a.event === "DEPRECATE" ? "Deprecate this model? This is terminal." : undefined}
            action={{ method: "POST", path: `${base}/advance`, query: { event: a.event }, success: `${a.event} → done` }} />)}
    </div>
  );
}

function RegisterModel({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState({ modelType: "", version: "1.0.0", description: "", author: "", owner: "", inputDataType: "", outputDataType: "", requiredResourceTypeId: "" });
  const action = useSmoAction();
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const json = Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v.trim() || null]));
    action.mutate({ method: "POST", path: "/ai-ml-workflow/models", json, success: "Model registered" }, { onSuccess: onClose });
  };
  return (
    <Modal title="Register model" onClose={onClose}>
      <form className="form grid cols-2 tight" onSubmit={submit}>
        <Field label="Model type"><input value={form.modelType} onChange={set("modelType")} required placeholder="traffic-steering" /></Field>
        <Field label="Version" hint="(type, version) must be unique"><input value={form.version} onChange={set("version")} required /></Field>
        <Field label="Description"><input value={form.description} onChange={set("description")} /></Field>
        <Field label="Owner"><input value={form.owner} onChange={set("owner")} /></Field>
        <Field label="Author"><input value={form.author} onChange={set("author")} /></Field>
        <Field label="Required resource type"><input value={form.requiredResourceTypeId} onChange={set("requiredResourceTypeId")} placeholder="e.g. GPU" /></Field>
        <Field label="Input data type"><input value={form.inputDataType} onChange={set("inputDataType")} /></Field>
        <Field label="Output data type"><input value={form.outputDataType} onChange={set("outputDataType")} /></Field>
        <div className="row gap end span-2"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={action.isPending}>Register</button></div>
      </form>
    </Modal>
  );
}

function ModelDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const base = `/ai-ml-workflow/models/${id}`;
  const model = useSmo<Model>(base);
  const jobs = useSmo<TrainingJob[]>("/ai-ml-workflow/training-jobs", { model_id: id });
  const inference = useSmo<InferenceJob[]>("/ai-ml-workflow/inference-jobs", { model_id: id });
  const m = model.data;
  const latestArtifact = m?.artifactLocation ? Number(m.artifactLocation.split(":").pop()) : 0;
  return (
    <Drawer title={m ? `${m.modelType} v${m.version}` : "Model"} onClose={onClose}>
      <ErrorBox error={model.error} />
      {m && <>
        <FsmStepper state={m.state} />
        <div className="row between"><StateBadge state={m.state} /><ModelActions model={m} /></div>
        <KeyValue items={[
          ["Model ID", <code>{m.modelId}</code>], ["Description", m.description], ["Author / owner", [m.author, m.owner].filter(Boolean).join(" / ") || null],
          ["Input → output", m.inputDataType || m.outputDataType ? `${m.inputDataType ?? "?"} → ${m.outputDataType ?? "?"}` : null],
          ["Cleared node groups", m.clearedNodeGroups.join(", ") || null], ["Artifact", m.artifactLocation],
        ]} />

        <h3>Artifacts</h3>
        {latestArtifact > 0 ? (
          <ul className="plain-list">
            {Array.from({ length: latestArtifact }, (_, i) => latestArtifact - i).map((v) => (
              <li key={v}><a href={`/api/smo${base}/artifact/${v}`} download>Download artifact v{v}</a></li>
            ))}
          </ul>
        ) : <p className="muted">No artifact uploaded.</p>}
        <Can method="POST" path={`${base}/artifact`}><ArtifactUpload modelId={id} /></Can>

        {DEPLOYABLE_MODEL_STATES.includes(m.state) && <Can method="POST" path={`${base}/deploy`}><DeployNodeGroups model={m} /></Can>}
        {m.state === "ACTIVE" && <div className="row gap"><ActionButton label="Request inference job" tone="primary" action={{ method: "POST", path: `${base}/inference-jobs`, success: "Inference job RUNNING" }} /></div>}

        <h3>Training jobs</h3>
        <TrainingTable rows={jobs.data} />
        <h3>Inference jobs</h3>
        <InferenceTable rows={inference.data} />
      </>}
    </Drawer>
  );
}

function ArtifactUpload({ modelId }: { modelId: string }) {
  const [file, setFile] = useState<File | null>(null);
  const action = useSmoAction();
  return (
    <form className="form inline" onSubmit={(e) => {
      e.preventDefault();
      if (!file) return;
      const body = new FormData();
      body.append("file", file);
      action.mutate({ method: "POST", path: `/ai-ml-workflow/models/${modelId}/artifact`, body, success: `Uploaded ${file.name}` });
    }}>
      <Field label="Upload artifact (.zip)"><input type="file" accept=".zip,application/zip" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field>
      <button className="btn" disabled={!file || action.isPending}>Upload</button>
    </form>
  );
}

function DeployNodeGroups({ model }: { model: Model }) {
  const [groups, setGroups] = useState(model.clearedNodeGroups.join(", "));
  const action = useSmoAction();
  return (
    <form className="form inline" onSubmit={(e) => {
      e.preventDefault();
      action.mutate({ method: "POST", path: `/ai-ml-workflow/models/${model.modelId}/deploy`, json: splitList(groups), success: "Deployment targets cleared (MLLF)" });
    }}>
      <Field label="Deploy to node groups" hint="Comma-separated. Stamps clearedNodeGroups; requires CERTIFIED or later."><input value={groups} onChange={(e) => setGroups(e.target.value)} placeholder="edge-gpu-a, edge-gpu-b" /></Field>
      <button className="btn" disabled={!splitList(groups).length || action.isPending}>Deploy</button>
    </form>
  );
}

// ---------------------------------------------------------------- jobs

function TrainingJobs() {
  const [status, setStatus] = useState("");
  const jobs = useSmo<TrainingJob[]>("/ai-ml-workflow/training-jobs", { status });
  return (
    <Card title="Training jobs" actions={<select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status"><option value="">All</option>{["RUNNING", "COMPLETED", "CANCELLED", "FAILED"].map((s) => <option key={s}>{s}</option>)}</select>}>
      <p className="muted small">Completing a job is a model transition: advance the model with <em>Training complete</em>. Metrics are written back by the trainer (MLTF).</p>
      <TrainingTable rows={jobs.data} loading={jobs.isLoading} error={jobs.error} />
    </Card>
  );
}

function TrainingTable({ rows, loading, error }: { rows?: TrainingJob[]; loading?: boolean; error?: unknown }) {
  const modelName = useModelNames();
  const [metricsFor, setMetricsFor] = useState<TrainingJob | null>(null);
  return (
    <>
      <DataTable rows={rows} loading={loading} error={error} rowKey={(j) => j.trainingJobId} empty="No training jobs." columns={[
        { header: "Job", render: (j) => <Id value={j.trainingJobId} /> },
        { header: "Target", render: (j) => j.modelId ? (modelName(j.modelId) ?? <Id value={j.modelId} />) : <>group <Id value={j.modelCoordinationGroupId} /></> },
        { header: "Producer", render: (j) => j.producerId },
        { header: "Status", render: (j) => <StateBadge state={j.status} /> },
        { header: "Metrics", render: (j) => j.modelMetrics ? <button className="btn small" onClick={() => setMetricsFor(j)}>View</button> : <span className="muted">—</span> },
        { header: "", className: "actions", render: (j) => j.status === "RUNNING" && (
          <ActionButton label="Cancel" confirm="Cancel this training job?" action={{ method: "DELETE", path: `/ai-ml-workflow/training-jobs/${j.trainingJobId}`, success: "Training job cancelled" }} />
        ) },
      ]} />
      {metricsFor && <Modal title="Model metrics" onClose={() => setMetricsFor(null)}><Json value={metricsFor.modelMetrics} /></Modal>}
    </>
  );
}

function InferenceJobs() {
  const [status, setStatus] = useState("");
  const jobs = useSmo<InferenceJob[]>("/ai-ml-workflow/inference-jobs", { status });
  return (
    <Card title="Inference jobs (MLEF)" actions={<select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status"><option value="">All</option>{["RUNNING", "COMPLETED", "FAILED"].map((s) => <option key={s}>{s}</option>)}</select>}>
      <p className="muted small">Results are pulled through DME against the model's output data type; this view tracks job state only.</p>
      <InferenceTable rows={jobs.data} loading={jobs.isLoading} error={jobs.error} />
    </Card>
  );
}

function InferenceTable({ rows, loading, error }: { rows?: InferenceJob[]; loading?: boolean; error?: unknown }) {
  const modelName = useModelNames();
  return (
    <DataTable rows={rows} loading={loading} error={error} rowKey={(j) => j.inferenceJobId} empty="No inference jobs." columns={[
      { header: "Job", render: (j) => <Id value={j.inferenceJobId} /> },
      { header: "Model", render: (j) => modelName(j.modelId) ?? <Id value={j.modelId} /> },
      { header: "Status", render: (j) => <StateBadge state={j.status} /> },
      { header: "", className: "actions", render: (j) => j.status === "RUNNING" && (
        <div className="row gap end">
          <ActionButton label="Completed" action={{ method: "POST", path: `/ai-ml-workflow/inference-jobs/${j.inferenceJobId}/resolve`, query: { succeeded: true }, success: "Inference COMPLETED" }} />
          <ActionButton label="Failed" action={{ method: "POST", path: `/ai-ml-workflow/inference-jobs/${j.inferenceJobId}/resolve`, query: { succeeded: false }, success: "Inference FAILED" }} />
        </div>
      ) },
    ]} />
  );
}

// ---------------------------------------------------------------- coordination groups

function Groups() {
  const groups = useSmo<CoordinationGroup[]>("/ai-ml-workflow/coordination-groups");
  const models = useSmo<Model[]>("/ai-ml-workflow/models");
  const modelName = useModelNames();
  const [members, setMembers] = useState<string[]>([]);
  const [useCases, setUseCases] = useState("");
  const [propagation, setPropagation] = useState("ANY_MEMBER_TRIGGERS");
  const action = useSmoAction();
  return (
    <>
      <Can method="POST" path="/ai-ml-workflow/coordination-groups">
        <Card title="New coordination group">
          <form className="form inline" onSubmit={(e) => {
            e.preventDefault();
            action.mutate({ method: "POST", path: "/ai-ml-workflow/coordination-groups", json: { memberModelIds: members, memberUseCases: splitList(useCases), retrainPropagation: propagation }, success: "Coordination group created" },
              { onSuccess: () => setMembers([]) });
          }}>
            <Field label="Member models" hint="Ctrl/Cmd-click to pick several">
              <select multiple value={members} onChange={(e) => setMembers([...e.target.selectedOptions].map((o) => o.value))} size={Math.min(5, Math.max(2, models.data?.length ?? 2))}>
                {models.data?.map((m) => <option key={m.modelId} value={m.modelId}>{m.modelType} {m.version} ({m.state})</option>)}
              </select>
            </Field>
            <Field label="Use cases"><input value={useCases} onChange={(e) => setUseCases(e.target.value)} placeholder="energy-saving, mobility" /></Field>
            <Field label="Retrain propagation"><select value={propagation} onChange={(e) => setPropagation(e.target.value)}><option>ANY_MEMBER_TRIGGERS</option><option>MAJORITY_TRIGGERS</option></select></Field>
            <button className="btn primary" disabled={members.length === 0 || action.isPending}>Create</button>
          </form>
        </Card>
      </Can>
      <Card title="Coordination groups" actions={<span className="muted small">A guard-KPI breach on any ACTIVE member retrains every ACTIVE member</span>}>
        <DataTable rows={groups.data} loading={groups.isLoading} error={groups.error} rowKey={(g) => g.groupId} empty="No coordination groups." columns={[
          { header: "Group", render: (g) => <Id value={g.groupId} /> },
          { header: "Members", render: (g) => g.memberModelIds.map((id) => modelName(id) ?? id.slice(0, 8)).join(", ") },
          { header: "Use cases", render: (g) => g.memberUseCases.join(", ") || "—" },
          { header: "Propagation", render: (g) => g.retrainPropagation },
          { header: "", className: "actions", render: (g) => <ActionButton label="Retrain group" action={{ method: "POST", path: "/ai-ml-workflow/training-jobs", json: { modelCoordinationGroupId: g.groupId, producerId: "smo-gui" }, success: "Group training job started" }} /> },
        ]} />
      </Card>
    </>
  );
}

// ---------------------------------------------------------------- MLMF

export function Mlmf() {
  const subs = useSmo<MlmfSubscription[]>("/ai-ml-workflow/mlmf/subscriptions");
  const modelName = useModelNames();
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <>
      <Can method="POST" path="/ai-ml-workflow/mlmf/subscriptions"><SubscribeMlmf /></Can>
      <Card title="MLMF subscriptions" actions={<span className="muted small">Model performance, distinct from RAN Analytics' MDAF</span>}>
        <DataTable rows={subs.data} loading={subs.isLoading} error={subs.error} rowKey={(s) => s.subscriptionId} empty="No MLMF subscriptions."
          onRowClick={(s) => setSelected(s.subscriptionId)} selectedKey={selected} columns={[
            { header: "Subscription", render: (s) => <Id value={s.subscriptionId} /> },
            { header: "Model", render: (s) => modelName(s.modelId) ?? <Id value={s.modelId} /> },
            { header: "Metrics", render: (s) => s.metricTypes.join(", ") },
            { header: "Guard KPI floor", render: (s) => s.guardKpiFloor ? Object.entries(s.guardKpiFloor).map(([k, v]) => `${k} ≥ ${v}`).join(", ") : <span className="muted">none</span> },
          ]} />
      </Card>
      {selected && <MlmfReports sub={subs.data?.find((s) => s.subscriptionId === selected)} />}
    </>
  );
}

function MlmfReports({ sub }: { sub?: MlmfSubscription }) {
  const reports = useSmo<MlmfReport[]>(sub ? `/ai-ml-workflow/mlmf/subscriptions/${sub.subscriptionId}/reports` : null, { limit: 100 });
  const [metrics, setMetrics] = useState("{}");
  const parsed = parseJsonObject(metrics);
  if (!sub) return null;
  const keys = numericMetricKeys(reports.data ?? []);
  return (
    <Card title={<>Reports for <Id value={sub.subscriptionId} /></>}>
      {keys.length === 0 ? <p className="muted">No reports yet.</p> : (
        <div className="spark-list">{keys.map((k) => <Sparkline key={k} points={metricSeries(reports.data!, k)} floor={sub.guardKpiFloor?.[k]} label={k} width={320} height={60} />)}</div>
      )}
      <DataTable rows={reports.data} rowKey={(r) => r.reportId} empty="—" columns={[
        { header: "Reported", render: (r) => formatTime(r.reportedAt) },
        { header: "Metrics", render: (r) => <code className="small">{JSON.stringify(r.metrics)}</code> },
        { header: "Floor", render: (r) => r.breachedFloor ? <span className="badge tone-bad">BREACHED</span> : <span className="badge tone-ok">ok</span> },
      ]} />
      <Can method="POST" path={`/ai-ml-workflow/mlmf/subscriptions/${sub.subscriptionId}/reports`}>
        <details className="admin-tools">
          <summary>Admin: inject a performance report</summary>
          <p className="muted small">A report under a guard floor marks the model for retrain — and, for a coordination-group member, retrains every ACTIVE member.</p>
          <Field label="Metrics (JSON)"><textarea rows={2} value={metrics} onChange={(e) => setMetrics(e.target.value)} placeholder='{"accuracy": 0.82}' spellCheck={false} /></Field>
          <ActionButton label="Report" disabled={!parsed.ok} action={{ method: "POST", path: `/ai-ml-workflow/mlmf/subscriptions/${sub.subscriptionId}/reports`, json: parsed.ok ? parsed.value : {}, success: "Report recorded" }} />
        </details>
      </Can>
    </Card>
  );
}

function SubscribeMlmf() {
  const models = useSmo<Model[]>("/ai-ml-workflow/models");
  const dmeTypes = useSmo<DmeType[]>("/dme/dme-types");
  const [modelId, setModelId] = useState("");
  const [dmeTypeId, setDmeTypeId] = useState("");
  const [metricTypes, setMetricTypes] = useState("accuracy");
  const [floor, setFloor] = useState('{"accuracy": 0.9}');
  const parsed = parseJsonObject(floor);
  const action = useSmoAction();
  return (
    <Card title="Subscribe to model performance">
      <form className="form inline" onSubmit={(e) => {
        e.preventDefault();
        if (!parsed.ok) return;
        action.mutate({ method: "POST", path: "/ai-ml-workflow/mlmf/subscriptions", query: { model_id: modelId, dme_type_id: dmeTypeId },
          json: { metric_types: splitList(metricTypes), guard_kpi_floor: Object.keys(parsed.value).length ? parsed.value : null }, success: "MLMF subscription created" });
      }}>
        <Field label="Model"><select value={modelId} onChange={(e) => setModelId(e.target.value)} required><option value="">Choose…</option>{models.data?.map((m) => <option key={m.modelId} value={m.modelId}>{m.modelType} {m.version}</option>)}</select></Field>
        <Field label="DME type" hint="Data type the metrics arrive on">
          <input list="dme-types" value={dmeTypeId} onChange={(e) => setDmeTypeId(e.target.value)} required placeholder="dmeTypeId (UUID)" pattern="[0-9a-fA-F-]{36}" />
          <datalist id="dme-types">{dmeTypes.data?.map((t) => <option key={t.dmeTypeId} value={t.dmeTypeId}>{t.typeName}</option>)}</datalist>
        </Field>
        <Field label="Metric types"><input value={metricTypes} onChange={(e) => setMetricTypes(e.target.value)} /></Field>
        <Field label="Guard KPI floor (JSON)" hint={parsed.ok ? undefined : <span className="text-bad">{parsed.error}</span>}><input value={floor} onChange={(e) => setFloor(e.target.value)} /></Field>
        <button className="btn primary" disabled={!parsed.ok || action.isPending}>Subscribe</button>
      </form>
    </Card>
  );
}
