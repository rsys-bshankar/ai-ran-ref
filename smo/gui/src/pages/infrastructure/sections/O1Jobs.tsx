/** Infrastructure → O1 endpoints & jobs, the two job boxes: CM write jobs (`infrastructure.config-jobs`, server-paged with a status filter, a
 * row opens the shared config-job drawer, "New config write" opens `ConfigWrite`) and software management jobs (`infrastructure.swm-jobs`,
 * server-paged, with "Start software update" and the phase advance / fail buttons). */
import { useState } from "react";

import type { ConfigJobSummary, SwmJob } from "../../../api/types";
import { ConfigJobDrawer } from "../../../components/ConfigJobDrawer";
import { ActionButton, Can, Card, Id, StateBadge } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { PATHS, useEndpointChoices } from "../data/queries";
import { ConfigWrite } from "./ConfigWrite";

const JOB_STATES = ["PENDING", "PROCESSING", "HALTED", "COMPLETED", "PARTIAL_SUCCESS", "FAILED"] as const;

/** The CM write jobs box. */
export function ConfigJobs() {
  const [writing, setWriting] = useState(false);
  const [job, setJob] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  return (
    <Card section="infrastructure.config-jobs" title="CM write jobs" sub="Schema-checked writes, dispatched per managed element as NETCONF <edit-config> (call-flow 03)." actions={<>
      <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status"><option value="">Any status</option>{JOB_STATES.map((s) => <option key={s}>{s}</option>)}</select>
      <Can method="POST" path={PATHS.configJobs}><button type="button" className="btn primary" onClick={() => setWriting(true)}>New config write</button></Can>
    </>}>
      <ServerTable<ConfigJobSummary> path={PATHS.configJobs} query={{ status: status || undefined }} rowKey={(j) => j.jobId} empty="No config jobs." onRowClick={(j) => setJob(j.jobId)} columns={[
        { header: "Job", render: (j) => <Id value={j.jobId} /> }, { header: "Requested by", render: (j) => j.requestedBy },
        { header: "Scope", render: (j) => j.scope }, { header: "Status", render: (j) => <StateBadge state={j.status} /> },
      ]} />
      {writing && <ConfigWrite onClose={() => setWriting(false)} />}
      {job && <ConfigJobDrawer id={job} onClose={() => setJob(null)} />}
    </Card>
  );
}

/** The software management jobs box. */
export function SoftwareJobs() {
  const [starting, setStarting] = useState(false);
  return (
    <Card section="infrastructure.swm-jobs" title="Software management jobs" actions={<Can method="POST" path={PATHS.swmJobs}>
      {starting ? <StartSoftwareUpdate onDone={() => setStarting(false)} /> : <button type="button" className="btn" onClick={() => setStarting(true)}>Start software update…</button>}
    </Can>}>
      <ServerTable<SwmJob> path={PATHS.swmJobs} rowKey={(j) => j.jobId} empty="No software jobs." columns={[
        { header: "Job", render: (j) => <Id value={j.jobId} /> }, { header: "Managed element", render: (j) => j.managedElementRef },
        { header: "Phase", render: (j) => <code>{j.phase}</code> }, { header: "Status", render: (j) => <StateBadge state={j.status} /> },
        { header: "", className: "actions", render: (j) => !["COMPLETED", "FAILED"].includes(j.status) && (
          <div className="row gap end">
            <ActionButton label={`${j.phase} ok`} action={{ method: "POST", path: `${PATHS.swmJobs}/${j.jobId}/advance`, query: { succeeded: true }, success: "Phase advanced" }} />
            <ActionButton label="Failed" action={{ method: "POST", path: `${PATHS.swmJobs}/${j.jobId}/advance`, query: { succeeded: false }, success: "Job failed" }} />
          </div>
        ) },
      ]} />
    </Card>
  );
}

/** The element picker and start button; its element list (one bounded page) is read only once the operator opens it. */
function StartSoftwareUpdate({ onDone }: { onDone: () => void }) {
  const [me, setMe] = useState("");
  const choices = useEndpointChoices(true);
  return (
    <>
      <select value={me} onChange={(e) => setMe(e.target.value)} aria-label="Managed element"><option value="">Managed element…</option>{choices.data?.items.map((e) => <option key={e.endpointId}>{e.managedElementRef}</option>)}</select>
      <ActionButton label="Start software update" disabled={!me} onDone={onDone} action={{ method: "POST", path: PATHS.swmJobs, query: { managed_element_ref: me }, success: "Software job started (DOWNLOAD)" }} />
      <button type="button" className="btn ghost" onClick={onDone}>Cancel</button>
    </>
  );
}
