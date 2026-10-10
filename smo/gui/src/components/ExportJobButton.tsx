/** "Export…" (GUI-9.5b): the button that turns a page's current filters into an asynchronous export job (`data/exports.ts`). It opens a dialog
 * that lists what the file will hold (and which of the page's filters it cannot take), starts the job, and then points to the Exports page,
 * where the file is downloaded when it is written. Used by the Decisions page, the alarm table (GUI-2.5) and Admin → Audit log. */
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { exportErrorText, useCreateExport, type ExportJob, type ExportRequest } from "../data/exports";
import { KeyValue, Modal } from "./ui";

/** The labels of the request fields the dialog lists. */
const LABELS: [keyof ExportRequest, string][] = [
  ["since", "From"], ["until", "Until"], ["invokerId", "rApp"], ["disposition", "Outcome"], ["region", "Region"], ["siteCluster", "Site cluster"],
  ["username", "User"], ["action", "Event"], ["severity", "Severity"], ["ackState", "Ack state"], ["openOnly", "Open alarms only"],
  ["probableCause", "Probable cause"], ["managedElementRef", "Managed element"], ["managedFunctionRef", "Managed function"],
];

/** Props: the request the page's filters make, the button's title, and a note on the filters the export does not take (null: none). */
export interface ExportJobButtonProps { request: ExportRequest; what: string; note?: ReactNode }

/** The button and its dialog. */
export function ExportJobButton({ request, what, note }: ExportJobButtonProps) {
  const [open, setOpen] = useState(false);
  const create = useCreateExport();
  const [job, setJob] = useState<ExportJob | null>(null);
  const close = () => { setOpen(false); setJob(null); create.reset(); };
  const start = () => create.mutate(request, { onSuccess: (j) => setJob(j) });
  const rows = LABELS.filter(([k]) => request[k]).map(([k, label]) => [label, k === "since" && request.since.startsWith("1970-") ? "the first record" : request[k] === true ? "yes" : String(request[k])] as [string, string]);
  if (!request.until) rows.splice(1, 0, ["Until", "now (when the job starts)"]);
  return (
    <>
      <button type="button" className="btn small" onClick={() => setOpen(true)} title="Write the rows these filters select to a CSV file in the background">Export…</button>
      {open && (
        <Modal title={`Export ${what}`} onClose={close}>
          {job ? (
            <div className="stack">
              <p>The export is <strong>{job.state.toLowerCase()}</strong>. It is written in the background; download it from{" "}
                <Link to="/exports" onClick={close}>Exports</Link> when it is done (the file is kept 24 hours).</p>
              <div className="row end"><button type="button" className="btn" onClick={close}>Close</button><Link className="btn primary" to="/exports" onClick={close}>Open Exports</Link></div>
            </div>
          ) : (
            <div className="stack">
              <p className="small muted">A CSV of every row these filters select, newest first, with no limit on the span (at most 10,000,000 rows).</p>
              <KeyValue items={rows} />
              {note && <p className="small muted" role="note">{note}</p>}
              {create.error && <div className="error-box" role="alert">{exportErrorText(create.error)}</div>}
              <div className="row gap end">
                <button type="button" className="btn" onClick={close}>Cancel</button>
                <button type="button" className="btn primary" disabled={create.isPending} onClick={start}>Start export</button>
              </div>
            </div>
          )}
        </Modal>
      )}
    </>
  );
}
