/** Section `aiml.inference` (Inference tab): every inference job (MLEF) as a server-paged table filtered by status on the server, with the
 * role-gated resolution of a RUNNING job. Results are pulled through DME, so this view tracks job state only. */
import { useState } from "react";

import type { InferenceJob } from "../../../api/types";
import { Card } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { INFERENCE_JOBS, INFERENCE_STATUSES } from "../data/queries";
import { useInferenceColumns } from "./InferenceTable";

/** The tab's table. */
export function InferenceJobs() {
  const [status, setStatus] = useState("");
  const columns = useInferenceColumns();
  return (
    <Card section="aiml.inference" title="Inference jobs (MLEF)" actions={
      <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status"><option value="">All</option>{INFERENCE_STATUSES.map((s) => <option key={s}>{s}</option>)}</select>}>
      <p className="muted small">Results are pulled through DME against the model's output data type; this view tracks job state only.</p>
      <ServerTable<InferenceJob> path={INFERENCE_JOBS} query={{ status: status || undefined }} rowKey={(j) => j.inferenceJobId} empty="No inference jobs." columns={columns} />
    </Card>
  );
}
