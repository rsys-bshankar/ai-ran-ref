import { useState, type FormEvent } from "react";

import { useSmo, useSmoAction } from "../api/hooks";
import type { CoordinationGroup, DmeType, FeatureGroup, InferenceJob, MlmfReport, MlmfSubscription, Model, TrainingJob } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { FsmStepper, Sparkline } from "../components/charts";
import {
  ActionButton, Can, Card, DataTable, Drawer, ErrorBox, Field, Id, Json, KeyValue, Modal, PageHeader, StateBadge, Tabs,
  useHashTab,
} from "../components/ui";
import { DEPLOYABLE_MODEL_STATES, formatTime, metricSeries, modelActions, numericMetricKeys, parseJsonObject, splitList } from "../lib/domain";

const TABS = ["models", "training", "inference", "groups", "mlmf", "features"] as const;

export function Aiml() {
  const [tab, setTab] = useHashTab(TABS, "models");
  return (
    <>
      <PageHeader title="AI/ML" subtitle={<>Model registration → training → validation → certification → deployment → inference, per <code>docs/call-flows/02-aiml-model-train-to-inference.md</code></>} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "models", label: "Models" }, { id: "training", label: "Training jobs" }, { id: "inference", label: "Inference jobs" },
        { id: "groups", label: "Coordination groups" }, { id: "mlmf", label: "Performance monitoring (MLMF)" },
        { id: "features", label: "Feature groups" },
      ]} />
      {tab === "models" && <Models />}
      {tab === "training" && <TrainingJobs />}
      {tab === "inference" && <InferenceJobs />}
      {tab === "groups" && <Groups />}
      {tab === "mlmf" && <Mlmf />}
      {tab === "features" && <FeatureGroups />}
    </>
  );
}

export function useModelNames() {
  const models = useSmo<Model[]>("/mlmr/models");
  return (id: string | null) => {
    const m = models.data?.find((x) => x.modelId === id);
    return m ? `${m.modelType} ${m.version}` : null;
  };
}

// ---------------------------------------------------------------- models

