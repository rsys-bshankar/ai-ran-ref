/** Sections `kpis.mdaFunctions`, `kpis.mdaRequests` and `kpis.mdaReports` (RAN Analytics tab, feature 9): MDAF's TS 28.104 resources as
 * server-paged tables. Functions (label, domain, capabilities, the models behind them); requests (outputs, scope, delivery, who asked, active
 * now); reports filtered on the server by kind (`report_kind`: ANALYTICS, PREDICTION, DRIFT) with their outputs and a download of the report
 * file (`GET /mdaf/mda-reports/{id}/file` through the BFF). Read-only. */
import { useState } from "react";

import { Card, Id, Json, Modal, StateBadge } from "../../../components/ui";
import { formatTime } from "../../../lib/domain";
import { Badge } from "../../../kit/Badge";
import { ServerTable } from "../../../kit/ServerTable";
import { MDA_FUNCTIONS, MDA_REPORTS, MDA_REQUESTS, REPORT_KINDS, mdaReportFileHref } from "../data/queries";
import type { MdaFunction, MdaReport, MdaRequest } from "../data/types";

/** MDA functions. */
export function MdaFunctions() {
  return (
    <Card section="kpis.mdaFunctions" title="MDA functions" sub="What MDAF can analyse">
      <ServerTable<MdaFunction> path={MDA_FUNCTIONS} rowKey={(f) => f.id} empty="No MDA function registered." columns={[
        { header: "Function", render: (f) => <div className="col" style={{ gap: 2 }}>{f.attributes.userLabel && <strong className="small">{f.attributes.userLabel}</strong>}<Id value={f.id} /></div> },
        { header: "Domain", render: (f) => f.attributes.supportedMDADomain ?? <span className="muted">—</span> },
        { header: "Capabilities", render: (f) => <span className="xs">{f.attributes.supportedMDACapabilities.join(", ") || "—"}</span> },
        { header: "Models", render: (f) => <span className="num">{f.attributes.mLModelRefList.length}</span> },
      ]} />
    </Card>
  );
}

/** MDA requests. */
export function MdaRequests() {
  return (
    <Card section="kpis.mdaRequests" title="Analysis requests" sub="MDAF · TS 28.104 MDARequest">
      <ServerTable<MdaRequest> path={MDA_REQUESTS} rowKey={(r) => r.id} empty="No analysis requested." columns={[
        { header: "Request", render: (r) => <Id value={r.id} /> },
        { header: "Outputs", render: (r) => <span className="xs">{r.attributes.requestedMDAOutputs.map((o) => o.mDAType).join(", ")}</span> },
        { header: "Scope", render: (r) => <span className="mono xs">{r.attributes.analyticsScope?.managedEntitiesScope?.join(", ") ?? (r.attributes.analyticsScope?.areaScope ? "area" : "all")}</span> },
        { header: "Delivery", render: (r) => r.attributes.reportingMethod },
        { header: "Requested by", render: (r) => r.attributes.requestedBy ?? <span className="muted">—</span> },
        { header: "State", render: (r) => <StateBadge state={r.attributes.active ? "ACTIVE" : "EXPIRED"} /> },
      ]} />
    </Card>
  );
}

/** MDA reports, with a kind filter, an output viewer and the file download. */
export function MdaReports() {
  const [kind, setKind] = useState("");
  const [shown, setShown] = useState<MdaReport | null>(null);
  return (
    <Card section="kpis.mdaReports" title="MDA reports" actions={
      <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Report kind"><option value="">All kinds</option>{REPORT_KINDS.map((k) => <option key={k}>{k}</option>)}</select>}>
      <ServerTable<MdaReport> path={MDA_REPORTS} query={{ report_kind: kind || undefined }} rowKey={(r) => r.id} empty="No MDA report published." onRowClick={setShown} columns={[
        { header: "Report", render: (r) => <Id value={r.id} /> },
        { header: "Kind", render: (r) => <Badge tone={r.attributes.reportKind === "DRIFT" ? "warn" : r.attributes.reportKind === "PREDICTION" ? "info" : "volt"} plain>{r.attributes.reportKind}</Badge> },
        { header: "Request", render: (r) => <Id value={r.attributes.mDARequestRef} /> },
        { header: "Generated", render: (r) => <span className="mono xs muted">{formatTime(r.attributes.generatedAt)}</span> },
        { header: "File", render: (r) => <a className="small" href={mdaReportFileHref(r.id)} download onClick={(e) => e.stopPropagation()}>Download</a> },
      ]} />
      {shown && <Modal title={`${shown.attributes.reportKind} report`} onClose={() => setShown(null)}><Json value={shown.attributes.mDAOutputs} /></Modal>}
    </Card>
  );
}
