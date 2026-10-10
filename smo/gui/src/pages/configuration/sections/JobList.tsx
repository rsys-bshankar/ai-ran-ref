/** Configuration · config job list (`configuration.jobs`): every CM write job, paged by the server (`GET /config-jobs?status=`, SCALE.md P1).
 * Halted jobs first: until a state is chosen, the list shows the halted ones when there are any (the summary's count), else all. A row opens
 * the job's staged detail (`?job=`). */
import { Card, Id, StateBadge, type Column } from "../../../components/ui";
import { count, useSummary } from "../../../data/summary";
import { ServerTable } from "../../../kit/ServerTable";
import { JOBS_PATH, useJobStatus, useSelectedJob } from "../data/queries";
import { JOB_STATES, type JobRow } from "../data/types";

/** The list's columns. */
const COLUMNS: Column<JobRow>[] = [
  { header: "Job", render: (j) => <Id value={j.jobId} /> },
  { header: "Requested by", render: (j) => j.requestedBy },
  { header: "Scope", render: (j) => <span className="mono small">{j.accessScope ?? j.scope}</span> },
  { header: "MSAC role", render: (j) => j.msacRole ?? <span className="muted">—</span> },
  { header: "Status", render: (j) => <StateBadge state={j.status} /> },
];

/** The list card. */
export function JobList() {
  const [chosen, setStatus] = useJobStatus();
  const [selected, select] = useSelectedJob();
  const summary = useSummary("configuration");
  const halted = count(summary.data, "configJobs.HALTED");
  const status = chosen ?? (halted ? "HALTED" : "all");
  return (
    <Card section="configuration.jobs" title="Config jobs" sub={status === "HALTED" && !chosen ? "halted first · pick “All” for every job" : "server-paged"}
      actions={<label className="row small">State
        <select aria-label="Job state" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="all">All</option>{JOB_STATES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>}>
      {chosen === null && summary.isLoading ? <p className="muted small">Loading…</p> : (
        <ServerTable<JobRow> path={JOBS_PATH} query={status === "all" ? undefined : { status }} columns={COLUMNS} rowKey={(j) => j.jobId}
          onRowClick={(j) => select(j.jobId)} selectedKey={selected} empty={status === "all" ? "No config job yet." : `No ${status} job.`} />
      )}
    </Card>
  );
}