function Models() {
  const [modelType, setModelType] = useState("");
  const models = useSmo<Model[]>("/mlmr/models", { model_type: modelType });
  const [selected, setSelected] = useState<string | null>(null);
  const [registering, setRegistering] = useState(false);
  return (
    <>
      <Card title="Registered models" actions={<>
        <input placeholder="Filter by model type" value={modelType} onChange={(e) => setModelType(e.target.value)} aria-label="Filter by model type" />
        <Can method="POST" path="/mlmr/models"><button className="btn primary" onClick={() => setRegistering(true)}>Register model</button></Can>
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
  const aimgfBase = `/aimgf/models/${model.modelId}`;
  return (
    <div className="row gap end">
      {modelActions(model.state).map((a) => a.kind === "train"
        ? <ActionButton key="train" label={a.label} tone="primary" action={{ method: "POST", path: "/aimgf/training-jobs", json: { modelId: model.modelId, producerId: "smo-gui" }, success: `${a.label}: training job started` }} />
        : <ActionButton key={a.event} label={a.label} tone={a.event === "DEPRECATE" ? "danger" : "primary"} confirm={a.event === "DEPRECATE" ? "Deprecate this model? This is terminal." : undefined}
            action={{ method: "POST", path: `${aimgfBase}/advance`, query: { event: a.event }, success: `${a.event} → done` }} />)}
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
    action.mutate({ method: "POST", path: "/mlmr/models", json, success: "Model registered" }, { onSuccess: onClose });
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
  const mlmrBase = `/mlmr/models/${id}`;
  const aimgfBase = `/aimgf/models/${id}`;
  const mllfBase = `/mllf/models/${id}`;
  const model = useSmo<Model>(mlmrBase);
  const jobs = useSmo<TrainingJob[]>("/aimgf/training-jobs", { model_id: id });
  const inference = useSmo<InferenceJob[]>("/aimgf/inference-jobs", { model_id: id });
  const m = model.data;
  const latestArtifact = m?.artifactLocation ? Number(m.artifactLocation.split(":").pop()) : 0;
  const [editing, setEditing] = useState(false);
  return (
    <Drawer title={m ? `${m.modelType} v${m.version}` : "Model"} onClose={onClose}>
      <ErrorBox error={model.error} />
      {m && <>
        <FsmStepper state={m.state} />
        <div className="row between"><StateBadge state={m.state} /><ModelActions model={m} /></div>
        <div className="row gap">
          <Can method="PUT" path={mlmrBase}><button className="btn small" onClick={() => setEditing(true)}>Edit metadata</button></Can>
          <ActionButton label="Delete model" tone="danger" confirm={`Delete ${m.modelType} v${m.version} with its jobs, subscriptions and artifacts?`}
            action={{ method: "DELETE", path: mlmrBase, success: "Model deleted" }} onDone={onClose} />
        </div>
        <KeyValue items={[
          ["Model ID", <code>{m.modelId}</code>], ["Description", m.description], ["Author / owner", [m.author, m.owner].filter(Boolean).join(" / ") || null],
          ["Input → output", m.inputDataType || m.outputDataType ? `${m.inputDataType ?? "?"} → ${m.outputDataType ?? "?"}` : null],
          ["Cleared node groups", m.clearedNodeGroups.join(", ") || null], ["Artifact", m.artifactLocation],
        ]} />

        <h3>Artifacts</h3>
        {latestArtifact > 0 ? (
          <ul className="plain-list">
            {Array.from({ length: latestArtifact }, (_, i) => latestArtifact - i).map((v) => (
              <li key={v}><a href={`/api/smo${mlmrBase}/artifact/${v}`} download>Download artifact v{v}</a></li>
            ))}
          </ul>
        ) : <p className="muted">No artifact uploaded.</p>}
        <Can method="POST" path={`${mlmrBase}/artifact`}><ArtifactUpload modelId={id} /></Can>

        {DEPLOYABLE_MODEL_STATES.includes(m.state) && <Can method="POST" path={`${mllfBase}/deploy`}><DeployNodeGroups model={m} /></Can>}
        {m.state === "ACTIVE" && <div className="row gap"><ActionButton label="Request inference job" tone="primary" action={{ method: "POST", path: `${aimgfBase}/inference-jobs`, success: "Inference job RUNNING" }} /></div>}

        <h3>Training jobs</h3>
        <TrainingTable rows={jobs.data} />
        <h3>Inference jobs</h3>
        <InferenceTable rows={inference.data} />
      </>}
      {editing && m && <EditModel model={m} onClose={() => setEditing(false)} />}
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
      action.mutate({ method: "POST", path: `/mlmr/models/${modelId}/artifact`, body, success: `Uploaded ${file.name}` });
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
      action.mutate({ method: "POST", path: `/mllf/models/${model.modelId}/deploy`, json: splitList(groups), success: "Deployment targets cleared (MLLF)" });
    }}>
      <Field label="Deploy to node groups" hint="Comma-separated. Stamps clearedNodeGroups; requires CERTIFIED or later."><input value={groups} onChange={(e) => setGroups(e.target.value)} placeholder="edge-gpu-a, edge-gpu-b" /></Field>
      <button className="btn" disabled={!splitList(groups).length || action.isPending}>Deploy</button>
    </form>
  );
}

// ---------------------------------------------------------------- jobs

function TrainingJobs() {
  const [status, setStatus] = useState("");
  const jobs = useSmo<TrainingJob[]>("/aimgf/training-jobs", { status });
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
  const [writeFor, setWriteFor] = useState<TrainingJob | null>(null);
  return (
    <>
      <DataTable rows={rows} loading={loading} error={error} rowKey={(j) => j.trainingJobId} empty="No training jobs." columns={[
        { header: "Job", render: (j) => <Id value={j.trainingJobId} /> },
        { header: "Target", render: (j) => j.modelId ? (modelName(j.modelId) ?? <Id value={j.modelId} />) : <>group <Id value={j.modelCoordinationGroupId} /></> },
        { header: "Producer", render: (j) => j.producerId },
        { header: "Status", render: (j) => <StateBadge state={j.status} /> },
        { header: "Metrics", render: (j) => <div className="row gap">
          {j.modelMetrics && <button className="btn small" onClick={() => setMetricsFor(j)}>View</button>}
          <Can method="POST" path={`/aimgf/training-jobs/${j.trainingJobId}/model-metrics`}><button className="btn small" onClick={() => setWriteFor(j)}>{j.modelMetrics ? "Update" : "Write back"}</button></Can>
          {!j.modelMetrics && <span className="muted">—</span>}
        </div> },
        { header: "", className: "actions", render: (j) => j.status === "RUNNING" && (
          <ActionButton label="Cancel" confirm="Cancel this training job?" action={{ method: "DELETE", path: `/aimgf/training-jobs/${j.trainingJobId}`, success: "Training job cancelled" }} />
        ) },
      ]} />
      {metricsFor && <Modal title="Model metrics" onClose={() => setMetricsFor(null)}><Json value={metricsFor.modelMetrics} /></Modal>}
      {writeFor && <WriteMetrics job={writeFor} onClose={() => setWriteFor(null)} />}
    </>
  );
}

function InferenceJobs() {
  const [status, setStatus] = useState("");
  const jobs = useSmo<InferenceJob[]>("/aimgf/inference-jobs", { status });
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
          <ActionButton label="Completed" action={{ method: "POST", path: `/aimgf/inference-jobs/${j.inferenceJobId}/resolve`, query: { succeeded: true }, success: "Inference COMPLETED" }} />
          <ActionButton label="Failed" action={{ method: "POST", path: `/aimgf/inference-jobs/${j.inferenceJobId}/resolve`, query: { succeeded: false }, success: "Inference FAILED" }} />
        </div>
      ) },
    ]} />
  );
}

