/**
 * The AI/ML page (route /aiml): the model lifecycle of call flow 02, in six tabs: models (MLMR registry with AIMgF's lifecycle and runtime state), training jobs, inference jobs, model coordination groups, performance monitoring (MLMF, also used
 * on the KPIs page) and feature groups. Every signed-in role may read, except feature groups, which hold datalake credentials and are visible only to roles the permission table allows (operator and up). What a button offers comes from the state machines in
 * `lib/domain.ts` (`modelActions`, `runtimeActions`); which buttons a role sees comes from the BFF's permission table (`Can` and `ActionButton`): the training, validation and emulation requests and the training and validation approvals are operator calls, the
 * governance decisions (submit for approval, approve, reject, certify, promote, roll back) and deprecate and retire are admin calls. The BFF sets `decidedBy` itself from the signed-in user (it overrides the `smo-gui` this page sends); the producer id `smo-gui`
 * sent with job requests is not an authority either.
 */

import { useState, type FormEvent } from "react";

import { useSmo, useSmoAction } from "../api/hooks";
import type { CoordinationGroup, DmeType, FeatureGroup, InferenceJob, MlmfReport, MlmfSubscription, Model, ModelLifecycle, TrainingJob } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { FsmStepper, Sparkline } from "../components/charts";
import { CompleteJobButton } from "../components/CompleteJobButton";
import {
  ActionButton, Can, Card, DataTable, Drawer, ErrorBox, Field, Id, Json, KeyValue, Modal, PageHeader, StateBadge, Tabs,
  useHashTab,
} from "../components/ui";
import { DEPLOYABLE_MODEL_STATES, formatTime, metricSeries, modelActions, numericMetricKeys, parseJsonObject, runtimeActions, splitList } from "../lib/domain";

const REGISTERED_LIFECYCLE: ModelLifecycle = {
  modelId: "", modelLifecycleState: "REGISTERED", runtimeLifecycleState: "NOT_DEPLOYED",
  trainingJobId: null, clearedNodeGroups: [], nfDeploymentDescriptorId: null, nfDeploymentId: null,
  trainingApproved: false, validationApproved: false,
};

const TABS = ["models", "training", "inference", "groups", "mlmf", "features"] as const;

/** The page: header and the six tabs, kept in the URL hash. */
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

/**
 * Returns a function that names a model id as "<type> <version>" from the cached model list, or null when the id is not (yet) known; callers fall back to showing the short id.
 */
export function useModelNames() {
  const models = useSmo<Model[]>("/mlmr/models");
  return (id: string | null) => {
    const m = models.data?.find((x) => x.modelId === id);
    return m ? `${m.modelType} ${m.version}` : null;
  };
}

// ---------------------------------------------------------------- models

function useLifecycles() {
  const lifecycles = useSmo<ModelLifecycle[]>("/aimgf/model-lifecycles");
  return (modelId: string): ModelLifecycle => lifecycles.data?.find((l) => l.modelId === modelId) ?? { ...REGISTERED_LIFECYCLE, modelId };
}

/**
 * The models tab: the registered models (filter by type) with their lifecycle state and cleared node groups, the buttons the state allows, Register model (for roles that may), and the model drawer on a row click.
 */
