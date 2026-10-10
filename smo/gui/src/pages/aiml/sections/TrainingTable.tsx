/** The training-job columns and dialogs shared by the Training tab (`TrainingJobs`, a server table) and the model's runtime box (one model's
 * jobs): job, target, producer, status, the step the runtime reported, its NFO runtime, the metrics (view, or write back as the trainer), and the
 * role-gated Suspend / Resume (`POST /aimgf/training-jobs/{id}/suspend|resume`, feature 7) and Cancel. The progress column draws the epoch the
 * runtime reported out of its total, with AIMgF's estimate of the time left (`epoch`, `totalEpochs`, `etaSeconds`, GUI-9.8). */
import { useState, type ReactNode } from "react";

import { useSmoAction } from "../../../api/hooks";
import type { TrainingJob } from "../../../api/types";
import { ActionButton, Can, DataTable, Id, Json, Modal, StateBadge, type Column } from "../../../components/ui";
import { UsageMeter } from "../../../kit/Meter";
import { formatDuration, parseJsonObject } from "../../../lib/domain";
import { TRAINING_JOBS, useModelNames } from "../data/queries";

// OI-5-aiml-trainingjob-steps: the steps a run passes through, reported by its runtime
const STEP_LABEL: Record<TrainingJob["currentStep"], string> = { DATA_EXTRACTION: "1/3 data extraction", TRAINING: "2/3 training", TRAINED_MODEL: "3/3 trained model" };

/** The columns plus the metric dialogs they open (render `dialogs` next to the table). */
export function useTrainingColumns(): { columns: Column<TrainingJob>[]; dialogs: ReactNode } {
  const modelName = useModelNames();
  const [metricsFor, setMetricsFor] = useState<TrainingJob | null>(null);
  const [writeFor, setWriteFor] = useState<TrainingJob | null>(null);
  const columns: Column<TrainingJob>[] = [
    { header: "Job", render: (j) => <Id value={j.trainingJobId} /> },
    { header: "Target", render: (j) => j.modelId ? (modelName(j.modelId) ?? <Id value={j.modelId} />) : <>group <Id value={j.modelCoordinationGroupId} /></> },
    { header: "Producer", render: (j) => j.producerId },
    { header: "Status", render: (j) => <StateBadge state={j.status} /> },
    { header: "Step", render: (j) => j.steps ? <span className="small">{STEP_LABEL[j.currentStep]} <span className="muted">({j.steps[j.currentStep].toLowerCase().replace("_", " ")})</span></span> : "—" },
    { header: "Progress", render: (j) => <TrainingProgress job={j} /> },
    { header: "Runtime", render: (j) => <Id value={j.nfDeploymentId} /> },
    { header: "Metrics", render: (j) => <div className="row gap">
      {j.modelMetrics && <button type="button" className="btn small" onClick={() => setMetricsFor(j)}>View</button>}
      <Can method="POST" path={`${TRAINING_JOBS}/${j.trainingJobId}/model-metrics`}><button type="button" className="btn small" onClick={() => setWriteFor(j)}>{j.modelMetrics ? "Update" : "Write back"}</button></Can>
      {!j.modelMetrics && <span className="muted">—</span>}
    </div> },
    { header: "", className: "actions", render: (j) => <JobControls job={j} /> },
  ];
  const dialogs = <>
    {metricsFor && <Modal title="Model metrics" onClose={() => setMetricsFor(null)}><Json value={metricsFor.modelMetrics} /></Modal>}
    {writeFor && <WriteMetrics job={writeFor} onClose={() => setWriteFor(null)} />}
  </>;
  return { columns, dialogs };
}

/** "epoch 7 of 20" with a bar and "about 4 min 12 s left" when AIMgF estimated it; "—" when the runtime reported no epoch. */
export function TrainingProgress({ job }: { job: TrainingJob }) {
  if (job.epoch == null || !job.totalEpochs) return <span className="muted">—</span>;
  return (
    <span className="col" style={{ gap: 2, minWidth: 110 }}>
      <UsageMeter used={job.epoch} limit={job.totalEpochs} label={`epoch ${job.epoch} of ${job.totalEpochs}`} />
      <span className="xs muted">epoch {job.epoch}/{job.totalEpochs}{job.etaSeconds != null ? ` · about ${formatDuration(job.etaSeconds)} left` : ""}</span>
    </span>
  );
}

/** Suspend (IN_PROGRESS), Resume (SUSPENDED) and Cancel (either), each role-gated by the BFF table. */
function JobControls({ job }: { job: TrainingJob }) {
  const base = `${TRAINING_JOBS}/${job.trainingJobId}`;
  return (
    <div className="row gap end">
      {job.status === "IN_PROGRESS" && <ActionButton label="Suspend" action={{ method: "POST", path: `${base}/suspend`, success: "Training job SUSPENDED" }} />}
      {job.status === "SUSPENDED" && <ActionButton label="Resume" tone="primary" action={{ method: "POST", path: `${base}/resume`, success: "Training job resumed" }} />}
      {["IN_PROGRESS", "SUSPENDED"].includes(job.status) && (
        <ActionButton label="Cancel" confirm="Cancel this training job?" action={{ method: "DELETE", path: base, success: "Training job cancelled" }} />
      )}
    </div>
  );
}

/** A client table of training jobs the caller already has (one model's). */
export function TrainingTable({ rows, loading, error }: { rows?: TrainingJob[]; loading?: boolean; error?: unknown }) {
  const { columns, dialogs } = useTrainingColumns();
  return <><DataTable rows={rows} loading={loading} error={error} rowKey={(j) => j.trainingJobId} empty="No training jobs." columns={columns} />{dialogs}</>;
}

/** Write back what the trainer (MLTF) reports for a job; replaces the stored metrics wholesale. */
function WriteMetrics({ job, onClose }: { job: TrainingJob; onClose: () => void }) {
  const [text, setText] = useState(JSON.stringify(job.modelMetrics ?? { accuracy: 0.93, loss: 0.12 }, null, 2));
  const parsed = parseJsonObject(text);
  const action = useSmoAction();
  return (
    <Modal title="Write back model metrics" onClose={onClose}>
      <p className="muted small">What the trainer (MLTF) reports for job <Id value={job.trainingJobId} />. Replaces the stored metrics wholesale.</p>
      <textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} aria-label="Model metrics (JSON)" />
      {!parsed.ok && <p className="text-bad small">{parsed.error}</p>}
      <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button>
        <button type="button" className="btn primary" disabled={!parsed.ok || action.isPending} onClick={() => parsed.ok && action.mutate(
          { method: "POST", path: `${TRAINING_JOBS}/${job.trainingJobId}/model-metrics`, json: parsed.value, success: "Metrics recorded" }, { onSuccess: onClose })}>Save</button>
      </div>
    </Modal>
  );
}
