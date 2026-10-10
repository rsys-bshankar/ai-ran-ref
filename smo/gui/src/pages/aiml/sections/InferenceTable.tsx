/** The inference-job columns shared by the Inference tab (a server table) and the model's runtime box: job, model, status, the serving runtime,
 * and, for a RUNNING job, the role-gated "Completed" / "Failed" resolution (`POST /aimgf/inference-jobs/{id}/resolve?succeeded=`). */
import type { InferenceJob } from "../../../api/types";
import { ActionButton, DataTable, Id, StateBadge, type Column } from "../../../components/ui";
import { INFERENCE_JOBS, useModelNames } from "../data/queries";

/** The columns. */
export function useInferenceColumns(): Column<InferenceJob>[] {
  const modelName = useModelNames();
  return [
    { header: "Job", render: (j) => <Id value={j.inferenceJobId} /> },
    { header: "Model", render: (j) => modelName(j.modelId) ?? <Id value={j.modelId} /> },
    { header: "Status", render: (j) => <StateBadge state={j.status} /> },
    { header: "Runtime", render: (j) => <Id value={j.nfDeploymentId} /> },
    { header: "", className: "actions", render: (j) => j.status === "RUNNING" && (
      <div className="row gap end">
        <ActionButton label="Completed" action={{ method: "POST", path: `${INFERENCE_JOBS}/${j.inferenceJobId}/resolve`, query: { succeeded: true }, success: "Inference COMPLETED" }} />
        <ActionButton label="Failed" action={{ method: "POST", path: `${INFERENCE_JOBS}/${j.inferenceJobId}/resolve`, query: { succeeded: false }, success: "Inference FAILED" }} />
      </div>
    ) },
  ];
}

/** A client table of inference jobs the caller already has (one model's). */
export function InferenceTable({ rows, loading, error }: { rows?: InferenceJob[]; loading?: boolean; error?: unknown }) {
  const columns = useInferenceColumns();
  return <DataTable rows={rows} loading={loading} error={error} rowKey={(j) => j.inferenceJobId} empty="No inference jobs." columns={columns} />;
}