function Models() {
  const [modelType, setModelType] = useState("");
  const models = useSmo<Model[]>("/mlmr/models", { model_type: modelType });
  const lifecycleFor = useLifecycles();
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
            { header: "State", render: (m) => <StateBadge state={lifecycleFor(m.modelId).modelLifecycleState} /> },
            { header: "Node groups", render: (m) => lifecycleFor(m.modelId).clearedNodeGroups.join(", ") || <span className="muted">—</span> },
            { header: "Owner", render: (m) => m.owner ?? <span className="muted">—</span> },
            { header: "", className: "actions", render: (m) => <ModelActions model={m} lifecycle={lifecycleFor(m.modelId)} /> },
          ]} />
      </Card>
      {registering && <RegisterModel onClose={() => setRegistering(false)} />}
      {selected && <ModelDrawer id={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

/**
 * The buttons of a model's lifecycle state (`modelActions`): request training, validation or emulation (job routes), complete the in-flight run (`CompleteJobButton`) or advance the lifecycle (governance events carry `decided_by`). DEPRECATE and RETIRE ask first because they are terminal.
 */
function ModelActions({ model, lifecycle }: { model: Model; lifecycle: ModelLifecycle }) {
  const aimgfBase = `/aimgf/models/${model.modelId}`;
  return (
    <div className="row gap end">
      {modelActions(lifecycle.modelLifecycleState, { trainingApproved: lifecycle.trainingApproved, validationApproved: lifecycle.validationApproved }).map((a) => {
        if (a.kind === "train") return <ActionButton key="train" label={a.label} tone="primary" action={{ method: "POST", path: "/aimgf/training-jobs", json: { modelId: model.modelId, producerId: "smo-gui" }, success: `${a.label}: training job started` }} />;
        if (a.kind === "validate") return <ActionButton key="validate" label={a.label} tone="primary" action={{ method: "POST", path: "/aimgf/validation-jobs", json: { modelId: model.modelId, producerId: "smo-gui" }, success: "Validation job started" }} />;
        if (a.kind === "emulate") return <ActionButton key="emulate" label={a.label} tone="primary" action={{ method: "POST", path: "/aimgf/emulation-jobs", json: { modelId: model.modelId, producerId: "smo-gui" }, success: "Emulation job started" }} />;
        if (a.kind === "complete") return <CompleteJobButton key={`complete-${a.stage}`} modelId={model.modelId} stage={a.stage} label={a.label} trainingJobId={lifecycle.trainingJobId} />;
        const destructive = a.event === "DEPRECATE" || a.event === "RETIRE" || a.event === "REJECT";
        return <ActionButton key={a.event} label={a.label} tone={destructive ? "danger" : "primary"}
          confirm={a.event === "DEPRECATE" || a.event === "RETIRE" ? `${a.label} this model? This is terminal.` : undefined}
          action={{ method: "POST", path: `${aimgfBase}/advance`, query: a.governance ? { event: a.event, decided_by: "smo-gui" } : { event: a.event }, success: `${a.event} → done` }} />;
      })}
    </div>
  );
}

/**
 * The buttons of a model's runtime state (deploy, activate, scale, terminate; `runtimeActions`); terminate asks first.
 */
function RuntimeActions({ modelId, lifecycle }: { modelId: string; lifecycle: ModelLifecycle }) {
  const aimgfBase = `/aimgf/models/${modelId}`;
  return (
    <div className="row gap end">
      {runtimeActions(lifecycle.runtimeLifecycleState).map((a) => (
        <ActionButton key={a.action} label={a.label} tone={a.action === "terminate" ? "danger" : "primary"}
          confirm={a.action === "terminate" ? "Terminate this model's runtime?" : undefined}
          action={{ method: "POST", path: `${aimgfBase}/runtime/${a.action}`, success: `Runtime ${a.action}: done` }} />
      ))}
    </div>
  );
}

/**
 * The dialog that registers a model (POST /mlmr/models). Blank fields are sent as null; (type, version) must be unique, which the backend enforces.
 */
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

/**
 * The drawer of one model: the lifecycle stepper, the state's actions, edit and delete, the metadata, the artifact versions to download (the latest version is the number after the last colon of `artifactLocation`, so artifacts v1 to vN are offered),
 * artifact upload, deployment to node groups (only for a CERTIFIED or PROMOTED model), runtime state and actions, an inference-job request for an ACTIVE runtime, and the training and inference jobs of the model.
 */
function ModelDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const mlmrBase = `/mlmr/models/${id}`;
  const aimgfBase = `/aimgf/models/${id}`;
  const mllfBase = `/mllf/models/${id}`;
  const model = useSmo<Model>(mlmrBase);
  const lifecycle = useSmo<ModelLifecycle>(`${aimgfBase}/lifecycle`);
  const jobs = useSmo<TrainingJob[]>("/aimgf/training-jobs", { model_id: id });
  const inference = useSmo<InferenceJob[]>("/aimgf/inference-jobs", { model_id: id });
  const m = model.data;
  const l = lifecycle.data;
  const latestArtifact = m?.artifactLocation ? Number(m.artifactLocation.split(":").pop()) : 0;
  const [editing, setEditing] = useState(false);
  return (
    <Drawer title={m ? `${m.modelType} v${m.version}` : "Model"} onClose={onClose}>
      <ErrorBox error={model.error || lifecycle.error} />
      {m && l && <>
        <FsmStepper state={l.modelLifecycleState} />
        <div className="row between"><StateBadge state={l.modelLifecycleState} /><ModelActions model={m} lifecycle={l} /></div>
        <div className="row gap">
          <Can method="PUT" path={mlmrBase}><button className="btn small" onClick={() => setEditing(true)}>Edit metadata</button></Can>
          <ActionButton label="Delete model" tone="danger" confirm={`Delete ${m.modelType} v${m.version} with its jobs, subscriptions and artifacts?`}
            action={{ method: "DELETE", path: mlmrBase, success: "Model deleted" }} onDone={onClose} />
        </div>
        <KeyValue items={[
          ["Model ID", <code>{m.modelId}</code>], ["Description", m.description], ["Author / owner", [m.author, m.owner].filter(Boolean).join(" / ") || null],
          ["Input → output", m.inputDataType || m.outputDataType ? `${m.inputDataType ?? "?"} → ${m.outputDataType ?? "?"}` : null],
          ["Cleared node groups", l.clearedNodeGroups.join(", ") || null], ["Artifact", m.artifactLocation],
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

        {DEPLOYABLE_MODEL_STATES.includes(l.modelLifecycleState) && <Can method="POST" path={`${mllfBase}/deploy`}><DeployNodeGroups modelId={id} clearedNodeGroups={l.clearedNodeGroups} /></Can>}

        <h3>Runtime — <StateBadge state={l.runtimeLifecycleState} /></h3>
        <RuntimeActions modelId={id} lifecycle={l} />
        {l.runtimeLifecycleState === "ACTIVE" && <div className="row gap"><ActionButton label="Request inference job" tone="primary" action={{ method: "POST", path: `${aimgfBase}/inference-jobs`, success: "Inference job RUNNING" }} /></div>}

        <h3>Training jobs</h3>
        <TrainingTable rows={jobs.data} />
        <h3>Inference jobs</h3>
        <InferenceTable rows={inference.data} />
      </>}
      {editing && m && <EditModel model={m} onClose={() => setEditing(false)} />}
    </Drawer>
  );
}

/**
 * The form that uploads a model artifact (a .zip) as multipart form data to MLMR; each upload becomes the next artifact version.
 */
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

/**
 * The form that clears a model for deployment on node groups (comma-separated; POST /mllf/models/<id>/deploy). It stamps the cleared node groups on AIMgF's lifecycle row and needs a CERTIFIED or PROMOTED model.
 */
function DeployNodeGroups({ modelId, clearedNodeGroups }: { modelId: string; clearedNodeGroups: string[] }) {
  const [groups, setGroups] = useState(clearedNodeGroups.join(", "));
  const action = useSmoAction();
  return (
    <form className="form inline" onSubmit={(e) => {
      e.preventDefault();
      action.mutate({ method: "POST", path: `/mllf/models/${modelId}/deploy`, json: splitList(groups), success: "Deployment targets cleared (MLLF)" });
    }}>
      <Field label="Deploy to node groups" hint="Comma-separated. Stamps clearedNodeGroups on AIMgF's own lifecycle row; requires CERTIFIED or PROMOTED."><input value={groups} onChange={(e) => setGroups(e.target.value)} placeholder="edge-gpu-a, edge-gpu-b" /></Field>
      <button className="btn" disabled={!splitList(groups).length || action.isPending}>Deploy</button>
    </form>
  );
}

// ---------------------------------------------------------------- jobs

/** The training jobs tab (filter by status). */
function TrainingJobs() {
  const [status, setStatus] = useState("");
  const jobs = useSmo<TrainingJob[]>("/aimgf/training-jobs", { status });
  return (
    <Card title="Training jobs" actions={<select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status"><option value="">All</option>{["NOT_STARTED", "IN_PROGRESS", "SUSPENDED", "FINISHED", "CANCELLED", "FAILED"].map((s) => <option key={s}>{s}</option>)}</select>}>
      <p className="muted small">Completing a job is a model transition: advance the model with <em>Training complete</em>. Metrics are written back by the trainer (MLTF).</p>
      <TrainingTable rows={jobs.data} loading={jobs.isLoading} error={jobs.error} />
    </Card>
  );
}

// OI-5-aiml-trainingjob-steps: the steps a run passes through, reported by its runtime
const STEP_LABEL: Record<TrainingJob["currentStep"], string> = { DATA_EXTRACTION: "1/3 data extraction", TRAINING: "2/3 training", TRAINED_MODEL: "3/3 trained model" };

/**
 * A table of training jobs: target (model or coordination group), producer, status, the furthest step the run reported (`STEP_LABEL`), the NFO runtime, and the metrics (view, and write back for roles that may); a job that is IN_PROGRESS or SUSPENDED can be cancelled.
 */
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
        { header: "Step", render: (j) => j.steps ? <span className="small">{STEP_LABEL[j.currentStep]} <span className="muted">({j.steps[j.currentStep].toLowerCase().replace("_", " ")})</span></span> : "—" },
        { header: "Runtime", render: (j) => <Id value={j.nfDeploymentId} /> },
        { header: "Metrics", render: (j) => <div className="row gap">
          {j.modelMetrics && <button className="btn small" onClick={() => setMetricsFor(j)}>View</button>}
          <Can method="POST" path={`/aimgf/training-jobs/${j.trainingJobId}/model-metrics`}><button className="btn small" onClick={() => setWriteFor(j)}>{j.modelMetrics ? "Update" : "Write back"}</button></Can>
          {!j.modelMetrics && <span className="muted">—</span>}
        </div> },
        { header: "", className: "actions", render: (j) => ["IN_PROGRESS", "SUSPENDED"].includes(j.status) && (
          <ActionButton label="Cancel" confirm="Cancel this training job?" action={{ method: "DELETE", path: `/aimgf/training-jobs/${j.trainingJobId}`, success: "Training job cancelled" }} />
        ) },
      ]} />
      {metricsFor && <Modal title="Model metrics" onClose={() => setMetricsFor(null)}><Json value={metricsFor.modelMetrics} /></Modal>}
      {writeFor && <WriteMetrics job={writeFor} onClose={() => setWriteFor(null)} />}
    </>
  );
}

