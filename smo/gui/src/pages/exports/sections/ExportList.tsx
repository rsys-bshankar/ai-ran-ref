/** Section `exports.list`: the export jobs, newest first: kind, who and when, the filters it was made with, state (with the error of a failed
 * one), rows and size so far, when it finished and expires, and Download (a DONE job's `fileUrl`) and Delete. The list is re-read every 2 s while
 * a job is QUEUED or RUNNING (`data/exports.ts` EXPORT_POLL), so the rows count up as it is written. An admin sees every user's jobs and can
 * narrow them to one user. */
import { useState } from "react";

import { useAuth } from "../../../auth/AuthContext";
import { Card, DataTable, StateBadge } from "../../../components/ui";
import { formatBytes, isRunning, useDeleteExport, useExports, type ExportJob } from "../../../data/exports";
import { formatTime } from "../../../lib/domain";

/** The filters a job was made with, as "rApp=…, region=…" (the time span is its own column). */
export function filtersOf(job: Pick<ExportJob, "params">): string {
  const skip = new Set(["kind", "since", "until"]);
  const parts = Object.entries(job.params ?? {}).filter(([k, v]) => !skip.has(k) && v !== null && v !== undefined && v !== "").map(([k, v]) => `${k}=${String(v)}`);
  return parts.join(", ");
}

/** The span a job covers, from its params ("first record" for the epoch start). */
export function spanOf(job: Pick<ExportJob, "params">): string {
  const since = typeof job.params?.since === "string" ? job.params.since : null;
  const until = typeof job.params?.until === "string" ? job.params.until : null;
  const from = !since || since.startsWith("1970-") ? "first record" : formatTime(since);
  return `${from} → ${until ? formatTime(until) : "request time"}`;
}

/** The card. */
export function ExportList() {
  const { me } = useAuth();
  const admin = me?.role === "admin";
  const [user, setUser] = useState("");
  const jobs = useExports({ username: admin ? user.trim() : undefined });
  const del = useDeleteExport();
  const items = jobs.data?.items;
  const running = (items ?? []).filter(isRunning).length;
  return (
    <Card section="exports.list" title="Your exports" sub={running ? `${running} running · refreshing every 2 s` : "newest first"}
      actions={admin && <input placeholder="All users" aria-label="Filter by user" value={user} onChange={(e) => setUser(e.target.value)} />}>
      <DataTable<ExportJob> rows={items} loading={jobs.isLoading} error={jobs.error} rowKey={(j) => j.id}
        empty="No export yet. Use “Export…” on Decisions or the Admin audit log." columns={[
          { header: "What", render: (j) => <><strong>{j.kind === "decisions" ? "Decision records" : "Audit log"}</strong><div className="muted small">{spanOf(j)}</div></> },
          { header: "Filters", render: (j) => <span className="small">{filtersOf(j) || <span className="muted">none</span>}</span> },
          { header: "Requested", render: (j) => <><span className="small">{formatTime(j.createdAt)}</span>{admin && <div className="muted small">{j.username}</div>}</> },
          { header: "State", render: (j) => <><StateBadge state={j.state} />{j.error && <div className="small t-warn">{j.error}</div>}</> },
          { header: "Rows", render: (j) => <span className="num">{j.rows === null || j.rows === undefined ? "—" : j.rows.toLocaleString("en-US")}</span> },
          { header: "Size", render: (j) => <span className="num">{formatBytes(j.bytes)}</span> },
          { header: "Finished", render: (j) => (j.finishedAt ? <span className="small">{formatTime(j.finishedAt)}</span> : <span className="muted">—</span>) },
          { header: "Kept until", render: (j) => (j.expiresAt && j.state === "DONE" ? <span className="small">{formatTime(j.expiresAt)}</span> : <span className="muted">—</span>) },
          { header: "", className: "actions", render: (j) => (
            <div className="row gap">
              {j.state === "DONE" && j.fileUrl && <a className="btn small primary" href={j.fileUrl} download={j.fileName}>Download</a>}
              <button type="button" className="btn small danger" disabled={del.isPending}
                onClick={() => { if (window.confirm(isRunning(j) ? "Stop and delete this export?" : "Delete this export and its file?")) del.mutate(j.id); }}>Delete</button>
            </div>
          ) },
        ]} />
    </Card>
  );
}
