/** Software · element software jobs (`software.jobs`): every per-element software job (flow 19: DOWNLOAD → INSTALL → ACTIVATE), paged by
 * the server (`GET /software-management-jobs`), optionally of one element (`?managed_element_ref=`, the route's only filter). A job opens
 * its flow-19 board, an element its Element detail page. */
import { useState } from "react";
import { Link } from "react-router-dom";

import { Card, Id, StateBadge, type Column } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { elementHref } from "../../element/data/types";
import { SWM_JOBS_PATH } from "../data/queries";
import { flow19Href, type SwmJob } from "../data/types";

/** The table's columns. */
const COLUMNS: Column<SwmJob>[] = [
  { header: "Job", render: (j) => <Link to={flow19Href(j.jobId)}><Id value={j.jobId} /></Link> },
  { header: "Element", render: (j) => <Link to={elementHref(j.managedElementRef)}>{j.managedElementRef}</Link> },
  { header: "RU", render: (j) => j.ruInstanceId ?? "—" },
  { header: "Phase", render: (j) => j.phase },
  { header: "Status", render: (j) => <StateBadge state={j.status} /> },
  { header: "Campaign", render: (j) => (j.campaignId ? <span className="small"><Id value={j.campaignId} /> · wave {j.campaignWave}</span> : <span className="muted">—</span>) },
  { header: "Undoes", render: (j) => (j.rollbackOf ? <Id value={j.rollbackOf} /> : <span className="muted">—</span>) },
];

/** The card. */
export function ElementJobs() {
  const [text, setText] = useState("");
  const [me, setMe] = useState("");
  return (
    <Card section="software.jobs" title="Element software jobs" sub="DOWNLOAD → INSTALL → ACTIVATE per element (flow 19)"
      actions={<form className="row" onSubmit={(e) => { e.preventDefault(); setMe(text.trim()); }}>
        <label className="search"><input aria-label="Element" placeholder="Managed element ref" value={text} onChange={(e) => setText(e.target.value)} /></label>
        <button type="submit" className="btn small">Filter</button>
        {me && <button type="button" className="btn small ghost" onClick={() => { setText(""); setMe(""); }}>Clear</button>}
      </form>}>
      <ServerTable<SwmJob> path={SWM_JOBS_PATH} query={me ? { managed_element_ref: me } : undefined} columns={COLUMNS} rowKey={(j) => j.jobId}
        empty={me ? `No software job for ${me}.` : "No element software job yet."} />
    </Card>
  );
}