// ---------------------------------------------------------------- coordination groups

function Groups() {
  const groups = useSmo<CoordinationGroup[]>("/mlmr/coordination-groups");
  const models = useSmo<Model[]>("/mlmr/models");
  const modelName = useModelNames();
  const [members, setMembers] = useState<string[]>([]);
  const [useCases, setUseCases] = useState("");
  const [propagation, setPropagation] = useState("ANY_MEMBER_TRIGGERS");
  const action = useSmoAction();
  return (
    <>
      <Can method="POST" path="/mlmr/coordination-groups">
        <Card title="New coordination group">
          <form className="form inline" onSubmit={(e) => {
            e.preventDefault();
            action.mutate({ method: "POST", path: "/mlmr/coordination-groups", json: { memberModelIds: members, memberUseCases: splitList(useCases), retrainPropagation: propagation }, success: "Coordination group created" },
              { onSuccess: () => setMembers([]) });
          }}>
            <Field label="Member models" hint={members.length === 1 ? <span className="text-bad">Pick at least 2 models</span> : "Ctrl/Cmd-click to pick 2 or more"}>
              <select multiple value={members} onChange={(e) => setMembers([...e.target.selectedOptions].map((o) => o.value))} size={Math.min(5, Math.max(2, models.data?.length ?? 2))}>
                {models.data?.map((m) => <option key={m.modelId} value={m.modelId}>{m.modelType} {m.version} ({m.state})</option>)}
              </select>
            </Field>
            <Field label="Use cases"><input value={useCases} onChange={(e) => setUseCases(e.target.value)} placeholder="energy-saving, mobility" /></Field>
            <Field label="Retrain propagation"><select value={propagation} onChange={(e) => setPropagation(e.target.value)}><option>ANY_MEMBER_TRIGGERS</option><option>MAJORITY_TRIGGERS</option></select></Field>
            <button className="btn primary" disabled={members.length < 2 || action.isPending}>Create</button>
          </form>
        </Card>
      </Can>
      <Card title="Coordination groups" actions={<span className="muted small">A guard-KPI breach on any ACTIVE member retrains every ACTIVE member</span>}>
        <DataTable rows={groups.data} loading={groups.isLoading} error={groups.error} rowKey={(g) => g.groupId} empty="No coordination groups." columns={[
          { header: "Group", render: (g) => <Id value={g.groupId} /> },
          { header: "Members", render: (g) => g.memberModelIds.map((id) => modelName(id) ?? id.slice(0, 8)).join(", ") },
          { header: "Use cases", render: (g) => g.memberUseCases.join(", ") || "—" },
          { header: "Propagation", render: (g) => g.retrainPropagation },
          { header: "", className: "actions", render: (g) => <ActionButton label="Retrain group" action={{ method: "POST", path: "/aimgf/training-jobs", json: { modelCoordinationGroupId: g.groupId, producerId: "smo-gui" }, success: "Group training job started" }} /> },
        ]} />
      </Card>
    </>
  );
}

// ---------------------------------------------------------------- MLMF