/**
 * The inference jobs tab (filter by status); results are pulled through DME, so this view tracks job state only.
 */
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

/**
 * A table of inference jobs with Completed and Failed buttons for a RUNNING job (the resolve route, which simulates the runtime reporting the outcome).
 */
function InferenceTable({ rows, loading, error }: { rows?: InferenceJob[]; loading?: boolean; error?: unknown }) {
  const modelName = useModelNames();
  return (
    <DataTable rows={rows} loading={loading} error={error} rowKey={(j) => j.inferenceJobId} empty="No inference jobs." columns={[
      { header: "Job", render: (j) => <Id value={j.inferenceJobId} /> },
      { header: "Model", render: (j) => modelName(j.modelId) ?? <Id value={j.modelId} /> },
      { header: "Status", render: (j) => <StateBadge state={j.status} /> },
      { header: "Runtime", render: (j) => <Id value={j.nfDeploymentId} /> },
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

/**
 * The coordination groups tab: create a group of two or more models (for roles that may) and list the groups with a Retrain group button (a training job for the group).
 */
function Groups() {
  const groups = useSmo<CoordinationGroup[]>("/mlmr/coordination-groups");
  const models = useSmo<Model[]>("/mlmr/models");
  const modelName = useModelNames();
  const lifecycleFor = useLifecycles();
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
                {models.data?.map((m) => <option key={m.modelId} value={m.modelId}>{m.modelType} {m.version} ({lifecycleFor(m.modelId).modelLifecycleState})</option>)}
              </select>
            </Field>
            <Field label="Use cases"><input value={useCases} onChange={(e) => setUseCases(e.target.value)} placeholder="energy-saving, mobility" /></Field>
            <Field label="Retrain propagation"><select value={propagation} onChange={(e) => setPropagation(e.target.value)}><option>ANY_MEMBER_TRIGGERS</option><option>MAJORITY_TRIGGERS</option></select></Field>
            <button className="btn primary" disabled={members.length < 2 || action.isPending}>Create</button>
          </form>
        </Card>
      </Can>
      <Card title="Coordination groups" actions={<span className="muted small">A guard-KPI breach on any PROMOTED member retrains every PROMOTED member</span>}>
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

/**
 * The MLMF tab (model performance monitoring, shared with the KPIs page): the subscriptions with their guard KPI floors and a report view for the selected one.
 */
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
            { header: "Delivery", render: (s) => s.notificationDestination ?? <span className="muted">poll</span> },
            { header: "", className: "actions", render: (s) => <ActionButton label="Unsubscribe" action={{ method: "DELETE", path: `/aimgf/mlmf/subscriptions/${s.subscriptionId}`, success: "Unsubscribed" }} /> },
          ]} />
      </Card>
      {selected && <MlmfReports sub={subs.data?.find((s) => s.subscriptionId === selected)} />}
    </>
  );
}

/**
 * The reports of one MLMF subscription: a sparkline per numeric metric with the guard floor drawn, the raw reports with BREACHED or ok, and an admin tool to inject a report.
 */
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
          <p className="muted small">A report under a guard floor marks the model for retrain — and, for a coordination-group member, retrains every PROMOTED member.</p>
          <Field label="Metrics (JSON)"><textarea rows={2} value={metrics} onChange={(e) => setMetrics(e.target.value)} placeholder='{"accuracy": 0.82}' spellCheck={false} /></Field>
          <ActionButton label="Report" disabled={!parsed.ok} action={{ method: "POST", path: `/aimgf/mlmf/subscriptions/${sub.subscriptionId}/reports`, json: parsed.ok ? parsed.value : {}, success: "Report recorded" }} />
        </details>
      </Can>
    </Card>
  );
}

