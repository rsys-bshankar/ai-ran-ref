/** Section `aiml.training` (Training tab): every training job as a server-paged table filtered by status on the server, with the step its
 * runtime reported, its epoch progress and estimated time left (when the runtime reports epochs), its metrics, and Suspend / Resume / Cancel
 * (role-gated). */
import { useState } from "react";

import type { TrainingJob } from "../../../api/types";
import { Card } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { TRAINING_JOBS, TRAINING_STATUSES } from "../data/queries";
import { useTrainingColumns } from "./TrainingTable";

/** The tab's table. */
export function TrainingJobs() {
  const [status, setStatus] = useState("");
  const { columns, dialogs } = useTrainingColumns();
  return (
    <Card section="aiml.training" title="Training jobs" actions={
      <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status"><option value="">All</option>{TRAINING_STATUSES.map((s) => <option key={s}>{s}</option>)}</select>}>
      <p className="muted small">Completing a job is a model transition: advance the model with <em>Training complete</em>. Metrics are written back by the trainer (MLTF).</p>
      <ServerTable<TrainingJob> path={TRAINING_JOBS} query={{ status: status || undefined }} rowKey={(j) => j.trainingJobId} empty="No training jobs." columns={columns} />
      <p className="small muted">Progress shows the epoch a run's runtime reported and AIMgF's estimate of the time left; "—" when the runtime reports no epochs.</p>
      {dialogs}
    </Card>
  );
}