export function Mlmf() {
  const subs = useSmo<MlmfSubscription[]>("/aimgf/mlmf/subscriptions");
  const modelName = useModelNames();
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <>
      <Can method="POST" path="/aimgf/mlmf/subscriptions"><SubscribeMlmf /></Can>
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
  const reports = useSmo<MlmfReport[]>(sub ? `/aimgf/mlmf/subscriptions/${sub.subscriptionId}/reports` : null, { limit: 100 });
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
      <Can method="POST" path={`/aimgf/mlmf/subscriptions/${sub.subscriptionId}/reports`}>
        <details className="admin-tools">
          <summary>Admin: inject a performance report</summary>
          <p className="muted small">A report under a guard floor marks the model for retrain — and, for a coordination-group member, retrains every ACTIVE member.</p>
          <Field label="Metrics (JSON)"><textarea rows={2} value={metrics} onChange={(e) => setMetrics(e.target.value)} placeholder='{"accuracy": 0.82}' spellCheck={false} /></Field>
          <ActionButton label="Report" disabled={!parsed.ok} action={{ method: "POST", path: `/aimgf/mlmf/subscriptions/${sub.subscriptionId}/reports`, json: parsed.ok ? parsed.value : {}, success: "Report recorded" }} />
        </details>
      </Can>
    </Card>
  );
}

function SubscribeMlmf() {
  const models = useSmo<Model[]>("/mlmr/models");
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
        action.mutate({ method: "POST", path: "/aimgf/mlmf/subscriptions", query: { model_id: modelId, dme_type_id: dmeTypeId },
          json: { metric_types: splitList(metricTypes), guard_kpi_floor: Object.keys(parsed.value).length ? parsed.value : null }, success: "MLMF subscription created" });
      }}>
        <Field label="Model"><select value={modelId} onChange={(e) => setModelId(e.target.value)} required><option value="">Choose…</option>{models.data?.map((m) => <option key={m.modelId} value={m.modelId}>{m.modelType} {m.version}</option>)}</select></Field>
        <Field label="DME type" hint="Data type the metrics arrive on">
          <input list="dme-types" value={dmeTypeId} onChange={(e) => setDmeTypeId(e.target.value)} required placeholder="dmeTypeId (UUID)" pattern="[0-9a-fA-F\-]{36}" />
          <datalist id="dme-types">{dmeTypes.data?.map((t) => <option key={t.dmeTypeId} value={t.dmeTypeId}>{t.typeName}</option>)}</datalist>
        </Field>
        <Field label="Metric types"><input value={metricTypes} onChange={(e) => setMetricTypes(e.target.value)} /></Field>
        <Field label="Guard KPI floor (JSON)" hint={parsed.ok ? undefined : <span className="text-bad">{parsed.error}</span>}><input value={floor} onChange={(e) => setFloor(e.target.value)} /></Field>
        <button className="btn primary" disabled={!parsed.ok || action.isPending}>Subscribe</button>
      </form>
    </Card>
  );
}


function EditModel({ model, onClose }: { model: Model; onClose: () => void }) {
  const [f, setF] = useState({
    description: model.description ?? "", author: model.author ?? "", owner: model.owner ?? "",
    inputDataType: model.inputDataType ?? "", outputDataType: model.outputDataType ?? "",
  });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const action = useSmoAction();
  return (
    <Modal title={`Edit ${model.modelType} v${model.version}`} onClose={onClose}>
      <form className="form grid cols-2 tight" onSubmit={(e) => {
        e.preventDefault();
        // modelType/version are the model's identity: sent unchanged (UpdateModel rejects a change)
        action.mutate({ method: "PUT", path: `/mlmr/models/${model.modelId}`, success: "Model metadata updated",
          json: { modelType: model.modelType, version: model.version, clearedNodeGroups: model.clearedNodeGroups, targetEnvironments: model.targetEnvironments,
            ...Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.trim() || null])) } }, { onSuccess: onClose });
      }}>
        <Field label="Description"><input value={f.description} onChange={set("description")} /></Field>
        <Field label="Owner"><input value={f.owner} onChange={set("owner")} /></Field>
        <Field label="Author"><input value={f.author} onChange={set("author")} /></Field>
        <Field label="Input data type"><input value={f.inputDataType} onChange={set("inputDataType")} /></Field>
        <Field label="Output data type"><input value={f.outputDataType} onChange={set("outputDataType")} /></Field>
        <div className="row gap end span-2"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={action.isPending}>Save</button></div>
      </form>
    </Modal>
  );
}