/**
 * The form that subscribes to a model's performance (POST /aimgf/mlmf/subscriptions): the model, the DME type the metrics arrive on, the metric types, an optional guard KPI floor and an optional notification URL (blank: poll).
 */
function SubscribeMlmf() {
  const models = useSmo<Model[]>("/mlmr/models");
  const dmeTypes = useSmo<DmeType[]>("/dme/dme-types");
  const [modelId, setModelId] = useState("");
  const [dmeTypeId, setDmeTypeId] = useState("");
  const [metricTypes, setMetricTypes] = useState("accuracy");
  const [floor, setFloor] = useState('{"accuracy": 0.9}');
  const [notificationDestination, setNotificationDestination] = useState("");
  const parsed = parseJsonObject(floor);
  const action = useSmoAction();
  return (
    <Card title="Subscribe to model performance">
      <form className="form inline" onSubmit={(e) => {
        e.preventDefault();
        if (!parsed.ok) return;
        action.mutate({ method: "POST", path: "/aimgf/mlmf/subscriptions",
          query: { model_id: modelId, dme_type_id: dmeTypeId, notification_destination: notificationDestination || undefined },
          json: { metric_types: splitList(metricTypes), guard_kpi_floor: Object.keys(parsed.value).length ? parsed.value : null }, success: "MLMF subscription created" });
      }}>
        <Field label="Model"><select value={modelId} onChange={(e) => setModelId(e.target.value)} required><option value="">Choose…</option>{models.data?.map((m) => <option key={m.modelId} value={m.modelId}>{m.modelType} {m.version}</option>)}</select></Field>
        <Field label="DME type" hint="Data type the metrics arrive on">
          <input list="dme-types" value={dmeTypeId} onChange={(e) => setDmeTypeId(e.target.value)} required placeholder="dmeTypeId (UUID)" pattern="[0-9a-fA-F\-]{36}" />
          <datalist id="dme-types">{dmeTypes.data?.map((t) => <option key={t.dmeTypeId} value={t.dmeTypeId}>{t.typeName}</option>)}</datalist>
        </Field>
        <Field label="Metric types"><input value={metricTypes} onChange={(e) => setMetricTypes(e.target.value)} /></Field>
        <Field label="Guard KPI floor (JSON)" hint={parsed.ok ? undefined : <span className="text-bad">{parsed.error}</span>}><input value={floor} onChange={(e) => setFloor(e.target.value)} /></Field>
        <Field label="Notification URL (optional)" hint="Leave blank to poll instead"><input value={notificationDestination} onChange={(e) => setNotificationDestination(e.target.value)} placeholder="http://consumer/mlmf-events" /></Field>
        <button className="btn primary" disabled={!parsed.ok || action.isPending}>Subscribe</button>
      </form>
    </Card>
  );
}


/**
 * The dialog that edits a model's metadata. The model type and version are its identity and are sent unchanged, because the backend rejects a change.
 */
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
          json: { modelType: model.modelType, version: model.version, targetEnvironments: model.targetEnvironments,
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

/**
 * The dialog that writes back a training job's model metrics as JSON, as the trainer (MLTF) would; it replaces the stored metrics wholesale.
 */
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

/**
 * The feature groups tab: the groups (visible only when the role may GET them, because they hold datalake credentials, which are never shown) and the create form (for roles that may), whose token field is a password input. With "source via DME" a DME type is chosen and AIMgF creates a data job for the group.
 */
function FeatureGroups() {
  const { can } = useAuth();
  const allowed = can("GET", "/aimgf/feature-groups");
  const groups = useSmo<FeatureGroup[]>(allowed ? "/aimgf/feature-groups" : null);
  const [f, setF] = useState({ featureGroupName: "", featureList: "", datalakeSource: "InfluxSource", host: "", port: "8086", bucket: "", token: "", dbOrg: "", measurement: "", sourceName: "" });
  const [enableDme, setEnableDme] = useState(false);
  const [dmeTypeId, setDmeTypeId] = useState("");
  const dmeTypes = useSmo<DmeType[]>(enableDme ? "/dme/dme-types" : null);
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
            {enableDme && <Field label="DME type" hint="AIMgF creates a DME data job of this type for the group">
              <select value={dmeTypeId} onChange={(e) => setDmeTypeId(e.target.value)}><option value="">Choose…</option>{dmeTypes.data?.map((t) => <option key={t.dmeTypeId} value={t.dmeTypeId}>{t.typeName}</option>)}</select>
            </Field>}
          </div>
          <ActionButton label="Create feature group" tone="primary" disabled={!valid || !f.featureList || !f.host || !f.bucket || !f.token || !f.dbOrg || !f.measurement || (enableDme && !dmeTypeId)}
            action={{ method: "POST", path: "/aimgf/feature-groups", json: { ...f, sourceName: f.sourceName || null, enableDme, dmeTypeId: enableDme ? dmeTypeId : null }, success: "Feature group created" }} />
        </Card>
      </Can>
      {!allowed && <Card title="Feature groups"><p className="muted">Feature groups carry datalake credentials, so they're visible to operators and admins only.</p></Card>}
      {allowed && <Card title="Feature groups" actions={<span className="muted small">Operator-only view: groups hold datalake credentials, which are never displayed here</span>}>
        <DataTable rows={groups.data} loading={groups.isLoading} error={groups.error} rowKey={(g) => g.featureGroupId} empty="No feature groups." columns={[
          { header: "Name", render: (g) => <strong>{g.featureGroupName}</strong> }, { header: "Features", render: (g) => <code className="small">{g.featureList}</code> },
          { header: "Source", render: (g) => `${g.datalakeSource} ${g.host}:${g.port}` }, { header: "Bucket / measurement", render: (g) => `${g.bucket} / ${g.measurement}` },
          { header: "DME data job", render: (g) => (g.dmeDataJobId ? <Id value={g.dmeDataJobId} /> : <span className="muted">none</span>) },
          { header: "", className: "actions", render: (g) => <ActionButton label="Delete" tone="danger" confirm={`Delete feature group ${g.featureGroupName}${g.dmeDataJobId ? " and its DME data job" : ""}?`} action={{ method: "DELETE", path: `/aimgf/feature-groups/${g.featureGroupName}`, success: "Feature group deleted" }} /> },
        ]} />
      </Card>}
    </>
  );
}