function WriteMetrics({ job, onClose }: { job: TrainingJob; onClose: () => void }) {
  const [text, setText] = useState(JSON.stringify(job.modelMetrics ?? { accuracy: 0.93, loss: 0.12 }, null, 2));
  const parsed = parseJsonObject(text);
  const action = useSmoAction();
  return (
    <Modal title="Write back model metrics" onClose={onClose}>
      <p className="muted small">What the trainer (MLTF) reports for job <Id value={job.trainingJobId} />. Replaces the stored metrics wholesale.</p>
      <textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      {!parsed.ok && <p className="text-bad small">{parsed.error}</p>}
      <div className="row gap end"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!parsed.ok || action.isPending} onClick={() => parsed.ok && action.mutate(
          { method: "POST", path: `/aimgf/training-jobs/${job.trainingJobId}/model-metrics`, json: parsed.value, success: "Metrics recorded" }, { onSuccess: onClose })}>Save</button>
      </div>
    </Modal>
  );
}

function FeatureGroups() {
  const { can } = useAuth();
  const allowed = can("GET", "/aimgf/feature-groups");
  const groups = useSmo<{ featureGroups: FeatureGroup[] }>(allowed ? "/aimgf/feature-groups" : null);
  const [f, setF] = useState({ featureGroupName: "", featureList: "", datalakeSource: "InfluxSource", host: "", port: "8086", bucket: "", token: "", dbOrg: "", measurement: "", sourceName: "" });
  const [enableDme, setEnableDme] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const valid = /^\w{3,63}$/.test(f.featureGroupName);
  return (
    <>
      <Can method="POST" path="/aimgf/feature-groups">
        <Card title="New feature group">
          <div className="form grid cols-3 tight">
            <Field label="Name" hint={f.featureGroupName && !valid ? <span className="text-bad">3-63 word characters</span> : "3-63 word characters, unique"}><input value={f.featureGroupName} onChange={set("featureGroupName")} /></Field>
            <Field label="Features" hint="Comma-separated"><input value={f.featureList} onChange={set("featureList")} placeholder="pdcpBytesDl,pdcpBytesUl" /></Field>
            <Field label="Datalake source"><input value={f.datalakeSource} onChange={set("datalakeSource")} /></Field>
            <Field label="Host"><input value={f.host} onChange={set("host")} /></Field>
            <Field label="Port"><input value={f.port} onChange={set("port")} /></Field>
            <Field label="Bucket"><input value={f.bucket} onChange={set("bucket")} /></Field>
            <Field label="Datalake token"><input type="password" autoComplete="off" value={f.token} onChange={set("token")} /></Field>
            <Field label="DB org"><input value={f.dbOrg} onChange={set("dbOrg")} /></Field>
            <Field label="Measurement"><input value={f.measurement} onChange={set("measurement")} /></Field>
            <label className="check"><input type="checkbox" checked={enableDme} onChange={(e) => setEnableDme(e.target.checked)} /> source via DME</label>
          </div>
          <ActionButton label="Create feature group" tone="primary" disabled={!valid || !f.featureList || !f.host || !f.bucket || !f.token || !f.dbOrg || !f.measurement}
            action={{ method: "POST", path: "/aimgf/feature-groups", json: { ...f, sourceName: f.sourceName || null, enableDme }, success: "Feature group created" }} />
        </Card>
      </Can>
      {!allowed && <Card title="Feature groups"><p className="muted">Feature groups carry datalake credentials, so they're visible to operators and admins only.</p></Card>}
      {allowed && <Card title="Feature groups" actions={<span className="muted small">Operator-only view: groups hold datalake credentials, which are never displayed here</span>}>
        <DataTable rows={groups.data?.featureGroups} loading={groups.isLoading} error={groups.error} rowKey={(g) => g.featureGroupId} empty="No feature groups." columns={[
          { header: "Name", render: (g) => <strong>{g.featureGroupName}</strong> }, { header: "Features", render: (g) => <code className="small">{g.featureList}</code> },
          { header: "Source", render: (g) => `${g.datalakeSource} ${g.host}:${g.port}` }, { header: "Bucket / measurement", render: (g) => `${g.bucket} / ${g.measurement}` },
          { header: "DME", render: (g) => (g.enableDme ? "yes" : "no") },
        ]} />
      </Card>}
    </>
  );
}
